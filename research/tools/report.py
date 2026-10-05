#!/usr/bin/env python3
"""Intelligence reports over the read surface: how an agent uses what the record answers, for any profile.

The record tools answer "what does the record hold" (navy.py and the tools behind it). This module answers "how does
an agent turn that into a report": it takes stock of the record domain by domain, turns a report spec (a JSON file
under research/report_specs/) into the exact commands to run, anchored on the offices the record places money and
movement on, runs them into an evidence bundle or lets a headless model run them, checks every claim of the written
answer against the evidence and the record, and renders the report. Nothing here reads data its own way: every answer
comes through navy.call, office_owners.page, budget.show or the loaders agency_truth already uses.

    python research/tools/report.py specs                                        the specs on disk
    python research/tools/report.py inventory [--agency KEY]                    what the record holds, by domain
    python research/tools/report.py plan --spec agency-brief [--focus TERM] [--mode brief|full] [--schema] [--agency KEY]
    python research/tools/report.py gather --spec agency-brief [--focus TERM] [--out DIR] [--agency KEY]
    python research/tools/report.py bundle --out DIR                           the plan's reads, for a run whose bundle is missing
    python research/tools/report.py check --out DIR [--answer FILE]            the claims against the evidence
    python research/tools/report.py render --out DIR                           DIR/report.md
    python research/tools/report.py run --spec agency-brief [--focus TERM] [--budget-usd 8] [--out DIR] [--agency KEY]
    python research/tools/report.py --selfcheck

Facts, inferences and recommendations stay apart; an anchor office is an inference with its rule; an empty domain is
stated as a boundary (what was read, how many rows, how recent) before anything else; the report is as of the
record's own date. The two commands navy.py exposes for an agent on the server (inventory, report) are served here.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _reexec_for_agency() -> None:
    """`--agency KEY` on the command line: the whole process runs under that profile (the profile is chosen when
    agency is imported, so the switch happens before any import)."""
    argv = sys.argv[1:]
    if "--agency" not in argv:
        return
    i = argv.index("--agency")
    key = argv[i + 1] if i + 1 < len(argv) else ""
    rest = argv[:i] + argv[i + 2:]
    if key and key != os.environ.get("AGENCY", "navy"):
        os.execve(sys.executable, [sys.executable, __file__, *rest], {**os.environ, "AGENCY": key})
    sys.argv = [sys.argv[0], *rest]


if __name__ == "__main__":
    _reexec_for_agency()

sys.path.insert(0, str(HERE))
import agency  # noqa: E402
import agency_truth  # noqa: E402
import budget  # noqa: E402
import navy  # noqa: E402
import office_owners  # noqa: E402
import pulse  # noqa: E402
import vendors  # noqa: E402
from agency import KEY, P, RESULTS  # noqa: E402
from backtest import register_problems  # noqa: E402
from dossier_compare import CLAIMS  # noqa: E402
from outreach import PLAIN, S, arr, obj, verbatim  # noqa: E402
from outreach_compare import ALLOWED, NAVY, SERVER, claude  # noqa: E402
from people import norm_name  # noqa: E402

SPECS = ROOT / "research" / "report_specs"
REPORTS = RESULTS / "reports"
LABEL, SHORT = P["label"], P["short"].removeprefix("U.S. ")
CAP = 1500  # characters of one answer shown in evidence.md and the sources section, as navy.beside cuts them
LOCAL = {"inventory", "report"}  # the commands this module serves; everything else is navy.HELP
PLACEHOLDERS = {"label", "short", "as_of", "office", "focus", "vendor"}
MODES = ("brief", "full")
WHEN = ("always", "focus", "no_focus")
STATUSES = ("planned", "boundary", "optional", "deferred")
# a record shows a role; budget authority, a decision or advocacy is only ever a "potential" reading, with its reasons
ROLE_WORDS = re.compile(r"\b(decision[- ]makers?|budget holders?|champions?)\b", re.I)
MEASURES = (("display_enacted", "Comptroller display, enacted"), ("shared_lines_enacted", "shared display lines, enacted"),
            ("program_sum_current_year", "program rows, current year"), ("book_sum_current_year", "book lines, current year"))
ANCHOR_RULE = ("the offices with the largest labelled budget measure in office_owners.json (display enacted, then shared display "
               "lines, then program rows, then book lines), then the offices with the most cells in the pulse ranking; a focus "
               "term replaces both with the offices whose statements carry it")
# the record's domains: what makes each present, and the commands that answer it
DOMAINS = {
    "record": "the frozen corpus of statements: search TERM, cell KEY, sources ID, pulse",
    "organization": "the organization tree: neighbors OFFICE, office OFFICE, wiki OFFICE, page office NAME",
    "people": "people with a stated role: people OFFICE, page person NAME",
    "budget": "budget lines and measures placed in offices: report budget OFFICE",
    "procurement": "notices and forecast rows: office OFFICE, search TERM, ask changed OFFICE, pulse rank|week|actions, revisions",
    "awards": "contract awards and the buying book: dna OFFICE, ask incumbents OFFICE, ask analogs KEY, trace award PIID",
    "vendors": "vendors by unique entity identifier: vendor NAME, ask moves VENDOR, ask team CAPABILITY",
    "programs": "SBIR and STTR topics: topics TERM",
    "signals": "what leaders, Congress, oversight, news, conferences and hiring say: initiatives OFFICE",
    "protest": "GAO bid protests: protests [TERM]",
    "forecast": "acquisition forecast rows: search TERM, cell KEY, trace need KEY, revisions",
}
SECTION = obj(summary=S, claims=CLAIMS, inferences=arr(obj(inference=S, rule=S, rests_on=arr(S))), not_found=S)
FIXTURE_WALK = None  # a Walk set by the selfcheck and the tests; the record's own layer otherwise


# ------------------------------------------------------------------ the record


def lay():
    return FIXTURE_WALK.layer if FIXTURE_WALK is not None else navy.layer()


def walk():
    return FIXTURE_WALK if FIXTURE_WALK is not None else navy.everything()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inventory(key: str = KEY) -> dict:
    """What one agency's record holds, from its files alone: each file with its rows and newest date, each domain with
    whether it is present and the commands that answer it, the record's date, and the truth table's verdict. Never
    opens the corpus through navy.layer(), so it answers for a layer whose corpus is not in this checkout."""
    d = agency_truth.research_dir(key)
    prof = agency.PROFILES.get(key, {})
    L = agency_truth.load_json
    files: dict[str, dict] = {}

    def note(name: str, path: Path, rows: int, newest: str = "") -> None:
        files[name] = {"path": str(path.relative_to(ROOT)) if path.exists() else "", "exists": path.exists(), "rows": rows, "newest": newest}

    corpus = L(d / "results" / "corpus.json")
    families = Counter(e.get("family", "") for e in (corpus or {}).get("events", []))
    corpus_end = pulse.corpus_end(corpus) if corpus and corpus.get("events") else ""
    note("corpus.json", d / "results" / "corpus.json", len((corpus or {}).get("events", [])), corpus_end)
    seed = L(d / "memory" / "organization_seed.json") or {}
    note("organization_seed.json", d / "memory" / "organization_seed.json", len(seed.get("nodes", [])), (seed.get("generated") or "")[:10])
    people_ = L(d / "memory" / "people.json") or {}
    rows = people_.get("rows", [])
    note("people.json", d / "memory" / "people.json", len(rows), max([r.get("last_seen") or "" for r in rows], default="")[:10])
    vend = L(d / "memory" / "vendors.json") or {}
    note("vendors.json", d / "memory" / "vendors.json", len(vend.get("vendors", [])), (vend.get("as_of") or "")[:10])
    programs = L(d / "memory" / "programs.json") or {}
    note("programs.json", d / "memory" / "programs.json", len(programs.get("programs", [])), str(programs.get("fiscal_year") or ""))
    stake = L(d / "memory" / "stakeholders.json") or {}
    note("stakeholders.json", d / "memory" / "stakeholders.json", len(stake.get("people", [])), (stake.get("as_of") or "")[:10])
    recs = L(d / "memory" / "contact_recommendations.json")
    note("contact_recommendations.json", d / "memory" / "contact_recommendations.json", len(agency_truth.rows_of(recs)) if recs is not None else 0)
    for name in agency_truth.EVENT_FILES:
        payload = L(d / "events" / name)
        rows_ = agency_truth.rows_of(payload) if payload is not None else []
        if name == "sbir_topics.json" and prof.get("sbir_component"):  # three files hold another agency's topics
            rows_ = [r for r in rows_ if isinstance(r, dict) and r.get("component", "") in ("", prof["sbir_component"])]
        dates = agency_truth.dates_in(rows_)
        note(name, d / "events" / name, len(rows_), max(dates) if dates else "")
    owners = L(d / "results" / "office_owners.json") or {}
    held = [r for r in owners.get("inferences", {}).get("budget_held", []) if any(r.get(m) for m, _ in MEASURES)]
    note("office_owners.json", d / "results" / "office_owners.json", len(owners.get("offices", [])), (owners.get("as_of") or "")[:10])
    pulse_ = L(d / "results" / "pulse.json") or {}
    note("pulse.json", d / "results" / "pulse.json", len(pulse_.get("ranking", [])), (pulse_.get("as_of") or "")[:10])
    dna = L(d / "results" / "buying_dna.json") or {}
    note("buying_dna.json", d / "results" / "buying_dna.json", sum(1 for v in dna.get("contracting_offices", {}).values() if (v or {}).get("awards")), (dna.get("as_of") or "")[:10])
    packs = sorted(p.name for p in agency.forecast_packs(ROOT / "datapack")) if prof.get("forecast", {}).get("pack_glob") else []

    def domain(present: bool, rows: int, newest: str, *names: str) -> dict:
        return {"present": bool(present and rows > 0), "rows": rows, "newest": newest, "files": list(names)}

    f = files
    signal_rows = sum(families.get(k, 0) for k in ("congress", "leaders", "oversight", "news", "conference", "hiring", "organization"))
    signal_files = ("congress_events.json", "remarks_events.json", "oversight_events.json", "news_observations.json", "hiring_observations.json", "fedreg_events.json")
    domains = {
        "record": domain(corpus is not None, f["corpus.json"]["rows"], corpus_end, "corpus.json"),
        "organization": domain(True, f["organization_seed.json"]["rows"] + len((corpus or {}).get("orgs", {}) or {}), f["organization_seed.json"]["newest"], "organization_seed.json", "corpus.json"),
        "people": domain(True, f["people.json"]["rows"] + f["stakeholders.json"]["rows"], f["people.json"]["newest"], "people.json", "stakeholders.json", "contact_recommendations.json"),
        "budget": domain(True, f["budget_lines.json"]["rows"] + f["budget_measures.json"]["rows"] + len(held),
                         max(f["budget_lines.json"]["newest"], f["budget_measures.json"]["newest"]), "budget_lines.json", "budget_measures.json", "office_owners.json"),
        "procurement": domain(corpus is not None, families.get("notice", 0) + families.get("forecast", 0), corpus_end, "corpus.json"),
        "awards": domain(True, families.get("incumbent", 0) + f["assistance_awards.json"]["rows"] + f["buying_dna.json"]["rows"],
                         corpus_end or f["buying_dna.json"]["newest"], "corpus.json", "assistance_awards.json", "buying_dna.json"),
        "vendors": domain(True, f["vendors.json"]["rows"], f["vendors.json"]["newest"], "vendors.json"),
        "programs": domain(True, f["sbir_topics.json"]["rows"], f["sbir_topics.json"]["newest"], "sbir_topics.json"),
        "signals": domain(True, signal_rows + sum(f[n]["rows"] for n in signal_files), corpus_end or max(f[n]["newest"] for n in signal_files), "corpus.json", *signal_files),
        "protest": domain(True, families.get("protest", 0) + f["protest_events.json"]["rows"], f["protest_events.json"]["newest"] or corpus_end, "protest_events.json", "corpus.json"),
        "forecast": domain(True, families.get("forecast", 0), corpus_end, "corpus.json", *(f"datapack/{p}" for p in packs)),
    }
    for name, dom in domains.items():
        dom["answered_by"] = DOMAINS[name]
    truth = (agency_truth.load_json(agency_truth.OUT_JSON) or {})
    row = (truth.get("agencies") or {}).get(key) or {}
    as_of = corpus_end or f["pulse.json"]["newest"] or f["office_owners.json"]["newest"]
    return {"agency": key, "label": prof.get("label", key), "short": prof.get("short", key).removeprefix("U.S. "), "as_of": as_of,
            "corpus": {"exists": corpus is not None, "frozen_at": (corpus or {}).get("frozen_at", ""), "events": f["corpus.json"]["rows"],
                       "orgs": len((corpus or {}).get("orgs", {}) or {}), "needs": len((corpus or {}).get("needs", []) or []), "families": dict(sorted(families.items())),
                       "note": "" if corpus is not None else f"no {d.relative_to(ROOT)}/results/corpus.json in this checkout; the commands that read the corpus cannot answer here"},
            "domains": domains, "files": files, "forecast_packs": packs,
            "truth": {"verdict": row.get("verdict", ""), "brief_ready": row.get("brief_ready"), "reason": row.get("reason", ""),
                      "built_from": "research/sources/agency_truth.json, a build artefact" + (f" at commit {truth.get('commit')}" if truth.get("commit") else "")},
            "generated": now()}


# ------------------------------------------------------------------ specs and plans


def specs() -> list[str]:
    return sorted(p.stem for p in SPECS.glob("*.json"))


def load_spec(name: str) -> dict:
    path = SPECS / f"{name}.json" if not name.endswith(".json") else Path(name)
    if not path.exists():
        raise FileNotFoundError(f"no spec {name!r}; specs on disk: {', '.join(specs()) or 'none'}")
    spec = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_spec(spec)
    if problems:
        raise ValueError(f"spec {name!r}: " + "; ".join(problems))
    return spec


def placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text or ""))


def validate_spec(spec: dict) -> list[str]:
    """What a spec must carry to be run: the only gate between a JSON file and a report."""
    out = []
    for k in ("name", "mode", "title", "objective", "anchors", "sections"):
        if k not in spec:
            out.append(f"missing {k!r}")
    if spec.get("mode") not in MODES:
        out.append(f"mode must be one of {MODES}")
    anchors_ = spec.get("anchors") or {}
    if not isinstance(anchors_.get("count"), int) or not set(anchors_.get("by", [])) <= {"budget", "pulse"}:
        out.append("anchors needs an integer count and by within budget, pulse")
    seen = set()
    for i, sec in enumerate(spec.get("sections") or []):
        where = f"section {i} ({sec.get('key', '?')})"
        for k in ("key", "title", "question", "command", "args", "needs", "per_anchor", "required_parts", "modes"):
            if k not in sec:
                out.append(f"{where}: missing {k!r}")
        if sec.get("key") in seen:
            out.append(f"{where}: duplicate key")
        seen.add(sec.get("key"))
        if sec.get("command") not in navy.HELP and sec.get("command") not in LOCAL:
            out.append(f"{where}: command {sec.get('command')!r} is not a navy.py command")
        if not set(sec.get("needs", [])) <= set(DOMAINS):
            out.append(f"{where}: needs outside {sorted(DOMAINS)}")
        if not set(sec.get("modes", [])) <= set(MODES) or not sec.get("modes"):
            out.append(f"{where}: modes must be within {MODES}")
        if sec.get("when", "always") not in WHEN:
            out.append(f"{where}: when must be one of {WHEN}")
        bad = (placeholders(sec.get("args", "")) | placeholders(sec.get("title", ""))) - PLACEHOLDERS
        if bad:
            out.append(f"{where}: unknown placeholders {sorted(bad)}")
        if sec.get("per_anchor") and "{office}" not in sec.get("args", "") and "{office}" not in sec.get("title", ""):
            out.append(f"{where}: per_anchor without {{office}}")
    return out


class _Ctx(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fill(template: str, ctx: dict, quote: bool = True) -> str:
    """A template with its placeholders filled; in a command's arguments, office, focus and vendor are quoted for the
    shell and shlex.split; in a title they stand as written."""
    quoted = {k: (shlex.quote(v) if quote and k in ("office", "focus", "vendor") and v else v) for k, v in ctx.items()}
    return template.format_map(_Ctx(quoted))


def owners_payload() -> dict:
    return json.loads(office_owners.OUT_JSON.read_text(encoding="utf-8")) if office_owners.OUT_JSON.exists() else {}


def pulse_payload() -> dict:
    return json.loads(pulse.PULSE.read_text(encoding="utf-8")) if pulse.PULSE.exists() else {}


def anchors(inv: dict, layer, focus: str, n: int, by: list[str], owners: dict | None = None, ranking: list | None = None,
            search=None) -> list[dict]:
    """The offices a report centres on, as an inference with its basis: the offices whose statements carry the focus
    term, else the offices holding the largest budget measure, then the offices with the most ranked cells."""
    owners = owners_payload() if owners is None else owners
    ranking = (pulse_payload().get("ranking", []) if ranking is None else ranking) or []
    out: list[dict] = []
    seen: set[str] = set()

    def keep(label: str, basis: str) -> None:
        oid = layer.org_id(label) if layer is not None else None
        if layer is not None and not oid:
            return
        key = oid or label.lower()
        if key in seen or len(out) >= n:
            return
        seen.add(key)
        out.append({"office": label, "org_id": oid or "", "basis": basis})

    if focus:
        found = search(focus) if search else json.loads(navy.call("search", {"args": shlex.quote(focus)}) or "{}")
        for row in found.get("offices", []):
            if row.get("type") == "agency" or not row.get("office"):
                continue
            keep(row["office"], f"search: {row.get('statements', 0)} statements carry {focus!r}")
    if "budget" in by and len(out) < n:
        by_uuid = {o["uuid"]: o for o in owners.get("offices", [])}
        ranked = []
        for row in owners.get("inferences", {}).get("budget_held", []):
            office = by_uuid.get(row.get("office"))
            measure = next(((m, label, row[m]) for m, label in MEASURES if row.get(m)), None)
            if office and measure:
                ranked.append((measure[2], office.get("acronym") or office.get("name"), measure[1]))
        for value, label, what in sorted(ranked, key=lambda t: -t[0]):
            keep(label, f"budget: {what} {value:,.1f} $M (office_owners.json)")
    if "pulse" in by and len(out) < n:
        for code, count in Counter(r.get("office") for r in ranking if r.get("office")).most_common():
            keep(code, f"pulse: {count} of the {len(ranking)} ranked cells (pulse.json)")
    return out


def boundary_reason(inv: dict, missing: list[str]) -> str:
    parts = []
    for dom in missing:
        d = inv["domains"].get(dom, {})
        read = ", ".join(f"{name} ({inv['files'][name]['rows']} rows" + (f", newest {inv['files'][name]['newest']}" if inv["files"][name]["newest"] else "") + ")"
                         if name in inv["files"] else name for name in d.get("files", []))
        parts.append(f"{dom}: {d.get('rows', 0)} rows in this record; read {read or 'nothing'}" + (f"; {inv['corpus']['note']}" if dom in ("record", "procurement") and inv["corpus"].get("note") else ""))
    return "; ".join(parts)


def plan(spec: dict, inv: dict, focus: str = "", mode: str | None = None, layer="record", owners=None, ranking=None, search=None) -> dict:
    """The spec against the record: the anchors, and one step per section (per anchor office where the section asks),
    each with the exact command, its shell and server forms, and its status: planned, boundary (a domain the record
    lacks: the reason says what was read), optional (needs a database this layer may not have) or deferred (its
    argument comes from another step's answer)."""
    mode = mode or spec["mode"]
    layer = (lay() if inv["corpus"]["exists"] else None) if layer == "record" else layer
    ctx = {"label": inv["label"], "short": inv["short"], "as_of": inv["as_of"], "focus": focus}
    cfg = spec["anchors"]
    chosen = anchors(inv, layer, focus if cfg.get("focus_overrides", True) else "", cfg["count"], cfg["by"], owners, ranking, search) \
        if any(s.get("per_anchor") for s in spec["sections"]) else []
    steps = []
    for sec in spec["sections"]:
        if mode not in sec["modes"]:
            continue
        when = sec.get("when", "always")
        if (when == "focus" and not focus) or (when == "no_focus" and focus):
            continue
        missing = [d for d in sec["needs"] if not inv["domains"].get(d, {}).get("present")]
        targets = chosen if sec["per_anchor"] else [None]
        if sec["per_anchor"] and not chosen:
            steps.append(step(sec, ctx, None, inv, "boundary", "no anchor office: the record places no budget measure and no ranked cell on an office this layer resolves" + (f"; nothing carries {focus!r}" if focus else "")))
            continue
        for a in targets:
            if missing:
                steps.append(step(sec, ctx, a, inv, "boundary", boundary_reason(inv, missing)))
            elif "{vendor}" in sec["args"]:
                steps.append(step(sec, ctx, a, inv, "deferred", "the vendor comes from the office view's top vendor once that step has run"))
            elif sec.get("optional"):
                steps.append(step(sec, ctx, a, inv, "optional", "needs the agency's database; skipped with a note when it does not answer"))
            else:
                steps.append(step(sec, ctx, a, inv, "planned", ""))
    for i, s in enumerate(steps, start=1):
        s["n"] = i
    return {"spec": spec["name"], "mode": mode, "agency": inv["agency"], "label": inv["label"], "as_of": inv["as_of"], "focus": focus,
            "title": fill(spec["title"], ctx, quote=False), "objective": fill(spec["objective"], ctx, quote=False), "generated": now(),
            "anchors": chosen, "anchor_rule": ANCHOR_RULE, "steps": steps, "inventory": inv}


def step(sec: dict, ctx: dict, anchor: dict | None, inv: dict, status: str, reason: str) -> dict:
    office = anchor["office"] if anchor else ""
    args = fill(sec["args"], {**ctx, "office": office})
    key = f"{sec['key']}:{office}" if anchor else sec["key"]
    return {"key": key, "section": sec["key"], "title": fill(sec["title"], {**ctx, "office": office}, quote=False), "question": fill(sec["question"], {**ctx, "office": office}, quote=False),
            "command": sec["command"], "args": args, "anchor": office, "needs": sec["needs"], "required_parts": sec["required_parts"],
            "cap": sec.get("cap", CAP), "status": status, "reason": reason,
            "cli": f"AGENCY={inv['agency']} python research/tools/navy.py {sec['command']} {args}".rstrip(),
            "mcp": {"tool": sec["command"], "arguments": {"args": args}}}


def answer_schema(plan_: dict) -> dict:
    """The structured answer a model or an agent writes: one section per step, keyed as the plan keys them."""
    return obj(sections=obj(**{s["key"]: SECTION for s in plan_["steps"]}), reading=S)


# ------------------------------------------------------------------ gathering


def is_empty(text: str) -> bool:
    head = text.strip().split("\n", 1)[0]
    return not text.strip() or bool(navy.NOTHING.match(head)) or head.startswith("usage:") or head.startswith("no office ") \
        or head.startswith("not collected") or head == "{}" or head == "[]"


def budget_text(office: str) -> str:
    """The budget an office holds, as office_owners places it, then the budget lines whose title or text name the
    office. Both come from the tools that build them; nothing is summed here."""
    parts = []
    if office_owners.OUT_JSON.exists():
        parts.append(navy.printed(office_owners.page, [office]).rstrip())
    else:
        parts.append(f"no {office_owners.OUT_JSON.relative_to(ROOT)}: the owners stage has not run for this record")
    if budget.OUT.exists():
        lines = navy.printed(budget.show, [office]).strip().splitlines()
        shown = lines[:40]
        more = f"\n... {len(lines) - 40} more line(s); budget.py show {shlex.quote(office)} for the rest" if len(lines) > 40 else ""
        parts.append(f"## Budget lines naming {office}\n\n" + ("\n".join(shown) + more if shown else f"no budget line names {office!r}"))
    else:
        parts.append(f"## Budget lines naming {office}\n\nno {budget.OUT.relative_to(ROOT)} in this record")
    return "\n\n".join(parts)


def run_step(step_: dict) -> dict:
    start = time.time()
    if step_["command"] == "inventory":
        text = json.dumps(inventory(), indent=1, ensure_ascii=False)
    elif step_["command"] == "report":
        words = shlex.split(step_["args"])
        text = budget_text(" ".join(words[1:])) if words[:1] == ["budget"] else json.dumps(plan(load_spec(words[words.index("--spec") + 1] if "--spec" in words else "agency-brief"), inventory()), indent=1) if words[:1] == ["plan"] else "usage: report plan [--spec NAME] [--focus TERM] | budget OFFICE"
    else:
        text = navy.call(step_["command"], {"args": step_["args"]})
    return {"output": text, "chars": len(text), "empty": is_empty(text), "seconds": round(time.time() - start, 1)}


UNAVAILABLE = re.compile(r"(psql|connection|does not exist|could not connect|publishes no (acquisition )?forecast|No such file|Traceback)", re.I)


def top_vendor(bundle: list[dict], anchor: str) -> str:
    for g in bundle:
        if g["command"] == "office" and g["anchor"] == anchor and not g["empty"]:
            try:
                vend = json.loads(g["output"]).get("vendors") or {}
            except json.JSONDecodeError:
                return ""
            return next(iter(vend), "") if isinstance(vend, dict) else ""
    return ""


def gather(plan_: dict, out: Path) -> dict:
    """Every planned step run in-process, in the plan's order, into the evidence bundle: the full text of each answer
    (check verifies quotes against it), evidence.md with each answer cut at its cap, and the boundaries."""
    out.mkdir(parents=True, exist_ok=True)
    gathered, boundaries = [], []
    for s in plan_["steps"]:
        row = {"n": s["n"], "key": s["key"], "section": s["section"], "title": s["title"], "command": s["command"], "args": s["args"], "anchor": s["anchor"], "status": s["status"]}
        if s["status"] == "boundary":
            boundaries.append({**row, "note": s["reason"]})
            gathered.append({**row, "output": "", "chars": 0, "empty": True, "boundary": s["reason"], "seconds": 0})
            continue
        if s["status"] == "deferred":
            vendor = top_vendor(gathered, s["anchor"])
            if not vendor:
                note = f"no vendor to ask about: the office view of {s['anchor']} names no vendor"
                boundaries.append({**row, "note": note})
                gathered.append({**row, "output": "", "chars": 0, "empty": True, "boundary": note, "seconds": 0, "status": "boundary"})
                continue
            s = {**s, "args": fill(s["args"], {"vendor": vendor}), "cli": s["cli"].replace("{vendor}", shlex.quote(vendor))}
            s["mcp"] = {"tool": s["command"], "arguments": {"args": s["args"]}}
            row = {**row, "args": s["args"]}
        got = run_step(s)
        if s["status"] == "optional" and UNAVAILABLE.search(got["output"]):
            note = f"optional section skipped: `{s['command']} {s['args']}` answered {got['output'].strip().splitlines()[0][:160]!r}"
            boundaries.append({**row, "note": note})
            gathered.append({**row, **got, "output": "", "empty": True, "boundary": note, "status": "skipped"})
            continue
        boundary = None
        if got["empty"]:
            boundary = (f"nothing gathered: `{s['command']} {s['args']}` answered {got['output'].strip().splitlines()[0][:160] if got['output'].strip() else '(nothing)'!r}; "
                        + boundary_reason(plan_["inventory"], s["needs"]))
            boundaries.append({**row, "note": boundary})
        gathered.append({**row, **got, "boundary": boundary})
    bundle = {"plan": "plan.json", "agency": plan_["agency"], "as_of": plan_["as_of"], "generated": now(), "from": "gather", "gathered": gathered, "boundaries": boundaries}
    (out / "plan.json").write_text(json.dumps(plan_, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "bundle.json").write_text(json.dumps(bundle, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "evidence.md").write_text(evidence_md(plan_, bundle), encoding="utf-8")
    (out / "schema.json").write_text(json.dumps(answer_schema(plan_), indent=1), encoding="utf-8")
    return bundle


def cut(text: str, cap: int, pointer: str) -> str:
    return text if len(text) <= cap else text[:cap] + f" ... ({pointer} for the rest)"


def evidence_md(plan_: dict, bundle: dict) -> str:
    caps = {s["key"]: s.get("cap", CAP) for s in plan_["steps"]}
    out = [f"# Evidence for {plan_['title']}", "", f"Record as of {plan_['as_of']}; gathered {bundle['generated']}; full text of every answer in bundle.json.", ""]
    if plan_["anchors"]:
        out += ["Anchor offices [inference]: " + "; ".join(f"{a['office']} ({a['basis']})" for a in plan_["anchors"]) + f". Rule: {plan_['anchor_rule']}.", ""]
    for g in bundle["gathered"]:
        out += [f"## {g['n']}. {g['title']} (`{f"{g['command']} {g['args']}".strip()}`)", ""]
        if g.get("boundary"):
            out += [f"**Boundary.** {g['boundary']}", ""]
        if g["output"]:
            out += ["```", cut(g["output"], caps.get(g["key"], CAP), f"navy.py {g['command']} {g['args']}".rstrip()), "```", ""]
    return "\n".join(out)


# ------------------------------------------------------------------ the headless run


def headless_prompt(plan_: dict, spec: dict) -> str:
    inv = plan_["inventory"]
    present = ", ".join(f"{k} ({v['rows']} rows" + (f", newest {v['newest']}" if v["newest"] else "") + ")" for k, v in inv["domains"].items() if v["present"])
    absent = ", ".join(k for k, v in inv["domains"].items() if not v["present"])
    lines = [f"Write the sections of an intelligence report on the {inv['label']} from its record alone, as of {inv['as_of']}. {plan_['objective']}",
             "",
             f"Work through the platform's {SERVER} tools, which answer from the record: {present}. Absent from this record: {absent or 'nothing'}. "
             "Start with the help tool, then the inventory tool. Call only the tools a section needs; each section below suggests the "
             "command that answers it, and you may call others the help lists when a part needs them.",
             "",
             f"Anchor offices, chosen by this rule ({plan_['anchor_rule']}): " + ("; ".join(f"{a['office']} ({a['basis']})" for a in plan_["anchors"]) or "none") + ".",
             "", "Sections (answer every key exactly as written):"]
    for s in plan_["steps"]:
        parts = "; ".join(s["required_parts"])
        if s["status"] == "boundary":
            lines.append(f"- {s['key']}: {s['question']}. BOUNDARY: {s['reason']}. Leave claims empty and state this in not_found.")
        else:
            lines.append(f"- {s['key']}: {s['question']}. Parts: {parts}. Suggested: {s['command']} {s['args']}".rstrip() + (" (optional: if the tool reports no database, say so in not_found)" if s["status"] == "optional" else ""))
    lines += ["",
              "Rules. Treat evidence as part of the data model: every claim is one fact with the identifier of the record, office, person, vendor "
              "or budget line it rests on, an exact quote copied from a tool's answer, and the source (the tool call that answered, or the URL "
              "the answer prints). A person may be read as a potential decision maker, budget holder or champion only as an inference "
              "resting on at least two claims (a title alone is never enough), with what is still to confirm; the words appear only "
              "after 'potential'. Keep "
              "facts, inferences and recommendations apart: an inference carries the rule it rests on and the identifiers of the claims it "
              "rests on. Where a part was not found, say what was read in not_found; where a section is a boundary, say so first and claim "
              f"nothing. Nothing after {inv['as_of']} exists for this report. {PLAIN} Never write 'will release' or 'RFP coming'. "
              "In `reading`, say in two sentences what the record supports and where it is thin."]
    return "\n".join(lines)


def transcript_bundle(messages: list[dict], plan_: dict) -> dict:
    """The tool calls of a headless session as an evidence bundle of the same shape gather writes: each call's text,
    placed on the plan step whose command and arguments it matches, else on its own."""
    results = {c["tool_use_id"]: c.get("content") for m in messages if m.get("type") == "user"
               for c in (m.get("message", {}).get("content") or []) if isinstance(c, dict) and c.get("type") == "tool_result"}
    text = lambda r: r if isinstance(r, str) else " ".join(i.get("text", "") for i in r or [] if isinstance(i, dict))
    prefix = f"mcp__{SERVER}__"
    by_call = {(s["command"], s["args"]): s for s in plan_["steps"]}
    gathered = []
    for m in messages:
        if m.get("type") != "assistant":
            continue
        for c in m.get("message", {}).get("content", []):
            if c.get("type") != "tool_use" or not c["name"].startswith(prefix):
                continue
            command = c["name"].removeprefix(prefix)
            args = (c.get("input") or {}).get("args", "")
            out = text(results.get(c["id"])) or ""
            s = by_call.get((command, args)) or next((s for s in plan_["steps"] if s["command"] == command and shlex.split(s["args"]) == shlex.split(args or "")), None)
            gathered.append({"n": len(gathered) + 1, "key": s["key"] if s else "", "section": s["section"] if s else "", "title": s["title"] if s else f"{command} {args}".rstrip(),
                             "command": command, "args": args, "anchor": s["anchor"] if s else "", "status": "called", "output": out, "chars": len(out),
                             "empty": is_empty(out), "boundary": None, "seconds": 0})
    boundaries = [{"n": s["n"], "key": s["key"], "section": s["section"], "title": s["title"], "command": s["command"], "args": s["args"], "anchor": s["anchor"],
                   "status": "boundary", "note": s["reason"]} for s in plan_["steps"] if s["status"] == "boundary"]
    return {"plan": "plan.json", "agency": plan_["agency"], "as_of": plan_["as_of"], "generated": now(), "from": "transcript", "gathered": gathered, "boundaries": boundaries}


def run(spec: dict, focus: str, budget_usd: str, out: Path) -> dict:
    """One headless Claude session on this agency's own read surface (the MCP server navy.py serves), writing the
    structured answer the schema asks for; then the checks and the report."""
    inv = inventory()
    plan_ = plan(spec, inv, focus)
    out.mkdir(parents=True, exist_ok=True)
    prompt, schema = headless_prompt(plan_, spec), answer_schema(plan_)
    (out / "plan.json").write_text(json.dumps(plan_, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    (out / "schema.json").write_text(json.dumps(schema, indent=1), encoding="utf-8")
    if not shutil.which("claude"):
        print(f"no `claude` on PATH; the prompt and schema are in {out} for a session run elsewhere")
        return {"error": "no claude"}
    got = claude(prompt, out, "", schema, budget_usd, ALLOWED["platform"], NAVY, stream=True)
    (out / "transcript.json").write_text(json.dumps(got.get("transcript", []), ensure_ascii=False), encoding="utf-8")
    if got.get("error") or not got.get("structured"):
        (out / "run.json").write_text(json.dumps({k: v for k, v in got.items() if k != "transcript"}, indent=1), encoding="utf-8")
        print(f"the session gave no structured answer: {got.get('error') or got.get('subtype') or got.get('result', '')[:300]}")
        return got
    answer = got["structured"]
    bundle = transcript_bundle(got["transcript"], plan_)
    if not bundle["gathered"]:  # a transcript without tool text (the CLI's `json` format carries only the result): the plan's own reads stand as the evidence
        bundle = gather(plan_, out)
        bundle["from"] = "gather (the transcript carried no tool calls; the plan's commands were run again for the evidence)"
    (out / "answer.json").write_text(json.dumps(answer, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "bundle.json").write_text(json.dumps(bundle, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "evidence.md").write_text(evidence_md(plan_, bundle), encoding="utf-8")
    (out / "run.json").write_text(json.dumps({"usage": got["usage"], "subtype": got.get("subtype"), "tools_called": Counter(g["command"] for g in bundle["gathered"])}, indent=1), encoding="utf-8")
    checked = check(bundle, answer, plan_)
    (out / "check.json").write_text(json.dumps(checked, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(render(plan_, bundle, answer, checked, got["usage"]), encoding="utf-8")
    return {"usage": got["usage"], "problems": checked["problems"], "out": str(out)}


# ------------------------------------------------------------------ the checks


_RESOLVED: dict[str, str | None] = {}


def resolve_any(ident: str, evidence: str = "") -> str | None:
    """What an identifier names in the record: a record (forecast key, notice, topic, contract or corpus id), an office,
    a person, a vendor, a budget line, a record file, or a URL the evidence prints. None when nothing does."""
    ident = (ident or "").strip()
    if not ident:
        return None
    if re.match(r"https?://", ident):
        return "url" if ident in evidence else None
    if ident in _RESOLVED:
        return _RESOLVED[ident]
    kind = None
    # an agent may write the kind before the identifier (person: Ann Example, PIID: HR001122F0003); the identifier is what counts
    bare = re.sub(r"^(person|people|office|domain|pulse|cell|piid|contract|award|notice|need|record|topic|vendor|budget line|line|pe|program)\s*:\s*", "", ident, flags=re.I)
    if bare != ident and bare:
        kind = resolve_any(bare, evidence)
        _RESOLVED[ident] = kind
        return kind
    if ident.lower() in DOMAINS:
        kind = "domain"
    try:
        from outreach_compare import resolve
        if resolve(walk(), ident, set()):
            kind = "record"
    except FileNotFoundError:
        pass
    if not kind and lay().org_id(ident):
        kind = "office"
    if not kind and ident.lower() in {str(o.get("acronym", "")).lower() for o in owners_payload().get("offices", [])} | {str(o.get("name", "")).lower() for o in owners_payload().get("offices", [])}:
        kind = "office"
    if not kind:
        names = {norm_name(r.get("name", "")) for r in lay().roster}
        if norm_name(ident) in names:
            kind = "person"
    if not kind:
        v = vendors.load()
        spellings = {k.upper() for k in (v.get("spelling_to_uei") or {})}
        if ident.upper() in spellings or any(ident == row.get("uei") for row in v.get("vendors", [])):
            kind = "vendor"
    if not kind:
        codes, programs = set(), set()
        for rows in owners_payload().get("facts", {}).values():
            for r in rows:
                if isinstance(r, dict):
                    codes |= {str(r.get(k, "")).lower() for k in ("id", "li", "pe", "code", "line_number") if r.get(k)}
                    if r.get("program"):
                        programs.add(str(r["program"]).lower())
        low = ident.lower()
        if low in codes or low.split()[0] in codes:  # a line by its number, or a program row by its element, project and name
            kind = "budget line"
        elif low in programs or any(low.endswith(" " + name) for name in programs):
            kind = "program row"
    if not kind and ident in inventory_files():
        kind = "record file"
    if not kind:
        ranking = pulse_payload().get("ranking", [])
        cell = re.sub(r"^[A-Za-z0-9/ .-]{1,40}:\s*", "", ident).strip().lower()  # the pulse view prints a cell as OFFICE: name
        if ident in {r.get("key") for r in ranking} or cell in {str(r.get("name", "")).strip().lower() for r in ranking}:
            kind = "ranked cell"
    _RESOLVED[ident] = kind
    return kind


def inventory_files() -> set[str]:
    return set(inventory()["files"])


def tool_call(s: str) -> str:
    """A tool call as a claim's source or a gathered step writes it: "AGENCY=navy python research/tools/navy.py office X",
    "navy.py office X", "mcp:office X" and "office X" are the same call."""
    s = re.sub(r"(?:^|\s)[A-Z_]+=\S+", " ", s)
    s = re.sub(r"\S*python[\d.]*\s", " ", s)
    s = re.sub(r"^\s*(?:\S*/)?navy\.py\s+|^\s*(?:mcp|tool):\s*", "", s.strip())
    try:
        s = " ".join(shlex.split(s))  # people 'PMA/PMW 101' and people PMA/PMW 101 are the same call
    except ValueError:
        pass
    return " ".join(s.split()).lower()


def url_blocks(output: str, url: str) -> list[str]:
    """The parts of a tool answer a URL source vouches for: each JSON record that carries the URL, or each paragraph
    of a text answer that prints it. A quote cited to the URL must sit in one of them, not anywhere in the answer.
    A record vouches for its own fields only: the records nested in it are judged on their own, so a wrapper that
    prints a source URL never lets a sibling row's quote pass."""
    try:
        data = json.loads(output)
    except ValueError:
        return [p for p in re.split(r"\n\s*\n", output) if url in p]
    nested = lambda v: isinstance(v, dict) or isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)  # noqa: E731
    blocks, stack = [], [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            own = {k: v for k, v in node.items() if not nested(v)}
            if any(isinstance(v, str) and url in v for v in own.values()):
                blocks.append(json.dumps(own, indent=1, ensure_ascii=False))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return blocks


def check(bundle: dict, answer: dict, plan_: dict) -> dict:
    """The written answer against the evidence and the record. Each problem is one line naming the section."""
    outputs = {g["key"]: g["output"] for g in bundle["gathered"] if g.get("output")}
    everything = "\n".join(outputs.values())
    calls = {}
    for g in bundle["gathered"]:
        if g.get("output"):
            calls.setdefault(tool_call(f"{g.get('command', '')} {g.get('args', '')}"), []).append(g["output"])
    status = {s["key"]: s["status"] for s in plan_["steps"]}
    cli = {f"{s['command']} {s['args']}".strip() for s in plan_["steps"]} | {s["cli"] for s in plan_["steps"]} | {f"navy.py {s['command']} {s['args']}".strip() for s in plan_["steps"]}
    problems, per = [], {}
    sections = answer.get("sections", {})
    for key, sec in sections.items():
        claims = sec.get("claims", [])
        row = {"claims": len(claims), "quotes_verified": 0, "identifiers_resolved": 0, "inferences": len(sec.get("inferences", [])), "problems": []}
        own = outputs.get(key, "")
        ids = set()
        for i, c in enumerate(claims, start=1):
            where = f"{key} claim {i}"
            quote = c.get("quote", "")
            src = (c.get("source") or "").strip()
            # the quote must be in the answer its source names; this section's own answer counts only when the source
            # names that call, and a URL vouches only for the record or paragraph that carries it
            cited = [b for o in outputs.values() for b in url_blocks(o, src)] if re.match(r"https?://", src) else calls.get(tool_call(src), [])
            if any(verbatim(quote, o) for o in cited):
                row["quotes_verified"] += 1
            elif verbatim(quote, everything):
                row["problems"].append(f"{where}: quote is not in the answer its source {src[:60]!r} names, only elsewhere in the gathered evidence: {quote[:60]!r}")
            else:
                row["problems"].append(f"{where}: quote not in the gathered evidence: {quote[:80]!r}")
            kind = resolve_any(c.get("identifier", ""), everything)
            if kind:
                row["identifiers_resolved"] += 1
                ids.add(c.get("identifier", "").strip())
            else:
                row["problems"].append(f"{where}: identifier {c.get('identifier', '')!r} names no record, office, person, vendor or budget line")
            if not (re.match(r"https?://", src) or src in cli or any(src.startswith(p) for p in ("navy.py ", "AGENCY=", "mcp:", "tool:")) or src.split(" ")[0] in navy.HELP or src.split(" ")[0] in LOCAL):
                row["problems"].append(f"{where}: source {src[:60]!r} is neither a tool call nor a URL")
        for i, inf in enumerate(sec.get("inferences", []), start=1):
            if not (inf.get("rule") or "").strip():
                row["problems"].append(f"{key} inference {i}: no rule stated")
            for r in inf.get("rests_on", []):
                if r.strip() not in ids:
                    row["problems"].append(f"{key} inference {i}: rests on {r!r}, which is not a claim identifier in this section")
            if ROLE_WORDS.search(inf.get("inference", "")) and len({r.strip() for r in inf.get("rests_on", [])}) < 2:
                row["problems"].append(f"{key} inference {i}: a potential decision maker, budget holder or champion rests on at least two claims; a title alone is not enough")
        prose = " ".join([sec.get("summary", ""), *(c.get("claim", "") for c in claims), *(f"{i.get('inference', '')} {i.get('rule', '')}" for i in sec.get("inferences", [])), sec.get("not_found", "")])
        row["problems"] += [f"{key}: {p}" for p in register_problems(prose)]
        for m in ROLE_WORDS.finditer(prose):
            if "potential" not in prose[max(0, m.start() - 60):m.start()].lower():
                row["problems"].append(f"{key}: '{m.group(0)}' without 'potential'; a record shows a role, never a decision, budget authority or advocacy")
        if status.get(key) == "boundary":
            if claims:
                row["problems"].append(f"{key}: a boundary section carries claims; the record holds nothing for it")
            if not (sec.get("not_found") or "").strip():
                row["problems"].append(f"{key}: a boundary section must say what was read in not_found")
        elif key in status and own and not claims and not (sec.get("not_found") or "").strip():
            row["problems"].append(f"{key}: the evidence answered but the section has neither claims nor a not_found")
        per[key] = row
        problems += row["problems"]
    for key in status:
        if key not in sections:
            problems.append(f"{key}: planned section missing from the answer")
    for key in sections:
        if key not in status:
            problems.append(f"{key}: section not in the plan")
    domains = sorted({d for s in plan_["steps"] for d in s["needs"] if per.get(s["key"], {}).get("quotes_verified", 0) and per[s["key"]]["identifiers_resolved"]})
    return {"problems": problems, "per_section": per, "claims": sum(r["claims"] for r in per.values()),
            "quotes_verified": sum(r["quotes_verified"] for r in per.values()), "identifiers_resolved": sum(r["identifiers_resolved"] for r in per.values()),
            "domains_with_verified_claims": domains, "checked": now()}


# ------------------------------------------------------------------ the report


def md_cell(text: str) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def render(plan_: dict, bundle: dict, answer: dict, checked: dict, usage: dict | None = None) -> str:
    inv = plan_["inventory"]
    out = [f"# {plan_['title']}", "",
           f"{inv['label']} record as of {plan_['as_of']}; spec `{plan_['spec']}` ({plan_['mode']}); generated {now()}" + (f"; focus {plan_['focus']!r}" if plan_["focus"] else "") + ".", "",
           "## 0. How to read this", "",
           "- A **fact** row carries the identifier of the record, office, person, vendor or budget line it rests on, an exact quote from the "
           "tool's answer, and the tool call or URL that is its source. Every quote was checked against the gathered evidence.",
           "- **[inference]** marks a reading built from named facts, with the rule it rests on. The anchor offices are an inference.",
           "- A **Boundary** states first what the record does not hold for a section: the files read, their rows and newest dates. "
           "Nothing is counted as zero; it is counted as not read.",
           f"- Nothing after {plan_['as_of']} exists for this report; the record's own date is the report's.", "",
           "## 1. What the record holds", "",
           "| Domain | Present | Rows | Newest | Answered by |", "|---|---|---|---|---|"]
    for name, d in inv["domains"].items():
        out.append(f"| {name} | {'yes' if d['present'] else 'no'} | {d['rows']:,} | {d['newest'] or ''} | {md_cell(d['answered_by'])} |")
    if inv["corpus"].get("note"):
        out += ["", f"**Boundary.** {inv['corpus']['note']}."]
    truth = inv.get("truth") or {}
    if truth:
        out += ["", f"Truth table: {truth.get('verdict') or 'no verdict'}" + (f", brief-ready {'yes' if truth.get('brief_ready') else 'no'}" if truth.get("brief_ready") is not None else "") + f" ({truth.get('built_from', '')})."]
    out += ["", "## 2. Anchor offices [inference]", ""]
    if plan_["anchors"]:
        out += ["| Office | Basis |", "|---|---|", *(f"| {a['office']} | {md_cell(a['basis'])} |" for a in plan_["anchors"]), ""]
    else:
        out += ["No anchor office: the record places no budget measure and no ranked cell on an office this layer resolves.", ""]
    out += [f"Rule: {plan_['anchor_rule']}.", ""]
    sections = answer.get("sections", {})
    reading = answer.get("reading", "")
    if reading:
        out += ["## 3. Reading", "", reading, ""]
    n = 4 if reading else 3
    by_key = {g["key"]: g for g in bundle["gathered"]}
    for s in plan_["steps"]:
        g = by_key.get(s["key"], {})
        sec = sections.get(s["key"], {})
        out += [f"## {n}. {s['title']}", "", f"*{s['question']}.* Answered by `{f"{s['command']} {s['args']}".strip()}`.", ""]
        n += 1
        if s["status"] == "boundary" or g.get("boundary"):
            out += [f"**Boundary.** {g.get('boundary') or s['reason']}", ""]
            if sec.get("not_found"):
                out += [sec["not_found"], ""]
            continue
        if sec.get("summary"):
            out += [sec["summary"], ""]
        if sec.get("claims"):
            out += ["**Facts**", "", "| Claim | Identifier | Quote | Source |", "|---|---|---|---|"]
            out += [f"| {md_cell(c.get('claim'))} | {md_cell(c.get('identifier'))} | \"{md_cell(c.get('quote'))}\" | {md_cell(c.get('source'))} |" for c in sec["claims"]]
            out.append("")
        if sec.get("inferences"):
            out += ["**Analysis [inference]**", ""]
            out += [f"- {i.get('inference', '')} Rule: {i.get('rule', '')}." + (f" Rests on {', '.join(i.get('rests_on', []))}." if i.get("rests_on") else "") for i in sec["inferences"]]
            out.append("")
        if sec.get("not_found"):
            out += [f"**Not found.** {sec['not_found']}", ""]
        if not sec:
            out += ["*No section written.* " + (f"The evidence holds {g.get('chars', 0):,} characters from `{s['command']} {s['args']}`." if g.get("chars") else ""), ""]
    out += [f"## {n}. Sources", ""]
    idents = {}
    for key, sec in sections.items():
        for c in sec.get("claims", []):
            ident = (c.get("identifier") or "").strip()
            if ident and ident not in idents:
                idents[ident] = resolve_any(ident, "\n".join(g.get("output", "") for g in bundle["gathered"]))
    records = [i for i, k in idents.items() if k == "record"]
    if records:
        text = navy.call("sources", {"args": " ".join(shlex.quote(i) for i in records[:40])}) if FIXTURE_WALK is None else ""
        for block in [b for b in text.split("\n### ") if b.strip()]:
            out += [("### " if not block.startswith("###") else "") + cut(block.strip(), CAP, "navy.py sources ID"), ""]
    for ident, kind in idents.items():
        if kind == "record":
            continue
        pointer = {"office": f"navy.py page office {shlex.quote(ident)}; navy.py neighbors {shlex.quote(ident)}", "person": f"navy.py page person {shlex.quote(ident)}",
                   "vendor": f"navy.py vendor {shlex.quote(ident)}", "budget line": f"office_owners.py page OFFICE; budget.py show {shlex.quote(ident)}",
                   "record file": f"report.py inventory ({ident})", "domain": f"report.py inventory ({ident})", "ranked cell": f"navy.py cell {shlex.quote(ident)}",
                   "program row": f"office_owners.py page OFFICE (program rows)",
                   "url": ident, None: "unresolved: names nothing in the record"}[kind]
        out.append(f"- {ident}: {kind or 'unresolved'}; {pointer}")
    if idents:
        out.append("")
    n += 1
    out += [f"## {n}. Method", "", "| Step | Command | Status | Characters |", "|---|---|---|---|"]
    out += [f"| {g.get('n', i)} | `{f"{g['command']} {g['args']}".strip()}`" + f" | {g.get('status', '')}{' (empty)' if g.get('empty') and g.get('status') != 'boundary' else ''} | {g.get('chars', 0):,} |" for i, g in enumerate(bundle["gathered"], start=1)]
    out += ["", f"Checks: {checked.get('claims', 0)} claims, {checked.get('quotes_verified', 0)} quotes verified, {checked.get('identifiers_resolved', 0)} identifiers resolved, "
            f"{len(checked.get('problems', []))} problems; domains with verified claims: {', '.join(checked.get('domains_with_verified_claims', [])) or 'none'}."]
    if usage:
        out.append(f"Headless session: model {usage.get('model')}, {usage.get('turns')} turns, {usage.get('input_tokens', 0):,} input and {usage.get('output_tokens', 0):,} output tokens, "
                   f"{usage.get('seconds')} s" + (f", {usage.get('cost_usd'):.2f} USD" if usage.get("cost_usd") is not None else "") + ".")
    out += ["", "Evidence gathered from the record only: " + ", ".join(f"`{f"{s['command']} {s['args']}".strip()}`" for s in plan_["steps"] if s["status"] != "boundary") + "."]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ navy.py's two commands, the CLI and the selfcheck


def navy_entry(command: str, args: list[str]) -> int:
    """`navy.py inventory` and `navy.py report plan|budget`, so an agent on the server can discover and decide."""
    if command == "inventory":
        print(json.dumps(inventory(), indent=1, ensure_ascii=False))
        return 0
    if not args or args[0] not in ("plan", "budget"):
        print("usage: report plan [--spec NAME] [--focus TERM] [--mode brief|full] [--schema] | budget OFFICE")
        return 2
    if args[0] == "budget":
        print(budget_text(" ".join(args[1:])) if args[1:] else "usage: report budget OFFICE")
        return 0 if args[1:] else 2
    ns = argparse.ArgumentParser(prog="report plan", add_help=False)
    ns.add_argument("--spec", default="agency-brief"); ns.add_argument("--focus", default=""); ns.add_argument("--mode", default=None); ns.add_argument("--schema", action="store_true")
    try:
        opts = ns.parse_args(args[1:])
        plan_ = plan(load_spec(opts.spec), inventory(), opts.focus, opts.mode)
    except (SystemExit, FileNotFoundError, ValueError) as e:
        print(f"report plan: {e}")
        return 2
    print(json.dumps(answer_schema(plan_) if opts.schema else plan_, indent=1, ensure_ascii=False, default=str))
    return 0


def default_out(spec: dict, inv: dict, focus: str, suffix: str = "") -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", focus.lower()).strip("-")[:40] if focus else ""
    return REPORTS / "-".join(p for p in (spec["name"], inv["as_of"], slug, suffix) if p)


def load_run(out: Path) -> tuple[dict, dict, dict]:
    plan_ = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    bundle = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    answer = json.loads((out / "answer.json").read_text(encoding="utf-8")) if (out / "answer.json").exists() else {"sections": {}, "reading": ""}
    return plan_, bundle, answer


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selfcheck"]:
        return selfcheck()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("specs", "inventory", "plan", "gather", "bundle", "check", "render", "run"):
        p = sub.add_parser(name)
        p.add_argument("--agency", help="the profile to answer for (the process re-runs under it)")
        if name in ("plan", "gather", "run"):
            p.add_argument("--spec", default="agency-brief"); p.add_argument("--focus", default=""); p.add_argument("--mode", choices=MODES, default=None)
        if name == "plan":
            p.add_argument("--schema", action="store_true", help="print the answer schema instead of the plan")
        if name in ("gather", "bundle", "check", "render", "run"):
            p.add_argument("--out", default="", help="the run folder (default: RESULTS/reports/<spec>-<as_of>[-focus])")
        if name == "check":
            p.add_argument("--answer", default="", help="the answer JSON (default: OUT/answer.json)")
        if name == "run":
            p.add_argument("--budget-usd", default="8")
    a = ap.parse_args(argv)
    if a.cmd == "specs":
        for name in specs():
            s = load_spec(name)
            print(f"{name:14s} {s['mode']:5s} {len(s['sections'])} sections, {s['anchors']['count']} anchors by {'/'.join(s['anchors']['by'])}: {s['objective']}")
        return 0
    if a.cmd == "inventory":
        print(json.dumps(inventory(), indent=1, ensure_ascii=False))
        return 0
    if a.cmd in ("plan", "gather", "run"):
        spec, inv = load_spec(a.spec), inventory()
        if a.cmd == "plan":
            plan_ = plan(spec, inv, a.focus, a.mode)
            print(json.dumps(answer_schema(plan_) if a.schema else plan_, indent=1, ensure_ascii=False, default=str))
            return 0
        out = Path(a.out) if a.out else default_out(spec, inv, a.focus, "headless" if a.cmd == "run" else "")
        if a.cmd == "gather":
            plan_ = plan(spec, inv, a.focus, a.mode)
            bundle = gather(plan_, out)
            full = [g for g in bundle["gathered"] if g["output"] and not g["empty"]]
            print(f"{len(full)} of {len(plan_['steps'])} steps answered, {len(bundle['boundaries'])} boundaries; evidence in {out}")
            for g in bundle["gathered"]:
                print(f"  {g['n']:2d} {g.get('status', ''):9s} {g['chars']:7,d}  {g['command']} {g['args']}".rstrip())
            return 0
        got = run(spec, a.focus, a.budget_usd, out)
        if got.get("error"):
            return 1
        print(f"headless run in {out}: {got['usage']['turns']} turns, {got['usage']['cost_usd']} USD, {len(got['problems'])} check problems")
        return 0
    out = Path(a.out)
    if a.cmd == "bundle":  # the evidence for a run folder whose answer exists but whose bundle does not: the plan's own reads
        plan_ = json.loads((out / "plan.json").read_text(encoding="utf-8"))
        bundle = gather(plan_, out)
        print(f"{sum(1 for g in bundle['gathered'] if g['output'])} answers gathered into {out / 'bundle.json'}")
        return 0
    plan_, bundle, answer = load_run(out)
    if getattr(a, "answer", ""):
        answer = json.loads(Path(a.answer).read_text(encoding="utf-8"))
    if a.cmd == "check":
        checked = check(bundle, answer, plan_)
        (out / "check.json").write_text(json.dumps(checked, indent=1, ensure_ascii=False), encoding="utf-8")
        print("\n".join(f"- {p}" for p in checked["problems"]) if checked["problems"] else "all checks pass")
        print(f"{checked['claims']} claims, {checked['quotes_verified']} quotes verified, {checked['identifiers_resolved']} identifiers resolved; "
              f"domains with verified claims: {', '.join(checked['domains_with_verified_claims']) or 'none'}")
        return 1 if checked["problems"] else 0
    checked = json.loads((out / "check.json").read_text(encoding="utf-8")) if (out / "check.json").exists() else check(bundle, answer, plan_)
    usage = json.loads((out / "run.json").read_text(encoding="utf-8")).get("usage") if (out / "run.json").exists() else None
    (out / "report.md").write_text(render(plan_, bundle, answer, checked, usage), encoding="utf-8")
    print(f"wrote {out / 'report.md'}")
    return 0


def selfcheck() -> int:
    global FIXTURE_WALK
    from outreach import fixture
    for name in specs():
        assert validate_spec(json.loads((SPECS / f"{name}.json").read_text(encoding="utf-8"))) == [], name
    assert "agency-brief" in specs() and "agency-full" in specs(), specs()
    assert validate_spec({"name": "x", "mode": "brief", "title": "t", "objective": "o", "anchors": {"count": 1, "by": ["budget"]},
                          "sections": [{"key": "a", "title": "t", "question": "q", "command": "nope", "args": "{thing}", "needs": ["x"], "per_anchor": True, "required_parts": [], "modes": ["brief"]}]})
    assert fill("changed {office}", {"office": "PMA/PMW 101"}) == "changed 'PMA/PMW 101'" and fill("{focus}", {"focus": ""}) == ""
    assert is_empty("no organization by that name") and is_empty("(no output)") and is_empty("usage: x") and not is_empty('{"office": "BTO"}')
    FIXTURE_WALK = fixture()
    try:
        layer = FIXTURE_WALK.layer
        owners = {"offices": [{"uuid": "u1", "acronym": "PMA/PMW 101", "name": "MIDS"}, {"uuid": "u2", "acronym": "PMW 205", "name": "NEN"}],
                  "inferences": {"budget_held": [{"office": "u1", "display_enacted": None, "shared_lines_enacted": 12.5}, {"office": "u2", "display_enacted": 40.0}]}}
        ranking = [{"office": "N00039"}, {"office": "N00039"}, {"office": "PMW 205"}]
        got = anchors({}, layer, "", 3, ["budget", "pulse"], owners, ranking)
        assert [a["office"] for a in got] == ["PMW 205", "PMA/PMW 101", "N00039"], got
        assert got[0]["basis"].startswith("budget: Comptroller display") and got[2]["basis"].startswith("pulse: 2 of the 3"), got
        assert anchors({}, layer, "", 3, ["budget"], {"offices": [], "inferences": {}}, []) == []
        inv = {"agency": "navy", "label": "Department of the Navy", "short": "Navy", "as_of": "2026-09-01", "corpus": {"exists": True, "note": ""},
               "domains": {d: {"present": d not in ("awards", "budget"), "rows": 0 if d in ("awards", "budget") else 5, "newest": "", "files": ["buying_dna.json"] if d == "awards" else ["corpus.json"], "answered_by": ""} for d in DOMAINS},
               "files": {"buying_dna.json": {"rows": 0, "newest": ""}, "corpus.json": {"rows": 5, "newest": "2026-09-01"}}}
        spec = load_spec("agency-brief")
        plan_ = plan(spec, inv, "", None, layer, owners, ranking)
        status = {(s["section"], s["anchor"]): s["status"] for s in plan_["steps"]}
        assert status[("budget", "PMW 205")] == "boundary" and status[("vendors", "PMW 205")] == "boundary" and status[("people", "PMW 205")] == "planned", status
        assert all("buying_dna.json (0 rows)" in s["reason"] for s in plan_["steps"] if s["section"] == "vendors"), [s["reason"] for s in plan_["steps"] if s["section"] == "vendors"]
        assert set(answer_schema(plan_)["properties"]["sections"]["required"]) == {s["key"] for s in plan_["steps"]}
        people_key = next(s["key"] for s in plan_["steps"] if s["section"] == "people" and s["anchor"] == "PMA/PMW 101")
        budget_key = next(s["key"] for s in plan_["steps"] if s["section"] == "budget" and s["anchor"] == "PMA/PMW 101")
        bundle = {"gathered": [{"key": people_key, "section": "people", "command": "people", "args": "'PMA/PMW 101'", "anchor": "PMA/PMW 101", "status": "planned",
                                "output": "Ann Example, Program Manager, PMA/PMW 101; presolicitation N00039-25-RFPREQ-PMA/PMW-101-0046 MIDS WDL SF3 Radio", "chars": 90, "empty": False, "boundary": None},
                               {"key": budget_key, "section": "budget", "command": "report", "args": "budget 'PMA/PMW 101'", "anchor": "PMA/PMW 101", "status": "boundary", "output": "", "chars": 0, "empty": True, "boundary": "budget: 0 rows"}],
                  "boundaries": []}
        empty = {k: {"summary": "", "claims": [], "inferences": [], "not_found": "nothing read"} for k in answer_schema(plan_)["properties"]["sections"]["required"]}
        good = {"sections": {**empty, people_key: {"summary": "One program manager is tied to the office.",
                                                    "claims": [{"claim": "Ann Example manages MIDS.", "identifier": "Ann Example", "quote": "Ann Example, Program Manager", "source": "people 'PMA/PMW 101'"},
                                                               {"claim": "The office has a radio requirement.", "identifier": "N00039-25-RFPREQ-PMA/PMW-101-0046", "quote": "MIDS WDL SF3 Radio", "source": "people 'PMA/PMW 101'"}],
                                                    "inferences": [{"inference": "The program manager answers for the radio requirement.", "rule": "a program manager answers for the requirements of the office that lists them", "rests_on": ["Ann Example"]}],
                                                    "not_found": ""}}, "reading": "r"}
        got = check(bundle, good, plan_)
        assert got["problems"] == [], got["problems"]
        assert got["domains_with_verified_claims"] == ["people", "record"] and got["quotes_verified"] == 2, got
        bad = lambda **kw: {"sections": {**good["sections"], people_key: {**good["sections"][people_key], **kw}}, "reading": "r"}
        c0 = good["sections"][people_key]["claims"][0]
        assert any("quote not in" in p for p in check(bundle, bad(claims=[{**c0, "quote": "Ann Example, Director"}]), plan_)["problems"])
        assert any("names no record" in p for p in check(bundle, bad(claims=[{**c0, "identifier": "ZZ-NOT-A-RECORD-1"}]), plan_)["problems"])
        assert any("neither a tool call nor a URL" in p for p in check(bundle, bad(claims=[{**c0, "source": "my memory"}]), plan_)["problems"])
        assert any("likely" in p for p in check(bundle, bad(summary="The office will likely buy radios."), plan_)["problems"])
        assert any("decision-maker" in p for p in check(bundle, bad(summary="She is the decision-maker."), plan_)["problems"])
        one = [{"inference": "Ann Example is a potential decision maker for the radio requirement.", "rule": "r", "rests_on": ["Ann Example"]}]
        assert any("title alone" in p for p in check(bundle, bad(inferences=one), plan_)["problems"])
        two = [{**one[0], "rests_on": ["Ann Example", "N00039-25-RFPREQ-PMA/PMW-101-0046"]}]
        assert check(bundle, bad(inferences=two), plan_)["problems"] == []
        other = {**bundle, "gathered": [*bundle["gathered"], {"key": "office:PMW 205", "section": "office", "command": "office", "args": "'PMW 205'",
                                                                "output": "Bob Other, Contracting Officer, PMW 205", "chars": 40, "empty": False, "boundary": None}]}
        stray = {**c0, "identifier": "Ann Example", "quote": "Bob Other, Contracting Officer"}
        assert any("only elsewhere" in p for p in check(other, bad(claims=[stray]), plan_)["problems"])
        assert check(other, bad(claims=[{**stray, "source": "navy.py office 'PMW 205'"}]), plan_)["problems"] == []
        # this section's own answer does not rescue a quote whose source names another call
        assert any("only elsewhere" in p for p in check(other, bad(claims=[{**c0, "source": "navy.py office 'PMW 205'"}]), plan_)["problems"])
        assert any("no rule" in p for p in check(bundle, bad(inferences=[{"inference": "x", "rule": "", "rests_on": []}]), plan_)["problems"])
        assert any("not a claim identifier" in p for p in check(bundle, bad(inferences=[{"inference": "x", "rule": "r", "rests_on": ["PMW 205"]}]), plan_)["problems"])
        boundary_bad = {"sections": {**good["sections"], budget_key: {"summary": "", "claims": [c0], "inferences": [], "not_found": ""}}, "reading": "r"}
        assert sum("boundary section" in p for p in check(bundle, boundary_bad, plan_)["problems"]) == 2
        text = render(plan_, bundle, good, got)
        for head in ("## 0. How to read this", "## 1. What the record holds", "## 2. Anchor offices [inference]", "**Facts**", "**Analysis [inference]**", "**Boundary.**", "Sources", "Method"):
            assert head in text, head
        budget_at = text.index("Who holds the budget at PMA/PMW 101")
        assert text.index("**Boundary.**", budget_at) < text.index("\n## ", budget_at + 1) and "**Facts**" not in text[budget_at:text.index("\n## ", budget_at + 1)]
        assert not register_problems(render(plan_, bundle, empty_answer(plan_), check(bundle, empty_answer(plan_), plan_)))
    finally:
        FIXTURE_WALK = None
    print("report selfcheck ok")
    return 0


def empty_answer(plan_: dict) -> dict:
    return {"sections": {s["key"]: {"summary": "", "claims": [], "inferences": [], "not_found": s["reason"] or "not written"} for s in plan_["steps"]}, "reading": ""}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

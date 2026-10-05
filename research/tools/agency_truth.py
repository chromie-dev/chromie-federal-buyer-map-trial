#!/usr/bin/env python3
"""The agency truth table: what is true of each agency layer, read from the files alone, beside what the documents
claim and beside what was observed at run time.

Three kinds of statement are kept apart, as the working agreement asks:

- file-derived columns, deterministic, regenerated from the repository's files by `build` (a read-back stage);
- runtime observations (a database built, a pipeline stage that ran or failed, a test run), each recorded once by
  `observe` with the date, the command and the commit that observed it, in `agency_truth_observations.jsonl`, and
  shown as observations, never as file facts;
- claims, hand-kept in `agency_claims.json`, each quoting the sentence with its path and line, printed beside what the
  files show and flagged where the two disagree.

The verdict per agency is one of `in-code-and-collected`, `in-code-partial`, `branch-only`, `not-in-code`, each with a
one-line reason and a brief-ready yes or no for the startup-agency-brief workflow. The verdict is a gate: P0 and P1
source rows, the promotion of a pending portal into a registry and a new brief are allowed only for an agency whose
verdict starts with `in-code`.

    python research/tools/agency_truth.py build [--check]     # writes research/sources/agency_truth.json and research/docs/22_agency_truth_table.md
    python research/tools/agency_truth.py observe --agency darpa --kind db_built --command "..." --result "..." [--note "..."]
    python research/tools/agency_truth.py --selfcheck
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "tools"))
import agency  # noqa: E402  (PROFILES: every profile the code knows, whatever AGENCY is set to)

SOURCES_DIR = ROOT / "research" / "sources"
CLAIMS = SOURCES_DIR / "agency_claims.json"
OBSERVATIONS = SOURCES_DIR / "agency_truth_observations.jsonl"
OUT_JSON = SOURCES_DIR / "agency_truth.json"
OUT_MD = ROOT / "research" / "docs" / "22_agency_truth_table.md"
VERDICTS = ("in-code-and-collected", "in-code-partial", "branch-only", "not-in-code")
OBSERVATION_KINDS = ("db_built", "stage_ran", "stage_failed", "tests", "collect_ran")
# The event files a layer may hold, and the family each one is: the same families the coverage matrix uses.
EVENT_FILES = {"budget_lines.json": "budget", "budget_measures.json": "budget", "congress_events.json": "congress", "fedreg_events.json": "organization",
               "news_observations.json": "news", "oversight_events.json": "oversight", "remarks_events.json": "leaders",
               "protest_events.json": "protest", "assistance_awards.json": "incumbent", "sbir_topics.json": "programs",
               "hiring_observations.json": "hiring", "vendor_hiring_observations.json": "hiring"}
# The key under which each event file lists its rows.
ROW_KEYS = ("rows", "events", "topics", "articles", "postings", "awards", "documents", "lines", "observations", "findings")
# A brief rests on the record's families; below this many families with events it would be mostly Still open.
BRIEF_FAMILIES = 5


def research_dir(key: str) -> Path:
    return ROOT / "research" if key == "navy" else ROOT / "research" / "agencies" / key


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def rows_of(payload) -> list:
    """The rows an event file holds, whatever it calls them; a dict of documents each carrying events counts the events."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ROW_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            if key == "documents" and value and isinstance(value[0], dict) and "events" in value[0]:
                # an event under a document is dated the day the document was issued when it states no day of its own
                return [e if any(e.get(k) for k in ("date", "published", "issued")) else {**e, "date": d.get("issued") or d.get("date") or ""}
                        for d in value for e in d.get("events", [])]
            return value
    return []


def dates_in(rows: list) -> list[str]:
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in ("date", "published", "opened", "pre_release", "issued", "posted", "filed", "action_date", "retrieved_at"):
            v = r.get(k)
            if isinstance(v, str) and len(v) >= 10 and v[4] == "-":
                out.append(v[:10])
                break
    return out


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def profile_locations(key: str) -> dict:
    """Where the code defines the profile: inline in agency.py, a file under agency_profiles/, or on another branch only."""
    inline = key in getattr(agency, "PROFILES", {})
    file_ = (ROOT / "research" / "tools" / "agency_profiles" / f"{key}.py").exists()
    branches = []
    for ref in git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").splitlines():
        if ref.endswith("/HEAD"):
            continue
        # a profile file on another branch only: the agency is branch-only until that branch lands
        exists = subprocess.run(["git", "cat-file", "-e", f"{ref}:research/tools/agency_profiles/{key}.py"], cwd=ROOT, capture_output=True)
        if exists.returncode == 0:
            branches.append(ref)
    return {"in_code": inline or file_, "where": "agency.py" if inline and not file_ else ("agency_profiles" if file_ else ""),
            "branches_with_profile_file": sorted(branches)}


def files_for(key: str) -> dict:
    """Everything the files alone say about one agency layer."""
    d = research_dir(key)
    registry = load_json(d / "sources" / "source_registry.json") or []
    status = load_json(d / "sources" / "source_status.json") or {"sources": {}}
    matrix = load_json(d / "sources" / "coverage_matrix.json")
    corpus = load_json(d / "results" / "corpus.json")
    seed = load_json(d / "memory" / "organization_seed.json")
    families: dict[str, dict] = {}
    for name, family in EVENT_FILES.items():
        payload = load_json(d / "events" / name)
        if payload is None:
            continue
        rows = rows_of(payload)
        dates = dates_in(rows)
        slot = families.setdefault(family, {"files": [], "rows": 0, "newest": ""})
        slot["files"].append(name)
        slot["rows"] += len(rows)
        slot["newest"] = max([slot["newest"], *dates]) if dates else slot["newest"]
    corpus_families = Counter(e.get("family", "") for e in (corpus or {}).get("events", [])) if corpus else Counter()
    with_docs = sorted(k for k, s in status["sources"].items() if s.get("documents"))
    without = sorted(k for k, s in status["sources"].items() if not s.get("documents"))
    newest_doc = {}
    for k, s in status["sources"].items():
        fam = agency_family(key, k)
        if s.get("documents") and s.get("last_seen"):
            newest_doc[fam] = max(newest_doc.get(fam, ""), s["last_seen"][:10])
    cells = (matrix or {}).get("cells", [])
    remarks = load_json(d / "events" / "remarks_events.json") or {}
    official = [doc for doc in remarks.get("documents", []) if doc.get("kind") in ("speech", "testimony", "statement")]
    leaders = {"official_documents": len(official), "newest_official": max([doc.get("issued") or "" for doc in official], default=""),
               "official_with_speaker": sum(1 for doc in official if doc.get("speaker_name")),
               "conference_documents": sum(1 for doc in remarks.get("documents", []) if doc.get("kind") == "conference"),
               "events_naming_a_person": sum(1 for doc in remarks.get("documents", []) for e in doc.get("events", []) if e.get("person")),
               "events": sum(len(doc.get("events", [])) for doc in remarks.get("documents", []))}
    return {
        "layer_folder": str(d.relative_to(ROOT)) if d.exists() else "",
        "layer_folder_exists": d.exists(),
        "registry_rows": len(registry),
        "sources_with_documents": with_docs,
        "sources_without_documents": without,
        "status_file": (d / "sources" / "source_status.json").exists(),
        "event_files": {f: families[f] for f in sorted(families)},
        "corpus": {"exists": corpus is not None, "frozen_at": (corpus or {}).get("frozen_at", ""),
                   "events": len((corpus or {}).get("events", [])), "orgs": len((corpus or {}).get("orgs", {}) or {}),
                   "needs": (corpus or {}).get("needs", 0) if isinstance((corpus or {}).get("needs"), int) else len((corpus or {}).get("needs", []) or []),
                   "families": dict(sorted(corpus_families.items()))},
        "memory_nodes": len((seed or {}).get("nodes", [])),
        # the leaders family by its own file: speeches, testimony and statements (official) apart from discovered conference pages
        "leaders_statements": leaders,
        "newest_document_by_family": dict(sorted(newest_doc.items())),
        "coverage_cells": {"covered": sum(1 for c in cells if c.get("sources")), "total": len(cells)},
    }


def agency_family(key: str, source_key: str) -> str:
    """The family a registry source feeds: the shared FAMILY map plus the profile's own additions."""
    base = {"sam_gov_site_api": "notice", "fpds_atom_feed": "incumbent", "usaspending_api": "incumbent", "gao_reports": "oversight",
            "oversight_gov_reports": "oversight", "house_committee_repository": "congress", "conference_pages_exa": "conference",
            "sbir_sttr_topics": "programs", "gao_bid_protests": "protest", "govinfo_api": "congress", "federal_register": "organization",
            "usajobs_historic_joa": "hiring", "vendor_jobs_routergrowth": "hiring", "news_articles_exa": "news"}
    prof = agency.PROFILES.get(key, {})
    return {**base, **(prof.get("families") or {})}.get(source_key, "other")


def verdict_for(key: str, loc: dict, f: dict) -> dict:
    """The verdict and its reason, from the file-derived columns alone."""
    families_with_events = sorted(k for k, v in f["corpus"]["families"].items() if v)
    if not loc["in_code"]:
        if loc["branches_with_profile_file"]:
            v, reason = "branch-only", f"profile file only on {', '.join(loc['branches_with_profile_file'])}; nothing under research/ on this branch reads it"
        else:
            v, reason = "not-in-code", "no profile in agency.py or agency_profiles/ on any fetched branch"
    elif f["corpus"]["exists"] and f["corpus"]["events"] and f["sources_with_documents"]:
        v, reason = "in-code-and-collected", (f"profile in {loc['where']}; corpus frozen {f['corpus']['frozen_at'][:10]} with {f['corpus']['events']} events in "
                                             f"{len(families_with_events)} families; {len(f['sources_with_documents'])} sources with documents")
    else:
        missing = []
        if not f["corpus"]["exists"]:
            missing.append("no results/corpus.json")
        elif not f["corpus"]["events"]:
            missing.append("corpus holds no events")
        if not f["sources_with_documents"]:
            missing.append("no source has collected a document")
        v, reason = "in-code-partial", f"profile in {loc['where']}; " + "; ".join(missing)
    record_dir = f["layer_folder_exists"]
    brief_ready = v == "in-code-and-collected" and record_dir and len(families_with_events) >= BRIEF_FAMILIES
    why = ("check.py has a record directory and the corpus carries events in at least %d families" % BRIEF_FAMILIES if brief_ready
           else "no record directory for check.py" if not record_dir
           else f"the brief would be mostly Still open: {len(families_with_events)} families with events (needs {BRIEF_FAMILIES})" if v.startswith("in-code")
           else "the agency is not in code on this branch")
    return {"verdict": v, "reason": reason, "brief_ready": bool(brief_ready), "brief_ready_reason": why}


def observations() -> list[dict]:
    if not OBSERVATIONS.exists():
        return []
    return [json.loads(l) for l in OBSERVATIONS.read_text(encoding="utf-8").splitlines() if l.strip()]


def claims() -> list[dict]:
    return (load_json(CLAIMS) or {}).get("claims", [])


def check_claim(claim: dict, table: dict) -> dict:
    """One claim beside what the files show. `expects` names a file-derived field and the value the claim implies."""
    row = table.get(claim["agency"])
    if row is None:
        return {**claim, "found": None, "verdict": "agency not in the table"}
    field = claim["expects"]["field"]
    found = row["files"]
    for part in field.split("."):
        found = found.get(part) if isinstance(found, dict) else None
    want = claim["expects"]["value"]
    op = claim["expects"].get("op", "==")
    ok = {"==": lambda a, b: a == b, ">": lambda a, b: (a or 0) > b, ">=": lambda a, b: (a or 0) >= b,
          "<": lambda a, b: (a or 0) < b, "in": lambda a, b: a in b, "truthy": lambda a, b: bool(a) == b}[op](found, want)
    return {**claim, "found": found, "verdict": "matches" if ok else "mismatch"}


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="agency_truth.py build")
    ap.add_argument("--check", action="store_true", help="compare with the saved JSON instead of writing it")
    args = ap.parse_args(argv)
    keys = sorted(set(agency.PROFILES) | set(claimed_agencies()) | set(p.name for p in (ROOT / "research" / "agencies").iterdir() if p.is_dir()))
    table = {}
    for key in keys:
        loc = profile_locations(key)
        f = files_for(key)
        table[key] = {"profile": loc, "files": f, **verdict_for(key, loc, f)}
    obs = observations()
    checked = [check_claim(c, table) for c in claims()]
    payload = {"built_from": "the repository's files alone (agency.py, research/agencies/*, research/*); observations and claims are listed, not merged",
               "commit": git("rev-parse", "--short", "HEAD"), "agencies": table,
               "observations": obs, "claims": checked,
               "gate": "P0 and P1 source rows, pending-to-registry promotions and new briefs only for agencies whose verdict starts with in-code"}
    stable = {k: v for k, v in payload.items() if k != "commit"}
    if args.check:
        saved = load_json(OUT_JSON) or {}
        if {k: v for k, v in saved.items() if k != "commit"} != stable:
            print("agency_truth.json differs from a fresh build; run without --check to regenerate", file=sys.stderr)
            return 1
        print("agency truth table matches a fresh build")
        return 0
    OUT_JSON.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(payload), encoding="utf-8")
    for key, row in table.items():
        print(f"{key:9s} {row['verdict']:22s} brief-ready {'yes' if row['brief_ready'] else 'no ':3s} {row['reason']}")
    mism = [c for c in checked if c["verdict"] == "mismatch"]
    print(f"{len(checked)} claim(s) checked, {len(mism)} mismatch(es); {len(obs)} runtime observation(s) -> {OUT_JSON.relative_to(ROOT)}, {OUT_MD.relative_to(ROOT)}")
    return 0


def claimed_agencies() -> list[str]:
    return sorted({c["agency"] for c in claims()})


def render(payload: dict) -> str:
    t = payload["agencies"]
    lines = ["# 22 - The agency truth table", "",
             "Generated by `research/tools/agency_truth.py build` from the repository's files alone; do not edit by hand. Three kinds of",
             "statement are kept apart: the file-derived columns (regenerated every build), the runtime observations (each with the date,",
             "command and commit that observed it, never presented as a file fact) and the hand-kept claims of the documents (each quoted",
             "with its path and line, printed beside what the files show).", "",
             f"**The gate.** {payload['gate']}.", "",
             "## Verdict per agency", "",
             "| Agency | Verdict | Reason | Brief-ready | Why |", "|---|---|---|---|---|"]
    for k, r in t.items():
        lines.append(f"| {k} | {r['verdict']} | {r['reason']} | {'yes' if r['brief_ready'] else 'no'} | {r['brief_ready_reason']} |")
    lines += ["", "## File-derived columns", "",
              "| Agency | Profile | Layer folder | Registry rows | Sources with documents | Sources with none (empty collectors) | Memory nodes | Corpus events (frozen) | Coverage cells covered |",
              "|---|---|---|---|---|---|---|---|---|"]
    for k, r in t.items():
        f, p = r["files"], r["profile"]
        where = p["where"] or (", ".join(p["branches_with_profile_file"]) + " only" if p["branches_with_profile_file"] else "none")
        lines.append(f"| {k} | {where} | {f['layer_folder'] or 'none'} | {f['registry_rows']} | {len(f['sources_with_documents'])} | "
                     f"{len(f['sources_without_documents'])}: {', '.join(f['sources_without_documents']) or 'none'} | {f['memory_nodes']} | "
                     f"{f['corpus']['events']} ({f['corpus']['frozen_at'][:10] or 'no corpus'}) | {f['coverage_cells']['covered']} of {f['coverage_cells']['total']} |")
    lines += ["", "## Event families per agency (rows in the event files; newest document per family from the source status)", ""]
    fams = sorted({fam for r in t.values() for fam in r["files"]["event_files"]} | {fam for r in t.values() for fam in r["files"]["corpus"]["families"]})
    lines.append("| Agency | " + " | ".join(fams) + " |")
    lines.append("|---|" + "---|" * len(fams))
    for k, r in t.items():
        cells = []
        for fam in fams:
            ev = r["files"]["event_files"].get(fam)
            corp = r["files"]["corpus"]["families"].get(fam, 0)
            newest = r["files"]["newest_document_by_family"].get(fam, "")
            cells.append(f"{ev['rows'] if ev else 0} rows / {corp} corpus" + (f" / doc {newest}" if newest else ""))
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    lines += ["", "## Runtime observations (dated; each names the command and the commit that observed it)", "",
              "| Date | Agency | Kind | Result | Command | Commit | Note |", "|---|---|---|---|---|---|---|"]
    for o in payload["observations"]:
        lines.append(f"| {o['date']} | {o['agency']} | {o['kind']} | {o['result']} | `{o['command']}` | {o['commit']} | {o.get('note', '')} |")
    if not payload["observations"]:
        lines.append("| none recorded | | | | | | |")
    lines += ["", "## Claimed versus found", "", "| Agency | Claim (quoted) | Where | Field | Found | Verdict |", "|---|---|---|---|---|---|"]
    for c in payload["claims"]:
        lines.append(f"| {c['agency']} | \"{c['quote']}\" | {c['path']}:{c['line']} | {c['expects']['field']} {c['expects'].get('op', '==')} {json.dumps(c['expects']['value'])} | {json.dumps(c['found'])} | {c['verdict']} |")
    if not payload["claims"]:
        lines.append("| none | | | | | |")
    lines.append("")
    return "\n".join(lines)


def observe(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="agency_truth.py observe")
    ap.add_argument("--agency", required=True)
    ap.add_argument("--kind", required=True, choices=OBSERVATION_KINDS)
    ap.add_argument("--command", required=True, help="the command whose outcome is observed, as run")
    ap.add_argument("--result", required=True, help="what it printed or how it ended, in a few words")
    ap.add_argument("--note", default="")
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    row = {"date": args.date, "agency": args.agency, "kind": args.kind, "command": args.command, "result": args.result,
           "commit": git("rev-parse", "--short", "HEAD"), "note": args.note}
    with OBSERVATIONS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"recorded: {row}")
    return 0


def selfcheck() -> int:
    assert rows_of({"rows": [1, 2]}) == [1, 2] and rows_of([1]) == [1] and rows_of({"x": 1}) == []
    assert rows_of({"documents": [{"issued": "2021-04-21", "events": [{"a": 1}]}, {"events": [{"b": 2, "date": "2026-01-01"}]}]}) == \
        [{"a": 1, "date": "2021-04-21"}, {"b": 2, "date": "2026-01-01"}], "an event takes its document's day"
    assert dates_in([{"date": "2026-09-02T00:00:00Z"}, {"published": ""}, {"opened": "2026-01-05"}]) == ["2026-09-02", "2026-01-05"]
    f = {"corpus": {"exists": True, "events": 10, "frozen_at": "2026-09-26T10:00:00Z", "families": {"notice": 5, "incumbent": 3, "budget": 1, "leaders": 1, "programs": 0}},
         "sources_with_documents": ["sam_gov_site_api"], "layer_folder_exists": True}
    v = verdict_for("x", {"in_code": True, "where": "agency.py", "branches_with_profile_file": []}, f)
    assert v["verdict"] == "in-code-and-collected" and v["brief_ready"] is False, v  # four families with events, five needed
    f["corpus"]["families"]["programs"] = 2
    assert verdict_for("x", {"in_code": True, "where": "agency.py", "branches_with_profile_file": []}, f)["brief_ready"] is True
    v = verdict_for("x", {"in_code": False, "where": "", "branches_with_profile_file": ["origin/sync"]}, {**f, "corpus": {**f["corpus"], "exists": False, "events": 0, "families": {}}})
    assert v["verdict"] == "branch-only" and v["brief_ready"] is False
    v = verdict_for("x", {"in_code": True, "where": "agency_profiles", "branches_with_profile_file": []}, {**f, "corpus": {**f["corpus"], "exists": False, "events": 0, "families": {}}})
    assert v["verdict"] == "in-code-partial" and "no results/corpus.json" in v["reason"]
    assert verdict_for("x", {"in_code": False, "where": "", "branches_with_profile_file": []}, {**f, "corpus": {**f["corpus"], "families": {}}})["verdict"] == "not-in-code"
    table = {"x": {"files": {"corpus": {"events": 10}, "registry_rows": 3}}}
    c = check_claim({"agency": "x", "quote": "q", "path": "p", "line": 1, "expects": {"field": "corpus.events", "op": ">", "value": 0}}, table)
    assert c["verdict"] == "matches" and c["found"] == 10
    assert check_claim({"agency": "x", "quote": "q", "path": "p", "line": 1, "expects": {"field": "registry_rows", "value": 4}}, table)["verdict"] == "mismatch"
    assert check_claim({"agency": "y", "quote": "q", "path": "p", "line": 1, "expects": {"field": "registry_rows", "value": 4}}, table)["verdict"] == "agency not in the table"
    assert set(VERDICTS) == {"in-code-and-collected", "in-code-partial", "branch-only", "not-in-code"}
    print("agency_truth selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    if argv[0] == "build":
        return build(argv[1:])
    if argv[0] == "observe":
        return observe(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

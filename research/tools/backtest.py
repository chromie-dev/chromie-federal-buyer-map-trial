#!/usr/bin/env python3
"""Back-test harness: freeze the built corpus to disk, have an agent name each outcome's
program office and the aliases its requirement goes by (kept only when written verbatim in the notice), then
rewind 180, 90 and 30 days before every notice or award and count the independent families of evidence that
were available by then. Recall is the share of outcomes whose cell had at least MIN_FAMILIES families in time;
precision walks the forecast rows the pilot offices own and asks how many cells that reached the bar were
followed by a notice or award within a year; lead time is the distance from the bar to the outcome.

    python research/tools/backtest.py freeze --db navy_proof_m   # snapshot -> research/results/corpus.json
    python research/tools/backtest.py label [--check] [--limit N] # agent, one call per outcome -> results/outcome_labels.json
    python research/tools/backtest.py run [--check] [--ratchet]   # rules only -> build/backtest_results.json and a table
    python research/tools/backtest.py show OUTCOME_ID             # the evidence behind one outcome, one line per family
    python research/tools/backtest.py check                       # pipeline stage: labels replay unchanged, results recompute
    python research/tools/backtest.py --selfcheck

The corpus is a snapshot, never a live pull: `run` and `show` read only the three files. FPDS rows are
admitted 90 days after signature (the FPDS publication lag); every other row on its release date.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm import MODEL, structured  # noqa: E402
from reader import flatten, verbatim  # noqa: E402
from sam_notices import SWEEP_ORGS  # noqa: E402
from vocabulary import classify, term_groups  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
from agency import BUILD, P, RESEARCH, SAM_NOTICES  # noqa: E402

CORPUS = RESEARCH / "results" / "corpus.json"
LABELS = RESEARCH / "results" / "outcome_labels.json"
RESULTS = BUILD / "backtest_results.json"
SAM_RAW = SAM_NOTICES

HORIZONS = (180, 90, 30)
MIN_FAMILIES = 3
FPDS_LAG_DAYS = 90
FOLLOW_WINDOW_DAYS = 365
MAX_TERM_HITS = 25  # a capability term found in more documents than this, or under more offices than
MAX_TERM_OFFICES = 3  # this, names a field or a kind of work, not a requirement
# A name has no document cap: a program with a hundred delivery orders (MIDS, CANES) is still one office's
# requirement, and the office spread alone tells a name from a field (III, FFP, FY25 sit under many offices).
FY24_START = "2023-10-01"
NOTICE_TYPES = ("rfi_released", "presolicitation_posted", "rfp_released")
# The pilot portfolio (research/docs/00_existing_work_and_pilot.md); precision cells are the forecast rows these offices own.
PILOT_OFFICES = P["pilot_offices"]

# One document family per registry row. A document that yields five events still counts once.
FAMILY = {"navwar_lrae_annex25": "forecast", "navsea_lrae_annex25": "forecast", "onr_lrae_annex25": "forecast",
          "sam_gov_site_api": "notice", "fpds_atom_feed": "incumbent", "usaspending_api": "incumbent",
          "gao_reports": "oversight", "oversight_gov_reports": "oversight",
          "navy_mil_speeches": "leaders", "house_committee_repository": "congress", "conference_pages_exa": "conference",
          "don_budget_justification_books": "budget", "sbir_sttr_topics": "programs",
          "gao_bid_protests": "protest", "govinfo_api": "congress", "federal_register": "organization",
          "usajobs_historic_joa": "hiring", "vendor_jobs_routergrowth": "hiring",
          "navy_pae_press": "organization", "peo_digital_site": "organization", "dvids_navy_units": "organization",
          "don_cio_chips": "organization", "navy_peoc4i_site": "organization", "navy_navwar_site": "organization",
          "": "news", **P.get("families", {})}  # a profile adds the providers only its layer has
# Words that name the buyer or the paperwork, not the requirement; an alias made of one is dropped.
GENERIC = set(P.get("generic_words", ())) | {"navy", "u.s. navy", "us navy", "navwar", "spawar", "naval", "peo c4i", "department of the navy", "don", "dod",
           "contract", "contracts", "services", "service", "support", "engineering", "program", "office", "system", "systems",
           "request for information", "rfi", "rfp", "sources sought", "presolicitation", "solicitation", "idiq", "mac",
           "task order", "follow-on", "recompete", "re-compete", "production", "requirement", "capability"}
BANNED = ("rfp coming", "likely", "expected to release", "will release", "imminent", "probably", "expected soon")

SYSTEM = (f"You read one {P['short']} procurement notice or award record and name what it buys, so that other documents about "
          "the same requirement can be found by name. Answer with: program_office, the program office the text names (for "
          "example PMW 160 or PMA/PMW 101) copied exactly, or empty; capability, what is bought in at most eight plain words; "
          "short_names, the one-to-three-word names the requirement itself goes by in the text (a system or program name, an "
          "acronym, a contract nickname: NILE, NGSA, ISS 6, MIDS-LVT), each copied verbatim; aliases, the longer names, "
          "phrases and contract or vehicle numbers in the text that a forecast row, a speech, an audit or a hearing statement "
          "about the same requirement would also use, each copied verbatim from the text, never invented; capability_terms, "
          "two to six short phrases for the capability itself as a senior leader, a hearing witness, an auditor or a "
          "conference programme would say it without naming the program (tactical data link, Link 16, unmanned surface "
          "vessel, undersea surveillance, satellite communications), each specific to this requirement and never a word "
          f"that fits every {P['short'].removeprefix('U.S. ')} purchase; no list may hold the buyer's own name (an office, command or agency) or a generic "
          "word like services or contract; confidence between 0 and 1.")
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["program_office", "capability", "short_names", "aliases", "capability_terms", "confidence"],
          "properties": {"program_office": {"type": "string"}, "capability": {"type": "string"},
                         "short_names": {"type": "array", "items": {"type": "string"}},
                         "aliases": {"type": "array", "items": {"type": "string"}},
                         "capability_terms": {"type": "array", "items": {"type": "string"}},
                         "confidence": {"type": "number"}}}


def awards_not_collected() -> str:
    """What an award reader prints in place of its zero when no award source (FAMILY 'incumbent') yielded an event
    (sources/source_status.json): the record holds no contract to count. A lookup saved while mapping the agency is a
    document, not an award, so it does not count. Empty once one has, or with no status file."""
    path = RESEARCH / "sources" / "source_status.json"
    status = json.loads(path.read_text(encoding="utf-8"))["sources"] if path.exists() else {}
    keys = sorted(k for k, f in FAMILY.items() if f == "incumbent")
    if not status or any(status.get(k, {}).get("events") for k in keys):
        return ""
    return (f"not collected: no award source ({', '.join(keys)}) has yielded an award for the {P['label']} (fpds_sweep.py sweep --fetch), "
            "so the record holds no contract count, not a count of zero")


# ------------------------------------------------------------------ freeze

def sql(db: str, query: str) -> list[list[str]]:
    """Rows from the built database; unit and record separators so bodies with tabs and newlines survive."""
    env = {**os.environ}
    env.setdefault("PGPASSWORD", "postgres")
    dsn = (f"postgresql://{env.get('PGUSER', 'postgres')}:{env['PGPASSWORD']}"
           f"@{env.get('PGHOST', '127.0.0.1')}:{env.get('PGPORT', '54322')}/{db}")
    out = subprocess.run(["psql", dsn, "-At", "-F", "\x1f", "-R", "\x1e", "-c", query],
                         capture_output=True, text=True, check=True, env=env)
    return [rec.split("\x1f") for rec in out.stdout.removesuffix("\n").split("\x1e") if rec.strip()]  # psql ends its output with a newline


def as_day(value: str) -> str:
    return value[:10]


def shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def notice_description(notice_id: str) -> str:
    """The saved SAM.gov record's description, when the sweep kept it."""
    path = SAM_RAW / f"{notice_id}.json"
    if not path.exists():
        return ""
    saved = json.loads(path.read_text(encoding="utf-8"))
    records = saved if isinstance(saved, list) else [saved]
    parts = []
    for rec in records:
        for d in rec.get("description") or []:
            if isinstance(d, dict) and d.get("body"):
                parts.append(re.sub(r"<[^>]+>", " ", d["body"]))
    return flatten(" ".join(parts))[:6000]


def notice_office(notice_id: str) -> str:
    """The contracting office that posted the saved notice, read from its SAM.gov organization id; empty when the
    notice is not saved or its organization is not one the sweep keeps."""
    path = SAM_RAW / f"{notice_id}.json"
    if not path.exists():
        return ""
    saved = json.loads(path.read_text(encoding="utf-8"))
    for rec in saved if isinstance(saved, list) else [saved]:
        o = rec.get("data2") or rec.get("data") or rec
        if isinstance(o, dict) and o.get("organizationId"):
            return SWEEP_ORGS.get(str(o["organizationId"]), "")
    return ""


def fy_quarter(fy: str, quarter: str) -> tuple[int, int] | None:
    f, q = re.search(r"(\d{2,4})", fy or ""), re.search(r"(\d)", quarter or "")
    return (int(f.group(1)), int(q.group(1)) if q else 0) if f else None


def moved_later(changed: dict) -> bool:
    """A forecast revision that pushed the solicitation or award later, or withdrew its date: a slip."""
    for stage in ("solicitation", "award"):
        fy, quarter = changed.get(f"{stage}_fy"), changed.get(f"{stage}_quarter")
        if not fy and not quarter:
            continue
        old = fy_quarter((fy or ["", ""])[0], (quarter or ["", ""])[0])
        new = fy_quarter((fy or ["", ""])[1], (quarter or ["", ""])[1])
        if old and (new is None or new > old):
            return True
    return False


NOTICE_FIELDS = ("line", "responses_due")  # what the load adds to a notice's data: never words of the notice


def need_key_for(claim_key: str, data: dict) -> str | None:
    if data.get("line"):
        return data["line"]
    m = re.search(r":ev:([^:]+):row(\d+)$", claim_key)
    return f"row:{m.group(1)}:{m.group(2)}" if m else None


def freeze(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="backtest.py freeze")
    ap.add_argument("--db", required=True)
    args = ap.parse_args(argv)
    frozen_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    orgs = {r[0]: {"acronym": r[1], "name": r[2], "parent": r[3], "org_type": r[4]} for r in sql(args.db,
            "select id, coalesce(acronym,''), name, coalesce(parent_organization_id::text,''), org_type from gov_organizations")}
    # An office with competing live parents has an empty column and its parents as dated rows (PEO C4I under
    # NAVWAR and under the portfolio); the resolver reads the rows, so the tree here does too: the newest live one.
    for child, parent in sql(args.db,
            "select distinct on (source_organization_id) source_organization_id::text, target_organization_id::text "
            "from gov_organization_relationships where relationship_type = 'functionally_aligned_to' and valid_to is null "
            "order by source_organization_id, observed_at desc nulls last"):
        if child in orgs and not orgs[child]["parent"] and parent in orgs and parent != child:
            orgs[child]["parent"] = parent
    needs = {}
    for key, title, owner, owner_id in sql(args.db,
            "select n.source_key, n.title, coalesce(o.acronym, o.name, ''), coalesce(o.id::text,'') from gov_needs n "
            "left join gov_need_organizations r on r.need_id=n.id and r.role='originating_requirement_owner' "
            "left join gov_organizations o on o.id=r.organization_id"):
        needs.setdefault(key, {"key": key, "title": title, "owner": owner, "owner_id": owner_id})

    events, outcomes, ahead = [], [], 0
    for (iid, event_type, published, provider, org, title, body, data_text, claim_key, source_text) in sql(args.db,
            "select id, event_type, published_at::date, coalesce(source_provider,''), coalesce(primary_organization_id::text,''), "
            "title, coalesce(body,''), data::text, claim_key, source::text from agency_brain_items "
            "where published_at is not null and event_type is not null order by published_at, claim_key"):
        if as_day(published) > frozen_at[:10]:
            ahead += 1  # an award starting next month or an event yet to happen: the database keeps it, a frozen corpus cannot
            continue
        data = json.loads(data_text or "{}")
        source = json.loads(source_text or "{}")
        need = needs.get(need_key_for(claim_key, data) or "")
        family = FAMILY.get(provider, "other")
        if family == "organization" and claim_key.startswith("remarks:"):
            family = "leaders"  # a speech or statement the agency's own site carries is its leaders' words, not an office page
        # A notice speaks in its own words: the line the load tied it to and its response date travel as fields, so the
        # text every reading of the notice sees is the notice's, whatever the join decided.
        said = None if family == "notice" else need
        strings = [str(v) for k, v in data.items() if isinstance(v, str) and not (family == "notice" and k in NOTICE_FIELDS)]
        description = notice_description(source.get("notice_id", "")) if provider == "sam_gov_site_api" else ""
        text = " ".join(filter(None, [title, body, said["title"] if said else "", *strings, description]))
        # DoD posts its awards to FPDS 90 days late; an award another department signed for this agency is posted at once,
        # and any award on the saved pages was public by the day they were read, so no event is available after the freeze.
        lag = FPDS_LAG_DAYS if family == "incumbent" else 0
        slip = event_type == "forecast_changed" and moved_later(data.get("changed") or {})
        row = {"id": iid, "event_type": event_type, "date": as_day(published), "available_by": min(shift(as_day(published), lag), frozen_at[:10]),
               "provider": provider, "family": family, "org": org or (need or {}).get("owner_id", ""),
               "title": f"{said['title']} ({title})" if said else title, "text": flatten(text)[:8000], "slip": slip,
               **classify(event_type, flatten(f"{said['title']} ({title})" if said else title), slip),
               **({"vendor": data["vendor"]} if data.get("vendor") else {}),
               **({"stated": data["stated"]} if data.get("stated") else {}),
               **({"line": need["key"]} if need else {}), **({"due": data["responses_due"]} if data.get("responses_due") else {}),
               # a topic closes on a date of its own; it is not a notice's response date, so it is not `due`
               **({"closes": data["close"]} if event_type == "sbir_topic" and data.get("close") else {}),
               **({"url": source["url"]} if str(source.get("url") or "").startswith("http") else {})}
        events.append(row)
        # An outcome is a notice one of the swept contracting offices posted: the sweep holds every notice those offices
        # posted, so recall is read over a whole set. A notice another office posted came in through a saved search and
        # stays an event, never an outcome (NSWC Crane's N0016425SNB24 in the 2026-09-26 corpus).
        office = notice_office(source.get("notice_id", "")) if event_type in NOTICE_TYPES else ""
        if office and row["date"] >= FY24_START:
            outcomes.append({"id": iid, "kind": "notice", "event_type": event_type, "date": row["date"], "org": org,
                             "contracting_office": office,
                             "title": title, "source": source.get("url", ""),
                             "text": flatten(" ".join([title, body, notice_description(source.get("notice_id", ""))]))[:8000]})

    corpus = {"frozen_at": frozen_at, "db": args.db,
              "orgs": orgs, "needs": sorted(needs.values(), key=lambda n: n["key"]), "events": events,
              "outcomes": sorted(outcomes, key=lambda o: (o["date"], o["id"]))}
    if not events:
        # A database the load left empty must not overwrite the frozen corpus: the harness would then measure nothing.
        raise SystemExit(f"{args.db} holds no dated events; the frozen corpus is left as it was")
    CORPUS.write_text(json.dumps(corpus, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(events)} event(s), {len(outcomes)} outcome(s), {len(needs)} need(s), "
          f"{len(orgs)} organization(s) frozen from {args.db} into {CORPUS.relative_to(ROOT)}"
          + (f"; {ahead} item(s) dated after the freeze left out" if ahead else ""))
    return 0


# ------------------------------------------------------------------ label (agent + rules)

# A forecast line key: a buying activity code, the year, RFPREQ, the office as the release writes it (PMW-150,
# Code 031, IWS-1.0, PEO_CARRIERS, PD-1410) and a four-digit serial.
LINE_RE = re.compile(r"[A-Z0-9]{6}-\d{2}-RFPREQ-[A-Za-z0-9/._&]+(?:[ -][A-Za-z0-9/._&]+){0,3}?-\d{4}(?![0-9]|-[0-9])")


def buyer_names(orgs: dict) -> set[str]:
    return {v.lower() for o in orgs.values() for v in (o["acronym"], o["name"]) if v}


def clean_aliases(aliases: list[str], text: str, buyers: set[str] = frozenset()) -> tuple[list[str], Counter]:
    """Aliases as the text writes them: verbatim, not the buyer's name, not a generic word, no repeats."""
    kept, seen, dropped = [], set(), Counter()
    for alias in aliases:
        alias = alias.strip().strip("\"'.,;:()")
        key = alias.lower()
        if not alias:
            continue
        if key in GENERIC or key in buyers or len(alias) < 3:
            dropped["alias names the buyer or the paperwork, not the requirement"] += 1
        elif not verbatim(alias, text):
            dropped["alias is not written so in the text"] += 1
        elif key in seen:
            continue
        else:
            kept.append(alias)
            seen.add(key)
    return kept[:14], dropped


def clean_terms(terms: list[str], buyers: set[str]) -> list[str]:
    """Capability terms are the agent's judgment, not the notice's words, so the only rules are shape and specificity."""
    kept, seen = [], set()
    for term in terms:
        term = re.sub(r"^(?:the|a|an)\s+", "", re.sub(r"\s+", " ", term.strip().strip("\"'.,;:()")), flags=re.I)
        key = term.lower()
        if key in seen or key in GENERIC or key in buyers or len(term) < 4 or len(term.split()) > 5:
            continue
        seen.add(key)
        kept.append(term)
    return kept[:8]


def label_one(outcome: dict, model: str, replay_only: bool, buyers: set[str]) -> tuple[dict, dict]:
    user = (f"Kind: {outcome['kind']}\nDate: {outcome['date']}\nTitle: {outcome['title']}\nURL: {outcome['source']}\n\n"
            f"Text:\n{outcome['text']}")
    answer, how = structured(SYSTEM, user, SCHEMA, "backtest_label", model, replay_only=replay_only)
    aliases, dropped = clean_aliases((answer.get("short_names") or []) + (answer.get("aliases") or []), outcome["text"], buyers)
    office = verbatim(answer.get("program_office", ""), outcome["text"])
    return {"id": outcome["id"], "program_office": office, "capability": answer.get("capability", ""),
            "aliases": aliases, "capability_terms": clean_terms(answer.get("capability_terms") or [], buyers),
            "confidence": answer.get("confidence"),
            "cassette": how["cassette"], "model": how["model"]}, dropped


def label(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="backtest.py label")
    ap.add_argument("--check", action="store_true", help="replay cassettes only and fail if the file would change")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=8, help="model calls in flight at once; the rows keep the corpus order")
    args = ap.parse_args(argv)
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    outcomes = corpus["outcomes"][: args.limit or None]
    rows, dropped, buyers = [], Counter(), buyer_names(corpus["orgs"])
    unread = 0

    def one(outcome: dict) -> tuple[dict, dict]:
        try:
            return label_one(outcome, args.model, args.check, buyers)
        except LookupError as exc:
            if args.check:
                raise  # a check may not call the model: a missing cassette fails it, as everywhere else
            # No cassette and no key: the outcome stands unread with the reason and names no cell until it is read.
            return {"id": outcome["id"], **EMPTY_LABEL, "confidence": None, "cassette": None, "model": args.model, "unread": str(exc)}, {}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max(1, args.workers)) as pool:
        answers = list(pool.map(one, outcomes))  # in order: a cassette replays, a live call is one outcome at a time per worker
    for outcome, (row, drops) in zip(outcomes, answers):
        unread += bool(row.get("unread"))
        dropped.update(drops)
        rows.append(row)
        print(f"{outcome['date']}  {outcome['kind']:22} {(row['program_office'] or '-')[:12]:12} {', '.join(row['aliases'])[:70]} | "
              f"{', '.join(row['capability_terms'])[:60]}")
    payload = {"source": "chromie-federal-buyer-map-trial/research/tools/backtest.py", "model": args.model,
               "models_answering": sorted({r.get("model") for r in rows if r.get("model")}),  # what answered, beside what was asked for
               "outcomes": len(corpus["outcomes"]), "labels": rows, "dropped": dict(dropped)}
    text = json.dumps(payload, indent=1, ensure_ascii=False) + "\n"
    if args.check:
        if LABELS.read_text(encoding="utf-8") != text:
            print("labels differ from the saved file", file=sys.stderr)
            return 1
        print(f"{len(rows)} label(s) replayed, file unchanged")
        return 0
    LABELS.write_text(text, encoding="utf-8")
    print(f"{len(rows) - unread} outcome(s) labelled, {unread} unread, {sum(dropped.values())} alias(es) dropped -> {LABELS.relative_to(ROOT)}")
    for what, n in dropped.most_common():
        print(f"  dropped: {what} x{n}")
    return 0


# ------------------------------------------------------------------ run (rules only)

def chain(org_id: str, orgs: dict) -> list[str]:
    out, seen = [], set()
    while org_id and org_id in orgs and org_id not in seen:
        seen.add(org_id)
        if orgs[org_id].get("org_type") != "agency":
            out.append(org_id)
        org_id = orgs[org_id]["parent"]
    return out


WIDE_TYPES = {"agency", "contracting_office"}
NOT_AN_OFFICE = WIDE_TYPES | {"contracting_activity", "acquisition_portfolio"}  # holds offices; is not one


def wide(org_id: str, orgs: dict) -> bool:
    """The department or a contracting office (N00039) speaks for every office. An office whose parent is not
    known speaks for itself alone: a missing parent is a gap in the tree, not a statement about scope."""
    o = orgs.get(org_id)
    return bool(o) and (o.get("org_type") in WIDE_TYPES or bool(re.fullmatch(r"N\d{5}", o["acronym"])))


def admissible(event_org: str, cell_org: str, orgs: dict) -> tuple[bool, bool]:
    """(admissible, same_tree): an event with no office, a wide office, or one in the cell's tree may count."""
    if not event_org or not cell_org or wide(cell_org, orgs):
        return True, False
    same = event_org in chain(cell_org, orgs) or cell_org in chain(event_org, orgs)
    return same or wide(event_org, orgs), same


def alias_pattern(aliases: list[str]) -> re.Pattern | None:
    if not aliases:
        return None
    parts = sorted((re.escape(flatten(a)) for a in aliases), key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?:e?s)?(?![A-Za-z0-9])", re.I)


_LOWER: dict[str, str] = {}  # each event's text lowered once per process, by event id
_WORDS: dict[str, list[str]] = {}  # each word of those texts, to the ids of the events holding it


def lowered(e: dict) -> str:
    low = _LOWER.get(e["id"])
    if low is None:
        low = _LOWER[e["id"]] = e["text"].lower()
        for w in set(re.findall(r"[a-z0-9]+", low)):
            _WORDS.setdefault(w, []).append(e["id"])
    return low


def holders(needle: str) -> set[str] | None:
    """The ids of the events whose words can hold the needle where the pattern matches it. The needle's own marks and
    the pattern's edges bound every word in it, so each is a whole word of the text, save the last, which the pattern
    lets take an s or es; the rarest word decides. None when the needle holds no word, and every event stays a candidate."""
    words = re.findall(r"[a-z0-9]+", needle)
    if not words:
        return None
    options = [(w,) if n < len(words) - 1 or not needle[-1].isalnum() else (w, w + "s", w + "es") for n, w in enumerate(words)]
    forms = min(options, key=lambda f: sum(len(_WORDS.get(w, ())) for w in f))
    return {i for w in forms for i in _WORDS.get(w, ())}


def scan(aliases: list[str], events: list[dict]) -> list[dict]:
    """The events whose text the aliases' pattern matches. The pattern needs the flattened alias to be present, so the
    word index narrows the events, a substring test on the lowered text goes next and the regex runs only where it can
    match; same answer, minutes faster."""
    pat = alias_pattern(aliases)
    if pat is None:
        return []
    needles = {flatten(a).lower() for a in aliases if flatten(a)}
    for e in events:
        lowered(e)
    pool: set[str] | None = set()
    for n in needles:
        hit = holders(n)
        if hit is None:
            pool = None
            break
        pool |= hit
    return [e for e in events if (pool is None or e["id"] in pool) and any(n in _LOWER[e["id"]] for n in needles) and pat.search(e["text"])]


def matched(cell_org: str, aliases: list[str], events: list[dict], orgs: dict, exclude: str = "") -> list[dict]:
    out = []
    for e in scan(aliases, events):
        if e["id"] == exclude:
            continue
        ok, same = admissible(e["org"], cell_org, orgs)
        if ok:
            out.append({**e, "same_tree": same})
    return sorted(out, key=lambda e: (e["available_by"], e["id"]))


def families_by(events: list[dict], day: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in events:
        if e["available_by"] <= day:
            out.setdefault(e["family"], []).append(e)
    return out


def reached_on(events: list[dict], minimum: int) -> str | None:
    seen: set[str] = set()
    for e in events:
        seen.add(e["family"])
        if len(seen) >= minimum:
            return e["available_by"]
    return None


def cell_org_for(label_row: dict, outcome: dict, orgs: dict, events: list[dict] = ()) -> str:
    """The cell's office: the one the label names when it is a known office; else, when the notice hangs off a
    contracting office, the single owner of the forecast rows the aliases reach; else the linked office."""
    office = re.sub(r"\b(PM[ASW])-(?=\d)", r"\1 ", re.sub(r"\s+", " ", label_row.get("program_office", ""))).strip()
    if office:
        for oid, o in orgs.items():
            if o["acronym"] and o["acronym"].lower() == office.lower():
                return oid
    linked = outcome["org"]
    if (not linked or wide(linked, orgs)) and events:
        pat = alias_pattern(label_row.get("aliases") or [])
        owners = {e["org"] for e in events if e["family"] == "forecast" and e["org"] and pat and pat.search(e["text"])}
        if len(owners) == 1:
            return owners.pop()
    return linked


def with_lines(cell_org: str, aliases: list[str], events: list[dict], orgs: dict, exclude: str = "") -> tuple[list[str], list[dict]]:
    """Match by name, then also by the forecast line ids those rows carry: the line id is the requirement's own key,
    so an incumbent row that names the line joins the cell without sharing a word with the notice."""
    hits = matched(cell_org, aliases, events, orgs, exclude)
    lines = sorted({m for e in hits if e["family"] == "forecast" for m in LINE_RE.findall(e["text"])} - set(aliases))
    if lines:
        aliases = aliases + lines
        hits = matched(cell_org, aliases, events, orgs, exclude)
    return aliases, hits


def recurring_tokens(needs: list[dict]) -> set[str]:
    """Upper-case tokens that two or more distinct forecast titles share: the program names an office reuses."""
    titles = {re.sub(r"\s*\((?:C|N|O)\)\s*$", "", n["title"]).strip() for n in needs}
    counts = Counter(t for title in titles for t in set(re.findall(r"\b([A-Z][A-Z0-9-]{1,}[A-Z0-9])\b", title)))
    return {t for t, c in counts.items() if c >= 2}


def need_aliases(title: str, recurring: set[str] = frozenset(), owner: dict | None = None, buyers: list[str] = ()) -> list[str]:
    """A forecast row's names: its title, its parenthesised acronyms, and a short upper-case token the office
    reuses across rows. An upper-case dictionary word (ACCOUNTING, DIGITAL) in a shouted title is not a name, and
    neither is the owning office's acronym (TTO in "TTO Office Wide BAA"): it names every statement of the office. An
    office named for its program (MIDS) keeps that name. No part of an organization's acronym the title carries
    (`buyers`, the record's acronyms) is a name either: ASA(ALT) is the buyer, not ASA and ALT."""
    own = (owner or {}).get("acronym", "").lower()
    base = re.sub(r"\s*\((?:C|N|O)\)\s*$", "", title).strip()
    out = [base] if len(base) >= 4 else []
    words = base
    for a in sorted((a for a in buyers if len(a) >= 3 and a in base), key=len, reverse=True):
        words = re.sub(r"(?<![A-Za-z0-9])" + re.escape(a) + r"(?![A-Za-z0-9])", " ", words)
    paren = re.findall(r"\(([A-Za-z][A-Za-z0-9/-]{1,11})\)", words)
    out += paren
    out += [t for t in re.findall(r"\b([A-Z][A-Z0-9-]{1,}[A-Z0-9])\b", words)
            if t.lower() not in GENERIC and (t in paren or (len(t) <= 5 and t in recurring))]
    seen, kept = set(), []
    for a in out:
        if a.lower() not in seen and a.lower() not in GENERIC and a.lower() != own:
            seen.add(a.lower())
            kept.append(a)
    return kept


def too_common(term: str, events: list[dict], limit: int | None, orgs: dict, offices: int = MAX_TERM_OFFICES) -> bool:
    """A word found under more program offices than one requirement can have, or (for a capability term) in more
    documents than the cap, names a field or a kind of work. `limit` None checks offices alone, the rule for names.
    A statement placed at a command or a portfolio (a topic whose office the text does not name) says nothing about
    which office uses the word, so it does not count; nor does the department or a contracting office, which speak
    for every office. Counting commands dropped MIDS and CANES as too common on 2026-09-22 (their commands' topics
    stated them beside the offices that own them); counting trees instead kept PMW and LLC, so neither is the rule.
    A forecast row at a command is the command's own requirement, so it counts: an Army command owns its rows."""
    hits = scan([term], events)
    spread = {e["org"] for e in hits if e["org"] and not wide(e["org"], orgs)
              and (e["family"] == "forecast" or orgs.get(e["org"], {}).get("org_type") not in NOT_AN_OFFICE)}
    return (limit is not None and len(hits) > limit) or len(spread) > offices


def specific(names: list[str], events: list[dict], orgs: dict) -> list[str]:
    return [n for n in names if not too_common(n, events, None, orgs)]


EMPTY_LABEL = {"aliases": [], "capability_terms": [], "program_office": "", "capability": ""}


def outcome_cell(outcome: dict, row: dict, corpus: dict, max_term_hits: int = MAX_TERM_HITS, replay: bool = True) -> dict:
    """One outcome's cell: its office, the names kept, the capability terms kept and dropped, the events matched by
    name alone (`strict`) and by name or term (`hits`); the outcome's own row never counts toward itself. A replay
    reads only the events available by the outcome's date: a later release neither names the office nor makes a
    name common. The live view (`replay` False) reads every event."""
    orgs = corpus["orgs"]
    events = [e for e in corpus["events"] if not replay or e["available_by"] <= outcome["date"]]
    cell_org = cell_org_for(row, outcome, orgs, events)
    aliases, strict = with_lines(cell_org, specific(row["aliases"], events, orgs), events, orgs, exclude=outcome["id"])
    terms = [t for t in row.get("capability_terms") or [] if not too_common(t, events, max_term_hits, orgs)]
    common = [t for t in row.get("capability_terms") or [] if t not in terms]
    hits = matched(cell_org, aliases + terms, events, orgs, exclude=outcome["id"]) if terms else strict
    return {"org": cell_org, "aliases": aliases, "terms": terms, "common": common, "strict": strict, "hits": hits}


def pilot_needs(corpus: dict) -> list[dict]:
    """The forecast rows the pilot offices own, one per (owner, title): the precision cells."""
    out, seen = [], set()
    for need in corpus["needs"]:
        key = (need["owner"], re.sub(r"\W+", " ", need["title"]).strip().lower())
        if need["owner"] in PILOT_OFFICES and key not in seen:
            seen.add(key)
            out.append(need)
    return out


def own_line(cell_key: str, events_by_id: dict[str, dict] | None = None) -> str | None:
    """The line a cell's own notices carry: a forecast row's id, a notice-keyed row's notice key, or the notice line of
    the outcome a cell is named after; None when the cell has no notice of its own (an outcome that is no notice)."""
    if cell_key.startswith("need:"):
        return cell_key[len("need:"):]
    if cell_key.startswith("outcome:") and events_by_id:
        e = events_by_id.get(cell_key[len("outcome:"):])
        return (e.get("line") or None) if e and e.get("family") == "notice" else None
    return cell_key or None


def own_notice(own: str | None, line: str | None) -> bool:
    """Whether a notice event on `line` belongs to the cell whose own line is `own`. The cell's line is its own; a notice
    keyed cell also owns the notices filed under its key (the award notice of its solicitation carries the key as a
    prefix); a forecast row's cell keeps a notice no forecast row claims (its line is a notice key) and refuses one the
    load tied to another forecast row; a cell with no own line keeps every notice its names reach."""
    if own is None:
        return True
    if not line or line == own:
        return True
    if own.startswith("notice:"):
        return line.startswith(own)
    return line.startswith("notice:")


def scan_groups(groups: list[list[str]], events: list[dict]) -> list[dict]:
    """The events whose text every group reaches (one alias of each group): a multi-word term read as its words, each
    widened to the words of the capability it names. An empty list of groups reaches nothing."""
    ids: set[str] | None = None
    for g in groups:
        got = {e["id"] for e in scan(g, events)}
        ids = got if ids is None else ids & got
        if not ids:
            return []
    return [e for e in events if ids and e["id"] in ids]


def scan_term(term: str, events: list[dict]) -> tuple[list[dict], str]:
    """A search term against the events: as written first; a multi-word term the record never writes as written is then
    read as its words, each widened to its capability's words, every word required. Returns the hits and how they matched
    ("as written", "by its words", or "" for none)."""
    hits = scan([term], events) if term.strip() else []
    if hits:
        return hits, "as written"
    groups = term_groups(term)
    if len(groups) < 2:
        return [], ""
    hits = scan_groups(groups, events)
    return hits, ("by its words" if hits else "")


def need_cell(need: dict, corpus: dict, recurring: set[str]) -> tuple[list[str], list[dict]]:
    """A forecast row's cell: its names (title, acronyms, reused tokens, its line id) and the events they reach."""
    events, orgs = corpus["events"], corpus["orgs"]
    buyers = [o["acronym"] for o in orgs.values() if o.get("acronym")]
    aliases = specific(need_aliases(need["title"], recurring, orgs.get(need["owner_id"]), buyers), events, orgs) + ([need["key"]] if LINE_RE.fullmatch(need["key"]) else [])
    return with_lines(need["owner_id"], aliases, events, orgs)


FOLLOW_TYPES = NOTICE_TYPES + ("contract_awarded",)


def followed_by(first: str, aliases: list[str], events: list[dict], window: int = FOLLOW_WINDOW_DAYS) -> str | None:
    """The first notice or award event inside `window` days of `first` whose text the cell's names reach, else None.
    Events, not the labelled outcomes: a cell that reached the bar in 2019 is judged against the notices of 2019 and
    2020, which the corpus holds, and not against a labelled set that starts in FY24."""
    candidates = [e for e in events if e["family"] == "notice" and e["event_type"] in FOLLOW_TYPES
                  and first <= e["available_by"] <= shift(first, window)]
    hits = scan(aliases, candidates)
    return min(hits, key=lambda e: (e["available_by"], e["id"]))["id"] if hits else None


def evaluate(corpus: dict, labels: dict, minimum: int = MIN_FAMILIES, horizons: tuple[int, ...] = HORIZONS,
             max_term_hits: int = MAX_TERM_HITS) -> dict:
    orgs, events = corpus["orgs"], corpus["events"]
    by_id = {row["id"]: row for row in labels["labels"]}
    corpus_end = max(e["available_by"] for e in events) if events else "0000-00-00"
    outcomes = []
    for outcome in corpus["outcomes"]:
        row = by_id.get(outcome["id"], EMPTY_LABEL)
        cell = outcome_cell(outcome, row, corpus, max_term_hits)
        cell_org, aliases, terms, common, strict, hits = (cell[k] for k in ("org", "aliases", "terms", "common", "strict", "hits"))
        first = reached_on([e for e in hits if e["available_by"] <= outcome["date"]], minimum)
        per_h = {}
        for h in horizons:
            fams = families_by(hits, shift(outcome["date"], -h))
            per_h[str(h)] = {"families": sorted(fams), "families_by_name": sorted(families_by(strict, shift(outcome["date"], -h))),
                             "hit": len(fams) >= minimum,
                             "evidence": {f: [{"id": e["id"], "date": e["available_by"], "title": e["title"][:120],
                                               "provider": e["provider"]} for e in rows[:3]] for f, rows in sorted(fams.items())}}
        outcomes.append({"id": outcome["id"], "kind": outcome["kind"], "date": outcome["date"], "title": outcome["title"][:160],
                         "office": orgs.get(cell_org, {}).get("acronym") or orgs.get(cell_org, {}).get("name", ""),
                         "capability": row.get("capability", ""), "aliases": aliases, "capability_terms": terms,
                         "terms_too_common": common,
                         "matched_events": len(hits), "same_tree_share": round(sum(e["same_tree"] for e in hits) / len(hits), 2) if hits else None,
                         "reached_on": first, "lead_days": (date.fromisoformat(outcome["date"]) - date.fromisoformat(first)).days if first else None,
                         "horizons": per_h})
    recall = {str(h): round(sum(o["horizons"][str(h)]["hit"] for o in outcomes) / len(outcomes), 3) if outcomes else None for h in horizons}
    recall_by_name = {str(h): round(sum(len(o["horizons"][str(h)]["families_by_name"]) >= minimum for o in outcomes) / len(outcomes), 3)
                      if outcomes else None for h in horizons}
    leads = [o["lead_days"] for o in outcomes if o["lead_days"] is not None]

    # Precision: every forecast row a pilot office owns is a cell; did the ones that reached the bar see a notice or award?
    cells, recurring = [], recurring_tokens(corpus["needs"])
    for need in pilot_needs(corpus):
        aliases, hits = need_cell(need, corpus, recurring)
        first = reached_on(hits, minimum)
        followed = followed_by(first, aliases, events) if first else None
        cells.append({"owner": need["owner"], "title": need["title"], "aliases": aliases, "matched_events": len(hits),
                      "reached_on": first, "followed_by": followed,
                      "too_recent": bool(first) and not followed and shift(first, FOLLOW_WINDOW_DAYS) > corpus_end})
    reached = [c for c in cells if c["reached_on"]]
    judged = [c for c in reached if not c["too_recent"]]
    followed_n = sum(1 for c in judged if c["followed_by"])
    precision = {"cells": len(cells), "reached": len(reached), "too_recent": len(reached) - len(judged),
                 "judged": len(judged), "followed": followed_n,
                 "value": round(followed_n / len(judged), 3) if judged else None}
    return {"corpus_events": len(events), "corpus_end": corpus_end, "min_families": minimum,
            "horizons": list(horizons), "outcomes": outcomes, "recall": recall, "recall_by_name": recall_by_name,
            "lead_days_median": statistics.median(leads) if leads else None, "reached_ever": len(leads),
            "precision": precision, "cells": cells}


def table(results: dict) -> str:
    lines = [f"{'date':10}  {'kind':22} {'office':12} {'fam@180':>7} {'fam@90':>6} {'fam@30':>6} {'lead':>5}  title   (families, by name alone in brackets)"]
    for o in results["outcomes"]:
        h = o["horizons"]
        lines.append(f"{o['date']:10}  {o['kind']:22} {(o['office'] or '-')[:12]:12} "
                     f"{len(h['180']['families']):>4}({len(h['180']['families_by_name'])}) {len(h['90']['families']):>3}({len(h['90']['families_by_name'])}) "
                     f"{len(h['30']['families']):>3}({len(h['30']['families_by_name'])}) "
                     f"{o['lead_days'] if o['lead_days'] is not None else '-':>5}  {o['title'][:60]}")
    r, p = results["recall"], results["precision"]
    lines.append(f"recall@180 {r['180']}  recall@90 {r['90']}  recall@30 {r['30']}  over {len(results['outcomes'])} outcome(s) "
                 f"at >= {results['min_families']} families (names and capability terms; by name alone "
                 f"{results['recall_by_name']['180']} / {results['recall_by_name']['90']} / {results['recall_by_name']['30']}); "
                 f"{results['reached_ever']} reached the bar before the outcome, median lead {results['lead_days_median']} day(s)")
    lines.append(f"precision: {p['cells']} pilot forecast cells, {p['reached']} reached the bar, {p['too_recent']} too recent to judge, "
                 f"{p['followed']} of {p['judged']} judged were followed by a notice or award within a year -> {p['value']}")
    return "\n".join(lines)


def load() -> tuple[dict, dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8")), json.loads(LABELS.read_text(encoding="utf-8"))


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="backtest.py run")
    ap.add_argument("--min-families", type=int, default=MIN_FAMILIES)
    ap.add_argument("--check", action="store_true", help="recompute and fail if the saved results would change")
    ap.add_argument("--ratchet", action="store_true", help="raise the recall floors to this run's values")
    args = ap.parse_args(argv)
    corpus, labels = load()
    results = evaluate(corpus, labels, args.min_families)
    floors = {}
    if RESULTS.exists():
        floors = json.loads(RESULTS.read_text(encoding="utf-8")).get("floors", {})
    if not floors or args.ratchet:
        floors = {f"recall@{h}": results["recall"][str(h)] for h in results["horizons"]}
    results["floors"] = floors
    text = json.dumps(results, indent=1, ensure_ascii=False) + "\n"
    print(table(results))
    # A layer with no outcome yet has no recall and no floor; there is nothing to fall below.
    below = [k for k, v in floors.items() if v is not None and (results["recall"][k.split("@")[1]] or 0) < v]
    if below:
        print(f"below the floor: {', '.join(below)}", file=sys.stderr)
    if args.check:
        if not RESULTS.exists() or RESULTS.read_text(encoding="utf-8") != text:
            print("results differ from the saved file", file=sys.stderr)
            return 1
        return 1 if below else 0
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(text, encoding="utf-8")
    return 1 if below else 0


# ------------------------------------------------------------------ show (the register the pulse will use)

def describe(outcome: dict, horizon: int = HORIZONS[0]) -> str:
    """Signals for one cell in the counting register: a count of independent signals across
    families with one line per family, never a forecast of a release."""
    h = outcome["horizons"][str(horizon)]
    day = shift(outcome["date"], -horizon)
    n = sum(len(v) for v in h["evidence"].values())
    head = (f"{n} independent signal(s) across {len(h['families'])} famil{'y' if len(h['families']) == 1 else 'ies'} for "
            f"\"{outcome['capability'] or outcome['title']}\" ({outcome['office'] or 'office not named'}) by {day}, "
            f"{horizon} days before the {outcome['kind'].replace('_', ' ')} of {outcome['date']}:")
    lines = [head]
    for fam, rows in h["evidence"].items():
        for e in rows:
            lines.append(f"  - {fam}: {e['date']} \"{e['title']}\" ({e['provider'] or 'news'})")
    return "\n".join(lines)


def register_problems(text: str) -> list[str]:
    outside = re.sub(r'"[^"]*"', '""', text).lower()
    return [f"banned phrase {p!r}" for p in BANNED if re.search(rf"\b{re.escape(p)}\b", outside)]


def show(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="backtest.py show")
    ap.add_argument("outcome_id")
    ap.add_argument("--horizon", type=int, default=HORIZONS[0])
    args = ap.parse_args(argv)
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    outcome = next((o for o in results["outcomes"] if o["id"].startswith(args.outcome_id)), None)
    if outcome is None:
        print(f"no outcome {args.outcome_id}", file=sys.stderr)
        return 1
    text = describe(outcome, args.horizon)
    print(text)
    problems = register_problems(text)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


# ------------------------------------------------------------------ selfcheck

def selfcheck() -> int:
    orgs = {"pmw": {"acronym": "PMW 160", "name": "PMW 160", "parent": "peo"}, "peo": {"acronym": "", "name": "PEO", "parent": ""},
            "hq": {"acronym": "N00039", "name": "HQ", "parent": ""}, "other": {"acronym": "PMS 485", "name": "PMS 485", "parent": "navsea"},
            "navsea": {"acronym": "", "name": "NAVSEA", "parent": ""}}
    ev = lambda i, fam, day, org, text, lag=0: {"id": i, "event_type": "x", "date": day, "available_by": shift(day, lag),
                                                "provider": fam, "family": fam, "org": org, "title": text, "text": text}
    events = [ev("e1", "forecast", "2025-06-19", "pmw", "NILE In-Service Support (ISS) 6 Contract"),
              ev("e2", "incumbent", "2025-01-01", "hq", "Incumbent NILE ISS 5 order ends 2026", 90),
              ev("e3", "leaders", "2025-11-15", "", "CNO: NILE ISS keeps allied networks talking"),
              ev("e4", "conference", "2026-05-10", "peo", "NILE panel at Sea-Air-Space"),
              ev("e5", "oversight", "2025-03-01", "other", "GAO on NILE at a different office"),
              ev("e6", "congress", "2025-02-01", "pmw", "hearing on tactical data links"),
              # the outcome is itself an event of the corpus, under its own id, as freeze writes it
              dict(ev("o1", "notice", "2026-06-01", "hq", "Sources sought NILE In-Service Support (ISS) 6 engineering services"), event_type="rfi_released")]
    assert clean_terms(["tactical data links", "services", " Link 22 ", "the Navy", "PMW 160", "a very long phrase of six words"], buyer_names(orgs)) == ["tactical data links", "Link 22"]
    outcome = {"id": "o1", "kind": "notice", "event_type": "rfi_released", "date": "2026-06-01", "org": "hq",
               "title": "NILE ISS 6 Engineering Services", "source": "", "text": "Sources sought NILE In-Service Support (ISS) 6, the ISS 6 engineering services, for PMW 160"}
    corpus = {"frozen_at": "t", "orgs": orgs, "events": events, "outcomes": [outcome],
              "needs": [{"key": "k", "title": "NILE In-Service Support (ISS) 6 Contract (C)", "owner": "PMW 160", "owner_id": "pmw"}]}
    aliases, dropped = clean_aliases(["NILE", "ISS 6", "services", "Made Up Name", "nile", "PMW 160"], outcome["text"], buyer_names(orgs))
    assert aliases == ["NILE", "ISS 6"] and sum(dropped.values()) == 4, (aliases, dropped)  # services, an invented name, a lower-case copy, the buyer
    labels = {"labels": [{"id": "o1", "program_office": "PMW 160", "capability": "NILE in-service support", "aliases": aliases, "capability_terms": []}]}
    res = evaluate(corpus, labels)
    o = res["outcomes"][0]
    assert o["office"] == "PMW 160" and o["matched_events"] == 4, o  # e5 is another office's; e6 has no alias
    assert o["horizons"]["180"]["families"] == ["forecast", "incumbent", "leaders"], o["horizons"]["180"]
    labels["labels"][0]["capability_terms"] = ["tactical data links"]
    broad = evaluate(corpus, labels)["outcomes"][0]
    assert evaluate(corpus, labels, max_term_hits=0)["outcomes"][0]["terms_too_common"] == ["tactical data links"]
    assert broad["matched_events"] == 5 and broad["horizons"]["180"]["families"] == ["congress", "forecast", "incumbent", "leaders"], broad["horizons"]["180"]
    assert broad["horizons"]["180"]["families_by_name"] == ["forecast", "incumbent", "leaders"]
    labels["labels"][0]["capability_terms"] = []
    assert o["horizons"]["30"]["families"] == ["forecast", "incumbent", "leaders"], o["horizons"]["30"]  # e4 lands after D-30
    assert all(o["horizons"][h]["hit"] for h in ("180", "90", "30")) and o["reached_on"] == "2025-11-15" and o["lead_days"] == 198, o
    assert res["recall"] == {"180": 1.0, "90": 1.0, "30": 1.0}, res["recall"]
    assert res["precision"]["cells"] == 1 and res["precision"]["reached"] == 1 and res["precision"]["followed"] == 1, res["precision"]
    assert res["cells"][0]["followed_by"] == "o1" and followed_by("2027-01-01", ["NILE"], events) is None
    assert "ADNS" in need_aliases("ADNS Production MAC RFP #28 (C)", {"ADNS"}) and "RFP" not in need_aliases("ADNS Production MAC RFP #28 (C)", {"ADNS", "RFP"})
    assert need_aliases("COMMUNICATIONS SECURITY (COMSEC) ACCOUNTING SUPPORT (C)", {"ACCOUNTING"}) == ["COMMUNICATIONS SECURITY (COMSEC) ACCOUNTING SUPPORT", "COMSEC"]
    assert need_aliases("TTO Office Wide (OW) BAA", {"TTO", "OW"}, {"acronym": "TTO", "name": "Tactical Technology Office"}) == ["TTO Office Wide (OW) BAA", "OW"]
    assert "MIDS" in need_aliases("MIDS WDL SF3 Radio (C)", {"MIDS"}, {"acronym": "PMW 101", "name": "MIDS"})
    assert recurring_tokens([{"title": "NILE ISS 5 (C)"}, {"title": "NILE ISS 6 (C)"}, {"title": "ADNS MAC (C)"}]) == {"NILE", "ISS"}
    spread = [ev(f"w{n}", "forecast", "2025-01-01", f"o{n}", "SECURITY row") for n in range(5)]
    wide_orgs = {**orgs, **{f"o{n}": {"acronym": f"PMW {n}", "name": f"PMW {n}", "parent": "peo"} for n in range(5)}}
    assert specific(["SECURITY", "NILE"], spread + events, wide_orgs) == ["NILE"]
    # three offices and a command say MIDS: the command's statement names no office, so the spread is three, not four
    said = [ev(f"c{n}", "forecast", "2025-01-01", f"o{n}", "MIDS row") for n in range(3)] + [ev("c9", "programs", "2025-01-01", "cmd", "MIDS topic")]
    assert specific(["MIDS"], said, {**wide_orgs, "cmd": {"acronym": "", "name": "NAVWAR", "parent": "", "org_type": "contracting_activity"}}) == ["MIDS"]
    assert specific(["MIDS"], said, {**wide_orgs, "cmd": {"acronym": "", "name": "PMW 9", "parent": "peo", "org_type": "program_office"}}) == []
    # the command's own forecast row counts as a fourth office; the buyer ASA(ALT) the title carries lends it no names
    assert specific(["MIDS"], said[:3] + [dict(said[3], family="forecast")], {**wide_orgs, "cmd": {"acronym": "", "name": "NAVWAR", "parent": "", "org_type": "contracting_activity"}}) == [] \
        and need_aliases("ASA(ALT) AOS CfS", {"ASA", "ALT", "AOS"}, None, ["ASA(ALT)"]) == ["ASA(ALT) AOS CfS", "AOS"]
    text = describe(o)
    assert text.startswith("3 independent signal(s) across 3 families") and not register_problems(text), text
    assert register_problems("RFP coming, likely in Q3") and not register_problems('"RFP coming" is quoted')
    assert alias_pattern(["unmanned surface vessel"]).search("two unmanned surface vessels") and not alias_pattern(["ISS"]).search("mission")
    assert moved_later({"award_fy": ["FY25", "FY26"], "award_quarter": ["Q3", "Q2"]}) and moved_later({"solicitation_fy": ["FY25", "TBD"]})
    assert not moved_later({"solicitation_fy": ["", "FY25"], "solicitation_quarter": ["", "Q1"]}) and not moved_later({"award_fy": ["FY27", "FY25"]})
    assert admissible("hq", "pmw", orgs) == (True, False) and admissible("other", "pmw", orgs) == (False, False)
    assert admissible("other", "hq", orgs) == (True, False)  # a contracting-office cell admits every office
    line_events = [ev("f1", "forecast", "2025-06-19", "pmw", "Front Office Recompete N00039-26-RFPREQ-PMW-160-0009"),
                   ev("i1", "incumbent", "2024-12-01", "hq", "Incumbent contract N0003921F0001 ends 2026; incumbent on forecast line(s) N00039-26-RFPREQ-PMW-160-0009", 90)]
    al, hits = with_lines("pmw", ["Front Office Recompete"], line_events, orgs)
    assert [e["id"] for e in hits] == ["i1", "f1"] and al[-1] == "N00039-26-RFPREQ-PMW-160-0009", (al, [e["id"] for e in hits])
    assert cell_org_for({"program_office": "", "aliases": ["Front Office Recompete"]}, {"org": "hq"}, orgs, line_events) == "pmw"
    assert cell_org_for({"program_office": "PMS-404"}, {"org": "hq"}, {**orgs, "pms": {"acronym": "PMS 404"}}) == "pms"
    front, later = {"program_office": "", "aliases": ["Front Office Recompete"]}, line_events + [ev("f2", "forecast", "2026-07-13", "pms", "Front Office Recompete")]
    pms_orgs = {**orgs, "pms": {**orgs["pmw"], "acronym": "PMS 404", "name": "PMS 404"}}
    assert outcome_cell({"id": "n", "org": "hq", "date": "2025-07-01"}, front, {"orgs": pms_orgs, "events": later})["org"] == "pmw", \
        "a release after the outcome neither names its office nor counts toward it"
    assert outcome_cell({"id": "n", "org": "hq", "date": "2026-08-01"}, front, {"orgs": pms_orgs, "events": later})["org"] == "hq"
    assert not awards_not_collected(), "the Navy record's FPDS sweep saved award pages, so a zero there is a count"
    # A cell's own notices: the row's line, the notice-keyed row's key and what is filed under it; a name-shared notice is another's
    assert own_notice("notice:HR001125S0011", "notice:HR001125S0011") and own_notice("notice:HR001122S0004", "notice:HR001122S0004HR001122C0139")
    assert not own_notice("notice:DARPASN2450", "notice:DARPASN2682"), "the HEL shopping notice is not the STO RFI's next step"
    assert own_notice("N00039-23-RFPREQ-PMW-160-0108", "notice:N0003926R0001") and not own_notice("N00039-23-RFPREQ-PMW-160-0108", "N00039-23-RFPREQ-PMW-160-0109")
    assert own_notice(None, "notice:X") and own_notice("N-1", None)
    by_id = {"o1": {"id": "o1", "family": "notice", "line": "notice:A"}, "o2": {"id": "o2", "family": "forecast", "line": "L-1"}}
    assert own_line("need:notice:A") == "notice:A" and own_line("need:L-1") == "L-1" and own_line("outcome:o1", by_id) == "notice:A" and own_line("outcome:o2", by_id) is None
    # A multi-word term the record never writes as written is read as its words, each widened to its capability's words
    evs = [{"id": "s1", "text": "LASSO asks for autonomous satellite navigation", "available_by": "2026-01-01"},
           {"id": "s2", "text": "a satellite ground antenna", "available_by": "2026-01-01"},
           {"id": "s3", "text": "autonomous surface vessel", "available_by": "2026-01-01"}]
    hits, how = scan_term("spacecraft autonomy", evs)
    assert [e["id"] for e in hits] == ["s1"] and how == "by its words", (hits, how)
    assert scan_term("satellite", evs)[1] == "as written" and len(scan_term("satellite", evs)[0]) == 2
    assert scan_term("nothing here at all", evs) == ([], "") and scan_term("", evs) == ([], "")
    print("backtest selfcheck ok")
    return 0


def check(argv: list[str]) -> int:
    """The pipeline's stage: labels replay from cassettes unchanged, then the results recompute into build/."""
    return label(["--check"]) or run([])


COMMANDS = {"freeze": freeze, "label": label, "run": run, "show": show, "check": check}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--selfcheck":
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

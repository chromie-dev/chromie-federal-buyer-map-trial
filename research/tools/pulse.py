#!/usr/bin/env python3
"""Temporal engine, agency pulse and action graph, all computed from the frozen back-test corpus and its labels,
never from a live pull.

Every cell (an office and the names and capability terms one requirement goes by) gets a score as of a date that
decomposes into four named parts, each 0 to 1, weighted 40/20/20/20 and summed in exact arithmetic:
  families     distinct document families among the events available by the date, over five
  recency      how new the newest event is, one at 0 days and zero at 365
  persistence  how many of the last eight calendar quarters carried at least one event
  proximity    procurement nearness: a notice in the last year or an incumbent ending within two years (1.0),
               a forecast row touched in the last year (0.5), nothing (0); a forecast revision that moved the
               date later halves it and raises the slip flag. With no new events the score never rises
Families and quarters, never event counts, drive the score. The pulse for a week lists exactly the events
that became available in it, per office, in the counting register. Actions come from a closed list and
every one points at events.

    python research/tools/pulse.py rank [--as-of DATE] [--top N]     # scored cells, one event per family
    python research/tools/pulse.py week [--until DATE] [--days 7]    # what changed, per office
    python research/tools/pulse.py actions [--as-of DATE]            # what a vendor can do now, with evidence
    python research/tools/pulse.py card KEY [--as-of DATE]           # the Why-Now card for one cell: for, against, stage, next
    python research/tools/pulse.py build [--as-of DATE] [--check]    # all three -> research/results/pulse.json
    python research/tools/pulse.py --selfcheck
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (CORPUS, LABELS, LINE_RE, chain, matched, need_aliases, outcome_cell, own_line, own_notice,  # noqa: E402
                      pilot_needs, recurring_tokens, register_problems, shift, specific, with_lines)
from people import PEOPLE, contacts_for, load_routes, routes_for  # noqa: E402
from vocabulary import classify, next_milestones, stage_of  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
from agency import RESULTS  # noqa: E402

PULSE = RESULTS / "pulse.json"

WEIGHTS = {"families": 40, "recency": 20, "persistence": 20, "proximity": 20}
FAMILY_CAP = 5
RECENCY_DAYS = 365
QUARTERS = 8
NOVEL_DAYS = 90  # a family first seen inside this window is new for the cell
VENDORS_SHOWN = 5
NOTICE_TYPES = ("rfi_released", "presolicitation_posted", "rfp_released")
ACTIONS = ("meet_office", "attend_event", "research_program", "find_partner", "monitor_forecast", "respond_notice", "watch_expiration",
           "track_person")
PAST_FORECAST = ("market_research", "solicitation", "award")  # a cell at one of these has moved beyond its forecast row
ENDS_RE = re.compile(r"\bends (\d{4}-\d{2}-\d{2})")
CONTRACT_RE = re.compile(r"\bcontract (\S+)")


# ------------------------------------------------------------------ cells

def cells(corpus: dict, labels: dict) -> list[dict]:
    """The outcome cells the agent labelled and the forecast rows the pilot offices own, as one list."""
    orgs, events = corpus["orgs"], corpus["events"]
    out, recurring, buyers = [], recurring_tokens(corpus["needs"]), [o["acronym"] for o in orgs.values() if o.get("acronym")]
    by_id = {r["id"]: r for r in labels["labels"]}
    for outcome in corpus["outcomes"]:
        row = by_id.get(outcome["id"])
        if not row or row.get("unread"):  # an outcome the model has not read yet names no cell
            continue
        cell = outcome_cell(outcome, row, corpus, replay=False)
        out.append({"key": f"outcome:{outcome['id']}", "org": cell["org"], "office": office_name(cell["org"], orgs),
                    "name": row.get("capability") or outcome["title"], "aliases": specific(row["aliases"], events, orgs), "terms": cell["terms"]})
    for need in live_needs(corpus):
        aliases = specific(need_aliases(need["title"], recurring, orgs.get(need["owner_id"]), buyers), events, orgs) + ([need["key"]] if LINE_RE.fullmatch(need["key"]) else [])
        out.append({"key": f"need:{need['key']}", "org": need["owner_id"], "office": need["owner"], "name": need["title"],
                    "aliases": aliases, "terms": []})
    return out


def live_needs(corpus: dict, days: int = 365) -> list[dict]:
    """The pilot offices' forecast rows, then every other row a forecast release restated in the year before the
    corpus ends, one per (owner, title): the rows an office still forecasts, whichever command owns them."""
    since = shift(corpus_end(corpus), -days)
    current = {e["line"] for e in corpus["events"] if e["family"] == "forecast" and e.get("line") and e["available_by"] > since}
    out = pilot_needs(corpus)
    seen = {(n["owner"], re.sub(r"\W+", " ", n["title"]).strip().lower()) for n in out}
    for need in corpus["needs"]:
        key = (need["owner"], re.sub(r"\W+", " ", need["title"]).strip().lower())
        if need["key"] in current and need["owner_id"] and key not in seen:
            seen.add(key)
            out.append(need)
    return out


def office_name(org_id: str, orgs: dict) -> str:
    o = orgs.get(org_id) or {}
    return o.get("acronym") or o.get("name") or ""


def cell_events(cell: dict, corpus: dict) -> list[dict]:
    aliases, hits = with_lines(cell["org"], cell["aliases"], corpus["events"], corpus["orgs"])
    if cell["terms"]:
        hits = matched(cell["org"], aliases + cell["terms"], corpus["events"], corpus["orgs"])
    own = cell["key"].removeprefix("need:")
    if cell["key"].startswith("need:"):
        # A notice the load tied to another forecast line is that line's, however many names the two rows share; one it
        # tied to this line is this cell's, whether or not it repeats the row's words; a notice-keyed row owns only the
        # notices filed under its key, so a shopping notice that shares its words is never read as its next step.
        hits = [e for e in hits if not (e["family"] == "notice" and not own_notice(own, e.get("line")))]
        seen = {e["id"] for e in hits}
        hits = sorted(hits + [e for e in corpus["events"] if e.get("line") == own and e["family"] == "notice" and e["id"] not in seen],
                      key=lambda e: (e["available_by"], e["id"]))
    return hits


# ------------------------------------------------------------------ score

def days_between(earlier: str, later: str) -> int:
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days


def clip(x: Fraction) -> Fraction:
    return max(Fraction(0), min(Fraction(1), x))


def score(events: list[dict], as_of: str) -> dict:
    """The score as of a date from the events available by then, with its parts and one or two events per family."""
    seen = [e for e in events if e["available_by"] <= as_of]
    families = sorted({e["family"] for e in seen})
    parts = {"families": clip(Fraction(min(len(families), FAMILY_CAP), FAMILY_CAP))}
    newest = max((e["available_by"] for e in seen), default=None)
    parts["recency"] = clip(Fraction(RECENCY_DAYS - days_between(newest, as_of), RECENCY_DAYS)) if newest else Fraction(0)
    window = shift(as_of, -90 * QUARTERS)
    quarters = {(e["available_by"][:4], (int(e["available_by"][5:7]) - 1) // 3) for e in seen if e["available_by"] > window}
    parts["persistence"] = clip(Fraction(len(quarters), QUARTERS))
    year_ago = shift(as_of, -365)
    notice_recent = any(e["family"] == "notice" and e["event_type"] in NOTICE_TYPES and e["available_by"] > year_ago for e in seen)
    ends = [m.group(1) for e in seen if e["family"] == "incumbent" for m in [ENDS_RE.search(e["title"])] if m]
    ends_ahead = any(as_of < d <= shift(as_of, 730) for d in ends)
    forecast_recent = any(e["family"] == "forecast" and e["available_by"] > year_ago for e in seen)
    proximity = Fraction(1) if (notice_recent or ends_ahead) else Fraction(1, 2) if forecast_recent else Fraction(0)
    slip = any(e.get("slip") and e["available_by"] > year_ago for e in seen)
    against = [e for e in seen if polarity_of(e) == "negative" and e["available_by"] > year_ago]
    if against:
        # Feature 14: a delay, a cancellation, a bridge or a sole-source renewal in the last year changes the timing,
        # not the families; a forecast date moved later is one such statement.
        proximity /= 2
    parts["proximity"] = proximity
    # Parts are stored to four places and the score is their weighted sum, so the stored score recomputes from the
    # stored parts exactly: no float on either side.
    stored = {k: (Decimal(v.numerator) / Decimal(v.denominator)).quantize(Decimal("0.0001")) for k, v in parts.items()}
    total = sum(Decimal(WEIGHTS[k]) * v for k, v in stored.items())
    evidence = {}
    for e in sorted(seen, key=lambda e: e["available_by"], reverse=True):
        rows = evidence.setdefault(e["family"], [])
        if len(rows) < 2:
            rows.append({"id": e["id"], "date": e["available_by"], "title": e["title"][:120], "provider": e["provider"]})
    current = stage_of(seen, as_of)
    return {"as_of": as_of, "score": str(total.quantize(Decimal("0.0001"))), "parts": {k: str(v) for k, v in stored.items()},
            "weights": WEIGHTS, "families": families, "slip": slip, "events_seen": len(seen), "evidence": dict(sorted(evidence.items())),
            "novel_families": novelty(seen, as_of), "momentum": momentum(seen, as_of), "vendors": vendors(seen),
            "stage": current, "next": next_milestones(current),
            "for": sum(1 for e in seen if polarity_of(e) == "positive" and e["available_by"] > year_ago),
            "against": [{"id": e["id"], "date": e["available_by"], "title": e["title"][:120], "event_type": e["event_type"]} for e in against]}


def polarity_of(e: dict) -> str:
    return e.get("polarity") or classify(e["event_type"], e.get("text", ""), bool(e.get("slip")))["polarity"]


def stage_key(e: dict) -> str:
    return e.get("stage") or classify(e["event_type"])["stage"]


def novelty(seen: list[dict], as_of: str) -> list[str]:
    """Families whose first event for the cell landed inside the last NOVEL_DAYS: what is new, not what is loud."""
    first: dict[str, str] = {}
    for e in seen:
        first[e["family"]] = min(first.get(e["family"], e["available_by"]), e["available_by"])
    since = shift(as_of, -NOVEL_DAYS)
    return sorted(f for f, day in first.items() if day > since)


def momentum(seen: list[dict], as_of: str) -> dict:
    """Events in the last quarter against the quarter before it; a report, not a score part."""
    quarter, prior = shift(as_of, -90), shift(as_of, -180)
    return {"quarter": sum(e["available_by"] > quarter for e in seen), "prior": sum(prior < e["available_by"] <= quarter for e in seen)}


def vendors(seen: list[dict]) -> list[dict]:
    """Who holds the cell's contracts today, from the incumbent events alone."""
    counts = Counter(e["vendor"] for e in seen if e["family"] == "incumbent" and e.get("vendor"))
    return [{"vendor": v, "contracts": n} for v, n in counts.most_common(VENDORS_SHOWN)]


def recomposes(scored: dict) -> bool:
    """The stored parts, weighted, give the stored score back exactly."""
    return sum(Decimal(scored["weights"][k]) * Decimal(v) for k, v in scored["parts"].items()) == Decimal(scored["score"])


def rank(corpus: dict, labels: dict, as_of: str) -> list[dict]:
    out = []
    for cell in cells(corpus, labels):
        scored = score(cell_events(cell, corpus), as_of)
        if scored["events_seen"]:
            out.append({**cell, **scored})
    return sorted(out, key=lambda c: (-float(c["score"]), -len(c["families"]), c["key"]))


# ------------------------------------------------------------------ pulse: what changed this week

def week(corpus: dict, since: str, until: str) -> dict:
    """Exactly the events that became available in (since, until], grouped by office and event type."""
    orgs = corpus["orgs"]
    fresh = [e for e in corpus["events"] if since < e["available_by"] <= until]
    by_office: dict[str, dict] = {}
    for e in sorted(fresh, key=lambda e: (e["available_by"], e["id"])):
        office = office_name(e["org"], orgs) or "not attributed to an office"
        slot = by_office.setdefault(office, {"events": 0, "families": set(), "stages": set(), "negative": 0, "changes": {}})
        slot["events"] += 1
        slot["families"].add(e["family"])
        slot["stages"].add(stage_key(e))
        slot["negative"] += polarity_of(e) == "negative"
        slot["changes"].setdefault(e["event_type"], []).append({"id": e["id"], "date": e["available_by"], "title": e["title"][:120],
                                                                 "family": e["family"], "slip": bool(e.get("slip")),
                                                                 "polarity": polarity_of(e)})
    for slot in by_office.values():
        slot["families"] = sorted(slot["families"])
        slot["stages"] = sorted(slot["stages"])
    # Feature 3: the command changed in N areas, an area being a stage of the path that gained a statement.
    commands: dict[str, dict] = {}
    for e in fresh:
        top = chain(e["org"], orgs)
        name = office_name(top[-1], orgs) if top else "not attributed"
        row = commands.setdefault(name, {"events": 0, "areas": set(), "offices": set(), "negative": 0})
        row["events"] += 1
        row["areas"].add(stage_key(e))
        row["offices"].add(office_name(e["org"], orgs) or "-")
        row["negative"] += polarity_of(e) == "negative"
    for row in commands.values():
        row["areas"], row["offices"] = sorted(row["areas"]), sorted(row["offices"])
    return {"since": since, "until": until, "events": len(fresh), "commands": dict(sorted(commands.items())), "offices": dict(sorted(by_office.items()))}


def week_text(pulse: dict) -> str:
    lines = [f"What changed between {pulse['since']} and {pulse['until']}: {pulse['events']} event(s)."]
    for command, row in pulse.get("commands", {}).items():
        lines.append(f"{command} changed in {len(row['areas'])} area(s): {', '.join(row['areas'])}; {row['events']} event(s) under {len(row['offices'])} office(s)"
                     + (f"; {row['negative']} against" if row["negative"] else ""))
    for office, slot in pulse["offices"].items():
        lines.append(f"{office}: {slot['events']} event(s) across {len(slot['families'])} famil{'y' if len(slot['families']) == 1 else 'ies'}"
                     + (f", {slot['negative']} against" if slot.get("negative") else ""))
        for kind, rows in slot["changes"].items():
            lines.append(f"  {kind.replace('_', ' ')}: {len(rows)}" + ("" if len(rows) > 3 else " " + "; ".join(f"\"{r['title']}\"" for r in rows)))
    return "\n".join(lines)


# ------------------------------------------------------------------ why now (the card)

def card(cell: dict, events: list[dict], as_of: str, orgs: dict, window_days: int = 365) -> str:
    """The Why-Now card: how many statements in the window, from how many families and organizations, what argues
    for the buy and what against it, where the requirement stands and what would have to happen next."""
    seen = [e for e in events if e["available_by"] <= as_of]
    since = shift(as_of, -window_days)
    recent = [e for e in seen if e["available_by"] > since]
    families = sorted({e["family"] for e in recent})
    offices = sorted({office_name(e["org"], orgs) for e in recent if e["org"]} - {""})
    against = [e for e in recent if polarity_of(e) == "negative"]
    forward = [e for e in recent if polarity_of(e) == "positive"]
    first_recent = min((e["available_by"] for e in recent), default=as_of)
    lines = [f"{cell['office']}: {cell['name'][:90]}",
             f"{len(recent)} statement(s) in the last {days_between(first_recent, as_of)} day(s) from {len(families)} famil{'y' if len(families) == 1 else 'ies'} "
             f"({', '.join(families) or 'none'}) under {len(offices)} organization(s); {len(seen)} statement(s) in all since {min((e['available_by'] for e in seen), default=as_of)}.",
             *radar(cell),
             f"Stage: {cell['stage']}. Next on the path: {'; '.join(cell['next']) or 'nothing, the award is the last step'}.",
             f"For ({len(forward)}): " + ("; ".join(f"{e['available_by']} {e['event_type'].replace('_', ' ')}, {e['title'][:70]}" for e in forward[-3:]) or "nothing in the window"),
             f"Against ({len(against)}): " + ("; ".join(f"{e['available_by']} {e['event_type'].replace('_', ' ')}, {e['title'][:70]}" for e in against[-3:]) or "nothing in the window")]
    if forward and against:
        lines.append("Reading: the evidence is mixed; the families hold but a statement in the last year moves the timing. Keep watching.")
    elif against:
        lines.append("Reading: only statements against in the last year; the requirement is not moving toward a buy.")
    elif len(families) >= 3:
        lines.append("Reading: independent families agree and nothing argues against; the record supports a conversation now.")
    else:
        lines.append("Reading: too few families in the window to call this live; the older record carries it.")
    return "\n".join(lines)


def radar(cell: dict) -> list[str]:
    """The score's parts as the card shows them, and whether the cell is gathering pace: the recency part decays over a
    year from the newest statement, and a quarter with more statements than the one before reinforces it."""
    if "parts" not in cell:
        return []
    parts, m = cell["parts"], cell["momentum"]
    return [f"Radar: {cell['score']} of 100 from families {parts['families']}, recency {parts['recency']}, persistence {parts['persistence']}, "
            f"proximity {parts['proximity']} (weights {', '.join(f'{k} {v}' for k, v in cell['weights'].items())}); "
            f"{m['quarter']} statement(s) this quarter against {m['prior']} the quarter before"
            + (f"; new in the last {NOVEL_DAYS} days: {', '.join(cell['novel_families'])}" if cell.get("novel_families") else "") + "."]


def card_cmd(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pulse.py card")
    ap.add_argument("key", help="a cell key from the ranking, outcome:<id> or need:<line>")
    ap.add_argument("--as-of", default=None)
    args = ap.parse_args(argv)
    corpus, labels = load()
    as_of = args.as_of or corpus_end(corpus)
    cell = next((c for c in cells(corpus, labels) if c["key"] == args.key), None)
    if cell is None:
        print(f"no cell {args.key}", file=sys.stderr)
        return 2
    events = cell_events(cell, corpus)
    print(card({**cell, **score(events, as_of)}, events, as_of, corpus["orgs"]))
    return 0


# ------------------------------------------------------------------ actions

def actions(ranked: list[dict], corpus: dict, as_of: str, minimum_families: int = 3, roster: list[dict] | None = None,
            routes: list[dict] | None = None, reach=None, named=None) -> list[dict]:
    """What a vendor can do now, each row from the closed list and pointing at the events behind it. A meeting names
    whom the record ties to the office by as_of: the contacts the cell's own forecast row names (`named`), then the
    office's people, its parents' and those of the contracting offices that buy for it (`reach`), newest first; and the
    routes in: the requirement side, the contracting side and the published channels of the nearest office that has any.
    A forecast row is watched only while the cell stands at its forecast, and an open notice is answered by its date."""
    reach = reach or (lambda oid: chain(oid, corpus["orgs"]) or [oid])
    named = named or (lambda cell: [])
    by_id = {e["id"]: e for e in corpus["events"]}
    rows = []
    year_ago, quarter_ago = shift(as_of, -365), shift(as_of, -90)
    for cell in ranked:
        ev = [e for e in cell_events(cell, corpus) if e["available_by"] <= as_of]
        if len(cell["families"]) >= minimum_families:
            meet = action("meet_office", cell, [r["id"] for rows_ in cell["evidence"].values() for r in rows_[:1]],
                          f"{len(cell['families'])} families of evidence on this requirement")
            own = named(cell)
            meet["contacts"] = [*own, *(c for c in contacts_for(reach(cell["org"]), roster or [], as_of) if c["name"] not in {o["name"] for o in own})][:3]
            meet["routes"] = routes_for(reach(cell["org"]), routes or [], as_of)
            rows.append(meet)
        stage = cell.get("stage") or stage_of(ev, as_of)
        recent_forecast = [e["id"] for e in ev if e["family"] == "forecast" and e["available_by"] > year_ago]
        if recent_forecast and stage not in PAST_FORECAST:
            rows.append(action("monitor_forecast", cell, recent_forecast[:3], "forecast rows touched in the last year" + (", one moved later" if cell["slip"] else "")))
        # The notice answered is the cell's own: on the forecast row's line, under the notice-keyed row's key, or the
        # outcome notice itself; a notice that only shares the cell's words carries another requirement's date.
        line = own_line(cell["key"], by_id)
        notices = [e for e in ev if e["event_type"] in NOTICE_TYPES and own_notice(line, e.get("line"))]
        newest = max(notices, key=lambda e: (e["available_by"], e["id"])) if notices else None
        due = (newest or {}).get("due")
        if due and as_of <= due and stage != "award":
            rows.append(action("respond_notice", cell, [newest["id"]], f"the notice of {newest['available_by']} takes responses until {due}", by=due))
        newest: dict[str, str] = {}  # contract -> the id of the latest event stating its end; an extension replaces the expiry
        for e in sorted(ev, key=lambda e: e["available_by"]):
            if e["family"] == "incumbent" and ENDS_RE.search(e["title"]):
                contract = CONTRACT_RE.search(e["title"])
                newest[contract.group(1) if contract else e["id"]] = e["id"]
        for e in ev:
            m = ENDS_RE.search(e["title"]) if e["id"] in newest.values() else None
            if m and as_of < m.group(1) <= shift(as_of, 730):
                rows.append(action("watch_expiration", cell, [e["id"]], f"incumbent ends {m.group(1)}", by=m.group(1)))
        meetings = [e["id"] for e in ev if e["event_type"] in ("conference_appearance", "industry_engagement") and e["available_by"] > quarter_ago]
        if meetings:
            rows.append(action("attend_event", cell, meetings[:3], "the office or its leaders appeared at an event this quarter"))
        studied = [e["id"] for e in ev if e["event_type"] in ("audit_finding", "program_delayed", "program_created", "congressional_directive") and e["available_by"] > year_ago]
        if studied:
            rows.append(action("research_program", cell, studied[:3], "an audit, a delay, a new program or a directive names it this year"))
        awards = [e["id"] for e in ev if e["event_type"] == "contract_awarded" and e["available_by"] > year_ago]
        if awards:
            rows.append(action("find_partner", cell, awards[:3], "an award this year names the vendor holding the work"))
        people = [e["id"] for e in ev if e["event_type"] == "leadership_change" and e["available_by"] > year_ago]
        if people:
            rows.append(action("track_person", cell, people[:3], "a leadership change this year"))
    # One row per (action, office, evidence): ten MIDS cells share one expiring terminal contract, and the
    # vendor watches that contract once, under the highest-ranked cell that carries it. Every row carries its cell's
    # rank as the priority, and a date where its evidence states one (the day a contract ends).
    rank = {c["key"]: n for n, c in enumerate(ranked, start=1)}
    seen: set[tuple] = set()
    unique = []
    for row in rows:
        assert row["type"] in ACTIONS and row["evidence"], row
        row["priority"] = rank[row["cell"]]
        key = (row["type"], row["office"], tuple(row["evidence"])) if row["type"] != "meet_office" else (row["type"], row["cell"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def action(kind: str, cell: dict, evidence: list[str], why: str, by: str | None = None) -> dict:
    return {"type": kind, "cell": cell["key"], "office": cell["office"], "name": cell["name"][:120], "evidence": evidence, "why": why,
            "by": by}


def queue(acts: list[dict]) -> list[dict]:
    """The actions in the order a vendor works them: a dated one first by its date, then the rest by priority."""
    return sorted(acts, key=lambda a: (a["by"] is None, a["by"] or "", a["priority"]))


# ------------------------------------------------------------------ build

def load() -> tuple[dict, dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8")), json.loads(LABELS.read_text(encoding="utf-8"))


def load_people() -> list[dict]:
    return json.loads(PEOPLE.read_text(encoding="utf-8"))["rows"] if PEOPLE.exists() else []


def record_reach(corpus: dict, as_of: str, roster: list[dict]) -> dict:
    """The page layer's reach (an office, its parents and the contracting offices that buy for it) and the contacts a
    need cell's own forecast row names, as the keyword arguments `actions` takes. Imported here: the pages read this module."""
    from ask import named_on_rows, rows_as_released
    from pages import Layer
    layer = Layer(corpus, roster, as_of)
    details, by_key = rows_as_released(layer), {n["key"]: n for n in corpus["needs"]}
    need_of = lambda cell: by_key.get(cell["key"].removeprefix("need:")) if cell["key"].startswith("need:") else None
    return {"reach": layer.reach, "named": lambda cell: named_on_rows(layer, [need_of(cell)], details) if need_of(cell) else []}


def corpus_end(corpus: dict) -> str:
    """The last day the corpus speaks to: the latest day a statement became available, and never after the day the
    corpus was frozen (an award signed inside the reporting lag becomes available after the freeze, not before it)."""
    end = max(e["available_by"] for e in corpus["events"])
    frozen = (corpus.get("frozen_at") or "")[:10]
    return min(end, frozen) if frozen else end


def rank_text(ranked: list[dict], top: int) -> str:
    lines = [f"{'score':>8} {'fam':>3} {'rec':>6} {'per':>6} {'prox':>6}  office        cell   (* = a forecast date moved later)"]
    for c in ranked[:top]:
        p = c["parts"]
        lines.append(f"{c['score']:>8} {len(c['families']):>3} {p['recency']:>6} {p['persistence']:>6} {p['proximity']:>6}{'*' if c['slip'] else ' '} "
                     f"{c['office'][:12]:12}  {c['name'][:70]}")
    return "\n".join(lines)


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pulse.py build")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    corpus, labels = load()
    as_of = args.as_of or corpus_end(corpus)
    ranked = rank(corpus, labels, as_of)
    assert all(recomposes(c) for c in ranked), "a score did not recompose from its parts"
    pulse = week(corpus, shift(as_of, -args.days), as_of)
    roster = load_people()
    acts = actions(ranked, corpus, as_of, roster=roster, routes=load_routes(), **record_reach(corpus, as_of, roster))
    payload = {"as_of": as_of, "corpus_events": len(corpus["events"]), "cells": len(ranked), "ranking": ranked[:50],
               "week": pulse, "actions": acts}
    text = json.dumps(payload, indent=1, ensure_ascii=False) + "\n"
    print(rank_text(ranked, 15))
    print()
    print(week_text(pulse))
    print(f"\n{len(acts)} action(s): " + ", ".join(f"{k} {sum(a['type'] == k for a in acts)}" for k in ACTIONS if any(a['type'] == k for a in acts)))
    print(f"{sum(bool(c['novel_families']) for c in ranked)} of {len(ranked)} cell(s) gained a family in the last {NOVEL_DAYS} days; "
          f"{sum(c['momentum']['quarter'] > c['momentum']['prior'] for c in ranked)} have more events this quarter than last; "
          f"{sum(bool(c['vendors']) for c in ranked)} name an incumbent vendor")
    meets = [a for a in acts if a["type"] == "meet_office"]
    print(f"{sum(bool(a['contacts']) for a in meets)} of {len(meets)} meetings name whom the record ties to the office; "
          f"{sum(any(r['side'] == 'requirement' for r in a['routes']) for a in meets)} name the requirement side and "
          f"{sum(any(r['side'] == 'acquisition' for r in a['routes']) for a in meets)} the contracting side")
    for line in (rank_text(ranked, 15), week_text(pulse)):
        problems = register_problems(line)
        assert not problems, problems
    if args.check:
        if not PULSE.exists() or PULSE.read_text(encoding="utf-8") != text:
            print("pulse differs from the saved file", file=sys.stderr)
            return 1
        return 0
    PULSE.write_text(text, encoding="utf-8")
    return 0


def saved_pulse(as_of: str) -> dict:
    """The built pulse when it was built for this day: its ranking and actions without scoring every cell again."""
    saved = json.loads(PULSE.read_text(encoding="utf-8")) if PULSE.exists() else {}
    return saved if saved.get("as_of") == as_of else {}


def rank_cmd(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pulse.py rank")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args(argv)
    corpus, labels = load()
    as_of = args.as_of or corpus_end(corpus)
    ranked = (saved_pulse(as_of).get("ranking") if args.top <= 50 else None) or rank(corpus, labels, as_of)
    print(rank_text(ranked, args.top))
    for c in ranked[: min(3, args.top)]:
        print(f"\n{c['office']}: {c['name'][:90]} = {c['score']} as of {c['as_of']}" + (" (slip)" if c["slip"] else ""))
        for fam, rows in c["evidence"].items():
            print(f"  - {fam}: {rows[0]['date']} \"{rows[0]['title']}\"")
    return 0


def week_cmd(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pulse.py week")
    ap.add_argument("--until", default=None)
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args(argv)
    corpus, _ = load()
    until = args.until or corpus_end(corpus)
    print(week_text(week(corpus, shift(until, -args.days), until)))
    return 0


def actions_cmd(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pulse.py actions")
    ap.add_argument("--as-of", default=None)
    args = ap.parse_args(argv)
    corpus, labels = load()
    as_of = args.as_of or corpus_end(corpus)
    roster = load_people()
    acts = saved_pulse(as_of).get("actions") or actions(rank(corpus, labels, as_of), corpus, as_of, roster=roster, routes=load_routes(),
                                                        **record_reach(corpus, as_of, roster))
    for a in queue(acts):
        print(f"{a['by'] or '':10} #{a['priority']:<4} {a['type']:17} {a['office'][:12]:12} {a['name'][:60]:60} {a['why']} [{len(a['evidence'])} event(s)]")
        for r in a.get("routes", [])[:3]:
            print(f"{'':30} {r['side']}: {r['recommendation'][:100]}")
    return 0


# ------------------------------------------------------------------ selfcheck

def selfcheck() -> int:
    ev = lambda i, fam, day, text, kind="x", org="navair", **k: {"id": i, "event_type": kind, "date": day, "available_by": day,
                                                                "provider": fam, "family": fam, "org": org, "title": text, "text": text, **k}
    # A NAVAIR autonomous-aircraft year against a control office with as many events in one family
    timeline = [ev("b", "congress", "2026-01-15", "budget increase for autonomous aircraft", "funding_change"),
                ev("s", "leaders", "2026-02-10", "speech: autonomous aircraft first", "capability_priority"),
                ev("t", "organization", "2026-04-05", "SBIR topic autonomous aircraft", "industry_engagement"),
                ev("c", "conference", "2026-06-12", "autonomous aircraft panel", "conference_appearance"),
                ev("p", "notice", "2026-07-20", "prototype award autonomous aircraft", "contract_awarded"),
                ev("f", "forecast", "2026-08-14", "Autonomous Aircraft Sustainment (C)", "forecast_created"),
                ev("i", "incumbent", "2026-06-01", "Incumbent contract N0001921C0001 ends 2027-03-31; autonomous aircraft sustainment", "contract_expires", vendor="Acme")]
    control = [ev(f"k{n}", "forecast", f"2026-0{n}-10", f"row {n}", "forecast_created", org="control") for n in range(1, 8)]
    as_of = "2026-09-01"
    a, b = score(timeline, as_of), score(control, as_of)
    assert float(a["score"]) > float(b["score"]) and len(a["families"]) == 7 and len(b["families"]) == 1, (a["score"], b["score"])
    assert recomposes(a) and recomposes(b)
    assert all(a["evidence"][f] for f in a["families"])  # one event per contributing family
    # Five more events in a family already present change nothing
    padded = timeline + [ev(f"x{n}", "leaders", "2026-02-1%d" % n, f"speech {n}", "capability_priority") for n in range(1, 6)]
    assert score(padded, as_of)["score"] == a["score"], (score(padded, as_of)["score"], a["score"])
    # Frozen events, moving clock, never rising
    series = [float(score(timeline, shift(as_of, 30 * n))["score"]) for n in range(0, 30)]
    assert all(x >= y for x, y in zip(series, series[1:])), series
    # A forecast revision that moved the date later halves proximity and flags the slip
    slipped = timeline + [ev("g", "forecast", "2026-08-20", "Autonomous Aircraft Sustainment (revised)", "forecast_changed", slip=True)]
    s = score(slipped, as_of)
    assert s["slip"] and Decimal(s["parts"]["proximity"]) == Decimal(a["parts"]["proximity"]) / 2, (s["parts"], a["parts"])
    # A delay named this year halves proximity exactly as a slip does, and is listed against the cell
    delayed = timeline + [ev("d", "oversight", "2026-08-01", "program delayed a year", "program_delayed")]
    d = score(delayed, as_of)
    assert Decimal(d["parts"]["proximity"]) == Decimal(a["parts"]["proximity"]) / 2 and [x["id"] for x in d["against"]] == ["d"], d["against"]
    assert d["for"] == a["for"] and a["against"] == [] and a["stage"] == "forecast" and a["next"][0].startswith("a sources sought"), (a["stage"], a["next"])
    text = card({"key": "cell:a", "office": "NAVAIR", "name": "Autonomous aircraft", **d}, delayed, as_of, {"navair": {"acronym": "NAVAIR", "name": "NAVAIR", "parent": ""}})
    assert "Against (1)" in text and "mixed" in text and "Stage: forecast" in text and "this quarter against" in text, text
    # What is new, whether the cell is speeding up, who holds the contracts; none of it moves the score
    assert a["novel_families"] == ["conference", "forecast", "notice"] and a["momentum"] == {"quarter": 3, "prior": 2}, (a["novel_families"], a["momentum"])
    assert a["vendors"] == [{"vendor": "Acme", "contracts": 1}] and b["vendors"] == [] and b["novel_families"] == []
    assert set(a["parts"]) == set(WEIGHTS)
    # As-of sees only what was available; the stored decimal recomputes to itself
    early = score(timeline, "2026-03-01")
    assert early["events_seen"] == 2 and early["score"] == score(timeline, "2026-03-01")["score"]
    # A week's pulse lists the seeded changes and nothing else
    orgs = {"navair": {"acronym": "NAVAIR", "name": "NAVAIR", "parent": ""}, "control": {"acronym": "CTRL", "name": "CTRL", "parent": ""}}
    corpus = {"orgs": orgs, "events": timeline + control, "outcomes": [], "needs": []}
    w = week(corpus, "2026-06-05", "2026-06-12")
    assert w["events"] == 2 and set(w["offices"]) == {"NAVAIR", "CTRL"} and list(w["offices"]["NAVAIR"]["changes"]) == ["conference_appearance"], w
    assert not register_problems(week_text(w))
    # Actions from the list, each with evidence
    cell = {"key": "cell:a", "org": "navair", "office": "NAVAIR", "name": "Autonomous aircraft", "aliases": ["autonomous aircraft"], "terms": [],
            **a}
    corpus["events"] = timeline
    acts = actions([cell], corpus, as_of)
    kinds = {x["type"] for x in acts}
    assert {"meet_office", "monitor_forecast", "watch_expiration", "attend_event", "find_partner"} <= kinds <= set(ACTIONS), kinds
    assert all(x["evidence"] for x in acts)
    corpus["events"] = timeline + [ev("e", "incumbent", "2026-08-01", "Incumbent contract N0001921C0001 extended, ends 2028-03-31 (was 2027-03-31): "
                                      "autonomous aircraft sustainment", "contract_extended")]
    watch = [x for x in actions([cell], corpus, as_of) if x["type"] == "watch_expiration"]
    assert [x["evidence"] for x in watch] == [["e"]] and watch[0]["why"] == "incumbent ends 2028-03-31", "the extension's end replaces the expiry's"
    assert watch[0]["by"] == "2028-03-31" and all(x["priority"] == 1 for x in acts)
    # An open notice is answered by its date; once a solicitation moves the cell past its forecast, the row is not watched
    rfp = ev("r", "notice", "2026-08-25", "SAM.gov solicitation: autonomous aircraft sustainment", "rfp_released", due="2026-09-30")
    corpus["events"] = timeline + [rfp]
    later = actions([{**cell, **score(timeline + [rfp], as_of)}], corpus, as_of)
    assert [(x["type"], x["by"]) for x in later if x["type"] == "respond_notice"] == [("respond_notice", "2026-09-30")]
    assert "monitor_forecast" not in {x["type"] for x in later}, "a cell at solicitation does not watch its forecast row"
    assert not [x for x in actions([cell], corpus, "2026-10-01") if x["type"] == "respond_notice"], "a closed notice takes no response"
    # The notice answered is the cell's own: a notice-keyed row's shopping-notice namesake carries another requirement's date
    rfi = ev("q", "notice", "2026-06-01", "SAM.gov RFI: autonomous aircraft sensing", "rfi_released", due="2026-12-31", line="notice:RFI1")
    shop = ev("h", "notice", "2026-08-01", "SAM.gov special notice: autonomous aircraft shopping", "rfp_released", due="2026-11-30", line="notice:SHOP2")
    corpus["events"] = timeline + [rfi, shop]
    keyed = {**cell, "key": "need:notice:RFI1", **score(timeline + [rfi, shop], as_of)}
    answered = [x for x in actions([keyed], corpus, as_of) if x["type"] == "respond_notice"]
    assert [(x["evidence"], x["by"]) for x in answered] == [(["q"], "2026-12-31")], answered
    kept = {e["id"] for e in cell_events(keyed, corpus) if e["family"] == "notice"}
    assert "q" in kept and "h" not in kept and "p" in kept, ("the namesake is not the row's notice; a notice on no line stays", kept)
    outcome = {**cell, "key": "outcome:h", **score(timeline + [rfi, shop], as_of)}
    assert [x["evidence"] for x in actions([outcome], corpus, as_of) if x["type"] == "respond_notice"] == [["h"]], "an outcome notice answers itself"
    ordered = queue([{"by": None, "priority": 1}, {"by": "2027-01-01", "priority": 9}, {"by": "2026-10-01", "priority": 5}])
    assert [x["by"] for x in ordered] == ["2026-10-01", "2027-01-01", None], "dated actions first, soonest first"
    print("pulse selfcheck ok")
    return 0


COMMANDS = {"rank": rank_cmd, "week": week_cmd, "actions": actions_cmd, "card": card_cmd, "build": build, "check": lambda argv: build(["--check"] + argv)}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--selfcheck":
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

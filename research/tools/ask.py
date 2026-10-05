#!/usr/bin/env python3
"""Questions put to the twin, each answered from the frozen corpus, the people file, the pulse and the buying books,
with the statements behind the answer. A vendor's own meeting notes are kept apart in build/ and never join the record.

    python research/tools/ask.py changed NAVSEA autonomy --days 120         what changed inside an organization on a topic
    python research/tools/ask.py match "autonomy; undersea" --naics 336611   which offices buy what a company does
    python research/tools/ask.py match --profile company.json                the same for one customer's profile
    python research/tools/ask.py prep "PMW 740" --since 2026-06-01           a meeting brief: what changed, what is open, whom, what to ask
    python research/tools/ask.py analogs N0003926RE014                       how long past buys took from the same step to an award
    python research/tools/ask.py incumbents "PMW 160"                        contracts ending within two years, what weakens or holds each
    python research/tools/ask.py moves Leidos --days 90                      what a competitor did in the window
    python research/tools/ask.py team "undersea" --org NAVSEA                whom to team with: vendors whose contracts do the work
    python research/tools/ask.py note "PMW 740" "what was said" --person NAME --date 2026-09-20
    python research/tools/ask.py notes "PMW 740"                             each note beside the later records that share its names
    python research/tools/ask.py export N00039-25-RFPREQ-PMW/A-170-0001 DIR  the evidence room for one requirement
    python research/tools/ask.py --as-of 2026-06-30 changed NAVWAR           any question as of an earlier day
    python research/tools/ask.py --selfcheck

A profile is {"name": ..., "capabilities": [...], "naics": [...]}; capabilities are read through vocabulary.CAPABILITIES.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from statistics import median, quantiles

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from agency import NEED_ROW, P  # noqa: E402
from backtest import CORPUS, GENERIC, RESEARCH, admissible, alias_pattern, awards_not_collected, chain, need_cell, own_notice, scan, shift, wide  # noqa: E402
from fetch import MANIFEST  # noqa: E402
from pages import DNA, PIID_TEXT_RE, SOLICITATION_RE, Layer, book_line, compact, person, place, pointed, row_contacts, upward  # noqa: E402
from people import contacts_for, first_routes, norm_name, routes_for  # noqa: E402
from pulse import CONTRACT_RE, ENDS_RE, PULSE, days_between, load_people, office_name, polarity_of, queue  # noqa: E402
from vendors import load as load_vendors, names_for  # noqa: E402
from vocabulary import RENEWAL_RE, capability_terms, named_capabilities  # noqa: E402

from agency import BUILD  # noqa: E402

NOTES = BUILD / "notes.jsonl"  # the profile's build folder: a vendor's own notes never join the record
SHOWN = 8
TWO_YEARS = 730
MIN_ANALOGS = 5  # the narrowest part of the tree with this many past buys sets the analogs
SOLICITATION_IN_TITLE_RE = re.compile(P["solicitation_re"])
SHA_RE = re.compile(r"sha256 ([0-9a-f]{12,64})")
URL_RE = re.compile(r"https?://[^\s;,]+")
SAM_RECORD_RE = re.compile(r"sam\.gov/api/prod/opps/v2/opportunities/([0-9a-f]+)")
ROW_RE = re.compile(r"LRAE (\S+ [^!;]*?![Rr]ow \d+)")
EXTENDED_RE = re.compile(r"ends \S+ \(was \S+\)")
SIGNED_RE = re.compile(r"base award signed (\d{4}-\d{2}-\d{2})")
STEPS = {"rfi_released": "request for information", "presolicitation_posted": "presolicitation", "rfp_released": "solicitation"}
OPEN_NOTICES = tuple(STEPS)
NOTE_STOP = {"they", "we", "our", "their", "this", "that", "office", "meeting", "said", "will", "the"}


def plain(e: dict) -> str:
    """A statement's title without the source prefix ("SAM.gov presolicitation 2026-05-22: "). A forecast row, a topic, a
    budget line and a protest are titled by what they are, so a colon in theirs is the title's own ("BEB: Autonomous ...")."""
    return e["title"] if e["family"] in ("forecast", "programs", "budget", "protest") else e["title"].split(": ", 1)[-1]


def listed(e: dict, orgs: dict) -> str:
    return (f"  - {e['available_by']} {place(e, orgs)} {e['event_type'].replace('_', ' ')}: {plain(e)[:100]}"
            + (" [against]" if polarity_of(e) == "negative" else ""))


def newest(events: list[dict], orgs: dict, shown: int = SHOWN) -> list[str]:
    """The newest statements, contract rows folded into one line by their work: a week of NMCI orders is one change,
    not thirty, and the notices and findings stay in view."""
    rows = sorted(events, key=lambda e: (e["available_by"], e["id"]), reverse=True)
    orders = Counter(plain(e)[:60] if ": " in e["title"] else "work not stated" for e in rows if e["family"] == "incumbent")
    out = [listed(e, orgs) for e in rows if e["family"] != "incumbent"][:shown]
    if orders:
        out.append(f"  - {sum(orders.values())} contract row(s): " + "; ".join(f"{w} {n}" for w, n in orders.most_common(3))
                   + (f"; {len(orders) - 3} other work title(s)" if len(orders) > 3 else ""))
    return out


# ------------------------------------------------------------------ what changed (the twin question)

def changed(layer: Layer, name: str, topic: str = "", days: int = 120) -> str:
    """What changed inside an organization and everything under it, on a topic, in the last `days`, against the window
    before; with no topic, every change, reorganizations and leadership among them."""
    oid = layer.org_id(name)
    if not oid:
        return f"no organization named {name!r}"
    tree = layer.subtree(oid)
    terms = capability_terms(topic) if topic else []
    mine = [e for e in layer.events if e["org"] in tree and e["available_by"] <= layer.as_of]
    if terms:
        mine = scan(terms, mine)
    since, before = shift(layer.as_of, -days), shift(layer.as_of, -2 * days)
    now = sorted((e for e in mine if e["available_by"] > since), key=lambda e: (e["available_by"], e["id"]), reverse=True)
    prior = [e for e in mine if before < e["available_by"] <= since]
    families = Counter(e["family"] for e in now)
    offices = Counter(office_name(e["org"], layer.orgs) or "-" for e in now)
    lines = [f"What changed inside {office_name(oid, layer.orgs)}{f' regarding {topic}' if topic else ''} between {since} and {layer.as_of}: "
             f"{len(now)} statement(s) from {len(families)} famil{'y' if len(families) == 1 else 'ies'} under {len(offices)} organization(s); "
             f"the {days} days before had {len(prior)}."]
    if terms:
        lines.append("searched for: " + ", ".join(terms))
    if not now:
        return "\n".join(lines)
    new = sorted(set(families) - {e["family"] for e in mine if e["available_by"] <= since})
    lines.append("by family: " + ", ".join(f"{f} {n}" for f, n in families.most_common()) + (f"; first time here: {', '.join(new)}" if new else ""))
    lines.append("by organization: " + ", ".join(f"{o} {n}" for o, n in offices.most_common(SHOWN)))
    against = [e for e in now if polarity_of(e) == "negative"]
    if against:
        lines.append(f"against ({len(against)}): " + "; ".join(f"{e['available_by']} {plain(e)[:70]}" for e in against[:3]))
    lines.append("newest:")
    lines += newest(now, layer.orgs, SHOWN * 2)
    pat = alias_pattern(terms)
    seen: dict[str, tuple] = {}
    for p_ in layer.roster:
        for p in p_["positions"]:
            if p["org"] in tree and since < p["observed_at"] <= layer.as_of and (pat is None or pat.search(f"{p.get('context', '')} {p['raw_title']}")):
                seen[p_["name"]] = max(seen.get(p_["name"], ()), (p["observed_at"], p["raw_title"], office_name(p["org"], layer.orgs)))
    if seen:
        lines.append(f"people observed in the window ({len(seen)}): "
                     + "; ".join(f"{n} ({t[:50]}, {o}, {d})" for n, (d, t, o) in sorted(seen.items(), key=lambda x: x[1], reverse=True)[:SHOWN]))
    return "\n".join(lines)


# ------------------------------------------------------------------ capability to agency map, per customer

def naics_fit(book: dict | None, codes: list[str]) -> float | None:
    """The share of the office's awards under any of the NAICS codes (a prefix counts), from its buying book."""
    if not book or not codes:
        return None
    return round(sum(r["share"] for r in book.get("naics", []) if any(r["value"].startswith(c) for c in codes)), 3)


def named_on_rows(layer: Layer, needs: list[dict], details: dict[str, dict]) -> list[dict]:
    """The contacts the given forecast rows name, newest release first: they answer for those rows."""
    by_name, named = {norm_name(p["name"]): p for p in layer.roster}, {}
    for n in sorted(needs, key=lambda n: (details.get(n["key"], {}).get("release", ""), n["key"]), reverse=True):
        for c in row_contacts(layer, n["owner_id"], n["key"], details.get(n["key"], {}), by_name):
            named.setdefault(c["name"], c)
    return list(named.values())


def read_profile(path: Path) -> dict:
    """A profile file: JSON as the module docstring gives it, or the company's own words as plain text, read for the
    listed capabilities they name (a page's every line searched as written would match its menus and addresses)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        profile = json.loads(text)
    except ValueError:
        profile = None
    return profile if isinstance(profile, dict) else {"capabilities": named_capabilities(text)}


def match(layer: Layer, capabilities: list[str], naics: list[str], dna: dict, top: int = 10, name: str = "",
          details: dict[str, dict] | None = None) -> str:
    """Which offices buy what a company does: its capability words across every statement and forecast row, grouped by
    the office that made each, ranked by the families that spoke in the last two years, then the notices of the last
    year, the forecast rows, and the share of the office's awards under the company's NAICS codes."""
    terms = capability_terms("; ".join(capabilities))
    if not terms:
        return "no capability given"
    two_years, year = shift(layer.as_of, -TWO_YEARS), shift(layer.as_of, -365)
    pat = alias_pattern(terms)
    by: dict[str, list[dict]] = {}
    for e in scan(terms, layer.events):
        if e["org"] and e["available_by"] <= layer.as_of:
            by.setdefault(e["org"], []).append(e)
            if e.get("model_read") and e["model_read"][0] != e["org"]:  # read to a program office; it also stays where it was filed
                by.setdefault(e["model_read"][0], []).append(e)
    rows: dict[str, list[dict]] = {}
    for n in layer.needs:
        if n["owner_id"] and pat.search(n["title"]):
            rows.setdefault(n["owner_id"], []).append(n)
    ranked = []
    for oid in set(by) | set(rows):
        if layer.orgs.get(oid, {}).get("org_type") == "agency":
            continue  # the department speaks for every office and places nothing
        ev = sorted(by.get(oid, []), key=lambda e: e["available_by"], reverse=True)
        acronym = office_name(oid, layer.orgs)
        book = dna.get("offices", {}).get(acronym) or dna.get("contracting_offices", {}).get(acronym)
        ranked.append({"org": oid, "office": acronym, "statements": len(ev), "rows": rows.get(oid, []), "fit": naics_fit(book, naics),
                       "read": sum(1 for e in ev if e["org"] != oid),
                       "families": sorted({e["family"] for e in ev if e["available_by"] > two_years}),
                       "notices": [e for e in ev if e["family"] == "notice" and e["available_by"] > year], "newest": ev[:2]})
    ranked.sort(key=lambda r: (-len(r["families"]), -len(r["notices"]), -len(r["rows"]), -(r["fit"] or 0), -r["statements"], r["office"]))
    lines = [f"{f'For {name}: ' if name else ''}{len(ranked)} organization(s) speak about {'; '.join(capabilities)} in "
             f"{sum(r['statements'] for r in ranked)} statement(s) and {sum(len(r['rows']) for r in ranked)} {NEED_ROW}(s); searched for: {', '.join(terms)}"]
    for n, r in enumerate(ranked[:top], start=1):
        up = chain(r["org"], layer.orgs)
        above = " > ".join(office_name(x, layer.orgs) for x in up[1:])
        kind = ", a contracting office" if layer.orgs.get(r["org"], {}).get("org_type") == "contracting_office" else ""
        read = f" ({r['read']} read here by the model)" if r["read"] else ""
        lines.append(f"{n}. {r['office']}{f' (under {above}{kind})' if above else f' ({kind[2:]})' if kind else ''}: {r['statements']} statement(s){read}; last two years "
                     f"{', '.join(r['families']) or 'nothing'}; {len(r['notices'])} notice(s) in the last year; {len(r['rows'])} {NEED_ROW}(s)"
                     + (f"; {round(r['fit'] * 100)}% of its awards under the NAICS given" if r["fit"] is not None else ""))
        lines += [f"     {x['key']}: {x['title'][:90]}" for x in r["rows"][:3]]
        lines += [f"     {e['available_by']} {e['event_type'].replace('_', ' ')}: {plain(e)[:100]}" for e in (r["notices"][:2] or r["newest"])]
        way = [f"{x['side']}: {x['recommendation'][:80]}" for x in first_routes(routes_for(up or [r["org"]], layer.routes, layer.routes_by), 2)]
        people = named_on_rows(layer, r["rows"], details or {})[:2] or contacts_for(layer.reach(r["org"]), layer.roster, layer.as_of, limit=2)
        way += [f"{p['name']} ({p['title'][:60]}, {p.get('named_on') or p['observed_at']})" for p in people]
        if way:
            lines.append("     in: " + "; ".join(way))
    return "\n".join(lines)


# ------------------------------------------------------------------ incumbents: what weakens or holds each

def contracts(events: list[dict]) -> dict[str, dict]:
    """Each incumbent contract in the events: its latest stated end (an extension replaces the expiry), its vendor and
    work, its extensions and the modifications that renew it (an option exercised, a bridge, a sole-source extension)."""
    out: dict[str, dict] = {}
    for e in sorted((e for e in events if e["family"] == "incumbent"), key=lambda e: (e["available_by"], e["id"])):
        m = CONTRACT_RE.search(e["title"])
        if not m:
            continue
        c = out.setdefault(m.group(1), {"contract": m.group(1), "end": "", "vendor": "", "work": "", "org": e["org"],
                                        "signed": "", "extended": [], "renewed": []})
        if ": " in e["title"] and not c["work"]:
            c["work"] = plain(e)
        if (signed := SIGNED_RE.search(e["text"])) and not c["signed"]:
            c["signed"] = signed.group(1)  # the day FPDS states the base award was signed, not the day of a later action
        if end := ENDS_RE.search(e["title"]):
            c["end"] = end.group(1)
        c["vendor"] = e.get("vendor") or c["vendor"]
        if e["event_type"] == "contract_extended":
            c["extended"].append(e)
        elif e["event_type"] == "contract_modified" and RENEWAL_RE.search(e["title"]):
            c["renewed"].append(e)
    return out


def cited(events: list[dict]) -> dict[str, list[dict]]:
    """Contract number -> the forecast rows, notices and other statements that name it: where a follow-on shows."""
    out: dict[str, list[dict]] = {}
    for e in events:
        if e["family"] != "incumbent":
            for piid in {compact(p) for p in PIID_TEXT_RE.findall(e["text"])}:
                out.setdefault(piid, []).append(e)
    return out


def weak_points(c: dict, naming: list[dict], share: float, lives: dict[str, dict], as_of: str) -> tuple[list[str], str]:
    """What weakens an incumbent's hold and what keeps it, each from a dated statement, and one reading. A follow-on
    notice whose solicitation has an award notice is settled; one older than two years with none is a question."""
    forecast = sorted((e for e in naming if e["family"] == "forecast"), key=lambda e: e["available_by"])
    # A notice that names the contract to keep it (sole source, bridge, extension) holds the incumbent; any other opens the follow-on.
    kept = sorted((e for e in naming if e["event_type"] == "justification_posted" or (e["event_type"] in OPEN_NOTICES and RENEWAL_RE.search(e["title"]))),
                  key=lambda e: e["available_by"])
    notices = sorted((e for e in naming if e["event_type"] in OPEN_NOTICES and e not in kept), key=lambda e: e["available_by"])
    justified = kept
    slipped = any(e.get("slip") for e in forecast)
    lines = []
    if c["extended"]:
        moved = EXTENDED_RE.search(c["extended"][-1]["title"])
        lines.append(f"extended {len(c['extended'])} time(s), latest signed {c['extended'][-1]['date']}" + (f": {moved.group(0)}" if moved else ""))
    if c["renewed"]:
        lines.append(f"{len(c['renewed'])} option(s) or renewing modification(s), latest {c['renewed'][-1]['date']}")
    if forecast:
        lines.append(f"a forecast row names it: {plain(forecast[-1])[:80]} ({forecast[-1]['available_by']}{', award moved later' if slipped else ''})")
    if notices:
        lines.append(f"a notice names it: {STEPS[notices[-1]['event_type']]} {notices[-1]['available_by']}, {plain(notices[-1])[:80]}")
    if justified:
        lines.append(f"a sole-source, bridge or justification notice names it: {justified[-1]['available_by']}, {plain(justified[-1])[:80]}")
    lines.append(f"the vendor holds {round(share * 100)}% of the office's contracts in the record")
    sol = SOLICITATION_RE.search(notices[-1]["text"]) if notices else None
    awarded = lives.get(compact(sol.group(1)), {}).get("steps", {}).get("contract_awarded") if sol else None
    if awarded:
        reading = f"the follow-on under {compact(sol.group(1))} has an award notice of {awarded}; the record says who holds the next contract"
    elif notices and notices[-1]["available_by"] > shift(as_of, -TWO_YEARS):
        reading = "the follow-on is in the market; compete on it now"
    elif notices:
        reading = f"a follow-on notice of {notices[-1]['available_by']} names it and no award notice followed; ask the office where it stands"
    elif forecast and (slipped or c["extended"] or justified):
        reading = "a follow-on is planned and has slipped or been bridged; the window is open and a bridge may come first"
    elif forecast:
        reading = "a follow-on is planned; plan against the forecast window"
    elif c["extended"] or justified:
        reading = "the buyer has extended the incumbent and no follow-on is in the record; ask the office how it will be bought"
    elif share >= 0.5:
        reading = "the vendor holds most of the office's work and nothing in the record weakens it"
    else:
        reading = "nothing in the record weakens the incumbent before it ends"
    return lines, reading


def incumbents(layer: Layer, name: str, within: int = TWO_YEARS) -> str:
    oid = layer.org_id(name)
    if not oid:
        return f"no organization named {name!r}"
    tree = layer.subtree(oid)
    book = contracts([e for e in layer.events if e["org"] in tree and e["available_by"] <= layer.as_of])
    if not book and (note := awards_not_collected()):
        return f"{office_name(oid, layer.orgs)}: {note}"
    live = sorted((c for c in book.values() if c["end"] and layer.as_of < c["end"] <= shift(layer.as_of, within)), key=lambda c: (c["end"], c["contract"]))
    held = Counter(c["vendor"] for c in book.values() if c["vendor"])
    public = [e for e in layer.events if e["available_by"] <= layer.as_of]
    naming, lives = cited(public), lifecycles(public, layer.orgs)
    lines = [f"{office_name(oid, layer.orgs)}: {len(live)} incumbent contract(s) end within {within} day(s) of {layer.as_of}, of {len(book)} in the record"]
    for c in live[:15]:
        found, reading = weak_points(c, naming.get(compact(c["contract"]), []), held[c["vendor"]] / max(1, sum(held.values())), lives, layer.as_of)
        lines.append(f"  - {c['contract']} {c['vendor'] or 'vendor not stated'}: {c['work'][:70] or 'work not stated'}"
                     + (f"; signed {c['signed']}" if c["signed"] else "") + f"; ends {c['end']}")
        lines += [f"      {x}" for x in found]
        lines.append(f"      reading: {reading}")
    return "\n".join(lines)


# ------------------------------------------------------------------ analogs: how long past buys took

def lifecycles(events: list[dict], orgs: dict) -> dict[str, dict]:
    """Every solicitation number the notices carry, with the first date of each step and of its first award notice:
    the dated paths past buys walked. The owner is the first program office a notice under the number sits at."""
    out: dict[str, dict] = {}
    for e in sorted(events, key=lambda e: (e["date"], e["id"])):
        m = SOLICITATION_RE.search(e["text"]) if e["family"] == "notice" else None
        if not m:
            continue
        key = compact(m.group(1))
        row = out.setdefault(key, {"solicitation": key, "org": e["org"], "title": plain(e), "steps": {}})
        if e["org"] and (not row["org"] or wide(row["org"], orgs)) and not wide(e["org"], orgs):
            row["org"] = e["org"]
        if e["event_type"] in STEPS or e["event_type"] == "contract_awarded":
            row["steps"].setdefault(e["event_type"], e["date"])
        if e["event_type"] == "rfp_released":
            row["title"] = plain(e)
    return out


def words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", title.lower()) if w not in GENERIC}


def slip_rate(layer: Layer, org: str, title: str) -> str:
    """A row with no notice yet: how often the forecast has moved award windows later in the nearest part of the tree."""
    for a in chain(org, layer.orgs) + [None]:
        tree = layer.subtree(a) if a else set(layer.orgs) | {""}
        revs = [e for e in layer.events if e["event_type"] == "forecast_changed" and e["org"] in tree and e["available_by"] <= layer.as_of]
        if len(revs) >= MIN_ANALOGS or a is None:
            where = f"under {office_name(a, layer.orgs)}" if a else "in the whole record"
            return (f"{title[:100]}: no notice under a solicitation number yet; the row stands at the forecast step. "
                    f"Of {len(revs)} forecast revision(s) {where}, {sum(1 for e in revs if e.get('slip'))} moved the award later.")
    return ""


def analogs(layer: Layer, key: str) -> str:
    """How long past buys took from the step this one has reached to an award notice, in the narrowest part of the tree
    that holds enough of them, and the three nearest by name."""
    lives = lifecycles([e for e in layer.events if e["available_by"] <= layer.as_of], layer.orgs)
    need = next((n for n in layer.needs if n["key"] == key), None)
    reading = ""
    if need:
        _, hits = need_cell(need, layer.corpus, layer.recurring)
        notices = [e for e in hits if e["family"] == "notice" and e["available_by"] <= layer.as_of]
        # The row's own notices carry its line (a notice-keyed row's key, or the award notice filed under it), or its id
        # or a number its title names in their text; a notice that only shares its name is a reading.
        named = {compact(x) for x in SOLICITATION_IN_TITLE_RE.findall(need["title"])}
        own = [e for e in notices if (e.get("line") and e["line"] != key and own_notice(key, e["line"]) and key.startswith("notice:")) or e.get("line") == key
               or key in e["text"] or any(compact(m.group(1)) in named for m in [SOLICITATION_RE.search(e["text"])] if m)]
        mine = [lives[s] for s in {compact(m.group(1)) for e in own or notices for m in [SOLICITATION_RE.search(e["text"])] if m}
                if s in lives and set(lives[s]["steps"]) & set(STEPS)]
        if not mine:
            return slip_rate(layer, need["owner_id"], need["title"])
        target = max(mine, key=lambda r: (max(r["steps"].values()), r["solicitation"]))
        org, title = need["owner_id"], need["title"]
        if not own:
            reading = "the notice shares the row's name but carries neither its line nor a number the row names: a reading, not a stored tie"
    else:
        target = lives.get(compact(key))
        if not target:
            return f"no forecast row or solicitation number {key!r} in the record"
        org, title = target["org"], target["title"]
    step = max((s for s in STEPS if s in target["steps"]), key=list(STEPS).index, default=None)
    if step is None:
        return f"{title[:100]}: solicitation {target['solicitation']} has no request for information, presolicitation or solicitation in the record"
    start, awarded = target["steps"][step], target["steps"].get("contract_awarded")
    cases = [(days_between(r["steps"][step], r["steps"]["contract_awarded"]), r) for r in lives.values()
             if r is not target and step in r["steps"] and r["steps"].get("contract_awarded", "") >= r["steps"][step]]
    where, scoped = "in the whole record", cases
    for a in chain(org, layer.orgs) if org else []:
        tree = layer.subtree(a)
        inside = [c for c in cases if c[1]["org"] in tree]
        if len(inside) >= MIN_ANALOGS:
            where, scoped = f"under {office_name(a, layer.orgs)}", inside
            break
    lines = [f"{title[:100]}: solicitation {target['solicitation']}, {STEPS[step]} on {start}"
             + (f"; award notice {awarded}, {days_between(start, awarded)} day(s) later" if awarded else "; no award notice yet")]
    lines += [reading] if reading else []
    if not scoped:
        return "\n".join(lines + [f"no past buy in the record went from a {STEPS[step]} to an award notice"])
    days = sorted(d for d, _ in scoped)
    low, mid, high = quantiles(days, n=4) if len(days) >= 2 else (days[0],) * 3
    lines.append(f"{len(days)} past buy(s) {where} went from a {STEPS[step]} to an award notice: median {median(days):.0f} day(s), "
                 f"middle half {low:.0f} to {high:.0f}")
    if not awarded:
        window = (shift(start, round(low)), shift(start, round(high)))
        lines.append(f"read against them, the award would fall between {window[0]} and {window[1]}, median {shift(start, round(median(days)))}"
                     + ("; that window has passed without an award notice" if window[1] < layer.as_of else ""))
    mine_words = words(title)
    nearest = sorted(scoped, key=lambda c: (-len(mine_words & words(c[1]["title"])) / max(1, len(mine_words | words(c[1]["title"]))), c[1]["solicitation"]))[:3]
    lines.append("nearest by name:")
    lines += [f"  - {r['solicitation']} {r['title'][:80]}: {STEPS[step]} {r['steps'][step]}, award notice {r['steps']['contract_awarded']} ({d} days)"
              for d, r in nearest]
    return "\n".join(lines)


# ------------------------------------------------------------------ competitor radar

def moves(layer: Layer, name: str, days: int = 90, resolved: dict | None = None) -> str:
    """What a vendor did in the last `days`: contracts and orders that became public, offices it entered, extensions
    and options, what ends next, and every other statement that names it; against the window before."""
    spellings = [s.lower() for s in names_for(name, resolved if resolved is not None else load_vendors())]
    theirs = [e for e in layer.events if e["family"] == "incumbent" and e["available_by"] <= layer.as_of
              and any(s in (e.get("vendor") or "").lower() for s in spellings)]
    if not theirs:
        return awards_not_collected() or f"no contract in the record names a vendor matching {name!r}"
    since, before = shift(layer.as_of, -days), shift(layer.as_of, -2 * days)
    new = [e for e in theirs if e["event_type"] == "contract_expires" and e["available_by"] > since]
    prior = [e for e in theirs if e["event_type"] == "contract_expires" and before < e["available_by"] <= since]
    kept = [e for e in theirs if e["event_type"] in ("contract_extended", "contract_modified") and e["available_by"] > since]
    first: dict[str, str] = {}
    for e in sorted(theirs, key=lambda e: e["available_by"]):
        first.setdefault(office_name(e["org"], layer.orgs) or "-", e["available_by"])
    entered = sorted(o for o, d in first.items() if d > since)
    ending = sorted((c for c in contracts(theirs).values() if c["end"] and layer.as_of < c["end"] <= shift(layer.as_of, days)), key=lambda c: c["end"])
    said = sorted((e for e in layer.events if e["family"] != "incumbent" and since < e["available_by"] <= layer.as_of
                   and any(len(s) >= 6 and s in e["text"].lower() for s in spellings)), key=lambda e: e["available_by"], reverse=True)
    lines = [f"{Counter(e['vendor'] for e in theirs).most_common(1)[0][0]} between {since} and {layer.as_of}: {len(new)} contract(s) or order(s) "
             f"became public (the {days} days before: {len(prior)}); {len(kept)} extension(s) or option(s); {len(said)} other statement(s) name it",
             "offices entered for the first time: " + (", ".join(entered) or "none")]
    if new:
        lines.append("new by office: " + ", ".join(f"{o} {n}" for o, n in Counter(office_name(e["org"], layer.orgs) or "-" for e in new).most_common(SHOWN)))
        lines += newest(new, layer.orgs)
    if ending:
        work = Counter(c["work"][:60] for c in ending)
        lines.append(f"ending in the next {days} days: {len(ending)}, the first {ending[0]['end']} ({ending[0]['contract']}); by work: "
                     + "; ".join(f"{w or 'not stated'} {n}" for w, n in work.most_common(3)))
    lines += [listed(e, layer.orgs) for e in said[:SHOWN]]
    return "\n".join(lines)


# ------------------------------------------------------------------ teaming: who already does the work

def team(layer: Layer, capabilities: list[str], name: str = "", top: int = 10, resolved: dict | None = None) -> str:
    """Whom to team with for a capability a company lacks: the vendors whose contracts' stated work carries its words,
    one per UEI, under an organization's tree when one is named; ranked by running contracts, contracts held, newest."""
    terms = capability_terms("; ".join(capabilities))
    if not terms:
        return "no capability given"
    oid = layer.org_id(name) if name else None
    if name and not oid:
        return f"no organization named {name!r}"
    tree = layer.subtree(oid) if oid else None
    resolved = resolved if resolved is not None else load_vendors()
    uei_of, name_of = resolved.get("spelling_to_uei", {}), {v["uei"]: v["name"] for v in resolved.get("vendors", [])}
    by: dict[str, list[dict]] = {}
    for e in scan(terms, layer.events):
        if e["family"] == "incumbent" and e.get("vendor") and e["available_by"] <= layer.as_of and (tree is None or e["org"] in tree):
            by.setdefault(uei_of.get(e["vendor"], e["vendor"]), []).append(e)
    ranked = []
    for key, evs in by.items():
        held = list(contracts(evs).values())
        running = sorted((c for c in held if c["end"] > layer.as_of), key=lambda c: c["end"])
        ranked.append((len(running), len(held), max(e["available_by"] for e in evs), key, held, running))
    ranked.sort(reverse=True)
    if not ranked and (note := awards_not_collected()):
        return note
    lines = [f"{f'Under {name}: ' if oid else ''}{len(ranked)} vendor(s) hold {sum(r[1] for r in ranked)} contract(s) "
             f"whose stated work names {'; '.join(capabilities)}; searched for: {', '.join(terms)}"]
    for n, (_, _, last, key, held, running) in enumerate(ranked[:top], start=1):
        offices = Counter(office_name(c["org"], layer.orgs) or "-" for c in held)
        soon = [c for c in running if c["end"] <= shift(layer.as_of, 365)]
        lines.append(f"{n}. {name_of.get(key, key)}: {len(held)} contract(s) under {', '.join(o for o, _ in offices.most_common(3))}; "
                     f"{len(running)} running; newest public {last}"
                     + (f"; ends within a year: {soon[0]['contract']} {soon[0]['end']} ({soon[0]['work'][:50] or 'work not stated'})" if soon else ""))
        lines += [f"     {w}" for w, _ in Counter(c["work"][:90] for c in held if c["work"]).most_common(2)]
    return "\n".join(lines)


# ------------------------------------------------------------------ meeting prep

def expiring_unannounced(ending: list[dict], naming: dict[str, list[dict]], as_of: str) -> list[dict]:
    return [c for c in ending if c["end"] <= shift(as_of, 365)
            and not any(e["event_type"] in OPEN_NOTICES for e in naming.get(compact(c["contract"]), []))]


def questions(layer: Layer, oid: str, tree: set[str], ending: list[dict], naming: dict[str, list[dict]]) -> list[str]:
    """What to ask, each from a dated statement: a slipped forecast, a request for information, a justification, a
    finding or a delay, a new leader or a reorganization above the office, a contract ending with no follow-on notice."""
    year, half = shift(layer.as_of, -365), shift(layer.as_of, -180)
    above = set(chain(oid, layer.orgs))
    recent = sorted((e for e in layer.events if year < e["available_by"] <= layer.as_of and (e["org"] in tree or e["org"] in above)),
                    key=lambda e: e["available_by"], reverse=True)
    out = []
    for e in recent:
        t, d = plain(e)[:80], e["available_by"]
        read = f" (the notice names no office; {e['read_as']})" if e.get("read_as") else ""
        if e["event_type"] == "forecast_changed" and e.get("slip") and e["org"] in tree:
            out.append(f"The forecast moved \"{t}\" later ({d}): what is the award window now, and what moved it?")
        elif e["event_type"] == "rfi_released" and d > half and e["org"] in tree:
            out.append(f"The request for information of {d}, \"{t}\": when is the solicitation planned, and what did the responses change?{read}")
        elif e["event_type"] == "justification_posted" and e["org"] in tree:
            out.append(f"The justification of {d}, \"{t}\": what has to be in place before the follow-on is competed?{read}")
        elif e["event_type"] in ("audit_finding", "program_delayed") and e["org"] in tree:
            out.append(f"\"{t}\" ({d}): how does it change the schedule or the requirement?")
        elif e["event_type"] == "leadership_change":
            out.append(f"{t}: what has the new leadership set as priorities?")
        elif e["event_type"] == "reorganization":
            out.append(f"The reorganization stated on {d} at {office_name(e['org'], layer.orgs)} (\"{t[:70]}\"): where does the office sit now, and who decides its buys?")
    guessed = sorted((e for e in layer.events if pointed(e) in tree and e["event_type"] in STEPS and year < e["available_by"] <= layer.as_of),
                     key=lambda e: e["available_by"], reverse=True)
    out += [f"The {STEPS[e['event_type']]} of {e['available_by']} filed at {office_name(e['org'], layer.orgs)}, \"{plain(e)[:70]}\", names no "
            f"office, and " + (f"the model reads it here from \"{e['model_read'][1][:60]}\"" if e.get("model_read") else
                               f"its words ({', '.join(e['guesses'][0][2])}) point here first") + ": is it this office's buy?" for e in guessed[:2]]
    out += [f"Contract {c['contract']} ({c['vendor'] or 'vendor not stated'}, {c['work'][:50]}) ends {c['end']} and no notice names a follow-on: "
            f"will it be competed, extended or bridged?" for c in expiring_unannounced(ending, naming, layer.as_of)[:3]]
    return list(dict.fromkeys(out))[:SHOWN]


def placed_here(a: dict, oid: str, layer: Layer, by_id: dict) -> bool:
    """An action whose every statement the page layer reads to another office is that office's: a cell's names can
    match a notice filed at the contracting office that the record places with a sibling office."""
    known = [by_id[x] for x in a.get("evidence", ()) if x in by_id]
    return not known or any(admissible(e["org"], oid, layer.orgs)[0] for e in known)


def action_label(a: dict, by_id: dict) -> str:
    """What an action on one statement is about: the notice to answer or the contract (by number and vendor) to watch,
    not the name of the cell whose names matched it. A forecast or meeting action is about the cell itself."""
    e = by_id.get(a["evidence"][0]) if len(a.get("evidence", ())) == 1 and a["type"] in ("respond_notice", "watch_expiration", "find_partner") else None
    if not e:
        return a["name"][:80]
    m = CONTRACT_RE.search(e["title"]) if e["family"] == "incumbent" else None
    return f"{m.group(1)} {e.get('vendor') or ''}".strip() if m else plain(e)[:80]


def prep(layer: Layer, name: str, since: str | None = None, pulse: dict | None = None, dna: dict | None = None, who: str = "",
         details: dict[str, dict] | None = None) -> str:
    """A meeting brief for an office: where it sits and how it buys, what changed since the last meeting, the open
    actions, the contracts ending, whom the record ties to it, and what to ask; with a person, their dated positions."""
    oid = layer.org_id(name)
    if not oid:
        return f"no organization named {name!r}"
    tree = layer.subtree(oid)
    since = since or shift(layer.as_of, -90)
    mine = [e for e in layer.events if e["org"] in tree and e["available_by"] <= layer.as_of]
    fresh = sorted((e for e in mine if e["available_by"] > since), key=lambda e: (e["available_by"], e["id"]), reverse=True)
    o, up = layer.orgs[oid], chain(oid, layer.orgs)
    lines = [f"Meeting brief: {o['acronym'] or o['name']}, {o['name']}, as of {layer.as_of}; above it: "
             + (" > ".join(office_name(x, layer.orgs) for x in upward(oid, layer.orgs)[1:]) or "-")]
    book = (dna or {}).get("offices", {}).get(office_name(oid, layer.orgs)) or (dna or {}).get("contracting_offices", {}).get(office_name(oid, layer.orgs))
    if book:
        lines.append(book_line(book))
    lines.append(f"since {since}: {len(fresh)} statement(s)"
                 + (": " + ", ".join(f"{t.replace('_', ' ')} {n}" for t, n in Counter(e["event_type"] for e in fresh).most_common()) if fresh else ""))
    lines += newest(fresh, layer.orgs)
    names, by_id = {office_name(x, layer.orgs) for x in tree}, {e["id"]: e for e in layer.events}
    acts = queue([a for a in (pulse or {}).get("actions", []) if a["office"] in names and placed_here(a, oid, layer, by_id)])
    if acts:
        lines.append(f"open actions from the pulse ({len(acts)}):")
        lines += [f"  - {a['by'] or 'now'} {a['type'].replace('_', ' ')}: {action_label(a, by_id)} ({a['why']})" for a in acts[:SHOWN]]
    ending = sorted((c for c in contracts(mine).values() if c["end"] and layer.as_of < c["end"] <= shift(layer.as_of, TWO_YEARS)), key=lambda c: c["end"])
    if ending:
        lines.append(f"incumbent contracts ending within two years: {len(ending)}")
        lines += [f"  - {c['end']} {c['contract']} {c['vendor'] or '-'}: {c['work'][:70]}" for c in ending[:5]]
    read_here = sorted((e for e in layer.events if e.get("model_read") and e["model_read"][0] in tree and e["org"] not in tree
                        and e["available_by"] <= layer.as_of), key=lambda e: (e["available_by"], e["id"]), reverse=True)
    if read_here:
        lines.append(f"records filed elsewhere that the model reads to this office: {len(read_here)} ("
                     + ", ".join(f"{f} {n}" for f, n in Counter(e["family"] for e in read_here).most_common()) + ")")
        lines += [f"  - {e['available_by']} {e['family']}: {plain(e)[:80]} (from \"{e['model_read'][1][:50]}\")" for e in read_here[:5]]
    owned = [n for n in layer.needs if n["owner_id"] in tree]
    named = named_on_rows(layer, owned, details or {})
    people = [*named, *(p for p in contacts_for(layer.reach(oid), layer.roster, layer.as_of, limit=6) if p["name"] not in {c["name"] for c in named})][:6]
    if people:
        lines.append("people: " + "; ".join(f"{p['name']} ({p['title'][:50]}, {p['observed_at']})" for p in people))
    ways = {r["recommendation"]: r for r in routes_for(up or [oid], layer.routes, layer.routes_by)}
    lines += [f"route in, {r['side']}: {r['recommendation'][:120]} (observed {r['observed_at']})" for r in first_routes(list(ways.values()), 3)]
    asked = questions(layer, oid, tree, ending, cited([e for e in layer.events if e["available_by"] <= layer.as_of]))
    lines += ["questions to ask:"] + [f"  - {q}" for q in asked] if asked else []
    if who:
        lines += ["", person(layer, who, layer.roster)]
    return "\n".join(lines)


# ------------------------------------------------------------------ after the meeting: notes kept apart

def note(layer: Layer, name: str, text: str, who: str = "", day: str = "", path: Path = NOTES) -> str:
    oid = layer.org_id(name)
    if not oid:
        return f"no organization named {name!r}"
    row = {"office": office_name(oid, layer.orgs), "org": oid, "date": day or layer.as_of, "person": who, "text": text,
           "status": "internal note; no record has confirmed it"}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return f"noted under {row['office']} on {row['date']}; kept in {path}, apart from the record"


def note_names(text: str, office: str) -> list[str]:
    """The names a note carries: acronyms and identifiers; not the office itself, a fiscal year (a date, not a name) or an
    ordinary word."""
    skip = GENERIC | NOTE_STOP | {w.lower() for w in office.split()}
    found = re.findall(r"[A-Za-z0-9][A-Za-z0-9/-]{2,}", text)
    return list(dict.fromkeys(t for t in found if (sum(ch.isupper() for ch in t) >= 2 or any(ch.isdigit() for ch in t))
                              and t.lower() not in skip and not re.fullmatch(r"FY\d{2,4}", t, re.I)))


def notes(layer: Layer, name: str, path: Path = NOTES) -> str:
    """Each note on the office beside the later records that share its names; a record beside a note is shown, never
    merged into it, and the note never becomes a record."""
    oid = layer.org_id(name)
    if not oid:
        return f"no organization named {name!r}"
    tree = layer.subtree(oid)
    command = layer.subtree((chain(oid, layer.orgs) or [oid])[-1])  # the office's command and its contracting offices, not another command's
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []
    mine = sorted((r for r in rows if r["org"] in tree), key=lambda r: r["date"])
    lines = [f"{office_name(oid, layer.orgs)}: {len(mine)} internal note(s)"]
    for r in mine:
        names = note_names(r["text"], r["office"])
        later = [e for e in layer.events if r["date"] < e["available_by"] <= layer.as_of and e["org"] in command]
        found = sorted(scan(names, later), key=lambda e: e["available_by"]) if names else []
        with_ = f" with {r['person']}" if r["person"] else ""
        lines.append(f"- {r['date']} {r['office']}{with_}: {r['text'][:200]}")
        lines.append(f"    names read: {', '.join(names) or 'none'}; later records sharing them: {len(found)}")
        lines += ["  " + listed(e, layer.orgs) for e in found[:3]]
    return "\n".join(lines)


# ------------------------------------------------------------------ evidence room

def manifest() -> dict[str, dict]:
    if not MANIFEST.exists():
        return {}
    rows = [json.loads(x) for x in MANIFEST.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["sha256"][:12]: r for r in rows if r.get("sha256")}


def where_from(e: dict) -> str:
    """The public place a statement comes from: the SAM.gov page for a notice, the sheet row for a forecast row, the
    page the source recorded, the link the text gives, else the source. The recorded page comes before the text: a
    news body can end on a publisher's copyright link."""
    if m := SAM_RECORD_RE.search(e["text"]):
        return f"https://sam.gov/opp/{m.group(1)}/view"
    if m := ROW_RE.search(e["text"]):
        return m.group(1)
    if e.get("url"):
        return e["url"]
    if m := URL_RE.search(e["text"]):
        return m.group(0)
    contract = CONTRACT_RE.search(e["title"]) if e["family"] == "incumbent" else None
    return f"{e['provider']} {contract.group(1)}" if contract else e["provider"]


def export(layer: Layer, key: str, out: Path, docs: dict[str, dict] | None = None) -> str:
    """The evidence room for one requirement: its card and every statement behind it with where it comes from, and a
    copy of each saved document a statement cites by its hash, in one folder a reviewer can open without the tools."""
    need = next((n for n in layer.needs if n["key"] == key), None)
    if need is None:
        return f"no forecast row {key!r}"
    docs = manifest() if docs is None else docs
    c = layer.cell(key)
    _, hits = need_cell(need, layer.corpus, layer.recurring)
    hits = sorted((e for e in hits if e["available_by"] <= layer.as_of), key=lambda e: (e["available_by"], e["id"]))
    (out / "documents").mkdir(parents=True, exist_ok=True)
    cell_ = lambda s: s.replace("|", "/").replace("\n", " ")
    lines = [f"# Evidence: {need['title']}", "", f"Forecast row {key}, owned by {need['owner'] or 'no office named'}; read as of {layer.as_of}.", "",
             "```", c["card"], "```", "", "| available | family | statement | from | saved document |", "|---|---|---|---|---|"]
    copied = set()
    for e in hits:
        saved = ""
        sha = SHA_RE.search(e["text"])
        doc = docs.get(sha.group(1)[:12]) if sha else None
        if doc:
            src = RESEARCH.parent / doc["path"]
            saved = f"{doc['path']} (sha256 {doc['sha256'][:12]})"
            if src.is_file():
                if src.name not in copied:
                    shutil.copyfile(src, out / "documents" / src.name)
                    copied.add(src.name)
                saved = f"documents/{src.name} (sha256 {doc['sha256'][:12]})"
        lines.append(f"| {e['available_by']} | {e['family']} | {e['event_type'].replace('_', ' ')}: {cell_(plain(e)[:110])} | {cell_(where_from(e))} | {saved} |")
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"wrote {out / 'README.md'}: {len(hits)} statement(s), {len(copied)} saved document(s) copied"


# ------------------------------------------------------------------ command line

def rows_as_released(layer: Layer) -> dict[str, dict]:
    from outreach import row_details  # outreach reads this module at import, so the forecast rows load here
    return row_details(layer.as_of)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selfcheck"]:
        return selfcheck()
    ap = argparse.ArgumentParser(prog="ask.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", default=None, help="answer as of this day, from what was public by then")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("changed"); p.add_argument("org"); p.add_argument("topic", nargs="?", default=""); p.add_argument("--days", type=int, default=120)
    p = sub.add_parser("match"); p.add_argument("capabilities", nargs="?", default=""); p.add_argument("--naics", default="")
    p.add_argument("--profile", help='a JSON profile {"name", "capabilities", "naics"}, or a plain-text file of the company\'s own words')
    p.add_argument("--top", type=int, default=10)
    p = sub.add_parser("prep"); p.add_argument("org"); p.add_argument("--since"); p.add_argument("--person", default="")
    p = sub.add_parser("analogs"); p.add_argument("key")
    p = sub.add_parser("incumbents"); p.add_argument("org"); p.add_argument("--within", type=int, default=TWO_YEARS)
    p = sub.add_parser("moves"); p.add_argument("vendor"); p.add_argument("--days", type=int, default=90)
    p = sub.add_parser("team"); p.add_argument("capabilities"); p.add_argument("--org", default=""); p.add_argument("--top", type=int, default=10)
    p = sub.add_parser("note"); p.add_argument("org"); p.add_argument("text"); p.add_argument("--person", default=""); p.add_argument("--date", default="")
    p = sub.add_parser("notes"); p.add_argument("org")
    p = sub.add_parser("export"); p.add_argument("key"); p.add_argument("dir")
    a = ap.parse_args(argv)
    roster = load_people()
    layer = Layer(json.loads(CORPUS.read_text(encoding="utf-8")), roster, as_of=a.as_of)
    if a.cmd == "changed":
        print(changed(layer, a.org, a.topic, a.days))
    elif a.cmd == "match":
        profile = read_profile(Path(a.profile)) if a.profile else {}
        caps = profile.get("capabilities") or [x.strip() for x in a.capabilities.split(";") if x.strip()]
        codes = profile.get("naics") or [x.strip() for x in a.naics.split(",") if x.strip()]
        print(match(layer, caps, codes, load_json(DNA), a.top, profile.get("name", ""), rows_as_released(layer)))
    elif a.cmd == "prep":
        print(prep(layer, a.org, a.since, load_json(PULSE), load_json(DNA), a.person, rows_as_released(layer)))
    elif a.cmd == "analogs":
        print(analogs(layer, a.key))
    elif a.cmd == "incumbents":
        print(incumbents(layer, a.org, a.within))
    elif a.cmd == "moves":
        print(moves(layer, a.vendor, a.days))
    elif a.cmd == "team":
        print(team(layer, [x.strip() for x in a.capabilities.split(";") if x.strip()], a.org, a.top))
    elif a.cmd == "note":
        print(note(layer, a.org, a.text, a.person, a.date))
    elif a.cmd == "notes":
        print(notes(layer, a.org))
    elif a.cmd == "export":
        print(export(layer, a.key, Path(a.dir)))
    return 0


# ------------------------------------------------------------------ selfcheck

def fixture() -> Layer:
    """A command, a program office and a contracting office under it, an office elsewhere; one buy in solicitation
    with five past buys under the same contracting office, an incumbent extended with a slipped follow-on row, and a
    vendor that entered a new office this quarter. Ids carry the tool's name: scan caches text by event id."""
    orgs = {"cmd": {"acronym": "NAVSEA", "name": "Naval Sea Systems Command", "parent": "", "org_type": "systems_command"},
            "pms": {"acronym": "PMS 406", "name": "Unmanned Maritime Systems", "parent": "cmd", "org_type": "program_office"},
            "ko": {"acronym": "N00024", "name": "NAVSEA contracts", "parent": "cmd", "org_type": "contracting_office"},
            "far": {"acronym": "PMW 1", "name": "Office One", "parent": "", "org_type": "program_office"}}

    def ev(i, fam, typ, day, org, title, text="", **kw):
        return {"id": f"ask-{i}", "event_type": typ, "date": day, "available_by": kw.pop("available_by", day), "provider": fam,
                "family": fam, "org": org, "title": title, "text": f"{title} {text}", "slip": kw.pop("slip", False), **kw}

    def notice(i, typ, word, day, sol, title):
        rec = f"; solicitation {sol}; record https://sam.gov/api/prod/opps/v2/opportunities/{i}0f?api_key=null retrieved 2026-09-01 sha256 {i:0>12}"
        return ev(i, "notice", typ, day, "ko", f"SAM.gov {word} {day}: {title}", rec)

    events = [notice("1", "presolicitation_posted", "presolicitation", "2026-01-10", "N00024-26-R-0001", "Unmanned Surface Vessel Support"),
              notice("2", "rfp_released", "solicitation", "2026-03-01", "N00024-26-R-0001", "Unmanned Surface Vessel Support")]
    for n, gap in enumerate((100, 200, 300, 400, 500), start=3):
        events += [notice(f"{n}a", "rfp_released", "solicitation", "2022-01-01", f"N00024-22-R-000{n}", f"Hull Coating Services {n}"),
                   notice(f"{n}b", "contract_awarded", "award notice", shift("2022-01-01", gap), f"N00024-22-R-000{n}", f"Hull Coating Services {n}")]
    events += [ev("i1", "incumbent", "contract_expires", "2020-02-01", "pms", "Incumbent contract N0002420C0001 ends 2026-06-30: USV SUSTAINMENT",
                  "base award signed 2020-02-01", vendor="ACME INC", available_by="2020-05-01"),
               ev("i2", "incumbent", "contract_extended", "2026-04-01", "pms", "Incumbent contract N0002420C0001 extended, ends 2027-01-31 (was 2026-06-30): USV SUSTAINMENT",
                  vendor="ACME INC", polarity="negative", available_by="2026-06-30"),
               ev("f1", "forecast", "forecast_changed", "2026-06-19", "pms", "USV Sustainment Follow-On (C)", "incumbent N00024-20-C-0001 sha256 " + "f" * 64,
                  slip=True, polarity="negative"),
               ev("i3", "incumbent", "contract_expires", "2026-06-01", "far", "Incumbent contract N0000126F0001 ends 2028-01-01: RADIO SPARES",
                  vendor="ACME INC", available_by="2026-08-30"),
               ev("l1", "leaders", "leadership_change", "2026-07-01", "cmd", "Leadership change at Naval Sea Systems Command on 2026-07-01")]
    needs = [{"key": "N00024-26-RFPREQ-PMS-406-0001", "title": "Unmanned Surface Vessel Support (C)", "owner": "PMS 406", "owner_id": "pms"}]
    roster = [{"name": "Ann Example", "emails": [], "positions": [{"office": "pms:406", "org": "pms", "role_type": "program_manager", "raw_title": "Program Manager",
                                                                  "observed_at": "2026-08-01", "source": "sam", "source_ref": "x", "source_url": "",
                                                                  "confidence": "0.9", "context": "USV support"}]}]
    return Layer({"orgs": orgs, "events": events, "needs": needs, "outcomes": []}, roster, as_of="2026-09-01", routes=[])


def selfcheck() -> int:
    layer = fixture()
    c = changed(layer, "NAVSEA", "autonomy")
    assert "2 statement(s) from 2 families under 1 organization(s)" in c and "USV" in c and "Ann Example" in c and "[against]" in c, c
    assert "Leadership change" in changed(layer, "NAVSEA") and "no organization" in changed(layer, "NOWHERE")
    m = match(layer, ["autonomy"], ["3366"], {"offices": {"PMS 406": {"naics": [{"value": "336611 SHIP BUILDING AND REPAIRING", "share": 0.6}]}}})
    assert "\n1. PMS 406 (under NAVSEA)" in m and "60% of its awards" in m and "     N00024-26-RFPREQ-PMS-406-0001: Unmanned" in m, m
    read = Layer({**layer.corpus, "events": [*layer.corpus["events"], {**layer.corpus["events"][0], "id": "ask-r1", "title": "SAM.gov award notice: autonomy kit",
                                                                            "text": "autonomy kit", "model_read": ("pms", "autonomy kit", "PMS 406 buys autonomy")}]},
                 layer.roster, as_of=layer.as_of, routes=[])
    assert "1. PMS 406 (under NAVSEA): 4 statement(s) (1 read here by the model)" in match(read, ["autonomy"], [], {}), match(read, ["autonomy"], [], {})
    a = analogs(layer, "N00024-26-R-0001")
    assert "solicitation on 2026-03-01; no award notice yet" in a and "5 past buy(s) under N00024" in a and "median 300 day(s), middle half 150 to 450" in a, a
    assert "between 2026-07-29 and 2027-05-25" in a, a
    by_row = analogs(layer, "N00024-26-RFPREQ-PMS-406-0001")
    assert by_row.startswith("Unmanned Surface Vessel Support (C): solicitation N0002426R0001") and "5 past buy(s) under NAVSEA" in by_row \
        and "a reading, not a stored tie" in by_row and "a reading" not in a, \
        "a row starts from its office, so the analogs come from the nearest part of the tree above it that holds five"
    assert "Of 1 forecast revision(s) in the whole record, 1 moved the award later" in slip_rate(layer, "pms", "A row"), slip_rate(layer, "pms", "A row")
    i = incumbents(layer, "PMS 406")
    assert "N0002420C0001 ACME INC: USV SUSTAINMENT; signed 2020-02-01; ends 2027-01-31" in i and "extended 1 time(s)" in i and "award moved later" in i and "planned and has slipped or been bridged" in i, i
    old = next(e for e in layer.events if e["id"] == "ask-3a")
    c0 = contracts(layer.events)["N0002420C0001"]
    lives = lifecycles(layer.events, layer.orgs)
    assert "has an award notice of 2022-04-11" in weak_points(c0, [old], 0.1, lives, layer.as_of)[1]
    assert "no award notice followed" in weak_points(c0, [old], 0.1, {}, layer.as_of)[1], "an old notice with no award is a question, not a market"
    sole = {**old, "title": "SAM.gov presolicitation 2026-08-01: Notice of Intent to Award a Sole Source Contract", "available_by": "2026-08-01"}
    assert "has slipped or been bridged" not in weak_points(c0, [sole], 0.1, {}, layer.as_of)[1] and \
        "sole-source" in weak_points(c0, [sole], 0.1, {}, layer.as_of)[0][-2], "a sole-source notice keeps the incumbent; it opens nothing"
    v = moves(layer, "acme", 90, {})
    assert "1 contract(s) or order(s) became public" in v and "offices entered for the first time: PMW 1" in v, v
    t = team(layer, ["autonomy"], "", 10, {"spelling_to_uei": {"ACME INC": "U1"}, "vendors": [{"uei": "U1", "name": "ACME"}]})
    assert "1 vendor(s) hold 1 contract(s)" in t and "\n1. ACME: 1 contract(s) under PMS 406; 1 running" in t \
        and "ends within a year: N0002420C0001 2027-01-31 (USV SUSTAINMENT)" in t, t
    assert team(layer, ["autonomy"], "PMW 1", 10, {}).startswith("Under PMW 1: 0 vendor(s)"), "radio spares are not autonomy work"
    p = prep(layer, "PMS 406", "2026-05-01", {"actions": [{"type": "watch_expiration", "office": "PMS 406", "name": "USV", "why": "ends", "by": "2027-01-31", "priority": 1}]})
    assert "open actions from the pulse (1)" in p and "moved \"USV Sustainment Follow-On (C)\" later" in p and "new leadership" in p, p
    assert "N0002420C0001 (ACME INC" in p and "Ann Example" in p, "a contract ending within a year with no follow-on notice is a question"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "notes.jsonl"
        note(layer, "PMS 406", "They said the USV follow-on slips to FY27", "Ann Example", "2026-05-01", path)
        n = notes(layer, "PMS 406", path)
        assert "names read: USV;" in n and "later records sharing them: 2" in n and "USV Sustainment Follow-On" in n, n
        raw = Path(tmp) / "raw.xlsx"
        raw.write_text("sheet", encoding="utf-8")
        e = export(layer, "N00024-26-RFPREQ-PMS-406-0001", Path(tmp) / "room", {"000000000001": {"sha256": "0" * 11 + "1" * 53, "path": str(raw)}})
        room = (Path(tmp) / "room" / "README.md").read_text(encoding="utf-8")
        assert "2 statement(s), 1 saved document(s) copied" in e and "https://sam.gov/opp/10f/view" in room and "Stage: solicitation" in room, room
        assert "documents/raw.xlsx (sha256 000000000001)" in room and (Path(tmp) / "room" / "documents" / "raw.xlsx").exists(), room
    print("ask selfcheck ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

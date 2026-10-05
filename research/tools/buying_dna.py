#!/usr/bin/env python3
"""Buying DNA and incumbent position from the FPDS pages the sweep saved: how a contracting
office, a program office and one requirement's cell actually buy, in the coded fields FPDS states on each base award,
and who holds the work, until when, with what pattern of renewal.

No new collection. The contracting office's book is every base award on the saved pages; a program office's book is
the awards the loader places under it (the office code in the description, the notice under the solicitation, a
forecast line's incumbent; document 04); a cell's book is the contracts its names reach in the frozen corpus. Shares are
of awards, values in dollars as FPDS states them (base and all options), durations from signature to the ultimate
completion date, and the recompete cadence is the gap between successive awards that carry the same requirement text.

    python research/tools/buying_dna.py build [--check]        # -> research/results/buying_dna.json and a table
    python research/tools/buying_dna.py show OFFICE|CELL_KEY   # one book, read out
    python research/tools/buying_dna.py --selfcheck
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import CORPUS, RESEARCH, awards_not_collected, shift, wide  # noqa: E402
from fpds_sweep import FIELDS, OFFICES, manifest_rows, saved_pages, windows  # noqa: E402
from lrae_package import ROOT, fpds_entries  # noqa: E402
from pulse import PULSE  # noqa: E402
from vocabulary import classify  # noqa: E402

DNA = RESEARCH / "results" / "buying_dna.json"
PIID_RE = re.compile(r"Incumbent contract (\S+) ends (\d{4}-\d{2}-\d{2})")
BANDS = (("under $250k", 250_000), ("$250k to $1M", 1_000_000), ("$1M to $10M", 10_000_000), ("$10M to $100M", 100_000_000), ("over $100M", None))
TOP = 5
RECOMPETE_MIN_AWARDS = 2
SAME_AWARD_DAYS = 90  # one requirement signed to several vendors across weeks is one multiple award, not a recompete
CONTRACT_ACTIONS = ("DEFINITIVE CONTRACT", "IDC", "BPA", "GWAC", "FSS", "BOA")  # a contract or a vehicle, not an order under one
POSITION_HORIZON_DAYS = 730


def book(today: date | None = None) -> dict[str, dict]:
    """Every base award on the saved sweep pages with its coded fields, by PIID."""
    manifest, out = manifest_rows(), {}
    # Both sweeps: the awards each contracting office signed and, where the profile names a funding agency, the
    # awards other offices signed for it (fpds_sweep.FIELDS; the second is empty for the Navy).
    for field, offices, _label in FIELDS:
        for office in offices:
            for window in windows(today or date.today()):
                pages, _ = saved_pages(manifest, office, window, field=field)
                for page in pages:
                    for entry in fpds_entries((ROOT / page["path"]).read_bytes(), width=None, full=True):
                        if entry["piid"]:
                            out.setdefault(entry["piid"], dict(entry, fy=window["fy"]))
    return out


def money(entry: dict) -> float | None:
    raw = entry["coded"]["totalBaseAndAllOptionsValue"]["code"]
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def months(entry: dict) -> int | None:
    if entry["signed"] and entry["completion"]:
        return round((date.fromisoformat(entry["completion"]) - date.fromisoformat(entry["signed"])).days / 30.4)
    return None


def shares(counter: Counter, total: int, top: int = TOP) -> list[dict]:
    return [{"value": k or "not stated", "awards": n, "share": round(n / total, 3)} for k, n in counter.most_common(top)] if total else []


def described(entry: dict, name: str) -> str:
    c = entry["coded"][name]
    return c["description"] or c["code"]


def requirement_key(description: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", description.lower())[:60].strip()


def award_events(days: set[str]) -> list[str]:
    """The first signature of each award event: a signature within SAME_AWARD_DAYS of the one before joins its event."""
    kept, last = [], None
    for day in sorted(days):
        if last is None or (date.fromisoformat(day) - date.fromisoformat(last)).days > SAME_AWARD_DAYS:
            kept.append(day)
        last = day
    return kept


def dna(entries: list[dict]) -> dict:
    """One book's Buying DNA; every number is a count, a share or a median of the coded fields on these awards."""
    n = len(entries)
    if not n:
        return {"awards": 0}
    values = [v for v in map(money, entries) if v is not None]
    bands = Counter()
    for v in values:
        bands[next(label for label, cap in BANDS if cap is None or v < cap)] += 1
    vendor_of = lambda e: e["coded"]["ultimateParentUEIName"]["code"] or e["vendor"]
    by_vendor = Counter(vendor_of(e) for e in entries)
    value_by_vendor = defaultdict(float)
    for e in entries:
        value_by_vendor[vendor_of(e)] += money(e) or 0.0
    total_value = sum(value_by_vendor.values())
    top_vendors = by_vendor.most_common(3)
    offers = [int(e["coded"]["numberOfOffersReceived"]["code"]) for e in entries if e["coded"]["numberOfOffersReceived"]["code"].isdigit()]
    durations = [m for m in map(months, entries) if m is not None]
    # Recompete cadence: the same requirement text awarded again as a new contract or vehicle, and the gap between the
    # signatures. Orders and BPA calls are not recompetes: a monthly order under one vehicle repeats its text every month.
    lines = defaultdict(set)
    for e in entries:
        if e["signed"] and described(e, "contractActionType") in CONTRACT_ACTIONS and len(requirement_key(e["description"])) >= 12:
            lines[requirement_key(e["description"])].add(e["signed"])  # distinct days: same-day orders are one award event
    events = {key: award_events(days) for key, days in lines.items()}
    gaps = []
    for days in events.values():
        gaps += [round((date.fromisoformat(b) - date.fromisoformat(a)).days / 30.4) for a, b in zip(days, days[1:])]
    return {
        "awards": n, "first_signed": min(e["signed"] for e in entries if e["signed"]), "last_signed": max(e["signed"] for e in entries if e["signed"]),
        "value": {"stated": len(values), "median": round(statistics.median(values)) if values else None,
                  "p25": round(statistics.quantiles(values, n=4)[0]) if len(values) >= 2 else None,
                  "p75": round(statistics.quantiles(values, n=4)[2]) if len(values) >= 2 else None,
                  "bands": [{"band": b, "awards": bands[b], "share": round(bands[b] / len(values), 3)} for b, _ in BANDS if bands[b]]},
        "vehicle": {"under_an_idv": round(sum(1 for e in entries if e["idv"]) / n, 3),
                    "idv_types": shares(Counter(described(e, "referencedIDVType") for e in entries if e["idv"]), sum(1 for e in entries if e["idv"])),
                    "multiple_or_single": shares(Counter(described(e, "referencedIDVMultipleOrSingle") for e in entries if e["idv"]), sum(1 for e in entries if e["idv"])),
                    "action_types": shares(Counter(described(e, "contractActionType") for e in entries), n)},
        "competition": {"extent": shares(Counter(described(e, "extentCompeted") for e in entries), n),
                        "set_aside": shares(Counter(described(e, "typeOfSetAside") for e in entries), n),
                        "procedures": shares(Counter(described(e, "solicitationProcedures") for e in entries), n),
                        "offers": {"stated": len(offers), "median": statistics.median(offers) if offers else None}},
        "pricing": shares(Counter(described(e, "typeOfContractPricing") for e in entries), n),
        "naics": shares(Counter(f"{e['coded']['principalNAICSCode']['code']} {e['coded']['principalNAICSCode']['description']}".strip() for e in entries), n),
        "psc": shares(Counter(f"{e['coded']['productOrServiceCode']['code']} {e['coded']['productOrServiceCode']['description']}".strip() for e in entries), n),
        "duration_months": {"stated": len(durations), "median": statistics.median(durations) if durations else None},
        "vendors": {"distinct": len(by_vendor), "top": [{"vendor": v, "awards": c, "share": round(c / n, 3),
                                                          "value_share": round(value_by_vendor[v] / total_value, 3) if total_value else None} for v, c in top_vendors],
                    "top_share": round(top_vendors[0][1] / n, 3), "top3_share": round(sum(c for _, c in top_vendors) / n, 3),
                    "hhi": round(sum((c / n) ** 2 for c in by_vendor.values()), 3)},
        "recompete": {"lines_awarded_again": sum(1 for d in events.values() if len(d) >= RECOMPETE_MIN_AWARDS),
                      "median_gap_months": statistics.median(gaps) if gaps else None},
    }


def position(cell_events: list[dict], entries_by_piid: dict[str, dict], as_of: str) -> dict:
    """Incumbent position for one cell: who holds the work, until when, how long they have held it, whether
    the record shows the buyer renewing them rather than competing, and how many others hold a piece."""
    held = []
    for e in cell_events:
        m = PIID_RE.search(e["title"]) if e["family"] == "incumbent" else None
        if m:
            held.append({"piid": m.group(1), "ends": m.group(2), "vendor": e.get("vendor", ""), "seen": e["available_by"],
                         "signed": entries_by_piid.get(m.group(1), {}).get("signed", "")})
    if not held:
        return {"contracts": 0}
    by_vendor = Counter(h["vendor"] for h in held)
    lead, lead_n = by_vendor.most_common(1)[0]
    lead_rows = [h for h in held if h["vendor"] == lead]
    signed = [h["signed"] for h in lead_rows if h["signed"]]
    live = [h for h in held if h["ends"] > as_of]
    ending = sorted((h for h in live if h["ends"] <= shift(as_of, POSITION_HORIZON_DAYS)), key=lambda h: h["ends"])
    renewals = [e for e in cell_events if e["available_by"] > shift(as_of, -POSITION_HORIZON_DAYS) and e["available_by"] <= as_of
                and (e.get("polarity") or classify(e["event_type"], e.get("text", ""), bool(e.get("slip")))["polarity"]) == "negative"
                and (e.get("stage") or classify(e["event_type"])["stage"]) in ("execution", "solicitation")]
    if not live:
        reading = "no live contract in the record; the last one ended " + max(h["ends"] for h in held)
    elif renewals:
        reading = f"the buyer renewed or bridged rather than competed in the last two years ({len(renewals)} statement(s)); expect timing to move"
    elif ending:
        reading = f"{len(ending)} live contract(s) end within two years and nothing shows a renewal: a recompete environment"
    else:
        reading = "live contracts run past two years; no recompete in the horizon"
    return {"contracts": len(held), "live": len(live), "lead_vendor": lead, "lead_contracts": lead_n,
            "lead_since": min(signed) if signed else None, "lead_until": max(h["ends"] for h in lead_rows),
            "next_end": min((h["ends"] for h in live), default=None), "ending_within_two_years": [h["piid"] for h in ending][:10],
            "other_vendors": len(by_vendor) - 1, "renewals_in_two_years": [e["id"] for e in renewals][:5], "reading": reading}


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="buying_dna.py build")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    pulse = json.loads(PULSE.read_text(encoding="utf-8"))
    as_of = pulse["as_of"]
    entries = book()
    orgs = corpus["orgs"]
    # A program office's book: the base awards placed under it in the corpus; an award left at its contracting office stays out.
    office_piids: dict[str, set] = defaultdict(set)
    for e in corpus["events"]:
        m = PIID_RE.search(e["title"]) if e["family"] == "incumbent" else None
        if m and e["org"] and not wide(e["org"], orgs):
            office_piids[orgs[e["org"]].get("acronym") or orgs[e["org"]]["name"]].add(m.group(1))
    from pulse import cell_events, cells, load  # noqa: E402  (labels are needed only here)
    _, labels = load()
    top_keys = {c["key"] for c in pulse["ranking"]}
    cell_rows = []
    for cell in cells(corpus, labels):
        if cell["key"] not in top_keys:
            continue
        ev = [e for e in cell_events(cell, corpus) if e["available_by"] <= as_of]
        piids = {PIID_RE.search(e["title"]).group(1) for e in ev if e["family"] == "incumbent" and PIID_RE.search(e["title"])}
        held = [entries[p] for p in sorted(piids) if p in entries]
        cell_rows.append({"key": cell["key"], "office": cell["office"], "name": cell["name"][:120], "dna": dna(held),
                          "position": position(ev, entries, as_of)})
    payload = {"as_of": as_of, "base_awards_on_the_pages": len(entries),
               # The offices the profile sweeps, in the profile's order, then any other office the funding-agency sweep
               # found signing for the agency (none for the Navy, whose pages hold only its own offices).
               "contracting_offices": {office: dna([e for e in entries.values() if e["contracting_office"] == office])
                                       for office in [*OFFICES, *sorted({e["contracting_office"] for e in entries.values()} - set(OFFICES))]},
               "offices": {office: dna([entries[p] for p in sorted(piids) if p in entries]) for office, piids in sorted(office_piids.items())},
               "cells": sorted(cell_rows, key=lambda c: c["key"])}
    text = json.dumps(payload, indent=1, ensure_ascii=False) + "\n"
    print(table(payload))
    if args.check:
        if not DNA.exists() or DNA.read_text(encoding="utf-8") != text:
            print("buying DNA differs from the saved file", file=sys.stderr)
            return 1
        return 0
    DNA.write_text(text, encoding="utf-8")
    return 0


def read_out(name: str, d: dict) -> str:
    if not d.get("awards"):
        return f"{name}: " + (awards_not_collected() or "no base award in the book")
    v, c, ven = d["value"], d["competition"], d["vendors"]
    fmt = lambda rows: ", ".join(f"{r['value']} {r['share']:.0%}" for r in rows[:3]) or "-"
    return "\n".join([
        f"{name}: {d['awards']} base award(s) signed {d['first_signed']} to {d['last_signed']}",
        (f"  value: median ${v['median']:,}" + (f" (p25 ${v['p25']:,}, p75 ${v['p75']:,})" if v["p25"] is not None else "") + f" over {v['stated']} stated")
        if v["median"] is not None else "  value: not stated",
        f"  vehicle: {d['vehicle']['under_an_idv']:.0%} under an IDV; {fmt(d['vehicle']['idv_types'])}; {fmt(d['vehicle']['multiple_or_single'])}",
        f"  competition: {fmt(c['extent'])}; set-aside {fmt(c['set_aside'])}; offers median {c['offers']['median']} over {c['offers']['stated']}",
        f"  pricing: {fmt(d['pricing'])}",
        f"  NAICS: {fmt(d['naics'])}", f"  PSC: {fmt(d['psc'])}",
        f"  duration: median {d['duration_months']['median']} month(s); recompete gap median {d['recompete']['median_gap_months']} month(s) over {d['recompete']['lines_awarded_again']} line(s) awarded again",
        f"  vendors: {ven['distinct']} distinct; top {ven['top'][0]['vendor']} {ven['top_share']:.0%} of awards; top three {ven['top3_share']:.0%}; HHI {ven['hhi']}"])


def table(payload: dict) -> str:
    lines = [read_out(office, d) for office, d in payload["contracting_offices"].items()]
    lines.append(f"{len(payload['offices'])} program office book(s), {len(payload['cells'])} cell(s) with a position:")
    for c in payload["cells"][:8]:
        p = c["position"]
        lines.append(f"  {c['office'][:12]:12} {c['name'][:50]:50} " + (f"{p['lead_vendor'][:28]} holds {p['lead_contracts']}, next end {p['next_end']}; {p['reading'][:60]}" if p.get("contracts") else "no contract reached"))
    return "\n".join(lines)


def show(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="buying_dna.py show")
    ap.add_argument("name")
    args = ap.parse_args(argv)
    payload = json.loads(DNA.read_text(encoding="utf-8"))
    if args.name in payload["contracting_offices"]:
        print(read_out(args.name, payload["contracting_offices"][args.name]))
    elif args.name in payload["offices"]:
        print(read_out(args.name, payload["offices"][args.name]))
    else:
        cell = next((c for c in payload["cells"] if c["key"] == args.name), None)
        if cell is None:
            print(awards_not_collected() or f"no book named {args.name}", file=sys.stderr)
            return 2
        print(read_out(f"{cell['office']}: {cell['name']}", cell["dna"]))
        print("  position: " + json.dumps(cell["position"], ensure_ascii=False))
    return 0


def selfcheck() -> int:
    def entry(piid, signed, completion, vendor, value, idv="", parent="", description="TACNET WDL terminal radios"):
        coded = {name: {"code": "", "description": ""} for name in
                 ("typeOfSetAside", "extentCompeted", "numberOfOffersReceived", "principalNAICSCode", "productOrServiceCode", "typeOfContractPricing",
                  "totalBaseAndAllOptionsValue", "totalObligatedAmount", "referencedIDVType", "referencedIDVMultipleOrSingle", "solicitationProcedures",
                  "contractActionType", "ultimateParentUEI", "ultimateParentUEIName", "UEI")}
        coded["totalBaseAndAllOptionsValue"]["code"] = str(value)
        coded["extentCompeted"] = {"code": "A", "description": "FULL AND OPEN COMPETITION"}
        coded["contractActionType"] = {"code": "B", "description": "DEFINITIVE CONTRACT"}
        coded["ultimateParentUEIName"]["code"] = parent
        return {"piid": piid, "signed": signed, "completion": completion, "vendor": vendor, "idv": idv, "description": description, "coded": coded}
    rows = [entry("A1", "2020-01-01", "2022-01-01", "Acme", 900_000, idv="IDV1", parent="ACME PARENT"),
            entry("A1b", "2020-01-01", "2022-01-01", "Acme", 900_000, idv="IDV1", parent="ACME PARENT"),  # same day, same line: one event
            entry("A2", "2022-01-01", "2024-01-01", "Acme", 1_500_000, parent="ACME PARENT"),
            entry("B1", "2023-06-01", "2026-06-01", "Bolt", 20_000_000, description="shipboard antenna production")]
    d = dna(rows)
    assert d["awards"] == 4 and d["vendors"]["distinct"] == 2 and d["vendors"]["top"][0]["vendor"] == "ACME PARENT", d["vendors"]
    assert d["value"]["median"] == 1_200_000 and [b["band"] for b in d["value"]["bands"]] == ["$250k to $1M", "$1M to $10M", "$10M to $100M"]
    assert d["vehicle"]["under_an_idv"] == 0.5 and d["competition"]["extent"][0]["share"] == 1.0
    assert d["recompete"] == {"lines_awarded_again": 1, "median_gap_months": 24}, d["recompete"]
    orders = [dict(r, coded={**r["coded"], "contractActionType": {"code": "C", "description": "DELIVERY ORDER"}}) for r in rows]
    assert dna(orders)["recompete"] == {"lines_awarded_again": 0, "median_gap_months": None}, "orders under one vehicle are not recompetes"
    assert d["duration_months"]["median"] == 24
    assert dna([]) == {"awards": 0}
    assert "value: median $20,000,000 over 1 stated" in read_out("PMS 1", dna(rows[3:])), "one stated value has no quartiles"
    assert award_events({"2020-01-01", "2020-01-20", "2020-03-01", "2022-01-01"}) == ["2020-01-01", "2022-01-01"]  # a multiple award spread over weeks
    ev = [{"family": "incumbent", "title": "Incumbent contract A2 ends 2024-01-01", "vendor": "Acme", "available_by": "2022-04-01", "event_type": "contract_expires"},
          {"family": "incumbent", "title": "Incumbent contract B1 ends 2026-06-01", "vendor": "Bolt", "available_by": "2023-09-01", "event_type": "contract_expires"},
          {"family": "notice", "title": "J&A bridge", "available_by": "2025-01-01", "event_type": "justification_posted", "id": "j", "text": "bridge"}]
    by = {r["piid"]: r for r in rows}
    p = position(ev, by, "2025-06-01")
    assert p["lead_vendor"] == "Acme" and p["live"] == 1 and p["next_end"] == "2026-06-01" and p["renewals_in_two_years"] == ["j"], p
    assert "renewed or bridged" in p["reading"]
    calm = position(ev[:2], by, "2025-06-01")
    assert "recompete environment" in calm["reading"] and calm["ending_within_two_years"] == ["B1"], calm
    two = ev[:2] + [{"family": "incumbent", "title": "Incumbent contract C1 ends 2026-01-01", "vendor": "Cog", "available_by": "2024-01-01", "event_type": "contract_expires"}]
    assert position(two, by, "2025-06-01")["ending_within_two_years"] == ["C1", "B1"], "soonest end first"
    assert position([], by, "2025-06-01") == {"contracts": 0}
    print("buying_dna selfcheck ok")
    return 0


COMMANDS = {"build": build, "show": show}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--selfcheck":
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

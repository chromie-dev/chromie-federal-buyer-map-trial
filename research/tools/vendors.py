"""Vendors resolved by their Unique Entity Identifier from the saved FPDS award pages: one vendor per UEI, every spelling
the feed used for it, its parent entity, and its awards by office. The record names a vendor as the feed spelled it on the
day, so DATA LINK SOLUTIONS L.L.C. and DATA LINK SOLUTIONS LLC are one vendor here and two strings there.

    python research/tools/vendors.py build [--check]      -> research/memory/vendors.json
    python research/tools/vendors.py resolve "Data Link Solutions"
    python research/tools/vendors.py --selfcheck
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from backtest import RESEARCH, awards_not_collected  # noqa: E402
from buying_dna import book  # noqa: E402
from lrae_package import ROOT, fpds_entries, fpds_history, manifest_rows, url_index  # noqa: E402

OUT = RESEARCH / "memory" / "vendors.json"
CONTRACTS_SHOWN = 10


def modifications(entries: dict[str, dict]) -> list[dict]:
    """Every action the saved FPDS histories of the swept awards hold (fpds_sweep.py histories), coded like the awards."""
    index = url_index(manifest_rows())
    return [a for piid in entries for page in fpds_history(index, piid)[0]
            for a in fpds_entries((ROOT / page["path"]).read_bytes(), full=True) if a["piid"] == piid]


def current_completion(entries: dict[str, dict], actions: list[dict] = ()) -> dict[str, str]:
    """Each award's completion date as its newest action states it: an extension or a termination moves the base date."""
    ends = {piid: e["completion"] for piid, e in entries.items() if e["completion"]}
    for a in sorted(actions, key=lambda a: (a["signed"], a.get("mod") or "")):
        if a["completion"] and a["piid"] in entries:
            ends[a["piid"]] = a["completion"]
    return ends


def resolve_entries(entries: dict[str, dict], as_of: str, actions: list[dict] = ()) -> dict:
    """Group the awards by UEI; a spelling belongs to the UEI it was seen with most often. The parent is the one the
    newest record naming a parent states, a modification included: a vendor bought after its awards is named on them
    by its old parent."""
    parent: dict[str, dict] = {}
    for r in sorted([*entries.values(), *actions], key=lambda r: (r["signed"], r["piid"])):
        if r["coded"]["UEI"]["code"] and r["coded"]["ultimateParentUEI"]["code"]:
            parent[r["coded"]["UEI"]["code"]] = r
    by_uei: dict[str, list[dict]] = defaultdict(list)
    unresolved = Counter()
    for e in entries.values():
        uei = e["coded"]["UEI"]["code"]
        if uei:
            by_uei[uei].append(e)
        elif e["vendor"]:
            unresolved[e["vendor"]] += 1
    ends = current_completion(entries, actions)
    vendors = []
    for uei, rows in by_uei.items():
        names = Counter(r["vendor"] for r in rows if r["vendor"])
        p = parent.get(uei)
        parent_uei, parent_name = (p["coded"]["ultimateParentUEI"]["code"], p["coded"]["ultimateParentUEIName"]["code"]) if p else ("", "")
        signed = sorted(r["signed"] for r in rows if r["signed"])
        vendors.append({"uei": uei, "name": names.most_common(1)[0][0] if names else "", "spellings": sorted(names),
                        "parent_uei": parent_uei, "parent_name": parent_name, "parent_signed": p["signed"] if p else "", "awards": len(rows),
                        "first_signed": signed[0] if signed else "", "last_signed": signed[-1] if signed else "",
                        "live": sum(1 for r in rows if ends.get(r["piid"], "") > as_of),
                        "offices": dict(Counter(r["contracting_office"] for r in rows).most_common()),
                        "funding_offices": dict(Counter(r["funding_office"] for r in rows if r["funding_office"]).most_common(5))})
    vendors.sort(key=lambda v: (-v["awards"], v["uei"]))
    seen = Counter()
    for v in vendors:
        for s in v["spellings"]:
            seen[s] += 1
    spelling = {}
    for v in vendors:  # a spelling seen under two UEIs (a re-registration) points to the one with more awards, the first in the sort
        for s in v["spellings"]:
            spelling.setdefault(s, v["uei"])
    return {"as_of": as_of, "awards_read": len(entries), "vendors": vendors, "spelling_to_uei": spelling,
            "spellings_under_two_ueis": sorted(s for s, n in seen.items() if n > 1),
            "unresolved_spellings": dict(unresolved.most_common()),
            "summary": {"vendors": len(vendors), "with_two_or_more_spellings": sum(1 for v in vendors if len(v["spellings"]) > 1),
                        "with_a_parent": sum(1 for v in vendors if v["parent_uei"] and v["parent_uei"] != v["uei"]),
                        "awards_without_uei": sum(unresolved.values())}}


def load() -> dict:
    return json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}


def under(q: str, ueis: set[str], vendors: list[dict]) -> set[str]:
    """The UEIs whose parent a query names: a parent among `ueis`, or by the parent name FPDS gives on the awards."""
    return {v["uei"] for v in vendors if v.get("parent_uei") not in ("", None, v["uei"]) and (v["parent_uei"] in ueis or q in v["parent_name"].lower())}


def names_for(query: str, resolved: dict) -> list[str]:
    """Every spelling the feed used for the vendor a query names, and for the vendors under it as their parent, or the
    query itself when nothing resolves."""
    q = query.lower()
    ueis = {u for s, u in resolved.get("spelling_to_uei", {}).items() if q in s.lower()}
    ueis |= under(q, ueis, resolved.get("vendors", []))
    if not ueis:
        return [query]
    return sorted({s for v in resolved["vendors"] if v["uei"] in ueis for s in v["spellings"]})


def text(v: dict) -> str:
    lines = [f"{v['name']} (UEI {v['uei']}): {v['awards']} award(s) {v['first_signed']} to {v['last_signed']}, {v['live']} live",
             "spellings: " + "; ".join(v["spellings"]),
             "offices: " + ", ".join(f"{o} {n}" for o, n in v["offices"].items())]
    if v["funding_offices"]:
        lines.append("funding offices: " + ", ".join(f"{o} {n}" for o, n in v["funding_offices"].items()))
    if v["parent_uei"] and v["parent_uei"] != v["uei"]:
        # the name FPDS carried on the award; SAM.gov may have renamed that parent UEI since
        lines.append(f"parent: {v['parent_name']} (UEI {v['parent_uei']}), as FPDS recorded it on the awards to {v.get('parent_signed') or v['last_signed']}")
    if v.get("contracts"):
        shown = v["contracts"][:CONTRACTS_SHOWN]
        lines.append(f"contracts ({len(v['contracts'])}, newest first{', ' + str(len(shown)) + ' shown' if len(shown) < len(v['contracts']) else ''}): "
                     + "; ".join(f"{c['piid']} signed {c['signed']}, ends {c['completion'] or 'unstated'}, {c['contracting_office']}"
                                 + (f", under {c['idv']}" if c.get("idv") else "") for c in shown))
    return "\n".join(lines)


def build(argv: list[str]) -> int:
    check = "--check" in argv
    entries = book()
    as_of = max((e["signed"] for e in entries.values() if e["signed"]), default=date.today().isoformat())
    result = resolve_entries(entries, as_of, modifications(entries))
    s = result["summary"]
    print(f"{result['awards_read']} award(s) read; {s['vendors']} vendor(s) by UEI, {s['with_two_or_more_spellings']} spelled two or more ways, "
          f"{s['with_a_parent']} under a parent entity; {s['awards_without_uei']} award(s) name a vendor without a UEI; "
          f"{len(result['spellings_under_two_ueis'])} spelling(s) seen under two UEIs")
    if check:
        saved = load()
        if saved != result:
            print("vendors.json differs from a fresh build; run without --check to regenerate", file=sys.stderr)
            return 1
        print("vendors.json matches a fresh build")
        return 0
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"-> {OUT.relative_to(RESEARCH.parent)}")
    return 0


def resolve(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: vendors.py resolve NAME", file=sys.stderr)
        return 2
    resolved = load()
    q, vendors = argv[0].lower(), resolved.get("vendors", [])
    found = {v["uei"] for v in vendors if any(q in s.lower() for s in v["spellings"])}
    found |= under(q, found, vendors)
    hits = [v for v in vendors if v["uei"] in found]
    if not hits:
        print(awards_not_collected() or f"no vendor spelling contains {argv[0]!r}")
        return 1
    entries = book()  # vendors.json keeps counts, not contracts; the awards are read back from the saved pages
    shown = {piid: e for piid, e in entries.items() if e["coded"]["UEI"]["code"] in {v["uei"] for v in hits[:5]}}
    ends = current_completion(shown, modifications(shown))
    for v in hits[:5]:
        v["contracts"] = sorted(({**e, "completion": ends.get(e["piid"], e["completion"])} for e in shown.values() if e["coded"]["UEI"]["code"] == v["uei"]),
                                key=lambda e: (e["signed"], e["piid"]), reverse=True)
    print("\n\n".join(text(v) for v in hits[:5]))
    return 0


def selfcheck() -> int:
    c = lambda uei, puei="", pname="": {"UEI": {"code": uei, "description": ""}, "ultimateParentUEI": {"code": puei, "description": ""},
                                          "ultimateParentUEIName": {"code": pname, "description": ""}}
    row = lambda piid, vendor, uei, signed, completion, office="N00039", **kw: {"piid": piid, "vendor": vendor, "signed": signed, "completion": completion,
                                                                              "contracting_office": office, "funding_office": kw.get("funding", ""), "coded": c(uei, **kw.get("parent", {}))}
    entries = {"A": row("A", "ACME & CO", "U1", "2024-01-01", "2027-01-01", parent={"puei": "P1", "pname": "ACME HOLDINGS"}),
               "B": row("B", "ACME AND CO", "U1", "2025-01-01", "2025-12-31", funding="PMW 1"),
               "C": row("C", "BETA LLC", "U2", "2023-05-05", "2024-05-05"),
               "D": row("D", "GAMMA INC", "", "2023-05-05", "2024-05-05")}
    r = resolve_entries(entries, "2026-09-21")
    v = r["vendors"][0]
    assert v["uei"] == "U1" and v["spellings"] == ["ACME & CO", "ACME AND CO"] and v["awards"] == 2 and v["live"] == 1 and v["parent_name"] == "ACME HOLDINGS", v
    assert v["funding_offices"] == {"PMW 1": 1} and r["spelling_to_uei"]["ACME AND CO"] == "U1" and r["unresolved_spellings"] == {"GAMMA INC": 1}
    assert r["summary"] == {"vendors": 2, "with_two_or_more_spellings": 1, "with_a_parent": 1, "awards_without_uei": 1}, r["summary"]
    assert names_for("acme", r) == ["ACME & CO", "ACME AND CO"] and names_for("nobody", r) == ["nobody"]
    assert "parent: ACME HOLDINGS" in text(v)
    later = resolve_entries(entries, "2026-09-21", [row("A", "ACME & CO", "U1", "2025-06-01", "2027-01-01", parent={"puei": "P9", "pname": "NEWCO INC"})])
    assert later["vendors"][0]["parent_name"] == "NEWCO INC" and names_for("newco", later) == ["ACME & CO", "ACME AND CO"], later["vendors"][0]
    extended = resolve_entries(entries, "2026-09-21", [row("C", "BETA LLC", "U2", "2024-03-01", "2029-06-29")])
    assert next(x for x in extended["vendors"] if x["uei"] == "U2")["live"] == 1, "an extension signed after the base award keeps it live"
    assert current_completion(entries, [row("A", "ACME & CO", "U1", "2025-02-01", "2025-06-30")])["A"] == "2025-06-30", "a later action that shortens it wins too"
    print("vendors selfcheck ok")
    return 0


COMMANDS = {"build": build, "resolve": resolve}


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selfcheck"]:
        return selfcheck()
    if not argv or argv[0] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

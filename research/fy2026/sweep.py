#!/usr/bin/env python3
"""FY2026 lifecycle sweep: collection stages. Every request is recorded in the manifest; nothing tracked is
rewritten (a notice whose saved copy predates its latest modification is saved again under data/raw/fy2026/).

    .venv/bin/python research/fy2026/sweep.py sam-org        # every notice NAVWAR HQ (and the NIWCs) modified in FY2026
    .venv/bin/python research/fy2026/sweep.py fpds-months    # NAVWAR HQ FPDS actions, July to September 2026
    .venv/bin/python research/fy2026/sweep.py usaspending    # every incumbent contract the forecast names: award + FY2026 actions
    .venv/bin/python research/fy2026/sweep.py fpds-sol       # FPDS by solicitation number for FY2026 NAVWAR notices

State written: research/fy2026/collect/*.json (listings and indexes for the events stage).
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "research" / "tools"))
import recorded  # noqa: E402
import sam_notices  # noqa: E402

COLLECT = HERE / "collect"
NOTICES = ROOT / "data" / "raw" / "sam_notices"
AS_OF = "2026-09-22"
FY_START = "2025-10-01"

SGS = "https://sam.gov/api/prod/sgs/v1/search/"
OPPS = "https://sam.gov/api/prod/opps"
ORGS = {"100076586": ("N00039", "NAVWAR HQ"), "100076487": ("N66001", "NIWC Pacific"), "100076484": ("N65236", "NIWC Atlantic")}
DETAIL_ORGS = {"100076586"}  # details and attachments harvested; the NIWCs are listed only


def sam_org_listing() -> None:
    COLLECT.mkdir(exist_ok=True)
    hits: dict[str, dict] = {}
    for org_id, (code, name) in ORGS.items():
        for active in ("true", "false"):
            page, total = 0, None
            while True:
                params = {"index": "opp", "page": page, "size": 100, "mode": "search", "is_active": active, "organization_id": org_id,
                          "sort": "-modifiedDate", "modified_date.from": f"{FY_START}-04:00", "modified_date.to": f"{AS_OF}-04:00"}
                url = SGS + "?" + urllib.parse.urlencode(params)
                row = recorded.get(url, f"SAM listing {name} ({code}) active={active} modified {FY_START} to {AS_OF}, page {page}; FY2026 sweep")
                j = recorded.json_of(row)
                res = (j.get("_embedded") or {}).get("results") or []
                total = (j.get("page") or {}).get("totalElements")
                for h in res:
                    hier = h.get("organizationHierarchy") or []
                    kind = h.get("type") or {}
                    hits[h["_id"]] = {"id": h["_id"], "org_id": org_id, "org": name, "title": h.get("title") or "",
                                      "type": kind.get("value") if isinstance(kind, dict) else kind,
                                      "posted": str(h.get("publishDate") or "")[:10], "modified": str(h.get("modifiedDate") or "")[:10],
                                      "active": h.get("isActive"), "cancelled": bool(h.get("isCanceled")),
                                      "solicitation": h.get("solicitationNumber") or "", "response": str(h.get("responseDate") or "")[:10],
                                      "posting_office": (hier[-1].get("name") if hier else ""), "query_url": url}
                print(f"{name} active={active} page {page}: {len(res)} of {total}")
                if not res or (page + 1) * 100 >= (total or 0):
                    break
                page += 1
    (COLLECT / "sam_org_listing.json").write_text(json.dumps({"as_of": AS_OF, "modified_from": FY_START, "orgs": ORGS, "hits": hits}, indent=1))
    print("listed", len(hits))
    # details for the NAVWAR HQ notices
    harvested, refreshed, skipped = 0, 0, 0
    for h in sorted(hits.values(), key=lambda x: x["posted"]):
        if h["org_id"] not in DETAIL_ORGS:
            continue
        saved = NOTICES / f"{h['id']}.json"
        if saved.exists():
            try:
                d = json.loads(saved.read_text(encoding="utf-8", errors="replace"))
                saved_mod = str(d.get("modifiedDate") or "")[:10]
            except ValueError:
                saved_mod = ""
            if saved_mod and h["modified"] and h["modified"] > saved_mod:
                url = f"{OPPS}/v2/opportunities/{h['id']}?api_key=null"
                recorded.get(url, f"SAM notice detail refreshed (saved copy {saved_mod}, modified {h['modified']}) {h['solicitation'] or h['id']}: {h['title'][:70]}; FY2026 sweep")
                refreshed += 1
            else:
                skipped += 1
            continue
        try:
            sam_notices.harvest_notice(h["id"], f"FY2026 sweep {h['solicitation'] or h['id']}")
            harvested += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  detail error {h['id']}: {exc}")
            sam_notices.record(f"{OPPS}/v2/opportunities/{h['id']}?api_key=null", None, None, f"SAM notice detail FY2026 sweep {h['id']}", None, error=str(exc)[:120])
        time.sleep(0.6)
    print(f"details: harvested {harvested}, refreshed {refreshed}, already saved {skipped}")


def fpds_actions(body: str) -> list[dict]:
    import trace as tr  # noqa: E402  (research/tools/trace.py, read-only helpers)
    return tr.fpds_actions(body)


def fpds_window(office: str, start: str, end: str, note: str) -> list[dict]:
    """Page an FPDS ATOM office scan until a page holds fewer than ten entries."""
    out, s = [], 0
    while True:
        q = f"CONTRACTING_OFFICE_ID:{office}+SIGNED_DATE:%5B{start},{end}%5D"
        url = f"https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q={q}&start={s}"
        row = recorded.get(url, note)
        body = recorded.body_of(row)
        acts = fpds_actions(body.decode("utf-8", "replace"))
        out += acts
        print(f"  {office} {start}-{end} start={s}: {len(acts)}")
        if len(acts) < 10:
            break
        s += 10
    return out


def fpds_months() -> None:
    COLLECT.mkdir(exist_ok=True)
    windows = [("2026/07/01", "2026/07/31"), ("2026/08/01", "2026/08/31"), ("2026/09/01", "2026/09/22")]
    result = {}
    for start, end in windows:
        acts = fpds_window("N00039", start, end, f"FPDS NAVWAR HQ office scan {start} to {end}, re-run on {AS_OF} (DoD publication lag); FY2026 sweep")
        result[f"{start}-{end}"] = acts
    (COLLECT / "fpds_months.json").write_text(json.dumps({"as_of": AS_OF, "windows": result}, indent=1))
    print({k: len(v) for k, v in result.items()})


def incumbent_piids() -> list[str]:
    piids = set()
    with (HERE / "candidate_universe.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            s = r["existing_contract_number"].upper()
            for m in re.findall(r"[A-Z]{1,2}\d{4,5}[-\s]?\d{2}[-\s]?[A-Z][-\s]?\d{4}|[A-Z]\d{5}\d{2}[A-Z]\d{4}|N652362290001", s):
                piids.add(re.sub(r"[-\s]", "", m))
    return sorted(piids)


def usaspending() -> None:
    COLLECT.mkdir(exist_ok=True)
    out: dict[str, dict] = {}
    for piid in incumbent_piids():
        entry: dict = {"piid": piid, "lookup": None, "award": None, "transactions": [], "fy2026_transactions": []}
        found = None
        for codes in (["A", "B", "C", "D"], ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]):
            row = recorded.post_json("https://api.usaspending.gov/api/v2/search/spending_by_award/",
                                     {"filters": {"award_ids": [piid], "award_type_codes": codes},
                                      "fields": ["Award ID", "generated_internal_id", "Recipient Name", "Start Date", "End Date", "Award Amount",
                                                 "Last Modified Date", "Awarding Sub Agency", "Base Obligation Date", "Description"],
                                      "limit": 10, "page": 1},
                                     f"USAspending award lookup by PIID {piid} ({'contract' if codes[0] == 'A' else 'IDV'} types); FY2026 sweep")
            j = recorded.json_of(row)
            res = [x for x in (j.get("results") or []) if str(x.get("Award ID", "")).replace("-", "") == piid]
            if res:
                found = res[0]
                break
        entry["lookup"] = found
        if found:
            gid = found["generated_internal_id"]
            det = recorded.get(f"https://api.usaspending.gov/api/v2/awards/{gid}/", f"USAspending award detail {piid}; FY2026 sweep")
            entry["award"] = recorded.json_of(det)
            page = 1
            while page <= 6:
                t = recorded.post_json("https://api.usaspending.gov/api/v2/transactions/", {"award_id": gid, "limit": 100, "page": page, "sort": "action_date", "order": "desc"},
                                       f"USAspending transactions {piid} page {page}; FY2026 sweep")
                tj = recorded.json_of(t)
                res = tj.get("results") or []
                entry["transactions"] += res
                if not (tj.get("page_metadata") or {}).get("hasNext") or not res:
                    break
                if res[-1].get("action_date", "") < FY_START:
                    break
                page += 1
            entry["fy2026_transactions"] = [x for x in entry["transactions"] if FY_START <= str(x.get("action_date", "")) <= AS_OF]
        out[piid] = entry
        print(piid, "found" if found else "not found", len(entry["fy2026_transactions"]), "FY2026 actions")
    (COLLECT / "usaspending_incumbents.json").write_text(json.dumps({"as_of": AS_OF, "incumbents": out}, indent=1))


def fpds_sol() -> None:
    """FPDS by solicitation number for every FY2026 NAVWAR HQ notice that carries one (awards lag about 90 days)."""
    listing = json.loads((COLLECT / "sam_org_listing.json").read_text())
    sols = sorted({h["solicitation"] for h in listing["hits"].values() if h["org_id"] == "100076586" and h["solicitation"] and h["posted"] >= FY_START
                   and h["type"] in ("Presolicitation", "Solicitation", "Combined Synopsis/Solicitation", "Award Notice", "Justification")})
    out = {}
    for sol in sols:
        compact = re.sub(r"[\s-]", "", sol.upper())
        url = f"https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=SOLICITATION_ID:{compact}&start=0"
        row = recorded.get(url, f"FPDS by solicitation {sol}, FY2026 NAVWAR notice; FY2026 sweep")
        body = recorded.body_of(row)
        out[sol] = fpds_actions(body.decode("utf-8", "replace"))
        print(sol, len(out[sol]))
    (COLLECT / "fpds_by_solicitation.json").write_text(json.dumps({"as_of": AS_OF, "solicitations": out}, indent=1))


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    {"sam-org": sam_org_listing, "fpds-months": fpds_months, "usaspending": usaspending, "fpds-sol": fpds_sol}[cmd]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

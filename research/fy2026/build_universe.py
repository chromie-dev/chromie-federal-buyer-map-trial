#!/usr/bin/env python3
"""Build the FY2026 candidate universe from the committed datapack, the latest status transcript and the
loaded database. Writes only into research/fy2026/. Reads everything else.

    .venv/bin/python research/fy2026/build_universe.py [--dsn DSN]

Three sets, one row each:
  forecast_2025   every included line of lrae_navwar_2025-06 (148)
  dropped_2024    every included line of lrae_navwar_2024-06 with no row in lrae_navwar_2025-06 (120)
  notice_created  every need the loader created from a SAM.gov notice (read from the database)

Windows are read from the forecast as fiscal quarters and turned into calendar end dates so that a line can be
placed against the research date without reading a future quarter as past. Nothing here is an event: the
`existing_reading` column is what trace.py printed on 2026-09-21 and is carried for comparison only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research" / "fy2026"
PACK25 = ROOT / "datapack" / "lrae_navwar_2025-06"
PACK24 = ROOT / "datapack" / "lrae_navwar_2024-06"
STATUS = ROOT / "research" / "transcripts" / "2026-09-21c" / "status_fy26.txt"
AS_OF = date(2026, 9, 22)
FY_END = date(2026, 9, 30)

QUARTER_END = {"Q1": (12, 31, -1), "Q2": (3, 31, 0), "Q3": (6, 30, 0), "Q4": (9, 30, 0)}


def fiscal_year(s: str) -> int | None:
    m = re.search(r"(\d{2,4})", s or "")
    if not m:
        return None
    v = int(m.group(1))
    return v + 2000 if v < 100 else v


def window_end(fy: str, q: str) -> date | None:
    y = fiscal_year(fy)
    if y is None:
        return None
    q = (q or "").strip().upper()
    if q not in QUARTER_END:
        return date(y, 9, 30)  # a year with no quarter closes with the fiscal year
    month, day, shift = QUARTER_END[q]
    return date(y + shift, month, day)


def time_class(end: date | None) -> str:
    if end is None:
        return "undated"
    if end <= AS_OF:
        return "window_closed_by_2026-09-22"
    if end <= FY_END:
        return "expected_by_2026-09-30"
    return "FY27_or_later"


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def status_readings() -> dict[str, str]:
    """PID -> first outcome word of trace.py status on 2026-09-21."""
    out: dict[str, str] = {}
    if not STATUS.exists():
        return out
    for line in STATUS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| N00039") and not line.startswith("| row:"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 10:
            continue
        reading = cells[-1].split(":")[0].strip()
        out[cells[0]] = reading
    return out


ORDER_RE = re.compile(r"(?i)\bTO\b|task order|\border\b|\bmod\b|modification|BPA|\bcall\b|RFP #|delivery order|"
                      r"option|extension|ceiling|incremental|de-obligation|award and fund|spares #")


def tiers_for(sol_fy, award_fy, title, incumbent) -> list[str]:
    t = []
    sfy, afy = fiscal_year(sol_fy), fiscal_year(award_fy)
    if afy == 2026:
        t.append("A:award_FY26")
    if sfy == 2026:
        t.append("S:sol_FY26")
    if sfy is None:
        t.append("U:undated_sol")
    if afy is not None and afy >= 2027:
        t.append("F:award_FY27+")
    if sfy is not None and sfy <= 2025:
        t.append("P:sol_FY25_or_earlier")
    if incumbent.strip():
        t.append("I:incumbent_named")
    if ORDER_RE.search(title or ""):
        t.append("O:order_or_modification")
    return t


def priority(tiers: list[str], value: str, reading: str) -> int:
    """1 = swept first. Explicit rule, recorded in 00_baseline.md."""
    small = value.strip() in ("< $2M", "$2M - $7.5M", "No Range Specified", "")
    if reading in ("awarded", "review", "restructured", "open") or "candidate" in reading:
        return 1
    if ("A:award_FY26" in tiers or "S:sol_FY26" in tiers) and "O:order_or_modification" not in tiers:
        return 1
    if "I:incumbent_named" in tiers and "F:award_FY27+" not in tiers:
        return 1
    if "A:award_FY26" in tiers or "S:sol_FY26" in tiers:
        return 2
    if "U:undated_sol" in tiers and not small:
        return 2
    if "P:sol_FY25_or_earlier" in tiers and not small:
        return 2
    return 3


def forecast_rows() -> list[dict]:
    raw = {(r["sheet"], r["row_number"]): r for r in read_csv(PACK25 / "rows_raw.csv")}
    readings = status_readings()
    rows = []
    for c in read_csv(PACK25 / "rows_classified.csv"):
        if c["include_decision"] != "included":
            continue
        r = raw[(c["sheet"], c["row_number"])]
        sol_end = window_end(r["solicitation_fy"], r["solicitation_quarter"])
        award_end = window_end(r["award_fy"], r["award_quarter"])
        tiers = tiers_for(r["solicitation_fy"], r["award_fy"], r["requirement_title"], r["existing_contract_number"])
        reading = readings.get(c["record_key"], "not_in_status_table")
        rows.append({
            "set": "forecast_2025", "record_key": c["record_key"], "release": "lrae_navwar_2025-06",
            "sheet_row": f"{c['sheet']}!{c['row_number']}", "title": r["requirement_title"],
            "office_code": c["office_code"], "office_id": c["office_id"],
            "sol_fy": r["solicitation_fy"], "sol_q": r["solicitation_quarter"], "sol_window_end": sol_end.isoformat() if sol_end else "",
            "award_fy": r["award_fy"], "award_q": r["award_quarter"], "award_window_end": award_end.isoformat() if award_end else "",
            "time_class_sol": time_class(sol_end), "time_class_award": time_class(award_end),
            "value_as_stated": r["anticipated_total_value"], "procurement_method": r["procurement_method"],
            "instrument": r["procurement_instrument"], "follow_on_or_new": r["follow_on_or_new"],
            "existing_contract_number": r["existing_contract_number"], "incumbent_contractor": r["incumbent_contractor"],
            "tiers": ";".join(tiers), "existing_reading_2026-09-21": reading,
            "priority": priority(tiers, r["anticipated_total_value"], reading),
        })
    return rows


def dropped_rows() -> list[dict]:
    raw24 = {r["row_number"]: r for r in read_csv(PACK24 / "rows_raw.csv")}
    cls24 = {c["row_number"]: c for c in read_csv(PACK24 / "rows_classified.csv")}
    diff = read_csv(PACK25 / "diff_lrae_navwar_2024-06_lrae_navwar_2025-06.csv")
    rows = []
    for d in diff:
        if d["change"] != "removed":
            continue
        c = cls24.get(d["old_row"])
        if not c or c["include_decision"] != "included":
            continue
        r = raw24[d["old_row"]]
        sol_end = window_end(r["solicitation_fy"], r["solicitation_quarter"])
        award_end = window_end(r["award_fy"], r["award_quarter"])
        tiers = tiers_for(r["solicitation_fy"], r["award_fy"], r["requirement_title"], r["existing_contract_number"]) + ["D:dropped_from_2025_release"]
        rows.append({
            "set": "dropped_2024", "record_key": c["record_key"], "release": "lrae_navwar_2024-06",
            "sheet_row": f"{c['sheet']}!{c['row_number']}", "title": r["requirement_title"],
            "office_code": c["office_code"], "office_id": c["office_id"],
            "sol_fy": r["solicitation_fy"], "sol_q": r["solicitation_quarter"], "sol_window_end": sol_end.isoformat() if sol_end else "",
            "award_fy": r["award_fy"], "award_q": r["award_quarter"], "award_window_end": award_end.isoformat() if award_end else "",
            "time_class_sol": time_class(sol_end), "time_class_award": time_class(award_end),
            "value_as_stated": r["anticipated_total_value"], "procurement_method": r["procurement_method"],
            "instrument": r["procurement_instrument"], "follow_on_or_new": r["follow_on_or_new"],
            "existing_contract_number": r["existing_contract_number"], "incumbent_contractor": r["incumbent_contractor"],
            "tiers": ";".join(tiers), "existing_reading_2026-09-21": "review (missing from latest release)",
            "priority": priority(tiers, r["anticipated_total_value"], ""),
        })
    return rows


def notice_rows(dsn: str) -> list[dict]:
    """Every need the loader created from a notice, read through psql as the other tools read the database.

    This set is not derivable from the datapack, so a database that cannot be reached, or one that holds no
    notice-created needs, is an error rather than an empty set: a silently short universe reads as a finding.
    """
    sql = """select coalesce(json_agg(row_to_json(t)), '[]'::json) from (
        select n.source_key, n.title, n.lifecycle,
               coalesce(string_agg(distinct o.name || ' [' || no.role || ']', '; '), '') as orgs
        from gov_needs n
        left join gov_need_organizations no on no.need_id = n.id
        left join gov_organizations o on o.id = no.organization_id
        where n.source_key like 'notice:%'
        group by n.id, n.source_key, n.title, n.lifecycle
        order by n.source_key) t"""
    out = subprocess.run(["psql", dsn, "-At", "-c", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}\n"
                         "the notice-created set is read from the loaded database; build it with "
                         "research/tools/pipeline.py --db <name> and pass --dsn")
    records = json.loads(out.stdout.strip() or "[]")
    if not records:
        raise SystemExit("the database holds no needs keyed notice:*; load it with research/tools/pipeline.py "
                         "before building the universe")
    rows = []
    for rec in records:
        key, title, lifecycle, orgs = rec["source_key"], rec["title"], rec["lifecycle"], rec["orgs"]
        rows.append({
            "set": "notice_created", "record_key": key, "release": "", "sheet_row": "", "title": title,
            "office_code": "", "office_id": orgs, "sol_fy": "", "sol_q": "", "sol_window_end": "",
            "award_fy": "", "award_q": "", "award_window_end": "", "time_class_sol": "n/a", "time_class_award": "n/a",
            "value_as_stated": "", "procurement_method": "", "instrument": "", "follow_on_or_new": "",
            "existing_contract_number": "", "incumbent_contractor": "", "tiers": f"N:notice_created;L:{lifecycle}",
            "existing_reading_2026-09-21": f"lifecycle {lifecycle}",
            "priority": 1 if re.search(r"(?i)2026|_26|26R|26D", key) else 2,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://postgres:postgres@127.0.0.1:54322/navy_proof_ab")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    rows = forecast_rows() + dropped_rows() + notice_rows(args.dsn)
    fields = list(rows[0].keys())
    with (OUT / "candidate_universe.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    summary: dict = {"as_of": AS_OF.isoformat(), "rows": len(rows), "by_set": {}, "by_priority": {}, "forecast_2025": {}}
    for r in rows:
        summary["by_set"][r["set"]] = summary["by_set"].get(r["set"], 0) + 1
        k = f"{r['set']}:P{r['priority']}"
        summary["by_priority"][k] = summary["by_priority"].get(k, 0) + 1
    f25 = [r for r in rows if r["set"] == "forecast_2025"]
    for col in ("time_class_sol", "time_class_award", "existing_reading_2026-09-21", "office_code"):
        agg: dict = {}
        for r in f25:
            agg[r[col]] = agg.get(r[col], 0) + 1
        summary["forecast_2025"][col] = dict(sorted(agg.items(), key=lambda kv: -kv[1]))
    tier_counts: dict = {}
    for r in f25:
        for t in r["tiers"].split(";"):
            tier_counts[t] = tier_counts.get(t, 0) + 1
    summary["forecast_2025"]["tiers"] = tier_counts
    (OUT / "universe_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

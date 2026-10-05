#!/usr/bin/env python3
"""Turn the saved NAVWAR LRAE releases into reproducible data packages.

    python research/tools/lrae_package.py build              # regenerate every package from saved bytes only
    python research/tools/lrae_package.py collect [--limit N] [--release KEY]  # fetch the FPDS, SAM.gov and USAspending lookups the joins need

`build` reads the spreadsheet bytes recorded in research/sources/documents_manifest.jsonl plus the saved
FPDS ATOM and SAM.gov search responses, and writes one datapack/lrae_<activity>_<release>/ per saved
release, plus a diff between consecutive releases in the newer package. It never touches the
network, so a reviewer with the same data/raw/ gets byte-identical files. `collect` performs the
lookups that are not yet in the manifest (one FPDS PIID search per contract token, one SAM.gov
search per PID and per token, the rest of each contract's FPDS history, and its USAspending award page)
through fetch.py so every request is recorded; by default for the newest release of every activity.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import sys
import tempfile
from functools import cache
import time
import urllib.parse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import P, RESEARCH, forecast_packs  # noqa: E402
from fetch import MANIFEST, ROOT, fetch, kept_page  # noqa: E402

SHEET = "LRAE Annex 25"
HEADER_ROW = 8  # Excel row number of the column headers in every release seen so far
PACK_BASE = Path(os.environ.get("LRAE_PACK_DIR") or ROOT / "datapack")  # override lets the test rebuild elsewhere
# Oldest first within an activity. `match` identifies the manifest row; `release_date` is the fallback when the
# sheet gives none; `scope` is which rows load: PEO C4I's offices only (the NAVWAR trial) or the whole activity.
# Releases are diffed and chained within one activity; a NAVSEA line is never a NAVWAR line's revision.
# The Department's combined forecast carries every activity as its own sheet, each with its own release date.
COMBINED = "the Department of the Navy's combined forecast, one sheet per activity; each sheet is its own release"
NAVY_RELEASES = [
    {"key": "lrae_navwar_2023-06", "activity": "navwar", "match": "NAVWAR_LRAE_Report.xlsx", "release_date": "2023-06-20",
     "release_note": "sheet says '20 June 2023 / TDB'; the report was exported 2023-05-25 (Filters sheet)", "sheet": "LRAE Annex 25", "header_row": 8, "scope": "peo_c4i"},
    {"key": "lrae_navwar_2024-06", "activity": "navwar", "match": "HQCA-2024-A-094", "release_date": "2024-06-20",
     "release_note": '', "sheet": "LRAE Annex 25", "header_row": 8, "scope": "peo_c4i"},
    {"key": "lrae_navwar_2025-06", "activity": "navwar", "match": "HQCA-2025-A-037", "release_date": "2025-06-19",
     "release_note": '', "sheet": "LRAE Annex 25", "header_row": 8, "scope": "peo_c4i"},
    {"key": "lrae_navwar_2026-07", "activity": "navwar", "match": "DON_Combined_LRAE_Report_20260713", "release_date": "2026-07-13",
     "release_note": COMBINED, "sheet": "NAVWAR", "header_row": 8, "scope": "peo_c4i"},
    {"key": "lrae_navsea_2025-12", "activity": "navsea", "match": "LRAE-NAVSEA_Enterprise_LRAE_18DECEMBER2025", "release_date": "2025-12-18",
     "release_note": '', "sheet": "Annex 25 Template", "header_row": 8, "scope": "all"},
    {"key": "lrae_navsea_2026-07", "activity": "navsea", "match": "DON_Combined_LRAE_Report_20260713", "release_date": "2026-07-13",
     "release_note": COMBINED, "sheet": "NAVSEA", "header_row": 8, "scope": "all"},
    {"key": "lrae_onr_2025-12", "activity": "onr", "match": "onr-and-nrl-long-range-acquisition-estimate", "release_date": "2025-12-19",
     "release_note": 'one workbook carries ONR and NRL as two sheets; each sheet is its own release', "sheet": "ONR", "header_row": 7, "scope": "all"},
    {"key": "lrae_nrl_2025-12", "activity": "nrl", "match": "onr-and-nrl-long-range-acquisition-estimate", "release_date": "2025-12-19",
     "release_note": 'one workbook carries ONR and NRL as two sheets; each sheet is its own release', "sheet": "NRL", "header_row": 7, "scope": "all"},
    {"key": "lrae_onr_2026-07", "activity": "onr", "match": "DON_Combined_LRAE_Report_20260713", "release_date": "2026-07-13",
     "release_note": COMBINED, "sheet": "ONR", "header_row": 8, "scope": "all",
     "carries": ("nrl",)},  # the combined report prints NRL's rows on the ONR sheet
]
# The Army Materiel Command forecast: one sheet, dates instead of fiscal quarters, and the owning office as a
# Command and a PM / Directorate column, which the office string joins as "Command - PM / Directorate".
AMC_HEADERS = {
    "pan, solicitation, or contract number": "number", "if follow-on, provide current contract number": "existing_contract_number",
    "description of requirement": "requirement_title", "consolidation anticipated": "consolidation", "bundling anticipated": "bundling",
    "forecasted contract value": "anticipated_total_value", "forecasted psc": "psc", "anticipated naics": "naics",
    "anticipated type of set aside": "procurement_method", "procact": "procurement_instrument", "indefinite delivery vehicle type": "idv_type",
    "palt": "palt_code", "anticipated contract type": "contract_type", "anticipated solicitation date": "solicitation_date",
    "anticipated solicitation closing date": "solicitation_close_date", "forecasted award date": "award_date",
    "period of performance": "period_of_performance_months", "contracting center": "contracting_center",
    "contracting office": "contracting_office", "command": "command", "pm / directorate": "pm_directorate",
    "assigned small business office email": "small_business_email", "assigned small business office": "small_business_office",
}
# The Corps of Engineers forecast: one sheet, the fiscal quarter as "FY2026, Q4", a value band, and the district or
# centre that buys (Buying / Requiring Activity) as the owning office.
USACE_HEADERS = {
    "project title": "requirement_title", "prior contract number": "existing_contract_number", "prior order number": "prior_order_number",
    "anticipated dollar value": "anticipated_total_value", "contract period of performance": "period_of_performance_months",
    "naics / psc": "naics_psc", "anticipated set-aside": "procurement_method", "action / award type": "procurement_instrument",
    "anticipated solicitation date": "solicitation_date", "anticipated award date": "award_date",
    "buying / requiring activity": "command", "small business office": "small_business_email",
}
# The National Guard forecast: one sheet per state Guard, the fiscal year and the quarter in two columns, the contracting
# office as a name or a DoDAAC. The sheet and the Requiring Activity column name the owning office.
NG_HEADERS = {
    "project description": "requirement_description", "title for the potential": "requirement_title",
    "existing contract number": "existing_contract_number", "solicitation number": "number", "incumbent vendor name": "incumbent_contractor",
    "contracting agency": "contracting_center", "contracting office/org": "contracting_org", "contracting office": "contracting_office",
    "consolidation / bundling": "consolidation", "estimated dollar threshold": "dollar_threshold",
    "total estimated contract or order value": "anticipated_total_value", "period of performance": "period_of_performance_months",
    "naics": "naics", "psc": "psc", "forecasted type of set aside": "procurement_method", "anticipated contract type": "contract_type",
    "anticipated award type": "procurement_instrument", "contract vehicle": "contract_vehicle", "extent competed": "extent_competed",
    "predominant place(s) of performance - state": "place_of_performance",
    "anticipated solicitation date fy": "solicitation_date", "anticipated solicitation date qt": "solicitation_quarter_as_written",
    "anticipated award date fy": "award_date", "anticipated award date qt": "award_quarter_as_written",
    "requiring activity": "pm_directorate", "point of contact": "contracting_poc_name", "sb office": "small_business_office",
    "contact email": "small_business_email",
}
# `sheet: "*"` reads every sheet but the `key_sheets`, and a row is then its sheet and its row ("TX!5").
ARMY_RELEASES = [
    {"key": "amc_2026-05", "activity": "amc", "match": "enclosure-1-fy26-amc-acquisition-forecast", "release_date": "2026-05-27",
     "release_note": "the June to December 2026 forecast, dated by its download path", "sheet": "FY26 AMC_2nd Iteration", "header_row": 2,
     "scope": "all", "headers": AMC_HEADERS},
    {"key": "usace_2026-05", "activity": "usace", "match": "usace-forecast-da-format-external", "release_date": "2026-05-28",
     "release_note": "dated by its download path; the file is named for 27 May", "sheet": "USACE DA Forecast", "header_row": 1,
     "scope": "all", "headers": USACE_HEADERS},
    {"key": "ngb_2026-05", "activity": "ngb", "match": "fy26-acquisition-forecast-master", "release_date": "2026-05-07",
     "release_note": "the FY2026 National Guard forecast, one sheet per state Guard, dated by its download path", "sheet": "*",
     "key_sheets": ("Acronym Key", "Column Header Key"), "header_row": 2, "scope": "all", "headers": NG_HEADERS, "command_from": "sheet"},
]
RELEASES = {"navy": NAVY_RELEASES, "army": ARMY_RELEASES}.get(P["key"]) or P["forecast"].get("releases", [])
JOINS_COLLECTED_FOR = "lrae_navwar_2025-06"  # the first release whose FPDS and SAM.gov lookups were collected
USASPENDING_AWARD = "https://api.usaspending.gov/api/v2/awards/{}/"
FPDS = "https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:{piid}&start={start}"
FPDS_PAGE = 10  # actions a page; the feed runs oldest first and names the next page with a rel="next" link
SGS = "https://sam.gov/api/prod/sgs/v1/search/?"
PIID_RE = re.compile("|".join(filter(None, (r"N\d{5}\d{2}[A-Z]\d{4,5}(?!\d)|NNG\d{2}S[A-Z]\d{2}B|GS-?\d{2}F-?\d{3,4}[A-Z]{1,2}",
                                            P["forecast"].get("contract_re")))))
FORECAST_PID_RE = re.compile(r"[A-Z0-9]{6}-\d{2}-RFPREQ-[A-Za-z0-9/\-]+?-\d{4}")
INCLUDED_PARENT = "peo:c4i"
# Division and competency codes resolve to the organization that owns them, which is enough to exclude them.
FAMILY_PARENT = {"niwc_atlantic_division": "center:niwc-atlantic", "niwc_pacific_competency": "center:niwc-pacific",
                 "navwar_hq_competency": "command:navwar"}
# Header text (first line, lower-cased prefix) -> field name. Releases differ in column count and order.
HEADERS = {
    "requirement title": "requirement_title", "requirement description": "requirement_description",
    "associated program or requirement office": "office_code_string", "anticipated total value": "anticipated_total_value",
    "anticipated procurement method": "procurement_method", "anticipated contract type": "contract_type",
    "anticipated procurement instrument": "procurement_instrument", "contracting office uic": "contracting_office_uic",
    "anticipated solicitation - fiscal year": "solicitation_fy", "anticipated solicitation - quarter": "solicitation_quarter",
    "anticipated award - fiscal year": "award_fy", "anticipated award - quarter": "award_quarter",
    "anticipated period of performance": "period_of_performance_months", "follow-on or new": "follow_on_or_new",
    "existing contract number": "existing_contract_number", "incumbent contractor": "incumbent_contractor",
    "anticipated place of performance": "place_of_performance", "anticipated naics code": "naics", "anticipated psc": "psc",
    "contracting poc name": "contracting_poc_name", "contracting poc e-mail or phone": "contracting_poc_contact",
    "secondary poc name": "secondary_poc_name", "secondary poc e-mail or phone": "secondary_poc_contact",
    "anticipated facilities clearance": "facility_clearance", "anticipated personnel clearance": "personnel_clearance",
    "palt code": "palt_code", "sub palt code": "sub_palt_code", "comments or special requirements": "comments",
    "pid number": "pid", "url": "url",
}
COLUMNS = list(dict.fromkeys(HEADERS.values()))
TRACKED = ["office_code_string", "anticipated_total_value", "procurement_method", "contract_type", "procurement_instrument",
           "contracting_office_uic", "solicitation_fy", "solicitation_quarter", "award_fy", "award_quarter", "follow_on_or_new",
           "existing_contract_number", "incumbent_contractor"]
VALUE_RANGES = {
    "< $2M": (0, 2_000_000), "<$2M": (0, 2_000_000), "$2M - $7.5M": (2_000_000, 7_500_000), "> $2M - < $7.5M": (2_000_000, 7_500_000),
    "$7.5M - $50M": (7_500_000, 50_000_000), "> $7.5M - < $50M": (7_500_000, 50_000_000), "$50M - $100M": (50_000_000, 100_000_000),
    "> $50M - < $100M": (50_000_000, 100_000_000), "$100M - $250M": (100_000_000, 250_000_000), "> $100M - < $250M": (100_000_000, 250_000_000),
    "$250M - $1B": (250_000_000, 1_000_000_000), "> $250M - < $1B": (250_000_000, 1_000_000_000), "> $1B+": (1_000_000_000, ""),
    "> $1B": (1_000_000_000, ""), "No Range Specified": ("", ""),
    "\u2265 $50M\u2012<$100M": (50_000_000, 100_000_000),  # the NAVSEA sheet once writes the range with a >= and a figure dash
    "< $25K": (0, 25_000), "$25K to $250K": (25_000, 250_000), "$250K to $1M": (250_000, 1_000_000), "$1M to $5M": (1_000_000, 5_000_000),
    "$5M to $10M": (5_000_000, 10_000_000), "$10M to $25M": (10_000_000, 25_000_000), "$25M to $50M": (25_000_000, 50_000_000),
    "$50M to $100M": (50_000_000, 100_000_000), "$100M to $250M": (100_000_000, 250_000_000), "\u2265 $250M": (250_000_000, ""),
}


# ---------------------------------------------------------------- saved inputs

def manifest_rows() -> list[dict]:
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]


def saved(rows: list[dict], predicate) -> dict | None:
    """Latest successful manifest row matching the predicate whose bytes are still on disk."""
    hits = [r for r in rows if kept_page(r)
            and predicate(r) and (ROOT / r["path"]).exists()]
    return hits[-1] if hits else None


def retrievals(release: dict, manifest: list[dict]) -> list[dict]:
    """A `record_system` release (a forecast system that serves only its current records, under numbers of its own: the
    DHS APFS) is every saved pull of its URL, one release per retrieval day keyed `<key>_<day>` and pinned to that pull's
    bytes, so a record withdrawn between two pulls keeps its history. Any other release is itself."""
    if not release.get("record_system"):
        return [release]
    days = {m["retrieved_at"][:10]: m for m in manifest if kept_page(m) and release["match"] in m.get("url", "")
            and m.get("mime", "").endswith("json") and (ROOT / m["path"]).exists()}
    return [{**release, "key": f"{release['key']}_{day}", "release_date": day, "sha256": m["sha256"],
             "release_note": f"the published records as the forecast system served them on {day}"} for day, m in sorted(days.items())]


if any(r.get("record_system") for r in RELEASES):
    RELEASES = [x for r in RELEASES for x in retrievals(r, manifest_rows())]


def url_index(rows: list[dict]) -> dict[str, dict]:
    """`saved` for every URL at once: the latest successful row per URL whose bytes are on disk. For callers
    that look up thousands of URLs, where a scan of the manifest per lookup does not finish."""
    index: dict[str, dict] = {}
    for r in rows:
        if (kept_page(r) and r.get("url")
                and (ROOT / r["path"]).exists()):
            index[r["url"]] = r
    return index


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def header_fields(header, headers: dict[str, str]) -> list[str | None]:
    """Each header cell's field: the first line, lower-cased and its spaces collapsed, against the map's prefixes."""
    firsts = [" ".join(str(text or "").split("\n")[0].split()).lower() for text in header]
    return [next((name for prefix, name in headers.items() if first.startswith(prefix)), None) for first in firsts]


def header_at(sheet, headers: dict[str, str], last: int) -> int:
    """The first row up to `last` that names three known columns: one state's sheet of the Guard forecast has no title row."""
    for number, values in enumerate(sheet.iter_rows(min_row=1, max_row=last, values_only=True), start=1):
        if sum(1 for f in header_fields(values, headers) if f) >= 3:
            return number
    return last


def flat(value):
    """A JSON field as one cell: an object by its text fields ({"display_name": "Over $100M", ...}), a list joined."""
    if isinstance(value, dict):
        return " ".join(v for v in value.values() if isinstance(v, str))
    return "; ".join(map(str, value)) if isinstance(value, list) else value


def read_sheet(path: Path, release_key: str = "", sheet_name: str = SHEET, header_row: int = HEADER_ROW) -> tuple[dict, list[dict]]:
    import openpyxl  # optional dependency, only needed here

    # From bytes, not the path: openpyxl judges a file by its extension, and a saved download may have none.
    data = path.read_bytes()
    if data[:2] == b"PK":  # a workbook is a zip
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    else:  # a forecast published as CSV or as JSON records reads as one sheet under the name the release gives it
        book = openpyxl.Workbook()
        book.active.title = sheet_name
        if data.lstrip()[:1] == b"[":  # JSON records (the DHS forecast system): the first record's keys are the header
            records = json.loads(data)
            keys = list(records[0]) if records else []
            lines = [keys, *([flat(r.get(k)) for k in keys] for r in records)]
        else:
            lines = csv.reader(io.StringIO(data.decode("utf-8-sig", "replace")))
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE  # control characters a worksheet cell refuses
        for line in lines:
            book.active.append([ILLEGAL_CHARACTERS_RE.sub(" ", c) if isinstance(c, str) else c for c in line])
    release = next((r for r in RELEASES if r["key"] == release_key), {})
    headers = release.get("headers") or HEADERS
    columns = list(dict.fromkeys([*COLUMNS, *filter(None, headers.values())]))  # None drops a field
    several = sheet_name == "*"
    names = [s for s in book.sheetnames if s not in release.get("key_sheets", ())] if several else [sheet_name]
    meta, rows = {}, []
    for name in names:
        sheet = book[name]
        at = header_at(sheet, headers, header_row) if several else header_row
        for row in sheet.iter_rows(min_row=1, max_row=at - 1, values_only=True):
            if row and row[0] and str(row[0]).endswith(":") and len(row) > 1:
                meta.setdefault(str(row[0]).rstrip(":").strip(), cell(row[1]))
        fields = header_fields(next(sheet.iter_rows(min_row=at, max_row=at, values_only=True), ()), headers)
        for number, values in enumerate(sheet.iter_rows(min_row=at + 1, values_only=True), start=at + 1):
            if all(v is None or str(v).strip() == "" for v in values):
                continue
            record = {"sheet": name, "row_number": f"{name}!{number}" if several else number, "release": release_key}
            record.update({field: "" for field in columns})
            for i, field in enumerate(fields):
                if field and i < len(values):
                    record[field] = cell(values[i])
            if release.get("command_from") == "sheet":
                record["command"] = name.strip()
            if not record["requirement_title"]:  # some Guard lines fill only the project description; a need carries a title
                record["requirement_title"] = record.get("requirement_description", "")
            if not record["requirement_title"]:  # a line with no title or description is a stray cell, not a need
                continue
            keep = release.get("keep")  # (field, prefix): a department's forecast read for the one bureau this layer covers
            if keep and not record.get(keep[0], "").startswith(keep[1]):
                continue
            rows.append(record)
    if headers is not HEADERS:
        derive(rows)
    if release.get("record_url"):  # a forecast system with a public page per record (the DHS forecast's print page)
        for r in rows:
            r["url"] = r["url"] or (release["record_url"].format(record_id=r["record_id"]) if r.get("record_id") else "")
    return meta, rows


def excel_row(r: dict) -> str:
    """The row's number on its sheet ("TX!5" -> "5")."""
    return str(r["row_number"]).rsplit("!", 1)[-1]


def fiscal_quarter(day: str) -> tuple[str, str]:
    """'2026-11-03' -> ('FY27', 'Q1'): the federal year starts in October. A forecast that writes the fiscal quarter
    ('FY2026, Q4', 'FY26 Q4', '2026 4') is read as written; a quarter without a year ('FYXXXX, Q4') is no date."""
    m = re.match(r"(\d{4})-(\d{2})", day)
    if m:
        year, month = int(m[1]), int(m[2])
        return f"FY{(year + (month >= 10)) % 100:02d}", f"Q{(month - 10) % 12 // 3 + 1}"
    year = re.match(r"\s*(?:FY\s*)?(?:20)?(\d{2})(?!\d)", day, re.I)
    if not year:
        return "", ""
    quarter = re.search(r"\bQ\s*([1-4])\b|(?<!\w)([1-4])(?:st|nd|rd|th)?\s*(?:QTR)?\s*$", day[year.end():], re.I)  # 'Q2', '2', '2nd' or '2nd QTR'
    return f"FY{year[1]}", (f"Q{quarter[1] or quarter[2]}" if quarter else "")


AMOUNT = r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([KMB])?"
SCALE = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def value_band(text: str) -> tuple:
    """(low, high) in dollars: a band the Navy's sheets name, else a band or a figure as written ('$1,000,000 to
    $2,500,000', '$10K - $350K', '18000000'). Anything else ('TBD', '<SAT') states no amount."""
    if text in VALUE_RANGES:
        return VALUE_RANGES[text]
    usd = lambda n, s: round(float(n.replace(",", "")) * SCALE[(s or "").upper()])  # noqa: E731
    text = re.sub(r"^[A-Z]\d+\s*[-\u2013]\s*", "", text.strip())  # a numbered band ('R2 \u2013 $250K\u2013$7.5M') is its range
    low = re.fullmatch(rf"(?:over|>)\s*{AMOUNT}|{AMOUNT}\s*\+", text, re.I)  # 'Over $1B', '>$100M', '$100M+': no upper bound
    if low:
        return (usd(low[1], low[2]) if low[1] else usd(low[3], low[4])), ""
    m = re.fullmatch(rf"{AMOUNT}(?:\s*(?:to|-|\u2013)\s*{AMOUNT})?", text, re.I)
    if not m:
        return "", ""
    return usd(m[1], m[2]), usd(m[3], m[4]) if m[3] else usd(m[1], m[2])


def office_by_name(center: str, office: str) -> str:
    """The DoDAAC of the office a row names, from the profile's names for them: 'ACC-APG (Natick)' for a row under ACC-APG
    whose office names Natick, else the centre's own name ('ACC-DTA'). A name the profile does not carry has none."""
    named = P.get("fpds_offices", {}).items()
    for code, name in named:
        head, _, qualifier = name.partition(" (")
        if qualifier and head.upper() == center.upper() and qualifier.rstrip(")").upper() in office.upper():
            return code
    return next((code for code, name in named if center and name.upper() == center.upper()), "")


# DFARS 204.1603 (and FAR 4.1603 for a civilian office): an instrument number opens with its issuer's office code.
OFFICE_CODE = P["agency"]["office_code_re"]
ISSUER_RE = re.compile(rf"{OFFICE_CODE}(?=-?\d{{2}}-?[A-Z])")
DODAAC_RE = re.compile(OFFICE_CODE)


def derive(rows: list[dict]) -> None:
    """The fields a dated forecast states another way: fiscal year and quarter from its dates or its written quarters,
    the office string from Command and PM / Directorate, the contracting office from the DoDAAC the number opens with,
    else the DoDAAC the office column prints, else the profile's DoDAAC for the office's name (`contracting_office_basis`
    says which). A number printed on several rows (one solicitation, several lines) is kept on each row but is no row's
    PID, and so is a cell that holds no number ('TBD', '8(a)', 'N/A OY1')."""
    printed = Counter(r.get("number") for r in rows if r.get("number"))
    for r in rows:
        r.setdefault("number", "")  # the Corps of Engineers prints no number before the notice
        for name in ("solicitation_date", "solicitation_close_date", "award_date"):
            r[name] = re.sub(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}$", r"\1", r.get(name, ""))
            r[name] = re.sub(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", lambda m: f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}", r[name])  # 09/28/2026
        for name in ("solicitation", "award"):
            stated = " ".join(x for x in (r[f"{name}_date"], r.get(f"{name}_quarter_as_written", "")) if x)
            r[f"{name}_fy"], r[f"{name}_quarter"] = fiscal_quarter(stated)
        if r.get("naics_psc") and not (r.get("naics") or r.get("psc")):
            r["naics"], _, r["psc"] = (x.strip() for x in r["naics_psc"].partition("/"))
        for who in ("requirement_contact", "alternate_contact"):  # a record system writes a contact's first and last name apart
            if f"{who}_first" in r:
                r[who] = " ".join(filter(None, (r.pop(f"{who}_first"), r.pop(f"{who}_last", ""))))
        r["office_code_string"] = " - ".join(x for x in (r.get("command", ""), r.get("pm_directorate", "")) if x)
        issuer = ISSUER_RE.match(r["number"])
        office = r.get("contracting_office", "")
        named = office_by_name(r.get("contracting_center", ""), office)
        r["contracting_office_uic"], r["contracting_office_basis"] = (
            (issuer.group(0), "number") if issuer else (office, "office column") if DODAAC_RE.fullmatch(office)
            else (named, "office name") if named else ("", ""))
        number = printed[r["number"]] == 1 and sum(c.isdigit() for c in r["number"]) >= 2 and not r["number"].upper().startswith("N/A")
        r["pid"] = r["number"] if number else ""


def record_key(r: dict) -> str:
    """PID when the row has one; otherwise the row itself, scoped to its release.

    A row is a source record. Two rows that share a title and an office code are
    distinct planned actions until a reviewer says otherwise: in the June 2024
    release (no PID column) rows 406 and 408-411 all read "Order to Contract
    #N0003922D4001" under PMA/PMW-101 and describe a Lot 7 order, terminal
    destruction, terminal shipment, a French MIS buy and a feasibility study.
    Hashing title and office collapsed them into one record. Identity across
    releases is the matcher's job (`pair_releases`), which reports a shared key as
    a candidate rather than resolving it.
    """
    if r["pid"]:
        return r["pid"]
    return f"row:{r.get('release', '')}:{r['row_number']}"


# ---------------------------------------------------------------- classification

def norm_code(text: str) -> str:
    return re.sub(r"[\s\-]", "", text.split("(")[0]).upper()


def handles(node: dict) -> list[str]:
    """The texts an office cell can name a node by: its name, aliases and codes, but not FPDS's agency id, a field of
    award records that NRL's department 1700 would otherwise match as the Department of the Navy."""
    texts = [node["name"]] + [a["text"] if isinstance(a, dict) else a for a in node.get("aliases") or []]
    return [str(t) for t in texts] + [str(v) for k, v in (node.get("codes") or {}).items() if k != "fpds_agency_id"]


def alias_map() -> dict[str, str]:
    seed = json.loads((RESEARCH / "memory" / "organization_seed.json").read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    for node in seed["nodes"]:
        if node["type"] == "person":
            continue
        for text in handles(node):
            aliases.setdefault(norm_code(text), node["id"])
    return aliases


def included_offices() -> set[str]:
    seed = json.loads((RESEARCH / "memory" / "organization_seed.json").read_text(encoding="utf-8"))
    offices = {r["from"] for r in seed["relationships"] if r["type"] == "child_of" and r["to"] == INCLUDED_PARENT
               and r["from"].startswith("pmw:") and r["review_status"] != "retracted"}
    return offices | {INCLUDED_PARENT}


def memory_file(name: str, empty):
    path = RESEARCH / "memory" / name  # an agency whose memory has no such file reads it empty
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else empty


def families() -> list[tuple[str, re.Pattern, str]]:
    data = memory_file("org_code_families.json", {"families": []})
    return [(f["family"], re.compile(f["pattern"]), f.get("org_type", "")) for f in data["families"]]


def classify_code(token: str, aliases: dict[str, str] | None = None, fams: list | None = None) -> dict:
    """Alias table first, then the first matching code family. `token` may carry a ' - NAME' suffix."""
    aliases = aliases if aliases is not None else alias_map()
    fams = fams if fams is not None else families()
    code = token.split(" - ")[0].strip()
    # The whole cell first: `Code 032 - Code 032/Ocean Battlespace Sensing - ONR` and `PSNS-1100 - 1100 - Executive Department`
    # are aliases whole, and their head alone is a number other activities print too.
    office_id = aliases.get(norm_code(token), "") or aliases.get(norm_code(code), "")
    family = next((name for name, pattern, _ in fams if pattern.match(code)), "")
    if office_id and family == "contracting_office_uic" and not office_id.startswith("contracting:"):
        family = ""  # an alias-resolved organization name is never a UIC, whatever its length
    org_type = next((t for name, _, t in fams if name == family), "")
    return {"code": code, "office_id": office_id, "family": family, "org_type": org_type}


def classify(rows: list[dict], scope: str = "peo_c4i") -> list[dict]:
    aliases, included, fams = alias_map(), included_offices(), families()
    out = []
    for r in rows:
        hit = classify_code(r["office_code_string"], aliases, fams)
        code, family, org_type, office_id = hit["code"], hit["family"], hit["org_type"], hit["office_id"]
        key = record_key(r)
        if scope == "all":
            # The whole activity loads. The office stays as the sheet wrote it until the memory knows it.
            decision, reason = "included", (f"activity-wide release; code resolves to {office_id}" if office_id
                                            else f"activity-wide release; code {code or 'blank'} names no office the memory knows")
        elif office_id in included:
            decision, reason = "included", f"code resolves to {office_id}, a PEO C4I office (alias table)"
        elif office_id:
            decision, reason = "excluded", f"code resolves to {office_id} ({org_type or 'other organization'}), outside the PEO C4I portfolio"
        elif family in FAMILY_PARENT:
            office_id = FAMILY_PARENT[family]
            decision, reason = "excluded", f"{org_type} code of {office_id}, outside the PEO C4I portfolio (division itself not in alias table)"
        elif family:
            decision, reason = "unresolved", f"family {family} recognised but code {code} has no alias-table entry"
        else:
            decision, reason = "unresolved", f"code {code} matches no code family"
        out.append({"sheet": r["sheet"], "row_number": r["row_number"], "record_key": key, "pid": r["pid"],
                    "office_code_string": r["office_code_string"], "office_code": code, "code_family": family,
                    "office_id": office_id, "include_decision": decision, "reason": reason})
    repeated = {k: n for k, n in Counter(c["record_key"] for c in out).items() if n > 1}
    if repeated:
        # A PID printed on two rows is a question for a person. Merging them would
        # discard one planned action; marking one a duplicate did exactly that.
        raise ValueError(f"record key repeats within one release: {repeated}")
    return out


# ---------------------------------------------------------------- joins

def contract_tokens(text: str) -> list[str]:
    compact = re.sub(r"[\s]", "", text.upper())
    tokens = []
    for match in PIID_RE.findall(compact.replace("-", "")):
        if match not in tokens:
            tokens.append(match)
    for match in PIID_RE.findall(compact):  # GSA schedule numbers keep their hyphens
        if match.startswith("GS") and match not in tokens:
            tokens.append(match)
    return tokens


def is_vehicle(token: str) -> bool:
    """IDVs (type letter D), SEWP and GSA schedule numbers are shared vehicles, never a requirement identity."""
    return token.startswith(("NNG", "GS")) or (len(token) in (13, 14) and token[8] == "D")


# The coded fields FPDS states on an action, each with the description it carries; read only when asked (Buying DNA).
FPDS_CODED = ("typeOfSetAside", "extentCompeted", "numberOfOffersReceived", "principalNAICSCode", "productOrServiceCode",
              "typeOfContractPricing", "totalBaseAndAllOptionsValue", "totalObligatedAmount", "referencedIDVType",
              "referencedIDVMultipleOrSingle", "solicitationProcedures", "contractActionType", "ultimateParentUEI",
              "ultimateParentUEIName", "UEI")


def fpds_entries(body: bytes, width: int | None = 160, full: bool = False) -> list[dict]:
    """Every action on an FPDS ATOM page; `width` clips the description (None keeps it whole). `full` adds the coded
    fields as {"code", "description"} under `coded`; the tag must end at the name, so vendorUEI is not vendorUEIInformation."""
    text = body.decode("utf-8", "replace")
    entries = []
    for chunk in text.split("<entry>")[1:]:
        def tag(name: str) -> str:
            found = re.search(rf"<ns1:{name}[^>]*>([^<]*)<", chunk)
            return html.unescape(found.group(1)).strip() if found else ""  # the feed escapes & as &amp; inside vendor names

        def coded(name: str) -> dict:
            # An order states set-aside and offers on its vehicle: idvTypeOfSetAside, idvNumberOfOffersReceived.
            found = (re.search(rf"<ns1:{name}(?=[\s>])([^>]*)>([^<]*)<", chunk)
                     or re.search(rf"<ns1:idv{name[0].upper()}{name[1:]}(?=[\s>])([^>]*)>([^<]*)<", chunk))
            if not found:
                return {"code": "", "description": ""}
            desc = re.search(r'description="([^"]*)"', found.group(1))
            return {"code": found.group(2).strip(), "description": desc.group(1).strip() if desc else ""}
        idv = re.search(r"<ns1:referencedIDVID>.*?<ns1:PIID>([^<]*)<", chunk, re.S)  # the vehicle's id sits below its agency id
        office_name = re.search(r'<ns1:contractingOfficeID name="([^"]*)"', chunk)
        row = {"piid": tag("PIID"), "signed": tag("signedDate")[:10], "contracting_office": tag("contractingOfficeID"),
               "funding_office": tag("fundingRequestingOfficeID"), "vendor": tag("vendorName"),
               "idv": idv.group(1).strip() if idv else "", "description": tag("descriptionOfContractRequirement")[:width],
               "completion": tag("ultimateCompletionDate")[:10], "solicitation": tag("solicitationID"),
               "obligated": tag("obligatedAmount"),  # this action's own obligation, as the feed prints it (a string; "" when absent)
               "mod": tag("modNumber"), "reason": tag("reasonForModification"), "research": tag("research"),
               "contracting_office_name": html.unescape(office_name.group(1)).strip() if office_name else ""}
        if full:
            row["coded"] = {name: coded(name) for name in FPDS_CODED}
        entries.append(row)
    return entries


def fpds_url(piid: str, start: int = 0) -> str:
    return FPDS.format(piid=piid, start=start)


def fpds_history(manifest: list[dict] | dict[str, dict], piid: str) -> tuple[list[dict], list[dict], bool]:
    """The saved pages of one contract's FPDS history, oldest first, every action on them tagged with
    the page that carries it, and whether the last page is among them. Pages chain from `start=0` in
    steps of FPDS_PAGE; the history is complete when the newest saved page names no next one.
    `manifest` is the manifest rows or their `url_index`."""
    pages: list[dict] = []
    actions: list[dict] = []
    while True:
        url = fpds_url(piid, len(pages) * FPDS_PAGE)
        capture = manifest.get(url) if isinstance(manifest, dict) else saved(manifest, lambda m, u=url: m.get("url") == u)
        if capture is None:
            return pages, actions, False
        body = (ROOT / capture["path"]).read_bytes()
        pages.append(capture)
        actions += [dict(e, page=capture["sha256"]) for e in fpds_entries(body)]
        if b'rel="next"' not in body:
            return pages, actions, True


def usaspending_url(piid: str, idv: str = "") -> str | None:
    """The award's USAspending page: an IDV (D or A in the ninth place) by its own number, an order under its IDV, any
    other award as a definitive contract. An order whose IDV FPDS has not named has no address yet."""
    kind = piid[8:9] if re.fullmatch(r"[A-Z][A-Z0-9]{5}\d{2}[A-Z][A-Z0-9]{4,5}", piid) else ""
    if kind in ("D", "A"):
        return USASPENDING_AWARD.format(f"CONT_IDV_{piid}_9700")
    if kind == "F":
        return USASPENDING_AWARD.format(f"CONT_AWD_{piid}_9700_{idv}_9700") if idv else None
    return USASPENDING_AWARD.format(f"CONT_AWD_{piid}_9700_-NONE-_-NONE-")


def current_releases() -> list[dict]:
    """The newest release of each activity, an activity another release carries included once: the rows an office
    forecasts today, whose contracts the joins look up."""
    newest: dict[str, dict] = {}
    for r in sorted(RELEASES, key=lambda r: r["release_date"]):
        for activity in (r["activity"], *r.get("carries", ())):
            newest[activity] = r
    return list({r["key"]: r for r in newest.values()}.values())


def sgs_url(query: str, active: str) -> str:
    return SGS + urllib.parse.urlencode({"index": "opp", "page": 0, "size": 25, "q": query, "mode": "search", "is_active": active})


def sgs_hits(body: bytes, needle: str) -> list[dict]:
    try:
        results = json.loads(body).get("_embedded", {}).get("results", [])
    except json.JSONDecodeError:
        return []
    compact = needle.replace("-", "").upper()
    return [x for x in results if compact in json.dumps(x).replace("-", "").upper()]


def contacts() -> dict[tuple[str, str], str]:
    rows = memory_file("contact_observations.json", [])
    return {(norm_code(c["name"]), c["office_id_as_resolved"]): c["id"] for c in rows if c.get("name")}


def attribution_by_pid() -> dict[str, dict]:
    rows = memory_file("attribution_examples.json", [])
    table = {}
    for x in rows:
        match = FORECAST_PID_RE.match((x.get("related") or {}).get("forecast_pid") or "")
        if match:
            table[match.group(0)] = x
    return table


def build_joins(rows: list[dict], classified: list[dict], manifest: list[dict]) -> list[dict]:
    decision = {c["row_number"]: c for c in classified}
    contact_table, examples = contacts(), attribution_by_pid()
    joins = []
    # Rows in this release citing each incumbent contract. A notice that cites a contract two
    # forecast rows share (a follow-on and a bridge, say) is joined to both as a candidate only.
    citers: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if decision[r["row_number"]]["include_decision"] == "included":
            for token in contract_tokens(r["existing_contract_number"]):
                citers[token].append(str(r["row_number"]))

    def add(r, join_type, method, key, target, evidence, note):
        joins.append({"record_key": record_key(r), "pid": r["pid"], "row_number": r["row_number"], "join_type": join_type, "method": method,
                      "key_used": key, "target_id": target, "evidence_ref": evidence, "note": note})

    for r in rows:
        c = decision[r["row_number"]]
        if c["include_decision"] != "included":
            continue
        if c["office_id"]:
            add(r, "office", "explicit", c["office_code"], c["office_id"],
                f"{r['sheet']}!C{excel_row(r)}; organization_seed.json alias table", "requirement-office code matched an alias")
        else:  # an activity-wide release keeps the row; the office stays as written until the memory knows it
            add(r, "office", "explicit", c["office_code"] or "(blank)", "", f"{r['sheet']}!C{excel_row(r)}",
                "office string names no organization the memory knows")
        tokens = contract_tokens(r["existing_contract_number"])
        if not tokens and r["existing_contract_number"]:
            add(r, "existing_contract", "explicit", r["existing_contract_number"], "", f"{r['sheet']}!O{excel_row(r)}",
                "no contract identifier pattern recognised in the cell")
        for token in tokens:
            pages, actions, complete = fpds_history(manifest, token)
            if not pages:
                add(r, "existing_contract", "explicit", token, "", "", "FPDS lookup not collected yet")
                continue
            mine = [e for e in actions if e["piid"] == token]
            if mine:
                e = mine[0]
                add(r, "existing_contract", "explicit", token, f"fpds:{token}", f"sha256:{pages[0]['sha256']}",
                    f"FPDS entries={len(mine)} on {len(pages)} page(s){'' if complete else ', history not saved to its last page'}; "
                    f"contracting {e['contracting_office']}; funding {e['funding_office']}; "
                    f"vendor {e['vendor']}; idv {e['idv'] or '-'}; first signed {e['signed']}")
            else:
                add(r, "existing_contract", "explicit", token, "", f"sha256:{pages[0]['sha256']}", "FPDS PIID search returned no entry")
        needles = [n for n in [r["pid"], *tokens] if n]
        for needle in needles:
            found, refs, hits = False, [], []
            for active in ("false", "true"):
                capture = saved(manifest, lambda m, u=sgs_url(needle, active): m.get("url") == u)
                if capture is None:
                    continue
                found = True
                refs.append(f"sha256:{capture['sha256']}")
                hits += sgs_hits((ROOT / capture["path"]).read_bytes(), needle)
            if not found:
                add(r, "notice", "explicit", needle, "", "", "SAM.gov search not collected yet")
            elif hits:
                vehicle, shared_by = is_vehicle(needle), citers.get(needle, [])
                if vehicle:
                    why = "key is a shared vehicle (IDV or schedule); the notice cites the vehicle, which does not establish the same requirement; "
                elif len(shared_by) > 1:
                    why = (f"key is an incumbent contract that {len(shared_by)} forecast rows in this release cite (rows {', '.join(shared_by)}); "
                           "the notice cites the contract, which does not tell the rows apart; ")
                else:
                    why = "notice text contains the key; "
                for h in hits:
                    add(r, "notice", "explicit" if why.startswith("notice text") else "inferred", needle, f"sam:{h.get('_id', '')}", "; ".join(refs),
                        why + f"title {str(h.get('title', ''))[:80]}")
            else:
                add(r, "notice", "explicit", needle, "", "; ".join(refs), "SAM.gov search returned no notice containing the key")
        example = examples.get(r["pid"]) if r["pid"] else None
        if example:
            sam_refs = [e["source_url"] for e in example["evidence"] if "sam.gov" in e.get("source_url", "")]
            add(r, "notice", "inferred", r["pid"], f"attribution:{example['id']}", "attribution_examples.json " + example["id"],
                f"forecast row linked to award {example['identifier']} by office, title and existing contract; "
                f"{len(sam_refs)} SAM.gov notice(s) cited on that award; evidence class {example['evidence_class']}")
        for role, name_col in (("contracting_poc", "contracting_poc_name"), ("secondary_poc", "secondary_poc_name")):
            name = r[name_col]
            if not name:
                continue
            target = contact_table.get((norm_code(name), c["office_id"]), "")
            elsewhere = [v for (n, o), v in contact_table.items() if n == norm_code(name) and o != c["office_id"]]
            note = f"{role} on the row matches a contact observation for the same office" if target else (
                f"{role} has no contact observation for {c['office_id']}" + (f"; observed for {', '.join(sorted(set(elsewhere)))}" if elsewhere else ""))
            add(r, "contact", "explicit", name, target, f"{r['sheet']}!{'T' if role == 'contracting_poc' else 'V'}{excel_row(r)}", note)
    joins.sort(key=lambda j: (j["row_number"], j["join_type"], j["method"], j["key_used"], j["target_id"]))
    return joins


# ---------------------------------------------------------------- layers (target schema)

def layers(rows: list[dict], classified: list[dict], joins: list[dict], source_sha: str, release: dict, release_date: str) -> dict[str, list[dict]]:
    decision = {c["row_number"]: c for c in classified}
    key = release["key"]
    evidence_id = lambda r: f"ev:{key}:row{r['row_number']}"  # noqa: E731
    needs, reqs, funding, refs, evidence = [], [], [], [], []
    contract_targets = {(j["row_number"], j["key_used"]): j for j in joins if j["join_type"] == "existing_contract"}
    for r in rows:
        c = decision[r["row_number"]]
        if c["include_decision"] != "included":
            continue
        rk = record_key(r)
        need_id = f"need:{key}:{rk}"
        evidence.append({"id": evidence_id(r), "source_sha256": source_sha, "locator": f"{r['sheet']}!row {excel_row(r)}",
                         "release_date": release_date, "kind": "spreadsheet_row"})
        needs.append({"id": need_id, "record_key": rk, "pid": r["pid"], "title": r["requirement_title"], "office_id": c["office_id"],
                      "office_code_string": r["office_code_string"], "contracting_office_uic": re.split(r"\s*[:-]\s*|\s+", r["contracting_office_uic"])[0].strip(),
                      "follow_on_or_new": r["follow_on_or_new"], "predecessor_refs": " ".join(contract_tokens(r["existing_contract_number"])),
                      "valid_from": release_date, "evidence_id": evidence_id(r)})
        reqs.append({"id": f"req:{key}:{rk}", "need_id": need_id, "revision": key,
                     "description": r["requirement_description"], "procurement_method": r["procurement_method"],
                     "contract_type": r["contract_type"], "instrument": r["procurement_instrument"],
                     "solicitation_fy": r["solicitation_fy"], "solicitation_quarter": r["solicitation_quarter"],
                     "award_fy": r["award_fy"], "award_quarter": r["award_quarter"], "pop_months": r["period_of_performance_months"],
                     "naics": r["naics"], "psc": r["psc"], "place": r["place_of_performance"], "evidence_id": evidence_id(r)})
        low, high = value_band(r["anticipated_total_value"])
        funding.append({"id": f"fund:{key}:{rk}", "need_id": need_id, "observation_type": "procurement_estimate",
                        "as_stated": r["anticipated_total_value"], "amount_low_usd": low, "amount_high_usd": high,
                        "fiscal_year": r["award_fy"], "period": r["award_quarter"], "evidence_id": evidence_id(r)})
        for token in contract_tokens(r["existing_contract_number"]):
            j = contract_targets.get((r["row_number"], token))
            refs.append({"id": f"pref:{key}:{rk}:{token}", "need_id": need_id, "identifier": token, "identifier_type": "piid",
                         "as_stated": r["existing_contract_number"], "resolved_in_fpds": "yes" if j and j["target_id"] else "no",
                         "evidence_ref": j["evidence_ref"] if j else ""})
    refs.sort(key=lambda x: (x["need_id"], x["identifier"]))
    return {"needs": needs, "need_requirements": reqs, "funding_observations": funding, "procurement_refs": refs, "evidence": evidence}


# ---------------------------------------------------------------- release matching

MATCH_MIN_RATIO = 0.85  # below this the two titles are different requirements, not a reword


def norm_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def office_code(r: dict) -> str:
    return (r["office_code_string"] or "").split(" - ")[0].strip().upper()


_OFFICES: dict[str, str] = {}
_alias_cache = cache(alias_map)  # ponytail: one seed per process; a build that rewrites the seed runs in its own process


def pair_office(r: dict) -> str:
    """The office two releases' rows are compared under: the node the cell resolves to, else its head token. ONR writes
    `ONR Code 32, ...` in one release and `Code 032 - Code 032/... - ONR` in the next, one department."""
    cell = r["office_code_string"] or ""
    if cell not in _OFFICES:
        _OFFICES[cell] = classify_code(cell, _alias_cache())["office_id"] or office_code(r)
    return _OFFICES[cell]


# Strongest signal first. Each entry is (basis, confidence, why, key function). A stage
# claims a pair only when the key hits exactly one row on each side; anything else is
# left for the next stage and, if nothing later resolves it, reported as ambiguous.
MATCH_STAGES = [
    ("pid", "confirmed", "same PID number in both releases",
     lambda r: [r["pid"]] if r["pid"] else []),
    ("title+office", "confirmed", "same requirement title under the same office code",
     lambda r: [norm_title(r["requirement_title"]) + "|" + pair_office(r)] if norm_title(r["requirement_title"]) else []),
    # A production follow-on and an engineering-support bridge share an office and an incumbent,
    # so this stage still claims a 1:1 pair but hands it to a reviewer.
    ("office+incumbent", "candidate", "same incumbent contract number under the same office code; a follow-on and a bridge can share both, so a reviewer decides",
     lambda r: [pair_office(r) + "|" + t for t in contract_tokens(r["existing_contract_number"])]),
]


def pair_releases(old_rows: list[dict], new_rows: list[dict], by_number: bool = False) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Follow the same requirement from one release to the next.

    No single key does this. The June 2024 release has no PID column at all, and PIDs are
    not stable where they exist: 31 of the 2023 export's 643 PIDs reappear in June 2025.
    So the stages run strongest first - PID, then exact title under the same office, then
    the incumbent contract number under the same office - and each one only claims a pair
    it can make 1:1. The first two confirm a pair; the third only nominates one, because a
    production follow-on and an engineering-support bridge can share an office and an
    incumbent. What survives all three gets one greedy pass of title similarity within the
    office, which also produces candidates rather than matches: a reworded title is a
    judgement, so the score and the earlier wording travel with the row for a reviewer to
    accept or reject. A key that matched several rows and was never resolved is
    reported as `ambiguous` with the counts that made it so, not silently dropped.
    """
    pairs: list[dict] = []
    contested: list[dict] = []
    left_old, left_new = list(old_rows), list(new_rows)
    for basis, confidence, why, key_of in MATCH_STAGES[:1] if by_number else MATCH_STAGES:
        index_old: dict[str, list[dict]] = defaultdict(list)
        index_new: dict[str, list[dict]] = defaultdict(list)
        for r in left_old:
            for k in key_of(r):
                index_old[k].append(r)
        for r in left_new:
            for k in key_of(r):
                index_new[k].append(r)
        taken_old, taken_new = set(), set()
        for k in sorted(set(index_old) & set(index_new)):
            # One row can carry several keys in the same stage (a row citing two incumbent contract
            # numbers). A key claims only rows no earlier key of this stage took, so a row is paired
            # once, the way the stages themselves hand rows on.
            olds = [r for r in index_old[k] if r["row_number"] not in taken_old]
            news = [r for r in index_new[k] if r["row_number"] not in taken_new]
            if not olds or not news:
                continue
            if len(olds) == 1 and len(news) == 1:
                pairs.append({"old": olds[0], "new": news[0], "basis": basis, "confidence": confidence, "reason": why})
                taken_old.add(olds[0]["row_number"])
                taken_new.add(news[0]["row_number"])
            else:
                contested.append({"key": k, "basis": basis, "olds": olds, "news": news, "why": why,
                                  "reason": f"{why}, but the key matches {len(olds)} earlier and {len(news)} later rows"})
        left_old = [r for r in left_old if r["row_number"] not in taken_old]
        left_new = [r for r in left_new if r["row_number"] not in taken_new]
    if by_number:  # a record system's number names one record for its life: a number on one side only was added or removed
        return pairs, contested, left_old, left_new

    by_office: dict[str, list[dict]] = defaultdict(list)
    for r in left_new:
        by_office[pair_office(r)].append(r)
    scored = []
    for r in left_old:
        for s in by_office.get(pair_office(r), ()):
            ratio = SequenceMatcher(None, norm_title(r["requirement_title"]), norm_title(s["requirement_title"])).ratio()
            if ratio >= MATCH_MIN_RATIO:
                scored.append((round(ratio, 4), r, s))
    # Best score first, then row order, so the pass is greedy but deterministic.
    scored.sort(key=lambda x: (-x[0], x[1]["row_number"], x[2]["row_number"]))
    taken_old, taken_new = set(), set()
    for ratio, r, s in scored:
        if r["row_number"] in taken_old or s["row_number"] in taken_new:
            continue
        pairs.append({"old": r, "new": s, "basis": "title~office", "confidence": "candidate",
                      "reason": f'titles {ratio:.2f} alike under office {office_code(r)}; earlier row read "{r["requirement_title"][:70]}"'})
        taken_old.add(r["row_number"])
        taken_new.add(s["row_number"])
    left_old = [r for r in left_old if r["row_number"] not in taken_old]
    left_new = [r for r in left_new if r["row_number"] not in taken_new]
    return pairs, contested, left_old, left_new


# ---------------------------------------------------------------- the model reads a candidate pair

PAIR_FIELDS = ("requirement_title", "requirement_description", "office_code_string", "existing_contract_number", "incumbent_contractor",
               "follow_on_or_new", "contract_type", "procurement_instrument", "anticipated_total_value", "solicitation_fy",
               "solicitation_quarter", "award_fy", "award_quarter", "psc", "naics")
SYSTEM_PAIR = ("You compare two rows of the U.S. Navy's long range acquisition forecast under one office, one from an earlier release "
               "and one from a later release, and say whether the later row carries the same planned acquisition forward (retitled, "
               "re-dated or re-valued) or states a different one (a follow-on, a bridge, another phase, lot or product). Answer with: "
               "same, true or false; earlier_words, a few words copied exactly from the earlier row's requirement_title or "
               "requirement_description that name the acquisition; later_words, the same from the later row; reason, at most 25 "
               "words. A shared office, incumbent, contract number, vehicle, NAICS code or value range alone never makes two rows "
               "the same acquisition.")
SCHEMA_PAIR = {"type": "object", "additionalProperties": False, "required": ["same", "earlier_words", "later_words", "reason"],
               "properties": {"same": {"type": "boolean"}, "earlier_words": {"type": "string"}, "later_words": {"type": "string"},
                              "reason": {"type": "string"}}}


def row_text(r: dict) -> str:
    return "\n".join(f"{f}: {' '.join(str(r.get(f) or '').split())[:400]}" for f in PAIR_FIELDS if r.get(f))


def pair_problems(answer: dict, old: dict, new: dict) -> list[str]:
    """A 'same' the rows do not support: words not copied verbatim from each row's title or description."""
    from reader import flatten
    if not answer["same"]:
        return []
    out = []
    for side, row, words in (("earlier", old, answer["earlier_words"]), ("later", new, answer["later_words"])):
        own = flatten(f"{row['requirement_title']} {row.get('requirement_description') or ''}").lower()
        if not words.strip() or flatten(words).lower() not in own:
            out.append(f"{side} words are not in the row's title or description verbatim")
    return out


def review(pairs: list[dict], replay_only: bool = False, workers: int = 8) -> None:
    """Each candidate pair read by the model: one it reads as the same acquisition, quoting both rows, is confirmed and
    folds like a PID match; one it reads as two acquisitions stays a candidate with the model's reason."""
    from concurrent.futures import ThreadPoolExecutor
    from llm import structured
    todo = [p for p in pairs if p["confidence"] == "candidate"]

    def one(p):
        user = f"Earlier release row:\n{row_text(p['old'])}\n\nLater release row:\n{row_text(p['new'])}"
        answer, _ = structured(SYSTEM_PAIR, user, SCHEMA_PAIR, "lrae_pair", replay_only=replay_only)
        return answer, pair_problems(answer, p["old"], p["new"])

    with ThreadPoolExecutor(workers) as pool:
        answers = list(pool.map(one, todo))
    for p, (a, problems) in zip(todo, answers):
        if a["same"] and not problems:
            p.update(confidence="confirmed", basis=f"{p['basis']}, read by the model",
                     reason=f'{p["reason"]}; the model reads one acquisition: "{a["earlier_words"]}", then "{a["later_words"]}"')
        else:
            p["reason"] += f"; the model reads {'one acquisition, but ' + problems[0] if a['same'] else 'two acquisitions: ' + a['reason']}"


# ---------------------------------------------------------------- release diff

def diff_releases(old_rows: list[dict], new_rows: list[dict], read: bool = False, by_number: bool = False) -> tuple[list[dict], str]:
    """Added, removed, changed and candidate records between two releases, from `pair_releases`; with `read`, the model
    reads each candidate pair (`review`)."""
    pairs, contested, only_old, only_new = pair_releases(old_rows, new_rows, by_number)
    if read:
        review(pairs)
    matched_old = {p["old"]["row_number"] for p in pairs}
    matched_new = {p["new"]["row_number"] for p in pairs}

    def row(change, key, basis, confidence, reason, field="", old_value="", new_value="", old_row="", new_row="", office=""):
        return {"change": change, "key": key, "key_method": basis, "confidence": confidence, "field": field,
                "old_value": old_value, "new_value": new_value, "old_row": old_row, "new_row": new_row,
                "office": office, "reason": reason}

    out = []
    for c in contested:
        # Only the rows still unpaired after every stage are ambiguous. A row a later stage resolved is
        # reported as that pair, and a key with nothing left on one side is no longer a contest: its
        # remaining rows are added or removed rows and are reported as such.
        live_old = [r for r in c["olds"] if r["row_number"] not in matched_old]
        live_new = [r for r in c["news"] if r["row_number"] not in matched_new]
        if not live_old or not live_new:
            continue
        out.append(row("ambiguous", c["key"], c["basis"], "candidate",
                       f"{c['why']}, but the key matches {len(live_old)} earlier and {len(live_new)} later rows",
                       old_value=f"{len(live_old)} rows", new_value=f"{len(live_new)} rows",
                       old_row=";".join(str(r["row_number"]) for r in live_old),
                       new_row=";".join(str(r["row_number"]) for r in live_new),
                       office=office_code(live_new[0])))
    for p in pairs:
        o, n = p["old"], p["new"]
        key = n["pid"] or o["pid"] or f"row {o['row_number']}->{n['row_number']}"
        changed = [f for f in TRACKED if o[f] != n[f]]
        common = dict(key=key, basis=p["basis"], confidence=p["confidence"], reason=p["reason"],
                      old_row=o["row_number"], new_row=n["row_number"], office=office_code(n))
        if not changed:
            out.append(row("unchanged", **common))
        for field in changed:
            out.append(row("changed", field=field, old_value=o[field][:120], new_value=n[field][:120], **common))
    for r in only_old:
        out.append(row("removed", r["pid"] or f"row {r['row_number']}", "", "confirmed",
                       "no PID, title, incumbent contract or similar title in the later release",
                       old_value=r["requirement_title"][:120], old_row=r["row_number"], office=office_code(r)))
    for r in only_new:
        out.append(row("added", r["pid"] or f"row {r['row_number']}", "", "confirmed",
                       "no PID, title, incumbent contract or similar title in the earlier release",
                       new_value=r["requirement_title"][:120], new_row=r["row_number"], office=office_code(r)))
    out.sort(key=lambda x: (x["change"], str(x["old_row"]).zfill(6), str(x["new_row"]).zfill(6), x["field"]))
    confirmed = sum(1 for p in pairs if p["confidence"] == "confirmed")
    method = (f"by the record system's own number alone ({confirmed} matched)" if by_number else
              f"staged: {', '.join(s[0] for s in MATCH_STAGES)}, then title similarity >= {MATCH_MIN_RATIO} "
              f"within the office ({confirmed} matched, {len(pairs) - confirmed} candidates)")
    return out, method


# ---------------------------------------------------------------- accepted connections

DIFF_NAME = re.compile(r"diff_(lrae_[a-z]+_[\d-]+)_(lrae_[a-z]+_[\d-]+)\.csv")


def fold_map(pack_base: Path = PACK_BASE) -> tuple[dict[tuple[str, str], dict], list[str]]:
    """(release, record key) -> the key its chain loads under, for rows the release diffs tie with `confirmed`.

    A chain is the rows one requirement occupies across releases, joined by PID or by exact title
    under the same office. A candidate pair (a similar title, a shared incumbent) joins one only
    when the model read it as one acquisition and quoted both rows (`review`). The chain loads
    under its PID, or under its earliest row key when no release gave it one, and every folded
    row carries its tie: the basis, the two rows and the diff that paired them.
    The loader writes that tie into the assertion it emits for the row, so a tool reading the
    database alone sees the full history. A chain that would join two different PIDs, or two rows
    of one release, is left unfolded and named in the second value: those are a reviewer's call.
    """
    packs = [p for p in forecast_packs(pack_base) if (p / "rows_classified.csv").exists()]
    keys: dict[tuple[str, str], str] = {}  # (release, row number) -> record key, included rows only
    for pack in packs:
        with (pack / "rows_classified.csv").open(newline="") as handle:
            for c in csv.DictReader(handle):
                if c["include_decision"] == "included":
                    keys[(pack.name, c["row_number"])] = c["record_key"]
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(node):
        while parent.setdefault(node, node) != node:
            node = parent[node]
        return node

    ties: dict[tuple[str, str], dict] = {}
    for pack in packs:
        for path in sorted(pack.glob("diff_*.csv")):
            m = DIFF_NAME.match(path.name)
            if not m:
                continue
            older, newer = m.groups()
            with path.open(newline="") as handle:
                for ch in csv.DictReader(handle):
                    if ch["change"] not in ("unchanged", "changed") or ch["confidence"] != "confirmed":
                        continue
                    a, b = (older, ch["old_row"]), (newer, ch["new_row"])
                    if a not in keys or b not in keys:
                        continue
                    parent[find(a)] = find(b)
                    tie = {"basis": ch["key_method"], "reason": ch["reason"],
                           "via": f"{older} row {ch['old_row']} -> {newer} row {ch['new_row']}, {path.name}"}
                    ties.setdefault(a, tie)
                    ties.setdefault(b, tie)
    chains: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for node in parent:
        chains[find(node)].append(node)
    folded, refused = {}, []
    for members in chains.values():
        if len(members) < 2:
            continue
        members.sort()
        pids = sorted({keys[m] for m in members if not keys[m].startswith("row:")})
        rows_of = ", ".join(f"{r} row {n}" for r, n in members)
        if len(pids) > 1:
            refused.append(f"{' | '.join(pids)}: one chain, two PIDs ({rows_of}); each row loads as its own record")
            continue
        if len({r for r, _ in members}) < len(members):
            refused.append(f"{pids[0] if pids else keys[members[0]]}: one chain, two rows of one release ({rows_of}); each row loads as its own record")
            continue
        canonical = pids[0] if pids else keys[members[0]]
        for m in members:
            if keys[m] != canonical:
                folded[(m[0], keys[m])] = {"key": canonical, **ties[m]}
    return folded, refused


# ---------------------------------------------------------------- outputs

def write_csv(path: Path, rows: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def reconciliation(release: dict, rows, classified, joins, diff_note: str) -> str:
    decisions = Counter(c["include_decision"] for c in classified)
    reasons = Counter((c["include_decision"], c["reason"]) for c in classified)
    unresolved = sorted({c["office_code"] for c in classified if c["include_decision"] == "unresolved"})
    per_office = Counter(c["office_id"] or "(no office the memory knows)" for c in classified if c["include_decision"] == "included")
    same = Counter((norm_title(r["requirement_title"]), office_code(r)) for r in rows if norm_title(r["requirement_title"]))
    shared = sorted(k for k, v in same.items() if v > 1)
    # Whether the release *has* a PID column, not whether every raw row filled one.
    # Section headers and blank continuation rows leave it empty, so `all` reported
    # the 2023 release as having no PID column while all 127 of its included rows
    # carry one. `diff` below already asks the question this way.
    pid_rows = sum(1 for r in rows if r["pid"])
    has_pid = pid_rows > 0
    key_note = (f"PID, where present ({pid_rows} of {len(rows)} raw rows); a row without one is its own record (release and row number)"
                if has_pid else "the row itself, as release and row number (this release has no PID column). No two rows are merged at import")
    sheets = (f"Every sheet but {', '.join(f'`{s}`' for s in release['key_sheets'])} ({len({r['sheet'] for r in rows})} with rows), header on "
              f"Excel row {release['header_row']} or the first row above it that names the columns" if release["sheet"] == "*"
              else f"Sheet `{release['sheet']}`, header on Excel row {release['header_row']}")
    lines = [f"# Reconciliation - {release['key']}", "", f"{sheets}, data rows {rows[0]['row_number']}-{rows[-1]['row_number']}.",
             f"Record key: {key_note}.",
             "", "## Rows", "", "| Decision | Rows |", "| --- | --- |", f"| raw | {len(rows)} |"]
    lines += [f"| {d} | {decisions.get(d, 0)} |" for d in ("included", "excluded", "unresolved")]
    lines += ["", f"Sum of decisions: {sum(decisions.values())} (equals raw: {'yes' if sum(decisions.values()) == len(rows) else 'NO'}).", "",
              "## Reasons", "", "| Decision | Reason | Rows |", "| --- | --- | --- |"]
    lines += [f"| {d} | {reason} | {n} |" for (d, reason), n in sorted(reasons.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))]
    lines += ["", "## Included rows per office", "", "| Office | Rows |", "| --- | --- |"]
    lines += [f"| {o} | {n} |" for o, n in sorted(per_office.items())]
    lines += ["", "## Unresolved codes", "", ", ".join(f"`{u}`" for u in unresolved) or "none", "",
              "## Rows sharing a title and an office", "",
              "Nothing is marked duplicate at import: a row is a source record until a reviewer resolves its identity. "
              "Rows that repeat a title under one office code are listed with what tells them apart (description, value, "
              "award window), so the reviewer sees what the spreadsheet actually says. Across releases the matcher reports "
              "such a key as a candidate rather than choosing a row.", ""]
    if not shared:
        lines.append("No two rows share a title under one office code in this release.")
    for title, office in shared:
        group = [r for r in rows if (norm_title(r["requirement_title"]), office_code(r)) == (title, office)]
        lines.append(f"- {group[0]['requirement_title'][:70]} ({office}), {len(group)} rows:")
        for r in group:
            detail = " / ".join(line.strip() for line in r["requirement_description"].splitlines()
                                if line.strip() and norm_title(line) != norm_title(r["requirement_title"]))
            lines.append(f"  - row {r['row_number']}: {detail[:90] or 'no description beyond the title'} | {r['anticipated_total_value'] or 'no value'} | "
                         f"award {r['award_fy']} {r['award_quarter']}".rstrip())
    lines += ["", "## Joins (included rows only)", "", "| Join | Lines | Matched | Unmatched | Not collected |", "| --- | --- | --- | --- | --- |"]
    for name in ("office", "existing_contract", "notice", "contact"):
        group = [j for j in joins if j["join_type"] == name]
        matched = sum(1 for j in group if j["target_id"])
        pending = sum(1 for j in group if "not collected" in j["note"])
        lines.append(f"| {name} | {len(group)} | {matched} | {len(group) - matched - pending} | {pending} |")
    lines += ["", "Explicit joins: office code through the alias table, contract number found in FPDS, notice text containing the PID or contract "
              "number, POC name matching a contact observation for the same office. Inferred joins: forecast row tied to an award through an "
              "attribution example, or a notice that only cites a shared vehicle. A shared vehicle (SeaPort-NxG IDV, SEWP, GSA schedule) alone is never a join."]
    pending = sum("not collected" in j["note"] for j in joins)
    if pending:
        lines += ["", f"{pending} FPDS and SAM.gov lookups for this release are not collected yet (`lrae_package.py collect --release {release['key']}`); "
                  "lines marked 'not collected' here are honest gaps, not misses. Contact observations were built from the 2025 release, so older rows show no contact match."]
    lines += ["", "## Releases", "", diff_note, ""]
    return "\n".join(lines)


def build() -> int:
    manifest = manifest_rows()
    packages = []
    for release in RELEASES:
        source = saved(manifest, lambda m, rel=release: rel["match"] in m.get("url", "") and m.get("mime", "").endswith(("sheet", "csv", "json"))
                       and m.get("sha256") == rel.get("sha256", m.get("sha256")))
        if source is None:
            print(f"{release['key']}: spreadsheet bytes not found under data/raw; skipped", file=sys.stderr)
            continue
        meta, rows = read_sheet(ROOT / source["path"], release["key"], release["sheet"], release["header_row"])
        release_date = meta.get("Release Date", "")[:10] if re.match(r"\d{4}-\d{2}-\d{2}", meta.get("Release Date", "")) else release["release_date"]
        packages.append((release, source, meta, rows, release_date))
    if not packages:
        return 1
    earlier: dict[str, list[tuple[dict, list[dict]]]] = {}  # per activity: a release is diffed against its own predecessors
    for release, source, meta, rows, release_date in packages:
        pack = PACK_BASE / release["key"]
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "layers").mkdir(exist_ok=True)
        classified = classify(rows, release["scope"])
        # Office joins come from the alias table for every release; FPDS, SAM.gov, contact and
        # attribution lookups were collected for the PEO C4I rows only and read "not collected" elsewhere.
        joins = build_joins(rows, classified, manifest)
        write_csv(pack / "rows_raw.csv", rows)
        write_csv(pack / "rows_classified.csv", classified)
        write_csv(pack / "joins.csv", joins)
        for name, table in layers(rows, classified, joins, source["sha256"], release, release_date).items():
            if table:
                write_csv(pack / "layers" / f"{name}.csv", table)
        notes = []
        for prev_release, prev_rows in [pr for a in (release["activity"], *release.get("carries", ())) for pr in earlier.get(a, [])]:
            changes, method = diff_releases(prev_rows, rows, read=True, by_number=bool(release.get("record_system")))
            name = f"diff_{prev_release['key']}_{release['key']}.csv"
            if changes:
                write_csv(pack / name, changes)
            counts = Counter(ch["change"] for ch in changes)
            paired = {ch["key"] for ch in changes if ch["change"] in ("unchanged", "changed")}
            candidates = {ch["key"] for ch in changes if ch["change"] in ("unchanged", "changed") and ch["confidence"] == "candidate"}
            notes.append(f"Compared with `{prev_release['key']}` ({method}): {len(paired)} records followed across the releases, "
                         f"{len(candidates)} of them candidates a reviewer still has to accept; {counts.get('changed', 0)} field changes on "
                         f"{len({ch['key'] for ch in changes if ch['change'] == 'changed'})} of them; {counts.get('added', 0)} added, {counts.get('removed', 0)} removed, "
                         f"{counts.get('ambiguous', 0)} key{'' if counts.get('ambiguous', 0) == 1 else 's'} left ambiguous. Every row carries its match basis and the reasoning in `{name}`.")
        diff_note = " ".join(notes) + " Every release is kept as its own package." if notes else "Earliest saved release; nothing to diff against."
        (pack / "reconciliation.md").write_text(reconciliation(release, rows, classified, joins, diff_note), encoding="utf-8")
        outputs = {p.relative_to(pack).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(pack.rglob("*")) if p.is_file() and p.name != "SOURCE.json" and not p.name.startswith("._")}
        info = {"release_key": release["key"], "activity": meta.get("Activity Name", ""), "release_date": release_date,
                "release_date_as_written": meta.get("Release Date", ""), "release_note": release["release_note"],
                "source_url": source["url"], "fetched_from": source.get("fetched_from", ""), "method": source["method"],
                "wayback_timestamp": source.get("wayback_timestamp", ""), "retrieved_at": source["retrieved_at"],
                "sha256": source["sha256"], "size": source["size"], "raw_path": source["path"], "sheet": release["sheet"],
                "header_row": release["header_row"], "scope": release["scope"],
                "refetch": f"python research/tools/fetch.py '{source['url']}' --wayback {source.get('wayback_timestamp', '')}",
                "regenerate": "python research/tools/lrae_package.py build", "record_key": "pid, or the row itself (release key and row number) when the row has none; nothing is merged at import",
                "joins_collected": not any("not collected" in j["note"] for j in joins if j["join_type"] in ("existing_contract", "notice")), "outputs": outputs}
        (pack / "SOURCE.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(release["key"], release_date, len(rows), "rows;", Counter(c["include_decision"] for c in classified))
        earlier.setdefault(release["activity"], []).append((release, rows))
    return 0


def collect(limit: int, keys: list[str] | None = None) -> int:
    manifest = manifest_rows()
    wanted: list[tuple[str, str]] = []
    piids: set[str] = set()
    for release in [r for r in RELEASES if r["key"] in keys] if keys else current_releases():
        source = saved(manifest, lambda m, rel=release: rel["match"] in m.get("url", "") and m.get("mime", "").endswith(("sheet", "csv", "json"))
                       and m.get("sha256") == rel.get("sha256", m.get("sha256")))
        if source is None:
            print(f"{release['key']}: spreadsheet bytes not saved; nothing to look up", file=sys.stderr)
            continue
        _, rows = read_sheet(ROOT / source["path"], release["key"], release["sheet"], release["header_row"])
        included = {c["row_number"] for c in classify(rows, release["scope"]) if c["include_decision"] == "included"}
        for r in rows:
            if r["row_number"] not in included:
                continue
            tokens = contract_tokens(r["existing_contract_number"])
            piids.update(tokens)
            where = f"{release['key']} row {r['row_number']}"
            wanted += [(fpds_url(t), f"LRAE join: FPDS search for existing contract {t} ({where})") for t in tokens]
            wanted += [(sgs_url(n, a), f"LRAE join: SAM.gov search for {n} ({where})") for n in (r["pid"], *tokens) if n for a in ("false", "true")]
    have = {m["url"] for m in manifest if kept_page(m) and m.get("url")}
    todo = []
    for url, note in wanted:
        if url not in have and url not in {u for u, _ in todo}:
            todo.append((url, note))
    print(f"{len(todo)} lookups to collect")
    done = 0
    with MANIFEST.open("a", encoding="utf-8") as handle:
        def take(url: str, note: str) -> dict:
            row = fetch(url, "direct", None, note)
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            print(row.get("status"), row.get("size"), url[:110])
            time.sleep(1.0)
            return row

        for url, note in todo[:limit]:
            take(url, note)
            done += 1
        # The rest of each contract's history: the completion date a follow-on is timed against is
        # stated by the newest action, and the feed puts that on the last page.
        manifest = manifest_rows()
        for piid in sorted(piids):
            while done < limit:
                pages, _, complete = fpds_history(manifest, piid)
                if complete or not pages:
                    break
                row = take(fpds_url(piid, len(pages) * FPDS_PAGE),
                           f"LRAE join: FPDS history page {len(pages) + 1} for existing contract {piid}")
                done += 1
                if not kept_page(row):
                    break
                manifest.append(row)
        # The award's USAspending page states the period of performance when the FPDS history does not reach its end.
        have = {m["url"] for m in manifest if kept_page(m) and m.get("url")}
        for piid in sorted(piids):
            if done >= limit:
                break
            _, actions, _ = fpds_history(manifest, piid)
            url = usaspending_url(piid, next((a["idv"] for a in actions if a["piid"] == piid and a["idv"]), ""))
            if url and url not in have:
                take(url, f"LRAE join: USAspending award page for existing contract {piid}")
                done += 1
    print(f"collected {done}")
    return 0


def selfcheck() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "f").write_bytes(b"\xef\xbb\xbfRequirement Title\nWidget\n\n")
        assert [r["requirement_title"] for r in read_sheet(Path(tmp) / "f", "", "csv", 1)[1]] == ["Widget"], "a CSV forecast reads as one sheet"
        (Path(tmp) / "j").write_bytes(b'[{"Requirement Title": "Gadget", "x": {"display_name": "Over $1M", "display_order": 3}}]')
        assert [r["requirement_title"] for r in read_sheet(Path(tmp) / "j", "", "json", 1)[1]] == ["Gadget"], "JSON records read as one sheet"
    assert value_band("R2 \u2013 $250K\u2013$7.5M") == (250_000, 7_500_000) and value_band("Over $1B") == (1_000_000_000, "")
    assert fiscal_quarter("2026 2nd") == ("FY26", "Q2") == fiscal_quarter("FY26 2nd QTR"), "an ordinal quarter"
    assert fiscal_quarter("2026-11-03") == ("FY27", "Q1") and fiscal_quarter("2027-03-31") == ("FY27", "Q2") and fiscal_quarter("") == ("", "")
    dated = [{"number": n, "solicitation_date": "2026-05-01 00:00:00", "solicitation_close_date": "", "award_date": "2026-09-30 00:00:00",
              "command": "PEO AVIATION", "pm_directorate": pm} for n, pm in (("N00019-26-R-0008", "UAS"), ("N00019-26-R-0008", ""), ("PANDTA-26-P-0000 1", "UAS"))]
    derive(dated)
    assert [(r["pid"], r["contracting_office_uic"], r["office_code_string"]) for r in dated] == [
        ("", "N00019", "PEO AVIATION - UAS"), ("", "N00019", "PEO AVIATION"), ("PANDTA-26-P-0000 1", "", "PEO AVIATION - UAS")], \
        "a number on two rows is no row's PID; the issuer is the DoDAAC the number opens with"
    assert dated[0]["award_fy"] == "FY26" and dated[0]["award_quarter"] == "Q4" and dated[0]["solicitation_date"] == "2026-05-01"
    named = [{**dated[0], "requirement_contact_first": "Jared", "requirement_contact_last": "Slizofski", "alternate_contact_first": "",
              "alternate_contact_last": ""}]
    derive(named)
    assert named[0]["requirement_contact"] == "Jared Slizofski" and named[0]["alternate_contact"] == "" and "requirement_contact_first" not in named[0]
    assert handles({"name": "Department of the Navy", "aliases": [{"text": "DoN"}], "codes": {"fpds_agency_id": "1700", "uic": "N00039"}}) \
        == ["Department of the Navy", "DoN", "N00039"]
    old_row, new_row = {"requirement_title": "MIDS JTRS Production Lot 12"}, {"requirement_title": "MIDS JTRS Lot 12 Production", "requirement_description": ""}
    same = {"same": True, "earlier_words": "MIDS JTRS Production Lot 12", "later_words": "MIDS JTRS Lot 12", "reason": ""}
    assert pair_problems(same, old_row, new_row) == []
    assert pair_problems({**same, "later_words": "N0003920D0061"}, old_row, new_row) == ["later words are not in the row's title or description verbatim"]
    assert pair_problems({**same, "same": False, "later_words": ""}, old_row, new_row) == [], "two acquisitions is an answer"
    onr = {norm_code("Code 032 - Code 032/Ocean Battlespace Sensing - ONR"): "dept:onr-code-32", norm_code("1100"): "dept:nrl-1100"}
    assert classify_code("Code 032 - Code 032/Ocean Battlespace Sensing - ONR", onr, [])["office_id"] == "dept:onr-code-32", "the whole cell first"
    assert classify_code("1100 - 1100 - Institute of Science DIV - NRL", onr, [])["office_id"] == "dept:nrl-1100", "then its head"
    blank = {c: "" for c in COLUMNS}

    def r(number, title, office, pid="", contract="", value="No Range Specified"):
        return {**blank, "sheet": SHEET, "row_number": number, "requirement_title": title, "pid": pid,
                "office_code_string": f"{office} - {office} - NAVWAR", "existing_contract_number": contract,
                "anticipated_total_value": value}

    # A PID carries a row when both releases have one.
    pairs, _, _, _ = pair_releases([r(1, "Alpha", "PMW-160", pid="N00039-23-RFPREQ-PMW-160-0001")],
                                   [r(9, "Alpha renamed", "PMW-160", pid="N00039-23-RFPREQ-PMW-160-0001")])
    assert [(p["basis"], p["confidence"]) for p in pairs] == [("pid", "confirmed")]

    # The release with no PID column still follows on title, then on the incumbent contract.
    pairs, _, _, _ = pair_releases([r(1, "Alpha", "PMW-160"), r(2, "Beta", "PMW-770", contract="N0003920D0061")],
                                   [r(9, "Alpha", "PMW-160"), r(8, "Beta, restructured buy", "PMW-770", contract="N0003920D0061")])
    assert sorted(p["basis"] for p in pairs) == ["office+incumbent", "title+office"]
    by_basis = {p["basis"]: p["confidence"] for p in pairs}
    assert by_basis["title+office"] == "confirmed"
    assert by_basis["office+incumbent"] == "candidate", "a follow-on and a bridge share office and incumbent; a reviewer decides"

    # Confirmed pairs fold into one chain under its PID; a candidate never joins one; two PIDs never fold.
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        def pack(name, entries):
            (base / name).mkdir()
            write_csv(base / name / "rows_classified.csv",
                      [{"row_number": n, "record_key": k, "include_decision": d} for n, k, d in entries])

        pack("lrae_navwar_2023-06", [("409", "P1", "included"), ("5", "P7", "included")])
        pack("lrae_navwar_2024-06", [("47", "row:lrae_navwar_2024-06:47", "included"), ("48", "row:lrae_navwar_2024-06:48", "included"),
                                     ("9", "row:lrae_navwar_2024-06:9", "excluded")])
        pack("lrae_navwar_2025-06", [("108", "P1", "included"), ("6", "P8", "included")])
        cols = ["change", "key", "key_method", "confidence", "field", "old_value", "new_value", "old_row", "new_row", "office", "reason"]

        def ch(change, method, confidence, old, new):
            return {**{c: "" for c in cols}, "change": change, "key_method": method, "confidence": confidence,
                    "old_row": old, "new_row": new, "reason": "why"}

        write_csv(base / "lrae_navwar_2024-06" / "diff_lrae_navwar_2023-06_lrae_navwar_2024-06.csv",
                  [ch("changed", "title+office", "confirmed", "409", "47"), ch("unchanged", "office+incumbent", "candidate", "409", "48"),
                   ch("unchanged", "title+office", "confirmed", "5", "48")])
        write_csv(base / "lrae_navwar_2025-06" / "diff_lrae_navwar_2024-06_lrae_navwar_2025-06.csv",
                  [ch("unchanged", "title+office", "confirmed", "47", "108"), ch("unchanged", "title+office", "confirmed", "48", "6")])
        folded, refused = fold_map(base)
        tie = folded[("lrae_navwar_2024-06", "row:lrae_navwar_2024-06:47")]
        assert tie["key"] == "P1" and tie["basis"] == "title+office" and tie["via"].startswith("lrae_navwar_2023-06 row 409 -> lrae_navwar_2024-06 row 47")
        assert ("lrae_navwar_2023-06", "P1") not in folded, "the rows carrying the PID need no tie"
        assert ("lrae_navwar_2024-06", "row:lrae_navwar_2024-06:48") not in folded, "P7 -> row 48 -> P8 would join two PIDs"
        assert len(refused) == 1 and "P7 | P8" in refused[0], refused

    # A reworded title is a candidate, not a match, and the reasoning travels with it.
    pairs, _, _, _ = pair_releases([r(1, "Shore Network Modernisation Support Services", "PMW-205")],
                                   [r(9, "Shore Network Modernization Support Services", "PMW-205")])
    assert len(pairs) == 1 and pairs[0]["basis"] == "title~office"
    assert pairs[0]["confidence"] == "candidate" and "alike under office PMW-205" in pairs[0]["reason"]

    # Different offices are never guessed at, however close the titles read.
    pairs, _, only_old, only_new = pair_releases([r(1, "Network Support Services", "PMW-205")],
                                                 [r(9, "Network Support Services", "PMW-160")])
    assert not pairs and len(only_old) == 1 and len(only_new) == 1

    # One key, several rows: kept as a candidate with the counts, never silently dropped.
    changes, _ = diff_releases([r(1, "Alpha", "PMW-160", contract="N0003920D0061"),
                                r(2, "Alpha II", "PMW-160", contract="N0003920D0061")],
                               [r(9, "Gamma", "PMW-160", contract="N0003920D0061")])
    ambiguous = [c for c in changes if c["change"] == "ambiguous"]
    assert len(ambiguous) == 1, ambiguous
    assert ambiguous[0]["confidence"] == "candidate"
    assert "2 earlier and 1 later rows" in ambiguous[0]["reason"]

    # A later stage that resolves every contested row withdraws the ambiguity.
    changes, _ = diff_releases([r(1, "Alpha", "PMW-160", contract="N0003920D0061"),
                                r(2, "Beta", "PMW-160", contract="N0003920D0061")],
                               [r(9, "Alpha", "PMW-160", contract="N0003920D0061"),
                                r(8, "Beta", "PMW-160", contract="N0003920D0061")])
    assert not [c for c in changes if c["change"] == "ambiguous"]
    assert {c["change"] for c in changes} == {"unchanged"}

    # A row citing two incumbent contract numbers carries two keys in one stage. It is paired once.
    pairs, _, _, _ = pair_releases([r(1, "Alpha", "PMW-160", contract="N0003920D0061, N0003921D0075")],
                                   [r(9, "Alpha follow-on", "PMW-160", contract="N0003920D0061, N0003921D0075")])
    assert len(pairs) == 1, pairs
    assert [(p["old"]["row_number"], p["new"]["row_number"]) for p in pairs] == [(1, 9)]

    # A row a later stage paired is not also reported inside an ambiguous group: the shared contract
    # contests four rows, the title stage then pairs the reworded Shore Network row, and only the
    # two rows still unpaired stay ambiguous.
    shore = ("Shore Network Modernisation Support Services", "Shore Network Modernization Support Services")
    changes, _ = diff_releases([r(1, shore[0], "PMW-160", contract="N0003920D0061"), r(2, "Beta", "PMW-160", contract="N0003920D0061")],
                               [r(9, shore[1], "PMW-160", contract="N0003920D0061"), r(8, "Gamma", "PMW-160", contract="N0003920D0061")])
    assert [(c["old_row"], c["new_row"]) for c in changes if c["change"] == "ambiguous"] == [("2", "8")], changes
    assert any(c["key"] == "row 1->9" and c["key_method"] == "title~office" for c in changes), "the title stage still pairs the Shore Network row"

    # Two rows with one title under one office are two records. The June 2024 release
    # lists five "Order to Contract #N0003922D4001" rows at PMA/PMW-101 describing
    # different work; neither is a duplicate of the other.
    twins = [dict(r(406, "Order to Contract #N0003922D4001", "PMA/PMW-101"), release="lrae_navwar_2024-06", requirement_description="Lot 7 DO#24F4014"),
             dict(r(408, "Order to Contract #N0003922D4001", "PMA/PMW-101"), release="lrae_navwar_2024-06", requirement_description="BU1 SRU Destruction")]
    assert len({record_key(t) for t in twins}) == 2
    assert [c["include_decision"] for c in classify(twins)] == ["included", "included"]
    # The same row number in another release is another record.
    assert record_key(dict(twins[0], release="lrae_navwar_2023-06")) != record_key(twins[0])
    # A PID on two rows is refused, not merged.
    try:
        classify([r(1, "Alpha", "PMW-160", pid="P1"), r(2, "Alpha, second lot", "PMW-160", pid="P1")])
    except ValueError as exc:
        assert "P1" in str(exc)
    else:
        raise AssertionError("a repeated PID must raise")

    # A tracked field that moved is reported once per field, against the pair.
    changes, _ = diff_releases([r(1, "Alpha", "PMW-160", pid="P1", value="$250M - $1B")],
                               [r(9, "Alpha", "PMW-160", pid="P1", value="> $1B+")])
    moved = [c for c in changes if c["change"] == "changed"]
    assert len(moved) == 1 and moved[0]["field"] == "anticipated_total_value"
    assert moved[0]["old_value"] == "$250M - $1B" and moved[0]["new_value"] == "> $1B+"

    # A contract's FPDS history is read across its saved pages, oldest first, and is complete only
    # when the newest saved page names no next one.
    with tempfile.TemporaryDirectory() as tmp:
        def page(start: int, signed: str, more: bool) -> dict:
            body = f"<entry><ns1:PIID>P1</ns1:PIID><ns1:signedDate>{signed}</ns1:signedDate></entry>" + ('<link rel="next" href="x"/>' if more else "")
            path = Path(tmp) / f"p{start}"
            path.write_text(body, encoding="utf-8")
            return {"url": fpds_url("P1", start), "status": 200, "path": str(path), "sha256": f"sha{start}"}
        saved_pages = [page(0, "2020-01-01", True)]
        pages, actions, complete = fpds_history(saved_pages, "P1")
        assert (len(pages), complete, [a["signed"] for a in actions]) == (1, False, ["2020-01-01"])
        saved_pages.append(page(10, "2021-01-01", False))
        pages, actions, complete = fpds_history(saved_pages, "P1")
        assert (len(pages), complete, [a["page"] for a in actions]) == (2, True, ["sha0", "sha10"])
        assert fpds_history(saved_pages, "P2") == ([], [], False)

    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "build"
    if command in ("--selfcheck", "selfcheck"):
        sys.exit(selfcheck())
    if command == "collect":
        n = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 10_000
        sys.exit(collect(n, [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--release"]))
    sys.exit(build())

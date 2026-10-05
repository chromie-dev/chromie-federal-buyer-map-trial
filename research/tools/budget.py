#!/usr/bin/env python3
"""Money family: the agency's own budget justification books, read line by line.

Every Exhibit P-40 (procurement line item justification) page of a saved book becomes part of one record per P-1
line item: the amounts the book prints for each fiscal year, the appropriation and budget activity, and the
justification prose, so a requirement's names (MIDS, CANES, ADNS) can be found in it. A line whose FY2027 total
differs from FY2026 is a funding_change event; a flat line is a budget_line event. Nothing is inferred: the amounts
are the book's, the date is the book's ("Date: April 2026"), credited on that month's last day.

Research, development, test and evaluation books carry Exhibit R-2 pages instead, one per program element. Each page
is read by the exhibit it carries, so one agency can have both kinds (the Navy's procurement books are P-40, its
RDT&E,N books R-2; DARPA's are all R-2); the record shape is the same with the program element number where the P-1
line item number stands, and `exhibit` says which kind a record came from. Below the element, every program block
("Title: ... FY 2025 FY 2026 FY 2027") is kept as a record of its own under "programs": its amounts, its description
(the need the book states) and its plans, word for word, so money can be followed to the program and its manager. Books are the `*_Book.pdf` files
under the profile's books folder and any ledger row whose note begins "budget book:" and names the agency.

Two more readings sit beside the books. The Comptroller's display spreadsheets (Exhibit R-1 and P-1, one file each for
the whole Department) print every component's lines with the columns labelled as the Comptroller labels them (FY 2025
Actuals, FY 2026 Discretionary Enacted, FY 2027 Discretionary Request, ...); `display` keeps the rows of this profile's
component (the profile's `budget.display` filter: the account's service letter, and for DARPA the PE suffix) into
research/events/budget_measures.json, in $M, each row with its sheet and row number. An RDT&E book's Exhibit R-2A pages
list the projects under each program element and the named programs under each project ("Title: ..." rows with the
three fiscal-year amounts); `extract` attaches them to the R-2 line so a program can be followed to its money.

  python research/tools/budget.py extract        # every *_Book.pdf under data/raw/jbooks -> research/events/budget_lines.json
                                                 # (and the display spreadsheets -> budget_measures.json where the profile has a filter)
  python research/tools/budget.py display        # the display spreadsheets alone
  python research/tools/budget.py show MIDS      # the lines whose text names it
"""
from __future__ import annotations

import calendar
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
PDFTOTEXT = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import BOOKS, EVENTS, P, RAW, NOTE_TAG  # noqa: E402

BOOKS = BOOKS
OUT = EVENTS / "budget_lines.json"
PROVIDER = P["budget"]["provider"]
BOOK_NOTE = f"budget book{NOTE_TAG}:"
AGENCY_ABBREVIATION = P["agency"]["subtier_abbreviation"]
# A book note names the agency by its abbreviation (DARPA, USAF), its short name less the "U.S." (Air Force) or its label.
AGENCY_NAMES = (AGENCY_ABBREVIATION, P["short"].removeprefix("U.S. "), P["label"])

P40 = "Exhibit P-40, Budget Line Item Justification"
R2 = "Exhibit R-2, RDT&E Budget Item Justification"
PE_RE = re.compile(r"\bPE (\d{7}[A-Z]?) / (\S.*?)\s*$")
R2_BA_RE = re.compile(r"^\s*(\d{4}): ([^/]+?) / BA (\d+):\s*(.*?)\s*(?:PE \d{7}[A-Z]? /.*)?$")
TOTAL_PE_RE = re.compile(r"^\s*Total Program Element\s+(.*)$", re.M)
# "efforts in this PE will be funded in PE 0603468E", "realigned out of line item 2900 into line item 2361"
MOVED_RE = re.compile(r"\b(?:will be funded in|(?:transfer|mov|realign)\w*\b[^.]*?\b(?:to|into)) ((?:PE|LI|BLI|line item) \d{4}[^.]*)")
MOVED_ID_RE = re.compile(r"\b(?:PE|LI|BLI|line item) (\d{7}[A-Z]?|\d{4})\b")
def columns_for(pb: str) -> tuple[str, ...]:
    """The twelve resource-summary columns of a book for President's Budget year `pb`: prior years, the two years
    before the request, the request (base, overseas/other, total), the four out-years, to complete, total. A
    book's columns are its own year's; reading a PB 2026 book under PB 2027 labels shifts every amount a year."""
    y = int(pb)
    return ("prior_years", f"fy{y - 2}", f"fy{y - 1}", f"fy{y}_base", f"fy{y}_ooc", f"fy{y}_total",
            f"fy{y + 1}", f"fy{y + 2}", f"fy{y + 3}", f"fy{y + 4}", "to_complete", "total")


COLUMNS = columns_for("2027")
# "1810N: Other Procurement, Navy / BA 02: Communications & Electronics Equip /        2026 / SPQ-9B Radar"; the layout
# text often cuts the budget activity to "BA" or "B" where the right-hand column overlaps, so a line takes it from any of
# its pages that prints it; a long name loses its slash and runs into the line number ("Communications and Electronics  4644 /").
# The appropriation code carries the service letter (1810N Other Procurement, Navy; 3080F Other Procurement, Air Force);
# the Navy numbers its P-1 lines with four digits, the Air Force with six (821800).
LI_RE = re.compile(r"^\s*(\d{4}[A-Z]?):\s*([^/]+?)\s*/.*?\s(\d{4,6}) / (\S.*?)\s*$")
BA_RE = re.compile(r"\bBA (\d+): ([^/\n]+?)(?:\s+\d{4,6})?\s*/")
TOA_RE = re.compile(r"^\s*Total Obligation Authority \(\$ in Millions\)\s+(.*)$", re.M)
DATE_RE = re.compile(r"Date: ([A-Z][a-z]+) (\d{4})")
PB_RE = re.compile(r"PB (\d{4}) " + re.escape(P["budget"]["pb_label"]))
NUMBER = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")
MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}


def text_of(pdf: Path) -> str:
    return subprocess.run([PDFTOTEXT, "-layout", str(pdf), "-"], check=True, capture_output=True, text=True, errors="replace").stdout


def month_end(month: str, year: str) -> str:
    m = MONTHS[month]
    return f"{int(year):04d}-{m:02d}-{calendar.monthrange(int(year), m)[1]:02d}"


def amounts(tail: str, pb: str = "2027") -> dict[str, float | None]:
    """The twelve resource-summary columns as the row prints them: a number, or '-' and 'Cont' as none."""
    columns = columns_for(pb) if pb else COLUMNS
    values: list[float | None] = []
    for token in tail.split():
        values.append(float(token.replace(",", "")) if NUMBER.match(token) else None)
    values = (values + [None] * len(columns))[:len(columns)]
    return dict(zip(columns, values))


def request_years(a: dict) -> tuple[str, str]:
    """(the year before the request, the request total) as the amount keys name them: fy2026 and fy2027_total in a
    PB 2027 book, fy2025 and fy2026_total in a PB 2026 book."""
    total = next((k for k in a if k.endswith("_total")), "fy2027_total")
    return f"fy{int(total[2:6]) - 1}", total


FURNITURE = ("Exhibit P-40", "Appropriation /", "ID Code", "Line Item MDAP", "LI ", "Navy Page",
             "Exhibit R-2", "Appropriation/Budget Activity", "UNCLASSIFIED", "R-1 Program Element", "Total Program Element",
             "(The following Resource Summary", "Exhibit ID", "Type Title", "*Title represents", "Note: Totals in this Exhibit")
APPROPRIATION_LINE = re.compile(r"\d{4}[A-Z]?: .+ / BA ?\d")  # the header every page repeats: 1810N: Other Procurement, Navy / BA 01: ...


def prose(page: str) -> list[str]:
    """The justification sentences of a page: lines with words, not the table rows."""
    out = []
    for line in page.splitlines():
        s = re.sub(r"\s+", " ", line).strip()
        numeric = sum(bool(NUMBER.match(t)) or t == "-" for t in s.split())
        if len(s) >= 40 and numeric < 3 and not s.startswith(FURNITURE) and not APPROPRIATION_LINE.match(s):
            out.append(s)
    return out


def parse_book(text: str) -> tuple[dict, list[dict]]:
    """(book header, one record per P-1 line item) from the layout text of a justification book. A book can span
    budget activities (APN_BA1-4, SCN), so each line takes the activity its own pages print."""
    header: dict = {}
    lines: dict[str, dict] = {}
    names: dict[str, str] = {}  # each activity's name as its fullest page prints it; the layout cuts it where columns overlap
    for page in text.split("\f"):
        if P40 not in page:
            continue
        if not header:
            d, pb = DATE_RE.search(page), PB_RE.search(page)
            header = {"date": f"{d.group(1)} {d.group(2)}" if d else "", "published": month_end(d.group(1), d.group(2)) if d else "", "pb": pb.group(1) if pb else ""}
        ba = BA_RE.search(page)
        if ba:
            names[ba.group(1)] = max(names.get(ba.group(1), ""), re.sub(r"\s+", " ", ba.group(2)).strip(), key=len)
        if "budget_activity" not in header and ba:
            header["budget_activity"] = f"BA {ba.group(1)}: {ba.group(2).strip()}"
        li = next((LI_RE.match(line) for line in page.splitlines() if LI_RE.match(line)), None)
        if not li:
            continue
        appropriation, approp_name, number, title = li.groups()
        row = lines.setdefault(number, {"li": number, "exhibit": "P-40", "title": title, "appropriation": appropriation,
                                        "appropriation_name": approp_name.strip(), "pages": 0, "amounts": None, "text": [], "ba": ""})
        row["ba"] = row["ba"] or (ba.group(1) if ba else "")
        row["pages"] += 1
        toa = TOA_RE.search(page)
        if toa and row["amounts"] is None:
            row["amounts"] = amounts(toa.group(1), header.get("pb") or "2027")
        row["text"].extend(prose(page))
    for row in lines.values():
        row["text"] = " ".join(dict.fromkeys(row["text"]))[:12000]
        n = row.pop("ba")
        row["budget_activity"] = f"BA {n}: {names[n]}" if n else ""
    return header, sorted(lines.values(), key=lambda r: r["li"])


def parse_book_r2(text: str) -> tuple[dict, list[dict]]:
    """(book header, one record per R-1 program element) from the layout text of an RDT&E justification book. The
    R-2 page names the appropriation, the budget activity (its name wraps to the next line) and the program element;
    the "Total Program Element" row carries the twelve resource-summary columns the P-40 reader knows."""
    header: dict = {}
    lines: dict[str, dict] = {}
    for page in text.split("\f"):
        if R2 not in page or "Exhibit R-2A" in page:
            continue
        if not header:
            d, pb = DATE_RE.search(page), PB_RE.search(page)
            header = {"date": f"{d.group(1)} {d.group(2)}" if d else "", "published": month_end(d.group(1), d.group(2)) if d else "", "pb": pb.group(1) if pb else ""}
        rows = page.splitlines()
        pe = next((PE_RE.search(line) for line in rows if PE_RE.search(line)), None)
        if not pe:
            continue
        number, title = pe.group(1), " ".join(pe.group(2).split())
        appropriation, approp_name, ba_number, ba_name = "", "", "", ""
        for i, line in enumerate(rows):
            m = R2_BA_RE.match(line)
            if m:
                appropriation, approp_name, ba_number, ba_name = m.group(1), m.group(2).strip(), m.group(3), m.group(4).strip()
                # The budget activity name wraps under the left column: "BA 1: Basic" / "Research".
                if i + 1 < len(rows):
                    tail = rows[i + 1].strip()
                    if tail and len(tail) < 60 and not any(ch.isdigit() for ch in tail) and not tail.startswith("Prior"):
                        ba_name = f"{ba_name} {tail}".strip()
                break
        row = lines.setdefault(number, {"li": number, "exhibit": "R-2", "title": title, "appropriation": appropriation, "appropriation_name": approp_name,
                                        "budget_activity": f"BA {ba_number}: {ba_name}" if ba_number else "", "pages": 0, "amounts": None, "text": []})
        row["pages"] += 1
        total = TOTAL_PE_RE.search(page)
        if total and row["amounts"] is None:
            row["amounts"] = amounts(total.group(1), header.get("pb") or "2027")
        row["text"].extend(prose(page))
    for row in lines.values():
        row["text"] = " ".join(dict.fromkeys(row["text"]))[:12000]
    return header, sorted(lines.values(), key=lambda r: r["li"])


# A DHS congressional justification lists each appropriation's investments in a Capital Investment Exhibits table, one
# row each ("024_000009571 - Continuous Diagnostics and Mitigation  Level 1  IT  Yes  $265,279  $265,279  $331,000":
# acquisition level, IT or not, on the Major Acquisition Oversight List, then the book's three years in thousands). A
# long name wraps: its columns print on the next line and the rest of the name below them. An investment's own pages
# carry its name at the right of the page head; the page foot names the component and appropriation ("CISA – PC&I - 11").
CJ = "Capital Investment Exhibits"
CJ_PB_RE = re.compile(r"Fiscal Year (\d{4})\s+Congressional Justification")
CJ_ID = r"(N?\d{3}_\d{9}|N/A)"
CJ_COLUMNS = r"(?:(Level \d|Non-Major)\s+(IT|Non-IT)\s+(Yes|No)\s+)?((?:\$[\d,]+|-)\s+(?:\$[\d,]+|-)\s+(?:\$[\d,]+|-))"
CJ_ROW_RE = re.compile(rf"^\s*{CJ_ID} - (\S.*?)\s+{CJ_COLUMNS}\s*$")
CJ_START_RE = re.compile(rf"^\s*{CJ_ID} - (\S.*?)\s*$")
CJ_TAIL_RE = re.compile(rf"^\s*{CJ_COLUMNS}\s*$")
CJ_FOOT_RE = re.compile(r"^\s*(\S+) [–-] ([A-Z&]+) - \d+\s*$", re.M)
CJ_APPROPRIATIONS = {"PC&I": "Procurement, Construction, and Improvements", "R&D": "Research and Development",
                     "O&S": "Operations and Support", "FA": "Federal Assistance"}


def cj_amount(token: str) -> float | None:
    return None if token == "-" else float(token.strip("$").replace(",", "")) / 1000  # thousands, kept in millions like the other books


CJ_STOPS = ("Overall Investment Funding", "Investment Schedule", "Contract Information", "Significant Changes to Investment",
            "Construction Award Schedule")
# An investment's pages open its prose under one of these; a construction project or an end items purchase names its own.
CJ_DESCRIPTION_RE = re.compile(r"(?:Investment|End Items|Construction) Description")
CJ_SKIP_RE = re.compile(r"Dollars in Thousands|Full.Year CR|Annualized CR|President.s Budget|^\s*FY \d{4}\s+FY \d{4}|^\s*Justification\s*$")


def cj_key(name: str) -> str:
    """A name as the page head and the table both spell it: "Checkpoint Property Screening System" heads the pages of
    "CheckPoint Property Screening System", "Next Generation Network Priority Services – Phase 2" those of "Next
    Generation Networks Priority Services Phase 2", "Counter-Unmanned Aircraft Systems" those of "Counter Unmanned
    Aircraft System (C-UAS)"."""
    return re.sub(r"s\b", "", " ".join(re.sub(r"\([^)]*\)|[–—-]", " ", name.lower()).split()).removesuffix(" investment"))


def cj_description(row: dict, text: str) -> str:
    """What the book says the investment is and why it is funded: its own pages from the description heading to the
    funding and schedule tables, less the amount rows; for one without pages of its own (a line the request zeroes),
    the paragraph, bulleted or not, that opens with its name where its PPA is described."""
    body = "\n".join(row["text"])
    start = CJ_DESCRIPTION_RE.search(body)
    if start:
        body = body[start.end():]
        stop = min([i for i in (body.find(s) for s in CJ_STOPS) if i >= 0] or [len(body)])
        return " ".join(" ".join(line.split()) for line in body[:stop].splitlines()
                        if line.strip() and line.count("$") < 2 and not CJ_SKIP_RE.search(line))[:12000]
    said = re.search(rf"^\s*(?:•\s*)?{re.escape(row['title'])}(?: \([^)\n]*\))?: (.+?)(?:\n\s*\n|\Z)", text, re.M | re.S)
    return " ".join(said.group(1).split())[:1500] if said else ""


def parse_book_cj(text: str) -> tuple[dict, list[dict]]:
    """(book header, one record per capital investment) from the layout text of a DHS congressional justification. The
    same row repeats in the appropriation's table, its PPA's table and on the investment's own pages; a name that
    repeats with other amounts (an "End Items" row under two PPAs) is another investment."""
    pb = CJ_PB_RE.search(text[:6000])
    if not pb or CJ not in text:
        return {}, []
    y = int(pb.group(1))
    years = (f"fy{y - 2}", f"fy{y - 1}", f"fy{y}_total")
    rows: dict[tuple, dict] = {}
    pages, last = text.split("\f"), ("", "")
    for page in pages:
        foot = CJ_FOOT_RE.findall(page)
        last = foot[-1] if foot else last  # a page whose foot the layout drops continues the page before
        component, appropriation = last[0], {"PCI": "PC&I"}.get(last[1], last[1])  # FLETC prints PCI
        if CJ not in page:
            continue
        lines = page.splitlines()
        for i, line in enumerate(lines):
            m, start = CJ_ROW_RE.match(line), CJ_START_RE.match(line)
            tail = CJ_TAIL_RE.match(lines[i + 1]) if not m and start and i + 1 < len(lines) else None
            if not m and not tail:
                continue
            (uii, name), (level, it, maol, cols) = (m.groups()[:2], m.groups()[2:]) if m else (start.groups(), tail.groups())
            rest = lines[i + 2].strip() if tail and i + 2 < len(lines) else ""
            if rest and len(rest) < 60 and "$" not in rest and not CJ_START_RE.match(rest) and not CJ_FOOT_RE.match(rest):
                name = f"{name} {rest}"  # "Survey and Design - Vessels, Boats, and" / columns / "Aircraft"
            name = " ".join(name.split())
            if not re.search(r"[A-Za-z]", name):
                continue
            values = dict(zip(years, (cj_amount(t) for t in cols.split())))
            row = rows.setdefault((uii, name, tuple(values.values())), {
                "li": uii, "exhibit": "CJ", "title": name, "appropriation": f"{component} {appropriation}".strip(),
                "appropriation_name": CJ_APPROPRIATIONS.get(appropriation, appropriation), "component": component,
                "level": "", "it": "", "maol": "", "pages": 0, "amounts": values, "text": []})
            if level and not row["level"]:  # a row whose columns wrapped away from its level in one table has them in another
                row.update(level=level, it=it, maol=maol)
    by_title = defaultdict(list)
    for row in rows.values():
        by_title[cj_key(row["title"])].append(row)
    for page in pages:
        lines = [line for line in page.splitlines() if line.strip()]
        for row in by_title.get(cj_key(re.split(r"\s{2,}", lines[0].strip())[-1]) if lines else "", []):
            row["pages"] += 1
            row["text"] += [line for line in lines[1:] if not CJ_FOOT_RE.match(line)]  # the page less its head and foot
    out, used = sorted(rows.values(), key=lambda r: (r["appropriation"], r["li"], r["title"], str(r["amounts"]))), Counter()
    for row in out:
        row["text"] = cj_description(row, text)
        used[(row["appropriation"], row["li"])] += 1  # N/A rows, and one identifier over two aircraft (HC-144, HC-27J)
        n = used[(row["appropriation"], row["li"])]
        row["li"] += f"-{n}" if n > 1 else ""
    return {"date": "", "published": "", "pb": pb.group(1)}, out


R2A = "Exhibit R-2A, RDT&E Project Justification"
# a Navy prototype element counts the articles it buys: "($ in Millions, Article Quantities in Each)", an "Articles:" row per title
PROGRAMS_HEAD_RE = re.compile(r"Accomplishments/Planned Programs \(\$ in Millions(?:, Article Quantities in Each)?\)")
COLUMN_LABELS_RE = re.compile(r"\s*(?:FY \d{4}\s+)*(?:(?:Base|OOC|OCO|Total)\s*)+")
PROGRAM_TITLE_RE = re.compile(r"^\s*Title:\s*(\S.*?)\s{2,}((?:(?:-|\d[\d,]*\.\d+)\s+)*(?:-|\d[\d,]*\.\d+))\s*$")
# DARPA numbers a project TT-07, the Navy 1101
PROJECT_RE = re.compile(r"\bPE \d{7}[A-Z]? / .*?\s([A-Z0-9]{2,5}(?:-\d{2}[A-Z]?)?) / ")
PROGRAMS_END = ("Accomplishments/Planned Programs Subtotals", "C. Other Program Funding", "D. Acquisition Strategy", "E. Performance Metrics")
PART_RE = re.compile(r"\b(Description|FY \d{4} (?:Plans|Accomplishments)):\s*")
PAGE_FOOT_RE = re.compile(r"\bPage \d+ of \d+\b")
ACRONYM_RE = re.compile(r"\(([A-Za-z0-9][A-Za-z0-9 .&/+-]{1,20})\)\s*$")


def program_parts(text: str) -> dict[str, str]:
    """The block's parts as the book heads them: "Description", "FY 2026 Plans", "FY 2025 Accomplishments"."""
    marks = list(PART_RE.finditer(text))
    return {m.group(1): text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip() for i, m in enumerate(marks)}


def program_columns(head: str, below: str) -> list[str]:
    """The amount columns of a program table: "FY 2025 FY 2026 FY 2027" on the head line (DARPA), or the request year
    three times on the head line over "FY 2025 FY 2026 Base OOC Total" (the Navy)."""
    head_years = re.findall(r"FY (\d{4})", head)
    if not COLUMN_LABELS_RE.fullmatch(below):
        return [f"fy{y}" for y in head_years]
    request = head_years[-1] if head_years else ""
    return [f"fy{y}" for y in re.findall(r"FY (\d{4})", below)] + [f"fy{request}_{label.lower()}" for label in re.findall(r"Base|OOC|OCO|Total", below)]


def parse_programs(text: str) -> list[dict]:
    """One record per program an R-2A page funds ("Title: Advanced Research Concepts (ARC)  30.000  -  -"): its
    program element and project, the three years' amounts the block's head names, and its description (the need the
    book states) and plans, word for word. A block that runs onto the next page keeps collecting there."""
    out: list[dict] = []
    current, columns, pe, project = None, [], "", ""
    for page in text.split("\f"):
        if R2A not in page and R2 not in page:  # a one-project element prints its programs on the R-2 page itself
            current = None
            continue
        rows = page.splitlines()
        pe_line = next((PE_RE.search(line) for line in rows if PE_RE.search(line)), None)
        if pe_line and pe_line.group(1) != pe:
            pe, project, current = pe_line.group(1), "", None
        if R2A in page:
            project = next((m.group(1) for line in rows if (m := PROJECT_RE.search(line))), project)
        # A page's header (element, project, cost table) sits above its "B. Accomplishments/Planned Programs" head, which
        # every page of the section repeats, so a block running onto the next page collects only below that head.
        inside, labels = False, -1
        for i, line in enumerate(rows):
            s = " ".join(line.split())
            if PROGRAMS_HEAD_RE.search(s):
                below = rows[i + 1] if i + 1 < len(rows) else ""
                columns, inside = program_columns(s, below), True
                labels = i + 1 if COLUMN_LABELS_RE.fullmatch(below) else -1
                continue
            if not inside or i == labels:
                continue
            if s.startswith(PROGRAMS_END):
                current = None
                continue
            title = PROGRAM_TITLE_RE.match(line)
            values = title.group(2).split() if title else []
            if title and columns and len(values) == len(columns):
                name = " ".join(title.group(1).split())
                acronym = ACRONYM_RE.search(name)
                # the request year's base and overseas columns are parts of its total, kept as the year's one amount
                amounts = {c.removesuffix("_total"): None if v == "-" else float(v.replace(",", "")) for c, v in zip(columns, values) if not c.endswith(("_base", "_ooc", "_oco"))}
                current = {"pe": pe, "project": project, "title": name, "acronym": acronym.group(1) if acronym else "", "amounts": amounts, "text": []}
                out.append(current)
                continue
            if current is not None and s and not s.startswith((*FURNITURE, "PE ", "Volume ", "Articles:")) and not PAGE_FOOT_RE.search(s):
                if not current["text"]:  # a wrapped title's second line can carry the articles row: "Process (C5IMP)  -  -  -  -  -"
                    s = re.sub(rf"(?:(?:^|\s+)(?:-|\d[\d,]*)){{{len(columns)}}}$", "", s).strip()
                if s:
                    current["text"].append(s)
    for p in out:
        body = " ".join(p.pop("text"))
        parts = program_parts(body)
        # a title that wraps prints its second line above "Description:"
        lead = body[:PART_RE.search(body).start()].strip() if PART_RE.search(body) else ""
        if lead and len(lead) < 120:
            p["title"] = f"{p['title']} {lead}"
            p["acronym"] = p["acronym"] or (ACRONYM_RE.search(p["title"]).group(1) if ACRONYM_RE.search(p["title"]) else "")
        p["description"] = parts.get("Description", "")[:4000]
        p["plans"] = {k: v[:3000] for k, v in parts.items() if k != "Description"}
    return out


def parse(text: str) -> tuple[dict, list[dict]]:
    """A book's header and records; each reader skips the pages of the other exhibit."""
    header, rows = parse_book(text)
    header_r2, rows_r2 = parse_book_r2(text)
    header_cj, rows_cj = parse_book_cj(text)
    return header or header_r2 or header_cj, rows + rows_r2 + rows_cj


def event_type(row: dict) -> str:
    a = row["amounts"] or {}
    year_before, request = request_years(a)
    before, after = a.get(year_before), a.get(request)
    return "funding_change" if before is not None and after is not None and before != after else "budget_line"


def moved_to(row: dict) -> list[str]:
    """The lines a book says an unfunded line's work moved to: a zero request the book explains is a move, not a cut."""
    a = row["amounts"] or {}
    if not a or a.get(request_years(a)[1]):
        return []
    return list(dict.fromkeys(n for m in MOVED_RE.finditer(row["text"]) for n in MOVED_ID_RE.findall(m.group(1)) if n != row["li"]))


def money(v: float | None) -> str:
    return "none" if v is None else f"${v:,.3f}M"


def event_title(row: dict) -> str:
    a = row["amounts"] or {}
    year_before, request = request_years(a)
    moved = moved_to(row)
    return (f"{row['appropriation']} line {row['li']} {row['title']}: FY{year_before[2:]} {money(a.get(year_before))}, "
            f"FY{request[2:6]} {money(a.get(request))}" + (f", funding moved to {'PE' if row.get('exhibit') == 'R-2' else 'line'} {', '.join(moved)}" if moved else ""))


def manifest_row(path: str) -> dict:
    for line in MANIFEST.read_text(encoding="utf-8").splitlines() if MANIFEST.exists() else []:
        r = json.loads(line)
        if r.get("path") == path and r.get("sha256"):
            return r
    return {}


UPLOAD_RE = re.compile(r"/files/(\d{4})-(\d{2})/")


def noted_books() -> list[Path]:
    """The books the ledger records under a "budget book:" note naming this agency, wherever their bytes were saved."""
    out = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines() if MANIFEST.exists() else []:
        r = json.loads(line)
        note = r.get("note") or ""
        if kept_page(r) and note.startswith(BOOK_NOTE) and any(name in note for name in AGENCY_NAMES) and (ROOT / r["path"]).exists():
            out.append(ROOT / r["path"])
    return out

# ------------------------------------------------------------------ Exhibit R-2A: projects and named programs under a program element

# "0400 / 1     PE 0601101E / DEFENSE RESEARCH SCI  CCS-02 / MATH AND COMPUTER": the R-2A page header names the PE and the project
R2A_HEAD_RE = re.compile(r"PE (\d{7}[A-Z]?) / .*?\b([A-Z]{2,4}-\d{2}) / (\S.*?)\s*$")
# "CCS-02: MATH AND     0.000   180.734   0.000 ...": a project's cost row on the R-2 page, name then the twelve columns
R2_PROJECT_RE = re.compile(r"^\s*([A-Z]{2,4}-\d{2}): (.+?)\s{2,}((?:(?:-?\d[\d,]*\.\d+|-|Cont)\s+){3,}(?:-?\d[\d,]*\.\d+|-|Cont))\s*$")
TITLE_RE = re.compile(r"^\s*Title: (.*)$")
SUBTOTAL_RE = re.compile(r"Accomplishments/Planned Programs Subtotals\s+(.*)$")
FOOTER_RE = re.compile(r"Volume \d+ - (\d+)\s*$", re.M)
NUM_TOKEN = re.compile(r"(?<!\S)(?:-?\d{1,3}(?:,\d{3})*\.\d{3}|-)(?!\S)")


def program_years(pb: str) -> tuple[str, str, str]:
    """The three columns of an R-2A accomplishments table: the two years before the request and the request."""
    y = int(pb)
    return f"fy{y - 2}", f"fy{y - 1}", f"fy{y}"


def parse_book_r2a(text: str, pb: str = "2027") -> dict:
    """The projects under each program element (from the R-2 cost rows) and the named programs under each project (the
    R-2A "Title:" rows with their three fiscal-year amounts), each with the book page the footer prints. Amounts are the
    book's own; a Title row whose amounts wrap to the next line is read across the wrap; a row with no amounts is kept
    with `amounts_found` false. Subtotals are kept so a project's programs can be checked against the book's own sum."""
    years = program_years(pb)
    projects: dict[str, dict[str, dict]] = {}
    programs: list[dict] = []
    subtotals: list[dict] = []
    pe = proj = None
    for page in text.split("\f"):
        footers = FOOTER_RE.findall(page)
        page_no = int(footers[-1]) if footers else None
        rows = page.splitlines()
        if R2 in page and R2A not in page:
            found = next((PE_RE.search(line) for line in rows if PE_RE.search(line)), None)
            if found:
                pe = found.group(1)
            for line in rows:
                m = R2_PROJECT_RE.match(line)
                if m and pe:
                    projects.setdefault(pe, {})[m.group(1)] = {"project": m.group(1), "title": " ".join(m.group(2).split()),
                                                              "amounts": amounts(m.group(3), pb), "page": page_no}
            # A program element with a single project prints its programs on the R-2 page itself, under that project.
            proj = next(iter(projects[pe])) if pe and len(projects.get(pe, {})) == 1 else None
        elif R2A in page:
            head = next((R2A_HEAD_RE.search(line) for line in rows if R2A_HEAD_RE.search(line)), None)
            if head:
                pe, proj = head.group(1), head.group(2)
                projects.setdefault(pe, {}).setdefault(proj, {"project": proj, "title": " ".join(head.group(3).split()), "amounts": None, "page": page_no})
        else:
            continue
        i = 0
        while i < len(rows):
            line = rows[i]
            m = TITLE_RE.match(line)
            if m:
                rest = m.group(1)
                tokens = NUM_TOKEN.findall(rest)
                title = NUM_TOKEN.sub("", rest).strip()
                j = i
                while len(tokens) < 3 and j + 1 < len(rows) and j < i + 3:
                    j += 1
                    nxt = rows[j].strip()
                    if nxt.startswith("Description"):
                        break
                    more = NUM_TOKEN.findall(rows[j])
                    if more:
                        tokens += more
                        title = f"{title} {NUM_TOKEN.sub('', rows[j]).strip()}".strip()
                    elif nxt and not tokens:
                        title = f"{title} {nxt}"
                title = " ".join(title.split())
                values = [None if t == "-" else float(t.replace(",", "")) for t in tokens[-3:]] if len(tokens) >= 3 else [None, None, None]
                programs.append({"pe": pe, "project": proj, "program": title, "amounts": dict(zip(years, values)),
                                 "amounts_found": len(tokens) >= 3, "page": page_no})
            m = SUBTOTAL_RE.search(line)
            if m:
                tokens = NUM_TOKEN.findall(m.group(1))
                values = [None if t == "-" else float(t.replace(",", "")) for t in tokens[:3]]
                subtotals.append({"pe": pe, "project": proj, "amounts": dict(zip(years, values)), "page": page_no})
            i += 1
    return {"projects": projects, "programs": programs, "subtotals": subtotals}


# ------------------------------------------------------------------ the Comptroller display spreadsheets (Exhibit R-1, P-1)

# The profile's component in the Department-wide sheets: the account's service letter (1319N, 2040A, 3600F) and, for an
# agency funded inside the Defense-Wide account, the program element's suffix. A civilian profile has no filter.
DISPLAY = P["budget"].get("display")
DISPLAY_PROVIDER = "dod_comptroller_budget_materials"
DISPLAY_FILES = {"R-1": "r1_display.xlsx", "P-1": "p1_display.xlsx"}
MEASURES_OUT = EVENTS / "budget_measures.json"
FY_HEADER = re.compile(r"^FY (\d{4}) (.+?)\s*$")


def measure_key(header: str) -> tuple[str, str] | None:
    """A fiscal-year column's key from the Comptroller's own header, shortened but not renamed: "FY 2026 Discretionary
    Enacted" -> fy2026_enacted, "FY 2027 Discretionary Request" -> fy2027_request, "FY 2025 Actuals" -> fy2025_actual,
    "FY 2026 PL 119-21 Spend Plan" -> fy2026_spend_plan_pl119_21; a P-1 "... Quantity" column is a quantity, the rest amounts."""
    m = FY_HEADER.match(" ".join(str(header).split()))
    if not m:
        return None
    words = m.group(2).lower()
    kind = "quantity" if words.endswith("quantity") else "amount"
    for old, new in (("quantity", ""), ("amount", ""), ("discretionary", ""), ("reconcilation", "reconciliation"),
                     ("actuals", "actual"), ("pl 119-21 spend plan", "spend_plan_pl119_21")):
        words = words.replace(old, new)
    return f"fy{m.group(1)}_{re.sub(r'[^a-z0-9_]+', '_', words).strip('_')}", kind


def keep_row(rec: dict, filt: dict) -> bool:
    if filt.get("account_suffix") and not rec["account"].endswith(filt["account_suffix"]):
        return False
    if filt.get("pe_re") and not re.search(filt["pe_re"], rec["code"] or ""):
        return False
    return True


def read_display(path: Path, exhibit: str, filt: dict | None = None) -> tuple[dict, list[dict]]:
    """(the sheet and its columns, one record per spreadsheet row kept) from a saved display spreadsheet. Amounts are the
    sheet's thousands turned into $M; every record carries its row number so the cell can be found again."""
    from openpyxl import load_workbook  # noqa: PLC0415  (only a DoD profile reads a spreadsheet)
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book[book.sheetnames[0]]
    header: list[str] | None = None
    columns: dict[int, tuple[str, str, str]] = {}
    out: list[dict] = []
    read = 0
    for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        cells = ["" if c is None else " ".join(str(c).split()) for c in row]
        if header is None:
            if "Account" in cells and "Account Title" in cells:
                header = cells
                columns = {i: (*measure_key(h), h) for i, h in enumerate(header) if measure_key(h)}
            continue
        if not cells or not cells[0] or cells[0].startswith("Total"):
            continue
        read += 1
        rec_by_header = dict(zip(header, cells))
        get = lambda *names: next((v for n in names for h, v in rec_by_header.items() if h == n or h.startswith(n + " ")), "")
        rec = {"exhibit": exhibit, "row": number, "account": get("Account"), "account_title": get("Account Title"),
               "organization": get("Organization"), "budget_activity": get("Budget Activity"), "budget_activity_title": get("Budget Activity Title"),
               "line_number": get("Line Number"), "code": get("PE/BLI", "Budget Line Item"),
               "title": get("Program Element/Budget Line", "Budget Line Item (BLI) Title"),
               "sub_activity": get("BSA"), "sub_activity_title": get("Budget SubActivity (BSA) Title"),
               "cost_type": get("Cost Type"), "cost_type_title": get("Cost Type Title"),
               "add": get("Add/Non-Add") != "Non-Add", "in_toa": get("Include In TOA"),
               "measures": {}, "quantities": {}}
        for i, (key, kind, _) in columns.items():
            value = cells[i] if i < len(cells) else ""
            if kind == "amount":
                rec["measures"][key] = round(float(value.replace(",", "")) / 1000, 3) if NUMBER.match(value) else None
            elif NUMBER.match(value):
                rec["quantities"][key] = int(float(value.replace(",", "")))
        if filt is None or keep_row(rec, filt):
            out.append(rec)
    return {"exhibit": exhibit, "sheet": book.sheetnames[0], "columns": {key: head for key, _, head in columns.values()},
            "rows_read": read, "rows_kept": len(out)}, out


def display_lines(records: list[dict]) -> list[dict]:
    """One line per program element or budget line item as the sheet numbers it: the R-1 row itself; the P-1's "Add"
    cost-type rows summed, the rows kept under `rows` so the sum can be checked against the sheet."""
    lines: dict[tuple[str, str, str], dict] = {}
    for rec in records:
        key = (rec["exhibit"], rec["account"], rec["line_number"], rec["code"])  # a PE listed under two budget activities is two lines
        line = lines.setdefault(key, {"exhibit": rec["exhibit"], "account": rec["account"], "account_title": rec["account_title"],
                                      "organization": rec["organization"], "budget_activity": rec["budget_activity"],
                                      "budget_activity_title": rec["budget_activity_title"], "line_number": rec["line_number"],
                                      "code": rec["code"], "title": rec["title"], "measures": {}, "rows": []})
        line["rows"].append(rec["row"])
        if rec["add"]:
            for k, v in rec["measures"].items():
                if v is not None:
                    line["measures"][k] = round((line["measures"].get(k) or 0.0) + v, 3)
                else:
                    line["measures"].setdefault(k, None)  # a blank cell adds nothing; a line blank throughout stays None
    return [lines[k] for k in sorted(lines)]


def display_files() -> list[dict]:
    """The newest saved copy of each display spreadsheet the ledger holds, by its file name."""
    found: dict[str, dict] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines() if MANIFEST.exists() else []:
        r = json.loads(line)
        url = r.get("url") or ""
        for exhibit, name in DISPLAY_FILES.items():
            if url.endswith("/" + name) and kept_page(r) and (ROOT / r["path"]).exists():
                if exhibit not in found or r.get("retrieved_at", "") > found[exhibit].get("retrieved_at", ""):
                    found[exhibit] = {**r, "exhibit": exhibit}
    return [found[k] for k in sorted(found)]


def display(argv: list[str]) -> int:
    if not DISPLAY:
        print(f"the {P['short']} profile has no budget.display filter: the Comptroller display spreadsheets carry no rows for it")
        return 0
    files, lines = [], []
    for r in display_files():
        header, records = read_display(ROOT / r["path"], r["exhibit"], DISPLAY)
        files.append({"exhibit": r["exhibit"], "path": r["path"], "url": r["url"], "sha256": r["sha256"], "retrieved_at": r["retrieved_at"],
                      **header})
        for line in display_lines(records):
            lines.append({**line, "source": {"url": r["url"], "sha256": r["sha256"], "retrieved_at": r["retrieved_at"],
                                             "locator": {"sheet": header["sheet"], "rows": line["rows"]}}})
        print(f"{Path(r['path']).name}: {header['rows_kept']} of {header['rows_read']} rows are this profile's; {len(display_lines(records))} line(s)")
    MEASURES_OUT.parent.mkdir(parents=True, exist_ok=True)
    MEASURES_OUT.write_text(json.dumps({"provider": DISPLAY_PROVIDER, "filter": DISPLAY, "files": files, "lines": lines}, indent=1, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    return 0



def extract(argv: list[str]) -> int:
    books, records, programs = [], [], []
    found = sorted(BOOKS.glob("*_Book.pdf")) if BOOKS.exists() else []
    for pdf in found + [b for b in noted_books() if b not in found]:
        if pdf.stat().st_size == 0 or pdf.name.startswith("._") or "Highlights" in pdf.name:
            continue
        text = text_of(pdf)
        header, rows = parse(text)
        if not rows:
            print(f"{pdf.name}: no P-40, R-2 or capital investment exhibits (another exhibit kind; not read yet)")
            continue
        # the R-2 books' projects and named programs per line (the office owners read them); a book of another exhibit has none
        detail = parse_book_r2a(text, header.get("pb") or "2027") if R2 in text else {"projects": {}, "programs": [], "subtotals": []}
        for row in rows:
            row["projects"] = list(detail["projects"].get(row["li"], {}).values())
            row["programs"] = [p for p in detail["programs"] if p["pe"] == row["li"]]
        if detail["programs"]:
            print(f"{pdf.name}: {len(detail['programs'])} program row(s) under {sum(len(v) for v in detail['projects'].values())} project(s), "
                  f"{sum(1 for p in detail['programs'] if not p['amounts_found'])} without amounts")
        rel = str(pdf.relative_to(ROOT))
        record = manifest_row(rel)
        upload = UPLOAD_RE.search(record.get("url", ""))
        if not header.get("published") and upload:  # a DHS book prints no date: the month of its upload folder, not after it was saved
            month = calendar.month_name[int(upload.group(2))]
            header.update(date=f"{month} {upload.group(1)}", published=min(month_end(month, upload.group(1)), record.get("retrieved_at", "")[:10] or "9999"))
        book = {"path": rel, "url": record.get("fetched_from") or record.get("url", ""), "sha256": record.get("sha256", ""),
                "retrieved_at": record.get("retrieved_at", ""), **header, "lines": len(rows)}
        books.append(book)
        for row in rows:
            records.append({**row, "book": rel, "published": header["published"], "event_type": event_type(row), "moved_to": moved_to(row), "event_title": event_title(row)})
        programs += [{**p, "book": rel, "pb": header["pb"], "published": header["published"]} for p in parse_programs(text)]
        changed = sum(r["event_type"] == "funding_change" for r in records if r["book"] == rel)
        print(f"{pdf.name}: {len(rows)} line item(s), {changed} with FY2027 differing from FY2026, dated {header['date']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"provider": PROVIDER, "books": books, "lines": records, "programs": programs}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(programs)} program block(s) read from the R-2A pages")
    return display([]) if DISPLAY else 0


def show(argv: list[str]) -> int:
    needle = (argv[0] if argv else "MIDS").lower()
    for row in json.loads(OUT.read_text(encoding="utf-8"))["lines"]:
        if needle in row["title"].lower() or needle in row["text"].lower():
            print(row["event_title"])
    return 0


FIXTURE = """Exhibit P-40, Budget Line Item Justification: PB 2027 Navy                       Date: April 2026
Appropriation / Budget Activity / Budget Sub Activity:                        P-1 Line Item Number / Title:
1810N: Other Procurement, Navy / BA 02: Communications & Electronics Equip /  3415 / MIDS
BSA 4: Communications and Electronic Equipment
          Resource Summary                          Prior Years   FY 2025   FY 2026   FY 2027 Base   FY 2027 OOC   FY 2027 Total   FY 2028   FY 2029   FY 2030   FY 2031  To Complete   Total
Gross/Weapon System Cost ($ in Millions)                321.645     7.402    10.000       12.500        0.000        12.500      0.000      0.000     0.000     0.000        Cont       Cont
Total Obligation Authority ($ in Millions)              321.645     7.402    10.000       12.500        0.000        12.500      0.000      0.000     0.000     0.000        Cont       Cont
 Description:
 The Multifunctional Information Distribution System (MIDS) program procures Link 16 terminals for ships and aircraft.
\fExhibit P-40, Budget Line Item Justification: PB 2027 Navy                       Date: April 2026
1810N: Other Procurement, Navy / BA 02: Communications & Electronics Equip /  3415 / MIDS
 [P40A / BR001 MIDS LVT TERMINALS]: Procures MIDS Low Volume Terminals and Joint Tactical Radio System terminals for the fleet.
\fExhibit P-40, Budget Line Item Justification: PB 2027 Navy                       Date: April 2026
1810N: Other Procurement, Navy / BA 02: Communications & Electronics Equip /  2026 / SPQ-9B Radar
Total Obligation Authority ($ in Millions)              321.645     7.402     0.000        0.000        0.000         0.000      0.000      0.000     0.000     0.000           -     329.047
"""


R2A_PROJECT_FIXTURE = """Exhibit R-2, RDT&E Budget Item Justification: PB 2027 Defense Advanced Research Projects Agency          Date: April 2026
0400: Research, Development, Test & Evaluation, Defense-Wide / BA 2: Applied PE 0602024E / EXAMPLE TECHNOLOGY
                                        Prior     FY 2025   FY 2026   FY 2027   FY 2027   FY 2027   FY 2028   FY 2029   FY 2030   FY 2031   Cost To     Total
Total Program Element                    -        20.000    30.000    40.000    -         40.000    0.000     0.000     0.000     0.000     -           -
WP-01: WARFIGHTER                        -        12.000    18.000    24.000    -         24.000    0.000     0.000     0.000     0.000     -           -
PROTECTION
WP-02: WARFIGHTER                        -         8.000    12.000    16.000    -         16.000    0.000     0.000     0.000     0.000     -           -
                                                                                                                                 Volume 1 - 60
\fExhibit R-2A, RDT&E Project Justification: PB 2027 Defense Advanced Research Projects Agency                Date: April 2026
0400 / 2                                                        PE 0602024E / EXAMPLE TECHNOLOGY  WP-01 / WARFIGHTER PROTECTION
B. Accomplishments/Planned Programs ($ in Millions)                                                        FY 2025   FY 2026   FY 2027
Title: Example Program One (EPO)                                                                              7.000    10.000    14.000
Description: The first program.
Title: Example Program Two with a Long Name that Wraps
                                                                                                              5.000     8.000    10.000
Description: The second program.
                                                          Accomplishments/Planned Programs Subtotals         12.000    18.000    24.000
                                                                                                                                 Volume 1 - 61
"""


def selfcheck_r2a() -> None:
    d = parse_book_r2a(R2A_PROJECT_FIXTURE, "2027")
    assert sorted(d["projects"]["0602024E"]) == ["WP-01", "WP-02"], d["projects"]
    assert d["projects"]["0602024E"]["WP-01"]["title"] == "WARFIGHTER" and d["projects"]["0602024E"]["WP-01"]["amounts"]["fy2026"] == 18.0
    assert d["projects"]["0602024E"]["WP-01"]["page"] == 60
    programs = d["programs"]
    assert [p["program"] for p in programs] == ["Example Program One (EPO)", "Example Program Two with a Long Name that Wraps"], programs
    assert all(p["pe"] == "0602024E" and p["project"] == "WP-01" and p["page"] == 61 and p["amounts_found"] for p in programs)
    assert programs[1]["amounts"] == {"fy2025": 5.0, "fy2026": 8.0, "fy2027": 10.0}, "amounts wrapped to the next line are read"
    assert d["subtotals"][0]["amounts"]["fy2026"] == 18.0 == sum(p["amounts"]["fy2026"] for p in programs)
    assert program_years("2026") == ("fy2024", "fy2025", "fy2026")


def selfcheck_display() -> None:
    assert measure_key("FY 2026 Discretionary Enacted") == ("fy2026_enacted", "amount")
    assert measure_key("FY 2027 Discretionary Request") == ("fy2027_request", "amount")
    assert measure_key("FY 2025 Actuals Quantity") == ("fy2025_actual", "quantity")
    assert measure_key("FY 2026 PL 119-21 Spend Plan Amount") == ("fy2026_spend_plan_pl119_21", "amount")
    assert measure_key("FY 2025 Reconcilation Amount") == ("fy2025_reconciliation", "amount") and measure_key("Account") is None
    try:
        from openpyxl import Workbook  # noqa: PLC0415
    except ImportError:  # the reader is exercised where a DoD profile is built; the rules above hold everywhere
        return
    import tempfile  # noqa: PLC0415
    book = Workbook()
    sheet = book.active
    sheet.title = "Exhibit P-1"
    sheet.append(["Total of Displayed Rows", "", "", ""])
    sheet.append(["Account", "Account Title", "Organization", "Budget Activity", "Budget Activity Title", "Line Number", "Budget Line Item",
                  "Budget Line Item (BLI) Title", "Cost Type", "Cost Type Title", "Add/Non-Add", "FY 2025 Actuals Quantity", "FY 2025 Actuals Amount",
                  "FY 2026 Discretionary Enacted Amount", "FY 2027 Discretionary Request Amount"])
    sheet.append(["1810N", "Other Procurement, Navy", "N", "02", "Communications", "34", "3415N00000", "MIDS", "A", "Weapon System Cost", "Add", "12", "7402", "10000", "12500"])
    sheet.append(["1810N", "Other Procurement, Navy", "N", "02", "Communications", "34", "3415N00000", "MIDS", "B", "Less: Advance Procurement", "Add", "", "-402", "", ""])
    sheet.append(["1810N", "Other Procurement, Navy", "N", "02", "Communications", "34", "3415N00000", "MIDS", "C", "Memo entry", "Non-Add", "", "999", "999", "999"])
    sheet.append(["2031A", "Aircraft Procurement, Army", "A", "01", "Aircraft", "1", "9670A00005", "MQ-1 UAV", "A", "Weapon System Cost", "Add", "8", "240000", "", ""])
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "p1_display.xlsx"
        book.save(path)
        header, rows = read_display(path, "P-1", {"account_suffix": "N"})
        assert header["sheet"] == "Exhibit P-1" and header["rows_read"] == 4 and header["rows_kept"] == 3, header
        assert header["columns"]["fy2026_enacted"] == "FY 2026 Discretionary Enacted Amount"
        assert rows[0]["measures"] == {"fy2025_actual": 7.402, "fy2026_enacted": 10.0, "fy2027_request": 12.5} and rows[0]["quantities"] == {"fy2025_actual": 12}
        assert rows[0]["row"] == 3 and rows[2]["add"] is False
        lines = display_lines(rows)
        assert len(lines) == 1 and lines[0]["code"] == "3415N00000" and lines[0]["title"] == "MIDS" and lines[0]["rows"] == [3, 4, 5], lines
        assert lines[0]["measures"] == {"fy2025_actual": 7.0, "fy2026_enacted": 10.0, "fy2027_request": 12.5}, "Add rows summed, Non-Add left out, blanks add nothing"
        _, darpa_rows = read_display(path, "P-1", {"account_suffix": "D", "pe_re": r"\d{7}E$"})
        assert darpa_rows == []


R2A_FIXTURE = """Exhibit R-2A, RDT&E Project Justification: PB 2027 Defense Advanced Research Projects Agency     Date: April 2026
0400 / 2                                                 PE 0602702E / TACTICAL TECHNOLOGY  TT-07 / AERONAUTICS TECHNOLOGY
B. Accomplishments/Planned Programs ($ in Millions)                                      FY 2025        FY 2026        FY 2027
Title: Aircrew Labor In-Cockpit Automation                                                 12.000         8.500             -
System (ALIAS)
Description: Pilots carry too many tasks in the cockpit.
FY 2026 Plans:
- Fly the automation on a second aircraft.
\fExhibit R-2A, RDT&E Project Justification: PB 2027 Defense Advanced Research Projects Agency     Date: April 2026
0400 / 2                                                 PE 0602702E / TACTICAL TECHNOLOGY  TT-07 / AERONAUTICS TECHNOLOGY
                                                                        NOLOGY                 TECHNOLOGY
B. Accomplishments/Planned Programs ($ in Millions)                                      FY 2025        FY 2026        FY 2027
- Hand the system to the Army.
Title: Speed Program                                                                           -          1,020.250         3.000
Description: Fast things.
PE 0602702E: TACTICAL TECHNOLOGY                                   UNCLASSIFIED
Defense Advanced Research Projects Agency                            Page 4 of 9                         R-1 Line #20
Accomplishments/Planned Programs Subtotals                                                 12.000         9.500          3.000
"""


# A Navy R-2 page and an R-2A page: the request year's three columns head the program table over the other two years.
NAVY_R2_FIXTURE = """Exhibit R-2, RDT&E Budget Item Justification: PB 2027 Navy                                                                            Date: April 2026
Appropriation/Budget Activity                                                 R-1 Program Element (Number/Name)
1319: Research, Development, Test & Evaluation, Navy / BA 1: Basic            PE 0601103N / University Research Initiatives
Research
                                     Prior                                FY 2027     FY 2027      FY 2027                                                   Cost To           Total
      COST ($ in Millions)
                                     Years      FY 2025     FY 2026        Base        OOC          Total     FY 2028     FY 2029      FY 2030      FY 2031 Complete           Cost
Total Program Element                  -       113.248       68.947      0.000           -        0.000      0.000        0.000         0.000        0.000   Continuing   Continuing
\fExhibit R-2A, RDT&E Project Justification: PB 2027 Navy                                                                                             Date: April 2026
Appropriation/Budget Activity                                                        R-1 Program Element (Number/Name)                 Project (Number/Name)
1319 / 1                                                                             PE 0601103N / University Research Initiati        1101 / Multidisciplinary University Research
B. Accomplishments/Planned Programs ($ in Millions)                                                                                                 FY 2027 FY 2027 FY 2027
                                                                                                                          FY 2025 FY 2026            Base    OOC     Total
Title: Multidisciplinary University Research Initiative (MURI)                                                              67.676  51.117             0.000   0.000   0.000
Description: Five-year research grants on multidisciplinary problems.
\fExhibit R-2A, RDT&E Project Justification: PB 2027 Navy                                                                                     Date: April 2026
1319 / 1                                                                        PE 0601103N / University Research Initiati      1101 / Multidisciplinary University Research
B. Accomplishments/Planned Programs ($ in Millions)                                                                                         FY 2027     FY 2027    FY 2027
                                                                                                                   FY 2025      FY 2026      Base        OOC        Total
FY 2026 Plans:
Update the funding notice with new topics.
"""

# a DHS congressional justification: the capital investment table (one name wraps around its columns), an
# investment's own pages headed by its name spelled another way, a zeroed line described in a bullet of its PPA, and a
# construction project whose pages name their own description heading
CJ_FIXTURE = """Department of Homeland Security
Cybersecurity and Infrastructure Security Agency
Budget Overview
Fiscal Year 2027
Congressional Justification
\fCybersecurity and Infrastructure Security Agency                          Procurement, Construction, and Improvements
                                       Capital Investment Exhibits
                                          Capital Investments
                                         (Dollars in Thousands)
                                                           Acquisition   IT/      MAOL      FY 2025      FY 2026      FY 2027
024_000009571 - Continuous Diagnostics and Mitigation          Level 1    IT       Yes     $265,279     $265,279     $331,000
024_000009508 - National Cybersecurity Protection System       Level 1    IT       Yes      $30,000      $30,000            -
024_000009610 - Next Generation Networks Priority Services
                                                                Level 1    IT       Yes      $25,000      $25,000      $18,643
Phase 2
N/A - Construction and Facility Improvements End Items       Non-Major  Non-IT     No            -            -       $4,000
N024_000005948 - Perimeter Fencing                           Non-Major  Non-IT     No            -            -       $3,766
                                                                                        CISA – PC&I - 11
\fCybersecurity and Infrastructure Security Agency                          Procurement, Construction, and Improvements
•     National Cybersecurity Protection System (NCPS): No funding is requested in FY 2027 as the system reaches the end of
its life.

                                                                                        CISA – PC&I - 12
\fProcurement, Construction, and Improvements                          Next Generation Network Priority Services – Phase 2
Investment Description
NGN-PS Phase 2 is a major acquisition program developing priority data services.
Justification
The FY 2027 Budget funds data and video priority in the wireless networks.
                        FY 2025 Enacted     FY 2026 Annualized CR     FY 2027 President's Budget
Development                  $20,000              $20,000                  $15,000
                                                                                        CISA – PC&I - 30
\fProcurement, Construction, and Improvements                          Next Generation Network Priority Services – Phase 2
Overall Investment Funding
Operations and Support                             $1,000
                                                                                        CISA – PC&I - 31
\fProcurement, Construction, and Improvements                                              Perimeter Fencing
Construction Description
The FY 2027 Budget includes $3.8M to upgrade perimeter fencing.
Justification
A phased approach addresses the largest campus first.
Construction Award Schedule
 Contract Solicitation                                                            FY 2027 Q1
                                                                                        CISA – PC&I - 32
"""


def selfcheck() -> int:
    progs = parse_programs(R2A_FIXTURE)
    assert [p["title"] for p in progs] == ["Aircrew Labor In-Cockpit Automation System (ALIAS)", "Speed Program"], progs
    alias, speed = progs
    assert alias["acronym"] == "ALIAS" and alias["pe"] == "0602702E" and alias["project"] == "TT-07"
    assert alias["amounts"] == {"fy2025": 12.0, "fy2026": 8.5, "fy2027": None} and speed["amounts"]["fy2026"] == 1020.25
    assert alias["description"] == "Pilots carry too many tasks in the cockpit."
    assert alias["plans"] == {"FY 2026 Plans": "- Fly the automation on a second aircraft. - Hand the system to the Army."}, "a block runs onto the next page"
    assert "Subtotals" not in speed["description"]
    muri, = parse_programs(NAVY_R2_FIXTURE)
    assert (muri["pe"], muri["project"], muri["title"], muri["acronym"]) == ("0601103N", "1101", "Multidisciplinary University Research Initiative (MURI)", "MURI"), muri
    assert muri["amounts"] == {"fy2025": 67.676, "fy2026": 51.117, "fy2027": 0.0}, "the request year's total is its amount"
    assert muri["plans"] == {"FY 2026 Plans": "Update the funding notice with new topics."}, "the second head line is not block text"
    ba4 = NAVY_R2_FIXTURE.split("\f")[1].replace("($ in Millions)", "($ in Millions, Article Quantities in Each)").replace(
        "Research Initiative (MURI)          ", "Research                            ").replace(
        "Description: Five-year", "Initiative (MURI)" + " " * 80 + "-       2       -       -       -\n" + " " * 90 + "Articles:\nDescription: Five-year")
    muri, = parse_programs(ba4)
    assert muri["title"] == "Multidisciplinary University Research Initiative (MURI)" and muri["description"].startswith("Five-year"), muri
    header, rows = parse(FIXTURE + "\f" + NAVY_R2_FIXTURE)
    assert [(r["li"], r["exhibit"]) for r in rows] == [("2026", "P-40"), ("3415", "P-40"), ("0601103N", "R-2")], "one book, both exhibits"
    assert rows[2]["budget_activity"] == "BA 1: Basic Research" and rows[2]["amounts"]["fy2026"] == 68.947 and header["pb"] == "2027"
    assert event_title({**rows[2], "text": "Efforts in this PE will be funded in PE 0601153N."}).endswith("funding moved to PE 0601153N")
    # A book spanning activities: the last page opens BA 03, and the first MIDS page cuts the name into the line number.
    head, last = FIXTURE.rsplit("BA 02: Communications & Electronics Equip", 1)
    _, rows = parse_book((head + "BA 03: Aviation Support Equipment" + last).replace("Electronics Equip /  3415", "Electronics      3415", 1))
    assert [r["budget_activity"] for r in rows] == ["BA 03: Aviation Support Equipment", "BA 02: Communications & Electronics Equip"], rows
    ba = BA_RE.search("1109N: Procurement, Marine Corps / BA 04: Communications and Electronics        4644 / Common Aviation")
    assert ba.group(2) == "Communications and Electronics", ba.group(2)
    header, rows = parse_book(FIXTURE)
    assert header == {"date": "April 2026", "published": "2026-04-30", "pb": "2027", "budget_activity": "BA 02: Communications & Electronics Equip"}, header
    assert [r["li"] for r in rows] == ["2026", "3415"] and rows[1]["title"] == "MIDS" and rows[1]["pages"] == 2
    a = rows[1]["amounts"]
    assert a["prior_years"] == 321.645 and a["fy2026"] == 10.0 and a["fy2027_total"] == 12.5 and a["to_complete"] is None and a["total"] is None
    assert rows[0]["amounts"]["total"] == 329.047 and rows[0]["amounts"]["to_complete"] is None
    assert event_type(rows[1]) == "funding_change" and event_type(rows[0]) == "budget_line" and event_type({"amounts": None}) == "budget_line"
    assert "Link 16 terminals" in rows[1]["text"] and "MIDS Low Volume Terminals" in rows[1]["text"] and "Resource Summary" not in rows[1]["text"]
    assert event_title(rows[1]) == "1810N line 3415 MIDS: FY2026 $10.000M, FY2027 $12.500M"
    assert moved_to({**rows[0], "text": "Funding has been realigned out of line item 2026 into line item 2361 starting in FY 2026."}) == ["2361"] and moved_to(rows[1]) == []
    m = LI_RE.match("1810N: Other Procurement, Navy / BA                         2026 / SPQ-9B Radar")
    assert m and m.group(3) == "2026" and m.group(4) == "SPQ-9B Radar", m
    m = LI_RE.match("1810N: Other Procurement, Navy / B                                            2312 / AN/SLQ-32")
    assert m and m.group(4) == "AN/SLQ-32" and m.group(2) == "Other Procurement, Navy", m
    assert LI_RE.match("3010F: Aircraft Procurement, Air Force / BA 05: Modification / 2026 / F-35"), "any service letter"
    assert month_end("February", "2027") == "2027-02-28"
    header, rows = parse(CJ_FIXTURE)
    assert header == {"date": "", "published": "", "pb": "2027"}, header
    assert [r["li"] for r in rows] == ["024_000009508", "024_000009571", "024_000009610", "N/A", "N024_000005948"], "only the capital investment reader answers"
    ncps, cdm, ngn, end_items, fence = rows
    assert cdm["amounts"] == {"fy2025": 265.279, "fy2026": 265.279, "fy2027_total": 331.0} and (cdm["level"], cdm["maol"]) == ("Level 1", "Yes")
    assert cdm["appropriation"] == "CISA PC&I" and cdm["component"] == "CISA" and cdm["appropriation_name"] == "Procurement, Construction, and Improvements"
    assert ngn["title"] == "Next Generation Networks Priority Services Phase 2" and ngn["pages"] == 2, "the name wraps; its pages spell it another way"
    assert ngn["text"] == ("NGN-PS Phase 2 is a major acquisition program developing priority data services. "
                           "The FY 2027 Budget funds data and video priority in the wireless networks."), ngn["text"]
    assert event_title(ngn) == "CISA PC&I line 024_000009610 Next Generation Networks Priority Services Phase 2: FY2026 $25.000M, FY2027 $18.643M"
    assert ncps["text"].startswith("No funding is requested") and ncps["amounts"]["fy2027_total"] is None and event_type(ncps) == "budget_line"
    assert end_items["title"] == "Construction and Facility Improvements End Items" and end_items["amounts"]["fy2027_total"] == 4.0
    assert fence["pages"] == 1 and fence["text"] == ("The FY 2027 Budget includes $3.8M to upgrade perimeter fencing. "
                                                     "A phased approach addresses the largest campus first."), fence["text"]
    selfcheck_r2a()
    selfcheck_display()
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"extract": extract, "show": show, "display": display}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

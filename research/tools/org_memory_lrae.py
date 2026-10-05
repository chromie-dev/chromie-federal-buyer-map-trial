#!/usr/bin/env python3
"""Read the activity-wide LRAE releases into the organization memory.

    python research/tools/org_memory_lrae.py build      # rewrite the generated records in organization_seed.json
    python research/tools/org_memory_lrae.py collect    # fetch the pages the statements cite and the pages still wanted
    python research/tools/org_memory_lrae.py --check    # exit 1 when a build would change the file
    python research/tools/org_memory_lrae.py --selfcheck

A NAVSEA, ONR or NRL forecast row names its requirement office and its contracting office in
words the memory did not know, so no need bound to an organization. This tool reads those two
columns of every activity-wide release (lrae_package.RELEASES with scope "all") and writes what
the sheet states, and only that, as draft records tagged `generator`: one node per organization
the sheet names, an existence observation per release that names it, a child_of relationship
where the office cell states the parent (`PMS-392 - PMS-392 - NAVSEA`), and every observed
spelling as an alias, so lrae_package.classify_code resolves the row through the alias table
with no new logic.

What becomes a node: a contracting office (`N00164: NSWC Crane`), one per UIC however a release spells it; a program office, PEO,
directorate or DRPM whose code is its public handle (PMS 392, IWS 3.0, SEA 21C, PEO SHIPS,
DRPM-MIB); a department the sheet names (`ONR Code 34, Warfighter Performance`,
`7600 - Space Science DIV - NRL`); and a handful of cells that are a name as printed (NAMED).
A department code with no name (`PD-102 - NSWCPD`, `N66604-Code 25`, `KPT-20`) resolves to
its center and stays on the record, as NIWC competency codes do. A cell that names several
organizations, one outside the Department of the Navy, a fleet unit or a free-text department
stays unresolved with the reason, and the build prints those reasons with their row counts.

A second source, the NAVSEA HQ deputy program manager list (July 2025, a saved PDF), places
the NAVSEA program offices: the list groups its rows under headings (PEO CARRIERS, PEO IWS,
TEAM SHIPS (PEO SHIPS/ SEA21) ...) and DPM_HEADINGS names the organization each heading
stands for, so a PMS office gets its PEO as parent and a code the LRAE never printed (SEA 00C,
UDPIO) gets a node. A heading that names no PEO (TEAM SUBS) leaves its rows without a parent.
The list's columns are read from its layout text, which needs poppler's pdftotext on the path.

Names are the sources' words, never expansions (the memory format, 08 section 5). Every record
is `review_status: draft`. Rerunning replaces the generated records and leaves the hand-written
ones byte for byte; `drafted_by.on` is the latest release date read so a rebuild is identical.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import ROOT  # noqa: E402
from lrae_package import COMBINED, RELEASES, RESEARCH, fpds_entries, handles, manifest_rows, norm_code, read_sheet, saved  # noqa: E402
from fpds_sweep import OFFICES as SWEPT_OFFICES, saved_pages, windows  # noqa: E402

SEED = RESEARCH / "memory" / "organization_seed.json"
GENERATOR = "org_memory_lrae"
OFFICE_COLUMN = "Associated Program or Requirement Office"
UIC_COLUMN = "Contracting Office UIC"
UIC_CELL = re.compile(r"^([A-Z]\d{4}[A-Z0-9])\s*[:\-]?\s*(.*)$")
UIC_PREFIX = re.compile(r"^([A-Z]\d{4}[A-Z0-9])-(.+)$")
CENTER_SUFFIX = re.compile(r"^([A-Z]{2,4}-[A-Z0-9]+)-((?:NSWC|NUWC)[A-Z]+)$")
ONR_CODE = re.compile(r"^ONR (Code \d+|PMR-\d+),\s*(.+)$")
ONR_SLASH = re.compile(r"^Code (\d+) - Code \1/(.+) - ONR$")  # the combined release: `Code 032 - Code 032/Ocean Battlespace Sensing - ONR`
SITE_DEPT = re.compile(r"^([A-Z&]+)-(\d+(?:\.\d+)?) - \2(?: - |\s+)(.+)$")  # `PSNS-290 - 290 - Combat Systems ...`, `PSNS-101.1 - 101.1 NWRMC Everett`
PMS_CODE = re.compile(r"\bPMS[- ]?(\d{3}[A-Z]?)\b")
IWS_CODE = re.compile(r"^(?:NAVSEA\s+)?IWS[- ]?(\d{1,2})(?:\.(\d))?$")
IWS_LETTERS = re.compile(r"^IWS[\s-]+([A-Z]{1,3})$")
SEA_CODE = re.compile(r"^(?:NAVSEA|SEA)\s?(\d{2})([A-Z]{0,2})$")
PEO_CODE = re.compile(r"^PEO[ _]([A-Z]+)$")
DRPM_CODE = re.compile(r"^DRPM-([A-Z]+)$")
SEVERAL = re.compile(r"\s*[,;/]\s*")
PREFIX_STOP = re.compile(r"\s+(?:Code\b|C\d|NS\b)")

# The command a cell names as a parent (`- NAVSEA`) or as the office itself; names as printed.
COMMANDS = {"NAVSEA": ("command:navsea", "command", "NAVSEA"), "ONR": ("command:onr", "command", "ONR"),
            "NAVAIR": ("command:navair", "command", "NAVAIR"), "SSP": ("command:ssp", "command", "SSP")}
# Cells that are a name as printed, not a code the rules read. A string value points at a node
# another cell creates (the contracting column names MARMC and NUWCKPT).
NAMED = {
    "Hawaii Regional Maintenance Center": ("activity:hawaii-regional-maintenance-center", "field_activity", "Hawaii Regional Maintenance Center"),
    "NWRMC": ("activity:nwrmc", "field_activity", "NWRMC"),
    "Support MARMC": "activity:marmc",
    "NUWC Keyport": "center:nuwckpt",
    "TRF - Bangor": ("activity:trf-bangor", "field_activity", "TRF - Bangor"),
    "Naval Ordnance Safety & Security Activity (NOSSA)": ("activity:nossa", "field_activity", "Naval Ordnance Safety & Security Activity (NOSSA)"),
    "CAD/PAD Joint Program Office (JP)": ("office:cad-pad-jpo", "program_office", "CAD/PAD Joint Program Office (JP)"),
    "NavalX - DoN SBIR/Special Programs Division": ("office:navalx", "department", "NavalX - DoN SBIR/Special Programs Division"),
    "ONR Global": ("activity:onr-global", "field_activity", "ONR Global"),
}
# The node a release's activity is, so the full name its heading prints ('Activity Name: Office of Naval Research')
# becomes that node's alias.
ACTIVITY_NODE = {"onr": "command:onr", "nrl": "center:nrl"}
NON_DON = {"USMC", "OSD", "USSOCOM", "USAF", "USA", "USCG", "MDA", "DARPA", "DOD EA", "COAST GUARD", "FMS"}
FLEET = {"USN", "U.S. PACIFIC FLEET", "COMPACFLT"}
NO_OFFICE = {"", "TBD", "MULTIPLE", "N/A"}


# The NAVSEA HQ deputy program manager list: the manifest URL fragment, the date it states
# ("UPDATED July 2025", filed 22 July 2025) and, per heading it groups rows under, the
# organizations the heading names. An empty list is a grouping the source ties to no PEO.
DPM_LIST = "DPMs_HQ_NamesCodesPhoneNumbers"
DPM_DATE = "2025-07-22"
DPM_HEADINGS: dict[str, list[tuple[str, str, str]]] = {
    "TEAM SUBS": [],
    "PEO CARRIERS": [("peo:carriers", "program_executive_office", "PEO CARRIERS")],
    "TEAM SHIPS (PEO SHIPS/ SEA21)": [("peo:ships", "program_executive_office", "PEO SHIPS"), ("sea:21", "department", "SEA 21")],
    "TEAM SHIPS (PEO SHIPS/ SEA21) Con't": [("peo:ships", "program_executive_office", "PEO SHIPS"), ("sea:21", "department", "SEA 21")],
    "PEO IWS": [("peo:iws", "program_executive_office", "PEO IWS")],
    "PEO UNMANNED & SMALL COMBATANTS (USC)": [("peo:usc", "program_executive_office", "PEO UNMANNED & SMALL COMBATANTS (USC)")],
    "NAVSEA DIRECTORATES": [("command:navsea", "command", "NAVSEA")],
}
DPM_SKIP = ("Deputy Program Manager List - UPDATED", "Page ")
INFERRED_SITE = "the row's contracting office; the sheet does not state the parent, so this is the generator's inference from the two columns"
DEPARTMENT = "agency:don"
INFERRED_CENTER = ("the command's own forecast sheet names this center, or lists this activity as the office owning rows; "
                   "the sheet does not state the reporting line, so the parent is the generator's inference")
INFERRED_DEPARTMENT = ("the Department of the Navy's combined forecast carries this command's sheet as one of its activities; "
                       "the report does not state the reporting line in words, so the parent is the generator's inference")
INFERRED_HQ = ("the NAVSEA headquarters deputy program manager list groups this office's programs; the list does not state "
               "the reporting line, so the parent is the generator's inference")
# Statements an agent read off saved official pages: a node the page names, its parent, verbatim
# passages, and the sheet cells that name it. Checked against the saved bytes on every build.
STATEMENTS = RESEARCH / "memory" / "org_page_statements.json"
# A department the row names by name and code (`Cast Products & Explosives Division (M2)`), or a
# code of the site (`Code 980`, `C760 Divers`, `N31, Port Operations`), when the row's contracting
# office is a technical center or field activity: the office belongs to that site by inference.
NAMED_CODE = re.compile(r"^(?P<name>[A-Za-z][^()]*[A-Za-z&T.])\s*\((?P<code>[A-Z]{0,2}\d{1,4}[A-Z]?|[A-Z]{1,2})\)?$")
SITE_CODE = re.compile(r"^(?:(?P<before>[A-Za-z][A-Za-z ]*?)[-\s]+)?(?P<code>(?:Code|C)\s?/?\d{2,4}[A-Z]?(?:\.\d+)?)(?:,?\s+(?P<after>[A-Za-z].*))?$")
SITES = ("technical_center", "field_activity")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def spec(node_id: str, node_type: str, name: str, **codes) -> dict:
    return {"id": node_id, "type": node_type, "name": name, "codes": {k: v for k, v in codes.items() if v}}


def contracting_node(cell: str) -> tuple[dict, str] | None:
    """The node a `UIC: NAME` cell names, and the name as printed (empty when the cell is a bare UIC)."""
    m = UIC_CELL.match(" ".join(cell.split()))
    if not m:
        return None
    uic, name = m.group(1), m.group(2).strip()
    if not name or name == "HQ":  # `N00019 - HQ`: the headquarters contracting office of the code, no name of its own
        return spec(f"contracting:{uic.lower()}", "contracting_office", uic, uic=uic), ""
    if name.startswith(("NSWC", "NUWC")) or name == "NRL":
        return spec(f"center:{slug(name)}", "technical_center", name, contracting_uic=uic), name
    if name.endswith(" HQ"):
        return spec(f"contracting:{uic.lower()}", "contracting_office", name, uic=uic), name
    return spec(f"activity:{slug(name)}", "field_activity", name, contracting_uic=uic), name


def found(node: dict, parent: dict | None, alias: str, via: str, inferred: bool = False) -> dict:
    return {"node": node, "parent": parent, "alias": alias, "via": via, "inferred": inferred}


def miss(reason: str) -> dict:
    return {"unresolved": reason}


def coded(code: str, name: str, org: dict, table: dict) -> dict:
    """A code the cell ties to an organization: its own node when the code is a public handle."""
    known = table.get(norm_code(code)) if norm_code(code) else None
    if known:  # the cell still states the parent, unless the code is the organization itself
        return found(known, org if org["id"] != known["id"] else None, code, "alias table")
    m = PMS_CODE.fullmatch(code)
    if m:
        return found(spec(f"pms:{m.group(1).lower()}", "program_office", f"PMS {m.group(1)}", office_code=f"PMS {m.group(1)}"), org, code, "PMS code")
    m = IWS_CODE.match(code)
    if m:
        label = f"{m.group(1)}.{m.group(2)}" if m.group(2) else (f"{m.group(1)}.0" if len(m.group(1)) == 1 else m.group(1))
        return found(spec(f"iws:{label}", "program_office", f"IWS {label}", office_code=f"IWS {label}"), org, code, "IWS code")
    m = IWS_LETTERS.match(code)
    if m:
        return found(spec(f"iws:{m.group(1).lower()}", "program_office", f"IWS {m.group(1)}", office_code=f"IWS {m.group(1)}"), org, code, "IWS code")
    m = SEA_CODE.match(code)
    if m:
        label = f"{m.group(1)}{m.group(2)}"
        return found(spec(f"sea:{label.lower()}", "department", f"SEA {label}", office_code=f"SEA {label}"), org, code, "SEA code")
    m = PEO_CODE.match(code)
    if m:
        return found(spec(f"peo:{m.group(1).lower()}", "program_executive_office", f"PEO {m.group(1)}"), org, code, "PEO name")
    m = DRPM_CODE.match(code)
    if m:
        return found(spec(f"drpm:{m.group(1).lower()}", "direct_reporting_program_manager", code), org, code, "DRPM code")
    if name:
        return found(spec(f"dept:{slug(org['name'])}-{slug(code)}", "department", f"{org['name']} Code {code}, {name}", office_code=code),
                     org, code, "named department")
    dept = next((n for n in table.values() if n["id"] == f"dept:{org['id'].split(':', 1)[1]}-{slug(code)}"), None)
    if dept:  # `102 - 102 - NSWCIHD` in one release, `Infrastructure Division (102)` at N00174 in another
        return found(dept, org, code, f"department of {org['name']} with this code")
    if org["type"] == "command":  # `SURFMEPP - SURFMEPP - NAVSEA`: an office the rules cannot read is never the command's alias
        return found(org, None, "", f"code under {org['name']} the rules do not read; the code stays on the record")
    return found(org, None, code, f"department code of {org['name']}; the code stays on the record")


def site_department(cell: str, table: dict) -> dict | None:
    """A department a site prefixes with its own name and names after the code: `PSNS-290 - 290 - Combat Systems ...`.
    Its code keeps the prefix, as `NNSY-220` does, since shipyards and NRL share bare numbers."""
    m = SITE_DEPT.match(cell)
    if not m or not table.get(norm_code(m.group(1))):
        return None
    site, code = table[norm_code(m.group(1))], f"{m.group(1)}-{m.group(2)}"
    return found(spec(f"dept:{slug(site['name'])}-{slug(code)}", "department", f"{site['name']} Code {code}, {m.group(3)}", office_code=code),
                 site, cell, "department of the site the cell names")


def onr_department(code: str, name: str, alias: str) -> dict:
    """An ONR department, one node however a release pads its code: `ONR Code 32` and `Code 032` are one department,
    and a PMR code keeps its letters."""
    code = f"Code {int(code):02d}" if code.isdigit() else code
    return found(spec(f"dept:onr-{slug(code)}", "department", f"ONR {code}, {name}", office_code=f"ONR {code}"),
                 spec(*COMMANDS["ONR"]), alias, "ONR department")


def resolve(text: str, table: dict, by_uic: dict, here: dict | None = None) -> dict:
    """What one requirement-office cell names. `table` maps norm_code(alias) to a node spec; `here`
    is the row's contracting office, the site a named department or a bare code belongs to by
    inference when nothing in the cell names an organization."""
    cell = " ".join(text.split())
    if cell.upper() in NO_OFFICE:
        return miss("no office stated")
    if cell in COMMANDS:
        return found(spec(*COMMANDS[cell]), None, cell, "command")
    head = cell.split(" - ")[0].strip()  # the token classify_code looks up, so also the alias
    known = table.get(norm_code(cell)) or table.get(norm_code(head)) if norm_code(head) else None
    if known and " - " not in cell:
        return found(known, None, head, "alias table")
    m = ONR_SLASH.match(cell) or ONR_CODE.match(cell)
    if m:
        return onr_department(m.group(1) if m.re is ONR_SLASH else m.group(1).removeprefix("Code "), m.group(2), cell if m.re is ONR_SLASH else head)
    parts = [p.strip() for p in cell.split(" - ")]
    if len(parts) >= 3:
        if len(parts) == 4 and parts[2] == f"{parts[0]}-{parts[1]}":  # `KPT - 20 - KPT-20 - NUWCKPT`
            parts = [parts[2], parts[2], parts[3]]
        code, org_name, name = parts[0], parts[-1], (parts[2] if len(parts) == 4 else "")
        org = table.get(norm_code(org_name)) or (spec(*COMMANDS[org_name]) if org_name in COMMANDS else None)
        if org is None and known:  # `NNSY - Norfolk Naval Shipyard - HQ`: the code is known, the last part names no organization
            return found(known, None, head, "alias table; the cell's last part names no organization")
        if org is None:
            return site_department(cell, table) or miss(f"parent organization {org_name} is not in the contracting column or the command list")
        hit = coded(code, name, org, table)
        hit["alias"] = head if hit["alias"] else ""
        return hit
    if known:
        return found(known, None, head, "alias table")
    m = UIC_PREFIX.match(cell)
    if m and m.group(1) in by_uic:
        return found(by_uic[m.group(1)], None, head, "UIC of the center; the department code stays on the record")
    m = CENTER_SUFFIX.match(cell)
    if m and table.get(norm_code(m.group(2))):
        return found(table[norm_code(m.group(2))], None, head, "center named after the department code")
    if IWS_CODE.match(cell) or IWS_LETTERS.match(cell) or SEA_CODE.match(cell) or PEO_CODE.match(cell) or DRPM_CODE.match(cell):
        hit = coded(cell, "", spec(*COMMANDS["NAVSEA"]), table)
        hit["parent"] = None  # a bare code states no parent
        return hit
    pms = set(PMS_CODE.findall(cell))
    if len(pms) == 1 and not SEVERAL.search(cell):
        code = pms.pop()
        return found(spec(f"pms:{code.lower()}", "program_office", f"PMS {code}", office_code=f"PMS {code}"), None, head, "PMS code in the cell")
    prefix = PREFIX_STOP.split(cell)[0].strip()
    if prefix != cell and norm_code(prefix) and table.get(norm_code(prefix)):
        return found(table[norm_code(prefix)], None, head, "activity named before a code or a place")
    if here is not None and here["type"] in SITES:
        site = here["id"].split(":", 1)[1]
        m = NAMED_CODE.match(cell)
        if m:
            return found(spec(f"dept:{site}-{slug(m['code'])}", "department", cell, office_code=m["code"]), here, head,
                         "department named with its code; the site is the row's contracting office", inferred=True)
        m = SITE_CODE.match(cell)
        if m and (m["before"] or m["after"]):
            return found(spec(f"dept:{site}-{slug(m['code'])}", "department", cell, office_code=m["code"]), here, head,
                         "code named with a name; the site is the row's contracting office", inferred=True)
        if m:
            return found(here, None, head, "code of the row's contracting office; the code stays on the record", inferred=True)
    pieces = SEVERAL.split(cell)
    if len(pieces) > 1 and any(norm_code(p) and (norm_code(p) in table or p in COMMANDS or p.upper() in NON_DON
                                                  or PMS_CODE.fullmatch(p) or SEA_CODE.match(p)) for p in pieces):
        return miss("names several organizations")
    if cell.upper() in NON_DON or cell.startswith("USSOCOM"):
        return miss("outside the Department of the Navy")
    if cell.upper() in FLEET or cell.startswith("USS "):
        return miss("fleet unit, not an acquisition organization")
    return site_department(cell, table) or miss("free text the rules do not read")


def hand_table(seed: dict) -> dict[str, dict]:
    """norm_code(alias) -> node spec for the hand-written nodes, the same way alias_map builds it."""
    table: dict[str, dict] = {}
    for node in seed["nodes"]:
        if node["type"] == "person" or node.get("generator"):
            continue
        for text in handles(node):
            table.setdefault(norm_code(text), spec(node["id"], node["type"], node["name"], **(node.get("codes") or {})))
    return table


def squash(text: str) -> str:
    """Text as compared for a verbatim passage: entities decoded, curly quotes straight, one space."""
    return " ".join(html.unescape(text).replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"').split())


def page_text(path: Path) -> str:
    """The visible text of a saved page, including its title, in squash() form."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    return squash(re.sub(r"<[^>]+>", " ", raw))


def pdf_text(path: Path) -> str:
    """The layout text of a PDF, columns kept, through poppler's pdftotext (pypdf's layout mode
    does not keep this list's columns)."""
    if not shutil.which("pdftotext"):
        raise SystemExit("pdftotext (poppler) is not on the path; the deputy program manager list cannot be read")
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def dpm_rows(text: str) -> list[tuple[str, str, str, str]]:
    """(heading, title, code cell, description) per row of the list's layout text. Each page's header
    line gives the column positions; a line with nothing in the title column is a heading, and a
    heading DPM_HEADINGS does not know stops the build so a new edition is read, not guessed."""
    rows: list[tuple[str, str, str, str]] = []
    heading, cols = "", None
    for line in text.splitlines():
        if "TITLE" in line and "CODE" in line and "DESCRIPTION" in line:
            cols = (line.index("PHONE"), line.index("CODE"), line.index("DESCRIPTION"))
            continue
        flat = " ".join(line.split())
        if not flat or cols is None or flat.startswith(DPM_SKIP):
            continue
        phone_x, code_x, desc_x = cols
        if not line[:phone_x].strip():
            if flat not in DPM_HEADINGS:
                raise SystemExit(f"deputy program manager list: heading {flat!r} is not in DPM_HEADINGS")
            heading = flat
            continue
        rows.append((heading, line[:phone_x].strip(), line[code_x:desc_x].strip(), line[desc_x:].strip()))
    return rows


def dpm_node(code_cell: str, parents: list[dict], table: dict) -> tuple[dict, dict | None, dict | None]:
    """The office a code cell of the list names, its parent, and the parent's own parent when the cell
    created it. The parent is the directorate in parentheses when the cell states one (`PMS 326B
    (SEA 21)`), else the heading's organization whose code prefixes this one (`SEA 21A` under
    TEAM SHIPS goes to SEA 21), else the heading's first organization. A handle the code rules do
    not read (SHIPS GEM, UDPIO) is its own office, named as printed."""
    code, _, paren = code_cell.partition("(")
    code, paren = code.strip(), paren.strip(" )")
    navsea = spec(*COMMANDS["NAVSEA"])
    grand = None
    if paren:
        hit = coded(paren, "", navsea, table)
        parent, grand = hit["node"], hit["parent"]
    else:
        parent = next((p for p in parents if p["codes"].get("office_code")
                       and norm_code(code).startswith(norm_code(p["codes"]["office_code"]))), parents[0] if parents else None)
    node = coded(code, "", parent or navsea, table)["node"]
    if node["id"] == (parent or navsea)["id"]:
        node = spec(f"office:{slug(code)}", "program_office", code, office_code=code)
    return node, parent, grand


def source_revision(source: dict) -> tuple[str, str | None]:
    if source.get("wayback_timestamp"):
        return f"wayback capture {source['wayback_timestamp']}; sha256 {source['sha256'][:12]}", f"wayback capture {source['wayback_timestamp']}"
    return f"direct retrieval {source['retrieved_at'][:10]}; sha256 {source['sha256'][:12]}", None


class Memory:
    """The generated records, accumulated over the releases and written sorted."""

    def __init__(self, seed: dict):
        self.table = hand_table(seed)
        self.hand_ids = {n["id"] for n in self.table.values()}
        self.by_uic: dict[str, dict] = {}
        self.nodes: dict[str, dict] = {}
        self.aliases: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.node_obs: dict[str, set[str]] = defaultdict(set)
        self.parents: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.observations: list[dict] = []
        self.latest = ""
        self.unresolved: Counter = Counter()
        self.resolved_rows = 0
        self.inferred: dict[tuple[str, str], str] = {}  # edge -> why the generator, not a source, states it
        self.stated: set[tuple[str, str]] = set()  # edges a source states; one release stating it outweighs another's inference
        self.from_fpds: set[str] = set()
        self.sheet_of: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # center -> command whose sheet names it -> observations
        self.buys_for: dict[tuple[str, str], set[str]] = defaultdict(set)  # (contracting office, office) -> observations
        self.named_at: dict[str, tuple[str, str]] = {}  # office -> (release date, release key) that named it
        self.hand_children = {r["from"] for r in seed.get("relationships", []) if r.get("type") == "child_of" and r.get("generator") != GENERATOR}

    def add_node(self, node: dict) -> None:
        if node["id"] in self.nodes:
            return
        if node["id"] in self.hand_ids:
            return  # a hand-written node; the observation still cites it
        self.nodes[node["id"]] = node
        self.table.setdefault(norm_code(node["name"]), node)

    def contracting(self, cell: str) -> tuple[dict, str] | None:
        """The node a contracting cell names and the name as printed. The office code is the identity: a code already
        known keeps its node, so `N00167: NSWCCD` in one release and `N00167: NSWC Carderock` in another are one office."""
        hit = contracting_node(cell)
        if not hit:
            return None
        node, name = hit
        uic = node["codes"].get("uic") or node["codes"].get("contracting_uic")
        return self.by_uic.get(uic) or self.table.get(norm_code(uic)) or node, name

    def register(self, node: dict, alias: str, obs_id: str) -> None:
        self.node_obs[node["id"]].add(obs_id)
        if alias and norm_code(alias) and norm_code(alias) != norm_code(node["name"]):
            self.aliases[node["id"]][alias].add(obs_id)
            self.table.setdefault(norm_code(alias), node)

    def node_by_id(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id) or next((n for n in self.table.values() if n["id"] == node_id), None)

    def observe(self, obs_id: str, kind: str, passage: str, subjects: list[str], source: dict, observed_at: str) -> str:
        revision, access = source_revision(source)
        self.observations.append({"id": obs_id, "source_url": source["url"], "source_revision": revision, "observed_at": observed_at,
                                  "statement_type": kind, "passage": passage, "subject_ids": subjects, "access": access,
                                  "generator": GENERATOR})
        return obs_id

    def parent_of(self, child: dict, parent: dict, obs_id: str) -> None:
        self.parents[(child["id"], parent["id"])].add(obs_id)
        self.node_obs[child["id"]].add(obs_id)
        self.node_obs[parent["id"]].add(obs_id)  # a parent named only as a parent still has its citation

    def read_pages(self, statements: list[dict], manifest: list[dict]) -> None:
        """Statements read off saved official pages: the node, its parent, the passages (checked
        verbatim against the saved bytes) and the sheet cells that name it, which route through the
        table so the LRAE pass records them as aliases with the sheet's own citation."""
        for st in statements:
            source = saved(manifest, lambda m, u=st["source_url"]: m.get("url") == u)
            if source is None:
                raise SystemExit(f"{st['id']}: {st['source_url']} is not saved")
            text = page_text(ROOT / source["path"])
            for passage in st["passages"]:
                if squash(passage) not in text:
                    raise SystemExit(f"{st['id']}: passage is not on the saved page: {passage!r}")
            node = spec(st["node"]["id"], st["node"]["type"], st["node"]["name"], **st["node"].get("codes", {}))
            self.add_node(node)
            passage = " | ".join(st["passages"])
            obs_id = self.observe(f"obs:page:{slug(node['id'])}", "existence", passage, [node["id"]], source, source["retrieved_at"][:10])
            for alias in st.get("aliases", []):
                self.register(node, alias, obs_id)
            for cell in st.get("resolves", []):
                self.table.setdefault(norm_code(cell), node)
            if st.get("parent"):
                self.stated.add((node["id"], st["parent"]))
                self.parent_of(node, {"id": st["parent"]},
                               self.observe(f"obs:page:{slug(node['id'])}:parent", "parentage", passage, [node["id"], st["parent"]], source, source["retrieved_at"][:10]))

    def read_fpds_offices(self, manifest: list[dict], today: date) -> None:
        """A contracting office the FPDS sweep covers and no other source names: the office ID and the name the feed
        prints on the newest saved sweep page, so that office's awards bind to a node and not to nothing; a release that
        printed only the ID (`N00019 - HQ`) leaves the node for the feed to name. A headquarters office is printed under
        its command's own name, so the node leads with the ID: a document naming the command must not resolve to its
        contracting office."""
        known = set(self.by_uic) | {code for n in list(self.table.values()) + list(self.nodes.values())
                                    for code in ((n.get("codes") or {}).get("uic"), (n.get("codes") or {}).get("contracting_uic")) if code}
        bare = {n["id"]: n for n in self.nodes.values() if n["name"] == (n.get("codes") or {}).get("uic")}
        for uic in SWEPT_OFFICES:
            if uic in known and f"contracting:{uic.lower()}" not in bare:
                continue
            page = next((p for w in reversed(windows(today)) for p in saved_pages(manifest, uic, w)[0]), None)
            names = [e["contracting_office_name"] for e in fpds_entries((ROOT / page["path"]).read_bytes())
                     if e["contracting_office"] == uic and e["contracting_office_name"]] if page else []
            if not names:
                continue
            node = spec(f"contracting:{uic.lower()}", "contracting_office", f"{uic} - {names[0]}", uic=uic)
            if node["id"] in bare:
                bare[node["id"]]["name"] = node["name"]
                node = bare[node["id"]]
            self.add_node(node)
            self.from_fpds.add(node["id"])
            self.register(node, "", self.observe(f"obs:fpds:{uic.lower()}", "existence", f'contractingOfficeID name="{names[0]}": {uic}',
                                                 [node["id"]], page, page["retrieved_at"][:10]))

    def read_dpm(self, source: dict) -> None:
        """The NAVSEA HQ deputy program manager list: one office per code cell, under the organization
        its parenthesis or its heading names; every code cell is an alias of its office."""
        lines_of: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
        parent_of: dict[str, dict] = {}
        navsea = self.node_by_id("command:navsea") or spec(*COMMANDS["NAVSEA"])
        for heading, title, code_cell, desc in dpm_rows(pdf_text(ROOT / source["path"])):
            parents = [self.node_by_id(pid) or spec(pid, ptype, pname) for pid, ptype, pname in DPM_HEADINGS[heading]]
            for peo in (p for p in parents if p["type"] == "program_executive_office"):
                # The list is NAVSEA headquarters' own; a PEO whose programs it groups sits under NAVSEA by inference.
                if not any(child == peo["id"] for child, _ in self.parents) and peo["id"] not in parent_of:
                    self.add_node(peo)
                    lines_of[peo["id"]].append((heading, title, code_cell, desc))
                    parent_of[peo["id"]] = navsea
                    self.inferred[(peo["id"], navsea["id"])] = INFERRED_HQ
            node, parent, grand = dpm_node(code_cell, parents, self.table)
            self.add_node(node)
            lines_of[node["id"]].append((heading, title, code_cell, desc))
            if parent is not None:
                if grand is not None and self.node_by_id(parent["id"]) is None:  # the cell created the directorate too
                    lines_of[parent["id"]].append((heading, title, code_cell, desc))
                    parent_of[parent["id"]] = grand
                    self.add_node(grand)
                self.add_node(parent)
                parent_of[node["id"]] = parent
        for node_id, lines in sorted(lines_of.items()):
            node = self.node_by_id(node_id)
            passage = "; ".join(f"under '{h}': '{t} | {c} | {d}'" for h, t, c, d in lines)
            obs_id = self.observe(f"obs:dpm:{slug(node_id)}", "existence", passage, [node_id], source, DPM_DATE)
            for _, _, code_cell, _ in lines:
                alias = code_cell.partition("(")[0].strip()
                self.register(node, alias if norm_code(alias) == norm_code(node["codes"].get("office_code", "")) else "", obs_id)
            parent = parent_of.get(node_id)
            if parent is not None:
                self.parent_of(node, parent, self.observe(f"obs:dpm:{slug(node_id)}:parent", "parentage", passage, [node_id, parent["id"]], source, DPM_DATE))

    def read(self, release: dict, source: dict, rows: list[dict], release_date: str, activity_name: str = "") -> None:
        key = release["key"]
        self.latest = max(self.latest, release_date)

        def observe(obs_id: str, kind: str, passage: str, subjects: list[str]) -> str:
            return self.observe(obs_id, kind, passage, subjects, source, release_date)

        # Contracting offices first: the office rules need them as parents and as UIC targets.
        contracting: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            hit = self.contracting(r["contracting_office_uic"])
            if hit:
                node, name = hit
                self.add_node(node)
                uic = node["codes"].get("uic") or node["codes"].get("contracting_uic")
                self.by_uic.setdefault(uic, node)
                contracting[node["id"]][" ".join(r["contracting_office_uic"].split())] += 1
        for node_id, cells in sorted(contracting.items()):
            node = self.nodes.get(node_id) or next(n for n in self.table.values() if n["id"] == node_id)
            passage = f"column '{UIC_COLUMN}': " + "; ".join(f"'{c}' ({n} rows)" for c, n in sorted(cells.items()))
            obs_id = observe(f"obs:lrae:{key}:{slug(node_id)}:contracting", "existence", passage, [node_id])
            self.named(release, node, obs_id)
            for cell in cells:
                name = contracting_node(cell)[1]
                # `N00039: NAVWAR` prints the command's name for its contracting office: the observation quotes the
                # cell, and the name stays the command's alias.
                self.register(node, name if self.table.get(norm_code(name), node)["id"] == node["id"] else "", obs_id)
        for text in NAMED.values():
            if isinstance(text, str):
                assert text in self.nodes or text in {n["id"] for n in self.table.values()}, f"NAMED points at {text}, which no cell creates"
        for cell, target in NAMED.items():
            node = target if isinstance(target, tuple) else None
            node = spec(*node) if node else next(n for n in list(self.nodes.values()) + list(self.table.values()) if n["id"] == target)
            self.table.setdefault(norm_code(cell), node)  # the node itself is created when a cell resolves to it

        offices: dict[str, Counter] = defaultdict(Counter)
        buys: dict[tuple[str, str], Counter] = defaultdict(Counter)  # (contracting office, office) -> contracting cells
        parents: dict[tuple[str, str], Counter] = defaultdict(Counter)
        aliases: dict[str, dict[str, str]] = defaultdict(dict)
        context: dict[str, str] = {}  # cell -> the contracting cell it was placed by, for inferred hits
        for r in rows:
            cell = " ".join(r["office_code_string"].split())
            site = self.contracting(r["contracting_office_uic"])
            here = self.node_by_id(site[0]["id"]) if site else None
            hit = resolve(cell, self.table, self.by_uic, here)
            if "unresolved" in hit:
                self.unresolved[(hit["unresolved"], cell or "(blank)")] += 1
                continue
            self.resolved_rows += 1
            node = hit["node"]
            self.add_node(node)
            self.rename(node, key, release_date)
            offices[node["id"]][cell] += 1
            aliases[node["id"]][cell] = hit["alias"]
            if site and site[0]["id"] != node["id"]:
                buys[(site[0]["id"], node["id"])][" ".join(r["contracting_office_uic"].split())] += 1
            if hit["inferred"]:
                context[cell] = " ".join(r["contracting_office_uic"].split())
            if hit["parent"]:
                self.add_node(hit["parent"])
                parents[(node["id"], hit["parent"]["id"])][cell] += 1
                if hit["inferred"]:
                    self.inferred[(node["id"], hit["parent"]["id"])] = INFERRED_SITE
                else:
                    self.stated.add((node["id"], hit["parent"]["id"]))

        def cited(cells: Counter) -> str:
            return f"column '{OFFICE_COLUMN}': " + "; ".join(
                f"'{c}' ({n} rows" + (f"; column '{UIC_COLUMN}': '{context[c]}'" if c in context else "") + ")" for c, n in sorted(cells.items()))

        for node_id, cells in sorted(offices.items()):
            node = self.node_by_id(node_id)
            obs_id = observe(f"obs:lrae:{key}:{slug(node_id)}", "existence", cited(cells), [node_id])
            self.named(release, node, obs_id, owner=True)
            for cell in cells:
                self.register(node, aliases[node_id][cell], obs_id)
        for (buyer, office), cells in sorted(buys.items()):
            passage = f"rows whose column '{OFFICE_COLUMN}' names {self.node_by_id(office)['name']} carry column '{UIC_COLUMN}': " + \
                "; ".join(f"'{c}' ({n} rows)" for c, n in sorted(cells.items()))
            self.buys_for[(buyer, office)].add(observe(f"obs:lrae:{key}:{slug(office)}:bought-by:{slug(buyer)}", "contracting", passage, [buyer, office]))
        for (child, parent), cells in sorted(parents.items()):
            passage = cited(cells)
            self.parent_of({"id": child}, {"id": parent}, observe(f"obs:lrae:{key}:{slug(child)}:parent", "parentage", passage, [child, parent]))
        self.department(release, source, release_date)
        node = self.node_by_id(ACTIVITY_NODE.get(release["activity"], ""))
        if node and activity_name:
            obs_id = observe(f"obs:lrae:{key}:{slug(node['id'])}:activity", "existence", f"sheet heading 'Activity Name: {activity_name}'", [node["id"]])
            self.register(node, activity_name, obs_id)

    def rename(self, node: dict, key: str, release_date: str) -> None:
        """The newest release's name for an office wins; the name an older release printed stays its alias, citing that
        release's observation. ONR Code 32 was 'Ocean Battlespace and Expeditionary Access' in December 2025 and is
        'Ocean Battlespace Sensing' in July 2026."""
        kept = self.nodes.get(node["id"])
        if kept is None:
            return  # a hand-written node keeps its name
        was = self.named_at.setdefault(node["id"], (release_date, key))
        if kept is not node and kept["name"] != node["name"] and release_date > was[0]:
            self.aliases[node["id"]][kept["name"]].add(f"obs:lrae:{was[1]}:{slug(node['id'])}")
            kept["name"] = node["name"]
            self.table.setdefault(norm_code(kept["name"]), kept)
            self.named_at[node["id"]] = (release_date, key)

    def named(self, release: dict, node: dict, obs_id: str, owner: bool = False) -> None:
        command = COMMANDS.get(release["activity"].upper())
        if command and (node["type"] == "technical_center" or owner and node["type"] == "field_activity"):
            self.sheet_of[node["id"]][command[0]].add(obs_id)

    def place_centers(self) -> None:
        """A technical center one command's own forecast sheet names, and no source places, sits under that command; so
        does a field activity that sheet lists as the office owning rows. A field activity named only as a contracting
        site does not: the NAVSEA sheet also names fleet units and stations that are not NAVSEA's."""
        placed = {child for child, _ in self.parents} | self.hand_children
        said = {o["id"]: o for o in self.observations}
        for center, by in sorted(self.sheet_of.items()):
            if len(by) == 1 and center not in placed:
                (command, obs_ids), = by.items()
                self.inferred[(center, command)] = INFERRED_CENTER
                for obs_id in sorted(obs_ids):  # the parentage rests on the same cells of the command's sheet
                    self.observations.append({**said[obs_id], "id": f"{obs_id}:command", "statement_type": "parentage", "subject_ids": [center, command]})
                    self.parent_of({"id": center}, {"id": command}, f"{obs_id}:command")

    def department(self, release: dict, source: dict, release_date: str) -> None:
        """A command whose sheet the department's combined forecast carries sits under the department."""
        command = COMMANDS.get(release["activity"].upper())
        if release["release_note"] == COMBINED and command and command[0] in self.nodes:
            self.inferred[(command[0], DEPARTMENT)] = INFERRED_DEPARTMENT
            self.parent_of({"id": command[0]}, {"id": DEPARTMENT}, self.observe(
                f"obs:lrae:{release['key']}:{slug(command[0])}:department", "parentage",
                f"sheet '{release['sheet']}' of the Department of the Navy's combined LRAE report", [command[0], DEPARTMENT], source, release_date))

    def records(self) -> tuple[list[dict], list[dict], list[dict]]:
        nodes = []
        for node_id in sorted(self.nodes):
            node = self.nodes[node_id]
            out = {"id": node_id, "type": node["type"], "name": node["name"],
                   "aliases": [{"text": text, "observation_ids": sorted(ids)} for text, ids in sorted(self.aliases[node_id].items())]}
            if node["codes"]:
                out["codes"] = node["codes"]
            out.update({"observation_ids": sorted(self.node_obs[node_id]),
                        "notes": f"drafted by {GENERATOR} from the contracting office FPDS prints on the swept awards; the office ID, then the name as printed"
                        if node_id in self.from_fpds else
                        f"drafted by {GENERATOR} from the LRAE office and contracting-office columns, the NAVSEA deputy program "
                                 "manager list and statements read off saved official pages; name as printed in the source, not expanded",
                        "review_status": "draft", "reviewed_by": None, "generator": GENERATOR})
            nodes.append(out)
        relationships = []
        observed = {o["id"]: o["observed_at"] for o in self.observations}
        for (child, parent), obs_ids in sorted(self.parents.items()):
            inferred = (child, parent) in self.inferred and (child, parent) not in self.stated
            if inferred:
                note = self.inferred[(child, parent)]
            elif all(o.startswith("obs:dpm:") for o in obs_ids):
                note = "the NAVSEA deputy program manager list of July 2025 names this parent; not re-verified since"
            elif all(o.startswith("obs:page:") for o in obs_ids):
                note = "the organization's official page names this parent; not re-verified since"
            else:
                note = "the office column of the latest cited release names this parent; not re-verified since"
            relationships.append({
                "id": f"rel:lrae:{slug(child)}:{slug(parent)}", "type": "child_of", "from": child, "to": parent,
                "effective_from": None, "effective_to": None, "effective_dates_status": "unknown", "scope_as_stated": None,
                "observation_ids": sorted(obs_ids), "evidence_class": "inferred" if inferred else "directly_documented",
                "current_status": {"state": "last_confirmed", "as_of": max(observed[o] for o in obs_ids), "note": note},
                "review_status": "draft", "drafted_by": {"actor": "assistant", "on": self.latest},
                "reviewed_by": None, "reviewed_on": None, "retraction": None, "generator": GENERATOR})
        for (buyer, office), obs_ids in sorted(self.buys_for.items()):
            relationships.append({
                "id": f"rel:lrae:contracts:{slug(buyer)}:{slug(office)}", "type": "contracts_for", "from": buyer, "to": office,
                "effective_from": None, "effective_to": None, "effective_dates_status": "unknown", "scope_as_stated": None,
                "observation_ids": sorted(obs_ids), "evidence_class": "directly_documented",
                "current_status": {"state": "last_confirmed", "as_of": max(observed[o] for o in obs_ids),
                                   "note": "the contracting column of the latest cited release names this office for the office's rows; not re-verified since"},
                "notes": "the forecast rows this office owns name this contracting office", "review_status": "draft",
                "drafted_by": {"actor": "assistant", "on": self.latest}, "reviewed_by": None, "reviewed_on": None, "retraction": None,
                "generator": GENERATOR})
        return nodes, sorted(self.observations, key=lambda o: o["id"]), relationships


def collisions(seed: dict) -> list[str]:
    """Alias texts that normalize to one key but name two nodes; classify_code would pick the first."""
    owner: dict[str, str] = {}
    clashes = []
    for node in seed["nodes"]:
        if node["type"] == "person":
            continue
        for text in handles(node):
            key = norm_code(text)
            if key and owner.setdefault(key, node["id"]) != node["id"]:
                clashes.append(f"{text!r} -> {owner[key]} and {node['id']}")
    return clashes


def build_seed(seed: dict) -> tuple[dict, Memory]:
    memory = Memory(seed)
    manifest = manifest_rows()
    if STATEMENTS.exists():
        memory.read_pages(json.loads(STATEMENTS.read_text(encoding="utf-8"))["statements"], manifest)
    for release in RELEASES:
        if release["scope"] != "all":
            continue
        source = saved(manifest, lambda m, rel=release: rel["match"] in m.get("url", "") and m.get("mime", "").endswith("sheet"))
        if source is None:
            raise SystemExit(f"{release['key']}: spreadsheet bytes not found under data/raw")
        meta, rows = read_sheet(ROOT / source["path"], release["key"], release["sheet"], release["header_row"])
        release_date = meta.get("Release Date", "")[:10] if re.match(r"\d{4}-\d{2}-\d{2}", meta.get("Release Date", "")) else release["release_date"]
        memory.read(release, source, rows, release_date, meta.get("Activity Name", ""))
    dpm = saved(manifest, lambda m: DPM_LIST in m.get("url", "") and m.get("mime") == "application/pdf")
    if dpm is None:
        raise SystemExit("the NAVSEA deputy program manager list is not under data/raw")
    memory.read_dpm(dpm)
    memory.read_fpds_offices(manifest, date.today())
    memory.place_centers()
    nodes, observations, relationships = memory.records()
    out = dict(seed)
    out["nodes"] = [n for n in seed["nodes"] if n.get("generator") != GENERATOR] + nodes
    out["observations"] = [o for o in seed["observations"] if o.get("generator") != GENERATOR] + observations
    out["relationships"] = [r for r in seed["relationships"] if r.get("generator") != GENERATOR] + relationships
    clashes = collisions(out)
    if clashes:
        raise SystemExit("alias collisions:\n  " + "\n  ".join(clashes))
    return out, memory


def dumps(seed: dict) -> str:
    return json.dumps(seed, indent=1, ensure_ascii=False) + "\n"


def build(check: bool) -> int:
    text = SEED.read_text(encoding="utf-8")
    seed = json.loads(text)
    assert dumps(seed) == text, "organization_seed.json is not in the indent=1 form this tool writes; not touching it"
    out, memory = build_seed(seed)
    new = dumps(out)
    generated = sum(1 for n in out["nodes"] if n.get("generator") == GENERATOR)
    print(f"{generated} generated nodes, {sum(1 for o in out['observations'] if o.get('generator') == GENERATOR)} observations, "
          f"{sum(1 for r in out['relationships'] if r.get('generator') == GENERATOR)} relationships; "
          f"{memory.resolved_rows} rows resolved, {sum(memory.unresolved.values())} not")
    by_reason: Counter = Counter()
    for (reason, _), n in memory.unresolved.items():
        by_reason[reason] += n
    for reason, n in by_reason.most_common():
        cells = sorted(((c, k) for (r, c), k in memory.unresolved.items() if r == reason), key=lambda ck: (-ck[1], ck[0]))
        print(f"  {n:4d}  {reason}: " + ", ".join(f"{c} ({k})" for c, k in cells[:6]) + (" ..." if len(cells) > 6 else ""))
    if new == text:
        print("organization_seed.json unchanged")
        return 0
    if check:
        print("organization_seed.json would change; run `org_memory_lrae.py build`")
        return 1
    SEED.write_text(new, encoding="utf-8")
    print("organization_seed.json rewritten")
    return 0


def selfcheck() -> int:
    table: dict[str, dict] = {}
    by_uic: dict[str, dict] = {}
    for cell in ("N00164: NSWC Crane", "N64498: NSWCPD", "N00178: NSWCDD", "N00253 NUWCKPT", "N63394: NSWCPHD", "N66604: Newport",
                 "N55236: SWRMC", "N40027: SERMC", "N50054: MARMC", "N00024: NAVSEA HQ", "N00014", "N00173 - NRL", "N62793: SSNN"):
        node, name = contracting_node(cell)
        by_uic[node["codes"].get("uic") or node["codes"]["contracting_uic"]] = node
        if name:
            table[norm_code(name)] = node
    assert by_uic["N00164"]["id"] == "center:nswc-crane" and by_uic["N00164"]["type"] == "technical_center"
    assert by_uic["N00014"] == spec("contracting:n00014", "contracting_office", "N00014", uic="N00014")
    assert by_uic["N00173"]["id"] == "center:nrl" and by_uic["N00253"]["id"] == "center:nuwckpt"
    assert by_uic["N00024"]["id"] == "contracting:n00024" and by_uic["N66604"] == spec("activity:newport", "field_activity", "Newport", contracting_uic="N66604")
    for cell, target in NAMED.items():
        table[norm_code(cell)] = spec(*target) if isinstance(target, tuple) else next(n for n in table.values() if n["id"] == target)
    table[norm_code("PEO IWS")] = spec("peo:iws", "program_executive_office", "Program Executive Office Integrated Warfare Systems")

    def r(cell):
        return resolve(cell, table, by_uic)

    hit = r("PMS-392 - PMS-392 - NAVSEA")
    assert (hit["node"]["id"], hit["parent"]["id"], hit["alias"]) == ("pms:392", "command:navsea", "PMS-392"), hit
    assert r("PMS 312")["node"]["id"] == "pms:312" and r("PMS 312")["parent"] is None
    assert r("VIRGINIA Class Program Office PMS 450")["node"]["id"] == "pms:450"
    assert r("PMS408 JEOD")["node"]["id"] == "pms:408" and r("PMS-400D - PMS-400D - NAVSEA")["node"]["id"] == "pms:400d"
    assert r("PD-102 - PD-102 - NSWCPD")["node"]["id"] == "center:nswcpd" and r("PD-102 - PD-102 - NSWCPD")["alias"] == "PD-102"
    table[norm_code("Infrastructure Division (102)")] = spec("dept:nswcpd-102", "department", "Infrastructure Division (102)", office_code="102")
    assert r("102 - 102 - NSWCPD")["node"]["id"] == "dept:nswcpd-102" and r("102 - 102 - NSWCPD")["parent"]["id"] == "center:nswcpd"
    memory = Memory({"nodes": []})
    memory.add_node(spec(*COMMANDS["NAVSEA"]))
    sheet = {"url": "https://example.test/x.xlsx", "sha256": "0" * 64, "retrieved_at": "2026-07-13T00:00:00Z"}
    memory.department({"key": "k", "activity": "navsea", "release_note": COMBINED, "sheet": "NAVSEA"}, sheet, "2026-07-13")
    assert memory.parents[("command:navsea", DEPARTMENT)] == {"obs:lrae:k:command-navsea:department"} \
        and memory.inferred[("command:navsea", DEPARTMENT)] == INFERRED_DEPARTMENT, "the combined forecast places its commands under the department"
    memory.sheet_of["center:x"]["command:navsea"].add("obs:x")
    memory.sheet_of["center:y"]["command:navsea"].add("obs:y")
    memory.sheet_of["center:y"]["command:onr"].add("obs:y2")
    memory.hand_children.add("center:z")
    memory.sheet_of["center:z"]["command:navsea"].add("obs:z")
    navsea = {"activity": "navsea"}
    memory.named(navsea, spec("activity:owner", "field_activity", "Owner"), "obs:o", owner=True)
    memory.named(navsea, spec("activity:site", "field_activity", "Site"), "obs:s")
    for obs_id in ("obs:x", "obs:y", "obs:y2", "obs:z", "obs:o", "obs:s"):
        memory.observe(obs_id, "existence", "column 'X': 'cell' (1 rows)", [], sheet, "2026-07-13")
    memory.place_centers()
    assert memory.parents[("center:x", "command:navsea")] == {"obs:x:command"} and memory.inferred[("center:x", "command:navsea")] == INFERRED_CENTER
    assert next(o for o in memory.observations if o["id"] == "obs:x:command")["statement_type"] == "parentage", "a placed edge cites a parentage statement"
    assert ("activity:owner", "command:navsea") in memory.parents and not any(c == "activity:site" for c, _ in memory.parents), \
        "an activity the sheet lists as owning rows sits under the command; one named only as a contracting site does not"
    assert not any(c in ("center:y", "center:z") for c, _ in memory.parents), "two commands' sheets, or a stated parent, leave a center alone"
    memory = Memory({"nodes": []})
    memory.by_uic.update(by_uic)
    assert memory.contracting("N00164 - NSWCCR")[0]["id"] == "center:nswc-crane"  # the code, not the spelling, is the office
    assert contracting_node("N00019 - HQ") == (spec("contracting:n00019", "contracting_office", "N00019", uic="N00019"), "")
    table[norm_code("NNSY")] = spec("activity:nnsy", "field_activity", "NNSY")
    assert r("IWS-X - IWS-X - NAVSEA")["node"]["id"] == "iws:x"
    assert r("SURFMEPP - SURFMEPP - NAVSEA")["node"]["id"] == "command:navsea" and r("SURFMEPP - SURFMEPP - NAVSEA")["alias"] == ""
    assert r("NNSY - Norfolk Naval Shipyard - HQ")["node"]["id"] == "activity:nnsy" and r("NNSY - Norfolk Naval Shipyard - HQ")["parent"] is None
    assert r("KPT - 20 - KPT-20 - NUWCKPT")["node"]["id"] == "center:nuwckpt" and r("KPT - 20 - KPT-20 - NUWCKPT")["alias"] == "KPT"
    assert r("NSWCPHD - NSWC Port Hueneme - NSWCPHD")["node"]["id"] == "center:nswcphd"
    assert r("PHD-E60-NSWCPHD")["node"]["id"] == "center:nswcphd"
    hit = r("7600 - 7600 - Space Science DIV - NRL")
    assert (hit["node"]["id"], hit["node"]["name"], hit["parent"]["id"]) == ("dept:nrl-7600", "NRL Code 7600, Space Science DIV", "center:nrl"), hit
    table.update(hand_table({"nodes": [{"id": "agency:don", "type": "agency", "name": "Department of the Navy", "codes": {"fpds_agency_id": "1700"}}]}))
    assert r("1700 - 1700 - Laboratory for Autonomous Systems Research DIV - NRL")["node"]["id"] == "dept:nrl-1700", "FPDS's agency id is no office code"
    hit = r("ONR Code 34, Warfighter Performance")
    assert (hit["node"]["id"], hit["node"]["type"], hit["parent"]["id"]) == ("dept:onr-code-34", "department", "command:onr"), hit
    assert r("ONR PMR-51, Office of Low Observable")["node"]["id"] == "dept:onr-pmr-51"
    assert r("Code 032 - Code 032/Ocean Battlespace Sensing - ONR")["node"]["id"] == "dept:onr-code-32", "the combined release pads ONR codes"
    assert r("Code 0332 - Code 0332/Materials - ONR")["node"]["id"] == "dept:onr-code-332" and r("ONR Code 02, Contracts")["node"]["id"] == "dept:onr-code-02"
    table[norm_code("PSNS")] = spec("activity:psns-imf", "field_activity", "PSNS&IMF", contracting_uic="N4523A")
    hit = r("PSNS-1100 - 1100 - Executive Department")
    assert (hit["node"]["id"], hit["parent"]["id"], hit["node"]["codes"]["office_code"]) == ("dept:psns-imf-psns-1100", "activity:psns-imf", "PSNS-1100"), hit
    assert r("PSNS-101.3 - 101.3 NWRMC Everett")["node"]["id"] == "dept:psns-imf-psns-101-3"
    assert r("N66604-Code 25")["node"]["id"] == "activity:newport" and r("N66604-Code 25")["alias"] == "N66604-Code 25"
    assert r("N00167-NSWC CARDEROCK - Code 70")["unresolved"], "a UIC the contracting column never printed stays unresolved"
    assert r("SWRMC Code 300")["node"]["id"] == "activity:swrmc" and r("SWRMC Code 300")["alias"] == "SWRMC Code 300"
    assert r("SERMC NS MAYPORT, FL")["node"]["id"] == "activity:sermc"
    assert r("Support MARMC")["node"]["id"] == "activity:marmc" and r("NUWC Keyport Code 107")["node"]["id"] == "center:nuwckpt"
    assert r("NWRMC C101.3")["node"]["id"] == "activity:nwrmc" and r("Hawaii Regional Maintenance Center")["node"]["id"].startswith("activity:hawaii")
    assert r("NAVSEA IWS2")["node"]["id"] == "iws:2.0" and r("IWS 12.0")["node"]["id"] == "iws:12.0" and r("IWS-80 - IWS-80 - NAVSEA")["node"]["id"] == "iws:80"
    assert r("IWS-3.0 - IWS-3.0 - NAVSEA")["parent"]["id"] == "command:navsea" and r("IWS 3.0")["parent"] is None
    assert r("IWS ESS")["node"]["id"] == "iws:ess"
    assert r("SEA21C - SEA21C - NAVSEA")["node"] == spec("sea:21c", "department", "SEA 21C", office_code="SEA 21C")
    assert r("NAVSEA 05")["node"]["id"] == "sea:05" and r("SEA 05")["node"]["id"] == "sea:05" and r("SEA05TU - SEA05TU - NAVSEA")["node"]["id"] == "sea:05tu"
    assert r("PEO_CARRIERS - PEO CARRIERS - NAVSEA")["node"]["id"] == "peo:carriers" and r("PEO CARRIERS")["node"]["id"] == "peo:carriers"
    assert r("PEO IWS")["node"]["id"] == "peo:iws", "a hand-written node wins over the rules"
    assert r("PEO SSN - PEO SSN - NAVSEA")["node"]["type"] == "program_executive_office"
    assert r("DRPM-MIB - DRPM-MIB - NAVSEA")["node"]["id"] == "drpm:mib"
    assert r("NAVSEA")["node"]["id"] == "command:navsea" and r("SSP")["node"]["id"] == "command:ssp" and r("NAVAIR")["node"]["id"] == "command:navair"
    assert r("TRF - Bangor")["node"]["id"] == "activity:trf-bangor" and r("TRF - Bangor")["alias"] == "TRF"
    assert r("CAD/PAD Joint Program Office (JP)")["node"]["id"] == "office:cad-pad-jpo"
    assert r("ONR Global")["node"]["id"] == "activity:onr-global"
    for cell, reason in (("TBD", "no office stated"), ("", "no office stated"), ("Multiple", "no office stated"),
                         ("PMS 397, 392, 405", "names several organizations"), ("PMS420/495", "names several organizations"),
                         ("NAVSEA, NAVAIR, USMC", "names several organizations"), ("PMS 312; SSNN Code152A", "names several organizations"),
                         ("NAVAIR, FMS", "names several organizations"), ("NAVSEA IWS2, COAST GUARD", "names several organizations"),
                         ("USMC", "outside the Department of the Navy"), ("USSOCOM PEO Maritime", "outside the Department of the Navy"),
                         ("USS California", "fleet unit, not an acquisition organization"), ("COMPACFLT", "fleet unit, not an acquisition organization"),
                         ("Measurement Science and Engineering Department", "free text the rules do not read"),
                         ("Corporate Operations Department, Property Management Division, Code 107", "free text the rules do not read"),
                         ("(NUWCDETFEO NORFOLK", "free text the rules do not read"), ("NAVAIR MTS", "free text the rules do not read"),
                         ("X - X - USAF", "parent organization USAF is not in the contracting column or the command list")):
        assert r(cell).get("unresolved") == reason, (cell, r(cell))

    # A department named with its code, or a code of the site, belongs to the row's contracting office by inference.
    ihd = spec("center:nswcihd", "technical_center", "NSWCIHD", contracting_uic="N00174")
    hit = resolve("Cast Products & Explosives Division (M2)", table, by_uic, ihd)
    assert (hit["node"]["id"], hit["node"]["name"], hit["parent"]["id"], hit["inferred"]) == ("dept:nswcihd-m2", "Cast Products & Explosives Division (M2)", "center:nswcihd", True), hit
    assert resolve("Naval PHS&T Division (W6", table, by_uic, ihd)["node"]["id"] == "dept:nswcihd-w6", "the sheet's missing parenthesis"
    hit = resolve("C760 Divers", table, by_uic, spec("activity:psns-imf", "field_activity", "PSNS&IMF", contracting_uic="N4523A"))
    assert (hit["node"]["id"], hit["node"]["codes"]["office_code"], hit["inferred"]) == ("dept:psns-imf-c760", "C760", True), hit
    hit = resolve("Code 980", table, by_uic, ihd)
    assert hit["node"]["id"] == "center:nswcihd" and hit["parent"] is None and hit["inferred"], hit
    assert resolve("N31, Port Operations", table, by_uic, ihd)["unresolved"], "an N code is an installation's, not the site's"
    assert resolve("Barge Program", table, by_uic, ihd)["unresolved"] and resolve("PMS 312", table, by_uic, ihd)["node"]["id"] == "pms:312"
    assert resolve("Cast Products & Explosives Division (M2)", table, by_uic, spec("contracting:n00024", "contracting_office", "N00024", uic="N00024"))["unresolved"], \
        "only a technical center or field activity places a department"
    # The deputy program manager list: header offsets give the columns, a title-less line is a heading.
    sample = ("TITLE                PHONE       CODE                  DESCRIPTION\n"
              "                                 PEO CARRIERS\n"
              "DPM                  555-0100    PMS 312               In-Service Carriers\n"
              "                                 TEAM SHIPS (PEO SHIPS/ SEA21)\n"
              "DPM                  555-0101    PMS 326B (SEA 21)     Boats\n"
              "DPM                  555-0102    SEA 21A               Deputy\n"
              "Page 2\n")
    rows_ = dpm_rows(sample)
    assert rows_ == [("PEO CARRIERS", "DPM", "PMS 312", "In-Service Carriers"),
                     ("TEAM SHIPS (PEO SHIPS/ SEA21)", "DPM", "PMS 326B (SEA 21)", "Boats"),
                     ("TEAM SHIPS (PEO SHIPS/ SEA21)", "DPM", "SEA 21A", "Deputy")], rows_
    try:
        dpm_rows(sample.replace("PEO CARRIERS\n", "PEO NOVEL\n"))
        raise AssertionError("an unknown heading must stop the build")
    except SystemExit:
        pass
    ships = [spec(pid, ptype, pname, **({"office_code": "SEA 21"} if pid == "sea:21" else {})) for pid, ptype, pname in DPM_HEADINGS["TEAM SHIPS (PEO SHIPS/ SEA21)"]]
    node, parent, grand = dpm_node("PMS 326B (SEA 21)", ships, table)
    assert (node["id"], parent["id"], grand["id"]) == ("pms:326b", "sea:21", "command:navsea"), (node, parent, grand)
    node, parent, _ = dpm_node("SEA 21A", ships, table)
    assert (node["id"], parent["id"]) == ("sea:21a", "sea:21"), (node, parent)
    node, parent, _ = dpm_node("SHIPS GEM", ships, table)
    assert (node["id"], node["type"], parent["id"]) == ("office:ships-gem", "program_office", "peo:ships"), (node, parent)
    # Page statements: a passage is compared to the saved page's visible text, tags and entities aside.
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        page = Path(tmp) / "p.html"
        page.write_text("<html><title>Unit &amp; Co</title><script>x = 'RANGE';</script><body><h1>RANGE\n  SYSTEMS</h1><p>It\u2019s here.</p></body></html>")
        rel = str(page.relative_to(ROOT))
        assert page_text(page) == "Unit & Co RANGE SYSTEMS It's here."
        manifest = [{"url": "https://example.test/p", "path": rel, "status": 200, "sha256": "0" * 64, "retrieved_at": "2026-09-22T00:00:00Z", "mime": "text/html"}]
        st = {"id": "st:x", "node": {"id": "dept:x-rs", "type": "department", "name": "Range Systems"}, "parent": "center:x",
              "source_url": "https://example.test/p", "passages": ["RANGE SYSTEMS", "It's here."], "aliases": ["RS"], "resolves": ["RS10 Program Support Division"]}
        mem = Memory({"nodes": [], "observations": [], "relationships": []})
        mem.read_pages([st], manifest)
        assert mem.nodes["dept:x-rs"]["name"] == "Range Systems" and ("dept:x-rs", "center:x") in mem.parents
        assert resolve("RS10 Program Support Division", mem.table, {})["node"]["id"] == "dept:x-rs" and resolve("RS", mem.table, {})["node"]["id"] == "dept:x-rs"
        assert [o["id"] for o in mem.observations] == ["obs:page:dept-x-rs", "obs:page:dept-x-rs:parent"]
        try:
            Memory({"nodes": [], "observations": [], "relationships": []}).read_pages([dict(st, passages=["RANGE SYSTEM X"])], manifest)
            raise AssertionError("a passage the page does not carry must stop the build")
        except SystemExit:
            pass

    # The file is in the form this tool writes, so a build leaves the hand-written records byte for byte.
    text = SEED.read_text(encoding="utf-8")
    assert dumps(json.loads(text)) == text
    assert not collisions(json.loads(text)), collisions(json.loads(text))
    print("org_memory_lrae selfcheck ok")
    return 0


def collect() -> int:
    """The official pages the page statements cite, and the pages the memory still wants read (`wanted` in the
    statements file: a leadership listing, a department directory, an office no source names yet), not yet saved.
    A statement is written from a saved page, its passages checked verbatim against the bytes, so collecting comes first."""
    from fetch import collect_missing  # noqa: E402
    d = json.loads(STATEMENTS.read_text(encoding="utf-8"))
    wanted = [(st["source_url"], f"organization page for {st['node']['id']}") for st in d["statements"]]
    return 1 if collect_missing(wanted + [(w["url"], w["why"]) for w in d.get("wanted", [])]) else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if args == ["collect"]:
        sys.exit(collect())
    if args == ["build"] or args == ["--check"]:
        sys.exit(build(check=args == ["--check"]))
    print(__doc__)
    sys.exit(2)

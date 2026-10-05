#!/usr/bin/env python3
"""Read NASA's own publications into the organization memory (the `memory` stage of the nasa profile).

    AGENCY=nasa python research/tools/org_memory_nasa.py build            # research/agencies/nasa/memory/organization_seed.json
    AGENCY=nasa python research/tools/org_memory_nasa.py build --check    # exit 1 when a build would change the file
    AGENCY=nasa python research/tools/org_memory_nasa.py collect          # fetch the documents the build reads, not yet saved
    AGENCY=nasa python research/tools/org_memory_nasa.py selfcheck

Three sources, each node resting on a dated observation in the source's own words:

- SAM.gov federal organization records: the department (100000266), the agency (100000267, FPDS code 8000) and the
  thirteen offices under it that USAspending shows awarding in FY2026, each with the office code its record states
  (80GSFC, 80JSC0, 80NSSC ...). A record whose office types include CONTRACT AWARDS contracts for the agency.
- The NASA Organization page (www.nasa.gov/organization, "Page Last Updated" printed on it): the Administrator's
  office, the mission directorates and headquarters offices, the centers and facilities, and the official each row
  names (`leads`, with the title the row prints). A facility the page marks "reporting to <center> director" is
  placed under that center; every other office under the agency, the section it is listed in kept in the note.
- The Agency-Wide Acquisition Forecast: the buying offices its BuyingOffice column names (the centers and agency
  offices by abbreviation, an alias of the node) and the mission directorates its HQMissionDirectorate column names.
  A directorate the forecast names and the Organization page does not (ESDMD, SOMD, ARMD, STMD, HEOMD) stands as the
  forecast prints it; no source here states which directorate now holds its portfolio, so none is claimed.

Every record is `review_status: draft` and carries `generator: org_memory_nasa`; a rebuild is byte for byte the
same while the saved documents are.
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import KEY, MANIFEST, MEMORY, NOTE_TAG, P, ROOT  # noqa: E402
from lrae_package import read_sheet  # noqa: E402
from org_memory_army import SAM_ORG, by_url, person_id, sam_parent, sam_record  # noqa: E402
from org_memory_darpa import node, observation, relationship, unique_aliases  # noqa: E402
from org_memory_lrae import squash  # noqa: E402

SEED = MEMORY / "organization_seed.json"
GENERATOR = "org_memory_nasa"
AGENCY_ID = P["agency"]["node"]
# NASA is its own department: SAM.gov prints the department and the agency as two records of one name, read as one node.
DEPARTMENT_ORG = "100000266"
ORG_URL = "https://www.nasa.gov/organization/"
FORECAST = P["forecast"]["releases"][0]
FORECAST_PAGE = "https://www.hq.nasa.gov/office/procurement/forecast/"
FORECAST_URL = "https://www.hq.nasa.gov/office/procurement/forecast/AcqForecastNew.xlsx"
SAM_NODES = P["sam_org_nodes"]
# The offices three sources name three ways: node -> (the Organization page's name, the forecast's BuyingOffice value).
# This table is the generator's one reading; the SAM.gov record gives each its id and office code.
NAMED = {"center:arc": ("Ames Research Center", "ARC"), "center:afrc": ("Armstrong Flight Research Center", "AFRC"),
         "center:grc": ("Glenn Research Center", "GRC"), "center:gsfc": ("Goddard Space Flight Center", "GSFC"),
         "center:jsc": ("Johnson Space Center", "JSC"), "center:ksc": ("Kennedy Space Center", "KSC"),
         "center:larc": ("Langley Research Center", "LaRC"), "center:msfc": ("Marshall Space Flight Center", "MSFC"),
         "center:ssc": ("Stennis Space Center", "SSC"), "contracting:80nssc": ("NASA Shared Services Center", "NSSC"),
         "contracting:80tech": (None, "ITPO"), "contracting:80hqtr": (None, "HQ"),
         "center:jpl": ("Jet Propulsion Laboratory", None),
         "facility:iv-v": ("Katherine Johnson Independent Verification and Validation (IV & V) Facility", "IV&V")}
BY_PAGE_NAME = {page: nid for nid, (page, _) in NAMED.items() if page}
BY_BUYER = {buyer: nid for nid, (_, buyer) in NAMED.items() if buyer}
SECTION_RE = re.compile(r'<h1 id="[^"]*" class="wp-block-heading">(.*?)</h1>', re.S)
REPORTS_TO_RE = re.compile(r"\*\s*\*\s*reporting to (?:the )?(.+?) director\b", re.I)
UPDATED_RE = re.compile(r"Page Last Updated:\s*</div>\s*<div[^>]*>\s*([A-Z][a-z]{2}) (\d{1,2}), (\d{4})")
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
ABBREVIATION_RE = re.compile(r"^(.*?)\s*\(([A-Z&]{2,8})\)$")
CONTRACT = ("08_org_memory_format.md: observations are what a source states; relationships are dated claims resting on "
            "observations; interpretations are our readings; corrections are retractions")
SCOPE = ("National Aeronautics and Space Administration: the department and agency records, the mission directorates and "
         "headquarters offices, the centers and facilities with JPL, the thirteen awarding offices and their stated leaders")
NOTES = ("drafted by org_memory_nasa from SAM.gov organization records, the NASA Organization page and the Agency-Wide "
         "Acquisition Forecast; names as printed in the source")
SAM_FIELDS = ("orgKey", "name", "type", "level", "fullParentPath", "fullParentPathName", "aacCode", "fpdsCode", "cgac", "codeHierarchy")


def rows() -> list[dict]:
    return [json.loads(l) for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cell_text(fragment: str) -> str:
    return squash(re.sub(r"<[^>]+>", " ", fragment).replace("\xa0", " "))


# ---------------------------------------------------------------- the Organization page

def org_rows(page: str) -> list[dict]:
    """Every table row of the page under its section: {section, person, role, office, reports_to}. A row that names
    no office (the Office of the Administrator's own rows) has office ''."""
    out = []
    parts = SECTION_RE.split(page)
    for title, body in zip(parts[1::2], parts[2::2]):
        section = cell_text(title)
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
            cells = [cell_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 2:
                continue
            reports = REPORTS_TO_RE.search(cells[0])
            person = cells[0].split("*", 1)[0].strip()
            out.append({"section": section, "person": person, "role": cells[1], "office": cells[2] if len(cells) > 2 else "",
                        "reports_to": reports[1].strip() if reports else "", "passage": " | ".join(cells)})
    return out


def page_day(page: str, fallback: str) -> str:
    m = UPDATED_RE.search(page)
    return f"{m[3]}-{MONTHS[m[1]]:02d}-{int(m[2]):02d}" if m else fallback


def office_kind(section: str, office: str) -> str:
    """The node type of an office by the section that lists it: a laboratory is a technical center, a center or
    facility a field activity, an office (headquarters, or the JPL oversight office listed among the centers) a program office."""
    if "Laboratory" in office:
        return "technical_center"
    if section.startswith("Centers and Facilities") and not office.startswith(("NASA Office", "Office")):
        return "field_activity"
    return "program_office"


def office_id(office: str, kind: str) -> str:
    return BY_PAGE_NAME.get(office) or f"{'facility' if kind == 'field_activity' else 'office'}:{slug(office)}"


# ---------------------------------------------------------------- the forecast

def directorates(rows_: list[dict]) -> Counter:
    """Each value the HQMissionDirectorate column prints ('A;#B' is two), with its row count."""
    return Counter(v.strip() for r in rows_ for v in r["mission_directorate"].split(";#") if v.strip())


def directorate_name(value: str) -> tuple[str, str]:
    """'Science Mission Directorate (SMD)' -> ('Science Mission Directorate', 'SMD'); a value without one keeps ''."""
    m = ABBREVIATION_RE.match(value)
    return (m[1], m[2]) if m else (value, "")


# ---------------------------------------------------------------- the seed

def build_seed(manifest: list[dict]) -> dict:
    obs, rels, nodes = [], [], []
    counter = {"obs": 0, "rel": 0}
    have: dict[str, dict] = {}

    def add_obs(row, observed_at, kind, passage, subjects) -> str:
        counter["obs"] += 1
        oid = f"obs:nasa:{counter['obs']:03d}"
        obs.append(observation(oid, row, observed_at, kind, passage, subjects))
        return oid

    def add_rel(kind, src, dst, obs_ids, as_of, **kw) -> None:
        same = next((r for r in rels if (r["type"], r["from"], r["to"]) == (kind, src, dst)), None)
        if same:  # a second source stating the same edge adds its observation
            same["observation_ids"] += [o for o in obs_ids if o not in same["observation_ids"]]
            return
        counter["rel"] += 1
        rel = relationship(f"rel:nasa:{counter['rel']:03d}", kind, src, dst, obs_ids, as_of, **kw)
        rel["generator"] = GENERATOR
        rels.append(rel)

    def add_node(nid, kind, name, obs_ids, aliases=None, codes=None, notes=NOTES) -> dict:
        if nid in have:  # a node another source made: this one adds its observation, spelling and codes
            n = have[nid]
            n["observation_ids"] += [o for o in obs_ids if o not in n["observation_ids"]]
            n["aliases"] = unique_aliases(n["aliases"] + [{"text": name, "observation_ids": obs_ids}] * (name != n["name"]) + (aliases or []))
            if codes:
                n["codes"] = {**n.get("codes", {}), **codes}
            return n
        n = node(nid, kind, name, obs_ids, unique_aliases(aliases or []), codes)
        n["generator"], n["notes"] = GENERATOR, notes
        nodes.append(n)
        have[nid] = n
        return n

    org = by_url(manifest, ORG_URL)
    forecast = by_url(manifest, FORECAST_URL)
    sam = {oid: by_url(manifest, SAM_ORG.format(oid)) for oid in SAM_NODES}
    missing = [k for k, v in (("organization page", org), ("forecast", forecast), *((f"sam {k}", v) for k, v in sam.items())) if v is None]
    if missing:
        raise SystemExit(f"documents not saved yet: {', '.join(missing)}; run `collect` first")

    # SAM.gov: the agency (its department and agency records), then the awarding offices, each on its own record.
    for org_id in sorted(SAM_NODES, key=lambda k: (SAM_NODES[k] != AGENCY_ID, SAM_NODES[k], k)):
        nid, row = SAM_NODES[org_id], sam[org_id]
        rec = sam_record(row)
        day = row["retrieved_at"][:10]
        passage = json.dumps({k: rec[k] for k in SAM_FIELDS if rec.get(k) is not None}, separators=(",", ":"), ensure_ascii=False)
        oid = add_obs(row, day, "naming", passage, [nid])
        aac = str(rec.get("aacCode") or "").strip()
        codes = {"sam_organization_id": org_id}
        aliases = []
        if org_id == DEPARTMENT_ORG:
            kind, codes = "agency", {"sam_department_organization_id": org_id, "cgac": str(rec.get("cgac") or P["agency"]["toptier_code"])}
        elif nid == AGENCY_ID:
            kind, codes["fpds_agency_id"] = "agency", str(rec.get("fpdsCode") or P["agency"]["subtier_code"])
            aliases.append({"text": P["agency"]["subtier_abbreviation"], "observation_ids": [oid]})
        else:
            kind = "field_activity" if nid.startswith("center:") else "contracting_office"
            codes["uic"] = aac
            aliases.append({"text": aac, "observation_ids": [oid]})
        add_node(nid, kind, squash(str(rec.get("name") or nid)), [oid], aliases, codes)
        parent = sam_parent(rec, SAM_NODES)
        if parent and parent != nid:
            add_rel("child_of", nid, parent, [oid], day, dates_status="unknown",
                    dates_note="the record states the organization's place in the hierarchy, not since when",
                    status_note=f"stated by the SAM.gov organization record of the latest cited retrieval ({rec.get('fullParentPathName')}); "
                                "not re-verified since")
        if any(t.get("office_type_name") == "CONTRACT AWARDS" and not t.get("end_date") for t in rec.get("orgOfficeTypes") or []):
            add_rel("contracts_for", nid, AGENCY_ID, [oid], day, role=f"awarding office {aac}", dates_status="unknown",
                    status_note="the SAM.gov organization record lists CONTRACT AWARDS among the office's types; not re-verified since")

    # The Organization page: the Administrator's office, the directorates and offices, the centers and facilities.
    page = (ROOT / org["path"]).read_text(encoding="utf-8", errors="replace")
    day = page_day(page, org["retrieved_at"][:10])
    for r in org_rows(page):
        # ponytail: a row naming no office leads the agency only in the Administrator's own office; elsewhere (an acting
        # officer with no office cell) it names no node, and is left out.
        if not r["office"] and not r["section"].startswith("Office of the Administrator"):
            continue
        target = AGENCY_ID
        if r["office"]:
            kind = office_kind(r["section"], r["office"])
            target = office_id(r["office"], kind)
            oid = add_obs(org, day, "existence", r["passage"], [target] + [person_id(r["person"])] * bool(r["person"]))
            add_node(target, kind, r["office"], [oid])
            parent = BY_PAGE_NAME.get(r["reports_to"], AGENCY_ID) if r["reports_to"] else AGENCY_ID
            add_rel("child_of", target, parent, [oid], day, dates_status="unknown",
                    status_note=(f"the NASA Organization page lists the office under '{r['section']}'"
                                 + (f" and marks it as reporting to the {r['reports_to']} director" if r["reports_to"] else "")
                                 + "; not re-verified since"))
        else:
            oid = add_obs(org, day, "leadership", r["passage"], [AGENCY_ID] + [person_id(r["person"])] * bool(r["person"]))
        if not r["person"]:
            continue
        pid = person_id(r["person"])
        add_node(pid, "person", r["person"], [oid], notes="public official role only; recorded with source and observation date; " + NOTES)
        add_rel("leads", pid, target, [oid], day, role=r["role"], dates_status="unknown",
                dates_note="the page states who holds the role on its date, not since when",
                status_note="named in this row of the NASA Organization page of the latest cited retrieval; not re-verified since")

    # The forecast: the buying offices (an alias of the node the table names) and the mission directorates.
    _, forecast_rows = read_sheet(ROOT / forecast["path"], FORECAST["key"], FORECAST["sheet"], FORECAST["header_row"])
    fday = FORECAST["release_date"]
    for buyer, n in sorted(Counter(r["command"] for r in forecast_rows if r["command"]).items()):
        nid = BY_BUYER.get(buyer)
        oid = add_obs(forecast, fday, "naming", f"column 'BuyingOffice': '{buyer}' ({n} rows)", [nid] if nid else [])
        if nid is None:  # a buying office the table has no node for: kept as the forecast prints it, under no parent
            add_node(f"office:{slug(buyer)}", "program_office", buyer, [oid], [], {"office_code": buyer},
                     notes="named in the BuyingOffice column of the forecast, which prints no parent for it; " + NOTES)
            continue
        add_node(nid, have[nid]["type"] if nid in have else "field_activity", buyer, [oid], [], {"office_code": buyer})
    for value, n in sorted(directorates(forecast_rows).items()):
        name, abbreviation = directorate_name(value)
        nid = f"office:{slug(name)}"
        oid = add_obs(forecast, fday, "existence", f"column 'HQMissionDirectorate': '{value}' ({n} rows)", [nid])
        aliases = [{"text": abbreviation, "observation_ids": [oid]}] if abbreviation else []
        listed = nid in have
        add_node(nid, "program_office", name, [oid], aliases, {"office_code": abbreviation} if abbreviation else None,
                 notes=NOTES if listed else ("named in the HQMissionDirectorate column of the forecast; the NASA Organization page of "
                                            f"{day} does not list it, and no source here states which office now holds its portfolio; " + NOTES))

    return {"generated": max(day, fday, *(r["retrieved_at"][:10] for r in sam.values())), "contract": CONTRACT, "scope": SCOPE,
            "nodes": nodes, "observations": obs, "relationships": rels, "interpretations": []}


def dumps(seed: dict) -> str:
    return json.dumps(seed, indent=1, ensure_ascii=False) + "\n"


def build(check: bool = False) -> int:
    if KEY != "nasa":
        print(f"this reader builds the nasa memory; the profile is {KEY} (set AGENCY=nasa)", file=sys.stderr)
        return 2
    seed = build_seed(rows())
    text = dumps(seed)
    kinds = Counter(n["type"] for n in seed["nodes"])
    print(f"{len(seed['nodes'])} node(s) {dict(kinds)}, {len(seed['observations'])} observation(s), {len(seed['relationships'])} relationship(s)")
    if check:
        if not SEED.exists() or SEED.read_text(encoding="utf-8") != text:
            print(f"{SEED.relative_to(ROOT)} differs from a fresh build; run `build` to regenerate", file=sys.stderr)
            return 1
        return 0
    SEED.parent.mkdir(parents=True, exist_ok=True)
    SEED.write_text(text, encoding="utf-8")
    print(f"written to {SEED.relative_to(ROOT)}")
    return 0


def collect() -> int:
    """The SAM.gov records, the Organization page and the forecast (with the page that dates it), directly."""
    from fetch import collect_missing  # noqa: E402
    wanted = [(SAM_ORG.format(oid), f"SAM.gov federal organization record{NOTE_TAG}: {oid} ({nid})") for oid, nid in SAM_NODES.items()]
    wanted += [(ORG_URL, f"NASA organization page{NOTE_TAG}"), (FORECAST_PAGE, f"NASA Acquisition Forecast page{NOTE_TAG}"),
               (FORECAST_URL, f"NASA Agency-Wide Acquisition Forecast workbook{NOTE_TAG}")]
    return 1 if collect_missing(wanted) else 0


def selfcheck() -> int:
    page = ('<h1 id="administrator" class="wp-block-heading">Office of the Administrator</h1><table><tr><td><a>Jared Isaacman</a></td>'
            '<td>Administrator</td></tr></table><h1 id="centers-facilities" class="wp-block-heading">Centers and Facilities</h1><table>'
            '<tr><td><a>Jamie Dunn</a></td><td>Director</td><td><a>Goddard Space Flight Center</a></td></tr>'
            '<tr><td><a>Wes Deadrick</a> * *reporting to Goddard Space Flight Center director</td><td>Director</td>'
            '<td>Katherine Johnson Independent Verification and Validation (IV &amp; V) Facility</td></tr>'
            '<tr><td>Dave Gallagher</td><td>Director</td><td>Jet Propulsion Laboratory</td></tr>'
            '<tr><td>Antti Pulkkinen</td><td>Acting Director</td><td>NASA Office of JPL Management and Oversight</td></tr></table>'
            '<div>Page Last Updated:</div>\n<div class="hds-footer-meta-value">\nSep 22, 2026</div>')
    got = org_rows(page)
    assert [(r["section"], r["person"], r["office"], r["reports_to"]) for r in got] == [
        ("Office of the Administrator", "Jared Isaacman", "", ""), ("Centers and Facilities", "Jamie Dunn", "Goddard Space Flight Center", ""),
        ("Centers and Facilities", "Wes Deadrick", "Katherine Johnson Independent Verification and Validation (IV & V) Facility",
         "Goddard Space Flight Center"), ("Centers and Facilities", "Dave Gallagher", "Jet Propulsion Laboratory", ""),
        ("Centers and Facilities", "Antti Pulkkinen", "NASA Office of JPL Management and Oversight", "")], got
    assert page_day(page, "x") == "2026-09-22" and page_day("", "2026-09-26") == "2026-09-26"
    assert [office_kind(r["section"], r["office"]) for r in got[1:]] == ["field_activity", "field_activity", "technical_center", "program_office"]
    assert office_id(got[2]["office"], "field_activity") == "facility:iv-v" and office_id("Mission Support Directorate", "program_office") == "office:mission-support-directorate"
    assert BY_PAGE_NAME[got[2]["reports_to"]] == "center:gsfc"
    assert directorates([{"mission_directorate": "Science Mission Directorate (SMD);#Space Technology Mission Directorate"},
                         {"mission_directorate": "Science Mission Directorate (SMD)"}, {"mission_directorate": ""}]) == {
        "Science Mission Directorate (SMD)": 2, "Space Technology Mission Directorate": 1}
    assert directorate_name("Office of the Chief Information Officer (OCIO)") == ("Office of the Chief Information Officer", "OCIO")
    assert directorate_name("Office of Strategic Infrastructure") == ("Office of Strategic Infrastructure", "")
    assert sam_parent({"fullParentPath": "100000266.100000267.100183432"}, SAM_NODES) == AGENCY_ID
    print("org_memory_nasa selfcheck ok")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv[:1] in (["selfcheck"], ["--selfcheck"]):
        sys.exit(selfcheck())
    if argv[:1] == ["collect"]:
        sys.exit(collect())
    if argv[:1] == ["build"]:
        sys.exit(build(check="--check" in argv))
    print(__doc__)
    sys.exit(2)

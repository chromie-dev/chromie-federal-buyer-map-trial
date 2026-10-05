#!/usr/bin/env python3
"""Read the Department of the Air Force's organization records into the organization memory (the `memory` stage of
the airforce profile).

    AGENCY=airforce python research/tools/org_memory_airforce.py collect   # fetch the records the build reads, not yet saved
    AGENCY=airforce python research/tools/org_memory_airforce.py build     # research/agencies/airforce/memory/organization_seed.json
    AGENCY=airforce python research/tools/org_memory_airforce.py --check   # exit 1 when a build would change the file
    python research/tools/org_memory_airforce.py --selfcheck

The department's own pages (af.mil, aflcmc.af.mil, ssc.spaceforce.mil) refuse this address, so the first memory is
read from the one official record of the acquisition structure that answers directly: SAM.gov's federal organization
records (`/api/prod/federalorganizations/v1/organizations/{orgKey}`). Each record states an organization's name, its
type (agency, major command, sub command, office), its activity address code where it is a contracting office, the
day it started, and its whole parent path by name: the department, the Air Force Materiel Command, the Life Cycle
Management Center, the program executive office (SAM.gov's "PEO WEAPONS", "PAE C3BM-HANSCOM", the Space Systems
Command's five PEOs) and the office. The seed holds one node per record on the path of every contracting office the
profile sweeps, each on a dated observation whose passage is a verbatim slice of the saved record, as
`research/docs/08_org_memory_format.md` asks. Relationships are `child_of` (the record's parent) and `contracts_for`
(an office to the organization above it). Where the FPDS feed for an office is saved, the office's FPDS name and
agency code are second observations.

Who leads what comes from the department's own leadership pages, saved through the hosted browser (LEADERSHIP_PAGES):
each lists its leaders as biography links whose text reads "Rank NAME Title, Organization". The head roles
(commander, executive director, the Secretary, Under Secretary, Chief and Vice Chief of Staff) become `leads`
relationships from a `person:` node, `directly_documented` when the title names the organization and `inferred`
when the title stands alone on the organization's own page. The Life Cycle Management Center has no leadership page
(its biography paths answer 404), so its commander, executive director and the program executive officers of the
three portfolios SAM.gov also names are read from the center's organizational chart (CHART_URL), a PDF whose columns
`pdftotext -layout` keeps: the name is the cell beneath the directorate heading in the heading's column, and the
role word "PAE" beneath that. A reading of a chart's layout is `inferred`, and says so.

Every record is `review_status: draft` and carries `generator: org_memory_airforce`; a rebuild is byte for byte the
same while the saved records are.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import KEY, MANIFEST, MEMORY, P, ROOT  # noqa: E402
from org_memory_darpa import node as _node, observation, relationship as _relationship, unique_aliases  # noqa: E402
from org_memory_lrae import page_text, pdf_text, squash  # noqa: E402

SEED = MEMORY / "organization_seed.json"
GENERATOR = "org_memory_airforce"
AGENCY_ID = P["agency"]["node"]
SAM_ORG = "https://sam.gov/api/prod/federalorganizations/v1/organizations/{}"
DEPARTMENT_KEY = "300000251"  # DEPT OF THE AIR FORCE, as SAM.gov keys it
# The organization key of each contracting office the profile sweeps, by its code: the profile's SAM.gov ids.
OFFICE_KEYS = {code: key for key, code in P["sam_orgs"].items()}
# SAM.gov's record types -> the memory's node types. A sub command under a center whose code starts PEO or PAE is a
# program executive office; a laboratory is a technical center; a center or major command is a command.
TECHNICAL = {"AFRL"}
SITE_WORDS = ("HANSCOM",)  # a sub command that is a place (Hanscom AFLCMC) groups offices; it owns nothing
SPACE_FORCE_COMMANDS = {"SSC", "AFSPC"}  # SAM.gov files the Space Force's field commands under the department directly
CONTRACT = ("08_org_memory_format.md: observations are what a source states; relationships are dated claims resting on "
            "observations; interpretations are our readings; corrections are retractions")
SCOPE = ("Department of the Air Force acquisition: the department, the Air Force Materiel Command and the Space Systems Command, "
         "the Life Cycle Management, Research Laboratory and Sustainment centers, the program executive offices SAM.gov names "
         "on the path of each swept contracting office, and those offices with their activity address codes")
NOTES = "drafted by org_memory_airforce from SAM.gov federal organization records and the FPDS feed; names as printed in the source"
PERSON_NOTES = "public official role only; recorded with source and observation date; name as the page prints it"

# The leadership pages saved through the hosted browser, each the page of the organization whose leaders it lists.
LEADERSHIP_PAGES = (
    ("https://www.af.mil/About-Us/Biographies/", "agency:daf"),
    ("https://www.afmc.af.mil/About-Us/Biographies/", "command:afmc"),
    ("https://www.afrl.af.mil/About-Us/Leadership/", "center:afrl"),
    ("https://www.afsc.af.mil/About-Us/Leadership/", "command:afsc"),
    ("https://www.ssc.spaceforce.mil/About-Us/Leadership", "command:ssc"),
)
BIO_LINK = re.compile(r'<a[^>]+href="(https?://[^"]*Biograph[^"]*/Display/Article/\d+/[^"]+)"[^>]*>(.*?)</a>', re.S)
RANKS = ("General", "Lieutenant General", "Major General", "Brigadier General", "Colonel", "Chief Master Sergeant of the Air Force",
         "Chief Master Sergeant", "Dr.", "DR.")
# The word a title starts with, after the name: what the pages write after a leader's name.
TITLE_START = re.compile(r"^(?:Commander|Executive|Deputy|Vice|Director|The|Chief|Command|Mobilization|Retired|Died|Secretary|Under|"
                         r"Assistant|Principal|Acting|Senior|Special|Program|\d+(?:st|nd|rd|th))$")
# The roles that lead an organization; deputies, command chiefs and mobilization assistants are listed, not leaders.
HEAD_ROLE = re.compile(r"^(?:Commander\b|Executive Director\b|The \d+(?:st|nd|rd|th) (?:Secretary|Under Secretary) of the Air Force|"
                       r"(?:Vice )?Chief of Staff of the Air Force)")
AGENCY_WORDS = ("of the air force",)  # a department title names the department this way

# The Life Cycle Management Center's organizational chart, a PDF the hosted browser saved from the center's front page.
CHART_URL = ("https://www.aflcmc.af.mil/Portals/79/MISSION%20BRIEF/AFLCMC%20Org%20Chart_September18_NoPhone.pdf"
             "?ver=Vdcnri_9L_pRXfqV0VZHOg%3d%3d")
CHART_NODE = "command:aflcmc"
# Directorate heading on the chart -> the program executive office SAM.gov names. The chart's "C3 & Battle Mgmt
# Directorate*" is not the PAE C3BM (its footnote: the directorate "supports AF C3BM with approved rating chain
# deviation to the PAE"), so it is not mapped.
CHART_PEOS = {"Fighters & Advanced Aircraft Dir": "peo:fight-adv-acft", "Armament / Weapons Directorate": "peo:weapons",
              "Training Directorate": "peo:training"}
CHART_HEADS = {"COMMANDER": "Commander", "EXECUTIVE DIRECTOR": "Executive Director"}  # the command block's headings
CHART_SITES = ("Hanscom", "Eglin", "Gunter", "Tinker", "W-Patt", "Hill", "Robins", "Nellis", "Pentagon", "JBSA", "Lackland", "Heath", "WP")
CHART_RANKS = ("Brig Gen", "Lt Gen", "Maj Gen", "Col", "Mr.", "Ms.", "Dr.", "LT GEN", "MAJ GEN", "BRIG GEN", "COL", "MR.", "MS.")


def node(nid: str, kind: str, name: str, obs_ids: list[str], aliases: list[dict] | None = None, codes: dict | None = None, **extra) -> dict:
    out = _node(nid, kind, name, obs_ids, aliases, codes, **extra)
    out["generator"], out["notes"] = GENERATOR, extra.get("notes", NOTES)
    return out


def relationship(*args, **kw) -> dict:
    out = _relationship(*args, **kw)
    out["generator"] = GENERATOR
    return out


def rows() -> list[dict]:
    return [json.loads(l) for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]


def by_url(manifest: list[dict], url: str) -> dict | None:
    hits = [r for r in manifest if kept_page(r) and (ROOT / r["path"]).exists()
            and url in (r.get("url"), r.get("final_url"))]
    return hits[-1] if hits else None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def record(path: Path) -> dict:
    """The organization record a saved response carries (the API wraps it in `_embedded[0].org`)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["_embedded"][0]["org"] if isinstance(data, dict) and data.get("_embedded") else data


def passage(raw: str, org: dict) -> str:
    """The record's own bytes from its name through its parent path by name: a verbatim slice of the saved file."""
    start = raw.find('"name":"' + org["name"])
    end = raw.find('"fullParentPathName":"', start)
    if start >= 0 and end >= 0:
        close = raw.find('"', end + len('"fullParentPathName":"'))
        return raw[start:close + 1]
    keys = ("orgKey", "name", "type", "parentOrgKey", "fullParentPathName", "aacCode", "startDate", "level", "code")
    return json.dumps({k: org.get(k) for k in keys if org.get(k) is not None}, separators=(",", ":"), ensure_ascii=False)


def node_kind(org: dict, key: str) -> str:
    code, kind = str(org.get("code") or ""), str(org.get("type") or "")
    if key == DEPARTMENT_KEY or kind == "AGENCY":
        return "agency"
    if kind == "OFFICE":
        return "contracting_office"
    if code.startswith(("PEO", "PAE")):
        return "program_executive_office"
    if code in TECHNICAL:
        return "technical_center"
    if any(w in str(org.get("name") or "") for w in SITE_WORDS):
        return "department"
    return "command"


def node_id(org: dict, key: str) -> str:
    kind = node_kind(org, key)
    code = str(org.get("code") or org.get("aacCode") or key)
    if kind == "agency":
        return AGENCY_ID
    if kind == "contracting_office":
        return f"contracting:{code.lower()}"
    if kind == "program_executive_office":
        return "peo:" + slug(re.sub(r"^(PEO|PAE)[ -]*", "", code))
    if kind == "technical_center":
        return f"center:{code.lower()}"
    if kind == "department":
        return f"site:{slug(code)}"
    return f"command:{code.lower()}"


def display_name(org: dict) -> str:
    """The record's name as printed, less the office code SAM.gov prefixes and the double spaces it pads with. A program
    executive office SAM.gov names by its portfolio word alone (name "WEAPONS", code "PEO-WEAPONS") takes the code's
    prefix, so that the word "weapons" in a report is not the office."""
    name = re.sub(r"\s+", " ", str(org.get("name") or "")).strip()
    aac = str(org.get("aacCode") or "")
    if aac and name.upper().startswith(aac):
        name = name[len(aac):].strip()
    code = str(org.get("code") or "")
    if code.upper().startswith(("PEO", "PAE")) and not name.upper().startswith(("PEO", "PAE")):
        name = f"{re.split(r'[- ]', code, maxsplit=1)[0].upper()} {name}"
    return name


def wanted_keys(manifest: list[dict]) -> list[str]:
    """Every organization key the build reads: the department, each swept office and every key on its parent path
    (read from the saved office records; an office not saved yet contributes only itself)."""
    keys = [DEPARTMENT_KEY, *OFFICE_KEYS.values()]
    for key in list(OFFICE_KEYS.values()):
        row = by_url(manifest, SAM_ORG.format(key))
        if row:
            org = record(ROOT / row["path"])
            keys += [k for k in str(org.get("fullParentPath") or "").split(".")[2:-1]]  # after DoD and the department
    return list(dict.fromkeys(keys))


def fpds_tags(manifest: list[dict], code: str) -> tuple[dict, str] | None:
    """The FPDS feed page the contracts sweep saved for an office, and the contractingOfficeID tag on it."""
    pages = [r for r in manifest if kept_page(r) and f"CONTRACTING_OFFICE_ID:{code}" in (r.get("url") or "")
             and (ROOT / r["path"]).exists()]
    for r in sorted(pages, key=lambda r: (r["retrieved_at"], r["path"])):
        raw = (ROOT / r["path"]).read_text(encoding="utf-8", errors="replace")
        m = re.search(r'<ns1:contractingOfficeID[^>]*>[^<]*</ns1:contractingOfficeID>', raw)
        if m:
            return r, m.group(0)
    return None


def bio_entry(link_text: str) -> tuple[str, str, str] | None:
    """(rank, name, title) from a biography link's text, "Rank NAME Title, Organization"; None when no title follows the
    name (an index entry that is a name alone)."""
    text = squash(re.sub(r"<[^>]+>", " ", link_text))
    rank = ""
    for r in sorted(RANKS, key=len, reverse=True):
        if text.startswith(r + " "):
            rank, text = r, text[len(r) + 1:]
            break
    words = text.split()
    for i, w in enumerate(words):
        if i and TITLE_START.match(w.rstrip(",.:;")):
            return rank, " ".join(words[:i]), " ".join(words[i:])
    return None


def bio_leads(raw: str) -> list[dict]:
    """The leaders a leadership page lists: one dict per biography link whose title is a head role, with the link
    text as squashed page text (the passage), the name and the title as written."""
    out = []
    for m in BIO_LINK.finditer(raw):
        entry = bio_entry(m.group(2))
        if entry is None:
            continue
        rank, name, title = entry
        if not HEAD_ROLE.match(title) or re.search(r"\bRetired\b|\bDied\b", title):
            continue
        passage = squash(re.sub(r"<[^>]+>", " ", m.group(2)))
        if not any(o["name"] == name for o in out):
            out.append({"passage": passage, "rank": rank, "name": name, "title": title, "bio_url": m.group(1)})
    return out


def cells(line: str) -> list[tuple[int, str]]:
    """(column, text) per run of words separated by single spaces on a line of layout text."""
    return [(m.start(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]


def chart_person(cell: str) -> str:
    """A chart cell's name as printed, without the site word after it: 'Brig Gen Timoth Helfrich    W-Patt' and
    'Brig Gen Joshua Williams Hanscom' both name the officer alone."""
    words = cell.split()
    while words and words[-1] in CHART_SITES:
        words.pop()
    return " ".join(words)


def chart_column(lines: list[str], at: int, x: int, width: int, depth: int = 7) -> list[str]:
    """The cells beneath line `at` that start inside the column [x - 8, x + width], top down: a name cell starts up
    to eight columns before its heading, and the role row sits beneath a senior enlisted leader's line."""
    out = []
    for below in lines[at + 1:at + 1 + depth]:
        out += [t for cx, t in cells(below) if x - 8 <= cx <= x + width]
    return out


def chart_leads(text: str) -> list[dict]:
    """Who the Life Cycle Management Center's chart places at its head and at the head of each mapped directorate:
    the first name cell beneath a heading in the heading's column, and the role word ('PAE') beneath that."""
    lines = text.splitlines()
    out: list[dict] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        for x, cell in cells(line):
            if cell in CHART_HEADS and cell not in seen:
                below = [t for t in chart_column(lines, i, x, len(cell), depth=4) if t.upper() == t and not t.startswith("(")]
                if below:
                    seen.add(cell)
                    out.append({"heading": cell, "node": CHART_NODE, "name_cell": chart_person(below[0]), "role": CHART_HEADS[cell]})
            elif cell in CHART_PEOS and cell not in seen:
                column = chart_column(lines, i, x, len(cell))
                names = [t for t in column if not t.startswith(("SEL", "(")) and t not in CHART_SITES and t != "PAE" and t != "Director"]
                if names:
                    seen.add(cell)
                    role = "PAE" if "PAE" in column else "Director"
                    out.append({"heading": cell, "node": CHART_PEOS[cell], "name_cell": chart_person(names[0]), "role": role})
    return out


def chart_name(cell: str) -> str:
    """The person a chart cell names, without rank and grade: 'Mr. Rodney Stevens, SES' names Rodney Stevens."""
    name = chart_person(cell)
    for r in sorted(CHART_RANKS, key=len, reverse=True):
        if name.startswith(r + " "):
            name = name[len(r) + 1:]
            break
    return re.sub(r",\s*(SES|NH-\d+|GG-\d+)$", "", name).strip()


def person_id(name: str) -> str:
    return "person:" + slug(name)


def build_seed(manifest: list[dict]) -> dict:
    obs: list[dict] = []
    rels: list[dict] = []
    nodes: dict[str, dict] = {}
    interpretations: list[dict] = []
    counter = {"obs": 0, "rel": 0}

    def add_obs(row, observed_at, kind, text, subjects) -> str:
        counter["obs"] += 1
        oid = f"obs:airforce:{counter['obs']:03d}"
        obs.append(observation(oid, row, observed_at, kind, text, subjects))
        return oid

    def add_rel(kind, src, dst, obs_ids, as_of, **kw) -> None:
        counter["rel"] += 1
        rels.append(relationship(f"rel:airforce:{counter['rel']:03d}", kind, src, dst, obs_ids, as_of, **kw))

    keys = wanted_keys(manifest)
    saved: dict[str, tuple[dict, dict, str]] = {}
    for key in keys:
        row = by_url(manifest, SAM_ORG.format(key))
        if row is None:
            continue
        raw = (ROOT / row["path"]).read_text(encoding="utf-8")
        saved[key] = (row, record(ROOT / row["path"]), raw)
    missing = [k for k in keys if k not in saved]
    if DEPARTMENT_KEY in missing or not any(k in saved for k in OFFICE_KEYS.values()):
        raise SystemExit(f"organization records not saved yet ({len(missing)} missing); run `collect` first")
    ids = {key: node_id(org, key) for key, (_, org, _) in saved.items()}
    dates = []
    # One node per record, oldest parent first so a child's parent exists when its edge is written.
    for key, (row, org, raw) in sorted(saved.items(), key=lambda kv: int(kv[1][1].get("level") or 0)):
        nid, kind = ids[key], node_kind(org, key)
        day = row["retrieved_at"][:10]
        dates.append(day)
        oid = add_obs(row, day, "naming", passage(raw, org), [nid])
        aliases = [{"text": str(org.get("code")), "observation_ids": [oid]}] if org.get("code") and str(org["code"]) != display_name(org) else []
        if kind == "agency":
            aliases += [{"text": "DAF", "observation_ids": [oid]}, {"text": str(org.get("shortName") or "USAF"), "observation_ids": [oid]}]
        codes = {"sam_organization_id": key}
        if org.get("aacCode"):
            codes["uic"] = str(org["aacCode"])
        if org.get("fpdsCode") and kind == "agency":
            codes["fpds_agency_id"] = str(org["fpdsCode"])
        name = "Department of the Air Force" if kind == "agency" else display_name(org)
        extra = {}
        if kind == "contracting_office":
            hit = fpds_tags(manifest, str(org.get("aacCode") or ""))
            if hit:
                frow, tag = hit
                foid = add_obs(frow, frow["retrieved_at"][:10], "naming", tag, [nid])
                name_attr = re.search(r'name="([^"]*)"', tag)
                if name_attr:
                    aliases.append({"text": re.sub(r"\s+", " ", name_attr.group(1)).strip(), "observation_ids": [foid]})
                extra["observation_ids_more"] = [foid]
        if kind == "program_executive_office":
            printed = re.sub(r"\s+", " ", str(org.get("name"))).strip()
            if printed.upper().startswith(("PEO", "PAE")):  # the record's own long name; a bare portfolio word is not an alias
                aliases.append({"text": printed, "observation_ids": [oid]})
        obs_ids = [oid, *extra.pop("observation_ids_more", [])]
        started = str(org.get("startDate") or "")[:10]
        nodes[nid] = node(nid, kind, name, obs_ids, unique_aliases(aliases), codes,
                          notes=(f"SAM.gov organization record (type {org.get('type')}, level {org.get('level')}"
                                 + (f", started {started}" if started else "") + "); " + NOTES))
        parent_key = str(org.get("parentOrgKey") or "")
        if kind != "agency" and parent_key in ids:
            add_rel("child_of", nid, ids[parent_key], [oid], day, effective_from=started or None,
                    dates_status="documented" if started else "unknown",
                    dates_note="the record states the organization's start date" if started else "the record states no start date",
                    status_note="stated by the latest cited SAM.gov organization record; not re-verified since")
            if kind == "contracting_office":
                add_rel("contracts_for", nid, ids[parent_key], [oid], day, role="contracting office of the organization the record files it under",
                        dates_status="unknown", dates_note="the record states the office's parent, not an arrangement's dates",
                        status_note="read from the latest cited SAM.gov organization record's parent path; the record states a parent, "
                                    "not a contracting arrangement, so this edge is inferred")
                rels[-1]["evidence_class"] = "inferred"
    # Who leads what: the leadership pages, then the Life Cycle Management Center's chart.
    def add_person(name: str, oid: str) -> str:
        pid = person_id(name)
        if pid not in nodes:
            nodes[pid] = node(pid, "person", name, [oid], notes=PERSON_NOTES)
        elif oid not in nodes[pid]["observation_ids"]:
            nodes[pid]["observation_ids"].append(oid)
        return pid

    for url, org_id in LEADERSHIP_PAGES:
        row = by_url(manifest, url)
        if row is None or org_id not in nodes:
            continue
        raw = (ROOT / row["path"]).read_text(encoding="utf-8", errors="replace")
        day = row["retrieved_at"][:10]
        dates.append(day)
        org_name = nodes[org_id]["name"].lower()
        for lead in bio_leads(raw):
            title_l = lead["title"].lower()
            named = org_name in title_l or (org_id == AGENCY_ID and any(w in title_l for w in AGENCY_WORDS))
            target = org_id
            if not named:  # a title naming another organization of the seed is that organization's leader, not this page's
                others = [nid for nid, n in nodes.items() if nid != org_id and n["type"] not in ("person", "contracting_office") and n["name"].lower() in title_l]
                if len(others) > 1:
                    continue
                if others:
                    target, named = others[0], True
            oid = add_obs(row, day, "existence", lead["passage"], [target, person_id(lead["name"])])
            pid = add_person(lead["name"], oid)
            add_rel("leads", pid, target, [oid], day, role=lead["title"], dates_status="unknown",
                    dates_note="the leadership page states no start date",
                    status_note="stated by the latest cited leadership page; not re-verified since" if named else
                    "the title names no organization; the page is the organization's own leadership page, so the edge is inferred")
            if not named:
                rels[-1]["evidence_class"] = "inferred"
    chart = by_url(manifest, CHART_URL)
    if chart is not None and CHART_NODE in nodes:
        text = pdf_text(ROOT / chart["path"])
        flat = squash(text)
        day = chart["retrieved_at"][:10]
        dates.append(day)
        for lead in chart_leads(text):
            if lead["node"] not in nodes:
                continue
            head_oid = add_obs(chart, day, "naming", squash(lead["heading"]), [lead["node"]])
            name_cell = squash(lead["name_cell"])
            assert name_cell in flat and squash(lead["heading"]) in flat, lead
            oid = add_obs(chart, day, "existence", name_cell, [lead["node"], person_id(chart_name(lead["name_cell"]))])
            pid = add_person(chart_name(lead["name_cell"]), oid)
            add_rel("leads", pid, lead["node"], [head_oid, oid], day, role=lead["role"], dates_status="unknown",
                    dates_note="the chart states no start date; it is dated 'Data current a/o 18 September 2026'",
                    status_note="read from the chart's column layout: the name is the cell beneath the heading in the heading's "
                                "column, the role word beneath that; a layout reading, so this edge is inferred")
            rels[-1]["evidence_class"] = "inferred"
    # A reading, not a statement: SAM.gov files the Space Systems Command under the department, with no Space Force level.
    ssc = next((nid for key, nid in ids.items() if saved[key][1].get("code") == "SSC"), None)
    if ssc:
        interpretations.append({
            "id": "int:airforce:001",
            "statement": "Space Systems Command is a field command of the U.S. Space Force; SAM.gov's organization records file it directly "
                         "under the Department of the Air Force with no Space Force level, so the seed does the same until an official "
                         "Space Force page is saved.",
            "subject_ids": [ssc], "observation_ids": [o["id"] for o in obs if ssc in o["subject_ids"]],
            "confidence": "medium",
            "basis": "the SAM.gov record's fullParentPathName ends 'DEPT OF THE AIR FORCE.SPACE SYSTEMS COMMAND'; no saved page states the Space Force "
                     "level (ssc.spaceforce.mil refuses this address), so the reading rests on the record's silence",
            "competing_readings": ["the department is the right parent for a contracting hierarchy", "a Space Force node should sit between them"],
            "what_would_resolve": "a saved ssc.spaceforce.mil or spaceforce.mil page naming SSC's parent",
            "review_status": "draft", "drafted_by": {"actor": "assistant", "on": max(dates)}, "reviewed_by": None, "reviewed_on": None})
    return {"generated": max(dates), "contract": CONTRACT, "scope": SCOPE, "nodes": list(nodes.values()), "observations": obs,
            "relationships": rels, "interpretations": interpretations}


def dumps(seed: dict) -> str:
    return json.dumps(seed, indent=1, ensure_ascii=False) + "\n"


def build(check: bool = False) -> int:
    if KEY != "airforce":
        print(f"this reader builds the airforce memory; the profile is {KEY} (set AGENCY=airforce)", file=sys.stderr)
        return 2
    seed = build_seed(rows())
    text = dumps(seed)
    kinds: dict[str, int] = {}
    for n in seed["nodes"]:
        kinds[n["type"]] = kinds.get(n["type"], 0) + 1
    print(f"{len(seed['nodes'])} node(s) {kinds}, {len(seed['observations'])} observation(s), {len(seed['relationships'])} relationship(s), "
          f"{len(seed['interpretations'])} interpretation(s)")
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
    """The SAM.gov organization records the build reads: the department, each swept office, then every organization on
    a saved office's parent path (two passes: the paths are read from the office records)."""
    from fetch import collect_missing  # noqa: E402
    manifest = rows()
    wanted = [(SAM_ORG.format(k), f"SAM.gov federal organization record [{KEY}] {k}") for k in [DEPARTMENT_KEY, *OFFICE_KEYS.values()]]
    missed = collect_missing(wanted)
    manifest = rows()
    parents = [k for k in wanted_keys(manifest) if k not in {DEPARTMENT_KEY, *OFFICE_KEYS.values()}]
    missed += collect_missing([(SAM_ORG.format(k), f"SAM.gov federal organization record [{KEY}] {k}") for k in parents])
    return 1 if missed else 0


def selfcheck() -> int:
    org = {"orgKey": 500019028, "name": "FA8730  KESSEL RUN AFLCMC/HBBK", "type": "OFFICE", "parentOrgKey": 500202881, "aacCode": "FA8730",
           "code": "FA8730", "level": 7, "startDate": "2025-10-01T04:00:00.000+00:00",
           "fullParentPathName": "DEPT OF DEFENSE.DEPT OF THE AIR FORCE.AIR FORCE MATERIEL COMMAND.AIR FORCE LIFE CYCLE MANAGEMENT CENTER.X"}
    raw = '{"_embedded":[{"org":' + json.dumps(org, separators=(",", ":")) + '}]}'
    assert node_kind(org, "500019028") == "contracting_office" and node_id(org, "500019028") == "contracting:fa8730"
    assert display_name(org) == "KESSEL RUN AFLCMC/HBBK", display_name(org)
    p = passage(raw, org)
    assert p in raw and p.startswith('"name":"FA8730  KESSEL RUN AFLCMC/HBBK"') and p.endswith('.X"'), p
    peo = {"orgKey": 500202881, "name": "COMMAND, CONTROL AND COMMUNICATION BATTLE MANAGEMENT - HANSCOM AIR FORCE B", "type": "SUB COMMAND",
           "code": "PAE C3BM-HANSCOM", "level": 6}
    assert node_kind(peo, "500202881") == "program_executive_office" and node_id(peo, "500202881") == "peo:c3bm-hanscom"
    assert display_name(peo).startswith("PAE COMMAND, CONTROL") and display_name({"name": "WEAPONS", "code": "PEO-WEAPONS"}) == "PEO WEAPONS"
    assert display_name({"name": "PEO SPACE SENSING", "code": "PEO SN"}) == "PEO SPACE SENSING"
    assert node_kind({"code": "AFRL", "type": "SUB COMMAND", "name": "AIR FORCE RESEARCH LABORATORY"}, "1") == "technical_center"
    assert node_kind({"code": "HANSCOM AFLCMC", "type": "SUB COMMAND", "name": "HANSCOM AIR FORCE LIFE CYCLE MANAGEMENT CENTER"}, "1") == "department"
    assert node_kind({"code": "AFMC", "type": "MAJOR COMMAND", "name": "AIR FORCE MATERIEL COMMAND"}, "1") == "command"
    assert node_kind({"code": "5700", "type": "AGENCY", "name": "DEPT OF THE AIR FORCE"}, DEPARTMENT_KEY) == "agency"
    assert node_id({"code": "SSC", "type": "MAJOR COMMAND", "name": "SPACE SYSTEMS COMMAND"}, "2") == "command:ssc"
    # Leadership pages: "Rank NAME Title, Organization" link text; head roles only, retired officers never.
    assert bio_entry("Lieutenant General PHILIP A. GARRANT Commander, Space Systems Command") == \
        ("Lieutenant General", "PHILIP A. GARRANT", "Commander, Space Systems Command")
    assert bio_entry("Major General Scott A. Cain Deputy Commander - Operations") == ("Major General", "Scott A. Cain", "Deputy Commander - Operations")
    assert bio_entry("DR. TROY E. MEINK The 27th Secretary of the Air Force") == ("DR.", "TROY E. MEINK", "The 27th Secretary of the Air Force")
    assert bio_entry("Chief Master Sergeant of the Air Force DAVID R. WOLFE 21st Chief Master Sgt. of the Air Force")[1] == "DAVID R. WOLFE"
    assert bio_entry("KATHY L. WATERN Executive Director") == ("", "KATHY L. WATERN", "Executive Director")
    page = ('<a href="https://www.afsc.af.mil/About-Us/Biographies/Display/Article/1/a/"><span>Lieutenant General JENNIFER HAMMERSTEDT</span> '
            'Commander, Air Force Sustainment Center</a> <a href="https://www.afsc.af.mil/About-Us/Biographies/Display/Article/2/b/">'
            'Colonel DEEDRICK L. REESE Deputy Commander, Air Force Sustainment Center</a> '
            '<a href="https://www.af.mil/About-Us/Biographies/Display/Article/3/c/">Major General DAVID W. ABBA Retired November 01, 2024</a>')
    leads = bio_leads(page)
    assert [l["name"] for l in leads] == ["JENNIFER HAMMERSTEDT"] and leads[0]["passage"].startswith("Lieutenant General JENNIFER HAMMERSTEDT Commander"), leads
    # The chart: a heading's column holds the name, then the role word.
    chart = ("   Fighters & Advanced Aircraft Dir            Bombers Directorate\n"
             " Brig Gen Timoth Helfrich    W-Patt     Brig Gen Timothy Spaulding W-Patt\n"
             " SEL: CMSgt Ryan Knickerbocker          SEL: SMSgt Ryan Rehanek (acting)\n"
             " (WA)         PAE                       (WB)         PAE\n"
             "                    COMMANDER\n\n              LT GEN JASON VOORHEIS\n")
    found = {l["node"]: (l["name_cell"], l["role"]) for l in chart_leads(chart)}
    assert found == {"peo:fight-adv-acft": ("Brig Gen Timoth Helfrich", "PAE"), "command:aflcmc": ("LT GEN JASON VOORHEIS", "Commander")}, found
    assert chart_person("Brig Gen Joshua Williams Hanscom") == "Brig Gen Joshua Williams" and chart_name("MR. DENNIS D'ANGELO, SES") == "DENNIS D'ANGELO" and chart_name("Mr. Rodney Stevens, SES      W-Patt") == "Rodney Stevens"
    print("org_memory_airforce selfcheck ok")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--selfcheck" in argv:
        sys.exit(selfcheck())
    if argv and argv[0] == "collect":
        sys.exit(collect())
    if argv and argv[0] == "--check":
        sys.exit(build(check=True))
    if argv and argv[0] == "build":
        sys.exit(build())
    print(__doc__)
    sys.exit(2)

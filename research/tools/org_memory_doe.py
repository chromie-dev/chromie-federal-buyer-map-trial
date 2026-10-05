#!/usr/bin/env python3
"""Read the Department of Energy's own publications into the organization memory (the `memory` stage of the doe profile).

    AGENCY=doe python research/tools/org_memory_doe.py build     # research/agencies/doe/memory/organization_seed.json
    AGENCY=doe python research/tools/org_memory_doe.py --check   # exit 1 when a build would change the file
    AGENCY=doe python research/tools/org_memory_doe.py collect   # fetch the documents the build reads, not yet saved
    python research/tools/org_memory_doe.py --selfcheck

Four sources, each node resting on a dated observation in the source's own words:

- SAM.gov federal organization records: the department (100011980), the agency record SAM.gov prints under it with
  the same name and FPDS code 8900 (100011981, so one node carries both), and the contracting offices the profile
  sweeps, each with the six-digit office code (AAC) its record states. USAspending's agency answer adds the toptier
  code 089.
- The Headquarters and Federal Field Office Acquisition Forecast (CSV): every value of its Program Office column is
  a program office as the forecast prints it. The forecast prints no hierarchy, so each program office's edge to the
  department is `inferred` and says so.
- The Office of Small Business Programs acquisition forecast page: its list of "National Laboratories/M&O/FMC
  procurements at DOE/NNSA contractor-managed sites". Each national laboratory is a technical_center and each other
  contractor-managed site a field_activity, `part_of` the department and reached through the procurement page the
  list links (`codes.subcontract_route`). They are not contracting offices of the department: no `contracts_for`.
  A list entry that names an office the memory already has (Idaho Operations Office) is an alias of it.
- One SAM.gov search page of the department's active notices: a laboratory's contractor office (the 899xxx
  offices SAM.gov files under the department) is attached to the laboratory only where the office's printed name
  names it or one of its notices states the management and operating contract; the office code goes under
  `contractor_office_code`, never `uic`, so no reader takes it for a buying office.

Every record is `review_status: draft` and carries `generator: org_memory_doe`; a rebuild is byte for byte the
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
from agency import KEY, MEMORY, NOTE_TAG, P, ROOT  # noqa: E402
from lrae_package import RELEASES, read_sheet  # noqa: E402
from org_memory_army import SAM_FIELDS, SAM_ORG, by_url, dumps, rows, sam_parent, sam_record, slug  # noqa: E402
from org_memory_darpa import node, observation, relationship, unique_aliases  # noqa: E402
from org_memory_lrae import squash  # noqa: E402

SEED = MEMORY / "organization_seed.json"
GENERATOR = "org_memory_doe"
AGENCY_ID = "agency:doe"
DEPARTMENT_ORG, SUBTIER_ORG = "100011980", "100011981"
# SAM.gov organization id -> node: the department, the agency record under it, the swept contracting offices.
SAM_NODES = {DEPARTMENT_ORG: AGENCY_ID, SUBTIER_ORG: AGENCY_ID, **P.get("sam_org_nodes", {})}
USASPENDING_URL = "https://api.usaspending.gov/api/v2/agency/089/"
SEARCH_URL = ("https://sam.gov/api/prod/sgs/v1/search/?index=opp&page=0&size=100&mode=search&is_active=true"
              "&organization_id=100011980&sort=-modifiedDate")
OSBP_URL = "https://www.energy.gov/osdbu/acquisition-forecast"  # redirects to /osbp/acquisition-forecast
FORECAST_URL = "https://www.energy.gov/documents/osbp-acquisition-forecast-public-version-web-20260911"
FORECAST = RELEASES[0] if RELEASES else {}
LIST_START = "Explore the list below of National Laboratories/M&amp;O/FMC procurements"
LIST_END = "Small Business Toolbox"
# The contractor-managed sites of the page's list: (link text as printed, node id, type, name, abbreviation, a word
# the SAM.gov contractor office's printed name carries when it names the site, or None when no office name does).
SITES = [
    ("Ames Laboratory (AMES)", "center:ames", "technical_center", "Ames Laboratory", "AMES", "AMES LABORATORY"),
    ("Argonne National Laboratory (ANL)", "center:anl", "technical_center", "Argonne National Laboratory", "ANL", "ARGONNE"),
    ("Brookhaven National Laboratory (BNL)", "center:bnl", "technical_center", "Brookhaven National Laboratory", "BNL", "BROOKHAVEN"),
    ("Fermi National Accelerator Laboratory (FNAL)", "center:fnal", "technical_center", "Fermi National Accelerator Laboratory", "FNAL", "FERMILAB"),
    ("Idaho National Laboratory (INL)", "center:inl", "technical_center", "Idaho National Laboratory", "INL", None),
    ("Lawrence Berkeley National Laboratory (LBNL)", "center:lbnl", "technical_center", "Lawrence Berkeley National Laboratory", "LBNL", "BERKELEY NATL LAB"),
    ("Lawrence Livermore National Laboratory (LLNL)", "center:llnl", "technical_center", "Lawrence Livermore National Laboratory", "LLNL", None),
    ("Los Alamos National Laboratory (LANL)", "center:lanl", "technical_center", "Los Alamos National Laboratory", "LANL", None),
    ("National Renewable Energy Laboratory (NREL)", "center:nrel", "technical_center", "National Renewable Energy Laboratory", "NREL", None),
    ("Oak Ridge National Laboratory (ORNL)", "center:ornl", "technical_center", "Oak Ridge National Laboratory", "ORNL", "ORNL"),
    ("Pacific Northwest National Laboratory (PNNL)", "center:pnnl", "technical_center", "Pacific Northwest National Laboratory", "PNNL", "PNNL"),
    ("Princeton Plasma Physics Laboratory (PPPL)", "center:pppl", "technical_center", "Princeton Plasma Physics Laboratory", "PPPL", None),
    ("Sandia National Laboratories (Sandia/SNL)", "center:snl", "technical_center", "Sandia National Laboratories", "SNL", None),
    ("Savannah River National Laboratory", "center:srnl", "technical_center", "Savannah River National Laboratory", None, None),
    ("SLAC National Accelerator Laboratory", "center:slac", "technical_center", "SLAC National Accelerator Laboratory", "SLAC", "SLAC"),
    ("Thomas Jefferson National Accelerator Facility (TJNAF)", "center:tjnaf", "technical_center", "Thomas Jefferson National Accelerator Facility", "TJNAF", "JEFFERSON LAB"),
    ("Naval Nuclear Laboratory, formerly known as Bettis/Knolls Atomic Power Laboratories (Bettis/KAPL)", "center:nnl", "technical_center",
     "Naval Nuclear Laboratory", None, None),
    ("Kansas City National Security Campus ((formerly Kansas City Plant (KCP))", "site:kcnsc", "field_activity", "Kansas City National Security Campus", None, None),
    ("Nevada National Security Site (NNSS)", "site:nnss", "field_activity", "Nevada National Security Site", "NNSS", None),
    ("Oak Ridge Institute for Science and Education (ORISE)", "site:orise", "field_activity", "Oak Ridge Institute for Science and Education", "ORISE", None),
    ("NNSA Production Office (NPO) Pantex Plant and Y-12 National Security Complex", "site:pantex-y12", "field_activity",
     "Pantex Plant and Y-12 National Security Complex", None, None),
    ("Management and Operations for Savannah River Site (SRS)", "site:srs", "field_activity", "Savannah River Site", "SRS", None),
]
MO_STATEMENT = re.compile(r"Manag\w* (?:and|&) Operat\w* Contractor (?:of|for) ", re.I)
MO_REACH = 120  # characters: the site a statement names follows its "Management and Operating Contractor of"
CONTRACT = ("08_org_memory_format.md: observations are what a source states; relationships are dated claims resting on "
            "observations; interpretations are our readings; corrections are retractions")
SCOPE = ("Department of Energy acquisition: the department, the contracting offices swept, the program offices its "
         "Headquarters and Federal Field Office forecast names, and the national laboratories and other contractor-managed "
         "sites that buy through subcontracts under their management and operating contracts")
NOTES = ("drafted by org_memory_doe from SAM.gov organization records, USAspending, the DOE acquisition forecast and the "
         "Office of Small Business Programs acquisition forecast page; names as printed in the source")
SITE_NOTES = ("a contractor-managed site: its procurement page is a subcontract route under the management and operating "
              "contract, not a contracting office of the department; ")


def bare(text: str) -> str:
    """A name compared across sources: a trailing abbreviation in parentheses dropped, case folded."""
    return re.sub(r"\s*\([^()]*\)$", "", squash(text)).lower()


def program_offices(rows_: list[dict]) -> Counter:
    """Program Office value -> rows, as the forecast prints it (lrae_package maps the column to `command`)."""
    return Counter(r["command"].strip() for r in rows_ if (r.get("command") or "").strip())


def site_links(raw: str) -> list[tuple[str, str, str]]:
    """(link text, href, anchor as saved) of each entry of the page's contractor-managed site list."""
    start = raw.find(LIST_START)
    end = raw.find(LIST_END, start)
    if start < 0 or end < 0:
        return []
    return [(squash(re.sub(r"<[^>]+>", " ", m[2])), m[1].strip(), m[0])
            for m in re.finditer(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', raw[start:end], re.S)]


def listing_passage(raw: str) -> str:
    """The page's own sentences on where the department's procurement goes and what the list below is."""
    text = squash(re.sub(r"<[^>]+>", " ", raw))
    share = re.search(r"Approximately 80% of DOE's annual procurement base[^.]*\.", text)
    listing = re.search(r"Explore the list below of National Laboratories/M&O/FMC procurements[^:]*:", text)
    return " ".join(m[0] for m in (share, listing) if m)


def notice_offices(search: dict) -> list[tuple[dict, str]]:
    """(the lowest office a notice's hierarchy prints, the notice's description text) for each notice on the page."""
    out = []
    for hit in (search.get("_embedded") or {}).get("results") or []:
        offices = [o for o in hit.get("organizationHierarchy") or [] if o.get("level", 0) >= 3]
        if offices:
            text = squash(re.sub(r"<[^>]+>", " ", " ".join(d.get("content") or "" for d in hit.get("descriptions") or [])))
            out.append((offices[-1], text))
    return out


def mo_statement(text: str, site_name: str) -> str:
    """The notice's own words that its office's company is the management and operating contractor of the site,
    from the sentence start to the site's name (and the abbreviation after it); '' when the notice states none."""
    for m in MO_STATEMENT.finditer(text):
        at = text.find(site_name, m.end(), m.end() + MO_REACH)
        if at < 0:
            continue
        start = text.rfind(". ", 0, m.start())
        tail = re.match(r"\s*\([A-Z-]+\)", text[at + len(site_name):])
        return text[start + 2 if start >= 0 else 0:at + len(site_name) + (tail.end() if tail else 0)].strip()
    return ""


def contractor_offices(notices: list[tuple[dict, str]]) -> dict[str, tuple[dict, str]]:
    """Site node id -> (the SAM.gov contractor office, the passage stating it is the site's): a notice's M&O
    statement first, else the office's own printed name; one office per site, one site per office."""
    out, taken = {}, set()
    for nid, name, token in ((s[1], s[3], s[5]) for s in SITES):
        found = next(((o, p) for o, t in notices if o["organizationId"] not in taken and (p := mo_statement(t, name))), None)
        if not found and token:
            found = next(((o, json.dumps({k: o[k] for k in ("organizationId", "code", "name")}, ensure_ascii=False, separators=(",", ":")))
                          for o, _ in notices if o["organizationId"] not in taken and token in o["name"].upper()), None)
        if found:
            out[nid] = found
            taken.add(found[0]["organizationId"])
    return out


# ---------------------------------------------------------------- the seed

def build_seed(manifest: list[dict]) -> dict:
    obs, rels, nodes = [], [], []
    counter = {"obs": 0, "rel": 0}

    def add_obs(row, observed_at, kind, passage, subjects) -> str:
        counter["obs"] += 1
        oid = f"obs:doe:{counter['obs']:03d}"
        obs.append(observation(oid, row, observed_at, kind, passage, subjects))
        return oid

    def add_rel(kind, src, dst, obs_ids, as_of, evidence="directly_documented", **kw) -> None:
        counter["rel"] += 1
        rel = relationship(f"rel:doe:{counter['rel']:03d}", kind, src, dst, obs_ids, as_of, **kw)
        rel["generator"] = GENERATOR
        rel["evidence_class"] = evidence
        rels.append(rel)

    def add_node(*args, **kw) -> dict:
        n = node(*args, **kw)
        n["generator"] = GENERATOR
        n["notes"] = kw.get("notes", NOTES)
        nodes.append(n)
        return n

    def also(n: dict, text: str, oid: str) -> None:
        n["aliases"] = unique_aliases(n["aliases"] + [{"text": text, "observation_ids": [oid]}])
        n["observation_ids"].append(oid)

    sam = {oid: by_url(manifest, SAM_ORG.format(oid)) for oid in SAM_NODES}
    docs = {"usaspending": by_url(manifest, USASPENDING_URL), "search": by_url(manifest, SEARCH_URL),
            "osbp": by_url(manifest, OSBP_URL), "forecast": by_url(manifest, FORECAST_URL)}
    missing = [k for k, v in (*docs.items(), *((f"sam {k}", v) for k, v in sam.items())) if v is None]
    if missing:
        raise SystemExit(f"documents not saved yet: {', '.join(missing)}; run `collect` first")
    day = lambda row: row["retrieved_at"][:10]  # noqa: E731

    # The department: USAspending's toptier record and the two SAM.gov records SAM prints for it.
    spend = json.loads((ROOT / docs["usaspending"]["path"]).read_text(encoding="utf-8"))
    spend_obs = add_obs(docs["usaspending"], day(docs["usaspending"]), "naming",
                        json.dumps({k: spend[k] for k in ("toptier_code", "name", "abbreviation")}, separators=(",", ":")), [AGENCY_ID])
    dept_obs, codes = [], {"toptier_code": str(spend["toptier_code"])}
    for org_id, key in ((DEPARTMENT_ORG, "sam_organization_id"), (SUBTIER_ORG, "sam_subtier_organization_id")):
        rec = sam_record(sam[org_id])
        dept_obs.append(add_obs(sam[org_id], day(sam[org_id]), "naming",
                                json.dumps({k: rec[k] for k in SAM_FIELDS if rec.get(k) is not None}, separators=(",", ":"), ensure_ascii=False), [AGENCY_ID]))
        codes[key] = org_id
        codes.setdefault("fpds_agency_id", str(rec.get("fpdsCode") or P["agency"]["subtier_code"]))
        codes.setdefault("cgac", str(rec.get("cgac") or ""))
    dept = add_node(AGENCY_ID, "agency", spend["name"], [spend_obs, *dept_obs],
                    unique_aliases([{"text": spend["abbreviation"], "observation_ids": [spend_obs]},
                                    {"text": squash(sam_record(sam[DEPARTMENT_ORG]).get("name") or ""), "observation_ids": dept_obs}]), codes)

    # The contracting offices the profile sweeps, each on its own record.
    for org_id, nid in SAM_NODES.items():
        if nid == AGENCY_ID:
            continue
        row, rec = sam[org_id], sam_record(sam[org_id])
        oid = add_obs(row, day(row), "naming", json.dumps({k: rec[k] for k in SAM_FIELDS if rec.get(k) is not None},
                                                          separators=(",", ":"), ensure_ascii=False), [nid])
        aac = str(rec.get("aacCode") or "").strip()
        add_node(nid, "contracting_office", squash(str(rec.get("name") or "")) or P["fpds_offices"].get(aac, nid), [oid],
                 [{"text": aac, "observation_ids": [oid]}] if aac else [], {"sam_organization_id": org_id} | ({"uic": aac} if aac else {}))
        parent = sam_parent(rec, SAM_NODES)
        if parent:
            add_rel("child_of", nid, parent, [oid], day(row), dates_status="unknown",
                    dates_note="the record states the organization's place in the hierarchy, not since when",
                    status_note=f"stated by the SAM.gov organization record of the latest cited retrieval ({rec.get('fullParentPathName')}); "
                                "not re-verified since")
        add_rel("contracts_for", nid, AGENCY_ID, [oid], day(row), role="contracting office of the Department of Energy",
                dates_status="unknown", status_note="stated by the SAM.gov organization record; not re-verified since")

    by_name = {bare(n["name"]): n for n in nodes}

    # The forecast: its Program Office values, as printed.
    forecast = docs["forecast"]
    _, forecast_rows = read_sheet(ROOT / forecast["path"], FORECAST["key"], FORECAST["sheet"], FORECAST["header_row"])
    released = FORECAST["release_date"]
    for office, n in sorted(program_offices(forecast_rows).items()):
        have = by_name.get(bare(office))  # the office the memory already has under its SAM.gov name
        nid = have["id"] if have else f"office:{slug(office)}"
        oid = add_obs(forecast, released, "existence", f"column 'Program Office': '{office}' ({n} rows)", [nid])
        if have:
            also(have, office, oid)
            continue
        by_name[bare(office)] = add_node(nid, "program_office", office, [oid],
                                         notes="named in the Program Office column of the DOE acquisition forecast; " + NOTES)
        add_rel("child_of", nid, AGENCY_ID, [oid], released, evidence="inferred", dates_status="unknown",
                dates_note="the forecast names the office, not since when",
                status_note="the generator's inference: the Department's own Headquarters and Federal Field Office forecast names this "
                            "office in its Program Office column; the forecast prints no hierarchy")

    # The contractor-managed sites of the small business page, and the contractor office each posts notices under.
    osbp = docs["osbp"]
    raw = (ROOT / osbp["path"]).read_text(encoding="utf-8", errors="replace")
    listing = add_obs(osbp, day(osbp), "existence", listing_passage(raw), [AGENCY_ID] + [s[1] for s in SITES])
    search = docs["search"]
    offices = contractor_offices(notice_offices(json.loads((ROOT / search["path"]).read_text(encoding="utf-8"))))
    sites = {s[0]: s for s in SITES}
    for text, href, anchor in site_links(raw):
        spec = sites.get(text)
        if spec is None:
            if bare(text) in by_name:  # a department office the list links (Idaho Operations Office)
                also(by_name[bare(text)], text, add_obs(osbp, day(osbp), "naming", anchor, [by_name[bare(text)]["id"]]))
            continue  # a contract (Tank Operations Contract), not an organization, or an office the memory lacks
        _, nid, kind, name, abbreviation, _ = spec
        if any(n["id"] == nid for n in nodes):
            continue
        oid = add_obs(osbp, day(osbp), "existence", anchor, [nid])
        aliases = [{"text": t, "observation_ids": [oid]} for t in (text, abbreviation) if t and t != name]
        codes = {"subcontract_route": href}
        if nid in offices:
            office, passage = offices[nid]
            sam_obs = add_obs(search, day(search), "naming", passage, [nid])
            aliases.append({"text": squash(office["name"]), "observation_ids": [sam_obs]})
            codes |= {"contractor_sam_organization_id": str(office["organizationId"]), "contractor_office_code": str(office["code"])}
        node_ = add_node(nid, kind, name, [oid, listing], unique_aliases(aliases), codes, notes=SITE_NOTES + NOTES)
        if nid in offices:
            node_["observation_ids"].append(sam_obs)
        add_rel("part_of", nid, AGENCY_ID, [oid, listing], day(osbp), role="DOE/NNSA contractor-managed site", dates_status="unknown",
                dates_note="the page lists the site, not since when",
                status_note="listed by the Office of Small Business Programs acquisition forecast page of the latest cited retrieval "
                            "among the National Laboratories/M&O/FMC procurements; not re-verified since")

    dates = [released, *(day(r) for r in (*sam.values(), *docs.values()))]
    assert all(n["observation_ids"] for n in nodes), "every node rests on an observation"
    return {"generated": max(dates), "contract": CONTRACT, "scope": SCOPE, "nodes": nodes, "observations": obs,
            "relationships": rels, "interpretations": []}


def build(check: bool = False) -> int:
    if KEY != "doe":
        print(f"this reader builds the doe memory; the profile is {KEY} (set AGENCY=doe)", file=sys.stderr)
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
    """The SAM.gov records, the notice page, USAspending's agency answer and the two energy.gov documents, directly."""
    from fetch import collect_missing  # noqa: E402
    wanted = [(SAM_ORG.format(oid), f"SAM.gov federal organization record{NOTE_TAG}: {oid} ({nid})") for oid, nid in SAM_NODES.items()]
    wanted += [(USASPENDING_URL, f"USAspending agency overview, Department of Energy toptier 089{NOTE_TAG}"),
               (SEARCH_URL, f"SAM search{NOTE_TAG} active notices under the Department of Energy (organization 100011980), newest first, page 0"),
               (OSBP_URL, f"DOE OSDBU acquisition forecast page{NOTE_TAG}"),
               (FORECAST_URL, f"DOE Headquarters and Federal Field Office Acquisition Forecast document page{NOTE_TAG}")]
    return 1 if collect_missing(wanted) else 0


def selfcheck() -> int:
    known = {DEPARTMENT_ORG: AGENCY_ID, SUBTIER_ORG: AGENCY_ID, "100187021": "contracting:893039"}
    assert sam_parent({"fullParentPath": "100011980.100011981.100187021"}, known) == AGENCY_ID
    assert sam_parent({"fullParentPath": "100011980"}, known) is None
    assert bare("National Energy Technology Laboratory (NETL)") == bare("NATIONAL ENERGY TECHNOLOGY LABORATORY")
    assert bare("Savannah River Operations Office") == "savannah river operations office"
    assert program_offices([{"command": "Office of Science"}, {"command": "Office of Science "}, {"command": ""}]) == {"Office of Science": 2}
    raw = ('<p>Approximately 80% of DOE&rsquo;s annual procurement base is allocated to the Agency&rsquo;s Management and Operating '
           'Contractors (M&amp;Os).</p><p>Explore the list below of National Laboratories/M&amp;O/FMC procurements at DOE/NNSA '
           'contractor-managed sites:</p><a href="https://www.anl.gov/x">Argonne National Laboratory (ANL)</a>'
           '<a href="/y">Idaho Operations Office</a><a href="/osdbu/small-business-toolbox">Small Business Toolbox</a>')
    assert site_links(raw) == [("Argonne National Laboratory (ANL)", "https://www.anl.gov/x", '<a href="https://www.anl.gov/x">Argonne National Laboratory (ANL)</a>'),
                               ("Idaho Operations Office", "/y", '<a href="/y">Idaho Operations Office</a>')]
    assert listing_passage(raw).startswith("Approximately 80% of DOE's annual") and listing_passage(raw).endswith("contractor-managed sites:")
    inl = ("INTRODUCTION Battelle Energy Alliance, LLC (BEA), Management & Operating Contractor of the U.S. Department of Energy (DOE) "
           "owned Idaho National Laboratory (INL), is seeking an Expression of Interest")
    assert mo_statement(inl, "Idaho National Laboratory") == inl.split(", is seeking")[0]
    assert mo_statement("Idaho National Laboratory (INL) and Argonne National Laboratory (ANL) are seeking information", "Idaho National Laboratory") == ""
    assert mo_statement("Scope. UT-Battelle, LLC is the management and operating contractor of Oak Ridge National Laboratory (ORNL). The",
                        "Oak Ridge National Laboratory") == "UT-Battelle, LLC is the management and operating contractor of Oak Ridge National Laboratory (ORNL)"
    bea = {"organizationId": "500047763", "code": "899050", "name": "BATTELLE ENERGY ALLIANCE–DOE CNTR"}
    fnal = {"organizationId": "500047667", "code": "899034", "name": "FERMILAB - DOE CONTRACTOR"}
    found = contractor_offices([(bea, "Idaho National Laboratory (INL) and Argonne National Laboratory (ANL) are seeking"), (bea, inl), (fnal, "")])
    assert set(found) == {"center:inl", "center:fnal"} and found["center:inl"][0] is bea, found
    assert json.loads(found["center:fnal"][1])["code"] == "899034", "a name match quotes the office as printed"
    assert "center:anl" not in found, "a notice naming a laboratory beside its own does not make the office the laboratory's"
    print("org_memory_doe selfcheck ok")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--selfcheck" in argv:
        sys.exit(selfcheck())
    if argv and argv[0] == "collect":
        sys.exit(collect())
    if argv and argv[0] == "--check" or argv[:2] == ["build", "--check"]:
        sys.exit(build(check=True))
    if argv and argv[0] == "build":
        sys.exit(build())
    print(__doc__)
    sys.exit(2)

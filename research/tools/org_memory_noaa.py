#!/usr/bin/env python3
"""Read NOAA's own pages and the government-wide records into the organization memory (the `memory` stage of the
noaa profile).

    AGENCY=noaa python research/tools/org_memory_noaa.py build     # research/agencies/noaa/memory/organization_seed.json
    AGENCY=noaa python research/tools/org_memory_noaa.py build --check   # exit 1 when a build would change the file
    AGENCY=noaa python research/tools/org_memory_noaa.py collect   # fetch the documents the build reads, not yet saved
    python research/tools/org_memory_noaa.py selfcheck

Five sources, each node resting on a dated observation in the source's own words:

- SAM.gov federal organization records: the Department of Commerce, NOAA and the contracting offices the profile
  sweeps, with the code and the hierarchy each record states; two saved SAM.gov notice search pages add the address
  each office's record prints, whose first line names the division ("EASTERN ACQUISITION DIVISION").
- USAspending: the Commerce toptier record (code 013) and the FY2026 sub-agency listing for contracts, which states
  NOAA's awarding offices by code with their obligations.
- noaa.gov "About our agency": the six line offices and the Acquisition and Grants Office among the corporate offices.
- noaa.gov "About AGO": the AGO acquisition divisions, where each is based and the line offices each supports, with
  the acronyms the page prints. A division is joined to a SAM.gov office only where the office's address line prints
  the division's name; no source here states the division of 1305M4 ("STRATEGIC SOURCING ACQUISITION DIV."), so none
  is claimed.
- The Commerce weekly procurement forecast: the Office and Organization Unit columns of NOAA's rows, as printed.

Every record is `review_status: draft` and carries `generator: org_memory_noaa`; a rebuild is byte for byte the
same while the saved documents are.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import KEY, MEMORY, NOTE_TAG, P, ROOT  # noqa: E402
from lrae_package import RELEASES, read_sheet  # noqa: E402
from org_memory_army import SAM_FIELDS, SAM_ORG, by_url, rows, sam_parent, sam_record, slug  # noqa: E402
from org_memory_darpa import node, observation, relationship, sentence_end, unique_aliases  # noqa: E402
from org_memory_lrae import page_text, squash  # noqa: E402

SEED = MEMORY / "organization_seed.json"
GENERATOR = "org_memory_noaa"
DEPARTMENT_ID, AGENCY_ID, AGO_ID = "department:doc", P["agency"]["node"], "command:ago"
SAM_NODES = {"100035122": DEPARTMENT_ID, **P["sam_org_nodes"]}
# Saved SAM.gov notice search pages whose organization hierarchy prints each office's address.
SAM_SEARCHES = ("https://sam.gov/api/prod/sgs/v1/search/?index=opp&page=0&size=25&q=NOAA&mode=search&is_active=true&sort=-modifiedDate",
                "https://sam.gov/api/prod/sgs/v1/search/?index=opp&page=0&size=25&q=1332KP&mode=search&is_active=false&sort=-modifiedDate")
TOPTIER_URL = "https://api.usaspending.gov/api/v2/agency/013/"
OFFICES_URL = "https://api.usaspending.gov/api/v2/agency/013/sub_agency/?fiscal_year=2026&award_type_codes=[A,B,C,D]"
ABOUT_URL = "https://www.noaa.gov/about-our-agency"
AGO_URL = "https://www.noaa.gov/acquisition-grants/about-ago"
FORECAST = RELEASES[0]
FORECAST_URL = "https://www.commerce.gov/sites/default/files/2022-12/DOC%20Weekly%20Forecast%20Report.xlsx"
# (node id, name as the about page prints it, acronym, name as the AGO page prints it before the acronym)
LINE_OFFICES = [
    ("office:nesdis", "National Environmental Satellite, Data, and Information Service", "NESDIS", "National Environmental Satellite, Data, and Information Service"),
    ("office:nmfs", "National Marine Fisheries Service", "NMFS", "National Marine Fisheries Service"),
    ("office:nos", "National Ocean Service", "NOS", "National Ocean Service"),
    ("office:nws", "National Weather Service", "NWS", "National Weather Service"),
    ("office:omao", "Office of Marine & Aviation Operations", "OMAO", "Office of Marine and Aviation Operations"),
    ("office:oar", "Office of Oceanic & Atmospheric Research", "OAR", "Oceanic and Atmospheric Research"),
]
# The AGO divisions that award: (name, acronym, node type when no SAM.gov office prints the name).
DIVISIONS = [("Corporate Services Acquisition Division", "CSAD", "contracting_office"),
             ("Satellite and Information Acquisition Division", "SIAD", "contracting_office"),
             ("Eastern Acquisition Division", "EAD", "contracting_office"), ("Western Acquisition Division", "WAD", "contracting_office"),
             ("Grants Management Division", "GMD", "department")]
ROLE_RE = re.compile(r"provides acquisition support to|is responsible for developing and executing contracts for")
CONTRACT = ("08_org_memory_format.md: observations are what a source states; relationships are dated claims resting on "
            "observations; interpretations are our readings; corrections are retractions")
SCOPE = ("National Oceanic and Atmospheric Administration: the Department of Commerce above it, NOAA, its six line offices, the "
         "Acquisition and Grants Office and its divisions, the contracting offices swept and the offices the Commerce forecast names")
NOTES = ("drafted by org_memory_noaa from SAM.gov organization records, USAspending, the noaa.gov About and About AGO pages and the "
         "Commerce procurement forecast; names as printed in the source")


def sentence_at(text: str, at: int) -> str:
    """The sentence holding position `at`: from the sentence's opening "The" (page headings run into the text) or the
    previous full stop, to the next full stop that is not an abbreviation's."""
    begin = text.rfind(". ", 0, at) + 2 if text.rfind(". ", 0, at) >= 0 else 0
    opener = text.rfind("The ", begin, at + 1)
    return text[opener if opener >= 0 else begin:sentence_end(text, at)].strip()


def division_of(street: str, divisions: list[tuple[str, str, str]]) -> tuple[str, str, str] | None:
    """The AGO division an office's address line names: 'EASTERN ACQUISITION DIVISION', or SAM.gov's cut-off
    'SATELLITE AND INFORMATION ACQUISITI', opens the division's name."""
    line = street.strip().upper()
    return next((d for d in divisions if line and len(line) >= 20 and d[0].upper().startswith(line)), None)


def addresses(manifest: list[dict]) -> tuple[dict[str, tuple[dict, dict]], list[str]]:
    """orgKey -> (search row, hierarchy entry) of the first saved search page printing the office, and the pages missing."""
    out, missing = {}, []
    for url in SAM_SEARCHES:
        row = by_url(manifest, url)
        if row is None:
            missing.append(url)
            continue
        for result in json.loads((ROOT / row["path"]).read_text(encoding="utf-8")).get("_embedded", {}).get("results", []):
            for org in result.get("organizationHierarchy") or []:
                if org.get("level") == 3 and org.get("organizationId") in SAM_NODES:
                    out.setdefault(org["organizationId"], (row, org))
    return out, missing


def forecast_units(rows_: list[dict]) -> Counter:
    """(Office, Organization Unit) -> rows for NOAA's rows of the Commerce forecast (Organization 'NOAA - ...')."""
    return Counter((r["command"], r["pm_directorate"]) for r in rows_ if r.get("organization", "").startswith("NOAA") and r["command"])


def build_seed(manifest: list[dict]) -> dict:
    obs, rels, nodes = [], [], []
    counter = {"obs": 0, "rel": 0}

    def add_obs(row, observed_at, kind, passage, subjects) -> str:
        counter["obs"] += 1
        oid = f"obs:noaa:{counter['obs']:03d}"
        obs.append(observation(oid, row, observed_at, kind, passage, subjects))
        return oid

    def add_rel(kind, src, dst, obs_ids, as_of, **kw) -> None:
        counter["rel"] += 1
        rel = relationship(f"rel:noaa:{counter['rel']:03d}", kind, src, dst, obs_ids, as_of, **kw)
        rel["generator"] = GENERATOR
        rels.append(rel)

    said: dict[str, str] = {}

    def ago_obs(sentence: str, subject: str, kind: str) -> str:
        """One observation per sentence of the About AGO page, naming every node that sentence states."""
        if sentence in said:
            seen = next(o for o in obs if o["id"] == said[sentence])
            seen["subject_ids"] += [subject] * (subject not in seen["subject_ids"])
            if kind == "contracting_support":  # a division's own sentence states whom it contracts for
                seen["statement_type"] = kind
            return said[sentence]
        said[sentence] = add_obs(pages["ago"], day(pages["ago"]), kind, sentence, [subject])
        return said[sentence]

    def add_node(*args, **kw) -> dict:
        n = node(*args, **kw)
        n["generator"] = GENERATOR
        n["notes"] = kw.get("notes", NOTES)
        nodes.append(n)
        return n

    sam = {oid: by_url(manifest, SAM_ORG.format(oid)) for oid in SAM_NODES}
    pages = {name: by_url(manifest, url) for name, url in (("toptier", TOPTIER_URL), ("offices", OFFICES_URL), ("about", ABOUT_URL),
                                                           ("ago", AGO_URL), ("forecast", FORECAST_URL))}
    printed, searches_missing = addresses(manifest)
    missing = [k for k, v in (*pages.items(), *((f"sam {k}", v) for k, v in sam.items())) if v is None] + searches_missing
    if missing:
        raise SystemExit(f"documents not saved yet: {', '.join(missing)}; run `collect` first")
    day = lambda row: row["retrieved_at"][:10]  # noqa: E731

    # USAspending: the department's toptier record and NOAA's awarding offices for contracts, FY2026.
    toptier = json.loads((ROOT / pages["toptier"]["path"]).read_text(encoding="utf-8"))
    top_obs = add_obs(pages["toptier"], day(pages["toptier"]), "naming", json.dumps(
        {k: toptier.get(k) for k in ("toptier_code", "name", "abbreviation", "website")}, separators=(",", ":"), ensure_ascii=False), [DEPARTMENT_ID])
    listing = json.loads((ROOT / pages["offices"]["path"]).read_text(encoding="utf-8"))
    noaa_listing = next(r for r in listing["results"] if r.get("abbreviation") == P["agency"]["subtier_abbreviation"])
    office_rows = {c["code"]: c for c in noaa_listing.get("children") or []}

    # SAM.gov: the department, NOAA and the swept contracting offices, each on its own record.
    about_text = page_text(ROOT / pages["about"]["path"])
    ago_text = page_text(ROOT / pages["ago"]["path"])
    division_ids: dict[str, str] = {}
    by_id: dict[str, dict] = {}
    for org_id, nid in SAM_NODES.items():
        row, rec = sam[org_id], sam_record(sam[org_id])
        passage = json.dumps({k: rec[k] for k in SAM_FIELDS if rec.get(k) is not None}, separators=(",", ":"), ensure_ascii=False)
        oids = [add_obs(row, day(row), "naming", passage, [nid])]
        codes = {"sam_organization_id": org_id}
        aac = str(rec.get("aacCode") or rec.get("code") or "").strip() if nid.startswith("contracting:") else ""
        aliases = [{"text": aac, "observation_ids": [oids[0]]}] if aac else []
        if nid == DEPARTMENT_ID:
            oids.append(top_obs)
            codes["toptier_code"] = toptier["toptier_code"]
            aliases += [{"text": toptier["abbreviation"], "observation_ids": [top_obs]}, {"text": squash(str(rec.get("name") or "")), "observation_ids": [oids[0]]}]
            by_id[nid] = add_node(nid, "department", toptier["name"], oids, unique_aliases(aliases), codes)
            continue
        if nid == AGENCY_ID:
            codes["fpds_agency_id"] = str(rec.get("fpdsCode") or P["agency"]["subtier_code"])
            oids.append(add_obs(pages["offices"], day(pages["offices"]), "naming", json.dumps(
                {k: noaa_listing.get(k) for k in ("name", "abbreviation", "total_obligations", "transaction_count", "new_award_count")},
                separators=(",", ":"), ensure_ascii=False), [nid]))
            mission = re.search(r"Our mission To understand and predict[^.]*\.", about_text)
            if mission:
                oids.append(add_obs(pages["about"], day(pages["about"]), "existence", mission.group(0), [nid]))
            aliases += [{"text": P["agency"]["subtier_abbreviation"], "observation_ids": [oids[1]]},
                        {"text": squash(str(rec.get("name") or "")), "observation_ids": [oids[0]]}]
            by_id[nid] = add_node(nid, "agency", P["agency"]["subtier_name"], oids, unique_aliases(aliases), codes)
        else:
            codes["uic"] = aac
            if aac in office_rows:
                oids.append(add_obs(pages["offices"], day(pages["offices"]), "naming", json.dumps(office_rows[aac], separators=(",", ":"), ensure_ascii=False), [nid]))
            name = squash(str(rec.get("name") or "")) or aac
            place = printed.get(org_id)
            if place:
                search_row, org = place
                oids.append(add_obs(search_row, day(search_row), "naming", json.dumps(org, separators=(",", ":"), ensure_ascii=False), [nid]))
                street = squash(str((org.get("address") or {}).get("streetAddress") or ""))
                division = division_of(street, DIVISIONS)
                name = division[0] if division else street or name
                if division:
                    division_ids[division[1]] = nid
                elif street:
                    aliases.append({"text": street, "observation_ids": [oids[-1]]})
            by_id[nid] = add_node(nid, "contracting_office", name, oids, unique_aliases(aliases), codes)
            add_rel("contracts_for", nid, AGENCY_ID, [oids[0]], day(row), role="contracting office of NOAA", dates_status="unknown",
                    status_note="stated by the SAM.gov organization record; not re-verified since")
        parent = sam_parent(rec, SAM_NODES)
        if parent and (nid == AGENCY_ID or nid not in division_ids.values()):
            add_rel("child_of", nid, parent, [oids[0]], day(row), dates_status="unknown",
                    dates_note="the record states the organization's place in the hierarchy, not since when",
                    status_note=f"stated by the SAM.gov organization record of the latest cited retrieval ({rec.get('fullParentPathName')}); "
                                "not re-verified since")

    # The about page: the line offices, and AGO among the corporate offices.
    lines = re.search(r"Line Offices (National Environmental Satellite.*?Office of Oceanic & Atmospheric Research)", about_text)
    line_obs = add_obs(pages["about"], day(pages["about"]), "listing", lines.group(0) if lines else "Line Offices", [o[0] for o in LINE_OFFICES])
    corporate = re.search(r"Corporate Offices Acquisition and Grants Office.*?Office of the Chief Information Officer", about_text)
    corp_obs = add_obs(pages["about"], day(pages["about"]), "listing", corporate.group(0) if corporate else "Corporate Offices", [AGO_ID])
    for nid, name, acr, ago_name in LINE_OFFICES:
        oids, aliases, codes = [line_obs], [], None
        at = ago_text.find(f"{ago_name} ({acr})")
        if at >= 0:
            oid = ago_obs(sentence_at(ago_text, at), nid, "existence")
            oids.append(oid)
            aliases = [{"text": acr, "observation_ids": [oid]}, {"text": ago_name, "observation_ids": [oid]}]
            codes = {"office_code": acr}
        by_id[nid] = add_node(nid, "department", name, oids, unique_aliases(aliases), codes)
        add_rel("child_of", nid, AGENCY_ID, [line_obs], day(pages["about"]),
                status_note="listed among NOAA's line offices by the About our agency page of the latest cited retrieval; not re-verified since")

    # About AGO: the office itself, then each division, the line offices it supports and where it stands.
    first = re.search(r"The Acquisition and Grants Office \(AGO\) provides[^.]*\.", ago_text)
    ago_first = add_obs(pages["ago"], day(pages["ago"]), "contracting_support", first.group(0) if first else "Acquisition and Grants Office", [AGO_ID])
    by_id[AGO_ID] = add_node(AGO_ID, "command", "Acquisition and Grants Office", [corp_obs, ago_first],
                             [{"text": "AGO", "observation_ids": [ago_first]}], {"office_code": "AGO"})
    add_rel("child_of", AGO_ID, AGENCY_ID, [corp_obs], day(pages["about"]),
            status_note="listed among NOAA's corporate offices by the About our agency page of the latest cited retrieval; not re-verified since")
    add_rel("contracts_for", AGO_ID, AGENCY_ID, [ago_first], day(pages["ago"]),
            role="planning, solicitation, award, administration, and closeout of acquisition and financial assistance transactions for NOAA Line and Staff Offices",
            dates_status="unknown")
    codes_by_acr = {acr: nid for nid, _, acr, _ in LINE_OFFICES}
    for name, acr, kind in DIVISIONS:
        at = ago_text.find(f"{name} ({acr})")
        if at < 0:
            continue
        sentence = sentence_at(ago_text, at)
        oid = ago_obs(sentence, division_ids.get(acr, f"office:{acr.lower()}"), "contracting_support" if kind == "contracting_office" else "existence")
        nid = division_ids.get(acr)
        if nid:
            n = by_id[nid]
            n["observation_ids"].append(oid)
            n["aliases"] = unique_aliases(n["aliases"] + [{"text": acr, "observation_ids": [oid]}, {"text": name, "observation_ids": [oid]}])
            n["codes"]["office_code"] = acr
        else:
            nid = f"office:{acr.lower()}"
            by_id[nid] = add_node(nid, kind, name, [oid], [{"text": acr, "observation_ids": [oid]}], {"office_code": acr},
                                  notes="named on the About AGO page; no SAM.gov office record of those saved prints its name; " + NOTES)
        add_rel("child_of", nid, AGO_ID, [oid], day(pages["ago"]),
                status_note="named among the AGO divisions by the About AGO page of the latest cited retrieval; not re-verified since")
        role = ROLE_RE.search(sentence)
        for client in sorted({codes_by_acr[a] for a in re.findall(r"\b(NESDIS|NWS|NMFS|NOS|OAR|OMAO)\b", sentence)}):
            add_rel("contracts_for", nid, client, [ago_obs(sentence, client, "contracting_support")], day(pages["ago"]),
                    role=role.group(0) if role else None, dates_status="unknown",
                    status_note="stated by the About AGO page of the latest cited retrieval; not re-verified since")

    # The Commerce forecast: the office and the unit code each NOAA row prints. A unit whose code is a node's is an
    # alias of it; one the memory lacks ("NOAA - OUS STAFF OFCS") is made from the value, under NOAA as its prefix prints.
    _, forecast_rows = read_sheet(ROOT / pages["forecast"]["path"], FORECAST["key"], FORECAST["sheet"], FORECAST["header_row"])
    fday = FORECAST["release_date"]
    by_code = {(n.get("codes") or {}).get("office_code"): n for n in nodes if (n.get("codes") or {}).get("office_code")}
    for (office, unit), count in sorted(forecast_units(forecast_rows).items()):
        acr = unit.removeprefix("NOAA - ").strip()
        target = by_code.get(acr)
        nid = target["id"] if target else f"office:{slug(acr)}"
        oid = add_obs(pages["forecast"], fday, "existence", f"column 'Office': '{office}'; column 'Organization Unit': '{unit}' ({count} rows)", [nid])
        aliases = [{"text": office, "observation_ids": [oid]}, {"text": unit, "observation_ids": [oid]}]
        if target:
            target["observation_ids"].append(oid)
            target["aliases"] = unique_aliases(target["aliases"] + aliases)
            continue
        by_code[acr] = add_node(nid, "department", office, [oid], unique_aliases(aliases[1:]), {"office_code": acr},
                                notes="named in the Office and Organization Unit columns of the Commerce procurement forecast; " + NOTES)
        add_rel("child_of", nid, AGENCY_ID, [oid], fday, dates_status="unknown",
                dates_note="the forecast's unit code prints the office under NOAA, not since when",
                status_note="stated by the forecast rows of the latest cited release; not re-verified since")

    dates = [fday, *(day(r) for r in pages.values()), *(day(r) for r in sam.values())]
    return {"generated": max(dates), "contract": CONTRACT, "scope": SCOPE, "nodes": nodes, "observations": obs,
            "relationships": rels, "interpretations": []}


def dumps(seed: dict) -> str:
    return json.dumps(seed, indent=1, ensure_ascii=False) + "\n"


def build(check: bool = False) -> int:
    if KEY != "noaa":
        print(f"this reader builds the noaa memory; the profile is {KEY} (set AGENCY=noaa)", file=sys.stderr)
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
    """The SAM.gov records, the USAspending records and the noaa.gov pages directly; the forecast through the Wayback
    Machine, since commerce.gov answers 403 to a direct request from this address."""
    from fetch import MANIFEST, collect_missing, fetch  # noqa: E402
    wanted = [(SAM_ORG.format(oid), f"SAM.gov federal organization record{NOTE_TAG}: {oid} ({nid})") for oid, nid in SAM_NODES.items()]
    wanted += [(u, f"SAM.gov notice search{NOTE_TAG}: one page naming NOAA offices") for u in SAM_SEARCHES]
    wanted += [(TOPTIER_URL, f"USAspending toptier agency record{NOTE_TAG}: Department of Commerce 013"),
               (OFFICES_URL, f"USAspending sub-agencies of Commerce FY2026, contracts only{NOTE_TAG}"),
               (ABOUT_URL, f"NOAA about our agency page, line offices{NOTE_TAG}"), (AGO_URL, f"NOAA About AGO page{NOTE_TAG}")]
    missed = collect_missing(wanted)
    if by_url(rows(), FORECAST_URL) is None:
        row = fetch(FORECAST_URL, "wayback", "closest", f"Commerce weekly procurement forecast workbook, Wayback capture{NOTE_TAG}")
        with MANIFEST.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        missed += not kept_page(row)
    return 1 if missed else 0


def selfcheck() -> int:
    text = ("Satellite and Information Acquisition The Satellite and Information Acquisition Division (SIAD), based in Silver Spring, MD, "
            "is responsible for developing and executing contracts for the National Environmental Satellite, Data, and Information Service "
            "(NESDIS) across the United States. Eastern Acquisition The Eastern Acquisition Division (EAD), based in Norfolk, VA, provides "
            "acquisition support to the National Weather Service (NWS) Headquarters Office.")
    s = sentence_at(text, text.find("(NESDIS)"))
    assert s.startswith("The Satellite and Information Acquisition Division (SIAD)") and s.endswith("across the United States."), s
    assert sentence_at(text, text.find("(NWS)")).startswith("The Eastern Acquisition Division (EAD)")
    assert ROLE_RE.search(s).group(0) == "is responsible for developing and executing contracts for"
    assert division_of("SATELLITE AND INFORMATION ACQUISITI", DIVISIONS)[1] == "SIAD"
    assert division_of("EASTERN ACQUISITION DIVISION", DIVISIONS)[1] == "EAD"
    assert division_of("STRATEGIC SOURCING ACQUISITION DIV.", DIVISIONS) is None and division_of("", DIVISIONS) is None
    assert division_of("OMAO FIELD DELEGATES", DIVISIONS) is None
    rows_ = [{"organization": "NOAA - PROGRAM OFFICE", "command": "National Weather Service", "pm_directorate": "NOAA - NWS"},
             {"organization": "NOAA - PROGRAM OFFICE", "command": "National Weather Service", "pm_directorate": "NOAA - NWS"},
             {"organization": "NIST - PROGRAM OFFICE", "command": "AD for Laboratory Programs", "pm_directorate": "NIST - ALP"}]
    assert forecast_units(rows_) == {("National Weather Service", "NOAA - NWS"): 2}, "NOAA's rows only"
    rec = {"fullParentPath": "100035122.100099213.100175863"}
    assert sam_parent(rec, SAM_NODES) == AGENCY_ID and sam_parent({"fullParentPath": "100035122.100099213"}, SAM_NODES) == DEPARTMENT_ID
    print("org_memory_noaa selfcheck ok")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--selfcheck" in argv or argv[:1] == ["selfcheck"]:
        sys.exit(selfcheck())
    if argv and argv[0] == "collect":
        sys.exit(collect())
    if argv and argv[0] in ("build", "--check"):
        sys.exit(build(check="--check" in argv))
    print(__doc__)
    sys.exit(2)

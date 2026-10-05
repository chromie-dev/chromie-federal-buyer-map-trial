#!/usr/bin/env python3
"""Read the Department of Homeland Security's own records into the organization memory (the `memory` stage of the dhs profile).

    AGENCY=dhs python research/tools/org_memory_dhs.py build           # research/agencies/dhs/memory/organization_seed.json
    AGENCY=dhs python research/tools/org_memory_dhs.py build --check   # exit 1 when a build would change the file
    AGENCY=dhs python research/tools/org_memory_dhs.py collect         # fetch the documents the build reads, not yet saved
    python research/tools/org_memory_dhs.py --selfcheck

Three sources, each node resting on a dated observation in the source's own words:

- SAM.gov federal organization records: the department's own record, the hierarchy listing of its components (the
  next level under the department) and the listing of each contracting component, with the office code (AAC) each
  entry states and the parent path it prints. Two offices stand past the first hundred entries of their component's
  listing (USCG HQ Contract Operations, the ICE Information Technology Division) and are read from their own records.
- USAspending: the FY2026 awarding sub-agencies and offices of toptier 070, with the obligations the answer states;
  the offices seeded are each component's largest awarders there (the profile lists them).
- The Acquisition Planning Forecast System (APFS) records, as the API behind the forecast page answers them: the
  Organization and Requirements Office columns name the requirement offices under each component, and the
  Contracting Office column of a headquarters row prints the office code beside the division that buys for it.

Every record is `review_status: draft` and carries `generator: org_memory_dhs`; a rebuild is byte for byte the same
while the saved documents are.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import KEY, MEMORY, NOTE_TAG, PROFILES, ROOT  # noqa: E402
from org_memory_army import SAM_ORG, by_url, dumps, rows, sam_parent, sam_record, slug  # noqa: E402
from org_memory_darpa import node, observation, relationship, unique_aliases  # noqa: E402
from org_memory_lrae import squash  # noqa: E402

SEED = MEMORY / "organization_seed.json"
GENERATOR = "org_memory_dhs"
# The DHS profile by name, not the running one: the pipeline runs every selfcheck under AGENCY=navy.
P = PROFILES["dhs"]
AGENCY_ID = P["agency"]["node"]
DEPARTMENT = "100011942"
HIERARCHY = "https://sam.gov/api/prod/federalorganizations/v1/org/hierarchy?fhorgid={}&limit=100&api_key=null"
# The components whose next level the build reads: OPO, CBP, FEMA, USCG, ICE, TSA, USSS, USCIS, FLETC. S&T, CISA and
# CWMD award through OPO's divisions (70RSAT, 70RCSJ, 70RWMD), so their own levels hold no seeded office.
LISTED = ("100013095", "100012587", "100011943", "100012855", "100012075", "100012177", "100012967", "100011968", "100012472")
USASPENDING = "https://api.usaspending.gov/api/v2/agency/070/sub_agency/?fiscal_year=2026&limit=100"
APFS = "https://apfs-cloud.dhs.gov/api/forecast/"
# The components (SAM.gov id -> node, from the profile) by the abbreviation USAspending and the forecast print.
COMPONENTS = {org: nid for org, nid in P["sam_org_nodes"].items() if nid.startswith("command:")}
OFFICES = {org: nid for org, nid in P["sam_org_nodes"].items() if nid.startswith("contracting:")}
ABBREVIATION = {"command:opo": "OPO", "command:st": "S&T", "command:cbp": "CBP", "command:cisa": "CISA", "command:tsa": "TSA",
                "command:fema": "FEMA", "command:uscg": "USCG", "command:ice": "ICE", "command:usss": "USSS", "command:cwmd": "CWMD",
                "command:uscis": "USCIS", "command:fletc": "FLETC"}
BY_ABBREVIATION = {a: nid for nid, a in ABBREVIATION.items()}
# Every requirement office one forecast row names is seeded: a small office with one record (CISA OCTO, the Coast Guard's
# Cyber Command at two) is often the one a question is about, and its rows otherwise load with no office to hold them.
MIN_ROWS = 1
CODE_RE = re.compile(r"\((70[A-Z][A-Z0-9]{3})\)")  # "MEO Contracting Division (70RCSJ)"
LISTING_FIELDS = ("fhorgid", "fhorgname", "fhorgtype", "status", "agencycode", "aacofficecode")
SAM_FIELDS = ("orgKey", "name", "type", "aacCode", "fpdsCode", "cgac", "fullParentPath", "fullParentPathName", "codeHierarchy")
CONTRACT = ("08_org_memory_format.md: observations are what a source states; relationships are dated claims resting on "
            "observations; interpretations are our readings; corrections are retractions")
SCOPE = ("Department of Homeland Security acquisition: the department, its components, their largest contracting offices "
         "and the requirement offices the DHS acquisition forecast names")
NOTES = "drafted by org_memory_dhs from SAM.gov organization records, the USAspending sub-agency answer and the APFS forecast; names as printed in the source"


def component_of(organization: str) -> str:
    """The component an APFS Organization value names: 'USCG/CG-SHORE' -> USCG, 'DHS HQ/CISA' -> CISA; a headquarters
    office that is no component ('DHS HQ/MGMT') falls to the department."""
    head, *rest = [x.strip() for x in organization.split("/")]
    return BY_ABBREVIATION.get(rest[0] if head == "DHS HQ" and rest else head, AGENCY_ID)


def latest_parent(entry: dict) -> dict:
    """The newest parent history row of a hierarchy listing entry: its current place and code path."""
    return max(entry.get("fhorgparenthistory") or [{}], key=lambda h: h.get("effectivedate") or "")


def listing_record(entry: dict) -> tuple[dict, str]:
    """(the fields quoted, the parent path) of a hierarchy listing entry."""
    parent = latest_parent(entry)
    quoted = {k: entry[k] for k in LISTING_FIELDS if entry.get(k) is not None}
    quoted |= {k: parent[k] for k in ("fhfullparentpathid", "fhfullparentpathname", "codehierarchy") if parent.get(k)}
    return quoted, str(parent.get("fhfullparentpathid") or "")


def forecast_pairs(records: list[dict]) -> tuple[Counter, Counter, Counter]:
    """(Organization -> rows, (Organization, Requirements Office) -> rows, (Organization, Contracting Office) -> rows)."""
    orgs, offices, buyers = Counter(), Counter(), Counter()
    for r in records:
        org = squash(r.get("organization") or "")
        orgs[org] += 1
        if squash(r.get("requirements_office") or ""):
            offices[(org, squash(r["requirements_office"]))] += 1
        if CODE_RE.search(r.get("contracting_office") or ""):
            buyers[(org, squash(r["contracting_office"]))] += 1
    return orgs, offices, buyers


def build_seed(manifest: list[dict]) -> dict:
    obs, rels, nodes = [], [], []
    counter = {"obs": 0, "rel": 0}

    def add_obs(row, observed_at, kind, passage, subjects) -> str:
        counter["obs"] += 1
        oid = f"obs:dhs:{counter['obs']:03d}"
        obs.append(observation(oid, row, observed_at, kind, passage, subjects))
        return oid

    def add_rel(kind, src, dst, obs_ids, as_of, **kw) -> None:
        counter["rel"] += 1
        rel = relationship(f"rel:dhs:{counter['rel']:03d}", kind, src, dst, obs_ids, as_of, **kw)
        rel["generator"] = GENERATOR
        rels.append(rel)

    def add_node(*args, **kw) -> dict:
        n = node(*args, **kw)
        n["generator"] = GENERATOR
        n["notes"] = kw.get("notes", NOTES)
        nodes.append(n)
        return n

    wanted = {"department": SAM_ORG.format(DEPARTMENT), "usaspending": USASPENDING, "apfs": APFS,
              **{f"hierarchy {k}": HIERARCHY.format(k) for k in (DEPARTMENT, *LISTED)}}
    saved = {k: by_url(manifest, u) for k, u in wanted.items()}
    listed = {}  # SAM.gov id -> (row, entry): the first listing that carries the organization
    for k in (DEPARTMENT, *LISTED):
        row = saved[f"hierarchy {k}"]
        for entry in json.loads((ROOT / row["path"]).read_text(encoding="utf-8")).get("orglist", []) if row else []:
            listed.setdefault(str(entry["fhorgid"]), (row, entry))
    records = {org: by_url(manifest, SAM_ORG.format(org)) for org in OFFICES if org not in listed}
    missing = [k for k, v in saved.items() if v is None] + [f"sam {k}" for k, v in records.items() if v is None]
    if missing:
        raise SystemExit(f"documents not saved yet: {', '.join(missing)}; run `collect` first")
    day = lambda row: row["retrieved_at"][:10]  # noqa: E731

    # The department, on its own record.
    row = saved["department"]
    rec = sam_record(row)
    oid = add_obs(row, day(row), "naming", json.dumps({k: rec[k] for k in (*SAM_FIELDS, "agencyName", "shortName") if rec.get(k) is not None},
                                                       separators=(",", ":"), ensure_ascii=False), [AGENCY_ID])
    aliases = [{"text": str(rec.get(k) or ""), "observation_ids": [oid]} for k in ("agencyName", "shortName")]
    add_node(AGENCY_ID, "agency", squash(str(rec.get("name") or "")), [oid], unique_aliases(aliases),
             {"sam_organization_id": DEPARTMENT, "fpds_agency_id": str(rec.get("fpdsCode") or P["agency"]["subtier_code"]),
              "toptier_code": str(rec.get("cgac") or P["agency"]["toptier_code"])})
    have = {AGENCY_ID: nodes[-1]}

    # The components and the contracting offices, each on its listing entry (or its record), under the parent it prints.
    for org, nid in (*COMPONENTS.items(), *OFFICES.items()):
        if org in listed:
            row, entry = listed[org]
            quoted, path = listing_record(entry)
            name, code, agency_code = entry["fhorgname"], entry.get("aacofficecode"), entry.get("agencycode")
        else:
            row = records[org]
            rec = sam_record(row)
            quoted, path = {k: rec[k] for k in SAM_FIELDS if rec.get(k) is not None}, str(rec.get("fullParentPath") or "")
            name, code, agency_code = rec.get("name"), rec.get("aacCode"), rec.get("fpdsCode")
        oid = add_obs(row, day(row), "naming", json.dumps(quoted, separators=(",", ":"), ensure_ascii=False), [nid])
        if nid in COMPONENTS.values():
            codes = {"sam_organization_id": org, "fpds_agency_id": str(agency_code), "office_code": ABBREVIATION[nid]}
            have[nid] = add_node(nid, "command", squash(str(name)), [oid], [], codes)
        else:
            have[nid] = add_node(nid, "contracting_office", squash(str(name)), [oid], [{"text": code, "observation_ids": [oid]}],
                                 {"sam_organization_id": org, "uic": code})
        parent = sam_parent({"fullParentPath": path}, {DEPARTMENT: AGENCY_ID, **COMPONENTS})
        if parent:
            add_rel("child_of", nid, parent, [oid], day(row), dates_status="unknown",
                    dates_note="the record states the organization's place in the hierarchy, not since when",
                    status_note=f"stated by the SAM.gov organization record of the latest cited retrieval ({quoted.get('fhfullparentpathname') or quoted.get('fullParentPathName')}); "
                                "not re-verified since")

    # USAspending: the component's abbreviation and each seeded office's FY2026 obligations, as the answer states them.
    row = saved["usaspending"]
    by_code = {n["codes"]["uic"]: n for n in have.values() if (n.get("codes") or {}).get("uic")}
    for sub in json.loads((ROOT / row["path"]).read_text(encoding="utf-8")).get("results", []):
        comp = next((have[nid] for nid in COMPONENTS.values() if sub["name"].casefold().endswith(have[nid]["name"].casefold())), None)
        if comp:
            quoted = {k: sub[k] for k in ("name", "abbreviation", "total_obligations", "transaction_count", "new_award_count") if k in sub}
            oid = add_obs(row, day(row), "naming", json.dumps(quoted, separators=(",", ":"), ensure_ascii=False), [comp["id"]])
            comp["observation_ids"].append(oid)
            if sub.get("abbreviation"):
                comp["aliases"] = unique_aliases(comp["aliases"] + [{"text": sub["abbreviation"], "observation_ids": [oid]}])
        for child in sub.get("children", []):
            office = by_code.get(child.get("code"))
            if office:
                oid = add_obs(row, day(row), "existence", f"sub-agency '{sub['name']}': " + json.dumps(child, separators=(",", ":"), ensure_ascii=False),
                              [office["id"]])
                office["observation_ids"].append(oid)

    # The forecast: the Organization values under each component, the requirement offices, and the headquarters divisions
    # a row's Contracting Office column names by code.
    row = saved["apfs"]
    records_ = json.loads((ROOT / row["path"]).read_text(encoding="utf-8"))
    orgs, offices, buyers = forecast_pairs(records_)
    for nid in COMPONENTS.values():
        named = sorted((org, n) for org, n in orgs.items() if component_of(org) == nid)
        if named:
            oid = add_obs(row, day(row), "naming", "column 'organization': " + ", ".join(f"'{o}' ({n} rows)" for o, n in named), [nid])
            have[nid]["observation_ids"].append(oid)
            have[nid]["aliases"] = unique_aliases(have[nid]["aliases"] + [{"text": ABBREVIATION[nid], "observation_ids": [oid]}])
    for (org, office), n in sorted(offices.items()):
        if n < MIN_ROWS:
            continue
        nid, parent = f"program:{slug(org)}--{slug(office)}", component_of(org)
        oid = add_obs(row, day(row), "existence", f"column 'organization': '{org}'; column 'requirements_office': '{office}' ({n} rows)", [nid, parent])
        add_node(nid, "program_office", f"{org} {office}", [oid], [], {"office_code": f"{org} - {office}"})
        add_rel("child_of", nid, parent, [oid], day(row), dates_status="unknown",
                dates_note="the forecast's rows state the office under the organization, not since when",
                status_note="stated by the forecast rows of the latest cited retrieval; not re-verified since")
    edges: dict[tuple[str, str], list[str]] = {}
    for (org, buyer), n in sorted(buyers.items()):
        office = by_code.get(CODE_RE.search(buyer)[1])
        target = component_of(org)
        if office is None or target == AGENCY_ID:
            continue
        oid = add_obs(row, day(row), "existence", f"column 'organization': '{org}'; column 'contracting_office': '{buyer}' ({n} rows)", [office["id"], target])
        edges.setdefault((office["id"], target), []).append(oid)
    for (src, dst), obs_ids in sorted(edges.items()):
        add_rel("contracts_for", src, dst, obs_ids, day(row), role=f"contracting office of {ABBREVIATION[dst]} requirements", dates_status="unknown",
                status_note="stated by the forecast rows of the latest cited retrieval, whose Contracting Office column prints the office code; not re-verified since")

    return {"generated": max(day(r) for r in (*saved.values(), *records.values())), "contract": CONTRACT, "scope": SCOPE,
            "nodes": nodes, "observations": obs, "relationships": rels, "interpretations": []}


def build(check: bool = False) -> int:
    if KEY != "dhs":
        print(f"this reader builds the dhs memory; the profile is {KEY} (set AGENCY=dhs)", file=sys.stderr)
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
    """The SAM.gov records and listings, the USAspending answer and the APFS records the build reads, not yet saved."""
    if KEY != "dhs":
        print(f"this reader collects for the dhs memory; the profile is {KEY} (set AGENCY=dhs)", file=sys.stderr)
        return 2
    from fetch import collect_missing  # noqa: E402
    manifest = rows()
    listed = {str(e["fhorgid"]) for k in (DEPARTMENT, *LISTED) if (r := by_url(manifest, HIERARCHY.format(k)))
              for e in json.loads((ROOT / r["path"]).read_text(encoding="utf-8")).get("orglist", [])}
    wanted = [(SAM_ORG.format(DEPARTMENT), f"SAM.gov federal organization record{NOTE_TAG}: {DEPARTMENT} ({AGENCY_ID})")]
    wanted += [(HIERARCHY.format(k), f"SAM.gov federal hierarchy next level{NOTE_TAG}: {k} ({P['sam_org_nodes'].get(k, AGENCY_ID)})")
               for k in (DEPARTMENT, *LISTED)]
    wanted += [(SAM_ORG.format(o), f"SAM.gov federal organization record{NOTE_TAG}: {o} ({nid})") for o, nid in OFFICES.items() if o not in listed]
    wanted += [(USASPENDING, f"USAspending agency record{NOTE_TAG}: toptier 070 sub-agencies and offices FY2026")]
    missed = collect_missing(wanted)
    # The forecast serves only its current records: a pull a day is what keeps a withdrawn record's history (lrae_package
    # makes each day's pull a release).
    if (by_url(manifest, APFS) or {}).get("retrieved_at", "")[:10] < datetime.now(timezone.utc).date().isoformat():
        from fetch import MANIFEST, fetch  # noqa: E402
        row = fetch(APFS, "direct", None, f"DHS APFS public forecast records{NOTE_TAG}: the JSON the forecast page exports to CSV and Excel")
        with MANIFEST.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        print(row.get("status"), APFS)
        missed += not kept_page(row)
    return 1 if missed else 0


def selfcheck() -> int:
    assert component_of("USCG/CG-SHORE") == "command:uscg" and component_of("DHS HQ/CISA") == "command:cisa"
    assert component_of("DHS HQ/S&T") == "command:st" and component_of("DHS HQ/MGMT") == AGENCY_ID and component_of("CBP") == "command:cbp"
    # The CISA Contracting Activity entry of the OPO listing as SAM.gov answered it on 2026-09-26, trimmed.
    entry = {"fhorgid": 500182403, "fhorgname": "CISA CONTRACTING ACTIVITY", "fhorgtype": "OFFICE", "status": "ACTIVE", "agencycode": "7001",
             "aacofficecode": "70RCSJ", "createdby": "someone",
             "fhorgparenthistory": [{"fhfullparentpathid": "100011942.100013095.500182403", "effectivedate": "2022-03-18 14:53",
                                     "fhfullparentpathname": "HOMELAND SECURITY, DEPARTMENT OF.OFFICE OF PROCUREMENT OPERATIONS.CISA CONTRACTING ACTIVITY"}]}
    quoted, path = listing_record(entry)
    assert "createdby" not in quoted and quoted["aacofficecode"] == "70RCSJ" and path.endswith(".500182403")
    assert sam_parent({"fullParentPath": path}, {DEPARTMENT: AGENCY_ID, **COMPONENTS}) == "command:opo"
    assert sam_parent({"fullParentPath": "100011942.100012855"}, {DEPARTMENT: AGENCY_ID, **COMPONENTS}) == AGENCY_ID
    recs = [{"organization": "DHS HQ/CISA", "requirements_office": "Cybersecurity Division (CSD)", "contracting_office": "Cyber Contracting Division (70RCSJ)"},
            {"organization": "DHS HQ/CISA", "requirements_office": "Cybersecurity Division (CSD)", "contracting_office": "Cyber Contracting Division (70RCSJ)"},
            {"organization": "CBP", "requirements_office": "", "contracting_office": "Mission Support (MSCD)"}]
    orgs, offices, buyers = forecast_pairs(recs)
    assert orgs == {"DHS HQ/CISA": 2, "CBP": 1} and offices == {("DHS HQ/CISA", "Cybersecurity Division (CSD)"): 2}
    assert buyers == {("DHS HQ/CISA", "Cyber Contracting Division (70RCSJ)"): 2}, "only a Contracting Office that prints a code binds an office"
    print("org_memory_dhs selfcheck ok")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--selfcheck" in argv:
        sys.exit(selfcheck())
    if argv and argv[0] == "collect":
        sys.exit(collect())
    if argv and (argv[0] == "--check" or argv[:2] == ["build", "--check"]):
        sys.exit(build(check=True))
    if argv and argv[0] == "build":
        sys.exit(build())
    print(__doc__)
    sys.exit(2)

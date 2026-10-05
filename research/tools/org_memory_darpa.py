#!/usr/bin/env python3
"""Read DARPA's own pages into the organization memory (the `memory` stage of the darpa profile).

    AGENCY=darpa python research/tools/org_memory_darpa.py build     # research/agencies/darpa/memory/organization_seed.json
    AGENCY=darpa python research/tools/org_memory_darpa.py --check   # exit 1 when a build would change the file
    AGENCY=darpa python research/tools/org_memory_darpa.py collect   # fetch the pages the build reads, not yet saved
    python research/tools/org_memory_darpa.py --selfcheck

DARPA publishes no forecast, so its memory is read from what it does publish: the offices page (the Director's
Office, six technical offices, the support offices), each office's own page, the Contracts Management Office
page, the staff listing (`/json/staff`: role, office and start date per person), one FPDS page (the office and
agency codes as the feed prints them) and one SAM.gov search page (the organization id the notices carry).
Every node rests on a dated observation whose passage is the source's own words (the first sentence of a page
that names the office, a tag of the feed, a record of the listing), as `research/docs/08_org_memory_format.md`
asks. Relationships are `child_of` (offices page), `contracts_for` (the CMO's stated authority) and `leads`
(the listing's "Office Director" and "Director" roles, with the start date the listing states). A role is a
stated role: nothing here says who decides what.

Every record is `review_status: draft` and carries `generator: org_memory_darpa`; a rebuild is byte for byte the
same while the saved pages are.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import KEY, MANIFEST, MEMORY, ROOT, SAM_NOTICES  # noqa: E402
from org_memory_lrae import page_text, squash  # noqa: E402

SEED = MEMORY / "organization_seed.json"
GENERATOR = "org_memory_darpa"
SITE = "https://www.darpa.mil"
AGENCY_ID = "agency:darpa"
CONTRACTING_ID = "contracting:hr0011"

# (node id, type, name, abbreviation, page path under darpa.mil)
OFFICES = [
    ("office:diro", "department", "Director's Office", "DIRO", "/about/offices/diro"),
    ("office:bto", "technical_center", "Biological Technologies Office", "BTO", "/about/offices/bto"),
    ("office:dso", "technical_center", "Defense Sciences Office", "DSO", "/about/offices/dso"),
    ("office:ipto", "technical_center", "Information Processing Techniques Office", "IPTO", "/about/offices/ipto"),
    ("office:mxo", "technical_center", "Multi X Office", "MXO", "/about/offices/mxo"),
    ("office:sto", "technical_center", "Strategic Technology Office", "STO", "/about/offices/sto"),
    ("office:tto", "technical_center", "Tactical Technology Office", "TTO", "/about/offices/tto"),
    (CONTRACTING_ID, "contracting_office", "Contracts Management Office", "CMO", "/about/offices/contracts-management"),
    ("office:sbpo", "department", "Small Business Programs Office", "SBPO", "/about/offices/sbpo"),
    ("office:cso", "department", "Commercial Strategy Office", "CSO", "/about/offices/commercial-strategy"),
]
TECHNICAL = {o[0] for o in OFFICES if o[1] == "technical_center"}
# Offices the saved notices name that the offices page of the latest retrieval does not list: former or renamed
# offices whose solicitations and awards still carry the name. Each stands on the earliest saved notice naming it.
FORMER_OFFICES = [
    ("office:mto", "Microsystems Technology Office", "MTO"),
    ("office:i2o", "Information Innovation Office", "I2O"),
    ("office:aco", "Adaptive Capabilities Office", "ACO"),
    ("office:apo", "Aerospace Projects Office", "APO"),
]
# Contracting offices of other agencies that sign awards DARPA funds (FPDS funding agency 97AE); one node per office
# with at least this many base awards on the saved pages, on the FPDS tag that names it.
SIGNER_MIN_AWARDS = 50
OFFICE_BY_NAME = {o[2].lower(): o[0] for o in OFFICES} | {o[1].lower(): o[0] for o in FORMER_OFFICES}


def raw_sentence(raw: str, name: str) -> str:
    """The sentence of a saved notice's own bytes that names the office: a verbatim slice, bounded by the sentence
    end, a line break tag or the field's closing quote."""
    at = raw.find(name)
    if at < 0:
        return ""
    starts = [raw.rfind(s, 0, at) + len(s) for s in (". ", "<br/>", "<br />", '":"', "\\n")]
    start = max(s for s in starts if s >= 0) if any(s >= 0 for s in starts) else 0
    ends = [e for e in (raw.find(s, at + len(name)) for s in (". ", "<br", '"', "\\n")) if e >= 0]
    end = min(ends) + (1 if raw[min(ends):min(ends) + 1] == "." else 0) if ends else min(len(raw), at + 300)
    return raw[start:end].strip()


def notices_naming(name: str) -> list[tuple[str, Path, str]]:
    """(posted date, file, raw text) of every saved notice detail naming the office, earliest first."""
    out = []
    for path in sorted(SAM_NOTICES.glob("*.json")) if SAM_NOTICES.exists() else []:
        if path.name.startswith("search_"):
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if name not in raw:
            continue
        try:
            posted = (json.loads(raw).get("postedDate") or "")[:10]
        except (ValueError, AttributeError):
            continue
        out.append((posted, path, raw))
    return sorted(out, key=lambda t: (t[0], t[1].name))
PAGES = {"about": "/about", "offices": "/about/offices", "staff": "/json/staff"}
# The ledger note prefixes of the two government-wide pages the codes are read from.
FPDS_NOTE = "FPDS base awards signed by DARPA's contracting office HR0011"
SAM_NOTE = "SAM sweep organization 500035490 (DEF ADVANCED RESEARCH PROJECTS AGCY, HR0011) active=false"
LEAD_ROLES = ("Office Director", "Director")
CONTRACT = ("08_org_memory_format.md: observations are what a source states; relationships are dated claims resting on "
            "observations; interpretations are our readings; corrections are retractions")
SCOPE = "Defense Advanced Research Projects Agency: the agency, its technical and support offices, its contracting office and their stated leaders"
NOTES = "drafted by org_memory_darpa from darpa.mil pages, the staff listing, one FPDS page and one SAM.gov search page; names as printed in the source"


def rows() -> list[dict]:
    return [json.loads(l) for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]


def latest(manifest: list[dict], predicate) -> dict | None:
    hits = [r for r in manifest if kept_page(r) and (ROOT / r["path"]).exists() and predicate(r)]
    return hits[-1] if hits else None


def by_url(manifest: list[dict], url: str) -> dict | None:
    return latest(manifest, lambda r: r.get("url") == url or r.get("final_url") == url)


STARTERS = re.compile(r"\b(The|Our|Driven|At|In|Since|We|A|Today|Founded|Established|As)\s")


def first_sentence_naming(text: str, name: str, abbreviation: str = "") -> str:
    """The first sentence of a page's text that names the office as prose, as the page prints it. The anchor is the
    name followed by its abbreviation in brackets, else the name followed by a lowercase word (navigation repeats the
    name as a bare heading); the sentence starts at the last sentence-opening word before the anchor and ends at the
    next full stop."""
    anchors = []
    if abbreviation:
        anchors += [m.start() for m in re.finditer(re.escape(f"{name} ({abbreviation})"), text)]
    anchors += [m.start() for m in re.finditer(re.escape(name) + r"(?=[,)]?\s+[a-z])", text)]
    for start in anchors:
        window = text[max(0, start - 60):start]
        opener = None
        for m in STARTERS.finditer(window):
            opener = m.start()
        begin = max(0, start - 60) + opener if opener is not None else start
        end = sentence_end(text, start)
        sentence = text[begin:end].strip()
        if 40 <= len(sentence) <= 600:
            return sentence
    return ""


ABBREVIATION_STOP = re.compile(r"(?:\bU\.S|\bD\.C|\b[A-Z])$")


def sentence_end(text: str, start: int) -> int:
    """The index just past the full stop that ends the sentence containing `start`; a stop inside an abbreviation
    ("U.S. small businesses") is not an end."""
    pos = start
    while True:
        end = text.find(". ", pos)
        if end < 0:
            return len(text)
        if not ABBREVIATION_STOP.search(text[:end]):
            return end + 1
        pos = end + 1


def observation(oid: str, row: dict, observed_at: str, statement_type: str, passage: str, subjects: list[str]) -> dict:
    method = row.get("method", "direct")
    return {"id": oid, "source_url": row.get("fetched_from") or row["url"],
            "source_revision": f"{method} retrieval {row['retrieved_at'][:10]}; sha256 {row['sha256'][:12]}",
            "observed_at": observed_at, "statement_type": statement_type, "passage": passage, "subject_ids": subjects, "access": None}


def relationship(rid: str, kind: str, src: str, dst: str, obs_ids: list[str], as_of: str, role: str | None = None,
                 effective_from: str | None = None, status_note: str = "", dates_status: str = "unknown", dates_note: str | None = None) -> dict:
    return {"id": rid, "type": kind, "from": src, "to": dst, "role_as_written": role, "effective_from": effective_from, "effective_to": None,
            "effective_dates_status": dates_status, "effective_dates_note": dates_note, "scope_as_stated": None, "observation_ids": obs_ids,
            "evidence_class": "directly_documented",
            "current_status": {"state": "last_confirmed", "as_of": as_of, "note": status_note or "stated by the latest cited page; not re-verified since"},
            "review_status": "draft", "drafted_by": {"actor": "assistant", "on": as_of}, "reviewed_by": None, "reviewed_on": None,
            "retraction": None, "generator": GENERATOR}


def node(nid: str, kind: str, name: str, obs_ids: list[str], aliases: list[dict] | None = None, codes: dict | None = None, **extra) -> dict:
    out = {"id": nid, "type": kind, "name": name, "aliases": aliases or [], "observation_ids": obs_ids, "notes": NOTES,
           "review_status": "draft", "reviewed_by": None, "generator": GENERATOR}
    if codes:
        out["codes"] = codes
    out.update(extra)
    return out


def unique_aliases(aliases: list[dict]) -> list[dict]:
    """One alias per spelling, carrying every observation that states it."""
    out: dict[str, dict] = {}
    for a in aliases:
        if not a["text"]:
            continue
        slot = out.setdefault(a["text"], {"text": a["text"], "observation_ids": []})
        slot["observation_ids"] += [o for o in a["observation_ids"] if o not in slot["observation_ids"]]
    return list(out.values())


def slug(view_node: str) -> str:
    return "person:" + re.sub(r"[^a-z0-9]+", "-", (view_node or "").rsplit("/", 1)[-1].lower()).strip("-")


def staff_records(row: dict) -> list[dict]:
    """The listing's records once per person: the site repeats a record per research topic it files them under."""
    seen, out = set(), []
    for rec in json.loads((ROOT / row["path"]).read_text(encoding="utf-8")):
        key = rec.get("view_node") or rec.get("nid")
        if key in seen or rec.get("moderation_state") not in (None, "Published"):
            continue
        seen.add(key)
        out.append(rec)
    return out


def listing_passage(rec: dict, raw: str) -> str:
    """The record's own bytes from the listing, name through page path: the passage is a verbatim slice of the saved
    file, as every observation's passage is, not a re-serialization of it."""
    at = raw.find(f'"nid":"{rec.get("nid")}"')
    start = raw.find('"field_first_name"', at) if at >= 0 else -1
    end = raw.find(',"body"', start) if start >= 0 else -1
    if start < 0 or end < 0:
        keys = ("field_first_name", "field_last_name", "field_role", "field_taxonomy_office", "field_start_date__raw", "view_node")
        return json.dumps({k: str(rec.get(k) or "") for k in keys}, separators=(",", ":"), ensure_ascii=False)
    return raw[start:end]


def build_seed(manifest: list[dict]) -> dict:
    obs: list[dict] = []
    rels: list[dict] = []
    nodes: list[dict] = []
    interpretations: list[dict] = []
    counter = {"obs": 0, "rel": 0}

    def add_obs(row, observed_at, kind, passage, subjects) -> str:
        counter["obs"] += 1
        oid = f"obs:darpa:{counter['obs']:03d}"
        obs.append(observation(oid, row, observed_at, kind, passage, subjects))
        return oid

    def add_rel(kind, src, dst, obs_ids, as_of, **kw) -> None:
        counter["rel"] += 1
        rels.append(relationship(f"rel:darpa:{counter['rel']:03d}", kind, src, dst, obs_ids, as_of, **kw))

    about = by_url(manifest, SITE + PAGES["about"])
    offices_page = by_url(manifest, SITE + PAGES["offices"])
    staff = by_url(manifest, SITE + PAGES["staff"])
    fpds = latest(manifest, lambda r: (r.get("note") or "").find(FPDS_NOTE) >= 0)
    sam = latest(manifest, lambda r: (r.get("note") or "").find(SAM_NOTE) >= 0)
    missing = [k for k, v in (("about", about), ("offices", offices_page), ("staff", staff), ("fpds", fpds), ("sam", sam)) if v is None]
    if missing:
        raise SystemExit(f"pages not saved yet: {', '.join(missing)}; fetch them first (research/docs/19, appendix)")
    dates = [about["retrieved_at"][:10], offices_page["retrieved_at"][:10], staff["retrieved_at"][:10]]

    # The agency: its mission sentence, its FPDS agency code, its SAM.gov organization.
    about_text = page_text(ROOT / about["path"])
    m = re.search(r"The DARPA mission is [^.]*\.", about_text)
    mission = m.group(0) if m else squash(about_text[about_text.find("The DARPA mission"):][:160])
    agency_obs = [add_obs(about, about["retrieved_at"][:10], "existence", mission, [AGENCY_ID])]
    fpds_bytes = (ROOT / fpds["path"]).read_text(encoding="utf-8", errors="replace")
    agency_tag = re.search(r'<ns1:contractingOfficeAgencyID[^>]*>[^<]*</ns1:contractingOfficeAgencyID>', fpds_bytes)
    office_tag = re.search(r'<ns1:contractingOfficeID[^>]*>[^<]*</ns1:contractingOfficeID>', fpds_bytes)
    codes = {}
    aliases = [{"text": "DARPA", "observation_ids": list(agency_obs)}]
    if agency_tag:
        code = re.search(r">([^<]*)<", agency_tag.group(0)).group(1)
        name = re.search(r'name="([^"]*)"', agency_tag.group(0))
        oid = add_obs(fpds, fpds["retrieved_at"][:10], "naming", agency_tag.group(0), [AGENCY_ID])
        agency_obs.append(oid)
        codes["fpds_agency_id"] = code
        if name:
            aliases.append({"text": squash(name.group(1)), "observation_ids": [oid]})
    sam_data = json.loads((ROOT / sam["path"]).read_text(encoding="utf-8"))
    hierarchy = next((h for x in sam_data.get("_embedded", {}).get("results", []) for h in (x.get("organizationHierarchy") or [])
                      if str(h.get("organizationId")) == "300000412"), None)
    if hierarchy:
        oid = add_obs(sam, sam["retrieved_at"][:10], "naming", json.dumps(hierarchy, separators=(",", ":"), ensure_ascii=False), [AGENCY_ID])
        agency_obs.append(oid)
        codes["sam_organization_id"] = "300000412"
        aliases.append({"text": squash(hierarchy.get("name") or ""), "observation_ids": [oid]})
    nodes.append(node(AGENCY_ID, "agency", "Defense Advanced Research Projects Agency", agency_obs, unique_aliases(aliases), codes))

    # The offices: the offices page lists them, each page states what it is.
    offices_text = page_text(ROOT / offices_page["path"])
    m = re.search(r"Our six technical offices put DARPA's mission into action\..*?Tactical Technology Office", offices_text)
    tech_passage = m.group(0) if m else "Technical Offices"
    tech_obs = add_obs(offices_page, offices_page["retrieved_at"][:10], "listing", tech_passage, sorted(TECHNICAL))
    m = re.search(r"Support Offices These offices provide vital support[^.]*\.(?:[^.]*\.){0,2}", offices_text)
    support_passage = m.group(0) if m else "Support Offices"
    support_ids = [o[0] for o in OFFICES if o[1] != "technical_center" and o[0] != "office:diro"]
    support_obs = add_obs(offices_page, offices_page["retrieved_at"][:10], "listing", support_passage, support_ids)
    m = re.search(r"The DARPA Director's Office \(DIRO\) comprises[^.]*\.", offices_text)
    diro_obs = add_obs(offices_page, offices_page["retrieved_at"][:10], "existence", m.group(0) if m else "Director's Office", ["office:diro"])
    for nid, kind, name, abbr, path in OFFICES:
        row = by_url(manifest, SITE + path)
        if row is None:
            raise SystemExit(f"office page not saved: {path}")
        dates.append(row["retrieved_at"][:10])
        text = page_text(ROOT / row["path"])
        sentence = first_sentence_naming(text, name, abbr)
        assert sentence, f"no sentence names {name} on its page"
        own = add_obs(row, row["retrieved_at"][:10], "contracting_support" if nid == CONTRACTING_ID else "existence", sentence, [nid])
        ids = [own] + ([tech_obs] if nid in TECHNICAL else [diro_obs] if nid == "office:diro" else [support_obs])
        aliases = [{"text": abbr, "observation_ids": [own]}] if abbr and f"({abbr})" in sentence or abbr in text else []
        # A technical office's abbreviation is the code awards and notices write it by ("DARPA STO ALBATROSS").
        codes = {"office_code": abbr} if nid in TECHNICAL and aliases else None
        if nid == CONTRACTING_ID and office_tag:
            oid = add_obs(fpds, fpds["retrieved_at"][:10], "naming", office_tag.group(0), [nid])
            ids.append(oid)
            name_attr = re.search(r'name="([^"]*)"', office_tag.group(0))
            codes = {"uic": re.search(r">([^<]*)<", office_tag.group(0)).group(1)}
            if name_attr:
                aliases.append({"text": squash(name_attr.group(1)), "observation_ids": [oid]})
            office_org = next((h for x in sam_data.get("_embedded", {}).get("results", []) for h in (x.get("organizationHierarchy") or [])
                               if str(h.get("organizationId")) == "500035490"), None)
            if office_org:
                oid = add_obs(sam, sam["retrieved_at"][:10], "naming", json.dumps(office_org, separators=(",", ":"), ensure_ascii=False), [nid])
                ids.append(oid)
                codes["sam_organization_id"] = "500035490"
        nodes.append(node(nid, kind, name, ids, unique_aliases(aliases), codes))
        as_of = row["retrieved_at"][:10]
        add_rel("child_of", nid, AGENCY_ID, [tech_obs if nid in TECHNICAL else diro_obs if nid == "office:diro" else support_obs], as_of,
                status_note="listed among DARPA's offices by the offices page of the latest cited retrieval; not re-verified since")
        if nid == CONTRACTING_ID:
            add_rel("contracts_for", nid, AGENCY_ID, [own], as_of, role="has the authority to enter into and administer contracts, grants, "
                    "cooperative agreements, and other transactions on behalf of DARPA", dates_status="unknown")

    # The stated leaders: the listing's Office Director and Director records, with the start date it states.
    # Former or renamed offices the saved notices still name: a node each, on the earliest notice naming it, so a
    # requirement posted under that name resolves to an office rather than to the contracting office.
    path_rows = {r["path"]: r for r in manifest if kept_page(r) and r.get("sha256")}
    for nid, name, abbr in FORMER_OFFICES:
        hits = [(posted, path, raw) for posted, path, raw in notices_naming(name) if str(path.relative_to(ROOT)) in path_rows]
        if not hits:
            continue
        posted, path, raw = hits[0]
        row = path_rows[str(path.relative_to(ROOT))]
        passage = raw_sentence(raw, name)
        if not passage:
            continue
        oid = add_obs(row, posted or row["retrieved_at"][:10], "existence", passage, [nid])
        aliases = [{"text": abbr, "observation_ids": [oid]}] if abbr in passage else []
        nodes.append(node(nid, "technical_center", name, [oid], aliases, {"office_code": abbr} if aliases else None,
                          notes=f"named as a DARPA office by {len(hits)} saved notice(s) (earliest posted {posted}); not listed on the offices page "
                                f"retrieved 2026-09-24, so a former or renamed office; the notices under its name still carry it; " + NOTES))
        add_rel("child_of", nid, AGENCY_ID, [oid], row["retrieved_at"][:10], dates_status="unknown",
                dates_note="the notice names the office as DARPA's; no source here states when it was formed or closed",
                status_note="named by saved notices; absent from the offices page of the latest retrieval")

    # Contracting offices of other agencies that sign awards DARPA funds, on the FPDS tag naming each.
    signer_pages = [r for r in manifest if kept_page(r) and "FUNDING_AGENCY_ID:97AE" in (r.get("url") or "")
                    and (ROOT / r["path"]).exists()]
    signer_tags: dict[str, list] = {}
    for r in sorted(signer_pages, key=lambda r: (r["retrieved_at"], r["path"])):
        raw = (ROOT / r["path"]).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'<ns1:contractingOfficeID[^>]*>[^<]*</ns1:contractingOfficeID>', raw):
            code = re.search(r">([^<]*)<", m.group(0)).group(1).strip()
            signer_tags.setdefault(code, []).append((r, m.group(0)))
    for code, tags in sorted(signer_tags.items()):
        if code == "HR0011" or len(tags) < SIGNER_MIN_AWARDS:
            continue
        row, tag = tags[0]
        name_attr = re.search(r'name="([^"]*)"', tag)
        name = squash(name_attr.group(1)) if name_attr else code
        nid = f"contracting:{code.lower()}"
        oid = add_obs(row, row["retrieved_at"][:10], "naming", tag, [nid])
        nodes.append(node(nid, "contracting_office", name, [oid], [{"text": code, "observation_ids": [oid]}], {"uic": code},
                          notes=f"contracting office of another agency: signs base awards funded by DARPA ({len(tags)} on the saved FY2020 to FY2026 "
                                f"funding-agency pages); named as FPDS prints it; " + NOTES))
        add_rel("contracts_for", nid, AGENCY_ID, [oid], row["retrieved_at"][:10], role="contracting office of record for awards funded by DARPA (FPDS funding agency 97AE)",
                dates_status="unknown", dates_note="the FPDS pages state the signing office per award, not an arrangement's dates",
                status_note="signs DARPA-funded awards on the saved FPDS pages; not re-verified since")

    staff_raw = (ROOT / staff["path"]).read_text(encoding="utf-8") if staff else ""
    for rec in staff_records(staff):
        role = html.unescape(str(rec.get("field_role") or "")).strip()
        if role not in LEAD_ROLES:
            continue
        office_name = html.unescape(str(rec.get("field_taxonomy_office") or "")).strip()
        office_id = OFFICE_BY_NAME.get(office_name.lower())
        if not office_id:
            continue
        name = " ".join(html.unescape(str(rec.get(k) or "")).strip() for k in ("field_first_name", "field_last_name")).strip()
        pid = slug(rec.get("view_node") or name)
        oid = add_obs(staff, staff["retrieved_at"][:10], "leadership", listing_passage(rec, staff_raw), [pid, office_id])
        nodes.append(node(pid, "person", name, [oid], notes="public official role only; recorded with source and observation date; " + NOTES))
        start = str(rec.get("field_start_date__raw") or "")[:10] or None
        add_rel("leads", pid, office_id, [oid], staff["retrieved_at"][:10], role=role, effective_from=start,
                dates_status="documented" if start else "unknown",
                dates_note="the listing states the start date" if start else "the listing states no start date",
                status_note="listed with this role on the latest cited retrieval of the staff listing; not re-verified since")

    # A reading, not a statement: the address of the former Information Innovation Office answers with the IPTO page.
    i2o = by_url(manifest, SITE + "/about/offices/i2o")
    if i2o and (i2o.get("final_url") or "").endswith("/ipto"):
        interpretations.append({
            "id": "int:darpa:001",
            "statement": "The Information Processing Techniques Office (IPTO) stands where the Information Innovation Office (I2O) stood: the address "
                         "/about/offices/i2o answers with the IPTO page, and NAVWAR's June 2024 forecast still names 'DARPA Information Innovation Office (I2O)'.",
            "subject_ids": ["office:ipto"], "observation_ids": [o["id"] for o in obs if "office:ipto" in o["subject_ids"]],
            "confidence": "medium",
            "basis": f"redirect recorded in the ledger row for /about/offices/i2o on {i2o['retrieved_at'][:10]}; the forecast row in datapack/lrae_navwar_2024-06",
            "competing_readings": ["I2O was renamed IPTO", "I2O was dissolved and IPTO created with a different portfolio"],
            "what_would_resolve": "an official DARPA statement naming the change and its date",
            "review_status": "draft", "drafted_by": {"actor": "assistant", "on": i2o["retrieved_at"][:10]}, "reviewed_by": None, "reviewed_on": None})

    return {"generated": max(dates), "contract": CONTRACT, "scope": SCOPE, "nodes": nodes, "observations": obs,
            "relationships": rels, "interpretations": interpretations}


def dumps(seed: dict) -> str:
    return json.dumps(seed, indent=1, ensure_ascii=False) + "\n"


def build(check: bool = False) -> int:
    if KEY != "darpa":
        print(f"this reader builds the darpa memory; the profile is {KEY} (set AGENCY=darpa)", file=sys.stderr)
        return 2
    seed = build_seed(rows())
    text = dumps(seed)
    kinds = {}
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


def selfcheck() -> int:
    text = ("Breadcrumb Home About DARPA Offices BTO BTO Biological Technologies Office About the Biological Technologies Office Thrust areas "
            "Leadership Contact The Biological Technologies Office (BTO) leverages biological properties and processes to revolutionize our "
            "ability to protect the nation's warfighters. BTO harnesses advances.")
    s = first_sentence_naming(text, "Biological Technologies Office", "BTO")
    assert s.startswith("The Biological Technologies Office (BTO) leverages") and s.endswith("warfighters."), s
    t = "Programs Program Managers Podcasts Contact Driven by visionaries, the Tactical Technology Office (TTO) creates decisive advantages across all warfighting domains. TTO's portfolio"
    assert first_sentence_naming(t, "Tactical Technology Office", "TTO").startswith("Driven by visionaries, the Tactical Technology Office (TTO)")
    assert first_sentence_naming("nothing here", "Multi X Office") == ""
    assert slug("/about/people/whitney-mason1") == "person:whitney-mason1"
    u = "Contact Our Small Business Programs Office (SBPO) develops relationships with U.S. small businesses to help them. Next"
    assert first_sentence_naming(u, "Small Business Programs Office", "SBPO").endswith("to help them.")
    assert [a["text"] for a in unique_aliases([{"text": "X", "observation_ids": ["1"]}, {"text": "X", "observation_ids": ["2"]}])] == ["X"]
    recs = staff_records_from([{"view_node": "/a", "nid": "1", "moderation_state": "Published"}, {"view_node": "/a", "nid": "1"}, {"view_node": "/b", "nid": "2"}])
    assert [r["nid"] for r in recs] == ["1", "2"], "one record per person"
    rec = {"nid": "7", "field_first_name": "A", "field_last_name": "B", "field_role": "Office Director", "field_taxonomy_office": "Director&#039;s Office",
           "field_start_date__raw": "2025-05-20", "view_node": "/about/people/a-b", "body": ""}
    raw = "[" + json.dumps({"nid": "0", "field_first_name": "Z", "body": ""}, separators=(",", ":")) + "," + json.dumps(rec, separators=(",", ":")) + "]"
    passage = listing_passage(rec, raw)
    assert passage in raw and passage.startswith('"field_first_name":"A"') and '"field_taxonomy_office":"Director&#039;s Office"' in passage, passage
    assert listing_passage(rec, "") .startswith('{"field_first_name":"A"'), "a record the raw text does not hold falls back to its own fields"
    raw = '{"body":"Amendment 3 revises the date. <br/><br/>The Adaptive Capabilities Office (ACO), working with the services, develops architectures. Proposals are due","title":"X"}'
    assert raw_sentence(raw, "Adaptive Capabilities Office") == "The Adaptive Capabilities Office (ACO), working with the services, develops architectures."
    assert raw_sentence(raw, "Adaptive Capabilities Office") in raw and raw_sentence(raw, "Not There") == ""
    assert raw_sentence('{"title":"Information Innovation Office (I2O) Office-Wide","x":1}', "Information Innovation Office") == "Information Innovation Office (I2O) Office-Wide"
    rel = relationship("rel:x", "leads", "person:a", "office:tto", ["obs:1"], "2026-09-24", role="Office Director", effective_from="2024-12-05", dates_status="documented")
    assert rel["evidence_class"] == "directly_documented" and rel["effective_from"] == "2024-12-05" and rel["generator"] == GENERATOR
    print("org_memory_darpa selfcheck ok")
    return 0


def staff_records_from(records: list[dict]) -> list[dict]:
    seen, out = set(), []
    for rec in records:
        key = rec.get("view_node") or rec.get("nid")
        if key in seen or rec.get("moderation_state") not in (None, "Published"):
            continue
        seen.add(key)
        out.append(rec)
    return out


def collect() -> int:
    """The pages the build reads (the about, offices and staff pages, and each office's own page), not yet saved."""
    from fetch import collect_missing  # noqa: E402
    wanted = [(SITE + p, f"DARPA {name} page") for name, p in PAGES.items()]
    wanted += [(SITE + path, f"DARPA office page for {nid}") for nid, _, _, _, path in OFFICES if path]
    return 1 if collect_missing(wanted + [(SITE + "/about/offices/i2o", "DARPA I2O page (the office IPTO replaced)")]) else 0


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

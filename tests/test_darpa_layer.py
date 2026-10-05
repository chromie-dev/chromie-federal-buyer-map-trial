"""The DARPA layer: the agency profile, the seed, the collected families and the artefacts the shared pipeline wrote
into research/agencies/darpa. Every test reads the files alone and skips while an artefact is not written yet; the rules
are the Navy layer's, applied to the second agency.

Run with AGENCY unset (the profile module is imported with an explicit key here, not through the environment).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "research" / "tools"
DARPA = ROOT / "research" / "agencies" / "darpa"
MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

sys.path.insert(0, str(TOOLS))
import agency  # noqa: E402

PROFILE = agency.PROFILES["darpa"]


def load(path: Path):
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} not written yet")
    return json.loads(path.read_text(encoding="utf-8"))


def manifest() -> list[dict]:
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ the profile

def test_profile_names_darpa_identifiers():
    assert PROFILE["fpds_offices"] == {"HR0011": "DARPA Contracts Management Office"} or "HR0011" in PROFILE["fpds_offices"]
    assert "97AE" in PROFILE["fpds_funding_agencies"], "awards other agencies sign for DARPA are found by the funding agency"
    assert "500035490" in PROFILE["sam_orgs"] and PROFILE["sam_orgs"]["500035490"] == "HR0011"
    assert PROFILE["sbir_component"] == "DARPA"
    assert PROFILE["forecast"]["pack_glob"] is None, "no acquisition forecast was found (research/docs/19, section 2)"
    assert re.search(PROFILE["congress_pattern"], "the Defense Advanced Research Projects Agency shall") and re.search(PROFILE["congress_pattern"], "DARPA")


def test_profile_runs_under_the_environment_key():
    out = subprocess.run([sys.executable, str(TOOLS / "agency.py"), "--selfcheck"], capture_output=True, text=True,
                         env={**os.environ, "AGENCY": "darpa"}, cwd=ROOT)
    assert out.returncode == 0, out.stdout + out.stderr


def test_note_tag_separates_the_layers():
    assert agency.PROFILES["navy"] is not PROFILE
    # The Navy owns unmarked notes; a note marked for another profile is not the Navy's.
    tagged, plain = "oversight watch [darpa]: oversight.gov 2026-05-15 DOWIG-2026-085", "oversight watch: oversight.gov 2026-05-15 DODIG-2025-158"
    if agency.KEY == "navy":
        assert agency.note_is_ours(plain) and not agency.note_is_ours(tagged)
    else:
        assert agency.note_is_ours(tagged) and not agency.note_is_ours(plain)


# ------------------------------------------------------------------ the seed

def test_seed_holds_every_office_the_profile_sweeps():
    seed = load(DARPA / "memory" / "organization_seed.json")
    ids = {n["id"] for n in seed["nodes"]}
    assert set(PROFILE["sam_org_nodes"].values()) <= ids, "every SAM.gov organization swept resolves to a seed node"
    assert "contracting:hr0011" in ids and "agency:darpa" in ids
    names = {n["name"] for n in seed["nodes"]} | {a["text"] for n in seed["nodes"] for a in n.get("aliases", [])}
    for office in PROFILE["pilot_offices"]:
        assert office in names, office
    for org in PROFILE["coverage_orgs"]:
        assert org in names, f"{org} in the coverage matrix is not a seed alias"


def test_seed_observations_are_verbatim_passages_on_saved_pages():
    seed = load(DARPA / "memory" / "organization_seed.json")
    saved = {r["sha256"][:12]: r for r in manifest() if r.get("status") == 200 and r.get("sha256") and r.get("path")}
    assert seed["observations"], "no observation"
    for obs in seed["observations"]:
        assert obs["passage"].strip(), obs["id"]
        assert DATE_RE.match(obs["observed_at"]), obs["id"]
        m = re.search(r"sha256 ([0-9a-f]{12})", obs["source_revision"] or "")
        assert m and m.group(1) in saved, f"{obs['id']} cites a document the ledger does not hold"
        row = saved[m.group(1)]
        assert row["url"] == obs["source_url"], obs["id"]
        page = ROOT / row["path"]
        if page.exists():
            # A JSON or text source is quoted byte for byte; an HTML page is quoted from its visible text, read the
            # way the seed builder read it (org_memory_lrae.page_text, squashed whitespace).
            text = page.read_text(encoding="utf-8", errors="ignore")
            if obs["passage"] not in text:
                from org_memory_lrae import page_text, squash
                text = squash(page_text(page))
            assert obs["passage"] in text, f"{obs['id']} passage is not on the saved page verbatim"


def test_seed_persons_carry_a_stated_role_not_authority():
    seed = load(DARPA / "memory" / "organization_seed.json")
    leads = [r for r in seed["relationships"] if r["type"] == "leads"]
    assert leads, "the staff listing names the office directors"
    for rel in leads:
        assert rel.get("observation_ids"), rel["id"]
        assert rel.get("effective_dates_status") == "documented" and DATE_RE.match(rel.get("effective_from") or ""), rel["id"]
        assert rel.get("evidence_class") == "directly_documented", rel["id"]
    text = json.dumps(seed).lower()
    assert "decision-maker" not in text and "decision maker" not in text


# ------------------------------------------------------------------ the events

def test_every_event_family_the_profile_has_is_written():
    events = DARPA / "events"
    for name in ("budget_lines.json", "sbir_topics.json", "fedreg_events.json", "congress_events.json", "news_observations.json",
                 "protest_events.json", "oversight_events.json", "remarks_events.json"):
        load(events / name)


def test_budget_lines_are_r2_program_elements_with_their_book():
    payload = load(DARPA / "events" / "budget_lines.json")
    assert payload["provider"] == PROFILE["budget"]["provider"]
    assert payload["lines"], "no program element read"
    books = {b["path"]: b for b in payload["books"]}
    assert len(books) >= 2, "the FY2026 and FY2027 books"
    for line in payload["lines"]:
        assert line["exhibit"] == "R-2" and re.fullmatch(r"\d{7}[A-Z]?", line["li"]), "DARPA is funded through RDT&E alone"
        assert line["appropriation"] == "0400" and line["budget_activity"].startswith("BA "), line["li"]
        assert line["book"] in books and DATE_RE.match(line["published"])
        # A book's columns are its own President's Budget year's: FY2025 and FY2026 total in the PB 2026 book,
        # FY2026 and FY2027 total in the PB 2027 book (a fixed FY2027 layout shifted the older book a year).
        pb = int(books[line["book"]]["pb"])
        assert {f"fy{pb - 1}", f"fy{pb}_total"} <= set(line["amounts"]), (line["li"], pb, sorted(line["amounts"]))
        assert f"FY{pb}" in line["event_title"], line["event_title"]


def test_sbir_topics_are_the_darpa_component_only():
    payload = load(DARPA / "events" / "sbir_topics.json")
    topics = payload["rows"]
    assert topics and payload["topics"] == len(topics), "no DARPA topic"
    for t in topics:
        assert (t.get("component") or "").upper() == "DARPA", t.get("code") or t
        assert DATE_RE.match(t.get("pre_release") or t.get("open") or ""), t.get("code")


def test_unread_documents_are_recorded_not_dropped():
    """Without a model key the oversight and remarks readers record each document as unread with the reason,
    so the gap stands in the file instead of stopping the build."""
    for name in ("oversight_events.json", "remarks_events.json"):
        payload = load(DARPA / "events" / name)
        for doc in payload["documents"]:
            if doc.get("unread"):
                assert doc["events"] == [] and doc["cassette"] is None, doc["url"]
            else:
                assert doc["cassette"], doc["url"]
        assert payload.get("unread", 0) == sum(1 for d in payload["documents"] if d.get("unread"))


# ------------------------------------------------------------------ the people

def test_people_come_from_the_staff_listing_and_the_notices():
    payload = load(DARPA / "memory" / "people.json")
    people = payload["rows"]
    assert people and payload["people"] == len(people)
    sources = {pos["source"] for p in people for pos in p.get("positions", [])}
    assert "agency_staff_listing" in sources and "sam_gov_site_api" in sources
    for p in people:
        for pos in p.get("positions", []):
            assert DATE_RE.match(pos["observed_at"]), p.get("name")
            assert pos["role_type"] != "decision_maker", "a role is a stated role, not authority"
            if pos["source"] == "agency_staff_listing":
                assert pos["source_url"].startswith("https://www.darpa.mil/"), p.get("name")
                assert "start date" in pos["context"] or pos["context"], p.get("name")


# ------------------------------------------------------------------ the sources

def test_registry_covers_every_provider_the_layer_emits():
    reg = {e["source_key"] for e in load(DARPA / "sources" / "source_registry.json")}
    needed = {"sam_gov_site_api", "fpds_atom_feed", "sbir_sttr_topics", "federal_register", "govinfo_api", "oversight_gov_reports",
              "gao_reports", "gao_bid_protests", PROFILE["budget"]["provider"], *PROFILE["remarks"]["providers"].values(), "darpa_site", "darpa_staff_listing"}
    assert needed <= reg, needed - reg


def test_coverage_matrix_rows_are_the_profile_orgs():
    matrix = load(DARPA / "sources" / "coverage_matrix.json")
    assert matrix["organizations"] == PROFILE["coverage_orgs"]
    assert all(c["reason"] == "no_public_source" for c in matrix["cells"] if c["family"] == "forecast"), "no forecast was found for DARPA"
    assert all(c.get("sources") for c in matrix["cells"] if c["family"] in ("notice", "incumbent", "organization"))


def test_coverage_status_lists_no_navy_host_as_unregistered():
    status = load(DARPA / "sources" / "source_status.json")
    assert not any(h.endswith("navy.mil") for h in status["unregistered_hosts"] if h not in ("www.niwcatlantic.navy.mil", "www.niwcpacific.navy.mil")), \
        "a host the Navy registry owns is the Navy layer's document, not an unregistered host of this one"


# ------------------------------------------------------------------ the ledger

def test_darpa_collection_rows_are_tagged_for_the_profile():
    rows = [r for r in manifest() if r.get("retrieved_at", "") >= "2026-09-24" and "darpa" in (r.get("note", "") + r.get("url", "")).lower()]
    watch = [r for r in rows if re.match(r"^(oversight|remarks|news) (watch|sweep|search|feed)", r.get("note", ""))]
    assert watch, "no DARPA watch row"
    for r in watch:
        assert " [darpa]:" in r["note"], r["note"]


# ------------------------------------------------------------------ the read-back layers (research/docs/19, section 9)

RESULTS = DARPA / "results"


def test_reading_patterns_are_darpas():
    reading = PROFILE["reading"]
    assert re.search(PROFILE["piid_re"], "awarded under HR001125C0043 and HR0011-24-9-0001"), "a Contracts Management Office contract or OT number"
    assert re.search(PROFILE["piid_re"], "N0003925F4027"), "an award another agency's office signs for DARPA carries that office's number"
    assert re.findall(reading["office_code_re"], "the BTO and TTO programs, not the PMW 160 ones") == ["BTO", "TTO"]
    assert not re.search(reading["hull_re"], "USS Example (DDG 1000)"), "DARPA titles carry no hull designator to strip"
    assert "darpa" in PROFILE["generic_words"] and "navy" not in PROFILE["generic_words"]
    assert PROFILE["short"] == "DARPA"
    for key, profile in agency.PROFILES.items():
        assert set(profile["reading"]) == set(reading), f"{key}: every profile answers the same reading questions"


def test_corpus_is_frozen_from_the_darpa_proof_database():
    corpus = load(RESULTS / "corpus.json")
    assert corpus["db"] == PROFILE["database"]
    assert corpus["orgs"] and corpus["events"] and corpus["needs"]
    assert not any(o["org_type"].endswith("\n") for o in corpus["orgs"].values()), "a field never carries psql's final newline"
    names = {o["acronym"] or o["name"] for o in corpus["orgs"].values()}
    assert {"DSO", "TTO", "HR0011"} <= names
    assert not any(e["date"] > corpus["frozen_at"][:10] for e in corpus["events"]), "no statement is dated after the freeze"
    assert all(e["family"] in ("notice", "incumbent", "programs", "other", "congress", "organization", "forecast", "news", "budget", "leaders", "oversight", "conference", "protest",
                               "grant", "assistance", "hiring")
               for e in corpus["events"])


def test_unread_outcomes_name_no_cell_but_keep_their_reason():
    labels = load(RESULTS / "outcome_labels.json")
    corpus = load(RESULTS / "corpus.json")
    assert labels["outcomes"] == len(corpus["outcomes"]) == len(labels["labels"])
    for row in labels["labels"]:
        if row.get("unread"):
            assert row["aliases"] == [] and row["program_office"] == "" and row["cassette"] is None, row
            assert "OPENAI_API_KEY" in row["unread"] or "cassette" in row["unread"], row["unread"]
        else:
            assert row["cassette"], "a reading names its cassette"


def test_office_reads_cover_every_contracting_office_notice_and_say_when_unread():
    reads = load(RESULTS / "office_reads.json")
    corpus = load(RESULTS / "corpus.json")
    contracting = {oid for oid, o in corpus["orgs"].items() if o["org_type"] == "contracting_office"}
    filed = [e for e in corpus["events"] if e["family"] == "notice" and e["org"] in contracting]
    assert set(reads["notices"]) <= {e["id"] for e in filed}, "only notices filed at a contracting office are read"
    # The notices the model reads are the ones the record itself places nowhere (office_wiki.unread over pages.read_offices,
    # under the DARPA profile: a title's BTO or TTO, a shared program name or a solicitation number places a notice).
    env = {**os.environ, "AGENCY": "darpa"}
    code = ("import json, sys; sys.path.insert(0, 'research/tools'); import office_wiki, pages\n"
            "c = json.load(open(sys.argv[1])); read = pages.read_offices(c, pages.Layer(c, [], routes=[]).org_id)\n"
            "print(json.dumps(sorted(e['id'] for e in office_wiki.unread(c, 'notices', read))))")
    ran = subprocess.run([sys.executable, "-c", code, str(RESULTS / "corpus.json")], capture_output=True, text=True, env=env, cwd=ROOT)
    assert ran.returncode == 0, ran.stderr[-800:]
    assert set(reads["notices"]) == set(json.loads(ran.stdout)), "the saved reads are exactly the notices the record places nowhere"
    assert len(reads["notices"]) > 500, "DARPA's contracting office files most notices without naming the technical office"
    for nid, a in reads["notices"].items():
        if a.get("unread"):
            assert a["office"] == "" and a["problems"] == [], "an unread notice places nothing"
        else:
            assert a["cassette"] and (a["office"] == "" or a["notice_words"]), nid


def test_pulse_ends_on_the_freeze_day_and_scores_darpa_cells():
    pulse = load(RESULTS / "pulse.json")
    corpus = load(RESULTS / "corpus.json")
    assert pulse["as_of"] <= corpus["frozen_at"][:10], "the pulse never speaks as of a day after the freeze"
    assert pulse["cells"] > 0 and pulse["ranking"], "the technical offices' requirements are cells"
    offices = {c["office"] for c in pulse["ranking"]}
    # The technical offices (the six current ones and the former ones the notices still name), the Contracts Management Office
    # and the agency itself; never a Navy office.
    owners = {o["name"] for o in corpus["orgs"].values() if o["org_type"] in ("technical_office", "contracting_office", "agency")}
    owners |= {o["acronym"] for o in corpus["orgs"].values() if o["org_type"] in ("technical_office", "contracting_office", "agency") and o.get("acronym")}
    assert offices <= owners, offices - owners
    assert offices & set(PROFILE["pilot_offices"]), "the current technical offices are among the ranked"
    for action in pulse["actions"]:
        assert action["type"] in ("meet_office", "watch", "ask", "prepare", "team", "position", "respond") or action["type"], action["type"]


def test_buying_dna_covers_the_offices_that_sign_for_darpa():
    dna = load(RESULTS / "buying_dna.json")
    offices = list(dna["contracting_offices"])
    assert offices[0] == "HR0011", "the Contracts Management Office first"
    assert len(offices) > 1, "the offices of other agencies that sign DARPA-funded awards follow"
    assert dna["base_awards_on_the_pages"] >= 3000


def test_small_business_office_is_the_agencys_from_the_department_directory():
    rows = load(DARPA / "memory" / "small_business_offices.json")
    assert [r["office_id"] for r in rows] == ["agency:darpa"]
    row = rows[0]
    assert row["source_url"].startswith("https://business.defense.gov/") and row["page_saved"]
    assert "webmaster" not in " ".join(row["lines"]).lower()
    saved = [r for r in manifest() if r.get("url") == row["url"] or r.get("final_url") == row["url"]]
    assert saved and saved[-1]["status"] == 200


def test_pages_and_questions_answer_from_the_darpa_corpus():
    if not (RESULTS / "corpus.json").exists():
        pytest.skip("corpus not frozen yet")
    env = {**os.environ, "AGENCY": "darpa"}
    page = subprocess.run([sys.executable, str(TOOLS / "pages.py"), "office", "Defense Sciences Office"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert page.returncode == 0, page.stderr[-800:]
    assert "sources speaking about this object" in page.stdout and "requirements owned (created by the notices)" in page.stdout, page.stdout[:400]
    asked = subprocess.run([sys.executable, str(TOOLS / "ask.py"), "changed", "Defense Sciences Office", "--days", "365"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert asked.returncode == 0, asked.stderr[-800:]
    assert asked.stdout.startswith("What changed inside DSO"), asked.stdout[:200]


# ------------------------------------------------------------------ the second sync (2026-09-25): kinds, reads of every kind, the read surface

def test_special_notice_kinds_are_read_per_profile_or_stand_unread():
    kinds = load(RESULTS / "notice_kinds.json")
    assert kinds, "every DARPA special notice has a row"
    for nid, a in kinds.items():
        if a.get("unread"):
            assert a["kind"] == "" and a["cassette"] is None, nid
        else:
            assert a["cassette"] and (a["kind"] == "" or a["words"]), nid
    navy = ROOT / "research" / "results" / "notice_kinds.json"
    if navy.exists():
        assert not set(kinds) & set(json.loads(navy.read_text(encoding="utf-8"))), "a DARPA notice is never in the Navy's file"


def test_office_reads_cover_every_kind_the_reader_knows():
    reads = load(RESULTS / "office_reads.json")
    assert {"notices", "awards", "topics", "directives"} <= set(reads), sorted(reads)
    corpus = load(RESULTS / "corpus.json")
    topics = [e for e in corpus["events"] if e["family"] == "programs" and corpus["orgs"].get(e["org"], {}).get("org_type") not in ("technical_office", "program_office")]
    assert set(reads["topics"]) >= {e["id"] for e in topics}, "every topic no office owns is read or stands unread"


def test_the_read_surface_answers_from_the_darpa_record():
    if not (RESULTS / "corpus.json").exists():
        pytest.skip("corpus not frozen yet")
    env = {**os.environ, "AGENCY": "darpa"}
    run = lambda *args: subprocess.run([sys.executable, str(TOOLS / "navy.py"), *args], capture_output=True, text=True, env=env, cwd=ROOT)
    helped = run("help")
    assert helped.returncode == 0 and "DARPA record as of" in helped.stdout, helped.stdout[:300]
    office = run("office", "Defense Sciences Office")
    assert office.returncode == 0, office.stderr[-600:]
    got = json.loads(office.stdout)
    assert got["office"] == "DSO" and got.get("beside"), sorted(got)
    assert "buying book" not in got["beside"] or "HR0011" in got["beside"]["buying book"] or got["beside"]["buying book"]
    near = run("neighbors", "Defense Sciences Office")
    assert near.returncode == 0 and json.loads(near.stdout).get("parent") == "Defense Advanced Research Projects Agency", near.stdout[:300]
    found = run("search", "quantum")
    assert found.returncode == 0 and found.stdout.strip(), found.stderr[-300:]


# ------------------------------------------------------------------ the contact routes (tasks/T02 shape, generated)

CONTACT_ROLE_TYPES = {"requirement_owner", "program_manager", "executive", "contracting_poc", "technical_support_office", "industry_intake_channel", "office_channel"}
CONFIDENCES = {"high", "medium", "low"}


def test_contact_observations_are_generated_from_the_listing_and_the_notices():
    rows = load(DARPA / "memory" / "contact_observations.json")
    seed_ids = {n["id"] for n in load(DARPA / "memory" / "organization_seed.json")["nodes"]}
    ids = [r["id"] for r in rows]
    assert rows and len(ids) == len(set(ids)), "duplicate observation id"
    for r in rows:
        assert r["generator"] == "contact_routes", f"{r['id']}: a DARPA contact row is generated, never hand-written"
        assert r["kind"] in {"person", "channel"} and (r.get("name") or r.get("channel_as_written")), r["id"]
        assert r["role_as_written"] and r["role_type"] in CONTACT_ROLE_TYPES, r["id"]
        assert r["office_id_as_resolved"] in seed_ids, f"{r['id']} resolves to an office the seed does not hold"
        assert r["source_url"] and r["source_revision"] and DATE_RE.match(r["observed_at"]), r["id"]
        assert r["locators"] and r["passage"], f"{r['id']} lacks a locator or passage"
        assert r["source_statement_confidence"] in CONFIDENCES and r["independent_sources"] == 1, r["id"]
        if r["role_type"] == "contracting_poc":
            assert r["member_of_office"] is False, f"{r['id']}: a point of contact answers for the solicitation, not the office"
            assert all(l.get("notice_id") and l.get("field") == "pointOfContact" for l in r["locators"]), r["id"]
            assert r["source_url"].startswith("https://sam.gov/opp/"), r["id"]
        else:
            assert r["source_url"] == PROFILE["people"]["staff_listing"] and r["locators"][0].get("nid"), r["id"]
            assert r["member_of_office"] is True and r["role_type"] in {"executive", "program_manager"}, r["id"]
    sources = {r["role_type"] for r in rows}
    assert {"executive", "program_manager", "contracting_poc"} <= sources


def test_contact_recommendations_reach_both_sides_of_every_listed_office():
    obs = {r["id"]: r for r in load(DARPA / "memory" / "contact_observations.json")}
    recs = load(DARPA / "memory" / "contact_recommendations.json")
    routes = {}
    for rec in recs:
        assert rec["generator"] == "contact_routes" and rec["office_id"] and rec["recommendation"], rec["id"]
        assert rec["contact_observation_ids"] and all(x in obs for x in rec["contact_observation_ids"]), rec["id"]
        assert all(obs[x]["role_type"] == rec["route_type"] for x in rec["contact_observation_ids"]), rec["id"]
        assert rec["source_confidence"] in CONFIDENCES, rec["id"]
        assert rec["currency_confidence"] in CONFIDENCES | {"unknown"} and rec["currency_basis"], rec["id"]
        for key in ("competing_candidates", "contradictions", "caveats"):
            assert isinstance(rec[key], list), rec["id"]
        assert rec["caveats"], f"{rec['id']}: a route is a recommendation to try, and says so"
        assert rec["review_status"] == "draft" and rec["reviewed_by"] is None, f"{rec['id']}: nobody has reviewed a generated route"
        routes.setdefault(rec["office_id"], set()).add(rec["route_type"])
    listed = {o for o, kinds in routes.items() if "program_manager" in kinds}
    assert len(listed) >= 6, "the staff listing names program managers under each current technical office"
    for office in listed:
        assert {"executive", "program_manager", "contracting_poc"} <= routes[office], f"{office}: requirement side and acquisition side both"


def test_review_log_checks_every_generated_observation():
    log = load(DARPA / "memory" / "review_log.json")
    obs = {r["id"] for r in load(DARPA / "memory" / "contact_observations.json")}
    checked = set()
    for e in log:
        assert e["generator"] == "contact_routes" and e["target"] and e["check"], e["id"]
        assert e["outcome"] in {"confirmed", "not_found"} and e["checked_against"]["source_url"], e["id"]
        assert e["checked_by"]["actor"] and DATE_RE.match(e["checked_by"]["on"]) and e["reviewed_by"] is None, e["id"]
        if e["target_kind"] == "contact_observation":
            checked.add(e["target"])
    assert checked == obs, "every contact observation has a check entry"


def test_contact_routes_rebuild_byte_for_byte_and_leave_the_navys_files_alone():
    out = subprocess.run([sys.executable, str(TOOLS / "contact_routes.py"), "build", "--check"], capture_output=True, text=True,
                         env={**os.environ, "AGENCY": "darpa"}, cwd=ROOT)
    assert out.returncode == 0, out.stdout + out.stderr
    navy = ROOT / "research" / "memory"
    before = {p.name: p.read_bytes() for p in (navy / "contact_observations.json", navy / "contact_recommendations.json", navy / "review_log.json")}
    out = subprocess.run([sys.executable, str(TOOLS / "contact_routes.py"), "build"], capture_output=True, text=True,
                         env={**os.environ, "AGENCY": "navy"}, cwd=ROOT)
    assert out.returncode == 0 and "skipped" in out.stdout, out.stdout + out.stderr
    assert before == {p.name: p.read_bytes() for p in (navy / "contact_observations.json", navy / "contact_recommendations.json", navy / "review_log.json")}


def test_people_do_not_count_a_generated_observation_twice():
    payload = load(DARPA / "memory" / "people.json")
    sources = {pos["source"] for p in payload["rows"] for pos in p.get("positions", [])}
    assert "contact_observations" not in sources, "the staff listing and the notices are read at first hand; the generated rows are not a second source"


# ------------------------------------------------------------------ the attribution examples (research/docs/04, per agency)

EVIDENCE_CLASSES = {"directly_documented", "inferred", "ambiguous", "unresolved"}


def _saved_text(url: str) -> str:
    """The saved bytes of the newest 200 row for the URL (a SAM.gov view URL maps to its saved notice detail), as text."""
    import html
    m = re.match(r"https://sam\.gov/opp/([0-9a-f]{32})/view$", url)
    if m:
        path = ROOT / "data" / "raw" / PROFILE["sam_dir"] / f"{m.group(1)}.json"
        assert path.exists(), f"no saved notice detail for {url}"
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        rows = [r for r in manifest() if r.get("url") == url and r.get("status") == 200 and r.get("path")]
        assert rows, f"no fetched manifest row for {url}"
        text = (ROOT / rows[-1]["path"]).read_text(encoding="utf-8", errors="replace")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()).upper()


def test_attribution_examples_quote_the_saved_records_and_name_their_rule():
    examples = load(DARPA / "memory" / "attribution_examples.json")
    seed_ids = {n["id"] for n in load(DARPA / "memory" / "organization_seed.json")["nodes"]}
    ids = [ex["id"] for ex in examples]
    assert len(ids) == len(set(ids)) and len(examples) >= 10, "duplicate example id or too few examples"
    classes = {ex["evidence_class"] for ex in examples}
    assert "directly_documented" in classes and "unresolved" in classes, "the examples show both a placement and an explicit unknown"
    for ex in examples:
        assert ex.get("identifier") and ex["evidence_class"] in EVIDENCE_CLASSES and ex.get("placement_rule"), ex["id"]
        assert "contracting_office" in ex and "funding_organization" in ex and isinstance(ex["program_offices"], list), ex["id"]
        assert set(ex["program_offices"]) <= seed_ids, f"{ex['id']} names an office the seed does not hold"
        assert ex["evidence"] and all(e.get("source_url") and DATE_RE.match(e.get("observed_at", "")) for e in ex["evidence"]), ex["id"]
        for e in ex["evidence"]:
            assert e.get("passage"), (ex["id"], e["source_url"])
            assert " ".join(e["passage"].split()).upper() in _saved_text(e["source_url"]), f"{ex['id']}: passage not found verbatim in the saved record {e['source_url']}"
        if ex["evidence_class"] == "directly_documented":
            assert ex["program_offices"], f"{ex['id']} documented but names no office"
        else:
            assert ex.get("counterevidence") is not None and not ex["program_offices"] or ex["evidence_class"] != "unresolved", ex["id"]
        if ex["evidence_class"] == "unresolved":
            assert ex.get("why_unresolved") and ex.get("resolution_path") and isinstance(ex.get("candidates"), list), ex["id"]
        review = ex["review"]
        assert review["status"] in {"draft", "reviewed", "retracted"} and review["drafted_by"]["actor"], ex["id"]
        assert review["reviewed_by"] is None or (review["reviewed_by"].get("name") and review["reviewed_by"].get("on")), ex["id"]


def test_attribution_examples_agree_with_the_layers_emitter():
    """Each documented example is placed where the emitter places the same award, by the same rule."""
    examples = load(DARPA / "memory" / "attribution_examples.json")
    out = subprocess.run([sys.executable, "-c", """
import json, sys
sys.path.insert(0, 'research/tools')
import agency_layers_sql as L
seed = json.loads(L.SEED.read_text()); offices = L.office_index(seed); uics = L.uic_index(seed); bysol = L.notice_offices_by_solicitation()
placed = {}
for a in L.swept_awards():
    office, rule = L.award_office(a, offices, uics, bysol)
    placed[a['piid']] = [office, rule]
print(json.dumps(placed))
"""], capture_output=True, text=True, env={**os.environ, "AGENCY": "darpa"}, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    placed = json.loads(out.stdout)
    for ex in examples:
        piid = ex["identifier"].split()[0]
        assert piid in placed, f"{ex['id']}: {piid} is not a swept award"
        office, rule = placed[piid]
        if ex["evidence_class"] == "directly_documented":
            assert ex["program_offices"] == [office], f"{ex['id']}: the emitter places {piid} under {office} by {rule}"
        else:
            assert office in (None, "contracting:hr0011"), f"{ex['id']}: the emitter places {piid} under {office}, so it is not unresolved"

"""One layer test for every agency profile: the rules the Navy layer holds to, read against each agency's own
artefacts under research/agencies/<key>. Every test reads the files alone and skips while an artefact is not written
yet. What is the agency's own (an identifier, an office to page, a search word) is in EXPECT; everything else is the
rule itself, so a new profile is tested by adding one EXPECT entry.

With AGENCY set to a profile other than navy (the pipeline's checks stage), only that profile runs; otherwise every
profile with an artefact folder does. The profile module is imported once and each profile is read from PROFILES,
so the tests hold whatever the environment key is.
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
MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

sys.path.insert(0, str(TOOLS))
import agency  # noqa: E402

# What each agency's record is expected to show: identifiers as its sources print them, an office to page, a word to
# search for. A profile with no entry here still runs the file-shape tests.
EXPECT = {
    "darpa": {
        "profile": {"fpds_offices": 1, "sam_orgs": 1, "sbir_component": "DARPA", "forecast": False, "funding_agencies": ["97AE"]},
        "leads": True,
        "piids": ["awarded under HR001125C0043 and HR0011-24-9-0001", "N0003925F4027"],
        "office_codes": ("the BTO and TTO programs, not the PMW 160 ones", ["BTO", "TTO"]),
        "no_hull": "USS Example (DDG 1000)",
        "generic_word": "darpa",
        "budget": {"exhibit": "R-2", "li_re": r"\d{7}[A-Z]?", "appropriation_re": r"0400", "min_books": 2},
        "corpus_names": {"Defense Sciences Office", "Tactical Technology Office", "HR0011"},
        "min_notice_reads": 500, "min_base_awards": 3000,
        "page_office": "Defense Sciences Office", "page_acronym": "DSO", "parent": "Defense Advanced Research Projects Agency", "search": "quantum",
        "help_phrase": "DARPA record as of",
        # the office owners report: the six technical offices at least, program rows from the R-2A pages, display lines from the R-1 sheet
        "owners": {"min_offices": 6, "display_lines": True, "program_rows": True, "staff_listing": True},
    },
    "airforce": {
        "profile": {"fpds_offices": 26, "sam_orgs": 21, "sbir_component": "USAF", "forecast": False, "funding_agencies": []},
        "leads": True,
        "piids": ["awarded under FA8730-25-C-0001 and FA875125Q0055", "FA8806-24-9-0001"],
        "office_codes": ("AFLCMC/HBBK and SSC/CGK filed it; PEO Weapons owns it", ["AFLCMC/HBBK", "SSC/CGK", "PEO Weapons"]),
        "no_hull": "USS Example (DDG 1000)",
        "generic_word": "air force",
        "budget": {"exhibit": "P-40", "li_re": r"\d{4,6}", "appropriation_re": r"3\d{3}F", "min_books": 4},
        "corpus_names": {"FA8730", "FA8750", "FA8806"},
        "min_notice_reads": 100, "min_base_awards": 5000,
        "page_office": "AIR FORCE LIFE CYCLE MANAGEMENT CENTER", "page_acronym": "AFLCMC", "parent": "AIR FORCE MATERIEL COMMAND", "search": "radar",
        "help_phrase": "Air Force record as of",
        # the office owners report: display lines from the P-1 and R-1 sheets, no R-2A program rows (procurement books), no staff listing
        "owners": {"min_offices": 1, "display_lines": True, "program_rows": False, "staff_listing": False},
    },
}


def keys() -> list[str]:
    """The profiles with a layer to test: a seed written under research/agencies/<key> and an EXPECT entry above. An empty
    folder is not a layer, and a layer still being collected (the Army's, 2026-09-26) is tested once its expectations
    are written."""
    env_key = os.environ.get("AGENCY", "").strip().lower()
    wanted = [env_key] if env_key and env_key != "navy" else [k for k in agency.PROFILES if k != "navy"]
    return [k for k in wanted if k in EXPECT and (ROOT / "research" / "agencies" / k / "memory" / "organization_seed.json").exists()]


KEYS = keys()
pytestmark = pytest.mark.skipif(not KEYS, reason="no agency layer under research/agencies with expectations written")


def folder(key: str) -> Path:
    return ROOT / "research" / "agencies" / key


def profile(key: str) -> dict:
    return agency.PROFILES[key]


def load(path: Path):
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} not written yet")
    return json.loads(path.read_text(encoding="utf-8"))


def manifest() -> list[dict]:
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]


def expect(key: str) -> dict:
    if key not in EXPECT:
        pytest.skip(f"no expectations written for {key} yet")
    return EXPECT[key]


def run_tool(key: str, tool: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOLS / tool), *args], capture_output=True, text=True,
                          env={**os.environ, "AGENCY": key}, cwd=ROOT)


# ------------------------------------------------------------------ the profile

@pytest.mark.parametrize("key", KEYS)
def test_profile_runs_under_the_environment_key(key):
    out = run_tool(key, "agency.py", "--selfcheck")
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("key", KEYS)
def test_profile_answers_the_same_questions_as_the_navy(key):
    p, navy = profile(key), agency.PROFILES["navy"]
    assert set(p) == set(navy), set(p) ^ set(navy)
    assert set(p["reading"]) == set(navy["reading"])
    assert p["short"] != navy["short"] and p["database"] != navy["database"]
    assert p["agency"]["node"] and p["fpds_offices"] and p["sam_orgs"], "an agency has offices to sweep"
    assert set(p["sam_orgs"]) <= set(p["sam_org_nodes"]), "every swept SAM.gov organization resolves to a memory node"
    assert p["forecast"]["memory_tool"] and (TOOLS / p["forecast"]["memory_tool"]).exists(), "the seed adapter is written"


@pytest.mark.parametrize("key", KEYS)
def test_note_tag_separates_the_layers(key):
    tagged, plain = f"oversight watch [{key}]: oversight.gov 2026-05-15 DOWIG-2026-085", "oversight watch: oversight.gov 2026-05-15 DODIG-2025-158"
    if agency.KEY == "navy":
        assert agency.note_is_ours(plain) and not agency.note_is_ours(tagged)
    elif agency.KEY == key:
        assert agency.note_is_ours(tagged) and not agency.note_is_ours(plain)
    assert agency.note_is_foreign(f"x [{key}]: y") == (agency.KEY != key)


@pytest.mark.parametrize("key", KEYS)
def test_profile_names_the_agencys_identifiers(key):
    p, e = profile(key), expect(key)["profile"]
    assert len(p["fpds_offices"]) == e["fpds_offices"] and len(p["sam_orgs"]) == e["sam_orgs"], (len(p["fpds_offices"]), len(p["sam_orgs"]))
    assert p["sbir_component"] == e["sbir_component"]
    assert bool(p["forecast"]["pack_glob"]) == e["forecast"], "a forecast is read only where the agency publishes one"
    assert sorted(p["fpds_funding_agencies"]) == e["funding_agencies"]
    assert re.search(p["congress_pattern"], p["label"]) or re.search(p["congress_pattern"], p["short"]), "the committee pattern names the agency"


@pytest.mark.parametrize("key", KEYS)
def test_reading_patterns_are_the_agencys(key):
    p, e = profile(key), expect(key)
    for text in e["piids"]:
        assert re.search(p["piid_re"], text), text
    text, codes = e["office_codes"]
    assert re.findall(p["reading"]["office_code_re"], text) == codes
    assert not re.search(p["reading"]["hull_re"], e["no_hull"]), "only the Navy strips a hull designator"
    assert e["generic_word"] in p["generic_words"] and "navy" not in p["generic_words"]


# ------------------------------------------------------------------ the seed

@pytest.mark.parametrize("key", KEYS)
def test_seed_holds_every_office_the_profile_sweeps(key):
    p = profile(key)
    seed = load(folder(key) / "memory" / "organization_seed.json")
    ids = {n["id"] for n in seed["nodes"]}
    assert set(p["sam_org_nodes"].values()) <= ids, set(p["sam_org_nodes"].values()) - ids
    assert p["agency"]["node"] in ids
    names = {n["name"] for n in seed["nodes"]} | {a["text"] for n in seed["nodes"] for a in n.get("aliases", [])}
    for office in p["pilot_offices"]:
        assert office in names, office
    for org in p["coverage_orgs"]:
        assert org in names, f"{org} in the coverage matrix is not a seed name or alias"
    for n in seed["nodes"]:
        assert n["observation_ids"] and n["review_status"] in ("draft", "reviewed") and n.get("generator"), n["id"]


@pytest.mark.parametrize("key", KEYS)
def test_seed_observations_are_verbatim_passages_on_saved_pages(key):
    seed = load(folder(key) / "memory" / "organization_seed.json")
    saved = {r["sha256"][:12]: r for r in manifest() if r.get("status") == 200 and r.get("sha256") and r.get("path")}
    assert seed["observations"], "no observation"
    obs_ids = {o["id"] for o in seed["observations"]}
    for obs in seed["observations"]:
        assert obs["passage"].strip() and DATE_RE.match(obs["observed_at"]), obs["id"]
        m = re.search(r"sha256 ([0-9a-f]{12})", obs["source_revision"] or "")
        assert m and m.group(1) in saved, f"{obs['id']} cites a document the ledger does not hold"
        row = saved[m.group(1)]
        assert row["url"] == obs["source_url"], obs["id"]
        page = ROOT / row["path"]
        if page.exists():
            text = page.read_text(encoding="utf-8", errors="ignore")
            if obs["passage"] not in text:
                from org_memory_lrae import page_text, pdf_text, squash
                text = squash(pdf_text(page) if page.suffix.lower() == ".pdf" else page_text(page))
            assert obs["passage"] in text, f"{obs['id']} passage is not on the saved page verbatim"
    for rel in seed["relationships"]:
        assert rel["observation_ids"] and set(rel["observation_ids"]) <= obs_ids, rel["id"]
        assert rel["evidence_class"] in ("directly_documented", "inferred") and rel["review_status"] == "draft", rel["id"]
        if rel["type"] == "leads":
            assert rel.get("role_as_written"), f"{rel['id']}: a leader carries the role the page writes"
            # A start date the page states is documented; a page that states none says so, and the observation is dated.
            if rel.get("effective_dates_status") == "documented":
                assert DATE_RE.match(rel.get("effective_from") or ""), rel["id"]
            else:
                assert rel.get("effective_dates_note"), rel["id"]
    leads = [r for r in seed["relationships"] if r["type"] == "leads"]
    if expect(key).get("leads"):
        assert leads, "the agency's own pages name who leads its commands and offices"
    text = json.dumps(seed).lower()
    assert "decision-maker" not in text and "decision maker" not in text


# ------------------------------------------------------------------ the events

@pytest.mark.parametrize("key", KEYS)
def test_every_event_family_the_profile_has_is_written(key):
    events = folder(key) / "events"
    for name in ("budget_lines.json", "sbir_topics.json", "fedreg_events.json", "congress_events.json", "news_observations.json",
                 "protest_events.json", "oversight_events.json", "remarks_events.json"):
        load(events / name)


@pytest.mark.parametrize("key", KEYS)
def test_budget_lines_carry_their_book_and_its_years(key):
    p, e = profile(key), expect(key)["budget"]
    payload = load(folder(key) / "events" / "budget_lines.json")
    assert payload["provider"] == p["budget"]["provider"]
    assert payload["lines"], "no line read"
    books = {b["path"]: b for b in payload["books"]}
    assert len(books) >= e["min_books"], sorted(books)
    for line in payload["lines"]:
        # The reader takes the exhibit from the book itself, so the agency's books show in the lines they give.
        assert line.get("exhibit") == e["exhibit"] and re.fullmatch(e["li_re"], line["li"]), (line["li"], line.get("exhibit"))
        assert re.fullmatch(e["appropriation_re"], line["appropriation"]), (line["li"], line["appropriation"])
        assert line["book"] in books and DATE_RE.match(line["published"])
        pb = int(books[line["book"]]["pb"])
        assert {f"fy{pb - 1}", f"fy{pb}_total"} <= set(line["amounts"]), (line["li"], pb, sorted(line["amounts"]))
        assert f"FY{pb}" in line["event_title"], line["event_title"]


@pytest.mark.parametrize("key", KEYS)
def test_sbir_topics_are_the_profiles_component_only(key):
    p = profile(key)
    payload = load(folder(key) / "events" / "sbir_topics.json")
    topics = payload["rows"]
    assert topics and payload["topics"] == len(topics), "no topic"
    orgs = set(p["sbir_commands"].values()) | {p["agency"]["node"]}
    for t in topics:
        assert (t.get("component") or "").upper() == p["sbir_component"], t.get("code") or t
        assert DATE_RE.match(t.get("pre_release") or t.get("open") or ""), t.get("code")
        assert t["org"] in orgs, (t.get("command"), t["org"])


@pytest.mark.parametrize("key", KEYS)
def test_unread_documents_are_recorded_not_dropped(key):
    for name in ("oversight_events.json", "remarks_events.json"):
        payload = load(folder(key) / "events" / name)
        for doc in payload["documents"]:
            if doc.get("unread"):
                assert doc["events"] == [] and doc["cassette"] is None, doc["url"]
            else:
                assert doc["cassette"], doc["url"]
            for ev in doc["events"]:
                assert ev.get("evidence_span") or ev.get("evidence") or ev.get("passage"), (doc["url"], ev)
        assert payload.get("unread", 0) == sum(1 for d in payload["documents"] if d.get("unread"))


# ------------------------------------------------------------------ the people

@pytest.mark.parametrize("key", KEYS)
def test_people_carry_a_stated_role_and_a_source(key):
    p = profile(key)
    payload = load(folder(key) / "memory" / "people.json")
    people = payload["rows"]
    assert people and payload["people"] == len(people)
    sources = {pos["source"] for person in people for pos in person.get("positions", [])}
    assert "sam_gov_site_api" in sources, sources
    if p["people"].get("staff_listing"):
        assert "agency_staff_listing" in sources
    for person in people:
        for pos in person.get("positions", []):
            assert DATE_RE.match(pos["observed_at"]), person.get("name")
            assert pos["role_type"] != "decision_maker", "a role is a stated role, not authority"
            assert pos.get("source_url"), person.get("name")


# ------------------------------------------------------------------ the sources

@pytest.mark.parametrize("key", KEYS)
def test_registry_covers_every_provider_the_layer_emits(key):
    p = profile(key)
    reg = {e["source_key"] for e in load(folder(key) / "sources" / "source_registry.json")}
    needed = {"sam_gov_site_api", "fpds_atom_feed", "sbir_sttr_topics", "federal_register", "govinfo_api", "oversight_gov_reports",
              "gao_reports", "gao_bid_protests", p["budget"]["provider"], *p["remarks"]["providers"].values(), *p["families"]}
    assert needed <= reg, needed - reg


@pytest.mark.parametrize("key", KEYS)
def test_coverage_matrix_rows_are_the_profile_orgs(key):
    p = profile(key)
    matrix = load(folder(key) / "sources" / "coverage_matrix.json")
    assert matrix["organizations"] == p["coverage_orgs"]
    if p["forecast"]["pack_glob"] is None:
        assert all(c.get("reason") == "no_public_source" for c in matrix["cells"] if c["family"] == "forecast"), "no forecast was found"
    assert all(c.get("sources") for c in matrix["cells"] if c["family"] in ("notice", "incumbent", "organization"))
    out = run_tool(key, "coverage.py", "check")
    assert out.returncode == 0, out.stdout[-1500:] + out.stderr[-500:]


@pytest.mark.parametrize("key", KEYS)
def test_coverage_status_lists_no_other_layers_host_as_unregistered(key):
    status = load(folder(key) / "sources" / "source_status.json")
    assert not any(h.endswith("navy.mil") for h in status["unregistered_hosts"] if h not in ("www.niwcatlantic.navy.mil", "www.niwcpacific.navy.mil")), \
        "a host the Navy registry owns is the Navy layer's document, not an unregistered host of this one"


# ------------------------------------------------------------------ the ledger

@pytest.mark.parametrize("key", KEYS)
def test_collection_rows_are_tagged_for_the_profile(key):
    rows = [r for r in manifest() if f"[{key}]" in (r.get("note") or "")]
    assert rows, f"no ledger row for {key}"
    watch = [r for r in rows if re.match(r"^(oversight|remarks|news) (watch|sweep|search|feed)", r["note"])]
    assert watch, f"no {key} watch row"
    for r in rows:
        assert f" [{key}]" in r["note"] and re.search(rf"\[{key}\](?::| |$)", r["note"]), r["note"]


# ------------------------------------------------------------------ the read-back layers

@pytest.mark.parametrize("key", KEYS)
def test_corpus_is_frozen_from_the_profiles_proof_database(key):
    p, e = profile(key), expect(key)
    corpus = load(folder(key) / "results" / "corpus.json")
    assert corpus["db"] == p["database"]
    assert corpus["orgs"] and corpus["events"] and corpus["needs"]
    assert not any(o["org_type"].endswith("\n") for o in corpus["orgs"].values()), "a field never carries psql's final newline"
    names = {o["acronym"] or o["name"] for o in corpus["orgs"].values()} | {o["name"] for o in corpus["orgs"].values()}
    assert e["corpus_names"] <= names, e["corpus_names"] - names
    assert not any(ev["date"] > corpus["frozen_at"][:10] for ev in corpus["events"]), "no statement is dated after the freeze"
    assert all(ev["family"] in ("notice", "incumbent", "programs", "other", "congress", "organization", "forecast", "news", "budget", "leaders",
                                "oversight", "protest", "conference", "grant", "assistance", "hiring") for ev in corpus["events"]), sorted({ev["family"] for ev in corpus["events"]})


@pytest.mark.parametrize("key", KEYS)
def test_unread_outcomes_name_no_cell_but_keep_their_reason(key):
    labels = load(folder(key) / "results" / "outcome_labels.json")
    corpus = load(folder(key) / "results" / "corpus.json")
    assert labels["outcomes"] == len(corpus["outcomes"]) == len(labels["labels"])
    for row in labels["labels"]:
        if row.get("unread"):
            assert row["aliases"] == [] and row["program_office"] == "" and row["cassette"] is None, row
        else:
            assert row["cassette"], "a reading names its cassette"


@pytest.mark.parametrize("key", KEYS)
def test_office_reads_cover_every_contracting_office_notice_and_say_when_unread(key):
    e = expect(key)
    reads = load(folder(key) / "results" / "office_reads.json")
    corpus = load(folder(key) / "results" / "corpus.json")
    contracting = {oid for oid, o in corpus["orgs"].items() if o["org_type"] == "contracting_office"}
    filed = [ev for ev in corpus["events"] if ev["family"] == "notice" and ev["org"] in contracting]
    assert set(reads["notices"]) <= {ev["id"] for ev in filed}, "only notices filed at a contracting office are read"
    code = ("import json, sys; sys.path.insert(0, 'research/tools'); import office_wiki, pages\n"
            "c = json.load(open(sys.argv[1])); read = pages.read_offices(c, pages.Layer(c, [], routes=[]).org_id)\n"
            "print(json.dumps(sorted(e['id'] for e in office_wiki.unread(c, 'notices', read))))")
    ran = subprocess.run([sys.executable, "-c", code, str(folder(key) / "results" / "corpus.json")], capture_output=True, text=True,
                         env={**os.environ, "AGENCY": key}, cwd=ROOT)
    assert ran.returncode == 0, ran.stderr[-800:]
    assert set(reads["notices"]) == set(json.loads(ran.stdout)), "the saved reads are exactly the notices the record places nowhere"
    assert len(reads["notices"]) >= e["min_notice_reads"]
    assert {"notices", "awards", "topics", "directives"} <= set(reads), sorted(reads)
    for nid, a in reads["notices"].items():
        if a.get("unread"):
            assert a["office"] == "" and a["problems"] == [], "an unread notice places nothing"
        else:
            assert a["cassette"] and (a["office"] == "" or a["notice_words"]), nid


@pytest.mark.parametrize("key", KEYS)
def test_pulse_ends_on_the_freeze_day_and_scores_the_agencys_cells(key):
    p = profile(key)
    pulse = load(folder(key) / "results" / "pulse.json")
    corpus = load(folder(key) / "results" / "corpus.json")
    assert pulse["as_of"] <= corpus["frozen_at"][:10], "the pulse never speaks as of a day after the freeze"
    assert pulse["cells"] > 0 and pulse["ranking"], "the offices' requirements are cells"
    offices = {c["office"] for c in pulse["ranking"]}
    owners = {o["name"] for o in corpus["orgs"].values()} | {o["acronym"] for o in corpus["orgs"].values() if o.get("acronym")}
    assert offices <= owners, offices - owners
    assert not any(re.search(r"\b(NAVWAR|NAVSEA|PMW \d+|PEO C4I)\b", o) for o in offices), "never a Navy office"
    assert offices & (set(p["pilot_offices"]) | owners), "the pilot offices are among the ranked"


@pytest.mark.parametrize("key", KEYS)
def test_buying_dna_starts_with_the_first_swept_office(key):
    p, e = profile(key), expect(key)
    dna = load(folder(key) / "results" / "buying_dna.json")
    offices = list(dna["contracting_offices"])
    assert offices[0] == next(iter(p["fpds_offices"])), offices[:3]
    assert dna["base_awards_on_the_pages"] >= e["min_base_awards"]


@pytest.mark.parametrize("key", KEYS)
def test_small_business_offices_come_from_the_department_directory(key):
    p = profile(key)
    rows = load(folder(key) / "memory" / "small_business_offices.json")
    assert rows and p["agency"]["node"] in {r["office_id"] for r in rows}
    saved = {r.get("url") for r in manifest() if r.get("status") == 200} | {r.get("final_url") for r in manifest() if r.get("status") == 200}
    for row in rows:
        assert row["source_url"].startswith("https://business.defense.gov/"), row["source_url"]
        assert "webmaster" not in " ".join(row["lines"]).lower()
        if row["page_saved"]:
            assert row["url"] in saved or row["url"].rstrip("/") in {u.rstrip("/") for u in saved if u}, f"{row['url']} is said to be saved and is not in the ledger"
    assert any(r["page_saved"] for r in rows), "at least one office page is saved"


@pytest.mark.parametrize("key", KEYS)
def test_special_notice_kinds_are_read_per_profile_or_stand_unread(key):
    kinds = load(folder(key) / "results" / "notice_kinds.json")
    assert kinds, "every special notice has a row"
    for nid, a in kinds.items():
        if a.get("unread"):
            assert a["kind"] == "" and a["cassette"] is None, nid
        else:
            assert a["cassette"] and (a["kind"] == "" or a["words"]), nid
    navy = ROOT / "research" / "results" / "notice_kinds.json"
    if navy.exists():
        assert not set(kinds) & set(json.loads(navy.read_text(encoding="utf-8"))), "a notice of this layer is never in the Navy's file"


@pytest.mark.parametrize("key", KEYS)
def test_pages_questions_and_the_read_surface_answer_from_the_record(key):
    e = expect(key)
    if not (folder(key) / "results" / "corpus.json").exists():
        pytest.skip("corpus not frozen yet")
    page = run_tool(key, "pages.py", "office", e["page_office"])
    assert page.returncode == 0, page.stderr[-800:]
    assert "sources speaking about this object" in page.stdout, page.stdout[:400]
    asked = run_tool(key, "ask.py", "changed", e["page_office"], "--days", "365")
    assert asked.returncode == 0, asked.stderr[-800:]
    assert asked.stdout.startswith("What changed inside "), asked.stdout[:200]  # the office by name or by its acronym
    helped = run_tool(key, "navy.py", "help")
    assert helped.returncode == 0 and e["help_phrase"] in helped.stdout, helped.stdout[:300]
    office = run_tool(key, "navy.py", "office", e["page_office"])
    assert office.returncode == 0, office.stderr[-600:]
    got = json.loads(office.stdout)
    assert got["office"] in (e["page_office"], e.get("page_acronym")) and got.get("beside"), sorted(got)  # the name or the acronym
    near = run_tool(key, "navy.py", "neighbors", e["page_office"])
    assert near.returncode == 0 and json.loads(near.stdout).get("parent") == e["parent"], near.stdout[:300]
    found = run_tool(key, "navy.py", "search", e["search"])
    assert found.returncode == 0 and found.stdout.strip(), found.stderr[-300:]


@pytest.mark.parametrize("key", KEYS)
def test_coverage_org_nodes_name_seed_nodes_or_wait_for_stage_two(key):
    """A generated matrix's rows name the memory node behind them; a row without one is not_started in every cell."""
    p = profile(key)
    nodes = p["coverage_org_nodes"]
    assert set(p) >= {"coverage_org_nodes", "coverage_department_wide"}
    if not nodes:
        return
    assert list(nodes) == p["coverage_orgs"]
    seed = {n["id"] for n in load(folder(key) / "memory" / "organization_seed.json")["nodes"]}
    matrix = load(folder(key) / "sources" / "coverage_matrix.json")
    for label, node in nodes.items():
        if node is None:
            assert all(c.get("reason") == "not_started" for c in matrix["cells"] if c["org"] == label), label
        else:
            assert node in seed, (label, node)
    for fam, sources in p["coverage_department_wide"].items():
        reg = {e["source_key"] for e in load(folder(key) / "sources" / "source_registry.json")}
        assert set(sources) <= reg, (fam, set(sources) - reg)


@pytest.mark.parametrize("key", KEYS)
def test_office_owners_report_keeps_facts_inferences_and_recommendations_apart(key):
    """The office owners report of the profile: written under results/, facts and inferences under their own keys, the
    budget measures with the Comptroller's labels where the profile reads the display sheets, the program rows where
    its books are RDT&E, and the boundary sentence where a family is not collected."""
    path = folder(key) / "results" / "office_owners.json"
    expect = EXPECT[key].get("owners")
    if not path.exists() or not expect:
        pytest.skip("office owners report not written or no expectations")
    payload = load(path)
    assert payload["agency"] == key and payload["summary"]["offices"] >= expect["min_offices"]
    assert set(payload) >= {"facts", "inferences", "recommendations", "boundaries"}
    assert bool(payload["facts"]["budget_measures"]) == expect["display_lines"]
    assert bool(payload["facts"]["program_rows"]) == expect["program_rows"]
    assert ("the staff listing (" in payload["boundaries"]["people"]) == expect["staff_listing"], payload["boundaries"]["people"]
    for group, rows in payload["inferences"].items():
        assert all(r["label"] in ("inference", "fact") and (r["label"] == "fact" or r.get("rule")) for r in rows), group
    text = (folder(key) / "results" / "office_owners.md").read_text(encoding="utf-8")
    assert "## 3. The offices" in text and "decision-maker" not in text.lower() and "decision maker" not in text.lower()

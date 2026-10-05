"""The hiring family: every job announcement modelled rests on a saved listing and, where the page was taken, on the
saved page; every claim quotes the announcement's own words; a posting is read as an intention to hire, never as
authority, parentage or ownership; the searches a negative rests on are recorded.

Reads the files alone and skips while the record is not built. NAVY_DB names the loaded database for the last test;
without it that test skips. With AGENCY set to another profile the same rules read that layer's files."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from agency import KEY, P  # noqa: E402
from jobs import ACQUISITION_SERIES, ANNOUNCEMENT_RE, API, POSTURE, PROVIDER, RECORDS, STATUS_READING, is_announcement, is_search, page_text  # noqa: E402
from news import manifest_rows  # noqa: E402
from reader import flatten  # noqa: E402

pytestmark = pytest.mark.skipif(not RECORDS.exists(), reason="hiring_observations.json not built")
DB = os.environ.get("NAVY_DB")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$")
RELATIONS = {"new signal", "corroborates", "conflicts"}
KINDS = {"vacancy", "placement", "stand_up", "workload", "existence"}
TOOL = ROOT / "research" / "tools" / "jobs.py"


def one(sql: str) -> str:
    dsn = (f"postgresql://{os.environ.get('PGUSER', 'postgres')}:{os.environ.get('PGPASSWORD', 'postgres')}"
           f"@{os.environ.get('PGHOST', '127.0.0.1')}:{os.environ.get('PGPORT', '54322')}/{DB}")
    return subprocess.run(["psql", dsn, "-At", "-c", sql], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(RECORDS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def postings(record) -> list[dict]:
    return record["postings"]


@pytest.fixture(scope="module")
def ledger() -> dict[str, dict]:
    return {r["sha256"]: r for r in manifest_rows() if r.get("sha256")}


def test_every_posting_is_dated_identified_and_filed_under_the_source(postings, record):
    assert postings and record["provider"] == PROVIDER and record["agency_codes"] == P["hiring"]["usajobs_agency_codes"]
    ids = [p["id"] for p in postings]
    assert len(ids) == len(set(ids)), "one record per control number, the newest listing read"
    for p in postings:
        assert p["id"] == f"job:{p['control_number']}" and p["control_number"].isdigit()
        assert ANNOUNCEMENT_RE.match(p["url"]) and ANNOUNCEMENT_RE.match(p["url"]).group(1) == p["control_number"]
        assert DAY.match(p["opened"]), (p["id"], "an announcement states the day it opened")
        assert p["hiring"]["agency_code"] in record["agency_codes"], (p["id"], p["hiring"]["agency_code"])
        assert p["hiring"]["org"] == record["agency_codes"][p["hiring"]["agency_code"]]
        assert all(re.fullmatch(r"\d{4}", s) for s in p["series"]), (p["id"], p["series"])
        assert p["acquisition_workforce"] == bool(set(p["series"]) & set(ACQUISITION_SERIES)), p["id"]
        assert p["source_type"] == "official announcement" and p["reliability"] == "high"
    assert postings == sorted(postings, key=lambda p: (p["opened"], p["id"]))


def test_every_posting_rests_on_a_saved_listing_and_page(postings, ledger):
    for p in postings:
        listing = ledger.get(p["record"]["sha256"])
        assert listing and is_search(listing) and listing["path"] == p["record"]["path"], (p["id"], "the listing it was read from is a saved row of this layer's sweep")
        assert (ROOT / p["record"]["path"]).exists() and listing["url"].startswith(API)
        assert p["listings"] and all(STAMP.match(l["retrieved_at"]) and len(l["sha256"]) == 64 for l in p["listings"]), p["id"]
        assert p["listed_at"] == max(l["retrieved_at"] for l in p["listings"]), "the newest listing read is the one the record shows"
        if p["announcement"]:
            page = ledger.get(p["announcement"]["sha256"])
            assert page and is_announcement(page) and page["url"] == p["url"], (p["id"], "the page is a saved row of this layer's sweep")
            assert (ROOT / p["announcement"]["path"]).exists()


def test_every_claim_quotes_the_announcement_verbatim(postings):
    for p in postings:
        assert p["claims"] and p["claims"][0]["statement_type"] == "vacancy", (p["id"], "the listing itself is the first claim")
        assert p["claims"][0]["passage"] == p["title"] and p["claims"][0]["fields"]["usajobsControlNumber"] == int(p["control_number"])
        if len(p["claims"]) > 1:
            assert p["announcement"], (p["id"], "a claim beyond the listing rests on the page")
            text = flatten(page_text((ROOT / p["announcement"]["path"]).read_bytes()))
            for claim in p["claims"][1:]:
                assert flatten(claim["passage"]) in text, (p["id"], claim["passage"][:80])
        for claim in p["claims"]:
            assert claim["statement_type"] in KINDS and claim["relation"] in RELATIONS and claim["confidence"] in ("low", "medium", "high"), (p["id"], claim)
            assert claim["relation_note"] and "a vacancy announced is an intention to hire, not a position filled" in claim["verify"], p["id"]
        assert p["relation"] in RELATIONS
        assert p["relation"] == ("conflicts" if any(c["relation"] == "conflicts" for c in p["claims"])
                                 else "corroborates" if any(c["relation"] == "corroborates" for c in p["claims"]) else "new signal")


def test_a_posting_is_intent_never_authority(postings):
    # The tool's own words never call anyone a decision-maker; an announcement's quoted passage may, and stays as written.
    own = json.dumps([{k: v for k, v in p.items() if k not in ("claims", "title", "entities")}
                      | {"claims": [{k: v for k, v in c.items() if k not in ("passage", "fields")} for c in p["claims"]]} for p in postings]).lower()
    assert "decision-maker" not in own and "decision maker" not in own
    for p in postings:
        assert p["reading"]["status"] == STATUS_READING.get(p["status_as_listed"] or None, "not stated")
        assert p["reading"]["posture"] == POSTURE[p["reading"]["status"]]
        if p["reading"]["posture"] == "action":
            assert p["status_as_listed"] == "Candidate selected", "only a stated selection is an action taken"
        if p["reading"]["flyer"]:
            assert all(c["confidence"] != "high" for c in p["claims"]), (p["id"], "a flyer of anticipated vacancies never reads high")
        # A posting names offices; it never places one under another or names who leads one.
        assert "parentage" not in {c["statement_type"] for c in p["claims"]} and "leadership" not in {c["statement_type"] for c in p["claims"]}
        if p["reading"]["leader_role"]:
            assert p["claims"][0]["verify"], p["id"]


def test_offices_named_are_in_the_organization_memory(postings):
    seed = {n["id"] for n in json.loads((ROOT / ("research" if KEY == "navy" else f"research/agencies/{KEY}") / "memory" / "organization_seed.json")
                                        .read_text(encoding="utf-8"))["nodes"]}
    for p in postings:
        assert set(p["links"]["offices"]) <= seed, (p["id"], set(p["links"]["offices"]) - seed)
        assert set(p["entities"]["organizations"]) == set(p["links"]["offices"])
        if p["hiring"]["org"]:
            assert p["hiring"]["org"] in seed, (p["id"], "the profile maps every code to a node the memory holds")


def test_what_loads_is_the_acquisition_workforce_or_a_known_name(postings):
    for p in postings:
        beyond = set(p["links"]["offices"]) - {p["hiring"]["org"], P["agency"]["node"]}
        named = bool(beyond or p["links"]["requirements"] or p["links"]["awards"] or p["links"]["solicitations"])
        assert p["loads"] == (p["acquisition_workforce"] or named), p["id"]


def test_searches_are_recorded_so_a_negative_names_its_search(record, ledger):
    assert record["searches"], "every listing page read is recorded with its window and hash"
    for s in record["searches"]:
        row = ledger.get(s["sha256"])
        assert row and is_search(row) and row["url"] == s["url"] and s["agency_code"] in record["agency_codes"], s["url"]
        assert all(DAY.match(d) for d in s["window"]) and s["window"][0] <= s["window"][1]
        assert s["announcements"] >= 0
    listed = sum(s["announcements"] for s in record["searches"])
    assert listed >= len(record["postings"]), "a control number listed twice is one record"


def test_selfcheck_holds():
    # The selfcheck reads Navy fixtures, as every tool's does; the checks stage runs them under the Navy profile too.
    out = subprocess.run([sys.executable, str(TOOL), "--selfcheck"], capture_output=True, text=True, cwd=ROOT, env={**os.environ, "AGENCY": "navy"})
    assert out.returncode == 0 and "jobs selfcheck ok" in out.stdout, out.stdout[-800:] + out.stderr[-800:]


@pytest.mark.skipif(not DB, reason="NAVY_DB names no loaded database")
def test_postings_load_as_dated_vacancy_events_with_their_claims_as_evidence(postings):
    loadable = [p for p in postings if p["loads"] and p["opened"]]
    assert one(f"select count(*) from public.agency_brain_items where source_provider='{PROVIDER}'") == str(len(loadable))
    assert one(f"select count(*) from public.agency_brain_items where source_provider='{PROVIDER}' and (published_at is null or event_type <> 'vacancy_posted' "
               "or section <> 'people' or source_tier <> 'official')") == "0"
    assert one(f"select count(*) from public.gov_intelligence_evidence e join public.agency_brain_items i on i.id=e.brain_item_id "
               f"where i.source_provider='{PROVIDER}'") == str(sum(len(p["claims"]) for p in loadable))
    assert one("select count(*) from public.gov_intelligence_assertions where source_key like 'job:%' or lineage_key like '%job:%'") == "0", \
        "a posting asserts nothing about the org chart"

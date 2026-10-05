"""The hiring family's second collector: a contractor's posting rests on a saved router answer and a saved page of the
company's own (or its applicant system's), never an aggregator; every claim quotes the page; a posting reads at most
medium and loads only when its words name something the record holds; the searches a negative rests on are recorded.

The rules run on fixtures without the record; the record tests skip while vendor_hiring_observations.json is not
built. NAVY_DB names the loaded database for the last test."""

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
from news import manifest_rows  # noqa: E402
from reader import flatten  # noqa: E402
from vendor_jobs import (AGGREGATOR_HOSTS, PROVIDER, RECORDS, RUN_URL, SOURCE_TYPE, VERIFY, company_tokens, display_name, host_allowed,  # noqa: E402
                         host_in, is_page, is_search, page_text, search_params)

DB = os.environ.get("NAVY_DB")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$")
RELATIONS = {"new signal", "corroborates", "conflicts"}
KINDS = {"vacancy", "support", "stand_up", "workload", "existence"}
TOOL = ROOT / "research" / "tools" / "vendor_jobs.py"
L3HARRIS = {"uei": "LB5KVANFKPY7", "name": "L3HARRIS TECHNOLOGIES, INC.", "spellings": ["HARRIS CORPORATION", "L3HARRIS TECHNOLOGIES, INC."], "hosts": []}
needs_record = pytest.mark.skipif(not RECORDS.exists(), reason="vendor_hiring_observations.json not built")


def one(sql: str) -> str:
    dsn = (f"postgresql://{os.environ.get('PGUSER', 'postgres')}:{os.environ.get('PGPASSWORD', 'postgres')}"
           f"@{os.environ.get('PGHOST', '127.0.0.1')}:{os.environ.get('PGPORT', '54322')}/{DB}")
    return subprocess.run(["psql", dsn, "-At", "-c", sql], capture_output=True, text=True, check=True).stdout.strip()


# ---------------------------------------------------------------- the rules

def test_a_page_is_the_companys_own_or_its_applicant_systems_and_matches_by_host_labels_not_substrings():
    assert host_allowed("https://careers.l3harris.com/en/job/123", L3HARRIS) == "company"
    assert host_allowed("https://l3harris.wd5.myworkdayjobs.com/en-US/Careers/job/1", L3HARRIS) == "ats"
    assert host_allowed("https://boards.greenhouse.io/l3harris/jobs/1", L3HARRIS) == "ats"
    assert host_allowed("https://boards.greenhouse.io/leidos/jobs/1", L3HARRIS) is None, "another company's applicant-system page"
    for url in ("https://www.indeed.com/viewjob?jk=1", "https://www.linkedin.com/jobs/view/1", "https://www.glassdoor.com/job/1", "https://www.usajobs.gov/job/1"):
        assert host_allowed(url, L3HARRIS) is None, url
    assert host_allowed("https://notlinkedin.com/x", L3HARRIS) is None
    assert host_allowed("https://spacex.com/careers/1", {"name": "SPACE EXPLORATION TECHNOLOGIES CORP."}) == "company"
    assert host_allowed("https://careers.example.com/1", {**L3HARRIS, "hosts": ["example.com"]}) == "company", "a host the profile names is the company's"
    assert host_in("jobs.lever.co", ("lever.co",)) and not host_in("cleverco.com", ("lever.co",)) and not host_in("lever.co.evil.com", ("lever.co",))
    assert company_tokens(L3HARRIS) == ["l3harris", "harris"] and display_name(L3HARRIS["name"]) == "L3Harris Technologies"
    assert all("." in h for h in AGGREGATOR_HOSTS)


def test_ledger_rows_are_told_apart_by_note_and_address():
    note = "vendor jobs search: LB5KVANFKPY7 L3Harris Technologies | L3Harris Technologies jobs NAVWAR HQ"
    if KEY != "navy":
        note = note.replace("search:", f"search [{KEY}]:")
    assert search_params(note) == {"uei": "LB5KVANFKPY7", "name": "L3Harris Technologies", "query": "L3Harris Technologies jobs NAVWAR HQ", "domain": ""}
    assert search_params(note + " | domain l3harris.com")["domain"] == "l3harris.com"
    assert is_search({"path": "p", "status": 200, "note": note, "url": RUN_URL})
    assert not is_page({"path": "p", "status": 200, "note": note, "url": RUN_URL}), "a search answer is not a page"
    page_note = "vendor job: L3Harris Contracts Manager" if KEY == "navy" else f"vendor job [{KEY}]: L3Harris Contracts Manager"
    assert is_page({"path": "p", "status": 200, "note": page_note, "url": "https://careers.l3harris.com/1"})
    assert not is_page({"path": "p", "status": 200, "note": "news sweep: x", "url": "https://careers.l3harris.com/1"}), "another tool's row is not a page"
    assert not is_search({"path": "p", "status": 404, "note": note, "url": RUN_URL}), "a refused answer carries nothing to model"


def test_selfcheck_holds():
    out = subprocess.run([sys.executable, str(TOOL), "--selfcheck"], capture_output=True, text=True, cwd=ROOT, env={**os.environ, "AGENCY": "navy"})
    assert out.returncode == 0 and "vendor_jobs selfcheck ok" in out.stdout, out.stdout[-800:] + out.stderr[-800:]


# ---------------------------------------------------------------- the record

@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(RECORDS.read_text(encoding="utf-8")) if RECORDS.exists() else {"postings": [], "searches": []}


@pytest.fixture(scope="module")
def postings(record) -> list[dict]:
    return record["postings"]


@pytest.fixture(scope="module")
def ledger() -> dict[str, dict]:
    return {r["sha256"]: r for r in manifest_rows() if r.get("sha256")}


@needs_record
def test_every_posting_is_identified_dated_and_filed_under_the_source(postings, record):
    assert record["provider"] == PROVIDER and record["searches"], "a record without a search is a record of nothing"
    ids = [p["id"] for p in postings]
    assert len(ids) == len(set(ids)), "one record per page"
    for p in postings:
        assert re.fullmatch(r"vendorjob:[0-9a-f]{12}", p["id"]) and p["url"].startswith("http")
        assert p["source_type"] == SOURCE_TYPE and p["reliability"] == "medium", "a company's page never reads high"
        assert p["posted"]["basis"] in ("page", "router", None) and (p["posted"]["date"] == "" or DAY.match(p["posted"]["date"]))
        assert (p["posted"]["basis"] is None) == (p["posted"]["date"] == ""), p["id"]
        assert p["company"]["name"] and p["page"]["host_class"] in ("company", "ats"), (p["id"], "a posting rests on the company's own page")
    assert postings == sorted(postings, key=lambda p: (p["posted"]["date"] or "", p["id"]))


@needs_record
def test_every_posting_rests_on_a_saved_search_and_a_saved_page_of_the_companys_own(postings, ledger):
    for p in postings:
        search = ledger.get(p["search"]["sha256"])
        assert search and is_search(search) and search["path"] == p["search"]["path"], (p["id"], "the router answer it came from is a saved row of this layer's sweep")
        assert (ROOT / p["search"]["path"]).exists() and search["url"] == RUN_URL
        page = ledger.get(p["page"]["sha256"])
        assert page and is_page(page) and page["url"] == p["url"] and (ROOT / p["page"]["path"]).exists(), (p["id"], "the page is a saved row of this layer's sweep")
        assert host_allowed(p["url"], {"name": p["company"]["name"], "spellings": [p["company"]["name"]], "hosts": []}) or p["page"]["host_class"] == "company", \
            (p["id"], "the page's host is the company's or its applicant system's")


@needs_record
def test_every_claim_quotes_the_page_verbatim(postings):
    for p in postings:
        assert p["claims"] and p["claims"][0]["statement_type"] == "vacancy" and p["claims"][0]["passage"] == p["title"], (p["id"], "the title is the first claim")
        text = flatten(page_text((ROOT / p["page"]["path"]).read_bytes()))
        for claim in p["claims"][1:]:
            assert flatten(claim["passage"]) in text, (p["id"], claim["passage"][:80])
        for claim in p["claims"]:
            assert claim["statement_type"] in KINDS and claim["relation"] in RELATIONS and claim["confidence"] in ("low", "medium"), (p["id"], claim)
            assert claim["relation_note"] and VERIFY in claim["verify"], p["id"]
        assert p["relation"] == ("conflicts" if any(c["relation"] == "conflicts" for c in p["claims"])
                                 else "corroborates" if any(c["relation"] == "corroborates" for c in p["claims"]) else "new signal")


@needs_record
def test_a_posting_is_a_companys_intention_never_authority_or_an_org_chart(postings):
    own = json.dumps([{k: v for k, v in p.items() if k not in ("claims", "title", "entities")}
                      | {"claims": [{k: v for k, v in c.items() if k != "passage"} for c in p["claims"]]} for p in postings]).lower()
    assert "decision-maker" not in own and "decision maker" not in own
    for p in postings:
        kinds = {c["statement_type"] for c in p["claims"]}
        assert not kinds & {"parentage", "placement", "leadership"}, (p["id"], "a contractor's seat is support, never a place in the org chart")
        assert VERIFY in p["verify"]


@needs_record
def test_what_loads_names_something_the_record_holds_never_a_title_alone(postings):
    seed = {n["id"] for n in json.loads((ROOT / ("research" if KEY == "navy" else f"research/agencies/{KEY}") / "memory" / "organization_seed.json")
                                        .read_text(encoding="utf-8"))["nodes"]}
    for p in postings:
        assert set(p["links"]["offices"]) <= seed, (p["id"], set(p["links"]["offices"]) - seed)
        named = bool(set(p["links"]["offices"]) - {P["agency"]["node"]} or p["links"]["requirements"] or p["links"]["awards"] or p["links"]["solicitations"])
        assert p["loads"] == named, p["id"]
        if p["loads"]:
            assert len(p["claims"]) > 1 or p["links"]["offices"] or p["links"]["awards"], (p["id"], "a title alone loads nothing")


@needs_record
def test_searches_are_recorded_with_their_pointers_so_a_negative_names_its_search(record, ledger):
    for s in record["searches"]:
        row = ledger.get(s["sha256"])
        assert row and is_search(row) and row["url"] == s["url"] and "query" in s, s  # a company-only search sends no keywords
        assert s["results"] >= s["fetchable"] >= s["pages_saved"] >= 0 and len(s["pointers"]) + s["fetchable"] == s["results"]
        for pointer in s["pointers"]:
            assert pointer["url"] and pointer["why"] and pointer["url"] not in {r.get("url") for r in ledger.values() if is_page(r)}, \
                (pointer["url"], "a pointer is never fetched as a page of this layer")
        assert {"provider", "cost", "currency", "status"} <= set(s["cost"]), "what the run cost and who answered travel with the search, or are null"


@pytest.mark.skipif(not DB, reason="NAVY_DB names no loaded database")
@needs_record
def test_postings_load_as_dated_vendor_vacancy_events_under_the_office_they_name(postings):
    loadable = [p for p in postings if p["loads"] and p["posted"]["date"] and p["links"]["offices"]]
    assert one(f"select count(*) from public.agency_brain_items where source_provider='{PROVIDER}'") <= str(len(loadable))
    assert one(f"select count(*) from public.agency_brain_items where source_provider='{PROVIDER}' and (published_at is null or event_type <> 'vendor_vacancy_posted' "
               "or section <> 'vendors_incumbents' or source_tier <> 'editorial' or primary_organization_id is null)") == "0"
    assert one("select count(*) from public.gov_intelligence_assertions where source_key like 'vendorjob:%' or lineage_key like '%vendorjob:%'") == "0", \
        "a posting asserts nothing about the org chart"

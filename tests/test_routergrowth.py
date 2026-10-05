"""The RouterGrowth client: the input fields come from the capability's own schema wherever it puts them, an option
the schema does not declare is never sent, a run answer reads as one shape whatever the provider's, a price the answer
does not state is null, and today's saved schema answer is reused. No network; every test runs on fixtures."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
import routergrowth  # noqa: E402
from routergrowth import BASE, cost_of, fields_of, payload_for, results, saved_inspect  # noqa: E402

DESCRIBED = {"capability": {"name": "company.jobs", "input_schema": {"properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}},
             "input": {"schema": {"properties": {"max_cost": {"type": "number"}}}},
             "schema": {"properties": {"days": {"type": "integer"}}}}


def test_fields_come_from_the_schema_wherever_the_answer_puts_it():
    assert set(fields_of(DESCRIBED)) == {"query", "limit", "max_cost", "days"}
    assert fields_of({}) == {} and fields_of({"capability": "a string"}) == {} and fields_of({"input": {"schema": "text"}}) == {}


def test_payload_sends_only_what_the_schema_declares_and_the_caller_set():
    fields = fields_of(DESCRIBED)
    assert payload_for(fields, "L3Harris jobs", 5, 30, 0.02) == {"query": "L3Harris jobs", "limit": 5, "days": 30, "max_cost": 0.02}
    assert payload_for({"query": {}, "num_results": {}}, "x", 5, 30, 0.02) == {"query": "x", "num_results": 5}, "max_cost is sent only where the schema takes it"
    assert payload_for(fields, "x") == {"query": "x"}
    assert payload_for({"query": {}, "time_range": {}}, "x", days=7) == {"query": "x", "time_range": "7d"}


def test_results_read_as_one_shape_from_any_provider():
    answer = {"output": {"results": [{"link": "https://careers.example.com/j/1", "job_title": "Contract Specialist", "company": {"name": "Example"},
                                     "date_posted": "2026-09-01", "location": "San Diego, CA"}, "not a dict", {"title": "no url"}]}}
    assert results(answer) == [{"url": "https://careers.example.com/j/1", "title": "Contract Specialist", "publishedDate": "2026-09-01",
                                "company": "Example", "location": "San Diego, CA", "description": ""}]
    assert results({"results": [{"url": "u", "title": "t"}]})[0]["url"] == "u"
    assert results({"output": [{"url": "v"}]})[0]["url"] == "v"
    assert results({"jobs": [{"apply_url": "w", "headline": "h", "employer": "E"}]}) == [{"url": "w", "title": "h", "publishedDate": "", "company": "E", "location": "", "description": ""}]
    routed = {"status": "succeeded", "result": {"jobs": [{"title": "Lead, Contracts", "company": "L3Harris Technologies", "location": "Palm Bay, FL",
                                                         "posted_at": "2026-09-26T18:31:12.000Z", "url": "https://www.linkedin.com/jobs/view/1/", "description": "d"}], "provider_items": []}}
    assert results(routed)[0]["url"] == "https://www.linkedin.com/jobs/view/1/" and results(routed)[0]["publishedDate"] == "2026-09-26T18:31:12.000Z", \
        "the router wraps the provider's list under result.jobs"
    for unknown in ({}, {"results": "text"}):
        with pytest.raises(ValueError):
            results(unknown)  # a shape with no result list is a failed search, not an empty one
    assert results({"status": "no_match", "error": {"code": "no_match"}}) == []


def test_the_charge_is_read_from_billing_and_a_price_the_answer_does_not_state_is_null():
    routed = {"status": "no_match", "provider": "leadmagic", "billing": {"currency": "USD", "quoted": "0.15", "charged": "0"},
              "attempts": [{"provider": "apify", "outcome": "no_match", "detail": "actor returned no items"}, {"provider": "leadmagic", "outcome": "no_match"}]}
    assert cost_of(routed) == {"provider": "leadmagic", "cost": 0.0, "currency": "USD", "status": "no_match",
                               "attempts": ["apify: no_match (actor returned no items)", "leadmagic: no_match"]}, "charged, never the quoted hold"
    assert cost_of({"provider": "leadmagic", "cost": {"amount": 0.0035, "currency": "USD"}})["cost"] == 0.0035
    assert cost_of({"meta": {"routed_to": "apify"}, "usage": 0.01}) == {"provider": "apify", "cost": 0.01, "currency": "USD", "status": None, "attempts": []}
    assert cost_of({"results": []}) == {"provider": None, "cost": None, "currency": None, "status": None, "attempts": []}


def test_only_todays_saved_schema_answer_for_the_capability_is_reused():
    today = date.today().isoformat()
    rows = [{"url": f"{BASE}/v1/inspect", "status": 200, "path": "research/tools/routergrowth.py", "retrieved_at": f"{today}T01:00:00Z", "note": "vendor jobs inspect: company.jobs"},
            {"url": f"{BASE}/v1/inspect", "status": 200, "path": "research/tools/routergrowth.py", "retrieved_at": "2020-01-01T01:00:00Z", "note": "vendor jobs inspect: news.search"},
            {"url": f"{BASE}/v1/run", "status": 200, "path": "research/tools/routergrowth.py", "retrieved_at": f"{today}T01:00:00Z", "note": "vendor jobs search: company.jobs"}]
    assert saved_inspect("company.jobs", rows, today) is rows[0]
    assert saved_inspect("news.search", rows, today) is None, "an older answer is asked again"
    assert saved_inspect("company.jobs", rows[2:], today) is None, "a run answer is not the schema"


def test_the_key_is_read_from_the_environment_or_the_env_file(monkeypatch):
    monkeypatch.setenv(routergrowth.KEY_NAME, "rg_test_value")
    assert routergrowth.key() == "rg_test_value" and routergrowth.headers()["Authorization"] == "Bearer rg_test_value"
    monkeypatch.setenv(routergrowth.KEY_NAME, "")
    monkeypatch.setattr(routergrowth, "env_value", lambda name: "")
    try:
        routergrowth.key()
    except LookupError as exc:
        assert routergrowth.KEY_NAME in str(exc)
    else:
        raise AssertionError("a missing key raises LookupError, which a sweep reads as 'drop this provider'")

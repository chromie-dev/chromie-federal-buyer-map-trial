"""The pre-release sweep (SBIR portals Stage 3): the cycle's due day from the first-Wednesday rule, what the ledger says of
each page this cycle, one recorded fetch for a page with no attempt, and the saved sweep file. The unit tests always run;
the file tests skip while the profile has not swept."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))

import prerelease  # noqa: E402
from prerelease import API, DSIP_KEY, INDEX_SIZE, RULE, answer_of, cycle_attempts, due_day, monthly_pages, new_days, nth_weekday  # noqa: E402

DAY = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


def test_the_due_day_is_the_most_recent_first_wednesday_on_or_before_today():
    assert nth_weekday(2026, 9, 2, 1) == date(2026, 9, 2) and nth_weekday(2026, 4, 2, 1) == date(2026, 4, 1)
    assert due_day(date(2026, 9, 28)) == "2026-09-02" and due_day(date(2026, 9, 2)) == "2026-09-02"
    assert due_day(date(2026, 9, 1)) == "2026-08-05", "before this month's day the cycle is last month's"
    assert due_day(date(2026, 1, 3)) == "2025-12-03", "the cycle rolls back across the year"
    assert due_day(date(2026, 4, 13), {"weekday": 2, "ordinal": 1, "exceptions": ["2026-04-13"]}) == "2026-04-01", "exceptions are documentation, not the rule"


def test_only_this_cycles_attempts_count_and_a_refusal_is_an_answer():
    ledger = [{"url": "https://x/", "retrieved_at": "2026-09-01T00:00:00Z", "status": 200, "path": "p"},
              {"url": "https://x/", "retrieved_at": "2026-09-03T00:00:00Z", "status": 403, "error": "HTTP 403"},
              {"url": "https://y/", "retrieved_at": "2026-09-04T00:00:00Z", "status": 200, "path": "q"}]
    assert [r["status"] for r in cycle_attempts(ledger, "https://x/", "2026-09-02")] == [403]
    assert cycle_attempts(ledger, "https://x/", "2026-09-04") == []
    assert answer_of(ledger[1]) == "refused" and answer_of(ledger[0]) == "page"
    assert answer_of({"status": 200, "path": "p", "content_status": "rejected_stub"}) == "stub"


def test_monthly_rows_alone_are_swept_and_the_dsip_row_lists_its_index_page():
    reg = {"a": {"instrument": {"cadence": {"kind": "monthly", "rule": RULE}, "pages": ["https://x/"]}},
           "b": {"instrument": {"cadence": {"kind": "standing"}, "pages": ["https://y/"]}},
           "c": {"instrument": {"cadence": {"kind": "monthly"}}},
           DSIP_KEY: {"instrument": {"cadence": {"kind": "monthly"}, "pages": ["https://www.dodsbirsttr.mil/topics-app/"]}}}
    pages = {k: p for k, _, p in monthly_pages(reg)}
    assert set(pages) == {"a", DSIP_KEY}, "a standing row and a monthly row with no pages are not swept"
    assert pages[DSIP_KEY][0] == "https://www.dodsbirsttr.mil/topics-app/" and pages[DSIP_KEY][1].startswith(API + "/search?")
    assert f"size={INDEX_SIZE}&page=0" in pages[DSIP_KEY][1]


def test_new_days_are_read_from_a_saved_index_page_and_never_loaded(tmp_path, monkeypatch):
    page = tmp_path / "page.json"
    page.write_text(json.dumps({"data": [
        {"topicId": "a", "topicCode": "DON26BZ07-NP001", "component": "NAVY", "topicPreReleaseStartDate": "1791331200000"},   # 2026-10-07
        {"topicId": "b", "topicCode": "DON26BZ06-DV088", "component": "NAVY", "topicPreReleaseStartDate": "1788307200000"},   # 2026-09-02, known
        {"topicId": "c", "topicCode": "DPA26BZ07-NP001", "component": "DARPA", "topicPreReleaseStartDate": "1791331200000"}]}), encoding="utf-8")
    monkeypatch.setattr(prerelease, "ROOT", tmp_path)
    row = {"url": API + "/search?x", "status": 200, "path": "page.json", "retrieved_at": "2026-10-08T00:00:00Z", "sha256": "f" * 64}
    days = new_days(row, "NAVY", "2026-09-02", {"2026-09-02"})
    assert [d["day"] for d in days] == ["2026-10-07"] and days[0]["codes"] == ["DON26BZ07-NP001"] and days[0]["matches_rule"]["first_wednesday"]
    assert new_days({**row, "status": 403, "path": ""}, "NAVY", "2026-09-02", set()) == []


def test_a_page_with_no_attempt_is_fetched_once_and_the_answer_recorded_whatever_it_is(tmp_path, monkeypatch):
    calls: list[str] = []
    manifest = tmp_path / "ledger.jsonl"
    manifest.write_text(json.dumps({"url": "https://armysbir.army.mil/", "retrieved_at": "2026-09-28T06:55:37Z", "status": 200, "path": "p", "sha256": "a" * 64}) + "\n")
    reg = {"army_sbir_program_site": {"instrument": {"cadence": {"kind": "monthly", "rule": RULE}, "pages": ["https://armysbir.army.mil/", "https://www.navysbir.com/"]}}}

    def fake_fetch(url, method, wayback, note):
        calls.append(url)
        assert method == "direct" and wayback is None and "prerelease sweep" in note and "cycle 2026-09-02" in note
        return {"url": url, "method": method, "retrieved_at": "2026-09-28T07:00:00Z", "status": 403, "error": "HTTP 403", "note": note}

    import fetch as fetch_mod
    monkeypatch.setattr(fetch_mod, "fetch", fake_fetch)
    monkeypatch.setattr(prerelease, "MANIFEST", manifest)
    monkeypatch.setattr(prerelease, "manifest_rows", lambda: [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()])
    monkeypatch.setattr(prerelease, "registry_rows", lambda: reg)
    monkeypatch.setattr(prerelease, "OUT", tmp_path / "none.json")
    monkeypatch.setattr(prerelease, "SWEEP_OUT", tmp_path / "sweep.json")
    assert prerelease.sweep(["--fetch", "--today", "2026-09-28", "--pause", "0"]) == 0
    assert calls == ["https://www.navysbir.com/"], "the page saved this cycle is kept, the other is fetched once"
    saved = json.loads((tmp_path / "sweep.json").read_text())
    by = {e["url"]: e for e in saved["listings"]}
    assert by["https://armysbir.army.mil/"]["action"] == "kept" and by["https://armysbir.army.mil/"]["answer"] == "page"
    assert by["https://www.navysbir.com/"]["action"] == "recorded_refused" and by["https://www.navysbir.com/"]["status"] == 403
    assert manifest.read_text().count("\n") == 2, "the refusal is appended to the ledger"
    # the same cycle again: the refusal is the answer, nothing is retried
    calls.clear()
    assert prerelease.sweep(["--fetch", "--today", "2026-09-29", "--pause", "0"]) == 0 and calls == []
    assert {e["action"] for e in json.loads((tmp_path / "sweep.json").read_text())["listings"]} == {"kept"}


# ---------------------------------------------------------------- the saved sweep of this profile

@pytest.fixture(scope="module")
def swept():
    if not prerelease.SWEEP_OUT.exists():
        pytest.skip("no sweep saved for this profile")
    return json.loads(prerelease.SWEEP_OUT.read_text(encoding="utf-8"))


def test_the_saved_sweep_names_every_monthly_page_and_rests_on_the_ledger(swept):
    rows = prerelease.manifest_rows()
    reg = prerelease.registry_rows()
    expected = {(k, u) for k, _, pages in monthly_pages(reg) for u in pages}
    assert {(e["source_key"], e["url"]) for e in swept["listings"]} == expected
    assert swept["rule"] == RULE and DAY.match(swept["as_of"])
    for e in swept["listings"]:
        assert DAY.match(e["due"]) and e["action"] in {"kept", "fetched", "recorded_refused", "recorded_stub", "due"}
        if e["action"] != "due":
            hits = [r for r in rows if r.get("url") == e["url"] and r.get("retrieved_at") == e["retrieved_at"]]
            assert hits and hits[-1].get("status") == e["status"], (e["url"], e["retrieved_at"])
            assert e["answer"] in {"page", "refused", "stub"} and (e["answer"] == "page") == bool(e["sha256"] and e["status"] == 200 and hits[-1].get("content_status") != "rejected_stub")
    for d in swept["new_days"]:
        assert DAY.match(d["day"]) and d["topics"] > 0 and set(d["matches_rule"]) == {"weekday", "ordinal", "first_wednesday"}

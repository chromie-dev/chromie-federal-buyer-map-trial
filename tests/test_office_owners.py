"""The office owners report (research/tools/office_owners.py): facts, inferences and recommendations kept apart, every fact
with its source, every inference with its rule, the sums recomputable from the rows, and an empty family stated as a
boundary. The file tests run for every profile whose report is written; the rule tests run on fixtures alone.

The rule the working agreement fixes is tested here as text: no row is a "decision_maker", and neither report says
"decision-maker".
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(TOOLS))
import agency  # noqa: E402
import office_owners as oo  # noqa: E402

FOLDERS = {key: (ROOT / "research" if key == "navy" else ROOT / "research" / "agencies" / key) for key in agency.PROFILES}
KEYS = [k for k, d in FOLDERS.items() if (d / "results" / "office_owners.json").exists()
        and (not os.environ.get("AGENCY", "").strip() or os.environ["AGENCY"].strip().lower() in (k, "navy"))]
SECTION_HEADS = ("## 0. How to read this", "## 1. Who holds the budget", "## 2. Budget line by line", "### 2.3 What has been spent this fiscal year",
                 "## 3. The offices", "## 4. Across the offices", "## 5. Funded lines and program rows placed in no office", "## 6. Recommendations",
                 "## 7. What could not be done, and why", "## 8. Sources")
OFFICE_HEADS = ("**Leadership as listed", "**Problem owners", "**Budget lines placed in this office", "**Budget held [inference]**",
                "**Roughly what is left [inference]**", "**What the office bought and is working on now", "**Who would champion a fix [inference]**")
FORBIDDEN = re.compile(r"decision[- ]maker", re.I)


def load(key: str) -> dict:
    return json.loads((FOLDERS[key] / "results" / "office_owners.json").read_text(encoding="utf-8"))


def report(key: str) -> str:
    return (FOLDERS[key] / "results" / "office_owners.md").read_text(encoding="utf-8")


def fact_rows(payload: dict):
    for group, rows in payload["facts"].items():
        for row in rows:
            if group == "activity":
                for sub in ("notices", "forecast_moves", "topics", "contract_events", "hiring", "news", "fy_by_piid"):
                    for r in row.get(sub, []):
                        yield f"activity.{sub}", r
            else:
                yield group, row


# ------------------------------------------------------------------ rules, on fixtures

def test_fiscal_year_window_crosses_the_first_of_october():
    assert oo.fiscal_year("2025-10-01") == 2026 and oo.fiscal_year("2025-09-30") == 2025
    assert oo.fy_window(2026) == ("2025-10-01", "2026-09-30")
    assert oo.in_window("2025-10-01", oo.fy_window(2026)) and not oo.in_window("2026-10-01", oo.fy_window(2026))


def test_program_name_variants_drop_generic_words_and_keep_acronyms():
    v = oo.variants("Network of Optimal Dynamic Energy Signatures (NODES)")
    assert "NODES" in v and "Network of Optimal Dynamic Energy Signatures" in v
    assert oo.variants("Support") == [] and oo.variants("Program Support Services") == [] and oo.variants("Systems") == []
    assert oo.variants("Fleetwood") == ["Fleetwood"] and oo.variants("CANES") == ["CANES"] and oo.variants("SPQ-9B Radar") == ["SPQ-9B Radar"]


def test_strict_matching_needs_a_program_name_position_in_a_long_description():
    assert oo.name_pattern("GO", strict=True).search("GENERATIVE OPTOGENETICS (GO) TA2")
    assert not oo.name_pattern("GO", strict=True).search("GO TO THE SITE FOR SERVICES")
    assert oo.name_pattern("Fleetwood", strict=True).search("BTO: FLEETWOOD TA1")
    assert not oo.name_pattern("Oversight", strict=True).search("ENGINEERING OVERSIGHT AND SUPPORT SERVICES")
    assert oo.name_pattern("Generative Optogenetics", strict=True).search("BTO GENERATIVE OPTOGENETICS TA2")
    assert oo.name_pattern("NODES").search("… (NODES) AWARD") and not oo.name_pattern("NODES").search("ANODES")


def test_ledger_citation_finds_a_notice_page_by_its_id_and_cites_the_newest_copy():
    ledger = oo.Ledger([{"url": "https://sam.gov/api/prod/opps/v2/opportunities/0123456789abcdef0123456789abcdef", "status": 200, "sha256": "abcdef123456" + "0" * 52,
                         "retrieved_at": "2026-09-01T00:00:00Z", "path": "x"},
                        {"url": "https://example.gov/a", "status": 200, "sha256": "f" * 64, "retrieved_at": "2026-01-01T00:00:00Z", "path": "x"},
                        {"url": "https://example.gov/a", "status": 200, "sha256": "e" * 64, "retrieved_at": "2026-02-01T00:00:00Z", "path": "x"}])
    c = ledger.cite("https://sam.gov/opp/0123456789abcdef0123456789abcdef/view", {"event": "e1"}, record="frozen record 2026")
    assert c["sha256_12"] == "abcdef123456" and c["ledger_row"]
    assert ledger.cite("https://example.gov/a")["sha256_12"] == "e" * 12
    missing = ledger.cite("", {"sheet": "NAVWAR", "row": 104}, record="frozen record 2026")
    assert "sheet NAVWAR" in oo.short(missing) and "frozen record" in oo.short(missing)


def test_measure_keys_take_the_plain_request_not_the_mandatory_one():
    keys = oo.measure_keys([{"measures": {"fy2025_actual": 1, "fy2026_enacted": 1, "fy2027_request": 1, "fy2027_mandatory_request": 1, "fy2027_total": 1}}])
    assert keys == {"actual": "fy2025_actual", "enacted": "fy2026_enacted", "request": "fy2027_request", "total_request": "fy2027_total"}


def test_champion_rule_matches_inside_the_year_only():
    window = oo.fy_window(2026)
    events = [{"id": "n1", "available_by": "2026-02-01", "title": "Future Program: Fleetwood"},
              {"id": "n2", "available_by": "2025-02-01", "title": "Fleetwood Proposers Day"},
              {"id": "n3", "available_by": "2026-05-01", "title": "Support services"}]
    year = [e for e in events if oo.in_window(e["available_by"], window)]
    hit = [e["id"] for e in year if any(oo.name_pattern(v).search(e["title"]) for v in oo.variants("Fleetwood"))]
    assert hit == ["n1"]


def test_the_rules_never_name_a_decision_maker():
    assert not FORBIDDEN.search(oo.CHAMPION_RULE) and not FORBIDDEN.search(oo.HOLDER_RULE)
    assert oo.JOIN_BASIS[-1] == "unplaced" and "program_named_on_people_page" in oo.DOCUMENTED and "program_named_in_forecast_row" not in oo.DOCUMENTED


# ------------------------------------------------------------------ the written reports, per profile

pytestmark_files = pytest.mark.skipif(not KEYS, reason="no office_owners.json written yet")


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_facts_inferences_and_recommendations_are_kept_apart(key):
    payload = load(key)
    assert set(payload) >= {"facts", "inferences", "recommendations", "boundaries", "offices", "spending", "unplaced", "summary"}
    for group, row in fact_rows(payload):
        assert "label" not in row or row["label"] != "inference", (group, row.get("id"))
    for group, rows in payload["inferences"].items():
        for row in rows:
            assert row["label"] in ("inference", "fact"), (group, row)
            if row["label"] == "inference":
                assert row.get("rule"), (group, row)
    for rec in payload["recommendations"]:
        assert rec["review_status"] == "draft" and rec["recommendation"]


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_every_fact_row_names_its_source(key):
    payload = load(key)
    checked = 0
    for group, row in fact_rows(payload):
        src = row.get("source")
        assert src is not None, (group, row.get("id"))
        assert "url" in src and "locator" in src, (group, row.get("id"))
        assert src.get("sha256_12") or src.get("record") or src["locator"], (group, row.get("id"), "a fact names a saved copy, the frozen record or a sheet locator")
        if src.get("sha256_12"):
            assert re.fullmatch(r"[0-9a-f]{12}", src["sha256_12"]) and src.get("retrieved_at"), (group, row.get("id"))
        checked += 1
    assert checked > 0


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_no_role_is_a_decision_maker_and_the_report_never_says_so(key):
    payload = load(key)
    for group, row in fact_rows(payload):
        assert row.get("role_type") != "decision_maker", (group, row.get("id"))
    text = report(key)
    assert not FORBIDDEN.search(text)
    assert not FORBIDDEN.search(json.dumps(payload["inferences"]))


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_measures_carry_the_sheets_labels_and_sums_recompute(key):
    payload = load(key)
    fy = payload["fiscal_year"]
    labels = payload.get("measure_labels", {})
    for m in payload["facts"]["budget_measures"]:
        assert m["join_basis"] in payload["join_basis_vocabulary"]
        assert all(re.fullmatch(r"fy\d{4}_[a-z0-9_]+", k) for k in m["measures"]), m["id"]
    if payload["facts"]["budget_measures"]:
        assert labels.get("enacted", "").endswith("_enacted") and labels.get("request", "").endswith("_request") and "mandatory" not in labels["request"]
    by_id = {r["id"]: r for group, r in fact_rows(payload) if "id" in r}
    for office, held in zip(payload["offices"], payload["inferences"]["budget_held"]):
        assert held["office"] == office["uuid"]
        own = [by_id[i] for i in office["budget_measures"] if by_id[i]["join_basis"] != "program_rows_under_the_line"]
        if labels.get("enacted") and own:
            assert held["display_enacted"] == round(sum(r["measures"].get(labels["enacted"]) or 0 for r in own), 3), office["acronym"]
        programs = [by_id[i] for i in office["program_rows"]]
        assert held["program_sum_current_year"] == round(sum((p["amounts"] or {}).get(f"fy{fy}") or 0 for p in programs), 3), office["acronym"]
    for left in payload["inferences"]["remaining"]:
        assert left["remaining_musd"] == round(left["enacted_or_book_current_year_musd"] - left["obligated_musd"], 3)
        assert len(left["caveats"]) == 3


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_fiscal_year_obligations_recompute_from_the_actions(key):
    payload = load(key)
    actions = payload["facts"]["fy_actions"]
    lo, hi = payload["spending"]["window"]
    assert all(lo <= a["signed"] <= hi for a in actions)
    assert payload["spending"]["fpds"]["obligated"] == round(sum(a["obligated"] for a in actions))
    assert payload["spending"]["fpds"]["actions"] == len(actions)
    for a in actions[:200]:
        assert a["source"]["url"], a["piid"]
    for act in payload["facts"]["activity"]:
        for row in act["fy_by_piid"]:
            assert row["source"]["url"], row["piid"]


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_placements_use_the_vocabulary_and_unplaced_rows_are_listed_once(key):
    payload = load(key)
    vocabulary = set(payload["join_basis_vocabulary"])
    for group in ("budget_lines", "budget_measures"):
        for row in payload["facts"][group]:
            assert row["join_basis"] in vocabulary, row["id"]
            assert bool(row["offices"]) == (row["join_basis"] != "unplaced"), row["id"]
    office_lines = {i for o in payload["offices"] for i in o["budget_lines"] + o["budget_measures"] + o["program_rows"]}
    for group in ("book_lines", "display_lines", "program_rows"):
        for i in payload["unplaced"][group]:
            assert i not in office_lines, i
    for p in payload["inferences"]["program_placement"]:
        assert p["basis"] in vocabulary and (p["label"] == "fact") == (p["basis"] in oo.DOCUMENTED)


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_report_has_every_section_and_each_office_its_headings(key):
    text = report(key)
    for head in SECTION_HEADS:
        assert head in text, head
    payload = load(key)
    sections = re.split(r"^### 3\.\d+ ", text, flags=re.M)[1:]
    assert len(sections) >= len(payload["offices"]), "one section per office"
    for section in sections[:len(payload["offices"])]:
        for head in OFFICE_HEADS:
            assert head in section, (section.splitlines()[0], head)
        assert "Rule: " in section, "the champion rule is stated in every office section"


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_an_empty_family_is_stated_as_a_boundary(key):
    payload = load(key)
    b = payload["boundaries"]
    for family in ("record", "budget", "people", "incumbent", "notice", "programs", "forecast", "hiring", "news", "leaders"):
        assert b[family], family
    text = report(key)
    if not payload["facts"]["budget_lines"] and not payload["facts"]["budget_measures"]:
        assert "No budget line is read for this profile" in text
    if not agency.PROFILES[key]["budget"].get("display"):
        assert "no Comptroller display rows" in b["budget"] or "not read yet" in b["budget"]
    for office in payload["offices"]:
        act = next(a for a in payload["facts"]["activity"] if a["office"] == office["uuid"])
        if not act["notices"]:
            assert "No notice this year" in text


@pytestmark_files
@pytest.mark.parametrize("key", KEYS)
def test_owner_rows_say_when_the_link_is_inferred(key):
    payload = load(key)
    for row in payload["facts"]["problem_owners"]:
        assert row["owner_basis"] in ("page_lists_program", "page_lists_no_program", "pm_of_owning_office", "pm_listed_no_requirement_owned", "pm_listed_earlier_than_the_newest"), row["id"]
        if row["owner_basis"] == "pm_of_owning_office":
            assert row["evidence_class"] == "inferred" and row.get("basis_note"), row["id"]
        else:
            assert row["evidence_class"] == "directly_documented", row["id"]

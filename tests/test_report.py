"""The report layer (research/tools/report.py): a spec over the read surface, never a data path of its own. The rule
tests run on the outreach fixture alone; the record tests run for the layers whose files are in this checkout and
skip otherwise. DARPA is the layer that holds budget, organization and people, procurement and vendors together;
NASA holds forecast rows and offices only, so its report takes the boundary path; DHS has no corpus in this checkout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(TOOLS))
import agency  # noqa: E402
import dossier_compare  # noqa: E402
import navy  # noqa: E402
import report  # noqa: E402
from outreach import fixture  # noqa: E402

FOLDERS = {key: (ROOT / "research" if key == "navy" else ROOT / "research" / "agencies" / key) for key in agency.PROFILES}
ONLY = os.environ.get("AGENCY", "").strip().lower()
KEYS = [k for k, d in FOLDERS.items() if (d / "results" / "office_owners.json").exists() and (not ONLY or ONLY in (k, "navy"))]
DARPA = FOLDERS["darpa"] / "results" / "corpus.json"
NASA = FOLDERS["nasa"] / "results" / "corpus.json"


def run_tool(key: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOLS / "report.py"), *args], capture_output=True, text=True,
                          env={**os.environ, "AGENCY": key}, cwd=ROOT, timeout=1500)


@pytest.fixture
def walk(monkeypatch):
    w = fixture()
    monkeypatch.setattr(report, "FIXTURE_WALK", w)
    return w


# ------------------------------------------------------------------ rules, on fixtures

def test_shipped_specs_validate_and_name_only_read_surface_commands():
    names = report.specs()
    assert {"agency-brief", "agency-full"} <= set(names)
    for name in names:
        spec = report.load_spec(name)
        assert report.validate_spec(spec) == []
        for sec in spec["sections"]:
            assert sec["command"] in navy.HELP or sec["command"] in report.LOCAL
            assert set(sec["needs"]) <= set(report.DOMAINS)
    brief, full = report.load_spec("agency-brief"), report.load_spec("agency-full")
    assert {s["key"] for s in brief["sections"]} <= {s["key"] for s in full["sections"]}, "the brief is a subset of the full report"


def test_validate_spec_names_each_defect():
    bad = {"name": "x", "mode": "brief", "title": "t", "objective": "o", "anchors": {"count": 1, "by": ["budget"]},
           "sections": [{"key": "a", "title": "t", "question": "q", "command": "nope", "args": "{thing}", "needs": ["x"], "per_anchor": True,
                         "required_parts": [], "modes": ["brief"]}]}
    problems = " ".join(report.validate_spec(bad))
    assert "not a navy.py command" in problems and "unknown placeholders" in problems and "needs outside" in problems and "per_anchor without {office}" in problems


def test_fill_quotes_arguments_and_leaves_titles_as_written():
    assert report.fill("changed {office}", {"office": "PMA/PMW 101"}) == "changed 'PMA/PMW 101'"
    assert report.fill("Where {office} sits", {"office": "PMA/PMW 101"}, quote=False) == "Where PMA/PMW 101 sits"
    assert report.fill("{focus}", {"focus": ""}) == "" and report.fill("{vendor}", {}) == "{vendor}"


def test_anchors_by_budget_then_pulse_with_their_basis(walk):
    owners = {"offices": [{"uuid": "u1", "acronym": "PMA/PMW 101", "name": "MIDS"}, {"uuid": "u2", "acronym": "PMW 205", "name": "NEN"}],
              "inferences": {"budget_held": [{"office": "u1", "display_enacted": None, "shared_lines_enacted": 12.5}, {"office": "u2", "display_enacted": 40.0}]}}
    ranking = [{"office": "N00039"}, {"office": "N00039"}, {"office": "PMW 205"}]
    got = report.anchors({}, walk.layer, "", 3, ["budget", "pulse"], owners, ranking)
    assert [a["office"] for a in got] == ["PMW 205", "PMA/PMW 101", "N00039"]
    assert got[0]["basis"].startswith("budget: Comptroller display, enacted 40.0") and got[1]["basis"].startswith("budget: shared display lines")
    assert got[2]["basis"].startswith("pulse: 2 of the 3 ranked cells")
    assert report.anchors({}, walk.layer, "", 2, ["budget"], owners, ranking) == got[:2], "the count caps the list"
    focus = report.anchors({}, walk.layer, "radios", 3, ["budget"], owners, ranking,
                           search=lambda term: {"offices": [{"office": "PMW 205", "type": "program_office", "statements": 4}, {"office": "Department of the Navy", "type": "agency", "statements": 9}]})
    assert focus[0]["office"] == "PMW 205" and focus[0]["basis"].startswith("search: 4 statements carry 'radios'") and len(focus) == 2
    assert report.anchors({}, walk.layer, "", 3, ["budget", "pulse"], {"offices": [], "inferences": {}}, []) == []


def inventory_stub(absent: tuple[str, ...], corpus: bool = True) -> dict:
    files = {"corpus.json": {"rows": 5 if corpus else 0, "newest": "2026-09-01" if corpus else ""}, "buying_dna.json": {"rows": 0, "newest": ""},
             "budget_lines.json": {"rows": 0, "newest": ""}, "people.json": {"rows": 0, "newest": ""}, "vendors.json": {"rows": 0, "newest": ""}}
    by = {"awards": ["buying_dna.json"], "budget": ["budget_lines.json"], "people": ["people.json"], "vendors": ["vendors.json"]}
    return {"agency": "navy", "label": "Department of the Navy", "short": "Navy", "as_of": "2026-09-01",
            "corpus": {"exists": corpus, "note": "" if corpus else "no corpus in this checkout"},
            "domains": {d: {"present": d not in absent, "rows": 0 if d in absent else 5, "newest": "", "files": by.get(d, ["corpus.json"]), "answered_by": report.DOMAINS[d]} for d in report.DOMAINS},
            "files": files, "truth": {"verdict": "in-code-and-collected", "brief_ready": True, "built_from": "test"}}


OWNERS = {"offices": [{"uuid": "u1", "acronym": "PMA/PMW 101", "name": "MIDS"}], "inferences": {"budget_held": [{"office": "u1", "display_enacted": 40.0}]}}


def test_plan_marks_missing_domains_as_boundaries_with_what_was_read(walk):
    spec = report.load_spec("agency-brief")
    plan = report.plan(spec, inventory_stub(("awards", "budget")), "", None, walk.layer, OWNERS, [])
    status = {s["section"]: s["status"] for s in plan["steps"]}
    assert status["budget"] == "boundary" and status["vendors"] == "boundary" and status["people"] == "planned" and status["record"] == "planned"
    vendors = next(s for s in plan["steps"] if s["section"] == "vendors")
    assert "awards: 0 rows in this record" in vendors["reason"] and "buying_dna.json (0 rows)" in vendors["reason"]
    assert vendors["cli"] == "AGENCY=navy python research/tools/navy.py dna 'PMA/PMW 101'" and vendors["mcp"] == {"tool": "dna", "arguments": {"args": "'PMA/PMW 101'"}}
    assert plan["anchors"][0]["office"] == "PMA/PMW 101" and plan["anchor_rule"] == report.ANCHOR_RULE
    assert set(report.answer_schema(plan)["properties"]["sections"]["required"]) == {s["key"] for s in plan["steps"]}


def test_plan_without_a_corpus_keeps_only_the_file_backed_sections(walk):
    spec = report.load_spec("agency-brief")
    inv = inventory_stub(("record", "procurement", "awards", "signals", "forecast", "protest", "programs", "vendors"), corpus=False)
    plan = report.plan(spec, inv, "", None, None, OWNERS, [])
    statuses = {s["section"]: s["status"] for s in plan["steps"]}
    assert statuses["budget"] == "planned" and statuses["record"] == "planned"
    assert all(v == "boundary" for k, v in statuses.items() if k not in ("budget", "record")), statuses
    assert "no corpus in this checkout" in next(s["reason"] for s in plan["steps"] if s["section"] == "buying_now")


def test_focus_and_mode_select_sections(walk):
    full = report.load_spec("agency-full")
    with_focus = report.plan(full, inventory_stub(()), "radios", None, walk.layer, OWNERS, [], search=lambda t: {"offices": []})
    without = report.plan(full, inventory_stub(()), "", None, walk.layer, OWNERS, [])
    assert {"focus", "topics_focus", "team"} <= {s["section"] for s in with_focus["steps"]} and "topics" not in {s["section"] for s in with_focus["steps"]}
    assert "topics" in {s["section"] for s in without["steps"]} and "focus" not in {s["section"] for s in without["steps"]}
    assert {s["status"] for s in without["steps"] if s["section"] in ("revisions", "status")} == {"optional"}
    assert {s["status"] for s in without["steps"] if s["section"] == "moves"} == {"deferred"}
    brief_only = report.plan(full, inventory_stub(()), "", "brief", walk.layer, OWNERS, [])
    assert {s["section"] for s in brief_only["steps"]} == {s["key"] for s in report.load_spec("agency-brief")["sections"] if s.get("when", "always") != "focus"}


def good_answer(plan: dict) -> tuple[dict, dict, str, str]:
    people_key = next(s["key"] for s in plan["steps"] if s["section"] == "people")
    budget_key = next(s["key"] for s in plan["steps"] if s["section"] == "budget")
    bundle = {"gathered": [{"key": people_key, "section": "people", "command": "people", "args": "'PMA/PMW 101'", "anchor": "PMA/PMW 101", "status": "planned",
                            "output": "Ann Example, Program Manager, PMA/PMW 101; presolicitation N00039-25-RFPREQ-PMA/PMW-101-0046 MIDS WDL SF3 Radio", "chars": 90, "empty": False, "boundary": None},
                           {"key": budget_key, "section": "budget", "command": "report", "args": "budget 'PMA/PMW 101'", "anchor": "PMA/PMW 101", "status": "boundary",
                            "output": "", "chars": 0, "empty": True, "boundary": "budget: 0 rows"}], "boundaries": []}
    sections = {k: {"summary": "", "claims": [], "inferences": [], "not_found": "nothing read"} for k in report.answer_schema(plan)["properties"]["sections"]["required"]}
    sections[people_key] = {"summary": "One program manager is tied to the office.",
                            "claims": [{"claim": "Ann Example manages MIDS.", "identifier": "Ann Example", "quote": "Ann Example, Program Manager", "source": "people 'PMA/PMW 101'"},
                                       {"claim": "The office has a radio requirement.", "identifier": "N00039-25-RFPREQ-PMA/PMW-101-0046", "quote": "MIDS WDL SF3 Radio", "source": "people 'PMA/PMW 101'"}],
                            "inferences": [{"inference": "The program manager answers for the radio requirement.", "rule": "a program manager answers for the requirements of the office that lists them", "rests_on": ["Ann Example"]}],
                            "not_found": ""}
    return bundle, {"sections": sections, "reading": "The record supports the people section."}, people_key, budget_key


def test_check_passes_a_sourced_answer_and_names_each_defect(walk):
    plan = report.plan(report.load_spec("agency-brief"), inventory_stub(("budget",)), "", None, walk.layer, OWNERS, [])
    bundle, good, people_key, budget_key = good_answer(plan)
    got = report.check(bundle, good, plan)
    assert got["problems"] == [] and got["claims"] == 2 and got["quotes_verified"] == 2 and got["identifiers_resolved"] == 2
    assert got["domains_with_verified_claims"] == ["people", "record"]
    c0 = good["sections"][people_key]["claims"][0]
    bad = lambda **kw: {"sections": {**good["sections"], people_key: {**good["sections"][people_key], **kw}}, "reading": "r"}
    cases = {"quote not in the gathered evidence": bad(claims=[{**c0, "quote": "Ann Example, Director"}]),
             "names no record": bad(claims=[{**c0, "identifier": "ZZ-NOT-A-RECORD-1"}]),
             "neither a tool call nor a URL": bad(claims=[{**c0, "source": "my memory"}]),
             "likely": bad(summary="The office will likely buy radios."),
             "decision-maker": bad(summary="She is the decision-maker."),
             "no rule": bad(inferences=[{"inference": "x", "rule": "", "rests_on": []}]),
             "not a claim identifier": bad(inferences=[{"inference": "x", "rule": "r", "rests_on": ["PMW 205"]}]),
             "planned section missing": {"sections": {k: v for k, v in good["sections"].items() if k != people_key}, "reading": "r"},
             "section not in the plan": {"sections": {**good["sections"], "extra": good["sections"][people_key]}, "reading": "r"}}
    for word, answer in cases.items():
        assert any(word in p for p in report.check(bundle, answer, plan)["problems"]), word
    boundary = {"sections": {**good["sections"], budget_key: {"summary": "", "claims": [c0], "inferences": [], "not_found": ""}}, "reading": "r"}
    assert sum("boundary section" in p for p in report.check(bundle, boundary, plan)["problems"]) == 2


def test_a_quote_counts_only_in_the_answer_its_source_names(walk):
    """Regression: a quote used to pass when it appeared anywhere in the section's own answer, whatever source the
    claim cited, and a URL source vouched for every answer that printed the URL."""
    plan = report.plan(report.load_spec("agency-brief"), inventory_stub(("budget",)), "", None, walk.layer, OWNERS, [])
    bundle, good, people_key, _ = good_answer(plan)
    url_a, url_b = "https://example.gov/notice-a", "https://example.gov/notice-b"
    records = [{"name": "Ann Example", "title": "Program Manager, PMA/PMW 101", "source_url": url_a},
               {"name": "Bob Other", "title": "Contracting Officer, PMW 205", "source_url": url_b}]
    bundle["gathered"] += [{"key": "office:PMW 205", "section": "office", "command": "office", "args": "'PMW 205'",
                            "output": json.dumps({"contacts": records}, indent=1), "chars": 300, "empty": False, "boundary": None}]
    c0 = good["sections"][people_key]["claims"][0]
    with_claims = lambda *claims: {"sections": {**good["sections"], people_key: {**good["sections"][people_key], "claims": list(claims)}}, "reading": "r"}
    problems = lambda answer: report.check(bundle, answer, plan)["problems"]

    assert problems(with_claims(c0)) == [], "the source names this section's own call"
    assert problems(with_claims({**c0, "source": "navy.py people PMA/PMW 101"})) == [], "shell quoting does not change the call"
    unrelated = {**c0, "source": "navy.py office 'PMW 205'"}
    assert any("only elsewhere" in p for p in problems(with_claims(unrelated))), "the section's own answer no longer rescues an unrelated source"
    assert problems(with_claims({**c0, "quote": "Program Manager, PMA/PMW 101", "source": url_a})) == [], "the record carrying the URL holds the quote"
    assert any("only elsewhere" in p for p in problems(with_claims({**c0, "quote": "Contracting Officer, PMW 205", "source": url_a}))), \
        "a URL vouches only for its own record, not the whole answer that prints it"

    # a wrapper printing a source URL vouches for its own fields, never for the rows it holds
    url_c = "https://example.gov/office-listing"
    wrapper = {"source_url": url_c, "office": "PMW 120 Battlespace Awareness", "contacts": [{"name": "Cy Row", "title": "Deputy Program Manager, PMW 120"}]}
    bundle["gathered"] += [{"key": "office:PMW 120", "section": "office", "command": "office", "args": "'PMW 120'",
                            "output": json.dumps(wrapper, indent=1), "chars": 200, "empty": False, "boundary": None}]
    assert problems(with_claims({**c0, "quote": "PMW 120 Battlespace Awareness", "source": url_c})) == [], "the wrapper's own field"
    assert any("only elsewhere" in p for p in problems(with_claims({**c0, "quote": "Deputy Program Manager, PMW 120", "source": url_c}))), \
        "a row inside the wrapper carries no URL, so the wrapper's URL does not vouch for it"


def test_render_puts_the_boundary_first_and_keeps_facts_apart(walk):
    plan = report.plan(report.load_spec("agency-brief"), inventory_stub(("budget",)), "", None, walk.layer, OWNERS, [])
    bundle, good, people_key, budget_key = good_answer(plan)
    text = report.render(plan, bundle, good, report.check(bundle, good, plan))
    for head in ("## 0. How to read this", "## 1. What the record holds", "## 2. Anchor offices [inference]", "## 3. Reading", "**Facts**", "**Analysis [inference]**", "**Boundary.**", "Sources", "Method"):
        assert head in text, head
    at = text.index("## 5. Who holds the budget at PMA/PMW 101") if "## 5. Who holds the budget at PMA/PMW 101" in text else text.index("Who holds the budget at PMA/PMW 101")
    section = text[at:text.index("\n## ", at + 1)]
    assert "**Boundary.**" in section and "**Facts**" not in section
    assert "| Ann Example manages MIDS. | Ann Example |" in text and "Rule: a program manager answers" in text
    assert "decision-maker" not in text.replace("never to decisions", "")
    assert report.register_problems(report.render(plan, bundle, report.empty_answer(plan), report.check(bundle, report.empty_answer(plan), plan))) == []


def test_selfchecks():
    assert report.selfcheck() == 0
    assert dossier_compare.selfcheck() == 0


# ------------------------------------------------------------------ the record

@pytest.mark.parametrize("key", KEYS)
def test_inventory_answers_for_every_layer_in_this_checkout(key):
    out = run_tool(key, "inventory")
    assert out.returncode == 0, out.stderr[-2000:]
    inv = json.loads(out.stdout)
    assert inv["agency"] == key and inv["as_of"] and set(inv["domains"]) == set(report.DOMAINS)
    assert all({"present", "rows", "newest", "files", "answered_by"} <= set(d) for d in inv["domains"].values())
    corpus = FOLDERS[key] / "results" / "corpus.json"
    assert inv["corpus"]["exists"] is corpus.exists()
    if not corpus.exists():
        assert "not in this checkout" in inv["corpus"]["note"] or "no " in inv["corpus"]["note"]
        assert not inv["domains"]["record"]["present"]


@pytest.mark.skipif(not DARPA.exists(), reason="the DARPA corpus is not in this checkout")
def test_darpa_plan_anchors_on_budget_and_pulse_and_plans_the_four_domains():
    out = run_tool("darpa", "plan", "--spec", "agency-brief")
    assert out.returncode == 0, out.stderr[-2000:]
    plan = json.loads(out.stdout)
    bases = [a["basis"].split(":")[0] for a in plan["anchors"]]
    assert "budget" in bases and plan["anchors"], plan["anchors"]
    status = {(s["section"], s["anchor"]): s["status"] for s in plan["steps"]}
    first = plan["anchors"][0]["office"]
    assert all(status[(sec, first)] == "planned" for sec in ("budget", "people", "buying_now", "vendors", "organization")), status
    assert plan["inventory"]["domains"]["forecast"]["present"] is False, "DARPA publishes no acquisition forecast"


@pytest.mark.skipif(not DARPA.exists(), reason="the DARPA corpus is not in this checkout")
def test_darpa_gather_combines_budget_people_procurement_and_vendors(tmp_path):
    out = run_tool("darpa", "gather", "--spec", "agency-brief", "--out", str(tmp_path))
    assert out.returncode == 0, out.stderr[-2000:]
    bundle = json.loads((tmp_path / "bundle.json").read_text())
    plan = json.loads((tmp_path / "plan.json").read_text())
    first = plan["anchors"][0]["office"]
    answered = {g["section"] for g in bundle["gathered"] if g["anchor"] in (first, "") and g["output"] and not g["empty"]}
    assert {"record", "organization", "budget", "people", "buying_now", "vendors", "pulse"} <= answered, answered
    budget = next(g for g in bundle["gathered"] if g["section"] == "budget" and g["anchor"] == first)
    assert "Budget lines" in budget["output"] and ("$" in budget["output"] or "FY" in budget["output"]), budget["output"][:300]
    office = json.loads(next(g for g in bundle["gathered"] if g["section"] == "buying_now" and g["anchor"] == first)["output"])
    assert office["office"] and office.get("vendors") is not None
    evidence = (tmp_path / "evidence.md").read_text()
    assert "Anchor offices [inference]" in evidence and "for the rest" in evidence, "long answers are cut with a pointer"
    assert (tmp_path / "schema.json").exists()


@pytest.mark.skipif(not NASA.exists(), reason="the NASA corpus is not in this checkout")
def test_nasa_takes_the_boundary_path(tmp_path):
    out = run_tool("nasa", "gather", "--spec", "agency-brief", "--out", str(tmp_path))
    assert out.returncode == 0, out.stderr[-2000:]
    plan = json.loads((tmp_path / "plan.json").read_text())
    bundle = json.loads((tmp_path / "bundle.json").read_text())
    by_section = {}
    for s in plan["steps"]:
        by_section.setdefault(s["section"], set()).add(s["status"])
    assert by_section["budget"] == {"boundary"} and by_section["people"] == {"boundary"}, by_section
    reason = next(s["reason"] for s in plan["steps"] if s["section"] == "people")
    assert "people.json (0 rows" in reason
    answer = report.empty_answer(plan)
    checked = report.check(bundle, answer, plan)
    assert checked["problems"] == []
    text = report.render(plan, bundle, answer, checked)
    assert text.count("**Boundary.**") >= 2 and "| forecast | yes |" in text


def test_navy_exposes_inventory_and_report_plan_as_tools():
    names = [t["name"] for t in navy.tools()]
    assert "inventory" in names and "report" in names and names[-1] == "report"
    assert set(dossier_compare.PLAIN) == {"help", *navy.HELP}

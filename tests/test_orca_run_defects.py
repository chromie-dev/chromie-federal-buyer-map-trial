"""The tool defects the Orca Aerospace x DARPA brief run of 2026-09-27/28 met, each reproduced by the exact command the run
typed against the DARPA record, and the unit behind each fix. The record-level tests skip while the DARPA layer is not on
disk; the unit tests always run. Defects the brief numbered and this file does not cover stay listed in DECISIONS.md."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "research" / "tools"
DARPA = ROOT / "research" / "agencies" / "darpa"
sys.path.insert(0, str(TOOLS))

from backtest import own_line, own_notice, scan_term  # noqa: E402
from people import own_contacts  # noqa: E402
from vocabulary import capability_terms, term_groups  # noqa: E402

# research/tools/trace.py shares its name with the standard library's trace module, so it is loaded by path.
_spec = importlib.util.spec_from_file_location("trace_tool", TOOLS / "trace.py")
trace_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trace_tool)

record = pytest.mark.skipif(not (DARPA / "results" / "corpus.json").exists() or not (DARPA / "memory" / "people.json").exists(),
                            reason="the DARPA record is not on disk")


def run(tool: str, *args: str) -> str:
    out = subprocess.run([sys.executable, str(TOOLS / tool), *args], capture_output=True, text=True, cwd=ROOT,
                         env={**os.environ, "AGENCY": "darpa"}, timeout=900)
    assert out.returncode == 0, out.stdout[-800:] + out.stderr[-800:]
    return out.stdout


# ---------------------------------------------------------------- defect 4: the active flag never read the response date

def test_a_passed_response_date_closes_a_notice_whatever_the_search_flag_said():
    today = date(2026, 9, 28)
    assert trace_tool.notice_open(True, "2026-08-21", today) is False, "SAM.gov's flag is as of the search, the date is the fact"
    assert trace_tool.notice_open(True, "2026-12-18", today) is True and trace_tool.notice_open(None, "", today) is None
    assert trace_tool.notice_open(None, "2026-08-21", today) is False, "a harvested notice with a passed deadline is closed too"


def test_sgs_hits_carry_the_due_date_and_read_it(tmp_path, monkeypatch):
    body = {"_embedded": {"results": [{"_id": "474fbae8516342138de45318f2c82586", "title": "TTO Office Wide (OW) BAA 2025", "type": {"value": "Solicitation"},
                                       "publishDate": "2026-06-23T11:03:17+00:00", "responseDate": "2026-08-21T20:00:00+00:00", "isActive": True,
                                       "isCanceled": False, "solicitationNumber": "HR001125S0011"}]}}
    (tmp_path / "search.json").write_text(json.dumps(body), encoding="utf-8")
    rows = [{"url": "https://sam.gov/api/prod/sgs/v1/search/?q=TTO", "path": "search.json", "status": 200, "sha256": "a" * 64}]
    monkeypatch.setattr(trace_tool, "ROOT", tmp_path)
    monkeypatch.setattr(trace_tool, "NOTICES", tmp_path / "none")
    hit = trace_tool.sgs_hits(rows, today=date(2026, 9, 28))["474fbae8516342138de45318f2c82586"]
    assert hit["due"] == "2026-08-21" and hit["active"] is False, hit
    assert trace_tool.sgs_hits(rows, today=date(2026, 8, 1))["474fbae8516342138de45318f2c82586"]["active"] is True


@record
def test_trace_notice_hr001125s0011_says_responses_closed():
    out = run("trace.py", "notice", "HR001125S0011")
    assert "responses closed 2026-08-21" in out and "active=True" not in out, out[:600]


# ---------------------------------------------------------------- defect 3: `notice:` keys other views print

@record
def test_trace_notice_accepts_the_record_key_form():
    assert "DARPA-SN-26-101" in run("trace.py", "notice", "notice:DARPASN26101")


# ---------------------------------------------------------------- defect 5: trace need on darpa_proof

def test_the_default_dsn_names_the_profile_database():
    assert trace_tool.DEFAULT_DSN.endswith("/" + trace_tool.P["database"]) and "navy" not in trace_tool.DEFAULT_DSN.split("/")[-1] or trace_tool.P["key"] == "navy"


# ---------------------------------------------------------------- defect 6: the USAspending id form

def test_the_award_id_names_the_agency_and_the_vehicle_fpds_states():
    assert trace_tool.award_uid("HR001126FE029", "9700", "HR001122D0001", "9700") == "CONT_AWD_HR001126FE029_9700_HR001122D0001_9700"
    assert trace_tool.award_uid("N0003926C0001", "9700") == "CONT_AWD_N0003926C0001_9700_-NONE-_-NONE-"
    assert trace_tool.award_uid("70RSAT23T00000016", "7001", kind="other_transaction") is None, "an other transaction has no contract id"
    assert trace_tool.award_uid("", "9700") is None


@record
def test_trace_award_prints_the_real_usaspending_id_or_says_why_not():
    out = run("trace.py", "award", "HR001126FE029")
    assert "CONT_AWD_HR001126FE029_9700_HR001122D0001_9700" in out or "USAspending retrieved" in out, out[:600]
    assert "_9700_-NONE-_-NONE-" not in out
    other = run("trace.py", "award", "HR0011269E082")
    assert "other transaction" in other and "-NONE-_-NONE-" not in other, other[:600]


# ---------------------------------------------------------------- defects 7 and 11: labels and dates from another notice

def test_a_notice_keyed_row_owns_only_the_notices_filed_under_its_key():
    assert own_notice("notice:HR001125S0011", "notice:HR001125S0011")
    assert own_notice("notice:HR001122S0004", "notice:HR001122S0004HR001122C0139"), "the award notice under the solicitation"
    assert not own_notice("notice:DARPASN2450", "notice:DARPASN2682"), "the HEL shopping notice is not the STO RFI's"
    assert own_notice("N00039-23-RFPREQ-PMW-160-0108", "notice:N0003926R0001"), "a forecast row keeps a notice no row claims"
    assert not own_notice("N00039-23-RFPREQ-PMW-160-0108", "N00039-23-RFPREQ-PMW-160-0109"), "one tied to another row is that row's"
    by_id = {"h": {"id": "h", "family": "notice", "line": "notice:SHOP"}, "f": {"id": "f", "family": "forecast"}}
    assert own_line("outcome:h", by_id) == "notice:SHOP" and own_line("outcome:f", by_id) is None and own_line("need:notice:A") == "notice:A"


@record
def test_ask_analogs_reads_a_notice_keyed_row_from_its_own_notice():
    for key in ("notice:HR001125S0011", "notice:HR001126S0015"):
        out = run("ask.py", "analogs", key)
        assert "a reading, not a stored tie" not in out, out[:400]
        assert key.removeprefix("notice:") in out.splitlines()[0], out[:200]


@record
def test_the_pulse_answers_only_a_cells_own_notice():
    corpus = json.loads((DARPA / "results" / "corpus.json").read_text(encoding="utf-8"))
    pulse = json.loads((DARPA / "results" / "pulse.json").read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in corpus["events"]}
    for a in pulse["actions"]:
        if a["type"] != "respond_notice":
            continue
        own = own_line(a["cell"], by_id)
        cited = by_id[a["evidence"][0]]
        assert own_notice(own, cited.get("line")), (a["cell"], a["name"], cited.get("line"))


# ---------------------------------------------------------------- defect 8: contacts from unrelated solicitations

def test_a_statements_own_contacts_come_from_its_own_record():
    pos = lambda ref, title: {"office": "contracting:hr0011", "org": "o", "role_type": "contract_specialist", "raw_title": title, "observed_at": "2026-09-25",  # noqa: E731
                              "source": "sam_gov_site_api", "source_ref": ref, "source_url": "", "context": ""}
    roster = [{"name": "Dr. Own", "emails": ["own@darpa.mil"], "positions": [pos("73f8dbf0ff1f4012b81ba8316d97ae94", "primary point of contact on presolicitation DARPA-PA-26-10_DRAFT")]},
              {"name": "APM PS Coordinator", "emails": [], "positions": [pos("9f7a4a6262ae4ccba3259b90700ce4c9", "primary point of contact on solicitation DARPA-PS-26-141")]}]
    hubble = {"text": "SAM.gov presolicitation 2026-09-24: HUBBLE DRAFT Program Announcement (PA) presolicitation; solicitation DARPA-PA-26-10_DRAFT; record x",
              "url": "https://sam.gov/opp/73f8dbf0ff1f4012b81ba8316d97ae94/view"}
    assert [c["name"] for c in own_contacts(hubble, roster)] == ["Dr. Own"]
    assert own_contacts({"text": "a budget line", "url": ""}, roster) == []


@record
def test_cell_hubble_lists_its_own_coordinator_and_labels_the_offices_people():
    out = run("navy.py", "cell", "610e0ff6-8aca-5c30-a057-06489d6b4e61")
    page = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert [p["name"] for p in page["people"]] == ["Dr. Abhishek Singharoy"], page["people"]
    assert "APM PS Coordinator" not in {p["name"] for p in page["people"]}
    assert page["office_contacts_note"].startswith("whom the record ties to the office")


# ---------------------------------------------------------------- defects 1, 13, 14: zeros where the record carries the words

def test_a_multi_word_term_is_read_by_its_words_when_the_phrase_is_absent():
    evs = [{"id": "s1", "text": "LASSO asks for autonomous satellite navigation", "available_by": "2026-01-01"},
           {"id": "s2", "text": "a satellite ground antenna", "available_by": "2026-01-01"}]
    hits, how = scan_term("spacecraft autonomy", evs)
    assert [e["id"] for e in hits] == ["s1"] and how == "by its words"
    assert term_groups("spacecraft autonomy")[0][0] == "spacecraft" and "satellite" in term_groups("spacecraft autonomy")[0]
    assert capability_terms("spacecraft autonomy")[:2] == ["spacecraft autonomy", "autonomous"], "team and match search the words too"
    assert capability_terms("Link 22; ai")[0] == "Link 22" and capability_terms("Next Generation Jammer") == ["Next Generation Jammer"]


@record
def test_search_spacecraft_autonomy_and_changed_space_are_not_zero():
    out = run("navy.py", "search", "spacecraft autonomy")
    page = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert page["statements"] > 0 and page["matched"] == "by its words", (page["statements"], page["matched"])
    changed = run("ask.py", "changed", "--days", "365", "Defense Advanced Research Projects Agency", "space")
    assert "0 statement(s)" not in changed.splitlines()[0], changed[:300]
    team = run("ask.py", "team", "onboard autonomy for constellation operations")
    assert not team.startswith("0 vendor(s)"), team[:200]

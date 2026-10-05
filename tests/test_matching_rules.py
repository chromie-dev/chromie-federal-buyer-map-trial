"""Matching and reading rules, one test each, on the smallest input that shows the rule.

Each test calls the function that applies the rule."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(TOOLS))
import lrae_package  # noqa: E402

# research/tools/trace.py shares its name with the standard library's trace module, so it is loaded by path.
_spec = importlib.util.spec_from_file_location("trace_tool", TOOLS / "trace.py")
trace_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trace_tool)

TODAY = date(2026, 9, 20)


def row(number: int, title: str, contract: str = "") -> dict:
    """One forecast row under PMW 160, every other column blank."""
    return {**{c: "" for c in lrae_package.COLUMNS}, "sheet": lrae_package.SHEET, "row_number": number,
            "requirement_title": title, "pid": "", "office_code_string": "PMW-160 - PMW-160 - NAVWAR",
            "existing_contract_number": contract, "anticipated_total_value": "No Range Specified"}


def test_1_a_row_citing_two_incumbent_contracts_is_paired_once() -> None:
    """A row citing two incumbent contract numbers pairs once, not once per number."""
    both = "N0003920D0061, N0003921D0075"
    pairs, _, _, _ = lrae_package.pair_releases([row(1, "Alpha", both)], [row(9, "Alpha follow-on", both)])
    assert [(p["old"]["row_number"], p["new"]["row_number"]) for p in pairs] == [(1, 9)], pairs


def test_2_an_ambiguous_group_holds_only_rows_no_stage_paired() -> None:
    """Four rows share one incumbent contract, so that stage cannot pair them; the later title stage pairs the
    reworded Shore Network row. The group then holds only the two rows still unpaired, and a group left with
    nothing on one side is withdrawn, its row reported as removed."""
    contract = "N0003920D0061"
    shore_old, shore_new = "Shore Network Modernisation Support Services", "Shore Network Modernization Support Services"
    changes, _ = lrae_package.diff_releases([row(1, shore_old, contract), row(2, "Beta", contract)],
                                            [row(9, shore_new, contract), row(8, "Gamma", contract)])
    ambiguous = [(c["old_row"], c["new_row"]) for c in changes if c["change"] == "ambiguous"]
    assert ambiguous == [("2", "8")], ambiguous
    changes, _ = lrae_package.diff_releases([row(1, shore_old, contract), row(2, "Beta", contract)], [row(9, shore_new, contract)])
    assert not [c for c in changes if c["change"] == "ambiguous"], changes
    assert [str(c["old_row"]) for c in changes if c["change"] == "removed"] == ["2"], changes


def test_3_the_lines_own_justification_reads_review() -> None:
    """A justification carrying the line's own identifier reads review, not delayed."""
    r = trace_tool.reading("FY26 Q2", [{"type": "Justification (J&A)", "posted": "2026-03-04"}], [], "", "", TODAY, "2026-09-20")
    assert r.startswith("review: justification (j&a) posted 2026-03-04 carries this line's own identifier"), r


def test_4_the_notices_own_record_decides_cancellation(tmp_path, monkeypatch) -> None:
    """The saved notice is the later word: a search that ran before the cancellation says live, the notice says cancelled."""
    search = {"_embedded": {"results": [{"_id": "n1", "title": "Alpha", "type": {"value": "Solicitation"}, "publishDate": "2026-01-05",
                                         "isActive": True, "isCanceled": False, "solicitationNumber": "N0003926R0001"}]}}
    (tmp_path / "search.json").write_text(json.dumps(search), encoding="utf-8")
    (tmp_path / "notices").mkdir()
    monkeypatch.setattr(trace_tool, "ROOT", tmp_path)
    monkeypatch.setattr(trace_tool, "NOTICES", tmp_path / "notices")
    monkeypatch.setattr(trace_tool, "notice_detail", lambda notice_id: {"cancelled": True, "title": "Alpha"})
    hits = trace_tool.sgs_hits([{"status": 200, "url": "https://sam.gov/api/prod/sgs/v1/search?q=alpha", "path": "search.json", "sha256": "0" * 64}])
    assert hits["n1"]["cancelled"], hits


def test_5_a_market_notice_before_the_line_is_the_incumbents_history() -> None:
    """A sources sought of 2020 on the incumbent contract researched the market for that contract, not for a line
    first forecast in 2025; a market notice is read the same way as a solicitation or an action."""
    sought = {"type": "Sources Sought", "posted": "2020-09-28", "via": "incumbent", "shared": [], "method": "explicit"}
    assert trace_tool.notice_role(sought, "2025-06-30") == "incumbent history"
    assert trace_tool.notice_role({**sought, "posted": "2026-01-10"}, "2025-06-30") == "line"


def test_6_a_folded_line_is_not_its_own_sibling() -> None:
    """The chain is keyed by the fold's canonical key, so a folded line never meets itself as a sibling."""
    latest = "lrae_navwar_2025-06"
    key = "N00039-25-RFPREQ-PMW/A-170-0001"
    folded = {"record_key": f"row:{latest}:9", "pid": "", "requirement_title": "NTCDL Follow-On Production", "office_id": "pmw:170",
              "existing_contract_number": "N0003916C0087", "release": latest}
    earlier = {**folded, "release": "lrae_navwar_2023-06", "record_key": "row:lrae_navwar_2023-06:1"}
    ctx = {"rarity": {"ntcdl": 1}, "parents": {}, "offices_of": {}, "latest": latest, "incumbent_lines": {},
           "chains": {key: [earlier, folded]},
           "canon": {(latest, folded["record_key"]): key, ("lrae_navwar_2023-06", earlier["record_key"]): key}}
    assert trace_tool.siblings_of(folded, ctx) == []

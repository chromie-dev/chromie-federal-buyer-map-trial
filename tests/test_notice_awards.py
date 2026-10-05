"""A notice's awards are read from the saved lookup by its solicitation number and from the office sweep, which
carries the orders a multiple-award solicitation produced and can be the later retrieval."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(TOOLS))

# research/tools/trace.py shares its name with the standard library's trace module, so it is loaded by path.
_spec = importlib.util.spec_from_file_location("trace_tool", TOOLS / "trace.py")
trace_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trace_tool)

SWEEP = ("https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=CONTRACTING_OFFICE_ID:N00039"
         "+SIGNED_DATE:%5B2025/10/01,2026/09/30%5D+MODIFICATION_NUMBER:0&start=0")
LOOKUP = "https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=SOLICITATION_ID:N0003926R9118&start=0"


def entry(piid: str, signed: str, solicitation: str) -> str:
    return (f"<entry><ns1:awardID><ns1:awardContractID><ns1:PIID>{piid}</ns1:PIID><ns1:modNumber>0</ns1:modNumber>"
            f"</ns1:awardContractID></ns1:awardID><ns1:signedDate>{signed}T00:00:00</ns1:signedDate>"
            f"<ns1:obligatedAmount>100</ns1:obligatedAmount><ns1:baseAndAllOptionsValue>100</ns1:baseAndAllOptionsValue>"
            f"<ns1:solicitationID>{solicitation}</ns1:solicitationID><ns1:vendorName>LEIDOS, INC.</ns1:vendorName></entry>")


def test_the_sweep_adds_the_awards_the_lookup_lacks(tmp_path, monkeypatch) -> None:
    (tmp_path / "sweep.atom").write_text(entry("N0003926F9137", "2026-02-04", "N0003926R9118") + entry("N0003926F0001", "2026-03-01", "N0003926R0001"))
    (tmp_path / "lookup.atom").write_text("<feed></feed>")
    rows = [{"url": SWEEP, "path": "sweep.atom", "status": 200, "sha256": "1" * 64, "retrieved_at": "2026-09-23T00:00:00Z"},
            {"url": LOOKUP, "path": "lookup.atom", "status": 200, "sha256": "2" * 64, "retrieved_at": "2026-09-21T00:00:00Z"}]
    monkeypatch.setattr(trace_tool, "ROOT", tmp_path)
    monkeypatch.setattr(trace_tool, "manifest", lambda: rows)
    trace_tool.swept_by_solicitation.cache_clear()
    try:
        actions, atom = trace_tool.fpds_by_solicitation(rows, "N00039-26-R-9118")
        assert [(a["piid"], a["signed"], a["source"]["url"]) for a in actions] == [("N0003926F9137", "2026-02-04", SWEEP)], json.dumps(actions)
        assert atom["url"] == LOOKUP
        rows.pop()
        actions, atom = trace_tool.fpds_by_solicitation(rows, "N0003926R9118")
        assert atom["url"] == SWEEP and len(actions) == 1
        assert trace_tool.fpds_by_solicitation(rows, "N0003926R5555") == ([], None)
    finally:
        trace_tool.swept_by_solicitation.cache_clear()

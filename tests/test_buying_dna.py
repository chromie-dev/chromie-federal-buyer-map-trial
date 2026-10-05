"""Buying DNA recomputes from the saved FPDS pages and the frozen corpus alone, and its numbers hold their shape: shares
in [0, 1] and summing to at most one per field, medians inside the stated bands, vendor shares ordered, every cell's
position naming a contract the corpus holds."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from backtest import CORPUS  # noqa: E402
from buying_dna import DNA, PIID_RE, book, dna  # noqa: E402

pytestmark = pytest.mark.skipif(not (DNA.exists() and CORPUS.exists() and (ROOT / "data" / "raw").exists()), reason="no Buying DNA on disk")


@pytest.fixture(scope="module")
def saved():
    return json.loads(DNA.read_text(encoding="utf-8"))


def test_the_contracting_office_book_recomputes_from_the_pages(saved):
    entries = book()
    assert len(entries) == saved["base_awards_on_the_pages"]
    for office, d in saved["contracting_offices"].items():
        assert dna([e for e in entries.values() if e["contracting_office"] == office]) == d


def every_share_list(d: dict):
    for name in ("extent", "set_aside", "procedures"):
        yield d["competition"][name]
    yield d["pricing"]
    yield d["naics"]
    yield d["psc"]
    yield d["vehicle"]["idv_types"]
    yield d["vehicle"]["multiple_or_single"]
    yield d["vehicle"]["action_types"]


def test_shapes(saved):
    books = list(saved["contracting_offices"].values()) + list(saved["offices"].values()) + [c["dna"] for c in saved["cells"]]
    for d in books:
        if not d["awards"]:
            continue
        for rows in every_share_list(d):
            assert all(0 <= r["share"] <= 1 for r in rows) and sum(r["share"] for r in rows) <= 1 + 0.0005 * len(rows) + 1e-9, rows  # shares are rounded to three places
        v = d["value"]
        if v["p25"] is not None:
            assert v["p25"] <= v["median"] <= v["p75"], v
        assert 0 <= d["vehicle"]["under_an_idv"] <= 1 and 0 < d["vendors"]["hhi"] <= 1
        assert d["vendors"]["top_share"] <= d["vendors"]["top3_share"] <= 1
        assert d["recompete"]["median_gap_months"] is None or d["recompete"]["median_gap_months"] > 0, "same-day orders are one award event"


def test_positions_name_contracts_the_corpus_holds(saved):
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    piids = {PIID_RE.search(e["title"]).group(1) for e in corpus["events"] if e["family"] == "incumbent" and PIID_RE.search(e["title"])}
    assert saved["cells"]
    for c in saved["cells"]:
        p = c["position"]
        if p["contracts"]:
            assert set(p["ending_within_two_years"]) <= piids and p["lead_vendor"] and p["reading"], c["key"]
            assert p["next_end"] is None or p["next_end"] > saved["as_of"]

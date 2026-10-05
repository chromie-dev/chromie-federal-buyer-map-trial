"""Vendors resolved by UEI: one vendor per identifier, every spelling the feed used, and the pages read through them."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from backtest import CORPUS  # noqa: E402
from vendors import OUT, names_for  # noqa: E402

pytestmark = pytest.mark.skipif(not OUT.exists(), reason="no vendors.json on disk")


@pytest.fixture(scope="module")
def saved():
    return json.loads(OUT.read_text(encoding="utf-8"))


def test_one_vendor_per_uei_and_one_uei_per_spelling(saved):
    ueis = [v["uei"] for v in saved["vendors"]]
    assert ueis and len(ueis) == len(set(ueis))
    for v in saved["vendors"]:
        assert v["spellings"] and v["name"] in v["spellings"] and v["awards"] >= len(v["spellings"]) and 0 <= v["live"] <= v["awards"]
        assert sum(v["offices"].values()) == v["awards"]
    for s, u in saved["spelling_to_uei"].items():
        assert u in set(ueis) and s in next(v for v in saved["vendors"] if v["uei"] == u)["spellings"]
    assert saved["summary"]["vendors"] == len(saved["vendors"])
    assert sum(v["awards"] for v in saved["vendors"]) + saved["summary"]["awards_without_uei"] <= saved["awards_read"]


def test_two_spellings_of_one_vendor_resolve_together(saved):
    two = [v for v in saved["vendors"] if len(v["spellings"]) > 1]
    assert two, "the feed spells at least one vendor two ways"
    v = two[0]
    for s in v["spellings"]:
        assert names_for(s, saved) == v["spellings"], (s, v["spellings"])
    assert names_for("no such vendor anywhere", saved) == ["no such vendor anywhere"]


@pytest.mark.skipif(not CORPUS.exists(), reason="no frozen corpus on disk")
def test_a_vendor_page_names_every_spelling(saved):
    from pages import Layer
    from pages import vendor
    from pulse import load_people
    layer = Layer(json.loads(CORPUS.read_text(encoding="utf-8")), load_people())
    corpus_names = {e.get("vendor") for e in layer.events if e["family"] == "incumbent"}
    v = next((v for v in saved["vendors"] if len(v["spellings"]) > 1 and len(set(v["spellings"]) & corpus_names) > 1), None)
    if v is None:
        pytest.skip("no vendor with two spellings in the frozen record")
    text = vendor(layer, v["spellings"][0], saved)
    assert all(s in text for s in set(v["spellings"]) & corpus_names), text.splitlines()[0]

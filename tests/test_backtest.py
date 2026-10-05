"""The back-test harness: the results recompute from the frozen corpus and labels alone, recall sits at
or above the ratcheted floors, every hit lists at least one dated event per family, the labels are the notices'
own words, and the register counts signals and never forecasts a release."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from backtest import CORPUS, LABELS, RESULTS, buyer_names, describe, evaluate, register_problems, shift  # noqa: E402
from reader import flatten  # noqa: E402

pytestmark = pytest.mark.skipif(not (CORPUS.exists() and LABELS.exists() and RESULTS.exists()),
                                reason="no frozen back-test on disk")


@pytest.fixture(scope="module")
def frozen():
    return (json.loads(CORPUS.read_text(encoding="utf-8")), json.loads(LABELS.read_text(encoding="utf-8")),
            json.loads(RESULTS.read_text(encoding="utf-8")))


def test_results_recompute_from_the_frozen_files_alone(frozen):
    corpus, labels, saved = frozen
    fresh = evaluate(corpus, labels, saved["min_families"], tuple(saved["horizons"]))
    fresh["floors"] = saved["floors"]
    assert fresh == saved


def test_recall_holds_the_floors(frozen):
    _, _, saved = frozen
    assert saved["floors"]
    for key, floor in saved["floors"].items():
        assert saved["recall"][key.split("@")[1]] >= floor, key


def test_every_hit_lists_a_dated_event_per_family(frozen):
    _, _, saved = frozen
    hits = 0
    for o in saved["outcomes"]:
        for h, row in o["horizons"].items():
            cutoff = shift(o["date"], -int(h))
            assert set(row["evidence"]) == set(row["families"]), (o["id"], h)
            for fam, events in row["evidence"].items():
                assert events and all(e["date"] <= cutoff for e in events), (o["id"], h, fam)
            if row["hit"]:
                hits += 1
                assert len(row["families"]) >= saved["min_families"]
    assert hits >= 1, "the floors above would hold on an empty file"


def test_labels_are_the_notices_own_words_and_never_the_buyer(frozen):
    corpus, labels, _ = frozen
    text_of = {o["id"]: flatten(o["text"]) for o in corpus["outcomes"]}
    buyers = buyer_names(corpus["orgs"])
    assert {r["id"] for r in labels["labels"]} == set(text_of)
    for r in labels["labels"]:
        for alias in r["aliases"]:
            assert flatten(alias) in text_of[r["id"]], (r["id"], alias)
            assert alias.lower() not in buyers, alias
        if r["program_office"]:
            assert flatten(r["program_office"]) in text_of[r["id"]], r["id"]


def test_register_counts_signals_and_never_forecasts_a_release(frozen):
    _, _, saved = frozen
    for o in saved["outcomes"]:
        for h in saved["horizons"]:
            text = describe(o, h)
            assert text.split(" ", 1)[0].isdigit() and "independent signal" in text, text[:80]
            assert not register_problems(text), (o["id"], register_problems(text))


def test_incumbents_are_admitted_after_the_publication_lag(frozen):
    corpus, _, _ = frozen
    lagged = [e for e in corpus["events"] if e["family"] == "incumbent"]
    assert lagged and all(e["available_by"] > e["date"] for e in lagged)
    assert all(e["available_by"] == e["date"] for e in corpus["events"] if e["family"] != "incumbent")

"""Questions put to the twin: each answer rests on dated statements, and a reading says when it is only a reading."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from ask import analogs, changed, fixture, incumbents, match, moves, prep, team, where_from  # noqa: E402
from backtest import CORPUS, register_problems  # noqa: E402
from pages import Layer  # noqa: E402


def test_the_questions_answer_from_the_synthetic_record():
    layer = fixture()
    assert "the 120 days before had 2" in changed(layer, "NAVSEA", "unmanned systems")
    assert "5 past buy(s) under N00024" in analogs(layer, "N00024-26-R-0001")
    assert "a reading, not a stored tie" in analogs(layer, "N00024-26-RFPREQ-PMS-406-0001"), "a name-only tie says so"
    assert "reading: a follow-on is planned and has slipped or been bridged" in incumbents(layer, "PMS 406")
    assert "offices entered for the first time: PMW 1" in moves(layer, "acme", 90, {})
    assert "\n1. ACME INC: 1 contract(s) under PMS 406; 1 running" in team(layer, ["autonomy"], resolved={})
    assert match(layer, ["autonomy"], [], {}).split("\n")[1].startswith("1. PMS 406")
    assert "questions to ask:" in prep(layer, "PMS 406", "2026-05-01", {}, {})


@pytest.mark.skipif(not CORPUS.exists(), reason="no frozen corpus on disk")
def test_the_questions_run_on_the_frozen_corpus():
    layer = Layer(json.loads(CORPUS.read_text(encoding="utf-8")), roster=[], routes=[])
    assert changed(layer, "NAVWAR", days=30).startswith("What changed inside Naval Information Warfare Systems Command")
    assert "past buy(s)" in analogs(layer, "N00039-23-RFPREQ-PMW-160-0108"), "the ADNS row names its solicitation, so its own notices set the path"
    assert "a reading" not in analogs(layer, "N00039-23-RFPREQ-PMW-160-0108")
    answers = [changed(layer, "NAVSEA", "autonomy"), analogs(layer, "N0003926RE014"), incumbents(layer, "PMW 160"), prep(layer, "PMW 740", pulse={}, dna={}),
               team(layer, ["communications"], "PEO C4I")]
    assert "incumbent contract(s) end within" in answers[2] and all(not register_problems(a) for a in answers), [register_problems(a) for a in answers]


def test_a_statement_is_published_where_its_source_recorded_it():
    """A news body can end on the publisher's copyright link; the page the source recorded is where the statement is."""
    news = {"text": "PEO C4I at WEST 2026 ... must comply with the restrictions shown on https://www.dvidshub.net/about/copyright.",
            "title": "industry_engagement", "family": "organization", "provider": "dvids_navy_units",
            "url": "https://www.dvidshub.net/news/558082/peo-c4i-highlights-industry-engagement"}
    assert where_from(news) == news["url"]
    assert where_from({k: v for k, v in news.items() if k != "url"}).endswith("/about/copyright."), "without a recorded page the text is all there is"

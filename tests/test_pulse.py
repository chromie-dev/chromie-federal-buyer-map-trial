"""Temporal engine, pulse and actions on the frozen corpus: the saved pulse rebuilds from the files alone, every
score recomposes exactly from its named parts, families and not event counts drive it, a
frozen set of events never scores higher later, a forecast date moved later lowers proximity, the
week lists exactly what became available in it, and every action is from the list with evidence."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from backtest import CORPUS, LABELS, shift  # noqa: E402
from people import load_routes  # noqa: E402
from pulse import ACTIONS, ENDS_RE, PULSE, actions, card, cell_events, cells, load_people, rank, recomposes, record_reach, register_problems, score, week, week_text  # noqa: E402
from vocabulary import STAGES  # noqa: E402

pytestmark = pytest.mark.skipif(not (CORPUS.exists() and LABELS.exists() and PULSE.exists()), reason="no frozen pulse on disk")


@pytest.fixture(scope="module")
def frozen():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    saved = json.loads(PULSE.read_text(encoding="utf-8"))
    return corpus, labels, saved


def test_pulse_rebuilds_from_the_frozen_files_alone(frozen):
    corpus, labels, saved = frozen
    ranked = rank(corpus, labels, saved["as_of"])
    assert [c["key"] for c in ranked[:50]] == [c["key"] for c in saved["ranking"]]
    assert [c["score"] for c in ranked[:50]] == [c["score"] for c in saved["ranking"]]
    assert week(corpus, saved["week"]["since"], saved["week"]["until"]) == saved["week"]
    # The build names a meeting's contacts through the page layer's reach and the cell's own forecast row; so does this.
    roster = load_people()
    assert actions(ranked, corpus, saved["as_of"], roster=roster, routes=load_routes(), **record_reach(corpus, saved["as_of"], roster)) == saved["actions"]


def test_every_score_recomposes_from_its_parts_with_an_event_per_family(frozen):
    _, _, saved = frozen
    assert saved["ranking"]
    for c in saved["ranking"]:
        assert recomposes(c), c["key"]
        assert set(c["evidence"]) == set(c["families"]) and all(c["evidence"][f] for f in c["families"]), c["key"]
        assert all(Decimal("0") <= Decimal(v) <= Decimal("1") for v in c["parts"].values())


def test_families_not_events_drive_the_score(frozen):
    corpus, labels, saved = frozen
    cell = next(c for c in cells(corpus, labels) if c["key"] == saved["ranking"][0]["key"])
    events = cell_events(cell, corpus)
    base = score(events, saved["as_of"])
    padded = events + [{**events[-1], "id": f"copy{n}"} for n in range(5)]
    assert score(padded, saved["as_of"])["score"] == base["score"]


def test_a_frozen_cell_never_scores_higher_later(frozen):
    """With no new statement, families, recency and persistence only decay. Proximity may rise once, when a known
    incumbent end comes inside two years: the recompete coming closer is the one thing time adds."""
    corpus, labels, saved = frozen
    wanted = {c["key"] for c in saved["ranking"][:5]}
    for cell in (c for c in cells(corpus, labels) if c["key"] in wanted):
        events = [e for e in cell_events(cell, corpus) if e["available_by"] <= saved["as_of"]]
        series = [score(events, shift(saved["as_of"], 30 * n)) for n in range(0, 40)]
        for was, now in zip(series, series[1:]):
            for part in ("families", "recency", "persistence"):
                assert Decimal(now["parts"][part]) <= Decimal(was["parts"][part]), (cell["key"], part, now["as_of"])
            if Decimal(now["parts"]["proximity"]) > Decimal(was["parts"]["proximity"]):
                ends = [m.group(1) for e in events if e["family"] == "incumbent" for m in [ENDS_RE.search(e["title"])] if m]
                assert any(shift(was["as_of"], 730) < d <= shift(now["as_of"], 730) for d in ends), (cell["key"], now["as_of"])


def test_a_statement_against_lowers_proximity(frozen):
    """A forecast date moved later, a delay, a bridge or a sole-source renewal in the last
    year halves proximity; with every such statement read as neutral the same events score twice the proximity."""
    corpus, labels, saved = frozen
    hit = next((c for c in saved["ranking"] if c["against"]), None)
    if hit is None:
        pytest.skip("no cell with a statement against in the top of the ranking")
    cell = next(c for c in cells(corpus, labels) if c["key"] == hit["key"])
    events = cell_events(cell, corpus)
    calm = [{**e, "slip": False, "polarity": "neutral" if e.get("polarity") == "negative" else e.get("polarity")} for e in events]
    assert Decimal(score(events, saved["as_of"])["parts"]["proximity"]) * 2 == Decimal(score(calm, saved["as_of"])["parts"]["proximity"])
    assert all(x["date"] > shift(saved["as_of"], -365) for x in hit["against"])


def test_every_cell_states_its_stage_and_the_card_reads(frozen):
    corpus, labels, saved = frozen
    for c in saved["ranking"]:
        assert c["stage"] in STAGES + ("shaping", "dormant") and isinstance(c["next"], list) and c["for"] >= 0, c["key"]
    top = saved["ranking"][0]
    cell = next(c for c in cells(corpus, labels) if c["key"] == top["key"])
    events = cell_events(cell, corpus)
    text = card({**cell, **score(events, saved["as_of"])}, events, saved["as_of"], corpus["orgs"])
    assert "Stage:" in text and "For (" in text and "Against (" in text and not register_problems(text), text


def test_the_week_lists_exactly_what_became_available(frozen):
    corpus, _, saved = frozen
    w = saved["week"]
    listed = {r["id"] for slot in w["offices"].values() for rows in slot["changes"].values() for r in rows}
    expected = {e["id"] for e in corpus["events"] if w["since"] < e["available_by"] <= w["until"]}
    assert listed == expected and w["events"] == len(expected)
    assert not register_problems(week_text(w))


def test_every_action_is_from_the_list_and_points_at_events(frozen):
    corpus, _, saved = frozen
    ids = {e["id"] for e in corpus["events"]}
    assert saved["actions"]
    for a in saved["actions"]:
        assert a["type"] in ACTIONS, a
        assert a["evidence"] and set(a["evidence"]) <= ids, a


def test_meetings_name_whom_the_record_ties_to_the_office(frozen) -> None:
    """A meet_office action carries the people the sources tie to the office by as_of, each with the
    document that names them; nobody is named from a later document."""
    corpus, labels, saved = frozen
    meets = [a for a in saved["actions"] if a["type"] == "meet_office"]
    assert meets and all("contacts" in a for a in meets)
    named = [c for a in meets for c in a["contacts"]]
    assert named, "no meeting names a person"
    for c in named:
        assert c["name"] and c["observed_at"] <= saved["as_of"] and c["source"] and c["source_ref"] and c["office"]
    assert all(len(a["contacts"]) <= 3 for a in meets)
    assert all("contacts" not in a for a in saved["actions"] if a["type"] != "meet_office")


def test_meetings_name_the_route_in_and_the_queue_is_ordered(frozen) -> None:
    """A meeting carries the office's routes (requirement side, contracting side, channels), each observed by as_of
    and marked when its observations were checked against the saved file; every action carries its cell's rank, and a watch on a contract carries the day it ends."""
    corpus, labels, saved = frozen
    meets = [a for a in saved["actions"] if a["type"] == "meet_office"]
    assert all("routes" in a for a in meets) and any(r["side"] == "requirement" for a in meets for r in a["routes"])
    assert all(r["observed_at"] <= saved["as_of"] and r["source_url"] and isinstance(r["checked"], bool) for a in meets for r in a["routes"])
    assert all(isinstance(a["priority"], int) and a["priority"] >= 1 for a in saved["actions"])
    assert all(a["by"] and a["why"].endswith(a["by"]) for a in saved["actions"] if a["type"] == "watch_expiration")

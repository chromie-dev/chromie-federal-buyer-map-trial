"""The twin's object pages: every page ends with the sources that speak about the object and when each last spoke."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from backtest import CORPUS, register_problems  # noqa: E402
from pages import Layer  # noqa: E402
from pages import DNA, OWNER_TYPES, cell, guess_trial, office, person, place, sources, vendor  # noqa: E402
from pulse import office_name  # noqa: E402
from pulse import load_people  # noqa: E402

pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="no frozen corpus on disk")


@pytest.fixture(scope="module")
def layer():
    roster = load_people()
    return Layer(json.loads(CORPUS.read_text(encoding="utf-8")), roster), roster


def test_every_page_ends_with_its_sources_and_silent_families(layer):
    lay, roster = layer
    dna = json.loads(DNA.read_text(encoding="utf-8")) if DNA.exists() else {}
    for text in (office(lay, "N00039", dna), vendor(lay, "Leidos"), cell(lay, next(n["key"] for n in lay.needs if n["owner"] == "PMA/PMW 101"))):
        assert "sources speaking about this object:" in text and "families silent:" in text, text[:200]
        assert "&amp;" not in text and "&#" not in text, "HTML entities belong to the feed, not the record"
    who = next(r["name"] for r in roster if len(r["positions"]) >= 3)
    assert f"{who}:" in person(lay, who, roster) and "sources:" in person(lay, who, roster)


def test_sources_count_statements_and_silence(layer):
    lay, _ = layer
    rows = sources([e for e in lay.events if e["family"] == "incumbent"][:50], lay.as_of)
    assert rows and sum(r["statements"] for r in rows) == 50 and all(r["first"] <= r["last"] <= lay.as_of for r in rows)
    assert rows == sorted(rows, key=lambda r: (r["last"], r["source"]), reverse=True)


def test_the_record_holds_no_html_entities():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    bad = [e["id"] for e in corpus["events"] if "&amp;" in e["title"] or "&amp;" in (e.get("vendor") or "")]
    bad += [n["key"] for n in corpus["needs"] if "&amp;" in n["title"]]
    assert not bad, f"{len(bad)} rows carry &amp;: unescape at the source reader, not here ({bad[:3]})"


def test_a_short_name_the_seed_observed_finds_its_organization(layer):
    lay, _ = layer
    assert lay.orgs[lay.org_id("NAVWAR")]["name"] == "Naval Information Warfare Systems Command"
    assert lay.orgs[lay.org_id("PEO C4I")]["name"].startswith("Program Executive Office Command, Control")


def test_a_notice_filed_at_a_contracting_office_is_read_to_a_program_office_with_its_basis(layer):
    lay, _ = layer
    read = [e for e in lay.events if e.get("read_as")]
    assert read and all(lay.orgs[e["filed"]]["org_type"] == "contracting_office" and lay.orgs[e["org"]]["org_type"] in OWNER_TYPES for e in read)
    adns = [e for e in read if "N0003925R9510" in e["text"]]
    assert adns and all(lay.orgs[e["org"]]["acronym"] == "PMW 160" and "N00039-23-RFPREQ-PMW-160-0108" in e["read_as"] for e in adns)
    lines = [place(e, lay.orgs) for e in read]
    assert all("office not stated" in s and "filed at" in s and not register_problems(s) for s in lines), lines[:3]


def test_a_notice_no_record_places_keeps_its_filed_office_and_carries_guesses(layer):
    lay, _ = layer
    guessed = [e for e in lay.events if e.get("guesses")]
    assert guessed and all(lay.orgs[e["org"]]["org_type"] == "contracting_office" and not e.get("read_as") for e in guessed)
    assert all(lay.orgs[oid]["org_type"] in OWNER_TYPES for e in guessed for oid, _, _ in e["guesses"])
    lines = [place(e, lay.orgs) for e in guessed]
    assert all("guessed from the words its records share" in s and not register_problems(s) for s in lines), lines[:3]
    iuss = [e for e in guessed if "IUSS" in e["title"]]
    assert iuss and all(office_name(e["guesses"][0][0], lay.orgs).startswith("PMS 485") for e in iuss), "IUSS is the maritime surveillance office's"


def test_the_guess_is_right_where_the_office_is_known():
    trial = guess_trial(json.loads(CORPUS.read_text(encoding="utf-8")))
    guessed = trial["first"] + trial["top three"] + trial["missed"]
    lead = trial["clear lead, first"] + trial["clear lead, not first"]
    assert guessed >= 100 and trial["first"] >= 0.85 * guessed and trial["clear lead, first"] >= 0.9 * lead, trial

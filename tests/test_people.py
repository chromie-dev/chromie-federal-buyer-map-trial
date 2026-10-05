"""People layer: people.json rebuilds from the saved sources alone, every position points at a
document with a date, one person per e-mail, two people sharing a name with no e-mail and different offices stay
apart, a person the organization memory holds keeps the memory's id, and the SAM.gov point of contact on a notice
that names one office is tied to that office."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from people import PEOPLE, contacts_for, merge, norm_name, seed_people  # noqa: E402
from agency_layers_sql import uid  # noqa: E402

pytestmark = pytest.mark.skipif(not PEOPLE.exists(), reason="people.json not built")
ROLES = {"contract_specialist", "program_manager", "deputy_program_manager", "acquisition_leader", "contracting_leader",
         "contracting_officer", "contract_specialist", "technical_lead", "other"}


@pytest.fixture(scope="module")
def people() -> dict:
    return json.loads(PEOPLE.read_text(encoding="utf-8"))


def test_every_position_points_at_a_dated_document(people: dict) -> None:
    assert people["people"] == len(people["rows"]) >= 150
    for person in people["rows"]:
        assert person["positions"] and person["name"].strip()
        for pos in person["positions"]:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", pos["observed_at"]), pos
            assert pos["source"] and pos["source_ref"] and pos["office"] and pos["org"] == uid("org", pos["office"])
            assert pos["role_type"] in ROLES, pos["role_type"]
            assert 0 < float(pos["confidence"]) <= 1


def test_one_person_per_email_and_names_without_email_split_by_office(people: dict) -> None:
    emails = [e for person in people["rows"] for e in person["emails"]]
    assert len(emails) == len(set(emails)), "an e-mail belongs to two people"
    keyed = [p for p in people["rows"] if p["key"].startswith("name:")]
    assert keyed, "no name-keyed person; the split rule has nothing to hold"
    assert all(len(p["offices"]) == 1 for p in keyed), "a name-only person spans offices"


def test_memory_persons_keep_their_ids(people: dict) -> None:
    seed = seed_people()
    merged = {p["seed_id"] for p in people["rows"] if p["seed_id"]}
    assert merged, "no person merged with the organization memory"
    assert merged <= {v["seed_id"] for v in seed.values()}
    for person in people["rows"]:
        if person["seed_id"]:
            assert person["id"] == uid("person", f"seed:{person['seed_id']}")


def test_sam_points_of_contact_follow_the_office_the_notice_names(people: dict) -> None:
    sam = [pos for person in people["rows"] for pos in person["positions"] if pos["source"] == "sam_gov_site_api"]
    assert len(sam) >= 300 and all(pos["role_type"] == "contract_specialist" for pos in sam)
    offices = {pos["office"] for pos in sam}
    assert "pmw:101" in offices and "contracting:n00039" in offices, offices
    assert all(pos["source_url"].startswith("https://sam.gov/opp/") for pos in sam)


def test_contacts_for_an_office_prefer_it_over_its_parent_then_newest(people: dict) -> None:
    got = contacts_for([uid("org", "pmw:101"), uid("org", "peo:c4i")], people["rows"], limit=5)
    assert got and all(c["office"] == "pmw:101" for c in got[:3])
    assert [c["observed_at"] for c in got] == sorted((c["observed_at"] for c in got), reverse=True) or len({c["office"] for c in got}) > 1
    assert contacts_for([uid("org", "pmw:101")], people["rows"], as_of="2000-01-01") == []


def test_normalisation_rules() -> None:
    assert norm_name("CAPT Raphael R. Castillejo") == norm_name("Castillejo, Raphael") == "raphael castillejo"
    assert norm_name("The Honorable Hung Cao") == "hung cao"
    rows = merge([("A Person", "", {"office": "pmw:120", "org": uid("org", "pmw:120"), "role_type": "other", "raw_title": "", "observed_at": "2026-01-01",
                                   "source": "s", "source_ref": "1", "source_url": "", "confidence": "0.6", "context": ""}),
                  ("A. Person", "", {"office": "pmw:130", "org": uid("org", "pmw:130"), "role_type": "other", "raw_title": "", "observed_at": "2026-01-01",
                                     "source": "s", "source_ref": "2", "source_url": "", "confidence": "0.6", "context": ""})])
    assert len(rows) == 2


def test_news_positions_cite_a_dated_article_that_names_the_person(people: dict) -> None:
    from people import NEWS
    articles = {a["id"]: a for a in json.loads(NEWS.read_text(encoding="utf-8"))["articles"]}
    news_positions = [(person, pos) for person in people["rows"] for pos in person["positions"] if pos["source"] == "news_articles"]
    assert news_positions, "the record's news names at least one change of charge"
    for person, pos in news_positions:
        article = articles[pos["source_ref"]]
        assert article["published"] == pos["observed_at"] and article["url"] == pos["source_url"]
        assert norm_name(person["name"]).split()[-1] in pos["context"].lower(), (person["name"], pos["context"])
        assert pos["role_type"] in {"program_manager", "deputy_program_manager", "acquisition_leader"}
    assert any(pos["office"] == "pmw:770" for _, pos in news_positions), "the PMW 770 change of command is read"
    for change in people["unplaced_changes"]:
        assert change["name"] and change["passage"] and change["url"], change

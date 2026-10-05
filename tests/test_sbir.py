"""Programs family: every saved Navy SBIR/STTR topic is a dated event under an office or command
the organization memory knows, rebuilt from the saved portal pages alone, and loaded as the programs family.

NAVY_DB names the loaded database for the last test; without it that test skips."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from sbir import COMMANDS, DEPARTMENT, OUT, PROVIDER, SINCE, detail_url  # noqa: E402

pytestmark = pytest.mark.skipif(not OUT.exists(), reason="sbir_topics.json not built")
DB = os.environ.get("NAVY_DB")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def one(sql: str) -> str:
    dsn = (f"postgresql://{os.environ.get('PGUSER', 'postgres')}:{os.environ.get('PGPASSWORD', 'postgres')}"
           f"@{os.environ.get('PGHOST', '127.0.0.1')}:{os.environ.get('PGPORT', '54322')}/{DB}")
    return subprocess.run(["psql", dsn, "-At", "-c", sql], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(scope="module")
def topics() -> list[dict]:
    return json.loads(OUT.read_text(encoding="utf-8"))["rows"]


def test_topics_are_navy_dated_and_inside_the_window(topics):
    assert len(topics) >= 800
    for t in topics:
        assert t["component"] == "NAVY" and re.match(r"^(N|DON)", t["code"]), t["code"]
        assert DAY.match(t["pre_release"]) and t["pre_release"] >= SINCE[:4] and t["open"] >= SINCE, (t["code"], t["pre_release"], t["open"])
        assert t["pre_release"] <= t["open"] <= t["close"], t["code"]
        assert t["url"] == detail_url(t["topic_id"]) and t["org"] in set(COMMANDS.values()) | {DEPARTMENT}


def test_offices_named_are_in_the_organization_memory(topics):
    seed = {n["id"] for n in json.loads((ROOT / "research" / "memory" / "organization_seed.json").read_text(encoding="utf-8"))["nodes"]}
    named = [t for t in topics if t["offices"]]
    assert len(named) >= 30, len(named)
    assert all(o in seed for t in named for o in t["offices"])
    # a topic that names an office is filed under it, not the command, when the loader picks the organization
    assert any(t["command"] == "NAVWAR" and t["offices"] for t in named)


def test_every_topic_keeps_its_saved_detail(topics):
    detailed = [t for t in topics if t["path"]]
    assert len(detailed) >= 0.95 * len(topics), (len(detailed), len(topics))
    for t in detailed[:50]:
        assert (ROOT / t["path"]).exists() and len(t["sha256"]) == 64 and t["retrieved_at"]
        assert t["text"], t["code"]


@pytest.mark.skipif(not DB, reason="NAVY_DB names no loaded database")
def test_topics_load_as_programs_events(topics):
    loadable = [t for t in topics if t["pre_release"] and t["code"]]
    assert one(f"select count(*) from public.agency_brain_items where source_provider='{PROVIDER}'") == str(len(loadable))
    assert one(f"select count(*) from public.agency_brain_items where source_provider='{PROVIDER}' and (published_at is null or "
               "primary_organization_id is null or event_type <> 'sbir_topic' or section <> 'mission_priorities')") == "0"
    assert int(one(f"select count(distinct primary_organization_id) from public.agency_brain_items where source_provider='{PROVIDER}'")) >= 4
    assert one(f"select count(*) from public.gov_intelligence_evidence e join public.agency_brain_items i on i.id=e.brain_item_id "
               f"where i.source_provider='{PROVIDER}' and e.source_url like 'https://www.dodsbirsttr.mil/%'") == str(len(loadable))


ENTRY_TYPES = {"sbir_xl", "xtech_competition", "open_topic", "direct_to_phase_ii", "phase_i_or_d2p2", "phase_i", "unknown"}
INSTRUMENTS = {"baa", "cso", "unknown"}


def test_every_topic_states_its_instrument_and_entry_from_the_saved_row(topics):
    """The entry type derives from the code letters, the phase set and the title words, never from a flyer; the phase set
    and the numbers are the saved index row's; a ceiling is the topic's own sentence with a dollar amount, else null."""
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    assert sum(payload["by_entry_type"].values()) == payload["topics"] == len(topics)
    assert sum(payload["by_instrument"].values()) == payload["topics"]
    for t in topics:
        instrument, entry = t["entry_type"].split(":")
        assert instrument == t["instrument"] in INSTRUMENTS and entry == t["entry"] in ENTRY_TYPES, t["code"]
        assert t["entry_basis"] in ("title", "phases", "code", "none") and (t["entry_basis"] == "none") == (entry == "unknown"), t["code"]
        assert isinstance(t["phases"], list) and all(isinstance(p, str) for p in t["phases"]), t["code"]
        assert t["ceiling"] is None or "$" in t["ceiling"], t["code"]
        assert isinstance(t["focus_areas"], list) and t["itar"] in (True, False, None)
        for k in ("qa_open", "qa_close"):
            assert t[k] == "" or DAY.match(t[k]), (t["code"], k)
        if t["code"].startswith("DON26"):
            assert t["solicitation_number"] and t["instrument"] != "unknown", t["code"]
            if "-DV" in t["code"]:
                assert t["entry"] == "direct_to_phase_ii", t["code"]
    assert {"baa:phase_i", "baa:direct_to_phase_ii"} <= set(payload["by_entry_type"]), payload["by_entry_type"]
    if topics[0]["component"] == "NAVY":
        assert "cso:open_topic" in payload["by_entry_type"], "the FY2026 DON26BX open topics"
    assert not any(k in payload["by_entry_type"] for k in ("catalyst", "catapult", "strategic_breakthrough", "prize_to_contract")), "never derived from DSIP"


def test_prerelease_days_are_read_from_the_saved_pages_and_compared_with_the_rule():
    path = OUT.parent / "prerelease_observations.json"
    if not path.exists():
        pytest.skip("prerelease_observations.json not built")
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["rule"] == {"weekday": 2, "ordinal": 1} and d["observations"] == len(d["rows"]) and d["rows"]
    if d["component"] == "NAVY":  # the rule was read off the Navy's FY2026 days; the other components' days are only compared
        fy26 = [r for r in d["rows"] if r["day"] >= "2025-10-01" and r["solicitation_number"].startswith("26.")]
        off = {r["day"] for r in fy26 if not r["matches_rule"]["first_wednesday"]}
        assert fy26 and off <= {"2026-04-13"}, f"the FY2026 days are first Wednesdays but the annual BAA's Monday: {sorted(off)}"
    for r in d["rows"]:
        assert DAY.match(r["day"]) and r["topics"] > 0 and len(r["evidence"]["sha256"]) == 64
        assert set(r["matches_rule"]) == {"weekday", "ordinal", "first_wednesday"}

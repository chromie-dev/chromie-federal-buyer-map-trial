"""From a capability to one letter: the model walks and writes, the checks refuse what the record does not carry, and a
saved run replays from its cassettes; an answer from another agent meets the same checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from backtest import CORPUS, register_problems  # noqa: E402
from outreach import OUT, ROOT as TRIAL, Layer, load_people, outreach, row_details, selfcheck, settled  # noqa: E402
from outreach_compare import selfcheck as compare_selfcheck  # noqa: E402
from navy import selfcheck as navy_selfcheck  # noqa: E402

SAVED = sorted(OUT.glob("[!.]*.json")) if OUT.exists() else []  # an external volume adds ._ shadow files beside each run


def test_the_checks_refuse_what_the_record_does_not_carry():
    assert selfcheck() == 0


def test_an_answer_from_elsewhere_meets_the_same_checks():
    assert compare_selfcheck() == 0


def test_every_platform_command_answers_and_the_server_offers_it():
    assert navy_selfcheck() == 0


@pytest.mark.skipif(not (TRIAL / "datapack" / "lrae_navsea_2026-07").exists(), reason="no NAVSEA release packs")
def test_a_row_a_later_release_dropped_says_so():
    later, earlier = row_details("2026-09-22"), row_details("2026-06-30")
    assert later["row:lrae_navsea_2025-12:195"]["dropped"] == "not in the 2026-07-13 release"
    assert "dropped" not in earlier["row:lrae_navsea_2025-12:195"]  # the as-of date comes before the release that dropped it
    assert "dropped" not in later["N00024-25-RFPREQ-PMS-406-0020"]  # carried by the newest release


@pytest.mark.skipif(not CORPUS.exists() or not SAVED, reason="no frozen corpus or no saved outreach run")
@pytest.mark.parametrize("path", SAVED, ids=[p.stem for p in SAVED])
def test_a_saved_run_replays_and_holds(path):
    saved = json.loads(path.read_text(encoding="utf-8"))
    layer = Layer(json.loads(CORPUS.read_text(encoding="utf-8")), load_people())
    try:
        again = outreach(layer, saved["company"], saved["profile"], saved["budget"], saved["model"], replay_only=True)
    except LookupError:
        pytest.skip("the record moved since this run was made; rerun outreach.py run")
    assert settled(again) == settled(saved)
    assert saved["status"] in ("written", "no fit") and not saved["chain_problems"] and not saved["email_problems"]
    if saved["email"]:
        assert not register_problems(saved["email"]["body"]) and (saved["contacts"] or saved["chain"]["routes"])

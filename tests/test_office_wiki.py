"""A notice no record places, read by a model against the office pages: the reading quotes both sides, is checked
where the office is known, and never moves the notice from the office that filed it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from backtest import CORPUS, register_problems  # noqa: E402
from office_wiki import trial  # noqa: E402
from pages import OWNER_TYPES, READS, Layer, place  # noqa: E402
from reader import flatten  # noqa: E402

pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="no frozen corpus on disk")


def test_a_model_reading_quotes_the_notice_and_leaves_it_where_it_was_filed():
    if not READS.exists():
        pytest.skip("no saved office reads")
    lay = Layer(json.loads(CORPUS.read_text(encoding="utf-8")), [], routes=[])
    read = [e for e in lay.events if e.get("model_read")]
    assert read and all(lay.orgs[e["org"]]["org_type"] == "contracting_office" and not e.get("read_as") for e in read)
    assert all(lay.orgs[e["model_read"][0]]["org_type"] in OWNER_TYPES for e in read)
    assert all(flatten(e["model_read"][1]).lower() in flatten(f"{e['title']} {e['text']}").lower().replace('"', "'") for e in read)
    lines = [place(e, lay.orgs) for e in read]
    assert all("the model reads" in s and not register_problems(s) for s in lines), lines[:3]


def test_the_model_names_the_office_a_notice_named_once_its_names_are_masked():
    try:
        tally = trial(json.loads(CORPUS.read_text(encoding="utf-8")), replay_only=True)["tally"]
    except LookupError:
        pytest.skip("the trial's answers are not saved; run office_wiki.py trial once")
    right, near, wrong = (tally.get(f"model: {k}", 0) for k in ("right", "one level apart", "wrong"))
    answered = right + near + wrong
    assert answered >= 100 and right + near >= 0.9 * answered and wrong <= 0.1 * answered, tally

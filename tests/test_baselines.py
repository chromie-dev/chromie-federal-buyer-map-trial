"""The direction tests recompute from the frozen files alone and keep their invariants: a lower bar fires at least as
often as a higher one, the union rule at least as often as either part, every rate lies in [0, 1], and the periods
partition the outcomes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from backtest import CORPUS, LABELS  # noqa: E402
from baselines import BARS, BASELINES, compute  # noqa: E402

pytestmark = pytest.mark.skipif(not (CORPUS.exists() and LABELS.exists() and BASELINES.exists()), reason="no frozen baselines on disk")


@pytest.fixture(scope="module")
def saved():
    return json.loads(BASELINES.read_text(encoding="utf-8"))


def test_recomputes_from_the_frozen_files_alone(saved):
    fresh = compute(json.loads(CORPUS.read_text(encoding="utf-8")), json.loads(LABELS.read_text(encoding="utf-8")),
                    tuple(saved["horizons"]))
    assert fresh == saved


def test_invariants(saved):
    rec = saved["recall"]
    for h in map(str, saved["horizons"]):
        for lower, higher in zip(BARS, BARS[1:]):
            assert rec[f"bar_{lower}"][h] >= rec[f"bar_{higher}"][h], (lower, higher, h)
        assert rec["either"][h] >= max(rec["forecast_exists"][h], rec["incumbent_ending"][h]), h
        assert rec["bar_1"][h] >= rec["either"][h], "any family fires whenever a forecast or an incumbent does"
    for rule, p in saved["precision"].items():
        assert p["fired"] <= p["cells"] and p["judged"] <= p["fired"] and p["followed"] <= p["judged"], rule
        assert p["value"] is None or 0 <= p["value"] <= 1, rule
    assert sum(v["outcomes"] for v in saved["by_period"].values()) == saved["outcomes"]
    assert sum(v["outcomes"] for v in saved["by_command"].values()) == saved["outcomes"]
    assert "-" not in saved["by_command"], "every outcome names the contracting office that posted it"
    assert sum(c["outcomes"] for c in saved["combinations_at_90_days"]) == saved["outcomes"]
    for c in saved["cell_combinations_at_the_bar"]:
        assert c["followed"] <= c["cells"] and len(c["families"].split("+")) >= 3, c

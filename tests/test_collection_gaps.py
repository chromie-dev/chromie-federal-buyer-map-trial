"""A failed or blocked fetch is a collection gap, never an empty result: one check decides what counts as a kept page,
and the FY2026 sweep helper stops on anything else instead of handing back an empty body."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
sys.path.insert(0, str(ROOT / "research" / "fy2026"))

import recorded  # noqa: E402
from fetch import kept_page  # noqa: E402

PAGE = {"url": "https://example.gov/a", "status": 200, "path": "page.json"}


def test_only_a_kept_200_page_counts():
    assert kept_page(PAGE)
    assert not kept_page({**PAGE, "content_status": "rejected_stub"}), "a firewall or maintenance page answered 200"
    assert not kept_page({**PAGE, "content_status": "over_size_cap"}), "hash kept, bytes not"
    assert not kept_page({**PAGE, "status": 503}) and not kept_page({**PAGE, "status": None, "error": "timeout"})
    assert not kept_page({k: v for k, v in PAGE.items() if k != "path"})


def test_the_sweep_helper_stops_on_a_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(recorded, "ROOT", tmp_path)
    (tmp_path / "page.json").write_text('{"results": []}')
    assert recorded.json_of(PAGE) == {"results": []}, "a real empty answer still reads as empty"
    for gap in ({**PAGE, "content_status": "rejected_stub"}, {**PAGE, "status": 500, "error": "HTTP 500"}):
        with pytest.raises(RuntimeError, match="not collected"):
            recorded.json_of(gap)
    (tmp_path / "page.json").write_text("<html>maintenance</html>")
    with pytest.raises(ValueError):
        recorded.json_of(PAGE)

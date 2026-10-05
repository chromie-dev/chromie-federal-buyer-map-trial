"""The oversight family: every kept finding rests on a passage that is on the saved bytes verbatim,
every report has its cassette, and a replay of the cassettes gives the same file."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from llm import CASSETTES, request_key  # noqa: E402
from news import manifest_rows  # noqa: E402
from oversight import EVENT_TYPES, EVENTS, REGISTRY, document_text, documents, flatten, lint  # noqa: E402

TOOL = ROOT / "research" / "tools" / "oversight.py"


def _events():
    if not EVENTS.exists():
        pytest.skip("research/events/oversight_events.json not built")
    return json.loads(EVENTS.read_text(encoding="utf-8"))


def _saved_text():
    """The one-line form of what the agent read: a GAO webpage reaches it as Markdown, and the verbatim
    rule compares in flatten() form, so the spans are looked for there."""
    return {d["url"]: flatten(document_text(d)[0]) for d in documents(manifest_rows())}


def test_every_kept_span_is_on_the_saved_bytes_verbatim() -> None:
    texts = _saved_text()
    for report in _events()["documents"]:
        assert report["url"] in texts, f"{report['url']} is not a saved report any more"
        for event in report["events"]:
            assert flatten(event["evidence_span"]) in texts[report["url"]], (report["report_number"], event["evidence_span"][:80])
            for field in ("affected_program", "affected_organization", "possible_remediation"):
                if event[field]:
                    assert flatten(event[field]) in texts[report["url"]], (report["report_number"], field, event[field][:80])
            for amount in event["amounts"]:
                assert flatten(amount) in flatten(event["evidence_span"]), (report["report_number"], amount)
            assert event["event_type"] in EVENT_TYPES and 0 <= event["confidence"] <= 1


def test_every_report_has_its_cassette_and_the_lint_is_what_kept_the_events() -> None:
    texts = _saved_text()
    for report in _events()["documents"]:
        cassette = CASSETTES / report["cassette"]
        assert cassette.exists(), report["url"]
        record = json.loads(cassette.read_text(encoding="utf-8"))
        assert record["model"] == report["model"] and record["requested_model"] == _events()["model"]
        kept, dropped = lint(json.loads(record["output_text"])["events"], texts[report["url"]])
        assert len(kept) == len(report["events"]), (report["report_number"], dropped)
        assert dict(sorted(dropped.items())) == report["dropped"], report["report_number"]


def test_replaying_the_cassettes_gives_the_same_file() -> None:
    _events()
    done = subprocess.run([sys.executable, str(TOOL), "extract", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-1500:]


def test_source_authority_is_the_registry_word_for_the_host() -> None:
    keys = {e["source_key"] for e in json.loads(REGISTRY.read_text(encoding="utf-8"))}
    assert {"gao_reports", "oversight_gov_reports"} <= keys
    for report in _events()["documents"]:
        assert report["source_authority"] == "first_party", report["url"]
        assert all(e["source_authority"] == "first_party" for e in report["events"])


def test_the_lint_drops_what_the_text_does_not_carry() -> None:
    text = "The Navy did not recover $2.6 million from the contractor for defective parts."
    good = {"event_type": "audit_finding", "problem": "x", "affected_program": "", "affected_organization": "",
            "possible_remediation": "", "future_acquisition_need": "", "evidence_span": "did not recover $2.6 million from the contractor",
            "amounts": [], "confidence": 0.9}
    kept, dropped = lint([good, dict(good, evidence_span="did not recover 2.6 million dollars"), dict(good, amounts=["$9 million"]),
                          dict(good, affected_program="F-35")], text)
    assert [e["amounts"] for e in kept] == [["$2.6 million"]]
    assert dropped == Counter({"evidence span is not on the saved page verbatim": 1, "a money figure is not in the evidence span": 1,
                               "affected_program is not written so in the text": 1})


def test_the_request_key_pins_model_prompt_and_schema() -> None:
    a = request_key("m", "s", "u", {"type": "object"})
    assert a == request_key("m", "s", "u", {"type": "object"})
    assert a != request_key("m", "s", "u ", {"type": "object"}) and a != request_key("m2", "s", "u", {"type": "object"})

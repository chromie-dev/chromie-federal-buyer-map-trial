"""The remarks family: every kept event rests on a passage that is on the saved bytes verbatim, the
speaker and venue fields are the text's words or blank, every document has its cassette, and a replay
of the cassettes gives the same file."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from llm import CASSETTES  # noqa: E402
from news import manifest_rows  # noqa: E402
from reader import flatten  # noqa: E402
from remarks import AUDIENCES, EVENT_TYPES, EVENTS, VERBATIM, document_text, documents, lint  # noqa: E402

TOOL = ROOT / "research" / "tools" / "remarks.py"


def _events():
    if not EVENTS.exists():
        pytest.skip("research/events/remarks_events.json not built")
    return json.loads(EVENTS.read_text(encoding="utf-8"))


def _saved_text():
    """The one-line form of what the agent read: a webpage reaches it as Markdown, and the verbatim rule
    compares in flatten() form, so the spans are looked for there."""
    return {d["url"]: flatten(document_text(d)[0]) for d in documents(manifest_rows())}


def test_every_kept_span_and_name_is_on_the_saved_bytes_verbatim() -> None:
    texts = _saved_text()
    for doc in _events()["documents"]:
        assert doc["url"] in texts, f"{doc['url']} is not a saved document any more"
        text = texts[doc["url"]]
        for field in ("speaker_name", "speaker_role", "event_name", "event_host"):
            assert not doc[field] or flatten(doc[field]) in text, (doc["title"], field, doc[field])
        assert doc["audience"] in AUDIENCES
        for event in doc["events"]:
            assert flatten(event["evidence_span"]) in text, (doc["title"], event["evidence_span"][:80])
            for field in VERBATIM:
                assert not event[field] or flatten(event[field]) in text, (doc["title"], field, event[field][:80])
            assert event["event_type"] in EVENT_TYPES and 0 <= event["confidence"] <= 1
            assert all(flatten(a) in flatten(event["evidence_span"]) for a in event["amounts"])


def test_one_document_many_events_one_provenance() -> None:
    """A statement naming several capabilities yields several events, each pointing at the same document."""
    data = _events()
    many = [d for d in data["documents"] if len(d["events"]) >= 3]
    assert many, "no document yielded three or more events"
    for doc in many:
        assert len({(doc["url"], doc["sha256"], doc["cassette"])}) == 1
        assert len({(e["event_type"], e["evidence_span"]) for e in doc["events"]}) == len(doc["events"]), (doc["title"], "the same event twice on one passage")


def test_every_document_has_its_cassette_and_the_lint_is_what_kept_the_events() -> None:
    texts = _saved_text()
    data = _events()
    for doc in data["documents"]:
        cassette = CASSETTES / doc["cassette"]
        assert cassette.exists(), doc["url"]
        record = json.loads(cassette.read_text(encoding="utf-8"))
        assert record["model"] == doc["model"] and record["requested_model"] == data["model"]
        kept, dropped = lint(json.loads(record["output_text"])["events"], texts[doc["url"]])
        assert len(kept) == len(doc["events"]), (doc["title"], dropped)
        assert dict(sorted(dropped.items())) == doc["dropped"], doc["title"]


def test_replaying_the_cassettes_gives_the_same_file() -> None:
    _events()
    done = subprocess.run([sys.executable, str(TOOL), "extract", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-1500:]


def test_source_authority_follows_the_kind_and_the_registry() -> None:
    for doc in _events()["documents"]:
        expected = "third_party" if doc["kind"] == "conference" else "first_party"
        assert doc["source_authority"] == expected, (doc["kind"], doc["url"])
        assert all(e["source_authority"] == expected for e in doc["events"])

"""The rules that check a reading agent, shared by every document family (oversight, remarks).

An agent reads one document and states events; these functions decide what of that statement the
saved bytes support. They never add a judgment of their own: a span, a name or a figure is kept when
the flattened text carries it verbatim and dropped otherwise; the source's authority is
the registry's word for the host; `normalized` is the shape two readings are compared in.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from agency import SOURCES  # noqa: E402

REGISTRY = SOURCES / "source_registry.json"  # the layer's own registry: a host's authority is its word for that host
from news import host_of  # noqa: E402
from org_memory_lrae import squash  # noqa: E402

MONEY_RE = re.compile(r"\$\s?\d[\d,.]*\s*(?:billion|million|thousand|B|M|K)?", re.I)
HYPHENS = dict.fromkeys(map(ord, "‐‑‒–"), "-")


def flatten(text: str) -> str:
    """The text both the agent and the rule see: one space, straight quotes, ASCII hyphens, no soft hyphens.
    An IG PDF prints `on‑hand` with a non-breaking hyphen; the model writes it back as `on-hand`, and a
    verbatim rule that saw the two as different would drop a true finding."""
    return squash(text.replace("­", "").translate(HYPHENS))


def flatten_lines(text: str) -> str:
    """flatten() applied line by line: a Markdown rendering keeps its blocks, and inside every line the
    same normalization holds, so `flatten(flatten_lines(md)) == flatten(md)` and the verbatim rule sees
    one text whether it reads the rendering or its one-line form."""
    lines = [flatten(line) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def pdf_text(path: Path) -> str:
    """Reading order, not `-layout`: layout mode interleaves the two columns of a report, so no sentence
    survives whole. A word broken at a line end is joined back before the flattening."""
    done = subprocess.run(["pdftotext", str(path), "-"], capture_output=True)
    raw = done.stdout.decode("utf-8", "replace") if done.returncode == 0 else ""
    return re.sub(r"(\w)-\n(\w)", r"\1\2", raw)


def lint(events: list[dict], text: str, event_types: tuple[str, ...], verbatim_fields: tuple[str, ...]) -> tuple[list[dict], Counter]:
    """Keep the events the text supports: the span verbatim, every named field verbatim when filled, the
    figures inside the span, a known type, a confidence in range. A figure the span carries that the agent
    did not list is listed by the rule, not lost."""
    flat = flatten(text)
    kept, dropped, stated = [], Counter(), set()
    for e in events:
        span = flatten(e.get("evidence_span", ""))
        if not span or span not in flat:
            dropped["evidence span is not on the saved page verbatim"] += 1
            continue
        if e.get("event_type") not in event_types:
            dropped["event type outside the list"] += 1
            continue
        try:
            confident = 0.0 <= float(e.get("confidence", -1)) <= 1.0
        except (TypeError, ValueError):
            confident = False
        if not confident:
            dropped["confidence outside 0..1"] += 1
            continue
        bad = [k for k in verbatim_fields if flatten(e.get(k) or "") and flatten(e[k]) not in flat]
        if bad:
            dropped[f"{bad[0]} is not written so in the text"] += 1
            continue
        if any(flatten(a) not in span for a in e.get("amounts") or []):
            dropped["a money figure is not in the evidence span"] += 1
            continue
        # One passage may state two different things (a funding line and the capability it buys); the same
        # kind of event twice on the same passage is the agent repeating itself.
        if (e["event_type"], span) in stated:
            dropped["a second event of the same kind on the same passage"] += 1
            continue
        stated.add((e["event_type"], span))
        listed = {flatten(a) for a in e.get("amounts") or []}
        found = [flatten(m.group(0)) for m in MONEY_RE.finditer(span)]
        kept.append(dict(e, amounts=sorted(listed | set(found))))
    return kept, dropped


def verbatim(value: str, text: str) -> str:
    """A document-level field (speaker, role, venue) as the agent wrote it, or empty when the text does not carry it."""
    return value if value and flatten(value) in flatten(text) else ""


def authority_of(url: str) -> str:
    """The registry's word for the host, never the model's."""
    entries = json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else []
    host = host_of(url)
    for e in entries:
        if e.get("access_mode") == "webpage" and host_of(e.get("official_url", "")) == host:
            return "first_party" if host.endswith((".gov", ".mil")) else "third_party"
    return "unregistered"


def link(events: list[dict], text: str, name_fields: tuple[str, ...] = ("affected_organization",)) -> list[dict]:
    """Organizations through the memory's aliases: what the event names first, else what the document names."""
    import trace as tracer

    document_orgs = sorted({o["office"] for o in tracer.resolve_offices(text[:20000])})
    out = []
    for e in events:
        words = " ".join([*(e.get(k) or "" for k in name_fields), e["evidence_span"]])
        named = sorted({o["office"] for o in tracer.resolve_offices(words)})
        out.append(dict(e, organizations=named or document_orgs, organizations_from="event" if named else "document"))
    return out


def normalized(events: list[dict], name_field: str = "affected_program") -> list[tuple]:
    return sorted((e["event_type"], flatten(e.get(name_field) or "").lower(), flatten(e["evidence_span"]).lower()) for e in events)


def selfcheck() -> int:
    text = "The Navy did not recover $2.6 million from the contractor for defective parts. NAVSUP agreed to seek restitution."
    good = {"event_type": "audit_finding", "affected_program": "", "affected_organization": "NAVSUP", "possible_remediation": "seek restitution",
            "evidence_span": "did not recover $2.6 million from the contractor", "amounts": [], "confidence": 0.9}
    fields = ("affected_program", "affected_organization", "possible_remediation")
    kept, dropped = lint([good, dict(good, evidence_span="did not recover 2.6 million dollars"), dict(good, affected_organization="Naval Supply Systems Command"),
                          dict(good, amounts=["$9 million"]), dict(good, event_type="rumour"), dict(good, confidence=1.5), dict(good, confidence="high"),
                          dict(good, problem="said again")],
                         text, ("audit_finding",), fields)
    assert len(kept) == 1 and kept[0]["amounts"] == ["$2.6 million"], (kept, dropped)
    assert dropped == Counter({"evidence span is not on the saved page verbatim": 1, "affected_organization is not written so in the text": 1,
                               "a money figure is not in the evidence span": 1, "event type outside the list": 1, "confidence outside 0..1": 2,
                               "a second event of the same kind on the same passage": 1}), dropped
    assert normalized(kept) == [("audit_finding", "", "did not recover $2.6 million from the contractor")]
    assert flatten("on‑hand  quan­tities – “yes”") == 'on-hand quantities - "yes"'
    md = "# Title\n\n\n\nThe  on‑hand   count.\n- item"
    assert flatten_lines(md) == "# Title\n\nThe on-hand count.\n- item" and flatten(flatten_lines(md)) == flatten(md)
    plain = dict(good, evidence_span="the on-hand quantities", affected_organization="", possible_remediation="")
    assert lint([plain], "the on‑hand quantities", ("audit_finding",), fields)[0], "a unicode hyphen must not drop a true span"
    assert verbatim("Adm. Daryl Caudle", "remarks by Adm. Daryl Caudle, Chief") == "Adm. Daryl Caudle" and verbatim("Admiral Caudle", "Adm. Caudle") == ""
    assert authority_of("https://www.example.com/x") == "unregistered"
    print("reader selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(selfcheck())

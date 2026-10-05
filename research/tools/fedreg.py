"""Federal Register documents from the Department of the Navy since FY22, saved page by page from the
public API, read into dated rows the loader emits as documents and, when a document announces one, as an event.

The API answers `conditions[agencies][]=navy-department` oldest first, a thousand documents a page, and
names the following page with `next_page_url` (absent on the last). New documents land on the newest
page, so every page before it is fetched once and the newest is re-taken on every sweep; when it fills,
the re-taken copy names a next page and the sweep follows it. `build` reads the saved pages back and
writes research/events/fedreg_events.json; it never touches the network.

    python research/tools/fedreg.py sweep [--fetch] [--limit N]
    python research/tools/fedreg.py build [--check]
    python research/tools/fedreg.py list
    python research/tools/fedreg.py --selfcheck
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import urllib.parse
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import MANIFEST, ROOT, fetch, kept_page  # noqa: E402
from lrae_package import manifest_rows, saved  # noqa: E402

SINCE = "2021-10-01"
FIELDS = ("document_number", "title", "type", "abstract", "publication_date", "html_url", "agencies",
          "action", "dates", "effective_on", "docket_ids")
from agency import EVENTS as EVENTS_DIR, P, NOTE_TAG  # noqa: E402

# The agency condition comes from the profile: an agency slug where the Register has one, a term search where it has none.
FIRST_PAGE = "https://www.federalregister.gov/api/v1/documents.json?" + urllib.parse.urlencode(
    [*P["fedreg_conditions"], ("conditions[publication_date][gte]", SINCE),
     *[("fields[]", f) for f in FIELDS], ("order", "oldest"), ("per_page", "1000")])
LABEL = P["fedreg_label"]
NAME_RE = re.compile(P["fedreg_name_pattern"]) if P.get("fedreg_name_pattern") else None
EVENTS = EVENTS_DIR / "fedreg_events.json"

# Read against "type|action|title", first match wins; a None match is a dated document, not an event.
# The first page (268 documents, FY22 to 2026-08) is 173 information collections, 18 certificates of
# alternate compliance, some 35 Naval Academy, Marine Corps University and advisory board meetings, some 40
# environmental impact statements and records of decision, 3 Privacy Act systems of records, performance
# review boards and three rules (a regulation removed as duplicative, JAG attorney conduct, the NEPA
# rescission). None of them announces an office, an industry engagement or an acquisition policy, so those
# routine forms are named first and never become events whatever their titles say ("an EIS for the
# realignment of", "a new system of records"). The abstract is not read: a Privacy Act abstract says a
# system "was established", an information collection names a program.
EVENT_TYPES = {
    None: re.compile(r"information collection|comment request|system of records|environmental (?:impact|assessment)"
                     r"|record of decision|alternate compliance|patent|board of visitors|advisory (?:committee|board)"
                     r"|performance review board", re.I),
    "program_created": re.compile(r"\b(?:establish(?:es|ing|ment of)|creat(?:es|ion of)|new)\b[^|]{0,60}\bprogram\b", re.I),
    "reorganization": re.compile(r"\b(?:disestablish\w*|establishment of|renam(?:e|es|ed|ing)\b|redesignat\w*|realign\w*)", re.I),
    "industry_engagement": re.compile(r"\b(?:industry day|industry engagement|request for information|sources sought)\b", re.I),
    # A final or interim rule on how the Navy buys, or a notice of acquisition policy; a proposed rule is not yet a change.
    "strategy_change": re.compile(r"^Rule\|[^|]*\|.*\b(?:acquisition|procurement|contracting)\b"
                                  r"|\bacquisition (?:policy|strategy|regulations?)\b", re.I),
}


def event_type(doc: dict) -> str | None:
    text = f"{doc.get('type') or ''}|{doc.get('action') or ''}|{doc.get('title') or ''}"
    return next((kind for kind, pattern in EVENT_TYPES.items() if pattern.search(text)), None)


def next_page(row: dict) -> str | None:
    return json.loads((ROOT / row["path"]).read_bytes()).get("next_page_url")


def saved_pages(manifest: list[dict]) -> tuple[list[dict], str | None]:
    """The saved pages in API order and the first page not yet saved (None once the newest page is saved)."""
    pages: list[dict] = []
    url = FIRST_PAGE
    while url:
        row = saved(manifest, lambda m, u=url: m.get("url") == u)
        if row is None:
            return pages, url
        pages.append(row)
        url = next_page(row)
    return pages, None


def row_of(doc: dict, page: dict) -> dict:
    kind = event_type(doc)
    title = (doc.get("title") or "").strip()
    abstract = (doc.get("abstract") or "").strip()
    return {
        "claim_key": f"fedreg:{doc['document_number']}", "event_type": kind,
        "published": doc.get("publication_date") or "", "title": title[:200],
        "body": "; ".join(p for p in (doc.get("type"), doc.get("action"), abstract) if p),
        "section": "mission_priorities", "url": doc.get("html_url") or "",
        "sha256": page["sha256"], "retrieved_at": page["retrieved_at"], "path": page["path"],
        # The event is read off the title, so an event quotes the title; a plain document quotes its abstract.
        "excerpt": (title if kind or not abstract else abstract)[:600], "uic": "",
        "data": {"document_number": doc["document_number"], "type": doc.get("type"), "action": doc.get("action"),
                 "agencies": [a.get("name") or a.get("raw_name") for a in doc.get("agencies") or []]},
    }


def events(manifest: list[dict]) -> dict:
    """Every document on the saved pages, once, tagged with the page that carries it."""
    pages, _ = saved_pages(manifest)
    by_key: dict[str, dict] = {}
    for page in pages:
        for doc in json.loads((ROOT / page["path"]).read_bytes()).get("results") or []:
            by_key.setdefault(f"fedreg:{doc['document_number']}", row_of(doc, page))
    return {"source_key": "federal_register", "rows": [by_key[k] for k in sorted(by_key)]}


def dumps(payload: dict) -> str:
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def sweep(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fedreg.py sweep")
    ap.add_argument("--fetch", action="store_true", help="take pages from federalregister.gov; without it, only report what is next")
    ap.add_argument("--limit", type=int, default=50, help="most pages to take in one run")
    args = ap.parse_args(argv)
    pages, missing = saved_pages(manifest_rows())
    url = missing or pages[-1]["url"]  # nothing missing: re-take the newest page, where new documents land
    number = len(pages) + (1 if missing else 0)
    if not args.fetch:
        print(f"{len(pages)} page(s) saved; next to take, page {number}: {url}")
        return 0
    taken = 0
    with MANIFEST.open("a", encoding="utf-8") as handle:
        while url and taken < args.limit:
            row = fetch(url, "direct", None, f"Federal Register{NOTE_TAG}: {LABEL} documents since {SINCE}, page {number}")
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            taken += 1
            print(row.get("status"), row.get("size"), f"page {number}")
            if not kept_page(row):
                break
            url, number = next_page(row), number + 1
            time.sleep(1.0)
    print(f"took {taken} page(s)")
    return 0


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fedreg.py build")
    ap.add_argument("--check", action="store_true", help="exit 1 if the saved file differs from a fresh build")
    args = ap.parse_args(argv)
    payload = events(manifest_rows())
    if NAME_RE:  # a term search: keep the documents whose title, action or abstract names the agency
        before = len(payload["rows"])
        payload["rows"] = [r for r in payload["rows"] if NAME_RE.search(f"{r['title']} {r['body']}")]
        print(f"{before - len(payload['rows'])} document(s) mention the agency only in their text and are left")
    text = dumps(payload)
    typed = Counter(r["event_type"] for r in payload["rows"] if r["event_type"])
    print(f"{len(payload['rows'])} document(s), {sum(typed.values())} event(s) {dict(sorted(typed.items()))}")
    if args.check:
        if not EVENTS.exists() or EVENTS.read_text(encoding="utf-8") != text:
            print(f"{EVENTS.relative_to(ROOT)} differs from a fresh build; run without --check to regenerate", file=sys.stderr)
            return 1
        return 0
    EVENTS.write_text(text, encoding="utf-8")
    print(f"written to {EVENTS.relative_to(ROOT)}")
    return 0


def list_cmd(argv: list[str]) -> int:
    manifest = manifest_rows()
    pages, missing = saved_pages(manifest)
    rows = events(manifest)["rows"]
    print(f"{len(pages)} page(s), {'INCOMPLETE' if missing else 'complete'}; {len(rows)} document(s)"
          + (f", {min(r['published'] for r in rows)} to {max(r['published'] for r in rows)}" if rows else ""))
    for kind, n in sorted(Counter(r["event_type"] or "document (no event)" for r in rows).items()):
        print(f"  {kind}: {n}")
    return 0


def page_bytes(docs: list[dict], next_url: str | None) -> bytes:
    answer = {"count": len(docs), "results": docs}
    if next_url:
        answer["next_page_url"] = next_url
    return json.dumps(answer).encode()


FIXTURE_DOCS = [
    ({"type": "Notice", "action": "60-Day information collection notice.", "title": "Proposed Collection; Comment Request"}, None),
    ({"type": "Notice", "action": "Notice.", "title": "Notice of Intent To Prepare an Environmental Impact Statement for the Realignment of Forces"}, None),
    ({"type": "Notice", "action": "Notice of a new system of records.", "title": "Privacy Act of 1974; System of Records"}, None),
    ({"type": "Notice", "action": "Notice of partially closed meeting.", "title": "Meeting of the U.S. Naval Academy Board of Visitors"}, None),
    ({"type": "Notice", "action": "Notice.", "title": "Disestablishment of the Naval Surface Warfare Center Detachment Norco"}, "reorganization"),
    ({"type": "Notice", "action": "Notice.", "title": "Renaming of the Space and Naval Warfare Systems Command"}, "reorganization"),
    ({"type": "Notice", "action": "Notice of industry day.", "title": "Industry Day for the Next Generation Enterprise Network"}, "industry_engagement"),
    ({"type": "Rule", "action": "Final rule.", "title": "Department of the Navy Acquisition Procedures"}, "strategy_change"),
    ({"type": "Proposed Rule", "action": "Proposed rule.", "title": "Department of the Navy Acquisition Procedures"}, None),
    ({"type": "Rule", "action": "Interim final rule.", "title": "Recission of Procedures for Implementing the National Environmental Policy Act (NEPA)"}, None),
    ({"type": "Notice", "action": "Notice.", "title": "Establishment of the Navy Small Business Innovation Pilot Program"}, "program_created"),
]


def selfcheck() -> int:
    for doc, expected in FIXTURE_DOCS:
        assert event_type(doc) == expected, (doc["title"], event_type(doc), expected)
    assert "conditions%5Bagencies%5D%5B%5D=navy-department" in FIRST_PAGE and FIRST_PAGE.endswith("order=oldest&per_page=1000")

    second = "https://www.federalregister.gov/api/v1/documents.json?page=2&per_page=1000"
    docs = [{"document_number": f"2026-0000{i}", "title": d["title"], "type": d["type"], "action": d["action"],
             "abstract": "An abstract. " * 80 if i % 2 else None, "publication_date": f"2026-01-0{i + 1}",
             "html_url": f"https://www.federalregister.gov/d/2026-0000{i}",
             "agencies": [{"name": "Navy Department", "raw_name": "Department of the Navy"}, {"raw_name": "UNLISTED"}]}
            for i, (d, _) in enumerate(FIXTURE_DOCS[:8])]
    with tempfile.TemporaryDirectory() as tmp:
        one, two = Path(tmp) / "one", Path(tmp) / "two"
        one.write_bytes(page_bytes(docs[:5], second))
        two.write_bytes(page_bytes(docs[4:], None))  # a boundary shift repeats a document on the next page
        # The paths are absolute here; ROOT / absolute stays absolute.
        rows = [{"url": FIRST_PAGE, "status": 200, "path": str(one), "sha256": "aaa", "retrieved_at": "2026-09-22T00:00:00Z"},
                {"url": second, "status": 200, "path": str(two), "sha256": "bbb", "retrieved_at": "2026-09-22T00:00:01Z"}]
        assert saved_pages([]) == ([], FIRST_PAGE), "nothing saved: start at the first page"
        pages, missing = saved_pages(rows[:1])
        assert len(pages) == 1 and missing == second, "a page naming a next page is closed; the next one is taken"
        pages, missing = saved_pages(rows)
        assert len(pages) == 2 and missing is None and pages[-1]["url"] == second, "the last page is the one re-taken"
        assert saved_pages(rows + [{**rows[1], "status": 404}])[0][-1]["sha256"] == "bbb", "a failed re-take keeps the saved page"
        payload = events(rows)
        assert dumps(payload) == dumps(events(rows)) == dumps(events(rows + rows)), "the same saved pages give the same bytes"
    out = payload["rows"]
    assert payload["source_key"] == "federal_register" and len(out) == 8, "one row per document, however many pages carry it"
    assert [r["claim_key"] for r in out] == sorted(r["claim_key"] for r in out)
    assert next(r for r in out if r["claim_key"] == "fedreg:2026-00004")["sha256"] == "aaa", "the first page carrying a document is cited"
    assert [r["event_type"] for r in out] == [e for _, e in FIXTURE_DOCS[:8]]
    first = out[0]
    assert first["body"] == "Notice; 60-Day information collection notice." and first["excerpt"] == first["title"], "no abstract: the title is quoted"
    assert out[1]["excerpt"].startswith("An abstract.") and len(out[1]["excerpt"]) == 600 and out[1]["excerpt"] in out[1]["body"]
    assert out[5]["excerpt"] == out[5]["title"] and out[5]["event_type"], "an event quotes the title it was read from"
    assert first["data"]["agencies"] == ["Navy Department", "UNLISTED"] and first["section"] == "mission_priorities" and first["uic"] == ""
    assert list(first) == ["claim_key", "event_type", "published", "title", "body", "section", "url", "sha256",
                           "retrieved_at", "path", "excerpt", "uic", "data"], "the shape the loader reads"
    print("fedreg selfcheck ok")
    return 0


COMMANDS = {"sweep": sweep, "build": build, "list": list_cmd}

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

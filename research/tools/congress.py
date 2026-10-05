"""Congressional directives about the Navy: the Armed Services committees' NDAA reports and the
Appropriations committees' defense bill reports, FY2021 on, read from the govinfo CRPT collection.

The collection listing (`/collections/CRPT/<since>`, one Congress at a time, a thousand packages a
page, paged by `nextPage`) names every committee report; a package is kept when its title is the
NDAA's or the defense appropriations bill's. Each kept report's text is the package's `htm`
rendition, fetched once. Both endpoints want the api.data.gov key: it rides on the request only and
is taken out of every field of the manifest row before the row is written, so no saved row, file
name or note carries it. `build` reads the saved texts and keeps each paragraph whose directive
sentence names the Navy, found by rule, under the heading GPO prints above the item.

    python research/tools/congress.py sweep [--fetch] [--limit N] [--since YYYY-MM-DD]
    python research/tools/congress.py list
    python research/tools/congress.py build [--check]    # -> research/events/congress_events.json
    python research/tools/congress.py --selfcheck
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import MANIFEST, ROOT, fetch, kept_page  # noqa: E402
from llm import env_value  # noqa: E402
from lrae_package import manifest_rows, saved  # noqa: E402

from agency import EVENTS, P, compiled, NOTE_TAG  # noqa: E402

OUT = EVENTS / "congress_events.json"
LABEL = P["congress_label"]
FIRST_FY = 2021
FIRST_CONGRESS = 116  # the FY2021 reports were filed in 2020
SINCE = "2019-01-01"  # listing start (lastModified); every 116th Congress package was made after it
COLLECTION = "https://api.govinfo.gov/collections/CRPT/"
LISTING = COLLECTION + "{since}T00:00:00Z?pageSize=1000&offsetMark=*&congress={congress}"
# www.govinfo.gov/content/pkg/<id>/html/<id>.htm answered with a redirect to its error page on
# 2026-09-23; the API rendition is the one a saved row (H. Rept. 119-715) proves.
TEXT = "https://api.govinfo.gov/packages/{package_id}/htm"

# The title ends with the act's name, so a Rules Committee report that merely mentions the bill does not match.
TITLES = [(kind, re.compile(pattern)) for kind, pattern in P["committees"]["reports"]]
COMMITTEES = P["committees"]["names"]
PACKAGE_RE = re.compile(r"CRPT-(\d+)([hs])rpt(\d+)")

PAGE_RE = re.compile(r"\[\[Page [^\]]*\]\]")
ITEM_RE = re.compile(r"\((?:\d{1,3}|[a-z]{1,4}|[A-Z])\)\s")
RUNIN_RE = re.compile(r"([A-Z][^.]{2,150}?)\.--")
SENTENCE_END = re.compile(r"[.?!][\"')\]]*\s+(?=[\"(\[]?[A-Z(])")
ABBREV_RE = re.compile(r"(?:\b[A-Z]|\b(?:Sec|Secs|No|Nos|Rept|Cong|Sess|Div|Doc|Mr|Mrs|Ms|Dr|Gen|Adm|Lt|Col|Capt|Cmdr|Pub|Stat|"
                       r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec|St|Inc|Corp|Co|Jr|etc|e\.g|i\.e|vs?))\.$")
DIRECTIVE_RE = re.compile(
    r"\b(?:[Cc]ommittee|[Cc]onferees|[Aa]greement)(?:,?\s+(?:also|further|therefore|strongly|again|hereby|additionally|continues\s+to),?)*"
    r"\s+(direct|require|encourage|recommend|urge)s?\b"
    r"|\b(?:is|are)\s+(?:also\s+|further\s+|hereby\s+|strongly\s+)?(directed|encouraged|urged)\s+to\b")
VERBS = {"direct": "directs", "directed": "directs", "require": "requires", "encourage": "encourages", "encouraged": "encourages",
         "recommend": "recommends", "urge": "urges", "urged": "urges"}
NAVY_RE = compiled(P["congress_pattern"])  # the agency's names, as a directive sentence must name them
MONEY_RE = re.compile(r"\$\s?\d|\b(?:funds|funding|appropriat\w*|budget|million|billion)\b", re.I)


def title_match(title: str) -> tuple[str, int] | None:
    """The kind of report a package title names and its fiscal year, or None when it is not one kept here."""
    text = " ".join(title.upper().split())
    if text.startswith("PROVIDING FOR"):
        return None
    for kind, pattern in TITLES:
        m = pattern.search(text)
        if m and int(m.group(1)) >= FIRST_FY:
            return kind, int(m.group(1))
    return None


def current_congress(today: date) -> int:
    return (today.year - 1789) // 2 + 1


def listing_url(congress: int, since: str = SINCE) -> str:
    return LISTING.format(since=since, congress=congress)


def text_url(package_id: str) -> str:
    return TEXT.format(package_id=package_id)


def scrub(text: str, key: str) -> str:
    """The text with the api_key parameter, and any bare copy of the key, taken out."""
    text = re.sub(r"[?&]api_key=[^&#\s\"]*", "", text)
    return text.replace(key, "") if key else text


def record(handle, row: dict, key: str) -> dict:
    """Write a fetched row to the manifest with the key out of every field (url, fetched_from, final_url, error)."""
    row = {k: scrub(v, key) if isinstance(v, str) else v for k, v in row.items()}
    handle.write(json.dumps(row, sort_keys=True) + "\n")
    handle.flush()
    return row


def listing_body(row: dict) -> dict:
    try:
        return json.loads((ROOT / row["path"]).read_bytes())
    except ValueError:
        return {}


def saved_listing(manifest: list[dict], congress: int, since: str = SINCE) -> tuple[list[dict], bool]:
    """The saved listing pages of one Congress in order and whether the last page is among them."""
    pages: list[dict] = []
    url = listing_url(congress, since)
    while True:
        row = saved(manifest, lambda m, u=url: m.get("url") == u)
        if row is None:
            return pages, False
        pages.append(row)
        url = scrub(listing_body(row).get("nextPage") or "", "")
        if not url:
            return pages, True


def kept(listed: list[dict]) -> list[dict]:
    """The packages of one listing page whose title is an NDAA or defense appropriations report."""
    out = []
    for p in listed:
        match, ident = title_match(p.get("title") or ""), PACKAGE_RE.match(p.get("packageId") or "")
        if match and ident:
            congress, chamber, number = ident.groups()
            out.append({"package_id": p["packageId"], "title": p["title"], "issued": p.get("dateIssued") or "",
                        "kind": match[0], "fiscal_year": match[1], "chamber": chamber,
                        "report": f"{chamber.upper()}. Rept. {congress}-{number}"})
    return out


def packages(manifest: list[dict]) -> list[dict]:
    """Every kept report on the saved listing pages, one per package, sorted by package id."""
    latest = {r["url"]: r for r in manifest if r.get("url", "").startswith(COLLECTION) and kept_page(r)
              and (ROOT / r["path"]).exists()}
    found: dict[str, dict] = {}
    for row in latest.values():
        found.update((p["package_id"], p) for p in kept(listing_body(row).get("packages") or []))
    return [found[k] for k in sorted(found)]


def saved_text(manifest: list[dict], package_id: str) -> dict | None:
    return saved(manifest, lambda m, u=text_url(package_id): m.get("url") == u)


def report_text(raw: bytes) -> str:
    """The report as GPO prints it: the <pre> body of the page, tags dropped, entities decoded."""
    page = raw.decode("utf-8", "replace")
    pre = re.findall(r"<pre[^>]*>(.*?)</pre>", page, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", "\n".join(pre) if pre else page))


def is_heading(lines: list[str], flat: str) -> bool:
    """A short block of its own that is not a sentence or a table row: the line GPO sets above an item."""
    return (len(lines) <= 3 and len(flat) <= 200 and (flat[0].isupper() or flat[0].isdigit())
            and not flat.endswith((".", ",", ";", ":")) and ".." not in flat and not re.search(r"\d,\d{3}$", flat)
            and not DIRECTIVE_RE.search(flat))


def add_block(items: list[list[str]], lines: list[str]) -> None:
    """File one blank-line-separated block as a heading, or as paragraph text, new or continued."""
    if not lines:
        return
    flat = " ".join(" ".join(lines).split())
    last = items[-1] if items and items[-1][0] == "p" else None
    if PAGE_RE.fullmatch(flat):
        if last is not None:
            last[1] += " " + flat  # left in place so an excerpt can stop at it
        return
    broken = last is not None and re.search(r"[a-z,]$", PAGE_RE.sub("", last[1]).rstrip())
    if not broken and is_heading(lines, flat):
        items.append(["h", flat])
        return
    for line in lines:
        text = line.strip()
        if len(line) - len(text) >= 4 and not ITEM_RE.match(text) or not items or items[-1][0] != "p":
            items.append(["p", text])
        else:
            items[-1][1] += " " + text


def paragraphs(text: str) -> list[tuple[str, str]]:
    """(heading, paragraph) in reading order. GPO sets a paragraph with a four-space first line and flush
    continuation lines and a heading on a block of its own, and prints [[Page N]] between blocks, so a
    block that carries on a sentence the page broke is joined back. A run-in heading (`Title.--The
    Committee ...`) heads its paragraph and the ones after it until the next heading."""
    items: list[list[str]] = []
    for block in re.split(r"\n[ \t]*\n", text):
        add_block(items, [line.rstrip() for line in block.splitlines() if line.strip()])
    out, heading = [], ""
    for kind, value in items:
        runin = RUNIN_RE.match(value) if kind == "p" else None
        heading = value if kind == "h" else (" ".join(runin.group(1).split()) if runin else heading)
        if kind == "p":
            out.append((heading, value))
    return out


def sentences(text: str) -> list[str]:
    """Sentences split at . ? ! before a capital, not after an initial or an abbreviation (U.S., H.R., Sec.)."""
    out, start = [], 0
    for end in SENTENCE_END.finditer(text):
        if not ABBREV_RE.search(text[start:end.start() + 1]):
            out.append(text[start:end.end()].strip())
            start = end.end()
    return [s for s in out + [text[start:].strip()] if s]


def directive(para: str) -> tuple[str, str, str] | None:
    """The first sentence that directs and names the Navy: as printed, without page marks, and its verb."""
    for sentence in sentences(para):
        clean = " ".join(PAGE_RE.sub(" ", sentence).split())
        verb = DIRECTIVE_RE.search(clean)
        if verb and NAVY_RE.search(clean):
            return sentence, clean, VERBS[(verb.group(1) or verb.group(2)).lower()]
    return None


def directives(text: str) -> list[dict]:
    """Every Navy directive paragraph of a report in reading order. The excerpt is the directive sentence,
    or its longest stretch between page marks, so it stays verbatim in the whitespace-squashed text."""
    found = []
    for heading, para in paragraphs(text):
        hit = directive(" ".join(para.split()))
        if hit:
            sentence, clean, verb = hit
            found.append({"heading": heading, "body": " ".join(PAGE_RE.sub(" ", para).split()), "verb": verb,
                          "excerpt": max(PAGE_RE.split(sentence), key=len).strip()[:600],
                          "section": "budget" if MONEY_RE.search(clean) else "mission_priorities"})
    return found


def committee(pkg: dict, text: str) -> str:
    """The committee whose report it is; a conference report is the conferees' joint statement."""
    return "Committee of Conference" if "CONFERENCE REPORT" in text[:6000] else COMMITTEES[(pkg["kind"], pkg["chamber"])]


def event(pkg: dict, row: dict, who: str, n: int, found: dict) -> dict:
    return {"claim_key": f"congress:{pkg['package_id']}:{n}", "event_type": "congressional_directive", "published": pkg["issued"],
            "title": (found["heading"] or found["excerpt"])[:200], "body": found["body"], "section": found["section"],
            "url": text_url(pkg["package_id"]), "sha256": row["sha256"], "retrieved_at": row["retrieved_at"], "path": row["path"],
            "excerpt": found["excerpt"], "uic": "",
            "data": {"report": pkg["report"], "committee": who, "fiscal_year": pkg["fiscal_year"], "verb": found["verb"],
                     "heading": found["heading"]}}


def payload(manifest: list[dict]) -> dict:
    """The events file: one row per Navy directive in every saved report, and the reports read."""
    rows, reports = [], []
    for pkg in packages(manifest):
        row = saved_text(manifest, pkg["package_id"])
        if row is None:
            continue
        text = report_text((ROOT / row["path"]).read_bytes())
        found = directives(text)
        who = committee(pkg, text)
        rows += [event(pkg, row, who, n, d) for n, d in enumerate(found, start=1)]
        reports.append({"package_id": pkg["package_id"], "title": pkg["title"], "issued": pkg["issued"], "passages": len(found)})
    return {"source_key": "govinfo_api", "rows": sorted(rows, key=lambda r: r["claim_key"]), "reports": reports}


def list_congress(handle, congress: int, since: str, key: str) -> list[dict]:
    """Take one Congress's listing page by page from the first page."""
    rows, url = [], listing_url(congress, since)
    while url:
        note = f"govinfo CRPT listing{NOTE_TAG}: {congress}th Congress, packages modified since {since}, page {len(rows) + 1}"
        row = record(handle, fetch(f"{url}&api_key={key}", "direct", None, note), key)
        rows.append(row)
        print(row.get("status"), row.get("size"), f"listing {congress}th Congress page {len(rows)}")
        time.sleep(1.0)
        url = scrub(listing_body(row).get("nextPage") or "", key) if kept_page(row) else ""
    return rows


def sweep(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="congress.py sweep")
    ap.add_argument("--fetch", action="store_true", help="take the listing and the missing reports from govinfo; without it, only report what is missing")
    ap.add_argument("--limit", type=int, default=100, help="most report texts to take in one run")
    ap.add_argument("--since", default=SINCE, help="listing start: packages govinfo modified on or after this day")
    args = ap.parse_args(argv)
    key = env_value("DATA_GOV_API_KEY") if args.fetch else ""
    if args.fetch and not key:
        print("DATA_GOV_API_KEY is not set in the environment or the local env files", file=sys.stderr)
        return 1
    manifest = manifest_rows()
    current = current_congress(date.today())
    taken = missing = 0
    with MANIFEST.open("a", encoding="utf-8") as handle:
        for congress in range(FIRST_CONGRESS, current + 1):
            pages, complete = saved_listing(manifest, congress, args.since)
            # govinfo posts reports weeks after filing, so the two latest Congresses are listed again every run
            if not args.fetch or (complete and congress < current - 1):
                print(f"listing {congress}th Congress: {len(pages)} page(s) saved, {'complete' if complete else 'INCOMPLETE'}")
                continue
            manifest += list_congress(handle, congress, args.since, key)
        for pkg in packages(manifest):
            if saved_text(manifest, pkg["package_id"]) is not None:
                continue
            missing += 1
            if not args.fetch or taken >= args.limit:
                print(f"missing {pkg['report']} {text_url(pkg['package_id'])}")
                continue
            note = f"govinfo{NOTE_TAG}: {pkg['report']}, {pkg['title']} (FY{pkg['fiscal_year']})"
            row = record(handle, fetch(f"{text_url(pkg['package_id'])}?api_key={key}", "direct", None, note), key)
            taken += 1
            print(row.get("status"), row.get("size"), pkg["report"], pkg["package_id"])
            time.sleep(1.0)
    print(f"{missing} report text(s) were missing; took {taken}")
    return 0


def list_cmd(argv: list[str]) -> int:
    manifest = manifest_rows()
    pkgs = packages(manifest)
    have = 0
    for pkg in pkgs:
        row = saved_text(manifest, pkg["package_id"])
        have += row is not None
        print(f"{'saved  ' if row else 'missing'} {pkg['report']:<16} {pkg['issued']} FY{pkg['fiscal_year']} {pkg['title'][:80]}")
    print(f"{len(pkgs)} report(s) listed, {have} saved, {len(pkgs) - have} missing")
    return 0


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="congress.py build")
    ap.add_argument("--check", action="store_true", help="fail if the saved file would change")
    args = ap.parse_args(argv)
    data = payload(manifest_rows())
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    for r in data["reports"]:
        print(f"{r['passages']:4d}  {r['package_id']:<18} {r['issued']}  {r['title'][:70]}")
    print(f"{len(data['reports'])} report(s), {len(data['rows'])} {P['short'].removeprefix('U.S. ')} directive(s)")
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("congress events differ from the saved file", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    return 0


FIXTURE = b"""<html>
<head>
<title> - NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR 2025</title>
</head>
<body><pre>
[House Report 118-529]
[From the U.S. Government Publishing Office]

                       ITEMS OF SPECIAL INTEREST

Army Ground Vehicle Sustainment

    The committee directs the Secretary of the Army to provide a
briefing to the House Committee on Armed Services by March 1, 2025, on
the sustainment of ground vehicles.

Unmanned Surface Vessel Test &amp; Evaluation

    The committee is aware of the U.S. Navy's plans for the medium
unmanned surface vessel. Therefore, the committee directs the
Secretary of the Navy to provide a briefing to the House Committee on
Armed Services by December 1, 2024, on the test plan of PMS 406,

[[Page 112]]

including:
    (1) the schedule; and
    (2) the cost.

    Ship depot maintenance.--The committee recommends an additional
$50,000,000 for Operation and Maintenance, Navy, for ship depot
maintenance.
</pre></body></html>"""

LISTED = [{"packageId": "CRPT-118hrpt529", "dateIssued": "2024-05-31",
           "title": "SERVICEMEMBER QUALITY OF LIFE IMPROVEMENT AND NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR 2025"},
          {"packageId": "CRPT-118srpt81", "dateIssued": "2023-07-27", "title": "DEPARTMENT OF DEFENSE APPROPRIATIONS BILL, 2024"},
          {"packageId": "CRPT-118hrpt125", "dateIssued": "2023-06-30", "title": "NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR 2024"},
          {"packageId": "CRPT-118hrpt559", "dateIssued": "2024-06-25",
           "title": "PROVIDING FOR CONSIDERATION OF THE BILL (H.R. 8774) MAKING APPROPRIATIONS FOR THE DEPARTMENT OF DEFENSE FOR THE FISCAL YEAR ENDING SEPTEMBER 30, 2025, AND FOR OTHER PURPOSES"},
          {"packageId": "CRPT-116srpt48", "dateIssued": "2019-06-11", "title": "NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR 2020"}]


def selfcheck() -> int:
    # the title filter, on titles the 118th and 119th Congress listings carry
    assert title_match("NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR 2027") == ("ndaa", 2027)
    assert title_match("SERVICEMEMBER QUALITY OF LIFE IMPROVEMENT AND NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR 2025") == ("ndaa", 2025)
    assert title_match("DEPARTMENT OF DEFENSE APPROPRIATIONS BILL, 2024") == ("appropriations", 2024)
    assert title_match(LISTED[3]["title"]) is None, "a Rules Committee report is not the committee's own"
    assert title_match("REPORT ON THE ACTIVITIES of the COMMITTEE ON ARMED SERVICES for the ONE HUNDRED EIGHTEENTH CONGRESS") is None
    assert title_match("NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR 2020") is None, "FY2021 on"
    assert current_congress(date(2026, 9, 23)) == 119 and current_congress(date(2021, 3, 1)) == 117

    # the key never reaches a recorded row
    sink = io.StringIO()
    url = listing_url(118)
    row = record(sink, {"url": f"{url}&api_key=abc123KEY", "fetched_from": f"{url}&api_key=abc123KEY",
                        "final_url": f"{url}&api_key=abc123KEY", "note": "listing", "status": 200}, "abc123KEY")
    line = sink.getvalue()
    assert "abc123KEY" not in line and not re.search(r"api_key=[^&\"\s]", line), "a recorded row carries no key"
    assert row["url"] == row["fetched_from"] == row["final_url"] == url
    assert scrub(text_url("CRPT-118hrpt529") + "?api_key=abc123KEY", "abc123KEY") == text_url("CRPT-118hrpt529")

    # directive finding: the Navy directive kept across a page break, the Army one dropped, headings recovered
    found = directives(report_text(FIXTURE))
    assert len(found) == 2, found
    assert found[0]["heading"] == "Unmanned Surface Vessel Test & Evaluation" and found[0]["verb"] == "directs"
    assert found[0]["section"] == "mission_priorities" and "[[Page" not in found[0]["body"] and found[0]["body"].endswith("(2) the cost.")
    assert found[0]["excerpt"].startswith("Therefore, the committee directs the Secretary of the Navy") and found[0]["excerpt"].endswith("PMS 406,")
    squashed = " ".join(report_text(FIXTURE).split())
    assert all(f["excerpt"] in squashed for f in found), "the excerpt is verbatim"
    assert found[1]["heading"] == "Ship depot maintenance" and found[1]["verb"] == "recommends" and found[1]["section"] == "budget"
    assert not any("Army" in f["body"] for f in found), "a directive to the Army is not a Navy passage"

    # deterministic: the same saved inputs in any manifest order give the same file, rows sorted by claim key
    with tempfile.TemporaryDirectory() as tmp:
        listing, text = Path(tmp) / "listing", Path(tmp) / "report"
        listing.write_text(json.dumps({"count": len(LISTED), "nextPage": None, "packages": LISTED}), encoding="utf-8")
        text.write_bytes(FIXTURE)
        stamp = {"status": 200, "sha256": "abc", "retrieved_at": "2026-09-23T00:00:00Z"}
        manifest = [dict(stamp, url=url, path=str(listing)), dict(stamp, url=text_url("CRPT-118hrpt529"), path=str(text)),
                    dict(stamp, url=text_url("CRPT-118srpt81"), path=str(text))]
        first = json.dumps(payload(manifest), indent=1, ensure_ascii=False)
        again = json.dumps(payload(manifest[::-1]), indent=1, ensure_ascii=False)
        assert saved_listing(manifest, 118) == ([manifest[0]], True)
        assert [p["package_id"] for p in packages(manifest)] == ["CRPT-118hrpt125", "CRPT-118hrpt529", "CRPT-118srpt81"]
    assert first == again, "a rebuild is byte-identical"
    data = json.loads(first)
    keys = [r["claim_key"] for r in data["rows"]]
    assert keys == sorted(keys) == ["congress:CRPT-118hrpt529:1", "congress:CRPT-118hrpt529:2", "congress:CRPT-118srpt81:1", "congress:CRPT-118srpt81:2"]
    assert [r["passages"] for r in data["reports"]] == [2, 2], "a listed report with no saved text is not read"
    assert data["rows"][0]["data"] == {"report": "H. Rept. 118-529", "committee": "House Armed Services Committee", "fiscal_year": 2025,
                                       "verb": "directs", "heading": "Unmanned Surface Vessel Test & Evaluation"}
    assert data["rows"][2]["data"]["committee"] == "Senate Appropriations Committee" and data["rows"][2]["published"] == "2023-07-27"
    print("congress selfcheck ok")
    return 0


COMMANDS = {"sweep": sweep, "list": list_cmd, "build": build}

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

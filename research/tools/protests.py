#!/usr/bin/env python3
"""GAO bid protests against the Department of the Navy, from GAO's public docket, as dated `protest` events.

The docket search (filtered to the Navy, twenty cases a page) lists each case's protester, solicitation number,
sub-agency, file number and status for the last twelve months; the case page adds the filed date, the due date,
the outcome and the decision date. gao.gov refuses this address, so both come through context.dev.
`watch` re-takes the listing every run, takes the case page of every case not yet saved, and re-takes an open
case's page once it is RETAKE_DAYS old so the outcome lands. A closed case is not taken again. `build` reads the
saved pages into research/events/protest_events.json, the shape the loader's emit_records reads: one row per case, dated
the day it was filed, placed by its solicitation number.

    python research/tools/protests.py watch [--fetch] [--limit N]
    python research/tools/protests.py build [--check]
    python research/tools/protests.py list
    python research/tools/protests.py --selfcheck
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import ROOT  # noqa: E402
from lrae_package import manifest_rows, url_index  # noqa: E402
from context_fetch import fetch as hosted  # noqa: E402

from agency import EVENTS as EVENTS_DIR, P  # noqa: E402

EVENTS = EVENTS_DIR / "protest_events.json"
LISTING = P["protests"]["listing"]  # the docket search filtered to the agency, as the profile states it
CASE = "https://www.gao.gov/docket/{file}"
AGENCY = P["protests"]["agency"]
RETAKE_DAYS = 14
MAX_PAGES = P["protests"]["max_pages"]  # the docket holds twelve months; the Navy files a few hundred cases a year
UIC_RE = re.compile(rf"^({P['agency']['office_code_re']})\d{{2}}[A-Z]")  # a solicitation number: office code, fiscal year, type letter
TOPIC_RE = re.compile(P["topic_re"])  # an SBIR/STTR topic protested in place of a solicitation (the whole number, fullmatch)
TEASER = "node--type-bid-protest-docket node--view-mode-teaser-search"
LABELLED = re.compile(r'<(?:h2|header) class="field__label">([^<]+)</(?:h2|header)>\s*<div class="field__item">(.*?)</div>', re.S)
# The fields a case page states that the row keeps; the attorney's name is not one of them.
KEPT = {"Protester": "protester", "Solicitation Number": "solicitation", "Agency": "agency", "File number": "file",
        "Outcome": "outcome", "Decision Date": "decision_date", "Filed Date": "filed", "Due Date": "due", "Case Type": "case_type"}


def text_of(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def listing_url(page: int) -> str:
    return LISTING.format(page=page)


def docket_taken() -> bool:
    """Whether the agency's docket listing was ever saved. Without it an empty protest file means not collected, not
    that the agency's awards drew no protests."""
    return listing_url(0) in url_index(manifest_rows())


def listing_rows(body: bytes) -> list[dict]:
    """Every case on one docket search page."""
    rows = []
    for chunk in body.decode("utf-8", "replace").split(TEASER)[1:]:
        link = re.search(r'href="/docket/([^"]+)"', chunk)
        solicitation = re.search(r'field--name-field-solicitation-number[^>]*>([^<]*)<', chunk)
        agency = re.search(r'teaser-search--agency">.*?<div class="field__item">(.*?)</div>', chunk, re.S)
        status = re.search(r'teaser-search--status">.*?<div class="field__item">(.*?)</div>', chunk, re.S)
        heading = re.search(r'<h3 class="heading"><a[^>]*>(.*?)</a>', chunk, re.S)
        if not link:
            continue
        sol = text_of(solicitation.group(1)) if solicitation else ""
        name = text_of(heading.group(1)) if heading else ""
        rows.append({"file": link.group(1).upper(), "protester": name[: -len(f" ({sol})")] if sol and name.endswith(f"({sol})") else name,
                     "solicitation": sol, "agency": text_of(agency.group(1)) if agency else "",
                     "status": text_of(status.group(1)) if status else ""})
    return rows


def last_page(body: bytes) -> int:
    return max([int(n) for n in re.findall(r"[?&](?:amp;)?page=(\d+)", body.decode("utf-8", "replace"))] or [0])


def case_fields(body: bytes) -> dict:
    """The labelled fields of one case page; dates as YYYY-MM-DD."""
    out = {}
    for label, value in LABELLED.findall(body.decode("utf-8", "replace")):
        key = KEPT.get(label.strip())
        if key:
            stamp = re.search(r'<time datetime="(\d{4}-\d{2}-\d{2})', value)
            out[key] = stamp.group(1) if stamp else text_of(value)
    return out


def closed(fields: dict) -> bool:
    return bool(fields.get("decision_date") or fields.get("outcome"))


def cases(index: dict[str, dict]) -> dict[str, dict]:
    """Every case the saved listing pages name, by file number, with the listing's fields."""
    found: dict[str, dict] = {}
    for page in range(MAX_PAGES):
        row = index.get(listing_url(page))
        if row is None:
            break
        for case in listing_rows((ROOT / row["path"]).read_bytes()):
            found.setdefault(case["file"], case)
    return found


def due(index: dict[str, dict], listed: dict[str, dict], today: date) -> list[str]:
    """The case pages to take: never saved, or open and RETAKE_DAYS old."""
    out = []
    for file in sorted(listed):
        row = index.get(CASE.format(file=file.lower()))
        if row is None:
            out.append(CASE.format(file=file.lower()))
            continue
        age = (today - datetime.strptime(row["retrieved_at"][:10], "%Y-%m-%d").date()).days
        if not closed(case_fields((ROOT / row["path"]).read_bytes())) and age >= RETAKE_DAYS:
            out.append(CASE.format(file=file.lower()))
    return out


def watch(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="protests.py watch")
    ap.add_argument("--fetch", action="store_true", help="take pages through context.dev; without it, only report what is due")
    ap.add_argument("--limit", type=int, default=200, help="most case pages to take in one run")
    args = ap.parse_args(argv)
    if not args.fetch:
        index = url_index(manifest_rows())
        print(f"listing re-taken every run; {len(due(index, cases(index), date.today()))} case page(s) due")
        return 0
    if hosted([listing_url(0)]):
        return 1
    index = url_index(manifest_rows())
    first = index.get(listing_url(0))
    if first is None:
        print("the first docket page did not save")
        return 1
    top = min(last_page((ROOT / first["path"]).read_bytes()), MAX_PAGES - 1)
    if top and hosted([listing_url(p) for p in range(1, top + 1)]):
        return 1
    index = url_index(manifest_rows())
    todo = due(index, cases(index), date.today())[: args.limit]
    print(f"{len(todo)} case page(s) to take")
    return hosted(todo) if todo else 0


def rows(index: dict[str, dict]) -> list[dict]:
    """One row per saved Navy case page that states a filed date. Read from the case pages, not the listing: the
    listing holds twelve months, so a case that has left it keeps its row."""
    out, listed, prefix = [], cases(index), CASE.format(file="")
    for url, page in sorted(index.items()):
        if not url.startswith(prefix):
            continue
        file = url[len(prefix):].upper()
        f = {"protester": "", "solicitation": "", "agency": "", "status": listed.get(file, {}).get("status", ""),
             **case_fields((ROOT / page["path"]).read_bytes())}
        if not f.get("filed") or not f.get("agency", "").startswith(AGENCY):
            continue
        sub = f["agency"].split(":", 1)[1].strip() if ":" in f["agency"] else ""
        uic = UIC_RE.match(f["solicitation"].replace("-", "").upper())
        state = f.get("outcome") or f.get("status") or "open"
        excerpt = (f"Protester: {f['protester']}; Solicitation Number: {f['solicitation']}; Agency: {f['agency']}; "
                   f"File number: {file}; Filed Date: {f['filed']}" + (f"; Outcome: {f['outcome']}" if f.get("outcome") else ""))
        out.append({"claim_key": f"protest:{file}", "event_type": "protest", "published": f["filed"],
                    "title": f"Protest {file} by {f['protester']} on solicitation {f['solicitation']}: {state}"[:200],
                    "body": excerpt + (f"; Decision Date: {f['decision_date']}" if f.get("decision_date") else "")
                            + (f"; Due Date: {f['due']}" if f.get("due") else "") + (f"; {f['case_type']}" if f.get("case_type") else ""),
                    "section": "procurement_patterns", "url": page["url"], "sha256": page["sha256"],
                    "retrieved_at": page["retrieved_at"], "path": page["path"], "excerpt": excerpt[:600],
                    "uic": uic.group(1) if uic else "",
                    "data": {"file_number": file, "protester": f["protester"], "solicitation": f["solicitation"], "sub_agency": sub,
                             "outcome": f.get("outcome", ""), "decision_date": f.get("decision_date", ""), "due_date": f.get("due", ""),
                             "case_type": f.get("case_type", ""), "topic_code": f["solicitation"] if TOPIC_RE.fullmatch(f["solicitation"]) else ""}})
    return out


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="protests.py build")
    ap.add_argument("--check", action="store_true", help="exit 1 when the file would change")
    args = ap.parse_args(argv)
    found = rows(url_index(manifest_rows()))
    text = json.dumps({"source_key": "gao_bid_protests", "rows": found}, indent=1, ensure_ascii=False) + "\n"
    old = EVENTS.read_text(encoding="utf-8") if EVENTS.exists() else ""
    print(f"{len(found)} protest(s)")
    if text == old:
        print(f"{EVENTS.name} unchanged")
        return 0
    if args.check:
        print(f"{EVENTS.name} would change; run `protests.py build`")
        return 1
    EVENTS.write_text(text, encoding="utf-8")
    print(f"{EVENTS.name} written")
    return 0


def list_cmd(argv: list[str]) -> int:
    index = url_index(manifest_rows())
    listed = cases(index)
    have = sum(1 for f in listed if CASE.format(file=f.lower()) in index)
    print(f"{len(listed)} case(s) listed, {have} case page(s) saved, {len(due(index, listed, date.today()))} due")
    return 0


LISTING_FIXTURE = f"""<a href="?agency=Department%20of%20the%20Navy&amp;page=3">4</a>
<article class="node {TEASER}"><a href="/docket/b-424516.3" rel="bookmark">Bid Protest Docket: Closed</a>
<h3 class="heading"><a href="/docket/b-424516.3">Patriot Contract Services, LLC (N3220524R4070)</a></h3>
<div class="field field--name-field-solicitation-number field--type-string field--label-hidden field__item">N3220524R4070</div>
<div class="teaser-search--agency"> <span>Agency:</span> <div class="field__item"> <span>Department of the Navy</span> : <span>Military Sealift Command</span> </div> </div>
<div class="teaser-search--status"> <span>Status:</span> <div class="field__item"> Closed </div> </div></article>
<article class="node {TEASER}"><h3 class="heading"><a href="/docket/b-424834.1">KAUSAR INDUSTRIES INC. dba A&amp;R Box (N0018926QL376/RFQ1834805)</a></h3>
<a href="/docket/b-424834.1">x</a><div class="field field--name-field-solicitation-number field__item">N0018926QL376/RFQ1834805</div>
<div class="teaser-search--agency"> <div class="field__item"> <span>Department of the Navy</span> : <span>Naval Supply Systems Command</span> </div> </div>
<div class="teaser-search--status"> <div class="field__item"> Case Currently Open </div> </div></article>""".encode()
CASE_FIXTURE = b"""<h2 class="field__label">Protester</h2> <div class="field__item">Patriot Contract Services, LLC</div>
<h2 class="field__label">Solicitation Number</h2> <div class="field__item">N3220524R4070</div>
<header class="field__label">Agency</header> <div class="field__item"> <span>Department of the Navy</span> : <span>Military Sealift Command</span> </div>
<header class="field__label">File number</header> <div class="field__item"> B-424516.3 </div>
<h2 class="field__label">Outcome</h2> <div class="field__item">Denied</div>
<h2 class="field__label">Decision Date</h2> <div class="field__item"> <time datetime="2026-09-17T13:00:00Z">Sep 17, 2026</time> </div>
<h2 class="field__label">Filed Date</h2> <div class="field__item"> <time datetime="2026-07-13T13:00:00Z">Jul 13, 2026</time> </div>
<h2 class="field__label">GAO Attorney</h2> <div class="field__item">A. Person</div>"""


def selfcheck() -> int:
    import tempfile
    listed = listing_rows(LISTING_FIXTURE)
    assert [c["file"] for c in listed] == ["B-424516.3", "B-424834.1"]
    assert listed[0]["protester"] == "Patriot Contract Services, LLC" and listed[0]["solicitation"] == "N3220524R4070"
    assert listed[1]["protester"] == "KAUSAR INDUSTRIES INC. dba A&R Box", "the solicitation in the heading is cut, the entity unescaped"
    assert listed[0]["agency"] == "Department of the Navy : Military Sealift Command" and listed[1]["status"] == "Case Currently Open"
    assert last_page(LISTING_FIXTURE) == 3
    fields = case_fields(CASE_FIXTURE)
    assert fields["filed"] == "2026-07-13" and fields["decision_date"] == "2026-09-17" and fields["outcome"] == "Denied"
    assert "A. Person" not in json.dumps(fields), "the attorney's name is not kept"
    assert closed(fields) and not closed({"filed": "2026-07-13"})

    with tempfile.TemporaryDirectory() as tmp:
        lst, case = Path(tmp) / "listing", Path(tmp) / "case"
        lst.write_bytes(LISTING_FIXTURE)
        case.write_bytes(CASE_FIXTURE)
        index = {listing_url(0): {"url": listing_url(0), "path": str(lst), "retrieved_at": "2026-09-23T00:00:00Z", "sha256": "l"},
                 CASE.format(file="b-424516.3"): {"url": CASE.format(file="b-424516.3"), "path": str(case),
                                                   "retrieved_at": "2026-01-01T00:00:00Z", "sha256": "c"}}
        assert due(index, cases(index), date(2026, 9, 23)) == [CASE.format(file="b-424834.1")], "a closed case is not re-taken; a new one is"
        out = rows(index)
    assert len(out) == 1 and out[0]["claim_key"] == "protest:B-424516.3" and out[0]["published"] == "2026-07-13"
    assert out[0]["uic"] == "N32205" and out[0]["data"]["sub_agency"] == "Military Sealift Command" and out[0]["data"]["outcome"] == "Denied"
    assert out[0]["title"].endswith(": Denied") and out[0]["event_type"] == "protest"
    assert not UIC_RE.match("N254122") and TOPIC_RE.fullmatch("N254-122") and UIC_RE.match("N0018926QL376").group(1) == "N00189"
    print("protests selfcheck ok")
    return 0


COMMANDS = {"watch": watch, "build": build, "list": list_cmd}

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

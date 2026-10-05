"""Grants and cooperative agreements the agency awards, from USAspending's award search, saved page by page and
read into dated award rows the loader emits as records.

FPDS carries contracts only; an agency that also awards assistance (DARPA's Contracts Management Office "enters
into contracts, grants, cooperative agreements, and other transactions") has those awards on USAspending alone.
The search answers only a POST, so each saved page keeps its query (`request_body`) in the documents ledger.
Pages run oldest start date first, 100 awards a page; every page before the last is taken once and the last is
re-taken on every sweep, where new awards land. `build` reads the saved pages back and writes
events/assistance_awards.json; it never touches the network.

    python research/tools/assistance.py sweep [--fetch] [--limit N]
    python research/tools/assistance.py build [--check]
    python research/tools/assistance.py --selfcheck
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import MANIFEST, ROOT, fetch, kept_page  # noqa: E402
from lrae_package import manifest_rows, saved  # noqa: E402
from agency import EVENTS as EVENTS_DIR, P, NOTE_TAG  # noqa: E402

SEARCH = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
SINCE = "2019-10-01"
# Block, formula and project grants and cooperative agreements: USAspending's assistance award type codes 02 to 05.
AWARD_TYPES = ["02", "03", "04", "05"]
FIELDS = ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Start Date", "End Date", "Description",
          "Awarding Agency", "Awarding Sub Agency", "Award Type", "generated_internal_id", "Assistance Listings"]
LIMIT = 100
# The awarding filter: the profile's subtier, or its toptier where the layer covers a whole department.
TIER = P["agency"].get("assistance_tier", "subtier")
EVENTS = EVENTS_DIR / "assistance_awards.json"


def fiscal_year_end(today: date) -> str:
    return f"{today.year + (today.month >= 10)}-09-30"


def request(page: int, today: date | None = None) -> dict:
    # ponytail: the window ends with the current fiscal year, so on 1 October every page is asked for again; that
    # re-take is also what picks up an award reported late with an early start date, which shifts later rows.
    end = fiscal_year_end(today or date.today())
    return {"filters": {"award_type_codes": AWARD_TYPES, "time_period": [{"start_date": SINCE, "end_date": end}],
                        "agencies": [{"type": "awarding", "tier": TIER, "name": P["agency"][f"{TIER}_name"]}]},
            "fields": FIELDS, "page": page, "limit": LIMIT, "sort": "Start Date", "order": "asc"}


def has_next(row: dict) -> bool:
    return bool(json.loads((ROOT / row["path"]).read_bytes()).get("page_metadata", {}).get("hasNext"))


def saved_pages(manifest: list[dict], today: date | None = None) -> tuple[list[dict], int | None]:
    """The saved pages of the current query in order and the first page number not yet saved (None once the last
    page, the one saying there is no next, is saved)."""
    pages: list[dict] = []
    while True:
        body = request(len(pages) + 1, today)
        row = saved(manifest, lambda m, b=body: m.get("url") == SEARCH and m.get("request_body") == b)
        if row is None:
            return pages, len(pages) + 1
        pages.append(row)
        if not has_next(row):
            return pages, None


def row_of(award: dict, page: dict) -> dict:
    gid, kind = award["generated_internal_id"], (award.get("Award Type") or "assistance award").lower()
    recipient = award.get("Recipient Name") or "a recipient the record does not name"
    description = " ".join((award.get("Description") or "").split())
    listings = [f"{a.get('cfda_number')} {a.get('cfda_program_title') or ''}".strip() for a in award.get("Assistance Listings") or []]
    amount = award.get("Award Amount")
    # An award is dated the day its period starts; one listed before its period starts (a cooperative agreement
    # signed in September for 1 October) is dated the day the listing was saved, since that is when it was public.
    start, seen = award.get("Start Date") or "", page["retrieved_at"][:10]
    published = min(start, seen) if start else ""
    return {
        # The canonical event types have no assistance award; to the back-test a grant is an award like a contract.
        "claim_key": f"usaspending:{gid}", "event_type": "contract_awarded",
        "published": published, "title": f"{award['Award ID']}: {kind} to {recipient}"[:200],
        "body": "; ".join(p for p in (description, f"award amount {amount:,.2f}" if amount is not None else "",
                                      f"period {award.get('Start Date') or 'unstated'} to {award.get('End Date') or 'unstated'}",
                                      f"assistance listing {', '.join(listings)}" if listings else "") if p),
        "section": "vendors_incumbents", "url": f"https://www.usaspending.gov/award/{gid}",
        "sha256": page["sha256"], "retrieved_at": page["retrieved_at"], "path": page["path"],
        # The description is where the award names its office ("DARPA DEFENSE SCIENCES OFFICE DSO SEEDLING PROGRAM").
        "excerpt": (description or f"{award['Award ID']} {kind}")[:600], "uic": "",
        "data": {"award_id": award["Award ID"], "award_type": award.get("Award Type"), "recipient": award.get("Recipient Name"),
                 "uei": award.get("Recipient UEI"), "amount": amount, "start": award.get("Start Date"), "end": award.get("End Date"),
                 "awarding_sub_agency": award.get("Awarding Sub Agency"), "assistance_listings": listings},
    }


def events(manifest: list[dict], today: date | None = None) -> dict:
    """Every award on the saved pages, once, cited to the first page that carries it."""
    pages, _ = saved_pages(manifest, today)
    by_key: dict[str, dict] = {}
    for page in pages:
        for award in json.loads((ROOT / page["path"]).read_bytes()).get("results") or []:
            by_key.setdefault(f"usaspending:{award['generated_internal_id']}", row_of(award, page))
    return {"source_key": "usaspending_api", "rows": [by_key[k] for k in sorted(by_key)]}


def dumps(payload: dict) -> str:
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def sweep(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="assistance.py sweep")
    ap.add_argument("--fetch", action="store_true", help="take pages from USAspending; without it, only report what is next")
    ap.add_argument("--limit", type=int, default=50, help="most pages to take in one run")
    args = ap.parse_args(argv)
    pages, missing = saved_pages(manifest_rows())
    number = missing or len(pages)  # nothing missing: re-take the last page, where new awards land
    if not args.fetch:
        print(f"{len(pages)} page(s) saved; next to take, page {number}")
        return 0
    taken = 0
    with MANIFEST.open("a", encoding="utf-8") as handle:
        while number and taken < args.limit:
            row = fetch(SEARCH, "direct", None, f"USAspending assistance awards{NOTE_TAG}: {P['agency']['subtier_name']} since {SINCE}, "
                        f"page {number}", payload=request(number))
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            taken += 1
            print(row.get("status"), row.get("size"), f"page {number}")
            if not kept_page(row):
                break
            number = number + 1 if has_next(row) else None
            time.sleep(1.0)
    print(f"took {taken} page(s)")
    return 0


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="assistance.py build")
    ap.add_argument("--check", action="store_true", help="exit 1 if the saved file differs from a fresh build")
    args = ap.parse_args(argv)
    payload = events(manifest_rows())
    text = dumps(payload)
    kinds = Counter(r["data"]["award_type"] for r in payload["rows"])
    print(f"{len(payload['rows'])} assistance award(s) {dict(sorted(kinds.items()))}")
    if args.check:
        if not EVENTS.exists() or EVENTS.read_text(encoding="utf-8") != text:
            print(f"{EVENTS.relative_to(ROOT)} differs from a fresh build; run without --check to regenerate", file=sys.stderr)
            return 1
        return 0
    EVENTS.write_text(text, encoding="utf-8")
    print(f"written to {EVENTS.relative_to(ROOT)}")
    return 0


# Two awards of the page USAspending returned on 2026-09-25 for the DARPA query (limit 3), descriptions shortened.
FIXTURE = [
    {"internal_id": 267937374, "Award ID": "HR00111320004", "Recipient Name": "UNIVERSITY OF SOUTHERN CALIFORNIA",
     "Recipient UEI": "G88KLJR3KYT5", "Award Amount": 1597179.84, "Start Date": "2012-11-15", "End Date": "2020-09-23",
     "Description": "THE PURPOSE OF THIS COOPERATIVE AGREEMENT IS TO FUND RESEARCH IN SUPPORT OF THE DEFENSE ADVANCED RESEARCH "
                    "PROJECTS AGENCY DARPA MICROSYSTEMS TECHNOLOGY OFFICE MTO",
     "Awarding Agency": "Department of Defense", "Awarding Sub Agency": "Defense Advanced Research Projects Agency",
     "Award Type": "COOPERATIVE AGREEMENT (B)", "generated_internal_id": "ASST_NON_HR00111320004_097",
     "Assistance Listings": [{"cfda_number": "12.910", "cfda_program_title": "RESEARCH AND TECHNOLOGY DEVELOPMENT"}]},
    {"internal_id": 267937394, "Award ID": "HR00111410001", "Recipient Name": "UNIVERSITY OF ALABAMA AT BIRMINGHAM",
     "Recipient UEI": "YND4PLMC9AN7", "Award Amount": 1488144.51, "Start Date": "2013-11-11", "End Date": "2014-11-14",
     "Description": "THE PURPOSE OF THIS GRANT IS TO FUND RESEARCH IN SUPPORT OF THE DEFENSE ADVANCED RESEARCH PROJECTS AGENCY "
                    "(DARPA) MICROSYSTEMS TECHNOLOGY OFFICE (MTO) OFFICE WIDE.",
     "Awarding Agency": "Department of Defense", "Awarding Sub Agency": "Defense Advanced Research Projects Agency",
     "Award Type": "PROJECT GRANT (B)", "generated_internal_id": "ASST_NON_HR00111410001_097",
     "Assistance Listings": [{"cfda_number": "12.910", "cfda_program_title": "RESEARCH AND TECHNOLOGY DEVELOPMENT"}]},
]


def selfcheck() -> int:
    today = date(2026, 9, 25)
    assert fiscal_year_end(today) == "2026-09-30" and fiscal_year_end(date(2026, 10, 1)) == "2027-09-30"
    assert request(2, today)["page"] == 2 and request(1, today)["filters"]["time_period"][0]["end_date"] == "2026-09-30"
    with tempfile.TemporaryDirectory() as tmp:
        one, two = Path(tmp) / "one", Path(tmp) / "two"
        one.write_text(json.dumps({"results": FIXTURE[:1], "page_metadata": {"page": 1, "hasNext": True}}))
        two.write_text(json.dumps({"results": FIXTURE, "page_metadata": {"page": 2, "hasNext": False}}))  # a shift repeats a row
        row = lambda path, page, sha: {"url": SEARCH, "status": 200, "path": str(path), "sha256": sha,  # noqa: E731
                                       "retrieved_at": "2026-09-25T00:00:00Z", "request_body": request(page, today)}
        rows = [row(one, 1, "aaa"), row(two, 2, "bbb")]
        assert saved_pages([], today) == ([], 1), "nothing saved: start at the first page"
        assert saved_pages(rows[:1], today) == (rows[:1], 2), "a page saying there is a next closes; the next is taken"
        assert saved_pages(rows, today) == (rows, None), "the last page is the one re-taken"
        assert saved_pages(rows, date(2026, 10, 1)) == ([], 1), "a new fiscal year asks for every page again"
        payload = events(rows, today)
        assert dumps(payload) == dumps(events(rows + rows, today)), "the same saved pages give the same bytes"
    out = payload["rows"]
    assert [r["claim_key"] for r in out] == ["usaspending:ASST_NON_HR00111320004_097", "usaspending:ASST_NON_HR00111410001_097"]
    assert out[0]["sha256"] == "aaa" and out[1]["sha256"] == "bbb", "the first page carrying an award is cited"
    first = out[0]
    assert first["title"] == "HR00111320004: cooperative agreement (b) to UNIVERSITY OF SOUTHERN CALIFORNIA"
    assert first["published"] == "2012-11-15" and first["event_type"] == "contract_awarded" and first["uic"] == ""
    assert "award amount 1,597,179.84" in first["body"] and "assistance listing 12.910 RESEARCH AND TECHNOLOGY DEVELOPMENT" in first["body"]
    assert first["excerpt"].endswith("MICROSYSTEMS TECHNOLOGY OFFICE MTO"), "the description is quoted, where the office is named"
    assert list(first) == ["claim_key", "event_type", "published", "title", "body", "section", "url", "sha256",
                           "retrieved_at", "path", "excerpt", "uic", "data"], "the shape the loader reads"
    print("assistance selfcheck ok")
    return 0


COMMANDS = {"sweep": sweep, "build": build}

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

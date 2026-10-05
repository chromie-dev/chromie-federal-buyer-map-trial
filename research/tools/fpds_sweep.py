"""FPDS award sweep: every base award (modification 0) a contracting office signed in a fiscal year,
saved page by page from the public ATOM feed, so the incumbent family covers an office's whole book
and not only the contracts a forecast row happens to name.

The feed answers `CONTRACTING_OFFICE_ID:<uic> SIGNED_DATE:[a,b] MODIFICATION_NUMBER:0` ten actions a
page and names the next page with a rel="next" link. A closed fiscal year is fetched once; the open
one is re-taken on every sweep because new awards move the page boundaries. `awards()` reads the
saved pages back and is what the loader emits `contract_expires` events from: the ultimate completion
date FPDS states on the base award, with the requirement description the buyer wrote.

`histories` follows each swept award through its whole FPDS history (the `PIID:` query), so the loader can
date extensions, options exercised and terminations: those running or ended within HISTORY_BACK_DAYS first,
then the rest, since a modification can move the end the base award states. A finished history is not taken
again; a running contract's newest page is re-taken once it is HISTORY_STALE_DAYS old, because a new
modification lands on it.

    python research/tools/fpds_sweep.py sweep --fetch [--limit N]
    python research/tools/fpds_sweep.py histories --fetch [--limit N]
    python research/tools/fpds_sweep.py list
    python research/tools/fpds_sweep.py --selfcheck
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import MANIFEST, ROOT, fetch, is_stub, kept_page  # noqa: E402
from lrae_package import FPDS_PAGE, fpds_entries, fpds_history, fpds_url, manifest_rows, saved, url_index  # noqa: E402
from agency import P, NOTE_TAG  # noqa: E402

# The contracting offices swept by CONTRACTING_OFFICE_ID and the agencies swept by FUNDING_AGENCY_ID, per agency profile.
OFFICES = P["fpds_offices"]
FUNDING = P["fpds_funding_agencies"]
FIELDS = (("CONTRACTING_OFFICE_ID", OFFICES, "contracting office"), ("FUNDING_AGENCY_ID", FUNDING, "funding agency"))
HISTORY_BACK_DAYS = 365
HISTORY_STALE_DAYS = 28
FIRST_FY = 2020
QUERY = ("https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q={field}:{office}"
         "+SIGNED_DATE:%5B{start_day},{end_day}%5D+MODIFICATION_NUMBER:0&start={start}")


def fiscal_year(day: date) -> int:
    return day.year + (1 if day.month >= 10 else 0)


def windows(today: date) -> list[dict]:
    """One window per fiscal year from FIRST_FY to the current one; only the current is open."""
    current = fiscal_year(today)
    return [{"fy": fy, "start_day": f"{fy - 1}/10/01", "end_day": f"{fy}/09/30", "open": fy == current}
            for fy in range(FIRST_FY, current + 1)]


def page_url(office: str, window: dict, start: int, field: str = "CONTRACTING_OFFICE_ID") -> str:
    return QUERY.format(field=field, office=office, start_day=window["start_day"], end_day=window["end_day"], start=start)


def saved_pages(manifest: list[dict], office: str, window: dict, field: str = "CONTRACTING_OFFICE_ID") -> tuple[list[dict], bool]:
    """The saved pages of one window in feed order and whether the last page is among them."""
    pages: list[dict] = []
    while True:
        url = page_url(office, window, len(pages) * FPDS_PAGE, field)
        row = saved(manifest, lambda m, u=url: m.get("url") == u)
        if row is None:
            return pages, False
        pages.append(row)
        if b'rel="next"' not in (ROOT / row["path"]).read_bytes():
            return pages, True


def awards(manifest: list[dict] | None = None, today: date | None = None) -> list[dict]:
    """Every base award on the saved sweep pages, one row per PIID, each tagged with the page that carries it."""
    manifest = manifest_rows() if manifest is None else manifest
    by_piid: dict[str, dict] = {}
    for field, table, _ in FIELDS:
        for office in table:
            for window in windows(today or date.today()):
                pages, _ = saved_pages(manifest, office, window, field)
                for page in pages:
                    for entry in fpds_entries((ROOT / page["path"]).read_bytes(), width=None):
                        if entry["piid"]:
                            by_piid.setdefault(entry["piid"], dict(entry, fy=window["fy"], page={
                                "url": page["url"], "sha256": page["sha256"], "retrieved_at": page["retrieved_at"]}))
    return [by_piid[k] for k in sorted(by_piid)]


def sweep(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fpds_sweep.py sweep")
    ap.add_argument("--fetch", action="store_true", help="take pages from fpds.gov; without it, only report what is missing")
    ap.add_argument("--limit", type=int, default=2000, help="most pages to take in one run")
    ap.add_argument("--fy", default="", help="comma-separated fiscal years to sweep (default all), so windows can run side by side")
    args = ap.parse_args(argv)
    only = {int(x) for x in args.fy.split(",") if x.strip()}
    manifest = manifest_rows()
    taken = 0
    with MANIFEST.open("a", encoding="utf-8") as handle:
        for field, table, kind in FIELDS:
            for office, label in table.items():
                for window in windows(date.today()):
                    pages, complete = saved_pages(manifest, office, window, field)
                    if (only and window["fy"] not in only) or (complete and not window["open"]):
                        continue
                    start = 0 if window["open"] else len(pages) * FPDS_PAGE
                    while taken < args.limit:
                        url = page_url(office, window, start, field)
                        if not args.fetch:
                            print(f"missing {url}")
                            break
                        row = fetch(url, "direct", None, f"FPDS sweep{NOTE_TAG}: {kind} {office} ({label}) base awards FY{window['fy']}, page {start // FPDS_PAGE + 1}")
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                        handle.flush()
                        taken += 1
                        print(row.get("status"), row.get("size"), f"{office} FY{window['fy']} start={start}", flush=True)
                        time.sleep(1.0)
                        if not kept_page(row) or b'rel="next"' not in (ROOT / row["path"]).read_bytes():
                            break
                        start += FPDS_PAGE
    print(f"took {taken} page(s)")
    return 0


def followed(rows: list[dict], today: date) -> list[dict]:
    """The swept awards whose history is followed, every one with an end: running or ended within HISTORY_BACK_DAYS
    first, soonest end first; then those the base award says ended earlier, latest end first, because a modification
    may have moved that end (the sweep holds base awards only)."""
    floor = (today - timedelta(days=HISTORY_BACK_DAYS)).isoformat()
    dated = sorted((r for r in rows if r["completion"]), key=lambda r: (r["completion"], r["piid"]))
    return [r for r in dated if r["completion"] >= floor] + [r for r in reversed(dated) if r["completion"] < floor]


def next_history_page(index: dict[str, dict], award: dict, today: date) -> str | None:
    """The history page to take next for one award, or None when the saved history is current."""
    pages, actions, complete = fpds_history(index, award["piid"])
    if not complete:
        return fpds_url(award["piid"], len(pages) * FPDS_PAGE)
    end = max([a["completion"] for a in actions if a["piid"] == award["piid"] and a["completion"]] or [award["completion"]])
    taken = datetime.strptime(pages[-1]["retrieved_at"][:10], "%Y-%m-%d").date()
    if end >= today.isoformat() and (today - taken).days >= HISTORY_STALE_DAYS:
        return fpds_url(award["piid"], (len(pages) - 1) * FPDS_PAGE)
    return None


def histories(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fpds_sweep.py histories")
    ap.add_argument("--fetch", action="store_true", help="take pages from fpds.gov; without it, only count what is due")
    ap.add_argument("--limit", type=int, default=2000, help="most pages to take in one run")
    ap.add_argument("--shard", default="0/1", help="i/n: follow only the awards whose position mod n is i, so shards run side by side "
                                                  "(fpds.gov answers a history page in ten to twenty seconds)")
    args = ap.parse_args(argv)
    shard, shards = (int(x) for x in args.shard.split("/"))
    manifest = manifest_rows()
    index, today, taken, due = url_index(manifest), date.today(), 0, 0
    with MANIFEST.open("a", encoding="utf-8") as handle:
        for position, award in enumerate(followed(awards(manifest), today)):
            if position % shards != shard:
                continue
            while taken < args.limit:
                url = next_history_page(index, award, today)
                if url is None:
                    break
                due += 1
                if not args.fetch:
                    break
                row = fetch(url, "direct", None, f"FPDS history{NOTE_TAG} of swept award {award['piid']} ({award['contracting_office']}), "
                                                 f"page {int(url.rsplit('=', 1)[1]) // FPDS_PAGE + 1}")
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
                taken += 1
                if not kept_page(row):
                    break
                index[url] = row
                time.sleep(1.0)
    print(f"{due} history page(s) due, took {taken}")
    return 0


def list_cmd(argv: list[str]) -> int:
    manifest = manifest_rows()
    rows = awards(manifest)
    for field, table, kind in FIELDS:
        for office in table:
            for window in windows(date.today()):
                pages, complete = saved_pages(manifest, office, window, field)
                urls = {p["url"] for p in pages}
                n = sum(1 for r in rows if r["fy"] == window["fy"] and r["page"]["url"] in urls)
                print(f"{kind} {office} FY{window['fy']}: {len(pages)} page(s), {n} award(s), {'complete' if complete else 'INCOMPLETE'}")
    dated = sum(1 for r in rows if r["completion"] and r["description"])
    print(f"{len(rows)} award(s), {dated} with a completion date and a description")
    return 0


FIXTURE = b"""<feed><link rel="next" href="x"/>
<entry><content><ns1:PIID>N0003925C0001</ns1:PIID><ns1:signedDate>2025-01-15 00:00:00</ns1:signedDate>
<ns1:ultimateCompletionDate>2030-01-14 00:00:00</ns1:ultimateCompletionDate>
<ns1:descriptionOfContractRequirement>NTCDL ENGINEERING SUPPORT SERVICES FOR PMW 160</ns1:descriptionOfContractRequirement>
<ns1:contractingOfficeID>N00039</ns1:contractingOfficeID><ns1:vendorName>ACME</ns1:vendorName></content></entry>
<entry><content><ns1:PIID>N0003925C0001</ns1:PIID><ns1:signedDate>2025-01-15 00:00:00</ns1:signedDate></content></entry>
</feed>"""


def selfcheck() -> int:
    ws = windows(date(2026, 9, 22))
    assert ws[0]["fy"] == FIRST_FY and ws[-1]["fy"] == 2026 and ws[-1]["open"] and not ws[-2]["open"]
    assert ws[-1]["start_day"] == "2025/10/01" and ws[-1]["end_day"] == "2026/09/30"
    assert fiscal_year(date(2026, 10, 1)) == 2027 and fiscal_year(date(2026, 9, 30)) == 2026
    office = next(iter(OFFICES))  # the profile's first swept office; the fixture is matched by URL, not by its office tag
    assert page_url(office, ws[-1], 20).endswith("MODIFICATION_NUMBER:0&start=20")
    assert page_url("97AE", ws[-1], 0, "FUNDING_AGENCY_ID").startswith("https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=FUNDING_AGENCY_ID:97AE+")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "page"
        path.write_bytes(FIXTURE)
        url = page_url(office, ws[-2], 0)
        manifest = [{"url": url, "status": 200, "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                     "sha256": "abc", "retrieved_at": "2026-09-22T00:00:00Z"}]
        # The row's path is absolute here; ROOT / absolute stays absolute.
        pages, complete = saved_pages(manifest, office, ws[-2])
        assert len(pages) == 1 and not complete, "a page naming a next page is not the last"
        rows = awards(manifest, today=date(2026, 9, 22))
    assert len(rows) == 1, "one row per PIID, however many actions a page repeats"
    assert rows[0]["completion"] == "2030-01-14" and rows[0]["fy"] == 2025 and rows[0]["page"]["sha256"] == "abc"
    assert rows[0]["description"] == "NTCDL ENGINEERING SUPPORT SERVICES FOR PMW 160", "the description is kept whole"
    assert rows[0]["mod"] == "" and rows[0]["research"] == "", "the fixture states neither; the parser leaves them empty"

    today = date(2026, 9, 22)
    old, live = dict(rows[0], piid="A", completion="2025-09-21"), dict(rows[0], piid="B", completion="2025-09-22")
    assert [r["piid"] for r in followed([rows[0], old, live], today)] == ["B", "N0003925C0001", "A"], "soonest end first; ended over a year ago last"
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "history"
        last.write_bytes(FIXTURE.replace(b'<link rel="next" href="x"/>', b""))
        piid = "N0003925C0001"
        assert next_history_page({}, rows[0], today) == fpds_url(piid, 0), "no page saved: take the first"
        fresh = {fpds_url(piid, 0): {"url": fpds_url(piid, 0), "path": str(last), "sha256": "h", "retrieved_at": "2026-09-01T00:00:00Z"}}
        assert next_history_page(fresh, rows[0], today) is None, "a complete history taken three weeks ago is current"
        stale = {fpds_url(piid, 0): dict(fresh[fpds_url(piid, 0)], retrieved_at="2026-08-01T00:00:00Z")}
        assert next_history_page(stale, rows[0], today) == fpds_url(piid, 0), "a running contract's last page is re-taken when stale"
        last.write_bytes(FIXTURE.replace(b'<link rel="next" href="x"/>', b"").replace(b"2030-01-14", b"2026-01-14"))
        assert next_history_page(stale, rows[0], today) is None, "a finished contract's complete history is never re-taken"
    assert is_stub(b"<html><p>The FPDS production application will be down for scheduled maintenance...</p></html>"), \
        "the maintenance notice FPDS answers with a 200 is not a feed page"
    print("fpds_sweep selfcheck ok")
    return 0


COMMANDS = {"sweep": sweep, "histories": histories, "list": list_cmd}

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

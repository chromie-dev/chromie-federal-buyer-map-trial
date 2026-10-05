#!/usr/bin/env python3
"""Programs family: Navy SBIR/STTR topics from the DoD SBIR/STTR portal (DSIP).

Every SBIR host (sbir.gov, navysbir.com, dodsbirsttr.mil) answers this machine's address with 403 and the portal's
API refuses the hosted browser's request context too, so each API page is fetched from inside a loaded portal page
in a hosted US browser (Browserbase). The API honours the sort and ignores every filter, so the sweep reads all
components newest first and stops at the window start; Navy rows within the window keep their detail record. Every
page and detail is saved with its hash in the manifest and the rows are rebuilt from the saved files alone, so a
rerun without the network gives the same rows.

  python research/tools/sbir.py sweep --fetch [--since 2019-10-01] [--size 500] [--pages N]
  python research/tools/sbir.py build              # research/events/sbir_topics.json from the saved pages and details
  python research/tools/sbir.py show N241-032
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from browserbase_fetch import fail, save  # noqa: E402
from llm import env_value  # noqa: E402
from lrae_package import manifest_rows, saved  # noqa: E402

PORTAL = "https://www.dodsbirsttr.mil/topics-app/"
API = "https://www.dodsbirsttr.mil/topics/api/public/topics"
from agency import EVENTS, P, NOTE_TAG  # noqa: E402

OUT = EVENTS / "sbir_topics.json"
PROVIDER = "sbir_sttr_topics"
SINCE = "2019-10-01"  # FY2020 on, the study window
DEPARTMENT = P["agency"]["node"]
COMPONENT = P["sbir_component"]  # the portal's component column: NAVY, DARPA, ...
# The portal's `command` column against the organization memory; a command the memory lacks falls to the Department.
COMMANDS = P["sbir_commands"]
FETCH_JS = "async (u) => { const r = await fetch(u, {headers: {Accept: 'application/json'}}); return {s: r.status, t: await r.text()}; }"
TAG_RE = re.compile(r"<[^>]+>")
# The FY2026 topic code: component letters, fiscal year, instrument letter pair (BZ/TZ a BAA, BX/TX a CSO), release, and
# after the dash the entry letters (NP: new Phase I; DV: Direct to Phase II; NV/PV/other pairs kept as written) and number.
CODE_RE = re.compile(r"^[A-Z]{3}(\d{2})([BT])([ZX])(\d{2})-([A-Z]{2})(\d{3})$")
LEGACY_D2_RE = re.compile(r"-D\d{3}$")  # an older code whose number is prefixed D (AF254-D001): Direct to Phase II
LEGACY_RE = re.compile(r"^[A-Z]{1,6}\d{2,3}[A-Z]?-T?\d{3}$")  # the pre-FY2026 code shape (N251-001, A20B-T018, AF221-0001 is four digits and stays unknown)
INSTRUMENTS = {"Z": "baa", "X": "cso"}
# Entry types the code letters, the phase set and the title words derive. First match wins; Catapult, Strategic Breakthrough,
# CATALYST and Prize-to-Contract are never derived from DSIP (they are not topics). A code or phase set none of these fit is unknown.
ENTRY_TYPES = ("sbir_xl", "xtech_competition", "open_topic", "direct_to_phase_ii", "phase_i_or_d2p2", "phase_i", "unknown")
CEILING_RE = re.compile(r"[^.]*\$\s?\d[\d,]*(?:\.\d+)?\s?(?:M|K|million|thousand)?[^.]*\.")


def page_url(page_no: int, size: int) -> str:
    param = urllib.parse.quote(json.dumps({"sortBy": "topicStartDate,desc"}))
    return f"{API}/search?searchParam={param}&size={size}&page={page_no}"



def command_org(command: str | None) -> str:
    """The memory node for a portal command: the name as printed, else its stem before a hyphen (the portal writes a
    laboratory's directorates as AFRL-RY, AFRL-RX; the profile names the laboratory once), else the department."""
    name = (command or "").strip()
    stems = [name, name.split("-")[0].strip(), re.split(r"[-/ ]", name)[0].strip()]
    return next((COMMANDS[s] for s in stems if s in COMMANDS), DEPARTMENT)

def detail_url(topic_id: str) -> str:
    return f"{API}/{topic_id}/details"


def ms_day(ms) -> str:
    """The portal prints epoch milliseconds; the day in UTC is the day it means."""
    if not ms or not str(ms).isdigit():
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def strip_html(text) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", text or ""))).strip()


class Browser:
    """One hosted browser session with the portal loaded; the API is read from inside that page."""

    def __enter__(self):
        from browserbase import Browserbase
        from playwright.sync_api import sync_playwright
        key = env_value("BROWSERBASE_API_KEY")
        if not key:
            raise SystemExit("BROWSERBASE_API_KEY is not set")
        created = Browserbase(api_key=key).sessions.create(browser_settings={"recordSession": False}, api_timeout=1800)
        print("session", created.id[:8], flush=True)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(created.connect_url)
        self.ctx = self.browser.contexts[0]
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.goto(PORTAL, wait_until="domcontentloaded", timeout=60_000)
        return self

    def __exit__(self, *exc):
        try:
            self.browser.close()
        finally:
            self.pw.stop()

    def fetch_json(self, url: str, note: str):
        r = self.page.evaluate(FETCH_JS, url)
        body = r["t"].encode("utf-8")
        if r["s"] != 200:
            self.error = f"HTTP {r['s']}: {r['t'][:300]}"  # the portal says why it refused; keep it with the failure
            fail(url, note, self.error, r["s"])
            return None
        try:
            parsed = json.loads(body)
        except ValueError:
            fail(url, note, f"not JSON ({len(body)} bytes)", 200)
            return None
        save(url, body, "application/json", 200, note, url)
        return parsed


def read(row: dict) -> dict:
    return json.loads((ROOT / row["path"]).read_text(encoding="utf-8"))


def sweep(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="sbir.py sweep")
    ap.add_argument("--fetch", action="store_true", help="touch the network; without it the sweep only reports what is saved")
    ap.add_argument("--saved", action="store_true",
                    help="take this component's topics from the index pages already saved (every component is on them) instead of "
                         "the live index, and fetch only the details; for when the portal refuses the index query")
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--size", type=int, default=500)
    ap.add_argument("--pages", type=int, default=200, help="stop after this many index pages (1 = only what is newest)")
    ap.add_argument("--reverse", action="store_true",
                    help="fetch the details oldest first, so a second sweep can meet the first in the middle (each keeps its own list of "
                         "what is saved, so the two overlap after they meet; stop both once every topic has its detail)")
    args = ap.parse_args(argv)
    manifest = manifest_rows()
    have = {r["url"] for r in manifest if kept_page(r) and r.get("url")}  # a backfill row has no URL
    if not args.fetch:
        print(f"{sum(u.startswith(API + '/search') for u in have)} index page(s) and {sum(u.endswith('/details') for u in have)} detail(s) saved; --fetch to sweep")
        return 0
    page_no, navy, restarts = 0, [], 0
    if args.saved:  # the portal refuses the index query: take the component's topics from the saved index pages, fetch the details
        pages = [r for r in manifest if kept_page(r) and (r.get("url") or "").startswith(API + "/search")
                 and (ROOT / r["path"]).exists()]
        latest: dict[str, dict] = {}
        for row in sorted(pages, key=lambda r: r["retrieved_at"]):
            for r in read(row).get("data") or []:
                if ours(r, args.since):
                    latest[r["topicId"]] = r
        navy, page_no = list(latest.values()), args.pages
        print(f"  {len(navy)} {COMPONENT} topic(s) in window on {len(pages)} saved index page(s)", flush=True)
    while page_no < args.pages or (args.saved and any(detail_url(r["topicId"]) not in have for r in navy)):
        try:
            with Browser() as b:
                while page_no < args.pages:
                    d = b.fetch_json(page_url(page_no, args.size), f"DSIP topics index{NOTE_TAG} page {page_no}, newest first")
                    if d is None and "Page Size is too large" in b.error and args.size > 10:
                        # the portal caps its page size without saying where; halve it and resume at the same offset
                        page_no, args.size = page_no * args.size // (args.size // 2), args.size // 2
                        print(f"  page size refused; trying {args.size}", flush=True)
                        continue
                    if d is None:  # a refused index page is a failed sweep, not an empty portal
                        raise RuntimeError(f"index page {page_no} refused: {b.error}")
                    rows = (d or {}).get("data") or []
                    starts = [ms_day(r.get("topicStartDate")) for r in rows]
                    fresh = [r for r in rows if ours(r, args.since)]
                    navy += fresh
                    print(f"  page {page_no}: {len(rows)} topic(s), {len(fresh)} {COMPONENT} in window, starts {min(starts, default='')}..{max(starts, default='')}", flush=True)
                    page_no += 1
                    if not rows or min(starts) < args.since:
                        page_no = args.pages
                        break
                for i, r in enumerate(navy[::-1] if args.reverse else navy):
                    url = detail_url(r["topicId"])
                    if url in have:
                        continue
                    b.fetch_json(url, f"DSIP topic{NOTE_TAG} {r.get('topicCode')} detail")
                    have.add(url)
                    if i % 100 == 0:
                        print(f"  detail {i} of {len(navy)}", flush=True)
                    time.sleep(0.3)
        except Exception as exc:  # noqa: BLE001 - a hosted session that dies mid-sweep is reopened, not fatal
            restarts += 1
            print(f"  session lost ({type(exc).__name__}: {str(exc)[:400]}); restart {restarts}", flush=True)
            if restarts > 3:
                return 1
    print(f"{len(navy)} {COMPONENT} topic(s) since {args.since}; details saved for {sum(detail_url(r['topicId']) in have for r in navy)}")
    return 0


def phases(hierarchy) -> list[str]:
    """The phase set the portal's phaseHierarchy JSON string lists, in its order (["1", "2", "2S"], ["D2", "2", "2S"])."""
    if not hierarchy:
        return []
    try:
        parsed = json.loads(hierarchy) if isinstance(hierarchy, str) else hierarchy
    except ValueError:
        return []
    return [str(c.get("phase")) for c in (parsed or {}).get("config") or [] if c.get("phase")]


def instrument_of(code: str, solicitation_title: str) -> str:
    """baa or cso from the code's instrument letter (FY2026 codes), else from the solicitation title's words, else unknown."""
    m = CODE_RE.match(code or "")
    if m:
        return INSTRUMENTS[m.group(3)]
    title = (solicitation_title or "").upper()
    if "CSO" in title:
        return "cso"
    if "BAA" in title or re.search(r"SBIR|STTR", title):
        return "baa"
    return "unknown"


def entry_of(code: str, title: str, phase_set: list[str]) -> tuple[str, str]:
    """The entry and what it rests on: the title's words first (SBIR XL, xTech, Open Topic), then the phase set, then the
    code's entry letters, then a legacy D-prefixed number."""
    t = (title or "").upper()
    if "SBIR XL" in t:
        return "sbir_xl", "title"
    if "XTECH" in t:
        return "xtech_competition", "title"
    if "OPEN TOPIC" in t:
        return "open_topic", "title"
    if phase_set == ["D2", "2", "2S"] or phase_set == ["D2", "2"]:
        return "direct_to_phase_ii", "phases"
    if phase_set == ["1", "D2", "2", "2S"]:
        return "phase_i_or_d2p2", "phases"
    m = CODE_RE.match(code or "")
    if m and m.group(5) == "DV":
        return "direct_to_phase_ii", "code"
    if phase_set[:1] == ["1"]:
        return "phase_i", "phases"
    if m and m.group(5) == "NP":
        return "phase_i", "code"
    if LEGACY_D2_RE.search(code or ""):
        return "direct_to_phase_ii", "code"
    if LEGACY_RE.match(code or ""):
        return "phase_i", "code"  # a legacy topic in a DoD BAA is a Phase I solicitation unless its number is D-prefixed
    return "unknown", "none"


def ceiling_sentence(text: str) -> str | None:
    """The first sentence of the topic text holding a dollar amount, verbatim; None when the text names no amount."""
    m = CEILING_RE.search(text or "")
    return m.group(0).strip() if m else None


def topic_row(r: dict, detail: dict | None, saved_row: dict | None, parents: dict, resolve) -> dict:
    text = strip_html(" ".join(filter(None, [(detail or {}).get("objective"), (detail or {}).get("description"),
                                             (detail or {}).get("phase3Description")])))
    keywords = strip_html((detail or {}).get("keywords") or "")
    offices = resolve(" ".join([r.get("topicTitle") or "", keywords, text]), parents)
    code, title = r.get("topicCode") or "", strip_html(r.get("topicTitle"))
    phase_set = phases(r.get("phaseHierarchy"))
    instrument = instrument_of(code, r.get("solicitationTitle") or "")
    entry, basis = entry_of(code, title, phase_set)
    return {"topic_id": r["topicId"], "code": code, "title": title, "program": r.get("program") or "",
            "component": r.get("component") or "", "command": r.get("command") or "", "org": command_org(r.get("command")),
            "cycle": r.get("cycleName") or "", "solicitation": r.get("solicitationTitle") or "", "status": r.get("topicStatus") or "",
            # verbatim from the saved index row: the solicitation number and release, the phase set, the Q&A window, the compliance flags
            "solicitation_number": str(r.get("solicitationNumber") or ""), "release": r.get("releaseNumber"), "phases": phase_set,
            "instrument": instrument, "entry": entry, "entry_type": f"{instrument}:{entry}", "entry_basis": basis,
            "qa_open": ms_day(r.get("topicQAStartDate")), "qa_close": ms_day(r.get("topicQAEndDate")),
            "itar": bool((detail or {}).get("itar")) if detail and "itar" in detail else None, "cmmc_level": (detail or {}).get("cmmcLevel") or r.get("cmmcLevel") or "",
            "focus_areas": (detail or {}).get("focusAreas") or [],
            "pre_release": min(filter(None, [ms_day(r.get("topicPreReleaseStartDate")), ms_day(r.get("topicStartDate"))]), default=""),
            "open": ms_day(r.get("topicStartDate")), "close": ms_day(r.get("topicEndDate")), "keywords": keywords,
            "technology_areas": (detail or {}).get("technologyAreas") or [], "text": text[:6000], "ceiling": ceiling_sentence(text), "offices": offices,
            "url": detail_url(r["topicId"]), "sha256": (saved_row or {}).get("sha256", ""), "retrieved_at": (saved_row or {}).get("retrieved_at", ""),
            "path": (saved_row or {}).get("path", "")}


def resolve_specific(text: str, parents: dict) -> list[str]:
    from trace import resolve_offices, specific_offices  # noqa: E402
    return specific_offices(resolve_offices(text), parents)


def ours(r: dict, since: str) -> bool:
    """A topic of this agency's portal component within the window. A profile with no component takes none: the
    portal leaves some old records without one, and a missing component is nobody's."""
    return bool(COMPONENT) and r.get("component") == COMPONENT and ms_day(r.get("topicStartDate")) >= since


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="sbir.py build")
    ap.add_argument("--since", default=SINCE)
    args = ap.parse_args(argv)
    from trace import match_context  # noqa: E402
    parents = match_context()["parents"]
    manifest = manifest_rows()
    pages = [r for r in manifest if kept_page(r) and r.get("url", "").startswith(API + "/search") and (ROOT / r["path"]).exists()]
    latest: dict[str, dict] = {}
    for row in sorted(pages, key=lambda r: r["retrieved_at"]):
        for r in read(row).get("data") or []:
            if ours(r, args.since):
                latest[r["topicId"]] = r
    rows = []
    for topic_id, r in latest.items():
        saved_row = saved(manifest, lambda m, u=detail_url(topic_id): m.get("url") == u)
        rows.append(topic_row(r, read(saved_row) if saved_row else None, saved_row, parents, resolve_specific))
    rows.sort(key=lambda t: (t["pre_release"], t["code"]))
    payload = {"built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "since": args.since, "provider": PROVIDER,
               "topics": len(rows), "with_detail": sum(bool(t["path"]) for t in rows), "with_office": sum(bool(t["offices"]) for t in rows),
               "by_command": dict(Counter(t["command"] for t in rows).most_common()),
               "by_fiscal_year": dict(sorted(Counter(fiscal_year(t["pre_release"]) for t in rows).items())),
               "by_entry_type": dict(Counter(t["entry_type"] for t in rows).most_common()), "by_instrument": dict(Counter(t["instrument"] for t in rows).most_common()),
               "by_focus_area": dict(Counter(a for t in rows for a in t["focus_areas"]).most_common()), "rows": rows}
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{payload['topics']} {COMPONENT} topic(s) since {args.since}, {payload['with_detail']} with detail, {payload['with_office']} naming a program office; "
          f"by command {payload['by_command']} -> {OUT.relative_to(ROOT)}")
    return 0


def fiscal_year(day: str) -> str:
    if not day:
        return "undated"
    year, month = int(day[:4]), int(day[5:7])
    return f"FY{year + 1 if month >= 10 else year}"


def show(argv: list[str]) -> int:
    rows = json.loads(OUT.read_text(encoding="utf-8"))["rows"]
    for t in rows:
        if t["code"] in argv or t["topic_id"] in argv:
            print(json.dumps({k: (v[:400] if isinstance(v, str) else v) for k, v in t.items()}, indent=1, ensure_ascii=False))
    return 0


FIXTURE = {"topicId": "abc_1", "topicCode": "N251-001", "topicTitle": "Antenna for <b>MIDS</b>", "program": "SBIR", "component": "NAVY",
           "command": "NAVWAR", "cycleName": "DOD_SBIR_2025_P1_C1", "solicitationTitle": "DoD SBIR 2025.1", "topicStatus": "Closed",
           "topicPreReleaseStartDate": "1733893200000", "topicStartDate": "1736485200000", "topicEndDate": "1739077200000"}


def selfcheck() -> int:
    assert ms_day("1748433600000") == "2025-05-28" and ms_day(None) == "" and ms_day("") == ""
    global COMPONENT
    kept, COMPONENT = COMPONENT, None
    assert not ours({"component": None, "topicStartDate": "1748433600000"}, "2019-10-01"), "a profile with no component takes no topic"
    COMPONENT = kept
    assert strip_html("<p>Develop &amp; demo</p>  <br/>x") == "Develop & demo x"
    assert fiscal_year("2025-10-01") == "FY2026" and fiscal_year("2025-09-30") == "FY2025" and fiscal_year("") == "undated"
    assert "sortBy" in urllib.parse.unquote(page_url(0, 500)) and page_url(3, 10).endswith("&size=10&page=3")
    detail = {"objective": "<p>Build a link for PMW 101 (MIDS).</p>", "description": "x", "keywords": "MANET, antenna", "technologyAreas": ["Air Platform"]}
    named = lambda text, parents: ["pmw:101"] if "PMW 101" in text else []  # noqa: E731 - the resolver is the trace module's; here only the plumbing
    t = topic_row(FIXTURE, detail, {"sha256": "ab", "retrieved_at": "2026-09-22T00:00:00Z", "path": "data/raw/x"}, {}, named)
    assert (t["pre_release"], t["open"], t["close"]) == ("2024-12-11", "2025-01-10", "2025-02-09"), (t["pre_release"], t["open"], t["close"])
    assert t["title"] == "Antenna for MIDS" and t["org"] == "command:navwar" and t["offices"] == ["pmw:101"] and t["keywords"] == "MANET, antenna"
    assert command_org("MCSC") == COMMANDS.get("MCSC", DEPARTMENT) and command_org("NAVFAC") == COMMANDS.get("NAVFAC", DEPARTMENT), "a command the profile maps is its node"
    bare = topic_row({**FIXTURE, "command": "CNRMC", "topicPreReleaseStartDate": None}, None, None, {}, named)  # a command no profile maps
    assert bare["org"] == DEPARTMENT and bare["pre_release"] == bare["open"] == "2025-01-10" and bare["offices"] == [] and bare["path"] == ""
    # the 20.4 special cycle printed a pre-release date after the open date; the event is the earlier of the two
    odd = topic_row({**FIXTURE, "command": "ONR", "topicPreReleaseStartDate": "1739077200000"}, None, None, {}, named)
    assert odd["org"] == "command:onr" and odd["pre_release"] == "2025-01-10", odd["pre_release"]
    # Entry types from the code letters, the phase set and the title words; the V/P suffix letter is stored in the code, not read
    p12 = '{"config": [{"phase": "1"}, {"phase": "2"}, {"phase": "2S"}]}'
    d2 = '{"config": [{"phase": "D2"}, {"phase": "2"}, {"phase": "2S"}]}'
    assert phases(p12) == ["1", "2", "2S"] and phases(None) == [] and phases("not json") == []
    assert instrument_of("DON26BZ06-DV088", "") == "baa" and instrument_of("DON26BX05-NP003", "") == "cso" and instrument_of("N251-001", "DoD SBIR 2025.1") == "baa"
    assert entry_of("DON26BZ06-DV088", "DIRECT TO PHASE II: Blood Collection", ["D2", "2", "2S"]) == ("direct_to_phase_ii", "phases")
    assert entry_of("DON26BX05-NP003", "NAVSEA Open Topic for MBSE", ["1", "2", "2S"]) == ("open_topic", "title")
    assert entry_of("DON26BZ01-NP010", "Antenna", ["1", "2", "2S"]) == ("phase_i", "phases") and entry_of("HR0011SB20264-04", "SBIR XL: Quantum", []) == ("sbir_xl", "title")
    assert entry_of("AF254-D001", "Widget", []) == ("direct_to_phase_ii", "code") and entry_of("ZZZ", "Widget", []) == ("unknown", "none")
    assert entry_of("N251-001", "Antenna", []) == ("phase_i", "code") and entry_of("A20B-T018", "STTR widget", []) == ("phase_i", "code")
    assert entry_of("A254-P007", "xTech Search", []) == ("xtech_competition", "title") and "CATALYST" not in ENTRY_TYPES
    row = topic_row({**FIXTURE, "topicCode": "DON26BZ06-DV088", "phaseHierarchy": d2, "solicitationNumber": "26.BZ", "releaseNumber": 6,
                     "topicQAStartDate": "1785931200000", "topicQAEndDate": "1788969600000", "cmmcLevel": "Level 2 (Self)"},
                    {**detail, "itar": True, "focusAreas": ["Trusted AI"], "description": "Awards up to $1.8M for Phase II. Deliver a prototype."}, None, {}, named)
    assert (row["entry_type"], row["entry_basis"], row["solicitation_number"], row["release"], row["phases"]) == ("baa:direct_to_phase_ii", "phases", "26.BZ", 6, ["D2", "2", "2S"])
    assert row["qa_open"] == "2026-08-05" and row["qa_close"] == "2026-09-09" and row["itar"] is True and row["cmmc_level"] == "Level 2 (Self)" and row["focus_areas"] == ["Trusted AI"]
    assert row["ceiling"] == "Awards up to $1.8M for Phase II." and t["ceiling"] is None and t["itar"] is None and t["entry_type"] == "baa:phase_i"
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"sweep": sweep, "build": build, "show": show}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

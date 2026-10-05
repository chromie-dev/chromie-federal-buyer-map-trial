#!/usr/bin/env python3
"""Pre-release observations: the days the DoD SBIR/STTR portal first showed this agency's topics, read from the saved
index pages alone (offline), grouped by pre-release day, solicitation number and release, and compared with the
first-Wednesday rule the registry states. Nothing here is loaded into the record; it is what the monitor (docs/06) will
compare a live sweep against, and the basis the registry's cadence rests on.

    AGENCY=navy python research/tools/prerelease.py build [--check]   # research/events/prerelease_observations.json
    AGENCY=navy python research/tools/prerelease.py sweep [--fetch]   # research/events/prerelease_sweep.json (network with --fetch)
    python research/tools/prerelease.py --selfcheck

The sweep (Stage 3) reads every registry row whose cadence is monthly and lists pages, computes the cycle's due day
from the rule (the most recent first Wednesday on or before today), and for each page asks the ledger whether any
attempt was recorded since that day. A page with none is fetched once through fetch.py with `--fetch` and the answer
appended to the ledger, refusal and stub included; nothing is worked around and nothing is retried inside a cycle.
The DSIP row's newest index page, when a saved copy of this cycle exists, is read for pre-release days not yet in
the observations file, listed as `new_days` and loaded by nothing until `build` runs.

Each observation: {"day", "solicitation_number", "release", "topics", "codes" (first eight), "matches_rule": {"weekday",
"ordinal", "first_wednesday": bool}, "evidence": the newest saved index page that lists the day}. `matches_rule` is a
labelled comparison, never a verdict: a day the rule does not fit is kept and says so.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from lrae_package import manifest_rows  # noqa: E402
from sbir import API, COMPONENT, PROVIDER, SINCE, ms_day, page_url, read  # noqa: E402

from agency import EVENTS, MANIFEST, NOTE_TAG, P, SOURCES  # noqa: E402

OUT = EVENTS / "prerelease_observations.json"
SWEEP_OUT = EVENTS / "prerelease_sweep.json"
RULE = {"weekday": 2, "ordinal": 1}  # the first Wednesday of the month, as the registry's DSIP cadence states it
DSIP_KEY = "sbir_sttr_topics"
INDEX_SIZE = 62  # the largest page size the topics API accepted on 2026-09-26; 125 and above answered 400 "Page Size is too large"


def rule_reading(day: str) -> dict:
    """How the day sits against the rule: its weekday (Monday 0), which of that weekday in the month it is, and whether it
    is a first Wednesday."""
    d = date.fromisoformat(day)
    ordinal = (d.day - 1) // 7 + 1
    return {"weekday": d.weekday(), "ordinal": ordinal, "first_wednesday": d.weekday() == RULE["weekday"] and ordinal == RULE["ordinal"]}


def observations(pages: list[dict], component: str, since: str) -> list[dict]:
    """Group the component's index rows by pre-release day, solicitation number and release; the newest page listing a
    row is its evidence."""
    latest: dict[str, tuple[dict, dict]] = {}
    for page in sorted(pages, key=lambda r: r["retrieved_at"]):
        for r in read(page).get("data") or []:
            if r.get("component") != component:
                continue
            day = min(filter(None, [ms_day(r.get("topicPreReleaseStartDate")), ms_day(r.get("topicStartDate"))]), default="")
            if day and day >= since:
                latest[r["topicId"]] = (r, page)
    groups: dict[tuple[str, str, str], list[tuple[dict, dict]]] = defaultdict(list)
    for r, page in latest.values():
        day = min(filter(None, [ms_day(r.get("topicPreReleaseStartDate")), ms_day(r.get("topicStartDate"))]))
        groups[(day, str(r.get("solicitationNumber") or ""), str(r.get("releaseNumber") or ""))].append((r, page))
    out = []
    for (day, sol, release), rows in sorted(groups.items()):
        newest = max((p for _, p in rows), key=lambda p: p["retrieved_at"])
        out.append({"day": day, "solicitation_number": sol, "release": release, "topics": len(rows),
                    "codes": sorted(r.get("topicCode") or "" for r, _ in rows)[:8], "matches_rule": rule_reading(day),
                    "evidence": {"url": newest["url"], "sha256": newest["sha256"], "retrieved_at": newest["retrieved_at"]}})
    return out


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="prerelease.py build")
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    pages = [r for r in manifest_rows() if kept_page(r) and r.get("url", "").startswith(API + "/search") and (ROOT / r["path"]).exists()]
    rows = observations(pages, COMPONENT, args.since)
    fits = sum(1 for o in rows if o["matches_rule"]["first_wednesday"])
    payload = {"provider": PROVIDER, "component": COMPONENT, "since": args.since, "rule": RULE, "observations": len(rows),
               "first_wednesdays": fits, "note": ("Pre-release days of the component's topics on the saved DSIP index pages, one row per day, solicitation number "
                                                  "and release; matches_rule is a reading of each day against the first-Wednesday rule, not a verdict. Offline: "
                                                  "rebuilt from the saved pages alone."), "rows": rows}
    text = json.dumps(payload, indent=1, ensure_ascii=False) + "\n"
    if args.check:
        saved = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
        if saved is None or {k: v for k, v in saved.items() if k != "built_at"} != payload:
            print("prerelease_observations.json differs from a fresh build; run `prerelease.py build` to regenerate", file=sys.stderr)
            return 1
        print("prerelease_observations.json matches a fresh build")
        return 0
    payload["built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(rows)} pre-release day(s) for {COMPONENT} since {args.since}, {fits} on a first Wednesday -> {OUT.relative_to(ROOT)}")
    return 0


def selfcheck() -> int:
    assert rule_reading("2026-05-06") == {"weekday": 2, "ordinal": 1, "first_wednesday": True}
    assert rule_reading("2026-04-13") == {"weekday": 0, "ordinal": 2, "first_wednesday": False}, "the annual BAA's Monday is kept and says so"
    assert rule_reading("2026-09-02")["first_wednesday"] and not rule_reading("2026-09-09")["first_wednesday"]
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "page.json"
        rows = [{"topicId": "a", "topicCode": "DON26BZ06-DV088", "component": "NAVY", "topicPreReleaseStartDate": "1788307200000", "solicitationNumber": "26.BZ", "releaseNumber": 6},
                {"topicId": "b", "topicCode": "DON26BZ06-NP001", "component": "NAVY", "topicPreReleaseStartDate": "1788307200000", "solicitationNumber": "26.BZ", "releaseNumber": 6},
                {"topicId": "c", "topicCode": "DPA26BZ06-NP001", "component": "DARPA", "topicPreReleaseStartDate": "1788307200000", "solicitationNumber": "26.BZ", "releaseNumber": 6},
                {"topicId": "d", "topicCode": "N19A-T001", "component": "NAVY", "topicPreReleaseStartDate": "1546300800000", "solicitationNumber": "19.A", "releaseNumber": None}]
        path.write_text(json.dumps({"data": rows}), encoding="utf-8")
        page = {"url": API + "/search?x", "path": str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path), "retrieved_at": "2026-09-22T00:00:00Z", "sha256": "f" * 64}
        import sbir
        original = sbir.read
        sbir.read = lambda row: json.loads(path.read_text(encoding="utf-8"))
        try:
            obs = observations([page], "NAVY", "2019-10-01")
        finally:
            sbir.read = original
    assert len(obs) == 1 and obs[0]["day"] == "2026-09-02" and obs[0]["topics"] == 2 and obs[0]["codes"] == ["DON26BZ06-DV088", "DON26BZ06-NP001"], obs
    assert obs[0]["matches_rule"]["first_wednesday"] and obs[0]["evidence"]["sha256"] == "f" * 64 and obs[0]["solicitation_number"] == "26.BZ"
    # the sweep's calendar and its reading of the ledger
    assert due_day(date(2026, 9, 28)) == "2026-09-02" and due_day(date(2026, 9, 2)) == "2026-09-02" and due_day(date(2026, 9, 1)) == "2026-08-05"
    assert due_day(date(2026, 1, 3)) == "2025-12-03" and due_day(date(2026, 10, 7)) == "2026-10-07", "the cycle rolls back across the year"
    ledger = [{"url": "https://x/", "retrieved_at": "2026-09-01T00:00:00Z", "status": 200, "path": "p"},
              {"url": "https://x/", "retrieved_at": "2026-09-03T00:00:00Z", "status": 403, "error": "HTTP 403"}]
    assert [r["status"] for r in cycle_attempts(ledger, "https://x/", "2026-09-02")] == [403], "last month's answer is not this cycle's"
    assert answer_of(ledger[1]) == "refused" and answer_of({"status": 200, "path": "p", "content_status": "rejected_stub"}) == "stub"
    reg = {"a": {"instrument": {"cadence": {"kind": "monthly", "rule": RULE}, "pages": ["https://x/"]}},
           "b": {"instrument": {"cadence": {"kind": "standing"}, "pages": ["https://y/"]}},
           DSIP_KEY: {"instrument": {"cadence": {"kind": "monthly"}, "pages": ["https://www.dodsbirsttr.mil/topics-app/"]}}}
    pages = dict((k, p) for k, _, p in monthly_pages(reg))
    assert set(pages) == {"a", DSIP_KEY} and pages[DSIP_KEY][-1].startswith(API + "/search?") and f"size={INDEX_SIZE}&page=0" in pages[DSIP_KEY][-1]
    print("prerelease selfcheck ok")
    return 0


# ---------------------------------------------------------------- the sweep (Stage 3)

def nth_weekday(year: int, month: int, weekday: int, ordinal: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (ordinal - 1))


def due_day(today: date, rule: dict | None = None) -> str:
    """The most recent rule day on or before today: this month's if it has come, else last month's. The rule's
    `exceptions` are documentation of days the portal departed from it, not part of the computation."""
    rule = rule or RULE
    this = nth_weekday(today.year, today.month, rule["weekday"], rule["ordinal"])
    if this <= today:
        return this.isoformat()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return nth_weekday(y, m, rule["weekday"], rule["ordinal"]).isoformat()


def registry_rows() -> dict[str, dict]:
    return {r["source_key"]: r for r in json.loads((SOURCES / "source_registry.json").read_text(encoding="utf-8"))}


def monthly_pages(reg: dict[str, dict]) -> list[tuple[str, dict, list[str]]]:
    """(source_key, rule, pages) for every row whose cadence is monthly and that lists pages; the DSIP row also lists
    the newest index page, which is what the observations are read from."""
    out = []
    for key, row in reg.items():
        ins = row.get("instrument") or {}
        cad = ins.get("cadence") or {}
        if cad.get("kind") != "monthly" or not ins.get("pages"):
            continue
        pages = list(ins["pages"])
        if key == DSIP_KEY:
            pages.append(page_url(0, INDEX_SIZE))
        out.append((key, cad.get("rule") or RULE, pages))
    return out


def cycle_attempts(rows: list[dict], url: str, since: str) -> list[dict]:
    """Every ledger row for the URL recorded on or after the cycle's due day, refusals included, oldest first."""
    return sorted((r for r in rows if r.get("url") == url and (r.get("retrieved_at") or "")[:10] >= since), key=lambda r: r.get("retrieved_at") or "")


def record_row(row: dict) -> None:
    with MANIFEST.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def answer_of(row: dict) -> str:
    if kept_page(row):
        return "page"
    if row.get("content_status") == "rejected_stub":
        return "stub"
    return "refused"


def new_days(page_row: dict, component: str, since: str, known: set[str]) -> list[dict]:
    """Pre-release days on a saved index page, on or after the cycle's due day, that the observations file lacks."""
    if answer_of(page_row) != "page" or not (ROOT / page_row["path"]).exists():
        return []
    days: dict[str, list[str]] = defaultdict(list)
    for r in json.loads((ROOT / page_row["path"]).read_text(encoding="utf-8")).get("data") or []:
        if r.get("component") != component:
            continue
        day = min(filter(None, [ms_day(r.get("topicPreReleaseStartDate")), ms_day(r.get("topicStartDate"))]), default="")
        if day and day >= since:
            days[day].append(r.get("topicCode") or "")
    return [{"day": d, "topics": len(codes), "codes": sorted(codes)[:8], "matches_rule": rule_reading(d)} for d, codes in sorted(days.items()) if d not in known]


def sweep(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="prerelease.py sweep")
    ap.add_argument("--fetch", action="store_true", help="take each page with no attempt recorded this cycle (network)")
    ap.add_argument("--today", default="", help="YYYY-MM-DD, for a replay; default today")
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone.utc).date()
    from fetch import fetch
    reg = registry_rows()
    rows = manifest_rows()
    known = {o["day"] for o in json.loads(OUT.read_text(encoding="utf-8"))["rows"]} if OUT.exists() else set()
    listings, fresh_days, refused, fetched = [], [], 0, 0
    for key, rule, pages in monthly_pages(reg):
        due = due_day(today, rule)
        for url in pages:
            attempts = cycle_attempts(rows, url, due)
            entry = {"source_key": key, "url": url, "due": due, "attempts_this_cycle": len(attempts)}
            if attempts:
                last = attempts[-1]
                entry.update(action="kept", answer=answer_of(last), retrieved_at=last.get("retrieved_at", ""), status=last.get("status"),
                             method=last.get("method", ""), sha256=last.get("sha256", ""), error=last.get("error", ""))
            elif args.fetch:
                row = fetch(url, "direct", None, f"prerelease sweep{NOTE_TAG}: {key} cycle {due}")
                record_row(row)
                rows.append(row)
                kind = answer_of(row)
                fetched += kind == "page"
                refused += kind != "page"
                entry.update(action="fetched" if kind == "page" else "recorded_" + kind, answer=kind, retrieved_at=row.get("retrieved_at", ""),
                             status=row.get("status"), method="direct", sha256=row.get("sha256", ""), error=row.get("error", ""))
                time.sleep(args.pause)
            else:
                entry.update(action="due", answer="", retrieved_at="", status=None, method="", sha256="", error="")
            if key == DSIP_KEY and url.startswith(API) and entry.get("answer") == "page":
                page_row = next(r for r in reversed(cycle_attempts(rows, url, due)) if answer_of(r) == "page")
                fresh_days += new_days(page_row, COMPONENT, due, known)
            listings.append(entry)
            print(f"{key}: {entry['action']:14} {entry.get('answer') or '-':8} {url[:90]}" + (f" ({entry.get('error')})" if entry.get("error") else ""))
    payload = {"agency": P["key"], "as_of": today.isoformat(), "rule": RULE, "listings": listings, "new_days": fresh_days,
               "note": ("One line per page a monthly row lists, for the cycle whose due day the rule gives: what the ledger recorded since that "
                        "day, or what one fetch answered; a refusal or a stub is the recorded answer and is not retried inside the cycle. "
                        "new_days are pre-release days on this cycle's saved index page that the observations file lacks; `build` reads them.")}
    SWEEP_OUT.parent.mkdir(parents=True, exist_ok=True)
    SWEEP_OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    due_n = sum(1 for e in listings if e["action"] == "due")
    print(f"\n{len(listings)} page(s) on {len(monthly_pages(reg))} monthly row(s); {fetched} taken, {refused} refusal(s) recorded, {due_n} due"
          + ("" if args.fetch else " (rerun with --fetch to take them)") + f"; {len(fresh_days)} new pre-release day(s) -> {SWEEP_OUT.relative_to(ROOT) if SWEEP_OUT.is_relative_to(ROOT) else SWEEP_OUT}"
          + ("; now `prerelease.py build`" if fresh_days else ""))
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"build": build, "sweep": sweep}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

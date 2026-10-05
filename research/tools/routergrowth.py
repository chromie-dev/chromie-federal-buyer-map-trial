#!/usr/bin/env python3
"""RouterGrowth: one query to whichever provider is live behind a named capability, with every answer saved first.

    python research/tools/routergrowth.py inspect company.jobs [--fresh]   # the capability's input schema, saved and printed
    python research/tools/routergrowth.py discover "open jobs"             # keyless: which capabilities and endpoints exist
    python research/tools/routergrowth.py --selfcheck

The router is a discovery service, never a source: what it returns is a list of addresses, and the bytes a record
quotes are the page behind an address, fetched first-hand. Its answers are still saved and recorded in the ledger
(`research/sources/documents_manifest.jsonl`) so a statement of what was searched has its bytes, the way the Exa and
GDELT answers are saved by `news.py`. The input field names come from `/v1/inspect` rather than from a copy of the
documentation, so a provider swap behind the capability does not silently drop a parameter; today's saved inspect
answer is reused, so one sweep asks the schema once. `/v1/discover` answers without a key; `/v1/inspect` and `/v1/run`
need `ROUTERGROWTH_API_KEY` (read from the environment, else `.env`) and a run is drawn from a prepaid wallet, so a
caller passes `max_cost` where the schema takes one and records what the answer says it cost.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import MANIFEST, NOTE_TAG  # noqa: E402
from llm import env_value  # noqa: E402

BASE = "https://api.routergrowth.com"
KEY_NAME = "ROUTERGROWTH_API_KEY"
RAW = ROOT / "data" / "raw" / "routergrowth"  # where a bare `inspect` or `discover` from this command line is saved
# The run answer's fields that carry a result list, in the order tried; a provider's own shape sits under `output`.
RESULT_KEYS = ("results", "items", "jobs", "postings", "articles", "news", "data")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    """The ledger's day, in UTC like every retrieved_at, so a reuse check does not miss an answer taken after midnight local."""
    return datetime.now(timezone.utc).date().isoformat()


def key() -> str:
    value = env_value(KEY_NAME)
    if not value:
        raise LookupError(f"{KEY_NAME} is not set")
    return value


def headers() -> dict:
    return {"Authorization": f"Bearer {key()}", "Content-Type": "application/json"}


def post_json(url: str, payload: dict, headers: dict, tries: int = 3) -> bytes:
    """POST and return the bytes. A throttled burst answers 503 or 429, so a query is repeated."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503) or attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def manifest_rows() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]


def record_answer(url: str, body: bytes, raw_dir: Path, note: str) -> dict:
    """Save an answer and record it, so a statement of what was searched has its bytes. Returns the ledger row."""
    digest = hashlib.sha256(body).hexdigest()
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"search_{digest[:12]}.json"
    path.write_bytes(body)
    row = {"url": url, "method": "direct", "status": 200, "retrieved_at": now(), "note": note, "mime": "application/json",
           "size": len(body), "sha256": digest, "path": str(path.relative_to(ROOT))}
    with MANIFEST.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


# ---------------------------------------------------------------- the schema

def fields_of(described: dict) -> dict:
    """The input fields an inspect answer declares, wherever it puts the schema: at the top, under `capability`, or
    under `input`; each as `input_schema` or `schema` with JSON-schema `properties`."""
    fields: dict = {}
    for holder in (described, described.get("capability") or {}, described.get("input") or {}):
        if not isinstance(holder, dict):
            continue
        schema = holder.get("input_schema") or holder.get("schema") or {}
        if isinstance(schema, dict):
            fields.update(schema.get("properties") or {})
    return fields


def payload_for(fields: dict, query: str, results: int | None = None, days: int | None = None, max_cost: float | None = None) -> dict:
    """The query plus every option the schema names and the caller set: how many results, how far back, how much
    to spend. A name the schema does not declare is not sent, so nothing is silently ignored on the other side."""
    payload: dict = {"query": query}
    for name, value in (("limit", results), ("num_results", results), ("max_results", results), ("depth", results),
                        ("days", days), ("time_range", f"{days}d" if days else None), ("max_cost", max_cost)):
        if value is not None and name in fields and name not in payload:
            payload[name] = value
    return payload


def saved_inspect(capability: str, rows: list[dict], today: str) -> dict | None:
    """Today's saved inspect answer for the capability, if this profile took one, so a sweep asks the schema once."""
    for row in reversed(rows):
        if (row.get("url") == f"{BASE}/v1/inspect" and kept_page(row)
                and (row.get("retrieved_at") or "")[:10] == today and (row.get("note") or "").endswith(f": {capability}")
                and (ROOT / row["path"]).exists()):
            return row
    return None


def inspect(capability: str, raw_dir: Path, note: str, fresh: bool = False) -> tuple[dict, dict]:
    """The capability's own description, saved; returns (answer, ledger row)."""
    if not fresh:
        row = saved_inspect(capability, manifest_rows(), today())
        if row:
            return json.loads((ROOT / row["path"]).read_bytes()), row
    body = post_json(f"{BASE}/v1/inspect", {"capability": capability}, headers())
    return json.loads(body), record_answer(f"{BASE}/v1/inspect", body, raw_dir, note)


def run(capability: str, payload: dict, raw_dir: Path, note: str) -> tuple[dict, dict]:
    """One run of the capability, saved before it is read; returns (answer, ledger row)."""
    body = post_json(f"{BASE}/v1/run", {"capability": capability, "input": payload}, headers())
    return json.loads(body), record_answer(f"{BASE}/v1/run", body, raw_dir, note)


def discover(query: str, raw_dir: Path | None = None, note: str = "") -> dict:
    """What the router offers for a query: capabilities and provider endpoints. Keyless; saved when a folder is given."""
    body = post_json(f"{BASE}/v1/discover", {"query": query}, {"Content-Type": "application/json"})
    if raw_dir is not None:
        record_answer(f"{BASE}/v1/discover", body, raw_dir, note or f"routergrowth discover{NOTE_TAG}: {query}")
    return json.loads(body)


# ---------------------------------------------------------------- the answer

def results(answer: dict) -> list[dict]:
    """A run answer as one shape: url, title, date, and for a job posting the company, location and description the
    provider states. The list sits at the top or under `output`, under one of a few names."""
    if answer.get("status") == "no_match" or (answer.get("error") or {}).get("code") == "no_match":
        return []  # the router ran every provider and none had a match: an empty search, not a failed one
    items = _items(answer)
    for holder in ("result", "output"):  # the router wraps a provider's list under `result` (company.jobs: result.jobs); older shapes used `output`
        if items is not None:
            break
        inner = answer.get(holder)
        if isinstance(inner, dict):
            items = _items(inner)
        elif isinstance(inner, list):
            items = inner
    if items is None:  # a shape this reader does not know is a failed search, not an empty one
        raise ValueError(f"RouterGrowth answer holds no result list (keys: {sorted(answer)})")
    read = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("link") or item.get("source_url") or item.get("job_url") or item.get("apply_url") or ""
        if not url:
            continue
        read.append({"url": url, "title": item.get("title") or item.get("headline") or item.get("job_title") or "",
                     "publishedDate": item.get("publishedDate") or item.get("published_at") or item.get("date_posted")
                     or item.get("posted_at") or item.get("date") or item.get("timestamp") or "",
                     "company": _text(item.get("company") or item.get("company_name") or item.get("organization") or item.get("employer") or ""),
                     "location": _text(item.get("location") or item.get("job_location") or ""),
                     "description": item.get("description") or item.get("snippet") or item.get("summary") or ""})
    return read


def _items(holder: dict) -> list | None:
    for name in RESULT_KEYS:
        value = holder.get(name)
        if isinstance(value, list):
            return value
    return None


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "")
    return str(value or "")


def cost_of(answer: dict) -> dict:
    """What the answer says about the run: the provider it routed to, what it charged (the router's `billing.charged`;
    `quoted` is the hold, not the price), the outcome, and the providers tried. A field the answer lacks is null, and
    the record says so rather than guessing a price."""
    cost, currency = None, None
    billing = answer.get("billing")
    if isinstance(billing, dict):
        cost = billing.get("charged", billing.get("amount", billing.get("total", billing.get("cost"))))
        currency = billing.get("currency")
    else:
        for name in ("cost", "usage", "charge"):
            value = answer.get(name)
            if isinstance(value, dict):
                cost = value.get("amount", value.get("total", value.get("usd", value.get("cost"))))
                currency = value.get("currency")
                break
            if isinstance(value, (int, float, str)):
                cost = value
                break
    if isinstance(cost, str):
        try:
            cost = float(cost)
        except ValueError:
            pass
    meta = answer.get("meta") if isinstance(answer.get("meta"), dict) else {}
    provider = answer.get("provider") or answer.get("routed_to") or meta.get("provider") or meta.get("routed_to") or None
    attempts = [f"{a.get('provider')}: {a.get('outcome')}" + (f" ({a.get('detail')})" if a.get("detail") else "")
                for a in (answer.get("attempts") or []) if isinstance(a, dict)]
    return {"provider": provider, "cost": cost, "currency": currency or answer.get("currency") or meta.get("currency") or ("USD" if cost is not None else None),
            "status": answer.get("status"), "attempts": attempts}


def search(capability: str, query: str, raw_dir: Path, note_prefix: str, results_wanted: int | None = None, days: int | None = None,
           max_cost: float | None = None, dry_run: bool = False) -> dict:
    """The schema read (or reused), the payload built from it, and, unless dry, one run; everything saved first.

    Notes are `<prefix> inspect: <capability>` and `<prefix> search: <query>` with the profile mark, so the coverage
    status files each row under the caller's source (coverage.NOTE_KEYS)."""
    described, inspect_row = inspect(capability, raw_dir, f"{note_prefix} inspect{NOTE_TAG}: {capability}")
    fields = fields_of(described)
    payload = payload_for(fields, query, results_wanted, days, max_cost)
    out = {"capability": capability, "payload": payload, "fields": sorted(fields), "inspect": inspect_row, "results": [], "cost": None, "row": None}
    if dry_run:
        return out
    answer, row = run(capability, payload, raw_dir, f"{note_prefix} search{NOTE_TAG}: {query}")
    return {**out, "results": results(answer), "cost": cost_of(answer), "row": row, "answer": answer}


# ---------------------------------------------------------------- commands

def cmd_inspect(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="routergrowth inspect")
    parser.add_argument("capability")
    parser.add_argument("--fresh", action="store_true", help="ask again even if today's answer is saved")
    args = parser.parse_args(argv)
    try:
        described, row = inspect(args.capability, RAW, f"routergrowth inspect{NOTE_TAG}: {args.capability}", fresh=args.fresh)
    except LookupError as exc:
        print(f"{exc}; the inspect call needs it (RUNBOOK.md, Credentials)")
        return 0
    fields = fields_of(described)
    print(f"{args.capability}: {len(fields)} input field(s): {', '.join(sorted(fields)) or 'none declared'}")
    for name in sorted(fields):
        spec = fields[name] if isinstance(fields[name], dict) else {}
        print(f"  {name}: {spec.get('type', '?')}" + (f"  {spec.get('description', '')[:100]}" if spec.get("description") else ""))
    print(f"saved {row['path']} (sha256 {row['sha256'][:12]}, retrieved {row['retrieved_at']})")
    return 0


def cmd_discover(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="routergrowth discover")
    parser.add_argument("query")
    parser.add_argument("--save", action="store_true", help="save and record the answer")
    args = parser.parse_args(argv)
    answer = discover(args.query, RAW if args.save else None)
    for item in answer.get("items") or []:
        if item.get("kind") == "capability":
            print(f"capability {item['capability']}: {item.get('title', '')}; {item.get('status')}; live providers {', '.join(item.get('live_providers') or [])};"
                  f" from {((item.get('starting_price') or {}).get('amount'))} {((item.get('starting_price') or {}).get('currency', ''))}")
        else:
            print(f"endpoint {item.get('provider')} {item.get('endpoint')}: {item.get('title', '')}; {item.get('status')}")
    return 0


def selfcheck() -> int:
    described = {"capability": {"name": "company.jobs", "input_schema": {"properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}},
                 "input": {"schema": {"properties": {"max_cost": {"type": "number"}}}},
                 "schema": {"properties": {"days": {"type": "integer"}}}}
    fields = fields_of(described)
    assert set(fields) == {"query", "limit", "max_cost", "days"}, fields
    assert payload_for(fields, "L3Harris jobs", 5, 30, 0.02) == {"query": "L3Harris jobs", "limit": 5, "days": 30, "max_cost": 0.02}
    assert payload_for({"query": {}}, "x", 5, 30, 0.02) == {"query": "x"}, "an option the schema does not declare is not sent"
    assert payload_for(fields, "x") == {"query": "x"}, "an option the caller did not set is not sent"
    assert fields_of({}) == {} and fields_of({"capability": "text"}) == {}
    answer = {"output": {"results": [{"link": "https://careers.example.com/j/1", "job_title": "Contract Specialist", "company": {"name": "Example"},
                                     "date_posted": "2026-09-01", "location": "San Diego, CA"}, "not a dict", {"title": "no url"}]},
              "provider": "leadmagic", "cost": {"amount": 0.0035, "currency": "USD"}}
    got = results(answer)
    assert got == [{"url": "https://careers.example.com/j/1", "title": "Contract Specialist", "publishedDate": "2026-09-01", "company": "Example",
                    "location": "San Diego, CA", "description": ""}], got
    assert results({"results": [{"url": "u", "title": "t"}]})[0]["url"] == "u" and results({"output": [{"url": "v"}]})[0]["url"] == "v"
    routed_jobs = {"status": "succeeded", "result": {"jobs": [{"title": "Lead, Contracts", "company": "L3Harris Technologies", "location": "Palm Bay, FL, United States",
                                                                "posted_at": "2026-09-26T18:31:12.000Z", "url": "https://www.linkedin.com/jobs/view/1/", "description": "d"}],
                                                      "provider_items": []}}
    assert results(routed_jobs) == [{"url": "https://www.linkedin.com/jobs/view/1/", "title": "Lead, Contracts", "publishedDate": "2026-09-26T18:31:12.000Z",
                                     "company": "L3Harris Technologies", "location": "Palm Bay, FL, United States", "description": "d"}], "the router's own wrapping, result.jobs"
    for unknown in ({}, {"results": "text"}):
        try:
            results(unknown); raise AssertionError("an answer with no result list read as zero results")
        except ValueError:
            pass
    assert cost_of(answer) == {"provider": "leadmagic", "cost": 0.0035, "currency": "USD", "status": None, "attempts": []}
    assert cost_of({"results": []}) == {"provider": None, "cost": None, "currency": None, "status": None, "attempts": []}, \
        "a price the answer does not state is null, not guessed"
    routed = {"status": "no_match", "provider": "leadmagic", "billing": {"currency": "USD", "quoted": "0.15", "charged": "0"},
              "attempts": [{"provider": "apify", "outcome": "no_match", "detail": "actor returned no items"}, {"provider": "leadmagic", "outcome": "no_match"}]}
    assert cost_of(routed) == {"provider": "leadmagic", "cost": 0.0, "currency": "USD", "status": "no_match",
                               "attempts": ["apify: no_match (actor returned no items)", "leadmagic: no_match"]}, "charged, not the quoted hold"
    today = date.today().isoformat()
    rows = [{"url": f"{BASE}/v1/inspect", "status": 200, "path": "research/tools/routergrowth.py", "retrieved_at": f"{today}T01:00:00Z", "note": "vendor jobs inspect: company.jobs"},
            {"url": f"{BASE}/v1/inspect", "status": 200, "path": "research/tools/routergrowth.py", "retrieved_at": "2020-01-01T01:00:00Z", "note": "vendor jobs inspect: news.search"}]
    assert saved_inspect("company.jobs", rows, today) is rows[0] and saved_inspect("news.search", rows, today) is None, "only today's answer for this capability is reused"
    print("routergrowth selfcheck ok")
    return 0


COMMANDS = {"inspect": cmd_inspect, "discover": cmd_discover}


def main(argv: list[str]) -> int:
    if "--selfcheck" in argv:
        return selfcheck()
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

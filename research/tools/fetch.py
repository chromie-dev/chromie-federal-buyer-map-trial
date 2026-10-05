#!/usr/bin/env python3
"""Fetch one URL, save the bytes under data/raw/, and record the retrieval in the manifest.

    python research/tools/fetch.py URL [--wayback [TIMESTAMP]] [--note TEXT] [--method M]

Direct fetches record status, final URL, MIME type, size and SHA-256. `--wayback` fetches the
closest Wayback Machine capture (or the given timestamp) of the original URL through the raw
`id_` endpoint and records the capture timestamp. Failures are recorded too, so blocked
documents are explicit rows rather than silent omissions. Stdlib only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
# ponytail: a plain browser UA; some .mil front ends reset connections for unfamiliar agents.


def _get(url: str, timeout: int = 90, insecure: bool = False, payload: dict | None = None) -> tuple[int, str, bytes, str]:
    headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"}
    data = None
    if payload is not None:  # a search API that answers only a POST (USAspending): the body is the query
        data, headers["Content-Type"] = json.dumps(payload, sort_keys=True).encode(), "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    context = ssl._create_unverified_context() if insecure else None  # DoD PKI roots are not in the default store
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)  # some archives ignore Accept-Encoding
        return resp.status, resp.geturl(), body, resp.headers.get("Content-Type", "")


def closest_capture(url: str) -> str | None:
    api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    try:
        _, _, body, _ = _get(api, timeout=60)
        closest = json.loads(body).get("archived_snapshots", {}).get("closest") or {}
        return closest.get("timestamp")
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None


def safe_name(url: str) -> str:
    base = urllib.parse.unquote(urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]) or "index"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:80]


def fetch(url: str, method: str, wayback: str | None, note: str, insecure: bool = False, payload: dict | None = None) -> dict:
    row: dict = {
        "url": url,
        "method": method,
        "tls_verified": not insecure,
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": note,
    }
    if payload is not None:
        row["request_body"] = payload  # the query a POST answer rests on, so the page can be asked for again
    target = url
    if method == "wayback":
        # A partial timestamp makes Wayback redirect to the nearest capture, so the
        # availability API is only a hint; the real capture time is read from the final URL.
        ts = wayback if wayback and wayback != "closest" else (closest_capture(url) or "2026")
        target = f"https://web.archive.org/web/{ts}id_/{url}"
    row["fetched_from"] = target
    try:
        status, final_url, body, mime = _get(target, insecure=insecure, payload=payload)
    except urllib.error.HTTPError as exc:
        row.update(status=exc.code, error=f"HTTP {exc.code}")
        return row
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        row.update(status=None, error=f"{type(exc).__name__}: {exc}")
        return row
    if method == "wayback":
        match = re.search(r"/web/(\d{14})id_/", final_url)
        row["wayback_timestamp"] = match.group(1) if match else None
        if body.startswith(b"<!DOCTYPE html>") and b"Wayback Machine" in body[:4000] and b"hasn't archived" in body:
            row.update(status=404, error="no wayback capture found")
            return row
    digest = hashlib.sha256(body).hexdigest()
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{digest[:12]}_{safe_name(url)}"
    path.write_bytes(body)
    row.update(status=status, final_url=final_url, mime=mime.split(";")[0].strip(), size=len(body),
               sha256=digest, path=str(path.relative_to(ROOT)))
    if is_stub(body):
        # A 200 whose body is a firewall or bot-protection page is not the document.
        row.update(content_status="rejected_stub", error="body is a firewall or bot-protection stub, not the page")
    return row


STUB_MARKERS = (b"Request Rejected", b"Access Denied", b"Attention Required", b"Pardon Our Interruption",
                b"down for scheduled maintenance")  # FPDS answers its maintenance notice with a 200


def is_stub(body: bytes) -> bool:
    return len(body) < 4000 and any(marker in body[:1500] for marker in STUB_MARKERS)


def kept_page(row: dict) -> bool:
    """A manifest row whose saved bytes are the page itself: a 200 kept on disk, not a refusal or maintenance stub
    (content_status). Anything else is a collection gap, never an empty result."""
    return row.get("status") == 200 and bool(row.get("path")) and not row.get("content_status")


def collect_missing(wanted: list[tuple[str, str]], limit: int = 10_000, pause: float = 1.0) -> int:
    """Fetch each (url, note) the manifest holds no saved copy of, recording every answer, a refusal included, so a
    collecting stage reruns to take only what is still missing. Returns how many are still not saved."""
    have = set()
    for line in MANIFEST.read_text(encoding="utf-8").splitlines() if MANIFEST.exists() else []:
        row = json.loads(line) if line.strip() else {}
        if kept_page(row):
            have |= {u for u in (row.get("url"), row.get("final_url")) if u}  # a redirect saved the page it landed on
    notes = dict(reversed(wanted))  # the first note given for a url
    todo = [u for u in dict.fromkeys(u for u, _ in wanted) if u not in have]
    missed = max(0, len(todo) - limit)
    with MANIFEST.open("a", encoding="utf-8") as handle:
        for url in todo[:limit]:
            row = fetch(url, "direct", None, notes[url])
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            print(row.get("status"), url[:110])
            missed += not kept_page(row)
            time.sleep(pause)
    print(f"{len(todo)} to collect, {missed} still not saved")
    return missed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--wayback", nargs="?", const="closest", default=None,
                        help="fetch the closest (or given YYYYMMDDhhmmss) Wayback capture")
    parser.add_argument("--method", choices=("direct", "wayback", "browserbase", "manual"))
    parser.add_argument("--note", default="")
    parser.add_argument("--insecure", action="store_true", help="skip TLS verification (DoD PKI hosts); recorded in the row")
    args = parser.parse_args()
    method = args.method or ("wayback" if args.wayback else "direct")
    row = fetch(args.url, method, args.wayback, args.note, insecure=args.insecure)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, sort_keys=True))
    return 0 if kept_page(row) else 1


if __name__ == "__main__":
    sys.exit(main())

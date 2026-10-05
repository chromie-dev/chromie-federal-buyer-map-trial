"""Recorded HTTP for the FY2026 sweep: every request appends one row to research/sources/documents_manifest.jsonl,
success or failure, and saves the body under data/raw/fy2026/. GET reuses research/tools/fetch.py; POST is
added here because the USAspending search endpoints take a JSON body. Nothing else is written."""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "tools"))
import fetch as _fetch  # noqa: E402

MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
RAW = ROOT / "data" / "raw" / "fy2026"
UA = _fetch.UA


def _append(row: dict) -> None:
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def get(url: str, note: str, wayback: str | None = None, retries: int = 2, pause: float = 0.7) -> dict:
    """GET through fetch.fetch (saved under data/raw/, hashed) and record. Retries transport errors and 5xx."""
    method = "wayback" if wayback else "direct"
    for attempt in range(retries + 1):
        row = _fetch.fetch(url, method, wayback, note)
        status = row.get("status")
        if status == 200 or (status and 400 <= status < 500):
            break
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
    _append(row)
    time.sleep(pause)
    return row


def post_json(url: str, body: dict, note: str, retries: int = 2, pause: float = 0.7) -> dict:
    """POST a JSON body, save the response under data/raw/fy2026/, record request body hash and response hash."""
    payload = json.dumps(body, sort_keys=True).encode()
    row = {"url": url, "method": "direct", "http_method": "POST", "request_sha256": hashlib.sha256(payload).hexdigest(),
           "request_body": body, "tls_verified": True, "note": note,
           "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=payload, headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
                digest = hashlib.sha256(data).hexdigest()
                RAW.mkdir(parents=True, exist_ok=True)
                path = RAW / f"{digest[:12]}_{_fetch.safe_name(url)}.json"
                path.write_bytes(data)
                row.update(status=resp.status, size=len(data), sha256=digest, path=str(path.relative_to(ROOT)),
                           mime=resp.headers.get("Content-Type", "").split(";")[0])
                break
        except urllib.error.HTTPError as exc:
            row.update(status=exc.code, error=f"HTTP {exc.code}")
            if 400 <= exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            row.update(status=None, error=f"{type(exc).__name__}: {exc}"[:160])
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
    _append(row)
    time.sleep(pause)
    return row


def body_of(row: dict) -> bytes:
    """The saved page. A failed or blocked fetch raises, so a sweep stops instead of reading it as an empty result."""
    if not _fetch.kept_page(row):
        raise RuntimeError(f"not collected: {row.get('url')}: {row.get('content_status') or row.get('error') or row.get('status')}")
    return (ROOT / row["path"]).read_bytes()


def json_of(row: dict):
    """The saved page as JSON; a body that is not JSON raises too."""
    return json.loads(body_of(row))

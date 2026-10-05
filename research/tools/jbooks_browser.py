#!/usr/bin/env python3
"""Budget books from a host that answers scripted clients with a JavaScript check (the Navy's FMB site).

A plain request to www.secnav.navy.mil gets an HTML check page instead of the PDF, from this machine and from Modal
alike, and the Wayback Machine captured the same page. A headless Chromium (the full build, channel "chromium") opens
the budget page, which runs the check, then downloads every book the page links with the browser's own cookies. The
Navy moved its FY2022 and later pages from /fmc/fmb/Pages/ to /fmc/Pages/; the old address redirects to the SECNAV
front page. Each book is saved under the books folder and gets a ledger row shaped like fetch.py's, with method
"browser".

  python research/tools/jbooks_browser.py https://www.secnav.navy.mil/fmc/Pages/Fiscal-Year-2027.aspx \
      [--match 27pres/] [--books data/raw/jbooks] [--note-prefix "DoN FY2027"] [--only RDTEN_BA4_Book.pdf ...]

Needs the playwright package and its Chromium (the chromie-runner virtual environment has both).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")


def book_links(hrefs: list[str], base: str, match: str) -> list[str]:
    urls = {urllib.parse.urljoin(base, h.strip()) for h in hrefs if h and match in h and h.lower().split("?")[0].endswith(".pdf")}
    return sorted(urls)


def ledger_row(url: str, body: bytes, status: int, books: Path, note: str) -> dict:
    row = {"url": url, "method": "browser", "tls_verified": True,
           "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "note": note,
           "fetched_from": url, "status": status}
    if not body.startswith(b"%PDF"):
        row.update(error="body is not a PDF (the JavaScript check did not clear)", size=len(body))
        return row
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]))
    path = books / name
    path.write_bytes(body)
    row.update(final_url=url, mime="application/pdf", size=len(body), sha256=hashlib.sha256(body).hexdigest(),
               path=str(path.relative_to(ROOT)))
    return row


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("page")
    parser.add_argument("--match", default="27pres/")
    parser.add_argument("--books", default="data/raw/jbooks")
    parser.add_argument("--note-prefix", default="DoN FY2027")
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args(argv)
    from playwright.sync_api import sync_playwright

    books = ROOT / args.books
    books.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chromium")
        context = browser.new_context(user_agent=USER_AGENT, accept_downloads=True)
        page = context.new_page()
        page.goto(args.page, wait_until="domcontentloaded", timeout=180_000)
        page.wait_for_selector(f'a[href*="{args.match}"]', timeout=180_000)  # the check reloads into the real page
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        urls = [u for u in book_links(hrefs, page.url, args.match) if not args.only or u.rsplit("/", 1)[-1] in args.only]
        print(f"{len(urls)} books linked from {page.url}", flush=True)
        for url in urls:
            try:
                response = context.request.get(url, timeout=900_000)
                row = ledger_row(url, response.body(), response.status, books, f"budget book: {args.note_prefix} {url.rsplit('/', 1)[-1]}")
            except Exception as exc:  # one failed book must not stop the rest
                row = {"url": url, "method": "browser", "note": f"budget book: {args.note_prefix}", "status": None,
                       "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            with MANIFEST.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
            print(url.rsplit("/", 1)[-1], row.get("status"), row.get("size"), row.get("error", "ok"), flush=True)
        browser.close()
    return 0


def selfcheck() -> int:
    hrefs = ["/fmc/fmb/Documents/27pres/RDTEN_BA4_Book.pdf", "../Documents/27pres/OPN_BA2_Book.pdf?x=1",
             "/fmc/fmb/Documents/26pres/SCN_Book.pdf", "/fmc/fmb/Pages/default.aspx", None]
    got = book_links(hrefs, "https://www.secnav.navy.mil/fmc/fmb/Pages/Fiscal-Year-2027.aspx", "27pres/")
    assert got == ["https://www.secnav.navy.mil/fmc/fmb/Documents/27pres/OPN_BA2_Book.pdf?x=1",
                   "https://www.secnav.navy.mil/fmc/fmb/Documents/27pres/RDTEN_BA4_Book.pdf"], got
    stub = ledger_row("https://x/27pres/A_Book.pdf", b"<!DOCTYPE html>", 200, Path("/nonexistent"), "n")
    assert "error" in stub and "path" not in stub
    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(selfcheck() if sys.argv[1:] == ["--selfcheck"] else main(sys.argv[1:]))

#!/usr/bin/env python3
"""Fetch official web pages through context.dev's scrape API with a United States address.

    python research/tools/context_fetch.py URL [URL ...]

gao.gov and the navy.mil hosts refuse this address; context.dev renders the page from a United States residential
address and returns its HTML. The page is saved under data/raw/ and recorded in the manifest with method
"context_dev". A file address (PDF, spreadsheet, document) and every tear sheet, forecast or budget file a saved
page links to go through the Browserbase fetcher, which keeps the bytes as served. The key is CONTEXT_DEV_API_KEY
from the environment or the local env files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browserbase_fetch import FILE_LINK_RE, WANTED_LINK_RE, fail, save  # noqa: E402
from fetch import ROOT  # noqa: E402
from agency import NOTE_TAG  # noqa: E402
from llm import env_value  # noqa: E402

API = "https://api.context.dev/v1/web/scrape/html"
METHOD = "context_dev"
TOOLS = Path(__file__).resolve().parent


def scrape(url: str, key: str) -> dict:
    """One page: context.dev's answer, {success, html, url, metadata}."""
    query = urllib.parse.urlencode({"url": url, "country": "US"})
    request = urllib.request.Request(f"{API}?{query}", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read())


def linked_files(html: str, base: str) -> list[str]:
    """The tear sheets, forecasts and budget files a page links to."""
    found = []
    for href in re.findall(r'href="([^"#]+)"', html, re.I):
        full = urllib.parse.urljoin(base, href.replace("&amp;", "&"))
        if FILE_LINK_RE.search(full) and WANTED_LINK_RE.search(urllib.parse.unquote(full)) and full not in found:
            found.append(full)
    return found


def take(url: str, key: str) -> tuple[dict, list[str]]:
    """Save one page; the manifest row and the files it links to."""
    note = f"live page via context.dev{NOTE_TAG}: {url}"
    try:
        answer = scrape(url, key)
    except urllib.error.HTTPError as exc:
        return fail(url, note, f"HTTP {exc.code}", exc.code, method=METHOD), []
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return fail(url, note, f"{type(exc).__name__}: {exc}", method=METHOD), []
    page = answer.get("html") or ""
    if not answer.get("success") or not page:
        return fail(url, note, f"no page returned: {str(answer.get('error') or answer.get('message') or '')[:120]}", method=METHOD), []
    meta = answer.get("metadata") or {}
    final = meta.get("finalUrl") or answer.get("url") or url
    row = save(url, page.encode("utf-8"), "text/html", 200, f"{note} | title: {(meta.get('title') or '')[:80]}", final, method=METHOD)
    return row, linked_files(page, final)


def browserbase(urls: list[str]) -> int:
    """Files through the Browserbase fetcher, keys from the env files."""
    if not urls:
        return 0
    env = dict(os.environ)
    for name in ("BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID", "BROWSERBASE_API_URL"):
        if env_value(name):
            env[name] = env_value(name)
    if not env.get("BROWSERBASE_API_KEY"):
        print(f"  {len(urls)} file(s) need research/tools/browserbase_fetch.py and BROWSERBASE_API_KEY is not set")
        return 1
    return subprocess.run([sys.executable, str(TOOLS / "browserbase_fetch.py"), *urls], cwd=ROOT, env=env).returncode


def fetch(urls: list[str]) -> int:
    """Pages through context.dev, files through Browserbase; 1 when any address did not save."""
    if not urls:
        return 0
    files = [u for u in urls if FILE_LINK_RE.search(u)]
    pages = [u for u in urls if u not in files]
    key = env_value("CONTEXT_DEV_API_KEY")
    if pages and not key:
        # Without context.dev, the hosted browser renders the pages too (browserbase_fetch saves a page as its rendered
        # HTML with method "browserbase"); only a machine with neither key stops here.
        if env_value("BROWSERBASE_API_KEY"):
            print(f"  {len(pages)} page(s) through Browserbase (CONTEXT_DEV_API_KEY is not set)")
            return browserbase(pages + files)
        print(f"  {len(pages)} page(s) need CONTEXT_DEV_API_KEY or BROWSERBASE_API_KEY, neither of which is set")
        return 1
    failed = 0
    for url in pages:
        row, linked = take(url, key)
        failed |= bool(row.get("error"))
        print(f"  {row.get('status') or 'ERR'} {row.get('size', '')} | {url[-90:]}")
        files += [f for f in linked if f not in files][:30]
    return browserbase(files) or failed


def selfcheck() -> int:
    page = '<a href="/Portals/1/FY27%20P-1%20Budget.pdf">P-1</a> <a href="/about.pdf">About</a> <a href="#top">top</a>'
    assert linked_files(page, "https://www.secnav.navy.mil/fmc/fmb/") == ["https://www.secnav.navy.mil/Portals/1/FY27%20P-1%20Budget.pdf"]
    assert linked_files('<a href="report.html">r</a>', "https://www.gao.gov/") == []
    print("context_fetch selfcheck ok")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        sys.exit(selfcheck())
    sys.exit(fetch(sys.argv[1:]))

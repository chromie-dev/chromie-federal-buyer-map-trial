"""The context.dev fetcher: a page is saved with method context_dev and its linked files go to Browserbase;
a refusal is recorded, never dropped. The API is mocked, so the test spends no credits."""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
import context_fetch  # noqa: E402

PAGE = '<a href="/Portals/1/FY27%20P-1%20Budget.pdf">P-1</a> <a href="/about.pdf">About</a>'


def _answer(url, key):
    if "missing" in url:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    return {"success": True, "html": PAGE, "url": url, "metadata": {"finalUrl": url, "title": "Budget books"}}


def test_pages_go_to_context_dev_and_their_files_to_browserbase():
    saved, failed = [], []
    with mock.patch.object(context_fetch, "scrape", _answer), \
            mock.patch.object(context_fetch, "env_value", lambda name: "key"), \
            mock.patch.object(context_fetch, "save", lambda url, body, mime, status, note, final, method: saved.append((url, method)) or {"status": 200, "size": len(body)}), \
            mock.patch.object(context_fetch, "fail", lambda url, note, error, status=None, method="": failed.append((url, error, method)) or {"status": status, "error": error}), \
            mock.patch.object(context_fetch, "browserbase", return_value=0) as files:
        code = context_fetch.fetch(["https://www.secnav.navy.mil/fmc/fmb/", "https://www.gao.gov/missing", "https://www.gao.gov/assets/report.pdf"])
    assert saved == [("https://www.secnav.navy.mil/fmc/fmb/", "context_dev")]
    assert failed == [("https://www.gao.gov/missing", "HTTP 404", "context_dev")]
    assert files.call_args.args[0] == ["https://www.gao.gov/assets/report.pdf", "https://www.secnav.navy.mil/Portals/1/FY27%20P-1%20Budget.pdf"]
    assert code == 1  # one page did not save


def test_no_key_takes_nothing():
    with mock.patch.object(context_fetch, "env_value", lambda name: ""), mock.patch.object(context_fetch, "scrape") as scrape:
        assert context_fetch.fetch(["https://www.gao.gov/"]) == 1
    scrape.assert_not_called()

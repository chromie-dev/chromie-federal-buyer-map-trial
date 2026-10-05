"""war.gov general officer, flag officer and senior executive announcements: stated upcoming moves.

A release gives one officer per paragraph: "Navy Rear Adm. Jane Q. Doe for appointment to the grade of vice
admiral, with assignment as commander, X, Place. Doe is currently serving as director, Y, Place." The assignment
is an announcement (it has not happened yet); the post held now is observed on the release day. A nomination
to a grade alone is not a move, only an observation of the current post. Paragraphs outside these forms go to
the linted model when one is configured.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Callable

from orchestration.gov.competitive.api_cache import cache_get, cache_put
from orchestration.gov.directory_fetch import fetch_official_directory
from orchestration.gov.people.bios import _DATE_RE, _match_date
from orchestration.gov.people.leadership import (
    PERSON,
    agent_changes,
    department_for,
    person_name,
    service_of,
    write_leadership_events,
)
from orchestration.gov.people.writer import RELEASE_SOURCE, normalize_text
from orchestration.supabase_client import get_supabase_client

LISTING_URL = "https://www.war.gov/News/Releases/?Page={page}"
# A backfill reads the officer announcements search, about one page a month, instead of every release.
BACKFILL_URL = "https://www.war.gov/News/Releases/Search/officer%20announcements/?Page={page}"
DEFAULT_LISTING_PAGES = 3
_DELAY_S = 2.0
_DEADLINE_RUNWAY_S = 60
_CURSOR_SOURCE = "people_war_gov_releases"
_TITLE_RE = re.compile(r"(?:General|Flag)\s+Officer\s+(?:Announcement|Assignment|Nomination)|Senior\s+Executive", re.I)
_CURRENT = r"is\s+currently\s+(?:serving|assigned)\s+(?:as|at|to)|currently\s+serves\s+(?:as|at)"
_ENTRY_RE = re.compile(
    rf"^(?P<person>{PERSON})\s+(?:for\s+(?:re)?appointment\s+(?:to\s+the\s+grade\s+of\s+(?P<grade>[a-z ()-]+?)"
    rf"(?:,\s+(?:and\s+)?(?:with|for)\s+assignment\s+as\s+(?P<post>.+?))?|as\s+(?P<post_as>.+?))|"
    rf"(?:will\s+be|has\s+been)\s+(?:assigned|selected|appointed)\s+as\s+(?P<post_assigned>.+?))"
    rf"\.\s+(?:(?:Mr|Ms|Mrs|Dr)\.\s+)?(?P<surname>[A-Z][A-Za-z'’-]+)\s+"
    rf"(?P<verb>{_CURRENT}|most\s+recently\s+served\s+as|previously\s+served\s+as)\s+(?P<current>.+?)\.?$"
)
_CURRENT_RE = re.compile(_CURRENT)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_MOVE_WORDS_RE = re.compile(r"\b(?:assign|nominat|appoint|select)", re.I)


def listing_items(page: str) -> list[dict[str, Any]]:
    """Personnel announcements a release listing page names, newest first."""
    items = []
    for tag in re.findall(r"<[^>]*\barticle-id=\"\d+\"[^>]*>", page, re.S):
        attributes = dict(re.findall(r'(article-[a-z-]+)="([^"]*)"', tag))
        title = html.unescape(attributes.get("article-title", ""))
        if _TITLE_RE.search(title) and attributes.get("article-url"):
            items.append({"id": int(attributes["article-id"]), "title": title, "url": attributes["article-url"]})
    return items


def release_day(title: str, page: str) -> date | None:
    """The announcement day the title names ("... for Sept. 17, 2026"), else the page's dateline."""
    for text in (title, *re.findall(r'(?s)class="date">\s*([^<]+?)\s*<', page)):
        if match := _DATE_RE.search(text):
            return _match_date(match)
    return None


def release_paragraphs(page: str) -> list[str]:
    """The release body, one paragraph per officer; the page renders the body twice."""
    body = re.search(r'(?is)<div class="body">(.*?)</div>', page)
    paragraphs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", part))).strip()
                  for part in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body.group(1) if body else "")]
    return list(dict.fromkeys(paragraph for paragraph in paragraphs if paragraph))


def entry_events(paragraph: str, *, day: date, url: str) -> list[dict[str, Any]] | None:
    """The events one officer paragraph states; None when it is not in the fixed form."""
    match = _ENTRY_RE.match(paragraph)
    if not match:
        return None
    name = person_name(match["person"])
    surname = next((token for token in reversed(name.split()) if normalize_text(token) not in _SUFFIXES), "")
    if normalize_text(surname) != normalize_text(match["surname"]):
        return None
    post = (match["post"] or match["post_as"] or match["post_assigned"] or "").strip(" ,")
    current = match["current"].strip(" ,")
    holds = current if _CURRENT_RE.fullmatch(match["verb"]) else None
    base = {
        "name": name,
        "department": department_for(service_of(match["person"]), post or current),
        "day": day,
        "date_basis": "stated",
        "source_ref": url,
        "source_url": url,
        "quote": paragraph,
        "holds": holds,
    }
    if post and normalize_text(post) != normalize_text(current):
        return [{**base, "event_type": "announcement", "title": post, "previous_title": current}]
    return [{**base, "event_type": "observation", "title": holds}] if holds else []


def read_release(
    page: str,
    *,
    title: str,
    url: str,
    invoke: Callable[..., Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Events from one release page, the fixed form first and the model for what it misses."""
    day = release_day(title, page)
    if day is None:
        return [], {"paragraphs": 0, "unread": 0, "model_calls": 0}
    events, unread = [], []
    for paragraph in release_paragraphs(page):
        found = entry_events(paragraph, day=day, url=url)
        if found is None:
            if _MOVE_WORDS_RE.search(paragraph) and len(paragraph) > 80:
                unread.append(paragraph)
            continue
        events.extend(found)
    calls = 0
    # The preamble ("announced today that the president has made the following nominations") is not an officer.
    unread = [paragraph for paragraph in unread if not re.search(r"announced today|following (?:nomination|assignment)", paragraph)]
    if unread and invoke is not None:
        calls = 1
        for change in agent_changes("\n\n".join(unread), published=day, invoke=invoke):
            name = person_name(change["incoming"])
            if len(name.split()) >= 2:
                events.append({
                    "name": name, "department": department_for(service_of(change["incoming"]), change["post"]),
                    "event_type": "announcement", "title": change["post"], "day": day, "date_basis": "stated",
                    "source_ref": url, "source_url": url, "quote": change["quote"],
                })
    return events, {"paragraphs": len(release_paragraphs(page)), "unread": len(unread), "model_calls": calls}


def default_fetch(url: str) -> bytes:
    return fetch_official_directory(url, 45.0, purpose="people-war-gov-releases")


def default_invoke() -> Callable[..., Any] | None:
    """The structured model call when a key is configured; the fixed-form reader runs either way."""
    if not (os.getenv("CHROMIE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")):
        return None
    from orchestration.gov.llm import invoke_structured

    return invoke_structured


def run_release_monitor(
    sb: Any,
    *,
    fetch: Callable[[str], bytes] | None = None,
    invoke: Callable[..., Any] | None = None,
    now: datetime | None = None,
    pages: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    deadline_epoch: float | None = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """Read personnel announcements newer than the cursor, oldest first, so a stopped run resumes cleanly.

    A backfill reads every announcement on its pages whatever the cursor says, and never moves the cursor back.
    """
    fetch = fetch or default_fetch
    clock = now or datetime.now(timezone.utc)
    limit = pages or int(os.getenv("CHROMIE_PEOPLE_RELEASE_PAGES") or DEFAULT_LISTING_PAGES)
    stored = int((cache_get(sb, _CURSOR_SOURCE, "cursor") or {}).get("newest_id") or 0)
    newest = 0 if backfill else stored
    items: dict[int, dict[str, Any]] = {}
    for page in range(1, limit + 1):
        if page > 1:
            sleep(_DELAY_S)
        listing = fetch((BACKFILL_URL if backfill else LISTING_URL).format(page=page)).decode("utf-8", "replace")
        ids = [int(value) for value in re.findall(r'article-id="(\d+)"', listing)]
        items.update({item["id"]: item for item in listing_items(listing) if item["id"] > newest})
        if not ids or min(ids) <= newest:
            break
    events, stats, read = [], {"paragraphs": 0, "unread": 0, "model_calls": 0}, 0
    for item_id in sorted(items):
        if deadline_epoch and time.time() > deadline_epoch - _DEADLINE_RUNWAY_S:
            break
        sleep(_DELAY_S)
        try:
            page = fetch(items[item_id]["url"]).decode("utf-8", "replace")
            found, counts = read_release(page, title=items[item_id]["title"], url=items[item_id]["url"], invoke=invoke)
        except Exception:
            break  # a fetch or model outage: this release waits for the next run; the cursor stays below it
        events.extend(found)
        stats = {key: stats[key] + counts[key] for key in stats}
        newest, read = item_id, read + 1
    for event in events:
        event["reported_at"] = clock.isoformat()
    result = write_leadership_events(sb, events, source=RELEASE_SOURCE, now=clock)
    cache_put(sb, _CURSOR_SOURCE, "cursor", {"newest_id": max(newest, stored)}, ttl_days=3650)
    return {"releases_listed": len(items), "releases_read": read, **stats, **result}


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=int, default=None, help="listing pages to read")
    parser.add_argument("--backfill", action="store_true", help="read older announcements from the search listing")
    args = parser.parse_args()
    result = run_release_monitor(get_supabase_client(), invoke=default_invoke(), pages=args.pages, backfill=args.backfill)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _main()

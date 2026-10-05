"""DVIDS command and leadership stories: who took or left a post, as the services' own news states it.

DVIDS carries the service news sites' stories (navy.mil, army.mil, af.mil and unit pages). Each story found by a
leadership query is read by the fixed change-of-command forms, then by the linted model when the forms miss a
stated change. DVIDS terms allow commercial use with attribution; only the extracted facts, the stating sentence
and the story URL are stored, never the story.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from orchestration.gov.competitive.api_cache import cache_get, cache_put
from orchestration.gov.people.leadership import change_events, get_json, story_changes, write_leadership_events
from orchestration.gov.people.releases import default_invoke
from orchestration.gov.people.writer import DVIDS_SOURCE
from orchestration.supabase_client import get_supabase_client

API_URL = "https://api.dvidshub.net"
QUERIES = ('"change of command"', '"assumed command"', '"assumption of command"', '"new commander"', '"assumes duties"')
DEFAULT_STORIES_PER_RUN = 60
DEFAULT_MODEL_CALLS = 20
DEFAULT_BACKFILL_DAYS = 90
_PAGE = 50
_DELAY_S = 1.0
_CURSOR_SOURCE = "people_dvids"
# DVIDS stories go up as much as a few weeks after the story's own date.
_LATE_DAYS = 30


def default_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode({**params, "api_key": os.environ["DVIDS_API_KEY"].strip()})
    return get_json(f"{API_URL}/{path}?{query}")


def story_text(body: str) -> str:
    """The story as plain text, one paragraph per line."""
    text = re.sub(r"(?i)</p>|<br\s*/?>", "\n", body or "")
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def _story_day(item: dict[str, Any]) -> str:
    return str(item.get("date") or item.get("date_published"))[:10]


def _published(story: dict[str, Any]) -> date:
    return date.fromisoformat(str(story.get("date_published") or story.get("date"))[:10])


def story_events(story: dict[str, Any], *, invoke: Callable[..., Any] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """Events one DVIDS story states, and whether the model was asked."""
    changes, called = story_changes(story_text(story.get("body") or ""), published=_published(story),
                                    story_unit=story.get("unit_name"), invoke=invoke)
    url = story["url"]
    events = [event for change in changes
              for event in change_events(change, service=story.get("branch"), source_ref=url, source_url=url)]
    return events, called


def run_dvids_monitor(
    sb: Any,
    *,
    get: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    invoke: Callable[..., Any] | None = None,
    now: datetime | None = None,
    stories_per_run: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Read leadership stories not read yet, oldest story date first, under a story and model-call budget.

    DVIDS filters and sorts by the story's own date, and a story often goes up weeks after it. Each run lists a
    trailing window by story date and skips the stories already read in it.
    """
    if get is None and not os.getenv("DVIDS_API_KEY", "").strip():
        return {"status": "skipped", "reason": "DVIDS_API_KEY is not set"}
    get = get or default_get
    clock = now or datetime.now(timezone.utc)
    budget = stories_per_run or int(os.getenv("CHROMIE_PEOPLE_DVIDS_STORIES") or DEFAULT_STORIES_PER_RUN)
    model_budget = int(os.getenv("CHROMIE_PEOPLE_EXTRACT_MAX_CALLS") or DEFAULT_MODEL_CALLS)
    cursor = cache_get(sb, _CURSOR_SOURCE, "cursor") or {}
    trailing = (clock - timedelta(days=_LATE_DAYS)).date().isoformat()
    since = cursor.get("since") or (clock - timedelta(days=int(os.getenv("CHROMIE_PEOPLE_DVIDS_BACKFILL_DAYS")
                                                                or DEFAULT_BACKFILL_DAYS))).date().isoformat()
    done: dict[str, str] = dict(cursor.get("read") or {})

    listed: dict[str, dict[str, Any]] = {}
    for query in QUERIES:
        page = 1
        while True:
            results = get("search", {"q": query, "type": "news", "sort": "date", "max_results": _PAGE, "page": page,
                                     "from_date": f"{since}T00:00:00Z"}).get("results") or []
            listed.update({item["id"]: item for item in results if item.get("id") not in done})
            if len(results) < _PAGE or page * _PAGE >= 1000:
                break
            page += 1
            sleep(_DELAY_S)

    ordered = sorted(listed.values(), key=lambda item: (_story_day(item), item["id"]))
    events, read, model_calls = [], 0, 0
    for item in ordered[:budget]:
        sleep(_DELAY_S)
        try:
            story = get("asset", {"id": item["id"]}).get("results") or {}
        except Exception:
            break  # the rest waits for the next run
        try:
            dated = bool(story.get("url")) and bool(_published(story))
        except ValueError:
            dated = False  # no usable publish date to read the story's dates against: read past it, as one with no url
        if dated:
            try:
                found, called = story_events(story, invoke=invoke if model_calls < model_budget else None)
            except Exception:
                break  # a model outage: this story and the rest wait for the next run, what was read is kept
            events.extend(found)
            model_calls += called
        done[item["id"]] = _story_day(item)
        read += 1
    unread = [_story_day(item) for item in ordered if item["id"] not in done]
    # The next window starts at the oldest unread story, and never later than the late-publishing window.
    since = min([trailing, *unread[:1]])
    for event in events:
        event["reported_at"] = clock.isoformat()
    result = write_leadership_events(sb, events, source=DVIDS_SOURCE, now=clock)
    cache_put(sb, _CURSOR_SOURCE, "cursor", {"since": since, "read": {key: day for key, day in done.items() if day >= since}},
              ttl_days=3650)
    return {"stories_listed": len(ordered), "stories_read": read, "model_calls": model_calls, **result}


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stories", type=int, default=None, help="stories to read this run (a backfill reads more)")
    args = parser.parse_args()
    result = run_dvids_monitor(get_supabase_client(), invoke=default_invoke(), stories_per_run=args.stories)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _main()

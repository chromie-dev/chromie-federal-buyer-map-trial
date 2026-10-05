"""Senate nominations from api.congress.gov: stated future posts, confirmations and the predecessors they replace.

A civilian nomination reads "Jane Doe, of Virginia, to be an Assistant Secretary of the Navy, vice John Roe,
resigned.": an announcement for the nominee and, when a reason is given, a departure for the predecessor. A
confirmation is the appointment. Military lists name officers for a grade; only general and flag grades are
read, and a list names a post only when its introduction says "for appointment as <post> and appointment ...".
A grade alone becomes a promotion once confirmed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from orchestration.gov.competitive.api_cache import cache_get, cache_put
from orchestration.gov.people.leadership import department_for, get_json, write_leadership_events
from orchestration.gov.people.writer import NOMINATION_SOURCE, normalize_text
from orchestration.supabase_client import get_supabase_client

API_URL = "https://api.congress.gov/v3"
DEFAULT_CALLS_PER_RUN = 400
DEFAULT_BACKFILL_DAYS = 730
_PAGE = 250
_CURSOR_SOURCE = "people_congress_nominations"
_ORGANIZATIONS = {"department of defense", "navy", "marine corps", "army", "air force", "space force",
                  "department of homeland security", "coast guard"}
_FLAG_GRADE_RE = re.compile(r"^(?:rear admiral(?: \(lower half\))?|vice admiral|admiral|brigadier general|"
                            r"major general|lieutenant general|general)$", re.I)
_DESCRIPTION_RE = re.compile(r"^(?P<name>.+?), of (?:the )?[^,]+, (?:.+?, )?to be (?P<rest>.+?)\.?\s*(?:\(Reappointment\))?$")
_TERM_RE = re.compile(r",?\s+for (?:a term|the (?:remainder of the )?term)\b.*$", re.I)
_DEPARTED_RE = re.compile(r"^(?:resigned|retired|retiring|deceased|term expired)\b", re.I)
_RETRACTED_RE = re.compile(r"withdrawal|returned to the President", re.I)
_INTRO_POST_RE = re.compile(r"for appointment as (?P<post>.+?) and appointment", re.I)


class CallBudget(Exception):
    """The run's congress.gov call budget is spent."""


def default_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    # get_json sends a browser-like user agent: api.congress.gov's edge refuses Python's default (Cloudflare 1010).
    query = urllib.parse.urlencode({**params, "api_key": os.environ["DATA_GOV_API_KEY"].strip(), "format": "json"})
    return get_json(f"{API_URL}/{path}?{query}")


def _ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def nomination_url(item: dict[str, Any]) -> str:
    # ponytail: the nomination page, not its part; congress.gov refuses this machine, so the part path is unverified.
    return f"https://www.congress.gov/nomination/{_ordinal(int(item['congress']))}-congress/{item['number']}"


def _api_path(url: str) -> str:
    return url.split("/v3/", 1)[1].split("?", 1)[0]


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _confirmed_on(item: dict[str, Any]) -> date | None:
    action = item.get("latestAction") or {}
    return _day(action.get("actionDate")) if str(action.get("text") or "").startswith("Confirmed by the Senate") else None


def _status(item: dict[str, Any]) -> str:
    return "retracted" if _RETRACTED_RE.search(str((item.get("latestAction") or {}).get("text") or "")) else "current"


def civilian_events(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The nominee's announcement and confirmation, and the predecessor's stated departure."""
    description = re.sub(r"\s+", " ", str(item.get("description") or "")).strip()
    match = _DESCRIPTION_RE.match(description)
    received = _day(item.get("receivedDate"))
    if not match or not received:
        return []
    post, _, vice = match["rest"].partition(", vice ")
    post = _TERM_RE.sub("", re.sub(r"^(?:an?|the)\s+", "", post.strip())).strip(" ,")
    # The post names its department ("Assistant Secretary of the Navy"); else the nominating organization does.
    department = department_for(None, f"{post} {item.get('organization') or ''}")
    base = {"department": department, "title": post, "source_ref": f"congress:{item['citation']}",
            "source_url": nomination_url(item), "quote": description}
    events = [{**base, "name": match["name"].strip(), "event_type": "announcement", "day": received,
               "date_basis": "stated", "status": _status(item)}]
    if confirmed := _confirmed_on(item):
        events.append({**base, "name": match["name"].strip(), "event_type": "appointment", "day": confirmed,
                       "date_basis": "stated", "started": False})
    predecessor, _, reason = vice.partition(", ")
    if predecessor and _DEPARTED_RE.match(reason.strip()):
        # The nomination says the post is vacated, not when: the departure is observed by the received date.
        events.append({**base, "name": predecessor.strip(" ."), "event_type": "departure", "day": received,
                       "date_basis": "observed"})
    return events


def military_events(item: dict[str, Any], detail: dict[str, Any], nominees: Callable[[str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """General and flag officer lists: a named post is announced then appointed; a grade alone is a promotion."""
    received = _day(item.get("receivedDate"))
    confirmed = _confirmed_on(item)
    events: list[dict[str, Any]] = []
    for group in detail.get("nominees") or []:
        grade = str(group.get("positionTitle") or "").strip()
        if not received or not _FLAG_GRADE_RE.match(grade):
            continue
        intro = str(group.get("introText") or "")
        post_match = _INTRO_POST_RE.search(intro)
        post = post_match["post"].strip() if post_match else None
        if not post and not confirmed:
            continue
        for person in nominees(group["url"]):
            name = " ".join(str(person.get(field) or "").strip() for field in ("firstName", "middleName", "lastName", "suffix"))
            name = re.sub(r"\s+", " ", name).strip()
            if len(name.split()) < 2:
                continue
            # An officer keeps their service's identity whatever joint post they are named to.
            base = {"name": name, "department": department_for(group.get("organization") or item.get("organization"), post or ""),
                    "source_ref": f"congress:{item['citation']}", "source_url": nomination_url(item), "quote": intro.strip()}
            if post:
                events.append({**base, "event_type": "announcement", "title": post, "day": received, "date_basis": "stated",
                               "status": _status(item)})
                if confirmed:
                    events.append({**base, "event_type": "appointment", "title": post, "day": confirmed, "date_basis": "stated",
                                   "started": False})
            else:
                events.append({**base, "event_type": "promotion", "title": grade, "day": confirmed, "date_basis": "stated"})
    return events


def run_nomination_monitor(
    sb: Any,
    *,
    get: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    now: datetime | None = None,
    calls_per_run: int | None = None,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Read nominations updated since the cursor, oldest first, under a call budget; reruns rewrite nothing new."""
    if get is None and not os.getenv("DATA_GOV_API_KEY", "").strip():
        return {"status": "skipped", "reason": "DATA_GOV_API_KEY is not set"}
    get = get or default_get
    clock = now or datetime.now(timezone.utc)
    budget = calls_per_run or int(os.getenv("CHROMIE_PEOPLE_NOMINATION_CALLS") or DEFAULT_CALLS_PER_RUN)
    calls = 0

    def call(path: str, **params: Any) -> dict[str, Any]:
        nonlocal calls
        if calls >= budget:
            raise CallBudget
        calls += 1
        return get(path, params)

    stored = (cache_get(sb, _CURSOR_SOURCE, "cursor") or {}).get("updated_since")
    start = since or (datetime.fromisoformat(stored) if stored else clock - timedelta(
        days=int(os.getenv("CHROMIE_PEOPLE_NOMINATIONS_BACKFILL_DAYS") or DEFAULT_BACKFILL_DAYS)))
    window = {"fromDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "toDateTime": clock.strftime("%Y-%m-%dT%H:%M:%SZ")}
    items: list[dict[str, Any]] = []
    try:
        while True:
            page = call("nomination", **window, limit=_PAGE, offset=len(items)).get("nominations") or []
            items.extend(page)
            if len(page) < _PAGE:
                break
    except CallBudget:
        return {"calls": calls, "listed": len(items), "read": 0, "events": 0, "moves": 0, "budget_spent": True}

    relevant = sorted((item for item in items if normalize_text(item.get("organization")) in _ORGANIZATIONS),
                      key=lambda item: str(item.get("updateDate") or ""))
    events: list[dict[str, Any]] = []
    cursor, read, spent = start.isoformat(), 0, False
    for item in relevant:
        try:
            if (item.get("nominationType") or {}).get("isMilitary"):
                detail = call(_api_path(item["url"])).get("nomination") or {}
                found = military_events(item, detail, lambda url: call(_api_path(url), limit=_PAGE).get("nominees") or [])
            else:
                found = civilian_events(item)
        except CallBudget:
            spent = True
            break
        events.extend(found)
        read += 1
        cursor = str(item.get("updateDate") or cursor).replace("Z", "+00:00")
    if not spent:
        cursor = clock.isoformat()
    for event in events:
        event["reported_at"] = clock.isoformat()
    result = write_leadership_events(sb, events, source=NOMINATION_SOURCE, now=clock)
    cache_put(sb, _CURSOR_SOURCE, "cursor", {"updated_since": cursor}, ttl_days=3650)
    return {"calls": calls, "listed": len(items), "relevant": len(relevant), "read": read, "budget_spent": spent, **result}


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calls", type=int, default=None, help="congress.gov calls this run (a backfill uses more)")
    args = parser.parse_args()
    print(json.dumps(run_nomination_monitor(get_supabase_client(), calls_per_run=args.calls), indent=2, default=str))


if __name__ == "__main__":
    _main()

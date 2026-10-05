"""Where government people went after government: licensed people data, read through the vendors' own APIs.

Coresignal checks each person a company tracks once a month; Orange Slice sweeps the alumni of each office a
company tracks. Both are licensed datasets behind keyed APIs; only their read-only search and collect calls are
made, never a logged-in page and never a contact lookup (e-mail or phone).

A vendor profile joins a contact only when four things agree: the name, an experience at the contact's office,
dates overlapping what Chromie's own records observed there, and a place matching the office's address. Anything
less is skipped, never merged. A joined profile that has left the office for a job outside government becomes a
departure in the move ledger: the post left, the month the profile states, the new employer as evidence. It is
current when Chromie's records agree (no sighting at the office well after the stated month) and review otherwise.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from orchestration.gov.competitive.api_cache import cache_get, cache_put
from orchestration.gov.people.moves import ROLE_TITLES
from orchestration.gov.people.writer import _day, _paged, _select_in, _upsert_batches, record_moves

CORESIGNAL = "coresignal"
ORANGE_SLICE = "orange_slice"
CORESIGNAL_URL = "https://api.coresignal.com/cdapi/v2/employee_base"
CORESIGNAL_CREDITS_PER_COLLECT = 10
DEFAULT_CORESIGNAL_COLLECTS = 30
DEFAULT_ORANGE_SLICE_OFFICES = 10
DEFAULT_ALUMNI_MONTHS = 36
ALUMNI_LIMIT = 500
SURNAMES_PER_QUERY = 300
# A search naming more profiles than this is a common name; collecting them all would spend credits on guesses.
MAX_SEARCH_HITS = 3
# Chromie still seeing the person at the office this long after the month a profile says they left is a
# contradiction: the move goes to review.
_AGREE_SLACK = timedelta(days=60)
_CHECK_TTL_DAYS = 25
_CACHE_SOURCE = "people_vendors"
_CORESIGNAL_DELAY_S = 0.25

_US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "DC": "district of columbia", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan",
    "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas",
    "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming",
}
# LinkedIn metro names that cross state lines. ponytail: only the metros around large defense offices; a profile
# placed by a single-state metro name ("Greater Seattle Area") is unplaced and skipped, add metros as misses show.
_METROS = {
    "washington dc-baltimore": {"DC", "MD", "VA"},
    "washington d.c. metro": {"DC", "MD", "VA"},
    "greater philadelphia": {"PA", "NJ", "DE"},
    "new york city metropolitan": {"NY", "NJ", "CT"},
    "greater boston": {"MA", "NH", "RI"},
    "norfolk-virginia beach": {"VA", "NC"},
    "virginia beach-norfolk": {"VA", "NC"},
}
_DC_RE = re.compile(r"\bwashington,?\s+(?:d\.?\s?c\.?(?=\W|$)|district of columbia\b)|\bdistrict of columbia\b")
_STATE_CODE_RE = re.compile(r"\b([A-Z]{2})\b")
# Words that name no particular office, so sharing them proves nothing.
_GENERIC_WORDS = frozenset("""
the and for with office offices command commands center centre department dept division directorate branch group
activity agency headquarters contracting contracts contract acquisition naval navy army force forces marine marines
corps united states federal systems system support services service logistics fleet regional region district
detachment site base station program programs executive staff national defense joint warfare
""".split())
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "pmp", "phd", "pe", "cpa", "mba", "ret", "usn", "usmc", "usa", "usaf"})
# Legal forms and trade words mark an employer; checked first so "Accenture Federal Services" is not government.
_ORG_FORM_RE = re.compile(r"\b(?:inc|llc|l\.l\.c|corp|corporation|company|companies|ltd|lp|llp|plc|pllc|group|associates|partners|"
                          r"consulting|services|solutions|technologies|technology)\b", re.I)
_GOVERNMENT_RE = re.compile(r"\b(?:department of|dept\.? of|united states|u\.?\s?s\.?\s+(?:navy|army|air force|marine corps|"
                            r"coast guard|space force)|navy|naval|army|air force|marine corps|space force|coast guard|"
                            r"national guard|dod|nasa|federal|government|command|peo|program executive office|agency|"
                            r"administration|bureau|commission|congress|senate)\b", re.I)
_RETIRED_RE = re.compile(r"\bretired?\b", re.I)


# --- matching -------------------------------------------------------------------------------------------------

def _words(value: object) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower())


def _name_words(name: object) -> list[str]:
    text = str(name or "").strip()
    head, _, tail = text.partition(",")
    # "Lee, Pat Q" is last-name first; "Pat Lee, PMP" only carries a credential after the comma.
    text = f"{tail} {head}" if tail and not set(_words(tail)) <= _SUFFIXES else head
    return [word for word in _words(text) if word not in _SUFFIXES and len(word) > 1]


def name_key(first: object, last: object = None) -> tuple[str, str] | None:
    """(first, last) of a name, without credentials, suffixes or middle names: "Thomas, PMP" -> thomas."""
    if last is None:
        words = _name_words(first)
        return (words[0], words[-1]) if len(words) >= 2 else None
    first_words = [word for word in _words(first) if len(word) > 1]
    last_words = [word for word in _words(str(last or "").split(",")[0]) if word not in _SUFFIXES and len(word) > 1]
    return (first_words[0], last_words[-1]) if first_words and last_words else None


def office_tokens(office: dict[str, Any]) -> set[str]:
    """The words that pick out this office in an employer name: NAVSEA HQ -> {navsea}."""
    names = [office.get("name"), office.get("acronym"), *(office.get("aliases") or [])]
    return {word for name in names for word in _words(name)
            if len(word) >= 4 and word not in _GENERIC_WORDS and not any(ch.isdigit() for ch in word)}


def company_matches(company: object, office: dict[str, Any]) -> bool:
    return bool(set(_words(company)) & office_tokens(office))


def place_states(text: object) -> set[str]:
    """US states a free-text location names: "Washington DC-Baltimore Area" -> {DC, MD, VA}."""
    raw = str(text or "")
    lowered = raw.lower()
    states: set[str] = set()
    for metro, codes in _METROS.items():
        if metro in lowered:
            states |= codes
            lowered = lowered.replace(metro, " ")
    if _DC_RE.search(lowered):
        states.add("DC")
        lowered = _DC_RE.sub(" ", lowered)
    states |= {code for code, name in _US_STATES.items() if re.search(rf"\b{name}\b", lowered)}
    states |= {code for code in _STATE_CODE_RE.findall(raw) if code in _US_STATES}
    return states


def place_agrees(office: dict[str, Any], *locations: object) -> bool:
    """An unplaced office agrees with nothing, so an office no notice has placed never joins a profile."""
    state = str(office.get("address_state") or "").upper()
    city = str(office.get("address_city") or "").lower()
    if not state:
        return False
    return any(state in place_states(text) or (city and city in str(text).lower()) for text in locations if text)


def _month(value: object) -> tuple[date, date] | None:
    """The span a vendor date names: "January 2021" a month, "2021" a year, an ISO date its month."""
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}", text):
        return date(int(text), 1, 1), date(int(text), 12, 31)
    for pattern in ("%B %Y", "%b %Y", "%Y-%m-%d", "%Y-%m"):
        try:
            day = datetime.strptime(text[:10] if pattern == "%Y-%m-%d" else text, pattern).date()
        except ValueError:
            continue
        return day.replace(day=1), day.replace(day=monthrange(day.year, day.month)[1])
    return None


def _month_end(day: date) -> date:
    return day.replace(day=monthrange(day.year, day.month)[1])


def _span(experience: dict[str, Any], today: date) -> tuple[date, date] | None:
    start = _month(experience.get("start"))
    end = _month(experience.get("end"))
    if not start:
        return None
    return start[0], end[1] if end else today


def _observed_span(post: dict[str, Any]) -> tuple[date, date] | None:
    first = _day(post.get("valid_from") or post.get("first_observed_at"))
    last = _day(post.get("valid_to") or post.get("last_observed_at"))
    if not (first and last):
        return None
    return date.fromisoformat(first), date.fromisoformat(last)


def employer_side(company: object) -> str | None:
    """industry for a named firm, government for a public body, None when the name says neither ("NSWC Carderock",
    "Maritime Industrial Base Program", "Ferguson"): a move there is not stated as a move to industry."""
    # ponytail: an employer-name reading; "Navy Federal Credit Union" reads as government. The vendor's company
    # industry would place more, but unlinked employers (most of the unplaced ones) carry none.
    text = str(company or "")
    if _ORG_FORM_RE.search(text):
        return "industry"
    return "government" if _GOVERNMENT_RE.search(text) else None


def match_profile(contact: dict[str, Any], posts: list[dict[str, Any]], profile: dict[str, Any], *, today: date) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The (post, office experience) a vendor profile shares with a contact, or None unless all four agree."""
    if not name_key(contact.get("name")) or name_key(contact.get("name")) != name_key(profile.get("first_name"), profile.get("last_name")):
        return None
    for post in posts:
        office = post.get("office") or {}
        ours = _observed_span(post)
        if not (office and ours):
            continue
        for experience in profile.get("experiences") or []:
            theirs = _span(experience, today)
            if (theirs and company_matches(experience.get("company"), office)
                    and theirs[0] <= ours[1] and ours[0] <= theirs[1]
                    and place_agrees(office, experience.get("location"), profile.get("location"))):
                return post, experience
    return None


def departure(profile: dict[str, Any], office_experience: dict[str, Any], *, today: date) -> dict[str, Any] | None:
    """What the profile says the person did after the office: a job outside government, retirement, or None."""
    left = _month(office_experience.get("end"))
    if not left:
        return None  # still at the office, as the profile tells it
    others = [item for item in profile.get("experiences") or [] if item is not office_experience]
    # A later government post means the move out of this office was inside government; other sources cover it.
    for item in others:
        span = _span(item, today)
        if span and span[1] > left[1] and employer_side(item.get("company")) == "government" and not _RETIRED_RE.search(
                f"{item.get('title') or ''} {item.get('company') or ''}"):
            return None
    current = [item for item in others if item.get("current")]
    jobs = [item for item in current if employer_side(item.get("company")) != "government"
            and not _RETIRED_RE.search(f"{item.get('title') or ''} {item.get('company') or ''}")]
    if jobs:
        job = max(jobs, key=lambda item: (_month(item.get("start")) or (date.min, date.min))[0])
        now = ", ".join(part for part in (job.get("title"), job.get("company")) if part)
        return {"left": left[0], "now": now, "employer": job.get("company"), "since": job.get("start"),
                "outside_government": employer_side(job.get("company")) == "industry" or None}
    if any(_RETIRED_RE.search(f"{item.get('title') or ''} {item.get('company') or ''}") for item in current):
        return {"left": left[0], "now": "Retired from government service", "employer": None, "since": None,
                "outside_government": True}
    return None


# --- vendor records -------------------------------------------------------------------------------------------

def coresignal_profile(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "vendor": CORESIGNAL,
        "profile_id": str(record.get("id") or ""),
        "first_name": record.get("first_name"),
        "last_name": record.get("last_name"),
        "url": record.get("profile_url") or record.get("url"),
        "location": record.get("location"),
        "checked_at": record.get("checked_at") or record.get("updated_at"),
        "experiences": [
            {"company": item.get("company_name"), "title": item.get("title"), "start": item.get("date_from"),
             "end": item.get("date_to"), "current": item.get("is_current") == 1 or not item.get("date_to"),
             "location": item.get("location")}
            for item in record.get("experience") or []
        ],
    }


def orange_slice_profile(row: dict[str, Any], office_company: str) -> dict[str, Any]:
    """One alumni row: the office position and the profile's current employer."""
    return {
        "vendor": ORANGE_SLICE,
        "profile_id": str(row.get("id") or ""),
        "first_name": row.get("first_name"),
        "last_name": row.get("last_name"),
        "url": row.get("public_profile_url"),
        "location": row.get("location_name"),
        "checked_at": None,
        "experiences": [
            {"company": office_company, "title": row.get("office_title"), "start": row.get("start_date"),
             "end": row.get("end_date"), "current": False, "location": row.get("locality")},
            {"company": row.get("now_org"), "title": row.get("now_title"), "start": None, "end": None,
             "current": True, "location": row.get("location_name")},
        ],
    }


# --- ledger ---------------------------------------------------------------------------------------------------

def _post_title(post: dict[str, Any]) -> str:
    office = (post.get("office") or {}).get("name") or ""
    role = post.get("raw_title") or ROLE_TITLES.get(post.get("role_type"), "Government staff")
    return f"{role}, {office}" if office else role


def departure_row(contact: dict[str, Any], post: dict[str, Any], experience: dict[str, Any], profile: dict[str, Any],
                  left: dict[str, Any], *, timestamp: str) -> dict[str, Any] | None:
    office = post.get("office") or {}
    url = str(profile.get("url") or "")
    if not re.match(r"^https?://", url) or not profile.get("profile_id") or not contact.get("identity_key"):
        return None
    left_end = _month_end(left["left"])
    last_seen = _observed_span(post)[1]
    agrees = last_seen <= left_end + _AGREE_SLACK
    return {
        "gov_contact_id": contact["id"],
        "person_identity_key": contact["identity_key"],
        "name": contact["name"],
        "agency_name": contact.get("agency") or office.get("name") or "Government office",
        "title": _post_title(post),
        "organization_id": office.get("id"),
        "event_type": "departure",
        "effective_date": left["left"].isoformat(),
        "reported_at": timestamp,
        "source_provider": profile["vendor"],
        "source_ref": f"{profile['vendor']}:{profile['profile_id']}",
        "source_url": url,
        "date_basis": "stated",
        # A move to an employer the name cannot place, or one our records contradict, waits for review.
        "status": "current" if agrees and left["outside_government"] else "review",
        "evidence": [
            {"source": profile["vendor"], "date_precision": "month", "left_office": left["left"].isoformat()[:7],
             "office_title": experience.get("title"), "now": left["now"], "employer": left["employer"],
             "since": left["since"], "outside_government": left["outside_government"],
             "profile_checked": str(profile.get("checked_at") or "")[:10] or None},
            {"office_code": office.get("source_ref"), "first_on": _day(post.get("first_observed_at")),
             "last_on": last_seen.isoformat(), "source_url": post.get("source_url")},
        ],
    }


def write_departures(sb: Any, found: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]],
                     *, now: datetime) -> dict[str, int]:
    """Ledger rows, vendor identifiers and closed posts for joined profiles; reruns rewrite the same rows."""
    timestamp = now.isoformat()
    candidates = [(contact, post, profile, row) for contact, post, experience, profile, left in found
                  if (row := departure_row(contact, post, experience, profile, left, timestamp=timestamp))]
    if not candidates:
        return {"departures": 0, "departures_review": 0, "positions_closed": 0, "profile_conflicts": 0}
    # A profile already joined to another contact stays where it is, and one this batch joins to two contacts joins
    # neither: two contacts never share one profile. One person matched through two of their posts is one join.
    held = {row["value"]: str(row["contact_id"]) for row in _select_in(
        sb, "gov_contact_identifiers", "contact_id,value", "value", {row["source_ref"] for *_, row in candidates})}
    owners: dict[str, set[str]] = {}
    for contact, *_rest, row in candidates:
        owners.setdefault(row["source_ref"], set()).add(str(contact["id"]))
    kept = [(contact, post, profile, row) for contact, post, profile, row in candidates
            if owners[row["source_ref"]] == {held.get(row["source_ref"], str(contact["id"]))}]
    moves = {(row["source_ref"], row["person_identity_key"]): row for *_, row in kept}
    prior = {(row["source_ref"], row["person_identity_key"]): row["reported_at"] for row in _select_in(
        sb, "gov_contact_role_history", "source_ref,person_identity_key,reported_at", "source_ref",
        {key[0] for key in moves})}
    for key, row in moves.items():
        row["reported_at"] = prior.get(key) or row["reported_at"]
    recorded = record_moves(sb, list(moves.values()), now=now)
    _upsert_batches(sb, "gov_contact_identifiers", list({
        row["source_ref"]: {"contact_id": contact["id"], "kind": "vendor_profile", "value": row["source_ref"],
                            "source": profile["vendor"], "basis": "rule", "first_seen": timestamp, "last_seen": timestamp,
                            "updated_at": timestamp}
        for contact, _post, profile, row in kept
    }.values()), "kind,value")
    # Posts the person left close on their own last observed day; a later sighting reopens them.
    closing = {str(contact["id"]): row for contact, _post, _profile, row in kept if row["status"] == "current"}
    closed = 0
    for position in _select_in(sb, "gov_contact_positions", "id,contact_id,valid_to,last_observed_at", "contact_id", closing):
        row = closing[str(position["contact_id"])]
        left_end = _month_end(date.fromisoformat(row["effective_date"]))
        if position.get("valid_to") is None and date.fromisoformat(_day(position["last_observed_at"])) <= left_end + _AGREE_SLACK:
            sb.table("gov_contact_positions").update(
                {"valid_to": _day(position["last_observed_at"]), "updated_at": timestamp}).eq("id", position["id"]).execute()
            closed += 1
    return {"departures": recorded, "departures_review": sum(row["status"] == "review" for row in moves.values()),
            "positions_closed": closed, "profile_conflicts": len(candidates) - len(kept)}


# --- who to check ---------------------------------------------------------------------------------------------

def _people(sb: Any, contact_ids: Iterable[str]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Contacts with their posts, each post carrying its office row."""
    ids = sorted(set(contact_ids))
    contacts = _select_in(sb, "gov_contacts", "id,name,agency,identity_key", "id", ids)
    positions = _select_in(sb, "gov_contact_positions", "*", "contact_id", ids)
    offices = {str(row["id"]): row for row in _select_in(
        sb, "gov_organizations", "id,name,acronym,aliases,source_ref,address_city,address_state", "id",
        {str(row["organization_id"]) for row in positions})}
    posts: dict[str, list[dict[str, Any]]] = {}
    for row in positions:
        posts.setdefault(str(row["contact_id"]), []).append({**row, "office": offices.get(str(row["organization_id"]))})
    return [(contact, posts.get(str(contact["id"]), [])) for contact in contacts]


def tracked_contact_ids(sb: Any) -> list[str]:
    return sorted({str(row["contact_id"]) for row in _paged(
        lambda: sb.table("gov_person_relationships").select("contact_id").order("id"))})


def tracked_office_ids(sb: Any) -> list[str]:
    return sorted({str(row["organization_id"]) for row in _paged(
        lambda: sb.table("gov_tracked_offices").select("organization_id").order("id"))})


# --- Coresignal -----------------------------------------------------------------------------------------------

def coresignal_request(path: str, body: dict[str, Any] | None = None) -> tuple[Any, int | None]:
    """One Coresignal call: (JSON, credits left). The key never leaves the request header."""
    request = urllib.request.Request(
        f"{CORESIGNAL_URL}/{path}", data=json.dumps(body).encode() if body is not None else None,
        # The API's edge refuses urllib's default User-Agent (Cloudflare error 1010).
        headers={"apikey": os.environ["CORESIGNAL_API_KEY"].strip(), "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "chromie-runner"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=60) as response:
        credits = response.headers.get("x-credits-remaining")
        return json.loads(response.read().decode()), int(credits) if credits and credits.isdigit() else None


def coresignal_query(contact: dict[str, Any], posts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Free search: the person's name with an experience naming one of their offices."""
    key = name_key(contact.get("name"))
    tokens = sorted({token for post in posts for token in office_tokens(post.get("office") or {})})
    if not (key and tokens):
        return None
    return {"query": {"bool": {"must": [
        {"match": {"full_name": {"query": f"{key[0]} {key[1]}", "operator": "and"}}},
        {"nested": {"path": "experience", "query": {"bool": {
            "should": [{"match": {"experience.company_name": token}} for token in tokens], "minimum_should_match": 1}}}},
    ]}}}


def run_coresignal_check(sb: Any, *, request: Callable[..., tuple[Any, int | None]] | None = None,
                         now: datetime | None = None, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Check every tracked person once a month; collects stop at CORESIGNAL_MONTHLY_COLLECTS."""
    if request is None and not os.getenv("CORESIGNAL_API_KEY"):
        return {"status": "skipped", "reason": "CORESIGNAL_API_KEY is not set"}
    request = request or coresignal_request
    clock = now or datetime.now(timezone.utc)
    cap = int(os.getenv("CORESIGNAL_MONTHLY_COLLECTS") or DEFAULT_CORESIGNAL_COLLECTS)
    stats = {"people": 0, "searched": 0, "ambiguous": 0, "collected": 0, "matched": 0, "credits_left": None}
    found, checked = [], []
    status = "complete"
    for contact, posts in _people(sb, tracked_contact_ids(sb)):
        stats["people"] += 1
        if cache_get(sb, _CACHE_SOURCE, f"{CORESIGNAL}:{contact['id']}"):
            continue
        query = coresignal_query(contact, posts)
        if not query:
            continue
        try:
            ids, _credits = request("search/es_dsl", query)
            stats["searched"] += 1
            sleep(_CORESIGNAL_DELAY_S)
            ids = [str(item) for item in ids or []]
            if len(ids) > MAX_SEARCH_HITS:
                stats["ambiguous"] += 1
                ids = []
            if stats["collected"] + len(ids) > cap:
                status = "paused"
                break
            for profile_id in ids:
                record, credits = request(f"collect/{profile_id}")
                stats["collected"] += 1
                stats["credits_left"] = credits if credits is not None else stats["credits_left"]
                sleep(_CORESIGNAL_DELAY_S)
                profile = coresignal_profile(record)
                if (joined := match_profile(contact, posts, profile, today=clock.date())) and (
                        left := departure(profile, joined[1], today=clock.date())):
                    found.append((contact, joined[0], joined[1], profile, left))
                    stats["matched"] += 1
        except Exception as error:  # an outage: what was checked is written and remembered, the rest waits
            status, stats["error"] = "stopped", f"{type(error).__name__}: {error}"[:200]
            break
        checked.append(f"{CORESIGNAL}:{contact['id']}")
    written = write_departures(sb, found, now=clock)
    # Remembered only once written: a failed write leaves the month's paid checks to run again, not lost.
    for key in checked:
        cache_put(sb, _CACHE_SOURCE, key, {"checked": clock.date().isoformat()}, ttl_days=_CHECK_TTL_DAYS)
    return {"status": status, "credits_spent": stats["collected"] * CORESIGNAL_CREDITS_PER_COLLECT, **stats, **written}


# --- Orange Slice ---------------------------------------------------------------------------------------------

class OrangeSliceClient:
    """MCP over HTTP, limited to the read-only LinkedIn SQL tool: the server also offers CRM writes and messaging."""

    TOOL = "linkedin_search"

    def __init__(self, url: str, key: str) -> None:
        self.url, self.key, self.session, self.calls = url, key, None, 0

    def _post(self, body: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            self.session = response.headers.get("Mcp-Session-Id") or self.session
            raw = response.read().decode()
        for line in raw.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        return json.loads(raw) if raw.strip() else None

    def sql(self, statement: str) -> list[dict[str, Any]]:
        if self.session is None:
            self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "chromie-people", "version": "1"}}})
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.calls += 1
        reply = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": self.TOOL, "arguments": {"sql": statement}}})
        result = (reply or {}).get("result") or {}
        if result.get("isError"):
            raise RuntimeError(f"Orange Slice query failed: {str(result.get('content'))[:200]}")
        text = next((item.get("text") for item in result.get("content") or [] if item.get("type") == "text"), "{}")
        return (json.loads(text) or {}).get("rows") or []


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def office_company(client: Any, office: dict[str, Any]) -> dict[str, Any] | None:
    """The LinkedIn company an office's distinctive word is the page slug of (navsea -> NAVSEA), if any."""
    for token in sorted(office_tokens(office)):
        rows = client.sql(
            "select c.id, c.company_name from linkedin_company_slug s join linkedin_company c on c.id = s.linkedin_company_id "
            f"where s.slug_key64 = key64({_sql_text(token)}) limit 1")
        if rows and company_matches(rows[0].get("company_name"), office):
            return {"id": int(rows[0]["id"]), "name": rows[0]["company_name"]}
    return None


def surname_forms(name: object) -> set[str]:
    """The ways a profile may write the surname of a name Chromie holds: "Pat Q Van Dyke" -> dyke, van dyke, van-dyke."""
    # ponytail: "Lee Jr." with no comma is missed here although the name join would take it; add suffix forms if seen.
    words = _name_words(name)
    if len(words) < 2:
        return set()
    return {words[-1], *([f"{words[-2]} {words[-1]}", f"{words[-2]}-{words[-1]}"] if len(words) > 2 else [])}


def alumni_sql(company_id: int, since: date, surnames: list[str]) -> str:
    """The office's alumni since a date with one of the surnames. The vendor has no ORDER BY, so a capped query
    returns an arbitrary slice; asking only for people Chromie saw at the office keeps the slice whole."""
    return (
        "select lp.id, lp.first_name, lp.last_name, lp.title as now_title, lp.org as now_org, lp.location_name, "
        "pos.title as office_title, pos.locality, pos.start_date, pos.end_date, lp.public_profile_url "
        "from linkedin_profile_position3 pos join linkedin_profile lp on lp.id = pos.linkedin_profile_id "
        f"where pos.linkedin_company_id = {int(company_id)} and pos.end_date >= {_sql_text(since.isoformat())} "
        f"and lp.linkedin_company_id <> {int(company_id)} and lp.location_country_code = 'US' "
        f"and lower(split_part(lp.last_name, ',', 1)) in ({', '.join(map(_sql_text, surnames))}) limit {ALUMNI_LIMIT}"
    )


def run_orange_slice_sweep(sb: Any, *, client: Any | None = None, now: datetime | None = None) -> dict[str, Any]:
    """For each tracked office, its LinkedIn alumni who left recently, joined to the people Chromie saw there."""
    if client is None:
        url, key = os.getenv("ORANGESLICE_MCP_URL"), os.getenv("ORANGESLICE_API_KEY")
        if not (url and key):
            return {"status": "skipped", "reason": "ORANGESLICE_MCP_URL or ORANGESLICE_API_KEY is not set"}
        client = OrangeSliceClient(url, key)
    clock = now or datetime.now(timezone.utc)
    cap = int(os.getenv("ORANGESLICE_MONTHLY_OFFICES") or DEFAULT_ORANGE_SLICE_OFFICES)
    months = int(os.getenv("ORANGESLICE_ALUMNI_MONTHS") or DEFAULT_ALUMNI_MONTHS)
    since = (clock.date() - timedelta(days=round(months * 30.44))).replace(day=1)
    offices = _select_in(sb, "gov_organizations", "id,name,acronym,aliases,source_ref,address_city,address_state", "id",
                         tracked_office_ids(sb))
    stats = {"offices": len(offices), "swept": 0, "no_company": 0, "alumni": 0, "truncated": 0, "matched": 0}
    found, swept = [], []
    status = "complete"
    for office in sorted(offices, key=lambda row: str(row["id"])):
        if cache_get(sb, _CACHE_SOURCE, f"{ORANGE_SLICE}:{office['id']}"):
            continue
        if stats["swept"] >= cap:
            status = "paused"
            break
        seen = _select_in(sb, "gov_contact_positions", "contact_id", "organization_id", [str(office["id"])])
        people = _people(sb, {str(row["contact_id"]) for row in seen})
        by_name: dict[tuple[str, str], list[tuple[dict[str, Any], list[dict[str, Any]]]]] = {}
        for contact, posts in people:
            if key := name_key(contact.get("name")):
                by_name.setdefault(key, []).append((contact, [post for post in posts if str(post["organization_id"]) == str(office["id"])]))
        surnames = sorted({form for contact, _posts in people for form in surname_forms(contact.get("name"))})
        rows = []
        try:
            cached = cache_get(sb, _CACHE_SOURCE, f"{ORANGE_SLICE}:company:{office['id']}")
            company = cached.get("company") if cached else None
            if cached is None:
                company = office_company(client, office)
                cache_put(sb, _CACHE_SOURCE, f"{ORANGE_SLICE}:company:{office['id']}", {"company": company}, ttl_days=365)
            for start in range(0, len(surnames) if company else 0, SURNAMES_PER_QUERY):
                batch = client.sql(alumni_sql(company["id"], since, surnames[start : start + SURNAMES_PER_QUERY]))
                stats["truncated"] += len(batch) >= ALUMNI_LIMIT
                rows += batch
        except Exception as error:  # an outage: what was swept is written and remembered, the rest waits
            status, stats["error"] = "stopped", f"{type(error).__name__}: {error}"[:200]
            break
        stats["swept"] += 1
        swept.append(f"{ORANGE_SLICE}:{office['id']}")
        if not company:
            stats["no_company"] += 1
            continue
        stats["alumni"] += len(rows)
        for row in rows:
            profile = orange_slice_profile(row, company["name"])
            candidates = by_name.get(name_key(profile["first_name"], profile["last_name"]) or ("", ""), [])
            # Two people at one office with the same name: the profile could be either, so it joins neither.
            joined = [(contact, hit) for contact, posts in candidates
                      if (hit := match_profile(contact, posts, profile, today=clock.date()))]
            if len(joined) != 1:
                continue
            contact, (post, experience) = joined[0]
            if left := departure(profile, experience, today=clock.date()):
                found.append((contact, post, experience, profile, left))
                stats["matched"] += 1
    written = write_departures(sb, found, now=clock)
    for key in swept:
        cache_put(sb, _CACHE_SOURCE, key, {"swept": clock.date().isoformat()}, ttl_days=_CHECK_TTL_DAYS)
    return {"status": status, "queries": getattr(client, "calls", None), **stats, **written}


def run_vendor_monitor(sb: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Monthly: Coresignal for tracked people, then Orange Slice for tracked offices. A missing key skips its vendor."""
    return {"coresignal": run_coresignal_check(sb, now=now), "orange_slice": run_orange_slice_sweep(sb, now=now)}

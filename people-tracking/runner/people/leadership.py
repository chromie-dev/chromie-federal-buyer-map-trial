"""Stated leadership changes: who took or left a post, as releases, nominations and command stories state it.

A reader turns a source's words into events; ``write_leadership_events`` turns events into positions and move
ledger rows on the shared writer. Event kinds:

- appointment: the person took the post on the day (a change of command, a confirmation). ``started`` marks a
  stated start, which opens the position with a stated valid_from.
- departure: the person left the post on the day; their open position there closes, or a closed one is
  written when no source held it yet.
- announcement: a stated move that has not happened (a nomination, an assignment release). It opens no
  position; ``holds`` is the post the source says the person serves in now, observed on the day.
- promotion: a grade, not a post.
- observation: no move, only ``holds`` (a release nominating an officer to a grade names the post held now).

Change-of-command sentences follow a few fixed forms ("X relieved Y as commanding officer of Z", "Y relinquished
command of Z to X", "X assumed command of Z from Y"); a story that states a change in other words goes to a
model whose every name, post, date and quote must appear verbatim in the story.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from orchestration.gov.people.bios import (
    _DATE_RE,
    _MONTH,
    _match_date,
    _month_number,
    _names_post,
    _organization_matcher,
    _sentences,
    _strip_rank,
    bio_role,
)
from orchestration.gov.people.writer import (
    _insert_batches,
    _select_in,
    normalize_text,
    record_moves,
    resolve_contacts,
    upsert_positions,
)


@dataclass(frozen=True)
class Department:
    name: str
    agency_code: str


DEPARTMENTS = {
    "navy": Department("Department of the Navy", "1700"),
    "army": Department("Department of the Army", "2100"),
    "air force": Department("Department of the Air Force", "5700"),
    "defense": Department("Department of Defense", "097"),
    "homeland": Department("Department of Homeland Security", "070"),
}
_SERVICE_DEPARTMENT = {
    "navy": "navy", "marine corps": "navy", "marines": "navy", "army": "army", "air force": "air force",
    "space force": "air force", "coast guard": "homeland",
}
_SERVICE = r"(?:U\.S\.\s+)?(?:Navy|Army|Air\s+Force|Space\s+Force|Marine\s+Corps|Coast\s+Guard)"
_GRADE = (r"(?:Rear\s+Adm(?:iral|\.)(?:\s+\(lower\s+half\))?|Vice\s+Adm(?:iral|\.)|Adm(?:iral|\.)|Capt(?:ain|\.)|"
          r"(?:Lt\.\s+)?Cmdr\.|Cdr\.|(?:Lt\.\s+)?Col\.|Colonel|(?:Brig|Maj|Lt)\.\s+Gen\.?|Gen\.|General|Maj\.|"
          r"Mr\.|Ms\.|Mrs\.|Dr\.)")
RANK = rf"(?:{_SERVICE}\s+)?(?:(?:Reserve|Retired)\s+)?{_GRADE}"
# A first name (or initials such as JD), an optional nickname, middle initials, then one or two surname words.
NAME = (r"[A-Z][A-Za-z'’-]+(?:\s+[“\"][^”\"]{1,20}[”\"])?(?:\s+[A-Z]\.)*(?:\s+[A-Z][A-Za-z'’-]+){1,2}"
        r"(?:,?\s+(?:Jr\.|Sr\.))?")
PERSON = rf"{RANK}\s+{NAME}"
# A holder named by rank and surname only ("relieved Vice Adm. Perry"): read, then dropped as a person.
_HOLDER = rf"{RANK}\s+[A-Z][A-Za-z'’-]+(?:\s+[“\"][^”\"]{{1,20}}[”\"])?(?:\s+[A-Z]\.)*(?:\s+[A-Z][A-Za-z'’-]+){{0,2}}"
_ORDINAL = r"(?:the\s+)?(?:\d+(?:st|nd|rd|th)\s+)?"
_OUTGOING = r"(?:(?:the\s+)?outgoing\s+(?:[Cc]ommander|[Cc]ommanding\s+[Oo]fficer|[Pp]rogram\s+[Mm]anager)\s+)?"
# ", a native of Waterford, Connecticut," between a subject and its verb.
_APPOSITIVE = r"(?:,[^,;]{1,80}(?:,[^,;]{1,40})?,)?"
# Where a post stops: the ceremony, place or date that follows it, a relative clause, or the sentence end.
_POST_END = (rf"(?=,?\s+(?:during|in\s+a|at\s+(?:a|an|the)\b|aboard|before|following|on\s+(?:{_MONTH})|"
             rf"in\s+(?:{_MONTH})|(?:{_MONTH})\.?\s+\d{{1,2}}|in\s+(?:front|the\s+presence)\s+of|(?:on\s+)?"
             rf"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day)\b|,\s+(?:where|who|which|whose|[a-z]+ing)\b|\.?\s*$)")
# A post written as the role alone ("relieved X as Commanding Officer") names no unit, and the story's publishing
# unit is often a parent command or the host base, so the fixed forms leave such a change to the model.
_BARE_ROLE_RE = re.compile(r"(?:the\s+)?(?:new\s+)?(?:commanding\s+officer|commander|commodore|director|program\s+manager)", re.I)
CHANGE_RES = (
    re.compile(rf"(?P<new>{PERSON}){_APPOSITIVE}\s+relieved\s+{_OUTGOING}(?P<old>{_HOLDER}),?\s+"
               rf"(?:to\s+assume\s+command\s+)?as\s+{_ORDINAL}(?P<post>.+?){_POST_END}"),
    re.compile(rf"(?P<old>{PERSON})\s+relinquished\s+command\s+of\s+(?P<unit>.+?)\s+to\s+(?P<new>{PERSON})"),
    re.compile(rf"(?P<new>{PERSON}){_APPOSITIVE}\s+(?:assumed|took)\s+command\s+(?:as\s+(?P<role>[a-z][a-z ]+?)\s+)?"
               rf"of\s+(?P<unit>.+?)(?:\s+from\s+{_OUTGOING}(?P<old>{PERSON})|{_POST_END})"),
    re.compile(rf"(?P<old>{PERSON})\s+was\s+relieved\s+as\s+{_ORDINAL}(?P<post>.+?)(?:\s+by\s+(?P<new>{PERSON})|{_POST_END})"),
    re.compile(rf"(?P<new>{PERSON}){_APPOSITIVE}\s+became\s+{_ORDINAL}(?P<post>(?:[Cc]ommander|[Cc]ommanding\s+[Oo]fficer|"
               rf"[Dd]irector|[Pp]rogram\s+[Mm]anager)\b.+?){_POST_END}"),
    # The news reader's announcement form: named, selected or appointed as the new program manager.
    re.compile(rf"(?P<new>{PERSON}){_APPOSITIVE}\s+(?:was|is|has\s+been)\s+(?:introduced|named|selected|appointed)\s+"
               rf"as\s+(?:the\s+)?(?:new\s+|interim\s+)?(?P<post>.+?){_POST_END}"),
)
_TRIGGER_RE = re.compile(r"change of command|assum(?:ed|es) command|relieved|relinquish|took command|"
                         r"assumption of command|named (?:as )?(?:the )?new", re.I)
# "the installation", "the premier joint reserve installation in North Texas", "Navy’s safety organization": a
# description, not the unit's name.
_DESCRIBED_UNIT_RE = re.compile(r"^(?:the\s+(?:\S+[’']s\s+)?|\S+[’']s\s+)[a-z]")
_ROLE_OF_RE = re.compile(r"^(?P<role>(?:commanding|executive)\s+officer|commander|director|program\s+manager)\s+of\s+(?P<unit>.+)$", re.I)
# "July 9" and the military "9 July", each with an optional year.
_DAY_RE = re.compile(rf"\b(?:(?P<month>{_MONTH})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?|(?P<day_first>\d{{1,2}})\s+"
                     rf"(?P<month_after>{_MONTH})\b\.?)(?:,?\s+(?P<year>(?:19|20)\d\d))?\b")
# A change of responsibility hands over the senior enlisted post, not the command.
_ENLISTED_RE = re.compile(r"\b(?:Sgt|Sergeant|Petty\s+Officer|(?:Master|Senior)\s+Chief|Chief\s+Petty|[CST]?M?Sgt)\b")
# A post whose first part names its role ("deputy commander, U.S. Pacific Fleet", "Vice Commander, Example Command")
# keeps it; a bare unit ("USS Example (SSN 999)", "Officer Training Command") is the unit's command.
_HEAD_WORDS_RE = re.compile(r"^[^,]*?\b(?:commander|commanding|director|chief|deputy|manager|executive|superintendent|"
                            r"supervisor|chairman|secretary|general|advocate|surgeon|chaplain|attach[eé]|official|"
                            r"assistant|head|president|inspector|commodore)\b", re.I)


def get_json(url: str, *, attempts: int = 3, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """GET a JSON API, retrying a dropped connection or a server error with backoff."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 chromie-runner"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code < 500 and error.code != 429 or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == attempts - 1:
                raise
        sleep(5 * 2 ** attempt)
    raise AssertionError("unreachable")


def department_for(service: str | None, text: str = "") -> Department:
    """The department a service or a post belongs to; a post naming no service is DoD's."""
    key = normalize_text(service)
    if key in _SERVICE_DEPARTMENT:
        return DEPARTMENTS[_SERVICE_DEPARTMENT[key]]
    lowered = text.lower()
    for word in ("navy", "marine corps", "army", "air force", "space force", "coast guard", "homeland security"):
        if word in lowered:
            return DEPARTMENTS[_SERVICE_DEPARTMENT.get(word, "homeland")]
    return DEPARTMENTS["defense"]


def person_name(written: str) -> str:
    """A name as a person record keeps it: no service, grade or nickname."""
    name = re.sub(r"\s*[“\"][^”\"]+[”\"]", "", written)
    return re.sub(r"\s+", " ", _strip_rank(name)).strip(" ,")


def service_of(written: str) -> str | None:
    match = re.match(rf"\s*({_SERVICE})\s", written)
    return re.sub(r"^U\.S\.\s+", "", match.group(1)) if match else None


def _unit_name(unit: str, story_unit: str | None) -> str:
    # "the Aircraft Launch ... Program Office", "amphibious transport dock ship USS New Orleans" -> the name.
    unit = re.sub(r"^.*?\b(?=(?:USS|USNS|USCGC)\s)", "", unit.strip(" ,"))
    if _DESCRIBED_UNIT_RE.match(unit):
        return story_unit or unit
    return re.sub(r"^the\s+", "", unit)


def _unit_post(unit: str, role: str | None, story_unit: str | None) -> str:
    unit = _unit_name(unit, story_unit)
    if role:
        return f"{role.strip()}, {unit}"
    return unit if _HEAD_WORDS_RE.match(unit) else f"Commander, {unit}"


def _sentence_day(sentence: str, published: date) -> date | None:
    """The first full date a sentence gives; a date without a year is the latest one not after publication."""
    for match in _DAY_RE.finditer(sentence):
        year = int(match.group("year") or published.year)
        try:
            found = date(year, _month_number(match.group("month") or match.group("month_after")),
                         int(match.group("day") or match.group("day_first")))
        except ValueError:
            continue
        if not match.group("year") and found > published:
            found = found.replace(year=year - 1)
        return found
    return None


def read_changes(text: str, *, published: date, story_unit: str | None = None) -> list[dict[str, Any]]:
    """Every change of charge the text states in a fixed form, with the sentence that states it."""
    sentences = [re.sub(r"\s+", " ", sentence).strip() for sentence in _sentences(text)]
    dateline = next((_sentence_day(sentence, published) for sentence in sentences[:2] if _sentence_day(sentence, published)), None)
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for sentence in sentences:
        for pattern in CHANGE_RES:
            for match in pattern.finditer(sentence):
                groups = match.groupdict()
                post = (groups.get("post") or "").strip(" ,")
                if role_of := _ROLE_OF_RE.match(post):
                    post = f"{role_of['role']} of {_unit_name(role_of['unit'], story_unit)}"
                post = post or _unit_post(groups["unit"], groups.get("role"), story_unit)
                if _BARE_ROLE_RE.fullmatch(post):
                    continue
                day = _sentence_day(sentence, published) or dateline
                change = {
                    "incoming": groups.get("new") or "",
                    "outgoing": groups.get("old") or "",
                    "post": post,
                    "day": day or published,
                    "date_basis": "stated" if day else "observed",
                    "quote": sentence,
                }
                key = (person_name(change["incoming"]), person_name(change["outgoing"]))
                # A story repeats a change (headline, lead, body); the first full statement is kept.
                if key not in found:
                    found[key] = change
    flat = " ".join(sentences)
    for change in found.values():
        # "Sumsion became the 53rd commander ... Sumsion relieved outgoing commander Capt. JD Crinklaw".
        if change["incoming"] and not change["outgoing"]:
            surname = re.escape(person_name(change["incoming"]).split()[-1])
            if match := re.search(rf"\b{surname}\s+relieved\s+{_OUTGOING}(?P<old>{PERSON})", flat):
                change["outgoing"] = match.group("old")
            # "... assumed command of the 1st Battalion during a ceremony Aug. 22, succeeding Lt. Col. Sam Ferro."
            elif match := re.search(rf"\bsucceeding\s+(?P<old>{PERSON})", change["quote"]):
                change["outgoing"] = match.group("old")
    return list(found.values())


class ExtractedChange(BaseModel):
    incoming: str = Field("", description="Who took the post, with rank, exactly as written; empty if none")
    outgoing: str = Field("", description="Who left the post, with rank, exactly as written; empty if none")
    post: str = Field(description="The command, unit or office whose head changed, exactly as written (for example "
                                  "'USS Example (SSN 999)'), with its role if the text joins them; never the role alone")
    date_text: str = Field("", description="The date of the change exactly as written; empty if the story gives none")
    quote: str = Field(description="The sentence that states the change, copied verbatim")


class ExtractedChanges(BaseModel):
    changes: list[ExtractedChange] = Field(default_factory=list)


_EXTRACT_SYSTEM = (
    "You read one official story or release and list each change of who holds a post that it states: who took "
    "the post, who left it, the command or office, and the date. Copy every name, the post, the date and the quote "
    "exactly as the text writes them. The post names the command (a ship, squadron, office), not only the role. A "
    "past posting mentioned as background is not a change. List nothing the text does not state."
)


def extract_model() -> str:
    return (os.getenv("CHROMIE_PEOPLE_EXTRACT_MODEL", "").strip() or os.getenv("OPENAI_NON_BROWSER_MODEL", "").strip()
            or "gpt-5.4-mini")


def _flat(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def lint_change(change: ExtractedChange, text: str) -> bool:
    """Every name, the post, the date and the quote appear verbatim; each named person's surname is in the quote."""
    flat, quote = _flat(text), _flat(change.quote)
    people = [value for value in (change.incoming, change.outgoing) if value.strip()]
    if not people or not quote or quote not in flat or _flat(change.post) not in flat or _BARE_ROLE_RE.fullmatch(change.post.strip()):
        return False
    if change.date_text and _flat(change.date_text) not in flat:
        return False
    return all(_flat(person) in flat and _flat(person_name(person).split()[-1]) in quote for person in people)


def agent_changes(
    text: str,
    *,
    published: date,
    invoke: Callable[..., Any],
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Model-read changes that pass the verbatim lint; a change failing it is dropped, never repaired."""
    result = asyncio.run(invoke(
        model=model or extract_model(),
        system=_EXTRACT_SYSTEM,
        user=text[:20_000],
        output_format=ExtractedChanges,
        metadata={"purpose": "people-leadership-extract"},
    ))
    changes = []
    for change in (result.changes if result else []):
        if not lint_change(change, text):
            continue
        day = _sentence_day(change.date_text, published) if change.date_text else None
        if day is None and change.date_text and (match := _DATE_RE.search(change.date_text)):
            day = _match_date(match)
        post = change.post.strip(" ,")
        changes.append({
            "incoming": change.incoming, "outgoing": change.outgoing,
            "post": post if _HEAD_WORDS_RE.match(post) or _ROLE_OF_RE.match(post) else _unit_post(post, None, None),
            "day": day or published, "date_basis": "stated" if day else "observed", "quote": change.quote,
        })
    return changes


def story_changes(
    text: str,
    *,
    published: date,
    story_unit: str | None = None,
    invoke: Callable[..., Any] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Fixed-form changes first; the model reads only a story that states a change the forms miss.

    Returns the changes and whether the model was called.
    """
    changes = read_changes(text, published=published, story_unit=story_unit)
    if changes or invoke is None or not _TRIGGER_RE.search(text):
        return changes, False
    return agent_changes(text, published=published, invoke=invoke), True


def change_events(change: dict[str, Any], *, service: str | None, source_ref: str, source_url: str) -> list[dict[str, Any]]:
    """A change of charge is an appointment for who took the post and a departure for who left it."""
    events = []
    for role, kind in (("incoming", "appointment"), ("outgoing", "departure")):
        written = change.get(role) or ""
        name = person_name(written)
        if len(name.split()) < 2 or _ENLISTED_RE.search(written):
            continue
        events.append({
            "name": name,
            "department": department_for(service_of(written) or service, change["post"]),
            "event_type": kind,
            "title": change["post"],
            "day": change["day"],
            "date_basis": change["date_basis"],
            "started": kind == "appointment" and change["date_basis"] == "stated",
            "source_ref": source_ref,
            "source_url": source_url,
            "quote": change["quote"],
        })
    return events


def _name_parts(name: str) -> tuple[str, list[str], str]:
    tokens = normalize_text(name).split()
    middles = [token for token in tokens[1:-1] if len(token) == 1]
    rest = [token for token in tokens[1:] if not (len(token) == 1 and token in middles)]
    return tokens[0], middles, " ".join(rest)


def _office_link(sb: Any, people: list[dict[str, Any]], events: list[dict[str, Any]], orgs: list[str]) -> None:
    """A name written without (or with) a middle initial joins the one department contact with the other form
    that holds or held the same post; any other name match stays a separate person."""
    for person, event, org in zip(people, events, orgs):
        own = person["identifiers"][0][1]
        first, middles, last = _name_parts(person["name"])
        department = own.split("|", 1)[1]
        rows = sb.table("gov_contact_identifiers").select("contact_id,value").eq("kind", "name_org").like(
            "value", f"{first} %{last}|{department}").execute().data or []
        if any(row["value"] == own for row in rows):
            continue
        candidates = {}
        for row in rows:
            name = row["value"].split("|", 1)[0]
            other_first, other_middles, other_last = _name_parts(name)
            compatible = not middles or not other_middles or middles == other_middles
            if (other_first, other_last) == (first, last) and compatible:
                candidates[str(row["contact_id"])] = row["value"]
        if not candidates:
            continue
        posts = [value for value in (event.get("title"), event.get("holds"), event.get("previous_title")) if value]
        held = _select_in(sb, "gov_contact_positions", "contact_id,organization_id,raw_title", "contact_id", list(candidates))
        matches = {
            str(row["contact_id"])
            for row in held
            if (org and row.get("organization_id") == org) or any(_names_post(row.get("raw_title") or "", post) or
                                                                  _names_post(post, row.get("raw_title") or "") for post in posts)
        }
        if len(matches) == 1:
            person["identifiers"] = [("name_org", candidates[matches.pop()])]


def _batch_link(people: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    """Two stories in one run that name the same holder of the same post, one with a middle initial, are one person,
    kept under the fuller name so they resolve to whatever contact that name already has."""
    clusters: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for person, event in zip(people, events):
        first, middles, last = _name_parts(person["name"])
        options = clusters.setdefault((first, last, person["identifiers"][0][1].split("|", 1)[1]), [])
        cluster = next((option for option in options
                        if (not middles or not option["middles"] or middles == option["middles"])
                        and (_names_post(option["post"], event["title"]) or _names_post(event["title"], option["post"]))), None)
        if cluster is None:
            options.append({"middles": middles, "post": event["title"], "people": [person]})
            continue
        cluster["people"].append(person)
        cluster["middles"] = max(cluster["middles"], middles, key=len)
    for cluster in (option for options in clusters.values() for option in options if len(option["people"]) > 1):
        fullest = max(cluster["people"], key=lambda person: len(_name_parts(person["name"])[1]))
        for person in cluster["people"]:
            person["name"], person["identifiers"] = fullest["name"], list(fullest["identifiers"])


def _join_retellings(sb: Any, moves: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Two stories of one ceremony are one move: the later story adds its quote to the row the first one wrote."""
    def key(row: dict[str, Any]) -> tuple[str, str, str, str]:
        return (str(row["gov_contact_id"]), row["event_type"], str(row["effective_date"])[:10], normalize_text(row.get("title") or ""))

    held = {}
    for row in _select_in(sb, "gov_contact_role_history", "gov_contact_id,event_type,effective_date,title,source_provider,"
                          "source_ref,source_url,evidence", "gov_contact_id", {move["gov_contact_id"] for move in moves}):
        if row["source_provider"] == source:
            held.setdefault(key(row), row)
    joined: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for move in moves:
        first = joined.get(key(move)) or held.get(key(move))
        if first:
            evidence = {(item.get("source_url"), item.get("quote")): item for item in [*(first.get("evidence") or []), *move["evidence"]]}
            move = {**move, "source_ref": first["source_ref"], "source_url": first["source_url"], "evidence": list(evidence.values())}
        joined[key(move)] = move
    return list(joined.values())


def write_leadership_events(sb: Any, events: list[dict[str, Any]], *, source: str, now: datetime | None = None) -> dict[str, int]:
    """Contacts, positions and move-ledger rows for a batch of stated leadership events; reruns add nothing."""
    clock = now or datetime.now(timezone.utc)
    timestamp = clock.isoformat()
    if not events:
        return {"events": 0, "moves": 0, "positions_inserted": 0, "positions_updated": 0, "positions_closed": 0}
    matchers: dict[str, tuple[Callable[[str], str | None], str | None]] = {}
    for event in events:
        code = event["department"].agency_code
        if code not in matchers:
            matchers[code] = _organization_matcher(sb, code)
    events = [event for event in events if matchers[event["department"].agency_code][1]]

    def org_of(event: dict[str, Any], field: str) -> str | None:
        match, department = matchers[event["department"].agency_code]
        return (match(event[field]) or department) if event.get(field) else None

    orgs = [org_of(event, "title") for event in events]
    people = [
        {
            "name": event["name"],
            "title": event.get("holds") or (event["title"] if event["event_type"] == "appointment" else None),
            "agency": event["department"].name,
            "source_url": event["source_url"],
            "identifiers": [("name_org", f"{normalize_text(event['name'])}|{normalize_text(event['department'].name)}")],
        }
        for event in events
    ]
    _office_link(sb, people, events, orgs)
    _batch_link(people, events)
    contact_ids, identity = resolve_contacts(sb, people, source=source, now=clock)
    keys = {
        str(row["id"]): row["identity_key"]
        for row in _select_in(sb, "gov_contacts", "id,identity_key", "id", {contact for contact in contact_ids if contact})
    }

    opens, departures, moves = [], [], []
    for event, contact, org in zip(events, contact_ids, orgs):
        if not contact:
            continue
        day = event["day"].isoformat()
        base = {"contact_id": contact, "source_ref": event["source_ref"], "source_url": event["source_url"],
                "first_observed_at": day, "last_observed_at": day}
        if event["event_type"] == "appointment":
            opens.append({**base, "organization_id": org, "role_type": bio_role(event["title"]), "raw_title": event["title"],
                          "date_basis": "stated" if event.get("started") else "observed",
                          "valid_from": day if event.get("started") else None})
        if event.get("holds"):
            opens.append({**base, "organization_id": org_of(event, "holds"), "role_type": bio_role(event["holds"]),
                          "raw_title": event["holds"]})
        if event["event_type"] == "departure":
            departures.append((event, contact, org))
        if contact in keys and event["event_type"] != "observation":
            moves.append({
                "gov_contact_id": contact,
                "person_identity_key": keys[contact],
                "name": event["name"],
                "agency_name": event["department"].name,
                "title": event["title"],
                "event_type": event["event_type"],
                "effective_date": day,
                "reported_at": event.get("reported_at") or timestamp,
                "started_at": day if event.get("started") else None,
                "ended_at": day if event["event_type"] == "departure" else None,
                "source_provider": source,
                "source_ref": event["source_ref"],
                "source_url": event["source_url"],
                "date_basis": event["date_basis"],
                "organization_id": org,
                "previous_title": event.get("previous_title"),
                "previous_organization_id": org_of(event, "previous_title"),
                "status": event.get("status") or "current",
                "evidence": [{"source_url": event["source_url"], "quote": event["quote"]}],
            })
    positions = upsert_positions(sb, opens, source=source, now=clock)
    closed = _close_departures(sb, departures, matchers, source=source, timestamp=timestamp)
    unique = {(row["source_ref"], row["person_identity_key"], row["event_type"]): row
              for row in reversed(_join_retellings(sb, moves, source))}
    recorded = record_moves(sb, list(unique.values()), now=clock)
    return {"events": len(events), **identity, **positions, "positions_closed": closed, "moves": recorded}


def _close_departures(
    sb: Any,
    departures: list[tuple[dict[str, Any], str, str | None]],
    matchers: dict[str, tuple[Callable[[str], str | None], str | None]],
    *,
    source: str,
    timestamp: str,
) -> int:
    """Close the post each departure names, whichever source opened it; write a closed post when none did."""
    if not departures:
        return 0
    held = _select_in(sb, "gov_contact_positions", "*", "contact_id", {contact for _, contact, _ in departures})
    closed, inserts = 0, []
    for event, contact, org in departures:
        day = event["day"].isoformat()
        department = matchers[event["department"].agency_code][1]
        mine = [row for row in held if str(row["contact_id"]) == contact]
        names = [row for row in mine if (org != department and row.get("organization_id") == org)
                 or _names_post(row.get("raw_title") or "", event["title"])]
        basis = event["date_basis"]
        for row in names:
            if row.get("valid_to") is None and str(row.get("valid_from") or "")[:10] <= day:
                # one basis covers both dates, so the closed post reads stated only when its start and end both are
                both = basis == "stated" and (not row.get("valid_from") or row.get("date_basis") == "stated")
                sb.table("gov_contact_positions").update(
                    {"valid_to": day, "date_basis": "stated" if both else "observed", "updated_at": timestamp}
                ).eq("id", row["id"]).execute()
                row["valid_to"] = day
                closed += 1
        if not names and not any(row.get("source") == source and row.get("source_ref") == event["source_ref"] for row in mine):
            inserts.append({
                "contact_id": contact, "organization_id": org, "role_type": bio_role(event["title"]),
                "raw_title": event["title"], "valid_from": None, "valid_to": day, "date_basis": basis,
                "first_observed_at": day, "last_observed_at": day, "observed_at": day, "source": source,
                "source_ref": event["source_ref"], "source_url": event["source_url"], "updated_at": timestamp,
            })
    _insert_batches(sb, "gov_contact_positions", inserts)
    return closed + len(inserts)

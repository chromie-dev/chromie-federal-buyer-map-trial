"""Staff moves the FPDS record shows, and the alerts every recorded move sends to the companies tracking it.

FPDS states no start or end dates. A move is observed when one person's account at one office stops before
their account at another office starts, with at least three records on each side (two independent records, as
the move rule asks). The move is dated by the first record at the new office, and the old office's open posts
close on their last observed day. Every move in the ledger, from any source, then alerts the companies that
track the person or either office, once per member.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from orchestration.gov.people.writer import (
    FPDS_SOURCE,
    _day,
    _paged,
    _select_in,
    _upsert_batches,
    parse_fpds_user,
    record_moves,
)
from orchestration.supabase_client import get_supabase_client

MIN_RECORDS = 3
ROLE_TITLES = {"contracting_officer": "Contracting Officer", "contract_specialist": "Contract Specialist"}
# A run reads moves recorded this recently, so a missed day still alerts; the notification key dedupes.
_ALERT_LOOKBACK_DAYS = 14
_ALERT_VERBS = {"transfer": "moved to", "appointment": "took up", "departure": "left", "announcement": "named to",
                "promotion": "promoted to", "acting": "acting as"}
_SOURCE_LABELS = {FPDS_SOURCE: "FPDS contract actions", "war_gov_releases": "war.gov release",
                  "congress_nominations": "Senate nomination", "dvids_leadership": "DVIDS story",
                  "bio_pages": "official biography", "coresignal": "Coresignal (licensed people data)",
                  "orange_slice": "Orange Slice (licensed people data)"}
# A move is confirmed only when the government's own publication states it, reported when a licensed profile
# does, and inferred when two observed records imply it. The app's person history labels moves the same way.
_OFFICIAL_SOURCES = {"bio_pages", "war_gov_releases", "congress_nominations", "dvids_leadership", "directory_affiliation"}
_CERTAINTY_TEXT = {
    "confirmed": "Confirmed: an official source states it",
    "reported": "Reported by a licensed profile; no official source confirms it yet",
    "inferred": "Inferred from two records, one office and then another; no source states the move",
}


def move_certainty(move: dict[str, Any]) -> str:
    """confirmed, reported or inferred, from the move's date basis and source."""
    if move.get("date_basis") != "stated":
        return "inferred"
    return "confirmed" if move.get("source_provider") in _OFFICIAL_SOURCES else "reported"


def account_moves(accounts: Iterable[dict[str, Any]]) -> list[tuple[tuple[str, str, str, int], tuple[str, str, str, int]]]:
    """(from, to) office spans, each (first day, last day, office code, records), for one person's accounts."""
    spans: dict[str, tuple[str, str, int]] = {}
    for row in accounts:
        parsed = parse_fpds_user(row.get("value"))
        if not parsed or not row.get("activity_count"):
            continue
        first, last, count = _day(row["activity_first_on"]), _day(row["activity_last_on"]), int(row["activity_count"])
        # Two ids for one office (with and without .CIV, NAVY.MIL and US.NAVY.MIL) are one account history.
        held = spans.get(parsed["office"])
        spans[parsed["office"]] = (min(held[0], first), max(held[1], last), held[2] + count) if held else (first, last, count)
    ordered = sorted((first, last, code, count) for code, (first, last, count) in spans.items())
    return [(a, b) for a, b in zip(ordered, ordered[1:]) if a[1] <= b[0] and a[3] >= MIN_RECORDS and b[3] >= MIN_RECORDS]


def _office_title(positions: list[dict[str, Any]], code: str, offices: dict[str, dict[str, Any]]) -> str:
    roles = {row["role_type"] for row in positions if row.get("source_ref") == code}
    role = next((ROLE_TITLES[role] for role in ROLE_TITLES if role in roles), "Contracting staff")
    office = offices.get(code)
    return f"{role}, {office['name'] if office else f'contracting office {code}'}"


def detect_fpds_moves(sb: Any, *, now: datetime | None = None) -> dict[str, int]:
    """Write every FPDS account move to the ledger and close the posts the person left; reruns rewrite the same rows."""
    clock = now or datetime.now(timezone.utc)
    timestamp = clock.isoformat()
    # ponytail: reads every FPDS account per run; filter on updated_at since the last run if it grows past ~100k.
    accounts = _paged(lambda: sb.table("gov_contact_identifiers").select("contact_id,value,activity_first_on,activity_last_on,activity_count")
                      .eq("kind", "fpds_user").order("id"))
    by_contact: dict[str, list[dict[str, Any]]] = {}
    for row in accounts:
        by_contact.setdefault(str(row["contact_id"]), []).append(row)
    found = {contact: (moves, rows) for contact, rows in by_contact.items() if (moves := account_moves(rows))}

    contacts = {str(row["id"]): row for row in _select_in(sb, "gov_contacts", "id,name,agency", "id", found)}
    positions = [row for row in _select_in(sb, "gov_contact_positions", "*", "contact_id", found) if row.get("source") == FPDS_SOURCE]
    codes = {code for moves, _ in found.values() for a, b in moves for code in (a[2], b[2])}
    offices = {row["source_ref"]: row for row in _select_in(sb, "gov_organizations", "id,name,source,source_ref", "source_ref", codes)
               if row.get("source") == "fpds_office"}
    rows, closes, skipped, seen = [], [], 0, set()
    for contact, (moves, accounts_held) in found.items():
        person = contacts.get(contact) or {}
        mine = sorted((row for row in positions if str(row["contact_id"]) == contact),
                      key=lambda row: str(row.get("last_observed_at") or ""), reverse=True)
        key = next(f"fpds:{parsed['person']}" for row in accounts_held if (parsed := parse_fpds_user(row["value"])))
        for (a_first, a_last, a_code, a_count), (b_first, b_last, b_code, b_count) in moves:
            seen.add((f"fpds-move:{a_code}>{b_code}", key))  # still shown by the records even when skipped below
            url_of = {code: next((row["source_url"] for row in mine if row.get("source_ref") == code and row.get("source_url")), None)
                      for code in (a_code, b_code)}
            source_url = url_of[b_code] or url_of[a_code] or next((row["source_url"] for row in mine if row.get("source_url")), None)
            if not (source_url and person.get("agency") and person.get("name")):
                skipped += 1
                continue
            rows.append({
                "gov_contact_id": contact,
                "person_identity_key": key,
                "name": person["name"],
                "agency_name": person["agency"],
                "title": _office_title(mine, b_code, offices),
                "previous_title": _office_title(mine, a_code, offices),
                "organization_id": (offices.get(b_code) or {}).get("id"),
                "previous_organization_id": (offices.get(a_code) or {}).get("id"),
                "event_type": "transfer",
                "effective_date": b_first,
                "reported_at": timestamp,
                "source_provider": FPDS_SOURCE,
                "source_ref": f"fpds-move:{a_code}>{b_code}",
                "source_url": source_url,
                "date_basis": "observed",
                "status": "current",
                "evidence": [
                    {"office_code": code, "first_on": first, "last_on": last, "records": count, "source_url": url_of[code]}
                    for first, last, code, count in ((a_first, a_last, a_code, a_count), (b_first, b_last, b_code, b_count))
                ],
            })
            # The old office's posts close on their own last observed day, unless the person still works there.
            closes += [row for row in mine if row.get("source_ref") == a_code and row.get("valid_to") is None
                       and _day(row.get("last_observed_at")) <= b_first]

    # A move already in the ledger keeps the day Chromie first reported it; one the records no longer show (the
    # person turned up at the old office after all) is retracted.
    held = {(row["source_ref"], row["person_identity_key"]): row for row in _paged(
        lambda: sb.table("gov_contact_role_history").select("id,source_ref,person_identity_key,reported_at,status")
        .eq("source_provider", FPDS_SOURCE).order("id"))}
    for row in rows:
        row["reported_at"] = (held.get((row["source_ref"], row["person_identity_key"])) or row)["reported_at"]
    retracted = [row["id"] for key, row in held.items() if key not in seen and row.get("status") != "retracted"]
    for start in range(0, len(retracted), 150):
        sb.table("gov_contact_role_history").update({"status": "retracted", "updated_at": timestamp}).in_(
            "id", retracted[start : start + 150]).execute()
    recorded = record_moves(sb, rows, now=clock)
    closed = {row["id"]: row for row in closes}
    for row in closed.values():
        sb.table("gov_contact_positions").update(
            {"valid_to": _day(row["last_observed_at"]), "updated_at": timestamp}
        ).eq("id", row["id"]).execute()
    return {"fpds_moves": recorded, "moves_retracted": len(retracted), "positions_closed": len(closed), "moves_skipped": skipped}


def alert_text(move: dict[str, Any]) -> tuple[str, str]:
    """The notification title and body for one ledger move."""
    title = f"{move['name']} {_ALERT_VERBS.get(move['event_type'], 'changed post:')} {move['title']}"
    dated = "Effective" if move.get("date_basis") == "stated" else "Observed from"
    if move["event_type"] == "announcement":
        dated = "Announced"
    evidence = [item for item in move.get("evidence") or [] if isinstance(item, dict)]
    # A vendor profile states the month only.
    month = any(item.get("date_precision") == "month" for item in evidence)
    parts = [_CERTAINTY_TEXT[move_certainty(move)],
             f"{dated} {_day(move['effective_date'])[:7] if month else _day(move['effective_date'])}"]
    if move.get("previous_title"):
        parts.append(f"Before: {move['previous_title']}")
    if now := next((item["now"] for item in evidence if item.get("now")), None):
        parts.append(f"Now: {now}")
    parts.append(f"Source: {_SOURCE_LABELS.get(move['source_provider'], move['source_provider'])}")
    # "Example Shipworks, Inc." already ends its sentence.
    return title, ". ".join(part.rstrip(".") for part in parts) + "."


def alert_moves(sb: Any, *, now: datetime | None = None) -> dict[str, int]:
    """Notify each member of a company that tracked the person or an office before the move was recorded."""
    clock = now or datetime.now(timezone.utc)
    since = (clock - timedelta(days=_ALERT_LOOKBACK_DAYS)).isoformat()
    moves = _paged(lambda: sb.table("gov_contact_role_history").select(
        "id,gov_contact_id,name,title,previous_title,event_type,effective_date,date_basis,organization_id,"
        "previous_organization_id,source_provider,source_url,evidence,created_at"
    ).gte("created_at", since).eq("status", "current").order("id"))
    if not moves:
        return {"moves_considered": 0, "notifications": 0}
    contacts = {str(move["gov_contact_id"]) for move in moves if move.get("gov_contact_id")}
    orgs = {str(org) for move in moves for org in (move.get("organization_id"), move.get("previous_organization_id")) if org}
    people = _select_in(sb, "gov_person_relationships", "gov_profile_id,contact_id,created_at", "contact_id", contacts)
    offices = _select_in(sb, "gov_tracked_offices", "gov_profile_id,organization_id,created_at", "organization_id", orgs)
    tenants_by_move: dict[str, set[str]] = {}
    for move in moves:
        targets = {str(move.get("organization_id")), str(move.get("previous_organization_id"))}
        # Tracking that began after the move was recorded shows it on the timeline, not as news.
        tenants = {str(row["gov_profile_id"]) for row in people
                   if str(row["contact_id"]) == str(move.get("gov_contact_id")) and row["created_at"] <= move["created_at"]}
        tenants |= {str(row["gov_profile_id"]) for row in offices
                    if str(row["organization_id"]) in targets and row["created_at"] <= move["created_at"]}
        if tenants:
            tenants_by_move[str(move["id"])] = tenants
    # ponytail: every member of a tracking company is told; narrow to the relationship owner if members ask.
    members = _select_in(sb, "profiles", "id,email,gov_profile_id", "gov_profile_id",
                         {tenant for tenants in tenants_by_move.values() for tenant in tenants})
    rows = []
    for move in moves:
        title, body = alert_text(move)
        for member in members:
            if str(member["gov_profile_id"]) in tenants_by_move.get(str(move["id"]), ()) and member.get("email"):
                rows.append({
                    "gov_profile_id": member["gov_profile_id"],
                    "user_id": member["id"],
                    "event_key": f"person_moved:{move['id']}",
                    "event_kind": "person_moved",
                    "title": title,
                    "body": body,
                    "payload": {
                        "role_history_id": move["id"], "contact_id": move.get("gov_contact_id"),
                        "event_type": move["event_type"], "effective_date": _day(move["effective_date"]),
                        "date_basis": move["date_basis"], "certainty": move_certainty(move),
                        "organization_id": move.get("organization_id"),
                        "previous_organization_id": move.get("previous_organization_id"), "source_url": move["source_url"],
                    },
                    "recipient_email": member["email"],
                })
    _upsert_batches(sb, "gov_user_notifications", rows, "event_key,user_id", ignore_duplicates=True)
    return {"moves_considered": len(moves), "notifications": len(rows)}


def run_move_monitor(sb: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The daily pass after every source has run: FPDS moves, then alerts for all newly recorded moves."""
    return {**detect_fpds_moves(sb, now=now), **alert_moves(sb, now=now)}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    print(json.dumps(run_move_monitor(get_supabase_client()), indent=2, default=str))

"""Shared writer for government people from every source.

Many source identifiers (e-mail, FPDS user id, vendor profile) resolve to one contact, so a person keeps one
row as they move. Positions carry whether their dates are stated by a source or only observed by Chromie, and
every detected move lands in the one ledger, gov_contact_role_history.
"""

from __future__ import annotations

import re
import string
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Iterable


PAGE_SIZE = 1000
FPDS_SOURCE = "fpds_staff"
NOTICE_SOURCE = "sam_notice_contacts"
BIO_SOURCE = "bio_pages"
RELEASE_SOURCE = "war_gov_releases"
NOMINATION_SOURCE = "congress_nominations"
DVIDS_SOURCE = "dvids_leadership"
EMAIL_RULE_SOURCE = "email_rule"
# Sources that write their own positions through this module. The directory affiliation pass must not
# treat contacts they create as directory evidence.
MONITOR_SOURCES = frozenset({FPDS_SOURCE, NOTICE_SOURCE, BIO_SOURCE, RELEASE_SOURCE, NOMINATION_SOURCE, DVIDS_SOURCE})
CONTACT_ROLES = frozenset({"contracting_officer", "contract_specialist", "program", "osdbu", "other"})
MOVE_CONFLICT = "source_provider,source_ref,person_identity_key,event_type"

# The office part is an activity address code (N00024, FA8650, W912CH, HR0011, SPE4A0): one to three letters, then
# a digit. A six-letter surname (CASEY.QUIXLE@US.AF.MIL) is not an office, or two Caseys become one person.
_FPDS_ID_RE = re.compile(
    r"^(?P<who>.+?)\.(?:(?P<kind>CIV|MIL|CTR)\.)?(?P<code>(?=[A-Z0-9]{6}@)[A-Z]{1,3}\d[A-Z0-9]*)@(?P<dom>[A-Z.]+)$")
_EMAIL_KIND_RE = re.compile(r"\.(?:civ|mil|ctr)$")
_SHARED_INBOX_RE = re.compile(
    r"(?:^|[._-])(office|info|help|support|contact|admin|acquisition|contracts?|"
    r"procurement|smallbusiness|osdbu|inquiries|mailbox|team)(?:[._+-]|@)",
    re.IGNORECASE,
)
# Ids per filter: 150 uuids keep a PostgREST URL well under the proxy limit. Longer values (identity keys that
# carry a name and agency) fill the same encoded length with fewer per request.
_QUERY_CHUNK = 150
_QUERY_CHARS = 6000


def normalize_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def is_shared_inbox(email: object) -> bool:
    return bool(_SHARED_INBOX_RE.search(str(email or "").strip()))


def _paged(query_factory: Callable[[], Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while True:
        query = query_factory()
        if hasattr(query, "range"):
            response = query.range(len(rows), len(rows) + PAGE_SIZE - 1).execute()
        else:
            response = query.limit(PAGE_SIZE).execute()
        page = [dict(row) for row in (getattr(response, "data", None) or [])]
        rows.extend(page)
        if len(page) < PAGE_SIZE or not hasattr(query, "range"):
            return rows


def _upsert_batches(sb: Any, table: str, rows: list[dict[str, Any]], conflict: str, **options: Any) -> None:
    for start in range(0, len(rows), 500):
        sb.table(table).upsert(rows[start : start + 500], on_conflict=conflict, **options).execute()


def _insert_batches(sb: Any, table: str, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), 500):
        sb.table(table).insert(rows[start : start + 500]).execute()


def _query_chunks(values: list[str]) -> Iterable[list[str]]:
    chunk: list[str] = []
    size = 0
    for value in values:
        cost = len(urllib.parse.quote(str(value))) + 3
        if chunk and (len(chunk) >= _QUERY_CHUNK or size + cost > _QUERY_CHARS):
            yield chunk
            chunk, size = [], 0
        chunk.append(value)
        size += cost
    if chunk:
        yield chunk


def _select_in(sb: Any, table: str, columns: str, field: str, values: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk in _query_chunks(sorted(set(values))):
        rows.extend(_paged(lambda chunk=chunk: sb.table(table).select(columns).in_(field, chunk).order("id")))
    return rows


def mail_domain(value: object) -> str:
    """Normalize a DoD mail domain: US.NAVY.MIL, NAVY.MIL and a bare NAVY are one domain."""
    domain = str(value or "").strip().strip(".").lower()
    domain = domain[3:] if domain.startswith("us.") else domain
    return domain if "." in domain else f"{domain}.mil"


def parse_fpds_user(value: object) -> dict[str, str] | None:
    """Split an FPDS createdBy/approvedBy id (FIRST.M.LAST[.CIV].N00024@NAVY.MIL) into its parts."""
    raw = str(value or "").strip().upper()
    match = _FPDS_ID_RE.match(raw)
    if not match:
        return None
    who = match["who"].lower()
    return {
        "user": raw.lower(),
        "person": f"{who}@{mail_domain(match['dom'])}",
        "office": match["code"],
        "kind": match["kind"] or "",
        "name": " ".join(string.capwords(part, "-") for part in re.sub(r"\d+", "", who).split(".") if part),
    }


def email_person_key(email: object) -> str | None:
    """The FPDS person key a DoD e-mail implies (jane.q.doe.civ@us.navy.mil -> jane.q.doe@navy.mil)."""
    local, _, domain = str(email or "").strip().lower().partition("@")
    if not local or "." not in local or not domain or is_shared_inbox(email):
        return None
    domain = mail_domain(domain)
    return f"{_EMAIL_KIND_RE.sub('', local)}@{domain}" if domain.endswith(".mil") else None


def person_identifiers(person: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(kind, value, basis) in resolution priority: e-mail, FPDS user, FPDS person, then source ids."""
    found: list[tuple[str, str, str]] = []
    email = str(person.get("email") or "").strip().lower()
    if email and not is_shared_inbox(email):
        found.append(("email", email, "exact"))
    fpds = parse_fpds_user(person.get("fpds_user"))
    if fpds:
        found += [("fpds_user", fpds["user"], "exact"), ("fpds_person", fpds["person"], "exact")]
    elif email and (key := email_person_key(email)):
        found.append(("fpds_person", key, "derived"))
    if username := str(person.get("username") or "").strip().lower():
        found.append(("username", username, "exact"))
    for kind, value in person.get("identifiers") or []:
        if str(value or "").strip():
            found.append((kind, str(value).strip(), "exact"))
    return found


def identity_key(person: dict[str, Any]) -> str:
    """The gov_contacts identity a new contact gets; matches the tools index format for e-mail and name."""
    email = str(person.get("email") or "").strip().lower()
    if email and not is_shared_inbox(email):
        return f"email:{email}"
    fpds = parse_fpds_user(person.get("fpds_user"))
    if fpds:
        return f"fpds:{fpds['person']}"
    if username := str(person.get("username") or "").strip().lower():
        return f"fpds:{username}"
    name = normalize_text(person.get("name"))
    return f"name:{name}|agency:{normalize_text(person.get('agency'))}" if name else ""


def _contact_row(person: dict[str, Any], key: str, source: str, timestamp: str) -> dict[str, Any]:
    fpds = parse_fpds_user(person.get("fpds_user"))
    role = person.get("role") if person.get("role") in CONTACT_ROLES else "other"
    return {
        "identity_key": key,
        "name": str(person.get("name") or (fpds or {}).get("name") or "").strip(),
        "title": str(person.get("title") or "").strip() or None,
        "email": str(person.get("email") or "").strip().lower() or None,
        "agency": str(person.get("agency") or "").strip() or None,
        "role": role,
        "source": source,
        "source_url": str(person.get("source_url") or "").strip() or None,
        "last_seen": timestamp,
    }


def resolve_contacts(
    sb: Any, people: list[dict[str, Any]], *, source: str, now: datetime | None = None
) -> tuple[list[str | None], dict[str, int]]:
    """Map each observed person to one contact id, creating contacts and identifiers as needed.

    Existing identifiers win in priority order; an identifier already held by another contact is never
    moved (counted as a conflict for review). Contacts that already exist keep their fields.
    """
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    candidates = [person_identifiers(person) for person in people]
    keys = [identity_key(person) for person in people]
    mapped = {
        (row["kind"], row["value"]): row
        for row in _select_in(
            sb, "gov_contact_identifiers", "id,contact_id,kind,value", "value",
            {value for found in candidates for _, value, _ in found},
        )
    }
    by_identity = {
        row["identity_key"]: str(row["id"])
        for row in _select_in(sb, "gov_contacts", "id,identity_key", "identity_key", {key for key in keys if key})
    }
    # A contact keyed on an e-mail holds that e-mail before it is recorded, so a derived FPDS key held by an
    # FPDS-only contact never outranks it.
    for key, contact in by_identity.items():
        if key.startswith("email:"):
            mapped.setdefault(("email", key.removeprefix("email:")), {"id": None, "contact_id": contact})

    resolved: list[str | None] = []
    new_contacts: dict[str, dict[str, Any]] = {}
    # Two new people sharing an identifier in one batch (JOHN.DOE@NAVY.MIL and JOHN.DOE.N00024@NAVY.MIL)
    # become one new contact under the first one's identity.
    pending: dict[tuple[str, str], str] = {}
    for index, (person, found, key) in enumerate(zip(people, candidates, keys)):
        hits = [str(mapped[(kind, value)]["contact_id"]) for kind, value, _ in found if (kind, value) in mapped]
        contact = hits[0] if hits else by_identity.get(key)
        if contact is None and key:
            key = next((pending[(kind, value)] for kind, value, _ in found if (kind, value) in pending), key)
            keys[index] = key
            new_contacts.setdefault(key, _contact_row(person, key, source, timestamp))
            for kind, value, _ in found:
                pending.setdefault((kind, value), key)
        resolved.append(contact)
    created = [row for row in new_contacts.values() if row["name"]]
    if created:
        _upsert_batches(sb, "gov_contacts", created, "identity_key", ignore_duplicates=True)
        by_identity.update({
            row["identity_key"]: str(row["id"])
            for row in _select_in(sb, "gov_contacts", "id,identity_key", "identity_key", [row["identity_key"] for row in created])
        })
        resolved = [contact or by_identity.get(key) for contact, key in zip(resolved, keys)]

    additions: dict[tuple[str, str], dict[str, Any]] = {}
    touched: set[str] = set()
    conflicts = 0
    for contact, found in zip(resolved, candidates):
        for kind, value, basis in found if contact else []:
            current = mapped.get((kind, value))
            if current is None or current["id"] is None:
                additions.setdefault((kind, value), {
                    "contact_id": contact, "kind": kind, "value": value, "source": source, "basis": basis,
                    "first_seen": timestamp, "last_seen": timestamp,
                })
            elif str(current["contact_id"]) == contact:
                touched.add(str(current["id"]))
            else:
                conflicts += 1
    _upsert_batches(sb, "gov_contact_identifiers", list(additions.values()), "kind,value", ignore_duplicates=True)
    ordered = sorted(touched)
    for start in range(0, len(ordered), _QUERY_CHUNK):
        sb.table("gov_contact_identifiers").update(
            {"last_seen": timestamp, "updated_at": timestamp}
        ).in_("id", ordered[start : start + _QUERY_CHUNK]).execute()
    return resolved, {
        "contacts_created": len(created),
        "identifiers_added": len(additions),
        "identifier_conflicts": conflicts,
    }


def record_activity(
    sb: Any, activity: dict[tuple[str, str], tuple[str, str, int]], *, now: datetime | None = None
) -> int:
    """Merge one batch of (first day, last day, record count) into identifiers that already exist.

    A batch that starts after the stored last day is new records and adds to the count; a batch that overlaps
    the stored span is a reread (a backfill rerun, a resumed day) and keeps the larger count, so reruns never
    inflate it.
    """
    # ponytail: an overlapping batch that is partly new undercounts; the count only gates the >= 3 move rule.
    if not activity:
        return 0
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    rows = []
    for row in _select_in(sb, "gov_contact_identifiers", "*", "value", {value for _, value in activity}):
        batch = activity.get((row["kind"], row["value"]))
        if not batch:
            continue
        first, last, count = batch
        if row.get("activity_count"):
            stored_first, stored_last = _day(row["activity_first_on"]), _day(row["activity_last_on"])
            count = row["activity_count"] + count if first > stored_last else max(row["activity_count"], count)
            first, last = min(first, stored_first), max(last, stored_last)
        if (first, last, count) != (_day(row.get("activity_first_on")), _day(row.get("activity_last_on")), row.get("activity_count")):
            rows.append({**row, "activity_first_on": first, "activity_last_on": last, "activity_count": count,
                         "updated_at": timestamp})
    _upsert_batches(sb, "gov_contact_identifiers", rows, "kind,value")
    return len(rows)


def link_identities(sb: Any, *, now: datetime | None = None) -> dict[str, int]:
    """Attach the FPDS person key each DoD e-mail implies; fold an FPDS-only contact into the e-mail contact.

    The rule is deterministic (same local part and domain once the .civ/.mil/.ctr tag and the us. prefix are
    removed). Two e-mail contacts sharing a key are left apart and counted for review.
    """
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    # ponytail: rescans every DoD e-mail contact per run; filter on updated_at since the last run if it grows.
    contacts = _paged(
        lambda: sb.table("gov_contacts").select("id,identity_key,email")
        .like("identity_key", "email:%").ilike("email", "%.mil").order("id")
    )
    keyed = {str(row["id"]): key for row in contacts if (key := email_person_key(row.get("email")))}
    held = {
        row["value"]: row
        for row in _select_in(sb, "gov_contact_identifiers", "id,contact_id,kind,value", "value", set(keyed.values()))
        if row["kind"] == "fpds_person"
    }
    holders = {
        str(row["id"]): row["identity_key"]
        for row in _select_in(sb, "gov_contacts", "id,identity_key", "id", {str(row["contact_id"]) for row in held.values()})
    }
    additions: list[dict[str, Any]] = []
    merged = conflicts = 0
    claimed: set[str] = set()
    for contact_id, key in sorted(keyed.items()):
        current = held.get(key)
        if key in claimed:
            conflicts += 1
        elif current is None:
            additions.append({
                "contact_id": contact_id, "kind": "fpds_person", "value": key, "source": EMAIL_RULE_SOURCE,
                "basis": "rule", "first_seen": timestamp, "last_seen": timestamp,
            })
        elif str(current["contact_id"]) == contact_id:
            pass
        elif str(holders.get(str(current["contact_id"]), "")).startswith("fpds:"):
            sb.rpc("gov_merge_contacts", {"p_survivor": contact_id, "p_duplicate": str(current["contact_id"])}).execute()
            merged += 1
        else:
            conflicts += 1
        claimed.add(key)
    _upsert_batches(sb, "gov_contact_identifiers", additions, "kind,value", ignore_duplicates=True)

    # The same rule read from the other side: an FPDS-only contact named fpds:<key> whose key another contact
    # now holds is that contact.
    orphans = {
        row["identity_key"][len("fpds:"):]: str(row["id"])
        for row in _paged(lambda: sb.table("gov_contacts").select("id,identity_key").like("identity_key", "fpds:%").order("id"))
    }
    for row in _select_in(sb, "gov_contact_identifiers", "contact_id,kind,value", "value", orphans):
        orphan = orphans[row["value"]]
        if row["kind"] == "fpds_person" and str(row["contact_id"]) != orphan:
            sb.rpc("gov_merge_contacts", {"p_survivor": str(row["contact_id"]), "p_duplicate": orphan}).execute()
            merged += 1
    return {"identifiers_added": len(additions), "contacts_merged": merged, "identity_conflicts": conflicts}


def _position_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(row["contact_id"]), str(row["organization_id"]), str(row["role_type"]), str(row.get("source_ref") or ""))


def _day(value: object) -> str:
    return str(value or "")[:10]


def _iso(value: object) -> str:
    """One timestamp form for comparison and storage; a bare date means midnight UTC."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


_DESCRIBING_FIELDS = ("raw_title", "source_url", "confidence")


def upsert_positions(
    sb: Any,
    rows: list[dict[str, Any]],
    *,
    source: str,
    now: datetime | None = None,
    close_missing: bool = False,
) -> dict[str, int]:
    """Open, extend or close positions for one source.

    Each row needs contact_id, organization_id, role_type, source_ref and first/last_observed_at; a row from a
    source that states its dates sets date_basis='stated' and valid_from. Observed positions never get a start
    date. With close_missing (a full-roster source), an open position the source no longer lists closes on its
    last observed day; event sources leave closure to the move detector.
    """
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    desired: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        row = {**row, "first_observed_at": _iso(row["first_observed_at"]), "last_observed_at": _iso(row["last_observed_at"])}
        key = _position_key(row)
        current = desired.get(key)
        if current:
            newer = row["last_observed_at"] >= current["last_observed_at"]
            current["first_observed_at"] = min(current["first_observed_at"], row["first_observed_at"])
            current["last_observed_at"] = max(current["last_observed_at"], row["last_observed_at"])
            # The latest observation names the post; an older one only fills a gap.
            for field in _DESCRIBING_FIELDS:
                current[field] = (row.get(field) or current.get(field)) if newer else (current.get(field) or row.get(field))
            current["valid_from"] = row.get("valid_from") or current.get("valid_from")
            current["date_basis"] = "stated" if "stated" in (current["date_basis"], row.get("date_basis")) else "observed"
        else:
            desired[key] = {**row, "date_basis": row.get("date_basis") or "observed"}

    if close_missing:
        existing = _paged(lambda: sb.table("gov_contact_positions").select("*").eq("source", source).order("id"))
    else:
        # An event source touches a few people per call; read only theirs.
        existing = [
            row
            for row in _select_in(sb, "gov_contact_positions", "*", "contact_id", {row["contact_id"] for row in desired.values()})
            if row.get("source") == source
        ]
    active = {_position_key(row): row for row in existing if row.get("valid_to") is None}
    # A closed post reopens only on newer evidence: a stated start after a stated close (a stale bio never undoes a
    # change of command); after an observed close, an observation after it, or a full roster listing the post again.
    ended: dict[tuple[str, str, str, str], tuple[str, bool]] = {}
    for row in existing:
        if row.get("valid_to") is not None:
            key, day = _position_key(row), _day(row["valid_to"])
            if key not in ended or day > ended[key][0]:
                ended[key] = (day, row.get("date_basis") == "stated")

    inserted = updated = reactivated = 0
    inserts: list[dict[str, Any]] = []
    for key, row in desired.items():
        stated_from = row.get("valid_from") if row["date_basis"] == "stated" else None
        current = active.get(key)
        if current:
            patch = {}
            first_seen = _iso(current.get("first_observed_at") or row["first_observed_at"])
            last_seen = _iso(current.get("last_observed_at") or current.get("observed_at") or row["last_observed_at"])
            if row["first_observed_at"] < first_seen or not current.get("first_observed_at"):
                patch["first_observed_at"] = min(row["first_observed_at"], first_seen)
            if row["last_observed_at"] > last_seen or not current.get("last_observed_at"):
                patch["last_observed_at"] = max(row["last_observed_at"], last_seen)
                patch["observed_at"] = patch["last_observed_at"]
            # ponytail: strict "newer", so pages read in any order settle; a same-day title change waits a day.
            newer = row["last_observed_at"] > last_seen
            for field in _DESCRIBING_FIELDS:
                if row.get(field) is not None and row.get(field) != current.get(field) and (newer or current.get(field) is None):
                    patch[field] = row[field]
            if stated_from and current.get("valid_from") != stated_from:
                patch.update(valid_from=stated_from, date_basis="stated")
            if patch:
                sb.table("gov_contact_positions").update({**patch, "updated_at": timestamp}).eq("id", current["id"]).execute()
                updated += 1
            continue
        if key in ended:
            day, stated = ended[key]
            if not (_day(stated_from) > day if stated else close_missing or _day(row["last_observed_at"]) > day):
                continue
            reactivated += 1
        else:
            inserted += 1
        inserts.append({
            "contact_id": row["contact_id"],
            "organization_id": row["organization_id"],
            "role_type": row["role_type"],
            "raw_title": row.get("raw_title"),
            "valid_from": stated_from,
            "valid_to": None,
            "date_basis": "stated" if stated_from else "observed",
            "first_observed_at": row["first_observed_at"],
            "last_observed_at": row["last_observed_at"],
            "observed_at": row["last_observed_at"],
            "confidence": row.get("confidence"),
            "source": source,
            "source_ref": row.get("source_ref"),
            "source_url": row.get("source_url"),
            "updated_at": timestamp,
        })
    _insert_batches(sb, "gov_contact_positions", inserts)

    closed = 0
    if close_missing:
        by_day: dict[str, list[str]] = {}
        for key, row in active.items():
            if key not in desired:
                day = max(_day(row.get("last_observed_at") or row.get("observed_at")), _day(row.get("valid_from")))
                by_day.setdefault(day, []).append(row["id"])
        for day, ids in sorted(by_day.items()):
            for start in range(0, len(ids), _QUERY_CHUNK):
                sb.table("gov_contact_positions").update(
                    {"valid_to": day, "updated_at": timestamp}
                ).in_("id", ids[start : start + _QUERY_CHUNK]).execute()
            closed += len(ids)
    return {
        "positions_inserted": inserted,
        "positions_updated": updated,
        "positions_reactivated": reactivated,
        "positions_closed": closed,
    }


_MOVE_REQUIRED = (
    "person_identity_key", "name", "agency_name", "title", "event_type", "effective_date", "reported_at",
    "source_provider", "source_ref", "source_url", "date_basis",
)


def record_moves(sb: Any, moves: list[dict[str, Any]], *, now: datetime | None = None) -> int:
    """Upsert monitor moves into gov_contact_role_history; a rerun of the same source move changes nothing new."""
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    rows = []
    for move in moves:
        missing = [field for field in _MOVE_REQUIRED if not str(move.get(field) or "").strip()]
        if missing:
            raise ValueError(f"move is missing {', '.join(missing)}")
        if not isinstance(move.get("evidence"), list) or not move["evidence"]:
            raise ValueError("move needs at least one evidence item")
        rows.append({**move, "updated_at": timestamp})
    _upsert_batches(sb, "gov_contact_role_history", rows, MOVE_CONFLICT)
    return len(rows)

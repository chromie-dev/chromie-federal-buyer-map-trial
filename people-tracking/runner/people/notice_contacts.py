"""Observed positions from SAM notice points of contact.

A notice names its contacts at the office that posted it (fullParentPathCode ends in the contracting office
code FPDS uses), so each listed person is observed at that office on the notice's posted date. Notices state no
start or end date; positions stay observed and extend as later notices name the same person.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from importlib import import_module
from typing import Any, Iterable

from orchestration.gov.people.fpds_staff import office_organization_id, office_rows
from orchestration.gov.people.writer import (
    NOTICE_SOURCE,
    _upsert_batches,
    is_shared_inbox,
    resolve_contacts,
    upsert_positions,
)
from orchestration.gov.people_affiliations import position_role

_OFFICE_CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
_STATE_RE = re.compile(r"^[A-Z]{2}$")


def notice_office(item: dict[str, Any]) -> dict[str, str] | None:
    """The posting office of a SAM notice: 017.1700.NAVAIR.NAVAIR HQS.N00019 -> N00019 under subtier 1700."""
    codes = [part.strip() for part in str(item.get("fullParentPathCode") or "").split(".")]
    if len(codes) < 3 or not _OFFICE_CODE_RE.match(codes[-1].upper()):
        return None
    names = [part.strip() for part in str(item.get("fullParentPathName") or "").split(".")]
    address = item.get("officeAddress") if isinstance(item.get("officeAddress"), dict) else {}
    state = str(address.get("state") or "").strip().upper()
    return {
        "office_code": codes[-1].upper(),
        # A name with its own periods (U.S. ...) no longer lines up with the codes; the office keeps its code.
        "office_name": names[-1] if len(names) == len(codes) else "",
        "agency_code": codes[1],
        # Where the office sits; vendor profiles join a person only when their place agrees.
        "city": str(address.get("city") or "").strip(),
        "state": state if _STATE_RE.match(state) else "",
    }


def _posted_day(item: dict[str, Any]) -> str:
    try:
        return date.fromisoformat(str(item.get("postedDate") or "")[:10]).isoformat()
    except ValueError:
        return ""


def notice_observations(items: Iterable[Any]) -> list[dict[str, Any]]:
    """One observation per named person per notice; shared inboxes and name-only contacts carry no identity."""
    sam_api = import_module("tools.gov_sam_api_search")
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        office = notice_office(item)
        day = _posted_day(item)
        notice_id = str(item.get("noticeId") or "").strip()
        if not (office and day and notice_id):
            continue
        url = f"https://sam.gov/opp/{notice_id}/view"
        for contact in sam_api.contacts_from_sam_item(item):
            if not contact.email or is_shared_inbox(contact.email):
                continue
            role = position_role({"title": contact.title})
            found.append({
                "person": {
                    "email": contact.email,
                    "name": contact.name or contact.email,
                    "title": contact.title or None,
                    "agency": str(item.get("fullParentPathName") or "").strip() or None,
                    "role": contact.role,
                    "source_url": url,
                },
                "office": office,
                # PCO/ACO titles only the contact classifier reads.
                "role_type": role if role != "other" else position_role({"role": contact.role}),
                "day": day,
                "raw_title": contact.title or None,
                "source_url": url,
            })
    return found


def write_notice_positions(sb: Any, items: Iterable[Any], *, now: datetime | None = None) -> dict[str, int]:
    """Extend the observed position of every person a page of notices names, at each notice's office."""
    clock = now or datetime.now(timezone.utc)
    found = notice_observations(items)
    if not found:
        return {"notice_people": 0, "positions_inserted": 0, "positions_updated": 0}
    # Offices FPDS already named keep their names; a notice only adds offices not seen before.
    offices = office_rows(sb, [entry["office"] for entry in found], clock.isoformat())
    _upsert_batches(sb, "gov_organizations", offices, "id", ignore_duplicates=True)
    places = {entry["office"]["office_code"]: entry["office"] for entry in found if entry["office"]["state"]}
    # ponytail: one update per office per page; a page names a few dozen offices at most.
    for code, office in sorted(places.items()):
        sb.table("gov_organizations").update({"address_city": office["city"] or None, "address_state": office["state"]}).eq(
            "id", office_organization_id(code)).execute()
    contact_ids, identity = resolve_contacts(sb, [entry["person"] for entry in found], source=NOTICE_SOURCE, now=clock)
    positions = upsert_positions(sb, [
        {
            "contact_id": contact,
            "organization_id": office_organization_id(entry["office"]["office_code"]),
            "role_type": entry["role_type"],
            "raw_title": entry["raw_title"],
            "source_ref": entry["office"]["office_code"],
            "source_url": entry["source_url"],
            "first_observed_at": entry["day"],
            "last_observed_at": entry["day"],
        }
        for entry, contact in zip(found, contact_ids)
        if contact
    ], source=NOTICE_SOURCE, now=clock)
    return {"notice_people": len({entry["person"]["email"] for entry in found}), **identity, **positions}

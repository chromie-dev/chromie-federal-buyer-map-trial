"""FPDS contracting staff: who created and approved each contract action, at which office, and when.

Every FPDS action names the user who created it and the user who approved it (usually the contracting officer),
in the form FIRST.M.LAST[.CIV|.MIL|.CTR].<office code>@DOMAIN. Each action is an observation that the person
worked at the action's contracting office on that day; no FPDS record states a start or end date, so every
position written here is observed. Two readers feed one roster: the public ATOM feed (retiring in FY2026; saved
pages and capped office windows backfill from it) and the SAM Contract Awards API, its replacement.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import string
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import NAMESPACE_URL, uuid5

import httpx

from orchestration.gov.competitive.api_cache import cache_get, cache_put
from orchestration.gov.competitive.sources import _FPDS_AWARD_ID_RE, _FPDS_ENTRY_RE, _fpds_tag
from orchestration.gov.people.writer import (
    FPDS_SOURCE,
    _paged,
    _select_in,
    _upsert_batches,
    is_shared_inbox,
    link_identities,
    normalize_text,
    parse_fpds_user,
    record_activity,
    resolve_contacts,
    upsert_positions,
)
from orchestration.supabase_client import get_supabase_client


OFFICE_SOURCE = "fpds_office"
SAM_AWARDS_URL = "https://api.sam.gov/contract-awards/v1/search"
SAM_PAGE_SIZE = 100
# DoD actions reach the public record 90 days after signature; the monitor reads each day once it is public.
DEFAULT_LAG_DAYS = 90
DEFAULT_DAILY_CALLS = 50
_CURSOR_SOURCE = "people_fpds_staff"
ROLES = {"approved": "contracting_officer", "created": "contract_specialist"}
_IDV_ID_RE = re.compile(r"<ns1:IDVID>(.*?)</ns1:IDVID>", re.S)
_LINK_RE = re.compile(r'<link[^>]*rel="alternate"[^>]*href="([^"]+)"')
# Civilian agencies log usernames (MORGAN.QUIXLE7001, JQUIXLE7014); these are accounts, not people.
_SYSTEM_USER_RE = re.compile(r"^(?:FPDS_|DOD_|ACPS\d)|ADMIN|MIGRATOR|CLOSEOUT|SYSTEM|BATCH|INTERFACE")
# Shared accounts that take the DoD id form (PADDS.W91CRB@KO.ARMY.MIL is the Army contract-writing system).
_SYSTEM_ACCOUNTS = {"padds", "guest"}
# NASA's contract-writing accounts put WPA. before the name (WPA.FIRST.LAST@NASA.GOV). The account stays the key:
# production contacts built from award records are keyed on it, and a stripped key would split them in two.
_ACCOUNT_PREFIX_RE = re.compile(r"^WPA\.(?=[^.@]+\.[^@]+@)", re.I)
_OFFICE_CODE_RE = re.compile(r"(?=[A-Z0-9]{6}$)[A-Z]{1,3}\d[A-Z0-9]*", re.I)
_EMPLOYMENT_TAGS = {"civ", "mil", "ctr"}


def _attr(block: str, tag: str, name: str) -> str:
    match = re.search(rf'<ns1:{tag}\b[^>]*\b{name}="([^"]*)"', block)
    return html.unescape(match.group(1)).strip() if match else ""


def _clean(value: object) -> str:
    return html.unescape(str(value or "")).strip()


def award_url(piid: str, agency: str) -> str:
    query = urllib.parse.quote_plus(f"{piid} {agency}".strip())
    return f"https://www.fpds.gov/ezsearch/search.do?s=FPDS&indexName=awardfull&templateName=1.5.3&q={query}"


def _action(*, agency: str, agency_name: str, award_agency: str, piid: str, mod: str, signed: str, office: str,
            office_name: str, staff: list[tuple[str, str, str]], url: str) -> dict[str, Any] | None:
    if not (piid and office and signed):
        return None
    return {
        "agency_code": agency, "agency_name": agency_name, "piid": piid, "mod": mod, "signed": signed[:10],
        "office_code": office.upper(), "office_name": office_name,
        "staff": [(role, user.upper(), (day or signed)[:10]) for role, user, day in staff if user],
        "url": url or award_url(piid, award_agency),
    }


def atom_actions(xml: str) -> list[dict[str, Any]]:
    """Contract actions in one FPDS ATOM page (awards and IDVs)."""
    actions = []
    for match in _FPDS_ENTRY_RE.finditer(xml or ""):
        entry = match.group(1)
        block = (_FPDS_AWARD_ID_RE.search(entry) or _IDV_ID_RE.search(entry))
        ids = block.group(1) if block else entry
        link = _LINK_RE.search(entry)
        action = _action(
            agency=_fpds_tag(entry, "contractingOfficeAgencyID") or "",
            agency_name=_attr(entry, "contractingOfficeAgencyID", "name"),
            award_agency=_fpds_tag(ids, "agencyID") or "",
            piid=_fpds_tag(ids, "PIID") or "",
            mod=_fpds_tag(ids, "modNumber") or "",
            signed=_fpds_tag(entry, "signedDate") or "",
            office=_fpds_tag(entry, "contractingOfficeID") or "",
            office_name=_attr(entry, "contractingOfficeID", "name"),
            staff=[
                ("created", _clean(_fpds_tag(entry, "createdBy")), _fpds_tag(entry, "createdDate") or ""),
                ("approved", _clean(_fpds_tag(entry, "approvedBy")), _fpds_tag(entry, "approvedDate") or ""),
            ],
            url=html.unescape(link.group(1)) if link else "",
        )
        if action:
            actions.append(action)
    return actions


def _get(record: dict[str, Any], *path: str) -> str:
    value: Any = record
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return _clean(value)


def sam_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Contract actions in one SAM Contract Awards search page."""
    actions = []
    for record in payload.get("awardSummary") or []:
        office = ("coreData", "federalOrganization", "contractingInformation")
        transaction = ("awardDetails", "transactionData")
        action = _action(
            agency=_get(record, *office, "contractingSubtier", "code"),
            agency_name=_get(record, *office, "contractingSubtier", "name"),
            award_agency=_get(record, "contractId", "subtier", "code"),
            piid=_get(record, "contractId", "piid"),
            mod=_get(record, "contractId", "modificationNumber"),
            signed=_get(record, "awardDetails", "dates", "dateSigned"),
            office=_get(record, *office, "contractingOffice", "code"),
            office_name=_get(record, *office, "contractingOffice", "name"),
            staff=[
                ("created", _get(record, *transaction, "createdBy"), _get(record, *transaction, "createdDate")),
                ("approved", _get(record, *transaction, "approvedBy"), _get(record, *transaction, "approvedDate")),
            ],
            url="",
        )
        if action:
            actions.append(action)
    return actions


def saved_page_actions(directory: str | Path) -> Iterable[dict[str, Any]]:
    """Actions from saved ATOM pages (files ending _ATOM), read-only."""
    for path in sorted(Path(directory).expanduser().glob("*_ATOM")):
        yield from atom_actions(path.read_text(errors="ignore"))


def staff_identity(user: str) -> tuple[str, str, str] | None:
    """(person key, kind, display name) for one FPDS user id; None for system accounts."""
    if re.split(r"[.@_]", str(user or "").lower())[0] in _SYSTEM_ACCOUNTS:
        return None
    parsed = parse_fpds_user(user)
    if parsed:
        return parsed["person"], "dod", parsed["name"]
    if not user or _SYSTEM_USER_RE.search(user) or is_shared_inbox(user):
        return None
    if "@" in user:
        local = _ACCOUNT_PREFIX_RE.sub("", user).split("@", 1)[0]
        # An office code (JANE.DOE.N00173@WEB1700.NRL), a digit telling namesakes' accounts apart (JANE.DOE.1) and
        # the employment tag (.CTR) are not part of the name; the key keeps them all.
        words = [re.sub(r"\d+", "", word) for word in re.split(r"[._]", local) if not _OFFICE_CODE_RE.fullmatch(word)]
        words = [string.capwords(word, "-") for word in words if word and word.lower() not in _EMPLOYMENT_TAGS]
        return user.lower(), "email", " ".join(words)
    words = [part for part in re.split(r"[._]", re.sub(r"^\d+|\d+$", "", user)) if part]
    return user.lower(), "username", " ".join(string.capwords(word, "-") for word in words) if len(words) > 1 else user


def roster(actions: Iterable[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """One entry per (person, office, role), counted once per action (agency, PIID, modification)."""
    seen: set[tuple[str, str, str]] = set()
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for action in actions:
        key = (action["agency_code"], action["piid"], action["mod"])
        if key in seen:
            continue
        seen.add(key)
        for action_role, user, day in action["staff"]:
            identity = staff_identity(user)
            if not identity:
                continue
            person, kind, name = identity
            entry = entries.setdefault((person, action["office_code"], ROLES[action_role]), {
                "person": person, "kind": kind, "name": name, "users": set(), "office_code": action["office_code"],
                "office_name": action["office_name"], "agency_code": action["agency_code"],
                "agency_name": action["agency_name"], "role_type": ROLES[action_role],
                "first": day, "last": day, "actions": 0, "url": action["url"], "accounts": {},
            })
            entry["users"].add(user)
            # An account's activity is its work for the office its id names, dated by the signing day the record
            # states. An account re-coded while its owner serves the same office is not a move, so work it does
            # for another office does not count.
            if (parse_fpds_user(user) or {}).get("office") == action["office_code"]:
                first, last, count = entry["accounts"].get(user, (action["signed"], action["signed"], 0))
                entry["accounts"][user] = (min(first, action["signed"]), max(last, action["signed"]), count + 1)
            entry["actions"] += 1
            entry["first"] = min(entry["first"], day)
            if day >= entry["last"]:
                entry["last"], entry["url"] = day, action["url"]
            entry["office_name"] = entry["office_name"] or action["office_name"]
    return entries


def office_organization_id(code: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"chromie:contracting-office:{code.upper()}"))


def office_rows(sb: Any, entries: Iterable[dict[str, Any]], timestamp: str) -> list[dict[str, Any]]:
    offices: dict[str, dict[str, Any]] = {}
    for entry in entries:
        office = offices.setdefault(entry["office_code"], {"name": "", "agency_code": entry["agency_code"]})
        office["name"] = office["name"] or entry["office_name"]
    agencies = _select_in(sb, "agencies", "id,subtier_code", "subtier_code", {o["agency_code"] for o in offices.values() if o["agency_code"]})
    agency_by_code = {str(row["subtier_code"]): str(row["id"]) for row in agencies}
    parents = {
        str(row["existing_agency_id"]): row
        for row in _select_in(sb, "gov_organizations", "id,agency_id,existing_agency_id", "existing_agency_id", agency_by_code.values())
    }
    rows = []
    for code, office in sorted(offices.items()):
        parent = parents.get(agency_by_code.get(office["agency_code"], ""))
        name = office["name"] or code
        rows.append({
            "id": office_organization_id(code),
            "agency_id": parent.get("agency_id") if parent else None,
            "parent_organization_id": parent["id"] if parent else None,
            "name": name,
            "normalized_name": normalize_text(name) or code.lower(),
            "org_type": "contracting_office",
            "aliases": [code],
            "normalized_aliases": [code.lower()],
            "external_ids": {"contracting_office_code": code, "contracting_agency_code": office["agency_code"]},
            "active": True,
            "source": OFFICE_SOURCE,
            "source_ref": code,
            "confidence": 1.0,
            "observed_at": timestamp,
            "updated_at": timestamp,
            "jurisdiction_code": "US",
            "jurisdiction_path": ["US"],
            "government_level": "federal",
        })
    return rows


def write_roster(sb: Any, actions: Iterable[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Resolve every staff member to one contact and extend their observed positions per office."""
    clock = now or datetime.now(timezone.utc)
    timestamp = clock.isoformat()
    entries = list(roster(actions).values())
    if not entries:
        return {"staff": 0, "offices": 0, "positions_inserted": 0, "positions_updated": 0}
    links = link_identities(sb, now=clock)
    offices = office_rows(sb, entries, timestamp)
    _upsert_batches(sb, "gov_organizations", offices, "id")

    people = []
    owners = []
    for index, entry in enumerate(entries):
        for user in sorted(entry["users"]):
            # One entry can hold ids of two kinds (JOHN.DOE@DARPA.MIL and its office-coded twin share a key).
            person, kind, name = staff_identity(user) or (entry["person"], entry["kind"], entry["name"])
            people.append({
                "fpds_user": user if kind == "dod" else None,
                "email": person if kind == "email" else None,
                "username": person if kind == "username" else None,
                "name": name,
                "agency": entry["agency_name"] or None,
                "role": entry["role_type"],
                "source_url": entry["url"],
            })
            owners.append(index)
    contact_ids, identity = resolve_contacts(sb, people, source=FPDS_SOURCE, now=clock)
    accounts: dict[tuple[str, str], tuple[str, str, int]] = {}
    for entry in entries:
        for user, (first, last, count) in entry["accounts"].items():
            if parsed := parse_fpds_user(user):
                held = accounts.get(("fpds_user", parsed["user"]))
                accounts[("fpds_user", parsed["user"])] = (
                    (min(held[0], first), max(held[1], last), held[2] + count) if held else (first, last, count))
    record_activity(sb, accounts, now=clock)
    contact_by_entry: dict[int, str] = {}
    for index, contact in zip(owners, contact_ids):
        if contact:
            contact_by_entry.setdefault(index, contact)

    positions = upsert_positions(sb, [
        {
            "contact_id": contact_by_entry[index],
            "organization_id": office_organization_id(entry["office_code"]),
            "role_type": entry["role_type"],
            "source_ref": entry["office_code"],
            "source_url": entry["url"],
            "first_observed_at": entry["first"],
            "last_observed_at": entry["last"],
            "confidence": 1.0,
        }
        for index, entry in enumerate(entries)
        if index in contact_by_entry
    ], source=FPDS_SOURCE, now=clock)
    return {
        "staff": len({entry["person"] for entry in entries}),
        "offices": len(offices),
        **identity,
        "identity_links": links,
        **positions,
    }



def backfill_saved_pages(sb: Any, directory: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Load the roster from saved ATOM pages before the feed retires; rerunning adds nothing new."""
    return write_roster(sb, saved_page_actions(directory), now=now)



class SamQuotaExhausted(RuntimeError):
    pass


def fetch_sam_page(params: dict[str, str]) -> dict[str, Any]:
    response = httpx.get(SAM_AWARDS_URL, params={**params, "api_key": os.environ["SAM_CONTRACT_AWARDS_API_KEY"]}, timeout=60)
    if response.status_code == 429:
        raise SamQuotaExhausted("SAM Contract Awards returned 429")
    response.raise_for_status()
    return response.json()


def monitored_offices(sb: Any) -> list[str]:
    """Offices any company tracks, plus CHROMIE_PEOPLE_FPDS_OFFICES (comma-separated office codes)."""
    configured = {code.strip().upper() for code in os.getenv("CHROMIE_PEOPLE_FPDS_OFFICES", "").split(",") if code.strip()}
    tracked = _paged(lambda: sb.table("gov_tracked_offices").select("organization_id").order("id"))
    rows = _select_in(sb, "gov_organizations", "id,external_ids", "id", {str(row["organization_id"]) for row in tracked})
    codes = {str((row.get("external_ids") or {}).get("contracting_office_code") or "").upper() for row in rows}
    return sorted((configured | codes) - {""})


def _sam_window(day: date) -> str:
    stamp = day.strftime("%m/%d/%Y")
    return f"[{stamp},{stamp}]"


def run_sam_staff_monitor(
    sb: Any,
    *,
    fetch: Callable[[dict[str, str]], dict[str, Any]] | None = None,
    now: datetime | None = None,
    offices: list[str] | None = None,
) -> dict[str, Any]:
    """Read each newly public signing day for every monitored office and extend the roster.

    The cursor (gov_api_cache) holds the last finished day, the offices done within the next day, and calls
    spent today, so a run that hits the daily call cap resumes where it stopped; SAM sends no quota headers.
    """
    if not os.getenv("SAM_CONTRACT_AWARDS_API_KEY") and fetch is None:
        return {"status": "skipped", "reason": "SAM_CONTRACT_AWARDS_API_KEY is not set"}
    clock = now or datetime.now(timezone.utc)
    fetch = fetch or fetch_sam_page
    offices = offices if offices is not None else monitored_offices(sb)
    if not offices:
        return {"status": "skipped", "reason": "no monitored offices"}
    lag = int(os.getenv("CHROMIE_PEOPLE_SAM_LAG_DAYS") or DEFAULT_LAG_DAYS)
    cap = int(os.getenv("CHROMIE_PEOPLE_SAM_DAILY_CALLS") or DEFAULT_DAILY_CALLS)
    horizon = clock.date() - timedelta(days=lag)
    cursor = cache_get(sb, _CURSOR_SOURCE, "sam_awards_cursor") or {}
    today = clock.date().isoformat()
    calls = int(cursor.get("calls") or 0) if cursor.get("calls_day") == today else 0
    through = date.fromisoformat(cursor["through"]) if cursor.get("through") else horizon - timedelta(days=1)
    done = set(cursor.get("done") or [])
    actions: list[dict[str, Any]] = []
    status = "complete"
    day = through + timedelta(days=1)
    while day <= horizon and status == "complete":
        for office in offices:
            if office in done:
                continue
            offset = 0
            while True:
                if calls >= cap:
                    status = "paused"
                    break
                calls += 1
                try:
                    page = fetch({"contractingOfficeCode": office, "dateSigned": _sam_window(day),
                                  "limit": str(SAM_PAGE_SIZE), "offset": str(offset)})
                except SamQuotaExhausted:
                    status = "paused"
                    break
                actions.extend(sam_actions(page))
                offset += SAM_PAGE_SIZE
                if offset >= int(page.get("totalRecords") or 0):
                    done.add(office)
                    break
            if status != "complete":
                break
        if status == "complete":
            through, done = day, set()
            day += timedelta(days=1)
    written = write_roster(sb, actions, now=clock)
    cache_put(sb, _CURSOR_SOURCE, "sam_awards_cursor", {
        "through": through.isoformat(), "done": sorted(done), "calls_day": today, "calls": calls,
    }, ttl_days=3650)
    return {"status": status, "through": through.isoformat(), "calls": calls, "actions": len(actions), **written}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill FPDS contracting staff from saved ATOM pages.")
    parser.add_argument("directory")
    print(json.dumps(backfill_saved_pages(get_supabase_client(), parser.parse_args().directory), default=str))

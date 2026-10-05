#!/usr/bin/env python3
"""Turn the Navy org memory and the LRAE datapacks into SQL for the prod agency-intelligence schema.

    python research/tools/agency_layers_sql.py > /tmp/navy_layers.sql
    psql "$LOCAL_DB" -v ON_ERROR_STOP=1 -f /tmp/navy_layers.sql
    python research/tools/agency_layers_sql.py --selfcheck

Reads research/memory/organization_seed.json (offices, observations, relationships, readings)
and datapack/lrae_*/layers/*.csv (needs, requirements, funding, procurement
references, evidence). Writes SQL against the tables the production migrations
`reconcile_agency_intelligence_prerequisites` and `agency_program_intelligence`
create. No network, no database driver, stdlib only, so the same inputs always
produce the same bytes.

The production model puts everything time-varying on `gov_intelligence_assertions`:
one dated, evidence-backed claim with a basis, a rationale and an optional
supersession, and a thin typed detail row keyed on `assertion_id`. Evidence is an
Agency Brain item wrapped in `gov_intelligence_evidence`. A `documented` assertion
will not commit without direct supporting evidence, so every claim here is written
with its detail and its citation in the same transaction.

Every id is a uuid5 of a fixed namespace and the record's own key, so re-running
inserts nothing. Nothing invents a value a source does not carry: rows the schema
cannot accept are counted and reported on stderr rather than padded.

One requirement, one need, every release a revision of it. Rows the release diffs tie
with `confirmed` (the same PID, or the same title under the same office code; see
`lrae_package.fold_map`) load under one key, so a tool reading the database alone sees
the full history. A row tied without carrying the PID itself writes its assertions with
basis `inferred` and the tie (basis, the two rows, the diff) in the rationale; the need's
description names it too. A chain the fold refuses (two PIDs, or two rows of one release)
loads as separate records and is reported on stderr.

Two more things ride on columns the production tables already have, so no table is added.
`research/sources/source_registry.json` loads into `gov_procurement_sources`, one row per source the
research inspected. Every Brain item the loader writes for a spreadsheet row, a SAM.gov notice
or an article carries `source_provider` (that registry row's key), `source_tier`,
`published_at` and, where the row is something that happened, `event_type`: a forecast line's
first release, a release that moved it, a notice by its type, an article by what it reports.
A release that restates a line as it was is no event. Three items are derived rather than read:
a leadership change at an office on a dated day, an incumbent contract's end as FPDS last stated
it, and each article claim that reports something that happened.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lrae_package import fold_map, fpds_history, manifest_rows, url_index  # noqa: E402
from fpds_sweep import awards as swept_awards  # noqa: E402
from ai_inventory import bureau_nodes  # noqa: E402

from agency import EVENTS as EVENTS_DIR, KEY as AGENCY_KEY, MEMORY, P, PROFILES, ROOT as AGENCY_ROOT, SAM_NOTICES, SOURCES, forecast_packs, note_is_foreign  # noqa: E402

# Provenance strings name the file each row was read from, relative to the repository, under the trial's prefix.
PROVENANCE = "chromie-federal-buyer-map-trial"
# The organization memory's claim keys, evidence ids and contact identity keys are namespaced by the profile that
# wrote the seed: the Navy's stay `navy-org-memory` (the loaded ids depend on it), another agency's carry its key.
MEMORY_NS = f"{AGENCY_KEY}-org-memory"


def provenance(path: Path) -> str:
    return f"{PROVENANCE}/{path.relative_to(AGENCY_ROOT).as_posix()}"

ROOT = Path(__file__).resolve().parents[2]
SEED = MEMORY / "organization_seed.json"
# Every packaged release, oldest first within an activity (the key sorts that way). A line's revisions
# chain within its activity; the NAVSEA, ONR and NRL sheets each stand alone until a second release lands.
# An agency without a forecast has no releases, and the forecast layer is not emitted.
RELEASES = [p.name for p in forecast_packs(ROOT / "datapack") if (p / "layers").is_dir()]


def activity_of(release: str) -> str:
    """The activity a release key names, the word before its date: lrae_navwar_2025-06 and amc_2026-05."""
    return release.rsplit("_", 1)[0].rsplit("_", 1)[-1]

NS = uuid.UUID("7c3d1f5a-9b24-4f8e-8c61-2a0d5e7b41c3")
SEED_SOURCE = provenance(MEMORY / "organization_seed.json")
LRAE_SOURCE = "chromie-federal-buyer-map-trial/datapack"
PRODUCER = "chromie-federal-buyer-map-trial/agency_layers_sql.py"
PRODUCER_VERSION = "14"

skipped: dict[str, int] = {}


def note_skip(reason: str) -> None:
    skipped[reason] = skipped.get(reason, 0) + 1



def own_manifest() -> list[dict]:
    """The ledger without the rows another profile's collectors marked: a layer reads its own pages and the unmarked
    ones, never the histories or lookups another layer saved (an award two layers both sweep is read from each
    layer's own capture). Without this, a history page one layer took for a shared award would change what the
    other layer emits."""
    return [r for r in manifest_rows() if not note_is_foreign(r.get("note") or "")]


def uid(kind: str, key: str) -> str:
    return str(uuid.uuid5(NS, f"{kind}:{key}"))


def sha(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ------------------------------------------------------------------ SQL literals

def lit(value) -> str:
    """Quote for Postgres. standard_conforming_strings is on, so doubling ' is enough."""
    if value is None or value == "":
        return "null"
    return "'" + str(value).replace("'", "''") + "'"


def arr(values) -> str:
    values = [v for v in values if v]
    if not values:
        return "'{}'::text[]"
    return "array[" + ",".join(lit(v) for v in values) + "]::text[]"


def jsonb(value) -> str:
    return lit(json.dumps(value, sort_keys=True, separators=(",", ":"))) + "::jsonb"


def norm(text: str) -> str:
    return " ".join(str(text).split()).strip().lower()


def insert(table: str, columns: list[str], rows: list[list[str]], out: list[str]) -> None:
    if not rows:
        out.append(f"-- {table}: nothing to insert")
        out.append("")
        return
    out.append(f"insert into {table} ({', '.join(columns)}) values")
    out.append(",\n".join("  (" + ", ".join(r) + ")" for r in rows))
    out.append("on conflict do nothing;")
    out.append("")


# ------------------------------------------------------------- source registry

REGISTRY = SOURCES / "source_registry.json"
REGISTRY_SOURCE = provenance(SOURCES / "source_registry.json")
# The registry's words for how a source is reached and whether it answered, in the table's.
ACCESS_MODE = {"api": "public_api", "webpage": "public_web", "spreadsheet": "download", "pdf": "download",
               "export": "download", "manual": "manual"}
VERIFICATION = {"verified": "verified", "blocked": "blocked", "not_inspected": "unverified",
                "restricted": "blocked", "stale": "degraded"}
# The ingestion primitive that reads the source: a connector where the URL and
# shape are known, a browser where the host refuses plain fetches, an agent where a person had to look.
ADAPTER = {"api": "api_connector", "manual": "research_agent"}
NOTICE_PROVIDER, FPDS_PROVIDER = "sam_gov_site_api", "fpds_atom_feed"
USASPENDING_PROVIDER = "usaspending_api"
LRAE_PROVIDERS = P["forecast"]["providers"]
FORECAST_LABEL, FORECAST_SHORT = P["forecast"]["label"], P["forecast"]["short"]


def source_row(entry: dict) -> list[str]:
    example = entry.get("inspected_example") or {}
    restrictions = entry.get("access_restrictions") or ""
    adapter = ADAPTER.get(entry["access_mode"], "page_fetcher")
    if example.get("method") in ("browserbase", "context_dev") or "Browserbase" in restrictions:
        adapter = "browser_extractor"
    restricted = entry["verification_status"] == "restricted"
    return [
        lit(uid("source", entry["source_key"])), lit(entry["source_key"]),
        lit(entry["responsible_org"]), lit(entry["provider_name"]),
        lit("US"), arr(["US"]), lit("federal"), lit(entry["official_url"]),
        lit(adapter), lit("authenticated" if restricted else ACCESS_MODE[entry["access_mode"]]),
        jsonb({k: entry[k] for k in ("answers", "cannot_answer", "lifecycle_stages", "fields_and_identifiers")}),
        lit(entry["proposed_monitor_frequency"]), lit(entry["last_verified_at"]),
        lit(VERIFICATION[entry["verification_status"]]),
        lit(f"{entry['historical_coverage']}. Published {entry['publication_frequency']}; "
            f"reporting lag {entry['reporting_lag']}."),
        jsonb([] if restrictions.lower().startswith("none") else [restrictions]),
        jsonb({"registry": REGISTRY_SOURCE, "access_mode": entry["access_mode"],
               "verification_status": entry["verification_status"],
               "extraction_difficulty": entry["extraction_difficulty"], "inspected_example": example,
               # the source as an instrument: Live/Historical/Adjacent, standing or episodic, what the collector is, its cadence
               "instrument": entry.get("instrument") or {}}),
    ]


def emit_sources(out: list[str]) -> dict[str, str]:
    """Every registry row as a procurement source. Returns host -> source_key for the sites, so an item
    read off a page can name its source: the first `webpage` row whose official page or inspected
    example sits on the host. A file or an API on a shared host (sam.gov, fpds.gov) names no host;
    a notice page is not the LRAE special notice because both live on sam.gov."""
    entries = json.loads(REGISTRY.read_text(encoding="utf-8"))
    keys = {e["source_key"] for e in entries}
    assert set(LRAE_PROVIDERS.values()) | {NOTICE_PROVIDER, FPDS_PROVIDER} | set(OVERSIGHT_PROVIDERS.values()) | set(REMARKS_PROVIDERS.values()) \
        | ({HIRING_PROVIDER} if HIRING_RECORDS.exists() else set()) | ({VENDOR_HIRING_PROVIDER} if VENDOR_HIRING_RECORDS.exists() else set()) <= keys, \
        "the providers the loader names must be registry rows"
    insert("public.gov_procurement_sources",
           ["id", "source_key", "provider_name", "portal_name", "jurisdiction_code", "jurisdiction_path",
            "government_level", "official_url", "adapter_key", "access_mode", "capabilities", "refresh_cadence",
            "last_verified_at", "verification_status", "coverage_notes", "known_access_gaps", "metadata"],
           [source_row(e) for e in entries], out)
    hosts: dict[str, str] = {}
    for e in entries:
        if e["access_mode"] != "webpage":
            continue
        for url in (e["official_url"], (e.get("inspected_example") or {}).get("url") or ""):
            if urlparse(url).netloc:
                hosts.setdefault(urlparse(url).netloc, e["source_key"])
    return hosts


# ---------------------------------------------------------------------- events

# The canonical event types plus the two SAM.gov steps between an RFI and an RFP that the
# back-test reads: a presolicitation and a J&A.
EVENT_TYPES = frozenset("""
funding_change program_created program_cancelled program_delayed leadership_change reorganization
strategy_change capability_priority industry_engagement forecast_created forecast_changed rfi_released
rfp_released contract_awarded contract_modified contract_extended contract_expires sbir_topic
sbir_selection prototype_transition audit_finding protest congressional_directive conference_appearance
presolicitation_posted justification_posted budget_line vacancy_posted vendor_vacancy_posted
""".split())
ITEM_COLUMNS = ["id", "agency_id", "section", "kind", "claim_key", "title", "body", "source", "as_of",
                "event_type", "published_at", "source_tier", "source_provider", "primary_organization_id", "data"]


def event_columns(event: str | None, published: str | None, tier: str | None, provider: str | None,
                  org_id: str | None = None, data: dict | None = None) -> list[str]:
    return [lit(event), lit(published), lit(tier), lit(provider), lit(org_id), jsonb(data or {})]


# -------------------------------------------------------------------- agencies

SUBTIER = P["agency"]  # the agency this layer is built for, as the profile states it
AGENCY_DOD = uid("agency", SUBTIER["toptier_code"])
AGENCY_NAVY = uid("agency", f"{SUBTIER['toptier_code']}:{SUBTIER['subtier_code']}")  # the subtier agency's id (the name predates the second agency)
AGENCY_NAME = SUBTIER["subtier_name"]
DEPARTMENT_NODE = SUBTIER["node"]


def emit_agencies(out: list[str]) -> None:
    insert("public.agencies",
           ["id", "parent_agency_id", "level", "canonical_name", "normalized_name",
            "abbreviation", "toptier_code", "subtier_code"],
           [[lit(AGENCY_DOD), "null", lit("toptier"), lit(SUBTIER["toptier_name"]),
             lit(norm(SUBTIER["toptier_name"])), lit(SUBTIER["toptier_abbreviation"]), lit(SUBTIER["toptier_code"]), "null"],
            [lit(AGENCY_NAVY), lit(AGENCY_DOD), lit("subtier"), lit(AGENCY_NAME),
             lit(norm(AGENCY_NAME)), lit(SUBTIER["subtier_abbreviation"]), lit(SUBTIER["toptier_code"]), lit(SUBTIER["subtier_code"])]],
           out)


# --------------------------------------------------------------------- offices

# `direct_reporting_program_manager` is not in the production vocabulary. A DRPM is
# a program office; what makes it direct-reporting is who it reports to, which is a
# relationship, not a type.
ORG_TYPE = {
    "agency": "agency",
    "command": "contracting_activity",
    "acquisition_portfolio": "acquisition_portfolio",
    "program_executive_office": "program_executive_office",
    "program_office": "program_office",
    "technical_center": "technical_office",
    "contracting_office": "contracting_office",
    "direct_reporting_program_manager": "program_office",
    "department": "department",
    "field_activity": "field_activity",
}

ROLE_TYPE = [
    ("deputy program manager", "deputy_program_manager"),
    ("program executive officer", "acquisition_leader"),
    ("executive director", "acquisition_leader"),
    ("commander", "acquisition_leader"),
    ("director of contracts", "contracting_leader"),
    ("contracting officer", "contracting_officer"),
    ("contract specialist", "contract_specialist"),
    ("program manager", "program_manager"),
    ("technical director", "technical_lead"),
]


# The position vocabulary gov_contact_positions checks; a row outside it fails the load.
POSITION_ROLES = frozenset({"acquisition_leader", "program_manager", "deputy_program_manager", "program_staff", "technical_lead",
                            "contracting_leader", "contracting_officer", "contract_specialist", "cor", "contract_administrator", "other"})
# gov_contacts.role is the coarser vocabulary the schema checks; positions keep the finer one.
CONTACT_ROLE = {"contracting_officer": "contracting_officer", "contracting_leader": "contracting_officer",
                "contract_specialist": "contract_specialist", "contract_administrator": "contract_specialist",
                "acquisition_leader": "program", "program_manager": "program", "deputy_program_manager": "program",
                "program_staff": "program", "technical_lead": "program", "cor": "program"}


def role_type(role_as_written: str | None) -> str:
    text = (role_as_written or "").lower()
    for needle, mapped in ROLE_TYPE:
        if needle in text:
            return mapped
    return "other"


# The production resolver treats a row with no confidence as having no provenance
# and will not consider it at all, so a number is part of the interface rather than
# something the sources supply. The mapping is deliberate and narrow: a claim a source
# states outright scores 1.0, one we derived scores 0.5, and 0.5 sits below the
# resolver's MIN_HIERARCHY_CONFIDENCE so a derived edge cannot drive an ancestry walk.
DOCUMENTED, DERIVED = "1.0", "0.5"


def chain_or_branch(previous: tuple[str, str] | None, office_id: str) -> str | None:
    """The assertion a new office observation supersedes, or None to start its own chain.

    Supersession may not change the subject. A later release naming the same office is a
    re-statement and chains; one naming a different office is a different claim, so it
    branches and both observations stay current for a reviewer to resolve.
    """
    return previous[1] if previous and previous[0] == office_id else None


def loadable_value(low: str, high: str) -> bool:
    """Both bounds or nothing: the column has no shape for an open-ended range."""
    return bool(low) and bool(high)


def confidence_for(evidence_class: str) -> str:
    return DOCUMENTED if evidence_class == "directly_documented" else DERIVED


def former_names(node: dict) -> list[str]:
    """A former name is an alias. name_history is otherwise dropped on the floor."""
    out = []
    for entry in node.get("name_history") or []:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if name:
            out.append(str(name))
    return out


def observation_urls(seed: dict) -> dict[str, str]:
    return {o["id"]: o.get("source_url") or "" for o in seed.get("observations") or []}


def observation_dates(seed: dict) -> dict[str, str]:
    return {o["id"]: o.get("observed_at") or "" for o in seed.get("observations") or []}


def latest_observed(observation_ids, dates: dict[str, str]) -> str | None:
    """When the record was last seen stated: the newest cited observation's date. The program-office
    resolver reads a row without observed_at as having no provenance and skips it, so every row
    that cites a dated observation carries one."""
    found = [dates[o] for o in observation_ids or [] if dates.get(o)]
    return max(found) if found else None


def first_url(observation_ids, urls: dict[str, str]) -> str | None:
    """The source behind a node or an edge: the first cited observation that has a fetchable URL."""
    for obs_id in observation_ids or []:
        url = urls.get(obs_id, "")
        if url.startswith("http"):
            return url
    return None


def newest_url(observation_ids, urls: dict[str, str], dates: dict[str, str]) -> str | None:
    """The source a row's observed_at is read from: the newest cited observation with a fetchable URL, so the
    page a reader opens states the claim on the date the row gives."""
    found = [(dates.get(o) or "", urls[o]) for o in observation_ids or [] if urls.get(o, "").startswith("http")]
    return max(found)[1] if found else None


def documented_dates(rel: dict) -> tuple[str | None, str | None]:
    """A claim's start and end as a date column may carry them. A date column reads as a day a source states,
    so a day the memory inferred or set by convention (acting from May 2023 held as 2023-05-01) stays empty."""
    if rel.get("effective_dates_status") != "documented":
        return None, None
    return rel.get("effective_from"), rel.get("effective_to")


def release_source_url(release: str) -> str | None:
    path = ROOT / "datapack" / release / "SOURCE.json"
    return json.loads(path.read_text(encoding="utf-8")).get("source_url") if path.exists() else None


def live_parent_claims(seed: dict, org_ids: dict[str, str]) -> dict[str, set[str]]:
    """The live `child_of` claims per office. A claimed parent that is itself an ancestor of another
    claimed parent (the LRAE says `- NAVSEA`, the deputy program manager list says PEO CARRIERS, and
    PEO CARRIERS sits under NAVSEA) is the same chain stated at two depths, not a competing claim,
    so it is dropped in favour of the nearer parent."""
    claims: dict[str, set[str]] = {}
    for rel in seed["relationships"]:
        if rel["type"] != "child_of" or rel["review_status"] == "retracted":
            continue
        if rel["current_status"]["state"] != "last_confirmed":
            continue
        if rel["from"] in org_ids and rel["to"] in org_ids:
            claims.setdefault(rel["from"], set()).add(rel["to"])

    def ancestors(node: str, seen: frozenset = frozenset()) -> set[str]:
        out = set()
        for parent in claims.get(node, ()):
            if parent not in seen:
                out.add(parent)
                out |= ancestors(parent, seen | {node})
        return out

    for child, parents in claims.items():
        if len(parents) > 1:
            above = set().union(*(ancestors(p) for p in parents))
            nearer = parents - above
            if nearer:
                claims[child] = nearer
    return claims


def emit_offices(seed: dict, out: list[str]) -> dict[str, str]:
    org_ids: dict[str, str] = {}
    rows = []
    urls, dates = observation_urls(seed), observation_dates(seed)
    for node in seed["nodes"]:
        if node["type"] == "person":
            continue
        org_id = uid("org", node["id"])
        org_ids[node["id"]] = org_id
        aliases = [a["text"] for a in node.get("aliases", [])] + former_names(node)
        seen, unique = set(), []
        for alias in aliases:
            if norm(alias) not in seen:
                seen.add(norm(alias))
                unique.append(alias)
        codes = node.get("codes") or {}
        rows.append([
            lit(org_id), lit(AGENCY_NAVY),
            lit(AGENCY_NAVY) if node["type"] == "agency" else "null",
            "null",  # parent set below, once every office exists
            lit(node["name"]), lit(norm(node["name"])),
            lit(codes.get("office_code") or codes.get("uic")),
            lit(ORG_TYPE[node["type"]]),
            arr(unique), arr(norm(a) for a in unique),
            jsonb(codes),
            lit(node.get("valid_from")), lit(node.get("valid_to")),
            lit("US"), arr(["US"]), lit("federal"),
            DOCUMENTED if node.get("observation_ids") else DERIVED,
            lit(SEED_SOURCE), lit(node["id"]), lit(first_url(node.get("observation_ids"), urls)),
            lit(latest_observed(node.get("observation_ids"), dates)),
        ])
        if latest_observed(node.get("observation_ids"), dates) is None:
            note_skip("office cites no dated observation; observed_at empty, the resolver reads it as unprovenanced")
        for unmapped in ("location", "notes", "capability_portfolios"):
            if node.get(unmapped):
                note_skip(f"gov_organizations has no column for node.{unmapped}")

    insert("public.gov_organizations",
           ["id", "agency_id", "existing_agency_id", "parent_organization_id", "name",
            "normalized_name", "acronym", "org_type", "aliases", "normalized_aliases",
            "external_ids", "valid_from", "valid_to", "jurisdiction_code",
            "jurisdiction_path", "government_level", "confidence", "source", "source_ref", "source_url", "observed_at"],
           rows, out)

    # parent_organization_id holds one edge. Where the sources give exactly one live
    # `child_of` claim that is the parent; where they give two, the column stays empty
    # and the claims go to gov_organization_relationships instead of one being picked.
    for child, parents in sorted(live_parent_claims(seed, org_ids).items()):
        if len(parents) == 1:
            out.append(f"update public.gov_organizations set parent_organization_id = {lit(org_ids[next(iter(parents))])} "
                       f"where id = {lit(org_ids[child])} and parent_organization_id is null;")
        else:
            note_skip("office with competing live parents left without parent_organization_id")
    out.append("")
    return org_ids


def leadership_events(seed: dict, org_ids: dict[str, str], hosts: dict[str, str]) -> list[list[str]]:
    """One derived item per office and day on which someone began or stopped leading it. An arrival
    and a departure the seed dates to the same day are one change, so a succession is one event with
    both names. A claim with no documented date states no day to put an event on and is counted instead."""
    names = {n["id"]: n["name"] for n in seed["nodes"]}
    urls = observation_urls(seed)
    changes: dict[tuple[str, str], dict] = {}
    for rel in seed["relationships"]:
        if rel["type"] != "leads" or rel["review_status"] == "retracted" or rel["to"] not in org_ids:
            continue
        start, end = documented_dates(rel)
        dated = [(day, side) for day, side in ((start, "arrived"), (end, "departed")) if day]
        if not dated:
            note_skip("leadership claim with no documented date states no day for an event")
            continue
        for day, side in dated:
            change = changes.setdefault((rel["to"], day), {"arrived": [], "departed": [], "observations": [], "dates": []})
            change[side].append({"person": rel["from"], "name": names.get(rel["from"], rel["from"]), "role": rel.get("role_as_written")})
            change["observations"] += [o for o in rel.get("observation_ids") or [] if o not in change["observations"]]
            status = rel.get("effective_dates_status") or "unknown"
            if status not in change["dates"]:
                change["dates"].append(status)
    rows = []
    for (office, day), change in sorted(changes.items()):
        claim_key = f"{MEMORY_NS}:leadership:{office}:{day}"
        url = first_url(change["observations"], urls)
        said = "; ".join(f"{p['name']} {verb} as {p['role'] or 'leader'}"
                         for verb, side in (("ended", "departed"), ("began", "arrived")) for p in change[side])
        rows.append([lit(uid("brainitem", claim_key)), lit(AGENCY_NAVY), lit("people"), lit("narrative"), lit(claim_key),
                     lit(f"Leadership change at {names.get(office, office)} on {day}"), lit(said),
                     jsonb({"url": url, "observations": change["observations"], "seed": SEED_SOURCE}), lit(day),
                     *event_columns("leadership_change", day, "derived", hosts.get(urlparse(url or "").netloc), org_ids[office],
                                    {"office": office, "arrived": change["arrived"], "departed": change["departed"],
                                     "dates_status": sorted(change["dates"]),
                                     "evidence_ids": [uid("evidence", f"{MEMORY_NS}:{o}") for o in change["observations"]]})])
    return rows


def person_last_seen(seed: dict) -> dict[str, tuple[str, str | None]]:
    """Memory person -> (day, url) of the newest dated source naming them: the observations their leadership
    claims cite and the documents the people file ties to them."""
    from people import PEOPLE  # noqa: E402
    urls, dates = observation_urls(seed), observation_dates(seed)
    found: dict[str, list] = {}
    for rel in seed["relationships"]:
        if rel["type"] == "leads" and rel["review_status"] != "retracted":
            for o in rel.get("observation_ids") or []:
                if dates.get(o):
                    found.setdefault(rel["from"], []).append((dates[o], urls[o] if urls.get(o, "").startswith("http") else None))
    if PEOPLE.exists():
        for person in json.loads(PEOPLE.read_text(encoding="utf-8"))["rows"]:
            if person["seed_id"] and person["last_seen"]:
                found.setdefault(person["seed_id"], []).append((person["last_seen"], person["positions"][0]["source_url"] or None))
    return {p: max(v, key=lambda d: (d[0], d[1] or "")) for p, v in found.items()}


def emit_people(seed: dict, org_ids: dict[str, str], out: list[str], hosts: dict[str, str]) -> None:
    people = {n["id"]: n for n in seed["nodes"] if n["type"] == "person"}
    # last_seen is required and defaults to the load's own time, so it carries the newest source naming the person.
    named, contacts = person_last_seen(seed), []
    for node_id, node in people.items():
        if node_id not in named:
            note_skip("memory person no dated source names; last_seen would read as the load time")
            continue
        day, url = named[node_id]
        contacts.append([lit(uid("contact", node_id)), lit(f"{MEMORY_NS}:{node_id}"), lit(node["name"]),
                         lit(AGENCY_NAME), lit("program"), lit(SEED_SOURCE), lit(url), lit(day)])
    insert("public.gov_contacts", ["id", "identity_key", "name", "agency", "role", "source", "source_url", "last_seen"], contacts, out)

    urls, dates = observation_urls(seed), observation_dates(seed)
    rows = []
    for rel in seed["relationships"]:
        if rel["type"] != "leads":
            continue
        if rel["from"] not in people or rel["to"] not in org_ids:
            note_skip("leads relationship without a person and an office at its ends")
            continue
        if rel["review_status"] == "retracted":
            note_skip("retracted leadership claim not loaded")
            continue
        # valid_to is the only way to say a position is over. A leadership claim a
        # source ended or superseded without giving a date would land as an open
        # current position beside the successor, so it is left out.
        start, end = documented_dates(rel)
        state = rel["current_status"]["state"]
        if state in ("ended", "superseded") and not end:
            note_skip(f"leadership claim is {state} with no documented end date; no column can say so")
            continue
        # observed_at is required and defaults to the load's own time, so it carries the newest observation stating the claim.
        seen = latest_observed(rel.get("observation_ids"), dates)
        if not seen:
            note_skip("leadership claim with no dated observation; observed_at would read as the load time")
            continue
        rows.append([
            lit(uid("position", rel["id"])), lit(uid("contact", rel["from"])), lit(org_ids[rel["to"]]),
            lit(role_type(rel.get("role_as_written"))), lit(rel.get("role_as_written")),
            lit(start), lit(end), lit(seen),
            lit(SEED_SOURCE), lit(rel["id"]), lit(newest_url(rel.get("observation_ids"), urls, dates)),
        ])
    insert("public.gov_contact_positions",
           ["id", "contact_id", "organization_id", "role_type", "raw_title",
            "valid_from", "valid_to", "observed_at", "source", "source_ref", "source_url"], rows, out)
    insert("public.agency_brain_items", ITEM_COLUMNS, leadership_events(seed, org_ids, hosts), out)


def emit_contacts(org_ids: dict[str, str], out: list[str]) -> None:
    """Every person research/memory/people.json ties to an office: SAM.gov points of contact, forecast
    POC columns, release passages, speakers and witnesses, one contact per identity and one dated position per
    document that names them. A person the organization memory already holds keeps the memory's contact id."""
    from people import PEOPLE, SOURCE as PEOPLE_SOURCE  # noqa: E402
    if not PEOPLE.exists():
        note_skip("people not built; run research/tools/people.py build")
        return
    contacts, positions = [], []
    for person in json.loads(PEOPLE.read_text(encoding="utf-8"))["rows"]:
        contact_id = uid("contact", person["seed_id"]) if person["seed_id"] else uid("contact", person["id"])
        newest = person["positions"][0]
        if not person["seed_id"]:
            contacts.append([lit(contact_id), lit(f"people:{person['key']}"), lit(person["name"]), lit(newest["raw_title"][:200] or None),
                             lit((person["emails"] or [None])[0]), lit(AGENCY_NAME), lit(CONTACT_ROLE.get(newest["role_type"], "other")),
                             lit(PEOPLE_SOURCE), lit(newest["source_url"] or None), lit(person["last_seen"])])
        for pos in person["positions"]:
            if pos["office"] not in org_ids:
                note_skip("person observed beside an office the memory lacks; position not loaded")
                continue
            positions.append([lit(uid("position", f"{person['id']}:{pos['office']}:{pos['source']}:{pos['source_ref']}")), lit(contact_id),
                              lit(org_ids[pos["office"]]), lit(pos["role_type"]), lit(pos["raw_title"] or None), lit(pos["observed_at"]),
                              pos["confidence"], lit(pos["source"]), lit(pos["source_ref"]), lit(pos["source_url"] or None)])
    insert("public.gov_contacts", ["id", "identity_key", "name", "title", "email", "agency", "role", "source", "source_url", "last_seen"], contacts, out)
    insert("public.gov_contact_positions", ["id", "contact_id", "organization_id", "role_type", "raw_title", "observed_at", "confidence",
                                            "source", "source_ref", "source_url"], positions, out)


BUDGET_LINES = EVENTS_DIR / "budget_lines.json"
BUDGET_PROVIDER = P["budget"]["provider"]
BUDGET_HOSTS = {"comptroller.war.gov", "comptroller.defense.gov", "www.secnav.navy.mil"}  # where the justification books are published


def emit_budget(out: list[str], org_ids: dict[str, str], offices: dict[tuple[str, str], str], components: dict[str, str]) -> None:
    """Every P-1 line item (and DHS capital investment) of the saved justification books as a dated money event: the
    amounts the book prints per fiscal year, the justification prose as the evidence text, the office when the prose
    names one, the command of the component a DHS investment sits under, and the Department otherwise. A line whose
    FY2027 total moved is funding_change; a flat line is budget_line."""
    if not BUDGET_LINES.exists():
        note_skip("budget lines not built; run research/tools/budget.py extract")
        return
    payload = json.loads(BUDGET_LINES.read_text(encoding="utf-8"))
    books = {b["path"]: b for b in payload["books"]}
    items, evidence = [], []
    for row in payload["lines"]:
        book = books[row["book"]]
        claim_key = f"budget:pb{book['pb']}:{row['appropriation']}:{row['li']}"
        item_id, ev_id = uid("brainitem", claim_key), uid("evidence", claim_key)
        office = office_of(row["text"], offices)
        # A P-40 book is one budget activity; an R-2 book states the activity per program element.
        activity = row.get("budget_activity") or book.get("budget_activity") or ""
        component = components.get((row.get("component") or "").upper())  # OSEM and MGMT are headquarters: the Department
        extra = {k: row[k] for k in ("component", "level", "it", "maol") if row.get(k)}
        items.append([lit(item_id), lit(AGENCY_NAVY), lit("budget"), lit("narrative"), lit(claim_key), lit(row["event_title"][:200].strip()),
                      lit(f"{activity or row.get('appropriation_name', '')}; {row['pages']} page(s); {row['text'][:6000]}"),
                      jsonb({"url": book["url"], "sha256": book["sha256"], "retrieved_at": book["retrieved_at"], "path": book["path"], "book_date": book["date"]}),
                      lit(row["published"]),
                      *event_columns(row["event_type"], row["published"], "official", BUDGET_PROVIDER,
                                     org_ids.get(office) if office else (org_ids.get(component) or org_ids.get(DEPARTMENT_NODE)),
                                     {"li": row["li"], "title": row["title"], "appropriation": row["appropriation"], "budget_activity": activity,
                                      "amounts": row["amounts"], "moved_to": row.get("moved_to", []), "pb": book["pb"], "office_named": office or "", **extra})])
        evidence.append([lit(ev_id), lit(item_id), lit(row["text"][:300] or row["event_title"]), lit(book["url"]), lit(row["published"]),
                         lit(P["budget"].get("evidence_host") or urlparse(book["url"]).netloc), lit(claim_key)])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence", ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


# The section vocabulary agency_brain_items checks; a topic is a stated need, so it files under mission priorities.
SECTIONS = frozenset({"mission_priorities", "budget", "forecast", "procurement_patterns", "vendors_incumbents", "people", "industry_engagement"})
SBIR_SECTION = "mission_priorities"
assert SBIR_SECTION in SECTIONS
SBIR_TOPICS = EVENTS_DIR / "sbir_topics.json"
SBIR_PROVIDER = "sbir_sttr_topics"


def emit_programs(out: list[str], org_ids: dict[str, str]) -> None:
    """Every saved Navy SBIR/STTR topic as a dated programs event: the day the topic was first shown
    to industry, the topic text as the evidence, the program office the text names and the sponsoring command
    otherwise. The topic is a stated technology need, not a buy, so it is its own family in the back-test."""
    if not SBIR_TOPICS.exists():
        note_skip("SBIR topics not built; run research/tools/sbir.py build")
        return
    items, evidence = [], []
    for t in json.loads(SBIR_TOPICS.read_text(encoding="utf-8"))["rows"]:
        if not t["pre_release"] or not t["code"]:
            note_skip("SBIR topic without a code or a release date not loaded")
            continue
        claim_key = f"sbir:{t['code']}"
        item_id, ev_id = uid("brainitem", claim_key), uid("evidence", claim_key)
        office = next((o for o in t["offices"] if o in org_ids), None)
        items.append([lit(item_id), lit(AGENCY_NAVY), lit(SBIR_SECTION), lit("narrative"), lit(claim_key), lit(f"{t['code']} {t['title']}"[:200].strip()),
                      lit(f"{t['program']} topic {t['code']}, {t['solicitation'] or t['cycle']}, {t['status']}; opens {t['open']}, closes {t['close']}; {t['text'][:6000]}"),
                      jsonb({"url": t["url"], "sha256": t["sha256"], "retrieved_at": t["retrieved_at"], "path": t["path"], "portal": "https://www.dodsbirsttr.mil/topics-app/"}),
                      lit(t["pre_release"]),
                      *event_columns("sbir_topic", t["pre_release"], "official", SBIR_PROVIDER, org_ids.get(office or t["org"]) or org_ids.get(DEPARTMENT_NODE),
                                     {"topic_code": t["code"], "program": t["program"], "command": t["command"], "cycle": t["cycle"], "open": t["open"],
                                      "close": t["close"], "keywords": t["keywords"], "offices_named": t["offices"],
                                      # verbatim from the saved index row (sbir.py, 2026-09-28): the instrument and entry the code and phases state
                                      "solicitation_number": t.get("solicitation_number", ""), "release": t.get("release"), "phases": t.get("phases", []),
                                      "instrument": t.get("instrument", ""), "entry_type": t.get("entry_type", ""), "entry_basis": t.get("entry_basis", ""),
                                      "focus_areas": t.get("focus_areas", []), "itar": t.get("itar"), "cmmc_level": t.get("cmmc_level", ""),
                                      "qa_open": t.get("qa_open", ""), "qa_close": t.get("qa_close", ""), "ceiling": t.get("ceiling")})])
        evidence.append([lit(ev_id), lit(item_id), lit((t["text"][:300] or t["title"])), lit(t["url"]), lit(t["pre_release"]), lit("dodsbirsttr.mil"), lit(claim_key)])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence", ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


STAKEHOLDERS = MEMORY / "stakeholders.json"
PROGRAM_MONEY = MEMORY / "programs.json"
LINKEDIN_PROVIDER = "linkedin_profiles_exa"


def stakeholder_provider(said: list[dict], hosts: dict[str, str], keys: set[str]) -> str | None:
    """The registered source a bucket's evidence was read from: the LinkedIn profiles when the person stated it
    there, SAM.gov for a notice contact, the site a page sits on, the budget books; none when no one source is it."""
    if any(r["basis"] == "self-stated" for r in said):
        return LINKEDIN_PROVIDER if LINKEDIN_PROVIDER in keys else None
    netloc = urlparse(said[0]["source_url"]).netloc
    found = NOTICE_PROVIDER if netloc == "sam.gov" else hosts.get(netloc) or (BUDGET_PROVIDER if netloc in BUDGET_HOSTS else None)
    return found if found in keys else None


def emit_stakeholders(out: list[str], org_ids: dict[str, str], hosts: dict[str, str]) -> None:
    """Every person stakeholders.py placed in a bucket, one people item per person and bucket, filed at the person's
    office: the bucket's evidence rows are its evidence, each with the source it was read from. A shared mailbox is
    a contact route, not a person, and is left to the contacts."""
    if not STAKEHOLDERS.exists():
        note_skip("stakeholders not built; run research/tools/stakeholders.py build")
        return
    built = json.loads(STAKEHOLDERS.read_text(encoding="utf-8"))
    keys = {e["source_key"] for e in json.loads(REGISTRY.read_text(encoding="utf-8"))}
    items, evidence = [], []
    for person in built["people"]:
        if person["mailbox"]:
            continue
        office = next((o for o in person["offices"] if o in org_ids), DEPARTMENT_NODE)
        for bucket, said in person["buckets"].items():
            claim_key = f"stakeholder:{person['id']}:{bucket}"
            item_id = uid("brainitem", claim_key)
            items.append([lit(item_id), lit(AGENCY_NAVY), lit("people"), lit("narrative"), lit(claim_key),
                          lit(f"{person['name'].strip()}: {bucket.replace('_', ' ')}"[:200].strip()), lit("; ".join(r["text"] for r in said)[:6000]),
                          jsonb({"built": str(STAKEHOLDERS.relative_to(AGENCY_ROOT)), "as_of": built["as_of"]}), lit(built["as_of"]),
                          *event_columns(None, None, "derived", stakeholder_provider(said, hosts, keys), org_ids.get(office),
                                         {"person": person["id"], "name": person["name"], "bucket": bucket, "basis": sorted({r["basis"] for r in said}),
                                          "programs": person["programs"], "linkedin": (person["linkedin"] or {}).get("url", ""),
                                          "posts": person.get("posts")})])
            for i, r in enumerate(said):
                if r["source_url"].startswith(("http://", "https://")):
                    evidence.append([lit(uid("evidence", f"{claim_key}:{i}")), lit(item_id), lit(r["text"][:300]), lit(r["source_url"]),
                                     lit(r["observed_at"] or None), lit(urlparse(r["source_url"]).netloc), lit(claim_key)])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence", ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


def emit_program_money(out: list[str], org_ids: dict[str, str]) -> None:
    """Every current program programs.py matched to money, one budget item filed at its office: the book's amounts,
    the need it states and its reason for the change, the contract money found against it in the fiscal year and the
    upper bound left, with the book and the listing as the evidence."""
    if not PROGRAM_MONEY.exists():
        note_skip("program money not built; run research/tools/programs.py build")
        return
    built = json.loads(PROGRAM_MONEY.read_text(encoding="utf-8"))
    fy, current = built["fiscal_year"], P["people"].get("program_current") or ""
    books = {r["path"]: r.get("final_url") or r.get("url") or "" for r in manifest_rows() if r.get("path")}
    items, evidence = [], []
    for prog in built["programs"]:
        if prog["status"] != current or prog["budget"]["match"] == "none":
            continue
        a = prog["budget"]["amounts_musd"]
        claim_key = f"program-money:{AGENCY_KEY}:{prog['id']}"
        item_id = uid("brainitem", claim_key)
        spend = prog.get("spend") or {}
        items.append([lit(item_id), lit(AGENCY_NAVY), lit("budget"), lit("narrative"), lit(claim_key),
                      lit(f"{prog['title']}: FY{fy} ${a.get(f'fy{fy}')}M, FY{fy + 1} ${a.get(f'fy{fy + 1}')}M"[:200].strip()),
                      # A line the book prints with amounts and no prose (most are lines the request zeroes) keeps its money.
                      lit(" ".join(x for x in (prog["problem"], prog.get("change_statement") or "") if x)[:6000]
                          or "The budget book prints this line's amounts without a description."),
                      jsonb({"listing": prog["listing_source"], "books": sorted({b["book"] for b in prog["budget"]["blocks"]})}), lit(built["listing_retrieved_at"][:10]),
                      *event_columns(None, None, "derived", BUDGET_PROVIDER, org_ids.get(prog["office"]) or org_ids.get(DEPARTMENT_NODE),
                                     {"program": prog["id"], "title": prog["title"], "manager": prog["manager"], "amounts_musd": a, "match": prog["budget"]["match"],
                                      "elements": [f"{b['pe']} {b['project']}".strip() for b in prog["budget"]["blocks"]],
                                      "spend_obligated_usd": spend.get("obligated"), "spend_awards": spend.get("awards", []),
                                      "left": prog.get("left"), "url": prog.get("url") or ""})])
        book = prog["budget"]["blocks"][0]["book"]
        if prog["problem"] and books.get(book):
            evidence.append([lit(uid("evidence", claim_key)), lit(item_id), lit(prog["problem"][:300]), lit(books[book]), lit(None),
                             lit(P["budget"].get("evidence_host") or urlparse(books[book]).netloc), lit(claim_key)])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence", ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


# --------------------------------------------------------------- office edges

# The production vocabulary has no `child_of`: parentage is the hierarchy column.
# What is left are the edges that column cannot carry.
REL_TYPE = {
    "contracts_for": "contracting_supports",
    "part_of": "functionally_aligned_to",
    "child_of": "functionally_aligned_to",
    "consolidated_into": "successor_to",
}
REVERSED = {"consolidated_into"}  # A consolidated into B means B is the successor


def emit_relationships(seed: dict, org_ids: dict[str, str], out: list[str]) -> None:
    urls, dates = observation_urls(seed), observation_dates(seed)
    rows = []
    for rel in seed["relationships"]:
        if rel["type"] == "leads":
            continue
        if rel["review_status"] == "retracted":
            note_skip("retracted claim not loaded (the production table has no retraction)")
            continue
        if rel["type"] not in REL_TYPE:
            note_skip(f"no production relationship_type for `{rel['type']}`")
            continue
        if rel["from"] not in org_ids or rel["to"] not in org_ids:
            note_skip("relationship endpoint missing from the office table")
            continue
        state = rel["current_status"]["state"]
        # Every live `child_of` claim is a row, the one in the parent column included: the resolver
        # treats a dated relationship row as authoritative and ignores the parent column for any
        # office that has rows, so an office whose nearest parent lived only in the column
        # (PMS 312 under PEO CARRIERS, with its farther NAVSEA claim as a row) had no path at all.
        # Ended claims stay too: NEN under PEO EIS until 2020-05-13 is the office's history.
        # valid_to is the only way to say a claim is over. A claim a source ended or
        # superseded without stating the day would read as current, so it is left out
        # rather than published as live.
        start, end = documented_dates(rel)
        if state in ("ended", "superseded") and not end:
            note_skip(f"claim is {state} with no documented end date; no column can say so")
            continue
        source_org, target_org = rel["from"], rel["to"]
        if rel["type"] in REVERSED:
            source_org, target_org = target_org, source_org
        rows.append([
            lit(uid("orgrel", rel["id"])), lit(org_ids[source_org]), lit(org_ids[target_org]),
            lit(REL_TYPE[rel["type"]]), lit(start), lit(end),
            confidence_for(rel["evidence_class"]), lit(SEED_SOURCE), lit(rel["id"]),
            lit(first_url(rel.get("observation_ids"), urls)), lit(latest_observed(rel.get("observation_ids"), dates)),
        ])
    insert("public.gov_organization_relationships",
           ["id", "source_organization_id", "target_organization_id", "relationship_type",
            "valid_from", "valid_to", "confidence", "source", "source_ref", "source_url", "observed_at"], rows, out)


# -------------------------------------------------------------------- evidence

# Evidence must point at a Brain item, a Brain document or a procurement reference;
# a bare URL is not accepted. A source statement is a Brain item, which is what one
# is, so the observations land there and the evidence row wraps them.
SECTION = {
    "leadership": "people",
    "contracting_support": "procurement_patterns",
    "parentage": "mission_priorities",
    "consolidation": "mission_priorities",
    "listing": "mission_priorities",
    "naming": "mission_priorities",
    "existence": "mission_priorities",
}


def emit_seed_evidence(seed: dict, out: list[str], hosts: dict[str, str]) -> dict[str, str]:
    """Returns observation id -> evidence uuid."""
    by_obs, items, evidence = {}, [], []
    for obs in seed["observations"]:
        claim_key = f"{MEMORY_NS}:{obs['id']}"
        item_id = uid("brainitem", claim_key)
        ev_id = uid("evidence", claim_key)
        by_obs[obs["id"]] = ev_id
        passage = obs.get("passage") or obs["statement_type"]
        items.append([
            lit(item_id), lit(AGENCY_NAVY), lit(SECTION.get(obs["statement_type"], "mission_priorities")),
            lit("narrative"), lit(claim_key),
            lit(f"{obs['statement_type']} observed {obs['observed_at']}"), lit(passage),
            jsonb({"url": obs.get("source_url"), "revision": obs.get("source_revision"),
                   "statement_type": obs["statement_type"], "seed_id": obs["id"],
                   "access": obs.get("access")}),
            lit(obs.get("observed_at")),
            *event_columns(None, None, None, hosts.get(urlparse(obs.get("source_url") or "").netloc)),
        ])
        url = obs.get("source_url") or ""
        evidence.append([
            lit(ev_id), lit(item_id), lit(passage),
            lit(url) if url.startswith("http") else "null",
            lit(obs.get("observed_at")), lit(MEMORY_NS), lit(claim_key),
        ])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence",
           ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"],
           evidence, out)
    return by_obs


# ------------------------------------------------------------------ LRAE layers

def read_layer(release: str, name: str) -> list[dict]:
    path = ROOT / "datapack" / release / "layers" / f"{name}.csv"
    if not path.exists():  # the packager writes no file for an empty table
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


# What a forecast row states past its need, requirement and value layers, carried on its event: the incumbent, the
# vehicle, the dates as written and the contacts by name (a contact's e-mail and phone columns are never read).
STATED = ("requirement_description", "contract_vehicle", "contract_type", "procurement_method", "follow_on_or_new",
          "existing_contract_number", "incumbent_contractor", "anticipated_total_value", "published", "solicitation_date",
          "award_date", "award_quarter_as_written", "period_of_performance_months", "place_of_performance", "naics", "psc",
          "contracting_center", "requirement_contact", "alternate_contact", "contracting_poc_name", "secondary_poc_name")


def stated_rows(release: str) -> dict[str, tuple[dict, str]]:
    """Evidence id -> (the STATED fields the row fills, the row's own public page or "")."""
    path = ROOT / "datapack" / release / "rows_raw.csv"
    if not path.exists():
        return {}
    with path.open(newline="") as handle:
        return {f"ev:{release}:row{r['row_number']}": ({k: r[k] for k in STATED if r.get(k)}, r.get("url") or "")
                for r in csv.DictReader(handle)}


# Federal fiscal quarters: FY26 Q1 starts October 2025.
QUARTER_START = {"Q1": (-1, 10), "Q2": (0, 1), "Q3": (0, 4), "Q4": (0, 7)}
QUARTER_END = {"Q1": (-1, 12, 31), "Q2": (0, 3, 31), "Q3": (0, 6, 30), "Q4": (0, 9, 30)}
MEASURE = {"procurement_estimate": "procurement_estimate", "budget_request": "budget_request",
           "enacted_funding": "enacted_funding", "obligation": "obligation"}
NEED_ROLE = {"requirement_owner": "originating_requirement_owner", "contracting": "contracting_office"}


def fiscal_year(text: str) -> int | None:
    text = (text or "").strip().upper()
    if text.startswith("FY") and text[2:].isdigit():
        year = int(text[2:])
        return 2000 + year if year < 100 else year
    if len(text) == 4 and text.isdigit():  # the NAVSEA and ONR sheets write the year bare
        return int(text)
    return None


def quarter_bounds(fy: int | None, quarter: str) -> tuple[str | None, str | None]:
    quarter = (quarter or "").strip().upper()
    if fy is None or quarter not in QUARTER_START:
        return None, None
    offset, month = QUARTER_START[quarter]
    end_offset, end_month, end_day = QUARTER_END[quarter]
    return f"{fy + offset:04d}-{month:02d}-01", f"{fy + end_offset:04d}-{end_month:02d}-{end_day:02d}"


def funding_lineage(key: str, fy_text: str, quarter: str) -> str:
    """The chain key of a funding estimate, read from the parsed period: the database refuses a supersession across
    lineages, and releases spell one fiscal year "FY28" and "2028"."""
    fy = fiscal_year(fy_text)
    period = (quarter or "").strip().upper() if quarter_bounds(fy, quarter)[0] else "unstated"
    return f"funding:{key}:{fy or 'unstated'}:{period}"


# What a release states about a line; a later release that moves one of these moved the forecast.
FORECAST_FIELDS = ("procurement_method", "contract_type", "instrument", "solicitation_fy",
                   "solicitation_quarter", "award_fy", "award_quarter", "as_stated")


def award_window(row: dict) -> str | None:
    return quarter_bounds(fiscal_year(row.get("award_fy", "")), row.get("award_quarter", ""))[0]


def forecast_change(prev: dict, cur: dict) -> dict[str, list]:
    """What a release moved on a line since the release before it: field -> [was, now]; empty is a
    restatement. `award_window` is the anticipated award quarter as a date, the reading Alert C
    (monitor_forecast_revision) makes of the loaded revisions, so the two agree by construction."""
    changed = {f: [prev.get(f, ""), cur.get(f, "")] for f in FORECAST_FIELDS if prev.get(f, "") != cur.get(f, "")}
    if award_window(prev) != award_window(cur):
        changed["award_window"] = [award_window(prev), award_window(cur)]
    return changed


def lrae_events(folded: dict) -> dict[str, tuple[str | None, dict, str]]:
    """Evidence id -> (event, data, office) for every spreadsheet row, walked in the order the
    revision chain is written: a line's first release is `forecast_created`, a later release that
    moved a field is `forecast_changed` carrying the moves, a release that restated it is no event."""
    events, last = {}, {}
    for release in RELEASES:
        needs = {n["id"]: n for n in read_layer(release, "needs")}
        values = {f["need_id"]: f for f in read_layer(release, "funding_observations")}
        for req in read_layer(release, "need_requirements"):
            need = needs[req["need_id"]]
            tie = folded.get((release, need["record_key"]))
            key = tie["key"] if tie else need["record_key"]
            row = {**req, "as_stated": values.get(req["need_id"], {}).get("as_stated", "")}
            prev = last.get(key)
            changed = forecast_change(prev, row) if prev else {}
            event = "forecast_created" if prev is None else "forecast_changed" if changed else None
            events[req["evidence_id"]] = (event, {"line": key, **({"changed": changed} if changed else {})}, need["office_id"])
            last[key] = row
    return events


def latest_end(entries: list[dict]) -> dict | None:
    """The action stating the completion date FPDS last stated for a contract: the latest date, and of
    the actions stating it, the last signed."""
    dated = [e for e in entries if e.get("completion")]
    return max(dated, key=lambda e: (e["completion"], e["signed"])) if dated else None


def incumbent_end(manifest: list[dict], piid: str, usaspending) -> dict | None:
    """When an incumbent contract ends, and what says so: the completion date FPDS last stated, when the saved history
    reaches its last page (the feed runs oldest first, ten actions a page, so an earlier page does not hold the last
    word); else the period of performance on the award's saved USAspending page; else nothing, and the reason counted."""
    pages, actions, complete = fpds_history(manifest, piid)
    entries = [e for e in actions if e["piid"] == piid]
    end = latest_end(entries) if pages and complete else None
    if end:
        latest = max(entries, key=lambda e: e["signed"])
        return {"source": "fpds", "expires_on": end["completion"], "signed": end["signed"], "vendor": latest["vendor"],
                "contracting_office": latest["contracting_office"], "idv": latest["idv"], "actions": len(entries), "pages": len(pages),
                "capture": next(p for p in pages if p["sha256"] == end["page"])}  # the page carrying the deciding action
    award = usaspending(manifest, piid)
    if award and award.get("pop_end"):
        return {"source": "usaspending", "expires_on": award["pop_end"][:10], "signed": (award["last_modified"] or award["signed"] or "")[:10],
                "vendor": award["recipient"] or "", "contracting_office": award["awarding_office"] or "", "idv": award["parent"] or "",
                "capture": {"url": award["url"], "sha256": award["sha256"], "retrieved_at": award["retrieved"]}}
    note_skip("incumbent contract's FPDS response is not on disk" if not pages
              else "incumbent contract's saved FPDS history stops before its last page; end date not stated" if not complete
              else "FPDS states no completion date for the incumbent contract")
    return None


def contract_expiries(org_ids: dict[str, str], canon) -> list[list[str]]:
    """One derived item per incumbent contract a forecast line of any release names: the contract ends on the date
    `incumbent_end` reads, which is what the follow-on is timed against. The newest release that cites the contract
    names its office; every line citing it is listed."""
    from trace import usaspending  # noqa: E402  (trace reads the datapack this module writes beside)
    manifest = own_manifest()
    contracts: dict[str, dict | None] = {}
    for release in sorted(RELEASES, key=lambda r: (r.rsplit("_", 1)[-1], r), reverse=True):
        path = ROOT / "datapack" / release / "joins.csv"
        if not path.exists():
            continue
        with path.open(newline="") as handle:
            joins = [j for j in csv.DictReader(handle) if j["join_type"] == "existing_contract"]
        office_of = {n["record_key"]: n["office_id"] for n in read_layer(release, "needs")}
        for join in joins:
            piid = join["key_used"]
            if piid not in contracts:
                contracts[piid] = incumbent_end(manifest, piid, usaspending)
                if contracts[piid]:
                    contracts[piid].update(lines=[], office=office_of.get(join["record_key"]))
            key, _ = canon(release, join["record_key"])
            if contracts[piid] and key not in contracts[piid]["lines"]:
                contracts[piid]["lines"].append(key)
    rows = []
    for piid, c in sorted((k, v) for k, v in contracts.items() if v):
        fpds = c["source"] == "fpds"
        claim_key, capture = f"{'fpds' if fpds else 'usaspending'}:{piid}:expires", c["capture"]
        how = (f"{c['actions']} FPDS action(s), the completion date stated by the action signed {c['signed']}" if fpds
               else f"the USAspending period of performance, the award record last modified {c['signed']}")
        rows.append([lit(uid("brainitem", claim_key)), lit(AGENCY_NAVY), lit("vendors_incumbents"), lit("narrative"), lit(claim_key),
                     lit(f"Incumbent contract {piid} ends {c['expires_on']}"),
                     lit(f"{c['vendor'] or 'vendor unstated'}; contracting office {c['contracting_office'] or 'unstated'}; {how}; "
                         f"incumbent on forecast line(s) {', '.join(c['lines'])}"),
                     jsonb({"url": capture["url"], "sha256": capture["sha256"], "retrieved_at": capture["retrieved_at"], "piid": piid,
                            **({"pages": c["pages"]} if fpds else {})}),
                     lit(c["signed"]),
                     *event_columns("contract_expires", c["signed"], "official", FPDS_PROVIDER if fpds else USASPENDING_PROVIDER, org_ids.get(c["office"]),
                                    {"piid": piid, "expires_on": c["expires_on"], "vendor": c["vendor"],
                                     "contracting_office": c["contracting_office"], "idv": c["idv"], "lines": c["lines"]})])
    return rows


OFFICE_CODE_RE = re.compile(P["office_key_re"])


def office_key(match: re.Match) -> str:
    return "".join(match.groups())


def office_index(seed: dict, code_re: re.Pattern = OFFICE_CODE_RE) -> dict[str, str]:
    """Office key -> node id over `codes.office_code`, so "PMW-160", "PMW160" and "PMW 160" in a contract
    description all name pmw:160, and "PMA 101" or "PMW 101" both name the joint office. An office the agency
    writes by an acronym the profile's pattern reads (DARPA's "STO", written "STO3" too) is indexed by that code."""
    index: dict[str, str] = {}
    for node in seed["nodes"]:
        code = (node.get("codes") or {}).get("office_code") or ""
        digits = re.search(r"\d{3}", code)
        if digits:
            for family in re.findall(r"PM[WSA]", code):
                index.setdefault(family + digits.group(0), node["id"])
        whole = code_re.fullmatch(code)
        if whole:
            index.setdefault(office_key(whole), node["id"])
    return index


def office_of(text: str, offices: dict[str, str], code_re: re.Pattern = OFFICE_CODE_RE) -> str | None:
    """The node the first office code in `text` names, if the memory has one."""
    m = code_re.search(text)
    return offices.get(office_key(m)) if m else None


def solicitation_key(number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (number or "").upper())


def notice_offices_by_solicitation() -> dict[str, list[str]]:
    """Solicitation number -> the specific current offices the saved SAM.gov notices under it name. Step 4 of the
    attribution process in research/04: the notice for an action's solicitation is an official record of the same
    action, and NAVWAR's notices open by naming the office."""
    from trace import NOTICES, match_context, notice_detail, resolve_offices, specific_offices  # noqa: E402
    parents, out = match_context()["parents"], {}
    for path in sorted(NOTICES.glob("*.json")) if NOTICES.exists() else []:
        if path.name.endswith(".resources.json") or path.name.startswith(("._", "search_")):
            continue
        d = notice_detail(path.stem)
        if d and d["solicitation"]:
            key = solicitation_key(d["solicitation"])
            out[key] = sorted(set(out.get(key, [])) | set(specific_offices(resolve_offices(d["text"]), parents)))
    return out


def award_office(award: dict, offices: dict[tuple[str, str], str], uics: dict[str, str], by_solicitation: dict[str, list[str]]) -> tuple[str | None, str]:
    """Where a swept award sits, in the order research/04 fixes: the office code its description names (step 3), else
    the one office the saved notice under its solicitation names (step 4), else the funding office's node, else the
    contracting office's. Two offices on the notice leave the award at the contracting office for a reviewer."""
    coded = office_of(" ".join(award["description"].split()).upper(), offices)
    if coded:
        return coded, "the office code in the description"
    named = by_solicitation.get(solicitation_key(award.get("solicitation") or ""), [])
    if len(named) == 1:
        return named[0], f"the office the notice under solicitation {award['solicitation']} names"
    if uics.get((award["funding_office"] or "").upper()):
        return uics[award["funding_office"].upper()], "the funding office"
    return uics.get((award["contracting_office"] or "").upper()), "the contracting office"


def swept_expiries(org_ids: dict[str, str], uics: dict[str, str], offices: dict[tuple[str, str], str],
                   emitted: list[list[str]], by_solicitation: dict[str, list[str]] | None = None) -> list[list[str]]:
    """One derived item per base award the FPDS office sweep saved that states a completion date and a
    description: the contracting office's whole book of incumbents, dated the day each award was signed.
    The office is `award_office`: the description's office code, else the one office the notice under the
    award's solicitation names, else the funding office's node, else the contracting office. A contract a
    forecast row already names keeps that item: one claim key, one item."""
    have = {row[4] for row in emitted}
    rows = []
    by_solicitation = by_solicitation if by_solicitation is not None else notice_offices_by_solicitation()
    for award in swept_awards():
        piid, expires_on, signed = award["piid"], award["completion"], award["signed"]
        description = " ".join(award["description"].split())
        if not expires_on or not description:
            note_skip("swept FPDS award states no completion date or no description")
            continue
        claim_key = f"fpds:{piid}:expires"
        if lit(claim_key) in have:
            continue
        office, placed_by = award_office(award, offices, uics, by_solicitation)
        page = award["page"]
        rows.append([lit(uid("brainitem", claim_key)), lit(AGENCY_NAVY), lit("vendors_incumbents"), lit("narrative"), lit(claim_key),
                     lit(f"Incumbent contract {piid} ends {expires_on}: {description}"),
                     lit(f"{award['vendor'] or 'vendor unstated'}; contracting office {award['contracting_office'] or 'unstated'}; "
                         f"base award signed {signed}, the ultimate completion date FPDS states on it; found by the office sweep; "
                         f"placed by {placed_by}"),
                     jsonb({"url": page["url"], "sha256": page["sha256"], "retrieved_at": page["retrieved_at"], "piid": piid}),
                     lit(signed),
                     *event_columns("contract_expires", signed, "official", FPDS_PROVIDER, org_ids.get(office),
                                    {"piid": piid, "expires_on": expires_on, "vendor": award["vendor"],
                                     "contracting_office": award["contracting_office"], "funding_office": award["funding_office"],
                                     "idv": award["idv"], "description": description})])
    return rows


# The modification reasons FPDS codes that change what a competitor can do; funding-only and administrative
# actions are left out. A modification that moves the completion date later is an extension whatever its code.
MOD_REASONS = {"G": "exercised an option", "A": "added work outside its scope", "J": "was novated to a new vendor",
               "E": "was terminated for default", "F": "was terminated for convenience", "X": "was terminated for cause",
               "N": "was cancelled"}
# FPDS's research code on an award: a Phase I or II award is a selection on a topic, Phase III the transition to a program.
SBIR_PHASE = {"SR1": ("sbir_selection", "SBIR Phase I"), "ST1": ("sbir_selection", "STTR Phase I"),
              "SR2": ("sbir_selection", "SBIR Phase II"), "ST2": ("sbir_selection", "STTR Phase II"),
              "SR3": ("prototype_transition", "SBIR Phase III"), "ST3": ("prototype_transition", "STTR Phase III")}
TOPIC_RE = re.compile(P["topic_re"])


def award_changes(award: dict, actions: list[dict]) -> list[tuple[dict, str, str]]:
    """What the modifications on one contract did, oldest first, as (action, event type, what happened)."""
    out, end = [], award["completion"]
    mods = sorted((a for a in actions if a["piid"] == award["piid"] and a["mod"] not in ("", "0")), key=lambda a: (a["signed"], a["mod"]))
    for a in mods:
        if a["completion"] and end and a["completion"] > end:
            out.append((a, "contract_extended", f"extended, ends {a['completion']} (was {end})"))
            end = a["completion"]
        elif a["reason"] in MOD_REASONS:
            out.append((a, "contract_modified", MOD_REASONS[a["reason"]]))
    return out


def emit_award_changes(out: list[str], org_ids: dict[str, str], uics: dict[str, str], offices: dict[tuple[str, str], str]) -> None:
    """Extensions, options, terminations and novations on the swept awards whose FPDS history is saved (fpds_sweep.py
    histories), dated the day each modification was signed, and the SBIR/STTR phase FPDS codes on each swept base
    award, dated the day it was signed. Placed where the award itself is (award_office). The extension's title states
    the new end the way an expiry does, so the pulse reads the newest end of a contract."""
    index, by_solicitation, items, seen = url_index(own_manifest()), notice_offices_by_solicitation(), [], set()
    for award in swept_awards():
        office, placed_by = award_office(award, offices, uics, by_solicitation)
        piid, description = award["piid"], " ".join(award["description"].split())
        pages, actions, _ = fpds_history(index, piid)
        for a, event, what in award_changes(award, actions):
            claim_key = f"fpds:{piid}:{a['mod']}"
            if claim_key in seen:
                continue
            seen.add(claim_key)
            page = next(p for p in pages if p["sha256"] == a["page"])
            items.append([lit(uid("brainitem", claim_key)), lit(AGENCY_NAVY), lit("vendors_incumbents"), lit("narrative"), lit(claim_key),
                          lit(f"Incumbent contract {piid} {what}: {description}"),
                          lit(f"{a['vendor'] or award['vendor'] or 'vendor unstated'}; modification {a['mod']} signed {a['signed']}"
                              f"{'; reason code ' + a['reason'] if a['reason'] else ''}; placed by {placed_by}"),
                          jsonb({"url": page["url"], "sha256": page["sha256"], "retrieved_at": page["retrieved_at"], "piid": piid}),
                          lit(a["signed"]),
                          *event_columns(event, a["signed"], "official", FPDS_PROVIDER, org_ids.get(office),
                                         {"piid": piid, "modification": a["mod"], "reason": a["reason"], "expires_on": a["completion"],
                                          "base_expires_on": award["completion"], "vendor": a["vendor"] or award["vendor"],
                                          "contracting_office": award["contracting_office"], "description": description})])
        if award["research"] in SBIR_PHASE:
            event, label = SBIR_PHASE[award["research"]]
            claim_key, page = f"fpds:{piid}:sbir", award["page"]
            items.append([lit(uid("brainitem", claim_key)), lit(AGENCY_NAVY), lit("vendors_incumbents"), lit("narrative"), lit(claim_key),
                          lit(f"{label} award {piid} to {award['vendor'] or 'a vendor unstated'}: {description}"),
                          lit(f"base award signed {award['signed']}; FPDS research code {award['research']}; placed by {placed_by}"),
                          jsonb({"url": page["url"], "sha256": page["sha256"], "retrieved_at": page["retrieved_at"], "piid": piid}),
                          lit(award["signed"]),
                          *event_columns(event, award["signed"], "official", FPDS_PROVIDER, org_ids.get(office),
                                         {"piid": piid, "research": award["research"], "topic_codes": sorted(set(TOPIC_RE.findall(description))),
                                          "vendor": award["vendor"], "contracting_office": award["contracting_office"], "description": description})])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)


# The connectors that write the shared row shape (claim_key, event_type, published, title, body, section, url,
# sha256, retrieved_at, path, excerpt, uic, data), each with the registry row it is filed under.
RECORDS = (("protest_events.json", "gao_bid_protests"), ("congress_events.json", "govinfo_api"), ("fedreg_events.json", "federal_register"),
           ("assistance_awards.json", "usaspending_api"), ("ai_use_cases.json", "ai_use_case_inventory"))


def record_office(row: dict, uics: dict[str, str], offices: dict[tuple[str, str], str], by_solicitation: dict[str, list[str]]) -> str:
    """Where a connector row sits that did not place itself (a row naming its `node` sits there): the one office the
    notice under its solicitation names, else its UIC's node, else the office code its title or excerpt names, else
    the Department."""
    named = by_solicitation.get(solicitation_key(row["data"].get("solicitation") or ""), [])
    if len(named) == 1:
        return named[0]
    if uics.get((row.get("uic") or "").upper()):
        return uics[row["uic"].upper()]
    return office_of(" ".join(f"{row['title']} {row['excerpt']}".split()).upper(), offices) or DEPARTMENT_NODE


def emit_records(out: list[str], org_ids: dict[str, str], uics: dict[str, str], offices: dict[tuple[str, str], str]) -> None:
    """Every row the connectors in RECORDS built as a dated brain item with its excerpt as evidence. A row the connector
    left without an event type (a routine Federal Register form, say) stays in its file for review and is not loaded."""
    by_solicitation = notice_offices_by_solicitation()
    for name, provider in RECORDS:
        path = EVENTS_DIR / name
        if not path.exists():
            note_skip(f"{name} not built; run the connector's build")
            continue
        items, evidence = [], []
        for row in json.loads(path.read_text(encoding="utf-8"))["rows"]:
            if row["event_type"] is None:
                note_skip(f"{name} row without an event type not loaded")
                continue
            assert row["event_type"] in EVENT_TYPES, row["event_type"]
            assert row["section"] in SECTIONS, row["section"]
            item_id = uid("brainitem", row["claim_key"])
            items.append([lit(item_id), lit(AGENCY_NAVY), lit(row["section"]), lit("narrative"), lit(row["claim_key"]), lit(row["title"][:200].strip()),
                          lit(row["body"][:6000]),
                          jsonb({"url": row["url"], "sha256": row["sha256"], "retrieved_at": row["retrieved_at"], "path": row["path"]}),
                          lit(row["published"]),
                          *event_columns(row["event_type"], row["published"], "official", provider,
                                         org_ids.get(row["data"].get("node")) or org_ids.get(record_office(row, uics, offices, by_solicitation)),
                                         row["data"])])
            evidence.append([lit(uid("evidence", row["claim_key"])), lit(item_id), lit(row["excerpt"][:600]), lit(row["url"]),
                             lit(row["published"]), lit(urlparse(row["url"]).netloc), lit(row["claim_key"])])
        insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
        insert("public.gov_intelligence_evidence", ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


def assertion(rows: list, ident: str, kind: str, lineage: str, basis: str, rationale: str,
              source_key: str, observed_at: str | None, supersedes: str | None = None,
              valid_from: str | None = None, valid_to: str | None = None) -> None:
    rows.append([
        lit(ident), lit(kind), lit(lineage), lit(basis), lit(rationale),
        lit(PRODUCER), lit(PRODUCER_VERSION), lit(source_key), lit(observed_at),
        lit(supersedes), lit(valid_from), lit(valid_to),
    ])


def after_priors(rows: list) -> list:
    """The assertion rows in an order the supersession trigger accepts: each after the row it supersedes, where that row
    is among them (notices under two solicitation numbers can state one requirement, the later group sorting first)."""
    by_id, placed, ordered = {r[0]: r for r in rows}, set(), []

    def place(row: list) -> None:
        if row[0] in placed:
            return
        placed.add(row[0])
        if row[9] in by_id:
            place(by_id[row[9]])
        ordered.append(row)

    for row in rows:
        place(row)
    return ordered


ASSERTION_COLUMNS = ["id", "assertion_kind", "lineage_key", "basis", "rationale", "producer",
                     "producer_version", "source_key", "observed_at", "supersedes_id",
                     "valid_from", "valid_to"]


def uic_index(seed: dict) -> dict[str, str]:
    """Contracting UIC -> node id, over `codes.uic` and `codes.contracting_uic`; a center that contracts
    for itself is one node, so the LRAE's contracting column binds to it rather than to `contracting:<uic>`."""
    index: dict[str, str] = {}
    for node in seed["nodes"]:
        for key in ("uic", "contracting_uic"):
            code = (node.get("codes") or {}).get(key)
            if code:
                index.setdefault(code.upper(), node["id"])
    return index


def emit_lrae(org_ids: dict[str, str], out: list[str], uics: dict[str, str] | None = None,
              offices: dict[tuple[str, str], str] | None = None) -> None:
    uics = uics or {}
    if not RELEASES:
        note_skip("no forecast releases for this agency; the forecast layer is not emitted")
        # The swept awards are no forecast's: the incumbent book stands without one.
        insert("public.agency_brain_items", ITEM_COLUMNS, swept_expiries(org_ids, uics, offices or {}, []), out)
        return {}
    # The accepted connections between releases (lrae_package.fold_map): a row the diffs
    # tie with `confirmed` loads under its chain's key, and carries how it was tied.
    folded, refused = fold_map(ROOT / "datapack")
    for line in refused:
        note_skip(f"chain not folded, rows load as their own records: {line}")
    events = lrae_events(folded)

    # Evidence first: every LRAE claim cites one spreadsheet row.
    items, evidence, ev_by_id = [], [], {}
    for release in RELEASES:
        # The spreadsheet the row was read from, so a claim can be followed to its bytes.
        url = release_source_url(release)
        raw = stated_rows(release)
        for row in read_layer(release, "evidence"):
            claim_key = f"{activity_of(release)}-lrae:{row['id']}"
            item_id, ev_id = uid("brainitem", claim_key), uid("evidence", claim_key)
            ev_by_id[row["id"]] = ev_id
            body = f"{release} {row['locator']}, source sha256 {row['source_sha256']}"
            event, data, office = events[row["id"]]
            # Nested, so the event text the readings see stays the row's locator; the corpus carries it whole.
            stated, page = raw.get(row["id"], ({}, ""))
            data = {**data, **({"stated": stated} if stated else {})}
            items.append([
                lit(item_id), lit(AGENCY_NAVY), lit("forecast"), lit("narrative"), lit(claim_key),
                lit(f"{FORECAST_SHORT} {release} {row['locator']}"), lit(body),
                jsonb({"release": release, "locator": row["locator"],
                       "sha256": row["source_sha256"], "release_date": row["release_date"], **({"url": page} if page else {})}),
                lit(row["release_date"]),
                *event_columns(event, row["release_date"], "official", LRAE_PROVIDERS[activity_of(release)], org_ids.get(office), data),
            ])
            evidence.append([lit(ev_id), lit(item_id), lit(body), lit(url),
                             lit(row["release_date"]), lit(f"{activity_of(release)}-lrae"), lit(claim_key)])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence",
           ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"],
           evidence, out)

    # A record key identifies the same planned action across releases, so the need is
    # keyed on it alone and each release becomes a revision of its requirement rather
    # than a second need describing the same thing.
    def canon(release: str, key: str) -> tuple[str, dict | None]:
        tie = folded.get((release, key))
        return (tie["key"], tie) if tie else (key, None)

    def tie_note(tie: dict | None) -> str:
        return f"Row tied to this requirement by {tie['reason']} ({tie['via']}). " if tie else ""

    def basis_of(tie: dict | None) -> str:
        return "inferred" if tie else "documented"

    needs, need_releases, first_seen, tied_rows = {}, {}, {}, {}
    # Each release states its own date. Merging the need records must not let a 2025
    # release date attach to the 2023 revision of a line that appears in both.
    stated_on: dict[tuple[str, str], str | None] = {}
    per_release: dict[tuple[str, str], dict] = {}
    for release in RELEASES:
        for row in read_layer(release, "needs"):
            key, tie = canon(release, row["record_key"])
            needs.setdefault(key, row)
            needs[key] = {**needs[key], **{k: v for k, v in row.items() if v}}
            need_releases.setdefault(key, []).append(release)
            per_release[(release, key)] = row
            if tie:
                tied_rows.setdefault(key, []).append(f"{release} {row['evidence_id'].split(':')[-1]} tied by {tie['basis']}")
            seen = row["valid_from"] or None
            stated_on[(release, key)] = seen
            if seen and (first_seen.get(key) is None or seen < first_seen[key]):
                first_seen[key] = seen

    insert("public.gov_needs", ["id", "agency_id", "title", "description", "lifecycle", "source", "source_key"],
           [[lit(uid("need", key)), lit(AGENCY_NAVY), lit(row["title"]),
             lit(f"{activity_of(need_releases[key][0]).upper()} {FORECAST_LABEL} line {key}, first seen {first_seen.get(key) or 'undated'}, "
                 f"released in {', '.join(need_releases[key])}"
                 + (f"; {'; '.join(tied_rows[key])}" if key in tied_rows else "")),
             lit("identified"), lit(LRAE_SOURCE), lit(key)]
            for key, row in needs.items()], out)

    assertions, details, links = [], {"need_organization": [], "requirement": [], "funding": []}, []

    def cite(assertion_id: str, evidence_id: str | None, direct: bool) -> bool:
        if not evidence_id:
            note_skip("claim with no evidence row; not loaded")
            return False
        links.append([lit(assertion_id), lit(evidence_id), lit("supports"), "true" if direct else "false"])
        return True

    # Which office owns the requirement and which one contracts for it, per release.
    #
    # Merging the need records and reading one office off the result applies the
    # newest release's office to every earlier year. Each release is its own dated
    # observation instead. Where consecutive releases name the same office the later
    # assertion supersedes the earlier, so the current view holds one row; where they
    # name different offices supersession is refused by design (it may not change the
    # subject), both observations stay live, and the disagreement is visible rather
    # than resolved by whichever row was read last.
    last_office: dict[tuple[str, str], tuple[str, str]] = {}
    for release in RELEASES:
        for key in sorted(k for r, k in per_release if r == release):
            row = per_release[(release, key)]
            tie = folded.get((release, row["record_key"]))
            ev_id = ev_by_id.get(row["evidence_id"])
            for local_id, role in ((row["office_id"], "requirement_owner"),
                                   (uics.get(row["contracting_office_uic"].upper(), "contracting:" + row["contracting_office_uic"].lower()), "contracting")):
                if local_id not in org_ids:
                    note_skip(f"{FORECAST_SHORT} office {local_id or '(code the memory does not resolve)'} has no node in the org memory")
                    continue
                ident = uid("assert", f"needorg:{release}:{key}:{local_id}:{role}")
                if not cite(ident, ev_id, True):
                    continue
                previous = last_office.get((key, role))
                prior = chain_or_branch(previous, local_id)
                if previous and prior is None:
                    note_skip(f"office for a {role.replace('_', ' ')} changed between releases; "
                              f"both observations kept")
                last_office[(key, role)] = (local_id, ident)
                assertion(assertions, ident, "need_organization",
                          f"need_organization:{key}:{role}", basis_of(tie),
                          tie_note(tie) + f"{release} names this office as the {role.replace('_', ' ')}.",
                          f"{LRAE_SOURCE}:{release}:{key}:{role}",
                          stated_on.get((release, key)), prior)
                details["need_organization"].append(
                    [lit(ident), lit(uid("need", key)), lit(org_ids[local_id]), lit(NEED_ROLE[role])])

    # One requirement per need, one revision assertion per release it appeared in.
    # A later release supersedes the earlier assertion, which is how a slipping
    # award date stays visible instead of being overwritten.
    requirements, revisions_by_need, funding_scope = [], {}, {}
    for release in RELEASES:
        for row in read_layer(release, "need_requirements"):
            key, tie = canon(release, row["need_id"].split(":", 2)[2])
            req_id = uid("requirement", key)
            if key not in revisions_by_need:
                requirements.append([lit(req_id), lit(uid("need", key)), lit("lrae-line"), lit("scope")])
                revisions_by_need[key] = []
            ident = uid("assert", f"requirement:{key}:{release}")
            ev_id = ev_by_id.get(row["evidence_id"])
            if not cite(ident, ev_id, True):
                continue
            prior = revisions_by_need[key][-1] if revisions_by_need[key] else None
            revisions_by_need[key].append(ident)
            award_fy = fiscal_year(row["award_fy"])
            expected_from, expected_to = quarter_bounds(award_fy, row["award_quarter"])
            # The release's own title: the merged need carries the newest release's, which this release may not state.
            statement = row["description"] or per_release[(release, key)]["title"]
            assertion(assertions, ident, "requirement", f"requirement:{key}", basis_of(tie),
                      tie_note(tie) + f"{release} states the scope, method ({row['procurement_method'] or 'unstated'}) "
                      f"and award timing ({row['award_fy'] or 'unstated'} {row['award_quarter']}).".strip(),
                      f"{LRAE_SOURCE}:{release}:{row['id']}", stated_on.get((release, key)), prior)
            details["requirement"].append(
                [lit(ident), lit(req_id), lit(statement), lit(expected_from), lit(expected_to)])

    # An LRAE value range is an estimate, not an obligation, and `measure` says so.
    #
    # The table accepts a flat amount or a closed range and nothing else. The LRAE
    # never states a flat amount, so a row is loadable only when the source gives
    # both bounds. "> $1B+" gives a floor and no ceiling; writing the floor as the
    # amount would publish "exactly $1B" for a buy the Navy only said exceeds $1B,
    # and writing the floor as both bounds would be the same lie twice. Those rows
    # and the "No Range Specified" ones stay out until the column can hold an open
    # range, and the wording the source used travels with every row that does load.
    for release in RELEASES:
        for row in read_layer(release, "funding_observations"):
            stated = (row["as_stated"] or "").strip()
            if not row["amount_low_usd"] and not row["amount_high_usd"]:
                note_skip(f"no value stated ({stated or 'blank'})")
                continue
            if not row["amount_low_usd"] or not row["amount_high_usd"]:
                note_skip(f"open-ended value the schema cannot hold ({stated})")
                continue
            key, tie = canon(release, row["need_id"].split(":", 2)[2])
            ident = uid("assert", f"funding:{key}:{release}")
            ev_id = ev_by_id.get(row["evidence_id"])
            if not cite(ident, ev_id, True):
                continue
            fy = fiscal_year(row["fiscal_year"])
            start, end = quarter_bounds(fy, row["period"])
            low, high = row["amount_low_usd"], row["amount_high_usd"]
            # Supersession may not change the measurement scope, so a re-estimate of
            # the same need for the same fiscal period chains and only the latest
            # stays current. A move to a different fiscal year is a different
            # measurement, not a correction, and both remain on the record.
            lineage = funding_lineage(key, row["fiscal_year"], row["period"])
            prior = funding_scope.get(lineage)
            funding_scope[lineage] = ident
            assertion(assertions, ident, "funding", lineage,
                      basis_of(tie),
                      tie_note(tie) + f"{release} states an anticipated total value of {row['as_stated'] or 'an unstated range'}.",
                      f"{LRAE_SOURCE}:{release}:{row['id']}", stated_on.get((release, key)), prior)
            details["funding"].append([
                lit(ident), lit(uid("need", key)), lit("procurement_estimate"),
                "null", lit(low), lit(high),
                str(fy) if fy else "null", lit(start), lit(end),
                lit(f"{activity_of(release).upper()} {FORECAST_SHORT} {release} anticipated total contract value, "
                    f"stated as {stated}"),
            ])

    insert("public.gov_intelligence_assertions", ASSERTION_COLUMNS, assertions, out)
    insert("public.gov_need_organizations", ["assertion_id", "need_id", "organization_id", "role"],
           details["need_organization"], out)
    insert("public.gov_need_requirements", ["id", "need_id", "requirement_key", "kind"], requirements, out)
    insert("public.gov_requirement_revisions",
           ["assertion_id", "requirement_id", "statement", "expected_from", "expected_to"],
           details["requirement"], out)
    insert("public.gov_funding_observations",
           ["assertion_id", "need_id", "measure", "amount", "amount_low", "amount_high",
            "fiscal_year", "period_start", "period_end", "scope_description"],
           details["funding"], out)
    insert("public.gov_assertion_evidence", ["assertion_id", "evidence_id", "relationship", "is_direct"],
           links, out)

    # gov_procurement_refs keys on a row that already exists in sam_opportunities,
    # usa_awards, usa_award_children or gov_procurement_records. An incumbent PIID
    # read off a forecast spreadsheet is none of those until it is ingested.
    piids = {row["identifier"] for release in RELEASES for row in read_layer(release, "procurement_refs")}
    for _ in piids:
        note_skip("incumbent PIID has no procurement record to reference")
    expiries = contract_expiries(org_ids, canon)
    insert("public.agency_brain_items", ITEM_COLUMNS, expiries + swept_expiries(org_ids, uics or {}, offices or {}, expiries), out)
    return revisions_by_need


# ------------------------------------------------------------ SAM.gov notices

NOTICE_SOURCE = provenance(SAM_NOTICES)
# The lifecycle a notice type states for its requirement; the latest notice under a number sets it, and a base award under it fulfils it.
NOTICE_LIFECYCLE = {"sources sought": "identified", "special notice": "identified", "presolicitation": "planned",
                    "solicitation": "in_procurement", "combined synopsis/solicitation": "in_procurement",
                    "justification (J&A)": "in_procurement", "intent to bundle": "planned", "award notice": "fulfilled"}
NOTICE_SECTION = {"sources sought": "industry_engagement", "special notice": "industry_engagement"}
# What happened when a notice of this type was posted; a special notice says by its title.
NOTICE_EVENT = {"sources sought": "rfi_released", "presolicitation": "presolicitation_posted",
                "solicitation": "rfp_released", "combined synopsis/solicitation": "rfp_released",
                "award notice": "contract_awarded", "justification (J&A)": "justification_posted"}
SPECIAL_EVENT = {"industry day": "industry_engagement", "action on an existing contract": "contract_modified",
                 "forecast": "forecast_created", "intent to award a sole source": "justification_posted",
                 "intent to award without full competition": "justification_posted",
                 "commercial solutions opening": "rfp_released", "request for information": "rfi_released",
                 "draft solicitation": "presolicitation_posted", "award announcement": "contract_awarded"}


def notice_event(notice_type: str, kind: str) -> str | None:
    if notice_type == "special notice":
        return next((event for said, event in SPECIAL_EVENT.items() if kind.startswith(said)), None)
    return NOTICE_EVENT.get(notice_type)


def outdates_forecast(stated: str, chain: list[dict]) -> bool:
    """A notice or award tied to a forecast line says where the line stands only when dated after the line's latest
    release; one the latest release already follows is history the forecast outlived."""
    return stated > max((l["release_date"] or "" for l in chain), default="")


def notice_group_key(detail: dict) -> str:
    return f"notice:{(detail['solicitation'] or '').upper().replace('-', '').replace(' ', '') or detail['id']}"


def emit_notices(org_ids: dict[str, str], out: list[str], revisions_by_need: dict[str, list[str]]) -> None:
    """Every harvested SAM.gov notice as a signal on a requirement: one need per solicitation number.

    A notice whose group one forecast line names explicitly (trace.place_notice) writes its
    revision under that line's need and chains after the line's latest forecast revision. Any
    other group creates its own need keyed `notice:<solicitation number>`, with the office the
    text names (the most specific current one; two branches leave it unasserted) and the
    contracting office SAM.gov posted it under. A notice posted by an organization the memory
    lacks and naming no office it knows is not this agency's need and is counted, not loaded.
    Candidates never load: a reviewer accepts them first.
    """
    from trace import (NOTICES, SAM_ORG_NODES, SAM_VIEW, compact, manifest, match_context, notice_detail, notice_source,  # noqa: E402
                       place_notice, resolve_offices, signal_kind, specific_offices, swept_by_solicitation)

    ctx, rows = match_context(), manifest()
    groups: dict[str, list[dict]] = {}
    for path in sorted(NOTICES.glob("*.json")) if NOTICES.exists() else []:
        if path.name.endswith(".resources.json") or path.name.startswith(("._", "search_")):
            continue
        d = notice_detail(path.stem)
        if not d or not d["title"] or not d["posted"]:
            continue
        d["offices"] = specific_offices(resolve_offices(d["text"]), ctx["parents"])
        d["contracting"] = SAM_ORG_NODES.get(d["organization_id"])
        if not d["contracting"] and not d["offices"]:
            note_skip("notice posted by an organization the memory lacks and naming no office it knows; not this agency's need")
            continue
        groups.setdefault(notice_group_key(d), []).append(d)

    items, evidence, needs, assertions, links, requirements = [], [], [], [], [], []
    details: dict[str, list] = {"need_organization": [], "requirement": []}
    advanced: dict[str, tuple[str, str]] = {}
    placed = []
    for group_key, notices in sorted(groups.items()):
        notices.sort(key=lambda d: (d["posted"], d["id"]))
        placed.append((group_key, notices, place_notice(notices[-1], ctx, notices[:-1])))
    # The groups tied to one requirement state one history: each notice supersedes the one posted before it, whichever
    # solicitation number it came under, and the first supersedes the forecast line's latest revision.
    tied: dict[str, list[dict]] = {}
    for _, notices, place in placed:
        tied.setdefault(place["key"], []).extend(notices)
    prior_of: dict[str, str | None] = {}
    for key, stated in tied.items():
        prior = (revisions_by_need.get(key) or [None])[-1]
        for d in sorted(stated, key=lambda d: (d["posted"], d["id"])):
            prior_of[d["id"]], prior = prior, uid("assert", f"requirement:{key}:notice:{d['id']}")
    for group_key, notices, place in placed:
        latest = notices[-1]
        key = place["key"]
        need_id, req_id = uid("need", key), uid("requirement", key)
        # A base award the FPDS office sweep saved under the number fulfils it; an order names its vehicle's solicitation.
        awarded = swept_by_solicitation().get(compact(latest["solicitation"] or ""), [])
        # A special notice's lifecycle is its type's (identified) unless the model's reading or its title says it is an
        # intent to award without full competition, which states the same stage as a justification: in procurement.
        lifecycle = ("cancelled" if latest["cancelled"]
                     else "fulfilled" if awarded
                     else "in_procurement" if signal_kind(latest).startswith(("intent to award without full competition", "intent to award a sole source"))
                     else NOTICE_LIFECYCLE.get(latest["type"], "unknown"))
        if place["how"] == "created":
            kinds = ", ".join(dict.fromkeys(f"{d['type']} {d['posted']}" for d in notices))
            offices = sorted({o for d in notices for o in d["offices"]})
            description = (f"SAM.gov {group_key.split(':', 1)[1]}: {len(notices)} notice(s) ({kinds}); {place['note']}"
                           + (f"; office named in the text: {', '.join(offices)}" if offices else "; no office the memory knows is named in the text")
                           + (f"; {', '.join(r['key'] for r in place['ambiguous'])} each claim it and a reviewer decides" if place["ambiguous"] else "")
                           + (f"; {len(awarded)} base award(s) under the number on the FPDS office sweep, first {awarded[0]['piid']} signed "
                              f"{awarded[0]['signed']} to {awarded[0]['vendor']}" if awarded else ""))
            needs.append([lit(need_id), lit(AGENCY_NAVY), lit(latest["title"]), lit(description), lit(lifecycle), lit(NOTICE_SOURCE), lit(key)])
            requirements.append([lit(req_id), lit(need_id), lit(key), lit("scope")])
        else:
            # The forecast line loaded its need as identified; the newest tied group that outdates the forecast moves it.
            stated = max([latest["posted"], *(a["signed"] for a in awarded)])
            if outdates_forecast(stated, ctx["chains"].get(key, [])) and stated >= advanced.get(need_id, ("", ""))[0]:
                advanced[need_id] = (stated, lifecycle)
        last_office: dict[str, tuple[str, str]] = {}
        for d in notices:
            claim_key = f"sam-notice:{d['id']}"
            item_id, ev_id = uid("brainitem", claim_key), uid("evidence", claim_key)
            record = notice_source(rows, d["id"]) or {}
            view = SAM_VIEW.format(d["id"])
            named = next((o for o in resolve_offices(d["text"]) if o["office"] in d["offices"]), None)
            kind, event = signal_kind(d), notice_event(d["type"], signal_kind(d))
            if event is None:
                note_skip(f"no event type for a SAM.gov notice read as `{kind}`")
            items.append([lit(item_id), lit(AGENCY_NAVY), lit(NOTICE_SECTION.get(d["type"], "procurement_patterns")), lit("narrative"), lit(claim_key),
                          lit(f"SAM.gov {d['type']} {d['posted']}: {d['title'][:80]}".strip()),
                          lit(f"{kind}; solicitation {d['solicitation'] or 'none'}; record {record.get('url', 'not in the manifest')} "
                              f"retrieved {str(record.get('retrieved_at', ''))[:10]} sha256 {str(record.get('sha256', ''))[:12]}"),
                          jsonb({"url": view, "record_url": record.get("url"), "retrieved_at": record.get("retrieved_at"), "sha256": record.get("sha256"),
                                 "notice_id": d["id"], "solicitation": d["solicitation"], "type": d["type"]}),
                          lit(d["posted"]),
                          # The statement carries its requirement's key, and a notice tied to a forecast line is filed at the line's office.
                          *event_columns(event, d["posted"], "official", NOTICE_PROVIDER,
                                         org_ids.get(place["line"]["office_id"] if place["how"] == "resolved"
                                                     else d["offices"][0] if len(d["offices"]) == 1 else d["contracting"]),
                                         {"line": key, **({"responses_due": d["deadline"]} if d.get("deadline") else {})})])
            evidence.append([lit(ev_id), lit(item_id), lit((named or {}).get("context") or d["title"][:300]), lit(view), lit(d["posted"]), lit("sam.gov"), lit(claim_key)])
            if not record:
                note_skip("notice detail has no manifest row; loaded with its SAM.gov link only")

            # The requirement as this notice states it; a later notice under the number supersedes an earlier one.
            ident = uid("assert", f"requirement:{key}:notice:{d['id']}")
            links.append([lit(ident), lit(ev_id), lit("supports"), "true"])
            how = f" Tied to the forecast line because {place['why']}." if place["how"] == "resolved" else ""
            assertion(assertions, ident, "requirement", f"requirement:{key}", place["basis"] or "documented",
                      f"SAM.gov {d['type']} posted {d['posted']} ({signal_kind(d)}) states the requirement.{how}",
                      f"{NOTICE_SOURCE}:{d['id']}", d["posted"], prior_of[d["id"]])
            details["requirement"].append([lit(ident), lit(req_id), lit(f"{d['type']} {d['posted']}: {d['title']}"), "null", "null"])
            if place["how"] == "resolved":
                continue  # the line already carries its offices; the notice adds the requirement's later statement
            offices = [(o, "requirement_owner", f"the text names it: '{(named or {}).get('context', '')[:160]}'") for o in d["offices"]] if len(d["offices"]) == 1 else []
            if len(d["offices"]) > 1:
                note_skip(f"notice names offices in more than one branch ({', '.join(d['offices'])}); no owner asserted")
            if d["contracting"]:
                offices.append((d["contracting"], "contracting", f"SAM.gov posts the notice under organization id {d['organization_id']}, the contracting office's own code"))
            for local_id, role, why in offices:
                if local_id not in org_ids:
                    note_skip(f"notice office {local_id} has no node in the org memory")
                    continue
                ident = uid("assert", f"needorg:notice:{d['id']}:{local_id}:{role}")
                links.append([lit(ident), lit(ev_id), lit("supports"), "true"])
                previous = last_office.get(role)
                prior = chain_or_branch(previous, local_id)
                if previous and prior is None:
                    note_skip(f"office for a {role.replace('_', ' ')} changed between notices under one number; both observations kept")
                last_office[role] = (local_id, ident)
                assertion(assertions, ident, "need_organization", f"need_organization:{key}:{role}", "documented",
                          f"SAM.gov {d['type']} posted {d['posted']} names this office as the {role.replace('_', ' ')}: {why}.",
                          f"{NOTICE_SOURCE}:{d['id']}:{local_id}:{role}", d["posted"], prior)
                details["need_organization"].append([lit(ident), lit(need_id), lit(org_ids[local_id]), lit(NEED_ROLE[role])])

    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence", ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)
    insert("public.gov_needs", ["id", "agency_id", "title", "description", "lifecycle", "source", "source_key"], needs, out)
    moved = sorted((need, lc) for need, (_, lc) in advanced.items() if lc != "identified")
    if moved:  # an update, so the lifecycle history trigger records the step from identified
        out.append("update public.gov_needs set lifecycle = v.lifecycle from (values\n"
                   + ",\n".join(f"  ({lit(need)}, {lit(lc)})" for need, lc in moved)
                   + "\n) as v(id, lifecycle) where public.gov_needs.id = v.id::uuid;")
        out.append("")
    insert("public.gov_intelligence_assertions", ASSERTION_COLUMNS, after_priors(assertions), out)
    insert("public.gov_need_organizations", ["assertion_id", "need_id", "organization_id", "role"], details["need_organization"], out)
    insert("public.gov_need_requirements", ["id", "need_id", "requirement_key", "kind"], requirements, out)
    insert("public.gov_requirement_revisions", ["assertion_id", "requirement_id", "statement", "expected_from", "expected_to"], details["requirement"], out)
    insert("public.gov_assertion_evidence", ["assertion_id", "evidence_id", "relationship", "is_direct"], links, out)


NEWS_RECORDS = EVENTS_DIR / "news_observations.json"
NEWS_SOURCE = provenance(EVENTS_DIR / "news_observations.json")
# What a news statement is about, in the sections the brain items table allows.
NEWS_SECTION = {"leadership": "people", "funding": "budget", "performance": "vendors_incumbents",
                "industry_engagement": "industry_engagement", "recompete": "procurement_patterns",
                "acquisition_strategy": "procurement_patterns", "contracting_support": "procurement_patterns",
                "protest": "procurement_patterns"}
# What an article reports, where its dominant claim is something that happened.
NEWS_EVENT = {"leadership": "leadership_change", "consolidation": "reorganization", "parentage": "reorganization",
              "industry_engagement": "industry_engagement", "acquisition_strategy": "strategy_change"}
NEWS_TIER = {"official announcement": "official", "trade reporting": "editorial", "secondary reporting": "editorial"}


def emit_news(out: list[str], hosts: dict[str, str], org_ids: dict[str, str]) -> None:
    """Every modelled article as one brain item, with a piece of evidence per claim. A claim that reports
    something that happened (a leader named, offices merged, industry engaged, a strategy changed) is
    its own item carrying the event, and its evidence row points at it; the article stays the container.

    An article is an observation, so it loads as what a source said and nothing more: no
    relationship, no office assertion, no lifecycle. A claim that conflicts with the memory is
    loaded too, marked as a conflict, because the disagreement is the finding. Promotion into the
    organization memory stays a reviewer's step (research/docs/08_org_memory_format.md).
    """
    if not NEWS_RECORDS.exists():
        note_skip("news observations not built; run research/tools/news.py build")
        return
    articles = json.loads(NEWS_RECORDS.read_text(encoding="utf-8"))["articles"]
    items, evidence = [], []
    for article in articles:
        kinds = [c["statement_type"] for c in article["claims"]]
        # Sorted first: a tie between two kinds must fall the same way on every run.
        dominant = max(sorted(set(kinds)), key=kinds.count) if kinds else ""
        section = NEWS_SECTION.get(dominant, "mission_priorities")
        tier, provider = NEWS_TIER.get(article["source_type"]), hosts.get(urlparse(article["url"]).netloc)
        if tier is None:
            note_skip(f"news source type `{article['source_type']}` has no tier")
        claim_key = article["id"]
        item_id = uid("brainitem", claim_key)
        body = (f"{article['publisher']} {article['source_type']}, {article['reliability']} reliability; "
                f"reads {article['relation']} against the stored record; {len(article['claims'])} claim(s)"
                + (f"; to verify: {'; '.join(article['verify'])}" if article["verify"] else ""))
        items.append([lit(item_id), lit(AGENCY_NAVY), lit(section), lit("narrative"), lit(claim_key),
                      lit(article["headline"][:200].strip()), lit(body),
                      jsonb({"url": article["url"], "publisher": article["publisher"], "author": article["author"],
                             "source_type": article["source_type"], "reliability": article["reliability"],
                             "retrieved_at": article["retrieved_at"], "sha256": article["record"]["sha256"],
                             "record_path": article["record"]["path"], "relation": article["relation"],
                             "offices": article["links"]["offices"], "requirements": article["links"]["requirements"],
                             "awards": article["links"]["awards"], "solicitations": article["links"]["solicitations"],
                             "budget_lines": article["links"]["budget_lines"]}),
                      lit(article["published"] or None),
                      *event_columns(None, article["published"] or None, tier, provider)])
        for number, claim in enumerate(article["claims"], start=1):
            # An undated page (an organization page read as an article) places nothing in time: its claims stay
            # evidence on the article and never become dated events.
            event, target = NEWS_EVENT.get(claim["statement_type"]) if article["published"] else None, item_id
            if event:
                target, passage = uid("brainitem", f"{claim_key}:{number}"), " ".join(claim["passage"].split())
                items.append([lit(target), lit(AGENCY_NAVY), lit(NEWS_SECTION.get(claim["statement_type"], section)), lit("narrative"),
                              lit(f"{claim_key}:{number}"), lit(f"{claim['statement_type']}: {passage[:150]}".strip()),
                              lit(passage[:600] + (f" ({claim['relation']}: {claim['relation_note']})" if claim.get("relation_note") else "")),
                              jsonb({"url": article["url"], "publisher": article["publisher"], "article": claim_key, "claim": number,
                                     "sha256": article["record"]["sha256"], "record_path": article["record"]["path"]}),
                              lit(claim.get("stated_on") or article["published"] or None),
                              *event_columns(event, article["published"] or None, tier, provider,
                                             next((org_ids[s] for s in claim.get("subjects") or [] if s in org_ids), None),
                                             {k: claim.get(k) for k in ("statement_type", "stated_on", "relation", "confidence",
                                                                        "subjects", "people", "programs", "contracts", "solicitations")})])
            evidence.append([lit(uid("evidence", f"{claim_key}:{number}")), lit(target), lit(claim["passage"][:600]),
                             lit(article["url"]), lit(article["published"] or None), lit("news"),
                             # (provider, source_key) is unique, and one article states several
                             # claims of the same kind, so the claim's number is part of the key.
                             lit(f"{claim_key}:{number}:{claim['statement_type']}:{claim['relation']}")])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence",
           ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


OVERSIGHT_RECORDS = EVENTS_DIR / "oversight_events.json"
OVERSIGHT_SECTION = {"funding_change": "budget", "program_delayed": "procurement_patterns", "program_cancelled": "procurement_patterns"}
# The registry row for each kind of report, named outright: gao.gov also carries the bid-protest row, so the
# first-webpage-row-on-the-host rule that serves news would name the wrong source for a report.
OVERSIGHT_PROVIDERS = {"gao": "gao_reports", "oversight": "oversight_gov_reports"}


def emit_oversight(out: list[str], hosts: dict[str, str], org_ids: dict[str, str]) -> None:
    """Every oversight report the agent read as one brain item, and each finding it kept as an event
    item with the passage as evidence (finding, problem, program, remediation, the need that may
    follow). The tier is `official` because GAO and the inspectors general are the
    government's own reviewers; the provider is the registry row for the host. A report with no
    finding about this agency is left out: the agent read it and said so."""
    if not OVERSIGHT_RECORDS.exists():
        note_skip("oversight events not built; run research/tools/oversight.py extract")
        return
    items, evidence = [], []
    for report in json.loads(OVERSIGHT_RECORDS.read_text(encoding="utf-8"))["documents"]:
        if not report["events"]:
            note_skip("oversight report states no finding about this agency")
            continue
        provider = OVERSIGHT_PROVIDERS[report["kind"]]
        claim_key, issued = f"oversight:{report['url']}", report["issued"] or None
        item_id = uid("brainitem", claim_key)
        label = " ".join(p for p in (report["publisher"], report["report_number"]) if p)
        items.append([lit(item_id), lit(AGENCY_NAVY), lit("mission_priorities"), lit("narrative"), lit(claim_key),
                      lit(f"{label}: {report['title']}"[:200].strip()),
                      lit(f"{report['report_type'] or 'report'} issued {report['issued'] or 'on an unstated date'}; {len(report['events'])} finding(s) kept"
                          + (f"; {report['recommendations']} recommendation(s)" if report["recommendations"] else "")
                          + (f"; questioned costs {report['questioned_costs']}" if report["questioned_costs"] else "")),
                      jsonb({"url": report["url"], "publisher": report["publisher"], "report_number": report["report_number"],
                             "report_type": report["report_type"], "agency_reviewed": report["agency_reviewed"],
                             "retrieved_at": report["retrieved_at"], "sha256": report["sha256"], "record_path": report["path"],
                             "file_sha256": report["file_sha256"], "file_path": report["file_path"], "model": report["model"],
                             "cassette": report["cassette"], "source_authority": report["source_authority"]}),
                      lit(issued), *event_columns(None, issued, "official", provider)])
        for number, event in enumerate(report["events"], start=1):
            target = uid("brainitem", f"{claim_key}:{number}")
            org = next((org_ids[o] for o in event["organizations"] if o in org_ids), None)
            items.append([lit(target), lit(AGENCY_NAVY), lit(OVERSIGHT_SECTION.get(event["event_type"], "mission_priorities")), lit("narrative"),
                          lit(f"{claim_key}:{number}"), lit(f"{event['event_type']}: {event['problem'][:160]}"[:200].strip()),
                          lit(event["problem"] + (f" Program: {event['affected_program']}." if event["affected_program"] else "")
                              + (f" Remediation: {event['possible_remediation']}" if event["possible_remediation"] else "")
                              + (f" Need that may follow: {event['future_acquisition_need']}" if event["future_acquisition_need"] else "")),
                          jsonb({"url": report["url"], "report": claim_key, "finding": number, "sha256": report["sha256"], "record_path": report["path"]}),
                          lit(issued),
                          *event_columns(event["event_type"], issued, "official", provider, org,
                                         {k: event.get(k) for k in ("affected_program", "affected_organization", "possible_remediation",
                                                                    "future_acquisition_need", "amounts", "confidence", "organizations",
                                                                    "organizations_from", "source_authority")})])
            evidence.append([lit(uid("evidence", f"{claim_key}:{number}")), lit(target), lit(event["evidence_span"][:600]),
                             lit(report["url"]), lit(issued), lit("oversight"), lit(f"{report['url']}:{number}")])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence",
           ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


REMARKS_RECORDS = EVENTS_DIR / "remarks_events.json"
REMARKS_PROVIDERS = P["remarks"]["providers"]  # the registry row each kind of remarks document is filed under
REMARKS_SECTION = {"industry_engagement": "industry_engagement", "conference_appearance": "industry_engagement", "funding_change": "budget",
                   "congressional_directive": "budget", "program_delayed": "procurement_patterns", "program_cancelled": "procurement_patterns"}
REMARKS_TIER = {"speech": "official", "testimony": "official", "statement": "official", "conference": "editorial"}


def emit_remarks(out: list[str], org_ids: dict[str, str]) -> None:
    """Every speech, hearing statement and conference page the agent read as one brain item, each kept
    event as an item with its passage as evidence. The speaker, role, venue and
    host travel on the document item as the text writes them (blank when the text does not)."""
    if not REMARKS_RECORDS.exists():
        note_skip("remarks events not built; run research/tools/remarks.py extract")
        return
    items, evidence = [], []
    for doc in json.loads(REMARKS_RECORDS.read_text(encoding="utf-8"))["documents"]:
        if not doc["events"]:
            note_skip("remarks document states nothing about this agency")
            continue
        provider, tier = REMARKS_PROVIDERS[doc["kind"]], REMARKS_TIER[doc["kind"]]
        claim_key, issued = f"remarks:{doc['url']}", doc["issued"] or None
        item_id = uid("brainitem", claim_key)
        who = ", ".join(p for p in (doc["speaker_name"], doc["speaker_role"]) if p) or "speaker not stated verbatim"
        items.append([lit(item_id), lit(AGENCY_NAVY), lit("mission_priorities"), lit("narrative"), lit(claim_key),
                      lit(f"{doc['kind']}: {doc['title']}"[:200].strip()),
                      lit(f"{who}; {doc['event_name'] or 'venue not stated verbatim'}"
                          + (f", hosted by {doc['event_host']}" if doc["event_host"] else "") + f"; audience {doc['audience']}; {len(doc['events'])} event(s) kept"),
                      jsonb({"url": doc["url"], "publisher": doc["publisher"], "kind": doc["kind"], "hearing_url": doc["hearing_url"], "date_basis": doc["date_basis"],
                             "witnesses": doc["witnesses"], "speaker_name": doc["speaker_name"], "speaker_role": doc["speaker_role"],
                             "event_name": doc["event_name"], "event_host": doc["event_host"], "audience": doc["audience"],
                             "retrieved_at": doc["retrieved_at"], "sha256": doc["sha256"], "record_path": doc["path"],
                             "model": doc["model"], "cassette": doc["cassette"], "source_authority": doc["source_authority"]}),
                      lit(issued), *event_columns(None, issued, tier, provider)])
        for number, event in enumerate(doc["events"], start=1):
            target = uid("brainitem", f"{claim_key}:{number}")
            org = next((org_ids[o] for o in event["organizations"] if o in org_ids), None)
            items.append([lit(target), lit(AGENCY_NAVY), lit(REMARKS_SECTION.get(event["event_type"], "mission_priorities")), lit("narrative"),
                          lit(f"{claim_key}:{number}"), lit(f"{event['event_type']}: {event['statement'][:160]}"[:200].strip()),
                          lit(event["statement"] + (f" Capability: {event['capability']}." if event["capability"] else "")
                              + (f" Program: {event['program']}." if event["program"] else "") + (f" Person: {event['person']}." if event["person"] else "")),
                          jsonb({"url": doc["url"], "document": claim_key, "event": number, "sha256": doc["sha256"], "record_path": doc["path"]}),
                          lit(issued),
                          *event_columns(event["event_type"], issued, tier, provider, org,
                                         {k: event.get(k) for k in ("capability", "program", "organization", "person", "amounts", "confidence",
                                                                    "organizations", "organizations_from", "source_authority")}
                                         | {"speaker_name": doc["speaker_name"], "event_name": doc["event_name"], "event_host": doc["event_host"],
                                            "date_basis": doc["date_basis"]})])
            evidence.append([lit(uid("evidence", f"{claim_key}:{number}")), lit(target), lit(event["evidence_span"][:600]),
                             lit(doc["url"]), lit(issued), lit("remarks"), lit(f"{doc['url']}:{number}")])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence",
           ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


HIRING_RECORDS = EVENTS_DIR / "hiring_observations.json"
HIRING_PROVIDER = "usajobs_historic_joa"  # the registry row every announcement is filed under (jobs.PROVIDER)


def emit_hiring(out: list[str], org_ids: dict[str, str]) -> None:
    """Every modelled job announcement that loads as one brain item in the people section, dated the day it opened,
    with a piece of evidence per claim: the listing itself (the title, with the listing's fields in the item's data)
    and each duties sentence that placed the position, named a program or a contract, or stated a new organization.

    A posting is an observation about hiring, so it loads as what the agency announced and nothing more: no
    relationship, no office assertion, no lifecycle, no person. The office it is filed under is the one the listing's
    own words name where the memory knows it, else the command the profile maps the USAJobs code to. A claim that
    conflicts with the memory loads marked as a conflict; the reading (intent, action or withdrawn) travels in data.
    A posting outside the acquisition workforce that names nothing the memory holds stays in the record and loads
    nothing (research/docs/21_job_postings_as_a_signal.md)."""
    if not HIRING_RECORDS.exists():
        note_skip("hiring observations not built; run research/tools/jobs.py build")
        return
    items, evidence = [], []
    for posting in json.loads(HIRING_RECORDS.read_text(encoding="utf-8"))["postings"]:
        if not posting["loads"]:
            note_skip("job announcement outside the acquisition workforce naming nothing the memory holds")
            continue
        if not posting["opened"]:
            note_skip("job announcement states no opening date")
            continue
        claim_key = posting["id"]
        item_id = uid("brainitem", claim_key)
        org = next((org_ids[o] for o in posting["links"]["offices"] if o in org_ids), org_ids.get(posting["hiring"]["org"]))
        who = posting["hiring"]["subelement"] or posting["hiring"]["agency"] or posting["hiring"]["department"]
        body = (f"{who}; series {', '.join(posting['series']) or 'not stated'}" + (f" ({posting['career_field']})" if posting["career_field"] else "")
                + f"; {posting['grade']['pay_scale']} {posting['grade']['minimum']}" + (f"-{posting['grade']['maximum']}" if posting["grade"]["maximum"] != posting["grade"]["minimum"] else "")
                + f"; {posting['openings'] or 'an unstated number of'} opening(s); {', '.join(posting['locations']) or 'site not stated'}; "
                f"opened {posting['opened']}, closes {posting['closes'] or 'not stated'}; listed as \"{posting['status_as_listed'] or 'not stated'}\" on {posting['listed_at'][:10]}: "
                f"{posting['reading']['note']}; reads {posting['relation']} against the stored record; {len(posting['claims'])} claim(s)"
                + (f"; to verify: {'; '.join(posting['verify'])}" if posting["verify"] else ""))
        items.append([lit(item_id), lit(AGENCY_NAVY), lit("people"), lit("narrative"), lit(claim_key),
                      lit(f"vacancy: {posting['title']}"[:200].strip()), lit(body),
                      jsonb({"url": posting["url"], "control_number": posting["control_number"], "announcement_number": posting["announcement_number"],
                             "publisher": "USAJobs", "source_type": posting["source_type"], "reliability": posting["reliability"],
                             "agency_code": posting["hiring"]["agency_code"], "agency": posting["hiring"]["agency"], "subelement": posting["hiring"]["subelement"],
                             "retrieved_at": posting["record"]["retrieved_at"], "sha256": posting["record"]["sha256"], "record_path": posting["record"]["path"],
                             "announcement_sha256": (posting["announcement"] or {}).get("sha256", ""), "announcement_path": (posting["announcement"] or {}).get("path", ""),
                             "relation": posting["relation"], "offices": posting["links"]["offices"], "requirements": posting["links"]["requirements"],
                             "awards": posting["links"]["awards"], "solicitations": posting["links"]["solicitations"]}),
                      lit(posting["opened"]),
                      *event_columns("vacancy_posted", posting["opened"], "official", HIRING_PROVIDER, org,
                                     {"series": posting["series"], "career_field": posting["career_field"], "acquisition_workforce": posting["acquisition_workforce"],
                                      "grade": posting["grade"], "openings": posting["openings"], "locations": posting["locations"], "closes": posting["closes"],
                                      "status_as_listed": posting["status_as_listed"], "listed_at": posting["listed_at"], "reading": posting["reading"],
                                      "hiring_org": posting["hiring"]["org"], "claims": [c["statement_type"] for c in posting["claims"]]})])
        for number, claim in enumerate(posting["claims"], start=1):
            evidence.append([lit(uid("evidence", f"{claim_key}:{number}")), lit(item_id), lit(claim["passage"][:600]),
                             lit(posting["url"]), lit(posting["opened"]), lit("hiring"),
                             lit(f"{claim_key}:{number}:{claim['statement_type']}:{claim['relation']}")])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence",
           ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


VENDOR_HIRING_RECORDS = EVENTS_DIR / "vendor_hiring_observations.json"
VENDOR_HIRING_PROVIDER = "vendor_jobs_routergrowth"  # the registry row every vendor posting is filed under (vendor_jobs.PROVIDER)


def emit_vendor_hiring(out: list[str], org_ids: dict[str, str]) -> None:
    """Every modelled contractor posting that loads as one brain item in the vendors and incumbents section, dated the
    day the page states it was posted, with a piece of evidence per claim: the page's title and each sentence that
    named an office, a program, a contract or a solicitation the record holds.

    A vendor posting is a company's statement about the work it wants to staff, so it loads at the editorial tier
    (the company's words, not the government's) under the office its own words name, never under the vendor, and
    asserts nothing: no relationship, no office, no lifecycle, no person. A posting that names nothing the record
    holds stays in the record and loads nothing; one with no date the page or the router states loads nothing either
    (research/docs/21_job_postings_as_a_signal.md, "Vendor postings")."""
    if not VENDOR_HIRING_RECORDS.exists():
        note_skip("vendor hiring observations not built; run research/tools/vendor_jobs.py build")
        return
    items, evidence = [], []
    for posting in json.loads(VENDOR_HIRING_RECORDS.read_text(encoding="utf-8"))["postings"]:
        if not posting["loads"]:
            note_skip("vendor posting naming nothing the record holds")
            continue
        if not posting["posted"]["date"]:
            note_skip("vendor posting with no posting date stated by the page or the router")
            continue
        org = next((org_ids[o] for o in posting["links"]["offices"] if o in org_ids), None)
        if not org:
            note_skip("vendor posting whose named office has no organization row")
            continue
        claim_key = posting["id"]
        item_id = uid("brainitem", claim_key)
        company = posting["company"]
        body = (f"{company['display']}" + (f" (UEI {company['uei']})" if company["uei"] else "")
                + (f", {company['live']} live award(s) at the swept offices" if company.get("live") else ", no live award at the swept offices")
                + f"; {posting['page']['host_class']} page on {posting['page']['host']}"
                + (f"; {posting['location']}" if posting["location"] else "")
                + f"; posted {posting['posted']['date']} (from the {posting['posted']['basis']}); reads {posting['relation']} against the stored record; "
                f"{len(posting['claims'])} claim(s)" + (f"; to verify: {'; '.join(posting['verify'])}" if posting["verify"] else ""))
        items.append([lit(item_id), lit(AGENCY_NAVY), lit("vendors_incumbents"), lit("narrative"), lit(claim_key),
                      lit(f"vendor vacancy: {company['display']}: {posting['title']}"[:200].strip()), lit(body),
                      jsonb({"url": posting["url"], "publisher": company["display"], "company": company, "source_type": posting["source_type"],
                             "reliability": posting["reliability"], "host": posting["page"]["host"], "host_class": posting["page"]["host_class"],
                             "retrieved_at": posting["page"]["retrieved_at"], "sha256": posting["page"]["sha256"], "record_path": posting["page"]["path"],
                             "search_sha256": posting["search"]["sha256"], "search_path": posting["search"]["path"], "search_query": posting["search"]["query"],
                             "relation": posting["relation"], "offices": posting["links"]["offices"], "requirements": posting["links"]["requirements"],
                             "awards": posting["links"]["awards"], "solicitations": posting["links"]["solicitations"]}),
                      lit(posting["posted"]["date"]),
                      *event_columns("vendor_vacancy_posted", posting["posted"]["date"], "editorial", VENDOR_HIRING_PROVIDER, org,
                                     {"company_uei": company["uei"], "company": company["display"], "live_awards": company.get("live"),
                                      "location": posting["location"], "posted_basis": posting["posted"]["basis"], "host_class": posting["page"]["host_class"],
                                      "claims": [c["statement_type"] for c in posting["claims"]]})])
        for number, claim in enumerate(posting["claims"], start=1):
            evidence.append([lit(uid("evidence", f"{claim_key}:{number}")), lit(item_id), lit(claim["passage"][:600]),
                             lit(posting["url"]), lit(posting["posted"]["date"]), lit("vendor_hiring"),
                             lit(f"{claim_key}:{number}:{claim['statement_type']}:{claim['relation']}")])
    insert("public.agency_brain_items", ITEM_COLUMNS, items, out)
    insert("public.gov_intelligence_evidence",
           ["id", "brain_item_id", "excerpt", "source_url", "published_at", "provider", "source_key"], evidence, out)


def main() -> int:
    seed = json.loads(SEED.read_text())
    out: list[str] = ["-- generated by research/tools/agency_layers_sql.py; do not edit by hand",
                      "begin;",
                      "set constraints all deferred;", ""]
    emit_agencies(out)
    org_ids = emit_offices(seed, out)
    hosts = emit_sources(out)
    emit_people(seed, org_ids, out, hosts)
    emit_contacts(org_ids, out)
    emit_relationships(seed, org_ids, out)
    emit_seed_evidence(seed, out, hosts)
    revisions = emit_lrae(org_ids, out, uic_index(seed), office_index(seed))
    emit_notices(org_ids, out, revisions)
    emit_news(out, hosts, org_ids)
    emit_oversight(out, hosts, org_ids)
    emit_remarks(out, org_ids)
    emit_hiring(out, org_ids)
    emit_vendor_hiring(out, org_ids)
    emit_budget(out, org_ids, office_index(seed), bureau_nodes(seed["nodes"]))
    emit_programs(out, org_ids)
    emit_program_money(out, org_ids)
    emit_stakeholders(out, org_ids, hosts)
    emit_award_changes(out, org_ids, uic_index(seed), office_index(seed))
    emit_records(out, org_ids, uic_index(seed), office_index(seed))
    out += ["commit;"]
    sys.stdout.write("\n".join(out) + "\n")
    for reason, count in sorted(skipped.items()):
        print(f"skipped: {reason} x{count}", file=sys.stderr)
    return 0


def selfcheck() -> int:
    rows = [["'b'", *[None] * 8, "'a'"], ["'c'", *[None] * 8, "'b'"], ["'a'", *[None] * 8, "null"]]
    assert [r[0] for r in after_priors(rows)] == ["'a'", "'b'", "'c'"], "a superseded row is inserted first"
    assert funding_lineage("K", "FY28", "q1 ") == funding_lineage("K", "2028", "Q1") == "funding:K:2028:Q1"
    assert activity_of("lrae_navwar_2025-06") == "navwar" and activity_of("amc_2026-05") == "amc"
    assert funding_lineage("K", "TBD", "Q1") == funding_lineage("K", "", "") == "funding:K:unstated:unstated"
    ix, ux = {"PMW160": "pmw:160"}, {"N00039": "contracting:n00039", "N66001": "center:niwc-pacific"}
    aw = lambda desc, sol="", fund="": {"description": desc, "solicitation": sol, "funding_office": fund, "contracting_office": "N00039"}
    assert award_office(aw("SERVICES FOR PMW-160"), ix, ux, {}) == ("pmw:160", "the office code in the description")
    assert award_office(aw("SERVICES", "N00039-20-R-0011"), ix, ux, {"N0003920R0011": ["pmw:150"]})[0] == "pmw:150"
    assert award_office(aw("SERVICES", "N0003920R0011"), ix, ux, {"N0003920R0011": ["pmw:150", "pmw:160"]}) == ("contracting:n00039", "the contracting office")
    assert award_office(aw("SERVICES", "", "N66001"), ix, ux, {}) == ("center:niwc-pacific", "the funding office")
    assert award_office(aw("SERVICES", "", ""), ix, ux, {}) == ("contracting:n00039", "the contracting office")
    base = {"piid": "P1", "completion": "2026-01-31"}
    act = lambda mod, signed, end="", reason="", piid="P1": {"piid": piid, "mod": mod, "signed": signed, "completion": end, "reason": reason}
    changes = award_changes(base, [act("P00003", "2025-12-01", "2026-06-30", "G"), act("P00001", "2025-03-01", "2026-01-31", "C"),
                                   act("0", "2024-01-31", "2026-01-31"), act("P00002", "2025-06-01", "", "G"), act("P00004", "2026-02-01", "2026-03-31", "F"),
                                   act("P00009", "2025-07-01", "2027-01-01", "G", piid="P2")])
    assert [(a["mod"], event) for a, event, _ in changes] == [("P00002", "contract_modified"), ("P00003", "contract_extended"), ("P00004", "contract_modified")], \
        "funding-only and other contracts' actions drop; an option that moves the end is an extension; an earlier end after it is not"
    assert changes[1][2] == "extended, ends 2026-06-30 (was 2026-01-31)" and changes[2][2] == "was terminated for convenience"
    row = {"title": "Protest B-1 on N0003925R0001", "excerpt": "", "uic": "N00039", "data": {"solicitation": "N00039-25-R-0001"}}
    assert record_office(row, ux, ix, {"N0003925R0001": ["pmw:150"]}) == "pmw:150", "the notice under the solicitation places it first"
    assert record_office(row, ux, ix, {}) == "contracting:n00039" and record_office(dict(row, uic=""), ux, ix, {}) == "agency:don"
    assert record_office(dict(row, uic="", excerpt="directs PMW 160 to report"), ux, ix, {}) == "pmw:160"
    assert lit("O'Brien") == "'O''Brien'"
    assert lit(None) == "null" and lit("") == "null"
    assert arr([]) == "'{}'::text[]" and arr(["a", "", "b'c"]) == "array['a','b''c']::text[]"
    assert norm("  PMW   160 ") == "pmw 160"

    assert fiscal_year("FY26") == 2026 and fiscal_year("2026") == 2026 and fiscal_year("") is None and fiscal_year("TBD") is None
    # FY26 Q1 is the last quarter of calendar 2025; backwards would date every
    # first-quarter forecast a year late.
    assert quarter_bounds(2026, "Q1") == ("2025-10-01", "2025-12-31")
    assert quarter_bounds(2026, "Q4") == ("2026-07-01", "2026-09-30")
    assert quarter_bounds(None, "Q1") == (None, None) and quarter_bounds(2026, "") == (None, None)

    assert role_type("Deputy Program Manager") == "deputy_program_manager"
    assert role_type("Program Executive Officer (acting from 2023-05)") == "acquisition_leader"
    assert role_type(None) == "other"

    assert sha({"b": 1, "a": 2}) == sha({"a": 2, "b": 1}), "hash must not depend on key order"
    assert uid("org", "pmw:160") == uid("org", "pmw:160") != uid("need", "pmw:160")
    codes = office_index({"nodes": [{"id": "pmw:160", "codes": {"office_code": "PMW 160"}}, {"id": "pmw:101", "codes": {"office_code": "PMA/PMW 101"}},
                                    {"id": "pmw:170", "codes": {"office_code": "PMW/A 170"}}, {"id": "x", "codes": {}}]})
    assert codes["PMW160"] == "pmw:160" and codes["PMA101"] == codes["PMW101"] == "pmw:101" and codes["PMW170"] == "pmw:170"
    for text in ("ESS FOR PMW-160", "PMW160 SUPPORT", "PMW/A 170 GPS", "SUPPORT TO PMW 160."):
        assert office_of(text, codes) in {"pmw:160", "pmw:170"}, text
    assert OFFICE_CODE_RE.search("N0003925R4011 PMW 16") is None, "two digits are not an office code"
    darpa_re = re.compile(PROFILES["darpa"]["office_key_re"])
    darpa = office_index({"nodes": [{"id": "office:sto", "codes": {"office_code": "STO"}}, {"id": "office:cso", "codes": {"office_code": "CSO"}}]}, darpa_re)
    assert office_of("DARPA STO ALBATROSS PROGRAM", darpa, darpa_re) == office_of("OFFICE (STO3) TECHNICAL", darpa, darpa_re) == "office:sto"
    assert office_of("CSO PHASE 1", darpa, darpa_re) is None and office_of("STORAGE", darpa, darpa_re) is None

    def rel(ident, kind, src, dst, state="last_confirmed", review="draft", to=None):
        return {"id": ident, "type": kind, "from": src, "to": dst, "review_status": review,
                "current_status": {"state": state}, "effective_from": None, "effective_to": to,
                "effective_dates_status": "documented" if to else "unknown", "evidence_class": "directly_documented"}

    seed = {"nodes": [{"id": f"o:{n}", "type": "program_office", "name": f"Office {n}",
                       "aliases": [], "codes": {}, "observation_ids": ["ob:1"] if n == 0 else []} for n in range(5)],
            "observations": [{"id": "ob:1", "source_url": "https://example.mil/page", "observed_at": "2025-01-02"}],
            "relationships": [rel("r1", "child_of", "o:0", "o:1"),
                              rel("r2", "child_of", "o:2", "o:0"),
                              rel("r3", "child_of", "o:2", "o:1"),
                              rel("r4", "contracts_for", "o:3", "o:1"),
                              rel("r5", "consolidated_into", "o:3", "o:0"),
                              rel("r6", "child_of", "o:3", "o:1", state="ended"),
                              rel("r7", "child_of", "o:3", "o:2", review="retracted"),
                              # o:4 has two live parents that are not on one chain: a real conflict.
                              rel("r11", "child_of", "o:4", "o:1"),
                              rel("r12", "child_of", "o:4", "o:3"),
                              # o:0 sits under o:1 now and sat under o:2 until 2020-05-13.
                              # This is the NEN case: PEO EIS then PEO Digital.
                              rel("r10", "child_of", "o:0", "o:2", state="ended", to="2020-05-13")]}
    lines: list[str] = []
    ids = emit_offices(seed, lines)
    office_rows = "\n".join(lines)
    assert "source_url, observed_at)" in office_rows.split("values")[0] and office_rows.count("'https://example.mil/page'") == 1, \
        "the first cited observation's URL is the office row's source_url; a node without one gets null"
    assert first_url(["ob:9", "ob:1"], observation_urls(seed)) == "https://example.mil/page" and first_url([], {}) is None
    assert office_rows.count("'2025-01-02'") == 1, "observed_at is the newest cited observation's date; a node without one gets null"
    assert latest_observed(["ob:1", "ob:9"], observation_dates(seed)) == "2025-01-02" and latest_observed([], {}) is None
    updates = [l for l in lines if l.startswith("update")]
    assert len(updates) == 2 and ids["o:0"] in updates[0] and ids["o:1"] in updates[0]
    assert ids["o:2"] in updates[1] and ids["o:0"] in updates[1], "o:1 is o:0's parent, so o:2's two claims are one chain and the nearer wins"
    assert not any(ids["o:4"] in u for u in updates), "office with two live parents on different chains must stay unparented"

    lines = []
    emit_relationships(seed, ids, lines)
    body = "\n".join(lines)
    assert "'contracting_supports'" in body, "contracts_for must map to contracting_supports"
    # o:3 consolidated into o:0 makes o:0 the successor, so the edge points the other way.
    successor = [l for l in body.splitlines() if "'successor_to'" in l][0]
    assert successor.index(ids["o:0"]) < successor.index(ids["o:3"])
    # r1 is already the parent column; r6 ended with no date; r7 is retracted.
    # r2, r3 and r10 load. r10 is the office's former parent, and an office keeping its
    # current parent in the column must not cost it the parent it used to have.
    # o:0 under o:1, o:2 under o:0 and o:1, o:4's two conflicting claims, and o:0's ended history: every live
    # or ended child_of claim, the parent column's included.
    assert body.count("'functionally_aligned_to'") == 6, body.count("'functionally_aligned_to'")
    former = [l for l in body.splitlines() if "'r10'" in l]
    assert len(former) == 1 and "'2020-05-13'" in former[0], former
    assert former[0].index(ids["o:0"]) < former[0].index(ids["o:2"]), "the child is the source of a child_of edge"
    # A day the memory inferred is no day a source states: an inferred start stays empty, and a claim
    # superseded on an inferred day would read as current, so it is left out.
    guessed = {**rel("r13", "child_of", "o:3", "o:0"), "effective_from": "2023-05-01", "effective_dates_status": "inferred"}
    gone = {**rel("r14", "child_of", "o:4", "o:0", state="superseded", to="2026-05-10"), "effective_dates_status": "inferred"}
    lines = []
    emit_relationships({**seed, "relationships": [guessed, gone]}, ids, lines)
    body = "\n".join(lines)
    assert "'r13'" in body and "2023-05-01" not in body and "'r14'" not in body, body

    # A leadership claim a source superseded without a date must not load as a
    # current position beside the person who replaced them.
    seed["nodes"].append({"id": "person:a", "type": "person", "name": "A", "aliases": [], "codes": {}})
    seed["nodes"].append({"id": "person:b", "type": "person", "name": "B", "aliases": [], "codes": {}})
    seed["relationships"][0]["observation_ids"] = ["ob:1"]
    edge_lines: list[str] = []
    emit_relationships(seed, ids, edge_lines)
    edges = "\n".join(edge_lines)
    assert edges.split("values")[0].rstrip().endswith("source_url, observed_at)") and edges.count("'https://example.mil/page'") == 1, \
        "r1 is the parent column and a row too; its URL rides on that row"
    seed["relationships"][1]["observation_ids"] = ["ob:1"]  # r2, o:2 under o:0: the parent column and a row
    edge_lines = []
    emit_relationships(seed, ids, edge_lines)
    r2 = [l for l in "\n".join(edge_lines).splitlines() if "'r2'" in l][0]
    assert r2.rstrip(",").endswith("'https://example.mil/page', '2025-01-02')"), "an edge cites its observation's URL and date"
    seed["relationships"] += [
        {**rel("r8", "leads", "person:a", "o:0", state="superseded"), "role_as_written": "Program Manager", "observation_ids": ["ob:1"]},
        {**rel("r9", "leads", "person:b", "o:0"), "role_as_written": "Program Manager", "observation_ids": ["ob:1"]},
        {**rel("r11", "leads", "person:b", "o:1"), "role_as_written": "Deputy Program Manager"}]
    lines = []
    m_ids = dict(ids)
    m_ids.pop("person:a", None)
    emit_people(seed, m_ids, lines, {})
    body = "\n".join(lines)
    assert body.count("'Program Manager'") == 1, "superseded leader must not load as current"
    # observed_at defaults to the load's own time, so a claim no dated observation states is left out.
    assert "'2025-01-02'" in [l for l in body.splitlines() if "'Program Manager'" in l][0]
    assert "'Deputy Program Manager'" not in body, "an undated leadership claim must not read as observed at load time"
    # A position carries the page its observed date is read from, and only a start a source states.
    seed["observations"].append({"id": "ob:2", "source_url": "https://example.mil/newer", "observed_at": "2026-04-12"})
    seed["relationships"][-2].update(observation_ids=["ob:1", "ob:2"], effective_from="2023-05-01", effective_dates_status="inferred")
    lines = []
    emit_people(seed, m_ids, lines, {})
    body = "\n".join(lines)
    assert "public.gov_contact_positions (id, contact_id, organization_id, role_type, raw_title, valid_from, valid_to, " \
           "observed_at, source, source_ref, source_url) values" in body, "positions name their source_url column"
    current = [l for l in body.splitlines() if "'Program Manager'" in l][0]
    assert "'https://example.mil/newer'" in current and "'2026-04-12'" in current and "2023-05-01" not in current, current
    assert newest_url(["ob:2", "ob:1"], observation_urls(seed), observation_dates(seed)) == "https://example.mil/newer"
    assert newest_url(["ob:9"], {}, {}) is None
    # A memory person's contact is last seen on the newest day a source names them, a superseded claim included;
    # a person no dated source names is left out rather than seen at load time.
    seed["nodes"].append({"id": "person:z", "type": "person", "name": "Z", "aliases": [], "codes": {}})
    lines = []
    emit_people(seed, m_ids, lines, {})
    contact = {p: [l for l in "\n".join(lines).splitlines() if f"'{MEMORY_NS}:{p}'" in l] for p in ("person:a", "person:b", "person:z")}
    assert contact["person:b"][0].rstrip(",").endswith("'https://example.mil/newer', '2026-04-12')"), contact
    assert contact["person:a"][0].rstrip(",").endswith("'https://example.mil/page', '2025-01-02')"), contact
    assert not contact["person:z"], "a person no dated source names must not read as seen at load time"

    # A dated departure and a dated arrival at one office on one day are one leadership change, naming
    # both; an undated claim is none. The FPDS end is the latest completion date any action stated.
    people = {"nodes": [{"id": "o:0", "type": "program_office", "name": "Office 0"}, {"id": "p:a", "type": "person", "name": "A"},
                        {"id": "p:b", "type": "person", "name": "B"}, {"id": "p:c", "type": "person", "name": "C"}],
              "observations": [{"id": "ob:1", "source_url": "https://x.mil/leaders"}],
              "relationships": [{**rel("l1", "leads", "p:a", "o:0", state="ended", to="2025-08-19"), "role_as_written": "Program Manager", "observation_ids": ["ob:1"]},
                                {**rel("l2", "leads", "p:b", "o:0"), "effective_from": "2025-08-19", "effective_dates_status": "documented",
                                 "role_as_written": "Program Manager", "observation_ids": ["ob:1"]},
                                {**rel("l3", "leads", "p:c", "o:0"), "role_as_written": "Deputy"},
                                {**rel("l4", "leads", "p:c", "o:0"), "effective_from": "2023-05-01", "effective_dates_status": "inferred",
                                 "role_as_written": "Program Executive Officer (acting from 2023-05)", "observation_ids": ["ob:1"]}]}
    events = leadership_events(people, {"o:0": "org-0"}, {"x.mil": "x_site"})
    assert len(events) == 1 and events[0][9] == "'leadership_change'" and events[0][12] == "'x_site'", events
    assert "A ended as Program Manager; B began as Program Manager" in events[0][6], "an inferred day is no event"
    end = latest_end([{"completion": "2026-09-30", "signed": "2024-01-05"}, {"completion": "2027-09-30", "signed": "2025-03-01"},
                      {"completion": "", "signed": "2025-06-01"}])
    assert (end["completion"], end["signed"]) == ("2027-09-30", "2025-03-01")
    assert latest_end([{"completion": "", "signed": "2025-06-01"}]) is None

    # "> $1B+" states a floor and no ceiling. Reading the floor as the amount
    # publishes "exactly $1B" for a buy the source only said exceeds it.
    # A release that re-states the same office chains; one that names a different office
    # branches, so the older observation is not overwritten by the newer office.
    assert chain_or_branch(("office:nen", "assert:1"), "office:nen") == "assert:1"
    assert chain_or_branch(("office:nen", "assert:1"), "office:other") is None
    assert chain_or_branch(None, "office:nen") is None

    assert loadable_value("1000000000", "") is False
    assert loadable_value("", "") is False
    assert loadable_value("100000000", "250000000") is True

    assert confidence_for("directly_documented") == "1.0"
    assert confidence_for("inferred") == "0.5" and float(DERIVED) < 0.8, \
        "a derived edge must stay under the resolver's hierarchy threshold"

    # A notice-originated need is keyed on the solicitation number, whatever its punctuation, else the notice id;
    # every SAM.gov notice type the tool knows maps to a lifecycle the schema accepts.
    assert notice_group_key({"solicitation": "N00039-26-R-E017", "id": "abc"}) == "notice:N0003926RE017"
    assert notice_group_key({"solicitation": "", "id": "abc"}) == "notice:abc"
    assert set(NOTICE_LIFECYCLE.values()) <= {"identified", "planned", "in_procurement", "fulfilled", "cancelled", "unknown"}
    # an award of 2024 does not fulfil a line the June 2025 release still forecasts; award notices of June 2026 do
    releases = [{"release_date": "2023-06-20"}, {"release_date": "2025-06-19"}]
    assert not outdates_forecast("2024-05-21", releases) and outdates_forecast("2026-06-10", releases) and outdates_forecast("2026-06-10", [])

    # Every event a map can write is one the schema names, and the maps say what the schema accepts.
    assert len(EVENT_TYPES) == 29  # the canonical 24, plus presolicitation_posted, justification_posted, budget_line, vacancy_posted and vendor_vacancy_posted
    assert (set(NOTICE_EVENT.values()) | set(SPECIAL_EVENT.values()) | set(NEWS_EVENT.values())
            | {"forecast_created", "forecast_changed"}) <= EVENT_TYPES
    assert set(ACCESS_MODE.values()) <= {"public_api", "public_feed", "public_web", "download", "manual", "authenticated"}
    assert set(VERIFICATION.values()) <= {"unverified", "verified", "degraded", "blocked", "retired"}
    assert set(NEWS_TIER.values()) <= {"official", "licensed_secondary", "derived", "editorial"}
    assert notice_event("sources sought", "sources sought") == "rfi_released"
    assert notice_event("special notice", "industry day (special notice)") == "industry_engagement"
    assert notice_event("special notice", "intent to award a sole source (special notice)") == "justification_posted"
    assert notice_event("special notice", "request for information (special notice)") == "rfi_released"
    assert notice_event("special notice", "intent to award without full competition (special notice)") == "justification_posted"
    assert notice_event("special notice", "special notice") is None and notice_event("j", "j") is None

    # A line restated as it was is no event; a moved award quarter is, and it reads as the same
    # date Alert C reads off the revision; a TBD year moving quarters states no date either time.
    line = {"procurement_method": "Full and Open Competition", "contract_type": "FFP", "instrument": "Contract",
            "solicitation_fy": "FY25", "solicitation_quarter": "Q1", "award_fy": "FY25", "award_quarter": "Q3",
            "as_stated": "$1M - $5M"}
    assert forecast_change(line, dict(line)) == {}
    assert forecast_change(line, {**line, "award_quarter": "Q4"}) == \
        {"award_quarter": ["Q3", "Q4"], "award_window": ["2025-04-01", "2025-07-01"]}
    assert forecast_change(line, {**line, "as_stated": "$5M - $10M"}) == {"as_stated": ["$1M - $5M", "$5M - $10M"]}
    assert "award_window" not in forecast_change({**line, "award_fy": "TBD"}, {**line, "award_fy": "TBD", "award_quarter": "Q4"})

    # A registry row: the browser reads a host that refuses fetches, a login wall is blocked and
    # authenticated, "none" is no access gap, and the words the registry used travel in metadata.
    entry = {"source_key": "x_site", "provider_name": "X site", "responsible_org": "X", "official_url": "https://x.mil/",
             "access_mode": "webpage", "verification_status": "verified", "access_restrictions": "Akamai 403; reachable through Browserbase",
             "inspected_example": {"url": "https://x.mil/a", "method": "browserbase"}, "answers": [], "cannot_answer": [],
             "lifecycle_stages": ["organization"], "fields_and_identifiers": [], "proposed_monitor_frequency": "weekly",
             "last_verified_at": "2026-09-16", "historical_coverage": "2023 on", "publication_frequency": "irregular",
             "reporting_lag": "none", "extraction_difficulty": "low",
             "instrument": {"status": "Live", "decided_by": "2026-09-28", "status_basis": "answers", "standing": "standing", "collector_kind": "collecting",
                            "cadence": {"kind": "standing"}, "org_scope": [], "close_by": ""}}
    row = source_row(entry)
    assert row[8] == "'browser_extractor'" and row[9] == "'public_web'" and row[13] == "'verified'"
    assert '"instrument":{"cadence":{"kind":"standing"},"close_by":"","collector_kind":"collecting"' in row[16], "the instrument block travels in metadata"
    assert '"instrument":{}' in source_row({k: v for k, v in entry.items() if k != "instrument"})[16], "a row without the block writes an empty one"
    row = source_row({**entry, "access_mode": "manual", "verification_status": "restricted", "access_restrictions": "none",
                      "inspected_example": {}})
    assert row[8] == "'research_agent'" and row[9] == "'authenticated'" and row[13] == "'blocked'" and row[15].startswith("'[]'")
    assert source_row({**entry, "access_mode": "api", "inspected_example": {}, "access_restrictions": "none"})[8] == "'api_connector'"

    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(selfcheck() if "--selfcheck" in sys.argv else main())

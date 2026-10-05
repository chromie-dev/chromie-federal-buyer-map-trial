"""The source registry and the event columns as loaded, read back from a database the pipeline built.

NAVY_DB names that database (pipeline.py sets it for the checks stage); without it these skip.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from agency_layers_sql import EVENT_TYPES, LRAE_PROVIDERS, LRAE_SOURCE, NEED_ROLE, NOTICE_PROVIDER, uic_index  # noqa: E402
from monitor_forecast_revision import REVISIONS_SQL  # noqa: E402

DB = os.environ.get("NAVY_DB")
pytestmark = pytest.mark.skipif(not DB, reason="NAVY_DB names no loaded database")
# The ingestion primitives.
ADAPTERS = {"api_connector", "page_fetcher", "browser_extractor", "search_discovery", "research_agent"}


def rows(sql: str) -> list[list[str]]:
    dsn = (f"postgresql://{os.environ.get('PGUSER', 'postgres')}:{os.environ.get('PGPASSWORD', 'postgres')}"
           f"@{os.environ.get('PGHOST', '127.0.0.1')}:{os.environ.get('PGPORT', '54322')}/{DB}")
    out = subprocess.run(["psql", dsn, "-At", "-F", "\t", "-c", sql], capture_output=True, text=True, check=True)
    return [line.split("\t") for line in out.stdout.splitlines() if line]


def one(sql: str) -> str:
    return rows(sql)[0][0]


def test_registry_loads_every_row_as_a_federal_source() -> None:
    registry = json.loads((ROOT / "research" / "sources" / "source_registry.json").read_text(encoding="utf-8"))
    loaded = rows("select source_key, government_level, adapter_key from public.gov_procurement_sources order by 1")
    assert [r[0] for r in loaded] == sorted(e["source_key"] for e in registry)
    assert {r[1] for r in loaded} == {"federal"}
    assert {r[2] for r in loaded} <= ADAPTERS


def test_every_item_names_a_registered_source_and_a_schema_event() -> None:
    keys = {r[0] for r in rows("select source_key from public.gov_procurement_sources")}
    providers = {r[0] for r in rows("select distinct source_provider from public.agency_brain_items where source_provider is not null")}
    assert providers <= keys, providers - keys
    events = {r[0] for r in rows("select distinct event_type from public.agency_brain_items where event_type is not null")}
    assert events <= EVENT_TYPES, events - EVENT_TYPES
    assert one("select count(*) from public.agency_brain_items where event_type is not null "
               "and (published_at is null or source_tier is null)") == "0"
    for prefix, provider in [(f"{a}-lrae:%", p) for a, p in LRAE_PROVIDERS.items()] + [("sam-notice:%", NOTICE_PROVIDER)]:
        assert one(f"select count(*) from public.agency_brain_items where claim_key like '{prefix}' "
                   f"and source_provider is distinct from '{provider}'") == "0"


def test_each_forecast_line_is_created_once() -> None:
    lines = one(f"select count(*) from public.gov_needs where source = '{LRAE_SOURCE}'")
    releases = ", ".join(f"'{p}'" for p in LRAE_PROVIDERS.values())  # a SAM.gov special notice can publish a forecast too
    assert one(f"select count(*) from public.agency_brain_items where event_type = 'forecast_created' and source_provider in ({releases})") == lines
    assert one("select count(distinct data->>'line') from public.agency_brain_items where event_type = 'forecast_created'") == lines


def test_moved_award_windows_are_the_revisions_alert_c_reports() -> None:
    moved = one("select count(*) from public.agency_brain_items where event_type = 'forecast_changed' "
                "and data->'changed' ? 'award_window'")
    assert moved == one(f"select coalesce(json_array_length(({REVISIONS_SQL})), 0)")


def test_leadership_changes_are_the_dated_boundaries_of_the_memory() -> None:
    from agency_layers_sql import SEED, emit_offices, leadership_events
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    expected = len(leadership_events(seed, emit_offices(seed, []), {}))
    assert expected and one("select count(*) from public.agency_brain_items where claim_key like 'navy-org-memory:leadership:%'") == str(expected)


def test_each_event_claim_in_the_news_is_an_item_and_the_article_is_not() -> None:
    from agency_layers_sql import NEWS_EVENT, NEWS_RECORDS
    articles = json.loads(NEWS_RECORDS.read_text(encoding="utf-8"))["articles"]
    expected = sum(1 for a in articles if a["published"] for c in a["claims"] if c["statement_type"] in NEWS_EVENT)  # an undated page's claims stay evidence
    assert one("select count(*) from public.agency_brain_items where claim_key like 'news:%:%' and event_type is not null") == str(expected)
    assert one("select count(*) from public.agency_brain_items where claim_key like 'news:%' "
               "and claim_key not like 'news:%:%' and event_type is not null") == "0"


def test_each_incumbent_contract_expires_once_on_a_date() -> None:
    ends = rows("select data->>'piid', data->>'expires_on' from public.agency_brain_items where event_type = 'contract_expires'")
    assert ends and len({piid for piid, _ in ends}) == len(ends)
    assert all(len(day) == 10 and day[4] == day[7] == "-" for _, day in ends), ends


def test_each_need_binds_to_the_offices_its_sheet_names() -> None:
    """One requirement-owner row per forecast line whose office the memory resolves, one contracting row
    per line whose contracting UIC is a node; the memory decides, the loader only follows."""
    seed = json.loads((ROOT / "research" / "memory" / "organization_seed.json").read_text(encoding="utf-8"))
    node_ids, uics = {n["id"] for n in seed["nodes"]}, uic_index(seed)
    owners = contracting = 0
    for pack in sorted((ROOT / "datapack").glob("lrae_*")):
        with (pack / "layers" / "needs.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                owners += bool(row["office_id"])
                uic = row["contracting_office_uic"]
                contracting += uics.get(uic.upper(), f"contracting:{uic.lower()}") in node_ids
    assert owners > 0 and contracting > 0
    for role, expected in (("requirement_owner", owners), ("contracting", contracting)):
        assert int(one(f"select count(*) from public.gov_need_organizations o join public.gov_needs n on n.id = o.need_id "
                       f"where n.source = '{LRAE_SOURCE}' and o.role = '{NEED_ROLE[role]}'")) == expected, role
    # The generated memory's types load under the production names.
    assert int(one("select count(*) from public.gov_organizations where org_type in ('department', 'field_activity')")) > 0


def test_every_organization_and_edge_carries_the_provenance_the_resolver_requires() -> None:
    """The program-office resolver skips a row without source, source_ref, observed_at and confidence.
    Every office and edge that cites a dated observation carries all four."""
    assert one("select count(*) from gov_organizations where source_ref is not null and observed_at is null "
               "and id in (select source_organization_id from gov_organization_relationships)") == "0"
    assert one("select count(*) from gov_organization_relationships where source is null or source_ref is null "
               "or observed_at is null or confidence is null") == "0"
    assert int(one("select count(*) from gov_organizations where observed_at is not null")) > 150


def test_each_oversight_finding_is_a_dated_official_event_with_its_passage() -> None:
    """A finding the agent kept loads as an event on the report's issue date, from a registered oversight
    source, with exactly one evidence row carrying the passage; the report item itself is not an event."""
    found = rows("""
        select i.event_type is not null, i.published_at is not null, i.source_tier, s.source_key,
               (select count(*) from gov_intelligence_evidence e where e.brain_item_id = i.id)
        from agency_brain_items i
        left join gov_procurement_sources s on s.source_key = i.source_provider
        where i.claim_key like 'oversight:%'""")
    assert found, "no oversight items loaded"
    reports = [r for r in found if r[0] == "f"]
    findings = [r for r in found if r[0] == "t"]
    assert reports and findings
    assert all(r[3] in ("gao_reports", "oversight_gov_reports") and r[2] == "official" for r in found)
    assert all(r[1] == "t" and r[4] == "1" for r in findings), [r for r in findings if not (r[1] == "t" and r[4] == "1")][:3]
    assert all(r[4] == "0" for r in reports)


def test_each_remark_event_is_dated_from_a_registered_source_with_its_passage() -> None:
    """A speech, statement or conference event loads on the document's date from its registry row, official
    for the government's own words and editorial for an organizer's page, with one evidence row each."""
    found = rows("""
        select i.event_type is not null, i.published_at is not null, i.source_tier, s.source_key,
               (select count(*) from gov_intelligence_evidence e where e.brain_item_id = i.id), i.data->>'kind'
        from agency_brain_items i
        left join gov_procurement_sources s on s.source_key = i.source_provider
        where i.claim_key like 'remarks:%'""")
    assert found, "no remarks items loaded"
    findings = [r for r in found if r[0] == "t"]
    assert findings and [r for r in found if r[0] == "f"]
    assert all(r[3] in ("navy_mil_speeches", "house_committee_repository", "conference_pages_exa") for r in found)
    assert all(r[2] in ("official", "editorial") for r in found)
    assert all(r[1] == "t" and r[4] == "1" for r in findings), [r for r in findings if not (r[1] == "t" and r[4] == "1")][:3]


def test_swept_incumbents_cover_the_office_book() -> None:
    """The incumbent family is the contracting office's whole book of base awards, not only the contracts a
    forecast row names: thousands of dated contract_expires events, each carrying the description FPDS
    states, an end date in its title, the page that carries it, and one item per contract."""
    swept = rows("select title, data->>'description', source->>'sha256', published_at::date, claim_key from public.agency_brain_items "
                 "where source_provider = 'fpds_atom_feed' and event_type = 'contract_expires' and data ? 'description'")
    assert len(swept) > 1000, len(swept)
    assert all(re.search(r"ends \d{4}-\d{2}-\d{2}: ", r[0]) and r[1] and r[2] and r[3] for r in swept)
    assert one("select count(*) - count(distinct claim_key) from public.agency_brain_items where source_provider = 'fpds_atom_feed'") == "0"


def test_people_load_as_contacts_with_dated_positions() -> None:
    """Every loaded position names an organization, a date and the document."""
    assert int(one("select count(*) from public.gov_contact_positions where source_ref is not null and observed_at is not null")) >= 500
    assert one("select count(*) from public.gov_contact_positions p left join public.gov_organizations o on o.id=p.organization_id where o.id is null") == "0"
    assert one("select count(*) from public.gov_contact_positions p left join public.gov_contacts c on c.id=p.contact_id where c.id is null") == "0"
    assert int(one("select count(distinct contact_id) from public.gov_contact_positions where source='sam_gov_site_api'")) >= 40
    assert one("select count(*) from (select email from public.gov_contacts where email is not null group by email having count(*) > 1) d") == "0"
    assert int(one("select count(*) from public.gov_contact_positions p join public.gov_organizations o on o.id=p.organization_id "
                   "where o.acronym='PMA/PMW 101'")) >= 5

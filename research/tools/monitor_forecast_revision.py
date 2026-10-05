#!/usr/bin/env python3
"""Alert C from the monitor design: an LRAE line whose forecast moved between releases.

    python research/tools/monitor_forecast_revision.py
    python research/tools/monitor_forecast_revision.py --json
    python research/tools/monitor_forecast_revision.py --selfcheck

Reads the loaded agency-intelligence tables and reports two things: every requirement
whose current revision supersedes an earlier one with a different anticipated award
window, and every funding estimate superseded by a different value for the same
fiscal period. Output follows the
format section 9 of `research/docs/06_continuous_monitor_design.md` sets: what changed, the office
and its ancestry, the evidence, the uncertainty, and why it matters.

Offline: one `psql` read, no network. Detection is a query rather than a diff of
files because the supersession chain already records which revision replaced which;
a row that never moved produces nothing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

# The ancestry walk climbs parent_organization_id, then adds any successor that a
# source documents for an office on that path. The reorganisation is a successor
# edge, not a parent, so a walk that only climbs parents would report the old chain
# as though nothing had happened. The successor is reported for the ancestor it is
# documented against (PEO C4I -> PAE Mission Systems), never pushed down onto the
# office: the release scoped the move to "mission systems elements" and itemized no
# offices, so an office's own placement is established only by its own source.
# Only a forecast release states an award window; a notice chained after it restates the requirement and says
# nothing about the window, so its null is not a move. The alert compares forecast statements with each other.
FORECAST_SOURCE = "chromie-federal-buyer-map-trial/datapack"

REVISIONS_SQL = f"""
with recursive ancestry as (
  select o.id as office_id, 1 as depth, o.id as ancestor_id, o.name, o.parent_organization_id
    from public.gov_organizations o
  union all
  select a.office_id, a.depth + 1, p.id, p.name, p.parent_organization_id
    from ancestry a
    join public.gov_organizations p on p.id = a.parent_organization_id
),
chain as (
  select office_id, json_agg(name order by depth) as path from ancestry group by office_id
),
succession as (
  select a.office_id,
         json_agg(distinct a.name || ' -> ' || s.name || ' (from ' || coalesce(r.valid_from::text, 'an undated release') || ')') as successors
    from ancestry a
    join public.gov_organization_relationships r on r.target_organization_id = a.ancestor_id
     and r.relationship_type = 'successor_to'
    join public.gov_organizations s on s.id = r.source_organization_id
   group by a.office_id
)
select json_agg(row_to_json(t)) from (
  select n.source_key as pid, n.title,
         o.name as office, o.acronym, oc.office_count, oc.office_claims,
         c.path as ancestry, coalesce(su.successors, '[]'::json) as successors,
         prior.expected_from as was_from, cur.expected_from as now_from,
         prior_a.observed_at::date as prior_release,
         cur_a.observed_at::date as current_release,
         prior_item.title as prior_evidence, cur_item.title as current_evidence,
         prior_item.source->>'sha256' as prior_sha, cur_item.source->>'sha256' as current_sha
    from public.gov_requirement_revisions cur
    join public.gov_intelligence_assertions cur_a on cur_a.id = cur.assertion_id
    join public.gov_intelligence_assertions prior_a on prior_a.id = cur_a.supersedes_id
    join public.gov_requirement_revisions prior on prior.assertion_id = prior_a.id
    join public.gov_need_requirements req on req.id = cur.requirement_id
    join public.gov_needs n on n.id = req.need_id
    -- One office observation per release, so a plain join fans a single revision out
    -- into one copy per release it appeared in. Take the office from the newest
    -- observation nothing has superseded, and count the current ones separately: a
    -- release that moved the requirement to a different office leaves two live
    -- observations by design, and that disagreement has to reach the alert instead of
    -- being resolved to whichever row happened to sort last.
    left join lateral (
      select org.id, org.name, org.acronym
        from public.gov_need_organizations nox
        join public.gov_intelligence_assertions na on na.id = nox.assertion_id
        join public.gov_organizations org on org.id = nox.organization_id
       where nox.need_id = n.id and nox.role = 'originating_requirement_owner'
         and not exists (select 1 from public.gov_intelligence_assertions sup
                          where sup.supersedes_id = na.id)
       order by na.observed_at desc nulls last, org.name
       limit 1) o on true
    left join lateral (
      select count(distinct org.id) as office_count,
             json_agg(distinct org.name || ' (' || coalesce(na.observed_at::date::text, 'undated') || ')') as office_claims
        from public.gov_need_organizations nox
        join public.gov_intelligence_assertions na on na.id = nox.assertion_id
        join public.gov_organizations org on org.id = nox.organization_id
       where nox.need_id = n.id and nox.role = 'originating_requirement_owner'
         and not exists (select 1 from public.gov_intelligence_assertions sup
                          where sup.supersedes_id = na.id)) oc on true
    left join chain c on c.office_id = o.id
    left join succession su on su.office_id = o.id
    left join lateral (
      select bi.title, bi.source from public.gov_assertion_evidence ae
      join public.gov_intelligence_evidence e on e.id = ae.evidence_id
      join public.agency_brain_items bi on bi.id = e.brain_item_id
      where ae.assertion_id = cur_a.id limit 1) cur_item on true
    left join lateral (
      select bi.title, bi.source from public.gov_assertion_evidence ae
      join public.gov_intelligence_evidence e on e.id = ae.evidence_id
      join public.agency_brain_items bi on bi.id = e.brain_item_id
      where ae.assertion_id = prior_a.id limit 1) prior_item on true
   where cur.expected_from is distinct from prior.expected_from
     and cur_a.source_key like '{FORECAST_SOURCE}%'
   order by n.source_key
) t
"""


# Alert C fires on a moved award window *or* a changed value range. A re-estimate for
# the same fiscal period supersedes its predecessor, so the chain finds it the same way
# the requirement chain does; a move to a different fiscal year is a different
# measurement and is not a revision of this one.
VALUE_SQL = """
with recursive ancestry as (
  select o.id as office_id, 1 as depth, o.id as ancestor_id, o.name, o.parent_organization_id
    from public.gov_organizations o
  union all
  select a.office_id, a.depth + 1, p.id, p.name, p.parent_organization_id
    from ancestry a
    join public.gov_organizations p on p.id = a.parent_organization_id
),
chain as (
  select office_id, json_agg(name order by depth) as path from ancestry group by office_id
),
succession as (
  select a.office_id,
         json_agg(distinct a.name || ' -> ' || s.name || ' (from ' || coalesce(r.valid_from::text, 'an undated release') || ')') as successors
    from ancestry a
    join public.gov_organization_relationships r on r.target_organization_id = a.ancestor_id
     and r.relationship_type = 'successor_to'
    join public.gov_organizations s on s.id = r.source_organization_id
   group by a.office_id
)
select json_agg(row_to_json(t)) from (
  select n.source_key as pid, n.title, o.name as office,
         oc.office_count, oc.office_claims,
         c.path as ancestry, coalesce(su.successors, '[]'::json) as successors,
         prior.amount_low as was_low, prior.amount_high as was_high, prior.amount as was_flat,
         cur.amount_low as now_low, cur.amount_high as now_high, cur.amount as now_flat,
         cur.fiscal_year, cur.scope_description,
         prior_a.observed_at::date as prior_release,
         cur_a.observed_at::date as current_release,
         prior_item.title as prior_evidence, cur_item.title as current_evidence
    from public.gov_funding_observations cur
    join public.gov_intelligence_assertions cur_a on cur_a.id = cur.assertion_id
    join public.gov_intelligence_assertions prior_a on prior_a.id = cur_a.supersedes_id
    join public.gov_funding_observations prior on prior.assertion_id = prior_a.id
    join public.gov_needs n on n.id = cur.need_id
    -- One office observation per release, so a plain join fans a single revision out
    -- into one copy per release it appeared in. Take the office from the newest
    -- observation nothing has superseded, and count the current ones separately: a
    -- release that moved the requirement to a different office leaves two live
    -- observations by design, and that disagreement has to reach the alert instead of
    -- being resolved to whichever row happened to sort last.
    left join lateral (
      select org.id, org.name, org.acronym
        from public.gov_need_organizations nox
        join public.gov_intelligence_assertions na on na.id = nox.assertion_id
        join public.gov_organizations org on org.id = nox.organization_id
       where nox.need_id = n.id and nox.role = 'originating_requirement_owner'
         and not exists (select 1 from public.gov_intelligence_assertions sup
                          where sup.supersedes_id = na.id)
       order by na.observed_at desc nulls last, org.name
       limit 1) o on true
    left join lateral (
      select count(distinct org.id) as office_count,
             json_agg(distinct org.name || ' (' || coalesce(na.observed_at::date::text, 'undated') || ')') as office_claims
        from public.gov_need_organizations nox
        join public.gov_intelligence_assertions na on na.id = nox.assertion_id
        join public.gov_organizations org on org.id = nox.organization_id
       where nox.need_id = n.id and nox.role = 'originating_requirement_owner'
         and not exists (select 1 from public.gov_intelligence_assertions sup
                          where sup.supersedes_id = na.id)) oc on true
    left join chain c on c.office_id = o.id
    left join succession su on su.office_id = o.id
    left join lateral (
      select bi.title from public.gov_assertion_evidence ae
      join public.gov_intelligence_evidence e on e.id = ae.evidence_id
      join public.agency_brain_items bi on bi.id = e.brain_item_id
      where ae.assertion_id = cur_a.id limit 1) cur_item on true
    left join lateral (
      select bi.title from public.gov_assertion_evidence ae
      join public.gov_intelligence_evidence e on e.id = ae.evidence_id
      join public.agency_brain_items bi on bi.id = e.brain_item_id
      where ae.assertion_id = prior_a.id limit 1) prior_item on true
   where (cur.amount_low, cur.amount_high, cur.amount)
         is distinct from (prior.amount_low, prior.amount_high, prior.amount)
   order by n.source_key
) t
"""


def query(dsn: str, sql: str) -> list[dict]:
    out = subprocess.run(["psql", dsn, "-At", "-c", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}")
    return json.loads(out.stdout.strip() or "null") or []


def quarter(value: str | None) -> str:
    """A federal fiscal quarter reads back from the calendar date it starts on."""
    if not value:
        return "unstated"
    year, month = int(value[:4]), int(value[5:7])
    fy, q = (year + 1, 1) if month >= 10 else (year, (month - 1) // 3 + 2)
    return f"FY{fy % 100:02d} Q{q}"


def direction(was: str | None, now: str | None) -> str:
    if not was or not now:
        return "changed"
    return "slips" if now > was else "pulls forward"


def money(low, high, flat) -> str:
    if flat is not None:
        return f"${float(flat)/1e6:,.1f}M"
    if low is None and high is None:
        return "unstated"
    if low is None or high is None:
        return f"${float(low if low is not None else high)/1e6:,.1f}M"
    return f"${float(low)/1e6:,.1f}M-${float(high)/1e6:,.1f}M"


def fiscal_label(fiscal_year) -> str:
    """A missing year is unstated. Slicing None printed `FYne`."""
    return f"FY{int(fiscal_year) % 100:02d}" if fiscal_year is not None else "an unstated year"


def render_value(row: dict) -> str:
    was = money(row["was_low"], row["was_high"], row["was_flat"])
    now = money(row["now_low"], row["now_high"], row["now_flat"])
    citations = [c for c in (row.get("prior_evidence"), row.get("current_evidence")) if c]
    return "\n".join([
        f"**{row['pid']}** - {row['title']}",
        f"- What changed: anticipated total value {was} -> {now} for "
        f"{fiscal_label(row.get('fiscal_year'))} between the {row['prior_release']} and "
        f"{row['current_release']} releases.",
        f"- Office: {office_line(row)} Ancestry: {ancestry_line(row)}.",
        f"- Evidence: {'; '.join(citations) if citations else row['scope_description']}.",
        "- Uncertainty: the LRAE is an estimate and the range is wide by design; a "
        "change may be scope, quantity or a better estimate of the same work.",
        "- Why it matters: the value range sets whether a bid is worth pursuing and "
        "who else will show up for it.",
    ])


def ancestry_line(row: dict) -> str:
    path = " -> ".join(row.get("ancestry") or []) or "office not resolved"
    successors = row.get("successors") or []
    if successors:
        path += ("; succession documented at the ancestor's level and scoped to what that source lists: "
                 + ", ".join(successors) + "; this office's own placement under the successor is not established in the loaded data")
    return path


def office_line(row: dict) -> str:
    """The requirement owner, or every office still claimed when the releases disagree.

    Two live observations mean a later release named a different office and neither was
    superseded. Naming one of them would be the flattening the per-release observations
    exist to prevent, so the alert carries both and their dates.
    """
    if (row.get("office_count") or 0) > 1:
        return ("contested, " + str(row["office_count"]) + " offices still claimed: "
                + "; ".join(row.get("office_claims") or []) + ".")
    return (row.get("office") or "not resolved") + "."


def render(row: dict) -> str:
    was, now = row["was_from"], row["now_from"]
    return "\n".join([
        f"**{row['pid']}** - {row['title']}",
        f"- What changed: anticipated award {direction(was, now)} "
        f"{quarter(was)} -> {quarter(now)} between the {row['prior_release']} and "
        f"{row['current_release']} releases.",
        f"- Office: {office_line(row)} Ancestry: {ancestry_line(row)}.",
        f"- Evidence: {row.get('prior_evidence')} (sha256 {str(row.get('prior_sha'))[:12]}); "
        f"{row.get('current_evidence')} (sha256 {str(row.get('current_sha'))[:12]}).",
        "- Uncertainty: the LRAE is an estimate. A move may be an acquisition-strategy "
        "change or a clerical correction, and the release does not say which.",
        "- Why it matters: the award window is what a capture timeline is built on, and "
        "this one moved after the plan was set.",
    ])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = query(args.dsn, REVISIONS_SQL)
    values = query(args.dsn, VALUE_SQL)
    if args.json:
        print(json.dumps({"timing": rows, "value": values}, indent=1, default=str))
        return 0
    if not rows and not values:
        print("no forecast revisions")
        return 0
    for row in rows:
        print(render(row))
        print()
    for row in values:
        print(render_value(row))
        print()
    slipped = sum(1 for r in rows if direction(r["was_from"], r["now_from"]) == "slips")
    print(f"{len(rows)} award-window revisions: {slipped} slip, "
          f"{len(rows) - slipped} pull forward or unstated")
    print(f"{len(values)} value-range revisions")
    return 0


def selfcheck() -> int:
    # A federal fiscal year starts in October, so October 2025 is FY26 Q1 and a
    # calendar reading would report it as FY25 Q4.
    assert quarter("2025-10-01") == "FY26 Q1", quarter("2025-10-01")
    assert quarter("2026-01-01") == "FY26 Q2"
    assert quarter("2026-04-01") == "FY26 Q3"
    assert quarter("2026-07-01") == "FY26 Q4"
    assert quarter(None) == "unstated"

    assert direction("2024-01-01", "2027-01-01") == "slips"
    assert direction("2027-01-01", "2024-01-01") == "pulls forward"
    assert direction(None, "2024-01-01") == "changed"

    row = {"pid": "P1", "title": "T", "office": "PMW 160", "ancestry": ["PMW 160", "PEO C4I"],
           "successors": ["PEO C4I -> PAE Mission Systems (from 2026-05-11)"],
           "was_from": "2024-01-01", "now_from": "2027-01-01",
           "prior_release": "2023-06-20", "current_release": "2025-06-19",
           "prior_evidence": "a", "current_evidence": "b", "prior_sha": "ab", "current_sha": "cd"}
    text = render(row)
    assert "FY24 Q2 -> FY27 Q2" in text, text
    assert "PMW 160 -> PEO C4I" in text and "succeeded by" not in text, "a PEO-level consolidation is never printed as the office's own succession"
    assert "succession documented at the ancestor's level" in text and "PEO C4I -> PAE Mission Systems (from 2026-05-11)" in text
    assert "not established in the loaded data" in text
    assert ancestry_line({"ancestry": [], "successors": []}) == "office not resolved"
    assert money(None, None, 5_000_000) == "$5.0M"
    assert money(1e8, 2.5e8, None) == "$100.0M-$250.0M"
    assert money(None, 2.5e8, None) == "$250.0M"
    assert money(None, None, None) == "unstated"
    value_row = {"pid": "P2", "title": "T", "office": "PMW 170", "was_low": 1e8,
                 "was_high": 2.5e8, "was_flat": None, "now_low": 2.5e8, "now_high": 5e8,
                 "now_flat": None, "fiscal_year": 2026, "scope_description": "LRAE value",
                 "prior_release": "2023-06-20", "current_release": "2025-06-19"}
    assert "$100.0M-$250.0M -> $250.0M-$500.0M" in render_value(value_row)

    # One office observation per release must not print the same alert twice, and two
    # offices that disagree must not collapse to one name.
    assert office_line({"office": "PMW 205", "office_count": 1}) == "PMW 205."
    contested = office_line({"office": "PMW 205", "office_count": 2,
                             "office_claims": ["PEO EIS (2023-06-20)", "PEO Digital (2025-06-19)"]})
    assert contested.startswith("contested, 2 offices still claimed:")
    assert "PEO EIS (2023-06-20)" in contested and "PEO Digital (2025-06-19)" in contested
    assert office_line({"office": None, "office_count": 0}) == "not resolved."

    # A missing fiscal year used to slice None and print `FYne`.
    assert fiscal_label(2026) == "FY26" and fiscal_label(None) == "an unstated year"
    assert "FYne" not in render_value({**value_row, "fiscal_year": None})
    assert "an unstated year" in render_value({**value_row, "fiscal_year": None})

    # The evidence line must name the releases it actually has, not claim both.
    cited = render_value({**value_row, "prior_evidence": "LRAE 2023 row 9",
                          "current_evidence": "LRAE 2025 row 4"})
    assert "LRAE 2023 row 9; LRAE 2025 row 4" in cited
    assert "both releases" not in cited
    assert "Ancestry:" in cited

    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(selfcheck() if "--selfcheck" in sys.argv else main(sys.argv[1:]))

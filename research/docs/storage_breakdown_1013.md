# Production storage: what to keep, what to fetch from USAspending (2026-10-13)

Production holds 13.2 GB (13,237 MiB, `pg_database_size`, read-only query 10-13 evening). Sizes below are whole
tables with indexes and TOAST (`pg_total_relation_size`); row counts are planner estimates. Slices of
`agency_brain_items` are from research/docs/storage_move_plan.md (row data, `pg_column_size`).

## Rule

Keep what USAspending cannot give back, or what we worked out ourselves. Fetch what USAspending serves on demand.

## By group

| Group | Tables | MB | Writer | Keep or fetch |
|---|---|---:|---|---|
| Our record | agency_brain_items | 3,202 | our loader | Ended contracts (702 MB of rows) and grants (1,008 MB) fetch; running contracts (133 MB), research awards (19 MB), other transaction agreements and every non-award row (notices, forecasts, budget lines, directives, office tree, people readings) keep |
| Proof | gov_intelligence_evidence, gov_intelligence_assertions, gov_assertion_evidence | 1,184 | our loader | Keep; the 324 MB of evidence under the moving grant rows goes with them |
| App's USAspending copy | private.usa_award_children (1.74M award search rows, written 09-03..10-11), public.usa_awards (1.21M awards) | 3,329 | app | Raw award fields fetch; the 10,300 awards with the app's recompete predictions keep. gov_procurement_refs keys on these rows, so the app owner decides |
| API cache | gov_api_cache | 1,099 | app | 108,664 of 148,266 rows past `expires_at` purge (about 0.7 GB); USAspending replies are fetches by nature |
| Grant pipeline | grant_chunks, grant_source_revisions, grant_awards, grant_sources | 2,155 | app | grant_awards carries `usaspending_award_id`, so award facts fetch; grant text and its revisions are the app's, ask its owner |
| Run logs | gov_runs (2,378 runs, payloads in TOAST), workflow_runs, brain_jobs | 786 | app | A retention window (for example 90 days) trims most of it |
| Everything else | needs, contacts, SAM opportunities, vehicles, competitors, partner snapshots, 200+ small tables | 1,459 | both | Keep |

## Totals

| | GB |
|---|---:|
| Out on our side (ended contracts, grants and their evidence, expired cache) | about 2.7 |
| Could come out with the app owner (USAspending copy less predictions, grant award facts, old run logs) | up to about 4.5 |
| Stays | about 6 |

## What the tool fetches instead (`.claude/skills/pursuit/usaspending.py`, public API, no key)

| Need | Command |
|---|---|
| A contract's obligated amount, ceiling, dates, offices | `award <piid>` |
| Past winners, grants | `search` |
| Contracts ending soon, incumbent, vehicle, competition | `recompetes` |
| Who wins at an office | `winners` |
| Orders under a vehicle | `orders <idv>` |
| Subcontractors | `subawards <piid>` |
| Change history | `history <piid>` |
| Which account paid for an award | `funding <piid>` |
| Competitor profile | `recipient` |
| Account resources, obligated, unobligated, by program activity | `budget` |
| Obligations by month | `monthly` |

Limits: other transaction agreements are not in USAspending (kept stored); DoD award data runs about 90 days
behind (awards acted on in the last 120 days stay stored); account data is monthly, about six weeks behind;
next year's account data does not exist until the year starts.

## Questions for the app owner

1. Does any app screen read ended contracts or grants from agency_brain_items directly?
2. Can usa_award_children and the raw fields of usa_awards be fetched instead of stored, keeping the predictions?
3. Is grant_chunks the search index for grant text, and does it need every revision?
4. What retention do gov_runs and workflow_runs need?

## How measured

```sql
select n.nspname||'.'||c.relname, pg_total_relation_size(c.oid), pg_relation_size(c.oid), c.reltuples::bigint
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r','m') and n.nspname not in ('pg_catalog','information_schema','pg_toast')
order by 2 desc;
```

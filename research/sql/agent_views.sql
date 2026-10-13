-- Read-only views an agent queries instead of the raw tables. Each row keeps the record's wording and carries its
-- source; a role reading the record cannot state is "potential"; a domain the record lacks shows in record_holds as
-- absent, never as zero. Idempotent. Apply to a local proof database first; production is the owner's step:
--     psql -h HOST -p PORT -U postgres -d DB -v ON_ERROR_STOP=1 -f research/sql/agent_views.sql
-- An agent connects as agent_reader (select on these views only), or through the Supabase MCP server in read-only mode.
--
-- v2 (2026-10-10): live rows only (superseded_by is null); every view carries `department` (the parent department of
-- the agency, so 'Department of Defense' holds the Navy and 'Department of Commerce' holds NOAA) and the office's
-- `office_path` (its ancestry, 'Department of Health and Human Services (HHS) > Centers for Disease Control and
-- Prevention (CDC)'), so a sub-agency name finds its rows; source_url falls back to the record's source_url and to the
-- document's citation URL and is only ever an http(s) URL; needs carry their date, release, estimate and source;
-- budget_lines keeps a book column the record holds with no figure (null amount); seed leaders' titles match by name.
-- Columns are only ever appended, so `create or replace` works over the v1 views production holds.

create schema if not exists agent;

-- A name for matching only: no honorific ("The Honorable", Dr.), no nickname in brackets, case and spacing folded.
create or replace function agent.name_key(t text) returns text language sql immutable parallel safe as $$
  select btrim(lower(regexp_replace(regexp_replace(regexp_replace(t, '\s*\([^)]*\)', '', 'g'),
         '^\s*((the\s+)?honorable|hon\.|dr\.?|mr\.?|mrs\.?|ms\.?)\s+', '', 'i'), '\s+', ' ', 'g')))
$$;

-- One person's name however a source wrote it, for grouping one person's readings: name_key, then 'Last, First' turned
-- round, a rank in front ('Brig. Gen.', 'LCDR'), credentials after a comma, middle initials and a Jr./III dropped.
-- 'Brig. Gen. Jane Doe', 'Jane Q. Doe' and 'Doe, Jane' all give 'jane doe'. A same-name
-- match, not a stated link.
create or replace function agent.person_key(t text) returns text language sql immutable parallel safe as $$
  select nullif(btrim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
           regexp_replace(case when k ~ '^[^ ,]+, [^,]+$' then split_part(k, ', ', 2) || ' ' || split_part(k, ', ', 1)
                               else split_part(k, ',', 1) end,
             '^(((lt|maj|brig|rear|vice|1st|2nd)\.?\s+)?(gen|general|col|colonel|adm|admiral|lt|ltg|ltc|mg|bg|maj|major|capt|captain|cpt|cdr|lcdr|cmdr|rdml|radm|vadm|sgt|msgt|smsgt|cmsgt|csm|sgm|cw[1-5])\.?\s+)+', ''),
           '[.]', ' ', 'g'), '\s(jr|sr|ii|iii|iv|phd|pmp)\s*$', ''), '(^|\s)[a-z](?=\s|$)', ' ', 'g'), '\s+', ' ', 'g')), '')
    from (select agent.name_key(t) as k) n
$$;

-- Every office the record holds, with its ancestry. Six levels cover the deepest office loaded (five ancestors).
create or replace view agent.offices as
select o.id as office_id, a.canonical_name as agency, coalesce(pa.canonical_name, a.canonical_name) as department,
       o.name as office, o.acronym, o.org_type, p1.name as parent_office,
       concat_ws(' > ',
         case when p6.acronym is null or strpos(p6.name, p6.acronym) > 0 then p6.name else p6.name || ' (' || p6.acronym || ')' end,
         case when p5.acronym is null or strpos(p5.name, p5.acronym) > 0 then p5.name else p5.name || ' (' || p5.acronym || ')' end,
         case when p4.acronym is null or strpos(p4.name, p4.acronym) > 0 then p4.name else p4.name || ' (' || p4.acronym || ')' end,
         case when p3.acronym is null or strpos(p3.name, p3.acronym) > 0 then p3.name else p3.name || ' (' || p3.acronym || ')' end,
         case when p2.acronym is null or strpos(p2.name, p2.acronym) > 0 then p2.name else p2.name || ' (' || p2.acronym || ')' end,
         case when p1.acronym is null or strpos(p1.name, p1.acronym) > 0 then p1.name else p1.name || ' (' || p1.acronym || ')' end,
         case when o.acronym is null or strpos(o.name, o.acronym) > 0 then o.name else o.name || ' (' || o.acronym || ')' end) as office_path,
       o.active,
       -- a reorganization: the office that took this one over, and the one this office took over (successor_to rows)
       (select string_agg(s.name, '; ' order by s.name) from gov_organization_relationships r
          join gov_organizations s on s.id = r.source_organization_id
         where r.target_organization_id = o.id and r.relationship_type = 'successor_to') as successor_office,
       (select string_agg(t.name, '; ' order by t.name) from gov_organization_relationships r
          join gov_organizations t on t.id = r.target_organization_id
         where r.source_organization_id = o.id and r.relationship_type = 'successor_to') as predecessor_office
from gov_organizations o
left join agencies a on a.id = o.agency_id
left join agencies pa on pa.id = a.parent_agency_id
left join gov_organizations p1 on p1.id = o.parent_organization_id
left join gov_organizations p2 on p2.id = p1.parent_organization_id
left join gov_organizations p3 on p3.id = p2.parent_organization_id
left join gov_organizations p4 on p4.id = p3.parent_organization_id
left join gov_organizations p5 on p5.id = p4.parent_organization_id
left join gov_organizations p6 on p6.id = p5.parent_organization_id;
comment on view agent.offices is 'Every office the record holds: its agency, parent department, type and office_path (the chain of offices above it, acronyms in brackets). Find a sub-agency or command by acronym in office_path (office_path ~ ''\mCDC\M''); the other views carry the same office_path.';

create or replace view agent.record_holds as
select a.canonical_name as agency, b.section, count(*) as rows,
       max(b.as_of) filter (where b.as_of between date '2000-01-01' and current_date) as newest,
       coalesce(pa.canonical_name, a.canonical_name) as department
from agency_brain_items b
join agencies a on a.id = b.agency_id
left join agencies pa on pa.id = a.parent_agency_id
where b.superseded_by is null
group by 1, 2, 5;
comment on view agent.record_holds is 'What the record holds per agency and section, with the newest date. Read it first: a section missing here was not collected, which is not the same as nothing happening. A row count is not coverage: read some rows (forecast items of most civilian agencies are locator-only; their content is in agent.needs).';

create or replace view agent.items as
select b.id as item_id, a.canonical_name as agency, b.section, b.event_type, b.title,
       b.body, b.as_of, b.published_at,
       coalesce(case when b.source ->> 'url' ~* '^https?://' then b.source ->> 'url' end,
                case when b.source -> 'listing' ->> 'url' ~* '^https?://' then b.source -> 'listing' ->> 'url' end,
                case when b.source ->> 'source_url' ~* '^https?://' then b.source ->> 'source_url' end,
                case when d.citation_url ~* '^https?://' then d.citation_url end,
                case when d.source_url ~* '^https?://' then d.source_url end) as source_url,
       coalesce(b.source ->> 'sha256', b.source -> 'listing' ->> 'sha256') as sha256,
       coalesce(b.source ->> 'retrieved_at', b.source -> 'listing' ->> 'retrieved_at') as retrieved_at,
       b.source_provider, b.source_tier, f.office as organization,
       coalesce(pa.canonical_name, a.canonical_name) as department, f.office_path, b.source -> 'pages' as source_pages,
       b.data as facts_as_written
from agency_brain_items b
join agencies a on a.id = b.agency_id
left join agencies pa on pa.id = a.parent_agency_id
left join agent.offices f on f.office_id = b.primary_organization_id
left join agency_brain_documents d on d.id = coalesce(b.source_document_id, case when b.source ->> 'document_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then (b.source ->> 'document_id')::uuid end)
where b.superseded_by is null;
comment on view agent.items is 'Every live dated statement in the record (notices, forecast rows, awards, budget, remarks, oversight, people), one row each with its source URL (an http(s) URL: the record''s own, else the cited document''s), sha256 and retrieval date, and source_pages when the document is long. Search title and body with ilike. department is the parent department and office_path the office''s ancestry: a sub-agency (CDC, NIH, FAA) is found by department plus office_path ~ ''\mCDC\M'', not by agency. facts_as_written is the record''s own fields for the statement, keys as it wrote them: a notice''s responses_due; a grant''s amount, recipient, award_id, start, end and awarding_sub_agency; a contract''s piid, vendor, contracting_office, funding_office and expires_on (no dollar amount: the record does not hold one for contracts); an SBIR topic''s open, close, qa_open, qa_close and ceiling; a protest''s outcome and decision_date. Dates and amounts the title and body leave out are often here.';

create or replace view agent.budget_lines as
select b.id as item_id, a.canonical_name as agency,
       coalesce(b.data ->> 'li', b.data ->> 'program') as line,
       b.data ->> 'title' as title,
       b.data ->> 'appropriation' as appropriation,
       b.data ->> 'budget_activity' as budget_activity,
       e.col as book_column,
       substring(e.col from 'fy(\d{4})')::int as fiscal_year,
       e.amt::numeric as amount_musd,
       case when b.data ? 'amounts_musd' then 'program block' else 'PB' || coalesce(b.data ->> 'pb', '?') || ' justification book line' end as measure,
       case when o.existing_agency_id = a.id then null else o.name end as placed_office,
       coalesce(case when b.source ->> 'url' ~* '^https?://' then b.source ->> 'url' end,
                case when b.source -> 'listing' ->> 'url' ~* '^https?://' then b.source -> 'listing' ->> 'url' end,
                case when b.source ->> 'source_url' ~* '^https?://' then b.source ->> 'source_url' end,
                case when d.citation_url ~* '^https?://' then d.citation_url end,
                case when d.source_url ~* '^https?://' then d.source_url end) as source_url,
       coalesce(b.source ->> 'sha256', b.source -> 'listing' ->> 'sha256') as sha256,
       coalesce(b.source ->> 'retrieved_at', b.source -> 'listing' ->> 'retrieved_at') as retrieved_at,
       b.source ->> 'book_date' as book_date,
       coalesce(pa.canonical_name, a.canonical_name) as department, f.office_path
from agency_brain_items b
join agencies a on a.id = b.agency_id
left join agencies pa on pa.id = a.parent_agency_id
left join gov_organizations o on o.id = b.primary_organization_id
left join agent.offices f on f.office_id = b.primary_organization_id
left join agency_brain_documents d on d.id = coalesce(b.source_document_id, case when b.source ->> 'document_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then (b.source ->> 'document_id')::uuid end)
cross join lateral jsonb_each_text(case when jsonb_typeof(b.data -> 'amounts') = 'object' then b.data -> 'amounts'
                                        else b.data -> 'amounts_musd' end) as e(col, amt)
where b.section = 'budget' and b.superseded_by is null
  and (jsonb_typeof(b.data -> 'amounts') = 'object' or b.data ? 'amounts_musd');
comment on view agent.budget_lines is 'One row per budget line (or program block) per book column, in $M as the book prints it. book_column is the column as written (fy2027_base, prior_years, to_complete); the column says what the year means, the view does not call it request or enacted. A program block (line:APPN:NNNN) repeats the amounts of the book line it reads, so never sum across measures: filter measure ilike ''%book line'' and name the book (PB2026 and PB2027 print the same year differently). placed_office is empty when the record placed the line in no office below the agency. A null amount_musd is a column the record holds with no figure (a dash in the book or a figure not read): say so, never zero. Budget statements that are not book lines are in agent.budget_statements.';

-- the budget section's other statements: they carry figures in words and in fields of their own shape, never in $M columns
create or replace view agent.budget_statements as
select b.id as item_id, a.canonical_name as agency, coalesce(b.event_type, 'statement') as kind, b.title, b.body,
       b.data as figures_as_written, b.as_of, b.published_at,
       coalesce(case when b.source ->> 'url' ~* '^https?://' then b.source ->> 'url' end,
                case when b.source -> 'listing' ->> 'url' ~* '^https?://' then b.source -> 'listing' ->> 'url' end,
                case when b.source ->> 'source_url' ~* '^https?://' then b.source ->> 'source_url' end,
                case when d.citation_url ~* '^https?://' then d.citation_url end,
                case when d.source_url ~* '^https?://' then d.source_url end) as source_url,
       coalesce(b.source ->> 'retrieved_at', b.source -> 'listing' ->> 'retrieved_at') as retrieved_at,
       b.source_provider,
       case when o.existing_agency_id = a.id then null else o.name end as placed_office,
       coalesce(b.fiscal_year, case when b.data ->> 'fiscal_year' ~ '^\d{4}$' then (b.data ->> 'fiscal_year')::int end) as fiscal_year,
       b.source -> 'pages' as source_pages,
       coalesce(pa.canonical_name, a.canonical_name) as department, f.office_path
from agency_brain_items b
join agencies a on a.id = b.agency_id
left join agencies pa on pa.id = a.parent_agency_id
left join gov_organizations o on o.id = b.primary_organization_id
left join agent.offices f on f.office_id = b.primary_organization_id
left join agency_brain_documents d on d.id = coalesce(b.source_document_id, case when b.source ->> 'document_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then (b.source ->> 'document_id')::uuid end)
where b.section = 'budget' and b.superseded_by is null
  and not coalesce(jsonb_typeof(b.data -> 'amounts') = 'object' or b.data ? 'amounts_musd', false);
comment on view agent.budget_statements is 'Every budget statement agent.budget_lines does not carry: congressional directives (report language), funding changes quoted from remarks, and summary figures. body states the figures in words; figures_as_written is the record''s own fields, keys and units as the item wrote them (millions, billions, dollars). Quote them as written: never convert units, never add figures across items. source_url is the record''s URL or the cited document''s (with source_pages); a row without one has no public source in the record: say so. fiscal_year is the report''s or statement''s year.';

create or replace view agent.people_readings as
-- computed once per query: as a joined subquery the planner, expecting one person, ran it again for every person
with ac as materialized (
  -- other sources write the agency as SAM's path ('HOMELAND SECURITY, DEPARTMENT OF.US COAST GUARD',
  -- 'DEPT OF DEFENSE.DEPT OF THE NAVY.NAVSEA') or as a name with its acronym: the agency's name, its 'X, DEPARTMENT OF'
  -- form or 'DEPT OF THE X' inside that text places the contact; only agencies with readings are matched (a generic
  -- name such as 'Administration' would place contacts everywhere)
  select m.agency_id, agent.name_key(c.name) as name_key, min(c.email) as email,
         string_agg(distinct c.phone, '; ') as phone, string_agg(distinct coalesce(c.source, 'the app'), ', ') as sources
    from (select ca.agency, ag.id as agency_id
            from (select distinct agency, upper(agency) as u from gov_contacts
                   where (source is null or source not like 'federal-buyer-map-trial/%') and email is not null) ca
            join (select g.id, upper(g.canonical_name) as u1,
                         upper(regexp_replace(g.canonical_name, '^Department of ', '')) || ', DEPARTMENT OF' as u2,
                         case when g.canonical_name like 'Department of the %' then 'DEPT OF THE ' || upper(substr(g.canonical_name, 19)) end as u3
                    from agencies g
                   where exists (select 1 from agency_brain_items x where x.agency_id = g.id and x.section = 'people'
                                    and x.data ? 'bucket' and x.superseded_by is null)) ag
              on position(ag.u1 in ca.u) > 0 or position(ag.u2 in ca.u) > 0 or position(ag.u3 in ca.u) > 0) m
    join gov_contacts c on c.agency = m.agency and (c.source is null or c.source not like 'federal-buyer-map-trial/%') and c.email is not null
   group by 1, 2
  having count(distinct lower(c.email)) = 1
),
-- a reading a later load re-read: the same person, office and words, loaded a day or more after it (the 10-11 rule
-- that a title alone is a leader by title re-read 216 budget holder and decision maker readings; insert-only loads
-- keep the old row live). Readings written by one load together (contracting and problem owner) all stand.
reread as materialized (
  select id from (select r.id, r.created_at,
                         max(r.created_at) over (partition by r.data ->> 'person', r.primary_organization_id, r.body) as newest
                    from agency_brain_items r
                   where r.section = 'people' and r.data ? 'bucket' and r.superseded_by is null) x
   where newest > created_at + interval '1 day'
)
select b.id as item_id, a.canonical_name as agency, b.data ->> 'name' as person, b.data ->> 'bucket' as bucket,
       case when b.data ->> 'bucket' in ('budget_holder', 'decision_maker', 'champion')
            then 'potential ' || replace(b.data ->> 'bucket', '_', ' ') else replace(b.data ->> 'bucket', '_', ' ') end as reading,
       array(select jsonb_array_elements_text(b.data -> 'basis')) as basis,
       p ->> 'role' as role_as_written, p ->> 'office' as office_key, p ->> 'status' as post_status,
       p ->> 'source' as post_source, nullif(p ->> 'source_url', '') as source_url, (p ->> 'observed_at')::date as observed_at,
       (select count(*) from gov_intelligence_evidence v where v.brain_item_id = b.id) as evidence_rows,
       -- the post's title as the source wrote it, from the loaded contact's positions: the contact id is uuid5 of the
       -- person id; a leader the organization memory lists sits under the memory's own contact, at the item's office
       coalesce(
         (select string_agg(distinct nullif(btrim(cp.raw_title), ''), '; ')
            from gov_contact_positions cp
           where cp.contact_id = extensions.uuid_generate_v5('7c3d1f5a-9b24-4f8e-8c61-2a0d5e7b41c3'::uuid, 'contact:' || (b.data ->> 'person'))
             and cp.source = p ->> 'source' and cp.observed_at::date = (p ->> 'observed_at')::date),
         (select string_agg(distinct nullif(btrim(cp.raw_title), ''), '; ')
            from gov_contact_positions cp join gov_contacts c on c.id = cp.contact_id
           where p ->> 'source' = 'organization_seed' and cp.organization_id = b.primary_organization_id
             and cp.source like '%organization_seed.json' and agent.name_key(c.name) = agent.name_key(b.data ->> 'name'))) as title_as_written,
       b.body as said,
       b.data ->> 'person' as person_id, f.office, f.office_path,
       coalesce(pa.canonical_name, a.canonical_name) as department,
       -- how to reach the person: the loaded contact's own address and phone, else a contact from another source
       -- (SAM, the app) with the same name at the same agency and one address only, said so in contact_basis
       case when oc.email is not null or oc.phone is not null then oc.email else ac.email end as email,
       case when oc.email is not null or oc.phone is not null then oc.phone else ac.phone end as phone,
       case when oc.email is not null or oc.phone is not null then 'our contact record'
            when ac.email is not null then 'same name at the same agency, from ' || ac.sources end as contact_basis,
       agent.person_key(b.data ->> 'name') as person_key
from agency_brain_items b
join agencies a on a.id = b.agency_id
left join agencies pa on pa.id = a.parent_agency_id
left join agent.offices f on f.office_id = b.primary_organization_id
left join lateral jsonb_array_elements(coalesce(b.data -> 'posts', '[]'::jsonb)) as p on true
left join gov_contacts oc on oc.id = extensions.uuid_generate_v5('7c3d1f5a-9b24-4f8e-8c61-2a0d5e7b41c3'::uuid, 'contact:' || (b.data ->> 'person'))
cross join lateral (select agent.name_key(b.data ->> 'name') as name_key offset 0) k  -- once per row, not per pair
left join ac on ac.agency_id = a.id and ac.name_key = k.name_key
where b.section = 'people' and b.data ? 'bucket' and b.superseded_by is null
  and not exists (select 1 from reread x where x.id = b.id)
  -- ponytail: a notice sentence read as a name ('WHEN SOLICITATION HAS BEEN APPROVED BY ...', 15 words) is no person;
  -- the longest real names held (an office prefix glued on, 'Natl Ctr for HIV, ... Jane Doe') run to 12 words
  and coalesce(array_length(regexp_split_to_array(btrim(b.data ->> 'name'), '\s+'), 1), 0) <= 12;
comment on view agent.people_readings is 'One row per person per post. reading is the record''s bucket, written as potential for budget holder, decision maker and champion: a reading, never a fact. basis says how it was reached (listed, stated, self-stated, inferred). "leader by title" is a title and nothing more. Contracting staff are a route, never an advocate. role_as_written is the record''s role code; title_as_written is the post as the source printed it, and said is what the reading rests on: search those two for words like a program name. office is the office the person is filed at, office_path its ancestry; post_status (confirmed_current, recently_observed, history) and observed_at say how current the post is. person_id groups one person''s rows. email and phone reach the person: contact_basis says whether they are on our own contact record or matched by the same name at the same agency from another source (a match, not a stated link: say so). person_key groups one person written several ways or filed under several person_ids (rank, initials, ''Last, First'' folded): a same-name match, not a stated link. When a later load re-read a reading (same person, office and words), only the newer one shows.';

create or replace view agent.needs as
select n.id as need_id, a.canonical_name as agency, n.title,
       n.description, n.lifecycle, n.source, n.source_key,
       (select string_agg(distinct coalesce(o.acronym, o.name), '; ') from gov_need_organizations x
          join gov_organizations o on o.id = x.organization_id
         where x.need_id = n.id and x.role = 'originating_requirement_owner') as owner_offices,
       (select string_agg(distinct coalesce(o.acronym, o.name), '; ') from gov_need_organizations x
          join gov_organizations o on o.id = x.organization_id
         where x.need_id = n.id and x.role = 'contracting_office') as contracting_offices,
       r.as_of, r.release, r.expected_from, r.expected_to, r.source_url, r.item_id,
       (select jsonb_strip_nulls(jsonb_build_object('measure', fo.measure, 'amount', fo.amount, 'amount_low', fo.amount_low,
                 'amount_high', fo.amount_high, 'currency', fo.currency_code, 'unit_multiplier', fo.unit_multiplier,
                 'fiscal_year', fo.fiscal_year, 'period_start', fo.period_start, 'period_end', fo.period_end, 'scope', fo.scope_description))
          from gov_funding_observations fo join gov_intelligence_assertions fa on fa.id = fo.assertion_id
         where fo.need_id = n.id and fa.retracted_at is null
         order by fa.observed_at desc nulls last limit 1) as estimate_as_written,
       (select string_agg(distinct f.office_path, '; ') from gov_need_organizations x
          join agent.offices f on f.office_id = x.organization_id where x.need_id = n.id) as office_paths,
       coalesce(pa.canonical_name, a.canonical_name) as department
from gov_needs n
join agencies a on a.id = n.agency_id
left join agencies pa on pa.id = a.parent_agency_id
-- the newest observation of the requirement: its date, the forecast release it came in, the dates it states, and the
-- notice or forecast file it rests on (an http(s) URL only), with the item that quotes it
left join lateral (
  select ia.observed_at::date as as_of, substring(ia.source_key from 'datapack:([^:]+):req') as release,
         rv.expected_from, rv.expected_to, ev.source_url, ev.item_id
    from gov_need_requirements rq
    join gov_requirement_revisions rv on rv.requirement_id = rq.id
    join gov_intelligence_assertions ia on ia.id = rv.assertion_id
    left join lateral (
      select case when e.source_url ~* '^https?://' then e.source_url end as source_url, e.brain_item_id as item_id
        from gov_assertion_evidence ae join gov_intelligence_evidence e on e.id = ae.evidence_id
       where ae.assertion_id = ia.id
       order by (e.source_url ~* '^https?://') desc nulls last limit 1) ev on true
   where rq.need_id = n.id and ia.retracted_at is null
   order by ia.observed_at desc nulls last limit 1) r on true;
comment on view agent.needs is 'Each requirement (forecast row or notice group) with the office that owns it and the office that contracts for it, as the record ties them (acronyms or codes: search owner_offices, contracting_offices and office_paths, not only the full name). as_of is the date of its newest observation (the notice''s posting, the forecast release''s date), release the forecast release it came in, expected_from/expected_to the dates it states, source_url the notice or forecast file, item_id the statement in agent.items/agent.evidence that quotes it, estimate_as_written the record''s procurement estimate with its own units. Forecast content lives here, not in agent.items.';

create or replace view agent.evidence as
select v.brain_item_id as item_id, b.section, b.title,
       case when v.source_url ~* '^https?://' then v.source_url end as source_url,
       v.excerpt, v.published_at, v.provider,
       v.pages, a.canonical_name as agency, coalesce(pa.canonical_name, a.canonical_name) as department, f.office_path
from gov_intelligence_evidence v
join agency_brain_items b on b.id = v.brain_item_id
join agencies a on a.id = b.agency_id
left join agencies pa on pa.id = a.parent_agency_id
left join agent.offices f on f.office_id = b.primary_organization_id
where b.superseded_by is null;
comment on view agent.evidence is 'The excerpts each statement rests on, with the source URL (http(s) only) and pages. Join to agent.items, agent.people_readings or agent.needs by item_id. An excerpt is what the source says; a statement''s title can say more than its excerpt, so quote the excerpt.';

do $$ begin
  if not exists (select from pg_roles where rolname = 'agent_reader') then create role agent_reader nologin; end if;
end $$;
grant usage on schema agent to agent_reader;
grant select on all tables in schema agent to agent_reader;
grant execute on function agent.name_key(text) to agent_reader;
grant execute on function agent.person_key(text) to agent_reader;

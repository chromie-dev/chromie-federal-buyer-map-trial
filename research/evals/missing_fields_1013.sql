-- B14 (2026-10-13, agent F): fill rates of next-action fields on OPEN opportunities, production, read only.
-- Run through the Supabase MCP server (read-only). Each statement is one cheap read; results are in
-- research/evals/missing_fields_1013.md. Local raw checks (SAM JSON, datapack CSV) are described there.
--
-- Open rules
--   SAM notice: live agency_brain_items row, source_provider = 'sam_gov_site_api', section procurement_patterns or
--     industry_engagement, event_type not contract_awarded / contract_modified, data->>'responses_due' >= '2026-10-13'.
--   Forecast line: newest live forecast item per (agency, data->>'line') carrying data->'stated'; its datapack need is
--     not fulfilled or cancelled, and the need's newest stated window end (expected_to) is null or >= '2026-10-13'.
--   "Filled" for a stated forecast field: non-empty and not a placeholder (TBD, to be determined, unknown, n/a,
--     none, "currently, this information is not available", dashes, 0, FYXXXX).

-- 0. What the agent views expose (agent.items.facts_as_written is agency_brain_items.data, whole)
select table_name, string_agg(column_name, ', ' order by ordinal_position) cols
  from information_schema.columns where table_schema = 'agent' group by 1;
select pg_get_viewdef('agent.items'::regclass, true);

-- 1. What a SAM notice row stores (recent rows: only line and responses_due in data)
select k, count(*) n
  from agency_brain_items b, jsonb_object_keys(b.data) k
 where b.superseded_by is null and b.source_provider = 'sam_gov_site_api' and b.section <> 'people' and b.as_of >= date '2026-06-01'
 group by 1 order by 2 desc;

-- 2. Deadline fill on recent notices (posted since 2026-08-01), per agency
select a.abbreviation,
       count(*) filter (where b.data->>'responses_due' >= '2026-10-13') open_by_due,
       count(*) filter (where b.data->>'responses_due' is null and b.event_type in ('rfp_released','presolicitation_posted','rfi_released')) undated_notices,
       count(*) filter (where b.event_type in ('rfp_released','presolicitation_posted','rfi_released')) notices
  from agencies a
  join agency_brain_items b on b.agency_id = a.id and b.superseded_by is null
   and b.section in ('procurement_patterns','industry_engagement') and b.as_of >= date '2026-08-01'
 where a.abbreviation in ('DARPA','USAF','USA','USN','HHS','GSA','NASA','DOT','VA','NOAA','EPA','DHS','DOE')
   and b.source_provider = 'sam_gov_site_api'
 group by 1 order by 1;

-- 3. Open SAM notices: what the view row carries, and what the app's sam_opportunities copy and its linked
--    gov_contacts hold for the same notice id (neither is in any agent view)
with o as (
  select a.abbreviation ag, b.id, b.source, b.primary_organization_id org, b.event_type
    from agencies a
    join agency_brain_items b on b.agency_id = a.id and b.superseded_by is null and b.section in ('procurement_patterns','industry_engagement')
   where a.abbreviation in ('DARPA','USAF','USA','USN','HHS','GSA','NASA','DOT','VA','NOAA','EPA','DHS','DOE')
     and b.source_provider = 'sam_gov_site_api' and coalesce(b.event_type,'') not in ('contract_awarded','contract_modified')
     and b.data->>'responses_due' >= '2026-10-13'
), j as (
  select o.*, s.id sid, s.naics_code, s.set_aside_code, s.psc_code, s.place_of_performance,
         (select count(*) from gov_contact_opportunities co where co.opportunity_id = s.id) n_poc,
         (select count(*) from gov_contact_opportunities co join gov_contacts c on c.id = co.contact_id
           where co.opportunity_id = s.id and c.email is not null) n_poc_email,
         (select count(*) from gov_contact_opportunities co join gov_contacts c on c.id = co.contact_id
           where co.opportunity_id = s.id and nullif(c.phone,'') is not null) n_poc_phone
    from o left join sam_opportunities s on s.source = 'sam_gov' and s.source_ref = o.source->>'notice_id'
)
select coalesce(ag,'ALL') ag, count(*) open_n,
       count(*) filter (where org is not null) office,
       count(*) filter (where source->>'solicitation' is not null) solnum,
       count(*) filter (where source->>'type' is not null) ntype,
       count(sid) in_sam_table,
       count(*) filter (where nullif(naics_code,'') is not null) naics,
       count(*) filter (where nullif(psc_code,'') is not null) psc,
       count(*) filter (where nullif(set_aside_code,'') is not null) set_aside,
       count(*) filter (where nullif(place_of_performance,'') is not null) pop,
       count(*) filter (where n_poc > 0) poc,
       count(*) filter (where n_poc_email > 0) poc_email,
       count(*) filter (where n_poc_phone > 0) poc_phone
  from j group by rollup(ag) order by 1;

-- 4. Open SAM notices: contracting vs owner office, forecast tie and estimate, notice kind (needs side)
with o as (
  select a.abbreviation ag, b.agency_id, b.data->>'line' line, b.event_type, b.source->>'type' ntype
    from agencies a
    join agency_brain_items b on b.agency_id = a.id and b.superseded_by is null and b.section in ('procurement_patterns','industry_engagement')
   where a.abbreviation in ('DARPA','USAF','USA','USN','HHS','GSA','NASA','DOT','VA','NOAA','EPA','DHS','DOE')
     and b.source_provider = 'sam_gov_site_api' and coalesce(b.event_type,'') not in ('contract_awarded','contract_modified')
     and b.data->>'responses_due' >= '2026-10-13'
), j as (
  select o.*, n.id need_id, n.source nsrc,
         exists(select 1 from gov_need_organizations x where x.need_id = n.id and x.role = 'contracting_office') co_role,
         exists(select 1 from gov_need_organizations x where x.need_id = n.id and x.role = 'originating_requirement_owner') own_role,
         exists(select 1 from gov_funding_observations fo where fo.need_id = n.id) est
    from o
    left join lateral (
      select n.* from gov_needs n
       where n.source = any(array['federal-buyer-map-trial/datapack','federal-buyer-map-trial/data/raw/sam_notices',
               'federal-buyer-map-trial/data/raw/sam_notices_darpa','federal-buyer-map-trial/data/raw/sam_notices_army',
               'federal-buyer-map-trial/data/raw/sam_notices_airforce','federal-buyer-map-trial/data/raw/sam_notices_dhs',
               'federal-buyer-map-trial/data/raw/sam_notices_doe','federal-buyer-map-trial/data/raw/sam_notices_dot',
               'federal-buyer-map-trial/data/raw/sam_notices_epa','federal-buyer-map-trial/data/raw/sam_notices_gsa',
               'federal-buyer-map-trial/data/raw/sam_notices_hhs','federal-buyer-map-trial/data/raw/sam_notices_nasa',
               'federal-buyer-map-trial/data/raw/sam_notices_noaa','federal-buyer-map-trial/data/raw/sam_notices_va'])
         and n.source_key = o.line and n.agency_id = o.agency_id
       order by (n.source like '%datapack') desc limit 1) n on true
)
select coalesce(ag,'ALL') ag, count(*) open_n, count(need_id) with_need,
       count(*) filter (where nsrc like '%datapack') tied_to_forecast,
       count(*) filter (where co_role) contracting_office, count(*) filter (where own_role) owner_office,
       count(*) filter (where est) estimate,
       count(*) filter (where ntype ilike '%sources sought%' or event_type = 'rfi_released') rfi_like,
       count(*) filter (where ntype ilike '%solicitation%') solicitation_like,
       count(*) filter (where ntype ilike '%special%') special,
       count(*) filter (where event_type = 'justification_posted' or ntype ilike '%justification%') justification
  from j group by rollup(ag) order by 1;

-- 5. Forecast items: which stated fields each source carries (key presence)
select source_provider, string_agg(k || ':' || n, ', ' order by n desc) keys from (
  select b.source_provider, k, count(*) n
    from agency_brain_items b, jsonb_object_keys(b.data->'stated') k
   where b.superseded_by is null and b.section = 'forecast' and jsonb_typeof(b.data->'stated') = 'object'
   group by 1, 2) x
 group by 1;

-- 6. Forecast date formats as written (shape only)
select b.source_provider,
       regexp_replace(regexp_replace(coalesce(b.data->'stated'->>'award_date','<null>'),'[0-9]','9','g'),'[A-Za-z]+','A','g') award_pat,
       regexp_replace(regexp_replace(coalesce(b.data->'stated'->>'solicitation_date','<null>'),'[0-9]','9','g'),'[A-Za-z]+','A','g') sol_pat,
       count(*) n
  from agency_brain_items b
 where b.superseded_by is null and b.section = 'forecast' and b.data ? 'stated'
 group by 1, 2, 3 having count(*) > 20 order by 1, 4 desc;

-- 7. Live items vs distinct lines (DHS carries two live releases)
select a.abbreviation, b.source_provider, count(*) items, count(distinct b.data->>'line') lines
  from agency_brain_items b join agencies a on a.id = b.agency_id
 where b.superseded_by is null and b.section = 'forecast' and b.data ? 'stated' and a.abbreviation in ('DHS','USN','USA')
 group by 1, 2;

-- 8. Open forecast lines: field fill per agency
with it as (
  select distinct on (b.agency_id, b.data->>'line') a.abbreviation ag, b.agency_id, b.data->>'line' line,
         b.data->'stated' s, b.primary_organization_id org
    from agency_brain_items b join agencies a on a.id = b.agency_id
   where b.superseded_by is null and b.section = 'forecast' and b.data ? 'stated'
   order by b.agency_id, b.data->>'line', b.as_of desc
), nd as (
  select it.*, n.id need_id, n.lifecycle,
         (select rv.expected_to from gov_need_requirements rq join gov_requirement_revisions rv on rv.requirement_id = rq.id
            join gov_intelligence_assertions ia on ia.id = rv.assertion_id and ia.retracted_at is null
           where rq.need_id = n.id order by ia.observed_at desc nulls last limit 1) exp_to,
         (select rv.expected_from from gov_need_requirements rq join gov_requirement_revisions rv on rv.requirement_id = rq.id
            join gov_intelligence_assertions ia on ia.id = rv.assertion_id and ia.retracted_at is null
           where rq.need_id = n.id order by ia.observed_at desc nulls last limit 1) exp_from
    from it left join gov_needs n on n.agency_id = it.agency_id and n.source like '%datapack' and n.source_key = it.line
), o as (
  select nd.*,
         exists(select 1 from gov_need_organizations x where x.need_id = nd.need_id and x.role = 'contracting_office') co_role,
         exists(select 1 from gov_need_organizations x where x.need_id = nd.need_id and x.role = 'originating_requirement_owner') own_role,
         exists(select 1 from gov_funding_observations fo where fo.need_id = nd.need_id) est
    from nd
   where coalesce(lifecycle,'') not in ('fulfilled','cancelled') and (exp_to is null or exp_to >= date '2026-10-13')
), f as (
  select o.*, (select jsonb_object_agg(k, v) from jsonb_each_text(o.s) e(k, v)
                where v !~* '^\s*(tbd|to be determined|unknown|unknown/to be determined|n/?a|none|not applicable|currently, this information is not available|-+|\?|0|fyxxxx.*|xx+)?\s*\.?\s*$') g
    from o
)
select coalesce(ag,'ALL') ag, count(*) open_n,
       count(*) filter (where g ?| array['solicitation_date','award_date','award_quarter_as_written'] or exp_from is not null) next_date,
       count(*) filter (where g ?| array['solicitation_date','award_date','award_quarter_as_written']) next_date_stated,
       count(*) filter (where g ?| array['contracting_poc_name','requirement_contact','alternate_contact','secondary_poc_name']) poc_name,
       count(*) filter (where g ? 'procurement_method' and g->>'procurement_method' not in ('True','False')) setaside_named,
       count(*) filter (where g ? 'contract_vehicle') vehicle,
       count(*) filter (where g->>'follow_on_or_new' ~* 'follow|recompet|re-compet|existing' or g ?| array['incumbent_contractor','existing_contract_number']) recompete_n,
       count(*) filter (where (g->>'follow_on_or_new' ~* 'follow|recompet|re-compet|existing' or g ?| array['incumbent_contractor','existing_contract_number']) and g ? 'incumbent_contractor') recompete_w_incumbent,
       count(*) filter (where (g->>'follow_on_or_new' ~* 'follow|recompet|re-compet|existing' or g ?| array['incumbent_contractor','existing_contract_number']) and g ? 'existing_contract_number') recompete_w_contract_no,
       count(*) filter (where g ? 'anticipated_total_value') value,
       count(*) filter (where est) value_in_needs,
       count(*) filter (where g ? 'naics') naics,
       count(*) filter (where g ? 'psc') psc,
       count(*) filter (where co_role) contracting_office_role,
       count(*) filter (where own_role) owner_role,
       count(*) filter (where g ? 'place_of_performance') pop
  from f group by rollup(ag) order by 1;

-- 9. Open forecast lines: contracting office from either source, named vehicle, lines open only for want of a window
--    (same it / nd / o CTEs as 8, without exp_from and est)
-- select coalesce(ag,'ALL') ag, count(*) open_n,
--        count(*) filter (where co_role or nullif(btrim(s->>'contracting_center'),'') is not null) contracting_any,
--        count(*) filter (where s->>'contract_vehicle' ~* 'schedule|gsa|sewp|oasis|seaport|cio-sp|alliant|gwac|stars|polaris|t4ng|ites|rs3|eagle|fss|bpa') vehicle_named,
--        count(*) filter (where exp_to is null) no_window
--   from o group by rollup(ag) order by 1;

-- 10. Contact details stored on forecast items at all (an @ or a phone pattern anywhere in data)
select b.source_provider, count(*) n, count(*) filter (where b.data::text like '%@%') has_at,
       count(*) filter (where b.data::text ~ '\(\d{3}\) ?\d{3}-\d{4}|\d{3}-\d{3}-\d{4}') has_phone
  from agency_brain_items b
 where b.superseded_by is null and b.section = 'forecast' and b.data ? 'stated'
 group by 1 order by 1;

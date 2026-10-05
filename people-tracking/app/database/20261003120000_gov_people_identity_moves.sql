-- People identity across sources, dated positions from every source, one move ledger,
-- per-company relationships, and move alerts.

-- 1. Licensed-intelligence ledger tables. Production received these from sql/gov_intel_ingestion.sql
-- out of band; a local reset never did. Every statement is idempotent, so production is unchanged.
create table if not exists public.gov_intel_facts (
  id uuid primary key default gen_random_uuid(),
  record_id uuid not null references public.gov_intel_records(id) on delete cascade,
  fact_key text not null check (fact_key = btrim(fact_key) and fact_key <> ''),
  event_type text not null check (
    event_type in (
      'agency_priority', 'budget', 'procurement_pattern', 'forecast', 'rfi', 'solicitation', 'recompete',
      'award', 'personnel_move', 'vendor_move', 'merger_acquisition', 'contract_vehicle',
      'industry_engagement', 'editorial', 'other'
    )
  ),
  title text not null check (title = btrim(title) and title <> ''),
  body text not null default '',
  data jsonb not null default '{}'::jsonb check (jsonb_typeof(data) = 'object'),
  evidence jsonb not null default '{}'::jsonb check (jsonb_typeof(evidence) = 'object'),
  source_tier text not null default 'licensed_secondary'
    check (source_tier in ('official', 'licensed_secondary', 'derived', 'editorial')),
  confidence numeric(4,3) not null default 1 check (confidence >= 0 and confidence <= 1),
  reported_at timestamptz,
  effective_date date,
  valid_until timestamptz,
  status text not null default 'current'
    check (status in ('current', 'superseded', 'disputed', 'retracted', 'review')),
  conflict_status text not null default 'none'
    check (conflict_status in ('none', 'possible', 'confirmed', 'resolved')),
  superseded_by uuid references public.gov_intel_facts(id) on delete restrict deferrable initially deferred,
  extraction_version text not null default 'orange-slices-v1',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (record_id, fact_key),
  constraint gov_intel_facts_not_self_superseded_check check (superseded_by is null or superseded_by <> id)
);
create index if not exists gov_intel_facts_record_status_idx
  on public.gov_intel_facts (record_id, status, event_type);
create index if not exists gov_intel_facts_freshness_idx
  on public.gov_intel_facts (event_type, reported_at desc nulls last, valid_until);

create table if not exists public.gov_intel_links (
  id uuid primary key default gen_random_uuid(),
  fact_id uuid not null references public.gov_intel_facts(id) on delete cascade,
  agency_id uuid references public.agencies(id) on delete cascade,
  usa_award_id text references public.usa_awards(award_id) on delete cascade,
  sam_opportunity_id uuid references public.sam_opportunities(id) on delete cascade,
  gov_competitor_id uuid references public.gov_competitors(id) on delete cascade,
  gov_contact_id uuid references public.gov_contacts(id) on delete cascade,
  match_method text not null check (match_method in ('exact', 'alias', 'composite', 'fuzzy', 'manual', 'projected')),
  confidence numeric(4,3) not null check (confidence >= 0 and confidence <= 1),
  review_status text not null default 'auto'
    check (review_status in ('auto', 'confirmed', 'review', 'rejected', 'conflict')),
  match_basis jsonb not null default '{}'::jsonb check (jsonb_typeof(match_basis) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint gov_intel_links_one_target_check check (
    num_nonnulls(agency_id, usa_award_id, sam_opportunity_id, gov_competitor_id, gov_contact_id) = 1
  )
);
create unique index if not exists gov_intel_links_fact_agency_key
  on public.gov_intel_links (fact_id, agency_id) where agency_id is not null;
create unique index if not exists gov_intel_links_fact_award_key
  on public.gov_intel_links (fact_id, usa_award_id) where usa_award_id is not null;
create unique index if not exists gov_intel_links_fact_opportunity_key
  on public.gov_intel_links (fact_id, sam_opportunity_id) where sam_opportunity_id is not null;
create unique index if not exists gov_intel_links_fact_competitor_key
  on public.gov_intel_links (fact_id, gov_competitor_id) where gov_competitor_id is not null;
create unique index if not exists gov_intel_links_fact_contact_key
  on public.gov_intel_links (fact_id, gov_contact_id) where gov_contact_id is not null;

create table if not exists public.gov_contact_role_history (
  id uuid primary key default gen_random_uuid(),
  fact_id uuid not null references public.gov_intel_facts(id) on delete cascade,
  gov_contact_id uuid references public.gov_contacts(id) on delete set null,
  person_identity_key text not null
    check (person_identity_key = btrim(person_identity_key) and person_identity_key <> ''),
  name text not null check (name = btrim(name) and name <> ''),
  agency_id uuid references public.agencies(id) on delete set null,
  agency_name text not null check (agency_name = btrim(agency_name) and agency_name <> ''),
  component text,
  title text not null check (title = btrim(title) and title <> ''),
  event_type text not null check (event_type in ('appointment', 'promotion', 'acting', 'transfer', 'departure')),
  effective_date date not null,
  reported_at timestamptz not null,
  started_at date,
  ended_at date,
  source_provider text not null,
  source_url text not null check (source_url ~* '^https?://'),
  conflict_status text not null default 'none'
    check (conflict_status in ('none', 'possible', 'confirmed', 'resolved')),
  status text not null default 'current' check (status in ('current', 'retracted', 'superseded', 'review')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (fact_id, person_identity_key, title, effective_date),
  constraint gov_contact_role_history_dates_check check (ended_at is null or started_at is null or ended_at >= started_at)
);
create index if not exists gov_contact_role_history_current_idx
  on public.gov_contact_role_history (person_identity_key, effective_date desc)
  where ended_at is null and status = 'current';
create index if not exists gov_contact_role_history_agency_idx
  on public.gov_contact_role_history (agency_id, effective_date desc);

create table if not exists public.gov_intel_jobs (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  job_type text not null check (job_type in ('backfill', 'fetch', 'normalize', 'reconcile', 'publish', 'tombstone')),
  record_id uuid references public.gov_intel_records(id) on delete cascade,
  status text not null default 'queued' check (status in ('queued', 'running', 'complete', 'failed')),
  dedupe_key text not null check (dedupe_key = btrim(dedupe_key) and dedupe_key <> ''),
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  progress jsonb not null default '{}'::jsonb check (jsonb_typeof(progress) = 'object'),
  attempts integer not null default 0 check (attempts >= 0),
  available_at timestamptz not null default now(),
  lease_token uuid,
  claimed_at timestamptz,
  heartbeat_at timestamptz,
  lease_expires_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);
create unique index if not exists gov_intel_jobs_active_dedupe_key
  on public.gov_intel_jobs (dedupe_key) where status in ('queued', 'running');
create index if not exists gov_intel_jobs_claim_idx
  on public.gov_intel_jobs (provider, status, available_at, created_at);

create table if not exists public.gov_intel_sync_state (
  provider text primary key,
  cursor text,
  watermark timestamptz,
  backfill_from timestamptz,
  backfill_to timestamptz,
  backfill_status text not null default 'pending'
    check (backfill_status in ('pending', 'running', 'complete', 'failed')),
  last_attempt_at timestamptz,
  last_success_at timestamptz,
  consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
  metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(metrics) = 'object'),
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.gov_intel_facts enable row level security;
alter table public.gov_intel_links enable row level security;
alter table public.gov_contact_role_history enable row level security;
alter table public.gov_intel_jobs enable row level security;
alter table public.gov_intel_sync_state enable row level security;
revoke all on table public.gov_intel_facts, public.gov_intel_links, public.gov_contact_role_history,
  public.gov_intel_jobs, public.gov_intel_sync_state from public, anon, authenticated;
grant all on table public.gov_intel_facts, public.gov_intel_links, public.gov_contact_role_history,
  public.gov_intel_jobs, public.gov_intel_sync_state to service_role;

-- 2. Every identifier a source knows a person by. Many identifiers resolve to one contact, so a new
-- e-mail, a new FPDS office suffix or a vendor profile keeps the same person.
create table if not exists public.gov_contact_identifiers (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid not null references public.gov_contacts(id) on delete cascade,
  kind text not null check (
    kind in ('email', 'fpds_person', 'fpds_user', 'name_org', 'linkedin', 'dvids_name', 'username', 'vendor_profile')
  ),
  value text not null check (value = btrim(value) and value <> ''),
  source text not null check (btrim(source) <> ''),
  basis text not null default 'exact' check (basis in ('exact', 'derived', 'rule', 'agent', 'manual')),
  first_seen timestamptz not null default now(),
  last_seen timestamptz not null default now(),
  activity_first_on date,
  activity_last_on date,
  activity_count integer check (activity_count is null or activity_count > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint gov_contact_identifiers_kind_value_key unique (kind, value),
  constraint gov_contact_identifiers_seen_order check (first_seen <= last_seen),
  constraint gov_contact_identifiers_activity_order check (activity_first_on <= activity_last_on)
);
create index if not exists gov_contact_identifiers_contact_idx on public.gov_contact_identifiers (contact_id);
comment on table public.gov_contact_identifiers is
  'Identifiers that resolve to one canonical contact. basis records why the identifier was attached: exact (the source states it), derived (computed from another identifier), rule (a deterministic merge rule), agent (a reviewed judgment) or manual.';
comment on column public.gov_contact_identifiers.activity_count is
  'Source records that carry this identifier, with activity_first_on and activity_last_on their first and last record dates. For an FPDS account: each action it created or approved for the office its id names, dated by the signed date. A person''s FPDS accounts at two offices, used in turn, are how a staff move is observed.';
alter table public.gov_contact_identifiers enable row level security;
revoke all on table public.gov_contact_identifiers from public, anon, authenticated;
grant select, insert, update, delete on table public.gov_contact_identifiers to service_role;

-- 3. Positions from every people source. date_basis says whether valid_from and valid_to are dates a
-- source states or dates Chromie observed; first and last observation are kept apart from both.
alter table public.gov_contact_positions
  add column if not exists date_basis text not null default 'observed',
  add column if not exists first_observed_at timestamptz,
  add column if not exists last_observed_at timestamptz;
update public.gov_contact_positions
set first_observed_at = coalesce(first_observed_at, least(observed_at, coalesce(valid_from::timestamptz, observed_at))),
    last_observed_at = coalesce(last_observed_at, observed_at)
where first_observed_at is null or last_observed_at is null;
alter table public.gov_contact_positions
  alter column first_observed_at set default now(),
  alter column first_observed_at set not null,
  alter column last_observed_at set default now(),
  alter column last_observed_at set not null;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.gov_contact_positions'::regclass
                 and conname = 'gov_contact_positions_date_basis_valid') then
    alter table public.gov_contact_positions add constraint gov_contact_positions_date_basis_valid
      check (date_basis in ('stated', 'observed'));
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.gov_contact_positions'::regclass
                 and conname = 'gov_contact_positions_observation_order') then
    alter table public.gov_contact_positions add constraint gov_contact_positions_observation_order
      check (first_observed_at <= last_observed_at);
  end if;
end $$;
comment on table public.gov_contact_positions is
  'Temporal positions for canonical contacts from every people source (directories, notices, FPDS contracting staff, official releases, licensed vendors). date_basis applies to valid_from and valid_to: stated when the source states the date, observed when Chromie only saw the person in the post; first_observed_at and last_observed_at bound the observations.';

-- 4. The move ledger accepts moves from any source. A licensed fact keeps fact_id; any other source
-- names its record in source_ref and carries the quoted evidence.
alter table public.gov_contact_role_history alter column fact_id drop not null;
alter table public.gov_contact_role_history
  add column if not exists source_ref text,
  add column if not exists evidence jsonb not null default '[]'::jsonb,
  add column if not exists date_basis text not null default 'stated',
  add column if not exists organization_id uuid references public.gov_organizations(id) on delete set null,
  add column if not exists previous_title text,
  add column if not exists previous_agency_name text,
  add column if not exists previous_organization_id uuid references public.gov_organizations(id) on delete set null;
-- Existing rows come from a licensed feed that states its dates; new writers must say which basis they use.
alter table public.gov_contact_role_history alter column date_basis drop default;
-- An announcement is a stated move that has not happened yet (a nomination, an assignment release); its
-- effective_date is the day it was announced and started_at stays empty.
alter table public.gov_contact_role_history drop constraint if exists gov_contact_role_history_event_type_check;
alter table public.gov_contact_role_history add constraint gov_contact_role_history_event_type_check
  check (event_type in ('appointment', 'promotion', 'acting', 'transfer', 'departure', 'announcement'));
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.gov_contact_role_history'::regclass
                 and conname = 'gov_contact_role_history_source_identity') then
    alter table public.gov_contact_role_history add constraint gov_contact_role_history_source_identity
      check (fact_id is not null or (source_ref is not null and btrim(source_ref) <> ''));
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.gov_contact_role_history'::regclass
                 and conname = 'gov_contact_role_history_evidence_array') then
    alter table public.gov_contact_role_history add constraint gov_contact_role_history_evidence_array
      check (jsonb_typeof(evidence) = 'array');
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.gov_contact_role_history'::regclass
                 and conname = 'gov_contact_role_history_date_basis_valid') then
    alter table public.gov_contact_role_history add constraint gov_contact_role_history_date_basis_valid
      check (date_basis in ('stated', 'observed'));
  end if;
end $$;
-- Licensed rows leave source_ref null, and nulls stay distinct, so this key binds only monitor rows. It is
-- not partial so writers can upsert on it.
create unique index if not exists gov_contact_role_history_source_event_uq
  on public.gov_contact_role_history (source_provider, source_ref, person_identity_key, event_type);
create index if not exists gov_contact_role_history_contact_idx
  on public.gov_contact_role_history (gov_contact_id, effective_date desc);
create index if not exists gov_contact_role_history_organization_idx
  on public.gov_contact_role_history (organization_id, effective_date desc);
create index if not exists gov_contact_role_history_previous_organization_idx
  on public.gov_contact_role_history (previous_organization_id) where previous_organization_id is not null;

-- Where an office sits, from the office address its SAM notices carry. A licensed vendor profile joins a person
-- only when the place of its office experience agrees with this one.
alter table public.gov_organizations
  add column if not exists address_city text,
  add column if not exists address_state text;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.gov_organizations'::regclass
                 and conname = 'gov_organizations_address_valid') then
    alter table public.gov_organizations add constraint gov_organizations_address_valid
      check ((address_city is null or (address_city = btrim(address_city) and address_city <> ''))
             and (address_state is null or address_state ~ '^[A-Z]{2}$'));
  end if;
end $$;

-- 5. Per-company relationship layer over the shared contacts. Rows never cross companies: the policies
-- admit only members of the owning gov profile.
create table if not exists public.gov_person_relationships (
  id uuid primary key default gen_random_uuid(),
  gov_profile_id uuid not null references public.gov_profiles(id) on delete cascade,
  contact_id uuid not null references public.gov_contacts(id) on delete restrict,
  stance text not null default 'target'
    check (stance in ('target', 'engaged', 'champion', 'detractor', 'unknown')),
  owner_user_id uuid references public.profiles(id) on delete set null,
  notes text not null default '',
  created_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint gov_person_relationships_profile_contact_key unique (gov_profile_id, contact_id)
);
create index if not exists gov_person_relationships_contact_idx on public.gov_person_relationships (contact_id);
create index if not exists gov_person_relationships_owner_idx
  on public.gov_person_relationships (owner_user_id) where owner_user_id is not null;
create index if not exists gov_person_relationships_created_by_idx
  on public.gov_person_relationships (created_by) where created_by is not null;

create table if not exists public.gov_person_interactions (
  id uuid primary key default gen_random_uuid(),
  gov_profile_id uuid not null references public.gov_profiles(id) on delete cascade,
  contact_id uuid not null references public.gov_contacts(id) on delete restrict,
  happened_on date not null,
  kind text not null check (kind in ('meeting', 'call', 'email', 'event', 'note', 'other')),
  summary text not null check (btrim(summary) <> ''),
  gov_run_id uuid references public.gov_runs(id) on delete set null,
  created_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists gov_person_interactions_profile_contact_idx
  on public.gov_person_interactions (gov_profile_id, contact_id, happened_on desc);
create index if not exists gov_person_interactions_contact_idx on public.gov_person_interactions (contact_id);
create index if not exists gov_person_interactions_run_idx
  on public.gov_person_interactions (gov_run_id) where gov_run_id is not null;
create index if not exists gov_person_interactions_created_by_idx
  on public.gov_person_interactions (created_by) where created_by is not null;

create table if not exists public.gov_tracked_offices (
  id uuid primary key default gen_random_uuid(),
  gov_profile_id uuid not null references public.gov_profiles(id) on delete cascade,
  organization_id uuid not null references public.gov_organizations(id) on delete cascade,
  created_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default now(),
  constraint gov_tracked_offices_profile_organization_key unique (gov_profile_id, organization_id)
);
create index if not exists gov_tracked_offices_organization_idx on public.gov_tracked_offices (organization_id);
create index if not exists gov_tracked_offices_created_by_idx
  on public.gov_tracked_offices (created_by) where created_by is not null;

alter table public.gov_person_relationships enable row level security;
alter table public.gov_person_interactions enable row level security;
alter table public.gov_tracked_offices enable row level security;
revoke all on table public.gov_person_relationships, public.gov_person_interactions, public.gov_tracked_offices
  from public, anon, authenticated;
grant select, insert, update, delete on table public.gov_person_relationships, public.gov_person_interactions,
  public.gov_tracked_offices to authenticated, service_role;

drop policy if exists gov_person_relationships_member on public.gov_person_relationships;
create policy gov_person_relationships_member on public.gov_person_relationships for all to authenticated
  using (exists (select 1 from public.profiles profile
                 where profile.id = (select auth.uid()) and profile.gov_profile_id = gov_person_relationships.gov_profile_id))
  with check (exists (select 1 from public.profiles profile
                      where profile.id = (select auth.uid()) and profile.gov_profile_id = gov_person_relationships.gov_profile_id));
drop policy if exists gov_person_interactions_member on public.gov_person_interactions;
create policy gov_person_interactions_member on public.gov_person_interactions for all to authenticated
  using (exists (select 1 from public.profiles profile
                 where profile.id = (select auth.uid()) and profile.gov_profile_id = gov_person_interactions.gov_profile_id))
  with check (exists (select 1 from public.profiles profile
                      where profile.id = (select auth.uid()) and profile.gov_profile_id = gov_person_interactions.gov_profile_id));
drop policy if exists gov_tracked_offices_member on public.gov_tracked_offices;
create policy gov_tracked_offices_member on public.gov_tracked_offices for all to authenticated
  using (exists (select 1 from public.profiles profile
                 where profile.id = (select auth.uid()) and profile.gov_profile_id = gov_tracked_offices.gov_profile_id))
  with check (exists (select 1 from public.profiles profile
                      where profile.id = (select auth.uid()) and profile.gov_profile_id = gov_tracked_offices.gov_profile_id));

-- 6. Move alerts reuse the in-app notification table. A task notification still needs its run.
alter table public.gov_user_notifications alter column gov_run_id drop not null;
alter table public.gov_user_notifications drop constraint if exists gov_user_notifications_event_kind_check;
alter table public.gov_user_notifications add constraint gov_user_notifications_event_kind_check
  check (event_kind in ('task_assigned', 'person_moved'));
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.gov_user_notifications'::regclass
                 and conname = 'gov_user_notifications_task_run_required') then
    alter table public.gov_user_notifications add constraint gov_user_notifications_task_run_required
      check (event_kind <> 'task_assigned' or gov_run_id is not null);
  end if;
end $$;

-- 7. One person, one contact. When a rule proves two contacts are the same person (an FPDS-only contact and
-- the e-mail contact a notice or directory later supplies), every reference moves to the survivor in one
-- transaction; rows the survivor already holds win.
create or replace function public.gov_merge_contacts(p_survivor uuid, p_duplicate uuid)
returns void
language plpgsql
-- Runs as owner: service_role may not delete from every referencing table (grant contacts are append-only for
-- it), and a merge must clear the duplicate's colliding rows. Execute stays service_role only.
security definer
set search_path = ''
as $$
begin
  if p_survivor is null or p_duplicate is null or p_survivor = p_duplicate then
    raise exception 'gov_merge_contacts needs two different contacts';
  end if;
  perform 1 from public.gov_contacts where id in (p_survivor, p_duplicate) for update;

  delete from public.gov_contact_opportunities d using public.gov_contact_opportunities s
  where d.contact_id = p_duplicate and s.contact_id = p_survivor
    and s.opportunity_id = d.opportunity_id and s.relationship = d.relationship;
  update public.gov_contact_opportunities set contact_id = p_survivor where contact_id = p_duplicate;

  delete from public.grant_opportunity_contacts d using public.grant_opportunity_contacts s
  where d.contact_id = p_duplicate and s.contact_id = p_survivor
    and s.grant_opportunity_id = d.grant_opportunity_id and s.relationship = d.relationship;
  update public.grant_opportunity_contacts set contact_id = p_survivor where contact_id = p_duplicate;

  delete from public.gov_intel_links d using public.gov_intel_links s
  where d.gov_contact_id = p_duplicate and s.gov_contact_id = p_survivor and s.fact_id = d.fact_id;
  update public.gov_intel_links set gov_contact_id = p_survivor where gov_contact_id = p_duplicate;

  update public.gov_contact_positions s
  set first_observed_at = least(s.first_observed_at, d.first_observed_at),
      last_observed_at = greatest(s.last_observed_at, d.last_observed_at),
      updated_at = now()
  from public.gov_contact_positions d
  where s.contact_id = p_survivor and d.contact_id = p_duplicate and s.valid_to is null and d.valid_to is null
    and s.organization_id = d.organization_id and s.role_type = d.role_type and s.source = d.source
    and s.source_ref is not distinct from d.source_ref;
  delete from public.gov_contact_positions d using public.gov_contact_positions s
  where d.contact_id = p_duplicate and s.contact_id = p_survivor and s.valid_to is null and d.valid_to is null
    and s.organization_id = d.organization_id and s.role_type = d.role_type and s.source = d.source
    and s.source_ref is not distinct from d.source_ref;
  update public.gov_contact_positions set contact_id = p_survivor where contact_id = p_duplicate;

  update public.gov_contact_role_history set gov_contact_id = p_survivor where gov_contact_id = p_duplicate;
  update public.gov_contact_identifiers set contact_id = p_survivor, updated_at = now() where contact_id = p_duplicate;

  delete from public.gov_person_relationships d using public.gov_person_relationships s
  where d.contact_id = p_duplicate and s.contact_id = p_survivor and s.gov_profile_id = d.gov_profile_id;
  update public.gov_person_relationships set contact_id = p_survivor where contact_id = p_duplicate;
  update public.gov_person_interactions set contact_id = p_survivor where contact_id = p_duplicate;

  delete from public.gov_contacts where id = p_duplicate;
end;
$$;
revoke all on function public.gov_merge_contacts(uuid, uuid) from public, anon, authenticated;
grant execute on function public.gov_merge_contacts(uuid, uuid) to service_role;

begin;
select plan(27);

select has_table('public', 'gov_contact_identifiers', 'contact identifier table exists');
select has_table('public', 'gov_contact_role_history', 'move ledger is in the migration chain');
select has_table('public', 'gov_person_relationships', 'relationship table exists');

select ok(
  not has_table_privilege('authenticated', 'public.gov_contact_identifiers', 'SELECT'),
  'authenticated users cannot read identifiers directly'
);
select ok(
  not has_table_privilege('authenticated', 'public.gov_contact_role_history', 'SELECT'),
  'authenticated users cannot read the move ledger directly'
);
select ok(
  not has_table_privilege('anon', 'public.gov_person_relationships', 'SELECT'),
  'anon cannot read relationships'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.gov_person_relationships'::regclass),
  'relationships have RLS enabled'
);

insert into auth.users (id, email) values
  ('20000000-0000-4000-8000-000000000001', 'people-a@example.test'),
  ('20000000-0000-4000-8000-000000000002', 'people-b@example.test');
insert into public.gov_profiles (id, name) values
  ('20000000-0000-4000-8000-0000000000a1', 'People company A'),
  ('20000000-0000-4000-8000-0000000000b1', 'People company B');
insert into public.profiles (id, email, gov_profile_id) values
  ('20000000-0000-4000-8000-000000000001', 'people-a@example.test', '20000000-0000-4000-8000-0000000000a1'),
  ('20000000-0000-4000-8000-000000000002', 'people-b@example.test', '20000000-0000-4000-8000-0000000000b1');
insert into public.gov_contacts (id, identity_key, name, source) values
  ('20000000-0000-4000-8000-0000000000c1', 'fpds:jane.q.doe@navy.mil', 'Jane Q Doe', 'fpds_staff');
insert into public.gov_organizations (
  id, name, normalized_name, org_type, source, source_ref, jurisdiction_code, jurisdiction_path, government_level
) values (
  '20000000-0000-4000-8000-0000000000d1', 'N00024', 'n00024', 'contracting_office', 'fpds_office', 'N00024',
  'US', array['US'], 'federal'
);

-- Identity and position rules.
insert into public.gov_contact_identifiers (contact_id, kind, value, source)
values ('20000000-0000-4000-8000-0000000000c1', 'fpds_person', 'jane.q.doe@navy.mil', 'fpds_staff');
select throws_ok(
  $$insert into public.gov_contact_identifiers (contact_id, kind, value, source)
    values ('20000000-0000-4000-8000-0000000000c1', 'fpds_person', 'jane.q.doe@navy.mil', 'sam_awards')$$,
  '23505', null,
  'one identifier resolves to exactly one contact'
);
select throws_ok(
  $$insert into public.gov_contact_identifiers (contact_id, kind, value, source, activity_first_on, activity_last_on, activity_count)
    values ('20000000-0000-4000-8000-0000000000c1', 'fpds_user', 'jane.q.doe.n00024@navy.mil', 'fpds_staff',
            '2025-05-01', '2025-04-01', 3)$$,
  '23514', null,
  'account activity cannot end before it starts'
);
select throws_ok(
  $$insert into public.gov_contact_positions (contact_id, organization_id, role_type, source, date_basis)
    values ('20000000-0000-4000-8000-0000000000c1', '20000000-0000-4000-8000-0000000000d1',
            'contracting_officer', 'fpds_staff', 'guessed')$$,
  '23514', null,
  'a position date is either stated or observed'
);
select lives_ok(
  $$insert into public.gov_contact_positions (contact_id, organization_id, role_type, source, source_ref, date_basis)
    values ('20000000-0000-4000-8000-0000000000c1', '20000000-0000-4000-8000-0000000000d1',
            'contracting_officer', 'fpds_staff', 'N00024', 'observed')$$,
  'a non-directory source can write a position'
);

-- One move ledger for every source.
select throws_ok(
  $$insert into public.gov_contact_role_history (
      person_identity_key, name, agency_name, title, event_type, effective_date, reported_at,
      source_provider, source_url, date_basis
    ) values ('fpds:jane.q.doe@navy.mil', 'Jane Q Doe', 'Navy', 'Contracting Officer', 'transfer',
              '2026-03-01', now(), 'fpds_staff', 'https://www.fpds.gov/', 'observed')$$,
  '23514', null,
  'a move without a licensed fact must name its source record'
);
select lives_ok(
  $$insert into public.gov_contact_role_history (
      gov_contact_id, person_identity_key, name, agency_name, title, event_type, effective_date, reported_at,
      source_provider, source_ref, source_url, date_basis, organization_id, evidence
    ) values ('20000000-0000-4000-8000-0000000000c1', 'fpds:jane.q.doe@navy.mil', 'Jane Q Doe', 'Navy',
              'Contracting Officer', 'transfer', '2026-03-01', now(), 'fpds_staff', 'move:N00104->N00024',
              'https://www.fpds.gov/', 'observed', '20000000-0000-4000-8000-0000000000d1', '[]'::jsonb)$$,
  'a monitor move with a source record is accepted'
);
select throws_ok(
  $$insert into public.gov_contact_role_history (
      person_identity_key, name, agency_name, title, event_type, effective_date, reported_at,
      source_provider, source_ref, source_url, date_basis
    ) values ('fpds:jane.q.doe@navy.mil', 'Jane Q Doe', 'Navy', 'Contracting Officer', 'transfer',
              '2026-03-02', now(), 'fpds_staff', 'move:N00104->N00024', 'https://www.fpds.gov/', 'observed')$$,
  '23505', null,
  'a rerun of the same source move does not add a second row'
);
select lives_ok(
  $$insert into public.gov_contact_role_history (
      person_identity_key, name, agency_name, title, event_type, effective_date, reported_at,
      source_provider, source_ref, source_url, date_basis
    ) values ('name:jane q doe|agency:department of the navy', 'Jane Q Doe', 'Department of the Navy',
              'Commander, Naval Sea Systems Command', 'announcement', '2026-09-17', now(), 'war_gov_releases',
              'https://www.war.gov/News/Releases/Release/Article/1/', 'https://www.war.gov/', 'stated')$$,
  'an announced move that has not happened yet is accepted'
);

-- Move alerts need no run; task alerts still do.
select lives_ok(
  $$insert into public.gov_user_notifications (
      gov_profile_id, user_id, event_key, event_kind, title, recipient_email
    ) values ('20000000-0000-4000-8000-0000000000a1', '20000000-0000-4000-8000-000000000001',
              'person_moved:test', 'person_moved', 'Jane Q Doe moved', 'people-a@example.test')$$,
  'a move alert is stored without a run'
);
select throws_ok(
  $$insert into public.gov_user_notifications (
      gov_profile_id, user_id, event_key, event_kind, title, recipient_email
    ) values ('20000000-0000-4000-8000-0000000000a1', '20000000-0000-4000-8000-000000000001',
              'task:test', 'task_assigned', 'Task', 'people-a@example.test')$$,
  '23514', null,
  'a task alert still requires its run'
);

-- A rule-proven duplicate folds into the survivor with its history.
insert into public.gov_contacts (id, identity_key, name, source, email) values
  ('20000000-0000-4000-8000-0000000000c2', 'email:jane.q.doe.civ@us.navy.mil', 'Jane Doe', 'sam_gov',
   'jane.q.doe.civ@us.navy.mil');
select ok(
  not has_function_privilege('authenticated', 'public.gov_merge_contacts(uuid, uuid)', 'EXECUTE'),
  'only the service role can merge contacts'
);
select lives_ok(
  $$select public.gov_merge_contacts('20000000-0000-4000-8000-0000000000c2', '20000000-0000-4000-8000-0000000000c1')$$,
  'an FPDS-only contact merges into the e-mail contact'
);
select is(
  (select count(*) from public.gov_contact_identifiers
   where contact_id = '20000000-0000-4000-8000-0000000000c2' and kind = 'fpds_person'),
  1::bigint,
  'the survivor carries the merged FPDS identifier'
);
select is(
  (select count(*) from public.gov_contact_positions p
   join public.gov_contact_role_history h on h.gov_contact_id = p.contact_id
   where p.contact_id = '20000000-0000-4000-8000-0000000000c2'),
  1::bigint,
  'positions and moves follow the survivor'
);

-- Tenant isolation, enforced by the database.
insert into public.gov_person_relationships (gov_profile_id, contact_id, stance) values
  ('20000000-0000-4000-8000-0000000000a1', '20000000-0000-4000-8000-0000000000c2', 'champion'),
  ('20000000-0000-4000-8000-0000000000b1', '20000000-0000-4000-8000-0000000000c2', 'detractor');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"20000000-0000-4000-8000-000000000001","role":"authenticated"}', true);

select is(
  (select array_agg(stance) from public.gov_person_relationships),
  array['champion'],
  'a member sees only their company relationship'
);
select throws_ok(
  $$insert into public.gov_person_relationships (gov_profile_id, contact_id)
    values ('20000000-0000-4000-8000-0000000000b1', '20000000-0000-4000-8000-0000000000c2')$$,
  '42501', null,
  'a member cannot write into another company'
);
select lives_ok(
  $$insert into public.gov_person_interactions (gov_profile_id, contact_id, happened_on, kind, summary)
    values ('20000000-0000-4000-8000-0000000000a1', '20000000-0000-4000-8000-0000000000c2',
            '2026-09-30', 'meeting', 'Industry day follow-up')$$,
  'a member logs an interaction for their company'
);
select is(
  (select count(*) from public.gov_person_relationships
   where gov_profile_id = '20000000-0000-4000-8000-0000000000b1'),
  0::bigint,
  'another company relationship is invisible even when asked for by id'
);
select lives_ok(
  $$insert into public.gov_tracked_offices (gov_profile_id, organization_id)
    values ('20000000-0000-4000-8000-0000000000a1', '20000000-0000-4000-8000-0000000000d1')$$,
  'a member tracks an office for their company'
);

reset role;
select throws_ok(
  $$update public.gov_organizations set address_state = 'Virginia' where id = '20000000-0000-4000-8000-0000000000d1'$$,
  '23514', null,
  'an office place is a two-letter state code'
);
select * from finish();
rollback;

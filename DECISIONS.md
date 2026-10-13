# Decisions

Add dated technical and product decisions here.


## 2026-10-09 - Agents read production through the `agent` views

- **Fact.** The owner applied `research/sql/agent_views.sql` to production on 2026-10-09 in the SQL editor (no login on
  this box may create a schema or role). Read back through the read-only MCP user: six views, `agent_reader` holds
  select, anon/authenticated/service_role hold nothing on the schema; the security advisor names nothing in it.
- **Fix.** `budget_lines.placed_office` tested `org_type = 'agency'`; production types the agency's own organization
  `other`, so 2,820 Navy lines read as placed in "Department of the Navy". The rule is now
  `existing_agency_id = agency id`, checked on the local copy (Link 16 block reads agency level); the owner ran the
  view again the same day and production reads the same split (Navy 12,413 agency level, 423 in an office).
- **Assumption.** Office placements kept only in research files (`office_owners.json`) are not in production, so a
  pursuit from production names no budget holder for a line at agency level; it says "not placed in an office".
- **Budget statements.** 2,218 agency budget items in production carry no book columns: congressional directives (HHS 401,
  Navy 375, VA 224, ...), funding changes whose amounts are quoted strings ("$14B"), and app-written summary figures
  in fields of their own shape and unit (HHS, VA, SBA, CMS). `agent.budget_statements` shows them with `body` and the
  item's own fields as written; nothing is converted to $M, since the units differ per item. Every agency budget item
  sits in exactly one of the two budget views (checked on the local copy; 294 items with no agency stay out of all views).

## 2026-10-12 - Agent views show e-mail addresses and phone numbers

- **Owner, 2026-10-12:** "we now have to use emails contacts everything". `agent.names_only` is removed from
  `research/sql/agent_views.sql`: titles, bodies, needs and excerpts show addresses and numbers as the record holds them.
  The paste file `research/sql/agent_views_v2.sql` is regenerated; `names_only.sql` stays cancelled.
- **Checked.** `research/evals/agent_views_v2_local_test.sql` on `dhs_proof`, rolled back: every `*_left` and `*_bad`
  is 0; 4 items and 13 excerpts carry a contact (DHS was built names only, so its text holds few).
- **Production.** Applied 2026-10-12 at the owner's request, through the Management API (`build/pursuit_cisa/run_sql.py`,
  a rolled-back run first). Eight views, all readable by `agent_reader`; the file's checks pass (GSA 344 of 345 posts
  titled, NOAA statements 0 local paths, GSA forecast needs all dated, NIH needs over 1,000).

## 2026-10-12 - E-mails back in production, and on each person in the agent views

- **Owner, 2026-10-12:** "touch prod data emails are needed"; name matches "use, labelled".
- **View.** `agent.people_readings` gains `email`, `phone`, `contact_basis`: our contact's own address by id, else a
  contact from another source (SAM, the app) with the same name at the same agency and one address only, labelled
  "same name at the same agency, from <source>" (a match, not a stated link). Agencies are matched on the agency's name,
  its "X, DEPARTMENT OF" form or "DEPT OF THE X" inside the contact's agency text, only for agencies with readings.
- **Data.** HHS, DHS, DOT and EPA were built names only. A person's id comes from the raw key before the digest, so
  `prod-load-test/correction/emails_back.py` reran the people merge with addresses kept and set `gov_contacts.email`
  by id where null on our rows (identity keys untouched): HHS 1,413, DHS 1,188, DOT 650, EPA 262. Rehearsed on each
  local copy, then a rolled-back run on production, then applied. People with an e-mail in the view: DHS 844 of 2,006,
  HHS 876 of 1,867, DOT 409 of 592, EPA 153 of 205.
- **Resolved below.** The builder still defaulted to names only and captured no phone; flipping it changed people items for every
  agency, so it waited on a local rebuild compare (done: EPA rebuilt identical).

## 2026-10-12 - The people builder writes every address and phone on the person, and stored text stays as loaded

- **Owner, 2026-10-11/12:** "emails, contacts everything". `research/tools/people.py` now writes each person's
  `emails` and `phones` (a SAM.gov point of contact's phone field; an old notice's single field holding several contacts
  gives no one its phone), and `emit_contacts` writes `gov_contacts.phone`.
- **Assumption.** Passages and identity keys stay names only (`KEEP_EMAILS` still False): rows already loaded rebuild
  identical, Apollo answers keyed by the person key still match, and the plan never meets a changed row. The contact
  row carries the address and phone, and `agent.people_readings` shows them. Forecast e-mail and phone columns are not
  read yet: an address there would change how forecast contacts merge, so person ids would move.
- **Checked.** people selfcheck (Navy) and agency_layers_sql selfcheck ok; tests/test_people*.py 10 passed. EPA rebuilt:
  323 people, same ids, keys, positions and offices as before; 262 with an e-mail, 246 with a phone.


## 2026-10-12 - Phones in production contacts, and a people view that answers a whole agency in seconds

- **Owner, 2026-10-12:** approved both. 13,795 of our production contacts got a phone (`NNN-NNN-NNNN`, several joined
  by "; ") and 11 an e-mail, written only into empty fields on rows whose source is ours. Files from
  `prod-load-test/correction/contacts_back.py`, counted read-only on production and rehearsed on local copies first;
  a recount after shows nothing left to fill.
- **View.** The one-address-per-agency-and-name match was a joined subquery the planner ran again for every person, so a
  whole-agency count with e-mail timed out at 120 s. It is now a materialized CTE, with the name key computed once per
  row: DHS, 2,205 rows, in about 4 s. Applied 2026-10-12 through `build/pursuit_cisa/run_sql.py`.

## 2026-10-12 - Pursuit skill: every "not held" line says what the source shows

- **Benchmark.** Record and web in one agent (`research/evals/benchmark_both_1012.md`): all 10 answers actionable, 1 of
  10 passed every check, 8 failed on a bare "not in the record".
- **Rule.** Say what was read for the item and what it shows: a blank, a printed "--", a continuing resolution column,
  a section not loaded, a notice held as a title only, a figure not yet published. DoD SBIR and STTR contacts come from
  the DSIP listing, not aggregators. A continuing resolution column is never called enacted.
- **Assumption.** A gap with no reason reads as a missing fact the vendor cannot act on; the grader's demand matches
  the reader's.

## 2026-10-12 - agent.items shows each statement's stored fields, and evidence keeps to live rows

- **Found.** Production items already hold, in their own fields, notice response dates (NASA GSMO-4, 2026-10-26),
  grant amounts, recipients and awarding sub-agency, contract PIID, vendor, offices and end date, and SBIR topic
  dates; no view showed them, so answers called them missing. The deadlines and grant office asks were view gaps.
- **Change.** `agent.items` appends `facts_as_written` (the item's fields, keys as written), and the skill reads
  deadlines and award fields from it. `agent.evidence` keeps to live rows and appends agency, department and
  office_path, like every other view. View comments no longer say contacts are names only (removed 2026-10-12, entry above).
- **Still open.** Contract rows hold no dollar amount; the USAspending award detail call fills it
  (`research/evals/usaspending_api_1012.md`), not a reload.
- **Production.** Applied 2026-10-13 at the owner's request after a rolled-back dry run; checked through the MCP server.

# 21 - Job postings as a signal: announcements as dated observations about hiring

Implemented in `research/tools/jobs.py`, loaded by `agency_layers_sql.emit_hiring`, read by `trace.py need`.
Companion to `research/docs/13_news_as_a_signal.md`, whose discipline this follows, and to
`research/docs/08_org_memory_format.md`, which it follows rather than extends.

## What a posting is

A vacancy an office announces is a source statement like a notice, a forecast row or an article: the
agency says, in public and on a date, what work it is staffing for, in which series and grade, at which
site, and from when. So an announcement enters the record the same way: the bytes are fetched and recorded
in `research/sources/documents_manifest.jsonl` with their retrieval date and SHA-256, and the model is built
from those bytes, never from a summary of them.

A posting is an observation about hiring. It is never a relationship, never authority and never a hire made.
Nothing it says changes the alias table, the ancestry walk, a need's office or who leads anything; a claim is
promoted only with the usual evidence, or it stays where it is. What differs from an article is what the
source is: an announcement is the agency's own words on the government's own recruiting site, so every
posting is an official announcement of high reliability, and the reading has one more question to answer,
whether the vacancy is an intention or an action.

## Where postings live

USAJobs carries every competitive-service announcement of the federal government. Two of its addresses
answer this machine without a key, from a browser user agent (a bare client is refused with 403):

- the historic announcement API, `data.usajobs.gov/api/historicjoa`, lists every announcement an agency code
  opened inside a date window, 500 to a page with a continuation token: control number, announcement number,
  title, series, pay scale and grades, salary, openings, opening and closing dates, sites, hiring paths,
  clearance, and the opening status as of the day it is read. A window with no announcement answers 204, and
  that answer is recorded, so a negative names the search it rests on;
- the announcement page, `usajobs.gov/job/<control number>`, is served as HTML with the summary, the duties,
  the requirements and the qualifications, and stays up after the announcement closes with a closed banner.

USAJobs files every announcement under a department code and an agency code of its own. The profile
(`agency.py`, key `hiring`) maps the codes of the commands the layer studies to the memory node each names:
NAVWAR is NV39, NAVSEA NV24, NAVAIR NV19, ONR NV14 and SSP NV30; DARPA is DD13; Air Force Materiel Command
is AF1M and Space Systems Command AF6S; the Army's Acquisition Support Center is ARAE, its Secretariat ARSA,
and TACOM, AMCOM and CECOM are ARX7, ARX6 and ARX8. The codes were read off the department listings on
2026-09-27 (`HiringDepartmentCodes=NV` lists every Navy code with its name). The mapping is a profile
assumption recorded in `DECISIONS.md`, not a stored fact; the subelement the listing states ("Information
Warfare Center Pacific; Communications & Network, Code 55000") is resolved against the memory's aliases like
any other text. The search API (`data.usajobs.gov/api/search`) needs a key and is not used. The agencies'
own careers pages sit on `.mil` hosts that refuse this address, and are recorded as an access finding.

## The record

`python research/tools/jobs.py build` writes `research/events/hiring_observations.json` (under
`research/agencies/<key>/events/` for another profile): the searches read, one entry each with its window,
hash and count, and one entry per announcement:

| field | what it holds |
| --- | --- |
| `id`, `control_number`, `announcement_number`, `title`, `url` | the announcement as USAJobs identifies it |
| `hiring` | the department, agency and subelement as the listing writes them, the agency code, and `org`, the memory node the profile maps the code to, with `org_basis` saying so |
| `series`, `career_field`, `acquisition_workforce` | the occupational series, and whether it is one of the acquisition workforce's (contracting, program management, engineering, business, logistics, information technology, quality) |
| `grade`, `salary`, `openings`, `appointment_type`, `work_schedule`, `supervisory`, `telework_eligible`, `security_clearance`, `who_may_apply`, `hiring_paths`, `locations` | the listing's fields, in the listing's words |
| `opened`, `closes`, `status_as_listed`, `listed_at`, `listings` | the opening and closing dates, the opening status as of the day the listing was read, and every listing that named this control number with its date, status and hash |
| `reading` | `status` (announced, under review, closed, selected, cancelled, not stated), `posture` (intent, action, withdrawn), `flyer` (a public notice flyer of anticipated vacancies), `leader_role`, and the sentence that says what the posting is |
| `record`, `announcement` | the saved listing page (path, SHA-256, method, retrieval time) and the saved announcement page, or null when the page was not taken |
| `entities` | organizations (through the memory's alias table), programs, contracts, solicitations, forecast lines, budget lines, places |
| `links` | the offices, forecast requirements, solicitations and awards the record already holds |
| `claims` | the listing itself first, then one per duties sentence that states something, each with its exact passage |
| `relation` | new signal, corroborates or conflicts, for the posting as a whole |
| `loads` | whether the loader takes it: the acquisition workforce, or a posting whose words name an office beyond the command it is filed under, or a requirement, contract or solicitation the record holds |
| `verify` | what has to be confirmed |

Each claim carries its own `statement_type` (vacancy, placement, stand_up, workload, existence), the passage
it rests on, the entities it names, its relation to the stored record with the reason, a confidence and its
own list of what to verify. The listing claim's passage is the title, and its `fields` are the listing's
fields verbatim, so a reader can hold the record to the saved JSON.

## How a claim is read against the record

- The listing claim is compared with the memory node the profile maps the agency code to. A node the
  memory holds corroborates: the organization exists and is hiring. A code the profile does not map, or maps
  to a node the memory lacks, is a new signal.
- A placement claim ("This position is located in the PMW 160 office, which reports to PEO C4I") is read as
  an article's parentage claim is: the same stored parent corroborates, a different stored parent conflicts,
  and the conflict is reported, not resolved; an office with no stored parent is a new signal.
- A workload claim naming a contract, a solicitation number or a forecast line the record holds corroborates
  that requirement: the office is staffing for work the record already knows.
- A stand-up claim ("the newly established portfolio") is compared with the offices the memory holds.
- Everything else is a new signal, which means untested, not true.

Confidence starts high, since the announcement is the agency's own, and falls where the page is a public
notice flyer of anticipated vacancies ("there may or may not be actual vacancies filled from this flyer"), and
again where the passage hedges. A statement of intent is never read as an action taken: an announcement is
an intention to hire while its status is accepting applications, under review or closed; only "Candidate
selected" is an action stated; "Job canceled" is a withdrawal. A vacancy for a leader's role (program manager,
director, deputy) says who is sought and never who leads: where the memory holds someone leading the office
named, the claim asks the reviewer to confirm whether the seat is open, and asserts nothing.

Three readings are separated because they read alike and mean different things:

- The agency name is the code's word, not the listing's. USAJobs prints "Commander, Naval Information
  Warfare Systems Command (NAVWARSYSCOM)" on every NV39 announcement, so the command reaches the record
  through the profile's mapping and is not read as an entity of the text; the subelement and the page are.
- A capitalised title is not a list of program codes. USAJobs writes "ADMINISTRATIVE SPECIALIST (CONTRACT
  SPECIALIST PAE Robotic and Autonomous Systems)", so the title is read for offices through the alias table
  and never for the codes a forecast row would share; the pay systems, regulations and hiring authorities
  every announcement names (DAWIA, FAR, DFARS, APS, CTAP) are not programs either.
- The conditions of employment and the education rules are about employment, not about the office's work,
  and are cut before the sentences are read; the duties, the qualifications and the additional information
  are the office's own words.

## Collection

- `jobs.py sweep` lists every announcement opened in the last 180 days under each agency code the profile
  maps, page by page, recording every answer (a 204 included); with `--fetch` it takes the announcement
  pages of the acquisition workforce's vacancies and of any listing whose words name an office the memory
  knows, up to `--limit` a run. A child-care or a lodging vacancy is recorded from the listing alone.
- The `vacancies` stage of the pipeline is the sweep; the `hiring` stage is the build. A profile that maps
  no code skips both and says so.
- Nothing in this document needs a key. A refusal by the API's front end is recorded and the build reads
  what is saved; an API is not a page the hosted browser or context.dev can render, so no fallback applies.
- The first posting was worked by hand before any sweep: the NAVWAR listing for 2026-03-31 to 2026-09-27
  (210 announcements) and the page of control number 880882900, a DP-3 contract specialist for the Robotic
  and Autonomous Systems portfolio, fetched with `fetch.py` and read with `jobs.py read 880882900`. Its
  duties name RAS, and the record holds three FY26 PAE RAS forecast lines under PMS 406, so the posting
  corroborates them; the page is a flyer of anticipated vacancies, so no claim reads high.

## What is loaded

`agency_layers_sql.emit_hiring` writes one brain item per posting that loads, in the people section, dated
the day it opened, with event type `vacancy_posted` (program stage, neutral polarity: an intention to hire
moves no buy by itself), filed under the office the posting's own words name where the memory knows it, else
the command the profile maps its code to; the listing's fields, the reading and the links travel in the
item's data, and one evidence row per claim carries the passage. No assertion, no relationship, no person and
no lifecycle change is loaded from a posting, including a conflicting one: the conflict is the finding, and
it is reported, not resolved. The frozen corpus reads the items as the `hiring` family, the pulse counts it
among a cell's families, and `trace.py need` shows the postings whose words name the requirement's office or
its program.

## Vendor postings

USAJobs sees the government hiring. The other side of the same signal is a contractor posting a role that names
an office, a program, a contract or a solicitation the record holds ("contracts manager supporting NAVWAR",
"SETA for DARPA BTO"): an incumbent staffing the work it already has, or a challenger positioning for it. Since
2026-09-28 `research/tools/vendor_jobs.py` collects it as a second collector of the hiring family, beside the
USAJobs one and never in its place.

- **Discovery is not evidence.** One query per company goes through RouterGrowth's `company.jobs` capability
  (`research/tools/routergrowth.py`; the schema is read from `/v1/inspect` and saved once a day, every run answer
  is saved and recorded). The answer is a list of addresses: it is the search a negative rests on, and nothing in
  it is quoted. The bytes a record quotes are the company's own careers page, or its applicant system's page
  carrying the company's name in the host or the path (Workday, Greenhouse, Lever, iCIMS and the like), fetched
  first-hand through `news.retrieve` with the note `vendor job: <company> <title>` and saved with its hash. A
  result on an aggregator or a social network (Indeed, LinkedIn, Glassdoor, ZipRecruiter) is recorded as a pointer
  with the reason it was not read, and is never fetched. Hosts match by their own labels (`careers.l3harris.com`
  is L3Harris's; `notlinkedin.com` is not LinkedIn), never by a substring.
- **Who is asked.** The incumbents with live awards at the contracting offices the profile sweeps, most live awards
  first (`research/memory/vendors.json`, by UEI; ten a run), and any company the profile names in
  `hiring.vendor_watch`. The query is the company's name as a search reads it, the word jobs, and the offices it
  holds awards at. A company with two spellings or two UEIs is asked under its canonical name and matched on any
  spelling; records are never merged across UEIs.
- **What a posting is.** A company's statement about the work it wants to staff: `source_type` "company
  statement", reliability medium (news.RELIABILITY), never high beside an agency's own announcement. The first
  claim is the page's title, read as corroboration where the record holds live awards to the company at the swept
  offices and as a new signal where it does not. Each further claim is a sentence of the page that names an office,
  program, contract or solicitation the record holds, quoted verbatim, read against the memory with the same
  questions an article is asked. A sentence that places the contractor's seat in an office is read as `support`
  (the company supports that office), never as parentage. A hedge lowers the claim to low.
- **What loads.** A posting whose own words name an office beyond the agency, or a requirement, contract or
  solicitation the record holds, and that states a posting date (from the page's JobPosting data, else the
  router's date, and the record says which). A title alone loads nothing: the source is a company's, not the
  buyer's. `agency_layers_sql.emit_vendor_hiring` writes one brain item in the vendors and incumbents section, at the
  editorial tier, with event type `vendor_vacancy_posted` (engagement stage, neutral polarity), filed under the
  office the posting names, never under the vendor, and one evidence row per claim. No assertion, relationship,
  person or lifecycle is loaded. The frozen corpus reads the items as the `hiring` family.
- **Cost and the first run.** A run is drawn from a prepaid wallet (from 0.0035 USD on 2026-09-28); the sweep passes
  `--max-cost` where the schema takes it and stops when the answers' reported cost passes `--budget` (0.50 USD by
  default); a price the answer does not state is recorded as null. The registry row `vendor_jobs_routergrowth` is
  written before any call as `not_inspected`, so the collector exists as an empty one rather than as claimed
  coverage, and the first posting is worked by hand (`sweep --company <UEI> --results 5`, then `--fetch --take 1`,
  `build`, `read`) and recorded in `DECISIONS.md` before any sweep.
- **What the first run found (2026-09-28).** For L3Harris (38 live awards at NAVWAR HQ) the capability's two live
  providers answer from LinkedIn: the keyword NAVWAR matched nothing (free); the keyword "contracts", asked by name and
  again by domain, returned five LinkedIn addresses each (apify, 0.0072 USD a run), none of them the company's own
  page. So the searches are recorded, the ten addresses are pointers, nothing was read, and no posting was modelled.
  The collector stands as built; until a route returns company-site addresses (the PredictLeads domain route was not
  live), a sweep buys negatives. The router answer's `description` field is the provider's copy of LinkedIn's text and
  is not evidence.

| reading of a vendor posting | yes or no |
| --- | --- |
| a company is staffing a role that names an office, program or contract the record holds | yes, a dated observation; corroboration of the incumbency where the record holds live awards |
| a company supports an office | a `support` claim to check against the office's own notices and awards, never a relationship |
| an award was made, or a requirement exists | no; a company's page states its own intention |
| who a company employs, or who holds a role | no; a posting says who is sought |
| anything from an aggregator or a social network | no; a pointer only |

## What a posting can and cannot be read as

| reading | yes or no |
| --- | --- |
| an office exists and is active on a date | yes, corroboration of the memory node |
| an office is staffing for a program, contract or requirement the record holds | yes, corroboration of that requirement, dated the day the vacancy opened |
| an office is building capacity in a field (contracting, engineering, IT) at a site | yes, a new signal, dated |
| a position sits in an office under another | a claim to check against the stored parents, never a relationship |
| a person holds a role, or has authority | no; a vacancy says who is sought, never who leads |
| a purchase will follow | no; hiring is capacity, not a requirement |
| a hire was made | only where the listing states "Candidate selected" |

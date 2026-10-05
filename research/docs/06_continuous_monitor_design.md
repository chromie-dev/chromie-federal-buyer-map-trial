# 06 - Continuous monitor: design, schedules, change detection, review, metrics, sample alerts

This document separates the reusable core from the Navy-specific adapters, names the existing
The platform tables each part would use, and gives four sample alerts built from evidence gathered in
this package.

## In plain terms

The monitor watches six kinds of source on their own clocks, keeps every document it reads with
a hash and two dates (when it was published, when we fetched it), notices when a row or page
changes in a way that matters, ties the change to a program office through the same evidence
rules used for the worked examples, and hands anything uncertain to a person. Most of the
machinery is generic; what is Navy-specific is a short list of adapters (the Annex 25 spreadsheet,
the PEO and command web pages, the budget-book exhibits) and the alias table of office codes.

## Architecture at a glance

```
 OFFICIAL SOURCES                REUSABLE CORE                          PLATFORM TABLES
 ----------------                -------------                          --------------
 LRAE spreadsheets  ---+                                            
 (NAVWAR, NAVSEA, ONR) |     +------------------+   manifest rows    gov_procurement_sources
 SAM.gov notices    ---+---> | FETCH and RECORD | ----------------->  gov_procurement_documents
 (daily extract,       |     | direct / archive |   url, dates,       agency_brain_documents
  site API)            |     | / US browser     |   sha256, failures
 FPDS ATOM,         ---+     +--------+---------+
 USAspending           |              |
 Budget tables      ---+              v
 (P-1, R-1, DoN books) |     +------------------+
 govinfo, congress  ---+     | EXTRACT          |  rows by PID, PDF pages,
 Org pages, tear    ---+     | (LRAE parser,    |  exhibit lines, HTML text
 sheets, DVIDS               |  exhibit parser) |
                             +---+----------+---+
                                 |          |
                 +---------------+          +------------------+
                 v                                             v
      +---------------------+                      +---------------------+
      | ORGANIZATION GRAPH  |  dated nodes/edges,  | SIGNALS             |
      | + alias table       |  reorg releases      | new LRAE row,       |
      | (PMW codes, LRAE    |--------------------->| funding change,     |
      |  HQ codes, NIWC     |  ancestry as of date | forecast revision,  |
      |  competency codes)  |                      | recompete           |
      +----------+----------+                      +----------+----------+
                 |  gov_organizations,                        |  gov_intel_facts,
                 |  gov_organization_relationships            |  agency_brain_items
                 v                                             v
      +---------------------+                      +---------------------+
      | ATTRIBUTION         |  evidence class       | ALERTS              |
      | evidence-gated      |  per action:          | what changed, office|
      | resolver:           |  direct / inferred /  | + ancestry, evidence|
      | award text, notice  |  ambiguous /          | uncertainty, why it |
      | text, LRAE row,     |  unresolved           | matters             |
      | tear-sheet map      |                       +----------+----------+
      +----------+----------+                                 |
                 |  gov_procurement_organizations,            |
                 |  gov_intel_links (review_status)           |
                 v                                             v
      +---------------------------------------------------------------+
      | REVIEW QUEUE: inferred / ambiguous / unresolved attributions,  |
      | new organization codes, conflicting official statements,      |
      | single-source alerts, stale or failing sources                |
      +---------------------------------------------------------------+

 Navy-specific: LRAE (Annex 25) parser, P-40/R-2 exhibit parser, alias table, list of
 organization URLs, host fallback rules (archive capture -> US browser -> manual).
 Everything else is reusable across agencies.
```

## 1. Shape

| Layer | Reusable core | Navy-specific adapter |
| --- | --- | --- |
| Source registry | `gov_procurement_sources` rows with `access_mode`, `refresh_cadence`, `verification_status`, `last_verified_at`, `known_access_gaps` | the 29 records in `research/sources/source_registry.json` |
| Fetch and preserve | one fetch-and-record helper (direct, archive capture, United States page render, browser, manual) writing a manifest row per retrieval, including failures and firewall stubs | fallback order per host (Wayback, context.dev for pages, Browserbase for files, manual) |
| Extract | PDF page-addressable text; spreadsheet row-addressable with a stable key; HTML text with link list | Annex 25 parser (header on row 6 or 7, PID as key); P-40/R-2 exhibit parser (line item, P-1 line, related PE, cost profile); tear-sheet parser (office, programs, PM) |
| Organization graph | `gov_organizations` + `gov_organization_relationships` with validity dates and provenance | alias table for PMW/PMA/PMS codes, LRAE HQ codes, NIWC competency codes; reorg-release reader |
| Attribution | evidence-gated resolver (`program_office_resolver.py`) producing an evidence class and abstentions; edges in `gov_procurement_organizations` with review status | Navy tear-sheet program-to-office map; LRAE existing-contract join |
| Signals and alerts | `gov_intel_facts` (event type, effective date, conflict status, superseded by) + `gov_intel_links` (office, award, notice, review status) | Navy alert rules below |
| Review | queue keyed on evidence class, value and novelty | reviewers who know NAVWAR |

## 2. Collection

Initial historical load, once:

| Source | Load | Key |
| --- | --- | --- |
| FPDS ATOM | all actions for N00039, N66001, N65236 from FY2019 (SPAWAR to NAVWAR rename) forward, paged by signed-date month | PIID + modification |
| USAspending | award detail for every PIID seen in FPDS; keyword search for each office alias | generated award id |
| SAM.gov | the full public extract once (about 230 MB), then the daily file | notice id + modified date; solicitation number for lineage |
| LRAE | current NAVWAR, NAVSEA, ONR releases plus every archived version that can be found | PID number, with row hash |
| Budget books | FY2019-FY2027 DoN OPN BA2 and RDT&E BA7-8 (and BA4-5 for development lines); enacted-year books for comparison | appropriation + line item / PE + fiscal year |
| Congress | govinfo packages for each year's NDAA and Defense appropriations (reports, explanatory statements, laws) | packageId; funding-table row |
| DVIDS | unit pages for NAVWAR and PEO C4I back to the earliest listed release | article id |
| Organization pages | current pages and tear sheets plus archived captures | URL + content hash |

Incremental schedules:

| Source | Cadence | Trigger for a fetch |
| --- | --- | --- |
| SAM daily extract | nightly | file `dateModified` changes |
| FPDS ATOM | daily | new actions since the last signed date per office; note the ~90-day DoD publication delay |
| USAspending | weekly, plus on demand for awards in an alert | award `last_modified_date` |
| DoD contracts RSS | daily | new item |
| DVIDS unit pages | daily | headline list diff |
| LRAE hosts | monthly, and weekly in May-July and December (release seasons observed) | new file link or file hash change |
| Organization pages and tear sheets | weekly | content hash change |
| Budget books | at the President's Budget release, then monthly for amendments; reprogramming actions when published | new file or hash change |
| govinfo / congress.gov | daily | new package matching the tracked bills |
| Federal Register | weekly | new document naming a tracked organization |
| DSIP topics (Navy, Army, DARPA, Air Force rows) | monthly, first Wednesday (observed on the Navy FY2026 pre-release days; the annual BAA of 2026-04-13 is the exception); swept by `prerelease.py sweep` (stage `prerelease`) | a new pre-release day on the index pages, listed by the sweep as `new_days` and read by `build`; reserved event type `sbir_topics_released`, not yet in `EVENT_TYPES` |
| Programme portals (navysbir.com, armysbir.army.mil, darpa.mil small business, darpaconnect.us, afwerx.com, spacewerx.us, diu.mil) | monthly with the DSIP day, weekly for the prize and CSO pages | landing page hash change; a refusal is recorded, never worked around |

Standing versus episodic (stated here, applied by nothing yet): a topic or a prize with a close date is episodic and
expires on it; a standing BAA, a community page or a rolling CSO has no close and stays Live until its page says
otherwise. The registry's `instrument.standing` carries the reading per source (`docs/01`).

## 3. Documents

- Every retrieval writes a manifest row: original URL, final URL, method, retrieval time, HTTP
  status, MIME type, size, SHA-256, and for archive captures the capture timestamp. Failures and
  firewall stubs are rows too (`content_status: rejected_stub`), so a missing document is visible.
- Publication date is stored separately from retrieval date: the LRAE "Release Date" cell, the
  P-40 "Date: April 2026", a release's dateline, a tear sheet's distribution statement. Where a
  page carries no date, the archive capture timestamp is the "available by" date.
- Large PDFs are extracted page by page (page number kept with every passage); spreadsheets row by
  row with the stable key; a new hash of the same URL creates a new version and supersedes the old
  one rather than replacing it. Raw bytes stay in object storage; the platform's
  `gov_procurement_documents` / `agency_brain_documents` hold the metadata and status.
- Size and safety limits: bounded file size, no macro execution, per-host rate limits and
  timeouts, resume for large books (the FY2027 OPN book needed a resumable download).

## 4. Detecting meaningful change and deduplicating

| Source | Unit | Meaningful change | Noise to ignore |
| --- | --- | --- | --- |
| LRAE | row by PID | new row; removed row; change in requirement office, value range, solicitation or award quarter, procurement method, existing contract, incumbent, follow-on/new | POC name or phone changes (recorded, not alerted) |
| SAM | notice lineage by solicitation number | first notice for a solicitation; type change (sources sought to solicitation); amendment that changes dates, set-aside or scope; award notice | re-posts with identical content |
| FPDS / USAspending | award by PIID | new award; option exercise; ceiling increase above a threshold; period-of-performance extension; description change | administrative modifications (COR change, clause updates, payment office) |
| Budget books | line item / PE | request differs from prior enacted beyond a threshold; new line; zeroed line; narrative names a new program | rounding |
| Congress | funding-table row | mark differs from request; directive language naming a tracked program | none |
| Organization pages | page | office added or removed; parent or name changed; leadership change | styling |
| DVIDS | release | leadership change; industry event; reorganization; program milestone | unrelated units |

Deduplication: one lineage per solicitation number across notice types (the platform already tracks
notice amendments); one award per PIID with modifications as children; one forecast requirement
per PID across LRAE releases; one organization per canonical id with aliases.

## 5. Keeping ownership mappings current

See `research/docs/02_organization_map.md` section 5. In monitor terms: a hash change on an organization page
or a reorganization release opens a review item; approved changes add dated edges and never
delete history; unknown codes seen in the LRAE or award text enter the alias table as
`unverified` and block automatic attribution until confirmed.

## 6. Source health

Each source carries `verification_status`, `last_verified_at`, consecutive failure count and the
last successful hash. Rules: a source that fails three consecutive fetches, or has not changed for
twice its expected cadence, is flagged stale; an HTTP 403 with a bot-protection body is classified
"blocked" and routed to the next fallback path; a 200 whose body is a firewall stub is classified
`rejected_stub`, not success. Flags surface in the review queue with the last good retrieval date.

## 7. Human review

Queue entries: every `inferred`, `ambiguous` or `unresolved` attribution above a value threshold;
every new organization code; every conflicting official statement; every alert whose evidence is a
single source. Reviewers confirm, correct or reject; confirmed results become reviewed examples
(gold candidates) and alias-table entries.

## 8. Metrics

| Metric | Definition | Where it comes from |
| --- | --- | --- |
| Coverage | share of NAVWAR HQ (and NIWC) actions in the window with an attribution or a reasoned abstention; share of PEO C4I LRAE rows tracked to a notice or award | FPDS, LRAE, attribution table |
| Attribution accuracy | precision by evidence class against reviewed examples; abstention correctness | review queue outcomes |
| Alert usefulness | reviewer rating per alert; share acted on | review queue |
| Advance notice | days between the earliest recorded signal and the first notice or award for the same requirement | backtests |
| Source health | share of sources verified within cadence | source registry |

## 8a. Alerting posture: recall first

Alerts name the most likely office and the most likely public contacts even when the evidence is
`inferred` or `ambiguous`, with the confidence label attached. A false lead costs a redirect; a
missed lead costs the opportunity. Suppression is limited to duplicates and administrative
modifications; uncertainty is shown, not filtered.

## 9. Sample alerts

Format: what changed; affected office and ancestry; evidence; uncertainty; why it matters.

**A. New requirement (LRAE row appears)**
- What changed: a new NAVWAR LRAE row "NILE In-Service Support (ISS) 6 Contract" with requirement
  office `PMW-150`, anticipated solicitation FY26 Q2, award FY27, existing contract N0003922D1004,
  incumbent Technology Unlimited Group, PID `N00039-25-RFPREQ-PMW-150-0067`.
- Office: PMW 150 Naval Command and Control Systems -> PEO C4I -> (from 2026-05-11) PAE Mission
  Systems; contracting office NAVWAR HQ N00039.
- Evidence: LRAE release 2025-06-19 (archive capture 2026-01-08); FPDS record of N0003922D1004.
- Uncertainty: forecast only; quarter and value range are estimates; no notice yet.
- Why it matters: a follow-on to a known incumbent contract with a stated quarter gives a
  capture team two to three quarters of warning.

**B. Funding change (request vs enacted)**
- What changed: OPN 1810N BA2 line item 2915 "CANES" (P-1 line 64, related PE 0303138N): FY2026
  enacted 534.324, FY2027 request 493.046 ($ millions), a decrease of about 41 million; the
  out-years return to the 505-533 range.
- Office: PMW 160 Tactical Networks (CANES is a PMW 160 program per the 2025 tear sheet); the
  budget line itself names no office, so the link is an inference by program.
- Evidence: FY2027 OPN BA2 justification book, P-40 exhibit dated April 2026 (pages 645-658);
  P-1 detail page.
- Uncertainty: enacted FY2027 figures will differ; the line funds many contracts and the effect on
  any one procurement is not observable publicly.
- Why it matters: a lower request year against a larger prior enacted amount changes quantities
  and timing for CANES production and installation buys in FY2027.

**B2. Funding change, congressional variant (House mark differs from request)**
- What changed: H. Rept. 119-715 (2026-06-26) recommends 543,046 for OPN line item 2915 CANES (P-1 line 64) against a
  request of 493,046 ($ thousands), a $50.0M program increase labeled "maritime containerized
  secure units"; RADIAC (line 65) is cut by $14.95M for contract award delays.
- Office: PMW 160 (CANES) by program inference; PEO C4I -> PAE Mission Systems.
- Evidence: govinfo HTML text of the report, table row and committee note.
- Uncertainty: House position only; the Senate report and the enacted act decide.
- Why it matters: an add for a named configuration signals a buy the request did not contain,
  a year or more before any notice.

**C. Forecast revision (LRAE row changes between releases)**
- What changed (the 2024-to-2025 release diff in `datapack/lrae_navwar_2025-06/` shows quarters and fiscal years move most often on matched PEO C4I rows): row PID
  `N00039-24-RFPREQ-PMW-160-0002` "PMW 160 Engineering Support Services (ESS) Follow-on" moves
  anticipated solicitation from FY25 Q4 to a later quarter, or its value range changes.
- Office: PMW 160 -> PEO C4I -> PAE Mission Systems.
- Evidence: the two LRAE versions with release dates and hashes; the row diff.
- Uncertainty: the LRAE is an estimate; a slip may reflect an acquisition-strategy change or a
  clerical update.
- Why it matters: slips and value changes move capture timelines and signal strategy changes.

**D. Potential recompete**
- What changed: order N0003922F3000 (Booz Allen, PEO C4I engineering support "IN SUPPORT OF
  PROGRAM MANAGER, WARFARE TACTICAL NETWORKS (PMW 160)") reaches its period-of-performance end on
  2026-10-26; the LRAE forecast a follow-on award in FY26 with the same incumbent; the predecessor
  order N0003917F3000 ran 2016-2021, so the requirement recurs on a five-year cycle.
- Office: PMW 160; contracting office NAVWAR HQ; vehicle SeaPort-NxG.
- Evidence: USAspending award detail; FPDS feeds for both orders; LRAE row.
- Uncertainty: the follow-on may already be awarded and not yet published (DoD publication lag);
  the vehicle may change.
- Why it matters: a recompete of a five-year support order is the most predictable event in the
  portfolio and the one a competitor can prepare for earliest.

## 11. Reusable versus Navy-specific, and expansion

Reusable without change: fetch-and-record, document versioning, FPDS/USAspending/SAM/govinfo/
Federal Register/DVIDS adapters, the organization graph, the resolver, the review queue, the
metrics. Reusable across the Navy: the Annex 25 LRAE parser (NAVWAR, NAVSEA, ONR/NRL and other
activities publish the same template) and the P-40/R-2 exhibit parser (all DoN books). Navy- and
portfolio-specific: the alias table, the tear-sheet program map, the list of organization URLs,
and the host fallback rules. Expansion order: other PAE Mission Systems components (PEO Digital,
PEO MLB, PEO IWS) using the same LRAE and FPDS adapters; NAVSEA and NAVAIR portfolios using their
LRAEs; other military departments by swapping the budget-book parser's exhibit conventions;
civilian agencies through the existing Agency Brain per-agency ingest pattern.

## 12. Oversight reports as findings

GAO and the inspectors general state, in dated documents, what a command cannot do yet, what slipped and what
it agreed to fix. The oversight chain (finding, problem, affected program, remediation, the acquisition
need that may follow) is read by an agent, not a rule: `research/tools/oversight.py` makes one structured call
per report (`llm.py`, gpt-5.4-mini, strict JSON schema, low reasoning) and records it as a cassette under
`research/cassettes/`, so a rebuild replays for free and `extract --check` fails when a replay would change
the file. Rules check the agent afterwards and drop what they cannot verify: the evidence span, the program,
organization and remediation names must sit on the saved text verbatim (after one flattening both sides see:
one space, straight quotes, ASCII hyphens); a money figure must be inside the span; the event type must be one of
the schema's; the source authority is the registry's word for the host, never the model's. Organizations are
linked through the memory's aliases, from the event's own words first and the report's opening pages second.

Connector: oversight.gov's federal listing for the queries `Navy` and `Naval` (a relevance search, so a date cut
applies after the page is read; investigations of a named person are left), the report page and its PDF, and the
GAO report feed; gao.gov product pages come through context.dev because the site refuses this address, and older
GAO products are found by search discovery (Exa, `includeDomains gao.gov`, `discover --fetch`). Pipeline stages:
`audits` (network) and `oversight` (agent, replays). Loader: one `narrative` item per report with findings, one
`narrative` item per finding carrying `event_type`, `published_at` (the issue date), `official` tier, the registry
provider and the linked organization, and one evidence row per finding with the passage.

## 13. Leaders' words as events: speeches, testimony, conferences

`research/tools/remarks.py` reads all three as one family, on the same agent-and-rules pattern as
the oversight family (section 12), with `reader.py` holding the rules both use. Connectors: the navy.mil speech
and testimony archives (index pages and articles through context.dev, since the site refuses this address),
the House Committee Repository feeds for Armed Services and Appropriations (direct and keyless: hearing page,
witness panel, witness statement PDFs; the feed's plain-http links are requested as https because the redirect
answers a shell), and Exa discovery for conference pages that name Navy officials (direct fetch, context.dev
on refusal, a Cloudflare challenge stays unread). The agent states, per document, who spoke, in what role, at
which event and host, to which audience, and the events: a capability named as a priority, a strategy stated,
industry asked, an official's appearance at a named event, a congressional directive, a funding change, a
program delayed, created or cancelled. The rules keep an event when the passage, the program, organization and
person are on the flattened text verbatim; the speaker, role, venue and host fields are kept as the text writes
them or blanked. A conference page is `editorial` tier and `third_party` authority; the government's own words
are `official` and `first_party`. Loader: one item per document, one per event, one evidence row per event;
pipeline stages `podium` (network) and `remarks` (agent, replays). Every event carries the document's date and
the record says where it came from: a navy.mil article's own "Presented on" field (the dateline is the place),
a hearing page's stated date, and for a conference page its meta or JSON-LD date, else the search index's date,
else the retrieval date (`date_basis`).

## 10. Alert C, implemented

`python research/tools/monitor_forecast_revision.py` produces alert C from the loaded
agency-intelligence tables; the pipeline's `revisions` stage writes it to `build/forecast_revisions.txt` on every
build. One `psql` read, no network, no file diffing: the
supersession chain already records which revision replaced which, so a line that never
moved produces nothing.

Against the NAVWAR, NAVSEA and ONR releases it reports every award window that moved,
slips and pull-forwards alike. Each alert carries the five parts section 9 specifies.
One example, abridged:

> **N00039-23-RFPREQ-PMW/A-170-0173**: All SATCOM Multi Award Contract (C)
> - What changed: anticipated award slips FY24 Q2 -> FY27 Q2 between the 2023-06-20
>   and 2024-06-20 releases.
> - Office: PMW/A 170 Communications and GPS Navigation Program Office. Ancestry:
>   PMW/A 170 -> PAE Mission Systems Capability Portfolio Executive (Comms, Sensor, EW
>   and PNT) -> Portfolio Acquisition Executive Mission Systems -> Department of the Navy.
> - Evidence: the two spreadsheet rows, each with its release and source hash.
> - Uncertainty: the LRAE is an estimate; a move may be strategy or a clerical
>   correction and the release does not say which.
> - Why it matters: the award window is what a capture timeline is built on.

It also checks the other half of alert C, a value range that changed for the same
fiscal period. A later release that restates the identical range produces nothing, so
a re-publication is not reported as news; a changed range is, such as the MIDS
Follow-On estimate for FY27 moving from $0.0M-$2.0M to $50.0M-$100.0M between the
2025-06-19 and 2026-07-13 releases. Releases write one fiscal year as "FY27" or as
"2027", so an estimate's lineage is read from the parsed year and quarter, never from
the text as written.

Two details of the implementation. The ancestry walk has to
climb `parent_organization_id` **and** then look for a documented successor of each
office on that path: the 2026-05-11 reorganisation is a `successor_to` edge, not a
parent, so a parent-only walk reports the pre-reorganisation chain as though nothing
had happened. And a fiscal quarter has to be read back from the calendar date rather
than the calendar quarter, or every October-to-December award window is reported a
year early.

## 14. Temporal engine, weekly pulse and actions

`research/tools/pulse.py` reads the frozen back-test corpus and its labels, never a live pull, and treats every
cell (an office and the names and capability terms one requirement goes by: the labelled outcomes and the 309
forecast rows the pilot offices own) the same way. A cell's score as of a date has four named parts, each 0 to 1,
weighted 40/20/20/20 and summed in decimal arithmetic so the stored score recomputes exactly from the stored parts
(T4.2, T4.3): families, the distinct document families among the events available by that date,
over five; recency, one at 0 days and zero at 365 for the newest event; persistence, how many of the last eight
calendar quarters carried an event; and procurement proximity, 1.0 for a notice in the last year or an incumbent
ending within two years, 0.5 for a forecast row touched in the last year, halved when a forecast revision moved
the solicitation or award date later (the slip flag). Families
and quarters drive the score, never event counts, so five events from one speech weigh as one (T4.4), and with no
new events the score never rises as the clock moves (T4.5). Each scored cell lists one or two events per family.

The pulse for a week is exactly the events that became available in it, per office, in the counting register
("PMW 740: 1 event across 1 family; rfp released: 1") and never a forecast of a release (T5.4). Actions come from
a closed list (meet office, attend event, research program, find partner, monitor forecast, watch expiration,
track person) and every row points at the events behind it (T7.1, T7.2): a cell with three families asks for a
meeting with one event per family attached; a forecast row touched in the last year is monitored; an incumbent
ending within two years is watched; an appearance this quarter is an event to attend. Every row carries its cell's
rank as its priority, and a watch carries the day the contract ends, so the queue reads dated actions first, soonest
first, then the rest by rank (`pulse.py actions`).

Over the swept corpus: 915 cells; every one of the top 50 holds five families or more and seven hold six (PMW 150's
Link 22 COMSEC devices row among them: budget, congress, forecast, incumbent, notice and a research topic), and the
top cell is NAVSEA's Aegis combat system integration and testing (forecast, incumbent, notice, an organization page
and a research topic); 883 actions (438 meet office, 284 watch expiration, 86 find partner, 63 monitor forecast,
9 attend event, 3 research program), one row per action, office and evidence, so cells sharing one expiring contract
watch it once; the last week carried 34 events. `pipeline.py` stages `backtest` and `pulse` replay and recompute both
files and fail on drift, and `tests/test_pulse.py` checks both on the real files.

## 15. People, money and programs; coverage and the pulse's reports

People, money and programs each come from a connector or a merge with no judgment in it, and three reporting
fields sit on the pulse without touching its score.

People (T2.4). `research/tools/people.py` merges every named person the saved sources carry into
one file: the primary and secondary points of contact on every SAM.gov notice detail (402 observations), the
contracting and secondary contacts on the forecast rows (97), the speakers of the navy.mil speeches and the
witnesses of the House hearings (57), and the officials the conference pages name (9). Two observations are one
person when they share an e-mail address, else when the normalised name and the office agree; a person the
organization memory already holds keeps the memory's contact id. Every position carries the office it was resolved
to, the date of the document, the source, its reference and a confidence from a fixed table by source. A SAM.gov
point of contact follows the office the notice names when it names exactly one, else the notice's organization; a
speaker follows the office the event's organizer states, else the Department. 565 observations give 173 people with
564 dated positions, loaded into gov_contacts and gov_contact_positions on the schema's role vocabulary. Contacts
attach to the meet-office action, never to the evidence and never as a scoring family: 312 of 315 meetings name
whom the record ties to the office. The same action carries the routes in from the contact recommendations: the
program manager on the requirement side, the contracting points of contact on the office's forecast rows on the
acquisition side, and the published channels (an office mailbox, the small business office, an intake portal), for
the office itself before its parents, each dated by the observations it rests on and marked when the review log shows those observations checked
against the saved file. The office page lists them too. The small business office of the department and of each
command comes from the Department of War's directory of small business offices and the office page it links to, only
the lines of that page that carry an address or a telephone number kept as written; an office with no small business
office of its own or above it reaches the department's. `match` and `prep` show it beside the first routes, since it is
a small company's first contact at a command.

Money. `research/tools/budget.py` reads the FY2027 Other Procurement, Navy budget activity 2
justification book with pdftotext in layout mode, splits pages on the form feed, and takes each Exhibit P-40 as one
P-1 line item with the twelve resource-summary columns the book prints. A line whose FY2027 total differs from
FY2026 is a funding_change event and a flat line a budget_line; the event date is the end of the book's month
(2026-04-30), the office is the program office the justification prose names when it names one, else the
Department, and the prose is the evidence text. 48 line items (45 changes) load as the budget family; MIDS sits in
line 2614 and CANES in 2915 and 2925.

Programs. `research/tools/sbir.py` reads the DoD SBIR/STTR portal's public topics API. Every SBIR
host refuses this machine's address and the portal refuses the hosted browser's request context, so each page is
fetched from inside a loaded portal page; the API ignores every filter and honours the sort, so the sweep reads all
components newest first and stops at the FY2020 window (seven index pages of 500), then saves the detail of every
Navy topic in the window. A topic is an sbir_topic event dated the day it was first shown to industry (the
pre-release date), under the program office its text names, else the sponsoring command (NAVSEA, NAVAIR, NAVWAR),
else the Department: 1,100 Navy topics since FY2020, 70 of them naming a program office. Since 2026-09-28 (SBIR
portals Stage 2) every topic row also carries, verbatim from the saved index row and detail: the solicitation number
and release, the phase set parsed from the portal's phase hierarchy, the Q&A window, the ITAR flag, the CMMC level and
the focus areas, plus two readings labelled with their basis: the instrument (the code's Z letter is the BAA, X the
commercial solutions opening, the legacy "DoD SBIR 20xx.x" titles the BAA, else unknown) and the entry (title words
for SBIR XL, xTech and Open Topic; then the phase set, D2 without 1 being Direct to Phase II and 1 with D2 either;
then the code's DV or NP letters; then the legacy -D suffix; else unknown). `entry_type` joins the two
(`baa:direct_to_phase_ii`); the header counts by entry type, instrument and focus area. Catapult, Strategic
Breakthrough, CATALYST and Prize-to-Contract are never derived from DSIP. The ceiling is the topic's own sentence with
a dollar amount, else null. The Navy `topic_re` reads the FY2026 `DON26BZ06-DV088` form beside `N251-001`; NAVFAC,
MCSC and MARCOR resolve to their commands (`sbir_commands`), so 55 topics that fell to the Department now sit under
`command:navfac` and `command:mcsc`. A frozen topic carries its close date as `closes` (not `due`; a topic's close is
not a requirement's deadline).

Pre-release days. `research/tools/prerelease.py build` (pipeline stage `releases`, offline) groups the component's
saved index rows by pre-release day, solicitation number and release into `events/prerelease_observations.json`, one
row per day with the topic count, the first codes, the newest saved page as evidence and `matches_rule`: the day's
weekday, which of that weekday in the month it is, and whether it is a first Wednesday. That is a labelled comparison,
never a verdict: the annual BAA's Monday (2026-04-13, three solicitations) is kept and says so. Nothing is loaded from
it; the reserved `sbir_topics_released` event stays out of `EVENT_TYPES` until a live sweep (Stage 3) has something to
compare against. Navy 47 days since FY2020, 20 on a first Wednesday and every FY2026 day but the annual BAA's; Army
109 (33), DARPA 83 (16), Air Force 81 (34): the monthly rule is the FY2026 Navy release rhythm, not the portal's history.

The sweep (Stage 3, 2026-09-28). `prerelease.py sweep --fetch` (pipeline network stage `prerelease`, after `topics`)
takes every registry row whose cadence is monthly and lists pages, computes the cycle's due day from the rule (the most
recent first Wednesday on or before today; the rule's exceptions are documentation, not the calendar), and for each page
asks the ledger whether any attempt was recorded since that day, refusals included. A page with none is fetched once
through `fetch.py` and the answer appended; a 403 or a stub is the recorded answer and is not retried inside the cycle,
never worked around. The DSIP row also lists its newest index page (size 62, the largest the API accepted); when a saved
copy of this cycle exists, whatever took it, its pre-release days not yet in the observations file are listed as
`new_days`, loaded by nothing until `build` runs. `events/prerelease_sweep.json` is the record. The first sweep on
2026-09-28 (cycle due 2026-09-02) kept the index page of 2026-09-26 and the armysbir.army.mil landing page of the same
morning, and recorded three refusals: the portal's topics-app shell and the two inner navysbir.com pages answered 403 to
a bare client, as the landing page had. No new pre-release day: the cycle's page holds only 2026-09-02.

Coverage and status (T1.5). `research/sources/coverage_matrix.json` is a hand-written grid of five
commands (NAVSEA, NAVAIR, NAVWAR, ONR, NIWC) by twelve families; a cell names the registered sources that cover it
or states why none does, from a closed list (no public source, blocked, restricted, not started) with a note.
`research/tools/coverage.py check` refuses a source the registry lacks, a source that has collected nothing, an
empty cell without a reason, or a hole in the grid. `coverage.py status` writes
`research/sources/source_status.json`: per registered source the events in the frozen corpus, the newest event date, the documents on disk, the newest retrieval and its hash; hosts
the registry does not own are listed, not hidden (four one-off pages). The registry carries a row for the news
articles the search sweep finds.

The pulse's reports. Each scored cell also states which families first appeared in
the last 90 days (novel_families), how many events landed in the last quarter against the quarter before
(momentum), and which vendors hold its contracts by count of incumbent awards (vendors, from the frozen rows'
vendor field). None of the three is a score part: every score recomposes from
the four named parts. As of the corpus end, 45 of 467 cells gained a family in the last 90
days, 64 carry more events this quarter than last, and 326 name an incumbent vendor.

## 16. One vocabulary for events; the card and the office book

Vocabulary. Every frozen event carries a stage and a polarity, set at freeze by
`research/tools/vocabulary.py` from its type and its statement. Ten stages: strategy, budget, program, research,
engagement, forecast, market research, solicitation, award, execution; the procurement path runs forecast, market
research, solicitation, award. Polarity is positive, negative or neutral. A forecast that slipped, a justification for
other than full and open competition, a bridge or extension modification, a delay and a cancellation are negative; a
funding line whose amounts fall by half or to zero is negative and one that dips is neutral; the amounts are read from
the statement, not the narrative, because the P-1 justification prose names cuts elsewhere in the account. The corpus
holds 6,752 positive, 197 neutral and 81 negative statements: 40 justifications, 24 forecast slips, 10 delays, 4
renewals, 2 cuts and 1 cancellation. A cell's stage is its newest non-negative path event within two years, else
shaping when the record spoke outside the path, else dormant; `next` names the milestones after that stage; `for`
counts the statements for it and `against` lists the negative statements of the last year, and any statement against
halves the proximity part, which generalises the slip rule of section 14. Among the top fifty cells, forty stand at
solicitation, five at market research, three at forecast and two at award, and none carries a statement against.
`pulse.py card <cell>` prints the Why-Now card: stage, next milestones, for and against, the four score parts, one
statement per family and the people the record ties to the office. The weekly diff groups by command as well as by
office and states which areas moved: in the week ending 2026-09-21, the Naval Information Warfare Systems Command
gained sixteen statements in execution and solicitation at N00039 and PMW 740, and no command recorded a negative.

Office book. `research/tools/buying_dna.py` reads the 4,061 base awards N00039 signed from
fiscal year 2020 from the saved FPDS pages, with their coded fields. The datapack's joins carry the vehicle, parsed
from the nested vehicle identifier, on 3,826 of the awards. The book: median
$326,021 (quartiles $58,826 and $2,830,394); 94 percent under a vehicle, of which Idc 86 percent; Bpa 8 percent, 82 percent single
award; 86 percent of actions are delivery orders; 53 percent full and open and 33 percent not competed; 86 percent
with no set-aside and 9 percent total small business set-asides; 83 percent firm fixed price and 15 percent cost plus
fixed fee; NAICS 541512 and 334220 at 31 and 30 percent; median duration 7 months; 340 vendors, the largest at
25 percent of awards and the top three at 46 percent, concentration index 0.091. Only 4 requirement lines were awarded again as a
contract or vehicle, 5.5 months apart at the median: an office that buys through orders shows its recompetes as new orders, which
text alone cannot tell from repeat orders. Thirteen program offices have a
book of their own, each of one to nine awards, because only an award a forecast line names by requirement number
reaches an office in the frozen corpus; 3,893 of the 3,960 incumbent statements sit at the contracting office. The
attribution process itself is document 04, and the layer loader applies it to every swept award: the office code in the
description, then the one office a saved notice under the same solicitation names, then the funding office, then the
contracting office (70, 1,242, 5,571 and 490 of 7,373 awards). Every one of the fifty positions
states the lead vendor and since when, the next contract end and the contracts ending within two years: the MIDS engineering and logistics line holds 175 contracts, 28 live,
Data Link Solutions L.L.C. on 66 of them since 2019-11-25, the next end 2026-09-30, and the reading is a recompete environment.

Object pages. The layer wraps the twin, so every object of the twin can be read on demand with the sources
that speak about it. `research/tools/pages.py` prints an office, a vendor, a person or a requirement cell as text from
the frozen corpus, the people file and the office book, and ends every page the same way: the sources speaking about
this object, with statements, first and last date and days silent, and the families that have never spoken about it.
PMW 150 on 2026-09-21: 74 statements from five sources, the forecast annex silent 459 days and the topics 593, and
six families silent, budget, conference, congress, leaders, news and oversight. A vendor page lists the contracts,
the offices, the live count and the ends within two years; a person page lists the dated positions and the sources
that named the person; a cell page is the Why-Now card with the sources under it. Nothing is stored: a page is the
record read at one point.

Questions of the twin. `research/tools/ask.py` puts the questions a capture team asks to the same record, and every
answer lists the statements behind it. `changed` reads what changed inside an organization and everything under it on
a topic in a window, against the window before. A topic is read through the capability vocabulary in `vocabulary.py`,
so autonomy is searched as autonomous, unmanned, uncrewed, UUV, USV and their kin; contract rows fold into one line by
their work, and the people observed in the window close the answer. `match` maps a company's capabilities and NAICS
codes, or a profile file for one customer, to the offices whose statements and forecast rows use those words, ranked
by the families that spoke in the last two years, then the notices of the last year, the forecast rows and the share
of the office's awards under the codes, each office with its routes in. `prep` is a meeting brief for an office: how
it buys, what changed since a given day, the open actions from the pulse, the contracts ending, the people and routes,
and questions each drawn from a dated statement (a slipped forecast row, a request for information, a justification,
a reorganization above the office, a notice filed with no office whose program names or model reading point to this
office, a contract ending within a year for which no notice names a follow-on).

`analogs` groups the notices by solicitation number into dated paths and reads how long past buys took from the step
a requirement has reached to an award notice, in the narrowest part of the tree that holds five of them. For the
Egyptian Navy AINTS request for proposal of 17 September 2026, nine past buys under PEO C4I went from a solicitation
to an award notice in a median of 307 days, which places the award between 13 April and 19 September 2027. A forecast
row's own notices carry its line or a number its title names; when only a notice sharing the row's name is found,
the answer states that the tie is a reading. `incumbents` lists the contracts ending within two years under an office,
each with what weakens the incumbent's hold (an extension, a follow-on forecast row, a follow-on notice) and what
keeps it (options exercised, a sole-source or bridge notice, the vendor's share of the office's contracts), and one
reading. A follow-on notice whose solicitation has an award notice is settled; one older than two years with none is
a question for the office. `moves` reads a competitor's window: the contracts that became public against the window
before, the offices entered for the first time, extensions and options, what ends next, and every other statement that
names the vendor. `team` answers whom to team with for a capability a company lacks: the vendors whose contracts'
stated work carries its words, one per UEI, under an organization's tree when one is named, ranked by running
contracts, each with its offices and the first contract ending within a year. `note` and `notes` keep a vendor's own meeting notes in `build/`, apart from the record, and show
each note beside the later records that share its names without merging them. `export` writes the evidence room for
one requirement: the card, every statement behind it with its public source, and a copy of each saved document a
statement cites by its hash.

Vendors by identifier. The record names a vendor as the feed spelled it on the day, so the same
company appears under two strings when its registration changed. `research/tools/vendors.py` reads the 4,061 saved
awards and resolves them by Unique Entity Identifier: 297 vendors, 24 of them spelled two or more ways, 111 under a
parent entity, 11 spellings seen under two identifiers (a re-registration), and every award carries an identifier.
A vendor page reads through the identifier, so a query for either spelling of Data Link Solutions returns the 117
awards under both. The feed's HTML escaping (an ampersand written as an entity) is
undone where the feed and the notices are read, with a check on the frozen record.

Program managers from the record's news. The public documents that name a program manager are the Navy's
own change-of-charge articles (DVIDS, the DON CIO's CHIPS magazine, the outlets that carry a Portfolio Acquisition
Executive appointment). `research/tools/news.py` reads every saved article for a change of charge as those outlets
write it: who relieved whom as what, and the office the same clause goes on to name, resolved through the organization
memory's aliases. The reading is deterministic and replays; a page that states the same change three times (a teaser, a
caption, the body) yields one row, the one whose clause names the office. `people.py` turns each row into a dated
position with the article as its source, so the PMW 770 change of command of 2025-05-07 and the PEO C4I ceremony of
2025-08-19 give nine program managers, and the PAE Mission Systems head of
2026-05-11 an acquisition leader. A change the memory cannot place, a subject named by surname alone or an office with
no alias (Project Overmatch is known to the memory as DRPM Project Overmatch; PAE Aviation, Maritime and Munitions sit
outside the pilot), is written to research/memory/people.json as an unplaced row rather than dropped. Discovery ran as five
Exa queries; the DVIDS feed the watch stage polls carries no unit filter, so a later
change of charge that names an office is taken there without a query.

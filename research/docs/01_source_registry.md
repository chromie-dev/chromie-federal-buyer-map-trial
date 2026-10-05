# 01 - Source registry: what each source proves, how it is reached, how often to look

The machine-readable registry is `research/sources/source_registry.json` (one record per source, field names
chosen to load into the platform's `gov_procurement_sources`). The table below summarizes that file;
the prose explains how the sources fit together. Every `verified` row has an inspected example
whose hash is in `research/sources/documents_manifest.jsonl`; rows marked `blocked` or `restricted` say why.

## In plain terms

Six kinds of public record cover the life of a Navy requirement: the organization pages that say
who owns what; the budget books that say what the Department wants to fund; the congressional
documents that say what was funded or changed; the Long-Range Acquisition Estimates that say what
each office plans to buy and when; SAM.gov notices for the live acquisition; and FPDS/USAspending
for the award and everything after it. For NAVWAR all six exist in public. The forecast layer is
unusually strong because the Navy's LRAE spreadsheet names the requirement office on every row.
The weak points are access, not existence: most Navy web hosts block automated clients, the
budget host resets connections, and solicitation documents increasingly sit inside PIEE.

## Sources by lifecycle stage

| Stage | Source | Access | Status | Inspected example | Monitor |
| --- | --- | --- | --- | --- | --- |
| Organization and ownership | PEO C4I official site (peoc4i.navy.mil) | webpage | verified | 11 program offices listed with tear-sheet links; footer 'One NAVWAR ... PEO C4I & Space, PEO Digital, PEO MLB' (wayback) | weekly hash diff of Program-Offices, Leadership, Contact and each tear sheet |
| Organization and ownership | NAVWAR official site (navwar.navy.mil) | webpage | verified | 'NAVWAR HQ ... procures the IW systems sought by PEO C4I, PEO MLB and PEO Digital'; NIWCs 'the technical heart' (wayback) | weekly hash diff of About, Work-With-Us, Small-Business-Programs |
| Organization and ownership | DVIDS (Defense Visual Information Distribution Service) unit pages and releases | webpage | verified | change of command: Castillejo to PMW 150 relieving Jolie; Andalis to PMW 760 (direct) | daily fetch of unit pages, diff headline list |
| Organization and ownership | Department of the Navy PAE press releases (navy.mil, paemaritime.navy.mil, DVIDS mirror) | webpage | verified | live navy.mil release: PAEs 'will have direct authority not only for program offices, but also over associated ...' (browserbase) | weekly check of PAE domains and DVIDS for new releases |
| Organization and ownership | NAVSEA Small Business Partnerships documents (org chart, Deputy Program Manager roster) | pdf | verified | two-page table: title, phone, code, description (PMS 396, PMS 397, PMS 450, ...) (wayback) | quarterly |
| Organization and ownership | Federal Register API | api | verified | query 'Naval Information Warfare' returns dated notices with agencies and PDF links (direct) | weekly query per organization name |
| Organization and people (hiring) | USAJobs historic job announcements API and announcement pages (keyless; `research/docs/21`) | api | verified | 210 NAVWAR (NV39) announcements opened 2026-03-31 to 2026-09-27, 11 of them contracting; control 880882900 is a DP-3 contract specialist for the Robotic and Autonomous Systems portfolio, a flyer of anticipated vacancies (direct) | weekly listing per agency code, 180-day window |
| Agency intent | Department of the Navy budget materials (FMB): justification books by appropriation | pdf | verified | 37 PDF links: RDTEN_BA1-3 ... BA7-8, OPN_BA1 ... BA5-8, OMN, WPN, APN, SCN, Highlights_Book, DON_Budget_Card (direct) | annual at budget release, then monthly checks for amended books |
| Agency intent | DoD Comptroller budget materials | spreadsheet | verified | p1_display.xlsx: 1,138 rows; 55 OPN BA2 lines incl. 2915 CANES 439,977 / 534,324 / 493,046 ($K) (browserbase) | annual |
| Agency intent | Federal IT Dashboard / IT Collect public API | api | verified | landing page reachable; detailed pull handled by the existing prod ingest (direct) | weekly (existing prod job) |
| Congressional decisions | govinfo API (committee reports, explanatory statements, enacted laws, bills) | api | verified | H. Rept. 119-715 (DoD Appropriations Act, 2027) HTML text; OPN table rows read as line, title, request, recommendation (direct) | daily poll of new CRPT/CREC/PLAW packages matching defense keywords |
| Congressional decisions | congress.gov API (bills, amendments, committee reports, hearings) | api | verified | bill endpoint responds with DEMO_KEY (direct) | daily |
| Planned requirements | NAVWAR Long-Range Acquisition Estimate (LRAE Annex 25) | spreadsheet | verified | 833 rows; 144 rows name a PEO program office, all contracted by N00039; 200 rows carry an existing contract number (wayback) | monthly check for a new release; diff rows by PID number |
| Planned requirements | NAVSEA Enterprise Long-Range Acquisition Estimate | spreadsheet | verified | sheet 'Annex 25 Template'; 228 distinct requirement offices; N00024 NAVSEA HQ is the top contracting UIC (wayback) | monthly |
| Planned requirements | ONR and NRL Long-Range Acquisition Estimate | spreadsheet | verified | two data sheets plus validation lists; header on row 7 (direct) | monthly |
| Planned requirements | PEO C4I industry engagement (AFCEA WEST events, Industry Intake Form) via DVIDS | webpage | verified | 'third annual engagement event at AFCEA WEST 2026, featuring all 11 program offices' (direct) | daily via DVIDS unit page |
| Planned requirements | NAVWAR Commercial Solutions Openings | webpage | verified | page links a SAM notice and the PIEE vendor instructions (wayback) | weekly |
| Planned requirements | SBIR/STTR topics (sbir.gov; DoD SBIR/STTR portal) | webpage | verified | search page reachable (direct) | per cycle (three times a year) |
| Active acquisition | SAM.gov Contract Opportunities public data extract (daily CSV of current notices) | export | verified | 241,486,025 bytes; 47 columns incl. AAC Code, Sol#, Type, PostedDate, AwardNumber; 535 NAVWAR-family notices (direct) | nightly download and diff on notice id + modified date |
| Active acquisition | PIEE Solicitation Module (replaced NAVWAR eCommerce) | manual | restricted | official access-instructions PDF (TLS chain is DoD PKI; fetched with verification off and recorded) (direct) | not automated; rely on SAM synopses |
| Active acquisition | SeaPort-NxG portal (Navy services vehicle) | manual | blocked | connection failed (HTTP 000) | not automated; awards tracked through FPDS referenced IDVs |
| Awards and execution | FPDS ATOM public feed | api | verified | 10 actions; funding offices N00039 and N00024 (NAVSEA HQ); descriptions such as 'LTS/CLTS PRODUCTION AND SUSTA...' (direct) | daily pull per contracting office since last signed date |
| Awards and execution | USAspending API v2 | api | verified | description names 'PROGRAM MANAGER, WARFARE TACTICAL NETWORKS (PMW 160)'; PoP 2021-10-27 to 2026-10-26 (direct) | weekly refresh of watched awards; nightly keyword search for office codes |
| Awards and execution | DoD (Department of War) daily contract announcements (RSS + article pages) | api | verified | 10 items: 'Contracts for Sept. 15, 2026' ... with war.gov article links and pubDate (direct) | daily RSS poll; fetch each day's article through the fallback path |
| Oversight | Oversight.gov federal report listing (inspector general reports) | webpage | verified | listing 'Navy': 50 rows, all with Department of War or the Navy as the agency reviewed; report pages carry date, OIG, report number, questioned costs and the PDF (direct) | weekly: the two listing queries, new report pages and files |
| Oversight | GAO reports (gao.gov products; feed for the latest 25, Exa discovery for older ones) | webpage | verified | Navy And Coast Guard Shipbuilding product page with the highlights as HTML (browserbase) | weekly feed poll; monthly search discovery |
| Leaders' words | U.S. Navy Press Office speech and testimony archives (navy.mil) | webpage | verified | first archive page: 20 CNO speeches June to August 2026 with datelines; article pages carry the full remarks (browserbase) | weekly: first archive page of each list, new articles |
| Leaders' words | House Committee Repository (docs.house.gov) Armed Services and Appropriations hearings | webpage | verified | Navy FY2027 Seapower budget hearing 2026-05-20: three witnesses, joint witness statement PDF (direct) | weekly: AS00 and AP00 feeds, new hearings naming the department |
| Leaders' words | Conference and event pages naming Navy officials (Exa discovery, organizer or trade page) | webpage | verified | Sea-Air-Space 2026 organizer page: CNO keynote at the Sea Services Luncheon (direct) | monthly discovery; known pages re-fetched before each major event |
| Awards and execution | GAO bid protest decisions and docket | webpage | blocked | HTTP 403; the platform's existing pursuit-intelligence runner already covers GAO | weekly |
| Organization and ownership | Internet Archive Wayback Machine (dated copies of official pages) | api | verified | 2026-05-19 capture shows the PAE Mission Systems front page (wayback) | on demand |
| Active acquisition | SAM.gov Contract Opportunities archived yearly extracts (FYxxxx_archived_opportunities.csv) | export | verified | FY2025 file: 1,159,352,018 bytes, 399,820 rows, 666 NAVWAR-family notices (posted 2024-10 to 2025-09) (direct) | quarterly re-pull of the two most recent fiscal years |
| Active acquisition | SAM.gov site API (opps v2/v3 and sgs search, keyless) | api | verified | NILE ISS 6 RFI detail with description body naming PEO C4I / PMW 150 (direct) | on demand per solicitation number surfaced by FPDS or the LRAE |
| Organization and ownership | PEO Digital official site (peodigital.navy.mil) | webpage | verified | 'IMPORTANT NOTICE: On May 11, 2026 ... PEO Digital is now part of the PAE Mission Systems' (browserbase) | weekly hash diff |
| Organization and ownership | DON CIO CHIPS magazine (official DoN IT publication) | webpage | verified | 'On May 13, 2020, the Deputy Assistant Secretary of the Navy for Information Warfare and Enterprise ...' (browserbase) | monthly |
| Organization and ownership | PAE Mission Systems official site (missionsystems.navy.mil; also served at peoc4i.navy.mil) | webpage | verified | Industry page: five capability portfolios and the Learn / Introduce / Propose intake process (browserbase) | weekly hash diff; alert when About, Leadership or a portfolio page appears |
| Organization and ownership | PEO MLB official site (peomlb.navy.mil) | webpage | verified | 'PEO MLB is now part of the PAE Mission Systems'; navigation: About, Portfolio, Industry Engagement, Strategic (browserbase) | weekly hash diff |

## How the sources connect

Three kinds of join carry a requirement across stages (details and pitfalls in
`research/docs/03_data_connection_map.md`):

- Shared identifiers: contracting office UIC (LRAE, FPDS, SAM, PIID prefix), contract number
  (LRAE "Existing Contract Number", FPDS, USAspending), solicitation number (SAM, FPDS),
  referenced IDV (FPDS, USAspending).
- Office codes and names in text: the LRAE requirement-office column; "PMW 160" in award and
  notice descriptions; tear sheets that map programs to offices; DVIDS releases that map people
  to offices with dates.
- Program names: the only bridge between a budget line and an office, so it is always recorded
  as an inference with both documents cited.

## Requested, enacted, obligated, ceiling

These four numbers describe different things and live in different sources; the registry keeps
them apart and the connection map (section 3 there) defines each. In short: the request is in
the Department of the Navy justification books; the enacted amount and any marks are in the
appropriations acts, explanatory statements and committee reports on govinfo; obligations are
per action in FPDS and per award in USAspending; the ceiling is the award's base-and-all-options
value. The LRAE's value ranges are estimates and belong to none of the four.

Where the budget-to-contract connection cannot be established publicly: no public source maps a
program element or procurement line item to a contract number. The join stops at the program
name in the exhibit narrative. For PEO C4I that means a CANES procurement line can be tied to
PMW 160 through the tear sheet and to CANES awards through award descriptions, but the dollar
flow from that line to a specific contract is not observable.

## Access findings

| Path | Sources | Notes |
| --- | --- | --- |
| Open, direct | USAspending API, FPDS ATOM, SAM public extract listing, govinfo, congress.gov, Federal Register, DVIDS, paemaritime.navy.mil, ONR, IT Dashboard, sbir.gov, PIEE landing, DoD contracts RSS, oversight.gov (listing, report pages, files), the GAO report feed, docs.house.gov (feeds, hearing pages, statements), most conference organizer pages | no key or a free api.data.gov key; gao.gov product pages, navy.mil archives and the Senate committee sites are in the geographic block below |
| Geographic block (HTTP 403 to any client from a non-US address) | navwar, peoc4i, navsea, navair, niwc, navy.mil, war.gov, comptroller, gao.gov, peodigital | Wayback captures for history; context.dev (a United States address) for live pages, Browserbase (US egress) for files |
| Firewall resets | secnav.navy.mil (budget library, OSBP) | Wayback and Browserbase are refused; the Comptroller's P-1/R-1 tables cover the line amounts |
| Login or key required | PIEE Solicitation Module (vendor login), SeaPort-NxG (vehicle holder) | recorded as `restricted` or `blocked`; SAM synopses and FPDS awards stand in |
| Documented host dead | api.sam.gov (Opportunities public API): 404 from two networks; its keys come from a SAM.gov account, not api.data.gov | the keyless SAM.gov site API (notice detail, description, attachments, archived search) replaces it |

## Cadence and lag

| Source | Publication cadence | Reporting lag | Proposed monitor |
| --- | --- | --- | --- |
| SAM public extract | daily | one day | nightly download, diff by notice id and modified date |
| FPDS ATOM | continuous | about 90 days for DoD actions | daily pull per contracting office |
| USAspending | daily loads | follows FPDS | weekly refresh of watched awards; nightly keyword search |
| NAVWAR LRAE | roughly annual with updates | forecast (estimate) | monthly check for a new release; diff rows by PID |
| DoN budget books | annual, plus amendments | request year minus one | annual at release, monthly for amendments |
| govinfo / congress.gov | daily | days | daily poll of new defense packages |
| DVIDS unit pages | event-driven | same day | daily headline diff |
| Official organization pages and tear sheets | irregular | months behind leadership changes | weekly hash diff |

## Sources as instruments (2026-09-28)

Every registry row carries an `instrument` block, written by `research/tools/registry_instrument_fill.py` from the row's
own words and the saved status and checked by `--check`:

| Field | Values | What decides it |
| --- | --- | --- |
| `status` | Live, Historical, Adjacent | Live when the source publishes now; Historical when its own words say the series ended or moved (PEO C4I's site); Adjacent for an archive, a directory or a re-generated copy (Wayback, the small business directory, the yearly extracts) |
| `standing` | standing, episodic, mixed | a feed or a page that is always there, or a release series, a cycle, a hearing |
| `collector_kind` | registered_empty, collecting, blocked | the saved status: nothing yet, something, or the registry says blocked or restricted |
| `cadence` | kind (standing, monthly, annual, per_cycle, episodic), rule, basis | the stated frequency; a rule only where saved rows or a saved page state one |
| `org_scope` | memory node ids | the nodes a saved source says the source feeds; `[]` otherwise |
| `close_by` | text | on a row with no document, how to close the gap |
| `contact`, `entry_types`, `envelopes`, `pages`, `url_prefixes` | optional | role mailboxes only, social handles as pointers never read; entry types with their basis; envelope keys into `sbir_instruments.json`; the pages a cadence sweep lists; the URL sections a row claims on a shared host |

The rule that follows from it: **an empty collector is registered, never placed in a cell**. `coverage.py` refuses a
cell naming a source with nothing collected, and the status's `open_gaps` section lists every zero-document row with
its blocker, its `close_by`, its collector kind and the last recorded attempt, the three Navy gaps first
(`sam_opportunities_api`: the keyed API answers 404 with an empty body, recorded 2026-09-16 and 2026-09-27, not retried;
`seaport_nxg`: no TLS handshake on 2026-09-28, task orders visible to holders only; `navy_posture_testimony`: the
congress.gov hearing endpoint answered on 2026-09-28 with DEMO_KEY, so the row is verified and the sweep is the next step).

### SBIR and prize portals registered on 2026-09-28

Each landing page was fetched once through `fetch.py` and the answer recorded in the ledger, refusals included; a row's
`inspected_example` names that attempt. Envelopes and cadence are quoted from the saved page where it states them
(`research/sources/sbir_instruments.json`), else marked unverified and read by nothing.

| Registry | Row | Landing page on 2026-09-28 | Collector |
| --- | --- | --- | --- |
| Navy | `navy_sbir_program_site` (navysbir.com) | 403, no bytes | blocked; the same topics are on the saved DSIP index pages |
| Navy | `sbir_sttr_topics` | unchanged; cadence monthly, first Wednesday, observed on the FY2026 pre-release days (2026-04-13 is the annual BAA's exception) | collecting |
| Army | `army_sbir_program_site` (armysbir.army.mil) | 200; states "PHASE I 1-6 months, up to $300K" and "PHASE II 12-18 months, up to $2M" | collecting |
| Army | `army_xtech_prizes` (xtech.army.mil) | 200; the text states no pool or date | collecting |
| DARPA | `darpa_small_business_community` | 200 (also 2026-09-24); claims only its URL section of darpa.mil | collecting |
| DARPA | `darpaconnect` (darpaconnect.us) | 200 | collecting |
| Air Force | `afwerx_site`, `spacewerx_site` | 200; a media mailbox on each | collecting |
| all four | `diu_cso_solicitations` (diu.mil) | 404 and 500 on the two addresses named | registered_empty; the other DoD layers read it through `shared_sources` |

### Pending portals (no agency in code)

`research/sources/pending_sources.json` holds the portals whose agency has no profile (DHS S&T LRBAA, USSOCOM, DTRA,
the civilian SBIR programmes, and two adjacent resources). A pending row feeds no registry, node or cell and names role
mailboxes only; it is promoted when the agency truth table (`docs/22`) gives its agency an in-code verdict.

### The generated Navy matrix

`python research/tools/coverage.py matrix` writes `coverage_matrix.json` for a profile whose `coverage_org_nodes` names
the memory node behind each row (the Navy: the four commands, the two NIWCs, NRL, SSP, PAE Mission Systems, DRPM RAS,
eleven PEOs, NAVFAC and MSC; the last two gained their nodes on 2026-09-28 from saved pages, Stage 2). A cell counts the corpus's events under the node's
subtree per family (people from the roster's positions), names the sources that made them and the family's
department-wide sources, carries the documents those sources hold (`documents_scope: source`, the ledger has no
organization tag), the newest date and a tag: Live (an event within 365 days of the freeze), Historical (events, none
that recent), Adjacent (a department-wide source alone). An empty cell keeps the hand-written reason. The Air Force
generator (`coverage_matrix_airforce.py`) stays until its profile names its nodes here.

Stage 2 (2026-09-28, branch `task/sbir-stage2`) read what the saved DSIP files already hold: every topic row states its
instrument, entry type, phases, solicitation, release, Q&A window, ITAR, CMMC level, focus areas and ceiling with the
basis of each reading (`docs/06`, Programs), the Navy topic pattern reads the FY2026 codes, NAVFAC, MCSC and MARCOR
resolve to their commands, and `prerelease.py` turns the index pages into dated pre-release observations compared
with the first-Wednesday rule. The memory gained `pae:aviation`, `pae:munitions`, `pae:maritime`,
`pae:industrial-operations`, `pae:marine-corps`, `pae:undersea` (children of `agency:don`, each from the release that
established it), `command:navfac`, `command:mcsc` and `command:msc` (no parent, no saved page states one), all as page
statements in `research/memory/org_page_statements.json` whose passages are checked verbatim against the saved bytes
on every build. The Navy matrix now reads 68 Live, 18 Historical, 161 Adjacent, 73 not_started, 2 no_public_source: the NAVFAC and
MSC rows are Adjacent through the department-wide sources, with no event of their own until the corpus is refrozen.

Stage 3 (2026-09-28, branch `task/sbir-stage3`) is the live side of the cadence: `prerelease.py sweep --fetch` reads the
monthly rows' `pages`, keeps what the ledger already recorded this cycle and fetches the rest once, refusal or stub
recorded and never worked around (`docs/06`, The sweep). A row's `collector_kind` does not change on a refusal: the
source publishes, the collector is blocked, and the open-gap list says so.

## Reusability

Every Navy activity inspected (NAVWAR, NAVSEA Enterprise, ONR/NRL) publishes its LRAE in the same
"Annex 25" template with the same column names, so one spreadsheet adapter and one
requirement-office alias table serve the whole Department. The registry records use the
`gov_procurement_sources` field names (`source_key`, `access_mode`, `refresh_cadence` as
`proposed_monitor_frequency`, `verification_status`, `last_verified_at`, `known_access_gaps` as
`access_restrictions`) so they can be loaded without renaming.

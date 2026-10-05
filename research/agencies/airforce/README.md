# Department of the Air Force agency intelligence

The Department of the Air Force, the Space Force included, read by the same tools as the Navy layer: which program
executive office or center wants what, when, and on what evidence. Nothing here is written by hand except the source
registry; the coverage matrix is written by a script from the layer's own files; every other file is the output of one
tool under `research/tools/`, run with `AGENCY=airforce`. The comparison with the Navy, family by family, is
`research/docs/20_navy_versus_airforce.md`.

## Layout

| File | Written by | What it holds (as built on 2026-09-25, rebuilt on 2026-09-26 after the merge with main) |
| --- | --- | --- |
| `memory/organization_seed.json` | `org_memory_airforce.py build` | 55 nodes: 38 from SAM.gov's federal organization records (the department, AFMC, AFLCMC, AFRL, AFSC, SSC, the legacy AFSPC, a Hanscom site, nine program executive offices and the 21 swept contracting offices, each on a verbatim slice of its saved record) and 17 people from the leadership pages of af.mil, AFMC, AFRL, AFSC and SSC and from AFLCMC's organizational chart; 75 relationships: `child_of`, `contracts_for` (inferred from the record's stated parent) and 17 `leads` (10 directly documented where the title names the organization, 7 inferred: a title alone on the organization's own page, or the chart's column layout); one interpretation (the Space Force level SAM.gov does not carry) |
| `memory/people.json` | `people.py build` | 1,052 people from 5,091 observations: 5,079 SAM.gov points of contact and 12 witnesses of the FY2027 posture hearing |
| `memory/vendors.json` | `vendors.py build` | Vendors resolved by UEI from the saved FPDS pages of 26 offices |
| `memory/small_business_offices.json` | `small_business.py build` | The department's small business office as the Department of War directory lists it and its own page (hosted browser) |
| `memory/contact_observations.json`, `memory/contact_recommendations.json`, `memory/review_log.json` | `contact_routes.py build` (the `routes` stage, from main's fourth DARPA pass) | 1,188 contact observations, every one a contracting point of contact on a saved notice (the department publishes no staff listing), 23 route recommendations for 23 offices, and the review log's 1,188 checks, all confirmed against the saved notices; a listed role is a stated role, not authority |
| `events/budget_lines.json` | `budget.py extract` | 86 P-1 lines from four FY2027 procurement books (Aircraft Vol I, Missile, Ammunition, Other Procurement), Wayback captures of SAF/FM's files |
| `events/budget_measures.json` | `budget.py display` (in `extract`) | 579 lines from the Comptroller's FY2027 P-1 and R-1 display spreadsheets, the columns kept under the sheet's own labels (FY 2025 Actuals, FY 2026 Discretionary Enacted, FY 2027 Discretionary Request), $M, each line with its sheet and rows |
| `results/office_owners.json`, `office_owners.md` | `office_owners.py build` (the `owners` stage, 2026-09-29) | Per program office: who owns which problem, who would champion a fix, who holds the budget, each with its source; the budget lines and where each is placed; the year's obligations from the saved feed; what the office bought. 10 office section(s), 6,269 fact rows, 25 inferences (4 champion readings), 665 budget lines of which 106 placed, 0 program rows of which 0 placed, FY2026 obligations $13,237.0M over 5,586 action(s); facts, inferences and recommendations under three keys, an empty family stated as a boundary |
| `events/sbir_topics.json` | `sbir.py build` | 755 USAF-component SBIR/STTR topics open FY2020 on, every detail read; 609 resolve to a command or center through the portal's command column (AFRL 296, AFMC 256, SSC 36, AFSC 10, AFLCMC 9, PEO Weapons 2) and 107 name an office in their text |
| `events/remarks_events.json` | `remarks.py extract` | 161 dated events from the three witness statements of the department's FY2027 posture hearing of 2026-05-20 (House Armed Services) |
| `events/oversight_events.json` | `oversight.py extract` | 22 findings from 7 DoD OIG and GAO reports since 2025 about the department |
| `events/news_observations.json` | `news.py build` | 19 articles modelled (31 claims): the af.mil and spaceforce.mil feed items naming the department and the pages the per-office Exa news searches found (11 official announcements, two of them on war.gov, and 8 secondary or trade-press pieces), the official pages through the hosted browser |
| `events/hiring_observations.json` | `jobs.py build` (the `hiring` stage; the `vacancies` stage lists) | 1,111 job announcements USAJobs filed under AF1M (Air Force Materiel Command) and AF6S (Space Systems Command) between 2026-03-31 and 2026-09-27; 24 announcement pages saved; 467 load (the acquisition workforce or a known name); 1,122 claims, 11 of them new signals; 966 read as an intention to hire, 119 as a selection stated, 26 withdrawn (research/docs/21) |
| `events/fedreg_events.json` | `fedreg.py build` | 230 Federal Register documents under the department's slug |
| `events/congress_events.json` | `congress.py build` | 82 directives naming the Air Force or the Space Force in the 3 committee reports saved |
| `events/protest_events.json` | `protests.py build` | 166 GAO protests of the department's awards |
| `events/assistance_awards.json` | `assistance.py build` (the `grants` stage, from main's fourth DARPA pass) | 5,885 grants and cooperative agreements USAspending lists for the department (5,108 project grants, 748 cooperative agreements, 26 block and 3 formula grants) on 59 saved pages; 4,002 are AFOSR's under FA9550, and 4,798 carry the code of a swept office in their award id; the query asks for FY2020 on, and the awards' own start dates run from 2004 to 2027 |
| `results/notice_kinds.json` | `notice_kinds.py build` | What each of 255 special notices announces, 199 kept with the words that say so |
| `results/corpus.json` | `backtest.py freeze` | 31,189 dated events, 1,213 outcomes, 2,748 needs and 38 organizations frozen from the database (2026-09-26, under the merged tools: the incumbent family is 26,784 events, the base awards with their FPDS histories and the grants; notices 3,125, programs 754, congress 243, protests 166, budget 86, oversight 22, news 9) |
| `results/outcome_labels.json` | `backtest.py label` | The office and the names each outcome goes by, in its own words |
| `results/office_reads.json` | `office_wiki.py build` | 2,672 notices, 2,495 live awards, 750 topics and 243 committee statements read against the office pages (Sonnet 5; the merged prompt names the department and the whole chain above an office, and main's reader takes every live award, not only the ones no record places); 81 notices, 104 awards, 79 topics and 16 statements placed with both quotes verbatim; an office named outside the directory or without the notice's words is no placement (what the model wrote is kept as `office_as_answered`); none stands unread |
| `results/pulse.json` | `pulse.py check` | 1,213 cells scored as of 2026-09-26, 50 ranked positions (at contracting offices and AFRL: no PEO owns a cell yet), the week's changes and 1,286 actions (585 office meetings and 536 incumbent contracts ending, the latter new with the histories in the record) |
| `results/buying_dna.json` | `buying_dna.py build` | How each of the 26 contracting offices buys: 17,564 base awards on the saved pages (FA8201 3,597, FA8501 2,420, FA8650 1,626, FA8101 1,521, FA8751 1,248), 937 distinct vendors at the top office; one program office book, AFRL's, where the office reading placed notices; no PEO owns a need yet |
| `sources/source_registry.json` | by hand | 21 sources, three of them the department's own (the sites with their leadership pages, the budget books, the speeches address that renders news) and one the Navy never needed (SAM.gov's organization records) |
| `sources/coverage_matrix.json` | `coverage_matrix_airforce.py` over the layer's files, validated by `coverage.py check` | 6 organizations by 14 evidence families (the fourteenth the grants), 49 of 84 cells covered; an uncovered cell says why (`not_started`, `blocked`, `no_public_source`) |
| `sources/source_status.json` | `coverage.py status` | What each registered source last collected |

`build/airforce/` (gitignored) holds what the load and read-back stages make: `schema.sql`, `layers.sql`, the
organization graph, the back-test results and the baselines.

## Rebuilding

The database is local only (port 54322); a machine without the platform's schema names the exported kit in
`SCHEMA_KIT`.

    export AGENCY=airforce SCHEMA_KIT=../navy-db-kit-2026-09-21
    python research/tools/pipeline.py --db airforce_proof --list
    python research/tools/pipeline.py --db airforce_proof --only people --refresh   # one stage
    python research/tools/pipeline.py --db airforce_proof --refresh                 # every build and read-back stage
    python research/tools/pipeline.py --db airforce_proof --collect                 # also take what is new from each source

The network stages that reach a `.mil` host or gao.gov need the hosted browser (`BROWSERBASE_API_KEY`, and the
`browserbase` and `playwright` packages, here in `.venv`); run those stages with `.venv/bin/python`. A build stage
reads only what is saved; model answers replay from the shared cassettes, and a record not seen before is one model
call: through OpenAI with `OPENAI_API_KEY`, else through the Claude Code command line (`research/tools/llm.py`).
The read-back stages compare with the files above and fail on any difference unless `--refresh` is given. The saved
bytes, the documents ledger and the cassettes are shared with the Navy and DARPA layers.

## How the Air Force differs from the Navy

| Point | Navy | Air Force |
| --- | --- | --- |
| Forecast | Long Range Acquisition Estimates, one package per command release | None department-wide (AFLCMC's SMART Guide PDF not yet read); the datapack, revision and fiscal-year stages are skipped |
| Two services | One | The Space Force buys under the same subtier; SAM.gov files Space Systems Command under the department, so it is read through SSC's six offices and the spaceforce.mil feed |
| Organization source | navy.mil and the PEO sites | SAM.gov federal organization records (every department site refuses this address) |
| Who owns a requirement | Program offices and program executive offices | Program executive offices and portfolio acquisition executives, as SAM.gov's hierarchy names them; AFRL's directorates perform |
| Contracting | Several offices per command, swept by code | 26 offices of the acquisition centers; the installation contracting squadrons are not swept |
| Budget | Department of the Navy P-40 books, direct | SAF/FM P-40 books from Internet Archive captures (the host completes no TLS handshake); RDT&E books not found on the captured page |
| Leaders' words | A speech archive and hearing feeds | No speech archive (the address renders the news listing); testimony and news |
| Access | navy.mil answers | Every department host refuses; pages through the hosted browser, files through the Archive |

Every difference is a key in the `AIRFORCE` profile of `research/tools/agency.py`; the tools branch on the agency
only to choose its folders.

## Results, 2026-09-26 (first built 2026-09-25)

The back-test replays each of the 1,213 outcomes (notices and awards from FY2024 on) against the 31,189 events public
before it, with no forecast family. The three-family bar reaches 342 of them: recall 0.214 at 180 days, 0.248 at 90
and 0.274 at 30, against floors of 0.113, 0.129 and 0.149; by name alone 0.088, 0.100 and 0.110; median lead 746.5
days. On the first record (8,720 events, before the award histories and the grants entered) the bar reached 244:
0.142, 0.166 and 0.191. The dumb baseline "an incumbent contract is ending" now reaches 0.286, 0.302 and 0.315 by
itself, more than the three-family bar: the histories made the incumbent family the largest and the most predictive,
and the bar's demand for three families holds it back where only notices and awards speak. Precision has no cell to judge: the pilot program executive offices own no need in the frozen record, because
the department publishes no forecast and the notices name the contracting office's symbol (AFLCMC/HBBK), not the
PEO, so a PEO cell appears only where the office reading places a notice. The outcome labels were read by Opus 5.5
(101 aliases dropped as not written so in the text).

## Open

- AFLCMC's leadership and biography paths answer 404, so its commander, executive director and the PAEs of three portfolios (Fighters and Advanced Aircraft, Armament/Weapons, Training) are read from the center's organizational chart by column layout, and say so (`inferred`). Six program executive offices name no leader: SSC's leadership page lists the command's head, not its five PEOs, and the chart's C3 directorate is not the PAE C3BM (its footnote says the directorate supports it).
- The RDT&E justification books and a Space Force procurement book were not on the captured FY2027 page.
- AFLCMC's SMART Guide (a forecast PDF) is not read.
- Five offices without a SAM.gov organization id (FA8702, FA8611, FA8620, FA8656, FA8814) are swept by code only.
- GovInfo committee reports need `DATA_GOV_API_KEY`; the Navy's saved reports are read.
- GDELT rate-limited the per-office news sweep after 10 queries; six Exa news searches were run, the conference-page search was not.
- No program executive office owns a need in the frozen record: the notices name the contracting office's symbol, and a rule carrying a need from the office to its PEO on SAM.gov's stated `contracts_for` edge is not yet written.

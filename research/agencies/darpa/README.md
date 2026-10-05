# DARPA agency intelligence

The Defense Advanced Research Projects Agency read by the same tools as the Navy layer: which technical office
wants what, when, and on what evidence. Nothing here is written by hand except the source registry and the
coverage matrix; every other file is the output of one tool under `research/tools/`, run with `AGENCY=darpa`.
The comparison with the Navy, family by family, is `research/docs/19_navy_versus_darpa.md`.

## Layout

| File | Written by | What it holds (as built on 2026-09-26) |
| --- | --- | --- |
| `memory/organization_seed.json` | `org_memory_darpa.py build` | 31 nodes: the agency, the Director's Office, six technical offices with their office codes, four former offices the notices still name (MTO, I2O, ACO, APO), the Contracts Management Office, eight contracting offices of other agencies that sign DARPA-funded awards, SBPO, CSO and eight persons, each claim resting on a verbatim passage of a saved page |
| `memory/people.json` | `people.py build` | 685 people from 1,302 observations (a role mailbox or an address written as the name is one contact per address): 1,168 SAM.gov points of contact, 127 staff listing entries with their start dates, 4 from darpa.mil pages, 3 from conference pages |
| `memory/contact_observations.json`, `contact_recommendations.json`, `review_log.json` | `contact_routes.py build` | 785 contact observations in the Navy's reviewed shape (tasks/T02): 15 office directors and deputies and 92 program managers from the staff listing, 678 points of contact from the notices, each with its locator and passage; 26 route recommendations for 13 offices (leadership, the program managers as the requirement side, the notice contacts as the acquisition side), every one a draft; 785 review entries, each observation checked against the saved record it cites, all confirmed. Every row carries `generator`; the tool never overwrites a hand-written set |
| `memory/attribution_examples.json` | by hand, checked by `tests/test_darpa_layer.py` | 15 swept awards worked through the placement rules of research/docs/04: 12 directly documented (nine by the office code in the description, three by the notice under the award's solicitation), 3 unresolved with the path to resolving each; every passage is verbatim on the saved record and every placement agrees with the layers emitter |
| `memory/vendors.json` | `vendors.py build` | 1,172 vendors resolved by UEI from 3,536 base awards |
| `memory/small_business_offices.json` | `small_business.py build` | The agency's small business office as the Department of War directory lists it, with its own page saved; the page prints no contact lines |
| `events/budget_lines.json` | `budget.py extract` | 48 program elements from the FY2026 and FY2027 RDT&E justification books (Exhibit R-2) |
| `events/budget_measures.json` | `budget.py display` (in `extract`) | 24 lines from the Comptroller's FY2027 P-1 and R-1 display spreadsheets, the columns kept under the sheet's own labels (FY 2025 Actuals, FY 2026 Discretionary Enacted, FY 2027 Discretionary Request), $M, each line with its sheet and rows |
| `results/office_owners.json`, `office_owners.md` | `office_owners.py build` (the `owners` stage, 2026-09-29) | Per program office: who owns which problem, who would champion a fix, who holds the budget, each with its source; the budget lines and where each is placed; the year's obligations from the saved feed; what the office bought. 10 office section(s), 3,455 fact rows, 414 inferences (21 champion readings), 72 budget lines of which 43 placed, 372 program rows of which 222 placed, FY2026 obligations $2,575.7M over 2,587 action(s); facts, inferences and recommendations under three keys, an empty family stated as a boundary |
| `events/sbir_topics.json` | `sbir.py build` | 170 DARPA-component SBIR/STTR topics open FY2020 on |
| `events/remarks_events.json` | `remarks.py extract` | 72 dated events from 16 documents: 52 from 8 hearing statements, 20 from 8 conference pages |
| `events/remarks_discovered.json` | `remarks.py discover` | The 8 conference pages the web search found, with the query that found each |
| `events/oversight_events.json` | `oversight.py extract` | 4 reports read (3 GAO products, 1 Inspector General evaluation); 1 finding kept, and 1 not kept because the remediation it named is not written so in the report |
| `events/news_observations.json` | `news.py build` | 5 observations from 2 darpa.mil articles |
| `events/hiring_observations.json` | `jobs.py build` (the `hiring` stage; the `vacancies` stage lists) | 5 job announcements USAJobs filed under DD13 between 2026-03-31 and 2026-09-27, every page saved; 4 load (the acquisition workforce or a known name); 16 claims, 5 corroborating the agency and 11 new signals; all 5 read as an intention to hire. DARPA recruits its program managers through darpa.mil/careers, which refuses this address (research/docs/21) |
| `events/fedreg_events.json` | `fedreg.py build` | 2 Federal Register documents whose title, action or abstract names DARPA |
| `events/congress_events.json` | `congress.py build` | 1 directive naming DARPA in the 3 committee reports saved |
| `events/protest_events.json` | `protests.py build` | Empty: the docket stage needs `CONTEXT_DEV_API_KEY` and has not been run for DARPA |
| `events/assistance_awards.json` | `assistance.py build` | 452 grants and cooperative agreements USAspending lists for DARPA since FY2020 (405 cooperative agreements, 44 project grants, 3 block grants), from five saved search pages; 218 carry an office code in their description (BTO 78, DSO 55, MTO 42, I2O 34, TTO 7, STO 2) and load under it, the rest at the agency |
| `results/notice_kinds.json` | `notice_kinds.py build` | What each of 405 special notices announces, 332 kept with the words that say so, 1 unread (no key) |
| `results/corpus.json` | `backtest.py freeze` | 6,720 dated events, 317 outcomes, 1,121 needs and 23 organizations frozen from the database; 5,309 of the events are the incumbent family: 3,513 award end dates, 826 awards (452 of them grants), 426 extensions, 308 modifications and 38 funding changes read from the FPDS histories |
| `results/outcome_labels.json` | `backtest.py label` | The office and the names each outcome goes by, in its own words; 5 outcomes stand unread (no key) |
| `results/office_reads.json` | `office_wiki.py build` | 697 notices, 1,067 awards, 165 topics and 1 directive read against the office pages (a notice whose title carries an office code is placed by the record); re-read on 2026-09-26 under the merged prompt (Opus 5.5 through the Claude command line, every record read again, none kept from an earlier build and none unread): 65 notices, 52 awards and 3 topics placed with both quotes verbatim, the directive not. The 2026-09-25 readings, kept without a key, had placed 189 notices, 90 awards and 44 topics; the first reading, whose prompts said "Navy", 201 notices and 32 topics |
| `results/pulse.json` | `pulse.py check` | 552 cells scored as of 2026-09-25, the week's changes and 655 actions, 468 of them an incumbent contract ending |
| `results/buying_dna.json` | `buying_dna.py build` | How the Contracts Management Office buys (2,462 base awards signed from 2019-10-03 to 2026-06-25), the 53 offices of other agencies that sign DARPA-funded awards, and the technical offices the awards are placed under |
| `sources/source_registry.json` | by hand | 19 sources, two of them the agency's own (the site and the staff listing) |
| `sources/coverage_matrix.json` | by hand, validated by `coverage.py` | 7 offices by 14 evidence families, the fourteenth the grants; 75 of 98 cells covered |
| `sources/source_status.json` | `coverage.py status` | What each registered source last collected: 17 of 19 have collected something |

`build/darpa/` (gitignored) holds what the load and read-back stages make: `schema.sql`, `layers.sql`, the
organization graph, the back-test results and the baselines.

## Rebuilding

The database is local only (port 54322); set `SCHEMA_DB_CONTAINER` to the running database container first.

    export AGENCY=darpa
    python research/tools/pipeline.py --db darpa_proof --list
    python research/tools/pipeline.py --db darpa_proof --only people --refresh    # one stage
    python research/tools/pipeline.py --db darpa_proof --refresh                  # every build and read-back stage
    python research/tools/pipeline.py --db darpa_proof --collect                  # also take what is new from each source

A build stage reads only what is saved; model answers replay from the shared cassettes, and a record not seen
before is one model call, which needs `OPENAI_API_KEY`. The read-back stages compare with the files above and
fail on any difference unless `--refresh` is given. The saved bytes, the documents ledger and the cassettes are
shared with the Navy layer.

## How DARPA differs from the Navy

| Point | Navy | DARPA |
| --- | --- | --- |
| Forecast | Long Range Acquisition Estimates, one package per command release | None published; the datapack, revision and fiscal-year stages are skipped |
| Who owns a requirement | Program offices and program executive offices | Technical offices (`owner_types` in the profile) |
| Contracting | Several contracting offices, swept by office code | One (HR0011); awards other agencies sign for DARPA are swept by funding agency 97AE |
| Office codes in text | PMW, PMS and PMA with a three-digit number | The office abbreviation (BTO, DSO, STO, TTO and the former MTO, I2O, ACO, APO), with or without a number; CSO is not read, because it also names a commercial solutions opening |
| Incumbents | Contracts the forecast rows name, and the swept awards | The swept awards alone: 3,513 base awards state a completion date and a description |
| Grants | None swept | Grants and cooperative agreements from USAspending (452 since FY2020), which FPDS does not carry |
| Budget | Procurement lines (Exhibit P-1) | Program elements (Exhibit R-2), one RDT&E book a year |
| Leaders' words | A speech archive and hearing feeds | The budgets and testimony page, read as the leaders family |
| People | Notices, forecast contacts, speakers, witnesses | The same, plus the published staff listing with office and start date |
| Federal Register | Agency slug search | Term search on the full name, kept only where the title, action or abstract names DARPA |

Every difference is a key in the `DARPA` profile of `research/tools/agency.py`; the tools branch on the agency
only to choose its folders.

## Results, 2026-09-26

The back-test replays each outcome against the events public before it. With no forecast, DARPA's outcomes are
reached late or not at all by the three-family bar: recall 0.044 at 180 and 90 days and 0.050 at 30 days over
317 outcomes, 19 reached, median lead 309 days (the five outcomes new since 2026-09-25 stand unread, so the
recall fell from 0.045 with the same 19 reached; the local floors were reset to these values, DECISIONS.md
2026-09-26). The bar's precision rose to 0.848 with the FPDS histories loaded: of 244 pilot cells, 39 reached
it, 6 are too recent to judge, and 28 of the 33 judged were followed by a notice or award within a year.

The incumbent book loads without a forecast: every swept award that states a completion date and a description
is an "incumbent contract ends" item (3,513 in the database), so the incumbent family now stands beside the
notices in the back-test, the pulse and the questions (`ask incumbents`, `ask moves`, `page vendor`). The
precision measured before the book loaded was 0.952 over 21 judged cells; both are kept as read.

A record owned by a technical office carries its code (DSO, not Defense Sciences Office), and the pilot offices
are named by code. The office-read prompts name DARPA, where they named the U.S. Navy under every profile, so the
model read every record again on 2026-09-25: 189 of 693 notices, 90 of 845 awards and 44 of 165 topics placed.
Those readings are kept; the records collected since stand unread until a key is present.

The fourth pass (2026-09-26) ran every collect stage that needs no key: 5 USAspending pages (452 grants and
cooperative agreements), 851 FPDS history pages for 719 swept awards (the stage stopped at its 40-minute limit
with the rest to take), 6 new notice details from the SAM.gov sweep, 66 new FPDS pages of FY2026 awards, and the
Federal Register, oversight and news polls, which found nothing new. The corpus grew from 5,531 to 6,720 events;
the pulse proposes 655 actions, 468 of them an incumbent contract ending. Every technical office the staff listing
names now has a requirement-side route (its program managers) and an acquisition-side route (the contacts on its
notices in the last year) in `memory/contact_recommendations.json`, each resting on observations checked against
the saved record.

## Open

- The SAM.gov yearly extracts are registered for DARPA but not yet filtered to its notices before FY2022.
- The GAO protest dockets (`CONTEXT_DEV_API_KEY`), the SBIR topic details (`BROWSERBASE_API_KEY`), the committee
  reports (`DATA_GOV_API_KEY`) and the news and conference discovery sweeps (`EXA_API_KEY`) each wait on a key.
- The office reader's prompts carry the office pages, so a collection changes every prompt; without
  `OPENAI_API_KEY` the reader keeps a record's earlier reading and `offices --check` fails until the model reads
  the changed pages. 4 notices, 222 awards, 1 special notice and 5 outcomes stand unread.
- The FPDS histories of the swept awards not yet followed (about 1,300 of the 2,000 the stage lists) wait for the
  next collect job.
- The outreach walk (`outreach.py run`) needs `OPENAI_API_KEY` and has not been run for DARPA.
- Four swept awards resolve to no office at all (their contracting and funding offices are not seed nodes; the
  example DX15 in `memory/attribution_examples.json` is one), and 35 sit at the Contracts Management Office because
  their descriptions name nothing.

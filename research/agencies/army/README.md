# Army agency intelligence

The Department of the Army read by the same tools as the Navy and DARPA layers: which program office wants what,
when, and on what evidence. Nothing here is written by hand except the source registry and the coverage matrix;
every other file is the output of one tool under `research/tools/`, run with `AGENCY=army`.

## Layout

| File | Written by | What it holds (as built on 2026-09-26) |
| --- | --- | --- |
| `memory/organization_seed.json` | `org_memory_army.py build` | 169 nodes: the Department, ASA(ALT) as a secretariat office with the code its chart prints, 36 commands (among them the Army Materiel Command, the Army Contracting Command and Army Futures Command), eight ACC contracting offices with their DoDAACs, the six portfolio acquisition executives and 27 PEOs, JPEOs and CPEs, 57 program offices and 33 persons; 148 observations and 142 relationships, each resting on a passage of a saved page |
| `memory/people.json` | `people.py build` | Empty: people are read from notice contacts, forecast contacts, speakers and witnesses, and none of those is swept for the Army yet |
| `memory/vendors.json` | `vendors.py build` | Empty until the contracts stage sweeps the ACC offices |
| `memory/small_business_offices.json` | `small_business.py build` | The Army, ACC and AMC small business offices as the Department of War directory lists them; their own pages are not saved yet |
| `events/budget_lines.json` | `budget.py extract` | Empty: the Army's procurement justification books are not saved (the budget stage is blocked, see Open) |
| `events/budget_measures.json` | `budget.py display` (in `extract`) | 481 lines from the Comptroller's FY2027 P-1 and R-1 display spreadsheets, the columns kept under the sheet's own labels (FY 2025 Actuals, FY 2026 Discretionary Enacted, FY 2027 Discretionary Request), $M, each line with its sheet and rows |
| `results/office_owners.json`, `office_owners.md` | `office_owners.py build` (the `owners` stage, 2026-09-29) | Per program office: who owns which problem, who would champion a fix, who holds the budget, each with its source; the budget lines and where each is placed; the year's obligations from the saved feed; what the office bought. 84 office section(s), 3,988 fact rows, 169 inferences (0 champion readings), 481 budget lines of which 26 placed, 0 program rows of which 0 placed, FY2026 obligations $15,191.0M over 3,407 action(s); facts, inferences and recommendations under three keys, an empty family stated as a boundary |
| `events/sbir_topics.json` | `sbir.py build` | 588 Army-component SBIR/STTR topics open FY2020 on (151 under ASA(ALT)), without topic detail |
| `events/remarks_events.json` | `remarks.py extract` | Empty until the leaders and conference sweeps run |
| `events/oversight_events.json` | `oversight.py extract` | Empty until the audits stage runs |
| `events/news_observations.json` | `news.py build` | Empty until the news sweep runs |
| `events/hiring_observations.json` | `jobs.py build` (the `hiring` stage; the `vacancies` stage lists) | 609 job announcements USAJobs filed under ARAE, ARSA, ARX7, ARX6 and ARX8 between 2026-03-31 and 2026-09-27; 24 announcement pages saved; 225 load (the acquisition workforce or a known name); 645 claims, 2 of them new signals; 522 read as an intention to hire, 46 as a selection stated, 41 withdrawn. Army Contracting Command had no code of its own in the listing (research/docs/21) |
| `events/fedreg_events.json` | `fedreg.py build` | Empty until the register stage runs |
| `events/congress_events.json` | `congress.py build` | 86 directives naming the Army in the 3 committee reports saved |
| `events/protest_events.json` | `protests.py build` | Empty: the docket stage has not been run for the Army |
| `events/assistance_awards.json` | `assistance.py build` | Empty until the collect job takes the USAspending grant and cooperative agreement pages |
| `results/notice_kinds.json` | `notice_kinds.py build` | Empty until special notices are swept |
| `results/corpus.json` | `backtest.py freeze` | 5,473 dated events (4,799 forecast lines, 588 topics, 86 directives), 4,799 needs and 136 organizations frozen from the database; no outcome yet |
| `results/outcome_labels.json` | `backtest.py label` | Empty: there is no outcome to label |
| `results/office_reads.json` | `office_wiki.py build` | 588 topics and 86 directives read against the office pages; 68 topics and 11 directives placed |
| `results/pulse.json` | `pulse.py build` | 1,748 cells scored as of 2026-09-02 (the latest event in the corpus), the week's changes and 1,280 actions, 1,274 of them a forecast line to monitor |
| `results/buying_dna.json` | `buying_dna.py build` | The eight ACC offices by DoDAAC with no base award yet, and 50 forecast cells with a position |
| `sources/source_registry.json` | by hand | 22 sources, six of them the Army's own (the chart, three forecasts, the budget materials, the speech archive) |
| `sources/coverage_matrix.json` | by hand, validated by `coverage.py` | 9 organizations by 13 evidence families |
| `sources/source_status.json` | `coverage.py status` | What each registered source last collected: 8 of 22 have collected something |

`datapack/amc_2026-05/`, `datapack/ngb_2026-05/` and `datapack/usace_2026-05/` hold the three forecast releases as rows,
classified rows, joins and layers, each with its reconciliation. `build/army/` (gitignored) holds what the load stages make: `schema.sql`, `layers.sql` and the
organization graph.

## Rebuilding

The database is local only (port 54322); set `SCHEMA_DB_CONTAINER` to the running database container first.

    export AGENCY=army
    python research/tools/pipeline.py --db army_proof --only orgpages     # the chart, the forecast, the SAM.gov records
    python research/tools/pipeline.py --db army_proof --only memory --refresh
    python research/tools/pipeline.py --db army_proof --refresh           # every build stage
    python research/tools/pipeline.py --db army_proof --collect           # also take what is new from each source

`api.army.mil` refuses a direct request from this address, so `orgpages` takes the chart and the forecast through
the hosted browser (`browserbase_fetch.py`, which needs `BROWSERBASE_API_KEY`). A build stage reads only what is
saved; the saved bytes, the documents ledger and the model cassettes are shared with the other layers.

## How the Army differs from the Navy

| Point | Navy | Army |
| --- | --- | --- |
| Forecast | Long Range Acquisition Estimates, one package per command release, fiscal year and quarter per line | The Army Materiel Command, National Guard and USACE acquisition forecasts, one workbook each; dates instead of quarters, read into fiscal year and quarter |
| Line identity | The forecast's PID | The PAN, solicitation or contract number; a number printed on several rows (one solicitation, several lines) is no row's key, each row keys on itself |
| Who owns a requirement | Program offices under PEOs and a PAE | Program offices under the PEOs, JPEOs and CPEs the forecast names; the ASA(ALT) chart of 2026-09-10 prints six PAEs and fourteen PMEs instead |
| Organization source | Command pages, the forecast's office column and the NAVSEA deputy program manager list | The ASA(ALT) organization chart, the forecast's Command and PM / Directorate columns, and SAM.gov organization records |
| Contracting | Offices swept by office code | Eight ACC offices swept by DoDAAC; a forecast line's contracting office is the DoDAAC its number opens with (DFARS 204.1603) |
| Budget | Procurement lines (Exhibit P-1) | Procurement lines (Exhibit P-40) from the Army's own budget materials |

The chart places each PME under a PAE only by its column, so that edge is `inferred` in the memory. No source
here states which PAE or PME succeeded which PEO, and none is claimed: the forecast and the notices still carry
the PEO names, and both sets of offices stand in the memory as their sources print them.

## Results, 2026-09-26

The forecasts load: the AMC workbook of 2026-05-27 gives 1,910 lines, the USACE workbook of 2026-05-28 gives 2,126
and the National Guard workbook of 2026-05-07 gives 763, 4,799 lines in all, each read into fiscal year and quarter
from its dates. An AMC line is placed under the PEO, JPEO or CPE and program office its columns name; 78 AMC lines
and every USACE and National Guard line (2,967 in all) are placed under no office. With no notice or
award swept there is no outcome, so the back-test states no recall; its 12 pilot forecast cells wait for the
notices and awards that would judge them.

The pulse scores 1,748 cells as of 2026-09-02 and proposes 1,280 actions, 1,274 of them a forecast line to monitor
and 6 an office to meet. The office reader placed 68 of 588 topics and 11 of 86 directives under an office. The coverage
matrix covers 27 of its 117 cells; every other cell states why it is empty.

## Open

- These collect stages have not been run for the Army: solicitations and notices, contracts, changes, dockets,
  register, audits, the leaders' speeches, committee reports beyond the three saved, grants, and the news and
  conference sweeps. Each is written for `pipeline.py --collect`; the recall, the incumbents and the people wait on them.
- People stay at zero until notice contacts are swept: `people.py` reads contacts from records, not the persons
  the organization chart names.
- No source here states the PEOs' parent, so none is claimed.
- The National Guard and USACE forecast lines are read into the forecast layer but placed under no office, so a
  cell reaches them only by a name its title shares with theirs.
- Topic detail pages are not fetched, so no topic carries its office.
- The budget stage is blocked: asafm.army.mil answers 403 to a direct request from this address, and the one FY2027
  book the Wayback Machine holds is not yet taken.

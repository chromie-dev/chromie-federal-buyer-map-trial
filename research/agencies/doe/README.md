# DOE agency intelligence

The Department of Energy read by the same tools as the Navy, DARPA and Army layers: which program office wants what,
when, and on what evidence. Nothing here is written by hand except the source registry and the coverage matrix;
every other file is the output of one tool under `research/tools/`, run with `AGENCY=doe`.

## Layout

| File | Written by | What it holds (as built on 2026-09-26) |
| --- | --- | --- |
| `memory/organization_seed.json` | `org_memory_doe.py build` | 82 nodes: the Department (one node for the department record 100011980 and the agency record SAM.gov prints under it, 100011981, both FPDS 8900), 13 contracting offices with their six-digit office codes, 46 program offices as the forecast's Program Office column prints them, 17 national laboratories (technical_center) and 5 other contractor-managed sites (field_activity), each with the procurement page it buys through; 104 observations and 94 relationships, each resting on a passage of a saved document |
| `sources/source_registry.json` | by hand | 15 sources, three of them the Department's own (the acquisition forecast, the small business pages, the budget justification) |
| `sources/coverage_matrix.json` | by hand, validated by `coverage.py` | 9 organizations by 13 evidence families |
| `sources/source_status.json` | `coverage.py status` | What each registered source last collected: 7 of 15 have collected something |
| `results/office_owners.json`, `office_owners.md` | `office_owners.py build` (the `owners` stage, 2026-09-29) | Per program office: who owns which problem, who would champion a fix, who holds the budget, each with its source; the budget lines and where each is placed; the year's obligations from the saved feed; what the office bought. 46 office section(s), 2,986 fact rows, 93 inferences (0 champion readings), 0 budget lines of which 0 placed, 0 program rows of which 0 placed, FY2026 obligations $5,092.7M over 2,940 action(s); facts, inferences and recommendations under three keys, an empty family stated as a boundary |

The other files of the layer (`memory/people.json`, `events/*.json`, `results/*.json`, the forecast datapack) are
written by the pipeline's build stages and have not been built for DOE. `build/doe/` (gitignored) holds what the
load stages make.

## Rebuilding

The database is local only (port 54322); set `SCHEMA_DB_CONTAINER` to the running database container first.

    export AGENCY=doe
    python research/tools/pipeline.py --db doe_proof --only orgpages     # the SAM.gov records, the notice page, the forecast
    python research/tools/pipeline.py --db doe_proof --only memory --refresh
    python research/tools/pipeline.py --db doe_proof --refresh           # every build stage
    python research/tools/pipeline.py --db doe_proof --collect           # also take what is new from each source

Every DOE document is taken by a direct request. `api.usaspending.gov` presents a certificate chain this machine's
system store does not verify; point `SSL_CERT_FILE` at the certifi bundle before a collect stage. A build stage
reads only what is saved; the saved bytes, the documents ledger and the model cassettes are shared with the other
layers.

## How DOE differs from the Navy

| Point | Navy | DOE |
| --- | --- | --- |
| Forecast | Long Range Acquisition Estimates, one package per command release, fiscal year and quarter per line | One Headquarters and Federal Field Office Acquisition Forecast (CSV, 868 rows, published 2026-09-11 and updated monthly); every row names a current incumbent and a current contract number |
| Line identity | The forecast's PID | The current contract number (FAR 4.1603 form, for example 89243126CSC000215, or the older DE-AC and DE-NA forms) |
| Who owns a requirement | Program offices under PEOs and a PAE | The forecast's Program Office column (47 values, from National Nuclear Security Administration to Western Area Power Administration); it prints no hierarchy, so each program office's edge to the Department is `inferred` |
| Organization source | Command pages, the forecast's office column and the NAVSEA deputy program manager list | SAM.gov organization records, USAspending's office list, the forecast's Program Office column and the small business page's list of contractor-managed sites |
| Contracting | Offices swept by office code | Twenty offices swept by six-digit office code (89xxxx), NNSA MO CONTRACTING (892332) first by FY2026 obligations; SAM.gov files each directly under the department (level 3) |
| Where the money goes | Prime contracts | The small business page states that approximately 80% of DOE's annual procurement base is allocated to the Management and Operating Contractors; a national laboratory buys through subcontracts under its contract and is a subcontract route, not a contracting office of the Department |
| Budget | Procurement lines (Exhibit P-1) | The Congressional Justification volumes (deferred) |

The memory ties a laboratory to the SAM.gov contractor office that posts for it (the 899xxx offices) only where the
office's printed name names the laboratory or one of its notices states the management and operating contract;
that code is kept as `contractor_office_code`, never as a contracting office code.

## Results, 2026-09-26

The forecast reads: 868 rows, 0 without an Acquisition Description, each with its Program Office, incumbent and
current contract number, and every contract number matches the profile's PIID pattern. The forecast carries no
contracting office column, so no row is placed under a contracting office. The organization memory holds 82
nodes; 10 of the 17 laboratories carry the SAM.gov contractor office that posts for them. The coverage matrix
covers 24 of its 117 cells; every other cell states why it is empty.

## Open

- These collect stages have not been run for DOE: solicitations and notices beyond the one saved page (100 of 305
  active), contracts, changes, dockets, register, audits, leaders, committee reports, grants, and the news and
  conference sweeps. Each is written for `pipeline.py --collect`; the recall, the incumbents' award dates and the people wait on them.
- `lrae_package.derive` takes a row's contracting office only from its `number`, and DOE's forecast has no number
  column (its contract number is the incumbent's), so no forecast row carries a contracting office.
  `lrae_package.value_band` does not read DOE's value bands (for example R1, $25K to $250K).
- `sam_notices.org_id_of` reads the office at level 5 of a SAM.gov path; DOE's offices sit at level 3, so the
  profile sweeps notices by organization id and leaves `sam_codes` empty.
- Seven laboratories carry no SAM.gov contractor office. TRIAD - DOE CONTRACTOR and ALLIANCE SUSTAINABLE
  ENRGY-DOECONTR post notices on the saved page, but neither their names nor those notices state which laboratory
  they serve; the other five post no notice on the saved page.
- No saved source states which program office a field office serves (Golden Field Office, National Energy
  Technology Laboratory, Idaho Operations Office), so none is claimed.
- SBIR and STTR topics are deferred for civilian agencies; the budget stage is deferred; the profile registers no
  news feed and no speech archive.

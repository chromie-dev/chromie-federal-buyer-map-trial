# NASA agency intelligence

The National Aeronautics and Space Administration read by the same tools as the Navy, DARPA and Army layers: which
center or office wants what, when, and on what evidence. Nothing here is written by hand except the source registry
and the coverage matrix; every other file is the output of one tool under `research/tools/`, run with `AGENCY=nasa`.
The profile is `research/tools/agency_profiles/nasa.py`.

## Layout

| File | Written by | What it holds (as built on 2026-09-26) |
| --- | --- | --- |
| `memory/organization_seed.json` | `org_memory_nasa.py build` | 95 nodes: the department and the agency as SAM.gov records them, nine centers and four agency contracting offices (the Shared Services Center, the IT Procurement Office, Headquarters and the NASA Management Office at JPL) with their office codes, JPL, six facilities, 29 mission directorates and headquarters offices, and 44 officials; 91 observations and 101 relationships, each resting on a passage of a saved document |
| `sources/source_registry.json` | by hand | 19 sources, five of them NASA's own (the forecast, the Organization page, the small business pages, the news feed, the budget request) and one JPL's |
| `sources/coverage_matrix.json` | by hand, validated by `coverage.py` | 12 organizations by 13 evidence families |
| `sources/source_status.json` | `coverage.py status` | What each registered source last collected: 8 of 19 have collected something |
| `results/office_owners.json`, `office_owners.md` | `office_owners.py build` (the `owners` stage, 2026-09-29) | Per program office: who owns which problem, who would champion a fix, who holds the budget, each with its source; the budget lines and where each is placed; the year's obligations from the saved feed; what the office bought. 38 office section(s), 81 fact rows, 88 inferences (11 champion readings), 0 budget lines of which 0 placed, 0 program rows of which 0 placed, FY2026 obligations $0.0M over 0 action(s); facts, inferences and recommendations under three keys, an empty family stated as a boundary |

The `events/` and `results/` folders stay empty until the build stages run for NASA. The forecast pack
(`datapack/nasa_2026-08/`) is made by `lrae_package.py build` from the saved workbook; it has not been built yet.

## Rebuilding

The database is local only (port 54322); set `SCHEMA_DB_CONTAINER` to the running database container first.

    export AGENCY=nasa
    python research/tools/pipeline.py --db nasa_proof --only orgpages     # the SAM.gov records, the Organization page, the forecast
    python research/tools/pipeline.py --db nasa_proof --only memory --refresh
    python research/tools/pipeline.py --db nasa_proof --refresh           # every build stage
    python research/tools/pipeline.py --db nasa_proof --collect           # also take what is new from each source

Every NASA document here answered a direct request. The Python certificate store on this machine lacks the Sectigo
root that api.usaspending.gov chains to, so a direct fetch from it needs `SSL_CERT_FILE` pointed at the system roots.

## How NASA differs from the Navy

| Point | Navy | NASA |
| --- | --- | --- |
| Forecast | Long Range Acquisition Estimates, one package per command release | One Agency-Wide Acquisition Forecast workbook for every center and office, updated quarterly (2026-08-04 is the release read) |
| Line identity | The forecast's PID | No line number fit for a PID: the forecast's SourceID is a bare list number, kept as a column; a line keys on its row |
| Who owns a requirement | Program offices under PEOs and a PAE | The center or agency office the BuyingOffice column names (KSC, JSC, NSSC ...); the HQMissionDirectorate column names the directorate a line serves |
| Organization source | Command pages, the forecast's office column and the NAVSEA deputy program manager list | SAM.gov organization records, the NASA Organization page, and the forecast's BuyingOffice and HQMissionDirectorate columns |
| Contracting | Offices swept by office code | Thirteen offices under agency 8000 by FAR 4.1603 office code (80GSFC, 80JSC0, 80NSSC ...); a center is its own contracting office |
| Congress | NDAA and Defense appropriations reports | Commerce, Justice, Science appropriations reports and the NASA authorization reports, which are not yearly |
| Budget, topics | Procurement lines; the DoD SBIR portal | Deferred: NASA's budget request is not a DoD exhibit, and its SBIR/STTR topics are not on the DoD portal |

The Organization page of 2026-09-22 prints four mission directorates (Research and Technology, Human Spaceflight,
Science, Mission Support). The forecast still names the Exploration Systems Development, Space Operations,
Aeronautics Research, Space Technology and Human Exploration and Operations directorates. Both sets stand in the
memory as their sources print them; no source here states which current directorate holds a former one's portfolio,
and none is claimed.

## Results, 2026-09-26

The forecast reads: 147 lines, none without a title, each with its buying office and fiscal year and quarter of
solicitation and award. KSC buys 40 of them, JSC 22, ARC 14, AFRC 13, NSSC 12, GRC 10, MSFC 8, SSC and GSFC 7 each,
ITPO 5, LaRC and HQ 4 each, and the IV&V Facility 1. USAspending shows the thirteen offices awarding 21.6 billion
dollars in FY2026, JSC the most (5.5 billion) and the Shared Services Center the most transactions (14,473).
The coverage matrix covers 23 of its 156 cells; every other cell states why it is empty.

## Open

- These collect stages have not been run for NASA: solicitations and notices, contracts, changes, dockets, register,
  audits, the leaders' hearings, committee reports, grants, and the news and conference sweeps. Each is written for
  `pipeline.py --collect`.
- The forecast's contracting office is not read: its BuyingOfficeCode column holds internal organization codes, not
  FAR 4.1603 office codes, so a line's office code comes only from the buying office the memory names.
- The incumbent contract of a forecast line sits in a second workbook (AwardeeDetails.xlsx), not yet taken.
- The forecast prints value bands NASA's own way ("$20.1M - $50M", "Over $1B"); the reader's value table does not
  hold them yet.
- JPL's own subcontract forecast (acquisition.jpl.nasa.gov/opportunities) is not taken.
- The program offices inside the directorates and centers are named by no saved source.
- Topics and the budget are deferred.

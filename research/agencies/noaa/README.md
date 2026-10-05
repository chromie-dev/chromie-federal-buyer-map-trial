# NOAA agency intelligence

The National Oceanic and Atmospheric Administration, under the Department of Commerce, read by the same tools as
the Navy, DARPA and Army layers: which line office wants what, when, and on what evidence. Nothing here is written
by hand except the source registry and the coverage matrix; every other file is the output of one tool under
`research/tools/`, run with `AGENCY=noaa`.

## Layout

| File | Written by | What it holds (as built on 2026-09-26) |
| --- | --- | --- |
| `memory/organization_seed.json` | `org_memory_noaa.py build` | 19 nodes: the Department of Commerce, NOAA (FPDS code 1330), the six line offices (NESDIS, NMFS, NOS, NWS, OAR, OMAO), the Acquisition and Grants Office (AGO), seven contracting offices (the six SAM.gov offices with their codes, among them the AGO divisions EAD, WAD and SIAD, and the Corporate Services Acquisition Division), the Grants Management Division, and the Office of Under Secretary and its staff offices as the forecast names them; 42 observations and 34 relationships (18 child_of, 16 contracts_for), each resting on a passage of a saved page |
| `sources/source_registry.json` | by hand | 16 sources, three of them NOAA's or Commerce's own (the NOAA website, the Commerce procurement forecast, the NOAA Congressional Justification) |
| `sources/coverage_matrix.json` | by hand, validated by `coverage.py` | 7 organizations by 13 evidence families |
| `sources/source_status.json` | `coverage.py status` | What each registered source last collected: 6 of 16 have collected something |
| `results/office_owners.json`, `office_owners.md` | `office_owners.py build` (the `owners` stage, 2026-09-29) | Per program office: who owns which problem, who would champion a fix, who holds the budget, each with its source; the budget lines and where each is placed; the year's obligations from the saved feed; what the office bought. 9 office section(s), 9 fact rows, 19 inferences (0 champion readings), 0 budget lines of which 0 placed, 0 program rows of which 0 placed, FY2026 obligations $0.0M over 0 action(s); facts, inferences and recommendations under three keys, an empty family stated as a boundary |

The `events/` and `results/` files and the forecast datapack are not built yet: their stages load a local database,
and no build stage has been run for NOAA.

## Rebuilding

    export AGENCY=noaa
    python research/tools/org_memory_noaa.py collect      # the SAM.gov records, USAspending listings, NOAA pages, the forecast
    python research/tools/org_memory_noaa.py build        # writes memory/organization_seed.json
    python research/tools/org_memory_noaa.py build --check
    python research/tools/coverage.py status              # writes sources/source_status.json
    python research/tools/coverage.py check

`www.commerce.gov` answers 403 to a direct request from this address, for the procurement forecasts page and for
the workbook, so `collect` takes the Wayback Machine copies (the page of 2026-01-24, the workbook of 2025-12-09).
The other build stages run through `pipeline.py --db noaa_proof`, as for the Army. A build stage reads only what is
saved; the saved bytes and the documents ledger are shared with the other layers.

## How NOAA differs from the Navy

| Point | Navy | NOAA |
| --- | --- | --- |
| Forecast | Long Range Acquisition Estimates, one package per command release, fiscal year and quarter per line | The Department of Commerce weekly procurement forecast, one workbook for every Commerce bureau; NOAA's rows are those whose Organization opens with NOAA |
| Line identity | The forecast's PID | The workbook's Forecast ID, unique on every row |
| Who owns a requirement | Program offices under PEOs and a PAE | The line office the row's Office column names (National Weather Service, National Marine Fisheries Service and the rest) |
| Organization source | Command pages, the forecast's office column and the NAVSEA deputy program manager list | The NOAA About our agency and About AGO pages, the forecast's Organization and Office columns, and SAM.gov organization records |
| Contracting | Offices swept by office code | Six AGO offices by code (1305M2, 1332KP, 1305M3, 1305M4, 1333MK, 1333MG); a PIID opens with the office code (FAR 4.1603); some older awards carry a DOC prefix |
| Budget | Procurement lines (Exhibit P-1) | Deferred for civilian agencies; the NOAA Congressional Justification is registered, not taken |
| Congress | Armed Services and Defense Appropriations reports | The Commerce, Justice, Science appropriations reports; House feeds AP00, SY00 (Science, Space, and Technology) and II00 (Natural Resources) |
| Assistance | Grants read at the Navy subtier | Grants read at the NOAA subtier (1330); the grants office 1305N2 awards assistance only |
| SBIR | The DoD SBIR portal, component NAVY | Deferred for civilian agencies |

The About AGO page names which division supports which line office: SIAD supports NESDIS, EAD supports NMFS, NOS,
NWS and OMAO, and WAD supports NMFS, NOS, NWS and OAR. The SAM.gov records give each division's code (EAD 1305M2,
WAD 1305M3, SIAD 1332KP). No source here places the Strategic Sourcing Acquisition Division (1305M4) under an AGO
division or states the Grants Management Division's code, and none is claimed.

## Results, 2026-09-26

The forecast reads: the workbook captured on 2025-12-09 gives 3,159 rows across the Commerce bureaus, each titled,
each with a unique Forecast ID; 1,980 are NOAA's. By Office they fall to the National Marine Fisheries Service
(453), the National Weather Service (449), the Staff Offices of the Office of Under Secretary (317), the National
Ocean Service (206), the Office of Oceanic and Atmospheric Research (199), NESDIS (189), the Office of Marine and
Aviation Operations (146), three AGO divisions (19) and the Office of Under Secretary (2). Every row carries its solicitation fiscal year; the
solicitation quarter is printed as an ordinal ("2nd") and is not read.

USAspending shows NOAA's FY2026 contract obligations at $2.46 billion, $1,018.5 million of it signed by 1305M2,
$620.1 million by 1332KP, $460.4 million by 1305M3 and $335.4 million by 1305M4. The coverage matrix covers 20 of
its 91 cells; every other cell states why it is empty.

## Open

- These collect stages have not been run for NOAA: solicitations and notices, contracts, changes, dockets,
  register, audits, the leaders' hearings, the committee reports, grants, and the news and conference sweeps.
- The release format has no row filter, so the forecast release is read with scope `all` and carries the other
  Commerce bureaus' 1,179 rows beside NOAA's 1,980.
- The solicitation quarter is not read from the ordinal the workbook prints.
- The workbook comes from the Wayback Machine because `www.commerce.gov` refuses a direct request; a release newer
  than 2025-12-09 is not in the record.
- The field delegate offices 1333MF, 1333MJ and 1333MH, and the grants office 1305N2, have no SAM.gov record saved.
- Awards other agencies sign with NOAA funds are not traced: no funding office is registered for the contracts sweep.
- The agency filter value of GAO's bid protest docket for NOAA is not confirmed.
- NOAA has no registered speech archive, and no hearing page or witness statement has been taken.

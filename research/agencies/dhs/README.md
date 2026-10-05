# DHS agency intelligence

The Department of Homeland Security read by the same tools as the Navy, DARPA and Army layers: which component and
office wants what, when, and on what evidence. Nothing here is written by hand except the source registry and the
coverage matrix; every other file is the output of one tool under `research/tools/`, run with `AGENCY=dhs`.

## Layout

| File | Written by | What it holds (as built on 2026-09-26) |
| --- | --- | --- |
| `memory/organization_seed.json` | `org_memory_dhs.py build` | 119 nodes: the Department, twelve components as commands (OPO, S&T, CBP, CISA, TSA, FEMA, USCG, ICE, USSS, CWMD, USCIS, FLETC), 31 contracting offices with the codes their instrument numbers open with, and 75 program offices (the APFS Organization and Requirements Office pairs with at least three records); 179 observations and 120 relationships, each resting on a passage of a saved page |
| `sources/source_registry.json` | by hand | 12 sources, two of them DHS's own (the APFS forecast and dhs.gov) |
| `sources/coverage_matrix.json` | by hand, validated by `coverage.py` | 10 components by 13 evidence families |
| `sources/source_status.json` | `coverage.py status` | What each registered source last collected: 7 of 12 have collected something |
| `results/office_owners.json`, `office_owners.md` | `office_owners.py build` (the `owners` stage, 2026-09-29) | Per program office: who owns which problem, who would champion a fix, who holds the budget, each with its source; the budget lines and where each is placed; the year's obligations from the saved feed; what the office bought. 81 office section(s), 3,254 fact rows, 163 inferences (0 champion readings), 0 budget lines of which 0 placed, 0 program rows of which 0 placed, FY2026 obligations $31,055.0M over 3,173 action(s); facts, inferences and recommendations under three keys, an empty family stated as a boundary |

The event and result files of the other layers (`events/`, `results/`, the datapack and the load stages) are not
built for DHS yet.

## Rebuilding

The database is local only (port 54322); set `SCHEMA_DB_CONTAINER` to the running database container first.

    export AGENCY=dhs
    python research/tools/org_memory_dhs.py collect                     # the SAM.gov records, the USAspending answer, the APFS records
    python research/tools/org_memory_dhs.py build
    python research/tools/coverage.py status
    python research/tools/pipeline.py --db dhs_proof --refresh           # every build stage
    python research/tools/pipeline.py --db dhs_proof --collect           # also take what is new from each source

On this machine Python does not find a certificate authority bundle for api.usaspending.gov; set `SSL_CERT_FILE` to
the `certifi` bundle of the virtual environment before a collect. A build stage reads only what is saved; the saved
bytes, the documents ledger and the model cassettes are shared with the other layers.

## How DHS differs from the Army

| Point | Army | DHS |
| --- | --- | --- |
| Scope | One military department (subtier 2100) | A whole department (toptier 070, subtier 7000); assistance awards are read at the toptier |
| Forecast | Three workbooks (AMC, National Guard, USACE), packaged as releases | The APFS forecast, one JSON array of 873 published records read by the organization memory; no release is packaged, since `read_sheet` reads workbook and CSV bytes only |
| Line identity | The PAN, solicitation or contract number | The APFS number (F2026074287 style); 249 records also print the incumbent contract number |
| Who owns a requirement | Program offices under the PEOs, JPEOs and CPEs | The component and office the APFS Organization column prints (USCG/CG-SHORE, DHS HQ/CISA) and its Requirements Office |
| Organization source | The ASA(ALT) chart, the forecast columns and SAM.gov records | SAM.gov organization records and hierarchy listings, the USAspending sub-agency answer and the APFS columns |
| Contracting | Eight ACC offices by DoDAAC | 31 offices by the six-character code their numbers open with (FAR 4.1603); S&T, CISA and CWMD buy through the OPO divisions 70RSAT, 70RCSJ and 70RWMD |
| Budget | Procurement lines (Exhibit P-40) | Deferred: the congressional justifications (www.dhs.gov/cj) are not saved |
| Congress | Armed Services and Defense appropriations | Homeland Security appropriations (House and Senate), the House Homeland Security (HM00) and Appropriations (AP00) feeds |
| Topics | The DoD SBIR portal | Deferred: DHS SBIR topics (DHS241-001 style) are posted on SAM.gov |

SAM.gov places the Coast Guard, FEMA and the other operating components directly under the Department, and the
CISA, S&T and CWMD contracting divisions under the Office of Procurement Operations. The memory keeps both as the
records print them. A contracting office is placed as buying for a component only where the APFS Contracting Office
column prints its code (70RCSJ for CISA, 70RSAT for S&T).

## Results, 2026-09-26

The organization memory holds 119 nodes, each with at least one observation. By FY2026 obligations in the USAspending
sub-agency answer, the largest contracting offices are 70B01C (CBP Administration Facilities Training Contracting
Division, $30.0 billion), 70Z023 (USCG HQ Contract Operations, $8.2 billion), 70CDCR (ICE Detention Compliance and
Removals, $6.1 billion), 70B02C (CBP Air and Marine, $2.3 billion), 70B04C (CBP Information Technology, $1.7
billion), 70SBUR (USCIS, $1.3 billion), 70CTD0 (ICE Information Technology, $1.3 billion), 70Z047 (USCG FDCC, $1.1
billion) and 70T040 (TSA Security Technology, $1.1 billion). FEMA's largest awarding office, 70FGRT (Financial
Assistance Awards, $24.6 billion), awards assistance, not contracts, and is not seeded.

The APFS forecast of 2026-09-26 carries 873 published records, each with a title: 497 USCG, 100 CBP, 52 TSA, 46
USCIS, 36 CISA, 36 ICE, 31 FEMA, 21 USSS, 21 FLETC, 4 S&T and 1 OPO, with 28 under headquarters offices; no record
names CWMD. The coverage matrix covers 20 of its 130 cells; every other cell states why it is empty.

## Open

- These collect stages have not been run for DHS: solicitations and notices, contracts, changes, dockets, register,
  audits, committee reports, grants, and the news and conference sweeps. Each is written for `pipeline.py --collect`.
- The APFS records are JSON. `lrae_package.read_sheet` reads workbook and CSV bytes only and the package build admits
  only spreadsheet and CSV rows, so the forecast is not packaged into forecast lines.
- A SAM.gov hierarchy listing returns at most 100 children. Offices beyond the first 100 of USCG (1627), ICE (335) and
  FEMA (120) are read from their own records when seeded; the rest of those components' offices are not listed.
- Budget and SBIR topics are deferred. The DHS appropriations report titles are in the shared govinfo listings, but
  no report text is saved.
- dhs.gov publishes no speech or testimony archive the layer reads. The GAO docket filter value for DHS is unconfirmed.
- The Federal Protective Service divisions (70RFP1 to 70RFP4) obligate above $200 million each in FY2026 while
  SAM.gov lists them as inactive; they are not seeded.

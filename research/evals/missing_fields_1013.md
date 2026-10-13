# Missing fields that change a vendor's next action (B14, 2026-10-13)

This report measures how often production fills the fields a vendor needs to decide its next step on an open opportunity, and where each missing field can be found.
The biggest gaps are who to call, whether the work is set aside, and whether the vendor must already hold a contract vehicle. Most of these sit in files we already have and are dropped when they are loaded.
Queries: `research/evals/missing_fields_1013.sql`. Production read only, through the MCP server. Local raw files read with a scratch script.

## Scope and rules

- **Open SAM notice**: a live `agency_brain_items` row (what `agent.items` shows) from `sam_gov_site_api`, not an award or
  modification, with `responses_due` on or after 2026-10-13. **1,303 open notices** across the 13 agencies.
- **Open forecast line**: the newest live forecast item per line. Its need in `agent.needs` is not fulfilled or cancelled, and the
  need's newest award window end (`expected_to`) is either empty or on or after 2026-10-13. **10,222 open lines** (10 agencies; USAF, DARPA
  and EPA have no forecast lines). 11,525 open items in all.
- **Filled** (forecast fields): non-empty and not a placeholder (TBD, to be determined, unknown, n/a, none, "not available", 0, FYXXXX).
- **Where a field can live**: *view*: `agent.items.facts_as_written` (the stored `data`, whole) or `agent.needs`. *App table*: production's
  `sam_opportunities` and its linked `gov_contacts` (in production, not in any agent view). *Raw*: our local SAM JSON (`data/raw/sam_notices_*`) and
  forecast files (`datapack/*/rows_raw.csv`, the DHS forecast system's JSON). These were loaded, but the field was dropped.
- **Gap type**: *view gap* means production holds the field and a view does not show it, so the fix is the view SQL. *Load gap* means our raw file holds it and the loader drops it,
  so the fix is the loader plus a reload. *Source gap* means the publisher does not state it.

## Facts

### Fill rate per field, open items

| Field | SAM notices in views (1,303) | Forecast lines in views (10,222) | All open in views | Where the missing part is | Gap type |
|---|---|---|---|---|---|
| Response deadline / next date | 100% by rule (98% of 5,840 notices posted since 08-01 carry one) | 81% (60% on the item; the rest is `agent.needs.expected_from`) | 83% | DOE: raw `performance_end_date` 100%. Army and DOT: source states no date for about half | load (DOE), source (Army, DOT) |
| Contact name | 0% (app table 44%, raw 81%) | 58% | 51% | SAM `pointOfContact`. NASA technical contact 84% raw. Navy 2026-07 files state none | load + view (SAM, NASA), source (Navy, HHS) |
| Contact e-mail / phone | 0% / 0% (app table 44% / 27%, raw 80% / 44%) | about 1% (an e-mail typed into a name field) / 0% | about 1% / 0% | SAM raw. DHS raw e-mail and phone 100%. NOAA e-mail 100%. NASA 99%. Small-business office e-mail about 100% in DOE, NASA, Army (AMC, NGB, USACE) | load + view |
| Set-aside / small business type | 0% (app table 20%, raw 36% stated; absent usually means none) | 51% named (DHS 0%: the True/False column was loaded) | 45% | SAM `solicitation.setAside`. DHS raw `small_business_program` 51%. NOAA raw `type_of_awardee` 25% | load |
| Contract vehicle / must hold it | 0% (notice text mentions one in 12%) | 12% field present, 3% names a vehicle (DHS 100%, NOAA 19%, Army 11%) | 10% | Raw `procurement_instrument` (delivery order, BPA call, contract) 72-100% in 11 of 15 forecast files, loaded nowhere | load (forecast), source (SAM) |
| Incumbent / existing contract no. | 0% (texts name an incumbent in 0.7%, follow-on wording in 6.5%) | 74% / 85% of 3,827 recompete lines | 25% of all open | NASA forecast has no such column. Army states the number but rarely the name | source; FPDS join possible |
| Estimated value | 1.6% (21 notices tied to a forecast line) | 99% (95% also in `agent.needs.estimate_as_written`) | 88% | Notices rarely state a value | source |
| NAICS / PSC | 0% / 0% (app table 42% / 45%, raw 77% / 83%) | 88% / 33% | 78% / 29% | SAM raw `naics`, `classificationCode`. DHS, HHS, DOE, NOAA forecasts carry no PSC | load (SAM), source (forecast PSC) |
| Contracting office | 99.8% | 54% (role or stated center) | 60% | DOE, DOT, NASA, NOAA lines 0%. NASA raw `buying_office_code` 82% | source, load (NASA) |
| Owner (requirement) office | 11% | 95% | 86% | Notices name an owner only when the text names a known office or ties to a forecast line | source |
| Place of performance | 0% (app table 26%, raw 57%) | 55% | 48% | SAM raw `placeOfPerformance` | load |
| Notice kind (solicitation, sources sought, special) | 100% (title and event type) | n/a | - | - | none |
| Clearance required | 0% | 0% | 0% | Navy forecast raw facility/personnel clearance 74-89%. DOT small-business forecast 83% | load |
| Awarded or withdrawn flag | n/a | not loaded | - | NASA raw `awarded_or_withdrawn` 16% of rows | load |

### Open SAM notices per agency (none of these fields is in a view)

| Agency | Open (prod) | App table: NAICS / PSC / set-aside / POC e-mail / phone | Open (local raw) | Raw: NAICS / PSC / set-aside / place / POC e-mail / phone |
|---|---|---|---|---|
| DARPA | 28 | 54% / 50% / 0% / 50% / 0% | 28 | 100% / 100% / 0% / 7% / 100% / 0% |
| DHS | 27 | 37% / 37% / 22% / 41% / 19% | 27 | 89% / 89% / 56% / 44% / 100% / 41% |
| DOE | 8 | 50% / 38% / 25% / 75% / 38% | 7 | 57% / 43% / 29% / 57% / 100% / 43% |
| DOT | 57 | 67% / 58% / 47% / 67% / 26% | 57 | 98% / 91% / 68% / 84% / 100% / 26% |
| EPA | 8 | 25% / 25% / 12% / 25% / 25% | 9 | 100% / 100% / 56% / 44% / 100% / 100% |
| GSA | 3 | 33% / 33% / 0% / 67% / 67% | 34 | 94% / 97% / 26% / 100% / 100% / 94% |
| HHS | 39 | 41% / 41% / 26% / 49% / 23% | 42 | 93% / 90% / 48% / 79% / 100% / 50% |
| NASA | 56 | 30% / 29% / 18% / 32% / 9% | 57 | 96% / 96% / 23% / 40% / 100% / 12% |
| NOAA | 4 | 25% / 25% / 25% / 50% / 0% | 6 | 83% / 83% / 17% / 67% / 100% / 33% |
| USA | 492 | 56% / 54% / 27% / 58% / 44% | 499 | 93% / 91% / 49% / 73% / 99% / 66% |
| USAF | 488 | 23% / 35% / 12% / 26% / 12% | 484 | 45% / 65% / 21% / 37% / 48% / 20% |
| USN | 36 | 50% / 47% / 8% / 25% / 14% | 53 | 100% / 98% / 45% / 47% / 83% / 42% |
| VA | 57 | 68% / 68% / 30% / 68% / 60% | 55 | 100% / 100% / 35% / 84% / 100% / 78% |
| ALL | 1,303 | 42% / 45% / 20% / 44% / 27% | 1,358 | 77% / 83% / 36% / 57% / 80% / 44% |

In the views, every open notice carries its deadline, solicitation number, notice kind and an office, and nothing else.
Contracting office 1,301 of 1,303. Owner office 140. Notice kinds: solicitation 652, special notice 448, sources sought or RFI 218, justification 19.
USAF's open set is 287 of 488 special notices.

### Open forecast lines per agency (views)

| Agency | Open lines | Next date (any / on item) | Contact name | Set-aside named | Vehicle | Incumbent / contract no. (of recompetes) | Value | NAICS / PSC | Contracting / owner office | Place |
|---|---|---|---|---|---|---|---|---|---|---|
| DHS | 621 | 100% / 100% | 100% | 0% | 100% | 98% / 98% (191) | 100% | 100% / 0% | 100% / 100% | 88% |
| DOE | 868 | 0% / 0% | 0% | 93% | 0% | 100% / 100% (868) | 100% | 100% / 0% | 0% / 100% | 100% |
| DOT | 177 | 44% / 44% | 100% | 55% | 0% | 95% / 95% (65) | 93% | 94% / 0% | 0% / 100% | 11% |
| GSA | 132 | 98% / 98% | 100% | 64% | 0% | 58% / 62% (26) | 99% | 87% / 0% | 90% / 40% | 0% |
| HHS | 2,326 | 100% / 100% | 100% | 49% | 0% | 85% / 92% (847) | 100% | 84% / 0% | 73% / 100% | 0% |
| NASA | 112 | 76% / 76% | 0% | 66% | 0% | 0% / 0% (49) | 76% | 76% / 76% | 0% / 100% | 67% |
| NOAA | 1,947 | 100% / 100% | 100% | 23% | 19% | 55% / 45% (641) | 100% | 100% / 0% | 0% / 100% | 93% |
| USA | 1,803 | 49% / 49% | 11% | 64% | 11% | 37% / 96% (281) | 95% | 62% / 85% | 52% / 86% | 11% |
| USN | 2,150 | 99% / 0% | 20% | 64% | 0% | 63% / 93% (840) | 100% | 96% / 80% | 100% / 95% | 96% |
| VA | 86 | 99% / 99% | 100% | 62% | 0% | 26% / 26% (19) | 95% | 100% / 0% | 49% / 49% | 0% |
| ALL | 10,222 | 81% / 60% | 58% | 51% | 12% | 74% / 85% (3,827) | 99% | 88% / 33% | 54% / 95% | 55% |

### Other facts

- A SAM notice row stores `data = {line, responses_due}`, and `source` adds the notice id, solicitation number and type. The loader
  (`research/tools/agency_layers_sql.py`, the `responses_due` line, and `trace.notice_detail`) reads only the title, solicitation, type, posted date,
  award block, organization and deadline from the raw JSON. The raw JSON also holds `naics`, `classificationCode`, `solicitation.setAside`,
  `placeOfPerformance`, `pointOfContact[]` (name, e-mail, phone), `archive.date` and the description text.
- Local raw has 34 open GSA notices, and production has 3. USN has 53 locally and 36 in production.
- The newest SAM notice in production was posted 2026-10-08 (retrieved 10-09). Notices posted after that are not in the open set.
- 4,480 of the 10,222 open forecast lines (44%) count as open only because they state no award window: DOE 868, NOAA 1,947, Army 1,300.
  NOAA's release is dated 2025-12, and its solicitation years go back to 2021.
- NASA's raw file marks 16% of rows awarded or withdrawn. That flag is not loaded, and only 1 NASA line is closed in production.
- Forecast dates are stored as written, in mixed formats: ISO dates (DHS, AMC), year-month (HHS), year only (NOAA, NASA), "FY26" or "26" (NGB),
  "FY2019, Q3" or "FYXXXX, Q4" (USACE). `agent.needs.expected_from/expected_to` holds them as real dates, for 99% of Navy lines and none of DOE's or NOAA's.
- DHS has two live releases (09-26 and 09-29) in `agent.items`: 1,701 items for 885 lines.
- The DHS forecast system's own JSON holds `small_business_program` (SB, 8(a), HUBZone and others; 51% name one), requirement and alternate contact
  e-mail and phone (100% and 52%), a small-business specialist e-mail (99%), `competitive` and `contract_status`. The loaded set-aside field is the
  True/False `small_business_set_aside`.
- The `procurement_instrument` column holds values like Delivery Order, BPA Call, Definitive Contract and "C" Type Contract. 92% of VA rows and 64% of GSA rows are orders or BPA calls.

## Inferences

- About half the gaps are load gaps. The publisher states the field and we hold it locally, but the loader keeps only what an earlier question needed.
  For SAM notices, one change at the loader's `data` dict would take NAICS, PSC, place and contact e-mail from 0% to 57-83%, and stated set-aside to 36%.
- Production's second SAM copy (`sam_opportunities` with `gov_contact_opportunities`) covers 44-52% of the open notices. A view join would show that much
  with no reload. The loader fix covers more, because the app copy misses about half the notice ids.
- "Delivery Order" or "BPA Call" on a forecast line means only holders of the parent vehicle can bid. Without it, an agent cannot tell a vendor whether to bid as prime or find a holder to team with.
- A SAM notice with no `setAside` usually means no set-aside, but the views cannot say "none stated" because the field is not stored at all.
- The open forecast count overstates what is live: the 44% with no window include stale releases (NOAA 2025-12) and lines NASA already marks awarded.
- Incumbents for open notices are not in SAM. They could be inferred by matching the solicitation number or the forecast's existing contract number to the
  FPDS rows already in `vendors_incumbents`, labelled as a match, not a stated fact.

## Recommendations: fills that change the next action most

| # | Fill | Action it decides | In views now (open) | Gap | Cheap fix |
|---|---|---|---|---|---|
| 1 | Contact e-mail and phone | who to call | about 1% | load + view | SAM: write `pointOfContact[]` into `data`. Forecasts: carry `*_contact`, requirement/alternate e-mail and phone, and small-business office e-mail into `stated`. Interim: view join to `sam_opportunities` contacts (44% of notices) |
| 2 | Set-aside type | bid prime vs team | 45% (notices 0%) | load | SAM: `solicitation.setAside` into `data`, with "none stated" when absent. DHS: load `small_business_program`, not the boolean. NOAA: load `type_of_awardee` |
| 3 | Vehicle and order type (must hold it) | bid prime vs team, or skip | 10% (3% named) | load | carry `procurement_instrument` into `stated` (11 of 15 files, 72-100%). Notices: source gap, text only |
| 4 | NAICS / PSC on notices | fit and size standard (can bid as small) | notices 0% | load | same loader change as 1-2 (`naics`, `classificationCode`) |
| 5 | Next date on forecast lines | act now | 81% | load (DOE), source (Army, DOT) | load DOE `performance_end_date` (100%, the recompete clock). Have the pursuit skill read `agent.needs.expected_from/to` as the date, not the as-written string |
| 6 | Incumbent and contract number on notices | recompete worth chasing | notices 0%, forecast recompetes 74% / 85% | source | match solicitation number or existing contract number to FPDS rows in `vendors_incumbents`, labelled as a match |
| 7 | Owner office on notices | who to call for the program (not the contracting officer) | notices 11% | source | tie more notices to forecast lines (21 of 1,303 tied). Load NASA `buying_office_code` for forecast contracting office |
| 8 | Value on notices | money real | notices 1.6% | source | as 7 (forecast tie), and the prior award's ceiling via the FPDS match in 6 |
| 9 | Clearance required | go / no-go | 0% | load | carry facility/personnel clearance into `stated` (Navy 74-89%, DOT 83%) |
| 10 | Place of performance on notices | go / no-go (site) | notices 0% | load | same loader change as 1-2 (`placeOfPerformance`) |
| 11 | Open-state accuracy | act now (is it still open) | - | load | load NASA `awarded_or_withdrawn`. Show one live item per DHS line. Flag forecast releases older than a set age |

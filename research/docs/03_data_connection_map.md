# 03 - Data-connection map: identifiers, joins, and where the chain breaks

Field names are as observed in the documents listed in `research/sources/documents_manifest.jsonl` (FPDS ATOM
feeds, the NAVWAR LRAE, USAspending award detail, the PEO C4I pages). Budget identifiers are
described from their public definitions.

## In plain terms

Money, forecasts, notices and awards each carry their own identifiers, and only a few of them
appear in more than one source. The contracting office code and the contract number travel
well; the program-office code travels only as text, and the budget line travels only as a
program name. So the chain "budget line -> forecast row -> notice -> award -> program office"
is joinable at every step except the first, where the link is an inference by program name.

## 1. Identifiers

| Identifier | Example | Issued by | Where it appears | What it connects | Pitfalls |
| --- | --- | --- | --- | --- | --- |
| Contracting office UIC / DoDAAC | `N00039` (NAVWAR HQ), `N66001` (NIWC Pacific), `N65236` (NIWC Atlantic) | DoD activity address directory | FPDS `contractingOfficeID`; LRAE "Contracting Office UIC"; first six characters of every PIID and solicitation number; LRAE PID prefix; SAM notice office | Award <-> forecast <-> notice by issuing office | Names the buyer's contracting office, never the requirement owner |
| Funding office DoDAAC | `N00024` (NAVSEA HQ) on `N0003919C0002` | same directory | FPDS `fundingRequestingOfficeID`; USAspending `funding_agency.office_agency_name` | Award -> paying organization | At NAVWAR the funding office usually equals the contracting office, so it separates systems commands, not PMWs |
| PIID (contract or order number) + modification | `N0003919C0002` (definitive contract), `N0003920D0054` (IDC), `N0003922F3000` (order), mod `P00040` | issuing contracting office | FPDS `PIID`/`modNumber`; USAspending `piid`; LRAE "Existing Contract Number"; SAM award notices | Forecast -> incumbent contract; notice -> award; all modifications of one award | Ninth character encodes the instrument (C, D, F, ...); the LRAE sometimes concatenates IDV and order numbers (`N0017819D8470N0003920F3015`) |
| Referenced IDV PIID | `N0017819D7264` (SeaPort-NxG, issued by `N00178`) | issuing office of the vehicle | FPDS `referencedIDVID`; USAspending `parent_award_piid`; LRAE PALT codes `K-S - Contract - SeaPort TO` | Order -> vehicle; groups task orders competed inside a vehicle | Orders under SeaPort-NxG are issued by NAVWAR (`N00039…F…`) against a Dahlgren-issued IDV; the vehicle's RFPs live in the SeaPort portal, not SAM |
| Solicitation number | `N0003919R0002`, `N0003921R3015` | contracting office | FPDS `solicitationID`; SAM `solicitationNumber`; NAVWAR CSO page links | Notice <-> award | Absent from the LRAE, which uses PIDs instead |
| SAM notice id | 32-hex opportunity id (e.g. the notice linked from the NAVWAR CSO page) | SAM.gov | SAM only | Notice versions, amendments, attachments | Not present in FPDS or the LRAE |
| LRAE PID number | `N00039-24-RFPREQ-PMW-160-0002` | Navy contracting activity | LRAE only | Forecast row -> office code -> contracting office | Not resolvable in any public system; internal to the activity |
| LRAE requirement-office code | `PMW-160`, `PMW/A-170`, `PMA/PMW-101`, `PMS-485`, `LSUBP00035`, `NP-41200`, `Pf007NERP`, `C4IEXEC`, `DRPM-NOA` | Navy activity | LRAE "Associated Program or Requirement Office" | Forecast row -> organization (via alias table) | Three code families on one sheet: PEO program offices, NIWC competencies, NAVWAR HQ and PEO front-office codes; PEO Digital and PEO MLB do not use PMW codes here |
| Program-office code in free text | "PMW 160 PROGRAM OFFICE", "TACTICAL NETWORKS PROGRAM OFFICE (PMW 160)" | author of the text | FPDS/USAspending `descriptionOfContractRequirement`; SAM titles, descriptions and attachments; tear sheets; DVIDS | Award or notice -> program office (the resolver's decisive evidence) | "PEO C4I" alone is a portfolio, not an office; lists of several offices mean multi-office support; negations and "formerly" clauses |
| Program or system name | CANES, ADNS, NMT, CSRR, MIDS-LVT, CLTS | program office | tear sheets (official program -> office mapping); award descriptions; LRAE titles; budget line-item titles | Budget line -> program -> office (inference); award -> program | A platform or program name is candidate evidence only; ownership needs an official mapping and a date |
| Appropriation, budget activity, line item, program element, project | e.g. RDT&E,N program elements `06xxxxxN`; OPN line items; O&M,N sub-activity groups | DoN budget office | DoN justification books (RDT&E R-2/R-2A, Procurement P-1/P-40, O&M OP-5); congressional funding tables and explanatory statements | Request -> enacted mark -> program | No public source ties a program element to a contract; the join to a PMW is by program name in the exhibit narrative |
| NAICS / PSC | `541330`, `R408`, `5845` | Census / GSA | FPDS, USAspending, LRAE, SAM | Candidate generation and category coverage | Never ownership evidence |
| UEI / CAGE / vendor name | Booz Allen Hamilton, BAE Systems | SAM entity registration | FPDS, USAspending, SAM; LRAE "Incumbent Contractor" (name only) | Incumbent -> follow-on forecast; award history | Names differ across sources; the LRAE has no UEI |
| Notice type and lifecycle | Sources Sought, Presolicitation, Solicitation, Award, Special Notice, amendment | SAM.gov | SAM | Stage of the acquisition | LRAEs are sometimes posted as SAM Special Notices (NAVSUP, NAWCAD) |
| Dates | LRAE anticipated solicitation/award FY+quarter; SAM posted/response dates; FPDS `signedDate`, period of performance; budget fiscal-year columns; Wayback capture timestamp | each source | each source | Timing, advance-notice measurement | Fiscal quarters, not dates, in the LRAE; DoD FPDS records are published about 90 days after signature |
| People | program manager names on tear sheets and DVIDS releases; contracting POC names and `.mil` emails in the LRAE | each source | tear sheets, DVIDS, LRAE, SAM POC fields | Person -> role -> office with an observation date | LRAE POCs are contracting staff, not requirement owners |

## 2. How the sources join

```mermaid
flowchart LR
  B[Budget line: appropriation / PE / line item<br/>DoN J-books, congressional tables] -- program name (inference) --> PO[Program office<br/>PMW code]
  L[LRAE row<br/>requirement-office code, PID, contracting UIC] -- office code --> PO
  L -- Existing Contract Number --> A[Award / order<br/>PIID + mods]
  L -- title, office, NAICS, timing (match) --> N[SAM notice<br/>notice id, solicitation number]
  N -- solicitation number --> A
  N -- office code in text / attachments --> PO
  A -- description text --> PO
  A -- referencedIDVID --> V[Vehicle IDV<br/>e.g. SeaPort-NxG N00178…D…]
  A -- contractingOfficeID --> CO[Contracting office<br/>N00039 / N66001 / N65236]
  A -- fundingRequestingOfficeID --> FO[Funding organization<br/>e.g. N00024 NAVSEA HQ]
  C[Congressional mark<br/>explanatory statement line] -- PE / line item --> B
  D[DVIDS / tear sheets] -- name + role + date --> P[Person] -- leads --> PO
  PO -- child_of (dated) --> PEO[PEO C4I] -- consolidated_into 2026-05-11<br/>mission-systems elements; offices not itemized --> PAE[PAE Mission Systems]
  PO -. child_of only where the office's own source says so<br/>e.g. PMW/A 170, notice of 2026-05-22 .-> CPE[PAE MS capability portfolio] --> PAE
```

Solid joins (an identifier shared by both sides): LRAE row -> award (existing contract number);
notice -> award (solicitation number); order -> vehicle (referenced IDV); award -> contracting
and funding office (DoDAACs); LRAE row -> office (requirement-office code, via the alias table in
`research/memory/organization_seed.json`).

Text joins (evidence must be quoted): award -> office and notice -> office through an office code
or explicit ownership language; person -> office through a dated official statement.

Inferred joins (labelled as inference): budget line -> office by program name; LRAE row -> notice
by title, office, NAICS and timing when no solicitation number has been published yet.

## 3. Keeping money categories distinct

| Category | Meaning | Source that holds it | Not to be confused with |
| --- | --- | --- | --- |
| Requested funding | what the Department asked Congress for, by fiscal year, appropriation and line | DoN justification books | enacted amounts |
| Enacted appropriation and marks | what Congress funded, added, cut or directed, by line | appropriations acts, explanatory statements and committee reports on govinfo and congress.gov | obligations |
| Anticipated total value | the LRAE's value range for a future procurement, including options | LRAE "Anticipated Total Value (Including Options)" | a budget line or a ceiling; it is an estimate with a disclaimer |
| Obligation | money placed on a contract action | FPDS `obligatedAmount` per action; USAspending `total_obligation` per award | the award ceiling |
| Ceiling / base and all options | maximum value of an award | USAspending `base_and_all_options`; FPDS `baseAndAllOptionsValue` | obligations to date |

A budget line funds many contracts and a contract can draw on several lines; the public record
does not publish the mapping. The connection is therefore made at the program level and stated
as such.

## 4. Where the chain breaks

| Break | Why | What is recorded instead |
| --- | --- | --- |
| Budget line -> forecast row | the LRAE has no program-element or line-item column | program-name inference with both documents cited |
| Forecast row -> notice | PIDs are not published in SAM; some solicitations are issued through PIEE or inside SeaPort-NxG | match on title, office, NAICS and timing; `unresolved` when no notice can be found |
| Notice -> award for vehicle orders | SeaPort-NxG task-order RFPs are not on SAM | award-only lineage through the referenced IDV |
| Award -> office | descriptions such as "DE-OBLIGATION" or "CEILING REALIGNMENT" carry no owner | `unresolved`, with the base award's evidence checked instead |
| Office -> current parent | PEO C4I's mission-systems elements moved to PAE Mission Systems from 2026-05-11; the release itemizes no offices, and NAVWAR pages still list the PEO | the consolidation is kept at the PEO level with the release's scope wording; an office moves under a PAE portfolio only when its own source says so (PMW/A 170, 2026-05-22), otherwise its parent claim stands with its last-confirmed date |
| Any source -> classified or CUI requirement | not public | absent by construction |

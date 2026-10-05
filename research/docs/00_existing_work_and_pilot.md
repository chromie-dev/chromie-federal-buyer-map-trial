# 00 - Existing work, verified organization, and pilot definition

Every claim below cites a document in `research/sources/documents_manifest.jsonl` (appendix at the end);
bracketed tags like [A] point at that appendix.

## In plain terms

The platform already has a production Agency Brain and a program-office resolver, and the Navy's
PEO C4I offices are already in its organization graph. The pilot adds the Navy-specific
source layer: the forecasts, budget books, notices and awards that show what each office is
about to buy. The pilot portfolio is mid-reorganization: on 2026-05-11 the Department of the
Navy stood up a Portfolio Acquisition Executive for Mission Systems that absorbs PEO C4I, so the
organization map has to carry dates, not just a tree. The single most useful public source is
NAVWAR's Long-Range Acquisition Estimate spreadsheet, because it names the requirement
office, the contracting office, the incumbent and the existing contract on the same row.

## 1. What exists and what is usable

### 1.1 The prototype repository (`chromie-federal-buyer-map-trial`)

`README.md` is the prototype specification. `src/buyer_map/` holds a synthetic graph validator
and an edge ranker over a five-node example; nothing in it is Navy-specific.
Usable: the working agreement (evidence is part of the data model; facts, inferences and
recommendations stay separate) and the output-package file names, which this package reuses.

### 1.2 Production scaffolding already in the platform

| Component | Where | State | Reusable for this pilot |
| --- | --- | --- | --- |
| Agency Brain pipeline: locate documents, fetch, chunk by page, extract claims per section, compile a page | production runner `orchestration/gov/agency_brain/` (`documents.py`, `extract.py`, `items.py`, `rollups.py`, `worker.py`) | Production; per-agency ingest modules for DHS, DOJ, VA, HHS, DOT, Treasury, USDA, NIH, DHA and a manual DoD loader (`dod_ingest.py`) | Yes: claim sections `budget`, `forecast`, `procurement_patterns`, `people`, `industry_engagement` fit the Navy signals; items can be scoped to an organization (`scope_organization_id`), so PMW-level claims are representable |
| Program-office resolver | `program_office_resolver.py`; `program_office_resolution_service.py` | Production; DB-independent pure function; evidence-gated (an office code or explicit ownership language is required; contracting office, platform, NAICS, vendor cannot create ownership); statuses include abstention | Yes: the attribution rulebook in section 04 follows the same evidence classes so examples can be replayed through it |
| Canonical organization graph | tables `gov_organizations`, `gov_organization_relationships` (temporal `valid_from`/`valid_to`, provenance columns); `scripts/onboard_resolver_reference_organizations.py` | PEO C4I and its 11 PMWs are already rows, sourced from NAVWAR's 2023 anniversary article; PAE Maritime, Aviation, Munitions and Marine Corps transitions are modeled with dates | Yes: 2025-2026 office names differ from the 2023 article (see 3.2) |
| Source registry table | `gov_procurement_sources` (`source_key`, `access_mode`, `refresh_cadence`, `last_verified_at`, `verification_status`, `known_access_gaps`) | Production, used for SLED portals | Yes: `research/sources/source_registry.json` uses these field names so it can be loaded into that table |
| Procurement records and documents | `gov_procurement_records` (solicitation/predecessor/resulting-contract links, lineage), `gov_procurement_documents` (hash, retrieval and extraction status), `gov_procurement_organizations` (organization to notice/award edges) | Production | Yes: the natural home for attribution edges and notice lineage |
| Intel facts and links | `gov_intel_records/facts/links` (event type, effective date, conflict status, superseding, review status) | Production | Yes: the natural home for alerts and signals |
| SAM notice amendment tracking | production runner PRs #233, #234, #241 | Production | Yes: change detection for notices exists |
| Hidden blind evaluator for the resolver | production runner `docs/hidden_program_office_eval.md` | Production | Worked examples here are gold candidates, not evaluator cases |

## 2. Verified organization

### 2.1 Ancestry

```
Department of the Navy
  |- Naval Information Warfare Systems Command (NAVWAR), San Diego                     [G][H]
  |    |- NAVWAR HQ contracts directorate, UIC N00039 ("NAVAL INFORMATION WARFARE SYSTEMS") [G][J]
  |    |- NIWC Pacific (technical center), contracting UIC N66001                          [G][J]
  |    |- NIWC Atlantic (technical center), contracting UIC N65236                         [G][J]
  |    |- NSFA; DRPM Project Overmatch (listed in the 2026-09-01 NAVWAR navigation)        [H]
  |    |- PEO C4I ("PEO C4I & Space" in site footers), Old Town Campus, San Diego          [A][G]
  |    |    |- PMA/PMW 101, PMW 120, PMW 130, PMW 150, PMW 160, PMW/A 170,
  |    |       PMW 740, PMW 750, PMW 760, PMW 770, PMW 790                                 [A][B][L]
  |    |- PEO Digital and Enterprise Services; PEO Manpower, Logistics and Business        [A][G]
  |
  |- PAE Mission Systems (from 2026-05-11, interim PAE Jim Day): "pulling together the
     mission systems elements of" PEO C4I, PEO Digital, PEO IWS, PEO MLB, DRPM Overmatch,
     DRPM LRNFO, DRPM MILE, the Minotaur program (PMA 290), NAVWAR, NAVSEA, NAVAIR, MCSC   [E]
```

Two official statements conflict in time and both are kept: the NAVWAR "Work With Us" page
captured 2026-09-01 still describes NAVWAR HQ as the office that "procures the IW systems
sought by PEO C4I, PEO MLB and PEO Digital" [G], while the PAE Mission Systems release of
2026-05-11 places those PEOs' mission-systems elements under the new PAE [E] and the former
`peoc4i.navy.mil` front page read "PAE Mission Systems - Front Page / Site Under Construction"
by 2026-05-19 [D]. The 2026-09-01 NAVWAR navigation lists NAVWAR, NIWC Atlantic, NIWC Pacific,
NSFA and DRPM Project Overmatch, and no PEOs [H]. Reading: the PEOs' web presence and
reporting line moved to the PAE; contracting execution for their programs still runs through
NAVWAR HQ. NAVWAR's own live About page (2026-09-16) now describes the command as providing
technical, in-service and support services "to its respective portfolio acquisition executive
(PAEs)" and as consisting of the two NIWCs, which is the post-reorganization role in the
command's own words. The organization seed records PEO C4I as a child of NAVWAR with an open end date and
as consolidated into PAE Mission Systems from 2026-05-11, and flags the pair for review.

### 2.2 Reorganization timeline (dated evidence only)

| Date | Event | Evidence |
| --- | --- | --- |
| 2026-03-15 | DoN establishes five PAEs: Industrial Operations, Marine Corps, Maritime, Strategic Systems Programs, Undersea, each with an interim PAE | [F] |
| 2026-05-11 | DoN launches PAE Mission Systems (interim PAE Jim Day), consolidating the organizations listed in 2.1, under the Warfighting Acquisition System | [E] |
| 2026-05-19 | `peoc4i.navy.mil` front page rebranded to PAE Mission Systems, "Site Under Construction" | [D] |
| 2026-09-01 | NAVWAR navigation no longer lists PEOs; the acquisition-pathways page still describes them as NAVWAR components | [G][H] |
| 2026-09-16 | Live captures through a US browser: every path on `peoc4i.navy.mil` returns the PAE Mission Systems home page (new domain `missionsystems.navy.mil`, organized as "capability portfolios"); PEO Digital's site carries the notice "PEO Digital is now part of the PAE Mission Systems"; NAVWAR's Work-With-Us footer still lists PEO C4I, PEO Digital, PEO MLB | live captures in the manifest (`method: browserbase`) |

Earlier history (SPAWAR renamed NAVWAR; PEO EIS split into PEO Digital and PEO MLB) is cited to
official documents in `research/docs/02_organization_map.md`.

### 2.3 "PAE" is not a data system

In official material PAE means
Portfolio Acquisition Executive, an organization type created by the 2026 reorganization [E][F].
There is no PAE record system with public identifiers to crosswalk. The identifiers that do
connect forecast to award are the LRAE's `PID Number` (for example
`N00039-24-RFPREQ-PMW-160-0002`: contracting UIC, fiscal year, office code, sequence) and the
`Existing Contract Number` column [I]; see `research/docs/03_data_connection_map.md`.

## 3. Program offices, contracting offices, technical centers, supporting organizations

### 3.1 Who does what

| Role | Organizations | How the evidence distinguishes them |
| --- | --- | --- |
| Requirement owner / program office | PMA/PMW 101, PMW 120, 130, 150, 160, PMW/A 170, 740, 750, 760, 770, 790 under PEO C4I | Listed as PEO C4I program offices on the official page and contact form [A][B]; appear in the LRAE column "Associated Program or Requirement Office" [I] |
| Portfolio | PEO C4I (to 2026-05-10 as a NAVWAR PEO; from 2026-05-11 within PAE Mission Systems) | [A][E][G] |
| Contracting office | NAVWAR HQ contracts directorate, UIC N00039 | Official page: HQ "procures the IW systems sought by PEO C4I, PEO MLB and PEO Digital" [G]; every LRAE row whose requirement office is a PMW/PMA has contracting UIC N00039 (144 of 144 in the June 2025 release) [I]; FPDS contracting office name "NAVAL INFORMATION WARFARE SYSTEMS" [J] |
| Technical centers with their own contracting | NIWC Pacific (N66001), NIWC Atlantic (N65236) | "the technical heart of NAVWAR" [G]; LRAE rows whose requirement office is a NIWC competency code (`LSUBP…`, `NP-…`) are contracted by N65236 or N66001, never by N00039 [I] |
| Funding organizations that are not the contracting office | e.g. NAVSEA HQ (N00024) funding actions contracted by N00039 or the NIWCs | FPDS `fundingRequestingOfficeID` differs from `contractingOfficeID` on those actions [J] |
| Other requirement offices contracted through N00039 | PMS 485 (a NAVSEA program office), PEO C4I front office (`C4IEXEC`), DRPM Overmatch (`DRPM-NOA`), PEO MLB and PEO Digital codes (`Pf007NERP`, `Pf004MNHR`, `Pf005NABS`, `Pf008MLBFO`, `Digital TD`, `PEO-MLB`), NAVWAR HQ competencies (`1.0`, `4.0`, `6.0`, `8.0`, `PAS`, `PCE`, `DCE`, `JTNC`) | LRAE requirement-office values [I] |

Consequence for attribution: a contracting office of N00039 narrows the owner to "a PEO C4I,
PEO Digital, PEO MLB, PMS 485, DRPM or NAVWAR HQ requirement", which is not a program office.
An NIWC contracting office points to an NIWC division, which is a technical center, not a PEO
program office, unless the funding office or text says otherwise.

### 3.2 PEO C4I program-office inventory

Row counts are from the June 2025 LRAE release [I]; names are as written on the 2026-04-12
program-office page [A]; program managers are as printed on the dated tear sheets [M] or in
the change-of-command release [K].

| Code | Name (2026 official page) | Top programs named on tear sheet | Program manager (as of date) | LRAE rows |
| --- | --- | --- | --- | ---: |
| PMA/PMW 101 | Multifunctional Information Distribution Systems (MIDS) | MIDS-LVT, MIDS JTRS, Link 16 waveform, FMS to 58 nations and NATO | not printed | 22 |
| PMW 120 | Battlespace Awareness and Information Operations ("creates and breaks kill webs") | no archived tear sheet | not printed | 2 |
| PMW 130 | Cybersecurity | crypto and key management, maritime cybersecurity products | not printed (2023 sheet) | 3 |
| PMW 150 | Naval Command and Control Systems | C2 modernization, Link 16/Link 22, TTNT afloat | CAPT Raphael R. Castillejo (from 2025-08-19, relieved Mr. Baron Jolie) | 18 |
| PMW 160 | Tactical Networks | CANES (ACAT IAC), ADNS | CAPT Katy Boehme (2023-05-01 sheet); CAPT Nicole Nigro (2025-01-01 sheet) | 17 |
| PMW/A 170 | Communications and GPS Navigation | NMT, STtNG SATCOM, tactical comms, assured PNT | CAPT Kris De Soto (2025 sheet) | 37 |
| PMW 740 | International C4I Integration | FMS/BPC C4I cases | not printed | 17 |
| PMW 750 | Carrier and Air Integration | force-level new construction and platform integration (CVN, L-class) | not printed | 10 |
| PMW 760 | Ship Integration | C4I integration on surface ships, MSC, USCG cutters, Aegis Ashore | CAPT Raphael Castillejo (2025-01-01 sheet); Mr. Eric Andalis (from 2025-08-19) | 5 |
| PMW 770 | Undersea Communications and Integration | Common Submarine Radio Room | not printed | 9 |
| PMW 790 | Tactical Shore and Expeditionary Integration | shore tactical C4I | Mr. Brian Miller (2025-01-01 sheet) | 4 |

Name drift to record as aliases: the 2023 NAVWAR article used in production says "Navy Command
and Control Systems (PMW 150)" and "Shore and Expeditionary Integration (PMW 790)"; the 2026
page says "Naval Command and Control Systems" and "Tactical Shore & Expeditionary Integration"
[A]. The LRAE writes codes with hyphens (`PMW-160`, `PMW/A-170`, `PMA/PMW-101`) [I]; FPDS and
USAspending descriptions write `PMW 160` [J]; PID numbers embed `PMW-160` [I].

## 4. What "comprehensive coverage" means for this pilot

1. Identity: each of the 11 PMWs has a canonical name, its code variants, a dated parent chain
   (PEO C4I, NAVWAR, PAE Mission Systems) and at least one dated official page or tear sheet.
2. Lifecycle: for each PMW, at least one inspected source per stage where a public source exists:
   organization page; budget line; LRAE row; SAM notice; FPDS/USAspending action.
3. Forecast universe: every LRAE row whose requirement office is a PEO C4I PMW (144 rows in the
   June 2025 release) is tracked to its solicitation and award or marked as not observed.
4. Award universe: every FPDS action with contracting office N00039 in the review window is
   attributed to a PMW with an evidence class, or abstained with a stated reason; NIWC actions
   are attributed only when funding office or text names a PMW.

## Appendix: evidence

| Tag | Document | Retrieval | Date on document or capture | SHA-256 (first 12) |
| --- | --- | --- | --- | --- |
| [E] | PAE Mission Systems establishment release, 2026-05-11 | direct | retrieved 2026-09-16 | `967d7526e041` |
| [F] | Navy Reshapes Warfighting Acquisition System, first PAEs, 2026-03 | direct | retrieved 2026-09-16 | `48126111a912` |
| [K] | PEO C4I PM changes PMW 150 / PMW 760, 2025-08 | direct | retrieved 2026-09-16 | `6ba21425c5c1` |
| [L] | PEO C4I WEST 2026 industry engagement, 11 program offices | direct | retrieved 2026-09-16 | `52e3de2aa1df` |
| [N] | DVIDS NAVWAR unit page | direct | retrieved 2026-09-16 | `cccf722d5499` |
| [A] | PEO C4I program office list | wayback | capture 2026-04-12 | `43c7525b85d5` |
| [C] | PEO C4I leadership | wayback | capture 2026-04-12 | `9facac44957b` |
| [D] | PEO C4I home | wayback | capture 2026-05-19 | `8a7c7610f156` |
| [I] | NAVWAR LRAE JUN 2025 xlsx | wayback | capture 2026-01-08 | `697ec8c004d2` |
| [M] | PMW 150 tear sheet 2025 | wayback | capture 2026-01-21 | `eb246589a673` |
| [M] | PMW 160 tear sheet 2023 | wayback | capture 2024-05-17 | `097c6905fd42` |
| [M] | PMW 130 tear sheet 2023 | wayback | capture 2024-12-17 | `b31c3d79b1b8` |
| [M] | PMW 750 tear sheet 2025 | wayback | capture 2026-01-21 | `f66bbb6d1bd7` |
| [M] | PMA/PMW 101 tear sheet 2025 | wayback | capture 2026-01-21 | `59c287a79369` |
| [M] | PMW 170 tear sheet 2025 | wayback | capture 2026-01-21 | `856c15fc4c46` |
| [M] | PMW 740 tear sheet 2023 | wayback | capture 2025-08-08 | `fbc29299b539` |
| [B] | PEO C4I contact page | wayback | capture 2026-04-12 | `94a0db075195` |
| [O] | ONR and NRL LRAE document | direct | retrieved 2026-09-16 | `29db4e9d73fb` |
| [H] | NAVWAR home | wayback | capture 2026-09-01 | `1c6dd971e695` |
| [G] | NAVWAR CSO opportunities / acquisition pathways (README link) | wayback | capture 2026-09-01 | `1e97effb2e65` |
| [M] | PMW 760 tear sheet 2025 | wayback | capture 2026-01-21 | `c0a13af26e4c` |
| [M] | PMW 770 tear sheet 2025 | wayback | capture 2026-01-21 | `0a1b2c3042e0` |
| [M] | PMW 790 tear sheet 2025 | wayback | capture 2025-11-13 | `606a844eca01` |
| [M] | PMW 740 tear sheet 2025 | wayback | capture 2026-01-21 | `5895f2f9ec87` |
| [M] | PMW 160 tear sheet 2025 | wayback | capture 2026-01-21 | `480faf1f0f8a` |
| [J] | FPDS ATOM: contracting office N00039 (NAVWAR HQ) actions Jun-Sep 2026, page 1 | direct | retrieved 2026-09-16 | `742559fc0a5e` |
| [J] | FPDS ATOM: contracting office N66001 (NIWC Pacific) actions Mar-Sep 2026, page 1 | direct | retrieved 2026-09-16 | `19ae269fecf1` |
| [J] | FPDS ATOM: contracting office N65236 (NIWC Atlantic) actions Mar-Sep 2026, page 1 | direct | retrieved 2026-09-16 | `9fbf76db76a9` |
| [J] | FPDS ATOM: BAE LTS/CLTS N0003919C0002, contracted by N00039, funded by NAVSEA HQ | direct | retrieved 2026-09-16 | `7b19068d8642` |

Full rows and the original URLs are in `research/sources/documents_manifest.jsonl`.

# 02 - Organization and program-office map

The machine-readable graph is `research/memory/organization_seed.json` (nodes, edges, validity dates, one or
more dated official citations each). This document explains the shape of the map, the
reorganization timeline it encodes, the alias problem, and how the map is maintained when the
Navy moves offices around.

## In plain terms

The pilot portfolio sits inside three layers that have each been renamed or reorganized in the
last seven years: the systems command (SPAWAR became NAVWAR in 2019), the portfolio layer
(PEO EIS split into PEO Digital and PEO MLB in 2020; PEO C4I moved under a new Portfolio
Acquisition Executive in 2026), and the program offices themselves (names drift between official
pages). The map therefore stores every organization with the dates a name or parent was valid,
and every claim with the document that says so. Nothing is overwritten when the Navy reorganizes;
a new edge with a start date is added and the old one gets an end date.

## 1. The map at a glance

```
Department of the Navy
 |- NAVWAR (Naval Information Warfare Systems Command; SPAWAR until 2019-06-02)
 |    |- NAVWAR HQ contracts directorate  N00039          contracting office for every PEO office and for
 |    |                                                    PMS 485, DRPM Overmatch and HQ competencies
 |    |- NIWC Pacific  N66001  (SSC Pacific until 2019-02-17)   technical center, own contracting
 |    |- NIWC Atlantic N65236  (SSC Atlantic until 2019-02-17)  technical center, own contracting
 |    |- NSFA; DRPM Project Overmatch (in the 1NAVWAR navigation, 2026-09-01)
 |    |- PEO C4I  (2003-; "PEO C4I & Space" in footers)  -> 11 program offices (below)
 |    |- PEO Digital and Enterprise Services (2020-05-)   -> NEN and other network/enterprise offices
 |    |- PEO Manpower, Logistics and Business Solutions (2020-05-) -> PMW 220, PMW 250, ...
 |    |- [PEO EIS, 2006 - 2020-05, disestablished]
 |
 |- PAE Mission Systems (2026-05-11-; interim PAE Jim Day)
      consolidates the "mission systems elements" of PEO C4I, PEO Digital, PEO IWS, PEO MLB,
      DRPM Overmatch, DRPM LRNFO, DRPM MILE, Minotaur (PMA 290), NAVWAR, NAVSEA, NAVAIR, MCSC
```

PEO C4I program offices (official page 2026-04-12; contact form 2026-04-12; anniversary article
2023-05-23): PMA/PMW 101 MIDS; PMW 120 Battlespace Awareness and Information Operations; PMW 130
Cybersecurity; PMW 150 Naval Command and Control Systems; PMW 160 Tactical Networks; PMW/A 170
Communications and GPS Navigation; PMW 740 International C4I Integration; PMW 750 Carrier and Air
Integration; PMW 760 Ship Integration; PMW 770 Undersea Communications and Integration; PMW 790
Tactical Shore and Expeditionary Integration.

Program-to-office mappings taken from the official tear sheets (used only as `inferred`
evidence for awards that name a program but not an office): CANES and ADNS -> PMW 160; MIDS-LVT,
MIDS JTRS, Link 16 terminals -> PMA/PMW 101; NMT, CBSP, NTCDL, CDLS and SATCOM -> PMW/A 170;
Common Submarine Radio Room -> PMW 770; GCCS-M, MTC2, Link 16/22 network management -> PMW 150;
SHARKCAGE -> PMW 130; force-level platform integration -> PMW 750/760.

## 2. Reorganization timeline

| Effective | Change | Source (observation date) |
| --- | --- | --- |
| 2006 (spring) | PEO EIS established | PEO Digital legacy article (2021-04-01, Wayback 2026-05-19) |
| 2019-02-18 | SPAWAR Systems Centers Pacific and Atlantic renamed Naval Information Warfare Centers | DVIDS release (2019-02-13) |
| 2019-06-03 | SPAWAR renamed NAVWAR, "effective immediately" | navy.mil release (2019-06-03, Wayback 2026-01-23) |
| 2020-05-13 | DASN(IW&ET) directs the disestablishment of PEO EIS and the realignment of its programs into PEO Digital and PEO MLB; NEN awarded the SMIT contract the same year | DON CIO CHIPS article (April-June 2020, retrieved live 2026-09-16); PEO Digital legacy article |
| 2023-05-23 | PEO C4I marks 20 years; 11 program offices listed; PEO Rear Adm. Kurt Rothenhaus | NAVWAR article (Wayback 2025-12-31) |
| 2023-05 | Dr. William Luebke acting PEO C4I | PEO C4I leadership page (Wayback 2026-04-12) |
| by 2025-05-13 | PMS 485 (Maritime Surveillance Systems) appears under "PEO Undersea Warfare Systems" in a NAVWAR presolicitation; the 2019 J&A had placed it under PEO Submarines. Two relationships in the seed graph with effective dates unknown; the realignment date itself is not published | SAM.gov notices N0003925R1006 (2025-05-13) and N00039-19-R-0002 (2019-07-03) |
| 2025-08-19 | PMW 150 and PMW 760 change program managers | DVIDS release |
| 2026-03-15 | Five PAEs established (Industrial Operations, Marine Corps, Maritime, Strategic Systems Programs, Undersea) | paemaritime.navy.mil release |
| 2026-05-11 | PAE Mission Systems established; PEO C4I among the consolidated organizations | DVIDS release |
| 2026-05-19 | `peoc4i.navy.mil` front page reads "PAE Mission Systems - Front Page / Site Under Construction" | Wayback capture |
| 2026-09-01 | NAVWAR navigation lists NAVWAR, NIWC Atlantic, NIWC Pacific, NSFA, DRPM Project Overmatch and no PEOs; the acquisition-pathways page still describes the PEOs as NAVWAR components | Wayback captures |
| 2026-09-16 | Live captures through a US browser: `peoc4i.navy.mil` serves the PAE Mission Systems site (`missionsystems.navy.mil`), whose Industry page lists five capability portfolios (Digital Infrastructure & Services; Warfighter Enterprise Business Services; Naval Intelligence, C2, Cyber Warfare & Information Operations; Sensors, PNT, EW & Communications Systems; Combat Systems & Fires); PEO Digital's and PEO MLB's sites carry the notice "now part of the PAE Mission Systems"; NAVWAR's About page says NAVWAR provides technical, in-service and support services "to its respective portfolio acquisition executive (PAEs)" and "consists of bicoastal reporting" NIWCs; navy.mil: PAEs "will have direct authority not only for program offices, but also over associated technical, contracting and sustainment functions" | Browserbase captures |

## 3. Aliases and identifier inconsistencies

| Where | How the same office is written | Handling |
| --- | --- | --- |
| Official pages | `PMW 160`, `PMW/A 170`, `PMA/PMW 101` | canonical form |
| LRAE requirement-office column and PID numbers | `PMW-160`, `PMW/A-170`, `PMA/PMW-101`, plus `C4IEXEC` / `PEO-C4I` for the front office | alias table in the seed graph |
| FPDS and USAspending descriptions | `PMW 160`, `PMW/A 170`, `PEOC4I`, `PEO C4I`, `PROGRAM MANAGER, WARFARE TACTICAL NETWORKS (PMW 160)` | regex over the alias table; PEO-level mentions resolve to the portfolio, not an office |
| Office names over time | PMW 150 "Navy" vs "Naval" Command and Control Systems; PMW 790 with and without "Tactical" | both names kept with dates |
| SAM.gov notice text | "Navy Command and Control Program Office (PMW 150)", "International Integration Program Office (PMW 740)", "MIDS International Program Office (IPO)", "Naval Enterprise Networks Program Office (PMW 205)" | first-sentence office names; IPO/MPO map to PMA/PMW 101, NEN to PMW 205 |
| PEO Digital and PEO MLB in the LRAE | `Pf007NERP`, `Pf004MNHR`, `Pf005NABS`, `Pf008MLBFO`, `Digital TD`, `PEO-MLB`, `PCE` instead of PMW codes | recorded as HQ/PEO codes |
| NIWC requirement offices | `LSUBP000xx - <division> - NIWCLANT`, `NP-xxxxx - <competency> - NIWCPAC` | technical-center divisions, never program offices |
| Contracting offices | `N00039 - NAVWAR` (LRAE), `NAVAL INFORMATION WARFARE SYSTEMS` (FPDS), `N00039` (PIID prefix) | one node with all three |
| Command name in old records | award descriptions written after 2019 say "NAVWAR" even for 2016 awards | descriptions are re-authored at modification time; never date an organization from an award description |

### 3a. Where the PMWs sit in the new portfolio structure (candidate mapping)

The PAE Mission Systems site names five capability portfolios without listing which former
offices belong to each. Reading the portfolio names against the PEO C4I tear sheets suggests:
PMW 120, PMW 130 and PMW 150 under "Naval Intelligence, C2, Cyber Warfare & Information
Operations"; PMA/PMW 101, PMW/A 170 and PMW 770 under "Sensors, PNT, EW & Communications
Systems"; PEO Digital's offices under "Digital Infrastructure & Services"; PEO MLB's under
"Warfighter Enterprise Business Services"; PEO IWS under "Combat Systems & Fires"; PMW 740, 750,
760 and 790 (integration and international offices) unplaced. This is an inference; the seed
graph keeps the PEO C4I parentage with the dated consolidation edge.

## 4. NAVSEA, ONR and NRL organizations

NAVSEA, ONR and NRL organizations are in the memory as their sources print them
(`org_memory_lrae.py`): the LRAE office and contracting columns, the NAVSEA HQ deputy program
manager list of July 2025 (PMS offices under PEO CARRIERS, PEO SHIPS, SEA 21, PEO IWS, PEO USC
and the directorates, 50 parent edges) and twelve statements read off the official NSWC Corona
and NSWC Indian Head department pages (`research/memory/org_page_statements.json`, passages checked
against the saved pages). The 168 generated nodes are all `draft` and carry the sheet's names
(`NSWCPD`, `Newport`) rather than full names. Department placements that the generator infers
from the row's contracting office say so (`evidence_class: inferred`). Rows that no source
places (`TBD`, cells naming several organizations, unplaced free text, organizations outside the
Department of the Navy, fleet units) stay unresolved. Shipyard pages name no codes publicly; the
Corona and Indian Head pages spell several departments differently from the sheet, so the
statements file records each reading as the agent's judgment.

## 5. Maintaining the map when the Navy changes

1. A weekly hash diff on the official organization pages and tear-sheet URLs opens a review item
   when content changes.
2. Reorganization releases (DVIDS, PAE domains, navy.mil) are read for organization names; a
   new edge gets `valid_from` set to the release's effective date, the superseded edge gets
   `valid_to` the day before. Nothing is deleted.
3. Conflicting official statements (as with NAVWAR's pages in September 2026) are both kept and
   flagged `conflict_flagged` until a later document settles them.
4. New codes seen in the LRAE requirement-office column or in award text are added to the alias
   table as `unverified` until an official page confirms the office and parent.
5. People edges carry the tear-sheet or release date as the observation date and end when a
   change-of-command release names a successor.

Mapping to the platform: nodes are `gov_organizations` rows (with `org_type`, aliases,
`valid_from/valid_to`, provenance columns); edges are `gov_organization_relationships` rows; the
2026 names map to aliases on the existing PEO C4I rows created from the 2023 article, and the
consolidation maps to a `consolidated_into` relationship to a PAE Mission Systems organization
dated 2026-05-11.

# 19 - Navy versus DARPA: the same layer read against a second agency

`research/docs/14_reusing_this_for_another_agency.md` says what travels to the next agency and what is written
once for it, and lists DARPA with a one-line expectation. This document is the first reading of that procedure
against an agency. Nothing of a DARPA layer was built: no organization seed, no sweep, no model call, nothing
loaded. Instead, for every family of statement the Navy layer collects, DARPA's public record for the same
family was fetched on 2026-09-24 with `research/tools/fetch.py` (37 ledger rows, 33 of them answered; the
appendix lists every one), and the Navy layer's own readers were run offline on what came back. Where the
comparison showed the reuse write-up to be wrong or stale, that write-up was corrected; section 6 lists what
changed and what was left alone.

Every number below is from a saved document with a hash and a retrieval time, or from a Navy artefact in this
repository as it stands on this date. Where a number came from a page of ten records, it is given as the range
the page count implies. Nothing is asserted that the record does not state.

## In plain terms

The Navy layer rests on a forecast: the Long-Range Acquisition Estimate names the requirement office, the
contracting office and the incumbent on one row, and everything else is read against it. DARPA publishes no
such thing. Its Contracts Management Office states that it enters into "contracts, grants, cooperative
agreements, and other transactions", and that this includes Broad Agency Announcements, SBIR and STTR topics,
Research Announcements and Requests for Proposals; its industry page adds Requests for Information and sends
newcomers to a Proposers Day. Its contracting office, HR0011, signed as many base awards in fiscal year 2026 as
NAVWAR headquarters did, and the first page of them is entirely Other Transactions. Its people are
published with role, office and start date. Its budget is one research and development justification book.

The government-wide records hold DARPA in the same shape as the Navy: the same award feed, the same notice
search, the same SBIR portal pages (which already carry 197 DARPA topics), the same Federal Register API,
oversight listing and committee reports. The Navy readers read them unchanged, and the award reader read the
Other Transactions without a change. What does not travel is a longer list than the reuse write-up gave: the
office code tables and the agency filters inside a dozen tools. Those are named in section 4 and now in the
write-up.

## 1. What was compared and how

| Step | What was done | Where the evidence is |
| --- | --- | --- |
| DARPA's own pages | 18 addresses on `darpa.mil` fetched directly; 17 answered, one path is gone (404) | appendix, rows tagged `darpa.mil` |
| Government-wide records | FPDS ATOM feed for DARPA's contracting office and for awards DARPA funds, in FY2025 and FY2026, beside the same queries for the Navy; SAM.gov search by organization id for DARPA and for NAVWAR headquarters; Federal Register API; oversight.gov listing; the comptroller's budget indexes | appendix |
| Navy readers on DARPA data | `fpds_sweep.fpds_entries` on the two DARPA FPDS pages; `congress.directives` with a DARPA pattern over the three committee reports whose text is saved; a count of DARPA topics in the saved SBIR portal pages; `oversight.listing_rows` on the DARPA listing | this document, sections 2 and 3 |
| Access findings | `darpa.mil`, `fpds.gov`, `sam.gov`, `federalregister.gov`, `oversight.gov` and `comptroller.war.gov` answer this address directly; `comptroller.defense.gov` and `gao.gov` refuse it (403), as they do for the Navy | appendix, status column |

Not done: no DARPA notice detail or award history was harvested, no SBIR detail read, no budget book downloaded,
no docket page taken through the hosted browser. Those are the collection of a DARPA layer, and this is a
comparison.

## 2. Family by family

The Navy column states what the layer holds today. The DARPA column states what the same family's public record
showed on 2026-09-24. The last column says whether the Navy tool read it without a change.

| Family | The Navy layer | DARPA's record on 2026-09-24 | Reads with the Navy tool? |
| --- | --- | --- | --- |
| Forecast | LRAE spreadsheets for NAVWAR, NAVSEA, ONR and NRL, one requirement per row with the requirement office named; the datapack compares releases | No forecast found. None of the seventeen `darpa.mil` pages links or names a procurement forecast; the word appears once, in an SBIR topic description in the opportunities feed. The Contracts Management Office page lists the instruments instead: BAAs, SBIR and STTR topics, Research Announcements, RFPs | Not applicable: notices carry the requirement, as the write-up expected |
| Notice | SAM.gov searched by organization id; NAVWAR headquarters (100076586): 2,414 archived and 33 active notices | SAM.gov organization 500035490 ("DEF ADVANCED RESEARCH PROJECTS AGCY", HR0011): 5,192 archived and 46 active; the first archived page holds 14 special notices, 6 solicitations, 2 justifications, 1 award notice. DARPA also publishes its own opportunities feed: 10 items, titles typed "RFI:", "SBIR:", "STTR:", "Proposers Day:", every item linking the generic opportunities page rather than a notice | Yes: same endpoint, same `organization_id` parameter; one entry in the sweep's organization table |
| Incumbent and awards | FPDS by contracting office. NAVWAR headquarters N00039, FY2026 to date: 291 to 300 base awards | HR0011, FY2026 to date: 291 to 300 base awards; FY2025: 441 to 450. Every entry on the first FY2026 page is an "OTHER TRANSACTION AGREEMENT" or "OTHER TRANSACTION IDV". Awards DARPA funds (agency 97AE), whatever office signs: 331 to 340 in FY2026 and 521 to 530 in FY2025; 41 to 50 of the FY2026 ones were signed by other agencies' offices (the first page names the Department of Commerce NIST and the Interior Business Center) | Yes: the parser read 10 of 10 entries on each page, Other Transactions included, with PIID, signed and completion dates, vendor, description, and funding office HR0011 |
| Programs (SBIR/STTR) | DoD SBIR/STTR portal pages, every component newest first, Navy rows kept: 1,118 Navy topics in the saved pages, 1,100 rows built | The same saved pages hold 197 DARPA topics. The Small Business Programs Office page states: "Pending SBIR/STTR program reauthorization, closing dates for active topics are To Be Determined (TBD)." | The sweep already collects them; the build keeps `component == "NAVY"` |
| Budget | Department of the Navy justification books; the reader parses P-40 exhibits and holds 48 line items from one book (Other Procurement, Navy, BA 2) | Research and development only. The about page: "The President's FY2027 budget request for DARPA is $5.039 billion. The FY2026 enacted budget was $4.322 billion." The comptroller's FY2026 index (Wayback capture of 2025-09-09; the live host answers 403) and FY2027 index (`comptroller.war.gov`, direct) each list one DARPA book, `RDTE_Vol1_DARPA_MasterJustificationBook`, under the RDT&E volumes | No: the reader matches "PB yyyy Navy" and P-40 exhibits; an R-2 reader is written once for any RDT&E-only agency |
| Oversight | oversight.gov full-text queries "Navy" and "Naval" plus the GAO feed; 27 documents read | oversight.gov full text for "Defense Advanced Research Projects Agency": 50 rows on the first page, one of whose titles names DARPA (an evaluation of the Under Secretary for Research and Engineering) | Same listing and parser; the query terms and the reviewed-agency pattern are per agency; a thin family |
| Leaders' words | navy.mil speech and testimony archives and the House committee feeds; 37 documents | The budgets-and-testimony page lists eight statement files, the newest issued 2021-04-21, as PDFs on `darpa.mil`, `docs.house.gov` and the Senate committees; the House feeds of 2026-09-24 and 2026-09-28 named no DARPA hearing, and the Senate Armed Services hearing index answers 403 to a bare client (2026-09-28), so the record's official leader statements end in 2021 and the recent leader remarks it holds are conference pages Exa discovered | The House feeds travel; the archive address is per agency; DARPA's page is a static list, not a feed |
| Congress | govinfo committee reports; directives found by a Navy name pattern: 104 in the three reports whose text is saved | The same three texts, read with a DARPA pattern: 1 directive names DARPA | Same reports, same finder; the pattern is per agency |
| Federal Register | API filtered to the agency slug `navy-department`; 268 rows since 2021-10-01 | No agency slug: the agencies endpoint answers 404 for DARPA. A term search for the full name since 2024-10-01 returns 9 documents, filed under other agencies (the Defense Department, the Treasury, the Executive Office of the President and the Office of Management and Budget among them) | The API travels; there is no DARPA filter, so a term search stands in and the family is near-empty |
| Protest | GAO's docket filtered to the Department of the Navy, taken through the hosted browser; 2 rows | `gao.gov` answers 403 to this address; the docket's DARPA filter was not tried through the browser | Open |
| Organization | 211 nodes with dated observations and parent claims | The offices page: "six technical offices put DARPA's mission into action", named Biological Technologies, Defense Sciences, Information Processing Techniques, Multi X, Strategic Technology and Tactical Technology, plus the Director's Office and support offices (Contracts Management, Small Business Programs, Commercial Strategy). The address `/about/offices/i2o` answers with the Information Processing Techniques Office page, while NAVWAR's June 2024 forecast names "DARPA Information Innovation Office (I2O)"; two dated observations, as the memory format stores them | The seed is written per agency; the dated-name model applies here too |
| People | 415 people with dated positions from tear sheets, notices, speeches and articles | `darpa.mil/json/staff`: 423 records with role, office, start date and research topics: 285 "Program Manager", 12 "Deputy Program Manager", 24 "Office Director", 25 "Deputy Director"; 293 of the 297 program-manager and deputy program-manager records carry a start date; the six technical offices hold 55 to 70 records each | The dated-position model travels; the reader is one adapter. A role the listing states is a stated role, not authority |
| News | five feeds (NAVWAR, navy.mil, DVIDS, the department's contracts page, PAE Mission Systems); 41 articles modelled | `darpa.mil/rss.xml`: 10 items, newest 2026-09-14; a news listing page | One entry in the feed list |
| Conference | Exa discovery of pages naming Navy officials | The industry page: "Come to Proposers Day"; events are listed on `darpa.mil` (not fetched) | Same discovery, different terms |

## 3. What travelled, as tested

- **The ledger and the fetch tool.** Seventeen `darpa.mil` pages, eight FPDS pages, four SAM.gov searches, two
  Federal Register calls, one oversight listing and two comptroller indexes are rows with bytes and hashes.
  `darpa.mil` answers this address directly, which no Navy host does.
- **The award reader.** `fpds_sweep.fpds_entries` read every entry on the DARPA pages, including the Other
  Transaction agreements and vehicles that make up HR0011's first page, with the same fields the Navy pages
  give. An Other Transaction carries no solicitation number and no referenced vehicle on the page, so the
  matcher's identifier stage would have less to work with; that is a property of the record, not of the reader.
- **The notice search.** The SAM.gov organization search answers for DARPA exactly as for NAVWAR: same
  endpoint, same parameter, same page shape and totals.
- **The SBIR portal pages.** The saved pages already hold 197 DARPA topics beside 1,118 Navy ones; nothing
  needs fetching to build a DARPA programs family from them.
- **The Federal Register API, the oversight listing parser and the committee-report directive finder** ran
  unchanged. Their results are thin for DARPA, which is a fact about DARPA's footprint in those records.

## 4. What is written once for DARPA

The reuse write-up listed the organization seed, a forecast reader, the alias table, the feed list, the egress
decision and the budget table. The comparison found these as well, each a constant inside a tool as the code
stands on 2026-09-24:

| Tool | Constant | What DARPA needs |
| --- | --- | --- |
| `fpds_sweep.py` | `OFFICES`, the contracting offices swept | HR0011; and a funding-agency query (97AE), because 41 to 50 of DARPA's FY2026 base awards were signed by other agencies' offices and a sweep keyed on the contracting office alone misses them |
| `sam_notices.py` | `SWEEP_ORGS`, `SWEEP_CODES`, `OFFICE_RE` | organization 500035490; an office pattern for DARPA's technical offices |
| `sbir.py` | `component == "NAVY"`, `DEPARTMENT`, `COMMANDS` | `DARPA` as the component; the office names as the topics print them |
| `protests.py` | `LISTING` (the docket's agency filter), `AGENCY` | the docket's name for DARPA, to be confirmed through the browser |
| `congress.py` | `NAVY_RE` | a DARPA pattern |
| `fedreg.py` | the agency slug `navy-department` | none exists; a term search or no family |
| `oversight.py`, `remarks.py` | `AGENCY`, `QUERIES`, `REVIEWED_RE`, `NAMES_RE`, the speech archive addresses | DARPA's names; the testimony list on `darpa.mil` instead of an archive |
| `budget.py` | P-40 exhibits, `PB_RE` "PB yyyy Navy" | an R-2 reader for the RDT&E book |
| `people.py` | `DEPARTMENT` `agency:don`, `EXECUTIVE` titles | the staff listing as a source, with role and start date as stated |
| `news.py` | `FEEDS`, `OFFICIAL_NAMES`, `STANDING_TERMS` | the DARPA feed and host |
| `agency_layers_sql.py` | `AGENCY_NAVY` (097:1700), `LRAE_PROVIDERS` | agency 097:97AE; no forecast providers |
| `trace.py`, `backtest.py` | `PACKS` (`lrae_navwar_*`), `SAM_ORG_NODES`, `PILOT_OFFICES` | no packs; the SAM organization to node map; the offices whose cells are judged |
| `pipeline.py` | the stage descriptions name the Navy | wording only |

## 5. Where the two agencies meet

The Navy record already touches DARPA, and the touch points show what a DARPA layer would share with it.

- **NAVWAR's forecast names DARPA as a customer.** The June 2023 release carries three rows, June 2024 four,
  June 2025 three, under the title prefix "DARPA IPT": SeaPort task orders for systems engineering support to
  DARPA's Tactical Technology Office, Information Innovation Office and Adaptive Capabilities Office, and
  "DARPA CHUGACH SAC under N65236-23-D-8021". The NAVSEA December 2025 release has one row whose office code
  string reads "SEA05, DARPA, MDA, ONR". The memory reader lists DARPA among the non-Navy names and makes no
  node for it.
- **NAVSEA's awards carry DARPA programs.** The frozen corpus holds four incumbent contracts signed by N00024
  whose descriptions name DARPA (the OUIJA program, PUMP program independent verification and validation
  support).
- **A Navy notice sweep harvested DARPA attachments.** The ledger holds two SAM.gov attachments titled "Next
  Generation Surveillance Array: DARPA-PS-24-18", taken during the Navy sweep.
- **Funding office and contracting office differ, in both directions.** In FY2026 to date, 41 to 50 of the 331
  to 340 base awards DARPA funds were signed by other agencies; 7,531 to 7,540 of the 42,701 to 42,710 the
  Department of the Navy funds were signed by other agencies. The attribution process in `research/docs/04`
  reads the funding office as the payer and never as the owner; the same rule places these.

## 6. Issues found, and what changed

1. **The reuse write-up described a pipeline of eight stages, six of which "hold no knowledge of the Navy".**
   The pipeline has thirty-six stages, and the agency is fixed inside the collectors for topics, dockets,
   reports and the register, inside the readers for oversight, remarks, budget and people, inside the layer
   loader and inside the tracer, as section 4 lists. The stage table in `research/docs/14` now names the
   current stages and the constant each one carries. (Changed.)
2. **The "written once per agency" table omitted the office code tables and the agency filters.** They are
   now listed there, with what DARPA needs in each. (Changed.)
3. **The budget row said "the P-1 and R-1 line items".** The reader parses P-40 exhibits of Navy procurement
   books and holds lines from one such book; an RDT&E-only agency needs an R-2 reader that does not exist.
   The row now says so. (Changed.)
4. **The DARPA row said "the entry point is the broad agency announcement and the notices under it; the news
   layer carries more weight".** The first half is what the Contracts Management Office states. The second
   half is not supported: DARPA's news feed carries ten items and its opportunities feed ten, while its
   notice record holds over five thousand and its awards match NAVWAR headquarters in number. The row now
   states what was found and points here. (Changed.)
5. **Coverage bookkeeping.** The fetches put `www.darpa.mil` into the ledger; it is not a source of the Navy
   layer, so it is listed in the coverage test's known-unregistered set and the source status is rebuilt. The
   comptroller's `.war.gov` host already belongs to the registered comptroller source. (Changed, recorded in
   `DECISIONS.md`.)

Left alone, on purpose: the tools. Turning the constants in section 4 into parameters is the first step of
building the DARPA layer, not a fix to the Navy one, and the goal of this pass was the comparison. Nothing in
the Navy artefacts (seed, events, results, datapack) changed. One side effect is recorded rather than hidden:
the tracer scopes every negative reading to the dates of all saved SAM.gov searches, and the four searches
made here move the latest such date from 2026-09-23 to 2026-09-24. The NAVWAR search pages taken beside the
DARPA ones were saved under paths of their own so the sweep's saved pages are untouched.

## 7. Building the DARPA layer, in the order the records depend on each other

1. Register the sources found here in `research/sources/source_registry.json`: the DARPA opportunities and
   news feeds, the offices and staff pages, the Contracts Management and Small Business Programs Office
   pages, the SAM.gov organization, the FPDS office and funding agency, the RDT&E justification book, the
   testimony list. Record the access findings (everything direct except the comptroller's `.defense.gov` host
   and GAO).
2. Write the organization seed from the offices page and the staff listing, each office and each stated role
   with its date; carry the I2O to IPTO observation as two dated names.
3. Sweep notices for organization 500035490 and awards for HR0011 and for funding agency 97AE. Read the
   Other Transactions as awards; expect fewer solicitation numbers and vehicles on them.
4. Build the programs family from the saved SBIR pages with the component set to DARPA.
5. Write the R-2 reader and take the two justification books through the hosted browser or the `.war.gov`
   host.
6. Add the feed, then run news, people and the load. Expect the Federal Register, oversight and Congress
   families to stay thin, and say so in the coverage grid rather than search harder.

## 8. The DARPA layer as built (2026-09-24)

The layer was built the same day, in the order section 7 gives, with the pipeline the Navy uses. What
changed in the tools is one thing: the constants section 4 lists became a profile. `research/tools/agency.py`
holds the Navy and the DARPA profiles (office codes, SAM.gov organizations, the SBIR component, the Federal
Register conditions, the Congress pattern, the oversight and remarks queries and name patterns, the feeds, the
budget book folder, the staff listing, the pilot and coverage offices) and the artefact roots. The
environment variable `AGENCY` picks the profile; the Navy keeps `research/{memory,events,results,sources}` and
`build/`, another agency gets `research/agencies/<key>/` and `build/<key>/`. `pipeline.py --agency darpa` runs
the same thirty-six stages, skipping the three the profile has no source for (datapack, revisions,
fiscal_year), and runs the selfchecks under the Navy profile because their fixtures are Navy records.

Two things the shared ledger needed. A collector running under another profile marks its note with the profile
key (`oversight watch [darpa]: ...`), and each reader keeps the notes marked for it, the Navy owning the unmarked
ones; without this the DoW OIG reports taken for DARPA read as Navy reports and the Navy's GAO pages as DARPA's.
And the coverage status of one layer does not list a host the other layer's registry owns as unregistered.

What stands under `research/agencies/darpa/` and `build/darpa/`:

| artefact | what it holds | source |
| --- | --- | --- |
| `memory/organization_seed.json` | 31 nodes: the agency, the Director's Office, six technical offices with their office codes, four offices the saved notices still name but the offices page no longer lists (MTO, I2O, ACO, APO), the Contracts Management Office, eight contracting offices of other agencies that sign DARPA-funded awards (fifty or more each on the saved pages), SBPO, CSO, eight persons; 38 observations, 31 relationships, 1 interpretation (I2O's address answers with IPTO) | `org_memory_darpa.py` from the office pages, the staff listing, the notices, the FPDS pages and one SAM.gov search page; every passage a verbatim slice of the saved bytes |
| `memory/people.json` | 685 people from 1,296 observations: 1,162 SAM.gov points of contact, 127 from the staff listing with the listed start date, 4 from darpa.mil pages and 3 from conference pages (a role mailbox, such as a BAA coordinator address, is its own contact rather than merged with another under the same title) | `people.py` |
| `memory/vendors.json` | 1,172 vendors resolved by UEI from the 3,536 base awards of both FPDS sweeps | `vendors.py` |
| `events/budget_lines.json` | 48 program elements: 24 from each of the FY2026 and FY2027 RDT&E Master Justification Books, each with its budget activity, its Total Program Element row and its prose | the R-2 reader added to `budget.py` (section 6 said it did not exist) |
| `events/sbir_topics.json` | 170 DARPA-component topics open FY2020 on, from the shared portal pages; 5 name an office | `sbir.py` |
| `events/fedreg_events.json` | 2 documents whose title, action or abstract names DARPA (33 more mention it only in their text and are left), 1 typed | `fedreg.py`, term search (no slug) |
| `events/congress_events.json` | 1 directive naming DARPA in the three committee reports saved | `congress.py` |
| `events/news_observations.json` | 5 observations from 2 darpa.mil articles | `news.py` (the feeds; the Exa sweep needs `EXA_API_KEY` exported) |
| `events/oversight_events.json`, `events/remarks_events.json` | 4 reports (3 GAO products and the OIG report whose title names DARPA; 9 saved reports mention it in passing and are left), 1 finding kept; 8 hearing statements and 8 conference pages read into 72 dated events | `oversight.py`, `remarks.py` |
| `events/assistance_awards.json` | empty until the collect job takes the USAspending grant pages | `assistance.py` |
| `events/protest_events.json` | empty: the docket answers the context.dev reader, and the dockets stage has not been run for DARPA | `protests.py` |
| `sources/source_registry.json`, `coverage_matrix.json`, `source_status.json` | 19 sources (two DARPA's own: the site and the staff listing; the SAM.gov yearly extracts added; the Senate committee sites and the conference discovery verified on 2026-09-25; the news discovery not yet inspected for DARPA); 7 offices by 13 families, 64 of 91 cells covered, the rest no_public_source (forecast; CMO's budget and programs), not_started (conference; programs for the offices no topic names) or blocked (protest) | by hand; `coverage.py` validates |
| `build/darpa/layers.sql` | 1,117 needs from 1,165 notices, 2,655 assertions with their evidence, 685 contacts, 23 organizations, 5,656 brain items (3,513 of them incumbent contracts ending) | `agency_layers_sql.py` |

The collection behind it, all in the ledger: 1,165 notice details of the 5,231 under organization 500035490
(`data/raw/sam_notices_darpa/`), 411 FPDS pages (HR0011 base awards FY2023 to FY2026 and, by funding agency
97AE, FY2020 to FY2026: 3,536 base awards), 34 darpa.mil pages, the FY2026 and FY2027 books, one Federal
Register page, the oversight.gov listing and its report files, the House feeds and one witness statement, the
GAO feed.

A second reading of the built layer against the Navy's (a review agent, 2026-09-25) found and this pass fixed:
the vendor and Buying DNA readers took only the contracting-office pages, dropping the 1,074 awards other
offices sign for DARPA; the coverage status counted each layer's watch rows for the other; the `checks` stage
ran the Navy suite under the DARPA profile; the emitted rows cited the Navy layer's files as their provenance;
the reader's source authority came from the Navy registry; every statement was labelled a House document; the
term-search families (Federal Register, oversight) admitted other agencies' documents; the former offices and
the signing offices were absent from the seed; four coverage cells claimed sources no event bears out; only one
budget book was read. A second reading (2026-09-25) verified those fixes and found what this pass then fixed:
the R-2 reader labelled every book's columns with the FY2027 layout, so the FY2026 book's amounts stood a year
late (the columns are now the book's own President's Budget year, and the test reads them by book); the SBIR
topics that name BTO or DSO were attributed to the agency (the profile now maps the portal's command field to
the offices); only three collectors marked their notes, so each layer's status counted the other's FPDS, SAM,
Federal Register and budget rows (every collector marks now, the 1,985 DARPA rows of this session were
re-noted, and a layer counts a shared host's unmarked rows only for the sources its profile names as shared:
the portal pages and the committee reports); the statements linked from DARPA's testimony page had no date or
title (the link text and the file name give them); an intent to award left its requirement at identified while
a justification moved it to in procurement (both do now); and the staff listing's page URL was a literal in
`people.py`. Left open from that reading: no source for the grants and cooperative agreements the
Contracts Management Office also awards (an assistance sweep of USAspending is the candidate); no office-name
pattern for DARPA in the notice and award description readers (the Navy's PMW/PMS pattern is what they carry);
the SAM.gov yearly extracts, which would reach the notices before FY2022, are not registered for DARPA; and a
shared vendor-resolver defect (`vendors.names_for` matches a spelling inside a longer one) that the DARPA data
exposes and the Navy data hides.

A third pass (2026-09-25) closed three of those and the saved-page gaps. The profile now carries an office code
pattern: the Navy's PMW, PMS and PMA codes, and DARPA's office abbreviations (BTO, DSO, STO, TTO and the former
MTO, I2O, ACO, APO, with or without a number; CSO is left out because it also names a commercial solutions
opening). The notice, award and budget readers and the tracer read it, and the Navy index it builds is the one
it built before. A grants stage takes the grants and cooperative agreements from USAspending's award search,
which answers only a POST, so the fetch tool now records the request body with the page; 452 DARPA awards
since FY2020 wait for the collect job. The SAM.gov yearly extracts are registered. The small business office
page, three GAO products (found by GAO's own search through the hosted browser, where Exa found none), three
Senate statements and eight conference pages are saved and read. The hosted browser now sends the linking page
as the referrer and, on a refusal, visits the site's home page and tries once more; the remarks watch sends a
refused statement file there, where it had counted a refused file as seen. The conference query and the
agency's own domains are per profile, and a search answer's ledger row carries the profile mark, as every
collector's does.

What the two layers still differ in, and why:

- The special notices. DARPA posts proposers days, information sessions, requests for information, "Future
  Program" announcements and notices of intent to award as special notices; the tracer read only industry days,
  ceiling actions and forecasts by title. Those titles are now read (an industry day under another name; an RFI;
  a forecast; an intent to award without full competition), which loads 288 of DARPA's 404 special notices and,
  on the Navy's next load, the Navy special notices whose titles say the same (35 titles: 31 notices of intent
  to award or sole source, 3 RFIs, 1 workshop, all dropped before for want of an event type; an intent to award
  also moves its requirement to in_procurement, as a justification does).
- The load and read-back stages. They ran for DARPA on 2026-09-25 against the local database: `results/`
  holds the frozen corpus (5,531 events, 312 outcomes), the outcome labels, the office reads, the pulse and the
  Buying DNA, and `agencies/darpa/README.md` gives the numbers as built. With no forecast, the three-family bar
  reaches 19 of 312 outcomes (recall 0.045 at 180 days) at a precision of 0.774 over the 31 cells judged
  (section 9b).
- The model. The oversight and remarks extracts read their documents with `OPENAI_API_KEY` set; the answers
  are in the shared cassettes, so a rebuild replays them as the Navy's do.
- Depth. No DARPA topic detail and no FPDS award history (`changes`) has been fetched; the notice harvest
  holds the newest 1,165 of 5,231, and the grants and the yearly extracts wait for the collect job.

## 9. The Navy branch's read-back layers, applied to DARPA (2026-09-25)

The `sync/navy-intelligence` branch merged onto main on 2026-09-25 brought five things the DARPA layer did not have.
What each is, and what it became for DARPA:

| Layer | What the branch added for the Navy | For DARPA |
| --- | --- | --- |
| A requirement's awards | `trace.swept_by_solicitation`: the base awards on the FPDS office sweep pages by solicitation number; a base award under the number makes the need fulfilled; a forecast line moves on a tied notice or award dated after its release | Applied by the shared emitter as it is: 424 of DARPA's 1,117 needs are fulfilled by a base award under their number (344 before the rule), 140 planned, 143 in procurement, 407 identified, 3 cancelled |
| The frozen corpus and the back-test (`backtest.py`) | The loaded database frozen into `results/corpus.json`, every outcome labelled by the model, recall and precision computed | Frozen from `darpa_proof` (as rebuilt on 2026-09-25 with the branch's office rule): 1,994 dated statements, 312 outcomes (134 solicitations, 83 presolicitations, 95 RFIs since FY2024), 1,117 needs, 23 organizations. Every outcome is labelled (the branch recorded the readings with the key; they replay from the shared cassettes): recall 0.022, 0.022 and 0.026 at 180, 90 and 30 days, 8 outcomes reached; precision 1.0 over the 14 cells old enough to judge. With no forecast the precision line counts the needs the notices created, so the recall figure is the one that says what the three-family bar reaches without one |
| The pulse (`pulse.py`) | Every cell scored as of the corpus end, the week's changes, the actions | 539 cells (the requirements the technical offices own, from the notices), as of 2026-09-25; 99 actions (36 respond to a notice, 33 meet the office, 23 attend an event, 6 find a partner, 1 track a person) |
| The office pages and the model's office reading (`office_wiki.py`, `results/office_reads.json`) | A page per program office from the record; a notice filed at a contracting office and placed nowhere is read by the model against the pages | 740 notices filed at HR0011 or another agency's office that the record places nowhere, each read against the DARPA office pages; 201 placed with the notice's words and the page's line. 39 more notices the branch had the model read are placed by the record now that the office-code pattern is the profile's (a title's BTO or TTO). A DARPA page renders from the record (`office_wiki.py page "Defense Sciences Office"`) |
| The object pages and the twin's questions (`pages.py`, `ask.py`, `vocabulary.py`) | An office, vendor, person or cell as text ending with its sources and silent families; the questions (what changed, which offices buy a capability, a meeting brief, analogs, incumbents, a competitor's moves, teaming, notes, the evidence room) | Answer from the DARPA corpus under `AGENCY=darpa`: the Defense Sciences Office page (118 statements, 130 requirements, 3 sources speaking, 3 families silent), `changed` (16 statements in the year), `prep` (the brief with the pulse's 6 open actions and the office's people). `incumbents`, `team` and `moves` answer "none in the record" for a technical office: DARPA's awards name no office (open item, section 8) |
| Buying DNA (`buying_dna.py`) | Per contracting office, program office and top cell from the saved FPDS pages | 53 contracting offices' DNA (HR0011: 2,462 base awards signed 2019-10-03 to 2026-06-25, 875 vendors, top vendor 8%; then the 52 offices of other agencies that sign DARPA-funded awards, 1333ND to W912CG); 0 program office books (awards name no office); 50 cells with a position |
| The small business office (`small_business.py`) | The department's and each command's office from the Department of War directory and each office page | One row: the directory links "Defense Advanced Research Projects Agency" to darpa.mil/work-with-us/for-small-businesses (fetched directly, answers with the communities/small-business page, saved); the page carries no e-mail or telephone line other than the webmaster's, so `lines` is empty and the row names the page. Every DARPA office falls back to it in `people.routes_for` |

What had to change so the branch's tools read another agency (all in `research/tools/`): the reading patterns are profile keys
(`agency.P["reading"]`: how the agency writes an office code and a hull designator to strip, read by `pages.py` and `people.py`;
the branch's own `piid_re`, `generic_words`, `owner_types` and `short` carry the contract number, the buyer's own names, the
owner types and the short name); the label and notice-kind prompts name the profile's short name, while the office reader's
prompt still says "U.S. Navy" under every profile because the cassettes that hold DARPA's readings were recorded with those
words (an open item: naming the profile re-records 906 readings); `ask.py` keeps a vendor's notes
under the profile's build folder; `small_business.py` writes under the profile's memory folder; and a stage that cannot call
the model records unread rows instead of stopping (`backtest.py label`, `office_wiki.py build`), while a `--check` still fails
on a missing cassette. Two shared defects met on the way were fixed: `buying_dna.build` named an office list that no longer
existed (the Navy `dna` stage failed the same way; the Navy file rebuilds byte for byte with the fix) and its read-out could
not print an office with one stated value; `backtest.freeze` gave the last row's last field psql's final newline (the Navy's
frozen corpus carries one such `department\n`; not regenerated); and the pulse's as-of day is capped at the freeze day (a
DARPA award signed inside the FPDS reporting lag put it three months into the future; the Navy's day is unchanged).

The proof database. `schema_subset.py --from-kit DIR` (the pipeline's `schema` stage when `SCHEMA_KIT` names the folder) writes
the kit's exported schema with a stand-in for `gov_procurement_sources`, the one table the export stubs and the loader has
since filled; the stand-in's columns are the loader's, typed from the values it writes, and the file's header says it is a proof
database's stand-in and not the platform's definition. Every DARPA row loads under it (1,117 needs, 2,051 brain items, 734
contacts, 23 organizations, 1,117 lifecycle-history rows the triggers wrote). The freeze reads none of the stand-in's columns.

The labels, the office readings, the notice kinds and the remarks and oversight extracts were made on the branch with the
key on 2026-09-25 and replay here from the shared cassettes; the corpus now carries 25 leaders' statements from the
Director's testimony. Still not done: the program-office books (DARPA's awards name no office), the news and Federal
Register statements beyond the documents the layer has; `fiscal_year`, `revisions` and `joins` stay Navy-only.

### 9a. The second sync of 2026-09-25, applied to DARPA

Three more commits reached main from `sync/navy-intelligence` the same day (the platform read surface and its MCP server,
the outreach walk and the two comparison harnesses with their Navy results, a model reader of what a special notice
announces, the office reader extended to awards, topics and committee statements, each statement's published page in the
record, and twelve open tool defects listed in HANDOVER). What each became for DARPA:

| Layer | For DARPA |
| --- | --- |
| `notice_kinds.py` (what a special notice announces, one model call each) | Reads and writes per profile (`results/notice_kinds.json`; the prompt names the agency's short name); DARPA's 404 special notices are read, 332 with a kind and the words that say so; a notice without a key or cassette would stand unread with the reason, so `trace.signal_kind` would type it from its title as before |
| `office_wiki.py` for awards, topics and committee statements | DARPA's reads: 740 notices (201 placed), 0 live awards at a contracting office, 165 topics (32 placed), 1 directive (placed). The prompts named the U.S. Navy under every profile, as the cassettes were recorded (section 9); on 2026-09-25 the prompts were made to name the profile's agency and DARPA's reads were re-recorded (Opus 5.5 through the Claude command line): 81 notices and 2 topics placed with both quotes verbatim, the directive not (`research/docs/20`, section 6) |
| `navy.py`, the read surface and MCP server | Answers from the profile's record under `AGENCY=darpa` (`navy.py help` says so; the server is named by the profile key, `navy` for the Navy so the comparison harness's tool names hold): `office "Defense Sciences Office"` returns the office with its wiki page, buying book and next actions beside it; `neighbors` gives the agency as parent; `search quantum` finds the statements |
| `outreach.py`, `outreach_compare.py`, `dossier_compare.py` | The prompts, the section titles and the server name follow the profile (the branch's `short`, "U.S. Navy" or "DARPA", with the "U.S. " dropped where a word is wanted). Not run for DARPA: the walk is a few dozen calls of the larger model, and the harnesses drive Claude headless with the MCP server; neither key is here. The chain a DARPA walk would name is the agency and the technical office, the levels between them empty, as the tree holds none |
| The people rules (a surname written first; one person across two addresses) | Guarded for a record whose contacts are roles and mailboxes: an office after the comma is no given name, and a role or an address written as the name never joins two mailboxes. DARPA: 683 people from 1,293 observations (the unguarded rules gave 335, with "DARPA/BTO Dr. Pedro Irazoqui" and one "BAA Coordinator" on 299 addresses). The branch's own rule (a name made only of role words never merges) is kept beside the guards; it alone gave 654, merging the twenty "TTO BAA Coordinator" mailboxes into two because "TTO" is no role word |
| Each statement's published page in the record | The DARPA corpus was re-frozen with the merged emitter: 1,994 statements, 1,117 needs, 23 organizations; `sources` prints each record's page |
| The twelve open defects | Read against DARPA: 7 (a comma-separated capability), 8 (contacts per matched row), 9 (deadlines and the notice stage) and 10 (the 80-character title cut in `emit_notices`) apply to any profile and stay open for both; 1, 2, 4, 5, 6, 11 and 12 concern the Navy's forecast, ONR and NRL; 3 (FPDS only) is what section 8 records as the missing assistance source for DARPA |

### 9b. The two DARPA passes merged (2026-09-25)

The third pass (section 8) and the two syncs (sections 9 and 9a) changed the same tools and the same DARPA files. Main was
merged into the branch and the DARPA layer rebuilt once from the merged code. What changed for DARPA:

| Point | Before the merge | After |
| --- | --- | --- |
| Incumbent book | Not emitted: the forecast emitter returned before the swept awards when the agency has no forecast release | 3,513 "incumbent contract ends" items from the swept base awards; `ask incumbents HR0011` lists 728 ending within two years of 3,317 in the record, DSO 16 of 35 |
| Office-read prompts | "U.S. Navy" under every profile (section 9) | The profile's names ("DARPA", "Defense Advanced Research Projects Agency"); the Navy's prompts are the same strings as before, so its readings replay. The model read every DARPA record again: 189 of 693 notices, 90 of 845 awards and 44 of 165 topics placed, the directive not placed |
| Office code patterns | Two profile keys of one name: the branch's (a code read into its key) and main's (a code found in text) | `office_key_re` for the first, `reading.office_code_re` for the second |
| The office page and the meeting brief | "above it: -" for an office directly under the agency | The agency is named; the model's pages are unchanged |
| `trace status` | Failed on the Navy's forecast attribution file | States that the agency publishes no forecast |
| Corpus | 2,018 events (branch), 1,994 (main) | 5,531 events (4,126 incumbent), 312 outcomes, 1,117 needs, 23 organizations |
| Back-test, three-family bar | Recall 0.026 at 180 days, precision 0.952 over 21 judged cells | Recall 0.045 at 180 days (19 of 312 reached, median lead 309 days), precision 0.774 (24 of 31 judged) |
| People | 658 (branch), 683 from 1,293 observations (main) | 685 from 1,296 observations, under main's guard |
| Pulse | 552 cells and 112 actions (branch), 539 and 99 (main) | 552 cells, 546 actions (388 of them an incumbent contract ending) |

The Navy layer's emitted rows (`build/layers.sql`) are byte for byte the same after the merge.

## 10. The gaps with the Navy layer closed where no key is needed (2026-09-26)

The Navy layer had, and the DARPA layer lacked, a set of things that fall in three groups: collect stages never
run for DARPA, layers the Navy has only by hand, and read-back files built from the older record. This pass took
each in turn on the branch `task/darpa-navy-gaps`, in the order the records depend on each other.

| Gap | What the Navy has | What DARPA has now |
| --- | --- | --- |
| Grants | Nothing swept (FPDS carries none) | 452 grants and cooperative agreements from 5 USAspending pages; 218 carry an office code and load under it. An award listed before its period starts is dated the day the listing was saved |
| Award histories | The `changes` stage, followed for the forecast's contracts | 851 FPDS history pages for 719 swept awards: 426 extensions, 308 modifications and 38 funding changes in the corpus; the stage stopped at its 40-minute limit with about 1,300 histories still to take |
| Notices and awards | Swept by the collect job | 6 new notice details and 66 new FPDS pages of FY2026 awards; the Federal Register, oversight and news polls found nothing new |
| Contact routes | `contact_observations.json`, `contact_recommendations.json` and `review_log.json`, hand-written and reviewed (tasks/T02) | The same three files generated by `contact_routes.py` (the `routes` stage): 785 observations from the staff listing and the notice contacts, 26 recommendations for 13 offices, 785 checks against the saved records. Every row carries `generator`, and the tool refuses to write over a file holding rows it did not write, so the Navy's files stand |
| Attribution examples | 19 in `research/memory/attribution_examples.json` | 15 in `research/agencies/darpa/memory/attribution_examples.json`: 12 directly documented (nine by the office code in the description, three by the notice under the award's solicitation), 3 unresolved with the path to resolving each. A test checks every passage is verbatim on the saved record and every placement agrees with the layers emitter |
| Coverage matrix | 13 families | 14: a `grants` family, covered for the four offices whose code the awards carry |
| Protests, topic detail, committee reports, discovery sweeps, outreach | Run with their keys | Each waits on a key (`CONTEXT_DEV_API_KEY`, `BROWSERBASE_API_KEY`, `DATA_GOV_API_KEY`, `EXA_API_KEY`, `OPENAI_API_KEY`); the stages ran and recorded why they collected nothing |

Three shared tools changed:

- `office_wiki.py`: the reader's prompt carries the office directory and pages as the corpus stands, so a collection
  that adds a record changes every prompt and every cassette misses. With a key the model reads every record again;
  without one the reader used to write every record as unread, throwing away 323 placements. It now keeps a
  record's earlier reading, with the cassette that holds the pages it was read against, and says so (`kept`); a
  record never read stands unread with the reason. The check mode is unchanged: a missing cassette fails it.
- `agency_layers_sql.py`: the emitter read FPDS history pages from the whole ledger, so the histories this pass took
  for DARPA-funded awards that Navy offices signed changed five of the Navy's emitted rows. The emitter now reads its
  own rows and the unmarked ones, never another profile's; the Navy's emission is byte for byte HEAD's again.
- `sbir.py`: the topic sweep fell over a backfill row, which has no URL.

The back-test recall fell from 0.045 to 0.044 at 180 and 90 days because five new outcomes stand unread while the
same 19 are reached; the local floors were reset to these values. Precision rose from 0.774 to 0.848 with the
histories loaded (28 of 33 judged cells followed within a year). The pulse proposes 655 actions over the same 552
cells (546 before), 468 of them an incumbent contract ending.

## Appendix: evidence

Every row carries the note prefix "Navy versus DARPA comparison (2026-09-24):" in
`research/sources/documents_manifest.jsonl`. Retrieval date 2026-09-24 throughout.

| Document | Method | Status | SHA-256 (first 12) |
| --- | --- | --- | --- |
| `darpa.mil/work-with-us/opportunities` | direct | 200 | `2f1a2ee91670` |
| `darpa.mil/about/offices/contracts-management` | direct | 200 | `d50f09ec1cf2` |
| `darpa.mil/work-with-us/communities/small-business` | direct | 200 | `c5ecd1107e02` |
| `darpa.mil/about/offices` | direct | 200 | `46e7c6ab9fa0` |
| `darpa.mil/about/people` | direct | 200 | `6691192b8dcf` |
| `darpa.mil/work-with-us` | direct | 200 | `0ce103cb8a8a` |
| `darpa.mil/work-with-us/communities/industry` | direct | 200 | `c3fcb2d1538c` |
| `darpa.mil/about` | direct | 200 | `6fbd9f1ec562` |
| `darpa.mil/news` | direct | 200 | `b1d7a3b5fecf` |
| `darpa.mil/rss.xml` | direct | 200 | `e71e5b6c3ccf` |
| `darpa.mil/research` | direct | 200 | `f96fd78f2ee4` |
| `darpa.mil/about/offices/i2o` (answers with `/about/offices/ipto`) | direct | 200 | `d0b6d6ac72af` |
| `darpa.mil/rss/opportunities.xml` | direct | 200 | `63d33c0b9985` |
| `darpa.mil/about/budgets-testimony` | direct | 200 | `c4c4c73a92fe` |
| `darpa.mil/about/offices/sbpo` | direct | 200 | `d9cba347fe82` |
| `darpa.mil/about/offices/tto` | direct | 200 | `5e5961957e50` |
| `darpa.mil/json/staff` | direct | 200 | `015258979a1c` |
| `darpa.mil/work-with-us/opportunities/how-to-respond` | direct | 404 | |
| FPDS: base awards signed by HR0011, FY2026 to 2026-09-24, first page (last page starts at 290) | direct | 200 | `a20a1aab6367` |
| FPDS: base awards signed by HR0011, FY2025, first page (last page starts at 440) | direct | 200 | `ff5287f37dcc` |
| FPDS: base awards funded by 97AE, FY2026 to date, first page (last page starts at 330) | direct | 200 | `630eadf3f4b0` |
| FPDS: base awards funded by 97AE, FY2025, first page (last page starts at 520) | direct | 200 | `56b080d6a396` |
| FPDS: base awards funded by 97AE and not signed by HR0011, FY2026 to date, first page (last page starts at 40) | direct | 200 | `ad782d6e048d` |
| FPDS: base awards signed by N00039, FY2026 to date, first page (last page starts at 290) | direct | 200 | `a27c9431dc00` |
| FPDS: base awards funded by 1700, FY2026 to date, first page (last page starts at 42,700) | direct | 200 | `db490da16fd1` |
| FPDS: base awards funded by 1700 and signed outside agency 1700, FY2026 to date, first page (last page starts at 7,530) | direct | 200 | `6ecb24e13f04` |
| SAM.gov search, organization 500035490, archived, page 1 (5,192 in total) | direct | 200 | `1dee996a096f` |
| SAM.gov search, organization 500035490, active, page 1 (46 in total) | direct | 200 | `843032199e1c` |
| SAM.gov search, organization 100076586, archived, page 1 (2,414 in total) | direct | 200 | `9f07e04a79a6` |
| SAM.gov search, organization 100076586, active, page 1 (33 in total) | direct | 200 | `eef06455618b` |
| Federal Register documents naming DARPA since 2024-10-01 (9) | direct | 200 | `fde9471fbf65` |
| Federal Register agencies endpoint for DARPA | direct | 404 | |
| oversight.gov listing for "Defense Advanced Research Projects Agency", page 1 | direct | 200 | `95261dfa93c7` |
| grants.gov search endpoint (a POST search, not reproducible by GET) | direct | 405 | |
| `comptroller.defense.gov` FY2026 budget justification index | direct | 403 | |
| `comptroller.defense.gov` FY2026 budget justification index, capture of 2025-09-09 | wayback | 200 | `6b8e8a50ddcc` |
| `comptroller.war.gov` FY2027 budget justification index | direct | 200 | `c55a8762bf25` |

Navy figures are from `research/results/corpus.json`, `research/events/*.json`, `research/memory/people.json`,
`research/memory/organization_seed.json` and the datapack tables as committed on 2026-09-24. The FPDS page count
is read from the feed's last-page link; ten actions a page, so a last start of 290 means 291 to 300 actions.

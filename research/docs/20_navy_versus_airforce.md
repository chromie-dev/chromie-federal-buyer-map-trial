# 20 - Navy versus the Air Force: the same layer read against the Department of the Air Force, the Space Force included

`research/docs/14_reusing_this_for_another_agency.md` says what travels to the next agency and what is written
once for it; `research/docs/19_navy_versus_darpa.md` is the first reading of that procedure against an agency.
This document is the second: the Department of the Air Force, the Space Force included, built on 2026-09-25 as the
`airforce` profile of `research/tools/agency.py` under `research/agencies/airforce/`. Every number below is from a
saved document with a hash and a retrieval time, or from an artefact in this repository as it stands on this date.

## In plain terms

The Navy layer rests on a forecast and on a department whose sites answer this address. The Air Force publishes no
department-wide forecast, and every one of its sites refuses this address: af.mil, spaceforce.mil, the commands'
sites and the budget office's host, which completes no TLS handshake at all. What answers directly is the
government-wide record, the RSS feeds, and one source the Navy layer never needed: SAM.gov's federal organization
records, which state for every contracting office its name, its activity address code, its start date and its whole
parent path by name, through the program executive office to the department. The Air Force seed is read from those
records, one node per record on the path of every swept office. Pages come through the hosted browser; the FY2027
procurement books come from the Internet Archive's April 2026 captures of the budget office's file addresses.

The Space Force is not a second profile. USAspending, FPDS and SAM.gov file its acquisition under the Department of
the Air Force (subtier 5700), and SAM.gov's hierarchy places Space Systems Command directly under the department with
no Space Force level between. The layer reads the Space Force through Space Systems Command's six contracting offices
and the spaceforce.mil feed, and the seed carries the missing level as an interpretation, not a claim.

Two things were built once for every agency on this date because the Air Force forced them: a live model call
through the Claude Code command line when no OpenAI key is present (`research/tools/llm.py`), and a shared layer test
that holds the Navy's rules against any profile (`tests/test_agency_layer.py`).

## 1. What was compared and how

| Step | What was done | Where the evidence is |
| --- | --- | --- |
| The department's own pages | af.mil, spaceforce.mil, aflcmc.af.mil, afrl.af.mil, ssc.spaceforce.mil, saffm.hq.af.mil probed directly; every HTML page 403, the budget host no TLS; the RSS feeds 200 | ledger rows tagged `[airforce]`, status column |
| Government-wide records | FPDS ATOM by contracting office for 26 codes, FY2020 to FY2026; SAM.gov search by organization id for 21 offices; SAM.gov federal organization records for 38 organizations; Federal Register by agency slug; oversight.gov by full text; the GAO docket by agency | ledger rows tagged `[airforce]` |
| Hosted browser | af.mil and spaceforce.mil articles, the af.mil speeches address, GAO report and docket pages, the small business directory's Air Force page, the SBIR topic details | ledger rows with method `browserbase` and the tag |
| Internet Archive | the SAF/FM FY2027 budget page (capture of 2026-04-24) and its five procurement books (captures of 2026-04-25 to 2026-04-30) | ledger rows with method `wayback` and the tag |
| The Navy readers on Air Force data | every build and read-back stage of `research/tools/pipeline.py --agency airforce`, cassettes replaying where a document was read before, Opus 5.5 through the Claude command line where not | `research/agencies/airforce/` and `build/airforce/` |

## 2. Family by family

| Family | The Navy layer | The Air Force record on 2026-09-25 | Reads with the Navy tool? |
| --- | --- | --- | --- |
| Forecast | LRAE spreadsheets, one requirement per row | None department-wide. The SAF/FM budget page, the SAM.gov hierarchy and the commands' pages (403) name none; AFLCMC publishes a quarterly "SMART Guide" of upcoming acquisitions as a PDF, not yet read | Not applicable: the datapack, revision and fiscal-year stages are skipped, as for DARPA |
| Organization | navy.mil pages, PEO and command sites, the LRAE's own office names | SAM.gov federal organization records: 38 read (the department, AFMC, AFLCMC, AFRL, AFSC, SSC, the legacy AFSPC, a Hanscom site, nine program executive offices, 21 offices), each with type, level, activity address code, start date and full parent path; the leadership pages of af.mil, AFMC, AFRL, AFSC and SSC (hosted browser) and AFLCMC's organizational chart PDF: 17 leaders, each on the page's own words | New adapter `org_memory_airforce.py`; the record shape (observation, relationship, interpretation) is the Navy's |
| Notice | SAM.gov by organization id, NAVWAR headquarters 2,414 archived | 21 organizations swept, 631 search pages each fetched twice the same day (1,262 ledger rows), 3,182 notice details harvested since 2021-10-01 in two shards; five offices (FA8702, FA8611, FA8620, FA8656, FA8814) show no organization id in their newest notices and are not swept by organization | Yes: the same endpoint and parameter; the profile names the ids |
| Incumbent and awards | FPDS by contracting office, N00024 and N00039 about 500 pages each | 26 offices FY2020 to FY2026: 1,917 feed pages (2,029 ledger rows, a page re-taken where a sweep was rerun), 2,151 base awards in the first frozen corpus and 26,784 incumbent events after the merge, the 5,193 history pages read into extensions, options and endings; every office probed signs under 500 base awards a year but Ogden (FA8201, 500 to 2,000); the histories were swept in six shards (`fpds_sweep.py histories --shard`) | Yes, unchanged; the history sweep gained shards because fpds.gov answers a history page in ten to twenty seconds |
| Programs (SBIR/STTR) | 1,100 Navy topics | 1,582 USAF topics on the index pages saved 2026-09-22, 755 opened since FY2020, every detail read through the hosted browser; the portal now refuses the index query (Akamai), so the sweep gained `--saved`; the command column (AFMC, AFRL-RY, SSC, AFWERX, PEO-WEAPONS) is read by its stem | Yes, with `sbir.command_org` |
| Budget | Department of the Navy P-40 books, 48 lines from one book | Five FY2027 procurement books from Wayback captures: Aircraft Procurement Vol I (5 lines), Missile (14), Ammunition (2), Other Procurement (65); Vol II carries no P-40; 86 lines; the RDT&E books and a Space Force procurement book were not on the captured page | Yes, after the line pattern took a service letter on the appropriation code (3080F) and six-digit line numbers |
| Oversight | oversight.gov "Navy" and "Naval", the GAO feed; 27 documents | oversight.gov "Air Force" and "Space Force" (50 rows each, 8 and 5 since 2025), the GAO feed (1); 7 reports read, 22 findings kept, 2 reports left as not about the department | Yes; the watch now re-takes under this profile's note a report another layer saved first, and the reviewed-agency pattern no longer accepts "Department of War" alone |
| Leaders' words | navy.mil speech and testimony archives, 37 documents | No speech archive: af.mil's /News/Speeches/ renders the general news listing (100 articles, titled "News"); the House Armed Services feed carried the department's FY2027 posture hearing of 2026-05-20, whose three witness statements read into 161 events | The House feeds travel; the archive is None in the profile; leaders' words come as testimony (the congress family, as for the Navy's hearings) and as news |
| Congress | committee reports from GovInfo, the Navy pattern | No key for GovInfo here; the 3 reports the Navy layer saved read with the Air Force pattern: 82 directives | Yes (shared source) |
| Federal Register | agency slug, 268 documents | agency slug `air-force-department`, 230 documents, none typed (as for the Navy) | Yes |
| Protests | GAO docket by agency, hosted browser | 9 docket pages and 166 case pages through the hosted browser (179 gao.gov rows with the feeds and the product page); 166 protests | Yes |
| Grants (main's fourth DARPA pass, 2026-09-26) | not read for the Navy (FPDS carries its contracts; the `grants` stage is new) | USAspending's assistance awards for the department since FY2020, 59 pages of 100 by POST to the search endpoint, most of them AFOSR's basic research grants (award ids under FA9550); placed under a swept office where the award id starts with its code | Yes: `assistance.py` reads the profile's subtier; the matrix gains a fourteenth family |
| News | navy.mil and NAVWAR feeds, DVIDS, the DoD contracts index | af.mil and spaceforce.mil feeds (25 items each, 5 naming the department, taken through the hosted browser after a direct 403), DVIDS, the per-office Exa news searches, whose official pages (aflcmc.af.mil, ssc.spaceforce.mil, afmc.af.mil) came through the hosted browser and whose secondary and trade-press pages (8) came direct; the DoD contracts index 403 as everywhere; 19 articles modelled, 31 claims | Yes; the per-office GDELT sweep answered 10 queries before GDELT rate-limited this address |
| People | notices, forecast contacts, speakers, witnesses | 5,091 observations from the notices and the hearing, 1,052 people, every position a stated role | Yes |
| Small business | the Department of War directory and the office pages | The directory's Air Force entry and the airforcesmallbiz.af.mil page (hosted browser): 2 offices, 1 page saved | Yes |

## 3. What travelled, as tested

Every build and read-back stage of the pipeline ran under `AGENCY=airforce` with no change but the profile,
except where section 4 says a tool was generalised. The FPDS, SAM.gov, oversight, protest, Federal Register, SBIR,
people, news, small business, layers, database, graph, back-test, office-reading, pulse, baselines, Buying DNA,
vendors and coverage tools read the Air Force record as they read the Navy's. The shared layer test passes against
DARPA (24 tests) and against the Air Force (24 tests) on every artefact written.

## 4. What is written once for the Air Force, and what was generalised

| Item | Where | Note |
| --- | --- | --- |
| The profile | `AIRFORCE` in `research/tools/agency.py` | 26 FPDS offices, 21 SAM.gov organization ids and 5 codes, the USAF SBIR component and its commands, the topic and PIID patterns, the Federal Register slug, the oversight queries, the feeds, the docket listing, the P-40 books folder, the pilot program executive offices as SAM.gov prints them, the coverage rows |
| The seed adapter | `research/tools/org_memory_airforce.py` | SAM.gov federal organization records read upward from each swept office; `contracts_for` from office to parent (inferred); the FPDS office tag as a second observation; a PEO named by its portfolio word alone takes the code's prefix (PEO WEAPONS) so that the word "weapons" in a report is not the office; `leads` from the leadership pages' biography link text ("Rank NAME Title, Organization") and from AFLCMC's chart by column |
| The coverage matrix script | `research/tools/coverage_matrix_airforce.py` | writes the matrix from the layer's files with a reason per uncovered cell; `coverage.py check` validates it |
| The registry and the matrix | `research/agencies/airforce/sources/` | 21 sources, three of them the department's own (the sites with their leadership pages, the budget books, the speeches address that renders news) and one the Navy never needed (SAM.gov's organization records); the matrix is written by `coverage_matrix_airforce.py` from the layer's own files, 6 organizations by 14 families, every covered cell saying what it covers and every uncovered one why |
| The budget reader (generalised) | `research/tools/budget.py` | the appropriation code takes a service letter, the line number four to six digits; a book note names the agency by abbreviation, short name or label |
| The SBIR sweep and build (generalised) | `research/tools/sbir.py` | `--saved` reads the component from the saved index pages when the portal refuses the index query; `command_org` reads the command's stem |
| The oversight watch (generalised) | `research/tools/oversight.py` | a report counts as saved for a layer only under that layer's note |
| The remarks readers (generalised) | `research/tools/remarks.py` | the archive address, the article pattern and the archive kinds come from the profile; a profile may have no archive |
| The model client (generalised) | `research/tools/llm.py` | a live call through the Claude Code command line when OPENAI_API_KEY is absent; Opus 5.5, because Sonnet 5 composed the "exactly as the text writes it" fields (0 of 7 findings kept) where Opus copied them (5 of 5) |
| The FPDS history sweep (generalised) | `research/tools/fpds_sweep.py` | `--shard i/n` |
| The layer test (generalised) | `tests/test_agency_layer.py` | one file for every profile; an EXPECT entry per agency |
| The hosted-browser fallback | `research/tools/context_fetch.py` | pages go through Browserbase when context.dev has no key |

## 5. Where the two agencies meet

The Air Force is closer to the Navy than DARPA is: several contracting offices per center, program executive
offices that own requirements, procurement books in the P-40 form, a docket of protests, a department-level small
business office. Where it differs from both is access: nothing of its own answers this address, so every page is a
hosted-browser or Internet Archive retrieval and is recorded as such, and the organization is read from SAM.gov's
records rather than from the department's pages, and the leaders from the leadership pages the hosted browser saved
(af.mil, AFMC, AFRL, AFSC, SSC) and from AFLCMC's organizational chart, since its own leadership paths answer 404.
A leader whose title names the organization is `directly_documented`; a title alone on the organization's own page,
or a name read from a chart's column layout, is `inferred` and says so.

## 6. Issues found, and what changed

- `sbir.py sweep` read a ledger row's `url` without a default and stopped on the backfill rows; fixed.
- The budget reader's line pattern was the Navy's (`1810N`, four digits); generalised.
- The oversight watch treated a report saved by any layer as saved for this one, so a report the Navy's watch took
  first was never read under the Air Force profile; the watch now looks at rows under its own note.
- The Air Force profile's reviewed-agency pattern accepted "Department of War", under which oversight.gov files every
  DoD OIG report; tightened.
- The seed's PEO nodes named "WEAPONS" and "TRAINING" (as SAM.gov prints them) drew every mention of weapons or
  training in a report to the office; the adapter now prefixes the code's "PEO".
- The remarks selfcheck failed under every profile but the Navy's because its fixture note lacked the profile tag; fixed.
- The layers emitter refuses a registry status outside its closed list; the registry uses `not_inspected`.
- The outcome labelling ran one model call at a time (five hours for 1,213 outcomes); it now runs eight.
- The office-reading prompts said "Navy" whatever the profile, and about two of five Air Force placements named an
  office that is not in the directory; the prompts now name the profile's agency, and an answer naming an unknown
  office, or an office without the notice's own words, is recorded with no office and the problems that say why. The cassette keys changed with the wording, so the Air
  Force and DARPA office reads were re-recorded.
- A model refusal stopped the office-reading stage; the client now raises the lookup error the reader records as
  `unread`.
- The Claude command-line fallback of `llm.py` is gated to profiles other than the Navy's (or `LLM_PROVIDER=claude`), so
  a Navy build without an OpenAI key still replays and stops rather than recording under a new model.
- `contracts_for` from SAM.gov's parent path is a reading of a stated parent, not a stated arrangement; the edge is
  now `inferred` with the note saying so.
- Twenty-three `news.py search` ledger rows of this date carried no profile tag; the search note now carries it,
  and those rows (uncommitted, of this layer) were re-noted.
- The shared layer test compares a PDF observation's passage with the PDF's layout text, as the chart needed.
- Second review round. The matrix script passed every covered cell's note into the reason slot, so the 47 covered
  cells said nothing; fixed, and its counts (topics, committee reports, the hearing date, the Exa searches) are now
  computed from the files. The office reader's blanking rule also applies to the Navy's saved reads, where 18
  placements carried "office is not in the directory"; the Navy file was rebuilt from its cassettes and the 18 are
  now blank with the answered office kept. The model client replayed a Claude cassette under any provider; it now
  does so only under the Claude provider. The remarks tool's conference query and publisher were the Navy's words;
  they come from the profile. The people reader typed "Secretary, Department of the Air Force" as other and
  "Secretary of the Air Force" as an acquisition leader for the same witness; the profile now names both forms. A
  leadership-page title that names another organization of the seed now attaches there, or nowhere when it names
  several.

## 7. Building the Air Force layer, in the order the records depend on each other

1. Probe every host and record the refusals; register the sources.
2. Resolve the SAM.gov organization ids from each office's newest notices; read the organization records upward
   from each office into the seed.
3. Sweep FPDS by office in fiscal-year shards and SAM.gov by organization in two shards; fetch the SBIR details from
   the saved index pages.
4. Take the blocked pages through the hosted browser and the budget books from the Internet Archive.
5. Read every family; load; freeze; label; read the offices; score the pulse; write the matrix from the files.
6. After the merge with main (2026-09-26): take the department's grants from USAspending (the `grants` stage, 59
   pages) into the record, write the contact routes from the notice contacts (the `routes` stage), rebuild the load
   and read-back stages under the merged tools, and re-record the office reads under main's prompt.

## 8. The Air Force layer as built (2026-09-25)

Every stage of the pipeline ran; the layer's files are listed in `research/agencies/airforce/README.md`. The record,
in numbers as frozen on 2026-09-26 after the merge with main (first frozen 2026-09-25):

| What | Air Force | For comparison |
| --- | --- | --- |
| Organizations in the seed | 38 (21 contracting offices, 9 program executive offices, 5 commands and centers, the department, a laboratory, a site), and 17 people leading 9 of them | DARPA 31 |
| Notices harvested | 3,182 under 21 organization ids since 2021-10-01 | DARPA 1,165 |
| Base awards on the FPDS pages | 17,564 across 26 offices, FY2020 to FY2026; 5,193 history pages followed | DARPA 3,536 |
| Frozen corpus | 31,189 events (26,784 incumbent, with the award histories and the grants), 2,748 needs, 1,213 outcomes; before the merge 8,720 events | DARPA 6,720 events, 317 outcomes |
| Special notices read by kind | 255, 199 with the words that say so | DARPA 405, 332 |
| Office reads | 2,672 notices, 2,516 awards, 750 topics, 243 statements; 81, 105, 79 and 16 placed with both quotes verbatim under the merged prompt (the first pass, with prompts that said "Navy", placed 256 notices, two in five at an office outside the directory; the second, naming the department, 81, 30, 74 and 16 over 551 awards) | DARPA 697 notices, 1,067 awards, 165 topics; 65, 52 and 3 placed under the merged prompt (see the DARPA readme) |
| People | 1,052 from 5,091 observations | DARPA 685 |
| Back-test, three-family bar | recall 0.214 at 180 days, 0.248 at 90, 0.274 at 30 (floors 0.113, 0.129, 0.149); 342 of 1,213 reached; median lead 746.5 days; no precision cell; the incumbent-ending baseline alone reaches 0.286, 0.302, 0.315. Before the merge: 0.142, 0.166, 0.191, 244 reached, median lead 637.5 days | DARPA recall 0.022, 8 reached, precision 1.0 over 14 cells (as of its 2026-09-25 build) |
| Pulse | 1,213 cells, 50 ranked positions, 1,286 actions (694 before the histories entered the record) | DARPA 552 cells, 655 actions |
| Coverage | 49 of 84 cells covered (6 organizations by 14 families, the grants added) | DARPA 75 of 98 |

What the numbers say. The Air Force record is the largest the pipeline has read, and it reaches outcomes better than
DARPA's did without a forecast (recall 0.14 against 0.02), because the same offices post the same requirement as a
sources-sought, a presolicitation and a solicitation over months, and the award histories add the option and
extension events. What it lacks is an owner above the contracting office: the notices name the office symbol
(AFLCMC/HBBK, SSC/CGK), not the program executive office, so the PEO cells that the Navy's forecast rows and DARPA's
title codes gave for free exist here only where the office reading placed a notice, and the precision measure has
no cell to judge. The `contracts_for` edges from SAM.gov's hierarchy state which PEO each office contracts for; a
reading that carries a need from the office to its PEO on that stated edge is the next step, and it is a rule to
write, not a fact to find.

The model work on this date was 255 notice kinds, 1,213 outcome labels, 7 oversight reports and 3 statements on
Opus 5.5, and 6,160 office reads on Sonnet 5 (recorded three times as the prompt changed: naming the department, then main's chain above the office), every one a cassette that replays.

## Appendix: access findings

| Host | Direct | Hosted browser | Internet Archive |
| --- | --- | --- | --- |
| www.af.mil, www.spaceforce.mil (pages) | 403 | 200 | not needed |
| www.af.mil, www.spaceforce.mil (RSS) | 200 | | |
| www.aflcmc.af.mil, www.afrl.af.mil, www.ssc.spaceforce.mil, www.afmc.af.mil | 403 | not yet taken | |
| www.saffm.hq.af.mil | no TLS handshake | no TLS handshake | captures of April 2026 for the FY2027 page and five books |
| www.airforcesmallbiz.af.mil | 403 | 200 | |
| sam.gov (search, notices, organization records) | 200 | | |
| www.fpds.gov | 200 | | |
| www.dodsbirsttr.mil (index) | 403 (Akamai) | 400 | |
| www.dodsbirsttr.mil (topic details) | 403 | 200 | |
| www.oversight.gov, www.federalregister.gov, docs.house.gov, www.dvidshub.net | 200 | | |
| www.gao.gov (feed) | 200 | | |
| www.gao.gov (reports, docket) | 403 | 200 | |
| www.war.gov/News/Contracts/ | 403 | not taken | |
| api.gdeltproject.org | 200, then rate limited | | |
| api.govinfo.gov | no key | | |

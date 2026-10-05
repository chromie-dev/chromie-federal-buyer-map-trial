# Startup intelligence brief: format and evidence bar

One brief per (startup profile, agency). The workflow in this folder (`startup-agency-brief.js`, see README.md)
writes it; every lane reads this file first.

## Paths and commands

All paths are relative to the repository root; `PY=.venv/bin/python` unless the run says otherwise.
- Record and tools: `AGENCY=<agency> $PY research/tools/navy.py <cmd>`. `navy.py help` prints the record date
  ("answered from the <X> record as of YYYY-MM-DD"). Agencies on disk: `ls research/agencies/`; navy is the
  default record (its files sit under `research/` itself) when AGENCY is unset.
- Report layer (`research/tools/report.py`, the same reads in a fixed order, each answer kept in full):
  `$PY research/tools/report.py inventory --agency <agency>` (what the record holds, domain by domain, with rows and
  newest date: the Sources counts and every boundary come from it); `$PY research/tools/report.py gather --agency
  <agency> --spec agency-full --focus <term> --out OUT/sources/gather` (the anchor offices with their rule, one
  answer per section in `evidence.md`, the full text in `bundle.json`); `AGENCY=<agency> $PY research/tools/navy.py
  report budget <office>` (the budget lines placed in an office, the basis of each placement, enacted and request
  amounts, obligations).
- Record files: `REC/events/*.json` (budget_lines, congress_events, fedreg_events, news_observations,
  oversight_events, remarks_events, protest_events, assistance_awards, sbir_topics, hiring_observations,
  vendor_hiring_observations), `REC/results/corpus.json` (orgs, needs, events, outcomes), `REC/memory/`,
  `REC/sources/source_status.json`, where REC is `research/agencies/<agency>` (or `research` for navy).
- Run directory OUT (given): `OUT/sources/{tools,record,web,profile,gather}/` hold every text a quote rests on,
  one file per command or page; `OUT/notes/<lane>.md` holds each lane's findings; `OUT/brief.md` is the brief.
- Checker: `AGENCY=<agency> $PY research/workflow/check.py OUT/brief.md OUT/sources REC` prints JSON: problems,
  quote failures, record identifiers that resolve to nothing, and the tally line.

## Hard rules

- Read only. Never run `pipeline.py`, a `sweep`, `watch`, `collect` or any stage; never write a database.
  A handful of single web pages per lane is fine.
- Keys come from the environment or the repository's `.env`: export one variable at a time, never print, echo or
  save a key. `SAM_API_KEY`, `CONTEXT_DEV_API_KEY`, `EXA_API_KEY` as `.env.example` lists them. Keyless: the
  SAM.gov site API, USAspending, the Federal Register, FPDS ATOM. A host that answers 403 (dodsbirsttr.mil and
  several .mil hosts do) goes in Still open; it is never worked around.
- On a Mac shell `grep` may be aliased: use `/usr/bin/grep`; there is no `timeout`.
- Prose: formal register, no em dashes, no reader asides, no internal terms (ledger, lane, stage, twin,
  cassette) outside Audit and How this was produced. Banned outside quotes: likely, probably, imminent,
  expected soon, will release, RFP coming. Dates are facts with their source, never forecasts.
- People: name, role and office as the source writes them, with the source. No personal e-mail or phone; an
  official mailbox or a contact a notice publishes only. A record shows a role, never budget authority, a
  decision or willingness to advocate: those are readings, always "potential", with their reasoning in People
  and program offices. A title alone establishes none of them. The words decision maker, budget holder and
  champion appear only after "potential".

## Evidence bar

- Every table row: `| Identifier | What it says (exact quote) | Status | Source | Class | Provenance |`.
  The quote is the source's words, character for character, in double quotes, and it must exist in the file
  the row's Source names. Paraphrase goes in prose, never in the quote column.
- Source: the id `save_source.py` printed for the saved result, then its url or command (`[S12] navy.py search
  radio`; its line in `OUT/sources/index.jsonl` points to the file holding the text), or a record file by path;
  "same" repeats the row above in the same table. Matching is exact: a url or command without its id must equal
  the saved one, so `navy.py search radiosonde` never resolves to a saved `navy.py search radio`. The checker
  reads the quote only there: a passage from one record never supports a row about another, and a note the run
  wrote is never a source.
- A refused, rate-limited or empty fetch is saved as failed (`save_source.py --failed`, or automatically for
  empty output and block pages). It is a collection gap: Still open lists it as "not collected" with its
  reason, and no row rests on it. It is never an empty result.
- Identifier: one identifier per row as the record or the page prints it (notice number, PIID, office acronym,
  program element, person's name). A tool or repo row's identifier must name something in the record (the
  checker resolves it through report.py); a web row's identifier is the page's own.
- Status: **Live** (open or current on TODAY: a notice whose response date is on or after TODAY, an active
  program page, a contract whose period of performance has not ended, the current or next budget request);
  **Historical** (a closed notice, a closed SBIR or STTR topic, an ended contract or program, a past budget
  year); **Adjacent** (true and current but not in the company's line: support offices, the contracting office,
  neighbouring programs). A date check against TODAY decides Live versus Historical, never the source's wording.
- Class: **primary** is the buyer's or the government's own publication (SAM.gov, USAspending, FPDS, the
  agency's own site, justification books, govinfo, GAO, the Federal Register) or the company's own page about
  itself; **secondary** is press, trade media, aggregators, conference write-ups.
- Provenance: repo (a saved document in the record), tool (a navy.py or report.py answer), web (a page read this
  run), profile (the company's own words).
- P0 fields (lead buyer, chain, lead instrument, deadline, intake rule, contracting office, named contact,
  incumbent, set-aside and eligibility) rest on primary sources only.
- "not in file" only when the primary source was read and lacks the fact; each one gets a Still open row.
- An empty answer from a source the record never collected is "not collected", never zero or "none"; say
  which collection would close it (the inventory's domains and `sources/source_status.json` say what was read).
- Never invent a level of the chain: write "(no PEO level in the record)" rather than a plausible PEO.
- An anchor office the report layer chose is an inference: state its rule once (report.py prints it).
- Never claim the brief comes from one file; the Sources section lists what it rests on.

## Fit

- **Fit:** line, first line of the Bottom line: plain, specific, honest. Say what fits, what does not, and
  what the agency does not buy. Never a bare "yes". Model: "fit for research that releases nothing; the Navy
  does not buy SO2 release or cooling credits."
- Two tests rule a candidate in or out before it is scored. Write them in plain words everywhere; never
  write gate codes (G1, G2) in the brief.
  - "Does the buyer ask for this?": the buyer's own words name what the company sells. A product the agency
    does not buy fails, however open or urgent the notice.
  - "Can the company bid as described?": the instrument needs no size status, set-aside, ownership, prior
    award or clearance that the profile does not state. Unstated means "not known yet", not a fail.
  - A third test only when the profile states an instrument preference or a production limit.
- Section "What rules a route in or out": a table `| Test | What it asks | What the company's pages say |
  Result for the lead |` with the two tests, then ONE sentence naming what the company has not stated and
  every route that waits on it ("Not known until the company states its size status and clearance: the
  Proposers Day (Top Secret to attend) and every SBIR topic."). Say it once here, not on every row.

## Ranked opportunities (published rule, same text every run)

1. Scores 0 to 3. Mission fit: 3 the buyer's own words name the capability, 2 they name the domain,
   1 a general fit, 0 none. Awardability: 3 an open instrument the company as profiled can submit to,
   2 open with a gate unknown, 1 a route to a future instrument, 0 closed or ineligible. Access and urgency:
   3 a named route and a dated step within 90 days of TODAY, 2 a named route and a step within a year,
   1 a route without a date, 0 none. Score = 40 x fit/3 + 35 x awardability/3 + 25 x access_urgency/3,
   one decimal.
2. Groups, in order, each under a bold title that starts with its name: **Instruments** (a Live solicitation,
   BAA, CSO, topic or shopping notice the company can submit to), **Routes** (office, person, event, prime
   contract, support office leading to an instrument), **Live signals** (program page, release, budget line,
   job posting, company record; no submission), **Historical** (past demand, closed topics included; never a buyer),
   **Ruled out** (fails a test: the buyer does not ask for this, or the company cannot bid).
3. Inside a group: tier (lead-eligible, conditional, route-only, historical, ruled-out), score highest first,
   primary before secondary, candidate id lowest first. Ranks run 1..N across groups.
4. Lead = first instrument; second buyer = first instrument from another office; third route = next
   instrument from a third office. When fewer offices hold an open instrument, the remaining places go to
   the first route of each office not yet named, in rank order. A route, signal or Historical row never leads.
- Table: `| Rank | Id | Tier | Candidate | Status | Blocked by | Mission fit | Awardability | Access/urgency |
  Score | Identifier | Provenance |`; Candidate is "Office: title"; scores as `n/3`; Provenance as
  "tool, primary". Blocked by is "none" when both tests pass, else a plain reason of a few words ("needs Top
  Secret clearance", "buyer asks for lasers, not autonomy", "size status not stated").
- After the rule: a worked example on this run's own top rows, then "Why each score" per candidate: three
  lines (fit, awardability, access and urgency), each quoting its row once, plus a "Blocked by" line only
  when a test fails or is not known yet. Never a line that says a test passed.
- **Lead order:** line in the Bottom line names lead, second and third identifiers in that order, each with
  its nearest dated step. The realistic first dollar (the open instrument with the nearest real deadline) is
  never hidden behind a route.

## Sections, in order (headers exact; "Past" takes the company's line)

1. Sources: first, before the Bottom line, so a reader sees what the brief rests on before any claim.
   A `| Family | Source | What the record holds |` table from the inventory (each domain's rows and newest date)
   and the event files (notices, awards, grants, SBIR topics, budget, Congress, Federal Register, oversight,
   remarks, news, organization and people, small business, bid protests, hiring), marking families not
   collected; then every web address read, the profile pages, and repo documents cited.
2. Bottom line: **Fit**, **Lead buyer**, **Second buyer**, **Third route**, **Lead order**,
   **Money and signals**, a mermaid chart agency > offices > instruments, **Do this week** (dated steps, each
   "Rests on" a row). A reader new to the agency must follow it: sentences under about 30 words, every
   acronym spelled out at first use, no candidate ids or codes, no line that repeats another.
3. What rules a route in or out. 4. Ranked opportunities. 5. P0 fields (`| Field | Value | Identifier |
   Passage (exact quote) | Source | Class | Provenance |`; the passage is the source's words for the value). 6. Agencies and offices. 7. Programs and requirements. 8. Budget lines behind the work
   (for each office in the line, the lines `report budget` places there with the basis of each placement, then a
   figures table per program element, then evidence rows). 9. What Congress has directed. 10. What leaders
   have said (speaker, role, date). 11. Oversight, Federal Register and news. 12. Solicitations, BAAs and
   planning notices. 13. How long from solicitation to award. 14. Awards and incumbents. 15. How its
   contracting office buys. 16. Contracts in this line ending soon. 17. What the winning companies did in the
   last two years. 18. SBIR and STTR (open topics in the record that fit, else say none fits; closed topics are
   Historical). 19. People and program offices (evidence rows for each person's documented role, then the
   readings below). 20. Where the office sits. 21. What changed in the last year.
   22. Next actions from the record. 23. Teaming and access routes (mermaid teaming map; inferences labelled as
   inferences). 24. Past awards in the company's line under the office. 25. Company capability and risk
   signals. 26. Bid protests. 27. Small business routes. 28. Brief for the first meeting (questions, each
   resting on rows). 29. Outreach drafts. 30. Still open (`| Section | Not found | How to close it |`).
   31. Audit. 32. How this was produced.
- People readings, in People and program offices. "Rests on" lists row identifiers separated by semicolons, and
  must name a row beyond the person's own role row: a potential budget holder rests on a line in Budget lines
  behind the work placed in the person's office; a potential decision maker or champion rests on an action or
  statement outside People and program offices (a notice or award naming them in the role, remarks, a meeting).
  "Still to confirm" is never empty or "none". A person's standing comes from the record, not the brief, and is
  per post: the person's `posts` entry for that office and role, so a record of one post never confirms another.
  Only `confirmed_current` (an official source stated the post within six months) is written as holding it now; a
  `recently_observed` person is written as last observed in the post on that date, and confirming the post is
  listed under "Still to confirm".
  `| Person | Reading | Documented role (exact quote) | Source | Why they matter for this requirement | Rests on | Still to confirm |`
  (Reading: potential budget holder or potential decision maker), and
  `| Person | Documented role (exact quote) | Source | Problem they own | Why the company matters to them | Evidence of willingness to advocate | Rests on | Still to confirm |`
  for potential champions (a new role plus a meeting makes a potential champion, never a confirmed one; no
  evidence of advocacy is written "none found").
- Outreach: one letter per named route of the top three; each letter carries only facts in the rows and names
  the rows it rests on; it never asserts size status, clearance, past performance or a contact the brief does
  not carry, and says so where it declines ("refuse-uncarried"). Run `navy.py check` on each letter (answer
  JSON on stdin) and paste its verdict.
- Completion: the checker exits 1 while any problem, quote failure or unresolved identifier is left. The
  workflow ends with a checker-only gate; the brief is released (copied to its final path) only on a clean run,
  and a failed gathering step, validator or gate ends the run as incomplete.
- Audit: the checker's tally line pasted last, its quote result ("n quotes checked, n not found"), its
  identifier result ("n record identifiers checked, n unresolved"), and every problem it raised with what was done.
- How this was produced: date, AGENCY, record date, navy.py and report.py calls by command, pages read, the
  validation pass and its fixes, tool defects seen this run (command, what it printed, why it is wrong).

## Tool map (which command feeds which section)

| Section | Commands and files |
|---|---|
| Sources, boundaries | `report.py inventory`; `sources/source_status.json` |
| Anchor offices, baseline evidence | `report.py gather --spec agency-full` (anchors with their rule, one answer per section) |
| Offices, chain, people | `search`, `office`, `people`, `neighbors`, `wiki`, `page office`, `page person`, `sources` |
| Programs, initiatives | `initiatives`, `cell`, `search`, `pulse rank`, `pulse actions` |
| Budget | `report budget OFFICE`; events budget_lines |
| Congress, leaders, oversight, news, Federal Register | `initiatives`; events congress_events, remarks_events, oversight_events, news_observations, fedreg_events; web pages |
| Solicitations | `search`, `trace notice`, `trace status`, `pulse week`, SAM.gov pages |
| Awards, incumbents, ending | `ask incumbents`, `vendor`, `trace award`, `dna`, USAspending award pages |
| Winners' moves, teaming | `ask moves`, `ask team`, `ask match --profile` |
| Hiring | `jobs.py read`, `vendor_jobs.py read`; the incumbents' own careers pages |
| Time to award, analogs | `ask analogs`, `trace need` |
| Changes, revisions, protests | `ask changed --days 365`, `revisions`, `protests`; events protest_events |
| SBIR and STTR | `topics`; events sbir_topics |
| First meeting | `ask prep --person` |
| Small business | `office` of the small business office; memory/small_business_offices.json |
| Outreach | `check` (answer JSON on stdin) |

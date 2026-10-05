export const meta = {
  name: 'startup-agency-brief',
  description: 'Startup profile + agency: the record tools, the report layer and the web in parallel, the ranking rule, three validators, then the brief',
  whenToUse: 'Build a startup intelligence brief against one agency record (navy, darpa, army, airforce, dhs, doe, nasa, noaa) to the review bar',
  phases: [
    { title: 'Profile', detail: 'company pages -> profile.txt and search terms' },
    { title: 'Gather', detail: 'four lanes at once: record tools, record files, web, company' },
    { title: 'Draft', detail: 'brief.md in the SPEC shape, ranked by the published rule, checker clean' },
    { title: 'Validate', detail: 'evidence, review bar and coverage, ranking and dates' },
    { title: 'Apply', detail: 'apply verified fixes, re-run the checker' },
    { title: 'Gate', detail: 'the checker alone decides: a clean run releases the brief, anything left ends the run incomplete' },
  ],
}

// Run from the repository root (the agents share the session's working directory). See research/workflow/README.md.
const A = args || {}
for (const k of ['company', 'profile', 'agency', 'today', 'out']) {
  if (!A[k]) throw new Error(`args.${k} missing`)
}
const DIR = 'research/workflow'
const SPEC = `${DIR}/SPEC.md`
const PY = A.python || '.venv/bin/python'
const OUT = A.out
const REC = A.agency === 'navy' ? 'research' : `research/agencies/${A.agency}`
const NAVY = `AGENCY=${A.agency} ${PY} research/tools/navy.py`
const REPORT = `${PY} research/tools/report.py`
const CHECK = `AGENCY=${A.agency} ${PY} ${DIR}/check.py ${OUT}/brief.md ${OUT}/sources ${REC}`
const SAVE = `${PY} ${DIR}/save_source.py ${OUT}/sources`

const COMMON = `Company: ${A.company}. Agency record: AGENCY=${A.agency} (files under ${REC}). TODAY is ${A.today}. Work from the repository root.
Read ${SPEC} first: paths, hard rules (read only: no pipeline.py, no sweep/watch/collect, no database writes, keys exported one at a time and never printed), the evidence bar, the people rules and the brief format. Run directory OUT=${OUT} (create subfolders as needed).
Save every text a quote could rest on through the source saver, which writes the file and its line in ${OUT}/sources/index.jsonl: \`<command> | ${SAVE} <tools|web|profile>/<name>.txt --source "<the url, or the command as you ran it>"\`; a file a tool wrote itself: \`${SAVE} <path under sources> --file --source "<command>"\`. A refused, rate-limited or empty fetch is recorded, never dropped: \`${SAVE} --failed "HTTP 403" --source "<url>"\` (the saver also marks empty output and block pages failed). WebFetch returns a model summary, so for quotes save raw text instead: curl plus a tag strip, the SAM.gov or USAspending APIs, or a Context.dev scrape for JavaScript pages. Never put a key in --source. A 403 host is a gap, not worked around.
A row's Source cell starts with the id the saver printed for that result, then the url or command as saved ("[S12] navy.py search radio"), or names a record file by path ("same" repeats the row above in the same table). Matching is exact: a different search, even one sharing a prefix, never stands in. The checker reads the quote only in the file that source points to, so a passage from one record never supports a row about another.
Read big outputs with head or /usr/bin/grep, not whole. Your final answer is data for the orchestrator: conclusions and paths, never file contents.`

const LANE = {
  type: 'object',
  properties: {
    notes_path: { type: 'string' },
    files_saved: { type: 'integer' },
    key_findings: { type: 'array', items: { type: 'string' }, description: 'at most 15, each naming its identifier and date' },
    gaps: { type: 'array', items: { type: 'string' } },
    tool_defects: { type: 'array', items: { type: 'string' }, description: 'command, what it printed, why it is wrong' },
  },
  required: ['notes_path', 'files_saved', 'key_findings', 'gaps', 'tool_defects'],
}
// The tools lane must answer the hiring reads and name the anchors: a lane that skips them cannot return.
const TOOLS_LANE = {
  ...LANE,
  properties: {
    ...LANE.properties,
    anchors: { type: 'array', items: { type: 'string' }, description: 'report.py anchor offices with the basis it printed, and the candidate offices added for the company line' },
    hiring: {
      type: 'object',
      properties: {
        agency_postings: { type: 'array', items: { type: 'string' }, description: 'jobs.py read: each posting that bears on the line (control number, title, office, opened date, file), or why none do' },
        vendor_postings: { type: 'array', items: { type: 'string' }, description: 'vendor_jobs.py read: each incumbent or competitor posting (company, title, what it names, posted date, file), or "not collected" with the record line that says so' },
        commands_run: { type: 'array', items: { type: 'string' } },
      },
      required: ['agency_postings', 'vendor_postings', 'commands_run'],
    },
  },
  required: [...LANE.required, 'anchors', 'hiring'],
}
const FINDINGS = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          section: { type: 'string' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          where: { type: 'string', description: 'verbatim excerpt of the brief to change, unique, at most 200 chars' },
          problem: { type: 'string' },
          fix: { type: 'string', description: "exact replacement text, or 'delete row', or an exact instruction" },
          evidence: { type: 'string', description: "file path or url plus the source's own words" },
        },
        required: ['id', 'section', 'severity', 'where', 'problem', 'fix', 'evidence'],
      },
    },
  },
  required: ['summary', 'findings'],
}
const APPLY = {
  type: 'object',
  properties: {
    applied: { type: 'array', items: { type: 'string' } },
    skipped: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, reason: { type: 'string' } }, required: ['id', 'reason'] } },
    checker_problems_left: { type: 'integer' },
    quote_failures_left: { type: 'integer' },
    identifiers_unresolved_left: { type: 'integer' },
    tally: { type: 'string' },
    lead_order: { type: 'string' },
    final_path: { type: 'string' },
  },
  required: ['applied', 'skipped', 'checker_problems_left', 'quote_failures_left', 'identifiers_unresolved_left', 'tally', 'lead_order', 'final_path'],
}

phase('Profile')
const profile = await agent(`${COMMON}

Build the company profile from: ${A.profile} (a file path, or company URLs).
If it is a path, save it with \`cat <path> | ${SAVE} profile/input.txt --source "<path>"\`. If URLs, save each page's raw text through the saver as profile/<slug>.txt with the page url as --source, and follow at most 4 more of the company's own pages that describe products, customers or contracts.
Then write ${OUT}/notes/profile.txt (your summary, not a source; profile rows cite the company's own pages): what the company sells and to whom, products by name, stated customers, contracts and programs, stated size status, clearances, facilities and NAICS, each line quoting the company's own words with its page. End with "Not stated:" listing what the pages do not say (size status, facility clearance, and the like).
Derive 6 to 12 search terms for the agency record (the capability words an agency would use, product names), the one main term that best names the company's line, and every spelling of the company's legal name for vendor lookups.`, {
  label: 'profile',
  schema: {
    type: 'object',
    properties: {
      profile_path: { type: 'string' },
      main_term: { type: 'string' },
      terms: { type: 'array', items: { type: 'string' } },
      name_spellings: { type: 'array', items: { type: 'string' } },
      capabilities: { type: 'string', description: 'one line for ask team / ask match' },
      not_stated: { type: 'array', items: { type: 'string' } },
    },
    required: ['profile_path', 'main_term', 'terms', 'name_spellings', 'capabilities', 'not_stated'],
  },
})
if (!profile) throw new Error('profile agent failed')
log(`profile: main term "${profile.main_term}", ${profile.terms.length} terms; not stated: ${profile.not_stated.join(', ')}`)
const P = `Profile: ${profile.profile_path}. Main term: "${profile.main_term}". Terms: ${profile.terms.join('; ')}. Name spellings: ${profile.name_spellings.join('; ')}. Capabilities line: "${profile.capabilities}".`

phase('Gather')
const LANES = [
  ['tools', TOOLS_LANE, `Tools lane: the record's read surface for this company's line, then the instruments on their primary pages.
0. Baseline from the report layer: ${REPORT} gather --agency ${A.agency} --spec agency-full --focus "${profile.main_term}" --out ${OUT}/sources/gather. Index what it wrote: ${SAVE} gather/bundle.json --file --source "report.py gather --agency ${A.agency} --spec agency-full". Read ${OUT}/sources/gather/evidence.md: the anchor offices with the rule report.py states, and each section's answer or boundary. bundle.json keeps the full text the checker reads.
1. ${NAVY} help (the record date). search and topics for every term. From the statements and notices of the company's line pick the candidate offices (the anchors plus up to 6 more) and the contracting office ids.
2. Each candidate office: office, people, neighbors, initiatives, wiki, page office, ${NAVY} report budget <office> (the budget lines placed there and the basis of each placement), ask changed --days 365, ask incumbents --within 730, ask prep (and ask prep --person and page person for the named program manager). The agency root: initiatives, ask changed --days 365 with the main term.
3. dna for each contracting office id. vendor for every company spelling and the top incumbents; ask moves for the top 4 incumbents; ask team --org <lead office> "<capabilities>"; ask match --profile <profile path> (read ask match -h).
4. Each open instrument (solicitation, BAA, CSO, shopping notice, open topic): trace notice, cell, sources <full uuid>, ask analogs. trace award for key awards, trace status, trace need when a forecast exists. revisions and protests for the main terms. pulse rank --top 25, pulse week --days 90, pulse actions <term>.
4b. Hiring (same python and AGENCY as navy.py): research/tools/jobs.py read <word> (USAJobs announcements, read as an office's intention to hire) and research/tools/vendor_jobs.py read <word> (incumbent contractors' own postings) for each candidate office and each main term. A posting is a hiring signal, never a buyer, an award or a requirement; a record that says it is not built is "not collected", never zero.
5. Primary check: for every open instrument read its SAM.gov record (response date, set-aside, contacts, intake rule); for every incumbent award read its USAspending record (period of performance end, amounts, recipient). Save under ${OUT}/sources/web/.
Save each command's output through the saver as tools/<cmd>_<arg>.txt, with the command as --source.
Write ${OUT}/notes/tools.md: the anchors and their rule; a candidates table (C1.., office, title, identifier, kind instrument/route/signal/historical/gated, status against TODAY with the date that decides it, gate facts, the file it rests on, proposed fit/awardability/access_urgency per the SPEC rubric with one-line reasons quoting the source); the budget each candidate office holds as report budget placed it; incumbents and end dates; people with sources (names and roles only); a Hiring section; tool defects (a 0 where search finds matches, a label from another notice, an empty route list, and the like).`],
  ['record', LANE, `Record lane: what the record holds, and the record's own files for this company's line.
- ${REPORT} inventory --agency ${A.agency} > ${OUT}/sources/record/inventory.json: every domain with present, rows and newest date; the domains that are absent are boundaries the brief states.
- events/budget_lines.json: the program elements carrying the line; amounts by book (field names differ by book); how many pages of each line the record holds.
- congress_events, remarks_events (speaker, role, date, url, evidence_span), oversight_events, fedreg_events, news_observations, protest_events, assistance_awards, hiring_observations and vendor_hiring_observations (when present).
- sbir_topics.json: open or closed against TODAY, and which fit (closed ones are Historical, never buyers).
- memory/small_business_offices.json, sources/source_status.json (families never collected: their empty result is "not collected").
Repo rows cite the record file itself by path (for example ${REC}/events/budget_lines.json); notes you write are never a source. Keep extracts with their exact text, ids, urls and record paths in ${OUT}/notes/record/<family>.md.
Write ${OUT}/notes/record.md: per family the rows for the brief (identifier, exact quote, status against TODAY, source url, class), the Sources table counts from the inventory, families not collected, gaps.`],
  ['web', LANE, `Web lane: what the record may not hold yet, from the buyer's own and public pages, read this run (at most 30 pages).
- Open notices: the SAM.gov site API (keyless) for this agency's active notices carrying each term; for each, response date, set-aside and office. Name every notice the record lacks (search the record with ${NAVY} search <notice number>).
- What leaders said: statements in the last twelve months by the agency's and the candidate offices' leaders on the agency's own site, congress.gov hearing pages and DVIDS: speaker, role, date, url and the exact passage.
- Congress and oversight: committee report language on the line where congress.gov or govinfo serve it; GAO and inspector general reports on the line.
- News: trade press in the last twelve months on the offices and the line (secondary class).
- Offices and people: the offices' own pages (mission, leadership by name and role) and the agency's small business office page. Names and roles only; no personal contacts.
Save raw text through the saver as web/<slug>.txt with the url as --source; record each refused or empty page with --failed.
Write ${OUT}/notes/web.md: rows (identifier, exact quote, status against TODAY, source url, class), the notices the record lacks, the statements by speaker and date, and the hosts that refused this machine.`],
  ['company', LANE, `Company lane: capability and risk signals for ${A.company}.
- Awards to the company: USAspending recipient and award search by every name spelling (PIIDs, agencies, periods of performance, amounts), SBIR/STTR awards (sbir.gov award pages), the company's own contract releases.
- Size status and eligibility: USAspending recipient business types, any public SAM.gov entity data; conflicts between sources are reported, not resolved.
- Risk and momentum (secondary unless the government published it): funding, leadership, litigation, export or security matters, program wins and losses, in the last two years.
Save raw text through the saver as web/company_<slug>.txt with the url as --source; record each refused or empty page with --failed.
Write ${OUT}/notes/company.md: rows (identifier, exact quote, status, source, class, provenance) and what stays unknown (size status, clearance) with how to close it.`],
]
const lanes = await parallel(LANES.map(([name, schema, task]) => () =>
  agent(`${COMMON}\n${P}\n\n${task}`, { label: `gather:${name}`, phase: 'Gather', schema })))
lanes.forEach((l, i) => {
  if (!l) throw new Error(`${LANES[i][0]} lane failed; a brief missing its evidence is incomplete`)
  log(`${LANES[i][0]}: ${l.files_saved} files, ${l.gaps.length} gaps, ${l.tool_defects.length} defects`)
})

phase('Draft')
const draft = await agent(`${COMMON}
${P}

Write ${OUT}/brief.md, titled "${A.company}: <agency full name> Startup Intelligence Brief", from ${OUT}/notes/*.md, ${OUT}/sources and the record. Facts come only from ${OUT}/sources and the record.
- Every section of the SPEC in order. A section with nothing says what was looked for and adds a Still open row. A domain the inventory marks absent is stated as a boundary (what was read, how many rows), never as zero.
- Rank the candidates in notes/tools.md, plus any open notice notes/web.md found that fits, by the published rule: re-check each score against the rubric and each status against TODAY, place each in its group, apply the tie-breaks, then write the Lead order line from rule 4. Publish the rule text of the SPEC, a worked example on this run's own rows, and "Why each score".
- Anchor offices: ${JSON.stringify(lanes[0].anchors)}. Say once, in Agencies and offices, which were chosen by the report layer's rule and which for the company's line.
- Budget lines behind the work: for each office in the line, the lines report budget placed there with the basis it gives for each placement, then the figures by book.
- What leaders have said and Oversight, Federal Register and news combine the record's events with the web lane's statements; each row keeps its own provenance.
- Hiring, as the tools lane returned it: ${JSON.stringify(lanes[0].hiring)}. A posting is a Live signal row (kind job posting), never a buyer.
- Fit line: plain and honest about what fits, what does not and what the agency does not buy.
- P0 fields use the SPEC header with the exact passage column; every evidence row carries the source's own words.
- People and program offices: the potential budget holders, decision makers and champions tables of the SPEC, each reading with its documented role, why it matters for this requirement, the rows it rests on beyond the title, and what is still to confirm. Nobody is a confirmed champion or the decision maker.
- Still open lists every failed fetch in ${OUT}/sources/index.jsonl as not collected, with its reason; no row rests on one.
- Outreach letters carry only facts in the rows, and decline explicitly what the brief does not carry. Run ${NAVY} check on each letter and paste its verdict.
- Sources, the first section, from the inventory and notes/record.md, plus every web address read and the profile pages. How this was produced lists the calls by command and the tool defects the lanes reported.
Then run: ${CHECK}
It exits 1 while anything is left. Fix every problem it raises. For each quote failure, cite the source that holds the words (found_in says where they are) or drop the row; for a source that names nothing saved, save it and cite the id the saver printed, or cite the record file. For each unresolved record identifier, write the identifier as the record prints it, or, when the row came from a page, set its provenance to web. Paste its tally line as the last Tally line in Audit. Re-run until the checker is clean.`, {
  label: 'draft',
  phase: 'Draft',
  schema: {
    type: 'object',
    properties: {
      brief_path: { type: 'string' },
      candidates: { type: 'integer' },
      lead_order: { type: 'string' },
      checker_problems: { type: 'integer' },
      quote_failures: { type: 'integer' },
      identifiers_unresolved: { type: 'integer' },
      tally: { type: 'string' },
    },
    required: ['brief_path', 'candidates', 'lead_order', 'checker_problems', 'quote_failures', 'identifiers_unresolved', 'tally'],
  },
})
if (!draft) throw new Error('draft failed')
log(`draft: ${draft.candidates} candidates; ${draft.lead_order}; checker ${draft.checker_problems} problems, ${draft.quote_failures} quote failures, ${draft.identifiers_unresolved} unresolved identifiers`)

phase('Validate')
const VBASE = `${COMMON}

Validate ${OUT}/brief.md adversarially: when in doubt, flag it. Do NOT edit the brief. Run the checker first: ${CHECK}
Each finding: "where" is copied verbatim from the brief (unique, at most 200 chars); "fix" is the exact replacement text, "delete row", or an exact instruction; "evidence" names the file or url and quotes the source. Report only defects, not praise.`
const LENSES = [
  ['evidence', `Lens: evidence and primary sources.
- Every P0 field and every row the Bottom line, Lead order and Do this week rest on: re-open the primary source (re-fetch SAM.gov, USAspending or the agency page when the saved copy is missing) and confirm the quote is exact and the date, amount, contact and set-aside are right.
- Class is honest (press and conference write-ups are secondary). Provenance is honest.
- "not in file" appears only where the primary source lacks the fact; "not collected" is used where the record never collected the family, never a zero.
- Investigate every checker quote failure and every unresolved record identifier: find the words or the record's spelling, or mark the row for deletion.
- People: names and roles with their source only; no personal contact; nobody called the decision-maker.
- Then sample 20 more rows across sections the same way.`],
  ['review', `Lens: the review bar and coverage.
- The Fit line is plain, specific and honest: it says what fits, what does not and what the agency does not buy, it matches the profile and the agency's own words, and it is not a bare yes.
- The realistic first dollar is not hidden: an open instrument with a real deadline within 30 days appears in Lead order and in Do this week.
- The ranking rule is published as the SPEC words it, with a worked example on this run's rows.
- Live, Historical and Adjacent are split correctly; closed topics are never buyers.
- The Sources section comes first and lists the inventory's domains, the web addresses and the profile pages; nothing claims one uploaded file; every absent domain is a stated boundary.
- P0 fields rest on primary sources. Every cell is stamped with identifier, status, source, class and provenance.
- Budget lines behind the work traces the money to the offices with the basis report budget gives; What leaders have said names speaker, role and date.
- Coverage: every command in the SPEC tool map ran (see ${OUT}/sources/tools and ${OUT}/sources/gather) and fed its section, or the section says why not; every record event family and every web lane finding appears in the brief or the Sources section.
- Style: banned words, em dashes, internal terms in prose, reader asides, forecasts. Tool defects seen in the sources are recorded in How this was produced.`],
  ['ranking', `Lens: ranking, status and dates.
- Recompute each candidate's three scores from the SPEC rubric using the rows it rests on. Check its group (instrument, route, signal, Historical, gated out), its tier, the tie-breaks and the rank numbering; lead, second and third follow rule 4.
- Check every Live status against TODAY using the deciding date in a source (response date, period of performance end, topic close).
- Every date in the Bottom line, Lead order, Do this week and Still open matches its row, and no future step sits in the past. Do this week steps are dated and feasible (for example, registration closes before the event).
- Budget figures match the book they cite.
- Search ${OUT}/sources/tools, ${OUT}/sources/web and the record for any open notice or topic in the company's line that the brief omits.`],
]
const verdicts = await parallel(LENSES.map(([k, lens]) => () =>
  agent(`${VBASE}\n\n${lens}`, { label: `validate:${k}`, phase: 'Validate', schema: FINDINGS })))
const all = []
verdicts.forEach((v, i) => {
  if (!v) throw new Error(`validator ${LENSES[i][0]} failed; an unvalidated brief is incomplete`)
  v.findings.forEach(f => all.push({ ...f, id: `${LENSES[i][0]}-${f.id}` }))
  log(`${LENSES[i][0]}: ${v.findings.length} findings (${v.findings.filter(f => f.severity === 'high').length} high)`)
})

phase('Apply')
const APPLY_BASE = `${COMMON}

Apply fixes to ${OUT}/brief.md. For each finding, open its evidence and confirm it; apply it if it holds, and skip it with a reason if it does not. When two findings conflict, the one with primary evidence wins. Keep the SPEC format and keep every other row as it is.
Then run ${CHECK}, fix what it raises (quote failures: find the exact words or drop the row; unresolved record identifiers: the record's spelling, or provenance web when the row came from a page), and paste its new tally line as the last Tally line in Audit.
Add a line to How this was produced: "Validation on ${A.today}: n findings from three validators (evidence, review bar, ranking and dates), n applied, n skipped", with the skip reasons.
Do not copy the brief anywhere: the gate releases it.`
let applied = await agent(`${APPLY_BASE}\n\nFindings (${all.length}):\n${JSON.stringify(all, null, 1)}`, { label: 'apply', phase: 'Apply', schema: APPLY })
if (applied && (applied.checker_problems_left > 0 || applied.quote_failures_left > 0 || applied.identifiers_unresolved_left > 0)) {
  log(`round 2: ${applied.checker_problems_left} checker problems, ${applied.quote_failures_left} quote failures, ${applied.identifiers_unresolved_left} unresolved identifiers left`)
  applied = await agent(`${APPLY_BASE}\n\nNo new findings. Clear what the checker still raises. Report what could not be cleared in skipped, and list it in Audit.`, { label: 'apply:round2', phase: 'Apply', schema: APPLY })
}

// The checker alone decides. The shell copies the brief only when the checker exits 0; the run fails otherwise.
phase('Gate')
const RELEASE = A.final ? ` && cp ${OUT}/brief.md ${A.final}` : ''
const gate = await agent(`Run exactly this one shell command from the repository root and nothing else; do not edit any file:
${CHECK} > ${OUT}/check.json; code=$?; [ $code -eq 0 ]${RELEASE}; echo "exit $code"
Then read ${OUT}/check.json and report: the exit code printed, the number of problems, quotes.not_found, the number of identifiers.unresolved, the number of gaps, and the tally.`, {
  label: 'gate',
  phase: 'Gate',
  schema: {
    type: 'object',
    properties: {
      exit_code: { type: 'integer' },
      problems: { type: 'integer' },
      quote_failures: { type: 'integer' },
      identifiers_unresolved: { type: 'integer' },
      gaps: { type: 'integer' },
      tally: { type: 'string' },
    },
    required: ['exit_code', 'problems', 'quote_failures', 'identifiers_unresolved', 'gaps', 'tally'],
  },
})
if (!gate) throw new Error('brief incomplete: the gate did not run')
if (gate.exit_code !== 0 || gate.problems || gate.quote_failures || gate.identifiers_unresolved) {
  throw new Error(`brief incomplete: ${gate.problems} problems, ${gate.quote_failures} quote failures, ${gate.identifiers_unresolved} unresolved identifiers left; see ${OUT}/check.json`)
}
log(`gate clean: ${gate.tally}; ${gate.gaps} collection gaps listed in Still open`)

return {
  final: A.final || `${OUT}/brief.md`,
  gate,
  profile: { main_term: profile.main_term, terms: profile.terms, not_stated: profile.not_stated },
  lanes: lanes.map((l, i) => l && { lane: LANES[i][0], files: l.files_saved, gaps: l.gaps, defects: l.tool_defects }),
  draft,
  validators: verdicts.map((v, i) => v && { lens: LENSES[i][0], summary: v.summary, count: v.findings.length }),
  apply: applied,
}

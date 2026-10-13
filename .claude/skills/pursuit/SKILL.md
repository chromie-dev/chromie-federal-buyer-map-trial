---
name: pursuit
description: >
  For a vendor pursuing a topic at one federal agency, answer one of two questions from production's record, the same
  way every run: where the money for the topic is, from the budget request to the account's unobligated balance, and who
  potentially holds it (budget, the money trail), or who could back the pursuit with a support letter, a customer memo
  or an introduction, each with a route to the money (champions). The record is production's read-only `agent` views
  through the Supabase MCP server; a web pass follows, labelled apart. Use when the user says "budget analysis for
  <topic> at <agency>", "where is the money for <topic>", "who holds the budget for <topic>", "money trail", "what is
  still available", "who can pay for <topic>", "who could champion <topic>", "letter of support", "customer memo",
  "potential champions at <agency>", or names a federal agency with a pursuit, budget or champion.
user-invokable: true
argument-hint: "<agency> <topic> budget|champions [--office OFFICE] [--no-web]"
---

# Pursuit: the money, or the people who could back it

The record is production, read through the Supabase MCP server in read-only mode (`execute_sql`, SELECT only), and
only from the `agent` schema: never the raw tables, never a write. What the views do not hold is stated as not held,
with what was read. After the record, a web pass (section 5) adds what the record cannot hold fresh, kept apart from
the record's facts. The one other source is the public USAspending API through `usaspending.py` (awards, accounts,
recompetes), cited with its `as_of`; no other database or API. Write to a working folder
`pursuit/<agency>-<budget|champions>-<topic slug>/` (DIR below).

Connect once per machine, then log in with `/mcp` using a Supabase account that has the project:

```
claude mcp add --transport http supabase "https://mcp.supabase.com/mcp?read_only=true&project_ref=bxzxoixtutqpmqjkjvbh"
```

If `execute_sql` is not available, stop and say the record is not connected; never answer from the web alone.

The views carry every loaded agency. Filter on `agency` with the name exactly as `agent.record_holds` prints it
(`Department of the Navy`, not `%navy%`).

| View | What it holds |
|---|---|
| `agent.record_holds` | The sections held per agency, their rows and newest date |
| `agent.offices` | Every office with its parent, type and `office_path` (its ancestry) |
| `agent.items` | Every live dated statement: notices, forecast rows, awards, budget, remarks, oversight, people |
| `agent.needs` | Requirements (forecast rows, notice groups) with owner and contracting offices, dates, estimates |
| `agent.budget_lines` | One row per budget line per book column, in $M as the book prints it, with `placed_office` |
| `agent.budget_statements` | Congressional directives, funding changes quoted from remarks, summary figures |
| `agent.people_readings` | Potential readings on people, with basis, post, title as written and date observed |
| `agent.evidence` | The excerpt and URL each statement rests on, by `item_id` |

Columns (a guessed name fails the query):

```
record_holds      agency, section, rows, newest, department
offices           office_id, agency, department, office, acronym, org_type, parent_office, office_path, active
items             item_id, agency, section, event_type, title, body, as_of, published_at, source_url, sha256, retrieved_at,
                  source_provider, source_tier, organization, department, office_path, source_pages, facts_as_written
needs             need_id, agency, title, description, lifecycle, source, source_key, owner_offices, contracting_offices,
                  as_of, release, expected_from, expected_to, source_url, item_id, estimate_as_written, office_paths, department
budget_lines      item_id, agency, line, title, appropriation, budget_activity, book_column, fiscal_year, amount_musd, measure,
                  placed_office, source_url, sha256, retrieved_at, book_date, department, office_path
budget_statements item_id, agency, kind, title, body, figures_as_written, as_of, published_at, source_url, retrieved_at,
                  source_provider, placed_office, fiscal_year, source_pages, department, office_path
people_readings   item_id, agency, person, bucket, reading, basis, role_as_written, office_key, post_status, post_source,
                  source_url, observed_at, evidence_rows, title_as_written, said, person_id, office, office_path,
                  department, email, phone, contact_basis
evidence          item_id, section, title, source_url, excerpt, published_at, provider, pages, agency, department, office_path
```

## 1. What the record holds, first

Query `agent.record_holds` for the agency. Tell the user the record's newest dates and what is absent before
anything else. A missing section is "not in this record", never zero: an agency with no budget lines means the budget
answer rests on statements, directives, forecasts and awards; an agency with no people beyond contracting staff means
champions come from offices and notice contacts only. `record_holds` counts rows, not coverage: read a few rows
before calling a section covered.

Every "not held" line says what was read for that item and what it shows: a blank column, a printed "--", a book
column that is a continuing resolution level, a section not loaded for this agency, a notice held as a title only, or
a figure not yet published. Name the web page that fills it if section 5 found one. A bare "not in the record" is not
an answer: the 2026-10-12 benchmark failed 8 of 10 answers on it.

## 2. The topic and its anchor offices

Search `agent.items` (`title`, `body`) and `agent.needs` (`title`, `description`) for the topic. The offices whose
statements and requirements carry the topic, contracting offices aside, are the anchors: an inference, so state the
rule. If the user named an office, add it as an anchor and say so. A user usually names only the product: derive
the search words from what it does (the problem it solves, the words a buyer writes in a notice, several synonyms),
let the matches name the offices, programs and missions, and say which words found each.

## 3. Budget: the money trail

Follow each line or program through six links, one row per link, each marked as stated by a source or inferred by a
named rule. Every money claim carries its line or program identifier, fiscal year, amount and the measure exactly as
labelled (request, enacted, actual, forecast estimate).

1. Requested: the line in `agent.budget_lines`, its `book_column` and `measure` as written, by fiscal year.
2. Congress: `agent.budget_statements` on that line, or on its PPA, program element, appropriation or account, and
   `agent.items` with `event_type` in ('congressional_directive', 'funding_change'): most directives sit there under
   section `mission_priorities`, outside `budget_statements`. Marks, directives and funding changes, `body` or
   `figures_as_written` quoted with units.
3. Obligated: the book's prior-year actual column for the line where the book prints one, and the awards under the
   line's office that are still running (`agent.items` section `vendors_incumbents`; the title carries the end date):
   vendor, award, end date, from `facts_as_written` (`piid`, `vendor`, `expires_on`; a grant's `amount`). Contract rows
   hold no dollar amount: run `python3 .claude/skills/pursuit/usaspending.py award <piid> [<piid> ...]` (public
   USAspending API, no key) for obligated to date, ceiling (base and all options), dates, awarding and funding office
   and the award page URL, and cite it as USAspending with the day you ran it. Who won similar buys before, and
   grants: `usaspending.py search` with several `--keywords` (one phrase misses most winners; `--kind grants
   --listings <number>` for a grant program), cited the same way; its first line says if the list is truncated. Run it
   with `--role funding` too: another agency often buys for the office (CISA's CDM orders are awarded by GSA FAS).
   An award counts as obligated on the line only when `usaspending.py funding <piid>` shows it paid from the line's
   account (and program activity, where step 5 matched one) in the line's fiscal year. Paid from another account, it
   is named with who paid and left out of the line's sum: CISA's CDM order is paid from GSA's Acquisition Services
   Fund (an assisted buy), not from CISA's procurement account.
   Every `usaspending.py` command's first line carries `as_of` (the data date to cite) and `name_corrected` when it
   had to respell an agency name; recompetes, winners and monthly take search's filters (`--agency`, `--role`...).
   - Recompetes and incumbents: `recompetes --subagency <name> --role funding [--months 18]`: contracts whose current
     or potential end is in the window, with incumbent, vehicle, ceiling, competition, offers and set-aside.
     A keyword filter misses awards whose description lacks the word: for a topic, filter by office or funding
     agency and read the descriptions, or run it once per synonym.
   - The competitive field: `winners <filters> --from <date> --to <date>`: recipients by obligations in the period,
     not contract values.
   - Subcontractors (teaming routes): `subawards <piid>`: sub-recipients by amount, as the prime reported them.
   - Vehicle orders: `orders <IDV piid>` (an award's `parent`): holder, ceiling, orders by funding agency.
   - Change history: `history <piid>`: each modification, option exercise and funding action, summing to obligated.
   - Competitor: `recipient <piid or recipient_id> --fy <FY>`: parent, subsidiaries, payers, NAICS; never by name.
4. Solicited: the open notices and forecast lines the line's office is buying (`agent.needs`), dates and estimates
   as written; a notice's response date is `facts_as_written ->> 'responses_due'` on its `agent.items` row.
5. Unobligated balance, in GAO's budget terms (GAO-05-734SP): budgetary resources, obligations, unobligated
   balance; only unexpired money can take new obligations. These are account figures, not the views': find the line's
   account with `usaspending.py budget <Treasury code: 070 DHS, 057 Air Force> --fy <FY> --keyword <bureau or
   appropriation words>` (the book's appropriation and bureau must both match the account title; two candidates, the
   link stops), then `budget <account> --fy <FY>`: resources, obligated, unobligated, the unexpired part, and what each
   program activity obligated in the year, through the period its `as_of` names. A program activity stands for the
   line only when its name is the line's PPA as the book prints it; then its obligations sit beside the line's amount.
   A line below the PPA (an investment, a program element) has no published obligations: say so. Never subtract
   solicitations (a notice names no account; list them beside, as solicited at the line's office), never subtract an
   account's or activity's obligations from a smaller line, and never call a remainder available: the words are
   "unobligated balance in <account>, unexpired, through <as_of>". When the money moves, for NEXT MOVE's date:
   `monthly <filters> --fy <years>` (obligations by fiscal month, September's share). Say which fiscal year's money
   it is and that multi-year money carries over. Otherwise the link stops, with why.
6. Who holds it: `placed_office`, on the basis the record states, and that office's people (`agent.people_readings`).
   Its listed head is a *potential* budget holder only as an inference resting on two claims (the role, and money
   placed in that office). An empty `placed_office`, or the agency itself, means the line sits at agency level: say
   "not placed in an office" and name no holder from the record. The web pass then looks for the office that runs
   the program (the agency's program page, the budget book's narrative) and names it under WEB as an inference. A
   contracting office holds no placed budget: it buys for others. People along the line: `usaspending.py paid
   <account> --fy <FY> [--activity <the PPA's program activity>]` lists the awards that account or activity paid,
   with their funding office (the requirement side) and awarding office (the contracting route); those offices'
   people in `agent.people_readings` link to the line through those awards, each link named.

A link the record does not reach says what was read and why it stops (no placement, an account shared by many
programs, a request not yet enacted), never zero.

## 4. Champions, each with a route to the money

A candidate needs a documented role quoted as written, plus at least one separate fact tying the person or office to
this topic's problem or money (a forecast row or notice naming them, a line placed in their office, a remark on the
topic). Rank, the same every run: owns the problem and the money; owns the problem; end user liaison; technical;
potential decision maker. Contracting staff are a route, never an advocate. A post confirmed current ranks above one
last seen on a date. Every reading is "potential", rests on two claims or more, and says what is still to confirm;
"willing to advocate" is "none found" unless a source says so. An office with no named person stays as an office row.

A champion with no purchasing power, and no line to someone who has it, moves nothing. For every champion give the
money route: walk `agent.offices` (`parent_office`, `office_path`) from the champion's office up to the office that
holds money for this topic (link 6 of the money trail), or say "none found" with what was read. Where no line is
placed in an office (most civilian agencies), the route is the channel the money moves through: an open solicitation,
BAA or other transaction call, a grant program or assistance listing, or a forecast row's requirement owner, named as
the record or the web states it. Never stop at one
champion: give candidates from several angles (end users, technical, program management, liaison) and, beside them,
the direct route to the potential budget holder, so the vendor can go through a champion or straight to the money. A
champion with a money route ranks above one without.

## 5. The web, after the record (on unless `--no-web`)

Search the web for what a "not held" line names, then for what the record cannot hold fresh: amendments, award size
and page limits on the topic or notice page, the sponsor it names, obligations to date on the line's account, who
holds a post today, the office's small business specialist, news within the month. A benchmark on 2026-10-12 (the
same questions, Claude with web search alone) found those on the web and not in the record, while only the record
held forecast fields, contracting officers, award history and budget line moves. Notice text in the views is often a
title only, and SBIR topic authors are absent (deadlines sit in `facts_as_written`): take them from the topic or notice page. A DoD SBIR or STTR topic's TPOC names, e-mails and phones are on its
DSIP listing (dodsbirsttr.mil); aggregators such as HigherGov drop the addresses.

- Web findings never go under FACTS: they go under WEB, one row each with what it adds, the exact quote, the URL and
  the date read.
- Quote only page text you opened. A search snippet is the tool's wording, labelled so, and confirms nothing. A page
  that would not open is "not collected".
- Prefer the agency's own pages, the solicitation portal and congress.gov over news and consultant pages, and say
  which kind each source is. LinkedIn is self-stated: use it only when name and office match the record.
- When the web and the record disagree, show both with their dates; never overwrite the record.
- A potential role reading still needs two claims; a web page can be one of them, labelled as web.

## 6. Write the answer

```
FACTS:
- <fact> (agent.VIEW item_id=ID: "<exact quote>")
WEB:
- <finding> (<URL>, read <date>: "<exact quote>")
INFERENCE: <reading> (rule: <rule>; rests on: <item ids or URLs>)
NEXT MOVE: first step <verify fit | verify authority | pitch>: <who> (<email or phone, with contact_basis>), on <the specific line, notice or need>.
  Fit: <why this buy fits the product, in the notice's or need's own words; or "unconfirmed: ask <what>">
  Authority: <why this person can help: role as written plus the fact tying them to this money; or "unconfirmed: ask <what>">
  Path to payment: <the vehicle, contract, open solicitation, line, SBIR or OT call the money moves through, and whether the vendor can use it>
  Timing: <deadline: a date a source sets, cited> or <suggested by <date> (<why>)>
```

The next move follows from the evidence above it: fit, authority and path to payment each name their fact. When fit
or authority rests on an inference, the first step is verifying it (does the buy cover the product, who owns the
money), not a pitch; plausible leads stay, labelled as leads. A ceiling is the most a contract or vehicle may
obligate, not money anyone holds: never present it as funding, room or budget. A deadline is a date a source sets (a
notice's `responses_due`, a forecast date as written, a contract's end); a date chosen for outreach is "suggested by",
with its reason.

For budget, add the money trail per line as a table (requested, Congress, obligated on the line's account or activity,
solicited at the office, unobligated balance in the account, who potentially holds it), each link stated or inferred. For champions, a table: candidate, role as written, the two
claims, money route, what is still to confirm. Save each query and its full result under `DIR/sql/NN-view.json`; a
quote is copied exactly from a saved result, its identifier the record id, line or person as the row writes it. Cite
the excerpt and URL from `agent.evidence` for the same `item_id`.

## Usage rules

Each one cost a wrong "not found" in the 2026-10-10 eval run.

- Forecasts: query `agent.needs` (`source like '%datapack'`), not `agent.items` section `forecast` (locator-only
  rows for most civilian agencies). `as_of`, `release`, `estimate_as_written` and `source_url` date and cite a need.
  A forecast need's own fields (contracting center, solicitation and award dates, vehicle, value, incumbent and its
  contract number, requirement contacts) are `facts_as_written -> 'stated'` on the `agent.items` row of its `item_id`.
- Sub-agencies (CDC, NIH, FDA, FAA, NESDIS, CISA, PEOs): filter the parent `agency` (or `department`) and match the
  acronym in `office_path` (`needs.office_paths`), e.g. `office_path ~ '\mCDC\M'`. `agency = 'Centers for Disease
  Control and Prevention'` finds almost nothing. `agent.offices` lists the offices under an agency.
- Offices are stored as acronyms and codes (NESDIS, 75N9xx for NIH contracting): on `agent.needs` search
  `owner_offices`, `contracting_offices` and `office_paths` (`office_path` on the other views), not only the full name.
- Short acronyms: case-sensitive word boundary (`~ '\mALS\M'`), then confirm with the full name; `ilike '%ALS%'`
  hits 'intervALS' (and EED, SCaN, LIDS hit ordinary words the same way). Old program names do not map to new ones (IVAS is now SBMC): search both.
- Book columns: a book's current-year column can be a continuing resolution level, not enacted (DHS books,
  CISA's among them, print "FY 2026 Annualized CR"; NASA's prints "Enacted", P.L. 119-74): quote `book_column` as
  written and never call it enacted unless it says so.
- Budget lines: never sum across `measure`s (a program block repeats its book line); filter
  `measure ilike '%book line'` and name the book (PB2026 and PB2027 print the same year differently). A null
  `amount_musd` is a column with no figure in the record: say so, never zero. A program block's `source_url` is the
  program listing; the book's citation is in `agent.evidence` for the same `item_id`.
- Directives: search `body` as well as `title` (headings can be 'Briefing'); the year is `fiscal_year`
  (or `figures_as_written->>'fiscal_year'`).
- A null `source_url` means no public URL in the record: say so; never cite a `local://` path.
- People: `reading` already says "potential"; "leader by title" is a title only, never a decision maker or budget
  holder. Quote `title_as_written` (the source's words), not `role_as_written` (a role code). `post_status` and
  `observed_at` answer "is this person current": take the newest `observed_at` across all of the person's rows, since
  one person's posts can sit under several office keys (a center and its contracting office). Group one person by
  `person_id`; a name can still appear as two people ("GRIFFIN, JIM" and "James Griffin"): say so, never merge them
  yourself. A hearing witness whose title names another employer (GAO, MITRE, VFW) is not agency staff. Contracting
  staff are a route, never an advocate, and a forecast contact who also appears as contracting staff is a route too,
  whatever the reading says.
- Contacts: `email` and `phone` in `agent.people_readings` are how to reach the person; give them in NEXT MOVE.
  `contact_basis` says where they come from: "our contact record", or "same name at the same agency, from <source>",
  which is a match, not a stated link: say so beside the address.
- Views are slow: one statement per call, filter `agency` first, aggregate in SQL, no unions of many view subqueries.

## Rules

- The record is production, read-only, through the `agent` views. The web adds to it under WEB, never replaces it.
  The public USAspending API through `usaspending.py` is the one other source; no other database or API.
- Facts, inferences and recommendations stay apart. Never infer authority, a decision or advocacy from a title alone.
- Nothing is zero that was not read. Name offices as the record writes them. No likely, probably, imminent, soon.

## Hand back

The answer file in DIR, the anchor offices with their basis, what the record does not hold, what the web added, and
the main rows: for
budget, the money trail per line; for champions, the ranked candidates with their money routes. Do not paste the
whole answer.

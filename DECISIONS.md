# Decisions

Add dated technical and product decisions here.


## 2026-09-16 - Evidence rules

Official public sources only. A Wayback Machine capture of an official page is a dated copy of that page, and its
capture timestamp is the "available by" date. Third-party mirrors are pointers, never evidence.

## 2026-09-16 - "PAE" is a reorganization, not a data system

PAE means Portfolio Acquisition Executive: the Department of the Navy stood up PAE Mission Systems on 2026-05-11,
consolidating mission-systems elements of PEO C4I, PEO Digital, PEO IWS, PEO MLB, three DRPMs, Minotaur, NAVWAR,
NAVSEA, NAVAIR and MCSC. The organization map models it as a dated reorganization.

## 2026-09-16 - The Navy LRAE is the primary forecast source and the anchor for attribution

The Long-Range Acquisition Estimate carries the associated program office, the contracting UIC, the existing
contract and the incumbent on one row, and its PIDs embed the office code. Every row is recorded as an estimate;
a forecast never counts as ownership evidence on its own.

## 2026-09-16 - Conflicting official statements are kept side by side

When two official sources place an office differently, the organization memory keeps both claims with their dates
and flags the pair for review rather than choosing one.

## 2026-09-16 - Registry records use the source-table vocabulary and four statuses

`research/sources/source_registry.json` uses the `gov_procurement_sources` field names and records each source as `verified`,
`blocked`, `restricted` or `not_inspected`. The Wayback Machine is a retrieval path, not a source of record.

## 2026-09-16 - DoD contract announcements are monitored through the RSS feed

The ArticleCS RSS endpoint is the trigger; the article body is fetched through the hosted browser.

## 2026-09-16 - Attribution evidence classes and the modification rule

Every attribution carries one of four classes: directly_documented, inferred, ambiguous or unresolved.
Modifications are attributed through their base award, and a description naming several offices keeps every one.

## 2026-09-16 - Alerts carry the evidence class and the ancestry as of the event date

An alert names what changed, the office and its parent chain as of the event date, the documents with dates, the
uncertainty and why it matters. A forecast alert says it is a forecast; a budget alert says the office link is an
inference by program name.

## 2026-09-16 - SAM.gov notices come from the keyless site API

The JSON endpoints behind the SAM.gov web application answer without a key, return full description text and
attachments, and reach archived notices; the documented Opportunities API is not used.

## 2026-09-16 - Navy hosts are fetched through a hosted browser with a United States address

Navy hosts refuse non-United States addresses. Live pages are fetched through a hosted browser with United States
egress and recorded with method "browserbase".

## 2026-09-16 - The PAE publishes portfolios, not offices

The PAE Mission Systems site names capability portfolios and no program offices. The organization map keeps the
PEO C4I parentage with the dated consolidation edge and records an office-to-portfolio mapping only as a candidate.

## 2026-09-17 - Organization memory is a dated graph that holds PEO and PAE structures together

Offices are nodes with dated relationships and an alias table fed by a registry of code families
(`research/memory/org_code_families.json`). A record resolves to the ancestry valid on its own date, and a consolidation is a dated
edge that never overwrites earlier history. Specification in `research/docs/08_org_memory_format.md`.

## 2026-09-17 - Candidates over silence for offices and contacts

Where ownership is ambiguous, a weak attribution lists ranked candidate offices and public contacts with a
confidence label; alerts surface uncertainty rather than suppress it.

## 2026-09-18 - A package regenerates from saved bytes

The source hash is the contract: the build reads only bytes recorded in the manifest, every row gets one decision
with a reason, every join is labelled explicit or inferred, and output hashes are recorded so a second run proves
idempotence. Network lookups are a separate `collect` step.

## 2026-09-18 - Observations, relationships and interpretations are stored apart; corrections are retractions

What a source states, the dated claims resting on it and our own readings are stored separately. Effective dates
exist only when a source gives them. A wrong claim is retracted with a reason and a pointer to its replacement;
only a documented real-world change gets an end date. Contacts follow the same split between observations and
recommendations.

## 2026-09-18 - Every LRAE release is its own package; diffs never merge rows

Each release is packaged from its own bytes, and a diff between releases is written into the newer package. A weak
match is reported as such rather than forced.

## 2026-09-20 - An office's former parents stay in the relationship table

Every ended or superseded parent loads with its dates; only the one live claim fills the parent column.

## 2026-09-20 - Following a requirement across releases is staged, and a weak match is a candidate

Releases are paired by PID, then exact title under the same office, then incumbent contract under the same office,
each stage claiming a pair only when it is one to one. Title similarity yields `candidate`, never `match`, and
every diff row carries `key_method`, `confidence` and `reason`.

## 2026-09-20 - An alert names every office still claimed

The office comes from the newest observation nothing has superseded. Where two releases name different offices, the
alert says the owner is contested and lists both with their dates.

## 2026-09-20 - A spreadsheet row is a source record

Nothing is marked duplicate at import. A row without a PID is keyed by its release and row number, and a PID that
repeats within one release stops the build rather than merging.

## 2026-09-20 - A notice or an award reaches a forecast line explicitly or as a labelled candidate

Explicit: the notice carries the line's PID or incumbent contract, or the line's title carries the solicitation
number. Candidate: a shared program name or code under a compatible office within three fiscal years. A candidate
is never read as solicited or awarded, and estimates, ceilings and obligations are never added together.

## 2026-09-21 - A local database is built from the loaded-table schema subset

`schema_subset.py` emits the tables the loader fills with their constraints, indexes and triggers, so a row
production rejects is rejected locally. Owners, grants, row-level security and production identities are not
emitted.

## 2026-09-21 - A negative is scoped and a connection is sourced

A negative names the records searched and their retrieval date. Every connection prints its source record. Two
titles stating different lots or generations are related procurements, never candidates for the same requirement.
A successor edge applies to the organization it is documented against.

## 2026-09-21 - Confirmed pairs load as one requirement, and a reading opens with its outcome

Rows tied across releases load under one key, with an `inferred` basis where the tie does not carry the PID. The
same office and incumbent make a candidate, not a match. Every reading opens with one outcome word: awarded,
solicited, cancelled, review, restructured, delayed, open, not yet due or not dated.

## 2026-09-21 - A notice is an entry point

Every notice is a signal on a requirement keyed by its solicitation number. It resolves to a forecast line only
when exactly one line of the latest release names it explicitly; a shared program name stays a candidate. A notice
posted by an organization the memory lacks and naming no office it knows is not this agency's need.

## 2026-09-21 - News is a signal of its own kind

An article is an observation: each claim quotes the sentence it rests on and is read against the stored record as a
new signal, a corroboration or a conflict. Nothing an article says is promoted into the organization memory.
Specification in `research/docs/13_news_as_a_signal.md`.

## 2026-09-21 - One pipeline builds the agency from nothing

`research/tools/pipeline.py` runs every stage in the order the records depend on each other. Collection stages touch
the network and run only with `--collect`; every other stage reads saved bytes, so a rebuild repeats byte for byte,
and the database is built from nothing on every run. Nothing in the pipeline holds agency knowledge.

## 2026-09-24 - Pages a host refuses are rendered through context.dev

gao.gov and the navy.mil hosts refuse this address. Their pages are rendered through context.dev from a United
States residential address and recorded with method `context_dev`; files keep the hosted browser, which saves the
bytes as served.

## 2026-09-24 - A meeting names the route in, not only the people

Every meeting action carries the office's routes from the contact recommendations: the program manager on the
requirement side, the contracting points of contact on the acquisition side, and the published channels, each
dated by the observations it rests on and marked when the review log shows them checked against the saved file.

## 2026-09-24 - The latest notice or award sets where a requirement stands

A requirement's awards are read from the lookup by its solicitation number and from the contracting office's base
awards, which carry the orders a multiple award solicitation produces; a base award under the number makes it
fulfilled. A forecast line moves from identified only on a tied notice or award dated after its latest release,
recorded as a step in its lifecycle history.

## 2026-09-24 - A model reads where a rule used to decide, and a rule checks the reading

Awards, SBIR topics and committee statements are read to a program office the way notices are, and the views use a
kind only once its masked check holds; a special notice's kind and an LRAE pair a title or an incumbent links are read
by the model too. Each reading quotes the record verbatim,
replays from its recorded call, and never moves a record from the office that filed it. The outreach agent walks from a
capability to a letter the same way: the model chooses, the rules only refuse.

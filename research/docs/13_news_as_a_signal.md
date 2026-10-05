# 13 - News as a signal: articles as dated observations

Implemented in `research/tools/news.py`, loaded by
`agency_layers_sql.emit_news`, read by `trace.py need`. Companion to
`research/docs/08_org_memory_format.md`, which this follows rather than extends.

## What an article is

An agency press release, a contract announcement, a program office update and a trade report are
source statements, the same kind of thing as a notice or a forecast row. So an article enters the
record the same way: the bytes are fetched and recorded in `research/sources/documents_manifest.jsonl`
with their retrieval date and SHA-256, and the model is built from those bytes, never from a
summary of them.

An article is an observation. It is never a relationship. Nothing it says changes the alias
table, the ancestry walk or a need's office; a claim is promoted only with the usual evidence, or
it stays where it is.

## The record

`python research/tools/news.py build` writes `research/events/news_observations.json`, one entry per
article:

| field | what it holds |
| --- | --- |
| `headline`, `publisher`, `url`, `published`, `author`, `retrieved_at` | what the page states about itself; a date is read or absent, never inferred |
| `record` | the saved path, the SHA-256, the retrieval method |
| `source_type` | official announcement, direct interview, trade reporting, secondary reporting |
| `reliability` | high, medium or low, from the source type |
| `entities` | organizations (through the memory's alias table), people, programs, contracts, solicitations, forecast lines, budget line items, places |
| `links` | the offices, forecast requirements, solicitations, awards and budget lines the record already holds |
| `claims` | one per sentence that names something known, each with its exact passage |
| `relation` | new signal, corroborates or conflicts, for the article as a whole |
| `verify` | what has to be confirmed |

Each claim carries its own `statement_type` (the memory's vocabulary, widened for what news
carries: milestone, delay, protest, funding, recompete, industry engagement, acquisition
strategy, performance), the passage it rests on, the entities it names, its relation to the
stored record with the reason, a confidence and its own list of what to verify.

## How a claim is read against the record

- A parentage claim is compared with the stored parents of the offices it names. The same parent
  corroborates; a different stored parent conflicts, and the conflict is reported, not resolved;
  an office with no stored parent is a new signal.
- A leadership claim is compared the same way with who the memory has leading the office.
- Any claim naming a contract, a solicitation number or a forecast line already in the record
  corroborates that record.
- Everything else is a new signal, which means untested, not true.

Confidence starts from the source type and falls where the passage hedges ("expects to",
"could"), or where a reorganization, rename or leadership change is stated without a date. Trade
and secondary reporting always ask for a second source. A statement of intent is never read as an
action taken.

## Collection

- `news.py watch` polls the feeds and index pages in `FEEDS` and lists the items not already in
  the manifest. A general feed carries the whole department, so an item is listed when its
  headline names something the memory knows or a standing term of this agency; the rest are
  counted, the way a notice posted by another command is. `--fetch` retrieves the listed items.
- `news.py search "QUERY"` is discovery by query and needs a key: `EXA_API_KEY` for Exa, or
  `ROUTERGROWTH_API_KEY` for RouterGrowth, which routes one query to whichever provider is live
  behind a capability. The provider is a flag; the record is the same either way. The response is
  saved and the search is recorded, so a statement that nothing was found names the search it
  rests on.
- `news.py sweep` runs one query per office in the organization seed, which is the memory read
  back out rather than a hand-kept list, and retrieves what is not already saved. A result from a
  site that republishes contracting notices, or from a social network, is not taken: the notices
  themselves are read first-hand elsewhere in this repository.
- A page a news search returned is read as an article whatever the host. The publisher table
  decides how the source is rated, not whether it is read, because a host list kept by hand is
  the part that would not travel to another agency.
- Nothing else in this document needs a key.
- Where a `.mil` host refuses the request, some by answering 200 with an empty body, the page is
  taken again through `research/tools/context_fetch.py` (context.dev, a United States address).

## What is a claim, and what is background

A sentence becomes a claim when it states something: one of the statement types above, or a
mention of a contract, solicitation, forecast line, budget line item or person. A sentence that
only carries an office name is counted as background and recorded as a number on the article, so
an article contributes what it asserts rather than one claim per paragraph.

Three readings are separated because they read alike and mean different things:

- A preposition is not a place in an org chart. A parentage claim is made only when the phrase is
  followed by an office the memory knows, so "under the theme of" and "as part of the
  reorganization" are not parentage.
- A denial is not a claim of the thing it is denying. A sentence stating that no change was
  announced is recorded as that, and it corroborates the memory rather than conflicting with it.
- A person the memory does not hold is still a person the article names. A rank, an honorific or
  a role in front of a name, or one of the verbs a newsroom uses after it, is the whole rule.

## What is loaded

`agency_layers_sql.emit_news` writes one brain item per article, in the section its dominant
statement type belongs to (people, budget, vendors and incumbents, industry engagement,
procurement patterns, mission priorities), with the article's links in the item's source record,
and one evidence row per claim carrying the passage. No assertion, no relationship and no
lifecycle change is loaded from an article, including a conflicting one: the conflict is the
finding, and it is reported, not resolved.

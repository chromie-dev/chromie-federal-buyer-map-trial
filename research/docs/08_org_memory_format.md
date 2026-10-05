# 08 - Organization memory: observations, relationships, interpretations, corrections

Implemented in `research/memory/organization_seed.json` (the data) and `research/memory/org_code_families.json` (how a parser
recognizes office codes). This document is the contract for both.

## In plain terms

There is no reliable org chart for the Navy's acquisition offices. The old structure (PEOs with
PMW program offices under NAVWAR) is what every contract, forecast and notice still cites; the new
one (Portfolio Acquisition Executives, since 2026-05-11) is only partly published. Nobody inside
or outside the Navy has the full picture, so the memory does not pretend to be a chart. It keeps
three things apart:

1. **What a source said** (an observation): the page or document, the date it was observed, the
   exact words, and which organizations or people it was about.
2. **What we claim was true, and when** (a relationship): "PMW 160 was a child of PEO C4I",
   resting on one or more observations, with dates only when a source gives them, and a plain
   statement of whether the claim was last confirmed, ended, superseded or is in conflict.
3. **What we read into the sources** (an interpretation): candidate mappings and readings that no
   single source states, kept separate with a confidence label.

When a claim turns out wrong, it is retracted with a reason and a pointer to what replaces it.
Nothing is deleted, and a wrong claim never gets an end date that would make it look as if it
had once been true.

## 1. Nodes

One node per organization or person.

| Field | Meaning |
| --- | --- |
| `id` | stable local id (`pmw:160`, `peo:c4i`, `pae:mission-systems`, `contracting:n00039`, `person:nigro`) |
| `type` | `agency`, `command`, `acquisition_portfolio` (PAE), `program_executive_office`, `program_office`, `technical_center`, `contracting_office`, `direct_reporting_program_manager`, `department` (a named directorate, department or division: SEA 21C, ONR Code 34, NRL Code 7600), `field_activity` (a shipyard, regional maintenance center, SUPSHIP or logistics center named in a contracting column), `person` |
| `name` | current name |
| `aliases` | every observed spelling and code, each as `{text, observation_ids}` so the source of the wording is kept |
| `codes` | structured identifiers: office code (`PMW 160`), contracting UIC (`N00039`), FPDS agency id |
| `valid_from`, `valid_to` | when the organization existed under this identity, only when a source states it |
| `name_history` | earlier names with their own date ranges (SPAWAR to NAVWAR, SSC Pacific to NIWC Pacific) |
| `observation_ids` | the observations that mention the node |
| `notes`, `review_status`, `reviewed_by` | free text; `draft` until a human reviews it; `reviewed_by` names who did, or is null |
| `generator` | present on records a tool wrote from a source (`org_memory_lrae`: the NAVSEA, ONR and NRL LRAE office and contracting columns); a rebuild replaces every record carrying the tag and touches nothing else. Names are the sheet's words (`NSWCPD`, `Newport`), never expansions, until a source states the full name. A code the combined release pads (`Code 032 - Code 032/Ocean Battlespace Sensing - ONR`) is the same department as the code written without the zero; a shipyard department (`PSNS-290 - 290 - Combat Systems`) becomes a department of the site, its code keeping the site's prefix so two sites' departments never share one |

## 2. Observations

One record per source statement. The wording is the source's, not ours.

| Field | Meaning |
| --- | --- |
| `id` | `obs:NNN` for hand-written observations; generated ones keep their ids across rebuilds: `obs:lrae:<release>:<node>` (`:parent`, `:contracting`) from an LRAE sheet, `obs:dpm:<node>` (`:parent`) from the NAVSEA deputy program manager list, `obs:page:<node>` (`:parent`) from a statement in `research/memory/org_page_statements.json` whose passages are checked verbatim against the saved page on every build |
| `source_url`, `source_revision` | the document and the exact copy (Wayback capture timestamp or retrieval date, plus the hash prefix from `research/sources/documents_manifest.jsonl`) |
| `observed_at` | the date the statement is dated to (publication, release or capture date) |
| `statement_type` | `parentage`, `contracting_support`, `leadership`, `consolidation`, `listing`, `naming`, `existence` |
| `passage` | the exact words, or a description of the table or list when the statement is structural |
| `subject_ids` | nodes the statement is about |

A sentence from an article is typed `parentage` only when an office the memory knows follows the phrase, and
`consolidation` only when an organization is what was established, merged or realigned: "the form establishes a
pathway" and the program name "Consolidated Afloat Network" are not reorganizations.

## 3. Relationships

One record per dated claim. A relationship never exists without observations.

| Field | Meaning |
| --- | --- |
| `id`, `type`, `from`, `to` | `child_of` (organizational parentage), `contracts_for` (contracting support), `leads` (a person's role, with `role_as_written`), `consolidated_into` (partial scope allowed), `part_of`, `listed_with` |
| `effective_from`, `effective_to`, `effective_dates_status` | dates only when a source gives them (`documented`); `inferred` when derived, with `effective_dates_note` saying how; otherwise both null and `unknown` |
| `scope_as_stated` | for consolidations, the source's own scope wording ("mission systems elements of ...") |
| `observation_ids`, `evidence_class` | what the claim rests on; `directly_documented` or `inferred`. The generator writes `inferred` in four cases: a department the LRAE names with its code (`Cast Products & Explosives Division (M2)`) or a code of the site (`C760 Divers`) placed under the row's contracting office when that office is a technical center or field activity; a PEO whose programs the NAVSEA headquarters deputy program manager list groups, placed under NAVSEA; a command whose sheet the Department of the Navy's combined forecast carries, placed under the department; and a technical center that one command's own forecast sheet names, or a field activity that sheet lists as the office owning rows, when no source places it, placed under that command (a field activity named only as a contracting site is not, since the NAVSEA sheet also names fleet units and stations there). The observation quotes what the source prints and the status note says it is the generator's inference, so human review can reject it. A parent another release's cell states outright (`10 - 10 - NSWCIHD`) makes the edge `directly_documented` |
| `current_status` | `{state, as_of, note}` with state `last_confirmed` (true as of the latest observation, not re-verified since), `ended` (a source documents the end), `superseded` (a later source states something else; the change date may be unknown), `conflicting` (sources disagree; both kept) |
| `review_status` | `draft`, `reviewed`, `retracted` |
| `drafted_by`, `reviewed_by`, `reviewed_on` | the assistant's draft record, and the identity and date of the human review (null until filled) |
| `retraction` | null, or `{reason, retracted_on, retracted_by, superseded_by}` |

An open `effective_to` means nothing by itself. Currency is read from `current_status`.

## 4. Interpretations

Readings that no single source states: the candidate placement of PMWs in the PAE capability
portfolios, how to resolve records across the PEO-to-PAE transition, an office's parent inferred
from a mission fit. Each carries `observation_ids`, `confidence` (`high`, `medium`, `low`),
`basis`, `competing_readings` and `what_would_resolve`. Interpretations never feed the alias
table or the ancestry walk unless human review promotes them to relationships with new observations.

## 5. Resolving a record to an office on its date

1. Extract candidate codes and names from the record (award description, notice text, LRAE
   requirement-office column, budget exhibit narrative).
2. Normalize (upper-case, strip a ` - NAME` suffix, collapse spaces and hyphens) and look the
   token up in the alias table first. Only then classify by the first matching family in
   `research/memory/org_code_families.json`; families say what kind of organization a code denotes, so an NIWC
   competency is never mistaken for a program office and a contracting UIC never for an owner.
3. An unknown code becomes an `unresolved` decision with the code kept verbatim (see the LRAE
   package reconciliation) and a review item; it does not become a node until a source names it.
   A contracting office is its UIC: releases that print one office two ways (`N00167: NSWC Carderock`,
   `N00167 - NSWCCD`) resolve to one node, and a bare department code under a site (`102 - 102 - NSWCIHD`)
   resolves to the department a release already named with that code at the site.
   A code matches an office by its name, aliases and office codes only; FPDS's agency id is a field of
   award records, so NRL's department 1700 is never read as the Department of the Navy.
4. Walk `child_of` and `consolidated_into` relationships whose observations are dated on or
   before the record's date, skipping retracted ones, to produce the ancestry as of that date.
   A 2019 award resolves to PMW 160 under PEO C4I under NAVWAR; a June 2026 modification of the
   same award resolves to PMW 160 under PEO C4I within PAE Mission Systems (interpretation
   `int:002`), with NAVWAR HQ still the contracting office.
5. Attach the record with the evidence class from `research/docs/04_attribution_process_and_examples.md`.

## 6. Adding and correcting

- Sources of change: organization pages (weekly hash diff), reorganization releases, new codes in
  the LRAE or in award text, change-of-command releases, tear-sheet revisions.
- Every new statement is an observation first. A relationship is added or its `current_status`
  updated only from observations.
- A real-world change supported by a source gets `effective_to` on the old relationship with
  `effective_dates_status: documented` and `current_status.state: ended` (PMW 150 and PMW 760
  program managers, 2025-08-19).
- A later statement that differs without a stated change date makes the old relationship
  `superseded` with the new observation's date in `as_of`; the change date stays unknown
  (PMW 160 program manager between the 2023 and 2025 tear sheets).
- Sources that disagree at the same time make the relationship `conflicting`, and both readings
  stay (PEO C4I under NAVWAR in September 2026).
- A wrong claim is never closed with an end date. It gets `review_status: retracted`, a
  `retraction.reason`, and `superseded_by` pointing to the replacement relationship or
  interpretation. The record stays in the file. The file holds three retractions: the two PMS 485
  parent relationships whose effective dates no source gave (`rel:059`, `rel:060`, replaced by
  `rel:061`, `rel:062` with dates unknown) and PMW 250's parentage, which was an inference and
  lives in `int:003`.
- The identity and date of the human review are recorded in `reviewed_by` / `reviewed_on`,
  separately from the assistant's `drafted_by`. Everything is `draft` until a human fills them.

## 7. Worked examples, read from the data

### 7a. PEO C4I: under NAVWAR, within PAE Mission Systems, or both

| Date | Source states | Recorded as |
| --- | --- | --- |
| 2026-04-12 (capture) | PEO C4I's own site lists eleven program offices and its leadership | `child_of` PMW -> PEO C4I relationships, `last_confirmed` 2026-04-12, dates unknown |
| 2026-05-11 | DoN release: PAE Mission Systems established "by pulling together the mission systems elements of ... PEO C4I, PEO Digital, PEO IWS, PEO MLB, DRPM Overmatch ..." | `consolidated_into` PEO C4I -> PAE MS, `effective_from` 2026-05-11 documented, `scope_as_stated` the release wording; nothing about which offices or functions moved |
| 2026-09-01 (capture) | NAVWAR Work-With-Us page: NAVWAR HQ "procures the IW systems sought by PEO C4I, PEO MLB and PEO Digital" | `child_of` PEO C4I -> NAVWAR still observed |
| 2026-09-16 (live) | NAVWAR Who-We-Are page describes NAVWAR as a service provider to the PAEs and no longer lists PEOs; PEO Digital and PEO MLB pages say they are "now part of the PAE Mission Systems" | `child_of` PEO C4I -> NAVWAR marked `conflicting`, both observations kept |

The sources do not state whether PEO C4I persists as an organization inside the PAE, or which
offices belong to which capability portfolio. `int:001` holds the candidate portfolio mapping at
low confidence; `int:002` states how records are resolved meanwhile. The September 2026 pages
conflict with one another; none of them supersedes the release.

### 7b. PMW 160 leadership

| Date | Source states | Recorded as |
| --- | --- | --- |
| 2023-05-01 (tear sheet, Wayback 2024-05-17) | "CAPT Katy Boehme, Program Manager" | `leads` Boehme -> PMW 160, dates unknown |
| 2025-01-01 (tear sheet v01312025, Wayback 2026-01-21) | "CAPT Nicole Nigro, Program Manager" | `leads` Nigro -> PMW 160, dates unknown, `last_confirmed` 2025-01-01 |
| between the two | nothing captured; the 2025-08-19 change-of-command release covers PMW 150 and PMW 760 only | Boehme's relationship `superseded` as of 2025-01-01, handover date unknown |
| after 2025-01 | nothing captured | `int:004`: current program manager unknown |

The later tear sheet supersedes the earlier one only for the fact it states (who was program
manager in January 2025). It says nothing about when the change happened or who holds the role
today, so those stay unknown rather than being filled by convention. Contrast PMW 760, where the
release of 2025-08-19 states the handover: Castillejo's relationship is `ended` with a documented
date, and Andalis moves from "Deputy Program Manager" (January 2025 tear sheet) to program manager
with a documented `effective_from`.

## 8. Mapping to the target tables

Nodes are `gov_organizations` rows (`org_type`, `aliases`, `valid_from`, `valid_to`, provenance);
relationships are `gov_organization_relationships` rows (`relationship_type`, dates, provenance);
observations are evidence references pointing at `research/sources/documents_manifest.jsonl` rows. Fields outside
the existing columns: `effective_dates_status`, `current_status`, `scope_as_stated`,
`review_status` with `retraction`, the review record, and interpretations as a table of their own. `consolidated_into` is a new relationship type.

A date column reads as a day a source states. The load writes `effective_from` and `effective_to` only when
`effective_dates_status` is `documented`; an inferred day stays in the memory with its note (acting from
May 2023 is not loaded as 2023-05-01). A claim ended or superseded on a day no source states is left out,
since a row without `valid_to` reads as current.
A position's `observed_at` carries its newest dated cited observation, and a memory person's `last_seen` the
newest dated source naming them (a cited observation or a document the people file ties to them); each row
takes that source's address as `source_url`. A claim or person no dated source states is left out rather than
dated at load time.

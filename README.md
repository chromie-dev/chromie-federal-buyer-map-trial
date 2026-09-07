# Federal Program Office Intelligence Trial

Build a source-grounded intelligence system that connects federal acquisition signals to the exact program offices and people who own the underlying need—before the need becomes an RFI or solicitation.

The one-week pilot will use the Department of the Navy as the difficult proving ground, with NAVWAR and PEO C4I as the recommended blocking test portfolio. The implementation must remain general enough to support other Navy PEOs, PMAs, PMSs, PMWs, commands, and federal agencies without flattening them into one generic agency directory.

The primary challenge is not contact collection. It is turning fragmented official pages, acquisition forecasts, budget exhibits, industry-day materials, SAM notices, awards, leadership records, and very large PDFs into a temporal evidence graph that can answer useful business-development questions with traceable evidence.

## Product outcomes

The final system must support two required directions of analysis. Opportunity resolution to the exact program office and relevant people is the primary product outcome. Program-office forecasting builds on that identity and evidence layer.

### 1. Opportunity to program office and people (primary)

Given a federal opportunity, RFI, solicitation, award, or contract, identify:

- The specific program office that owns or sponsors the requirement.
- Its parent portfolio, PEO, command, and agency path.
- The contracting office without confusing it for the requirement owner.
- The relevant program manager, deputy, requirements owner, technical lead, contracting officer, contracting specialist, small-business liaison, and other public contacts when supported.
- The exact documents, pages, passages, and structured fields supporting each relationship.
- Alternative candidate owners and an abstention result when ownership is not established.

The resolver must not stop at `Department of the Navy`, `NAVWAR`, `NAVAIR`, `NAVSEA`, or a contracting activity when a specific PMW, PMA, PMS, program office, or equivalent owner can be established.

### 2. Program office to future needs and predicted acquisitions (required)

Given a program office, produce:

- Its current mission, program responsibilities, systems, and portfolio boundaries.
- Historical RFIs, solicitations, awards, contracts, modifications, and recurring procurement patterns.
- Current and next-year budget signals, program-element changes, acquisition forecasts, industry engagement, and strategic priorities.
- A ranked set of likely future needs, with the evidence supporting and weakening each prediction.
- A prediction of what an early notice, RFI, or solicitation could look like, including likely requirement language, scope, timing range, acquisition path, and contracting activity.
- Likely recompete, follow-on, modernization, sustainment, and new-start patterns when the evidence supports them.
- Potential champions and decision-makers, clearly labeled as evidence-based recommendations rather than confirmed advocates.
- A prioritized early-engagement plan tied to public contact information and official sources.

Predictions must use only information available before the prediction cutoff date. Future documents may be used for backtesting labels but never as model inputs for that historical prediction.

## One-week outcome

Produce a working Navy program-office intelligence prototype that:

- Builds a verified source registry across the acquisition lifecycle.
- Documents the Navy PAE system: its official name and purpose, authoritative entry points, identifiers, workflow stages, data available to the public, access constraints, and how its records connect opportunities to program offices and people.
- Discovers and downloads the official documents needed for the analysis.
- Extracts page-addressable text and tables from large PDFs and spreadsheets.
- Resolves one Navy acquisition portfolio and its child program-office universe.
- Links official documents, facts, needs, procurements, awards, organizations, positions, and public contacts through explicit evidence.
- Runs the two required product queries above, with opportunity-to-program-office-and-people resolution treated as the primary demo.
- Backtests future-need predictions using historical cutoff dates.
- Evaluates Chromie's current Supabase patterns against the required queries.
- Produces a local-only, scalable data-model proposal and migration plan when the current model cannot represent the evidence cleanly.
- Exports deterministic fixtures that can be reviewed and later integrated without giving the intern production access.

The expected result is not a broad Navy research memo or a list of executives. It is a reusable source, data, and evaluation foundation that proves whether Chromie can trace and predict acquisition demand at the program-office level.

## Required demos

### Opportunity-first (primary)

```bash
python -m buyer_map.cli resolve-opportunity \
  --input data/examples/navy_opportunity.json \
  --as-of 2026-09-07 \
  --output build/opportunity
```

### Program-office-first (required)

```bash
python -m buyer_map.cli forecast-office \
  --program-office "PMW 160" \
  --as-of 2026-09-07 \
  --output build/program-office
```

Each required demo must return the answer, alternative candidates, confidence, source passages, document-page citations, counterevidence, and explicit unknowns.

## Extra credit: natural-language need to program offices

Only after the required forecasting, resolution, provenance, and backtesting gates pass, add a need-first workflow:

```bash
python -m buyer_map.cli match-need \
  --query "secure edge computing for disconnected naval operations" \
  --as-of 2026-09-07 \
  --output build/extra_credit/need
```

Given a natural-language customer capability, problem, or proposed solution, rank the program offices whose missions, funded programs, forecasts, solicitations, briefings, and historical awards indicate a relevant need. Return supporting passages, relevant programs and budget lines, public contacts, confidence, counterevidence, and whether each need is funded, forecast, recurring, emerging, or inferred.

Semantic similarity may generate candidates, but it cannot by itself establish organizational ownership or a real buying need. Extra-credit work cannot compensate for a failure in any required acceptance gate.

## Navy source registry

Source discovery is the main deliverable. The intern must create a machine-readable registry showing what each source can prove, which acquisition stage it covers, its identifiers, document formats, update cadence, access constraints, and extraction strategy.

### Required source families

| Stage | Official source family | What it should establish |
| --- | --- | --- |
| Organization and authority | Official PEO, command, portfolio, and program-office pages; charters; organization charts; directives | Canonical office identity, aliases, parentage, mission, child-office inventory, authority, and reorganizations |
| People | Official leadership pages, program-office pages, industry-day presentations, SAM contacts, forecasts, and public directories | Current public roles, office affiliation, responsibility, contact channel, and observation date |
| Agency intent | Department of the Navy budget materials, RDT&E exhibits, procurement justification books, operations books, strategy documents, and posture materials | Funded programs, program elements, project lines, requested funding, milestones, priorities, and emerging gaps |
| Congressional decision | Enacted appropriations, authorization material, committee reports, and official budget execution documents when material | Funding changes, restrictions, directed work, and program continuation or termination signals |
| Planned acquisition | Navy and command-level Long Range Acquisition Estimates, procurement forecasts, industry-day pipelines, sources sought, and planned opportunities | Expected requirement, office, estimated timing/value, acquisition strategy, and forecast contact |
| Navy acquisition systems | The Navy PAE system and authoritative documentation describing it | Official system name, purpose, workflow, record types, identifiers, responsible organizations, access model, and links to forecasts, notices, program offices, contracting offices, and contacts |
| Active acquisition | SAM.gov notices and attachments, PIEE Solicitation Module, SeaPort-NxG, Navy CSOs, SBIR/STTR topics, and other official notice channels | Requirement text, program ownership signals, dates, contacts, acquisition method, documents, and amendments |
| Award and execution | USAspending, SAM contract data, official award announcements, contract documents, and modifications | Awardee, obligation history, buying office, performance period, vehicle, predecessor chain, and execution patterns |

### Navy proving-ground sources

Begin with these official entry points and verify the specific pages, downloads, and identifiers used:

| Source | Initial use |
| --- | --- |
| [NAVWAR structure and acquisition pathways](https://www.navwar.navy.mil/Work-With-Us/NAVWAR-CSO-Opportunities/) | Distinguish NAVWAR HQ, NIWCs, PEOs, contracting pathways, DoDAACs, SAM, PIEE, and CSO channels |
| [NAVWAR Small Business Programs](https://www.navwar.navy.mil/About/Small-Business-Programs/) | Portfolio summaries, public contacts, industry engagement, and acquisition resources |
| [NAVWAR Long Range Acquisition Estimate](https://www.navwar.navy.mil/Portals/93/Images/Documents/NAVWAR%20HQCA-2025-A-037%20Long%20Range%20Acquisition%20Estimate%20JUN%202025.xlsx?ver=Vrhi0FQo3p2OOwPkkazUeA%3d%3d) | Planned requirements, timing, offices, contract expectations, and forecast contacts |
| [NAVSEA Program Executive Offices](https://www.navsea.navy.mil/serve-from-netstorage/About/Organization/Program-Executive-Offices/index.html) | A second organization universe for testing portability beyond NAVWAR |
| [NAVSEA Long Range Acquisition Estimate](https://www.navsea.navy.mil/serve-from-netstorage/Small-Business-Partnerships/LRAE/index.html) | Planned NAVSEA requirements and acquisition timing |
| [Office of Naval Research LRAE](https://www.onr.navy.mil/work-with-us/small-business/long-range-acquisition-forecast) | Research requirements, forecast records, program context, and small-business contacts |
| [Department of the Navy FY2027 budget document library](https://www.secnav.navy.mil/fmc/fmb/documents/forms/allitems.aspx?FolderCTID=0x01200085D748C964D2FE4D8C1403FD9F1A1CAC&RootFolder=%2Ffmc%2Ffmb%2FDocuments%2F27pres) | Large budget books and exhibits used to recover program elements, funding, projects, and future demand |
| [SAM.gov Contract Opportunities](https://sam.gov/content/opportunities) | Active and historical notices, attachments, official POCs, and procurement chronology |
| [PIEE](https://piee.eb.mil/) | Navy solicitations and secure vendor interactions not fully represented on ordinary pages |
| [SeaPort-NxG](https://www.seaport.navy.mil/) | Navy support-services vehicle and task-order context |
| [USAspending API](https://api.usaspending.gov/) | Awards, transactions, recipients, offices, obligations, and contract history |

The registry must preserve exact Navy identifiers such as PMW, PMA, PMS, PEO, program element, project number, appropriation line, DoDAAC, solicitation number, award ID, contract number, and vehicle ID. It must not treat platform names as organization identifiers unless an official source explicitly does so.

The intern must specifically investigate the Navy PAE system rather than treating it as a known acronym. Confirm its official expansion and scope from authoritative Navy material; identify who operates and uses it; map its entities, identifiers, lifecycle states, exports, and integrations; determine what can be accessed without Navy credentials; and document whether it contains unique signals that are absent from SAM.gov, PIEE, forecasts, budget documents, and USAspending. If PAE is restricted, produce a clear access-gap analysis and a public-source approximation strategy instead of attempting to bypass access controls.

## Document and PDF pipeline

The system must autonomously download and process the source documents needed for each result. Large PDFs are a core test, not a stretch goal.

### Acquisition

- Enumerate linked PDFs, spreadsheets, attachments, and revisions from official landing pages and notices.
- Use direct HTTP first and the optional browser API for JavaScript-heavy public pages.
- Preserve original URL, discovery page, displayed filename, final URL, publication date, retrieval time, MIME type, size, hash, and access status.
- Apply safe redirects, per-host rate limits, bounded retries, timeouts, and file-size limits.
- Record authenticated, expired, missing, blocked, and manual-download-required sources explicitly.
- Never bypass authentication, CAPTCHA, access controls, or terms of use.

### Extraction

- Retain the raw document and SHA-256 hash outside git.
- Extract page-level text with page numbers and bounding/layout information.
- Extract tables without losing row/column relationships, footnotes, headers, appropriation units, or fiscal-year columns.
- Use OCR only when native extraction is missing or unusable, and record OCR confidence.
- Detect section headings, exhibit numbers, program elements, office codes, named roles, contacts, dollar units, fiscal years, and solicitation/contract references.
- Produce stable chunks with document ID, page range, section path, token count, content hash, and extraction version.
- Maintain a page-to-chunk-to-fact-to-relationship evidence path.
- Never cite a document that was not successfully retrieved and processed.

Chunking must support both retrieval and auditability. A result is unacceptable if the relevant sentence can be retrieved but the original document and page cannot be reconstructed.

## Organization and ownership rules

The model must distinguish:

- Department and agency.
- Command or systems command.
- PEO or acquisition portfolio.
- Specific program office such as PMW, PMA, or PMS.
- Program, platform, product, project, and budget element.
- Requirement owner or sponsor.
- Contracting office and awarding office.
- Technical, test, sustainment, laboratory, warfare-center, and field activities.
- Vendor, incumbent, and contract vehicle.

A contracting office, awarding office, incumbent, platform name, NAICS, PSC, solicitation prefix, or semantic match may support candidate generation but cannot alone establish program-office ownership.

Valid ownership evidence includes an official source explicitly stating that the program office owns, manages, sponsors, issues, or has the requirement; an exact program-office code tied to the requirement; or unambiguous official POC context.

The system must support:

- Multiple candidate owners.
- Multi-program requirements.
- Explicit abstention when evidence is insufficient.
- Historical and current organization versions.
- Legacy names and reorganizations without force-mapping old records to a modern office.
- Confidence and review status on every derived ownership assertion.

## People and early-engagement intelligence

The objective is to find appropriate potential champions and decision-makers, not merely names with senior titles.

For every contact, preserve:

- Canonical identity and public contact information.
- Exact organization and program-office affiliation.
- Role type and raw title.
- Valid-from, valid-to, last-seen, and observed dates when available.
- Source URL, document, page, and evidence passage.
- Evidence of responsibility for the relevant program, requirement, budget, technology area, or acquisition.

Rank contacts into separate categories such as program leadership, requirement ownership, technical influence, acquisition authority, contracting execution, and small-business/industry engagement. Do not collapse these roles into one generic `buyer` label.

`Potential champion` is a prediction. The score must explain which public behaviors or responsibilities support the recommendation—for example, ownership of the relevant program, authorship or presentation of the need, responsibility for a matching budget or forecast line, appearance as a technical or program POC, or repeated industry engagement. Seniority or title alone is insufficient.

## Data-model challenge

The intern must evaluate whether Chromie's current Supabase patterns can answer the two required product queries without hiding critical relationships inside unqueryable JSON or duplicating facts across compiled agency pages.

The sanitized current-state contract will cover these existing patterns:

- `agencies`.
- `gov_organizations` and `gov_organization_relationships`.
- `gov_contacts`, `gov_contact_positions`, and `gov_contact_role_history`.
- `gov_procurement_organizations`.
- `gov_procurement_sources`, `gov_procurement_records`, and `gov_procurement_documents`.
- `gov_intel_records`, `gov_intel_facts`, and `gov_intel_links`.
- `agency_brain_documents`, `agency_brain_doc_chunks`, `agency_brain_items`, and `agency_brain_pages`.

The evaluation must specifically test:

- Whether one opportunity can retain several candidate program-office links with distinct evidence and confidence.
- Whether the model separates requirement ownership, program management, funding, technical influence, contracting, and award execution.
- Whether program-element, budget-line, project, PMW/PMA/PMS, DoDAAC, solicitation, award, and contract identifiers can be resolved across sources.
- Whether Navy PAE identifiers and lifecycle states can be represented and crosswalked to program offices, opportunities, contacts, and other official systems without assuming unsupported equivalence.
- Whether temporal reorganizations and historical names can be represented without rewriting history.
- Whether a large document's page and chunk can be traced to an extracted fact and then to a program-office/contact assertion.
- Whether conflicts and superseding evidence remain queryable.
- Whether a predicted future acquisition can be represented as a versioned, time-bounded output.
- Whether compiled `agency_brain_pages` remain derived views rather than becoming an untraceable source of truth.

If the existing model is insufficient, the intern must propose a local-only alternative or extension and demonstrate the required queries against it. The proposal must include tradeoffs, indexes, natural keys, temporal behavior, evidence requirements, migration mapping, and a rollback plan. It must not alter Chromie production.

## Required output package

```text
build/
  resources.md
  source_registry.json
  organization_seed.json
  gold.json
  manual_pdf_requests.json
  test_loop_README.md
  documents_manifest.jsonl
  document_chunks.jsonl
  extracted_facts.jsonl
  opportunity_resolution.json
  office_forecast.json
  contact_recommendations.json
  prediction_backtest.json
  model/
    current_schema_assessment.md
    proposed_model.md
    proposed_schema.sql
    current_to_proposed_mapping.md
    query_examples.sql
    validation_report.json
  extra_credit/
    need_to_offices.json
```

The `extra_credit/` directory is optional and must be attempted only after all required outputs and blocking tests are complete.

The first six files form the frozen research and regression handoff. The coding implementation may evolve, but it may not rewrite verified gold labels to make tests pass.

All fixtures must use deterministic local identifiers. No output may depend on production UUIDs, credentials, customer data, or a production database connection.

## Gold test suite

The resolver must be evaluated against ground truth created independently from its output.

For the NAVWAR/PEO C4I portfolio, aim for:

- Primary-source positives covering at least three distinct child program offices when available.
- At least three nearby hard negatives from sibling offices or the same contracting ecosystem.
- At least two abstention, multi-program, or legacy-reorganization cases.
- Exact expected ancestry paths from notice to program office to portfolio to agency.

Fixture classes:

- `positive_single_program`.
- `negative_non_target`.
- `abstain_insufficient_owner`.
- `multi_program_support`.
- `legacy_mapping_review`.

Initial blocking gates:

- Exact program-office accuracy on verified positives: 100%.
- Target precision: 100%.
- False-positive rate on hard negatives: 0%.
- Correct abstention on verified abstention cases: 100%.
- Multi-program cases incorrectly collapsed to one office: 0.
- Legacy cases force-mapped to current offices: 0.

Recall is reported but is not initially blocking. Precision and defensible abstention matter more than assigning every opportunity to an office.

## Prediction backtest

Select at least five historical Navy RFIs or solicitations across at least three program offices. For each event:

1. Establish a prediction cutoff before the RFI or solicitation was published.
2. Restrict model inputs to documents and facts publicly available by that date.
3. Generate predicted needs, likely scope, timing range, office, contracting pathway, and relevant contacts.
4. Compare the prediction with the later official RFI or solicitation.
5. Report correct signals, missed requirements, false positives, timing error, ownership accuracy, and evidence quality.

This is a research backtest, not proof that future opportunities are guaranteed.

## Five-day plan

| Day | Target |
| --- | --- |
| 1 | Resolve NAVWAR/PEO C4I identity and child program-office universe. Build the authoritative source registry, including a focused Navy PAE system investigation, download queue, and initial gold positives/hard negatives. Freeze the current Supabase contract. |
| 2 | Implement autonomous acquisition and page/table-aware extraction for official pages, PDFs, spreadsheets, SAM attachments, forecasts, and budget books. Produce stable document chunks and manifests. |
| 3 | Implement the local organization/evidence model and opportunity-to-program-office resolver. Validate exact ownership, ancestry, multi-owner cases, temporal identity, abstention, and contacts against gold fixtures. |
| 4 | Implement office-to-future-need forecasting, likely RFI/solicitation scope and timing, contact/champion ranking, and time-sliced historical backtests. Attempt natural-language need matching only after blocking gates pass. |
| 5 | Run the complete regression suite, demonstrate both required query directions, evaluate the current Supabase model, deliver the local proposal/mapping, and identify the next Navy portfolios and sources to onboard. |

## Acceptance criteria

- Every ownership, need, contact, and prediction claim has an evidence path to an official document and page or structured official record.
- The system resolves to the specific program office when decisive evidence exists and abstains when it does not.
- Contracting organizations are never silently substituted for requirement owners.
- The Navy PAE system is documented from authoritative sources, including its confirmed meaning, users, workflow, identifiers, access constraints, and crosswalk to the program-office intelligence model.
- Large budget and strategy PDFs are downloaded, hashed, extracted, chunked, and queryable with page reconstruction.
- Budget tables retain fiscal year, appropriation, unit, program-element, project, and dollar-scale context.
- Public contacts include temporal role evidence and are categorized by actual function.
- Potential champions are labeled predictions with supporting and weakening evidence.
- Opportunity-first and program-office-first demos run against the same underlying model; opportunity-to-program-office-and-people resolution is the primary acceptance workflow.
- Historical prediction tests enforce their cutoff dates and prevent future-data leakage.
- The model supports conflicting sources, multiple candidate owners, reorganizations, and superseding evidence.
- The Supabase assessment identifies which current patterns can be retained, extended, treated as projections, or replaced.
- Local query examples reproduce every demo output from structured data rather than hidden prompt context.
- No production access, customer data, private contact enrichment, or proprietary GovWin data is used.

## Evaluation rubric (100)

| Area | Points |
| --- | ---: |
| Authoritative source discovery and lifecycle coverage | 20 |
| Large-document acquisition, extraction, and provenance | 15 |
| Opportunity-to-program-office identity and resolution | 25 |
| Program-office future needs, likely scope, and acquisition backtesting | 15 |
| Scalable temporal/evidence data model | 15 |
| Contact and potential-champion intelligence | 5 |
| Reproducibility, tests, and handoff | 5 |

Extra credit: up to 10 additional points for defensible natural-language need-to-program-office matching. Extra credit cannot offset a failed blocking gate or incomplete required deliverable.

## Out of scope

- Mapping the entire Department of the Navy in one week.
- Treating all commands, PEOs, warfare centers, laboratories, field activities, and program offices as one flat hierarchy.
- Production Supabase migrations or writes.
- Private contact enrichment or automated outreach.
- Predicting classified or nonpublic requirements.
- Scraping paid intelligence products.
- Claiming a predicted person is a confirmed champion.
- Optimizing recall by accepting unsupported ownership mappings.

## Definition of done

The trial is complete when a reviewer can provide either a Navy program-office identifier or a Navy opportunity and receive a specific, evidence-backed path through the relevant organization, programs, budget and acquisition signals, public contacts, and predicted future needs, RFIs, solicitations, likely scope, timing, and next actions—with every result traceable to official source material, every uncertainty preserved, every historical prediction protected from future-data leakage, and a concrete assessment of how the model should integrate with or improve Chromie's Supabase architecture.

See `PROJECT_BRIEF.md`, `SECURITY.md`, and `AGENTS.md` before coding.

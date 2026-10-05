# 18 - Market entry and onboarding routes, from one set of conference notes

The manager's notes from a conference (section 8, verbatim) name registrations, portals, offices and people
through which a company enters federal selling, and a set of product ideas. This document sorts those notes
into four classes and keeps them apart: facts an official source states, first-party observations from the
conference, claims nobody has verified, and recommendations. The structured directory of routes is
`research/memory/onboarding_routes.json`, one entry per route, each carrying its evidence and its class; the
retrievals behind it are rows of `research/sources/documents_manifest.jsonl` whose note begins
`onboarding routes (conference notes, 2026-09-24)`, and the bytes are under `data/raw/`.

Every factual sentence below names its source and the date the page was read. A sentence attributed to the
notes is an observation or a claim, not a fact about how any agency buys. No decision, funding or purchasing
authority is read from any title on any page.

## 1. Conference observation record

| Item | Value |
| --- | --- |
| Conference | TechConnect, as the notes name it ("My TechConnect takeaway", "the specific classified SBIRs discussed at TechConnect") |
| Date of the conference | Not stated in the notes; not filled in from elsewhere |
| Provenance of the notes | The manager's own notes, provided to this task on 2026-09-24 and reproduced verbatim in section 8 |
| People named in the notes | Referred to here by role: the connector named in the notes, the proposal-development contact named in the notes, the contact whose team is discussed in the APEX market-research item |
| Dating rule | Conference attributions carry `event_date: null` with the note that the notes name no date, and `notes_date: 2026-09-24` |

Observations recorded from the notes, with the notes' wording preserved, each labelled
`observed, not independently verified`:

- "My TechConnect takeaway was that purchase cards may be held by a limited set of staff, including
  administrative purchasers." The notes themselves add: "That is an observation, not a rule that
  micro-purchases are only for administrative work." FAR 13.301(b), read 2026-09-24, says agency procedures
  "should not limit the use of the Governmentwide commercial purchase card to micro-purchases" and leaves
  the procedures to each agency, so who holds a card is an agency matter to ask about, not a rule.
- A contact named in the notes "uses GovWin; the discovery question is what her team still has to research
  or do manually." The notes place this under the APEX market-research idea; they do not state the contact's
  organization, and none is inferred.
- "This feels like a particularly accessible route for me: it is easier to build a direct relationship
  locally, and I have encountered less competition for attention." A first-party experience of the state
  and local route, not a property of that route.

## 2. Federal registration basics

Two registrations the notes call "different steps", each documented from its publisher's page.

| Route | What the source states | Source, date read |
| --- | --- | --- |
| SAM.gov entity registration | "If you want to apply for federal awards as a prime awardee, you need a registration. A registration allows you to bid on government contracts and apply for federal assistance. As part of registration, we will assign you a Unique Entity ID." | `https://sam.gov/content/entity-registration` (final address `https://sam.gov/entity-registration`), 2026-09-24 |
| The same, in SBA's contracting guide | "To participate in government contracting, you must register your business in the federal government’s System for Award Management (SAM). SAM is a database that government agencies search to find contractors." | `https://www.sba.gov/federal-contracting/contracting-guide/basic-requirements` (final address `https://www.sba.gov/counseling/get-started/#basic-requirements`), 2026-09-24 |
| SBA Small Business Search profile | The address answers with an application titled "SBA Small Business Search" whose description reads "Small businesses everywhere are seeking federal contracts. Find the ones that meet your agency’s needs right now." It redirected to `search.certifications.sba.gov`. | `https://dsbs.sba.gov/search/dsp_dsbs.cfm`, 2026-09-24 |

What the notes add that the saved pages do not state: the profile fields (capabilities, keywords, NAICS
codes, certifications, past performance). The saved search page is a script-rendered shell and carries no
field list; the SBA guide's profile passage ("Your small business’ profile in SAM is like a résumé") speaks
of the SAM profile. The field list is an open verification item (section 7).

## 3. Portals and network directory

Directly documented routes for finding primes and agency small-business offices. The entries in
`onboarding_routes.json` carry the full passages; the table gives the sentence each rests on.

| Route | Publisher | What the source states | Source, date read |
| --- | --- | --- | --- |
| Directory of Federal Government Prime Contractors with a Subcontracting Plan | SBA | "The directory lists federal government contractors with requirements to subcontract to small businesses." | `https://www.sba.gov/document/support-directory-federal-government-prime-contractors-subcontracting-plans`, 2026-09-24 |
| SUBNet | SBA | "any large business can post a notice of a subcontracting opportunity, including the solicitation, to SBA’s subcontracting database, SUBNet." | SBA contracting guide, prime and subcontracting, `https://www.sba.gov/federal-contracting/contracting-guide/prime-subcontracting`, 2026-09-24. The portal host `web.sba.gov` did not resolve from this address; the failure is a ledger row |
| NASA Office of Small Business Programs | NASA | "The mission of the NASA Office of Small Business Programs is to promote and integrate small businesses into the industrial base of contractors and subcontractors that support the future of space exploration, scientific discovery, and aeronautics research." | `https://www.nasa.gov/osbp/`, 2026-09-24. No NASA page titled a "subcontracting program" was located; `/osbp/subcontracting-opportunities/` answers 404 |
| NSA Acquisition Resource Center | NSA | "The NSA Acquisition Resource Center (ARC) is an innovative business registry database that provides industry with a one-stop source for acquisition information. The registry also serves as a market research tool for NSA personnel, as well as a means for distribution of acquisition documents to our industry partners." | `https://www.nsa.gov/business/Acquisition-Resource-Center/`, Wayback capture 2026-06-08 (nsa.gov refuses this address), read 2026-09-24 |

The SBA guide page also names agency subcontracting directories (Department of War LYNX, DOT, GSA, USDA).
They are outside the notes and are not entered; the passage stands in the saved bytes for whoever needs them.

The notes' instruction "build a directory of prime supplier networks, agency vendor portals, consortiums,
registration requirements, and the person who manages each route" is the purpose of `onboarding_routes.json`.
Its `who_manages_it` field records the role or organizational level the source states and nothing about
authority. "Register a customer where its capability fits a plausible team or requirement" is a
recommendation (section 6).

## 4. People and relationship routes

### Small-business offices

The GSA page "Get help" (`https://www.gsa.gov/small-business/get-help`, read 2026-09-24) states: "Our small
business specialists provide access to our nationwide procurement opportunities through outreach, training,
and counseling." The notes call this a directory; the saved page offers a way to connect, and the two
addresses containing "specialists" tried first answered 404 the same day. Whether a per-region listing exists
is an open item. The notes' general rule for other agencies ("find its small business office and the program
office with the need") names no specific office and is recorded as a recommendation.

### APEX Accelerators

SBA's targeted-support page (`https://www.sba.gov/counseling/local-assistance/targeted-support-and-services/`,
read 2026-09-24) states: "APEX Accelerators (formerly known as Procurement Technical Assistance Centers)
provide technical assistance to businesses interested in selling products or services to federal, state, and
local governments." Its list of what they can help with reads, item by item, "Determine if you’re ready for
federal contracting", "Help you register in the proper places", "See if you’re eligible for small business
certifications" and "Assist you in researching past contract opportunities"; it links "Find an APEX
accelerator near you" to
`apexaccelerators.us`, which refuses this address; the Wayback capture of 2026-09-21 is a script shell
titled "APEX Accelerators". Not stated by the source: that APEX hosts supplier matchmaking events where
subcontractors meet primes. That part of the notes is an open item.

SBA's events listing (`https://www.sba.gov/events`, read 2026-09-24) exists and is searchable: "Use the
fields to narrow your search and select the “Search for events” button to see your results." Whether it
carries matchmaking events was not read.

### GSA FAS

GSA's organization address for the Federal Acquisition Service redirects to "Explore acquisition options"
(`https://www.gsa.gov/about-us/organization/federal-acquisition-service`, read 2026-09-24): "One way we do
this is through our Federal Acquisition Service business line. FAS helps government agencies buy and access
the products, services, and solutions they need to achieve their missions." The page names no
representatives or regions; who the notes' "FAS representatives" are is an open item.

### SBA Regional Innovation Clusters

The same SBA targeted-support page states: "SBA’s Regional Innovation Clusters (RICs) deliver direct support
to innovative small businesses and startups across the country. RICs provide services like accelerators,
market research, customer discovery," and lists the clusters with the region and industries each serves and
a program mailbox. That RICs "reveal buyer needs, market-entry routes, and introductions" is the notes'
expectation, not the source's statement.

### Local and state routes

No official source is fetched for this route because the notes name no office. The route rests on the
first-party observation in section 1 and is otherwise a recommendation: "Make local events and office visits
part of relevant customer plans."

### Official contact forms and general inboxes

The notes' rule ("A generic inbox is a starting route, not a reason to stop looking for an owner") is a
working method, recorded as a recommendation. It matches this package's existing practice of storing a
published channel as an `industry_intake_channel` observation and the person who answers as a separate,
dated observation.

## 5. Turning research into a pursuit

The notes' method (pick an agency, map its labs, program offices, budget signals, incumbent work, small
business office and likely champions, then answer the written requirement and show the mission connection
without claiming an unstated requirement is mandatory) is what `research/docs/17` walks for one Navy
requirement. Two specifics from the notes:

- **NOAA Research.** The laboratories and program offices page (`https://research.noaa.gov/labs-programs/`,
  Wayback capture 2025-09-19 because the host refuses this address, read 2026-09-24) states: "The NOAA
  Research Laboratories conduct an integrated program of research, technology development, and services to
  improve the understanding of Earth's atmosphere, oceans and inland waters, and to describe and predict
  changes occurring to them. The laboratories and their field stations are located across the country and
  around the world." and, of the program offices, "Located in NOAA Headquarters in Silver Spring, Maryland,
  our six Program Offices select, fund". Investigating each lab and office individually is the notes'
  recommendation; nothing about what they buy is read here.
- **Acquisition route for a scoped pilot.** FAR 2.101 (`https://www.acquisition.gov/far/2.101`, read
  2026-09-24) states "Micro-purchase threshold means $15,000, except it means- (1) For acquisitions of
  construction ... $2,000; (2) For acquisitions of services subject to 41 U.S.C. chapter 67 ... $2,500; (3)
  ... contingency operation ..." and "Simplified acquisition threshold means $350,000, except for— (1)
  Acquisitions of supplies or services that, as determined by the head of the agency, are to be used to
  support a contingency operation ...". FAR 13.301 (`https://www.acquisition.gov/far/13.301`, read
  2026-09-24) states "(a) ... the Governmentwide commercial purchase card is authorized for use in making
  and/or paying for purchases of supplies, services, or construction. The Governmentwide commercial
  purchase card may be used by contracting officers and other individuals designated in accordance with
  1.603-3." and "(b) ... Agency procedures should not limit the use of the Governmentwide commercial purchase
  card to micro-purchases." Which route fits a given pilot is, as the notes say, a question for the buyer
  and the contracting team.

**Other transaction consortia.** The notes ask to track "the consortium, membership rules, project calls,
and sponsor for each relevant defense technology". No consortium is named, so none is entered; the authority
page (10 U.S.C. 4022 on uscode.house.gov) did not answer from this address and has no Wayback capture, so
even the general statement is left for the next pass (section 7).

**Classified SBIR-related opportunities through the ARC.** The notes make a distinction that is kept: the
ARC as a registry and acquisition-document channel is documented (section 3); that classified SBIR-related
opportunities are listed there is not. The saved ARC capture contains no occurrence of "SBIR". The route is
`unverified` in the directory and is not to be added to a customer playbook until section 7's step is done.

## 6. Chromie product ideas / recommendations

These are the notes' proposals and the owner's follow-ups. None is a fact, none was acted on by this task,
and none is entered in the route directory unless it is itself an external route.

1. **A procurement-onboarding agent.** Guide a company from profile and registrations (section 2) through
   agency fit, portal enrollment (section 3), opportunities, contacts (section 4) and next actions. The
   notes' rationale: "Getting more useful companies into the network could benefit agencies, APEX
   counselors, and primes."
2. **A market-research workflow for APEX.** Discovery question from the notes: what the team of the
   contact named in the notes "still has to research or do manually" beside GovWin. Recommended follow-up
   for the owner: a conversation with that contact; nothing was asked by this task.
3. **Find the SBA software buyer.** Recommended follow-up for the owner: the connector named in the notes,
   then the program owner and the acquisition contact. The page of the SBA Office of the Chief Information
   Officer (OCIO)
   (`https://www.sba.gov/about-sba/sba-locations/headquarters-offices/office-chief-information-officer`,
   read 2026-09-24) states its mission ("to foster an environment in which information and technology are
   used to support small businesses") and that the CIO "reports to the SBA Administrator and Deputy
   Administrator". As the notes say, "OCIO is a technology stakeholder; its involvement alone does not
   establish who funds or buys a pilot." Buyer authority is an open item, not an inference from the office.
4. **Ask about the acquisition route for a scoped pilot.** Ask the buyer and the contracting team which
   route fits; the thresholds and the purchase-card rule are in section 5; who holds cards is the section 1
   observation, to be asked, not assumed.
5. **Proposal-development firms.** Test a monthly research retainer plus a fixed-scope review. Recommended
   follow-up for the owner: the proposal-development contact named in the notes.
6. **Local and state relationship-building.** Make local events and office visits part of customer plans
   (section 4).
7. **Register a customer where its capability fits.** Use the directories in section 3 once a plausible
   team or requirement is identified.
8. **Grant-writing assistance mentioned through AFCEA.** Identify the provider, eligibility and scope before
   recommending it; it is `unverified` in the directory (section 7).

## 7. Open verification items

| Item | What the notes claim | What was verified | What remains | Next step |
| --- | --- | --- | --- | --- |
| Classified SBIR via NSA ARC | "classified SBIR-related opportunities may be found through ARC" | ARC is a business registry and acquisition-document channel (Wayback 2026-06-08) | That SBIR opportunities, classified or not, are distributed through it; access requirements | Fetch the ARC registration and document pages from a United States address and search for SBIR; if absent, ask NSA's small business office through its published contact |
| AFCEA grant-writing assistance | "free grant-writing assistance mentioned through AFCEA" | Nothing; no provider is named | Provider, eligibility, scope, whether free | Identify the provider from the AFCEA material, then fetch the provider's own page |
| SBA Small Business Search profile fields | capabilities, keywords, NAICS codes, certifications, past performance | The search application exists (shell saved) | The field list | Render `search.certifications.sba.gov` through the hosted browser and quote the fields |
| SUBNet portal | SBA's subcontracting database | SBA's guide names and describes it | The portal address and access | Read the link target from the saved guide bytes and fetch it |
| NASA "subcontracting program" | "agency resources such as NASA’s subcontracting program" | OSBP and its mission covering subcontractors | A page describing a subcontracting program by that name | Follow OSBP's Doing Business With NASA pages |
| GSA specialist directory | "GSA’s small business specialist directory" | The specialists and a way to connect | A per-region listing | Follow the connect link in the saved page |
| GSA FAS representatives | "relevant GSA FAS representatives" | FAS is a GSA business line | Who the representatives are and how industry reaches them | Fetch GSA's regional or customer-service contact pages |
| APEX matchmaking events and calendars | "host training or supplier matchmaking events where subcontractors can meet primes; ... local APEX calendars" | Counseling, registration, certification and research help (SBA page) | Matchmaking events; calendars | Render `apexaccelerators.us` through the hosted browser |
| OT consortia | track consortium, membership rules, project calls, sponsor | Nothing (no consortium named; statute page unreachable) | Everything, per consortium | Fetch 10 U.S.C. 4022 from a United States address; enter consortia one by one as customers need them |
| SBA software buyer | program owner, OCIO, acquisition contact | OCIO exists with the mission and reporting line its page states | Who owns, funds or buys | Owner follow-up with the connector named in the notes; then SBA's IT forecast and contracting pages |
| Purchase-card holders | "a limited set of staff, including administrative purchasers" | FAR 13.301 leaves procedures to the agency | Who holds cards at a target agency | Ask the agency's contracting team; record the answer as a dated observation |

## 8. SOURCE

The manager's conference notes, reproduced exactly as provided on 2026-09-24. Names appear here as written;
everywhere else in this package the people are referred to by role.

```text
Conference notes
Get a company into the right systems
 Start with the federal basics. Register the entity in SAM.gov to pursue federal prime contracts. Complete its SBA Small Business Search profile with capabilities, keywords, NAICS codes, certifications, and past performance so primes can find it. These are different steps. SBA contracting guide
 Map the portals that matter for each customer. Build a directory of prime supplier networks, agency vendor portals, consortiums, registration requirements, and the person who manages each route. Start with SBA’s directory of primes with subcontracting plans and SUBNet and agency resources such as NASA’s subcontracting program. Register a customer where its capability fits a plausible team or requirement.
 Getting into a network has value of its own. A complete supplier profile makes a company easier to discover when a prime starts market research. It also gives us a reason to contact the prime’s small business or supplier team.
Find people, then build relationships
 For each target agency, find its small business office and the program office with the need. Subscribe to updates, attend office hours, ask how the agency buys, and maintain the relationship. The small business contact can route us; the program owner can explain the mission problem. GSA’s small business specialist directory is an example of a useful starting point.
 Use SBA and APEX as connectors. Have each customer contact its local APEX Accelerator. SBA and APEX offer counseling and host training or supplier matchmaking events where subcontractors can meet primes; track SBA events alongside local APEX calendars. SBA’s APEX overview
 Meet state and local officials in person. This feels like a particularly accessible route for me: it is easier to build a direct relationship locally, and I have encountered less competition for attention. Make local events and office visits part of relevant customer plans.
 Use the routes agencies actually provide. A focused question through an official contact form, communications office, or general inbox can reach the right team. Record who answers and ask for the specific program or acquisition contact. A generic inbox is a starting route, not a reason to stop looking for an owner.
 Reach relevant GSA FAS representatives and SBA Regional Innovation Clusters. They can reveal buyer needs, market-entry routes, and introductions. Build these relationships around a customer or use case.
Turn research into a pursuit
 Pick an agency and identify its need before pitching. Map its labs, program offices, budget signals, incumbent work, small business office, and likely champions. For environmental and climate customers, investigate the relevant NOAA Research labs and program offices individually.
 Use agency analysis to respond beyond the solicitation. Answer the written requirement, then show how the work advances the agency’s larger mission, program goals, and likely next step. Keep the connection sourced and specific; do not claim an unstated requirement is mandatory.
 Treat a proposal as one stage of the sales path. The useful output is a fit assessment, evidence gaps, pricing or work plan, contacts, introductions, and a transition path. That is where Chromie’s report-style analysis and long-running agents can help.
 Explore OT consortiums and less visible channels for fitting customers. Track the consortium, membership rules, project calls, and sponsor for each relevant defense technology. I also heard that classified SBIR-related opportunities may be found through ARC. I verified the NSA Acquisition Resource Center as an industry registry and acquisition-document channel, but have not verified that the specific classified SBIRs discussed at TechConnect are listed there. Confirm the route and access requirements before adding it to a customer playbook.
Sell Chromie into the system
 Test an agent for procurement onboarding. It could guide a company from profile and registrations through agency fit, portal enrollment, opportunities, contacts, and next actions. Getting more useful companies into the network could benefit agencies, APEX counselors, and primes.
 Explore a market research tool for APEX. Lisa Wood uses GovWin; the discovery question is what her team still has to research or do manually. Test whether Chromie can prepare deeper company-specific briefs and carry out follow-up tasks.
 Find the SBA software buyer. Follow up with Nick as a connector and seek the program owner, SBA Office of the Chief Information Officer, and acquisition contact for procurement-onboarding or market-research software. OCIO is a technology stakeholder; its involvement alone does not establish who funds or buys a pilot.
 Ask about the acquisition route for a scoped pilot. My TechConnect takeaway was that purchase cards may be held by a limited set of staff, including administrative purchasers. That is an observation, not a rule that micro-purchases are only for administrative work. The standard federal micro-purchase threshold is $15,000 and the simplified acquisition threshold is $350,000, subject to exceptions and agency procedures. Ask the buyer and contracting team which route fits. Acquisition.gov thresholds, purchase-card rule
 Sell to proposal-development firms too. Some organizations outsource research and proposal work. Test a monthly research retainer plus a fixed-scope review with contacts such as Ortman.
 Check the free grant-writing assistance mentioned through AFCEA. Identify the actual provider, eligibility, and scope before recommending it to customers; I have not verified the specific service. It may be a useful referral or complement to Chromie’s research reports.
```

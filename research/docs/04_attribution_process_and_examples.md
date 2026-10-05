# 04 - Contract-to-program-office attribution: process and reviewed examples

Machine-readable examples: `research/memory/attribution_examples.json` (one record per
reviewed contract, order, vehicle or modification, with quoted passages and source URLs). The
table below is generated from that file.

## In plain terms

A contract record almost never says outright which program office owns the need. NAVWAR's
contracting office code appears on every action, but it serves three PEOs, a NAVSEA program
office and NAVWAR's own headquarters, so the code alone narrows nothing. The description text
does carry the answer often (about half of the sampled actions name a PMW), the SAM.gov notice
for the action's solicitation almost always names the office in its first sentence, the
Long-Range Acquisition Estimate names the requirement office for the follow-on of many existing
contracts, and tear sheets map program names to offices. The process below applies those sources
in a fixed order and records how strong the result is, including when it is "we do not know".

## 1. The repeatable process

1. **Fix the unit.** Attribute the order, contract or vehicle, not the modification. Read the base
   award's description first; modifications carry text such as "DE-OBLIGATION" or "CEILING
   REALIGNMENT" (EX15, EX16, EX19). Orders are attributed separately from their parent vehicle; a
   multiple-award vehicle such as SeaPort-NxG is never attributed (EX01, EX04, EX05), a
   single-purpose vehicle can be (EX13).
2. **Read the offices on the record.** Contracting office and funding office come from FPDS
   (`contractingOfficeID`, `fundingRequestingOfficeID`). They are recorded as facts about the
   buyer and the payer and are never promoted to ownership. A funding office in another systems
   command is strong evidence against the pilot portfolio (EX09).
3. **Look for an office code or explicit ownership language in the description** ("PMW 160",
   "PMW/A 170", "PROGRAM MANAGER, WARFARE TACTICAL NETWORKS (PMW 160)", "IN SUPPORT OF PEO MLB
   PMW 220"). A match against the alias table in `research/memory/organization_seed.json` yields
   `directly_documented`. Lists of several offices yield a multi-office result with a primary and
   supporting offices; unknown tokens stay unresolved inside it (EX07). A PEO-level mention
   ("PEO C4I") resolves to the portfolio or its front office, not to a PMW (EX17).
4. **Read the SAM.gov notice for the action's solicitation number.** NAVWAR's presolicitations,
   RFPs, J&As and award notices open with "in support of PEO C4I, <office name> (PMW xxx)"; the
   notice is an official record of the same action, so a match is `directly_documented` (EX09,
   EX12-EX16). SAM.gov's site API returns notices back to at least 2014 by solicitation number.
   Task orders competed inside SeaPort-NxG have no SAM.gov notice (nine solicitation numbers
   checked, none found), so this step yields nothing for them.
5. **Check the LRAE.** Match the contract number against the LRAE "Existing Contract Number"
   column and read the "Associated Program or Requirement Office" of the follow-on row. Because
   the LRAE is an estimate about a future requirement, it corroborates rather than documents:
   the result is `inferred` unless the award text also names the office (EX08, EX09, EX13).
6. **Map program names through official tear sheets.** "CANES", "ADNS", "MIDS-LVT", "NMT" map to
   offices through the tear sheet that lists them as the office's programs. Result: `inferred`,
   with counterevidence recorded when another organization also holds requirements for the same
   program (EX14). A functional match (a program's purpose fits an office's mission) is
   `ambiguous`, not inferred (EX16).
7. **Rank candidates even when nothing is decisive.** Ownership is ambiguous by design while
   PEOs migrate into PAEs, and a wrong first contact is cheap (the office redirects to the right
   one). So an `inferred`, `ambiguous` or `unresolved` result still lists the most likely
   offices and the most likely public contacts, each with a confidence label and the basis, and
   the monitor surfaces them instead of staying silent. Recall is favored over precision; the
   evidence class keeps the record honest about how strong the answer is.
8. **Date the ancestry.** The office's parent chain is read from the seed graph as of the
   action date: PEO C4I under NAVWAR until 2026-05-10, consolidated into PAE Mission Systems from
   2026-05-11; PEO EIS offices moved to PEO Digital or PEO MLB in May 2020 (EX12, EX13).
   Descriptions are re-authored at modification time, so the organization name in a description
   never dates anything (EX02).
9. **Record the result** with the quoted passages, document, observation date, evidence class,
   counterevidence, and, when unresolved, what would resolve it (EX19). Every record goes to a
   reviewer when the class is `inferred`, `ambiguous` or `unresolved`.

A notice filed at a contracting office that names no program office is read the same way by
`research/tools/pages.py`, strongest step first: an office code in its own text, a forecast row that
carries its solicitation number, a contract it cites that one program office holds, then a program name
in its title that two or more forecast rows of one office use and no other office's rows do. Each step
must point to a single office, otherwise the notice stays at the contracting office. The object pages
and every answer print a reading as office not stated, with its basis and the office that filed it: the
ADNS notices under solicitation N0003925R9510 are read to PMW 160 because forecast row
N00039-23-RFPREQ-PMW-160-0108 carries that number.

A notice no record places keeps the office that filed it and carries up to three guesses, best first: the
program offices whose own forecast rows, notices, contract rows and topics share a word the notice writes
as a program name, each word weighed by how rare it is across the record and by how much of its use is
that office's. A program name is a word the titles mostly write in capitals or with a digit (AEGIS,
IUSS, SF2), so a title of plain words guesses nothing. A guess is printed as a guess with the words behind
it, never moves the notice, and becomes a question in the guessed office's meeting brief: the AEGIS
notices NAVSEA filed with no office carry IWS 1.0 first, and the IUSS notices carry PMS 485.

`research/tools/office_wiki.py` writes a page per program office from the record: where it sits, the
program names its records use, and its newest forecast rows, notices, contract work and topics, with one
directory line per office. A model reads each notice no record places against the directory and the pages
of the offices its program names point to, and names one office or none. The rules keep the answer only
when the notice words and the page line it quotes are verbatim and the notice words are more than generic
words. The reading is printed with both quotes beside the guesses and never moves the notice. It is checked
on the notices that do name their office, with every office name masked and every record under the same
solicitation left out of the pages.

The same reading covers three more kinds of record, each with its own prompt and the same rules: a live
contract award signed at an office that owns no requirement, read from its description; an SBIR topic a
command published; and a committee statement addressed to the department. A kind is checked on the
records that do name their office, with the office names masked, and the views use it only once that
check holds: award readings are used; topic readings are saved and not used, since on the topics that
name their office the model often names a neighbouring office; committee statements name no program
office to check against, so their readings are saved and not used. A special notice, SAM.gov's catch-all
type, is read by `research/tools/notice_kinds.py`: the model names what it announces from a fixed list
(an industry day, an intent to award a sole source, a request for information, a draft solicitation and
others) or none, and copies the words that state it. The kind is kept only when those words are in the
notice verbatim; otherwise the title's keywords type the notice as before.

`research/tools/outreach.py` starts from what a company sells and ends with one letter. A model chooses
every move through the views the object pages use and rates how promising each one is; the walk takes the
most promising move of any branch next, so one branch cannot spend the step budget. The model then names
the chain from the agency to the program office, the requirements under that office and the initiatives above it, and the people to write to, and
writes the letter from that evidence alone. The rules choose nothing. They refuse an answer when a hop is
not above the one below it in the organization tree, a record was not shown or its quote is not verbatim,
a person is not tied to the chain, or the letter carries a number, date, identifier or name the evidence
does not. One repair is allowed; a run that still fails is kept and marked refused. A profile the record
holds nothing for comes back with no chain and no letter.

Likely contacts per office, from published sources only (tear sheets, change-of-command releases,
the LRAE's published contracting points of contact, official small-business and industry pages),
are in `research/memory/contact_observations.json` and `research/memory/contact_recommendations.json` with a confidence label and the reason for it.

## 2. Evidence classes

| Class | Meaning | Examples |
| --- | --- | --- |
| `directly_documented` | an official record of the action (award description, or the SAM.gov notice for the action's solicitation) names the office or its code | EX01-EX07, EX09-EX18 |
| `inferred` | the office is named only in a corroborating official document (LRAE row for the follow-on, tear sheet program list) | EX08 |
| `ambiguous` | candidates exist but no official document ties the program to an office | none remaining (EX16 was ambiguous until the solicitation notice was read) |
| `unresolved` | no owner signal in any retrieved record; recorded with the path to resolution | EX19 |

Things that never establish ownership on their own: contracting office, funding office, vendor,
vehicle, NAICS, PSC, platform or system name, or a semantic resemblance.

## 3. Reviewed examples

| Id | Identifier | Vendor | Contracting / funding office | Owner (program office) | Class | Why |
| --- | --- | --- | --- | --- | --- | --- |
| EX01 | `N0003922F3000` | Booz Allen Hamilton Inc. | N00039 | pmw:160 | directly_documented | Recorded at order level. The IDV serves many Navy customers and is not attributed. Period of performance ends 2026-10-26 with the follow-on forecast f |
| EX02 | `N0003917F3000` | Booz Allen Hamilton Inc. | N00039 | pmw:160 | directly_documented | Awarded in 2016 under SPAWAR, yet the current description says 'NAVWAR': descriptions are re-authored at modification time, so the organization name i |
| EX03 | `N0003920F3016` | Alpha Omega Group LLC | N00039 | pmw:160 | directly_documented | Recurring purchase: the same requirement recompeted to the same vendor; a template for recompete alerts. SAM.gov search for the order's solicitation n |
| EX04 | `N0003921F3003` | Booz Allen Hamilton Inc. | N00039 | pmw:120 | directly_documented | Same vendor, same vehicle and same contracting office as EX01 and EX05, three different program offices: vendor, vehicle and contracting office cannot |
| EX05 | `N0003921F3016` | Booz Allen Hamilton Inc. | N00039 | pmw:150 | directly_documented | Program manager changed on 2025-08-19 (Castillejo relieved Jolie); office ownership unchanged. SAM.gov search for the order's solicitation number retu |
| EX06 | `N0003921F3012` | Highbury Defense Group | N00039 | pmw:170 | directly_documented | SAM.gov search for the order's solicitation number returned no notice (SeaPort-NxG task-order competitions are not posted on SAM.gov). |
| EX07 | `N0003921F3008` | KOAM Engineering Systems, In | N00039 | pmw:760, pmw:160, pmw:740 | directly_documented | Multi-program case. It must not collapse to PMW 760 alone, and the unresolved 'FRD' component is recorded rather than guessed. SAM.gov search for the  |
| EX08 | `N0003920F3015` | Science Applications Interna | N00039 | pmw:740 | inferred | Inferred, not directly documented: the office comes from the forecast row for the follow-on and from the office's mission statement, not from the awar |
| EX09 | `N0003919C0002` | BAE Systems Information and  | N00039 / funded by N00024 | pms:485 | directly_documented | Hard negative for the pilot portfolio: a NAVWAR HQ contract whose requirement belongs to a NAVSEA program office. The funding office separates systems |
| EX10 | `N0003918F1437` | Chenega Professional & Techn | N00039 | pmw:220 | directly_documented | Hard negative for PEO C4I: the same contracting office serves a PEO MLB office. |
| EX11 | `N0003921F3013` | Falconwood, Inc. | N00039 | pmw:220 | directly_documented | The same vendor holds N0003922F3001, which the LRAE (code PCE, 'PEO Digital Engineering and Logistics Support') places with PEO Digital: one vendor, t |
| EX12 | `N0003920D0054` | Leidos, Inc. | N00039 | office:nen | directly_documented | Program-name case resolved through an official article that names the awarding office. Ownership changed parent twice (PEO EIS to PEO Digital in 2020; |
| EX13 | `N0003915D0042` | Data Link Solutions LLC (com | N00039 | pmw:101 | directly_documented | Attribution at vehicle level is acceptable here because the IDV is single-purpose (MIDS-LVT), unlike SeaPort-NxG. Modification P00029 (2026-05-14) fal |
| EX14 | `N0003920F9504` | Serco Inc. | N00039 | pmw:160 | directly_documented | Program-name inference with recorded counterevidence: production buys of ADNS enclaves through NAVWAR HQ fit the program office of record; in-service  |
| EX15 | `N0003919F9520 (modification P00019)` | L3 Technologies, Inc. | N00039 | pmw:101 | directly_documented | Process point: a modification is attributed through its base award, never from the modification text alone. The 2014 presolicitat |
| EX16 | `N0003920D0021` | Raytheon Company | N00039 | pmw:170 | directly_documented | Ambiguous by rule: a functional match between a program name and an office mission is candidate generation, not ownership evidence. Resolution path: t |
| EX17 | `N0003921F3014` | XST Inc. | N00039 | peo:c4i | directly_documented | Portfolio-level owner. The resolver must stop at PEO C4I here rather than pick PMW 750 or PMW 760 because the work is integration for the portfolio; t |
| EX18 | `N0003917F3010` | Booz Allen Hamilton Inc. | N00039 | pmw:170 | directly_documented | SAM.gov search for the order's solicitation number returned no notice (SeaPort-NxG task-order competitions are not posted on SAM.gov). |
| EX19 | `N6523618D8014 (modification P00025)` | Science Applications Interna | N65236 | none | unresolved | Explicit unknown. A large ceiling increase is a meaningful signal even without an owner, so the monitor should still raise it and route it to review. |

Coverage of the set: seven PEO C4I program offices plus the PEO C4I front office
(PMA/PMW 101, PMW 120, PMW 150, PMW 160, PMW/A 170, PMW 740, PMW 760), two PEO MLB offices, one
PEO Digital office (PMW 205), one NAVSEA program office (PMS 485, PEO Submarines), one explicit
unknown. Seventeen of nineteen are directly documented once the solicitation notices are read;
the one remaining inference (EX08) is a SeaPort-NxG order whose competition never appeared on
SAM.gov. Three Booz Allen orders under
one SeaPort-NxG vehicle resolve to three different offices (EX01, EX04, EX05); one Falconwood
vendor serves PEO MLB and PEO Digital (EX11); one NAVWAR HQ contract is funded by NAVSEA HQ for a
NAVSEA office (EX09); one order names three offices (EX07); one office changed parent twice while
its contract ran (EX12); one modification signed after 2026-05-11 carries a different ancestry
from the base award (EX13).

## 4. What the examples say about the sources

- Award descriptions name a PMW often enough to attribute a large share of NAVWAR HQ actions
  directly; the rest need the LRAE or tear sheets.
- The LRAE's "Existing Contract Number" column is the single best public bridge from a
  forecast to an incumbent contract, and its requirement-office column is the best public
  statement of who owns a follow-on requirement. It lists the vehicle rather than the order in
  some rows (EX01), so matching must try both.
- The funding office separates systems commands but not offices inside NAVWAR, because PEO
  offices fund through NAVWAR HQ.
- NIWC actions attribute to technical-center divisions unless text or funding says otherwise;
  the same program (ADNS) appears both as a PMW 160 production buy and as NIWC Pacific
  in-service engineering work (EX14).
- SAM.gov notice text resolved every `inferred` and `ambiguous` case that had a notice: six
  examples moved to `directly_documented` once the site API was used. What remains
  inferred or unresolved are SeaPort-NxG orders and an NIWC vehicle. The notices' attachment
  lists hold no documents, only PIEE Solicitation Module links, so the statement of work
  itself stays behind the PIEE login; the notice synopsis is what SAM.gov
  publishes and it is enough to name the office.
- The documented public API host (api.sam.gov) answered 404 from two networks; the keyless site
  API that the SAM.gov web application uses (and that the platform's runner already uses) worked for
  notice detail, description text, attachment lists and archived search.

## 5. Replaying through the platform's resolver

The classes above line up with the production resolver's evidence rules
(`program_office_resolver.py`: an office code or explicit ownership language is required;
contracting office, platform, NAICS and vendor cannot create ownership; abstention is a result).
Each record here is a gold candidate: `directly_documented` rows as positives, EX09-EX12 as
hard negatives for the PEO C4I portfolio, EX07 as a multi-program case, EX12 and EX13 as
reorganization cases, EX16 and EX19 as abstentions.

## Replay against the production resolver

Run `python research/tools/replay_attributions.py` with the offices and edges loaded
in a local database. It feeds each example's own passages to
`resolve_program_office()` and compares the answer with the office recorded here.
Offline, no network.

Three things the replay established that reading the resolver would not have:

**An office with no confidence value is invisible to it.** `_organization_has_provenance`
requires `source`, `source_ref`, `observed_at` *and* `confidence`. Loaded with a null
confidence (which is what this research holds, since no source states a number),
every office was skipped. A confidence is part of the interface, not a source value.
The loader sets 1.0 where a source states the
claim outright and 0.5 where it was derived; 0.5 sits below the resolver's
`MIN_HIERARCHY_CONFIDENCE` of 0.8, so a derived edge cannot drive an ancestry walk.

**EX03 and EX06 decline on the same shape.** Both rest on a requirement title that
leads with the office code and says nothing else: "PMW 160 PROFESSIONAL SUPPORT
SERVICES", "PMW/A 170 CYBERSECURITY, ENGINEERING, AND TECHNICAL SUPPORT SERVICES."
The resolver wants an ownership clause around the alias and a bare title has none. It
declines identically whether the text arrives as an attachment fragment or as the
record's own `award_description`, so this is the rule and not an artefact of how the
replay passes evidence. This package reads a code-prefixed requirement title on a
NAVWAR task order as directly documented and the resolver does not.

**EX17 is not a miss.** `PROGRAM_OFFICE_TYPES` is `{"program_office"}`, so a PEO can
never be a candidate. An example whose owner is a PEO is outside what the resolver
asserts, and the replay reports it separately from a disagreement.

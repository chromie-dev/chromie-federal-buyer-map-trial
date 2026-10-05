#!/usr/bin/env python3
"""The agency profile: everything the pipeline writes once per agency, in one place.

    AGENCY=darpa python research/tools/pipeline.py --db darpa_proof
    python research/tools/agency.py --selfcheck

`research/docs/14_reusing_this_for_another_agency.md` lists what travels to a new agency and what does not:
the office code tables, the agency filters, the feed list, the forecast reader and the organization seed.
Until 2026-09-24 those sat as constants inside a dozen tools, each spelling the Navy. This module holds them
per agency, selected by the `AGENCY` environment variable (default `navy`), and every tool reads its constants
from here. The Navy profile reproduces the constants the tools carried, so a Navy build is byte for byte what
it was; a second profile adds an agency without touching the rules.

Artefacts are kept apart: the Navy layer keeps `research/{memory,events,results,sources}`; every other agency
writes under `research/agencies/<key>/` with the same four folders. The document ledger, the saved bytes under
`data/raw/` and the model cassettes are shared, because a retrieval is a retrieval whatever layer reads it;
SAM.gov notice details are the one exception (one folder per agency), because several readers take every
detail file in the folder as this agency's.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY = os.environ.get("AGENCY", "navy").strip().lower() or "navy"

# The committee reports read for directives (the title must end with the act's name, so a Rules Committee report that
# merely mentions the bill does not match), the committee each report kind comes from by chamber, and the House
# committee calendars watched for hearings. Every Defense profile reads the same two acts.
DOD_COMMITTEES = {
    "reports": [("ndaa", r"(?:^|\s)NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR (\d{4})$"),
                ("appropriations", r"^DEPARTMENT OF DEFENSE APPROPRIATIONS (?:BILL|ACT),? (\d{4})$")],
    "names": {("ndaa", "h"): "House Armed Services Committee", ("ndaa", "s"): "Senate Armed Services Committee",
              ("appropriations", "h"): "House Appropriations Committee", ("appropriations", "s"): "Senate Appropriations Committee"},
    "house_feeds": {"AS00": "https://docs.house.gov/Committee/RSS.ashx?Code=AS00", "AP00": "https://docs.house.gov/Committee/RSS.ashx?Code=AP00"}}
# One small business directory for every military department and defense agency.
DOD_SMALL_BUSINESS = "https://business.defense.gov/Work-with-us/Military-Departments-and-Defense-Agencies/"

NAVY = {
    "key": "navy",
    "label": "Department of the Navy",
    "short": "U.S. Navy",
    "database": "navy_proof_ab",
    "agency": {"toptier_code": "097", "toptier_name": "Department of Defense", "toptier_abbreviation": "DOD",
               "subtier_code": "1700", "subtier_name": "Department of the Navy", "subtier_abbreviation": "DON",
               "node": "agency:don",
               # DFARS 204.1603 (FAR 4.1603 outside Defense): an instrument number opens with its issuing office's code.
               "office_code_re": r"[A-Z]\d{4}[A-Z0-9]", "small_business_directory": DOD_SMALL_BUSINESS},
    # FPDS: the contracting offices swept by CONTRACTING_OFFICE_ID, and the agencies swept by FUNDING_AGENCY_ID
    # (an agency whose awards other agencies sign for it; none for the Navy, whose sweep is by office).
    "fpds_offices": {"N00039": "NAVWAR HQ", "N00024": "NAVSEA HQ", "N00014": "ONR", "N00173": "NRL",
                     "N00019": "NAVAIR HQ", "N00421": "NAWCAD Patuxent River", "N68335": "NAWCAD Lakehurst",
                     "N66001": "NIWC Pacific", "N65236": "NIWC Atlantic"},
    "fpds_funding_agencies": {},
    # SAM.gov: the folder the notice details live in, the organization ids swept (id -> office code), the offices
    # swept by code whose id is looked up at sweep time, and the memory node each organization id stands for.
    "sam_dir": "sam_notices",
    "sam_orgs": {"100076586": "N00039", "100076491": "N00024", "100076476": "N00014", "100255323": "N00173",
                 "100076487": "N66001", "100076484": "N65236"},
    "sam_codes": ("N00019", "N00421", "N68335"),
    "sam_org_nodes": {"100076586": "contracting:n00039", "100076491": "contracting:n00024", "100076476": "contracting:n00014",
                      "100255323": "center:nrl", "100076487": "center:niwc-pacific", "100076484": "center:niwc-atlantic"},
    # The DoD SBIR/STTR portal's component, and its `command` column against the memory.
    "sbir_component": "NAVY",
    # A laboratory, warfare center or field activity a notice names is often where the work is done rather than whose
    # it is: it counts as the named office only when the notice names no other (trace.specific_offices).
    "performer_types": ("technical_center", "field_activity"),
    # The office codes a contract description, a budget line or a record excerpt names, one key per match
    # (agency_layers_sql.office_of): "PMW-160", "PMW160" and "PMW 160" all read PMW160.
    "office_key_re": r"\b(PM[WSA])(?:/A)?[ -]?(\d{3})\b",
    # The org types that own requirements beside program offices and PEOs, the event family of each provider only
    # this layer has, and the words that name the buyer rather than a requirement: backtest.FAMILY and GENERIC
    # already hold the Navy's.
    "owner_types": (),
    "families": {},
    "moved_urls": {},  # a directory address whose host no longer resolves -> where its page is saved from
    "generic_words": (),
    "sbir_commands": {"NAVSEA": "command:navsea", "NAVAIR": "command:navair", "NAVWAR": "command:navwar", "SPAWAR": "command:navwar",
                      "ONR": "command:onr", "SSPO": "command:ssp", "SSP": "command:ssp",
                      # the portal's other Navy commands, once their nodes exist (Stage 2, 2026-09-28): both Marine Corps spellings are one command
                      "NAVFAC": "command:navfac", "MCSC": "command:mcsc", "MARCOR": "command:mcsc"},
    # Federal Register API conditions; an agency without a slug searches its name as a term.
    "fedreg_conditions": [("conditions[agencies][]", "navy-department")],
    "fedreg_label": "Department of the Navy",
    "fedreg_name_pattern": None,
    "shared_sources": (),  # the Navy collected everything it reads; the unmarked ledger rows are its own
    # How this agency's SBIR/STTR topic codes, contract numbers and solicitation numbers are written in a text.
    "topic_re": r"\bN\d{2}[0-9AB]-T?\d{3}\b|\bDON\d{2}[BT][ZX]\d{2}-[A-Z]{2}\d{3}\b",  # N251-001 and the FY2026 DON26BZ06-DV088 shape
    "piid_re": r"\bN\d{5}-?\d{2}-?[A-Z]-?\d{4}\b",
    "solicitation_re": r"\bN\d{5}-?\d{2}-?[A-Z]-?[A-Z0-9]{4}\b",
    # Committee reports: the pattern a directive sentence must name.
    "congress_pattern": (r"\bN(?:avy|AVY)\b|\bChief of Naval Operations\b|\bMarine Corps\b|\b(?:NAVSEA|NAVAIR|NAVWAR|NAVSUP|ONR|NRL|NIWC)\b"
                         r"|\bNaval (?:Sea|Air|Supply) Systems Command\b|\bNaval Information Warfare\b|\bOffice of Naval Research\b"
                         r"|\bNaval Research Laboratory\b|\bPEO\b|\bPM[SAW][- ]?\d{2,3}\b"),
    "congress_label": "Navy",
    "committees": DOD_COMMITTEES,
    # oversight.gov and the GAO feed: the agency as the reader is told it, the full-text queries, what a reviewed-agency
    # cell must say and what a title must name.
    "oversight": {"agency": "Department of the Navy (the Navy and the Marine Corps, their systems commands, program offices and field activities)",
                  "queries": ("Navy", "Naval"),
                  "reviewed_re": r"Department of (War|Defense|the Navy)\b|\bNavy\b",
                  "names_re": r"\b(Navy|Naval|NAVSEA|NAVAIR|NAVWAR|NAVSUP|NAVFAC|ONR|Marine Corps|shipbuilding|submarine|carrier)\b",
                  "gao_query": "GAO report Navy shipbuilding acquisition program"},
    # Leaders' words: the speech and testimony archives (None where the agency keeps no archive), the article pattern,
    # what a hearing or conference page must name, and the agency as the reader is told it.
    "remarks": {"speeches": "https://www.navy.mil/Press-Office/Speeches/",
                "testimony": "https://www.navy.mil/Press-Office/Testimony/",
                "article_re": r"^https://www\.navy\.mil/Press-Office/(Speeches/display-speech|Testimony/display-testimony)/Article/\d+/[^?#]+$",
                "names_re": r"\b(Navy|Naval|NAVSEA|NAVAIR|NAVWAR|NAVSUP|NAVFAC|Marine Corps|Seapower|shipbuilding|submarine)\b",
                "agency": "Department of the Navy (the Navy and the Marine Corps, their systems commands, program offices and field activities)",
                "testimony_pages": [],
                # Exa discovery of conference pages naming the agency's officials; the agency's own sites are read
                # by their own sources and left out.
                "conference_query": 'defense conference agenda speakers Navy "Chief of Naval Operations" OR "Program Executive Officer" OR "Naval Sea Systems Command" keynote panel',
                "own_domains": ["navy.mil", "dvidshub.net"],
                "providers": {"speech": "navy_mil_speeches", "testimony": "navy_mil_speeches", "statement": "house_committee_repository",
                              "conference": "conference_pages_exa"}},
    # News: the feeds polled, the official publishers by host, and the terms an article must carry to be kept.
    "news": {"feeds": [
        {"publisher": "NAVWAR", "url": "https://www.navwar.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1114&max=25"},
        {"publisher": "U.S. Navy", "url": "https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1075&max=25"},
        {"publisher": "DVIDS", "url": "https://www.dvidshub.net/rss/news"},
        {"publisher": "Department of Defense", "url": "https://www.war.gov/News/Contracts/", "index": True},
        {"publisher": "PAE Mission Systems", "url": "https://www.missionsystems.navy.mil/News/", "index": True}],
        "official_names": {
            "www.navy.mil": "U.S. Navy", "www.navwar.navy.mil": "NAVWAR", "www.dvidshub.net": "DVIDS",
            "www.missionsystems.navy.mil": "PAE Mission Systems", "www.paemaritime.navy.mil": "PAE Maritime",
            "www.secnav.navy.mil": "Secretary of the Navy", "www.doncio.navy.mil": "DON CIO",
            "www.war.gov": "Department of Defense", "www.defense.gov": "Department of Defense",
            "www.navsea.navy.mil": "NAVSEA", "www.onr.navy.mil": "Office of Naval Research"},
        "standing_terms": ["NAVWAR", "SPAWAR", "NIWC", "Naval Information Warfare", "PEO C4I", "PEO Digital",
                           "Portfolio Acquisition Executive", "Program Executive Office", "program office",
                           "Direct Reporting Program Manager", "acquisition", "contract award"]},
    # GAO's bid-protest docket: the listing address (the agency filter is in it) and the agency as the docket prints it.
    "protests": {"listing": "https://www.gao.gov/legal/bid-protests/search?agency=Department%20of%20the%20Navy&page={page}",
                 "agency": "Department of the Navy", "max_pages": 60},
    # Budget: the justification-book exhibit the reader parses and the words the book prints beside "PB yyyy".
    # evidence_host: the host the Navy evidence rows were first written with (the loaded rows keep it); another agency's
    # rows carry the book URL's own host.
    "budget": {"exhibit": "P-40", "pb_label": "Navy", "books_dir": "jbooks", "provider": "don_budget_justification_books",
               "evidence_host": "secnav.navy.mil",
               # The Comptroller display spreadsheets: the Navy's accounts end in N (1319N RDT&E,N; 1810N OPN; 1109N Procurement, Marine Corps).
               "display": {"account_suffix": "N"}},
    # People: the department node a person falls to, and the titles read as executive.
    "people": {"department": "agency:don",
               "executive": ("secretary of the navy", "assistant secretary", "chief of naval operations", "commandant", "vice chief",
                             "under secretary", "deputy secretary", "executive officer", "commander,", "commander of", "director",
                             "portfolio acquisition executive"),
               # The source keys people.py has always written for the Navy's remarks documents (a statement under the
               # speeches key, testimony under the House key); kept as they are so the loaded rows keep their ids.
               # The loader's REMARKS_PROVIDERS (remarks.providers) reads them the other way round: an open item
               # for the Navy layer, recorded in DECISIONS.md (2026-09-25). None means the remarks providers apply.
               "remarks_providers": {"speech": "navy_mil_speeches", "statement": "navy_mil_speeches", "testimony": "house_committee_repository",
                                     "conference": "conference_pages_exa"},
               "staff_listing": None},
    # Forecast: the datapack releases the tracer reads (a glob under datapack/), the forecast providers by activity,
    # and whether the memory stage reads a forecast at all.
    "forecast": {"pack_glob": "lrae_*", "label": "Long Range Acquisition Estimate", "short": "LRAE", "providers": {"navwar": "navwar_lrae_annex25", "navsea": "navsea_lrae_annex25",
                                                              "onr": "onr_lrae_annex25", "nrl": "onr_lrae_annex25"},
                 "memory_tool": "org_memory_lrae.py"},
    # The offices whose forecast cells are judged for precision (research/docs/00).
    "pilot_offices": ("PMA/PMW 101", "PMW 120", "PMW 130", "PMW 150", "PMW 160", "PMW/A 170", "PMW 740"),
    # The coverage grid's organizations (the families are the layer's and do not change).
    "coverage_orgs": ["NAVSEA", "NAVAIR", "NAVWAR", "ONR", "NIWC Pacific", "NIWC Atlantic", "NRL", "SSP", "PAE Mission Systems", "DRPM RAS",
                      "PEO C4I", "PEO Digital", "PEO EIS", "PEO IWS", "PEO MLB", "PEO Ships", "PEO SSN", "PEO Submarines", "PEO USC", "PEO UWS",
                      "PEO Carriers", "NAVFAC", "MSC"],
    # The memory node behind each row of the generated matrix (coverage.py matrix); None until Stage 2 adds the node from a
    # saved page, and the row's cells then read not_started.
    "coverage_org_nodes": {"NAVSEA": "command:navsea", "NAVAIR": "command:navair", "NAVWAR": "command:navwar", "ONR": "command:onr",
                           "NIWC Pacific": "center:niwc-pacific", "NIWC Atlantic": "center:niwc-atlantic", "NRL": "center:nrl", "SSP": "command:ssp",
                           "PAE Mission Systems": "pae:mission-systems", "DRPM RAS": "drpm:ras", "PEO C4I": "peo:c4i", "PEO Digital": "peo:digital",
                           "PEO EIS": "peo:eis", "PEO IWS": "peo:iws", "PEO MLB": "peo:mlb", "PEO Ships": "peo:ships", "PEO SSN": "peo:ssn",
                           "PEO Submarines": "peo:submarines", "PEO USC": "peo:usc", "PEO UWS": "peo:uws", "PEO Carriers": "peo:carriers",
                           "NAVFAC": "command:navfac", "MSC": "command:msc"},
    # Sources that speak for the whole department in a family: a cell they alone feed is Adjacent, never Live.
    "coverage_department_wide": {"oversight": ["oversight_gov_reports", "gao_reports"], "leaders": ["navy_mil_speeches"],
                                 "congress": ["house_committee_repository", "govinfo_api"], "conference": ["conference_pages_exa"],
                                 "news": ["news_articles_exa"], "protest": ["gao_bid_protests"], "programs": ["sbir_sttr_topics"],
                                 "hiring": ["usajobs_historic_joa"]},
    # Reading the frozen corpus (pages.py, office_wiki.py, people.py): how the agency writes a program office code in a
    # notice or after a person's name, and a hull or platform designator to strip from a title (none where the agency
    # has none). The contract number pattern is `piid_re` above and the buyer's own names are `generic_words`.
    "reading": {"office_code_re": r"\b(?:PMW|PMS|PMA|IWS)[ /-]*(?:A[ -]*)?\d{2,3}(?:\.\d)?\b|\bPEO [A-Z][A-Za-z0-9]+",
                "hull_re": r"\bUSS\s+[A-Z][A-Za-z .'-]*?\s*\(?[A-Z]{2,4}[\s-]*\d{1,4}\)?|\b[A-Z]{2,4}[\s-]+\d{1,4}\b",
                # A solicitation number written into a forecast title or an article, read with its spaces removed.
                "rfp_re": r"N\d{5}-?\d{2}-?R-?[A-Z]?-?\d{3,4}(?![0-9])"},
    # Hiring (research/tools/jobs.py): USAJobs files every announcement under a department code and an agency code of
    # its own; the codes here are the commands the layer studies, each mapped to the memory node it names, as the
    # historic announcement API listed them on 2026-09-27. The NIWCs and NRL announce under their command's code.
    "hiring": {"usajobs_department_code": "NV",
               "usajobs_agency_codes": {"NV39": "command:navwar", "NV24": "command:navsea", "NV19": "command:navair",
                                        "NV14": "command:onr", "NV30": "command:ssp"},
               "note": "NAVWAR (NV39) listed 406 announcements in the twelve months to 2026-09-27; the Marine Corps, the fleets and the shore commands announce under codes of their own and are not swept",
               # Vendor postings (vendor_jobs.py): the incumbents with live awards at the swept contracting offices are
               # asked through RouterGrowth; `vendor_watch` names companies to ask beside them ({"uei", "name", "hosts"}).
               "vendor_jobs": True, "vendor_watch": []},
}

DARPA = {
    "key": "darpa",
    "label": "Defense Advanced Research Projects Agency",
    "short": "DARPA",
    "database": "darpa_proof",
    "agency": {"toptier_code": "097", "toptier_name": "Department of Defense", "toptier_abbreviation": "DOD",
               "subtier_code": "97AE", "subtier_name": "Defense Advanced Research Projects Agency", "subtier_abbreviation": "DARPA",
               "node": "agency:darpa",
               # DFARS 204.1603 (FAR 4.1603 outside Defense): an instrument number opens with its issuing office's code.
               "office_code_re": r"HR\d{4}", "small_business_directory": DOD_SMALL_BUSINESS},
    # One contracting office (HR0011, the Contracts Management Office); awards DARPA funds through other agencies'
    # offices are swept by funding agency (research/docs/19, section 2: 41 to 50 of the FY2026 base awards).
    "fpds_offices": {"HR0011": "DARPA Contracts Management Office"},
    "fpds_funding_agencies": {"97AE": "DARPA"},
    "sam_dir": "sam_notices_darpa",
    "sam_orgs": {"500035490": "HR0011"},
    "sam_codes": (),
    "sam_org_nodes": {"500035490": "contracting:hr0011", "300000412": "agency:darpa"},
    "sbir_component": "DARPA",
    # The portal's command field names the DARPA office that sponsors a topic, where it names one.
    "sbir_commands": {"BTO": "office:bto", "DSO": "office:dso", "IPTO": "office:ipto", "I2O": "office:i2o", "MTO": "office:mto",
                      "MXO": "office:mxo", "STO": "office:sto", "TTO": "office:tto"},
    # Sources this layer reads from the shared collection rather than its own sweeps (the portal pages and the
    # committee reports the Navy layer saved): their documents count for this layer though their notes are unmarked.
    "shared_sources": ("sbir_sttr_topics", "govinfo_api", "diu_cso_solicitations"),  # DIU openings: one collection, read by every DoD layer
    # The event family of each provider only this layer has (backtest.FAMILY holds the shared and the Navy ones), and
    # the words that name this buyer or its paperwork rather than a requirement.
    # The technical offices own DARPA's programs, as the program offices own the Navy's.
    "owner_types": ("technical_office",),
    "performer_types": (),  # the technical offices own DARPA's programs; none is only where work is done
    # A technical office as awards and notices write it ("DARPA STO ALBATROSS", "BTO SEEDLING", "(STO3)"). CSO is left
    # out: it is the commercial solutions opening as often as the office.
    "office_key_re": r"\b(BTO|DSO|I2O|MTO|MXO|STO|TTO|IPTO|ACO|APO)\d{0,2}\b",
    "families": {"darpa_site": "organization", "darpa_staff_listing": "organization", "dod_comptroller_budget_materials": "budget"},
    "moved_urls": {},  # a directory address whose host no longer resolves -> where its page is saved from
    # Topic codes as the portal writes them (HR001119S0035-14, HR0011SB20234XL-01, DPA26BZ01-DV003). Contract and
    # solicitation numbers under any DoD office, since other agencies' offices sign most DARPA-funded awards, and an
    # Other Transaction carries a digit (9) where a contract carries its type letter.
    "topic_re": r"\b(?:HR0011[0-9A-Z]{6,9}|DPA\d{2}[BT]Z\d{2})-[A-Z]{0,2}\d{2,3}\b",
    "piid_re": r"\b[A-Z][A-Z0-9]{5}-?\d{2}-?[A-Z0-9]-?\d{4}\b",
    "solicitation_re": r"\b[A-Z][A-Z0-9]{5}-?\d{2}-?[A-Z]-?[A-Z0-9]{4}\b",
    "generic_words": ("darpa", "defense advanced research projects agency", "broad agency announcement", "baa",
                      "program announcement", "other transaction", "ot", "proposers day"),
    # The Federal Register has no agency slug for DARPA (the agencies endpoint answers 404, research/docs/19); a term
    # search for the full name stands in.
    "fedreg_conditions": [("conditions[term]", "\"Defense Advanced Research Projects Agency\"")],
    "fedreg_label": "Defense Advanced Research Projects Agency",
    # A term search returns every document whose text mentions the agency; only the ones whose title, action or
    # abstract names it are this layer's documents (the Navy's slug search needs no such filter: None).
    "fedreg_name_pattern": r"\bDARPA\b|\bDefense Advanced Research Projects Agency\b",
    "congress_pattern": r"\bDARPA\b|\bDefense Advanced Research Projects Agency\b",
    "congress_label": "DARPA",
    "committees": DOD_COMMITTEES,
    "oversight": {"agency": "Defense Advanced Research Projects Agency (DARPA, its technical offices and its Contracts Management Office)",
                  "queries": ("DARPA", "Defense Advanced Research Projects Agency"),
                  # The OIG lists every report under "Department of War", so the department name admits nothing here;
                  # a report is DARPA's when the agency reviewed or the title names DARPA.
                  "reviewed_re": r"\bDARPA\b|Defense Advanced Research Projects Agency",
                  "names_re": r"\b(DARPA|Defense Advanced Research Projects Agency)\b",
                  "gao_query": "GAO report DARPA research program"},
    # DARPA keeps no speech archive; its budgets-and-testimony page lists the Director's statements as submitted.
    "remarks": {"speeches": None, "testimony": None, "article_re": r"^(?!)",
                "names_re": r"\b(DARPA|Defense Advanced Research Projects Agency)\b",
                "agency": "Defense Advanced Research Projects Agency (DARPA, its technical offices and its Contracts Management Office)",
                "testimony_pages": ["https://www.darpa.mil/about/budgets-testimony"],
                "conference_query": 'defense conference agenda speakers DARPA "Defense Advanced Research Projects Agency" director OR "program manager" keynote panel',
                "own_domains": ["darpa.mil"],
                "providers": {"speech": "darpa_site", "testimony": "darpa_site", "statement": "darpa_site", "conference": "conference_pages_exa"}},
    "news": {"feeds": [
        {"publisher": "DARPA", "url": "https://www.darpa.mil/rss.xml"},
        {"publisher": "DARPA", "url": "https://www.darpa.mil/rss/opportunities.xml"},
        {"publisher": "Department of Defense", "url": "https://www.war.gov/News/Contracts/", "index": True}],
        "official_names": {"www.darpa.mil": "DARPA", "www.war.gov": "Department of Defense", "www.defense.gov": "Department of Defense"},
        "standing_terms": ["DARPA", "Defense Advanced Research Projects Agency", "Broad Agency Announcement", "program manager",
                           "Proposers Day", "Other Transaction", "acquisition", "contract award"]},
    "protests": {"listing": "https://www.gao.gov/legal/bid-protests/search?agency=Defense%20Advanced%20Research%20Projects%20Agency&page={page}",
                 "agency": "Defense Advanced Research Projects Agency", "max_pages": 10},
    # DARPA is funded through research, development, test and evaluation alone: one R-1 justification book a year,
    # whose pages are Exhibit R-2 program elements.
    "budget": {"exhibit": "R-2", "pb_label": "Defense Advanced Research Projects Agency", "books_dir": "jbooks_darpa",
               "provider": "dod_comptroller_budget_materials",
               # Funded inside the Defense-Wide RDT&E account (0400D); DARPA's program elements end in E.
               "display": {"account_suffix": "D", "pe_re": r"\d{7}E$"}},
    "people": {"department": "agency:darpa",
               "executive": ("director", "deputy director", "office director", "chief of staff", "general counsel"),
               # darpa.mil/json/staff: role, office and start date as the agency publishes them; a record's page path
               # is relative to the site.
               "staff_listing": "https://www.darpa.mil/json/staff", "staff_base_url": "https://www.darpa.mil",
               # darpa.mil/json/program: every program the agency lists, current and completed, with its office and
               # the program manager by name, one row per research topic (programs.py).
               "program_listing": "https://www.darpa.mil/json/program", "program_current": "Current",
               "program_fields": {"id": "nid", "title": "title", "status": "field_program_status", "office": "field_taxonomy_office",
                                  "manager": ("field_program_manager__field_first_name", "field_program_manager__field_last_name"),
                                  "manager_role": "field_program_manager__field_role", "path": "view_node", "topics": "field_research_topics"},
               "remarks_providers": None},
    "forecast": {"pack_glob": None, "label": "", "short": "", "providers": {}, "memory_tool": "org_memory_darpa.py"},
    # The technical offices by the acronym the loaded organization carries (its office code), as the Navy's by code.
    "pilot_offices": ("BTO", "DSO", "IPTO", "MXO", "STO", "TTO"),
    "coverage_orgs": ["BTO", "DSO", "IPTO", "MXO", "STO", "TTO", "CMO"],
    "coverage_org_nodes": {},  # the matrix is written by hand (or by its own script) until the nodes are named here
    "coverage_department_wide": {},
    # A DARPA notice names its office by acronym (the six technical offices, the four former ones the notices still
    # name, and the staff offices); DARPA has no hull designators.
    "reading": {"office_code_re": r"\b(?:BTO|DSO|I2O|IPTO|MTO|MXO|STO|TTO|DIRO|CMO|SBPO|ACO|APO|CSO)\b",
                "hull_re": r"(?!)",
                # ponytail: the Navy pattern this layer was built with; its own moves news links and with them paid office pages.
                "rfp_re": r"N\d{5}-?\d{2}-?R-?[A-Z]?-?\d{3,4}(?![0-9])"},
    # USAJobs lists DARPA as DD13 under the Department of Defense: five announcements between April and September 2026.
    # The agency recruits its program managers through darpa.mil/careers, a host that refuses this address.
    "hiring": {"usajobs_department_code": "DD", "usajobs_agency_codes": {"DD13": "agency:darpa"},
               "note": "DD13 listed five announcements between 2026-04-01 and 2026-09-27; darpa.mil/careers refuses this address and is not read",
               "vendor_jobs": True, "vendor_watch": []},
}

ARMY = {
    "key": "army",
    "label": "Department of the Army",
    "short": "U.S. Army",
    "database": "army_proof",
    "agency": {"toptier_code": "097", "toptier_name": "Department of Defense", "toptier_abbreviation": "DOD",
               "subtier_code": "2100", "subtier_name": "Department of the Army", "subtier_abbreviation": "DA",
               "node": "agency:army",
               # DFARS 204.1603 (FAR 4.1603 outside Defense): an instrument number opens with its issuing office's code.
               "office_code_re": r"W[A-Z0-9]{5}", "small_business_directory": DOD_SMALL_BUSINESS},
    # The Army Contracting Command offices that sign most acquisition-program awards (FPDS, FY2026), by DoDAAC.
    "fpds_offices": {"W56KGY": "ACC-APG", "W15P7T": "ACC-APG (CECOM)", "W91CRB": "ACC-APG (Natick)", "W31P4Q": "ACC-RSA",
                     "W58RGZ": "ACC-RSA (AMCOM)", "W912CH": "ACC-DTA", "W15QKN": "ACC-NJ (Picatinny)", "W900KK": "ACC-ORL"},
    "fpds_funding_agencies": {},
    "sam_dir": "sam_notices_army",
    "sam_orgs": {"500043662": "W56KGY", "500038571": "W15P7T", "500043793": "W91CRB", "500038573": "W31P4Q",
                 "500045573": "W58RGZ", "100255245": "W912CH", "500045967": "W15QKN", "500036903": "W900KK"},
    "sam_codes": (),
    "sam_org_nodes": {"500043662": "contracting:w56kgy", "500038571": "contracting:w15p7t", "500043793": "contracting:w91crb",
                      "500038573": "contracting:w31p4q", "500045573": "contracting:w58rgz", "100255245": "contracting:w912ch",
                      "500045967": "contracting:w15qkn", "500036903": "contracting:w900kk", "300000201": "agency:army"},
    "sbir_component": "ARMY",
    # The portal's command field against the memory; a code the memory has no node for falls to the department.
    "sbir_commands": {"ASA(ALT)": "office:asaalt"},
    # The DEVCOM centers and laboratories are often where the work is done rather than whose requirement it is.
    "performer_types": ("technical_center", "field_activity"),
    "office_key_re": r"(?!)",  # the forecast writes its program offices by name; no code pattern yet
    "shared_sources": ("sbir_sttr_topics", "govinfo_api", "diu_cso_solicitations"),  # DIU openings: one collection, read by every DoD layer
    "owner_types": (),
    "families": {"army_asaalt_chart": "organization", "amc_acquisition_forecast": "forecast", "usace_acquisition_forecast": "forecast",
                 "army_national_guard_forecast": "forecast", "army_budget_materials": "budget"},
    # A directory address whose host no longer resolves -> where the same page is saved from. The Department's small
    # business directory still lists osbp.army.mil, which has no DNS record; the office's page is www.army.mil/osbp.
    "moved_urls": {"http://osbp.army.mil/": "https://www.army.mil/osbp"},
    # Topic codes as the portal writes them (A20-179, A214-006, A20B-T018, A254-P007, ARM26BX06-NV012); contract and
    # solicitation numbers under an Army DoDAAC (W...).
    "topic_re": r"\b(?:A\d{2}[0-9A-Z]?-[A-Z]?\d{3}|ARM\d{2}[A-Z]{2}\d{2}-[A-Z]{2}\d{3})\b",
    "piid_re": r"\bW[A-Z0-9]{5}-?\d{2}-?[A-Z]-?\d{4}\b",
    "solicitation_re": r"\bW[A-Z0-9]{5}-?\d{2}-?[A-Z]-?[A-Z0-9]{4}\b",
    "generic_words": ("army", "u.s. army", "department of the army", "army contracting command", "acc", "army materiel command",
                      "amc", "sources sought", "industry day"),
    "fedreg_conditions": [("conditions[agencies][]", "army-department")],
    "fedreg_label": "Department of the Army",
    "fedreg_name_pattern": None,
    "congress_pattern": (r"\bArmy\b|\bARMY\b|\bASA\(ALT\)|\bArmy (?:Contracting|Materiel|Futures) Command\b"
                         r"|\b(?:TACOM|AMCOM|CECOM|DEVCOM|JPEO)\b|\bPortfolio Acquisition Executive\b"),
    "congress_label": "Army",
    "committees": DOD_COMMITTEES,
    "oversight": {"agency": "Department of the Army (the Army, its commands, its portfolio and program offices and its contracting centers)",
                  "queries": ("Army",),
                  "reviewed_re": r"Department of (War|Defense|the Army)\b|\bArmy\b",
                  "names_re": r"\b(Army|ASA\(ALT\)|TACOM|AMCOM|CECOM|DEVCOM|Army Contracting Command|Army Materiel Command)\b",
                  "gao_query": "GAO report Army acquisition program"},
    # army.mil refuses this address, so the speech archive is not read until a hosted-browser route is registered.
    "remarks": {"speeches": None, "testimony": None, "article_re": r"^(?!)",
                "names_re": r"\b(Army|ASA\(ALT\)|Army Contracting Command|Army Materiel Command|Army Futures Command)\b",
                "agency": "Department of the Army (the Army, its commands, its portfolio and program offices and its contracting centers)",
                "testimony_pages": [],
                "conference_query": 'defense conference agenda speakers Army "Program Executive Officer" OR "Army Materiel Command" OR "ASA(ALT)" keynote panel',
                "own_domains": ["army.mil", "dvidshub.net"],
                "providers": {"speech": "army_mil_speeches", "testimony": "house_committee_repository", "statement": "house_committee_repository",
                              "conference": "conference_pages_exa"}},
    "news": {"feeds": [
        {"publisher": "DVIDS", "url": "https://www.dvidshub.net/rss/unit/ACC"},
        {"publisher": "Department of Defense", "url": "https://www.war.gov/News/Contracts/", "index": True}],
        "official_names": {"www.dvidshub.net": "DVIDS", "www.army.mil": "U.S. Army", "www.war.gov": "Department of Defense",
                           "www.defense.gov": "Department of Defense"},
        "standing_terms": ["Army Contracting Command", "Portfolio Acquisition Executive", "Program Executive Office",
                           "Capability Program Executive", "program manager", "acquisition", "contract award"]},
    "protests": {"listing": "https://www.gao.gov/legal/bid-protests/search?agency=Department%20of%20the%20Army&page={page}",
                 "agency": "Department of the Army", "max_pages": 60},
    "budget": {"exhibit": "P-40", "pb_label": "Army", "books_dir": "jbooks_army", "provider": "army_budget_materials",
               "display": {"account_suffix": "A"}},  # 2040A RDT&E,A; 2031A Aircraft Procurement, Army
    "people": {"department": "agency:army",
               "executive": ("secretary of the army", "assistant secretary", "under secretary", "chief of staff", "commanding general",
                             "portfolio acquisition executive", "program executive", "director"),
               "remarks_providers": None, "staff_listing": None},
    # The forecasts the Office of Small Business Programs links (AMC, USACE, the National Guard) are the forecast; AMC's
    # Command and PM / Directorate columns and the ASA(ALT) chart are the organization memory's sources. One Path.glob
    # pattern each: the packs amc_, ngb_ and usace_, not the Navy's lrae_ packs or another agency's in the same directory.
    "forecast": {"pack_glob": ("amc_20??-??", "usace_20??-??", "ngb_20??-??"), "label": "acquisition forecast", "short": "forecast", "memory_tool": "org_memory_army.py",
                 "providers": {"amc": "amc_acquisition_forecast", "usace": "usace_acquisition_forecast", "ngb": "army_national_guard_forecast"},
                 "contract_re": r"W[A-Z0-9]{5}\d{2}[A-Z]\d{4}(?!\d)"},  # Army PIIDs as the forecast writes them, hyphens gone
    "pilot_offices": ("PEO AVIATION", "PEO MS", "JPEO AA", "PEO SOLDIER", "PEO GCS", "CPE C2IN"),
    "coverage_orgs": ["ASA(ALT)", "ACC", "AMC", "PEO AVIATION", "PEO MS", "JPEO AA", "PEO SOLDIER", "PEO GCS", "CPE C2IN"],
    "coverage_org_nodes": {},  # the matrix is written by hand (or by its own script) until the nodes are named here
    "coverage_department_wide": {},
    # An Army notice names its office as a PEO, JPEO, CPE, PM or PdM followed by the office's word (PEO Aviation, PM UAS);
    # the Army has no hull designators. A first pattern from the forecast's office names, not yet run against a corpus.
    "reading": {"office_code_re": r"\b(?:J?PEO|CPE|P[dD]M|PM) [A-Z][A-Za-z0-9&()/-]+",
                "hull_re": r"(?!)",
                # ponytail: the Navy pattern this layer was built with; its own moves news links and with them paid office pages.
                "rfp_re": r"N\d{5}-?\d{2}-?R-?[A-Z]?-?\d{3,4}(?![0-9])"},
    # USAJobs codes as the September 2026 listing wrote them: the Acquisition Support Center (ARAE) announces the acquisition
    # workforce's vacancies and names a portfolio acquisition executive in the subelement; TACOM, AMCOM and CECOM are
    # AMC's commands; the Secretariat is ARSA. Army Contracting Command showed no code of its own in that listing.
    "hiring": {"usajobs_department_code": "AR",
               "usajobs_agency_codes": {"ARAE": "agency:army", "ARSA": "agency:army", "ARX7": "command:amc", "ARX6": "command:amc", "ARX8": "command:amc"},
               "note": "the subelement names the PEO or PAE where the listing states one; Army Contracting Command had no code in the 2026-09 listing",
               "vendor_jobs": True, "vendor_watch": []},
}

AIRFORCE = {
    "key": "airforce",
    "label": "Department of the Air Force",
    "short": "U.S. Air Force",
    "database": "airforce_proof",
    # One department, two services: the Space Force's acquisition (Space Systems Command) buys under the same subtier
    # code 5700, and USAspending, FPDS and SAM.gov file it as the Department of the Air Force (research/docs/20).
    "agency": {"toptier_code": "097", "toptier_name": "Department of Defense", "toptier_abbreviation": "DOD",
               "subtier_code": "5700", "subtier_name": "Department of the Air Force", "subtier_abbreviation": "USAF",
               "node": "agency:daf", "office_code_re": r"FA\d{4}", "small_business_directory": DOD_SMALL_BUSINESS},
    "moved_urls": {},  # a directory address whose host no longer resolves -> where its page is saved from
    "committees": DOD_COMMITTEES,
    # FPDS: the contracting offices of the acquisition centers, as the feed names them (first entry of each office's
    # feed, 2026-09-25): AFLCMC's program-office contracting at Hanscom, Eglin and Wright-Patterson, the Air Force
    # Research Laboratory's directorates, Space Systems Command's program executive offices, and the Sustainment
    # Center's three complexes. Installation contracting squadrons (the CONS) and the operational contracting
    # divisions (PZIO) buy base services and are not swept. FY2025 volumes: under 490 base awards for every office probed.
    "fpds_offices": {"FA8730": "AFLCMC/HBBK Kessel Run (Hanscom)", "FA8702": "AFLCMC/PZE Hanscom", "FA8213": "AFLCMC/EBHK Weapons (Eglin)",
                     "FA8621": "AFLCMC/WNSK Simulators", "FA8615": "AFLCMC/WAMK F-16", "FA8611": "AFLCMC/WAUK F-22",
                     "FA8620": "AFLCMC/WIJK Big Safari", "FA8622": "AFLCMC/AZS EPASS", "FA8656": "AFLCMC/EBX",
                     "FA8650": "AFRL/PZL Wright-Patterson", "FA8750": "AFRL/RIK Rome", "FA8751": "AFRL/RIKO Rome",
                     "FA9453": "AFRL/RVK Kirtland", "FA9550": "AFRL/AFOSR", "FA8651": "AFRL/RWK Eglin",
                     "FA8806": "SSC/BCK Battle Management C3", "FA8807": "SSC/CGK Military Communications and PNT",
                     "FA8808": "SSC Military Satellite Communications", "FA8810": "SSC/SNK Space Sensing",
                     "FA8814": "SSC Space Development, Test and Planning", "FA8818": "SSC/AAK Assured Access to Space",
                     "FA8819": "SSC/SZK Space Domain Awareness and Combat Power", "FA2518": "USSF SpOC/SAIO",
                     "FA8101": "AFSC Oklahoma City", "FA8201": "AFSC Ogden", "FA8501": "AFSC Warner Robins"},
    "fpds_funding_agencies": {},
    "sam_dir": "sam_notices_airforce",
    # SAM.gov organization ids resolved from the code's own notices on 2026-09-25 (sam_notices.resolve_orgs); an office
    # whose 25 newest hits showed no id is swept by code at sweep time.
    "sam_orgs": {"500019028": "FA8730", "500019464": "FA8213", "500019412": "FA8621", "500019547": "FA8615", "500019553": "FA8622",
                 "100025313": "FA8650", "500019728": "FA8750", "100512674": "FA8751", "100028651": "FA9453", "500021994": "FA9550",
                 "100191253": "FA8651", "500038477": "FA8806", "500038471": "FA8807", "500038478": "FA8808", "500038475": "FA8810",
                 "500038525": "FA8818", "500038482": "FA8819", "500041436": "FA2518", "500021018": "FA8101", "500042213": "FA8201",
                 "500022527": "FA8501"},
    "sam_codes": ("FA8702", "FA8611", "FA8620", "FA8656", "FA8814"),
    "sam_org_nodes": {"500019028": "contracting:fa8730", "500019464": "contracting:fa8213", "500019412": "contracting:fa8621",
                      "500019547": "contracting:fa8615", "500019553": "contracting:fa8622", "100025313": "contracting:fa8650",
                      "500019728": "contracting:fa8750", "100512674": "contracting:fa8751", "100028651": "contracting:fa9453",
                      "500021994": "contracting:fa9550", "100191253": "contracting:fa8651", "500038477": "contracting:fa8806",
                      "500038471": "contracting:fa8807", "500038478": "contracting:fa8808", "500038475": "contracting:fa8810",
                      "500038525": "contracting:fa8818", "500038482": "contracting:fa8819", "500041436": "contracting:fa2518",
                      "500021018": "contracting:fa8101", "500042213": "contracting:fa8201", "500022527": "contracting:fa8501"},
    # The DoD SBIR/STTR portal files the Department of the Air Force, the Space Force included, under USAF (1,582 topics on
    # the index pages saved 2026-09-22); its `command` column names AFMC, the laboratory's directorates (AFRL-RY, AFRL-RX,
    # AFRL-711HPW: sbir.command_org reads the stem before the hyphen), SSC, AFWERX and AIR FORCE.
    "sbir_component": "USAF",
    "sbir_commands": {"AFMC": "command:afmc", "AFRL": "center:afrl", "SSC": "command:ssc", "AFLCMC": "command:aflcmc", "AFSC": "command:afsc",
                      "PEO-WEAPONS": "peo:weapons", "PEO-TRAINING": "peo:training", "AFWERX": "command:afmc",
                      "AIR FORCE": "agency:daf", "SPACE FORCE": "agency:daf"},
    # The laboratory directorates perform research under their own announcements; a program office or program
    # executive office owns the requirement where a notice names one.
    "performer_types": ("technical_center",),
    "shared_sources": ("sbir_sttr_topics", "govinfo_api", "diu_cso_solicitations"),  # DIU openings: one collection, read by every DoD layer
    "owner_types": (),
    # The department's sites carry news articles (af.mil/News, the commands' newsrooms), read as the news family; the
    # organization family is SAM.gov's records.
    "families": {"daf_site": "news", "sam_gov_organizations": "organization", "af_mil_speeches": "leaders",
                 "daf_budget_justification_books": "budget"},
    # Topic codes as the portal writes them (AF201-D001, AF254-0813, SF254-D803, AF20A-T001, DAF26BZ06-DV037);
    # contract and solicitation numbers under an Air Force office (FA....-yy-L-nnnn; an Other Transaction carries 9).
    "topic_re": r"\b(?:AF|SF)\d{2}[0-9A-D]?-[A-Z]?\d{3,4}\b|\bDAF\d{2}[BT]Z\d{2}-[A-Z]{2}\d{3}\b",
    "piid_re": r"\bFA\d{4}-?\d{2}-?[A-Z0-9]-?\d{4}\b",
    "solicitation_re": r"\bFA\d{4}-?\d{2}-?[A-Z]-?[A-Z0-9]{4}\b",
    "generic_words": ("air force", "u.s. air force", "department of the air force", "daf", "usaf", "space force", "u.s. space force",
                      "ussf", "afmc", "aflcmc", "afrl", "ssc", "space systems command", "air force research laboratory",
                      "air force materiel command", "dod", "department of defense", "department of war"),
    "fedreg_conditions": [("conditions[agencies][]", "air-force-department")],
    "fedreg_label": "Department of the Air Force",
    "fedreg_name_pattern": None,
    "congress_pattern": (r"\bAir Force\b|\bSpace Force\b|\bUSAF\b|\bUSSF\b|\bSecretary of the Air Force\b|\bChief of Space Operations\b"
                         r"|\b(?:AFMC|AFLCMC|AFRL|AFSC|AFNWC|AFOSR)\b|\bSpace Systems Command\b|\bAir Force Research Laboratory\b"
                         r"|\bSpace Rapid Capabilities Office\b"),
    "congress_label": "Air Force",
    # oversight.gov files every DoD OIG report under "Department of War", so that name alone does not make a report this
    # layer's: the agency reviewed must name the department, or the title the Air Force or the Space Force.
    "oversight": {"agency": "Department of the Air Force (the Air Force and the Space Force, their materiel and systems commands, program executive offices and laboratories)",
                  "queries": ("Air Force", "Space Force"),
                  "reviewed_re": r"Department of the Air Force\b|\bAir Force\b|\bSpace Force\b",
                  "names_re": r"\b(Air Force|Space Force|USAF|USSF|AFMC|AFLCMC|AFRL|Space Systems Command|Sentinel|B-21|F-35|KC-46|E-7|NGAD|CCA)\b",
                  "gao_query": "GAO report Air Force acquisition program"},
    # af.mil publishes no speech archive: its /News/Speeches/ address, taken through the hosted browser on 2026-09-25,
    # renders the department's general news listing (100 articles, titled "News"), so leaders' words reach this layer as
    # news articles and as House committee testimony (research/docs/20). No archive index is polled.
    "remarks": {"speeches": None,
                "conference_query": 'defense conference agenda speakers "Air Force" OR "Space Force" "Program Executive Officer" OR "Space Systems Command" OR "Life Cycle Management Center" keynote panel',
                "own_domains": ["af.mil", "spaceforce.mil", "dvidshub.net"],
                "testimony": None,
                "article_re": r"^https://www\.af\.mil/(?:News|About-Us)/Speeches/Display/Article/\d+/[^?#]+$",
                "names_re": r"\b(Air Force|Space Force|USAF|USSF|AFMC|AFLCMC|AFRL|Space Systems Command|airmen|guardians|Sentinel|B-21|F-35|KC-46|NGAD)\b",
                "agency": "Department of the Air Force (the Air Force and the Space Force, their materiel and systems commands, program executive offices and laboratories)",
                "testimony_pages": [],
                "providers": {"speech": "af_mil_speeches", "testimony": "af_mil_speeches", "statement": "house_committee_repository",
                              "conference": "conference_pages_exa"}},
    # News: the department's and the Space Force's own feeds (the only af.mil and spaceforce.mil addresses that answer
    # this machine directly), DVIDS and the DoD contract announcements.
    "news": {"feeds": [
        {"publisher": "U.S. Air Force", "url": "https://www.af.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1&max=25"},
        {"publisher": "U.S. Space Force", "url": "https://www.spaceforce.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=25"},
        {"publisher": "DVIDS", "url": "https://www.dvidshub.net/rss/news"},
        {"publisher": "Department of Defense", "url": "https://www.war.gov/News/Contracts/", "index": True}],
        "official_names": {
            "www.af.mil": "U.S. Air Force", "www.spaceforce.mil": "U.S. Space Force", "www.dvidshub.net": "DVIDS",
            "www.afmc.af.mil": "AFMC", "www.aflcmc.af.mil": "AFLCMC", "www.afrl.af.mil": "AFRL", "www.ssc.spaceforce.mil": "Space Systems Command",
            "www.afsc.af.mil": "AFSC", "www.war.gov": "Department of Defense", "www.defense.gov": "Department of Defense"},
        "standing_terms": ["AFLCMC", "Air Force Life Cycle Management Center", "Space Systems Command", "AFRL", "Air Force Research Laboratory",
                           "program executive officer", "Portfolio Acquisition Executive", "Program Executive Office", "program office",
                           "acquisition", "contract award"]},
    "protests": {"listing": "https://www.gao.gov/legal/bid-protests/search?agency=Department%20of%20the%20Air%20Force&page={page}",
                 "agency": "Department of the Air Force", "max_pages": 60},
    # Budget: the department's procurement books (Aircraft, Missile, Other and Space Procurement) are Exhibit P-40 books
    # as the Navy's are; published on saffm.hq.af.mil and af.mil, which refuse this address, so taken through the hosted
    # browser. The RDT&E volumes (Exhibit R-2) are a second reader run, not yet made.
    "budget": {"exhibit": "P-40", "pb_label": "Air Force", "books_dir": "jbooks_airforce", "provider": "daf_budget_justification_books",
               "display": {"account_suffix": "F"}},  # 3600F RDT&E,AF and the Space Force's 3620F; 3010F Aircraft Procurement, AF
    "people": {"department": "agency:daf",
               "executive": ("secretary of the air force", "secretary, department of the air force", "under secretary", "assistant secretary", "chief of staff", "chief of space operations",
                             "vice chief", "commander,", "commander of", "director", "program executive officer", "portfolio acquisition executive"),
               "remarks_providers": None, "staff_listing": None},
    # No department-wide forecast spreadsheet was found on 2026-09-25 (research/docs/20): AFLCMC publishes a quarterly
    # "SMART Guide" of upcoming acquisitions as a PDF, not yet read; notices and awards carry the requirement.
    "forecast": {"pack_glob": None, "label": "", "short": "", "providers": {}, "memory_tool": "org_memory_airforce.py"},
    # The notices name the contracting office's symbol (AFLCMC/HBBK), not an office of the seed; no code pattern yet.
    "office_key_re": r"(?!)",
    # The offices whose cells are judged for precision: the program executive offices on the SAM.gov path of a swept
    # office, named as SAM.gov's records print them (the seed's names and aliases; refined after the first freeze).
    "pilot_offices": ("PEO BMC3", "PEO MCPNT", "PEO SN", "PEO AATS", "PEO SDACP", "PEO-WEAPONS", "PEO-FIGHT&ADV ACFT", "PEO-TRAINING",
                      "PAE C3BM-HANSCOM"),
    # The rows of the coverage matrix: the department, its two acquisition commands and the three centers the swept
    # offices belong to (seed aliases). The Space Force has no node of its own in SAM.gov's hierarchy (SSC sits under
    # the department), so it is read through SSC.
    "coverage_orgs": ["DAF", "AFMC", "AFLCMC", "AFRL", "AFSC", "SSC"],
    "coverage_org_nodes": {},  # the matrix is written by hand (or by its own script) until the nodes are named here
    "coverage_department_wide": {},
    # A notice names an office as the center's code and the office symbol (AFLCMC/HBBK, AFRL/RIKE, SSC/CGK) or as a PEO or
    # PAE followed by its word; an aircraft designator (F-35, KC-46) names the program, so nothing is stripped.
    "reading": {"office_code_re": r"\b(?:AFLCMC|AFRL|SSC|AFSC|AFNWC|AFTC|AFIMSC|SMC)/[A-Z]{2,5}\b|\b(?:PEO|PAE) [A-Z][A-Za-z0-9&/-]+",
                "hull_re": r"(?!)",
                # ponytail: the Navy pattern trace.py read for every agency before the profiles carried one.
                "rfp_re": r"N\d{5}-?\d{2}-?R-?[A-Z]?-?\d{3,4}(?![0-9])"},
    # USAJobs files the department under AF; Air Force Materiel Command (AF1M) covers AFLCMC, AFRL and AFSC, and Space
    # Systems Command (AF6S) the Space Force's acquisition. The subelement names the center where the listing states one.
    "hiring": {"usajobs_department_code": "AF", "usajobs_agency_codes": {"AF1M": "command:afmc", "AF6S": "command:ssc"},
               "note": "AFMC and SSC are the acquisition commands; the operational commands, the Guard and the Reserve announce under codes of their own and are not swept",
               "vendor_jobs": True, "vendor_watch": []},
}

PROFILES = {"navy": NAVY, "darpa": DARPA, "army": ARMY, "airforce": AIRFORCE}

# Every later agency is one file under agency_profiles/ that defines PROFILE, so adding one touches no shared file.
# A file written before the hiring signal maps no USAJobs code and asks no contractor postings until it names its own.
NO_HIRING = {"usajobs_department_code": "", "usajobs_agency_codes": {}, "note": "no USAJobs agency code mapped for this agency yet",
             "vendor_jobs": False, "vendor_watch": []}
for _path in sorted((Path(__file__).resolve().parent / "agency_profiles").glob("*.py")):
    _spec = importlib.util.spec_from_file_location(f"agency_profiles.{_path.stem}", _path)
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    _module.PROFILE.setdefault("hiring", dict(NO_HIRING))
    PROFILES[_module.PROFILE["key"]] = _module.PROFILE
if KEY not in PROFILES:
    sys.exit(f"AGENCY={KEY!r} is not a profile; known: {', '.join(sorted(PROFILES))}")
P = PROFILES[KEY]

# Where this agency's artefacts live. The Navy keeps the original folders; another agency gets its own tree.
RESEARCH = ROOT / "research" if KEY == "navy" else ROOT / "research" / "agencies" / KEY
MEMORY, EVENTS, RESULTS, SOURCES = RESEARCH / "memory", RESEARCH / "events", RESEARCH / "results", RESEARCH / "sources"
# The build folder (gitignored) the load and read-back stages write into.
BUILD = ROOT / "build" if KEY == "navy" else ROOT / "build" / KEY
# Shared by every agency: the ledger, the saved bytes, the cassettes.
MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
RAW = ROOT / "data" / "raw"
SAM_NOTICES = RAW / P["sam_dir"]
BOOKS = RAW / P["budget"]["books_dir"]
# What a requirement is called: a forecast row where the agency publishes a forecast, else the notices made it.
NEED_ROW = "forecast row" if P["forecast"]["pack_glob"] else "requirement"

# Another agency's tree starts empty; the tools write into it as the Navy tools write into folders
# that already exist, so the folders are made here rather than in each writer.
if KEY != "navy":
    for _folder in (MEMORY, EVENTS, RESULTS, SOURCES):
        _folder.mkdir(parents=True, exist_ok=True)


# The ledger is shared by every layer. A collector running under another profile marks its note with the profile
# key ("oversight watch [darpa]: ..."), and each reader keeps only the notes marked for it: the Navy, the original
# layer, owns the unmarked notes. Without this the DoW OIG reports taken for DARPA would read as Navy reports.
NOTE_TAG = "" if KEY == "navy" else f" [{KEY}]"
_OTHER_TAG = re.compile(r" \[[a-z0-9_]+\]")


def note_mark(note: str) -> str:
    """The profile mark in a note's head (the part before its first colon): "oversight watch [darpa]: ..." and
    "SAM notice detail [darpa] sweep: ..." are marked darpa; an unmarked head is the Navy's."""
    m = _OTHER_TAG.search(note.split(":", 1)[0])
    return m.group(0).strip()[1:-1] if m else ""


def note_is_ours(note: str) -> bool:
    """Whether a ledger row's note was written by a collector running under this profile."""
    return note_mark(note) == (KEY if NOTE_TAG else "")


def note_is_foreign(note: str) -> bool:
    """Whether a ledger row's note is marked for another profile (an unmarked note is nobody's mark)."""
    mark = note_mark(note)
    return bool(mark) and mark != KEY


def compiled(pattern: str, flags: int = 0) -> re.Pattern:
    return re.compile(pattern, flags)


def forecast_packs(base: Path) -> list[Path]:
    """This profile's forecast pack folders under base: one glob, or several when the agency publishes several forecasts."""
    globs = P["forecast"]["pack_glob"] or ()
    return sorted({p for g in ([globs] if isinstance(globs, str) else globs) for p in base.glob(g) if p.is_dir()})


def selfcheck() -> int:
    assert NAVY["fpds_offices"]["N00039"] == "NAVWAR HQ" and NAVY["sam_orgs"]["100076586"] == "N00039"
    assert DARPA["fpds_offices"] == {"HR0011": "DARPA Contracts Management Office"} and DARPA["fpds_funding_agencies"] == {"97AE": "DARPA"}
    assert compiled(NAVY["congress_pattern"]).search("the Secretary of the Navy shall") and not compiled(NAVY["congress_pattern"]).search("DARPA shall")
    assert compiled(DARPA["congress_pattern"]).search("directs DARPA to") and not compiled(DARPA["congress_pattern"]).search("the Navy shall")
    assert not compiled(DARPA["remarks"]["article_re"]).match("https://www.navy.mil/Press-Office/Speeches/display-speech/Article/1/x")
    assert compiled(ARMY["congress_pattern"]).search("directs the Secretary of the Army to") and not compiled(ARMY["congress_pattern"]).search("the Navy shall")
    assert all(compiled(ARMY["topic_re"]).search(c) for c in ("A20-179", "A214-006", "A20B-T018", "A254-P007", "ARM26BX06-NV012"))
    assert not compiled(ARMY["topic_re"]).search("N251-001") and compiled(ARMY["piid_re"]).search("W58RGZ-26-C-0001")
    assert all(compiled(NAVY["topic_re"]).search(c) for c in ("N251-001", "N24B-T012", "DON26BZ06-DV088", "DON26BX05-NP003", "DON26TZ01-NP002"))
    assert not compiled(NAVY["topic_re"]).search("DPA26BZ01-NP001") and not compiled(NAVY["topic_re"]).search("ARM26BX06-NV012")
    for key, profile in PROFILES.items():
        assert profile["key"] == key and set(profile) == set(NAVY), key
        assert profile["agency"]["toptier_code"].isdigit() and profile["agency"]["node"].startswith("agency:"), key
        # Every USAJobs agency code the profile sweeps names a memory node, and every code is the department's.
        hiring = profile["hiring"]
        assert all(code.startswith(hiring["usajobs_department_code"]) for code in hiring["usajobs_agency_codes"]), key
        assert all(":" in node for node in hiring["usajobs_agency_codes"].values()), key
    assert (ROOT / "research").is_dir()
    print(f"selfcheck ok (profile {KEY}: {P['label']}; artefacts under {RESEARCH.relative_to(ROOT)})")
    return 0


if __name__ == "__main__":
    sys.exit(selfcheck() if "--selfcheck" in sys.argv else print(KEY, RESEARCH.relative_to(ROOT)))

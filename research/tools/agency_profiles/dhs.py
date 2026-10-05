"""The Department of Homeland Security profile (the whole department, every component): loaded by agency.py from agency_profiles/.

Codes, office names and organization ids are as the saved SAM.gov hierarchy listings and records of 2026-09-26 print them;
the offices are each component's largest contract awarders by FY2026 obligations in the USAspending sub-agency answer of the
same day. Contract numbers follow the DHS uniform PIID (office code, fiscal year, instrument letter, eight-character serial):
230 of the 232 DHS numbers the APFS forecast of 2026-09-26 prints match `piid_re`.
"""

# The components, each a command node under the department (SAM.gov organization id -> node, name as SAM.gov prints it,
# FPDS agency code). The Office of Procurement Operations contracts for the headquarters, S&T, CISA and CWMD.
_COMPONENTS = {
    "100013095": ("command:opo", "OFFICE OF PROCUREMENT OPERATIONS", "7001"),
    "300000272": ("command:st", "SCIENCE AND TECHNOLOGY", "7040"),
    "100012587": ("command:cbp", "U.S. CUSTOMS AND BORDER PROTECTION", "7014"),
    "500044551": ("command:cisa", "Cybersecurity and Infrastructure Security Agency", "7061"),
    "100012177": ("command:tsa", "TRANSPORTATION SECURITY ADMINISTRATION", "7013"),
    "100011943": ("command:fema", "FEDERAL EMERGENCY MANAGEMENT AGENCY", "7022"),
    "100012855": ("command:uscg", "U.S. COAST GUARD", "7008"),
    "100012075": ("command:ice", "U.S. IMMIGRATION AND CUSTOMS ENFORCEMENT", "7012"),
    "100012967": ("command:usss", "U.S. SECRET SERVICE", "7009"),
    "500044550": ("command:cwmd", "Countering Weapons of Mass Destruction", "7062"),
    "100011968": ("command:uscis", "U.S. CITIZENSHIP AND IMMIGRATION SERVICES", "7003"),
    "100012472": ("command:fletc", "FEDERAL LAW ENFORCEMENT TRAINING CENTER", "7015"),
}
# The contracting offices seeded and swept (SAM.gov organization id -> office code, name as SAM.gov prints it).
_OFFICES = {
    "100170417": ("70RSAT", "SCI TECH ACQ DIV"), "500182403": ("70RCSJ", "CISA CONTRACTING ACTIVITY"),
    "500044552": ("70RWMD", "CWMD ACQ DIV"), "100170418": ("70RTAC", "INFO TECH ACQ CENTER"),
    "500189406": ("70RDA2", "DEPARTMENTAL OPERATIONS ACQUISITION DIVISION II"),
    "100173520": ("70B01C", "ADMINISTRATION FACILITIES TRAINING CONTRACTING DIVISION"),
    "100186620": ("70B02C", "AIR AND MARINE CONTRACTING DIVISION"), "100164020": ("70B03C", "BORDER ENFORCEMENT CONTRACTING DIVISION"),
    "100173522": ("70B04C", "INFORMATION TECHNOLOGY CONTRACTING DIVISION"), "100167151": ("70B06C", "MISSION SUPPORT CONTRACTING DIVISION"),
    "500181964": ("70FA31", "INFORMATION TECHNOLOGY DEVELOPMENT AND SUSTAINMENT"),
    "100176825": ("70FA30", "INFORMATION TECHNOLOGY COMMODITIES AND TELECOMMUNICATIONS"),
    "100167166": ("70FA60", "MITIGATION SECTION(MIT60)"), "100186634": ("70FA20", "PREPAREDNESS SECTION(PRE20)"),
    "100181479": ("70Z023", "HQ CONTRACT OPERATIONS (CG-912)(000"), "100168424": ("70Z038", "AVIATION LOGISTICS CENTER (ALC)(00038)"),
    "100181483": ("70Z079", "C5I DIVISION 1 ALEXANDRIA"), "100178068": ("70Z047", "FDCC(00047)"),
    "100178069": ("70Z050", "FDCC DET SEATTLE(00050)"),
    "500030763": ("70CDCR", "DETENTION COMPLIANCE AND REMOVALS"), "500030974": ("70CTD0", "INFORMATION TECHNOLOGY DIVISION"),
    "500031354": ("70CMSD", "INVESTIGATIONS AND OPERATIONS SUPPORT DALLAS"), "500030468": ("70CMSW", "MISSION SUPPORT WASHINGTON"),
    "500000022": ("70T040", "SECURITY TECHNOLOGY"), "500000090": ("70T030", "ENTERPRISE INFORMATION TECHNOLOGY"),
    "500000023": ("70T050", "MISSION ESSENTIALS"), "500000113": ("70T010", "WORKFORCE & ENTERPRISE OPERATIONS"),
    "100186639": ("70US09", "U. S. SECRET SERVICE"), "100164031": ("70SBUR", "USCIS CONTRACTING OFFICE(ERBUR)"),
    "100167172": ("70LGLY", "FLETC GLYNCO PROCUREMENT OFFICE"), "100180126": ("70LART", "FLETC ARTESIA PROCUREMENT OFFICE"),
}
_NAMES_RE = (r"\b(DHS|Homeland Security|CBP|CISA|TSA|FEMA|ICE|USCIS|FLETC|CWMD|Coast Guard|Secret Service|"
             r"Customs and Border Protection|Transportation Security Administration|Federal Emergency Management Agency)\b")
_AGENCY = ("Department of Homeland Security (its components, their contracting offices and the requirement offices the "
           "DHS acquisition forecast names)")

# The forecast system's JSON records by field name. Prefixes are matched in order, so a longer name comes before the
# name it starts with, and None drops a field: a contact's name is kept, never the phone or e-mail beside it.
APFS_HEADERS = {
    "apfs_number": "number", "requirements_title": "requirement_title", "requirements_office": "pm_directorate",
    "requirements_contact_first_name": "requirement_contact_first", "requirements_contact_last_name": "requirement_contact_last",
    "alternate_contact_first_name": "alternate_contact_first", "alternate_contact_last_name": "alternate_contact_last",
    "requirements_contact": None, "requirement": "requirement_description", "organization": "command",
    "dollar_range": "anticipated_total_value", "contract_vehicle": "contract_vehicle", "contract_type": "contract_type",
    "competitive": "follow_on_or_new", "small_business_set_aside": "procurement_method", "naics": "naics",
    "contractor": "incumbent_contractor", "contract_number": "existing_contract_number",
    "estimated_solicitation_release_date": "solicitation_date", "anticipated_award_date": "award_date",
    "award_quarter": "award_quarter_as_written", "contracting_office": "contracting_center",
    "place_of_performance_city": "place_of_performance", "publish_date": "published", "id": "record_id"}

PROFILE = {
    "key": "dhs",
    "label": "Department of Homeland Security",
    "short": "DHS",
    "database": "dhs_proof",
    "agency": {"toptier_code": "070", "toptier_name": "Department of Homeland Security", "toptier_abbreviation": "DHS",
               "subtier_code": "7000", "subtier_name": "Department of Homeland Security", "subtier_abbreviation": "DHS",
               "node": "agency:dhs",
               # FAR 4.1603: an instrument number opens with its issuing office's code (70RSAT, 70B01C, 70Z023, 70T040).
               "office_code_re": r"70[A-Z][A-Z0-9]{3}",
               "small_business_directory": "https://www.dhs.gov/osdbu/small-business-specialists",
               # The layer covers the whole department, so assistance awards are filtered by the toptier agency.
               "assistance_tier": "toptier"},
    "fpds_offices": {code: name for code, name in _OFFICES.values()},
    "fpds_funding_agencies": {},
    "sam_dir": "sam_notices_dhs",
    "sam_orgs": {org: code for org, (code, _) in _OFFICES.items()},
    "sam_codes": (),
    "sam_org_nodes": {"100011942": "agency:dhs", **{org: node for org, (node, _, _) in _COMPONENTS.items()},
                      **{org: f"contracting:{code.lower()}" for org, (code, _) in _OFFICES.items()}},
    # DHS SBIR topics are published outside the DoD portal; topics are deferred.
    "sbir_component": None,
    "sbir_commands": {},
    "performer_types": (),
    "office_key_re": r"(?!)",  # the forecast writes its requirement offices by name; no code pattern yet
    "shared_sources": ("govinfo_api",),
    "owner_types": (),
    "families": {"dhs_apfs_forecast": "forecast", "dhs_site": "organization", "dhs_budget_justification": "budget"},
    "moved_urls": {},
    # Topic codes as the S&T SBIR pre-solicitation of 2023-11-15 prints them (DHS241-001 to DHS241-006); contract and
    # solicitation numbers under a DHS office code. A solicitation serial runs seven to ten characters in the notices
    # of 2026-09-26 (70Z08526Q0028362, 70Z03326QSEAT32798), a contract serial eight.
    "topic_re": r"\bDHS\d{3}-\d{3}\b",
    "piid_re": r"\b70[A-Z][A-Z0-9]{3}-?\d{2}-?[A-Z]-?[A-Z0-9]{8}\b",
    "solicitation_re": r"\b70[A-Z][A-Z0-9]{3}-?\d{2}-?[A-Z]-?[A-Z0-9]{7,10}\b",
    "generic_words": ("dhs", "department of homeland security", "homeland security", "office of procurement operations", "opo",
                      "sources sought", "industry day", "request for information"),
    "fedreg_conditions": [("conditions[agencies][]", "homeland-security-department")],
    "fedreg_label": "Department of Homeland Security",
    "fedreg_name_pattern": None,
    "congress_pattern": (r"\bDepartment of Homeland Security\b|\bDHS\b|\b(?:CBP|CISA|TSA|FEMA|ICE|USCIS|FLETC|CWMD)\b"
                         r"|\bCoast Guard\b|\bSecret Service\b|\bCustoms and Border Protection\b|\bScience and Technology Directorate\b"
                         r"|\bTransportation Security Administration\b|\bFederal Emergency Management Agency\b"
                         r"|\bImmigration and Customs Enforcement\b|\bCybersecurity and Infrastructure Security Agency\b"
                         r"|\bCountering Weapons of Mass Destruction\b|\bOffice of Procurement Operations\b"),
    "congress_label": "DHS",
    # The Homeland Security appropriations reports (the govinfo titles of H. Rept. 116-180 to 119-697 and S. Rept. 116-125
    # and 118-85 read "DEPARTMENT OF HOMELAND SECURITY APPROPRIATIONS BILL, <year>"), and the House Appropriations and
    # Homeland Security committee calendars (HM00 answered as the Committee on Homeland Security Meeting Feed, 2026-09-26).
    "committees": {"reports": [("appropriations", r"^DEPARTMENT OF HOMELAND SECURITY APPROPRIATIONS (?:BILL|ACT),? (\d{4})$")],
                   "names": {("appropriations", "h"): "House Appropriations Committee",
                             ("appropriations", "s"): "Senate Appropriations Committee"},
                   "house_feeds": {"HM00": "https://docs.house.gov/Committee/RSS.ashx?Code=HM00",
                                   "AP00": "https://docs.house.gov/Committee/RSS.ashx?Code=AP00"}},
    "oversight": {"agency": _AGENCY,
                  "queries": ("Homeland Security", "DHS"),
                  "reviewed_re": r"Department of Homeland Security\b|\bHomeland Security\b",
                  "names_re": _NAMES_RE,
                  "gao_query": "GAO report Department of Homeland Security acquisition program"},
    # No speech or testimony archive is registered for DHS yet.
    "remarks": {"speeches": None, "testimony": None, "article_re": r"^(?!)",
                "names_re": _NAMES_RE,
                "agency": _AGENCY,
                "testimony_pages": [],
                "conference_query": 'homeland security conference agenda speakers DHS "Under Secretary" OR "Chief Procurement Officer" OR "Program Executive" keynote panel',
                "own_domains": ["dhs.gov", "cbp.gov", "tsa.gov", "fema.gov", "uscg.mil", "ice.gov", "cisa.gov", "secretservice.gov",
                                "uscis.gov", "fletc.gov"],
                "providers": {"speech": "dhs_site", "testimony": "house_committee_repository", "statement": "house_committee_repository",
                              "conference": "conference_pages_exa"}},
    "news": {"feeds": [{"publisher": "Department of Homeland Security", "url": "https://www.dhs.gov/news-releases/press-releases", "index": True}],
             "official_names": {"www.dhs.gov": "Department of Homeland Security", "www.cbp.gov": "U.S. Customs and Border Protection",
                                "www.tsa.gov": "Transportation Security Administration", "www.fema.gov": "Federal Emergency Management Agency",
                                "www.uscg.mil": "U.S. Coast Guard", "www.ice.gov": "U.S. Immigration and Customs Enforcement",
                                "www.cisa.gov": "Cybersecurity and Infrastructure Security Agency", "www.secretservice.gov": "U.S. Secret Service",
                                "www.uscis.gov": "U.S. Citizenship and Immigration Services", "www.fletc.gov": "Federal Law Enforcement Training Centers"},
             "standing_terms": ["Department of Homeland Security", "Office of Procurement Operations", "Science and Technology Directorate",
                                "Customs and Border Protection", "Coast Guard", "Transportation Security Administration",
                                "Cybersecurity and Infrastructure Security Agency", "program office", "acquisition", "contract award"]},
    "protests": {"listing": "https://www.gao.gov/legal/bid-protests/search?agency=Department%20of%20Homeland%20Security&page={page}",
                 "agency": "Department of Homeland Security", "max_pages": 60},
    # The FY2027 congressional justifications (dhs.gov/cj, as USAspending names it), one book per component saved under a
    # "budget book" note: budget.py reads their capital investment exhibits (91 investments in 11 books; USCIS, CWMD and
    # A&O carry none) and dates a book by its upload folder, as it prints no date.
    "budget": {"pb_label": "Department of Homeland Security", "books_dir": "jbooks_dhs", "provider": "dhs_budget_justification"},
    "people": {"department": "agency:dhs",
               "executive": ("secretary of homeland security", "deputy secretary", "under secretary", "assistant secretary", "administrator",
                             "commissioner", "commandant", "director", "chief procurement officer", "head of the contracting activity"),
               "remarks_providers": None, "staff_listing": None,
               "emails": False},  # names only: a notice contact's work e-mail is used to merge, never written
    # The Acquisition Planning Forecast System publishes its records as JSON (/api/forecast/), which read_sheet reads; the CSV
    # and Excel buttons of the forecast page build their files in the browser from it.
    # The system serves only its current records (828 on 2026-09-28 against 873 two days before: 57 gone, 12 new), so every
    # daily pull is a release of its own (`record_system`), keyed by its day, and releases pair by the APFS number alone.
    "forecast": {"pack_glob": "dhs_20??-??-??", "label": "Acquisition Planning Forecast System", "short": "APFS",
                 "providers": {"dhs": "dhs_apfs_forecast"}, "memory_tool": "org_memory_dhs.py",
                 "releases": [{"key": "dhs", "activity": "dhs", "match": "apfs-cloud.dhs.gov/api/forecast", "release_date": "",
                               "release_note": "", "record_system": True,
                               "sheet": "APFS", "header_row": 1, "scope": "all", "headers": APFS_HEADERS,
                               # each record's public page, the one the forecast page links (read_sheet fills `url`)
                               "record_url": "https://apfs-cloud.dhs.gov/record/{record_id}/public-print/"}]},
    "pilot_offices": ("S&T", "CBP", "CISA", "TSA", "USCG", "FEMA"),
    "coverage_orgs": ["OPO", "S&T", "CBP", "CISA", "TSA", "FEMA", "USCG", "ICE", "USSS", "CWMD"],
    "coverage_org_nodes": {},  # the matrix is written by hand (or by its own script) until the nodes are named here
    "coverage_department_wide": {},
    # A DHS notice names its buyer by component acronym or a Coast Guard directorate (CG-912); a cutter by name and hull
    # designator as the forecast prints them (USCGC HEALY (WAGB-20), USCGC HAMMER (WLIC 75302), WLR 6550, WPC-154).
    "reading": {"office_code_re": r"\b(?:OPO|S&T|CBP|CISA|TSA|FEMA|ICE|USCIS|USSS|USCG|FLETC|CWMD|FPS|OBIM)\b|\bCG-\d{1,3}\b",
                "hull_re": (r"\bUSCGC\s+[A-Z][A-Za-z .'-]*?\s*\(?W[A-Z]{1,4}[\s-]*\d{1,5}\)?"
                            r"|\bW(?:AGB|LB|LBB|LIC|LI|LM|LR|MEC|MSL|MSM|PB|PC|TGB|YTL)[\s-]+\d{1,5}\b"),
                # A request for proposals written into a title, read with its spaces removed (70B06C26R00000185).
                "rfp_re": r"70[A-Z][A-Z0-9]{3}-?\d{2}-?R-?[A-Z0-9]{8}(?![A-Z0-9])"},
    # USAJobs agency codes as its agency list names them (read 2026-09-29 through context.dev: the list answers this
    # address with 403, the historic announcement API does not). HSDA still carries the name Domestic Nuclear Detection
    # Office, the office CWMD absorbed. The Inspector General (HSAE) and Intelligence and Analysis (HSIC) have no node in
    # the organization memory.
    "hiring": {"usajobs_department_code": "HS",
               "usajobs_agency_codes": {"HSAA": "agency:dhs", "HSAB": "command:uscis", "HSAC": "command:uscg", "HSAD": "command:usss",
                                        "HSBB": "command:ice", "HSBC": "command:tsa", "HSBD": "command:cbp", "HSBE": "command:fletc",
                                        "HSCA": "command:cisa", "HSCB": "command:fema", "HSDA": "command:cwmd", "HSFA": "command:st"},
               "note": "the codes listed 6,848 announcements opened 2026-04-02 to 2026-09-29 (CBP 2,015, USCG 1,224, TSA 1,154, CISA 97); "
                       "HSDA and HSFA answered 204, none in the window",
               "vendor_jobs": False, "vendor_watch": []},
}

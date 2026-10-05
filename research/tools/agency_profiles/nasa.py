"""The NASA profile: Headquarters, its mission directorates, the centers, JPL (an FFRDC) and the NASA Shared Services
Center, read by the same tools as the Navy, DARPA and Army layers.

Codes as the sources state them on 2026-09-26: USAspending toptier 080 (one subtier, 8000, with thirteen awarding offices
in FY2026), SAM.gov organizations 100000266 (department) and 100000267 (agency) with the thirteen offices under the
agency, each carrying its FAR 4.1603 office code (80GSFC, 80JSC0, 80NSSC ...). The forecast is the Agency-Wide
Acquisition Forecast workbook the Office of Procurement publishes (hq.nasa.gov/office/procurement/forecast).
SBIR/STTR topics are deferred (NASA's are not on the DoD portal) and so is the budget.
"""

# The Agency-Wide Acquisition Forecast (AcqForecastNew.xlsx, sheet "Forecast", one header row). The buying office (a
# center or an agency office by its abbreviation: KSC, JSC, NSSC, ITPO, HQ, IV&V) is the owning office; its internal
# code, the mission directorate and the item's SourceID ride along. The SourceID is a bare list number, so it is kept
# as a column and is no row's PID. A longer header sits before the one it starts with (the reader takes the first
# prefix that matches).
NASA_HEADERS = {
    "buyingofficecode": "buying_office_code", "buyingoffice": "command", "acquisitionstatus": "acquisition_status",
    "awardedorwithdrawn": "awarded_or_withdrawn", "acquisitionphase": "acquisition_phase", "sourceid": "source_id",
    "titleofrequirement": "requirement_title", "technicalpoc": "technical_poc_contact", "tech poc name": "technical_poc_name",
    "placeofperformancestate": "place_of_performance", "naics description": "naics_description", "naics": "naics",
    "psc code description": "psc_description", "psc code": "psc", "hqmissiondirectorate": "mission_directorate",
    "fundingsource": "funding_source", "smallbusinessspecialistpoc": "small_business_office",
    "smallbusinessspecialistemail": "small_business_email", "anticipatedfyaward": "award_date",
    "anticipated qtr of award": "award_quarter_as_written", "neworrecompete": "follow_on_or_new",
    "estimatedcontractvalue": "anticipated_total_value", "setasidetype": "procurement_method", "contracttype": "contract_type",
    "grantorcooperativeagreement": "grant_or_cooperative_agreement", "qtrsolornoforelease": "solicitation_quarter_as_written",
    "fyofsolornoforelease": "solicitation_date", "periodofperformance": "period_of_performance_months",
    "type of award/contract vehicle": "procurement_instrument", "extentcompeted": "extent_competed",
    "description": "requirement_description",
}

NASA_RELEASES = [
    {"key": "nasa_2026-08", "activity": "nasa", "match": "AcqForecastNew", "release_date": "2026-08-04",
     "release_note": "the FY 2026 Agency-Wide Acquisition Forecast, dated by its page ('updated August 4, 2026') and the file's Last-Modified header",
     "sheet": "Forecast", "header_row": 1, "scope": "all", "headers": NASA_HEADERS},
]

# SAM.gov organization id -> (FAR 4.1603 office code, office name as the record prints it, memory node).
_OFFICES = {
    "100183432": ("80GSFC", "NASA GODDARD SPACE FLIGHT CENTER", "center:gsfc"),
    "100167400": ("80JSC0", "NASA JOHNSON SPACE CENTER", "center:jsc"),
    "100170446": ("80MSFC", "NASA MARSHALL SPACE FLIGHT CENTER", "center:msfc"),
    "100170445": ("80KSC0", "NASA KENNEDY SPACE CENTER", "center:ksc"),
    "100164057": ("80GRC0", "NASA GLENN RESEARCH CENTER", "center:grc"),
    "100170444": ("80ARC0", "NASA AMES RESEARCH CENTER", "center:arc"),
    "100173681": ("80LARC", "NASA LANGLEY RESEARCH CENTER", "center:larc"),
    "100177106": ("80SSC0", "NASA STENNIS SPACE CENTER", "center:ssc"),
    "100187000": ("80AFRC", "NASA ARMSTRONG FLIGHT RESEARCH CNTR", "center:afrc"),
    "100164058": ("80NSSC", "NASA SHARED SERVICES CENTER", "contracting:80nssc"),
    "500168982": ("80TECH", "NASA IT PROCUREMENT OFFICE", "contracting:80tech"),
    "100187003": ("80HQTR", "NASA HEADQUARTERS", "contracting:80hqtr"),
    "100173682": ("80NM00", "NASA MANAGEMENT OFFICE -- JPL", "contracting:80nm00"),
}

PROFILE = {
    "key": "nasa",
    "label": "National Aeronautics and Space Administration",
    "short": "NASA",
    "database": "nasa_proof",
    "agency": {"toptier_code": "080", "toptier_name": "National Aeronautics and Space Administration", "toptier_abbreviation": "NASA",
               "subtier_code": "8000", "subtier_name": "National Aeronautics and Space Administration", "subtier_abbreviation": "NASA",
               "node": "agency:nasa",
               # FAR 4.1603: an instrument number opens with its issuing office's six-character code (80GSFC, 80JSC0, 80NSSC).
               "office_code_re": r"80[A-Z0-9]{4}",
               # The Office of Small Business Programs' page of the centers and offices, each with its own links.
               "small_business_directory": "https://www.nasa.gov/osbp/about-nasa-centers/"},
    # Every office USAspending shows awarding for NASA in FY2026, by office code (the thirteen the sub-agency listing names).
    "fpds_offices": {code: name for code, name, _ in _OFFICES.values()},
    "fpds_funding_agencies": {},
    "sam_dir": "sam_notices_nasa",
    "sam_orgs": {oid: code for oid, (code, _, _) in _OFFICES.items()},
    "sam_codes": (),
    "sam_org_nodes": {**{oid: nid for oid, (_, _, nid) in _OFFICES.items()}, "100000267": "agency:nasa", "100000266": "agency:nasa"},
    "sbir_component": None,  # NASA's SBIR/STTR topics are not on the DoD portal; deferred
    "sbir_commands": {},
    # JPL is a federally funded research and development center: where work is done. A center is both the buyer and
    # the owner of its programs, so it is not a performer here.
    "performer_types": ("technical_center",),
    "office_key_re": r"(?!)",  # no office code pattern in contract text yet
    "shared_sources": ("govinfo_api",),
    # The centers (field activities) own requirements beside program offices: the forecast names them as buying offices.
    "owner_types": ("field_activity",),
    "families": {"nasa_acquisition_forecast": "forecast", "nasa_organization_page": "organization", "nasa_osbp": "organization",
                 "nasa_news_releases": "news"},
    "moved_urls": {},
    # Topics are deferred. Contract numbers as FPDS and SAM.gov print them (80AFRC19C0004, 80AFRC21CA022, 80GSFC19D0011),
    # and the older form a 2026 notice still carries (NNK14MA74C). Solicitation numbers (80GRC026R0009, 80TECH26QA123,
    # 80JSC026R0021DRFP), and the Shared Services Center's simplified form (80NSSC26943019Q, 80NSSC26938932Q-1).
    "topic_re": r"(?!)",
    "piid_re": r"\b(?:80[A-Z0-9]{4}-?\d{2}-?[A-Z]-?[A-Z0-9]\d{3}|NN[A-Z]\d{2}[A-Z]{2}\d{2,3}[A-Z])\b",
    "solicitation_re": r"\b80[A-Z0-9]{4}-?\d{2}-?(?:[A-Z]-?[A-Z0-9]{4}[A-Z]{0,4}|\d{6}Q?)(?:-\d)?\b",
    "generic_words": ("nasa", "national aeronautics and space administration", "nasa headquarters", "mission directorate",
                      "sources sought", "request for information", "industry day", "draft rfp"),
    "fedreg_conditions": [("conditions[agencies][]", "national-aeronautics-and-space-administration")],
    "fedreg_label": "National Aeronautics and Space Administration",
    "fedreg_name_pattern": None,
    "congress_pattern": (r"\bNASA\b|\bNational Aeronautics and Space Administration\b|\bMission Directorate\b|\bJet Propulsion Laboratory\b"
                         r"|\b(?:Goddard|Marshall|Johnson|Kennedy|Stennis) Space(?: Flight)? Center\b|\b(?:Ames|Glenn|Langley) Research Center\b"
                         r"|\bArmstrong Flight Research Center\b|\bSpace Launch System\b|\bArtemis\b"),
    "congress_label": "NASA",
    # The Commerce, Justice, Science appropriations reports (the House titles them "COMMERCE, JUSTICE, SCIENCE, ...", the
    # Senate "DEPARTMENTS OF COMMERCE AND JUSTICE, SCIENCE, ..."), and the NASA authorization reports, which are not
    # yearly (H. Rept. 118-701, NASA Reauthorization Act of 2024; S. Rept. 116-262): their year is the act's.
    "committees": {
        "reports": [("appropriations", r"^(?:DEPARTMENTS OF )?COMMERCE,? (?:AND )?JUSTICE, SCIENCE, AND RELATED AGENCIES APPROPRIATIONS (?:BILL|ACT),? (\d{4})$"),
                    ("authorization", r"^(?:NATIONAL AERONAUTICS AND SPACE ADMINISTRATION|NASA) (?:TRANSITION )?(?:RE)?AUTHORIZATION ACT OF (\d{4})$")],
        "names": {("appropriations", "h"): "House Appropriations Committee", ("appropriations", "s"): "Senate Appropriations Committee",
                  ("authorization", "h"): "House Committee on Science, Space, and Technology",
                  ("authorization", "s"): "Senate Committee on Commerce, Science, and Transportation"},
        "house_feeds": {"AP00": "https://docs.house.gov/Committee/RSS.ashx?Code=AP00", "SY00": "https://docs.house.gov/Committee/RSS.ashx?Code=SY00"}},
    "oversight": {"agency": "National Aeronautics and Space Administration (Headquarters, the mission directorates, the centers, JPL and the Shared Services Center)",
                  "queries": ("NASA", "National Aeronautics and Space Administration"),
                  "reviewed_re": r"National Aeronautics and Space Administration|\bNASA\b",
                  "names_re": r"\b(NASA|National Aeronautics and Space Administration|Artemis|Space Launch System|Orion|International Space Station"
                              r"|Jet Propulsion Laboratory|Goddard|Marshall Space Flight Center|Johnson Space Center|Kennedy Space Center)\b",
                  "gao_query": "GAO report NASA major projects acquisition"},
    # NASA keeps no speech or testimony archive this layer reads yet.
    "remarks": {"speeches": None, "testimony": None, "article_re": r"^(?!)",
                "names_re": r"\b(NASA|National Aeronautics and Space Administration|Mission Directorate|Jet Propulsion Laboratory)\b",
                "agency": "National Aeronautics and Space Administration (Headquarters, the mission directorates, the centers, JPL and the Shared Services Center)",
                "testimony_pages": [],
                "conference_query": 'space industry conference agenda speakers NASA "Associate Administrator" OR "Center Director" OR "Mission Directorate" keynote panel',
                "own_domains": ["nasa.gov"],
                "providers": {"speech": "nasa_news_releases", "testimony": "house_committee_repository", "statement": "house_committee_repository",
                              "conference": "conference_pages_exa"}},
    "news": {"feeds": [{"publisher": "NASA", "url": "https://www.nasa.gov/news-release/feed/"}],
             "official_names": {"www.nasa.gov": "NASA", "science.nasa.gov": "NASA Science", "www.jpl.nasa.gov": "NASA JPL"},
             "standing_terms": ["NASA", "Mission Directorate", "Space Flight Center", "Research Center", "Jet Propulsion Laboratory",
                                "Shared Services Center", "program office", "acquisition", "contract award"]},
    "protests": {"listing": "https://www.gao.gov/legal/bid-protests/search?agency=National%20Aeronautics%20and%20Space%20Administration&page={page}",
                 "agency": "National Aeronautics and Space Administration", "max_pages": 20},
    # Deferred: NASA's congressional justification is not a DoD exhibit; the folder stays empty.
    "budget": {"pb_label": "NASA", "books_dir": "jbooks_nasa", "provider": "nasa_budget_request"},
    "people": {"department": "agency:nasa",
               "executive": ("administrator", "deputy administrator", "associate administrator", "assistant administrator", "chief of staff",
                             "center director", "director", "executive director", "chief financial officer", "chief information officer"),
               "remarks_providers": None, "staff_listing": None},
    "forecast": {"pack_glob": "nasa_20??-??", "label": "Agency-Wide Acquisition Forecast", "short": "forecast", "memory_tool": "org_memory_nasa.py",
                 "providers": {"nasa": "nasa_acquisition_forecast"}, "releases": NASA_RELEASES},
    # The centers with the most forecast lines, by the abbreviation the forecast and the memory's office code carry.
    "pilot_offices": ("KSC", "JSC", "ARC", "AFRC", "NSSC", "GRC"),
    "coverage_orgs": ["HQ", "GSFC", "JSC", "KSC", "MSFC", "ARC", "GRC", "LaRC", "AFRC", "SSC", "JPL", "NSSC"],
    "coverage_org_nodes": {},  # the matrix is written by hand (or by its own script) until the nodes are named here
    "coverage_department_wide": {},
    # A NASA person's organization code follows the name as center and code ("Reinert, Nick (KSC-LXB00)"); NASA has no
    # hull designators.
    "reading": {"office_code_re": r"\b(?:ARC|AFRC|GRC|GSFC|HQ|JSC|KSC|LaRC|LARC|MSFC|SSC|NSSC|ITPO|WFF)-[A-Z0-9]{2,6}\b",
                "hull_re": r"(?!)",
                "rfp_re": r"80[A-Z0-9]{4}-?\d{2}-?R-?\d{4}(?![0-9])"},
}

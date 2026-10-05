"""The National Oceanic and Atmospheric Administration profile: NOAA and its six line offices (NESDIS, NWS, NMFS, NOS,
OAR, OMAO) under the Department of Commerce, read by the same tools as the Navy, DARPA and Army layers.

Codes as the records state them on 2026-09-26: USAspending toptier 013 (Department of Commerce); SAM.gov department
100035122 (code 1300) and agency 100099213 (code 1330); the NOAA contracting offices by their activity address codes,
each opening NOAA's instrument numbers (FAR 4.1603). SBIR/STTR topics and the budget are deferred.
"""

# The FAR 4.1603 activity address codes NOAA's awards open with: the Acquisition and Grants Office divisions (1305..),
# the satellite acquisition offices (1332K.) and the field delegates (1333L., 1333M.), as USAspending lists NOAA's
# awarding offices for FY2026. The Commerce code space (13....) is shared with NIST and Census, hence the narrow form.
_OFFICE = r"13(?:05[LMN]\d|32K[A-Z]|33[LM][A-Z])"
# Legacy NOAA contract numbers (before the uniform PIID of FY2018): a two-letter region, 133 and a letter, the year, a
# two-letter type and four digits (EA-133W-16-CQ-0051, ST1330-18-CQ-0073, DG133E09CN0094; USAspending adds DOC).
_LEGACY = r"(?:DOC)?[A-Z]{2}-?133[A-Z0-9]-?\d{2}-?[A-Z]{2}-?\d{4}"
_NAMES = (r"\bNOAA\b|\bNational Oceanic and Atmospheric Administration\b|\bNational Weather Service\b|\bNational Marine Fisheries Service\b"
          r"|\bNational Ocean Service\b|\bNational Environmental Satellite, Data, and Information Service\b|\bNESDIS\b"
          r"|\bOffice of Oceanic and Atmospheric Research\b|\bOffice of Marine and Aviation Operations\b|\b(?:NWS|NMFS|OMAO)\b")
_AGENCY = "National Oceanic and Atmospheric Administration (NOAA, its line offices and its Acquisition and Grants Office)"
_FORECAST_URL = "https://www.commerce.gov/sites/default/files/2022-12/DOC%20Weekly%20Forecast%20Report.xlsx"

# The Commerce weekly procurement forecast (BAS PRISM AAP): one sheet, the header on row 3, the solicitation's fiscal
# year and quarter in two columns ("2026", "2nd"), the owning office as Office and Organization Unit ("National Weather
# Service", "NOAA - NWS"). Prefixes are matched in order, so a longer header comes before the header it starts with.
DOC_HEADERS = {
    "forecast id": "number", "workspace number": "workspace_number", "date created or modified": "date_modified",
    "date created": "date_created", "organization unit": "pm_directorate", "organization": "organization", "office": "command",
    "title": "requirement_title", "description": "requirement_description", "naics code": "naics",
    "months": "period_of_performance_months", "years": "period_of_performance_years",
    "place of performance city": "place_of_performance", "place of performance state": "place_of_performance_state",
    "place of performance country": "place_of_performance_country", "type of awardee": "type_of_awardee",
    "estimated value range": "anticipated_total_value", "estimated solicitation fiscal quarter": "solicitation_quarter_as_written",
    "estimated solicitation fiscal year": "solicitation_date", "competition strategy": "competition_strategy",
    "new requirement or recompete": "follow_on_or_new", "incumbent contractor name": "incumbent_contractor",
    "awarded contract order number": "existing_contract_number", "anticipated set aside and type": "procurement_method",
    "anticipated contract vehicle": "contract_vehicle", "anticipated action award type": "procurement_instrument",
    "point of contact name": "contracting_poc_name", "point of contact email": "contracting_poc_contact",
    "does this acquisition contain information technology": "information_technology", "awarded?": "awarded",
}

PROFILE = {
    "key": "noaa",
    "label": "National Oceanic and Atmospheric Administration",
    "short": "NOAA",
    "database": "noaa_proof",
    "agency": {"toptier_code": "013", "toptier_name": "Department of Commerce", "toptier_abbreviation": "DOC",
               "subtier_code": "1330", "subtier_name": "National Oceanic and Atmospheric Administration", "subtier_abbreviation": "NOAA",
               "node": "agency:noaa",
               "office_code_re": _OFFICE,
               # Commerce's own small business page answers 403 to this address; NOAA's AGO Small Business Office page is read.
               "small_business_directory": "https://www.noaa.gov/organization/acquisition-grants/small-business"},
    # The contracting offices that sign NOAA's contracts, by FY2026 contract obligations (USAspending), named as the
    # SAM.gov office record's address prints the division.
    "fpds_offices": {"1305M2": "Eastern Acquisition Division", "1332KP": "Satellite and Information Acquisition Division",
                     "1305M3": "Western Acquisition Division", "1305M4": "Strategic Sourcing Acquisition Division",
                     "1333MK": "OMAO Field Delegates", "1333MG": "NOS Field Delegates"},
    "fpds_funding_agencies": {},
    "sam_dir": "sam_notices_noaa",
    "sam_orgs": {"100175863": "1305M2", "100518256": "1332KP", "100163078": "1305M3", "100179142": "1305M4",
                 "100163558": "1333MK", "100166321": "1333MG"},
    "sam_codes": (),
    "sam_org_nodes": {"100175863": "contracting:1305m2", "100518256": "contracting:1332kp", "100163078": "contracting:1305m3",
                      "100179142": "contracting:1305m4", "100163558": "contracting:1333mk", "100166321": "contracting:1333mg",
                      "100099213": "agency:noaa"},
    "sbir_component": None,  # a civilian agency: its SBIR topics are not on the DoD portal (deferred)
    "sbir_commands": {},
    # NOAA's laboratories, science centers and field offices are often where the work is done rather than whose it is.
    "performer_types": ("technical_center", "field_activity"),
    # A line office as contract descriptions and forecast rows write it ("NWS MEL Melbourne Emer Power Generator").
    "office_key_re": r"\b(NESDIS|NWS|NMFS|NOS|OAR|OMAO)\b",
    "shared_sources": ("govinfo_api",),
    # The line offices own NOAA's requirements, as the program offices own the Navy's.
    "owner_types": ("department",),
    "families": {"noaa_site": "organization", "commerce_procurement_forecast": "forecast",
                 "noaa_congressional_justification": "budget"},
    "moved_urls": {},
    "generic_words": ("noaa", "national oceanic and atmospheric administration", "department of commerce", "doc", "commerce",
                      "acquisition and grants office", "ago", "sources sought", "industry day", "notice of intent"),
    "topic_re": r"(?!)",  # SBIR/STTR topics are deferred; no topic code is read yet
    "piid_re": rf"\b{_OFFICE}-?\d{{2}}-?[A-Z]-?(?:[A-Z0-9]{{4}}-?)?\d{{4}}\b|\b{_LEGACY}(?!\d)",
    "solicitation_re": rf"\b{_OFFICE}-?\d{{2}}-?[A-Z]-?(?:[A-Z0-9]{{4}}-?)?[A-Z0-9]{{4}}\b",
    "fedreg_conditions": [("conditions[agencies][]", "national-oceanic-and-atmospheric-administration")],
    "fedreg_label": "National Oceanic and Atmospheric Administration",
    "fedreg_name_pattern": None,
    "congress_pattern": _NAMES,
    "congress_label": "NOAA",
    # NOAA is funded through the Commerce, Justice, Science bill; Science, Space, and Technology and Natural Resources
    # hold its authorizing hearings.
    "committees": {
        "reports": [("appropriations", r"^COMMERCE, JUSTICE, SCIENCE, AND RELATED AGENCIES APPROPRIATIONS (?:BILL|ACT),? (\d{4})$")],
        "names": {("appropriations", "h"): "House Appropriations Committee", ("appropriations", "s"): "Senate Appropriations Committee"},
        "house_feeds": {"AP00": "https://docs.house.gov/Committee/RSS.ashx?Code=AP00",
                        "SY00": "https://docs.house.gov/Committee/RSS.ashx?Code=SY00",
                        "II00": "https://docs.house.gov/Committee/RSS.ashx?Code=II00"}},
    "oversight": {"agency": _AGENCY,
                  "queries": ("NOAA", "National Oceanic and Atmospheric Administration"),
                  # The Commerce OIG files every report under the Department, so the department name admits nothing here.
                  "reviewed_re": r"National Oceanic and Atmospheric Administration|\bNOAA\b",
                  "names_re": r"\b(NOAA|National Oceanic and Atmospheric Administration|National Weather Service|NESDIS|National Marine Fisheries Service|National Ocean Service|weather satellite)\b",
                  "gao_query": "GAO report NOAA weather satellite acquisition program"},
    # No speech archive is registered; testimony reaches the layer through the House committee repository.
    "remarks": {"speeches": None, "testimony": None, "article_re": r"^(?!)",
                "names_re": r"\b(NOAA|National Oceanic and Atmospheric Administration|National Weather Service|NESDIS|National Marine Fisheries Service|National Ocean Service)\b",
                "agency": _AGENCY,
                "testimony_pages": [],
                "conference_query": 'conference agenda speakers NOAA "National Oceanic and Atmospheric Administration" "Assistant Administrator" OR "NOAA Administrator" keynote panel',
                "own_domains": ["noaa.gov", "commerce.gov"],
                "providers": {"speech": "noaa_site", "testimony": "house_committee_repository", "statement": "house_committee_repository",
                              "conference": "conference_pages_exa"}},
    "news": {"feeds": [{"publisher": "NOAA", "url": "https://www.noaa.gov/rss.xml"}],
             "official_names": {"www.noaa.gov": "NOAA", "www.commerce.gov": "Department of Commerce"},
             "standing_terms": ["NOAA", "National Oceanic and Atmospheric Administration", "Acquisition and Grants Office",
                                "National Weather Service", "NESDIS", "National Marine Fisheries Service", "acquisition", "contract award"]},
    # Not yet read against the docket: the agency filter is written as NOAA's name, not checked against GAO's list.
    "protests": {"listing": "https://www.gao.gov/legal/bid-protests/search?agency=National%20Oceanic%20and%20Atmospheric%20Administration&page={page}",
                 "agency": "National Oceanic and Atmospheric Administration", "max_pages": 10},
    # Deferred: the shape is kept and the books folder stays empty.
    "budget": {"pb_label": "National Oceanic and Atmospheric Administration", "books_dir": "jbooks_noaa",
               "provider": "noaa_congressional_justification"},
    "people": {"department": "agency:noaa",
               "executive": ("administrator", "deputy administrator", "under secretary", "assistant secretary", "assistant administrator",
                             "chief scientist", "director"),
               "remarks_providers": None, "staff_listing": None},
    # The Commerce weekly forecast carries every Commerce bureau on one sheet; NOAA's rows are the ones whose
    # Organization column opens with "NOAA". The release format reads the whole sheet (no row filter), so scope is "all".
    "forecast": {"pack_glob": "noaa_20??-??", "label": "Commerce procurement forecast", "short": "forecast", "memory_tool": "org_memory_noaa.py",
                 "providers": {"noaa": "commerce_procurement_forecast"},
                 "contract_re": rf"{_OFFICE}\d{{2}}[A-Z](?:[A-Z0-9]{{4}})?\d{{4}}(?!\d)",  # NOAA PIIDs as the forecast writes them, hyphens gone
                 "releases": [
                     {"key": "noaa_2025-12", "activity": "noaa", "match": "DOC%20Weekly%20Forecast%20Report", "release_date": "2025-12-09",
                      "release_note": "the weekly report replaced in place at one address, dated by its Wayback Machine capture of 2025-12-09; "
                                      "its newest row was created 2025-12-08; every Commerce bureau on one sheet, NOAA's 1,980 of 3,159 rows",
                      "sheet": "Sheet1", "header_row": 3, "scope": "all", "headers": DOC_HEADERS, "keep": ("organization", "NOAA")}]},
    "pilot_offices": ("NESDIS", "NWS", "NMFS", "NOS", "OAR", "OMAO"),
    "coverage_orgs": ["AGO", "NESDIS", "NWS", "NMFS", "NOS", "OAR", "OMAO"],
    "coverage_org_nodes": {},  # the matrix is written by hand (or by its own script) until the nodes are named here
    "coverage_department_wide": {},
    # A NOAA notice names its office by the line office's acronym or an AGO division's; NOAA has no hull designators.
    "reading": {"office_code_re": r"\b(?:NESDIS|NWS|NMFS|NOS|OAR|OMAO|AGO|EAD|WAD|CSAD|SIAD|GMD)\b",
                "hull_re": r"(?!)",
                # A solicitation number (request for proposals, quotations or bids) with its spaces removed.
                "rfp_re": rf"{_OFFICE}-?\d{{2}}-?[RQB]-?(?:[A-Z0-9]{{4}}-?)?\d{{4}}(?![0-9])"},
}

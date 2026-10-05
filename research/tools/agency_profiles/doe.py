"""The Department of Energy profile: Headquarters procurement, the Office of Science, ARPA-E, EERE, NNSA and the field
and site offices that sign awards. The national laboratories are management and operating (M&O) contractors: the
memory holds each as a technical center reached through its own subcontracting, never as a buying office of the
Department, so no laboratory's SAM.gov office is swept or loaded as a contracting office.

Sources behind the codes (saved in the shared ledger on 2026-09-26, notes marked [doe]): USAspending
/api/v2/agency/089/ and its FY2026 sub-agency page (offices and obligations), the SAM.gov organization records of the
Department (100011980, FPDS 8900) and of the thirteen offices below, the SAM.gov search of the Department's active
notices, and the OSBP acquisition forecast of 2026-09-11 (CSV, 868 rows).
"""

# DOE forecast headers (first line, lower-cased prefix) -> the field names the Army releases use. The file lists the
# current contracts that end, so the owning office is the Program Office column, the date is the current contract's
# performance end (not an anticipated award date, so no award quarter is derived), and the Contract Type column holds
# the instrument (Contract, Delivery / Task Order, Purchase Order, BPA Call).
DOE_HEADERS = {
    "performance end date": "performance_end_date", "naics code": "naics", "naics description": "naics_description",
    "program office": "command", "current incumbent": "incumbent_contractor", "current contract number": "existing_contract_number",
    "acquisition description": "requirement_title", "estimated value range": "anticipated_total_value",
    "contracting officers business size selection": "business_size", "type of set aside": "procurement_method",
    "contract type": "procurement_instrument", "principal place of performance state": "place_of_performance",
    "small business program manager": "small_business_email",
}
DOE_RELEASES = [
    {"key": "doe_2026-09", "activity": "doe", "match": "osbp-acquisition-forecast-public-version-web-20260911", "release_date": "2026-09-11",
     "release_note": "the DOE Headquarters and Federal Field Office Acquisition Forecast, published September 11, 2026 and updated monthly "
                     "(OSBP page); a CSV of current contracts by performance end date",
     "sheet": "DOE Acquisition Forecast", "header_row": 1, "scope": "all", "headers": DOE_HEADERS},
]

# SAM.gov organization id -> office code (the aacCode each record states), for the offices that sign most of the
# Department's FY2026 obligations. Every one sits at level 3 under the Department in SAM.gov.
SAM_ORGS = {"100167427": "892332", "100188159": "892432", "100515927": "892431", "100188157": "892430", "100187021": "893039",
            "100188149": "892330", "100188269": "892331", "100173705": "893037", "100188172": "893030", "100188175": "893033",
            "100188161": "892434", "100188160": "892433", "100183461": "897030"}

PROFILE = {
    "key": "doe",
    "label": "Department of Energy",
    "short": "DOE",
    "database": "doe_proof",
    "agency": {"toptier_code": "089", "toptier_name": "Department of Energy", "toptier_abbreviation": "DOE",
               "subtier_code": "8900", "subtier_name": "Department of Energy", "subtier_abbreviation": "DOE",
               "node": "agency:doe",
               # FAR 4.1603: an instrument number opens with its issuing office's six-character code (89243126CSC000215 is
               # the SC Oak Ridge Office, 892431; 89303021CMA000062 Headquarters Procurement Services, 893030).
               "office_code_re": r"89\d{4}",
               "small_business_directory": "https://www.energy.gov/osbp/small-business-program-manager-directory",
               # The layer covers the whole department, so grants are filtered on the toptier agency.
               "assistance_tier": "toptier"},
    # FPDS: the offices by the code USAspending prints for FY2026 (the thirteen swept on SAM.gov, the Environmental
    # Management site offices, the Strategic Petroleum Reserve and OCED). The Power Marketing Administrations and FERC
    # are left out. The swept offices sign about 97 percent of the Department's FY2026 obligations, so no funding-agency
    # sweep is added: sweeping funding agency 8900 would take every award the Department signs, office by office.
    "fpds_offices": {"892332": "NNSA MO CONTRACTING", "892432": "IDAHO OPERATIONS OFFICE", "892431": "SC OAK RIDGE OFFICE",
                     "892430": "SC CHICAGO SERVICE CENTER", "893039": "HANFORD FIELD OFFICE", "892330": "NNSA NAVAL REACTORS LAB FLD OFFICE",
                     "892331": "NNSA NON-MO CNTRCTNG OPS DIV", "893037": "SAVANNAH RIVER OPERATIONS OFFICE",
                     "893030": "HEADQUARTERS PROCUREMENT SERVICES", "893033": "EM-ENVIRONMENTAL MGMT CON BUS CTR",
                     "892434": "GOLDEN FIELD OFFICE", "892433": "NATIONAL ENERGY TECHNOLOGY LABORATORY",
                     "897030": "ADVANCED RSRCH PROJ AGENCY ARPA-E", "893035": "EM-OAK RIDGE", "893031": "EM-PORTSMOUTH/PADUCAH PROJECT OFC",
                     "893042": "EM-IDAHO", "893034": "EM-LOS ALAMOS", "893032": "EM-CARLSBAD", "892435": "STRATEGIC PETROLEUM RESERVE",
                     "892436": "OFFICE OF CLEAN ENERGY DEMONSTRATIONS (OCED)"},
    "fpds_funding_agencies": {},
    "sam_dir": "sam_notices_doe",
    "sam_orgs": SAM_ORGS,
    # ponytail: sam_notices.org_id_of reads a level-5 office (the Defense depth); the Department's offices sit at level 3,
    # so every swept office is listed by id and none by code.
    "sam_codes": (),
    "sam_org_nodes": {org: f"contracting:{code}" for org, code in SAM_ORGS.items()},
    # SBIR/STTR topics are deferred: the Department's topics are not on the DoD portal.
    "sbir_component": None,
    "sbir_commands": {},
    # The national laboratories and the contractor-managed sites are where the work is done, reached by subcontract.
    "performer_types": ("technical_center", "field_activity"),
    "office_key_re": r"(?!)",  # ponytail: the forecast names its program offices in full; no code pattern yet
    "shared_sources": ("govinfo_api",),  # the NDAA reports the Navy layer saved carry the NNSA titles
    "owner_types": (),
    "families": {"doe_acquisition_forecast": "forecast", "doe_osbp_pages": "organization", "doe_budget_justification": "budget"},
    "moved_urls": {},
    "generic_words": ("doe", "department of energy", "u.s. department of energy", "energy department", "management and operating",
                      "m&o", "funding opportunity announcement", "foa", "sources sought", "industry day"),
    "fedreg_conditions": [("conditions[agencies][]", "energy-department")],
    "fedreg_label": "Department of Energy",
    "fedreg_name_pattern": None,
    # ponytail: topics are deferred (sbir_component None) and no DOE topic sample was read, so the pattern matches nothing.
    "topic_re": r"(?!)",
    # Contract numbers from the forecast's 868 current contracts: the office-code form (89243126CSC000215, orders such as
    # 89243323FFE400463) and the older DE- forms the M&O and site contracts still carry (DE-AC05-00OR22725, DE-NA0003525).
    "piid_re": r"\b(?:89\d{4}-?\d{2}-?[A-Z]-?[A-Z]{2}\d{6}|DE-?[A-Z]{2}\d{2}-?\d{2}[A-Z]{2}\d{5}|DE-?[A-Z]{2}\d{7})\b",
    # Solicitation numbers from the saved SAM.gov notices (89243326QFE000570, 89243126RSC000170, 89233126NNA000150) and the
    # Department's funding opportunity announcements (DE-FOA-0003412). A laboratory's own subcontract numbers are its own.
    "solicitation_re": r"\b(?:89\d{4}-?\d{2}-?[A-Z]-?[A-Z]{2}\d{6}|DE-FOA-\d{7})\b",
    "congress_pattern": (r"\bDepartment of Energy\b|\bDOE\b|\bNNSA\b|\bNational Nuclear Security Administration\b|\bARPA-E\b"
                         r"|\bOffice of Science\b|\bEnergy Efficiency and Renewable Energy\b|\bEERE\b|\bOffice of Environmental Management\b"
                         r"|\bNaval Reactors\b|\bnational laborator(?:y|ies)\b"),
    "congress_label": "DOE",
    "committees": {
        "reports": [("ndaa", r"(?:^|\s)NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR (\d{4})$"),
                    # H. Rept. 119-213 "ENERGY AND WATER DEVELOPMENT AND RELATED AGENCIES APPROPRIATIONS BILL, 2026"; the Senate
                    # reports omit "AND RELATED AGENCIES".
                    ("appropriations", r"^ENERGY AND WATER DEVELOPMENT (?:AND RELATED AGENCIES )?APPROPRIATIONS (?:BILL|ACT),? (\d{4})$")],
        "names": {("ndaa", "h"): "House Armed Services Committee", ("ndaa", "s"): "Senate Armed Services Committee",
                  ("appropriations", "h"): "House Appropriations Committee", ("appropriations", "s"): "Senate Appropriations Committee"},
        # The Appropriations, Energy and Commerce, and Science, Space, and Technology meeting feeds (each feed's title
        # names its committee, saved 2026-09-26).
        "house_feeds": {"AP00": "https://docs.house.gov/Committee/RSS.ashx?Code=AP00",
                        "IF00": "https://docs.house.gov/Committee/RSS.ashx?Code=IF00",
                        "SY00": "https://docs.house.gov/Committee/RSS.ashx?Code=SY00"}},
    "oversight": {"agency": "Department of Energy (Headquarters, the Office of Science, ARPA-E, EERE, the National Nuclear Security Administration "
                            "and the field and site offices that sign its awards)",
                  "queries": ("Department of Energy", "National Nuclear Security Administration"),
                  "reviewed_re": r"Department of Energy\b|\bDOE\b|National Nuclear Security Administration",
                  "names_re": r"\b(Department of Energy|DOE|NNSA|National Nuclear Security Administration|ARPA-E|Office of Science"
                              r"|Environmental Management|national laborator(?:y|ies))\b",
                  "gao_query": "GAO report Department of Energy contract and project management NNSA Environmental Management"},
    # No speech or testimony archive is registered for the Department yet.
    "remarks": {"speeches": None, "testimony": None, "article_re": r"^(?!)",
                "names_re": r"\b(Department of Energy|DOE|NNSA|National Nuclear Security Administration|ARPA-E|Office of Science)\b",
                "agency": "Department of Energy (Headquarters, the Office of Science, ARPA-E, EERE, the National Nuclear Security Administration "
                          "and the field and site offices that sign its awards)",
                "testimony_pages": [],
                "conference_query": 'energy conference agenda speakers "Department of Energy" "Under Secretary" OR "NNSA Administrator" OR "Office of Science" keynote panel',
                "own_domains": ["energy.gov"],
                "providers": {"speech": "house_committee_repository", "testimony": "house_committee_repository",
                              "statement": "house_committee_repository", "conference": "conference_pages_exa"}},
    "news": {"feeds": [],  # no Department feed is registered yet
             "official_names": {"www.energy.gov": "Department of Energy"},
             "standing_terms": ["Department of Energy", "NNSA", "Office of Science", "ARPA-E", "management and operating contract",
                                "national laboratory", "acquisition", "contract award"]},
    "protests": {"listing": "https://www.gao.gov/legal/bid-protests/search?agency=Department%20of%20Energy&page={page}",
                 "agency": "Department of Energy", "max_pages": 20},
    # Budget is deferred: the Department's justification volumes are not saved and no exhibit reader exists for them.
    "budget": {"pb_label": "Department of Energy", "books_dir": "jbooks_doe",
               "provider": "doe_budget_justification"},
    "people": {"department": "agency:doe",
               "executive": ("secretary of energy", "deputy secretary", "under secretary", "assistant secretary", "administrator",
                             "director", "manager", "head of contracting activity"),
               "remarks_providers": None, "staff_listing": None},
    "forecast": {"pack_glob": "doe_20??-??", "label": "acquisition forecast", "short": "forecast", "memory_tool": "org_memory_doe.py",
                 "providers": {"doe": "doe_acquisition_forecast"},
                 # The forecast's contract numbers as written (no hyphens in the office-code form, hyphens in the DE- forms).
                 "contract_re": r"89\d{6}[A-Z]{3}\d{6}(?!\d)|DE-[A-Z]{2}\d{2}-\d{2}[A-Z]{2}\d{5}|DE-[A-Z]{2}\d{7}",
                 "releases": DOE_RELEASES},
    # The Program Office values of the forecast with the most rows among the offices in scope.
    "pilot_offices": ("National Nuclear Security Administration", "Office of Science", "Assistant Secretary for Environmental Management",
                      "Assistant Secretary for Fossil Energy", "Assistant Secretary for Nuclear Energy",
                      "Assistant Secretary for Energy Efficiency and Renewable Energy", "Advanced Research Projects Agency - Energy (ARPA-E)"),
    "coverage_orgs": ["DOE HQ", "NNSA", "SC", "EM", "EERE", "ARPA-E", "FE", "NE", "National laboratories"],
    "coverage_org_nodes": {},  # the matrix is written by hand (or by its own script) until the nodes are named here
    "coverage_department_wide": {},
    # A first pattern for how a notice names a DOE element by code, not yet run against a corpus; no hull designators.
    "reading": {"office_code_re": r"\b(?:NA-[A-Z0-9]{2,4}|EM-\d{1,3}|SC-\d{1,2}|EERE|FECM|CESER|OCED|GDO|MESC|LPO|ARPA-E|NNSA)\b",
                "hull_re": r"(?!)",
                "rfp_re": r"89\d{4}-?\d{2}-?R-?[A-Z]{2}\d{6}(?![0-9])"},
}

#!/usr/bin/env python3
"""Read a job announcement as a dated observation about the office that is hiring.

    python research/tools/jobs.py build                  # model every saved announcement listing and page
    python research/tools/jobs.py read CONTROL|job:<id>|WORD   # one posting's record
    python research/tools/jobs.py sweep [--fetch] [--days N] [--limit N] [--codes NV39,NV24]
    python research/tools/jobs.py --selfcheck

A vacancy an office announces is a source statement like a notice or an article: the bytes are saved and recorded
in `research/sources/documents_manifest.jsonl` first, and the record built here quotes the announcement's own words.
The source is USAJobs, the government's recruiting site: its historic announcement API lists every announcement of
an agency code inside a date window (series, grade, dates, sites, opening status) without a key, and the announcement
page carries the summary, the duties and the requirements. Each claim carries the passage it rests on, what the
memory already holds about it (a new signal, a corroboration or a conflict), a confidence and what a reviewer still
has to verify. Nothing here is promoted into the organization memory: a posting is an observation about hiring,
never authority, parentage or ownership, and an announcement is an intention to hire, not a hire made
(research/docs/21_job_postings_as_a_signal.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import EVENTS as EVENTS_DIR, MANIFEST, NOTE_TAG, P, note_is_ours  # noqa: E402
from markdown import html_to_markdown  # noqa: E402
from news import HEDGE_RE, RELIABILITY, as_date, entities_in, is_prose, manifest_rows, memory, relate, sentences  # noqa: E402

HIRING = P["hiring"]
API = "https://data.usajobs.gov/api/historicjoa"
ANNOUNCEMENT = "https://www.usajobs.gov/job/{control}"
ANNOUNCEMENT_RE = re.compile(r"^https://www\.usajobs\.gov/job/(\d+)/?$")
PROVIDER = "usajobs_historic_joa"  # the registry row every posting is filed under
RECORDS = EVENTS_DIR / "hiring_observations.json"
SOURCE_TYPE = "official announcement"  # USAJobs publishes the agency's own announcement; the reliability table is news.py's

# The Defense Acquisition Workforce and the series beside it whose vacancies say what an office is staffing to buy or
# run: contracting, program management, engineering and technical management, business and financial management,
# life-cycle logistics, test and evaluation, information technology and quality. A posting in another series is
# still recorded; it is not loaded unless its text names an office or a requirement the memory holds.
ACQUISITION_SERIES = {
    "1102": "contracting", "1105": "contracting", "1101": "business", "1106": "contracting",
    "0340": "program management", "0343": "program management", "0301": "program management",
    "0801": "engineering", "0830": "engineering", "0850": "engineering", "0854": "engineering", "0855": "engineering",
    "0861": "engineering", "0871": "engineering", "0896": "engineering", "1550": "engineering", "1515": "engineering",
    "1301": "engineering", "1310": "engineering",
    "0501": "business", "0510": "business", "0560": "business", "1160": "business",
    "0346": "logistics", "2003": "logistics", "2010": "logistics", "1670": "logistics",
    "2210": "information technology", "1910": "quality", "0080": "security",
}
# A role whose vacancy is the office's own leadership, as USAJobs titles write it.
LEADER_TITLE_RE = re.compile(r"\b(?:supervisory )?(?:program manager|deputy program manager|program executive officer|director|deputy director"
                             r"|executive director|division head|department head|commanding officer|portfolio acquisition executive)\b", re.I)
# What the listing states about itself, in USAJobs' words, and what each is: an announcement is an intention to hire
# until a selection is stated; a cancelled one was withdrawn.
STATUS_READING = {"Accepting applications": "announced", "Applications under review": "under review", "Job closed": "closed",
                  "Candidate selected": "selected", "Job canceled": "cancelled"}
POSTURE = {"announced": "intent", "under review": "intent", "closed": "intent", "not stated": "intent", "selected": "action", "cancelled": "withdrawn"}
# A flyer of anticipated vacancies states less than an open announcement does.
INTENT_RE = re.compile(r"\b(anticipated vacanc|may or may not be actual vacancies|public notice flyer|no actual vacanc|expected vacanc|anticipates? (?:a |an )?vacanc)", re.I)
# A duties sentence is one kind of statement. The first pattern that matches names it; the rest is the office's work.
STATEMENT_PATTERNS = [
    ("placement", re.compile(r"\b(?:position is (?:located|assigned) (?:in|at|within|to)|located (?:in|at|within) the|serves? as (?:the |a |an )?[^.]{0,80}? (?:for|of|within|in) the"
                             r"|assigned to the|part of the|within the|reports? (?:directly )?to the|under the)\b", re.I)),
    ("stand_up", re.compile(r"\b(newly (?:established|created|formed|stood up)|new(?:ly)? organi[sz]ation|stand(?:s|ing)? up|establish(?:es|ed|ing|ment of) (?:a |an |the )?(?:new )?(?:office|command|portfolio|program executive|division|directorate)|reorganization|realign)", re.I)),
    ("workload", re.compile(r"\b(support(?:s|ing)? the|serves? as|responsible for|manag(?:e|es|ing)|lead(?:s|ing)?|oversee(?:s|ing)?|execut(?:e|es|ing)|administer(?:s|ing)?|provid(?:e|es|ing)|develop(?:s|ing)?|plan(?:s|ning)?|award(?:s|ing)?|negotiat(?:e|es|ing))\b", re.I)),
]
PAGE_END_RE = re.compile(r"^#{1,3}\s+(?:How you will be evaluated|Benefits|Required documents|How to Apply|Fair and transparent)\b", re.I | re.M)
# The sections every announcement repeats about employment itself, not about the office's work.
BOILERPLATE_RE = re.compile(r"^###\s+(?:Conditions of employment|Education)\b.*?(?=^#{1,3}\s|\Z)", re.I | re.M | re.S)
# Acronyms every announcement writes that name no program: the pay systems, the regulations and the hiring authorities.
# USAJobs writes its titles in capitals, so the title's words are read for offices, never as program codes.
NOT_PROGRAMS = {"aps", "dawia", "far", "dfars", "nmcars", "ctap", "ictap", "rpl", "msp", "ncs", "dod", "don", "dow", "gs", "nh", "nj", "nk", "dp", "dr", "dj",
                "dk", "de", "st", "cy", "naf", "ssn", "ein", "sf", "opf", "usc", "cfr", "eeo", "ada", "pcs", "tsp", "fers", "csrs", "hr", "hro", "ocho", "dcpas",
                "veoa", "vra", "vet", "usa", "u.s", "gs-", "wg", "ws", "wl", "eod", "ppp", "dha", "drp", "std", "kdb", "ksa", "moa", "mou", "faq", "pdf", "usajobs"}
# The listing's fields that travel on the record, in USAJobs' names, so a reader can hold the record to the saved JSON.
FIELDS = ("usajobsControlNumber", "announcementNumber", "positionTitle", "hiringDepartmentCode", "hiringDepartmentName", "hiringAgencyCode",
          "hiringAgencyName", "hiringSubelementName", "positionOpenDate", "positionCloseDate", "positionExpireDate", "positionOpeningStatus",
          "announcementClosingTypeDescription", "payScale", "minimumGrade", "maximumGrade", "promotionPotential", "minimumSalary", "maximumSalary",
          "salaryType", "totalOpenings", "appointmentType", "workSchedule", "supervisoryStatus", "teleworkEligible", "securityClearanceRequired",
          "securityClearance", "serviceType", "whoMayApply", "travelRequirement", "vendor")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_row(row: dict) -> None:
    with MANIFEST.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


# ---------------------------------------------------------------- the listing

def search_url(code: str, start: str, end: str, token: str = "") -> str:
    """The API address for one agency code and window; a continuation token asks for the next page."""
    query = {"HiringAgencyCodes": code, "StartPositionOpenDate": start, "EndPositionOpenDate": end}
    if token:
        query["continuationtoken"] = token
    return API + "?" + urllib.parse.urlencode(query)


def window(days: int, today: date | None = None) -> tuple[str, str]:
    end = today or date.today()
    return (end - timedelta(days=days)).isoformat(), end.isoformat()


def search_params(url: str) -> dict:
    """The agency code, the window and the page token a saved search address carries."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    low = {k.lower(): v[0] for k, v in query.items()}
    return {"agency_code": low.get("hiringagencycodes", ""), "start": low.get("startpositionopendate", ""),
            "end": low.get("endpositionopendate", ""), "token": low.get("continuationtoken", "")}


def is_search(row: dict) -> bool:
    """A saved answer of the historic announcement API taken by this layer's sweep."""
    return kept_page(row) and note_is_ours(row.get("note") or "") \
        and bool(re.match(r"^jobs sweep\b", row.get("note") or "")) and (row.get("url") or "").startswith(API)


def is_announcement(row: dict) -> bool:
    """A saved announcement page taken by this layer's sweep, or by hand for it."""
    return kept_page(row) and note_is_ours(row.get("note") or "") \
        and bool(re.match(r"^jobs announcement\b", row.get("note") or "")) and bool(ANNOUNCEMENT_RE.match(row.get("url") or ""))


def listing_items(body: bytes) -> tuple[list[dict], str]:
    """The announcements one API page lists, and the address of the next page (empty on the last)."""
    if not body.strip():
        return [], ""  # 204: no announcement in the window
    answer = json.loads(body.decode("utf-8", "replace"))
    items = [i for i in (answer.get("data") or []) if isinstance(i, dict) and i.get("usajobsControlNumber")]
    nxt = (answer.get("paging") or {}).get("next") or ""
    return items, (urllib.parse.urljoin(API, nxt) if nxt else "")


def series_of(item: dict) -> list[str]:
    return sorted({str(c.get("series")) for c in item.get("jobcategories") or [] if c.get("series")})


def career_field(series: list[str]) -> str:
    fields = sorted({ACQUISITION_SERIES[s] for s in series if s in ACQUISITION_SERIES})
    return ", ".join(fields)


def locations_of(item: dict) -> list[str]:
    out = []
    for loc in item.get("positionlocations") or []:
        place = ", ".join(p for p in (loc.get("positionLocationCity"), loc.get("positionLocationState")) if p)
        if place and place not in out:
            out.append(place)
    return out


def hiring_paths(item: dict) -> list[str]:
    return sorted({h.get("hiringPath") for h in item.get("hiringpaths") or [] if h.get("hiringPath")})


def organization_text(item: dict) -> str:
    """The words the listing uses for who is hiring: the agency and the subelement as written."""
    return " ".join(str(item.get(k) or "") for k in ("hiringAgencyName", "hiringSubelementName")).strip()


# ---------------------------------------------------------------- the page

def page_text(body: bytes) -> str:
    """The announcement's own words: its Markdown rendering up to the evaluation and application boilerplate, without
    the conditions of employment and the education rules every announcement repeats."""
    text = html_to_markdown(body.decode("utf-8", "replace"))
    cut = PAGE_END_RE.search(text)
    text = BOILERPLATE_RE.sub("", text[:cut.start()] if cut else text)
    # A first-level heading is the page's furniture: the capitalised title, "Benefits", "How to Apply".
    return "\n".join(line for line in text.split("\n") if not re.match(r"^#\s", line))


def named_in(text: str, title: str, mem: dict) -> dict:
    """The entities the announcement's words name. The title is read for offices (the alias table is case-blind), not
    for program codes, because USAJobs writes titles in capitals and every word would read as a code."""
    ents = entities_in(text, mem)
    for office in mem["tracer"].resolve_offices(title):
        if office["office"] not in ents["organizations"]:
            ents["organizations"].append(office["office"])
            ents["organizations_as_written"].append({"office": office["office"], "as_written": office.get("context", "")[:160], "former": office["former"]})
    ents["organizations"].sort()
    ents["programs"] = [t for t in ents["programs"] if t not in NOT_PROGRAMS]
    if not ents["programs"]:
        # A requirement joined through a program name alone goes with the name; one joined through its identifier stays.
        ents["requirements"] = [r for r in ents["requirements"] if r in text]
    return ents


def statement_type_of(passage: str, ents: dict, mem: dict | None) -> str:
    """placement when the sentence puts the position in an office the memory knows; stand_up when it states a new
    organization; workload when it states work the position will do; existence when it only names something."""
    for kind, pattern in STATEMENT_PATTERNS:
        if not pattern.search(passage):
            continue
        if kind == "placement" and not ents["organizations"]:
            continue
        if kind == "stand_up" and not (ents["organizations"] or ents["programs"]):
            continue
        return kind
    return "existence" if ents["organizations"] or ents["programs"] or ents["contracts"] or ents["solicitations"] else "listing"


# What the memory can be asked about each kind: news.relate's questions, which read the same stored record.
RELATE_AS = {"placement": "parentage", "stand_up": "consolidation", "workload": "performance", "existence": "existence"}


def confidence_of(passage: str, kind: str, flyer: bool) -> tuple[str, list[str]]:
    """How far the claim can be taken, and what a reviewer still has to confirm. A posting is the agency's own
    announcement, so it starts high; a hedge, or a flyer of anticipated vacancies, lowers it."""
    order = ["low", "medium", "high"]
    level = order.index(RELIABILITY[SOURCE_TYPE])
    verify = ["a vacancy announced is an intention to hire, not a position filled"]
    if flyer:
        level -= 1
        verify.append("a public notice flyer states anticipated vacancies, which may or may not be filled")
    if HEDGE_RE.search(passage) or INTENT_RE.search(passage):
        level -= 1
        verify.append("the passage states an intention, not an action taken")
    if kind in ("placement", "stand_up"):
        verify.append("the office's own page or a release stating the placement")
    return order[max(0, min(level, 2))], verify


def posting_record(item: dict, search: dict, page: dict | None, body: bytes | None, mem: dict, listings: list[dict] | None = None) -> dict:
    """One announcement as a dated observation: the listing's fields, the page's claims, the entities and links."""
    control = str(item["usajobsControlNumber"])
    series = series_of(item)
    org = HIRING["usajobs_agency_codes"].get(str(item.get("hiringAgencyCode") or ""), "")
    status = item.get("positionOpeningStatus")
    reading = STATUS_READING.get(status, "not stated")
    text = page_text(body) if body else ""
    flyer = bool(INTENT_RE.search(text))
    title = item.get("positionTitle") or ""
    subelement = str(item.get("hiringSubelementName") or "")
    whole = named_in(" ".join(filter(None, [subelement, text])), title, mem)
    claims, background, seen = [], 0, set()
    # The listing itself is the first claim: who is hiring for what, in the listing's own fields. The subelement is
    # the listing's word for the office; the agency name is the code's, and travels as `hiring`, not as an entity.
    listing_ents = named_in(subelement, title, mem)
    if org and org in mem["node_ids"]:
        relation, why = "corroborates", f"the memory holds {org}, the organization USAJobs files this announcement under, as an organization of this agency"
    elif org:
        relation, why = "new signal", f"the profile maps agency code {item.get('hiringAgencyCode')} to {org}, which the memory does not hold"
    else:
        relation, why = "new signal", f"agency code {item.get('hiringAgencyCode')} is not one the profile maps"
    level, verify = confidence_of("", "listing", flyer)
    claims.append({"statement_type": "vacancy", "passage": item.get("positionTitle") or "", "fields": {k: item.get(k) for k in FIELDS if item.get(k) is not None},
                   "subjects": sorted(set(([org] if org else []) + listing_ents["organizations"])), "people": [],
                   "programs": listing_ents["programs"], "contracts": listing_ents["contracts"], "solicitations": listing_ents["solicitations"],
                   "relation": relation, "relation_note": why, "confidence": level, "verify": verify, "stated_on": item.get("positionOpenDate") or ""})
    for passage in sentences(text):
        if passage in seen or not is_prose(passage):
            continue  # the page repeats its summary; a field label or a menu line is not a statement
        seen.add(passage)
        ents = named_in(passage, "", mem)
        if not (ents["organizations"] or ents["programs"] or ents["contracts"] or ents["solicitations"] or ents["requirements"]):
            continue
        kind = statement_type_of(passage, ents, mem)
        if kind == "existence" and not (ents["requirements"] or [c for c in ents["contracts"] if c in mem["known_contracts"]]
                                        or [s for s in ents["solicitations"] if s in mem["known_sols"]]):
            background += 1  # a sentence that only names the office is background, not a claim
            continue
        if kind == "placement":
            # A contracting office that resolves beside its own command ("Naval Information Warfare Systems Command" names
            # both) is the command's contracting arm, not a second place in the org chart; the placement is read without it.
            types = {n["id"]: n["type"] for n in mem["seed"]["nodes"]}
            if any(types.get(o) == "command" for o in ents["organizations"]):
                ents = {**ents, "organizations": [o for o in ents["organizations"] if types.get(o) != "contracting_office"]}
        relation, why = relate(RELATE_AS[kind], ents, mem)
        level, verify = confidence_of(passage, kind, flyer)
        claims.append({"statement_type": kind, "passage": passage[:600], "subjects": ents["organizations"], "people": ents["people_as_written"],
                       "programs": ents["programs"], "contracts": ents["contracts"], "solicitations": ents["solicitations"],
                       "relation": relation, "relation_note": why, "confidence": level, "verify": verify, "stated_on": as_date(passage)})
    leader = bool(LEADER_TITLE_RE.search(item.get("positionTitle") or ""))
    if leader:
        for office in whole["organizations"]:
            held = mem["leads"].get(office)
            if held:
                claims[0]["verify"].append(f"the memory holds {', '.join(sorted(held))} leading {office}; a vacancy for a leader's role says who is sought, never who leads")
    relations = {c["relation"] for c in claims}
    rollup = "conflicts" if "conflicts" in relations else ("corroborates" if "corroborates" in relations else "new signal")
    field = career_field(series)
    acquisition = bool(field)
    return {
        "id": f"job:{control}", "control_number": control, "announcement_number": item.get("announcementNumber") or "",
        "title": item.get("positionTitle") or "", "url": ANNOUNCEMENT.format(control=control),
        "hiring": {"department_code": item.get("hiringDepartmentCode") or "", "department": item.get("hiringDepartmentName") or "",
                   "agency_code": item.get("hiringAgencyCode") or "", "agency": item.get("hiringAgencyName") or "",
                   "subelement": item.get("hiringSubelementName") or "", "org": org,
                   "org_basis": "the profile maps the agency code USAJobs files the announcement under" if org else "no mapping for this agency code"},
        "series": series, "career_field": field, "acquisition_workforce": acquisition,
        "grade": {"pay_scale": item.get("payScale") or "", "minimum": item.get("minimumGrade") or "", "maximum": item.get("maximumGrade") or "",
                  "promotion_potential": item.get("promotionPotential") or ""},
        "salary": {"minimum": item.get("minimumSalary"), "maximum": item.get("maximumSalary"), "type": item.get("salaryType") or ""},
        "openings": item.get("totalOpenings") or "", "appointment_type": item.get("appointmentType") or "", "work_schedule": item.get("workSchedule") or "",
        "supervisory": item.get("supervisoryStatus") or "", "telework_eligible": item.get("teleworkEligible") or "",
        "security_clearance": item.get("securityClearance") or "", "who_may_apply": item.get("whoMayApply") or "", "hiring_paths": hiring_paths(item),
        "locations": locations_of(item),
        "opened": as_date(str(item.get("positionOpenDate") or "")), "closes": as_date(str(item.get("positionCloseDate") or "")),
        "status_as_listed": status or "", "listed_at": search.get("retrieved_at", ""),
        "listings": listings or [{"retrieved_at": search.get("retrieved_at", ""), "status": status or "", "sha256": search.get("sha256", "")}],
        "reading": {"status": reading, "posture": POSTURE[reading], "flyer": flyer, "leader_role": leader,
                    "note": ("a selection is stated: the position was filled" if reading == "selected"
                             else "the announcement was withdrawn" if reading == "cancelled"
                             else "an announced vacancy is an intention to hire, not a hire made")},
        "source_type": SOURCE_TYPE, "reliability": RELIABILITY[SOURCE_TYPE],
        "record": {"path": search.get("path", ""), "sha256": search.get("sha256", ""), "method": search.get("method", ""),
                   "retrieved_at": search.get("retrieved_at", ""), "url": search.get("url", "")},
        "announcement": ({"path": page.get("path", ""), "sha256": page.get("sha256", ""), "method": page.get("method", ""),
                          "retrieved_at": page.get("retrieved_at", ""), "url": page.get("url", "")} if page else None),
        "entities": whole,
        "links": {"offices": whole["organizations"], "requirements": whole["requirements"],
                  "solicitations": [s for s in whole["solicitations"] if s in mem["known_sols"]],
                  "awards": [c for c in whole["contracts"] if c in mem["known_contracts"]]},
        "claims": claims, "relation": rollup, "background_sentences": background,
        # What the loader takes: a posting in the acquisition workforce, or one whose own words name an office beyond
        # the command it is filed under, or a requirement, contract or solicitation the record holds. The rest stays
        # in this record and loads nothing.
        "loads": acquisition or bool(set(whole["organizations"]) - {org, P["agency"]["node"]} or whole["requirements"]
                                     or [c for c in whole["contracts"] if c in mem["known_contracts"]] or [s for s in whole["solicitations"] if s in mem["known_sols"]]),
        "verify": sorted({v for c in claims for v in c["verify"]}),
        "modelled_at": now(),
    }


# ---------------------------------------------------------------- commands

def saved_searches(rows: list[dict]) -> list[dict]:
    return [r for r in rows if is_search(r)]


def saved_pages(rows: list[dict]) -> dict[str, dict]:
    """The newest saved announcement page per control number."""
    out: dict[str, dict] = {}
    for r in rows:
        if is_announcement(r):
            control = ANNOUNCEMENT_RE.match(r["url"]).group(1)
            if control not in out or r.get("retrieved_at", "") > out[control].get("retrieved_at", ""):
                out[control] = r
    return out


def build(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="jobs build")
    parser.parse_args(argv)
    if not HIRING["usajobs_agency_codes"]:
        print(f"the {P['key']} profile maps no USAJobs agency code; nothing to model ({HIRING['note']})")
        return 0
    mem = memory()
    rows = manifest_rows()
    searches, pages = saved_searches(rows), saved_pages(rows)
    newest: dict[str, tuple[dict, dict]] = {}
    listings: dict[str, list[dict]] = {}
    recorded = []
    missing = 0
    for row in sorted(searches, key=lambda r: r.get("retrieved_at", "")):
        path = ROOT / row["path"]
        if not path.exists():
            missing += 1
            continue
        items, _ = listing_items(path.read_bytes())
        params = search_params(row["url"])
        recorded.append({"url": row["url"], "agency_code": params["agency_code"], "org": HIRING["usajobs_agency_codes"].get(params["agency_code"], ""),
                         "window": [params["start"], params["end"]], "page_token": params["token"], "retrieved_at": row.get("retrieved_at", ""),
                         "sha256": row.get("sha256", ""), "path": row["path"], "announcements": len(items)})
        for item in items:
            control = str(item["usajobsControlNumber"])
            listings.setdefault(control, []).append({"retrieved_at": row.get("retrieved_at", ""), "status": item.get("positionOpeningStatus") or "",
                                                     "sha256": row.get("sha256", "")})
            newest[control] = (item, row)  # sorted oldest first, so the last listing read wins
    postings = []
    for control, (item, row) in newest.items():
        page = pages.get(control)
        body = (ROOT / page["path"]).read_bytes() if page and (ROOT / page["path"]).exists() else None
        postings.append(posting_record(item, row, page if body else None, body, mem, listings[control]))
    postings.sort(key=lambda p: (p["opened"], p["id"]))
    RECORDS.write_text(json.dumps({"generated": now(), "source": "chromie-federal-buyer-map-trial/research/tools/jobs.py", "provider": PROVIDER,
                                   "agency_codes": HIRING["usajobs_agency_codes"], "searches": recorded, "postings": postings},
                                  indent=1, sort_keys=True) + "\n", encoding="utf-8")
    loaded = [p for p in postings if p["loads"]]
    with_page = [p for p in postings if p["announcement"]]
    claims = [c for p in postings for c in p["claims"]]
    print(f"{len(postings)} announcement(s) modelled from {len(recorded)} saved listing page(s)"
          + (f" ({missing} listing row(s) whose file is not on disk)" if missing else "")
          + f"; {len(with_page)} with the announcement page saved; {len(loaded)} load (acquisition workforce or naming a known office or requirement)")
    print(f"  {len(claims)} claim(s): " + ", ".join(f"{r} {len([c for c in claims if c['relation'] == r])}" for r in ("conflicts", "corroborates", "new signal")))
    for posture in ("intent", "action", "withdrawn"):
        print(f"  {posture}: {len([p for p in postings if p['reading']['posture'] == posture])}")
    print(f"written to {RECORDS.relative_to(ROOT)}")
    return 0


def load_records() -> dict:
    if not RECORDS.exists():
        print(f"{RECORDS.relative_to(ROOT)} not built yet: run `python research/tools/jobs.py build`", file=sys.stderr)
        return {"postings": [], "searches": []}
    return json.loads(RECORDS.read_text(encoding="utf-8"))


def show(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="jobs read")
    parser.add_argument("target", help="a control number, job:<control>, or a word in the title or hiring organization")
    args = parser.parse_args(argv)
    want = args.target.lower()
    hits = [p for p in load_records()["postings"]
            if want in (p["id"], p["control_number"]) or want in p["title"].lower() or want in (p["hiring"]["agency"] + " " + p["hiring"]["subelement"]).lower()]
    if not hits:
        print("no modelled announcement matches", file=sys.stderr)
        return 1
    for p in hits[:5]:
        print(f"\n# {p['title']}")
        print(f"{p['hiring']['agency'] or p['hiring']['department']}" + (f"; {p['hiring']['subelement']}" if p["hiring"]["subelement"] else "")
              + f" (USAJobs code {p['hiring']['agency_code']}" + (f", memory node {p['hiring']['org']}" if p["hiring"]["org"] else "") + ")")
        print(f"announcement {p['announcement_number']}, control {p['control_number']}, {p['url']}")
        print(f"opened {p['opened'] or 'date not stated'}, closes {p['closes'] or 'not stated'}; listed as \"{p['status_as_listed'] or 'not stated'}\" on {p['listed_at'][:10]}: "
              f"{p['reading']['status']} ({p['reading']['posture']}); {p['reading']['note']}")
        print(f"series {', '.join(p['series']) or 'not stated'}" + (f" ({p['career_field']})" if p["career_field"] else "") + f"; {p['grade']['pay_scale']} {p['grade']['minimum']}"
              + (f"-{p['grade']['maximum']}" if p["grade"]["maximum"] != p["grade"]["minimum"] else "") + f"; {p['openings'] or '?'} opening(s); {', '.join(p['locations']) or 'site not stated'}")
        print(f"listing retrieved {p['record']['retrieved_at']}, sha256 {p['record']['sha256'][:12]}, saved at {p['record']['path']}")
        if p["announcement"]:
            print(f"page retrieved {p['announcement']['retrieved_at']}, sha256 {p['announcement']['sha256'][:12]}, saved at {p['announcement']['path']}")
        else:
            print("page not saved: the listing's fields are the whole record")
        print(f"reads {p['relation']} against the stored record; loads: {'yes' if p['loads'] else 'no'}")
        ents = p["entities"]
        named = [(k, v) for k in ("organizations", "programs", "contracts", "solicitations", "requirements", "locations") for v in [ents.get(k)] if v]
        if named:
            print("\n## Named")
            for k, v in named:
                print(f"- {k}: {', '.join(str(x) for x in v)}")
        print("\n## Claims, each on the passage it rests on"
              + (f" ({p['background_sentences']} further sentence(s) name the office without stating anything)" if p["background_sentences"] else ""))
        for claim in p["claims"]:
            print(f"- {claim['statement_type']} [{claim['relation']}, confidence {claim['confidence']}]"
                  f"{' stated ' + claim['stated_on'] if claim['stated_on'] else ''}: {claim['relation_note']}")
            print(f"  \"{claim['passage']}\"")
            for item in claim["verify"]:
                print(f"  to verify: {item}")
    return 0


def saved_urls(rows: list[dict]) -> set[str]:
    return {r.get("url") for r in rows if kept_page(r)}


def wanted(item: dict, mem_names: re.Pattern | None) -> bool:
    """Whose announcement page is worth taking: the acquisition workforce, or a listing whose words name an office the
    memory knows. A child-care or a lodging vacancy is recorded from the listing alone."""
    if career_field(series_of(item)):
        return True
    # The listing's own words are the title and the subelement; the agency name is the code's, printed on every listing.
    text = " ".join(filter(None, [item.get("positionTitle") or "", str(item.get("hiringSubelementName") or "")]))
    return bool(mem_names and mem_names.search(text))


FIELD_ORDER = ("contracting", "program management", "business", "engineering", "logistics", "information technology", "quality", "security")


def take_order(item: dict, mem_names: re.Pattern | None) -> tuple:
    """Which pages to take first when a run cannot take them all: a listing whose own words name an office the memory
    knows, then the fields nearest the buy (contracting, program management), then the newest announcement."""
    text = " ".join(filter(None, [item.get("positionTitle") or "", str(item.get("hiringSubelementName") or "")]))
    named = 0 if mem_names and mem_names.search(text) else 1
    fields = [f for f in FIELD_ORDER if f in career_field(series_of(item))]
    return (named, FIELD_ORDER.index(fields[0]) if fields else len(FIELD_ORDER), str(item.get("positionOpenDate") or ""))


def ordered(candidates: list[tuple[str, dict]], mem_names: re.Pattern | None) -> list[tuple[str, dict]]:
    """The candidates in the order their pages are taken: by rank, and the newest announcement first within a rank."""
    return sorted(candidates, key=lambda pair: (*take_order(pair[1], mem_names)[:2], -_ordinal(pair[1].get("positionOpenDate"))))


def _ordinal(day) -> int:
    try:
        return date.fromisoformat(str(day)[:10]).toordinal()
    except ValueError:
        return 0


def sweep(argv: list[str]) -> int:
    """One listing per agency code the profile maps, page by page, and the announcement pages of what is worth reading.

    The listing states what USAJobs has; the manifest states what was taken; a 204 is recorded as the answer that
    nothing was announced in the window, so a negative names the search it rests on."""
    parser = argparse.ArgumentParser(prog="jobs sweep")
    parser.add_argument("--days", type=int, default=180, help="announcements opened in the last N days")
    parser.add_argument("--fetch", action="store_true", help="also take the announcement pages not yet saved")
    parser.add_argument("--limit", type=int, default=30, help="how many announcement pages to take in this run")
    parser.add_argument("--codes", default="", help="only these agency codes, comma-separated (default: every code the profile maps)")
    parser.add_argument("--pause", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not HIRING["usajobs_agency_codes"]:
        print(f"the {P['key']} profile maps no USAJobs agency code; nothing to sweep ({HIRING['note']})")
        return 0
    from fetch import fetch
    from news import relevance

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or list(HIRING["usajobs_agency_codes"])
    unknown = [c for c in codes if c not in HIRING["usajobs_agency_codes"]]
    if unknown:
        print(f"{', '.join(unknown)}: not a code the {P['key']} profile maps; the mapping is in research/tools/agency.py", file=sys.stderr)
        return 2
    start, end = window(args.days)
    rows = manifest_rows()
    have = saved_urls(rows)
    names = relevance()
    listed, candidates, refused = 0, [], 0
    for code in codes:
        org = HIRING["usajobs_agency_codes"][code]
        url, page = search_url(code, start, end), 1
        while url:
            # The same window listed earlier today is read from the saved answer: a sweep takes only what is new, and
            # a second pass for more pages does not list twice. The window carries today's date, so tomorrow lists again.
            saved = next((r for r in rows if r.get("url") == url and kept_page(r) and (ROOT / r["path"]).exists()
                          and (r.get("retrieved_at") or "")[:10] == end and is_search(r)), None)
            row = saved or fetch(url, "direct", None, f"jobs sweep{NOTE_TAG}: {code} {org} {start}..{end}" + (f" page {page}" if page > 1 else ""))
            if not saved:
                record_row(row)
            if row.get("status") == 204 or (row.get("status") == 200 and not (row.get("size") or 0)):
                print(f"{code} ({org}): no announcement opened between {start} and {end} (the API answered {row.get('status')}, recorded)")
                break
            if not kept_page(row):
                refused += 1
                print(f"{code} ({org}): {row.get('error') or row.get('status')}; the front end refuses a bare client, and an API is not a page "
                      f"the hosted browser can render, so the refusal is recorded and the sweep goes on")
                break
            items, nxt = listing_items((ROOT / row["path"]).read_bytes())
            listed += len(items)
            print(f"{code} ({org}): page {page}, {len(items)} announcement(s) opened between {start} and {end}"
                  + (", more pages follow" if nxt else "") + (" (listed earlier today, read from the saved answer)" if saved else ""))
            candidates += [(code, i) for i in items]
            url, page = nxt, page + 1
            time.sleep(args.pause)
    worth = [(c, i) for c, i in candidates if wanted(i, names)]
    fresh = ordered([(c, i) for c, i in worth if ANNOUNCEMENT.format(control=i["usajobsControlNumber"]) not in have], names)
    print(f"\n{listed} announcement(s) listed; {len(worth)} in the acquisition workforce or naming a known office, {len(fresh)} of them without a saved page")
    taken = 0
    if args.fetch:
        for code, item in fresh[:args.limit]:
            control = item["usajobsControlNumber"]
            page_url = ANNOUNCEMENT.format(control=control)
            row = fetch(page_url, "direct", None, f"jobs announcement{NOTE_TAG}: {control} {(item.get('positionTitle') or '')[:60]} {code}")
            record_row(row)
            taken += row.get("status") == 200
            print(f"  {row.get('status')} {page_url} {(item.get('positionTitle') or '')[:50]}")
            time.sleep(args.pause)
        print(f"{taken} announcement page(s) taken" + (f"; {len(fresh) - args.limit} more wait for the next run" if len(fresh) > args.limit else "")
              + "; now `jobs.py build`")
    else:
        print("rerun with --fetch to take the announcement pages, then `jobs.py build`")
    return 1 if refused else 0


# ---------------------------------------------------------------- selfcheck

SAMPLE_ITEM = {"usajobsControlNumber": 880882900, "hiringAgencyCode": "NV39", "hiringAgencyName": "Commander, Naval Information Warfare Systems Command (NAVWARSYSCOM)",
               "hiringDepartmentCode": "NV", "hiringDepartmentName": "Department of the Navy", "hiringSubelementName": None,
               "positionTitle": "ADMINISTRATIVE SPECIALIST (CONTRACT SPECIALIST PAE Robotic and Autonomous Systems)", "announcementNumber": "DE-13035134-26-JCP",
               "positionOpenDate": "2026-08-18", "positionCloseDate": "2026-08-25", "positionOpeningStatus": "Applications under review",
               "payScale": "DP", "minimumGrade": "3", "maximumGrade": "3", "promotionPotential": "3", "minimumSalary": 102246.0, "maximumSalary": 158062.0,
               "salaryType": "Per Year", "totalOpenings": "1", "appointmentType": "Permanent", "workSchedule": "Full-time", "supervisoryStatus": "N",
               "teleworkEligible": "Y", "securityClearance": "Secret", "jobcategories": [{"series": "1102"}],
               "positionlocations": [{"positionLocationCity": "San Diego", "positionLocationState": "California"}, {"positionLocationCity": "Washington", "positionLocationState": "District of Columbia"}],
               "hiringpaths": [{"hiringPath": "The public"}]}
SAMPLE_PAGE = b"""<html><head><title>USAJOBS - Job Announcement</title></head><body><nav>menu</nav>
<h1>ADMINISTRATIVE SPECIALIST (CONTRACT SPECIALIST PAE Robotic and Autonomous Systems)</h1>
<h2>Summary</h2><p>This is a public notice flyer to notify interested applicants of anticipated vacancies. There may or may not be actual vacancies filled from this flyer.</p>
<h2>Duties</h2><ul>
<li>You will provide expert contracting and business advice to support the Navy's Robotic and Autonomous Systems (RAS) initiatives within the Naval Information Warfare Systems Command (NAVWAR).</li>
<li>You will administer contracts for RAS programs, including modifications to N0003916C0087, funding actions, and scope changes.</li>
<li>You will serve as the contracting officer for the PMW 160 Tactical Networks Program Office, which reports to the PEO C4I organization.</li>
</ul>
<h2>Requirements</h2><h3>Conditions of employment</h3><p>Generally, current Federal employees applying to APS jobs must serve at least one year at the next lower grade level in the PMW 160 office.</p>
<h3>Qualifications</h3><p>Experience with the CANES program is desired.</p>
<h2>How you will be evaluated</h2><p>You will be evaluated for this job based on how well you meet the qualifications above.</p>
<h2>How to Apply</h2><p>Click Apply Online.</p></body></html>"""


def selfcheck() -> int:
    assert search_url("NV39", "2026-03-31", "2026-09-27") == API + "?HiringAgencyCodes=NV39&StartPositionOpenDate=2026-03-31&EndPositionOpenDate=2026-09-27"
    assert search_params(search_url("NV39", "2026-03-31", "2026-09-27", "abc")) == {"agency_code": "NV39", "start": "2026-03-31", "end": "2026-09-27", "token": "abc"}
    assert search_params("https://data.usajobs.gov/api/historicjoa?continuationtoken=x&hiringagencycodes=NV&startpositionopendate=2026-09-15&endpositionopendate=2026-09-27") \
        == {"agency_code": "NV", "start": "2026-09-15", "end": "2026-09-27", "token": "x"}, "the API writes its next-page address in lower case"
    assert window(180, date(2026, 9, 27)) == ("2026-03-31", "2026-09-27")
    assert listing_items(b"") == ([], ""), "a 204 is the answer that nothing was announced"
    items, nxt = listing_items(json.dumps({"data": [SAMPLE_ITEM, {"no": "control"}], "paging": {"next": "/api/historicjoa?continuationtoken=t&hiringagencycodes=NV39"}}).encode())
    assert len(items) == 1 and nxt == "https://data.usajobs.gov/api/historicjoa?continuationtoken=t&hiringagencycodes=NV39", (len(items), nxt)
    assert series_of(SAMPLE_ITEM) == ["1102"] and career_field(["1102", "0343"]) == "contracting, program management" and career_field(["1702"]) == ""
    assert locations_of(SAMPLE_ITEM) == ["San Diego, California", "Washington, District of Columbia"]
    assert ANNOUNCEMENT_RE.match("https://www.usajobs.gov/job/880882900").group(1) == "880882900" and not ANNOUNCEMENT_RE.match("https://www.usajobs.gov/Search/")
    assert is_search({"path": "p", "status": 200, "note": "jobs sweep" + NOTE_TAG + ": NV39 x", "url": API + "?HiringAgencyCodes=NV39"})
    assert not is_search({"path": "p", "status": 200, "note": "news sweep: x", "url": API}), "another tool's row is not a listing"
    assert not is_search({"path": "p", "status": 204, "note": "jobs sweep" + NOTE_TAG + ": NV39 x", "url": API}), "a 204 carries no announcement to model"
    assert is_announcement({"path": "p", "status": 200, "note": "jobs announcement" + NOTE_TAG + ": 1 x", "url": "https://www.usajobs.gov/job/1"})
    assert STATUS_READING["Candidate selected"] == "selected" and POSTURE["selected"] == "action" and POSTURE["announced"] == "intent"
    text = page_text(SAMPLE_PAGE)
    assert "menu" not in text and "Click Apply Online" not in text and "You will administer contracts" in text, "the page's own words, up to the application boilerplate"
    assert "APS jobs" not in text and "CANES program" in text, "the conditions of employment are boilerplate; the qualifications are the office's words"
    assert statement_type_of("This position is located in the PMW 160 office.", {"organizations": ["pmw:160"], "programs": [], "contracts": [], "solicitations": []}, None) == "placement"
    assert statement_type_of("You will support the CANES program.", {"organizations": [], "programs": ["canes"], "contracts": [], "solicitations": []}, None) == "workload"
    assert statement_type_of("The office was newly established in 2026.", {"organizations": ["a"], "programs": [], "contracts": [], "solicitations": []}, None) == "stand_up"
    assert statement_type_of("PMW 160 is in San Diego.", {"organizations": ["pmw:160"], "programs": [], "contracts": [], "solicitations": []}, None) == "existence"
    level, verify = confidence_of("You will support the program.", "workload", False)
    assert level == "high" and verify == ["a vacancy announced is an intention to hire, not a position filled"]
    assert confidence_of("You will support the program.", "workload", True)[0] == "medium", "a flyer of anticipated vacancies states less"
    assert confidence_of("The office expects to award the follow-on.", "workload", True)[0] == "low"
    assert wanted(SAMPLE_ITEM, None) and not wanted({**SAMPLE_ITEM, "jobcategories": [{"series": "1702"}], "positionTitle": "CHILD AND YOUTH PROGRAM ASSISTANT", "hiringAgencyName": ""}, None)
    assert not wanted({**SAMPLE_ITEM, "jobcategories": [{"series": "1702"}], "positionTitle": "CHILD AND YOUTH PROGRAM ASSISTANT"}, re.compile("Naval Information Warfare")), \
        "the agency name is on every listing; it does not make a page worth taking"
    assert wanted({**SAMPLE_ITEM, "jobcategories": [{"series": "1702"}], "positionTitle": "CHILD AND YOUTH PROGRAM ASSISTANT", "hiringSubelementName": "NIWC Pacific"}, re.compile("NIWC Pacific"))
    it_job = {**SAMPLE_ITEM, "usajobsControlNumber": 1, "jobcategories": [{"series": "2210"}], "positionOpenDate": "2026-09-01"}
    older = {**SAMPLE_ITEM, "usajobsControlNumber": 2, "positionOpenDate": "2026-05-01"}
    named = {**SAMPLE_ITEM, "usajobsControlNumber": 3, "jobcategories": [{"series": "2210"}], "hiringSubelementName": "PMW 160 Tactical Networks", "positionOpenDate": "2026-01-01"}
    order = [i["usajobsControlNumber"] for _, i in ordered([("NV39", it_job), ("NV39", older), ("NV39", SAMPLE_ITEM), ("NV39", named)], re.compile("PMW 160"))]
    assert order == [3, 880882900, 2, 1], order  # a known office first, then contracting before IT, the newest first within a rank

    # End to end on the saved memory: the listing corroborates the command the profile maps its code to, the duties
    # place the position and name a known contract, every claim quotes the page, and the reading is intent.
    mem = memory()
    search = {"path": "", "sha256": "b" * 64, "method": "direct", "retrieved_at": now(), "url": search_url("NV39", "2026-03-31", "2026-09-27")}
    page = {"path": "", "sha256": "c" * 64, "method": "direct", "retrieved_at": now(), "url": "https://www.usajobs.gov/job/880882900"}
    record = posting_record(SAMPLE_ITEM, search, page, SAMPLE_PAGE, mem)
    assert record["id"] == "job:880882900" and record["url"] == "https://www.usajobs.gov/job/880882900" and record["opened"] == "2026-08-18"
    assert record["hiring"]["org"] == "command:navwar" and record["claims"][0]["statement_type"] == "vacancy"
    assert record["claims"][0]["relation"] == "corroborates" and record["claims"][0]["passage"] == SAMPLE_ITEM["positionTitle"]
    assert record["claims"][0]["fields"]["announcementNumber"] == "DE-13035134-26-JCP"
    assert record["reading"] == {"status": "under review", "posture": "intent", "flyer": True, "leader_role": False,
                                 "note": "an announced vacancy is an intention to hire, not a hire made"}, record["reading"]
    kinds = {c["statement_type"] for c in record["claims"]}
    assert "placement" in kinds and "workload" in kinds, kinds
    assert not any(c["relation"] == "conflicts" and "contracting:n00039" in c["relation_note"] for c in record["claims"]), \
        "a command's contracting office is not a second place in the org chart"
    assert "N0003916C0087" in record["entities"]["contracts"] and "pmw:160" in record["entities"]["organizations"], record["entities"]
    assert not {"administrative", "specialist", "contract"} & set(record["entities"]["programs"]), "a capitalised title is not a list of program codes"
    flat = " ".join(SAMPLE_PAGE.decode().replace("\n", " ").split()).replace("<li>", " ").replace("</li>", " ")
    assert all(c["passage"] in " ".join(re.sub(r"<[^>]+>", " ", flat).split()) for c in record["claims"][1:]), "a claim quotes the announcement, it does not paraphrase it"
    assert all(c["confidence"] in ("low", "medium") for c in record["claims"]), "a flyer of anticipated vacancies never reads high"
    assert record["relation"] in ("new signal", "corroborates", "conflicts") and record["loads"] and record["acquisition_workforce"]
    assert "a vacancy announced is an intention to hire, not a position filled" in record["verify"]
    assert not any(k in json.dumps(record).lower() for k in ("decision-maker", "decision maker")), "a posting is never read as authority"
    # Without a page, the listing's fields are the whole record, and a selection is an action stated.
    bare = posting_record({**SAMPLE_ITEM, "positionOpeningStatus": "Candidate selected"}, search, None, None, mem)
    assert bare["announcement"] is None and len(bare["claims"]) == 1 and bare["reading"]["posture"] == "action" and bare["reading"]["flyer"] is False
    assert bare["entities"]["organizations"] == [] and bare["claims"][0]["subjects"] == ["command:navwar"], \
        "the agency name is the code's word, not the listing's: the command comes from the profile's mapping, not from reading the name"
    assert not bare["loads"] or bare["acquisition_workforce"], "a listing alone loads only for the acquisition workforce"
    # A leader's vacancy for an office the memory has a leader for asks a question; it never says who leads.
    led = next((o for o, people in mem["leads"].items() if people), None)
    if led:
        name = next(n["name"] for n in mem["seed"]["nodes"] if n["id"] == led)
        titled = posting_record({**SAMPLE_ITEM, "positionTitle": f"PROGRAM MANAGER, {name}"}, search, None, None, mem)
        assert titled["reading"]["leader_role"] and any("never who leads" in v for v in titled["claims"][0]["verify"]), titled["claims"][0]["verify"]
    print("jobs selfcheck ok")
    return 0


COMMANDS = {"build": build, "read": show, "sweep": sweep}


def main(argv: list[str]) -> int:
    if "--selfcheck" in argv:
        return selfcheck()
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

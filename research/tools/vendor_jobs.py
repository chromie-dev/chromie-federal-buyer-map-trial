#!/usr/bin/env python3
"""Read a contractor's job posting as a dated observation about the work it names, beside the agency's own postings.

    python research/tools/vendor_jobs.py sweep [--fetch] [--company UEI|NAME] [--limit 10] [--results 5] [--take 2]
                                              [--days 90] [--max-cost 0.02] [--budget 0.50] [--dry-run]
    python research/tools/vendor_jobs.py build                  # model every saved careers page
    python research/tools/vendor_jobs.py read vendorjob:<id>|WORD  # one posting's record
    python research/tools/vendor_jobs.py --selfcheck

USAJobs (jobs.py) sees the government hiring. This tool sees the other side: an incumbent or a challenger posting a
role that names an office, a program, a contract or a solicitation the record holds ("contract specialist supporting
PMW 160"). Discovery goes through RouterGrowth's `company.jobs` capability (research/tools/routergrowth.py): one query
per company on the watch list, which is the incumbents with live awards at the contracting offices the profile sweeps
(research/memory/vendors.json), plus any company the profile names in `hiring.vendor_watch`. The router's answer is
saved as the search a negative rests on and nothing more: the evidence is the company's own careers page, or its
applicant system's page carrying the company's name, fetched first-hand, saved with its hash, and quoted. A page on an
aggregator or a social network (Indeed, LinkedIn, Glassdoor) is a pointer in the record and is never read.

A vendor posting is a company's intention to staff, never an award, a requirement or a place in the org chart. It
reads at most medium (news.RELIABILITY, "company statement") and loads only when its own words name an office,
requirement, contract or solicitation the memory holds; a title alone loads nothing
(research/docs/21_job_postings_as_a_signal.md, "Vendor postings").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
import routergrowth  # noqa: E402
from agency import EVENTS as EVENTS_DIR, MEMORY, NOTE_TAG, P, RAW, note_is_ours  # noqa: E402
from jobs import NOT_PROGRAMS, STATEMENT_PATTERNS, statement_type_of  # noqa: E402
from markdown import html_to_markdown  # noqa: E402
from news import HEDGE_RE, RELIABILITY, as_date, entities_in, host_of, is_prose, manifest_rows, memory, relate, retrieve, sentences  # noqa: E402

HIRING = P["hiring"]
CAPABILITY = os.environ.get("VENDOR_JOBS_CAPABILITY", "company.jobs")
PROVIDER = "vendor_jobs_routergrowth"  # the registry row every posting is filed under
RECORDS = EVENTS_DIR / "vendor_hiring_observations.json"
VENDORS = MEMORY / "vendors.json"
RAW_DIR = RAW / "vendor_jobs"
NOTE = "vendor jobs"  # ledger notes: "vendor jobs inspect: ...", "vendor jobs search: ...", and "vendor job: ..." for a page
SOURCE_TYPE = "company statement"
VERIFY = "a vendor vacancy is a company's intention to staff, not an award or a requirement"

# An applicant system serves a company's postings under its own host; the company's name in the host or the path says
# whose. Anything else that is not the company's own domain is a pointer, never a page read.
ATS_HOSTS = ("myworkdayjobs.com", "workdayjobs.com", "greenhouse.io", "lever.co", "icims.com", "taleo.net", "successfactors.com",
             "smartrecruiters.com", "jobvite.com", "ashbyhq.com", "workable.com", "bamboohr.com", "ultipro.com", "oraclecloud.com",
             "avature.net", "eightfold.ai", "phenompeople.com", "recruitee.com", "applytojob.com", "csod.com", "brassring.com")
AGGREGATOR_HOSTS = ("indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com", "google.com", "simplyhired.com", "monster.com",
                    "dice.com", "clearancejobs.com", "careerbuilder.com", "jooble.org", "talent.com", "lensa.com", "adzuna.com",
                    "hiring.cafe", "jobsdb.com", "x.com", "twitter.com", "facebook.com", "reddit.com", "youtube.com", "usajobs.gov")
# The words a legal name carries that say nothing about which company it is.
DROP_WORDS = frozenset({"inc", "llc", "corporation", "corp", "co", "company", "the", "technologies", "technology", "ltd", "limited", "lp",
                        "group", "holdings", "international", "incorporated", "solutions", "services", "systems", "and", "of"})
# What the memory can be asked about each kind of sentence. A sentence that places a contractor's seat in an office
# says the company supports that office, so it is read as `support` and checked for the office's existence, never as
# a parent in the org chart.
KIND_OF = {"placement": "support", "stand_up": "stand_up", "workload": "workload", "existence": "existence"}
RELATE_AS = {"support": "existence", "stand_up": "consolidation", "workload": "performance", "existence": "existence"}
DATE_POSTED_RE = re.compile(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})')  # the JobPosting structured data an ATS page embeds
TITLE_RE = re.compile(r"^#\s+(.+)$", re.M)
RUN_URL = f"{routergrowth.BASE}/v1/run"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- companies

def vendors() -> list[dict]:
    if not VENDORS.exists():
        return []
    return json.loads(VENDORS.read_text(encoding="utf-8")).get("vendors") or []


def company_tokens(vendor: dict) -> list[str]:
    """The tokens a company's own host or applicant-system path carries: its legal names with the empty words dropped,
    joined ("l3harris", "boozallenhamilton"), and the first word or two ("booz", "boozallen") when four letters or more."""
    tokens: set[str] = set()
    for name in [vendor.get("name") or "", *(vendor.get("spellings") or [])]:
        words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in DROP_WORDS]
        if not words:
            continue
        tokens.add("".join(words))
        if len(words[0]) >= 4:
            tokens.add(words[0])
        if len(words) >= 2 and len(words[0] + words[1]) >= 4:
            tokens.add(words[0] + words[1])
    return sorted((t for t in tokens if len(t) >= 4), key=len, reverse=True)


def display_name(name: str) -> str:
    """The name as a search reads it: the legal suffix dropped, capitalised ("L3HARRIS TECHNOLOGIES, INC." reads
    "L3Harris Technologies")."""
    words = [w for w in re.findall(r"[A-Za-z0-9&.-]+", name) if w.strip(".,").lower() not in {"inc", "llc", "corporation", "corp", "ltd", "lp", "incorporated"}]
    return " ".join(w.title() if w.isupper() else w for w in words).strip(" ,") or name


def host_in(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def registrable(host: str) -> str:
    """The label a company owns in its host: careers.l3harris.com reads l3harris; jobs.example.co.uk reads example."""
    parts = host.lower().split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"}:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else host


def host_allowed(url: str, vendor: dict) -> str | None:
    """Whose page an address is: `company` for the company's own host (one the profile names for it, or one whose
    label is the company's name), `ats` for an applicant system's host carrying the company's name in the host or the
    path, else None: an aggregator, a social network, or a host nothing ties to the company."""
    host = host_of(url)
    if not host or host_in(host, AGGREGATOR_HOSTS):
        return None
    if any(host == d or host.endswith("." + d) for d in (vendor.get("hosts") or [])):
        return "company"
    tokens = company_tokens(vendor)
    label = registrable(host)
    if any(label == t or label.startswith(t) for t in tokens):
        return "company"
    if host_in(host, ATS_HOSTS):
        path = urllib.parse.urlparse(url).path.lower()
        if any(t in host or t in path for t in tokens):
            return "ats"
    return None


def watch_list(limit: int) -> list[dict]:
    """The companies to ask: those the profile names, then the incumbents with live awards at the contracting offices
    the profile sweeps, most live awards first, up to the limit."""
    out = []
    for w in HIRING.get("vendor_watch") or []:
        out.append({"uei": w.get("uei") or "", "name": w["name"], "spellings": w.get("spellings") or [w["name"]], "hosts": w.get("hosts") or [],
                    "parent_uei": w.get("parent_uei") or "", "parent_name": w.get("parent_name") or "", "live": None, "awards": None, "offices": {},
                    "basis": "named in the profile's hiring.vendor_watch"})
    swept = set(P["fpds_offices"])
    rows = [v for v in vendors() if (v.get("live") or 0) > 0 and set(v.get("offices") or {}) & swept]
    rows.sort(key=lambda v: (-(v.get("live") or 0), -(v.get("awards") or 0), v.get("name") or ""))
    named = {w["uei"] for w in out if w["uei"]}
    for v in rows[:limit]:
        if v["uei"] in named:
            continue
        at = sorted(set(v["offices"]) & swept)
        out.append({"uei": v["uei"], "name": v["name"], "spellings": v.get("spellings") or [v["name"]], "hosts": [], "parent_uei": v.get("parent_uei") or "",
                    "parent_name": v.get("parent_name") or "", "live": v.get("live"), "awards": v.get("awards"), "offices": {k: v["offices"][k] for k in at},
                    "basis": f"{v.get('live')} live award(s) at {', '.join(P['fpds_offices'][c] for c in at)} (vendors.json as of {json.loads(VENDORS.read_text())['as_of']})"})
    return out


def office_words(vendor: dict) -> list[str]:
    """The offices a company holds awards at, as words a job title or description would carry (NAVWAR, not NAVWAR HQ)."""
    labels = [P["fpds_offices"][c] for c in sorted(vendor.get("offices") or {}) if c in P["fpds_offices"]][:3]
    return [re.sub(r"\s+HQ$", "", label).strip() for label in labels]


def query_for(vendor: dict, fields: dict | None = None) -> str:
    """One query per company. Where the capability takes the company in a field of its own (company.jobs does), no
    keywords are sent: company.jobs matches them against job titles, so an office name finds nothing (L3Harris with
    "NAVWAR": no match on 2026-09-27; with "contracts": five postings), and the company's newest postings come back for
    the build to read for the offices their own text names. Else the company's name leads, with its offices."""
    if fields is not None and any(name in fields for name in ("company", "company_name", "employer")):
        return ""
    return " ".join([display_name(vendor["name"]), "jobs", *office_words(vendor)]).strip()


def posted_within(days: int, spec: dict) -> str | None:
    """The router's window word for a number of days, from the schema's own enum (24h, week, month) where it has one."""
    choices = spec.get("enum") if isinstance(spec, dict) else None
    want = "24h" if days <= 1 else "week" if days <= 7 else "month"
    if choices and want not in choices:
        return choices[-1] if choices else None
    return want


def vendor_of(uei: str, name: str, watch: list[dict]) -> dict:
    """The watch-list entry a saved search names, else the vendors.json row, else the name alone."""
    for w in watch:
        if uei and w["uei"] == uei:
            return w
    for v in vendors():
        if uei and v["uei"] == uei:
            return {"uei": v["uei"], "name": v["name"], "spellings": v.get("spellings") or [v["name"]], "hosts": [], "parent_uei": v.get("parent_uei") or "",
                    "parent_name": v.get("parent_name") or "", "live": v.get("live"), "awards": v.get("awards"), "offices": v.get("offices") or {}, "basis": "vendors.json"}
    return {"uei": uei, "name": name, "spellings": [name], "hosts": [], "parent_uei": "", "parent_name": "", "live": None, "awards": None, "offices": {},
            "basis": "the saved search's note alone"}


# ---------------------------------------------------------------- the ledger

def search_note(vendor: dict, query: str, domain: str = "") -> str:
    """The ledger note of a search: who was asked, with what, and by which domain when one was given, so a search with
    a different input is a different search and is not read from another's saved answer."""
    return f"{NOTE} search{NOTE_TAG}: {vendor['uei'] or '-'} {display_name(vendor['name'])} | {query}" + (f" | domain {domain}" if domain else "")


def search_params(note: str) -> dict:
    """The company, the query and the domain a saved search's note carries."""
    head, _, tail = note.partition(": ")
    parts = tail.split(" | ")
    who, query = parts[0], parts[1] if len(parts) > 1 else ""
    domain = next((p.split(" ", 1)[1] for p in parts[2:] if p.startswith("domain ")), "")
    uei, _, name = who.partition(" ")
    return {"uei": "" if uei == "-" else uei, "name": name, "query": query, "domain": domain}


def is_inspect(row: dict) -> bool:
    return kept_page(row) and note_is_ours(row.get("note") or "") \
        and bool(re.match(rf"^{NOTE} inspect\b", row.get("note") or ""))


def is_search(row: dict) -> bool:
    """A saved run answer taken by this layer's sweep."""
    return kept_page(row) and note_is_ours(row.get("note") or "") \
        and bool(re.match(rf"^{NOTE} search\b", row.get("note") or "")) and row.get("url") == RUN_URL


def is_page(row: dict) -> bool:
    """A saved careers page taken by this layer's sweep, or by hand for it (`vendor job:`, not `vendor jobs search:`)."""
    return kept_page(row) and note_is_ours(row.get("note") or "") \
        and bool(re.match(r"^vendor job\b", row.get("note") or "")) and (row.get("url") or "").startswith("http")


def saved_pages(rows: list[dict]) -> dict[str, dict]:
    """The newest saved page per address."""
    out: dict[str, dict] = {}
    for r in rows:
        if is_page(r) and (r.get("retrieved_at", "") > out.get(r["url"], {}).get("retrieved_at", "")):
            out[r["url"]] = r
    return out


# ---------------------------------------------------------------- the page

def page_text(body: bytes) -> str:
    """The page's own words as Markdown, without the first-level headings (the page's furniture)."""
    text = html_to_markdown(body.decode("utf-8", "replace"))
    return "\n".join(line for line in text.split("\n") if not re.match(r"^#\s", line))


def page_title(body: bytes) -> str:
    text = html_to_markdown(body.decode("utf-8", "replace"))
    m = TITLE_RE.search(text)
    if m:
        return m.group(1).strip()
    m = re.search(r"<title[^>]*>(.*?)</title>", body.decode("utf-8", "replace"), re.I | re.S)
    return " ".join(m.group(1).split()) if m else ""


def posted_on_page(body: bytes) -> str:
    m = DATE_POSTED_RE.search(body.decode("utf-8", "replace"))
    return m.group(1) if m else ""


def named_in(text: str, mem: dict) -> dict:
    ents = entities_in(text, mem)
    ents["programs"] = [t for t in ents["programs"] if t not in NOT_PROGRAMS]
    if not ents["programs"]:
        ents["requirements"] = [r for r in ents["requirements"] if r in text]
    return ents


def confidence_of(passage: str, kind: str) -> tuple[str, list[str]]:
    """A company's page starts medium; a hedge lowers it. What a reviewer still has to confirm travels with it."""
    order = ["low", "medium", "high"]
    level = order.index(RELIABILITY[SOURCE_TYPE])
    verify = [VERIFY]
    if HEDGE_RE.search(passage):
        level -= 1
        verify.append("the passage states an intention, not an action taken")
    if kind in ("support", "workload"):
        verify.append("the office's own notice or award states the requirement; the company's page states the work it wants to staff")
    if kind == "stand_up":
        verify.append("the office's own page or a release stating the new organization")
    return order[max(0, min(level, 2))], verify


def posting_record(result: dict, vendor: dict, search: dict, page: dict, body: bytes, mem: dict) -> dict:
    """One saved careers page as a dated observation: the company, the router's pointer, the page's claims, the links."""
    url = page["url"]
    text = page_text(body)
    title = page_title(body) or result.get("title") or ""
    posted = posted_on_page(body)
    posted_basis = "page" if posted else ("router" if as_date(str(result.get("publishedDate") or "")) else None)
    posted = posted or as_date(str(result.get("publishedDate") or ""))
    whole = named_in(f"{title}\n{text}", mem)
    claims, background, seen = [], 0, set()
    live = vendor.get("live")
    if live:
        relation, why = "corroborates", (f"the record holds {vendor['name']} (UEI {vendor['uei']}) with {live} live award(s) at "
                                         f"{', '.join(P['fpds_offices'].get(c, c) for c in sorted(vendor.get('offices') or {}))}; the posting is the incumbent staffing")
    else:
        relation, why = "new signal", f"the record holds no live award to {vendor['name']}" + (f" (UEI {vendor['uei']})" if vendor.get("uei") else "") + " at the swept offices"
    level, verify = confidence_of("", "vacancy")
    claims.append({"statement_type": "vacancy", "passage": title, "subjects": [], "people": [], "programs": [], "contracts": [], "solicitations": [],
                   "relation": relation, "relation_note": why, "confidence": level, "verify": verify, "stated_on": posted})
    for passage in sentences(text):
        if passage in seen or not is_prose(passage):
            continue
        seen.add(passage)
        ents = named_in(passage, mem)
        if not (ents["organizations"] or ents["programs"] or ents["contracts"] or ents["solicitations"] or ents["requirements"]):
            continue
        kind = KIND_OF[statement_type_of(passage, ents, mem)] if statement_type_of(passage, ents, mem) != "listing" else "existence"
        known = (ents["requirements"] or [c for c in ents["contracts"] if c in mem["known_contracts"]] or [s for s in ents["solicitations"] if s in mem["known_sols"]])
        if kind == "existence" and not known:
            background += 1  # a sentence that only names the office is background, not a claim
            continue
        rel, note = relate(RELATE_AS[kind], ents, mem)
        level, verify = confidence_of(passage, kind)
        claims.append({"statement_type": kind, "passage": passage[:600], "subjects": ents["organizations"], "people": ents["people_as_written"],
                       "programs": ents["programs"], "contracts": ents["contracts"], "solicitations": ents["solicitations"],
                       "relation": rel, "relation_note": note, "confidence": level, "verify": verify, "stated_on": as_date(passage)})
    relations = {c["relation"] for c in claims}
    rollup = "conflicts" if "conflicts" in relations else ("corroborates" if "corroborates" in relations else "new signal")
    offices = whole["organizations"]
    awards = [c for c in whole["contracts"] if c in mem["known_contracts"]]
    sols = [s for s in whole["solicitations"] if s in mem["known_sols"]]
    return {
        "id": f"vendorjob:{hashlib.sha256(url.encode()).hexdigest()[:12]}", "url": url, "title": title,
        "company": {"name": vendor["name"], "display": display_name(vendor["name"]), "uei": vendor.get("uei") or "", "parent_uei": vendor.get("parent_uei") or "",
                    "parent_name": vendor.get("parent_name") or "", "live": live, "awards": vendor.get("awards"), "offices": vendor.get("offices") or {},
                    "basis": vendor.get("basis") or ""},
        "location": result.get("location") or "", "posted": {"date": posted, "basis": posted_basis},
        "source_type": SOURCE_TYPE, "reliability": RELIABILITY[SOURCE_TYPE],
        "search": {"url": search.get("url", ""), "sha256": search.get("sha256", ""), "path": search.get("path", ""), "retrieved_at": search.get("retrieved_at", ""),
                   "query": search_params(search.get("note") or "")["query"], "capability": CAPABILITY, "router_title": result.get("title") or "",
                   "router_company": result.get("company") or "", "router_date": result.get("publishedDate") or ""},
        "page": {"path": page.get("path", ""), "sha256": page.get("sha256", ""), "method": page.get("method", ""), "retrieved_at": page.get("retrieved_at", ""),
                 "url": url, "host": host_of(url), "host_class": host_allowed(url, vendor)},
        "entities": whole,
        "links": {"offices": offices, "requirements": whole["requirements"], "solicitations": sols, "awards": awards},
        "claims": claims, "relation": rollup, "background_sentences": background,
        # What the loader takes: a posting whose own words name an office beyond the agency, or a requirement, contract
        # or solicitation the record holds. A title alone loads nothing: the source is a company's, not the buyer's.
        "loads": bool(set(offices) - {P["agency"]["node"]} or whole["requirements"] or awards or sols),
        "verify": sorted({v for c in claims for v in c["verify"]}),
        "modelled_at": now(),
    }


# ---------------------------------------------------------------- commands

def build(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vendor_jobs build")
    parser.parse_args(argv)
    if not HIRING.get("vendor_jobs", True):
        print(f"the {P['key']} profile opts out of vendor postings; nothing to model")
        return 0
    mem = memory()
    rows = manifest_rows()
    watch = watch_list(10)  # the record shows the default sweep's list; a company searched beyond it is read from vendors.json
    pages = saved_pages(rows)
    searches, postings, pointers_total = [], {}, 0
    for row in sorted((r for r in rows if is_search(r)), key=lambda r: r.get("retrieved_at", "")):
        path = ROOT / row["path"]
        if not path.exists():
            continue
        answer = json.loads(path.read_bytes())
        params = search_params(row.get("note") or "")
        vendor = vendor_of(params["uei"], params["name"], watch)
        results = routergrowth.results(answer)
        pointers, fetchable = [], []
        for result in results:
            cls = host_allowed(result["url"], vendor)
            if cls is None:
                pointers.append({"url": result["url"], "title": result.get("title") or "", "host": host_of(result["url"]),
                                 "why": ("an aggregator or a social network; not read" if host_in(host_of(result["url"]), AGGREGATOR_HOSTS)
                                         else "not the company's own page or its applicant system; not read")})
                continue
            fetchable.append(result["url"])
            page = pages.get(result["url"])
            if page and (ROOT / page["path"]).exists():
                postings[result["url"]] = posting_record(result, vendor, row, page, (ROOT / page["path"]).read_bytes(), mem)  # the newest search wins
        pointers_total += len(pointers)
        searches.append({"url": row["url"], "uei": vendor.get("uei") or "", "company": vendor["name"], "query": params["query"], "capability": CAPABILITY,
                         "retrieved_at": row.get("retrieved_at", ""), "sha256": row.get("sha256", ""), "path": row["path"],
                         "results": len(results), "fetchable": len(fetchable), "pages_saved": len([u for u in fetchable if u in pages]),
                         "pointers": pointers, "cost": routergrowth.cost_of(answer)})
    out = sorted(postings.values(), key=lambda p: (p["posted"]["date"] or "", p["id"]))
    RECORDS.write_text(json.dumps({"generated": now(), "source": "chromie-federal-buyer-map-trial/research/tools/vendor_jobs.py", "provider": PROVIDER,
                                   "capability": CAPABILITY, "watch": [{k: w[k] for k in ("uei", "name", "live", "awards", "basis")} for w in watch],
                                   "searches": searches, "postings": out}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    loaded = [p for p in out if p["loads"]]
    claims = [c for p in out for c in p["claims"]]
    print(f"{len(out)} posting(s) modelled from {len(searches)} saved search(es); {pointers_total} pointer(s) not read (aggregators and unrelated hosts); "
          f"{len(loaded)} load (naming an office, requirement, contract or solicitation the record holds)")
    print(f"  {len(claims)} claim(s): " + ", ".join(f"{r} {len([c for c in claims if c['relation'] == r])}" for r in ("conflicts", "corroborates", "new signal")))
    print(f"written to {RECORDS.relative_to(ROOT)}")
    return 0


def load_records() -> dict:
    if not RECORDS.exists():
        print(f"{RECORDS.relative_to(ROOT)} not built yet: run `python research/tools/vendor_jobs.py build`", file=sys.stderr)
        return {"postings": [], "searches": []}
    return json.loads(RECORDS.read_text(encoding="utf-8"))


def show(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vendor_jobs read")
    parser.add_argument("target", help="vendorjob:<id>, or a word in the title or the company")
    args = parser.parse_args(argv)
    want = args.target.lower()
    hits = [p for p in load_records()["postings"] if want == p["id"] or want in p["title"].lower() or want in p["company"]["name"].lower()]
    if not hits:
        print("no modelled posting matches", file=sys.stderr)
        return 1
    for p in hits[:5]:
        print(f"\n# {p['title']}")
        print(f"{p['company']['display']} (UEI {p['company']['uei'] or 'not stated'}"
              + (f", {p['company']['live']} live award(s)" if p["company"]["live"] else "") + f"); {p['company']['basis']}")
        print(f"{p['url']} ({p['page']['host_class']} page on {p['page']['host']})")
        print(f"posted {p['posted']['date'] or 'date not stated'}" + (f" (from the {p['posted']['basis']})" if p["posted"]["basis"] else "")
              + (f"; {p['location']}" if p["location"] else ""))
        print(f"found by the search \"{p['search']['query']}\" ({p['search']['capability']}) retrieved {p['search']['retrieved_at']}, sha256 {p['search']['sha256'][:12]}")
        print(f"page retrieved {p['page']['retrieved_at']}, sha256 {p['page']['sha256'][:12]}, saved at {p['page']['path']}")
        print(f"reads {p['relation']} against the stored record; loads: {'yes' if p['loads'] else 'no'}; {p['source_type']}, reliability {p['reliability']}")
        ents = p["entities"]
        named = [(k, v) for k in ("organizations", "programs", "contracts", "solicitations", "requirements") for v in [ents.get(k)] if v]
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


def saved_search_today(rows: list[dict], note: str, today: str) -> dict | None:
    for row in reversed(rows):
        if is_search(row) and row.get("note") == note and (row.get("retrieved_at") or "")[:10] == today and (ROOT / row["path"]).exists():
            return row
    return None


def sweep(argv: list[str]) -> int:
    """One router query per company on the watch list, saved; with --fetch, the company's own pages the answer points at.

    The router is asked once for its schema (today's saved answer is reused) and once per company; a query already
    asked today is read from its saved answer. Every run's reported cost is summed against --budget."""
    parser = argparse.ArgumentParser(prog="vendor_jobs sweep")
    parser.add_argument("--company", action="append", default=[], help="only this company: a UEI or a word of the name (repeatable)")
    parser.add_argument("--limit", type=int, default=10, help="how many incumbents to ask, most live awards first")
    parser.add_argument("--results", type=int, default=5, help="results asked of the router per company")
    parser.add_argument("--query", default="", help="the keywords to ask with instead of the offices the company holds awards at")
    parser.add_argument("--domain", default="", help="the company's domain to ask by, where the schema takes one (one company at a time)")
    parser.add_argument("--take", type=int, default=2, help="pages to take per company in this run")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--max-cost", type=float, default=0.02, help="per run, passed to the router where its schema takes one")
    parser.add_argument("--budget", type=float, default=0.50, help="stop when the answers' reported cost passes this")
    parser.add_argument("--fetch", action="store_true", help="also take the company pages not yet saved")
    parser.add_argument("--dry-run", action="store_true", help="read the schema and print each payload; run nothing")
    parser.add_argument("--pause", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not HIRING.get("vendor_jobs", True):
        print(f"the {P['key']} profile opts out of vendor postings; nothing to sweep")
        return 0
    try:
        routergrowth.key()
    except LookupError as exc:
        print(f"{exc}; the vendor sweep needs it (RUNBOOK.md, Credentials); nothing collected")
        return 0
    watch = watch_list(args.limit)
    if args.company:
        wanted = [w.lower() for w in args.company]
        watch = [w for w in watch if any(x == w["uei"].lower() or x in w["name"].lower() for x in wanted)]
        if not watch:
            print(f"{', '.join(args.company)}: not on the watch list (the incumbents with live awards at {', '.join(P['fpds_offices'])}, or hiring.vendor_watch)", file=sys.stderr)
            return 2
    if not watch:
        print(f"no company to ask: {VENDORS.relative_to(ROOT)} names no vendor with a live award at the swept offices and the profile names none")
        return 0
    described, inspect_row = routergrowth.inspect(CAPABILITY, RAW_DIR, f"{NOTE} inspect{NOTE_TAG}: {CAPABILITY}")
    fields = routergrowth.fields_of(described)
    print(f"{CAPABILITY}: input fields {', '.join(sorted(fields)) or 'none declared'} (inspect saved {inspect_row['path']}, retrieved {inspect_row['retrieved_at']})")
    rows = manifest_rows()
    have = {r.get("url") for r in rows if kept_page(r)}
    today = routergrowth.today()
    spent, asked, taken, pointers = 0.0, 0, 0, 0
    for vendor in watch:
        query = args.query or query_for(vendor, fields)
        payload = routergrowth.payload_for(fields, query, args.results, args.days, args.max_cost)
        if not query:
            payload.pop("query")  # the company alone: its newest postings
        for name in ("company", "company_name", "employer"):
            if name in fields and name not in payload:
                payload[name] = display_name(vendor["name"])
        hosts = [args.domain] if args.domain and len(watch) == 1 else (vendor.get("hosts") or [])
        for name in ("domain", "website", "company_domain"):
            if name in fields and hosts and name not in payload:
                payload[name] = hosts[0]
        if args.domain and len(watch) == 1:
            vendor = {**vendor, "hosts": sorted(set(vendor.get("hosts") or []) | {args.domain})}  # the page behind that host is the company's
        if "posted_within" in fields and "posted_within" not in payload:
            payload["posted_within"] = posted_within(args.days, fields["posted_within"])
        print(f"\n{display_name(vendor['name'])} ({vendor['uei'] or 'no UEI'}; {vendor['basis']})")
        if args.dry_run:
            print(f"  would run {json.dumps(payload)}")
            continue
        if spent > args.budget:
            print(f"  budget of {args.budget} USD passed ({spent:.4f} reported); the rest waits for the next run")
            break
        note = search_note(vendor, query, payload.get("domain") or payload.get("website") or payload.get("company_domain") or "")
        saved = saved_search_today(rows, note, today)
        if saved:
            answer, row = json.loads((ROOT / saved["path"]).read_bytes()), saved
            print(f"  asked earlier today; read from the saved answer {row['path']}")
        else:
            answer, row = routergrowth.run(CAPABILITY, payload, RAW_DIR, note)
            rows.append(row)
            asked += 1
            time.sleep(args.pause)
        cost = routergrowth.cost_of(answer)
        if isinstance(cost.get("cost"), (int, float)):
            spent += float(cost["cost"])
        results = routergrowth.results(answer)
        print(f"  {len(results)} result(s); {cost.get('status') or 'status not stated'}; provider {cost.get('provider') or 'not stated'}; "
              f"charged {cost.get('cost') if cost.get('cost') is not None else 'not stated'}"
              + (f"; tried {'; '.join(cost['attempts'])}" if cost.get("attempts") else "") + f"; saved {row['path']}")
        took = 0
        for result in results:
            cls = host_allowed(result["url"], vendor)
            mark = "saved" if result["url"] in have else ""
            print(f"    [{cls or 'pointer'}] {(result.get('title') or '')[:60]}  {result['url'][:100]} {mark}")
            if cls is None:
                pointers += 1
                continue
            if args.fetch and result["url"] not in have and took < args.take:
                page = retrieve(result["url"], f"vendor job{NOTE_TAG}: {display_name(vendor['name'])[:40]} {(result.get('title') or '')[:60]}")
                have.add(result["url"])
                took += 1
                taken += page.get("status") == 200
                print(f"      {page.get('status')} {page.get('error', '')} {page.get('path', '')}")
    print(f"\n{asked} query(ies) run, {spent:.4f} USD reported; {pointers} result(s) are pointers (aggregators or unrelated hosts, never read)"
          + (f"; {taken} page(s) taken; now `vendor_jobs.py build`" if args.fetch else "; rerun with --fetch to take the company pages, then `vendor_jobs.py build`"))
    return 0


# ---------------------------------------------------------------- selfcheck

SAMPLE_VENDOR = {"uei": "LB5KVANFKPY7", "name": "L3HARRIS TECHNOLOGIES, INC.", "spellings": ["HARRIS CORPORATION", "L3HARRIS TECHNOLOGIES, INC."], "hosts": [],
                 "parent_uei": "SJULQDJ8NZU7", "parent_name": "HARRIS CORPORATION", "live": 38, "awards": 861, "offices": {"N00039": 861}, "basis": "38 live award(s) at NAVWAR HQ"}
SAMPLE_PAGE = b"""<html><head><title>Contracts Manager - San Diego | L3Harris Careers</title>
<script type="application/ld+json">{"@type":"JobPosting","title":"Contracts Manager","datePosted":"2026-09-02"}</script></head><body><nav>menu</nav>
<h1>Contracts Manager</h1>
<h2>Job Description</h2>
<p>L3Harris is seeking a Contracts Manager to support the Naval Information Warfare Systems Command (NAVWAR) in San Diego.</p>
<ul>
<li>You will administer contract N0003916C0087 and its modifications for the PMW 160 Tactical Networks Program Office.</li>
<li>The team expects to support the CANES program through 2028.</li>
<li>PMW 160 is in San Diego.</li>
</ul>
<h2>Qualifications</h2><p>Experience with Navy contracting is desired.</p>
<h2>Apply</h2><p>Click Apply Now.</p></body></html>"""


def selfcheck() -> int:
    assert company_tokens(SAMPLE_VENDOR) == ["l3harris", "harris"], company_tokens(SAMPLE_VENDOR)
    assert display_name("L3HARRIS TECHNOLOGIES, INC.") == "L3Harris Technologies" and display_name("LEIDOS, INC.") == "Leidos"
    assert registrable("careers.l3harris.com") == "l3harris" and registrable("jobs.example.co.uk") == "example" and registrable("l3harris.com") == "l3harris"
    assert host_allowed("https://careers.l3harris.com/en/job/123", SAMPLE_VENDOR) == "company"
    assert host_allowed("https://l3harris.wd5.myworkdayjobs.com/en-US/Careers/job/1", SAMPLE_VENDOR) == "ats"
    assert host_allowed("https://boards.greenhouse.io/l3harris/jobs/1", SAMPLE_VENDOR) == "ats"
    assert host_allowed("https://boards.greenhouse.io/leidos/jobs/1", SAMPLE_VENDOR) is None, "another company's applicant-system page"
    assert host_allowed("https://www.indeed.com/viewjob?jk=1", SAMPLE_VENDOR) is None and host_allowed("https://www.linkedin.com/jobs/view/1", SAMPLE_VENDOR) is None
    assert host_allowed("https://notlinkedin.com/x", SAMPLE_VENDOR) is None and host_allowed("https://spacex.com/careers/1", {"name": "SPACE EXPLORATION TECHNOLOGIES CORP."}) == "company", \
        "a host matches by its own labels, never by a substring"
    assert host_allowed("https://careers.example.com/1", {**SAMPLE_VENDOR, "hosts": ["example.com"]}) == "company", "a host the profile names for the company is the company's"
    assert host_in("jobs.lever.co", ATS_HOSTS) and not host_in("cleverco.com", ATS_HOSTS)
    note = search_note(SAMPLE_VENDOR, "L3Harris Technologies jobs NAVWAR HQ")
    assert note == f"{NOTE} search{NOTE_TAG}: LB5KVANFKPY7 L3Harris Technologies | L3Harris Technologies jobs NAVWAR HQ", note
    assert search_params(note) == {"uei": "LB5KVANFKPY7", "name": "L3Harris Technologies", "query": "L3Harris Technologies jobs NAVWAR HQ", "domain": ""}
    by_domain = search_note(SAMPLE_VENDOR, "contracts", "l3harris.com")
    assert by_domain.endswith("| contracts | domain l3harris.com") and search_params(by_domain)["domain"] == "l3harris.com" and search_params(by_domain)["query"] == "contracts"
    assert by_domain != search_note(SAMPLE_VENDOR, "contracts"), "a search by domain is another search, not read from the first's saved answer"
    assert is_search({"path": "p", "status": 200, "note": note, "url": RUN_URL}) and not is_page({"path": "p", "status": 200, "note": note, "url": RUN_URL}), \
        "a search answer is not a page"
    assert is_page({"path": "p", "status": 200, "note": f"vendor job{NOTE_TAG}: L3Harris Contracts Manager", "url": "https://careers.l3harris.com/1"})
    assert not is_page({"path": "p", "status": 200, "note": "news sweep: x", "url": "https://careers.l3harris.com/1"}), "another tool's row is not a page"
    assert not is_search({"path": "p", "status": 200, "note": note, "url": f"{routergrowth.BASE}/v1/inspect"}), "the schema answer is not a search"
    assert is_inspect({"path": "p", "status": 200, "note": f"{NOTE} inspect{NOTE_TAG}: company.jobs"})
    assert posted_on_page(SAMPLE_PAGE) == "2026-09-02" and page_title(SAMPLE_PAGE) == "Contracts Manager"
    text = page_text(SAMPLE_PAGE)
    assert "menu" not in text and "You will administer contract N0003916C0087" in text and not re.search(r"^#\s", text, re.M), "the page's own words, without its furniture"
    level, verify = confidence_of("You will administer the contract.", "workload")
    assert level == "medium" and verify[0] == VERIFY, "a company's page never reads high"
    assert confidence_of("The team expects to support the program.", "workload")[0] == "low"
    if VENDORS.exists():
        watch = watch_list(3)
        assert len(watch) == 3 and all(w["live"] and set(w["offices"]) <= set(P["fpds_offices"]) for w in watch), [w["name"] for w in watch]
        assert watch == sorted(watch, key=lambda w: (-w["live"], -w["awards"], w["name"])), "most live awards first"
        assert " jobs " in query_for(watch[0]) and any(word in query_for(watch[0]) for word in office_words(watch[0]))
    assert office_words(SAMPLE_VENDOR) == ["NAVWAR"] and query_for(SAMPLE_VENDOR) == "L3Harris Technologies jobs NAVWAR"
    assert query_for(SAMPLE_VENDOR, {"company": {}, "query": {}}) == "", "where the company travels in its own field, no title keywords are sent"
    assert posted_within(90, {"enum": ["24h", "week", "month"]}) == "month" and posted_within(7, {"enum": ["24h", "week", "month"]}) == "week"
    assert posted_within(90, {"enum": ["24h", "week"]}) == "week" and posted_within(90, {}) == "month"
    # End to end on the saved memory: the page names the command, a program office, a known contract and a program;
    # every claim quotes the page; the vacancy corroborates the incumbency; nothing reads high; the record loads.
    mem = memory()
    search = {"url": RUN_URL, "path": "", "sha256": "b" * 64, "retrieved_at": now(), "note": note}
    page = {"url": "https://careers.l3harris.com/en/job/123", "path": "", "sha256": "c" * 64, "method": "direct", "retrieved_at": now()}
    result = {"url": page["url"], "title": "Contracts Manager", "publishedDate": "2026-09-01", "company": "L3Harris", "location": "San Diego, CA", "description": ""}
    record = posting_record(result, SAMPLE_VENDOR, search, page, SAMPLE_PAGE, mem)
    assert record["id"].startswith("vendorjob:") and len(record["id"]) == len("vendorjob:") + 12 and record["url"] == page["url"]
    assert record["title"] == "Contracts Manager" and record["posted"] == {"date": "2026-09-02", "basis": "page"}, record["posted"]
    assert record["claims"][0]["statement_type"] == "vacancy" and record["claims"][0]["relation"] == "corroborates" and record["claims"][0]["passage"] == "Contracts Manager"
    assert record["source_type"] == SOURCE_TYPE and record["reliability"] == "medium" and record["page"]["host_class"] == "company"
    kinds = {c["statement_type"] for c in record["claims"]}
    assert "workload" in kinds and "vacancy" in kinds, kinds
    assert "parentage" not in kinds and "placement" not in kinds and "leadership" not in kinds, "a contractor's seat is support, never a place in the org chart"
    assert "N0003916C0087" in record["entities"]["contracts"] and "pmw:160" in record["entities"]["organizations"], record["entities"]
    flat = " ".join(re.sub(r"<[^>]+>", " ", SAMPLE_PAGE.decode()).split())
    assert all(c["passage"] in flat for c in record["claims"][1:]), "a claim quotes the page, it does not paraphrase it"
    assert all(c["confidence"] in ("low", "medium") for c in record["claims"]), "a company's page never reads high"
    assert any(c["confidence"] == "low" for c in record["claims"] if "expects to" in c["passage"]), "a hedge lowers the claim"
    assert record["loads"] and "N0003916C0087" in record["links"]["awards"] and "pmw:160" in record["links"]["offices"]
    assert VERIFY in record["verify"] and record["search"]["query"] == "L3Harris Technologies jobs NAVWAR HQ"
    assert not any(k in json.dumps(record).lower() for k in ("decision-maker", "decision maker")), "a posting is never read as authority"
    bare = posting_record(result, {**SAMPLE_VENDOR, "live": 0}, search, page, b"<html><body><h1>Engineer</h1><p>Join us.</p></body></html>", mem)
    assert bare["claims"][0]["relation"] == "new signal" and len(bare["claims"]) == 1 and not bare["loads"], "a title alone loads nothing"
    assert bare["posted"] == {"date": "2026-09-01", "basis": "router"}, "a date the page does not state comes from the router, and says so"
    print("vendor_jobs selfcheck ok")
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

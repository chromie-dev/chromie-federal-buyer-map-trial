#!/usr/bin/env python3
"""Read a news article or press release as dated source observations about the agency.

    python research/tools/news.py build                 # model every saved news page
    python research/tools/news.py read URL|news:<id>    # one article's record
    python research/tools/news.py watch [--fetch]       # poll the feeds for items not yet saved
    python research/tools/news.py search "QUERY" [--fetch]   # discovery through Exa (EXA_API_KEY)
    python research/tools/news.py sweep [--fetch] [--provider exa|routergrowth]   # GDELT, keyless, runs beside it
    python research/tools/news.py --selfcheck

A news item is a source statement like any other: the bytes are saved and recorded in
`research/sources/documents_manifest.jsonl` first, and the record built here quotes the article's own
sentences. Each claim carries the exact passage it rests on, what the memory already holds about
it (a new signal, a corroboration or a conflict), a confidence and what a reviewer still has to
verify. Nothing here is promoted into the organization memory: an article is an observation and a
candidate relationship, never a fact about the org chart (research/docs/08_org_memory_format.md).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "research"
MANIFEST = RESEARCH / "sources" / "documents_manifest.jsonl"
from fetch import kept_page  # noqa: E402
from agency import EVENTS as EVENTS_DIR, MEMORY, NOTE_TAG, P, note_is_ours  # noqa: E402

RECORDS = EVENTS_DIR / "news_observations.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from markdown import html_to_markdown  # noqa: E402
BUDGET_LINES = EVENTS_DIR / "budget_lines.json"
RAW_NEWS = ROOT / "data" / "raw" / "news"

# Who published it, and what kind of source that makes it. An official announcement states the
# agency's own actions; trade reporting is a newsroom reading them; secondary reporting repeats
# another outlet. A direct interview is read off the text, not the host, because any of these
# can carry one.
OFFICIAL_HOSTS = (".mil", ".gov")
TRADE_HOSTS = {
    "breakingdefense.com": "Breaking Defense", "www.defensenews.com": "Defense News",
    "insidedefense.com": "Inside Defense", "news.usni.org": "USNI News",
    "www.nationaldefensemagazine.org": "National Defense", "seapowermagazine.org": "Seapower",
    "executivegov.com": "ExecutiveGov", "www.govconwire.com": "GovConWire",
    "orangeslices.ai": "OrangeSlices AI", "federalnewsnetwork.com": "Federal News Network",
    "www.defenseone.com": "Defense One", "defensescoop.com": "DefenseScoop",
    "www.militaryaerospace.com": "Military Aerospace Electronics",
    "intelligencecommunitynews.com": "Intelligence Community News",
    "www.c4isrnet.com": "C4ISRNET", "www.airandspaceforces.com": "Air & Space Forces Magazine",
}
OFFICIAL_NAMES = P["news"]["official_names"]
RELIABILITY = {"official announcement": "high", "direct interview": "high",
               "trade reporting": "medium", "secondary reporting": "low",
               # a company's own careers page: first-hand for what the company says it wants to staff, and nothing
               # more; never read as high beside an agency's own announcement (vendor_jobs.py)
               "company statement": "medium"}

# Hosts a query returns that are not articles: a social post is not a publication with a byline,
# and the contracting sites are already read by the notice and award tools in this repo.
NOT_ARTICLE_HOSTS = ("linkedin.com", "x.com", "twitter.com", "facebook.com", "youtube.com", "reddit.com",
                     "sam.gov", "highergov.com", "govtribe.com", "usaspending.gov", "fpds.gov",
                     # a job announcement is the hiring family's record (jobs.py), never an article
                     "usajobs.gov",
                     # a site that republishes notices is a copy of a source this repo reads first-hand
                     "cleat.ai", "cleatus.com", "govwin.com", "bidprime.com", "findrfp.com")

# Feeds and index pages polled by `watch`. An index page has no feed, so the item links are read
# out of its HTML. Nothing here needs an API key.
FEEDS = P["news"]["feeds"]  # a general feed (DVIDS) takes no query; the relevance filter below is ours

# A sentence is read as one kind of statement. The first pattern that matches names it; the
# vocabulary is the organization memory's, widened for what news carries that a notice does not.
STATEMENT_PATTERNS = [
    ("naming", re.compile(r"\b(renamed|rename[sd]?|change[sd]? its name|now known as|formerly known as|redesignat)", re.I)),
    ("consolidation", re.compile(r"\b(consolidat|merg(?:e|ed|ing)|disestablish|establish(?:es|ed|ing|ment)|stood up"
                                 r"|stand(?:s|ing)? up|activat(?:es|ed|ion)|realign|transition(?:s|ed|ing)? to)", re.I)),
    ("parentage", re.compile(r"\b(reports? to|reporting to|under the|part of|assigned to|falls under|aligned under|transferred to)\b", re.I)),
    ("leadership", re.compile(r"\b(assumed (?:command|the duties)|relieved|was named|has been named|appointed"
                              r"|takes? over as|becomes? (?:the )?program manager|will (?:serve|lead)"
                              r"|serves? as (?:the )?(?:acting )?(?:program manager|program executive officer|portfolio"
                              r"|direct reporting|deputy)|reporting directly to|performing the duties of"
                              r"|selected to lead|stepped down|departs? the)\b", re.I)),
    ("protest", re.compile(r"\b(protest|GAO (?:sustain|den|dismiss)|Court of Federal Claims|corrective action)\b", re.I)),
    ("funding", re.compile(r"\b(budget request|appropriat|enacted|funding (?:line|shift|cut|increase)|P-1 line|R-1 line|reprogramm)\b", re.I)),
    ("recompete", re.compile(r"\b(recompete|re-compete|follow-on (?:contract|production)|bridge contract|option (?:year|period)|extend(?:s|ed)? the contract)\b", re.I)),
    ("industry_engagement", re.compile(r"\b(industry day|industry engagement|request for information|sources sought|pitch day|advance planning brief)\b", re.I)),
    ("acquisition_strategy", re.compile(r"\b(acquisition strategy|other transaction|commercial solutions opening|sole[- ]source|full and open competition|indefinite[- ]delivery)\b", re.I)),
    ("milestone", re.compile(r"\b(milestone [ABC]|initial operational capability|full[- ]rate production|low[- ]rate initial production|fielded|delivered the first|completed (?:testing|trials)|authority to operate)\b", re.I)),
    ("delay", re.compile(r"\b(delay|slipp?(?:ed|age)|behind schedule|postponed|rescheduled)\b", re.I)),
    ("performance", re.compile(r"\b(awarded|was awarded|task order|delivery order|exercised an option|contract (?:to|with))\b", re.I)),
    ("contracting_support", re.compile(r"\b(contracting (?:office|activity)|on behalf of|issued by)\b", re.I)),
]
# A sentence that denies a change is a statement about the agency, but it is not a claim of the
# thing it denies: "the Navy has not announced changes to PEO C4I" is not a parentage assertion.
NEGATION_RE = re.compile(r"\b(has not|have not|had not|did not|does not|will not|no (?:changes?|announcement|plans)"
                         r"|not announced|remains? unchanged)\b", re.I)
# A parentage phrase counts only when an office follows it. "under the theme of" and "as part of
# the reorganization" are prepositions, not places in an org chart.
PARENTAGE_TAIL = re.compile(r"\b(?:reports? to|reporting to|part of|assigned to|falls under|aligned under"
                            r"|transferred to|under)\s+(?:the|its|a|an)?\s*(?P<tail>[A-Z0-9][^.;]{0,140})")
# A consolidation verb counts only when an organization is what was established, merged or
# realigned, named just after the verb or as its subject: "this form establishes a clear pathway"
# and "establishing the foundation for" are not reorganizations. A capitalised verb inside a
# sentence belongs to a name or a quoted title ("Consolidated Afloat Network").
ORG_HEAD_RE = re.compile(r"\b(?:offices?|commands?|organi[sz]ations?|executives?|directorates?|centers?|portfolios?"
                         r"|elements|PEOs?|PAEs?|DRPMs?|PMW|PMA|PMS|SYSCOMs?)\b", re.I)
PASSIVE_RE = re.compile(r"\b(?:was|were|is|are|has been|have been|had been|will be|being)\s+(?:\w+ly\s+)?$", re.I)
# "May" is a month here as often as it is a hedge, so the hedge has to carry its verb.
HEDGE_RE = re.compile(r"\b(expected to|expects to|plans to|could|may (?:be|have|not|also|still)|would|is likely|sources said|reportedly|according to (?:people|sources)|anticipates)\b", re.I)
INTERVIEW_RE = re.compile(r"\b(told (?:reporters|[A-Z][a-z]+)|in an interview|said in an interview|speaking (?:at|to))\b")
# The page's own time words, read against its metadata date: a "Posted" stamp in the text wins, and a text that looks
# ahead to a month the date has already passed ("will retire by the end of April", dated July) was indexed, not written, then.
POSTED_RE = re.compile(r"\bPosted(?: on)?:?\s+([A-Z][a-z]+\.? \d{1,2}, \d{4}|\d{4}-\d{2}-\d{2})")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
AHEAD_RE = re.compile(rf"\bwill\b[^.]{{0,80}}?\b(?:by|in|until)\s+(?:the\s+end\s+of\s+|early\s+|mid-|late\s+)?({'|'.join(MONTH_NAMES)})\b(?![ ,]*\d)")


def dated(text: str, published: str) -> tuple[str, str]:
    """The date to keep and, when the text disagrees with the metadata, why."""
    posted = as_date(m.group(1)) if (m := POSTED_RE.search(text)) else ""
    if posted and posted != published:
        return posted, f"the page prints Posted {posted}; its metadata says {published or 'nothing'}"
    ahead = AHEAD_RE.search(text)
    if published and ahead and MONTH_NAMES.index(ahead.group(1)) + 1 < int(published[5:7]):
        return published, f"the text looks ahead to {ahead.group(1)} yet is dated {published}: likely the date it was indexed, not written"
    return published, ""
DATE_IN_TEXT = re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(\d{4})\b")
DATE_DOTTED = re.compile(r"\b(0?[1-9]|1[0-2])\.(0?[1-9]|[12]\d|3[01])\.(20\d{2})\b")
DATE_DAY_FIRST = re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{4})\b", re.I)
BUDGET_LINE_RE = re.compile(r"\b(?:P-1|R-1)\s*line\s*(?:item\s*)?(\d{1,4})\b|\bline item\s*(\d{4})\b", re.I)
# A person the memory does not hold yet is still a person the article names. A rank, an honorific
# or a role in front of a name, or one of the verbs a newsroom uses after it, is the whole rule:
# a general name detector would take half the capitalised words on the page.
PERSON_TITLE_RE = re.compile(
    r"\b(?:Acting |Deputy |Assistant |Under )?(?:Secretary|Rear Adm\.|Vice Adm\.|Adm\.|Capt\.|Cmdr\.|Cdr\.|Col\.|"
    r"Lt\. Col\.|Maj\.|Lt\.|Mr\.|Ms\.|Mrs\.|Dr\.|Program Manager|Program Executive Officer|Portfolio Acquisition Executive)"
    r"(?:\s+of\s+the\s+[A-Z][a-z]+)?"  # "Secretary of the Navy Hung Cao"
    r"\s+([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){0,2})")
PERSON_VERB_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+)\s+(?:will serve|will lead|was named|has been named"
                            r"|assumed|takes over|said|told|serves as|was appointed)\b")
# ponytail: the places NAVWAR's offices actually sit, as a list. A general gazetteer is a
# dependency and a whole class of false hits for one field of the record.
PLACES = ["San Diego", "Point Loma", "Charleston", "Norfolk", "Washington", "Arlington", "Pearl Harbor",
          "New Orleans", "Philadelphia", "Patuxent River", "Dahlgren", "Crane", "Quantico", "Bahrain", "Naples", "Yokosuka"]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest_rows() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]


def origin_url(row: dict) -> str:
    """The article's own address, with the Wayback wrapper taken off."""
    url = row.get("final_url") or row.get("url") or ""
    match = re.search(r"/web/\d{14}(?:id_)?/(https?://.+)$", url)
    return match.group(1) if match else url


def host_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def publisher_of(url: str) -> tuple[str, str]:
    """(publisher, source type) from the address alone; the text can still raise it to an interview."""
    host = host_of(url)
    if host in TRADE_HOSTS:
        return TRADE_HOSTS[host], "trade reporting"
    if host in OFFICIAL_NAMES:
        return OFFICIAL_NAMES[host], "official announcement"
    if host.endswith(OFFICIAL_HOSTS):
        return host.replace("www.", ""), "official announcement"
    return host.replace("www.", "") or "unknown", "secondary reporting"


class _Reader(HTMLParser):
    """The title and the metadata a page states about itself (meta tags, <time>, JSON-LD). Stdlib only.
    The page's content is not read here: markdown.py renders it, and article_record() reads the rendering."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.jsonld: list[str] = []
        self._in_title = False
        self._in_ld = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and (attributes.get("type") or "").endswith("ld+json"):
            self._in_ld = True
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            name = (attributes.get("property") or attributes.get("name") or "").lower()
            if name and attributes.get("content"):
                self.meta.setdefault(name, attributes["content"])
        if tag == "time" and attributes.get("datetime"):
            self.meta.setdefault("time", attributes["datetime"])

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_ld:
            self.jsonld.append(data)
        elif self._in_title:
            self.meta.setdefault("title", data.strip())


def linked_data(reader: _Reader) -> dict:
    """The schema.org block a newsroom emits, when it parses."""
    for blob in reader.jsonld:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and str(item.get("@type", "")).lower() in ("newsarticle", "article", "report", "blogposting"):
                return item
    return {}


def as_date(value: str) -> str:
    """A date in one format, or empty. Never a guess: an unparsable date is no date."""
    value = (value or "").strip()
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})(?!\d)", value)  # an ISO stamp runs on into 2026-05-11T10:00
    if iso:
        return iso.group(0)
    written = DATE_IN_TEXT.search(value)
    if written:
        month = MONTHS[written.group(1)[:3].lower()]
        return f"{int(written.group(3)):04d}-{month:02d}-{int(written.group(2)):02d}"
    rfc = DATE_DAY_FIRST.search(value)  # feeds write "Mon, 11 May 2026 10:00:00 GMT"
    if rfc:
        return f"{int(rfc.group(3)):04d}-{MONTHS[rfc.group(2)[:3].lower()]:02d}-{int(rfc.group(1)):02d}"
    dotted = DATE_DOTTED.search(value)  # DVIDS prints 05.11.2026
    if dotted:
        return f"{int(dotted.group(3)):04d}-{int(dotted.group(1)):02d}-{int(dotted.group(2)):02d}"
    return ""


def author_of(reader: _Reader, ld: dict, text: str) -> str:
    author = ld.get("author")
    if isinstance(author, dict):
        author = author.get("name")
    if isinstance(author, list):
        author = ", ".join(a.get("name", "") if isinstance(a, dict) else str(a) for a in author)
    if author:
        return str(author).strip()
    for key in ("author", "article:author", "dcterms.creator", "byline"):
        if reader.meta.get(key):
            return reader.meta[key].strip()
    byline = re.search(r"\bBy\s+([A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){1,3})", text[:1500])
    return byline.group(1) if byline else ""


def published_of(reader: _Reader, ld: dict, text: str, headline: str = "") -> str:
    """The date the page states for itself: its metadata first, then the date it prints by the
    headline. A site that prints the date only in the body (DVIDS writes 08.19.2025) is read there,
    from the headline on, so the navigation's dates are not mistaken for the article's."""
    for candidate in (ld.get("datePublished"), reader.meta.get("article:published_time"),
                      reader.meta.get("og:article:published_time"), reader.meta.get("date"),
                      reader.meta.get("dcterms.date"), reader.meta.get("pubdate"), reader.meta.get("time")):
        stamp = as_date(str(candidate or ""))
        if stamp:
            return stamp
    start = text.find(headline[:40]) if headline else -1
    return as_date(text[start:start + 1500] if start >= 0 else text[:1500])


BLOCK_MARKER = re.compile(r"^(?:>\s?)*(?:#{1,6}\s+|-\s+|\d+\.\s+)?")


def sentences(text: str) -> list[str]:
    """The sentences of a Markdown rendering, one block per line; the block's marker (a heading's `#`, a
    list item's `-`, a quote's `>`) is the rendering's, not the writer's, and is not part of any passage."""
    out = []
    for block in text.split("\n"):
        for part in re.split(r"(?<=[.!?])\s+(?=[\"'(]?[A-Z0-9])", BLOCK_MARKER.sub("", block.strip(), count=1)):
            part = part.strip()
            if len(part) >= 40:
                out.append(part)
    return out


# ---------------------------------------------------------------- what the memory already holds

def memory() -> dict:
    """Everything a claim is checked against: the org memory, the forecast lines and the notices.

    Imported here rather than at module load so `watch` and `search`, which only need the network,
    run on a checkout whose datapack has not been built yet.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import trace as tracer
    from lrae_package import contract_tokens

    seed = json.loads((MEMORY / "organization_seed.json").read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    for node in seed["nodes"]:
        for text in [node["name"]] + [a["text"] for a in node.get("aliases", [])]:
            if len(text) > 3:
                names.setdefault(text.lower(), node["id"])
    parents: dict[str, set[str]] = {}
    leads: dict[str, set[str]] = {}
    for rel in seed["relationships"]:
        if rel.get("review_status") == "retracted" or rel.get("retraction"):
            continue
        if rel["type"] == "child_of":
            parents.setdefault(rel["from"], set()).add(rel["to"])
        if rel["type"] == "leads":
            leads.setdefault(rel["to"], set()).add(rel["from"])
    try:
        ctx = tracer.match_context()
        lines = [l for l in tracer.lrae_lines() if l["release"] in ctx["current"]]
    except (FileNotFoundError, IndexError, KeyError):
        ctx, lines = {"rarity": {}, "parents": parents, "offices_of": {}, "canon": {}, "chains": {}, "latest": ""}, []
    known_contracts = {t for l in lines for t in contract_tokens(l["existing_contract_number"])}
    known_sols = {tracer.compact(s) for l in lines for s in tracer.SOL_RE.findall(l["requirement_title"].replace(" ", ""))}
    for path in sorted(tracer.NOTICES.glob("*.json")) if tracer.NOTICES.exists() else []:
        detail = tracer.notice_detail(path.stem) if not path.name.startswith(("._", "search_")) else None
        if detail and detail["solicitation"]:
            known_sols.add(tracer.compact(detail["solicitation"]))
    return {"seed": seed, "node_ids": {n["id"] for n in seed["nodes"]},
            "person_names": {n["name"].lower(): n["id"] for n in seed["nodes"] if n["type"] == "person"},
            "node_names": names, "parents": parents, "leads": leads, "tracer": tracer,
            "contract_tokens": contract_tokens, "lines": lines, "ctx": ctx,
            "known_contracts": known_contracts, "known_sols": known_sols, "budget_lines": budget_lines()}


def budget_lines() -> dict[str, str]:
    """P-1 line item numbers the budget books carry, to the program each names."""
    if not BUDGET_LINES.exists():
        return {}
    return {line["li"]: line["title"] for line in json.loads(BUDGET_LINES.read_text(encoding="utf-8"))["lines"]}


def named_people(text: str) -> list[str]:
    """People the text names, whether or not the memory holds them; an article often names them first."""
    found = {m.group(1).strip() for m in PERSON_TITLE_RE.finditer(text)}
    found |= {m.group(1).strip() for m in PERSON_VERB_RE.finditer(text)}
    return sorted(n for n in found if len(n.split()) >= 2)


# A change of charge as the Navy's own outlets write it: who relieved whom as what, and the office the same
# clause goes on to name. A subject keeps its rank; a surname alone is not a name here, so "White was
# introduced as the new program manager" is left to the sentence that gives the full name.
LEADER_RANK = r"(?:(?:U\.S\. Navy |Navy )?(?:Rear Adm\.|Vice Adm\.|Adm\.|Capt\.|CAPT|Cmdr\.|CDR|Lt\. Cmdr\.|Mr\.|Ms\.|Mrs\.|Dr\.)\s+)?"
LEADER_WORD = r"[A-Z][a-z][A-Za-z'’-]*"
LEADER_NAME = LEADER_RANK + r"[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+" + LEADER_WORD + r"){1,2}"
LEADER_OLD = LEADER_RANK + r"[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+" + LEADER_WORD + r"){0,2}"
LEADER_TITLED = LEADER_RANK[:-1] + r"[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+" + LEADER_WORD + r"){1,2}"  # the rank is required: a listing writes "PAE Maritime: Mr. Jim Day"
LEADER_ROLE = r"program manager|deputy program manager|program executive officer|portfolio acquisition executive|commanding officer|executive director"
LEADERSHIP_RE = re.compile(
    rf"(?P<new>{LEADER_NAME}) relieved? (?P<old>{LEADER_OLD}) as (?:the )?(?P<role>{LEADER_ROLE})\b"
    rf"|(?P<new2>{LEADER_NAME})(?:, [^.]{{0,160}}?,)? (?:was|is) (?:introduced|named|selected|appointed) as (?:the )?(?:new |interim )?(?P<role2>{LEADER_ROLE})\b"
    rf"|(?P<office3>PAE [A-Z][a-z]+(?: [A-Z][a-z]+)?): (?P<new3>{LEADER_TITLED})")
# The clause after the role runs to the end of its sentence, or to the ellipsis a teaser cuts it with; an
# abbreviation's full stop does not end it.
CLAUSE_END_RE = re.compile(r"\.{2,}|(?<!U\.S)(?<!\bMr)(?<!\bMs)(?<!\bDr)(?<!\bMrs)(?<!\bCapt)(?<!\bCmdr)(?<!\bAdm)(?<!\bLt)(?<!\bJr)(?<!\bSr)[.!?](?=\s+[A-Z“\"]|\s*$)")
PROGRAM_OFFICE_PREFIXES = ("pmw:", "pma:", "drpm:", "pae:")


def office_named_in(clause: str) -> str:
    """The one program office the clause names through the memory's aliases; nothing when it names none or two."""
    from trace import resolve_offices  # noqa: E402
    flat = re.sub(r"\s+", " ", clause).lower()
    hits = sorted((flat.find(re.sub(r"\s+", " ", o["matched"]).lower()) % 10_000, o["office"]) for o in resolve_offices(clause)
                  if not o["former"] and o["office"].startswith(PROGRAM_OFFICE_PREFIXES))
    offices = sorted({office for _, office in hits}, key=lambda office: next(pos for pos, o in hits if o == office))
    return offices[0] if len(offices) == 1 else ""


def leadership_changes(text: str) -> list[dict]:
    """Every change of charge the text states, with the office its own clause names. A page states the same change
    more than once (a teaser, a caption, the body); the reading that names the office is the one kept.
    The text is the page's Markdown rendering, one block per line: a clause does not run past its block."""
    blocks = [re.sub(r"\s+", " ", BLOCK_MARKER.sub("", block.strip(), count=1)) for block in text.split("\n")]
    flat = "\n".join(block for block in blocks if block)
    out: dict[tuple, dict] = {}
    for m in LEADERSHIP_RE.finditer(flat):
        if m.group("office3"):
            name, old, role, clause, tail = m.group("new3"), "", "portfolio acquisition executive", m.group("office3"), 0
        else:
            name, old, role = m.group("new") or m.group("new2"), m.group("old") or "", (m.group("role") or m.group("role2")).lower()
            rest = flat[m.end():m.end() + 300].split("\n", 1)[0]
            end = CLAUSE_END_RE.search(rest)
            clause = rest[:end.start()] if end else rest
            tail = len(clause)
        name, old = (re.sub(r"^(?:U\.S\. )?Navy\s+", "", n) for n in (name, old))
        change = {"name": name, "role_as_written": role, "office": office_named_in(clause), "relieved": old,
                  "passage": re.sub(r"\s+", " ", flat[m.start():m.end() + tail])[:300]}
        held = out.get((name, role))
        if held is None or (not held["office"] and change["office"]):
            out[(name, role)] = change
    return list(out.values())


def entities_in(text: str, mem: dict) -> dict:
    """Every organization, person, program, contract, solicitation, budget line and place the text names."""
    tracer = mem["tracer"]
    offices = [o for o in tracer.resolve_offices(text)]
    # A program name is a token few forecast lines carry. "c4i", "peo" and "navwar" are on dozens,
    # so they name the command, not a buy, and they would join an article to every line at once.
    programs = sorted({t for t in tracer.distinctive_tokens(text) if 0 < mem["ctx"]["rarity"].get(t, 0) <= 3})
    contracts = [c for c in mem["contract_tokens"](text)]
    sols = sorted({tracer.compact(s) for s in tracer.SOL_RE.findall(text.replace(" ", ""))})
    people = sorted({node_id for name, node_id in mem["person_names"].items() if re.search(rf"\b{re.escape(name)}\b", text, re.I)})
    budget = sorted({m.group(1) or m.group(2) for m in BUDGET_LINE_RE.finditer(text)})
    requirements = sorted({(l["pid"] or l["record_key"]) for l in mem["lines"]
                           if (l["pid"] and l["pid"] in text)
                           or (programs and {t for t in tracer.distinctive_tokens(l["requirement_title"])
                                             if mem["ctx"]["rarity"].get(t, 0) <= 3} & set(programs))})
    return {"organizations": sorted({o["office"] for o in offices}),
            "organizations_as_written": [{"office": o["office"], "as_written": o.get("context", "")[:160], "former": o["former"]} for o in offices],
            "people": people, "people_as_written": named_people(text), "programs": programs,
            "contracts": contracts, "solicitations": sols,
            "requirements": requirements, "budget_lines": budget,
            "locations": [p for p in PLACES if re.search(rf"\b{re.escape(p)}\b", text)]}


# What a content system prints around an article: photo captions, image tools, share bars.
FURNITURE_RE = re.compile(r"(read more|view image page|see less|photo by|download image|share this|click here|skip to main)", re.I)


def is_prose(passage: str) -> bool:
    """A sentence a person wrote about the agency, not a caption, a menu label or a heading."""
    return len(passage.split()) >= 10 and not FURNITURE_RE.search(passage) and passage.count("|") < 2


def states_parentage(passage: str, mem: dict) -> bool:
    """True when a parentage phrase is followed by an office the memory knows."""
    for match in PARENTAGE_TAIL.finditer(passage):
        if mem["tracer"].resolve_offices(match.group("tail")):
            return True
    return False


def states_consolidation(passage: str) -> bool:
    """True when an establish, merge or realign verb has an organization as its object or subject."""
    for match in dict(STATEMENT_PATTERNS)["consolidation"].finditer(passage):
        before = passage[:match.start()]
        if match.group(0)[0].isupper() and before.strip(" \"'“‘–—-"):
            continue
        after = re.split(r"[.;,:]", passage[match.end():], maxsplit=1)[0].split()[:10]
        subject = re.split(r"[.;,:]", before)[-1].split()[-8:] if PASSIVE_RE.search(before) else []
        if ORG_HEAD_RE.search(" ".join(after + subject)):
            return True
    return False


def statement_type_of(passage: str, ents: dict, mem: dict | None = None) -> str:
    for kind, pattern in STATEMENT_PATTERNS:
        if not pattern.search(passage):
            continue
        if kind == "consolidation" and not states_consolidation(passage):
            continue
        if NEGATION_RE.search(passage) and kind in ("parentage", "leadership", "consolidation", "naming"):
            return "no change stated"
        if kind == "parentage" and mem is not None and not states_parentage(passage, mem):
            continue
        return kind
    return "existence" if ents["organizations"] or ents["people"] else "listing"


def relate(kind: str, ents: dict, mem: dict) -> tuple[str, str]:
    """Whether the claim opens a signal, corroborates the memory, or conflicts with it.

    Only what the memory can be asked about is answered: a parentage claim against the stored
    parents, a leadership claim against the stored leads, and anything naming a contract,
    solicitation or forecast line already stored against that record. The rest is a new signal,
    which means untested, not true.
    """
    offices = ents["organizations"]
    if kind == "no change stated":
        held = [o for o in offices if o in mem["parents"] or o in mem["leads"]]
        if held:
            return "corroborates", f"the article states no change, and the memory holds {', '.join(sorted(held)[:3])} as it is"
        return "new signal", "the article states that no change was announced"
    if kind == "parentage" and len(offices) >= 2:
        for child in offices:
            stored = mem["parents"].get(child, set())
            named = [o for o in offices if o != child]
            if stored & set(named):
                return "corroborates", f"the memory already holds {child} under {', '.join(sorted(stored & set(named)))}"
            if stored:
                return "conflicts", f"the memory holds {child} under {', '.join(sorted(stored))}, the article names {', '.join(named)}"
        return "new signal", "no parentage is stored for the offices named"
    if kind == "leadership" and ents["people"] and offices:
        for office in offices:
            stored = mem["leads"].get(office, set())
            if stored & set(ents["people"]):
                return "corroborates", f"the memory already has {', '.join(sorted(stored & set(ents['people'])))} leading {office}"
            if stored:
                return "conflicts", f"the memory has {', '.join(sorted(stored))} leading {office}, the article names {', '.join(ents['people'])}"
        return "new signal", "no leader is stored for the office named"
    if kind in ("consolidation", "naming") and offices:
        held = [o for o in offices if o in mem["node_ids"]]
        if held:
            return "corroborates", f"the memory holds {', '.join(sorted(held)[:3])} as an office of this agency"
        return "new signal", "the article names an office the memory does not hold"
    hit = ([c for c in ents["contracts"] if c in mem["known_contracts"]]
           + [s for s in ents["solicitations"] if s in mem["known_sols"]] + ents["requirements"])
    if hit:
        return "corroborates", f"the record already holds {', '.join(sorted(set(hit))[:3])}"
    if offices and not any(o in mem["parents"] or o in mem["leads"] for o in offices):
        return "new signal", "the office is in the memory but the article states something it does not hold"
    return "new signal", "nothing stored to check this against"


CONFIDENCE_ORDER = ["low", "medium", "high"]


def confidence_of(source_type: str, passage: str, kind: str, dated: bool) -> tuple[str, list[str]]:
    """How far the claim can be taken, and what a reviewer still has to confirm."""
    level = CONFIDENCE_ORDER.index(RELIABILITY[source_type])
    verify: list[str] = []
    if HEDGE_RE.search(passage):
        level -= 1
        verify.append("the passage states an intention, not an action taken")
    if kind in ("parentage", "leadership", "consolidation", "naming") and not dated:
        level -= 1
        verify.append("no date is stated for the change")
    if source_type in ("trade reporting", "secondary reporting"):
        verify.append("a second source, or the agency's own announcement")
    if kind in ("funding", "protest", "delay"):
        verify.append("the budget exhibit, docket or notice the passage refers to")
    return CONFIDENCE_ORDER[max(0, min(level, 2))], verify


def asserts_something(kind: str, ents: dict, mem: dict) -> bool:
    """A claim states something about the agency. A sentence that only mentions it is background.

    Without this, an article contributes one claim per sentence that happens to carry an office
    name, and the record fills with paragraphs that assert nothing a reviewer can check.
    """
    if kind not in ("existence", "listing"):
        return True
    return bool(ents["people"] or ents.get("people_as_written") or ents["requirements"] or ents["budget_lines"]
                or [c for c in ents["contracts"] if c in mem["known_contracts"]]
                or [s for s in ents["solicitations"] if s in mem["known_sols"]])


def article_record(row: dict, body: bytes, mem: dict) -> dict:
    """One saved page as a dated observation with its claims, entities, links and open questions."""
    reader = _Reader()
    reader.feed(body.decode("utf-8", "replace"))  # the page's metadata: title, meta tags, JSON-LD
    text = html_to_markdown(body.decode("utf-8", "replace"))  # the page's content, as Markdown
    ld = linked_data(reader)
    url = origin_url(row)
    publisher, source_type = publisher_of(url)
    if INTERVIEW_RE.search(text):
        source_type = "direct interview" if source_type == "official announcement" else source_type
    headline = (ld.get("headline") or reader.meta.get("og:title") or reader.meta.get("title") or "").strip()
    published, date_note = dated(text, published_of(reader, ld, text, headline) or as_date(row.get("note", "")))
    claims, background = [], 0
    for passage in sentences(text):
        if not is_prose(passage) or passage.strip() == headline.strip():
            continue  # a caption, a menu label or the headline again is not a statement about the agency
        ents = entities_in(passage, mem)
        if not (ents["organizations"] or ents["people"] or ents["contracts"] or ents["solicitations"] or ents["programs"]):
            continue
        kind = statement_type_of(passage, ents, mem)
        if not asserts_something(kind, ents, mem):
            background += 1
            continue
        relation, why = relate(kind, ents, mem)
        level, verify = confidence_of(source_type, passage, kind, bool(as_date(passage) or (published and not date_note)))
        claims.append({"statement_type": kind, "passage": passage[:600],
                       "subjects": ents["organizations"] + ents["people"], "people": ents["people_as_written"],
                       "programs": ents["programs"], "contracts": ents["contracts"], "solicitations": ents["solicitations"],
                       "relation": relation, "relation_note": why, "confidence": level, "verify": verify,
                       "stated_on": as_date(passage)})
    whole = entities_in(text, mem)
    relations = {c["relation"] for c in claims}
    rollup = "conflicts" if "conflicts" in relations else ("corroborates" if "corroborates" in relations else "new signal")
    return {
        "id": "news:" + (row.get("sha256") or "")[:12],
        "headline": headline, "publisher": publisher, "url": url, "published": published, "date_note": date_note,
        "author": author_of(reader, ld, text), "retrieved_at": row.get("retrieved_at", ""),
        "source_type": source_type, "reliability": RELIABILITY[source_type],
        "record": {"path": row.get("path", ""), "sha256": row.get("sha256", ""), "method": row.get("method", ""),
                   "fetched_from": row.get("fetched_from", "")},
        "entities": whole,
        "links": {"offices": whole["organizations"], "requirements": whole["requirements"],
                  "solicitations": [s for s in whole["solicitations"] if s in mem["known_sols"]],
                  "awards": [c for c in whole["contracts"] if c in mem["known_contracts"]],
                  "budget_lines": [{"line_item": b, "program_as_written": mem["budget_lines"].get(b, "")} for b in whole["budget_lines"]]},
        "claims": claims, "leadership": leadership_changes(text), "relation": rollup, "background_sentences": background,
        "verify": sorted({v for c in claims for v in c["verify"]} | ({f"the date: {date_note}"} if date_note else set())),
        "modelled_at": now(),
    }


# ---------------------------------------------------------------- commands

# Article addresses as the agency's content systems write them: a news path, or the DotNetNuke
# "Article/<id>" and "ArticleDetails.aspx?ID=" forms every .navy.mil site still serves.
NEWS_URL_RE = re.compile(r"/(news|press|press-office|press-release|article|story|media)(?:/|\b)|articledetails|[?&]article=", re.I)


def is_news(row: dict) -> bool:
    """A saved page that reads as an article: an official or trade host, on a news path."""
    if row.get("status") != 200 or row.get("mime") != "text/html" or not row.get("path"):
        return False
    if row.get("content_status") == "rejected_stub":
        return False
    url = origin_url(row)
    host = host_of(url)
    note = row.get("note", "")
    if any(h in host for h in NOT_ARTICLE_HOSTS):
        return False
    # A page taken because a news search returned it is read as an article whatever the host: the
    # publisher table only decides how the source is rated, not whether it is read. Hand-keeping a
    # host list per agency is the thing that would not travel to the next agency.
    if not note_is_ours(note):
        return False
    if re.search(r"^news (?:sweep|search|watch)", note, re.I):
        return True
    if not (host in TRADE_HOSTS or host in OFFICIAL_NAMES or host.endswith(OFFICIAL_HOSTS)):
        return False
    return bool(NEWS_URL_RE.search(url) or re.search(r"\b(release|article|news|announce)", note, re.I))


def build(argv: list[str]) -> int:
    mem = memory()
    records, skipped, seen = [], 0, set()
    for row in manifest_rows():
        if not is_news(row) or row.get("sha256") in seen:
            continue  # the same page fetched twice (direct, then through Browserbase) is one article
        path = ROOT / row["path"]
        if not path.exists():
            skipped += 1
            continue
        seen.add(row.get("sha256"))
        record = article_record(row, path.read_bytes(), mem)
        if not record["claims"]:
            skipped += 1  # a page that names nothing the memory knows is not evidence about this agency
            continue
        records.append(record)
    records.sort(key=lambda r: (r["published"] or "", r["id"]))
    RECORDS.write_text(json.dumps({"generated": now(), "source": "chromie-federal-buyer-map-trial/research/tools/news.py",
                                   "articles": records}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    claims = [c for r in records for c in r["claims"]]
    print(f"{len(records)} article(s) modelled, {skipped} saved page(s) carried nothing to model, {len(claims)} claim(s)")
    for relation in ("conflicts", "corroborates", "new signal"):
        print(f"  {relation}: {len([c for c in claims if c['relation'] == relation])}")
    print(f"written to {RECORDS.relative_to(ROOT)}")
    return 0


def load_records() -> list[dict]:
    if not RECORDS.exists():
        print(f"{RECORDS.relative_to(ROOT)} not built yet: run `python research/tools/news.py build`", file=sys.stderr)
        return []
    return json.loads(RECORDS.read_text(encoding="utf-8"))["articles"]


def show(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="news read")
    parser.add_argument("target", help="news:<sha12>, a URL, or a word in the headline")
    args = parser.parse_args(argv)
    hits = [r for r in load_records() if args.target in (r["id"], r["url"]) or args.target.lower() in r["headline"].lower()]
    if not hits:
        print("no modelled article matches", file=sys.stderr)
        return 1
    for record in hits[:3]:
        print(f"\n# {record['headline']}")
        print(f"{record['publisher']}, published {record['published'] or 'date not stated'}"
              f"{', ' + record['author'] if record['author'] else ''}")
        print(f"{record['url']}")
        print(f"retrieved {record['retrieved_at']}, {record['record']['method']}, sha256 {record['record']['sha256'][:12]}, saved at {record['record']['path']}")
        print(f"source type {record['source_type']}, reliability {record['reliability']}, reading {record['relation']}")
        ents = record["entities"]
        print("\n## Named")
        for label in ("organizations", "people", "people_as_written", "programs", "contracts",
                      "solicitations", "requirements", "locations"):
            if ents.get(label):
                shown = "people named in the text" if label == "people_as_written" else label
                print(f"- {shown}: {', '.join(str(e) for e in ents[label])}")
        if record["links"]["budget_lines"]:
            print(f"- budget lines: {', '.join(b['line_item'] + ' (' + b['program_as_written'] + ')' for b in record['links']['budget_lines'])}")
        print(f"\n## Claims, each on the passage it rests on"
              + (f" ({record['background_sentences']} further sentence(s) name the agency without stating anything)"
                 if record.get("background_sentences") else ""))
        for claim in record["claims"]:
            print(f"- {claim['statement_type']} [{claim['relation']}, confidence {claim['confidence']}]"
                  f"{' stated ' + claim['stated_on'] if claim['stated_on'] else ''}: {claim['relation_note']}")
            print(f"  \"{claim['passage']}\"")
            for item in claim["verify"]:
                print(f"  to verify: {item}")
    return 0


def feed_items(body: bytes, base_url: str) -> list[dict]:
    """Items of an RSS or Atom feed; for an index page, the article links on it."""
    def index_links() -> list[dict]:
        out: list[dict] = []
        for link in re.findall(rb'href="([^"]+)"', body):
            url = urllib.parse.urljoin(base_url, link.decode("utf-8", "replace"))
            if NEWS_URL_RE.search(url) and url not in [o["url"] for o in out]:
                out.append({"url": url, "title": "", "published": ""})
        return out

    # A page of links is well-formed enough to parse as XML and still carry no items, so the
    # shape is read off the document, not off whether the parser accepted it.
    if not re.search(rb"<(rss|feed|channel)\b", body[:4000], re.I):
        return index_links()
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return index_links()
    out = []
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {child.tag.split("}")[-1]: child for child in item}
        link = fields.get("link")
        url = (link.text or link.get("href") or "") if link is not None else ""
        stamp = ""
        # An element with no children is falsy, so every lookup here is an explicit None check.
        for name in ("pubDate", "published", "updated", "date"):
            node = fields.get(name)
            if node is not None and node.text and as_date(node.text):
                stamp = as_date(node.text)
                break
        title = fields.get("title")
        out.append({"url": urllib.parse.urljoin(base_url, url.strip()),
                    "title": (title.text or "").strip() if title is not None else "",
                    "published": stamp})
    return out


# Words that make a headline this agency's: the names the memory already knows, and the commands
# and roles the Navy writes around them.
STANDING_TERMS = P["news"]["standing_terms"]


def relevance() -> re.Pattern:
    """A headline names this agency when it carries a name the memory holds, or a standing term."""
    terms = list(STANDING_TERMS)
    seed_path = MEMORY / "organization_seed.json"
    if seed_path.exists():
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        for node in seed["nodes"]:
            if node["type"] in ("person", "agency"):
                continue
            terms += [node["name"]] + [a["text"] for a in node.get("aliases", [])]
    return re.compile("|".join(sorted({re.escape(t) for t in terms if len(t) >= 4}, key=len, reverse=True)), re.I)


def watch(argv: list[str]) -> int:
    """Poll the feeds and index pages, and report the items not already saved.

    This is the whole of the watching half: the feed states what it has, the manifest states what
    was taken, and the difference is what is new. A hosted watcher would add a subscription and a
    second place for the record to live.
    """
    parser = argparse.ArgumentParser(prog="news watch")
    parser.add_argument("--fetch", action="store_true", help="retrieve the new items and record them")
    parser.add_argument("--limit", type=int, default=5, help="how many new items to retrieve per feed")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fetch import fetch

    rows = manifest_rows()
    seen = {origin_url(r) for r in rows} | {r.get("url") for r in rows}
    names_agency = relevance()
    new_total = 0
    for feed in FEEDS:
        row = fetch(feed["url"], "direct", None, f"news watch{NOTE_TAG}: {feed['publisher']}")
        with MANIFEST.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        if not kept_page(row):
            print(f"{feed['publisher']}: {row.get('error') or row.get('status')} "
                  f"(a .mil host that blocks this address needs research/tools/browserbase_fetch.py)")
            continue
        body = (ROOT / row["path"]).read_bytes()
        items = feed_items(body, feed["url"])
        if not items and host_of(feed["url"]).endswith(OFFICIAL_HOSTS):
            # A .mil front end answers 200 with an empty body for a blocked address rather than
            # refusing, so an empty feed here is a geography problem, not an empty week.
            print(f"{feed['publisher']}: 200 with {len(body)} byte(s) and no items; the host answers this "
                  f"address with nothing, so this feed needs research/tools/browserbase_fetch.py")
            continue
        fresh = [i for i in items if i["url"] not in seen]
        # A general feed carries the whole department. What names this agency is listed; the rest
        # is counted, the way a notice posted by another command is.
        named = [i for i in fresh if names_agency.search(i["title"] or i["url"])]
        print(f"{feed['publisher']}: {len(items)} item(s), {len(fresh)} not saved yet, "
              f"{len(named)} naming this agency, {len(fresh) - len(named)} counted and not listed")
        for item in named[:args.limit if args.fetch else len(named)]:
            print(f"  {item['published'] or '          '}  {item['url']}")
            if args.fetch:
                got = fetch(item["url"], "direct", None, f"news watch item{NOTE_TAG}: {feed['publisher']} {item['title'][:60]}")
                with MANIFEST.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(got, sort_keys=True) + "\n")
                print(f"    {got.get('status')} {got.get('error', '')} {got.get('path', '')}")
        new_total += len(named)
    print(f"{new_total} item(s) naming this agency are not yet in the manifest"
          + ("" if args.fetch else "; rerun with --fetch to retrieve them, then `news.py build`"))
    return 0


def retrieve(url: str, note: str) -> dict:
    """Take one page and record the retrieval, the way every other document here is taken.

    A .mil front end refuses this address or answers it with almost nothing, so an official page that comes back
    that way is taken again through context.dev's United States address."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from browserbase_fetch import FILE_LINK_RE
    from context_fetch import take
    from fetch import fetch as fetch_one
    from llm import env_value

    row = fetch_one(url, "direct", None, note)
    with MANIFEST.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    blocked = not kept_page(row) or (row.get("size") or 0) < 2000
    if blocked and host_of(url).endswith(OFFICIAL_HOSTS) and not FILE_LINK_RE.search(url) and env_value("CONTEXT_DEV_API_KEY"):
        row, _ = take(url, env_value("CONTEXT_DEV_API_KEY"))
    return row


def record_answer(url: str, query: str, body: bytes) -> None:
    """Save a search answer and record it, so a statement of what was searched has its bytes."""
    import hashlib

    digest = hashlib.sha256(body).hexdigest()
    RAW_NEWS.mkdir(parents=True, exist_ok=True)
    path = RAW_NEWS / f"search_{digest[:12]}.json"
    path.write_bytes(body)
    with MANIFEST.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"url": url, "method": "direct", "status": 200, "retrieved_at": now(),
                                 "note": f"search{NOTE_TAG}: {query}", "mime": "application/json", "size": len(body),
                                 "sha256": digest, "path": str(path.relative_to(ROOT))}, sort_keys=True) + "\n")


from routergrowth import post_json  # noqa: E402  (the POST with retries; one copy for every provider)

# RouterGrowth routes one query to whichever provider is live behind a capability. `news.search`
# is the Google News one; `web.search` is the semantic one, which routes to Exa among others.
# The client is research/tools/routergrowth.py, shared with the vendor hiring sweep (vendor_jobs.py).
RG_BASE = "https://api.routergrowth.com"
RG_CAPABILITY = os.environ.get("ROUTERGROWTH_CAPABILITY", "news.search")


def routergrowth_search(query: str, days: int, results: int) -> list[dict]:
    """The same discovery through RouterGrowth, with the capability's own schema read first.

    The input field names come from `/v1/inspect` rather than from a copy of the documentation, so a provider
    swap behind the capability does not silently drop a parameter. The key is read from the environment or
    `.env` (llm.env_value); a missing key raises LookupError, which `sweep` reads as "drop this provider".
    """
    import routergrowth

    answer = routergrowth.search(RG_CAPABILITY, query, RAW_NEWS, "news", results_wanted=results, days=days)
    return [{"url": r["url"], "title": r["title"], "publishedDate": r["publishedDate"]} for r in answer["results"]]


def rg_fields(described: dict) -> dict:
    """The capability's input fields, wherever the inspect answer keeps its schema. `capability` is the
    capability's name (a string) in the live answer, so only mappings are read."""
    fields = {}
    for holder in (described, described.get("capability"), described.get("input")):
        if isinstance(holder, dict):
            fields.update((holder.get("input_schema") or holder.get("schema") or {}).get("properties") or {})
    return fields


def rg_results(answer) -> list[dict]:
    """Read a run answer as the same shape the rest of this module uses: url, title, date."""
    import routergrowth

    return [{"url": r["url"], "title": r["title"], "publishedDate": r["publishedDate"]} for r in routergrowth.results(answer)]


def exa_search(query: str, days: int, results: int) -> list[dict]:
    """One query to Exa. The answer's bytes are saved and recorded before anything is read from them."""
    key = os.environ.get("EXA_API_KEY", "")
    if not key:
        raise LookupError("EXA_API_KEY is not set")
    start = date.fromordinal(date.today().toordinal() - days).isoformat()
    payload = json.dumps({"query": query, "numResults": results, "type": "auto",
                          "startPublishedDate": start + "T00:00:00.000Z",
                          "contents": {"text": False}})
    body = post_json("https://api.exa.ai/search", json.loads(payload),
                     {"x-api-key": key, "Content-Type": "application/json"})
    record_answer("https://api.exa.ai/search", query, body)
    return json.loads(body).get("results") or []


GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


def keyword_query(query: str) -> str:
    """A sweep query as a keyword engine reads it: the office as a phrase, the command as a word.

    Keyword engines (GDELT, RouterGrowth's news search) AND every word they are given, so the sentence tail
    Exa reads as intent is dropped.
    """
    words = re.findall(r"\w[\w.&/-]*", query.split(" contract award", 1)[0])
    # ponytail: the command is the last word while its short label is one word (NAVWAR); pass the seed's context in when it is not
    return f'"{words[0]}"' if len(words) == 1 else f'"{" ".join(words[:-1])}" {words[-1]}'


def gdelt_query(query: str) -> str:
    """A sweep query as GDELT reads it: the keyword form, US English sources only."""
    return keyword_query(query) + " sourcelang:english sourcecountry:unitedstates"


def gdelt_search(query: str, days: int, results: int) -> list[dict]:
    """One query to GDELT DOC 2.0, newest first, saved and recorded like an Exa answer. No key.

    The page is GDELT's 250 whatever `results` asks; how many are taken is still `offer`'s limit.
    The DOC API reaches back three months, so a longer window is cut to that.
    """
    url = GDELT + "?" + urllib.parse.urlencode({"query": gdelt_query(query), "mode": "artlist", "format": "json",
                                                "maxrecords": 250, "sort": "datedesc", "timespan": f"{min(days, 90)}d"})
    for wait in (6, 30, 90):  # GDELT asks for one request every five seconds; from a busy address it wants longer
        time.sleep(wait)
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                body = response.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or wait == 90:
                raise
    record_answer(url, query, body)
    return gdelt_results(body)


def gdelt_results(body: bytes) -> list[dict]:
    """An artlist answer in the one shape, once per URL, US English only. A query GDELT cannot read is
    answered with a line of text rather than JSON, and that line is the error."""
    try:
        answer = json.loads(body.decode("utf-8", "replace"), strict=False)  # a title can carry a raw control character
    except ValueError:
        raise ValueError("GDELT answered: " + body[:200].decode("utf-8", "replace").strip()) from None
    read, urls = [], set()
    for item in answer.get("articles") or []:
        url = item.get("url") or ""
        if (not url or url in urls or item.get("language", "English") != "English"
                or item.get("sourcecountry", "United States") != "United States"):
            continue
        urls.add(url)
        seen = item.get("seendate") or ""  # when GDELT saw it (20260511T101500Z); build reads the article's own date
        read.append({"url": url, "title": item.get("title") or "",
                     "publishedDate": f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else ""})
    return read


# Discovery is one query in and a list of articles out, so the provider is a choice, not a rewrite.
PROVIDERS = {"exa": exa_search, "routergrowth": routergrowth_search, "gdelt": gdelt_search}
KEY_OF = {"exa": "EXA_API_KEY", "routergrowth": "ROUTERGROWTH_API_KEY", "gdelt": ""}  # GDELT needs no key


def saved_urls() -> set[str]:
    rows = manifest_rows()
    return {origin_url(r) for r in rows} | {r.get("url") for r in rows}


def offer(results: list[dict], seen: set[str], take: bool, limit: int, note: str) -> int:
    """List what a query returned and, when asked, take the pages not already saved."""
    taken = 0
    skipped = 0
    for result in results:
        url = result.get("url", "")
        if any(h in host_of(url) for h in NOT_ARTICLE_HOSTS):
            skipped += 1
            continue
        publisher, source_type = publisher_of(url)
        published = as_date(str(result.get("publishedDate") or "")) or " " * 10
        print(f"  {published}  [{source_type}] {publisher}  {(result.get('title') or '')[:70]}"
              + ("  (saved)" if url in seen else ""))
        print(f"      {url}")
        if not take or url in seen or taken >= limit:
            continue
        row = retrieve(url, f"{note}: {(result.get('title') or '')[:60]}")
        seen.add(url)
        taken += 1
        body = (ROOT / row["path"]).read_bytes() if row.get("path") else b""
        if not kept_page(row) or not body:
            print(f"      {row.get('status')} {row.get('error', '')}")
        elif len(body) < 2000 and host_of(url).endswith(OFFICIAL_HOSTS):
            # A .mil front end answers a blocked address with 200 and almost nothing rather than
            # refusing, so a short body from one of these hosts is a geography problem.
            print(f"      200 with {len(body)} byte(s): this host answered this address and context.dev with nothing")
        else:
            print(f"      {row.get('status')}  {len(body)} bytes  {row.get('path')}")
    if skipped:
        print(f"  {skipped} result(s) skipped: a social post or a contracting site, not an article")
    return taken


def sweep_queries(seed: dict) -> list[str]:
    """One query per office the memory holds, written in the agency's own words.

    The list is not hand-kept: it is the organization memory read back out, so the same command
    covers another agency as soon as that agency's seed exists.
    """
    # The command is the name a headline uses: the office's own, found up its child_of chain; the first command, else
    # the department, when the memory states no chain.
    nodes = {n["id"]: n for n in seed["nodes"]}
    parent = {r["from"]: r["to"] for r in seed.get("relationships", []) if r.get("type") == "child_of"}
    first = next((short_label(n) for wanted in ("command", "agency") for n in seed["nodes"] if n["type"] == wanted), "")

    def context_of(nid: str) -> str:
        seen = set()
        while nid in parent and nid not in seen:
            seen.add(nid)
            nid = parent[nid]
            if nodes.get(nid, {}).get("type") in ("command", "agency"):
                return short_label(nodes[nid])
        return first
    queries = []
    for node in seed["nodes"]:
        if node["type"] in ("person", "agency"):
            continue
        label, context = short_label(node), context_of(node["id"])
        if label == context:
            queries.append(f"{label} contract award, reorganization, leadership change or industry day")
        else:
            queries.append(f"{label} {context} contract award, program news, leadership change or industry day")
    return sorted(dict.fromkeys(queries))


def short_label(node: dict) -> str:
    """The name a newsroom would print: the shortest usable alias, else the full name."""
    usable = [a["text"] for a in node.get("aliases", [])
              if 4 <= len(a["text"]) <= 24 and "(" not in a["text"] and "." not in a["text"]]
    return min(usable, key=len) if usable else node["name"]


def search(argv: list[str]) -> int:
    """Discovery through Exa: find articles the feeds do not carry, and record what was searched."""
    parser = argparse.ArgumentParser(prog="news search")
    parser.add_argument("query")
    parser.add_argument("--days", type=int, default=180, help="only results published in the last N days")
    parser.add_argument("--results", type=int, default=10)
    parser.add_argument("--fetch", action="store_true", help="retrieve the results not already saved")
    parser.add_argument("--limit", type=int, default=5, help="how many pages to retrieve")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="exa")
    args = parser.parse_args(argv)
    try:
        results = PROVIDERS[args.provider](args.query, args.days, args.results)
    except LookupError:
        print(f"{KEY_OF[args.provider]} is not set. The feeds in `news.py watch` need no key; discovery by "
              "query does.\nSet it (see .env.example) and rerun.", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the error is reported, not swallowed
        print(f"{args.provider} search failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    seen = saved_urls()
    print(f"{len(results)} result(s)")
    taken = offer(results, seen, args.fetch, args.limit, f"news search{NOTE_TAG}: {args.query[:40]}")
    print(f"{taken} page(s) retrieved" if args.fetch
          else "retrieve them with --fetch, or one by one with research/tools/fetch.py, then `news.py build`")
    return 0


def sweep(argv: list[str]) -> int:
    """Run the whole query list, one per office, and take what is new."""
    parser = argparse.ArgumentParser(prog="news sweep")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--results", type=int, default=6, help="results per query")
    parser.add_argument("--fetch", action="store_true", help="retrieve the results not already saved")
    parser.add_argument("--limit", type=int, default=2, help="how many pages to retrieve per query")
    parser.add_argument("--queries", type=int, default=0, help="stop after this many queries (0: all)")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="exa")
    parser.add_argument("--no-gdelt", action="store_true", help="skip the GDELT pass beside a keyed provider (a second provider's run)")
    args = parser.parse_args(argv)
    seed = json.loads((MEMORY / "organization_seed.json").read_text(encoding="utf-8"))
    queries = sweep_queries(seed)
    if args.queries:
        queries = queries[:args.queries]
    seen = saved_urls()
    taken = 0
    # GDELT needs no key, so it runs beside the chosen provider and alone when that provider's key is not set.
    providers = [args.provider] if args.no_gdelt else list(dict.fromkeys([args.provider, "gdelt"]))
    for query in queries:
        print(f"\n{query}")
        for provider in list(providers):
            try:
                results = PROVIDERS[provider](query, args.days, args.results)
            except LookupError:
                print(f"{KEY_OF[provider]} is not set; this sweep runs without {provider}.", file=sys.stderr)
                providers.remove(provider)
                continue
            except Exception as exc:  # noqa: BLE001 - the failed query is reported, the sweep goes on
                print(f"  {provider} failed: {type(exc).__name__}: {exc}")
                continue
            taken += offer(results, seen, args.fetch, args.limit, ("news sweep (gdelt)" if provider == "gdelt" else "news sweep") + NOTE_TAG)
        time.sleep(1)  # the service throttles a burst of queries
    print(f"\n{len(queries)} quer(ies), {taken} page(s) retrieved"
          + ("; now `news.py build`" if taken else "; nothing new"))
    return 0


# ---------------------------------------------------------------- selfcheck

SAMPLE = b"""<html><head><title>Navy stands up new portfolio</title>
<meta property="article:published_time" content="2026-05-11T10:00:00Z">
<meta name="author" content="Fleet Public Affairs">
<script type="application/ld+json">{"@type":"NewsArticle","headline":"Navy stands up new portfolio","datePublished":"2026-05-11"}</script>
</head><body><nav>menu</nav>
<p>The Department of the Navy announced that the Tactical Data Link (TDL) Program Office reports to
the Program Executive Office Command, Control, Communications, Computers and Intelligence (PEO C4I)
for the remainder of the fiscal year.</p>
<p>The office in San Diego expects to award a follow-on contract to N0003916C0087 later this year.</p>
<p>Short line.</p></body></html>"""


def selfcheck() -> int:
    assert origin_url({"final_url": "https://web.archive.org/web/20260901003800id_/https://www.navy.mil/a"}) == "https://www.navy.mil/a"
    assert publisher_of("https://www.navy.mil/x") == ("U.S. Navy", "official announcement")
    assert publisher_of("https://breakingdefense.com/x") == ("Breaking Defense", "trade reporting")
    assert publisher_of("https://example.com/x") == ("example.com", "secondary reporting")
    assert publisher_of("https://www.niwcpacific.navy.mil/x")[1] == "official announcement"
    assert as_date("May 11, 2026") == "2026-05-11" and as_date("2026-05-11T10:00:00Z") == "2026-05-11"
    assert as_date("no date here") == "", "a date is read or it is absent; never guessed"

    reader = _Reader()
    reader.feed(SAMPLE.decode())
    text = html_to_markdown(SAMPLE.decode())
    assert "menu" not in text, "navigation is not the article"
    assert text.startswith("The Department of the Navy announced") and "\n\nThe office in San Diego" in text, "the rendering keeps the page's paragraphs"
    assert reader.meta["article:published_time"].startswith("2026-05-11")
    assert linked_data(reader)["headline"] == "Navy stands up new portfolio"
    assert published_of(reader, linked_data(reader), text) == "2026-05-11"
    assert dated("Posted: 2026-09-03. This job is no longer available.", "2026-09-24") == ("2026-09-03", "the page prints Posted 2026-09-03; its metadata says 2026-09-24")
    assert dated("Eisensmith will retire by the end of April after five years.", "2026-07-23")[1].startswith("the text looks ahead to April")
    assert dated("She will retire in April 2027.", "2026-07-23") == ("2026-07-23", "") and dated("It will close in December.", "2026-07-23")[1] == ""
    assert author_of(reader, {}, text) == "Fleet Public Affairs"
    assert len(sentences(text)) == 2, "a fragment shorter than a claim is not a sentence"
    marked = "## NAVWAR awards the follow-on contract to the incumbent for another five years.\n- The office in San Diego expects to award a follow-on contract later this year."
    assert sentences(marked) == [s.split(" ", 1)[1] for s in marked.split("\n")], "a block marker is the rendering's, not part of the passage"

    # The statement vocabulary reads the sentence, and a hedged or undated one is worth less.
    assert statement_type_of("PMW 160 reports to PEO C4I.", {"organizations": ["pmw:160"], "people": []}) == "parentage"
    assert statement_type_of("Captain Smith was named program manager.", {"organizations": [], "people": ["person:smith"]}) == "leadership"
    assert statement_type_of("GAO sustained the protest.", {"organizations": ["pmw:160"], "people": []}) == "protest"
    assert confidence_of("official announcement", "The office was realigned on 11 May 2026.", "parentage", True)[0] == "high"
    assert confidence_of("official announcement", "The office expects to realign.", "parentage", True)[0] == "medium"
    level, verify = confidence_of("trade reporting", "The office was realigned.", "parentage", False)
    assert level == "low" and "no date is stated for the change" in verify and "a second source, or the agency's own announcement" in verify

    # A claim is checked against what the memory holds, and disagreement is reported, not resolved.
    stub = {"parents": {"pmw:530": {"peo:c4i"}}, "leads": {"pmw:740": {"person:a"}},
            "known_contracts": {"N0003916C0087"}, "known_sols": set()}
    ents = {"organizations": ["pmw:530", "peo:c4i"], "people": [], "contracts": [], "solicitations": [], "requirements": []}
    assert relate("parentage", ents, stub)[0] == "corroborates"
    assert relate("parentage", {**ents, "organizations": ["pmw:530", "pae:mission-systems"]}, stub)[0] == "conflicts"
    assert relate("parentage", {**ents, "organizations": ["pmw:999", "peo:c4i"]}, stub)[0] == "new signal"
    assert relate("leadership", {**ents, "organizations": ["pmw:740"], "people": ["person:b"]}, stub)[0] == "conflicts"
    assert relate("performance", {**ents, "organizations": [], "contracts": ["N0003916C0087"]}, stub)[0] == "corroborates"

    feed = b"""<rss><channel><item><title>A</title><link>https://www.navy.mil/a</link><pubDate>Mon, 11 May 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
    items = feed_items(feed, "https://www.navy.mil/")
    assert items == [{"url": "https://www.navy.mil/a", "title": "A", "published": "2026-05-11"}], items
    index = b'<html><a href="/News/Article/1/peo-c4i-news">x</a><a href="/about">y</a></html>'
    assert [i["url"] for i in feed_items(index, "https://www.navwar.navy.mil/")] == ["https://www.navwar.navy.mil/News/Article/1/peo-c4i-news"]

    names_agency = relevance()
    assert names_agency.search("NAVWAR awards contract") and names_agency.search("PEO C4I welcomes new program managers")
    assert not names_agency.search("Misawa Air Fest 2026 celebrates 46 years of community"), "a general feed carries the whole department"

    assert is_news({"status": 200, "mime": "text/html", "path": "p", "final_url": "https://www.navy.mil/Press-Office/x", "note": ""})
    assert not is_news({"status": 200, "mime": "text/html", "path": "p", "final_url": "https://sam.gov/opp/1/view", "note": ""})
    assert not is_news({"status": 403, "mime": "text/html", "path": "p", "final_url": "https://www.navy.mil/news/x", "note": ""})

    # End to end on the saved memory: the offices resolve through the alias table, the known
    # contract corroborates, and every claim carries the sentence it came from.
    mem = memory()
    record = article_record({"path": "", "sha256": "a" * 64, "method": "direct", "retrieved_at": now(),
                             "final_url": "https://www.navy.mil/Press-Office/Press-Releases/x", "note": ""}, SAMPLE, mem)
    assert record["publisher"] == "U.S. Navy" and record["source_type"] == "official announcement"
    assert record["published"] == "2026-05-11" and record["id"] == "news:" + "a" * 12
    assert "peo:c4i" in record["entities"]["organizations"], record["entities"]
    assert "San Diego" in record["entities"]["locations"]
    assert "N0003916C0087" in record["entities"]["contracts"]
    assert any(c["statement_type"] == "parentage" for c in record["claims"]), record["claims"]
    assert all(c["passage"] in SAMPLE.decode().replace("\n", " ") or c["passage"] in " ".join(SAMPLE.decode().split())
               for c in record["claims"]), "a claim quotes the article, it does not paraphrase it"
    assert record["relation"] in ("new signal", "corroborates", "conflicts")
    assert budget_lines().get("2237", "").startswith("SURTASS"), "the budget books' P-1 lines are the budget link"
    # The query list is the memory read back out: one query per office, named as a newsroom names it.
    seed = {"nodes": [{"id": "a", "name": "Department of the Navy", "type": "agency"},
                      {"id": "c", "name": "Naval Information Warfare Systems Command", "type": "command",
                       "aliases": [{"text": "NAVWAR"}, {"text": "navwar.navy.mil"}]},
                      {"id": "o", "name": "PMW 160 Tactical Networks Program Office", "type": "program_office",
                       "aliases": [{"text": "PMW-160"}]},
                      {"id": "p", "name": "A person", "type": "person"}]}
    queries = sweep_queries(seed)
    assert len(queries) == 2, queries
    assert any(q.startswith("PMW-160 NAVWAR ") for q in queries), queries
    assert not any("person" in q.lower() for q in queries), "a person is not an office to search for"
    seed["nodes"].append({"id": "s", "name": "Naval Sea Systems Command", "type": "command", "aliases": [{"text": "NAVSEA"}]})
    seed["nodes"].append({"id": "o2", "name": "PMS 485 Maritime Surveillance Program Office", "type": "program_office"})
    seed["relationships"] = [{"type": "child_of", "from": "o2", "to": "s"}, {"type": "child_of", "from": "s", "to": "a"}]
    assert any(q.startswith("PMS 485 Maritime Surveillance Program Office NAVSEA ") for q in sweep_queries(seed)), "an office's own command"
    assert short_label({"name": "Program Executive Office Command, Control", "aliases":
                        [{"text": "PEO C4I"}, {"text": "C4IEXEC (LRAE front-office code)"}]}) == "PEO C4I"
    # Either provider's answer is read into the one shape: a url, a headline and a date.
    rg = rg_results({"output": {"articles": [{"link": "https://breakingdefense.com/a", "headline": "H",
                                              "published_at": "2026-05-11"}, {"no_url": True}]}})
    assert rg == [{"url": "https://breakingdefense.com/a", "title": "H", "publishedDate": "2026-05-11"}], rg
    assert rg_results({"output": [{"url": "https://a.com/x", "title": "T", "date": "2026-01-02"}]}) == \
        [{"url": "https://a.com/x", "title": "T", "publishedDate": "2026-01-02"}], "a list under output is the result list"
    # the live run answer: articles under result; no_match is an empty search
    assert [r["url"] for r in rg_results({"status": "succeeded", "done": True, "result": {"query": "q", "articles": [
        {"title": "T", "url": "https://itif.org/a", "source": "itif.org"}]}})] == ["https://itif.org/a"]
    assert rg_results({"status": "no_match", "done": True, "error": {"code": "no_match", "message": "No result matched the input."}}) == []
    try:
        rg_results({"output": {"status": "ok"}}); raise AssertionError("an unknown answer shape read as zero results")
    except ValueError:
        pass
    assert set(rg_fields({"capability": "news.search", "input_schema": {"properties": {"query": {}, "limit": {}}}})) == {"query", "limit"}, \
        "the live inspect answer names the capability as a string beside its input_schema"
    assert set(PROVIDERS) == set(KEY_OF)
    # GDELT's artlist joins the same path: the one shape, each URL once, US English sources only, and a
    # query it cannot read raised as the line of text it answers with.
    assert sorted(gdelt_query(q) for q in queries) == [
        '"NAVWAR" sourcelang:english sourcecountry:unitedstates',
        '"PMW-160" NAVWAR sourcelang:english sourcecountry:unitedstates'], [gdelt_query(q) for q in queries]
    assert gdelt_query("CBRD Division (W1) NAVWAR contract award, program news") \
        == '"CBRD Division W1" NAVWAR sourcelang:english sourcecountry:unitedstates'
    answer = json.dumps({"articles": [
        {"url": "https://breakingdefense.com/a", "title": "H", "seendate": "20260511T101500Z", "domain": "breakingdefense.com",
         "language": "English", "sourcecountry": "United States"},
        {"url": "https://breakingdefense.com/a", "title": "H again", "seendate": "20260511T111500Z",
         "language": "English", "sourcecountry": "United States"},
        {"url": "https://www.ukdefencejournal.org.uk/b", "title": "B", "seendate": "20260510T000000Z",
         "language": "English", "sourcecountry": "United Kingdom"},
        {"url": "https://www.navy.mil/c", "title": "C", "seendate": "20260509T000000Z",
         "language": "English", "sourcecountry": "United States"}]}).encode()
    gd = gdelt_results(answer)
    assert gd == [{"url": "https://breakingdefense.com/a", "title": "H", "publishedDate": "2026-05-11"},
                  {"url": "https://www.navy.mil/c", "title": "C", "publishedDate": "2026-05-09"}], gd
    assert gdelt_results(b"{}") == [], "no match is an empty object"
    try:
        gdelt_results(b"The specified phrase is too short.")
        raise AssertionError("a text answer is an error, not an empty result")
    except ValueError as exc:
        assert "phrase is too short" in str(exc)
    # What is already saved is listed, not taken again, whichever provider found it.
    with contextlib.redirect_stdout(io.StringIO()):
        assert offer(gd, {r["url"] for r in gd}, True, 5, "news sweep (gdelt)") == 0
    assert is_news({"status": 200, "mime": "text/html", "path": "x", "url": "https://example.com/a",
                    "note": "news sweep (gdelt): H"}), "a GDELT-found page is modelled like an Exa-found one"
    taken = {"status": 200, "mime": "text/html", "path": "x", "url": "https://example.com/a",
             "note": "news sweep: something"}
    assert is_news(taken), "a page a news search returned is read whatever the host"
    assert not is_news({**taken, "url": "https://www.cleat.ai/government/contracts/x"}), \
        "a site that republishes notices is not an article"
    assert publisher_of("https://example.com/a") == ("example.com", "secondary reporting")
    # A preposition is not a place in an org chart, and a denial is not a claim of what it denies.
    mem_stub = {"tracer": type("T", (), {"resolve_offices": staticmethod(
        lambda text: ["peo-c4i"] if "PEO C4I" in text else [])})(),
        "parents": {"peo-c4i": {"navwar"}}, "leads": {}}
    ents_two = {"organizations": ["peo-c4i", "navwar"], "people": []}
    assert statement_type_of("The event was held under the theme of NAVWAR Next, with speakers.",
                             ents_two, mem_stub) != "parentage"
    assert statement_type_of("The Tactical Data Link office reports to the PEO C4I organization.",
                             ents_two, mem_stub) == "parentage"
    assert statement_type_of("The Navy has not announced changes to PEO C4I as part of the reorganization.",
                             ents_two, mem_stub) == "no change stated"
    assert relate("no change stated", ents_two, {**mem_stub, "known_contracts": set(), "known_sols": set()})[0] \
        == "corroborates"
    empty = {"people": [], "requirements": [], "budget_lines": [], "contracts": [], "solicitations": []}
    assert not asserts_something("existence", empty, {"known_contracts": set(), "known_sols": set()}), \
        "a sentence that only names the agency is background, not a claim"
    assert asserts_something("existence", {**empty, "contracts": ["N0003916C0087"]},
                             {"known_contracts": {"N0003916C0087"}, "known_sols": set()})
    assert asserts_something("recompete", empty, {"known_contracts": set(), "known_sols": set()})
    assert named_people("Acting Secretary of the Navy Hung Cao today announced the establishment.") == ["Hung Cao"]
    assert named_people("Christopher Miller will serve as the acting manager.") == ["Christopher Miller"]
    assert named_people("The Department of the Navy announced a new office.") == []
    assert statement_type_of("The Department established the new portfolio manager this month.",
                             {"organizations": ["a"], "people": []}, None) == "consolidation"
    assert statement_type_of("As part of the realignment, the existing PEO EIS programs will transition to PEO Digital and PEO MLB.",
                             {"organizations": ["a"], "people": []}, None) == "consolidation", "programs moving to an office"
    assert statement_type_of("The effort will transition to production in the fourth quarter of the year.",
                             {"organizations": ["a"], "people": []}, None) != "consolidation"
    assert statement_type_of("PEO EIS was disestablished to make way for two new PEOs.",
                             {"organizations": ["a"], "people": []}, None) == "consolidation", "the subject is the office"
    for loose in ("This new form establishes a clear, structured pathway to the PEO C4I program offices.",
                  "She served in PMW-160 as the Consolidated Afloat Network and Enterprise Services APM.",
                  'Please see "Navy Establishes Two New IT Delivery Offices" for more information.'):
        assert statement_type_of(loose, {"organizations": ["a"], "people": []}, None) != "consolidation", loose
    stand_up = {"organizations": ["drpm:ras"], "people": []}
    held = {"node_ids": {"drpm:ras"}, "parents": {}, "leads": {}, "known_contracts": set(), "known_sols": set()}
    assert relate("consolidation", stand_up, held)[0] == "corroborates", "the memory holds the office being announced"
    assert relate("consolidation", {"organizations": ["peo:new"], "people": []},
                  {**held, "node_ids": set()})[0] == "new signal"
    changes = leadership_changes(
        "U.S. Navy Capt. Raphael R. Castillejo relieved Mr. Baron Jolie as the program manager for the U.S. Navy’s Naval Command and "
        "Control Systems Program Office (PMW 150) during a ceremony on the Naval Information Warfare Systems Command (NAVWAR) campus. "
        "The ceremony also saw Mr. Eric Andalis relieve Capt. Castillejo as the program manager for the U.S. Navy’s Ship Integration "
        "Program Office (PMW 760). Photo By Lt.Cmdr. Janice Leister | U.S. Navy Capt. Raphael R. Castillejo relieved Mr. Baron Jolie as the "
        "program manager...... read more White, formerly the combat systems officer, was introduced as the new program manager for PMW 770. "
        "PAE Mission Systems: Mr. Jim Day PAE Munitions: Mr. Paul Mann PAE Undersea: Portfolio Acquisition Executive Undersea")
    assert [(c["name"], c["role_as_written"], c["office"], c["relieved"]) for c in changes] == [
        ("Capt. Raphael R. Castillejo", "program manager", "pmw:150", "Mr. Baron Jolie"),
        ("Mr. Eric Andalis", "program manager", "pmw:760", "Capt. Castillejo"),
        ("Mr. Jim Day", "portfolio acquisition executive", "pae:mission-systems", ""),
        ("Mr. Paul Mann", "portfolio acquisition executive", "pae:munitions", "")], changes  # the memory names PAE Munitions since 2026-09-29
    assert office_named_in("for PMW 150 and later PMW 760") == "", "two offices in one clause place nobody"
    at_block_end = leadership_changes("Mr. Eric Andalis relieve Capt. Castillejo as the program manager for the U.S. Navy’s Ship "
                                      "Integration Program Office (PMW 760)\n\n## IMAGE INFO\n\n| Date Taken: | 08.19.2025 |")
    assert [(c["office"], c["passage"][-9:]) for c in at_block_end] == [("pmw:760", "(PMW 760)")], "a clause ends with its block"
    print("news selfcheck ok")
    return 0


COMMANDS = {"build": build, "read": show, "watch": watch, "search": search, "sweep": sweep}


def main(argv: list[str]) -> int:
    if "--selfcheck" in argv:
        return selfcheck()
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

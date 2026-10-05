"""What the department's leaders said, where, to whom: speeches, testimony and conference appearances.

    python research/tools/remarks.py watch [--fetch] [--pages N] [--limit N]   # navy.mil speeches + testimony, House hearings
    python research/tools/remarks.py discover [--fetch] [--days N] [--results N] # conference pages through Exa
    python research/tools/remarks.py extract [--limit N] [--verify K] [--check] # the agent reads each document
    python research/tools/remarks.py show
    python research/tools/remarks.py --selfcheck

A speech is not an article, it is a set of dated events (a capability named
as a priority, a strategy stated, industry asked for something); a conference is an event with a host,
dates and the government people who spoke; a hearing carries testimony that states priorities and
funding to Congress. Connectors: navy.mil's speech and testimony archives (the site refuses this address,
so the index pages and the documents come through Browserbase), the House committee repository feed for
Armed Services and Appropriations (direct, keyless: hearing page, witnesses, witness statement PDFs), and
Exa discovery for conference pages that name Navy officials (direct fetch, Browserbase on refusal).
The agent (llm.py, one structured call per document, cassettes) states the events; reader.py's rules
keep only what the saved text carries verbatim.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "research"
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from fetch import kept_page  # noqa: E402
from context_fetch import fetch as hosted  # noqa: E402
from llm import MODEL, env_value, structured  # noqa: E402
from markdown import html_to_markdown  # noqa: E402
from news import as_date, feed_items, host_of, manifest_rows, origin_url, post_json, record_answer  # noqa: E402
from oversight import take  # noqa: E402
from reader import authority_of, flatten, flatten_lines, lint as check, link, normalized, pdf_text, verbatim  # noqa: E402

MANIFEST = RESEARCH / "sources" / "documents_manifest.jsonl"
from agency import EVENTS as EVENTS_DIR, NOTE_TAG, P  # noqa: E402

EVENTS = EVENTS_DIR / "remarks_events.json"
DISCOVERED = EVENTS_DIR / "remarks_discovered.json"
# The agency's speech and testimony archives (None where it keeps none) and the pages that list its testimony as files.
SPEECHES = P["remarks"]["speeches"]
TESTIMONY = P["remarks"]["testimony"]
TESTIMONY_PAGES = P["remarks"]["testimony_pages"]
HOUSE_FEEDS = P["committees"]["house_feeds"]
NAVY_ARTICLE = re.compile(P["remarks"]["article_re"])  # an archive article of this agency; matches nothing where there is no archive
HOUSE_EVENT = re.compile(r"^https?://docs\.house\.gov/Committee/Calendar/ByEvent\.aspx\?EventID=(\d+)$")
NAMES_RE = re.compile(P["remarks"]["names_re"], re.I)
DATE_RE = re.compile(r"\b(\d{1,2} [A-Z][a-z]+ \d{4})\b")
NOTE, FILE_NOTE = f"remarks watch{NOTE_TAG}", f"remarks watch file{NOTE_TAG}"
CAP = 60_000
AGENCY = P["remarks"]["agency"]
EVENT_TYPES = ("capability_priority", "strategy_change", "industry_engagement", "conference_appearance", "congressional_directive",
               "funding_change", "program_delayed", "program_created", "program_cancelled")
VERBATIM = ("program", "organization", "person")
AUDIENCES = ("congress", "industry", "fleet", "public", "allies", "other")

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["speaker_name", "speaker_role", "event_name", "event_host", "audience", "events"],
    "properties": {
        "speaker_name": {"type": "string", "description": "who spoke or signed, exactly as the text writes the name; for several witnesses, the first named; empty when the text names nobody"},
        "speaker_role": {"type": "string", "description": "the speaker's title or position exactly as the text writes it; empty when absent"},
        "event_name": {"type": "string", "description": "the conference, hearing, ceremony or venue exactly as the text writes it; empty when absent"},
        "event_host": {"type": "string", "description": "the organization that hosts the event exactly as the text writes it (a committee, an association, a league); empty when absent"},
        "audience": {"type": "string", "enum": list(AUDIENCES)},
        "events": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["event_type", "statement", "capability", "program", "organization", "person", "evidence_span", "amounts", "confidence"],
            "properties": {
                "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
                "statement": {"type": "string", "description": "what is stated, one sentence of your own words"},
                "capability": {"type": "string", "description": "the capability at stake in two to five words of your own (autonomous systems, shipyard capacity); empty when none"},
                "program": {"type": "string", "description": "the program, system, ship class or platform exactly as the text writes it; empty when none"},
                "organization": {"type": "string", "description": "the command, office or activity the statement is about exactly as the text writes it; empty when none"},
                "person": {"type": "string", "description": "for conference_appearance only: the government official who appears, exactly as the text writes the name; else empty"},
                "evidence_span": {"type": "string", "description": "one contiguous passage copied verbatim from the text, 15 to 60 words, that states it"},
                "amounts": {"type": "array", "items": {"type": "string"}, "description": "every money figure in the evidence span, copied verbatim"},
                "confidence": {"type": "number"},
            }}},
    },
}
SYSTEM = (
    "You read one document in which a federal agency's leaders speak: a speech, a hearing transcript, a written witness "
    "statement to Congress, or a conference page that names government speakers. State what it says as events, one per "
    "distinct statement, never one you expect. Fields marked 'exactly as the text writes it' and the evidence span are "
    "copied verbatim, same words, casing and punctuation; a paraphrase there is wrong. The evidence span is one contiguous "
    "passage, never two joined with an ellipsis. Event types: capability_priority when a capability is stated as a need or a "
    "priority; strategy_change when a strategy, plan, posture or doctrine is stated as new or changed; industry_engagement "
    "when industry is asked for something or invited; conference_appearance when a named government official speaks or "
    "will speak at a named event (one event per official); congressional_directive when Congress is said to have directed, "
    "required or restricted something; funding_change when a funding increase, cut, request or shortfall is stated; "
    "program_delayed, program_created and program_cancelled when the text states that. Return no events for a document "
    "that states nothing about this agency's needs, plans, programs or appearances."
)


# ---------------------------------------------------------------- connectors

def strip_tags(fragment: str) -> str:
    return flatten(re.sub(r"<[^>]+>", " ", fragment))


def navy_index_rows(body: bytes, article_re: re.Pattern | None = None) -> list[dict]:
    """Every speech or testimony the agency's archive page lists: date, title, url; the pages it links. The archive is
    the .mil content system's (navy.mil, af.mil and the rest print one item block per article); an article is a link
    the profile's `article_re` matches."""
    text = body.decode("utf-8", "replace")
    out = []
    for chunk in re.split(r'class="[^"]*\bitem item-\d+"', text)[1:]:
        link = next((m for m in re.finditer(r'href="([^"#]+)"', chunk) if (article_re or NAVY_ARTICLE).match(m.group(1))), None)
        when = DATE_RE.search(chunk)
        title = re.search(r"<h\d[^>]*>(.*?)</h\d>", chunk, re.S) or re.search(r'title="([^"]+)"', chunk)
        if link:
            out.append({"url": link.group(1), "issued": as_date(when.group(1)) if when else "", "title": strip_tags(title.group(1)) if title else ""})
    return out


def archive_kind(url: str, speeches: str | None = None, testimony: str | None = None) -> str:
    """Speech or testimony, by the archive the article sits under (the profile's speeches or testimony address); the
    navy.mil paths say it in the address as well."""
    speeches, testimony = speeches or SPEECHES, testimony or TESTIMONY
    if speeches and url.startswith(speeches):
        return "speech"
    if testimony and url.startswith(testimony):
        return "testimony"
    return "speech" if "display-speech" in url else "testimony"


def navy_pages(body: bytes) -> int:
    pages = [int(p) for p in re.findall(r"\?Page=(\d+)", body.decode("utf-8", "replace"))]
    return max(pages) if pages else 1


def navy_article(body: bytes) -> dict:
    """A navy.mil speech or testimony page: title, the date it was presented (else published), the speaker and
    place the page states in its own fields, and the article's text without the site chrome."""
    text = body.decode("utf-8", "replace")
    title = re.search(r'<meta property="og:title" content="([^"]*)"', text)

    def field(label: str) -> str:
        m = re.search(rf"<h2>\s*{label}\s*</h2>\s*<p>(.*?)</p>", text, re.S | re.I)
        return strip_tags(m.group(1)) if m else ""

    presented, published = DATE_RE.search(field("Presented on")), DATE_RE.search(field("Date Published"))
    place = re.search(r'class="[^"]*press-release__dateline[^"]*"[^>]*>(.*?)</h\d>', text, re.S)
    marker = text.find('class="article-view"')
    start = text.rfind("<", 0, marker) if marker >= 0 else -1  # the element that carries the class, from its opening bracket
    body_html = text[start:] if start >= 0 else text
    body_html = re.split(r'<footer|class="footer', body_html)[0]
    # The article as Markdown (markdown.py drops script, style and nav itself); the page's own
    # fields above are read from the HTML, not from the rendering.
    return {"title": html.unescape(title.group(1)) if title else "", "issued": as_date((presented or published).group(1)) if (presented or published) else "",
            "speaker": field("Speech by") or field("Testimony by"), "place": strip_tags(place.group(1)) if place else "",
            "text": flatten_lines(html_to_markdown(body_html))}


def house_event(body: bytes) -> dict:
    """A House committee repository hearing page: title, committee, date, witnesses and the documents by label."""
    text = body.decode("utf-8", "replace")
    title = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    committee = re.search(r"<h1[^>]*>.*?</h1>\s*<p[^>]*>(.*?)</p>", text, re.S)
    when = re.search(r"\b([A-Z][a-z]+day, [A-Z][a-z]+ \d{1,2}, \d{4})", text)
    witnesses = [{"name": strip_tags(n).strip(), "position": strip_tags(p)} for n, p in
                 re.findall(r'<div class="witnessPanel">\s*<p><strong>(.*?)</strong><br><small[^>]*>(.*?)</small>', text, re.S)]
    documents = [{"label": strip_tags(label), "url": html.unescape(url).replace("http://", "https://")} for label, url in
                 re.findall(r'<li>(.*?)\[<a target="_blank" href="([^"]+\.pdf)">PDF</a>\]', text, re.S)]
    return {"title": strip_tags(title.group(1)) if title else "", "committee": strip_tags(committee.group(1)) if committee else "",
            "issued": as_date(when.group(1)) if when else "", "witnesses": witnesses, "documents": documents}


def statements(event: dict) -> list[dict]:
    """The written testimony among a hearing's documents: labelled a statement, or named Wstate in the file."""
    return [d for d in event["documents"] if "statement" in d["label"].lower() or "-Wstate-" in d["url"]]


def latest_saved(rows: list[dict], url: str) -> dict | None:
    for r in reversed(rows):
        if r.get("url") == url and kept_page(r) and (ROOT / r["path"]).exists():
            return r
    return None


def watch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="remarks watch")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--pages", type=int, default=1, help="navy.mil archive pages per index (10 items each)")
    parser.add_argument("--limit", type=int, default=10, help="new documents to retrieve per source")
    parser.add_argument("--since", default="2025-01-01")
    args = parser.parse_args(argv)
    rows = manifest_rows()
    seen = {origin_url(r) for r in rows} | {r.get("url") for r in rows}
    new_total = 0
    # navy.mil: the index pages first (context.dev), then the documents they list.
    indexes = [f"{base}?Page={n}" if n > 1 else base for base in (SPEECHES, TESTIMONY) if base for n in range(1, args.pages + 1)]
    if args.fetch:
        hosted(indexes)
        rows = manifest_rows()
    listed: list[dict] = []
    for url in indexes:
        row = latest_saved(rows, url)
        if row:
            listed += navy_index_rows((ROOT / row["path"]).read_bytes())
        else:
            print(f"navy.mil index not saved yet: {url}" + ("" if args.fetch else " (needs --fetch)"))
    fresh = sorted({r["url"]: r for r in listed if r["url"] not in seen and r["issued"] >= args.since}.values(), key=lambda r: r["issued"], reverse=True)
    if indexes:
        print(f"navy.mil archives: {len(listed)} document(s) listed, {len(fresh)} issued since {args.since} and not saved yet")
    for r in fresh[: args.limit]:
        print(f"  {r['issued']}  {r['title'][:90]}")
    if args.fetch and fresh:
        hosted([r["url"] for r in fresh[: args.limit]])
    new_total += len(fresh)
    # House committee repository: the feed (direct), hearings about this department, their statements.
    for code, feed_url in HOUSE_FEEDS.items():
        feed = take(feed_url, f"{NOTE}: House {code} feed")
        if not kept_page(feed):
            print(f"House {code} feed: {feed.get('error') or feed.get('status')}")
            continue
        # The feed links plain http; the redirect to https answers with the repository shell, not the hearing.
        items = [dict(i, url=i["url"].replace("http://", "https://", 1)) for i in feed_items((ROOT / feed["path"]).read_bytes(), feed_url) if NAMES_RE.search(i["title"])]
        # A hearing counts as saved only when its saved page carries the hearing: the repository sometimes
        # answers a request with a shell, and that capture must not hide the hearing from the next run.
        def hearing_saved(url: str) -> bool:
            row = latest_saved(rows, url)
            return bool(row and house_event((ROOT / row["path"]).read_bytes())["title"])
        hearings = [i for i in items if HOUSE_EVENT.match(i["url"]) and not hearing_saved(i["url"]) and (as_date(i.get("published") or "") or "9999") >= args.since]
        print(f"House {code} feed: {len(items)} item(s) naming this department, {len(hearings)} hearing(s) not saved yet")
        for i in hearings[: args.limit]:
            print(f"  {as_date(i.get('published') or '') or '          '}  {i['title'][:90]}")
            if not args.fetch:
                continue
            page = take(i["url"], f"{NOTE}: House {code} hearing {i['title'][:70]}")
            seen.add(i["url"])
            if not kept_page(page):
                print(f"    {page.get('status')} {page.get('error', '')}")
                continue
            event = house_event((ROOT / page["path"]).read_bytes())
            if not event["title"]:
                time.sleep(3)
                page = take(i["url"], f"{NOTE}: House {code} hearing {i['title'][:70]} (second request)")
                event = house_event((ROOT / page["path"]).read_bytes()) if kept_page(page) else event
            if not event["title"]:
                print("    page answered without the hearing twice")
                continue
            for doc in statements(event):
                if doc["url"] in seen:
                    continue
                got = take(doc["url"], f"{FILE_NOTE}: {i['url']}")
                seen.add(doc["url"])
                print(f"    {got.get('status')} {doc['label'][:40]} {got.get('size', '')}b")
        new_total += len(hearings)
    # An agency without an archive lists its testimony as files on a page: every statement linked there is a document.
    for page_url in TESTIMONY_PAGES:
        page = take(page_url, f"{NOTE}: testimony page {page_url}")
        if not kept_page(page):
            print(f"testimony page {page_url}: {page.get('error') or page.get('status')}")
            continue
        links = testimony_links((ROOT / page["path"]).read_bytes(), page_url)
        # A file refused before is not saved: it is asked for again, and through the hosted browser if refused again
        # (the Senate committee sites refuse this address).
        have = {origin_url(r) for r in rows if kept_page(r)}
        fresh_files, refused = [u for u in links if u not in have], []
        print(f"testimony page {page_url}: {len(links)} statement file(s) linked, {len(fresh_files)} not saved yet")
        for url in fresh_files[: args.limit]:
            print(f"  {url}")
            if args.fetch:
                got = take(url, f"{FILE_NOTE}: {page_url}")
                seen.add(url)
                print(f"    {got.get('status')} {got.get('size', '')}b")
                if got.get("status") in (401, 403):
                    refused.append(url)
        if refused:
            from browserbase_fetch import main as browserbase
            browserbase(refused, {u: f"{FILE_NOTE}: {page_url}" for u in refused})
        new_total += len(fresh_files)
    print(f"{new_total} document(s) not yet in the manifest" + ("" if args.fetch else "; rerun with --fetch, then `remarks.py extract`"))
    return 0


TESTIMONY_LINK = re.compile(r'href="([^"]+\.pdf)"', re.I)


def testimony_links(body: bytes, base_url: str) -> list[str]:
    """The PDF files a testimony page links, absolute, in page order, once each."""
    import urllib.parse
    out: list[str] = []
    for href in TESTIMONY_LINK.findall(body.decode("utf-8", errors="replace")):
        url = urllib.parse.urljoin(base_url, href.replace("&amp;", "&"))
        if url not in out:
            out.append(url)
    return out


def discover(argv: list[str]) -> int:
    """Conference pages that name this department's officials, through Exa; direct fetch, Browserbase on refusal."""
    parser = argparse.ArgumentParser(prog="remarks discover")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--days", type=int, default=480)
    parser.add_argument("--results", type=int, default=15)
    parser.add_argument("--query", default=None, help="the Exa query; the profile's conference query by default")
    args = parser.parse_args(argv)
    if args.query is None:
        args.query = P["remarks"].get("conference_query") or f'defense conference agenda speakers "{P["short"]}" "Program Executive Officer" keynote panel'
    key = env_value("EXA_API_KEY")
    if not key:
        raise SystemExit("EXA_API_KEY is not set")
    start = date.fromordinal(date.today().toordinal() - args.days).isoformat()
    payload = {"query": args.query, "numResults": args.results, "type": "auto", "startPublishedDate": start + "T00:00:00.000Z",
               "contents": {"text": False}, "excludeDomains": [*P["remarks"].get("own_domains", []), "linkedin.com", "youtube.com", "x.com", "facebook.com"]}
    body = post_json("https://api.exa.ai/search", payload, {"x-api-key": key, "Content-Type": "application/json"})
    record_answer("https://api.exa.ai/search", args.query, body)
    known = json.loads(DISCOVERED.read_text(encoding="utf-8")) if DISCOVERED.exists() else {}
    rows = manifest_rows()
    seen = {origin_url(r) for r in rows} | {r.get("url") for r in rows}
    fresh = []
    for r in json.loads(body).get("results") or []:
        url = (r.get("url") or "").split("#")[0]
        if not url or url in known:
            continue
        known[url] = {"query": args.query, "found": date.today().isoformat(), "title": r.get("title") or "", "published": as_date(str(r.get("publishedDate") or ""))}
        fresh.append(url)
        print(f"  {known[url]['published'] or '          '}  {known[url]['title'][:80]}")
    DISCOVERED.write_text(json.dumps(known, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(fresh)} new page(s) discovered, {len(known)} known")
    if not args.fetch:
        return 0
    refused = []
    for url in [u for u in known if u not in seen]:
        got = take(url, f"{NOTE}: conference page")
        if not kept_page(got):
            refused.append(url)
        print(f"  {got.get('status')}  {url[:90]}")
    return hosted(refused)


# --------------------------------------------------------------------- extract

def documents(rows: list[dict], article_re: re.Pattern | None = None) -> list[dict]:
    """Every saved document to read, with its kind: speech, testimony (navy.mil), statement (House PDF, with
    its hearing page), conference (a discovered page). The latest good capture of each URL."""
    known = json.loads(DISCOVERED.read_text(encoding="utf-8")) if DISCOVERED.exists() else {}
    latest: dict[str, dict] = {}
    for r in rows:
        if not kept_page(r) or not (ROOT / r["path"]).exists():
            continue
        url, note = origin_url(r), r.get("note", "")
        if (article_re or NAVY_ARTICLE).match(url):
            latest[url] = {"kind": archive_kind(url), "url": url, "row": r}
        elif note.startswith(FILE_NOTE + ":"):
            latest[url] = {"kind": "statement", "url": url, "row": r, "hearing_url": note.split(":", 1)[1].strip()}
        elif url in known or (r.get("url") in known):
            latest[url] = {"kind": "conference", "url": url, "row": r}
    for doc in latest.values():
        if doc["kind"] == "statement":
            page = latest_saved(rows, doc["hearing_url"])
            if page and host_of(doc["hearing_url"]) == "docs.house.gov":
                doc["hearing"] = house_event((ROOT / page["path"]).read_bytes())
            else:
                # A file linked from an agency's testimony page: its title and date are what the page's link says,
                # else what the file name carries; no committee, no witness list.
                doc["hearing"] = testimony_link_meta((ROOT / page["path"]).read_bytes() if page else b"", doc["url"])
    return sorted(latest.values(), key=lambda d: d["url"])


FILE_DATE_RES = (re.compile(r"(20\d{2})(\d{2})(\d{2})"), re.compile(r"(20\d{2})-(\d{2})-(\d{2})"), re.compile(r"(\d{1,2})-(\d{1,2})-(20\d{2})"),
                 re.compile(r"(20\d{2})-(\d{2})(?!\d)"))


def file_date(name: str) -> str:
    """A date the file name carries (20180314, 2016-04-01, 4-1-2016, 2024-11), as YYYY-MM-DD or YYYY-MM; else ''."""
    for i, rx in enumerate(FILE_DATE_RES):
        m = rx.search(name)
        if not m:
            continue
        if i == 2:
            month, day, year = m.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
        if i == 3:
            return f"{m.group(1)}-{m.group(2)}"
        year, month, day = m.groups()
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f"{year}-{month}-{day}"
    return ""


def testimony_link_meta(page: bytes, url: str) -> dict:
    """The hearing-shaped record for a statement file linked from a testimony page: the link's own text as the
    title, a date the link text or the file name states, and where the date came from."""
    text = page.decode("utf-8", errors="replace")
    name = url.rsplit("/", 1)[-1]
    anchor = re.search(r'<a[^>]+href="[^"]*' + re.escape(name) + r'"[^>]*>(.*?)</a>', text, re.S | re.I)
    title = " ".join(strip_tags(anchor.group(1)).split()) if anchor else ""
    issued, basis = "", ""
    if anchor:
        m = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (20\d{2})\b", anchor.group(1))
        if m:
            issued, basis = as_date(m.group(0)) or "", "link text"
    if not issued and file_date(name):
        issued, basis = file_date(name), "file name"
    return {"title": title, "committee": "", "issued": issued, "date_basis": basis, "witnesses": [], "documents": []}


COVER = 1000  # a statement states its date on its cover, before the text begins
LABEL_RE = re.compile(r"\W*(?:\d{4}|[A-Za-z]+)?\W*")  # a link's label on the page ("2021", "House"), not a title


def dated(meta: dict, text: str, url: str) -> tuple[str, str]:
    """The record's date: the day the page, link or file name gave, else the day the document's cover states, else a
    day the url's path carries. published_at holds a day, so a month or a year alone stays out and the basis says so."""
    if len(meta["issued"]) == 10:
        return meta["issued"], meta["date_basis"]
    cover = as_date(text[:COVER])
    if cover:
        return cover, "text"
    path = file_date(unquote(urlparse(url).path))
    return (path, "url") if len(path) == 10 else ("", f"url {path}, no day" if path else "")


def document_text(doc: dict) -> tuple[str, dict]:
    body = (ROOT / doc["row"]["path"]).read_bytes()
    if doc["kind"] in ("speech", "testimony"):
        art = navy_article(body)
        meta = {"title": art["title"], "issued": art["issued"], "publisher": P["short"], "witnesses": [],
                "stated": {k: art[k] for k in ("speaker", "place") if art[k]}}
        text = art["text"]
    elif doc["kind"] == "statement":
        h = doc["hearing"]
        # A statement reached from a House hearing page is the committee's; one linked from an agency's testimony
        # page has no hearing page here, and its publisher is the host that serves the file.
        publisher = h["committee"] or ("U.S. House of Representatives" if host_of(doc.get("hearing_url") or "") == "docs.house.gov" else host_of(doc["url"]))
        meta = {"title": h["title"], "issued": h["issued"], "date_basis": h.get("date_basis") or "page", "publisher": publisher, "witnesses": h["witnesses"]}
        text = pdf_text(ROOT / doc["row"]["path"])
    else:
        text = body.decode("utf-8", "replace")
        title = re.search(r"<title>(.*?)</title>", text, re.S)
        issued, basis = page_date(text)
        if not issued:
            known = json.loads(DISCOVERED.read_text(encoding="utf-8")) if DISCOVERED.exists() else {}
            issued, basis = (known.get(doc["url"]) or {}).get("published") or "", "search_index"
        if not issued:
            issued, basis = doc["row"]["retrieved_at"][:10], "retrieved"
        meta = {"title": strip_tags(title.group(1)) if title else "", "issued": issued, "date_basis": basis, "publisher": host_of(doc["url"]), "witnesses": []}
        text = html_to_markdown(text)
    meta.setdefault("date_basis", "page")
    # A webpage reaches the agent as Markdown, block structure kept; a PDF statement stays the one-line
    # text it always was, so its recorded cassette still answers.
    return (flatten(text) if doc["kind"] == "statement" else flatten_lines(text))[:CAP], meta


def page_date(text: str) -> tuple[str, str]:
    """The date a page states for itself: a published-time meta tag, JSON-LD datePublished, or a <time> element.
    Empty when the page states none; the caller then falls back to the search index or the retrieval date."""
    for pattern in (r'<meta[^>]+(?:property|name)="(?:article:published_time|datePublished|date|pubdate|publish-date|DC\.date\.issued)"[^>]+content="([^"]+)"',
                    r'<meta[^>]+content="([^"]+)"[^>]+(?:property|name)="(?:article:published_time|datePublished|date|pubdate|publish-date)"',
                    r'"datePublished"\s*:\s*"([^"]+)"', r'<time[^>]+datetime="([^"]+)"'):
        m = re.search(pattern, text, re.I)
        if m and as_date(m.group(1)[:10]) or (m and as_date(m.group(1))):
            return as_date(m.group(1)[:10]) or as_date(m.group(1)), "page"
    return "", ""


def lint(events: list[dict], text: str) -> tuple[list[dict], Counter]:
    return check(events, text, EVENT_TYPES, VERBATIM)


def ask(text: str, meta: dict, doc: dict, model: str, replay_only: bool, fresh: bool = False) -> tuple[dict, dict]:
    witnesses = "; ".join(f"{w['name']}, {w['position']}" for w in meta["witnesses"]) or "(none listed)"
    stated = "; ".join(f"{k}: {v}" for k, v in (meta.get("stated") or {}).items())
    user = (f"Agency: {AGENCY}\nKind: {doc['kind']}\nTitle: {meta['title']}\nPublisher: {meta['publisher']}\nDate: {meta['issued']}\n"
            + (f"Page fields: {stated}\n" if stated else "")
            + f"Witnesses: {witnesses}\nURL: {doc['url']}\n\nText:\n{text}")
    return structured(SYSTEM, user, SCHEMA, "remarks_events", model, replay_only=replay_only, fresh=fresh)


def extract(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="remarks extract")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--verify", type=int, default=0)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    docs = documents(manifest_rows())
    if args.limit:
        docs = docs[: args.limit]
    records, dropped_total, cost = [], Counter(), {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    unread = 0
    # The calls go out eight at a time, as in notice_kinds; the records are still taken in document order.
    pool = ThreadPoolExecutor(8)
    read = [document_text(doc) for doc in docs]
    asked = [pool.submit(ask, text, meta, doc, args.model, replay_only=args.check) for doc, (text, meta) in zip(docs, read)]
    for doc, (text, meta), answer in zip(docs, read, asked):
        try:
            answer, how = answer.result()
        except LookupError as why:
            # No cassette and no way to make the call (no model key): the document is recorded as unread with the
            # reason, so the build carries the gap instead of stopping on it. `--check` keeps failing, as it must.
            if args.check:
                raise
            answer, how, unread = {"events": [], "unread": str(why)}, {"replayed": False, "cassette": None, "model": None, "usage": {}}, unread + 1
        if not how["replayed"] and how["cassette"]:
            cost["calls"] += 1
            for k in ("input_tokens", "output_tokens"):
                cost[k] += how["usage"].get(k) or 0
        kept, dropped = lint(answer.get("events", []), text)
        dropped_total.update(dropped)
        events = link(kept, text, ("organization",))
        authority = "third_party" if doc["kind"] == "conference" else authority_of(doc["url"])
        head = {k: verbatim(answer.get(k) or "", text) for k in ("speaker_name", "speaker_role", "event_name", "event_host")}
        blanked = sorted(k for k in head if (answer.get(k) or "") and not head[k])
        # The prompt keeps the page's title and date, so the recorded cassette still answers; the record does better
        # where the page gave a bare label or no day.
        issued, basis = dated(meta, text, doc["url"])
        title = (head["event_name"] or head["event_host"] or meta["title"]) if LABEL_RE.fullmatch(meta["title"]) else meta["title"]
        records.append({
            **({"unread": answer["unread"]} if answer.get("unread") else {}),
            "url": doc["url"], "kind": doc["kind"], "publisher": meta["publisher"], "title": title, "issued": issued,
            "date_basis": basis, "hearing_url": doc.get("hearing_url"), "witnesses": meta["witnesses"], **head, "audience": answer.get("audience") or "other",
            "not_verbatim": blanked, "sha256": doc["row"]["sha256"], "path": doc["row"]["path"], "retrieved_at": doc["row"]["retrieved_at"],
            "text_chars": len(text), "cassette": how["cassette"], "model": how["model"], "source_authority": authority,
            "events": [dict(e, source_authority=authority) for e in events], "dropped": dict(sorted(dropped.items())),
        })
        print(f"{issued or '          '}  {doc['kind']:10} {len(events)} event(s)" + (f", {sum(dropped.values())} dropped" if dropped else "")
              + ("  (replayed)" if how["replayed"] else "") + ("  (unread: " + answer["unread"] + ")" if answer.get("unread") else "")
              + f"  {title[:70]}")
    if args.verify:
        agree = 0
        for doc in docs[: args.verify]:
            text, meta = document_text(doc)
            first, _ = ask(text, meta, doc, args.model, replay_only=False)
            second, how = ask(text, meta, doc, args.model, replay_only=False, fresh=True)
            cost["calls"] += 1
            for k in ("input_tokens", "output_tokens"):
                cost[k] += how["usage"].get(k) or 0
            a, b = normalized(lint(first.get("events", []), text)[0], "program"), normalized(lint(second.get("events", []), text)[0], "program")
            agree += a == b
            print(f"verify {meta['title'][:50]}: {'same' if a == b else 'differs'} ({len(a)} vs {len(b)} events, {len(set(a) & set(b))} shared)")
        print(f"verify: {agree} of {min(args.verify, len(docs))} document(s) gave the same normalized event set twice")
    out = {"source": "chromie-federal-buyer-map-trial/research/tools/remarks.py", "model": args.model,
           "models_answering": sorted({d.get("model") for d in docs if d.get("model")}), "cap_chars": CAP,
           "documents": records, "dropped": dict(sorted(dropped_total.items())), **({"unread": unread} if unread else {})}
    text_out = json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        if EVENTS.exists() and EVENTS.read_text(encoding="utf-8") == text_out:
            print(f"{EVENTS.relative_to(ROOT)} unchanged on replay")
            return 0
        print(f"{EVENTS.relative_to(ROOT)} would change on replay")
        return 1
    EVENTS.write_text(text_out, encoding="utf-8")
    n = sum(len(r["events"]) for r in records)
    print(f"{len(records)} document(s) read, {n} event(s) kept, {sum(dropped_total.values())} dropped; {cost['calls']} live call(s), "
          f"{cost['input_tokens']} in / {cost['output_tokens']} out tokens; written to {EVENTS.relative_to(ROOT)}"
          + (f"; {unread} document(s) unread (no cassette and no model key)" if unread else ""))
    for reason, count in sorted(dropped_total.items()):
        print(f"  dropped: {reason} x{count}")
    return 0


def show(argv: list[str]) -> int:
    for r in json.loads(EVENTS.read_text(encoding="utf-8"))["documents"]:
        print(f"{r['issued'] or '          '}  {r['kind']:10} {r['speaker_name'][:28]:28} {r['title'][:60]}")
        for e in r["events"]:
            print(f"    [{e['event_type']}] {e['statement'][:100]}")
            print(f"       capability: {e['capability']!r} program: {e['program'][:40]!r} orgs: {e['organizations']} conf {e['confidence']}")
    return 0


def selfcheck() -> int:
    index = b'''<article class="grid-item content-box item item-4529922"><p class="author-dateline"> 23 June 2026 </p><div class="inner"><a href="https://www.navy.mil/Press-Office/Speeches/display-speech/Article/4529922/cno-remarks-at-inter-american-naval-conference/"><h1 class="content-box-header"> CNO Remarks at Inter-American Naval Conference </h1></a></div></article>
    <article class="grid-item content-box item item-1"><div>no link here</div></article><a href="https://www.navy.mil/Press-Office/Speeches/?Page=5">5</a>'''
    navy_re = re.compile(r"^https://www\.navy\.mil/Press-Office/(Speeches/display-speech|Testimony/display-testimony)/Article/\d+/[^?#]+$")
    rows = navy_index_rows(index, navy_re)  # the Navy's own pattern, whatever profile runs the check
    assert rows == [{"url": "https://www.navy.mil/Press-Office/Speeches/display-speech/Article/4529922/cno-remarks-at-inter-american-naval-conference/",
                     "issued": "2026-06-23", "title": "CNO Remarks at Inter-American Naval Conference"}], rows
    assert navy_pages(index) == 5
    navy_archives = ("https://www.navy.mil/Press-Office/Speeches/", "https://www.navy.mil/Press-Office/Testimony/")
    assert archive_kind(rows[0]["url"], *navy_archives) == "speech"
    assert archive_kind("https://www.navy.mil/Press-Office/Testimony/display-testimony/Article/1/x/", *navy_archives) == "testimony"
    assert archive_kind("https://www.af.mil/News/Speeches/Display/Article/1/x/", "https://www.af.mil/News/Speeches/", None) == "speech"
    article = b'''<html><head><meta property="og:title" content="CNO Remarks at X"></head><body><nav>Home Press</nav>
    <div class="article-view"><h4 class="press-release__dateline">Panama City, Panama</h4><p>Good morning. We need autonomous systems on\xe2\x80\x91watch now.</p>
    <div class="bottom-blue"><h2>Speech by</h2><p> Adm. Daryl Caudle <br> </p><h2>Presented on</h2><p>23 June 2026</p><h2>Date Published</h2><p>24 June 2026</p></div></div>
    <footer>Privacy</footer></body></html>'''
    art = navy_article(article)
    assert art["title"] == "CNO Remarks at X" and art["issued"] == "2026-06-23" and art["speaker"] == "Adm. Daryl Caudle" and art["place"] == "Panama City, Panama", art
    assert "autonomous systems on-watch now" in art["text"] and "Privacy" not in art["text"] and "Home Press" not in art["text"], art["text"]
    assert art["text"].startswith("#### Panama City, Panama\n\nGood morning.") and "## Speech by\n\nAdm. Daryl Caudle" in art["text"], art["text"]
    hearing = b'''<h1>Department of the Navy Fiscal Year 2027 Budget Request</h1>
    <p>Subcommittee on Seapower and Projection Forces (Committee on Armed Services)</p><p>Wednesday, May 20, 2026 (3:30 PM)</p>
    <h2>Witnesses</h2><div class="witnessPanel"><p><strong>Mr. Jason Potter </strong><br><small class="text-small">Performing the Duties of ASN RDA</small></p></div>
    <ul><li>Potter Bio [<a target="_blank" href="http://docs.house.gov/meetings/AS/AS28/20260520/119324/HHRG-119-AS28-20260520-SD001.pdf">PDF</a>]</li>
    <li>Joint Witness Statement [<a target="_blank" href="http://docs.house.gov/meetings/AS/AS28/20260520/119324/HHRG-119-AS28-20260520-SD004.pdf">PDF</a>]</li></ul>'''
    ev = house_event(hearing)
    assert ev["title"].startswith("Department of the Navy") and ev["issued"] == "2026-05-20" and ev["committee"].startswith("Subcommittee on Seapower"), ev
    assert ev["witnesses"] == [{"name": "Mr. Jason Potter", "position": "Performing the Duties of ASN RDA"}], ev["witnesses"]
    assert [d["url"][-9:] for d in statements(ev)] == ["SD004.pdf"] and statements(ev)[0]["url"].startswith("https://"), statements(ev)
    assert HOUSE_EVENT.match("https://docs.house.gov/Committee/Calendar/ByEvent.aspx?EventID=119324") and navy_re.match(rows[0]["url"])  # the fixture is navy.mil's whatever profile runs the check
    assert page_date('<meta property="article:published_time" content="2026-03-26T14:02:11+00:00">') == ("2026-03-26", "page")
    assert page_date('<script type="application/ld+json">{"datePublished": "2026-04-17"}</script>') == ("2026-04-17", "page")
    assert page_date('<time datetime="2026-01-28T09:00">Jan 28</time>') == ("2026-01-28", "page") and page_date("<p>no date</p>") == ("", "")
    assert dated({"issued": ""}, "Statement by Dr. X Submitted to the Subcommittee April 13, 2021 Chairman", "https://x.gov/a.pdf") == ("2021-04-13", "text") \
        and dated({"issued": "2024-11"}, "Table of Contents", "https://x.gov/files/2024-11/afr.pdf") == ("", "url 2024-11, no day")
    text = "Admiral Caudle said the fleet needs unmanned surface vessels by 2027. Sea-Air-Space is hosted by the Navy League."
    good = {"event_type": "capability_priority", "statement": "x", "capability": "unmanned surface", "program": "", "organization": "",
            "person": "", "evidence_span": "the fleet needs unmanned surface vessels by 2027", "amounts": [], "confidence": 0.8}
    kept, dropped = lint([good, dict(good, program="USV program"), dict(good, event_type="rumour")], text)
    assert len(kept) == 1 and dropped == Counter({"program is not written so in the text": 1, "event type outside the list": 1}), dropped
    assert verbatim("Navy League", text) == "Navy League" and verbatim("the Navy League of the United States", text) == ""
    fake = [{"status": 200, "path": "research/sources/source_registry.json", "url": rows[0]["url"], "note": "live page via Browserbase", "sha256": "a", "retrieved_at": "x"},
            {"status": 200, "path": "research/sources/source_registry.json", "url": "https://docs.house.gov/meetings/AS/x.pdf", "note": f"{FILE_NOTE}: https://docs.house.gov/Committee/Calendar/ByEvent.aspx?EventID=1", "sha256": "b", "retrieved_at": "x"},
            {"status": 200, "path": "research/sources/source_registry.json", "url": "https://www.navy.mil/Press-Office/Speeches/?Page=2", "note": "live page via Browserbase", "sha256": "c", "retrieved_at": "x"}]
    kinds = [(d["kind"], d["url"][-8:]) for d in documents(fake, navy_re)]
    assert kinds == [("statement", "AS/x.pdf"), ("speech", "ference/")], kinds
    print("remarks selfcheck ok")
    return 0


COMMANDS = {"watch": watch, "discover": discover, "extract": extract, "show": show}

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if args and args[0] in COMMANDS:
        sys.exit(COMMANDS[args[0]](args[1:]))
    print(__doc__)
    sys.exit(2)

"""Oversight reports as dated audit findings: GAO and the inspectors general.

    python research/tools/oversight.py watch [--fetch] [--limit N] [--pages N]   # oversight.gov listing + GAO feed
    python research/tools/oversight.py discover [--fetch] [--days N] [--results N]  # GAO reports through Exa
    python research/tools/oversight.py extract [--limit N] [--verify K] [--check]  # the agent reads each report
    python research/tools/oversight.py show
    python research/tools/oversight.py --selfcheck

Three primitives. The connector: oversight.gov's federal
report listing (the inspectors general, filtered to this department's names) and GAO's report feed;
every page and file lands in the manifest first. Search discovery: Exa finds GAO product pages
the feed no longer carries; Browserbase fetches them because gao.gov refuses this address. The
agent: one structured call per report, recorded as a cassette (llm.py), asked for the
oversight chain: finding, problem, affected program and organization, possible
remediation, the acquisition need that may follow, and the passage the finding rests on.

Rules check the agent, they do not replace it. A span that is not on the saved page verbatim
drops the event; a money figure the text does not carry drops it; the source's
authority is the registry's word for the host, never the model's; a replay of the cassette
must give the same file (`--check`), and `--verify` makes a fresh call on a few reports and
reports whether the judgment held. Organizations are linked through the memory's aliases.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "research"
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from fetch import kept_page  # noqa: E402
from context_fetch import fetch as hosted  # noqa: E402
from llm import MODEL, env_value, structured  # noqa: E402
from markdown import html_to_markdown  # noqa: E402
from news import as_date, feed_items, host_of, manifest_rows, origin_url, post_json, record_answer  # noqa: E402
from org_memory_lrae import squash  # noqa: E402
from reader import REGISTRY, authority_of, flatten, flatten_lines, link, normalized, pdf_text  # noqa: E402,F401
from reader import lint as check  # noqa: E402

MANIFEST = RESEARCH / "sources" / "documents_manifest.jsonl"
from agency import EVENTS as EVENTS_DIR, NOTE_TAG, P, note_is_ours  # noqa: E402

EVENTS = EVENTS_DIR / "oversight_events.json"
LISTING = "https://www.oversight.gov/reports/federal?search_api_fulltext={query}&items_per_page=50&page={page}"
GAO_RSS = "https://www.gao.gov/rss/reports.xml"
GAO_PRODUCT_RE = re.compile(r"^https?://www\.gao\.gov/products/([a-z]+-\d{2}-\d+[a-z]*)/?$", re.I)
OVERSIGHT_HOST, GAO_HOST = "www.oversight.gov", "www.gao.gov"
# The department's names as oversight.gov's full-text search and the GAO feed print them.
AGENCY = P["oversight"]["agency"]
QUERIES = P["oversight"]["queries"]
REVIEWED_RE = re.compile(P["oversight"]["reviewed_re"], re.I)
NAMES_RE = re.compile(P["oversight"]["names_re"], re.I)
NOTE, FILE_NOTE, FEED_NOTE = f"oversight watch{NOTE_TAG}", f"oversight watch file{NOTE_TAG}", f"oversight feed{NOTE_TAG}"
CAP = 60_000  # characters of a report the agent reads: the summary, results in brief and findings come first
EVENT_TYPES = ("audit_finding", "program_delayed", "funding_change", "capability_priority", "program_cancelled")

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["events"],
    "properties": {"events": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["event_type", "problem", "affected_program", "affected_organization", "possible_remediation",
                     "future_acquisition_need", "evidence_span", "amounts", "confidence"],
        "properties": {
            "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
            "problem": {"type": "string", "description": "the capability gap, delay, cost issue, modernization problem or management weakness the report states, in one sentence of your own words"},
            "affected_program": {"type": "string", "description": "the program, system or platform, exactly as the text writes it; empty when the report names none"},
            "affected_organization": {"type": "string", "description": "the office, command or activity the finding is about, exactly as the text writes it; empty when the report names none"},
            "possible_remediation": {"type": "string", "description": "what the report recommends or the agency agreed to do, exactly as the text writes it when it states one; else empty"},
            "future_acquisition_need": {"type": "string", "description": "the buy this could lead to, in one sentence of your own words; empty when the finding does not point at one"},
            "evidence_span": {"type": "string", "description": "one passage copied verbatim from the text, 15 to 60 words, that states the finding"},
            "amounts": {"type": "array", "items": {"type": "string"}, "description": "every money figure in the evidence span, copied verbatim"},
            "confidence": {"type": "number", "description": "0 to 1: how surely the passage states this finding about this program"},
        }}}},
}
SYSTEM = (
    "You read one oversight report (a GAO report or an inspector general report) about a federal agency and state "
    "its findings as events. One event per distinct finding; report the finding the document states, never one you "
    "expect. Fields marked 'exactly as the text writes it' and the evidence span must be copied verbatim from the "
    "text you are given, with the same words, casing and punctuation; a paraphrase there is wrong. The evidence span is one "
    "contiguous passage: never join two passages with an ellipsis, and prefer the sentence that states the finding over a "
    "heading. Event types: "
    "audit_finding for a stated weakness, gap or deficiency; program_delayed when a schedule slip is stated; "
    "funding_change when a funding shortfall, cut or increase is stated; capability_priority when the report states "
    "a capability the agency must build or field; program_cancelled when a termination is stated. Return no events "
    "for a report that states no finding about this agency."
)


# ------------------------------------------------------------------ oversight.gov

def strip_tags(fragment: str) -> str:
    return squash(re.sub(r"<[^>]+>", " ", fragment))


def listing_rows(body: bytes) -> list[dict]:
    """Every report row of an oversight.gov listing page: date issued, agency reviewed, title, type, link."""
    text = body.decode("utf-8", "replace")
    out = []
    for row in re.findall(r'<tr class="listing-table__row[^"]*">(.*?)</tr>', text, re.S):
        cells = {h.split("view-")[-1].replace("-table-column", ""): c for h, c in re.findall(r'<td headers="([^"]*)"[^>]*>(.*?)</td>', row, re.S)}
        link = re.search(r'href="(/reports/[^"?#]+)"', cells.get("url", "") + cells.get("title", ""))
        when = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', cells.get("field-report-date-issued", ""))
        if not link:
            continue
        out.append({"url": "https://www.oversight.gov" + link.group(1), "title": strip_tags(cells.get("title", "")),
                    "issued": when.group(1) if when else "", "agency_reviewed": strip_tags(cells.get("field-report-agency-reviewed", "")),
                    "report_type": strip_tags(cells.get("field-report-type", ""))})
    return out


def detail_fields(body: bytes) -> dict:
    """What an oversight.gov report page states about the report, field by field, and its file link."""
    text = body.decode("utf-8", "replace")
    fields: dict[str, str] = {}
    for m in re.finditer(r'field--name-([a-z0-9-]+)[^>]*>(.*?)</div>\s*</div>', text, re.S):
        value = strip_tags(m.group(2))
        label = {"field-report-date-issued": "Date Issued", "field-report-submitting-oig": "Submitting OIG",
                 "field-report-agency-reviewed": "Agencies Reviewed/Investigated", "field-report-number": "Report Number",
                 "field-report-type": "Report Type", "field-report-number-of-recs": "Number of Recommendations",
                 "field-net-questioned-costs": "Questioned Costs", "field-net-funds-for-better-use": "Funds for Better Use"}.get(m.group(1))
        if label and value.startswith(label):
            fields[m.group(1).replace("field-report-", "").replace("field-net-", "").replace("-", "_")] = value[len(label):].strip()
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    body_m = re.search(r'field--name-body[^>]*>(.*?)</div>\s*</div>', text, re.S)
    pdf = re.search(r'href="(/sites/default/files/[^"]+\.pdf[^"]*)"', text, re.I)
    issued = fields.get("date_issued", "")
    try:
        issued_iso = datetime.strptime(issued, "%A, %B %d, %Y").strftime("%Y-%m-%d") if issued else ""
    except ValueError:
        issued_iso = as_date(issued)
    return {"title": strip_tags(h1.group(1)) if h1 else "", "description": strip_tags(body_m.group(1)).removeprefix("Report Description").strip() if body_m else "",
            "issued": issued_iso, "submitting_oig": fields.get("submitting_oig", ""), "agency_reviewed": fields.get("agency_reviewed", ""),
            "report_number": fields.get("number", ""), "report_type": fields.get("type", ""),
            "recommendations": fields.get("number_of_recs", ""), "questioned_costs": fields.get("questioned_costs", ""),
            "funds_for_better_use": fields.get("funds_for_better_use", ""),
            "file_url": "https://www.oversight.gov" + html.unescape(pdf.group(1)) if pdf else ""}


def take(url: str, note: str) -> dict:
    sys.path.insert(0, str(TOOLS))
    from fetch import fetch

    row = fetch(url, "direct", None, note)
    with MANIFEST.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def watch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="oversight watch")
    parser.add_argument("--fetch", action="store_true", help="retrieve the new reports and their files")
    parser.add_argument("--limit", type=int, default=10, help="new reports to retrieve per query")
    parser.add_argument("--pages", type=int, default=1, help="listing pages per query")
    parser.add_argument("--since", default="2025-01-01", help="oldest issue date to retrieve; the listing is ordered by search relevance, not date")
    args = parser.parse_args(argv)
    rows = manifest_rows()
    # A report is saved for this layer when a row under this profile's note holds it: the ledger is shared, and a
    # report another layer's watch took is taken again here (same bytes, a row of ours) so that `extract` reads it.
    ours = [r for r in rows if note_is_ours(r.get("note") or "")]
    seen = {origin_url(r) for r in ours} | {r.get("url") for r in ours}
    new_total, gao_new = 0, []
    for query in QUERIES:
        listed: list[dict] = []
        for page in range(args.pages):
            url = LISTING.format(query=urllib.parse.quote(query), page=page)
            row = take(url, f"{NOTE}: oversight.gov listing '{query}' page {page}")
            if not kept_page(row):
                print(f"oversight.gov '{query}' page {page}: {row.get('error') or row.get('status')}")
                break
            listed += listing_rows((ROOT / row["path"]).read_bytes())
        about = [r for r in listed if REVIEWED_RE.search(r["agency_reviewed"]) or NAMES_RE.search(r["title"])]
        # Investigations are reports on a named person's conduct, not on a program: the connector leaves them.
        fresh = sorted((r for r in about if r["url"] not in seen and r["issued"] >= args.since and r["report_type"] != "Investigation"),
                       key=lambda r: r["issued"], reverse=True)
        print(f"oversight.gov '{query}': {len(listed)} report(s) listed, {len(about)} about this department, {len(fresh)} issued since {args.since} and not saved yet")
        for report in fresh[: args.limit if args.fetch else len(fresh)]:
            print(f"  {report['issued']}  {report['report_type']:22} {report['title'][:80]}")
            if not args.fetch:
                continue
            got = take(report["url"], f"{NOTE}: oversight.gov {report['issued']} {report['title'][:70]}")
            seen.add(report["url"])
            if kept_page(got):
                detail = detail_fields((ROOT / got["path"]).read_bytes())
                if detail["file_url"] and detail["file_url"] not in seen:
                    file_row = take(detail["file_url"], f"{FILE_NOTE}: {report['url']}")
                    seen.add(detail["file_url"])
                    print(f"    {got['status']} page, {file_row.get('status')} file {file_row.get('size', '')}b {detail['report_number']}")
                else:
                    print(f"    {got['status']} page, no file link {detail['report_number']}")
            else:
                print(f"    {got.get('status')} {got.get('error', '')}")
        new_total += len(fresh)
    feed = take(GAO_RSS, f"{FEED_NOTE}: GAO reports")
    if kept_page(feed):
        body = (ROOT / feed["path"]).read_bytes()
        items = feed_items(body, GAO_RSS)
        text_of = {i["url"]: i for i in items}
        descriptions = dict(re.findall(r"<link>(https://www\.gao\.gov/products/[^<]+)</link>\s*<description>(.*?)</description>", body.decode("utf-8", "replace"), re.S))
        named = [i for i in items if NAMES_RE.search(i["title"] + " " + html.unescape(descriptions.get(i["url"], "")))]
        gao_new = [i["url"] for i in named if i["url"] not in seen]
        print(f"GAO feed: {len(items)} report(s), {len(named)} naming this department, {len(gao_new)} not saved yet")
        for i in named:
            print(f"  {i['published'] or '          '}  {i['title'][:90]}")
        new_total += len(gao_new)
        if args.fetch and gao_new:
            hosted(gao_new[: args.limit])
    else:
        print(f"GAO feed: {feed.get('error') or feed.get('status')}")
    print(f"{new_total} report(s) about this department are not yet in the manifest"
          + ("" if args.fetch else "; rerun with --fetch to retrieve them, then `oversight.py extract`"))
    return 0


def discover(argv: list[str]) -> int:
    """GAO reports about this department through Exa (search discovery), then context.dev for the pages."""
    parser = argparse.ArgumentParser(prog="oversight discover")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--days", type=int, default=540)
    parser.add_argument("--results", type=int, default=25)
    parser.add_argument("--query", default=P["oversight"]["gao_query"])
    args = parser.parse_args(argv)
    key = env_value("EXA_API_KEY")
    if not key:
        raise SystemExit("EXA_API_KEY is not set")
    from datetime import date
    start = date.fromordinal(date.today().toordinal() - args.days).isoformat()
    payload = {"query": args.query, "numResults": args.results, "type": "auto", "includeDomains": ["gao.gov"],
               "startPublishedDate": start + "T00:00:00.000Z", "contents": {"text": False}}
    body = post_json("https://api.exa.ai/search", payload, {"x-api-key": key, "Content-Type": "application/json"})
    record_answer("https://api.exa.ai/search", args.query, body)
    results = json.loads(body).get("results") or []
    rows = manifest_rows()
    seen = {origin_url(r) for r in rows} | {r.get("url") for r in rows}
    products = []
    for r in results:
        url = (r.get("url") or "").split("?")[0].rstrip("/")
        m = GAO_PRODUCT_RE.match(url)
        print(f"  {as_date(str(r.get('publishedDate') or '')) or '          '}  {(r.get('title') or '')[:80]}" + ("  (saved)" if url in seen else "" if m else "  (not a product page)"))
        if m and url not in seen and url not in products:
            products.append(url)
    print(f"{len(results)} result(s), {len(products)} GAO product page(s) not saved yet")
    if args.fetch and products:
        return hosted(products)
    return 0


# --------------------------------------------------------------------- extract

def documents(rows: list[dict]) -> list[dict]:
    """The saved reports to read: one per URL (the latest good capture), with its file where one was taken."""
    latest: dict[str, dict] = {}
    files: dict[str, dict] = {}
    for r in rows:
        if not kept_page(r):
            continue
        if not (ROOT / r["path"]).exists():
            continue
        url, note = origin_url(r), r.get("note", "")
        if note.startswith(FILE_NOTE + ":"):
            files[note.split(":", 1)[1].strip()] = r
        elif host_of(url) == OVERSIGHT_HOST and "/reports/" in url and "?" not in url and note.startswith(NOTE + ":"):
            latest[url] = {"kind": "oversight", "url": url, "row": r}
        elif host_of(url) == GAO_HOST and GAO_PRODUCT_RE.match(url.rstrip("/")) and r.get("mime") == "text/html" and note_is_ours(note):
            latest[url.rstrip("/")] = {"kind": "gao", "url": url.rstrip("/"), "row": r}
    for doc in latest.values():
        doc["file"] = files.get(doc["url"])
    return sorted(latest.values(), key=lambda d: d["url"])


def document_text(doc: dict) -> tuple[str, dict]:
    """The text the agent reads and what the page states about the report."""
    body = (ROOT / doc["row"]["path"]).read_bytes()
    if doc["kind"] == "oversight":
        meta = detail_fields(body)
        text = "\n".join(p for p in (meta["title"], meta["description"]) if p)
        if doc.get("file"):
            text += "\n\n" + pdf_text(ROOT / doc["file"]["path"])
        meta["publisher"] = meta["submitting_oig"] or "oversight.gov"
    else:
        # A GAO product page is a webpage: rendered as Markdown, block structure kept.
        page = html_to_markdown(body.decode("utf-8", "replace"))
        title = re.search(r"<title>(.*?)</title>", body.decode("utf-8", "replace"), re.S)
        published = re.search(r"Published:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", flatten(page))
        m = GAO_PRODUCT_RE.match(doc["url"])
        meta = {"title": squash(html.unescape(title.group(1))).split(" | U.S. GAO")[0] if title else "", "description": "",
                "issued": as_date(published.group(1)) if published else "", "submitting_oig": "", "agency_reviewed": "",
                "report_number": m.group(1).upper() if m else "", "report_type": "GAO report", "recommendations": "",
                "questioned_costs": "", "funds_for_better_use": "", "file_url": "", "publisher": "GAO"}
        text = page
    # oversight.gov reports read as their stated fields plus the PDF, one line as before, so the recorded
    # cassettes still answer; the GAO webpage reaches the agent as Markdown.
    return (flatten_lines(text) if doc["kind"] == "gao" else flatten(text))[:CAP], meta


VERBATIM = ("affected_program", "affected_organization", "possible_remediation")


def lint(events: list[dict], text: str) -> tuple[list[dict], Counter]:
    return check(events, text, EVENT_TYPES, VERBATIM)


def ask(text: str, meta: dict, url: str, model: str, replay_only: bool, fresh: bool = False) -> tuple[dict, dict]:
    user = (f"Agency: {AGENCY}\nReport: {meta['title']}\nPublisher: {meta['publisher']}\nReport number: {meta['report_number']}\n"
            f"Date issued: {meta['issued']}\nURL: {url}\n\nText:\n{text}")
    return structured(SYSTEM, user, SCHEMA, "oversight_events", model, replay_only=replay_only, fresh=fresh)


def extract(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="oversight extract")
    parser.add_argument("--limit", type=int, default=0, help="read at most this many reports (0 = all)")
    parser.add_argument("--verify", type=int, default=0, help="fresh second call on this many reports; report agreement")
    parser.add_argument("--check", action="store_true", help="replay the cassettes only and fail if the file would change")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    docs = documents(manifest_rows())
    if args.limit:
        docs = docs[: args.limit]
    records, dropped_total, cost = [], Counter(), {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    unread, not_about = 0, 0
    # The listing's rule, applied again at reading time: an oversight.gov report is this agency's when the agency
    # reviewed or the title names it. A report saved under a wider rule is left, and counted.
    def ours(doc: dict, meta: dict) -> bool:
        return doc["kind"] != "oversight" or bool(REVIEWED_RE.search(meta["agency_reviewed"] or "") or NAMES_RE.search(meta["title"] or ""))
    # The calls go out eight at a time, as in notice_kinds; the records are still taken in document order.
    pool = ThreadPoolExecutor(8)
    read = [document_text(doc) for doc in docs]
    asked = [pool.submit(ask, text, meta, doc["url"], args.model, replay_only=args.check) if ours(doc, meta) else None
             for doc, (text, meta) in zip(docs, read)]
    for doc, (text, meta), answer in zip(docs, read, asked):
        if not ours(doc, meta):
            not_about += 1
            print(f"{meta['issued'] or '          '}  not about this agency (agency reviewed: {meta['agency_reviewed'] or '?'})  {meta['report_number'] or meta['title'][:60]}")
            continue
        try:
            answer, how = answer.result()
        except LookupError as why:
            # No cassette and no way to make the call (no model key): the report is recorded as unread with the
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
        events = link(kept, text)
        authority = authority_of(doc["url"])
        records.append({
            **({"unread": answer["unread"]} if answer.get("unread") else {}),
            "url": doc["url"], "kind": doc["kind"], "publisher": meta["publisher"], "title": meta["title"], "issued": meta["issued"],
            "report_number": meta["report_number"], "report_type": meta["report_type"], "submitting_oig": meta["submitting_oig"],
            "agency_reviewed": meta["agency_reviewed"], "recommendations": meta["recommendations"],
            "questioned_costs": meta["questioned_costs"], "funds_for_better_use": meta["funds_for_better_use"],
            "sha256": doc["row"]["sha256"], "path": doc["row"]["path"], "retrieved_at": doc["row"]["retrieved_at"],
            "file_sha256": (doc.get("file") or {}).get("sha256"), "file_path": (doc.get("file") or {}).get("path"),
            "text_chars": len(text), "cassette": how["cassette"], "model": how["model"], "source_authority": authority,
            "events": [dict(e, source_authority=authority) for e in events], "dropped": dict(sorted(dropped.items())),
        })
        print(f"{meta['issued'] or '          '}  {len(events)} event(s)" + (f", {sum(dropped.values())} dropped" if dropped else "")
              + ("  (replayed)" if how["replayed"] else "") + ("  (unread: " + answer["unread"] + ")" if answer.get("unread") else "")
              + f"  {meta['report_number'] or meta['title'][:60]}")
    if args.verify:
        agree = 0
        for doc in docs[: args.verify]:
            text, meta = document_text(doc)
            first, _ = ask(text, meta, doc["url"], args.model, replay_only=False)
            second, how = ask(text, meta, doc["url"], args.model, replay_only=False, fresh=True)
            cost["calls"] += 1
            for k in ("input_tokens", "output_tokens"):
                cost[k] += how["usage"].get(k) or 0
            a, b = normalized(lint(first.get("events", []), text)[0]), normalized(lint(second.get("events", []), text)[0])
            same = a == b
            agree += same
            overlap = len(set(a) & set(b))
            print(f"verify {meta['report_number'] or doc['url'][-40:]}: {'same' if same else 'differs'} ({len(a)} vs {len(b)} events, {overlap} shared)")
        print(f"verify: {agree} of {min(args.verify, len(docs))} report(s) gave the same normalized event set twice")
    out = {"source": "chromie-federal-buyer-map-trial/research/tools/oversight.py", "model": args.model,
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
    n_events = sum(len(r["events"]) for r in records)
    print(f"{len(records)} report(s) read, {n_events} event(s) kept, {sum(dropped_total.values())} dropped; "
          f"{cost['calls']} live call(s), {cost['input_tokens']} in / {cost['output_tokens']} out tokens; written to {EVENTS.relative_to(ROOT)}"
          + (f"; {unread} report(s) unread (no cassette and no model key)" if unread else "")
          + (f"; {not_about} saved report(s) not about this agency, left" if not_about else ""))
    for reason, n in sorted(dropped_total.items()):
        print(f"  dropped: {reason} x{n}")
    return 0


def show(argv: list[str]) -> int:
    data = json.loads(EVENTS.read_text(encoding="utf-8"))
    for r in data["documents"]:
        print(f"{r['issued'] or '          '}  {r['publisher'][:24]:24} {r['report_number']:16} {r['title'][:70]}")
        for e in r["events"]:
            print(f"    [{e['event_type']}] {e['problem'][:110]}")
            print(f"       program: {e['affected_program'][:60]!r}  orgs: {e['organizations']}  conf {e['confidence']}")
    return 0


def selfcheck() -> int:
    listing = b'''<table><tr class="listing-table__row table-row"><td headers="view-field-report-date-issued-table-column"><time datetime="2026-03-17T12:00:00Z">03/17/2026</time></td>
    <td headers="view-field-report-agency-reviewed-table-column">Department of War</td><td headers="view-title-table-column"><a href="/reports/audit/audit-navy-defective-parts" hreflang="en">Audit of Navy Defective Parts</a></td>
    <td headers="view-field-report-type-table-column">Audit</td></tr><tr class="listing-table__row"><td headers="view-title-table-column">no link</td></tr>
    <tr class="listing-table__row table-row"><td headers="view-field-report-date-issued-table-column"><time datetime="2024-10-15T12:00:00Z">10/15/2024</time></td>
    <td headers="view-field-report-agency-reviewed-table-column" class="views-field">Department of War		</td><td headers="view-title-table-column" class="views-field">Report Of Investigation: Navy Cyberwarfare		</td>
    <td headers="view-field-report-type-table-column">Investigation</td><td headers="view-url-table-column" class="action-cell" rowspan="2"><a href="/reports/report-investigation-navy" hreflang="en">View Report<span class="fa"></span></a></td></tr></table>'''
    rows = listing_rows(listing)
    assert rows == [{"url": "https://www.oversight.gov/reports/audit/audit-navy-defective-parts", "title": "Audit of Navy Defective Parts",
                     "issued": "2026-03-17", "agency_reviewed": "Department of War", "report_type": "Audit"},
                    {"url": "https://www.oversight.gov/reports/report-investigation-navy", "title": "Report Of Investigation: Navy Cyberwarfare",
                     "issued": "2024-10-15", "agency_reviewed": "Department of War", "report_type": "Investigation"}], rows
    detail = b'''<h1 class="page-title">Audit of Navy Defective Parts</h1>
    <div class="field field--name-field-report-date-issued"><div class="title">Date Issued</div><div class="field__item">Tuesday, March 17, 2026</div></div>
    <div class="field field--name-field-report-submitting-oig"><div class="title">Submitting OIG</div><div class="field__item">Department of War OIG</div></div>
    <div class="field field--name-field-report-number"><div class="title">Report Number</div><div class="field__item">DODIG-2026-070</div></div>
    <div class="field field--name-field-net-questioned-costs"><div class="title">Questioned Costs</div><div class="field__item">$2,600,000</div></div>
    <div class="field field--name-body"><div class="title">Report Description</div><div class="field__item"><p>We determined whether the Navy recovered costs.</p></div></div>
    <a href="/sites/default/files/documents/reports/2026-03/DODIG-2026-070_SECURE.pdf">View Report</a>'''
    d = detail_fields(detail)
    assert (d["title"], d["issued"], d["submitting_oig"], d["report_number"], d["questioned_costs"]) == \
        ("Audit of Navy Defective Parts", "2026-03-17", "Department of War OIG", "DODIG-2026-070", "$2,600,000"), d
    assert d["description"] == "We determined whether the Navy recovered costs." and d["file_url"].endswith("DODIG-2026-070_SECURE.pdf"), d
    text = "The Navy did not recover $2.6 million from the contractor for defective parts. NAVSUP agreed to seek restitution."
    good = {"event_type": "audit_finding", "problem": "x", "affected_program": "", "affected_organization": "NAVSUP",
            "possible_remediation": "seek restitution", "future_acquisition_need": "", "evidence_span": "did not recover $2.6 million from the contractor",
            "amounts": [], "confidence": 0.9}
    kept, dropped = lint([good, dict(good, evidence_span="did not recover 2.6 million dollars"), dict(good, affected_organization="Naval Supply Systems Command"),
                          dict(good, amounts=["$9 million"]), dict(good, event_type="rumour"), dict(good, confidence=1.5)], text)
    assert len(kept) == 1 and kept[0]["amounts"] == ["$2.6 million"], (kept, dropped)
    assert dropped == Counter({"evidence span is not on the saved page verbatim": 1, "affected_organization is not written so in the text": 1,
                               "a money figure is not in the evidence span": 1, "event type outside the list": 1, "confidence outside 0..1": 1}), dropped
    assert normalized(kept) == [("audit_finding", "", "did not recover $2.6 million from the contractor")]
    assert GAO_PRODUCT_RE.match("https://www.gao.gov/products/gao-26-108451") and not GAO_PRODUCT_RE.match("https://www.gao.gov/products")
    assert REVIEWED_RE.search("Department of War") and REVIEWED_RE.search("Department of the Navy") and not REVIEWED_RE.search("Department of Energy")
    assert authority_of("https://www.example.com/x") == "unregistered"
    docs = documents([{"status": 200, "path": "research/sources/source_registry.json", "url": "https://www.oversight.gov/reports/audit/a", "note": "oversight watch: x", "mime": "text/html", "sha256": "a", "retrieved_at": "2026-09-22T00:00:00Z"},
                      {"status": 200, "path": "research/sources/source_registry.json", "url": "https://www.oversight.gov/sites/default/files/a.pdf", "note": "oversight watch file: https://www.oversight.gov/reports/audit/a", "mime": "application/pdf", "sha256": "b", "retrieved_at": "2026-09-22T00:00:00Z"},
                      {"status": 200, "path": "research/sources/source_registry.json", "url": "https://www.oversight.gov/reports/federal?search_api_fulltext=Navy", "note": "oversight watch: listing", "mime": "text/html", "sha256": "c", "retrieved_at": "2026-09-22T00:00:00Z"},
                      {"status": 200, "path": "research/sources/source_registry.json", "url": "https://www.gao.gov/products/gao-26-1", "note": "live page via Browserbase", "mime": "text/html", "sha256": "d", "retrieved_at": "2026-09-22T00:00:00Z"}])
    assert [(d["kind"], d["url"], bool(d.get("file"))) for d in docs] == [("gao", "https://www.gao.gov/products/gao-26-1", False), ("oversight", "https://www.oversight.gov/reports/audit/a", True)], docs
    print("oversight selfcheck ok")
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

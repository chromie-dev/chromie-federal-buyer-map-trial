#!/usr/bin/env python3
"""Programs: each program the agency lists, with its office and manager, the money its budget book gives it, the
contract money obligated against it in the fiscal year, and what that leaves.

Sources, each saved in the ledger before it is read:
  - the agency's own program listing (profile `people.program_listing`; DARPA publishes darpa.mil/json/program):
    title, status, office and the program manager by name;
  - the program blocks of the budget books (budget.py, "programs" in the budget lines file): the amounts per fiscal
    year, the need the book states and the plans, word for word;
  - the fiscal year's contract transactions whose description names the program (USAspending, keyless; one search
    per program term, funded by the agency).

What is inferred, and marked so on every record:
  - which budget blocks are the program: its title equals the block's acronym or the block's title;
  - which transactions are the program's: "strong" when the description names it unmistakably (the title in full, the
    acronym in parentheses, beside the word program, or an acronym no dictionary word spells: NOM4D, SPACE-BACN);
    "weak" (an acronym that is also a word: CASTLE) are listed and never counted;
  - money left = the book's amount for the fiscal year minus the strong contract obligations found. It is an upper
    bound: grants, other transactions and contracts whose description does not name the program are not found, and
    research money stays available for obligation for two years.

An agency that lists no programs (the Navy) is read from its books instead: every P-1 line item, and every R-1
program element as the program blocks its R-2A pages fund. The books name no manager, so each program is tied to the
office its money is managed in, and that is inferred, with its basis on the record: the program office codes the
program's own text prints ("named"; a block may fall back on its element's justification), else the owners of the
requirements whose titles name the program ("needs", from the frozen corpus: CANES in 16 requirement titles, all
owned by PMW 160). The leading office must hold two thirds of the mentions or of the requirements, and at least two
requirements; anything short of that is listed with its candidates. Only program offices, program executive offices
and direct reporting program managers count: a warfare center that performs the work does not hold the money.

  AGENCY=darpa python research/tools/programs.py collect [--limit N]   # network: the listing (or the book programs), then the spend searches
  AGENCY=darpa python research/tools/programs.py build [--check]       # <agency memory>/programs.json
  python research/tools/programs.py --selfcheck
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import EVENTS, MANIFEST, MEMORY, NOTE_TAG, P, RESULTS  # noqa: E402
from agency_layers_sql import uid  # noqa: E402
from fetch import fetch, kept_page  # noqa: E402

OUT = MEMORY / "programs.json"
SEED = MEMORY / "organization_seed.json"
BUDGET = EVENTS / "budget_lines.json"
CORPUS = RESULTS / "corpus.json"
LEADS = ("program_office", "program_executive_office", "direct_reporting_program_manager")  # the offices that hold a program's money
SHARE = 2 / 3  # the leading office's share of the mentions, or of the requirements, that ties a book program to it
PEOPLE = P["people"]
LISTING = PEOPLE.get("program_listing")
FIELDS = PEOPLE.get("program_fields") or {}
CURRENT = PEOPLE.get("program_current") or ""
LISTING_NOTE = f"program listing{NOTE_TAG}:"
SPEND_NOTE = f"program spend{NOTE_TAG}:"
SPEND_URL = "https://api.usaspending.gov/api/v2/search/spending_by_transaction/"
SPEND_FIELDS = ["Award ID", "Recipient Name", "Action Date", "Transaction Amount", "Transaction Description", "Award Type",
                "Awarding Agency", "Funding Agency"]
CONTRACT_TYPES = ["A", "B", "C", "D"]  # USAspending carries no other transactions; grants take no keyword search
STALE = timedelta(days=7)
PAGES = 5
CHANGE_RE = re.compile(r"FY \d{4} to FY \d{4} Increase/Decrease Statement:\s*(.+)$", re.S)  # the book's own reason the money moves
LEFT_BASIS = ("upper bound: the budget book's amount for the fiscal year less the contract obligations whose description "
              "names the program; grants, other transactions and contracts that do not name it are not found, and "
              "research money stays available for obligation for two years, procurement money for three")


def norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", html.unescape(text or "").upper())


def fiscal_year(day: str) -> int:
    d = date.fromisoformat(day[:10])
    return d.year + (d.month >= 10)


def fy_window(fy: int) -> tuple[str, str]:
    return f"{fy - 1}-10-01", f"{fy}-09-30"


def manifest_rows() -> list[dict]:
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()] if MANIFEST.exists() else []


def newest(rows: list[dict], prefix: str) -> dict | None:
    hits = [r for r in rows if (r.get("note") or "").startswith(prefix) and kept_page(r) and (ROOT / r["path"]).exists()]
    return max(hits, key=lambda r: r["retrieved_at"]) if hits else None


def listing_programs(records: list[dict], fields: dict = FIELDS) -> list[dict]:
    """One program per listing id: the listing repeats a program once per research topic."""
    out: dict[str, dict] = {}
    for rec in records:
        pid = str(rec.get(fields["id"]) or "")
        if not pid:
            continue
        first, last = (html.unescape(rec.get(f) or "").strip() for f in fields["manager"])
        prog = out.setdefault(pid, {"id": pid, "title": html.unescape(rec.get(fields["title"]) or "").strip(),
                                    "status": rec.get(fields["status"]) or "", "office_as_listed": html.unescape(rec.get(fields["office"]) or "").strip(),
                                    "manager": " ".join(p for p in (first, last) if p), "manager_role": rec.get(fields["manager_role"]) or "",
                                    "path": rec.get(fields["path"]) or "", "topics": []})
        for topic in (rec.get(fields["topics"]) or "").split(","):
            if topic.strip() and topic.strip() not in prog["topics"]:
                prog["topics"].append(topic.strip())
    return sorted(out.values(), key=lambda p: (p["office_as_listed"], p["title"]))


def office_ids(seed: dict) -> dict[str, str]:
    """Office name or alias, normalized, to the seed's node id."""
    out = {}
    for n in seed.get("nodes", []):
        for name in [n.get("name") or ""] + [a["text"] if isinstance(a, dict) else str(a) for a in n.get("aliases") or []]:
            if name:
                out.setdefault(norm(name), n["id"])
    return out


def budget_blocks(programs: list[dict]) -> list[dict]:
    """The newest book's program blocks: its three years are the ones the rest of the layer reads."""
    if not programs:
        return []
    pb = max(p["pb"] for p in programs)
    return [p for p in programs if p["pb"] == pb]


def match_blocks(title: str, blocks: list[dict]) -> tuple[list[dict], str]:
    t = norm(title)
    if not t:
        return [], "none"
    by_acronym = [b for b in blocks if b.get("acronym") and norm(b["acronym"]) == t]
    if by_acronym:
        return by_acronym, "acronym"
    by_title = [b for b in blocks if t in (norm(b["title"]), norm(re.sub(r"\s*\([^)]*\)\s*$", "", b["title"])))]
    return by_title, ("title" if by_title else "none")


def summed(blocks: list[dict]) -> dict[str, float | None]:
    years = sorted({k for b in blocks for k in b["amounts"]})
    return {y: (round(sum(b["amounts"][y] or 0 for b in blocks), 3) if any(b["amounts"].get(y) is not None for b in blocks) else None) for y in years}


def strength(description: str, title: str, acronym: str, full_title: str, ambiguous: bool = False) -> str:
    """Whether a transaction description names the program unmistakably. An `ambiguous` acronym, one the contracts spell
    out two ways (MDA: Maritime Domain Awareness and the Missile Defense Agency), names it only with its full title."""
    d = " ".join((description or "").upper().split())
    full = norm(full_title)
    if full and len(full) > 12 and full in norm(d):
        return "strong"
    kind = "none"
    for name in sorted({title.upper(), acronym.upper()} - {""}, key=len, reverse=True):
        if not re.search(rf"(?<![A-Z0-9]){re.escape(name)}(?![A-Z0-9])", d):
            continue
        kind = "weak"
        if ambiguous and name == acronym.upper():
            continue
        if not name.isalpha() or re.search(rf"\({re.escape(name)}\)|{re.escape(name)} PROGRAM|PROGRAM\W[^.]*{re.escape(name)}", d):
            return "strong"  # NOM4D, SPACE-BACN, OPS-5G: no word spells them
    return kind


def spend_terms(prog: dict, blocks: list[dict]) -> list[str]:
    """What to search for: the listed title, and the full title of a matched block when it differs."""
    terms = [prog["title"]]
    for b in blocks:
        base = re.sub(r"\s*\([^)]*\)\s*$", "", b["title"])
        if norm(base) != norm(prog["title"]) and base not in terms:
            terms.append(base)
    return [t for t in terms if len(norm(t)) >= 3]


def spend_query(term: str, fy: int, page: int) -> dict:
    start, end = fy_window(fy)
    return {"filters": {"keywords": [term], "time_period": [{"start_date": start, "end_date": end}],
                        "agencies": [{"type": "funding", "tier": "subtier", "name": P["agency"]["subtier_name"]}],
                        "award_type_codes": CONTRACT_TYPES},
            "fields": SPEND_FIELDS, "limit": 100, "page": page, "sort": "Transaction Amount", "order": "desc"}


def search_spend(terms: list[str], fy: int, rows: list[dict], handle, limit: int) -> int:
    """Each term's contract transactions in the fiscal year from USAspending, page by page; a saved answer under a
    week old is read instead of asked again. Returns the searches sent."""
    now = datetime.now(timezone.utc)
    fresh = lambda r: r and datetime.fromisoformat(r["retrieved_at"].replace("Z", "+00:00")) > now - STALE  # noqa: E731
    asked = {json.dumps(r.get("request_body"), sort_keys=True): r for r in rows if (r.get("note") or "").startswith(SPEND_NOTE) and kept_page(r)}
    searched = 0
    for term in dict.fromkeys(terms):
        for page in range(1, PAGES + 1):
            query = spend_query(term, fy, page)
            old = asked.get(json.dumps(query, sort_keys=True))
            if fresh(old):
                answer = json.loads((ROOT / old["path"]).read_text(encoding="utf-8"))
            else:
                if searched >= limit:
                    break
                row = fetch(SPEND_URL, "direct", None, f"{SPEND_NOTE} {term}", payload=query)
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
                searched += 1
                if not kept_page(row):
                    print(row.get("status"), term, row.get("error", ""))
                    break
                answer = json.loads((ROOT / row["path"]).read_text(encoding="utf-8"))
                time.sleep(0.3)
            if not (answer.get("page_metadata") or {}).get("hasNext"):
                break
    return searched


def collect(argv: list[str]) -> int:
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 10_000
    rows = manifest_rows()
    budget = json.loads(BUDGET.read_text(encoding="utf-8")) if BUDGET.exists() else {}
    if not LISTING:
        units, fy = book_units(budget), book_year(budget)
        tied = [u for u, tie in zip(units, book_ties(units)) if tie["office"]]
        with MANIFEST.open("a", encoding="utf-8") as handle:
            searched = search_spend([t for u in tied for t in unit_terms(unit_names(u["title"], u["text"]))], fy, rows, handle, limit)
        print(f"{len(units)} book program(s), {len(tied)} tied to an office; {searched} spend search(es) sent for FY{fy}")
        return 0
    now = datetime.now(timezone.utc)
    with MANIFEST.open("a", encoding="utf-8") as handle:
        listing = newest(rows, LISTING_NOTE)
        if not (listing and datetime.fromisoformat(listing["retrieved_at"].replace("Z", "+00:00")) > now - STALE):
            listing = fetch(LISTING, "direct", None, f"{LISTING_NOTE} {LISTING}")
            handle.write(json.dumps(listing, sort_keys=True) + "\n")
            handle.flush()
            print(listing.get("status"), LISTING)
        if not kept_page(listing):
            print("the program listing did not answer; spend not searched")
            return 1
        fy = fiscal_year(listing["retrieved_at"])
        progs = [p for p in listing_programs(json.loads((ROOT / listing["path"]).read_text(encoding="utf-8"))) if p["status"] == CURRENT]
        blocks = budget_blocks(budget.get("programs", []))
        searched = search_spend([t for prog in progs for t in spend_terms(prog, match_blocks(prog["title"], blocks)[0])], fy, rows, handle, limit)
    print(f"{len(progs)} current program(s); {searched} spend search(es) sent for FY{fy}")
    return 0


def saved_spend(rows: list[dict], fy: int) -> dict[str, list[dict]]:
    """Transactions by search term, from each query's newest answer."""
    newest_by_query: dict[str, dict] = {}
    for r in rows:
        body = r.get("request_body") or {}
        if (r.get("note") or "").startswith(SPEND_NOTE) and kept_page(r) and (ROOT / r["path"]).exists() \
                and (body.get("filters") or {}).get("time_period", [{}])[0].get("start_date") == fy_window(fy)[0]:
            key = json.dumps(body, sort_keys=True)
            if r["retrieved_at"] >= newest_by_query.get(key, {}).get("retrieved_at", ""):
                newest_by_query[key] = r
    out: dict[str, list[dict]] = defaultdict(list)
    for key, r in sorted(newest_by_query.items()):
        term = r["request_body"]["filters"]["keywords"][0]
        out[term] += [{**t, "_source": {"url": SPEND_URL, "retrieved_at": r["retrieved_at"], "sha256": r.get("sha256", "")}}
                      for t in json.loads((ROOT / r["path"]).read_text(encoding="utf-8")).get("results", [])]
    return out


def program_spend(prog: dict, blocks: list[dict], by_term: dict[str, list[dict]]) -> dict:
    acronym = next((b["acronym"] for b in blocks if b.get("acronym")), "")
    full = next((re.sub(r"\s*\([^)]*\)\s*$", "", b["title"]) for b in blocks), "")
    return spend_found(spend_terms(prog, blocks), prog["title"], acronym, full, by_term)


def spend_found(terms: list[str], title: str, acronym: str, full: str, by_term: dict[str, list[dict]], known: Counter | None = None) -> dict:
    """The transactions the searches for a program's terms found, strong (counted) and weak (listed only). `known` holds
    how the budget books spell the acronym out."""
    seen, strong, weak = set(), [], []
    found = [t for term in terms for t in by_term.get(term, [])]
    # The acronym alone names the program only where the books plainly mean it and no contract spells it another way
    # (MISSILE DEFENSE AGENCY (MDA) against Maritime Domain Awareness); otherwise only the full title does.
    plain, sense = meaning(Counter(known), stems(full, acronym)) if acronym else (True, None)
    contracts = sorted({x for t in found if acronym and (x := stems(expansion(acronym.upper(), (t.get("Transaction Description") or "").upper()), acronym))})
    two_ways = not plain or any(not similar(x, y) for x, y in combinations(([sense] if sense else []) + contracts, 2))
    for t in found:
        key = (t.get("Award ID"), t.get("Action Date"), t.get("Transaction Amount"), t.get("Transaction Description"))
        if key in seen:
            continue
        seen.add(key)
        kind = strength(t.get("Transaction Description") or "", title, acronym, full, two_ways)
        row = {"award": t.get("Award ID"), "recipient": t.get("Recipient Name"), "date": t.get("Action Date"),
               "amount": t.get("Transaction Amount"), "description": t.get("Transaction Description"), "source": t["_source"]}
        (strong if kind == "strong" else weak if kind == "weak" else []).append(row)
    return {"obligated": round(sum(r["amount"] or 0 for r in strong), 2), "transactions": len(strong),
            "awards": sorted({r["award"] for r in strong if r["award"]}), "strong": strong, "weak": weak,
            "searched": bool(terms) and all(term in by_term for term in terms)}


def office_money(progs: list[dict], fy: int, found: dict[str, list[dict]] | None = None) -> dict[str, dict]:
    """Per office: its programs' amounts summed, the contract money found against any of them counted once (a transaction
    two of its programs share, or one a name only its own lines carry finds, `found`), and the upper bound left. Nothing is
    left when more was found than the programs hold: then a line the office runs is not tied to it (the procurement line
    of a missile whose development blocks are), and the amount says so instead of going below zero."""
    out = {}
    for office in sorted({p["office"] for p in progs if p.get("office")}):
        mine = [p for p in progs if p.get("office") == office]
        money: dict[str, float] = defaultdict(float)
        for p in mine:
            for y, v in p["budget"]["amounts_musd"].items():
                money[y] += v or 0
        seen, strong = set(), []
        for t in [t for p in mine for t in (p.get("spend") or {}).get("strong", [])] + (found or {}).get(office, []):
            key = (t["award"], t["date"], t["amount"], t["description"])
            if key not in seen:
                seen.add(key)
                strong.append(t)
        obligated = round(sum(t["amount"] or 0 for t in strong), 2)
        budget_fy = money.get(f"fy{fy}")
        searched = any((p.get("spend") or {}).get("searched") for p in mine)
        over = budget_fy is not None and obligated / 1e6 > budget_fy
        recipients: dict[str, float] = defaultdict(float)
        for t in strong:
            recipients[t["recipient"] or ""] += t["amount"] or 0
        out[office] = {"programs": len(mine), "amounts_musd": {y: round(v, 3) for y, v in sorted(money.items())},
                       "spend": {"obligated": obligated, "transactions": len(strong), "awards": sorted({t["award"] for t in strong if t["award"]})},
                       "left": {"amount_musd": round(budget_fy - obligated / 1e6, 3), "basis": LEFT_BASIS}
                       if searched and budget_fy is not None and not over else None,
                       "left_note": (f"FY{fy} contract money found (${obligated / 1e6:,.1f}M) passes the FY{fy} amount of the lines tied to the "
                                     f"office (${budget_fy:,.1f}M): earlier years' money is still being obligated, a line it runs is not tied to it, "
                                     f"or a transaction names another office's line") if over else "",
                       "top_recipients_found": [{"recipient": k, "obligated_usd": round(v, 2)} for k, v in sorted(recipients.items(), key=lambda kv: -kv[1])[:10]]}
    return out


def build_programs(progs: list[dict], blocks: list[dict], by_term: dict[str, list[dict]], offices: dict[str, str], fy: int, listing: dict,
                   current: str = CURRENT) -> dict:
    out, used = [], set()
    for prog in progs:
        matched, how = match_blocks(prog["title"], blocks)
        used |= {id(b) for b in matched}
        amounts = summed(matched) if matched else {}
        spend = program_spend(prog, matched, by_term) if prog["status"] == current else None
        budget_fy = amounts.get(f"fy{fy}")
        left = None
        if spend is not None and spend["searched"] and budget_fy is not None:
            left = {"amount_musd": round(budget_fy - spend["obligated"] / 1e6, 3), "basis": LEFT_BASIS}
        plans: dict[str, str] = {}
        for b in matched:  # a program the book splits across elements states its plans under the element that funds the year
            for label, text in (b.get("plans") or {}).items():
                if len(text) > len(plans.get(label, "")):
                    plans[label] = text
        change = next((m.group(1).strip() for text in plans.values() if (m := CHANGE_RE.search(text))), "")
        out.append({**prog, "office": offices.get(norm(prog["office_as_listed"]), ""),
                    "url": (PEOPLE.get("staff_base_url") or "") + prog["path"] if prog["path"] else "",
                    "listing_source": {"url": LISTING, "retrieved_at": listing.get("retrieved_at", ""), "sha256": listing.get("sha256", "")},
                    "budget": {"match": how, "amounts_musd": amounts, "blocks": [{k: b[k] for k in ("pe", "project", "title", "amounts", "book", "pb")} for b in matched]},
                    "problem": max((b.get("description") or "" for b in matched), key=len, default=""), "plans": plans,
                    "change_statement": change, "spend": spend, "left": left})
    unlisted = [{k: b[k] for k in ("pe", "project", "title", "acronym", "amounts", "book", "pb")} for b in blocks if id(b) not in used]
    live = [p for p in out if p["status"] == current]
    return {"fiscal_year": fy, "listing_retrieved_at": listing.get("retrieved_at", ""), "left_basis": LEFT_BASIS, "programs": out,
            "offices": office_money(live, fy), "budget_blocks_without_a_listed_program": unlisted,
            "summary": {"programs": len(out), "current": len(live), "current_with_manager": sum(bool(p["manager"]) for p in live),
                        "current_with_budget": sum(p["budget"]["match"] != "none" for p in live),
                        "current_with_spend_found": sum(bool(p["spend"] and p["spend"]["transactions"]) for p in live),
                        "budget_blocks_unmatched": len(unlisted)}}


# ---------------------------------------------------------------- programs read from the books (no listing)

TITLE_ACRONYM = re.compile(r"\(([A-Z][A-Z0-9/&-]{2,14})\)")
# a mention that says the office runs the money ("operated by PMS 406", "PEO UWS provides Milestone Decision Authority"),
# not one that lists it among the platforms a program serves or the office a technology transitions to
RUNS_BEFORE = re.compile(r"(?:(?:managed|operated|led|executed|overseen|directed|funded|procured)\s+(?:by|under|within|through)|direction of|enables)\s+(?:the\s+)?$", re.I)
RUNS_AFTER = re.compile(r"\)?\s*(?:(?:has|have|is|are|provides?|manages?|leads?|oversees?|continues?|executes?|operates?|directs?|will|plans?)\b"
                        r"|(?:FY ?\d{4}\s+)?funding\b|[^.;:]{0,160}?\b(?:program office (?:is|are|has|have|provides?|manages?|leads?|executes?)\b|portfolio|responsib))", re.I)
DESIGNATOR = re.compile(r"(?<![A-Za-z0-9])(?:AN/)?([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)(?![A-Za-z0-9/-])")
SMALL_WORDS = {"and", "of", "the", "for", "to", "in", "on", "a", "an", "&"}
# ponytail: domain words spelled like designators name no program (C4ISR Equipment is not every C4ISR contract); a list,
# since no shape tells them from NOM4D. Add a word when a line's money is found in another office's contracts.
GENERIC = {"C2", "C3", "C4I", "C4ISR", "C5ISR", "C2ISR", "ISR", "IT", "FMS", "USN", "USMC"}


def year_amounts(a: dict | None) -> dict[str, float | None]:
    """A line's resource summary as one amount per fiscal year, the request year's total under its year."""
    return {k.removesuffix("_total"): v for k, v in (a or {}).items() if re.fullmatch(r"fy\d{4}(?:_total)?", k)}


def book_year(budget: dict) -> int:
    """The fiscal year the newest book was published in: the year whose obligations are set against its amounts."""
    dates = [b["published"] for b in budget.get("books", []) if b.get("published")]
    return fiscal_year(max(dates)) if dates else fiscal_year(date.today().isoformat())


def book_units(budget: dict) -> list[dict]:
    """The newest budget year's programs: every P-1 line item, and every R-1 program element as the program blocks its
    pages fund (the element itself when it prints none)."""
    pb_of = {b["path"]: b.get("pb", "") for b in budget.get("books", [])}
    if not pb_of:
        return []
    pb = max(pb_of.values())
    lines = [row for row in budget.get("lines", []) if pb_of.get(row["book"]) == pb]
    blocks = [b for b in budget.get("programs", []) if pb_of.get(b["book"]) == pb]
    with_blocks = {b["pe"] for b in blocks}
    projects = defaultdict(set)
    for b in blocks:
        projects[b["pe"]].add(b["project"])
    # an element's justification speaks for its blocks only when they are one project; a multi-project element's
    # summary names the offices of some projects, not of all
    element_text = {row["li"]: row["text"] for row in lines if row.get("exhibit") == "R-2" and len(projects[row["li"]]) == 1}
    out, ids = [], Counter()
    for row in lines:
        if row.get("exhibit") == "R-2" and row["li"] in with_blocks:
            continue
        kind = "element" if row.get("exhibit") == "R-2" else "line"
        out.append({"id": f"{kind}:{row['appropriation']}:{row['li']}", "kind": kind, "title": row["title"], "li": row["li"], "project": "",
                    "amounts": year_amounts(row["amounts"]), "text": row["text"], "element_text": "", "problem": row["text"][:1500],
                    "plans": {}, "book": row["book"], "pb": pb})
    for b in blocks:
        out.append({"id": f"block:{b['pe']}:{b['project']}:{norm(b['title'])[:48]}", "kind": "block", "title": b["title"], "li": b["pe"],
                    "project": b["project"], "amounts": b["amounts"], "text": " ".join([b.get("description") or "", *(b.get("plans") or {}).values()]),
                    "element_text": element_text.get(b["pe"], ""), "problem": b.get("description") or "", "plans": b.get("plans") or {},
                    "book": b["book"], "pb": pb})
    for u in out:  # a title the book repeats inside one project still gets its own id
        ids[u["id"]] += 1
        u["id"] += f":{ids[u['id']]}" if ids[u["id"]] > 1 else ""
    return out


def spelled_out(acronym: str, text: str) -> list[str]:
    """The words a text spells an acronym out with, each place it does: Ship Self Defense System for "Ship Self-Defense
    System (SSDS)", MIDS On Ship for "MIDS On Ship (MOS)" (a small word gives its letter or not)."""
    out = []
    for m in re.finditer(rf"\({re.escape(acronym)}\)", text or ""):
        picked, bare, every = [], "", ""
        for w in reversed(re.findall(r"[A-Za-z]+", text[max(0, m.start() - 160):m.start()])):
            picked.insert(0, w)
            bare, every = ("" if w.lower() in SMALL_WORDS else w[0].upper()) + bare, w[0].upper() + every
            if acronym in (bare, every):
                out.append(" ".join(picked))
                break
            if not (acronym.endswith(bare) or acronym.endswith(every)):
                break
    return out


def expansion(acronym: str, text: str) -> str:
    return next(iter(spelled_out(acronym, text)), "")


def stems(words: str, acronym: str) -> tuple[str, ...] | None:
    """The three-letter stems of words that spell an acronym out (CON, AFL, NET, ENT, SER for CANES), else None."""
    every = re.findall(r"[A-Za-z]+", words or "")
    for picked in ([w for w in every if w.lower() not in SMALL_WORDS], every):
        if picked and "".join(w[0] for w in picked).upper() == acronym.upper():
            return tuple(w[:3].upper() for w in picked)
    return None


def similar(x: tuple, y: tuple) -> bool:
    """Two spellings of an acronym sharing half their words or more are one thing (Networks for Network, Cmd for Command)."""
    return 2 * len(set(x) & set(y)) >= max(len(x), len(y))


def meaning(uses: Counter, own: tuple | None) -> tuple[bool, tuple | None]:
    """Whether the books' spelled-out uses of an acronym plainly mean the program, two thirds of them or more its own
    spelling (the commonest, where it spells it nowhere), and that spelling. CEC is Cooperative Engagement Capability 22
    times of 23; MOS is MIDS On Ship twice and Military Operation Specialty and Manpower Operations Systems once each."""
    groups: list[list] = []
    for x, n in uses.most_common():
        g = next((g for g in groups if similar(g[0], x)), None)
        if g:
            g[1] += n
        else:
            groups.append([x, n])
    total = sum(g[1] for g in groups)
    mine = next((g for g in groups if similar(g[0], own)), [own, 0]) if own else (groups[0] if groups else [None, 0])
    return not total or 3 * mine[1] >= 2 * total, mine[0]


def book_spellings(units: list[dict]) -> dict[str, Counter]:
    """How many book lines spell each acronym out each way: MOS is MIDS On Ship in the Navy's procurement book and
    Manpower Operations Systems in the Marine Corps' research book."""
    out: dict[str, Counter] = defaultdict(Counter)
    for u in units:
        mine: set[tuple[str, tuple]] = set()
        for text in {u["title"], u["text"] or "", u.get("element_text") or ""}:
            mine |= {(acr, x) for acr in set(re.findall(r"\(([A-Z]{2,8})\)", text)) for e in spelled_out(acr, text) if (x := stems(e, acr))}
        for acr, x in mine:
            out[acr][x] += 1
    return out


def unit_names(title: str, text: str) -> list[str]:
    """What a program is called in its own line: the acronym its title ends with (NTCSS), the title's designators and
    all-capital words (SPQ-9B, SLQ-32, CANES), the title's initials when the text writes them in parentheses (CEC for
    Cooperative Engagement Capability), and the title itself when it has three words or more. An acronym inside the
    title names a term, not the program: Long-Range Over the Horizon (OTH) Communications is not OTH."""
    names = {a for a in TITLE_ACRONYM.findall(title) if title.rstrip().endswith(f"({a})") and a not in GENERIC}
    bare = " ".join(TITLE_ACRONYM.sub(" ", title).split())
    # A capitals word stands out as a name in a title that has small letters, or is the whole title (CANES). In a title
    # all in capitals it is a name only where the program's own text, written in small letters, still writes it in
    # capitals (LCAC SLEP); in CENTER FOR NAVAL ANALYSES, NAVY no word is.
    stands_out = any(c.islower() for c in bare) or len(bare.split()) == 1
    written = (lambda tok: bool(re.search(rf"(?<![A-Za-z0-9]){tok}(?![A-Za-z0-9])", text))) if any(c.islower() for c in text or "") else (lambda tok: False)
    for tok in DESIGNATOR.findall(bare):
        if tok in GENERIC:
            continue
        if (len(tok) >= 3 and any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)) or (tok.isalpha() and len(tok) >= 4 and (stands_out or written(tok))):
            names.add(tok)
    words = [w for w in re.split(r"[\s/]+", bare) if w and w.lower() not in SMALL_WORDS]
    initials = "".join(w[0] for w in words if w[0].isalpha()).upper()
    if len(initials) >= 3 and f"({initials})" in text:
        names.add(initials)
    if len(bare.split()) >= 3:
        names.add(bare)
    return sorted(names)


def unit_terms(names: list[str]) -> list[str]:
    """The spend search terms: the names, at most three, the shortest (the acronyms) first."""
    return sorted(names, key=lambda n: (" " in n, len(n), n))[:3]


def office_patterns(seed: dict) -> list[tuple[re.Pattern, str]]:
    """Each program office's codes as a book prints them (PMW 160, PMW-160, IWS 2.0, PEO C4I), one pattern per office
    with its codes as alternatives, longest first, so one mention counts once however many codes spell it."""
    out = []
    for n in seed.get("nodes", []):
        if n["type"] not in LEADS:
            continue
        codes = {v for v in (n.get("codes") or {}).values() if isinstance(v, str)} | {a["text"] if isinstance(a, dict) else str(a) for a in n.get("aliases") or []}
        alts = set()
        for code in codes:
            parts = [x for x in re.split(r"[\s_-]+", code.strip()) if x]
            if parts and len(code) <= 24 and (any(c.isdigit() for c in code) or (2 <= len(parts) <= 3 and parts[0].isupper())):
                alts.add(r"[\s_-]?".join(map(re.escape, parts)))
        if alts:
            out.append((re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(sorted(alts, key=lambda a: (-len(a), a))) + r")(?![A-Za-z0-9]|\.\d)"), n["id"]))
    return out


def owned_needs(corpus: dict, seed: dict) -> list[dict]:
    """The frozen corpus's requirements whose owner is a program office, with the owner's memory id."""
    lead = {uid("org", n["id"]): n["id"] for n in seed.get("nodes", []) if n["type"] in LEADS}
    return [{"key": n["key"], "title": n["title"], "office": lead[n["owner_id"]]} for n in corpus.get("needs", []) if n.get("owner_id") in lead]


def leading(counts: Counter) -> tuple[str, int, int]:
    total = sum(counts.values())
    if not total:
        return "", 0, 0
    top, n = counts.most_common(1)[0]
    return (top if n / total >= SHARE else ""), n, total


def around(text: str, m: re.Match, width: int = 160) -> str:
    start = max(text.rfind(". ", 0, m.start()) + 2, m.start() - width, 0)
    stop = text.find(". ", m.end())
    return text[start:min(stop + 1 if stop >= 0 else len(text), m.end() + width)].strip()


def attribute(unit: dict, names: list[str], patterns: list[tuple[re.Pattern, str]], needs: list[dict], label: dict[str, str],
              spell: dict[str, set] | None = None) -> dict:
    """The office a book program's money is managed in and how that was reached, or its candidates."""
    candidates = []
    for field, where in (("text", "the program's own text"), ("element_text", "its program element's justification")):
        body = f"{unit['title']}. {unit[field]}" if field == "text" else unit[field] or ""
        mentions = [(m, node) for rx, node in patterns for m in rx.finditer(body)]
        hits = [(m, node) for m, node in mentions if RUNS_BEFORE.search(body, max(0, m.start() - 40), m.start()) or RUNS_AFTER.match(body, m.end())]
        office, n, total = leading(Counter(node for _, node in hits))
        if office:
            m = next(m for m, node in hits if node == office)
            return {"office": office, "how": "named", "basis": f"{where} says {m.group(0)} runs it ({n} of {total} such program office mentions): '{around(body, m)}'", "candidates": []}
        candidates += [{"office": o, "mentioned": c} for o, c in Counter(node for _, node in mentions).most_common(3)]
    matched: dict[str, tuple[dict, str]] = {}
    for name in names:  # OTH-WS is another designator than OTH
        own = stems(expansion(name, f"{unit['title']}. {unit['text']} {unit.get('element_text') or ''}"), name)
        if name.isalpha() and not meaning((spell or {}).get(name, Counter()), own)[0]:
            continue  # the books use it for other things: MOS requirements are MIDS On Ship, not Manpower Operations Systems
        rx = re.compile(rf"(?<![A-Za-z0-9-]){re.escape(name)}(?![A-Za-z0-9]|-[A-Za-z0-9])", re.I if " " in name else 0)
        for need in needs:
            if need["key"] not in matched and rx.search(need["title"]):
                matched[need["key"]] = (need, name)
    counts = Counter(need["office"] for need, _ in matched.values())
    office, n, total = leading(counts)
    # a plain capital word (NATO, VIRGINIA) is shared by more than one program: three requirements, not two
    defined = lambda name: " " in name or not name.isalpha() or unit["title"].rstrip().endswith(f"({name})") or f"({name})" in unit["text"]  # noqa: E731
    if office and n >= (2 if any(defined(name) for need, name in matched.values() if need["office"] == office) else 3):
        keys = sorted(k for k, (need, _) in matched.items() if need["office"] == office)
        said = sorted({name for need, name in matched.values() if need["office"] == office})
        return {"office": office, "how": "needs", "candidates": [],
                "basis": f"{n} of the {total} program office requirements whose titles name {', '.join(said)} are owned by {label.get(office, office)} (e.g. {', '.join(keys[:2])})"}
    return {"office": "", "how": "none", "basis": "", "candidates": candidates + [{"office": o, "requirements": c} for o, c in counts.most_common(3)]}


def book_ties(units: list[dict]) -> list[dict]:
    seed = json.loads(SEED.read_text(encoding="utf-8")) if SEED.exists() else {}
    corpus = json.loads(CORPUS.read_text(encoding="utf-8")) if CORPUS.exists() else {}
    patterns, needs = office_patterns(seed), owned_needs(corpus, seed)
    label = {n["id"]: (n.get("codes") or {}).get("office_code") or n["name"] for n in seed.get("nodes", [])}
    spell = book_spellings(units)
    return [attribute(u, unit_names(u["title"], u["text"]), patterns, needs, label, spell) for u in units]


def build_book_programs(units: list[dict], ties: list[dict], by_term: dict[str, list[dict]], fy: int, books: dict[str, dict]) -> dict:
    names_of = [unit_names(u["title"], u["text"]) for u in units]
    texts = {u["id"]: f"{u['title']}. {u['text'] or ''}" for u in units}
    spell = book_spellings(units)
    holders: dict[str, set[str]] = defaultdict(set)
    for u, names in zip(units, names_of):
        for n in names:
            holders[n].add(u["id"])
    office_of = {u["id"]: tie["office"] for u, tie in zip(units, ties)}
    out = []
    for u, tie, names in zip(units, ties, names_of):
        # A name two lines share names neither: SM-6 is the Block IB development and the Block IA update, and the missile's
        # production buy ("FY26 SM-6 BLK IA TAC AUR") is a third line's money. The line keeps the names only it carries.
        own = [n for n in names if holders[n] == {u["id"]}]
        acronym = next((n for n in own if " " not in n), "")
        full = (expansion(acronym, texts[u["id"]]) if acronym else "") or next((n for n in own if " " in n), "")
        title = u["title"] if holders.get(u["title"], {u["id"]}) == {u["id"]} else ""
        spend = spend_found(unit_terms(names), title, acronym, full, by_term, spell.get(acronym)) if tie["office"] and (title or acronym or full) else None
        budget_fy = u["amounts"].get(f"fy{fy}")
        left = {"amount_musd": round(budget_fy - spend["obligated"] / 1e6, 3), "basis": LEFT_BASIS} if spend and spend["searched"] and budget_fy is not None else None
        book = books.get(u["book"], {})
        out.append({"id": u["id"], "title": u["title"], "status": "", "office_as_listed": "", "manager": "", "manager_role": "", "path": "", "topics": [],
                    "names": names, "office": tie["office"], "office_basis": {k: tie[k] for k in ("how", "basis", "candidates")}, "url": book.get("url", ""),
                    "listing_source": {"url": book.get("url", ""), "retrieved_at": book.get("retrieved_at", ""), "sha256": book.get("sha256", "")},
                    "budget": {"match": u["kind"], "amounts_musd": u["amounts"],
                               "blocks": [{"pe": u["li"], "project": u["project"], "title": u["title"], "amounts": u["amounts"], "book": u["book"], "pb": u["pb"]}]},
                    "problem": u["problem"], "plans": u["plans"], "change_statement": next((m.group(1).strip() for t in u["plans"].values() if (m := CHANGE_RE.search(t))), ""),
                    "spend": spend, "left": left})
    tied = [p for p in out if p["office"]]
    # A name every line of which one office holds (CANES: the line, CANES Intell, CANES Integration, all PMW 160) finds
    # that office's money, counted once, though no single line can claim it.
    found: dict[str, list[dict]] = defaultdict(list)
    for n, ids in sorted(holders.items()):
        offices = {office_of[i] for i in ids}
        if len(offices) == 1 and "" not in offices and n in by_term:
            full = n if " " in n else next((e for i in sorted(ids) if (e := expansion(n, texts[i]))), "")  # SSDS as its lines spell it
            found[offices.pop()] += spend_found([n], n if " " in n else "", "" if " " in n else n, full, by_term, spell.get(n))["strong"]
    return {"fiscal_year": fy, "listing_retrieved_at": "", "left_basis": LEFT_BASIS, "programs": out, "offices": office_money(tied, fy, found),
            "budget_blocks_without_a_listed_program": [],
            "summary": {"programs": len(out), "current": len(out), "tied_to_an_office": len(tied),
                        "tied_by": dict(sorted(Counter(p["office_basis"]["how"] for p in tied).items())), "offices": len({p["office"] for p in tied}),
                        "current_with_budget": len(out), "current_with_spend_found": sum(bool(p["spend"] and p["spend"]["transactions"]) for p in out),
                        "current_with_manager": 0, "budget_blocks_unmatched": 0}}


def build(argv: list[str]) -> int:
    rows = manifest_rows()
    budget = json.loads(BUDGET.read_text(encoding="utf-8")) if BUDGET.exists() else {}
    if LISTING:
        listing = newest(rows, LISTING_NOTE)
        if not listing:
            print("no saved program listing; run `programs.py collect` first", file=sys.stderr)
            return 1
        fy = fiscal_year(listing["retrieved_at"])
        progs = listing_programs(json.loads((ROOT / listing["path"]).read_text(encoding="utf-8")))
        seed = json.loads(SEED.read_text(encoding="utf-8")) if SEED.exists() else {}
        result = build_programs(progs, budget_blocks(budget.get("programs", [])), saved_spend(rows, fy), office_ids(seed), fy, listing)
    else:
        units, fy = book_units(budget), book_year(budget)
        if not units:
            print(f"{P['key']}: no program listing and no budget lines read; nothing to build")
            return 0
        result = build_book_programs(units, book_ties(units), saved_spend(rows, fy), fy, {b["path"]: b for b in budget.get("books", [])})
    text = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.name} differs from a fresh build; run `programs.py build` to regenerate", file=sys.stderr)
            return 1
        print(f"{OUT.name} matches a fresh build")
        return 0
    OUT.write_text(text, encoding="utf-8")
    s = result["summary"]
    if not LISTING:
        print(f"{s['programs']} book program(s): {s['tied_to_an_office']} tied to {s['offices']} office(s) {s['tied_by']}, "
              f"{s['current_with_spend_found']} with FY{fy} contract spend found -> {OUT.relative_to(ROOT)}")
        return 0
    print(f"{s['current']} current program(s): {s['current_with_manager']} with a manager, {s['current_with_budget']} matched to the "
          f"budget book, {s['current_with_spend_found']} with FY{fy} contract spend found -> {OUT.relative_to(ROOT)}")
    return 0


def selfcheck() -> int:
    fields = {"id": "nid", "title": "title", "status": "st", "office": "off", "manager": ("f", "l"), "manager_role": "r", "path": "p", "topics": "t"}
    recs = [{"nid": "1", "title": "ALIAS", "st": "Current", "off": "Tactical Technology Office", "f": "Ann", "l": "Lee", "r": "Program Manager", "p": "/x", "t": "Air"},
            {"nid": "1", "title": "ALIAS", "st": "Current", "off": "Tactical Technology Office", "f": "Ann", "l": "Lee", "r": "Program Manager", "p": "/x", "t": "Autonomy"},
            {"nid": "2", "title": "CASTLE", "st": "Current", "off": "Director&#039;s Office", "f": "", "l": "", "r": "", "p": "", "t": ""}]
    progs = listing_programs(recs, fields)
    assert [p["title"] for p in progs] == ["CASTLE", "ALIAS"] and progs[1]["topics"] == ["Air", "Autonomy"] and progs[0]["office_as_listed"] == "Director's Office"
    blocks = [{"pe": "0602702E", "project": "TT-07", "title": "Aircrew Labor In-Cockpit Automation System (ALIAS)", "acronym": "ALIAS",
               "amounts": {"fy2025": 12.0, "fy2026": 8.5, "fy2027": None}, "description": "Pilots carry too many tasks.", "plans": {"FY 2026 Plans": "- Fly."}, "book": "b", "pb": "2027"},
              {"pe": "0602303E", "project": "IT-03", "title": "Aircrew Labor In-Cockpit Automation System (ALIAS)", "acronym": "ALIAS",
               "amounts": {"fy2025": None, "fy2026": 1.5, "fy2027": 2.0}, "description": "", "book": "b", "pb": "2027",
               "plans": {"FY 2026 Plans": "- Fly longer.", "FY 2027 Plans": "- Test. FY 2026 to FY 2027 Increase/Decrease Statement: The FY 2027 increase reflects flight tests."}},
              {"pe": "0601101E", "project": "CCS-02", "title": "Castle Walls", "acronym": "", "amounts": {"fy2026": 3.0}, "description": "", "plans": {}, "book": "b", "pb": "2027"}]
    matched, how = match_blocks("ALIAS", blocks)
    assert how == "acronym" and len(matched) == 2 and summed(matched) == {"fy2025": 12.0, "fy2026": 10.0, "fy2027": 2.0}
    assert match_blocks("castle walls", blocks)[1] == "title" and match_blocks("CASTLE", blocks)[1] == "none"
    assert spend_terms(progs[1], matched) == ["ALIAS", "Aircrew Labor In-Cockpit Automation System"]
    full = "Aircrew Labor In-Cockpit Automation System"
    assert strength("PROGRAM: DN4D66 - NOVEL ORBITAL (NOM4D)", "NOM4D", "NOM4D", "") == "strong"
    assert strength("NOM4D PHASE 2 OPTION", "NOM4D", "NOM4D", "") == "strong", "no word spells NOM4D"
    assert strength("CASTLE PHASE 2", "CASTLE", "", "") == "weak" and strength("THE CASTLE PROGRAM", "CASTLE", "", "") == "strong"
    assert strength("AIRCREW LABOR IN-COCKPIT AUTOMATION SYSTEM PHASE 3", "ALIAS", "ALIAS", full) == "strong"
    assert strength("CASTLES OF SAND", "CASTLE", "", "") == "none"
    src = {"url": SPEND_URL, "retrieved_at": "2026-09-29T00:00:00Z", "sha256": "h"}
    by_term = {"ALIAS": [{"Award ID": "A1", "Recipient Name": "Co", "Action Date": "2026-03-01", "Transaction Amount": 2_000_000.0,
                          "Transaction Description": "ALIAS PROGRAM PHASE 3", "_source": src},
                         {"Award ID": "A2", "Recipient Name": "Co", "Action Date": "2026-04-01", "Transaction Amount": 5.0,
                          "Transaction Description": "ALIAS NOT NAMED PLAINLY", "_source": src}],
               "Aircrew Labor In-Cockpit Automation System": [{"Award ID": "A1", "Recipient Name": "Co", "Action Date": "2026-03-01",
                                                               "Transaction Amount": 2_000_000.0, "Transaction Description": "ALIAS PROGRAM PHASE 3", "_source": src}]}
    res = build_programs(progs, blocks, by_term, {norm("Tactical Technology Office"): "office:tto"}, 2026, {"retrieved_at": "2026-09-29T00:00:00Z"}, current="Current")
    alias = next(p for p in res["programs"] if p["title"] == "ALIAS")
    assert alias["office"] == "office:tto" and alias["spend"]["obligated"] == 2_000_000.0 and alias["spend"]["transactions"] == 1, alias["spend"]
    assert len(alias["spend"]["weak"]) == 1 and alias["left"]["amount_musd"] == 8.0 and "upper bound" in alias["left"]["basis"]
    assert alias["problem"] == "Pilots carry too many tasks." and alias["plans"]["FY 2026 Plans"] == "- Fly longer.", "plans merge across elements"
    assert alias["change_statement"] == "The FY 2027 increase reflects flight tests."
    castle = next(p for p in res["programs"] if p["title"] == "CASTLE")
    assert castle["left"] is None and castle["budget"]["match"] == "none", "no budget match, no money left stated"
    assert [b["title"] for b in res["budget_blocks_without_a_listed_program"]] == ["Castle Walls"]
    assert fiscal_year("2026-09-29") == 2026 and fiscal_year("2026-10-01") == 2027 and fy_window(2026) == ("2025-10-01", "2026-09-30")

    # book mode: the Navy lists no programs, so the book's lines and program blocks are the programs
    assert year_amounts({"prior_years": 1.0, "fy2025": 2.0, "fy2027_base": 3.0, "fy2027_total": 4.0}) == {"fy2025": 2.0, "fy2027": 4.0}
    assert unit_names("Navy Multiband Terminal (NMT)", "") == ["NMT", "Navy Multiband Terminal"]
    assert unit_names("AN/SQQ-89 Surface ASW Combat System", "") == ["AN/SQQ-89 Surface ASW Combat System", "SQQ-89"]
    assert unit_names("Cooperative Engagement Capability", "procures Cooperative Engagement Capability (CEC) kits") == ["CEC", "Cooperative Engagement Capability"]
    assert unit_names("Other (Space)", "") == [], "a word in parentheses is no acronym"
    assert unit_names("CENTER FOR NAVAL ANALYSES, NAVY", "") == ["CENTER FOR NAVAL ANALYSES, NAVY"], "no capitals word stands out"
    assert unit_names("MANPOWER OPERATIONS SYSTEMS (MOS)", "") == ["MANPOWER OPERATIONS SYSTEMS", "MOS"]
    assert unit_names("LCAC SLEP", "The LCAC Service Life Extension Program (SLEP) extends the craft service life") == ["LCAC", "SLEP"]
    assert unit_names("CANES", "") == ["CANES"] and unit_names("C4ISR Equipment", "") == [], "a whole-title word is a name; a domain word is not"
    assert strength("MISSILE DEFENSE AGENCY (MDA) SUPPORT", "Maritime Domain Awareness (MDA)", "MDA", "Maritime Domain Awareness", True) == "weak"
    assert strength("MARITIME DOMAIN AWARENESS (MDA) SUPPORT", "Maritime Domain Awareness (MDA)", "MDA", "Maritime Domain Awareness") == "strong"
    assert strength("FY25 MDA PROJECT P-693, PDI: GUAM DEFENSE SYSTEM", "", "MDA", "") == "weak", "a short acronym alone is never strong"
    assert expansion("SSDS", "integrates the Ship Self-Defense System (SSDS) MK2") == "Ship Self Defense System"
    assert expansion("CEC", "the Cooperative Engagement Capability (CEC) is") == "Cooperative Engagement Capability" and expansion("MDA", "no (MDA) here") == ""
    assert strength("SHIP SELF-DEFENSE SYSTEM (SSDS) COMBAT SYSTEMS ENGINEERING AGENT", "", "SSDS", "Ship Self Defense System") == "strong"
    seed = {"nodes": [{"id": "pmw:160", "type": "program_office", "name": "PMW 160 Tactical Networks", "codes": {"office_code": "PMW 160"}, "aliases": [{"text": "PMW-160"}]},
                      {"id": "pmw:170", "type": "program_office", "name": "PMW/A 170", "codes": {"office_code": "PMW/A 170"}, "aliases": ["PMW 170"]},
                      {"id": "peo:iws", "type": "program_executive_office", "name": "PEO IWS", "codes": None, "aliases": [{"text": "PEO IWS"}, {"text": "an alias far longer than any code"}]},
                      {"id": "office:tto", "type": "office", "name": "TTO", "codes": {"office_code": "TTO 1"}}]}
    patterns = office_patterns(seed)
    assert {node for _, node in patterns} == {"pmw:160", "pmw:170", "peo:iws"}, "offices only, and codes only"
    found = lambda text: sorted({node for rx, node in patterns if rx.search(text)})  # noqa: E731
    assert found("funds PMW160 and PMW-160") == ["pmw:160"] and found("PMW 1601 or PMW 160.1") == [] and found("PEO IWS 1.0") == ["peo:iws"]
    needs = [{"key": f"n{i}", "title": t, "office": o} for i, (t, o) in enumerate([("NMT spares", "pmw:170"), ("NMT antenna repair", "pmw:170"),
                                                                                 ("Navy multiband terminal kits", "pmw:170"), ("NMT shipping", "pmw:160"),
                                                                                 ("CANES racks", "pmw:160"), ("canes cables", "pmw:170")])]
    unit = {"title": "Navy Multiband Terminal (NMT)", "text": "Procures terminals.", "element_text": ""}
    tie = attribute(unit, unit_names(unit["title"], unit["text"]), patterns, needs, {"pmw:170": "PMW/A 170"})
    assert tie["office"] == "pmw:170" and tie["how"] == "needs" and tie["basis"].startswith("3 of the 4 program office requirements"), tie
    tie = attribute({"title": "CANES", "text": "", "element_text": ""}, ["CANES"], patterns, needs, {})
    assert tie["office"] == "" and tie["how"] == "none", "one requirement is not two, and an acronym matches in capitals only"
    text = "Upgrades are managed by PMW 170. PMW-170 executes the buys and PMW 160 provides racks. Continue to support PMW 160 testing."
    tie = attribute({"title": "Sensors", "text": text, "element_text": ""}, [], patterns, needs, {})
    assert tie["office"] == "pmw:170" and tie["how"] == "named" and "(2 of 3 such program office mentions)" in tie["basis"], tie
    tie = attribute({"title": "Radar", "text": "Upgrades.", "element_text": "Prototypes are operated under PEO IWS."}, [], patterns, needs, {})
    assert tie["office"] == "peo:iws" and "its program element's justification" in tie["basis"], tie
    served = "Improvements for key platforms: DDG 51, CVN 78, and PEO IWS systems. Provide support to PMW 160 for transition."
    tie = attribute({"title": "Metals", "text": served, "element_text": ""}, [], patterns, needs, {})
    assert tie["office"] == "" and {c["office"] for c in tie["candidates"]} == {"peo:iws", "pmw:160"}, "served, not run"
    assert "OTH" not in unit_names("Long-Range Over the Horizon (OTH) Communications", ""), "an acronym inside the title names a term"
    oth = [{"key": f"o{i}", "title": f"OTH-WS spares {i}", "office": "iws:3.0"} for i in range(3)]
    assert attribute({"title": "Radios", "text": "", "element_text": ""}, ["OTH"], patterns, oth, {})["how"] == "none", "OTH-WS is not OTH"
    nato = [{"key": f"t{i}", "title": t, "office": o} for i, (t, o) in enumerate([("NATO link support 5", "pmw:150"), ("NATO link support 6", "pmw:150"), ("NATO missile", "iws:12.0")])]
    assert attribute({"title": "NATO Cooperative R & D", "text": "", "element_text": ""}, ["NATO"], patterns, nato, {})["how"] == "none", "a plain word needs three"
    assert attribute({"title": "NATO Cooperative R & D", "text": "", "element_text": ""}, ["NATO"], patterns, nato + [dict(nato[0], key="t9")], {})["office"] == "pmw:150"
    budget = {"books": [{"path": "old.pdf", "pb": "2026"}, {"path": "opn.pdf", "pb": "2027"}, {"path": "rdt.pdf", "pb": "2027"}],
              "lines": [{"book": "old.pdf", "appropriation": "1810N", "li": "0950", "title": "Old", "amounts": {}, "text": "", "exhibit": "P-40"},
                        {"book": "opn.pdf", "appropriation": "1810N", "li": "0950", "title": "Strategic Platform Support Equip",
                         "amounts": {"fy2026": 38.0, "fy2027_total": 72.2}, "text": "Funding enables PMS396.", "exhibit": "P-40"},
                        {"book": "rdt.pdf", "appropriation": "1319", "li": "0601103N", "title": "University Research Initiatives", "amounts": {}, "text": "Basic research.", "exhibit": "R-2"},
                        {"book": "rdt.pdf", "appropriation": "1319", "li": "0601153N", "title": "Defense Research Sciences", "amounts": {"fy2026": 5.0}, "text": "Sciences.", "exhibit": "R-2"}],
              "programs": [{"book": "rdt.pdf", "pe": "0601103N", "project": "1101", "title": "MURI", "amounts": {"fy2026": 51.1}, "description": "Teams.", "plans": {"FY 2027 Plans": "- Fund."}},
                           {"book": "rdt.pdf", "pe": "0601103N", "project": "1101", "title": "MURI", "amounts": {"fy2026": 1.0}, "description": "", "plans": {}}]}
    units = book_units(budget)
    assert [u["id"] for u in units] == ["line:1810N:0950", "element:1319:0601153N", "block:0601103N:1101:MURI", "block:0601103N:1101:MURI:2"], [u["id"] for u in units]
    assert units[0]["amounts"] == {"fy2026": 38.0, "fy2027": 72.2} and units[0]["problem"] == "Funding enables PMS396."
    assert units[2]["element_text"] == "Basic research." and units[2]["text"] == "Teams. - Fund." and units[2]["problem"] == "Teams."
    budget["programs"].append({"book": "rdt.pdf", "pe": "0601103N", "project": "1102", "title": "Other", "amounts": {}, "description": "", "plans": {}})
    assert all(u["element_text"] == "" for u in book_units(budget) if u["kind"] == "block"), "a two-project element speaks for neither"
    # a name two lines share counts for neither line; the office holding both takes what it finds, once
    tx = lambda desc, amount, award: {"Award ID": award, "Recipient Name": "R", "Action Date": "2026-03-01", "Transaction Amount": amount,  # noqa: E731
                                      "Transaction Description": desc, "_source": src}
    blocks2 = [{"id": f"b{i}", "title": t, "text": "", "amounts": {"fy2026": a}, "kind": "block", "li": "0604", "project": "1", "book": "r", "pb": "2027",
                "problem": "", "plans": {}} for i, (t, a) in enumerate((("SM-6 BLK IB Development", 80.0), ("SM-6 BLK IAU TTP", 40.0)))]
    tie2 = [{"office": "iws:3.0", "how": "named", "basis": "", "candidates": []}] * 2
    aur, dev = tx("FY26 SM-6 BLK IA TAC AUR", 335e6, "N1"), tx("SM-6 BLK IB DEVELOPMENT", 5e6, "N2")
    res2 = build_book_programs(blocks2, tie2, {"SM-6": [aur, dev], "SM-6 BLK IB Development": [dev], "SM-6 BLK IAU TTP": []}, 2026, {})
    assert [p["spend"]["obligated"] for p in res2["programs"]] == [5e6, 0], "the production buy is neither block's"
    iws = res2["offices"]["iws:3.0"]
    assert iws["spend"]["obligated"] == 340e6 and iws["left"] is None and "passes the FY2026 amount" in iws["left_note"], iws
    assert office_money([dict(res2["programs"][1], office="iws:3.0")], 2026)["iws:3.0"]["left"]["amount_musd"] == 40.0
    # the contracts spell MDA two ways, so only Maritime Domain Awareness written out is the program's; SSDS one way
    mda = spend_found(["MDA"], "", "MDA", "Maritime Domain Awareness", {"MDA": [tx("MISSILE DEFENSE AGENCY (MDA) FACILITIES", 7e5, "M1"),
                                                                                tx("MARITIME DOMAIN AWARENESS (MDA) SUPPORT", 3e6, "M2")]})
    assert mda["obligated"] == 3e6 and len(mda["weak"]) == 1, mda
    assert expansion("MOS", "fields the MIDS On Ship (MOS) terminal") == "MIDS On Ship"
    assert not similar(stems("Maritime Domain Awareness", "MDA"), stems("Missile Defense Agency", "MDA"))
    assert similar(stems("Consolidated Afloat Networks and Enterprise Services", "CANES"), stems("Consolidated Afloat Network Enterprise System", "CANES"))
    cec = Counter({stems("Cooperative Engagement Capability", "CEC"): 22, stems("Central Electronics Chassis", "CEC"): 1})
    assert meaning(cec, None) == (True, ("COO", "ENG", "CAP")) and not meaning(cec, stems("Central Electronics Chassis", "CEC"))[0]
    two = {"MOS": Counter({stems("MIDS On Ship", "MOS"): 2, stems("Manpower Operations Systems", "MOS"): 1})}
    mos_needs = [{"key": f"r{i}", "title": f"MOS Mod BPA Call {i}", "office": "pmw:150"} for i in range(3)]
    mos_unit = {"title": "MANPOWER OPERATIONS SYSTEMS (MOS)", "text": "", "element_text": ""}
    assert attribute(mos_unit, ["MANPOWER OPERATIONS SYSTEMS", "MOS"], [], mos_needs, {})["office"] == "pmw:150"
    assert attribute(mos_unit, ["MANPOWER OPERATIONS SYSTEMS", "MOS"], [], mos_needs, {}, two)["office"] == "", "an acronym the books spell two ways ties nothing"
    mos = spend_found(["MOS"], "", "MOS", "Manpower Operations Systems", {"MOS": [tx("MILITARY OCCUPATIONAL SPECIALTY (MOS) MODERNIZATION", 7e5, "O1")]})
    assert mos["obligated"] == 0, "a contract spelling the acronym another way than the program does is not the program's"
    canes = [tx(f"CONSOLIDATED AFLOAT {n} (CANES) SUPPORT", 1e6, f"C{i}") for i, n in enumerate(("NETWORKS AND ENTERPRISE SERVICES", "NETWORK ENTERPRISE SYSTEM"))]
    assert spend_found(["CANES"], "CANES", "CANES", "", {"CANES": canes})["obligated"] == 2e6, "Network for Networks is one spelling"
    ssds = spend_found(["SSDS"], "", "SSDS", "", {"SSDS": [tx("SHIP SELF-DEFENSE SYSTEM (SSDS) CSEA", 28e6, "S1"), tx("SSDS MK2 CEILING", 1e6, "S2")]})
    assert ssds["obligated"] == 28e6 and len(ssds["weak"]) == 1, ssds
    customer = "The project prototypes new sonar arrays for PMS 401, the Submarine Acoustics Program Office in PEO UWS."
    assert attribute({"title": "Arrays", "text": customer, "element_text": ""}, [], office_patterns({"nodes": [{"id": "pms:401", "type": "program_office", "codes": {"office_code": "PMS 401"}}]}), [], {})["office"] == ""
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"collect": collect, "build": build}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

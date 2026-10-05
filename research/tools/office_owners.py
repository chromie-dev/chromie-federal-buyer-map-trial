#!/usr/bin/env python3
"""Per program office: who owns which problem, who would champion a fix, who holds the budget; the budget line by line
(requested, enacted, spent this fiscal year, roughly left); what the office bought and is working on now. Read from the
frozen record and the saved files alone, for whichever agency profile is set, and written as one JSON and one Markdown
report under the profile's results folder.

Three kinds of statement are kept apart, as the working agreement asks, and the JSON keeps them under three keys:

- `facts`: what a saved page, sheet, feed or record states, each row with its citation (URL, the ledger's retrieval date
  and sha256 prefix, a locator into the document, the passage or field). A listed role is a fact about the listing and
  nothing more: it never says who decides a buy.
- `inferences`: readings built from named facts, each row labelled `inference` with the rule it rests on and the fact ids
  it used. "Who would champion a fix" is always an inference: no page says who champions anything. "Who holds the budget"
  is an ordered inference: the line (statutory), the office the line's programs are placed in (by a stated basis), the
  office's listed head, the office above it. "Roughly what is left" is enacted less the obligations the saved feed shows,
  with the reasons it is a floor.
- `recommendations`: how to use the above, drafted and unreviewed.

A family the profile never collected is stated as a boundary ("0 budget lines read; no book saved for this profile")
before any section that would otherwise read "none".

    python research/tools/office_owners.py build [--as-of DATE] [--check]   # research/<...>/results/office_owners.{json,md}
    python research/tools/office_owners.py page "PMW 160"                    # one office's section, printed
    python research/tools/office_owners.py collect [--fy 2026]               # (network) the year's USAspending totals for the funding subtier
    python research/tools/office_owners.py --selfcheck
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from fetch import kept_page  # noqa: E402
from agency import EVENTS, KEY, MANIFEST, MEMORY, NOTE_TAG, P, RESULTS  # noqa: E402
from agency_layers_sql import uid  # noqa: E402  (the corpus names an organization by uuid5 of its seed id)
from backtest import CORPUS, GENERIC  # noqa: E402
from lrae_package import FPDS_PAGE, fpds_entries, fpds_history, manifest_rows, url_index  # noqa: E402
from fpds_sweep import FIELDS, saved_pages, windows  # noqa: E402
from pages import Layer, OWNER_TYPES, upward  # noqa: E402
from pulse import load_people, office_name  # noqa: E402

OUT_JSON = RESULTS / "office_owners.json"
OUT_MD = RESULTS / "office_owners.md"
BUDGET_LINES = EVENTS / "budget_lines.json"
BUDGET_MEASURES = EVENTS / "budget_measures.json"
CONTACT_OBSERVATIONS = MEMORY / "contact_observations.json"
SEED = MEMORY / "organization_seed.json"
DNA = RESULTS / "buying_dna.json"
NOTICE_KINDS = RESULTS / "notice_kinds.json"
GENERATOR = "office_owners"
USASPENDING = "https://api.usaspending.gov/api/v2/search/"
USASPENDING_NOTE = f"office owners{NOTE_TAG}: usaspending"

# The basis a budget line is placed in an office on, most direct first. The first is a statement of the document; the
# next three rest on a program name shared with a stated record and are inferences of the placement; the last is a fact
# about the record: nothing places the line, and it is listed, not guessed. A person page that names the person's office and lists
# the program states the placement on one page, so it is documented too. `program_rows_under_the_line` is the aggregate: the
# line's named programs were placed (each by its own basis) and the line follows them.
JOIN_BASIS = ("office_code_in_text", "program_named_on_people_page", "program_named_in_forecast_row",
              "program_named_in_notice_or_award", "program_rows_under_the_line", "unplaced")
DOCUMENTED = {"office_code_in_text", "program_named_on_people_page"}  # one page states the office and the program; the placement is its statement
CHAMPION_RULE = ("a program manager (or the office's listed head where no program manager is listed) whose stated program or "
                 "office appears by name in a notice, a forecast-row move or a topic dated inside the fiscal year is the person "
                 "who can put a new performer on contract this year; nothing on any page says who champions anything, and this "
                 "row says no more than that")
HOLDER_RULE = ("Congress funds the line (statutory); the line's programs are placed in an office on the basis each row states "
               "(execution); the office's listed head and the office above it are the approval points the listing implies. The "
               "office as such holds no line; unplaced lines on the same program element may also be the office's")
REMAINING_CAVEATS = ("FPDS posts an action up to 90 days after it is signed, so the last quarter is thin",
                     "research, development, test and evaluation money is available for two years, so this year's enacted "
                     "amount can still obligate next year",
                     "the saved feed holds the base awards each office signed and the histories of the awards followed by the "
                     "changes stage; modifications on other awards are not in it, so the obligations are a floor")
ROLE_ORDER = ("acquisition_leader", "program_manager", "deputy_program_manager", "other")
STAFF_ROLE_SENTENCE = re.compile(r"\b(director|manager|leads?|responsible|budget|financial|oversees|portfolio)\b", re.I)
PIID_IN_TITLE = re.compile(r"^Incumbent contract (\S+) ")
PLACED_BY = re.compile(r"placed by ([^;]+?)(?: [A-Z0-9]{6,} |$)")
HEX32 = re.compile(r"[0-9a-f]{32}")
AWARD_ID = re.compile(r"(?:CONT|ASST)_[A-Z]+_[A-Z0-9_.-]+")
WORD = re.compile(r"[a-z0-9]+")


# ------------------------------------------------------------------ dates

def fiscal_year(day: str) -> int:
    d = date.fromisoformat(day[:10])
    return d.year + (1 if d.month >= 10 else 0)


def fy_window(fy: int) -> tuple[str, str]:
    return f"{fy - 1}-10-01", f"{fy}-09-30"


def in_window(day: str, window: tuple[str, str]) -> bool:
    return bool(day) and window[0] <= day[:10] <= window[1]


# ------------------------------------------------------------------ the ledger and citations

class Ledger:
    """The document ledger read once: the newest saved copy of each URL, and the identifiers (SAM.gov notice ids, USAspending
    award ids, PIIDs) a saved URL carries, so a record event whose page address differs from the API address it was saved
    from still finds its bytes."""

    def __init__(self, rows: list[dict] | None = None):
        rows = manifest_rows() if rows is None else rows
        self.by_url: dict[str, dict] = {}
        self.by_token: dict[str, dict] = {}
        for r in rows:
            if not kept_page(r):
                continue
            for url in sorted({r.get("url") or "", r.get("fetched_from") or "", r.get("final_url") or ""} - {""}):
                if url not in self.by_url or (r.get("retrieved_at") or "") > (self.by_url[url].get("retrieved_at") or ""):
                    self.by_url[url] = r
                for token in HEX32.findall(url) + AWARD_ID.findall(url):
                    if token not in self.by_token or (r.get("retrieved_at") or "") > (self.by_token[token].get("retrieved_at") or ""):
                        self.by_token[token] = r

    def row(self, url: str) -> dict | None:
        if url in self.by_url:
            return self.by_url[url]
        for token in HEX32.findall(url or "") + AWARD_ID.findall(url or ""):
            if token in self.by_token:
                return self.by_token[token]
        return None

    def cite(self, url: str, locator: dict | None = None, passage: str | None = None, field: str | None = None, record: str | None = None) -> dict:
        r = self.row(url) or {}
        # a row backfilled for bytes committed without one carries the first-commit day where a fetched row carries its retrieval time
        out = {"url": url, "retrieved_at": r.get("retrieved_at") or r.get("first_committed") or r.get("backfilled_at"), "sha256_12": (r.get("sha256") or "")[:12] or None,
               "locator": locator or {}, "ledger_row": bool(r)}
        if passage:
            out["passage"] = " ".join(passage.split())[:400]
        if field:
            out["field"] = field
        if record:
            out["record"] = record
        return out


def short(cite: dict) -> str:
    """The citation as the report prints it: (URL; saved DATE; sha256 12 hex), or (URL; frozen record) for a corpus event, or the
    locator alone for a statement that lives in a packaged sheet rather than at an address."""
    if cite.get("sha256_12"):
        return f"({cite['url']}; saved {(cite.get('retrieved_at') or '')[:10]}; sha256 {cite['sha256_12']})"
    where = cite.get("url") or "; ".join(f"{k} {v}" for k, v in (cite.get("locator") or {}).items() if v) or "no address"
    return f"({where}; {cite.get('record') or 'no ledger row'})"


# ------------------------------------------------------------------ names

def norm(text: str) -> str:
    return " ".join(WORD.findall((text or "").lower()))


def variants(title: str) -> list[str]:
    """The names a program is known by in a book title: the title, the parenthesised acronym, the title without it.
    Short or generic forms are dropped so "Support" or "Navy" never joins anything."""
    title = " ".join((title or "").split())
    out = {title}
    m = re.search(r"\(([^()]{2,25})\)\s*$", title)
    if m:
        out.add(m.group(1).strip())
        out.add(re.sub(r"\s*\([^)]*\)\s*$", "", title).strip())
    keep = []
    for v in out:
        n = norm(v)
        words = n.split()
        if not words or n in GENERIC or n in GENERIC_EXTRA:
            continue
        if len(words) == 1 and not (v.isupper() and len(v) >= 3 or any(ch.isdigit() for ch in v) or (v[:1].isupper() and len(v) >= 5)):
            continue  # a short lower-case word is not a name to search for; a one-word program name ("Fleetwood") is kept and matched in context
        if len(words) >= 2 and all(w in GENERIC_WORDS for w in words):
            continue
        keep.append(v)
    return sorted(keep, key=lambda v: (-len(v), v))  # longest first, then by name: the same order in every process


GENERIC_EXTRA = {"program", "programs", "project", "projects", "office", "support", "services", "systems", "system", "technology",
                 "technologies", "research", "development", "engineering", "management", "operations", "other", "studies",
                 "analysis", "concepts", "initiatives", "activities", "equipment", "modernization", "sustainment"}
GENERIC_WORDS = GENERIC_EXTRA | {"and", "of", "the", "for", "in", "to", "a", "an", "on", "advanced", "integrated", "joint", "naval",
                                 "defense", "navy", "air", "force", "army", "national", "space", "cyber", "information", "warfare",
                                 "tactical", "strategic", "electronic", "network", "networks", "communications", "command", "control",
                                 "computers", "intelligence", "logistics", "training", "test", "evaluation", "science", "sciences",
                                 "basic", "applied", "component", "prototype", "prototypes", "demonstration", "demonstrations",
                                 "capability", "capabilities", "enterprise", "mission", "next", "generation", "future", "small",
                                 "business", "innovation", "innovative", "rapid", "materials", "material", "biological", "medical",
                                 "sensors", "sensor", "data", "software", "hardware", "platform", "platforms", "vehicle", "vehicles",
                                 "weapons", "weapon", "munitions", "aircraft", "ship", "ships", "submarine", "surface", "undersea"}


def name_pattern(name: str, strict: bool = False) -> re.Pattern:
    """The name as a whole word. `strict` is for long texts (an FPDS action description): a one-word name or a short
    acronym, which may also be an ordinary word (GO, ICE, SHIELD, Oversight), counts only in a program-name position
    there: in parentheses, beside the word "program", or after a colon or dash."""
    name = " ".join(name.split())
    plain = r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"
    if strict and len(name.split()) == 1 and len(name) <= 12:
        word = re.escape(name)
        plain = (r"(?:\(" + word + r"\)|(?:[:\-\u2013]\s*|\bPROGRAM\s+|\bPROGRAM:\s*)" + word + r"(?![A-Za-z0-9])|"
                 r"(?<![A-Za-z0-9])" + word + r"\s+PROGRAM\b)")
    return re.compile(plain, re.I)


def names_in(text: str, candidates: list[tuple[str, object]]) -> list[object]:
    """The candidates whose name (any variant) the text prints as a whole word."""
    found = []
    for pattern, payload in candidates:
        if pattern.search(text):
            found.append(payload)
    return found


# ------------------------------------------------------------------ the record

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def offices_of(layer: Layer, seed: dict) -> list[dict]:
    """The offices the report is written for: every organization of an owner type in the record (the profile's own
    types after the shared program-office types), the pilot offices first, and where a profile names no owner type the
    offices the record's requirements are owned by. Each with its seed id where the seed has the node."""
    orgs = layer.orgs
    seed_ids = {uid("org", n["id"]): n["id"] for n in (seed or {}).get("nodes", [])}
    order: list[str] = []
    for name in P.get("pilot_offices", ()):
        oid = layer.org_id(name)
        if oid and oid not in order:
            order.append(oid)
    for oid, o in sorted(orgs.items(), key=lambda kv: (office_name(kv[0], orgs) or "")):
        if o.get("org_type") in OWNER_TYPES and oid not in order:
            order.append(oid)
    if len(order) <= len(P.get("pilot_offices", ())):
        for n in layer.needs:
            if n.get("owner_id") and n["owner_id"] in orgs and n["owner_id"] not in order and orgs[n["owner_id"]].get("org_type") not in ("agency", "contracting_office"):
                order.append(n["owner_id"])
    out = []
    for oid in order:
        o = orgs[oid]
        out.append({"uuid": oid, "seed_id": seed_ids.get(oid, ""), "acronym": o.get("acronym") or "", "name": o.get("name") or "",
                    "org_type": o.get("org_type") or "", "parent": o.get("parent") or "", "pilot": office_name(oid, orgs) in P.get("pilot_offices", ()),
                    "subtree": sorted(layer.subtree(oid) - {""})})
    return out


# ------------------------------------------------------------------ people: leadership as listed, problem owners as stated

def seed_index(seed: dict) -> tuple[dict, dict]:
    nodes = {n["id"]: n for n in (seed or {}).get("nodes", [])}
    observations = {o["id"]: o for o in (seed or {}).get("observations", [])}
    return nodes, observations


def leadership_facts(office: dict, seed: dict, roster: list[dict], ledger: Ledger, layer: Layer) -> list[dict]:
    """Who the sources list in charge of the office or an office under it: the seed's `leads` relationships with their
    observation passage, and the people file's positions with a stated role. A row states the listing and nothing else."""
    nodes, observations = seed_index(seed)
    subtree_seed = {nodes[n]["id"] for n in nodes if uid("org", n) in set(office["subtree"])}
    out = []
    for rel in (seed or {}).get("relationships", []):
        if rel.get("type") != "leads" or rel.get("to") not in subtree_seed or rel.get("retraction"):
            continue
        person = nodes.get(rel["from"], {})
        for obs_id in rel.get("observation_ids", []):
            obs = observations.get(obs_id)
            if not obs:
                continue
            out.append({"id": f"fact:lead:{rel['id']}:{obs_id}", "kind": "leadership_as_listed", "name": person.get("name") or rel["from"],
                        "role_as_written": rel.get("role_as_written", ""), "office": rel["to"], "office_name": nodes.get(rel["to"], {}).get("name", rel["to"]),
                        "effective_from": rel.get("effective_from"), "evidence_class": rel.get("evidence_class"),
                        "source": ledger.cite(obs["source_url"], {"observation": obs_id, "revision": obs.get("source_revision")}, obs.get("passage"))})
    seen = {(r["name"], r["role_as_written"]) for r in out}
    stated = {(norm(r["name"]), r["source"].get("sha256_12")) for r in out}
    for person in roster:
        for pos in person.get("positions", []):
            if pos.get("role_type") not in ROLE_ORDER or pos.get("role_type") == "other":
                continue
            office_seed = pos.get("office") or ""
            if office_seed not in subtree_seed:
                continue
            key = (person["name"], pos.get("raw_title", ""))
            cite = ledger.cite(pos.get("source_url") or "", {"source": pos.get("source"), "ref": pos.get("source_ref")}, field=pos.get("context") or "")
            if key in seen or (norm(person["name"]), cite.get("sha256_12")) in stated:
                continue  # the seed's leads relationship already quotes this page for this person
            seen.add(key)
            out.append({"id": f"fact:position:{person['id']}:{pos.get('source_ref') or pos.get('observed_at')}", "kind": "position_as_listed",
                        "name": person["name"], "role_as_written": pos.get("raw_title", ""), "role_type": pos["role_type"], "office": office_seed,
                        "office_name": nodes.get(office_seed, {}).get("name", office_seed), "observed_at": pos.get("observed_at"),
                        "source": cite})
    out.sort(key=lambda r: (ROLE_ORDER.index(r.get("role_type", "other")) if r.get("role_type") in ROLE_ORDER else 0, r["name"]))
    return out


RENAME_RE = re.compile(r"\b(renamed|formerly|previously known as|now called|expansion of [^.]{3,80} into|has become|was established as)\b", re.I)


def office_notes(office: dict, seed: dict, ledger: Ledger) -> list[dict]:
    """What the office's own cited pages say about its name: the sentences of a saved page (cited by a seed observation
    about the office) that speak of a renaming or an expansion, quoted. A statement of the page, not a reading of ours."""
    nodes, observations = seed_index(seed)
    if not office["seed_id"]:
        return []
    urls = []
    for obs in observations.values():
        if office["seed_id"] in (obs.get("subject_ids") or []) and obs.get("source_url"):
            urls.append(obs["source_url"])
    out = []
    for url in dict.fromkeys(urls):
        row = ledger.row(url)
        if not row or not row.get("path") or not (ROOT / row["path"]).exists() or "html" not in (row.get("mime") or "html"):
            continue
        plain = page_text((ROOT / row["path"]).read_text(encoding="utf-8", errors="replace"), main_only=False)
        names = [n for n in (office["name"], office["acronym"]) if n and len(n) >= 3]
        for sentence in re.split(r"(?<=[.!?])\s+", plain):
            # a sentence about the office's own name, not a biography's "formerly Bellcore"
            if RENAME_RE.search(sentence) and 20 < len(sentence) < 400 and any(n.lower() in sentence.lower() for n in names):
                out.append({"id": f"fact:note:{office['seed_id']}:{len(out)}", "kind": "office_note", "office": office["uuid"],
                            "source": ledger.cite(url, {"page": "sentence"}, sentence)})
            if len(out) >= 3:
                break
    return out


def page_text(raw: str, main_only: bool = True) -> str:
    """The words of a saved page: JSON string escapes the site embeds (\\u003C, \\/) decoded, scripts and tags dropped, entities unescaped."""
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw).replace("\\/", "/")
    if main_only:
        main = re.search(r"<main.*?</main>", text, flags=re.S)
        text = main.group(0) if main else text
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", text, flags=re.S)
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def staff_pages(ledger: Ledger) -> list[dict]:
    """The agency's staff listing and each person's own page, where the profile publishes them (darpa.mil/json/staff): role,
    office, research topics and start date as the listing prints them; the programs the page links; a role sentence from
    the page. Nothing is read from a title: the fields are the listing's own."""
    listing_url = P["people"].get("staff_listing")
    if not listing_url:
        return []
    row = ledger.row(listing_url)
    if not row or not (ROOT / row["path"]).exists():
        return []
    base = P["people"].get("staff_base_url", "")
    seen: set[str] = set()
    out = []
    for rec in json.loads((ROOT / row["path"]).read_text(encoding="utf-8", errors="replace")):
        nid = str(rec.get("nid") or "")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        url = base + (rec.get("view_node") or "")
        page = ledger.row(url)
        programs, sentences = [], []
        if page and page.get("path") and (ROOT / page["path"]).exists():
            text = (ROOT / page["path"]).read_text(encoding="utf-8", errors="replace")
            programs = [{"slug": m.group(1), "name": html.unescape(m.group(2)).strip()}
                        for m in re.finditer(r'<a[^>]*href="(/research/programs/[^"]+)"[^>]*>([^<]+)</a>', text)]
            programs = list({p["slug"]: p for p in programs}.values())
            plain = page_text(text)
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", plain) if STAFF_ROLE_SENTENCE.search(s) and 40 < len(s) < 400]
        out.append({"nid": nid, "name": (rec.get("title") or "").strip(), "role_as_written": rec.get("field_role") or "",
                    "office_as_written": rec.get("field_taxonomy_office") or "",
                    "topics": [t.strip() for t in (rec.get("field_research_topics") or "").split(",") if t.strip()],
                    "start_as_listed": rec.get("field_start_date__raw") or "", "url": url, "programs": programs,
                    "role_sentence": next((s for s in sentences if re.search(r"\b(director|deputy|manager|leads)\b", s, re.I)), ""),
                    "listing": ledger.cite(listing_url, {"nid": nid, "fields": ["field_role", "field_taxonomy_office", "field_research_topics", "field_start_date__raw"]},
                                           field=f"{rec.get('field_role') or ''}, {rec.get('field_taxonomy_office') or ''}"),
                    "page": ledger.cite(url, {"page": "programs linked; role sentence"}, sentences[0] if sentences else None) if page else None})
    return out


def owner_facts(office: dict, staff: list[dict], observations: list[dict], layer: Layer, ledger: Ledger, seed: dict) -> list[dict]:
    """One row per (person, stated program or requirement). From a person page that lists programs: the page is the fact and
    the basis is `page_lists_program`. From a contact observation that names the person as the office's program manager:
    the requirements the record says the office owns, with the basis `pm_of_owning_office`, an inferred link the row says
    is inferred."""
    nodes, _ = seed_index(seed)
    subtree = set(office["subtree"])
    out = []
    for person in staff:
        oid = layer.org_id(person["office_as_written"]) if person["office_as_written"] else None
        if oid not in subtree:
            continue
        for program in person["programs"] or [None]:
            out.append({"id": f"fact:owner:{person['nid']}:{program['slug'] if program else 'none'}", "kind": "problem_owner", "name": person["name"],
                        "role_as_written": person["role_as_written"], "office": office_name(oid, layer.orgs), "topics_as_listed": person["topics"],
                        "start_as_listed": person["start_as_listed"], "program": program["name"] if program else None,
                        "owner_basis": "page_lists_program" if program else "page_lists_no_program", "evidence_class": "directly_documented",
                        "source": person["page"] or person["listing"]})
    seed_subtree = {n for n in nodes if uid("org", n) in subtree}
    needs_by_owner: dict[str, list[dict]] = defaultdict(list)
    for n in layer.needs:
        if n.get("owner_id") in subtree:
            needs_by_owner[n["owner_id"]].append(n)
    paged = {norm(o["name"]) for o in out if o["owner_basis"] == "page_lists_program"}
    newest_pm: dict[str, str] = {}
    for obs in observations:
        if obs.get("kind") == "person" and obs.get("role_type") == "program_manager" and obs.get("office_id_as_resolved") in seed_subtree:
            key = obs["office_id_as_resolved"]
            if (obs.get("observed_at") or "") > (newest_pm.get(key, ("", ""))[0] if isinstance(newest_pm.get(key), tuple) else ""):
                newest_pm[key] = (obs.get("observed_at") or "", obs["id"])
    for obs in observations:
        if obs.get("kind") != "person" or obs.get("role_type") not in ("program_manager", "executive"):
            continue
        if norm(obs.get("name", "")) in paged:
            continue  # the person's own page already states the programs; the office-level reading adds nothing
        office_seed = obs.get("office_id_as_resolved") or ""
        if office_seed not in seed_subtree:
            continue
        owned = needs_by_owner.get(uid("org", office_seed), [])
        if obs.get("role_type") == "program_manager" and newest_pm.get(office_seed, ("", ""))[1] != obs["id"]:
            owned = []  # an earlier listing: the person is listed, the requirements go with the newest listing
        if obs.get("role_type") == "program_manager":
            for need in owned[:8] or [None]:
                out.append({"id": f"fact:owner:{obs['id']}:{need['key'] if need else 'none'}", "kind": "problem_owner", "name": obs.get("name", ""),
                            "role_as_written": obs.get("role_as_written", ""), "office": nodes.get(office_seed, {}).get("name", office_seed),
                            "program": need["title"] if need else None, "need_key": need["key"] if need else None,
                            "owner_basis": "pm_of_owning_office" if need else ("pm_listed_earlier_than_the_newest" if newest_pm.get(office_seed, ("", ""))[1] != obs["id"] else "pm_listed_no_requirement_owned"),
                            "evidence_class": "inferred" if need else "directly_documented",
                            "basis_note": ("the page names the person as the office's program manager and the record says the office owns "
                                           "the requirement; that the person owns this requirement is the reading, not a statement") if need else None,
                            "source": ledger.cite(obs.get("source_url", ""), {"locators": obs.get("locators"), "revision": obs.get("source_revision")}, obs.get("passage"))})
            if len(owned) > 8:
                out[-1]["more_requirements_owned"] = len(owned) - 8
    return out


# ------------------------------------------------------------------ budget: lines, measures, programs, and where each sits

def budget_lines(ledger: Ledger) -> tuple[dict, list[dict], list[dict]]:
    """(the books, the lines with their book citation, the R-2A program rows) from the profile's budget_lines.json."""
    payload = load_json(BUDGET_LINES) or {"books": [], "lines": []}
    books = {b["path"]: b for b in payload["books"]}
    lines, programs = [], []
    for row in payload["lines"]:
        book = books.get(row["book"], {})
        cite = ledger.cite(book.get("url", ""), {"book": row["book"], "pages": row.get("pages"), "pb": book.get("pb")}, field=row.get("event_title"))
        cite["sha256_12"] = cite["sha256_12"] or (book.get("sha256") or "")[:12] or None
        cite["retrieved_at"] = cite["retrieved_at"] or book.get("retrieved_at")
        lines.append({"id": f"fact:line:{book.get('pb')}:{row['appropriation']}:{row['li']}", "kind": "budget_line_book", "pb": book.get("pb"),
                      "appropriation": row["appropriation"], "appropriation_name": row.get("appropriation_name", ""), "code": row["li"], "title": row["title"],
                      "budget_activity": row.get("budget_activity") or book.get("budget_activity") or "", "amounts": row.get("amounts") or {},
                      "event_type": row.get("event_type"), "moved_to": row.get("moved_to", []), "text": row.get("text", ""),
                      "projects": row.get("projects", []), "source": cite})
        for p in row.get("programs", []):
            programs.append({"id": f"fact:program:{book.get('pb')}:{p['pe']}:{p.get('project')}:{norm(p['program'])[:40]}", "kind": "program_row",
                             "pb": book.get("pb"), "pe": p["pe"], "project": p.get("project"), "program": p["program"], "amounts": p.get("amounts", {}),
                             "amounts_found": p.get("amounts_found"), "page": p.get("page"),
                             "source": ledger.cite(book.get("url", ""), {"book": row["book"], "page": p.get("page"), "row": "Title:"}, field=p["program"])})
    return payload, lines, programs


def budget_measures(ledger: Ledger) -> tuple[dict | None, list[dict]]:
    """The Comptroller display lines (R-1 and P-1) with their labelled measures, each citing its sheet and rows."""
    payload = load_json(BUDGET_MEASURES)
    if not payload:
        return None, []
    out = []
    for line in payload["lines"]:
        src = line["source"]
        cite = ledger.cite(src["url"], src["locator"], field=f"{line['code']} {line['title']}")
        cite["sha256_12"] = cite["sha256_12"] or src["sha256"][:12]
        cite["retrieved_at"] = cite["retrieved_at"] or src["retrieved_at"]
        out.append({"id": f"fact:measure:{line['exhibit']}:{line['account']}:{line['line_number']}:{line['code']}", "kind": "budget_line_display",
                    "exhibit": line["exhibit"], "account": line["account"], "account_title": line["account_title"], "budget_activity": line["budget_activity"],
                    "budget_activity_title": line["budget_activity_title"], "line_number": line["line_number"], "code": line["code"], "title": line["title"],
                    "measures": line["measures"], "source": cite})
    return payload, out


def measure_keys(measures: list[dict]) -> dict[str, str]:
    """The three columns the report leads with, by the sheet's own labels: the newest actual, the enacted year, the request."""
    keys = sorted({k for m in measures for k in m["measures"]})
    pick = lambda pattern: next((k for k in sorted(keys, reverse=True) if re.fullmatch(pattern, k)), None)
    request = pick(r"fy\d{4}_request")
    return {"actual": pick(r"fy\d{4}_actual"), "enacted": pick(r"fy\d{4}_enacted"), "request": request,
            "total_request": f"{request[:6]}_total" if request and f"{request[:6]}_total" in keys else None}


def office_code_index(layer: Layer) -> tuple[re.Pattern | None, dict[str, str]]:
    """The office-code pattern the profile reads and a map from the code as written (upper, single-spaced) to the office."""
    pattern = (P.get("reading") or {}).get("office_code_re") or P.get("office_key_re")
    if not pattern or pattern == r"(?!)":
        return None, {}
    return re.compile(pattern, re.I), {}


def place_line(text: str, title: str, layer: Layer, offices: list[dict], code_re: re.Pattern | None, page_programs: dict[str, list[dict]],
               record_names: dict[str, list[tuple[re.Pattern, dict]]]) -> tuple[list[str], str, list[dict]]:
    """(office uuids, basis, the evidence rows) for one line: the office code the line's text names; else an office whose
    person page lists a program the line names; else an office whose forecast row shares the line's program name; else an
    office whose notice or award does; else unplaced."""
    if code_re:
        for m in code_re.finditer(text or ""):
            oid = layer.org_id(m.group(0))
            if oid:
                return [oid], "office_code_in_text", [{"code": m.group(0)}]
    names = variants(title)
    if not names:
        return [], "unplaced", []
    for basis, table in (("program_named_on_people_page", None), ("program_named_in_forecast_row", "forecast"), ("program_named_in_notice_or_award", "record")):
        hits: dict[str, list[dict]] = defaultdict(list)
        for name in names:
            pattern = name_pattern(name)
            if table is None:
                for oid, programs in page_programs.items():
                    for p in programs:
                        if pattern.fullmatch(" ".join(p["name"].split())) or norm(p["name"]) == norm(name):
                            hits[oid].append({"program_on_page": p["name"], "person": p["person"], "url": p["url"]})
            else:
                for oid, rows in record_names.get(table, {}).items():
                    for pat, payload in rows:
                        if pattern.search(payload["title"]):
                            hits[oid].append(payload)
                            break
        if hits:
            best = max(len(v) for v in hits.values())
            chosen = [oid for oid, v in hits.items() if len(v) == best][:3]
            return chosen, basis, [h for oid in chosen for h in hits[oid][:2]]
    return [], "unplaced", []


# ------------------------------------------------------------------ spending: the fiscal year's obligations from the saved feed

def fpds_actions(fy: int) -> tuple[list[dict], dict]:
    """Every action the saved FPDS pages show signed in the fiscal year: the base awards on the year's sweep pages for each
    swept office or funding agency, and every action on the saved histories of the swept awards. One row per (PIID, mod,
    signed day), with the page it was read from."""
    manifest = manifest_rows()
    index = url_index(manifest)
    window = next((w for w in windows(date.today()) if w["fy"] == fy), None)
    lo, hi = fy_window(fy)
    actions: dict[tuple, dict] = {}
    pages_read = 0
    piids: set[str] = set()
    for field, table, _ in FIELDS:
        for office in table:
            for w in windows(date.today()):
                pages, _complete = saved_pages(manifest, office, w, field)
                for page in pages:
                    pages_read += 1
                    for e in fpds_entries((ROOT / page["path"]).read_bytes(), width=None):
                        if not e["piid"]:
                            continue
                        piids.add(e["piid"])
                        if w["fy"] == fy and lo <= e["signed"] <= hi:
                            actions.setdefault((e["piid"], e["mod"], e["signed"]), {**e, "page": page["url"], "page_sha256": page["sha256"], "history": False})
    history_pages = 0
    for piid in sorted(piids):
        pages, rows, _complete = fpds_history(index, piid)
        history_pages += len(pages)
        by_sha = {pg["sha256"]: pg for pg in pages}  # fpds_history tags each action with its page's sha256
        for e in rows:
            if e.get("piid") and lo <= (e.get("signed") or "") <= hi:
                page = by_sha.get(e.get("page") or "", {})
                actions.setdefault((e["piid"], e.get("mod", ""), e["signed"]), {**e, "page": page.get("url", ""), "page_sha256": page.get("sha256"), "history": True})
    out = sorted(actions.values(), key=lambda a: (a["signed"], a["piid"], a.get("mod") or ""))
    for a in out:
        try:
            a["obligated_amount"] = float((a.get("obligated") or "").replace(",", "")) if a.get("obligated") else 0.0
        except ValueError:
            a["obligated_amount"] = 0.0
    return out, {"sweep_pages": pages_read, "history_pages": history_pages, "swept_awards": len(piids), "window": {"fy": fy, "from": lo, "to": hi},
                 "windows_known": bool(window)}


def piid_offices(layer: Layer) -> dict[str, tuple[str, str]]:
    """PIID -> (office uuid, the basis the record states) from the frozen incumbent events: the placement the loader made
    and wrote into each item's text ("placed by the office code in the description")."""
    out = {}
    for e in layer.events:
        if e["family"] != "incumbent":
            continue
        m = PIID_IN_TITLE.match(e["title"])
        if not m:
            continue
        basis = PLACED_BY.search(e.get("text", ""))
        out.setdefault(m.group(1), (e.get("filed") or e["org"], basis.group(1).strip() if basis else "the record's placement"))
    return out


def place_action(action: dict, by_piid: dict, layer: Layer, code_re: re.Pattern | None, uics: dict[str, str]) -> tuple[str, str]:
    if action["piid"] in by_piid:
        oid, basis = by_piid[action["piid"]]
        return oid, f"the record's incumbent item ({basis})"
    if code_re:
        for m in code_re.finditer(" ".join((action.get("description") or "").split())):
            oid = layer.org_id(m.group(0))
            if oid:
                return oid, "the office code in the description"
    if (action.get("funding_office") or "").upper() in uics:
        return uics[action["funding_office"].upper()], "the funding office"
    if (action.get("contracting_office") or "").upper() in uics:
        return uics[action["contracting_office"].upper()], "the contracting office"
    return "", "no office: the funding and contracting offices are not in the record"


def usaspending_saved(ledger_rows: list[dict], fy: int) -> list[dict]:
    """The year's USAspending answers `collect` saved for this profile, newest per kind."""
    found: dict[str, dict] = {}
    for r in ledger_rows:
        note = r.get("note") or ""
        if not note.startswith(USASPENDING_NOTE) or f"FY{fy}" not in note or not kept_page(r):
            continue
        kind = note.split(USASPENDING_NOTE, 1)[1].strip().split(" ")[0]
        if kind not in found or (r.get("retrieved_at") or "") > (found[kind].get("retrieved_at") or ""):
            found[kind] = r
    out = []
    for kind, r in sorted(found.items()):
        try:
            body = json.loads((ROOT / r["path"]).read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        results = body.get("results", [])
        total = sum(float(x.get("aggregated_amount") or x.get("amount") or 0) for x in results)
        out.append({"kind": kind, "total": round(total), "rows": len(results), "url": r["url"], "retrieved_at": r["retrieved_at"], "sha256_12": r["sha256"][:12],
                    "top": [{"name": x.get("name"), "amount": round(float(x.get("amount") or 0))} for x in results[:8]] if kind.startswith("recipients") else
                           [{"period": f"{x['time_period'].get('fiscal_year')}-{x['time_period'].get('month')}", "amount": round(float(x.get("aggregated_amount") or 0))}
                            for x in results if isinstance(x.get("time_period"), dict)]})
    return out


# ------------------------------------------------------------------ boundaries: what was collected, so an empty section is a fact about the record

def boundaries(layer: Layer, roster: list[dict], staff: list[dict], lines: list[dict], measures: list[dict], spend_meta: dict) -> dict[str, str]:
    import agency_truth  # noqa: PLC0415  (the truth table's file facts, read for this profile)
    facts = agency_truth.files_for(KEY)
    families = Counter(e["family"] for e in layer.events)
    newest = {fam: max((e["available_by"] for e in layer.events if e["family"] == fam), default="") for fam in families}
    listing = P["people"].get("staff_listing")
    out = {
        "record": f"the frozen record ({CORPUS.relative_to(ROOT)}, frozen {layer.corpus.get('frozen_at', '')}) holds {len(layer.events):,} statements over "
                  f"{len(layer.orgs)} organizations and {len(layer.needs):,} requirements; families: " + ", ".join(f"{k} {v:,}" for k, v in families.most_common()),
        "budget": (f"{len(lines)} budget line(s) read from {len({l['source']['url'] for l in lines})} justification book(s)"
                   + (f"; {len(measures)} line(s) from the Comptroller display spreadsheets" if measures else
                      "; no Comptroller display rows: the profile has no filter for the Department-wide sheets" if not P["budget"].get("display") else
                      "; the Comptroller display spreadsheets are not read yet (run budget.py extract)")
                   + ("" if lines or measures else f"; no book is saved for this profile ({facts['event_files'].get('budget', {}).get('rows', 0)} rows in budget_lines.json)")),
        "people": (f"{len(roster)} people with a stated role in people.json"
                   + (f"; the staff listing ({listing}) gives {len(staff)} people, {sum(1 for s in staff if s['programs'])} with programs on their own page"
                      if listing else "; the profile publishes no staff listing, so program managers are known only where a release or notice names one")),
        "incumbent": f"{families.get('incumbent', 0):,} contract statements, newest {newest.get('incumbent', '') or 'none'}; "
                     f"the saved FPDS feed for FY{spend_meta['window']['fy']}: {spend_meta['sweep_pages']:,} sweep page(s), {spend_meta['history_pages']:,} history page(s), "
                     f"{spend_meta['swept_awards']:,} swept award(s)",
        "notice": f"{families.get('notice', 0):,} notices, newest {newest.get('notice', '') or 'none'}",
        "programs": f"{families.get('programs', 0):,} SBIR/STTR topics, newest {newest.get('programs', '') or 'none'}",
        "forecast": f"{families.get('forecast', 0):,} forecast statements, newest {newest.get('forecast', '') or 'none'}" if families.get("forecast")
                    else "no forecast: the profile publishes none or none is packaged",
        "hiring": f"{families.get('hiring', 0):,} vacancy statements, newest {newest.get('hiring', '') or 'none'}",
        "news": f"{families.get('news', 0):,} news statements, newest {newest.get('news', '') or 'none'}",
        "leaders": (lambda l: f"{l.get('official_documents', 0)} official speech, testimony or statement document(s), newest {l.get('newest_official') or 'none'}; "
                              f"{l.get('conference_documents', 0)} conference page(s)")(facts.get("leaders_statements", {})),
    }
    return out


# ------------------------------------------------------------------ the build

def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="office_owners.py build")
    ap.add_argument("--as-of", default=None, help="read the record as of this day (default: the newest statement)")
    ap.add_argument("--check", action="store_true", help="recompute and fail if the saved file would change")
    args = ap.parse_args(argv)
    if not CORPUS.exists():
        print(f"no frozen record at {CORPUS.relative_to(ROOT)}; run the backtest stage first")
        return 1
    payload = compute(args.as_of)
    text = json.dumps(payload, indent=1, ensure_ascii=False, default=str) + "\n"
    md = render(payload)
    if args.check and OUT_JSON.exists():
        saved = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        fresh = json.loads(text)  # compared as written: a tuple in memory is a list on disk
        same = {k: v for k, v in saved.items() if k != "generated"} == {k: v for k, v in fresh.items() if k != "generated"}
        print("office owners: unchanged" if same else "office owners: the saved file would change; run without --check to rewrite it")
        return 0 if same else 1
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(text, encoding="utf-8")
    OUT_MD.write_text(md, encoding="utf-8")
    s = payload["summary"]
    print(f"{OUT_MD.relative_to(ROOT)}: {s['offices']} office(s), {s['facts']} fact row(s), {s['inferences']} inference(s), {s['problems']} problem(s); "
          f"budget lines {s['budget_lines']} (placed {s['budget_lines_placed']}), FY{payload['fiscal_year']} obligations ${s['fy_obligations'] / 1e6:,.1f}M over {s['fy_actions']:,} action(s)")
    return 0


def compute(as_of: str | None = None) -> dict:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    roster = load_people()
    layer = Layer(corpus, roster, as_of=as_of)
    seed = load_json(SEED) or {"nodes": [], "observations": [], "relationships": [], "interpretations": []}
    ledger = Ledger()
    observations = load_json(CONTACT_OBSERVATIONS) or []
    observations = observations if isinstance(observations, list) else observations.get("observations", [])
    dna = load_json(DNA) or {"offices": {}, "contracting_offices": {}}
    kinds = load_json(NOTICE_KINDS) or {}
    fy = fiscal_year(layer.as_of)
    window = fy_window(fy)
    record = f"frozen record {corpus.get('frozen_at', '')}"

    offices = offices_of(layer, seed)
    staff = staff_pages(ledger)
    books_payload, lines, all_program_rows = budget_lines(ledger)
    newest_pb = max((p["pb"] or "" for p in all_program_rows), default="")
    program_rows = [p for p in all_program_rows if p["pb"] == newest_pb]
    # the older book asked for this year under its own program names: the amount first requested, matched by name (a renamed program is missed)
    prior_request: dict[str, float] = defaultdict(float)
    for p in all_program_rows:
        if p["pb"] and p["pb"] != newest_pb and (p["amounts"] or {}).get(f"fy{int(p['pb'])}"):
            prior_request[norm(p["program"])] += p["amounts"][f"fy{int(p['pb'])}"]
    for p in program_rows:
        p["first_requested"] = round(prior_request[norm(p["program"])], 3) if norm(p["program"]) in prior_request else None
        p["first_requested_book"] = f"PB {int(newest_pb) - 1}" if newest_pb and prior_request else None
    lines = [l for l in lines if l["pb"] == newest_pb or not newest_pb] + [l for l in lines if newest_pb and l["pb"] != newest_pb]
    _measures_payload, measures = budget_measures(ledger)
    mkeys = measure_keys(measures)
    code_re, _ = office_code_index(layer)
    actions, spend_meta = fpds_actions(fy)
    by_piid = piid_offices(layer)
    uics = {o["acronym"].upper(): oid for oid, o in layer.orgs.items() if o.get("org_type") == "contracting_office" and o.get("acronym")}
    for a in actions:
        a["office"], a["placed_by"] = place_action(a, by_piid, layer, code_re, uics)

    # program names by office, from the people pages (fact of the page) and from the record (forecast rows, notices, awards);
    # a statement filed under an organization inside an office's subtree counts for that office, and the agency's own or a
    # contracting office's statements place nothing
    office_of_uuid: dict[str, str] = {}
    for office in offices:
        for member in office["subtree"]:
            office_of_uuid.setdefault(member, office["uuid"])
    page_programs: dict[str, list[dict]] = defaultdict(list)
    for person in staff:
        oid = layer.org_id(person["office_as_written"]) if person["office_as_written"] else None
        if oid and office_of_uuid.get(oid):
            for p in person["programs"]:
                page_programs[office_of_uuid[oid]].append({"name": p["name"], "person": person["name"], "url": person["url"]})
    record_names: dict[str, dict[str, list[tuple[re.Pattern, dict]]]] = {"forecast": defaultdict(list), "record": defaultdict(list)}
    for n in layer.needs:
        if office_of_uuid.get(n.get("owner_id") or "") and not n["key"].startswith("notice:"):
            record_names["forecast"][office_of_uuid[n["owner_id"]]].append((None, {"title": n["title"], "key": n["key"], "kind": "forecast row"}))
    for e in layer.events:
        if e["family"] in ("notice", "incumbent", "programs") and office_of_uuid.get(e.get("org") or ""):
            record_names["record"][office_of_uuid[e["org"]]].append((None, {"title": e["title"], "id": e["id"], "kind": e["family"], "date": e["available_by"], "url": e.get("url", "")}))

    # every budget line placed: the books' lines (with the R-2A programs under them) and the display lines
    placed_lines = []
    program_place: dict[str, dict] = {}
    for p in program_rows:
        oids, basis, evidence = place_line("", p["program"], layer, offices, None, page_programs, record_names)
        program_place[p["id"]] = {"offices": oids, "basis": basis, "evidence": evidence}
    for line in lines:
        under = [p for p in program_rows if p["pe"] == line["code"] and p["pb"] == line["pb"]]
        if under:
            offices_of_programs = Counter(o for p in under for o in program_place[p["id"]]["offices"])
            oids, basis, evidence = (list(offices_of_programs), "program_rows_under_the_line", [{"programs_placed": sum(offices_of_programs.values()), "programs": len(under)}]) \
                if offices_of_programs else place_line(line["text"], line["title"], layer, offices, code_re, page_programs, record_names)
        else:
            oids, basis, evidence = place_line(line["text"], line["title"], layer, offices, code_re, page_programs, record_names)
        placed_lines.append({**{k: v for k, v in line.items() if k != "text"}, "offices": oids, "join_basis": basis, "join_evidence": evidence,
                             "evidence_class": "directly_documented" if basis in DOCUMENTED else "inferred" if basis != "unplaced" else "none"})
    placed_measures = []
    for m in measures:
        oids, basis, evidence = place_line(m["title"], m["title"], layer, offices, code_re, page_programs, record_names)
        # a display line for a program element the books also carry inherits the books' placement when its own finds none
        if not oids:
            twin = next((pl for pl in placed_lines if pl["code"] == m["code"] and pl["offices"]), None)
            if twin:
                oids, basis, evidence = twin["offices"], twin["join_basis"], twin["join_evidence"]
        placed_measures.append({**m, "offices": oids, "join_basis": basis, "join_evidence": evidence,
                                "evidence_class": "directly_documented" if basis in DOCUMENTED else "inferred" if basis != "unplaced" else "none"})

    # programs named in the year's obligations (FPDS action descriptions), for the R-2A profiles
    program_candidates = [(name_pattern(v, strict=True), p) for p in program_rows for v in variants(p["program"])]
    program_spend: dict[str, dict] = defaultdict(lambda: {"obligated": 0.0, "actions": 0, "vendors": Counter(), "piids": set()})
    for a in actions:
        desc = " ".join((a.get("description") or "").split())
        hit = next((p for pat, p in program_candidates if pat.search(desc)), None)
        if hit:
            s = program_spend[hit["id"]]
            s["obligated"] += a["obligated_amount"]
            s["actions"] += 1
            s["vendors"][a.get("vendor") or ""] += a["obligated_amount"]
            s["piids"].add(a["piid"])
            a["program"] = hit["program"]

    facts: dict[str, list[dict]] = {"leadership": [], "problem_owners": [], "budget_lines": placed_lines, "budget_measures": placed_measures,
                                    "program_rows": program_rows, "fy_actions": [], "activity": [], "office_notes": []}
    inferences: dict[str, list[dict]] = {"champions": [], "budget_held": [], "remaining": [], "program_placement": []}
    problems: list[str] = []
    per_office = []
    silent_offices: list[dict] = []
    for office in offices:
        subtree = set(office["subtree"])
        lead = leadership_facts(office, seed, roster, ledger, layer)
        owners = owner_facts(office, staff, observations, layer, ledger, seed)
        notes = office_notes(office, seed, ledger)
        facts["office_notes"] += notes
        facts["leadership"] += lead
        facts["problem_owners"] += owners
        own_lines = [pl for pl in placed_lines if set(pl["offices"]) & subtree]
        own_measures = [pm for pm in placed_measures if set(pm["offices"]) & subtree]
        own_programs = [p for p in program_rows if set(program_place[p["id"]]["offices"]) & subtree]
        own_program_names = {p["program"] for p in own_programs}
        own_actions = [a for a in actions if a["office"] in subtree or (a.get("program") in own_program_names)]
        for a in own_actions:
            if a["office"] not in subtree:
                a["placed_by"] = f"the action names the program {a['program']} (placed in the office by its own basis)"
        year_events = [e for e in layer.events if e.get("org") in subtree and in_window(e["available_by"], window)]
        if not office["pilot"] and not (year_events or own_lines or own_measures or own_programs or lead or owners or own_actions):
            silent_offices.append({"uuid": office["uuid"], "acronym": office["acronym"], "name": office["name"], "org_type": office["org_type"],
                                   "statements_in_record": sum(1 for e in layer.events if e.get("org") in subtree)})
            facts["leadership"] += lead
            facts["problem_owners"] += owners
            continue
        notices = sorted((e for e in year_events if e["family"] == "notice"), key=lambda e: e["available_by"], reverse=True)
        topics = sorted((e for e in year_events if e["family"] == "programs"), key=lambda e: e["available_by"], reverse=True)
        incumbents = sorted((e for e in year_events if e["family"] == "incumbent"), key=lambda e: e["available_by"], reverse=True)
        moves = sorted((e for e in year_events if e["family"] == "forecast"), key=lambda e: e["available_by"], reverse=True)
        hiring = [e for e in year_events if e["family"] == "hiring"]
        news = [e for e in year_events if e["family"] == "news"]
        book = dna.get("offices", {}).get(office["acronym"]) or dna.get("offices", {}).get(office["name"]) or {}
        activity = {
            "notices": [{"id": e["id"], "date": e["available_by"], "type": e["event_type"], "title": e["title"][:160],
                         "kind": (kinds.get(HEX32.search(e.get("url") or "").group(0), {}) if HEX32.search(e.get("url") or "") else {}).get("kind", ""),
                         "source": ledger.cite(e.get("url", ""), {"event": e["id"]}, record=record)} for e in notices[:40]],
            "notices_total": len(notices),
            "forecast_moves": [{"id": e["id"], "date": e["available_by"], "type": e["event_type"], "title": e["title"][:160], "line": e.get("line", ""),
                                "source": ledger.cite(e.get("url", ""), {"event": e["id"]}, record=record)} for e in moves[:25]],
            "forecast_moves_total": len(moves),
            "topics": [{"id": e["id"], "date": e["available_by"], "title": e["title"][:160], "source": ledger.cite(e.get("url", ""), {"event": e["id"]}, record=record)}
                       for e in topics[:20]],
            "topics_total": len(topics),
            "contract_events": [{"id": e["id"], "date": e["available_by"], "title": e["title"][:200], "vendor": e.get("vendor", ""),
                                 "source": ledger.cite(e.get("url", ""), {"event": e["id"]}, record=record)} for e in incumbents[:25]],
            "contract_events_total": len(incumbents),
            "hiring": [{"id": e["id"], "date": e["available_by"], "title": e["title"][:160], "source": ledger.cite(e.get("url", ""), {"event": e["id"]}, record=record)} for e in hiring[:10]],
            "news": [{"id": e["id"], "date": e["available_by"], "title": e["title"][:160], "source": ledger.cite(e.get("url", ""), {"event": e["id"]}, record=record)} for e in news[:10]],
            "buying_book": {k: book.get(k) for k in ("awards", "first_signed", "last_signed", "value", "vehicle", "competition", "vendors")} if book else None,
            "fy_obligations": round(sum(a["obligated_amount"] for a in own_actions)),
            "fy_actions": len(own_actions),
            "fy_by_vendor": [{"vendor": v, "obligated": round(x)} for v, x in Counter({v: sum(a["obligated_amount"] for a in own_actions if (a.get("vendor") or "") == v)
                                                                                  for v in sorted({a.get("vendor") or "" for a in own_actions})}).most_common(8)],
            "fy_by_piid": [{"piid": k, "obligated": round(x), "actions": sum(1 for a in own_actions if a["piid"] == k),
                            "description": next(a.get("description", "") for a in own_actions if a["piid"] == k)[:120],
                            "placed_by": next(a["placed_by"] for a in own_actions if a["piid"] == k),
                            "source": ledger.cite(next(a["page"] for a in own_actions if a["piid"] == k), {"piid": k})}
                           for k, x in Counter({k: sum(a["obligated_amount"] for a in own_actions if a["piid"] == k) for k in sorted({a["piid"] for a in own_actions})}).most_common(15)],
            "fy_by_program": sorted([{"program": p["program"], "pe": p["pe"], "project": p["project"], "obligated": round(program_spend[p["id"]]["obligated"]),
                                      "actions": program_spend[p["id"]]["actions"],
                                      "top_vendors": [{"vendor": v, "obligated": round(x)} for v, x in program_spend[p["id"]]["vendors"].most_common(3)]}
                                     for p in own_programs if p["id"] in program_spend], key=lambda r: (-r["obligated"], r["program"])),
        }
        facts["activity"].append({"office": office["uuid"], **activity})

        # inferences: champions, budget held, remaining
        champions = []
        year_titles = " \n ".join(e["title"] for e in notices + moves + topics)
        pm_rows = [o for o in owners if o.get("program")] or []
        heads = [l for l in lead if l["kind"] == "leadership_as_listed" or l.get("role_type") == "acquisition_leader"]
        candidates = pm_rows if pm_rows else []
        for row in candidates:
            program = row["program"]
            for v in variants(program) or []:
                pat = name_pattern(v)
                named = [e for e in notices + moves + topics if pat.search(e["title"])]
                if named:
                    champions.append({"label": "inference", "office": office["uuid"], "name": row["name"], "role_as_written": row["role_as_written"],
                                      "program": program, "named_in": [{"id": e["id"], "date": e["available_by"], "title": e["title"][:120]} for e in named[:3]],
                                      "rule": CHAMPION_RULE, "from_facts": [row["id"], *[e["id"] for e in named[:3]]]})
                    break
        if not candidates and heads and (notices or moves):
            for h in heads[:1]:
                champions.append({"label": "inference", "office": office["uuid"], "name": h["name"], "role_as_written": h["role_as_written"], "program": None,
                                  "named_in": [{"id": e["id"], "date": e["available_by"], "title": e["title"][:120]} for e in (notices + moves)[:3]],
                                  "rule": CHAMPION_RULE + "; no program manager is listed for this office, so the listed head stands in",
                                  "from_facts": [h["id"], *[e["id"] for e in (notices + moves)[:3]]]})
        seen_c = set()
        champions = [c for c in champions if not (c["name"] in seen_c or seen_c.add(c["name"]))]
        inferences["champions"] += champions

        enacted_key, request_key = mkeys.get("enacted"), mkeys.get("request")
        book_year = f"fy{fy}"
        own_only = lambda rows: [r for r in rows if r["join_basis"] != "program_rows_under_the_line"]  # a line the office shares through its programs is drawn on, not held
        held = {"label": "inference", "office": office["uuid"], "rule": HOLDER_RULE,
                "book_lines": len(own_lines), "book_lines_shared": len(own_lines) - len(own_only(own_lines)),
                "book_sum_current_year": round(sum((pl["amounts"] or {}).get(book_year) or 0 for pl in own_only(own_lines)), 3),
                "program_rows": len(own_programs), "program_sum_current_year": round(sum((p["amounts"] or {}).get(book_year) or 0 for p in own_programs), 3),
                "program_sum_request": round(sum((p["amounts"] or {}).get(f"fy{fy + 1}") or 0 for p in own_programs), 3),
                "display_lines": len(own_measures), "display_lines_shared": len(own_measures) - len(own_only(own_measures)),
                "display_enacted": round(sum((pm["measures"] or {}).get(enacted_key) or 0 for pm in own_only(own_measures)), 3) if enacted_key and own_only(own_measures) else None,
                "display_request": round(sum((pm["measures"] or {}).get(request_key) or 0 for pm in own_only(own_measures)), 3) if request_key and own_only(own_measures) else None,
                "shared_lines_enacted": round(sum((pm["measures"] or {}).get(enacted_key) or 0 for pm in own_measures if pm["join_basis"] == "program_rows_under_the_line"), 3) if enacted_key else None,
                "by_basis": dict(Counter(pl["join_basis"] for pl in own_lines + own_measures)),
                "chain": [{"step": "line", "what": "statutory: the appropriation act funds the program element or line item", "facts": [pl["id"] for pl in (own_lines + own_measures)[:6]]},
                          {"step": "office", "what": "placement by the basis each line states", "facts": [pl["id"] for pl in own_lines[:6]], "evidence_class": "inferred" if any(pl["evidence_class"] == "inferred" for pl in own_lines + own_measures) else "directly_documented" if own_lines or own_measures else "none"},
                          {"step": "listed head", "what": "the office's program managers and leaders as listed", "facts": [l["id"] for l in lead[:6]]},
                          {"step": "above", "what": "the office above it: " + " > ".join(office_name(o, layer.orgs) for o in upward(office["uuid"], layer.orgs)[1:4]), "facts": []}],
                "from_facts": [pl["id"] for pl in own_lines[:10]] + [l["id"] for l in lead[:4]]}
        inferences["budget_held"].append(held)
        # the office's year: its own lines (display enacted where the sheet is read, else the book) plus its named program rows
        own_line_money = held["display_enacted"] if held["display_enacted"] is not None else held["book_sum_current_year"]
        enacted_total = (own_line_money or 0) + held["program_sum_current_year"]
        enacted_basis = (("Comptroller display enacted on the office's own lines" if held["display_enacted"] is not None else "the book's current-year amount on the office's own lines")
                         + (" plus its named program rows" if held["program_rows"] else "")) if (own_line_money or held["program_rows"]) else "no line or program row placed in the office"
        inferences["remaining"].append({"label": "inference", "office": office["uuid"], "fiscal_year": fy,
                                        "enacted_or_book_current_year_musd": round(enacted_total, 3), "enacted_basis": enacted_basis,
                                        "computable": bool(own_line_money or held["program_rows"]),
                                        "obligated_musd": round(activity["fy_obligations"] / 1e6, 3), "remaining_musd": round(enacted_total - activity["fy_obligations"] / 1e6, 3),
                                        "caveats": list(REMAINING_CAVEATS), "rule": "enacted (or the book's current-year amount) less the obligations the saved feed shows placed in the office; a floor, for the reasons listed",
                                        "from_facts": [pl["id"] for pl in (own_measures or own_lines)[:10]]})
        per_office.append({**office, "notes": [n["id"] for n in notes], "leadership": [l["id"] for l in lead], "problem_owners": [o["id"] for o in owners], "budget_lines": [pl["id"] for pl in own_lines],
                           "budget_measures": [pm["id"] for pm in own_measures], "program_rows": [p["id"] for p in own_programs],
                           "champions": len(champions)})
        for o in owners:
            if not o["source"].get("ledger_row"):
                problems.append(f"{office['acronym'] or office['name']}: owner row {o['id']} cites a URL with no ledger row")

    inferences["program_placement"] = [{"label": "inference" if program_place[p["id"]]["basis"] not in DOCUMENTED else "fact", "program": p["id"], **program_place[p["id"]],
                                        "rule": "a program is placed in the office whose person page lists it (one page states the person's office and the program); failing that in the office whose forecast row, notice or award names it (the record is the fact, the placement the reading)"}
                                       for p in program_rows]
    facts["fy_actions"] = [{"piid": a["piid"], "mod": a.get("mod", ""), "signed": a["signed"], "obligated": round(a["obligated_amount"], 2), "vendor": a.get("vendor", ""),
                            "contracting_office": a.get("contracting_office", ""), "funding_office": a.get("funding_office", ""), "office": a["office"], "placed_by": a["placed_by"],
                            "program": a.get("program"), "description": (a.get("description") or "")[:160], "source": ledger.cite(a["page"], {"piid": a["piid"], "mod": a.get("mod", "")})}
                           for a in actions]
    unplaced_lines = [pl for pl in placed_lines if not pl["offices"] and any((pl["amounts"] or {}).get(k) for k in (f"fy{fy}", f"fy{fy + 1}_total", f"fy{fy + 1}"))]
    unplaced_measures = [pm for pm in placed_measures if not pm["offices"]]
    unplaced_programs = [p for p in program_rows if not program_place[p["id"]]["offices"] and any((p["amounts"] or {}).get(k) for k in (f"fy{fy}", f"fy{fy + 1}"))]
    by_month = Counter()
    for a in actions:
        by_month[a["signed"][:7]] += a["obligated_amount"]
    own_funded = [a for a in actions if (a.get("funding_office") or "").upper() in {k.upper() for k in P.get("fpds_offices", {})} or
                  (a.get("contracting_office") or "").upper() in {k.upper() for k in P.get("fpds_offices", {})}]
    spending = {"fy": fy, "window": window, "fpds": {"obligated": round(sum(a["obligated_amount"] for a in actions)), "actions": len(actions),
                                                     "newest_signed": max((a["signed"] for a in actions), default=""),
                                                     "by_month": {k: round(v) for k, v in sorted(by_month.items())},
                                                     "placed_in_an_office": round(sum(a["obligated_amount"] for a in actions if a["office"] and layer.orgs.get(a["office"], {}).get("org_type") not in ("agency", "contracting_office"))),
                                                     "at_a_contracting_office_or_the_agency": round(sum(a["obligated_amount"] for a in actions if not a["office"] or layer.orgs.get(a["office"], {}).get("org_type") in ("agency", "contracting_office"))),
                                                     "matched_to_a_program_row": round(sum(a["obligated_amount"] for a in actions if a.get("program"))),
                                                     **spend_meta},
                "usaspending": usaspending_saved(manifest_rows(), fy),
                "enacted_current_year": {"display_total": round(sum((m["measures"] or {}).get(mkeys["enacted"]) or 0 for m in measures), 3) if mkeys.get("enacted") else None,
                                         "display_label": mkeys.get("enacted"), "book_total": round(sum((l["amounts"] or {}).get(f"fy{fy}") or 0 for l in lines), 3)}}
    enacted = spending["enacted_current_year"]["display_total"] if spending["enacted_current_year"]["display_total"] is not None else spending["enacted_current_year"]["book_total"]
    inferences["remaining"].insert(0, {"label": "inference", "office": None, "scope": "agency-wide", "fiscal_year": fy, "enacted_or_book_current_year_musd": round(enacted, 3),
                                       "obligated_musd": round(spending["fpds"]["obligated"] / 1e6, 3), "remaining_musd": round(enacted - spending["fpds"]["obligated"] / 1e6, 3),
                                       "views": {"fpds_all": round(spending["fpds"]["obligated"] / 1e6, 3),
                                                 "fpds_profile_offices": round(sum(a["obligated_amount"] for a in own_funded) / 1e6, 3),
                                                 **{f"usaspending_{u['kind']}": round(u["total"] / 1e6, 3) for u in spending["usaspending"] if not u["kind"].startswith("recipients")}},
                                       "caveats": list(REMAINING_CAVEATS), "rule": "enacted less obligations, on each view of the same year", "from_facts": []})

    recommendations = [
        {"id": f"rec:{KEY}:unit", "recommendation": "Treat the budget line (and the program row where the book lists one) as the unit of tracking: each has a page or sheet row, "
                                                     "a placement basis, and a request amount that says whether it is growing.", "review_status": "draft", "caveats": ["a line funds many contracts and a contract can draw on several lines"]},
        {"id": f"rec:{KEY}:spend", "recommendation": "Read 'spent this year' as a floor from the saved feed; for a figure to act on, ask the contracting office or read the award "
                                                      "list under the solicitation.", "review_status": "draft", "caveats": list(REMAINING_CAVEATS)},
        {"id": f"rec:{KEY}:people", "recommendation": "Re-run the people stage each quarter: listed starts are recent and program managers rotate; a champion inference expires with the "
                                                       "person's listing.", "review_status": "draft", "caveats": ["a listed role states who holds the position, not who decides a buy"]},
        {"id": f"rec:{KEY}:read", "recommendation": "Before approaching anyone named here, read the page cited on the row; the roles are copied from the listing and nothing in this "
                                                     "report says what they decide.", "review_status": "draft", "caveats": []},
    ]
    all_facts = sum(len(v) for v in facts.values())
    all_inferences = sum(len(v) for v in inferences.values())
    payload = {"generator": GENERATOR, "agency": KEY, "as_of": layer.as_of, "fiscal_year": fy, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "record": {"corpus": str(CORPUS.relative_to(ROOT)), "frozen_at": corpus.get("frozen_at", "")},
               "boundaries": boundaries(layer, roster, staff, lines, measures, spend_meta),
               "measure_labels": {k: v for k, v in mkeys.items() if v}, "measure_headers": {f["exhibit"]: f.get("columns", {}) for f in (_measures_payload or {}).get("files", [])},
               "join_basis_vocabulary": list(JOIN_BASIS),
               "offices": per_office, "silent_offices": silent_offices, "facts": facts, "inferences": inferences, "recommendations": recommendations, "spending": spending,
               "unplaced": {"book_lines": [pl["id"] for pl in unplaced_lines], "display_lines": [pm["id"] for pm in unplaced_measures], "program_rows": [p["id"] for p in unplaced_programs]},
               "problems": problems,
               "summary": {"offices": len(per_office), "silent_offices": len(silent_offices), "facts": all_facts, "inferences": all_inferences, "problems": len(problems), "budget_lines": len(placed_lines) + len(placed_measures),
                           "budget_lines_placed": sum(1 for x in placed_lines + placed_measures if x["offices"]), "program_rows": len(program_rows),
                           "program_rows_placed": sum(1 for p in program_rows if program_place[p["id"]]["offices"]),
                           "fy_obligations": spending["fpds"]["obligated"], "fy_actions": len(actions), "champions": len(inferences["champions"])}}
    return payload


# ------------------------------------------------------------------ the report

def musd(x: float | None) -> str:
    return "-" if x is None else f"{x:,.3f}"


def usd_m(x: float | None) -> str:
    return "—" if not x or abs(x) < 5e4 else f"${x / 1e6:,.1f}M"


def render(payload: dict) -> str:
    fy, b = payload["fiscal_year"], payload["boundaries"]
    facts, inf = payload["facts"], payload["inferences"]
    by_id = {r["id"]: r for group in facts.values() for r in group if isinstance(r, dict) and "id" in r}
    label = P["label"]
    out = [f"# {label}: program offices, who owns which problem, who would champion a fix, who holds the budget",
           "",
           f"Built {payload['generated'][:10]} by `research/tools/office_owners.py build` from the {P['short']} record as of {payload['as_of']} "
           f"(fiscal year {fy}); {payload['record']['corpus']} frozen {payload['record']['frozen_at'][:19]}. Book figures are in $ millions as printed; "
           f"spreadsheet figures are the Comptroller's thousands turned into $ millions; feed figures are dollars.",
           "",
           "## 0. How to read this",
           "",
           "- **Fact** rows carry a source in brackets: URL, the day the ledger saved it, the first twelve hex characters of its sha256, or `frozen record` for a "
           "statement read from the frozen corpus. Nothing in a fact row goes beyond what that page, sheet or feed says.",
           "- **[inference]** marks a reading of ours built from named facts, with the rule it rests on. \"Would champion a fix\" is always an inference. "
           "\"Holds the budget\" is an ordered inference. \"Roughly left\" is arithmetic over two facts with its caveats.",
           "- **Recommendations** sit only in section 6.",
           "- A title is never treated as authority. A person is listed with a role because a saved page lists the role, and that is all the row claims.",
           "- A section over a family the profile never collected opens with the family's boundary (what was collected, how much, how new) rather than \"none\".",
           "",
           "## 1. Who holds the budget: what the sources say",
           "",
           f"- **The record.** {b['record']}.",
           f"- **Budget.** {b['budget']}.",
           f"- **People.** {b['people']}.",
           f"- **Leaders' statements.** {b['leaders']}.",
           f"- **[inference] {HOLDER_RULE}.**",
           "",
           "## 2. Budget line by line",
           ""]
    labels = payload.get("measure_labels", {})
    measures = facts["budget_measures"]
    headers = payload.get("measure_headers", {})
    if measures:
        cols = [k for k in (labels.get("actual"), labels.get("enacted"), labels.get("request"), labels.get("total_request")) if k]
        heads = {k: next((h.get(k) for h in headers.values() if h.get(k)), k) for k in cols}
        out += [f"### 2.1 Lines from the Comptroller display spreadsheets ({len(measures)}; $ millions; the columns are the sheet's own labels)", "",
                "| Exhibit | Account | Line | Code | Title | " + " | ".join(heads[k] for k in cols) + " | Placed in | Basis | Source |",
                "|---|---|---|---|---|" + "---:|" * len(cols) + "---|---|---|"]
        for m in sorted(measures, key=lambda m: (m["exhibit"], m["account"], int(m["line_number"]) if str(m["line_number"]).isdigit() else 0)):
            offices = ", ".join(office_label(o, payload) for o in m["offices"]) or "—"
            out.append(f"| {m['exhibit']} | {m['account']} | {m['line_number']} | {m['code']} | {m['title']} | " + " | ".join(musd((m['measures'] or {}).get(k)) for k in cols)
                       + f" | {offices} | {m['join_basis']} | {short(m['source'])} |")
        out.append("")
    lines = facts["budget_lines"]
    if lines:
        out += [f"### 2.2 Lines from the justification books ({len(lines)}; $ millions as printed)", "",
                f"| PB | Appropriation | Code | Title | FY{fy - 1} | FY{fy} | FY{fy + 1} request | Programs under it | Placed in | Basis | Source |",
                "|---|---|---|---|---:|---:|---:|---:|---|---|---|"]
        for l in sorted(lines, key=lambda l: (l["pb"] or "", l["appropriation"], l["code"])):
            a = l["amounts"] or {}
            programs = sum(1 for p in facts["program_rows"] if p["pe"] == l["code"] and p["pb"] == l["pb"])
            offices = ", ".join(office_label(o, payload) for o in l["offices"]) or "—"
            out.append(f"| {l['pb']} | {l['appropriation']} | {l['code']} | {l['title']} | {musd(a.get(f'fy{fy - 1}'))} | {musd(a.get(f'fy{fy}'))} | "
                       f"{musd(a.get(f'fy{fy + 1}_total', a.get(f'fy{fy + 1}')))} | {programs or '—'} | {offices} | {l['join_basis']} | {short(l['source'])} |")
        out.append("")
    if not measures and not lines:
        out += [f"No budget line is read for this profile: {b['budget']}.", ""]
    sp = payload["spending"]
    f = sp["fpds"]
    out += [f"### 2.3 What has been spent this fiscal year, and roughly what is left", "",
            f"Boundary of the feed: {b['incumbent']}.", "",
            "| Measure | FY" + str(fy) + " to date | Source |", "|---|---:|---|",
            f"| FPDS actions signed in the year on the saved pages, all funding sources | {usd_m(f['obligated'])} over {f['actions']:,} action(s), newest signed {f['newest_signed'] or '—'} | the FPDS ATOM pages in the ledger ({f['sweep_pages']:,} sweep, {f['history_pages']:,} history) |",
            f"| of which placed in a program office by the record | {usd_m(f['placed_in_an_office'])} | the incumbent items' stated placement, else the office code, funding or contracting office |",
            f"| of which matched to a named program row by the action's description | {usd_m(f['matched_to_a_program_row'])} | the book's Title rows against the feed's descriptions |"]
    for u in sp["usaspending"]:
        out.append(f"| USAspending, funding subtier {P['agency']['subtier_name']}: {u['kind']} | {usd_m(u['total'])} | ({u['url']}; saved {u['retrieved_at'][:10]}; sha256 {u['sha256_12']}) |")
    if not sp["usaspending"]:
        out.append(f"| USAspending, funding subtier {P['agency']['subtier_name']} | not collected for this profile and year | run `office_owners.py collect --fy {fy}` (network) |")
    e = sp["enacted_current_year"]
    if e["display_total"] is not None:
        head = next((h.get(e["display_label"]) for h in headers.values() if h.get(e["display_label"])), e["display_label"])
        out.append(f"| \"{head}\" (Comptroller display, all lines kept) | ${e['display_total']:,.1f}M | the display spreadsheets cited in 2.1 |")
    if e["book_total"]:
        out.append(f"| FY{fy} as the justification books print it, all lines read | ${e['book_total']:,.1f}M | the books cited in 2.2 |")
    agency_wide = inf["remaining"][0]
    swept = ", ".join(f"{k} ({v})" for k, v in P.get("fpds_offices", {}).items()) or "none"
    funding = ", ".join(f"{k} ({v})" for k, v in P.get("fpds_funding_agencies", {}).items())
    out += ["", f"**Roughly what is left [inference].** FY{fy} {agency_wide['enacted_or_book_current_year_musd']:,.1f} less obligations: "
            + "; ".join(f"{k.replace('_', ' ')} view {v:,.1f} → remainder {agency_wide['enacted_or_book_current_year_musd'] - v:,.1f}" for k, v in agency_wide["views"].items())
            + " ($ millions). Floors, not answers: " + "; ".join(REMAINING_CAVEATS) + ".",
            "", f"The feed view covers the contracting offices the profile sweeps ({swept})" + (f" and the funding agencies it sweeps ({funding})" if funding else "")
            + f", not every office of the {label}; where the enacted total is the whole component's and the swept offices are a part of it (the Navy), the agency-wide remainder "
            "overstates what is left, and the office sections, which subtract only what the record places in the office, are the figures to read.",
            "", "Month by month (FPDS, $ millions): " + ", ".join(f"{k} {v / 1e6:,.1f}" for k, v in f["by_month"].items()) + ".", ""]
    if facts["program_rows"]:
        placed = sum(1 for p in inf["program_placement"] if p["offices"])
        out += [f"### 2.4 Named programs under the lines ({len(facts['program_rows'])} rows from the books' R-2A pages; {placed} placed in an office)", "",
                "The rows are listed under their office in section 3 and, where no office is found, in section 5.", ""]
    out += ["## 3. The offices", ""]
    for i, office in enumerate(payload["offices"], start=1):
        out += office_section(office, payload, by_id, i)
    if payload.get("silent_offices"):
        silent = payload["silent_offices"]
        out += [f"### 3.{len(payload['offices']) + 1} Offices with no statement this year and no line, leader or owner ({len(silent)})", "",
                "Listed so the absence is a fact about the record, not an omission: " + "; ".join(f"{o['acronym'] or o['name']} ({o['statements_in_record']} statement(s) in all)" for o in silent) + ".", ""]
    out += ["## 4. Across the offices", ""]
    held = [h for h in inf["budget_held"]]
    out += ["| Office | Own lines (book / display) | Shared program elements | Program rows | Own lines FY" + str(fy) + " enacted $M | Own lines FY" + str(fy + 1) + " request $M | Program rows FY" + str(fy) + " $M | Program rows FY" + str(fy + 1) + " $M | FY" + str(fy) + " obligations placed or matched | Champions [inference] |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for office, h in zip(payload["offices"], held):
        act = next(a for a in facts["activity"] if a["office"] == office["uuid"])
        out.append(f"| {office['acronym'] or office['name']} | {h['book_lines'] - h['book_lines_shared']} / {h['display_lines'] - h['display_lines_shared']} | {h['display_lines_shared']} | {h['program_rows']} | "
                   f"{musd(h['display_enacted'])} | {musd(h['display_request'])} | {musd(h['program_sum_current_year'])} | {musd(h['program_sum_request'])} | {usd_m(act['fy_obligations'])} | {office['champions']} |")
    out.append("")
    un = payload["unplaced"]
    out += [f"## 5. Funded lines and program rows placed in no office ({len(un['book_lines'])} book lines, {len(un['display_lines'])} display lines, {len(un['program_rows'])} program rows)", "",
            "Listed, not guessed. A line here may belong to an office above; nothing in the record names one.", ""]
    for pid in un["program_rows"][:200]:
        p = by_id[pid]
        a = p["amounts"] or {}
        out.append(f"- {p['pe']} {p['project'] or ''} {p['program']}: FY{fy} {musd(a.get(f'fy{fy}'))} / FY{fy + 1} {musd(a.get(f'fy{fy + 1}'))} (p.{p['page']}) {short(p['source'])}")
    for lid in un["book_lines"][:200]:
        l = by_id[lid]
        a = l["amounts"] or {}
        out.append(f"- {l['appropriation']} {l['code']} {l['title']}: FY{fy} {musd(a.get(f'fy{fy}'))} {short(l['source'])}")
    for mid in un["display_lines"][:400]:
        m = by_id[mid]
        out.append(f"- {m['exhibit']} {m['account']} {m['code']} {m['title']}: {labels.get('enacted', 'enacted')} {musd((m['measures'] or {}).get(labels.get('enacted')))} {short(m['source'])}")
    out += ["", "## 6. Recommendations", ""]
    for i, r in enumerate(payload["recommendations"], start=1):
        out.append(f"{i}. {r['recommendation']}" + (f" Caveats: {'; '.join(r['caveats'])}." if r["caveats"] else ""))
    out += ["", "## 7. What could not be done, and why", ""]
    for k in ("budget", "people", "incumbent", "forecast", "programs", "hiring", "news", "leaders"):
        out.append(f"- {k}: {b[k]}.")
    out.append(f"- Per-line or per-office obligations are not published: FPDS carries no program element and USAspending returns no program-activity rows for a component; "
               f"the placements above rest on the record's own joins and are floors.")
    for problem in payload["problems"][:50]:
        out.append(f"- {problem}")
    out += ["", "## 8. Sources", ""]
    sources = Counter()
    for group in facts.values():
        for r in group:
            if isinstance(r, dict):
                s = r.get("source") or {}
                if s.get("url"):
                    sources[re.sub(r"\?.*$", "", s["url"]).rsplit("/", 1)[0] if s.get("record") else s["url"]] += 1
    for url, n in sources.most_common(60):
        out.append(f"- {url} ({n} row(s))")
    return "\n".join(out) + "\n"


def office_label(oid: str, payload: dict) -> str:
    for o in payload["offices"]:
        if o["uuid"] == oid:
            return o["acronym"] or o["name"]
    return oid[:8]


def office_section(office: dict, payload: dict, by_id: dict, number: int) -> list[str]:
    fy, facts, inf, b = payload["fiscal_year"], payload["facts"], payload["inferences"], payload["boundaries"]
    act = next(a for a in facts["activity"] if a["office"] == office["uuid"])
    lead = [by_id[i] for i in office["leadership"] if i in by_id]
    owners = [by_id[i] for i in office["problem_owners"] if i in by_id]
    lines = [by_id[i] for i in office["budget_lines"] if i in by_id]
    measures = [by_id[i] for i in office["budget_measures"] if i in by_id]
    programs = [by_id[i] for i in office["program_rows"] if i in by_id]
    held = next(h for h in inf["budget_held"] if h["office"] == office["uuid"])
    left = next(r for r in inf["remaining"] if r.get("office") == office["uuid"])
    champions = [c for c in inf["champions"] if c["office"] == office["uuid"]]
    labels = payload.get("measure_labels", {})
    title = f"{office['acronym']}: {office['name']}" if office["acronym"] and office["acronym"] != office["name"] else office["name"]
    out = [f"### 3.{number} {title}", "",
           f"Type as the record states it: {office['org_type'] or 'unstated'}; {len(office['subtree'])} organization(s) in its subtree" + (" (pilot office)" if office["pilot"] else "") + "."]
    for n in [by_id[i] for i in office.get("notes", []) if i in by_id]:
        out.append(f"The office's own page says: \"{n['source'].get('passage', '')}\" {short(n['source'])}")
    out += ["", "**Leadership as listed (fact of the listing, no authority inferred)**", ""]
    if lead:
        out += ["| Role as written | Name | Observed | Office | Source |", "|---|---|---|---|---|"]
        for l in lead[:25]:
            out.append(f"| {l['role_as_written']} | {l['name']} | {l.get('observed_at') or l.get('effective_from') or ''} | {l['office_name']} | {short(l['source'])} |")
        if len(lead) > 25:
            out.append(f"| … {len(lead) - 25} more listed positions in the JSON | | | | |")
    else:
        out.append(f"No listed leader or program manager in the record for this office. Boundary: {b['people']}.")
    out += ["", "**Problem owners: who a source names against which program or requirement**", ""]
    if owners:
        out += ["| Name | Role as written | Program or requirement | Basis | Source |", "|---|---|---|---|---|"]
        for o in owners[:40]:
            basis = o["owner_basis"] + (" [inference of the link]" if o.get("evidence_class") == "inferred" else "")
            out.append(f"| {o['name']} | {o['role_as_written'][:80]} | {(o.get('program') or '—')[:100]} | {basis} | {short(o['source'])} |")
        if len(owners) > 40:
            out.append(f"| … {len(owners) - 40} more rows in the JSON | | | | |")
        if any(o.get("evidence_class") == "inferred" for o in owners):
            out.append("")
            out.append("Rows marked as an inference of the link: the page names the person as the office's program manager and the record says the office owns the requirement; that the person owns it is the reading.")
    else:
        out.append(f"No source in the record names a person against a program or requirement of this office. Boundary: {b['people']}.")
    out += ["", f"**Budget lines placed in this office ({len(lines)} book, {len(measures)} display, {len(programs)} program rows)**", ""]
    if measures:
        cols = [k for k in (labels.get("enacted"), labels.get("request")) if k]
        out += ["| Exhibit | Code | Title | " + " | ".join(cols) + " | Basis | Source |", "|---|---|---|" + "---:|" * len(cols) + "---|---|"]
        for m in measures[:40]:
            out.append(f"| {m['exhibit']} | {m['code']} | {m['title']} | " + " | ".join(musd((m['measures'] or {}).get(k)) for k in cols) + f" | {m['join_basis']} | {short(m['source'])} |")
    if lines:
        out += ["", f"| PB | Code | Title | FY{fy} | FY{fy + 1} request | Basis | Source |", "|---|---|---|---:|---:|---|---|"]
        for l in lines[:40]:
            a = l["amounts"] or {}
            out.append(f"| {l['pb']} | {l['code']} | {l['title']} | {musd(a.get(f'fy{fy}'))} | {musd(a.get(f'fy{fy + 1}_total', a.get(f'fy{fy + 1}')))} | {l['join_basis']} | {short(l['source'])} |")
    if programs:
        placement = {p["program"]: p for p in inf["program_placement"]}
        out += ["", f"| Program row (book) | PE / project | FY{fy} | FY{fy + 1} | Placed by | FY{fy} obligations matched | Source |", "|---|---|---:|---:|---|---:|---|"]
        spend = {r["program"]: r for r in act["fy_by_program"]}
        for p in sorted(programs, key=lambda p: (-((p["amounts"] or {}).get(f"fy{fy}") or 0), p["program"]))[:60]:
            a = p["amounts"] or {}
            pl = placement.get(p["id"], {})
            basis = pl.get("basis", "")
            ev = pl.get("evidence") or []
            who = "; ".join(f"{x.get('person', '')} ({x.get('program_on_page', '')})" if "person" in x else f"{x.get('kind', '')}: {x.get('title', '')[:60]}" for x in ev[:2])
            out.append(f"| {p['program']} | {p['pe']} {p['project'] or ''} | {musd(a.get(f'fy{fy}'))} | {musd(a.get(f'fy{fy + 1}'))} | {basis}{' [inference]' if basis not in DOCUMENTED else ''}: {who} | "
                       f"{usd_m(spend.get(p['program'], {}).get('obligated'))} | (p.{p['page']}) {short(p['source'])} |")
    if not (lines or measures or programs):
        out.append(f"No budget line is placed in this office. Boundary: {b['budget']}. Lines placed nowhere are listed in section 5.")
    out += ["", "**Budget held [inference]**", "",
            f"{HOLDER_RULE}. For this office: {held['display_lines'] - held['display_lines_shared']} display line(s) of its own summing to {musd(held['display_enacted'])} enacted and {musd(held['display_request'])} requested"
            + (f", and {held['display_lines_shared']} shared program element(s) it draws on through its program rows ({musd(held['shared_lines_enacted'])} enacted in all, not the office's)" if held['display_lines_shared'] else "")
            + f"; {held['program_rows']} program row(s) summing to {musd(held['program_sum_current_year'])} in FY{fy} and {musd(held['program_sum_request'])} requested; "
            f"{held['book_lines']} book line(s), {held['book_lines_shared']} of them shared. Placement bases: {', '.join(f'{k} {v}' for k, v in held['by_basis'].items()) or 'none'}. "
            f"Chain: line → {office['acronym'] or office['name']} → listed head(s): {', '.join(l['name'] for l in lead[:3]) or 'none listed'} → {held['chain'][3]['what'].removeprefix('the office above it: ') or 'no parent stated'}.",
            "",
            (f"**Roughly what is left [inference]**: FY{fy} {left['enacted_basis']}: {musd(left['enacted_or_book_current_year_musd'])} less obligations placed here or matched to its programs "
             f"{musd(left['obligated_musd'])} → {musd(left['remaining_musd'])} ($ millions). Floors: {'; '.join(REMAINING_CAVEATS)}." if left.get("computable") else
             f"**Roughly what is left [inference]**: not computable for this office: {left['enacted_basis']}; FY{fy} obligations placed here {musd(left['obligated_musd'])} ($ millions)."),
            "", f"**What the office bought and is working on now (FY{fy})**", ""]
    if act["notices"]:
        out.append(f"Notices the record places in the office this year ({act['notices_total']}; newest first):")
        out.append("")
        for n in act["notices"][:20]:
            out.append(f"- {n['date']} {n['type'].replace('_', ' ')}{(' (' + n['kind'] + ')') if n['kind'] else ''}: {n['title']} {short(n['source'])}")
        out.append("")
    else:
        out += [f"No notice this year in the office's subtree. Boundary: {b['notice']}.", ""]
    if act["forecast_moves"]:
        out.append(f"Forecast rows that moved this year ({act['forecast_moves_total']}):")
        out.append("")
        for n in act["forecast_moves"][:15]:
            out.append(f"- {n['date']} {n['type'].replace('_', ' ')}: {n['title']} {short(n['source'])}")
        out.append("")
    if act["topics"]:
        out.append(f"SBIR/STTR topics this year ({act['topics_total']}):")
        out.append("")
        for n in act["topics"][:10]:
            out.append(f"- {n['date']}: {n['title']} {short(n['source'])}")
        out.append("")
    if act["fy_by_piid"]:
        out += [f"FY{fy} obligations on the saved feed placed in the office: {usd_m(act['fy_obligations'])} over {act['fy_actions']:,} action(s); by contract:", "",
                "| PIID | Obligated | Actions | Description | Placed by | Source |", "|---|---:|---:|---|---|---|"]
        for r in act["fy_by_piid"]:
            out.append(f"| {r['piid']} | {usd_m(r['obligated'])} | {r['actions']} | {r['description']} | {r['placed_by']} | {short(r['source'])} |")
        out.append("")
        out.append("Top vendors this year: " + "; ".join(f"{v['vendor']} {usd_m(v['obligated'])}" for v in act["fy_by_vendor"][:6]) + ".")
        out.append("")
    else:
        out += [f"No FY{fy} obligation on the saved feed is placed in this office. Boundary: {b['incumbent']}.", ""]
    if act["fy_by_program"]:
        out += [f"Obligations matched to the office's program rows (the action's description names the program):", "",
                "| Program | Obligated | Actions | Top vendors |", "|---|---:|---:|---|"]
        for r in act["fy_by_program"][:15]:
            out.append(f"| {r['program']} | {usd_m(r['obligated'])} | {r['actions']} | {'; '.join(f'{v['vendor']} {usd_m(v['obligated'])}' for v in r['top_vendors'])} |")
        out.append("")
    if act["contract_events"]:
        out.append(f"Contract events this year on incumbents the record ties to the office ({act['contract_events_total']}; newest first):")
        out.append("")
        for c in act["contract_events"][:12]:
            out.append(f"- {c['date']}: {c['title']} {short(c['source'])}")
        out.append("")
    if act["buying_book"]:
        bk = act["buying_book"]
        val = bk.get("value") or {}
        veh = bk.get("vehicle") or {}
        vend = bk.get("vendors") or {}
        out.append(f"How it buys (office book from the saved FPDS pages): {bk.get('awards')} base awards {bk.get('first_signed')} to {bk.get('last_signed')}; median value ${(val.get('median') or 0) / 1e6:,.1f}M; "
                   f"{round((veh.get('under_an_idv') or 0) * 100)}% under a vehicle; {vend.get('distinct')} vendors, top three {round((vend.get('top3_share') or 0) * 100)}%.")
        out.append("")
    if act["hiring"] or act["news"]:
        for h in act["hiring"][:5]:
            out.append(f"- hiring {h['date']}: {h['title']} {short(h['source'])}")
        for n in act["news"][:5]:
            out.append(f"- news {n['date']}: {n['title']} {short(n['source'])}")
        out.append("")
    out += ["**Who would champion a fix [inference]**", "", f"Rule: {CHAMPION_RULE}.", ""]
    if champions:
        for c in champions[:10]:
            named = "; ".join(f"{n['date']} {n['title'][:90]}" for n in c["named_in"])
            out.append(f"- {c['name']} ({c['role_as_written']}){(': the page lists ' + c['program']) if c.get('program') else ''}; the office's record this year names it: {named}.")
    else:
        out.append(f"Nothing matched: no listed person's stated program or office appears in a notice, forecast move or topic of the office this year. {CHAMPION_RULE.split(';')[1].strip().capitalize()}.")
    out.append("- The listed head(s) above are approval points for any new program by the listing's description of the role, not champions of a specific fix; the listing does not say what they favour.")
    out.append("")
    return out


# ------------------------------------------------------------------ page, collect

def page(argv: list[str]) -> int:
    if not OUT_JSON.exists():
        print(f"no {OUT_JSON.relative_to(ROOT)}; run build first")
        return 1
    payload = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    want = " ".join(argv).strip().lower()
    by_id = {r["id"]: r for group in payload["facts"].values() for r in group if isinstance(r, dict) and "id" in r}
    for i, office in enumerate(payload["offices"], start=1):
        if want in (office["acronym"].lower(), office["name"].lower(), office["seed_id"].lower()):
            print("\n".join(office_section(office, payload, by_id, i)))
            return 0
    print(f"no office {want!r}; offices: " + ", ".join(o["acronym"] or o["name"] for o in payload["offices"]))
    return 1


def collect(argv: list[str]) -> int:
    """(network) The fiscal year's USAspending totals for the profile's funding subtier: obligations by month for contracts,
    IDVs and assistance, and the top recipients; each answer a ledger row with its query body."""
    from fetch import fetch  # noqa: PLC0415
    ap = argparse.ArgumentParser(prog="office_owners.py collect")
    ap.add_argument("--fy", type=int, default=None)
    args = ap.parse_args(argv)
    fy = args.fy or fiscal_year(date.today().isoformat())
    lo, hi = fy_window(fy)
    subtier = P["agency"]["subtier_name"]
    agencies = [{"type": "funding", "tier": "subtier", "name": subtier, "toptier_name": P["agency"]["toptier_name"]}]
    groups = {"contracts": ["A", "B", "C", "D"], "idvs": ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"],
              "assistance": ["02", "03", "04", "05", "06", "07", "08", "09", "10", "11"]}
    queries = []
    for kind, codes in groups.items():
        queries.append((f"{kind} FY{fy} by month", USASPENDING + "spending_over_time/",
                        {"group": "month", "filters": {"agencies": agencies, "time_period": [{"start_date": lo, "end_date": hi}], "award_type_codes": codes}}))
    queries.append((f"recipients FY{fy}", USASPENDING + "spending_by_category/recipient/",
                    {"filters": {"agencies": agencies, "time_period": [{"start_date": lo, "end_date": hi}]}, "limit": 25, "page": 1}))
    with MANIFEST.open("a", encoding="utf-8") as handle:
        for kind, url, body in queries:
            row = fetch(url, "direct", None, f"{USASPENDING_NOTE} {kind} ({subtier})", payload=body)
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            print(row.get("status"), kind, row.get("size"), row.get("error", ""))
    return 0


# ------------------------------------------------------------------ selfcheck

def selfcheck() -> int:
    assert fiscal_year("2026-09-29") == 2026 and fiscal_year("2025-10-01") == 2026 and fiscal_year("2025-09-30") == 2025
    assert fy_window(2026) == ("2025-10-01", "2026-09-30") and in_window("2026-03-01", fy_window(2026)) and not in_window("2025-09-30", fy_window(2026))
    assert norm("  Multi-X  Office (MXO) ") == "multi x office mxo"
    v = variants("Network of Optimal Dynamic Energy Signatures (NODES)")
    assert "NODES" in v and "Network of Optimal Dynamic Energy Signatures" in v and v[0].startswith("Network"), v
    assert variants("Support") == [] and variants("Program Support Services") == [] and variants("systems") == [], "generic names never join"
    assert variants("Oversight") == ["Oversight"] and variants("Fleetwood") == ["Fleetwood"], "a one-word program name is kept and matched in context"
    assert variants("SPQ-9B Radar") == ["SPQ-9B Radar"] and variants("CANES") == ["CANES"]
    assert name_pattern("NODES").search("BTO NETWORK OF OPTIMAL DYNAMIC ENERGY SIGNATURES (NODES) AWARD") and not name_pattern("NODES").search("ANODES")
    assert name_pattern("GO", strict=True).search("GENERATIVE OPTOGENETICS (GO) TA2") and not name_pattern("GO", strict=True).search("GO TO THE SITE FOR SERVICES")
    assert name_pattern("Fleetwood", strict=True).search("FLEETWOOD PROGRAM PHASE 1") and name_pattern("Fleetwood", strict=True).search("BTO: FLEETWOOD TA1")
    assert not name_pattern("Oversight", strict=True).search("ENGINEERING OVERSIGHT AND SUPPORT SERVICES")
    assert name_pattern("Generative Optogenetics", strict=True).search("BTO GENERATIVE OPTOGENETICS TA2"), "a multi-word name needs no context"
    ledger = Ledger([{"url": "https://sam.gov/api/prod/opps/v2/opportunities/0123456789abcdef0123456789abcdef", "status": 200, "sha256": "abcdef123456" + "0" * 52,
                      "retrieved_at": "2026-09-01T00:00:00Z", "path": "x"},
                     {"url": "https://example.gov/a", "status": 200, "sha256": "f" * 64, "retrieved_at": "2026-01-01T00:00:00Z", "path": "x"},
                     {"url": "https://example.gov/a", "status": 200, "sha256": "e" * 64, "retrieved_at": "2026-02-01T00:00:00Z", "path": "x"},
                     {"url": "https://example.gov/b", "status": 404}])
    c = ledger.cite("https://sam.gov/opp/0123456789abcdef0123456789abcdef/view", {"event": "e1"}, record="frozen record 2026")
    assert c["sha256_12"] == "abcdef123456" and c["ledger_row"] and short(c).startswith("(https://sam.gov/opp/"), "a notice page is found by its id on the API address"
    assert ledger.cite("https://example.gov/a")["sha256_12"] == "e" * 12, "the newest saved copy is cited"
    missing = ledger.cite("https://example.gov/b", record="frozen record 2026")
    assert missing["sha256_12"] is None and not missing["ledger_row"] and "frozen record" in short(missing)
    # the champion rule on a three-event fixture: a match inside the year, the same name outside it, a generic word
    window = fy_window(2026)
    events = [{"id": "n1", "available_by": "2026-02-01", "title": "Future Program: Fleetwood", "family": "notice"},
              {"id": "n2", "available_by": "2025-02-01", "title": "Fleetwood Proposers Day", "family": "notice"},
              {"id": "n3", "available_by": "2026-05-01", "title": "Support services", "family": "notice"}]
    year = [e for e in events if in_window(e["available_by"], window)]
    hit = [e for e in year if any(name_pattern(v).search(e["title"]) for v in variants("Fleetwood (FLW)"))]
    assert [e["id"] for e in hit] == ["n1"], hit
    assert not any(name_pattern(v).search("Support services") for v in variants("Support"))
    assert JOIN_BASIS[-1] == "unplaced" and DOCUMENTED == {"office_code_in_text", "program_named_on_people_page"}
    assert measure_keys([{"measures": {"fy2025_actual": 1, "fy2026_enacted": 1, "fy2027_request": 1, "fy2027_mandatory_request": 1, "fy2027_total": 1}}]) == \
        {"actual": "fy2025_actual", "enacted": "fy2026_enacted", "request": "fy2027_request", "total_request": "fy2027_total"}
    assert page_text('<main><p>Announcing the expansion of the Microsystems Technology Office into the Multi X Office (MXO).\\u0026nbsp;\\u003C\\/p\\u003E</p></main>') == \
        "Announcing the expansion of the Microsystems Technology Office into the Multi X Office (MXO)."
    assert all(w not in CHAMPION_RULE.lower() and w not in HOLDER_RULE.lower() for w in ("decision-maker", "decision maker"))
    assert round(4321.5 - 2575.7, 1) == 1745.8  # the arithmetic the remaining row does, on the DARPA figures of 2026-09-29
    print("office_owners selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"build": build, "page": page, "collect": collect}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

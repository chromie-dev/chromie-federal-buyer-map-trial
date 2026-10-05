#!/usr/bin/env python3
"""Stakeholders: who plays which part when an office buys, and the evidence for each part.

Every person the layer knows (people.json) is placed in the buckets below. Each bucket rests on evidence rows, every
row with its source and date, and says how it was reached: "listed" (the agency lists the role), "stated" (a source
says it in words: a biography, the program listing naming the manager, the budget book), "self-stated" (the person's
public LinkedIn profile) or "inferred" (read from signals).

  decision_maker    the agency's director, an office's director or deputy (listed)
  budget_holder     the manager of a program the budget book funds this fiscal year or next (stated: the listing names
                    the manager, the book the money); a manager whose programs the book does not match is one by role.
                    Where the books name no manager (the Navy), the one person a current record places in an office's
                    single post (its program manager, its program executive officer) holds the programs tied to that
                    office (inferred: programs.py records how each program was tied)
  budget_process    an office's assistant director for program management and its program analysts, who run budget
                    formulation, execution and justification and so know what is left (listed)
  problem_owner     the manager of a program whose book states the need; anyone whose biography states research
                    interests or whose recorded remarks state a capability priority; the requirements contact and the
                    alternate a forecast record names for its office's requirement (stated)
  champion          a problem owner or decision maker with two or more signals of acting now: new in the post, a
                    program that grows next year or starts, a solicitation out, talks to industry, an industry
                    background (inferred; every signal carries its evidence)
  technical         deputy program managers, innovation fellows, support contractors posted to the agency (listed)
  end_user_liaison  an operational liaison a service posts to the agency (stated in the biography)
  contracting       the contacts on solicitations; a shared mailbox is marked and never counted as a person (stated)
  small_business    the small business office's staff (listed)

Organizations a program's book names are kept on the program as its named users and partners, a transition partner
when "transition" is in the same sentence; people there are not collected.

A person the agency publishes no biography of is looked up in Exa's index of public LinkedIn profiles, and a program
office no known person leads is searched the same way (`collect`). A profile is used only when the name matches and a
current or recent post names the agency or the office; a profile that matches the name alone is kept as a candidate
and used for nothing. A discovered person is used only when a current post names the office's code or full name.

  AGENCY=darpa python research/tools/stakeholders.py collect [--limit N]   # network: Exa, about $0.01 a search
  AGENCY=darpa python research/tools/stakeholders.py build [--check]       # <agency memory>/stakeholders.json
  python research/tools/stakeholders.py --selfcheck
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import EVENTS, MANIFEST, MEMORY, NOTE_TAG, P  # noqa: E402
from agency_layers_sql import uid  # noqa: E402
from people import RECENT, ROLE_WORDS, norm_name, post_standings, standing  # noqa: E402
from programs import office_money  # noqa: E402

OUT = MEMORY / "stakeholders.json"
PEOPLE = MEMORY / "people.json"
PROGRAMS = MEMORY / "programs.json"
SEED = MEMORY / "organization_seed.json"
REMARKS = EVENTS / "remarks_events.json"
STAFF_LISTING = P["people"].get("staff_listing")
CURRENT = P["people"].get("program_current") or ""
RAW = ROOT / "data" / "raw" / "people"
EXA_URL = "https://api.exa.ai/search"
PROFILE_NOTE = f"person profile{NOTE_TAG}:"
OFFICE_NOTE = f"office people{NOTE_TAG}:"
LEADING = {"acquisition_leader", "contracting_leader", "program_manager", "deputy_program_manager", "technical_lead", "other"}
DISCOVER_TYPES = {"program_office", "program_executive_office", "direct_reporting_program_manager"}
NEW_IN_POST = timedelta(days=548)  # eighteen months: a manager's first programs are started then
RECENT_NOTICE = timedelta(days=180)
RECENT_TALK = timedelta(days=365)
RECENT_POST_YEARS = 2
CAP = 4  # evidence rows kept per bucket, newest first

BUCKETS = ("decision_maker", "budget_holder", "budget_process", "problem_owner", "champion", "technical", "end_user_liaison",
           "contracting", "small_business")
ROLE_BUCKET = {"acquisition_leader": "decision_maker", "contracting_leader": "contracting", "contract_specialist": "contracting",
               "program_manager": "budget_holder", "deputy_program_manager": "technical", "technical_lead": "technical"}
# A title rule wins over the role type: DARPA's assistant director for program management is typed a leader by title words.
TITLE_BUCKET = [(re.compile(r"requirements contact on forecast record|\brequirements (?:development|branch|division|manager|officer|lead)", re.I), "problem_owner"),
                (re.compile(r"program management|program analyst|budget|comptroller|\bfinanc|resource manag", re.I), "budget_process"),
                (re.compile(r"innovation fellow|deputy program manager|program manager\W*s? rep|\bPMR\b|technical director|\bengineer\b|\bscientist\b", re.I), "technical"),
                (re.compile(r"contracting officer|contract specialist|procuring contracting|\bprocurement\b", re.I), "contracting"),
                (re.compile(r"small business", re.I), "small_business"),
                (re.compile(r"program manager|product (?:line )?manager|project manager|portfolio manager|\bP?APM\b", re.I), "budget_holder"),
                (re.compile(r"\bdirector\b|program executive|acquisition executive|commander|commanding officer|\bchief\b|secretary|"
                            r"\bassistant (?:commissioner|commandant|administrator)", re.I), "decision_maker")]

# A title with none of these names an office, not a job ("PEO IWS"): no bucket can be read from it.
ROLE_NOUN = re.compile(r"manager|director|officer|analyst|engineer|scientist|specialist|lead\b|head\b|chief|deputy|principal|executive|"
                       r"commander|representative|\brep\b|\bP?APM\b|\bPMR\b|\bILSM\b|coordinator|advis[eo]r|administrator|assistant|"
                       r"supervisor|architect|fellow|technician|planner|accountant|liaison", re.I)

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
ABBREV = re.compile(r"(?:\b(?:Lt|Col|Gen|Maj|Capt|Cmdr|Adm|Sgt|Dr|Mr|Ms|Mrs|St|No|Jr|Sr|Ph\.D|U\.S|Calif|Mass)|\b[A-Z])\.$")
INTEREST_RE = re.compile(r"research interests|area of focus|areas of focus|focus(?:es)? (?:is |are |will be )?on|interests include", re.I)
PRIOR_RE = re.compile(r"\bprior to\b|\bpreviously\b|\bbefore joining\b|\bmost recently\b|\bserved as\b|\bformerly\b", re.I)
INDUSTRY_RE = re.compile(r"\b(?:Inc\.?|LLC|Corp\.?|Corporation|company|companies|start-?up|founder|co-founder|founded|"
                         r"chief (?:executive|technology|operating) officer|CEO|CTO|venture|businesses)\b")
LIAISON_RE = re.compile(r"\bliaison\b", re.I)
USERS = [("Army", r"\bArmy\b"), ("Navy", r"\bNavy\b|\bNaval\b"), ("Air Force", r"\bAir Force\b"), ("Marine Corps", r"\bMarine Corps\b|\bUSMC\b"),
         ("Space Force", r"\bSpace Force\b"), ("Special Operations Command", r"\bSpecial Operations Command\b|\bU?SOCOM\b"),
         ("Missile Defense Agency", r"\bMissile Defense Agency\b"), ("Coast Guard", r"\bCoast Guard\b"),
         ("combatant commands", r"\bcombatant commands?\b|\bINDOPACOM\b|\bEUCOM\b|\bCENTCOM\b|\bNORTHCOM\b|\bSOUTHCOM\b|\bAFRICOM\b|\bSPACECOM\b|\bSTRATCOM\b|\bTRANSCOM\b|\bCYBERCOM\b"),
         ("Intelligence Community", r"\bIntelligence Community\b"), ("NASA", r"\bNASA\b"),
         ("Department of Homeland Security", r"\bHomeland Security\b"), ("Department of Energy", r"\bDepartment of Energy\b")]
USER_RES = [(name, re.compile(rx)) for name, rx in USERS]  # ponytail: a DoD list; a civilian profile names its own when one needs it
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
DATES_RE = re.compile(r"^(?:([A-Z][a-z]{2}) )?(\d{4}) - (?:(Present)|(?:([A-Z][a-z]{2}) )?(\d{4}))")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
RETIRED_RE = re.compile(r"\b(?:retired|former|veteran)\b", re.I)
STALE_CLAIM_YEARS, STALE_MILITARY_YEARS = 8, 4  # a profile left unedited: a civilian rarely holds one post past eight years, an officer rotates in three
STALE_ACTING_YEARS = 1  # an acting post is a stopgap: an SES detail runs 120 days, a vacancy's acting officer 210
ACTING_RE = re.compile(r"\bacting\b", re.I)
MILITARY_RE = re.compile(r"^(?:CAPT|Capt\.?|CDR|Cmdr\.?|LCDR|Col\.?|Lt\.? ?Col\.?|RADM|RDML|VADM|Maj\.?)\b")
# Posts one person holds at a time, by the office type they are held in: an older claim to the same post is an unedited profile.
SINGLE_POSTS = {"program_office": ("major program manager", "program manager"), "direct_reporting_program_manager": ("program manager",),
                "program_executive_office": ("program executive officer",),
                # a component has one of each: two current claims to its CISO are one stale profile, not two CISOs
                "command": ("component acquisition executive", "chief information officer", "chief information security officer",
                            "chief technology officer", "chief data officer", "chief financial officer", "chief procurement officer")}


def norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", html.unescape(text or "").upper())


def day(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def manifest_rows() -> list[dict]:
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()] if MANIFEST.exists() else []


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


CONTACT = re.compile(r"[,;]?\s*(?:[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b)")


def row(basis: str, text: str, source_url: str = "", observed_at: str = "") -> dict:
    """One piece of evidence; an e-mail address or phone number the source prints beside a name is not kept."""
    return {"basis": basis, "text": CONTACT.sub("", " ".join((text or "").split()))[:600], "source_url": source_url or "", "observed_at": observed_at or ""}


# ---------------------------------------------------------------- what the sources say

def sentences(text: str) -> list[str]:
    """Sentences, not split after a rank or an abbreviation ('Lt. Col. Kim', 'U.S. Army')."""
    out: list[str] = []
    for part in SENTENCE.split(" ".join((text or "").split())):
        if out and ABBREV.search(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return [x for x in out if x]


def paragraphs(body_html: str) -> list[str]:
    parts = re.split(r"</p>|</li>|</h\d>|<br\s*/?>", body_html or "")
    return [t for t in (" ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)).split()) for p in parts) if t]


def bio_facts(body_html: str) -> dict:
    """What a biography states in words: interests, prior posts, a liaison post, education. Sentences are kept verbatim."""
    paras = paragraphs(body_html)
    cut = next((i for i, p in enumerate(paras) if p.lower() == "education"), len(paras))
    said = [s for p in paras[:cut] for s in sentences(p)]
    return {"interests": [s for s in said if INTEREST_RE.search(s)], "prior": [s for s in said if PRIOR_RE.search(s)],
            "liaison": [s for s in said if LIAISON_RE.search(s) and re.search(r"\bjoined\b|\bis (?:the|a|an)\b|\bserves\b", s)],
            "education": paras[cut + 1:], "text": " ".join(said)}


def staff_records(rows: list[dict]) -> dict[str, dict]:
    """The newest saved staff listing, one record per person's page (the key people.py writes as source_url), the one
    carrying the biography."""
    hits = [r for r in rows if STAFF_LISTING and r.get("url") == STAFF_LISTING and kept_page(r) and (ROOT / r["path"]).exists()]
    if not hits:
        return {}
    listing = max(hits, key=lambda r: r["retrieved_at"])
    base, out = P["people"].get("staff_base_url") or "", {}
    for rec in json.loads((ROOT / listing["path"]).read_text(encoding="utf-8")):
        page = base + (rec.get("view_node") or "") if rec.get("view_node") else ""
        if page and (page not in out or (rec.get("body") and not out[page].get("body"))):
            out[page] = rec
    return out


def profile_posts(text: str) -> tuple[str, list[dict]]:
    """A LinkedIn profile as Exa's index writes it: the name, then each post under Experience with its dates and the
    index's line on the employer ("... is a government agency")."""
    lines = [LINK_RE.sub(r"\1", line).strip() for line in (text or "").splitlines()]
    name = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
    posts, inside, org_head = [], False, ""
    for line in lines:
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "experience"
            continue
        if not inside:
            continue
        head = re.match(r"^(#{3,4}) (.+)$", line)
        if head:
            level, title = head.groups()
            current = title.endswith("(Current)")
            title = title.removesuffix("(Current)").strip()
            if " - " in title:
                title, org = title.rsplit(" - ", 1)
            elif level == "###":
                title, org, org_head = "", title, title
            else:
                org = org_head
            retired = bool(RETIRED_RE.search(title))
            posts.append({"title": title.strip(), "org": org.strip(), "current": current and not retired, "retired": current and retired, "start": "", "end": "", "blurb": ""})
            continue
        if posts:
            post, dates = posts[-1], DATES_RE.match(line)
            if dates and not post["start"]:
                m1, y1, present, m2, y2 = dates.groups()
                post["start"] = f"{y1}-{MONTHS[m1]:02d}" if m1 in MONTHS else y1
                post["end"] = "present" if present else (f"{y2}-{MONTHS[m2]:02d}" if m2 in MONTHS else y2 or "")
                post["current"] = (post["current"] or bool(present)) and not RETIRED_RE.search(post["title"])
                post["retired"] = post["retired"] or bool(present and RETIRED_RE.search(post["title"]))
            elif line and not post["blurb"] and " is a" in line:
                post["blurb"] = line[:300]
    return name, posts


def sector(post: dict) -> str:
    blurb, org = post["blurb"].lower(), post["org"]
    if "government agency" in blurb or re.search(r"\b(?:Army|Navy|Naval|Air Force|Marine|Department|Agency|Command|DEVCOM|Laboratory)\b", org):
        return "government"
    if re.search(r"\b(?:University|College)\b", org) or "higher education" in blurb:
        return "academia"
    return "industry" if " company" in blurb or re.search(r"\b(?:Inc|LLC|Corp|Corporation)\b", org) else ""


def same_name(a: str, b: str) -> bool:
    x, y = norm_name(a).split(), norm_name(b).split()
    return bool(x and y) and x[0] == y[0] and x[-1] == y[-1]


def recent(post: dict, asof: date) -> bool:
    return post["current"] or (post["end"][:4].isdigit() and int(post["end"][:4]) >= asof.year - RECENT_POST_YEARS)


def mentions(text: str, tokens: set[str]) -> bool:
    """A long name or a code is found in the text however it is spaced ('PMW-120', 'PMW 120'); a short word only as a
    word, so 'DON' is not found in 'Donaldson'."""
    n, up = norm(text), (text or "").upper()
    return any((len(norm(t)) >= 6 and norm(t) in n) or re.search(rf"(?<![A-Z0-9]){re.escape(t.upper())}(?![A-Z0-9])", up)
               for t in tokens if len(norm(t)) >= 3)


def names_any(post: dict, tokens: set[str]) -> str:
    """'employer' when the post's employer names the agency or office, 'title' when only the title does (a support
    contractor writes 'Engineer - DARPA - Their Company'), '' when neither."""
    if mentions(post["org"], tokens):
        return "employer"
    return "title" if mentions(post["title"], tokens) else ""


def match_profile(person_name: str, results: list[dict], tokens: set[str], asof: date) -> dict | None:
    """The profile that is this person: the name matches and a current or recent post names the agency or office.
    A name-only match is returned marked so, and nothing is read from it."""
    candidate = None
    for r in results:
        name, posts = profile_posts(r.get("text") or "")
        if not same_name(name or r.get("title") or "", person_name):
            continue
        hit = next(((p, how) for p in posts if recent(p, asof) and (how := names_any(p, tokens))), None)
        view = {"url": r.get("url") or "", "name": name, "posts": posts}
        if hit:
            return {**view, "status": "confirmed", "post": hit[0], "named_by": hit[1]}
        candidate = candidate or {**view, "status": "name_only"}
    return candidate


def node_names(node: dict) -> list[str]:
    return [node.get("name") or ""] + [a["text"] if isinstance(a, dict) else str(a) for a in node.get("aliases") or []]


def node_codes(node: dict) -> set[str]:
    return {m.group(0) for n in node_names(node) for m in re.finditer(P["reading"]["office_code_re"], n)}


def shared_codes(offices: list[dict]) -> set[str]:
    """Codes more than one office carries: 'CBP' in each CBP requirements office, 'PEO AVIATION' in each of its project
    offices. Such a code names the group, so a post that names it places the person in none of them."""
    seen = Counter(c for n in offices for c in node_codes(n))
    return {c for c, k in seen.items() if k > 1}


def office_tokens(node: dict, shared: set[str] = frozenset()) -> set[str]:
    """What an office is called in a post: its codes ('PMW120' from 'PMW 120', 'PMA/PMW-101') and its full names."""
    names = node_names(node)
    codes = node_codes(node) - shared
    tail = node["id"].split(":", 1)[-1]
    if re.search(r"[a-z]", tail) and re.search(r"\d", tail) and len(norm(tail)) >= 4:  # 'office:pmo-555': the id carries a code the regex does not know
        codes.add(tail.upper())
    return codes | {n for n in names if len(norm(n)) >= 12}


def agency_tokens(seed: dict) -> set[str]:
    words = {P["short"].removeprefix("U.S. "), P["agency"]["subtier_name"], P["agency"]["subtier_abbreviation"]}
    for n in seed.get("nodes", []):
        if n["type"] in ("agency", "command"):
            words |= {n["name"]} | {a["text"] if isinstance(a, dict) else str(a) for a in n.get("aliases") or []}
    return {w for w in words if len(norm(w)) >= 3}


def mailbox(name: str) -> bool:
    tokens = norm_name(name).split()
    return not tokens or set(tokens) <= ROLE_WORDS


def named_users(prog: dict) -> list[dict]:
    """Organizations the book names in the program's need and plans, and whether a sentence ties them to transition."""
    found: dict[str, dict] = {}
    parts = [("description", prog.get("problem") or "")] + sorted((prog.get("plans") or {}).items())
    for where, text in parts:
        for sentence in sentences(text):
            for name, rx in USER_RES:
                if rx.search(sentence):
                    hit = found.setdefault(name, {"organization": name, "transition": False, "spans": []})
                    hit["transition"] |= "transition" in sentence.lower()
                    if len(hit["spans"]) < 2:
                        hit["spans"].append({"in": where, "text": sentence[:300]})
    return sorted(found.values(), key=lambda h: (not h["transition"], h["organization"]))


def program_names(prog: dict) -> list[str]:
    names = {prog["title"]} | {re.sub(r"\s*\([^)]*\)\s*$", "", b["title"]) for b in prog["budget"]["blocks"]}
    names |= {m.group(1) for b in prog["budget"]["blocks"] if (m := re.search(r"\(([^)]+)\)\s*$", b["title"]))}
    return sorted(n for n in names if len(norm(n)) >= 3)


def names_pattern(names: list[str]) -> re.Pattern:
    """One pattern for a program's names, compiled once: 1,944 book lines are more patterns than the re cache keeps."""
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(sorted(map(re.escape, names), key=len, reverse=True) or ["(?!)"]) + r")(?![A-Za-z0-9])", re.I)


def names_program(context: str, names: list[str]) -> bool:
    return bool(names_pattern(names).search(context or ""))


# ---------------------------------------------------------------- the buckets

def listed_buckets(position: dict, first_sentence: str = "") -> list[str]:
    text = f"{position.get('raw_title') or ''} {first_sentence}"
    by_title = [b for rx, b in TITLE_BUCKET if rx.search(position.get("raw_title") or "")]
    out = by_title[:1] or ([ROLE_BUCKET[position["role_type"]]] if position.get("role_type") in ROLE_BUCKET else [])
    return out + (["end_user_liaison"] if LIAISON_RE.search(text) else [])


def discovered(office_hits: dict[str, list[dict]], offices: list[dict], known: set[str], asof: date) -> tuple[list[dict], list[dict]]:
    """People an office search found, each placed in the office a current post names (whichever search returned
    them), the most specific office when several are named, else the command it names ('U.S. Customs and Border
    Protection' for a CBP requirements office). Someone a company employs is a support contractor. A name
    the layer already knows is left to its own record. Set aside, with the reason: a post claimed longer than anyone
    holds it, the older of two claims to a post one person holds, and a title that names an office but no job."""
    rank = {"program_office": 0, "direct_reporting_program_manager": 1, "program_executive_office": 2}
    offices = sorted(offices, key=lambda n: (rank.get(n["type"], 3), n["id"]))
    shared = shared_codes([n for n in offices if n["type"] in DISCOVER_TYPES])  # a command keeps its code: it is the group
    tokens = {n["id"]: office_tokens(n, shared if n["type"] in DISCOVER_TYPES else frozenset()) for n in offices}
    kind = {n["id"]: n["type"] for n in offices}
    out, aside = [], []
    for _, answers in sorted(office_hits.items()):
        for answer in answers:
            for r in answer["results"]:
                name, posts = profile_posts(r.get("text") or "")
                if not name or norm_name(name) in known:
                    continue
                hit = next(((p, n["id"]) for n in offices for p in posts if p["current"] and mentions(f"{p['title']} {p['org']}", tokens[n["id"]])), None)
                if not hit:
                    continue
                post, office = hit
                known.add(norm_name(name))
                years = asof.year - int(post["start"][:4]) if post["start"][:4].isdigit() else 0
                if years > (STALE_ACTING_YEARS if ACTING_RE.search(f"{post['title']} {post['org']}") else STALE_MILITARY_YEARS if MILITARY_RE.search(name) else STALE_CLAIM_YEARS):
                    aside.append({"name": name, "office": office, "url": r.get("url") or "", "reason": f"claims {post['title'] or post['org']} since {post['start']}: an unedited profile"})
                    continue
                # a retirement dated at or after the post began: the profile kept the post after the person left
                gone = next((p for p in posts if p["retired"] and p["start"] and p["start"] >= post["start"]), None)
                if gone:
                    aside.append({"name": name, "office": office, "url": r.get("url") or "", "reason": f"claims {post['title'] or post['org']} since {post['start']} and retirement since {gone['start']}"})
                    continue
                contractor = sector(post) == "industry"
                if not contractor and not ROLE_NOUN.search(post["title"] or post["org"]):
                    aside.append({"name": name, "office": office, "url": r.get("url") or "", "reason": f"title states no job: {post['title'] or post['org']}"})
                    continue
                b = "technical" if contractor else (listed_buckets({"raw_title": post["title"] or post["org"]}) or ["technical"])[0]
                text = f"{'support contractor: ' if contractor else ''}{post['title']} - {post['org']} (since {post['start'] or '?'})"
                before = next((p for p in posts if not p["current"] and sector(p) == "industry"), None)
                out.append({"id": uid("person", f"linkedin:{r.get('url') or name}"), "name": name, "offices": [office], "page": "", "start": post["start"],
                            "listed_title": "", "emails": [], "mailbox": False, "programs": [], "bio": None,
                            "linkedin": {"status": "discovered", "url": r.get("url") or "", "name": name, "current": post, "contractor": contractor,
                                         "observed_at": answer["retrieved_at"][:10],
                                         # a government post the profile also shows as current, begun after this one: this one may be
                                         # the old post left open (PEO Ships beside PAE Maritime); an older open post is not a conflict
                                         "also_current": [f"{p['title']} - {p['org']} (since {p['start']})" for p in posts
                                                          if p["current"] and p is not post and sector(p) == "government"
                                                          and p["start"] > (post["start"] or "9999") and p["title"] != post["title"]]},
                            "buckets": {b: [row("self-stated", text, r.get("url") or "", answer["retrieved_at"][:10])]}, "champion_signals": 0,
                            "_industry": f"{before['title']} - {before['org']} ({before['start']} to {before['end']})" if before else ""})
    holders: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for person in (p for p in out if not p["linkedin"]["contractor"]):
        title = (person["linkedin"]["current"]["title"] or person["linkedin"]["current"]["org"]).lower()
        post = next((p for p in SINGLE_POSTS.get(kind[person["offices"][0]], ()) if re.match(rf"(?:acting )?{p}\b", title)), None)
        if post:
            holders[(person["offices"][0], post)].append(person)
    for (office, post), claims in holders.items():
        newest = max(claims, key=lambda c: c["start"])
        for c in claims:
            if c is not newest and c["start"] < newest["start"]:
                out.remove(c)
                aside.append({"name": c["name"], "office": office, "url": c["linkedin"]["url"],
                              "reason": f"claims {post} since {c['start']}; {newest['name']} claims it since {newest['start']}"})
    return out, aside


def office_holders(people: list[dict], found: list[dict], nodes: dict[str, dict], asof: date) -> dict[str, dict]:
    """Who last held each office's one post (its program manager, its program executive officer): a position a source
    dates within two years, or a discovered profile's current post. The newest claim wins; two people claiming the
    post on the same date leave the office without a holder. Each holder carries its standing from its own claims:
    confirmed_current only when an official source stated the post within six months, else recently_observed."""
    claims: dict[str, list[tuple[str, dict, dict]]] = defaultdict(list)
    holds_post = lambda title, node: any(re.match(rf"(?:acting )?{p}\b", (title or "").lower()) for p in SINGLE_POSTS.get(node["type"], ()))  # noqa: E731
    for person in people:
        for q in person["positions"]:
            node = nodes.get(q.get("office") or "")
            d = day(q.get("observed_at"))
            if node and q.get("source") != "sam_gov_site_api" and d and asof - d <= RECENT and holds_post(q.get("raw_title"), node):
                claims[node["id"]].append((q["observed_at"][:10], person, q))
    for person in found:
        post, node = person["linkedin"]["current"], nodes.get(person["offices"][0])
        if node and holds_post(post["title"] or post["org"], node):
            seen = {"source": "linkedin_profile", "observed_at": person["linkedin"]["observed_at"], "source_url": person["linkedin"]["url"]}
            claims[node["id"]].append((post["start"] or "", person, seen))
    out = {}
    for office, cs in claims.items():
        newest = max(c[0] for c in cs)
        holders = {c[1]["id"]: c[1] for c in cs if c[0] == newest}
        if len(holders) == 1:
            person = next(iter(holders.values()))
            out[office] = {"person": person, "standing": standing([c[2] for c in cs if c[1] is person], asof.isoformat())}
    return out


def held_rows(mine: list[dict], fy: int, url_of: dict[str, str], listed_on: str, held_as: dict[str, tuple[str, dict]]) -> tuple[list[dict], list[dict]]:
    """The budget holder and problem owner rows a person's programs give: stated where the listing names the person as
    the manager, inferred where the person holds the office a book program is tied to (held_as: program id -> office
    and the holder's standing; a holder only recently observed is written as last observed, never as holding it now)."""
    held, owned = [], []
    for prog in mine:
        a = prog["budget"]["amounts_musd"]
        money = {y: a.get(f"fy{y}") for y in (fy, fy + 1)}
        src = prog.get("url") or prog["listing_source"]["url"]
        office, holder = held_as.get(prog["id"]) or ("", {})
        basis = "inferred" if office else "stated"
        tied = (prog.get("office_basis") or {}).get("basis", "")
        post = f"holds {office}" if holder.get("status") == "confirmed_current" else f"was last observed holding {office} on {holder.get('observed_at')} (not confirmed current)"
        if any(v for v in money.values()):
            what = (f"{post}; {prog['title']} is tied to it" + (f" ({tied[:220]})" if tied else "") if office
                    else f"manages {prog['title']}")
            held.append(row(basis, f"{what}: FY{fy} ${money[fy]}M, FY{fy + 1} ${money[fy + 1]}M in the budget book"
                                   + ("" if office else f" (matched by {prog['budget']['match']})"), src, listed_on))
        if prog.get("problem"):
            book = prog["budget"]["blocks"][0]["book"] if prog["budget"]["blocks"] else ""
            owned.append(row(basis, f"{prog['title']}: {prog['problem'][:400]}", url_of.get(book, ""), ""))
    return held, owned


def growth_signal(mine: list[dict], fy: int) -> dict | None:
    """The person's program that grows most next year, or starts this year; one signal however many grow."""
    rises = []
    for prog in mine:
        a = prog["budget"]["amounts_musd"]
        this, nxt, prev = a.get(f"fy{fy}"), a.get(f"fy{fy + 1}"), a.get(f"fy{fy - 1}")
        if this is not None and nxt is not None and nxt > this:
            why = f"; the book says: {prog['change_statement'][:250]}" if prog.get("change_statement") else ""
            rises.append((nxt - this, row("inferred", f"{prog['title']} grows: FY{fy} ${this}M to FY{fy + 1} ${nxt}M{why}", prog.get("url") or "", "")))
        elif not prev and this:
            rises.append((this, row("inferred", f"{prog['title']} starts: nothing in FY{fy - 1}, ${this}M in FY{fy}", prog.get("url") or "", "")))
    return max(rises, key=lambda r: r[0])[1] if rises else None


def notice_signal(mine: list[dict], notices: dict[str, list[dict]], asof: date) -> dict | None:
    out = [n for prog in mine for n in notices.get(prog["id"], []) if (d := day(n["posted"])) and timedelta(0) <= asof - d <= RECENT_NOTICE]
    if not out:
        return None
    n = max(out, key=lambda n: n["posted"])
    return row("inferred", f"soliciting: {n['notice']} ({n['context']})", n["source_url"], n["posted"])


def build_stakeholders(people: list[dict], staff: dict[str, dict], programs: dict, remarks: dict, seed: dict,
                       profiles: dict[str, list[dict]], office_hits: dict[str, list[dict]], url_of: dict[str, str], asof: date) -> dict:
    fy = programs.get("fiscal_year") or asof.year + (asof.month >= 10)
    nodes = {n["id"]: n for n in seed.get("nodes", [])}
    small_business = {i for i, n in nodes.items() if "small business" in (n.get("name") or "").lower()}
    a_tokens = agency_tokens(seed)
    progs = [p for p in programs.get("programs", []) if not CURRENT or p["status"] == CURRENT]
    completed = [p for p in programs.get("programs", []) if CURRENT and p["status"] != CURRENT]
    by_name: dict[str, list[dict]] = defaultdict(list)
    for person in people:
        by_name[norm_name(person["name"])].append(person)

    def person_for(name: str, office: str) -> dict | None:
        cands = by_name.get(norm_name(name)) or []
        same = [c for c in cands if office in c.get("offices", [])]
        return (same or cands)[0] if len(same) == 1 or (not same and len(cands) == 1) else None

    managed: dict[str, list[dict]] = defaultdict(list)
    for prog in progs:
        if prog.get("manager") and (who := person_for(prog["manager"], prog.get("office") or "")):
            managed[who["id"]].append(prog)
    found, set_aside = discovered(office_hits, [n for n in nodes.values() if n["type"] in DISCOVER_TYPES | {"command"}], {norm_name(p["name"]) for p in people}, asof)
    holders = office_holders(people, found, nodes, asof)
    held_as: dict[str, tuple[str, dict]] = {}  # a book program no listing names a manager for: held by whoever holds its office
    for prog in progs:
        if not prog.get("manager") and (holder := holders.get(prog.get("office") or "")):
            managed[holder["person"]["id"]].append(prog)
            node = nodes[prog["office"]]
            held_as[prog["id"]] = ((node.get("codes") or {}).get("office_code") or node.get("name") or prog["office"], holder["standing"])

    # notices: a solicitation contact's position names the program in its context
    notices: dict[str, list[dict]] = defaultdict(list)
    pattern_of = {p["id"]: names_pattern(program_names(p)) for p in progs}
    by_first: dict[str, set[str]] = defaultdict(set)  # a name's first word: the programs worth testing a context against
    for prog in progs:
        for n in program_names(prog):
            by_first[next(iter(re.findall(r"[a-z0-9]+", n.lower())), "")].add(prog["id"])
    for person in people:
        for q in person["positions"]:
            if q.get("source") != "sam_gov_site_api":
                continue
            maybe = set().union(*(by_first[w] for w in set(re.findall(r"[a-z0-9]+", (q.get("context") or "").lower())) & by_first.keys()))
            for prog in progs:
                if prog["id"] in maybe and pattern_of[prog["id"]].search(q.get("context") or ""):
                    notices[prog["id"]].append({"notice": q.get("raw_title") or "", "posted": q.get("observed_at") or "", "context": q.get("context") or "",
                                                "source_url": q.get("source_url") or "", "contact": person["name"], "contact_id": person["id"],
                                                "emails": person.get("emails", []), "mailbox": mailbox(person["name"])})

    talks: dict[str, list[dict]] = defaultdict(list)
    statements: dict[str, list[dict]] = defaultdict(list)
    for doc in remarks.get("documents", []):
        src = url_of.get(doc.get("path") or "", "") or doc.get("hearing_url") or ""
        speaker = person_for(doc.get("speaker_name") or "", "") if doc.get("speaker_name") else None
        if speaker and (doc.get("audience") == "industry" or doc.get("kind") == "conference"):
            talks[speaker["id"]].append(row("inferred", f"spoke to {doc.get('audience') or 'a conference'}: {doc.get('event_name') or doc.get('title') or ''}", src, doc.get("issued") or ""))
        for ev in doc.get("events") or []:
            who = person_for(ev.get("person") or "", "") if ev.get("person") else speaker
            if who and ev.get("event_type") == "capability_priority":
                statements[who["id"]].append(row("stated", f"{ev.get('statement') or ''} ({doc.get('event_name') or doc.get('title') or ''})", src, doc.get("issued") or ""))
    for person in people:
        for q in person["positions"]:
            if q.get("source") == "conference_pages_exa" and (q.get("context") or "").startswith("spoke"):
                talks[person["id"]].append(row("inferred", q["context"], q.get("source_url") or "", q.get("observed_at") or ""))

    out_people = []
    roster = bool(staff)  # an agency that publishes its staff: who it lists is who works there now
    fresh = lambda q: (d := day(q.get("observed_at"))) is not None and asof - d <= RECENT  # noqa: E731
    for person in people:
        buckets: dict[str, list[dict]] = defaultdict(list)
        listed = next((q for q in person["positions"] if q.get("source") == "agency_staff_listing"), None)
        # a former director on a 2016 hearing page, or an official of another agency a conference page placed here, is not staff.
        # This only decides whom the buckets list; whether the record confirms the post today is the person's standing
        current = listed is not None if roster else any(fresh(q) for q in person["positions"] if q.get("source") != "sam_gov_site_api")
        rec = staff.get(listed["source_url"]) if listed else None
        bio = bio_facts(rec.get("body") or "") if rec else None
        page = listed.get("source_url") if listed else ""
        start = str((rec or {}).get("field_start_date__raw") or "")[:10]
        liaison = bio["liaison"][0] if bio and bio["liaison"] else ""  # a liaison the biography states as the present post
        for q in sorted(person["positions"], key=lambda q: q.get("observed_at") or "", reverse=True):
            if not fresh(q) and q is not listed:
                continue
            if q.get("source") == "sam_gov_site_api":
                what = f"{'shared mailbox' if mailbox(person['name']) else 'contact'} on {q.get('raw_title') or 'a notice'}: {q.get('context') or ''}"
                buckets["contracting"].append(row("stated", what, q.get("source_url"), q.get("observed_at")))
                continue
            if not current:
                continue
            for b in listed_buckets(q, liaison if q is listed else ""):
                basis = "listed" if q.get("source") == "agency_staff_listing" and b != "end_user_liaison" else "stated"
                text = (bio["liaison"][0] if b == "end_user_liaison" and bio and bio["liaison"] else q.get("context") or q.get("raw_title") or "")
                buckets[b].append(row(basis, text, q.get("source_url"), q.get("observed_at")))
            if q.get("office") in small_business:
                buckets["small_business"].append(row("listed", q.get("context") or q.get("raw_title") or "", q.get("source_url"), q.get("observed_at")))

        linkedin = None
        tokens = a_tokens | {t for o in person.get("offices", []) if o in nodes for t in office_tokens(nodes[o])}
        for answer in profiles.get(person["key"], []):
            if found_profile := match_profile(person["name"], answer["results"], tokens, asof):
                linkedin = {**found_profile, "observed_at": answer["retrieved_at"][:10]}
            if linkedin and linkedin["status"] == "confirmed":
                break
        if linkedin and linkedin["status"] == "confirmed":
            post = linkedin["post"]
            for b in listed_buckets({"raw_title": post["title"]}):
                if b not in buckets:
                    buckets[b].append(row("self-stated", f"{post['title']} - {post['org']} (since {post['start'] or '?'})", linkedin["url"]))
            if linkedin["named_by"] == "title":
                buckets["technical"].append(row("self-stated", f"support contractor: {post['title']} - {post['org']}", linkedin["url"]))
            start = start or (post["start"] + "-01" if len(post["start"]) == 7 else "")

        mine = managed.get(person["id"], [])
        held, owned = held_rows(mine, fy, url_of, programs.get("listing_retrieved_at", "")[:10], held_as)
        # each claim names its post (office and role), so a record confirms only the post it names
        seen = person["positions"] + [{"office": p.get("office") or "", "role_type": "program_manager", "source": "program_listing",
                                       "observed_at": programs.get("listing_retrieved_at", ""), "source_url": p.get("url") or ""}
                                      for p in mine if p["id"] not in held_as]
        if linkedin and linkedin["status"] == "confirmed":  # a profile places no office; its title is the post it states
            seen.append({"office": "", "role_type": linkedin["post"]["title"], "source": "linkedin_profile",
                         "observed_at": linkedin.get("observed_at", ""), "source_url": linkedin["url"]})
        buckets["budget_holder"] += held
        buckets["problem_owner"] += owned
        if bio:
            for s in bio["interests"][:1]:
                buckets["problem_owner"].append(row("stated", s, page, (listed or {}).get("observed_at", "")))
        if current and statements.get(person["id"]):
            buckets["problem_owner"] += statements[person["id"]]

        signals = []  # one row per kind of signal, so two growing programs are one signal
        if start and (d := day(start)) and timedelta(0) <= asof - d <= NEW_IN_POST:
            signals.append(row("inferred", f"new in the post: since {start}", page or (linkedin or {}).get("url", ""), start))
        signals += [s for s in (growth_signal(mine, fy), notice_signal(mine, notices, asof)) if s]
        recent_talks = [t for t in talks.get(person["id"], []) if (d := day(t["observed_at"])) and timedelta(0) <= asof - d <= RECENT_TALK]
        if recent_talks:
            signals.append(max(recent_talks, key=lambda t: t["observed_at"]))
        industry = [s for s in (bio or {}).get("prior", []) if INDUSTRY_RE.search(s)]
        if industry:
            signals.append(row("inferred", f"industry background: {industry[0]}", page, ""))
        elif linkedin and linkedin["status"] == "confirmed" and (post := next((p for p in linkedin["posts"] if not p["current"] and sector(p) == "industry"), None)):
            signals.append(row("inferred", f"industry background: {post['title']} - {post['org']} ({post['start']} to {post['end']})", linkedin["url"], ""))
        if current and len(signals) >= 2 and (buckets.get("problem_owner") or buckets.get("decision_maker")):
            buckets["champion"] = signals

        if not any(buckets.values()):
            continue
        kept = {b: sorted(buckets[b], key=lambda r: r["observed_at"], reverse=True)[:CAP] if b != "champion" else buckets[b] for b in BUCKETS if buckets.get(b)}
        out_people.append({"id": person["id"], "name": person["name"], "offices": person.get("offices", []), "page": page, "start": start,
                           "listed_title": (listed or {}).get("raw_title", ""), "emails": person.get("emails", []),
                           "mailbox": mailbox(person["name"]), "programs": sorted(p["id"] for p in mine), "posts": post_standings(seen, asof.isoformat()),
                           "bio": {k: bio[k] for k in ("interests", "prior", "education")} if bio else None,
                           "linkedin": {k: linkedin[k] for k in ("status", "url", "name")} | ({"current": linkedin["post"]} if linkedin.get("post") else {}) if linkedin else None,
                           "buckets": kept, "champion_signals": len(buckets.get("champion", []))})

    for person in found:  # a discovered holder of an office gets its programs' evidence and signals as anyone else
        industry = person.pop("_industry")
        person["posts"] = post_standings([{"office": person["offices"][0], "role_type": person["linkedin"]["current"]["title"], "source": "linkedin_profile",
                                           "observed_at": person["linkedin"]["observed_at"], "source_url": person["linkedin"]["url"]}], asof.isoformat())
        mine = managed.get(person["id"], [])
        if not mine:
            continue
        held, owned = held_rows(mine, fy, url_of, "", held_as)
        buckets = defaultdict(list, person["buckets"])
        buckets["budget_holder"] += held
        buckets["problem_owner"] += owned
        start, url = person["start"], person["linkedin"]["url"]
        since = start + "-01" if len(start) == 7 else start  # a month the profile states opens on its first day, as for a listed person
        signals = []
        if since and (d := day(since)) and timedelta(0) <= asof - d <= NEW_IN_POST:
            signals.append(row("inferred", f"new in the post: since {start}", url, since))
        signals += [s for s in (growth_signal(mine, fy), notice_signal(mine, notices, asof)) if s]
        if industry:
            signals.append(row("inferred", f"industry background: {industry}", url, ""))
        if len(signals) >= 2 and (buckets.get("problem_owner") or buckets.get("decision_maker")):
            buckets["champion"] = signals
        person["buckets"] = {b: sorted(buckets[b], key=lambda r: r["observed_at"], reverse=True)[:CAP] if b != "champion" else buckets[b] for b in BUCKETS if buckets.get(b)}
        person["champion_signals"] = len(buckets.get("champion", []))
        person["programs"] = sorted(p["id"] for p in mine)
    out_people += found
    out_people.sort(key=lambda p: (norm_name(p["name"]), p["id"]))
    by_id = {p["id"]: p for p in out_people}
    in_bucket = lambda b, office: sorted(p["id"] for p in out_people if b in p["buckets"] and office in p["offices"])  # noqa: E731
    # a named manager's standing is that of the manager post at the program's office, never of the person's other posts
    post_of = lambda pid, office, role: next(({k: v for k, v in p.items() if k not in ("office", "role")} for p in by_id.get(pid, {}).get("posts", [])  # noqa: E731
                                              if (p["office"], p["role"]) == (office, role)), None)

    # a program office's decisions go up to the office it reports to (a PMW to its PEO) when it has no decision maker of its own
    parents: dict[str, list[str]] = defaultdict(list)
    for r in sorted(seed.get("relationships", []), key=lambda r: r["to"]):
        if r["type"] == "child_of" and not r.get("retraction") and not r.get("effective_to"):
            parents[r["from"]].append(r["to"])
    decides = lambda office: next(((o, ids) for o in [office, *parents.get(office, [])] if (ids := in_bucket("decision_maker", o))), (office, []))  # noqa: E731

    out_programs = []
    for prog in sorted(progs, key=lambda p: (p.get("office") or "", p["title"])):
        who = person_for(prog["manager"], prog.get("office") or "") if prog.get("manager") else holders[prog["office"]]["person"] if prog["id"] in held_as else None
        office = prog.get("office") or ""
        contacts = sorted({(n["contact_id"], n["mailbox"]) for n in notices.get(prog["id"], []) if fresh({"observed_at": n["posted"]})})
        decided_in, deciders = decides(office) if office else ("", [])
        out_programs.append({"id": prog["id"], "title": prog["title"], "office": office, "url": prog.get("url") or "",
                             "manager": {"name": prog.get("manager") or (who or {}).get("name", ""), "person": who["id"] if who else None,
                                         "held_by_office": prog["id"] in held_as,
                                         "standing": held_as[prog["id"]][1] if prog["id"] in held_as else post_of(who["id"], office, "program_manager") if who else None},
                             "office_basis": prog.get("office_basis"),
                             "money_musd": prog["budget"]["amounts_musd"], "change_statement": prog.get("change_statement") or "",
                             "left": prog.get("left"), "spend_found_usd": (prog.get("spend") or {}).get("obligated"),
                             "stakeholders": {"decision_maker": deciders, "decision_maker_office": decided_in,
                                              "budget_holder": [who["id"]] if who and who["id"] in by_id else [],
                                              "budget_process": in_bucket("budget_process", office),
                                              "champion": [who["id"]] if who and "champion" in by_id.get(who["id"], {}).get("buckets", {}) else [],
                                              "contracting": [c for c, is_box in contacts if not is_box], "contracting_mailboxes": [c for c, is_box in contacts if is_box]},
                             "notices": sorted({(n["posted"], n["notice"], n["source_url"]) for n in notices.get(prog["id"], [])}, reverse=True)[:5],
                             "named_users": named_users(prog)})

    offices = sorted({o for p in out_people for o in p["offices"]} | {p["office"] for p in out_programs if p["office"]})
    out_offices = []
    by_office = programs.get("offices") or office_money(progs, fy)  # a transaction two programs share counts once
    for office in offices:
        mine = [p for p in out_programs if p["office"] == office]
        money = by_office.get(office) or {}
        out_offices.append({"office": office, "name": (nodes.get(office) or {}).get("name", ""),
                            **{b: in_bucket(b, office) for b in BUCKETS if b != "contracting"},
                            "contracting_contacts": len(in_bucket("contracting", office)),
                            "current_programs": len(mine), "money_musd": money.get("amounts_musd", {}),
                            "spend_found_usd": (money.get("spend") or {}).get("obligated"),
                            "left_musd": (money.get("left") or {}).get("amount_musd"), "left_note": money.get("left_note", ""),
                            "completed_programs": sorted(p["title"] for p in completed if p.get("office") == office),
                            "top_recipients_found": money.get("top_recipients_found", [])})

    count = lambda b: sum(1 for p in out_people if b in p["buckets"] and not p["mailbox"])  # noqa: E731
    return {"as_of": asof.isoformat(), "fiscal_year": fy, "buckets": list(BUCKETS), "people": out_people, "programs": out_programs, "offices": out_offices,
            "set_aside": sorted(set_aside, key=lambda a: (a["office"], a["name"])),
            "summary": {"people": sum(not p["mailbox"] for p in out_people), "mailboxes": sum(p["mailbox"] for p in out_people),
                        **{b: count(b) for b in BUCKETS},
                        "linkedin_confirmed": sum(1 for p in out_people if (p["linkedin"] or {}).get("status") == "confirmed"),
                        "linkedin_discovered": sum(1 for p in out_people if (p["linkedin"] or {}).get("status") == "discovered"),
                        "programs": len(out_programs), "programs_with_budget_holder": sum(bool(p["stakeholders"]["budget_holder"]) for p in out_programs),
                        "programs_with_champion": sum(bool(p["stakeholders"]["champion"]) for p in out_programs),
                        "programs_with_contracting_contact": sum(bool(p["stakeholders"]["contracting"] or p["stakeholders"]["contracting_mailboxes"]) for p in out_programs),
                        "programs_with_named_users": sum(bool(p["named_users"]) for p in out_programs)}}


# ---------------------------------------------------------------- the ledger

def saved_answers(rows: list[dict], prefix: str) -> dict[str, list[dict]]:
    """Each saved Exa answer under its note's subject (a person's key or an office id), newest first."""
    out: dict[str, list[dict]] = defaultdict(list)
    mine = [r for r in rows if (r.get("note") or "").startswith(prefix) and kept_page(r)]
    for r in sorted(mine, key=lambda r: r.get("retrieved_at") or "", reverse=True):
        if (ROOT / r["path"]).exists():
            body = json.loads((ROOT / r["path"]).read_text(encoding="utf-8"))
            out[r["note"][len(prefix):].strip()].append({"results": body.get("results") or [], "retrieved_at": r["retrieved_at"]})
    return out


def as_of(rows: list[dict], people: list[dict], programs: dict) -> date:
    """The newest input's date, so a rebuild from the same inputs writes the same file. The saved profile and office
    lookups are inputs too: a profile read after the record date would otherwise be dated in its future."""
    listing = [r.get("retrieved_at") or "" for r in rows if kept_page(r) and ((STAFF_LISTING and r.get("url") == STAFF_LISTING)
                                                                              or (r.get("note") or "").startswith((PROFILE_NOTE, OFFICE_NOTE)))]
    dates = listing + [programs.get("listing_retrieved_at") or ""] + [p.get("last_seen") or "" for p in people]
    return max(d for d in (day(x) for x in dates) if d) if any(day(x) for x in dates) else date.today()


def wanted(people: list[dict], staff: dict[str, dict], seed: dict) -> tuple[list[dict], list[dict]]:
    """Who to look up: people in a leading role the agency publishes no biography of; and program offices no known
    person holds a leading role in."""
    persons = [p for p in people if any(q.get("role_type") in LEADING and q.get("source") != "sam_gov_site_api" for q in p["positions"])
               and not any(staff.get(q.get("source_url") or "", {}).get("body") for q in p["positions"])]
    led = {q.get("office") for p in people for q in p["positions"] if q.get("role_type") in LEADING and q.get("source") != "sam_gov_site_api"}
    offices = [n for n in seed.get("nodes", []) if n["type"] in DISCOVER_TYPES and n["id"] not in led]
    return sorted(persons, key=lambda p: p["key"]), sorted(offices, key=lambda n: n["id"])


def collect(argv: list[str]) -> int:
    from llm import env_value
    from routergrowth import post_json, record_answer

    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 10_000
    key = env_value("EXA_API_KEY")
    if not key:
        print("EXA_API_KEY is not set", file=sys.stderr)
        return 1
    rows = manifest_rows()
    people = load(PEOPLE, {}).get("rows", [])
    persons, offices = wanted(people, staff_records(rows), load(SEED, {}))
    asked = {r["note"] for r in rows if kept_page(r) and (r.get("note") or "").startswith((PROFILE_NOTE, OFFICE_NOTE))}
    label = P["short"].removeprefix("U.S. ")
    shared = shared_codes([n for n in load(SEED, {}).get("nodes", []) if n["type"] in DISCOVER_TYPES])
    code = lambda n: next((m.group(0) for m in re.finditer(P["reading"]["office_code_re"], n["name"]) if m.group(0) not in shared), n["name"])  # noqa: E731
    jobs = [(f"{OFFICE_NOTE} {n['id']}", f"{code(n)} {label} program manager", 5) for n in offices]
    jobs += [(f"{PROFILE_NOTE} {p['key']}", f"{' '.join(norm_name(p['name']).split()).title()} {label}", 3) for p in persons]
    todo = [j for j in jobs if j[0] not in asked]
    print(f"{len(offices)} office(s) and {len(persons)} person(s) to look up; {len(jobs) - len(todo)} already asked; sending {min(len(todo), limit)}")
    spent = 0.0
    for note, query, results in todo[:limit]:
        payload = {"query": query, "numResults": results, "category": "linkedin profile", "contents": {"text": {"maxCharacters": 6000}}}
        try:
            body = post_json(EXA_URL, payload, {"x-api-key": key, "Content-Type": "application/json"})
        except Exception as exc:  # a refused query is reported and the next one sent; nothing is recorded for it
            print(f"  failed: {note}: {type(exc).__name__}: {exc}")
            continue
        record_answer(EXA_URL, body, RAW, note)
        spent += float((json.loads(body).get("costDollars") or {}).get("total") or 0)
    print(f"done; ${spent:.3f} spent")
    return 0


def build(argv: list[str]) -> int:
    rows = manifest_rows()
    people = load(PEOPLE, {}).get("rows", [])
    if not people:
        print("no people.json; run `people.py` first", file=sys.stderr)
        return 1
    programs = load(PROGRAMS, {})
    url_of = {r["path"]: r.get("final_url") or r.get("url") or "" for r in rows if r.get("path") and r.get("status") == 200}
    result = build_stakeholders(people, staff_records(rows), programs, load(REMARKS, {}), load(SEED, {}),
                                saved_answers(rows, PROFILE_NOTE), saved_answers(rows, OFFICE_NOTE), url_of, as_of(rows, people, programs))
    text = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.name} differs from a fresh build; run `stakeholders.py build` to regenerate", file=sys.stderr)
            return 1
        print(f"{OUT.name} matches a fresh build")
        return 0
    OUT.write_text(text, encoding="utf-8")
    s = result["summary"]
    print(f"{s['people']} people ({s['mailboxes']} mailboxes aside): " + ", ".join(f"{b} {s[b]}" for b in BUCKETS)
          + f"; {s['programs_with_champion']}/{s['programs']} programs with a champion -> {OUT.relative_to(ROOT)}")
    return 0


# ---------------------------------------------------------------- check

PROFILE_FIXTURE = """# Ann Lee

Engineer and program manager

## About

Builds things.

## Experience

### Program Manager - DARPA (Current)

Mar 2026 - Present in Arlington, Virginia

Defense Advanced Research Projects Agency is a government agency.

### Chief Technology Officer - [Acme Robotics Inc](https://www.linkedin.com/company/acme)

Jan 2019 - Feb 2026 (7 years)

Acme Robotics Inc is a Robotics company with 50-60 employees.

### Systems Engineer - DARPA - [Support Co](https://www.linkedin.com/company/support)

2016 - 2018

## Education

MIT
"""


def selfcheck() -> int:
    body = ("<p>Ann Lee, Ph.D., joined DARPA in March 2026 as a program manager. Her research interests include autonomy at sea.</p>"
            "<p>Prior to joining DARPA, Lee was chief technology officer of Acme Robotics Inc. She &amp; her team built drones.</p>"
            "<h4>Education</h4><ul><li>Ph.D., MIT</li></ul>")
    bio = bio_facts(body)
    assert bio["interests"] == ["Her research interests include autonomy at sea."] and bio["education"] == ["Ph.D., MIT"], bio
    assert bio["prior"] == ["Prior to joining DARPA, Lee was chief technology officer of Acme Robotics Inc."] and INDUSTRY_RE.search(bio["prior"][0])
    assert sentences("Lt. Col. Bo Kim, U.S. Army, joined DARPA. He flies.") == ["Lt. Col. Bo Kim, U.S. Army, joined DARPA.", "He flies."]
    assert not bio_facts("<p>Bo Kim is a program manager. He served as Army liaison to NATO.</p>")["liaison"], "a past liaison post is not the post"
    liaison = bio_facts("<p>Col. Kim joined DARPA as special assistant to the director/Army operational liaison.</p>")
    assert liaison["liaison"] and listed_buckets({"raw_title": "Special Assistant", "role_type": "other"}, liaison["text"]) == ["end_user_liaison"]
    assert listed_buckets({"raw_title": "Assistant Director Program Management", "role_type": "acquisition_leader"}) == ["budget_process"]
    assert listed_buckets({"raw_title": "Office Director", "role_type": "acquisition_leader"}) == ["decision_maker"]
    assert listed_buckets({"raw_title": "Program Manager", "role_type": "program_manager"}) == ["budget_holder"]
    assert mailbox("Solicitation Coordinator") and mailbox("") and not mailbox("Kerry Payne")

    name, posts = profile_posts(PROFILE_FIXTURE)
    assert name == "Ann Lee" and [p["title"] for p in posts] == ["Program Manager", "Chief Technology Officer", "Systems Engineer - DARPA"], posts
    assert posts[0]["current"] and posts[0]["start"] == "2026-03" and posts[1]["end"] == "2026-02" and posts[1]["org"] == "Acme Robotics Inc"
    assert [sector(p) for p in posts] == ["government", "industry", ""]
    asof = date(2026, 9, 29)
    tokens = {"DARPA"}
    hit = match_profile("Dr. Ann Lee", [{"url": "u1", "text": PROFILE_FIXTURE}], tokens, asof)
    assert hit["status"] == "confirmed" and hit["named_by"] == "employer" and hit["post"]["title"] == "Program Manager"
    stale = PROFILE_FIXTURE.replace("Program Manager - DARPA (Current)", "Deputy Director - Army Lab (Current)").replace("Systems Engineer - DARPA - ", "Engineer - ")
    assert match_profile("Ann Lee", [{"url": "u2", "text": stale}], tokens, asof)["status"] == "name_only", "no post names the agency"
    assert match_profile("Ann Leeds", [{"url": "u3", "text": PROFILE_FIXTURE}], tokens, asof) is None
    contractor = {"title": "Systems Engineer - DARPA", "org": "Support Co", "current": True, "end": "present"}
    assert names_any(contractor, tokens) == "title"
    pmw = office_tokens({"id": "pmw:120", "name": "PMW 120 Battlespace Awareness Program Office", "aliases": ["PMW-120"]})
    assert mentions("Assistant Program Manager - PMW120", pmw) and not mentions("Assistant Program Manager - PMW 130", pmw)
    mlb = [{"id": f"office:{a}", "name": f"PEO MLB {a} Program Office"} for a in ("Boats", "Craft")]
    assert shared_codes(mlb) == {"PEO MLB"} and not mentions("Program Manager - PEO MLB", office_tokens(mlb[0], shared_codes(mlb)))
    navwar = {"id": "command:navwar", "type": "command", "name": "Naval Information Warfare Systems Command"}
    pmw_node = {"id": "pmw:120", "type": "program_office", "name": "PMW 120 Battlespace Awareness Program Office"}
    for post, office in (("Naval Information Warfare Systems Command", "command:navwar"), ("PMW 120, Naval Information Warfare Systems Command", "pmw:120")):
        hits = {"pmw:120": [{"results": [{"url": "u4", "text": PROFILE_FIXTURE.replace("Manager - DARPA (Current)", f"Manager - {post} (Current)")}],
                             "retrieved_at": "2026-09-29T00:00:00Z"}]}
        assert [p["offices"] for p in discovered(hits, [navwar, pmw_node], set(), asof)[0]] == [[office]], "the office a post names, else its command"
    two = [{"url": f"u{i}", "text": PROFILE_FIXTURE.replace("# Ann Lee", f"# {who}").replace("Program Manager - DARPA (Current)", "Chief Information Security Officer - Naval Information Warfare Systems Command (Current)").replace("Mar 2026", start)}
           for i, (who, start) in enumerate((("Ann Lee", "Mar 2026"), ("Bo Chan", "Mar 2024")))]
    kept, aside = discovered({"x": [{"results": two, "retrieved_at": "2026-09-29T00:00:00Z"}]}, [navwar], set(), asof)
    assert [p["name"] for p in kept] == ["Ann Lee"] and "Bo Chan" in [a["name"] for a in aside], "the older of two claims to a component's one post is set aside"
    both = PROFILE_FIXTURE.replace("### Chief Technology Officer - [Acme Robotics Inc](https://www.linkedin.com/company/acme)\n\nJan 2019 - Feb 2026 (7 years)\n\nAcme Robotics Inc is a Robotics company with 50-60 employees.",
                                   "### Deputy Director - Naval Information Warfare Systems Command (Current)\n\nJun 2026 - Present\n\nNaval Information Warfare Systems Command is a government agency.")
    found = discovered({"x": [{"results": [{"url": "u9", "text": both.replace("Manager - DARPA (Current)", "Manager - Naval Information Warfare Systems Command (Current)")}],
                               "retrieved_at": "2026-09-29T00:00:00Z"}]}, [navwar], set(), asof)[0]
    assert found and found[0]["linkedin"]["also_current"] == ["Deputy Director - Naval Information Warfare Systems Command (since 2026-06)"], found and found[0]["linkedin"]
    assert mentions("U.S. Navy", {"Navy"}) and not mentions("Donaldson Company", {"DON"}) and mentions("DON staff", {"DON"})
    titles = ("Head of Procurement and Contracting", "Resource Manager", "Component Acquisition Executive", "Commanding Officer, Base Cape Cod",
              "Deputy Assistant Commandant for C5I", "Patrol Boat Product Line Manager", "Requirements Development Branch Manager", "Deputy Program Manager")
    assert [listed_buckets({"raw_title": t}) for t in titles] == [["contracting"], ["budget_process"], ["decision_maker"], ["decision_maker"],
                                                                ["decision_maker"], ["budget_holder"], ["problem_owner"], ["technical"]]

    prog = {"id": "1", "title": "ALIAS", "office": "office:tto", "status": "Current", "manager": "Ann Lee", "url": "https://x/alias",
            "listing_source": {"url": "https://x/json"}, "change_statement": "The FY 2027 increase reflects flight tests.",
            "budget": {"match": "acronym", "amounts_musd": {"fy2025": 1.0, "fy2026": 8.0, "fy2027": 20.0},
                       "blocks": [{"pe": "0602702E", "project": "TT-07", "title": "Aircrew Labor In-Cockpit Automation System (ALIAS)", "book": "b.pdf"}]},
            "problem": "Pilots carry too many tasks. The program will transition to the Air Force and Army aviation.",
            "plans": {"FY 2027 Plans": "- Fly with the Navy."}, "spend": {"obligated": 0, "strong": []}, "left": None}
    assert row("stated", "CAPT Jane Roe, Program Manager, 619-555-0100, jane.roe.mil@us.navy.mil")["text"] == "CAPT Jane Roe, Program Manager"
    assert listed_buckets({"raw_title": "alternate requirements contact on forecast record F1 (published 09/01/2026)", "role_type": "program_staff"}) == ["problem_owner"]
    assert names_program("ALIAS Proposers Day", program_names(prog)) and not names_program("PALIAS thing", program_names(prog))
    users = named_users(prog)
    assert [(u["organization"], u["transition"]) for u in users] == [("Air Force", True), ("Army", True), ("Navy", False)], users
    staff = {"https://www.darpa.mil/about/people/ann-lee": {"body": body, "field_start_date__raw": "2026-03-01"}}
    people = [{"id": "p1", "key": "name:ann lee:office:tto", "name": "Dr. Ann Lee", "emails": [], "offices": ["office:tto"],
               "positions": [{"office": "office:tto", "role_type": "program_manager", "raw_title": "Program Manager", "observed_at": "2026-09-24",
                              "source": "agency_staff_listing", "source_url": "https://www.darpa.mil/about/people/ann-lee", "context": "Program Manager, TTO"}]},
              {"id": "p2", "key": "email:alias@darpa.mil", "name": "BAA Coordinator", "emails": ["alias@darpa.mil"], "offices": ["contracting:hr0011"],
               "positions": [{"office": "contracting:hr0011", "role_type": "contract_specialist", "raw_title": "primary point of contact on special notice DARPA-SN-26-1",
                              "observed_at": "2026-09-01", "source": "sam_gov_site_api", "source_url": "https://sam.gov/opp/1/view", "context": "ALIAS Proposers Day"}]},
              {"id": "p3", "key": "name:bo ray:office:tto", "name": "Bo Ray", "emails": [], "offices": ["office:tto"],
               "positions": [{"office": "office:tto", "role_type": "acquisition_leader", "raw_title": "Office Director", "observed_at": "2026-09-24",
                              "source": "agency_staff_listing", "source_url": "https://www.darpa.mil/about/people/bo-ray", "context": "Office Director, TTO"}]}]
    people.append({"id": "p4", "key": "name:cy old:agency:darpa", "name": "Dr. Cy Old", "emails": [], "offices": ["agency:darpa"],
                   "positions": [{"office": "agency:darpa", "role_type": "acquisition_leader", "raw_title": "Director", "observed_at": "2026-06-01",
                                  "source": "conference_pages_exa", "source_url": "https://conf", "context": "spoke: Summit"}]})
    people.append({"id": "p5", "key": "email:x@darpa.mil", "name": "Old Contact", "emails": ["x@darpa.mil"], "offices": ["contracting:hr0011"],
                   "positions": [{"office": "contracting:hr0011", "role_type": "contract_specialist", "raw_title": "primary point of contact on award notice",
                                  "observed_at": "2021-01-01", "source": "sam_gov_site_api", "source_url": "https://sam.gov/opp/2/view", "context": "ALIAS award"}]})
    seed = {"nodes": [{"id": "office:tto", "type": "office", "name": "Tactical Technology Office"}, {"id": "agency:darpa", "type": "agency", "name": "DARPA"}]}
    res = build_stakeholders(people, staff, {"fiscal_year": 2026, "programs": [prog], "listing_retrieved_at": "2026-09-24T00:00:00Z"}, {}, seed, {}, {}, {"b.pdf": "https://book"}, asof)
    ann = next(p for p in res["people"] if p["id"] == "p1")
    assert set(ann["buckets"]) == {"budget_holder", "problem_owner", "champion"}, ann["buckets"]
    kinds = [s["text"].split(":")[0] for s in ann["buckets"]["champion"]]
    assert kinds == ["new in the post", "ALIAS grows", "soliciting", "industry background"], kinds
    assert "flight tests" in ann["buckets"]["champion"][1]["text"] and ann["buckets"]["problem_owner"][0]["source_url"] in ("https://book", "https://www.darpa.mil/about/people/ann-lee")
    alias = res["programs"][0]
    assert alias["stakeholders"]["budget_holder"] == ["p1"] and alias["stakeholders"]["decision_maker"] == ["p3"] and alias["stakeholders"]["champion"] == ["p1"]
    assert alias["stakeholders"]["contracting_mailboxes"] == ["p2"] and alias["stakeholders"]["contracting"] == []
    assert res["summary"]["people"] == 2 and res["summary"]["mailboxes"] == 1 and res["summary"]["champion"] == 1, res["summary"]
    assert not any(p["id"] in ("p4", "p5") for p in res["people"]), "not on the roster; a contact from 2021"
    tto = next(o for o in res["offices"] if o["office"] == "office:tto")
    assert tto["decision_maker"] == ["p3"] and tto["money_musd"]["fy2027"] == 20.0

    office_seed = {"nodes": [{"id": "pmw:120", "type": "program_office", "name": "PMW 120 Battlespace Awareness Program Office", "aliases": ["PMW-120"]}]}
    found = PROFILE_FIXTURE.replace("# Ann Lee", "# Cy Dunn").replace("Program Manager - DARPA (Current)", "Principal Assistant Program Manager - PMW 120, NAVWAR (Current)")
    res = build_stakeholders([], {}, {}, {}, office_seed, {}, {"pmw:120": [{"results": [{"url": "u9", "text": found}, {"url": "u8", "text": PROFILE_FIXTURE}],
                                                                           "retrieved_at": "2026-09-29T00:00:00Z"}]}, {}, asof)
    assert [(p["name"], list(p["buckets"])) for p in res["people"]] == [("Cy Dunn", ["budget_holder"])], res["people"]
    office_seed["nodes"].append({"id": "office:pmo-555", "type": "program_office", "name": "Shipyard Infrastructure Optimization Program Office"})
    rep = found.replace("# Cy Dunn", "# Di Fox").replace("Principal Assistant Program Manager - PMW 120, NAVWAR", "Program Manager s Rep - PMO 555 Shipyard Infrastructure Opt")
    vendor = found.replace("# Cy Dunn", "# Ed Gray").replace("Principal Assistant Program Manager - PMW 120, NAVWAR (Current)", "Program Manager, PMW 120 support - Serco Inc (Current)").replace(
        "Defense Advanced Research Projects Agency is a government agency.", "Serco Inc is an IT Services and IT Consulting company.")
    res = build_stakeholders([], {}, {}, {}, office_seed, {}, {"pmw:120": [{"results": [{"url": "u7", "text": rep}, {"url": "u6", "text": vendor}],
                                                                           "retrieved_at": "2026-09-29T00:00:00Z"}]}, {}, asof)
    got = {p["name"]: (p["offices"], list(p["buckets"]), p["buckets"][list(p["buckets"])[0]][0]["text"][:18]) for p in res["people"]}
    assert got == {"Di Fox": (["office:pmo-555"], ["technical"], "Program Manager s "), "Ed Gray": (["pmw:120"], ["technical"], "support contractor")}, got
    assert listed_buckets({"raw_title": "Finance Division Director"}) == ["budget_process"]
    pm = lambda who, since: found.replace("# Cy Dunn", f"# {who}").replace("Principal Assistant Program Manager - PMW 120, NAVWAR (Current)",  # noqa: E731
                                          "Program Manager - PMW 120 (Current)").replace("Mar 2026 - Present", f"{since} - Present")
    res = build_stakeholders([], {}, {}, {}, office_seed, {}, {"pmw:120": [{"results": [{"url": "a", "text": pm("Al New", "Jan 2025")}, {"url": "b", "text": pm("Bo Old", "Jan 2021")},
                                                                                         {"url": "c", "text": pm("CAPT Cy Gone", "Jan 2012")}],
                                                                           "retrieved_at": "2026-09-29T00:00:00Z"}]}, {}, asof)
    assert [p["name"] for p in res["people"]] == ["Al New"] and [a["name"] for a in res["set_aside"]] == ["Bo Old", "CAPT Cy Gone"], (res["people"], res["set_aside"])
    assert listed_buckets({"raw_title": "Logistics APM"}) == ["budget_holder"] and listed_buckets({"raw_title": "Deputy Technical Director"}) == ["technical"]
    nojob = build_stakeholders([], {}, {}, {}, office_seed, {}, {"pmw:120": [{"results": [{"url": "d", "text": pm("Ed Blank", "Jan 2025").replace("Program Manager - PMW 120", "PMW 120 - US Navy")}],
                                                                             "retrieved_at": "2026-09-29T00:00:00Z"}]}, {}, asof)
    assert not nojob["people"] and "no job" in nojob["set_aside"][0]["reason"], nojob["set_aside"]
    acting = build_stakeholders([], {}, {}, {}, office_seed, {}, {"pmw:120": [{"results": [{"url": "e", "text": pm("Fe Long", "Jun 2022").replace("Program Manager - PMW 120", "Program Manager - PMW 120 (Acting)")}],
                                                                              "retrieved_at": "2026-09-29T00:00:00Z"}]}, {}, asof)
    assert not acting["people"] and acting["set_aside"][0]["name"] == "Fe Long", "acting since 2022 is not acting now"
    retire = lambda since: pm("Gus Ret", "Jan 2025").replace("## Experience\n", f"## Experience\n\n### Retired - US Navy (Current)\n\n{since} - Present\n", 1)  # noqa: E731
    left = build_stakeholders([], {}, {}, {}, office_seed, {}, {"pmw:120": [{"results": [{"url": "g", "text": retire("Jun 2026")}], "retrieved_at": "2026-09-29T00:00:00Z"}]}, {}, asof)
    assert not left["people"] and "retirement since 2026-06" in left["set_aside"][0]["reason"], left["set_aside"]
    before = build_stakeholders([], {}, {}, {}, office_seed, {}, {"pmw:120": [{"results": [{"url": "g", "text": retire("Jun 2015")}], "retrieved_at": "2026-09-29T00:00:00Z"}]}, {}, asof)
    assert [p["name"] for p in before["people"]] == ["Gus Ret"], "retired from uniform, then took the post"
    assert not profile_posts("# Di Eve\n\n## Experience\n\n### Retired - US Navy (Current)\n\nJan 2020 - Present\n")[1][0]["current"]

    # a book line no listing names a manager for: the office's current program manager holds it
    line = {"id": "line:OPN:2950", "title": "Battlespace Sensors", "office": "pmw:120", "status": "", "manager": "", "url": "https://x/opn.pdf",
            "listing_source": {"url": "https://x/opn.pdf"}, "change_statement": "",
            "office_basis": {"how": "named", "basis": "the line's text prints PMW 120 (2 of 2 program office mentions)", "candidates": []},
            "budget": {"match": "line", "amounts_musd": {"fy2025": 5.0, "fy2026": 6.0, "fy2027": 9.0},
                       "blocks": [{"pe": "2950", "project": "", "title": "Battlespace Sensors", "book": "opn.pdf"}]},
            "problem": "Sensors age out.", "plans": {}, "spend": {"obligated": 0, "strong": []}, "left": {"amount_musd": 4.0}}
    hits = {"pmw:120": [{"results": [{"url": "a", "text": pm("Al New", "Jan 2025")}], "retrieved_at": "2026-09-29T00:00:00Z"}]}
    peo = {"id": "q9", "key": "name:pat peo:peo:c4i", "name": "Pat Peo", "emails": [], "offices": ["peo:c4i"],
           "positions": [{"office": "peo:c4i", "role_type": "acquisition_leader", "raw_title": "Program Executive Officer", "observed_at": "2026-04-12",
                          "source": "contact_observations", "source_url": "https://peo", "context": "Program Executive Officer, PEO C4I"}]}
    line_seed = {"nodes": office_seed["nodes"] + [{"id": "peo:c4i", "type": "program_executive_office", "name": "PEO C4I"}],
                 "relationships": [{"type": "child_of", "from": "pmw:120", "to": "peo:c4i"}]}
    res = build_stakeholders([peo], {}, {"fiscal_year": 2026, "programs": [line], "offices": {"pmw:120": {"amounts_musd": {"fy2026": 6.0},
                             "spend": {"obligated": 2e6}, "left": {"amount_musd": 4.0}, "left_note": ""}}}, {}, line_seed, {}, hits, {"opn.pdf": "https://book"}, asof)
    assert res["programs"][0]["stakeholders"]["decision_maker"] == ["q9"] and res["programs"][0]["stakeholders"]["decision_maker_office"] == "peo:c4i"
    al = next(p for p in res["people"] if p["name"] == "Al New")
    assert al["programs"] == ["line:OPN:2950"] and set(al["buckets"]) == {"budget_holder", "problem_owner", "champion"}, al
    # a profile is self-stated: its holder is recently observed, and the evidence never says the person holds the office now
    seen_al = {"status": "recently_observed", "source": "linkedin_profile", "observed_at": "2026-09-29", "source_url": "a"}
    assert al["posts"] == [{"office": "pmw:120", "role": "Program Manager", **seen_al}], al["posts"]
    assert any(r["basis"] == "inferred" and r["text"].startswith("was last observed holding PMW 120 Battlespace Awareness Program Office on 2026-09-29 (not confirmed current); ")
               for r in al["buckets"]["budget_holder"]), al["buckets"]
    assert [(p["office"], p["role"], p["status"]) for p in next(p for p in res["people"] if p["id"] == "q9")["posts"]] == \
        [("peo:c4i", "acquisition_leader", "confirmed_current")]  # the office's own record, 170 days old
    # a recent SAM.gov contact record at one office confirms that post only, never a leadership post the news reported
    sam = {"office": "pmw:120", "role_type": "contract_specialist", "raw_title": "N00039-26-R-0001", "observed_at": "2026-09-01",
           "source": "sam_gov_site_api", "source_url": "https://sam", "context": "contact"}
    news = dict(peo["positions"][0], source="news_articles", observed_at="2026-09-10", source_url="https://news")
    both = build_stakeholders([dict(peo, offices=["peo:c4i", "pmw:120"], positions=[sam, news])], {}, {"fiscal_year": 2026, "programs": [], "offices": {}},
                              {}, line_seed, {}, {}, {}, asof)
    assert [(p["office"], p["role"], p["status"]) for p in both["people"][0]["posts"]] == \
        [("peo:c4i", "acquisition_leader", "recently_observed"), ("pmw:120", "contract_specialist", "confirmed_current")], both["people"][0]["posts"]
    assert [s["text"].split(":")[0] for s in al["buckets"]["champion"]] == ["Battlespace Sensors grows", "industry background"], al["buckets"]["champion"]
    got = res["programs"][0]
    assert got["manager"] == {"name": "Al New", "person": al["id"], "held_by_office": True, "standing": seen_al} and got["stakeholders"]["champion"] == [al["id"]], got
    pmw120 = next(o for o in res["offices"] if o["office"] == "pmw:120")
    assert pmw120["left_musd"] == 4.0 and pmw120["spend_found_usd"] == 2e6 and pmw120["money_musd"] == {"fy2026": 6.0}, pmw120
    nodes = {n["id"]: n for n in office_seed["nodes"]}
    two = [{"id": i, "name": n, "positions": [{"office": "pmw:120", "raw_title": "Program Manager", "observed_at": "2026-05-01", "source": "news"}]}
           for i, n in (("q1", "Jo A"), ("q2", "Jo B"))]
    assert not office_holders(two, [], nodes, asof), "two claims on one date: no holder"
    one = office_holders(two[:1], [], nodes, asof)["pmw:120"]
    assert one["person"]["id"] == "q1" and one["standing"]["status"] == "recently_observed", one  # a news story only reports the post
    speech = lambda d: [dict(two[0], positions=[dict(two[0]["positions"][0], source="navy_mil_speeches", observed_at=d)])]  # noqa: E731
    assert office_holders(speech("2026-05-01"), [], nodes, asof)["pmw:120"]["standing"]["status"] == "confirmed_current"
    assert office_holders(speech("2025-11-01"), [], nodes, asof)["pmw:120"]["standing"]["status"] == "recently_observed"  # official, past six months
    deputy = [dict(two[0], positions=[dict(two[0]["positions"][0], raw_title="Deputy Program Manager")])]
    assert not office_holders(deputy, [], nodes, asof)
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"collect": collect, "build": build}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""From what a company sells to one outreach e-mail, walked by a model through the Navy record: the agency, the command,
the program executive office and the program office that buys the capability, that office's requirements, the
initiatives above it, the people the record ties to it, and a letter written from those alone.

Every judgment is a recorded model call: the search terms (and fresh ones when the walk runs dry with steps left), each
next move, the chain, the letter. The code looks things
up (the views of pages.Layer and `initiatives`) and checks what the model returns, and decides nothing else:
- each hop of the chain is above the program office in the organization tree and of its level, no level the tree holds
  is skipped, and the program office was opened and buys (a program office, a technical office, a department or a field
  activity, never a command or a contracting office);
- every requirement and initiative was shown, its quote is in the record verbatim, a requirement sits under the program
  office and an initiative sits under it, above it or at the department; a chain rests on a requirement, or on an
  initiative under the program office when the office shows no requirement the company could bid on;
- every person was shown and the record ties them to the chain; every route was shown and leads into the chain;
- the letter goes to a chosen person or route, cites chosen records, names no other person, and every number, date and
  identifier in it is in the evidence or the profile.
A failed check gets up to two repair calls that list the problems; a run that still fails is kept and marked refused. A
profile the record holds nothing for comes back with no chain and no letter.

    python research/tools/outreach.py run --company NAME --profile TEXT_OR_FILE [--steps 32] [--check]
    python research/tools/outreach.py show SLUG
    python research/tools/outreach.py audit SLUG     # one readable file a second model can check without these tools
    python research/tools/outreach.py --selfcheck
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency_layers_sql import SEED, release_source_url, uid  # noqa: E402
from ask import ROW_RE, where_from  # noqa: E402
from agency import P  # noqa: E402
from backtest import CORPUS, RESEARCH, ROOT, alias_pattern, chain, register_problems  # noqa: E402
from vocabulary import term_groups  # noqa: E402
from llm import structured  # noqa: E402
from lrae_package import DIFF_NAME  # noqa: E402
from pages import SHOWN, Layer, row_contacts, upward  # noqa: E402
from people import contacts_for, norm_name, routes_for  # noqa: E402
from pulse import load_people, office_name  # noqa: E402
from reader import flatten  # noqa: E402
from trace import pack_rows  # noqa: E402

OUT = RESEARCH / "results" / "outreach"
AGENCY_NAME = P["label"]  # the record walked is the profile's
MODEL = "gpt-5.5"  # fit is judged from thin evidence, a few dozen calls a run: the larger model, not the readers' mini
TOOLS = ("search", "office", "cell", "topics", "people", "neighbors", "initiatives")
INITIATIVE_FAMILIES = ("leaders", "congress", "budget", "oversight", "conference", "news", "organization")
BUYING_TYPES = ("program_office", "technical_office", "department", "field_activity")  # where a requirement is owned, below a command
# The levels above a program office, each with the organization types that hold it; a level the record shows must be named.
LEVELS = {"peo": ("program_executive_office",), "command": ("contracting_activity", "acquisition_portfolio", "field_activity"), "agency": ("agency",)}
DEFAULT_STEPS = 32
MAX_NEXT = 3
MAX_RESEEDS = 2
DESCRIBED = 400  # characters of a forecast row's description shown with it
ROW_FIELDS = {"procurement_method": "procurement method", "contract_type": "contract type", "anticipated_total_value": "value",
              "solicitation_fy": "solicitation FY", "solicitation_quarter": "solicitation quarter", "award_fy": "award FY",
              "award_quarter": "award quarter", "follow_on_or_new": "new or follow-on", "existing_contract_number": "existing contract",
              "incumbent_contractor": "incumbent", "contracting_poc_name": "contracting POC", "contracting_poc_contact": "contracting POC contact",
              "secondary_poc_name": "secondary POC", "secondary_poc_contact": "secondary POC contact", "naics": "NAICS", "psc": "PSC",
              "procurement_instrument": "instrument", "contracting_office_uic": "contracting office", "period_of_performance_months": "months of performance",
              "place_of_performance": "place of performance", "facility_clearance": "facility clearance", "personnel_clearance": "personnel clearance"}
REPAIRS = 2  # each repair is checked again; a fix for one refusal can trip another
SHOWN = 12
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/.-]*\d[A-Za-z0-9/.-]*")
INTERNAL_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")  # a record's key in the corpus, never public
SEARCHED = ("search and topics match the whole term as written, case aside, so a term is a short name the record itself would "
            "write, one to three words (Link 16, MIDS, UUV, unmanned undersea, availability planning), never a long description")
PLAIN = ("Write in a formal register, past and present tense, no em dashes, and never with words such as likely, probably, "
         "imminent, expected or soon.")


def obj(**fields) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(fields), "properties": fields}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
CITED = arr(obj(identifier=S, quote=S, why=S))

SYSTEM_SEED = (f"You find where the {AGENCY_NAME} buys what one company sells, in a record of dated, sourced statements. From the "
               "profile, name up to eight search terms a forecast row, SBIR topic, notice, award or congressional statement "
               "would use for it: capability words, not marketing words; two may be the broader mission areas it serves; "
               + SEARCHED + "; each with its promise (0 to 1), how likely the term leads to the office that buys it. Then "
               "state in one sentence what would count as a requirement this company could bid on or team for. Terms are words "
               "to look up, never programs or offices you assume exist.")
SCHEMA_SEED = obj(terms=arr(obj(term=S, promise=N)), relevant_when=S)
SYSTEM_RESEED = (f"A walk of a record of {AGENCY_NAME} procurement statements for one company ran out of moves with steps left. From "
                 "the profile and what each move returned, name up to eight new search terms the walk has not tried: broader words "
                 "for the capability, the science or the mission it serves, and the kind of office that would buy it, each with its "
                 "promise from 0 to 1. " + SEARCHED + ". Keep relevant_when as given.")
SYSTEM_STEP = (f"You walk a record of {AGENCY_NAME} procurement statements for one company to find the one program "
               "office that buys what it sells, that office's requirements, the initiatives that make the capability matter "
               "there (leaders' priorities, congressional directives, budget lines, oversight findings), and the people and "
               "routes into it. You see the profile, the path so far and the record's answer to your last move. Judge the "
               "answer's relevance to the company (0 to 1). Record findings only for a record the answer shows that bears on "
               "this company: its identifier exactly as shown (id or need_key), a few words copied exactly from its title or "
               "text, and why in one sentence. Then choose up to three next moves, each with a one-line reason and its promise (0 "
               "to 1), how likely it leads to the buying office, its requirements or its people; the walk takes the most "
               "promising move of every branch next, so a lead elsewhere is not lost: open an office "
               "where the signal is (office shows its forecast rows, topics, people and routes; initiatives shows what leaders, "
               "Congress, the budget and oversight say in its chain), follow a record (cell), climb or step sideways "
               "(neighbors), or search the record's own words. A program_office, technical_office, department or field_activity owns requirements; "
               "a command or contracting office files and signs for them. Stop the branch when the "
               "answer shows nothing for this company. "
               "Tools: search TERM; office NAME; cell NEED_KEY_OR_ID; topics TERM; people OFFICE; neighbors OFFICE; initiatives "
               "OFFICE. " + SEARCHED + ". " + PLAIN)
SCHEMA_STEP = obj(relevance=N, findings=CITED, next=arr(obj(tool={"type": "string", "enum": list(TOOLS)}, argument=S, reason=S, promise=N)),
                  stop_branch=B, note=S)
SYSTEM_CHAIN = (f"From the walk below, name the one buying chain in the {AGENCY_NAME} for this company's capability, each office "
                "written exactly as the record writes it: the agency; the command; the program executive office; and the "
                "program office that owns the requirement, which must be one the walk opened. The levels above it are the ones "
                "the office view lists in its chain; leave a level empty only when that chain holds none. Then the requirements of that program office or under it that the company could bid on or team for "
                "(when the office shows none, leave requirements empty and let an initiative under the program office carry the chain); "
                "weigh what a row shows of how it is bought: a sole-source buy, a funding modification to another firm's contract, a row "
                "a later release dropped, or a record whose window closed years before the as-of date is one the company cannot act on now, so prefer one it can and "
                "say so in why when none can; "
                "and the initiatives in its chain or at the department that make the capability matter there; each by its "
                "identifier as shown, with words copied exactly from that record and why in one sentence. Then whom to write "
                "to: people and routes the walk showed for that chain, each with why this is the one to write to; the program "
                "manager or the requirement side answers about the need, a contracting point of contact about one procurement. When the walk "
                "shows no program office that buys what the company sells, answer fit false with the reason in one sentence that "
                "names what the record did hold closest to the capability, by title, and why it names no buyer; leave the rest empty. An office buys it only when its requirements or initiatives concern that capability "
                "itself; a word they share is not a fit, and no fit is a right answer. Never name an office, record, person or route the walk did not show. " + PLAIN)
SCHEMA_CHAIN = obj(fit=B, agency=S, command=S, peo=S, program_office=S, why_office=S, requirements=CITED, initiatives=CITED,
                   people=arr(obj(name=S, why=S)), routes=arr(obj(route=S, why=S)), no_fit=S)
SYSTEM_EMAIL = ("Write one outreach e-mail from the company to a chosen person, the program side before a contracting point of "
                "contact; write to a route's office only when no person was chosen. Tailor it to this office: name the "
                "program office and the requirement (the initiative, when no requirement was chosen) by the record's own title and identifier, describe what the company sells in "
                "the record's own terms (never saying that it matches them), cite the initiative that makes it matter when one was chosen, and ask for one concrete next step (a short "
                "call, a capability briefing, or where to send a white paper). Use only facts from the evidence and the "
                "company's profile: no number, date, identifier, program, contract, office or person the evidence does not "
                "carry, and no claim about the company beyond its profile. The bracketed keys in the evidence are internal and "
                "go only in cites; the letter names a record by its title and by the solicitation, notice, contract or forecast "
                "number its text carries. Address the person by full name, with no courtesy title (Mr., Ms., Dr.) the record does not give. Under 220 words. " + PLAIN + " Answer with: to (the "
                "chosen person's name exactly, or the office a chosen route names, its name without the link), subject, body, and cites (the "
                "keys the evidence shows in square brackets, without the brackets, of the records the body refers to).")
SCHEMA_EMAIL = obj(to=S, subject=S, body=S, cites=arr(S))


# ------------------------------------------------------------------ the layer as the model sees it

def row_details(as_of: str) -> dict[str, dict[str, str]]:
    """Each forecast row as the latest release by the as-of date states it: its description, how it is bought, its value
    and dates, the incumbent and the contacts it names, with the release it comes from. The corpus keeps a row's title
    only, and a title such as "SF4 Cryptographic Device Solutions" says neither what the office wants nor whether a
    newcomer can bid. A field the latest release leaves empty keeps the last value a release gave it. A row a later
    release diff marks removed, and no release after carries again, says so: an office that stopped forecasting a buy
    has awarded, moved or dropped it."""
    rows, released, keys = [], {}, {}
    packs = sorted((ROOT / "datapack").glob("lrae_*"))
    for pack in packs:
        released[pack.name] = json.loads((pack / "SOURCE.json").read_text(encoding="utf-8")).get("release_date", "")
        keys[pack.name] = {c["row_number"]: c["record_key"] for c in pack_rows(pack, "rows_classified.csv")}
        rows += [(released[pack.name], keys[pack.name][r["row_number"]], r) for r in pack_rows(pack, "rows_raw.csv")
                 if released[pack.name] and released[pack.name] <= as_of and keys[pack.name].get(r["row_number"])]
    out: dict[str, dict[str, str]] = {}
    for when, key, r in sorted(rows, key=lambda x: (x[0], x[1])):
        row = out.setdefault(key, {})
        row.update({"release": when, **{label: " ".join(r[f].split()) for f, label in ROW_FIELDS.items() if (r.get(f) or "").strip()}})
        if r["requirement_description"].strip():
            row["description"] = " ".join(r["requirement_description"].split())
    for pack in packs:
        for path in sorted(pack.glob("diff_*.csv")):
            m = DIFF_NAME.match(path.name)
            older, newer = m.groups() if m else ("", "")
            if not m or not released.get(newer) or released[newer] > as_of:
                continue
            for ch in pack_rows(pack, path.name):
                row = out.get(keys.get(older, {}).get(ch["old_row"], ""))
                if ch["change"] == "removed" and row and row["release"] < released[newer] and row.get("dropped", "") < released[newer]:
                    row["dropped"] = released[newer]
    for row in out.values():
        if "dropped" in row:
            row["dropped"] = f"not in the {row['dropped']} release"
    return out


class Walk:
    """The layer behind seven views, with a record of everything shown: a finding, a chain or a letter may use only that."""

    def __init__(self, layer: Layer, details: dict[str, dict[str, str]] | None = None):
        self.layer = layer
        self.details = details or {}       # forecast key -> the row as its latest release states it (row_details)
        self.by_name = {norm_name(p["name"]): p for p in layer.roster}
        self.shown: dict[str, dict] = {}   # record id or forecast key -> the brief as shown
        self.opened: set[str] = set()      # offices the model opened
        self.people: dict[str, dict] = {}  # name -> the contact as shown
        self.routes: dict[str, dict] = {}  # recommendation -> the route as shown
        self.events = {e["id"]: e for e in layer.events}
        self.needs = {n["key"]: n for n in layer.needs}

    def call(self, tool: str, argument: str) -> dict:
        answer = self.initiatives(argument) if tool == "initiatives" else getattr(self.layer, tool)(argument)
        if tool == "search" and argument.strip():  # rows whose description carries the term as statements do, plurals too
            title_rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(argument) + r"(?![A-Za-z0-9])", re.I)  # the rows search already gave
            rx = alias_pattern([argument])
            groups = [alias_pattern(g) for g in term_groups(argument)] if answer.get("matched") == "by its words" else []
            carries = lambda text: bool(rx.search(text)) or bool(groups and all(g.search(text) for g in groups))  # noqa: E731
            more = [self.layer.need_brief(self.needs[k]) for k, d in sorted(self.details.items())
                    if k in self.needs and carries(d.get("description", "")) and not title_rx.search(self.needs[k]["title"])]
            answer["forecast_rows"] += more[:max(0, SHOWN - len(answer["forecast_rows"]))]
            answer["forecast_rows_total"] += len(more)
        if tool in ("search", "topics") and not (answer.get("statements") or answer.get("topics") or answer.get("forecast_rows_total")):
            answer["note"] = "no record carries this term as written or as its words; try a shorter name the record writes"
        elif tool in ("search", "topics") and answer.get("matched") == "by its words":
            answer["note"] = "no record carries this term as written; the statements carry each of its words (or its capability's words)"
        if isinstance(answer.get("offices"), dict):  # the tally by name, with each office's type: a buyer is told from a command
            answer["offices"] = [{"office": n, "type": self.layer.orgs.get(self.layer.org_id(n) or "", {}).get("org_type", ""), "statements": c}
                                 for n, c in answer["offices"].items()]
        if tool in ("office", "initiatives") and "error" not in answer:
            self.opened.add(self.layer.org_id(argument))
        if tool == "people" and "error" not in answer:
            answer["people"] = self.row_people(argument, answer["people"])
        self.note(answer)
        return answer

    def note(self, found) -> None:
        if isinstance(found, list):
            for x in found:
                self.note(x)
        elif isinstance(found, dict):
            if "id" in found and "family" in found:
                self.shown[found["id"]] = found
            elif "need_key" in found and "title" in found:
                row = self.details.get(found["need_key"])
                if row:
                    found["row"] = {k: v[:DESCRIBED] if k == "description" else v for k, v in row.items()}
                    self.named(found["need_key"], row)
                self.shown[found["need_key"]] = found
            elif {"name", "role", "observed_at"} <= set(found):
                self.people[found["name"]] = found
            elif {"recommendation", "route"} <= set(found):
                self.routes[found["recommendation"]] = found
            for v in found.values():
                self.note(v)

    def row_contacts(self, key: str, row: dict[str, str]) -> list[dict]:
        """The contacts a row names, as the record holds them in the chain of the office that owns the row: they answer
        for this row rather than whoever the office's newest notice named."""
        return row_contacts(self.layer, self.needs[key]["owner_id"], key, row, self.by_name)

    def named(self, key: str, row: dict[str, str]) -> None:
        """The contacts a shown row names: the chain may write to them."""
        for c in self.row_contacts(key, row):
            self.people[c["name"]] = c

    def row_people(self, name: str, shown: list[dict]) -> list[dict]:
        """The contacts an office's own forecast rows name, newest release first, before the others the record ties to
        its chain: a laboratory's division names its buyer on its rows, where the laboratory above it has dozens."""
        mine = self.layer.subtree(self.layer.org_id(name) or "")
        rows = sorted((k for k, n in self.needs.items() if n["owner_id"] in mine and k in self.details),
                      key=lambda k: (self.details[k].get("release", ""), k), reverse=True)
        named: dict[str, dict] = {}
        for k in rows:  # the newest row names a person first; the contracting POC comes before the secondary
            for c in self.row_contacts(k, self.details[k]):
                named.setdefault(c["name"], c)
        return [*named.values(), *(p for p in shown if p["name"] not in named)][:SHOWN]

    def initiatives(self, name: str) -> dict:
        """What leaders, Congress, the budget, oversight, conferences, news and reorganizations say in an office's chain
        and under it, and the ones filed elsewhere that the model reads to it; newest first. Department-wide statements
        name no office and are counted, not listed: search finds them by topic."""
        lay = self.layer
        oid = lay.org_id(name)
        if not oid:
            return {"office": name, "error": "no organization by that name; use neighbors or search to find one"}
        below = lay.subtree(oid)
        near = below | set(chain(oid, lay.orgs))
        said = [e for e in lay.events if e["family"] in INITIATIVE_FAMILIES and e["available_by"] <= lay.as_of]
        mine = sorted((e for e in said if e["org"] in near or (e.get("model_read") or ("",))[0] in below),
                      key=lambda e: (e["available_by"], e["id"]), reverse=True)
        wide = sum(1 for e in said if e["org"] not in near and lay.orgs.get(e["org"], {}).get("org_type", "agency") == "agency")
        return {"office": office_name(oid, lay.orgs), "statements": len(mine), "families": dict(Counter(e["family"] for e in mine).most_common()),
                "newest": [{**lay.brief(e), "text": e["text"][:300]} for e in mine[:SHOWN]], "department_wide": wide}

    def text(self, identifier: str) -> str:
        if identifier in self.events:
            e = self.events[identifier]
            return f"{e['title']} {e['text']}"
        if identifier in self.needs:
            row = self.details.get(identifier, {})
            facts = "; ".join(f"{k}: {v}" for k, v in row.items() if k != "description")
            return " ".join(x for x in (self.needs[identifier]["title"], row.get("description", ""), facts) if x)
        return ""

    def home(self, identifier: str) -> set[str]:
        """The offices a record belongs to: where it was filed or read to, or the office that owns a forecast row."""
        if identifier in self.needs:
            return {self.needs[identifier]["owner_id"]}
        e = self.events.get(identifier, {})
        return {e.get("org", ""), e.get("filed", ""), (e.get("model_read") or ("",))[0]} - {""}

    def ancestors(self, oid: str) -> list[str]:
        return upward(oid, self.layer.orgs)


# ------------------------------------------------------------------ the checks

def verbatim(quote: str, text: str) -> bool:
    return bool(quote.strip()) and flatten(quote).lower() in flatten(text).lower()


def cited_problems(walk: Walk, rows: list[dict], kind: str, fits) -> list[str]:
    out = []
    for r in rows:
        ident = r["identifier"]
        if ident not in walk.shown:
            out.append(f"{kind} {ident} was not shown")
        elif not verbatim(r["quote"], walk.text(ident)):
            out.append(f"{kind} {ident}: the quote is not in the record verbatim")
        elif not fits(walk.home(ident)):
            out.append(f"{kind} {ident} sits outside the chain")
    return out


def chain_problems(walk: Walk, a: dict) -> list[str]:
    """What the record does not support in a chain; no fit is an answer, not a failure."""
    if not a["fit"]:
        return [] if a["no_fit"].strip() else ["no fit given without a reason"]
    lay = walk.layer
    po = lay.org_id(a["program_office"])
    if not po:
        return [f"program office {a['program_office']!r} is not an organization in the record"]
    out = [] if lay.orgs[po].get("org_type") in BUYING_TYPES else [f"{a['program_office']} is a {lay.orgs[po].get('org_type')}, not a buying office"]
    if po not in walk.opened:
        out.append(f"{a['program_office']} was not opened")
    up = walk.ancestors(po)
    for label, types in LEVELS.items():
        oid = lay.org_id(a[label]) if a[label].strip() else None
        held = next((x for x in up[1:] if lay.orgs[x].get("org_type") in types), None)
        holds = f"the record puts it under {office_name(held, lay.orgs)}" if held else f"the record holds no {label} over it; leave it empty"
        if a[label].strip() and oid not in up[1:]:
            out.append(f"{label} {a[label]!r} is not above {a['program_office']} in the record; {holds}")
        elif oid and lay.orgs[oid].get("org_type") not in types:
            out.append(f"{label} {a[label]!r} is a {lay.orgs[oid].get('org_type')}, not a {label}; {holds}")
        elif held and not oid:
            out.append(f"{a['program_office']} sits under {office_name(held, lay.orgs)}; name it as the {label}")
    tree, above = lay.subtree(po), set(up)
    # As with people below, a refusal names the rows the walk showed under the office, so the repair picks one of them.
    rows = [f"{k}: {walk.needs[k]['title'][:80]}" for k in sorted(walk.shown) if k in walk.needs and walk.home(k) & tree][:20]
    here = f" (shown under it: {'; '.join(rows) or 'none'})"
    if not a["requirements"] and not any(walk.home(r["identifier"]) & tree for r in a["initiatives"] if r["identifier"] in walk.shown):
        out.append(f"no requirement named, and no initiative under the program office; name one the walk showed under it{here}, "
                   "choose a program office the walk showed one under, or answer fit false")
    out += [p + here for p in cited_problems(walk, a["requirements"], "requirement", lambda home: bool(home & tree))]
    out += cited_problems(walk, a["initiatives"], "initiative",
                          lambda home: not home or bool(home & (tree | above)) or all(lay.orgs.get(h, {}).get("org_type") == "agency" for h in home))
    for r in a["initiatives"]:
        family = walk.events[r["identifier"]]["family"] if r["identifier"] in walk.events else "forecast row" if r["identifier"] in walk.needs else ""
        if family and family not in (*INITIATIVE_FAMILIES, "programs"):
            out.append(f"initiative {r['identifier']} is a {family} record, not what leaders, Congress, the budget, oversight or a topic says")
    both = {r["identifier"] for r in a["requirements"]} & {r["identifier"] for r in a["initiatives"]}
    out += [f"{ident} is named both as a requirement and as an initiative" for ident in sorted(both)]
    named = Counter(r["identifier"] for k in ("requirements", "initiatives") for r in a[k])
    out += [f"{ident} is named {n} times; name each record once" for ident, n in sorted(named.items()) if n > 1 and ident not in both]
    ours = {p["name"] for p in contacts_for(up, lay.roster, lay.as_of, limit=100)}
    # A refusal names what the walk did show for the chain, so the repair copies it rather than guessing again.
    mine = sorted(n for n in walk.people if n in ours)
    out += [f"person {p['name']!r} was not shown for this chain (shown for it: {', '.join(mine) or 'no one'})"
            for p in a["people"] if p["name"] not in walk.people or p["name"] not in ours]
    ways = [r["recommendation"] for r in routes_for(up, lay.routes, lay.routes_by) if r["recommendation"] in walk.routes]
    out += [f"route {r['route'][:60]!r} was not shown for this chain (shown for it: {' | '.join(w[:120] for w in ways) or 'none'})" for r in a["routes"]
            if len(r["route"].strip()) < 12 or not any(verbatim(r["route"], w) for w in ways)]
    if not a["people"] and not a["routes"]:
        out.append("no one to write to")
    return out + register_problems(" ".join([a["why_office"], *(r["why"] for k in ("requirements", "initiatives", "people", "routes") for r in a[k])]))


def evidence(walk: Walk, a: dict) -> str:
    """What the letter may say: the chain, each chosen record in full, the chosen people and routes as the record holds them."""
    lay = walk.layer
    lines = ["Chain: " + " > ".join(f"{office_name(o, lay.orgs)} ({lay.orgs[o]['name']})" for o in reversed(walk.ancestors(lay.org_id(a["program_office"]))))]
    for kind in ("requirements", "initiatives"):
        lines += [f"[{r['identifier']}] {kind[:-1].title()}: {walk.text(r['identifier'])[:700]}" for r in a[kind]]
    for p in a["people"]:
        c = walk.people[p["name"]]
        lines.append(f"Person: {c['name']}, {c['title']}, {c['office']}, observed {c['observed_at']}, e-mail {c['email'] or 'none on record'}")
    lines += [f"Route: {w} (observed {way['observed_at']})" for r in a["routes"] for w, way in walk.routes.items() if verbatim(r["route"], w)]
    return "\n".join(lines)


def email_problems(walk: Walk, a: dict, e: dict, proof: str, profile: str) -> list[str]:
    out = []
    names = {p["name"] for p in a["people"]}
    if e["to"] not in names and not any(verbatim(e["to"], r["route"]) for r in a["routes"]):
        out.append(f"the letter goes to {e['to']!r}, not a chosen person or route")
    chosen = {r["identifier"] for k in ("requirements", "initiatives") for r in a[k]}
    cites = [c.strip("[] ") for c in e["cites"]]
    out += [f"cites {c}, which is not the bracketed key of a chosen record" for c in cites if c not in chosen]
    if not ({r["identifier"] for r in a["requirements"]} or chosen) & set(cites):
        out.append("cites no requirement" if a["requirements"] else "cites no initiative")
    letter = f"{e['subject']}\n{e['body']}"
    known = INTERNAL_RE.sub(" ", flatten(f"{proof}\n{profile}").lower())
    out += [f"names the internal key {k}" for k in sorted(set(INTERNAL_RE.findall(letter)))]
    for tok in sorted({t.rstrip(".-/") for t in TOKEN_RE.findall(letter)}):
        if not re.search(rf"(?<![a-z0-9]){re.escape(tok.lower())}(?![a-z0-9])", known):
            out.append(f"{tok!r} is in no evidence")
    body = flatten(letter).lower()
    out += [f"names {p['name']}, who was not chosen" for p in walk.layer.roster
            if p["name"] not in names and len(p["name"]) > 6 and flatten(p["name"]).lower() in body]
    out += [f"calls someone {t!r}, a courtesy title the record does not give" for t in sorted(set(re.findall(r"\b(?:Mr|Ms|Mrs|Miss|Dr)\.", letter)))
            if not re.search(rf"(?<![a-z]){t[:-1].lower()}(?![a-z])", known)]
    if "—" in letter:
        out.append("em dash")
    return out + register_problems(letter)


# ------------------------------------------------------------------ the walk

def compact(found, limit: int = 6000) -> str:
    text = json.dumps(found, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + " ...[cut]"


def next_move(frontier: list[dict]) -> dict:
    """The frontier holds every move not yet walked, from every branch; the most promising goes next, the newest first
    among equals, so one strong branch cannot spend the budget while a better lead waits."""
    return frontier.pop(max(range(len(frontier)), key=lambda i: (frontier[i]["promise"], i)))


def outreach(layer: Layer, company: str, profile: str, steps: int = DEFAULT_STEPS, model: str = MODEL, replay_only: bool = False) -> dict:
    walk, calls = Walk(layer, row_details(layer.as_of)), []

    def ask(system, user, schema, name):
        out, meta = structured(system, user, schema, name, model=model, replay_only=replay_only)
        calls.append({"name": name, "cassette": meta["cassette"], "replayed": meta["replayed"], "usage": meta["usage"]})
        return out

    def checked(system, user, schema, name, problems):
        """One call, and up to REPAIRS repair calls while the checks refuse it."""
        answer = ask(system, user, schema, name)
        found = problems(answer)
        for _ in range(REPAIRS):
            if not found:
                break
            calls[-1]["refused"] = found
            answer = ask(system, f"{user}\n\nYour last answer: {json.dumps(answer, ensure_ascii=False)}\nThe checks refused it: "
                         + "; ".join(found) + ". Answer again, fixing these and changing nothing the checks accepted.", schema, name + "_repair")
            found = problems(answer)
        return answer, found

    who = f"Company: {company}\nProfile: {profile}"
    seed = ask(SYSTEM_SEED, who, SCHEMA_SEED, "outreach_seed")
    frontier = [{"tool": "search", "argument": t["term"].strip(), "reason": "seed term", "promise": t["promise"], "depth": 0, "path": [], "from": 0}
                for t in seed["terms"][:8] if t["term"].strip()]
    visited, log, findings, dropped, reseeds, spent = set(), [], [], 0, 0, 0
    while spent < steps and len(log) < 2 * steps:  # a search the record holds nothing for costs no step
        if not frontier:  # the walk ran dry with steps left: the model names fresh terms from what it has seen
            if reseeds == MAX_RESEEDS:
                break
            reseeds += 1
            tried = [{"move": f"{s['tool']} {s['argument']}", "relevance": s["relevance"], "record": s["answer_summary"], "note": s["note"]} for s in log]
            fresh = ask(SYSTEM_RESEED, f"{who}\nRelevant when: {seed['relevant_when']}\nSteps left: {steps - spent}.\nTried:\n{compact(tried, 9000)}",
                        SCHEMA_SEED, "outreach_reseed")
            frontier = [{"tool": "search", "argument": t["term"].strip(), "reason": "fresh term", "promise": t["promise"], "depth": 0, "path": [], "from": 0}
                        for t in fresh["terms"][:8] if t["term"].strip() and ("search", t["term"].strip().lower()) not in visited]
            continue
        move = next_move(frontier)
        if (move["tool"], move["argument"].lower()) in visited:
            continue
        visited.add((move["tool"], move["argument"].lower()))
        answer = walk.call(move["tool"], move["argument"])
        counted = not (move["tool"] in ("search", "topics") and "note" in answer)
        spent += counted
        path = move["path"] + [f"{move['tool']} {move['argument']}"]
        judged = ask(SYSTEM_STEP, f"{who}\nRelevant when: {seed['relevant_when']}\nPath: {' > '.join(path)}\nDepth: {move['depth']}. "
                     f"Steps left: {steps - spent}.\nAnswer from the record:\n{compact(answer)}", SCHEMA_STEP, "outreach_step")
        kept, lost = [], []
        for f in judged["findings"]:
            miss = ("the record never showed this identifier" if f["identifier"] not in walk.shown
                    else "" if verbatim(f["quote"], walk.text(f["identifier"])) else "the quote is not in the record verbatim")
            if miss:
                lost.append({**f, "refused": miss})
            else:
                kept.append({**f, "path": path})
        dropped += len(lost)
        findings += kept
        log.append({"n": len(log) + 1, "tool": move["tool"], "argument": move["argument"], "reason": move["reason"], "promise": move["promise"],
                    "from": move["from"], "depth": move["depth"], "counted": counted,
                    "relevance": judged["relevance"], "findings": len(kept), "stop_branch": judged["stop_branch"], "note": judged["note"],
                    "answer_summary": {k: v for k, v in answer.items() if k in ("statements", "families", "offices", "forecast_rows_total", "topics", "error")},
                    "shown": compact(answer), "dropped": lost})
        if not judged["stop_branch"]:
            frontier += [{**nxt, "argument": nxt["argument"].strip(), "depth": move["depth"] + 1, "path": path, "from": len(log)}
                         for nxt in reversed(judged["next"][:MAX_NEXT]) if nxt["argument"].strip()]  # reversed: among equals the first named goes first
    lay = layer.orgs
    opened = [{"office": office_name(o, lay), "name": lay[o]["name"], "type": lay[o].get("org_type", ""),
               "above": [office_name(x, lay) for x in walk.ancestors(o)[1:]]} for o in sorted(walk.opened, key=lambda o: office_name(o, lay))]
    dossier = (f"{who}\nRelevant when: {seed['relevant_when']}\nOffices opened: {compact(opened, 4000)}\n"
               f"Findings: {compact([{k: f[k] for k in ('identifier', 'quote', 'why')} | {'brief': walk.shown[f['identifier']]} for f in findings], 14000)}\n"
               f"People shown: {compact(list(walk.people.values()), 5000)}\n"
               f"Routes shown: {compact([{k: r[k] for k in ('side', 'recommendation', 'office_id', 'observed_at')} for r in walk.routes.values()], 5000)}\n"
               f"Walk notes: {compact([s['note'] for s in log], 4000)}")
    picked, picked_problems = checked(SYSTEM_CHAIN, dossier, SCHEMA_CHAIN, "outreach_chain", lambda a: chain_problems(walk, a))
    letter, letter_problems, proof = None, [], ""
    if picked["fit"] and not picked_problems:
        proof = evidence(walk, picked)
        why = compact({k: picked[k] for k in ("why_office", "requirements", "initiatives", "people", "routes")}, 6000)
        letter, letter_problems = checked(SYSTEM_EMAIL, f"{who}\nEvidence:\n{proof}\nThe chain and why: {why}", SCHEMA_EMAIL, "outreach_email",
                                          lambda e: email_problems(walk, picked, e, proof, f"{company} {profile}"))
    status = "refused" if picked_problems or letter_problems else "written" if letter else "no fit"
    return {"company": company, "profile": profile, "as_of": layer.as_of, "model": model, "budget": steps, "seed": seed, "steps": log,
            "findings": findings, "findings_dropped": dropped, "dossier": dossier, "chain": picked, "chain_problems": picked_problems, "evidence": proof,
            "email": letter, "email_problems": letter_problems, "status": status,
            "contacts": [walk.people[p["name"]] for p in picked["people"] if p["name"] in walk.people], "calls": calls,
            "usage": {k: sum((c["usage"] or {}).get(k) or 0 for c in calls) for k in ("input_tokens", "output_tokens")}}


def settled(result: dict) -> dict:
    """A run without how each call was had: a replay of a live run holds when everything else is the same."""
    return {**result, "calls": [{k: v for k, v in c.items() if k != "replayed"} for c in result["calls"]]}


def readout(result: dict) -> str:
    a = result["chain"]
    lines = [f"{result['company']}: outreach, record as of {result['as_of']}; {result['status']}",
             f"Searched for: {', '.join(t['term'] for t in result['seed']['terms'])}", ""]
    for s in result["steps"]:
        came = f"from {s['from']}, " if s["from"] else ""
        empty = "; nothing in the record, no step spent" if not s.get("counted", True) else ""
        lines.append(f"{s['n']:>2}. {s['tool']} {s['argument']}  ({came}promise {s['promise']}, relevance {s['relevance']}, findings {s['findings']}{empty})")
    if not a["fit"]:
        lines += ["", f"No chain: {a['no_fit']}"]
    else:
        lines += ["", "Chain: " + " > ".join(x for x in (a["agency"], a["command"], a["peo"], a["program_office"]) if x.strip()), f"  {a['why_office']}"]
        for kind in ("requirements", "initiatives"):
            lines += [f"{kind.title()}:"] + [f"- {r['identifier']}: \"{r['quote']}\"; {r['why']}" for r in a[kind]]
        lines += ["Write to:"] + [f"- {c['name']}, {c['title']}, {c['office']} ({c['email'] or 'no e-mail on record'}; observed {c['observed_at']}, {c['source']})"
                                  for c in result["contacts"]]
        lines += [f"- route: {r['route']}" for r in a["routes"]]
    for label, found in (("Chain refused", result["chain_problems"]), ("Letter refused", result["email_problems"])):
        lines += [f"{label}: " + "; ".join(found)] if found else []
    if result["email"]:
        e = result["email"]
        lines += ["", f"To: {e['to']}", f"Subject: {e['subject']}", "", e["body"], "", f"Cites: {', '.join(e['cites'])}"]
    spent = sum(s.get("counted", True) for s in result["steps"])
    lines += ["", f"{spent} step(s) and {len(result['steps']) - spent} empty search(es), {len(result['calls'])} model call(s), {sum(c['replayed'] for c in result['calls'])} replayed; "
              f"{result['findings_dropped']} finding(s) dropped as unshown or misquoted."]
    return "\n".join(lines)


VERIFY = """Every claim below carries the record text it rests on and where that text is published. A verifier checks:
1. each quote kept in the walk, and each requirement and initiative quote, is in its record's text verbatim (Appendix A);
2. each office in the chain sits directly under the next one in the organization record (Appendix B), and each
   requirement's owner is the program office or sits under it (Appendix A);
3. each person and route is tied in the record to an office in the chain (Appendix C);
4. every number, date and identifier in the letter is in the evidence or the profile, and the letter claims nothing
   about the company beyond the profile and forecasts nothing;
5. no step shows an office, record, person or route the chain uses before the walk had been shown it."""


def org_path(oid: str, orgs: dict) -> str:
    return " > ".join(office_name(o, orgs) for o in reversed(upward(oid, orgs))) or "no office on record"


def published(e: dict) -> str:
    row = ROW_RE.search(e["text"])
    return where_from(e) + (f"; release published at {release_source_url(row.group(1).split()[0]) or 'no address recorded'}" if row else "")


def record_entry(i: str, layer: Layer, details: dict[str, dict[str, str]]) -> list[str]:
    """One record in full with where it is published: a forecast requirement with every release row that states it and
    the row as its latest release gives it, or a statement with its text and its office."""
    orgs, need = layer.orgs, next((n for n in layer.needs if n["key"] == i), None)
    if need:
        key = re.compile(rf"(?<![\w-]){re.escape(i)}(?![\w-])")
        lines = [f"### {i}", "", f"Forecast requirement \"{need['title']}\", owned by {org_path(need['owner_id'], orgs)}. The rows that state it:", ""]
        lines += [f"- {e['title']}; dated {e['date']}, public from {e['available_by']}; {published(e)}"
                  for e in layer.events if e["family"] == "forecast" and key.search(e["text"])]
        if i in details:
            row = details[i]
            lines += ["", f"The row as the latest release carrying it (released {row['release']}) states it:", ""]
            lines += [f"- {k}: {v}" for k, v in row.items() if k not in ("release", "description")]
            lines += ["", f"> {row['description']}"] if row.get("description") else []
        return lines
    e = next((e for e in layer.events if e["id"] == i), None)
    if e is None:
        return []
    read = f"; read by the model to {org_path(e['model_read'][0], orgs)}" if e.get("model_read") else ""
    return [f"### {i}", "", f"A {e['family']} record ({e['event_type']}), dated {e['date']}, public from {e['available_by']}, "
                            f"filed at {org_path(e['org'], orgs)}{read}. Published at: {published(e)}", "", f"> {e['title']}", "",
            "> " + " ".join(e["text"].split())]


def audit(result: dict, layer: Layer, seed: dict) -> str:
    """One run as one file a second model can check without these tools: what the model was told and shown at every
    step and what it answered, what the checks refused, and each cited record, office and person with where it is
    published."""
    a, orgs = result["chain"], layer.orgs
    details = row_details(result["as_of"])
    nodes = {uid("org", n["id"]): n for n in seed.get("nodes", [])}
    observed = {o["id"]: o for o in seed.get("observations", [])}
    edges = {(r["from"], r["to"]): r for r in seed.get("relationships", []) if r["type"] == "child_of"}

    use = result["usage"]
    lines = [f"# Outreach audit: {result['company']}", "",
             f"Record as of {result['as_of']}; model {result['model']}; status {result['status']}; "
             f"{sum(s.get('counted', True) for s in result['steps'])} steps and {sum(not s.get('counted', True) for s in result['steps'])} empty searches; "
             f"{len(result['calls'])} model calls, {use['input_tokens']} input and {use['output_tokens']} output tokens.", "",
             "## How to verify", "", VERIFY, "", "## Profile", "", result["profile"], "", "## What the model was told", ""]
    for title, text in (("Search terms", SYSTEM_SEED), ("Each step", SYSTEM_STEP), ("The chain", SYSTEM_CHAIN), ("The letter", SYSTEM_EMAIL)):
        lines += [f"### {title}", "", text, ""]
    seeded = result["seed"]
    lines += ["## Walk", "", "Search terms: " + ", ".join(f"{t['term']} (promise {t['promise']})" for t in seeded["terms"]),
              f"Relevant when: {seeded['relevant_when']}", ""]
    for s in result["steps"]:
        move = f"{s['tool']} {s['argument']}"
        lines += [f"### Step {s['n']}: {move}", "",
                  f"Reached from {'step ' + str(s['from']) if s['from'] else 'the search terms'}; promise {s['promise']}. Reason: {s['reason']}", "",
                  "The record's answer, as the model saw it:", "", "```json", s.get("shown", "(not saved in this run)"), "```", "",
                  f"The model's reading: relevance {s['relevance']}{'; branch stopped' if s['stop_branch'] else ''}. {s['note']}"]
        lines += [f"- kept {f['identifier']}: \"{f['quote']}\" ({f['why']})" for f in result["findings"] if f["path"][-1] == move]
        lines += [f"- refused {f['identifier']}: \"{f['quote']}\"; {f['refused']}" for f in s.get("dropped", [])]
        lines.append("")
    lines += ["## Chain", "", "The chain call was given:", "", "```", result.get("dossier", "(not saved in this run)"), "```", ""]
    lines += [f"The checks refused the first {c['name'].removeprefix('outreach_')} answer: " + "; ".join(c["refused"]) + ". One repair call followed.\n"
              for c in result["calls"] if c.get("refused")]
    if not a["fit"]:
        lines += [f"No chain: {a['no_fit']}", ""]
    else:
        lines += ["Chain: " + " > ".join(x for x in (a["agency"], a["command"], a["peo"], a["program_office"]) if x.strip()), f"Why this office: {a['why_office']}", ""]
        for kind in ("requirements", "initiatives"):
            lines += [f"{kind.title()}:"] + [f"- {r['identifier']}: \"{r['quote']}\"; {r['why']}" for r in a[kind]] + [""]
        lines += ["People:"] + [f"- {x['name']}: {x['why']}" for x in a["people"]] + ["Routes:"] + [f"- {x['route']}: {x['why']}" for x in a["routes"]] + [""]
    lines += [f"Chain checks: {'; '.join(result['chain_problems']) or 'all passed'}", ""]
    if result["email"]:
        e = result["email"]
        lines += ["## Evidence the letter was written from", "", "```", result["evidence"], "```", "", "## Letter", "",
                  f"To: {e['to']}", f"Subject: {e['subject']}", "", e["body"], "", f"Cites: {', '.join(e['cites'])}", "",
                  f"Letter checks: {'; '.join(result['email_problems']) or 'all passed'}", ""]

    lines += ["## Appendix A. Every record a kept finding, requirement or initiative cites", ""]
    cited = [f["identifier"] for f in result["findings"]] + [r["identifier"] for k in ("requirements", "initiatives") for r in a[k]]
    for i in dict.fromkeys(cited):
        lines += record_entry(i, layer, details) + [""]

    lines += ["## Appendix B. The organization record for the chain", ""]
    hops = upward(layer.org_id(a["program_office"]) or "", orgs) if a["fit"] else []
    for child, parent in zip(hops, hops[1:] + [""]):
        o = orgs[child]
        lines.append(f"- {office_name(child, orgs)} ({o['name']}), type {o.get('org_type') or 'unknown'}, "
                     + (f"under {office_name(parent, orgs)}" if parent else "at the top of the record"))
        edge = edges.get((nodes.get(child, {}).get("id"), nodes.get(parent, {}).get("id"))) if parent else None
        if parent and not edge:
            lines.append("  - no parentage statement for this edge in the organization record")
        if edge:
            lines.append(f"  - {edge['id']}, {edge['evidence_class'].replace('_', ' ')}")
        for obs_id in (edge or nodes.get(child) or {}).get("observation_ids", [])[:3]:
            ob = observed.get(obs_id)
            if ob:
                lines.append(f"  - {obs_id}, observed {ob['observed_at']}, {ob['statement_type']}, {ob['source_url']}: \"{' '.join(ob['passage'].split())[:400]}\"")
    lines += ["", "## Appendix C. People and routes", ""]
    lines += [f"- {c['name']}, {c['title']}, {c['office']}; e-mail {c['email'] or 'none on record'}; observed {c['observed_at']}; "
              f"{c['source']} {c['source_url'] or c['source_ref']}" for c in result["contacts"]]
    for r in a["routes"]:
        lines += [f"- route {w['recommendation']}; {w['office_id']}; observed {w['observed_at']}; {w['source_url']}"
                  for w in layer.routes if verbatim(r["route"], w["recommendation"])][:1] or [f"- route {r['route']}: not found among the saved routes"]
    return "\n".join(lines) + "\n"


def slug(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="outreach.py run")
    ap.add_argument("--company", required=True)
    ap.add_argument("--profile", required=True, help="what the company sells, in its own words, or a file holding it")
    ap.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    ap.add_argument("--check", action="store_true", help="replay from cassettes only and compare with the saved run")
    args = ap.parse_args(argv)
    profile = Path(args.profile).read_text(encoding="utf-8").strip() if Path(args.profile).is_file() else args.profile
    layer = Layer(json.loads(CORPUS.read_text(encoding="utf-8")), load_people())
    result = outreach(layer, args.company, profile, args.steps, replay_only=args.check)
    text = json.dumps(result, indent=1, ensure_ascii=False) + "\n"
    out = OUT / f"{slug(args.company)}.json"
    print(readout(result))
    if args.check:
        same = out.exists() and settled(json.loads(out.read_text(encoding="utf-8"))) == settled(result)
        print("outreach matches the saved run" if same else "outreach differs from the saved run", file=sys.stderr)
        return 0 if same else 1
    OUT.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    write_audit(result, layer)
    return 0


def write_audit(result: dict, layer: Layer) -> Path:
    seed = json.loads(SEED.read_text(encoding="utf-8")) if SEED.exists() else {}
    path = OUT / f"{slug(result['company'])}.audit.md"
    path.write_text(audit(result, layer, seed), encoding="utf-8")
    print(f"audit written to {path.relative_to(RESEARCH.parent)}", file=sys.stderr)
    return path


def show(argv: list[str]) -> int:
    print(readout(json.loads((OUT / f"{argv[0]}.json").read_text(encoding="utf-8"))))
    return 0


def audit_command(argv: list[str]) -> int:
    write_audit(json.loads((OUT / f"{argv[0]}.json").read_text(encoding="utf-8")), Layer(json.loads(CORPUS.read_text(encoding="utf-8")), load_people()))
    return 0


def fixture() -> Walk:
    """The department over a command, a PEO and a program office, a sibling office under another PEO, a contracting
    office; a forecast row, a topic, a directive at the department, a priority under the sibling; one program manager."""
    orgs = {"don": {"acronym": "", "name": "Department of the Navy", "parent": "", "org_type": "agency"},
            "cmd": {"acronym": "", "name": "NAVWAR", "parent": "don", "org_type": "contracting_activity"},
            "peo": {"acronym": "", "name": "PEO C4I", "parent": "cmd", "org_type": "program_executive_office"},
            "pmw": {"acronym": "PMA/PMW 101", "name": "MIDS", "parent": "peo", "org_type": "program_office"},
            "peo2": {"acronym": "", "name": "PEO Digital", "parent": "cmd", "org_type": "program_executive_office"},
            "far": {"acronym": "PMW 205", "name": "Naval Enterprise Networks", "parent": "peo2", "org_type": "program_office"},
            "ko": {"acronym": "N00039", "name": "NAVWAR contracts", "parent": "cmd", "org_type": "contracting_office"}}
    ev = lambda i, fam, typ, day, org, text: {"id": i, "event_type": typ, "date": day, "available_by": day, "provider": fam, "family": fam,
                                                "org": org, "title": text, "text": text, "slip": False, "stage": "", "polarity": ""}
    events = [ev("t1", "programs", "sbir_topic", "2025-04-02", "pmw", "N252-D11 Advanced Interference Mitigation for MIDS tactical data links"),
              ev("c1", "congress", "congressional_directive", "2025-07-01", "don", "The committee directs a briefing on Link 16 terminal production capacity"),
              ev("l1", "leaders", "capability_priority", "2025-08-01", "far", "Enterprise network modernization is the first priority"),
              ev("n1", "notice", "presolicitation_posted", "2026-08-12", "ko", "SAM.gov presolicitation 2026-08-12: MIDS WDL SF3 Radio")]
    needs = [{"key": "N00039-25-RFPREQ-PMA/PMW-101-0046", "title": "MIDS WDL SF3 Radio (C)", "owner": "PMA/PMW 101", "owner_id": "pmw"}]
    roster = [{"name": "Ann Example", "emails": ["ann.example@us.navy.mil"],
               "positions": [{"office": "pmw:101", "org": "pmw", "role_type": "program_manager", "raw_title": "Program Manager", "observed_at": "2026-08-01",
                              "source": "sam", "source_ref": "x", "source_url": "", "confidence": "0.9", "context": "MIDS"}]},
              {"name": "Bob Elsewhere", "emails": [], "positions": [{"office": "pmw:205", "org": "far", "role_type": "program_manager", "raw_title": "Program Manager",
                                                                     "observed_at": "2026-08-01", "source": "sam", "source_ref": "y", "source_url": "",
                                                                     "confidence": "0.9", "context": ""}]}]
    return Walk(Layer({"orgs": orgs, "events": events, "needs": needs, "outcomes": []}, roster, as_of="2026-09-01", routes=[]))


def selfcheck() -> int:
    walk = fixture()
    ini = walk.call("initiatives", "PMA/PMW 101")
    assert [e["id"] for e in ini["newest"]] == [] and ini["department_wide"] == 1, "the department's directive is counted, the sibling's priority is not shown"
    assert walk.call("search", "Link 16 terminal maker")["note"].startswith("no record carries") and "note" not in walk.call("search", "Link 16")
    walk.call("office", "PMA/PMW 101")
    assert "c1" in walk.shown and "N00039-25-RFPREQ-PMA/PMW-101-0046" in walk.shown and "t1" in walk.shown and "Ann Example" in walk.people
    assert walk.opened == {"pmw"} and walk.ancestors("pmw") == ["pmw", "peo", "cmd", "don"]
    good = {"fit": True, "agency": "Department of the Navy", "command": "NAVWAR", "peo": "PEO C4I", "program_office": "PMA/PMW 101", "why_office": "It owns MIDS.",
            "requirements": [{"identifier": "N00039-25-RFPREQ-PMA/PMW-101-0046", "quote": "MIDS WDL SF3 Radio", "why": "a terminal"}],
            "initiatives": [{"identifier": "c1", "quote": "Link 16 terminal production capacity", "why": "Congress asks about capacity."}],
            "people": [{"name": "Ann Example", "why": "She manages the program."}], "routes": [], "no_fit": ""}
    assert chain_problems(walk, good) == [], chain_problems(walk, good)
    bad = [({"peo": ""}, "sits under PEO C4I; name it as the peo"), ({"command": "PEO Digital"}, "is not above PMA/PMW 101 in the record; the record puts it under NAVWAR"),
           ({"program_office": "PMW 205"}, "was not opened"), ({"program_office": "N00039"}, "not a buying office"),
           ({"command": "PEO C4I", "peo": "NAVWAR"}, "'NAVWAR' is a contracting_activity, not a peo; the record puts it under PEO C4I"),
           ({"initiatives": [{"identifier": "N00039-25-RFPREQ-PMA/PMW-101-0046", "quote": "MIDS WDL SF3 Radio", "why": ""}]}, "both as a requirement and as an initiative"),
           ({"requirements": [{"identifier": "N00039-25-RFPREQ-PMA/PMW-101-0046", "quote": "MIDS WDL SF3 Radio", "why": ""}] * 2}, "named 2 times"), ({"agency": ""}, "name it as the agency"),
           ({"requirements": [{"identifier": "made-up", "quote": "x", "why": ""}]}, "made-up was not shown"),
           ({"requirements": [{"identifier": "t1", "quote": "MIDS radio", "why": ""}]}, "not in the record verbatim"),
           ({"initiatives": [{"identifier": "l1", "quote": "network modernization", "why": ""}]}, "sits outside the chain"),
           ({"initiatives": [{"identifier": "n1", "quote": "MIDS WDL SF3 Radio", "why": ""}]}, "is a notice record, not what leaders"),
           ({"people": [{"name": "Bob Elsewhere", "why": ""}]}, "was not shown for this chain (shown for it: Ann Example)"), ({"people": []}, "no one to write to"),
           ({"why_office": "An RFP is likely soon."}, "banned phrase")]
    walk.shown["l1"] = walk.layer.brief(walk.events["l1"])
    for change, problem in bad:
        found = chain_problems(walk, {**good, **change})
        assert any(problem in p for p in found), (change, found)
    assert chain_problems(walk, {**good, "fit": False, "no_fit": "The record holds no aerosol buyer."}) == []
    topic_only = {**good, "requirements": [], "initiatives": [{"identifier": "t1", "quote": "Advanced Interference Mitigation", "why": "a topic"}]}
    assert chain_problems(walk, topic_only) == [], chain_problems(walk, topic_only)
    unnamed = chain_problems(walk, {**good, "requirements": []})
    assert any(x.startswith("no requirement named, and no initiative under the program office") and "N00039-25-RFPREQ-PMA/PMW-101-0046: " in x for x in unnamed), unnamed
    key = "N00039-25-RFPREQ-PMA/PMW-101-0046"  # a row's description is searched, shown with it, and quotable
    described = Walk(walk.layer, {key: {"release": "2025-06-19", "procurement method": "Full and Open Competition", "contracting POC": "Ann Example",
                                        "description": "Small terminals with embedded cryptographic engines, software configurable."}})
    found = described.call("search", "embedded cryptographic")
    assert described.call("search", "terminal")["forecast_rows_total"] == 1  # "Small terminal" by description, not the title
    assert found["forecast_rows_total"] == 1 and found["forecast_rows"][0]["row"]["description"].startswith("Small terminals") and "note" not in found
    assert verbatim("embedded cryptographic engines", described.text(key)) and not verbatim("embedded cryptographic engines", walk.text(key))
    assert Walk(walk.layer).call("search", "embedded cryptographic").get("note")
    assert described.people["Ann Example"]["named_on"] == f"contracting POC on forecast row {key}" and "Full and Open" in described.text(key)
    proof = evidence(walk, good)
    assert "Chain: Department of the Navy (Department of the Navy) > NAVWAR (NAVWAR) > PEO C4I (PEO C4I) > PMA/PMW 101 (MIDS)" in proof \
        and "ann.example@us.navy.mil" in proof, proof
    mail = {"to": "Ann Example", "subject": "MIDS WDL SF3 Radio (N00039-25-RFPREQ-PMA/PMW-101-0046)", "cites": ["N00039-25-RFPREQ-PMA/PMW-101-0046", "c1"],
            "body": "Ann Example, our terminals fit the MIDS WDL SF3 Radio row, and the committee directs a briefing on Link 16 terminal production capacity."}
    assert email_problems(walk, good, mail, proof, "Acme builds Link 16 terminals") == [], email_problems(walk, good, mail, proof, "")
    by_topic = {**mail, "subject": "MIDS", "cites": ["t1"], "body": "Ann Example, the topic Advanced Interference Mitigation for MIDS tactical data links."}
    assert email_problems(walk, topic_only, by_topic, evidence(walk, topic_only), "Acme") == [], email_problems(walk, topic_only, by_topic, evidence(walk, topic_only), "Acme")
    assert "cites no initiative" in email_problems(walk, topic_only, {**by_topic, "cites": []}, evidence(walk, topic_only), "Acme")
    for change, problem in [({"to": "Bob Elsewhere"}, "not a chosen person"), ({"cites": ["c1"]}, "cites no requirement"), ({"cites": ["x", "[N00039-25-RFPREQ-PMA/PMW-101-0046]"]}, "cites x"),
                            ({"body": "Contract N00039-26-C-0099 ends in FY2027."}, "'N00039-26-C-0099' is in no evidence"),
                            ({"body": "Bob Elsewhere suggested we write."}, "names Bob Elsewhere"), ({"body": "A short call — this week."}, "em dash"),
                            ({"body": "The RFP is imminent."}, "banned phrase"), ({"body": "Dear Ms. Example, about MIDS."}, "courtesy title"),
                            ({"body": "Requirement dd1980e1-8723-5748-9ff1-829aabbc0548 fits."}, "names the internal key dd1980e1")]:
        found = email_problems(walk, good, {**mail, **change}, proof, "Acme builds Link 16 terminals")
        assert any(problem in p for p in found), (change, found)
    said = []

    def fake(system, user, schema, name, **_):
        said.append(name)
        answers = {"outreach_seed": {"terms": [{"term": "Link 16 terminal maker", "promise": 0.9}], "relevant_when": "a terminal"},
                   "outreach_reseed": {"terms": [{"term": "Link 16", "promise": 0.8}, {"term": "LINK 16 terminal maker", "promise": 0.7}], "relevant_when": ""},
                   "outreach_step": {"relevance": 0, "findings": [], "next": [], "stop_branch": True, "note": ""},
                   "outreach_chain": {"fit": False, "agency": "", "command": "", "peo": "", "program_office": "", "why_office": "", "requirements": [],
                                      "initiatives": [], "people": [], "routes": [], "no_fit": "The record holds no terminal buyer."}}
        return answers[name], {"cassette": "", "replayed": True, "usage": {}}

    real, globals()["structured"] = structured, fake
    try:
        dry = outreach(fixture().layer, "Acme", "Acme builds Link 16 terminals")
    finally:
        globals()["structured"] = real
    assert said == ["outreach_seed", "outreach_step", "outreach_reseed", "outreach_step", "outreach_reseed", "outreach_chain"], said
    assert [(s["argument"], s["counted"]) for s in dry["steps"]] == [("Link 16 terminal maker", False), ("Link 16", True)] and dry["status"] == "no fit", dry["steps"]
    run_ = {"company": "Acme", "profile": "Acme builds Link 16 terminals", "as_of": "2026-09-01", "model": "m", "status": "written",
            "usage": {"input_tokens": 1, "output_tokens": 1}, "seed": {"terms": [{"term": "Link 16", "promise": 0.9}], "relevant_when": "a terminal"},
            "steps": [{"n": 1, "tool": "office", "argument": "PMA/PMW 101", "from": 0, "promise": 0.9, "reason": "seed term", "relevance": 1,
                       "stop_branch": False, "note": "It owns MIDS.", "shown": '{"office": "PMA/PMW 101"}',
                       "dropped": [{"identifier": "zz", "quote": "q", "why": "", "refused": "the record never showed this identifier"}]}],
            "findings": [{"identifier": "t1", "quote": "MIDS tactical data links", "why": "a topic", "path": ["office PMA/PMW 101"]}],
            "dossier": "the walk", "calls": [{"name": "outreach_chain", "refused": ["no one to write to"]}], "chain": good, "chain_problems": [],
            "evidence": proof, "email": mail, "email_problems": [], "contacts": [walk.people["Ann Example"]]}
    page = audit(run_, walk.layer, {})
    for part in ('```json\n{"office": "PMA/PMW 101"}\n```', '- kept t1: "MIDS tactical data links"', "- refused zz: \"q\"; the record never showed",
                 "refused the first chain answer: no one to write to", "Letter checks: all passed", "### t1", "filed at Department of the Navy > NAVWAR > PEO C4I > PMA/PMW 101",
                 "owned by Department of the Navy > NAVWAR > PEO C4I > PMA/PMW 101", "- PEO C4I (PEO C4I), type program_executive_office, under NAVWAR",
                 "no parentage statement for this edge", "- Ann Example, Program Manager, pmw:101; e-mail ann.example@us.navy.mil"):
        assert part in page, part
    moves = [{"argument": "a", "promise": 0.5}, {"argument": "b", "promise": 0.9}, {"argument": "c", "promise": 0.9}]
    assert [next_move(moves)["argument"] for _ in range(3)] == ["c", "b", "a"]
    assert slug("Acme Link, Inc.") == "acme-link-inc"
    print("outreach selfcheck ok")
    return 0


COMMANDS = {"run": run, "show": show, "audit": audit_command}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--selfcheck":
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

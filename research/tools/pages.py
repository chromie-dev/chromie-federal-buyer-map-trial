"""Object pages of the twin, on demand: an office, a vendor, a person or a requirement cell as text, each ending with the
sources that speak about it, how much, when each last spoke, and which families are silent. Nothing is stored.

    python research/tools/pages.py office "PMW 150"
    python research/tools/pages.py vendor Leidos
    python research/tools/pages.py person "Damon R Griffin"
    python research/tools/pages.py cell N00039-23-RFPREQ-PMA/PMW-101-0010
    python research/tools/pages.py --selfcheck
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from agency import P  # noqa: E402
from backtest import CORPUS, GENERIC, RESEARCH, awards_not_collected, chain, need_aliases, need_cell, recurring_tokens, scan, scan_term, shift  # noqa: E402
from buying_dna import PIID_RE  # noqa: E402
from people import SEED, contacts_for, load_routes, norm_name, own_contacts, routes_for, uid  # noqa: E402
from pulse import CONTRACT_RE, card, days_between, load_people, office_name, score  # noqa: E402
from trace import STOP, distinctive_tokens  # noqa: E402
from vendors import load as load_vendors, names_for  # noqa: E402

DNA = RESEARCH / "results" / "buying_dna.json"
READS = RESEARCH / "results" / "office_reads.json"  # office_wiki.py build: the model's reading of each record no program office holds
# The readings the views use: those whose masked trial (office_wiki.py trial) names the office a record states. Topics are
# read and saved but not used, since on topics that name their office the model names a neighbour too often; committee
# statements name no program office to test against, so their readings are saved and not used either.
READ_KINDS = ("notices", "awards")
TWO_YEARS = 730
SHOWN = 8
SAM_API_RE = re.compile(r"https://sam\.gov/api/prod/opps/v2/opportunities/(\w+)\S*")  # the API link to a notice, whose page is /opp/ID/view
OWNER_TYPES = ("program_office", "program_executive_office", *P.get("owner_types", ()))  # a profile adds the types that own its programs
# How the agency writes an office code and a hull designator (the profile's `reading`), and a contract number (`piid_re`).
OFFICE_CODE_RE = re.compile(P["reading"]["office_code_re"])
HULL_RE = re.compile(P["reading"]["hull_re"])
SOLICITATION_RE = re.compile(r"; solicitation ([A-Za-z0-9_-]+);")
TOPIC_DATES_RE = re.compile(r"; opens (\d{4}-\d{2}-\d{2})?, closes (\d{4}-\d{2}-\d{2})?;")
PIID_TEXT_RE = re.compile(P["piid_re"])
# Words any notice title may carry whoever buys: the notice kind, the instrument and the parts vocabulary.
NOTICE_WORDS = {"day", "industry", "request", "information", "report", "summary", "notice", "intent", "sources", "sought", "synopsis",
                "solicitation", "presolicitation", "amendment", "draft", "special", "announcement", "justification", "approval", "sole",
                "source", "brand", "name", "only", "purchase", "repair", "parts", "part", "equipment", "items", "item", "various", "kit",
                "kits", "material", "delivery", "requirement", "requirements", "acquisition", "event", "session", "questions", "answers",
                "update", "extension", "modification", "exercise", "letter", "direction", "technical", "phase", "sbir", "sttr", "vessel", "vessels",
                "usn", "fms", "uca", "lrae", "rfpreq", "peo", "pae", "c4i", "iii", "cno"}


class Layer:
    """The frozen corpus, the people file and the pulse, read as six views: search, office, cell, topics, people, neighbours."""

    def __init__(self, corpus: dict, roster: list[dict], as_of: str | None = None, routes: list[dict] | None = None):
        self.corpus, self.orgs, self.events = corpus, corpus["orgs"], corpus["events"]
        self.needs = corpus["needs"]
        self.roster = roster
        self.routes = load_routes() if routes is None else routes
        self.as_of = as_of or max(e["available_by"] for e in self.events)
        self.routes_by = as_of  # a replay hides a route observed after its date; the live view shows every route known
        self.recurring = recurring_tokens(self.needs)
        self.edges = seed_edges(self.orgs, self.as_of)
        self.by_acronym = {o["acronym"].lower(): oid for oid, o in self.orgs.items() if o.get("acronym")}
        self.by_acronym.update({o["name"].lower(): oid for oid, o in self.orgs.items()})
        self.by_acronym.update(seed_names(self.by_acronym, self.orgs))
        head = lambda o: o["name"].split(",")[0].strip().lower()  # "NRL Code 7600" for "NRL Code 7600, Space Science DIV"
        heads = Counter(head(o) for o in self.orgs.values())
        self.by_acronym.update({head(o): oid for oid, o in self.orgs.items() if heads[head(o)] == 1 and head(o) not in self.by_acronym})
        # A notice filed at a contracting office is read to the office other records place it with, so an office's
        # questions see it; the filed office and the basis travel with it, and the corpus itself is not changed.
        read = read_offices(corpus, self.org_id)
        # A notice no record places keeps its filed office and carries the offices whose records share its program names,
        # and the model's reading of it against the office pages when that reading quotes both verbatim.
        guessed, modelled = guess_offices(corpus, read), model_reads(corpus)
        self.events = [{**e, "org": read[e["id"]][0], "filed": e["org"], "read_as": read[e["id"]][1]} if e["id"] in read
                       else {**e, **({"guesses": guessed[e["id"]]} if e["id"] in guessed else {}),
                             **({"model_read": modelled[e["id"]]} if e["id"] in modelled else {})} if e["id"] in guessed or e["id"] in modelled
                       else e for e in self.events]

    def reach(self, oid: str) -> list[str]:
        """The office, the offices above it, then each contracting office the graph documents buying for one of them:
        whose people answer for the office. ONR holds no contact of its own; its contracting office N00014 does."""
        up = chain(oid, self.orgs) or [oid]
        buyers = [e["from"]["oid"] for e in self.edges if e["relation"] == "contracts for" and e["to"]["oid"] in up
                  and e["from"]["oid"] and e["from"]["oid"] not in up]
        return up + list(dict.fromkeys(buyers))

    def org_id(self, name: str) -> str | None:
        key = re.sub(r"\s+", " ", name).strip().lower().replace("pmw-", "pmw ")
        return self.by_acronym.get(key)

    def brief(self, e: dict) -> dict:
        row = {"id": e["id"], "date": e["available_by"], "family": e["family"], "type": e["event_type"], "stage": e.get("stage", ""),
               "polarity": e.get("polarity", ""), "office": place(e, self.orgs), "title": e["title"][:140]}
        return {**row, **topic_status(e, self.as_of)}

    def need_brief(self, n: dict) -> dict:
        row = {"need_key": n["key"], "owner": n["owner"] or "no office named", "title": n["title"][:120],
               "type": "notice with no forecast row" if n["key"].startswith("notice:") else "forecast row"}
        return row

    def search(self, term: str) -> dict:
        # The phrase as written first; a multi-word phrase the record never repeats word for word is then read as its words,
        # each widened to its capability's words, every word required, and the answer says which reading matched.
        found, matched = scan_term(term, self.events)
        hits = [e for e in found if e["available_by"] <= self.as_of]
        offices = Counter(office_name(e["org"], self.orgs) or "-" for e in hits)
        rows = sorted((n for n in self.needs if re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", n["title"], re.I)),
                      key=lambda n: n["key"])
        return {"term": term, "matched": matched or "nothing", "statements": len(hits), "families": dict(Counter(e["family"] for e in hits).most_common()),
                "offices": dict(offices.most_common(SHOWN)),
                "newest": [self.brief(e) for e in sorted(hits, key=lambda e: e["available_by"], reverse=True)[:SHOWN]],
                "forecast_rows": [self.need_brief(n) for n in rows[:SHOWN]], "forecast_rows_total": len(rows)}

    def subtree(self, oid: str) -> set[str]:
        """The organization and every one under it. `upward`, not `chain`: chain leaves the agency out, so an agency's
        subtree would be empty. An agency's also holds the roots no stated parent ties to it (a PEO or a command whose
        parent no source names) and the statements filed at no office (""): everything in the record is the agency's."""
        agency = self.orgs.get(oid, {}).get("org_type") == "agency"
        return {o for o in self.orgs if oid in (up := upward(o, self.orgs)) or agency and self.orgs[up[-1]].get("org_type") != "agency"} | ({""} if agency else set())

    def children(self, oid: str) -> list[str]:
        """The organizations whose stated parent is this one: the one list office and neighbors both read."""
        return sorted(named_office(o, self.orgs) for o, row in self.orgs.items() if row["parent"] == oid)

    def office(self, name: str) -> dict:
        oid = self.org_id(name)
        if not oid:
            return {"office": name, "error": "no organization by that name; use neighbors or search to find one"}
        tree = self.subtree(oid)
        mine = [e for e in self.events if e["org"] in tree and e["available_by"] <= self.as_of]
        owned = [n for n in self.needs if n["owner_id"] in tree]
        vendors = Counter(e.get("vendor", "") for e in mine if e["family"] == "incumbent" and e.get("vendor"))
        guessed = sorted((e for e in self.events if pointed(e) in tree and e["available_by"] <= self.as_of),
                         key=lambda e: e["available_by"], reverse=True)
        o = self.orgs[oid]
        return {"office": o["acronym"] or o["name"], "name": o["name"], "type": o.get("org_type", ""),
                "chain": [office_name(x, self.orgs) for x in upward(oid, self.orgs)[1:]],
                "children": len(self.children(oid)), "statements": len(mine), "families": dict(Counter(e["family"] for e in mine).most_common()),
                "forecast_rows_total": len(owned), "forecast_rows": [self.need_brief(n) for n in owned[:SHOWN]],
                "newest": [self.brief(e) for e in sorted(mine, key=lambda e: e["available_by"], reverse=True)[:SHOWN]],
                "topics": [self.brief(e) for e in sorted((e for e in mine if e["family"] == "programs"), key=lambda e: e["available_by"], reverse=True)[:5]],
                "vendors": dict(vendors.most_common(5)),
                "guessed_total": len(guessed), "guessed": [self.brief(e) for e in guessed[:5]],
                "people": contacts_for(self.reach(oid), self.roster, self.as_of)[:5],
                "routes": routes_for(self.reach(oid), self.routes, self.routes_by),
                # What the office was consolidated into, is part of, is listed with and who leads it, each with its source
                "relations": [{"edge": f"{e['from']['name']} {e['relation']} {e['to']['name']}", "status": e["status"],
                               **({"from_date": e["from_date"]} if e.get("from_date") else {}),
                               "source": e["sources"][0]["url"] if e["sources"] else ""}
                              for e in self.edges if oid in (e["from"]["oid"], e["to"]["oid"]) and e["relation"] != "contracts for"][:SHOWN]}

    def statement(self, e: dict) -> dict:
        """One statement in full, with the forecast rows that share a name with it: the way from a topic or a notice to a cell."""
        names = [a for a in need_aliases(e["title"], self.recurring, None, [o["acronym"] for o in self.orgs.values() if o.get("acronym")]) if len(a) >= 4][:6]
        rows = [n for n in self.needs if any(re.search(r"(?<![A-Za-z0-9])" + re.escape(a) + r"(?![A-Za-z0-9])", n["title"], re.I) for a in names)]
        return {**self.brief(e), "text": e["text"][:700], "vendor": e.get("vendor", ""), "names": names,
                "forecast_rows": [self.need_brief(n) for n in rows[:SHOWN]], "forecast_rows_total": len(rows),
                # The statement's own contacts (the notice's points of contact) stand apart from the office's people, who
                # answer for other solicitations; the office's are labelled so, never read as this statement's.
                "people": own_contacts(e, self.roster, self.as_of)[:3],
                "office_contacts": contacts_for(self.reach(e["org"]), self.roster, self.as_of)[:3] if e["org"] else [],
                "office_contacts_note": "whom the record ties to the office and the offices above it, not to this statement"}

    def cell(self, need_key: str) -> dict:
        need = next((n for n in self.needs if n["key"] == need_key), None)
        if need is None:
            e = next((e for e in self.events if e["id"] == need_key), None)
            if e is not None:
                return self.statement(e)
            return {"need_key": need_key, "error": "no forecast row or statement with that identifier"}
        aliases, hits = need_cell(need, self.corpus, self.recurring)
        hits = [e for e in hits if e["available_by"] <= self.as_of]
        scored = score(hits, self.as_of)
        cell = {"key": f"need:{need_key}", "org": need["owner_id"], "office": need["owner"], "name": need["title"], "aliases": aliases, "terms": [], **scored}
        self.need_brief(need)
        return {"need_key": need_key, "owner": need["owner"], "title": need["title"], "aliases": aliases, "score": scored["score"],
                "families": scored["families"], "stage": scored["stage"], "next": scored["next"], "against": len(scored["against"]),
                "evidence": {f: [self.brief(next(e for e in hits if e["id"] == r["id"])) for r in rows] for f, rows in scored["evidence"].items()},
                "vendors": scored["vendors"], "card": card(cell, hits, self.as_of, self.orgs)}

    def topics(self, term: str) -> dict:
        found, matched = scan_term(term, [e for e in self.events if e["family"] == "programs"])
        hits = [e for e in found if e["available_by"] <= self.as_of]
        return {"term": term, "matched": matched or "nothing", "topics": len(hits), "offices": dict(Counter(office_name(e["org"], self.orgs) or "-" for e in hits).most_common(SHOWN)),
                "newest": [self.brief(e) for e in sorted(hits, key=lambda e: e["available_by"], reverse=True)[:SHOWN]]}

    def people(self, name: str) -> dict:
        oid = self.org_id(name)
        if not oid:
            return {"office": name, "error": "no organization by that name"}
        everyone = contacts_for(self.reach(oid), self.roster, self.as_of, limit=None)
        return {"office": office_name(oid, self.orgs), "people": everyone[:SHOWN],
                **({"shown": f"{SHOWN} of {len(everyone)} (nearest office, newest, leaders first); the rest are in memory/people.json"} if len(everyone) > SHOWN else {})}

    def neighbors(self, name: str) -> dict:
        oid = self.org_id(name)
        if not oid:
            near = [o["acronym"] or o["name"] for o in self.orgs.values() if name.lower() in (o["acronym"] + " " + o["name"]).lower()][:SHOWN]
            return {"office": name, "error": "no organization by that name", "similar": near}
        parent = self.orgs[oid]["parent"]
        up = {o: i for i, o in enumerate([oid, *chain(oid, self.orgs)])}  # the office first, then each level above it
        near = sorted((e for e in self.edges if e["from"]["oid"] in up or e["to"]["oid"] in up),
                      key=lambda e: min(up.get(e["from"]["oid"], len(up)), up.get(e["to"]["oid"], len(up))))
        return {"office": office_name(oid, self.orgs), "parent": office_name(parent, self.orgs) if parent else "",
                "siblings": sorted(named_office(o, self.orgs) for o, row in self.orgs.items() if row["parent"] == parent and o != oid and parent)[:SHOWN * 2],
                "children": self.children(oid),
                "relationships": [{"relation": f"{e['from']['name']} {e['relation']} {e['to']['name']}",
                                   **{k: e[k] for k in ("evidence", "status", "from_date", "to_date", "note") if e.get(k)}, "sources": e["sources"]}
                                  for e in near[:SHOWN * 2]]}


def named_office(oid: str, orgs: dict) -> str:
    """An office as a reader needs it: the short name with the full one when they differ ("ONR Code 32" alone says nothing)."""
    o = orgs.get(oid) or {}
    short, full = o.get("acronym") or "", o.get("name") or ""
    return full if not short or full.startswith(short) else f"{short} ({full})"


def row_contacts(layer: Layer, owner: str, key: str, row: dict[str, str], by_name: dict[str, dict]) -> list[dict]:
    """The contacts a forecast row names, as the record holds them in the reach of the office that owns the row: they
    answer for this row rather than whoever the office's newest notice named."""
    people = [(label, by_name.get(norm_name(row.get(label, "")))) for label in ("contracting POC", "secondary POC")]
    return [{**c, "named_on": f"{label} on forecast row {key}"} for label, person in people if person
            for c in contacts_for(layer.reach(owner), [person], layer.as_of, limit=1)]


def topic_status(e: dict, as_of: str) -> dict:
    """An SBIR topic's status on the record date from its open and close dates, with the dates; {} for another record or
    a topic with neither date. The statement's own word is the portal's on the day the page was read, not the record date's."""
    m = TOPIC_DATES_RE.search(e.get("text") or "") if e.get("event_type") == "sbir_topic" else None
    if not m or not any(m.groups()):
        return {}
    opens, closes = m.groups()
    status = "pre-release" if opens and opens > as_of else "closed" if closes and closes < as_of else "open"
    return {k: v for k, v in (("opens", opens), ("closes", closes), ("status", status)) if v}


def upward(oid: str, orgs: dict) -> list[str]:
    """The office and every organization above it, the agency included (`backtest.chain` leaves the agency out for matching)."""
    out = []
    while oid and oid in orgs and oid not in out:
        out.append(oid)
        oid = orgs[oid]["parent"]
    return out


def seed_edges(orgs: dict[str, dict], as_of: str) -> list[dict]:
    """The organization graph's edges other than the tree: who contracts for an office, who leads it, what it was
    consolidated into, what it is part of or listed with. Each joins record offices, or a person and one, and carries the
    passages that document it, newest first; an edge no passage documents by the as-of date is left out."""
    if not SEED.exists():
        return []
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    # A record office's id is made from its seed id, so the two meet on the id: a name the seed changes later (N00014
    # took its FPDS name on 2026-09-29) still finds the office a frozen corpus holds under the old one.
    nodes = {n["id"]: {"name": n["name"], "oid": oid if (oid := uid("org", n["id"])) in orgs else ""} for n in seed.get("nodes", [])}
    obs = {o["id"]: o for o in seed.get("observations", [])}
    out = []
    for r in seed.get("relationships", []):
        seen = sorted((obs[i] for i in r.get("observation_ids", []) if i in obs and (obs[i].get("observed_at") or "") <= as_of),
                      key=lambda o: o.get("observed_at") or "", reverse=True)
        if r["type"] == "child_of" or not seen or r["from"] not in nodes or r["to"] not in nodes:
            continue
        status, ended = r.get("current_status") or {}, r.get("effective_to") or ""
        out.append({"from": nodes[r["from"]], "to": nodes[r["to"]], "relation": r["type"].replace("_", " "), "evidence": r.get("evidence_class", ""),
                    "status": status.get("state", "") if (status.get("as_of") or "") <= as_of else "last_confirmed",
                    **({"from_date": r["effective_from"]} if (r.get("effective_from") or "9") <= as_of else {}),
                    **({"to_date": ended} if ended and ended <= as_of else {}), "note": r.get("notes") or "",
                    "sources": [{"observed_at": o.get("observed_at"), "url": SAM_API_RE.sub(r"https://sam.gov/opp/\1/view", o.get("source_url") or ""),
                                 "passage": o.get("passage", "")[:240]} for o in seen[:2]]})
    return out


def seed_names(known: dict[str, str], orgs: dict[str, dict]) -> dict[str, str]:
    """The short names the seed observed for an organization (NAVWAR, PEO C4I), each to its id; a short name observed
    for two organizations, or already an acronym or a full name, is left out. A node meets its office on the id."""
    if not SEED.exists():
        return {}
    seen: dict[str, set[str]] = {}
    for node in json.loads(SEED.read_text(encoding="utf-8")).get("nodes", []):
        oid = uid("org", node["id"]) if uid("org", node["id"]) in orgs else None
        for a in node.get("aliases", []) if oid else []:
            seen.setdefault(a["text"].lower(), set()).add(oid)
    return {a: ids.pop() for a, ids in seen.items() if len(ids) == 1 and a not in known}


def compact(token: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", token.upper())


def read_offices(corpus: dict, org_id) -> dict[str, tuple[str, str]]:
    """Notice id -> (office, basis) for a notice filed at a contracting office because it names no program office there.
    The office other records place it with, strongest first: the notice's own text names one; a forecast row carries its
    solicitation number; it cites a contract a program office holds; its title carries a program name that two or more
    forecast rows of one office use and no other office's rows do. A reading travels with its basis and the filed office."""
    orgs = corpus["orgs"]
    owner = lambda oid: orgs.get(oid, {}).get("org_type") in OWNER_TYPES
    needs = [n for n in corpus["needs"] if owner(n["owner_id"])]
    by_sol: dict[str, set[tuple[str, str]]] = {}
    rows_with: dict[str, Counter] = {}
    for n in needs:
        for t in re.findall(r"\bN\d{5}\S*", n["title"]):
            if len(compact(t)) >= 12:
                by_sol.setdefault(compact(t), set()).add((n["owner_id"], n["key"]))
        for t in names_in(n["title"]):
            rows_with.setdefault(t.upper(), Counter())[n["owner_id"]] += 1
    names = {t: next(iter(c.items())) for t, c in rows_with.items() if len(c) == 1 and max(c.values()) >= 2}
    held: dict[str, set[str]] = {}
    for e in corpus["events"]:
        if e["family"] == "incumbent" and owner(e["org"]) and (m := CONTRACT_RE.search(e["title"])):
            held.setdefault(compact(m.group(1)), set()).add(e["org"])
    out = {}
    for e in corpus["events"]:
        if e["family"] != "notice" or orgs.get(e["org"], {}).get("org_type") != "contracting_office":
            continue
        named = {oid: code for code in OFFICE_CODE_RE.findall(e["text"]) if owner(oid := org_id(code) or "")}
        named = {o: c for o, c in named.items() if not any(o != x and o in chain(x, orgs) for x in named)}  # PEO C4I ... PMW 760 names PMW 760
        sol = SOLICITATION_RE.search(e["text"])
        rows = by_sol.get(compact(sol.group(1)), set()) if sol else set()
        cites = {(o, p) for p in PIID_TEXT_RE.findall(e["text"]) for o in held.get(compact(p), ())}
        carried = {names[t.upper()][0]: t for t in names_in(plain_title(e["title"])) if t.upper() in names}
        if len(named) == 1:
            (oid, code), = named.items()
            out[e["id"]] = (oid, f"the notice names {code}")
        elif len({o for o, _ in rows}) == 1:
            oid, key = min(rows)
            out[e["id"]] = (oid, f"forecast row {key} carries solicitation {sol.group(1)}")
        elif len({o for o, _ in cites}) == 1:
            oid, piid = min(cites)
            out[e["id"]] = (oid, f"it cites contract {piid}, which {office_name(oid, orgs)} holds")
        elif len(carried) == 1:
            (oid, token), = carried.items()
            out[e["id"]] = (oid, f"its title carries {token}, a program name {names[token.upper()][1]} forecast rows of {office_name(oid, orgs)} use and no other office's do")
    return out


def title_words(title: str) -> set[str]:
    """The words of a title that can tell whose buy it is: no stop word, notice vocabulary, contract or topic number."""
    return {w for w in re.findall(r"[a-z][a-z0-9]{2,}", bare(title).lower())
            if w not in STOP and w not in GENERIC and w not in NOTICE_WORDS and not re.match(r"n\d{3}|fy\d\d$|oy\d+$", w) and not re.search(r"\d{4}", w)}


def office_words(corpus: dict) -> tuple[dict[str, Counter], dict[str, set[str]], set[str]]:
    """Word -> program office -> how many of its records use it, over its forecast rows and the notices, contract rows,
    forecast statements and topics filed at it; each record's words, so a trial can take records out; and the program
    names: words the titles not written all in capitals mostly write in capitals or with a digit (AEGIS, MIDS, SF2; Class
    and Total are written both ways and name nothing)."""
    owner = lambda oid: corpus["orgs"].get(oid, {}).get("org_type") in OWNER_TYPES
    records = [(f"need:{n['key']}", n["owner_id"], n["title"]) for n in corpus["needs"]]
    records += [(e["id"], e["org"], plain_title(e["title"])) for e in corpus["events"] if e["family"] in ("notice", "incumbent", "forecast", "programs")]
    by_word: dict[str, Counter] = {}
    words_of: dict[str, set[str]] = {}
    for rid, oid, title in records:
        if owner(oid):
            words_of[rid] = title_words(title)
            for w in words_of[rid]:
                by_word.setdefault(w, Counter())[oid] += 1
    capital, seen = Counter(), Counter()
    for title in [n["title"] for n in corpus["needs"]] + [plain_title(e["title"]) for e in corpus["events"]]:
        for part in re.findall(r"[A-Za-z0-9]{3,}", bare(title)) if title.upper() != title else ():
            seen[part.lower()] += 1
            capital[part.lower()] += part.isupper() or bool(re.search(r"\d", part))
    return by_word, words_of, {w for w, n in seen.items() if 2 * capital[w] > n}


def rank_offices(title: str, by_word: dict[str, Counter], records: int, known: set[str], top: int = 3) -> list[tuple[str, float, list[str]]]:
    """Offices by the words they share with a title: each word weighs by how rare it is across the record and by the share
    of its uses that are the office's (MIDS is PMA/PMW 101's own; a word every office uses weighs next to nothing). Only an
    office sharing a word the title writes as a program name ranks: plain words alone (department, research) guess nothing."""
    words = title_words(title)
    ranked = {oid for w in words & known for oid, n in by_word.get(w, Counter()).items() if n > 0}
    scores: Counter = Counter()
    why: dict[str, Counter] = {}
    for w in sorted(words):
        uses = +by_word.get(w, Counter())
        total = sum(uses.values())
        for oid in sorted(ranked & set(uses)):
            why.setdefault(oid, Counter())[w] = math.log(records / total) * uses[oid] / total
            scores[oid] += why[oid][w]
    best = lambda counts: sorted(counts.items(), key=lambda x: (-round(x[1], 6), x[0]))  # ties by name, so a rerun reads the same
    return [(oid, round(s, 2), [w for w, _ in best(why[oid])[:3]]) for oid, s in best(scores)[:top]]


def guess_offices(corpus: dict, read: dict) -> dict[str, list[tuple[str, float, list[str]]]]:
    """Notice id -> the program offices whose own records share its telling words, best first, for a notice filed at a
    contracting office that no record places. A guess never moves the notice: it stays at the office that filed it."""
    by_word, words_of, known = office_words(corpus)
    out = {}
    for e in corpus["events"]:
        if e["family"] == "notice" and e["id"] not in read and corpus["orgs"].get(e["org"], {}).get("org_type") == "contracting_office":
            if ranked := rank_offices(plain_title(e["title"]), by_word, len(words_of), known):
                out[e["id"]] = ranked
    return out


def model_reads(corpus: dict) -> dict[str, tuple[str, str, str]]:
    """Record id -> (office, record words, page line) from the model's saved readings, where the rules held, of the kinds
    whose masked trial holds (READ_KINDS). A reading holds only while its record is still filed at a contracting office:
    a rebuild that files it at an office of its own leaves the saved reading behind."""
    if not READS.exists():
        return {}
    by_name = {office_name(oid, corpus["orgs"]): oid for oid, o in corpus["orgs"].items() if o.get("org_type") in OWNER_TYPES}
    unplaced = {e["id"] for e in corpus["events"] if corpus["orgs"].get(e["org"], {}).get("org_type") == "contracting_office"}
    saved = json.loads(READS.read_text(encoding="utf-8"))
    return {rid: (by_name[a["office"]], a["notice_words"].replace('"', "'"), a["page_line"].replace('"', "'"))
            for kind in READ_KINDS for rid, a in saved.get(kind, {}).items() if a["office"] in by_name and not a["problems"] and rid in unplaced}


def pointed(e: dict) -> str:
    """The office a notice no record places points to: the model's reading, else the first guess from the words."""
    return e["model_read"][0] if e.get("model_read") else e["guesses"][0][0] if e.get("guesses") else ""


def guess_trial(corpus: dict) -> Counter:
    """How the guess fares where the office is known: each notice filed at a program office under a solicitation number is
    guessed with every record under that number taken out, and counted as first, in the top three, or missed. A guess of
    an office under the one that filed it counts as that office: SM-6 filed at PEO IWS and guessed as IWS 3.0 is right."""
    by_word, words_of, known = office_words(corpus)

    def under_filer(oid: str, filer: str) -> bool:
        seen = set()
        while oid and oid not in seen:
            if oid == filer:
                return True
            seen.add(oid)
            oid = corpus["orgs"].get(oid, {}).get("parent")
        return False

    under: dict[str, list[dict]] = {}
    for e in corpus["events"]:
        if e["family"] == "notice" and (m := SOLICITATION_RE.search(e["text"])):
            under.setdefault(compact(m.group(1)), []).append(e)
    out: Counter = Counter()
    for group in under.values():
        stated = [e for e in group if corpus["orgs"].get(e["org"], {}).get("org_type") in OWNER_TYPES]
        if not stated:
            continue
        trimmed, out_ids = dict(by_word), [e for e in group if e["id"] in words_of]
        for e in out_ids:
            for w in words_of[e["id"]]:
                trimmed[w] = trimmed[w].copy() if trimmed[w] is by_word[w] else trimmed[w]
                trimmed[w][e["org"]] -= 1
        for e in stated:
            ranked = rank_offices(plain_title(e["title"]), trimmed, len(words_of) - len(out_ids), known)
            top = [oid for oid, _, _ in ranked]
            hit = [under_filer(oid, e["org"]) for oid in top]
            out["first" if hit[:1] == [True] else "top three" if any(hit) else "no guess" if not top else "missed"] += 1
            if ranked and (len(ranked) == 1 or ranked[0][1] >= 2 * ranked[1][1]):
                out["clear lead, first" if hit[0] else "clear lead, not first"] += 1
    return out


def bare(title: str) -> str:
    """A title without its ship names and hull numbers (USS TRUXTUN (DDG 103), LPD 30): the platform, not the program."""
    return HULL_RE.sub(" ", re.sub(r"\s*\((?:C|N|O)\)\s*$", "", title))


def names_in(title: str) -> set[str]:
    """Program names a title writes in capitals; in a title written all in capitals only a code with a digit counts, since
    every word there is capitalised (INDUSTRY DAY names no program)."""
    tokens = distinctive_tokens(bare(title))
    return {t for t in tokens if re.search(r"\d", t)} if title.upper() == title else tokens


def plain_title(title: str) -> str:
    return title.split(": ", 1)[-1]


def place(e: dict, orgs: dict) -> str:
    """The office a statement sits with; when it was read rather than stated, how, and where the record filed it."""
    where = office_name(e["org"], orgs) or "-"
    if e.get("read_as"):
        return f"{where} (office not stated; {e['read_as']}; filed at {office_name(e['filed'], orgs)})"
    parts = []
    if e.get("model_read"):
        oid, words, line = e["model_read"]
        parts.append(f"the model reads {office_name(oid, orgs)} from \"{words}\" against its page line \"{line}\"")
    if e.get("guesses"):
        parts.append("guessed from the words its records share, best first: "
                     + "; ".join(f"{office_name(oid, orgs)} ({', '.join(w)})" for oid, _, w in e["guesses"]))
    return f"{where} (office not stated; {'; '.join(parts)})" if parts else where


def sources(events: list[dict], as_of: str) -> list[dict]:
    """Which sources speak about this object, how much, and how long each has been silent; the newest speaker first."""
    by = {}
    for e in events:
        by.setdefault(e["provider"], []).append(e["available_by"])
    rows = [{"source": p, "statements": len(d), "first": min(d), "last": max(d), "silent_days": days_between(max(d), as_of)} for p, d in by.items()]
    return sorted(rows, key=lambda r: (r["last"], r["source"]), reverse=True)


def source_lines(events: list[dict], layer: Layer) -> list[str]:
    lines = ["sources speaking about this object:"]
    lines += [f"  - {r['source']}: {r['statements']} statement(s), first {r['first']}, last {r['last']}, silent {r['silent_days']} day(s)" for r in sources(events, layer.as_of)]
    silent = sorted({e["family"] for e in layer.events} - {e["family"] for e in events})
    return lines + ["families silent: " + (", ".join(silent) or "none")]


def book_line(book: dict) -> str:
    v, comp = book["value"], book["competition"]
    share = lambda rows, value: next((r["share"] for r in rows if r["value"] == value), 0)
    return (f"buying book: {book['awards']} awards {book['first_signed']} to {book['last_signed']}; median ${v['median']:,}; "
            f"{round(book['vehicle']['under_an_idv'] * 100)}% under a vehicle; full and open {round(share(comp['extent'], 'FULL AND OPEN COMPETITION') * 100)}%, "
            f"not competed {round(share(comp['extent'], 'NOT COMPETED') * 100)}%; {book['pricing'][0]['value'].lower()} {round(book['pricing'][0]['share'] * 100)}%; "
            f"{book['vendors']['distinct']} vendors, the largest at {round(book['vendors']['top_share'] * 100)}%")


def office(layer: Layer, name: str, dna: dict) -> str:
    o = layer.office(name)
    if "error" in o:
        return o["error"]
    tree = layer.subtree(layer.org_id(name))
    mine = [e for e in layer.events if e["org"] in tree and e["available_by"] <= layer.as_of]
    lines = [f"{o['office']}: {o['name']} ({o['type'] or 'organization'}); above it: {' > '.join(o['chain']) or '-'}; {o['children']} organization(s) directly below",
             f"{o['statements']} statement(s): " + (", ".join(f"{f} {n}" for f, n in o["families"].items()) or "none"),
             # An agency without a forecast owns requirements the notices created, not forecast rows.
             f"{'forecast rows owned' if P['forecast']['pack_glob'] else 'requirements owned (created by the notices)'}: {o['forecast_rows_total']}"]
    lines += [f"  - {n['need_key']}: {n['title']}" for n in o["forecast_rows"]]
    if o["newest"]:
        lines.append("newest statements:")
        lines += [f"  - {e['date']} {e['family']}/{e['type']} [{e['stage']}] {e['title']}" for e in o["newest"]]
    if o["topics"]:
        lines.append("topics:")
        lines += [f"  - {e['date']} {e['title']}" for e in o["topics"]]
    if o["vendors"]:
        lines.append("vendors by contracts: " + ", ".join(f"{v} {n}" for v, n in o["vendors"].items()))
    if o["guessed_total"]:
        lines.append(f"records filed elsewhere that name no program office and point to this office: {o['guessed_total']}")
        lines += [f"  - {e['date']} {e['title']}" for e in o["guessed"]]
    book = dna.get("offices", {}).get(o["office"]) or dna.get("contracting_offices", {}).get(o["office"])
    if book:
        lines.append(book_line(book))
    if o["people"]:
        lines.append("people: " + "; ".join(f"{p['name']} ({p['title'][:60]}, {p['observed_at']})" for p in o["people"]))
    if o["routes"]:
        lines.append("routes in:")
        lines += [f"  - {r['side']}: {r['recommendation'][:140]} ({r['office_id']}, observed {r['observed_at']}, {r['review_status']}"
                  f"{', checked against the saved file' if r.get('checked') else ''})" for r in o["routes"]]
    return "\n".join(lines + source_lines(mine, layer))


def vendor(layer: Layer, name: str, resolved: dict | None = None) -> str:
    spellings = [s.lower() for s in names_for(name, resolved if resolved is not None else load_vendors())]  # every spelling under the UEI
    mine = [e for e in layer.events if e["family"] == "incumbent" and e["available_by"] <= layer.as_of
            and any(s in (e.get("vendor") or "").lower() for s in spellings)]
    # A protest, an article or a directive that names the vendor by one of its spellings: what it does besides holding work.
    said = sorted((e for e in layer.events if e["family"] != "incumbent" and e["available_by"] <= layer.as_of
                   and any(len(s) >= 6 and s in e["title"].lower() for s in spellings)), key=lambda e: e["available_by"], reverse=True)
    if not mine and not said:
        return awards_not_collected() or f"no contract or statement in the record names a vendor matching {name!r}"
    if not mine:
        return "\n".join([f"{name}: {awards_not_collected() or 'no contract in the record'}; {len(said)} statement(s) name it:"]
                         + [f"  - {e['date']} {e['family']}/{e['event_type']}: {e['title'][:110]}" for e in said[:8]] + source_lines(said, layer))
    names = Counter(e["vendor"] for e in mine)
    offices = Counter(office_name(e["org"], layer.orgs) or "-" for e in mine)
    ends = sorted((m.group(2), m.group(1), e["title"].split(": ", 1)[-1]) for e in mine if (m := PIID_RE.search(e["title"])))
    live = [x for x in ends if x[0] > layer.as_of]
    lines = [f"{', '.join(names)}: {len(mine)} contract(s) in the record; offices: " + ", ".join(f"{o} {n}" for o, n in offices.most_common(5)),
             f"live at {layer.as_of}: {len(live)}; ending within two years: {sum(1 for x in live if x[0] <= shift(layer.as_of, TWO_YEARS))}; next end {live[0][0] if live else '-'}"]
    lines += [f"  - ends {d} {p}: {t[:90]}" for d, p, t in live[:8]]
    if said:
        lines.append(f"{len(said)} other statement(s) name it:")
        lines += [f"  - {e['date']} {e['family']}/{e['event_type']}: {e['title'][:110]}" for e in said[:8]]
    return "\n".join(lines + source_lines(mine + said, layer))


def person(layer: Layer, name: str, roster: list[dict]) -> str:
    rows = [r for r in roster if name.lower() in r["name"].lower()]
    if not rows:
        return f"no person in the record matching {name!r}"
    r = rows[0]
    pos = sorted(r["positions"], key=lambda p: p["observed_at"], reverse=True)
    where = lambda p: office_name(p["org"], layer.orgs) or p["office"]
    lines = [f"{r['name']}: {len(pos)} dated position(s); offices: " + ", ".join(sorted({where(p) for p in pos}))]
    lines += [f"  - {p['observed_at']} {where(p)}: {p['role_type']}, {p['raw_title'][:80]}" + (f" ({p['context'][:60]})" if p.get("context") else "") for p in pos[:10]]
    last = {}
    for p in pos:
        last[p["source"]] = max(last.get(p["source"], ""), p["observed_at"])
    lines.append("sources: " + "; ".join(f"{s} {n} position(s), last {last[s]}" for s, n in Counter(p["source"] for p in pos).most_common()))
    if len(rows) > 1:
        lines.append(f"{len(rows) - 1} other person(s) match {name!r}: " + ", ".join(x["name"] for x in rows[1:4]))
    return "\n".join(lines)


def cell(layer: Layer, key: str) -> str:
    c = layer.cell(key)
    if "error" in c:
        return c["error"]
    if "card" not in c:  # a single statement: the record's own brief and the forecast rows it shares a name with
        return json.dumps(c, indent=1, ensure_ascii=False)
    need = next(n for n in layer.needs if n["key"] == key)
    _, hits = need_cell(need, layer.corpus, layer.recurring)
    return "\n".join([c["card"]] + source_lines([e for e in hits if e["available_by"] <= layer.as_of], layer))


def page(kind: str, name: str, layer: Layer, roster: list[dict], dna: dict) -> str:
    if kind == "office":
        return office(layer, name, dna)
    if kind == "vendor":
        return vendor(layer, name)
    if kind == "person":
        return person(layer, name, roster)
    if kind == "cell":
        return cell(layer, name)
    raise SystemExit(f"unknown page kind {kind!r}; office, vendor, person or cell")


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selfcheck"]:
        return selfcheck()
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    dna = json.loads(DNA.read_text(encoding="utf-8")) if DNA.exists() else {}
    roster = load_people()
    print(page(argv[0], argv[1], Layer(corpus, roster), roster, dna))
    return 0


def selfcheck() -> int:
    orgs = {"o1": {"acronym": "PMW 1", "name": "Office One", "parent": None, "org_type": "program_office", "claims": []}}
    ev = lambda i, fam, typ, day, prov, title, vendor="": {"id": i, "event_type": typ, "date": day, "available_by": day, "provider": prov, "family": fam, "org": "o1",
                                                          "title": title, "text": title, "slip": False, "stage": "execution", "polarity": "positive", "vendor": vendor}
    events = [ev("e1", "incumbent", "contract_expires", "2026-01-10", "fpds", "Incumbent contract N1 ends 2027-03-01: WIDGET SUPPORT", "ACME & CO"),
              ev("e2", "forecast", "forecast_created", "2026-06-19", "lrae", "WIDGET SUPPORT (C)"),
              ev("e3", "notice", "rfi_released", "2026-08-01", "sam", "SAM.gov RFI 2026-08-01: WIDGET SUPPORT")]
    corpus = {"orgs": orgs, "events": events, "needs": [{"key": "N-1", "title": "WIDGET SUPPORT (C)", "owner": "PMW 1", "owner_id": "o1"}]}
    roster = [{"name": "Ann Example", "emails": [], "positions": [{"office": "pmw:1", "org": "o1", "role_type": "contract_specialist", "raw_title": "primary point of contact on RFI",
                                                      "observed_at": "2026-08-01", "source": "sam", "source_ref": "x", "source_url": "", "confidence": "0.9", "context": "WIDGET SUPPORT"}]}]
    layer = Layer(corpus, roster, as_of="2026-09-01")
    v = vendor(layer, "acme", {})
    assert "ACME & CO: 1 contract(s)" in v and "next end 2027-03-01" in v and "fpds: 1 statement(s)" in v and "families silent: forecast, notice" in v, v
    o = office(layer, "PMW 1", {})
    assert "3 statement(s)" in o and "forecast rows owned: 1" in o and "silent 31 day(s)" in o and "families silent: none" in o and "Ann Example" in o, o
    p = person(layer, "ann", roster)
    assert "1 dated position(s)" in p and "PMW 1" in p and "sam 1 position(s), last 2026-08-01" in p, p
    assert "no person" in person(layer, "nobody", roster) and "no contract" in vendor(layer, "nobody", {})
    resolved = {"vendors": [{"uei": "U1", "spellings": ["ACME & CO", "ACME AND CO"]}], "spelling_to_uei": {"ACME & CO": "U1", "ACME AND CO": "U1"}}
    assert "ACME & CO: 1 contract(s)" in vendor(layer, "acme and co", resolved), "a spelling resolves to every spelling under its UEI"
    c = cell(layer, "N-1")
    assert "sources speaking about this object" in c and "lrae" in c, c
    layer_views()
    print("pages selfcheck ok")
    return 0


def layer_views() -> None:
    orgs = {"peo": {"acronym": "PEO C4I", "name": "PEO C4I", "parent": "", "org_type": "program_executive_office"},
            "pmw": {"acronym": "PMW 101", "name": "MIDS", "parent": "peo", "org_type": "program_office"},
            "other": {"acronym": "PMW 160", "name": "Networks", "parent": "peo", "org_type": "program_office"}}
    ev = lambda i, fam, day, org, text, et="forecast_created", **kw: {"id": i, "event_type": et, "date": day, "available_by": day, "provider": fam,
                                                                      "family": fam, "org": org, "title": text, "text": text, **kw}
    events = [ev("f1", "forecast", "2025-06-19", "pmw", "MIDS WDL SF3 Radio (C) N00039-25-RFPREQ-PMA/PMW-101-0046"),
              ev("t1", "programs", "2025-04-02", "pmw", "N252-D11 Advanced Interference Mitigation for MIDS tactical data links", "sbir_topic"),
              ev("i1", "incumbent", "2025-01-01", "pmw", "Incumbent contract N0003925F4027 ends 2027-07-16: MIDS SF1 radio development", "contract_expires", vendor="L3"),
              ev("n1", "notice", "2026-08-12", "pmw", "SAM.gov presolicitation 2026-08-12: MIDS WDL SWARMM SF3 Radio", "presolicitation_posted")]
    needs = [{"key": "N00039-25-RFPREQ-PMA/PMW-101-0046", "title": "MIDS WDL SF3 Radio (C)", "owner": "PMW 101", "owner_id": "pmw"},
             {"key": "N00039-24-RFPREQ-PEO-C4I-0012", "title": "MIDS WDL SF2 Production (C)", "owner": "PEO C4I", "owner_id": "peo"}]  # MIDS and WDL recur: names
    layer = Layer({"orgs": orgs, "events": events, "needs": needs, "outcomes": []}, roster=[])
    s = layer.search("MIDS")
    assert s["statements"] == 4 and s["offices"] == {"PMW 101": 4} and s["forecast_rows_total"] == 2, s
    assert layer.search("stratospheric aerosol")["statements"] == 0
    o = layer.office("pmw 101")
    assert o["statements"] == 4 and o["vendors"] == {"L3": 1} and o["topics"][0]["id"] == "t1" and o["chain"] == ["PEO C4I"], o
    assert layer.office("PEO C4I")["children"] == 2 and layer.office("nowhere")["error"]
    c = layer.cell("N00039-25-RFPREQ-PMA/PMW-101-0046")
    assert c["families"] == ["forecast", "incumbent", "notice", "programs"] and c["stage"] == "solicitation" and "Stage:" in c["card"], c["families"]
    st = layer.cell("t1")
    assert st["id"] == "t1" and "text" in st and st["forecast_rows_total"] == 2 and layer.cell("nothing")["error"], st
    assert layer.topics("tactical data links")["topics"] == 1 and layer.neighbors("PMW 101")["siblings"] == ["PMW 160 (Networks)"]
    assert topic_status({"event_type": "sbir_topic", "text": "Pre-Release; opens 2026-09-23, closes 2026-10-21; x"}, "2026-09-02")["status"] == "pre-release"
    assert layer.neighbors("PMW")["similar"] and layer.people("PMW 101") == {"office": "PMW 101", "people": []}
    read_views(orgs)


def read_views(orgs: dict) -> None:
    orgs = {**orgs, "kt": {"acronym": "NAVWAR 2.0", "name": "Contracts", "parent": "", "org_type": "contracting_office"}}
    ev = lambda i, org, title, text="", fam="notice": {"id": i, "event_type": "rfp_released", "date": "2026-08-01", "available_by": "2026-08-01",
                                                        "provider": "sam", "family": fam, "org": org, "title": title, "text": text or title}
    events = [ev("i1", "pmw", "Incumbent contract N0003924C0001 ends 2027-01-01: RADIO SUSTAINMENT", fam="incumbent"),
              ev("i2", "other", "Incumbent contract N0003923C0002 ends 2027-01-01: TACNET ROUTERS", fam="incumbent"),
              ev("k1", "kt", "SAM.gov RFP: PEO C4I PMW 101 Radio"),
              ev("k2", "kt", "SAM.gov RFP: NETWORK SERVICES", "SAM.gov RFP; solicitation N0003925R9510; network services"),
              ev("k3", "kt", "SAM.gov notice of intent: RADIO SUSTAINMENT", "follow-on to N00039-24-C-0001"),
              ev("k4", "kt", "SAM.gov sources sought: SWARMM Radio Upgrade"),
              ev("k5", "kt", "SAM.gov RFP: PMW 101 and PMW 160 joint radio"),
              ev("k6", "kt", "SAM.gov RFI: INDUSTRY DAY"),
              ev("k7", "kt", "SAM.gov RFI: TACNET Router Refresh")]
    needs = [{"key": "R1", "title": "ADNS MAC N0003925R9510 (C)", "owner": "PMW 160", "owner_id": "other"},
             {"key": "R2", "title": "SWARMM radio lot 1 (C)", "owner": "PMW 101", "owner_id": "pmw"},
             {"key": "R3", "title": "SWARMM radio lot 2 (C)", "owner": "PMW 101", "owner_id": "pmw"}]
    layer = Layer({"orgs": orgs, "events": events, "needs": needs, "outcomes": []}, roster=[])
    read = {e["id"]: (e["org"], e["read_as"]) for e in layer.events if e.get("read_as")}
    assert read == {"k1": ("pmw", "the notice names PMW 101"), "k2": ("other", "forecast row R1 carries solicitation N0003925R9510"),
                    "k3": ("pmw", "it cites contract N00039-24-C-0001, which PMW 101 holds"),
                    "k4": ("pmw", "its title carries swarmm, a program name 2 forecast rows of PMW 101 use and no other office's do")}, read
    k1 = next(e for e in layer.events if e["id"] == "k1")
    assert place(k1, layer.orgs) == "PMW 101 (office not stated; the notice names PMW 101; filed at NAVWAR 2.0)", place(k1, layer.orgs)
    guessed = {e["id"]: [g[0] for g in e["guesses"]] for e in layer.events if e.get("guesses")}
    assert guessed == {"k7": ["other"]}, f"only a program name one office's records use guesses: {guessed}"
    k7 = next(e for e in layer.events if e["id"] == "k7")
    assert k7["org"] == "kt" and place(k7, layer.orgs) == "NAVWAR 2.0 (office not stated; guessed from the words its records share, best first: PMW 160 (tacnet))", place(k7, layer.orgs)
    assert layer.office("PMW 160")["guessed_total"] == 1 and layer.office("PMW 101")["guessed_total"] == 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

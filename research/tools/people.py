#!/usr/bin/env python3
"""People layer: every person a saved source names beside an office, as a dated observation,
merged into one person per identity, so an action can say whom the public record ties to an office.

Sources, all already on disk:
  - SAM.gov notice points of contact (data/raw/sam_notices/<id>.json): name, e-mail, primary or secondary. Their
    office is the one the notice text names (one, specific) else the contracting office the notice was posted under.
  - contact_observations.json: the LRAE forecast POC columns and release passages a reviewer read (program managers,
    executives), each with the office it was resolved to and the date it was observed.
  - remarks_events.json: the speaker of each speech, statement and conference page and the witnesses of each hearing,
    with the role as the text writes it; the Department when the text names no office.
  - news_observations.json: whoever an article states relieved whom as program manager, or was named to lead an
    office, dated by the article and placed by the office its own clause names.
  - organization_seed.json person nodes, so a person the memory already knows keeps the memory's contact id.

Merge rule: one person per e-mail, and one person for two e-mails under one name at a shared office; without an e-mail, one person per normalised name and office (ranks and
honorifics dropped, "Last, First" turned around, middle initials dropped). Two people who share a name, have no
e-mail and sit in different offices stay two people. Nobody is invented: every position points at the document.

  python research/tools/people.py build          # writes research/memory/people.json
  python research/tools/people.py show pmw:101   # whom the sources tie to an office, newest first, and the routes in

Routes come from contact_recommendations.json: per office, the requirement side (the program manager), the
acquisition side (the contracting points of contact on its forecast rows), the published channels (an office
mailbox, the small business office, an intake portal) and the portfolio executive, each resting on the
observations it names and dated by the newest of them. The small business offices small_business.py reads from the
Department of War's directory join them, and an office with none of its own, or above it, falls back to the department's.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency_layers_sql import POSITION_ROLES, role_type, uid  # noqa: E402

from agency import EVENTS as EVENTS_DIR, MEMORY, P  # noqa: E402

PEOPLE = MEMORY / "people.json"
OBSERVATIONS = MEMORY / "contact_observations.json"
RECOMMENDATIONS = MEMORY / "contact_recommendations.json"
REVIEWS = MEMORY / "review_log.json"
REMARKS = EVENTS_DIR / "remarks_events.json"
NEWS = EVENTS_DIR / "news_observations.json"
SEED = MEMORY / "organization_seed.json"
SMALL_BUSINESS = MEMORY / "small_business_offices.json"  # written by small_business.py; a layer without it has no such routes
STAFF_LISTING = P["people"]["staff_listing"]  # an agency that publishes its staff with role, office and start date
SOURCE = f"chromie-federal-buyer-map-trial/{(MEMORY / 'people.json').relative_to(ROOT).as_posix()}"
DEPARTMENT = P["agency"]["node"]

HONORIFICS = {"mr", "mrs", "ms", "dr", "hon", "the", "honorable", "capt", "cdr", "lcdr", "lt", "ltjg", "ens", "adm", "vadm",
              "radm", "rdml", "gen", "ltgen", "maj", "col", "ltcol", "sgt", "phd", "ph", "d", "jr", "sr", "ii", "iii", "iv",
              "ret", "usn", "usmc", "sc", "esq"}
EXECUTIVE = P["people"]["executive"]
# A SAM.gov point of contact is the contracting shop's named contact; the schema has no finer word for it.
POC_ROLE = "contract_specialist"
# The read text gets a lower confidence than a structured field: the model named the person, a lint checked the span.
CONFIDENCE = {"sam_gov_site_api": "0.90", "dhs_apfs_forecast": "0.90", "contact_observations": "0.90", "organization_seed": "0.90", "agency_staff_listing": "0.90",
              "navy_mil_speeches": "0.70", "house_committee_repository": "0.70", "conference_pages_exa": "0.60", "news_articles": "0.80",
              "darpa_site": "0.70"}
REMARKS_PROVIDER = P["people"].get("remarks_providers") or P["remarks"]["providers"]
CONFIRMED = timedelta(days=183)  # an official statement this recent confirms a post today
RECENT = timedelta(days=730)  # a post dated within this is recently observed, never current; older is history
# A government publication states a post; a news story or a conference page only reports it, and a profile is self-stated
OFFICIAL = {"agency_staff_listing", "sam_gov_site_api", "contact_observations", "organization_seed", "program_listing",
            *(v for k, v in {**P["remarks"]["providers"], **REMARKS_PROVIDER}.items() if k != "conference"),
            *(P.get("forecast") or {}).get("providers", {}).values()}
KEEP_EMAILS = P["people"].get("emails", True)  # a profile that keeps names only merges by address and writes none
STAFF_BASE_URL = P["people"].get("staff_base_url") or ""


TITLE_WORDS = {"contract", "contracting", "contracts", "specialist", "officer", "manager", "director", "deputy", "assistant", "chief",
               "head", "lead", "buyer", "analyst", "engineer", "program", "procurement", "purchasing", "agent", "representative",
               "coordinator", "administrator", "branch", "division", "code", "pco", "aco", "cor", "ph", "phd", "jr", "sr", "ii", "iii",
               "usn", "usmc", "ret", "ses"}  # words that make what follows a comma a title or suffix, not a given name
# A name made only of these is a mailbox's role ("BAA Coordinator", DARPA's one per program), not a person's name.
ROLE_WORDS = TITLE_WORDS | {"baa", "solicitation", "team", "desk", "help", "mailbox", "inbox", "office", "support", "sbir", "sttr",
                            "general", "questions", "info", "information", "contact", "contacts", "point", "of", "the", "and", "for"}


def person_name(name: str) -> str:
    """The name without what a form appends to it: 'Kerry Payne (Contract Specialist)' and 'Frederick Mitchell, Contract
    Specialist' give the name; 'Marsh, Stephanie L.' stays, since one word before the comma is a surname; a two-word
    surname written first ('ST DENIS, DANIEL') is turned around, since no title follows its comma."""
    name = " ".join(re.sub(r"\(.*?\)", " ", name or "").split())
    head, comma, tail = name.partition(",")
    if not comma or len(head.split()) < 2:
        return name
    return head.strip() if not tail.strip() or names_office(tail) else f"{tail.strip()} {head.strip()}"


def names_office(tail: str) -> bool:
    """True when what follows the comma is a title or an office, not a given name: a title word, a slash or a digit
    ('DARPA/BTO', 'PMW 160'), the agency's own short name or an office code as the profile writes it."""
    words = set(re.findall(r"[a-z]+", tail.lower()))
    return bool(words & TITLE_WORDS or "/" in tail or re.search(r"\d", tail) or P["short"].removeprefix("U.S. ").lower() in words
                or re.search(P["reading"]["office_code_re"], tail))


def is_person(name: str) -> bool:
    """A name that can stand for one person across e-mail addresses: two words or more, none a title word. 'BAA
    Coordinator' or 'Solicitation Coordinator' is a role a mailbox carries, and each address is its own contact."""
    tokens = norm_name(name).split()
    return "@" not in name and len(tokens) >= 2 and not set(tokens) & TITLE_WORDS  # an address written as the name is no name


def norm_name(name: str) -> str:
    """'CAPT Raphael R. Castillejo' and 'Castillejo, Raphael' both give 'raphael castillejo'."""
    name = person_name(name)
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first} {last}"
    tokens = [t for t in re.split(r"[^a-z]+", name.lower()) if t and t not in HONORIFICS and len(t) > 1]
    return " ".join(tokens)


def role_of(raw: str) -> str:
    """The schema's position vocabulary; a department executive is an acquisition leader there."""
    text = (raw or "").lower()
    if any(k in text for k in EXECUTIVE) and role_type(raw) == "other":
        return "acquisition_leader"
    return role_type(raw)


def position(office: str, role: str, raw_title: str, observed_at: str, source: str, ref: str, url: str, context: str = "") -> dict:
    assert role in POSITION_ROLES, role
    return {"office": office, "org": uid("org", office), "role_type": role, "raw_title": (raw_title or "")[:200], "observed_at": observed_at[:10],
            "source": source, "source_ref": ref, "source_url": url, "confidence": CONFIDENCE.get(source, "0.60"), "context": context[:160]}


# ------------------------------------------------------------------ sources

def observed_role(row: dict) -> str:
    """The forecast sheets name a contracting or secondary point of contact; otherwise read the title."""
    role = row.get("role_type") or ""
    if role.endswith("_poc"):
        return POC_ROLE
    return role if role in POSITION_ROLES else role_of(row.get("role_as_written"))


def sam_contacts() -> list[tuple[str, str, dict]]:
    """(name, email, position) per point of contact on every saved notice detail."""
    from trace import NOTICES, SAM_ORG_NODES, SAM_VIEW, match_context, notice_detail, resolve_offices, specific_offices  # noqa: E402
    parents = match_context()["parents"]
    out = []
    for path in sorted(NOTICES.glob("*.json")) if NOTICES.exists() else []:
        if path.name.endswith(".resources.json") or path.name.startswith(("._", "search_")):
            continue
        d = notice_detail(path.stem)
        if not d or not d["posted"]:
            continue
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        pocs = (raw.get("data2") or raw.get("data") or {}).get("pointOfContact") or []
        named = specific_offices(resolve_offices(d["text"]), parents)
        office = named[0] if len(named) == 1 else SAM_ORG_NODES.get(d["organization_id"])
        if not office:
            continue
        for poc in pocs:
            title = f"{poc.get('type') or 'point of'} point of contact on {d['type']} {d['solicitation'] or d['id']}"
            for name, email in split_pocs((poc.get("fullName") or "").strip(), (poc.get("email") or "").strip().lower()):
                if norm_name(name):
                    out.append((name, email, position(office, POC_ROLE, title, d["posted"], "sam_gov_site_api", d["id"],
                                                      SAM_VIEW.format(d["id"]), d["title"])))
    return out


LEGACY_POC = re.compile(r"^Point of Contact\s*-\s*", re.I)


def split_pocs(full: str, email: str) -> list[tuple[str, str]]:
    """A notice carried over from the old system writes every contact in one field, 'Point of Contact - Name, Title,
    phone; Name, Title, phone', then a mailto link. One (name, e-mail) per contact; an address goes to the contact
    whose surname it carries."""
    if not LEGACY_POC.match(full):
        return [(full, email)]
    mails = [m.lower() for m in re.findall(r"mailto:([^\"'>\s]+)", full)] + ([email] if email else [])
    out = []
    for part in LEGACY_POC.sub("", full).split("\n", 1)[0].split(";"):
        name = part.split(",", 1)[0].strip()
        surname = (norm_name(name).split() or [""])[-1]
        out.append((name, next((m for m in mails if surname and surname in m.split("@")[0]), "")))
    return out


def forecast_contacts(packs: list[Path] | None = None) -> list[tuple[str, str, dict]]:
    """The requirements contact and the alternate contact a forecast record names (the DHS forecast prints them, and
    only their names are kept), placed in the office the record's requirement resolved to and dated by the newest
    release that still names them: the agency's live forecast states them as of that day."""
    if packs is None:
        from agency_layers_sql import RELEASES  # noqa: E402
        packs = [ROOT / "datapack" / r for r in RELEASES]
    provider = next(iter((P.get("forecast") or {}).get("providers", {}).values()), "forecast")
    out, seen = [], set()
    for pack in sorted(packs, key=lambda x: x.name, reverse=True):
        day = re.search(r"\d{4}-\d{2}-\d{2}$", pack.name)
        if not day or not (pack / "rows_raw.csv").exists() or not (pack / "rows_classified.csv").exists():
            continue
        with (pack / "rows_classified.csv").open(newline="", encoding="utf-8") as handle:
            office_of = {r["row_number"]: r["office_id"] for r in csv.DictReader(handle) if r["include_decision"] == "included" and r["office_id"]}
        with (pack / "rows_raw.csv").open(newline="", encoding="utf-8") as handle:
            for r in csv.DictReader(handle):
                for field, label in (("requirement_contact", "requirements contact"), ("alternate_contact", "alternate requirements contact")):
                    name, office = (r.get(field) or "").strip(), office_of.get(r["row_number"])
                    if not office or not norm_name(name) or (r["pid"], field) in seen:
                        continue
                    seen.add((r["pid"], field))
                    title = f"{label} on forecast record {r['pid']}" + (f" (published {r['published']})" if r.get("published") else "")
                    out.append((name, "", position(office, "program_staff", title, day.group(0), provider, r["pid"], r.get("url") or "",
                                                   r.get("requirement_title") or "")))
    return out


def observed_contacts() -> list[tuple[str, str, dict]]:
    if not OBSERVATIONS.exists():
        return []
    out = []
    for row in json.loads(OBSERVATIONS.read_text(encoding="utf-8")):
        if row.get("kind") != "person" or not row.get("office_id_as_resolved") or not row.get("observed_at"):
            continue
        if row.get("generator"):
            # Written by contact_routes.py from the staff listing and the notice contacts, which this tool already
            # reads at first hand; counting them again would make one listing look like two sources.
            continue
        channel = (row.get("channel_as_written") or "").strip().lower()
        out.append((row["name"], channel if "@" in channel else "",
                    position(row["office_id_as_resolved"], observed_role(row), row.get("role_as_written") or "",
                             row["observed_at"], "contact_observations", row["id"], row.get("source_url") or "", row.get("passage") or "")))
    return out


def remarks_people() -> list[tuple[str, str, dict]]:
    """The speaker of each document and the witnesses of each hearing; office from the events when one office recurs."""
    if not REMARKS.exists():
        return []
    from trace import resolve_offices  # noqa: E402
    out = []
    for doc in json.loads(REMARKS.read_text(encoding="utf-8"))["documents"]:
        if not doc.get("issued"):
            continue
        source = REMARKS_PROVIDER.get(doc["kind"], REMARKS_PROVIDER.get("speech", "other"))
        orgs = Counter(o for e in doc.get("events") or [] for o in (e.get("organizations") or []) if o != DEPARTMENT)
        office = orgs.most_common(1)[0][0] if len(orgs) == 1 else DEPARTMENT
        people = []
        if doc.get("speaker_name"):
            people.append((doc["speaker_name"], doc.get("speaker_role") or "", f"spoke: {doc['title']}", doc["url"]))
        # The witness list comes from the hearing page, not from this statement, so the hearing page is what names them.
        hearing = doc.get("hearing_url") or doc["url"]
        for w in doc.get("witnesses") or []:
            if isinstance(w, dict) and w.get("name"):
                people.append((w["name"], w.get("position") or "", f"witness: {doc['title']}", hearing))
        for name, role, context, url in people:
            if not norm_name(name):
                continue
            named = [o["office"] for o in resolve_offices(role) if not o["former"]]
            where = named[0] if len(named) == 1 else office
            out.append((name, "", position(where, role_of(role), role, doc["issued"], source, url, url, context)))
    return out


def staff_people() -> list[tuple[str, str, dict]]:
    """An agency that publishes its staff: each record's role and office as the listing states them, dated by the
    retrieval, with the start date the listing states kept as context. One record per person; the listing repeats a
    person per research topic. A role is a stated role, not authority."""
    if not STAFF_LISTING:
        return []
    import html
    from lrae_package import manifest_rows, saved  # noqa: E402
    row = saved(manifest_rows(), lambda r: r.get("url") == STAFF_LISTING)
    if not row or not (ROOT / row["path"]).exists():
        return []
    seed = json.loads(SEED.read_text(encoding="utf-8")) if SEED.exists() else {"nodes": []}
    by_name = {}
    for n in seed["nodes"]:
        if n["type"] != "person":
            for text in [n["name"]] + [a["text"] for a in n.get("aliases", [])]:
                by_name.setdefault(text.lower(), n["id"])
    out, seen = [], set()
    for rec in json.loads((ROOT / row["path"]).read_text(encoding="utf-8")):
        key = rec.get("view_node") or rec.get("nid")
        if key in seen:
            continue
        seen.add(key)
        name = " ".join(html.unescape(str(rec.get(k) or "")).strip() for k in ("field_first_name", "field_last_name")).strip()
        role = html.unescape(str(rec.get("field_role") or "")).strip()
        office_name = html.unescape(str(rec.get("field_taxonomy_office") or "")).strip()
        if not norm_name(name) or not role:
            continue
        office = by_name.get(office_name.lower(), DEPARTMENT)
        mapped = "acquisition_leader" if role.lower() == "director" else role_of(role)
        start = str(rec.get("field_start_date__raw") or "")[:10]
        context = f"{role}, {office_name}" + (f"; start date {start} as listed" if start else "")
        url = STAFF_BASE_URL + str(rec.get("view_node") or "")
        out.append((name, "", position(office, mapped, role, row["retrieved_at"][:10], "agency_staff_listing", str(rec.get("nid") or key), url, context)))
    return out


def news_people() -> list[tuple[str, str, dict]]:
    """Whoever an article states took charge of an office, dated by the article; a change no office places stays in the article."""
    if not NEWS.exists():
        return []
    out = []
    for article in json.loads(NEWS.read_text(encoding="utf-8"))["articles"]:
        for change in article.get("leadership") or []:
            if article.get("published") and change["office"]:
                out.append((change["name"], "", position(change["office"], role_of(change["role_as_written"]), change["role_as_written"],
                                                          article["published"], "news_articles", article["id"], article["url"], change["passage"])))
    return out


def unplaced_changes() -> list[dict]:
    """The changes of charge the articles state for an office the memory does not know, so the gap is a row and not a silence."""
    if not NEWS.exists():
        return []
    return [{"name": c["name"], "role_as_written": c["role_as_written"], "published": a.get("published", ""), "url": a["url"], "passage": c["passage"]}
            for a in json.loads(NEWS.read_text(encoding="utf-8"))["articles"] for c in a.get("leadership") or [] if not c["office"]]


def seed_people() -> dict[str, dict]:
    """Normalised name -> {seed id, name, offices} for the persons the organization memory already holds."""
    if not SEED.exists():
        return {}
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    people = {n["id"]: n for n in seed["nodes"] if n["type"] == "person"}
    offices: dict[str, set] = {}
    for rel in seed["relationships"]:
        if rel["type"] == "leads" and rel["from"] in people:
            offices.setdefault(rel["from"], set()).add(rel["to"])
    return {norm_name(n["name"]): {"seed_id": nid, "name": n["name"], "offices": sorted(offices.get(nid, ()))} for nid, n in people.items()}


# ------------------------------------------------------------------ merge

def merge(rows: list[tuple[str, str, dict]], seed: dict[str, dict] | None = None) -> list[dict]:
    seed = seed or {}
    by_key: dict[str, dict] = {}
    for name, email, pos in rows:
        normal = norm_name(name)
        known = seed.get(normal)
        if known and (not known["offices"] or pos["office"] in known["offices"] or pos["office"] == DEPARTMENT):
            key = f"seed:{known['seed_id']}"
        elif email:
            key = f"email:{email}"
        else:
            key = f"name:{normal}:{pos['office']}"
        person = by_key.setdefault(key, {"key": key, "id": uid("person", key), "seed_id": known["seed_id"] if key.startswith("seed:") else None,
                                         "names": Counter(), "emails": set(), "positions": []})
        person["names"][name.strip()] += 1
        if email:
            person["emails"].add(email)
        person["positions"].append(pos)
    # One person under two e-mails when the name is the same and an office is shared: the move from navy.mil to
    # us.navy.mil gave staff a second address. Name-only rows stay apart by office as the rule says, and a role
    # written where a name goes ('BAA Coordinator' on a program mailbox) never joins two addresses into one person.
    clusters: dict[str, list[dict]] = {}
    for key in sorted(by_key):
        person = by_key[key]
        first = person["names"].most_common(1)[0][0]
        if not key.startswith("email:") or not is_person(first) or set(norm_name(first).split()) <= ROLE_WORDS:
            continue  # a role mailbox under one title, or an address written as the name, is as many contacts as addresses
        offices = {p["office"] for p in person["positions"]}
        group = clusters.setdefault(norm_name(person["names"].most_common(1)[0][0]), [])
        into = [c for c in group if c["offices"] & offices]
        if not into:
            group.append({"person": person, "offices": offices})
            continue
        keep = into[0]
        for other in [person] + [c["person"] for c in into[1:]]:
            keep["person"]["names"].update(other["names"])
            keep["person"]["emails"] |= other["emails"]
            keep["person"]["positions"] += other["positions"]
            keep["offices"] |= {p["office"] for p in other["positions"]}
            del by_key[other["key"]]
        group[:] = [c for c in group if c not in into[1:]]
    out = []
    for person in by_key.values():
        positions = sorted(person["positions"], key=lambda p: (p["observed_at"], p["source_ref"]), reverse=True)
        seen, unique = set(), []
        for p in positions:
            k = (p["office"], p["role_type"], p["source"], p["source_ref"])
            if k not in seen:
                seen.add(k)
                unique.append(p)
        key = person["key"] if KEEP_EMAILS or not person["key"].startswith("email:") else \
            "email:" + hashlib.sha256(person["key"].encode()).hexdigest()[:16]  # the address merged the rows; it is not written
        out.append({"id": person["id"], "key": key, "seed_id": person["seed_id"],
                    "name": person_name(max(person["names"], key=lambda n: ("," not in person_name(n), person["names"][n], len(n)))),
                    "emails": sorted(person["emails"]) if KEEP_EMAILS else [], "positions": unique,
                    "offices": sorted({p["office"] for p in unique}), "last_seen": unique[0]["observed_at"]})
    return sorted(out, key=lambda p: (p["last_seen"], p["name"]), reverse=True)


def build(argv: list[str]) -> int:
    rows = sam_contacts() + forecast_contacts() + observed_contacts() + remarks_people() + news_people() + staff_people()
    rows = [r for r in rows if KEEP_EMAILS or "@" not in r[0]]  # an address written as the name is a mailbox, not a person
    people = merge(rows, seed_people())
    payload = {"source": SOURCE, "observations": len(rows), "people": len(people),
               "by_source": dict(Counter(p["source"] for r in rows for p in [r[2]]).most_common()),
               "by_role": dict(Counter(p["role_type"] for person in people for p in person["positions"]).most_common()),
               "unplaced_changes": unplaced_changes(), "rows": people}
    PEOPLE.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(rows)} observation(s) -> {len(people)} people; by source {payload['by_source']}; by role {payload['by_role']}; "
          f"{len(payload['unplaced_changes'])} change(s) of charge name an office the memory does not know")
    return 0


# Who leads before who staffs: one staff listing dates every name the same day, so the leaders must not sort after the alphabet.
ROLE_ORDER = {r: i for i, r in enumerate(("acquisition_leader", "contracting_leader", "program_manager", "deputy_program_manager", "technical_lead"))}


def standing(claims: list[dict], as_of: str) -> dict:
    """Whether the record confirms a post today or only saw it. confirmed_current: an official source stated it within
    CONFIRMED of as_of. recently_observed: some source dated it within RECENT. history: older. Each claim carries source,
    observed_at and source_url; the claim that decides comes back with the status."""
    end = date.fromisoformat(as_of[:10])
    dated = sorted((c for c in claims if (c.get("observed_at") or "")[:10] and c["observed_at"][:10] <= as_of[:10]),
                   key=lambda c: c["observed_at"], reverse=True)
    age = lambda c: end - date.fromisoformat(c["observed_at"][:10])  # noqa: E731
    status, c = next((("confirmed_current", c) for c in dated if c.get("source") in OFFICIAL and age(c) <= CONFIRMED), None) \
        or next((("recently_observed", c) for c in dated if age(c) <= RECENT), None) or ("history", dated[0] if dated else {})
    return {"status": status, "source": c.get("source", ""), "observed_at": (c.get("observed_at") or "")[:10], "source_url": c.get("source_url", "")}


def post_standings(claims: list[dict], as_of: str) -> list[dict]:
    """A person's standing per post, one entry per office and role. A claim confirms only the post it names, so a recent
    official record at one office never confirms the person's role at another, nor another role at the same office."""
    posts: dict[tuple[str, str], list[dict]] = {}
    for c in claims:
        posts.setdefault((c.get("office") or "", c.get("role_type") or ""), []).append(c)
    return [{"office": office, "role": role, **standing(cs, as_of)} for (office, role), cs in sorted(posts.items())]


def contacts_for(org_ids: list[str], people: list[dict], as_of: str | None = None, limit: int | None = 3) -> list[dict]:
    """Whom the record ties to any of these organization ids (most specific first, then newest, then leaders first),
    observed by as_of; every one when limit is None."""
    rank = {org: i for i, org in enumerate(org_ids)}
    rows = []
    for person in people:
        fits = [(rank[p["org"]], -int(p["observed_at"].replace("-", "")), ROLE_ORDER.get(p["role_type"], len(ROLE_ORDER)), p)
                for p in person["positions"] if p["org"] in rank and (not as_of or p["observed_at"] <= as_of)]
        if fits:
            best = min(fits, key=lambda r: r[:3])
            rows.append((*best[:3], person, best[3]))
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]["name"]))
    return [{"name": person["name"], "role": p["role_type"], "title": p["raw_title"], "office": p["office"], "observed_at": p["observed_at"],
             "source": p["source"], "source_ref": p["source_ref"], "source_url": p["source_url"], "email": (person["emails"] or [""])[0],
             "standing": standing([p], as_of or date.today().isoformat())["status"]}
            for _, _, _, person, p in rows[:limit]]


def own_contacts(event: dict, people: list[dict], as_of: str | None = None) -> list[dict]:
    """Whom the record ties to this statement itself: a position observed on the same saved notice (its source_ref is the
    notice id the statement's record or page names) or whose title or context carries the statement's solicitation
    number. Contacts of the office the statement is filed at are another question; they answer for other solicitations."""
    text = f"{event.get('text', '')} {event.get('url', '')} {event.get('source', '')}"
    ids = set(re.findall(r"/opp/([0-9a-f]{32})/|/opportunities/([0-9a-f]{32})", text))
    ids = {x for pair in ids for x in pair if x}
    sol = re.search(r"solicitation ([A-Z0-9][A-Z0-9_.-]{4,})", event.get("text", ""))
    number = re.sub(r"[\s-]", "", sol.group(1).upper()) if sol else ""
    rows = []
    for person in people:
        fits = [p for p in person["positions"] if (not as_of or p["observed_at"] <= as_of)
                and (p.get("source_ref") in ids or (number and number in re.sub(r"[\s-]", "", f"{p.get('raw_title', '')} {p.get('context', '')}".upper())))]
        if fits:
            p = max(fits, key=lambda p: p["observed_at"])
            rows.append({"name": person["name"], "role": p["role_type"], "title": p["raw_title"], "office": p["office"], "observed_at": p["observed_at"],
                         "source": p["source"], "source_ref": p["source_ref"], "source_url": p["source_url"], "email": (person["emails"] or [""])[0],
                         "standing": standing([p], as_of or date.today().isoformat())["status"], "basis": "named on this statement's own record"})
    return sorted(rows, key=lambda r: (r["observed_at"], r["name"]), reverse=True)


SMALL_BUSINESS_ROUTE = "small_business_office"
ROUTE_ORDER = ("program_manager", "contracting_poc", "office_channel", SMALL_BUSINESS_ROUTE, "industry_intake_channel", "executive")
SIDE = {"program_manager": "requirement", "contracting_poc": "acquisition", "office_channel": "channel",
        SMALL_BUSINESS_ROUTE: "small business office", "industry_intake_channel": "channel", "executive": "executive"}


def load_routes() -> list[dict]:
    """Every recommended route with the date and the source of the observations it rests on, and whether each of
    those observations was checked against the saved file it cites."""
    # the recommendation and observation files are hand-written for the Navy only; an agency without them still has its small business offices
    observed = {o["id"]: o for o in json.loads(OBSERVATIONS.read_text(encoding="utf-8"))} if OBSERVATIONS.exists() else {}
    reviews = json.loads(REVIEWS.read_text(encoding="utf-8")) if REVIEWS.exists() else []
    checked = {r["target"] for r in reviews if r["outcome"] in ("confirmed", "corrected")}
    rows = []
    for rec in json.loads(RECOMMENDATIONS.read_text(encoding="utf-8")) if RECOMMENDATIONS.exists() else []:
        basis = [observed[i] for i in rec["contact_observation_ids"] if i in observed]
        if not basis:
            continue
        newest = max(basis, key=lambda o: o["observed_at"])
        rows.append({"org": uid("org", rec["office_id"]), "office_id": rec["office_id"], "route": rec["route_type"],
                     "side": SIDE.get(rec["route_type"], "other"), "recommendation": rec["recommendation"],
                     "confidence": rec["source_confidence"], "currency": rec["currency_confidence"],
                     "review_status": rec["review_status"], "observed_at": newest["observed_at"], "source_url": newest["source_url"],
                     "checked": all(o["id"] in checked for o in basis)})
    for sb in json.loads(SMALL_BUSINESS.read_text(encoding="utf-8")) if SMALL_BUSINESS.exists() else []:
        rows.append({"org": uid("org", sb["office_id"]), "office_id": sb["office_id"], "route": SMALL_BUSINESS_ROUTE,
                     "side": SIDE[SMALL_BUSINESS_ROUTE], "recommendation": "; ".join([f"{sb['name']}, {sb['url']}", *sb["lines"]]),
                     "confidence": "high", "currency": "unknown", "review_status": "draft", "observed_at": sb["observed_at"],
                     "source_url": sb["url"] if sb["page_saved"] else sb["source_url"], "checked": False})
    return rows


def routes_for(org_ids: list[str], routes: list[dict], as_of: str | None = None) -> list[dict]:
    """The routes into the most specific of these organizations that has any, observed by as_of: the program
    manager and the contracting side of the office itself before its parent's channels."""
    rank = {org: i for i, org in enumerate(org_ids)}
    fits = [r for r in routes if r["org"] in rank and (not as_of or r["observed_at"] <= as_of)]
    if not any(r["route"] == SMALL_BUSINESS_ROUTE for r in fits):  # any office can start at the department's small business office
        fits += [r for r in routes if r["office_id"] == DEPARTMENT and r["route"] == SMALL_BUSINESS_ROUTE and (not as_of or r["observed_at"] <= as_of)]
    order = {route: i for i, route in enumerate(ROUTE_ORDER)}
    fits.sort(key=lambda r: (rank.get(r["org"], len(rank)), order.get(r["route"], len(order)), r["recommendation"]))
    return [{k: v for k, v in r.items() if k != "org"} for r in fits]


def first_routes(routes: list[dict], n: int) -> list[dict]:
    """The first n routes, and the small business office after them when it is not among them: a small company's first
    contact at a command."""
    return routes[:n] + [r for r in routes[n:] if r["route"] == SMALL_BUSINESS_ROUTE][:1]


def show(argv: list[str]) -> int:
    office = argv[0] if argv else "pmw:101"
    people = json.loads(PEOPLE.read_text(encoding="utf-8"))["rows"]
    for c in contacts_for([uid("org", office)], people, limit=20):
        print(f"{c['observed_at']}  {c['name']:32} {c['role']:20} {c['source']:26} {c['source_ref'][:40]}")
    for r in routes_for([uid("org", office)], load_routes()):
        print(f"{r['observed_at']}  {r['side']:12} {r['route']:24} {r['recommendation'][:90]}")
    return 0


def selfcheck() -> int:
    with tempfile.TemporaryDirectory() as tmp:  # two releases of one record: the newer one dates the statement
        for day, contact in (("2026-09-26", "Jared Slizofski"), ("2026-09-29", "Jared Slizofski")):
            pack = Path(tmp) / f"x_{day}"
            pack.mkdir()
            (pack / "rows_classified.csv").write_text("row_number,office_id,include_decision\n2,program:cg-cyber,included\n", encoding="utf-8")
            (pack / "rows_raw.csv").write_text("row_number,pid,published,requirement_contact,alternate_contact,url,requirement_title\n"
                                               f"2,F1,09/01/2026,Ann Lee,{contact},https://r/1,Cyber tools\n", encoding="utf-8")
        got = forecast_contacts(sorted(Path(tmp).iterdir()))
    assert [(n, q["role_type"], q["observed_at"], q["office"]) for n, _, q in got] == [
        ("Ann Lee", "program_staff", "2026-09-29", "program:cg-cyber"), ("Jared Slizofski", "program_staff", "2026-09-29", "program:cg-cyber")], got
    assert got[1][2]["raw_title"] == "alternate requirements contact on forecast record F1 (published 09/01/2026)" and got[0][1] == ""
    assert norm_name("CAPT Raphael R. Castillejo") == norm_name("Castillejo, Raphael") == "raphael castillejo"
    assert norm_name("The Honorable Hung Cao") == "hung cao" and norm_name("Mr. Eric Andalis") == "eric andalis"
    assert norm_name("Ashley, Megan") == "megan ashley" and norm_name("") == "" and norm_name("A.") == ""
    assert norm_name("Frederick Mitchell, Contract Specialist") == norm_name("Frederick Mitchell") == "frederick mitchell"
    assert person_name("Kerry Payne (Contract Specialist)") == "Kerry Payne" and person_name("Marsh, Stephanie L.") == "Marsh, Stephanie L."
    assert norm_name("ST DENIS, DANIEL") == norm_name("Daniel St. Denis") == "daniel st denis" and person_name("De Vera, Jennifer") == "Jennifer De Vera"
    assert person_name("William H. Luebke, Ph.D.") == "William H. Luebke", "a suffix after the comma is no given name"
    assert role_of("Chief of Naval Operations") == "acquisition_leader" and role_of("Program Manager, PMW 150") == "program_manager"
    assert role_of("Deputy Program Manager") == "deputy_program_manager" and role_of("liaison") == "other"
    assert role_of("portfolio acquisition executive") == "acquisition_leader"
    assert role_of("Contract Specialist") == "contract_specialist" and role_of("") == "other"
    pos = lambda office, ref, day="2026-01-01", src="sam_gov_site_api": position(office, POC_ROLE, "poc", day, src, ref, "u")  # noqa: E731
    people = merge([("Megan Ashley", "megan@navy.mil", pos("pmw:101", "n1")), ("Ashley, Megan", "megan@navy.mil", pos("peo:c4i", "n2", "2026-02-01")),
                    ("Jim Day", "", pos("pmw:120", "n3")), ("Jim Day", "", pos("pmw:130", "n4")),
                    ("Mr. Eric Andalis", "", pos("pmw:760", "r1", "2025-08-19", "contact_observations"))],
                   {"eric andalis": {"seed_id": "person:andalis", "name": "Eric Andalis", "offices": ["pmw:760"]}})
    by = {p["name"]: p for p in people}
    assert len(people) == 4, [p["key"] for p in people]                       # e-mail merges two spellings; two Jim Days stay apart
    assert by["Megan Ashley"]["offices"] == ["peo:c4i", "pmw:101"] and by["Megan Ashley"]["last_seen"] == "2026-02-01"
    assert sum(p["name"] == "Jim Day" for p in people) == 2
    assert by["Mr. Eric Andalis"]["seed_id"] == "person:andalis" and by["Mr. Eric Andalis"]["id"] == uid("person", "seed:person:andalis")
    assert len(merge([("BAA Coordinator", "a@darpa.mil", pos("office:dso", "a")), ("BAA Coordinator", "b@darpa.mil", pos("office:dso", "b"))])) == 2, \
        "two program mailboxes under one role stay two contacts"
    moved = merge([("Kimberly Ellis", "kimberly.ellis3@navy.mil", pos("pmw:740", "a")), ("Kimberly Ellis", "kimberly.a.ellis10.civ@us.navy.mil", pos("pmw:740", "b")),
                   ("Kimberly Ellis", "kimberly.ellis@nrl.navy.mil", pos("center:nrl", "c"))])
    assert len(moved) == 2 and moved[0]["emails"] == ["kimberly.a.ellis10.civ@us.navy.mil", "kimberly.ellis3@navy.mil"], moved  # one office: one person
    assert person_name("Dr. Pedro Irazoqui, DARPA/BTO") == "Dr. Pedro Irazoqui" and person_name("Jane Roe, PMW 160") == "Jane Roe", "an office after the comma is no given name"
    boxes = merge([("BAA Coordinator", "a3ml@darpa.mil", pos("office:bto", "x")), ("BAA Coordinator", "abc@darpa.mil", pos("office:bto", "y"))])
    assert len(boxes) == 2 and not is_person("BAA Coordinator") and not is_person("DARPA-SN-23-85@darpa.mil") and is_person("Kimberly Ellis"), \
        "a role or an address on two mailboxes is two contacts"
    got = contacts_for([uid("org", "pmw:101"), uid("org", "peo:c4i")], people)
    assert [c["name"] for c in got] == ["Megan Ashley"] and got[0]["office"] == "pmw:101" and got[0]["email"] == "megan@navy.mil"
    assert contacts_for([uid("org", "pmw:101")], people, as_of="2025-12-31") == []
    route = lambda office, kind, day: {"org": uid("org", office), "office_id": office, "route": kind, "side": SIDE[kind],
                                       "recommendation": kind, "confidence": "high", "currency": "high", "review_status": "draft",
                                       "observed_at": day, "source_url": "u"}
    routes = [route("command:navwar", "industry_intake_channel", "2026-09-16"), route("pmw:160", "contracting_poc", "2025-06-01"),
              route("pmw:160", "program_manager", "2025-01-01"), route("pmw:170", "program_manager", "2025-01-01")]
    chain_ = [uid("org", "pmw:160"), uid("org", "peo:c4i"), uid("org", "command:navwar")]
    assert [r["route"] for r in routes_for(chain_, routes)] == ["program_manager", "contracting_poc", "industry_intake_channel"]
    assert [r["route"] for r in routes_for(chain_, routes, "2025-03-01")] == ["program_manager"]  # nothing observed later
    routes += [route(DEPARTMENT, SMALL_BUSINESS_ROUTE, "2026-09-24")]
    assert [r["route"] for r in routes_for(chain_, routes)][-1] == SMALL_BUSINESS_ROUTE        # the department's, after the chain's own
    assert [r["route"] for r in first_routes(routes_for(chain_, routes), 2)] == ["program_manager", "contracting_poc", SMALL_BUSINESS_ROUTE]
    own = routes_for([uid("org", "command:navwar")], routes + [route("command:navwar", SMALL_BUSINESS_ROUTE, "2026-09-16")])
    assert [r["office_id"] for r in own if r["route"] == SMALL_BUSINESS_ROUTE] == ["command:navwar"]  # a command's own office replaces it
    legacy = ('Point of Contact - Clayton R Thomas, Contract Specialist,  619-524-7199; Stephen R Beckner, Contracting Officer, '
              '619-524-7389\n\n<a href="mailto:clayton.r.thomas@navy.mil">Contract Specialist</a>')
    assert split_pocs(legacy, "") == [("Clayton R Thomas", "clayton.r.thomas@navy.mil"), ("Stephen R Beckner", "")]
    assert split_pocs("Megan Ashley", "megan.ashley@navy.mil") == [("Megan Ashley", "megan.ashley@navy.mil")]
    # A statement's own contacts come from its own record; the office's coordinators on other solicitations are not its people
    roster = [{"name": "Own Coordinator", "emails": ["own@x.mil"], "positions": [pos("contracting:hr0011", "aaaa" * 8) | {"raw_title": "primary point of contact on presolicitation DARPA-PA-26-10_DRAFT", "context": ""}]},
              {"name": "Other Coordinator", "emails": [], "positions": [pos("contracting:hr0011", "bbbb" * 8) | {"raw_title": "primary point of contact on solicitation DARPA-PS-26-141", "context": ""}]}]
    statement = {"text": "SAM.gov presolicitation 2026-09-24: HUBBLE presolicitation; solicitation DARPA-PA-26-10_DRAFT; record x", "url": f"https://sam.gov/opp/{'aaaa' * 8}/view"}
    assert [c["name"] for c in own_contacts(statement, roster)] == ["Own Coordinator"], own_contacts(statement, roster)
    assert own_contacts({"text": "SAM.gov presolicitation: other; solicitation DARPA-PA-26-10_DRAFT", "url": ""}, roster)[0]["name"] == "Own Coordinator", "the number alone ties"
    assert own_contacts({"text": "a remark with no solicitation", "url": ""}, roster) == []
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"build": build, "show": show}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Contact observations, route recommendations and the review log for an agency that publishes its staff and posts
its notices, written from saved sources in the shape the Navy's reviewed files have (tasks/T02), so that
`people.routes_for` reaches a requirement-side and an acquisition-side route for every office of the layer.

    python research/tools/contact_routes.py build [--check]     # writes <agency memory>/contact_observations.json,
                                                                #   contact_recommendations.json and review_log.json
    python research/tools/contact_routes.py --selfcheck

What is written, and from where:
  - an observation per office director, deputy director and program manager the agency's staff listing names
    (`P["people"]["staff_listing"]`): the role and office as listed, the start date as listed, the person's page.
    A listed role is a stated role, not authority, and the observation says so.
  - an observation per point of contact on the saved notice details, per office the notice was read to (the office
    its text names, else the organization it was posted under, as people.sam_contacts reads them): the name and
    address as the notice writes them, the notices it stands on. A point of contact answers for the solicitation,
    not for the office the notice is for, so `member_of_office` is false, as it is for the Navy's forecast contacts.
  - a recommendation per office: the office leadership (`executive`), the program managers (`program_manager`, the
    requirement side) and the points of contact seen on the office's notices in the last year, most notices first
    (`contracting_poc`, the acquisition side). Each rests on the observations it names and carries a source
    confidence (the agency's own listing and its own notices: high) and a currency confidence read from the date of
    the newest observation against the day the newest source was retrieved (a year: high; two: medium; older: low).
  - a review-log entry per observation: the fields were compared with the saved record they cite (the staff record
    by nid, the notice's point-of-contact entry by address or name), confirmed or not_found.

Every row carries `"generator": "contact_routes"`, and the tool writes only where every existing row carries it: the
Navy's files are hand-written and reviewed and are never overwritten. Dates on the drafted-by and checked-by
records are the day the newest source read was retrieved, so a rebuild from the same bytes is byte for byte the
same. A recommendation is a route to try, labelled as such; nothing here says who decides a buy.
"""

from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import MEMORY, P, ROOT  # noqa: E402
from lrae_package import manifest_rows, saved  # noqa: E402

OBSERVATIONS = MEMORY / "contact_observations.json"
RECOMMENDATIONS = MEMORY / "contact_recommendations.json"
REVIEWS = MEMORY / "review_log.json"
SEED = MEMORY / "organization_seed.json"
GENERATOR = "contact_routes"
STAFF_LISTING = P["people"].get("staff_listing")
STAFF_BASE_URL = P["people"].get("staff_base_url") or ""
EXECUTIVE_ROLES = {"office director", "deputy director", "director", "deputy office director", "chief of staff"}
MANAGER_ROLES = {"program manager", "deputy program manager"}
ROUTE_NOTE = "a listed role states who holds the position, not who decides a buy; a route is a recommendation to try"
POC_NOTE = ("a point of contact on a notice answers for that solicitation (a BAA coordinator mailbox, a contracting officer); "
            "the office the notice was read to is who the notice is for, not who the contact belongs to")


def slug(text: str) -> str:
    """Lower-cased, with runs of other characters as one hyphen; an underscore stays, so two mailboxes that differ only in
    an underscore against a hyphen stay two observations."""
    return re.sub(r"[^a-z0-9_]+", "-", (text or "").lower()).strip("-")[:60]


def clean(value) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def currency(newest: str, asof: str) -> tuple[str, str]:
    """high within a year of the newest retrieval, medium within two, low beyond; unknown when undated."""
    if not newest:
        return "unknown", "the source states no date"
    days = (date.fromisoformat(asof) - date.fromisoformat(newest[:10])).days
    if days <= 365:
        return "high", f"source dated {newest[:10]}, within a year of {asof}"
    if days <= 730:
        return "medium", f"source dated {newest[:10]}, one to two years before {asof}"
    return "low", f"source dated {newest[:10]}, more than two years before {asof}"


def office_index(seed: dict) -> dict[str, str]:
    """The seed's organization names and aliases, lower-cased, to their node ids (first name wins)."""
    by_name: dict[str, str] = {}
    for n in seed["nodes"]:
        if n["type"] == "person":
            continue
        for text in [n["name"]] + [a["text"] for a in n.get("aliases", [])]:
            by_name.setdefault(text.lower(), n["id"])
    return by_name


def office_label(seed: dict, office_id: str) -> str:
    node = next((n for n in seed["nodes"] if n["id"] == office_id), None)
    if not node:
        return office_id
    code = (node.get("codes") or {}).get("office_code")
    return f"{node['name']} ({code})" if code else node["name"]


# ------------------------------------------------------------------ the staff listing

def staff_observations(seed: dict, manifest: list[dict]) -> tuple[list[dict], list[dict], str]:
    """One observation per listed director, deputy or program manager of an office the seed holds; the review entry
    that compared the observation's fields with the record; the listing's retrieval date."""
    if not STAFF_LISTING:
        return [], [], ""
    row = saved(manifest, lambda r: r.get("url") == STAFF_LISTING)
    if not row:
        return [], [], ""
    retrieved = row["retrieved_at"][:10]
    records = json.loads((ROOT / row["path"]).read_text(encoding="utf-8"))
    by_name = office_index(seed)
    revision = f"retrieved {row['retrieved_at']}; sha256 {row['sha256'][:12]}"
    obs, checks, seen = [], [], set()
    for rec in records:
        key = rec.get("view_node") or rec.get("nid")
        if key in seen:
            continue
        seen.add(key)
        role = clean(rec.get("field_role"))
        office_name = clean(rec.get("field_taxonomy_office"))
        name = " ".join(clean(rec.get(k)) for k in ("field_first_name", "field_last_name")).strip()
        office = by_name.get(office_name.lower())
        if not office or not name or role.lower() not in EXECUTIVE_ROLES | MANAGER_ROLES:
            continue
        role_type = "executive" if role.lower() in EXECUTIVE_ROLES else "program_manager"
        start = str(rec.get("field_start_date__raw") or "")[:10]
        topics = clean(rec.get("field_research_topics"))
        passage = " | ".join(p for p in (name, role, office_name, f"start date {start}" if start else "", topics) if p)
        oid = f"cobs:staff:{office.split(':')[-1]}:{slug(name)}"
        obs.append({"id": oid, "generator": GENERATOR, "kind": "person", "name": name, "channel_as_written": "",
                    "role_as_written": role, "role_type": role_type, "office_id_as_resolved": office,
                    "office_code_as_written": office_name, "member_of_office": True, "member_of_office_note": ROUTE_NOTE,
                    "source_url": STAFF_LISTING, "source_revision": revision, "observed_at": retrieved,
                    "locators": [{"nid": str(rec.get("nid") or ""), "view_node": str(rec.get("view_node") or ""),
                                  "fields": ["field_first_name", "field_last_name", "field_role", "field_taxonomy_office",
                                             "field_start_date__raw"]}],
                    "row_count": 1, "independent_sources": 1, "passage": passage, "research_topics": topics,
                    "start_date_as_listed": start, "person_url": STAFF_BASE_URL + str(rec.get("view_node") or ""),
                    "source_statement_confidence": "high",
                    "statement_confidence_basis": "the agency's own staff listing states the role, office and start date; "
                                                  "the listing is undated, so the retrieval day is the observation date"})
        # The check: the same record, found again by nid in the saved bytes, states the same fields.
        again = next((r for r in records if str(r.get("nid")) == str(rec.get("nid"))), None)
        ok = bool(again) and all(clean(again.get(k)) == clean(rec.get(k)) for k in ("field_first_name", "field_last_name", "field_role", "field_taxonomy_office"))
        checks.append({"id": f"chk:{oid}", "generator": GENERATOR, "target": oid, "target_kind": "contact_observation",
                       "checked_against": {"source_url": STAFF_LISTING, "sha256": row["sha256"]},
                       "check": f"staff record nid {rec.get('nid')} in the saved listing states name '{name}', role '{role}' and office '{office_name}'",
                       "checked_by": {"actor": f"assistant ({GENERATOR}.py)", "on": retrieved}, "reviewed_by": None, "reviewed_on": None,
                       "outcome": "confirmed" if ok else "not_found",
                       "detail": f"found as '{name}', '{role}', '{office_name}'" + (f"; start date {start} as listed" if start else "")})
    return obs, checks, retrieved


# ------------------------------------------------------------------ the notices

def notice_observations(manifest: list[dict]) -> tuple[list[dict], list[dict], str]:
    """One observation per (office, point of contact) across the saved notice details, with every notice it stands
    on as a locator; the check compares the address or name with the notice's own pointOfContact entry."""
    from people import is_person, sam_contacts  # noqa: E402
    from trace import NOTICES, SAM_VIEW  # noqa: E402
    by_path = {r["path"]: r for r in manifest if r.get("path")}
    groups: dict[tuple[str, str], list[tuple[str, str, dict]]] = defaultdict(list)
    for name, email, pos in sam_contacts():
        groups[(pos["office"], email or name.lower())].append((name, email, pos))
    obs, checks, newest_retrieval = [], [], ""
    for (office, _), rows in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        rows.sort(key=lambda r: r[2]["observed_at"], reverse=True)
        name, email, pos = rows[0]
        newest_id = pos["source_ref"]
        path = NOTICES / f"{newest_id}.json"
        ledger = by_path.get(path.relative_to(ROOT).as_posix()) if path.exists() else None
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else {}
        pocs = (raw.get("data2") or raw.get("data") or {}).get("pointOfContact") or []
        entry = next((p for p in pocs if (email and (p.get("email") or "").strip().lower() == email)
                      or (not email and (p.get("fullName") or "").strip() == name)), None)
        written_name = (entry or {}).get("fullName") or name
        passage = " | ".join(p for p in ((entry or {}).get("fullName") or name, (entry or {}).get("email") or email) if p)
        kind = "person" if is_person(name) else "channel"
        oid = f"cobs:notice:{office.split(':')[-1]}:{slug(email or name)}"
        retrieved = (ledger or {}).get("retrieved_at", "")
        newest_retrieval = max(newest_retrieval, retrieved)
        obs.append({"id": oid, "generator": GENERATOR, "kind": kind, "name": written_name if kind == "person" else "",
                    "channel_as_written": email or written_name, "role_as_written": pos["raw_title"],
                    "role_type": "contracting_poc", "office_id_as_resolved": office, "office_code_as_written": pos.get("context", ""),
                    "member_of_office": False, "member_of_office_note": POC_NOTE,
                    "source_url": SAM_VIEW.format(newest_id),
                    "source_revision": f"notice detail {newest_id} retrieved {retrieved}; sha256 {(ledger or {}).get('sha256', '')[:12]}",
                    "observed_at": pos["observed_at"],
                    "locators": [{"notice_id": r[2]["source_ref"], "field": "pointOfContact", "posted": r[2]["observed_at"],
                                  "notice": r[2]["raw_title"]} for r in rows],
                    "row_count": len(rows), "independent_sources": 1, "passage": passage,
                    "source_statement_confidence": "high",
                    "statement_confidence_basis": "named in the point-of-contact field of the agency's own notice; repetition across "
                                                  "notices of one contracting office is one source"})
        checks.append({"id": f"chk:{oid}", "generator": GENERATOR, "target": oid, "target_kind": "contact_observation",
                       "checked_against": {"source_url": SAM_VIEW.format(newest_id), "sha256": (ledger or {}).get("sha256", "")},
                       "check": f"pointOfContact entry of notice {newest_id} carries '{email or name}'",
                       "checked_by": {"actor": f"assistant ({GENERATOR}.py)", "on": retrieved[:10] or pos["observed_at"]},
                       "reviewed_by": None, "reviewed_on": None,
                       "outcome": "confirmed" if entry else "not_found",
                       "detail": (f"found as '{passage}'; notices naming this contact for this office: {len(rows)}" if entry
                                  else f"no pointOfContact entry with '{email or name}' in the saved detail")})
    return obs, checks, newest_retrieval[:10]


# ------------------------------------------------------------------ the recommendations

def recommendations(seed: dict, obs: list[dict], asof: str) -> list[dict]:
    drafted = {"actor": f"assistant ({GENERATOR}.py)", "on": asof}
    by_office: dict[str, list[dict]] = defaultdict(list)
    for o in obs:
        by_office[o["office_id_as_resolved"]].append(o)
    out = []
    for office in sorted(by_office):
        rows = by_office[office]
        label = office_label(seed, office)
        execs = sorted([o for o in rows if o["role_type"] == "executive"], key=lambda o: (o["role_as_written"], o["name"]))
        if execs:
            newest = max(o["observed_at"] for o in execs)
            cur, basis = currency(newest, asof)
            named = ", ".join(f"{o['name']} ({o['role_as_written']}" + (f", listed from {o['start_date_as_listed']}" if o.get("start_date_as_listed") else "") + ")" for o in execs)
            out.append({"id": f"crec:{office.split(':')[-1]}:executive", "generator": GENERATOR, "office_id": office, "route_type": "executive",
                        "recommendation": f"Office leadership of {label} as the agency lists it: {named}.",
                        "contact_observation_ids": [o["id"] for o in execs], "source_confidence": "high",
                        "currency_confidence": cur, "currency_basis": basis, "competing_candidates": [], "contradictions": [],
                        "caveats": [ROUTE_NOTE], "review_status": "draft", "drafted_by": drafted, "reviewed_by": None, "reviewed_on": None})
        managers = sorted([o for o in rows if o["role_type"] == "program_manager"], key=lambda o: o["name"])
        if managers:
            newest = max(o["observed_at"] for o in managers)
            cur, basis = currency(newest, asof)
            out.append({"id": f"crec:{office.split(':')[-1]}:program_manager", "generator": GENERATOR, "office_id": office, "route_type": "program_manager",
                        "recommendation": f"Requirement-side contacts for {label}: the {len(managers)} program manager(s) the agency's staff "
                                          f"listing names under the office, each with the research topics listed; write to the one whose "
                                          f"topics match the capability (the observations carry the topics).",
                        "contact_observation_ids": [o["id"] for o in managers], "source_confidence": "high",
                        "currency_confidence": cur, "currency_basis": basis, "competing_candidates": [], "contradictions": [],
                        "caveats": [ROUTE_NOTE, "a program manager runs the programs the listing's topics name; the listing does not say which "
                                                "solicitation each one will sign"],
                        "review_status": "draft", "drafted_by": drafted, "reviewed_by": None, "reviewed_on": None})
        pocs = [o for o in rows if o["role_type"] == "contracting_poc"]
        if pocs:
            year_ago = (date.fromisoformat(asof) - timedelta(days=365)).isoformat()
            recent = [o for o in pocs if o["observed_at"] >= year_ago] or pocs
            recent.sort(key=lambda o: (-o["row_count"], o["observed_at"]), reverse=False)
            recent.sort(key=lambda o: (-o["row_count"], -int(o["observed_at"].replace("-", "")), o["id"]))
            lead, rest = recent[:3], recent[3:8]
            newest = max(o["observed_at"] for o in lead)
            cur, basis = currency(newest, asof)
            named = "; ".join(f"{o['channel_as_written']}" + (f" ({o['name']})" if o["name"] else "") + f", on {o['row_count']} notice(s), newest {o['observed_at']}" for o in lead)
            out.append({"id": f"crec:{office.split(':')[-1]}:contracting_poc", "generator": GENERATOR, "office_id": office, "route_type": "contracting_poc",
                        "recommendation": f"Acquisition-side route for {label}: the points of contact on the office's notices in the last year, "
                                          f"most notices first: {named}.",
                        "contact_observation_ids": [o["id"] for o in lead], "source_confidence": "high",
                        "currency_confidence": cur, "currency_basis": basis,
                        "competing_candidates": [{"observation_id": o["id"], "channel": o["channel_as_written"], "notices": o["row_count"], "newest": o["observed_at"]} for o in rest],
                        "contradictions": [],
                        "caveats": [POC_NOTE, "the agency names a mailbox per solicitation; write to the one the current notice names"],
                        "review_status": "draft", "drafted_by": drafted, "reviewed_by": None, "reviewed_on": None})
    return out


# ------------------------------------------------------------------ build

def own(path: Path) -> bool:
    """True when the file is absent or every row in it was written by this tool."""
    if not path.exists():
        return True
    rows = json.loads(path.read_text(encoding="utf-8"))
    return isinstance(rows, list) and all(isinstance(r, dict) and r.get("generator") == GENERATOR for r in rows)


def assemble() -> tuple[list[dict], list[dict], list[dict], str]:
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    manifest = manifest_rows()
    staff, staff_checks, staff_day = staff_observations(seed, manifest)
    notices, notice_checks, notice_day = notice_observations(manifest)
    asof = max(staff_day, notice_day)
    obs = staff + notices
    return obs, recommendations(seed, obs, asof), staff_checks + notice_checks, asof


def dumps(rows: list[dict]) -> str:
    return json.dumps(rows, indent=1, ensure_ascii=False) + "\n"


def build(argv: list[str]) -> int:
    for path in (OBSERVATIONS, RECOMMENDATIONS, REVIEWS):
        if not own(path):
            print(f"skipped: {path.relative_to(ROOT)} holds rows this tool did not write (hand-written and reviewed); nothing written")
            return 0
    if not STAFF_LISTING and not (MEMORY / "organization_seed.json").exists():
        print("skipped: the profile names no staff listing and no seed")
        return 0
    obs, recs, checks, asof = assemble()
    if not obs and not any(p.exists() for p in (OBSERVATIONS, RECOMMENDATIONS, REVIEWS)):
        print("nothing observed: the profile's staff listing and notice details are not saved yet; nothing written")
        return 0
    texts = {OBSERVATIONS: dumps(obs), RECOMMENDATIONS: dumps(recs), REVIEWS: dumps(checks)}
    if "--check" in argv:
        stale = [p.name for p, t in texts.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        if stale:
            print(f"{', '.join(stale)} differ from a fresh build; run without --check to regenerate", file=sys.stderr)
            return 1
        print(f"contact routes match a fresh build: {len(obs)} observations, {len(recs)} recommendations, {len(checks)} checks")
        return 0
    MEMORY.mkdir(parents=True, exist_ok=True)
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8")
    kinds = Counter(o["role_type"] for o in obs)
    outcomes = Counter(c["outcome"] for c in checks)
    print(f"{len(obs)} observation(s) ({dict(kinds)}) -> {len(recs)} recommendation(s) for {len({r['office_id'] for r in recs})} office(s); "
          f"{len(checks)} check(s) {dict(outcomes)}; dated {asof}")
    return 0


def selfcheck() -> int:
    assert currency("2026-09-01", "2026-09-25")[0] == "high" and currency("2025-01-01", "2026-09-25")[0] == "medium"
    assert currency("2023-01-01", "2026-09-25")[0] == "low" and currency("", "2026-09-25")[0] == "unknown"
    assert slug("Dr. Pedro Irazoqui, DARPA/BTO") == "dr-pedro-irazoqui-darpa-bto"
    assert slug("MXO-PitchDay@darpa.mil") != slug("MXO_PitchDay@darpa.mil"), "two mailboxes, two observations"
    seed = {"nodes": [{"id": "office:dso", "type": "technical_center", "name": "Defense Sciences Office",
                       "codes": {"office_code": "DSO"}, "aliases": [{"text": "DSO"}]}]}
    assert office_index(seed)["dso"] == "office:dso" and office_label(seed, "office:dso") == "Defense Sciences Office (DSO)"
    obs = [{"id": "cobs:staff:dso:a", "office_id_as_resolved": "office:dso", "role_type": "executive", "role_as_written": "Office Director",
            "name": "A B", "observed_at": "2026-09-24", "start_date_as_listed": "2019-08-01"},
           {"id": "cobs:staff:dso:c", "office_id_as_resolved": "office:dso", "role_type": "program_manager", "role_as_written": "Program Manager",
            "name": "C D", "observed_at": "2026-09-24", "start_date_as_listed": ""},
           {"id": "cobs:notice:dso:x", "office_id_as_resolved": "office:dso", "role_type": "contracting_poc", "role_as_written": "primary point of contact",
            "name": "", "channel_as_written": "x@darpa.mil", "observed_at": "2026-09-01", "row_count": 3},
           {"id": "cobs:notice:dso:y", "office_id_as_resolved": "office:dso", "role_type": "contracting_poc", "role_as_written": "primary point of contact",
            "name": "", "channel_as_written": "y@darpa.mil", "observed_at": "2024-01-01", "row_count": 9}]
    recs = recommendations(seed, obs, "2026-09-25")
    assert [r["route_type"] for r in recs] == ["executive", "program_manager", "contracting_poc"], recs
    assert recs[0]["contact_observation_ids"] == ["cobs:staff:dso:a"] and "listed from 2019-08-01" in recs[0]["recommendation"]
    lead = recs[2]
    assert lead["contact_observation_ids"] == ["cobs:notice:dso:x"], "a mailbox older than a year is not the route in when a newer one exists"
    assert lead["currency_confidence"] == "high" and all(isinstance(lead[k], list) for k in ("competing_candidates", "contradictions", "caveats"))
    assert all(r["reviewed_by"] is None and r["review_status"] == "draft" and r["generator"] == GENERATOR for r in recs)
    # The Navy's hand-written files are never this tool's to overwrite.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "contact_observations.json"
        p.write_text('[{"id": "cobs:lrae:x"}]', encoding="utf-8")
        assert not own(p) and own(Path(tmp) / "absent.json")
    print("contact_routes selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    if argv[0] == "build":
        return build(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

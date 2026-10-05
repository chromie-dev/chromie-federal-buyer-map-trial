#!/usr/bin/env python3
"""The `instrument` block on every source registry row: what kind of thing the source is to a company that wants to sell.

    AGENCY=navy python research/tools/registry_instrument_fill.py            # write the block on every row of the profile's registry
    AGENCY=navy python research/tools/registry_instrument_fill.py --check    # fail if the saved blocks differ from a fresh derivation
    python research/tools/registry_instrument_fill.py --selfcheck

Derived from the row's own words and the saved source status, never from a guess about the publisher:
  status          Live (the source publishes now), Historical (its words say the series ended or moved) or Adjacent (an
                  archive, a directory or a support page around the buyer, not the buyer's own publication)
  standing        standing (a feed or a page that is always there) or episodic (a release series, a cycle, a hearing)
  collector_kind  blocked (the registry says blocked or restricted), collecting (the status shows documents or events) or
                  registered_empty (a row with nothing collected: the collector exists, the coverage is not claimed)
  cadence         kind from the stated publication frequency; a rule and its basis only where a saved page or the saved
                  index rows state one (the DSIP pre-release days)
  org_scope       the memory nodes the source feeds, where a saved source states them; [] otherwise
  close_by        how to close the gap, on a row with no documents; "" otherwise
The hand-kept part (HAND) holds what no rule derives: the contacts, the cadence rules, the entry types and the
envelopes of the portal rows, the wording of every Historical and Adjacent tag, and the close_by of the known gaps.
Every value in HAND names the saved page or the row it rests on. Registries are written with indent=1, ensure_ascii=False.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import KEY, P, SOURCES  # noqa: E402

REGISTRY = SOURCES / "source_registry.json"
STATUS = SOURCES / "source_status.json"
DECIDED = "2026-09-28"
STATUSES = ("Live", "Historical", "Adjacent")
STANDINGS = ("standing", "episodic", "mixed")
COLLECTORS = ("registered_empty", "collecting", "blocked")
CADENCES = ("standing", "monthly", "annual", "per_cycle", "episodic")
REQUIRED = ("status", "decided_by", "status_basis", "standing", "collector_kind", "cadence", "org_scope", "close_by")
OPTIONAL = ("contact", "entry_types", "envelopes", "pages", "url_prefixes")

# Sources that are not the buyer's own publication: an archive of other pages, a directory of offices, a re-generated copy.
ADJACENT = {"wayback_machine": "an archive of other sources' pages; it publishes nothing of its own (the row's words: crawler-dependent)",
            "dow_small_business_directory": "a directory of small business offices across the department; it points at buyers and states no requirement",
            "sam_archived_yearly_extracts": "yearly re-generations of the SAM.gov dataset the site API already serves; a copy for back-filling, not a publication of its own"}
# Sources whose own words say the series ended or moved: the row's historical_coverage is quoted in the basis.
HISTORICAL = {"navy_peoc4i_site": "the row's historical_coverage: on 2026-09-16 every path on peoc4i.navy.mil returns the PAE Mission Systems home page, so the office pages and tear sheets are no longer published there"}
EPISODIC_WORDS = ("annual", "semiannual", "per hearing", "per cycle", "cycles", "twice a year", "event-driven", "per report", "per release", "when ")
MONTHLY_WORDS = ("monthly", "every month", "first wednesday")

# The DSIP pre-release days of the Navy FY2026 rows on the saved index pages (research/events/sbir_topics.json,
# provider sbir_sttr_topics): five first Wednesdays and one Monday (the annual BAA of 2026-04-13). The rule is the
# five; the exception is kept beside it so the rule is never read as a promise.
DSIP_DATES = ["2026-04-13", "2026-05-06", "2026-06-03", "2026-07-01", "2026-08-05", "2026-09-02"]
DSIP_RULE = {"weekday": 2, "ordinal": 1, "exceptions": ["2026-04-13"]}
DSIP_BASIS = {"kind": "observed_dates", "dates": DSIP_DATES, "url": "https://www.dodsbirsttr.mil/topics-app/",
              "note": "pre_release of the Navy FY2026 rows in research/events/sbir_topics.json (DON26.. codes); five first Wednesdays and the annual BAA's 2026-04-13; no saved page states the rule"}
POINTER = lambda platform, handle: {"platform": platform, "handle": handle, "fetched": False}  # noqa: E731

# Hand-kept values, by registry (profile key) then source_key. Each names what it rests on.
HAND: dict[str, dict[str, dict]] = {
    "navy": {
        "sbir_sttr_topics": {"cadence": {"kind": "monthly", "rule": DSIP_RULE, "basis": DSIP_BASIS}, "standing": "episodic",
                             "org_scope": sorted(set(P["sbir_commands"].values())) if KEY == "navy" else [],
                             "pages": ["https://www.dodsbirsttr.mil/topics-app/"],
                             "entry_types": [{"name": "Conventional BAA", "basis": "observed", "note": "DSIP DON26BZ.. rows (the DoW SBIR 2026 BAA)"},
                                             {"name": "Direct to Phase II", "basis": "observed", "note": "DON26BZ..-DV rows titled DIRECT TO PHASE II on the saved index pages"},
                                             {"name": "Open Topic CSO", "basis": "observed", "note": "DON26BX..-NP rows titled Open Topic on the saved index pages"}]},
        "navy_sbir_program_site": {
            "status": "Live", "standing": "standing", "collector_kind": "blocked",
            "status_basis": "the landing page https://www.navysbir.com/ answered 403 to the direct fetch of 2026-09-28 (ledger row, no bytes); the programme publishes monthly through DSIP, whose saved FY2026 index rows carry Navy topics every month from April to September 2026",
            "org_scope": ["agency:don"],
            "close_by": "read the site from an address it accepts (the hosted browser is for registered .mil pages; navysbir.com is a .com the RUNBOOK lists as refusing), or take the same topics from the saved DSIP index pages, which already carry every Navy topic",
            "contact": {"emails": ["navy-sbir-sttr@navy.mil"], "phones": [],
                        "social_pointers": [POINTER("LinkedIn", "donsbir"), POINTER("X", "@donsbir")],
                        "basis": "the user's note of 2026-09-28; the landing page refused, so no saved page states them"},
            "cadence": {"kind": "monthly", "rule": DSIP_RULE, "basis": DSIP_BASIS},
            "pages": ["https://www.navysbir.com/", "https://www.navysbir.com/poc.htm", "https://www.navysbir.com/links_forms.htm"],
            "entry_types": [{"name": "Conventional BAA", "basis": "observed", "note": "DSIP DON26BZ.. rows"},
                            {"name": "Direct to Phase II", "basis": "observed", "note": "16 DON26BZ..-DV rows titled DIRECT TO PHASE II"},
                            {"name": "Open Topic CSO", "basis": "observed", "note": "DON26BX..-NP rows titled Open Topic"}],
            "envelopes": ["navy_sbir_phase_i"], "url_prefixes": []},
        "diu_cso_solicitations": {
            "status": "Live", "standing": "mixed", "collector_kind": "registered_empty",
            "status_basis": "https://www.diu.mil/work-with-us/submit-solution answered 404 and https://www.diu.mil/latest/solicitations answered 500 to the direct fetches of 2026-09-28 (ledger rows, no bytes); the unit's commercial solutions openings are the instrument the Orca brief needed and no saved page yet states one",
            "org_scope": [],
            "close_by": "find the unit's current solicitations address (the two the note named answer 404 and 500), fetch it through fetch.py, and register the first saved page as the inspected example; until then the row is an empty collector",
            "contact": {"emails": [], "phones": [], "social_pointers": [], "basis": "no saved page"},
            "cadence": {"kind": "standing", "basis": {"kind": "stated_by_user", "note": "rolling commercial solutions openings with dated areas of interest; no saved page states it"}},
            "pages": ["https://www.diu.mil/work-with-us/submit-solution", "https://www.diu.mil/latest/solicitations"],
            "entry_types": [{"name": "Commercial Solutions Opening", "basis": "stated_by_user", "note": "no saved page"}], "envelopes": [], "url_prefixes": []},
        "sam_opportunities_api": {"close_by": "the keyed opportunities API answered 404 with an empty body to every call on 2026-09-16 and again on 2026-09-27 (Orca run, control query included): open a ticket with SAM.gov support quoting the request id, or read the same notices from the site API (sam_gov_site_api) and the public extract, which the layer already does; do not retry around it"},
        "seaport_nxg": {"close_by": "https://www.seaport.navy.mil/ completed no TLS handshake on 2026-09-28 (URLError, TLSV1_ALERT_INTERNAL_ERROR, ledger row); task orders are visible to contract holders only, so the gap closes through a holder's export or the FPDS order records under the vehicle, never a fetch",
                        "status_basis": "the vehicle stands (the row's historical_coverage: vehicle since 2019); the portal refused the handshake on 2026-09-28"},
        "navy_posture_testimony": {"status_basis": "the annual posture cycle stands; the congress.gov hearing API answered 200 with DEMO_KEY on 2026-09-28 (ledger row 29c5cdea980e, a five-hearing page), so the row holds a document and a keyed sweep of the armed services committees' hearings for Navy witnesses is its next step"},
    },
    "army": {
        "sbir_sttr_topics": {"cadence": {"kind": "monthly", "rule": DSIP_RULE, "basis": {**DSIP_BASIS, "note": "the DSIP pre-release days observed on the Navy FY2026 rows; the Army's own rows on the same index pages share the cycle"}},
                             "standing": "episodic", "org_scope": sorted(set(P["sbir_commands"].values())) if KEY == "army" else [],
                             "pages": ["https://www.dodsbirsttr.mil/topics-app/"]},
        "army_sbir_program_site": {
            "status": "Live", "standing": "standing", "collector_kind": "collecting",
            "status_basis": "the landing page https://armysbir.army.mil/ answered 200 on 2026-09-28 (ledger row c0ba474e3290, 357,092 bytes) and states the phase envelopes on the page",
            "org_scope": ["agency:army"], "close_by": "",
            "contact": {"emails": ["usarmy.sbirsttr@army.mil"], "phones": [], "social_pointers": [], "basis": "the user's note of 2026-09-28; the saved landing page lists no mailbox in its text"},
            "cadence": {"kind": "monthly", "rule": DSIP_RULE, "basis": {**DSIP_BASIS, "kind": "observed_dates", "note": "the Army FUZE monthly cadence the user's note names; the saved landing page states no rule, so the DSIP pre-release days stand in as the observed basis"}},
            "pages": ["https://armysbir.army.mil/"],
            "entry_types": [{"name": "Phase I white paper", "basis": "stated_on_page", "note": "the page: 'It starts with a white paper PHASE I 1-6 months, up to $300K'"},
                            {"name": "Phase II", "basis": "stated_on_page", "note": "the page: 'PHASE II 12-18 months, up to $2M'"},
                            {"name": "Strategic Breakthrough", "basis": "stated_on_page", "note": "the page: 'Army FUZE Awards Historic $3.5M Strategic Breakthrough to FluxWorks'"}],
            "envelopes": ["army_sbir_phase_i", "army_sbir_phase_ii", "army_catalyst"], "url_prefixes": []},
        "army_xtech_prizes": {
            "status": "Live", "standing": "episodic", "collector_kind": "collecting",
            "status_basis": "the landing page https://xtech.army.mil/ answered 200 on 2026-09-28 (ledger row 38f6cf935a0a, 377,666 bytes); prize competitions open and close by date",
            "org_scope": ["agency:army"], "close_by": "",
            "contact": {"emails": [], "phones": [], "social_pointers": [], "basis": "the saved landing page lists no mailbox in its text"},
            "cadence": {"kind": "episodic", "basis": {"kind": "stated_by_user", "note": "competitions announced by date; the saved landing page states no schedule"}},
            "pages": ["https://xtech.army.mil/"],
            "entry_types": [{"name": "Prize-to-Contract", "basis": "stated_by_user", "note": "the user's note; the saved landing page's text names no instrument"}],
            "envelopes": ["army_xtech_disrupt_fires"], "url_prefixes": []},
        "diu_cso_solicitations": None,  # copied from the Navy block below
    },
    "darpa": {
        "sbir_sttr_topics": {"cadence": {"kind": "monthly", "rule": DSIP_RULE, "basis": {**DSIP_BASIS, "note": "the DSIP pre-release days observed on the Navy FY2026 rows; DARPA's own rows on the same index pages share the cycle"}},
                             "standing": "episodic", "org_scope": sorted(set(P["sbir_commands"].values())) if KEY == "darpa" else [],
                             "pages": ["https://www.dodsbirsttr.mil/topics-app/"]},
        "darpa_small_business_community": {
            "status": "Live", "standing": "standing", "collector_kind": "collecting",
            "status_basis": "https://www.darpa.mil/work-with-us/communities/small-business answered 200 on 2026-09-24 (Navy-versus-DARPA comparison) and on 2026-09-28 (ledger row 6afc2c89b233, 44,143 bytes)",
            "org_scope": ["office:sbpo"], "close_by": "",
            "contact": {"emails": [], "phones": [], "social_pointers": [], "basis": "the saved landing page lists no mailbox in its text"},
            "cadence": {"kind": "standing", "basis": {"kind": "stated_on_page", "note": "a standing community page; it announces no cycle of its own"}},
            "pages": ["https://www.darpa.mil/work-with-us/communities/small-business"],
            "entry_types": [{"name": "SBIR XL", "basis": "stated_by_user", "note": "the user's note; the saved landing page's text states no envelope"}],
            "envelopes": ["darpa_sbir_xl"],
            "url_prefixes": ["https://www.darpa.mil/work-with-us/communities/small-business"]},
        "darpaconnect": {
            "status": "Live", "standing": "standing", "collector_kind": "collecting",
            "status_basis": "https://darpaconnect.us/ answered 200 on 2026-09-28 (ledger row 89e1898f48a2, 93,928 bytes)",
            "org_scope": [], "close_by": "",
            "contact": {"emails": ["darpaconnect@darpa.mil"], "phones": [], "social_pointers": [], "basis": "the user's note of 2026-09-28; the saved landing page lists no mailbox in its text"},
            "cadence": {"kind": "standing", "basis": {"kind": "stated_by_user", "note": "a standing onboarding programme; the saved landing page states no cycle"}},
            "pages": ["https://darpaconnect.us/"], "entry_types": [], "envelopes": [], "url_prefixes": []},
        "diu_cso_solicitations": None,
    },
    "airforce": {
        "sbir_sttr_topics": {"cadence": {"kind": "monthly", "rule": DSIP_RULE, "basis": {**DSIP_BASIS, "note": "the DSIP pre-release days observed on the Navy FY2026 rows; the Air Force's own rows on the same index pages share the cycle"}},
                             "standing": "episodic", "org_scope": sorted(set(P["sbir_commands"].values())) if KEY == "airforce" else [],
                             "pages": ["https://www.dodsbirsttr.mil/topics-app/"]},
        "afwerx_site": {
            "status": "Live", "standing": "standing", "collector_kind": "collecting",
            "status_basis": "https://afwerx.com/ answered 200 on 2026-09-28 (ledger row 1136b89aeb3d, 111,809 bytes)",
            "org_scope": ["command:afmc"], "close_by": "",
            "contact": {"emails": ["AFRL.PA.Inquiry@us.af.mil"], "phones": [], "social_pointers": [], "basis": "the saved landing page: 'Media Queries: AFRL.PA.Inquiry@us.af.mil'; a media mailbox, not a proposer's"},
            "cadence": {"kind": "standing", "basis": {"kind": "stated_on_page", "note": "a standing programme site; open topics and challenges are dated on their own pages, not read here"}},
            "pages": ["https://afwerx.com/"], "entry_types": [], "envelopes": [], "url_prefixes": []},
        "spacewerx_site": {
            "status": "Live", "standing": "standing", "collector_kind": "collecting",
            "status_basis": "https://spacewerx.us/ answered 200 on 2026-09-28 (ledger row 45eeb407c860, 108,189 bytes)",
            "org_scope": [], "close_by": "",
            "contact": {"emails": ["AFRL.PA.Inquiry@us.af.mil"], "phones": [], "social_pointers": [], "basis": "the saved landing page: 'Media Queries: AFRL.PA.Inquiry@us.af.mil'; a media mailbox, not a proposer's"},
            "cadence": {"kind": "standing", "basis": {"kind": "stated_on_page", "note": "a standing programme site"}},
            "pages": ["https://spacewerx.us/"], "entry_types": [], "envelopes": [], "url_prefixes": []},
        "diu_cso_solicitations": None,
    },
}
for _k in ("army", "darpa", "airforce"):
    HAND[_k]["diu_cso_solicitations"] = HAND["navy"]["diu_cso_solicitations"]


def cadence_kind(frequency: str) -> str:
    f = (frequency or "").lower()
    if any(w in f for w in MONTHLY_WORDS):
        return "monthly"
    if any(w in f for w in ("semiannual", "twice a year", "per cycle", "cycles", "per hearing", "per report", "per release")):
        return "per_cycle"
    if "annual" in f:
        return "annual"
    if any(w in f for w in ("event-driven", "irregular", "when ", "on demand", "as hearings", "as the hierarchy", "under construction", "crawler")):
        return "episodic"
    return "standing"


def derive(row: dict, stat: dict | None) -> dict:
    """The block a row's own words and its status derive; the hand-kept part is merged over it by fill()."""
    key = row["source_key"]
    documents = (stat or {}).get("documents", 0)
    events = (stat or {}).get("events", 0)
    verification = row.get("verification_status", "")
    frequency = row.get("publication_frequency", "")
    if key in HISTORICAL:
        status, basis = "Historical", HISTORICAL[key]
    elif key in ADJACENT:
        status, basis = "Adjacent", ADJACENT[key]
    else:
        status = "Live"
        basis = (f"the source publishes now (verification_status {verification}; publication_frequency '{frequency}'); "
                 "the saved source status counts its documents and events")
    collector = "blocked" if verification in ("blocked", "restricted") else "collecting" if (documents or events) else "registered_empty"
    kind = cadence_kind(frequency)
    standing = "episodic" if any(w in (frequency or "").lower() for w in EPISODIC_WORDS) else "standing"
    close_by = "" if documents else (row.get("access_restrictions") or "").strip() or f"nothing collected yet; {(row.get('inspected_example') or {}).get('note') or 'run the collector'}"
    return {"status": status, "decided_by": DECIDED, "status_basis": basis, "standing": standing, "collector_kind": collector,
            "cadence": {"kind": kind, "basis": {"kind": "stated_frequency", "note": f"the row's publication_frequency: '{frequency}'"}},
            "org_scope": [], "close_by": close_by}


def merged(row: dict, stat: dict | None) -> dict:
    block = derive(row, stat)
    hand = (HAND.get(KEY) or {}).get(row["source_key"]) or {}
    for k, v in hand.items():
        block[k] = v
    if block["collector_kind"] == "registered_empty" and stat and (stat.get("documents") or stat.get("events")):
        block["collector_kind"] = "collecting"  # a hand block written before the first collection reads the status
    ordered = {k: block[k] for k in REQUIRED}
    ordered.update({k: block[k] for k in OPTIONAL if k in block})
    return ordered


def fill(check: bool = False) -> int:
    rows = json.loads(REGISTRY.read_text(encoding="utf-8"))
    stat = json.loads(STATUS.read_text(encoding="utf-8"))["sources"] if STATUS.exists() else {}
    changed = []
    for row in rows:
        block = merged(row, stat.get(row["source_key"]))
        if row.get("instrument") != block:
            changed.append(row["source_key"])
            row["instrument"] = block
    if check:
        if changed:
            print(f"{len(changed)} row(s) whose instrument block differs from a fresh derivation: {', '.join(changed)}", file=sys.stderr)
            return 1
        print(f"{len(rows)} instrument block(s) match a fresh derivation")
        return 0
    REGISTRY.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tags = {s: sum(1 for r in rows if r["instrument"]["status"] == s) for s in STATUSES}
    kinds = {c: sum(1 for r in rows if r["instrument"]["collector_kind"] == c) for c in COLLECTORS}
    print(f"{len(rows)} row(s) written ({len(changed)} changed): {tags}; collectors {kinds}")
    for r in rows:
        if r["instrument"]["status"] != "Live":
            print(f"  {r['instrument']['status']:10} {r['source_key']}: {r['instrument']['status_basis'][:110]}")
    return 0


def selfcheck() -> int:
    row = {"source_key": "x_feed", "verification_status": "verified", "publication_frequency": "daily", "access_restrictions": "none"}
    b = derive(row, {"documents": 3, "events": 0})
    assert (b["status"], b["collector_kind"], b["standing"], b["cadence"]["kind"], b["close_by"]) == ("Live", "collecting", "standing", "standing", "")
    empty = derive({**row, "verification_status": "not_inspected", "access_restrictions": "needs a key"}, {"documents": 0, "events": 0})
    assert empty["collector_kind"] == "registered_empty" and empty["close_by"] == "needs a key", empty
    blocked = derive({**row, "verification_status": "blocked"}, None)
    assert blocked["collector_kind"] == "blocked" and blocked["status"] == "Live", "a blocked source still publishes; the collector is what is blocked"
    assert derive({**row, "source_key": "wayback_machine"}, None)["status"] == "Adjacent"
    assert derive({**row, "source_key": "navy_peoc4i_site"}, None)["status"] == "Historical"
    assert cadence_kind("semiannual (activity-dependent)") == "per_cycle" and cadence_kind("annual (President's Budget)") == "annual"
    assert cadence_kind("monthly, first Wednesday") == "monthly" and cadence_kind("real time") == "standing" and cadence_kind("event-driven") == "episodic"
    assert derive({**row, "publication_frequency": "roughly annual"}, None)["standing"] == "episodic"
    for agency, rows in HAND.items():
        for key, hand in rows.items():
            assert hand is not None, (agency, key)
            for c in (hand.get("contact") or {}).get("phones", []):
                raise AssertionError(f"{agency}/{key}: a phone number in the registry")
            if hand.get("contact"):
                assert hand["contact"].get("basis") and all("@" in e for e in hand["contact"]["emails"]), f"{agency}/{key}: every contact names its basis"
                assert not any(re.search(r"\d{3}[-.\s]\d{3,4}", e) for e in hand["contact"]["emails"]), f"{agency}/{key}: no phone"
            for p in (hand.get("contact") or {}).get("social_pointers", []):
                assert p["fetched"] is False
            if "cadence" in hand:
                assert hand["cadence"]["kind"] in CADENCES and ("rule" not in hand["cadence"] or hand["cadence"]["kind"] in ("monthly", "annual"))
    print("registry_instrument_fill selfcheck ok")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(selfcheck() if "--selfcheck" in args else fill(check="--check" in args))

#!/usr/bin/env python3
"""Write research/agencies/airforce/sources/coverage_matrix.json from the Air Force layer's own files.

    python research/tools/coverage_matrix_airforce.py   # then: AGENCY=airforce python research/tools/coverage.py check

A cell names the registered sources whose saved documents or built events speak about the organization; an empty cell
states why from coverage.py's closed list. Every count in a note is computed here from the artefacts, so a rerun after
more collection rewrites the notes. Run from the repository root with AGENCY unset (the profile is read explicitly).
"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AF = ROOT / "research/agencies/airforce"
MANIFEST = ROOT / "research/sources/documents_manifest.jsonl"
OUT = AF / "sources/coverage_matrix.json"
TODAY = ""  # set below: the day of the newest saved Air Force row, so a rebuild dates itself

import sys
sys.path.insert(0, str(ROOT / "research/tools"))
import agency  # noqa: E402
from fetch import kept_page  # noqa: E402

P = agency.PROFILES["airforce"]
ORGS = P["coverage_orgs"]
FAMILIES = ["forecast", "notice", "incumbent", "budget", "programs", "oversight", "leaders", "congress", "conference", "organization", "news", "people", "protest", "grants"]
NODE = {"DAF": "agency:daf", "AFMC": "command:afmc", "AFLCMC": "command:aflcmc", "AFRL": "center:afrl", "AFSC": "command:afsc", "SSC": "command:ssc"}
WORDS = {"DAF": [r"\bAir Force\b", r"\bSpace Force\b", r"\bDAF\b", r"\bUSAF\b", r"\bUSSF\b"],
         "AFMC": [r"\bAFMC\b", r"Air Force Materiel Command"],
         "AFLCMC": [r"\bAFLCMC\b", r"Life Cycle Management Center"],
         "AFRL": [r"\bAFRL\b", r"Air Force Research Laboratory", r"\bAFOSR\b", r"Office of Scientific Research"],
         "AFSC": [r"\bAFSC\b", r"Air Force Sustainment Center", r"Air Logistics Complex"],
         "SSC": [r"\bSSC\b", r"Space Systems Command", r"Space and Missile Systems Center"]}

seed = json.loads((AF / "memory/organization_seed.json").read_text(encoding="utf-8"))
rows = [json.loads(l) for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]
ok = [r for r in rows if kept_page(r)]
TODAY = max(((r.get("retrieved_at") or "")[:10] for r in ok if "[airforce]" in (r.get("note") or "")), default="")
notice_rows = sum(1 for r in ok if (r.get("note") or "").startswith("SAM notice detail [airforce]"))


def load(name: str):
    p = AF / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# The offices under each organization: the seed's child_of chain from every contracting office upward.
parent = {r["from"]: r["to"] for r in seed["relationships"] if r["type"] == "child_of"}
office_code = {n["id"]: n["codes"].get("uic") for n in seed["nodes"] if n["type"] == "contracting_office"}


def ancestors(nid: str) -> set[str]:
    out, cur = set(), nid
    while cur in parent:
        cur = parent[cur]
        out.add(cur)
    return out


offices_of = {org: sorted(code for nid, code in office_code.items() if code and (NODE[org] == nid or NODE[org] in ancestors(nid) or org == "DAF"))
              for org in ORGS}


def mentions(org: str, text: str) -> bool:
    return any(re.search(w, text or "", re.I) for w in WORDS[org])


def names(org: str, nodes: list) -> bool:
    return NODE[org] in (nodes or [])


# --- counts per family
fpds = Counter()
for r in ok:
    m = re.search(r"office (FA\d{4})", r.get("note", "")) if r.get("note", "").startswith("FPDS sweep [airforce]") else None
    if m:
        fpds[m.group(1)] += 1
sam_orgs = Counter()
for r in ok:
    if r.get("note", "").startswith("SAM sweep organization [airforce]"):
        m = re.search(r"(FA\d{4})", r["note"])
        if m:
            sam_orgs[m.group(1)] += 1
sam_dir = ROOT / "data/raw" / P["sam_dir"]
notice_files = [p for p in sam_dir.glob("*.json") if not p.name.startswith("search_")] if sam_dir.exists() else []
notices_by_office = Counter()
for p in notice_files:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    text = json.dumps(d)[:4000]
    m = re.search(r'"(FA\d{4})', text) or re.search(r"\b(FA\d{4})", text)
    notices_by_office[m.group(1) if m else "?"] += 1

topics = (load("events/sbir_topics.json") or {}).get("rows", [])
topics_by = Counter(t["org"] for t in topics)
oversight = (load("events/oversight_events.json") or {}).get("documents", [])
ov_events = [e for d in oversight for e in d["events"]]
remarks = (load("events/remarks_events.json") or {}).get("documents", [])
rm_events = [e for d in remarks for e in d["events"]]
congress = (load("events/congress_events.json") or {}).get("rows", [])
grants = (load("events/assistance_awards.json") or {}).get("rows", [])
grant_pages = [r for r in ok if (r.get("note") or "").startswith("USAspending assistance awards [airforce]")]
n_reports = (load("events/congress_events.json") or {}).get("reports", 0)
hearing_day = remarks[0]["issued"] if remarks else "unknown"
exa_queries = {r["note"].split(":", 1)[1].strip() for r in ok if (r.get("note") or "").startswith("search [airforce]:")}
protests = (load("events/protest_events.json") or {}).get("rows", [])
news = (load("events/news_observations.json") or {}).get("articles", [])
budget = load("events/budget_lines.json") or {"books": [], "lines": []}
people = load("memory/people.json") or {"rows": []}
gdelt = [r for r in ok if "gdeltproject" in (r.get("url") or "") and "[airforce]" in r.get("note", "")]
watch_items = [r for r in ok if r.get("note", "").startswith("news watch item [airforce]") or r.get("note", "").startswith("live page via Browserbase [airforce]")]

cells = []


def cell(org, family, sources=None, note="", reason=None):
    c = {"org": org, "family": family}
    if sources:
        c["sources"] = sources
    else:
        c["reason"] = reason
    c["note"] = note
    cells.append(c)


for org in ORGS:
    offs = offices_of[org]
    # forecast
    cell(org, "forecast", reason="no_public_source",
         note=f"no department-wide acquisition forecast was found on {TODAY} (research/docs/20, section 2): the SAF/FM budget page, the SAM.gov hierarchy and "
              "the commands' pages (403 to this address) name none; AFLCMC's quarterly SMART Guide is a PDF not yet read; notices and awards carry the requirement")
    # notice
    swept = [o for o in offs if sam_orgs.get(o)]
    n_not = sum(notices_by_office.get(o, 0) for o in offs)
    if swept:
        cell(org, "notice", ["sam_gov_site_api"],
             f"{len(swept)} of {len(offs)} office(s) swept by organization id ({', '.join(swept[:8])}{'...' if len(swept) > 8 else ''}); "
             f"{sum(sam_orgs[o] for o in swept)} search pages and {n_not} notice details whose saved page states an office code of {org} (the ledger holds {notice_rows} detail rows for the swept organizations; a page states no code where the notice names the office by name alone), harvested since 2021-10-01 as of {TODAY}")
    else:
        cell(org, "notice", reason="not_started", note=f"no office of {org} is swept by organization id yet ({', '.join(offs) or 'no office'})")
    # incumbent
    n_pages = sum(fpds.get(o, 0) for o in offs)
    if n_pages:
        cell(org, "incumbent", ["fpds_atom_feed"],
             f"base awards signed by {len([o for o in offs if fpds.get(o)])} office(s) FY2020 to FY2026, {n_pages} feed pages saved; the award histories (changes stage) follow")
    else:
        cell(org, "incumbent", reason="not_started", note=f"no FPDS page saved for an office of {org}")
    # budget
    if org == "DAF" and budget["lines"]:
        cell(org, "budget", ["daf_budget_justification_books", "wayback_machine"],
             f"{len(budget['books'])} FY2027 procurement books read (Aircraft Procurement Vol I, Missile, Ammunition, Other Procurement; Vol II carries no P-40), "
             f"{len(budget['lines'])} P-1 lines, from Wayback captures of the SAF/FM files (the live host completes no TLS handshake); RDT&E and Space Force procurement books not found on the captured page")
    else:
        cell(org, "budget", reason="not_started",
             note="the P-40 books name the appropriation line and its program, not the center that executes it; a line-to-office reading is not yet made" if budget["lines"]
             else "no book read yet")
    # programs
    n_top = topics_by.get(NODE[org], 0) if org != "DAF" else len(topics)
    if org == "AFMC":
        n_top += sum(topics_by.get(NODE[o], 0) for o in ("AFLCMC", "AFRL", "AFSC"))
    if n_top:
        cell(org, "programs", ["sbir_sttr_topics"],
             f"{n_top} USAF SBIR/STTR topic(s) opened since FY2020 whose portal command resolves to {org}" + (" or a center under it" if org == "AFMC" else "")
             + f"; details read for all {len(topics)} through the hosted browser on {TODAY}")
    else:
        cell(org, "programs", reason="no_public_source", note=f"no saved topic's command resolves to {org}")
    # oversight
    ov = [e for e in ov_events if names(org, e.get("organizations")) or mentions(org, e.get("affected_organization", "") + " " + e.get("evidence_span", ""))]
    if org == "DAF":
        ov = ov_events
    if ov:
        cell(org, "oversight", ["oversight_gov_reports", "gao_reports"],
             f"{len(ov)} finding(s) from {len({d['url'] for d in oversight for e in d['events'] if e in ov})} report(s) name {org}; "
             f"{len(oversight)} DoD OIG and GAO reports since 2025 read on {TODAY}")
    else:
        cell(org, "oversight", reason="not_started", note=f"none of the {len(oversight)} reports read names {org} in a finding; GAO and DoD OIG reports about the center's programs exist and are not yet searched for by the center's name")
    # leaders
    rm = rm_events if org == "DAF" else [e for e in rm_events if names(org, e.get("organizations")) or mentions(org, e.get("evidence_span", ""))]
    if rm:
        cell(org, "leaders", ["house_committee_repository"],
             f"{len(rm)} statement(s) from the department's FY2027 posture hearing ({hearing_day}) name {org}; af.mil publishes no speech archive (its /News/Speeches/ address renders the news listing), so leaders' words come as testimony and as news")
    else:
        cell(org, "leaders", reason="blocked", note=f"the FY2027 posture testimony names no {org} program; the department publishes no speech archive, and the command's own site refuses this address (its leadership page is taken through the hosted browser and names who leads it, in the organization family, not what they said)")
    # congress
    cg = congress if org == "DAF" else [e for e in congress if mentions(org, e.get("title", "") + " " + e.get("body", ""))]
    if cg:
        cell(org, "congress", ["govinfo_api"], f"{len(cg)} directive(s) naming {org} in the {n_reports} committee reports saved by the Navy layer (shared source); no new report taken (DATA_GOV_API_KEY absent)")
    else:
        cell(org, "congress", reason="not_started", note=f"none of the {len(congress)} directives in the {n_reports} committee reports saved names {org}; the later reports are not taken (DATA_GOV_API_KEY absent)")
    # conference
    # conference: the pages Exa found for the profile's names and the remarks reader read (kind "conference")
    conf = [d for d in remarks if d.get("kind") == "conference"]
    conf_events = [e for d in conf for e in d["events"]]
    cf = conf_events if org == "DAF" else [e for e in conf_events if names(org, e.get("organizations")) or mentions(org, e.get("evidence_span", ""))]
    if cf:
        cell(org, "conference", ["conference_pages_exa"],
             f"{len(cf)} statement(s) on {len({d['url'] for d in conf if any(e in cf for e in d['events'])})} conference page(s) Exa found for the department's names "
             f"(organizers' agendas and speaker lists, trade write-ups) name {org}; {len(conf)} page(s) read on {TODAY}")
    elif conf:
        cell(org, "conference", reason="no_public_source", note=f"none of the {len(conf_events)} statements on the {len(conf)} conference pages read names {org}")
    else:
        cell(org, "conference", reason="not_started", note=f"no conference page read for the department yet: the remarks discovery (Exa) has not been run")
    # organization
    led = [r for r in seed["relationships"] if r["type"] == "leads" and r["to"] == NODE[org]]
    who = {n["id"]: n["name"] for n in seed["nodes"]}
    cell(org, "organization", ["sam_gov_organizations"] + (["daf_site"] if led or org == "DAF" else []),
         f"SAM.gov's federal organization record of {org} and of each office under it ({len(offs)} office(s)) read into the seed; "
         + (f"{len(led)} leader(s) ({', '.join(who[r['from']] for r in led)}) from " + ("the leadership page taken through the hosted browser" if all(r["evidence_class"] == "directly_documented" or "chart" not in (r.get("current_status") or {}).get("note", "") for r in led) else "the center's organizational chart PDF (its leadership paths answer 404), a column reading marked inferred")
            if led else "the command's own pages refuse this address and are not yet taken through the hosted browser")
         + ("; the department's news articles (hosted browser) add appointments" if org == "DAF" else ""))
    # news
    nw = news if org == "DAF" else [a for a in news if names(org, (a.get("entities") or {}).get("organizations")) or mentions(org, json.dumps(a.get("claims", [])))]
    if nw:
        cell(org, "news", ["news_articles_exa", "daf_site"], f"{len(nw)} article(s) modelled naming {org}, from the af.mil and spaceforce.mil feeds and {len(exa_queries)} Exa news searches (one per office) (the official pages taken through the hosted browser after a direct 403); the per-office GDELT sweep answered {len(gdelt)} queries before GDELT rate-limited this address on {TODAY}")
    else:
        cell(org, "news", reason="not_started", note=f"no saved article names {org} yet; the per-office GDELT sweep answered {len(gdelt)} queries before GDELT rate-limited this address on {TODAY}")
    # people
    pp = [p for p in people["rows"] if any(pos.get("office") in ([NODE[org]] + [f"contracting:{o.lower()}" for o in offs]) for pos in p.get("positions", []))] if people["rows"] else []
    if people["rows"] and (pp or org == "DAF"):
        cell(org, "people", ["sam_gov_site_api"] + (["house_committee_repository"] if org == "DAF" else []),
             f"{len(pp) if org != 'DAF' else len(people['rows'])} person(s) named as points of contact on the notices of {org}'s offices" + (" and as witnesses" if org == "DAF" else ""))
    elif not people["rows"]:
        cell(org, "people", reason="not_started", note="people.json not built yet")
    else:
        cell(org, "people", reason="no_public_source", note=f"no notice of an office under {org} names a point of contact")
    # protest
    pr = protests if org == "DAF" else [e for e in protests if mentions(org, e.get("title", "") + " " + e.get("body", "")) or any(re.search(rf"\b{o}", e.get("body", "")) for o in offs)]
    if pr:
        cell(org, "protest", ["gao_bid_protests"], f"{len(pr)} GAO protest(s) of the department's awards" + (f" on solicitations of {org}'s offices" if org != "DAF" else "") + f", from the docket taken through the hosted browser on {TODAY}")
    else:
        cell(org, "protest", reason="no_public_source", note=f"no docketed protest names a solicitation of an office under {org}")
    # grants: USAspending names the awarding sub-agency, not the office; an award id's first six characters are the
    # awarding office's code (FA9550 is AFOSR under AFRL), so an award is placed where its id starts with a swept office
    if not grants:
        cell(org, "grants", reason="not_started", note="the grants stage (USAspending assistance awards, page by page) has not been run for the department")
    else:
        mine = grants if org == "DAF" else [g for g in grants if str((g.get("data") or {}).get("award_id") or "")[:6].upper() in set(offs)]
        if mine:
            cell(org, "grants", ["usaspending_api"],
                 f"{len(mine)} of the {len(grants)} grants and cooperative agreements USAspending lists for the department since FY2020"
                 + ("" if org == "DAF" else f" carry an award id starting with the code of an office of {org}") + f"; {len(grant_pages)} page(s) saved on {TODAY}")
        else:
            cell(org, "grants", reason="no_public_source", note=f"none of the {len(grants)} assistance awards carries an award id starting with the code of an office of {org}; USAspending names the awarding sub-agency, not the office")

matrix = {"as_of": TODAY,
          "note": "One row per Department of the Air Force organization the layer studies (the department, its two acquisition commands and the three centers the swept "
                  "offices belong to) and one column per evidence family the back-test counts. A cell names the registered sources that produce dated events or documents "
                  "for that organization; an empty cell states why from a closed list. Written by a script from the layer's own files and validated by "
                  "research/tools/coverage.py (AGENCY=airforce) against the Air Force registry, the frozen corpus and the shared manifest.",
          "organizations": ORGS, "families": FAMILIES, "cells": cells}
OUT.write_text(json.dumps(matrix, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
covered = sum(1 for c in cells if c.get("sources"))
print(f"{covered} of {len(cells)} cells covered -> {OUT.relative_to(ROOT)}")
print({o: offices_of[o] for o in ORGS})

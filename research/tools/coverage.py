#!/usr/bin/env python3
"""Coverage matrix and source status: which registered source covers each command and
family, with a stated reason wherever nothing does, and per registered source what was last collected.

The matrix is generated (`matrix`) for a profile whose `coverage_org_nodes` names the memory node behind each row: a cell
counts the frozen corpus's events under that node's subtree per family (people from the roster's positions), names the
sources that made them beside the department-wide sources of the family, carries the documents those sources hold,
the newest date and a tag: Live (an event within a year of the corpus freeze), Historical (events, none that recent),
Adjacent (only a department-wide source speaks). Where nothing speaks the cell keeps the hand-written reason from the
closed list. Other profiles' matrices stay hand-written; this tool refuses a matrix that names a source the registry
lacks, a source that has collected nothing, an empty cell without a reason, or a grid with a hole. The status is
computed: events per source from the frozen corpus, documents per source from the manifest by host, the newest retrieval
and its hash; beside it the sources as instruments and the open gaps (every row with no documents, with its blocker
and how to close it).

  python research/tools/coverage.py check      # validate the matrix, print the grid
  python research/tools/coverage.py status [--check]   # write research/sources/source_status.json, or fail if it would change
  python research/tools/coverage.py matrix [--check]   # write research/sources/coverage_matrix.json from the files (profiles with coverage_org_nodes)
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from backtest import CORPUS  # noqa: E402

from agency import KEY, MANIFEST, MEMORY, P, PROFILES, SOURCES, note_is_foreign, note_is_ours  # noqa: E402

RESEARCH = ROOT / "research"
MATRIX = SOURCES / "coverage_matrix.json"
STATUS = SOURCES / "source_status.json"
REGISTRY = SOURCES / "source_registry.json"
REASONS = ("no_public_source", "blocked", "restricted", "not_started")
SHARED_SOURCES = frozenset(P.get("shared_sources") or ())
NS = uuid.UUID("7c3d1f5a-9b24-4f8e-8c61-2a0d5e7b41c3")  # agency_layers_sql.NS: the corpus's org ids are uuid5(NS, "org:<node>")
CELL_STATUSES = ("Live", "Historical", "Adjacent")
LIVE_DAYS = 365
# The rows whose gap the open-gap list names first (the three Navy sources with nothing collected, in the order asked).
GAPS_FIRST = ("sam_opportunities_api", "seaport_nxg", "navy_posture_testimony")
WAYBACK_RE = re.compile(r"https?://web\.archive\.org/web/\d+(?:id_)?/(https?://.*)")
# Hosts the registry's URLs do not show but that belong to a registered source: renamed domains and the portals a
# source is read through.
EXTRA_HOSTS = {"sbir_sttr_topics": ["www.dodsbirsttr.mil", "www.sbir.gov", "api.www.sbir.gov", "www.navysbir.com"],
               "ai_use_case_inventory": ["api.github.com"],  # the file's commit log, which dates the saved rows
               # The Department of the Air Force's sites beside af.mil: the commands', the Space Force's and the small business office's.
               "daf_site": ["www.spaceforce.mil", "www.afmc.af.mil", "www.aflcmc.af.mil", "www.afrl.af.mil", "www.afsc.af.mil",
                            "www.ssc.spaceforce.mil", "www.airforcesmallbiz.af.mil", "media.defense.gov"],
               "news_articles_exa": ["api.gdeltproject.org"],
               # The announcement pages the listing points at are the same source as the listing.
               "usajobs_historic_joa": ["www.usajobs.gov"],
               "dod_contract_announcements": ["www.war.gov"],
               "dod_comptroller_budget_materials": ["comptroller.war.gov"],
               "senate_committee_sites": ["www.appropriations.senate.gov"]}
# One router host answers several capabilities (news search, contractor job search), so its host names no source: the
# folder the collector saved the answer in does.
ROUTER_FOLDERS = {"api.routergrowth.com": {"data/raw/news": "news_articles_exa", "data/raw/vendor_jobs": "vendor_jobs_routergrowth"}}
# Pages found through a search are filed under the search source by the note the fetch left, not by their host. The
# first prefix that starts the note wins, so a specific prefix sits before the bare "search".
NOTE_KEYS = (("remarks watch", "conference_pages_exa"), ("news sweep", "news_articles_exa"), ("news watch", "news_articles_exa"),
             ("news search", "news_articles_exa"), ("news inspect", "news_articles_exa"),
             # the router's schema and run answers ("vendor jobs inspect", "vendor jobs search") and the company pages
             # they point at ("vendor job: ..."): one source, whatever the company's host (vendor_jobs.py)
             ("vendor job", "vendor_jobs_routergrowth"),
             ("person profile", "linkedin_profiles_exa"), ("office people", "linkedin_profiles_exa"),
             ("exa search", "conference_pages_exa"), ("search", "conference_pages_exa"))


def registry() -> dict[str, dict]:
    return {r["source_key"]: r for r in json.loads(REGISTRY.read_text(encoding="utf-8"))}


def host(url: str) -> str:
    m = WAYBACK_RE.match(url or "")
    return urllib.parse.urlparse(m.group(1) if m else url or "").netloc


def hosts_by_key(reg: dict[str, dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for key, row in reg.items():
        for url in (row.get("official_url"), (row.get("inspected_example") or {}).get("url")):
            if url and host(url) and host(url) != "web.archive.org":
                out[key].add(host(url))
        out[key].update(EXTRA_HOSTS.get(key, ()))
    return out


def other_agency_hosts() -> set[str]:
    """The hosts another agency's registry owns. The ledger is shared, so a page fetched for the other layer is that
    layer's document, not a host this layer failed to register."""
    out: set[str] = set()
    for key in PROFILES:
        if key == KEY:
            continue
        folder = RESEARCH / "sources" if key == "navy" else RESEARCH / "agencies" / key / "sources"
        path = folder / "source_registry.json"
        if path.exists():
            reg = {r["source_key"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
            for hosts in hosts_by_key(reg).values():
                out |= hosts
    return out


def urls_by_key(reg: dict[str, dict]) -> dict[str, set[str]]:
    return {key: {u.rstrip("/") for u in (row.get("official_url"), (row.get("inspected_example") or {}).get("url")) if u} for key, row in reg.items()}


def first_segment(url: str) -> str:
    m = WAYBACK_RE.match(url or "")
    return urllib.parse.urlparse(m.group(1) if m else url or "").path.strip("/").split("/")[0]


def narrowed(url: str, keys: list[str], urls: dict[str, set[str]]) -> list[str]:
    """Of several sources on one host, the ones that registered this very URL: one download host serves the chart and
    three forecasts, each registered by its file. Else the ones that registered a URL in the same first path section
    (GAO's /products/ pages are its reports, not its bid protest docket). A URL in no source's section stays with every
    source on its host."""
    url = (url or "").rstrip("/")
    exact = [k for k in keys if url in urls.get(k, ())]
    if exact:
        return exact
    seg = first_segment(url)
    return [k for k in keys if seg and any(first_segment(u) == seg for u in urls.get(k, ()))] or keys


def prefixed(url: str, keys: list[str], reg: dict[str, dict]) -> list[str]:
    """A source whose instrument block lists `url_prefixes` claims only the URLs under them and takes them from the host's
    other sources: DARPA's small business page is its own row, the rest of darpa.mil stays with the site."""
    url = (url or "").rstrip("/")
    under = lambda p: url == p.rstrip("/") or url.startswith(p.rstrip("/") + "/")  # noqa: E731  a prefix ends at a path boundary
    owners = [k for k in keys if any(under(p) for p in ((reg.get(k) or {}).get("instrument") or {}).get("url_prefixes") or [])]
    if owners:
        return owners
    return [k for k in keys if not ((reg.get(k) or {}).get("instrument") or {}).get("url_prefixes")]


def key_for(row: dict, by_host: dict[str, list[str]]) -> list[str]:
    """The registered sources a manifest row belongs to: by host first, then by the note that fetched it."""
    note = row.get("note") or ""
    if note_is_foreign(note):  # written by a collector running under another profile: that layer's document
        return []
    if host(row.get("url", "")) in ROUTER_FOLDERS:
        key = ROUTER_FOLDERS[host(row["url"])].get(str(Path(row.get("path") or "").parent))
        return [key] if key else []
    keys = by_host.get(host(row.get("url", "")), [])
    if keys:  # sources sharing one host (each Exa-based source asks api.exa.ai): the note that fetched the row picks among them
        return [key for prefix, key in NOTE_KEYS if note.lower().startswith(prefix) and key in keys][:1] or keys
    if not note_is_ours(note):  # an unmarked search or watch note is the Navy's, the layer that wrote them first
        return []
    note = note.lower()
    return [key for prefix, key in NOTE_KEYS if note.startswith(prefix)][:1]


def status() -> dict:
    reg = registry()
    by_host: dict[str, list[str]] = defaultdict(list)
    for key, hosts in hosts_by_key(reg).items():
        for h in hosts:
            by_host[h].append(key)
    out = {key: {"events": 0, "newest_event": "", "documents": 0, "last_seen": "", "last_hash": "", "hosts": sorted(hosts_by_key(reg)[key])} for key in reg}
    if CORPUS.exists():
        for e in json.loads(CORPUS.read_text(encoding="utf-8"))["events"]:
            slot = out.get(e["provider"])
            if slot is not None:
                slot["events"] += 1
                slot["newest_event"] = max(slot["newest_event"], e["date"])
    unregistered: Counter = Counter()
    theirs = other_agency_hosts()
    rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
    # A backfill row records committed bytes whose retrieval was never recorded; it is not a
    # retrieval, so it does not count a document or move a source's last seen.
    rows = [r for r in rows if r.get("method") != "backfill"]
    rows = [r for r in rows if not (r.get("note") or "").startswith("portal verify")]  # a page check (portals.py) is no collection
    urls = urls_by_key(reg)
    # A page first found by a search and later re-fetched through the hosted browser keeps the search's source.
    by_url = {r["url"]: key_for(r, by_host) for r in rows if r.get("url") and key_for(r, by_host)}
    for row in rows:
        if not kept_page(row):
            continue
        # the prefix owners first (a row claiming a section takes it and leaves the host's rest), then the URL narrowing
        # by_url is read across every layer's rows, so it can name a source this layer does not register (one router
        # endpoint answers the Navy's vendor-job runs and this layer's people searches): only this layer's own count.
        keys = [k for k in narrowed(row.get("url", ""), prefixed(row.get("url", ""), key_for(row, by_host), reg), urls) or by_url.get(row.get("url", ""), []) if k in out]
        # A host is unregistered for this layer when a row of this layer's own collection reaches it and no source owns it.
        if not keys and note_is_ours(row.get("note") or "") and host(row.get("url", "")) not in theirs:
            unregistered[host(row.get("url", ""))] += 1
        # Another agency's layer counts a shared host's row only when its own collector wrote it, or when the source is
        # one it reads from the shared collection (the profile's shared_sources); the Navy owns the unmarked rows.
        if not note_is_ours(row.get("note") or ""):
            keys = [k for k in keys if k in SHARED_SOURCES]
        for key in keys:
            slot = out[key]
            slot["documents"] += 1
            if row["retrieved_at"] > slot["last_seen"]:
                slot["last_seen"], slot["last_hash"] = row["retrieved_at"], row["sha256"]
    return {"sources": out, "unregistered_hosts": dict(sorted(unregistered.items())), "instruments": instruments(reg, out), "open_gaps": open_gaps(reg, out)}


def collector_of(row: dict, slot: dict) -> str:
    if row.get("verification_status") in ("blocked", "restricted"):
        return "blocked"
    return "collecting" if (slot["documents"] or slot["events"]) else "registered_empty"


def instruments(reg: dict[str, dict], out: dict[str, dict]) -> dict[str, dict]:
    """Each source as an instrument: the tag and collector kind its registry block declares beside the collector kind the
    status derives, and what it last collected."""
    return {key: {"status": (row.get("instrument") or {}).get("status", ""),
                  "declared_collector": (row.get("instrument") or {}).get("collector_kind", ""),
                  "collector": collector_of(row, out[key]),
                  "last_collected": out[key]["last_seen"][:10] or out[key]["newest_event"],
                  "cadence_kind": ((row.get("instrument") or {}).get("cadence") or {}).get("kind", "")}
            for key, row in reg.items()}


def open_gaps(reg: dict[str, dict], out: dict[str, dict]) -> dict:
    """Every registered source with no document: its blocker (the registry's access restriction, else the inspected
    example's note), how the registry says to close it, the collector kind and the last recorded attempt."""
    keys = [k for k in reg if not out[k]["documents"]]
    order = [k for k in GAPS_FIRST if k in keys] + sorted(k for k in keys if k not in GAPS_FIRST)
    rows = {}
    for k in order:
        row, ex = reg[k], reg[k].get("inspected_example") or {}
        rows[k] = {"blocker": (row.get("access_restrictions") or "").strip() or (ex.get("note") or "").strip() or "not stated",
                   "close_by": (row.get("instrument") or {}).get("close_by", ""), "collector": collector_of(row, out[k]),
                   "last_attempt": (ex.get("retrieved_at") or "")[:10], "events": out[k]["events"]}
    return {"order": order, "rows": rows}


def problems(matrix: dict, stat: dict, reg: dict[str, dict]) -> list[str]:
    out = []
    grid = {(c["org"], c["family"]) for c in matrix["cells"]}
    for org in matrix["organizations"]:
        for family in matrix["families"]:
            if (org, family) not in grid:
                out.append(f"hole: {org} x {family}")
    if len(grid) != len(matrix["cells"]):
        out.append("a cell is listed twice")
    for c in matrix["cells"]:
        where = f"{c['org']} x {c['family']}"
        if c.get("org") not in matrix["organizations"] or c.get("family") not in matrix["families"]:
            out.append(f"{where}: outside the grid")
        if c.get("sources"):
            for key in c["sources"]:
                if key not in reg:
                    out.append(f"{where}: {key} is not in the registry")
                elif not (stat["sources"][key]["events"] or stat["sources"][key]["documents"]):
                    out.append(f"{where}: {key} has collected nothing")
            if "reason" in c:
                out.append(f"{where}: both sources and a reason")
        elif c.get("reason") not in REASONS or not c.get("note"):
            out.append(f"{where}: empty cell needs a reason from {REASONS} and a note")
        if "node" in c:  # a generated cell
            if bool(c.get("sources")) != (c.get("status") in CELL_STATUSES):
                out.append(f"{where}: a tag exactly when sources speak")
            if bool(c.get("events") or c.get("documents")) != bool(re.match(r"\d{4}-\d{2}-\d{2}", c.get("last_collected") or "")):
                out.append(f"{where}: last_collected dated exactly when something was collected")
            if c.get("node") is None and c.get("reason") != "not_started":
                out.append(f"{where}: a row without a memory node is not_started")
            if c.get("status") == "Live" and not c.get("events"):
                out.append(f"{where}: Live needs an event of the organization's own")
    return out


# ------------------------------------------------------------------ the generated matrix

def org_uid(node: str) -> str:
    return str(uuid.uuid5(NS, f"org:{node}"))


def subtree(root: str, orgs: dict[str, dict]) -> set[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for u, o in orgs.items():
        children[o.get("parent") or ""].append(u)
    out, stack = {root}, [root]
    while stack:
        for ch in children.get(stack.pop(), []):
            if ch not in out:
                out.add(ch)
                stack.append(ch)
    return out


def build_cells(nodes: dict[str, str | None], families: list[str], corpus: dict, positions: list[tuple[str, str, str]], stat: dict,
                wide: dict[str, list[str]], preserved: dict[tuple[str, str], dict], alias: dict[str, str] | None = None) -> list[dict]:
    """One cell per organization and family from the corpus, the roster, the status and the department-wide map; the
    hand-written reason and note stand where the files say nothing."""
    alias = alias or {}
    frozen = corpus["frozen_at"][:10]
    window = (date.fromisoformat(frozen) - timedelta(days=LIVE_DAYS)).isoformat()
    collected = lambda k: k in stat["sources"] and bool(stat["sources"][k]["events"] or stat["sources"][k]["documents"])  # noqa: E731
    by_org: dict[str, list[dict]] = defaultdict(list)
    for e in corpus["events"]:
        by_org[e["org"]].append(e)
    cells = []
    for label, node in nodes.items():
        u = org_uid(node) if node else None
        sub = subtree(u, corpus["orgs"]) if u and u in corpus["orgs"] else set()
        for fam in families:
            if fam == "people":
                evs = [{"provider": src, "date": day} for (o, src, day) in positions if o in sub]
            else:
                evs = [e for o in sub for e in by_org.get(o, []) if e["family"] == fam]
            providers = {e["provider"] for e in evs if collected(e["provider"])}
            # a row with no memory node is nobody's subtree: no source can be said to speak about it, department-wide or not
            sources = sorted(providers | {k for k in wide.get(fam, []) if collected(k)}) if node else []
            newest = max((e["date"][:10] for e in evs), default="")
            old = preserved.get((label, fam)) or preserved.get((alias.get(label, ""), fam)) or {}
            cell: dict = {"org": label, "node": node, "family": fam, "events": len(evs), "documents": 0, "documents_scope": "source", "last_collected": ""}
            if sources:
                cell["sources"] = sources
                cell["documents"] = sum(stat["sources"][k]["documents"] for k in sources)
                cell["last_collected"] = newest or max((stat["sources"][k]["last_seen"][:10] for k in sources), default="")
                cell["status"] = "Live" if evs and newest >= window else "Historical" if evs else "Adjacent"
                if old.get("sources") and old.get("note"):
                    cell["note"] = old["note"]
            elif node is None:
                cell["reason"], cell["note"] = "not_started", "no memory node yet; Stage 2 adds it from a saved official page, and the row's cells are then generated"
            elif old.get("reason"):
                cell["reason"], cell["note"] = old["reason"], old["note"]
            else:
                cell["reason"], cell["note"] = "not_started", f"no event of {label} or the offices under it in the frozen corpus, and no department-wide source feeds this family"
            cells.append(cell)
    return cells


def matrix_cmd(argv: list[str]) -> int:
    nodes = P.get("coverage_org_nodes") or {}
    if not nodes:
        print(f"the {KEY} matrix is written by hand: the profile names no coverage_org_nodes", file=sys.stderr)
        return 1
    assert list(nodes) == list(P["coverage_orgs"]), "coverage_org_nodes and coverage_orgs name the same rows in the same order"
    reg, stat = registry(), status()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    old = json.loads(MATRIX.read_text(encoding="utf-8")) if MATRIX.exists() else {"families": [], "cells": []}
    families = old["families"]
    roster = json.loads((MEMORY / "people.json").read_text(encoding="utf-8")).get("rows", []) if (MEMORY / "people.json").exists() else []
    positions = [(p["org"], p["source"], p["observed_at"]) for r in roster for p in r.get("positions", [])]
    preserved = {(c["org"], c["family"]): c for c in old["cells"]}
    cells = build_cells(nodes, families, corpus, positions, stat, P.get("coverage_department_wide") or {}, preserved,
                        alias={"NIWC Pacific": "NIWC", "NIWC Atlantic": "NIWC"})
    matrix = {"as_of": corpus["frozen_at"][:10], "generated_by": "research/tools/coverage.py matrix",
              "note": ("One row per organization the profile names (coverage_org_nodes) and one column per evidence family. A cell counts the frozen "
                       "corpus's events under the organization's memory node and the offices below it (people: the roster's positions), names the "
                       "registered sources that made them and the department-wide sources of the family, and carries the documents those sources hold "
                       "(documents_scope source: the ledger has no organization tag). Live: an event within 365 days of the corpus freeze; Historical: "
                       "events, none that recent; Adjacent: only a department-wide source speaks. An empty cell keeps the hand-written reason. "
                       "Regenerate with `python research/tools/coverage.py matrix`; `check` validates it."),
              "organizations": list(nodes), "families": families, "cells": cells}
    text = json.dumps(matrix, indent=1, ensure_ascii=False) + "\n"
    found = problems(matrix, stat, reg)
    for p_ in found:
        print("  problem:", p_)
    if "--check" in argv:
        if not MATRIX.exists() or MATRIX.read_text(encoding="utf-8") != text:
            print("coverage_matrix.json differs from a fresh build; run `coverage.py matrix` to regenerate", file=sys.stderr)
            return 1
        print("coverage_matrix.json matches a fresh build")
        return 1 if found else 0
    MATRIX.write_text(text, encoding="utf-8")
    tags = Counter(c.get("status") or c.get("reason") for c in cells)
    print(f"{len(cells)} cells for {len(nodes)} organizations x {len(families)} families -> {MATRIX.relative_to(ROOT)}; {dict(tags)}")
    return 1 if found else 0


def grid_text(matrix: dict, stat: dict) -> str:
    width = max(len(f) for f in matrix["families"])
    lines = [f"{'':{width}}  " + "  ".join(f"{o[:10]:>10}" for o in matrix["organizations"])]
    by = {(c["org"], c["family"]): c for c in matrix["cells"]}
    for family in matrix["families"]:
        row = []
        for org in matrix["organizations"]:
            c = by[(org, family)]
            row.append(f"{len(c['sources']):>10}" if c.get("sources") else f"{c['reason'][:10]:>10}")
        lines.append(f"{family:{width}}  " + "  ".join(row))
    lines.append("(a number is how many registered sources cover the cell; a word is why none does)")
    return "\n".join(lines)


def check(argv: list[str]) -> int:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    stat, reg = status(), registry()
    found = problems(matrix, stat, reg)
    print(grid_text(matrix, stat))
    for p in found:
        print("  problem:", p)
    covered = sum(bool(c.get("sources")) for c in matrix["cells"])
    print(f"{covered} of {len(matrix['cells'])} cells covered; {len(stat['unregistered_hosts'])} unregistered host(s): {sorted(stat['unregistered_hosts'])}")
    return 1 if found else 0


def status_cmd(argv: list[str]) -> int:
    stat = status()
    text = json.dumps(stat, indent=1, sort_keys=True) + "\n"
    if "--check" in argv:
        if not STATUS.exists() or STATUS.read_text(encoding="utf-8") != text:
            print("source_status.json differs from a fresh build; run `coverage.py status` to regenerate", file=sys.stderr)
            return 1
        print("source_status.json matches a fresh build")
        return 0
    STATUS.write_text(text, encoding="utf-8")
    live = [k for k, s in stat["sources"].items() if s["documents"] or s["events"]]
    print(f"{len(live)} of {len(stat['sources'])} registered sources have collected something -> {STATUS.relative_to(ROOT)}")
    return 0


def selfcheck() -> int:
    reg = {"a": {"official_url": "https://a.example/x", "inspected_example": {"url": "https://web.archive.org/web/2020/https://a2.example/y"}},
           "b": {"official_url": "https://b.example/"}}
    assert hosts_by_key(reg) == {"a": {"a.example", "a2.example"}, "b": {"b.example"}}, hosts_by_key(reg)
    by_host = {"a.example": ["a"]}
    assert key_for({"url": "https://a.example/p"}, by_host) == ["a"]
    routed = {"api.routergrowth.com": ["vendor_jobs_routergrowth"]}
    assert key_for({"url": "https://api.routergrowth.com/v1/run", "path": "data/raw/news/search_1.json", "note": "search: q"}, routed) == \
        ["news_articles_exa"], "a news search through the router is news, whichever source registered the router's host"
    assert key_for({"url": "https://api.routergrowth.com/v1/run", "path": "data/raw/vendor_jobs/search_1.json"}, routed) == ["vendor_jobs_routergrowth"]
    assert key_for({"url": "https://z.example/p", "note": "remarks watch: conference page"}, by_host) == ["conference_pages_exa"]
    assert key_for({"url": "https://z.example/p", "note": "live page via Browserbase"}, by_host) == []
    exa = {"api.exa.ai": ["conference_pages_exa", "news_articles_exa", "linkedin_profiles_exa"]}
    assert key_for({"url": "https://api.exa.ai/search", "note": "office people: pms:404"}, exa) == ["linkedin_profiles_exa"]
    assert key_for({"url": "https://api.exa.ai/search", "note": "no known prefix"}, exa) == exa["api.exa.ai"], "no note to go by: every source on the host"
    assert key_for({"url": "https://z.example/p", "note": "news search: PMW 770 program manager: headline"}, by_host) == ["news_articles_exa"]
    urls = {"chart": {"https://d.example/c.pdf", "https://p.example/org"}, "fc": {"https://d.example/f.xlsx", "https://p.example/osbp"},
            "talks": {"https://p.example/leaders"}}
    assert narrowed("https://d.example/f.xlsx", ["chart", "fc"], urls) == ["fc"], "one download host, the file's own source"
    assert narrowed("https://p.example/osbp/", ["chart", "fc", "talks"], urls) == ["fc"] and narrowed("https://p.example/osbp/x", ["fc", "talks"], urls) == ["fc"]
    assert narrowed("https://p.example/other", ["fc", "talks"], urls) == ["fc", "talks"], "a section no source registered stays shared"
    matrix = {"organizations": ["X"], "families": ["f", "g"],
              "cells": [{"org": "X", "family": "f", "sources": ["a"]}, {"org": "X", "family": "g", "reason": "blocked", "note": "why"}]}
    stat = {"sources": {"a": {"events": 1, "documents": 0}, "b": {"events": 0, "documents": 0}}}
    assert problems(matrix, stat, reg) == []
    bad = {**matrix, "cells": [{"org": "X", "family": "f", "sources": ["b", "zz"]}, {"org": "X", "family": "g", "reason": "tired"}]}
    found = problems(bad, stat, reg)
    assert len(found) == 3 and any("collected nothing" in p for p in found) and any("not in the registry" in p for p in found), found
    assert problems({**matrix, "cells": matrix["cells"][:1]}, stat, reg) == ["hole: X x g"]
    assert "blocked" in grid_text(matrix, stat)
    # A source that claims URL prefixes takes them from the host's other sources and nothing else
    reg2 = {"site": {"official_url": "https://d.mil/"}, "sb": {"official_url": "https://d.mil/work/small-business", "instrument": {"url_prefixes": ["https://d.mil/work/small-business"]}}}
    assert prefixed("https://d.mil/work/small-business/", ["site", "sb"], reg2) == ["sb"] and prefixed("https://d.mil/news/x", ["site", "sb"], reg2) == ["site"]
    assert prefixed("https://d.mil/work/small-businesses/overview", ["site", "sb"], reg2) == ["site"], "a prefix ends at a path boundary"
    assert prefixed("https://d.mil/news/x", ["site"], reg2) == ["site"]
    # The generated cells: Live needs the organization's own recent event, a department-wide source alone is Adjacent, no node is not_started
    corpus = {"frozen_at": "2026-09-26T00:00:00Z",
              "orgs": {org_uid("command:a"): {"parent": ""}, org_uid("peo:a1"): {"parent": org_uid("command:a")}, org_uid("command:b"): {"parent": ""}},
              "events": [{"org": org_uid("peo:a1"), "family": "notice", "provider": "sam", "date": "2026-08-01"},
                         {"org": org_uid("command:b"), "family": "notice", "provider": "sam", "date": "2024-01-01"}]}
    stat2 = {"sources": {"sam": {"events": 2, "documents": 5, "last_seen": "2026-09-01T00:00:00Z"}, "gao": {"events": 0, "documents": 3, "last_seen": "2026-09-02T00:00:00Z"},
                         "idle": {"events": 0, "documents": 0, "last_seen": ""}}}
    cells = build_cells({"A": "command:a", "B": "command:b", "C": None}, ["notice", "oversight", "people"], corpus,
                        [(org_uid("command:a"), "sam", "2026-05-01")], stat2, {"oversight": ["gao", "idle"]},
                        {("B", "people"): {"reason": "blocked", "note": "no roster"}})
    by = {(c["org"], c["family"]): c for c in cells}
    assert (by[("A", "notice")]["status"], by[("A", "notice")]["events"], by[("A", "notice")]["documents"], by[("A", "notice")]["last_collected"]) == ("Live", 1, 5, "2026-08-01"), by[("A", "notice")]
    assert by[("B", "notice")]["status"] == "Historical" and by[("A", "oversight")]["status"] == "Adjacent" and by[("A", "oversight")]["sources"] == ["gao"]
    assert by[("A", "people")]["status"] == "Live" and by[("A", "people")]["events"] == 1
    assert by[("B", "people")]["reason"] == "blocked" and by[("C", "notice")]["reason"] == "not_started" and by[("C", "oversight")]["node"] is None
    reg3 = {"sam": {}, "gao": {}, "idle": {}}
    assert problems({"organizations": ["A", "B", "C"], "families": ["notice", "oversight", "people"], "cells": cells}, stat2, reg3) == []
    assert "a row without a memory node is not_started" in " ".join(problems({"organizations": ["C"], "families": ["notice"], "cells": [{**by[("C", "notice")], "reason": "blocked"}]}, stat2, reg3))
    assert "Live needs an event" in " ".join(problems({"organizations": ["A"], "families": ["oversight"], "cells": [{**by[("A", "oversight")], "status": "Live"}]}, stat2, reg3))
    # The instruments and the open gaps read the registry's blocks beside the status
    reg4 = {"a": {"verification_status": "verified", "instrument": {"status": "Live", "collector_kind": "collecting", "cadence": {"kind": "standing"}}},
            "seaport_nxg": {"verification_status": "blocked", "access_restrictions": "no connection", "instrument": {"status": "Live", "collector_kind": "blocked", "close_by": "a holder's export"}},
            "zz_new": {"verification_status": "not_inspected", "inspected_example": {"note": "not yet run", "retrieved_at": ""}, "instrument": {"collector_kind": "registered_empty", "close_by": "run it"}}}
    out4 = {"a": {"events": 1, "documents": 2, "last_seen": "2026-09-01T00:00:00Z", "newest_event": "2026-08-01"},
            "seaport_nxg": {"events": 0, "documents": 0, "last_seen": "", "newest_event": ""}, "zz_new": {"events": 0, "documents": 0, "last_seen": "", "newest_event": ""}}
    inst = instruments(reg4, out4)
    assert inst["a"] == {"status": "Live", "declared_collector": "collecting", "collector": "collecting", "last_collected": "2026-09-01", "cadence_kind": "standing"}
    gaps = open_gaps(reg4, out4)
    assert gaps["order"] == ["seaport_nxg", "zz_new"] and gaps["rows"]["seaport_nxg"]["blocker"] == "no connection" and gaps["rows"]["zz_new"]["blocker"] == "not yet run"
    assert gaps["rows"]["seaport_nxg"]["collector"] == "blocked" and gaps["rows"]["zz_new"]["collector"] == "registered_empty" and gaps["rows"]["zz_new"]["close_by"] == "run it"
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"check": check, "status": status_cmd, "matrix": matrix_cmd}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

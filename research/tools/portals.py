#!/usr/bin/env python3
"""The source by agency matrix of innovation and small-business portals: per portal URL its contact, cadence,
Live/Historical/Adjacent status, last collection and the organization nodes it feeds.

research/sources/portal_registry.json holds the rows as the reviewer's list states them. This tool checks each page
and builds the matrix; a check is never a collection. A row whose collector is null is an empty collector: its line
shows no last collection and no node fed, whatever its page answered. A collector's last collection is its source's
last retrieval (the layer's source_status.json) and the nodes it feeds are the organizations of the corpus events
that source produced, so neither is ever stated by hand.

  python research/tools/portals.py verify            # fetch each row's page (network), skipping one checked in the last 7 days
  python research/tools/portals.py build [--check]   # write research/sources/portal_matrix.{json,md}, or fail if either would change
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import MANIFEST, PROFILES  # noqa: E402
from agency_layers_sql import uid  # noqa: E402
from fetch import fetch  # noqa: E402

REGISTRY = ROOT / "research" / "sources" / "portal_registry.json"
MATRIX = ROOT / "research" / "sources" / "portal_matrix.json"
MATRIX_MD = ROOT / "research" / "sources" / "portal_matrix.md"
NOTE = "portal verify"  # coverage.py leaves these rows out: a page check is no collection
TIERS, STATUSES, KINDS = ("P0", "P1", "adjacent"), ("Live", "Historical", "Adjacent"), ("standing", "episodic", "")
RECHECK = timedelta(days=7)


def layer(agency: str) -> Path:
    return ROOT / "research" if agency == "navy" else ROOT / "research" / "agencies" / agency


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def seed_nodes(agency: str) -> dict[str, dict]:
    return {n["id"]: n for n in read_json(layer(agency) / "memory" / "organization_seed.json", {"nodes": []})["nodes"]}


def problems(reg: dict) -> list[str]:
    out = []
    keys = [r["key"] for r in reg["rows"]]
    out += [f"{k}: key repeated" for k in sorted({k for k in keys if keys.count(k) > 1})]
    for r in reg["rows"]:
        k, agency = r["key"], r["agency"]
        if r["tier"] not in TIERS or r["status"] not in STATUSES or r["kind"] not in KINDS:
            out.append(f"{k}: tier, status or kind outside {TIERS}, {STATUSES}, {KINDS}")
        if r["url"] is not None and not r["url"].startswith("https://"):
            out.append(f"{k}: url is neither null nor https")
        if r["tier"] == "P1" and agency not in PROFILES:
            out.append(f"{k}: a P1 row for {agency}, whose profile is not in the PR (hold it instead)")
        if agency not in PROFILES and (r["org_nodes"] or r["collector"] or r["registry_key"]):
            out.append(f"{k}: {agency} has no layer, so the row can feed no node, run no collector and name no source")
        if agency in PROFILES:
            nodes = seed_nodes(agency)
            out += [f"{k}: {n} is not a node of the {agency} seed" for n in r["org_nodes"] if n not in nodes]
            sources = {s["source_key"] for s in read_json(layer(agency) / "sources" / "source_registry.json", [])}
            if r["registry_key"] and r["registry_key"] not in sources:
                out.append(f"{k}: {r['registry_key']} is not in the {agency} source registry")
        if r["collector"] and not r["registry_key"]:
            out.append(f"{k}: a collector with no registered source has no retrieval to date it by")
    out += [f"held {h['agency']}: its profile is in the PR, so it takes rows" for h in reg["held"] if h["agency"] in PROFILES]
    navy = seed_nodes("navy")
    out += [f"navy portfolio {p['name']}: {p['node']} is not a node of the navy seed" for p in reg["navy_portfolios"] if p["node"] and p["node"] not in navy]
    return out


def latest_checks(rows: list[dict]) -> dict[str, dict]:
    """The newest page check of each URL."""
    out = {}
    for row in rows:
        if (row.get("note") or "").startswith(NOTE) and row.get("url") and row["retrieved_at"] >= out.get(row["url"], {}).get("retrieved_at", ""):
            out[row["url"]] = row
    return out


def manifest_rows() -> list[dict]:
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()] if MANIFEST.exists() else []


def verify(argv: list[str]) -> int:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    checked = latest_checks(manifest_rows())
    since = (datetime.now(timezone.utc) - RECHECK).strftime("%Y-%m-%dT%H:%M:%SZ")
    urls = dict.fromkeys(r["url"] for r in reg["rows"] if r["url"])
    # a page that answered is checked again after a week; one that refused, on every run
    todo = [u for u in urls if checked.get(u, {}).get("retrieved_at", "") < since or page_state(checked.get(u)) != "200"]
    first_key = {r["url"]: r["key"] for r in reversed(reg["rows"]) if r["url"]}
    failed = []
    with MANIFEST.open("a", encoding="utf-8") as handle:
        for url in todo:
            row = fetch(url, "direct", None, f"{NOTE}: {first_key[url]}")
            if "CERTIFICATE_VERIFY_FAILED" in (row.get("error") or ""):  # DoD PKI hosts: the unverified retry is recorded as such
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                row = fetch(url, "direct", None, f"{NOTE}: {first_key[url]}", insecure=True)
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            if row.get("status") != 200 or row.get("content_status"):
                failed.append(url)
            print(row.get("status"), row.get("content_status") or row.get("error") or "", url)
            time.sleep(1.0)
    print(f"{len(urls)} portal page(s); {len(todo)} checked now, {len(failed)} not answered with the page")
    if failed and os.environ.get("BROWSERBASE_API_KEY"):  # hosts that refuse this machine answer a hosted US browser
        import browserbase_fetch
        browserbase_fetch.main(failed, {u: f"{NOTE}: {first_key[u]}" for u in failed})
    return 0


def page_text(check: dict) -> str:
    path = ROOT / check["path"] if check.get("path") else None
    if not path or not path.exists():
        return ""
    return html.unescape(path.read_bytes().decode("utf-8", "replace"))


def page_state(check: dict | None) -> str:
    if not check:
        return "not checked"
    if check.get("status") == 200 and check.get("sha256"):
        return "refused (bot wall)" if check.get("content_status") else "200"
    return f"{check.get('status') or 'no answer'}: {check.get('error', '')}".strip()


def on_page(text: str, contact: str) -> bool:
    digits = re.sub(r"\D", "", contact)
    if "@" not in contact and len(digits) >= 10:  # a phone number, however the page spaces it
        return bool(re.search(r"\D{0,3}".join(digits), text))
    return contact.lower() in text.lower()


def names_node(text: str, node: dict) -> bool:
    names = [node.get("name") or ""] + [a["text"] if isinstance(a, dict) else str(a) for a in node.get("aliases") or []]
    for name in filter(None, names):
        flags = 0 if len(name) <= 5 else re.IGNORECASE  # a short acronym only as written
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text, flags):
            return True
    return False


def row_out(row: dict, check: dict | None, text: str, nodes: dict[str, dict], last_seen: str, fed: list[str]) -> dict:
    """One matrix line. last_seen and fed are the collector's; an empty collector drops both."""
    collected = bool(row["collector"])
    return {**{k: row[k] for k in ("key", "tier", "agency", "program", "role", "url", "contacts", "channels", "cadence", "kind",
                                   "due", "entry_types", "envelope", "status", "collector", "registry_key", "org_nodes")},
            "listed": row.get("listed", True), "agency_in_pr": row["agency"] in PROFILES,
            "page": page_state(check) if row["url"] else "no URL given", "checked_at": (check or {}).get("retrieved_at", ""),
            "contacts_on_page": [c for c in row["contacts"] if text and on_page(text, c)],
            "org_nodes_on_page": [n for n in row["org_nodes"] if text and n in nodes and names_node(text, nodes[n])],
            "last_collected": last_seen if collected else "", "org_nodes_fed": fed if collected else []}


def fed_nodes(agency: str, source: str, nodes: dict[str, dict]) -> list[str]:
    """The organizations of the corpus events a source produced."""
    by_uid = {uid("org", n): n for n in nodes}
    events = read_json(layer(agency) / "results" / "corpus.json", {"events": []})["events"]
    return sorted({by_uid[e["org"]] for e in events if e["provider"] == source and e.get("org") in by_uid})


def matrix(reg: dict, rows: list[dict]) -> dict:
    checks = latest_checks(rows)
    out = []
    for r in reg["rows"]:
        nodes = seed_nodes(r["agency"]) if r["agency"] in PROFILES else {}
        status = read_json(layer(r["agency"]) / "sources" / "source_status.json", {"sources": {}})["sources"] if nodes else {}
        last_seen = status.get(r["registry_key"] or "", {}).get("last_seen", "")
        fed = fed_nodes(r["agency"], r["registry_key"], nodes) if r["collector"] else []
        check = checks.get(r["url"]) if r["url"] else None
        text = page_text(check) if check and page_state(check) == "200" else ""
        out.append(row_out(r, check, text, nodes, last_seen, fed))
    return {"stated_by": reg["stated_by"], "rows": out, "held": reg["held"], "navy_portfolios": reg["navy_portfolios"],
            "summary": {"rows": len(out), "empty_collectors": sum(not r["collector"] for r in out),
                        "pages_answered": sum(r["page"] == "200" for r in out),
                        "agencies_without_a_layer": sorted({r["agency"] for r in out if not r["agency_in_pr"] and r["agency"] != "all"})}}


def cell(values) -> str:
    return "; ".join(values).replace("|", "/") if values else ""


def markdown(m: dict) -> str:
    s = m["summary"]
    lines = ["# Source by agency matrix: innovation and small-business portals", "",
             f"Rows as stated by the {m['stated_by']}. {s['rows']} rows, {s['empty_collectors']} empty collectors, "
             f"{s['pages_answered']} pages answered on the last check. Agencies with rows but no layer in the PR: "
             f"{', '.join(s['agencies_without_a_layer']) or 'none'}. An empty collector claims no coverage; a page check is no collection.", "",
             "| Tier | Agency | Program | URL | Contact (on page) | Cadence | Kind | Status | Page | Collector | Last collected | Org nodes fed |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in m["rows"]:
        contacts = cell(f"{c}{' (found)' if c in r['contacts_on_page'] else ''}" for c in r["contacts"]) if r["contacts"] else ""
        fed = f"{len(r['org_nodes_fed'])}: {cell(r['org_nodes_fed'][:6])}{' ...' if len(r['org_nodes_fed']) > 6 else ''}" if r["org_nodes_fed"] else "none"
        agency = r["agency"] + ("" if r["agency_in_pr"] or r["agency"] == "all" else " (no layer)")
        lines.append(f"| {r['tier']} | {agency} | {r['program'].replace('|', '/')}{'' if r['listed'] else ' (not on the list)'} | {r['url'] or ''} | "
                     f"{contacts} | {r['cadence']} | {r['kind']} | {r['status']} | {r['page']} | {r['collector'] or 'empty'} | "
                     f"{r['last_collected'] or ''} | {fed} |")
    lines += ["", "## Entry types, envelopes and due dates as stated", ""]
    lines += [f"- {r['key']}: {cell(r['entry_types']) or 'no entry types stated'}"
              f"{'; envelope ' + r['envelope'] if r['envelope'] else ''}{'; due ' + r['due'] if r['due'] else ''}"
              for r in m["rows"] if r["entry_types"] or r["envelope"] or r["due"]]
    lines += ["", "## Org nodes each row is meant to feed, and which its page names", ""]
    lines += [f"- {r['key']}: {cell(r['org_nodes'])}; named on the page: {cell(r['org_nodes_on_page']) or 'none'}"
              for r in m["rows"] if r["org_nodes"]]
    lines += ["", "## Held until the agency's profile is in the PR", ""]
    lines += [f"- {h['tier']} {h['agency']}: {h['program']}, {cell(h['urls'])}{', ' + cell(h['contacts']) if h['contacts'] else ''}"
              f"{'. ' + h['note'] if h['note'] else ''}" for h in m["held"]]
    lines += ["", "## Navy portfolios and their organization nodes", "", "| Portfolio | Node |", "|---|---|"]
    lines += [f"| {p['name']} | {p['node'] or 'no node yet'} |" for p in m["navy_portfolios"]]
    return "\n".join(lines) + "\n"


def build(argv: list[str]) -> int:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    found = problems(reg)
    if found:
        print("\n".join(found), file=sys.stderr)
        return 1
    m = matrix(reg, manifest_rows())
    texts = {MATRIX: json.dumps(m, indent=1, sort_keys=True) + "\n", MATRIX_MD: markdown(m)}
    if "--check" in argv:
        stale = [p.name for p, t in texts.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        if stale:
            print(f"{', '.join(stale)} differ from a fresh build; run `portals.py build` to regenerate", file=sys.stderr)
            return 1
        print("portal_matrix matches a fresh build")
        return 0
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8")
    s = m["summary"]
    print(f"{s['rows']} row(s), {s['empty_collectors']} empty collector(s), {s['pages_answered']} page(s) answered -> {MATRIX.relative_to(ROOT)}")
    return 0


def selfcheck() -> int:
    row = {"key": "k", "tier": "P0", "agency": "navy", "program": "p", "role": "portal", "url": "https://x.example/", "contacts": ["a@x.mil", "(703) 767-3436"],
           "channels": [], "cadence": "", "kind": "", "due": "", "entry_types": [], "envelope": "", "status": "Live",
           "collector": None, "registry_key": "s", "org_nodes": ["command:onr"]}
    nodes = {"command:onr": {"name": "Office of Naval Research", "aliases": [{"text": "ONR"}]}}
    check = {"status": 200, "sha256": "h", "retrieved_at": "2026-09-28T00:00:00Z", "url": "https://x.example/"}
    text = "Write to A@X.MIL or call 703.767.3436; the onr page"
    out = row_out(row, check, text, nodes, "2026-09-27T00:00:00Z", ["command:onr"])
    assert out["last_collected"] == "" and out["org_nodes_fed"] == [], "an empty collector claims no collection, whatever its source holds"
    assert out["contacts_on_page"] == ["a@x.mil", "(703) 767-3436"] and out["org_nodes_on_page"] == [], "ONR only as written"
    assert row_out(row, check, "ONR", nodes, "", [])["org_nodes_on_page"] == ["command:onr"]
    ran = row_out({**row, "collector": "sbir.py"}, check, "", nodes, "2026-09-27T00:00:00Z", ["command:onr"])
    assert ran["last_collected"] == "2026-09-27T00:00:00Z" and ran["org_nodes_fed"] == ["command:onr"]
    assert page_state({**check, "content_status": "rejected_stub"}) == "refused (bot wall)" and page_state(None) == "not checked"
    assert page_state({"status": 403, "error": "HTTP 403"}) == "403: HTTP 403"
    assert latest_checks([{**check, "note": "portal verify: k"}, {**check, "note": "news search", "retrieved_at": "2026-09-29T00:00:00Z"}]) == \
        {"https://x.example/": {**check, "note": "portal verify: k"}}, "only page checks count"
    reg = {"rows": [row, {**row, "key": "k2", "tier": "P1", "agency": "epa", "registry_key": None, "org_nodes": []},
                    {**row, "key": "k3", "agency": "ussocom", "collector": "x.py", "registry_key": None, "org_nodes": ["command:onr"]}],
           "held": [{"agency": "navy"}], "navy_portfolios": [{"name": "PAE Nowhere", "node": "pae:nowhere"}]}
    found = problems(reg)
    assert any("k2: a P1 row for epa" in p for p in found) and any("k3: ussocom has no layer" in p for p in found), found
    assert any("held navy" in p for p in found) and any("pae:nowhere" in p for p in found), found
    real = problems(json.loads(REGISTRY.read_text(encoding="utf-8")))
    assert real == [], real
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"verify": verify, "build": build}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""AI use cases the agency reports in the federal AI Use Case Inventory, read into dated rows the loader emits as
records.

OMB publishes each year's inventory as CSV on GitHub. A row names the use case, the bureau reporting it, the problem
it solves, its stage (pre-deployment, pilot, deployed, retired), the vendor where one was bought, and whether the
system holds an authority to operate. The contact column is an agency mailbox (every DHS row gives ai@hq.dhs.gov),
so the file places workflows and vendors under a component, not people. `sweep --fetch` saves the CSV, then the
file's latest commit, whose date is when the file as saved was published; `build` reads both back and writes
events/ai_use_cases.json without touching the network. The consolidated COTS file (department-wide office tools, no
bureau) is not read.

    python research/tools/ai_inventory.py sweep [--fetch] [--refresh]
    python research/tools/ai_inventory.py build [--check]
    python research/tools/ai_inventory.py --selfcheck
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import MANIFEST, ROOT, fetch, kept_page  # noqa: E402
from lrae_package import manifest_rows, saved  # noqa: E402
from agency import EVENTS as EVENTS_DIR, MEMORY, P, NOTE_TAG  # noqa: E402

YEAR = "2025"
REPO = f"ombegov/{YEAR}-Federal-Agency-AI-Use-Case-Inventory"
FILE = f"Data/{YEAR}_individually_reported_AI_use_cases.csv"
CSV_URL = f"https://raw.githubusercontent.com/{REPO}/main/{FILE}"
COMMITS_URL = f"https://api.github.com/repos/{REPO}/commits?path={FILE}&per_page=1"
EVENTS = EVENTS_DIR / "ai_use_cases.json"


def bureau_code(row: dict) -> str:
    """The bureau's abbreviation: "CBP", "GSFC: Goddard Space Flight Center", "PNNL - Pacific Northwest ..."."""
    return re.split(r":| - ", row.get("agency_bureau") or "", maxsplit=1)[0].strip()


def ours(row: dict, agency: dict) -> bool:
    """A layer for a whole department takes every bureau's rows; a subtier layer (NOAA in Commerce) only its own."""
    if row.get("agency") != agency["toptier_abbreviation"]:
        return False
    return agency["subtier_abbreviation"] == agency["toptier_abbreviation"] or bureau_code(row) == agency["subtier_abbreviation"]


def bureau_nodes(nodes: list[dict]) -> dict[str, str]:
    """Bureau abbreviation -> the seed command whose id or alias is that abbreviation."""
    out: dict[str, str] = {}
    for node in (n for n in nodes if n["type"] == "command"):
        for name in [node["id"].split(":", 1)[-1]] + [a["text"] if isinstance(a, dict) else str(a) for a in node.get("aliases") or []]:
            out.setdefault(name.upper(), node["id"])
    return out


def published_on(manifest: list[dict], csv_row: dict) -> str:
    """The date of the file's latest commit, read from a commits page taken no earlier than the CSV (an earlier page
    could miss a commit that changed the saved rows); without one, the day the CSV was saved."""
    page = saved(manifest, lambda m: m.get("url") == COMMITS_URL and m["retrieved_at"] >= csv_row["retrieved_at"])
    commits = json.loads((ROOT / page["path"]).read_bytes()) if page else []
    return commits[0]["commit"]["committer"]["date"][:10] if commits else csv_row["retrieved_at"][:10]


def row_of(use: dict, csv_row: dict, published: str, nodes: dict[str, str], department: str) -> dict:
    field = lambda key: " ".join((use.get(key) or "").split())  # noqa: E731
    stage, vendor, bureau = field("development_stage"), field("vendor_name"), bureau_code(use)
    ato = field("have_ato") + (f" ({field('system_name_ato')})" if field("system_name_ato") else "")
    return {
        # A retired use case states no current priority; it stays in the file for review and the loader skips it.
        "claim_key": f"ai_inventory:{YEAR}:{use['id']}", "event_type": None if stage == "Retired" else "capability_priority",
        "published": published, "title": f"{field('use_case_name')}: {bureau or 'agency'} AI use case, {stage.lower() or 'stage unstated'}"[:200],
        "body": "; ".join(p for p in (field("problem_solved"), f"stage {stage}" if stage else "",
                                      f"operational {field('operational_date')[:10]}" if field("operational_date") else "",
                                      f"vendor {vendor}" if vendor else "", f"sourcing {field('contracting_usage')}" if field("contracting_usage") else "",
                                      f"authority to operate {ato}" if ato else "", f"topic {field('topic_area')}" if field("topic_area") else "",
                                      f"technique {field('classification')}" if field("classification") else "",
                                      field("is_high_impact"), f"reported in the {YEAR} federal AI Use Case Inventory as {use['id']}") if p),
        "section": "mission_priorities", "url": CSV_URL,
        "sha256": csv_row["sha256"], "retrieved_at": csv_row["retrieved_at"], "path": csv_row["path"],
        "excerpt": (field("problem_solved") or field("use_case_name"))[:600], "uic": "",
        # `node` is where the loader files the row: the command the bureau names, else the layer's own node.
        "data": {"use_case_id": use["id"], "bureau": field("agency_bureau"), "node": nodes.get(bureau.upper(), department),
                 "stage": stage, "vendor": vendor, "sourcing": field("contracting_usage"), "ato": ato,
                 "operational": field("operational_date")[:10], "high_impact": field("is_high_impact"),
                 "topic": field("topic_area"), "technique": field("classification")},
    }


def events(manifest: list[dict], agency: dict, seed_nodes: list[dict]) -> dict:
    csv_row = saved(manifest, lambda m: m.get("url") == CSV_URL)
    if csv_row is None:
        return {"source_key": "ai_use_case_inventory", "rows": []}
    text = (ROOT / csv_row["path"]).read_bytes().decode("utf-8-sig", errors="replace")
    published, nodes = published_on(manifest, csv_row), bureau_nodes(seed_nodes)
    rows = [row_of(u, csv_row, published, nodes, agency["node"]) for u in csv.DictReader(io.StringIO(text)) if ours(u, agency)]
    return {"source_key": "ai_use_case_inventory", "rows": sorted(rows, key=lambda r: r["claim_key"])}


def dumps(payload: dict) -> str:
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def sweep(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ai_inventory.py sweep")
    ap.add_argument("--fetch", action="store_true", help="take the file from GitHub; without it, only report what is saved")
    ap.add_argument("--refresh", action="store_true", help="take it again although a copy is saved")
    args = ap.parse_args(argv)
    # ponytail: taken once (one file serves every layer); OMB corrects it rarely (the 2025 file last changed
    # 2026-04-14), so --refresh re-takes it and the next year's inventory is a new YEAR.
    have = saved(manifest_rows(), lambda m: m.get("url") == CSV_URL)
    if not args.fetch or (have and not args.refresh):
        print(f"{FILE}: {'saved ' + have['retrieved_at'] if have else 'not saved'}")
        return 0
    with MANIFEST.open("a", encoding="utf-8") as handle:
        for url, what in ((CSV_URL, "use cases"), (COMMITS_URL, "latest commit of the use case file")):  # CSV first: see published_on
            row = fetch(url, "direct", None, f"federal AI Use Case Inventory {YEAR}{NOTE_TAG}: {what}")
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            print(row.get("status"), row.get("size"), url)
            if not kept_page(row):
                return 1
    return 0


def build(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ai_inventory.py build")
    ap.add_argument("--check", action="store_true", help="exit 1 if the saved file differs from a fresh build")
    args = ap.parse_args(argv)
    seed = json.loads((MEMORY / "organization_seed.json").read_text(encoding="utf-8")).get("nodes", [])
    payload = events(manifest_rows(), P["agency"], seed)
    text = dumps(payload)
    stages = Counter(r["data"]["stage"] or "unstated" for r in payload["rows"])
    placed = Counter(r["data"]["node"] for r in payload["rows"])
    print(f"{len(payload['rows'])} AI use case(s) {dict(sorted(stages.items()))}; by node {dict(placed.most_common())}")
    if args.check:
        if not EVENTS.exists() or EVENTS.read_text(encoding="utf-8") != text:
            print(f"{EVENTS.relative_to(ROOT)} differs from a fresh build; run without --check to regenerate", file=sys.stderr)
            return 1
        return 0
    EVENTS.write_text(text, encoding="utf-8")
    print(f"written to {EVENTS.relative_to(ROOT)}")
    return 0


# Rows of the 2025 file as saved 2026-09-29, text shortened; the NOAA and Census rows test the subtier filter.
FIXTURE = [
    {"agency": "DHS", "id": "DHS-2543", "use_case_name": "AI Security and Monitoring", "agency_bureau": "USCIS", "development_stage": "Deployed",
     "is_high_impact": "Presumed High-Impact, but Not High-impact", "topic_area": "Cybersecurity", "classification": "Generative AI",
     "problem_solved": "Organizations adopting AI face a number of risks from data leaks, shadow AI, and unsecured outputs.",
     "operational_date": "2025-03-31 00:00:00", "contracting_usage": "Vendor Purchased", "vendor_name": "Lasso", "have_ato": "Yes", "system_name_ato": "ESS"},
    {"agency": "DHS", "id": "DHS-419", "use_case_name": "AdaptiveMFA", "agency_bureau": "MGMT", "development_stage": "Deployed",
     "is_high_impact": "Not High-impact", "topic_area": "Admin Functions", "classification": "Classical ML",
     "problem_solved": "Enhances security and the user experience", "operational_date": "2025-03-30 00:00:00",
     "contracting_usage": "Contracting and In House", "vendor_name": "Okta", "have_ato": "Yes", "system_name_ato": "DHSAuthPortal"},
    {"agency": "DHS", "id": "DHS-165", "use_case_name": "Automated Data Annotation", "agency_bureau": "CBP", "development_stage": "Retired"},
    {"agency": "DOC", "id": "DOC-16", "use_case_name": "Community-based Messaging with LLM", "agency_bureau": "NOAA", "development_stage": ""},
    {"agency": "DOC", "id": "DOC-5", "use_case_name": "Real Time Classification for the Economic Census", "agency_bureau": "Census"},
]
SEED = [{"id": "agency:dhs", "type": "agency", "name": "HOMELAND SECURITY, DEPARTMENT OF"},
        {"id": "command:uscis", "type": "command", "name": "U.S. CITIZENSHIP AND IMMIGRATION SERVICES", "aliases": ["USCIS"]},
        {"id": "command:cbp", "type": "command", "name": "U.S. CUSTOMS AND BORDER PROTECTION", "aliases": [{"text": "CBP"}]},
        {"id": "command:cwmd", "type": "command", "name": "Countering Weapons of Mass Destruction", "aliases": []}]
DHS = {"toptier_abbreviation": "DHS", "subtier_abbreviation": "DHS", "node": "agency:dhs"}
NOAA = {"toptier_abbreviation": "DOC", "subtier_abbreviation": "NOAA", "node": "agency:noaa"}


def selfcheck() -> int:
    assert [bureau_code({"agency_bureau": b}) for b in ("CBP", "GSFC: Goddard Space Flight Center", "PNNL - Pacific Northwest (SC43 OIM)")] == ["CBP", "GSFC", "PNNL"]
    assert [u["id"] for u in FIXTURE if ours(u, DHS)] == ["DHS-2543", "DHS-419", "DHS-165"], "a department layer takes every bureau"
    assert [u["id"] for u in FIXTURE if ours(u, NOAA)] == ["DOC-16"], "a subtier layer takes only its own bureau"
    assert bureau_nodes(SEED) == {"USCIS": "command:uscis", "CBP": "command:cbp", "CWMD": "command:cwmd"}, "id tail and aliases"
    with tempfile.TemporaryDirectory() as tmp:
        table, log = Path(tmp) / "table.csv", Path(tmp) / "commits"
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(FIXTURE[0]) + ["agency"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(FIXTURE)
        table.write_text("﻿" + out.getvalue(), encoding="utf-8")
        log.write_text(json.dumps([{"sha": "3c225ba843", "commit": {"committer": {"date": "2026-04-14T14:33:21Z"}}}]))
        csv_row = {"url": CSV_URL, "status": 200, "path": str(table), "sha256": "aaa", "retrieved_at": "2026-09-29T05:00:00Z"}
        commit_row = lambda at: {"url": COMMITS_URL, "status": 200, "path": str(log), "sha256": "ccc", "retrieved_at": at}  # noqa: E731
        payload = events([csv_row, commit_row("2026-09-29T05:00:01Z")], DHS, SEED)
        assert published_on([commit_row("2026-09-28T00:00:00Z"), csv_row], csv_row) == "2026-09-29", \
            "a commits page older than the CSV is not read: the CSV is dated the day it was saved"
        assert events([], DHS, SEED) == {"source_key": "ai_use_case_inventory", "rows": []}
    rows = {r["data"]["use_case_id"]: r for r in payload["rows"]}
    assert [r["claim_key"] for r in payload["rows"]] == ["ai_inventory:2025:DHS-165", "ai_inventory:2025:DHS-2543", "ai_inventory:2025:DHS-419"]
    lasso = rows["DHS-2543"]
    assert lasso["published"] == "2026-04-14" and lasso["event_type"] == "capability_priority" and lasso["data"]["node"] == "command:uscis"
    assert lasso["title"] == "AI Security and Monitoring: USCIS AI use case, deployed"
    assert "vendor Lasso" in lasso["body"] and "authority to operate Yes (ESS)" in lasso["body"] and "operational 2025-03-31" in lasso["body"]
    assert lasso["excerpt"].startswith("Organizations adopting AI") and lasso["sha256"] == "aaa"
    assert rows["DHS-419"]["data"]["node"] == "agency:dhs", "a bureau the seed has no command for sits under the layer's node"
    assert rows["DHS-165"]["event_type"] is None and rows["DHS-165"]["data"]["node"] == "command:cbp", "a retired use case is kept, not loaded"
    assert list(lasso) == ["claim_key", "event_type", "published", "title", "body", "section", "url", "sha256",
                           "retrieved_at", "path", "excerpt", "uic", "data"], "the shape the loader reads"
    print("ai_inventory selfcheck ok")
    return 0


COMMANDS = {"sweep": sweep, "build": build}

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

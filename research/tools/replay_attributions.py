#!/usr/bin/env python3
"""Replay the reviewed attribution examples through the production resolver.

    python research/tools/replay_attributions.py
    python research/tools/replay_attributions.py --resolver /path/to/program_office_resolver.py
    python research/tools/replay_attributions.py --selfcheck

Each example in `research/memory/attribution_examples.json` records a contract, the office
this research says owns it, and the passages that say so. This feeds those same
passages to `resolve_program_office()` with the offices and edges loaded in the
local database and reports where the resolver agrees, disagrees or declines.

Offline: no network. It reads the local database through `psql` and imports the
resolver from a file path, because the resolver lives in another repository and this
one does not depend on it.

Disagreement is not automatically a resolver bug. An example the resolver declines
may be one where the evidence genuinely does not name an office, which is the
behaviour the resolver is built for; the report keeps the two apart.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "research" / "memory" / "attribution_examples.json"
DEFAULT_RESOLVER = Path.home() / "chromie-runner/orchestration/gov/agency_brain/program_office_resolver.py"
DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

ORG_COLUMNS = ("id", "agency_id", "name", "org_type", "acronym", "aliases",
               "normalized_aliases", "external_ids", "parent_organization_id", "active",
               "valid_from", "valid_to", "source", "source_ref", "source_url",
               "observed_at", "confidence")
REL_COLUMNS = ("source_organization_id", "target_organization_id", "relationship_type",
               "valid_from", "valid_to", "source", "source_ref", "source_url",
               "observed_at", "confidence")


def query(dsn: str, sql: str) -> list[dict]:
    out = subprocess.run(["psql", dsn, "-At", "-c", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}")
    return json.loads(out.stdout.strip() or "[]")


def load_resolver(path: Path):
    spec = importlib.util.spec_from_file_location("program_office_resolver", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import a resolver from {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves field types through sys.modules, so the module has to be
    # registered before it executes or every @dataclass in it raises.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The resolver reads only allow-listed fields. A research passage is narrative text;
# calling it anything else would smuggle it into a field with different weight.
def fragments_for(example: dict) -> list[dict]:
    out = []
    for n, item in enumerate(example.get("evidence", [])):
        passage = (item.get("passage") or "").strip()
        if not passage:
            continue
        out.append({
            "field": "award_description",
            "text": passage,
            "source": "chromie-federal-buyer-map-trial",
            "source_ref": f"{example['id']}:evidence:{n}",
            "source_url": item.get("source_url"),
            "observed_at": item.get("observed_at"),
        })
    return out


def event_date_for(example: dict) -> str | None:
    dates = example.get("dates") or {}
    for key in ("signed", "pop_start", "awarded", "posted"):
        if dates.get(key):
            return dates[key]
    observed = [i.get("observed_at") for i in example.get("evidence", []) if i.get("observed_at")]
    return min(observed) if observed else None


def procurement_for(example: dict) -> dict:
    return {
        "source": "chromie-federal-buyer-map-trial",
        "source_ref": example["identifier"] or example["id"],
        "source_url": (example.get("evidence") or [{}])[0].get("source_url"),
        "event_date": event_date_for(example),
        "evidence_fragments": fragments_for(example),
    }


def verdict(expected: list[str], resolved: list[str], status: str, in_scope: bool = True) -> str:
    """Separate a real disagreement from the resolver doing its job.

    It only ever asserts program offices, so an example whose owner is a PEO is out
    of scope rather than missed. And it can surface a candidate without asserting
    it, which is a weaker answer than a match, not the same one.
    """
    if not in_scope:
        return "out of scope: only program offices are asserted"
    if not expected:
        return "match, correctly unresolved" if not resolved else "found an office the research did not"
    if not resolved:
        return "declined"
    if set(resolved) == set(expected):
        return "match" if status.casefold() == "resolved" else f"right office, not asserted ({status})"
    if set(resolved) < set(expected):
        return "partial"
    return "disagree"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolver", type=Path, default=DEFAULT_RESOLVER)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    resolver = load_resolver(args.resolver)
    organizations = query(args.dsn,
                          f"select coalesce(json_agg(row_to_json(t)),'[]') from "
                          f"(select {', '.join(ORG_COLUMNS)} from public.gov_organizations) t")
    relationships = query(args.dsn,
                          f"select coalesce(json_agg(row_to_json(t)),'[]') from "
                          f"(select {', '.join(REL_COLUMNS)} from public.gov_organization_relationships) t")
    # The examples name offices by their local seed id (`pmw:160`); the database keys
    # them by uuid and keeps the seed id in source_ref.
    by_seed_id = {o["source_ref"]: o["id"] for o in organizations if o.get("source_ref")}
    office_types = {o["source_ref"]: o["org_type"] for o in organizations if o.get("source_ref")}

    examples = json.loads(EXAMPLES.read_text())
    rows, tally = [], {}
    for example in examples:
        result = resolver.resolve_program_office(
            procurement_for(example), organizations, relationships,
            observed_at=datetime.now(timezone.utc))
        resolved = [c.organization_id for c in result.candidates]
        expected = [by_seed_id[o] for o in example["program_offices"] if o in by_seed_id]
        in_scope = all(
            office_types.get(o) in resolver.PROGRAM_OFFICE_TYPES
            for o in example["program_offices"]) if example["program_offices"] else True
        outcome = verdict(expected, resolved, result.status.value, in_scope)
        tally[outcome] = tally.get(outcome, 0) + 1
        rows.append({
            "id": example["id"],
            "identifier": example["identifier"],
            "evidence_class": example["evidence_class"],
            "expected": example["program_offices"],
            "resolved": [c.organization_name for c in result.candidates],
            "status": result.status.value,
            "reason": result.reason,
            "outcome": outcome,
        })

    if args.json:
        print(json.dumps({"summary": tally, "rows": rows}, indent=1))
        return 0
    width = max(len(r["id"]) for r in rows)
    for row in rows:
        expected = ",".join(row["expected"]) or "-"
        resolved = ",".join(row["resolved"]) or "-"
        print(f"{row['id']:<{width}}  {row['outcome']:<42}  {row['status']:<12} "
              f"expected={expected:<24} resolved={resolved}")
    print()
    for outcome, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"{count:>3}  {outcome}")
    return 0


def selfcheck() -> int:
    assert verdict(["a"], ["a"], "resolved") == "match"
    assert verdict(["a"], ["a"], "RESOLVED") == "match", "status casing must not change the verdict"
    assert verdict(["a"], [], "unresolved") == "declined"
    assert verdict([], [], "unresolved") == "match, correctly unresolved"
    assert verdict([], ["a"], "resolved").startswith("found an office")
    assert verdict(["a", "b"], ["a"], "resolved") == "partial"
    assert verdict(["a"], ["b"], "resolved") == "disagree"
    # Surfacing the right office without asserting it is a weaker answer than a match.
    assert verdict(["a"], ["a"], "unresolved") == "right office, not asserted (unresolved)"
    assert verdict(["a"], [], "unresolved", in_scope=False).startswith("out of scope")

    example = {"id": "EX00", "identifier": "N001", "dates": {"pop_start": "2020-01-02"},
               "evidence": [{"passage": " owned by PMW 160 ", "observed_at": "2026-01-01",
                             "source_url": "https://example.gov/a"},
                            {"passage": "   ", "observed_at": "2026-01-01"}]}
    frags = fragments_for(example)
    assert len(frags) == 1, "a blank passage is not evidence"
    assert frags[0]["field"] == "award_description"
    assert frags[0]["source_ref"] == "EX00:evidence:0"
    # A signing or performance date, not the day we happened to fetch the page.
    assert event_date_for(example) == "2020-01-02"
    assert event_date_for({"evidence": [{"observed_at": "2026-02-02"}, {"observed_at": "2026-01-01"}]}) == "2026-01-01"
    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(selfcheck() if "--selfcheck" in sys.argv else main(sys.argv[1:]))

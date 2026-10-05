"""The organization graph of a built database in the shape the program-office resolver and its
evaluation read (`scripts/sync_program_office_eval_graph.py` field lists): one JSON document with
`organizations` and `relationships`, every row carrying source, source_ref, observed_at and
confidence, which the resolver requires before it reads a row at all.

    python research/tools/graph_export.py --db navy_proof_k > build/program_office_graph.json
    python research/tools/graph_export.py --selfcheck
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ORG_FIELDS = ("id", "agency_id", "name", "org_type", "acronym", "aliases", "normalized_aliases", "external_ids",
              "parent_organization_id", "active", "valid_from", "valid_to", "source", "source_ref", "source_url",
              "observed_at", "confidence")
REL_FIELDS = ("source_organization_id", "target_organization_id", "relationship_type", "valid_from", "valid_to",
              "source", "source_ref", "source_url", "observed_at", "confidence")
PROVENANCE = ("source", "source_ref", "observed_at", "confidence")


def select(table: str, fields: tuple[str, ...]) -> str:
    return f"select coalesce(json_agg(row_to_json(t) order by t.id), '[]') from (select {', '.join(fields)} from public.{table}) t"


def rel_select() -> str:
    return ("select coalesce(json_agg(row_to_json(t) order by t.source_organization_id, t.target_organization_id, t.relationship_type), '[]') "
            f"from (select {', '.join(REL_FIELDS)} from public.gov_organization_relationships) t")


def psql(db: str, sql: str) -> list[dict]:
    env = {**os.environ}
    env.setdefault("PGPASSWORD", "postgres")
    done = subprocess.run(["psql", "-h", os.environ.get("PGHOST", "127.0.0.1"), "-p", os.environ.get("PGPORT", "54322"),
                           "-U", os.environ.get("PGUSER", "postgres"), "-d", db, "-Atc", sql],
                          capture_output=True, text=True, env=env)
    if done.returncode:
        raise SystemExit(done.stderr.strip())
    return json.loads(done.stdout)


def graph(organizations: list[dict], relationships: list[dict]) -> dict:
    """The document, with the rows the resolver would skip counted so a build that drops provenance shows it."""
    def missing(rows):
        return sum(1 for r in rows if any(r.get(f) in (None, "") for f in PROVENANCE))
    return {
        "organizations": organizations,
        "relationships": relationships,
        "summary": {"organizations": len(organizations), "relationships": len(relationships),
                    "organizations_without_provenance": missing(organizations),
                    "relationships_without_provenance": missing(relationships)},
    }


def selfcheck() -> int:
    orgs = [{"id": "b", "source": "s", "source_ref": "r", "observed_at": "2026-01-01", "confidence": 1.0},
            {"id": "a", "source": "s", "source_ref": None, "observed_at": None, "confidence": 0.5}]
    doc = graph(orgs, [])
    assert doc["summary"] == {"organizations": 2, "relationships": 0, "organizations_without_provenance": 1,
                              "relationships_without_provenance": 0}, doc["summary"]
    assert select("gov_organizations", ORG_FIELDS).startswith("select coalesce(json_agg(row_to_json(t) order by t.id)")
    assert "observed_at" in ORG_FIELDS and "observed_at" in REL_FIELDS
    print("graph_export selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if "--selfcheck" in argv:
        return selfcheck()
    if len(argv) != 2 or argv[0] != "--db":
        print(__doc__)
        return 2
    doc = graph(psql(argv[1], select("gov_organizations", ORG_FIELDS)), psql(argv[1], rel_select()))
    json.dump(doc, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

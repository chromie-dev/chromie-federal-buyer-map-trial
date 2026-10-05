#!/usr/bin/env python3
"""Plan a promotion of the Navy export into the production agency-intelligence tables.

    python research/tools/promote_plan.py --fresh-check DIR     # write DIR/fresh_check.sql, run it with psql
    python research/tools/promote_plan.py [--db NAME] [--snapshot DIR] [--offices FILE]
    python research/tools/promote_plan.py ... --emit-sql FILE   # write the load file
    python research/tools/promote_plan.py --selfcheck

Read-only against production. It never writes there and holds no code that could:
the only production calls are GET, and the SQL it emits goes to a file for a person
to read and run.

Why a plan rather than a push. The production layer tables are append-only - both
UPDATE and DELETE raise - so a row inserted against the wrong office cannot be
removed, only retracted. Production already holds many of the offices under its own
ids, while the export minted its own, so a straight copy would create duplicate
program offices that un-deletable assertions then point at. Everything here exists to
make that mismatch visible before anything is written.

Offices. An agency-level row matches production's row for the same agency. Otherwise
an office code carries the claim, an exact name or alias counts only when the types
agree, and anything matching two production rows is refused rather than guessed. An
office production does not know under any name, acronym or code is created under its
mapped parent. One that shares any of those with a production row, whatever its type,
is held with everything pointing at it until --offices names the production row or
says "new".

Rows production's own check constraints refuse are left out with everything pointing
at them and counted as refused: production keeps procurement sources for state, local,
tribal and territorial portals only, so the export's federal sources stay in the export.

--fresh-check DIR writes one psql script for the read-only check login: server facts, the
applied migrations, row counts and the target tables' definitions under DIR/check, and
every table this plan reads under DIR/snapshot, in one session with the password typed at
the prompt. --snapshot DIR then reads production only from DIR/<table>.json and stops if a
table is missing, so the plan and the file's guard describe the same production state and
no service key is used. Without --snapshot it reads live (GET). The file
stops before writing if the production offices it relies on changed since then. It
writes transactions of at most 1,000 parent rows, each child with its parent, and every
insert skips a row whose primary key exists, so a partly applied file can be rerun;
any other unique collision stops it. FILE.manifest.csv lists every inserted id and
FILE.check.sql counts the ones production lacks.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

LOCAL = "postgresql://postgres:postgres@127.0.0.1:54322/"
# PRODUCTION_ENV_FILE points elsewhere when the production keys live in another checkout.
ENV_FILE = Path(os.environ.get("PRODUCTION_ENV_FILE") or Path.home() / "chromie/.env.local")
BATCH = 1000
# Production stamps these itself on insert.
PRODUCTION_FILLS = {"created_at", "updated_at", "recorded_at"}
ASSERTIONS = "gov_intelligence_assertions"
# Production's insert triggers write these: the agency page goes stale and a compile job queues.
TRIGGER_FILLED = ("agency_brain_pages", "brain_jobs")

# The file's transaction kinds in load order. A batch counts rows of the kind's first
# table; rows of the others travel in the transaction of the row they point at. Brain
# items go 1,000 to a transaction because every insert rewrites the agency's page row,
# which costs more the longer one transaction runs.
KINDS = (
    ("offices", ("gov_organizations",)),
    ("sources", ("gov_procurement_sources",)),
    ("relationships", ("gov_organization_relationships",)),
    ("contacts", ("gov_contacts",)),
    ("positions", ("gov_contact_positions",)),
    ("brain items", ("agency_brain_items", "gov_intelligence_evidence")),
    ("needs", ("gov_needs", "gov_need_requirements")),
    ("assertions", (ASSERTIONS, "gov_need_organizations", "gov_requirement_revisions",
                    "gov_funding_observations", "gov_assertion_evidence")),
)
TABLES = tuple(table for _, tables in KINDS for table in tables)
# Rows production may already hold under its own id: offices by matching, these by a
# unique key. Every row pointing at one takes the production id.
REUSE_KEYS = {"gov_contacts": "identity_key", "gov_procurement_sources": "source_key"}
MAPPED = ("agencies", "gov_organizations", *REUSE_KEYS)


def current(*columns):
    """Key of a partial unique index over current rows, NULLS NOT DISTINCT as tuples compare."""
    return lambda row: [] if row["valid_to"] is not None else [tuple(row[c] for c in columns)]


def unique(*columns):
    """Key of a plain unique index, where a null never collides."""
    return lambda row: [] if any(row[c] is None for c in columns) else [tuple(row[c] for c in columns)]


def live_claims(row):
    """Keys the two live-claim indexes hold for a Brain item."""
    if row["superseded_by"] is not None:
        return []
    return [(scope, row[scope], row["claim_key"], row["fiscal_year"] or 0)
            for scope in ("agency_id", "scope_organization_id") if row[scope] is not None]


# Production already states this fact under another id: leave ours out, with
# everything that depends on it.
SKIP_KEYS = {
    "agency_brain_items": live_claims,
    "gov_contact_positions": current("contact_id", "organization_id", "role_type", "source", "source_ref"),
    "gov_organization_relationships": current("source_organization_id", "target_organization_id",
                                              "relationship_type", "source", "source_ref"),
}
# The export and production disagree about one record: stop for a person to look.
STOP_KEYS = {
    "gov_needs": unique("source", "source_key"),
    "gov_intelligence_evidence": unique("provider", "source_key"),
    ASSERTIONS: unique("producer", "source_key"),
}
# Production's check constraint refuses the row: leave it out, with everything that
# depends on it. government_level is not null and one of these four in production.
REFUSED = {
    "gov_procurement_sources": lambda row: row["government_level"] not in ("state", "local", "tribal", "territorial"),
}

# Types that may stand in for one another. A program office is never a contracting
# office, and matching across the line would put the buy on the wrong desk.
TYPE_FAMILY = {
    "program_office": "program",
    "direct_reporting_program_manager": "program",
    "program_executive_office": "portfolio",
    "acquisition_portfolio": "portfolio",
    "contracting_office": "contracting",
    "contracting_activity": "contracting",
    "contracting_directorate": "contracting",
    "contracting_division": "contracting",
    "contracting_branch": "contracting",
    "hca_office": "contracting",
    "technical_office": "technical",
    "field_activity": "technical",
    "agency": "agency",
    "department": "agency",
    "other": "other",
}
FAMILIES = ("PMW", "PMA", "PMS", "PEO")
# `PMW/A 170` is one office with a service letter. `PMA/PMW 101` is one office written
# under two code families, and collapsing it to the second loses the designation a
# production row might be stored under.
CODE_RE = re.compile(
    r"\b(?P<first>PMW|PMA|PMS|PEO)\s*(?:/\s*(?P<second>PMW|PMA|PMS|PEO|[A-Z])\s*)?-?\s*(?P<number>\d{3})\b")

# An assertion will not commit without its typed detail row: the completeness check is
# a constraint trigger deferred to commit, so an assertion whose detail table is not
# carried rolls its whole transaction back at the very end.
DETAIL_OF_KIND = {
    "requirement": "gov_requirement_revisions",
    "funding": "gov_funding_observations",
    "need_organization": "gov_need_organizations",
    "program_organization": "gov_program_organizations",
    "program_need": "gov_program_needs",
    "procurement_attribution": "gov_procurement_attributions",
    "program_procurement": "gov_program_procurements",
    "need_procurement": "gov_need_procurements",
    "procurement_lineage": "gov_procurement_lineage",
}


# ----------------------------------------------------------------- reads

def psql(dsn: str, sql: str) -> str:
    out = subprocess.run(["psql", dsn, "-X", "-At", "-c", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"local psql failed: {out.stderr.strip()}")
    return out.stdout


def catalog(dsn: str) -> tuple[dict, dict, dict, dict]:
    """Columns, numeric columns, primary keys and foreign keys of the export's tables."""
    columns, numeric, pk, fks = {}, {}, {}, {}
    for table, column, kind in json.loads(psql(dsn, """
            select json_agg(json_build_array(c.relname, a.attname, format_type(a.atttypid, a.atttypmod))
                            order by c.relname, a.attnum)
            from pg_attribute a join pg_class c on c.oid = a.attrelid
            where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
              and a.attnum > 0 and not a.attisdropped""")):
        columns.setdefault(table, []).append(column)
        if kind.startswith("numeric"):
            numeric.setdefault(table, set()).add(column)
    for table, kind, keys, parent in json.loads(psql(dsn, """
            select json_agg(json_build_array(conrelid::regclass::text, contype,
                   array(select attname from unnest(conkey) with ordinality k(n, i)
                         join pg_attribute on attrelid = conrelid and attnum = k.n order by k.i),
                   confrelid::regclass::text))
            from pg_constraint where connamespace = 'public'::regnamespace and contype in ('p', 'f')""")):
        if kind == "p":
            pk[table] = tuple(keys)
        elif len(keys) == 1:
            fks.setdefault(table, []).append((keys[0], parent))
        elif table in TABLES:
            raise SystemExit(f"{table} has a composite foreign key {keys}; the remap handles single columns")
    return columns, numeric, pk, fks


def rows_sql(table: str, columns: list[str], numeric: set[str], pk: tuple) -> str:
    """One JSON row per line. Numeric is read as text, so an amount stays exact."""
    select = ", ".join(f'"{c}"::text as "{c}"' if c in numeric else f'"{c}"' for c in columns)
    order = ", ".join(f'"{c}"' for c in pk)
    return f'select row_to_json(t)::text from (select {select} from public."{table}" order by {order}) t'


def export_rows(dsn: str, table: str, columns: list[str], numeric: set[str], pk: tuple) -> list[dict]:
    return [json.loads(line) for line in psql(dsn, rows_sql(table, columns, numeric, pk)).splitlines()]


def prod_env() -> tuple[str, str]:
    path = ENV_FILE
    if not path.exists():
        raise SystemExit(f"no {path}; set PRODUCTION_ENV_FILE to reach production read-only")
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    key = values.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise SystemExit(f"SUPABASE_SERVICE_ROLE_KEY missing from {path}")
    # The custom domain in SUPABASE_URL answers scripts with Cloudflare 1010; the
    # project host named by the key's ref serves the same API.
    body = key.split(".")[1]
    ref = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("ref")
    return (f"https://{ref}.supabase.co" if ref else values["SUPABASE_URL"]), key


def prod_get(table: str, order: tuple) -> list[dict]:
    """GET only. PostgREST caps a page at 1000, so this pages until short."""
    url, key = prod_env()
    out = []
    while True:
        request = urllib.request.Request(
            f"{url}/rest/v1/{table}?select=*&order={','.join(order)}&limit=1000&offset={len(out)}",
            method="GET", headers={"apikey": key, "Authorization": f"Bearer {key}",
                                   "User-Agent": "promote-plan"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                page = json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise SystemExit(f"production read of {table} failed: {e.code} {e.read()[:300]!r}")
        out += page
        if len(page) < 1000:
            return out


def prod_rows(table: str, snapshot: Path | None, order: tuple) -> list[dict]:
    if snapshot:
        path = snapshot / f"{table}.json"
        if not path.exists():
            raise SystemExit(f"{path} missing: take the snapshot with --fresh-check and the check login")
        return json.loads(path.read_text())
    return prod_get(table, order)


def snapshot_label(path: Path) -> str:
    """The snapshot as the files that travel with the load name it, without this machine's home directory."""
    return "/".join(path.resolve().parts[-2:])


def fresh_check_sql(tables: list[str], read: dict[str, tuple]) -> str:
    """One read-only psql session: what step 2 of the load plan reports, then the production rows the plan reads.
    Run from the directory it was written to, so check/ and snapshot/ land beside it."""
    names = ", ".join(f"'{t}'" for t in tables)
    lines = ["-- Fresh production check for the Navy load, generated by research/tools/promote_plan.py.",
             "-- Run with the read-only check login, from this directory:",
             "--   psql 'postgresql://chromie_navy_check.<project ref>@<session pooler host>:5432/postgres' -f fresh_check.sql",
             "\\set ON_ERROR_STOP on", "set default_transaction_read_only = on;", "\\pset footer off",
             "\\o check/server.txt",
             "select now() as read_at, version();",
             "select pg_size_pretty(pg_database_size(current_database())) as database_size,",
             "       (select pg_size_pretty(sum(size)) from pg_ls_waldir()) as wal_size;",
             "select state, count(*) from pg_stat_activity where datname = current_database() group by 1 order by 1;",
             "\\o check/migrations.txt", "select version from supabase_migrations.schema_migrations order by 1;",
             "\\o check/counts.txt",
             " union all\n".join(f"select '{t}' as table_name, count(*) from public.\"{t}\"" for t in tables) + ";",
             "\\o check/definitions.txt",
             "select table_name, ordinal_position, column_name, data_type, is_nullable, column_default"
             f" from information_schema.columns where table_schema = 'public' and table_name in ({names}) order by 1, 2;",
             "select conrelid::regclass as table_name, conname, pg_get_constraintdef(oid) from pg_constraint"
             f" where conrelid::regclass::text in ({names}) order by 1, 2;",
             "select tgrelid::regclass as table_name, tgname, pg_get_triggerdef(oid) from pg_trigger"
             f" where not tgisinternal and tgrelid::regclass::text in ({names}) order by 1, 2;",
             "\\t on", "\\a"]
    for table, order in read.items():
        by = ", ".join(f'"{c}"' for c in order)
        lines += [f"\\o snapshot/{table}.json",
                  f"select coalesce(json_agg(t order by {by}), '[]'::json) from public.\"{table}\" t;"]
    return "\n".join(lines + ["\\o", ""])


# ----------------------------------------------------------------- office match

def office_codes(row: dict) -> set[str]:
    text = [row.get("acronym") or "", row.get("name") or "", *(row.get("aliases") or [])]
    text += [str(v) for v in (row.get("external_ids") or {}).values()]
    out = set()
    for value in text:
        for m in CODE_RE.finditer(value.upper()):
            number, second = m.group("number"), m.group("second")
            out.add(m.group("first") + number)
            if second in FAMILIES:
                out.add(second + number)
    return out


def office_names(row: dict) -> set[str]:
    values = {(row.get("name") or "").strip().lower()}
    values |= {a.strip().lower() for a in (row.get("aliases") or [])}
    return {v for v in values if v}


def office_keys(row: dict) -> set[str]:
    """Everything that names an office: name, aliases and acronym, lowercased, and its codes."""
    keys = office_names(row) | {"code:" + c for c in office_codes(row)}
    acronym = (row.get("acronym") or "").strip().lower()
    return keys | {acronym} if acronym else keys


def compatible(a: str, b: str) -> bool:
    return TYPE_FAMILY.get(a, a) == TYPE_FAMILY.get(b, b)


def match_offices(local_rows: list[dict], prod_rows: list[dict]) -> tuple[dict, dict, list]:
    by_code, by_name = {}, {}
    for row in prod_rows:
        for code in office_codes(row):
            by_code.setdefault(code, []).append(row)
        for name in office_names(row):
            by_name.setdefault(name, []).append(row)

    resolved, ambiguous, unmatched = {}, {}, []
    for row in local_rows:
        hits = {p["id"]: p for code in office_codes(row) for p in by_code.get(code, [])
                if compatible(row["org_type"], p["org_type"])}
        how = "office code"
        if not hits:
            hits = {p["id"]: p for name in office_names(row) for p in by_name.get(name, [])
                    if compatible(row["org_type"], p["org_type"])}
            how = "exact name"
        if len(hits) == 1:
            target = next(iter(hits.values()))
            resolved[row["id"]] = {"local": row, "prod": target, "how": how}
        elif hits:
            ambiguous[row["id"]] = {"local": row, "candidates": list(hits.values())}
        else:
            unmatched.append(row)

    # One local office hitting two production rows is refused above. The reverse is
    # just as wrong and easier to miss: two distinct offices resolving to the same
    # production row silently merges them, and the layer tables are append-only, so
    # the merge cannot be undone afterwards. Refuse both sides of the collision.
    claimed: dict[str, list[str]] = {}
    for local_id, entry in resolved.items():
        claimed.setdefault(entry["prod"]["id"], []).append(local_id)
    for local_ids in claimed.values():
        if len(local_ids) < 2:
            continue
        refs = {i: resolved[i]["local"]["source_ref"] for i in local_ids}
        for local_id in local_ids:
            entry = resolved.pop(local_id)
            ambiguous[local_id] = {
                "local": entry["local"], "candidates": [entry["prod"]],
                "note": "shares this production row with "
                        + ", ".join(refs[i] for i in local_ids if i != local_id)}
    return resolved, ambiguous, unmatched


def resolve_offices(local_rows: list[dict], prod_rows: list[dict], agency_map: dict,
                    decisions: dict) -> tuple[dict, list, dict]:
    """Decide every export office: a production row, a new row, or held for a person.

    Returns ({local id: (production row, how)}, [rows to create], {local id: [twins]}).
    A decision wins over any match, because it is a person's reading of the twins.
    """
    by_id = {p["id"]: p for p in prod_rows}
    refs = {r["source_ref"] for r in local_rows}
    wrong = sorted(ref for ref in decisions if ref not in refs)
    wrong += sorted(f"{ref} -> {target}" for ref, target in decisions.items()
                    if target != "new" and target not in by_id)
    if wrong:
        raise SystemExit("--offices names offices or production rows that do not exist: " + "; ".join(wrong))

    by_agency = {p["existing_agency_id"]: p for p in prod_rows if p.get("existing_agency_id")}
    matched, created, rest = {}, [], []
    for row in local_rows:
        decision = decisions.get(row["source_ref"])
        agency_row = by_agency.get(agency_map.get(row.get("existing_agency_id")))
        if decision == "new":
            created.append(row)
        elif decision:
            matched[row["id"]] = (by_id[decision], "decision")
        elif agency_row:
            # Production allows one agency-level row per agency, so the agency is the identity.
            matched[row["id"]] = (agency_row, "agency")
        else:
            rest.append(row)

    resolved, ambiguous, unmatched = match_offices(rest, prod_rows)
    matched |= {local_id: (e["prod"], e["how"]) for local_id, e in resolved.items()}
    claimed: dict[str, list[str]] = {}
    for local_id, (target, _) in matched.items():
        claimed.setdefault(target["id"], []).append(local_id)
    ref_of = {r["id"]: r["source_ref"] for r in local_rows}
    shared = [" and ".join(ref_of[i] for i in ids) + f" -> {target}"
              for target, ids in claimed.items() if len(ids) > 1]
    if shared:
        raise SystemExit("two export offices resolve to one production row: " + "; ".join(shared))

    index: dict[str, list[dict]] = {}
    for p in prod_rows:
        for key in office_keys(p):
            index.setdefault(key, []).append(p)
    held = {local_id: e["candidates"] for local_id, e in ambiguous.items()}
    for row in unmatched:
        twins = list({p["id"]: p for key in office_keys(row) for p in index.get(key, [])}.values())
        if twins:
            held[row["id"]] = twins
        else:
            created.append(row)
    return matched, created, held


def read_decisions(raw: dict) -> tuple[dict[str, str], dict[str, str]]:
    """An office decision is a production id or "new", or {"id": ..., "reason": ...} with the reading written down."""
    decisions = {ref: d["id"] if isinstance(d, dict) else d for ref, d in raw.items()}
    return decisions, {ref: d["reason"] for ref, d in raw.items() if isinstance(d, dict) and d.get("reason")}


def map_agencies(local_rows: list[dict], prod_rows: list[dict]) -> dict[str, str]:
    """Production already has the agencies; a promotion points at them, never inserts."""
    index = {(r["level"], r["toptier_code"], r["subtier_code"]): r["id"] for r in prod_rows}
    mapping = {}
    for row in local_rows:
        key = (row["level"], row["toptier_code"], row["subtier_code"])
        if key not in index:
            raise SystemExit(f"no production agency for {key}; refusing to guess")
        mapping[row["id"]] = index[key]
    return mapping


# ----------------------------------------------------------------- the load

def remap(rows: dict, fks: dict, maps: dict) -> None:
    """Point every foreign key at the production row that stands for its target.

    A target with no mapping keeps its export id: a held office, whose rows the drop
    pass then removes, or a row this file inserts under its own id.
    """
    for table, table_rows in rows.items():
        for column, parent in fks.get(table, ()):
            mapping = maps.get(parent)
            if mapping:
                for row in table_rows:
                    if row[column] is not None:
                        row[column] = mapping.get(row[column], row[column])


def collisions(rows: list[dict], prod_rows: list[dict], keys) -> list[tuple[dict, str]]:
    """Our rows whose key production holds under another id. The same id is this file, rerun."""
    taken = {key: p["id"] for p in prod_rows for key in keys(p)}
    return [(row, taken[key]) for row in rows for key in keys(row)
            if taken.get(key, row["id"]) != row["id"]]


def propagate(rows: dict, pk: dict, fks: dict, dropped: dict) -> None:
    """Drop every row pointing at a dropped row, and every assertion that lost a detail row or link.

    An assertion commits only with its typed detail row and supporting evidence, and a
    superseding one only after the row it supersedes, so a partial set would fail at
    commit or, worse, land a claim without the evidence it was made on.
    """
    changed = True
    while changed:
        changed = False
        for table, table_rows in rows.items():
            refs = fks.get(table, ())
            owner_ref = ("assertion_id", ASSERTIONS) in refs
            gone = dropped.setdefault(table, set())
            for row in table_rows:
                key = tuple(row[c] for c in pk[table])
                if key not in gone and any(row[col] is not None and (row[col],) in dropped.get(parent, ())
                                           for col, parent in refs):
                    gone.add(key)
                    changed = True
                if key in gone and owner_ref and (row["assertion_id"],) not in dropped.setdefault(ASSERTIONS, set()):
                    dropped[ASSERTIONS].add((row["assertion_id"],))
                    changed = True


def levels(rows: list[dict], parent: str) -> dict[str, int]:
    """Depth of each row below the rows among these it needs inserted first."""
    by_id = {r["id"]: r for r in rows}
    depth: dict[str, int] = {}
    for row in rows:
        chain, node = [], row
        while node["id"] not in depth and node.get(parent) in by_id:
            if node["id"] in chain:
                raise SystemExit(f"{parent} cycle through {node['id']}; cannot order the rows")
            chain.append(node["id"])
            node = by_id[node[parent]]
        base = depth.setdefault(node["id"], 0)
        for step, row_id in enumerate(reversed(chain), 1):
            depth[row_id] = base + step
    return depth


def batches(tables: tuple, rows: dict, fks: dict, depth: dict, size: int = BATCH) -> list[list[list]]:
    """Transactions of at most `size` parent rows, each parent with the rows pointing at it.

    Parents are sorted by depth, so a row a parent needs (an office's parent, the
    assertion it supersedes) lands in an earlier transaction or earlier in the same one.
    """
    parent, *children = tables
    attached: dict[str, list] = {}
    for child in children:
        column = next(c for c, target in fks[child] if target == parent)
        for row in rows[child]:
            attached.setdefault(row[column], []).append((child, row))
    groups = [[(parent, row), *attached.pop(row["id"], [])]
              for row in sorted(rows[parent], key=lambda r: depth.get(r["id"], 0))]
    if attached:
        raise SystemExit(f"{sum(map(len, attached.values()))} rows of {', '.join(children)} "
                         f"lost their {parent} row")
    return [groups[i:i + size] for i in range(0, len(groups), size)]


def insert_sql(table: str, rows: list[dict], columns: list[str], pk: tuple) -> str:
    """One jsonb literal per statement, so text[] and jsonb columns keep their types.

    The conflict target is the primary key alone: a rerun skips what an earlier run
    committed, and any other unique collision still raises and stops the file.
    """
    names = ", ".join(f'"{c}"' for c in columns)
    target = ", ".join(f'"{c}"' for c in pk)
    body = json.dumps([{c: r[c] for c in columns} for r in rows], ensure_ascii=False, separators=(",", ":"))
    quoted = body.replace("'", "''")
    return (f"insert into public.{table} ({names})\n"
            f"select {names} from jsonb_populate_recordset(null::public.{table}, '{quoted}'::jsonb)\n"
            f"on conflict ({target}) do nothing;")


def transaction_sql(tables: tuple, batch: list, depth: dict, columns: dict, pk: dict) -> list[str]:
    """One insert per table and depth, parents first, each child table after its parent."""
    units = sorted(((tables.index(t), depth.get(r.get("id"), 0), r) for group in batch for t, r in group),
                   key=lambda u: u[:2])
    statements = []
    for (index, _), run in itertools.groupby(units, key=lambda u: u[:2]):
        table = tables[index]
        statements.append(insert_sql(table, [u[2] for u in run], columns[table], pk[table]))
    return statements


def uuid_array(ids) -> str:
    return "'{" + ",".join(sorted(ids)) + "}'::uuid[]"


def guard_sql(prod_offices: list[dict], agency_ids: set, office_ids: set, created_ids: set) -> str:
    """Stop before any write if the production offices this file relies on changed."""
    watched = [p for p in prod_offices if p["agency_id"] in agency_ids or p["id"] in office_ids]
    latest = max((p["updated_at"] for p in watched), default=None)
    expect = f"'{latest}'::timestamptz" if latest else "null"
    return (f"do $$\ndeclare seen bigint; latest timestamptz;\nbegin\n"
            f"  select count(*), max(updated_at) into seen, latest from public.gov_organizations\n"
            f"  where (agency_id = any({uuid_array(agency_ids)}) or id = any({uuid_array(office_ids)}))\n"
            f"    and not (id = any({uuid_array(created_ids)}));\n"
            f"  if seen <> {len(watched)} or latest is distinct from {expect} then\n"
            f"    raise exception 'production offices changed since the snapshot (% rows, latest %; "
            f"this file expects {len(watched)} and {latest}). Regenerate it.', seen, latest;\n"
            f"  end if;\nend $$;")


def check_sql(sql_name: str, manifest_name: str, tables: list[str]) -> str:
    counts = "\nunion all\n".join(
        f"select '{t}' as table_name, count(*) as listed, count(*) filter (where x.id is null) as missing\n"
        f"from manifest m left join public.{t} x on x.id = m.id where m.table_name = '{t}'"
        for t in tables)
    return (f"-- Rows {sql_name} inserted that the database lacks. Every missing count should be 0.\n"
            f"-- Run from the directory holding {manifest_name}. Detail rows and evidence links\n"
            f"-- are covered by their assertion, which cannot commit without them.\n"
            "-- The read-only check login runs it too: a read-only transaction refuses even a temporary\n"
            "-- table, so the manifest is read in one read-write transaction that ends in rollback.\n"
            "\\set ON_ERROR_STOP on\n"
            "begin read write;\n"
            "create temp table manifest (table_name text, id uuid);\n"
            f"\\copy manifest from '{manifest_name}' with (format csv, header)\n"
            f"{counts}\norder by table_name;\nrollback;\n")


def check_export(export: dict) -> None:
    """Shapes of the export this load does not handle, refused before any planning."""
    if any(r["superseded_by"] for r in export["agency_brain_items"]):
        raise SystemExit("superseded Brain items need their replacement in the same transaction; not handled")
    kinds = {r["assertion_kind"] for r in export[ASSERTIONS]}
    missing = sorted(DETAIL_OF_KIND.get(k, f"a detail table for {k}") for k in kinds
                     if DETAIL_OF_KIND.get(k) not in TABLES)
    if missing:
        raise SystemExit(f"assertions need detail tables this load does not carry: {', '.join(missing)}")


def verify_references(rows: dict, prod: dict, fks: dict) -> None:
    """Every foreign key points at a row this file inserts or one production holds."""
    allowed: dict[str, set] = {}
    for table, table_rows in rows.items():
        for column, parent in fks.get(table, ()):
            if parent not in allowed:
                allowed[parent] = ({r["id"] for r in rows.get(parent, ())}
                                   | {p["id"] for p in prod.get(parent, ())})
            bad = sum(1 for r in table_rows if r[column] is not None and r[column] not in allowed[parent])
            if bad:
                raise SystemExit(f"{bad} rows of {table} point through {column} at {parent} rows "
                                 f"that neither production nor this file holds")


def build(db: str, snapshot: Path | None, offices: dict) -> dict:
    """Everything the load file is made of: the export, production as read, each office's fate and the final rows,
    remapped to production's ids. The verifier calls this too, so it checks the rows the file inserts."""
    dsn = LOCAL + db
    columns, numeric, pk, fks = catalog(dsn)
    read = {t: pk[t] for t in dict.fromkeys((*MAPPED, *SKIP_KEYS, *STOP_KEYS))}
    wanted = ("agencies", *TABLES)
    if missing := [t for t in wanted if t not in columns]:
        raise SystemExit(f"{db} lacks {', '.join(missing)}")
    carried = {t: [c for c in columns[t] if c not in PRODUCTION_FILLS] for t in wanted}
    export = {t: export_rows(dsn, t, carried[t], numeric.get(t, set()), pk[t]) for t in wanted}
    check_export(export)
    prod = {t: prod_rows(t, snapshot, order) for t, order in read.items()}
    decisions, reasons = read_decisions(offices)

    agency_map = map_agencies(export["agencies"], prod["agencies"])
    matched, created, held = resolve_offices(export["gov_organizations"], prod["gov_organizations"],
                                             agency_map, decisions)
    maps = {"agencies": agency_map,
            "gov_organizations": {i: p["id"] for i, (p, _) in matched.items()}}
    load = {t: list(export[t]) for t in TABLES}
    load["gov_organizations"] = created
    for table, key in REUSE_KEYS.items():
        known = {p[key]: p["id"] for p in prod[table] if p.get(key) is not None}
        maps[table] = {r["id"]: known[r[key]] for r in export[table] if r[key] in known}
        load[table] = [r for r in export[table] if r[key] not in known]
    reused = {"agencies": len(export["agencies"]), "gov_organizations": len(matched)}
    reused |= {t: len(export[t]) - len(load[t]) for t in REUSE_KEYS}
    remap(load, fks, maps)

    def key_of(table, row):
        return tuple(row[c] for c in pk[table])

    held_rows = {"gov_organizations": {(i,) for i in held}}
    propagate(load, pk, fks, held_rows)
    dropped = {t: set(keys) for t, keys in held_rows.items()}
    skipped_items = []
    for table, keys in SKIP_KEYS.items():
        live = [r for r in load[table] if key_of(table, r) not in dropped.get(table, ())]
        for row, prod_id in collisions(live, prod[table], keys):
            dropped.setdefault(table, set()).add(key_of(table, row))
            if table == "agency_brain_items":
                skipped_items.append((row, prod_id))
    refused = {table: {key_of(table, r) for r in load[table] if refused_by_production(r)}
               for table, refused_by_production in REFUSED.items()}
    for table, keys in refused.items():
        dropped.setdefault(table, set()).update(keys)
    propagate(load, pk, fks, dropped)
    final = {t: [r for r in load[t] if key_of(t, r) not in dropped.get(t, ())] for t in TABLES}
    for table, keys in STOP_KEYS.items():
        if clash := collisions(final[table], prod[table], keys):
            raise SystemExit(f"production holds {len(clash)} {table} keys under other ids, first: "
                             + "; ".join(f"{keys(r)[0]} -> {p}" for r, p in clash[:5]))
    verify_references(final, prod, fks)

    return {"columns": columns, "numeric": numeric, "pk": pk, "fks": fks, "carried": carried, "export": export,
            "prod": prod, "agency_map": agency_map, "matched": matched, "held": held, "held_rows": held_rows,
            "reused": reused, "dropped": dropped, "refused": refused, "skipped_items": skipped_items, "final": final,
            "decisions": decisions, "reasons": reasons, "maps": maps}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="postgres", help="local database holding the export")
    parser.add_argument("--snapshot", type=Path, help="production rows as DIR/<table>.json")
    parser.add_argument("--offices", type=Path,
                        help='checked office decisions, JSON {"source_ref": "production id" or "new", '
                             'or {"id": ..., "reason": ...}}')
    parser.add_argument("--emit-sql", type=Path)
    parser.add_argument("--fresh-check", type=Path, help="write DIR/fresh_check.sql for the check login and stop")
    args = parser.parse_args(argv)

    if args.fresh_check:
        pk = catalog(LOCAL + args.db)[2]
        read = {t: pk[t] for t in dict.fromkeys((*MAPPED, *SKIP_KEYS, *STOP_KEYS))}
        for sub in ("check", "snapshot"):
            (args.fresh_check / sub).mkdir(parents=True, exist_ok=True)
        (args.fresh_check / "fresh_check.sql").write_text(fresh_check_sql(["agencies", *TABLES, *TRIGGER_FILLED], read))
        print(f"wrote {args.fresh_check / 'fresh_check.sql'}; run it there with psql and the check login")
        return 0
    built = build(args.db, args.snapshot, json.loads(args.offices.read_text()) if args.offices else {})
    prod, final, pk, fks, matched, agency_map = (built[k] for k in ("prod", "final", "pk", "fks", "matched", "agency_map"))
    depth = levels(final["gov_organizations"], "parent_organization_id") | levels(final[ASSERTIONS], "supersedes_id")
    plan = [(kind, tables, batches(tables, final, fks, depth)) for kind, tables in KINDS]
    report(args, built, plan)

    if not args.emit_sql:
        print("\nRe-run with --emit-sql FILE to write the load file. "
              "Nothing is ever written to production by this tool.")
        return 0
    created_ids = {r["id"] for r in final["gov_organizations"]}
    guard = guard_sql(prod["gov_organizations"], set(agency_map.values()),
                      {p["id"] for p, _ in matched.values()}, created_ids)
    emit(args, plan, guard, depth, built["carried"], pk, final)
    return 0


def report(args, built: dict, plan) -> None:
    export, prod, matched, held, reused, held_rows, dropped, final, skipped_items, pk, refused, decisions, reasons = (
        built[k] for k in ("export", "prod", "matched", "held", "reused", "held_rows", "dropped", "final",
                           "skipped_items", "pk", "refused", "decisions", "reasons"))
    prod_name = {p["id"]: p["name"] for p in prod["gov_organizations"]}
    local = {r["id"]: r for r in export["gov_organizations"]}
    source = f"snapshot {snapshot_label(args.snapshot)}" if args.snapshot else "live GET"
    print(f"export {args.db} | production {source}\n")

    def because(ref):
        return f"\n      reason: {reasons[ref]}" if reasons.get(ref) else ""

    how_counts = Counter(how for _, how in matched.values())
    print(f"offices matched to production rows: {len(matched)} ("
          + ", ".join(f"{n} by {how}" for how, n in how_counts.most_common()) + ")")
    for local_id, (p, how) in sorted(matched.items(), key=lambda m: (m[1][1], local[m[0]]["source_ref"])):
        row = local[local_id]
        print(f"  {row['source_ref']:<24} by {how:<9} {row['name'][:40]:<40} ({row['org_type']})\n"
              f"      -> {p['name'][:60]} ({p['org_type']}, {p['id'][:8]}){because(row['source_ref'])}")
    inserted_offices = final["gov_organizations"]
    print(f"\noffices created, parents first: {len(inserted_offices)}")
    for row in inserted_offices:
        parent = row["parent_organization_id"]
        under = prod_name.get(parent) or local.get(parent, {}).get("source_ref") or "no parent"
        decided = " (decided new)" if decisions.get(row["source_ref"]) == "new" else ""
        print(f"  {row['source_ref']:<26} {row['org_type']:<26} {row['name'][:34]:<34} under {under[:40]}"
              f"{decided}{because(row['source_ref'])}")
    if held:
        print(f"\noffices held until --offices decides them: {len(held)}, "
              f"with {len(held_rows['gov_organizations']) - len(held)} new offices under them")
        for local_id, twins in held.items():
            row = local[local_id]
            print(f"  {row['source_ref']:<26} {row['org_type']:<26} {row['name'][:48]}")
            for p in twins:
                print(f"      twin {p['id']}  {p['org_type']:<24} {p['name'][:60]}")

    columns = ("export", "reused", "refused", "skipped", "held", "inserted")
    print("\n" + f"{'table':<34}" + "".join(f"{c:>9}" for c in columns))
    totals = Counter()
    for table in ("agencies", *TABLES):
        held_keys = held_rows.get(table, set())
        refused_keys = refused.get(table, set()) - held_keys
        counts = {"export": len(export[table]), "reused": reused.get(table, 0), "refused": len(refused_keys),
                  "skipped": len(dropped.get(table, set()) - held_keys - refused_keys),
                  "held": len(held_keys), "inserted": len(final.get(table, ()))}
        assert counts["export"] == sum(v for k, v in counts.items() if k != "export"), (table, counts)
        totals.update(counts)
        print(f"{table:<34}" + "".join(f"{counts[c]:>9}" for c in columns))
    print(f"{'total':<34}" + "".join(f"{totals[c]:>9}" for c in columns))

    detail = sum(len(rows) for t, rows in final.items() if pk[t] != ("id",))
    reused_by = ", ".join(f"{n:,} {t}" for t, n in reused.items() if n)
    print(f"""
reconciliation
  {totals['export']:>9,}  rows in the export
  {totals['reused']:>9,}  already in production, so the load points at production's row ({reused_by})
  {totals['refused']:>9,}  refused by production: federal procurement sources, which production's check
             constraint does not accept (government_level must be state, local, tribal or territorial)
  {totals['skipped']:>9,}  left out because production already states the same fact under its own id
  {totals['held']:>9,}  held until an office decision is written down
  {totals['inserted']:>9,}  inserted by the load file
  {totals['inserted'] - detail:>9,}  ids in the manifest: inserted rows less the {detail:,} detail rows, which have no id
             of their own and cannot commit without the assertion the manifest lists
  Production's insert triggers also mark the agency page stale, queue page-compile jobs and record each new
  need's first lifecycle state. Those rows are production's own and are not counted above.""")
    if skipped_items:
        print(f"\nBrain items skipped because production holds the live claim key: {len(skipped_items)}")
        for row, prod_id in skipped_items:
            print(f"  {row['claim_key'][:70]:<70} fy {row['fiscal_year']}  production {prod_id}")
    print(f"\ntransactions: {sum(len(b) for _, _, b in plan)} "
          f"({', '.join(f'{len(b)} {kind}' for kind, _, b in plan)})")


def emit(args, plan, guard, depth, carried, pk, final) -> None:
    path = args.emit_sql
    manifest, check = path.with_suffix(".manifest.csv"), path.with_suffix(".check.sql")
    inserted = Counter({t: len(rows) for t, rows in final.items()})
    total = sum(len(b) for _, _, b in plan)
    lines = [
        f"-- Navy promotion, generated by research/tools/promote_plan.py on {datetime.date.today()}",
        f"-- from the export database {args.db} and production {snapshot_label(args.snapshot) if args.snapshot else 'read live'}.",
        "-- Read it, then run it: psql \"$DATABASE_URL\" -f " + path.name,
        "-- The layer tables are append-only: UPDATE and DELETE raise, and an assertion allows",
        "-- one retraction and nothing else. Inserted rows stay.",
        f"-- {sum(inserted.values())} rows in {total} transactions. A failed transaction undoes only itself;",
        "-- rerun the file and committed rows are skipped by primary key. Any other clash stops it.",
        f"-- {manifest.name} lists every inserted id; {check.name} counts the ones missing.",
        *(f"--   {n:>7}  {t}" for t, n in inserted.items() if n),
        "",
        "\\set ON_ERROR_STOP on",
        "set statement_timeout = '120s';",
        "set lock_timeout = '5s';",
        "set standard_conforming_strings = on;",
        "",
        "-- Stop if the production offices this file points at changed since the snapshot.",
        guard,
        "",
    ]
    listed = []
    number = 0
    for kind, tables, kind_batches in plan:
        for batch in kind_batches:
            number += 1
            counts = Counter(t for group in batch for t, _ in group)
            what = ", ".join(f"{counts[t]} {t}" for t in tables if counts[t])
            lines += [f"\\echo {number}/{total} {kind} ({what})", "begin;",
                      *transaction_sql(tables, batch, depth, carried, pk), "commit;", ""]
            listed += [(t, r["id"]) for group in batch for t, r in group if pk[t] == ("id",)]
    path.write_text("\n".join(lines) + "\n")
    with manifest.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("table_name", "id"))
        writer.writerows(listed)
    check.write_text(check_sql(path.name, manifest.name, [t for t in TABLES if pk[t] == ("id",) and inserted[t]]))
    print(f"\nwrote {path}, {manifest.name} ({len(listed)} ids) and {check.name}")
    print("Read it, then run it yourself. This tool will not.")


def selfcheck() -> int:
    assert office_codes({"name": "Tactical Networks (PMW 160)"}) == {"PMW160"}
    # A single letter after the slash is a service variant of one office.
    assert office_codes({"name": "Communications and GPS Navigation (PMW/A 170)"}) == {"PMW170"}
    # Two families after the slash are two designations of one office, and a
    # production row may be filed under either, so both have to be searchable.
    assert office_codes({"name": "PMA/PMW 101 MIDS"}) == {"PMA101", "PMW101"}
    assert office_codes({"acronym": "PMS 485"}) == {"PMS485"}
    assert office_codes({"external_ids": {"office_code": "PMW 740"}}) == {"PMW740"}
    assert office_codes({"name": "NAVWAR HQ contracts directorate"}) == set()

    assert compatible("program_office", "program_office")
    assert compatible("direct_reporting_program_manager", "program_office")
    assert compatible("contracting_activity", "contracting_office")
    # The one that matters: a buy must never land on a contracting desk as its owner.
    assert not compatible("program_office", "contracting_office")
    assert not compatible("program_executive_office", "program_office")

    loc = [{"id": "L1", "source_ref": "pmw:160", "name": "PMW 160 Tactical Networks",
            "org_type": "program_office", "aliases": [], "external_ids": {}},
           {"id": "L2", "source_ref": "pmw:999", "name": "Nowhere Office",
            "org_type": "program_office", "aliases": [], "external_ids": {}},
           {"id": "L3", "source_ref": "dup", "name": "Twin",
            "org_type": "program_office", "aliases": [], "external_ids": {}}]
    pro = [{"id": "P1", "name": "Tactical Networks (PMW 160)", "org_type": "program_office",
            "aliases": [], "external_ids": {}},
           {"id": "P2", "name": "Twin", "org_type": "program_office", "aliases": [], "external_ids": {}},
           {"id": "P3", "name": "Twin", "org_type": "program_office", "aliases": [], "external_ids": {}},
           {"id": "P4", "name": "PMW 160 Contracts", "org_type": "contracting_office",
            "aliases": [], "external_ids": {}}]
    resolved, ambiguous, unmatched = match_offices(loc, pro)
    assert set(resolved) | set(ambiguous) | {r["id"] for r in unmatched} == {"L1", "L2", "L3"}
    # P4 shares the code but not the family, so the code match stays single.
    assert resolved["L1"]["prod"]["id"] == "P1" and resolved["L1"]["how"] == "office code"
    assert [r["id"] for r in unmatched] == ["L2"]
    assert "L3" in ambiguous and len(ambiguous["L3"]["candidates"]) == 2

    # Two local offices resolving onto one production row merges them permanently,
    # so both sides of the collision are refused rather than either being picked.
    twins = [{"id": "A", "source_ref": "a", "name": "PMW 300 One", "org_type": "program_office",
              "aliases": [], "external_ids": {}},
             {"id": "B", "source_ref": "b", "name": "PMW 300 Two", "org_type": "program_office",
              "aliases": [], "external_ids": {}}]
    one = [{"id": "P9", "name": "Something (PMW 300)", "org_type": "program_office",
            "aliases": [], "external_ids": {}}]
    r2, a2, u2 = match_offices(twins, one)
    assert r2 == {}, "a shared production row must not resolve for either office"
    assert set(a2) == {"A", "B"} and "shares this production row with b" in a2["A"]["note"]

    def refused(call) -> bool:
        try:
            call()
        except SystemExit:
            return True
        return False

    # Agency-level rows match on their agency; a same-named twin of any type holds an
    # office until a person decides; an office production never named is created.
    def office(i, ref, name, org_type, **extra):
        return {"id": i, "source_ref": ref, "name": name, "org_type": org_type, "acronym": None,
                "aliases": [], "external_ids": {}, "existing_agency_id": None, **extra}
    prod_offices = [office("PA", None, "Department of the Navy", "other", existing_agency_id="AG-P"),
                    office("PN", None, "Naval Sea Systems Command", "other", acronym="NAVSEA"),
                    office("PC", None, "Tactical Networks (PMW 160)", "program_office")]
    export_offices = [office("LA", "agency:don", "Department of the Navy", "agency", existing_agency_id="AG-L"),
                      office("LN", "command:navsea", "NAVSEA", "contracting_activity"),
                      office("LC", "pmw:160", "PMW 160", "program_office"),
                      office("LX", "lab:x", "Brand New Lab", "technical_office")]
    agencies = {"AG-L": "AG-P"}
    m, c, h = resolve_offices(export_offices, prod_offices, agencies, {})
    assert m["LA"][0]["id"] == "PA" and m["LA"][1] == "agency"
    assert m["LC"][0]["id"] == "PC" and m["LC"][1] == "office code"
    assert set(h) == {"LN"} and [p["id"] for p in h["LN"]] == ["PN"], "NAVSEA is a twin by acronym"
    assert [r["id"] for r in c] == ["LX"]
    m, c, h = resolve_offices(export_offices, prod_offices, agencies, {"command:navsea": "PN"})
    assert m["LN"] == (prod_offices[1], "decision") and not h
    m, c, h = resolve_offices(export_offices, prod_offices, agencies, {"command:navsea": "new"})
    assert {r["id"] for r in c} == {"LN", "LX"} and not h
    assert refused(lambda: resolve_offices(export_offices, prod_offices, agencies, {"command:navsea": "P?"}))
    assert refused(lambda: resolve_offices(export_offices, prod_offices, agencies, {"nowhere": "new"}))
    # A decision onto the row another office matched would merge the two.
    assert refused(lambda: resolve_offices(export_offices, prod_offices, agencies, {"command:navsea": "PC"}))

    # Foreign keys take the production id; an unmapped target keeps its export id.
    rows = {"gov_contact_positions": [{"id": "p", "contact_id": "Lc", "organization_id": "Lo"}]}
    remap(rows, {"gov_contact_positions": [("contact_id", "gov_contacts"),
                                           ("organization_id", "gov_organizations")]},
          {"gov_contacts": {"Lc": "Pc"}, "gov_organizations": {"Lx": "Px"}})
    assert rows["gov_contact_positions"][0] == {"id": "p", "contact_id": "Pc", "organization_id": "Lo"}

    # A key production holds under another id collides; under the same id it is a rerun.
    keys = unique("source", "source_key")
    mine = [{"id": "n1", "source": "s", "source_key": "k"}, {"id": "n2", "source": "s", "source_key": "k2"},
            {"id": "n3", "source": "s", "source_key": None}]
    theirs = [{"id": "p1", "source": "s", "source_key": "k"}, {"id": "n2", "source": "s", "source_key": "k2"},
              {"id": "p3", "source": "s", "source_key": None}]
    assert [(r["id"], p) for r, p in collisions(mine, theirs, keys)] == [("n1", "p1")]
    now = current("a", "b")
    assert now({"a": 1, "b": None, "valid_to": None}) == [(1, None)]
    assert now({"a": 1, "b": None, "valid_to": "2026-01-01"}) == []
    item = {"agency_id": "AG", "scope_organization_id": None, "claim_key": "k", "fiscal_year": None,
            "superseded_by": None}
    assert live_claims(item) == [("agency_id", "AG", "k", 0)]
    assert live_claims({**item, "superseded_by": "x"}) == []

    # A held office takes its new children and every row pointing at them; a lost
    # evidence link takes its assertion, which takes its other links, its detail row
    # and the assertion superseding it.
    pk = {"gov_organizations": ("id",), "gov_contact_positions": ("id",), "agency_brain_items": ("id",),
          "gov_intelligence_evidence": ("id",), ASSERTIONS: ("id",), "gov_need_organizations": ("assertion_id",),
          "gov_assertion_evidence": ("assertion_id", "evidence_id", "relationship")}
    fks = {"gov_organizations": [("parent_organization_id", "gov_organizations")],
           "gov_contact_positions": [("organization_id", "gov_organizations")],
           "agency_brain_items": [("primary_organization_id", "gov_organizations")],
           "gov_intelligence_evidence": [("brain_item_id", "agency_brain_items")],
           ASSERTIONS: [("supersedes_id", ASSERTIONS)],
           "gov_need_organizations": [("assertion_id", ASSERTIONS), ("organization_id", "gov_organizations")],
           "gov_assertion_evidence": [("assertion_id", ASSERTIONS), ("evidence_id", "gov_intelligence_evidence")]}
    rows = {"gov_organizations": [{"id": "child", "parent_organization_id": "held"},
                                  {"id": "free", "parent_organization_id": None}],
            "gov_contact_positions": [{"id": "pos", "organization_id": "child"}],
            "agency_brain_items": [{"id": "item", "primary_organization_id": "child"},
                                   {"id": "item2", "primary_organization_id": "free"}],
            "gov_intelligence_evidence": [{"id": "ev", "brain_item_id": "item"},
                                          {"id": "ev2", "brain_item_id": "item2"}],
            ASSERTIONS: [{"id": "a1", "supersedes_id": None}, {"id": "a2", "supersedes_id": "a1"},
                         {"id": "a3", "supersedes_id": None}],
            "gov_need_organizations": [{"assertion_id": "a1", "organization_id": "free"},
                                       {"assertion_id": "a3", "organization_id": "free"}],
            "gov_assertion_evidence": [{"assertion_id": "a1", "evidence_id": "ev", "relationship": "supports"},
                                       {"assertion_id": "a1", "evidence_id": "ev2", "relationship": "supports"},
                                       {"assertion_id": "a3", "evidence_id": "ev2", "relationship": "supports"}]}
    dropped = {"gov_organizations": {("held",)}}
    propagate(rows, pk, fks, dropped)
    assert dropped["gov_organizations"] == {("held",), ("child",)}
    assert dropped["gov_contact_positions"] == {("pos",)}
    assert dropped["agency_brain_items"] == {("item",)} and dropped["gov_intelligence_evidence"] == {("ev",)}
    assert dropped[ASSERTIONS] == {("a1",), ("a2",)}
    assert dropped["gov_need_organizations"] == {("a1",)}
    assert dropped["gov_assertion_evidence"] == {("a1", "ev", "supports"), ("a1", "ev2", "supports")}

    # Production refuses federal sources and a missing level; the other four load.
    levels_seen = ["federal", None, "state", "local", "tribal", "territorial"]
    assert [REFUSED["gov_procurement_sources"]({"government_level": g}) for g in levels_seen] == \
        [True, True, False, False, False, False]

    # A row needs the row it points at inserted first, at any depth.
    chain = [{"id": "c", "p": "b"}, {"id": "a", "p": None}, {"id": "b", "p": "a"}, {"id": "z", "p": "elsewhere"}]
    assert levels(chain, "p") == {"a": 0, "b": 1, "c": 2, "z": 0}
    assert refused(lambda: levels([{"id": "x", "p": "y"}, {"id": "y", "p": "x"}], "p"))

    # Batches cap the parent rows, keep each child with its parent, and put a
    # superseded assertion no later than the one superseding it.
    kind = (ASSERTIONS, "gov_need_organizations", "gov_assertion_evidence")
    chain = [{"id": f"a{i}", "supersedes_id": f"a{i - 1}" if i % 2 else None} for i in range(5)]
    rows = {ASSERTIONS: chain,
            "gov_need_organizations": [{"assertion_id": r["id"]} for r in chain],
            "gov_assertion_evidence": [{"assertion_id": r["id"], "evidence_id": "e", "relationship": "supports"}
                                       for r in chain]}
    depth = levels(chain, "supersedes_id")
    split = batches(kind, rows, fks, depth, size=2)
    assert [len(b) for b in split] == [2, 2, 1]
    where = {r["id"]: n for n, b in enumerate(split) for group in b for t, r in group if t == ASSERTIONS}
    assert all(len(group) == 3 and {r.get("assertion_id", r.get("id")) for _, r in group} == {group[0][1]["id"]}
               for b in split for group in b)
    assert all(where[r["supersedes_id"]] <= where[r["id"]] for r in chain if r["supersedes_id"])
    # Inside one transaction the superseded row gets its own, earlier statement.
    columns = {ASSERTIONS: ["id", "supersedes_id"], "gov_need_organizations": ["assertion_id"],
               "gov_assertion_evidence": ["assertion_id", "evidence_id", "relationship"]}
    pk[ASSERTIONS] = ("id",)
    statements = transaction_sql(kind, split[1], depth, columns, pk)
    assert [s.split()[2] for s in statements] == [f"public.{ASSERTIONS}", f"public.{ASSERTIONS}",
                                                  "public.gov_need_organizations", "public.gov_assertion_evidence"]
    assert '"a4"' in statements[0] and '"a1"' in statements[1]

    sql = insert_sql("gov_contacts", [{"id": "1", "name": "O'Brien", "aliases": ["x"], "extra": 1}],
                     ["id", "name", "aliases"], ("id",))
    assert sql.startswith('insert into public.gov_contacts ("id", "name", "aliases")')
    assert "'[{\"id\":\"1\",\"name\":\"O''Brien\",\"aliases\":[\"x\"]}]'::jsonb" in sql
    assert sql.endswith('on conflict ("id") do nothing;') and "extra" not in sql
    assert 'on conflict ("assertion_id", "evidence_id", "relationship")' in insert_sql(
        "gov_assertion_evidence", [], ["assertion_id"], ("assertion_id", "evidence_id", "relationship"))

    guard = guard_sql([{"id": "o1", "agency_id": "AG", "updated_at": "2026-08-25T00:00:00+00:00"},
                       {"id": "o2", "agency_id": "ZZ", "updated_at": "2026-09-01T00:00:00+00:00"},
                       {"id": "o3", "agency_id": "ZZ", "updated_at": "2026-09-02T00:00:00+00:00"}],
                      {"AG"}, {"o2"}, {"new1"})
    assert "seen <> 2" in guard and "'2026-09-01T00:00:00+00:00'::timestamptz" in guard
    assert "not (id = any('{new1}'::uuid[]))" in guard

    # Parents load before the rows pointing at them.
    for parent, child in (("gov_organizations", "gov_organization_relationships"),
                          ("gov_organizations", "gov_procurement_sources"),
                          ("gov_contacts", "gov_contact_positions"),
                          ("agency_brain_items", "gov_intelligence_evidence"),
                          ("gov_intelligence_evidence", "gov_assertion_evidence"),
                          ("gov_needs", "gov_need_organizations"),
                          ("gov_need_requirements", "gov_requirement_revisions"),
                          (ASSERTIONS, "gov_requirement_revisions")):
        assert TABLES.index(parent) < TABLES.index(child), f"{parent} must precede {child}"
    assert {"gov_requirement_revisions", "gov_funding_observations", "gov_need_organizations"} <= set(TABLES)

    # With a snapshot, production is read from it alone: a missing table stops, it is never fetched.
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "agencies.json").write_text('[{"id": "a"}]')
        assert prod_rows("agencies", Path(tmp), ("id",)) == [{"id": "a"}]
        assert refused(lambda: prod_rows("gov_organizations", Path(tmp), ("id",)))
    script = fresh_check_sql(["agencies", "gov_needs"], {"agencies": ("id",), "gov_needs": ("id",)})
    assert "set default_transaction_read_only = on;" in script and "\\set ON_ERROR_STOP on" in script
    assert "\\o snapshot/gov_needs.json" in script and 'json_agg(t order by "id")' in script
    assert not re.search(r"^\s*(insert|update|delete|create|alter|drop)\b", script, re.I | re.M)

    assert read_decisions({"a": "p1", "b": {"id": "new", "reason": "no twin is this office"}, "c": {"id": "p2"}}) == (
        {"a": "p1", "b": "new", "c": "p2"}, {"b": "no twin is this office"})

    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(selfcheck() if "--selfcheck" in sys.argv else main(sys.argv[1:]))

#!/usr/bin/env python3
"""Emit the schema subset the loader fills, so the database can be built locally.

    python research/tools/schema_subset.py [OUT.sql]
    python research/tools/schema_subset.py --selfcheck

Starts from the tables `agency_layers_sql.py` inserts into, adds every table reached
through a foreign key or named inside a trigger function on the way, and dumps that
closure from the local Supabase container: tables, constraints, indexes, the trigger
functions (`public` and `private`), the triggers, and any view a function reads.
Owners, grants, row-level-security policies and the `\\restrict` guard that psql 14
cannot parse are dropped; nothing else is edited. Production identities, the other
tables, RLS and the migration history stay where they are.

Environment: `SCHEMA_DB_CONTAINER` (the Supabase Postgres container) and `SCHEMA_DSN`
(the same database over TCP, for the catalog queries).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOADER = ROOT / "research" / "tools" / "agency_layers_sql.py"
CONTAINER = os.environ.get("SCHEMA_DB_CONTAINER", "supabase_db_chromie-security-closeout")
DSN = os.environ.get("SCHEMA_DSN", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
# Filled by triggers on the loaded tables, never by the loader itself.
TRIGGER_FILLED = {"gov_need_lifecycle_history", "brain_jobs"}
DROP_TYPES = {"POLICY", "ROW SECURITY"}
DROP_LINE = re.compile(r"^(\\(un)?restrict\b|SET transaction_timeout\b)")


def query(sql: str) -> list[list[str]]:
    out = subprocess.run(["psql", DSN, "-At", "-F", "\t", "-c", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}")
    return [line.split("\t") for line in out.stdout.splitlines() if line]


def scalar(sql: str) -> str:
    """One value that may span lines (a function body, a view definition)."""
    out = subprocess.run(["psql", DSN, "-At", "-c", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}")
    return out.stdout.rstrip("\n")


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def in_list(names: set[str]) -> str:
    return ", ".join(lit(n) for n in sorted(names))


def loader_tables() -> set[str]:
    found = set(re.findall(r'insert\("public\.([a-z_]+)"', LOADER.read_text(encoding="utf-8")))
    assert found, "no insert targets found in the loader"
    return {f"public.{t}" for t in found | TRIGGER_FILLED}


def qualified(regclass_text: str) -> str:
    return regclass_text if "." in regclass_text else f"public.{regclass_text}"


def closure(start: set[str]) -> tuple[set[str], set[str], list[tuple[str, str]], set[str]]:
    """Core tables (loaded, or named in a trigger function on the way), their trigger functions, the
    views those functions read, and the rim: tables the core reaches only by foreign key."""
    core = set(start)
    all_tables = {r[0] for r in query("select n.nspname || '.' || c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                                      "where n.nspname in ('public', 'private') and c.relkind in ('r', 'p')")}
    all_views = {r[0] for r in query("select viewname from pg_views where schemaname = 'public'")}
    functions: dict[str, str] = {}
    src = ""
    while True:
        before = len(core)
        rows = query(f"select distinct n.nspname || '.' || p.proname, p.oid from pg_trigger t join pg_proc p on p.oid = t.tgfoid "
                     f"join pg_namespace n on n.oid = p.pronamespace where not t.tgisinternal and t.tgrelid in {oids(core)}")
        for name, oid in rows:
            functions.setdefault(name, oid)
        if functions:
            src = scalar(f"select string_agg(prosrc, E'\\n') from pg_proc where oid in ({', '.join(functions.values())})")
            words = set(re.findall(r"[a-z_]+", src))
            # A public table is named bare or qualified; a private one only ever qualified.
            core |= {t for t in all_tables if t.split(".")[1] in words and (t.startswith("public.") or t in src)}
        if len(core) == before:
            break
    rim = {qualified(t) for (t,) in query(f"select distinct confrelid::regclass::text from pg_constraint "
                                           f"where contype = 'f' and conrelid in {oids(core)}")} - core
    views = all_views & set(re.findall(r"[a-z_]+", src)) if functions else set()
    return core, rim, sorted(functions.items()), views


def oids(tables: set[str]) -> str:
    return (f"(select c.oid from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            f"where n.nspname || '.' || c.relname in ({in_list(tables)}))")


def stubs(rim: set[str], core: set[str]) -> str:
    """A rim table as only the columns the core's foreign keys reach, so the constraints resolve."""
    parts = []
    for t in sorted(rim):
        cols = query(f"select distinct a.attname, format_type(a.atttypid, a.atttypmod) from pg_constraint c "
                     f"join pg_attribute a on a.attrelid = c.confrelid and a.attnum = any(c.confkey) "
                     f"where c.contype = 'f' and c.confrelid = '{t}'::regclass and c.conrelid in {oids(core)} order by 1")
        keys = {tuple(k.split(",")) for (k,) in query(
            f"select string_agg(a.attname, ',' order by a.attnum) from pg_constraint c "
            f"join pg_attribute a on a.attrelid = c.confrelid and a.attnum = any(c.confkey) "
            f"where c.contype = 'f' and c.confrelid = '{t}'::regclass and c.conrelid in {oids(core)} group by c.oid")}
        body = ",\n".join([f"    {name} {typ}" for name, typ in cols] + [f"    UNIQUE ({', '.join(k)})" for k in sorted(keys)])
        parts.append(f"--\n-- Name: {t}; Type: TABLE (stub: only the columns foreign keys reach; the full definition is not included)\n--\n\n"
                     f"CREATE TABLE {t} (\n{body}\n);\n")
    return "\n".join(parts)


def dump(tables: set[str]) -> str:
    cmd = ["docker", "exec", CONTAINER, "pg_dump", "-s", "-U", "postgres", "-d", "postgres",
           "--no-owner", "--no-privileges", "--no-security-labels", "--no-publications",
           "--no-subscriptions", "--no-tablespaces"]
    for t in sorted(tables):
        cmd += ["-t", t]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"pg_dump failed: {out.stderr.strip()}")
    return out.stdout


def strip(text: str) -> str:
    """Drop policy and row-security objects whole, and the lines psql 14 or Postgres 15 reject."""
    kept = []
    for block in re.split(r"\n(?=--\n-- Name: )", text):
        m = re.match(r"--\n-- Name: .*?; Type: ([A-Z ]+); Schema:", block)
        if m and m.group(1) in DROP_TYPES:
            continue
        kept.append("\n".join(l for l in block.split("\n") if not DROP_LINE.match(l)))
    return "\n".join(kept)


def function_defs(functions: list[tuple[str, str]]) -> str:
    parts = []
    for name, oid in functions:
        body = scalar(f"select pg_get_functiondef({oid})")
        parts.append(f"--\n-- Name: {name}; Type: FUNCTION\n--\n\n{body};\n")  # pg_get_functiondef ends without one
    return "\n".join(parts)


def view_defs(views: set[str]) -> str:
    parts = []
    for v in sorted(views):
        body = scalar(f"select pg_get_viewdef('public.{v}'::regclass, true)")
        parts.append(f"--\n-- Name: {v}; Type: VIEW\n--\n\nCREATE VIEW public.{v} AS\n{body}\n")
    return "\n".join(parts)


def build() -> str:
    start = loader_tables()
    core, rim, functions, views = closure(start)
    watermark = scalar("select max(version) from supabase_migrations.schema_migrations")
    schemas = sorted(({name.split(".")[0] for name, _ in functions} | {t.split(".")[0] for t in core | rim}) - {"public"})
    head = [
        "-- Generated by research/tools/schema_subset.py; do not edit by hand.",
        f"-- Platform migrations applied in the source database through version {watermark}.",
        f"-- {len(core)} tables in full ({len(start)} filled by agency_layers_sql.py or its triggers, the rest named in a",
        f"-- trigger function), {len(functions)} trigger functions, {len(views)} view(s), and {len(rim)} stub tables that",
        "-- exist only so the foreign keys resolve. Owners, grants and row-level-security policies are not",
        "-- included; nothing else was edited.",
        "",
        "SET check_function_bodies = false;",
        *[f"CREATE SCHEMA IF NOT EXISTS {s};" for s in schemas],
        "",
    ]
    body = strip(dump(core))
    if "extensions.gin_trgm_ops" in body:  # trigram indexes; pg_trgm ships with every Postgres build
        head += ["CREATE SCHEMA IF NOT EXISTS extensions;", "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;", ""]
    # Stubs and functions before the dump (its constraints and triggers need them); views after.
    marker = "SET default_table_access_method = heap;\n"
    body = body.replace(marker, marker + "\n" + stubs(rim, core) + "\n" + function_defs(functions), 1)
    return "\n".join(head) + body + "\n" + view_defs(views)


def selfcheck() -> int:
    sample = ("\\restrict abc\nSET transaction_timeout = 0;\nSET client_encoding = 'UTF8';\n\n"
              "--\n-- Name: t; Type: TABLE; Schema: public; Owner: -\n--\n\nCREATE TABLE public.t (id uuid);\n\n"
              "--\n-- Name: t p; Type: POLICY; Schema: public; Owner: -\n--\n\nCREATE POLICY p ON public.t\n  USING ((select 1)\n   = 1);\n\n"
              "--\n-- Name: t; Type: ROW SECURITY; Schema: public; Owner: -\n--\n\nALTER TABLE public.t ENABLE ROW LEVEL SECURITY;\n\n"
              "--\n-- Name: t t_trg; Type: TRIGGER; Schema: public; Owner: -\n--\n\nCREATE TRIGGER t_trg BEFORE INSERT ON public.t FOR EACH ROW EXECUTE FUNCTION private.f();\n")
    got = strip(sample)
    assert "restrict" not in got and "transaction_timeout" not in got, got
    assert "CREATE POLICY" not in got and "ROW LEVEL SECURITY" not in got, "policies are dropped whole, however many lines"
    assert "CREATE TABLE public.t" in got and "CREATE TRIGGER t_trg" in got and "client_encoding" in got, got
    assert loader_tables() >= {"public.gov_organizations", "public.gov_needs", "public.gov_intelligence_assertions", "public.brain_jobs"}
    assert qualified("agencies") == "public.agencies" and qualified("private.usa_awards") == "private.usa_awards"
    stub = "CREATE TABLE public.gov_procurement_sources (\n    id uuid,\n    UNIQUE (id)\n);"
    assert re.search(STUB_RE.format(table=re.escape("public.gov_procurement_sources")), stub), "the kit's stub shape is what is replaced"
    print("schema_subset selfcheck ok")
    return 0


# A machine without the platform database can build from an exported kit (the folder handed over on 2026-09-21
# holds `schema.sql` as this tool wrote it then). That export stubs `gov_procurement_sources` to its id, and the
# loader has since filled the table; the stand-in below carries the columns the loader writes, typed from the values
# it writes, so the rows load. It is a local stand-in for a proof database, not the platform's definition of the
# table, and the header of the written file says so.
KIT_STAND_INS = {
    "public.gov_procurement_sources": """CREATE TABLE public.gov_procurement_sources (
    id uuid NOT NULL,
    source_key text NOT NULL,
    provider_name text,
    portal_name text,
    jurisdiction_code text,
    jurisdiction_path text[],
    government_level text,
    official_url text,
    adapter_key text,
    access_mode text,
    capabilities jsonb,
    refresh_cadence text,
    last_verified_at date,
    verification_status text,
    coverage_notes text,
    known_access_gaps jsonb,
    metadata jsonb,
    UNIQUE (id),
    UNIQUE (source_key)
);"""}
STUB_RE = r"CREATE TABLE {table} \(\n    id uuid,\n    UNIQUE \(id\)\n\);"


def from_kit(kit: Path) -> str:
    """The kit's exported schema with the stubs the loader has since filled replaced by their stand-ins."""
    text = (kit / "schema.sql").read_text(encoding="utf-8")
    replaced = []
    for table, ddl in KIT_STAND_INS.items():
        text, n = re.subn(STUB_RE.format(table=re.escape(table)), lambda _m, ddl=ddl: ddl, text, count=1)
        if n:
            replaced.append(table)
    head = (f"-- Built by research/tools/schema_subset.py --from-kit from {kit.name}/schema.sql (the platform export of that\n"
            f"-- date), not from the platform database. Stand-in definitions replace the export's stubs for the tables the\n"
            f"-- loader has since filled: {', '.join(replaced) or 'none'}. A proof database only; not the platform's schema.\n\n")
    return head + text


def main(argv: list[str]) -> int:
    if "--selfcheck" in argv:
        return selfcheck()
    if "--from-kit" in argv:
        kit = Path(argv[argv.index("--from-kit") + 1])
        argv = [a for i, a in enumerate(argv) if a != "--from-kit" and argv[i - 1] != "--from-kit"]
        text = from_kit(kit)
    else:
        text = build()
    if argv:
        Path(argv[0]).write_text(text, encoding="utf-8")
        print(f"wrote {argv[0]} ({len(text.splitlines())} lines)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

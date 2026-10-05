#!/usr/bin/env python3
"""Run the agency pipeline end to end: collect, model, load, read back.

    python research/tools/pipeline.py [--db navy_proof_ab] [--collect] [--from STAGE] [--only STAGE]
    python research/tools/pipeline.py --agency darpa [--db darpa_proof] ...   # the same stages for another agency
    python research/tools/pipeline.py --list
    python research/tools/pipeline.py --selfcheck

Every stage is one of the tools beside this file, run in the order the records depend on each
other: the sources are collected first, then modelled into the datapack and the news records,
then emitted as rows, then loaded, then read back by the checks. The agency is a profile
(`research/tools/agency.py`, selected by `--agency` or the `AGENCY` environment variable): the
office tables, the filters, the feeds and the artefact folders come from it, and the memory stage
runs the profile's own reader. A stage the profile has no source for (a forecast, a fiscal-year
table) is skipped and says so (research/docs/14_reusing_this_for_another_agency.md).

`--collect` adds the two network stages, which are skipped by default so a rebuild is
deterministic and offline. The database stage drops and recreates the named database, so it
never edits one that other work is reading.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
# `--agency` is read before the profile is imported, so every stage below sees the same profile.
if "--agency" in sys.argv[1:]:
    os.environ["AGENCY"] = sys.argv[sys.argv.index("--agency") + 1]
sys.path.insert(0, str(TOOLS))
from agency import BUILD, KEY as AGENCY, P, PROFILES  # noqa: E402

PY = sys.executable
DSN_HOST = os.environ.get("PGHOST", "127.0.0.1")
DSN_PORT = os.environ.get("PGPORT", "54322")
DSN_USER = os.environ.get("PGUSER", "postgres")

# name, what it does, whether it needs the network, and how it is run. A stage is a command, not
# a function, so each one is also runnable on its own and prints the same output either way.
STAGES = [
    ("watch", "poll the feeds for articles not yet saved", True,
     [PY, str(TOOLS / "news.py"), "watch", "--fetch"]),
    ("sweep", "one discovery query per office, and take what is new", True,
     [PY, str(TOOLS / "news.py"), "sweep", "--fetch"]),
    ("audits", "poll oversight.gov and the GAO feed for reports not yet saved", True,
     [PY, str(TOOLS / "oversight.py"), "watch", "--fetch"]),
    ("podium", "poll the speech archive and the House hearing feeds for documents not yet saved", True,
     [PY, str(TOOLS / "remarks.py"), "watch", "--fetch"]),
    ("conferences", "find conference pages that name the agency's officials through Exa and take each one not yet saved", True,
     [PY, str(TOOLS / "remarks.py"), "discover", "--fetch"]),
    ("contracts", "sweep FPDS for the base awards each contracting office signed, page by page", True,
     [PY, str(TOOLS / "fpds_sweep.py"), "sweep", "--fetch"]),
    ("solicitations", "sweep SAM.gov for every notice each contracting office posted since FY22 and harvest the new ones", True,
     [PY, str(TOOLS / "sam_notices.py"), "sweep"]),
    ("topics", "sweep the DoD SBIR/STTR portal newest first for every topic since FY2020 and keep the agency's details", True,
     [PY, str(TOOLS / "sbir.py"), "sweep", "--fetch"]),
    ("prerelease", "for every monthly portal row, take the pages with no attempt recorded since the cycle's first Wednesday, refusals recorded", True,
     [PY, str(TOOLS / "prerelease.py"), "sweep", "--fetch"]),
    ("changes", "follow the FPDS history of every swept award running or ended within a year, for extensions, options and terminations", True,
     [PY, str(TOOLS / "fpds_sweep.py"), "histories", "--fetch"]),
    ("dockets", "take GAO's docket of the agency's bid protests and the case page of every new or still-open case", True,
     [PY, str(TOOLS / "protests.py"), "watch", "--fetch"]),
    ("reports", "list the committee reports on govinfo and take the NDAA and defense appropriations reports not yet saved", True,
     [PY, str(TOOLS / "congress.py"), "sweep", "--fetch"]),
    ("register", "take the agency's Federal Register documents", True,
     [PY, str(TOOLS / "fedreg.py"), "sweep", "--fetch"]),
    ("grants", "take the grants and cooperative agreements the agency awards from USAspending, page by page", True,
     [PY, str(TOOLS / "assistance.py"), "sweep", "--fetch"]),
    ("ai_inventory", "take the federal AI Use Case Inventory file and its latest commit (once; one file serves every agency)", True,
     [PY, str(TOOLS / "ai_inventory.py"), "sweep", "--fetch"]),
    ("outreach", "take the Department of War's directory of small business offices and each office page it links to not yet saved", True,
     [PY, str(TOOLS / "small_business.py"), "collect"]),
    ("orgpages", "take the official organization pages the memory reads or still wants (departments, leadership, offices) not yet saved", True,
     [PY, str(TOOLS / P["forecast"]["memory_tool"]), "collect"]),
    ("program_spend", "take the agency's program listing and search USAspending for the fiscal year's contract transactions naming each current "
                      "program (terms from the last built budget lines; a profile with no program listing collects nothing)", True,
     [PY, str(TOOLS / "programs.py"), "collect"]),
    ("profiles", "look up public LinkedIn profiles through Exa for leaders the agency publishes no biography of, and search each program "
                 "office no known person leads (people from the last build; a key is needed, without one the stage says so)", True,
     [PY, str(TOOLS / "stakeholders.py"), "collect"]),
    ("vacancies", "list every job announcement USAJobs filed under the agency codes the profile maps, and take the announcement pages of the "
                  "acquisition workforce's and of any naming a known office", True,
     [PY, str(TOOLS / "jobs.py"), "sweep", "--fetch"]),
    ("vendor_vacancies", "ask RouterGrowth for the postings of the incumbents with live awards at the swept offices, and take the company's own "
                         "pages the answers point at (a key is needed; without one the stage records that and collects nothing)", True,
     [PY, str(TOOLS / "vendor_jobs.py"), "sweep", "--fetch"]),
    ("joins", "look up every contract and line the newest forecast releases cite in FPDS, SAM.gov and USAspending, and the rest of "
              "each contract's FPDS history", True,
     [PY, str(TOOLS / "lrae_package.py"), "collect"]),
    ("spending", "take the fiscal year's USAspending totals for the profile's funding subtier (obligations by month for contracts, vehicles and "
                 "assistance, and the top recipients), the agency-wide views of the office owners report", True,
     [PY, str(TOOLS / "office_owners.py"), "collect"]),
    ("memory", "read the agency's organization sources into the organization memory", False,
     [PY, str(TOOLS / P["forecast"]["memory_tool"]), "build"]),
    ("routes", "contact observations, route recommendations and the review log from the agency's staff listing and its notice contacts, "
               "compared with the saved files (a hand-written, reviewed set is left as it is)", False,
     [PY, str(TOOLS / "contact_routes.py"), "build", "--check"]),
    ("datapack", "read the forecast releases into the datapack and compare them", False,
     [PY, str(TOOLS / "lrae_package.py"), "build"]),
    ("oversight", "read every saved oversight report into dated findings (cassettes replay; a new report is one agent call)", False,
     [PY, str(TOOLS / "oversight.py"), "extract"]),
    ("remarks", "read every saved speech, statement and conference page into dated events (cassettes replay)", False,
     [PY, str(TOOLS / "remarks.py"), "extract"]),
    ("budget", "read every saved budget justification book into P-1 line items and R-1 program elements with their fiscal-year amounts, "
               "and the program blocks the R-2A pages fund", False,
     [PY, str(TOOLS / "budget.py"), "extract"]),
    ("program_money", "each listed program (or, for an agency that lists none, each book line and program block, tied to the office "
                      "that runs it) with its manager, the budget book's amounts and stated need, the contract money found against it "
                      "in the fiscal year and the upper bound left", False,
     [PY, str(TOOLS / "programs.py"), "build"]),
    ("news", "model every saved article as dated observations", False,
     [PY, str(TOOLS / "news.py"), "build"]),
    ("hiring", "model every saved job announcement as a dated observation about the office that is hiring, read against the record", False,
     [PY, str(TOOLS / "jobs.py"), "build"]),
    ("vendor_hiring", "model every saved contractor careers page as a dated observation about the work the company is staffing, read against the record", False,
     [PY, str(TOOLS / "vendor_jobs.py"), "build"]),
    ("people", "merge every point of contact, forecast POC, speaker and witness the saved sources name into people.json", False,
     [PY, str(TOOLS / "people.py"), "build"]),
    ("stakeholders", "every known person in the buckets of a purchase (decision maker, budget holder, problem owner, champion, contracting ...), "
                     "each with its evidence, per program and per office", False,
     [PY, str(TOOLS / "stakeholders.py"), "build"]),
    ("small_business", "the small business office of the department and each command from the saved directory and office pages, "
                       "compared with the saved file", False,
     [PY, str(TOOLS / "small_business.py"), "build", "--check"]),
    ("programs", "model every saved SBIR/STTR topic of the agency as a dated programs event with the office it names", False,
     [PY, str(TOOLS / "sbir.py"), "build"]),
    ("releases", "the days the portal first showed the agency's topics, read from the saved index pages and compared with the first-Wednesday rule (nothing loaded)", False,
     [PY, str(TOOLS / "prerelease.py"), "build"]),
    ("protests", "read every saved GAO case page into a protest dated the day it was filed", False,
     [PY, str(TOOLS / "protests.py"), "build"]),
    ("directives", "read every saved committee report into the directives it states for the agency", False,
     [PY, str(TOOLS / "congress.py"), "build"]),
    ("federal", "read the saved Federal Register pages into dated documents, typed where the text says what happened", False,
     [PY, str(TOOLS / "fedreg.py"), "build"]),
    ("assistance", "read the saved USAspending pages into dated grant and cooperative agreement awards", False,
     [PY, str(TOOLS / "assistance.py"), "build"]),
    ("ai_use_cases", "read the saved AI Use Case Inventory into the agency's use cases, each under the component that reported it", False,
     [PY, str(TOOLS / "ai_inventory.py"), "build"]),
    ("kinds", "read what every saved special notice announces (cassettes replay; a new notice is one model call), compared with the saved file", False,
     [PY, str(TOOLS / "notice_kinds.py"), "build", "--check"]),
    # The schema comes from the platform's local database; a machine without it names an exported kit in SCHEMA_KIT
    # (research/tools/schema_subset.py --from-kit) and builds a proof database from that export and its stand-ins.
    ("schema", "write the loaded-table subset of the production schema" + (" (from the kit in SCHEMA_KIT)" if os.environ.get("SCHEMA_KIT") else ""), False,
     [PY, str(TOOLS / "schema_subset.py"), *(["--from-kit", os.environ["SCHEMA_KIT"]] if os.environ.get("SCHEMA_KIT") else []), str(BUILD / "schema.sql")]),
    ("layers", "emit the rows: needs, assertions, evidence, organizations, brain items", False,
     [PY, str(TOOLS / "agency_layers_sql.py")]),
    ("database", "create the database and load the schema and the rows", False, None),
    ("graph", "export the organization graph as the program-office resolver reads it", False, None),
    ("revisions", "the forecast-revision alert: every forecast line whose award window or value moved between releases, into build/", False, None),
    ("backtest", "replay the outcome labels and compute the back-test from the frozen corpus into build/", False,
     [PY, str(TOOLS / "backtest.py"), "check"]),
    ("offices", "read every notice filed at a contracting office that no record places, every live award a contracting office signed, and "
                "every topic and committee statement no program office was named for, against the office pages (cassettes replay; a new "
                "record is one model call), compared with the saved file", False,
     [PY, str(TOOLS / "office_wiki.py"), "build", "--check"]),
    ("pulse", "score every cell as of the corpus end, list the week's changes and the actions, and compare with the saved pulse", False,
     [PY, str(TOOLS / "pulse.py"), "check"]),
    ("baselines", "the bar against the dumb baselines, recall by period and the family combinations, into build/", False,
     [PY, str(TOOLS / "baselines.py"), "run"]),
    ("dna", "Buying DNA per contracting office, program office and top cell from the saved FPDS pages, compared with the saved file", False,
     [PY, str(TOOLS / "buying_dna.py"), "build", "--check"]),
    ("vendors", "vendors resolved by UEI from the saved FPDS pages, every spelling and the parent entity, compared with the saved file", False,
     [PY, str(TOOLS / "vendors.py"), "build", "--check"]),
    ("owners", "per program office: who owns which problem, who would champion a fix and who holds the budget, each with its source; the budget "
               "lines with the Comptroller's own labels, the year's obligations from the saved feed and what each office bought; compared with the saved file", False,
     [PY, str(TOOLS / "office_owners.py"), "build", "--check"]),
    ("coverage", "which registered source covers each command and family, and what each source last collected, compared with the saved file", False,
     [PY, str(TOOLS / "coverage.py"), "status", "--check"]),
    ("truth", "the agency truth table: what the files say of every agency layer beside the documents' claims and the dated runtime observations, compared with the saved file", False,
     [PY, str(TOOLS / "agency_truth.py"), "build", "--check"]),
    ("fiscal_year", "read every requirement against what the current fiscal year has produced", False, None),
    ("checks", "every tool's selfcheck and the test suite", False, None),
]
NETWORK = {name for name, _, network, _ in STAGES if network}
# Stages that read a source this profile does not have: the forecast and its datapack, the revision alert over it,
# and the fiscal-year tables written for the Navy layer.
NOT_FOR_PROFILE = set() if P["forecast"]["pack_glob"] else {"datapack", "revisions", "joins"}
if AGENCY != "navy":
    NOT_FOR_PROFILE |= {"fiscal_year"}
if not P["sbir_component"]:  # the DoD portal lists no civilian agency's topics
    NOT_FOR_PROFILE |= {"topics", "prerelease", "releases"}
if not P["hiring"]["usajobs_agency_codes"]:
    NOT_FOR_PROFILE |= {"vacancies", "hiring"}  # a profile that maps no USAJobs agency code has no announcements to list
if not P["hiring"].get("vendor_jobs", True):
    NOT_FOR_PROFILE |= {"vendor_vacancies", "vendor_hiring"}  # a profile that opts out of contractor postings


def refreshed(db: str) -> dict[str, list[list[str]]]:
    """The stages that compare with a saved file, run instead to rewrite it from what was just collected: the corpus is
    frozen from the new database, every outcome is labelled (one labelled before replays its cassette), and the
    results, pulse, books and status follow."""
    tool = lambda name, *rest: [PY, str(TOOLS / name), *rest]
    return {"backtest": [tool("backtest.py", "freeze", "--db", db), tool("backtest.py", "label"), tool("backtest.py", "run")],
            "routes": [tool("contact_routes.py", "build")],
            "kinds": [tool("notice_kinds.py", "build")], "offices": [tool("office_wiki.py", "build")], "small_business": [tool("small_business.py", "build")], "pulse": [tool("pulse.py", "build")], "dna": [tool("buying_dna.py", "build")],
            "vendors": [tool("vendors.py", "build")], "owners": [tool("office_owners.py", "build")], "coverage": [tool("coverage.py", "status")]}


def run(command: list[str], out: Path | None = None, env: dict | None = None) -> int:
    """Run one command, streaming to the terminal or into a file, and report the seconds it took."""
    started = time.time()
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as handle:
            done = subprocess.run(command, cwd=ROOT, stdout=handle,
                                  stderr=subprocess.PIPE, env=env or os.environ.copy())
        if done.stderr:
            (out.parent / (out.stem + ".skipped.txt")).write_bytes(done.stderr)
    else:
        done = subprocess.run(command, cwd=ROOT, env=env or os.environ.copy())
    print(f"    {time.time() - started:5.1f}s  exit {done.returncode}")
    return done.returncode


def psql_env() -> dict:
    env = os.environ.copy()
    env.setdefault("PGPASSWORD", "postgres")
    return env


def load_database(name: str) -> int:
    """Build the database from nothing: a load that cannot half-apply over an older one."""
    base = ["-h", DSN_HOST, "-p", DSN_PORT, "-U", DSN_USER]
    env = psql_env()
    for command in ([["dropdb", "--if-exists", *base, name], ["createdb", *base, name]]):
        if run(command, env=env):
            return 1
    for sql in (BUILD / "schema.sql", BUILD / "layers.sql"):
        if not sql.exists():
            print(f"    {sql.relative_to(ROOT)} is missing; run the schema and layers stages first")
            return 1
        if run(["psql", *base, "-d", name, "-v", "ON_ERROR_STOP=1", "-q", "-f", str(sql)], env=env):
            return 1
    return 0


def fiscal_year(name: str) -> int:
    """Read the loaded requirements against the fiscal year: the universe first, then the dated events."""
    dsn = f"postgresql://{DSN_USER}:postgres@{DSN_HOST}:{DSN_PORT}/{name}"
    fy = ROOT / "research" / "fy2026"
    for command in ([PY, str(fy / "build_universe.py"), "--dsn", dsn], [PY, str(fy / "events.py")]):
        if run(command, out=BUILD / (Path(command[1]).stem + ".txt"), env=psql_env()):
            return 1
    return 0


def checks(db: str) -> int:
    """Read the tools back: each one's selfcheck, then the suite, told which database was built."""
    failures = 0
    # The selfchecks test each tool's rules on Navy fixtures, so they run under the Navy profile whatever agency was
    # built; the suite is then told which database was built.
    rules_env = {**psql_env(), "AGENCY": "navy"}
    for tool in sorted(TOOLS.glob("*.py")):
        # A tool without a selfcheck is covered by the suite, and "._name.py" is the resource fork
        # an exFAT volume leaves beside a file, not a module.
        if tool.name == "pipeline.py" or tool.name.startswith("._"):
            continue
        if "--selfcheck" not in tool.read_text(encoding="utf-8", errors="ignore"):
            print(f"    {tool.name}: no selfcheck, covered by the suite")
            continue
        # An agency's own organization reader tests that agency's records, so it runs under that agency's profile.
        own = tool.stem.removeprefix("org_memory_")
        probe = subprocess.run([PY, str(tool), "--selfcheck"], cwd=ROOT, env={**rules_env, "AGENCY": own} if own in PROFILES else rules_env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if "selfcheck ok" in probe.stdout:
            print(f"    {tool.name}: ok")
        else:
            failures += 1
            print(f"    {tool.name}: FAILED\n{probe.stdout[-800:]}")
    # The suite asserts the Navy records, so it runs under the Navy profile, and reads the built database only when the
    # Navy was built: another agency's database holds none of the rows the Navy's database tests count, so they skip.
    # Another profile then adds its own layer test and the coverage test, read against its files.
    suite_env = {k: v for k, v in psql_env().items() if k != "NAVY_DB"}
    if run([PY, "-m", "pytest", "-q", "tests"], env={**suite_env, **({"NAVY_DB": db} if AGENCY == "navy" else {}), "AGENCY": "navy"}):
        failures += 1
    if AGENCY != "navy":
        own = [str(p) for p in (ROOT / "tests" / "test_agency_layer.py", ROOT / "tests" / f"test_{AGENCY}_layer.py",
                                ROOT / "tests" / "test_coverage.py") if p.exists()]  # the shared layer test reads the AGENCY key
        if own and run([PY, "-m", "pytest", "-q", *own], env={**psql_env(), "NAVY_DB": db, "AGENCY": AGENCY}):
            failures += 1
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pipeline")
    parser.add_argument("--agency", default=AGENCY, help="the agency profile (research/tools/agency.py)")
    parser.add_argument("--db", default=P["database"], help="the database to build from nothing")
    parser.add_argument("--collect", action="store_true", help="include the network stages")
    parser.add_argument("--refresh", action="store_true", help="rewrite the saved results from the new build instead of comparing with them")
    parser.add_argument("--from", dest="start", help="start at this stage")
    parser.add_argument("--only", help="run this stage alone")
    parser.add_argument("--list", action="store_true", help="print the stages and stop")
    args = parser.parse_args(argv)

    names = [name for name, _, _, _ in STAGES]
    if args.list:
        for name, what, network, _ in STAGES:
            print(f"  {name:9} {'(network)' if network else '         '}  {what}")
        return 0
    wanted = names
    if args.only:
        wanted = [args.only]
    elif args.start:
        wanted = names[names.index(args.start):]
    if not args.collect and not args.only:
        wanted = [n for n in wanted if n not in NETWORK]

    BUILD.mkdir(parents=True, exist_ok=True)
    rewrite, missed = refreshed(args.db) if args.refresh else {}, []
    for name, what, _, command in STAGES:
        if name not in wanted:
            continue
        print(f"\n== {name}: {what}")
        if name in NOT_FOR_PROFILE:
            print(f"    skipped: the {AGENCY} profile has no source for this stage")
            continue
        if name in rewrite:
            code = next((c for c in (run(step) for step in rewrite[name]) if c), 0)
        elif name == "database":
            code = load_database(args.db)
        elif name == "graph":
            code = run([PY, str(TOOLS / "graph_export.py"), "--db", args.db], out=BUILD / "program_office_graph.json")
        elif name == "fiscal_year":
            code = fiscal_year(args.db)
        elif name == "revisions":
            dsn = f"postgresql://{DSN_USER}:postgres@{DSN_HOST}:{DSN_PORT}/{args.db}"
            code = run([PY, str(TOOLS / "monitor_forecast_revision.py"), "--dsn", dsn], out=BUILD / "forecast_revisions.txt")
        elif name == "checks":
            code = checks(args.db)
        elif name == "layers":
            code = run(command, out=BUILD / "layers.sql")
        else:
            code = run(command)
        if code and name in NETWORK:
            # A source that did not answer leaves what it saved before; the build goes on without its new pages.
            print(f"\n{name} did not finish collecting; building from what is saved")
            missed.append(name)
        elif code:
            print(f"\n{name} failed; the pipeline stops here so the next stage does not run on half a build")
            return code
    print(f"\ndone: {', '.join(n for n in names if n in wanted)}" + (f"; collection incomplete: {', '.join(missed)}" if missed else ""))
    return 1 if missed else 0


def selfcheck() -> int:
    names = [name for name, _, _, _ in STAGES]
    assert names.index("datapack") < names.index("layers") < names.index("database") < names.index("checks"), \
        "the rows are emitted before they are loaded, and read back after"
    assert names.index("news") < names.index("layers"), "articles are modelled before they are emitted"
    assert names.index("vacancies") < names.index("memory") < names.index("hiring") < names.index("layers"), \
        "announcements are listed with the collection, modelled against the memory, and emitted after"
    assert names.index("vendor_vacancies") < names.index("memory") < names.index("vendor_hiring") < names.index("layers"), \
        "contractor postings are collected with the collection, modelled against the memory, and emitted after"
    assert names.index("coverage") < names.index("truth") < names.index("fiscal_year"), "the truth table reads the coverage status the stage before it wrote"
    assert names.index("programs") < names.index("releases") < names.index("layers"), "the pre-release days are read after the topics and before the rows are emitted"
    assert names.index("database") < names.index("fiscal_year") < names.index("checks"), \
        "the fiscal year is read from the loaded database, and checked after"
    assert names.index("database") < names.index("revisions"), "the revision alert reads the loaded database"
    assert names.index("backtest") < names.index("offices"), "the office reads are made over the frozen corpus"
    assert names.index("dna") < names.index("owners") < names.index("truth"), "the office owners read the frozen corpus, the people and the buying books, and the truth table reads their file"
    assert names.index("budget") < names.index("owners") and names.index("people") < names.index("owners"), "the owners report reads the budget lines and the people file"
    assert names.index("topics") < names.index("prerelease") < names.index("programs") < names.index("releases"), \
        "the portal is swept, then the cycle's pages, then the topics are modelled and the pre-release days read"
    assert names.index("budget") < names.index("program_money") < names.index("stakeholders") and names.index("people") < names.index("stakeholders"), \
        "stakeholders read the people and the programs, and the programs read the budget lines"
    assert NETWORK == {"watch", "sweep", "audits", "podium", "conferences", "contracts", "solicitations", "topics", "prerelease", "changes", "dockets",
                       "reports", "register", "grants", "ai_inventory", "outreach", "orgpages", "program_spend", "profiles", "vacancies", "vendor_vacancies",
                       "joins", "spending"}, "only collection touches the network"
    assert set(refreshed("x")) <= set(names), "every refreshed stage is a stage"
    assert max(names.index(n) for n in NETWORK) < names.index("memory"), "collection runs before any build stage"
    assert all(c is None or c[0] == PY for _, _, _, c in STAGES), "every stage runs this interpreter"
    print("pipeline selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(selfcheck() if "--selfcheck" in sys.argv[1:] else main(sys.argv[1:]))

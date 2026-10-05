#!/usr/bin/env python3
"""The platform's read surface for an agent: every question the Navy record answers, from one command, as of the
record's own date. Each command calls the tool that already answers it; nothing here computes an answer of its own.

    python research/tools/navy.py help                       every command with what it answers
    python research/tools/navy.py search "Link 16"           statements, forecast rows and offices that carry a term
    python research/tools/navy.py check < answer.json        the record's checks on a draft answer
    python research/tools/navy.py sources N00039PRESOL_SF3   where each record is published, with its full text
    python research/tools/navy.py serve                      the same commands as an MCP server on stdio, one tool each
    python research/tools/navy.py --selfcheck

The date is the newest statement in the frozen record, so no answer carries what was public after it.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shlex
import sys
from functools import cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import KEY as AGENCY_KEY, P  # noqa: E402  (the record read is the profile's; the Navy's server keeps its name)
import ask  # noqa: E402
import buying_dna  # noqa: E402
import monitor_forecast_revision  # noqa: E402
import office_wiki  # noqa: E402
import protests as protests_tool  # noqa: E402
import pulse  # noqa: E402
import trace  # noqa: E402
import vendors  # noqa: E402
from agency import P  # noqa: E402
from backtest import CORPUS  # noqa: E402
from outreach import Walk, fixture, record_entry, row_details  # noqa: E402
from outreach_compare import record_checks, resolve, seeing  # noqa: E402
from pages import DNA, Layer, page  # noqa: E402
from pulse import load_people  # noqa: E402

VIEWS = {
    "search": "TERM: statements, forecast rows (full row facts, dropped marks) and offices that carry a term; `matched` says whether the phrase "
              "was found as written or, when the record never repeats it, by its words (each widened to its capability's words)",
    "office": "OFFICE: one office: its chain, forecast rows owned, newest statements, people and small business routes, and beside them "
              "its wiki page, buying book, next actions, incumbents ending, bid protests and forecast revisions",
    "cell": "KEY: one forecast row or statement in full, with the evidence around it and its stage; a statement's `people` are those its own "
            "record names, and `office_contacts` are the office's people, who answer for other solicitations",
    "topics": "TERM: SBIR/STTR topics that carry a term, by office",
    "people": "OFFICE: the people the record ties to an office and the offices above it",
    "neighbors": "OFFICE: the parent, siblings and children of an office, and the organization graph's other edges in its chain: who "
                 "contracts for it, who leads it (current or ended), what it was consolidated into, each with its sources",
    "initiatives": "OFFICE: what leaders, Congress, the budget, oversight, conferences, news and reorganizations say in an office's chain",
}
ASK = ("changed", "match", "prep", "analogs", "incumbents", "moves", "team")
HELP = {
    **VIEWS,
    "page": "office|vendor|person|cell NAME: the object page with its pulse card, buying book and sources",
    "ask": f"{'|'.join(ASK)} ...: the twin's questions (run 'ask QUESTION -h' for the arguments of one)",
    "pulse": "rank [--top N] | week [--days N] | actions [TERM]: the temporal engine's ranked requirements, the week's changes and next actions",
    "protests": f"[TERM]: GAO bid protests against the {P['label']} in the record",
    "wiki": "OFFICE: the office's wiki page: what it buys, in its own words, and the notices read to it",
    "dna": "OFFICE: a contracting office's buying book: awards, vehicles, competition, small business share, top vendors",
    "vendor": "NAME: a vendor resolved by its unique entity identifier, with every spelling and its contracts",
    "trace": "need KEY | notice KEY | award PIID | status [--office ID]: one requirement across releases, notices and awards",
    "revisions": "[TERM]: forecast rows whose award window or value moved between releases",
    "check": "(answer JSON on stdin): the checks the record's own agent must pass; the JSON carries company, profile and the answer",
    "sources": "ID ...: each record in full with where it is published (SAM.gov page, forecast release and row)",
    "inventory": "what this record holds, domain by domain: the files read with their rows and newest dates, the commands that answer each "
                 "domain, the record's date, and whether the corpus is in this checkout (report.py)",
    "report": "plan [--spec NAME] [--focus TERM] [--mode brief|full] [--schema] | budget OFFICE: an intelligence report's plan over this record "
              "(its anchor offices and the exact commands per section, from a spec in research/report_specs/), or the budget an office holds "
              "with the lines that name it (office_owners.py page, budget.py show)",
}


@cache
def layer() -> Layer:
    return Layer(json.loads(CORPUS.read_text(encoding="utf-8")), load_people())


def as_of() -> str:
    return layer().as_of


@cache
def details() -> dict[str, dict[str, str]]:
    return row_details(as_of())


@cache
def everything() -> Walk:
    return seeing(layer())


# What an office view carries from the other layers: the label, the command and the words that lead the office's name.
BESIDE = {"wiki page": ("wiki", ""), "buying book": ("dna", ""), "next actions": ("pulse", "actions "), "incumbents ending": ("ask", "incumbents "),
          "bid protests": ("protests", ""), "forecast revisions": ("revisions", "")}
NOTHING = re.compile(r"^(no |0 protest|\(no output\))")


def beside(office: str, cap: int = 1500) -> dict[str, str]:
    """What the other layers hold for an office, so an agent that opens it sees them without asking for each: in eight
    sessions none of these was asked for on its own. Each is cut at `cap` characters; its command gives the rest."""
    out = {}
    for label, (command, lead) in BESIDE.items():
        text = call(command, {"args": lead + shlex.quote(office)})
        if not NOTHING.match(text):
            out[label] = text[:cap] + (f" ... (navy.py {command} {lead}{office} for the rest)" if len(text) > cap else "")
    return out


def view(tool: str, args: list[str]) -> int:
    got = Walk(layer(), details()).call(tool, " ".join(args))
    if tool == "office" and isinstance(got, dict) and got.get("office"):
        got["beside"] = beside(got["office"])
    print(json.dumps(got, indent=1, ensure_ascii=False, default=str))
    return 0


def protests(args: list[str]) -> int:
    term = " ".join(args).lower()
    hits = [e for e in layer().events if e["family"] == "protest" and e["available_by"] <= as_of() and term in f"{e['title']} {e['text']}".lower()]
    for e in hits:
        print(f"{e['id']} {e['date']} {e['event_type']}: {e['title']}\n  {' '.join(e['text'].split())[:600]}")
    if not hits and not protests_tool.docket_taken():
        print(f"not collected: GAO's bid protest docket for the {P['protests']['agency']} was never taken "
              f"(protests.py watch --fetch), so the record holds no protest count, not a count of zero")
        return 0
    print(f"{len(hits)} protest(s) in the record as of {as_of()}")
    return 0


def filtered(blocks: list[str], term: str, what: str, cap: int = 30) -> None:
    """The blocks of a long answer that mention a term, at most `cap` of them, and how many were left out."""
    kept = [b for b in blocks if term.lower() in b.lower()]
    print("\n".join(kept[:cap]) or f"no {what} mentions {term!r}")
    if len(kept) > cap:
        print(f"... {len(kept) - cap} more {what}(s); narrow the term")


def printed(fn, argv: list[str]) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(argv)
    return out.getvalue()


def revisions(args: list[str]) -> int:
    if not P["forecast"]["pack_glob"]:
        print(f"{P['short'].removeprefix('U.S. ')} publishes no acquisition forecast this layer reads, so there are no forecast revisions; "
              "the notices and awards carry the requirement (see `trace need`).")
        return 0
    blocks = [b + "\n" for b in printed(monitor_forecast_revision.main, []).split("\n\n") if b.strip()]
    filtered(blocks, " ".join(args), "forecast revision")
    return 0


def check_answer(payload: dict, lay: Layer) -> list[str]:
    """What the record's checks refuse in a draft answer: the chain, the people, the quotes and the letter."""
    answer = {"fit": True, "agency": "", "command": "", "peo": "", "program_office": "", "why_office": "", "requirements": [],
              "initiatives": [], "people": [], "routes": [], "email": {"to": "", "subject": "", "body": ""}, "no_fit": "", **payload}
    for kind in ("requirements", "initiatives"):
        answer[kind] = [{"identifier": "", "quote": "", "source": "", "why": "", **r} for r in answer[kind]]
    answer["people"] = [{"name": "", "why": "", **p} for p in answer["people"]]
    answer["routes"] = [{"route": "", "why": "", **r} for r in answer["routes"]]
    got = record_checks(everything() if lay is layer() else seeing(lay), answer, payload.get("profile", ""))
    return ([f"identifier {i!r} names no record; cite a forecast key, notice, topic or contract number the record holds" for i in got["unmapped"]]
            + got["chain_problems"] + got["letter_problems"])


def check(args: list[str]) -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(f"the answer is not JSON: {e}")
        return 2
    found = check_answer(payload, layer())
    print("\n".join(f"- {p}" for p in found) if found else "all checks pass")
    return 0


def sources(args: list[str]) -> int:
    for ident in args:
        hit = resolve(everything(), ident, set())
        print("\n".join(record_entry(hit, layer(), details())) if hit else f"### {ident}\n\nno record in the platform carries this identifier")
        print()
    return 0


def run(command: str, args: list[str]) -> int:
    if command in VIEWS:
        return view(command, args)
    if command == "page":
        dna = json.loads(DNA.read_text(encoding="utf-8")) if DNA.exists() else {}
        print(page(args[0], " ".join(args[1:]), layer(), layer().roster, dna) if len(args) >= 2 else "usage: page office|vendor|person|cell NAME")
        return 0
    if command == "ask":
        if not args or args[0] not in ASK:
            print(f"usage: ask {'|'.join(ASK)} ...")
            return 2
        return ask.main(["--as-of", as_of(), *args])
    if command == "pulse":
        if not args or args[0] not in ("rank", "week", "actions"):
            print("usage: pulse rank | week | actions (a requirement's own card is in cell)")
            return 2
        if args[0] == "actions":  # one line per action (dated or not), its routes indented deeper beneath it
            filtered(re.split(r"\n(?=\S| {11}#)", printed(pulse.actions_cmd, ["--as-of", as_of()]).strip()), " ".join(args[1:]), "action")
            return 0
        return pulse.COMMANDS[args[0]]([*args[1:], *(["--until", as_of()] if args[0] == "week" else ["--as-of", as_of()])])
    if command == "wiki":
        return office_wiki.main(["page", " ".join(args)])
    if command == "dna":
        return buying_dna.show([" ".join(args)])
    if command == "vendor":
        return vendors.resolve([" ".join(args)])
    if command == "trace":
        return trace.main(args)
    if command in ("inventory", "report"):
        import report  # the report layer imports this module, so it is imported here when asked for
        return report.navy_entry(command, args)
    return {"protests": protests, "revisions": revisions, "check": check, "sources": sources}[command](args)


def help_text() -> str:
    return (f"Commands, each answered from the {P['short'].removeprefix('U.S. ')} record as of {as_of()}:\n"
            + "\n".join(f"  navy.py {name} {what}" for name, what in HELP.items()))


def call(name: str, arguments: dict) -> str:
    """One tool call as the server answers it: everything the command printed, its usage errors included."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            if name == "help":
                print(help_text())
            elif name == "check":
                found = check_answer(arguments.get("answer") or {}, layer())
                print("\n".join(f"- {p}" for p in found) if found else "all checks pass")
            else:
                run(name, shlex.split(arguments.get("args", "")))
        except SystemExit:
            pass
    return out.getvalue().strip() or "(no output)"


def tools() -> list[dict]:
    args = lambda what: {"type": "object", "properties": {"args": {"type": "string", "description": what}}, "required": []}
    answer = {"type": "object", "properties": {"answer": {"type": "object", "description": "the draft answer's fields, with company and profile added"}},
              "required": ["answer"]}
    return ([{"name": "help", "description": "Every tool with what it answers, and the record's date.", "inputSchema": args("nothing")}]
            + [{"name": n, "description": what, "inputSchema": answer if n == "check" else args(what.split(":")[0])} for n, what in HELP.items()])


def serve() -> int:
    """A Model Context Protocol server on stdio: JSON-RPC 2.0, one message per line; every command above is a tool."""
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method, params = msg.get("method"), msg.get("params") or {}
        if "id" not in msg:  # a notification wants no answer
            continue
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                      "serverInfo": {"name": AGENCY_KEY, "version": "1"}}  # "navy" for the Navy (outreach_compare names its tools so)
        elif method == "tools/list":
            result = {"tools": tools()}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": call(params.get("name", ""), params.get("arguments") or {})}], "isError": False}
        elif method == "ping":
            result = {}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"no method {method}"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False), flush=True)
    return 0


def selfcheck() -> int:
    assert set(HELP) == set(VIEWS) | {"page", "ask", "pulse", "protests", "wiki", "dna", "vendor", "trace", "revisions", "check", "sources", "inventory", "report"}
    walk = fixture()  # the checks refuse a parent the record does not hold and a person it did not show for the chain
    good = {"company": "Acme", "profile": "builds terminals", "agency": "Department of the Navy", "command": "NAVWAR", "peo": "PEO C4I",
            "program_office": "PMA/PMW 101", "why_office": "It buys terminals.",
            "requirements": [{"identifier": "N00039-25-RFPREQ-PMA/PMW-101-0046", "quote": "MIDS WDL SF3 Radio"}],
            "people": [{"name": "Ann Example", "why": "She manages it."}]}
    assert not any("PEO C4I" in p and "not above" in p for p in check_answer(good, walk.layer)), check_answer(good, walk.layer)
    assert any("not above" in p for p in check_answer({**good, "peo": "NAVSEA"}, walk.layer))
    assert any("Bob Elsewhere" in p for p in check_answer({**good, "people": [{"name": "Bob Elsewhere"}]}, walk.layer))
    assert any("names no record" in p for p in check_answer({**good, "requirements": [{"identifier": "ZZ-NOT-A-RECORD-1"}]}, walk.layer))
    if CORPUS.exists():  # every command answers on the real record
        samples = {"search": ["Link 16"], "office": ["PMS 406"], "cell": ["N00039-26-RFPREQ-PMA/PMW-101-0148"], "topics": ["autonomy"],
                   "people": ["PMS 406"], "neighbors": ["PMS 406"], "initiatives": ["PMS 406"], "page": ["office", "PMS 406"],
                   "ask": ["team", "undersea", "--top", "3"], "pulse": ["actions", "PMW"], "protests": [], "wiki": ["PMS 406"],
                   "dna": ["N00024"], "vendor": ["Leidos"], "trace": ["need", "N00039-26-RFPREQ-PMA/PMW-101-0148"], "revisions": ["PMW"],
                   "sources": ["N00039-26-RFPREQ-PMA/PMW-101-0148"], "inventory": [], "report": ["plan", "--spec", "agency-brief"]}
        for name, args in samples.items():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    run(name, args)
                except SystemExit:
                    pass
            assert out.getvalue().strip(), f"{name} printed nothing"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sources(["N00039-26-RFPREQ-PMA/PMW-101-0148"])
        assert "Forecast requirement" in out.getvalue() and re.search(r"https?://", out.getvalue()), out.getvalue()[:400]
        near = json.loads(call("office", {"args": "PMA/PMW 101"}))["beside"]  # the other layers come with the office
        assert {"wiki page", "buying book", "next actions", "incumbents ending"} <= set(near) and "bid protests" not in near, sorted(near)
        graph = json.loads(call("neighbors", {"args": "PMW 120"}))["relationships"]  # who buys for the office and who leads above it
        assert any("contracts for PMW 120" in r["relation"] for r in graph) and any(r["relation"].split(" leads ")[1:] and r["sources"] for r in graph), graph
        assert json.loads(call("neighbors", {"args": "NRL Code 7600"})).get("parent") == "NRL", "an office answers to its name before the comma"
    assert [t["name"] for t in tools()] == ["help", *HELP]
    assert next(t for t in tools() if t["name"] == "check")["inputSchema"]["required"] == ["answer"]
    assert "not above" in call("check", {"answer": {**good, "peo": "NAVSEA"}}) or not CORPUS.exists()
    print("navy selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selfcheck"]:
        return selfcheck()
    if argv[:1] == ["serve"]:
        return serve()
    if not argv or argv[0] in ("help", "-h", "--help") or argv[0] not in HELP:
        print(help_text())
        return 0 if argv[:1] in ([], ["help"], ["-h"], ["--help"]) else 2
    return run(argv[0], argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

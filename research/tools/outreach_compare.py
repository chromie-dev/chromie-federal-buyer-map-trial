#!/usr/bin/env python3
"""Our outreach walk against an agent out of the box: the same profiles, the same question, and each answer checked by
the same record checks and by a second model that sees no labels.

The arms:
- ours: the saved outreach run (research/results/outreach/<slug>.json);
- web: Claude Code headless in an empty folder with web search and fetch only, given a targeted prompt;
- repo: Claude Code headless in a copy of this repository's data, with the walk, its cassettes and its runs removed and
  no model key reachable, and no web.
Every session loads no user or project settings and no MCP servers, so no user instructions, hooks or memories reach
it (a bare session would also drop the web tools), and the whole transcript is kept.

Every answer is put in one shape: the chain; requirements and initiatives, each with identifier, quote and source;
people with source; the letter. Then two checks:
- record checks: outreach's own chain and letter checks on a walk that has seen every office, record, person and route,
  so only what the record contradicts or lacks fails. A public identifier maps to a forecast key or to the record whose
  text carries it; one that maps to nothing is not counted false, the verifier decides it;
- a blind verifier: one clean headless session per answer, labelled only by number, that opens each source and gives a
  verdict per record, chain edge, person and fact in the letter.

    python research/tools/outreach_compare.py prompt SLUG
    python research/tools/outreach_compare.py baseline --arm web|repo SLUG [SLUG ...]
    python research/tools/outreach_compare.py verify SLUG [SLUG ...]
    python research/tools/outreach_compare.py table
    python research/tools/outreach_compare.py --selfcheck
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outreach import (B, CORPUS, OUT, S, SEED, Layer, Walk, arr, audit, chain_problems, email_problems, evidence, row_details,  # noqa: E402
                      fixture, load_people, obj, verbatim)
from people import contacts_for, norm_name  # noqa: E402
from agency import KEY as AGENCY_KEY, P  # noqa: E402
from agency_layers_sql import uid  # noqa: E402
from trace import resolve_offices  # noqa: E402

COMPARE = OUT / "compare"
LABEL, SHORT = P["label"], P["short"].removeprefix("U.S. ")  # "Navy", "DARPA": the word the prompts use for the buyer
BASELINE_REPO = Path(os.environ.get("OUTREACH_BASELINE_REPO", "/tmp/fbm-baseline"))
ARMS = ("ours", "web", "repo", "platform", "platform-web")
SERVER = AGENCY_KEY  # the read surface's server name: "navy" for the Navy, the profile key for another agency
NAVY = {"mcpServers": {SERVER: {"command": sys.executable, "args": [str(Path(__file__).resolve().parent / "navy.py"), "serve"]}}}  # the platform's read surface
MODEL = "opus"
BUDGET = {"baseline": "8", "verify": "5"}
TOOLS = {"web": "WebSearch,WebFetch", "repo": "Read,Grep,Glob,Bash", "verify": "Read,Grep,Glob,Bash,WebSearch,WebFetch",
         "platform": "", "platform-web": "WebSearch,WebFetch"}  # a platform arm has no shell and no file tools: its record comes only through NAVY
ALLOWED = {"platform": f"mcp__{SERVER}", "platform-web": f"mcp__{SERVER},WebSearch,WebFetch"}
VERDICT = {"type": "string", "enum": ["supported", "unsupported", "contradicted", "unverifiable"]}
SOURCED = arr(obj(identifier=S, title=S, quote=S, source=S, why=S))
ANSWER = obj(fit=B, agency=S, command=S, peo=S, program_office=S, why_office=S, requirements=SOURCED, initiatives=SOURCED,
             people=arr(obj(name=S, title=S, email=S, source=S, why=S)), routes=arr(obj(route=S, source=S, why=S)),
             email=obj(to=S, subject=S, body=S), no_fit=S)
CHECKED = obj(records=arr(obj(identifier=S, exists=VERDICT, quote=VERDICT, office=VERDICT, reason=S)),
              edges=arr(obj(office=S, parent=S, verdict=VERDICT, reason=S)),
              people=arr(obj(name=S, verdict=VERDICT, reason=S)),
              letter=arr(obj(fact=S, verdict=VERDICT, reason=S)),
              beyond_profile=arr(S),
              fit=obj(verdict={"type": "string", "enum": ["buys it", "plausible", "does not buy it", "no fit, rightly", "no fit, wrongly"]}, reason=S))

TASK = ("go from a company's capability to the agency, the command, the program executive office and the program office "
        "that buys it, that office's requirements and initiatives, the relevant people, and one extremely tailored "
        "outreach e-mail")
PROMPT = f"""You help a small company reach the right buyer in the {LABEL}. From the profile below, {{task}}.

{{where}}

Rules:
- Use only what was public on or before {{as_of}}.
- Every requirement and initiative needs its identifier (a forecast, solicitation, notice, topic or contract number), a
  quote copied exactly from its source, and the source: a URL, or a file path with the sheet row or line.
- Every person needs their title, their e-mail if public, and the source that ties them to that office.
- Name each office exactly as its source writes it; leave a level empty only when the office sits under none.
- The letter uses only facts from your sources and the profile, and claims nothing about the company beyond the profile.
- Never forecast: never write likely, probably, imminent, expected, soon, "will release" or "RFP coming".
- When no {SHORT} office buys what the company sells, answer fit false and give the reason in one sentence.

Company: {{company}}
Profile: {{profile}}"""
PLATFORM = (f"Work through the platform's {SERVER} tools, which answer from a record of {LABEL} procurement statements: forecasts "
            "with their full rows, notices, awards, SBIR topics, Congress, the budget, oversight, leaders, news, bid protests, "
            "the organization tree, people and small business offices. Start with the help tool. Give each record the source "
            "the sources tool prints for it. Before you answer, pass your draft to the check tool (the answer's fields with "
            "company and profile added) and fix what it refuses.")
WHERE = {"web": "Use web search and fetch to find and read the sources.",
         "repo": f"Work from the data in this folder, a record of {LABEL} procurement statements with its tools; start by "
                 "reading CLAUDE.md and README.md. There is no web access.",
         "platform": PLATFORM + " They are your only tools; there is no web access.",
         "platform-web": PLATFORM + " You may also use web search and fetch; cite a web page by its URL."}
VERIFY = """You check one answer to this task, written by a system you do not know: {task}.

Check it against its sources. Open each source: a URL with WebFetch, a file path with Read or a short read-only python
script (this folder holds a record of U.S. Navy procurement statements; a record's text names its saved original under
data/raw by sha256, and research/memory/organization_seed.json holds the organization tree with its sources). Do not
change any file.

Give, as the schema asks:
- records: for each requirement and initiative, whether it exists at its source, whether the quote is at that source
  verbatim, and whether it belongs to the office the answer puts it under;
- edges: each office in the chain with the one named above it, correct or not by a source you can cite;
- people: whether each person is tied to that office at the cited source;
- letter: every factual statement in the letter (numbers, dates, identifiers, titles, offices, programs, claims about
  the Navy), each with a verdict;
- beyond_profile: every claim the letter makes about the company that the profile does not carry;
- fit: whether the program office buys what the company sells, judged from the sources.
A verdict is supported, unsupported (the source does not say it), contradicted (a source says otherwise) or
unverifiable (the source cannot be opened). Give the reason in one sentence each.

{packet}""".replace("U.S. Navy", LABEL)


# ------------------------------------------------------------------ one shape for every arm

def ours(run: dict, layer: Layer, seed: dict) -> dict:
    """Our saved run in the answer shape, with the audit's appendices as its sources."""
    a = run["chain"]
    page = audit(run, layer, seed)
    contacts = {c["name"]: c for c in run["contacts"]}
    titles = {**{e["id"]: e["title"] for e in layer.events}, **{n["key"]: n["title"] for n in layer.needs}}
    answer = {k: a[k] for k in ("fit", "agency", "command", "peo", "program_office", "why_office", "no_fit")}
    for kind in ("requirements", "initiatives"):
        answer[kind] = [{"identifier": r["identifier"], "title": titles.get(r["identifier"], ""), "quote": r["quote"], "source": "Appendix A", "why": r["why"]}
                        for r in a[kind]]
    answer["people"] = [{"name": p["name"], "title": contacts.get(p["name"], {}).get("title", ""), "email": contacts.get(p["name"], {}).get("email", ""),
                         "source": contacts.get(p["name"], {}).get("source_url", "") or "Appendix C", "why": p["why"]} for p in a["people"]]
    answer["routes"] = [{"route": r["route"], "source": "Appendix C", "why": r["why"]} for r in a["routes"]]
    e = run["email"] or {"to": "", "subject": "", "body": ""}
    answer["email"] = {k: e[k] for k in ("to", "subject", "body")}
    return {"answer": answer, "sources": page[page.find("## Appendix A"):] if "## Appendix A" in page else "",
            "usage": {"input_tokens": run["usage"]["input_tokens"], "output_tokens": run["usage"]["output_tokens"], "turns": len(run["calls"]),
                      "seconds": None, "cost_usd": None, "model": run["model"], "lookups": len(run["steps"])}}


# ------------------------------------------------------------------ record checks

def seeing(layer: Layer) -> Walk:
    """A walk that has opened every office and been shown every record, person and route: the checks then refuse only
    what the record contradicts or lacks."""
    walk = Walk(layer, row_details(layer.as_of))
    walk.opened = set(layer.orgs)
    walk.shown = {**{e["id"]: e for e in layer.events}, **{n["key"]: n for n in layer.needs}}
    walk.people = {p["name"]: {"name": p["name"]} for p in layer.roster}
    walk.routes = {r["recommendation"]: r for r in layer.routes}
    return walk


def office(layer: Layer, name: str) -> str:
    """The record's name for an office an answer writes its own way: the whole name or the name before its brackets as
    they stand, else the most specific office the organization memory's aliases or the record's acronyms find in it as
    words ("Code 7600" over "Naval Research Laboratory" in one name)."""
    for form in (name, re.sub(r"\s*\([^)]*\)", "", name)):
        if form.strip() and layer.org_id(form):
            return form.strip()
    found = {}
    nodes = {n["id"]: n for n in json.loads(SEED.read_text(encoding="utf-8"))["nodes"]}
    for o in resolve_offices(name):
        form = layer.orgs.get(uid("org", o["office"]), {}).get("acronym") or (nodes[o["office"]].get("codes") or {}).get("office_code", "")
        if not o["former"] and form and layer.org_id(form):
            found[layer.org_id(form)] = form
    flat = re.sub(r"\s+", " ", re.sub(r"[()]", " ", name.lower())).replace("pmw-", "pmw ")  # "(PMW) 150" reads as "PMW 150"
    for k, oid in layer.by_acronym.items():
        if len(k) >= 3 and re.search(rf"(?<![\w/-]){re.escape(k)}(?![\w/-])", flat):
            found.setdefault(oid, layer.orgs[oid].get("acronym") or k)

    def depth(oid: str) -> int:
        n = 0
        while oid in layer.orgs and n < 20:
            oid, n = layer.orgs[oid].get("parent"), n + 1
        return n
    return found[max(found, key=lambda oid: (depth(oid), len(found[oid])))] if found else name


def resolve(walk: Walk, identifier: str, under: set[str]) -> str | None:
    """The record an answer's identifier names: a forecast key or corpus id as it stands, else the record whose title or
    text carries the number or the SAM.gov notice id, one under the program office first."""
    ident = identifier.strip().strip("[]")
    if ident in walk.shown:
        return ident
    sam = re.search(r"sam\.gov/(?:opp|api/prod/opps/v2/opportunities)/([0-9a-f]{16,})", ident)
    token = sam.group(1) if sam else ident
    if len(token) < 5:
        return None
    rx = re.compile(rf"(?<![\w-]){re.escape(token)}(?![\w-])", re.I)
    keys = [n["key"] for n in walk.layer.needs if rx.search(n["key"])]
    if keys:
        return keys[0]
    hits = [e["id"] for e in walk.layer.events if rx.search(e["title"]) or rx.search(e["text"])]
    hits.sort(key=lambda i: not (walk.home(i) & under))
    return hits[0] if hits else None


def record_checks(walk: Walk, answer: dict, profile: str) -> dict:
    """The answer against the record: what did not map, and what outreach's own checks refuse."""
    lay = walk.layer
    a = {**answer, **{k: office(lay, answer[k]) for k in ("agency", "command", "peo", "program_office")}}
    po = lay.org_id(a["program_office"]) if a["fit"] else None
    under = lay.subtree(po) if po else set()
    unmapped, mapped = [], {}
    for kind in ("requirements", "initiatives"):
        mapped[kind] = []
        for r in answer[kind]:
            hit = resolve(walk, r["identifier"], under) or next((resolve(walk, x, under) for x in re.findall(r"[A-Z0-9][A-Z0-9-]{4,}", r["source"])), None)
            if hit:
                mapped[kind].append({"identifier": hit, "quote": r["quote"], "why": r["why"]})
            else:
                unmapped.append(r["identifier"])
    names = {norm_name(p["name"]): p["name"] for p in lay.roster}
    people = [{"name": names.get(norm_name(p["name"]), p["name"]), "why": p["why"]} for p in answer["people"]]
    routes = [{"route": r["route"], "why": r["why"]} for r in answer["routes"]]
    picked = {**a, **mapped, "people": people, "routes": routes}
    chain = chain_problems(walk, picked) if answer["fit"] else []
    letter = []
    if po and answer["email"]["body"].strip():
        near = {c["name"]: c for c in contacts_for(walk.ancestors(po), lay.roster, lay.as_of, limit=100)}
        walk.people.update(near)  # the contact as this chain's office holds it, for the evidence
        fair = {**picked, "people": [p for p in people if p["name"] in near]}
        proof = evidence(walk, fair)
        cites = [r["identifier"] for k in ("requirements", "initiatives") for r in mapped[k]]
        to = answer["email"]["to"].strip()  # an answer may address the letter by e-mail: the person it names that e-mail for
        to = next((p["name"] for p, q in zip(people, answer["people"]) if to and to.lower() in (q.get("email") or "").lower()), to)
        letter = email_problems(walk, fair, {**answer["email"], "to": to, "cites": cites}, proof, f"{answer.get('company', '')} {profile}")
    return {"unmapped": unmapped, "chain_problems": chain, "letter_problems": letter}


# ------------------------------------------------------------------ the sessions

def claude(prompt: str, cwd: Path, tools: str, schema: dict, budget: str, allowed: str = "", mcp: dict | None = None, stream: bool = False) -> dict:
    """One clean headless Claude Code session; its structured answer, how much it cost, and the whole transcript. It has
    the built-in `tools` and the servers in `mcp` and no other, and uses unasked only what `allowed` names (default: `tools`).
    With `stream`, the transcript is every message of the session (the CLI's stream-json, one object per line), so each
    tool call and what it returned are in it; without it, the CLI's json format carries the result alone."""
    fmt = ["--output-format", "stream-json", "--verbose"] if stream else ["--output-format", "json"]
    cmd = ["claude", "-p", prompt, "--model", MODEL, *fmt, "--json-schema", json.dumps(schema),
           "--tools", tools, "--allowedTools", allowed or tools, "--max-budget-usd", budget, "--no-session-persistence",
           "--setting-sources", "", "--strict-mcp-config", *(["--mcp-config", json.dumps(mcp)] if mcp else [])]
    start = time.time()
    proc = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5400)
    seconds = round(time.time() - start)
    try:
        transcript = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()] if stream else json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stderr or proc.stdout)[-2000:], "seconds": seconds}
    messages = transcript if isinstance(transcript, list) else [transcript]
    last = next((m for m in reversed(messages) if m.get("type") == "result"), {})
    use = last.get("usage") or {}
    return {"structured": last.get("structured_output"), "result": last.get("result", ""), "subtype": last.get("subtype"),
            "usage": {"input_tokens": sum(use.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
                      "output_tokens": use.get("output_tokens") or 0, "turns": last.get("num_turns"), "seconds": seconds,
                      "cost_usd": last.get("total_cost_usd"), "model": next((m.get("model") for m in messages if m.get("subtype") == "init"), MODEL)},
            "transcript": messages}


def tool_uses(messages: list[dict]) -> list[str]:
    """Every tool the session called, in order; the answer itself is not a lookup."""
    return [c.get("name", "") for m in messages if m.get("type") == "assistant" for c in m.get("message", {}).get("content", [])
            if c.get("type") in ("tool_use", "server_tool_use") and c.get("name") != "StructuredOutput"]


def features(messages: list[dict]) -> Counter:
    """The platform tools a session called, by name: which of the platform's features it reached for."""
    return Counter(n.removeprefix(f"mcp__{SERVER}__") for n in tool_uses(messages) if n.startswith(f"mcp__{SERVER}__"))


def saved(slug_: str) -> dict:
    return json.loads((OUT / f"{slug_}.json").read_text(encoding="utf-8"))


def prompt_for(slug_: str, arm: str) -> str:
    run = saved(slug_)
    return PROMPT.format(task=TASK, where=WHERE[arm], as_of=run["as_of"], company=run["company"], profile=run["profile"])


def baseline(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="outreach_compare.py baseline")
    ap.add_argument("--arm", choices=("web", "repo", "platform", "platform-web"), required=True)
    ap.add_argument("slugs", nargs="+")
    args = ap.parse_args(argv)
    COMPARE.mkdir(parents=True, exist_ok=True)
    for slug_ in args.slugs:
        if args.arm == "repo" and not (BASELINE_REPO / "research" / "results" / "corpus.json").exists():
            print(f"no data copy at {BASELINE_REPO}", file=sys.stderr)
            return 1
        cwd = BASELINE_REPO if args.arm == "repo" else COMPARE / f"empty-{slug_}"
        cwd.mkdir(parents=True, exist_ok=True)
        got = claude(prompt_for(slug_, args.arm), cwd, TOOLS[args.arm], ANSWER, BUDGET["baseline"], ALLOWED.get(args.arm, ""),
                     NAVY if args.arm in ALLOWED else None)
        out = COMPARE / f"{slug_}.{args.arm}.json"
        out.write_text(json.dumps({"slug": slug_, "arm": args.arm, "prompt": prompt_for(slug_, args.arm), **got}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{slug_} {args.arm}: {'answer' if got.get('structured') else 'no answer'}; {got.get('usage', {}).get('cost_usd')} USD; {out.name}")
    return 0


def answers(slug_: str, layer: Layer, seed: dict) -> dict[str, dict]:
    """Every arm's answer for one profile, in one shape, with its sources and cost."""
    got = {"ours": ours(saved(slug_), layer, seed)}
    for arm in ARMS[1:]:
        path = COMPARE / f"{slug_}.{arm}.json"
        if path.exists():
            run = json.loads(path.read_text(encoding="utf-8"))
            if run.get("structured"):
                got[arm] = {"answer": run["structured"], "sources": "Each record and person above names its own source.",
                            "usage": {**run["usage"], "lookups": len(tool_uses(run["transcript"]))}, "features": features(run["transcript"])}
    return got


def packet(n: int, company: str, profile: str, got: dict) -> str:
    a = got["answer"]
    return (f"# Answer {n}\n\nCompany: {company}\nProfile: {profile}\n\n## The answer\n\n```json\n{json.dumps(a, indent=1, ensure_ascii=False)}\n```\n\n"
            f"## Its sources\n\n{got['sources']}\n")


def verify(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="outreach_compare.py verify")
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--arm", action="append", help="verify only these arms (default: every arm with an answer)")
    args = ap.parse_args(argv)
    layer, seed = load()
    for slug_ in args.slugs:
        run = saved(slug_)
        got = answers(slug_, layer, seed)
        order = sorted(got, key=lambda arm: random.Random(f"{slug_}:{arm}").random())  # a fixed shuffle: the label says nothing
        for n, arm in enumerate(order, 1):
            if args.arm and arm not in args.arm:
                continue
            text = VERIFY.format(task=TASK, packet=packet(n, run["company"], run["profile"], got[arm]))
            checked = claude(text, BASELINE_REPO, TOOLS["verify"], CHECKED, BUDGET["verify"])
            (COMPARE / f"{slug_}.{arm}.verdict.json").write_text(json.dumps({"slug": slug_, "arm": arm, "label": n, "packet": text, **checked},
                                                                             indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"{slug_} answer {n}: {'verdict' if checked.get('structured') else 'no verdict'}; {checked.get('usage', {}).get('cost_usd')} USD")
    return 0


# ------------------------------------------------------------------ the table

def tally(verdict: dict | None) -> dict:
    if not verdict:
        return {}
    ok = lambda rows, *keys: f"{sum(all(r[k] == 'supported' for k in keys) for r in rows)}/{len(rows)}"
    return {"records": ok(verdict["records"], "exists", "quote", "office"), "edges": ok(verdict["edges"], "verdict"),
            "people": ok(verdict["people"], "verdict"), "letter": ok(verdict["letter"], "verdict"),
            "contradicted": sum(r.get(k) == "contradicted" for part in ("records", "edges", "people", "letter") for r in verdict[part]
                                for k in ("exists", "quote", "office", "verdict")),
            "beyond_profile": len(verdict["beyond_profile"]), "fit": verdict["fit"]["verdict"]}


def row(slug_: str, arm: str, got: dict, checks: dict, verdict: dict | None) -> dict:
    a, use = got["answer"], got["usage"]
    t = tally(verdict)
    return {"profile": slug_, "arm": arm, "office": a["program_office"] if a["fit"] else "no fit",
            "requirements": len(a["requirements"]), "initiatives": len(a["initiatives"]), "people": len(a["people"]),
            "unmapped": len(checks["unmapped"]), "chain refused": len(checks["chain_problems"]), "letter refused": len(checks["letter_problems"]),
            **{f"verified {k}": t.get(k, "") for k in ("records", "edges", "people", "letter", "contradicted", "beyond_profile", "fit")},
            "tokens in/out": f"{use['input_tokens']}/{use['output_tokens']}", "turns": use["turns"], "lookups": use.get("lookups", ""),
            "minutes": round(use["seconds"] / 60, 1) if use.get("seconds") else "", "USD": use.get("cost_usd") or "",
            "platform commands": ", ".join(f"{k} {n}" for k, n in got.get("features", Counter()).most_common())}


def table(argv: list[str]) -> int:
    layer, seed = load()
    walk = seeing(layer)
    rows, notes = [], []
    for path in sorted(OUT.glob("[!.]*.json")):
        slug_ = path.stem
        run = saved(slug_)
        for arm, got in answers(slug_, layer, seed).items():
            checks = record_checks(walk, {**got["answer"], "company": run["company"]}, run["profile"])
            vpath = COMPARE / f"{slug_}.{arm}.verdict.json"
            verdict = json.loads(vpath.read_text(encoding="utf-8")).get("structured") if vpath.exists() else None
            rows.append(row(slug_, arm, got, checks, verdict))
            notes.append(f"- {slug_} / {arm}: unmapped {checks['unmapped'] or 'none'}; chain {checks['chain_problems'] or 'holds'}; "
                         f"letter {checks['letter_problems'] or 'holds'}")
    if not rows:
        print("no saved outreach runs")
        return 1
    head = list(rows[0])
    text = "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(r[h]) for h in head) + " |" for r in rows])
    text += "\n\nRecord checks, in full:\n" + "\n".join(notes) + "\n"
    COMPARE.mkdir(parents=True, exist_ok=True)
    (COMPARE / "table.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def load() -> tuple[Layer, dict]:
    return (Layer(json.loads(CORPUS.read_text(encoding="utf-8")), load_people()),
            json.loads(SEED.read_text(encoding="utf-8")) if SEED.exists() else {})


def prompt(argv: list[str]) -> int:
    print(prompt_for(argv[0], argv[1] if len(argv) > 1 else "web"))
    return 0


def selfcheck() -> int:
    walk = fixture()
    lay = walk.layer
    all_seen = seeing(lay)
    assert office(lay, "Multifunctional Information Distribution System (PMA/PMW 101)") == "PMA/PMW 101" and office(lay, "NAVWAR") == "NAVWAR"
    assert office(lay, "PMA/PMW 101 Multifunctional Information Distribution System (MIDS) Program Office (MPO)") == "PMA/PMW 101"
    assert office(lay, "Tactical Data Link (TDL) Program Office (PMW-530) (formerly MIDS)") == "PMA/PMW 101"  # the memory's alias for the new name
    assert office(lay, "Program Manager, Warfare (PMW) 205, Enterprise Networks") == "PMW 205" and office(lay, "Made Up Office") == "Made Up Office"
    assert office(lay, "NAVWAR Enterprise, PMW 205 office") == "PMW 205" and office(lay, "PEO C4I (NAVWAR)") == "PEO C4I"
    assert resolve(all_seen, "N00039-25-RFPREQ-PMA/PMW-101-0046", set()) == "N00039-25-RFPREQ-PMA/PMW-101-0046"
    assert resolve(all_seen, "N252-D11", set()) == "t1" and resolve(all_seen, "https://sam.gov/opp/0123456789abcdef0123/view", set()) is None
    good = {"fit": True, "agency": "Department of the Navy", "command": "NAVWAR", "peo": "PEO C4I", "program_office": "MIDS (PMA/PMW 101)",
            "why_office": "It owns MIDS.", "no_fit": "",
            "requirements": [{"identifier": "N00039-25-RFPREQ-PMA/PMW-101-0046", "title": "", "quote": "MIDS WDL SF3 Radio", "source": "x", "why": "a terminal"}],
            "initiatives": [{"identifier": "N252-D11", "title": "", "quote": "Advanced Interference Mitigation", "source": "x", "why": "a topic"}],
            "people": [{"name": "Ann Example", "title": "", "email": "", "source": "", "why": "She manages it."}], "routes": [],
            "email": {"to": "Ann Example", "subject": "MIDS WDL SF3 Radio", "body": "Ann Example, about the MIDS WDL SF3 Radio row."}}
    held = record_checks(all_seen, good, "Acme builds Link 16 terminals")
    assert held == {"unmapped": [], "chain_problems": [], "letter_problems": []}, held
    by_mail = {**good, "people": [{**good["people"][0], "email": "ann.example@us.navy.mil"}], "email": {**good["email"], "to": "ann.example@us.navy.mil"}}
    assert record_checks(all_seen, by_mail, "Acme builds Link 16 terminals")["letter_problems"] == []
    wrong = record_checks(all_seen, {**good, "peo": "PEO Digital", "people": [{**good["people"][0], "name": "Made Up"}],
                                     "requirements": [{**good["requirements"][0], "identifier": "N00039-99-X-0001"}]}, "")
    assert wrong["unmapped"] == ["N00039-99-X-0001"] and any("PEO Digital" in p for p in wrong["chain_problems"]) \
        and any("Made Up" in p for p in wrong["chain_problems"]), wrong
    verdict = {"records": [{"identifier": "a", "exists": "supported", "quote": "supported", "office": "supported", "reason": ""},
                           {"identifier": "b", "exists": "supported", "quote": "unsupported", "office": "supported", "reason": ""}],
               "edges": [{"office": "x", "parent": "y", "verdict": "contradicted", "reason": ""}], "people": [], "letter": [],
               "beyond_profile": ["x"], "fit": {"verdict": "buys it", "reason": ""}}
    assert tally(verdict) == {"records": "1/2", "edges": "0/1", "people": "0/0", "letter": "0/0", "contradicted": 1, "beyond_profile": 1, "fit": "buys it"}
    got = {"answer": good, "usage": {"input_tokens": 1, "output_tokens": 2, "turns": 3, "seconds": 120, "cost_usd": 0.5}}
    r = row("acme", "web", got, held, verdict)
    assert r["office"] == "MIDS (PMA/PMW 101)" and r["verified records"] == "1/2" and r["minutes"] == 2.0 and r["tokens in/out"] == "1/2"
    assert tool_uses([{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch"}, {"type": "tool_use", "name": "StructuredOutput"}]}}]) == ["WebSearch"]
    used = lambda name: {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": {}}]}}
    ran = [used(f"mcp__{SERVER}__search"), used(f"mcp__{SERVER}__search"), used(f"mcp__{SERVER}__check"), used("WebSearch")]
    assert features(ran) == Counter({"search": 2, "check": 1}), features(ran)
    assert "Company: Acme\nProfile: builds terminals" in packet(1, "Acme", "builds terminals", {"answer": good, "sources": ""}) \
        and "Answer 1" in packet(1, "Acme", "", {"answer": good, "sources": ""})
    assert verbatim("MIDS WDL", "the MIDS  WDL SF3 Radio")
    print("outreach_compare selfcheck ok")
    return 0


COMMANDS = {"prompt": prompt, "baseline": baseline, "verify": verify, "table": table}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--selfcheck":
        sys.exit(selfcheck())
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[args[0]](args[1:]))

#!/usr/bin/env python3
"""Claude on the platform's tools against Claude with the web, on one request that needs everything the platform
answers: the outreach walk (company capability to program office, requirements, people and one letter) and around it a
dossier with one section for each thing the platform answers. A verifier that sees neither label then checks both
answers side by side, one section at a time.

The arms are outreach_compare's: platform (the navy MCP server only, no shell, no files, no web) and web (web search and
fetch only). The request names no tool; each section lists the parts it must cover.

    python research/tools/dossier_compare.py ask --arm platform|web [SLUG]
    python research/tools/dossier_compare.py verify [--jobs N] [SLUG]
    python research/tools/dossier_compare.py check [--jobs N] [SLUG]   # the platform's answer alone, part by part
    python research/tools/dossier_compare.py trail [SLUG]      # writes research/results/outreach/dossier/SLUG.platform.trail.txt
    python research/tools/dossier_compare.py report [SLUG]     # writes research/results/outreach/dossier/SLUG.report.txt
    python research/tools/dossier_compare.py --selfcheck
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import textwrap
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import P  # noqa: E402
from navy import HELP  # noqa: E402
from outreach import OUT, S, arr, obj  # noqa: E402
from outreach_compare import ALLOWED, ANSWER, BASELINE_REPO, NAVY, PLATFORM, TOOLS, VERDICT, claude, features, saved, tool_uses  # noqa: E402

DOSSIER = OUT / "dossier"
LABEL, SHORT = P["label"], P["short"].removeprefix("U.S. ")  # "Navy", "DARPA": the word the section titles use for the buyer
BUDGET = {"ask": "25", "verify": "8"}
ARMS = ("platform", "web")
# section -> (the platform feature it calls on, what it asks, the parts it must cover)
SECTIONS = {
    "buyers": ("ask match", f"the {SHORT} offices that buy what the company could sell",
               ["the five best-fitting offices, ranked", "for each, the records that show it buys this", ("for each, its chain up to the systems command" if SHORT == "Navy" else "for each, its chain up to the agency")]),
    "org_graph": ("neighbors", "the chosen program office's place in the organization",
                  [f"every level above it up to the {LABEL}", "its sibling offices", "the contracting office that buys for it",
                   "who leads each level, current or ended", "reorganizations affecting the chain"]),
    "people": ("people", "the people tied to the chosen office and the offices above it",
               ["name", "title", "office", "e-mail if public", "the source that ties them to it"]),
    "wiki": ("wiki", "what the chosen office buys, in its own words", ["what it buys or researches, quoted", "the notices or forecast rows that say it"]),
    "changed": ("ask changed", f"what changed in the 90 days before {{as_of}} in {SHORT} buying that touches the company's line",
                ["new forecast rows, notices or topics", "rows dropped or moved", "awards made"]),
    "incumbents": ("ask incumbents", f"{SHORT} contracts in the company's line that end within 18 months of {{as_of}}",
                   ["contract number", "holder", "office", "end date", "value"]),
    "buying_book": ("dna", "how the contracting office that buys for the chosen office buys",
                    ["the contracting office and its code", "award count and value", "vehicles and contract types",
                     "competition and small business share", "top vendors"]),
    "vendors": ("vendor", "the vendors that hold the chosen office's or its contracting office's contracts in the company's line",
                ["vendor", "unique entity identifier", "other spellings", "contracts and values"]),
    "teaming": ("ask team", "the companies to approach as a prime or teaming partner for this work",
                ["company", f"the {SHORT} contract that shows it does this work", "office", "why it fits"]),
    "competitor_moves": ("ask moves", "what the companies that win this work did in the 12 months before {as_of}",
                         ["company", "the move (award, notice, protest, new office)", "date", "record"]),
    "analogs": ("ask analogs", f"past {SHORT} requirements most like the work the company would bid on, and how each ended",
                ["requirement", "office", "outcome (award, holder, value)", "time from forecast to award"]),
    "trace": ("trace", "the requirement you rank first, traced across forecast releases, notices and awards",
              ["the release it first appeared in", "what each later release changed", "any notice or award tied to it", "where it stands on {as_of}"]),
    "revisions": ("revisions", "forecast rows in the company's line whose award window or value moved between releases",
                  ["row", "what moved, from and to", "the releases"]),
    "protests": ("protests", "GAO bid protests against the Department of the Navy that bear on this work",
                 ["protest number", "protester", "Navy office", "outcome and date", "what it teaches a first-time bidder"]),
    "pulse": ("pulse", "the open requirements fitting the company that are most time-critical on {as_of}, and what to do on each now",
              ["requirement", "the dates that make it time-critical", "the action", "the person or route to act through"]),
    "initiatives": ("initiatives", "what leaders, Congress, the budget, oversight reports, conferences and news say about priorities in the chosen office's chain that bear on the company's line",
                    ["the statement", "who said it", "date", "what it means for the company"]),
    "sbir_topics": ("topics", "Navy SBIR and STTR topics that fit the company's proposed research",
                    ["topic number", "title", "office", "open and close dates", "why it fits"]),
    "small_business": ("office routes", "the small business offices and routes to reach the chosen office and its contracting office",
                       ["office", "contact", "e-mail or page", "what the route covers"]),
    "meeting_brief": ("ask prep", "a brief for the company's first meeting with the chosen office",
                      ["who to meet", "what the office buys", "its open requirements", "its recent statements", "questions to ask"]),
}
CLAIMS = arr(obj(claim=S, identifier=S, quote=S, source=S))
SECTION = obj(summary=S, claims=CLAIMS, not_found=S)
DOSSIER_ANSWER = {**ANSWER, "properties": {**ANSWER["properties"], "dossier": obj(**{k: SECTION for k in SECTIONS})},
                  "required": [*ANSWER["required"], "dossier"]}
PART = {"type": "string", "enum": ["answered", "partly", "missing"]}
SIDE = obj(claims=arr(obj(claim=S, verdict=VERDICT, reason=S)), parts=arr(obj(part=S, verdict=PART)), missed=S)
JUDGED = obj(A=SIDE, B=SIDE, better={"type": "string", "enum": ["A", "B", "tie"]}, why=S)
OUTREACH_PARTS = ["agency, command, program executive office and program office, each tied to the one above by a source",
                  "the office's requirements and initiatives, each with identifier, quote and source", "the people, each tied to the office",
                  "one tailored letter that uses only sourced facts and the profile"]

PROMPT = """You help a small company reach the right buyer in the U.S. Navy. From the profile below, go from the company's
capability to the agency, the command, the program executive office and the program office that buys it, that office's
requirements and initiatives, the relevant people, and one extremely tailored outreach e-mail. Around that chain, write
the dossier: one section for each item below, each covering the parts listed. The chosen office is the program office
of your chain.

{sections}

{where}

Rules:
- Use only what was public on or before {as_of}; answer as of that day.
- Every requirement and initiative needs its identifier (a forecast, solicitation, notice, topic or contract number), a
  quote copied exactly from its source, and the source: a URL, or a file path with the sheet row or line.
- Every person needs their title, their e-mail if public, and the source that ties them to that office.
- Name each office exactly as its source writes it; leave a level empty only when the office sits under none.
- Every dossier claim is one fact with the identifier it rests on (a record number, an office code or a person's name),
  a quote copied exactly from its source, and the source. A claim states what a source says: never claim that
  something does not exist, or that a record or the web holds none of it; what you could not find goes in the
  section's not_found.
- The letter uses only facts from your sources and the profile, and claims nothing about the company beyond the profile.
- Never forecast: never write likely, probably, imminent, expected, soon, "will release" or "RFP coming".
- When no Navy office buys what the company sells, answer fit false and give the reason in one sentence.

Company: {company}
Profile: {profile}"""
WHERE = {"platform": PLATFORM + " They are your only tools; there is no web access.",
         "web": "Use web search and fetch to find and read the sources."}
VERIFY = """Two systems you do not know answered the same request for a small company that wants to sell to the U.S. Navy,
as of {as_of}. You check one part of both answers against their sources and say which serves the company better.

Open each source: a URL with WebFetch, a file path with Read or a short read-only python script (this folder holds a
record of U.S. Navy procurement statements; a record's text names its saved original under data/raw by sha256, and
research/memory/organization_seed.json holds the organization tree and graph with their sources). Do not change any file.

For each answer give:
- claims: every factual claim in the part (records, offices, people, numbers, dates, and in a letter every fact about
  the Navy), with a verdict: supported (the source says it), unsupported (the source does not say it), contradicted (a
  source says otherwise) or unverifiable (the source cannot be opened); one sentence of reason each;
- parts: each part listed below, answered, partly or missing;
- missed: what a well-informed answer as of {as_of} gives that this one lacks, in one or two sentences.
Then better: A, B or tie, and why in two sentences, weighing correctness first, then how much is answered, then how
useful it is to the company.

The part: {what}
Its parts: {parts}
Company: {company}
Profile: {profile}

# Answer A

```json
{a}
```

# Answer B

```json
{b}
```"""


SOLO = obj(A=SIDE)
ALONE = (VERIFY.replace("Two systems you do not know answered the same request", "A system you do not know answered a request")
         .replace("You check one part of both answers against their sources and say which serves the company better.",
                  "You check one part of the answer against its sources.")
         .replace("For each answer give:", "Give:")
         .split("Then better:")[0] + """
The part: {what}
Its parts: {parts}
Company: {company}
Profile: {profile}

# Answer A

```json
{a}
```""")


def parts_of(sid: str, as_of: str) -> tuple[str, list[str]]:
    if sid == "outreach":
        return "the chain from the company to its program office, the office's requirements and initiatives, its people and the letter", OUTREACH_PARTS
    _, what, parts = SECTIONS[sid]
    return what.format(as_of=as_of), [p.format(as_of=as_of) for p in parts]


def request(run: dict, arm: str) -> str:
    listed = "\n".join(f"- {sid}: {what}. Cover: {'; '.join(parts)}." for sid in SECTIONS for what, parts in [parts_of(sid, run["as_of"])])
    return PROMPT.format(sections=listed, where=WHERE[arm], as_of=run["as_of"], company=run["company"], profile=run["profile"])


def ask(slug: str, arm: str) -> str:
    run = saved(slug)
    cwd = DOSSIER / "empty"
    cwd.mkdir(parents=True, exist_ok=True)
    text = request(run, arm)
    got = claude(text, cwd, TOOLS[arm], DOSSIER_ANSWER, BUDGET["ask"], ALLOWED.get(arm, ""), NAVY if arm in ALLOWED else None)
    (DOSSIER / f"{slug}.{arm}.json").write_text(json.dumps({"slug": slug, "arm": arm, "prompt": text, **got}, indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    return f"{slug} {arm}: {'answer' if got.get('structured') else 'no answer'}; {got.get('usage', {}).get('cost_usd')} USD"


def piece(answer: dict, sid: str) -> dict:
    """The part of an answer one verifier checks: a dossier section, or the chain, people and letter."""
    return answer["dossier"][sid] if sid != "outreach" else {k: v for k, v in answer.items() if k != "dossier"}


def order(slug: str, sid: str) -> tuple[str, str]:
    """Which arm is A: a fixed shuffle per part, so the label says nothing."""
    return ARMS if random.Random(f"{slug}:{sid}").random() < 0.5 else ARMS[::-1]


def answers(slug: str) -> dict[str, dict]:
    return {arm: json.loads(p.read_text(encoding="utf-8")) for arm in ARMS if (p := DOSSIER / f"{slug}.{arm}.json").exists()}


def verify_one(slug: str, sid: str, got: dict[str, dict]) -> str:
    run = saved(slug)
    a, b = order(slug, sid)
    what, parts = parts_of(sid, run["as_of"])
    text = VERIFY.format(as_of=run["as_of"], what=what, parts="; ".join(parts), company=run["company"], profile=run["profile"],
                         a=json.dumps(piece(got[a]["structured"], sid), indent=1, ensure_ascii=False),
                         b=json.dumps(piece(got[b]["structured"], sid), indent=1, ensure_ascii=False))
    checked = claude(text, BASELINE_REPO, TOOLS["verify"], JUDGED, BUDGET["verify"])
    (DOSSIER / f"{slug}.{sid}.verdict.json").write_text(json.dumps({"slug": slug, "part": sid, "A": a, "B": b, "packet": text, **checked},
                                                                   indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"{slug} {sid}: {'verdict' if checked.get('structured') else 'no verdict'}; {checked.get('usage', {}).get('cost_usd')} USD"


def verify_alone(slug: str, sid: str, got: dict) -> str:
    """One part of the platform's answer checked on its own, for its audit trail."""
    run = saved(slug)
    what, parts = parts_of(sid, run["as_of"])
    text = ALONE.format(as_of=run["as_of"], what=what, parts="; ".join(parts), company=run["company"], profile=run["profile"],
                        a=json.dumps(piece(got["structured"], sid), indent=1, ensure_ascii=False))
    checked = claude(text, BASELINE_REPO, TOOLS["verify"], SOLO, BUDGET["verify"])
    (DOSSIER / f"{slug}.{sid}.platform.check.json").write_text(json.dumps({"slug": slug, "part": sid, "packet": text, **checked},
                                                                          indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"{slug} {sid}: {'checked' if checked.get('structured') else 'not checked'}; {checked.get('usage', {}).get('cost_usd')} USD"


def calls(messages: list[dict]) -> list[tuple[str, dict, str]]:
    """Every tool call of a session in order: the tool, its arguments and what it returned."""
    results = {c["tool_use_id"]: c.get("content") for m in messages if m.get("type") == "user"
               for c in (m.get("message", {}).get("content") or []) if isinstance(c, dict) and c.get("type") == "tool_result"}
    text = lambda r: r if isinstance(r, str) else " ".join(i.get("text", "") for i in r or [] if isinstance(i, dict))
    return [(c["name"].removeprefix("mcp__navy__"), c.get("input") or {}, text(results.get(c["id"])))
            for m in messages if m.get("type") == "assistant" for c in m.get("message", {}).get("content", [])
            if c.get("type") == "tool_use" and c["name"] != "StructuredOutput"]


PLAIN = {"help": "the list of tools", "search": "statements and forecast rows that carry a term",
         "office": "one office: its chain, forecast rows, statements, people, routes and the layers beside it",
         "cell": "one forecast row or statement in full", "topics": "SBIR and STTR topics that carry a term",
         "people": "the people tied to an office and the offices above it",
         "neighbors": "an office's parent, siblings and children, and who contracts for it, leads it or absorbed it",
         "initiatives": "what leaders, Congress, the budget, oversight and news say in an office's chain",
         "page": "an office, vendor, person or row page with its sources",
         "ask": "buyers for a capability, changes, analogs, incumbents, competitor moves, teaming, meeting brief",
         "pulse": "requirements ranked by timing, the week's changes and next actions", "protests": "GAO bid protests against the Navy",
         "wiki": "what an office buys, in its own words", "dna": "a contracting office's buying book",
         "vendor": "a vendor by its unique entity identifier, with its contracts",
         "trace": "one requirement across forecast releases, notices and awards",
         "revisions": "forecast rows whose award window or value moved", "check": "checks a draft answer against the record",
         "sources": "each record in full with where it is published",
         "inventory": "what the record holds, domain by domain", "report": "an intelligence report's plan over the record, or an office's budget"}


def one_line(text: str, cap: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= cap else flat[:cap] + f" ... ({len(flat)} characters)"


# What we found, in the order a reader acts on it, each dossier part under a plain heading.
FOUND = {"pulse": "What to do now", "trace": "The lead requirement, release by release",
         "revisions": "What moved between forecast releases", "changed": "What changed in the last 90 days",
         "wiki": "What the office buys, in its own words", "org_graph": "Where the office sits",
         "people": "People named on the office's work", "buyers": "Other Navy offices that buy this work",
         "buying_book": "How its contracting office buys", "vendors": "Who holds this work now",
         "incumbents": "Contracts in this line ending soon", "analogs": "The closest past contract",
         "competitor_moves": "What the winning companies did this year", "teaming": "Who to team with",
         "initiatives": "What Congress and leaders have said", "sbir_topics": "Fitting SBIR and STTR topics",
         "protests": "Bid protests", "small_business": "Small business routes", "meeting_brief": "Brief for the first meeting"}


def wrap(text: str, indent: str = "  ") -> list[str]:
    """Wrap to 110 columns; a bullet's later lines sit under its text."""
    flat = " ".join(str(text).split())
    return textwrap.wrap(flat, 110, initial_indent=indent, subsequent_indent=indent + "  " * flat.startswith("- "))


def trail(slug: str) -> str:
    """The platform session as a formal plain-text audit trail: first what it found in plain words, then the answer and
    dossier with every claim's identifier, quote and source, the verifier's verdict on each claim, and last the tools it
    called, what each returned and the check tool's refusals."""
    run, got = saved(slug), json.loads((DOSSIER / f"{slug}.platform.json").read_text(encoding="utf-8"))
    a, use, made = got["structured"], got["usage"], calls(got["transcript"])
    tally = Counter(name for name, _, _ in made)
    asked = Counter((args.get("args") or "").split(" ")[0] for name, args, _ in made if name == "ask")
    checks = [(n, r) for n, (name, _, r) in enumerate(made, 1) if name == "check"]
    checked = {sid: json.loads(f.read_text(encoding="utf-8")).get("structured") for sid in ["outreach", *SECTIONS]
               if (f := DOSSIER / f"{slug}.{sid}.platform.check.json").exists()}
    total = Counter(c["verdict"] for v in checked.values() if v for c in v["A"]["claims"])
    chain = " > ".join(x for x in (a["agency"], a["command"], a["peo"], a["program_office"]) if x)
    claims = sum(len(a["dossier"][sid]["claims"]) for sid in SECTIONS) + sum(len(a[k]) for k in ("requirements", "initiatives", "people"))
    asks = [l.strip() for l in a["email"]["body"].splitlines() if l.strip().startswith("- ")]
    lines = [f"{run['company'].upper()}: AUDIT TRAIL OF CLAUDE ON THE PLATFORM'S TOOLS", f"Record date: {run['as_of']}.", "",
             "1. What we found", "",
             "The buyer", *wrap(chain), *wrap(a["why_office"]), "",
             "What the buyer plans to buy", *[l for r in a["requirements"] for l in wrap(f"- {r['title']} ({r['identifier']})")], "",
             # ponytail: a 36-character identifier is a row key, meaningless to a reader
             "What Congress and the Navy have asked for in this line", *[l for r in a["initiatives"] for l in wrap(
                 f"- {r['title']}" + ("" if len(r["identifier"]) == 36 else f" ({r['identifier']})"))], "",
             "Who to write to", *[l for r in a["people"] for l in wrap(
                 f"- {r['name']}, {r['title'].split(';')[0]}, {r['email'] or 'no published e-mail'}. {r['why']}")], "",
             "Where to get small business help", *[l for r in a["routes"] for l in wrap(f"- {r['route']}")], ""]
    for sid in [*FOUND, *(set(SECTIONS) - set(FOUND))]:
        lines += [FOUND.get(sid, sid), *wrap(a["dossier"][sid]["summary"]), ""]
    lines += ["The letter", *wrap(f"To {a['email']['to']}. Subject: {a['email']['subject']}."),
              *(["  It asks:", *[l for q in asks for l in wrap(q, "    ")]] if asks else []), "",
              "How this was proven", *wrap(
                  f"Each of the {claims} claims in sections 2 and 3 carries its identifier, the exact passage it rests on and "
                  "the page that publishes it. Before answering, the session passed its draft to a check that refuses an office, "
                  f"record or person the record does not hold. The check refused {sum(r.strip() != 'all checks pass' for _, r in checks)} "
                  "drafts, and the answer in section 2 is the one it passed." + (
                      " A separate Claude session with web access, told nothing about who wrote the answer, then opened every "
                      f"source{'' if len(checked) > len(SECTIONS) else f' in the {len(checked)} of {len(SECTIONS) + 1} parts checked so far'}: "
                      f"{total['supported']} claims supported, {total['unsupported']} unsupported, {total['contradicted']} "
                      f"contradicted and {total['unverifiable']} unverifiable (section 4)." if total else "")), "",
             "2. The answer", "", f"Fit: {'yes' if a['fit'] else 'no'}.", f"Chain: {chain}.", f"Why this office: {a['why_office']}", ""]
    for label, key in (("Requirements", "requirements"), ("Initiatives", "initiatives")):
        lines.append(f"{label}:")
        for r in a[key]:
            lines += [f"  - {r['identifier']}: {r['title']}", f"    Quote: \"{r['quote']}\"", f"    Source: {r['source']}"]
        lines.append("")
    lines.append("People:")
    for r in a["people"]:
        lines += [f"  - {r['name']}, {r['title']}" + (f", {r['email']}" if r["email"] else ""), f"    Source: {r['source']}"]
    lines += ["", "Routes:", *[f"  - {r['route']} ({r['source']})" for r in a["routes"]], "",
              "The letter:", f"  To: {a['email']['to']}", f"  Subject: {a['email']['subject']}", "",
              *["  " + l for l in a["email"]["body"].splitlines()], "", "3. The dossier", ""]
    for n, sid in enumerate(SECTIONS, 1):
        sec = a["dossier"][sid]
        lines += [f"3.{n} {sid} ({SECTIONS[sid][0]})", f"  {sec['summary']}"]
        for c in sec["claims"]:
            lines += [f"  - {c['claim']}", f"    Identifier: {c['identifier']}", f"    Quote: \"{c['quote']}\"", f"    Source: {c['source']}"]
        lines.append("")
    if checked:
        lines += ["4. Independent verification", "",
                  "A separate Claude session with web access and the saved documents, told nothing about who wrote the answer,",
                  "opened every source and gave each claim a verdict.", "",
                  *grid([["Part", "Supported", "Unsupported", "Contradicted", "Unverifiable"]] +
                        [[sid, *(str(sum(c["verdict"] == k for c in v["A"]["claims"])) for k in ("supported", "unsupported", "contradicted", "unverifiable"))]
                         for sid, v in checked.items() if v] +
                        [["All parts", *(str(total[k]) for k in ("supported", "unsupported", "contradicted", "unverifiable"))]]), ""]
        for sid, v in checked.items():
            if v:
                lines.append(f"{sid}:")
                lines += [f"  [{c['verdict']}] {c['claim']}\n     Check: {c['reason']}" for c in v["A"]["claims"]]
                lines.append("")
    lines += [f"{5 if checked else 4}. How the session worked", "",
              f"Session: {use.get('model')}, {use.get('cost_usd') or 0:.2f} USD, {round((use.get('seconds') or 0) / 60, 1)} minutes, "
              f"{len(made)} tool calls, {use.get('turns')} turns.", "",
              "Tools used:", "", *grid([["Tool", "Calls", "What it answers"]] + [[n, str(c), PLAIN.get(n, n)] for n, c in tally.most_common()]),
              "", "Questions asked through ask: " + ", ".join(f"{q} {c}" for q, c in asked.most_common()) + ".",
              "", f"Tools offered and not called: {', '.join(sorted(set(['help', *HELP]) - set(tally))) or 'none'}.", "",
              "The check tool:",
              "Before answering, the session passed its draft to the check tool, which refuses a chain, record or person the",
              "record does not hold. Each call and its result:"]
    lines += [f"  Call {n}: {one_line(r, 700)}" for n, r in checks] or ["  No call."]
    lines += ["", "Every tool call, in order:", ""]
    for n, (name, args, result) in enumerate(made, 1):
        shown = args.get("args") if set(args) == {"args"} else json.dumps(args, ensure_ascii=False) if args else ""
        lines += [f"{n:>3}. {name} {one_line(shown or '', 160)}".rstrip(), f"     returned: {one_line(result, 400)}"]
    return "\n".join(lines) + "\n"


def score(side: dict) -> dict:
    verdicts = Counter(c["verdict"] for c in side["claims"])
    parts = Counter(p["verdict"] for p in side["parts"])
    return {"claims": len(side["claims"]), "supported": verdicts["supported"], "contradicted": verdicts["contradicted"],
            "unsupported": verdicts["unsupported"], "unverifiable": verdicts["unverifiable"],
            "parts": parts["answered"] + parts["partly"] / 2, "of": len(side["parts"])}


def rows(slug: str) -> list[dict]:
    out = []
    for sid in ["outreach", *SECTIONS]:
        path = DOSSIER / f"{slug}.{sid}.verdict.json"
        verdict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        judged = verdict.get("structured")
        row = {"part": sid, "feature": SECTIONS[sid][0] if sid in SECTIONS else "check"}
        for label in ("A", "B") if judged else ():
            row[verdict[label]] = score(judged[label])
        if judged:
            row["better"] = judged["better"] if judged["better"] == "tie" else verdict[judged["better"]]
            row["why"] = judged["why"]
        out.append(row)
    return out


def used(got: dict[str, dict]) -> dict:
    """Which platform tools the platform session called, and which it never did; the cost and time of each arm."""
    p = got.get("platform", {})
    calls = features(p["transcript"]) if p.get("transcript") else Counter()
    return {"called": dict(calls.most_common()), "never": sorted(set(["help", *HELP]) - set(calls)),
            **{arm: {**{k: g.get("usage", {}).get(k) for k in ("cost_usd", "seconds", "turns", "input_tokens")},
                     "lookups": len(tool_uses(g["transcript"])) if g.get("transcript") else 0} for arm, g in got.items()}}


def ratio(s: dict | None, key: str = "supported") -> str:
    return f"{s[key]}/{s['claims']}" if s else "-"


def grid(rows_: list[list[str]]) -> list[str]:
    """Plain-text columns: a header, a rule, then the rows, each column as wide as its widest cell."""
    width = [max(len(r[i]) for r in rows_) for i in range(len(rows_[0]))]
    line = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, width)).rstrip()
    return [line(rows_[0]), line(["-" * w for w in width]), *map(line, rows_[1:])]


def report(slug: str) -> str:
    """The comparison as a formal plain-text record: the method, one table of results, tool use and cost, then the audit
    trail, every claim the verifier checked with its verdict and the reason it gave, part by part."""
    run, got, parts = saved(slug), answers(slug), rows(slug)
    use = used(got)
    total = {arm: Counter() for arm in ARMS}
    for r in parts:
        for arm in ARMS:
            total[arm].update(r.get(arm, {}))
    wins = Counter(r.get("better") for r in parts)
    table = [["Part", "Platform tool", "Platform supported", "Web supported", "Contradicted (platform/web)", "Preferred"]]
    table += [[r["part"], r["feature"], ratio(r.get("platform")), ratio(r.get("web")),
               f"{r['platform']['contradicted']}/{r['web']['contradicted']}" if r.get("platform") and r.get("web") else "-", r.get("better", "-")]
              for r in parts]
    table.append(["All parts", "", ratio(total["platform"]), ratio(total["web"]), f"{total['platform']['contradicted']}/{total['web']['contradicted']}",
                  f"platform {wins['platform']}, web {wins['web']}, tie {wins['tie']}"])
    lines = [f"{run['company'].upper()}: CLAUDE ON THE PLATFORM'S TOOLS COMPARED WITH CLAUDE ON THE OPEN WEB",
             f"Record date: {run['as_of']}.", "",
             "1. Method", "",
             "Both sessions received the same request: to go from the company's capability to the agency, command, program",
             "executive office and program office that buy it, with that office's requirements, initiatives and people and one",
             "outreach letter, and to write a dossier of nineteen sections around that chain, one for each thing the platform",
             "answers. The platform session could reach only the platform's tools; the web session could reach only web search",
             "and fetch. Both used the same model. For each part, a separate verifier received both answers under the labels A",
             "and B in a fixed random order, opened every source, gave each claim a verdict and stated which answer serves the",
             "company better.", "",
             "2. Results", "", *grid(table), "",
             "A claim is supported when its source states it, contradicted when a source states otherwise, and otherwise",
             "unsupported or unverifiable. The counts are the verifier's.", "",
             "3. Tool use and cost", "",
             "Platform tools called: " + ", ".join(f"{k} {v}" for k, v in use["called"].items()) + ".",
             "Platform tools not called: " + (", ".join(use["never"]) or "none") + "."]
    for arm in ARMS:
        u = use.get(arm, {})
        lines.append(f"{arm.capitalize()} session: {u.get('cost_usd') or 0:.2f} USD, {round((u.get('seconds') or 0) / 60, 1)} minutes, "
                     f"{u.get('lookups')} lookups, {(u.get('input_tokens') or 0) / 1e6:.1f} million input tokens.")
    lines += ["", "4. Audit trail", ""]
    for n, r in enumerate(parts, 1):
        path = DOSSIER / f"{slug}.{r['part']}.verdict.json"
        verdict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        judged = verdict.get("structured")
        lines.append(f"4.{n} {r['part']} ({r['feature']})")
        if not judged:
            lines += ["Not checked.", ""]
            continue
        for label in sorted(("A", "B"), key=lambda lab: ARMS.index(verdict[lab])):
            lines += ["", f"{verdict[label].capitalize()} answer"]
            for i, c in enumerate(judged[label]["claims"], 1):
                lines += [f"  {i}. [{c['verdict']}] {c['claim']}", f"     Check: {c['reason']}"]
        lines += ["", f"Preferred: {r['better']}. {r['why']}", ""]
    return "\n".join(lines) + "\n"


def selfcheck() -> int:
    run = {"as_of": "2026-09-22", "company": "Acme", "profile": "builds balloons"}
    text = request(run, "platform")
    assert "{as_of}" not in text and all(f"- {sid}:" in text for sid in SECTIONS) and "check tool" in text, text[:400]
    assert "check tool" not in request(run, "web") and "web search" in request(run, "web")
    assert set(DOSSIER_ANSWER["required"]) == set(ANSWER["required"]) | {"dossier"}
    assert set(PLAIN) == {"help", *HELP}, set(PLAIN) ^ {"help", *HELP}
    assert set(DOSSIER_ANSWER["properties"]["dossier"]["properties"]) == set(SECTIONS)
    assert {order("x", sid) for sid in ["outreach", *SECTIONS]} == {ARMS, ARMS[::-1]}, "both labels fall to each arm"
    covered = {SECTIONS[s][0].split()[0] for s in SECTIONS} | {"ask", "check", "sources", "help"}
    assert set(HELP) - covered <= {"search", "office", "cell", "page", "inventory", "report"}, set(HELP) - covered  # the walk's own views and the report layer's commands need no section
    side = {"claims": [{"verdict": "supported"}, {"verdict": "contradicted"}], "parts": [{"verdict": "answered"}, {"verdict": "partly"}]}
    assert score(side) == {"claims": 2, "supported": 1, "contradicted": 1, "unsupported": 0, "unverifiable": 0, "parts": 1.5, "of": 2}
    print("dossier_compare selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selfcheck"]:
        return selfcheck()
    ap = argparse.ArgumentParser(prog="dossier_compare.py")
    ap.add_argument("command", choices=("ask", "verify", "check", "report", "trail"))
    ap.add_argument("slug", nargs="?", default="make-sunsets")
    ap.add_argument("--arm", choices=ARMS)
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--part", action="append", help="verify only these parts (default: every part)")
    args = ap.parse_args(argv)
    DOSSIER.mkdir(parents=True, exist_ok=True)
    if args.command == "report":
        out = DOSSIER / f"{args.slug}.report.txt"
        out.write_text(report(args.slug), encoding="utf-8")
        print(out)
    elif args.command == "trail":
        out = DOSSIER / f"{args.slug}.platform.trail.txt"
        out.write_text(trail(args.slug), encoding="utf-8")
        print(out)
    elif args.command == "check":
        got = answers(args.slug).get("platform", {})
        if not got.get("structured"):
            print("the platform arm needs an answer first", file=sys.stderr)
            return 1
        with ThreadPoolExecutor(args.jobs) as pool:
            for line in pool.map(lambda sid: verify_alone(args.slug, sid, got), args.part or ["outreach", *SECTIONS]):
                print(line, flush=True)
    elif args.command == "ask":
        print(ask(args.slug, args.arm or ap.error("ask needs --arm")))
    else:
        got = answers(args.slug)
        if not all(got.get(arm, {}).get("structured") for arm in ARMS):
            print("both arms need an answer first", file=sys.stderr)
            return 1
        with ThreadPoolExecutor(args.jobs) as pool:
            for line in pool.map(lambda sid: verify_one(args.slug, sid, got), args.part or ["outreach", *SECTIONS]):
                print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

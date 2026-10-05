---
name: intelligence-report
description: >
  Write an intelligence report on one federal agency from this repository's own record, through the read surface
  (research/tools/navy.py) and the report layer (research/tools/report.py): take stock of what the record holds,
  plan the reads from a spec, gather the evidence, write claims that quote it, check every claim, render Markdown.
  Use when the user says "intelligence report for <agency>", "agency brief", "report on <agency>", "what is
  <agency> buying", "who holds the budget at <office>", "brief me on <agency> <topic>", or names an agency key
  (navy, darpa, airforce, army, dhs, doe, nasa, noaa) with report, brief or intelligence.
user-invokable: true
argument-hint: "<agency key> [--spec agency-brief|agency-full] [--focus TERM] [--headless]"
---

# Intelligence report for one agency

The record tools answer "what does the record hold". This skill is "how an agent turns that into a report". Every
fact in the report comes through a tool call on the frozen record; nothing is fetched; the report is as of the
record's own date. Work from the repository root with its virtualenv (`.venv/bin/python`).

For a startup against an agency (a company profile, the web beside the record, three validators), run the
`startup-agency-brief` workflow instead: `research/workflow/README.md`.

## 1. Take stock

```
.venv/bin/python research/tools/report.py inventory --agency KEY
```

Read `as_of`, `corpus.exists`, and each domain's `present`, `rows` and `newest`. Tell the user what the record
holds and what is absent before anything else. If `corpus.exists` is false, say so: the sections that read the
corpus become boundaries and the report rests on the files that are present.

## 2. Plan

```
.venv/bin/python research/tools/report.py specs
.venv/bin/python research/tools/report.py plan --agency KEY --spec agency-brief [--focus TERM] [--mode brief|full]
.venv/bin/python research/tools/report.py plan --agency KEY --spec agency-brief [--focus TERM] --schema
```

The plan carries the anchor offices (an inference; its rule is stated) and one step per section, each with the
exact command, its shell form (`cli`), its server form (`mcp`) and a status: `planned`, `boundary` (the record
lacks the domain; the reason says what was read), `optional` (needs the agency's database) or `deferred` (its
argument comes from another step's answer). Decide which steps the user's question needs; keep every boundary,
because the reader needs them. A user's own report structure is a new JSON file in `research/report_specs/`.

## 3. Gather the evidence, one of two ways

Deterministic, in-process:

```
.venv/bin/python research/tools/report.py gather --agency KEY --spec agency-brief [--focus TERM] --out DIR
```

Then read `DIR/evidence.md` (each answer cut at its cap with a pointer to the command for the rest) and
`DIR/bundle.json` (the full text, which the checker verifies quotes against).

Or through the agency's own server, following the plan's `mcp` field for each step:

```
AGENCY=KEY .venv/bin/python research/tools/navy.py serve
```

The server's tools are the navy.py commands, `inventory` and `report plan|budget` among them. Write what each call
returned into `DIR/bundle.json` in the shape `gather` writes (`gathered: [{key, section, command, args, anchor,
output, chars, empty, boundary}]`), and the plan into `DIR/plan.json`.

## 4. Write the answer

Write `DIR/answer.json` in the schema `plan --schema` prints (also saved as `DIR/schema.json`): one section per
step, keyed exactly as the plan keys them. Each section holds:

- `claims`: one fact each, with the `identifier` of the record, office, person, vendor or budget line it rests on,
  an exact `quote` copied from the evidence, and the `source` (the tool call that answered, for example
  `people BTO`, or a URL the answer prints).
- `inferences`: each with the `rule` it rests on and `rests_on`, the claim identifiers it rests on.
- `summary` in two or three sentences; `not_found` saying what was read when a required part is missing.
- A boundary section claims nothing and states the boundary in `not_found`.

## 5. Check until clean, then render

```
.venv/bin/python research/tools/report.py check --out DIR
.venv/bin/python research/tools/report.py render --out DIR
```

The checker refuses a quote that is not in the evidence, an identifier that names nothing in the record, a source
that is neither a tool call nor a URL, the words likely, probably, imminent, expected soon, will release, RFP coming
and decision maker, budget holder or champion without "potential", a quote taken from another step's answer than the
one its source names, an inference without a rule, a potential role reading resting on fewer than two claims, and a boundary section with claims. Fix every line and run it
again. `render` writes `DIR/report.md`: how to read it, what the record holds, the anchor offices with their rule,
each section with its boundary first or its facts table, analysis and not-found, the sources, the method.

## Headless alternative

```
AGENCY=KEY .venv/bin/python research/tools/report.py run --spec agency-brief [--focus TERM] [--budget-usd 8]
```

One `claude -p` session on the agency's server does steps 3 to 5 and writes the same files, plus `transcript.json`
and `run.json` (model, turns, tokens, cost). It costs model budget and needs `claude` on PATH.

## Rules

- Treat evidence as part of the data model. Each quote comes from the answer its source names. A person is a
  potential decision maker, budget holder or champion only as an inference resting on at least two claims, never on
  a title alone, and with what is still to confirm. Keep facts, inferences and recommendations apart.
- State a boundary before anything else where the record holds nothing: what was read, its rows, its newest date.
  Nothing is counted as zero; it is counted as not read.
- Formal register, no em dashes, never likely, probably, imminent, expected, soon, "will release" or "RFP coming".
- Nothing after the record's date exists for the report. Name offices as the record writes them.
- Read-only: no pipeline stage, no collection, no database write.

## Hand back

The path of `report.md`; the checker's counts (claims, quotes verified, identifiers resolved, problems, the domains
with verified claims); the anchor offices with their basis; the boundaries; the cost line if headless. Do not paste
the whole report.

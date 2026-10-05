# Startup intelligence brief workflow

One brief for one startup against one agency record: Claude runs the record's tools and the web, applies the
ranking rule, has the draft validated three ways, applies the fixes and leaves a checked brief.

| File | What it is |
|---|---|
| `startup-agency-brief.js` | The Claude Code workflow: the phases, the agents and what each must return |
| `SPEC.md` | The brief's format and evidence bar: sections, ranking rule, people rules, tool map. Every agent reads it first |
| `check.py` | The mechanical checker: sections in order, each quote against the source its row cites, failed fetches listed as gaps, record identifiers through `report.py`'s resolver, the ranking rule and lead order, banned words, people readings, the tally. Exits 1 while anything is left |
| `save_source.py` | Saves a page or command output under `OUT/sources` with its line in `index.jsonl`; records refused, rate-limited and empty fetches as failed |

## What it does

1. **Profile.** The company's own pages (or a profile file) become `profile.txt`: what it sells, to whom, what it
   has not stated (size status, clearance), and the search terms for the record.
2. **Gather**, four agents at once:
   - **tools**: `report.py gather --spec agency-full` first (the anchor offices with their rule, one answer per
     section), then the company line across the read surface (`navy.py`: search, office, people, neighbors, wiki,
     page, `report budget`, ask changed/incumbents/prep/moves/team/match/analogs, vendor, dna, trace, cell,
     sources, revisions, protests, pulse, topics), the hiring readers (`jobs.py`, `vendor_jobs.py`), and each open
     instrument and incumbent award on SAM.gov and USAspending.
   - **record**: `report.py inventory` (what the record holds and what is absent) and the event files: budget
     lines, Congress, remarks, oversight, Federal Register, news, protests, grants, SBIR topics, hiring.
   - **web**: what the record may lack: open notices on SAM.gov, what leaders said in the last year, committee
     language and oversight reports, trade news, the offices' own pages (names and roles only).
   - **company**: the company's awards, size status and risk signals.
3. **Draft.** `brief.md` in the SPEC's 32 sections, candidates ranked by the published rule, then `check.py` until
   it is clean.
4. **Validate**, three agents at once: evidence (re-opens the primary sources), the review bar and coverage
   (every tool in the tool map ran and fed its section), ranking and dates.
5. **Apply.** Each finding is confirmed against its evidence before it is applied; the checker runs again, with a
   second round when it still raises anything.
6. **Gate.** One agent runs only the checker. A clean run releases the brief (the copy to `final` happens in the
   same shell line, only on exit 0); anything left, or a failed gathering step or validator, ends the run as
   incomplete.

Everything a quote rests on is saved under `OUT/sources/` through `save_source.py`, so the checker and the
validators read the same text, and each row's quote is checked only in the saved result that row cites by its id.
Read only: no pipeline stage, sweep or database write.

## Run it

From the repository root, with the virtualenv built and the agency's record on disk (`research/agencies/<key>/`,
or `research/` for navy), in a Claude Code session:

```
Workflow({scriptPath: "research/workflow/startup-agency-brief.js", args: {
  company: "Example Robotics", profile: "https://example.com",   // or a profile file path
  agency: "darpa", today: "2026-10-02", out: "research/results/briefs/example-robotics-darpa",
  final: "optional/copy/of/brief.md", python: ".venv/bin/python"  // both optional
}})
```

The same script is linked as `.claude/workflows/startup-agency-brief.js`, so the session also lists it by name.
Keys (`SAM_API_KEY`, `CONTEXT_DEV_API_KEY`, `EXA_API_KEY`) come from the environment or `.env`; the keyless
APIs cover the rest.

Check a brief on its own:

```
AGENCY=darpa .venv/bin/python research/workflow/check.py OUT/brief.md OUT/sources research/agencies/darpa
```

For an agency-only report with no company (one session on the record, no web), use the report layer directly:
`research/tools/report.py` and the `intelligence-report` skill.

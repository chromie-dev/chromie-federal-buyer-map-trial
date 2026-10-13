# Record + web in one agent, scored (2026-10-12)

The next step from the 10-12 benchmark: one agent reads production's agent views, then searches the web, under
the pursuit skill. Four agents (I, J, K, L) answered 5 HARD questions already scored for the record alone (v3) and
web alone (claude-web), plus 5 new ones. Graded by `promptfoo/both.yaml` (same rubrics and fact checks as run.sh).
Results: `promptfoo/results/both-1012-1012-1006.json`; notes and saved queries: `build/evals/both-1012/<QID>/`.

## Scores

| Question | record + web | record (v3) | web alone |
|---|---|---|---|
| HARD-05 NASA ground systems | gaps fail | pass | facts fail 2 |
| HARD-06 DHS cyber ops buyers | on_topic, gaps, fact fail | pass | fact fail |
| HARD-07 DHS AI security tools | on_topic, gaps, fact fail | pass | gaps, fact fail |
| HARD-10 CISA cyber lines | gaps, 1 fact fail | pass | 1 fact fail |
| HARD-13 DV513 full pursuit | gaps, 1 fact fail | pass | facts fail 2 |
| NEW-01..05 | 1 pass; 3 gaps fail, 1 on_topic fail | - | - |

All 10 passed `actionable`. 1 of 10 passed every check.

## Why the checks failed

| Check | Cause | Kind |
|---|---|---|
| no_bare_gaps (8 of 10) | "not in the record" with no reason: the skill said to state what is not held, not what the source shows for it | skill, fixed |
| HARD-13 TPOC e-mail | took the topic from HigherGov, which drops addresses; v3 read the DSIP listing. Production holds no contact for the TPOC | skill, fixed; data |
| HARD-06 IOM solicitation date 2026-10-13 | DHS forecast dates are not in production (BOARD E4) | data |
| HARD-07 ICE RFI due 2026-10-16 | found on the web (in the notes), left out of the answer | agent judgement |
| HARD-10 "Cyber Operations PPA" | answer gives the Cyber Operations figures without the word PPA | phrasing |
| on_topic (3) | stretched rows: FEMA support orders, counter-UAS under UAS integration | agent judgement |

v3's passes came from the repository's full local data (forecast fields, DSIP topic text, contact addresses), which
the agent views do not all carry yet. The web closed some of that gap and none of the gap-explanation one.

## What the web added that the record lacks

- CISA FY2026 book column is an annualized CR level, not enacted; stopgap end date 2026-12-11.
- House FY2027 report fences (CDM $302.6M, SOC automation $2M) the record holds only as headings.
- Live notice facts: NCCS 2.0 phase dates, CISA hygiene recompete solicitation date, DepthFirst notice office and contact.
- SBIR topic TPOC names (not addresses, from an aggregator), Q&A closing dates.

## What only the record held

Forecast rows and requirement owners, contracting officers, award history by office, budget line moves across books.

## Skill changes from this run

- Every "not held" line says what was read and what it shows (blank, "--", CR column, section not loaded, title only).
- DoD SBIR/STTR TPOC contacts come from the DSIP listing, not aggregators.
- Earlier the same day: view columns listed, directives also live in `agent.items`, CR book columns never called
  enacted, unplaced lines say "not placed in an office", champions route through the channel the money moves through.

## Data defects the agents hit (for the board)

Award rows without amounts; House report text partly loaded, FAA directives and FAA CJ missing; blank and printed "--"
look alike; leaders without e-mail; office tree lacks parent and successor links, ARPA-H modelled as an NIH contracting
office; notice deadlines and DHS forecast fields missing; notice text title only, SBIR topic text cut; Air Force lines
procurement only; one person under several ids; LinkedIn-only budget holder readings; mailboxes counted as people;
`needs` lacks the forecast number; people view times out on whole-agency e-mail questions (fix written, not applied).

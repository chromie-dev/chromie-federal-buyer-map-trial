# Benchmark, 2026-10-13: a rubric a script decides and a person can check

Earlier benchmarks were graded by one Claude model reading another's answers against four loose questions
(promptfoo `llm-rubric`). The 10-13 review asked for a grader less tied to the model it grades, with checks a person
can verify by hand. `research/evals/rubric.py` is that grader: twelve yes/no checks, each a rule over the answer's
text (and, for contract figures, a USAspending lookup). No model is asked. Every verdict is written to a sheet with
the lines it rests on, so a person confirms it in about a minute.

## The checks

| Check | Passes when | How a person verifies it |
|---|---|---|
| sourced_figures | every bullet with a dollar figure names its source, or sits under one that does | read the flagged bullet: is there a view, report, book, URL, award id or "(web)"? |
| facts_inference | FACTS and INFERENCE are separate sections | look at the headings |
| web_labelled | every URL sits under WEB, or carries the date read, or cites the record | read the flagged line |
| no_available | no remainder of money is called available, headroom or left to spend | read the flagged line: is a remainder offered as money to win? |
| no_ceiling_funding | no ceiling is presented as available money, room or funding | read the flagged sentence |
| dates_honest | a NEXT MOVE date is a deadline only if the same date appears in a sourced fact; any other date says "suggested" | find the date among the facts |
| next_move_parts | NEXT MOVE names a contact, a specific line, notice or need, and a date | read NEXT MOVE |
| fit_authority_path | NEXT MOVE says why the buy fits the product, why the person can help, and how the money is paid | read NEXT MOVE |
| verify_first | where a role or fit is only inferred, NEXT MOVE starts by confirming it | read INFERENCE, then NEXT MOVE |
| no_bare_gaps | every "not in the record" or "no figure" says what the source shows or why | read the flagged bullet |
| role_basis | every budget holder, decision maker or champion reading names what it rests on | read the flagged bullet |
| figures_match | each contract's obligated and ceiling figures equal USAspending's, within rounding and 1% | open the award page the sheet names |

An answer with nothing for a check to judge passes it. A vehicle's obligated figure is skipped: a vehicle obligates
nothing itself, and an answer's figure for it is its orders' total.

## Checked by hand

The first runs flagged lines that were right: a sub-bullet whose source sits on its parent line, "room, not money",
"still available: stops", the question line itself, "(web)" table labels, an answer refusing a title-only reading.
The rules were fixed and the self-check holds each case. Three passes read the flags outside fit_authority_path and
dates_honest by hand; those two were sampled, and every sampled flag was real. 30 awards were looked up on
USAspending; every comparable figure matched.

## Scores on the answers already written

| Set | What it is | Answers | All checks pass | Mean checks passed |
|---|---|---:|---:|---:|
| v3-1012 | record only | 8 | 0 | 79% |
| claude-web-1012 | web search alone | 5 | 0 | 65% |
| both-1012 | record and web | 10 | 0 | 78% |
| q5-1013 | five new questions, drafts | 5 | 0 | 78% |
| q5-1013-out | the same, edited for sending (sources removed by the sending rules) | 5 | 0 | 73% |
| q5-hidden-1013 | the same questions with stored grants and ended contracts hidden | 5 | 0 | 77% |

| Check | v3 | web | both | q5 | q5 sent | q5 hidden |
|---|---:|---:|---:|---:|---:|---:|
| sourced_figures | 7/8 | 1/5 | 10/10 | 5/5 | 1/5 | 3/5 |
| facts_inference | 8/8 | 5/5 | 10/10 | 5/5 | 5/5 | 5/5 |
| web_labelled | 8/8 | 1/5 | 10/10 | 5/5 | 5/5 | 5/5 |
| no_available | 8/8 | 5/5 | 10/10 | 5/5 | 5/5 | 4/5 |
| no_ceiling_funding | 8/8 | 5/5 | 10/10 | 5/5 | 5/5 | 5/5 |
| dates_honest | 4/8 | 5/5 | 2/10 | 1/5 | 2/5 | 2/5 |
| next_move_parts | 6/8 | 0/5 | 10/10 | 5/5 | 5/5 | 5/5 |
| fit_authority_path | 0/8 | 0/5 | 3/10 | 0/5 | 0/5 | 1/5 |
| verify_first | 3/8 | 2/5 | 1/10 | 1/5 | 2/5 | 1/5 |
| no_bare_gaps | 8/8 | 5/5 | 7/10 | 5/5 | 4/5 | 5/5 |
| role_basis | 8/8 | 5/5 | 10/10 | 5/5 | 5/5 | 5/5 |
| figures_match | 8/8 | 5/5 | 10/10 | 5/5 | 5/5 | 5/5 |

What it shows: the answers source their figures, keep facts apart from readings and get contract figures right.
They fail the 10-13 bar on the next move: it rarely says why the buy fits the product and why the person can help,
it seldom starts by confirming what is only inferred, and dates chosen for outreach read as deadlines. The skill
now requires all three (SKILL.md section 6).

## Realistic questions

The questions above named the office, program and mission, which a customer would not know. Five new questions
name only the product and the agency (DHS, HHS, NASA, Air Force and Space Force, VA), for two products: AI agents
that run IT operations and fix security problems, and spacecraft that refuel, inspect and move satellites. Each
answer found its offices and programs from words for what the product does, and says which word found what. Stored
grants and ended contracts were hidden; contract dollars, account balances and which account paid came from
USAspending through the skill's tool.

| Set | Answers | All checks pass | Mean checks passed |
|---|---:|---:|---:|
| real-1013 | 5 | 5 | 100% |

What the answers did that earlier sets did not: every next move starts by confirming fit or authority when either is
only inferred; contracting staff are named as a route, never as the person who decides; suggested dates say
"suggested by" and why; an award counts against a budget line only where USAspending shows the line's account paid
it, and where it does not (an assisted buy paid from GSA's fund, a line below the published program activity) the
answer says where the trail stops.

Read with care:
- The answering agents could run the script on their own answer before handing it in, so a full score partly means
  the rules were followed. The person's reading is the check that matters, and it found one error the script cannot:
  an answer said USAspending gives no program-activity split for an account that it does split (by IT portfolio,
  none named for security). The sentence was corrected; the answer's conclusion did not change.
- The answers were written after the skill's next-move rules (SKILL.md section 6) changed, so the rise from the
  earlier sets is the new rules and the new questions together.
- Five questions are a small sample.

Checked by hand: all five answers read in full; every account balance they quote (five accounts) pulled again from
USAspending and equal to the cent; 27 contract figures equal to USAspending (with 23 more in q5-hidden-1013). The
figure check now also reads "obligated $X" and "ceiling $X" and keeps a figure inside its own sentence, after a
ceiling written for another award in the next sentence was read as this award's.

Data gaps the answers hit, in the order they cost the answer:
- Notices held as a title or cut text: an open SBIR topic stops mid-section, an RFI and a cancellation notice are
  titles only, so a dropped buy still looks open.
- People split across ids or with first and last names swapped; forecast contacts carry the forecast role as their
  title, and contracting contacts read as problem owners.
- Forecast contact addresses dropped on load for one agency, and misspelled domains on others.
- Budget lines with no placed office (NASA, VA) and books not loaded (Space Force research and procurement, CISA
  operations), so the requested-money link stops for those programs.
- A reorganization (NASA's research directorates merging) and two running contracts not in the record.

## Rerun

```
python research/evals/rubric.py v3-1012 claude-web-1012 both-1012 q5-1013 q5-1013-out q5-hidden-1013 real-1013 --verify
python research/evals/rubric.py --selfcheck
```
Sheets (with the flagged lines) go to build/evals/rubric/<set>/, outside the repository, because answers name people.

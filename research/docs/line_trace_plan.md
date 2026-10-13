# Trace budget lines and link people along the way (plan, 2026-10-13)

The 10-13 review: "we need to trace the actual budget lines and link people along the way." The chain, each link with
its source and how sure it is:

| Link | From | To | Source | Today |
|---|---|---|---|---|
| 1 | budget line (book) | Treasury account | book appropriation + bureau vs USAspending account title | answer time, by name (`budget --keyword`); stored lines carry no Treasury code |
| 2 | account | program activity (the book's PPA) | USAspending account files | answer time (`budget <account>`); DHS lines store no PPA, DoD lines store budget activity |
| 3 | account or activity | awards it paid | USAspending `tas_codes` + `program_activities` filters | answer time (`paid`), new 10-13 |
| 4 | award | funding office, awarding office | USAspending award | answer time (`award`, `paid`) |
| 5 | office | people | `agent.people_readings` (record) | in the views; office names differ between USAspending and the record |
| 6 | line | placed office | book (`placed_office`) | in the views, about half the lines |

Proof limits, said in every answer: USAspending stops at the program activity, so a line below the PPA (a DHS
investment, a DoD program element) has no published obligations or awards of its own; an assisted buy (GSA for
CISA's CDM) is paid from the buying agency's fund and shows up under that fund, not the line's account.

## Steps

1. Store each budget line's Treasury account and PPA where the book prints them (DHS PC&I and O&S books list the
   PPA per investment and the account per appropriation; DoD R-1/P-1 rows carry the appropriation, which maps to
   one account). Builder change, new stored fields, insert-only; makes link 1 exact and link 2 possible for DHS.
2. A table of USAspending office names to record offices (funding and awarding office codes on awards against
   `agent.offices` codes), so link 5 joins on codes, not names.
3. One view or tool step that walks 1 to 5 for a line and returns every hop with its source and whether it is
   stated or inferred; people come out with the hop that reached them (placed office, funding office, awarding office).
4. Test: CISA CDM (line 024_000009571) and one DoD program element end to end, figures checked by hand against the
   book and USAspending; then rerun the realistic questions and score them with research/evals/rubric.py.

Order: 3 at answer time first (the tool already has links 1-4), then 2, then 1 (it needs a load).

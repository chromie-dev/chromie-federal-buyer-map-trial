# People tracking

Tracks government people across sources as one identity with dated posts, detects when they move, and alerts the
companies that follow a person or an office. Every post and every move carries its source and the date it was
stated or observed.

The code lands in two product repositories: the runner (monitors, writers, move detection, alerts) and the app
(database, person and office pages, assistant tools). This folder holds both halves for review, grouped by purpose.
New files are copied whole; changes to files that already exist in those repositories are kept as one patch per
side, so only the changed lines appear.

## Layout

| Folder or file | Lands in | What it holds |
|---|---|---|
| `runner/people/` | runner `orchestration/gov/people/` | The shared writer (identity, dated posts, moves), the monitors listed under Sources, the move detector and the scheduled worker |
| `runner/tests/` | runner `tests/` | One test file per monitor and for the writer and move detector, with recorded HTML, XML and text samples |
| `runner/changes-to-existing-files.patch` | runner | Scheduled entry point and schedules, documented settings, the move alert e-mail, directory posts that no longer invent start dates, and the SAM contact sync writing notice posts and linking new e-mails to people already tracked |
| `app/database/` | app `supabase/migrations/`, `supabase/tests/` | Identity, dated posts, move ledger, company relationships, interactions and tracked offices, with row-level security and its test |
| `app/api/gov-people/route.js` | app `src/app/api/gov-people/[id]/route.js` | A person's timeline, relationship and interactions |
| `app/api/gov-organizations/route.js` | app `src/app/api/gov-organizations/[id]/route.js` | An office: who is confirmed or last observed there, moves in and out, potential champions |
| `app/pages/` | app `src/app/contractors/offices/[id]/page.js`, `src/components/pages/` | The office page |
| `app/components/` | app `src/components/ui/gov/people/` | The person history panel in the directory |
| `app/lib/` | app `src/lib/gov/people/` | The rules that label posts, moves and champion readings, with tests |
| `app/changes-to-existing-files.patch` | app | Move alerts in the notification menu, the history panel in the directory, current posts and history in the assistant's people tools |

## Sources

| Source | What it gives | Kind |
|---|---|---|
| FPDS award records (the accounts that create and approve each action) | Contracting staff per office, and moves when an account starts working for another office | Observed |
| SAM notice contacts | People at each notice's posting office | Observed |
| Official biography pages (flag officers, senior executives) | Posts with the dates the page states | Stated |
| war.gov officer and senior executive releases | Announced and effective assignments | Stated |
| Senate nominations | Nominations and confirmations | Stated |
| DVIDS change-of-command stories | The outgoing and incoming holder of a post | Stated |
| Licensed people data, monthly, for tracked people only | Departures to industry | Reported |

## How posts, moves and champions are shown

- **Confirmed current** only when an official source stated the post in the last 183 days. Anyone else is shown as
  **last observed here**, with the date. A two-year window decides only which people an office page lists; it
  never presents anyone as currently holding a role.
- A move is **confirmed** when the government's own publication states it, **reported** when a licensed profile
  does, and **inferred** when two observed records imply it (one office, then another). The label and its basis
  appear in the app and in the alert e-mail. A single absence never creates a move.
- A **potential champion** shows the documented role with its source, the problem the person owns, why the
  company matters to them, any evidence they will advocate (written "none found" when there is none) and what is
  still to confirm. A new role plus a meeting makes a potential champion, never a confirmed one. A title alone
  establishes neither budget authority nor willingness to advocate.
- Relationships, interactions and tracked offices belong to one company. Row-level security keeps each company's
  rows to itself, and the database test checks that one company cannot read another's.

## Tests

| Side | Command (run in that repository) | Result |
|---|---|---|
| Runner | `python -m unittest tests.test_gov_people_bios tests.test_gov_people_fpds_staff tests.test_gov_people_leadership tests.test_gov_people_moves tests.test_gov_people_notice_contacts tests.test_gov_people_vendors tests.test_gov_people_writer tests.test_gov_people_sam_sync` | 101 passed |
| App | `node --test src/lib/gov/people/person-history.test.mjs src/lib/gov/mcp/tools/people.test.mjs` | 18 passed |
| Database | `supabase test db` | all 15 files pass, people tracking included |

Code only: no scraped data, tables or JSON files. The FPDS staff and notice contact tests also read two recorded
SAM API answers, which stay with the runner repository; those tests run there.

"""The startup brief workflow's checker (research/workflow/check.py) and source saver (save_source.py): each quote
against the source its row cites, failed fetches as gaps, record identifiers through a resolver, the people readings,
the exit code, and the workflow's completion gate. Runs on small saved sources in a temp dir; the record is not needed."""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / "research" / "workflow"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, WF / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _load("brief_check", "check.py")
save_source = _load("brief_save_source", "save_source.py")

NOTICE = 'The office "will hold an industry day on 3 November 2026 for the autonomy line" per the notice.'
OTHER = "Award N0001426C0001 was made for hull coatings with a period of performance ending 30 June 2027."
ROLE = "Dr. Jane Roe, Program Manager, Tactical Technology Office, leads the autonomy portfolio."
BUDGET = "PE 0603468E: the program funds autonomy demonstrations for the Tactical Technology Office in FY 2027."
TALK = "Dr. Roe said the office needs onboard autonomy for satellites and wants new performers this year."
EVIDENCE = "| Identifier | What it says (exact quote) | Status | Source | Class | Provenance |\n|---|---|---|---|---|---|\n"


def sources(tmp_path, failed=()):
    """A run's OUT/sources with four saved pages and one command output, indexed the way save_source.py writes them."""
    src = tmp_path / "sources"
    for rel, source, text in [
        ("web/notice.txt", "https://sam.gov/opp/abc/view", NOTICE),
        ("web/award.txt", "https://www.usaspending.gov/award/CONT_AWD_N0001426C0001", OTHER),
        ("web/office.txt", "https://www.darpa.mil/about/offices/tto", ROLE),
        ("web/talk.txt", "https://www.darpa.mil/news/2026/roe-remarks", TALK),
        ("tools/budget.txt", "AGENCY=darpa .venv/bin/python research/tools/navy.py report budget TTO > x.txt", BUDGET),
    ]:
        save_source.save(str(src), rel, source, text=text)
    for source, reason in failed:
        save_source.save(str(src), None, source, reason=reason)
    return check.load(str(src))


def table(*rows):
    return EVIDENCE + "\n".join(rows) + "\n"


def problems(got, kind):
    return [p["text"] for p in got["problems"] if p["check"] == kind]


def test_quote_must_come_from_the_cited_source(tmp_path):
    S = sources(tmp_path)
    good = table('| HR001126S0001 | "will hold an industry day on 3 November 2026" | Live | https://sam.gov/opp/abc/view | primary | web |')
    got = check.check(good, S)
    assert got["quotes"] == {"checked": 1, "not_found": 0, "failures": []}
    # the same words cited to another record's page: the checker no longer searches every file at once
    wrong = table('| N0001426C0001 | "will hold an industry day on 3 November 2026" | Live | https://www.usaspending.gov/award/CONT_AWD_N0001426C0001 | primary | web |')
    fail = check.check(wrong, S)["quotes"]["failures"]
    assert len(fail) == 1 and fail[0]["found_in"].endswith("web/notice.txt")


def test_source_cell_must_name_something_saved(tmp_path):
    S = sources(tmp_path)
    got = check.check(table('| X1 | "will hold an industry day on 3 November 2026" | Live | SAM.gov | primary | web |'), S)
    assert any("names nothing saved" in p for p in problems(got, "source"))
    # an agent's own note under OUT/sources is not a source unless indexed
    (tmp_path / "sources" / "record").mkdir()
    (tmp_path / "sources" / "record" / "notes.md").write_text(NOTICE)
    S = check.load(S["src"])
    got = check.check(table('| X1 | "will hold an industry day on 3 November 2026" | Live | record/notes.md | primary | repo |'), S)
    assert problems(got, "source")


def test_same_and_commands_resolve(tmp_path):
    S = sources(tmp_path)
    brief = table(
        '| 0603468E | "the program funds autonomy demonstrations" | Live | `navy.py report budget TTO` | primary | tool |',
        '| 0603468E | "Tactical Technology Office in FY 2027" | Live | same | primary | tool |',
    )
    got = check.check(brief, S)
    assert not problems(got, "source") and got["quotes"]["checked"] == 2 and got["quotes"]["not_found"] == 0
    got = check.check(EVIDENCE + '| 0603468E | "Tactical Technology Office in FY 2027" | Live | same | primary | tool |\n', S)
    assert problems(got, "source"), "'same' with no row above resolves to nothing"


def test_a_citation_binds_only_the_saved_result_it_names(tmp_path):
    src = str(tmp_path / "sources")
    radio = "PMW 170 buys tactical radio terminals for surface ships under the next communications increment."
    sonde = "The program office fields radiosonde launchers for shipboard weather balloons in FY 2027."
    run = "AGENCY=navy .venv/bin/python research/tools/navy.py search {} > x.txt"
    row = '| PMW 170 | "buys tactical radio terminals for surface ships" | Live | {} | primary | tool |'
    assert save_source.save(src, "tools/radio.txt", run.format("radio"), text=radio)["id"] == "S1"
    S = check.load(src)
    for cell in ("[S1] navy.py search radio", "[S1]", "navy.py search radio"):
        got = check.check(table(row.format(cell)), S)
        assert not problems(got, "source") and got["quotes"]["not_found"] == 0, cell
    # a search never saved that shares the saved one's prefix, an unknown id, and an id under another command
    for cell in ("navy.py search radiosonde", "[S9]", "[S1] navy.py search radiosonde"):
        got = check.check(table(row.format(cell)), S)
        assert any("names nothing saved" in p for p in problems(got, "source")), cell
    assert save_source.save(src, "tools/sonde.txt", run.format("radiosonde"), text=sonde)["id"] == "S2"
    S = check.load(src)
    for cell in ("navy.py search radiosonde", "[S2] navy.py search radiosonde"):
        fail = check.check(table(row.format(cell)), S)["quotes"]["failures"]
        assert len(fail) == 1 and fail[0]["found_in"].endswith("tools/radio.txt"), "the quote is read only in S2"


def test_a_failed_tool_run_is_listed_by_its_own_command(tmp_path):
    S = sources(tmp_path, failed=[("AGENCY=navy .venv/bin/python research/tools/navy.py office NAVSEA", "timeout")])
    still = "\n## Still open\n\n{}\n"
    got = check.check(still.format("navy.py search radio was read; navy.py office NAVSEAL is pending."), S)
    assert any("is not in Still open" in p for p in problems(got, "gap")), "a script name or a longer command is no listing"
    for listing in ("not collected: navy.py office NAVSEA (timeout)", "not collected: [S6] (timeout)"):
        assert not problems(check.check(still.format(listing), S), "gap"), listing


def test_record_file_by_path(tmp_path):
    rec = tmp_path / "record" / "events"
    rec.mkdir(parents=True)
    (rec / "budget_lines.json").write_text(json.dumps({"lines": [{"text": BUDGET}]}))
    src = tmp_path / "sources"
    src.mkdir()
    S = check.load(str(src), [str(tmp_path / "record")])
    got = check.check(table('| 0603468E | "funds autonomy demonstrations for the Tactical" | Live | events/budget_lines.json | primary | repo |'), S)
    assert not problems(got, "source") and got["quotes"]["not_found"] == 0


def test_every_row_needs_a_passage(tmp_path):
    S = sources(tmp_path)
    got = check.check(table("| HR001126S0001 | an industry day in November | Live | https://sam.gov/opp/abc/view | primary | web |"), S)
    assert problems(got, "quote")
    got = check.check(table("| HR001126S0001 | not in file | Live | https://sam.gov/opp/abc/view | primary | web |"), S)
    assert not problems(got, "quote")


def test_p0_fields_carry_a_passage(tmp_path):
    S = sources(tmp_path)
    old = ("| Field | Value | Identifier | Source | Class | Provenance |\n|---|---|---|---|---|---|\n"
           "| Deadline | 3 November 2026 | HR001126S0001 | https://sam.gov/opp/abc/view | primary | web |\n")
    assert any("passage column" in p for p in problems(check.check(old, S), "row"))
    new = (check.P0_HEADER + "\n|---|---|---|---|---|---|---|\n"
           '| Deadline | 3 November 2026 | HR001126S0001 | "will hold an industry day on 3 November 2026" '
           "| https://sam.gov/opp/abc/view | primary | web |\n")
    got = check.check(new, S)
    assert not problems(got, "row") and got["quotes"]["checked"] == 1 and got["quotes"]["not_found"] == 0
    assert "evidence rows 1" in got["tally"] and "rows on a primary source 1" in got["tally"]


def test_failed_fetch_is_a_gap(tmp_path):
    S = sources(tmp_path, failed=[("https://www.dodsbirsttr.mil/topics/X", "HTTP 403")])
    row = '| X | "anything at all from the topic page" | Live | https://www.dodsbirsttr.mil/topics/X | primary | web |'
    got = check.check(table(row) + "\n## Still open\n\nNothing.\n", S)
    gap = problems(got, "gap")
    assert any("rests on a failed fetch" in p for p in gap) and any("not in Still open" in p for p in gap)
    assert got["gaps"] == [{"source": "https://www.dodsbirsttr.mil/topics/X", "reason": "HTTP 403"}]
    listed = ("\n## Still open\n\n| Section | Not found | How to close it |\n|---|---|---|\n"
              "| SBIR | not collected: dodsbirsttr.mil refused this machine (HTTP 403) | read it from another network |\n")
    assert not problems(check.check(listed, S), "gap")


def test_save_source_records_failures(tmp_path):
    src = str(tmp_path / "s")
    sys.stdin, saved = io.StringIO(""), sys.stdin
    try:
        with redirect_stdout(io.StringIO()):
            assert save_source.main([src, "web/a.txt", "--source", "https://x.gov/a?api_key=SECRET1"]) == 2
    finally:
        sys.stdin = saved
    last = json.loads((tmp_path / "s" / "index.jsonl").read_text().splitlines()[-1])
    assert last["status"] == "failed" and last["reason"] == "empty response"
    assert "SECRET1" not in last["source"], "a key never reaches the index"
    assert save_source.failure("<html><title>Access Denied</title>Reference #18</html>").startswith("block page")
    assert save_source.failure("x" * 3000 + " status 403 ") is None, "a long real page that mentions 403 is kept"
    assert save_source.failure(NOTICE) is None
    entry = save_source.save(src, "web/b.txt", "https://x.gov/b", text=NOTICE)
    assert entry["status"] == "ok" and (tmp_path / "s" / "web" / "b.txt").read_text() == NOTICE


PEOPLE = """## Budget lines behind the work

{evidence}| 0603468E | "the program funds autonomy demonstrations" | Live | navy.py report budget TTO | primary | tool |

## What leaders have said

{evidence}| Roe remarks 2026 | "the office needs onboard autonomy for satellites" | Live | https://www.darpa.mil/news/2026/roe-remarks | primary | web |

## People and program offices

{evidence}| Dr. Jane Roe | "Dr. Jane Roe, Program Manager, Tactical Technology Office" | Live | https://www.darpa.mil/about/offices/tto | primary | web |

| Person | Reading | Documented role (exact quote) | Source | Why they matter for this requirement | Rests on | Still to confirm |
|---|---|---|---|---|---|---|
| Dr. Jane Roe | potential budget holder | "Program Manager, Tactical Technology Office" | https://www.darpa.mil/about/offices/tto | Runs the office the line is placed in | {rests} | {confirm} |

| Person | Documented role (exact quote) | Source | Problem they own | Why the company matters to them | Evidence of willingness to advocate | Rests on | Still to confirm |
|---|---|---|---|---|---|---|---|
| Dr. Jane Roe | "Program Manager, Tactical Technology Office" | https://www.darpa.mil/about/offices/tto | Onboard autonomy for satellites | The company sells onboard autonomy | none found | {champion} | Whether she would sponsor a proposal |

{prose}
"""


def people(S, rests="Dr. Jane Roe; 0603468E", confirm="Whether the line is hers to spend", champion="Roe remarks 2026",
           prose="Brief the potential decision maker first."):
    return check.check(PEOPLE.format(evidence=EVIDENCE, rests=rests, confirm=confirm, champion=champion, prose=prose), S)


def test_people_readings_pass_with_reasons(tmp_path):
    got = people(sources(tmp_path))
    assert not problems(got, "people"), problems(got, "people")
    assert not problems(got, "source") and got["quotes"]["checked"] == 5 and got["quotes"]["not_found"] == 0


def test_title_alone_is_refused(tmp_path):
    S = sources(tmp_path)
    assert any("budget line placed" in p for p in problems(people(S, rests="Dr. Jane Roe"), "people"))
    assert any("action or statement" in p for p in problems(people(S, champion="Dr. Jane Roe"), "people"))
    assert any("does not carry" in p for p in problems(people(S, rests="Dr. Jane Roe; PE 9999999X"), "people"))
    assert any("still needs confirmation" in p for p in problems(people(S, confirm="none"), "people"))


def test_role_words_need_potential(tmp_path):
    S = sources(tmp_path)
    got = people(S, prose="Ask the program manager, the decision-maker, at jane.doe@gmail.com. She is our champion.")
    texts = problems(got, "people")
    assert any("decision-maker" in p for p in texts) and any("'champion'" in p for p in texts)
    assert any("jane.doe@gmail.com" in p for p in texts)


def resolver(ident):
    return "record" if ident == "HR001126S0001" else None


def test_identifiers_through_the_resolver(tmp_path):
    S = sources(tmp_path)
    brief = table(
        '| HR001126S0001 | "will hold an industry day on 3 November 2026" | Live | https://sam.gov/opp/abc/view | primary | tool |',
        '| NOT-A-RECORD-9 | "will hold an industry day on 3 November 2026" | Live | https://sam.gov/opp/abc/view | primary | tool |',
        '| https://sam.gov/opp/abc/view | "will hold an industry day on 3 November 2026" | Live | https://sam.gov/opp/abc/view | primary | web |',
    )
    ids = check.check(brief, S, resolver)["identifiers"]
    assert ids["checked"] == 2, "web rows are not resolved against the record"
    assert [u["identifier"] for u in ids["unresolved"]] == ["NOT-A-RECORD-9"]
    assert check.check(brief, S, None)["identifiers"]["checked"] == 0


def test_blocking_and_exit_code(tmp_path):
    clean = {"problems": [], "quotes": {"not_found": 0}, "identifiers": {"unresolved": []}}
    assert not check.blocking(clean)
    assert check.blocking({**clean, "quotes": {"not_found": 1}})
    assert check.blocking({**clean, "identifiers": {"unresolved": [{"identifier": "X"}]}})
    S = sources(tmp_path)
    brief = tmp_path / "brief.md"
    brief.write_text(table('| X | "will hold an industry day on 3 November 2026" | Live | SAM.gov | primary | web |'))
    run = subprocess.run([sys.executable, str(WF / "check.py"), str(brief), S["src"]], capture_output=True, text=True)
    assert run.returncode == 1 and json.loads(run.stdout)["ok"] is False


def test_workflow_gates_completion():
    script = (WF / "startup-agency-brief.js").read_text(encoding="utf-8")
    assert script.startswith("export const meta = {")
    assert "research/workflow/check.py" in script.replace("${DIR}", "research/workflow")
    assert "save_source.py" in script
    assert "brief incomplete" in script and "lane failed" in script and "validator" in script
    apply_prompt = script[script.index("const APPLY_BASE"):script.index("let applied")]
    assert "Copy" not in apply_prompt and "cp " not in apply_prompt, "the copy happens only after the gate passes"
    linked = ROOT / ".claude" / "workflows" / "startup-agency-brief.js"
    assert linked.resolve() == (WF / "startup-agency-brief.js").resolve()

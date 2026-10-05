#!/usr/bin/env python3
"""What a SAM.gov special notice announces, read by a model from its title and text.

A special notice is SAM.gov's catch-all: the same type carries an industry day, a notice of intent to award a sole source,
a ceiling increase on a running contract, a workload forecast or a call for white papers. The model reads each one and
names one kind from a fixed list, or none, and copies the words of the notice that state it; the reading is kept only
when those words are in the notice verbatim. `trace.signal_kind` uses a kept reading and falls back to the title's
keywords otherwise, so a notice the model leaves unread is typed as before.

    python research/tools/notice_kinds.py build [--check]
    python research/tools/notice_kinds.py read NOTICE_ID
    python research/tools/notice_kinds.py --selfcheck
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from agency import P, RESULTS  # noqa: E402
from llm import structured  # noqa: E402
from reader import flatten  # noqa: E402

KINDS_FILE = RESULTS / "notice_kinds.json"  # the profile's results folder: each agency's notices are read on their own
# The kinds a special notice announces, each as trace.signal_kind prints it before "(special notice)".
KINDS = {"industry day": "an industry day, industry engagement, one-on-one sessions or a conference with industry",
         "action on an existing contract": "a ceiling increase, modification, extension, bridge or option on a named running contract",
         "intent to award a sole source": "the government intends to award to one named source without competition",
         "forecast": "a long range acquisition forecast, a workload forecast or a list of planned buys",
         "commercial solutions opening": "a commercial solutions opening or an area of interest under one",
         "request for information": "sources sought, market research, a request for information or for white papers",
         "draft solicitation": "a draft request for proposals or a draft statement of work for a planned buy, released for comment",
         "draft specification": "a draft military specification, standard or handbook circulated for comment, not a buy",
         "award announcement": "a contract, order or agreement that was awarded"}
SYSTEM = (f"You read one {P['short']} special notice from SAM.gov and say what it announces, choosing one kind from the list, or "
          "none when it announces none of them. Kinds:\n" + "\n".join(f"- {k}: {v}" for k, v in KINDS.items())
          + "\nAnswer with: kind, copied exactly from the list, or empty; words, a few words copied exactly from the notice's "
          "title or text that state it; reason, at most 20 words. Judge by what the notice says it is, not by the product it buys.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "words", "reason"],
          "properties": {"kind": {"type": "string", "enum": ["", *KINDS]}, "words": {"type": "string"}, "reason": {"type": "string"}}}


def problems(answer: dict, notice: str) -> list[str]:
    """What the notice does not support: the stated words not in it verbatim. No kind is an answer, not a failure."""
    if not answer["kind"]:
        return []
    if not answer["words"].strip() or flatten(answer["words"]).lower() not in flatten(notice).lower():
        return ["words are not in the notice verbatim"]
    return []


def ask(detail: dict, replay_only: bool = False) -> dict:
    notice = f"{detail['title']}\n{detail['text'][:3000]}"
    try:
        answer, how = structured(SYSTEM, f"Special notice posted {detail['posted']}:\n{notice}", SCHEMA, "notice_kind", replay_only=replay_only)
    except LookupError as exc:
        if replay_only:
            raise  # a check may not call the model: a missing cassette fails it, as everywhere else
        # No cassette and no key: the notice stands unread with the reason; trace.signal_kind falls back to the title's keywords.
        return {"kind": "", "words": "", "problems": [], "cassette": None, "unread": str(exc)}
    return {"kind": answer["kind"], "words": answer["words"], "problems": problems(answer, notice), "cassette": how["cassette"]}


def build(replay_only: bool = False, workers: int = 8) -> dict:
    from trace import NOTICES, notice_detail
    details = [d for p in sorted(NOTICES.glob("*.json")) if not p.name.startswith("._") and (d := notice_detail(p.stem))]
    todo = [d for d in details if d["type"] == "special notice"]
    with ThreadPoolExecutor(workers) as pool:
        answers = list(pool.map(lambda d: ask(d, replay_only), todo))
    return {d["id"]: a for d, a in zip(todo, answers)}


_KEPT: list[dict] = []


def kept(notice_id: str) -> str:
    """The kind the model read for a notice, where its words held; empty otherwise."""
    if not _KEPT:
        saved = json.loads(KINDS_FILE.read_text(encoding="utf-8")) if KINDS_FILE.exists() else {}
        _KEPT.append({nid: a["kind"] for nid, a in saved.items() if a["kind"] and not a["problems"]})
    return _KEPT[0].get(notice_id, "")


def selfcheck() -> int:
    notice = "NOTICE OF INTENT TO AWARD A SOLE SOURCE This Notice of Intent is not a request for competitive proposals."
    ok = {"kind": "intent to award a sole source", "words": "intent to award a sole source", "reason": ""}
    assert problems(ok, notice) == []
    assert problems({**ok, "words": "sole-source award intended"}, notice) == ["words are not in the notice verbatim"]
    assert problems({**ok, "kind": "", "words": ""}, notice) == [], "no kind is an answer"
    assert set(SCHEMA["properties"]["kind"]["enum"]) == {"", *KINDS}
    print("notice_kinds selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    if argv[0] == "read":
        from trace import notice_detail
        d = notice_detail(argv[1])
        print(json.dumps(ask(d), indent=1) if d else f"no saved notice {argv[1]}")
        return 0 if d else 1
    check = "--check" in argv
    out = build(replay_only=check)
    text = json.dumps(out, indent=1, sort_keys=True) + "\n"
    read = sum(1 for a in out.values() if a["kind"] and not a["problems"])
    unread = sum(1 for a in out.values() if a.get("unread"))
    print(f"{len(out)} special notice(s), {read} with a kind the model read and quoted verbatim" + (f", {unread} unread (no key)" if unread else ""))
    if check:
        same = KINDS_FILE.exists() and KINDS_FILE.read_text(encoding="utf-8") == text
        print("notice kinds match the saved file" if same else "notice kinds differ from the saved file", file=sys.stderr)
        return 0 if same else 1
    KINDS_FILE.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

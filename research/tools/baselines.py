#!/usr/bin/env python3
"""Direction tests for the back-test: the bar against
two dumb baselines, recall by the outcome's fiscal year, and which family combinations were followed by an outcome.

A monitor that only beats "a forecast row exists" or "an incumbent contract ends inside a year" by luck is a
forecast reader with extra steps. So, for every outcome and horizon, this asks whether each rule would have fired
by the cutoff, and for every pilot forecast cell, whether the rule's first firing was followed by a notice or award
within a year. The same matching as the back-test, the same follow window; nothing here is tuned.

    python research/tools/baselines.py run [--check]     # -> build/backtest_baselines.json and a table
    python research/tools/baselines.py --selfcheck
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (CORPUS, EMPTY_LABEL, FOLLOW_WINDOW_DAYS, HORIZONS, LABELS, ROOT, families_by, followed_by,  # noqa: E402
                      need_cell, outcome_cell, pilot_needs, reached_on, recurring_tokens, shift)
from pulse import ENDS_RE  # noqa: E402

from agency import BUILD  # noqa: E402

BASELINES = BUILD / "backtest_baselines.json"
BARS = (1, 2, 3, 4, 5)
ENDING_WINDOW_DAYS = 365
COMBINATION_MIN_CELLS = 3


def fiscal_year(day: str) -> str:
    year = int(day[:4]) + (1 if day[5:7] >= "10" else 0)
    return f"FY{str(year)[2:]}"


def ends_dates(events: list[dict]) -> list[tuple[str, str]]:
    """(available_by, ends) for every incumbent event that states when the contract ends."""
    out = []
    for e in events:
        m = ENDS_RE.search(e["title"]) if e["family"] == "incumbent" else None
        if m:
            out.append((e["available_by"], m.group(1)))
    return out


def incumbent_ending(events: list[dict], day: str, window: int = ENDING_WINDOW_DAYS) -> bool:
    """By `day`, the record already showed a contract of the cell's ending within `window` days after it."""
    return any(seen <= day < ends <= shift(day, window) for seen, ends in ends_dates(events))


def ending_fires_on(events: list[dict], window: int = ENDING_WINDOW_DAYS) -> str | None:
    """The first day the incumbent-ending rule fires for a cell: the contract is known and ends within the window."""
    days = [max(seen, shift(ends, -window)) for seen, ends in ends_dates(events) if seen < ends]
    return min(days) if days else None


def rules_at(hits: list[dict], day: str) -> dict:
    fams = set(families_by(hits, day))
    forecast = "forecast" in fams
    ending = incumbent_ending(hits, day)
    return {**{f"bar_{k}": len(fams) >= k for k in BARS}, "forecast_exists": forecast, "incumbent_ending": ending,
            "either": forecast or ending, "families": sorted(fams)}


def compute(corpus: dict, labels: dict, horizons: tuple[int, ...] = HORIZONS) -> dict:
    by_id = {row["id"]: row for row in labels["labels"]}
    rules = [f"bar_{k}" for k in BARS] + ["forecast_exists", "incumbent_ending", "either"]
    fired: dict[str, dict[str, int]] = {r: Counter() for r in rules}
    tally = lambda: {"outcomes": 0, "bar_3": Counter(), "forecast_exists": Counter()}
    groups: dict[str, dict[str, dict]] = {"by_period": defaultdict(tally), "by_command": defaultdict(tally)}
    combos_at_90: Counter = Counter()
    outcome_rows = []
    for outcome in corpus["outcomes"]:
        cell = outcome_cell(outcome, by_id.get(outcome["id"], EMPTY_LABEL), corpus)
        hits = [e for e in cell["hits"] if e["available_by"] <= outcome["date"]]
        period = fiscal_year(outcome["date"])
        keys = {"by_period": period, "by_command": outcome.get("contracting_office") or "-"}  # the office that posted it
        for g, k in keys.items():
            groups[g][k]["outcomes"] += 1
        row = {"id": outcome["id"], "date": outcome["date"], "period": period, "horizons": {}}
        for h in horizons:
            at = rules_at(hits, shift(outcome["date"], -h))
            row["horizons"][str(h)] = at
            for r in rules:
                fired[r][str(h)] += at[r]
            for g, k in keys.items():
                groups[g][k]["bar_3"][str(h)] += at["bar_3"]
                groups[g][k]["forecast_exists"][str(h)] += at["forecast_exists"]
            if h == 90:
                combos_at_90["+".join(at["families"]) or "none"] += 1
        outcome_rows.append(row)
    n = len(outcome_rows)
    recall = {r: {str(h): round(fired[r][str(h)] / n, 3) for h in horizons} for r in rules} if n else {}
    rates = lambda g: {p: {"outcomes": v["outcomes"], "bar_3": {h: round(v["bar_3"][h] / v["outcomes"], 3) for h in map(str, horizons)},
                           "forecast_exists": {h: round(v["forecast_exists"][h] / v["outcomes"], 3) for h in map(str, horizons)}}
                       for p, v in sorted(groups[g].items())}

    # Precision: for each pilot forecast cell, when did each rule first fire, and was that followed within a year?
    corpus_end = max(e["available_by"] for e in corpus["events"])
    recurring = recurring_tokens(corpus["needs"])
    events = corpus["events"]
    fires: dict[str, list[tuple[str | None, str | None]]] = {r: [] for r in rules if r != "either"}
    combo_cells: dict[str, Counter] = defaultdict(Counter)
    for need in pilot_needs(corpus):
        aliases, hits = need_cell(need, corpus, recurring)
        first_by_rule = {f"bar_{k}": reached_on(hits, k) for k in BARS}
        first_by_rule["forecast_exists"] = next((e["available_by"] for e in hits if e["family"] == "forecast"), None)
        first_by_rule["incumbent_ending"] = ending_fires_on(hits)
        for r, first in first_by_rule.items():
            followed = followed_by(first, aliases, events) if first else None
            fires[r].append((first, followed))
        first = first_by_rule["bar_3"]
        followed = followed_by(first, aliases, events) if first else None
        if first and (followed or shift(first, FOLLOW_WINDOW_DAYS) <= corpus_end):
            combo = "+".join(sorted(families_by(hits, first)))
            combo_cells[combo]["cells"] += 1
            combo_cells[combo]["followed"] += bool(followed)
    precision = {}
    for r, rows in fires.items():
        judged = [(f, fol) for f, fol in rows if f and (fol or shift(f, FOLLOW_WINDOW_DAYS) <= corpus_end)]
        followed = sum(1 for _, fol in judged if fol)
        precision[r] = {"cells": len(rows), "fired": sum(1 for f, _ in rows if f), "judged": len(judged), "followed": followed,
                        "value": round(followed / len(judged), 3) if judged else None}
    combinations = sorted(({"families": k, "cells": v["cells"], "followed": v["followed"], "rate": round(v["followed"] / v["cells"], 3)}
                           for k, v in combo_cells.items() if v["cells"] >= COMBINATION_MIN_CELLS),
                          key=lambda c: (-c["rate"], -c["cells"], c["families"]))
    return {"outcomes": n, "horizons": list(horizons), "recall": recall, "by_period": rates("by_period"), "by_command": rates("by_command"),
            "combinations_at_90_days": [{"families": k, "outcomes": v} for k, v in combos_at_90.most_common()],
            "precision": precision, "cell_combinations_at_the_bar": combinations, "per_outcome": outcome_rows}


def table(result: dict) -> str:
    hs = [str(h) for h in result["horizons"]]
    lines = [f"{'rule':18}" + "".join(f"{'recall@' + h:>11}" for h in hs) + f"{'precision':>11}{'judged':>8}"]
    for r, rec in result["recall"].items():
        p = result["precision"].get(r, {})
        lines.append(f"{r:18}" + "".join(f"{rec[h]:>11}" for h in hs) + f"{str(p.get('value', '-')):>11}{str(p.get('judged', '-')):>8}")
    for group, label in (("by_period", "by outcome period"), ("by_command", "by the contracting office that posted the outcome")):
        lines.append(f"{label} (bar_3 recall, forecast_exists recall):")
        for p, v in result[group].items():
            lines.append(f"  {p} n={v['outcomes']:<3} " + " ".join(f"@{h} {v['bar_3'][h]} ({v['forecast_exists'][h]})" for h in hs))
    lines.append("family combinations present when a pilot cell reached the bar, and the share followed within a year:")
    for c in result["cell_combinations_at_the_bar"][:12]:
        lines.append(f"  {c['rate']:<6} {c['followed']:>3} of {c['cells']:<4} {c['families']}")
    return "\n".join(lines)


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="baselines.py run")
    ap.add_argument("--check", action="store_true", help="recompute and fail if the saved file would change")
    args = ap.parse_args(argv)
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    result = compute(corpus, labels)
    text = json.dumps(result, indent=1, ensure_ascii=False) + "\n"
    print(table(result))
    if args.check:
        if not BASELINES.exists() or BASELINES.read_text(encoding="utf-8") != text:
            print("baselines differ from the saved file", file=sys.stderr)
            return 1
        return 0
    BASELINES.parent.mkdir(exist_ok=True)
    BASELINES.write_text(text, encoding="utf-8")
    return 0


def selfcheck() -> int:
    assert fiscal_year("2025-09-30") == "FY25" and fiscal_year("2025-10-01") == "FY26"
    ev = [{"family": "incumbent", "available_by": "2024-01-01", "title": "x ends 2025-06-30"},
          {"family": "forecast", "available_by": "2024-03-01", "title": "row"}]
    assert not incumbent_ending(ev, "2024-02-01"), "17 months out is not inside the year"
    assert incumbent_ending(ev, "2024-08-01")
    assert not incumbent_ending(ev, "2025-07-01"), "an ended contract does not fire"
    assert ending_fires_on(ev) == "2024-06-30", ending_fires_on(ev)
    at = rules_at(ev, "2024-08-01")
    assert at["bar_2"] and not at["bar_3"] and at["forecast_exists"] and at["incumbent_ending"] and at["either"]
    late = [{"family": "incumbent", "available_by": "2024-01-01", "title": "x ends 2024-03-01"}]
    assert ending_fires_on(late) == "2024-01-01", "a contract already inside its last year fires when it is first seen"
    print("baselines selfcheck ok")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--selfcheck":
        sys.exit(selfcheck())
    if args and args[0] == "run":
        sys.exit(run(args[1:]))
    print(__doc__)
    sys.exit(2)

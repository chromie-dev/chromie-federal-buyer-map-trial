#!/usr/bin/env python3
"""One event vocabulary: every event type sits at one stage of the
procurement path and carries a polarity, so a cell can say where the requirement stands, what would have to
happen next, and which statements argue for the buy and which against it.

Stage is the path position: strategy, budget, program, research, engagement, forecast, market_research,
solicitation, award, execution. Polarity is what the statement does to the case for a coming buy: positive
(the path moves), negative (delay, cancellation, extension, sole source, a cut), neutral (a fact about the
office that moves nothing by itself). Polarity is read from the type, and for four types from the words.

    python research/tools/vocabulary.py --selfcheck
"""

from __future__ import annotations

import re
import sys

STAGES = ("strategy", "budget", "program", "research", "engagement", "forecast", "market_research", "solicitation", "award", "execution")
# The path a requirement walks to a buy; execution is the incumbent's contract, not a step toward the next one.
PATH = ("forecast", "market_research", "solicitation", "award")
MILESTONE = {"forecast": "a forecast row", "market_research": "a sources sought or request for information",
             "solicitation": "a presolicitation or solicitation", "award": "an award"}

STAGE = {
    "strategy_change": "strategy", "capability_priority": "strategy", "congressional_directive": "strategy",
    "leadership_change": "strategy", "reorganization": "strategy",
    "funding_change": "budget", "budget_line": "budget",
    "program_created": "program", "program_delayed": "program", "program_cancelled": "program", "audit_finding": "program",
    "sbir_topic": "research", "sbir_selection": "research", "prototype_transition": "research",
    "industry_engagement": "engagement", "conference_appearance": "engagement",
    "forecast_created": "forecast", "forecast_changed": "forecast",
    "rfi_released": "market_research",
    "presolicitation_posted": "solicitation", "rfp_released": "solicitation", "justification_posted": "solicitation",
    "contract_awarded": "award", "protest": "award",
    "contract_modified": "execution", "contract_extended": "execution", "contract_expires": "execution",
    # A vacancy an office announces is the office building the capacity to run a program, before any engagement.
    "vacancy_posted": "program",
    # A vacancy a contractor posts against an office is industry positioning itself for the work, not the office's own step.
    "vendor_vacancy_posted": "engagement",
}
POLARITY = {
    "program_delayed": "negative", "program_cancelled": "negative", "contract_extended": "negative", "protest": "negative",
    "justification_posted": "negative",  # a sole-source or limited-competition justification closes the buy to others
    "leadership_change": "neutral", "reorganization": "neutral", "audit_finding": "neutral", "contract_awarded": "neutral",
    "vacancy_posted": "neutral",  # a job announcement is an intention to hire; by itself it moves no buy
    "vendor_vacancy_posted": "neutral",  # a contractor's posting is its intention to staff; it is neither an award nor a requirement
}
CUT_RE = re.compile(r"\b(cut|cuts|reduc\w*|decreas\w*|below|terminat\w*|cancel\w*|rescind\w*|shortfall|divest\w*|delay\w*)\b", re.I)
RENEWAL_RE = re.compile(r"\b(sole[- ]source|bridge|extension|extend\w*|exercis\w* (?:the |an? )?option|option period)\b", re.I)
AMOUNT_RE = re.compile(r"FY\d{4} \$(\d[\d,]*\.?\d*)M")
MOVED_RE = re.compile(r"\bfunding moved to\b")  # budget.py's note on a line whose book says the work went to another line
CUT_SHARE = 0.5  # a line that falls to under half of the year before, or to nothing, is a cut; a smaller dip moves nothing


# A capability a company names, with the words Navy records use for it. A topic outside the list is searched as written.
# Words that name a command as well as a field (space, as in Space and Naval Warfare) stay out.
CAPABILITIES = {
    "autonomy": ("autonomous", "autonomy", "unmanned", "uncrewed", "UUV", "USV", "UAS", "UAV", "robotic", "swarm"),
    "undersea": ("undersea", "underwater", "subsea", "sonar", "acoustic", "UUV", "seabed", "anti-submarine"),
    "cyber": ("cyber", "cybersecurity", "zero trust", "encryption", "cryptographic", "intrusion detection"),
    "artificial intelligence": ("artificial intelligence", "machine learning", "AI/ML", "neural network", "computer vision", "deep learning"),
    "communications": ("communications", "radio", "SATCOM", "data link", "datalink", "Link 16", "waveform", "antenna"),
    "satellites": ("satellite", "spacecraft", "orbital", "space-based", "SATCOM"),
    "electronic warfare": ("electronic warfare", "jamming", "jammer", "SIGINT", "electronic attack", "decoy"),
    "sensors": ("radar", "sensor", "electro-optical", "infrared", "EO/IR", "lidar", "sonar"),
    "command and control": ("command and control", "C2", "C4I", "battle management", "common operational picture", "mission planning"),
    "networks": ("network", "cloud", "enterprise services", "data center", "CANES", "ADNS", "NMCI", "NGEN"),
    "training": ("training", "simulation", "simulator", "trainer", "live virtual constructive", "LVC"),
    "logistics": ("logistics", "sustainment", "maintenance", "supply chain", "depot"),
    "positioning and timing": ("positioning", "navigation", "timing", "PNT", "GPS", "inertial"),
    "directed energy": ("directed energy", "laser", "high power microwave"),
    "counter unmanned": ("counter-UAS", "counter UAS", "C-UAS", "counter-drone", "counter unmanned"),
    "hypersonics": ("hypersonic",),
}
CAPABILITY_NAMES = {"autonomous systems": "autonomy", "unmanned systems": "autonomy", "ai": "artificial intelligence",
                    "machine learning": "artificial intelligence", "ew": "electronic warfare", "c2": "command and control",
                    "pnt": "positioning and timing", "c-uas": "counter unmanned", "asw": "undersea", "anti-submarine warfare": "undersea",
                    "space": "satellites"}


DESCRIPTION_WORDS = 2  # a part of two or more words that is no listed name describes a capability ("spacecraft autonomy"); its
# capabilities' words are searched beside it, since the phrase as written matches only a record that repeats it word for word
STOPWORDS = frozenset("a an and for in of on or the to with".split())


def named_capabilities(text: str) -> list[str]:
    """The listed capabilities a description names by any of their words or names: a capability line or a company's
    own page, read for what it offers."""
    said = lambda w: re.search(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", text, re.I)  # noqa: E731
    named = [n for n, words in CAPABILITIES.items() if said(n) or any(said(w) for w in words)]
    return list(dict.fromkeys(named + [n for alias, n in CAPABILITY_NAMES.items() if said(alias)]))


def capability_terms(topic: str) -> list[str]:
    """A topic's search words: a named capability's words, else the words as written; topics split on ; and ,. A
    multi-word part that is no listed name also brings the words of each capability it names: searched as written alone
    it matches only a record that repeats it word for word ("spacecraft autonomy" found nothing where "autonomous
    satellite navigation" stood)."""
    out = []
    for part in (p.strip() for p in re.split(r"[;,]", topic)):
        name = CAPABILITY_NAMES.get(part.lower(), part.lower())
        out += CAPABILITIES.get(name, (part,) if part else ())
        if name not in CAPABILITIES and len(part.split()) >= DESCRIPTION_WORDS and not proper_name(part):
            out += [w for n in named_capabilities(part) for w in CAPABILITIES[n]]
    return list(dict.fromkeys(out))


def proper_name(part: str) -> bool:
    """A part written as a name, every word capitalised or a number ("Next Generation Jammer", "Link 22"): searched as
    written, since its words name a program, not a capability."""
    words = part.split()
    return bool(words) and all(w[:1].isupper() or w[:1].isdigit() for w in words)


def term_groups(term: str) -> list[list[str]]:
    """A multi-word term as groups of aliases, one per word worth searching: the word itself with its plural handled by the
    pattern, widened to the words of the capability it names or belongs to. A search that requires one alias of every
    group finds "autonomous satellite navigation" for "spacecraft autonomy". Stopwords make no group; a one-word term
    makes one group."""
    groups = []
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9/-]*", term):
        if word.lower() in STOPWORDS:
            continue
        aliases = [word]
        for name, words in CAPABILITIES.items():
            if word.lower() == name or word.lower() in {w.lower() for w in words} or CAPABILITY_NAMES.get(word.lower()) == name:
                aliases += [name, *words]
        groups.append(list(dict.fromkeys(aliases)))
    return groups


def stage(event_type: str) -> str:
    return STAGE.get(event_type, "strategy")


def money_polarity(amounts: list[float]) -> str:
    """Fiscal-year amounts in order: rising or holding is positive, falling to nothing or under half is a cut, a
    smaller dip is neutral; a line with no money in any year says nothing."""
    if not any(amounts):
        return "neutral"
    if len(amounts) < 2:
        return "positive"
    before, after = amounts[-2], amounts[-1]
    if after >= before:
        return "positive"
    return "negative" if after < before * CUT_SHARE else "neutral"


def polarity(event_type: str, statement: str = "", slip: bool = False) -> str:
    """The type decides; four types read the statement (the event's title, not the document behind it): a funding
    line's amounts or a funding claim's words, a budget line's amounts, a forecast revision that moved later, a
    modification that keeps the incumbent."""
    if event_type in POLARITY:
        return POLARITY[event_type]
    if event_type in ("funding_change", "budget_line"):
        amounts = [float(a.replace(",", "")) for a in AMOUNT_RE.findall(statement)]
        if amounts:
            return "neutral" if MOVED_RE.search(statement) else money_polarity(amounts)
        return "neutral" if event_type == "budget_line" else ("negative" if CUT_RE.search(statement) else "positive")
    if event_type == "forecast_changed":
        return "negative" if slip else "neutral"
    if event_type == "contract_modified":
        return "negative" if RENEWAL_RE.search(statement) else "neutral"
    return "positive"


def classify(event_type: str, statement: str = "", slip: bool = False) -> dict:
    return {"stage": stage(event_type), "polarity": polarity(event_type, statement, slip)}


def stage_of(events: list[dict], as_of: str, within_days: int = 730) -> str:
    """Where the requirement stands as of a date: the stage of the newest path event (forecast, market research,
    solicitation, award) inside the window; `shaping` when only earlier stages spoke; `dormant` when nothing did."""
    from datetime import date, timedelta
    since = (date.fromisoformat(as_of) - timedelta(days=within_days)).isoformat()
    recent = [e for e in events if since < e["available_by"] <= as_of]
    path = [e for e in recent if e.get("stage", stage(e["event_type"])) in PATH and e.get("polarity", polarity(e["event_type"])) != "negative"]
    if path:
        newest = max(path, key=lambda e: (e["available_by"], PATH.index(e.get("stage", stage(e["event_type"])))))
        return newest.get("stage", stage(newest["event_type"]))
    if any(e.get("stage", stage(e["event_type"])) != "execution" for e in recent):
        return "shaping"
    return "dormant"


def next_milestones(current: str) -> list[str]:
    """What would have to happen next: the path steps after the current stage, in order."""
    start = PATH.index(current) + 1 if current in PATH else 0
    return [MILESTONE[s] for s in PATH[start:]]


def selfcheck() -> int:
    assert set(STAGE.values()) <= set(STAGES) and set(POLARITY) <= set(STAGE)
    assert polarity("program_delayed") == "negative" and polarity("forecast_created") == "positive"
    assert polarity("funding_change", "the budget adds $1 billion for infrastructure") == "positive"
    assert polarity("funding_change", "the request cuts the line by 40 percent") == "negative"
    assert polarity("budget_line", "FY2026 $0.000M, FY2027 $0.000M") == "neutral"
    assert polarity("funding_change", "line 2614 ATDLS: FY2026 $58.739M, FY2027 $52.758M") == "neutral", "a dip of a tenth is not a cut"
    assert polarity("funding_change", "line 2614 ATDLS: FY2026 $58.739M, FY2027 $61.000M") == "positive"
    assert polarity("funding_change", "line 2614 ATDLS: FY2026 $58.739M, FY2027 $0.000M") == "negative"
    assert polarity("funding_change", "line 2900 MIBS: FY2026 $8.479M, FY2027 $0.000M, funding moved to line 2361") == "neutral"
    assert polarity("funding_change", "line 2614 ATDLS: FY2026 $58.739M, FY2027 $20.000M") == "negative"
    assert polarity("budget_line", "FY2026 $58.739M, FY2027 $52.758M") == "neutral" and polarity("budget_line", "no amounts") == "neutral"
    assert polarity("forecast_changed", slip=True) == "negative" and polarity("forecast_changed") == "neutral"
    assert polarity("contract_modified", "Notice of Intent to Award Sole Source Modification") == "negative"
    assert polarity("contract_modified", "administrative change") == "neutral"
    assert polarity("never_seen_before") == "positive" and stage("never_seen_before") == "strategy"
    ev = [{"event_type": "sbir_topic", "available_by": "2025-01-01"}, {"event_type": "forecast_created", "available_by": "2025-06-01"},
          {"event_type": "rfi_released", "available_by": "2026-02-01"}, {"event_type": "contract_expires", "available_by": "2020-01-01"}]
    assert stage_of(ev, "2026-09-01") == "market_research"
    assert stage_of(ev, "2025-03-01") == "shaping", stage_of(ev, "2025-03-01")
    assert stage_of(ev, "2021-01-01") == "dormant", "an old contract end alone places nothing on the path"
    assert stage_of(ev + [{"event_type": "forecast_changed", "available_by": "2026-05-01", "stage": "forecast", "polarity": "negative"}], "2026-09-01") == "market_research", \
        "a slip does not move the requirement back along the path"
    assert next_milestones("market_research") == [MILESTONE["solicitation"], MILESTONE["award"]]
    assert next_milestones("shaping") == [MILESTONE[s] for s in PATH] and next_milestones("award") == []
    assert "UUV" in capability_terms("autonomous systems") and capability_terms("Link 22; ai")[0] == "Link 22"
    assert "machine learning" in capability_terms("Link 22; ai") and capability_terms(" ; ") == []
    assert capability_terms("Next Generation Jammer") == ["Next Generation Jammer"]  # a part written as a name is searched as written
    assert capability_terms("spacecraft autonomy")[:3] == ["spacecraft autonomy", "autonomous", "autonomy"], "a two-word description brings its capability's words"
    assert capability_terms("onboard autonomy for constellation operations")[0] == "onboard autonomy for constellation operations"
    assert term_groups("spacecraft autonomy") == [["spacecraft", "satellites", "satellite", "orbital", "space-based", "SATCOM"],
                                                  ["autonomy", "autonomous", "unmanned", "uncrewed", "UUV", "USV", "UAS", "UAV", "robotic", "swarm"]]
    assert term_groups("guidance navigation and control")[0] == ["guidance"] and len(term_groups("guidance navigation and control")) == 3, "stopwords make no group"
    assert term_groups("Link 22") == [["Link"], ["22"]] and term_groups("") == []
    line = capability_terms("AI pilot software for unmanned aircraft")
    assert line[0] == "AI pilot software for unmanned aircraft" and "UUV" in line and "machine learning" in line, line
    print("vocabulary selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(selfcheck() if "--selfcheck" in sys.argv[1:] else print(__doc__) or 2)

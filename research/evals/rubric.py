#!/usr/bin/env python3
"""A semi-deterministic rubric for pursuit answers (2026-10-13, after the 10-13 review: an evaluator less biased
than one model grading another, checks a person can verify by hand). Every check is yes or no and decided by a rule
in this file over the answer's text; none asks a model. Each verdict comes with the lines it rests on, written to a
sheet per answer, so a person checks it in about a minute. `--verify` also compares each contract's obligated and
ceiling figures with USAspending (public API).

Checks (an answer with nothing for a check to judge passes it):
  sourced_figures     every bullet with a dollar figure names its source (a view, USAspending, a report, a book, a URL)
  facts_inference     FACTS and INFERENCE are separate sections
  web_labelled        every URL sits under WEB or carries the date it was read
  no_available        no remainder of money is called available, headroom or left to spend (unobligated balance is fine)
  no_ceiling_funding  no ceiling is presented as available money, room or funding
  dates_honest        a NEXT MOVE date is a deadline only if the same date appears in a sourced fact; otherwise "suggested"
  next_move_parts     NEXT MOVE names a contact (address, phone or office), a specific line, notice or need, and a date
  fit_authority_path  NEXT MOVE says why the buy fits, why the person can help, and how the money is paid
  verify_first        where a role or fit is only inferred, NEXT MOVE starts by confirming it
  no_bare_gaps        every "not in the record" or "no figure" says what the source shows or why
  role_basis          every budget holder, decision maker or champion reading names what it rests on
  figures_match       (--verify) contract figures equal USAspending's, within rounding and 1%

    python research/evals/rubric.py SET [SET ...] [--verify] [--sheets build/evals/rubric]
    python research/evals/rubric.py --selfcheck
SET is a folder of answers (research/evals/promptfoo/answers/<SET>/*.txt).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANSWERS = HERE / "promptfoo" / "answers"
SECTION = re.compile(r"^(FACTS|WEB|FROM THE WEB|INFERENCE|NEXT MOVE|RECOMMENDATION|WHAT THE RECORD HOLDS)\b:?")
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:[KMBT]\b|million|billion)?", re.I)
SOURCE = re.compile(r"agent\.|usaspending|H\. ?Rept|S\. ?Rept|book|https?://|record|notice|item_id|as_of|forecast|"
                    r"budget_lines|justification|\bread 20\d\d|\(\w[^)]*20\d\d-\d\d-\d\d|\((?:web|stated)[,)]|\bweb:|rests on|same page|House|Senate|"
                    r"\b(?=[A-Z0-9]*[A-Z])(?=(?:[A-Z]*\d){6})[A-Z0-9]{10,17}\b", re.I)  # an award id is traceable too
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
AVAILABLE = re.compile(r"\b(?:still available|available (?:funding|money|budget)|headroom|left to (?:spend|obligate))\b"
                       r"|\$[\d,.]+\s?[MBK]?\s+(?:is )?available\b", re.I)
AVAILABLE_OK = re.compile(r"unobligated|\bstops\b|not (?:shown|proven|available)|never|no remainder|cannot|can't|"
                          r"no floor|not be worked", re.I)
CEILING_MONEY = re.compile(r"available|headroom|\broom\b|capacity|funding left|money left|unused", re.I)
CEILING_OK = re.compile(r"not (?:money|funding|available|a budget)|never", re.I)
GAP = re.compile(r"not in (?:the|this) record|no (?:FY ?\d{4} )?(?:figure|amount|obligations?)|not held|"
                 r"none in the record", re.I)
GAP_REASON = re.compile(r"because|since|shows|ended|not yet|expired|moved|zero|only|continuing resolution|\bCR\b|"
                        r"not swept|does not|blank|--|title only|not loaded|not published|instead|prints|lists|column|"
                        r"https?://|aggregator|web|stopgap|evidence|page|\bread\b|no reading|searched|book|\.gov|\.com|"
                        r"news|govinfo|congress|Rept|the one|holds", re.I)
ROLE = re.compile(r"budget holder|decision[- ]maker|champion", re.I)
ROLE_BASIS = re.compile(r"rests on|because|basis|inference|rule|placed|owns|forecast|notice|line|confirm|named as|"
                        r"requirement|signed|listed|not used|title alone|\btie\b|role held|none found|contact|scope|"
                        r"no source names", re.I)
CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|\boffice\b|directorate|command|division", re.I)
ANCHOR = re.compile(r"\b(?=[A-Z0-9_-]*\d{3})[A-Z0-9][A-Z0-9_-]{5,}\b|\"[^\"]{6,}\"|\bline\b|notice|forecast|topic", re.I)
FIT = re.compile(r"\bfit\b|fits|matches|covers|scope|asks for|requirement|need\b|calls for", re.I)
AUTHORITY = re.compile(r"authority|\brole\b|owns|holds|budget holder|decision|contracting officer|requirement (?:owner|contact)|"
                       r"program manager|\bPM\b|lead|director|chief|signs|point of contact|\bPOC\b|TPOC", re.I)
PATH = re.compile(r"path to payment|vehicle|IDIQ|\bBPA\b|GWAC|SEWP|schedule|SBIR|STTR|\bOTA?\b|other transaction|\bBAA\b|"
                  r"solicitation|\border\b|contract|grant|\bline\b|CSO|follow-on|recompete", re.I)
INFERRED = re.compile(r"\bpotential\b|unconfirmed|inferred|\binference\b|lead\b|not confirmed", re.I)
VERIFY = re.compile(r"confirm|verify|\bask\b[^.]{0,40}\b(?:whether|who|if|which)\b|find out|check (?:whether|who|if)", re.I)
SUGGESTED = re.compile(r"suggest|recommend|\baim\b|\btarget\b", re.I)  # a chosen date says so in words
PIID = re.compile(r"\b(?=[A-Z0-9]*[A-Z][A-Z0-9]*[A-Z])(?=(?:[A-Z]*\d){6})[A-Z0-9]{11,17}\b")
CLAIM = re.compile(r"(?:\b(obligated|ceiling)\s+)?\$\s?(\d[\d,]*(?:\.(\d+))?)\s?([MBK])?\b(\s*(?:obligated|of\b|ceiling))?", re.I)
VEHICLE = re.compile(r"INDEFINITE|BPA|BLANKET|GWAC|BASIC ORDERING|FSS|IDV", re.I)
UNIT = {"K": 1e3, "M": 1e6, "B": 1e9, None: 1.0}
CHECKS = ["sourced_figures", "facts_inference", "web_labelled", "no_available", "no_ceiling_funding", "dates_honest",
          "next_move_parts", "fit_authority_path", "verify_first", "no_bare_gaps", "role_basis"]


def bullets(text: str) -> list[dict]:
    """The answer cut into bullets (a line starting a bullet or section, with its indented continuation lines), each
    with its section and first line number."""
    out, section = [], "TOP"
    for n, line in enumerate(text.splitlines(), 1):
        m = SECTION.match(line)
        if m:
            section = "WEB" if m.group(1) == "FROM THE WEB" else m.group(1)
        if out and line.startswith((" ", "\t")) and not line.lstrip().startswith("- ") and not m:
            out[-1]["text"] += " " + line.strip()
        elif line.strip():
            depth = len(line) - len(line.lstrip())
            parent = next((b for b in reversed(out) if b["section"] == section and b["depth"] < depth), None)
            out.append({"line": n, "section": section, "text": line.strip(), "depth": depth, "parent": parent})
    return out


def sourced(b: dict | None) -> bool:
    """A bullet names its source, or sits under one that does (a list of orders under "(USAspending 2026-10-13):")."""
    return b is not None and (bool(SOURCE.search(b["text"])) or sourced(b["parent"]))


def sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.;])\s+(?=[A-Z(])", text)


def score(text: str) -> dict[str, dict]:
    """Each check's verdict and the bullets it rests on: `flagged` failed it, `seen` were judged and passed."""
    every = bullets(text)
    bs = [b for b in every if b["section"] != "TOP"]  # the question and title lines are not the answer
    nm = [b for b in bs if b["section"] == "NEXT MOVE"]
    nm_text = " ".join(b["text"] for b in nm)
    facts_dates = {d for b in bs if b["section"] not in ("NEXT MOVE", "INFERENCE") and SOURCE.search(b["text"])
                   for d in DATE.findall(b["text"])}
    r: dict[str, dict] = {}

    def put(name, seen, flagged, ok=None):
        r[name] = {"pass": (not flagged) if ok is None else ok, "flagged": flagged, "seen": seen}

    money = [b for b in bs if MONEY.search(b["text"]) and b["section"] not in ("INFERENCE", "NEXT MOVE")]  # restated there
    put("sourced_figures", money, [b for b in money if not sourced(b)])
    secs = {b["section"] for b in bs}
    put("facts_inference", [], [] if {"FACTS", "INFERENCE"} <= secs else [{"line": 0, "text": f"sections: {sorted(secs)}"}])
    urls = [b for b in bs if "http" in b["text"]]
    put("web_labelled", urls, [b for b in urls if b["section"] != "WEB"
                               and not re.search(r"read (?:on )?20\d\d|as of 20\d\d|agent\.", b["text"])])
    avail = [b for b in bs if AVAILABLE.search(b["text"])]
    put("no_available", avail, [b for b in avail if not AVAILABLE_OK.search(b["text"])])
    ceil = [{**b, "text": s} for b in bs for s in sentences(b["text"]) if "ceiling" in s.lower()]
    put("no_ceiling_funding", ceil, [b for b in ceil if CEILING_MONEY.search(b["text"]) and not CEILING_OK.search(b["text"])])
    dated = [{**b, "text": s} for b in nm for s in sentences(b["text"]) for d in DATE.findall(s) if d not in facts_dates]
    put("dates_honest", dated, [b for b in dated if not SUGGESTED.search(b["text"])])
    parts = {"contact": CONTACT.search(nm_text), "anchor": ANCHOR.search(nm_text), "date": DATE.search(nm_text)}
    put("next_move_parts", nm, [{"line": 0, "text": f"missing: {k}"} for k, v in parts.items() if not v])
    fap = {"fit": FIT.search(nm_text), "authority": AUTHORITY.search(nm_text), "path": PATH.search(nm_text)}
    put("fit_authority_path", nm, [{"line": 0, "text": f"missing: {k}"} for k, v in fap.items() if not v])
    inferred = [b for b in bs if b["section"] in ("INFERENCE", "NEXT MOVE") and INFERRED.search(b["text"])]
    put("verify_first", inferred, [] if not inferred or VERIFY.search(nm_text) else
        [{"line": 0, "text": "a reading is inferred, NEXT MOVE confirms nothing"}])
    gaps = [b for b in bs if GAP.search(b["text"])]
    put("no_bare_gaps", gaps, [b for b in gaps if not GAP_REASON.search(b["text"])])
    roles = [b for b in bs if ROLE.search(b["text"]) and not re.match(r"[A-Z ]{6,}\(", b["text"])]  # not a table heading
    put("role_basis", roles, [b for b in roles if not ROLE_BASIS.search(b["text"])])
    return r


def claims(text: str) -> list[dict]:
    """Contract figures as written: dollar amounts within 140 characters after an award id, in the same sentence, when
    the word before or after one says obligated or ceiling, or "of" (obligated of ceiling)."""
    out = []
    for b in bullets(text):
        ids = list(PIID.finditer(b["text"]))
        for i, m in enumerate(ids):
            end = re.compile(r"\.\s+[A-Z]").search(b["text"], m.end())  # a new sentence is about something else
            stop = min(ids[i + 1].start() if i + 1 < len(ids) else len(b["text"]), m.end() + 140,
                       end.start() if end else len(b["text"]))
            after_of = False
            for c in CLAIM.finditer(b["text"], m.end(), stop):
                word = (c.group(5) or c.group(1) or "").strip().lower()
                kind = "ceiling" if word == "ceiling" or after_of else "obligated" if word else None
                after_of = word == "of"
                if kind:
                    unit = UNIT[(c.group(4) or "").upper() or None]
                    out.append({"line": b["line"], "piid": m.group(), "kind": kind,
                                "value": float(c.group(2).replace(",", "")) * unit,
                                "tol": 0.5 * unit * 10 ** -len(c.group(3) or "")})
    return out


def verify(cs: list[dict], cache: dict) -> dict:
    sys.path.insert(0, str(HERE.parents[1] / ".claude" / "skills" / "pursuit"))
    import usaspending as u
    flagged, seen = [], []
    for c in cs:
        if c["piid"] not in cache:
            a = u.award(c["piid"])[0]
            cache[c["piid"]] = {"obligated": a.get("obligated"), "ceiling": a.get("ceiling"), "type": a.get("type")} if a.get("found") else None
        got = (cache[c["piid"]] or {}).get(c["kind"])
        if c["kind"] == "obligated" and VEHICLE.search((cache[c["piid"]] or {}).get("type") or ""):
            continue  # a vehicle obligates nothing itself; an answer's figure for it is its orders' total (`orders`)
        if got is None:
            continue
        row = {"line": c["line"], "text": f"{c['piid']} {c['kind']}: answer {c['value']:,.0f}, USAspending {got:,.0f}"}
        (seen if abs(got - c["value"]) <= c["tol"] + 0.01 * abs(got) else flagged).append(row)
    return {"pass": not flagged, "flagged": flagged, "seen": seen}


def sheet(qid: str, r: dict) -> str:
    out = [f"# {qid}", ""]
    for name, v in r.items():
        out.append(f"## {name}: {'PASS' if v['pass'] else 'FAIL'}")
        out += [f"- FLAG line {b['line']}: {b['text'][:300]}" for b in v["flagged"]]
        out += [f"- seen line {b['line']}: {b['text'][:160]}" for b in v["seen"][:6]]
        out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sets", nargs="+")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--sheets", type=Path, default=HERE.parents[1] / "build" / "evals" / "rubric")
    o = ap.parse_args(argv)
    cache_path = o.sheets / "usaspending_awards.json"
    cache = json.loads(cache_path.read_text()) if o.verify and cache_path.exists() else {}
    names = CHECKS + (["figures_match"] if o.verify else [])
    print("| set | answers | " + " | ".join(names) + " | all pass | mean checks passed |")
    print("|---|---:|" + "---:|" * (len(names) + 2))
    for s in o.sets:
        rows = {}
        for f in sorted((ANSWERS / s).glob("*.txt")):
            text = f.read_text()
            r = score(text)
            if o.verify:
                r["figures_match"] = verify(claims(text), cache)
            rows[f.stem] = r
            (o.sheets / s).mkdir(parents=True, exist_ok=True)
            (o.sheets / s / f"{f.stem}.md").write_text(sheet(f.stem, r))
        n = len(rows)
        per = [sum(r[c]["pass"] for r in rows.values()) for c in names]
        allp = sum(all(r[c]["pass"] for c in names) for r in rows.values())
        mean = sum(per) / (n * len(names)) if n else 0
        print(f"| {s} | {n} | " + " | ".join(f"{p}/{n}" for p in per) + f" | {allp}/{n} | {mean:.0%} |")
        (o.sheets / s / "verdicts.json").write_text(json.dumps(
            {q: {c: r[c]["pass"] for c in names} for q, r in rows.items()}, indent=1))
    if o.verify:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=1))
    return 0


def selfcheck() -> int:
    good = """FACTS:
- Line 024_000009571 CDM, FY2027 $331.0M request (agent.budget_lines, FY27 book).
- Order 70RTAC26FR0000045, $23.98M obligated, $33.12M ceiling, ends 2027-08-31 (USAspending 2026-10-13).
- Congress: no FY2027 figure, because FY2027 runs on a continuing resolution.
- Notice 70RTAC26R00000007 responses_due 2026-11-02 (agent.items).
WEB:
- Program page (https://example.gov/cdm, read 2026-10-13: "CDM")
INFERENCE: the program office is a potential budget holder (rule: line placed in the office; rests on the line).
NEXT MOVE: first step verify authority: the program office (cdm@example.gov), on notice 70RTAC26R00000007.
  Fit: the notice asks for dashboard sensors. Authority: unconfirmed: ask who owns the money.
  Path to payment: the open solicitation. Timing: deadline 2026-11-02; suggested by 2026-10-24 (time to ask).
"""
    bad = """FACTS:
- CDM has $331.0M.
- The vehicle has $500M of ceiling room available for this work.
- Budget: not in the record.
- The CIO is the decision maker.
NEXT MOVE: pitch the CIO by 2026-11-15.
"""
    g, b = score(good), score(bad)
    assert all(v["pass"] for v in g.values()), {k: v["flagged"] for k, v in g.items() if not v["pass"]}
    want_fail = {"sourced_figures", "facts_inference", "no_ceiling_funding", "dates_honest", "next_move_parts",
                 "fit_authority_path", "no_bare_gaps", "role_basis"}
    assert {k for k, v in b.items() if not v["pass"]} == want_fail, {k: v["pass"] for k, v in b.items()}
    assert score("FACTS:\n- $5M is still available on the line (agent.budget_lines).\nINFERENCE: x\n")["no_available"]["pass"] is False
    nested = score("FACTS:\n- Orders (USAspending 2026-10-13):\n  - Order A: $5.0M obligated.\n- $51.3M to ceiling is room, not money.\nINFERENCE: x\n")
    assert nested["sourced_figures"]["flagged"][0]["text"].startswith("- $51.3M") and nested["no_ceiling_funding"]["pass"], nested
    cs = claims(good)
    assert {(c["piid"], c["kind"], round(c["value"])) for c in cs} == {
        ("70RTAC26FR0000045", "obligated", 23980000), ("70RTAC26FR0000045", "ceiling", 33120000)}, cs
    cs = claims("FACTS:\n- IDIQ 70RTAC26D00000004 (ceiling $2.565B). It also went to Oracle (ceiling $567.9M).\n")
    assert [(c["kind"], round(c["value"])) for c in cs] == [("ceiling", 2565000000)], cs
    cs = claims("FACTS:\n- FA945320C0031 SLADES, ends 2028-06-29, obligated $30,416,427.06, ceiling $37,229,276.\n")
    assert [(c["kind"], round(c["value"])) for c in cs] == [("obligated", 30416427), ("ceiling", 37229276)], cs
    print(f"rubric selfcheck ok ({len(CHECKS)} checks; good answer passes all, bad fails {len(want_fail)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(selfcheck() if sys.argv[1:] == ["--selfcheck"] else main(sys.argv[1:]))

"""Mechanical checks on a startup intelligence brief: table shape, each row's exact quote against the saved result its
Source cell names (its id in OUT/sources/index.jsonl, written by save_source.py, or a record file), failed fetches listed as
gaps, record identifiers against the record (report.py's resolver), the published ranking rule, the fit and lead
order lines, banned words, people readings and the tally.

usage: AGENCY=<key> python research/workflow/check.py BRIEF OUT/sources [RECORD_DIR ...]
Prints JSON: {"ok": bool, "problems": [...], "tally": "...", "quotes": {...}, "identifiers": {...}, "gaps": [...]}
and exits 1 unless ok: any problem, quote failure or unresolved identifier blocks the brief."""
import html
import json
import os
import re
import sys
from urllib.parse import urlparse

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")

PROV = {"repo", "tool", "web", "profile"}
CLASS = {"primary", "secondary"}
STATUS = {"Live", "Historical", "Adjacent"}
TIER = {"lead-eligible": 0, "conditional": 1, "route-only": 2, "historical": 3, "ruled-out": 4, "gated-out": 4}
GROUP_WORDS = [("instrument", 0), ("route", 1), ("live signal", 2), ("signal", 2), ("historical", 3), ("ruled out", 4), ("gated", 4)]
BANNED = re.compile(r"\b(likely|probably|imminent|expected soon|will release|rfp coming)\b", re.I)
CLAIMS = re.compile(r"every fact (?:is )?from", re.I)
GATE_CODE = re.compile(r"\bG[1-4]\b")
# a record shows a role; a reading of budget authority, decision or advocacy is only ever "potential", with its reasons
ROLE_WORDS = re.compile(r"\b(decision[- ]makers?|budget holders?|champions?)\b", re.I)
READINGS = ("potential budget holder", "potential decision maker", "potential champion")
P0_HEADER = "| Field | Value | Identifier | Passage (exact quote) | Source | Class | Provenance |"
NO_PASSAGE = ("not in file", "not collected")
TEXT_FILES = (".txt", ".md", ".json", ".html", ".tsv", ".csv")
# ponytail: personal webmail domains only; a notice's published contact is allowed, so phones are left to the validators
PERSONAL = re.compile(r"\b[\w.+-]+@(?:gmail|yahoo|outlook|hotmail|icloud|proton)(?:mail)?\.\w+\b", re.I)
SECTIONS = [
    "Sources", "Bottom line", "What rules a route in or out", "Ranked opportunities", "P0 fields", "Agencies and offices",
    "Programs and requirements", "Budget lines behind the work", "What Congress has directed",
    "What leaders have said", "Oversight, Federal Register and news", "Solicitations, BAAs and planning notices",
    "How long from solicitation to award", "Awards and incumbents", "How its contracting office buys",
    "Contracts in this line ending soon", "What the winning companies did in the last two years", "SBIR and STTR",
    "People and program offices", "Where the office sits", "What changed in the last year",
    "Next actions from the record", "Teaming and access routes", "Past ", "Company capability and risk signals",
    "Bid protests", "Small business routes", "Brief for the first meeting", "Outreach drafts", "Still open",
    "Audit", "How this was produced",
]
SOURCE_ID = re.compile(r"\[(S\d+)\]")
QUOTE = re.compile(r'"([^"]{12,}?)"|“([^”]{12,}?)”')
TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                       "‑": "-", " ": " ", " ": " ", "*": None, "`": None})


def norm(s):
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace("\\n", " ").replace("\\t", " ").replace('\\"', '"').replace("\\/", "/")
    s = html.unescape(s).translate(TRANS)
    return re.sub(r"\s+", " ", s).strip().lower()


def source_key(s):
    """A url or command as the index and a Source cell both write it: no scheme, environment, interpreter, script
    directory or redirect ("AGENCY=navy .venv/bin/python research/tools/navy.py office X > f" -> "navy.py office x")."""
    s = norm(s)
    s = re.sub(r"https?://(?:www\.)?", "", s)
    s = re.sub(r"(?:^|\s)[a-z_]+=\S+", " ", s)
    s = re.sub(r"\S*python[\d.]*\s", " ", s)
    s = re.sub(r"[\w./-]*/(?=[\w-]+\.py\b)", "", s)
    s = re.sub(r"\s(?:>|2>|&>)\s*\S+", " ", s)
    return re.sub(r"\s+", " ", s).strip().rstrip("/")


def load(src_dir, record_dirs=()):
    """The saved sources: every text file under the run's sources and the record dirs, normalized, by path; and the
    run's index (save_source.py), which maps each url or command to the file holding its text, or to a failed fetch."""
    texts = {}
    for d in [src_dir, *record_dirs]:
        for root, _, files in os.walk(d, followlinks=True):
            for f in files:
                if f.endswith(TEXT_FILES) and not f.startswith("brief"):
                    path = os.path.normpath(os.path.join(root, f))
                    with open(path, errors="replace") as fh:
                        texts[path] = norm(fh.read())
    index, path = [], os.path.join(src_dir, "index.jsonl")
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    e = json.loads(line)
                    e.setdefault("id", f"S{len(index) + 1}")  # an index written before ids: the same order save_source.py numbers
                    e["path"] = os.path.normpath(os.path.join(src_dir, e["file"])) if e.get("file") else None
                    e["key"] = source_key(e["source"])
                    index.append(e)
    return {"src": os.path.normpath(src_dir), "records": [os.path.normpath(d) for d in record_dirs],
            "texts": texts, "index": index}


def resolve(cell, S):
    """The saved results a Source cell names, matched exactly, never by a shared prefix: each "[S12]" id is that one
    result (any url or command written after the id must be the one saved under it), each other part of the cell,
    split on ";", is a url or command whose key equals an indexed one, and record files are cited by path. A file under
    the run's sources counts only when indexed, so an agent's own notes never stand in for a source."""
    hits, by_id = [], {e["id"]: e for e in S["index"]}
    for part in cell.split(";"):
        ids, rest = SOURCE_ID.findall(part), source_key(SOURCE_ID.sub(" ", part))
        if ids:
            hits += [by_id[i] for i in ids if i in by_id and (not rest or by_id[i]["key"] == rest)]
        else:
            hits += [e for e in S["index"] if rest and e["key"] == rest]
    indexed = {e["path"] for e in S["index"] if e["status"] == "ok"}
    for tok in re.findall(r"[\w./-]+\.(?:txt|md|json|html|tsv|csv)\b", cell):
        for path in [os.path.normpath(os.path.join(base, tok)) for base in ("", S["src"], *S["records"])]:
            if path in S["texts"] and (path in indexed or not path.startswith(S["src"] + os.sep)):
                hits.append({"source": tok, "key": tok, "path": path, "status": "ok", "reason": ""})
                break
    return hits


def gaps(S):
    """Failed fetches that no later save of the same source replaced."""
    good = {e["key"] for e in S["index"] if e["status"] == "ok"}
    return [e for e in S["index"] if e["status"] == "failed" and e["key"] not in good]


def frags(q):
    for frag in re.split(r"\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*", q):
        frag = norm(frag).strip(" .,;:")
        if len(frag) >= 12:
            yield frag


def source_row(i, passage, source, prov, last, S, quotes, bad):
    """Tie a row's passage to the source its Source cell names ("same" repeats the row above in the table). A quote
    found only in another file fails, with where it was found. Returns the resolution for the next row."""
    res = last if source.strip().lower().rstrip(".") in ("same", "same as above") and last else resolve(source, S)
    if not res:
        bad("source", i, f"Source '{source[:80]}' names nothing saved: cite the id save_source.py printed for the result "
                         "([S12]), the url or command exactly as saved, or the record file")
        return res
    ok = [e for e in res if e["status"] == "ok" and e["path"] in S["texts"]]
    if not ok:
        bad("gap", i, f"rests on a failed fetch of '{res[0]['source'][:80]}' ({res[0]['reason']}): list it in Still open "
                      "as not collected and drop the row")
        return res
    found_any = False
    for m in QUOTE.finditer(passage):
        found_any = True
        for frag in frags(m.group(1) or m.group(2)):
            quotes["checked"] += 1
            if not any(frag in S["texts"][e["path"]] for e in ok):
                elsewhere = next((p for p, t in S["texts"].items() if frag in t), None)
                quotes["failures"].append({"line": i + 1, "provenance": prov, "quote": frag[:160],
                                           "source": source[:120], "found_in": elsewhere})
    if not found_any and not passage.lower().startswith(NO_PASSAGE):
        bad("quote", i, "no exact passage in double quotes: each row carries the source's own words")
    return res


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def unquoted(line):
    return QUOTE.sub(" ", line)


def check(text, S, resolver=None):
    lines = text.split("\n")
    problems = []

    def bad(kind, i, msg):
        problems.append({"check": kind, "line": i + 1, "text": msg[:300]})

    heads = [(i, l[3:].strip()) for i, l in enumerate(lines) if l.startswith("## ")]
    pos = -1
    for want in SECTIONS:
        hit = next((i for i, h in heads if h.startswith(want) and i > pos), None)
        if hit is None:
            bad("section", pos, f"missing or out of order: '## {want}'")
        else:
            pos = hit

    fit = next((i for i, l in enumerate(lines) if l.startswith("**Fit:**")), None)
    if fit is None:
        bad("fit", 0, "no '**Fit:**' line in the Bottom line")
    elif len(lines[fit].split()) < 20 or re.match(r"\*\*Fit:\*\*\s*(yes|no)\W*$", lines[fit], re.I):
        bad("fit", fit, "fit line is a bare verdict; say what fits, what does not and what the agency does not buy")

    rows, tally_rows, evidence, not_in_file, quotes = [], [], [], 0, {"checked": 0, "failures": []}
    in_ranked, group, ranked, header, section, last, readings, where = False, None, [], "", "", None, [], {}
    for i, l in enumerate(lines):
        if l.startswith("## "):
            section = l[3:].strip()
            in_ranked = section.startswith("Ranked opportunities")
        if in_ranked and l.startswith("**") and not l.startswith("| "):
            low = l.strip("* ").lower()
            group = next((g for w, g in GROUP_WORDS if low.startswith(w)), group)
        if not l.startswith("|") or set(l) <= set("|-: "):
            continue
        c = cells(l)
        if i + 1 < len(lines) and lines[i + 1].startswith("|---"):
            header, last = l.strip(), None
            if header.startswith("| Field |") and header != P0_HEADER:
                bad("row", i, "P0 fields need the passage column: " + P0_HEADER)
            continue
        not_in_file += sum(1 for x in c if x.lower().startswith("not in file"))
        for x in c:
            if x == "":
                bad("row", i, "empty cell; write 'not in file' when the primary source lacks the fact")
                break
        if in_ranked and len(c) == 12 and c[0].isdigit():
            ranked.append((i, group, c))
            continue
        hc = cells(header)
        if hc[0] == "Person":
            row = dict(zip(hc, c))
            readings.append((i, row))
            role = next((v for k, v in row.items() if k.startswith("Documented role")), "")
            last = source_row(i, role, row.get("Source", ""), "people", last, S, quotes, bad)
            continue
        p0 = header == P0_HEADER
        if (len(c) == 6 or p0 and len(c) == 7) and c[-1] in PROV and c[-2] in CLASS:
            ident = (c[2] if header.startswith("| Field |") else c[0]).strip("` ")
            tally_rows.append(c)
            evidence.append((i, ident, c[-1]))
            where.setdefault(ident, set()).add(section)
            if len(c) == 6 and c[2] in STATUS:
                rows.append((i, c))
            last = source_row(i, c[3] if p0 else c[1], c[-3], c[-1], last, S, quotes, bad)
        elif header.startswith(("| Identifier |", "| Field |")):
            bad("row", i, f"evidence row needs {len(hc)} cells with Class in {sorted(CLASS)} and Provenance in "
                          f"{sorted(PROV)}; got {len(c)} cells, '{c[-2]}', '{c[-1]}'")

    for i, row in readings:
        check_reading(i, row, where, bad)

    still = norm("\n".join(section_lines(lines, "Still open")))
    open_gaps = gaps(S)
    for e in open_gaps:
        first = e["key"].split()[0]
        host = urlparse("//" + first).hostname if "." in first and not first.endswith(".py") else None  # a tool run has no host
        named = re.search(re.escape(e["key"]) + r"(?![\w.-])", still) or f"[{e['id'].lower()}]" in still
        if not named and not (host and host in still):
            bad("gap", 0, f"failed fetch of '{e['source'][:100]}' ({e['reason']}) is not in Still open as not collected")

    for i, l in enumerate(lines):
        plain = unquoted(l)
        if "—" in plain:
            bad("style", i, "em dash outside a quote")
        m = BANNED.search(plain)
        if m:
            bad("style", i, f"banned word '{m.group(0)}' outside a quote")
        m = CLAIMS.search(plain)
        if m:
            bad("style", i, f"corpus claim '{m.group(0)}'; list the sources instead")
        if GATE_CODE.search(plain):
            bad("style", i, "gate code (G1, G2); say the test in plain words")
        for m in ROLE_WORDS.finditer(plain):
            if "potential" not in plain[max(0, m.start() - 60):m.start()].lower():
                bad("people", i, f"'{m.group(0)}' without 'potential': a record shows a role, never budget authority, "
                                 "a decision or advocacy; give the reading in People and program offices")
        m = PERSONAL.search(l)
        if m:
            bad("people", i, f"personal contact '{m.group(0)}'; only official mailboxes and contacts a notice publishes")

    check_ranked(ranked, lines, bad)

    prov = {p: sum(1 for c in tally_rows if c[-1] == p) for p in sorted(PROV)}
    st = {s: sum(1 for _, c in rows if c[2] == s) for s in sorted(STATUS)}
    cl = {k: sum(1 for c in tally_rows if c[-2] == k) for k in sorted(CLASS)}
    tally = (f"Tally: cells 'not in file' {not_in_file}; evidence rows {len(tally_rows)}; "
             + "; ".join(f"rows {s} {n}" for s, n in st.items() if n) + "; "
             + "; ".join(f"rows from {p} {n}" for p, n in prov.items() if n) + "; "
             + "; ".join(f"rows on a {k} source {n}" for k, n in cl.items() if n))
    last = [l for l in lines if l.startswith("Tally:")]
    if not last or last[-1].strip() != tally:
        bad("tally", 0, "the last Tally line in Audit differs from the count; paste: " + tally)
    result = {"problems": problems, "tally": tally,
              "quotes": {"checked": quotes["checked"], "not_found": len(quotes["failures"]),
                         "failures": quotes["failures"]},
              "identifiers": identifiers(evidence, resolver, S["texts"].values()),
              "gaps": [{"source": e["source"], "reason": e["reason"]} for e in open_gaps]}
    result["ok"] = not blocking(result)
    return result


def blocking(result):
    """Anything the brief cannot be released with."""
    return bool(result["problems"] or result["quotes"]["not_found"] or result["identifiers"]["unresolved"])


def section_lines(lines, title):
    start = next((i for i, l in enumerate(lines) if l.startswith("## " + title)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start + 1:end]


def check_reading(i, row, where, bad):
    """A potential budget holder, decision maker or champion rests on more than a title: a budget holder on a budget
    line placed in the person's office, a decision maker or champion on an action or statement outside People and
    program offices. Each says what is still to confirm. "Rests on" lists row identifiers, separated by semicolons."""
    person = row.get("Person", "").strip("* ")
    reading = row.get("Reading", "potential champion").strip().lower()
    if reading not in READINGS:
        bad("people", i, f"{person}: reading '{reading}' is not one of {', '.join(READINGS)}")
        return
    rests = [x.strip("` ") for x in row.get("Rests on", "").split(";") if x.strip()]
    missing = [x for x in rests if x not in where]
    if missing:
        bad("people", i, f"{person}: Rests on names rows the brief does not carry: {'; '.join(missing)[:200]}")
    sections = {s for x in rests if x in where and x != person for s in where[x]}
    if reading == "potential budget holder":
        if not any(s.startswith("Budget lines behind the work") for s in sections):
            bad("people", i, f"{person}: a budget holder reading needs a budget line placed in the person's office, a "
                             "row in Budget lines behind the work; a title alone is not budget authority")
    elif not any(not s.startswith("People and program offices") for s in sections):
        bad("people", i, f"{person}: a {reading} reading needs an action or statement outside People and program "
                         "offices; a title alone is not enough")
    if row.get("Still to confirm", "").strip().lower().rstrip(".") in ("", "none", "nothing", "n/a", "-"):
        bad("people", i, f"{person}: say what still needs confirmation")


def record_resolver():
    """report.py's resolver for the AGENCY in the environment, or None when the record's tools cannot load here."""
    if not os.environ.get("AGENCY"):
        return None
    sys.path.insert(0, TOOLS)
    try:
        import report
    except (ImportError, SystemExit):
        return None
    return report.resolve_any


def bare(ident):
    """The identifier without what a brief writes around it: a kind word before it, a description after a comma or
    in brackets ("PE 0603468E ACX-01, LASSO, FY 2027" -> "0603468E ACX-01")."""
    head = re.split(r"\s+\(|,\s|;\s", ident, maxsplit=1)[0].strip()
    return re.sub(r"^(PE|PIID|notice|contract|award|program element)\s+", "", head, flags=re.I)


def identifiers(evidence, resolver, texts=()):
    """Each tool or repo row's identifier must name a record, office, person, vendor or budget line in the record
    (report.py's resolver), or appear word for word in the record files and sources read (an observation id, a
    report number). A typo or an invented identifier does neither."""
    if resolver is None:
        return {"checked": 0, "unresolved": [], "note": "not checked: set AGENCY so the record's tools load"}
    checked, unresolved = 0, []
    for i, ident, prov in evidence:
        ident = ident.strip("` ")
        if prov not in ("tool", "repo") or not ident or ident.lower().startswith("not in file"):
            continue
        checked += 1
        short = bare(ident) or ident
        head = norm(short.split()[0])
        if any(norm(short) in t or len(head) >= 6 and head in t for t in texts):
            continue
        try:
            kind = resolver(ident) or resolver(short)
        except Exception as e:  # a record file this layer lacks: the row cannot be resolved, say why
            unresolved.append({"line": i + 1, "identifier": ident[:120], "why": f"{type(e).__name__}: {e}"[:160]})
            continue
        if not kind:
            unresolved.append({"line": i + 1, "identifier": ident[:120]})
    return {"checked": checked, "unresolved": unresolved}


def check_ranked(ranked, lines, bad):
    if not ranked:
        bad("rank", 0, "no ranked tables under '## Ranked opportunities'")
        return
    ids, seen_groups, prev = set(), [], None
    n = 0
    for i, g, c in ranked:
        n += 1
        if c[0] != str(n):
            bad("rank", i, f"rank {c[0]} out of sequence; expected {n}")
        if c[1] in ids:
            bad("rank", i, f"candidate {c[1]} listed twice")
        ids.add(c[1])
        if g is None:
            bad("rank", i, "table not under a group title (Instruments, Routes, Live signals, Historical, Ruled out)")
            continue
        try:
            f, a, u = (int(x.split("/")[0]) for x in c[6:9])
            score = float(c[9])
        except ValueError:
            bad("rank", i, "scores must read 'n/3' and the score a number")
            continue
        want = round(40 * f / 3 + 35 * a / 3 + 25 * u / 3, 1)
        if abs(want - score) > 0.051:
            bad("rank", i, f"{c[1]} score {score} but the rule gives {want}")
        if c[2] not in TIER:
            bad("rank", i, f"{c[1]} tier '{c[2]}' is not one of {list(TIER)}")
            continue
        if g == 0 and c[4] != "Live":
            bad("rank", i, f"{c[1]} is an instrument that is not Live; only an open instrument can lead")
        if g == 3 and c[4] != "Historical":
            bad("rank", i, f"{c[1]} sits under Historical with status {c[4]}")
        if TIER[c[2]] == 4 and g != 4:
            bad("rank", i, f"{c[1]} is ruled out but sits outside the Ruled out group")
        key = (g, TIER[c[2]], -score, 0 if "primary" in c[11] else 1, int(re.sub(r"\D", "", c[1]) or 0))
        if prev and key < prev:
            bad("rank", i, f"{c[1]} breaks the published order (group, tier, score, source class, id)")
        prev = key
        if not seen_groups or seen_groups[-1] != g:
            seen_groups.append(g)

    # rule 4: instruments of distinct offices first, then the first route of each office not yet named
    pool = [c for _, g, c in ranked if g == 0] + [c for _, g, c in ranked if g == 1]
    order, offices = [], []
    for c in pool:
        office = c[3].split(":")[0].strip()
        if office not in offices:
            offices.append(office)
            order.append(c[10])
        if len(order) == 3:
            break
    lo = next((i for i, l in enumerate(lines) if l.startswith("**Lead order:**")), None)
    if lo is None:
        bad("lead", 0, "no '**Lead order:**' line in the Bottom line")
        return
    at = [lines[lo].find(x) for x in order]
    if any(p < 0 for p in at) or at != sorted(at):
        bad("lead", lo, "Lead order line must name, in this order, the rule's lead, second and third: " + ", ".join(order))


if __name__ == "__main__":
    with open(sys.argv[1]) as fh:
        out = check(fh.read(), load(sys.argv[2], sys.argv[3:]), record_resolver())
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["ok"] else 1)

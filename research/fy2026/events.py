#!/usr/bin/env python3
"""FY2026 lifecycle reconstruction: tie every collected FY2026 event to the requirements in the candidate universe
and write a dated status per requirement. Reads the manifest, the saved notices, awards and incumbent records,
and research/tools/trace.py's matching helpers. Writes only research/fy2026/events.csv, status_fy2026.csv and
sweep_summary.json.

Labels: documented (a saved document states the tie), inferred (follows by a stated rule), candidate (a shared
program name; a reviewer decides), related (same program, different lot or generation; context only).
A forecast line's status is never read from a candidate. A negative names the searches it rests on.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
import trace as tr  # noqa: E402
from fetch import kept_page  # noqa: E402
from lrae_package import contract_tokens  # noqa: E402

COLLECT = HERE / "collect"
AS_OF = date(2026, 9, 22)
AS_OF_S = AS_OF.isoformat()
FY_START = "2025-10-01"
FY_END = "2026-09-30"
NAVWAR_ORG = "100076586"

STAGE_OF = {"Sources Sought": "rfi_sources_sought", "Presolicitation": "presolicitation", "Solicitation": "solicitation",
            "Combined Synopsis/Solicitation": "solicitation", "Special Notice": "special_notice", "Award Notice": "award_notice",
            "Justification": "justification", "Justification (J&A)": "justification", "Intent to Bundle": "special_notice"}
EXTEND_RE = re.compile(r"(?i)\boption|extend|extension|period of performance|\bpop\b|bridge|ceiling increase|increase (the )?ceiling")


def compact(s: str) -> str:
    return re.sub(r"[\s\-/]", "", (s or "").upper())


# ---------------------------------------------------------------- inputs

def manifest_rows() -> list[dict]:
    return tr.manifest()


def refreshed_details(rows: list[dict]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for r in rows:
        if kept_page(r) and "SAM notice detail refreshed" in (r.get("note") or ""):
            m = re.search(r"/opportunities/([0-9a-f]{32})", r["url"])
            if m:
                out[m.group(1)] = ROOT / r["path"]
    return out


def parse_detail(notice_id: str, path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    o = d.get("data2") or {}
    text = ""
    for candidate in re.findall(r'"((?:[^"\\]|\\.){300,})"', json.dumps(d)):
        text = max(text, candidate.encode().decode("unicode_escape", "ignore"), key=len)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
    return {"id": notice_id, "title": o.get("title") or "", "solicitation": o.get("solicitationNumber") or "",
            "type": tr.NOTICE_TYPE.get(str(o.get("type") or ""), str(o.get("type") or "")), "posted": str(d.get("postedDate") or "")[:10],
            "modified": str(d.get("modifiedDate") or "")[:10], "award": o.get("award") or {}, "cancelled": bool(d.get("cancelled")),
            "response": str(((o.get("solicitation") or {}).get("deadlines") or {}).get("response") or "")[:10],
            "text": text, "path": str(path.relative_to(ROOT)), "organization_id": str(o.get("organizationId") or "")}


def attachment_text(notice_id: str) -> str:
    """Text of the saved attachments (PDF, DOCX, TXT) under data/raw/sam_notices/<id>/, where the J&A or synopsis names
    the incumbent contract and the program. Capped so one large attachment cannot dominate."""
    import sam_notices  # noqa: E402  (research/tools/sam_notices.py, text_of only)
    folder = tr.NOTICES / notice_id
    if not folder.is_dir():
        return ""
    parts = []
    for f in sorted(folder.iterdir()):
        if f.name.startswith("._") or not f.suffix.lower() in (".pdf", ".docx", ".txt"):
            continue
        try:
            parts.append(sam_notices.text_of(f.name, f.read_bytes())[:60000])
        except Exception:  # noqa: BLE001
            continue
    return re.sub(r"\s+", " ", " ".join(parts))


def detail_of(notice_id: str, refreshed: dict[str, Path]) -> dict | None:
    if notice_id in refreshed:
        d = parse_detail(notice_id, refreshed[notice_id])
    else:
        p = tr.NOTICES / f"{notice_id}.json"
        d = parse_detail(notice_id, p) if p.exists() else None
    if d is None:
        return None
    awarded = (d.get("award") or {}).get("number") or ""
    extra = attachment_text(notice_id)
    d["attachment_chars"] = len(extra)
    d["text"] = " ".join(x for x in (d["text"], f"awarded contract {awarded}" if awarded else "", extra) if x)
    return d


def fpds_office_actions(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Every FY2026 NAVWAR HQ action in the saved monthly office scans, deduplicated, with the windows read."""
    seen, out, windows = set(), [], set()
    for r in rows:
        u = r.get("url", "")
        m = re.search(r"CONTRACTING_OFFICE_ID:N00039\+SIGNED_DATE:%5B([\d/]+),([\d/]+)%5D&start=", u)
        if not m or not kept_page(r) or not (ROOT / r["path"]).exists():
            continue
        start, end = m.group(1).replace("/", "-"), m.group(2).replace("/", "-")
        if end < FY_START or start > FY_END:
            continue
        windows.add(f"{start}..{end} (retrieved {r['retrieved_at'][:10]})")
        for a in tr.fpds_actions((ROOT / r["path"]).read_text(encoding="utf-8", errors="replace")):
            k = (a["piid"], a["mod"], a["signed"], a["idv"])
            if k in seen:
                continue
            seen.add(k)
            out.append(a)
    return out, sorted(windows)


def load_json(name: str) -> dict:
    p = COLLECT / name
    return json.loads(p.read_text()) if p.exists() else {}


# ---------------------------------------------------------------- ties

TITLE_NOISE = re.compile(r"(?i)\(c\)|follow[- ]?on|re-?compete|contract|new award|fy\d{2}|\bnew\b|\bcontracts?\b|production|procurement")
NAME_STOP = {"navy", "naval", "support", "services", "program", "system", "systems", "engineering", "training", "integration", "production",
             "contract", "follow", "phase", "block", "order", "task", "install", "installation", "spares", "repair", "upgrade", "sustainment"}


def title_core(title: str) -> str:
    """The forecast title with procurement boilerplate removed, compacted, for containment tests."""
    return compact(TITLE_NOISE.sub(" ", title or ""))


def proper_names(text: str) -> set[str]:
    """Capitalised words of five letters or more that are not procurement vocabulary: countries, programs written as words."""
    return {w.lower() for w in re.findall(r"\b[A-Z][a-z]{4,}\b", text or "") if w.lower() not in NAME_STOP and w.lower() not in tr.STOP}


def notice_offices_of(det: dict) -> set[str]:
    return set(offices_in(det["text"]))


@lru_cache(maxsize=None)
def offices_in(text: str) -> frozenset[str]:
    """The current program offices a notice names; read once per notice, not once per forecast line it is tried against."""
    return frozenset(o["office"] for o in tr.resolve_offices(text) if not o["former"] and o["office"].startswith(("pmw:", "peo:", "drpm:", "pms:")))


def office_ok(line: dict, offices: set[str], ctx: dict) -> bool | None:
    """True: the notice names this line's office or its parent; False: it names an unrelated current office; None: names none."""
    if not offices:
        return None
    return any(tr.related_office(line["office_id"], o, ctx["parents"]) for o in offices)


def tie_notice_to_line(det: dict, line: dict, ctx: dict) -> dict | None:
    """Strongest tie between a notice and a forecast line, or None.

    documented: the notice carries the line's PID, or the forecast title carries the notice's solicitation number.
    inferred: the notice text cites an incumbent contract only this line cites; or the notice title restates the
      forecast title (its compacted core is contained, or two rare program codes are shared) and the notice names
      the line's office or its parent and no other latest-release line shares the same codes.
    candidate: a shared rare program code, or a shared proper name (a country, a program written as a word).
    related: same program, different lot or generation.
    """
    key = line["pid"] or line["record_key"]
    hay = compact(det["title"] + " " + det["text"])
    if line["pid"] and compact(line["pid"]) in hay:
        return {"label": "documented", "basis": f"forecast PID {line['pid']} appears in the notice"}
    if det["solicitation"] and len(compact(det["solicitation"])) >= 8 and compact(det["solicitation"]) in compact(line["requirement_title"]):
        return {"label": "documented", "basis": f"solicitation number {det['solicitation']} appears in the forecast title"}
    piids = set(contract_tokens(det["text"])) & set(contract_tokens(line["existing_contract_number"]))
    if piids:
        others = sorted({k for t in piids for k in ctx["incumbent_lines"].get(t, [])} - {key})
        # a vehicle (SeaPort IDV) is shared by many lines and identifies none of them
        vehicle = all(t.startswith("N00178") for t in piids)
        if others or vehicle:
            return {"label": "candidate", "basis": f"incumbent contract {', '.join(sorted(piids))} in the notice text, also cited by "
                                                    f"{len(others)} other line(s)" + (" (a shared vehicle)" if vehicle else "")}
        return {"label": "inferred", "basis": f"incumbent contract {', '.join(sorted(piids))} in the notice text, cited by this line alone"}
    offices = notice_offices_of(det)
    ok = office_ok(line, offices, ctx)
    if ok is False:
        return None  # the notice names a current office unrelated to this line
    note = f"; notice names {', '.join(sorted(offices))}" if offices else "; notice names no office the memory knows"
    core = title_core(line["requirement_title"])
    codes = tr.distinctive_tokens(line["requirement_title"])
    rare = {t for t in codes if ctx["rarity"].get(t, 0) <= 3}
    ncodes = tr.distinctive_tokens(det["title"])
    shared_rare = sorted(rare & ncodes)
    same_gen = tr.relation_of(line["requirement_title"], det["title"]) != "related"
    ncore = title_core(det["title"])
    if len(ncore) >= 12 and ncore in compact(line["requirement_title"]) and same_gen:
        label = "inferred" if ok else "candidate"
        return {"label": label, "basis": f"notice title ('{det['title'][:60]}') is contained in the forecast title"
                                          + ("; office confirmed" if ok else "; office not confirmed by the notice text") + note}
    if len(core) >= 10 and core in compact(det["title"]) and same_gen:
        label = "inferred" if ok else "candidate"
        return {"label": label, "basis": f"notice title restates the forecast title ('{TITLE_NOISE.sub(' ', line['requirement_title']).strip()[:60]}')"
                                          + ("; office confirmed" if ok else "; office not confirmed by the notice text") + note}
    if len(shared_rare) >= 2 and same_gen:
        others = [l for l in ctx["chains"] if l != ctx["canon"].get((line["release"], line["record_key"]), line["record_key"])
                  and set(shared_rare) <= tr.distinctive_tokens(ctx["chains"][l][-1]["requirement_title"]) and ctx["chains"][l][-1]["release"] in ctx["current"]]
        if ok and not others:
            return {"label": "inferred", "basis": f"two rare program codes shared ({', '.join(shared_rare)}), the notice names the line's office, and no other "
                                                   f"latest-release line carries both" + note}
    m = tr.line_match(det, line["requirement_title"], [line], ctx)
    if m:
        label = "related" if m["relation"] == "related" else "candidate"
        return {"label": label, "basis": m["basis"] + note}
    names = proper_names(line["requirement_title"]) & proper_names(det["title"])
    if names:
        sharers = sum(1 for c in ctx["chains"].values() if c[-1]["release"] in ctx["current"] and names & proper_names(c[-1]["requirement_title"]))
        return {"label": "candidate", "basis": f"shared name {', '.join(sorted(names))} ({sharers} latest-release lines carry it)" + note}
    return None


def tie_award_to_line(a: dict, line: dict, tied_sols: set[str], ctx: dict) -> dict | None:
    sols_in_title = {compact(m) for m in tr.SOL_RE.findall(line["requirement_title"].replace(" ", ""))}
    sol = compact(a.get("solicitation") or "")
    if sol and sol in sols_in_title:
        return {"label": "documented", "basis": f"solicitation {a['solicitation']} on the award is in the forecast title"}
    if sol and sol in tied_sols:
        return {"label": "documented", "basis": f"solicitation {a['solicitation']} on the award is that of a notice documented to this line"}
    codes = {t for t in tr.distinctive_tokens(line["requirement_title"]) if ctx["rarity"].get(t, 0) <= 3}
    desc = a.get("description", "") or ""
    shared = codes & tr.distinctive_tokens(desc)
    inc = set(contract_tokens(line["existing_contract_number"]))
    core = title_core(line["requirement_title"])
    under_vehicle = a.get("idv") in inc or a.get("piid") in inc
    if len(core) >= 10 and core in compact(desc):
        return {"label": "inferred" if under_vehicle else "candidate",
                "basis": f"award description restates the forecast title" + (f" and the award is under the line's incumbent vehicle {a.get('idv')}" if under_vehicle else "")}
    if shared and under_vehicle and re.search(r"(?i)order|bpa|call", line.get("procurement_instrument", "") + " " + line["requirement_title"]):
        return {"label": "inferred", "basis": f"order under the line's incumbent vehicle {a.get('idv') or a.get('piid')}, the forecast states an order instrument, "
                                               f"and the description shares {', '.join(sorted(shared))}"}
    if shared and under_vehicle:
        return {"label": "candidate", "basis": f"action under the line's incumbent vehicle {a.get('idv') or a.get('piid')} sharing {', '.join(sorted(shared))}; "
                                                "the forecast does not state an order instrument, so this may be routine ordering"}
    if shared:
        return {"label": "candidate", "basis": f"award description shares program token(s) {', '.join(sorted(shared))}"}
    return None


# ---------------------------------------------------------------- status

NEWS = ROOT / "research" / "events" / "news_observations.json"
NEWS_TYPE = {"leadership": "news_leadership", "parentage": "news_organizational_change",
             "consolidation": "news_organizational_change", "naming": "news_organizational_change",
             "industry_engagement": "news_industry_engagement", "acquisition_strategy": "news_acquisition_strategy",
             "performance": "news_performance", "milestone": "news_milestone"}


def news_articles() -> list[dict]:
    """The modelled articles, each already carrying the records the article layer linked it to."""
    if not NEWS.exists():
        return []
    articles = json.loads(NEWS.read_text(encoding="utf-8")).get("articles") or []
    return list(articles.values()) if isinstance(articles, dict) else articles


def news_tie(article: dict, key: str, line: dict | None, tied: set[str]) -> dict:
    """How firmly an article that already links a requirement is about that requirement.

    The link itself is the article layer's, made when the article was modelled; this only reads how strongly
    the sentences support it, and picks the sentence to quote. An article never sets a status: a procurement
    fact comes from a notice or an award, and a claim is listed beside the reading, never inside it.
    """
    pid = compact(key)
    incumbent = {compact(c) for c in contract_tokens((line or {}).get("existing_contract_number", ""))}
    best = {"label": "candidate", "basis": "the article layer links this requirement", "claim": None, "n": 0}
    for i, c in enumerate(article.get("claims") or [], 1):
        passage = compact(c.get("passage") or "")
        sols = {compact(s) for s in c.get("solicitations") or []}
        if (len(pid) >= 8 and pid in passage) or (sols & tied):
            return {"label": "documented", "basis": "a passage names this requirement's own number", "claim": c, "n": i}
        named = {compact(x) for x in c.get("contracts") or []} & incumbent
        if named and best["label"] != "inferred":
            best = {"label": "inferred", "basis": f"a passage names the incumbent contract {sorted(named)[0]} the forecast carries",
                    "claim": c, "n": i}
    if best["claim"] is None:
        claims = article.get("claims") or []
        best["claim"], best["n"] = (claims[0], 1) if claims else ({}, 0)
    return best


def window_end(fy: str, q: str) -> date | None:
    d = tr.quarter_dates(fy, q)
    if d:
        return d[1]
    y = tr.fiscal_year(fy)
    return date(y, 9, 30) if y else None


def status_for(events: list[dict], line: dict | None, kind: str) -> tuple[str, str, str]:
    """(status, label, time_class). Only documented or inferred ties drive a status; candidates are listed."""
    own = [e for e in events if e["tie_label"] in ("documented", "inferred") and e["event_date"] <= AS_OF_S]
    cands = [e for e in events if e["tie_label"] == "candidate"]
    kinds = {e["event_type"] for e in own}
    latest = max((e["event_date"] for e in own), default="")
    resp = max((e.get("response_date") or "" for e in own if e["event_type"] in ("solicitation",)), default="")
    if "award" in kinds:
        return "awarded_fy2026", "documented" if any(e["tie_label"] == "documented" and e["event_type"] == "award" for e in own) else "inferred", "completed"
    if "award_notice" in kinds:
        return "award_notice_fy2026", "documented", "completed"
    if "cancellation" in kinds:
        return "cancelled_fy2026", "documented", "completed"
    if "solicitation" in kinds:
        if resp and resp > AS_OF_S:
            return "solicitation_open", "documented", "expected_by_" + resp
        return "solicited_pending_award", "documented", "completed"
    if "presolicitation" in kinds:
        return "presolicitation_stage", "documented", "completed"
    if "justification" in kinds:
        return "justification_posted", "documented", "completed"
    if "rfi_sources_sought" in kinds:
        return "market_research_stage", "documented", "completed"
    if "special_notice" in kinds or "industry_day" in kinds:
        return "special_notice_only", "documented", "completed"
    if "notice_modified" in kinds:
        return "earlier_notice_modified_fy2026", "documented", "completed"
    inc = [e for e in own if e["event_type"].startswith("incumbent_")]
    if inc:
        ext = [e for e in inc if e["event_type"] == "incumbent_extension_or_option"]
        st = "incumbent_extended_or_option_fy2026" if ext else "incumbent_actions_only_fy2026"
        return st, "inferred", "completed"
    # nothing tied: place against the window
    if line is None:
        base = "no_qualifying_evidence"
        return base, "unresolved", "n/a"
    aw = window_end(line.get("award_fy", ""), line.get("award_quarter", ""))
    so = window_end(line.get("solicitation_fy", ""), line.get("solicitation_quarter", ""))
    end = aw or so
    suffix = "_candidate_only" if cands else ""
    if end is None:
        return "undated_no_qualifying_evidence" + suffix, "unresolved", "undated"
    if end <= AS_OF:
        return "window_passed_no_qualifying_evidence" + suffix, "unresolved", "window_closed"
    if end.isoformat() <= FY_END:
        return "expected_by_2026-09-30_no_evidence_yet" + suffix, "unresolved", "expected_by_2026-09-30"
    return "not_yet_due_fy27_plus" + suffix, "unresolved", "FY27_or_later"


# ---------------------------------------------------------------- main

def main() -> int:
    rows = manifest_rows()
    refreshed = refreshed_details(rows)
    ctx = tr.match_context()
    lines = tr.lrae_lines()
    universe = list(csv.DictReader((HERE / "candidate_universe.csv").open(newline="", encoding="utf-8")))
    # The study reads the two releases its universe was drawn from, whatever release is now the newest.
    release_of = {u["set"]: u["release"] for u in universe if u["release"]}
    latest = [l for l in lines if l["release"] == release_of["forecast_2025"]]
    prev = {l["record_key"]: l for l in lines if l["release"] == release_of["dropped_2024"]}
    listing = load_json("sam_org_listing.json")
    hits = listing.get("hits", {})
    usas = load_json("usaspending_incumbents.json").get("incumbents", {})
    fpds_sol = load_json("fpds_by_solicitation.json").get("solicitations", {})
    office_actions, windows = fpds_office_actions(rows)
    new_awards = [a for a in office_actions if a["mod"] in ("0", "") and FY_START <= a["signed"] <= AS_OF_S]
    sam_dates = sorted(r["retrieved_at"][:10] for r in rows if kept_page(r) and "organization_id=" in r.get("url", ""))
    searched = (f"SAM.gov listing of every NAVWAR HQ notice modified {FY_START}..{AS_OF_S} (retrieved {sam_dates[0] if sam_dates else '?'}..{sam_dates[-1] if sam_dates else '?'}); "
                f"saved SAM.gov PID and program searches (retrieved {tr.search_dates(rows)[0]}..{tr.search_dates(rows)[1]}); "
                f"FPDS NAVWAR HQ office scans {'; '.join(windows)}; USAspending award and transaction records for every incumbent contract the forecast names")

    events: list[dict] = []
    per_key: dict[str, list[dict]] = defaultdict(list)

    def add(key: str, **e):
        e.setdefault("requirement_key", key)
        events.append(e)
        per_key[key].append(e)

    # --- FY2026 NAVWAR HQ notices against every forecast line (latest release and dropped 2024 rows)
    details: dict[str, dict] = {}
    for nid, h in hits.items():
        if h["org_id"] != NAVWAR_ORG:
            continue
        d = detail_of(nid, refreshed)
        if d is None:
            d = {"id": nid, "title": h["title"], "solicitation": h["solicitation"], "type": (h["type"] or "").lower(), "posted": h["posted"],
                 "modified": h["modified"], "cancelled": h["cancelled"], "response": h["response"], "text": "", "path": "", "organization_id": NAVWAR_ORG, "award": {}}
        d["hit"] = h
        details[nid] = d
    targets = [(l["pid"] or l["record_key"], l, "forecast_2025") for l in latest] + [(l["record_key"], l, "dropped_2024") for l in prev.values()
                                                                                   if l["record_key"] in {u["record_key"] for u in universe if u["set"] == "dropped_2024"}]
    tied_sols: dict[str, set[str]] = defaultdict(set)
    for nid, d in details.items():
        hit = d["hit"]
        etype = STAGE_OF.get(hit["type"] or "", (hit["type"] or "notice").lower().replace(" ", "_"))
        if etype == "special_notice" and re.search(r"(?i)industry day|industry engagement", d["title"]):
            etype = "industry_day"
        posted_in_fy = FY_START <= (d["posted"] or hit["posted"]) <= AS_OF_S
        for key, line, which in targets:
            t = tie_notice_to_line(d, line, ctx)
            if not t:
                continue
            if t["label"] in ("documented", "inferred") and d["solicitation"]:
                tied_sols[key].add(compact(d["solicitation"]))
            common = dict(source_type="SAM.gov notice", source_url=f"https://sam.gov/opp/{nid}/view", notice_id=nid, solicitation=d["solicitation"],
                          notice_type=hit["type"], title=d["title"][:120], tie_label=t["label"], basis=t["basis"], saved_path=d["path"],
                          response_date=d.get("response") or hit.get("response") or "", cancelled=d["cancelled"], set=which)
            if posted_in_fy:
                add(key, event_type=etype, event_date=d["posted"] or hit["posted"], **common)
            elif hit["modified"] and hit["modified"] >= FY_START:
                add(key, event_type="notice_modified", event_date=hit["modified"], **{**common, "basis": t["basis"] + f"; notice posted {d['posted'] or hit['posted']}, modified in FY2026"})
            if d["cancelled"] and t["label"] in ("documented", "inferred"):
                add(key, event_type="cancellation", event_date=hit["modified"] or d["posted"], **{**common, "basis": t["basis"] + "; the notice record is marked cancelled"})
    # --- NIWC Pacific and Atlantic notices are listed by title only (no detail harvested): a shared rare program
    # name makes a candidate, never more, and the posting office is named on the row.
    for nid, h in hits.items():
        if h["org_id"] == NAVWAR_ORG or not (FY_START <= h["posted"] <= AS_OF_S):
            continue
        ncodes = tr.distinctive_tokens(h["title"]) - {"c4isr", "c4i", "fms", "usn", "don", "dod", "cots", "gfe", "isr"}
        for key, line, which in targets:
            codes = {t for t in tr.distinctive_tokens(line["requirement_title"]) if ctx["rarity"].get(t, 0) <= 3}
            shared = codes & ncodes
            if not shared:
                continue
            etype = STAGE_OF.get(h["type"] or "", (h["type"] or "notice").lower().replace(" ", "_"))
            add(key, event_type=etype, event_date=h["posted"], source_type="SAM.gov notice (title only)", source_url=f"https://sam.gov/opp/{nid}/view",
                notice_id=nid, solicitation=h["solicitation"], notice_type=h["type"], title=h["title"][:120], tie_label="candidate",
                basis=f"posted by {h['org']}, title shares program token(s) {', '.join(sorted(shared))}; detail not harvested", saved_path="",
                response_date=h.get("response") or "", cancelled=h["cancelled"], set=which)
    # --- awards: FY2026 new actions in the office scans, and FPDS by solicitation for FY2026 notices
    all_awards = list(new_awards)
    for sol, acts in fpds_sol.items():
        for a in acts:
            if a["mod"] in ("0", "") and a["signed"] >= FY_START:
                all_awards.append({**a, "via_solicitation": sol})
    seen_aw = set()
    for a in all_awards:
        k = (a["piid"], a["mod"], a["signed"])
        if k in seen_aw:
            continue
        seen_aw.add(k)
        for key, line, which in targets:
            t = tie_award_to_line(a, line, tied_sols.get(key, set()), ctx)
            if not t:
                continue
            add(key, event_type="award", event_date=a["signed"], source_type="FPDS", source_url=f"https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:{a['piid']}",
                notice_id="", solicitation=a.get("solicitation", ""), notice_type="", title=f"{a['piid']} {a.get('vendor', '')} {a.get('description', '')[:80]}",
                tie_label=t["label"], basis=t["basis"], saved_path="", response_date="", cancelled=False, set=which,
                piid=a["piid"], idv=a.get("idv", ""), obligated=a.get("obligated", ""), base_and_all_options=a.get("base_and_all_options", ""))
    # --- incumbent actions in FY2026 (USAspending)
    for key, line, which in targets:
        for piid in contract_tokens(line["existing_contract_number"]):
            rec = usas.get(piid)
            if not rec or not rec.get("lookup"):
                continue
            aw = rec.get("award") or {}
            pop = aw.get("period_of_performance") or {}
            for tx in rec.get("fy2026_transactions", []):
                desc = (tx.get("description") or "")
                ext = bool(EXTEND_RE.search(desc)) or "OPTION" in (tx.get("action_type_description") or "").upper()
                add(key, event_type="incumbent_extension_or_option" if ext else "incumbent_action", event_date=str(tx.get("action_date") or "")[:10],
                    source_type="USAspending", source_url=f"https://www.usaspending.gov/award/{rec['lookup']['generated_internal_id']}",
                    notice_id="", solicitation="", notice_type="", title=f"{piid} {tx.get('modification_number', '')} {tx.get('action_type_description', '')}: {desc[:90]}",
                    tie_label="inferred", basis=f"action on the incumbent contract {piid} the forecast names; an incumbent event, not the forecast requirement's own",
                    saved_path="", response_date="", cancelled=False, set=which, piid=piid, obligated=tx.get("federal_action_obligation", ""),
                    pop_end=pop.get("end_date", ""))
    # --- notice-created requirements: their own chain
    notice_keys = [u for u in universe if u["set"] == "notice_created"]
    for u in notice_keys:
        raw = u["record_key"][len("notice:"):]
        for nid, d in details.items():
            hit = d["hit"]
            same = (compact(d["solicitation"]) == compact(raw) and len(compact(raw)) >= 6) or nid == raw
            if not same:
                continue
            etype = STAGE_OF.get(hit["type"] or "", (hit["type"] or "notice").lower().replace(" ", "_"))
            if etype == "special_notice" and re.search(r"(?i)industry day", d["title"]):
                etype = "industry_day"
            in_fy = FY_START <= (d["posted"] or hit["posted"]) <= AS_OF_S
            add(u["record_key"], event_type=etype if in_fy else "notice_modified", event_date=(d["posted"] or hit["posted"]) if in_fy else hit["modified"],
                source_type="SAM.gov notice", source_url=f"https://sam.gov/opp/{nid}/view", notice_id=nid, solicitation=d["solicitation"], notice_type=hit["type"],
                title=d["title"][:120], tie_label="documented", basis="same solicitation number", saved_path=d["path"],
                response_date=d.get("response") or hit.get("response") or "", cancelled=d["cancelled"], set="notice_created")
            if d["cancelled"]:
                add(u["record_key"], event_type="cancellation", event_date=hit["modified"] or d["posted"], source_type="SAM.gov notice", source_url=f"https://sam.gov/opp/{nid}/view",
                    notice_id=nid, solicitation=d["solicitation"], notice_type=hit["type"], title=d["title"][:120], tie_label="documented",
                    basis="the notice record is marked cancelled", saved_path=d["path"], response_date="", cancelled=True, set="notice_created")
        own = {(x["piid"], x["mod"], x["signed"]): x
               for s, acts in fpds_sol.items() if compact(s) == compact(raw) for x in acts}
        for a in (own[k] for k in sorted(own)):
            if a["mod"] in ("0", "") and a["signed"] >= FY_START:
                add(u["record_key"], event_type="award", event_date=a["signed"], source_type="FPDS", source_url=f"https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=SOLICITATION_ID:{compact(raw)}",
                    notice_id="", solicitation=raw, notice_type="", title=f"{a['piid']} {a.get('vendor', '')}", tie_label="documented", basis="FPDS action under the same solicitation number",
                    saved_path="", response_date="", cancelled=False, set="notice_created", piid=a["piid"], obligated=a.get("obligated", ""))

    # --- news: every article the article layer linked to a requirement, as a dated observation beside it
    articles = news_articles()
    line_of = {key: line for key, line, _ in targets}
    set_of = {key: which for key, _, which in targets}
    notice_raw = [(u["record_key"], compact(u["record_key"][len("notice:"):]))
                  for u in universe if u["set"] == "notice_created"]
    news_rows = 0
    for a in articles:
        published = a.get("published") or (a.get("retrieved_at") or "")[:10]
        linked = list((a.get("links") or {}).get("requirements") or [])
        for key, raw in notice_raw:
            if len(raw) >= 6 and any(raw in compact(c.get("passage") or "") for c in a.get("claims") or []):
                linked.append(key)
        for key in dict.fromkeys(linked):
            if key not in line_of and key not in {k for k, _ in notice_raw}:
                continue
            t_ = news_tie(a, key, line_of.get(key), tied_sols.get(key, set()))
            c = t_["claim"] or {}
            add(key, event_type=NEWS_TYPE.get(c.get("statement_type", ""), "news_report"), event_date=published,
                tie_label=t_["label"], set=set_of.get(key, "notice_created"),
                basis=f"{t_['basis']}; {a.get('source_type', 'reporting')}, reliability {a.get('reliability', 'unstated')}, "
                      f"claim {t_['n']} of {len(a.get('claims') or [])}; the article reads as {a.get('relation', 'an observation')}",
                source_type="news article", source_url=a.get("url", ""), notice_id="", solicitation="", notice_type="",
                title=(a.get("headline") or "")[:120], saved_path=(a.get("record") or {}).get("path", ""),
                response_date="", cancelled=False, passage=(c.get("passage") or "")[:300])
            news_rows += 1

    # --- status per universe row
    by_key = {(l["pid"] or l["record_key"]): l for l in latest}
    by_key.update({k: l for k, l in prev.items()})
    examples = tr.attribution_examples()
    sgs = tr.sgs_hits(rows)
    out_rows = []
    for u in universe:
        key = u["record_key"]
        line = by_key.get(key)
        everything = sorted(per_key.get(key, []), key=lambda e: e["event_date"])
        # An article never sets a status. It is listed beside the reading the notices and awards produce.
        evs = [e for e in everything if e["source_type"] != "news article"]
        news = [e for e in everything if e["source_type"] == "news article"]
        status, label, tclass = status_for(evs, line if u["set"] != "notice_created" else None, u["set"])
        if u["set"] == "notice_created" and not evs:
            status, label, tclass = "no_fy2026_activity_found_on_this_number", "unresolved", "n/a"
        trace_reading = ""
        if line is not None and u["set"] == "forecast_2025":
            try:
                trace_reading = tr.line_status(line, rows, sgs, examples, AS_OF, ctx)["reading"][:300]
            except Exception as exc:  # noqa: BLE001
                trace_reading = f"trace.py line_status error: {exc}"[:200]
        own = [e for e in evs if e["tie_label"] in ("documented", "inferred")]
        cands = [e for e in evs if e["tie_label"] == "candidate"]
        rel = [e for e in evs if e["tie_label"] == "related"]
        expected = []
        for e in own:
            if e.get("response_date") and e["response_date"] > AS_OF_S:
                expected.append(f"proposals due {e['response_date']} under {e['solicitation'] or e['notice_id'][:8]}")
        if line is not None:
            aw = window_end(line.get("award_fy", ""), line.get("award_quarter", ""))
            if aw and aw > AS_OF and aw.isoformat() <= FY_END:
                expected.append(f"forecast award window ends {aw.isoformat()}")
        out_rows.append({
            "set": u["set"], "record_key": key, "title": u["title"][:100], "office_code": u["office_code"] or u["office_id"][:60],
            "sol_window": f"{u['sol_fy']} {u['sol_q']}".strip(), "award_window": f"{u['award_fy']} {u['award_q']}".strip(),
            "incumbent": u["existing_contract_number"], "priority": u["priority"],
            "status_as_of_2026-09-22": status, "status_label": label, "time_class": tclass,
            "events_documented_or_inferred": len(own), "events_candidate": len(cands), "events_related": len(rel),
            "first_fy2026_event": own[0]["event_date"] + " " + own[0]["event_type"] if own else "",
            "latest_fy2026_event": own[-1]["event_date"] + " " + own[-1]["event_type"] if own else "",
            "event_chain": " | ".join(f"{e['event_date']} {e['event_type']} [{e['tie_label']}] {e['solicitation'] or e.get('piid', '') or e['notice_id'][:8]}" for e in own)[:600],
            "candidates": " | ".join(f"{e['event_date']} {e['event_type']} {e['solicitation'] or e['notice_id'][:8]}: {e['basis'][:90]}" for e in cands)[:500],
            "expected_before_2026-09-30": "; ".join(expected),
            "negative_scope": "" if own else f"No qualifying evidence was found in the searched sources as of {AS_OF_S}. Searched: {searched}",
            "news_signals": " | ".join(f"{e['event_date']} [{e['tie_label']}] {e['title'][:60]}: {e['passage'][:110]}"
                                      for e in news)[:700],
            "existing_reading_2026-09-21": u["existing_reading_2026-09-21"], "trace_reading_2026-09-22": trace_reading,
        })
    fields = list(out_rows[0].keys())
    with (HERE / "status_fy2026.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    efields = ["requirement_key", "set", "event_type", "event_date", "tie_label", "basis", "source_type", "source_url", "notice_id", "solicitation", "notice_type",
               "title", "response_date", "cancelled", "saved_path", "piid", "idv", "obligated", "base_and_all_options", "pop_end",
               "passage"]
    with (HERE / "events.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=efields, extrasaction="ignore")
        w.writeheader()
        for e in sorted(events, key=lambda e: (e["requirement_key"], e["event_date"])):
            w.writerow({k: e.get(k, "") for k in efields})
    # --- summary
    summary: dict = {"as_of": AS_OF_S, "navwar_hq_notices_listed_fy2026": sum(1 for h in hits.values() if h["org_id"] == NAVWAR_ORG),
                     "navwar_hq_notices_posted_fy2026": sum(1 for h in hits.values() if h["org_id"] == NAVWAR_ORG and FY_START <= h["posted"] <= AS_OF_S),
                     "niwc_notices_listed": sum(1 for h in hits.values() if h["org_id"] != NAVWAR_ORG),
                     "fpds_navwar_hq_actions_fy2026_in_saved_scans": len(office_actions), "fpds_new_awards_fy2026": len(new_awards),
                     "fpds_scan_windows": windows, "usaspending_incumbents_found": sum(1 for r in usas.values() if r.get("lookup")),
                     "usaspending_incumbents_not_found": sorted(k for k, r in usas.items() if not r.get("lookup")),
                     "events": len(events), "news_articles_modelled": len(articles),
                     "news_events": sum(1 for e in events if e["source_type"] == "news article"),
                     "requirements_with_a_news_signal": len({e["requirement_key"] for e in events if e["source_type"] == "news article"}),
                     "searched": searched, "status_counts": {}}
    for r in out_rows:
        k = f"{r['set']}:{r['status_as_of_2026-09-22']}"
        summary["status_counts"][k] = summary["status_counts"].get(k, 0) + 1
    summary["status_counts"] = dict(sorted(summary["status_counts"].items()))
    (HERE / "sweep_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

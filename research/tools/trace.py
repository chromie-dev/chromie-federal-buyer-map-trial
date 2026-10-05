#!/usr/bin/env python3
"""Follow a Navy requirement across forecasts, offices, notices and awards, from one data set.

    python research/tools/trace.py status  [--dsn DSN] [--office pmw:170] [--through 26]
    python research/tools/trace.py need    <PID | record key>            [--dsn DSN]
    python research/tools/trace.py notice  <SAM notice id | solicitation number>
    python research/tools/trace.py award   <PIID>
    python research/tools/trace.py --selfcheck

Three questions, answered from the same rows:

  status  - where every forecast line whose solicitation window has arrived stands, one
            outcome word first: awarded, solicited, cancelled (marked so on SAM.gov),
            review (an action on the incumbent; a row missing from the latest release),
            restructured (a stated method, instrument, type or value changed; a sibling
            line split off), delayed, open, not yet due. States that also hold follow
            "also"; a candidate notice is appended and never promoted.
  need    - one requirement followed across releases: every revision with its date,
            the office each release named, the incumbent contract with its ceiling and
            obligations kept apart from the estimate, the notices and awards tied to it.
  notice  - an active notice traced to the office it names, that office's ancestry and
            history from the organization memory, and the forecast lines that may be
            the same requirement, each with the basis for the match.
  award   - a contract read back to the forecast lines that cite it and forward to the
            notices and awards that followed.

Offline. Reads the agency-intelligence tables through `psql`, the LRAE datapacks and the
saved SAM.gov, FPDS and USAspending responses listed in research/sources/documents_manifest.jsonl.
It never fetches: a lookup that was not collected prints as "not collected" with the
command that would collect it. An LRAE value range is an estimate, a contract's
base-and-all-options is a ceiling, and FPDS/USAspending obligations are money placed;
the three print in separate columns and are never added together.

Three rules the output keeps:

  - A negative names the records searched and their retrieval date ("no award found in
    the saved FPDS lookup as of 2026-09-20"). It never says nothing was awarded.
  - Every connection carries its source: the URL, the retrieval date and the hash of the
    saved bytes, the spreadsheet row, or the observation ids in the organization memory.
  - Two titles naming different lots, families or generations (SF2 and SF3, Services II
    and III) are related procurements in one program, printed apart from candidates for
    the same requirement; they are context, never the same buy.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import MANIFEST, ROOT, kept_page  # noqa: E402
from fpds_sweep import OFFICES as SWEPT_OFFICES, awards as sweep_awards, fiscal_year as fiscal_year_of, saved_pages, windows  # noqa: E402
from lrae_package import RELEASES, alias_map, contract_tokens, fold_map, memory_file, norm_code, norm_title  # noqa: E402
from notice_kinds import kept  # noqa: E402

from agency import P, RESEARCH, SAM_NOTICES, forecast_packs, note_is_ours  # noqa: E402

DEFAULT_DSN = f"postgresql://postgres:postgres@127.0.0.1:{os.environ.get('PGPORT', '54322')}/{P['database']}"  # this agency's built layer

OFFICE_CODE_RE = re.compile(P["office_key_re"])

# The forecast packs the tracer reads (none for an agency without a forecast) and this agency's notice folder.
PACKS = forecast_packs(ROOT / "datapack")


def release_order(packs: list[Path] = PACKS) -> dict[str, list[str]]:
    """Per forecast activity, its saved releases oldest first. A release that carries another activity's rows (the
    combined report's ONR sheet prints NRL's) is that activity's release too."""
    saved = {p.name for p in packs}
    out: dict[str, list[str]] = defaultdict(list)
    for r in sorted(RELEASES, key=lambda r: (r["release_date"], r["key"])):
        if r["key"] in saved:
            for activity in (r["activity"], *r.get("carries", ())):
                out[activity].append(r["key"])
    return dict(out)


ORDER = release_order()
CURRENT = {keys[-1] for keys in ORDER.values()}  # the newest release of each activity: what the forecasts say today
NOTICES = SAM_NOTICES
SOL_RE = re.compile(P["reading"]["rfp_re"])
# SAM.gov notice type codes as the site API returns them.
NOTICE_TYPE = {"p": "presolicitation", "o": "solicitation", "k": "combined synopsis/solicitation", "r": "sources sought",
               "s": "special notice", "a": "award notice", "u": "justification (J&A)", "i": "intent to bundle", "g": "sale of surplus"}
STOP = {"the", "and", "for", "of", "to", "a", "in", "on", "with", "new", "follow", "contract", "contracts", "services", "service",
        "support", "system", "systems", "program", "navy", "navwar", "c", "fy24", "fy25", "fy26", "fy27", "rfp", "order", "task",
        # procurement vocabulary that any two lines may share without being the same buy
        "production", "development", "procurement", "spares", "terminals", "terminal", "integration", "modernization",
        "engineering", "lot", "buy", "award", "fund", "base", "idiq", "mac", "recompete", "compete", "upgrade", "sustainment",
        "logistics", "integrated", "professional", "technical", "management", "office", "solutions", "increase", "ceiling",
        "version", "receive", "multi", "multiple", "single", "iss", "ess", "pss", "hw", "sw", "software", "hardware", "year", "option",
        # instruments and notice kinds, which any two buys may share: a CSO area of interest is not a program
        "cso", "aoi", "ota", "baa", "rfi", "rfq", "rpp", "rfpp", "idiq", "gwac", "seaport", "nxg"}


# ---------------------------------------------------------------- saved inputs

def manifest() -> list[dict]:
    """The ledger rows this profile's collectors wrote: another layer's searches are no negative reading here."""
    rows = (json.loads(l) for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip())
    return [r for r in rows if note_is_ours(r.get("note", ""))]


def saved(rows: list[dict], predicate) -> dict | None:
    hits = [r for r in rows if kept_page(r)
            and predicate(r.get("url", "")) and (ROOT / r["path"]).exists()]
    return hits[-1] if hits else None


def load_json(row: dict | None):
    return json.loads((ROOT / row["path"]).read_text(encoding="utf-8")) if row else None


def search_dates(rows: list[dict]) -> tuple[str, str]:
    """Earliest and latest retrieval dates of the saved SAM.gov searches; every negative reading is scoped to them."""
    dates = sorted(r["retrieved_at"][:10] for r in rows if kept_page(r) and "sgs/v1/search" in r.get("url", ""))
    return (dates[0], dates[-1]) if dates else ("", "")


def not_found(what: str, where: str, as_of: str) -> str:
    """A negative names the records searched and the date; it never claims the thing did not happen."""
    return f"no {what} found in {where}" + (f" as of {as_of}" if as_of else "")


def cite(row: dict | None) -> str:
    """One saved lookup, cited: URL, retrieval date, hash prefix."""
    return f"{row['url']} retrieved {row['retrieved_at'][:10]} sha {row['sha256'][:12]}" if row else "not collected"


SAM_VIEW = "https://sam.gov/opp/{}/view"


def notice_source(rows: list[dict], notice_id: str) -> dict | None:
    """The manifest row of a harvested notice detail (not its attachment list)."""
    return saved(rows, lambda u: f"/opportunities/{notice_id}?" in u and "/resources" not in u)


def compact(token: str) -> str:
    return re.sub(r"[\s-]", "", (token or "").upper())


def fpds_actions(body: str) -> list[dict]:
    """Every contract action in an FPDS ATOM page, with the award and IDV ids read from their nested blocks."""
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", body, re.S):
        def tag(name: str, block: str = entry) -> str:
            m = re.search(rf"<ns1:{name}(?:\s[^>]*)?>(.*?)</ns1:{name}>", block, re.S)
            return re.sub(r"&amp;", "&", m.group(1).strip()) if m else ""
        award_id = re.search(r"<ns1:(?:awardID|IDVID|OtherTransactionAwardID|OtherTransactionIDVID)>(.*?)</ns1:(?:awardID|IDVID|OtherTransactionAwardID|OtherTransactionIDVID)>", entry, re.S)
        block = award_id.group(1) if award_id else entry
        kind = ("other_transaction" if "<ns1:OtherTransaction" in entry else "idv" if "<ns1:IDV " in entry or "<ns1:IDV>" in entry else "award")
        own = re.search(r"<ns1:(?:awardContractID|IDVID|OtherTransactionAwardContractID|OtherTransactionIDVID)>(.*?)</ns1:(?:awardContractID|IDVID|OtherTransactionAwardContractID|OtherTransactionIDVID)>", block, re.S)
        piid = re.search(r"<ns1:PIID>(.*?)</ns1:PIID>", own.group(1), re.S) if own else None
        agency = re.search(r"<ns1:agencyID[^>]*>(.*?)</ns1:agencyID>", own.group(1), re.S) if own else None
        ref = re.search(r"<ns1:referencedIDVID>(.*?)</ns1:referencedIDVID>", block, re.S)
        idv = re.search(r"<ns1:PIID>(.*?)</ns1:PIID>", ref.group(1), re.S) if ref else None
        idv_agency = re.search(r"<ns1:agencyID[^>]*>(.*?)</ns1:agencyID>", ref.group(1), re.S) if ref else None
        out.append({"piid": piid.group(1) if piid else tag("PIID"), "idv": idv.group(1) if idv else "", "kind": kind,
                    "agency": agency.group(1).strip() if agency else "", "idv_agency": idv_agency.group(1).strip() if idv_agency else "",
                    "mod": tag("modNumber"), "signed": tag("signedDate")[:10], "obligated": tag("obligatedAmount"),
                    "base_and_all_options": tag("baseAndAllOptionsValue"), "solicitation": tag("solicitationID"),
                    "vendor": tag("vendorName"), "contracting_office": tag("contractingOfficeID"),
                    "description": tag("descriptionOfContractRequirement")})
    return out


def award_uid(piid: str, agency: str = "", idv: str = "", idv_agency: str = "", kind: str = "award") -> str | None:
    """USAspending's generated award id for a contract action as FPDS states it: CONT_AWD_<piid>_<agency>_<idv>_<idv agency>
    for an award, CONT_IDV_<piid>_<agency> for a vehicle, -NONE- where FPDS names no vehicle. An other transaction has no
    contract id in this form (None): the tool says so instead of printing an address that answers 404."""
    if not piid:
        return None
    if kind == "other_transaction":
        return None
    if kind == "idv":
        return f"CONT_IDV_{compact(piid)}_{agency or '-NONE-'}"
    return f"CONT_AWD_{compact(piid)}_{agency or '-NONE-'}_{compact(idv) if idv else '-NONE-'}_{(idv_agency or agency) if idv else '-NONE-'}"


def award_uid_for(piid: str, actions: list[dict] | None = None) -> str | None:
    """The USAspending id for a PIID from what the record holds of it: its saved FPDS actions, else the office sweep's
    base award. None when nothing saved names the action (the parent vehicle is then unknown) or it is an other transaction."""
    a = next((a for a in actions or [] if compact(a["piid"]) == compact(piid)), None) or swept_awards().get(compact(piid))
    if not a:
        return None
    return award_uid(piid, a.get("agency", ""), a.get("idv", ""), a.get("idv_agency", ""), a.get("kind", "award"))


def usaspending_check(piid: str, actions: list[dict] | None = None) -> str:
    """How to collect the award's USAspending record, when the record knows its id; else what stands in the way."""
    uid_ = award_uid_for(piid, actions)
    if uid_:
        return f"python research/tools/fetch.py 'https://api.usaspending.gov/api/v2/awards/{uid_}/'"
    a = next((a for a in actions or [] if compact(a["piid"]) == compact(piid)), None) or swept_awards().get(compact(piid))
    if a and a.get("kind") == "other_transaction":
        return "an other transaction award in FPDS; USAspending's contract id form (CONT_AWD_...) does not name it, so no address is offered"
    return f"USAspending id unknown: the record holds no FPDS action naming {compact(piid)}'s agency and vehicle; collect python research/tools/fetch.py 'https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:{compact(piid)}&start=0' first"


def usaspending(rows: list[dict], piid: str) -> dict | None:
    row = saved(rows, lambda u: "usaspending" in u and (f"/CONT_AWD_{piid}_" in u or f"/CONT_IDV_{piid}_" in u))
    j = load_json(row)
    if not j:
        return None
    pop = j.get("period_of_performance") or {}
    ltx = j.get("latest_transaction_contract_data") or {}
    return {"piid": j.get("piid"), "type": j.get("type_description"), "description": (j.get("description") or ""),
            "ceiling": j.get("base_and_all_options"), "obligated": j.get("total_obligation"), "signed": j.get("date_signed"),
            "pop_start": pop.get("start_date"), "pop_end": pop.get("end_date"), "last_modified": str(pop.get("last_modified_date") or "")[:10],
            "recipient": (j.get("recipient") or {}).get("recipient_name"), "solicitation": ltx.get("solicitation_identifier"),
            "competed": ltx.get("extent_competed_description"), "offers": ltx.get("number_of_offers_received"),
            "parent": (j.get("parent_award") or {}).get("piid"), "awarding_office": (j.get("awarding_agency") or {}).get("office_agency_name"),
            # a backfilled row carries the day its bytes were first committed, not a retrieval time
            "url": row["url"], "retrieved": retrieved_on(row), "sha": row["sha256"][:12], "sha256": row["sha256"]}


def retrieved_on(row: dict) -> str:
    return str(row.get("retrieved_at") or row.get("first_committed") or row.get("backfilled_at") or "")[:10]


def fpds_by_piid(rows: list[dict], piid: str) -> tuple[list[dict], dict | None, bool]:
    """Every action on the saved pages of the contract's FPDS history, oldest first, the first page's
    row, and whether the newest saved page is the last one (it names no next page)."""
    by_url = {r["url"]: r for r in rows if kept_page(r) and f"q=PIID:{piid}&start=" in r.get("url", "")
              and (ROOT / r["path"]).exists()}
    pages = sorted(by_url.values(), key=lambda r: int(r["url"].rsplit("start=", 1)[1]))
    bodies = [(ROOT / p["path"]).read_text(encoding="utf-8", errors="replace") for p in pages]
    actions = [a for body in bodies for a in fpds_actions(body)]
    return actions, (pages[0] if pages else None), bool(bodies) and 'rel="next"' not in bodies[-1]


@lru_cache(maxsize=1)
def swept_awards() -> dict[str, dict]:
    """The base awards on the saved FPDS sweep pages by PIID: the end date and vehicle of a contract whose USAspending
    record and PIID lookup were never taken."""
    return {compact(a["piid"]): a for a in sweep_awards(manifest())}


@lru_cache(maxsize=1)
def swept_by_solicitation() -> dict[str, list[dict]]:
    """The base awards on the saved FPDS office sweep pages (fpds_sweep.py) by solicitation number, one per
    PIID, each carrying the page it was read from under "source"."""
    pages = {r["url"]: r for r in manifest() if kept_page(r) and "CONTRACTING_OFFICE_ID:" in r.get("url", "")
             and "+MODIFICATION_NUMBER:0&start=" in r["url"] and (ROOT / r["path"]).exists()}
    by_piid: dict[str, dict] = {}
    for page in pages.values():
        for a in fpds_actions((ROOT / page["path"]).read_text(encoding="utf-8", errors="replace")):
            if a["piid"] and a["solicitation"]:
                by_piid.setdefault(a["piid"], {**a, "source": page})
    out: dict[str, list[dict]] = defaultdict(list)
    for a in sorted(by_piid.values(), key=lambda a: (a["signed"], a["piid"])):
        out[compact(a["solicitation"])].append(a)
    return dict(out)


def fpds_by_solicitation(rows: list[dict], sol: str) -> tuple[list[dict], dict | None]:
    """The actions under a solicitation number: the saved lookup by that number, then any base award the office
    sweep saved under it that the lookup lacks (the sweep can be the later retrieval). Each action names its page
    under "source"; the second value is the lookup's row, else the first swept page."""
    row = saved(rows, lambda u: f"SOLICITATION_ID:{compact(sol)}" in u)
    actions = [{**a, "source": row} for a in fpds_actions((ROOT / row["path"]).read_text(encoding="utf-8", errors="replace"))] if row else []
    have = {a["piid"] for a in actions}
    actions += [a for a in swept_by_solicitation().get(compact(sol), []) if a["piid"] not in have]
    return actions, row or (actions[0]["source"] if actions else None)


def sweep_scope(rows: list[dict], sol: str, posted: str) -> str:
    """The office sweep a number no saved award carries was checked against: its issuing office's base awards
    from the notice's fiscal year on, each year saved to its last page; empty when the sweep does not cover it."""
    office = compact(sol)[:6]
    if office not in SWEPT_OFFICES or not posted:
        return ""
    years = [w for w in windows(date.today()) if w["fy"] >= fiscal_year_of(date.fromisoformat(posted))]
    taken = [saved_pages(rows, office, w) for w in years]
    if not years or not all(complete for _, complete in taken):
        return ""
    retrieved = max(p["retrieved_at"][:10] for pages, _ in taken for p in pages)
    return (f"the saved FPDS sweep of {office} base awards signed FY{years[0]['fy']}"
            + (f" to FY{years[-1]['fy']}, each year" if len(years) > 1 else "") + f" saved to its last page, retrieved to {retrieved}")


def notice_open(flag, due: str, today: date | None = None) -> bool | None:
    """Whether a notice still takes responses: a saved search's active flag stands as of the day the search ran, so a
    response date that has passed closes the notice whatever the flag said; with no date the flag stands, and with
    neither the answer is unknown (None)."""
    today = today or date.today()
    if due and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) and date.fromisoformat(due) < today:
        return False
    return None if flag is None else bool(flag)


def sgs_hits(rows: list[dict], today: date | None = None) -> dict[str, dict]:
    """Every SAM.gov search hit ever saved, by notice id, with the query that found it. `active` reads the response
    date against today (notice_open), never the search's flag alone."""
    hits: dict[str, dict] = {}
    for r in rows:
        if not kept_page(r) or "sgs/v1/search" not in r.get("url", "") or not (ROOT / r["path"]).exists():
            continue
        try:
            body = json.loads((ROOT / r["path"]).read_text(encoding="utf-8"))
        except ValueError:
            continue
        for h in (body.get("_embedded") or {}).get("results") or []:
            kind = h.get("type")
            due = str(h.get("responseDate") or "")[:10]
            hits.setdefault(h["_id"], {"id": h["_id"], "title": h.get("title") or "", "type": kind.get("value") if isinstance(kind, dict) else kind,
                                       "posted": str(h.get("publishDate") or "")[:10], "due": due, "active": notice_open(h.get("isActive"), due, today),
                                       "cancelled": bool(h.get("isCanceled")), "solicitation": h.get("solicitationNumber") or "", "query_url": r["url"], "sha": r["sha256"][:12]})
    # Notices harvested in full (research/tools/sam_notices.py) count too, whether or not a search found them.
    for path in sorted(NOTICES.glob("*.json")) if NOTICES.exists() else []:
        if path.name.endswith(".resources.json") or path.name.startswith(("._", "search_")) or path.stem in hits:
            continue
        d = notice_detail(path.stem)
        if d and d["title"]:
            hits[path.stem] = {"id": path.stem, "title": d["title"], "type": d["type"].title() if d["type"] in NOTICE_TYPE.values() else d["type"],
                               "posted": d["posted"], "due": d["deadline"], "active": notice_open(None, d["deadline"], today), "cancelled": d["cancelled"],
                               "solicitation": d["solicitation"], "query_url": d["path"], "sha": ""}
    return apply_notice_cancellation(hits)


def apply_notice_cancellation(hits: dict[str, dict], detail_of=None) -> dict[str, dict]:
    """The notice's own record decides whether it is cancelled, over any search that found it.

    A search hit carries the flag as it stood when the search ran (`isCanceled`); the saved notice
    carries it as `cancelled` and is the later word. Reading only the search calls a notice live
    after it was cancelled, and the loader, which reads the notice, would then disagree with the tool.
    """
    detail_of = detail_of or notice_detail
    for notice_id, hit in hits.items():
        detail = detail_of(notice_id)
        if detail and detail["cancelled"]:
            hit["cancelled"] = True
    return hits


def notice_detail(notice_id: str) -> dict | None:
    path = NOTICES / f"{notice_id}.json"
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return None  # a saved body that is not the notice (an error page, an attachment listing)
    if not isinstance(d, dict):
        return None
    o = d.get("data2") or d.get("data") or d
    if not isinstance(o, dict):
        return None
    text = ""
    for candidate in re.findall(r'"((?:[^"\\]|\\.){300,})"', json.dumps(d)):
        # unicode_escape decodes an escaped emoji as two lone surrogates; the utf-16 round trip joins them into one character
        text = max(text, candidate.encode().decode("unicode_escape", "ignore").encode("utf-16", "surrogatepass").decode("utf-16", "replace"), key=len)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
    return {"id": notice_id, "title": html.unescape(o.get("title") or "" or ""), "solicitation": o.get("solicitationNumber") or "",
            "type": NOTICE_TYPE.get(str(o.get("type") or ""), str(o.get("type") or "")), "posted": str(d.get("postedDate") or o.get("postedDate") or "")[:10],
            "award": o.get("award") or {}, "cancelled": bool(d.get("cancelled")), "text": text, "path": str(path.relative_to(ROOT)),
            "organization_id": str(o.get("organizationId") or ""),
            "deadline": str(((o.get("solicitation") or {}).get("deadlines") or {}).get("response") or "")[:10]}


# SAM.gov organization ids as the site API writes them on a notice, to the memory node for that office.
# The id's hierarchy on a search hit reads: level 5, code N00039, "NAVAL INFORMATION WARFARE SYSTEMS" (OFFICE).
SAM_ORG_NODES = P["sam_org_nodes"]

# A notice type as SAM.gov states it, read as the signal it carries for a requirement.
SIGNAL_OF = {"sources sought": "request for information (sources sought)", "presolicitation": "presolicitation (intent to solicit)",
             "solicitation": "solicitation (RFP or RFQ)", "combined synopsis/solicitation": "solicitation (combined synopsis)",
             "special notice": "special notice", "award notice": "award notice", "justification (J&A)": "justification (sole source or limited competition)",
             "intent to bundle": "intent to bundle", "sale of surplus": "sale of surplus"}


def signal_kind(detail: dict) -> str:
    """What kind of signal a notice is: its SAM.gov type, and for a special notice what the model read it announces, else
    what its title's keywords say."""
    kind = SIGNAL_OF.get(detail["type"], detail["type"] or "notice")
    if detail["type"] in SOL_KINDS and ("area of interest" in detail["title"].lower() or "commercial solutions opening" in detail["text"][:800].lower()):
        kind = f"area of interest under a commercial solutions opening ({detail['type']})"
    if detail["type"] == "special notice" and (read := kept(detail["id"])):
        kind = f"{read} (special notice)"  # notice_kinds.py: the model's reading, its words in the notice verbatim
    elif detail["type"] == "special notice":
        low = detail["title"].lower()
        # A proposers day, information session, workshop or webinar is an industry day under another name: the
        # research agencies announce their programs to industry that way (DARPA posts hundreds).
        if ("industry day" in low or "industry engagement" in low or "industry event" in low or re.search(r"proposer'?s'? day", low)
                or "information session" in low or "workshop" in low or "webinar" in low):
            kind = "industry day (special notice)"
        elif "request for information" in low or re.search(r"\brfi\b", low):
            kind = "request for information (special notice)"
        elif "ceiling" in low or "modification" in low:
            kind = "action on an existing contract (special notice)"
        elif "long range acquisition" in low or "forecast" in low or low.startswith("future program"):
            kind = "forecast (special notice)"
        elif "intent to award" in low or "intent to sole source" in low or "other than full and open" in low:
            kind = "intent to award without full competition (special notice)"
        elif "commercial solutions opening" in low or "cso" in low.split():
            kind = "commercial solutions opening (special notice)"
    if detail["cancelled"]:
        kind += ", marked cancelled on SAM.gov"
    return kind


def line_claims(detail: dict, ctx: dict) -> list[dict]:
    """Current forecast lines that name this notice explicitly: the line's PID in the notice, a solicitation
    number the line's title carries (basis documented), an incumbent contract only this line cites on a
    solicitation-stage notice posted after the line first appeared in a forecast, or the line's office named in
    the notice text with three title words that no other current line of that office shares (basis inferred)."""
    haystack = compact(f"{detail['title']} {detail['solicitation']} {detail['text']}")
    sol = compact(detail["solicitation"])
    piids = set(contract_tokens(detail["text"]))
    out = []
    for key, chain in ctx["chains"].items():
        latest = chain[-1]
        if latest["release"] not in ctx["current"]:
            continue  # a line dropped from its activity's newest release reads `review`; it is not a resolution target
        pid = latest["pid"]
        if pid and compact(pid) in haystack:
            out.append({"key": key, "line": latest, "basis": "documented", "why": f"the {detail['type']} of {detail['posted']} carries the line's PID {pid}"})
            continue
        own = [compact(m) for m in SOL_RE.findall(latest["requirement_title"].replace(" ", ""))]
        if sol and sol in own:
            out.append({"key": key, "line": latest, "basis": "documented", "why": f"the line's title carries the solicitation number {detail['solicitation']}"})
            continue
        mine = piids & set(contract_tokens(latest["existing_contract_number"]))
        if mine and detail["type"] in SOL_KINDS and detail["posted"] > chain[0]["release_date"]:
            others = {k for t in mine for k in ctx["incumbent_lines"].get(t, [])} - {pid or latest["record_key"]}
            if not others:
                out.append({"key": key, "line": latest, "basis": "inferred",
                            "why": f"the {detail['type']} of {detail['posted']} cites incumbent contract {', '.join(sorted(mine))}, which only this line cites, "
                                   f"and is solicitation-stage and posted after the line first appeared ({chain[0]['release_date']})"})
    named = set(detail["offices"]) if "offices" in detail else set(specific_offices(resolve_offices(detail["text"]), ctx["parents"]))
    words, by_office = title_tokens(detail["title"]), defaultdict(list)
    for key, chain in ctx["chains"].items():
        latest = chain[-1]
        if latest["release"] not in ctx["current"]:
            continue
        # A notice naming the line's parent alone ("NRL" for a Code 7600 line) must single the line out among every
        # current line under that parent, a stricter test than among the lines of one office.
        for office in named & ({latest["office_id"]} | set(ctx["parents"].get(latest["office_id"], ()))):
            shared = words & title_tokens(latest["requirement_title"])
            if len(shared) >= 3:
                by_office[office].append((key, chain, shared))
    claimed = {c["key"] for c in out}
    for office, hits in sorted(by_office.items()):
        if len(hits) != 1 or hits[0][0] in claimed or detail["posted"] <= hits[0][1][0]["release_date"]:
            continue  # two lines of the office share the words, or the notice predates the line: a reviewer's call
        key, chain, shared = hits[0]
        out.append({"key": key, "line": chain[-1], "basis": "inferred",
                    "why": f"the {detail['type']} of {detail['posted']} names {office}, the line's office or its parent, and shares the "
                           f"title words {', '.join(sorted(shared))}, which no other current line under that office shares"})
    return out


def place_notice(detail: dict, ctx: dict, kin: list[dict] = ()) -> dict:
    """Where a notice's requirement lives: resolved to one forecast line, or created from the notice.

    The notice is read with `kin`, the saved notices under the same solicitation number (an RFI, a
    presolicitation and the RFP are one requirement). resolved: exactly one line of the latest release
    names one of them explicitly (`line_claims`). created: none does, or more than one does, so the
    requirement is keyed on the solicitation number (the notice id when it has none) and the lines that
    share a program name stay candidates a reviewer accepts or rejects. Nothing is forced.
    """
    resolved: dict[str, dict] = {}
    for d in [detail, *kin]:
        for claim in line_claims(d, ctx):
            resolved.setdefault(claim["key"], claim)
    if len(resolved) == 1:
        return {"how": "resolved", **next(iter(resolved.values()))}
    key = f"notice:{compact(detail['solicitation']) or detail['id']}"
    if resolved:
        return {"how": "created", "key": key, "line": None, "basis": None, "ambiguous": list(resolved.values()),
                "note": f"{len(resolved)} forecast lines each claim it explicitly ({', '.join(resolved)}); a reviewer decides, so the notice keeps its own record"}
    return {"how": "created", "key": key, "line": None, "basis": None, "ambiguous": [],
            "note": (f"no line of {ctx['latest']} carries this notice's PID, its solicitation number in a title, or an incumbent contract only that line cites"
                     if ctx["latest"] else "the agency publishes no acquisition forecast, so the notice keeps its own record")
                    + (f" (read with {len(kin)} earlier notice(s) under the same number)" if kin else "")}


# A notice that names only its systems command, its agency or its contracting office names no office a requirement sits in.
NOT_NAMED_TYPES = {"agency", "command", "contracting_office", "person"}


@lru_cache(maxsize=1)
def node_types() -> dict[str, str]:
    seed = json.loads((RESEARCH / "memory" / "organization_seed.json").read_text(encoding="utf-8"))
    return {n["id"]: n["type"] for n in seed["nodes"]}


def lineage(office: str, parents: dict) -> set[str]:
    out, frontier = set(), set(parents.get(office, ()))
    while frontier:
        out |= frontier
        frontier = {p for f in frontier for p in parents.get(f, ())} - out
    return out


def specific_offices(offices: list[dict], parents: dict) -> list[str]:
    """The offices a notice names as current, without their own ancestors: 'PEO C4I ... PMW 740' names PMW 740.
    A center, a field activity or one of their departments is where work is done; named beside a program office
    it is not the owner."""
    types = node_types()
    current = {o["office"] for o in offices if not o["former"] and types.get(o["office"]) not in NOT_NAMED_TYPES}
    performer = set(P.get("performer_types", ()))
    performers = {o for o in current if {types.get(a) for a in lineage(o, parents) | {o}} & performer}
    if current - performers:
        current -= performers
    ancestors = set().union(*(lineage(o, parents) for o in current))
    return sorted(current - ancestors)


# ---------------------------------------------------------------- datapacks

def pack_rows(pack: Path, name: str) -> list[dict]:
    path = pack / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def lrae_lines() -> list[dict]:
    """Every included LRAE row across the saved releases, with its decision and joins."""
    lines = []
    for pack in PACKS:
        joins = defaultdict(list)
        for j in pack_rows(pack, "joins.csv"):
            joins[j["row_number"]].append(j)
        decision = {c["row_number"]: c for c in pack_rows(pack, "rows_classified.csv")}
        source = json.loads((pack / "SOURCE.json").read_text(encoding="utf-8")) if (pack / "SOURCE.json").exists() else {}
        for r in pack_rows(pack, "rows_raw.csv"):
            c = decision.get(r["row_number"])
            if not c or c["include_decision"] != "included":
                continue
            lines.append({**r, "release": pack.name, "release_date": source.get("release_date", ""), "sha": source.get("sha256", "")[:12],
                          "source_url": source.get("source_url", ""), "fetched_from": source.get("fetched_from", ""),
                          "retrieved_at": source.get("retrieved_at", "")[:10], "raw_path": source.get("raw_path", ""),
                          "record_key": c["record_key"], "office_id": c["office_id"], "joins": joins.get(r["row_number"], [])})
    return lines


def release_sources(lines: list[dict]) -> list[str]:
    """One citation per release the lines came from: the spreadsheet URL, how and when it was fetched, its hash."""
    seen: dict[str, dict] = {}
    for l in lines:
        seen.setdefault(l["release"], l)
    return [f"{l['release']}: {l['source_url'] or 'source URL not recorded'} (retrieved {l['retrieved_at'] or '?'}"
            + (f" via {l['fetched_from']}" if l["fetched_from"] and l["fetched_from"] != l["source_url"] else "")
            + f"; sha256 {l['sha']}; saved {l['raw_path'] or '?'})" for _, l in sorted(seen.items())]


def quarter_dates(fy_text: str, quarter: str) -> tuple[date, date] | None:
    """Calendar bounds of a federal fiscal quarter: FY26 Q1 is October to December 2025."""
    fy, q = fiscal_year(fy_text), (quarter or "").strip().upper()
    starts = {"Q1": (-1, 10), "Q2": (0, 1), "Q3": (0, 4), "Q4": (0, 7)}
    if not fy or q not in starts:
        return None
    offset, month = starts[q]
    start = date(fy + offset, month, 1)
    return start, date(start.year, month + 2, {12: 31, 3: 31, 6: 30, 9: 30}[month + 2])


def fiscal_year(text: str) -> int | None:
    m = re.search(r"(\d{2})\b", text or "")
    return 2000 + int(m.group(1)) if m else None


def window(fy: str, quarter: str) -> str:
    return f"{fy} {quarter}".strip() if (fy or quarter) and "TBD" not in f"{fy}{quarter}" else "TBD"


# ---------------------------------------------------------------- database

def query(dsn: str, sql: str) -> list[dict]:
    out = subprocess.run(["psql", dsn, "-At", "-c", sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}")
    return json.loads(out.stdout.strip() or "null") or []


def lit(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ancestry(dsn: str, org_id: str) -> list[dict]:
    return query(dsn, f"""
        with recursive up as (
          select o.id, o.name, o.acronym, o.source_ref, o.parent_organization_id, 1 as depth from public.gov_organizations o where o.id = {lit(org_id)}
          union all
          select p.id, p.name, p.acronym, p.source_ref, p.parent_organization_id, up.depth + 1 from up join public.gov_organizations p on p.id = up.parent_organization_id)
        select json_agg(row_to_json(up) order by depth) from up""")


def org_history(dsn: str, org_id: str) -> list[dict]:
    return query(dsn, f"""
        select json_agg(row_to_json(t)) from (
          select r.source_ref, s.name as source_name, r.relationship_type, t.name as target_name, r.valid_from, r.valid_to, r.confidence
            from public.gov_organization_relationships r
            join public.gov_organizations s on s.id = r.source_organization_id
            join public.gov_organizations t on t.id = r.target_organization_id
           where r.source_organization_id = {lit(org_id)} or r.target_organization_id = {lit(org_id)}
           order by r.valid_from nulls first, r.source_ref) t""")


def find_org(dsn: str, needle: str) -> list[dict]:
    return query(dsn, f"""
        select json_agg(row_to_json(o)) from (
          select id, name, acronym, org_type, source_ref, parent_organization_id, valid_from, valid_to from public.gov_organizations
           where source_ref = {lit(needle)} or acronym = {lit(needle)} or name ilike {lit('%' + needle + '%')}
              or {lit(needle)} = any(aliases) order by name) o""")


def need_record(dsn: str, key: str) -> dict | None:
    needs = query(dsn, f"select json_agg(row_to_json(n)) from (select id, source_key, title, description, lifecycle from public.gov_needs where source_key = {lit(key)}) n")
    if not needs:
        return None
    need = needs[0]
    need["revisions"] = query(dsn, f"""
        select json_agg(row_to_json(t) order by observed_at) from (
          select a.observed_at::date as observed_at, a.source_key, a.basis, a.rationale, rv.statement, rv.expected_from, rv.expected_to,
                 a.supersedes_id is not null as supersedes_prior,
                 not exists (select 1 from public.gov_intelligence_assertions s where s.supersedes_id = a.id) as live
            from public.gov_intelligence_assertions a
            join public.gov_requirement_revisions rv on rv.assertion_id = a.id
            join public.gov_need_requirements rq on rq.id = rv.requirement_id
           where rq.need_id = {lit(need['id'])}) t""")
    need["funding"] = query(dsn, f"""
        select json_agg(row_to_json(t) order by observed_at) from (
          select a.observed_at::date as observed_at, f.measure, f.amount_low, f.amount_high, f.fiscal_year, f.period_start, f.period_end, f.scope_description,
                 not exists (select 1 from public.gov_intelligence_assertions s where s.supersedes_id = a.id) as live
            from public.gov_funding_observations f join public.gov_intelligence_assertions a on a.id = f.assertion_id
           where f.need_id = {lit(need['id'])}) t""")
    need["offices"] = query(dsn, f"""
        select json_agg(row_to_json(t) order by observed_at, role) from (
          select a.observed_at::date as observed_at, no.role, o.id as org_id, o.name, o.acronym,
                 not exists (select 1 from public.gov_intelligence_assertions s where s.supersedes_id = a.id) as live
            from public.gov_need_organizations no join public.gov_intelligence_assertions a on a.id = no.assertion_id
            join public.gov_organizations o on o.id = no.organization_id
           where no.need_id = {lit(need['id'])}) t""")
    need["evidence"] = query(dsn, f"""
        select json_agg(row_to_json(t)) from (
          select distinct e.source_key, e.source_url, e.excerpt, a.source_key as assertion_key, a.observed_at::date as observed_at
            from public.gov_assertion_evidence ae join public.gov_intelligence_evidence e on e.id = ae.evidence_id
            left join public.agency_brain_items i on i.id = e.brain_item_id
            join public.gov_intelligence_assertions a on a.id = ae.assertion_id
            join public.gov_requirement_revisions rv on rv.assertion_id = a.id
            join public.gov_need_requirements rq on rq.id = rv.requirement_id
           where rq.need_id = {lit(need['id'])}) t""")
    return need


def needs_for_office(dsn: str, org_id: str) -> list[dict]:
    return query(dsn, f"""
        select json_agg(row_to_json(t) order by title) from (
          select distinct n.id, n.source_key, n.title from public.gov_needs n
            join public.gov_need_organizations no on no.need_id = n.id and no.role = 'originating_requirement_owner'
           where no.organization_id = {lit(org_id)}
             and n.source_key not like 'notice:%') t""")  # forecast lines only; the office's notices print as its own stream


# ---------------------------------------------------------------- readings

def money_block(estimates: list[str], ceilings: list[tuple[str, object]], obligations: list[tuple[str, object]]) -> list[str]:
    """Three kinds of money, three lines, never one number."""
    fmt = lambda v: f"${float(v):,.0f}" if v not in (None, "", 0, "0", 0.0) else ("$0" if v in (0, "0", 0.0) else "not stated")  # noqa: E731
    return [f"  estimate (LRAE anticipated total value, as stated): {'; '.join(estimates) or 'none'}",
            f"  ceiling (base and all options, USAspending/FPDS):  {'; '.join(f'{p} {fmt(v)}' for p, v in ceilings) or 'none collected'}",
            f"  obligated to date (USAspending total_obligation):   {'; '.join(f'{p} {fmt(v)}' for p, v in obligations) or 'none collected'}"]


SOL_KINDS = {"solicitation", "presolicitation", "combined synopsis/solicitation"}
INCUMBENT_ACTION_KINDS = {"award notice", "justification (j&a)", "justification", "special notice"}
MARKET_KINDS = {"sources sought", "special notice"}


def reading(sol_window: str, notices: list[dict], awards: list[dict], incumbent_last: str, instrument: str, today: date, searched: str = "", *,
            incumbent_actions: list[dict] = (), award_windows: list[str] = (), restructured: list[str] = (), dropped: str = "", candidate: str = "") -> str:
    """One deterministic sentence on where a forecast line stands, its outcome word first.

    awarded, solicited, cancelled, review, restructured, delayed, open, not yet due, not dated: the
    first that holds leads; states that also hold follow as "also ...". `notices` are the line's own
    (the PID or its solicitation number in the text); `incumbent_actions` cite the incumbent contract
    and only ever ask for review. A candidate is appended, never promoted. A negative is scoped to
    the saved SAM.gov searches and their latest retrieval date; the saved records holding nothing
    is what is known, not that nothing happened.
    """
    def kinds(ns):
        return ", ".join(sorted({n.get("type", "").lower() for n in ns}))

    def latest_of(ns):
        return max((n.get("posted") or "" for n in ns), default="")

    searched_in = "the saved SAM.gov searches"
    stage = [n for n in notices if n.get("type", "").lower() in SOL_KINDS]
    live_stage = [n for n in stage if not n.get("cancelled")]
    cancelled = [n for n in stage if n.get("cancelled")]
    award_notices = [n for n in notices if n.get("type", "").lower() == "award notice"]
    # The line's own justification or special notice: a sole-source intent, a changed plan or an
    # industry day on this requirement. It states nothing about a solicitation, so no stage branch
    # catches it, and without this the line reads delayed while its own J&A sits in the record.
    own_actions = [n for n in notices if n.get("type", "").lower() in INCUMBENT_ACTION_KINDS - {"award notice"}]
    market = [n for n in notices if n.get("type", "").lower() in MARKET_KINDS and n not in own_actions]
    parts = (sol_window or "").split()
    dates = quarter_dates(parts[0], parts[1]) if len(parts) == 2 else None
    closed = bool(dates and today > dates[1])
    moved = f"award window moved {' -> '.join(award_windows)} across releases" if len(award_windows) > 1 else ""
    also: list[str] = []
    if awards:
        first = min(a["signed"] for a in awards if a.get("signed")) if any(a.get("signed") for a in awards) else "date unstated"
        primary = f"awarded: {len(awards)} action(s) under the line's solicitation, first signed {first}"
    elif award_notices:
        n = award_notices[-1]
        primary = f"awarded: award notice posted {n.get('posted')} [{n.get('solicitation') or 'no number'}] naming this line"
    elif live_stage:
        primary = f"solicited: {kinds(live_stage)} posted {latest_of(live_stage)}"
        if cancelled:
            also.append(f"an earlier {kinds(cancelled)} of {latest_of(cancelled)} is marked cancelled on SAM.gov")
    elif cancelled:
        primary = f"cancelled: {kinds(cancelled)} posted {latest_of(cancelled)} is marked cancelled on SAM.gov; {not_found('later solicitation', searched_in, searched)}"
    elif dropped:
        primary = f"review: {dropped}"
    elif own_actions:
        primary = (f"review: {kinds(own_actions)} posted {latest_of(own_actions)} carries this line's own identifier "
                   "(a justification, a sole-source intent or a special notice on the requirement itself); "
                   f"{not_found('solicitation', searched_in, searched)}")
    elif incumbent_actions:
        piids = sorted({n.get("key", "") for n in incumbent_actions})
        primary = (f"review: {kinds(incumbent_actions)} posted {latest_of(incumbent_actions)} on incumbent {', '.join(piids)} "
                   "(an action on the incumbent: a modification, extension or sole-source continuation; does the follow-on still stand?); "
                   f"{not_found('follow-on solicitation', searched_in, searched)}")
    elif restructured:
        primary = "restructured: " + "; ".join(restructured)
    elif not dates:
        primary = f"not dated: solicitation window {sol_window or 'blank'}; {not_found('public notice', searched_in, searched)}"
    elif today < dates[0]:
        primary = f"not yet due: solicitation window {sol_window} opens in {(dates[0] - today).days} days"
    elif not closed:
        primary = f"open: solicitation window {sol_window} runs to {dates[1]}; {not_found('public notice', searched_in, searched)}"
    else:
        primary = f"delayed: solicitation window {sol_window} closed {(today - dates[1]).days} days ago; {not_found('public notice', searched_in, searched)}"
        if moved:
            primary += f"; {moved}"
    word = primary.split(":")[0]
    if restructured and word != "restructured":
        also.append("also restructured: " + "; ".join(restructured))
    if closed and word in ("review", "restructured"):
        also.append(f"also delayed: solicitation window {sol_window} closed {(today - dates[1]).days} days ago" + (f"; {moved}" if moved else ""))
    elif moved and word in ("review", "restructured", "open", "not yet due", "not dated"):
        also.append(moved)
    if market and word not in ("awarded", "solicited"):
        also.append(f"market research only so far: {kinds(market)} posted {latest_of(market)}")
    if word in ("delayed", "open", "not dated"):
        if "order" in (instrument or "").lower():
            also.append("SeaPort/GSA order competitions are not posted on SAM.gov")
        if incumbent_last:
            also.append(f"incumbent last acted {incumbent_last}")
    if candidate:
        also.append(candidate)
    return "; ".join([primary] + also)


def notice_summaries(line: dict, hits: dict[str, dict], ctx: dict | None = None) -> list[dict]:
    """Notices the datapack joined to the line by a key in the notice text: the PID (`via` pid) or an incumbent
    contract number (`via` incumbent, with the other latest-release lines that cite the same contract in `shared`)."""
    out = []
    me = line["pid"] or line["record_key"]
    for j in line["joins"]:
        if j["join_type"] == "notice" and j["target_id"].startswith("sam:"):
            h = hits.get(j["target_id"][4:])
            if h:
                via = "pid" if j["key_used"] == line["pid"] else "incumbent"
                shared = (ctx or {}).get("incumbent_lines", {}).get(j["key_used"], []) if via == "incumbent" else []
                out.append({**h, "method": j["method"], "key": j["key_used"], "via": via, "shared": [k for k in shared if k != me]})
    return out


def title_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def distinctive_tokens(text: str) -> set[str]:
    """Program names and codes: words the source wrote in capitals or with a digit (NTCDL, MIDS-LVT, SF2, GPNTS)."""
    out = set()
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9/-]*", text or ""):
        for part in re.split(r"[/-]", raw):
            # A bare number (a lot, a DO, a year) names nothing; a code needs a letter.
            if len(part) >= 3 and re.search(r"[A-Za-z]", part) and (part.isupper() or re.search(r"\d", part)) and part.lower() not in STOP:
                out.add(part.lower())
    return out


SOLICITATION_STAGE = ("Presolicitation", "Solicitation", "Combined Synopsis/Solicitation")
# A line whose title says it bridges, extends or stands in for another is read as carved out of it.
BRIDGE_RE = re.compile(r"\b(bridge|interim|extension|stand[- ]alone|gap[- ]?filler)\b", re.I)

# A lot, family, increment, phase, block or version number in a title. Two titles that carry
# different ones name different buys in one program.
GEN_RE = re.compile(r"\b(?:SF|SWARMM\s+Family|Family|Lot|Increment|Inc\.?|Phase|Block|Spiral|Generation|Gen|Version|Ver\.?)\s*-?\s*(\d{1,2}|[IVX]{1,4})\b", re.I)
ROMAN_RE = re.compile(r"\b([IVX]{2,4})\b")  # "C4ISR Training Services III"
ROMAN = {"I": 1, "V": 5, "X": 10}


def roman_to_int(text: str) -> int:
    total = 0
    for i, ch in enumerate(text.upper()):
        value = ROMAN[ch]
        total += -value if i + 1 < len(text) and ROMAN[text[i + 1].upper()] > value else value
    return total


def generations(text: str) -> set[int]:
    """Generation numbers a title states; empty when it states none."""
    out = set()
    for m in GEN_RE.finditer(text or ""):
        out.add(int(m.group(1)) if m.group(1).isdigit() else roman_to_int(m.group(1)))
    for m in ROMAN_RE.finditer(text or ""):  # ponytail: a bare II..XV is read as a generation; a stray acronym would too
        out.add(roman_to_int(m.group(1)))
    return out


def gen_label(text: str) -> str:
    return ", ".join(str(g) for g in sorted(generations(text))) or "none stated"


def relation_of(line_title: str, notice_title: str) -> str:
    """'candidate' when two titles may name one buy; 'related' when each states a different generation."""
    a, b = generations(line_title), generations(notice_title)
    return "related" if a and b and not (a & b) else "candidate"


def match_context() -> dict:
    """What matching and reading need once: how rare each program token is across the latest release, who is
    whose parent, the accepted chains (lrae_package.fold_map: the connections the loader persists) and which
    latest-release lines cite each incumbent contract."""
    rarity: dict[str, int] = defaultdict(int)
    folded, _ = fold_map()
    chains: dict[str, list[dict]] = defaultdict(list)
    canon: dict[tuple[str, str], str] = {}
    incumbent_lines: dict[str, list[str]] = defaultdict(list)
    for l in lrae_lines():
        tie = folded.get((l["release"], l["record_key"]))
        key = tie["key"] if tie else l["record_key"]
        canon[(l["release"], l["record_key"])] = key
        chains[key].append({**l, "tie": tie})
        if l["release"] in CURRENT:
            for t in distinctive_tokens(l["requirement_title"]):
                rarity[t] += 1
            for t in contract_tokens(l["existing_contract_number"]):
                incumbent_lines[t].append(l["pid"] or l["record_key"])
    for lines in chains.values():
        lines.sort(key=lambda l: (l["release_date"], l["release"]))
    seed = json.loads((RESEARCH / "memory" / "organization_seed.json").read_text(encoding="utf-8"))
    parents = defaultdict(set)
    for r in seed["relationships"]:
        if r["type"] == "child_of" and r["review_status"] != "retracted":
            parents[r["from"]].add(r["to"])
    return {"rarity": rarity, "parents": parents, "offices_of": {}, "chains": chains, "canon": canon, "incumbent_lines": incumbent_lines,
            "current": CURRENT, "latest": ", ".join(sorted(CURRENT))}


def siblings_of(line: dict, ctx: dict) -> list[dict]:
    """Other latest-release lines under the line's office that share a rare program token or an incumbent contract with it."""
    # The chains are keyed by the canonical key of the fold, which is not always the row's own record
    # key. Comparing a row key against a chain key would let a folded line meet itself here.
    me = ctx["canon"].get((line["release"], line["record_key"]), line["record_key"])
    my_tokens = {t for t in distinctive_tokens(line["requirement_title"]) if ctx["rarity"].get(t, 0) <= 3}
    my_piids = set(contract_tokens(line["existing_contract_number"]))
    out = []
    for key, chain in ctx["chains"].items():
        other = chain[-1]
        if key == me or other["release"] not in ctx["current"] or other["office_id"] != line["office_id"]:
            continue
        if my_tokens & distinctive_tokens(other["requirement_title"]) or my_piids & set(contract_tokens(other["existing_contract_number"])):
            out.append(other)
    return sorted(out, key=lambda l: l["pid"] or l["record_key"])


def restructure_notes(chain: list[dict], ctx: dict) -> list[str]:
    """What the latest release restated about a line between two stated values, and whether the requirement split.

    A blank or TBD filled in is a definition, not a restructuring, so only a stated value that became a
    different stated value counts. A sibling line that first appears in the latest release under the same
    office is read as a split when it cites the same incumbent contract, or when it shares a rare program
    name and its own title says bridge, interim, extension or stand-alone (a bridge carved out of a
    follow-on). A new order line in a program with many is neither, and an older sibling is context.
    """
    out: list[str] = []
    if len(chain) > 1:
        prev, cur = chain[-2], chain[-1]
        for field, label in (("procurement_method", "method"), ("procurement_instrument", "instrument"), ("contract_type", "contract type"),
                             ("follow_on_or_new", "follow-on or new"), ("anticipated_total_value", "value as stated")):
            a, b = (prev.get(field) or "").strip(), (cur.get(field) or "").strip()
            if a and b and a.upper() != "TBD" and b.upper() != "TBD" and norm_title(a) != norm_title(b):
                out.append(f"{label} {a} -> {b} between {prev['release']} and {cur['release']}")
        a, b = (prev.get("office_code_string") or "").split(" - ")[0].strip(), (cur.get("office_code_string") or "").split(" - ")[0].strip()
        if a and b and a.upper() != b.upper():
            out.append(f"office code {a} -> {b} between {prev['release']} and {cur['release']}")
    cur = chain[-1]
    if cur["release"] in ctx["current"]:
        for other in siblings_of(cur, ctx):
            other_key = ctx["canon"].get((other["release"], other["record_key"]), other["record_key"])
            if other_key == ctx["canon"].get((cur["release"], cur["record_key"]), cur["record_key"]):
                continue  # a line is not a sibling of itself
            if len(ctx["chains"].get(other_key, [])) != 1:
                continue  # it stood in an earlier release too, so the latest one did not add it
            shared = set(contract_tokens(cur["existing_contract_number"])) & set(contract_tokens(other["existing_contract_number"]))
            bridge = BRIDGE_RE.search(other["requirement_title"] or "")
            if not shared and not bridge:
                continue
            how = f"the incumbent {', '.join(sorted(shared))}" if shared else f"a program name, and its title says '{bridge.group(1)}'"
            out.append(f"sibling line {other['pid'] or other['record_key']} ({other['requirement_title'][:45]}) first appears in {other['release']} "
                       f"under the same office sharing {how}: a split, or a new line beside this one")
    return out


def notice_offices(notice_id: str, ctx: dict) -> set[str]:
    """Program offices a harvested notice names as current (not 'formerly'); empty when the detail is not saved."""
    if notice_id not in ctx["offices_of"]:
        d = notice_detail(notice_id)
        ctx["offices_of"][notice_id] = set(specific_offices(resolve_offices(d["text"]), ctx["parents"])) if d else set()
    return ctx["offices_of"][notice_id]


RELATIONS = ("same office", "notice names the line's parent", "line filed under the notice office's parent code")


def related_office(line_office: str, notice_office: str, parents: dict) -> str:
    if line_office == notice_office:
        return "same office"
    if notice_office in parents.get(line_office, ()):
        return "notice names the line's parent"
    if line_office in parents.get(notice_office, ()):
        return "line filed under the notice office's parent code"
    return ""


def closest_relation(line_office: str, notice_offices_: set[str], parents: dict) -> str:
    """The closest relation any of the notice's offices has to the line's, whatever order the set holds."""
    relations = {related_office(line_office, o, parents) for o in notice_offices_}
    return next((r for r in RELATIONS if r in relations), "")


def candidate_notices(line: dict, hits: dict[str, dict], ctx: dict) -> list[dict]:
    """Saved solicitation-stage hits that may be this line: candidates a reviewer accepts or rejects, never matches.

    Two titles are related when they share a program name or code (a capitalised word or one
    with a digit) that few lines carry, or two such tokens, or three words overall; generic
    procurement words never count. When the notice text is saved and names a current office
    that is neither the line's office nor its parent or child, the hit is dropped, and the
    office comparison travels with every candidate that survives. A hit whose title states a
    different lot, family or generation from the line's comes back with method `related`: a
    procurement in the same program that is context for this line and never its solicitation.
    """
    words, codes = title_tokens(line["requirement_title"]), distinctive_tokens(line["requirement_title"])
    # A notice posted more than two fiscal years before the line's own window solicited something else.
    due = fiscal_year(line.get("solicitation_fy", "")) or fiscal_year(line.get("award_fy", ""))
    earliest = f"{due - 3}-10-01" if due else ""
    out = []
    for h in hits.values():
        if h["type"] not in SOLICITATION_STAGE or (earliest and h["posted"] and h["posted"] < earliest):
            continue
        shared = sorted(words & title_tokens(h["title"]))
        shared_codes = sorted(codes & distinctive_tokens(h["title"]))
        rare = [t for t in shared_codes if ctx["rarity"].get(t, 0) <= 3]
        if not (rare or len(shared_codes) >= 2 or len(shared) >= 3):
            continue
        offices = notice_offices(h["id"], ctx)
        relation = ""
        if offices:
            relation = closest_relation(line["office_id"], offices, ctx["parents"])
            if not relation:
                continue  # the notice names a current office unrelated to this line
        basis = "shared program tokens " + ", ".join(shared_codes or shared[:4])
        if relation:
            basis += f"; {relation}"
        elif notice_detail(h["id"]) is not None:
            basis += "; notice text names no office the memory knows"
        else:
            basis += "; notice office not read (detail not saved)"
        kind = relation_of(line["requirement_title"], h["title"])
        if kind == "related":
            basis += (f"; different generation (line {gen_label(line['requirement_title'])}, notice {gen_label(h['title'])}): "
                      "a related procurement in the same program, not this buy")
        out.append({**h, "method": kind, "key": basis})
    return sorted(out, key=lambda h: h["posted"])


def incumbent_recency(rows: list[dict], line: dict) -> tuple[str, str]:
    """Latest action on the incumbent contract(s): USAspending's last-modified date when saved, else the first FPDS page."""
    last, source = "", ""
    for token in contract_tokens(line["existing_contract_number"]):
        u = usaspending(rows, token)
        if u and u["last_modified"]:
            last, source = max(last, u["last_modified"]), "usaspending"
            continue
        actions, _, complete = fpds_by_piid(rows, token)
        if actions:
            last = max([last] + [a["signed"] for a in actions if a["signed"]])
            source = source or ("fpds" if complete else "fpds, history not saved to its last page")
    return last, source


def notice_role(n: dict, first_release: str) -> str:
    """What a joined notice is to the line: `line`, `incumbent history`, `candidate` or `incumbent action`.

    A notice carrying the PID is the line's own, whatever kind it is. Every other notice reached the
    line through the incumbent contract number: posted before the line first appeared in a forecast it
    belongs to the incumbent's own buy, whatever kind it is (a sources sought of 2020 researched the
    market for the contract, not for a line first forecast in 2025); posted after, a solicitation-stage
    notice is the line's own only where the contract is cited by no other line and the join is explicit,
    and an action on the incumbent asks for review.
    """
    kind = (n["type"] or "").lower()
    if n["via"] == "pid":
        return "line"
    if n["posted"] and first_release and n["posted"] < first_release and kind in SOL_KINDS | INCUMBENT_ACTION_KINDS | MARKET_KINDS:
        return "incumbent history"
    if kind in SOL_KINDS:
        return "candidate" if (n["shared"] or n["method"] != "explicit") else "line"
    if kind in INCUMBENT_ACTION_KINDS:
        return "incumbent action"
    return "line"


def line_status(line: dict, rows: list[dict], hits: dict[str, dict], examples: dict[str, dict], today: date, ctx: dict, dropped: str = "") -> dict:
    """Where one forecast line stands, from its chain, the saved notices and the saved awards.

    A notice carrying the PID is the line's own. A notice carrying the incumbent contract is the line's
    own only when it is solicitation-stage, the contract is cited by no other line, and it was posted
    after the line first appeared in a forecast. Posted before that it is the incumbent's own history
    (its procurement, its extensions, the sources sought that preceded it) and drives nothing, whatever
    its kind; cited by several lines or through a vehicle it is a candidate; and an award notice, J&A or
    special notice on the incumbent posted after the line first appeared asks for review.
    """
    key = ctx["canon"].get((line["release"], line["record_key"]), line["record_key"])
    chain = ctx["chains"].get(key) or [line]
    first_release = chain[0]["release_date"]
    joined = notice_summaries(line, hits, ctx)
    notices, incumbent_actions, own, shared_stage = [], [], [], []
    bucket = {"line": notices, "incumbent history": own, "candidate": shared_stage, "incumbent action": incumbent_actions}
    for n in joined:
        bucket[notice_role(n, first_release)].append(n)
    known = {n["id"] for n in joined}
    nearby = [c for c in candidate_notices(line, hits, ctx) if c["id"] not in known]
    candidates = [c for c in nearby if c["method"] == "candidate"]
    for n in shared_stage:
        how = f"cites incumbent {n['key']}" + (f", which {len(n['shared']) + 1} lines share ({', '.join(n['shared'])} too)" if n["shared"] else " through a shared vehicle")
        candidates.append({**n, "method": "candidate", "key": how})
    candidates.sort(key=lambda c: c["posted"])
    related = [c for c in nearby if c["method"] == "related"]
    _, searched = search_dates(rows)
    sols = [compact(m) for m in SOL_RE.findall(line["requirement_title"].replace(" ", ""))]
    for n in notices:
        if n["solicitation"] and compact(n["solicitation"]) not in sols and n["type"] in SOLICITATION_STAGE:
            sols.append(compact(n["solicitation"]))
    candidate_sols = [compact(c["solicitation"]) for c in candidates if c["solicitation"] and compact(c["solicitation"]) not in sols]
    awards, candidate_awards = [], []
    for sol in sols:
        actions, _ = fpds_by_solicitation(rows, sol)
        awards += [a for a in actions if a["mod"] in ("0", "")]
    for sol in dict.fromkeys(candidate_sols):
        actions, _ = fpds_by_solicitation(rows, sol)
        candidate_awards += [{**a, "via": sol} for a in actions if a["mod"] in ("0", "")]
    incumbent_last, recency_source = incumbent_recency(rows, line)
    example = examples.get(line["pid"])
    sol_window = window(line["solicitation_fy"], line["solicitation_quarter"])
    windows = [window(l["award_fy"], l["award_quarter"]) for l in chain]
    award_windows = [w for i, w in enumerate(windows) if i == 0 or w != windows[i - 1]]
    candidate = ""
    if candidates:
        c = candidates[-1]
        candidate = f"candidate: {c['type'].lower()} posted {c['posted']} [{c['solicitation'] or c['id'][:12]}] ({c['key']})"
        # Only the named candidate's own awards: the others' solicitations are not "that solicitation".
        named = [a for a in candidate_awards if a["via"] == compact(c["solicitation"] or "")]
        if named:
            first = min(a["signed"] for a in named)
            candidate += f"; {len(named)} award action(s) under that solicitation, first signed {first}"
        candidate += "; a reviewer decides whether it is this line"
    verdict = reading(sol_window, notices, awards, incumbent_last, line["procurement_instrument"], today, searched,
                      incumbent_actions=incumbent_actions, award_windows=award_windows, restructured=restructure_notes(chain, ctx),
                      dropped=dropped, candidate=candidate)
    if own:
        verdict += (f"; the incumbent's own {', '.join(f"{n['type'].lower()} {n['posted']}" for n in sorted(own, key=lambda n: n['posted']))} "
                    f"predate{'s' if len(own) == 1 else ''} the line's first forecast appearance ({first_release}): the incumbent's history, not this line's solicitation")
    return {"pid": line["pid"] or line["record_key"], "key": key, "title": line["requirement_title"], "office": line["office_id"],
            "sol": sol_window, "award": window(line["award_fy"], line["award_quarter"]),
            "value": line["anticipated_total_value"], "instrument": line["procurement_instrument"],
            "notices": joined, "candidates": candidates, "related": related, "awards": awards, "candidate_awards": candidate_awards,
            "incumbent_last": incumbent_last + ("*" if recency_source.startswith("fpds,") else ""),
            "linked_award": f"{example['identifier']} ({example['id']}, reviewed attribution)" if example else "",
            "outcome": verdict.split(":")[0], "reading": verdict}


def paired_rows(key: str) -> dict[tuple[str, str], str]:
    """(release, row) -> match basis, for rows the release diffs paired with this key (confirmed or candidate)."""
    out: dict[tuple[str, str], str] = {}
    for pack in PACKS:
        for path in sorted(pack.glob("diff_*.csv")):
            m = re.match(r"diff_(lrae_navwar_[\d-]+)_(lrae_navwar_[\d-]+)\.csv", path.name)
            if not m:
                continue
            older, newer = m.groups()
            for ch in pack_rows(pack, path.name):
                if ch["key"] == key and ch["change"] in ("unchanged", "changed"):
                    basis = f"{ch['key_method']} ({ch['confidence']})"
                    if ch["old_row"]:
                        out[(older, ch["old_row"])] = basis
                    if ch["new_row"]:
                        out[(newer, ch["new_row"])] = basis
    return out


def seed_index() -> dict:
    """The organization memory by id: nodes, observations, relationships, and live child_of edges by (child, parent)."""
    seed = json.loads((RESEARCH / "memory" / "organization_seed.json").read_text(encoding="utf-8"))
    return {"nodes": {n["id"]: n for n in seed["nodes"]},
            "obs": {o["id"]: o for o in seed["observations"]},
            "rels": {r["id"]: r for r in seed["relationships"]},
            "child_of": {(r["from"], r["to"]): r for r in seed["relationships"]
                         if r["type"] == "child_of" and r["review_status"] != "retracted"}}


def evidence_of(obs_ids: list[str], idx: dict, limit: int = 2) -> str:
    """The observations behind a claim, newest last: id, date and source URL, with a count of the older ones."""
    obs = sorted((idx["obs"][o] for o in obs_ids or [] if o in idx["obs"]), key=lambda o: o["observed_at"])
    if not obs:
        return "no observation cited"
    shown = obs[-limit:]
    return (f"+{len(obs) - limit} earlier; " if len(obs) > limit else "") + "; ".join(f"{o['id']} {o['observed_at']} {o['source_url']}" for o in shown)


def succession(dsn: str, org_id: str) -> list[dict]:
    """Successor edges documented for the office or an ancestor, each with the organization it is documented for."""
    return query(dsn, f"""
        with recursive up as (
          select o.id, o.name, o.source_ref, o.parent_organization_id from public.gov_organizations o where o.id = {lit(org_id)}
          union all
          select p.id, p.name, p.source_ref, p.parent_organization_id from up join public.gov_organizations p on p.id = up.parent_organization_id)
        select json_agg(row_to_json(t)) from (
          select up.name as ancestor, up.source_ref as ancestor_ref, s.name as successor, s.source_ref as successor_ref,
                 r.valid_from, r.source_ref, r.source_url
            from up join public.gov_organization_relationships r on r.target_organization_id = up.id and r.relationship_type = 'successor_to'
            join public.gov_organizations s on s.id = r.source_organization_id order by r.valid_from, r.source_ref) t""")


def print_succession(dsn: str, org_id: str, chain: list[dict], idx: dict, indent: str) -> None:
    """A reorganization is reported at the level the source documents it, never pushed down onto the office.

    PEO C4I's mission-systems elements were consolidated into PAE Mission Systems; the release
    does not itemize offices. An office is placed under the successor only when its own source
    says so (then it is in the ancestry above); otherwise the succession prints as its
    ancestor's, with the office's own parent claim and when it was last confirmed.
    """
    office = chain[0]
    for s in succession(dsn, org_id):
        rel = idx["rels"].get(s["source_ref"]) or {}
        scope = rel.get("scope_as_stated") or "scope not stated by the source"
        level = "this office itself" if s["ancestor_ref"] == office["source_ref"] else f"an ancestor ({s['ancestor_ref']})"
        print(f"{indent}succession documented for {level}: {s['ancestor']} -> {s['successor']} from {s['valid_from'] or 'an undated release'} "
              f"({s['source_ref']}; evidence {evidence_of(rel.get('observation_ids'), idx)})")
        print(f"{indent}  scope as the source states it: {scope}")
        if s["ancestor_ref"] != office["source_ref"]:
            claim = idx["child_of"].get((office["source_ref"], s["ancestor_ref"]))
            if claim:
                status = claim["current_status"]
                held = f"{claim['id']} is {status['state']} as of {status['as_of']}" + (f": {status['note']}" if status.get("note") else "")
            else:
                held = "no parent claim in the memory ties it to that ancestor"
            print(f"{indent}  this office under {s['successor']}: not established by any loaded source; its parent claim {held}")


def print_names(node: dict, idx: dict, indent: str = "  ") -> None:
    """Every name and code the sources have used for the office, with the observation dates each rests on."""
    entries = [(node["name"], node.get("observation_ids") or [])] + [(a["text"], a["observation_ids"]) for a in node.get("aliases") or []]
    print(f"{indent}names over time ({len(entries)} in the organization memory; a name is only as current as its latest observation):")
    for text, obs_ids in entries:
        obs = sorted((idx["obs"][o] for o in obs_ids if o in idx["obs"]), key=lambda o: o["observed_at"])
        if not obs:
            print(f"{indent}  {text!r}: no observation cited")
            continue
        span = obs[0]["observed_at"] if obs[0]["observed_at"] == obs[-1]["observed_at"] else f"{obs[0]['observed_at']} .. {obs[-1]['observed_at']}"
        print(f"{indent}  {text!r}: observed {span} ({len(obs)}; latest {obs[-1]['id']} {obs[-1]['source_url']})")
    if node.get("codes"):
        print(f"{indent}  codes: {', '.join(f'{k} {v}' for k, v in node['codes'].items())}")


def print_office(dsn: str, org_id: str, idx: dict, indent: str = "  ") -> None:
    chain = ancestry(dsn, org_id)
    print(f"{indent}ancestry today: {' -> '.join(a['name'] for a in chain)}")
    for child, parent in zip(chain, chain[1:]):
        rel = idx["child_of"].get((child["source_ref"], parent["source_ref"]))
        if rel:
            status = rel["current_status"]
            print(f"{indent}  {child['source_ref']} under {parent['source_ref']}: {rel['id']}, from {rel['effective_from'] or 'start unstated'} "
                  f"({rel['effective_dates_status']} dates), {status['state']} as of {status['as_of']}; evidence {evidence_of(rel['observation_ids'], idx)}")
        else:
            print(f"{indent}  {child['source_ref']} under {parent['source_ref']}: parent column set, no child_of claim found in the memory")
    print_succession(dsn, org_id, chain, idx, indent)
    for h in org_history(dsn, org_id):
        rel = idx["rels"].get(h["source_ref"])
        print(f"{indent}history: {h['source_name']} {h['relationship_type']} {h['target_name']} "
              f"[{h['valid_from'] or 'start unstated'} .. {h['valid_to'] or 'open'}] ({h['source_ref']}"
              + (f"; evidence {evidence_of(rel.get('observation_ids'), idx)})" if rel else ")"))


def attribution_examples() -> dict[str, dict]:
    rows = memory_file("attribution_examples.json", [])
    return {(x.get("related") or {}).get("forecast_pid", ""): x for x in rows if (x.get("related") or {}).get("forecast_pid")}


# ---------------------------------------------------------------- commands

def cmd_status(args) -> int:
    if not RELEASES:
        print(f"# {P['short']} publishes no forecast, so there are no forecast lines to read; the notices and awards are read by ask and pulse")
        return 0
    rows, hits, examples, today, ctx = manifest(), sgs_hits(manifest()), attribution_examples(), date.today(), match_context()
    latest = ", ".join(sorted(CURRENT))
    lines = [l for l in lrae_lines() if l["release"] in CURRENT]
    # an id as it stands, or a name or acronym through the memory's aliases (PEO AVIATION - UAS is pm:peo-aviation--uas),
    # with the offices under it: PEO STRI's lines sit at its program offices
    offices = {args.office, *specific_offices(resolve_offices(args.office), ctx["parents"])} if args.office else set()
    if args.office:
        lines = [l for l in lines if offices & ({l["office_id"]} | lineage(l["office_id"], ctx["parents"]))]
    through = 2000 + int(args.through)
    due = [l for l in lines if (fiscal_year(l["solicitation_fy"]) or 9999) <= through]
    first, last = search_dates(rows)
    fpds = sorted(r["retrieved_at"][:10] for r in rows if kept_page(r) and "fpds.gov" in r.get("url", ""))
    print(f"# Forecast lines in {latest} with a solicitation window through FY{args.through}: {len(due)} of {len(lines)} included lines")
    searches = f"saved SAM.gov searches (retrieved {first} to {last})" if first else "no saved SAM.gov search"
    lookups = f"FPDS lookups (retrieved {fpds[0]} to {fpds[-1]})" if fpds else "no saved FPDS lookup"
    print(f"# Read on {today} against {searches}, {lookups} "
          "and attribution examples. Nothing here is fetched live: a negative reading says the saved records hold nothing, not that nothing happened.")
    print("# Reading column, outcome word first: awarded (FPDS action under the line's solicitation, or an award notice naming it); solicited (a solicitation-stage notice naming it); "
          "cancelled (that notice is marked cancelled on SAM.gov); review (an award notice, J&A or special notice on the incumbent contract posted after the line first appeared in a forecast, or a line missing from the latest release); "
          "restructured (a stated method, instrument, type or value changed between releases, or a sibling line split off in the latest one); delayed (window closed, nothing found); open; not yet due; not dated. "
          "States that also hold follow 'also'. A candidate is appended, never promoted: a reviewer decides.")
    print("# Notice column: joins by a key in the notice text first (the PID, or the incumbent contract number), then candidates marked ~: a shared program name or code that few lines carry, with the notice's own office compared to the line's, "
          "or a solicitation-stage notice citing an incumbent that other lines share. 'related:' marks a notice for a different lot, family or generation of the same program: context, never this line's solicitation.")
    print("# Incumbent column: USAspending last-modified date; a * means only the first FPDS page was saved and later actions may exist.")
    print()
    print("| PID | title | office | sol | award | value as stated | notice | incumbent last action | award found | reading |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    counts, with_candidate = defaultdict(int), 0
    for l in sorted(due, key=lambda l: (l["office_id"], l["solicitation_fy"], l["solicitation_quarter"], l["requirement_title"])):
        s = line_status(l, rows, hits, examples, today, ctx)
        counts[s["outcome"]] += 1
        with_candidate += bool(s["candidates"])
        notice = "; ".join(f"{n['type']} {n['posted']} ({n['solicitation'] or n['id'][:8]})" for n in s["notices"][:2])
        notice = "; ".join(x for x in [notice] + [f"~{c['type']} {c['posted']} ({c['solicitation'] or c['id'][:8]})" for c in s["candidates"][-2:]]
                           + [f"related: {c['type']} {c['posted']} ({c['solicitation'] or c['id'][:8]})" for c in s["related"][-2:]] if x) or "-"
        awards = "; ".join(f"{a['piid']} {a['signed']}" for a in s["awards"][:3]) or \
            "; ".join(f"~{a['piid']} {a['signed']}" for a in s["candidate_awards"][:3]) or (s["linked_award"] or "-")
        print(f"| {s['pid']} | {s['title'][:48]} | {s['office']} | {s['sol']} | {s['award']} | {s['value']} | {notice} | {s['incumbent_last'] or '-'} | {awards} | {s['reading'][:260]} |")
    # Per activity, the rows of its previous release that its newest release no longer carries.
    latest_keys = {ctx["canon"][(l["release"], l["record_key"])] for l in lines}
    dropped, carried_by = [], {}
    for keys in ORDER.values():
        if len(keys) < 2 or keys[-2] in CURRENT:
            continue
        previous, newest = keys[-2], keys[-1]
        diff = ROOT / "datapack" / newest / f"diff_{previous}_{newest}.csv"
        paired_old = {ch["old_row"] for ch in pack_rows(diff.parent, diff.name) if ch["change"] in ("unchanged", "changed")}
        for l in lrae_lines():
            if (l["release"] == previous and (not args.office or l["office_id"] in offices)
                    and ctx["canon"][(l["release"], l["record_key"])] not in latest_keys and l["row_number"] not in paired_old):
                dropped.append(l)
                carried_by[(previous, l["row_number"])] = newest
    if dropped:
        print(f"\n## Lines of a previous release with no row in its activity's newest release: {len(dropped)}, each to review")
        print("# A forecast row that disappears is not a cancellation; no release states one. Nothing here is paired to a latest-release row by PID, exact title or similar title "
              "(candidates included), so a reviewer decides whether each was awarded, folded into another line or dropped. Notice and award columns read the saved records as above.")
        print()
        print("| key | title | office | sol | award | value as stated | notice | award found | reading |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for l in sorted(dropped, key=lambda l: (l["office_id"], l["solicitation_fy"], l["solicitation_quarter"], l["requirement_title"])):
            s = line_status(l, rows, hits, examples, today, ctx,
                            dropped=f"not in {carried_by[(l['release'], l['row_number'])]}, last carried by {l['release']} row {l['row_number']}; "
                                    "no pairing by PID, exact title or similar title; "
                                    "no cancellation stated in the saved records; a reviewer decides whether it was awarded, folded into another line or dropped")
            counts[s["outcome"]] += 1
            notice = "; ".join(f"{n['type']} {n['posted']} ({n['solicitation'] or n['id'][:8]})" for n in s["notices"][:2]) or "-"
            awards = "; ".join(f"{a['piid']} {a['signed']}" for a in s["awards"][:3]) or "-"
            print(f"| {s['pid']} | {s['title'][:48]} | {s['office']} | {s['sol']} | {s['award']} | {s['value']} | {notice} | {awards} | {s['reading'][:200]} |")
    print()
    print("Readings: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
          + f"; with a candidate notice awaiting a reviewer {with_candidate}" + (f"; of the review lines, {len(dropped)} are rows missing from {latest}" if dropped else ""))
    return 0


def watch_block(lines: list[dict], today: date, incumbents: list[tuple[str, dict | None]], candidates: list[dict],
                related: list[dict], siblings: list[dict], office: str, searched: str, verdict: str = "") -> list[str]:
    """What to watch for a requirement whose RFP has not appeared: read from the rows above, nothing new asserted."""
    latest = lines[-1]
    pid = latest["pid"] or latest["record_key"]
    piids = [t for t, _ in incumbents]
    tokens = sorted(distinctive_tokens(latest["requirement_title"]))
    out = ["\n## Watch: a requirement to follow before its RFP (read from the rows above; the tool asserts nothing new)"]
    if verdict:
        out.append(f"- reads today as: {verdict}")
    sol = quarter_dates(latest["solicitation_fy"], latest["solicitation_quarter"])
    sol_window, award_window = window(latest["solicitation_fy"], latest["solicitation_quarter"]), window(latest["award_fy"], latest["award_quarter"])
    if sol:
        first, last = sol
        if today < first:
            when = f"opens in {(first - today).days} days"
        elif today <= last:
            when = "is open now"
        else:
            when = f"closed {(today - last).days} days ago; {not_found('solicitation of this line', 'the saved SAM.gov searches', searched)}"
        out.append(f"- forecast: solicitation {sol_window} ({first} to {last}) {when}; award {award_window}; {latest['release']} row {latest['row_number']}")
    else:
        out.append(f"- forecast: solicitation window {sol_window} is not a dated quarter; award {award_window}; {latest['release']} row {latest['row_number']}")
    why = [latest["anticipated_total_value"] or "no value stated", latest["procurement_method"] or "method unstated",
           latest["procurement_instrument"] or "instrument unstated", latest["follow_on_or_new"] or "new/follow-on unstated"]
    if latest["existing_contract_number"]:
        why.append(f"incumbent {latest['existing_contract_number']}" + (f" ({latest['incumbent_contractor']})" if latest["incumbent_contractor"] else ""))
    out.append("- why it matters, as the forecast states it: " + "; ".join(why))
    pop_end = ""
    for t, u in incumbents:
        if not u:
            out.append(f"- incumbent {t}: USAspending not collected, so its end date and headroom are unknown here")
            continue
        pop_end = pop_end or u["pop_end"] or ""
        days = f" ({(date.fromisoformat(u['pop_end']) - today).days} days from {today})" if u["pop_end"] else ""
        ceiling, obligated = float(u["ceiling"] or 0), float(u["obligated"] or 0)
        out.append(f"- incumbent {t}: period of performance ends {u['pop_end'] or 'unstated'}{days}; ceiling ${ceiling:,.0f}, obligated ${obligated:,.0f}, "
                   f"headroom ${ceiling - obligated:,.0f} (this one contract's ceiling less its own obligations); USAspending retrieved {u['retrieved']}")
    if candidates:
        out.append("- on the record so far, as candidates: " + "; ".join(f"{c['type'].lower()} {c['posted']} [{c['solicitation'] or c['id'][:12]}] ({c['key']})" for c in candidates))
    if related:
        out.append("- related procurements in the program (not this buy): " + "; ".join(f"{c['type'].lower()} {c['posted']} [{c['solicitation'] or c['id'][:12]}]" for c in related))
    if siblings:
        out.append("- sibling forecast lines in the same office sharing a program name: " + "; ".join(f"{l['pid'] or l['record_key']} {l['requirement_title'][:50]} (sol {window(l['solicitation_fy'], l['solicitation_quarter'])})" for l in siblings))
    confirm = [f"a solicitation-stage SAM.gov notice carrying the PID {pid}" + (f" or the incumbent contract {', '.join(piids)}" if piids else ""),
               f"a solicitation-stage notice whose title carries {', '.join(tokens) or 'the line title'} and names {office} or its parent",
               "the next LRAE release keeping the line with an unchanged or nearer window"]
    if piids:
        confirm.insert(2, f"an FPDS or USAspending action under a new solicitation number naming {piids[0]} as the predecessor")
    invalidate = ["the next LRAE release dropping the line" + (f" or folding its scope into {', '.join(l['pid'] or l['record_key'] for l in siblings)}" if siblings else "")]
    if piids:
        invalidate.insert(0, f"a J&A, extension or modification carrying {piids[0]} past {pop_end or 'its current end date'}")
    if candidates:
        invalidate.append("an award under " + ", ".join(sorted({compact(c["solicitation"]) for c in candidates if c["solicitation"]}) or ["the candidate notice"]) + " whose description covers this line's scope")
    out.append("- would confirm (the reading becomes solicited, then awarded): " + "; ".join(confirm))
    out.append("- would invalidate (the reading becomes cancelled, restructured or review): " + "; ".join(invalidate))
    checks = [f"python research/tools/sam_notices.py {t}" for t in tokens[:2]]
    checks += [usaspending_check(t) for t in piids[:1]]
    checks += [f"python research/tools/fetch.py 'https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:{t}&start=0'" for t in piids[:1]]
    out.append("- to re-check: " + "; ".join(checks))
    return out


def news_about(key: str, office_id: str, programs: set[str] = frozenset()) -> list[dict]:
    """Modelled news articles that name this requirement, its office or its program name.

    Built by research/tools/news.py; absent until it has run, which is not an error: the tool
    reads what has been collected, and says so when nothing has.
    """
    path = RESEARCH / "events" / "news_observations.json"
    if not path.exists():
        return []
    articles = json.loads(path.read_text(encoding="utf-8"))["articles"]
    hits = [a for a in articles
            if key in a["links"]["requirements"] or office_id in a["links"]["offices"]
            or (programs & set(a["entities"]["programs"]))]
    return sorted(hits, key=lambda a: a["published"] or "")


def hiring_about(office_id: str, programs: set[str] = frozenset()) -> list[dict]:
    """Modelled job announcements whose own words name this requirement's office or its program name.

    Built by research/tools/jobs.py; absent until it has run, which is not an error. The command a posting is filed
    under is not enough to name it here: every NAVWAR vacancy is filed under NAVWAR, and a requirement's window
    shows the postings that speak of its office or its program.
    """
    path = RESEARCH / "events" / "hiring_observations.json"
    if not path.exists():
        return []
    postings = json.loads(path.read_text(encoding="utf-8"))["postings"]
    hits = [p for p in postings if office_id in p["links"]["offices"] or (programs & set(p["entities"]["programs"]))]
    return sorted(hits, key=lambda p: p["opened"] or "", reverse=True)


def print_need(dsn: str, key: str) -> int:
    rows, hits, examples, ctx, idx = manifest(), sgs_hits(manifest()), attribution_examples(), match_context(), seed_index()
    _, searched = search_dates(rows)
    today = date.today()
    canonical = next((ck for (_, rk), ck in ctx["canon"].items() if rk == key), key)
    if canonical != key:
        print(f"{key} loads under {canonical} (an accepted connection: see the forecast history below)")
        key = canonical
    need = need_record(dsn, key)
    pairs = paired_rows(key)
    chain = ctx["chains"].get(key, [])
    in_chain = {(l["release"], l["row_number"]) for l in chain}
    lines = chain + [l for l in lrae_lines() if (l["release"], l["row_number"]) not in in_chain
                     and (l["record_key"] == key or l["pid"] == key or (l["release"], l["row_number"]) in pairs)]
    lines.sort(key=lambda l: l["release"])
    if not need and not lines:
        print(f"nothing loaded or packaged under {key!r}; try the PID or a record key like row:lrae_navwar_2024-06:406")
        return 1
    title = need["title"] if need else lines[-1]["requirement_title"]
    print(f"# {key} - {title}")
    office_id = ""
    if need:
        live = [o for o in need["offices"] if o["live"] and o["role"] == "originating_requirement_owner"]
        for o in live:
            print(f"Office (live claim, {o['observed_at']}): {o['name']}")
            print_office(dsn, o["org_id"], idx)
        if len(live) > 1:
            print("  NOTE: more than one live owner claim; the releases disagree and both are kept")
    if lines:
        office_id = lines[-1]["office_id"]
    if not lines:
        # A requirement created from SAM.gov notices: no forecast row carries it, so its history is its notices.
        print(f"\n{need['description']}")
        print("\n## Loaded revisions (one per notice under this solicitation number; the latest is live)")
        for r in need["revisions"] or []:
            print(f"  {r['observed_at']}: {r['statement'][:100]}{' (live)' if r['live'] else ' (superseded)'}; basis {r['basis']}; {r['source_key']}")
        for e in sorted(need["evidence"] or [], key=lambda e: (e["observed_at"] or "", e["source_key"])):
            print(f"  evidence {e['source_key']}: {e['excerpt'][:80]}; source {e['source_url'] or 'URL not loaded'}")
        print(f"\nRead it from the notice side for offices, candidates, related notices and awards: python research/tools/trace.py notice <notice id>")
        return 0
    print("\n## Forecast history (one row per release that carried the line, with how the row was tied to this key)")
    print("| release | row | matched by | sol | award window | value as stated | office named | method | instrument |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for l in lines:
        if l.get("tie"):
            basis = f"{l['tie']['basis']} (confirmed, loaded under this key: {l['tie']['via'].split(',')[0]})"
        else:
            basis = "PID on the row" if l["pid"] == key else ("record key" if l["record_key"] == key else pairs.get((l["release"], l["row_number"]), ""))
        print(f"| {l['release']} ({l['release_date']}) | {l['sheet']}!row {l['row_number']} (sha {l['sha']}) | {basis} | {window(l['solicitation_fy'], l['solicitation_quarter'])} | "
              f"{window(l['award_fy'], l['award_quarter'])} | {l['anticipated_total_value']} | {l['office_code_string'].split(' - ')[0]} -> {l['office_id']} | {l['procurement_method'] or '-'} | {l['procurement_instrument'] or '-'} |")
    for src in release_sources(lines):
        print(f"  source {src}")
    if any("candidate" in pairs.get((l["release"], l["row_number"]), "") for l in lines):
        print("\nA row matched as a candidate is a reviewer's call, not the tool's; its field changes are reported against the pair in the release diff.")
    if need and need["revisions"]:
        print("\nLoaded revisions (gov_requirement_revisions, one per release; basis inferred = the row was tied to this key without carrying its PID, and the assertion's rationale says how):")
        for r in need["revisions"]:
            tie = f"; {r['rationale'].split('. ')[0]}" if r["basis"] == "inferred" and r["rationale"].startswith("Row tied") else ""
            print(f"  {r['observed_at']}: award {r['expected_from'] or '?'}..{r['expected_to'] or '?'}{' (live)' if r['live'] else ''}; basis {r['basis']}{tie}")
    status = line_status(lines[-1], rows, hits, examples, today, ctx,
                         dropped="" if lines[-1]["release"] in CURRENT else
                         f"not in its activity's newest release, last carried by {lines[-1]['release']} row {lines[-1]['row_number']}; no pairing by PID, exact title or similar title; "
                                 "no cancellation stated in the saved records; a reviewer decides whether it was awarded, folded into another line or dropped")
    print("\n## Reading (outcome word first: awarded, solicited, cancelled, review, restructured, delayed, open, not yet due; a candidate is a reviewer's call)")
    print(f"- {status['reading']}")
    estimates = [f"{l['release_date']}: {l['anticipated_total_value'] or 'no range'}" for l in lines]
    ceilings, obligations, sources = [], [], {}
    print("\n## Incumbent and related contracts (saved USAspending / FPDS)")
    tokens = []
    for l in lines:
        for t in contract_tokens(l["existing_contract_number"]):
            if t not in tokens:
                tokens.append(t)
    incumbents = []
    for t in tokens:
        u = usaspending(rows, t)
        actions, atom, _ = fpds_by_piid(rows, t)
        incumbents.append((t, u))
        if u:
            print(f"- {t} ({u['type']}, {u['recipient']}): signed {u['signed']}, PoP {u['pop_start']} -> {u['pop_end']}, last modified {u['last_modified']}; "
                  f"solicitation {u['solicitation'] or 'unstated'}; {u['competed'] or ''}, offers {u['offers']}")
            print(f"    source USAspending {u['url']} retrieved {u['retrieved']} sha {u['sha']}")
            ceilings.append((t, u["ceiling"])); obligations.append((t, u["obligated"]))
            sources[t] = f"USAspending {u['url']} retrieved {u['retrieved']} sha {u['sha']}"
        else:
            print(f"- {t}: USAspending not collected ({usaspending_check(t, actions)})")
        if actions:
            recent = sorted(actions, key=lambda a: a["signed"])[-3:]
            print(f"  FPDS: first page of actions saved ({len(actions)}; later pages were not collected, so USAspending's last-modified date is the recency to trust); "
                  + "; ".join(f"{a['mod'] or 'base'} {a['signed']} obligated ${float(a['obligated'] or 0):,.0f}" for a in recent))
            print(f"    source FPDS {cite(atom)}")
        elif atom is None:
            print(f"  FPDS PIID lookup not collected (python research/tools/lrae_package.py collect)")
    print("\n## Notices (saved SAM.gov searches joined to this line; ~ marks a candidate by shared program name, a reviewer's call)")
    seen = set()
    sols = set()
    candidate_sols = set()
    candidates, related = [], []
    for l in lines:
        for n in notice_summaries(l, hits):
            if n["id"] in seen:
                continue
            seen.add(n["id"])
            print(f"- {n['posted']} {n['type']}: {n['title'][:90]} [{n['solicitation'] or 'no number'}] via {n['method']} key {n['key']}")
            print(f"    source {SAM_VIEW.format(n['id'])}; found by {n['query_url']} (sha {n['sha']})" if n["sha"] else f"    source {SAM_VIEW.format(n['id'])}; saved {n['query_url']}")
            d = notice_detail(n["id"])
            if d and d["award"]:
                a = d["award"]
                print(f"    award block on the notice: {a.get('number')} on {a.get('date')} for ${float(a.get('amount') or 0):,.0f} to {(a.get('awardee') or {}).get('name')} (a modification, so it sits under the incumbent's ceiling above, not a new award)")
            if n["solicitation"] and n["type"] in SOLICITATION_STAGE:
                sols.add(compact(n["solicitation"]))
    for l in lines:
        for c in candidate_notices(l, hits, ctx):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            (related if c["method"] == "related" else candidates).append(c)
    for c in candidates:
        print(f"- ~{c['posted']} {c['type']}: {c['title'][:90]} [{c['solicitation'] or 'no number'}] candidate, {c['key']}")
        print(f"    source {SAM_VIEW.format(c['id'])}")
        if c["solicitation"]:
            candidate_sols.add(compact(c["solicitation"]))
    for c in related:
        print(f"- related, not this buy: {c['posted']} {c['type']}: {c['title'][:90]} [{c['solicitation'] or 'no number'}] {c['key']}")
        print(f"    source {SAM_VIEW.format(c['id'])}")
    for l in lines:
        sols.update(compact(m) for m in SOL_RE.findall(l["requirement_title"].replace(" ", "")))
    if not seen:
        print("- " + not_found("notice joined to this line", "the saved SAM.gov searches", searched) + "; "
              + ("SeaPort/GSA order competitions are not posted on SAM.gov" if any("order" in l["procurement_instrument"].lower() for l in lines) else "no saved search hit contains the PID or the incumbent contract"))
    sols |= {s for s in candidate_sols if s not in sols}
    print("\n## Awards under solicitations tied to this line (saved FPDS lookups by solicitation number, else the office sweep; ~ = reached through a candidate notice)")
    found = False
    for sol in sorted(sols):
        actions, atom = fpds_by_solicitation(rows, sol)
        base = [a for a in actions if a["mod"] in ("0", "")]
        mark = "~" if sol in candidate_sols else ""
        if base:
            found = True
            for a in base:
                print(f"- {mark}{sol}: {a['piid']} signed {a['signed']} to {a['vendor']}; base and all options ${float(a['base_and_all_options'] or 0):,.0f}; obligated at award ${float(a['obligated'] or 0):,.0f}; {a['description'][:80]}")
                print(f"    source FPDS {cite(a['source'])}")
                ceilings.append((a["piid"], a["base_and_all_options"])); obligations.append((a["piid"] + " at award", a["obligated"]))
                sources[a["piid"]] = f"FPDS {cite(a['source'])}"
                u = usaspending(rows, a["piid"])
                if u:
                    obligations[-1] = (a["piid"], u["obligated"])
                    sources[a["piid"]] += f"; USAspending {u['url']} retrieved {u['retrieved']} sha {u['sha']}"
        elif atom is not None:
            print(f"- {mark}{sol}: {not_found('award', 'the saved FPDS lookup by solicitation number', atom['retrieved_at'][:10])}; source FPDS {cite(atom)}")
        else:
            print(f"- {mark}{sol}: not collected (python research/tools/fetch.py 'https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=SOLICITATION_ID:{sol}&start=0')")
    if not sols:
        print("- no solicitation number on the line or in its notices, so there is no FPDS lookup to make by solicitation")
    pid = lines[-1]["pid"] if lines else key
    if pid in examples:
        x = examples[pid]
        print(f"- reviewed attribution {x['id']}: award {x['identifier']} ({x['evidence_class']}); {x.get('notes', '')[:140]}")
        u = usaspending(rows, x["identifier"])
        if u:
            ceilings.append((x["identifier"], u["ceiling"])); obligations.append((x["identifier"], u["obligated"]))
            sources[x["identifier"]] = f"USAspending {u['url']} retrieved {u['retrieved']} sha {u['sha']}"
            print(f"  {x['identifier']}: signed {u['signed']}, PoP {u['pop_start']} -> {u['pop_end']}, {u['recipient']}, solicitation {u['solicitation'] or 'unstated'}")
    print("\n## Money, kept apart")
    print("\n".join(money_block(estimates, ceilings, obligations)))
    for piid, src in sources.items():
        print(f"  source for {piid}: {src}")
    program_tokens = {t for l in lines for t in distinctive_tokens(l["requirement_title"]) if ctx["rarity"].get(t, 0) <= 3}
    articles = news_about(lines[-1]["pid"] or key if lines else key, office_id, program_tokens)
    print("\n## News naming this requirement, its office or its program")
    if not articles:
        print("- nothing in research/events/news_observations.json names them (research/tools/news.py build)")
    for article in articles:
        named = [c for c in article["claims"] if program_tokens & set(c["programs"]) or office_id in c["subjects"]]
        print(f"- {article['published'] or 'date not stated'} {article['publisher']} ({article['source_type']}, "
              f"reliability {article['reliability']}, reads {article['relation']}): {article['headline'][:90]}")
        print(f"  {article['url']}")
        for claim in named[:2]:
            print(f"  {claim['statement_type']} [{claim['relation']}]: \"{claim['passage'][:200]}\"")
    postings = hiring_about(office_id, program_tokens)
    print("\n## Hiring naming this requirement's office or its program")
    if not postings:
        print("- nothing in research/events/hiring_observations.json names them (research/tools/jobs.py build)")
    for posting in postings[:5]:
        print(f"- {posting['opened'] or 'date not stated'} {posting['hiring']['agency'] or posting['hiring']['department']}: {posting['title'][:80]} "
              f"(series {', '.join(posting['series']) or 'not stated'}; {posting['reading']['status']}, {posting['reading']['posture']}; reads {posting['relation']})")
        print(f"  {posting['url']}")
        for claim in [c for c in posting["claims"] if office_id in c["subjects"] or program_tokens & set(c["programs"])][:2]:
            print(f"  {claim['statement_type']} [{claim['relation']}]: \"{claim['passage'][:200]}\"")
    if len(postings) > 5:
        print(f"- and {len(postings) - 5} more; `python research/tools/jobs.py read WORD` prints one")
    if need:
        print("\n## Loaded records (the database rows the claims above rest on)")
        print(f"- need {need['id']} source_key {need['source_key']}")
        for r in need["revisions"]:
            tie = f"; {r['rationale'].split('. ')[0]}" if r["basis"] == "inferred" and r["rationale"].startswith("Row tied") else ""
            print(f"- revision assertion {r['source_key']} observed {r['observed_at']}{' (live)' if r['live'] else ' (superseded)'}, basis {r['basis']}{tie}")
        for e in sorted(need["evidence"], key=lambda e: (e["observed_at"] or "", e["source_key"])):
            print(f"- evidence {e['source_key']}: {e['excerpt'][:80]}; source {e['source_url'] or 'URL not loaded'}")
    if lines and lines[-1]["release"] in CURRENT and not found:
        print("\n".join(watch_block(lines, today, incumbents, candidates, related, siblings_of(lines[-1], ctx), office_id, searched, status["reading"])))
    return 0


def cmd_need(args) -> int:
    return print_need(args.dsn, args.key)


SHORT_ALIAS = 8


def whole_word(probe: str, hay: str) -> int:
    """Where the alias sits in the text, or -1; an alias under SHORT_ALIAS characters must not sit inside a longer word.
    A bare number (an office code such as 7600) counts only written as a code, `Code 7600`: DoDI 1000.04 is no office."""
    if probe not in hay:  # every pattern below holds the probe; most aliases are in no notice, and the regex costs more
        return -1
    if probe.isdigit():
        m = re.search(r"(?<![a-z0-9])code\s+" + re.escape(probe) + r"(?![0-9])(?!\.[0-9])", hay)
        return m.start() if m else -1
    if len(probe) >= SHORT_ALIAS:
        return hay.find(probe)
    m = re.search(r"(?<![a-z0-9])" + re.escape(probe) + r"(?![a-z0-9])", hay)
    return m.start() if m else -1


def squeeze(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower().strip()


def unbracket(s: str) -> str:
    """Notices write an acronym in brackets after a name; aliases sometimes do too. Compare both with every bracketed
    part removed, so "(MIDS) International Program Office" and "MIDS International Program Office (IPO)" meet on the
    words that stay."""
    return squeeze(re.sub(r"\s*\([^)]*\)", " ", s))


@lru_cache(maxsize=2)
def office_names(seed: Path, mtime_ns: int) -> list[tuple[dict, list[tuple[str, str, str]]]]:
    """Each office of the organization memory with its names as (text, probe, probe_open), read once per version of the
    file: rebuilding them for every notice was most of a layers build."""
    out = []
    for node in json.loads(seed.read_text(encoding="utf-8"))["nodes"]:
        if node["type"] == "person":
            continue
        texts = [node["name"]] + [a["text"] if isinstance(a, dict) else a for a in node.get("aliases") or []] + list((node.get("codes") or {}).values())
        # Only a trailing bracket group comes off ("PMW 740 (IIPO)"); a bracket mid-alias stays, or an alias
        # such as "PAE (PAE) for Robotics and Autonomous Systems (RAS)" would shrink to its first three words.
        out.append((node, [(t, squeeze(re.sub(r"\s*\([^()]*\)\s*$", "", t)), unbracket(t)) for t in texts]))
    return out


def resolve_offices(text: str) -> list[dict]:
    """Offices the notice names, through the organization memory's aliases; the matched wording travels with each."""
    seed = RESEARCH / "memory" / "organization_seed.json"
    flat, flat_open = squeeze(text), unbracket(text)
    out = []
    for node, names in office_names(seed, seed.stat().st_mtime_ns):
        for t, probe, probe_open in names:
            if len(probe) < 4:
                # An acronym too short to stand alone ("STO") counts where the profile's office-code pattern reads it.
                hit = next((m for m in OFFICE_CODE_RE.finditer(text) if "".join(m.groups()) == t), None)
                if hit:
                    context = squeeze(text[max(0, hit.start() - 60): hit.end() + 60])
                    out.append({"office": node["id"], "name": node["name"], "matched": t, "former": False, "context": context})
                    break
                continue
            # A short alias must stand as a word: "SUB-S" is not in "sub-surface", "PMW 101" is not in "PMW 1010".
            start = whole_word(probe, flat)
            if start >= 0:
                hay = flat
            else:
                start = whole_word(probe_open, flat_open) if len(probe_open) >= 4 else -1
                if start < 0:
                    continue
                hay, probe = flat_open, probe_open
            context = hay[max(0, start - 60): start + len(probe) + 60]
            former = bool(re.search(r"(formerly|previously)[^.]{0,80}" + re.escape(probe[:30]), hay))
            out.append({"office": node["id"], "name": node["name"], "matched": t, "former": former, "context": context})
            break
    return out


def line_match(detail: dict, title: str, lines: list[dict], ctx: dict, codes_only: bool = False) -> dict | None:
    """How a forecast line stands to a notice.

    explicit: the notice text names the line's incumbent contract. candidate: the titles share
    a program name or code few lines carry (or two of them, or three words when `codes_only`
    is off), so they may be one buy and a reviewer decides. related: they share a program but
    state different lots, families or generations, so they are distinct buys. None: no tie.
    """
    piids = set(contract_tokens(detail["text"]))
    line_piids = {t for l in lines for t in contract_tokens(l["existing_contract_number"])}
    explicit = piids & line_piids
    if explicit:
        mine = {l.get("pid") or l.get("record_key") for l in lines}
        others = sorted({k for t in explicit for k in ctx.get("incumbent_lines", {}).get(t, [])} - mine)
        if others:
            return {"relation": "candidate", "basis": f"candidate: incumbent contract {', '.join(sorted(explicit))} appears in the notice text, but "
                                                       f"{len(others) + 1} forecast lines cite it ({', '.join(others)} too), so the contract alone does not tell them apart"}
        return {"relation": "explicit", "basis": f"explicit: incumbent contract {', '.join(sorted(explicit))} appears in the notice text"}
    notice_words, notice_codes = title_tokens(detail["title"]), distinctive_tokens(detail["title"])
    words, codes = title_tokens(title), distinctive_tokens(title)
    shared_codes = sorted(codes & notice_codes)
    rare = [t for t in shared_codes if ctx["rarity"].get(t, 0) <= 3]
    if rare or len(shared_codes) >= 2:
        shared = shared_codes
    elif not codes_only and len(words & notice_words) >= 3:
        shared = sorted(words & notice_words)
    else:
        return None
    kind = relation_of(title, detail["title"])
    basis = f"shared program tokens {', '.join(shared[:6])}"
    if kind == "related":
        basis += f"; different generation (line {gen_label(title)}, notice {gen_label(detail['title'])}), so a distinct buy in the same program"
    return {"relation": kind, "basis": f"{kind}: {basis}"}


def line_bullet(key: str, title: str, lines: list[dict], match: dict, office_note: str = "") -> str:
    latest = lines[-1] if lines else None
    when = (f"sol {window(latest['solicitation_fy'], latest['solicitation_quarter'])}, award {window(latest['award_fy'], latest['award_quarter'])}, "
            f"{latest['anticipated_total_value'] or 'no value stated'}") if latest else "no datapack row"
    record = f"; record {latest['release']} {latest['sheet']}!row {latest['row_number']} (sha {latest['sha']})" if latest else ""
    return f"- {key} {title[:70]} ({when}){office_note} - {match['basis']}{record}"


def cmd_notice(args) -> int:
    args.key = re.sub(r"^notice:", "", args.key, flags=re.I)  # the need key other views print (notice:DARPAPS26123)
    rows, hits, ctx, idx = manifest(), sgs_hits(manifest()), match_context(), seed_index()
    detail = notice_detail(args.key)
    if detail is None:
        candidates = [h for h in hits.values() if compact(h["solicitation"]) == compact(args.key) or h["id"].startswith(args.key)]
        if not candidates:  # a forecast row that carries the number says what the solicitation is before it is collected
            carried = [l for l in lrae_lines() if compact(args.key) in (compact(l["pid"]), compact(l.get("number") or ""))]
            for l in carried:
                print(line_bullet(l["pid"] or l["record_key"], l["requirement_title"], [l], {"basis": "the forecast row carries this solicitation number"},
                                  f" under {l['office_id']} ({l['office_code_string'].split(' - ')[0]})"))
            print(f"no saved notice or search hit for {args.key!r}; collect it with: python research/tools/sam_notices.py {args.key}")
            return 0 if carried else 1
        for h in sorted(candidates, key=lambda h: h["posted"]):
            state = (f"responses closed {h['due']}" if h["active"] is False and h.get("due") else
                     f"open, responses until {h['due']}" if h["active"] and h.get("due") else f"active={h['active']}")
            print(f"- {h['posted']} {h['type']}: {h['title'][:90]} [{h['solicitation']}] id {h['id']} {state} {SAM_VIEW.format(h['id'])}")
        print("\nDetail not saved for these; harvest one with: python research/tools/sam_notices.py <id>")
        return 0
    print(f"# {detail['title']}")
    print(f"{detail['type']} posted {detail['posted']}; solicitation {detail['solicitation'] or 'none'}; notice {detail['id']}")
    print(f"source {SAM_VIEW.format(detail['id'])}; record {cite(notice_source(rows, detail['id']))}; saved {detail['path']}")
    node = SAM_ORG_NODES.get(detail["organization_id"])
    print(f"signal: {signal_kind(detail)}; posted on SAM.gov under organization id {detail['organization_id'] or 'unstated'}"
          + (f" -> {node} ({idx['nodes'][node]['name']})" if node and node in idx["nodes"] else " (no memory node for this id: the contracting office is not resolved)"))
    if detail["award"]:
        a = detail["award"]
        print(f"Award block: {a.get('number')} on {a.get('date')} for ${float(a.get('amount') or 0):,.0f} to {(a.get('awardee') or {}).get('name')}")
    print("\n## Offices named in the notice text")
    offices = resolve_offices(detail["text"])
    if not offices:
        print("- no organization-memory alias appears in the text; the office is not stated or uses a wording the memory lacks")
    for o in offices:
        print(f"- {o['office']} ({o['name'][:70]}) via alias {o['matched']!r}{' - named as the FORMER designation' if o['former'] else ''}\n    ...{o['context']}...")
    specific = specific_offices(offices, ctx["parents"])
    current = [o for o in offices if o["office"] in specific]
    used_lines: list[dict] = []
    if offices and not current:
        print(f"\nNo program office, portfolio or PEO is named as current in the text; it names only {', '.join(dict.fromkeys(o['office'] for o in offices))}. "
              "The related notices below are read for the office they name.")
    for o in current:
        found = find_org(args.dsn, o["office"])
        if not found:
            print(f"\n{o['office']} is not in the loaded office table")
            continue
        org = found[0]
        print(f"\n## {org['name']} (loaded as {org['org_type']}, source_ref {org['source_ref']}, row {org['id']})")
        if o["office"] in idx["nodes"]:
            print_names(idx["nodes"][o["office"]], idx)
        print_office(args.dsn, org["id"], idx)
        if o["office"] in specific:
            owned = needs_for_office(args.dsn, org["id"])
            same, related = [], []
            for n in owned:
                lines = [l for l in lrae_lines() if l["record_key"] == n["source_key"] or l["pid"] == n["source_key"]]
                m = line_match(detail, n["title"], lines, ctx)
                if m:
                    (related if m["relation"] == "related" else same).append((n, lines, m))
                    used_lines += lines
            print("\n## Forecast lines owned by this office that may be the same requirement (an explicit tie, or a candidate a reviewer accepts or rejects)")
            for n, lines, m in same:
                print(line_bullet(n["source_key"], n["title"], lines, m))
            if not same:
                print(f"- none of the {len(owned)} loaded lines owned by this office shares a program name with the notice or cites a contract it names")
            print("\n## Related procurements in the same program owned by this office (a different lot, family or generation: context for this buy, not the same requirement)")
            for n, lines, m in related:
                print(line_bullet(n["source_key"], n["title"], lines, m))
            if not related:
                print("- none")
    print("\n## Forecast lines under OTHER offices sharing the notice's program names (the office differs, so each carries that question)")
    named = {o["office"] for o in offices}
    others_same, others_related = [], []
    for l in lrae_lines():
        if l["release"] not in CURRENT or l["office_id"] in named:
            continue
        m = line_match(detail, l["requirement_title"], [l], ctx, codes_only=True)
        if m:
            (others_related if m["relation"] == "related" else others_same).append((l, m))
    for l, m in others_same[:10]:
        print(line_bullet(l["pid"] or l["record_key"], l["requirement_title"], [l], m, f" under {l['office_id']} ({l['office_code_string'].split(' - ')[0]})"))
        used_lines.append(l)
    if not others_same:
        print("- none")
    print("\n## Related procurements under OTHER offices (a different lot, family or generation of the program; not this buy)")
    for l, m in others_related[:10]:
        print(line_bullet(l["pid"] or l["record_key"], l["requirement_title"], [l], m, f" under {l['office_id']} ({l['office_code_string'].split(' - ')[0]})"))
        used_lines.append(l)
    if not others_related:
        print("- none")
    for src in release_sources(used_lines):
        print(f"  forecast source {src}")
    same_key = sorted((h for h in hits.values() if detail["solicitation"] and compact(h["solicitation"]) == compact(detail["solicitation"]) and h["id"] != detail["id"]),
                      key=lambda h: h["posted"])
    place = place_notice(detail, ctx, [d for d in (notice_detail(h["id"]) for h in same_key) if d])
    print("\n## Requirement (resolved to the one forecast line that names this notice, or created from the notice; candidates above never resolve it)")
    if place["how"] == "resolved":
        l = place["line"]
        print(f"- resolved to {place['key']} {l['requirement_title'][:70]} (basis {place['basis']}: {place['why']}); "
              f"record {l['release']} {l['sheet']}!row {l['row_number']} (sha {l['sha']})")
    else:
        print(f"- created as {place['key']}: {place['note']}")
        for r in place["ambiguous"]:
            print(f"    claims it: {r['key']} {r['line']['requirement_title'][:60]} ({r['why']})")
    print(f"- history under this solicitation number in the saved records: {len(same_key) + 1} notice(s): "
          + "; ".join(f"{h['posted']} {h['type'].lower()}" for h in [*same_key, {"posted": detail["posted"], "type": detail["type"]}])
          + "; a notice under another number is a related notice below, never folded in")
    stored = need_record(args.dsn, place["key"])
    if stored:
        print(f"- loaded: need {stored['id']} source_key {stored['source_key']} lifecycle {stored['lifecycle']}")
        for r in stored["revisions"] or []:
            print(f"    revision {r['observed_at']}: {r['statement'][:90]}{' (live)' if r['live'] else ' (superseded)'}; basis {r['basis']}")
        for o in stored["offices"] or []:
            print(f"    office {o['role']}: {o['name'][:70]}{' (live)' if o['live'] else ' (superseded)'}, observed {o['observed_at']}")
    else:
        print(f"- not loaded under {place['key']} in this database (regenerate research/tools/agency_layers_sql.py and load; the loader creates notice-keyed requirements)")
    print("\n## Related notices (saved searches sharing the solicitation number or title stem, or a saved notice whose text carries a rare program name from this title)")
    stem = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", detail["title"].lower()))[:45]
    rare = {t for t in distinctive_tokens(detail["title"]) if ctx["rarity"].get(t, 0) <= 3}

    def kin(h: dict) -> bool:
        if detail["solicitation"] and compact(h["solicitation"]) == compact(detail["solicitation"]):
            return True
        if stem and re.sub(r"[^a-z0-9 ]", " ", h["title"].lower()).startswith(stem):
            return True
        # An RFI often carries only a number in its title ("N0003925R4011 - Egypt A2"); the program name is in the body.
        d = notice_detail(h["id"]) if rare else None
        return bool(d and rare & distinctive_tokens(d["title"] + " " + d["text"]))

    kin_hits = [h for h in hits.values() if h["id"] != detail["id"] and kin(h)]
    for h in sorted(kin_hits, key=lambda h: h["posted"]):
        gen = relation_of(detail["title"], h["title"])
        print(f"- {h['posted']} {h['type']}: {h['title'][:80]} [{h['solicitation'] or 'no number'}] {SAM_VIEW.format(h['id'])}"
              + (f" (different generation: {gen_label(h['title'])}; a related buy)" if gen == "related" else ""))
    if not kin_hits:
        print("- none saved")
    related_sols = [s for h in sorted(kin_hits, key=lambda h: h["posted"]) if h["solicitation"] and h["type"] in SOLICITATION_STAGE
                    for s in (SOL_RE.findall(compact(h["solicitation"])) or [compact(h["solicitation"])]) if s != compact(detail["solicitation"])]
    if related_sols:
        print("\n## Awards under the related notices' solicitation numbers (saved FPDS lookups: the program's earlier buys, context for this requirement, not this buy)")
        for sol in dict.fromkeys(related_sols):
            actions, atom = fpds_by_solicitation(rows, sol)
            base = [a for a in actions if a["mod"] in ("0", "")]
            for a in base:
                u = usaspending(rows, a["piid"])
                print(f"- {sol}: {a['piid']} signed {a['signed']} to {a['vendor']}; base and all options ${float(a['base_and_all_options'] or 0):,.0f}; obligated at award ${float(a['obligated'] or 0):,.0f}; {a['description'][:60]}"
                      + (f"; PoP {u['pop_start']} -> {u['pop_end']}; obligated to date ${float(u['obligated'] or 0):,.0f}; awarding office {u['awarding_office']}" if u else ""))
                print(f"    source FPDS {cite(a['source'])}" + (f"; USAspending {u['url']} retrieved {u['retrieved']} sha {u['sha']}" if u else ""))
            if not base:
                print(f"- {sol}: " + (not_found("award", "the saved FPDS lookup by solicitation number", atom["retrieved_at"][:10]) + f"; source FPDS {cite(atom)}" if atom is not None
                                      else f"not collected (python research/tools/fetch.py 'https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=SOLICITATION_ID:{sol}&start=0')"))
    if specific:
        print("\n## Other saved notices naming the same office (the office's own stream: context, not this buy unless a section above ties it)")
        kin_ids = {h["id"] for h in kin_hits}
        same_office = []
        for h in hits.values():
            if h["id"] == detail["id"] or h["id"] in kin_ids:
                continue
            d = notice_detail(h["id"])
            if d and set(specific) & set(specific_offices(resolve_offices(d["text"]), ctx["parents"])):
                same_office.append(h)
        for h in sorted(same_office, key=lambda h: h["posted"]):
            print(f"- {h['posted']} {h['type']}: {h['title'][:80]} [{h['solicitation'] or 'no number'}] {SAM_VIEW.format(h['id'])}")
        if not same_office:
            print("- none saved")
    if not specific:
        print("\n## Offices named by the related notices (the notice itself names none: a candidate for its office, a reviewer's call, never asserted on this notice)")
        named_by_kin = []
        for h in sorted(kin_hits, key=lambda h: h["posted"]):
            d = notice_detail(h["id"])
            for office in specific_offices(resolve_offices(d["text"]), ctx["parents"]) if d else []:
                named_by_kin.append(f"{office} named by the {h['type'].lower()} of {h['posted']} [{h['solicitation'] or h['id'][:12]}]")
        for line in named_by_kin:
            print(f"- {line}")
        if not named_by_kin:
            print("- none: no related notice with a saved detail names an office the memory knows")
    if detail["solicitation"]:
        actions, atom = fpds_by_solicitation(rows, detail["solicitation"])
        print("\n## Awards under this solicitation number (saved FPDS lookup by solicitation number, else the office sweep)")
        base = [a for a in actions if a["mod"] in ("0", "")]
        for a in base:
            u = usaspending(rows, a["piid"])
            print(f"- {a['piid']} signed {a['signed']} to {a['vendor']}; base and all options ${float(a['base_and_all_options'] or 0):,.0f}; obligated at award ${float(a['obligated'] or 0):,.0f}")
            print(f"    source FPDS {cite(a['source'])}" + (f"; USAspending {u['url']} retrieved {u['retrieved']} sha {u['sha']}" if u else ""))
        if not base:
            scope = sweep_scope(rows, detail["solicitation"], detail["posted"])
            if atom is not None:
                print(f"- {not_found('award', 'the saved FPDS lookup by solicitation number', atom['retrieved_at'][:10])}; source FPDS {cite(atom)}"
                      + (f"; nor in {scope}" if scope else ""))
            elif scope:
                print(f"- {not_found('base award under this number', scope, '')}")
            else:
                print(f"- not collected (python research/tools/fetch.py 'https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=SOLICITATION_ID:{compact(detail['solicitation'])}&start=0')")
    return 0


def cmd_award(args) -> int:
    rows, hits = manifest(), sgs_hits(manifest())
    piid = compact(args.piid)
    u = usaspending(rows, piid)
    actions, atom, _ = fpds_by_piid(rows, piid)
    swept = None if u else swept_awards().get(piid)
    print(f"# {piid}")
    if u:
        print(f"{u['type']} to {u['recipient']}; {u['description'][:200]}\nsigned {u['signed']}; PoP {u['pop_start']} -> {u['pop_end']}; last modified {u['last_modified']}; "
              f"awarding office {u['awarding_office']}; solicitation {u['solicitation'] or 'unstated'}; {u['competed'] or 'competition unstated'}, offers {u['offers']}; parent {u['parent'] or '-'}\n"
              f"USAspending retrieved {u['retrieved']} (sha {u['sha']})")
    else:
        print(f"USAspending not collected: {usaspending_check(piid, actions)}")  # the id names the agency and the vehicle FPDS states
        if any(a.get("kind") == "other_transaction" for a in actions):
            print("kind: other transaction award (FPDS OtherTransactionAward), not a contract")
    if actions:
        print(f"FPDS actions saved: {len(actions)} on one page; first {min(a['signed'] for a in actions)} last {max(a['signed'] for a in actions)}")
    elif atom is None:
        print("FPDS PIID lookup not collected" + (f"; the FPDS sweep holds its base award: signed {swept['signed']}, ultimate completion "
              f"{swept['completion'] or 'unstated'}, {swept['vendor']}, vehicle {swept['idv'] or 'none'} ({swept['page']['url']})" if swept else ""))
    print("\n## Forecast lines that cite this contract as the incumbent")
    cited = [l for l in lrae_lines() if piid in contract_tokens(l["existing_contract_number"])]
    for l in cited:
        print(f"- {l['release']} row {l['row_number']}: {l['pid'] or l['record_key']} {l['requirement_title'][:70]} | sol {window(l['solicitation_fy'], l['solicitation_quarter'])}, award {window(l['award_fy'], l['award_quarter'])}, {l['anticipated_total_value']}, {l['follow_on_or_new'] or '-'}")
    if not cited:
        print("- none")
    print("\n## Reviewed attributions naming this contract")
    for x in memory_file("attribution_examples.json", []):
        if compact(x["identifier"]) == piid:
            print(f"- {x['id']}: {', '.join(x['program_offices'])}; {x['evidence_class']}; {x['owner_summary'][:100]}; related {json.dumps(x.get('related'))[:160]}")
    print("\n## Notices whose saved search hit cites this contract number")
    for h in sorted((h for h in hits.values() if piid in compact(h["title"]) or piid in compact(h["solicitation"])), key=lambda h: h["posted"]):
        print(f"- {h['posted']} {h['type']}: {h['title'][:80]} [{h['solicitation']}] id {h['id'][:12]}")
    print("\n## Predecessor and successor candidates among saved awards (same parent vehicle and recipient, overlapping description)")
    kin = 0
    if u and u["parent"]:
        for row in rows:
            url = row.get("url", "")
            if not kept_page(row) or "/awards/CONT_AWD_" not in url or f"_{piid}_" in url:
                continue
            other_piid = url.split("/awards/CONT_AWD_")[1].split("_")[0]
            v = usaspending(rows, other_piid)
            if not v or v["parent"] != u["parent"] or v["recipient"] != u["recipient"]:
                continue
            shared = sorted(title_tokens(v["description"]) & title_tokens(u["description"]))
            if len(shared) >= 2:
                kin += 1
                role = "successor" if (v["signed"] or "") > (u["signed"] or "") else "predecessor"
                print(f"- {role} candidate {v['piid']}: signed {v['signed']}, PoP {v['pop_start']} -> {v['pop_end']}, ceiling ${float(v['ceiling'] or 0):,.0f}, obligated ${float(v['obligated'] or 0):,.0f}; shared words {', '.join(shared[:5])}")
    if not kin:
        print("- none among the saved awards")
    print("\n## Money, kept apart")
    print("\n".join(money_block([f"{l['release_date']}: {l['anticipated_total_value']}" for l in cited],
                                [(piid, u["ceiling"])] if u else [], [(piid, u["obligated"])] if u else [])))
    print("\n## Recompete signal (the contract as an entry point: when it ends, and what the forecasts and notices say follows it)")
    ends = u or ({"pop_end": swept["completion"]} if swept and swept["completion"] else None)
    print(f"- {recompete_reading(ends, cited, [h for h in hits.values() if piid in compact(h['title']) or piid in compact(h['solicitation']) or piid in compact((notice_detail(h['id']) or {}).get('text', ''))], date.today(), search_dates(rows)[1])}")
    return 0


def recompete_reading(u: dict | None, cited: list[dict], notices: list[dict], today: date, searched: str) -> str:
    """One line on a contract read as a recompete signal: its end date against today, the forecast lines that
    name it as the incumbent, and the solicitation-stage notices that cite it after the earliest such line."""
    if not u or not u.get("pop_end"):
        return "period of performance not collected, so the contract's end cannot be read"
    end = date.fromisoformat(u["pop_end"][:10])
    days = (end - today).days
    out = f"period of performance ends {u['pop_end'][:10]} ({'in ' + str(days) + ' days' if days >= 0 else str(-days) + ' days ago'})"
    if cited:
        latest = sorted(cited, key=lambda l: l["release"])[-1]
        out += f"; {len(cited)} forecast row(s) name it as the incumbent, latest {latest['release']} row {latest['row_number']}: sol {window(latest['solicitation_fy'], latest['solicitation_quarter'])}, award {window(latest['award_fy'], latest['award_quarter'])}"
        first = min(l["release_date"] for l in cited)
        after = sorted((n for n in notices if n["type"] in SOLICITATION_STAGE and n["posted"] > first), key=lambda n: n["posted"])
        out += (f"; solicitation-stage notice(s) citing it after the line first appeared ({first}): " + ", ".join(f"{n['type'].lower()} {n['posted']} [{n['solicitation'] or n['id'][:12]}]" for n in after)
                if after else f"; {not_found('solicitation-stage notice citing it after the line first appeared (' + first + ')', 'the saved SAM.gov searches', searched)}")
    else:
        out += f"; no forecast row names it as the incumbent; {not_found('forecast line', 'the saved releases', searched)}"
    return out


# ---------------------------------------------------------------- selfcheck

def selfcheck() -> int:
    from sam_notices import SWEEP_ORGS
    assert set(SAM_ORG_NODES) == set(SWEEP_ORGS), "every organization the sweep keeps has a memory node, and no other"
    atom = """<feed><entry><title>x</title><content><ns1:award><ns1:awardID><ns1:awardContractID><ns1:agencyID>9700</ns1:agencyID>
    <ns1:PIID>N0003926F4006</ns1:PIID><ns1:modNumber>0</ns1:modNumber></ns1:awardContractID><ns1:referencedIDVID><ns1:agencyID>9700</ns1:agencyID>
    <ns1:PIID>N0003925D4006</ns1:PIID></ns1:referencedIDVID></ns1:awardID><ns1:relevantContractDates><ns1:signedDate>2026-06-18 00:00:00</ns1:signedDate></ns1:relevantContractDates>
    <ns1:dollarValues><ns1:obligatedAmount>3083496.00</ns1:obligatedAmount><ns1:baseAndAllOptionsValue>82061676.54</ns1:baseAndAllOptionsValue></ns1:dollarValues>
    <ns1:vendor><ns1:vendorHeader><ns1:vendorName>ROCKWELL COLLINS, INC.</ns1:vendorName></ns1:vendorHeader></ns1:vendor>
    <ns1:contractData><ns1:descriptionOfContractRequirement>MIDS WDL SE&amp;I</ns1:descriptionOfContractRequirement></ns1:contractData></ns1:award></content></entry></feed>"""
    a = fpds_actions(atom)[0]
    assert (a["piid"], a["idv"], a["mod"], a["signed"]) == ("N0003926F4006", "N0003925D4006", "0", "2026-06-18"), a
    assert a["description"] == "MIDS WDL SE&I" and a["base_and_all_options"] == "82061676.54"
    assert (a["agency"], a["idv_agency"], a["kind"]) == ("9700", "9700", "award"), a
    assert award_uid(a["piid"], a["agency"], a["idv"], a["idv_agency"], a["kind"]) == "CONT_AWD_N0003926F4006_9700_N0003925D4006_9700"
    assert award_uid("HR001126FE029", "9700", "HR001122D0001", "9700") == "CONT_AWD_HR001126FE029_9700_HR001122D0001_9700", "the Orca run's award"
    assert award_uid("N0003925D4006", "9700", kind="idv") == "CONT_IDV_N0003925D4006_9700"
    ot = """<entry><content><ns1:OtherTransactionAward><ns1:OtherTransactionAwardID><ns1:OtherTransactionAwardContractID><ns1:agencyID name="X">7001</ns1:agencyID>
    <ns1:PIID>70RSAT23T00000016</ns1:PIID><ns1:modNumber>0</ns1:modNumber></ns1:OtherTransactionAwardContractID></ns1:OtherTransactionAwardID>
    <ns1:signedDate>2023-04-26 00:00:00</ns1:signedDate><ns1:vendorName>CHAINGUARD INC</ns1:vendorName></ns1:OtherTransactionAward></content></entry>"""
    o = fpds_actions(ot)[0]
    assert (o["piid"], o["agency"], o["kind"], o["idv"]) == ("70RSAT23T00000016", "7001", "other_transaction", ""), o
    assert award_uid(o["piid"], o["agency"], kind=o["kind"]) is None, "an other transaction has no CONT_AWD id"
    assert "does not name it" in usaspending_check("70RSAT23T00000016", [o]) and "CONT_AWD_N0003926F4006_9700_N0003925D4006_9700" in usaspending_check("N0003926F4006", [a])
    # A search hit's active flag is as of the search; a response date that has passed closes the notice
    assert notice_open(True, "2026-08-21", date(2026, 9, 28)) is False and notice_open(True, "2026-12-18", date(2026, 9, 28)) is True
    assert notice_open(None, "", date(2026, 9, 28)) is None and notice_open(False, "", date(2026, 9, 28)) is False and notice_open(None, "2026-08-21", date(2026, 9, 28)) is False

    today = date(2026, 9, 20)
    # One outcome word first; every state it tells apart has its own.
    assert reading("FY26 Q2", [], [{"piid": "N1", "signed": "2026-04-13"}], "", "", today).startswith("awarded: 1 action")
    assert reading("FY26 Q2", [{"type": "Presolicitation", "posted": "2026-08-12"}], [], "", "", today).startswith("solicited: presolicitation posted 2026-08-12")
    assert reading("FY26 Q2", [{"type": "Award Notice", "posted": "2026-06-01", "solicitation": "N0003926R0001"}], [], "", "", today) == \
        "awarded: award notice posted 2026-06-01 [N0003926R0001] naming this line"
    r = reading("FY26 Q2", [{"type": "Presolicitation", "posted": "2026-01-12", "cancelled": True}], [], "", "", today, "2026-09-20")
    assert r == "cancelled: presolicitation posted 2026-01-12 is marked cancelled on SAM.gov; no later solicitation found in the saved SAM.gov searches as of 2026-09-20", r
    r = reading("FY26 Q2", [{"type": "Presolicitation", "posted": "2026-01-12", "cancelled": True}, {"type": "Solicitation", "posted": "2026-05-01"}], [], "", "", today)
    assert r.startswith("solicited: solicitation posted 2026-05-01; an earlier presolicitation of 2026-01-12 is marked cancelled"), r
    r = reading("FY26 Q2", [], [], "", "", today, "2026-09-20", incumbent_actions=[{"type": "Award Notice", "posted": "2024-11-05", "key": "N0003916C0087"}],
                award_windows=["FY27 Q1", "FY25 Q3", "FY27 Q2"], restructured=["sibling line X first appears in lrae_navwar_2025-06"])
    assert r.startswith("review: award notice posted 2024-11-05 on incumbent N0003916C0087 (an action on the incumbent"), r
    assert "no follow-on solicitation found in the saved SAM.gov searches as of 2026-09-20" in r, "a negative names the records searched"
    assert "; also restructured: sibling line X" in r and "; also delayed: solicitation window FY26 Q2 closed 173 days ago; award window moved FY27 Q1 -> FY25 Q3 -> FY27 Q2 across releases" in r, r
    r = reading("FY25 Q3", [], [], "", "", today, "2026-09-20", dropped="not in lrae_navwar_2025-06, last carried by lrae_navwar_2024-06 row 13")
    assert r.startswith("review: not in lrae_navwar_2025-06, last carried by lrae_navwar_2024-06 row 13; also delayed: solicitation window FY25 Q3 closed"), r
    r = reading("FY26 Q2", [], [], "", "", today, restructured=["method Sole Source -> Full and Open Competition between lrae_navwar_2024-06 and lrae_navwar_2025-06"])
    assert r.startswith("restructured: method Sole Source -> Full and Open Competition") and "; also delayed: solicitation window FY26 Q2 closed 173 days ago" in r, r

    # A justification or a special notice carrying the line's own identifier states no solicitation, so
    # no stage branch catches it. It asks for review, and a special notice read that way is not also
    # reported as market research.
    r = reading("FY26 Q2", [{"type": "Justification (J&A)", "posted": "2026-03-04"}], [], "", "", today, "2026-09-20")
    assert r.startswith("review: justification (j&a) posted 2026-03-04 carries this line's own identifier"), r
    assert "no solicitation found in the saved SAM.gov searches as of 2026-09-20" in r, r
    r = reading("FY26 Q2", [{"type": "Special Notice", "posted": "2026-09-10"}], [], "", "", today, "2026-09-20")
    assert r.startswith("review: special notice posted 2026-09-10") and "market research only so far" not in r, r
    r = reading("FY26 Q2", [{"type": "Sources Sought", "posted": "2026-02-02"}], [], "", "", today, "2026-09-20")
    assert r.startswith("delayed:") and "market research only so far: sources sought posted 2026-02-02" in r, r

    # The notice's own record decides cancellation, over a search that ran before it was cancelled.
    merged = apply_notice_cancellation({"n1": {"cancelled": False}, "n2": {"cancelled": False}},
                                       lambda notice_id: {"cancelled": notice_id == "n1"})
    assert merged["n1"]["cancelled"] and not merged["n2"]["cancelled"]

    # A notice that reached the line through the incumbent contract and predates the line's first
    # forecast appearance is the incumbent's history, whatever kind it is.
    sought = {"type": "Sources Sought", "posted": "2020-09-28", "via": "incumbent", "shared": [], "method": "explicit"}
    assert notice_role(sought, "2025-06-30") == "incumbent history"
    assert notice_role({**sought, "posted": "2026-01-10"}, "2025-06-30") == "line"
    assert notice_role({**sought, "via": "pid"}, "2025-06-30") == "line"
    assert notice_role({**sought, "type": "Presolicitation", "posted": "2026-01-10", "shared": ["N00039-25-RFPREQ-PMW-150-0157"]}, "2025-06-30") == "candidate"

    # A line folded into a chain keyed by another row must not meet itself as a sibling.
    latest_release = "lrae_navwar_2025-06"
    folded = {"record_key": "row:lrae_navwar_2025-06:9", "pid": "", "requirement_title": "NTCDL Follow-On Production",
              "office_id": "pmw:170", "existing_contract_number": "N0003916C0087", "release": latest_release}
    earlier = {**folded, "release": "lrae_navwar_2023-06", "record_key": "row:lrae_navwar_2023-06:1"}
    folded_ctx = {"rarity": {"ntcdl": 1}, "parents": {}, "offices_of": {}, "latest": latest_release, "current": {latest_release}, "incumbent_lines": {},
                  "chains": {"N00039-25-RFPREQ-PMW/A-170-0001": [earlier, folded]},
                  "canon": {(latest_release, folded["record_key"]): "N00039-25-RFPREQ-PMW/A-170-0001",
                            ("lrae_navwar_2023-06", earlier["record_key"]): "N00039-25-RFPREQ-PMW/A-170-0001"}}
    assert siblings_of(folded, folded_ctx) == [], "a folded line is keyed by its chain, not by its row"
    assert not [n for n in restructure_notes(folded_ctx["chains"]["N00039-25-RFPREQ-PMW/A-170-0001"], folded_ctx) if n.startswith("sibling line")]
    r = reading("FY26 Q2", [{"type": "Sources Sought", "posted": "2026-02-10"}], [], "", "", today, "2026-09-20", award_windows=["FY26 Q4", "FY27 Q2"])
    assert r == ("delayed: solicitation window FY26 Q2 closed 173 days ago; no public notice found in the saved SAM.gov searches as of 2026-09-20; "
                 "award window moved FY26 Q4 -> FY27 Q2 across releases; market research only so far: sources sought posted 2026-02-10"), r
    assert reading("FY26 Q4", [], [], "", "", today, "2026-09-20") == "open: solicitation window FY26 Q4 runs to 2026-09-30; no public notice found in the saved SAM.gov searches as of 2026-09-20"
    assert reading("FY27 Q2", [], [], "", "", today) == "not yet due: solicitation window FY27 Q2 opens in 103 days"
    assert reading("TBD", [], [], "", "", today, "2026-09-20").startswith("not dated: solicitation window TBD; no public notice found")
    assert reading("FY26 Q1", [], [], "2025-12-08", "Delivery Order/Task Order", today, "2026-09-20") == \
        ("delayed: solicitation window FY26 Q1 closed 263 days ago; no public notice found in the saved SAM.gov searches as of 2026-09-20; "
         "SeaPort/GSA order competitions are not posted on SAM.gov; incumbent last acted 2025-12-08")
    assert closest_relation("pmw:160", {"peo:c4i", "pmw:160"}, {"pmw:160": ["peo:c4i"]}) == "same office"
    assert closest_relation("pmw:160", {"peo:c4i", "pmw:999"}, {"pmw:160": ["peo:c4i"]}) == "notice names the line's parent"
    r = reading("FY27 Q2", [], [], "", "", today, candidate="candidate: solicitation posted 2026-09-17 [N0003926RE014] (shared program tokens aints); a reviewer decides whether it is this line")
    assert r.endswith("; candidate: solicitation posted 2026-09-17 [N0003926RE014] (shared program tokens aints); a reviewer decides whether it is this line"), "a candidate is appended, never promoted"
    assert not_found("award", "the saved FPDS lookup", "2026-09-20") == "no award found in the saved FPDS lookup as of 2026-09-20"

    # A notice as the entry point: resolved to the one line that names it, or created; candidates never resolve it.
    def ln(pid, title, inc="", release="lrae_navwar_2025-06", release_date="2025-06-19", **kw):
        return {"pid": pid, "record_key": pid, "requirement_title": title, "existing_contract_number": inc, "release": release,
                "release_date": release_date, "row_number": "1", "sheet": "s", "sha": "x", "office_id": "pmw:160", **kw}
    nctx = {"latest": "lrae_navwar_2025-06", "current": {"lrae_navwar_2025-06"}, "incumbent_lines": {"N0003916C0087": ["P1"], "N0003922D1004": ["P2", "P3"]},
            "chains": {"P1": [ln("P1", "NTCDL Follow-On", "N0003916C0087", "lrae_navwar_2023-06", "2023-06-20"), ln("P1", "NTCDL Follow-On", "N0003916C0087")],
                       "P2": [ln("P2", "NILE ISS 6", "N0003922D1004")], "P3": [ln("P3", "NILE PSS", "N0003922D1004")],
                       "P4": [ln("P4", "ADNS MAC N0003925R9510")], "OLD": [ln("OLD", "Dropped line", release="lrae_navwar_2024-06", release_date="2024-06-20")]},
            "parents": {"pmw:740": {"peo:c4i"}, "peo:c4i": {"pae:mission-systems"}}}
    base = {"title": "x", "solicitation": "", "text": "", "type": "solicitation", "posted": "2026-08-01", "id": "n1", "cancelled": False}
    p = place_notice({**base, "text": "... N00039-25-RFPREQ-PMW/A-170-0001 ..."}, {**nctx, "chains": {"P1": [ln("N00039-25-RFPREQ-PMW/A-170-0001", "NTCDL")]}})
    assert p["how"] == "resolved" and p["basis"] == "documented" and "PID" in p["why"], p
    p = place_notice({**base, "solicitation": "N00039-25-R-9510"}, nctx)
    assert p["how"] == "resolved" and p["key"] == "P4" and p["basis"] == "documented", "the line's own solicitation number resolves"
    p = place_notice({**base, "text": "follow-on to N0003916C0087"}, nctx)
    assert p["how"] == "resolved" and p["key"] == "P1" and p["basis"] == "inferred", "a uniquely cited incumbent on a later solicitation-stage notice resolves, basis inferred"
    p = place_notice({**base, "text": "follow-on to N0003916C0087", "posted": "2023-01-01"}, nctx)
    assert p["how"] == "created", "a notice before the line's first forecast appearance is the incumbent's history"
    p = place_notice({**base, "text": "follow-on to N0003916C0087", "type": "award notice"}, nctx)
    assert p["how"] == "created", "an award notice on the incumbent never resolves"
    p = place_notice({**base, "text": "recompete of N0003922D1004", "solicitation": "N0003926RE013"}, nctx)
    assert p["how"] == "created" and p["key"] == "notice:N0003926RE013" and not p["ambiguous"], "an incumbent two lines cite resolves neither"
    p = place_notice({**base, "text": "N0003925R9510 and N00039-25-RFPREQ-PMW/A-170-0001", "solicitation": "N0003925R9510"},
                     {**nctx, "chains": {**nctx["chains"], "P1": [ln("N00039-25-RFPREQ-PMW/A-170-0001", "NTCDL")]}})
    assert p["how"] == "created" and len(p["ambiguous"]) == 2, "two lines claiming one notice leave it its own record"
    p = place_notice({**base, "solicitation": "N0003926RE017"}, nctx, kin=[{**base, "id": "n0", "text": "N00039-25-RFPREQ-PMW/A-170-0001", "posted": "2024-01-01"}])
    assert p["how"] == "created" and p["key"] == "notice:N0003926RE017", "kin without a live line do not resolve"
    p = place_notice({**base, "solicitation": "N0003926RE017"}, nctx, kin=[{**base, "id": "n0", "text": "recompete of N0003916C0087", "posted": "2026-01-01"}])
    assert p["how"] == "resolved" and p["key"] == "P1", "an earlier notice under the same number carries the tie for the whole requirement"
    assert specific_offices([{"office": "pmw:740", "former": False}, {"office": "peo:c4i", "former": False}, {"office": "contracting:n00039", "former": False}], nctx["parents"]) == ["pmw:740"]
    assert specific_offices([{"office": "pmw:740", "former": True}, {"office": "pae:mission-systems", "former": False}], nctx["parents"]) == ["pae:mission-systems"]
    assert specific_offices([{"office": "center:nrl", "former": False}, {"office": "command:onr", "former": False}], {}) == ["center:nrl"], \
        "a laboratory is named; its systems command is not"
    assert specific_offices([{"office": "pms:312", "former": False}, {"office": "center:nswcpd", "former": False}], {}) == ["pms:312"], \
        "the center doing the work does not make a program office's notice ambiguous"
    assert specific_offices([{"office": "pms:485", "former": False}, {"office": "dept:psns-imf-code-2300", "former": False}],
                            {"dept:psns-imf-code-2300": {"activity:psns-imf"}}) == ["pms:485"], "so does a department of the activity"
    lab = {**nctx, "parents": {"dept:nrl-7600": {"center:nrl"}}, "chains": {
        "L1": [ln("L1", "Research and Development for Space Science Division (C)", office_id="dept:nrl-7600")],
        "L2": [ln("L2", "Plasma Physics Division Support", office_id="dept:nrl-6700")]}}
    claims = line_claims({**base, "title": "NRL Space Science Division R&D Support Services", "text": "NRL", "offices": ["center:nrl"]}, lab)
    assert [c["key"] for c in claims] == ["L1"], "a notice naming the line's parent alone claims the one line under it that shares its title words"
    lab["chains"]["L3"] = [ln("L3", "Space Science Division Engineering", office_id="dept:nrl-7600")]
    assert not line_claims({**base, "title": "NRL Space Science Division R&D Support Services", "text": "NRL", "offices": ["center:nrl"]}, lab), \
        "two lines under the parent sharing the words leave the notice a reviewer's call"
    assert signal_kind({**base, "type": "sources sought"}) == "request for information (sources sought)"
    assert signal_kind({**base, "type": "special notice", "title": "DRPM RAS Industry Day"}) == "industry day (special notice)"
    assert signal_kind({**base, "type": "special notice", "title": "Firefox Proposers Day"}) == "industry day (special notice)"
    assert signal_kind({**base, "type": "special notice", "title": "Multiscale Reasoning For Human Physiology RFI"}) == "request for information (special notice)"
    assert signal_kind({**base, "type": "special notice", "title": "Future Program: Intrinsic Market Resilience (IMR)"}) == "forecast (special notice)"
    assert signal_kind({**base, "type": "special notice", "title": "Notice of Intent to Award Using Other than Full and Open Competition"}) == "intent to award without full competition (special notice)"
    assert signal_kind({**base, "type": "special notice", "title": "Convergence Special Notice"}) == "special notice"
    assert signal_kind({**base, "title": "Area of Interest (AOI): ExVLF"}) == "area of interest under a commercial solutions opening (solicitation)"
    assert signal_kind({**base, "type": "presolicitation", "cancelled": True}).endswith(", marked cancelled on SAM.gov")
    r = recompete_reading({"pop_end": "2028-09-29"}, [ln("P1", "NTCDL", "N0003916C0087", release_date="2025-06-19", solicitation_fy="FY26", solicitation_quarter="Q2", award_fy="FY27", award_quarter="Q2")],
                          [{"type": "Presolicitation", "posted": "2026-05-22", "solicitation": "N0003926RB002", "id": "x"}, {"type": "Award Notice", "posted": "2024-11-05", "solicitation": "", "id": "y"}], today, "2026-09-20")
    assert r.startswith("period of performance ends 2028-09-29 (in 740 days); 1 forecast row(s) name it as the incumbent") and "presolicitation 2026-05-22 [N0003926RB002]" in r and "award notice" not in r, r
    r = recompete_reading({"pop_end": "2026-01-01"}, [], [], today, "2026-09-20")
    assert "262 days ago" in r and "no forecast row names it" in r, r
    assert quarter_dates("FY26", "Q1") == (date(2025, 10, 1), date(2025, 12, 31)) and quarter_dates("FY26", "Q4") == (date(2026, 7, 1), date(2026, 9, 30))
    assert quarter_dates("TBD", "") is None and quarter_dates("FY27", "Q2") == (date(2027, 1, 1), date(2027, 3, 31))

    # Same program, different generation: related, never the same buy.
    assert generations("MIDS WDL SF2 Production (C)") == {2} and generations("MIDS (WDL) SWARMM Family 3 (SF3) Radio") == {3}
    assert generations("RSNF C4ISR Training Services III (C)") == {3} and generations("NTCDL – Follow-On Production and ESS Contract (C)") == set()
    assert generations("LBUCS Receive Version 2 Development") == {2} and generations("DO 0002 under SF1 Contract N0003923D4000") == {1}
    assert generations("PEO C4I Engineering Support") == set(), "C4I is not a roman numeral"
    assert relation_of("MIDS WDL SF2 Production (C)", "MIDS Weapons Data Link (WDL) SWARMM Family 2") == "candidate"
    assert relation_of("MIDS WDL SF2 Production (C)", "MIDS (WDL) SWARMM Family 3 (SF3) Radio") == "related"
    assert relation_of("NTCDL – Follow-On Production", "NTCDL Engineering Support Services") == "candidate", "no generation on either side keeps a candidate"

    block = money_block(["2025-06-19: $250M - $1B"], [("N1", 307743354.0)], [("N1", 0.0)])
    assert "$307,743,354" in block[1] and block[2].endswith("N1 $0") and "$250M - $1B" in block[0]
    assert not any("total" in line.lower() and "$5" in line for line in block), "money kinds are never summed"

    text = ("NAVWAR, in support of the Tactical Data Link (TDL) Program Office (PMW-530) (formerly Multifunctional Information "
            "Distribution (MIDS) Program Office (MPO) (PMA/W-101)), intends to process a ceiling increase")
    offices = resolve_offices(text)
    assert any(o["office"] == "pmw:101" for o in offices), offices
    ipo = resolve_offices("NAVWAR, on behalf of the Multifunctional Information Distribution System (MIDS) International Program Office (IPO) issues a Full and Open RFP")
    assert any(o["office"] == "pmw:101" for o in ipo), "an acronym in brackets between the words must not hide the office"

    # Candidates rest on program names and codes, never on procurement vocabulary.
    assert distinctive_tokens("NTCDL – Follow-On Production and ESS Contract (C)") == {"ntcdl"}
    assert distinctive_tokens("MIDS WDL SF2 Production (C)") == {"mids", "wdl", "sf2"}
    assert distinctive_tokens("DO 0002 under SF1 Contract N0003923D4000") == {"sf1", "n0003923d4000"}, "a bare number names nothing"
    ctx = {"rarity": {"ntcdl": 2, "mids": 12, "wdl": 2, "sf3": 0}, "parents": {"pmw:101": {"peo:c4i"}}, "offices_of": {"n1": set(), "n2": {"pmw:101"}, "n3": {"pmw:170"}, "n4": {"pmw:101"}}}
    hits = {"n1": {"id": "n1", "title": "NTCDL Engineering Support Services - Sole Source", "type": "Presolicitation", "posted": "2026-05-22", "solicitation": "N0003926RB002"},
            "n2": {"id": "n2", "title": "MIDS Weapons Data Link (WDL) SWARMM Family 2", "type": "Solicitation", "posted": "2024-08-30", "solicitation": "N0003924R4100"},
            "n3": {"id": "n3", "title": "LBUCS Development and Production", "type": "Presolicitation", "posted": "2024-01-01", "solicitation": "X"},
            "n4": {"id": "n4", "title": "MIDS Weapons Data Link (WDL) SWARMM Family 3 (SF3) Radio", "type": "Presolicitation", "posted": "2026-08-12", "solicitation": "N00039PRESOL_SF3"}}
    line = {"requirement_title": "NTCDL – Follow-On Production and ESS Contract (C)", "office_id": "pmw:170", "solicitation_fy": "FY26", "award_fy": "FY27"}
    assert [c["id"] for c in candidate_notices(line, hits, ctx)] == ["n1"], "one rare program token is enough"
    line = {"requirement_title": "MIDS WDL SF2 Production (C)", "office_id": "peo:c4i", "solicitation_fy": "FY24", "award_fy": "FY25"}
    got = candidate_notices(line, hits, ctx)
    assert [(c["id"], c["method"]) for c in got] == [("n2", "candidate"), ("n4", "related")], got
    assert "line filed under the notice office's parent code" in got[0]["key"]
    assert "different generation (line 2, notice 3)" in got[1]["key"], "SF3 is context for the SF2 line, never its solicitation"
    detail = {"title": "MIDS Weapons Data Link (WDL) SWARMM Family 3 (SF3) Radio", "text": "no contract numbers here"}
    m = line_match(detail, "MIDS WDL SF2 Production (C)", [], ctx)
    assert m and m["relation"] == "related" and m["basis"].startswith("related: shared program tokens mids, wdl; different generation"), m
    m = line_match(detail, "MIDS WDL Terminal Spares", [], ctx)
    assert m and m["relation"] == "candidate" and m["basis"] == "candidate: shared program tokens mids, wdl", m
    assert line_match(detail, "MIDS-LVT IDIQ - New Contracts", [], ctx) is None, "one common program name is not a tie"
    assert line_match(detail, "LBUCS Development", [], ctx) is None
    m = line_match({"title": "x", "text": "extends N0003916C0087 for BAE"}, "NTCDL Follow-On", [{"existing_contract_number": "N00039-16-C-0087"}], ctx)
    assert m and m["relation"] == "explicit" and "N0003916C0087" in m["basis"], m
    # The same office and the same incumbent do not make two rows one requirement: a follow-on and a bridge share both.
    shared_ctx = {**ctx, "incumbent_lines": {"N0003916C0087": ["N00039-25-RFPREQ-PMW/A-170-0001", "N00039-25-RFPREQ-PMW/A-170-0276"]}}
    m = line_match({"title": "x", "text": "extends N0003916C0087 for BAE"}, "NTCDL Follow-On",
                   [{"pid": "N00039-25-RFPREQ-PMW/A-170-0001", "record_key": "N00039-25-RFPREQ-PMW/A-170-0001", "existing_contract_number": "N00039-16-C-0087"}], shared_ctx)
    assert m and m["relation"] == "candidate" and "2 forecast lines cite it (N00039-25-RFPREQ-PMW/A-170-0276 too)" in m["basis"], m
    line = {"pid": "P1", "record_key": "P1", "existing_contract_number": "N0003916D0075",
            "joins": [{"join_type": "notice", "target_id": "sam:j1", "method": "inferred", "key_used": "N0003916D0075"},
                      {"join_type": "notice", "target_id": "sam:p1", "method": "explicit", "key_used": "P1"}]}
    joined = notice_summaries(line, {"j1": {"id": "j1", "type": "Justification", "posted": "2022-11-10"}, "p1": {"id": "p1", "type": "Solicitation", "posted": "2026-01-01"}},
                              {"incumbent_lines": {"N0003916D0075": ["P1", "P2"]}})
    assert [(n["via"], n["shared"]) for n in joined] == [("incumbent", ["P2"]), ("pid", [])], joined
    line = {"requirement_title": "MIDS WDL SF2 Production (C)", "office_id": "peo:c4i", "solicitation_fy": "FY28", "award_fy": "FY29"}
    assert [(c["id"], c["method"]) for c in candidate_notices(line, hits, ctx)] == [("n4", "related")], \
        "a notice three fiscal years before the line's window is not its solicitation; the later SF3 notice stays a related buy"
    line = {"requirement_title": "LBUCS Receive Version 2 Development and Production", "office_id": "pmw:770", "solicitation_fy": "FY26", "award_fy": "FY26"}
    assert candidate_notices(line, hits, ctx) == [], "generic words never link, and a notice naming another office is dropped"
    assert related_office("pmw:101", "peo:c4i", ctx["parents"]) == "notice names the line's parent"
    # A split is a sibling that cites the same incumbent or calls itself a bridge; a new order line in a busy program is not.
    def ln(pid, title, inc="", release="lrae_navwar_2025-06", **kw):
        return {"pid": pid, "record_key": pid, "requirement_title": title, "existing_contract_number": inc, "office_id": "pmw:170", "release": release,
                "procurement_method": "", "procurement_instrument": "", "contract_type": "", "follow_on_or_new": "", "anticipated_total_value": "", "office_code_string": "PMW/A-170", **kw}
    follow_on = ln("F", "NTCDL – Follow-On Production and ESS Contract (C)", "N0003916C0087")
    rctx = {"rarity": {"ntcdl": 3, "link": 9, "drs": 2}, "latest": "lrae_navwar_2025-06", "current": {"lrae_navwar_2025-06"}, "canon": {},
            "chains": {"F": [ln("F", "NTCDL – Follow-On Production and ESS Contract (C)", "N0003916C0087", "lrae_navwar_2024-06", anticipated_total_value="$250M - $1B"),
                             dict(follow_on, anticipated_total_value="$250M - $1B")],
                       "B": [ln("B", "NTCDL - Stand Alone Bridge Contract")],
                       "O": [ln("O", "NTCDL Spares Order 7")],
                       "I": [ln("I", "DMR Antenna Spares Mod", "N0003916C0087")]}}
    notes = restructure_notes(rctx["chains"]["F"], rctx)
    assert any("sibling line B" in n and "says 'Stand Alone'" in n for n in notes), notes
    assert any("sibling line I" in n and "the incumbent N0003916C0087" in n for n in notes), notes
    assert not any("sibling line O" in n for n in notes), "a new order line sharing only the program name is not a split"
    rctx["chains"]["F"][0]["anticipated_total_value"] = "$50M - $100M"
    assert any(n.startswith("value as stated $50M - $100M -> $250M - $1B") for n in restructure_notes(rctx["chains"]["F"], rctx))
    rctx["chains"]["F"][0]["anticipated_total_value"] = "TBD"
    assert not any(n.startswith("value as stated") for n in restructure_notes(rctx["chains"]["F"], rctx)), "TBD filled in is a definition, not a restructuring"
    assert compact("N00039-24-R-4019") == "N0003924R4019" and SOL_RE.findall("ADNSMACN0003925R9510(NewAwardFY25)") == ["N0003925R9510"]
    print("trace selfcheck ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    if "--selfcheck" in (argv or sys.argv[1:]):
        return selfcheck()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("status", help="forecast lines whose solicitation window has arrived: solicited, awarded, or silent")
    s.add_argument("--office", default="", help="organization-memory id, name or acronym, e.g. pmw:170 or 'PMW 170'")
    s.add_argument("--through", default="26", help="two-digit fiscal year the solicitation window must fall in or before")
    s.set_defaults(func=cmd_status)
    n = sub.add_parser("need", help="one requirement across releases, offices, contracts, notices and awards")
    n.add_argument("key")
    n.set_defaults(func=cmd_need)
    o = sub.add_parser("notice", help="a SAM.gov notice traced to its office, history and candidate forecast lines")
    o.add_argument("key")
    o.set_defaults(func=cmd_notice)
    w = sub.add_parser("award", help="a contract read back to the forecast and forward to what followed")
    w.add_argument("piid")
    w.set_defaults(func=cmd_award)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

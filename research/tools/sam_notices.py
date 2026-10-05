#!/usr/bin/env python3
"""Harvest SAM.gov notices by solicitation number or notice id through sam.gov's site API.

    python research/tools/sam_notices.py SOLNUM_OR_NOTICEID [...]

Uses the keyless endpoints the SAM.gov web application itself calls (`api_key=null`), which
The platform's runner also uses; the documented public host api.sam.gov answered 404 from every
network tried on 2026-09-16. For each notice: saves the detail JSON (description text included),
lists attachments, downloads PDF/DOCX/TXT attachments under a size cap, extracts their text,
and records every retrieval in research/sources/documents_manifest.jsonl. Prints office-code mentions.
"""

from __future__ import annotations

import hashlib
from collections import Counter
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from agency import P, SAM_NOTICES, NOTE_TAG  # noqa: E402

OUT = SAM_NOTICES  # one folder of notice details per agency: every reader takes the folder as this agency's
MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
SGS = "https://sam.gov/api/prod/sgs/v1/search/"
OPPS = "https://sam.gov/api/prod/opps"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/hal+json, application/json"}
OFFICE_RE = re.compile(
    r"(PM[WA]\s?/?\s?A?\s?-?\s?\d{3}|PMS\s?-?\s?\d{3}|PEO\s?C4I|Program Executive Office[^.;]{0,80}|"
    r"Naval Enterprise Networks|\bNEN\b|[Pp]rogram [Oo]ffice[^.;]{0,60}|Tactical Networks|Communications and GPS Navigation|"
    r"Battlespace Awareness|Command and Control Systems|International C4I|Ship Integration|Undersea Communications|"
    r"Shore and Expeditionary|Cybersecurity Program|Multifunctional Information Distribution)"
)
MAX_ATTACHMENT = 25_000_000


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url: str, timeout: int = 90) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(), dict(resp.headers)


def record(url: str, body: bytes | None, path: Path | None, note: str, status: int | None, mime: str = "", error: str = "") -> None:
    row = {"url": url, "method": "direct", "retrieved_at": now(), "note": note, "status": status, "tls_verified": True}
    if body is not None and path is not None:
        row.update(size=len(body), sha256=hashlib.sha256(body).hexdigest(), path=str(path.relative_to(ROOT)), mime=mime)
    if error:
        row["error"] = error
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def search(query: str) -> list[dict]:
    """Both halves of a SAM.gov search (archived and active), each saved and recorded so a negative can
    name the search it rests on and trace.py reads every hit, harvested or not."""
    hits = []
    OUT.mkdir(parents=True, exist_ok=True)
    for active in ("false", "true"):
        q = urllib.parse.urlencode({"index": "opp", "page": 0, "size": 25, "q": query, "mode": "search", "is_active": active})
        url = f"{SGS}?{q}"
        try:
            status, body, _ = get(url)
            path = OUT / f"search_{hashlib.sha256(url.encode()).hexdigest()[:12]}.json"
            path.write_bytes(body)
            record(url, body, path, f"SAM search{NOTE_TAG} {query} active={active}", status, "application/json")
            hits += json.loads(body).get("_embedded", {}).get("results", [])
        except Exception as exc:  # noqa: BLE001
            print(f"  search error {query} active={active}: {exc}")
            record(url, None, None, f"SAM search{NOTE_TAG} {query} active={active}", None, error=str(exc)[:120])
        time.sleep(0.8)
    seen, out = set(), []
    for h in hits:
        if h.get("_id") not in seen:
            seen.add(h["_id"]); out.append(h)
    return out


def text_of(name: str, data: bytes) -> str:
    low = name.lower()
    try:
        if low.endswith(".pdf"):
            from pypdf import PdfReader
            return " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
        if low.endswith(".docx"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            return re.sub(r"<[^>]+>", " ", xml)
        if low.endswith((".txt", ".csv")):
            return data.decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        return f"[extraction failed: {exc}]"
    return ""


def mentions(text: str) -> list[str]:
    out = []
    for m in OFFICE_RE.finditer(text):
        s, e = max(0, m.start() - 90), min(len(text), m.end() + 90)
        out.append(re.sub(r"\s+", " ", text[s:e]).strip())
        if len(out) >= 6:
            break
    return out


def harvest_notice(notice_id: str, label: str, attachments: bool = True) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    url = f"{OPPS}/v2/opportunities/{notice_id}?api_key=null"
    status, body, _ = get(url)
    detail = json.loads(body)
    path = OUT / f"{notice_id}.json"; path.write_bytes(body)
    record(url, body, path, f"SAM notice detail{NOTE_TAG} {label}:{detail.get('data2', {}).get('title', '')[:80]}", status, "application/json")
    d2 = detail.get("data2", {})
    desc = re.sub(r"<[^>]+>", " ", " ".join(x.get("body", "") for x in detail.get("description", []) if isinstance(x, dict)))
    desc = re.sub(r"\s+", " ", desc)
    result = {"notice_id": notice_id, "title": d2.get("title"), "type": d2.get("type"), "solicitation": d2.get("solicitationNumber"),
              "posted": (detail.get("postedDate") or "")[:10], "org_id": d2.get("organizationId"),
              "poc": [(p.get("fullName"), p.get("email"), p.get("type")) for p in d2.get("pointOfContact", [])],
              "description_chars": len(desc), "description_mentions": mentions(desc), "attachments": []}
    time.sleep(0.8)
    if not attachments:
        return result  # the sweep wants the notice's own words; NAVWAR attachments are PIEE links anyway
    rurl = f"{OPPS}/v3/opportunities/{notice_id}/resources?api_key=null&random={int(time.time()*1000)}"
    try:
        rstatus, rbody, _ = get(rurl)
        rpath = OUT / f"{notice_id}.resources.json"; rpath.write_bytes(rbody)
        record(rurl, rbody, rpath, f"SAM notice attachment list{NOTE_TAG} {label}", rstatus, "application/json")
        rows = []
        def walk(o):
            if isinstance(o, dict):
                if "resourceId" in o:
                    rows.append(o)
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(json.loads(rbody))
    except Exception as exc:  # noqa: BLE001
        rows = []; result["attachments_error"] = str(exc)[:120]
    for r in rows[:12]:
        rid = r.get("resourceId"); name = str(r.get("name") or r.get("fileName") or r.get("description") or rid)
        size = int(r.get("size") or 0)
        entry = {"resource_id": rid, "name": name, "size": size, "mime": r.get("mimeType"), "kind": r.get("type")}
        if r.get("type") == "link":
            # NAVWAR notices usually attach only a PIEE Solicitation Module link; record it, do not follow it.
            entry.update(uri=r.get("uri"), description=r.get("description"), skipped="external link, not a file")
            result["attachments"].append(entry); continue
        if size and size > MAX_ATTACHMENT:
            entry["skipped"] = "over size cap"; result["attachments"].append(entry); continue
        if not name.lower().endswith((".pdf", ".docx", ".txt", ".csv", ".xlsx")):
            entry["skipped"] = "not an extractable type"; result["attachments"].append(entry); continue
        durl = f"{OPPS}/v3/opportunities/resources/files/{rid}/download?api_key=null"
        try:
            time.sleep(1.0)
            dstatus, dbody, dh = get(durl, timeout=180)
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:90]
            dpath = OUT / notice_id / safe; dpath.parent.mkdir(parents=True, exist_ok=True); dpath.write_bytes(dbody)
            record(durl, dbody, dpath, f"SAM attachment{NOTE_TAG} {label}: {name[:70]}", dstatus, dh.get("Content-Type", ""))
            txt = text_of(name, dbody)
            entry.update(bytes=len(dbody), text_chars=len(txt), mentions=mentions(txt))
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:120]
            record(durl, None, None, f"SAM attachment{NOTE_TAG} {label}: {name[:70]}", None, error=str(exc)[:120])
        result["attachments"].append(entry)
    return result


# The SAM.gov organizations the sweep keeps, by the id the site search filters on (`organization_id`) and the
# level-5 office code a hit's hierarchy carries; a legacy hit may carry the id and no code.
SWEEP_ORGS = P["sam_orgs"]  # SAM.gov organization ids at the office level, per agency profile
# Offices swept by code whose SAM.gov id is read off a search for the code at sweep time (NAVAIR HQ, NAWCAD).
SWEEP_CODES = P["sam_codes"]
SWEEP_SINCE = "2021-10-01"  # FY22 on: two years of prior notices before the first back-test outcome


def wanted(hits: list[dict], since: str = SWEEP_SINCE, orgs: dict[str, str] = SWEEP_ORGS) -> list[dict]:
    """The search hits posted by one of the swept organizations on or after `since`, one per notice id."""
    out, seen = [], set()
    for h in hits:
        tree = h.get("organizationHierarchy") or []
        ours = {str(o.get("code") or "") for o in tree} & set(orgs.values()) or {str(o.get("organizationId") or "") for o in tree} & set(orgs)
        if h.get("_id") in seen or not ours or (h.get("publishDate") or "")[:10] < since:
            continue
        seen.add(h["_id"]); out.append(h)
    return out


def last_page(d: dict, page: int, since: str) -> str:
    """Why paging stops after this search page, or "" to go on. Pages run newest change first, so a page whose oldest
    change predates `since` ends the useful run (a notice changes on or after it posts); SAM.gov refuses pages past
    maxAllowedRecords (10,000 results) with a 400."""
    results = d.get("_embedded", {}).get("results", [])
    info = d.get("page", {})
    if not results or page + 1 >= int(info.get("totalPages") or 0):
        return "end"
    if min((r.get("modifiedDate") or "9999")[:10] for r in results) < since:
        return "since"
    if (page + 1) * int(info.get("size") or 25) >= int(info.get("maxAllowedRecords") or 10000):
        return "cap"
    return ""


def org_id_of(code: str, hits: list[dict]) -> str | None:
    """The office-level SAM.gov organization id the hits' hierarchies give for an office code, the most common one."""
    ids = Counter(str(o["organizationId"]) for h in hits for o in h.get("organizationHierarchy") or []
                  if o.get("code") == code and o.get("level") == 5 and o.get("organizationId"))
    return ids.most_common(1)[0][0] if ids else None


def resolve_orgs(codes: tuple[str, ...]) -> dict[str, str]:
    """SAM.gov organization id -> office code for offices known only by code: one search for the code each, since
    the notices an office posts carry its solicitation prefix and its hierarchy."""
    found = {}
    for code in codes:
        url = f"{SGS}?{urllib.parse.urlencode({'index': 'opp', 'page': 0, 'size': 25, 'q': code, 'mode': 'search', 'sort': '-modifiedDate'})}"
        _, body, _ = get(url)
        org = org_id_of(code, json.loads(body).get("_embedded", {}).get("results", []))
        if org:
            found[org] = code
        else:
            print(f"no SAM.gov organization id found for {code}; not swept", flush=True)
    return found


def sweep(argv: list[str]) -> int:
    """Every notice an office posted since FY22: page the site search for the office code (archived and active,
    newest first, every page saved and recorded), then harvest the detail of each notice not yet on disk.
    The loader reads every detail file in data/raw/sam_notices, so nothing else changes.

        python research/tools/sam_notices.py sweep [--org 100076586] [--since 2021-10-01] [--shard i/n] [--limit N]
    """
    import argparse
    ap = argparse.ArgumentParser(prog="sam_notices.py sweep")
    ap.add_argument("--org", default=",".join(SWEEP_ORGS), help="SAM.gov organization ids to sweep")
    ap.add_argument("--since", default=SWEEP_SINCE)
    ap.add_argument("--shard", default="0/1", help="i/n: harvest only the notices whose position mod n is i, so shards run side by side")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--no-search", action="store_true", help="reuse the saved search pages instead of paging again")
    args = ap.parse_args(argv)
    shard, shards = (int(x) for x in args.shard.split("/"))
    OUT.mkdir(parents=True, exist_ok=True)
    orgs = dict(SWEEP_ORGS)
    if args.org == ",".join(SWEEP_ORGS):
        orgs.update(resolve_orgs(SWEEP_CODES))
    hits: list[dict] = []
    for org in (orgs if args.org == ",".join(SWEEP_ORGS) else args.org.split(",")):
        for active in ("false", "true"):
            page = 0
            while True:
                q = urllib.parse.urlencode({"index": "opp", "page": page, "size": 25, "organization_id": org, "mode": "search",
                                            "is_active": active, "sort": "-modifiedDate"})
                url = f"{SGS}?{q}"
                path = OUT / f"search_{hashlib.sha256(url.encode()).hexdigest()[:12]}.json"
                note = f"SAM sweep organization{NOTE_TAG} {org} ({orgs.get(org, org)}) active={active} page {page + 1}"
                if args.no_search and path.exists():
                    body = path.read_bytes()
                else:
                    try:
                        status, body, _ = get(url)
                    except Exception as exc:  # noqa: BLE001  one refused page ends this pass, not the sweep
                        record(url, None, None, note, getattr(exc, "code", None), error=str(exc))
                        print(f"{org} active={active} page {page + 1}: {exc}", flush=True)
                        break
                    path.write_bytes(body)
                    record(url, body, path, note, status, "application/json")
                    time.sleep(0.8)
                d = json.loads(body)
                hits += d.get("_embedded", {}).get("results", [])
                stop = last_page(d, page, args.since)
                if stop == "cap":
                    print(f"{org} active={active}: SAM.gov lists at most {(page + 1) * 25} results; older notices past the cap not listed", flush=True)
                if stop:
                    break
                page += 1
    keep = wanted(hits, args.since, orgs)
    print(f"{len(hits)} hit(s), {len(keep)} posted by {sorted(orgs.values())} since {args.since}", flush=True)
    todo = [h for i, h in enumerate(keep) if i % shards == shard and not (OUT / f"{h['_id']}.json").exists()]
    print(f"shard {shard}/{shards}: {len(todo)} detail(s) to harvest", flush=True)
    for n, h in enumerate(todo[:args.limit], 1):
        try:
            res = harvest_notice(h["_id"], "sweep", attachments=False)
            print(f"{n}/{len(todo)} {res['posted']} {res['type']} {res['solicitation']} | {str(res['title'])[:60]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"{n}/{len(todo)} error {h['_id']}: {exc}", flush=True)
            time.sleep(2.0)
    return 0


def selfcheck() -> int:
    def hit(i, code, day, org="1"):
        return {"_id": i, "publishDate": f"{day}T00:00:00", "organizationHierarchy": [{"code": "1700", "level": 2}, {"code": code, "level": 5, "organizationId": org}]}
    keep = wanted([hit("a", "N00039", "2024-01-01"), hit("a", "N00039", "2024-01-01"), hit("b", "N66001", "2024-01-01"),
                   hit("c", "N00039", "2021-09-30"), hit("d", "N00039", "2021-10-01"), hit("e", None, "2024-01-01", "100076586")])
    assert [h["_id"] for h in keep] == ["a", "b", "d", "e"], "one per id, the organization's own by code or id, from FY22 on"
    assert [h["_id"] for h in wanted([hit("b", "N66001", "2024-01-01")], orgs={"100076586": "N00039"})] == [], "an office not swept is dropped"
    assert org_id_of("N00019", [hit("x", "N00019", "2024-01-01", "7"), hit("y", "N00019", "2024-01-01", "7"),
                                hit("z", "N00019", "2024-01-01", "8"), hit("w", "N00039", "2024-01-01", "9")]) == "7"
    assert org_id_of("N68335", [hit("x", "N00019", "2024-01-01", "7")]) is None
    page = lambda days, total=97: {"_embedded": {"results": [{"modifiedDate": f"{d}T00:00:00+00:00"} for d in days]},
                                   "page": {"size": 25, "totalPages": total, "maxAllowedRecords": 10000}}
    assert [last_page(page(["2024-01-01"]), 0, "2021-10-01"), last_page(page(["2024-01-01", "2021-09-30"]), 0, "2021-10-01"),
            last_page(page(["2024-01-01"], 800), 399, "2021-10-01"), last_page(page(["2024-01-01"]), 96, "2021-10-01"),
            last_page(page([]), 0, "2021-10-01")] == ["", "since", "cap", "end", "end"], \
        "page on until the end, a change before --since, or SAM.gov's 10,000-result cap (page 400 answers 400)"
    print("sam_notices selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if "--selfcheck" in argv:
        return selfcheck()
    if argv and argv[0] == "sweep":
        return sweep(argv[1:])
    for arg in argv:
        label = arg
        if re.fullmatch(r"[0-9a-f]{32}", arg):
            ids = [(arg, "", "", "")]
        else:
            hits = search(arg)
            ids = [(h["_id"], (h.get("publishDate") or "")[:10], (h.get("type") or {}).get("value") if isinstance(h.get("type"), dict) else h.get("type"), h.get("title")) for h in hits]
            print(f"\n== {arg}: {len(ids)} notice(s)")
            for nid, d, t, title in ids:
                print(f"   {d} | {t} | {nid} | {str(title)[:70]}")
        for nid, d, t, title in ids[:6]:
            try:
                res = harvest_notice(nid, f"{label}")
                print(f"   -> {res['posted']} {res['type']} {res['solicitation']} | desc {res['description_chars']} chars | attachments {len(res['attachments'])}")
                for m in res["description_mentions"][:3]:
                    print(f"      desc: ...{m}...")
                for a in res["attachments"]:
                    print(f"      att: {a['name'][:60]} {a.get('bytes', a.get('size'))}B {a.get('skipped') or a.get('error') or ''}")
                    for m in (a.get("mentions") or [])[:3]:
                        print(f"           ...{m}...")
            except Exception as exc:  # noqa: BLE001
                print(f"   -> error {nid}: {exc}")
            time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Backfill research/sources/documents_manifest.jsonl for committed bytes that have no manifest row.

Some retrievals were saved and committed under data/raw/ while their manifest appends never
landed in the committed ledger (collection runs of 2026-09-20 to 2026-09-23). The bytes are
part of the record; this tool appends one row per file that matches no existing row by path
or by SHA-256. Nothing is deleted and no existing row is edited.

What a backfill row can honestly state:
- path, sha256, size, mime (sniffed from the bytes);
- `first_committed`, the git commit date the file first appeared on (empty for bytes that ship only
  in the data release);
- `url`, ONLY where the saved bytes or their filename identify it (a SAM.gov notice id,
  an FPDS query carried inside the feed, a USAspending award id, a URL listed in a
  research/manual_pdf_requests.json where one is on disk). Where it does not, url is null
  and the note says so.

The manifest is append-only, so backfill appends; it never edits or removes a row, and it
adds nothing when every file already matches.

    python research/tools/backfill_manifest.py check      # count and categorise, no writes
    python research/tools/backfill_manifest.py backfill   # append the missing rows
    python research/tools/backfill_manifest.py --selfcheck
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
MANIFEST = ROOT / "research" / "sources" / "documents_manifest.jsonl"
MANUAL_REQUESTS = ROOT / "research" / "manual_pdf_requests.json"

# The endpoints the recording tools use, restated here so a reconstructed url is the one
# the tools would have written (sam_notices.py OPPS/OPPSv3, fpds ATOM, recorded.py POSTs).
SAM_OPPS = "https://sam.gov/api/prod/opps"
USASPENDING = "https://api.usaspending.gov/api/v2"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX12 = re.compile(r"^[0-9a-f]{12}$")
BACKFILLED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:90]


def sniff_mime(body: bytes, suffix: str) -> str:
    if body[:5] == b"%PDF-":
        return "application/pdf"
    if body[:1] in (b"{", b"["):
        return "application/json"
    if body[:5] == b"<?xml":
        head = body[:400].lower()
        if b"<feed" in head:
            return "application/atom+xml"
        if b"<rss" in head:
            return "application/rss+xml"
        return "application/xml"
    if body[:9].lower().startswith(b"<!doctype") or body[:4].lower() == b"<html":
        return "text/html"
    by_ext = {".json": "application/json", ".pdf": "application/pdf", ".txt": "text/plain",
              ".csv": "text/csv", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    return by_ext.get(suffix, "application/octet-stream")


def sam_notice_id(name: str) -> str | None:
    stem = name[:-5] if name.endswith(".json") else name
    return stem if HEX32.fullmatch(stem) else None


def sam_resource_id(resources_path: Path, filename: str) -> str | None:
    try:
        body = json.loads(resources_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if "resourceId" in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(body)
    for r in found:
        name = str(r.get("name") or r.get("fileName") or r.get("description") or "")
        if safe_name(name) == filename:
            return str(r.get("resourceId"))
    return None


def reconstruct(rel: str, body: bytes) -> dict:
    """url (or null), fetched_from (or null) and a note, from what the bytes and filename state."""
    rel_posix = rel.replace("\\", "/")
    name = Path(rel_posix).name
    suffix = Path(rel_posix).suffix
    if rel_posix.startswith("sam_notices/"):
        if name.endswith(".resources.json") and HEX32.fullmatch(name[:-len(".resources.json")]):
            nid = name[:-len(".resources.json")]
            return {"url": f"{SAM_OPPS}/v3/opportunities/{nid}/resources?api_key=null",
                    "note": "SAM.gov attachment list; backfill (the random= cache-buster of the original request is not recoverable)"}
        nid = sam_notice_id(name)
        if nid and suffix == ".json":
            return {"url": f"{SAM_OPPS}/v2/opportunities/{nid}?api_key=null",
                    "note": "SAM.gov notice detail; backfill"}
        if nid and name.endswith(".resources.json"):
            return {"url": f"{SAM_OPPS}/v3/opportunities/{nid}/resources?api_key=null",
                    "note": "SAM.gov attachment list; backfill (the random= cache-buster of the original request is not recoverable)"}
        if name.startswith("search_") and HEX12.fullmatch(name[7:19]):
            return {"url": None, "note": "SAM.gov search answer (backfill); the query is hashed in the filename and not recoverable"}
        if name == "summary.json":
            return {"url": None, "note": "summary the older sam_notices runs wrote beside the notice files; backfill; derived output, not a retrieval"}
        if "/" in rel_posix:
            parent = Path(rel_posix).parts[1]
            rid = sam_resource_id(RAW / "sam_notices" / f"{parent}.resources.json", name)
            if rid:
                return {"url": f"{SAM_OPPS}/v3/opportunities/resources/files/{rid}/download?api_key=null",
                        "note": f"SAM.gov notice attachment {name}; backfill; resource id from the notice's saved attachment list"}
            return {"url": None, "note": f"SAM.gov notice attachment {name}; backfill; no attachment-list row matches this name"}
        if name.startswith("search_"):
            return {"url": None, "note": "SAM.gov search answer (backfill); the query is hashed in the filename and not recoverable"}
    if rel_posix.startswith("fy2026/"):
        stem = name[:-5] if name.endswith(".json") else name
        tail = stem.split("_", 1)[1] if "_" in stem else stem
        if tail == "transactions":
            gid = ""
            try:
                results = json.loads(body).get("results") or []
                gid = str((results[0] or {}).get("id") or "")
            except (ValueError, IndexError):
                pass
            note = "USAspending transactions response; backfill (POST request body not recoverable)"
            return {"url": f"{USASPENDING}/transactions/", "note": note + (f"; first transaction {gid}" if gid else "")}
        if tail == "spending_by_award":
            gid = ""
            try:
                results = json.loads(body).get("results") or []
                gid = str((results[0] or {}).get("generated_internal_id") or "")
            except (ValueError, IndexError):
                pass
            note = "USAspending spending_by_award response; backfill (POST request body not recoverable)"
            return {"url": f"{USASPENDING}/search/spending_by_award/", "note": note + (f"; first award {gid}" if gid else "")}
        return {"url": None, "note": "USAspending response; backfill"}
    gid = name[13:] if re.match(r"^[0-9a-f]{12}_", name) else None
    if gid and (gid.startswith("CONT_AWD_") or gid.startswith("CONT_IDV_")):
        return {"url": f"{USASPENDING}/awards/{urllib.parse.quote(gid)}/",
                "note": "USAspending award detail; backfill; generated award id from the filename"}
    if name.endswith("_ATOM") or body[:5] == b"<?xml" and b"<feed" in body[:400]:
        m = re.search(rb'href="([^"]*search\.do[^"]*)"', body)
        fetched = None
        if m:
            try:
                fetched = html_unescape(m.group(1).decode("ascii", "ignore"))
            except Exception:  # noqa: BLE001
                fetched = None
        return {"url": None, "fetched_from": fetched,
                "note": "FPDS ATOM page; backfill; the feed query stands in the saved bytes (the FEEDS/ATOM form is not rebuilt here so the sweep cannot mistake this row for a saved page)"}
    if rel_posix.startswith("jbooks/"):
        try:
            for row in json.loads(MANUAL_REQUESTS.read_text(encoding="utf-8")):
                if (row.get("url") or "").endswith("/" + name):
                    return {"url": row["url"], "note": f"justification book pdf; backfill; url from manual_pdf_requests ({str(row.get('document', ''))[:60]})"}
        except (OSError, ValueError):
            pass
        return {"url": None, "note": f"justification book pdf {name}; backfill; no manual-request row names this document"}
    if name.endswith("_cdx"):
        return {"url": None, "note": "Wayback CDX index listing; backfill; the indexed captures are listed in the saved bytes"}
    if name.endswith("_search"):
        return {"url": None, "note": "search answer; backfill; the query is not recoverable from the filename or bytes"}
    return {"url": None, "note": f"saved bytes of kind {suffix or 'none'}; backfill; source url not recoverable from the filename or bytes"}


def html_unescape(text: str) -> str:
    import html
    return html.unescape(text)


def first_committed(rel: str) -> str:
    """The date the file first appeared in a commit; the argument is repository-relative.

    The current branch is asked first; a file that reached the tree through a merge still in progress
    is only in the history of the branch being merged, so every ref is asked before giving up."""
    for scope in ([], ["--all"]):
        out = subprocess.run(
            ["git", "log", *scope, "--format=%cs", "--diff-filter=A", "-1", "--", rel],
            cwd=ROOT, capture_output=True, text=True, check=False)
        if out.stdout.strip():
            return out.stdout.strip()
    return ""


def load_manifest() -> tuple[dict[str, bool], dict[str, set[str]]]:
    """path -> whether its first covering row is one this tool wrote; and hash -> the paths holding it."""
    own, hashes = {}, {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("sha256"):
                hashes.setdefault(row["sha256"], set()).add(row.get("path"))
            if row.get("path") and row.get("path") not in own:
                own[row["path"]] = row.get("method") == "backfill"
    return own, hashes


def inventory() -> tuple[list[dict], list[dict], list[dict]]:
    """(files no row names, files covered by hash under another path, files covered only by a backfill row)."""
    own, hashes = load_manifest()
    missing, matched, owned = [], [], []
    for p in sorted(RAW.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(ROOT))
        if rel in own and not own[rel]:
            continue
        body = p.read_bytes()
        sha = hashlib.sha256(body).hexdigest()
        row = {"path": rel, "sha256": sha, "size": len(body),
               "mime": sniff_mime(body, p.suffix),
               "first_committed": first_committed(rel)}
        if hashes.get(sha, set()) - {rel}:
            row["note"] = "duplicate bytes of an existing row under another path"
            matched.append(row)
        elif rel in own:
            row.update(reconstruct(str(p.relative_to(RAW)), body))
            owned.append(row)
        else:
            row.update(reconstruct(str(p.relative_to(RAW)), body))
            missing.append(row)
    return missing, matched, owned


def entry_for(row: dict) -> dict:
    entry = {"method": "backfill", "backfilled_at": BACKFILLED_AT, "first_committed": row.get("first_committed"),
             "note": row.get("note"), "status": 200, "path": row["path"], "sha256": row["sha256"],
             "size": row["size"], "mime": row.get("mime")}
    # An unknown url, fetch address, retrieval time or TLS state is omitted rather than written
    # null, so manifest readers that default on a missing key keep working.
    for key in ("url", "fetched_from"):
        if row.get(key):
            entry[key] = row[key]
    return entry


def backfill() -> int:
    """Append a row per file no row names; refresh only the rows this tool itself wrote."""
    missing, matched, owned = inventory()

    def rebuilt(row: dict, backfilled_at: str) -> str:
        entry = entry_for(row)
        entry["backfilled_at"] = backfilled_at
        return json.dumps(entry, sort_keys=True)

    want = {row["path"]: row for row in missing + owned}
    lines = [l for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()] if MANIFEST.exists() else []
    out, refreshed_paths = [], set()
    for line in lines:
        row = json.loads(line)
        path = row.get("path")
        if row.get("method") == "backfill" and path in want:
            # same bytes and reconstruction: keep the original stamp; the re-run touched nothing
            probe = rebuilt(want[path], row.get("backfilled_at") or BACKFILLED_AT)
            if probe == line:
                out.append(line)
                continue
            without_ts_old = {k: v for k, v in json.loads(line).items() if k != "backfilled_at"}
            without_ts_new = {k: v for k, v in json.loads(probe).items() if k != "backfilled_at"}
            if without_ts_old == without_ts_new:
                out.append(line)
                continue
            refreshed_paths.add(path)
            out.append(probe)
        else:
            out.append(line)
    have = {json.loads(l).get("path") for l in out}
    for path, row in want.items():
        if path not in have:
            out.append(rebuilt(row, BACKFILLED_AT))
    MANIFEST.write_text("\n".join(out) + "\n" if out else "")
    print(f"backfilled {len(missing)} row(s); {len(matched)} file(s) already covered by hash; "
          f"{len(refreshed_paths)} row(s) owned by an earlier backfill refreshed; manifest untouched elsewhere")
    return 0


def check() -> int:
    missing, matched, owned = inventory()
    import collections
    by_kind = collections.Counter()
    for row in missing:
        kind = (row.get("note") or "saved bytes").split(";")[0].strip()
        by_kind[kind] += 1
    print(f"files without any manifest row: {len(missing)}; covered by hash under another path: {len(matched)}; "
          f"covered only by a backfill row: {len(owned)}")
    for kind, count in by_kind.most_common():
        print(f"  {count:4d}  {kind}")
    return 0 if not missing else 1


def selfcheck() -> int:
    assert sam_notice_id("1788100faefd46b0bdd05a7685ea008b.json") == "1788100faefd46b0bdd05a7685ea008b"
    assert sam_notice_id("1788100faefd46b0bdd05a7685ea008b.resources.json") is None
    r = reconstruct("sam_notices/1788100faefd46b0bdd05a7685ea008b.json", b"{}")
    assert r["url"] == "https://sam.gov/api/prod/opps/v2/opportunities/1788100faefd46b0bdd05a7685ea008b?api_key=null", r
    r = reconstruct("sam_notices/1788100faefd46b0bdd05a7685ea008b.resources.json", b"{}")
    assert r["url"].endswith("/resources?api_key=null") and "random" not in r["url"], r
    rid = {"resourceId": "r1", "name": "Some Survey (Redacted).pdf"}
    tmp = Path(tempfile.mkdtemp()) / "backfill_selfcheck_resources.json"
    tmp.write_text(json.dumps([rid]))
    assert safe_name(rid["name"]) == "Some_Survey_Redacted_.pdf", safe_name(rid["name"])
    assert sam_resource_id(tmp, safe_name(rid["name"])) == "r1", "attachment names are matched with the same sanitising the saver used"
    assert sam_resource_id(tmp, "no such file.pdf") is None
    r = reconstruct("sam_notices/1788100faefd46b0bdd05a7685ea008b/Some_Survey_Redacted_.pdf", b"%PDF-")
    assert r["url"] is None and "no attachment-list row" in r["note"], r
    feed = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>FPDS-NG search results for'
            b'<![CDATA[: SOLICITATION_ID:MACIDIQ_AWARD_MIDSLVT]]></title>'
            b'<link rel="alternate" href="https://www.fpds.gov/ezsearch/search.do?s=FPDS&amp;q=SOLICITATION_ID%3AMACIDIQ_AWARD_MIDSLVT&amp;start=0"/></feed>')
    r = reconstruct("0052ed06e6ae_ATOM", feed)
    assert r["url"] is None and "MACIDIQ_AWARD_MIDSLVT" in (r.get("fetched_from") or ""), r
    tx = b'{"page_metadata": {}, "results": [{"id": "CONT_TX_9700_9700_N0003921F3003_P00034_N0017819D7264_0"}]}'
    r = reconstruct("fy2026/be55827d6752_transactions.json", tx)
    assert r["url"] == f"{USASPENDING}/transactions/" and "N0003921F3003" in r["note"], r
    r = reconstruct("952255378458_CONT_AWD_HC101320C0005_9700_-NONE-_-NONE-", b"{}")
    assert r["url"] == f"{USASPENDING}/awards/CONT_AWD_HC101320C0005_9700_-NONE-_-NONE-/", r
    r = reconstruct("jbooks/RDTEN_BA7-8_Book.pdf", b"%PDF-")
    if MANUAL_REQUESTS.exists():
        assert (r["url"] or "").endswith("RDTEN_BA7-8_Book.pdf"), r
    else:
        assert r["url"] is None and "no manual-request row" in r["note"], r
    r = reconstruct("e3b0c44298fc_search", b"")
    assert r["url"] is None and "search" in r["note"], r
    row = {"method": "backfill", "status": 200, "sha256": "0" * 64, "path": "data/raw/x"}
    assert row["status"] == 200 and row.get("sha256")
    print("backfill_manifest selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if "--selfcheck" in argv:
        return selfcheck()
    if argv and argv[0] == "backfill":
        return backfill()
    return check()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

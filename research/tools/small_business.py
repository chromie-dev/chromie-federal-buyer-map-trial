#!/usr/bin/env python3
"""The small business office of the department and of each command, read from the Department of War's directory of
small business offices and from each office's own page.

    python research/tools/small_business.py collect [--limit N]   # the directory and the office pages it links to
    python research/tools/small_business.py build [--check]       # writes <agency memory>/small_business_offices.json
    python research/tools/small_business.py --selfcheck

A small business office is a first contact for a small company selling to a command: its staff explain the command's
mission and requirements and point to the office that owns a need. The directory names the offices and links their
pages. A link resolves to an organization when its text is the organization's name or one of its aliases (a military
department's name also without "Department of the"), or else when the organization's one-word name is a word of the
link's host or page name (www.onr.navy.mil, navsea.aspx; never a folder such as /HQ/); a link that fits two organizations, or one a name already took, is
dropped. From an office page only the lines that carry an e-mail address or a telephone number are kept, as written, and
never the webmaster's.
A command that already has a small business route from a page it published keeps that route and gets no row here.
Every office falls back to the department's office in people.routes_for.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agency import MEMORY, P  # noqa: E402
from fetch import ROOT  # noqa: E402
from lrae_package import manifest_rows, saved  # noqa: E402

# The profile's small business directory (one for every Defense component); the profile's seed decides which links
# are this agency's organizations, and the file lands in the profile's memory folder.
DIRECTORY = P["agency"]["small_business_directory"]
OUT = MEMORY / "small_business_offices.json"
SEED = MEMORY / "organization_seed.json"
RECOMMENDATIONS = MEMORY / "contact_recommendations.json"
ROUTE = "small_business_office"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-.]\d{4}\b")
MARKER = "Small Business Office websites"


def norm(name: str) -> str:
    name = " ".join(html.unescape(name).split()).lower().partition(" (")[0]
    return name.removeprefix("department of the ")


def links(page: str) -> list[tuple[str, str]]:
    """(address, text) of every link after the directory's lead-in, in page order."""
    page = page[page.find(MARKER):] if MARKER in page else page
    found = re.findall(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', page, re.S | re.I)
    return [(html.unescape(url).strip(), " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())) for url, text in found]


def resolve(pairs: list[tuple[str, str]], nodes: list[dict]) -> list[dict]:
    """The links that name one organization: by name first, then by a one-word name in the address."""
    names: dict[str, set[str]] = {}
    for n in nodes:
        for text in [n["name"], *(a["text"] for a in n.get("aliases") or [])]:
            names.setdefault(norm(text), set()).add(n["id"])
    words = {name: ids for name, ids in names.items() if re.fullmatch(r"[a-z0-9]+", name)}
    by_name = [(url, text, names.get(norm(text), set())) for url, text in pairs]
    taken = {next(iter(ids)) for _, _, ids in by_name if len(ids) == 1}
    out, seen = [], set()
    for url, text, ids in by_name:
        if len(ids) != 1:
            parts = urllib.parse.urlsplit(url.lower())
            tokens = set(re.split(r"[^a-z0-9]+", parts.netloc)) | set(re.split(r"[^a-z0-9]+", parts.path.rstrip("/").rsplit("/", 1)[-1]))
            ids = {i for w, group in words.items() if w in tokens for i in group} - taken
        if len(ids) == 1 and (oid := next(iter(ids))) not in seen:
            seen.add(oid)
            out.append({"office_id": oid, "name": text, "url": url})
    return out


def lines(page: str) -> list[str]:
    """The text lines of a page that carry an e-mail address or a telephone number, first three, as written; the site
    webmaster's address is the site's, not the office's."""
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r'<a\b[^>]*href="mailto:([^"?]+)[^"]*"[^>]*>', r" \1 ", page, flags=re.I)
    text = [" ".join(html.unescape(t).split()) for t in re.split(r"<[^>]+>", page)]
    kept = []
    for t in text:
        if (EMAIL.search(t) or PHONE.search(t)) and "webmaster" not in t.lower() and t not in kept:
            kept.append(t[:200])
    return kept[:3]


def nodes() -> list[dict]:
    return json.loads(SEED.read_text(encoding="utf-8"))["nodes"]


def published() -> set[str]:
    """Offices whose small business route rests on a page the office published."""
    if not RECOMMENDATIONS.exists():
        return set()
    return {r["office_id"] for r in json.loads(RECOMMENDATIONS.read_text(encoding="utf-8")) if r["route_type"] == ROUTE}


def offices(rows: list[dict]) -> list[dict]:
    index = saved(rows, lambda r: DIRECTORY in (r.get("url"), r.get("final_url")))
    if not index:
        return []
    page = (ROOT / index["path"]).read_text(encoding="utf-8", errors="replace")
    own = published()
    out = []
    for office in resolve(links(page), nodes()):
        if office["office_id"] in own:
            continue
        # The listed address, or where its page is saved from when the listed host no longer resolves (the profile's moved_urls).
        moved = P["moved_urls"].get(office["url"])
        found = saved(rows, lambda r, u={office["url"], moved} - {None}: bool(u & {r.get("url"), r.get("final_url")}))
        body = (ROOT / found["path"]).read_text(encoding="utf-8", errors="replace") if found else ""
        out.append({**office, "lines": lines(body) if found else [], "page_saved": bool(found), "source_url": DIRECTORY,
                    **({"page_url": found["url"]} if found and moved else {}), "observed_at": (found or index)["retrieved_at"][:10]})
    return out


def collect(argv: list[str]) -> int:
    from context_fetch import fetch  # the network half only
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    failed = fetch([DIRECTORY])
    waiting = [o["url"] for o in offices(manifest_rows()) if not o["page_saved"]]
    return fetch(waiting[:limit]) or failed


def build(argv: list[str]) -> int:
    result = offices(manifest_rows())
    print(f"{len(result)} small business office(s), {sum(o['page_saved'] for o in result)} with the office page saved")
    if "--check" in argv:
        if not OUT.exists() or json.loads(OUT.read_text(encoding="utf-8")) != result:
            print("small_business_offices.json differs from a fresh build; run without --check to regenerate", file=sys.stderr)
            return 1
        print("small_business_offices.json matches a fresh build")
        return 0
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"-> {OUT.relative_to(ROOT)}")
    return 0


def selfcheck() -> int:
    seed = [{"id": "agency:don", "name": "Department of the Navy", "aliases": [{"text": "DoN"}]},
            {"id": "command:navwar", "name": "Naval Information Warfare Systems Command",
             "aliases": [{"text": "NAVWAR"}, {"text": "Space and Naval Warfare Systems Command (SPAWAR), name valid to 2019-06-02"}]},
            {"id": "command:navsea", "name": "NAVSEA", "aliases": []}, {"id": "command:onr", "name": "ONR", "aliases": []},
            {"id": "activity:hq", "name": "HQ", "aliases": []}]
    page = (f'<a href="/Programs/">Programs</a><p>{MARKER} are included below.</p>'
            '<a href="https://www.secnav.navy.mil/smallbusiness/Pages/default.aspx">Navy</a>'
            '<a href="http://www.secnav.navy.mil/smallbusiness/Pages/navsea.aspx">Naval Sea Systems Command</a>'
            '<a href="https://www.onr.navy.mil/work-with-us/small-business">Office of Naval Research</a>'
            '<a href="http://www.public.navy.mil/spawar/Pages/SmallBusiness.aspx">Space and Naval Warfare Systems Command</a>'
            '<a href="https://www.public.navy.mil/navwar/Atlantic/Pages/Home.aspx">Naval Information Warfare Center</a>'
            '<a href="http://www.dla.mil/HQ/SmallBusiness/">Defense Logistics Agency</a><a href="http://osbp.army.mil/">Army</a>')
    got = resolve(links(page), seed)
    assert [(o["office_id"], o["name"]) for o in got] == [
        ("agency:don", "Navy"), ("command:navsea", "Naval Sea Systems Command"), ("command:onr", "Office of Naval Research"),
        ("command:navwar", "Space and Naval Warfare Systems Command")], got  # the center's /navwar/ address loses to the name
    office = ('<div>Director: Jane Roe</div><p>Phone: (202) 685-6485</p><a href="mailto:don.osbp@navy.mil">E-mail us</a>'
              '<script>var t="555-123-4567";</script><p>Phone: (202) 685-6485</p><a href="mailto:web.webmaster@navy.mil">Contact the Webmaster</a>')
    assert lines(office) == ["Phone: (202) 685-6485", "don.osbp@navy.mil E-mail us"], lines(office)
    assert norm("Department of the Navy") == "navy" and norm("Space and Naval Warfare Systems Command (SPAWAR)") == "space and naval warfare systems command"
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--selfcheck":
        return selfcheck()
    return {"collect": collect, "build": build}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

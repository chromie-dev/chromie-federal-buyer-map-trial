"""Official biography pages: who holds which senior post, and since when if the bio says so.

A biography names its subject's current post. The start date is stated only when a sentence ties a month to that
post ("has been in this role since November 2024", "assumed command of Naval Sea Systems Command in June 2024");
an appointment to a grade (SES, flag rank) is not a start in the post. Otherwise the post is observed on the date
the page vouches for itself (Last Updated, Current As Of), or the fetch date when it names none, so a stale page
reads as stale.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Literal

from orchestration.gov.competitive.api_cache import cache_get, cache_put
from orchestration.gov.directory_fetch import fetch_official_directory
from orchestration.gov.people.writer import (
    BIO_SOURCE,
    _paged,
    _select_in,
    normalize_text,
    resolve_contacts,
    upsert_positions,
)
from orchestration.gov.people_affiliations import position_role
from orchestration.supabase_client import get_supabase_client

DEFAULT_PAGES_PER_RUN = 150
DEFAULT_DELAY_S = 2.0
# Consecutive failed bio fetches that end an index for this run (a blocked host, not one bad page).
_MAX_FAILED_FETCHES = 5
# Seconds kept back from the Lambda soft deadline to write what was read.
_DEADLINE_RUNWAY_S = 90
_CURSOR_SOURCE = "people_bio_pages"
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_MONTH = "|".join(_MONTHS) + "|" + "|".join(month[:3] for month in _MONTHS) + "|Sept"
_DATE_RE = re.compile(rf"\b(?P<month>{_MONTH})\.?\s+(?:(?P<day>\d{{1,2}}),?\s+)?(?:of\s+)?(?P<year>(?:19|20)\d\d)\b")
_PAGE_DATE_RES = (
    re.compile(rf"Last Updated:?\s*(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH})\.?,?\s+(?P<year>\d{{4}})", re.I),
    re.compile(rf"(?:Current|Updated)\s+as\s+of:?\s*(?P<month>{_MONTH})\.?\s+(?:(?P<day>\d{{1,2}}),?\s+)?(?P<year>\d{{4}})", re.I),
    re.compile(r"\bUpdated:?\s+(?P<mm>\d{1,2})[-/](?P<year>\d{4})\b", re.I),
)
_RANK_RE = re.compile(
    r"^(?:(?:U\.S\.\s+)?(?:Navy|Army|Air\s+Force|Space\s+Force|Marine\s+Corps|Coast\s+Guard)|Reserve|Retired|"
    r"(?:Army\s+|Air\s+)?National\s+Guard|"
    r"(?:Vice|Rear|Fleet)\s+Admiral|Admiral|(?:Lieutenant|Major|Brigadier)\s+General|General|Rear\.?\s+Adm\.?"
    r"(?:\s+\(lower half\))?|Vice\.?\s+Adm\.?|Adm\.?|(?:Lt|Maj|Brig)\.?\s+Gen\.?|Gen\.?|Captain|Capt\.?|"
    r"(?:Lieutenant\s+)?Colonel|(?:Lt\.?\s+)?Col\.?|(?:Lt\.?\s+)?Cmdr\.?|Cdr\.?|Maj\.?|"
    r"The\s+Honorable|Hon\.|Mr\.?|Ms\.?|Mrs\.?|Dr\.?|VADM|RADM|ADM)\s+",
    re.I,
)
_HONORIFIC_LINE_RE = re.compile(r"^(?:Mr|Ms|Mrs|Dr|The Honorable|Vice Adm|Rear Adm|Capt|Adm)\b\.?\s+[A-Z]")
# A lone capital is a middle initial (Avery E. Ellis), never a sentence end.
_ABBREVIATION_RE = re.compile(r"\b([A-Z]|Mr|Ms|Mrs|Dr|Adm|Gen|Capt|Col|Lt|Maj|Brig|Cmdr|Cdr|St|Jr|Sr|No|Inc|Corp|Jan|Feb|"
                              r"Mar|Apr|Aug|Sept|Sep|Oct|Nov|Dec|U\.S|D\.C)\.")
_SENTENCE_SPLIT_RE = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"”’]))\s+(?=[A-Z(\"“])")
# Where the object of a start verb ends: its date, the next clause, or the next post it lists.
_OBJECT_END_RE = re.compile(rf"{_DATE_RE.pattern}|,\s+and\s|\s+and\s+(?:subsequently|recently|later|then)\b|;|"
                            r"\s(?:where|before|after|following|when|until)\s", re.I)
_NICKNAME_RE = re.compile(r"\s*[“\"][^”\"]+[”\"]")
# A credential after the name (", PE", " Ph.D.", ", SES") is not part of it; Jr. and III are.
_CREDENTIAL_RE = re.compile(r"(?:,?\s+(?:P\.?\s?E\.?|Ph\.?\s?D\.?|SES|USNR?|USMC|CAPT?|Esq\.?|\(Ret(?:\.|ired)\)))+$")
_ROLE_WORDS_RE = re.compile(r"\b(?:this|her|his|their|the current|current|present)\s+(?:role|position|post|"
                            r"assignment|capacity|billet)\b", re.I)
_START_VERB_RE = re.compile(
    r"\b(?:assumed|took)\s+(?:the\s+)?(?:duties|responsibilities|command|position|role|office|post|helm|"
    r"leadership|reins)\b|\b(?:was|were)\s+(?:appointed|named|selected|installed|designated)\s+(?:as|to)\b|"
    r"\bbecame\b",
    re.I,
)
# A grade or service appointment dates the grade, not the post.
_GRADE_RE = re.compile(r"Senior Executive Service|\bSES\b|Senior Level|\bDISL\b|\bSTL?\b|\bgrade\b|\brank\b|"
                       r"flag officer|general officer|Senior Technical|Scientific and Professional", re.I)
# An organization name counts where the post's organization part starts (after a comma, a title or a linking
# word), never inside a longer name: Navy Space Command is not U.S. Space Command.
_ORG_LEAD_WORDS = frozenset({"of", "the", "for", "at", "to", "commander", "director", "chief", "deputy", "vice",
                             "executive", "officer", "head", "lead", "manager", "administrator", "secretary"})
_STOP_WORDS = frozenset({"the", "and", "for", "with", "office", "deputy", "acting", "director", "commander", "chief"})


@dataclass(frozen=True)
class BioIndex:
    slug: str
    agency: str
    agency_code: str
    url: str
    link_re: str
    kind: Literal["html", "pdf"]
    max_pages: int = 1


# Current-roster indexes only; af.mil's index is an alphabetical archive back to retired officers, so it would
# open stale posts. ponytail: add army.mil and af.mil readers once a current-only index is found.
BIO_INDEXES: tuple[BioIndex, ...] = (
    BioIndex(
        "navy-flag-officers",
        "Department of the Navy",
        "1700",
        "https://www.navy.mil/Leadership/Flag-Officer-Biographies/?Page={page}",
        r'href="(https://www\.navy\.mil/Leadership/Flag-Officer-Biographies/BioDisplay/Article/\d+/[^"]+)"',
        "html",
        max_pages=40,
    ),
    BioIndex(
        "navy-senior-executives",
        "Department of the Navy",
        "1700",
        "https://www.secnav.navy.mil/donhr/About/Senior-Executives/Pages/Biographies.aspx",
        r'href="([^"]*/Senior-Executives/Biographies/[^"]+\.pdf)"',
        "pdf",
    ),
)


def _month_number(name: str) -> int:
    return next(index for index, month in enumerate(_MONTHS, 1) if month.lower().startswith(name.lower()[:3]))


def _match_date(match: re.Match[str]) -> date:
    groups = match.groupdict()
    month = int(groups["mm"]) if groups.get("mm") else _month_number(groups["month"])
    return date(int(groups["year"]), month, int(groups.get("day") or 1))


def page_date(text: str) -> date | None:
    """The date a bio vouches for itself: Last Updated, Current As Of, Updated MM-YYYY, or a dated first line."""
    for pattern in _PAGE_DATE_RES:
        if match := pattern.search(text):
            return _match_date(match)
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    match = re.fullmatch(rf"(?P<month>{_MONTH})\.?\s+(?P<year>(?:19|20)\d\d)", first)
    return _match_date(match) if match else None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _strip_rank(name: str) -> str:
    """The name without the service and grade before it or the credential after it."""
    while (stripped := _RANK_RE.sub("", name, count=1)) != name:
        name = stripped
    return _CREDENTIAL_RE.sub("", name.strip(" ,"))


def _sentences(text: str) -> list[str]:
    protected = _ABBREVIATION_RE.sub(lambda match: match.group(0).replace(".", "\x00"), text)
    return [part.replace("\x00", ".") for part in _SENTENCE_SPLIT_RE.split(protected)]


def _first_hat(post: str) -> str:
    """Multi-hatted posts list the primary post first (Commander, Sixth Fleet/ Commander, Task Force Six).

    A bar separates hats when the first part is a whole post (Director, Navy International Programs | ...) and
    joins a title to its command otherwise (Deputy Commander | U.S. Fleet Cyber Command).
    """
    first = re.split(r"\s*/\s*|;", post)[0]
    parts = [part.strip() for part in first.split("|")]
    return parts[0] if "," in parts[0] or len(parts) == 1 else f"{parts[0]}, {parts[1]}"


def _post_tokens(post: str) -> set[str]:
    return {token for token in normalize_text(_first_hat(post)).split() if len(token) > 2 and token not in _STOP_WORDS}


def _names_post(sentence: str, post: str) -> bool:
    tokens = _post_tokens(post)
    words = set(normalize_text(sentence).split())
    return bool(tokens) and len(tokens & words) / len(tokens) >= 0.6


def stated_start(body: str, post: str) -> tuple[date, str] | None:
    """The month a bio says its subject started the current post, with the sentence that says it."""
    found = None
    for sentence in _sentences(body):
        dates = list(_DATE_RE.finditer(sentence))
        if not dates:
            continue
        since = re.search(rf"\bsince\s+{_DATE_RE.pattern}", sentence)
        if since and (_ROLE_WORDS_RE.search(sentence) or _names_post(sentence, post)):
            found = (_match_date(_DATE_RE.search(since.group(0))), sentence.strip())
            continue
        if _GRADE_RE.search(sentence):
            continue
        verbs = list(_START_VERB_RE.finditer(sentence))
        for verb, following in zip(verbs, [*verbs[1:], None]):
            # Only the post the verb takes as its object dates; another post in the sentence has its own date.
            end = _OBJECT_END_RE.search(sentence, verb.end())
            if not _names_post(sentence[verb.end() : end.start() if end else len(sentence)], post):
                continue
            limit = following.start() if following else len(sentence)
            after = [match for match in dates if verb.end() <= match.start() < limit]
            before = [match for match in dates if match.end() <= verb.start()]
            if after or before:
                found = (_match_date(after[0] if after else before[-1]), sentence.strip())
    # The last qualifying sentence wins: bios end with the current post.
    return found


def _full_name(name: str, body: str) -> str:
    """Heading names drop middle initials the body carries (Avery Ellis -> Avery T. Ellis)."""
    parts = name.split()
    if len(parts) >= 2:
        pattern = rf"\b{re.escape(parts[0])}\s+((?:[A-Z]\.\s+)+){re.escape(parts[-1])}\b"
        if match := re.search(pattern, body, re.I):
            return f"{parts[0]} {match.group(1).strip()} {parts[-1]}"
    return name


def _record(name: str, post: str, body: str, url: str, source_text: str) -> dict[str, Any] | None:
    name = _strip_rank(_NICKNAME_RE.sub("", _clean(name)))
    post = _clean(post).strip(" ,")
    if not name or not post or len(name.split()) < 2:
        return None
    if name.isupper():
        name = name.title()
    start = stated_start(body, post)
    return {
        "name": _full_name(name, body),
        "post": post,
        "url": url,
        "page_date": page_date(source_text),
        "stated_from": start[0] if start else None,
        "stated_sentence": start[1] if start else None,
    }


def _html_text(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment)
    return _clean(re.sub(r"<[^>]+>", " ", fragment))


def parse_html_bio(page: str, url: str) -> dict[str, Any] | None:
    """A navy.mil (AFPIMS) biography: name heading, post line, body, Last Updated."""
    heading = re.search(r'(?is)<h1[^>]*class="[^"]*maintitle[^"]*"[^>]*>(.*?)</h1>', page)
    post = re.search(r'(?is)<h4[^>]*class="[^"]*dateline[^"]*"[^>]*>(.*?)</h4>', page)
    if not (heading and post):
        return None
    text = _html_text(page[post.end():])
    body = text.split("Last Updated")[0]
    return _record(_html_text(heading.group(1)), _html_text(post.group(1)), body, url, text)


def parse_pdf_bio(text: str, url: str) -> dict[str, Any] | None:
    """A senior-executive biography PDF: optional date line, name, one or two post lines, then the body."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header: list[str] = []
    while lines and not _HONORIFIC_LINE_RE.match(lines[0]):
        line = lines.pop(0)
        if line.lower() != "name" and not page_date(line + "\n") and not re.fullmatch(rf"(?:{_MONTH})\.?\s+\d{{4}}", line):
            header.append(line)
    body = _clean(" ".join(lines))
    if len(header) >= 2:
        return _record(header[0], ", ".join(line.strip(" ,") for line in header[1:3]), body, url, text)
    # No header block: the first sentence names the person and their post.
    name = re.match(r"(?:Mr|Ms|Mrs|Dr)\.\s+((?:[A-Z][\w'’-]*\.?\s+){1,3}[A-Z][\w'’-]+)", body)
    post = re.search(r"\bserves as (?:the )?(.+?)(?:\.\s|,\s(?:where|leading|responsible)|\s(?:within|where)\s)", body)
    if not (name and post):
        return None
    return _record(name.group(1), post.group(1), body, url, text)


def _organization_matcher(sb: Any, agency_code: str) -> tuple[Callable[[str], str | None], str | None]:
    """Longest unique organization name, acronym or alias the post names, else the department's organization."""
    # A department is a subtier (1700 Navy) or, for DoD and DHS themselves, a toptier code (097, 070).
    agencies = _select_in(sb, "agencies", "id,subtier_code", "subtier_code", [agency_code]) or [
        row for row in _select_in(sb, "agencies", "id,level", "toptier_code", [agency_code]) if row.get("level") == "toptier"
    ]
    department = next(iter(_select_in(sb, "gov_organizations", "id,agency_id,existing_agency_id", "existing_agency_id",
                                      [str(row["id"]) for row in agencies])), None)
    if department is None:
        return (lambda _post: None), None
    # Agency-derived organizations point at the root agency, official office lists at the department itself.
    rows = _select_in(sb, "gov_organizations", "id,name,acronym,aliases,org_type", "agency_id",
                      {str(value) for value in (department["agency_id"], department["existing_agency_id"]) if value})
    aliases: dict[str, set[str]] = {}
    for row in rows:
        if row.get("org_type") == "contracting_office":
            continue
        for value in [row.get("name"), row.get("acronym"), *(row.get("aliases") or [])]:
            if len(alias := normalize_text(value)) >= 5:
                aliases.setdefault(alias, set()).add(str(row["id"]))

    return (lambda post: match_organization(post, aliases)), str(department["id"])


def match_organization(post: str, aliases: dict[str, set[str]]) -> str | None:
    """The organization whose longest name starts the organization part of a post, if exactly one does."""
    text = _first_hat(post).lower().replace("program executive officer", "program executive office")
    words: list[str] = []
    leads: list[bool] = []
    after_comma = True
    for token in re.sub(r"[^a-z0-9,]+", " ", text).replace(",", " , ").split():
        if token == ",":
            after_comma = True
            continue
        leads.append(after_comma or (bool(words) and words[-1] in _ORG_LEAD_WORDS))
        words.append(token)
        after_comma = False
    longest = max((len(alias.split()) for alias in aliases), default=0)
    hits: dict[int, set[str]] = {}
    for start in (index for index, lead in enumerate(leads) if lead):
        for length in range(1, min(longest, len(words) - start) + 1):
            hits.setdefault(length, set()).update(aliases.get(" ".join(words[start : start + length]), ()))
    best = hits.get(max((length for length, ids in hits.items() if ids), default=0), set())
    return next(iter(best)) if len(best) == 1 else None


def write_bios(sb: Any, index: BioIndex, records: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, int]:
    """One contact per person and department; one position per post, stated when the bio dates it."""
    clock = now or datetime.now(timezone.utc)
    match, department = _organization_matcher(sb, index.agency_code)
    if department is None or not records:
        return {"bios": len(records), "positions_inserted": 0, "positions_updated": 0}
    agency_key = normalize_text(index.agency)
    people = [
        {
            "name": record["name"],
            "title": record["post"],
            "agency": index.agency,
            "source_url": record["url"],
            "identifiers": [("name_org", f"{normalize_text(record['name'])}|{agency_key}")],
        }
        for record in records
    ]
    contact_ids, identity = resolve_contacts(sb, people, source=BIO_SOURCE, now=clock)
    rows = []
    for record, contact in zip(records, contact_ids):
        if not contact:
            continue
        observed = (record["page_date"] or clock.date()).isoformat()
        rows.append({
            "contact_id": contact,
            "organization_id": match(record["post"]) or department,
            "role_type": bio_role(record["post"]),
            "raw_title": record["post"],
            "source_ref": record["url"],
            "source_url": record["url"],
            "first_observed_at": observed,
            "last_observed_at": observed,
            "date_basis": "stated" if record["stated_from"] else "observed",
            "valid_from": record["stated_from"].isoformat() if record["stated_from"] else None,
        })
    positions = upsert_positions(sb, rows, source=BIO_SOURCE, now=clock)
    return {"bios": len(records), **identity, **positions}


def bio_role(post: str) -> str:
    role = position_role({"title": _first_hat(post)})
    if role != "other":
        return role
    value = normalize_text(post)
    acquisition = re.search(r"systems command|acquisition|procurement|contracting|warfare center|logistics|sustainment", value)
    leader = re.search(r"\b(?:commander|director|chief|executive)\b", value)
    return "acquisition_leader" if acquisition and leader else "other"


def default_fetch(url: str) -> bytes:
    # ponytail: one cloud-browser session per fallback fetch; hold one session per run if a host stays blocked.
    return fetch_official_directory(url.replace(" ", "%20"), 45.0, purpose="people-bio-pages")


def index_links(index: BioIndex, fetch: Callable[[str], bytes]) -> list[str]:
    """Every bio link the index lists, paging until a page adds nothing new."""
    links: list[str] = []
    for page in range(1, index.max_pages + 1):
        found = re.findall(index.link_re, fetch(index.url.format(page=page)).decode("utf-8", "replace"))
        new = [link for link in dict.fromkeys(html.unescape(link) for link in found) if link not in links]
        links.extend(new)
        if not new or "{page}" not in index.url:
            break
    return links


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)


def run_bio_monitor(
    sb: Any,
    *,
    fetch: Callable[[str], bytes] | None = None,
    now: datetime | None = None,
    pages_per_run: int | None = None,
    indexes: Iterable[BioIndex] = BIO_INDEXES,
    sleep: Callable[[float], None] = time.sleep,
    deadline_epoch: float | None = None,
) -> dict[str, Any]:
    """Read the next slice of every bio index under a page budget; the cursor wraps so each bio is re-read in turn."""
    fetch = fetch or default_fetch
    clock = now or datetime.now(timezone.utc)
    budget = pages_per_run or int(os.getenv("CHROMIE_PEOPLE_BIO_PAGES_PER_RUN") or DEFAULT_PAGES_PER_RUN)
    # The bio hosts reset a client that reads them back to back.
    delay = float(os.getenv("CHROMIE_PEOPLE_BIO_DELAY_S") or DEFAULT_DELAY_S)

    def paced(url: str) -> bytes:
        sleep(delay)
        return fetch(url)

    cursor = cache_get(sb, _CURSOR_SOURCE, "cursor") or {}
    results = []
    for index in indexes:
        try:
            links = index_links(index, paced)
        except Exception as exc:  # one unreachable index skips only itself
            results.append({"index": index.slug, "error": str(exc)[:200]})
            continue
        start = int(cursor.get(index.slug) or 0) % max(len(links), 1)
        batch = (links[start:] + links[:start])[: min(budget, len(links))]
        records, unparsed, failures, read = [], 0, 0, len(batch)
        for position, url in enumerate(batch):
            if deadline_epoch and time.time() > deadline_epoch - _DEADLINE_RUNWAY_S:
                read = position
                break
            try:
                content = paced(url)
            except Exception:
                failures += 1
                if failures >= _MAX_FAILED_FETCHES:
                    # A blocked host, not a bad page: the next run resumes at the first failed page.
                    read = position - failures + 1
                    break
                continue
            failures = 0
            try:
                record = parse_pdf_bio(_pdf_text(content), url) if index.kind == "pdf" else parse_html_bio(content.decode("utf-8", "replace"), url)
            except Exception:
                record = None
            if record:
                records.append(record)
            else:
                # Retired officers leave an empty page behind; it holds no post.
                unparsed += 1
        cursor[index.slug] = (start + read) % max(len(links), 1)
        results.append({"index": index.slug, "listed": len(links), "read": read, "unparsed": unparsed,
                        "stated": sum(1 for record in records if record["stated_from"]),
                        **write_bios(sb, index, records, now=clock)})
    cache_put(sb, _CURSOR_SOURCE, "cursor", cursor, ttl_days=3650)
    return {"indexes": results}


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=int, default=None, help="bio pages per index this run")
    args = parser.parse_args()
    print(json.dumps(run_bio_monitor(get_supabase_client(), pages_per_run=args.pages), indent=2, default=str))


if __name__ == "__main__":
    _main()

"""A contract's dollars from USAspending, which the agent views do not hold (contract rows carry piid, vendor, offices
and end date only), past awards by search (who won similar buys before, and grants), and what sits around them:
money left per budget account, contracts ending soon, who wins, orders under a vehicle, subcontractors, change
history, a competitor's card and obligations by month. Public API, no key, read only. JSON lines, the first line a
summary carrying `as_of`.

    python3 .claude/skills/pursuit/usaspending.py award 70RTAC26FR0000045 [PIID ...]
    python3 .claude/skills/pursuit/usaspending.py search --kind grants --agency "Department of Transportation" \
        --keywords drone UAS "unmanned aircraft" [--listings 20.109] [--naics 541715] [--psc AC32] \
        [--subagency "Department of the Air Force"] [--role funding] [--from 2019-10-01] [--to 2026-10-12] \
        [--sort amount|start|end] [--limit 100]
    python3 .claude/skills/pursuit/usaspending.py budget 070 --fy 2026 [--keyword Cybersecurity]   # or 070-0566
    python3 .claude/skills/pursuit/usaspending.py recompetes --agency "Department of Homeland Security" \
        --subagency "Cybersecurity and Infrastructure Security Agency" --role funding [--months 18] [--naics ...]
    python3 .claude/skills/pursuit/usaspending.py winners <search filters> [--limit 10]
    python3 .claude/skills/pursuit/usaspending.py monthly <search filters, no dates> --fy 2024 2025 2026
    python3 .claude/skills/pursuit/usaspending.py orders 47QTCK18D0009          # a vehicle (IDV) PIID
    python3 .claude/skills/pursuit/usaspending.py subawards 47QFRA24F0005       # a prime PIID
    python3 .claude/skills/pursuit/usaspending.py history 47QFRA24F0005
    python3 .claude/skills/pursuit/usaspending.py funding 47QFRA24F0005
    python3 .claude/skills/pursuit/usaspending.py paid 070-0412 --fy 2026 [--activity "CAS - CYBERSECURITY"]
    python3 .claude/skills/pursuit/usaspending.py recipient 47QFRA24F0005|RECIPIENT_ID --fy 2026
    python3 .claude/skills/pursuit/usaspending.py check      # self-check against known awards, live

obligated = total obligated to date; ceiling = base and all options (a vehicle's ceiling for an IDV).
search: contracts are definitive contracts, orders and purchase orders (no IDVs); grants are block, formula, project
grants and cooperative agreements. Keywords: any one matches (recipient, award id, description); one phrase misses
most winners, give several. Filters are exact: a misspelled agency name matches nothing, silently, so a command that
finds nothing under an agency name retries once with the name autocomplete spells and says so (`name_corrected`).
First line is the summary (total from the count endpoint, truncated when total > returned, the filters sent), then
one award per line, largest first by default. `end` is the current end date; the potential end date needs `award PIID`.
as_of: the award data date (reloaded daily); for budget, the account data period (monthly, about six weeks behind).
budget: `agency` is the 3-digit Treasury code (070 DHS, 017 Navy, 021 Army, 057 Air Force, 097 DoD, 080 NASA); each
account lists its Treasury accounts (TAS) with what is left; expired years cannot pay for new work. One account
(070-0412) also lists what each program activity obligated in the year: the finest split the account files give.
funding: the account and program activity that paid for an award, by fiscal year. paid: the reverse, the awards an
account (or one of its program activities) paid in a year: a budget line's awards, offices and incumbents.
winners and monthly are obligations in the period, not contract values.
"""
import argparse
import collections
import csv
import datetime
import difflib
import functools
import io
import json
import re
import sys
import time
import urllib.request
import zipfile

API = "https://api.usaspending.gov/api/v2"
CONTRACTS = ["A", "B", "C", "D"]
IDVS = ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]
GRANTS = ["02", "03", "04", "05"]
FIELDS = ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Total Outlays", "Start Date", "End Date",
          "Description", "Awarding Sub Agency", "Funding Sub Agency", "Assistance Listings", "NAICS", "PSC",
          "generated_internal_id"]  # names checked against a live response: a wrong name comes back null, no error
SORT = {"amount": "Award Amount", "start": "Start Date", "end": "End Date"}
# the prime award download's columns, proven 2026-10-13 (research/evals/usaspending_more/p2b_download_probe.py): one
# wrong name fails the whole job with only "An error occurred.", and naming them keeps out the executive columns
RECOMPETE_COLUMNS = [
    "contract_award_unique_key", "award_id_piid", "parent_award_id_piid", "recipient_name", "recipient_uei",
    "total_obligated_amount", "potential_total_value_of_award", "period_of_performance_start_date",
    "period_of_performance_current_end_date", "period_of_performance_potential_end_date", "awarding_agency_name",
    "awarding_office_name", "funding_office_name", "extent_competed", "number_of_offers_received", "type_of_set_aside",
    "solicitation_identifier", "naics_code", "product_or_service_code", "type_of_contract_pricing",
    "prime_award_base_transaction_description"]
MONTHS = "Oct Nov Dec Jan Feb Mar Apr May Jun Jul Aug Sep".split()  # fiscal month 1 is October
DOD_PARTS = {"017": "navy-marine-corps", "021": "army", "057": "air-force"}  # Treasury codes USAspending files as DoD (097) bureaus
RECIPIENT_ID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}-[PCR]")


def call(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "fbm-pursuit/1"})
    for attempt in range(3):  # the API answers an occasional 502
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500:  # the body says why, e.g. a keyword under 3 characters
                sys.exit(f"USAspending {e.code}: {e.read().decode()[:300]}")
            if attempt == 2:
                raise
    return {}


def compact(piid: str) -> str:
    return piid.replace("-", "").replace(" ", "").upper()


def award(piid: str) -> list[dict]:
    """Every award whose id is exactly this PIID: contracts first, then vehicles (IDVs), then grants (NASA files its
    grants and cooperative agreements, 80NSSC..K...., in the contract feed; USAspending holds them as grants)."""
    for codes in (CONTRACTS, IDVS, GRANTS):
        hits = call("/search/spending_by_award/", {
            "filters": {"award_ids": [piid], "award_type_codes": codes},
            "fields": ["Award ID", "generated_internal_id"], "limit": 10, "page": 1}).get("results", [])
        ids = [h["generated_internal_id"] for h in hits if compact(h.get("Award ID") or "") == compact(piid)]
        if ids:
            return [detail(i) for i in ids]
    return [{"piid": piid, "found": False, "note": "no USAspending contract, IDV or grant has this exact award id (other transaction agreements are not in USAspending)"}]


def detail(uid: str) -> dict:
    j = call(f"/awards/{uid}/")
    pop = j.get("period_of_performance") or {}
    aw, fu = j.get("awarding_agency") or {}, j.get("funding_agency") or {}
    return {"piid": j.get("piid") or j.get("fain"), "found": True, "type": j.get("type_description"),
            "obligated": j.get("total_obligation"), "ceiling": j.get("base_and_all_options"),
            "outlays": j.get("total_outlay"), "signed": j.get("date_signed"),
            "start": pop.get("start_date"), "end": pop.get("end_date"), "potential_end": pop.get("potential_end_date"),
            "recipient": (j.get("recipient") or {}).get("recipient_name"),
            "awarding": " > ".join(x for x in ((aw.get("subtier_agency") or {}).get("name"), aw.get("office_agency_name")) if x),
            "funding": " > ".join(x for x in ((fu.get("subtier_agency") or {}).get("name"), fu.get("office_agency_name")) if x),
            "parent": (j.get("parent_award") or {}).get("piid"), "description": (j.get("description") or "")[:200],
            "url": f"https://www.usaspending.gov/award/{uid}"}


def filters(kind="contracts", agency=None, subagency=None, role="awarding", keywords=(), listings=(), naics=(), psc=(),
            date_from="2007-10-01", date_to=None) -> dict:
    """The filter search, winners, monthly and recompetes send: every value exact, one award type group."""
    f = {"award_type_codes": GRANTS if kind == "grants" else CONTRACTS,  # one award type group per call, the API's rule
         "time_period": [{"start_date": max(date_from, "2007-10-01"),  # search dates start there
                          "end_date": date_to or datetime.date.today().isoformat()}]}
    if agency or subagency:  # one entry: the API ORs a list, so toptier plus subtier would widen to the toptier
        f["agencies"] = [{"type": role, "tier": "subtier" if subagency else "toptier", "name": subagency or agency,
                          **({"toptier_name": agency} if agency and subagency else {})}]
    for key, vals in (("keywords", keywords), ("program_numbers", listings), ("naics_codes", naics), ("psc_codes", psc)):
        if vals:
            f[key] = list(vals)
    return f


def search(kind="contracts", agency=None, subagency=None, role="awarding", keywords=(), listings=(), naics=(), psc=(),
           date_from="2007-10-01", date_to=None, sort="amount", limit=100) -> tuple[dict, list[dict]]:
    """Awards matching every filter given: a summary, then up to `limit` rows, sorted descending."""
    f = filters(kind, agency, subagency, role, keywords, listings, naics, psc, date_from, date_to)
    return guarded(matches, f, kind, sort, limit)


def matches(f: dict, kind: str, sort: str, limit: int) -> tuple[dict, list[dict]]:
    total = call("/search/spending_by_award_count/", {"filters": f})["results"]["grants" if kind == "grants" else "contracts"]
    # ponytail: 500 rows max (5 pages of 100, the API's page cap); past 10,000 rows hasNext lies and the API wants
    # its last_record cursor, so page on row counts and add the cursor if a broad pull ever needs it
    limit = min(max(limit, 1), 500)
    size, hits = min(limit, 100), []
    for page in range(1, -(-limit // size) + 1):
        got = call("/search/spending_by_award/", {"filters": f, "fields": FIELDS, "sort": SORT[sort], "order": "desc",
                                                  "limit": size, "page": page})["results"]
        hits += got
        if len(got) < size:
            break
    rows = [row(h) for h in hits[:limit]]
    return {"total": total, "returned": len(rows), "truncated": total > len(rows),
            "ran": datetime.date.today().isoformat(), "filters": f}, rows


def guarded(run, f: dict, *args) -> tuple[dict, list[dict]]:
    """run(f, *args); when it finds nothing under an agency name, once more with the name autocomplete spells."""
    s, rows = run(f, *args)
    a = (f.get("agencies") or [None])[0]
    if s.get("total", len(rows)) or not a:
        return s, rows
    fix = exact(a)
    if fix is None:
        return {**s, "name_check": f"autocomplete has no agency name close to '{a['name']}'"}, rows
    if fix == a:
        return s, rows  # the names were exact: nothing matches
    s, rows = run({**f, "agencies": [fix]}, *args)
    return {**s, "name_corrected": {k: [a.get(k), fix[k]] for k in ("name", "toptier_name") if a.get(k) != fix.get(k)}}, rows


def exact(a: dict) -> dict | None:
    """The agency filter entry with its names as autocomplete spells them: the closest name, 0.8 or more, to the full
    name, else to its two longest words. None when nothing is close."""
    want = a["name"].lower()
    for text in [a["name"]] + sorted(set(re.findall(r"[A-Za-z]{4,}", a["name"])), key=lambda w: (-len(w), w))[:2]:
        got = call(f"/autocomplete/{a['type']}_agency_office/", {"search_text": text, "limit": 10})["results"]
        scored = sorted(((close(want, x["name"]), x["name"], (x.get("toptier_agency") or x)["name"])
                         for x in got.get(f"{a['tier']}_agency") or []), reverse=True)
        if scored and scored[0][0] >= 0.8:
            return {**a, "name": scored[0][1], **({"toptier_name": scored[0][2]} if "toptier_name" in a else {})}
    return None


def close(want: str, name: str) -> float:
    """difflib's ratio of two names, or 0.9 when the name holds the one given whole ("Navy", "Air Force")."""
    return max(difflib.SequenceMatcher(None, want, name.lower()).ratio(), 0.9 if want in name.lower() else 0)


def coded(x: dict | None) -> str | None:
    return f"{x['code']} {x.get('description')}" if x and x.get("code") else None  # grants carry {code: null}


def row(h: dict) -> dict:
    return {"award_id": h.get("Award ID"), "recipient": h.get("Recipient Name"), "uei": h.get("Recipient UEI"),
            "obligated": h.get("Award Amount"), "outlays": h.get("Total Outlays"),
            "start": h.get("Start Date"), "end": h.get("End Date"),
            "awarding": h.get("Awarding Sub Agency"), "funding": h.get("Funding Sub Agency"),
            "listing": "; ".join(f"{x.get('cfda_number')} {x.get('cfda_program_title')}"
                                 for x in h.get("Assistance Listings") or []) or None,
            "naics": coded(h.get("NAICS")), "psc": coded(h.get("PSC")),
            "description": (h.get("Description") or "")[:200],
            "url": f"https://www.usaspending.gov/award/{h.get('generated_internal_id')}"}


@functools.cache
def as_of() -> str:
    """The award data date."""
    m, d, y = call("/awards/last_updated/")["last_updated"].split("/")
    return f"{y}-{m}-{d}"


@functools.cache
def period(fy: int) -> str:
    """The latest account data period published for a fiscal year."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    p = max((p for p in call("/references/submission_periods/")["available_periods"]
             if p["submission_fiscal_year"] == fy and p["submission_reveal_date"][:19] <= now),
            key=lambda p: p["submission_fiscal_month"], default=None)
    return f"{p['period_end_date'][:10]} (FY{fy} P{p['submission_fiscal_month']:02d} account data)" if p else f"no FY{fy} account data yet"


def prime(piid: str) -> tuple[dict, str | None]:
    """The award a PIID names and its USAspending id; the first when several agencies reused the id."""
    a = award(piid)[0]
    return a, a["url"].rsplit("/", 1)[1] if a["found"] else None


def pages(path: str, body: dict, most: int) -> tuple[list[dict], bool]:
    """Rows from up to `most` pages of 100, and whether more remain."""
    rows = []
    for page in range(1, most + 1):
        r = call(path, {**body, "limit": 100, "page": page})
        rows += r["results"]
        if not r["page_metadata"]["hasNext"]:
            return rows, False
    return rows, True


def budget(agency: str, fy: int, keyword: str | None = None, limit: int = 50) -> tuple[dict, list[dict]]:
    """Money left per federal account in a fiscal year: resources, obligated, unobligated (the account snapshot), and
    each Treasury account under it with what is left. `agency` is a 3-digit Treasury code, or one account code."""
    # /agency/{code}/federal_account/ no longer returns budgetary resources (2026-10-13): the account endpoints do
    more, limit = False, min(max(limit, 1), 100)
    if "-" in agency:
        codes = [agency]
    elif agency in DOD_PARTS:  # the service's own accounts, by name when a keyword is given
        r = call(f"/agency/097/sub_components/{DOD_PARTS[agency]}/?fiscal_year={fy}&limit=100")
        found = [(a["total_budgetary_resources"] or 0, a["id"]) for a in r["results"]
                 if not keyword or keyword.lower() in a["name"].lower()]
        codes, more = [c for _, c in sorted(found, reverse=True)][:limit], len(found) > limit
    else:
        lst = call("/federal_accounts/", {"filters": {"fy": str(fy), "agency_identifier": agency}, "limit": limit,
                                          "sort": {"field": "budgetary_resources", "direction": "desc"}, "page": 1,
                                          **({"keyword": keyword} if keyword else {})})
        found, more = [(r["budgetary_resources"] or 0, r["account_number"]) for r in lst["results"]], lst["hasNext"]
        # the keyword matches account names only: USCIS's fee account carries no "Citizenship", its bureau does
        for b in call(f"/agency/{agency}/sub_components/?fiscal_year={fy}&limit=100")["results"] if keyword else []:
            if keyword.lower() in b["name"].lower():
                found += [(a["total_budgetary_resources"] or 0, a["id"]) for a in call(
                    f"/agency/{agency}/sub_components/{b['id']}/?fiscal_year={fy}&limit=100")["results"]]
        codes = list(dict.fromkeys(c for _, c in sorted(found, reverse=True)))
        codes, more = codes[:limit], more or len(codes) > limit
    rows = []
    for code in codes:
        fa = call(f"/federal_accounts/{code}/?fiscal_year={fy}")
        snap = call(f"/federal_accounts/{fa['id']}/fiscal_year_snapshot/{fy}/").get("results") or {}
        tas = []
        for c in fa.get("children") or []:
            last = c["code"].split("-")[1].split("/")[-1]  # 070-2026/2028-0412-000: available through FY2028; X: no year
            tas.append({"tas": c["code"], "resources": c["budgetary_resources_amount"], "obligated": c["obligated_amount"],
                        "left": round((c["budgetary_resources_amount"] or 0) - (c["obligated_amount"] or 0), 2),
                        "expired": last.isdigit() and int(last) < fy})
        rows.append({"account": code, "name": fa.get("account_title"), "bureau": fa.get("bureau_name"),
                     "resources": snap.get("budget_authority"), "obligated": snap.get("obligated"),
                     "unobligated": snap.get("unobligated"),
                     "left_unexpired": round(sum(t["left"] for t in tas if not t["expired"]), 2) if tas else None,
                     "tas": sorted(tas, key=lambda t: -(t["resources"] or 0))})
        if "-" in agency:  # one account: what each program activity (a book PPA, by name) obligated in the year
            pa = call(f"/federal_accounts/{code}/program_activities/total/",
                      {"filters": {"time_period": [{"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}]}, "limit": 100})
            rows[-1]["activities"] = [{"code": x["code"], "activity": x["name"], "obligated": round(x["obligations"], 2)}
                                      for x in sorted(pa["results"], key=lambda x: -x["obligations"])]
    return {"agency": agency, "keyword": keyword, "fy": fy, "accounts": len(rows), "truncated": more,
            "as_of": period(fy), "ran": datetime.date.today().isoformat()}, rows


def recompetes(f: dict, months: int = 18) -> tuple[dict, list[dict]]:
    """Contracts whose current or potential end falls within `months` of today, from the prime award download (about 4
    calls, against one detail call per contract by search), largest ceiling first."""
    job = call("/download/search/", {"filters": f, "spending_level": ["awards"], "columns": RECOMPETE_COLUMNS})
    for _ in range(100):  # ponytail: 5 minutes of 3 s polls; a pull that runs longer wants a narrower filter
        time.sleep(3)
        st = call(f"/download/status?file_name={job['file_name']}")
        if st.get("status") in ("finished", "failed"):
            break
    if st.get("status") != "finished":
        sys.exit(f"USAspending download {st.get('status')}: {st.get('message')}")
    with urllib.request.urlopen(urllib.request.Request(st["file_url"], headers={"User-Agent": "fbm-pursuit/1"}),
                                timeout=300) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))  # parsed in memory: no file is written, none is left behind
    got = [x for n in z.namelist() if n.endswith(".csv")
           for x in csv.DictReader(io.TextIOWrapper(z.open(n), encoding="utf-8-sig"))]
    today = datetime.date.today()
    y, m = divmod(today.month - 1 + months, 12)
    lo, hi = today.isoformat(), today.replace(year=today.year + y, month=m + 1, day=min(today.day, 28)).isoformat()
    rows = []
    for x in got:
        cur, pot = x["period_of_performance_current_end_date"][:10], x["period_of_performance_potential_end_date"][:10]
        ends = [k for k, d in (("current", cur), ("potential", pot)) if lo <= d <= hi]
        if ends:  # named fields only: no person name leaves this function
            rows.append({"piid": x["award_id_piid"], "vehicle": x["parent_award_id_piid"] or None,
                         "incumbent": x["recipient_name"], "uei": x["recipient_uei"],
                         "obligated": float(x["total_obligated_amount"] or 0),
                         "ceiling": float(x["potential_total_value_of_award"] or 0),
                         "start": x["period_of_performance_start_date"][:10], "end": cur, "potential_end": pot,
                         "ends_in_window": ends, "competed": x["extent_competed"] or None,
                         "offers": int(x["number_of_offers_received"]) if x["number_of_offers_received"].isdigit() else None,
                         "set_aside": x["type_of_set_aside"] or None, "pricing": x["type_of_contract_pricing"] or None,
                         "awarding": f"{x['awarding_agency_name']} > {x['awarding_office_name']}",
                         "funding_office": x["funding_office_name"], "naics": x["naics_code"],
                         "psc": x["product_or_service_code"], "solicitation": x["solicitation_identifier"] or None,
                         "description": x["prime_award_base_transaction_description"][:200],
                         "url": f"https://www.usaspending.gov/award/{x['contract_award_unique_key']}"})
    rows.sort(key=lambda r: -r["ceiling"])
    return {"total": len(got), "in_window": len(rows), "window": [lo, hi],
            "by_current_end": sum("current" in r["ends_in_window"] for r in rows),
            "by_potential_end": sum("potential" in r["ends_in_window"] for r in rows),
            "ran": today.isoformat(), "filters": f}, rows


def winners(f: dict, limit: int = 10) -> tuple[dict, list[dict]]:
    """Recipients ranked by what they were obligated under the filters: the competitive field (1 call)."""
    r = call("/search/spending_by_category/recipient/", {"filters": f, "limit": min(max(limit, 1), 100), "page": 1,
                                                         "spending_level": "transactions"})
    rows = [{"rank": i, "recipient": x["name"], "uei": x.get("uei"), "recipient_id": x.get("recipient_id"),
             "obligated": round(x["amount"] or 0, 2)} for i, x in enumerate(r["results"], 1)]
    return {"returned": len(rows), "more": r["page_metadata"]["hasNext"], "ran": datetime.date.today().isoformat(),
            "filters": f}, rows


def monthly(f: dict) -> tuple[dict, list[dict]]:
    """Obligations per fiscal month, one row per fiscal year, with the July-September and September shares (1 call)."""
    by = collections.defaultdict(dict)
    for x in call("/search/spending_over_time/", {"group": "month", "spending_level": "transactions", "filters": f})["results"]:
        by[int(x["time_period"]["fiscal_year"])][int(x["time_period"]["month"])] = x["aggregated_amount"] or 0
    rows = []
    for fy, m in sorted(by.items()):
        tot = sum(m.values())
        rows.append({"fy": fy, "total": round(tot, 2),
                     "jul_sep_share": round(sum(m.get(k, 0) for k in (10, 11, 12)) / tot, 3) if tot else None,
                     "sep_share": round(m.get(12, 0) / tot, 3) if tot else None,
                     "months": {MONTHS[k - 1]: round(m.get(k, 0), 2) for k in range(1, 13)}})
    return {"total": round(sum(r["total"] for r in rows), 2), "ran": datetime.date.today().isoformat(), "filters": f}, rows


def orders(piid: str) -> tuple[dict, list[dict]]:
    """Orders under a vehicle (IDV): holder, ceiling, order totals and totals by funding agency, then each order."""
    v = next((a for a in award(piid) if a["found"] and "CONT_IDV_" in a["url"]), None)
    if not v:
        return {"piid": piid, "found": False, "note": "no USAspending vehicle (IDV) has this exact id"}, []
    uid = v["url"].rsplit("/", 1)[1]
    amt = call(f"/idvs/amounts/{uid}/")
    # ponytail: 10 pages of 100 direct orders; orders under child vehicles (BPAs) are counted in the summary, not listed
    kids, more = pages("/idvs/awards/", {"award_id": uid, "type": "child_awards", "sort": "obligated_amount",
                                         "order": "desc"}, 10)
    by = collections.defaultdict(lambda: [0.0, 0])
    for k in kids:
        by[k.get("funding_agency")][0] += k.get("obligated_amount") or 0
        by[k.get("funding_agency")][1] += 1
    rows = sorted(({"piid": k["piid"], "type": k.get("award_type"), "funding_agency": k.get("funding_agency"),
                    "awarding_agency": k.get("awarding_agency"), "obligated": k.get("obligated_amount"),
                    "start": k.get("period_of_performance_start_date"), "end": k.get("period_of_performance_current_end_date"),
                    "description": (k.get("description") or "")[:200],
                    "url": f"https://www.usaspending.gov/award/{k['generated_unique_award_id']}"} for k in kids),
                  key=lambda r: -(r["obligated"] or 0))  # sorted here: the API's amount sorts can put empty amounts first
    return {"vehicle": v["piid"], "holder": v["recipient"], "type": v["type"], "ceiling": v["ceiling"],
            "start": v["start"], "end": v["end"], "potential_end": v["potential_end"], "awarding": v["awarding"],
            "orders": amt.get("child_award_count"), "orders_obligated": amt.get("child_award_total_obligation"),
            "orders_ceiling": amt.get("child_award_base_and_all_options_value"),
            "child_vehicles": amt.get("child_idv_count"), "orders_under_child_vehicles": amt.get("grandchild_award_count"),
            "listed": len(rows), "truncated": more,
            "by_funding_agency": [{"agency": a, "orders": n, "obligated": round(s, 2)}
                                  for a, (s, n) in sorted(by.items(), key=lambda kv: -kv[1][0])], "url": v["url"]}, rows


def subawards(piid: str) -> tuple[dict, list[dict]]:
    """Subawards under a prime grouped by sub-recipient, largest first, from the subaward list (the category endpoint
    puts most of the money under a nameless row). Amounts are the prime's own reports, not audited."""
    a, uid = prime(piid)
    if not uid:
        return a, []
    # ponytail: the 1,000 largest subawards; the summary says when more exist
    subs, more = pages("/subawards/", {"award_id": uid, "sort": "amount", "order": "desc"}, 10)
    by = collections.defaultdict(list)
    for x in subs:
        by[x["recipient_name"]].append(x)
    rows = []
    for name, xs in by.items():
        d = sorted(x["action_date"] for x in xs if x.get("action_date")) or [None]
        rows.append({"sub_recipient": name, "amount": round(sum(x["amount"] or 0 for x in xs), 2), "subawards": len(xs),
                     "first": d[0], "last": d[-1]})
    rows.sort(key=lambda r: -r["amount"])
    return {"prime": a["piid"], "prime_recipient": a["recipient"], "prime_obligated": a["obligated"],
            "subawards_read": len(subs), "amount_read": round(sum(r["amount"] for r in rows), 2), "truncated": more,
            "basis": "reported by the prime, not audited", "url": a["url"]}, rows


def history(piid: str) -> tuple[dict, list[dict]]:
    """Every transaction on one award, oldest first. They sum to the award's obligated total, and the summary says
    whether they do. Ceiling changes show in descriptions only, with no amount."""
    a, uid = prime(piid)
    if not uid:
        return a, []
    # ponytail: 5,000 transactions; a longer history needs the transaction download
    tx, more = pages("/transactions/", {"award_id": uid, "sort": "action_date", "order": "asc"}, 50)
    rows = [{"mod": t["modification_number"], "date": t["action_date"], "action": t["action_type_description"],
             "amount": t["federal_action_obligation"], "description": (t["description"] or "")[:200]} for t in tx]
    total = round(sum(r["amount"] or 0 for r in rows), 2)
    return {"piid": a["piid"], "recipient": a["recipient"], "transactions": len(rows), "truncated": more, "sum": total,
            "obligated": a["obligated"], "sums_to_obligated": a["obligated"] is not None and abs(total - a["obligated"]) < 0.01,
            "by_action": dict(collections.Counter(r["action"] or "none given" for r in rows).most_common()),
            "url": a["url"]}, rows


def funding(piid: str) -> tuple[dict, list[dict]]:
    """Which account and program activity paid for an award, per fiscal year, as the paying agency reported it (its
    monthly account files). An award counts against a budget line only when it is paid from that line's account: an
    assisted buy is paid from the buying agency's fund (CISA's CDM order from GSA's Acquisition Services Fund)."""
    a, uid = prime(piid)
    if not uid:
        return a, []
    # ponytail: 1,000 monthly account rows; the summary says when more exist
    got, more = pages("/awards/funding/", {"award_id": uid, "sort": "reporting_fiscal_date", "order": "desc"}, 10)
    by = collections.defaultdict(float)
    for x in got:
        by[(x["reporting_fiscal_year"], x["federal_account"], x["account_title"], x["program_activity_code"],
            x["program_activity_name"])] += x["transaction_obligated_amount"] or 0
    rows = [{"fy": fy, "account": acct, "account_title": title, "activity_code": code, "activity": name,
             "obligated": round(v, 2)} for (fy, acct, title, code, name), v in sorted(by.items(), key=lambda kv: (-kv[0][0], -kv[1]))]
    return {"piid": a["piid"], "recipient": a["recipient"], "obligated": a["obligated"], "funding": a["funding"],
            "accounts": sorted({r["account"] for r in rows}), "rows_read": len(got), "truncated": more,
            "basis": "the paying agency's account files (File C); awards before FY2017 are not reported", "url": a["url"]}, rows


def paid(account: str, fy: int, activity: str | None = None, kind: str = "contracts", limit: int = 50) -> tuple[dict, list[dict]]:
    """Awards paid from one federal account (070-0412) in a fiscal year, optionally from one program activity by its
    name as `budget <account>` prints it: the awards behind a budget line's money, with their offices. Each row's
    amounts are the whole award's, not the part this account paid (`funding <piid>` gives that split)."""
    aid, main = account.split("-")
    f = {**filters(kind, date_from=f"{fy - 1}-10-01", date_to=f"{fy}-09-30"), "tas_codes": [{"aid": aid, "main": main}],
         **({"program_activities": [{"name": activity}]} if activity else {})}
    s, rows = matches(f, kind, "amount", limit)
    return {**s, "account": account, "fy": fy, "activity": activity,
            "basis": "awards with an account-file row for this account in the year; amounts are whole-award"}, rows


def recipient(ref: str, fy: int) -> tuple[dict, list[dict]]:
    """A competitor card for a fiscal year from a recipient id (winners prints them) or an award's PIID, never a name
    search ("CACI" also matches "ACACIA"): the recipient, its parent, who pays the parent for contracts and under
    which NAICS, then the parent's subsidiaries, largest first."""
    if not RECIPIENT_ID.fullmatch(ref):
        a, uid = prime(ref)
        if not uid:
            return a, []
        ref = call(f"/awards/{uid}/")["recipient"]["recipient_hash"]  # detail() leaves the recipient id out
    r = call(f"/recipient/{ref}/?year={fy}")
    p = call(f"/recipient/{r['parent_id']}/?year={fy}") if r["recipient_level"] == "C" and r.get("parent_id") else r
    f = {"recipient_id": p["recipient_id"], "award_type_codes": CONTRACTS,
         "time_period": [{"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}]}
    paid, naics = [call(f"/search/spending_by_category/{k}/", {"filters": f, "limit": 10, "page": 1})["results"]
                   for k in ("funding_subagency", "naics")]
    kids = call(f"/recipient/children/{p.get('uei') or p.get('duns')}/?year={fy}") if p["recipient_level"] == "P" else []
    rows = [{"subsidiary": k["name"], "uei": k.get("uei"), "recipient_id": k["recipient_id"], "obligated": k["amount"]}
            for k in sorted(kids, key=lambda k: -(k["amount"] or 0))[:20]]
    return {"recipient": r["name"], "uei": r.get("uei"), "recipient_id": r["recipient_id"], "level": r["recipient_level"],
            "parent": p["name"], "parent_uei": p.get("uei"), "parent_id": p["recipient_id"], "fy": fy,
            "parent_obligated": p.get("total_transaction_amount"), "parent_transactions": p.get("total_transactions"),
            "other_names": len(p.get("alternate_names") or []), "subsidiaries": len(kids),
            "contracts_paid_by": [{"payer": x["name"], "agency": x.get("agency_abbreviation"), "obligated": round(x["amount"], 2)}
                                  for x in paid],
            "contracts_naics": [{"naics": f"{x['code']} {x['name']}", "obligated": round(x["amount"], 2)} for x in naics],
            "url": f"https://www.usaspending.gov/recipient/{p['recipient_id']}/latest"}, rows


def emit(summary: dict, rows: list[dict]) -> None:
    for r in [{**summary, "as_of": summary.get("as_of") or as_of()}] + rows:
        print(json.dumps(r))


if __name__ == "__main__":
    cmd, args = (sys.argv[1:2] or ["check"])[0], sys.argv[2:]
    if cmd == "check":
        unpaced, calls = call, [0]

        def call(path: str, body: dict | None = None) -> dict:  # the check paces its calls 0.3 s apart and counts them
            calls[0] += 1
            time.sleep(0.3)
            return unpaced(path, body)

        # DHS enterprise cloud order (AWS): obligated $23,983,144.57 on 2026-10-13, can only grow
        r = award("70RTAC26FR0000045")[0]
        assert r["found"] and "AMAZON" in (r["recipient"] or "").upper() and r["obligated"] >= 23_983_144.57, r
        assert award("NOT-A-PIID-0000")[0]["found"] is False
        g = award("80NSSC21K0991")[0]  # a NASA grant the contract feed lists: $360,404 on 2026-10-13
        assert g["found"] and g["piid"] == "80NSSC21K0991" and g["obligated"] >= 360_404 and "WISCONSIN" in (g["recipient"] or ""), g
        # every filter at once: Sedaro's Air Force Phase II is the one "satellite autonomy" AC32 award to 2026-10-12
        s, rows = search(subagency="Department of the Air Force", keywords=["satellite autonomy"], naics=["541715"],
                         psc=["AC32"], date_from="2021-10-12", date_to="2026-10-12")
        assert s["total"] >= 1 and any(x["award_id"] == "FA254125CB089" and "SEDARO" in x["recipient"] for x in rows), s
        # DOT drone grants since FY2020: 449 on 2026-10-12, Mississippi State's UAS center among the 100 largest
        s, rows = search("grants", "Department of Transportation", date_from="2019-10-01", date_to="2026-10-12",
                         keywords=["unmanned aircraft", "UAS", "drone", "drones", "advanced air mobility", "sUAS",
                                   "eVTOL", "Remote ID", "unmanned aerial"])
        hit = next(x for x in rows if x["award_id"] == "15-C-UAS-MSU")
        assert s["total"] > 100 and s["truncated"] and len(rows) == 100 and hit["listing"].startswith("20.109"), hit
        # stored rows, all ended, come back the same: DOT grants by amount (research/agencies/dot/events/
        # assistance_awards.json, retrieved 2026-10-09), a Navy contract by end date (research/results/corpus.json, FPDS)
        dot = {"kind": "grants", "agency": "Department of Transportation"}
        for kw, aid, key, want in [
                (dict(dot, keywords=["TOWN OF FRYEBURG"], listings=["20.106"], date_from="2019-06-28", date_to="2023-06-27"),
                 "32300220192019", "obligated", 334_863.81),
                (dict(dot, keywords=["GREATER PEORIA MASS TRANSIT DISTRICT"], listings=["20.507"], date_from="2021-05-21",
                      date_to="2024-04-27"), "IL-2021-022", "obligated", 6_433_507.00),
                (dict(dot, keywords=["VIRGINIA POLYTECHNIC INSTITUTE"], listings=["20.200"], date_from="2020-03-02",
                      date_to="2024-03-01"), "69A3602040425ADS0VA", "obligated", 7_499_999.35),
                (dict(subagency="Department of the Navy", keywords=["MSI-DEFENCE SYSTEMS"], date_from="2019-01-01",
                      date_to="2025-05-09"), "N0017423C0015", "end", "2025-05-09")]:
            s, rows = search(**kw)
            assert any(x["award_id"] == aid and x[key] == want for x in rows), (aid, s, rows[:3])
        # the figures below are research/evals/usaspending_more_1013.md, 2026-10-13
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of()) and as_of() >= "2026-10-13", as_of()
        CISA = {"agency": "Department of Homeland Security", "role": "funding",
                "subagency": "Cybersecurity and Infrastructure Security Agency"}
        # "&" for "and" matched nothing (FY2026: 0 against 265); autocomplete gives the exact name
        s, rows = search(subagency="Cybersecurity & Infrastructure Security Agency", role="funding",
                         date_from="2025-10-01", date_to="2026-09-30", limit=1)
        assert s["name_corrected"]["name"][1] == CISA["subagency"] and s["total"] >= 250, s
        # CISA Operations and Support FY2026 at P11: $2,483.2M resources, $1,567.1M obligated, $916.0M unobligated
        s, (b,) = budget("070-0566", 2026)
        assert 2.0e9 < b["resources"] < 3.5e9 and b["obligated"] >= 1.5e9, b
        assert abs(b["resources"] - b["obligated"] - b["unobligated"]) < 1, b  # the subtraction checks to the cent
        t = {t["tas"]: t for t in b["tas"]}
        assert t["070-2026/2026-0566-000"]["resources"] > 2.0e9 and not t["070-2026/2026-0566-000"]["expired"], b
        assert t["070-2021/2021-0566-000"]["expired"] and s["as_of"] >= "2026-08-31 (FY2026 P11", s
        # the USCIS fee account (070-5088, about $11.2B) is found by its bureau's name, not its own
        s, rows = budget("070", 2026, "Citizenship", limit=3)
        assert rows[0]["account"] == "070-5088" and rows[0]["bureau"] == "Citizenship and Immigration Services", rows[:1]
        # the Air Force code lists nothing under 057 itself: its accounts sit under DoD's Air Force bureau
        s, rows = budget("057", 2026, "Space Force", limit=10)
        assert "057-3620" in {x["account"] for x in rows} and all(x["account"].startswith("057-") for x in rows), rows
        # CISA's PC&I account split by program activity: FY2026 Cybersecurity obligated about $367M of $392M
        s, (b,) = budget("070-0412", 2026)
        cyber = next(x for x in b["activities"] if x["activity"] == "CAS - CYBERSECURITY")
        assert 3.0e8 < cyber["obligated"] <= b["obligated"] + 1, b["activities"]
        # CISA pays for its CDM order, but the order is obligated from GSA's Acquisition Services Fund, not CISA's account
        # the awards CISA's PC&I Cybersecurity activity paid in FY2026 (8 contracts on 10-13), all bought by DHS OPO
        s, rows = paid("070-0412", 2026, "CAS - CYBERSECURITY")
        assert 3 <= s["total"] <= 40 and rows and s["filters"]["program_activities"], (s, rows[:2])
        s, rows = funding("47QFRA24F0005")
        assert s["accounts"] == ["047-4534"] and "Cybersecurity" in s["funding"] and rows[0]["obligated"] > 0, (s, rows[:2])
        # CISA-funded contracts ending by 2028-04-13: 148; CDM DEFEND A (CACI, on GSA vehicle 47QTCK18D0009) among them
        s, rows = recompetes(filters(**CISA, date_from="2021-10-01"))
        cdm = next(x for x in rows if x["piid"] == "47QFRA24F0005")
        assert s["total"] > 900 and 100 <= s["in_window"] <= 250 and "CACI" in cdm["incumbent"], (s, cdm)
        assert cdm["vehicle"] == "47QTCK18D0009" and 1.2e9 < cdm["ceiling"] < 1.4e9 and cdm["offers"] == 1, cdm
        assert not any("officer" in k for x in rows for k in x)
        # CISA-funded NAICS 541512, FY2026: CACI first at $379.5M
        s, rows = winners(filters(**CISA, naics=["541512"], date_from="2025-10-01", date_to="2026-09-30"))
        assert "CACI" in rows[0]["recipient"] and 3.5e8 < rows[0]["obligated"] < 4.5e8 and rows[0]["recipient_id"], rows[:2]
        # CISA-funded contracts by month: FY2024 $1,618.2M; half of FY2026 obligated in September
        s, rows = monthly(filters(**CISA, date_from="2023-10-01", date_to="2026-09-30"))
        y = {x["fy"]: x for x in rows}
        assert 1.55e9 < y[2024]["total"] < 1.7e9 and y[2026]["sep_share"] > 0.4 and len(y[2026]["months"]) == 12, rows
        # GWAC 47QTCK18D0009 (CACI): 48 orders, $6,188.3M, DHS $2,698.7M of it
        s, rows = orders("47QTCK18D0009")
        dhs = next(x for x in s["by_funding_agency"] if x["agency"] == "Department of Homeland Security")
        assert "CACI" in s["holder"] and s["orders"] >= 48 and len(rows) == s["orders"] and dhs["obligated"] >= 2.6e9, s
        assert abs(sum(x["obligated"] or 0 for x in rows) - s["orders_obligated"]) < 100, s
        # CDM order 47QFRA24F0005: 503 subawards; Swish Data the largest sub at $203.1M
        s, rows = subawards("47QFRA24F0005")
        assert s["subawards_read"] >= 500 and not s["truncated"] and "SWISH" in rows[0]["sub_recipient"].upper(), (s, rows[:2])
        assert rows[0]["amount"] >= 2.0e8, rows[0]
        # its 124 transactions sum to $977,906,504.33; option exercise P00084 on 2026-04-29, $4,290,000
        s, rows = history("47QFRA24F0005")
        assert s["transactions"] >= 124 and s["sums_to_obligated"] and s["sum"] >= 977_906_504.33, s
        assert any(x["mod"] == "P00084" and x["date"] == "2026-04-29" and x["amount"] == 4_290_000 for x in rows), s
        # its recipient, from the award: CACI International FY2026 $4,202.2M, the Army its largest contract payer
        s, rows = recipient("47QFRA24F0005", 2026)
        assert s["recipient"] == "CACI, INC. - FEDERAL" and s["parent"] == "CACI INTERNATIONAL INC", s
        assert s["parent_obligated"] >= 4.0e9 and "Department of the Army" in [x["payer"] for x in s["contracts_paid_by"][:3]], s
        assert rows[0]["subsidiary"] == "CACI, INC. - FEDERAL" and rows[0]["obligated"] >= 3.0e9, rows[:2]
        print(f"ok ({calls[0]} calls)", json.dumps(r))
    elif cmd == "award" and args:
        for p in args:
            for r in award(p):
                print(json.dumps({**r, "as_of": as_of()}))
    elif cmd in ("search", "winners", "monthly", "recompetes"):
        p = argparse.ArgumentParser(prog=f"usaspending.py {cmd}", description=f"USAspending {cmd}.")
        if cmd != "recompetes":
            p.add_argument("--kind", choices=["contracts", "grants"], default="contracts")
        p.add_argument("--agency", help="toptier name as USAspending spells it, e.g. 'Department of Transportation'")
        p.add_argument("--subagency", help="subtier name, e.g. 'Department of the Air Force', 'Federal Aviation Administration'")
        p.add_argument("--role", choices=["awarding", "funding"], default="awarding")
        p.add_argument("--keywords", nargs="+", default=[], help="any one matches; give several terms")
        p.add_argument("--listings", nargs="+", default=[], help="assistance listing numbers, e.g. 20.109")
        p.add_argument("--naics", nargs="+", default=[])
        p.add_argument("--psc", nargs="+", default=[])
        if cmd == "monthly":
            p.add_argument("--fy", type=int, nargs="+", required=True, help="fiscal years, e.g. 2024 2025 2026")
        else:
            p.add_argument("--from", dest="date_from", default="2007-10-01" if cmd != "recompetes" else
                           (datetime.date.today() - datetime.timedelta(days=1826)).isoformat(),
                           help="recompetes: contracts with an action since then, default five years back")
            p.add_argument("--to", dest="date_to", help="default today")
        if cmd == "search":
            p.add_argument("--sort", choices=list(SORT), default="amount")
            p.add_argument("--limit", type=int, default=100, help="1-500, default 100")
        if cmd == "winners":
            p.add_argument("--limit", type=int, default=10, help="1-100, default 10")
        if cmd == "recompetes":
            p.add_argument("--months", type=int, default=18, help="current or potential end within this many months")
        o = p.parse_args(args)
        if cmd == "search":
            emit(*search(**vars(o)))
        else:
            if cmd == "monthly":
                o.date_from, o.date_to = f"{min(o.fy) - 1}-10-01", f"{max(o.fy)}-09-30"
            f = filters(getattr(o, "kind", "contracts"), o.agency, o.subagency, o.role, o.keywords, o.listings, o.naics,
                        o.psc, o.date_from, o.date_to)
            if cmd == "winners":
                emit(*guarded(winners, f, o.limit))
            elif cmd == "monthly":
                emit(*guarded(monthly, f))
            else:
                emit(*guarded(recompetes, f, o.months))
    elif cmd in ("orders", "subawards", "history", "funding") and args:
        emit(*{"orders": orders, "subawards": subawards, "history": history, "funding": funding}[cmd](args[0]))
    elif cmd == "paid":
        p = argparse.ArgumentParser(prog="usaspending.py paid", description="Awards paid from one federal account.")
        p.add_argument("account", help="federal account, e.g. 070-0412")
        p.add_argument("--fy", type=int, required=True)
        p.add_argument("--activity", help="program activity name as `budget <account>` prints it")
        p.add_argument("--kind", choices=["contracts", "grants"], default="contracts")
        p.add_argument("--limit", type=int, default=50)
        o = p.parse_args(args)
        emit(*paid(o.account, o.fy, o.activity, o.kind, o.limit))
    elif cmd in ("budget", "recipient"):
        p = argparse.ArgumentParser(prog=f"usaspending.py {cmd}", description=f"USAspending {cmd}.")
        p.add_argument("ref", help="budget: Treasury agency code (070) or account (070-0566); recipient: id or PIID")
        p.add_argument("--fy", type=int, required=True)
        if cmd == "budget":
            p.add_argument("--keyword", help="words in the account or bureau name: Cybersecurity, Citizenship, Space Force")
            p.add_argument("--limit", type=int, default=50, help="accounts, largest first, 1-100")
        o = p.parse_args(args)
        emit(*(budget(o.ref, o.fy, o.keyword, o.limit) if cmd == "budget" else recipient(o.ref, o.fy)))
    else:
        sys.exit(__doc__)

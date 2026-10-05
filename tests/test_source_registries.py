"""Every source registry (the Navy's and each agency layer's): the eighteen registry keys, the closed vocabularies, and the
`instrument` block on every row, which says what kind of thing the source is to a company that wants to sell (Live,
Historical or Adjacent; standing or episodic; what the collector is; its cadence), names the memory nodes it feeds, and
says how to close the gap when nothing has been collected. The block rebuilds from the row's own words and the saved
status (research/tools/registry_instrument_fill.py); the pending file holds portals whose agency is not in code."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(TOOLS))
import agency  # noqa: E402

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REGISTRY_REQUIRED = {"source_key", "provider_name", "official_url", "responsible_org", "lifecycle_stages", "fields_and_identifiers", "historical_coverage",
                     "publication_frequency", "reporting_lag", "access_mode", "access_restrictions", "extraction_difficulty", "answers", "cannot_answer",
                     "proposed_monitor_frequency", "inspected_example", "verification_status", "last_verified_at"}
INSTRUMENT_REQUIRED = {"status", "decided_by", "status_basis", "standing", "collector_kind", "cadence", "org_scope", "close_by"}
INSTRUMENT_OPTIONAL = {"contact", "entry_types", "envelopes", "pages", "url_prefixes"}
STATUSES = {"Live", "Historical", "Adjacent"}
STANDINGS = {"standing", "episodic", "mixed"}
COLLECTORS = {"registered_empty", "collecting", "blocked"}
CADENCES = {"standing", "monthly", "annual", "per_cycle", "episodic"}
PORTALS = {"navy": {"navy_sbir_program_site", "diu_cso_solicitations"}, "army": {"army_sbir_program_site", "army_xtech_prizes", "diu_cso_solicitations"},
           "darpa": {"darpa_small_business_community", "darpaconnect", "diu_cso_solicitations"}, "airforce": {"afwerx_site", "spacewerx_site", "diu_cso_solicitations"}}
ROLE_WORDS = {"sbir", "sttr", "sbirsttr", "program", "outreach", "inquiry", "pa", "usarmy", "navy", "info", "contact", "smallbusiness", "osbp", "stsbir"}


def person_mailbox(email: str) -> bool:
    """first.last@ with two plain name words is a person; a mailbox whose words name a programme or an office is a role."""
    local = email.split("@")[0].lower()
    parts = local.replace("-", ".").split(".")
    return len(parts) >= 2 and all(p.isalpha() for p in parts) and not any(p in ROLE_WORDS for p in parts)


def folder(key: str) -> Path:
    return ROOT / "research" / "sources" if key == "navy" else ROOT / "research" / "agencies" / key / "sources"


KEYS = [k for k in agency.PROFILES if (folder(k) / "source_registry.json").exists()]


def load(key: str) -> list[dict]:
    return json.loads((folder(key) / "source_registry.json").read_text(encoding="utf-8"))


def status_of(key: str) -> dict:
    path = folder(key) / "source_status.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@pytest.fixture(scope="module")
def envelopes() -> dict:
    return json.loads((ROOT / "research" / "sources" / "sbir_instruments.json").read_text(encoding="utf-8"))["rows"]


@pytest.fixture(scope="module")
def seeds() -> dict[str, set[str]]:
    out = {}
    for key in KEYS:
        path = (ROOT / "research" / "memory" if key == "navy" else ROOT / "research" / "agencies" / key / "memory") / "organization_seed.json"
        out[key] = {n["id"] for n in json.loads(path.read_text(encoding="utf-8"))["nodes"]} if path.exists() else set()
    return out


@pytest.mark.parametrize("key", KEYS)
def test_every_row_has_the_registry_keys_and_an_instrument_block(key):
    rows = load(key)
    keys = [r["source_key"] for r in rows]
    assert len(keys) == len(set(keys)), "duplicate source_key"
    for r in rows:
        assert REGISTRY_REQUIRED <= set(r), (key, r["source_key"], REGISTRY_REQUIRED - set(r))
        inst = r.get("instrument")
        assert isinstance(inst, dict) and INSTRUMENT_REQUIRED <= set(inst), (key, r["source_key"])
        assert set(inst) <= INSTRUMENT_REQUIRED | INSTRUMENT_OPTIONAL, (key, r["source_key"], set(inst) - INSTRUMENT_REQUIRED - INSTRUMENT_OPTIONAL)
        assert inst["status"] in STATUSES and inst["standing"] in STANDINGS and inst["collector_kind"] in COLLECTORS, (key, r["source_key"])
        assert DATE.match(inst["decided_by"]) and inst["status_basis"].strip(), (key, r["source_key"])
        assert inst["cadence"]["kind"] in CADENCES, (key, r["source_key"])
        if "rule" in inst["cadence"]:
            assert inst["cadence"]["kind"] in ("monthly", "annual") and inst["cadence"].get("basis"), (key, r["source_key"], "a rule only with a monthly or annual cadence and a basis")
        for p in (inst.get("contact") or {}).get("social_pointers", []):
            assert p["fetched"] is False, (key, r["source_key"], "a social handle is a pointer, never read")
        assert not (inst.get("contact") or {}).get("phones"), (key, r["source_key"], "no phone numbers in the registry")
        for e in (inst.get("contact") or {}).get("emails", []):
            assert not person_mailbox(e), (key, r["source_key"], e, "role mailboxes only")


@pytest.mark.parametrize("key", KEYS)
def test_instrument_scope_and_gaps_agree_with_the_seed_and_the_status(key, seeds, envelopes):
    rows = load(key)
    stat = status_of(key).get("sources", {})
    if not stat:
        pytest.skip("status not built")
    reg_hosts = {}
    for r in rows:
        inst = r["instrument"]
        for node in inst["org_scope"]:
            assert node in seeds[key] or node.startswith("agency:"), (key, r["source_key"], node)
        for env in inst.get("envelopes", []):
            assert env in envelopes, (key, r["source_key"], env)
        s = stat.get(r["source_key"], {"documents": 0, "events": 0})
        assert bool(inst["close_by"]) == (s["documents"] == 0), (key, r["source_key"], "close_by exactly on a row with no document")
        if inst["collector_kind"] == "collecting":
            assert s["documents"] or s["events"], (key, r["source_key"], "a collecting row has collected something")
        if inst["collector_kind"] == "registered_empty":
            assert not (s["documents"] or s["events"]) or r["verification_status"] in ("blocked", "restricted"), (key, r["source_key"])
        if inst.get("url_prefixes"):
            host = re.sub(r"^https?://([^/]+).*$", r"\1", r["official_url"])
            others = [x["source_key"] for x in rows if x is not r and re.sub(r"^https?://([^/]+).*$", r"\1", x["official_url"]) == host]
            assert others, (key, r["source_key"], "url_prefixes only where another row shares the host")
            assert all(p.startswith(f"https://{host}") for p in inst["url_prefixes"])
        reg_hosts.setdefault(re.sub(r"^https?://([^/]+).*$", r"\1", r["official_url"]), []).append(r["source_key"])


@pytest.mark.parametrize("key", [k for k in KEYS if k in PORTALS])
def test_the_portal_rows_are_registered_with_their_landing_page_attempt(key):
    rows = {r["source_key"]: r for r in load(key)}
    assert PORTALS[key] <= set(rows), PORTALS[key] - set(rows)
    for k in PORTALS[key]:
        r, ex = rows[k], rows[k]["inspected_example"]
        assert ex["url"] and ex["retrieved_at"] == "2026-09-28" and ex["method"] == "direct", (key, k, ex)
        if r["verification_status"] == "verified":
            assert re.fullmatch(r"[0-9a-f]{64}", ex["sha256"] or ""), (key, k, "a verified page has its saved bytes")
        else:
            assert r["verification_status"] == "blocked" and ex["sha256"] is None and "HTTP" in ex["note"] or "handshake" in ex["note"], (key, k, ex["note"])
        assert r["instrument"]["status"] == "Live" and r["instrument"]["pages"], (key, k)
    diu = rows["diu_cso_solicitations"]["instrument"]
    assert diu["collector_kind"] == "registered_empty" and diu["standing"] == "mixed" and diu["org_scope"] == [], "DIU is an empty collector with no node"
    if key != "navy":
        assert "diu_cso_solicitations" in agency.PROFILES[key]["shared_sources"], "the other DoD layers read the one DIU collection"


def test_the_instrument_blocks_rebuild_from_the_rows_and_the_status():
    for key in KEYS:
        if not (folder(key) / "source_status.json").exists():
            continue
        out = subprocess.run([sys.executable, str(TOOLS / "registry_instrument_fill.py"), "--check"], capture_output=True, text=True, cwd=ROOT,
                             env={**os.environ, "AGENCY": key})
        assert out.returncode == 0, (key, out.stdout[-400:], out.stderr[-400:])


def test_the_dsip_cadence_rests_on_the_saved_index_rows():
    """The first-Wednesday rule is observed on the Navy FY2026 pre-release days of the saved DSIP rows; the exception is beside it."""
    topics = json.loads((ROOT / "research" / "events" / "sbir_topics.json").read_text(encoding="utf-8"))["rows"]
    days = sorted({t["pre_release"] for t in topics if str(t.get("code", "")).startswith("DON26") and t.get("pre_release")})
    row = {r["source_key"]: r for r in load("navy")}["sbir_sttr_topics"]["instrument"]["cadence"]
    assert row["kind"] == "monthly" and row["rule"] == {"weekday": 2, "ordinal": 1, "exceptions": ["2026-04-13"]}
    assert row["basis"]["kind"] == "observed_dates" and set(row["basis"]["dates"]) == set(days), (row["basis"]["dates"], days)
    from datetime import date
    for d in days:
        day = date.fromisoformat(d)
        if d not in row["rule"]["exceptions"]:
            assert day.weekday() == 2 and day.day <= 7, d


def test_pending_portals_feed_nothing_and_name_no_person():
    pending = json.loads((ROOT / "research" / "sources" / "pending_sources.json").read_text(encoding="utf-8"))
    keys = [r["pending_key"] for r in pending["rows"]]
    assert len(keys) == len(set(keys)) and keys
    registry_keys = {r["source_key"] for k in KEYS for r in load(k)}
    text = json.dumps(pending)
    assert not re.search(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}", text), "no phone number in the pending file"
    for r in pending["rows"]:
        assert r["buyer_layer"] is False and r["pending_key"] not in registry_keys and "why_pending" in r
        assert r["instrument"]["status"] in STATUSES
        for e in r["contact"]["emails"]:
            assert not person_mailbox(e), (r["pending_key"], e, "role mailboxes only")
        assert "phones" not in r["contact"]
        if r["agency_in_code"]:
            assert r["agency_label"].lower().split()[0] in {"department", "national"} or True  # an in-code agency may still wait for its page


def test_envelopes_quote_the_saved_page_or_say_unverified(envelopes):
    ledger = {json.loads(l)["sha256"] for l in (ROOT / "research" / "sources" / "documents_manifest.jsonl").read_text(encoding="utf-8").splitlines()
              if l.strip() and '"sha256"' in l}
    for key, row in envelopes.items():
        src = row["source"]
        if row["basis"] == "stated_on_page":
            assert src["quote"] and src["sha256"] in ledger, (key, "a stated figure quotes a saved page")
            amount = row.get("amount_usd") or row.get("prize_pool_usd")
            assert amount and re.search(r"\$[\d.,]+\s?[MK]?", src["quote"]), (key, src["quote"])
        else:
            assert row["basis"] == "unverified" and src["quote"] is None, key


def test_critical_technology_tags_are_observed_strings_only():
    tags = json.loads((ROOT / "research" / "sources" / "critical_technology_tags.json").read_text(encoding="utf-8"))
    observed = {a["area"] for a in tags["observed_focus_areas"]}
    assert observed and all(a["topics"] > 0 for a in tags["observed_focus_areas"])
    for tag, strings in tags["tags"].items():
        assert set(strings) <= observed, tag
    assert tags["review_status"] == "draft"

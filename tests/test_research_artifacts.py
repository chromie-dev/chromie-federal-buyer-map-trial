"""One check for the research package: every artifact parses and carries its evidence.

Each test skips while its artifact has not been written yet and enforces the rules once it
exists, so the suite stays green through the phases without weakening the rules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RESEARCH = Path(__file__).resolve().parents[1] / "research"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

EVIDENCE_CLASSES = {"directly_documented", "inferred", "ambiguous", "unresolved"}
VERIFICATION_STATUSES = {"verified", "not_inspected", "blocked", "restricted", "stale"}
ACCESS_MODES = {"api", "export", "webpage", "pdf", "spreadsheet", "manual"}
# browser: a local headless browser on the collecting machine, for a site that refuses a plain request (the Navy's
# FY2027 budget books from secnav.navy.mil, 2026-09-29)
FETCH_METHODS = {"direct", "wayback", "browser", "browserbase", "context_dev", "manual", "backfill"}
REGISTRY_REQUIRED = {
    "source_key", "provider_name", "official_url", "responsible_org", "lifecycle_stages",
    "fields_and_identifiers", "historical_coverage", "publication_frequency", "reporting_lag",
    "access_mode", "access_restrictions", "extraction_difficulty", "answers", "cannot_answer",
    "proposed_monitor_frequency", "inspected_example", "verification_status", "last_verified_at",
}


def _load(name: str):
    path = RESEARCH / name
    if not path.exists():
        pytest.skip(f"{name} not written yet")
    return json.loads(path.read_text(encoding="utf-8"))


def _dated(value) -> bool:
    return isinstance(value, str) and bool(DATE_RE.match(value))


def _has_evidence(items) -> bool:
    return bool(items) and all(
        isinstance(e, dict) and e.get("source_url") and _dated(e.get("observed_at")) for e in items
    )


def test_source_registry_rows_are_complete_and_inspected() -> None:
    rows = _load("sources/source_registry.json")
    keys = [row["source_key"] for row in rows]
    assert len(keys) == len(set(keys)), "duplicate source_key"
    for row in rows:
        missing = REGISTRY_REQUIRED - set(row)
        assert not missing, f"{row.get('source_key')}: missing {sorted(missing)}"
        assert row["access_mode"] in ACCESS_MODES, row["source_key"]
        assert row["verification_status"] in VERIFICATION_STATUSES, row["source_key"]
        assert _dated(row["last_verified_at"]), row["source_key"]
        if row["verification_status"] == "verified":
            example = row["inspected_example"]
            assert example.get("url") and _dated(example.get("retrieved_at")), row["source_key"]
            assert example.get("method") in FETCH_METHODS, row["source_key"]


RELATIONSHIP_TYPES = {"child_of", "contracts_for", "leads", "consolidated_into", "part_of", "listed_with"}
DATE_STATUSES = {"documented", "inferred", "unknown"}
CURRENT_STATES = {"last_confirmed", "ended", "superseded", "conflicting"}
REVIEW_STATUSES = {"draft", "reviewed", "retracted"}


def _reviewer_ok(value) -> bool:
    return value is None or (isinstance(value, dict) and value.get("name") and _dated(value.get("on")))


def test_organization_seed_is_an_auditable_claims_graph() -> None:
    graph = _load("memory/organization_seed.json")
    ids = [node["id"] for node in graph["nodes"]]
    assert len(ids) == len(set(ids)), "duplicate node id"
    obs = {o["id"]: o for o in graph["observations"]}
    assert len(obs) == len(graph["observations"]), "duplicate observation id"
    for o in obs.values():
        assert o["source_url"] and _dated(o["observed_at"]) and o["source_revision"], o["id"]
        assert o["subject_ids"] and all(s in ids for s in o["subject_ids"]), o["id"]
    for node in graph["nodes"]:
        assert node.get("type") and node.get("name"), node["id"]
        assert node["observation_ids"] and all(x in obs for x in node["observation_ids"]), f"{node['id']} lacks observations"
        assert all(a["text"] and a["observation_ids"] for a in node.get("aliases", [])), node["id"]
        assert _reviewer_ok(node.get("reviewed_by")), node["id"]
    rel_ids = set()
    for rel in graph["relationships"]:
        assert rel["id"] not in rel_ids, rel["id"]
        rel_ids.add(rel["id"])
        assert rel["from"] in ids and rel["to"] in ids, f"dangling relationship {rel['id']}"
        assert rel["type"] in RELATIONSHIP_TYPES, rel["id"]
        assert rel["observation_ids"] and all(x in obs for x in rel["observation_ids"]), f"{rel['id']} has no observation"
        assert rel["effective_dates_status"] in DATE_STATUSES, rel["id"]
        if rel["effective_dates_status"] == "unknown":
            assert rel["effective_from"] is None and rel["effective_to"] is None, f"{rel['id']} carries a date it calls unknown"
        else:
            assert rel["effective_from"] or rel["effective_to"], f"{rel['id']} claims dated status without a date"
        if rel["effective_dates_status"] == "inferred":
            assert rel.get("effective_dates_note"), rel["id"]
        if rel["effective_from"] and rel["effective_to"]:
            assert rel["effective_from"] <= rel["effective_to"], rel["id"]
        assert rel["current_status"]["state"] in CURRENT_STATES and _dated(rel["current_status"]["as_of"]), rel["id"]
        if rel["type"] == "consolidated_into":
            assert rel["scope_as_stated"], f"{rel['id']} consolidation without the source's scope"
        assert rel["review_status"] in REVIEW_STATUSES, rel["id"]
        assert rel["drafted_by"]["actor"] and _dated(rel["drafted_by"]["on"]), rel["id"]
        assert _reviewer_ok(rel["reviewed_by"]), rel["id"]
        if rel["review_status"] == "retracted":
            assert rel["retraction"] and rel["retraction"]["reason"] and rel["retraction"]["superseded_by"], rel["id"]
        else:
            assert rel["retraction"] is None, rel["id"]
    all_ids = rel_ids | {i["id"] for i in graph["interpretations"]}
    for rel in graph["relationships"]:
        if rel["retraction"]:
            assert all(x in all_ids for x in rel["retraction"]["superseded_by"]), rel["id"]
    for it in graph["interpretations"]:
        assert it["statement"] and it["confidence"] in {"high", "medium", "low"}, it["id"]
        assert it["observation_ids"] and all(x in obs for x in it["observation_ids"]), it["id"]
        assert all(s in ids for s in it["subject_ids"]) and it.get("what_would_resolve"), it["id"]


def test_attribution_examples_are_classified_and_evidenced() -> None:
    examples = _load("memory/attribution_examples.json")
    ids = [ex["id"] for ex in examples]
    assert len(ids) == len(set(ids)), "duplicate example id"
    for ex in examples:
        assert ex.get("identifier"), ex["id"]
        assert ex["evidence_class"] in EVIDENCE_CLASSES, ex["id"]
        assert "contracting_office" in ex and "funding_organization" in ex, ex["id"]
        assert isinstance(ex.get("program_offices"), list), ex["id"]
        assert _has_evidence(ex.get("evidence")), f"{ex['id']} lacks dated evidence with a source URL"
        if ex["evidence_class"] == "directly_documented":
            assert ex["program_offices"], f"{ex['id']} documented but names no office"
            assert any(e.get("passage") for e in ex["evidence"]), f"{ex['id']} has no quoted passage"
        if ex["evidence_class"] in ("inferred", "ambiguous"):
            assert ex.get("counterevidence") is not None, f"{ex['id']} must state counterevidence or 'none'"
        if ex["evidence_class"] == "unresolved":
            assert ex.get("why_unresolved"), ex["id"]
        review = ex["review"]
        assert review["status"] in REVIEW_STATUSES and review["drafted_by"]["actor"], ex["id"]
        assert _reviewer_ok(review["reviewed_by"]), ex["id"]


def test_documents_manifest_rows_are_hashed_and_dated() -> None:
    path = RESEARCH / "sources" / "documents_manifest.jsonl"
    if not path.exists():
        pytest.skip("documents_manifest.jsonl not written yet")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        assert row.get("method") in FETCH_METHODS, row
        if row.get("method") == "backfill":
            # A backfill row records saved bytes whose retrieval was never recorded: the bytes
            # and the stamp are known, the url and the retrieval date are unknown and stay null
            # rather than being invented. first_committed is the commit the bytes first appeared
            # in, and stays empty for bytes that ship only in the data release.
            assert row.get("path") and row.get("sha256"), row
            assert row.get("status") == 200, row
            assert _dated(row.get("backfilled_at")), row
            assert row.get("first_committed") == "" or _dated(row.get("first_committed")), row
            continue
        assert row.get("url") and _dated(row.get("retrieved_at")), row
        if row.get("status") == 200:
            assert SHA256_RE.match(row.get("sha256") or ""), row
        else:
            assert row.get("error") or row.get("status"), row


def test_every_committed_document_has_a_manifest_row() -> None:
    raw = Path(__file__).resolve().parents[1] / "data" / "raw"
    manifest = RESEARCH / "sources" / "documents_manifest.jsonl"
    if not raw.is_dir() or not manifest.exists():
        pytest.skip("data/raw or documents_manifest.jsonl not present")
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_path = {r.get("path") for r in rows if r.get("path")}
    by_sha = {r.get("sha256") for r in rows if r.get("sha256")}
    orphan = []
    for p in sorted(raw.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(raw.parent.parent))
        if rel in by_path:
            continue
        import hashlib
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if sha in by_sha:
            continue
        orphan.append(rel)
    assert not orphan, f"{len(orphan)} committed file(s) with no manifest row: {orphan[:5]}"


def test_every_cited_evidence_url_has_a_fetched_manifest_row() -> None:
    path = RESEARCH / "sources" / "documents_manifest.jsonl"
    if not path.exists():
        pytest.skip("documents_manifest.jsonl not written yet")
    fetched = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("method") == "backfill":
            continue
        if row.get("status") == 200 and row.get("sha256") and row.get("content_status") != "rejected_stub":
            fetched.add(row["url"].replace("%5B", "[").replace("%5D", "]"))
    cited = set()
    for name in ("memory/attribution_examples.json", "memory/organization_seed.json", "memory/contact_observations.json"):
        p = RESEARCH / name
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data["observations"] if isinstance(data, dict) else data
        for item in items:
            if "source_url" in item:
                cited.add(item["source_url"])
            for e in item.get("evidence", []):
                cited.add(e["source_url"])
            for key in ("actual_event", "post_cutoff_check"):
                if key in item:
                    cited.add(item[key]["source_url"])
    missing = sorted(u for u in cited if u.replace("%5B", "[").replace("%5D", "]") not in fetched)
    assert not missing, f"cited evidence without a fetched manifest row: {missing[:5]}"


def test_code_families_compile_and_carry_examples() -> None:
    data = _load("memory/org_code_families.json")
    seen = set()
    for family in data["families"]:
        assert family["family"] not in seen, f"duplicate family {family['family']}"
        seen.add(family["family"])
        re.compile(family["pattern"])
        for key in ("org_type", "era", "parent_hint", "where_seen", "normalize"):
            assert family.get(key), f"{family['family']} lacks {key}"
        for example in family.get("examples", []):
            assert re.search(family["pattern"], example), f"{family['family']}: example {example!r} does not match its own pattern"


ROLE_TYPES = {"requirement_owner", "program_manager", "executive", "contracting_poc", "technical_support_office", "industry_intake_channel", "office_channel"}
CONFIDENCES = {"high", "medium", "low"}


def test_contact_observations_trace_to_a_document_location() -> None:
    rows = _load("memory/contact_observations.json")
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate observation id"
    for r in rows:
        assert r["kind"] in {"person", "channel"} and (r.get("name") or r.get("channel_as_written")), r["id"]
        assert r["role_as_written"] and r["role_type"] in ROLE_TYPES, r["id"]
        assert r["source_url"] and r["source_revision"] and _dated(r["observed_at"]), r["id"]
        assert r["locators"] and r["passage"], f"{r['id']} lacks a locator or passage"
        assert r["source_statement_confidence"] in CONFIDENCES and r["independent_sources"] == 1, r["id"]
        if r["role_type"] == "contracting_poc":
            assert r["member_of_office"] is False, f"{r['id']}: a contracting POC is not a program-office member"
            assert all(l.get("pid") and l.get("row_number") and l.get("column") for l in r["locators"]), r["id"]


def test_contact_recommendations_rest_on_observations_with_two_confidences() -> None:
    obs = {r["id"] for r in _load("memory/contact_observations.json")}
    for rec in _load("memory/contact_recommendations.json"):
        assert rec["office_id"] and rec["route_type"] and rec["recommendation"], rec["id"]
        assert rec["contact_observation_ids"] and all(x in obs for x in rec["contact_observation_ids"]), rec["id"]
        assert rec["source_confidence"] in CONFIDENCES, rec["id"]
        assert rec["currency_confidence"] in CONFIDENCES | {"unknown"} and rec["currency_basis"], rec["id"]
        for key in ("competing_candidates", "contradictions", "caveats"):
            assert isinstance(rec[key], list), rec["id"]
        assert rec["review_status"] in REVIEW_STATUSES and _reviewer_ok(rec["reviewed_by"]), rec["id"]


def test_review_log_records_checker_and_outcome() -> None:
    log = _load("memory/review_log.json")
    obs = {r["id"] for r in _load("memory/contact_observations.json")}
    checked = set()
    for e in log:
        assert e["target"] and e["check"] and e["outcome"] in {"confirmed", "not_found", "unchecked", "corrected", "retracted"}, e["id"]
        assert e["checked_by"]["actor"] and _dated(e["checked_by"]["on"]), e["id"]
        assert _reviewer_ok(e["reviewed_by"]), e["id"]
        if e["target_kind"] == "contact_observation" and e["target"] in obs:
            checked.add(e["target"])
    assert checked == obs, "every contact observation must have a check entry"


ROUTE_EVIDENCE_CLASSES = {"directly_documented", "observed", "unverified"}


def _fetched_urls() -> set[str]:
    path = RESEARCH / "sources" / "documents_manifest.jsonl"
    urls = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("method") != "backfill" and row.get("status") == 200 and row.get("sha256") and row.get("content_status") != "rejected_stub":
            urls.add(row["url"])
    return urls


def test_onboarding_routes_carry_their_evidence_and_class() -> None:
    rows = _load("memory/onboarding_routes.json")
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate route id"
    fetched = _fetched_urls()
    for r in rows:
        assert r.get("route") and r.get("publisher"), r["id"]
        assert r["evidence_class"] in ROUTE_EVIDENCE_CLASSES, r["id"]
        assert r.get("who_manages_it", {}).get("role"), f"{r['id']} names no managing role"
        assert r.get("evidence"), f"{r['id']} carries no evidence"
        for e in r["evidence"]:
            # A document points at its saved copy; an observation or a heard claim names the conference and the notes' date.
            assert e.get("source_url") or (e.get("conference") and _dated(e.get("notes_date"))), (r["id"], e)
            if e.get("kind") == "document":
                assert e.get("passage") and _dated(e.get("observed_at")), (r["id"], e.get("source_url"))
        if r["evidence_class"] == "directly_documented":
            documents = [e for e in r["evidence"] if e.get("kind") == "document"]
            assert documents, f"{r['id']} is documented but quotes no document"
            for e in documents:
                assert e["source_url"] in fetched, f"{r['id']}: no fetched manifest row for {e['source_url']}"
        else:
            assert r.get("next_verification_step"), f"{r['id']} is {r['evidence_class']} but names no next verification step"
        assert r["review_status"] in REVIEW_STATUSES and _reviewer_ok(r["reviewed_by"]), r["id"]


def test_code_classifier_uses_the_alias_table_before_the_uic_family() -> None:
    import sys
    sys.path.insert(0, str(RESEARCH / "tools"))
    from lrae_package import classify_code  # noqa: E402

    expect = {"PEOC4I": ("peo:c4i", ""), "NAVWAR": ("command:navwar", ""), "N00039 - NAVWAR": ("contracting:n00039", "contracting_office_uic"),
              "PMW-160": ("pmw:160", "peo_program_office"), "LSUBP00035": ("", "niwc_atlantic_division"), "PMS-485": ("pms:485", "navsea_program_office")}
    for token, (office, family) in expect.items():
        hit = classify_code(token)
        assert hit["office_id"] == office, (token, hit)
        if family:
            assert hit["family"] == family, (token, hit)
        assert not (hit["family"] == "contracting_office_uic" and not hit["office_id"].startswith("contracting:")), (token, hit)
    data = _load("memory/org_code_families.json")
    uic = next(f for f in data["families"] if f["family"] == "contracting_office_uic")
    for token in uic["non_examples"]:
        assert classify_code(token)["family"] != "contracting_office_uic", token

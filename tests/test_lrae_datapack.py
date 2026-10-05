"""Checks for the reproducible LRAE data package under datapack/.

The package is regenerated from saved bytes by research/tools/lrae_package.py. These tests read
the committed package; the regeneration check runs only when the raw spreadsheet is on disk.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKS = sorted(p for p in (ROOT / "datapack").glob("lrae_*") if p.is_dir()) if (ROOT / "datapack").exists() else []
DECISIONS = {"included", "excluded", "unresolved"}
JOIN_TYPES = {"office", "existing_contract", "notice", "contact"}
METHODS = {"explicit", "inferred"}


def _csv(pack: Path, name: str) -> list[dict]:
    path = pack / name
    if not path.exists():
        pytest.skip(f"{name} not built yet")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _source(pack: Path) -> dict:
    path = pack / "SOURCE.json"
    if not path.exists():
        pytest.skip("SOURCE.json not built yet")
    return json.loads(path.read_text(encoding="utf-8"))


pytestmark = pytest.mark.skipif(not PACKS, reason="no datapack built yet")
packs = pytest.mark.parametrize("pack", PACKS, ids=[p.name for p in PACKS])


@packs
def test_source_names_the_bytes_and_the_outputs(pack):
    source = _source(pack)
    for key in ("source_url", "release_date", "retrieved_at", "sha256", "size", "sheet", "header_row", "refetch", "regenerate", "outputs"):
        assert key in source, key
    assert len(source["sha256"]) == 64
    present = [name for name in source["outputs"] if (pack / name).exists()]
    if not present:
        pytest.skip("CSV tables are regenerated locally, not committed; run lrae_package.py build")
    for name in present:
        assert hashlib.sha256((pack / name).read_bytes()).hexdigest() == source["outputs"][name], f"{name} differs from SOURCE.json"


@packs
def test_every_raw_row_has_one_decision_and_counts_reconcile(pack):
    raw, classified = _csv(pack, "rows_raw.csv"), _csv(pack, "rows_classified.csv")
    assert len(raw) == len(classified)
    assert [r["row_number"] for r in raw] == [c["row_number"] for c in classified]
    decisions = Counter(c["include_decision"] for c in classified)
    assert set(decisions) <= DECISIONS
    assert sum(decisions.values()) == len(raw)
    assert all(c["reason"] for c in classified)
    if _source(pack).get("scope", "peo_c4i") == "peo_c4i":
        assert all(c["office_id"] for c in classified if c["include_decision"] == "included")
    assert all(c["sheet"] and c["record_key"] for c in classified)
    text = (pack / "reconciliation.md").read_text(encoding="utf-8")
    assert f"| raw | {len(raw)} |" in text
    for decision in DECISIONS:
        assert f"| {decision} | {decisions.get(decision, 0)} |" in text


@packs
def test_every_row_is_its_own_record(pack):
    """A row is a source record until a reviewer resolves its identity.

    The June 2024 release has no PID column; rows 406 and 408-411 share the title
    "Order to Contract #N0003922D4001" under PMA/PMW-101 and describe different work.
    Keying on title and office marked four of them duplicates and dropped them from
    the layers. Keys are now the row, nothing is marked duplicate, and the
    reconciliation lists the shared titles for the reviewer instead.
    """
    classified = _csv(pack, "rows_classified.csv")
    keys = [c["record_key"] for c in classified]
    assert len(keys) == len(set(keys)), "two rows share a record key"
    assert not [c for c in classified if c["include_decision"] == "duplicate"]
    raw = _csv(pack, "rows_raw.csv")
    for c, r in zip(classified, raw):
        assert c["record_key"] == (r["pid"] or f"row:{r['release']}:{r['row_number']}"), c
    text = (pack / "reconciliation.md").read_text(encoding="utf-8")
    assert "## Rows sharing a title and an office" in text
    if pack.name == "lrae_navwar_2024-06":
        assert "Order to Contract #N0003922D4001" in text
        included = {c["row_number"] for c in classified if c["include_decision"] == "included"}
        assert {"406", "408", "409", "410", "411"} <= included


@packs
def test_joins_are_labelled_and_every_included_row_has_an_office_join(pack):
    classified, joins = _csv(pack, "rows_classified.csv"), _csv(pack, "joins.csv")
    included = {c["row_number"] for c in classified if c["include_decision"] == "included"}
    resolved = {c["row_number"] for c in classified if c["include_decision"] == "included" and c["office_id"]}
    assert {j["join_type"] for j in joins} <= JOIN_TYPES
    assert {j["method"] for j in joins} <= METHODS
    office_rows = {j["row_number"] for j in joins if j["join_type"] == "office" and j["target_id"]}
    # Every included row states what the office column resolved to, or that it resolved to nothing.
    assert included <= {j["row_number"] for j in joins if j["join_type"] == "office"}
    assert resolved == office_rows
    if _source(pack).get("scope", "peo_c4i") == "peo_c4i":
        assert included <= office_rows, "a PEO C4I row is included only when its office resolves"
    else:
        unresolved = [j for j in joins if j["join_type"] == "office" and not j["target_id"]]
        assert all(j["note"] == "office string names no organization the memory knows" for j in unresolved)
    assert {j["row_number"] for j in joins} <= included, "joins exist only for included rows"
    assert all(j["record_key"] for j in joins)
    for j in joins:
        assert j["key_used"] and j["note"], j
        if j["target_id"]:
            assert j["evidence_ref"], j
        if j["method"] == "inferred":
            assert j["target_id"].startswith(("attribution:", "sam:")), j
            # A notice join is inferred only when its key cannot single the row out: a shared vehicle, or an
            # incumbent contract several rows of the release cite.
            assert "vehicle" in j["note"] or "does not tell the rows apart" in j["note"] or j["target_id"].startswith("attribution:"), j


@packs
def test_layers_reference_needs_and_evidence(pack):
    needs = _csv(pack, "layers/needs.csv")
    evidence = {e["id"] for e in _csv(pack, "layers/evidence.csv")}
    need_ids = {n["id"] for n in needs}
    assert len(need_ids) == len(needs)
    assert all(n["evidence_id"] in evidence and n["valid_from"] for n in needs)
    if _source(pack).get("scope", "peo_c4i") == "peo_c4i":
        assert all(n["office_id"] for n in needs)
    for name in ("need_requirements.csv", "funding_observations.csv", "procurement_refs.csv"):
        for row in _csv(pack, f"layers/{name}"):
            assert row["need_id"] in need_ids, (name, row)
    assert all(f["observation_type"] == "procurement_estimate" for f in _csv(pack, "layers/funding_observations.csv"))


@packs
def test_regeneration_is_byte_identical(pack, tmp_path):
    source = _source(pack)
    if not (ROOT / source["raw_path"]).exists():
        pytest.skip("raw spreadsheet not on this machine")
    pytest.importorskip("openpyxl")
    env = {"LRAE_PACK_DIR": str(tmp_path)}
    result = subprocess.run([sys.executable, str(ROOT / "research" / "tools" / "lrae_package.py"), "build"],
                            cwd=ROOT, env={**__import__("os").environ, **env}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for name, digest in source["outputs"].items():
        assert hashlib.sha256((tmp_path / pack.name / name).read_bytes()).hexdigest() == digest, name


ALL_PACKS = sorted(p for p in (ROOT / "datapack").glob("*_*") if p.is_dir() and (p / "rows_raw.csv").exists()) if (ROOT / "datapack").exists() else []


@pytest.mark.parametrize("pack", ALL_PACKS, ids=[p.name for p in ALL_PACKS])
def test_procurement_refs_are_written_exactly_when_a_row_names_a_contract(pack):
    """The procurement_refs layer holds the contract numbers the forecast rows carry; a pack whose source has no such
    column (NASA's forecast workbook names an estimated value and a vehicle type, never an incumbent contract) writes
    none, and that absence is the fact, not a gap."""
    sys.path.insert(0, str(ROOT / "research" / "tools"))
    from lrae_package import contract_tokens
    with (pack / "rows_raw.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    named = sum(1 for r in rows if contract_tokens(r.get("existing_contract_number") or ""))
    assert (pack / "layers" / "procurement_refs.csv").exists() == (named > 0), (pack.name, named)

"""The generated part of the organization memory: what org_memory_lrae.py reads out of the
activity-wide LRAE sheets. The rebuild check runs only when the raw spreadsheets are on disk."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from org_memory_lrae import (GENERATOR, INFERRED_CENTER, INFERRED_DEPARTMENT, INFERRED_HQ, INFERRED_SITE, ROOT as TRIAL, SEED,  # noqa: E402
                              STATEMENTS, collisions, page_text, saved, squash)


def _seed() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))


def test_no_alias_normalizes_to_two_nodes() -> None:
    assert collisions(_seed()) == []


def test_generated_records_are_draft_and_cite_the_sheet() -> None:
    seed = _seed()
    nodes = [n for n in seed["nodes"] if n.get("generator") == GENERATOR]
    obs = {o["id"]: o for o in seed["observations"] if o.get("generator") == GENERATOR}
    assert nodes and obs
    for node in nodes:
        assert node["review_status"] == "draft" and node["reviewed_by"] is None
        assert node["observation_ids"] and all(obs[i]["source_url"] for i in node["observation_ids"]), node["id"]
        assert node["type"] in {"command", "contracting_office", "technical_center", "field_activity", "program_office",
                                "program_executive_office", "department", "direct_reporting_program_manager",
                                "acquisition_portfolio"}, node["id"]  # the PAEs, read off saved releases (docs/08 lists the type)
    for rel in (r for r in seed["relationships"] if r.get("generator") == GENERATOR):
        # A parent comes from the office column; the office that buys for an office comes from the contracting column
        # of the rows that office owns, which is stated, never inferred.
        assert rel["type"] in {"child_of", "contracts_for"} and rel["evidence_class"] in {"directly_documented", "inferred"}
        assert rel["type"] == "child_of" or rel["evidence_class"] == "directly_documented", rel["id"]
        kind = "parentage" if rel["type"] == "child_of" else "contracting"
        assert all(obs[i]["statement_type"] == kind for i in rel["observation_ids"]), rel["id"]
        if rel["evidence_class"] == "inferred":
            assert "inference" in rel["current_status"]["note"], rel["id"]
            # What each inference rests on: the site's two columns, the HQ list's heading, the command's own sheet
            # naming the center or activity, or the department's combined report carrying the command's sheet.
            rests_on = {INFERRED_SITE: lambda i: "Contracting Office UIC" in obs[i]["passage"], INFERRED_HQ: lambda i: i.startswith("obs:dpm:"),
                        INFERRED_CENTER: lambda i: obs[i]["passage"].startswith("column '"),
                        INFERRED_DEPARTMENT: lambda i: "combined LRAE report" in obs[i]["passage"]}[rel["current_status"]["note"]]
            assert all(rests_on(i) for i in rel["observation_ids"]), rel["id"]


def test_an_office_drafted_from_fpds_does_not_take_its_commands_name() -> None:
    """FPDS prints a headquarters office under the command's own name; the node leads with the office ID, so a
    document naming the command does not resolve to the contracting office."""
    offices = [n for n in _seed()["nodes"] if any(i.startswith("obs:fpds:") for i in n["observation_ids"])]
    assert offices
    for node in offices:
        assert node["type"] == "contracting_office" and node["name"].startswith(node["codes"]["uic"] + " - "), node["name"]


def test_deputy_program_manager_list_gives_program_offices_their_peo() -> None:
    rels = {r["id"]: r for r in _seed()["relationships"]}
    for rel_id, obs_prefix in (("rel:lrae:pms-312:peo-carriers", "obs:dpm:"), ("rel:lrae:pms-326b:sea-21", "obs:dpm:"),
                               ("rel:lrae:sea-21a:sea-21", "obs:dpm:"), ("rel:lrae:sea-21:command-navsea", "obs:lrae:")):
        assert rel_id in rels, rel_id
        assert rels[rel_id]["evidence_class"] == "directly_documented"
        assert any(o.startswith(obs_prefix) for o in rels[rel_id]["observation_ids"]), rels[rel_id]["observation_ids"]


def test_page_statements_quote_the_saved_pages() -> None:
    manifest = [json.loads(line) for line in (TRIAL / "research" / "sources" / "documents_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    statements = json.loads(STATEMENTS.read_text(encoding="utf-8"))["statements"]
    assert statements
    checked = 0
    for st in statements:
        source = saved(manifest, lambda m, u=st["source_url"]: m.get("url") == u)
        assert source is not None, st["id"]
        if not (TRIAL / source["path"]).exists():
            continue
        text = page_text(TRIAL / source["path"])
        for passage in st["passages"]:
            assert squash(passage) in text, (st["id"], passage)
        checked += 1
    if not checked:
        pytest.skip("saved pages not on this machine")
    seed = _seed()
    nodes = {n["id"] for n in seed["nodes"]}
    obs = {o["id"] for o in seed["observations"]}
    for st in statements:
        assert st["node"]["id"] in nodes and f"obs:page:{st['node']['id'].replace(':', '-')}" in obs, st["id"]


def test_rebuild_leaves_the_seed_unchanged() -> None:
    manifest = ROOT / "research" / "sources" / "documents_manifest.jsonl"
    if not any((ROOT / json.loads(line)["path"]).exists() for line in manifest.read_text(encoding="utf-8").splitlines()
               if line.strip() and json.loads(line).get("path") and json.loads(line).get("mime", "").endswith("sheet")):
        pytest.skip("raw spreadsheets not on this machine")
    result = subprocess.run([sys.executable, str(ROOT / "research" / "tools" / "org_memory_lrae.py"), "--check"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-500:]


def _live_parents(seed: dict) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = {}
    for rel in seed["relationships"]:
        if rel["type"] in {"child_of", "part_of"} and rel["review_status"] != "retracted" and rel["current_status"]["state"] == "last_confirmed":
            parents.setdefault(rel["from"], set()).add(rel["to"])
    return parents


def test_every_generated_program_office_with_a_parent_reaches_a_command() -> None:
    """The resolver walks the parent path; a generated program office whose chain stops short of a
    command is a dangling edge the generator wrote. Offices no source places are not failed here."""
    seed = _seed()
    nodes = {n["id"]: n for n in seed["nodes"]}
    parents = _live_parents(seed)

    def reaches_root(node_id: str, seen: frozenset = frozenset()) -> bool:
        if nodes[node_id]["type"] in {"command", "agency", "acquisition_portfolio"}:
            return True
        return any(reaches_root(p, seen | {node_id}) for p in parents.get(node_id, ()) if p not in seen)

    offices = [n["id"] for n in seed["nodes"] if n["type"] in {"program_office", "program_executive_office"}]
    generated = [o for o in offices if nodes[o].get("generator") == GENERATOR]
    assert generated
    assert [o for o in generated if o in parents and not reaches_root(o)] == []
    placed = [o for o in generated if o in parents]
    assert len(placed) > len(generated) // 2, (len(placed), len(generated))

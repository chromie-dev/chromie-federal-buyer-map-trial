"""Coverage matrix and source status: every command x family cell names a registered
source that has collected something or states why none does; the status names, per registered source, what was
last collected and when; a fetched host the registry does not know is listed, not hidden."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from agency import KEY  # noqa: E402
from coverage import MATRIX, REASONS, STATUS, problems, registry, status  # noqa: E402

pytestmark = pytest.mark.skipif(not (MATRIX.exists() and STATUS.exists()), reason="coverage not built")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Pages fetched once by hand or by a probe from hosts no registry row owns yet; a new host here fails until it is
# registered or added on purpose. The ledger is shared by every agency layer, so the set holds for each profile;
# a host another layer's registry owns is that layer's document and is not listed (coverage.other_agency_hosts).
# NIWC sites: not_started in the grid; eventscribe: a WEST 2026 agenda page fetched during
# the conference sweep that no event cites, so no source claims it
KNOWN_UNREGISTERED = {"www.niwcatlantic.navy.mil", "www.niwcpacific.navy.mil", "westconference2026.eventscribe.net",
                      # NAVAIR's small business office page, taken by hand through the hosted browser on 2026-09-27; the NAVAIR
                      # organization cell reads not_started until a navair.navy.mil row is registered (Stage 2 work)
                      "www.navair.navy.mil"}
# Onboarding-route pages (SBA, GSA, NASA, NSA, NOAA, acquisition.gov, the APEX site) fetched for
# research/memory/onboarding_routes.json on 2026-09-24; they document how a company enters federal selling, not a
# source of the Navy layer, so they are listed here rather than registered (DECISIONS.md, 2026-09-24)
KNOWN_UNREGISTERED |= {"www.sba.gov", "dsbs.sba.gov", "www.gsa.gov", "www.nasa.gov", "www.nsa.gov", "research.noaa.gov",
                       "www.acquisition.gov", "www.apexaccelerators.us"}
# NAVAIR's small business page, taken through context.dev on 2026-09-27 with the other small business offices; no registry
# row owns NAVAIR's site. api.context.dev: the answers of a hand-run DHS people search on 2026-09-29 and the USAJobs code
# list it relayed; the people search is not a source of any layer.
KNOWN_UNREGISTERED |= {"www.navair.navy.mil", "api.context.dev"}
# DARPA's pages and the Senate committee sites were fetched on 2026-09-24 for the Navy-versus-DARPA comparison
# (research/docs/19_navy_versus_darpa.md) and belong to the DARPA layer's registry (research/agencies/darpa/sources).
# The sources each layer must have collected from before its status is read as live.
LIVE = {"navy": {"sam_gov_site_api", "fpds_atom_feed", "navwar_lrae_annex25", "don_budget_justification_books", "oversight_gov_reports"},
        "darpa": {"sam_gov_site_api", "fpds_atom_feed", "darpa_site", "darpa_staff_listing", "dod_comptroller_budget_materials",
                  "oversight_gov_reports", "federal_register", "sbir_sttr_topics"},
        "army": {"sam_gov_site_api", "amc_acquisition_forecast", "usace_acquisition_forecast", "army_asaalt_chart", "sbir_sttr_topics"}}


def test_every_cell_is_covered_or_explained():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert problems(matrix, status(), registry()) == []
    assert len(matrix["cells"]) == len(matrix["organizations"]) * len(matrix["families"])
    assert all(c.get("reason") in REASONS for c in matrix["cells"] if not c.get("sources"))


def test_status_covers_every_registered_source_with_dated_last_seen():
    saved = json.loads(STATUS.read_text(encoding="utf-8"))
    assert set(saved["sources"]) == set(registry())
    for key, s in saved["sources"].items():
        assert set(s) == {"events", "newest_event", "documents", "last_seen", "last_hash", "hosts"}, key
        assert (s["documents"] == 0) == (s["last_seen"] == "") == (s["last_hash"] == ""), key
        if s["documents"]:
            assert STAMP.match(s["last_seen"]) and len(s["last_hash"]) == 64, key
        if s["events"]:
            assert STAMP.match(s["newest_event"]), key
    live = {k for k, s in saved["sources"].items() if s["events"] or s["documents"]}
    assert LIVE.get(KEY, {"sam_gov_site_api"}) <= live  # a new layer has at least collected its notices


def test_unregistered_hosts_are_the_known_ones():
    saved = json.loads(STATUS.read_text(encoding="utf-8"))
    assert set(saved["unregistered_hosts"]) <= KNOWN_UNREGISTERED, saved["unregistered_hosts"]


def test_saved_status_rebuilds_from_the_files_alone():
    assert json.loads(STATUS.read_text(encoding="utf-8")) == json.loads(json.dumps(status()))


GENERATED_CELL_KEYS = {"org", "node", "family", "events", "documents", "documents_scope", "last_collected"}


def test_status_lists_the_instruments_and_the_open_gaps():
    saved = json.loads(STATUS.read_text(encoding="utf-8"))
    reg = registry()
    assert set(saved["instruments"]) == set(reg)
    for key, row in saved["instruments"].items():
        assert set(row) == {"status", "declared_collector", "collector", "last_collected", "cadence_kind"}, key
        assert row["status"] == reg[key]["instrument"]["status"] and row["declared_collector"] == reg[key]["instrument"]["collector_kind"], key
        if row["collector"] == "collecting":
            assert saved["sources"][key]["documents"] or saved["sources"][key]["events"], key
    gaps = saved["open_gaps"]
    assert set(gaps["order"]) == set(gaps["rows"]) == {k for k, s in saved["sources"].items() if s["documents"] == 0}
    first = [k for k in ("sam_opportunities_api", "seaport_nxg", "navy_posture_testimony") if k in gaps["rows"]]
    assert gaps["order"][:len(first)] == first, "the three Navy gaps lead the list"
    for key, g in gaps["rows"].items():
        assert g["blocker"] and g["close_by"], (key, "every open gap names its blocker and how to close it")
        assert g["collector"] in ("registered_empty", "blocked")


def test_a_generated_matrix_rebuilds_from_the_files_and_tags_every_covered_cell():
    from agency import P
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    if not P.get("coverage_org_nodes"):
        pytest.skip("this layer's matrix is written by hand")
    assert matrix["organizations"] == list(P["coverage_org_nodes"]) == P["coverage_orgs"]
    for c in matrix["cells"]:
        assert GENERATED_CELL_KEYS <= set(c), c
        assert bool(c.get("sources")) == (c.get("status") in ("Live", "Historical", "Adjacent")), c
        if c.get("status") == "Live":
            assert c["events"] > 0 and STAMP.match(c["last_collected"]), c
        if c.get("status") == "Adjacent":
            assert c["events"] == 0 and c["documents"] > 0, c
        if c["node"] is None:
            assert c["reason"] == "not_started", c
    out = subprocess.run([sys.executable, str(ROOT / "research" / "tools" / "coverage.py"), "matrix", "--check"], capture_output=True, text=True, cwd=ROOT,
                         env={**os.environ, "AGENCY": KEY})
    assert out.returncode == 0, out.stdout[-600:] + out.stderr[-400:]

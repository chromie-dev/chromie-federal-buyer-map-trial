"""Scheduled entry for people sources: {"people_sources": {"source": "<name>"}}."""

from __future__ import annotations

from typing import Any, Callable

from orchestration.gov.people.bios import run_bio_monitor
from orchestration.gov.people.dvids import run_dvids_monitor
from orchestration.gov.people.fpds_staff import run_sam_staff_monitor
from orchestration.gov.people.moves import run_move_monitor
from orchestration.gov.people.nominations import run_nomination_monitor
from orchestration.gov.people.releases import default_invoke, run_release_monitor
from orchestration.gov.people.vendors import run_vendor_monitor
from orchestration.supabase_client import get_supabase_client


# Each runner takes the Supabase client and the Lambda soft deadline (epoch seconds or None).
RUNNERS: dict[str, Callable[[Any, float | None], dict[str, Any]]] = {
    "fpds_staff": lambda sb, _deadline: run_sam_staff_monitor(sb),
    "bio_pages": lambda sb, deadline: run_bio_monitor(sb, deadline_epoch=deadline),
    "war_gov_releases": lambda sb, deadline: run_release_monitor(sb, invoke=default_invoke(), deadline_epoch=deadline),
    "congress_nominations": lambda sb, _deadline: run_nomination_monitor(sb),
    "dvids_leadership": lambda sb, _deadline: run_dvids_monitor(sb, invoke=default_invoke()),
    "moves": lambda sb, _deadline: run_move_monitor(sb),
    "vendors": lambda sb, _deadline: run_vendor_monitor(sb),
}


def run(event: dict[str, Any], *, soft_deadline_epoch: float | None = None) -> dict[str, Any]:
    params = event.get("people_sources") if isinstance(event.get("people_sources"), dict) else {}
    source = str(params.get("source") or "").strip()
    runner = RUNNERS.get(source)
    if runner is None:
        raise ValueError(f"unknown people source {source!r}; expected one of {sorted(RUNNERS)}")
    return {"source": source, **runner(get_supabase_client(), soft_deadline_epoch)}

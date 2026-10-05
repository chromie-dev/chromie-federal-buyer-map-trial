"""A post reads confirmed current only when an official source stated it within six months; anything else seen within
two years is recently observed, and older is history. The stakeholder holders, people and program managers carry that
standing in the data, so a two-year-old observation is never the office's holder today."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from people import post_standings, standing  # noqa: E402

AS_OF = "2026-09-29"


def claim(source: str, day: str) -> dict:
    return {"source": source, "observed_at": day, "source_url": f"https://x/{source}"}


def test_only_a_recent_official_statement_confirms_a_post() -> None:
    assert standing([claim("sam_gov_site_api", "2026-06-01")], AS_OF)["status"] == "confirmed_current"
    assert standing([claim("news_articles", "2026-09-01")], AS_OF)["status"] == "recently_observed"  # a story reports, it does not state
    assert standing([claim("linkedin_profile", AS_OF)], AS_OF)["status"] == "recently_observed"  # self-stated
    assert standing([claim("contact_observations", "2025-12-01")], AS_OF)["status"] == "recently_observed"  # official, past six months
    assert standing([claim("contact_observations", "2024-01-01")], AS_OF)["status"] == "history"
    assert standing([claim("contact_observations", "2026-10-15")], AS_OF)["status"] == "history"  # after the record date
    assert standing([], AS_OF)["status"] == "history"
    got = standing([claim("news_articles", "2026-09-20"), claim("contact_observations", "2026-05-01")], AS_OF)
    assert got == {"status": "confirmed_current", "source": "contact_observations", "observed_at": "2026-05-01",
                   "source_url": "https://x/contact_observations"}


def test_a_claim_confirms_only_the_post_it_names() -> None:
    """Regression: a person's standing was one value over every claim, so a recent SAM.gov contact record at one office
    made a leadership post that only the news reported, at another office, read confirmed current."""
    sam = {**claim("sam_gov_site_api", "2026-09-01"), "office": "pmw:120", "role_type": "contract_specialist"}
    news = {**claim("news_articles", "2026-09-10"), "office": "peo:c4i", "role_type": "acquisition_leader"}
    same_office = {**claim("news_articles", "2026-09-10"), "office": "pmw:120", "role_type": "program_manager"}
    got = {(p["office"], p["role"]): p["status"] for p in post_standings([sam, news, same_office], AS_OF)}
    assert got == {("pmw:120", "contract_specialist"): "confirmed_current", ("peo:c4i", "acquisition_leader"): "recently_observed",
                   ("pmw:120", "program_manager"): "recently_observed"}, got


def test_stakeholder_holders_carry_their_standing() -> None:
    out = subprocess.run([sys.executable, str(ROOT / "research" / "tools" / "stakeholders.py"), "--selfcheck"], capture_output=True,
                         text=True, cwd=ROOT, env={**os.environ, "AGENCY": "navy"})
    assert out.returncode == 0 and "selfcheck ok" in out.stdout, out.stdout[-800:] + out.stderr[-800:]

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from orchestration.gov.people.fpds_staff import (
    atom_actions,
    office_organization_id,
    roster,
    run_sam_staff_monitor,
    sam_actions,
    staff_identity,
    write_roster,
)
from orchestration.gov.people import worker
from tests.test_gov_people_writer import _Db


FIXTURES = Path(__file__).parent / "fixtures" / "people"
ATOM = (FIXTURES / "fpds_atom_navsea.xml").read_text()
SAM = json.loads((FIXTURES / "sam_awards_navsea.json").read_text())
NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
NAVY = {"id": "agency-navy", "subtier_code": "1700"}
NAVY_ORG = {"id": "org-navy", "agency_id": "agency-dod", "existing_agency_id": "agency-navy"}


def _db() -> _Db:
    return _Db(agencies=[NAVY], gov_organizations=[NAVY_ORG], gov_contacts=[], gov_contact_identifiers=[],
               gov_contact_positions=[], gov_tracked_offices=[], gov_api_cache=[])


class ParserTests(unittest.TestCase):
    def test_atom_page_yields_office_staff_and_touch_dates(self) -> None:
        actions = atom_actions(ATOM)
        self.assertEqual(len(actions), 3)
        first = actions[0]
        self.assertEqual((first["office_code"], first["agency_code"]), ("N00024", "1700"))
        self.assertTrue(first["office_name"] and first["piid"] and first["url"].startswith("https://www.fpds.gov/"))
        self.assertEqual({role for role, _, _ in first["staff"]}, {"created", "approved"})
        self.assertTrue(all(len(day) == 10 for _, _, day in first["staff"]))

    def test_sam_page_reads_the_same_fields(self) -> None:
        actions = sam_actions(SAM)
        self.assertEqual(len(actions), 3)
        self.assertEqual({a["office_code"] for a in actions}, {"N00024"})
        self.assertEqual(actions[0]["signed"], "2026-09-25")
        self.assertIn("ezsearch", actions[0]["url"])

    def test_identity_kinds(self) -> None:
        self.assertEqual(staff_identity("ALEX.R.MORGAN.N00178@NAVY")[:2], ("alex.r.morgan@navy.mil", "dod"))
        self.assertEqual(staff_identity("MORGAN.QUIXLE7001"), ("morgan.quixle7001", "username", "Morgan Quixle"))
        self.assertIsNone(staff_identity("DOD_CLOSEOUT"))
        self.assertIsNone(staff_identity("PADDS.W91CRB@KO.ARMY.MIL"))
        self.assertIsNone(staff_identity("PADDS.W519TC@CS1.ARMY.MIL"))
        self.assertEqual(staff_identity("CASEY.QUIXLE@US.AF.MIL")[:2], ("casey.quixle@us.af.mil", "email"))
        # NASA's account prefix is not part of the person's name; the account stays the key.
        self.assertEqual(staff_identity("WPA.DANA.R.QUILL@NASA.GOV"), ("wpa.dana.r.quill@nasa.gov", "email", "Dana R Quill"))
        self.assertEqual(staff_identity("ROBIN.TALLIS.12@US.AF.MIL"), ("robin.tallis.12@us.af.mil", "email", "Robin Tallis"))
        self.assertEqual(staff_identity("ROBIN.TALLIS.N00173@WEB1700.NRL")[2], "Robin Tallis")
        self.assertEqual(staff_identity("ROBIN.TALLIS.CTR1@DARPA.MIL")[2], "Robin Tallis")
        self.assertEqual(staff_identity("ROBIN.TALLIS-OKAFOR@US.AF.MIL")[2], "Robin Tallis-Okafor")
        self.assertIsNone(staff_identity("ACQUISITION.HELP.DESK@GSA.GOV"))

    def test_roster_counts_each_action_once_across_feeds(self) -> None:
        actions = atom_actions(ATOM)
        entries = roster(actions + actions)
        morgan = entries[("alex.r.morgan@navy.mil", "N00024", "contracting_officer")]
        self.assertEqual(morgan["actions"], 3)
        self.assertEqual(entries[("alex.r.morgan@navy.mil", "N00024", "contract_specialist")]["users"],
                         {"ALEX.R.MORGAN.N00024@NAVY.MIL", "ALEX.R.MORGAN.N00178@NAVY"})
        self.assertIn(("dana.k.price@navy.mil", "N00024", "contract_specialist"), entries)


class WriteRosterTests(unittest.TestCase):
    def test_backfill_is_idempotent_and_observed_only(self) -> None:
        db = _db()
        first = write_roster(db, atom_actions(ATOM) + sam_actions(SAM), now=NOW)
        office = next(row for row in db.tables["gov_organizations"] if row.get("source") == "fpds_office")
        self.assertEqual((office["id"], office["parent_organization_id"], office["agency_id"]),
                         (office_organization_id("N00024"), "org-navy", "agency-dod"))
        # The ATOM and SAM samples share one person.
        self.assertEqual(first["staff"], 4)
        self.assertEqual(len(db.tables["gov_contacts"]), 4)
        positions = db.tables["gov_contact_positions"]
        self.assertTrue(all(row["valid_from"] is None and row["date_basis"] == "observed" for row in positions))
        again = write_roster(db, atom_actions(ATOM) + sam_actions(SAM), now=NOW)
        self.assertEqual((again["contacts_created"], again["positions_inserted"], again["positions_updated"]), (0, 0, 0))
        self.assertEqual(len(db.tables["gov_contact_positions"]), len(positions))


class SamMonitorTests(unittest.TestCase):
    def test_pages_until_done_then_pauses_at_the_call_cap_and_resumes(self) -> None:
        db = _db()
        calls = []

        def fetch(params: dict[str, str]) -> dict:
            calls.append(params)
            return SAM if params["contractingOfficeCode"] == "N00024" else {"awardSummary": [], "totalRecords": "0"}

        env = {"SAM_CONTRACT_AWARDS_API_KEY": "test", "CHROMIE_PEOPLE_SAM_DAILY_CALLS": "1"}
        with patch.dict(os.environ, env):
            first = run_sam_staff_monitor(db, fetch=fetch, now=NOW, offices=["N00024", "N00178"])
            self.assertEqual((first["status"], first["calls"], first["staff"]), ("paused", 1, 3))
            self.assertEqual(calls[0]["dateSigned"], "[07/04/2026,07/04/2026]")
            paused = run_sam_staff_monitor(db, fetch=fetch, now=NOW, offices=["N00024", "N00178"])
            self.assertEqual((paused["status"], len(calls)), ("paused", 1))
            tomorrow = run_sam_staff_monitor(db, fetch=fetch, now=datetime(2026, 10, 3, tzinfo=timezone.utc),
                                             offices=["N00024", "N00178"])
        self.assertEqual([c["contractingOfficeCode"] for c in calls], ["N00024", "N00178"])
        self.assertEqual((tomorrow["status"], tomorrow["through"]), ("paused", "2026-07-04"))

    def test_missing_key_skips_only_this_connector(self) -> None:
        with patch.dict(os.environ, {"SAM_CONTRACT_AWARDS_API_KEY": ""}):
            self.assertEqual(run_sam_staff_monitor(_db(), now=NOW)["status"], "skipped")


class WorkerTests(unittest.TestCase):
    def test_dispatches_by_source_and_rejects_unknown(self) -> None:
        with patch.object(worker, "get_supabase_client", return_value=_db()), \
                patch.dict(worker.RUNNERS, {"fpds_staff": lambda sb, _deadline: {"status": "complete"}}):
            self.assertEqual(worker.run({"people_sources": {"source": "fpds_staff"}}),
                             {"source": "fpds_staff", "status": "complete"})
            with self.assertRaises(ValueError):
                worker.run({"people_sources": {"source": "nope"}})


if __name__ == "__main__":
    unittest.main()

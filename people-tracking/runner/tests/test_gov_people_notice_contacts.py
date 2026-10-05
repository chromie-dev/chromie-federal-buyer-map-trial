from __future__ import annotations

import json
import unittest
from pathlib import Path

from orchestration.gov.people.fpds_staff import office_organization_id
from orchestration.gov.people.notice_contacts import notice_office, write_notice_positions
from orchestration.gov.people.writer import link_identities
from tests.test_gov_people_writer import NOW, _Db


FIXTURE = Path(__file__).parent / "fixtures" / "people" / "sam_notices_navsea.json"
ITEMS = json.loads(FIXTURE.read_text())["opportunitiesData"]
NAVSEA = office_organization_id("N00024")


def _db() -> _Db:
    # The tools index has already upserted the notice e-mails; FPDS knows Alex Morgan without an e-mail.
    return _Db(
        gov_contacts=[
            {"id": "c-alex-fpds", "identity_key": "fpds:alex.r.morgan@navy.mil", "name": "Alex R Morgan"},
            {"id": "c-alex", "identity_key": "email:alex.r.morgan.civ@us.navy.mil", "email": "alex.r.morgan.civ@us.navy.mil",
             "name": "Alex R. Morgan"},
            {"id": "c-dana", "identity_key": "email:dana.k.price.civ@us.navy.mil", "email": "dana.k.price.civ@us.navy.mil",
             "name": "Dana K. Price"},
            {"id": "c-inbox", "identity_key": "email:example.contracts@navy.mil", "email": "example.contracts@navy.mil",
             "name": "NAVAIR Contracts"},
        ],
        gov_contact_identifiers=[
            {"id": "i-1", "contact_id": "c-alex-fpds", "kind": "fpds_person", "value": "alex.r.morgan@navy.mil"},
        ],
        gov_organizations=[{"id": NAVSEA, "name": "NAVSEA HQ", "source": "fpds_office"}],
        gov_contact_positions=[],
        agencies=[],
    )


class NoticeOfficeTests(unittest.TestCase):
    def test_posting_office_comes_from_the_path_code(self) -> None:
        self.assertEqual(notice_office(ITEMS[2]), {
            "office_code": "N00019", "office_name": "NAVAL AIR SYSTEMS COMMAND", "agency_code": "1700", "city": "", "state": ""})
        self.assertEqual((notice_office(ITEMS[0])["city"], notice_office(ITEMS[0])["state"]), ("WASHINGTON NAVY YARD", "DC"))
        self.assertIsNone(notice_office(ITEMS[3]))
        dotted = {"fullParentPathCode": "097.97ZS.H92240", "fullParentPathName": "DEPT OF DEFENSE.U.S. SPECIAL OPERATIONS COMMAND (USSOCOM).HQ USSOCOM"}
        self.assertEqual(notice_office(dotted)["office_name"], "")


class NoticePositionTests(unittest.TestCase):
    def test_named_people_are_observed_at_the_posting_office(self) -> None:
        db = _db()
        result = write_notice_positions(db, ITEMS, now=NOW)
        positions = {(row["contact_id"], row["organization_id"], row["role_type"]): row
                     for row in db.tables["gov_contact_positions"]}
        alex = positions[("c-alex", NAVSEA, "contracting_officer")]
        self.assertEqual((alex["first_observed_at"][:10], alex["last_observed_at"][:10]), ("2026-05-14", "2026-09-03"))
        self.assertEqual((alex["date_basis"], alex["valid_from"], alex["source"]), ("observed", None, "sam_notice_contacts"))
        self.assertIn(("c-dana", NAVSEA, "other"), positions)
        jordan = [row for row in db.tables["gov_contacts"] if row["identity_key"] == "email:jordan.t.lee.civ@us.navy.mil"]
        self.assertIn((jordan[0]["id"], office_organization_id("N00019"), "contract_specialist"), positions)
        # Shared inboxes, name-only contacts and notices without an office write nothing.
        self.assertEqual(len(positions), 3)
        self.assertEqual(result["notice_people"], 3)
        # A notice adds offices it is first to name; FPDS keeps naming the ones it knows.
        offices = {row["id"]: row["name"] for row in db.tables["gov_organizations"]}
        self.assertEqual((offices[NAVSEA], offices[office_organization_id("N00019")]), ("NAVSEA HQ", "NAVAL AIR SYSTEMS COMMAND"))
        # The office address a notice carries is where the office sits; an office no notice placed stays unplaced.
        places = {row["id"]: (row.get("address_city"), row.get("address_state")) for row in db.tables["gov_organizations"]}
        self.assertEqual(places[NAVSEA], ("WASHINGTON NAVY YARD", "DC"))
        self.assertEqual(places[office_organization_id("N00019")], (None, None))

        again = write_notice_positions(db, ITEMS, now=NOW)
        self.assertEqual((again["positions_inserted"], again["positions_updated"], again["contacts_created"]), (0, 0, 0))

    def test_the_email_contact_keeps_the_notice_and_absorbs_the_fpds_twin(self) -> None:
        db = _db()
        write_notice_positions(db, ITEMS, now=NOW)
        alex = [row for row in db.tables["gov_contact_identifiers"] if row["contact_id"] == "c-alex"]
        self.assertEqual({row["kind"] for row in alex}, {"email"})
        link_identities(db, now=NOW)
        self.assertEqual(db.merges, [("c-alex", "c-alex-fpds")])


if __name__ == "__main__":
    unittest.main()

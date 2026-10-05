from __future__ import annotations

import unittest
from datetime import datetime, timezone

from orchestration.gov.opportunity_notifications import build_person_moved_email
from orchestration.gov.people.fpds_staff import office_organization_id, write_roster
from orchestration.gov.people.moves import account_moves, alert_moves, alert_text, detect_fpds_moves
from tests.test_gov_people_writer import _Db


NOW = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
NAVY = {"id": "agency-navy", "subtier_code": "1700"}
NAVY_ORG = {"id": "org-navy", "agency_id": "agency-dod", "existing_agency_id": "agency-navy"}


def _action(office: str, user: str, signed: str, piid: str, role: str = "approved") -> dict:
    return {"agency_code": "1700", "agency_name": "DEPT OF THE NAVY", "piid": piid, "mod": "0", "signed": signed,
            "office_code": office, "office_name": f"OFFICE {office}", "staff": [(role, user, signed)],
            "url": f"https://www.fpds.gov/ezsearch/search.do?q={piid}"}


def _career(person: str = "PAT.Q.LEE") -> list[dict]:
    """Three actions on an N00024 account in 2024, then three on an N00178 account in 2025."""
    return ([_action("N00024", f"{person}.CIV.N00024@US.NAVY.MIL", f"2024-0{month}-10", f"A{person}{month}") for month in (2, 3, 4)]
            + [_action("N00178", f"{person}.N00178@NAVY.MIL", f"2025-0{month}-10", f"B{person}{month}") for month in (5, 6, 7)])


def _db() -> _Db:
    return _Db(agencies=[NAVY], gov_organizations=[NAVY_ORG], gov_contacts=[], gov_contact_identifiers=[],
               gov_contact_positions=[], gov_contact_role_history=[], gov_person_relationships=[],
               gov_tracked_offices=[], profiles=[], gov_user_notifications=[])


def _account(value: str, first: str, last: str, count: int) -> dict:
    return {"value": value, "activity_first_on": first, "activity_last_on": last, "activity_count": count}


class AccountMoveRuleTests(unittest.TestCase):
    def test_accounts_used_in_turn_with_three_records_each_are_a_move(self) -> None:
        moves = account_moves([
            _account("pat.q.lee.civ.n00024@us.navy.mil", "2024-02-10", "2024-04-10", 2),
            # A second id for the same office joins its history.
            _account("pat.q.lee.n00024@navy.mil", "2024-01-05", "2024-01-05", 1),
            _account("pat.q.lee.n00178@navy.mil", "2025-05-10", "2025-07-10", 3),
        ])
        self.assertEqual(moves, [(("2024-01-05", "2024-04-10", "N00024", 3), ("2025-05-10", "2025-07-10", "N00178", 3))])

    def test_overlap_or_thin_evidence_is_not_a_move(self) -> None:
        overlap = [_account("pat.q.lee.n00024@navy.mil", "2024-01-01", "2025-06-01", 9),
                   _account("pat.q.lee.n00178@navy.mil", "2025-05-10", "2025-07-10", 9)]
        thin = [_account("pat.q.lee.n00024@navy.mil", "2024-01-01", "2024-02-01", 2),
                _account("pat.q.lee.n00178@navy.mil", "2025-05-10", "2025-07-10", 9)]
        self.assertEqual(account_moves(overlap), [])
        self.assertEqual(account_moves(thin), [])


class DetectorTests(unittest.TestCase):
    def test_roster_records_activity_once_and_later_days_add(self) -> None:
        db = _db()
        actions = _career()
        write_roster(db, actions, now=NOW)
        write_roster(db, actions, now=NOW)
        counts = {row["value"]: row["activity_count"] for row in db.tables["gov_contact_identifiers"] if row["kind"] == "fpds_user"}
        self.assertEqual(counts, {"pat.q.lee.civ.n00024@us.navy.mil": 3, "pat.q.lee.n00178@navy.mil": 3})
        write_roster(db, [_action("N00178", "PAT.Q.LEE.N00178@NAVY.MIL", "2025-08-01", "BPATLATER")], now=NOW)
        later = next(row for row in db.tables["gov_contact_identifiers"] if row["value"] == "pat.q.lee.n00178@navy.mil")
        self.assertEqual((later["activity_count"], later["activity_last_on"]), (4, "2025-08-01"))

    def test_move_is_written_once_and_the_old_post_closes_for_good(self) -> None:
        db = _db()
        write_roster(db, _career(), now=NOW)
        first = detect_fpds_moves(db, now=NOW)
        again = detect_fpds_moves(db, now=NOW)
        self.assertEqual((first["fpds_moves"], first["positions_closed"]), (1, 1))
        self.assertEqual(again["positions_closed"], 0)
        [move] = db.tables["gov_contact_role_history"]
        self.assertEqual((move["event_type"], move["effective_date"], move["date_basis"], move["status"]),
                         ("transfer", "2025-05-10", "observed", "current"))
        self.assertEqual((move["organization_id"], move["previous_organization_id"]),
                         (office_organization_id("N00178"), office_organization_id("N00024")))
        self.assertEqual((move["title"], move["previous_title"]),
                         ("Contracting Officer, OFFICE N00178", "Contracting Officer, OFFICE N00024"))
        self.assertEqual([item["records"] for item in move["evidence"]], [3, 3])
        old = next(row for row in db.tables["gov_contact_positions"] if row["source_ref"] == "N00024")
        self.assertEqual(old["valid_to"], "2024-04-10")
        # Rereading the same FPDS days does not reopen the post the move closed.
        reread = write_roster(db, _career(), now=NOW)
        self.assertEqual(reread["positions_reactivated"], 0)
        self.assertEqual(old["valid_to"], "2024-04-10")

    def test_a_recoded_account_serving_the_same_office_is_not_a_move(self) -> None:
        db = _db()
        recoded = [{**action, "office_code": "N00024", "office_name": "OFFICE N00024"} for action in _career()]
        write_roster(db, recoded, now=NOW)
        self.assertEqual(detect_fpds_moves(db, now=NOW)["fpds_moves"], 0)
        self.assertEqual(db.tables["gov_contact_role_history"], [])

    def test_work_at_the_old_office_after_the_move_retracts_it_and_reopens_the_post(self) -> None:
        db = _db()
        write_roster(db, _career(), now=NOW)
        detect_fpds_moves(db, now=NOW)
        written = write_roster(db, [_action("N00024", "PAT.Q.LEE.CIV.N00024@US.NAVY.MIL", "2025-06-01", "ALATE")], now=NOW)
        result = detect_fpds_moves(db, now=NOW)
        self.assertEqual((result["fpds_moves"], result["moves_retracted"], written["positions_reactivated"]), (0, 1, 1))
        self.assertEqual(db.tables["gov_contact_role_history"][0]["status"], "retracted")

    def test_a_move_skipped_for_a_missing_field_is_not_retracted(self) -> None:
        db = _db()
        write_roster(db, _career(), now=NOW)
        detect_fpds_moves(db, now=NOW)
        for row in db.tables["gov_contact_positions"]:
            row["source_url"] = None  # the records still show the move; only the link to cite is gone this run
        result = detect_fpds_moves(db, now=NOW)
        self.assertEqual(result["moves_retracted"], 0)
        self.assertEqual(db.tables["gov_contact_role_history"][0]["status"], "current")


class AlertTests(unittest.TestCase):
    def _moved(self) -> _Db:
        db = _db()
        write_roster(db, _career(), now=NOW)
        detect_fpds_moves(db, now=NOW)
        move = db.tables["gov_contact_role_history"][0]
        move["created_at"] = "2026-10-02T08:00:00+00:00"
        db.tables["profiles"] = [
            {"id": "user-a1", "email": "a1@example.com", "gov_profile_id": "company-a"},
            {"id": "user-a2", "email": "a2@example.com", "gov_profile_id": "company-a"},
            {"id": "user-b1", "email": "b1@example.com", "gov_profile_id": "company-b"},
            {"id": "user-c1", "email": "c1@example.com", "gov_profile_id": "company-c"},
        ]
        db.tables["gov_person_relationships"] = [
            {"gov_profile_id": "company-a", "contact_id": move["gov_contact_id"], "created_at": "2026-09-01T00:00:00+00:00"},
            # Tracking that began after the move was recorded is not news.
            {"gov_profile_id": "company-c", "contact_id": move["gov_contact_id"], "created_at": "2026-10-02T09:00:00+00:00"},
        ]
        db.tables["gov_tracked_offices"] = [
            {"gov_profile_id": "company-b", "organization_id": office_organization_id("N00024"), "created_at": "2026-01-01T00:00:00+00:00"},
        ]
        return db

    def test_trackers_of_the_person_or_either_office_are_told_once(self) -> None:
        db = self._moved()
        first = alert_moves(db, now=NOW)
        alert_moves(db, now=NOW)
        rows = db.tables["gov_user_notifications"]
        self.assertEqual(first["notifications"], 3)
        self.assertEqual(sorted(row["user_id"] for row in rows), ["user-a1", "user-a2", "user-b1"])
        self.assertTrue(all(row["event_kind"] == "person_moved" for row in rows))
        self.assertEqual(rows[0]["title"], "Pat Q Lee moved to Contracting Officer, OFFICE N00178")
        self.assertIn("Observed from 2025-05-10", rows[0]["body"])
        # FPDS records imply the move; no source states it, and the alert says so.
        self.assertTrue(rows[0]["body"].startswith("Inferred from two records"))
        self.assertEqual(rows[0]["payload"]["certainty"], "inferred")

    def test_announcement_wording_and_email(self) -> None:
        title, body = alert_text({"name": "Jordan Vale", "event_type": "announcement", "title": "Commander, Example Command",
                                  "effective_date": "2026-09-17", "date_basis": "stated", "previous_title": None,
                                  "source_provider": "war_gov_releases"})
        self.assertEqual((title, body), ("Jordan Vale named to Commander, Example Command",
                                         "Confirmed: an official source states it. Announced 2026-09-17. Source: war.gov release."))
        email = build_person_moved_email({"title": title, "body": body,
                                          "payload": {"contact_id": "contact-1", "source_url": "https://www.war.gov/x",
                                                      "certainty": "confirmed"}},
                                         app_url="https://app.example.com")
        self.assertTrue(email["text"].startswith("Tracked person: confirmed move"))
        self.assertIn("Tracked person: confirmed move", email["html"])
        self.assertIn("https://app.example.com/contractors/directory?person=contact%3Acontact-1", email["text"])
        self.assertIn("Source record: https://www.war.gov/x", email["text"])


if __name__ == "__main__":
    unittest.main()

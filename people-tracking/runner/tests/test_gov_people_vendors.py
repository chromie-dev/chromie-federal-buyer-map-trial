from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from unittest import mock

from orchestration.gov.people.moves import alert_text
from orchestration.gov.people.vendors import (
    alumni_sql,
    company_matches,
    employer_side,
    name_key,
    office_tokens,
    place_agrees,
    place_states,
    run_coresignal_check,
    run_orange_slice_sweep,
    surname_forms,
)
from tests.test_gov_people_writer import _Db


NOW = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
NAVSEA = {"id": "org-navsea", "name": "NAVSEA HQ", "source_ref": "N00024", "aliases": ["N00024"],
          "address_city": "WASHINGTON NAVY YARD", "address_state": "DC"}


def _post(contact: str, *, first: str, last: str, post_id: str | None = None) -> dict:
    return {"id": post_id or f"p-{contact}", "contact_id": contact, "organization_id": "org-navsea",
            "role_type": "contract_specialist", "raw_title": None, "valid_from": None, "valid_to": None,
            "date_basis": "observed", "first_observed_at": f"{first}T00:00:00+00:00",
            "last_observed_at": f"{last}T00:00:00+00:00", "source": "fpds_staff",
            "source_url": "https://www.fpds.gov/ezsearch/search.do?q=N0002423C0001"}


def _db(*contacts: tuple[str, str], positions: list[dict], tracked: bool = True) -> _Db:
    return _Db(
        gov_contacts=[{"id": cid, "name": name, "agency": "DEPT OF THE NAVY", "identity_key": f"fpds:{cid}@navy.mil"}
                      for cid, name in contacts],
        gov_contact_positions=positions,
        gov_organizations=[dict(NAVSEA)],
        gov_person_relationships=[{"id": f"r-{cid}", "gov_profile_id": "company-a", "contact_id": cid}
                                  for cid, _ in contacts] if tracked else [],
        gov_tracked_offices=[{"id": "t-1", "gov_profile_id": "company-a", "organization_id": "org-navsea"}],
        gov_contact_identifiers=[],
        gov_contact_role_history=[],
        gov_api_cache=[],
    )


def _record(profile_id: int, first: str, last: str, experience: list[dict], location: str = "Washington, District of Columbia, United States") -> dict:
    return {"id": profile_id, "first_name": first, "last_name": last, "full_name": f"{first} {last}",
            "profile_url": f"https://www.linkedin.com/in/example-{profile_id}", "location": location,
            "checked_at": "2026-09-30 10:00:00", "experience": experience}


NAVSEA_JOB = {"company_name": "Naval Sea Systems Command (NAVSEA)", "title": "Contract Specialist",
              "date_from": "March 2022", "date_to": "February 2025", "is_current": 0,
              "location": "Washington DC-Baltimore Area"}
SHIPWORKS_JOB = {"company_name": "Example Shipworks LLC", "title": "Vice President", "date_from": "March 2025",
                 "date_to": None, "is_current": 1, "location": "Arlington, Virginia, United States"}


class _Coresignal:
    """Search by name returns ids; collect returns the stored record; every call is counted."""

    def __init__(self, records: dict[str, list[dict]]) -> None:
        self.records = records
        self.searches = 0
        self.collects = 0

    def __call__(self, path: str, body: dict | None = None):
        if path == "search/es_dsl":
            self.searches += 1
            name = body["query"]["bool"]["must"][0]["match"]["full_name"]["query"]
            return [record["id"] for record in self.records.get(name, [])], None
        self.collects += 1
        wanted = path.split("/")[-1]
        record = next(item for items in self.records.values() for item in items if str(item["id"]) == wanted)
        return record, 1960 - 10 * self.collects


class MatchingTests(unittest.TestCase):
    def test_names_drop_credentials_suffixes_and_middle_names(self) -> None:
        self.assertEqual(name_key("Pat Q. Lee"), ("pat", "lee"))
        self.assertEqual(name_key("Lee, Pat Q"), ("pat", "lee"))
        self.assertEqual(name_key("Pat Lee, PMP"), ("pat", "lee"))
        self.assertEqual(name_key("Pat W.", "Lee, PMP"), ("pat", "lee"))
        self.assertIsNone(name_key("Lee"))

    def test_an_office_is_named_by_its_distinctive_words(self) -> None:
        self.assertEqual(office_tokens(NAVSEA), {"navsea"})
        self.assertTrue(company_matches("Naval Sea Systems Command (NAVSEA)", NAVSEA))
        self.assertFalse(company_matches("US Navy", NAVSEA))

    def test_place_reads_states_metros_and_codes(self) -> None:
        self.assertEqual(place_states("Washington DC-Baltimore Area"), {"DC", "MD", "VA"})
        self.assertEqual(place_states("Washington, District of Columbia, United States"), {"DC"})
        self.assertEqual(place_states("San Antonio, Texas, United States"), {"TX"})
        self.assertEqual(place_states("Norfolk, VA"), {"VA"})
        self.assertTrue(place_agrees(NAVSEA, "Washington DC-Baltimore Area"))
        self.assertFalse(place_agrees(NAVSEA, "San Antonio, Texas, United States"))
        # An office no notice has placed joins nobody.
        self.assertFalse(place_agrees({**NAVSEA, "address_state": None}, "Washington DC-Baltimore Area"))

    def test_surnames_are_asked_in_every_form_the_name_join_takes(self) -> None:
        self.assertEqual(surname_forms("Pat Q Van Dyke"), {"dyke", "van dyke", "van-dyke"})
        self.assertEqual(surname_forms("Lee, Pat Q"), {"lee"})
        self.assertEqual(surname_forms("Pat"), set())

    def test_employers_are_firms_public_bodies_or_unplaced(self) -> None:
        self.assertEqual(employer_side("Example Shipworks LLC"), "industry")
        self.assertEqual(employer_side("Example Federal Services"), "industry")
        self.assertEqual(employer_side("Program Executive Office, Ships"), "government")
        self.assertEqual(employer_side("United States Department of Defense"), "government")
        self.assertIsNone(employer_side("Example Dynamics Electric Boat"))
        self.assertIsNone(employer_side("Maritime Industrial Base Program"))


class CoresignalTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {"CORESIGNAL_MONTHLY_COLLECTS": "10"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_joined_profile_that_left_for_industry_is_a_departure(self) -> None:
        db = _db(("c-pat", "Pat Q Lee"), ("c-jordan", "Jordan Vale"), ("c-casey", "Casey Moore"), positions=[
            _post("c-pat", first="2023-01-10", last="2025-01-20"),
            _post("c-jordan", first="2023-01-10", last="2025-01-20"),
            _post("c-casey", first="2023-01-10", last="2025-01-20"),
        ])
        vendor = _Coresignal({
            "pat lee": [_record(101, "Pat", "Lee", [SHIPWORKS_JOB, NAVSEA_JOB])],
            # Same name pattern, a NAVSEA job, but the profile sits in Texas: place disagrees, no join.
            "jordan vale": [_record(102, "Jordan", "Vale", [SHIPWORKS_JOB, {**NAVSEA_JOB, "location": "San Antonio, Texas"}],
                                    location="San Antonio, Texas, United States")],
            # Left NAVSEA for another Navy office: a move inside government, not a departure.
            "casey moore": [_record(103, "Casey", "Moore", [
                {"company_name": "Program Executive Office, Ships", "title": "Program Analyst", "date_from": "March 2025",
                 "date_to": None, "is_current": 1, "location": "Washington DC-Baltimore Area"}, NAVSEA_JOB])],
        })
        result = run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual((result["searched"], result["collected"], result["matched"], result["departures"]), (3, 3, 1, 1))
        self.assertEqual((result["credits_spent"], result["credits_left"]), (30, 1930))
        [move] = db.tables["gov_contact_role_history"]
        self.assertEqual((move["gov_contact_id"], move["event_type"], move["status"], move["date_basis"]),
                         ("c-pat", "departure", "current", "stated"))
        self.assertEqual((move["title"], move["effective_date"], move["source_ref"]),
                         ("Contract Specialist, NAVSEA HQ", "2025-02-01", "coresignal:101"))
        self.assertEqual(move["evidence"][0]["now"], "Vice President, Example Shipworks LLC")
        self.assertEqual(move["evidence"][1]["last_on"], "2025-01-20")
        self.assertNotIn("@", str(move["evidence"]))
        [identifier] = db.tables["gov_contact_identifiers"]
        self.assertEqual((identifier["contact_id"], identifier["kind"], identifier["value"]), ("c-pat", "vendor_profile", "coresignal:101"))
        closed = {row["contact_id"]: row["valid_to"] for row in db.tables["gov_contact_positions"]}
        self.assertEqual(closed, {"c-pat": "2025-01-20", "c-jordan": None, "c-casey": None})

        title, body = alert_text(move)
        self.assertEqual(title, "Pat Q Lee left Contract Specialist, NAVSEA HQ")
        self.assertEqual(body, "Reported by a licensed profile; no official source confirms it yet. "
                               "Effective 2025-02. Now: Vice President, Example Shipworks LLC. "
                               "Source: Coresignal (licensed people data).")

        # Within the month nobody is searched or collected again.
        again = run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual((again["searched"], again["collected"], vendor.collects), (0, 0, 3))
        self.assertEqual(len(db.tables["gov_contact_role_history"]), 1)

    def test_a_profile_our_records_contradict_goes_to_review(self) -> None:
        # FPDS saw the person at NAVSEA eight months after the month the profile says they left.
        db = _db(("c-pat", "Pat Q Lee"), positions=[_post("c-pat", first="2023-01-10", last="2025-10-20")])
        vendor = _Coresignal({"pat lee": [_record(101, "Pat", "Lee", [SHIPWORKS_JOB, NAVSEA_JOB])]})
        result = run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual((result["departures"], result["departures_review"], result["positions_closed"]), (1, 1, 0))
        self.assertEqual(db.tables["gov_contact_role_history"][0]["status"], "review")

    def test_an_employer_ending_in_a_period_ends_the_alert_once(self) -> None:
        move = {"name": "Pat Q Lee", "event_type": "departure", "title": "Contract Specialist, NAVSEA HQ",
                "date_basis": "stated", "effective_date": "2025-02-01", "source_provider": "orange_slice",
                "evidence": [{"date_precision": "month", "now": "Director, Example Shipworks, Inc."}]}
        self.assertEqual(alert_text(move)[1], "Reported by a licensed profile; no official source confirms it yet. "
                                              "Effective 2025-02. Now: Director, Example Shipworks, Inc. "
                                              "Source: Orange Slice (licensed people data).")

    def test_a_move_to_an_employer_the_name_cannot_place_waits_for_review(self) -> None:
        db = _db(("c-pat", "Pat Q Lee"), positions=[_post("c-pat", first="2023-01-10", last="2025-01-20")])
        program = {**SHIPWORKS_JOB, "company_name": "Maritime Industrial Base Program", "title": "Program Analyst"}
        vendor = _Coresignal({"pat lee": [_record(101, "Pat", "Lee", [program, NAVSEA_JOB])]})
        result = run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual((result["departures"], result["departures_review"], result["positions_closed"]), (1, 1, 0))
        self.assertIsNone(db.tables["gov_contact_role_history"][0]["evidence"][0]["outside_government"])

    def test_collects_stop_at_the_cap_and_common_names_are_not_collected(self) -> None:
        db = _db(("c-pat", "Pat Q Lee"), ("c-sam", "Sam Roe"), positions=[
            _post("c-pat", first="2023-01-10", last="2025-01-20"), _post("c-sam", first="2023-01-10", last="2025-01-20")])
        many = [_record(200 + n, "Sam", "Roe", [NAVSEA_JOB]) for n in range(4)]
        vendor = _Coresignal({"pat lee": [_record(101, "Pat", "Lee", [SHIPWORKS_JOB, NAVSEA_JOB])], "sam roe": many})
        with mock.patch.dict(os.environ, {"CORESIGNAL_MONTHLY_COLLECTS": "0"}):
            paused = run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual((paused["status"], vendor.collects), ("paused", 0))
        result = run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual((result["ambiguous"], vendor.collects), (1, 1))

    def test_a_check_is_remembered_only_once_written_and_an_outage_keeps_what_was_checked(self) -> None:
        db = _db(("c-pat", "Pat Q Lee"), ("c-sam", "Sam Roe"), positions=[
            _post("c-pat", first="2023-01-10", last="2025-01-20"), _post("c-sam", first="2023-01-10", last="2025-01-20")])
        vendor = _Coresignal({"pat lee": [_record(101, "Pat", "Lee", [SHIPWORKS_JOB, NAVSEA_JOB])]})
        with mock.patch("orchestration.gov.people.vendors.write_departures", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual(db.tables["gov_api_cache"], [])

        def outage(path: str, body: dict | None = None):
            if body and body["query"]["bool"]["must"][0]["match"]["full_name"]["query"] == "sam roe":
                raise OSError("connection reset")
            return vendor(path, body)

        stopped = run_coresignal_check(db, request=outage, now=NOW, sleep=lambda _s: None)
        self.assertEqual((stopped["status"], stopped["departures"], len(db.tables["gov_api_cache"])), ("stopped", 1, 1))
        resumed = run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual((resumed["searched"], vendor.collects), (1, 2))

    def test_a_missing_key_skips_only_this_vendor(self) -> None:
        with mock.patch.dict(os.environ, {"CORESIGNAL_API_KEY": ""}):
            self.assertEqual(run_coresignal_check(_db(positions=[]))["status"], "skipped")


class _OrangeSlice:
    def __init__(self, alumni: list[dict]) -> None:
        self.alumni = alumni
        self.statements: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.statements)

    def sql(self, statement: str) -> list[dict]:
        self.statements.append(statement)
        if "linkedin_company_slug" in statement:
            return [{"id": 17636, "company_name": "Naval Sea Systems Command (NAVSEA)"}] if "'navsea'" in statement else []
        return self.alumni


def _alumni(profile_id: int, first: str, last: str, **overrides) -> dict:
    return {"id": profile_id, "first_name": first, "last_name": last, "now_title": "Program Director",
            "now_org": "Example Shipworks LLC", "location_name": "Washington DC-Baltimore Area",
            "office_title": "Contract Specialist", "locality": "Washington DC-Baltimore Area",
            "start_date": "2022-03-01T00:00:00.000Z", "end_date": "2025-02-01T00:00:00.000Z",
            "public_profile_url": f"https://www.linkedin.com/in/example-{profile_id}", **overrides}


class OrangeSliceTests(unittest.TestCase):
    def test_office_alumni_join_only_the_one_person_they_can_be(self) -> None:
        db = _db(("c-pat", "Pat Q Lee"), ("c-dana-1", "Dana Fox"), ("c-dana-2", "Dana R Fox"), positions=[
            _post("c-pat", first="2023-01-10", last="2025-01-20"),
            _post("c-dana-1", first="2023-01-10", last="2025-01-20"),
            _post("c-dana-2", first="2023-05-10", last="2024-11-20"),
        ], tracked=False)
        client = _OrangeSlice([_alumni(301, "Pat", "Lee, PMP"), _alumni(302, "Dana", "Fox"),
                               _alumni(303, "Riley", "Stone")])
        result = run_orange_slice_sweep(db, client=client, now=NOW)
        self.assertEqual((result["swept"], result["alumni"], result["matched"], result["departures"]), (1, 3, 1, 1))
        [move] = db.tables["gov_contact_role_history"]
        self.assertEqual((move["gov_contact_id"], move["source_provider"], move["effective_date"]), ("c-pat", "orange_slice", "2025-02-01"))
        self.assertEqual(move["evidence"][0]["now"], "Program Director, Example Shipworks LLC")
        self.assertIn("pos.linkedin_company_id = 17636", client.statements[-1])
        self.assertIn("in ('fox', 'lee') limit 500", client.statements[-1])
        # The company lookup and the sweep are both remembered for the month.
        again = run_orange_slice_sweep(db, client=client, now=NOW)
        self.assertEqual((again["swept"], client.calls), (0, 2))

    def test_a_sweep_is_remembered_only_once_written(self) -> None:
        db = _db(("c-pat", "Pat Q Lee"), positions=[_post("c-pat", first="2023-01-10", last="2025-01-20")], tracked=False)
        client = _OrangeSlice([_alumni(301, "Pat", "Lee")])
        with mock.patch("orchestration.gov.people.vendors.write_departures", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                run_orange_slice_sweep(db, client=client, now=NOW)
        result = run_orange_slice_sweep(db, client=client, now=NOW)
        self.assertEqual((result["swept"], result["departures"]), (1, 1))

    def test_one_profile_is_one_join(self) -> None:
        db = _db(("c-pat", "Pat Q Lee"), positions=[_post("c-pat", first="2023-01-10", last="2025-01-20")], tracked=False)
        # Two NAVSEA rows on one profile (specialist, then contracting officer) join the person once.
        client = _OrangeSlice([_alumni(301, "Pat", "Lee"), _alumni(301, "Pat", "Lee", office_title="Contracting Officer",
                                                                    start_date="2024-01-01T00:00:00.000Z")])
        result = run_orange_slice_sweep(db, client=client, now=NOW)
        self.assertEqual((result["departures"], len(db.tables["gov_contact_identifiers"])), (1, 1))

    def test_a_profile_two_people_match_in_one_batch_joins_neither(self) -> None:
        db = _db(("c-pat", "Pat Q Lee"), ("c-pat-2", "Pat Lee"), positions=[
            _post("c-pat", first="2023-01-10", last="2025-01-20"),
            _post("c-pat-2", first="2023-01-10", last="2025-01-20", post_id="p-2")])
        vendor = _Coresignal({"pat lee": [_record(101, "Pat", "Lee", [SHIPWORKS_JOB, NAVSEA_JOB])]})
        result = run_coresignal_check(db, request=vendor, now=NOW, sleep=lambda _s: None)
        self.assertEqual((result["departures"], result["profile_conflicts"]), (0, 2))
        self.assertEqual(db.tables["gov_contact_identifiers"], [])

    def test_alumni_sql_is_bounded(self) -> None:
        statement = alumni_sql(17636, date(2023, 10, 1), ["lee", "o'neil"])
        self.assertIn("pos.end_date >= '2023-10-01'", statement)
        self.assertIn("in ('lee', 'o''neil')", statement)
        self.assertTrue(statement.endswith("limit 500"))
        self.assertNotIn("order by", statement.lower())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from orchestration.gov.people.bios import (
    BIO_INDEXES,
    bio_role,
    match_organization,
    parse_html_bio,
    parse_pdf_bio,
    run_bio_monitor,
    stated_start,
)
from tests.test_gov_people_writer import NOW, _Db


FIXTURES = Path(__file__).parent / "fixtures" / "people" / "bios"
FLAG, SES = BIO_INDEXES
BIO_URL = "https://www.navy.mil/Leadership/Flag-Officer-Biographies/BioDisplay/Article/"
PAGES = {
    FLAG.url.format(page=1): "flag_index_page1.html",
    FLAG.url.format(page=2): "flag_index_page2.html",
    f"{BIO_URL}1000001/rear-admiral-morgan-q-ellis/": "flag_bio_ellis.html",
    f"{BIO_URL}1000002/vice-admiral-jordan-avery/": "flag_bio_avery.html",
    f"{BIO_URL}1000003/rear-admiral-casey-retired/": "flag_bio_empty.html",
    SES.url: "ses_index.html",
    "https://www.secnav.navy.mil/donhr/About/Senior-Executives/Biographies/Carter, R.pdf": "ses_bio_carter.txt",
}


def _fetch(url: str) -> bytes:
    return (FIXTURES / PAGES[url]).read_bytes()


def _db() -> _Db:
    return _Db(
        agencies=[{"id": "agency-navy", "subtier_code": "1700"}],
        gov_organizations=[
            {"id": "org-navy", "name": "Department of the Navy", "agency_id": "agency-dod", "existing_agency_id": "agency-navy"},
            {"id": "org-navsea", "name": "Naval Sea Systems Command", "acronym": "NAVSEA", "aliases": ["NAVSEA"],
             "agency_id": "agency-navy", "org_type": "other"},
            {"id": "org-spacecom", "name": "Space Command", "agency_id": "agency-dod", "org_type": "other"},
        ],
        gov_contacts=[],
        gov_contact_identifiers=[],
        gov_contact_positions=[],
    )


class StatedStartTests(unittest.TestCase):
    def test_a_month_tied_to_the_current_post_is_stated(self) -> None:
        post = "Commander, Naval Sea Systems Command"
        self.assertEqual(stated_start("Ellis assumed duties as Commander, Naval Sea Systems Command in June 2024.", post)[0],
                         date(2024, 6, 1))
        self.assertEqual(stated_start("Ms. Carter has been in this role since March 2025.", "Executive Director")[0],
                         date(2025, 3, 1))

    def test_each_post_in_a_sentence_keeps_its_own_date(self) -> None:
        sentence = ("In June 2025, he assumed duties as Director, Navy International Programs, and subsequently "
                    "assumed the role of Community Lead in May 2026.")
        self.assertEqual(stated_start(sentence, "Director, Navy International Programs | Community Lead")[0],
                         date(2025, 6, 1))
        prior = ("In November 2025 he became the Program Executive Officer for Unmanned Aviation and recently "
                 "transitioned into his current role as Deputy Portfolio Acquisition Executive Aviation.")
        self.assertIsNone(stated_start(prior, "Deputy Portfolio Acquisition Executive Aviation"))

    def test_a_grade_appointment_does_not_date_the_post(self) -> None:
        sentence = ("Mr. Lee was appointed to the Senior Executive Service in August 2025 and serves as Division "
                    "Technical Director, Naval Surface Warfare Center Corona Division.")
        self.assertIsNone(stated_start(sentence, "Division Technical Director, Naval Surface Warfare Center Corona Division"))


class ParseTests(unittest.TestCase):
    def test_flag_officer_page(self) -> None:
        record = parse_html_bio(_fetch(f"{BIO_URL}1000001/rear-admiral-morgan-q-ellis/").decode(), "u")
        self.assertEqual((record["name"], record["post"]), ("Morgan Q. Ellis", "Commander, Naval Sea Systems Command"))
        self.assertEqual((record["page_date"], record["stated_from"]), (date(2025, 9, 16), date(2024, 6, 1)))
        avery = parse_html_bio(_fetch(f"{BIO_URL}1000002/vice-admiral-jordan-avery/").decode(), "u")
        self.assertEqual((avery["name"], avery["stated_from"], avery["page_date"]), ("Jordan K. Avery", None, date(2024, 9, 20)))
        self.assertIsNone(parse_html_bio(_fetch(f"{BIO_URL}1000003/rear-admiral-casey-retired/").decode(), "u"))

    def test_senior_executive_pdf_text(self) -> None:
        record = parse_pdf_bio((FIXTURES / "ses_bio_carter.txt").read_text(), "u")
        self.assertEqual((record["name"], record["post"]), ("Riley S. Carter", "Executive Director, Naval Sea Systems Command"))
        self.assertEqual((record["page_date"], record["stated_from"]), (date(2025, 7, 1), date(2025, 3, 1)))

    def test_a_credential_after_the_name_is_dropped(self) -> None:
        page = ('<h1 class="maintitle">{}</h1><h4 class="dateline">Vice Commander, Example Systems Command</h4>'
                "<p>He serves as Vice Commander.</p>")
        self.assertEqual(parse_html_bio(page.format("Sam J. Oduya-Lind, PE"), "u")["name"], "Sam J. Oduya-Lind")
        self.assertEqual(parse_html_bio(page.format("Dr. Ana Brill, Ph.D."), "u")["name"], "Ana Brill")
        self.assertEqual(parse_html_bio(page.format("Lee Moss, Jr."), "u")["name"], "Lee Moss, Jr.")

    def test_an_organization_counts_only_where_the_post_names_it(self) -> None:
        aliases = {"naval sea systems command": {"navsea"}, "space command": {"spacecom"},
                   "program executive office ships": {"peo-ships"}}
        self.assertEqual(match_organization("Executive Director, Naval Sea Systems Command", aliases), "navsea")
        self.assertEqual(match_organization("Program Executive Officer, Ships", aliases), "peo-ships")
        self.assertIsNone(match_organization("Commander, Navy Space Command", aliases))

    def test_a_program_executive_officer_is_an_acquisition_leader(self) -> None:
        self.assertEqual(bio_role("Program Executive Officer, Ships"), "acquisition_leader")
        self.assertEqual(bio_role("Deputy Program Executive Officer, Ships"), "acquisition_leader")
        self.assertEqual(bio_role("Commander, Sixth Fleet/ Commander, Task Force Six"), "other")


class MonitorTests(unittest.TestCase):
    def test_bios_become_dated_positions_and_rerun_adds_nothing(self) -> None:
        db = _db()
        with patch("orchestration.gov.people.bios._pdf_text", lambda content: content.decode()):
            result = run_bio_monitor(db, fetch=_fetch, now=NOW, sleep=lambda _s: None)
            flag, ses = result["indexes"]
            self.assertEqual((flag["listed"], flag["read"], flag["unparsed"], flag["stated"]), (3, 3, 1, 1))
            self.assertEqual((ses["read"], ses["stated"]), (1, 1))
            positions = {row["raw_title"]: row for row in db.tables["gov_contact_positions"]}
            ellis = positions["Commander, Naval Sea Systems Command"]
            self.assertEqual((ellis["organization_id"], ellis["role_type"]), ("org-navsea", "acquisition_leader"))
            self.assertEqual((ellis["date_basis"], ellis["valid_from"]), ("stated", "2024-06-01"))
            self.assertEqual(ellis["last_observed_at"][:10], "2025-09-16")
            avery = positions["Commander, Sixth Fleet/ Commander, Task Force Six"]
            self.assertEqual((avery["organization_id"], avery["date_basis"], avery["valid_from"]), ("org-navy", "observed", None))
            self.assertEqual(positions["Executive Director, Naval Sea Systems Command"]["organization_id"], "org-navsea")
            keys = {row["identity_key"] for row in db.tables["gov_contacts"]}
            self.assertIn("name:morgan q ellis|agency:department of the navy", keys)
            self.assertIn(("name_org", "morgan q ellis|department of the navy"),
                          {(row["kind"], row["value"]) for row in db.tables["gov_contact_identifiers"]})

            again = run_bio_monitor(db, fetch=_fetch, now=NOW, sleep=lambda _s: None)
        for index in again["indexes"]:
            self.assertEqual((index["positions_inserted"], index["positions_updated"], index["contacts_created"]), (0, 0, 0))

    def test_a_blocked_host_resumes_at_its_first_failed_page(self) -> None:
        def blocked(url: str) -> bytes:
            if "BioDisplay" in url:
                raise TimeoutError("reset by peer")
            return _fetch(url)

        db = _db()
        with patch("orchestration.gov.people.bios._MAX_FAILED_FETCHES", 2):
            result = run_bio_monitor(db, fetch=blocked, now=NOW, sleep=lambda _s: None, indexes=[FLAG])
        self.assertEqual(result["indexes"][0]["read"], 0)
        cursor = next(row for row in db.tables["gov_api_cache"] if row["cache_key"] == "cursor")
        self.assertEqual(cursor["payload"][FLAG.slug], 0)


if __name__ == "__main__":
    unittest.main()

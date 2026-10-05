from __future__ import annotations

import io
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from orchestration.gov.people.dvids import run_dvids_monitor
from orchestration.gov.people.leadership import (
    DEPARTMENTS,
    ExtractedChange,
    ExtractedChanges,
    change_events,
    get_json,
    lint_change,
    read_changes,
    story_changes,
    write_leadership_events,
)
from orchestration.gov.people.nominations import civilian_events, military_events, run_nomination_monitor
from orchestration.gov.people.releases import BACKFILL_URL, LISTING_URL, entry_events, run_release_monitor
from orchestration.gov.people.writer import upsert_positions
from tests.test_gov_people_writer import NOW, _Db

PUBLISHED = date(2026, 9, 26)
NAVY = DEPARTMENTS["navy"]


def _db(**extra: list[dict[str, Any]]) -> _Db:
    return _Db(
        agencies=[{"id": "agency-navy", "subtier_code": "1700"}, {"id": "agency-dod", "toptier_code": "097", "level": "toptier"}],
        gov_organizations=[
            {"id": "org-navy", "name": "Department of the Navy", "agency_id": "agency-dod", "existing_agency_id": "agency-navy"},
            {"id": "org-dod", "name": "Department of Defense", "agency_id": "agency-dod", "existing_agency_id": "agency-dod"},
            {"id": "org-navsea", "name": "Naval Sea Systems Command", "acronym": "NAVSEA", "agency_id": "agency-navy",
             "org_type": "other"},
            {"id": "org-frc", "name": "Fleet Readiness Center Southwest", "agency_id": "agency-navy", "org_type": "other"},
        ],
        gov_contacts=[], gov_contact_identifiers=[], gov_contact_positions=[], gov_contact_role_history=[],
        gov_api_cache=[], **extra,
    )


def _invoke_returning(*changes: ExtractedChange) -> Any:
    calls: list[str] = []

    async def invoke(**kwargs: Any) -> ExtractedChanges:
        calls.append(kwargs["user"])
        return ExtractedChanges(changes=list(changes))

    invoke.calls = calls  # type: ignore[attr-defined]
    return invoke


class ReaderTests(unittest.TestCase):
    def _one(self, text: str, story_unit: str | None = None) -> dict[str, Any]:
        [change] = read_changes(text, published=PUBLISHED, story_unit=story_unit)
        return change

    def test_relieved_as_names_both_people_post_and_ceremony_day(self) -> None:
        change = self._one("SAN DIEGO — Capt. Avery J. Lindqvist relieved Capt. Morgan Pell as commanding officer of "
                           "Arleigh Burke-class guided-missile destroyer USS Example (DDG 999) during a ceremony, Sept. 12.")
        self.assertEqual((change["incoming"], change["outgoing"]), ("Capt. Avery J. Lindqvist", "Capt. Morgan Pell"))
        self.assertEqual(change["post"], "commanding officer of USS Example (DDG 999)")
        self.assertEqual((change["day"], change["date_basis"]), (date(2026, 9, 12), "stated"))

    def test_relinquished_and_assumed_forms_build_the_post_from_the_unit(self) -> None:
        change = self._one("Rear Adm. Dana Okafor relinquished command of Fleet Readiness Center Southwest to "
                           "Capt. Riley Brandt on Aug. 3, 2026.")
        self.assertEqual((change["outgoing"], change["incoming"]), ("Rear Adm. Dana Okafor", "Capt. Riley Brandt"))
        self.assertEqual(change["post"], "Commander, Fleet Readiness Center Southwest")
        assumed = self._one("Capt. Riley Brandt assumed command of the installation from Capt. Sam Ferro.",
                            story_unit="Naval Air Station Example")
        self.assertEqual(assumed["post"], "Commander, Naval Air Station Example")
        self.assertEqual((assumed["day"], assumed["date_basis"]), (PUBLISHED, "observed"))

    def test_a_described_unit_takes_the_story_unit(self) -> None:
        change = self._one("Capt. Lee Varga relieved Capt. Noor Haddad as commanding officer of the premier joint "
                           "reserve installation in North Texas.", story_unit="Naval Air Station Example")
        self.assertEqual(change["post"], "commanding officer of Naval Air Station Example")

    def test_a_post_stops_before_the_ceremony_words(self) -> None:
        cases = {
            "Col. Lee Varga assumed command of Example Energy Pacific, bringing decisions closer to the fleet.":
                "Commander, Example Energy Pacific",
            "Col. Lee Varga assumed command of the 999th Example Wing, Sunday, Aug. 2.": "Commander, 999th Example Wing",
            "Capt. Lee Varga became the 40th commander of Example Shipyard, Training and Readiness Center Aug. 14.":
                "commander of Example Shipyard, Training and Readiness Center",
        }
        for text, post in cases.items():
            self.assertEqual(self._one(text, story_unit="HSC-99")["post"], post)

    def test_a_role_without_its_unit_is_left_to_the_model(self) -> None:
        text = "A ceremony for USS Example (SSN 999) was held where Cmdr. Lee Varga relieved Cmdr. Noor Haddad as commanding officer in front of guests."
        self.assertEqual(read_changes(text, published=PUBLISHED, story_unit="Example Shipyard"), [])

    def test_succeeding_names_the_outgoing_person(self) -> None:
        change = self._one("Lt. Col. Lee Varga assumed command of the 1st Battalion, 999th Example Regiment during a "
                           "ceremony Aug. 22, succeeding Lt. Col. Sam N. Ferro.")
        self.assertEqual((change["outgoing"], change["post"]), ("Lt. Col. Sam N. Ferro", "Commander, 1st Battalion, 999th Example Regiment"))

    def test_became_commander_finds_the_outgoing_person_by_surname(self) -> None:
        change = self._one("Capt. Avery Lindqvist became the 40th commander of Example Naval Shipyard Aug. 14. "
                           "Lindqvist relieved outgoing commander Capt. Morgan Pell, who retires.")
        self.assertEqual((change["outgoing"], change["post"]), ("Capt. Morgan Pell", "commander of Example Naval Shipyard"))

    def test_a_date_without_a_year_is_never_after_publication(self) -> None:
        change = self._one("Capt. Lee Varga relieved Capt. Noor Haddad as commander, Example Group, Dec. 20.")
        self.assertEqual(change["day"], date(2025, 12, 20))
        change = self._one("Col. Rae Quill relinquished command of the 9th Example Brigade to Col. Sam Ortiz at a "
                           "change of command ceremony on 9 July on Example Base.")
        self.assertEqual(change["day"], date(2026, 7, 9))

    def test_change_events_drop_a_person_named_by_surname_only(self) -> None:
        [change] = read_changes("Rear Adm. Dana Okafor relieved Vice Adm. Pell as the acting commander.", published=PUBLISHED)
        events = change_events(change, service="Navy", source_ref="u", source_url="u")
        self.assertEqual([(event["name"], event["event_type"], event["started"]) for event in events],
                         [("Dana Okafor", "appointment", False)])

    def test_change_events_keep_only_the_name(self) -> None:
        change = {"incoming": "Vice. Adm. Yvette M. Okafor", "outgoing": "National Guard Maj. Gen. Sam R. Ortiz (Ret.)",
                  "post": "deputy commander, Example Fleet", "day": date(2026, 7, 8), "date_basis": "stated", "quote": "q"}
        self.assertEqual([event["name"] for event in change_events(change, service="Navy", source_ref="u", source_url="u")],
                         ["Yvette M. Okafor", "Sam R. Ortiz"])

    def test_change_events_drop_a_senior_enlisted_handover(self) -> None:
        change = {"incoming": "Command Sgt. Maj. Ari Belden", "outgoing": "Command Sgt. Maj. Tove Marsh",
                  "post": "Commander, 9th Example Brigade", "day": date(2026, 7, 8), "date_basis": "stated", "quote": "q"}
        self.assertEqual(change_events(change, service="Army", source_ref="u", source_url="u"), [])


class GetJsonTests(unittest.TestCase):
    def test_a_dropped_connection_is_retried_and_a_refusal_is_not(self) -> None:
        replies: list[Any] = [urllib.error.URLError("reset"), io.BytesIO(b'{"ok": 1}')]

        def urlopen(*_args: Any, **_kwargs: Any) -> Any:
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        waits: list[float] = []
        with patch("urllib.request.urlopen", urlopen):
            self.assertEqual(get_json("https://example.test", sleep=waits.append), {"ok": 1})
            replies[:] = [urllib.error.HTTPError("u", 403, "forbidden", None, None)]  # type: ignore[arg-type]
            with self.assertRaises(urllib.error.HTTPError):
                get_json("https://example.test", sleep=waits.append)
        self.assertEqual(waits, [5])


class AgentTests(unittest.TestCase):
    TEXT = ("The ceremony marked a new chapter. Following the transition, Capt. Lee Varga took the helm of Example "
            "Group from Capt. Noor Haddad on Sept. 3, 2026, in a change of command.")

    def test_the_lint_keeps_only_verbatim_changes(self) -> None:
        good = ExtractedChange(incoming="Capt. Lee Varga", outgoing="Capt. Noor Haddad", post="Example Group",
                               date_text="Sept. 3, 2026", quote=self.TEXT.split(". ", 1)[1])
        self.assertTrue(lint_change(good, self.TEXT))
        self.assertFalse(lint_change(good.model_copy(update={"incoming": "Capt. Lee M. Varga"}), self.TEXT))
        self.assertFalse(lint_change(good.model_copy(update={"post": "Commander, Example Group"}), self.TEXT))
        self.assertFalse(lint_change(good.model_copy(update={"post": "commander"}), self.TEXT.replace("Example Group", "commander")))

    def test_a_post_that_names_its_role_keeps_it_and_a_bare_unit_is_its_command(self) -> None:
        text = ("Rear Adm. Lee Varga will be assigned as deputy commander, Example Fleet, Pearl Harbor, Hawaii. "
                "Capt. Rae Quill took the helm of USS Example (SSN 999).")
        invoke = _invoke_returning(
            ExtractedChange(incoming="Rear Adm. Lee Varga", post="deputy commander, Example Fleet, Pearl Harbor, Hawaii",
                            quote="Rear Adm. Lee Varga will be assigned as deputy commander, Example Fleet, Pearl Harbor, Hawaii."),
            ExtractedChange(incoming="Capt. Rae Quill", post="USS Example (SSN 999)",
                            quote="Capt. Rae Quill took the helm of USS Example (SSN 999)."))
        from orchestration.gov.people.leadership import agent_changes

        posts = [change["post"] for change in agent_changes(text, published=PUBLISHED, invoke=invoke)]
        self.assertEqual(posts, ["deputy commander, Example Fleet, Pearl Harbor, Hawaii", "Commander, USS Example (SSN 999)"])

    def test_the_model_reads_only_what_the_forms_miss(self) -> None:
        good = ExtractedChange(incoming="Capt. Lee Varga", outgoing="Capt. Noor Haddad", post="Example Group",
                               date_text="Sept. 3, 2026", quote=self.TEXT.split(". ", 1)[1])
        invented = ExtractedChange(incoming="Capt. Pat Quill", post="Example Group", quote="Pat Quill took command.")
        invoke = _invoke_returning(good, invented)
        changes, called = story_changes(self.TEXT, published=PUBLISHED, invoke=invoke)
        self.assertTrue(called)
        self.assertEqual([(c["incoming"], c["post"], c["day"], c["date_basis"]) for c in changes],
                         [("Capt. Lee Varga", "Commander, Example Group", date(2026, 9, 3), "stated")])
        fixed = "Capt. Lee Varga relieved Capt. Noor Haddad as commander, Example Group, Sept. 3, 2026."
        self.assertFalse(story_changes(fixed, published=PUBLISHED, invoke=invoke)[1])
        self.assertFalse(story_changes("A ribbon cutting.", published=PUBLISHED, invoke=invoke)[1])
        self.assertEqual(len(invoke.calls), 1)


class WriterEventTests(unittest.TestCase):
    def _change(self, **extra: Any) -> list[dict[str, Any]]:
        change = {"incoming": "Capt. Lee Varga", "outgoing": "Rear Adm. Dana Q. Okafor",
                  "post": "Commander, Fleet Readiness Center Southwest", "day": date(2026, 9, 3), "date_basis": "stated",
                  "quote": "Capt. Lee Varga relieved Rear Adm. Dana Q. Okafor as Commander, Fleet Readiness Center Southwest.",
                  **extra}
        return change_events(change, service="Navy", source_ref="https://example.test/news/1",
                             source_url="https://example.test/news/1")

    def test_a_change_opens_the_new_post_closes_the_old_and_reruns_add_nothing(self) -> None:
        db = _db()
        # The outgoing commander is already known from a bio, written without the middle initial.
        bio = [{"name": "Dana Okafor", "title": "Commander, Fleet Readiness Center Southwest", "agency": NAVY.name,
                "source_url": "u", "identifiers": [("name_org", f"dana okafor|{NAVY.name.lower()}")]}]
        from orchestration.gov.people.writer import resolve_contacts

        [okafor], _ = resolve_contacts(db, bio, source="bio_pages", now=NOW)
        upsert_positions(db, [{"contact_id": okafor, "organization_id": "org-frc", "role_type": "other",
                               "raw_title": "Commander, Fleet Readiness Center Southwest", "source_ref": "bio",
                               "first_observed_at": "2025-05-01", "last_observed_at": "2026-08-01",
                               "date_basis": "stated", "valid_from": "2024-06-01"}], source="bio_pages", now=NOW)

        result = write_leadership_events(db, self._change(), source="dvids_leadership", now=NOW)
        self.assertEqual((result["contacts_created"], result["positions_closed"], result["moves"]), (1, 1, 2))
        positions = {(row["contact_id"], row["source"]): row for row in db.tables["gov_contact_positions"]}
        closed = positions[(okafor, "bio_pages")]
        self.assertEqual((closed["valid_to"], closed["date_basis"]), ("2026-09-03", "stated"))
        [varga] = [row for key, row in positions.items() if key[1] == "dvids_leadership"]
        self.assertEqual((varga["organization_id"], varga["valid_from"], varga["date_basis"]), ("org-frc", "2026-09-03", "stated"))
        moves = {row["event_type"]: row for row in db.tables["gov_contact_role_history"]}
        self.assertEqual(moves["departure"]["gov_contact_id"], okafor)
        self.assertEqual(moves["appointment"]["started_at"], "2026-09-03")

        again = write_leadership_events(db, self._change(), source="dvids_leadership", now=NOW)
        self.assertEqual((again["contacts_created"], again["positions_inserted"], again["positions_closed"]), (0, 0, 0))
        self.assertEqual(len(db.tables["gov_contact_role_history"]), 2)
        # A stale bio read later does not reopen the post the change of command closed.
        stale = upsert_positions(db, [{"contact_id": okafor, "organization_id": "org-frc", "role_type": "other",
                                       "raw_title": "Commander, Fleet Readiness Center Southwest", "source_ref": "bio",
                                       "first_observed_at": "2026-09-20", "last_observed_at": "2026-09-20",
                                       "date_basis": "stated", "valid_from": "2024-06-01"}], source="bio_pages", now=NOW)
        self.assertEqual((stale["positions_inserted"], stale["positions_reactivated"]), (0, 0))

    def test_a_closed_post_reads_stated_only_when_its_start_and_end_both_are(self) -> None:
        # an observed post is written without a start date, so a stated end is then its only date
        for start, close, want in [("stated", "observed", "observed"), ("observed", "stated", "stated"),
                                   ("stated", "stated", "stated")]:
            db = _db()
            from orchestration.gov.people.writer import resolve_contacts

            bio = [{"name": "Dana Okafor", "title": "Commander, Fleet Readiness Center Southwest", "agency": NAVY.name,
                    "source_url": "u", "identifiers": [("name_org", f"dana okafor|{NAVY.name.lower()}")]}]
            [okafor], _ = resolve_contacts(db, bio, source="bio_pages", now=NOW)
            upsert_positions(db, [{"contact_id": okafor, "organization_id": "org-frc", "role_type": "other",
                                   "raw_title": "Commander, Fleet Readiness Center Southwest", "source_ref": "bio",
                                   "first_observed_at": "2025-05-01", "last_observed_at": "2026-08-01",
                                   "date_basis": start, "valid_from": "2024-06-01"}], source="bio_pages", now=NOW)
            write_leadership_events(db, self._change(date_basis=close), source="dvids_leadership", now=NOW)
            [closed] = [row for row in db.tables["gov_contact_positions"] if row["source"] == "bio_pages"]
            self.assertEqual((closed["valid_to"], closed["date_basis"]), ("2026-09-03", want), (start, close))

    def test_two_stories_of_one_ceremony_are_one_person_and_one_move(self) -> None:
        db = _db()
        first = self._change()
        second = [{**event, "source_ref": "https://example.test/news/2", "source_url": "https://example.test/news/2",
                   "name": event["name"].replace("Dana Q. Okafor", "Dana Okafor"), "quote": "Okafor handed over the center."}
                  for event in self._change()]
        write_leadership_events(db, [*first, *second], source="dvids_leadership", now=NOW)
        self.assertEqual(len(db.tables["gov_contacts"]), 2)
        moves = db.tables["gov_contact_role_history"]
        self.assertEqual(sorted(row["event_type"] for row in moves), ["appointment", "departure"])
        departure = next(row for row in moves if row["event_type"] == "departure")
        self.assertEqual([item["source_url"] for item in departure["evidence"]],
                         ["https://example.test/news/1", "https://example.test/news/2"])
        # A third story read on a later run joins the same row.
        third = [{**event, "source_ref": "n3", "source_url": "n3", "quote": "Okafor retired."} for event in second]
        write_leadership_events(db, third, source="dvids_leadership", now=NOW)
        self.assertEqual(len(db.tables["gov_contact_role_history"]), 2)
        self.assertEqual(len(next(row for row in db.tables["gov_contact_role_history"]
                                  if row["event_type"] == "departure")["evidence"]), 3)

    def test_name_forms_in_one_run_join_the_contact_the_fuller_name_has(self) -> None:
        db = _db()
        from orchestration.gov.people.writer import resolve_contacts

        # Known from a grade nomination, which names no post.
        [nominee], _ = resolve_contacts(db, [{"name": "Lee R. Varga", "title": None, "agency": NAVY.name, "source_url": "n",
                                              "identifiers": [("name_org", f"lee r varga|{NAVY.name.lower()}")]}],
                                        source="congress_nominations", now=NOW)
        short = self._change()
        full = [{**event, "name": event["name"].replace("Lee Varga", "Lee R. Varga"), "source_ref": "n2", "source_url": "n2"}
                for event in self._change()]
        write_leadership_events(db, [*short, *full], source="dvids_leadership", now=NOW)
        appointments = [row for row in db.tables["gov_contact_role_history"] if row["event_type"] == "appointment"]
        self.assertEqual([row["gov_contact_id"] for row in appointments], [nominee])
        self.assertEqual(len(appointments[0]["evidence"]), 2)

    def test_a_name_match_without_the_post_stays_a_separate_person(self) -> None:
        db = _db()
        from orchestration.gov.people.writer import resolve_contacts

        resolve_contacts(db, [{"name": "Dana Okafor", "title": "Director, Example Lab", "agency": NAVY.name, "source_url": "u",
                               "identifiers": [("name_org", f"dana okafor|{NAVY.name.lower()}")]}], source="bio_pages", now=NOW)
        write_leadership_events(db, self._change(), source="dvids_leadership", now=NOW)
        self.assertEqual(len(db.tables["gov_contacts"]), 3)

    def test_an_announcement_opens_no_future_post(self) -> None:
        db = _db()
        events = [{"name": "Riley Brandt", "department": NAVY, "event_type": "announcement",
                   "title": "Commander, Naval Sea Systems Command", "previous_title": "Commander, Fleet Readiness Center Southwest",
                   "holds": "Commander, Fleet Readiness Center Southwest", "day": date(2026, 9, 17), "date_basis": "stated",
                   "source_ref": "r", "source_url": "r", "quote": "q"}]
        write_leadership_events(db, events, source="war_gov_releases", now=NOW)
        [position] = db.tables["gov_contact_positions"]
        self.assertEqual((position["organization_id"], position["date_basis"], position["valid_from"]), ("org-frc", "observed", None))
        [move] = db.tables["gov_contact_role_history"]
        self.assertEqual((move["organization_id"], move["previous_organization_id"], move["started_at"]),
                         ("org-navsea", "org-frc", None))


class ReopenRuleTests(unittest.TestCase):
    def _row(self, day: str, **extra: Any) -> dict[str, Any]:
        return {"contact_id": "c1", "organization_id": "o1", "role_type": "other", "source_ref": "s",
                "first_observed_at": day, "last_observed_at": day, **extra}

    def test_an_observation_after_an_observed_close_reopens(self) -> None:
        db = _Db(gov_contact_positions=[{**self._row("2026-01-01"), "id": "p1", "source": "fpds_staff",
                                         "valid_to": "2026-03-01", "date_basis": "observed"}])
        self.assertEqual(upsert_positions(db, [self._row("2026-02-01")], source="fpds_staff", now=NOW)["positions_reactivated"], 0)
        self.assertEqual(upsert_positions(db, [self._row("2026-05-01")], source="fpds_staff", now=NOW)["positions_reactivated"], 1)

    def test_only_a_later_stated_start_reopens_a_stated_close(self) -> None:
        db = _Db(gov_contact_positions=[{**self._row("2026-01-01"), "id": "p1", "source": "bio_pages",
                                         "valid_to": "2026-03-01", "date_basis": "stated"}])
        self.assertEqual(upsert_positions(db, [self._row("2026-06-01")], source="bio_pages", now=NOW)["positions_reactivated"], 0)
        later = self._row("2026-06-01", date_basis="stated", valid_from="2026-05-15")
        self.assertEqual(upsert_positions(db, [later], source="bio_pages", now=NOW)["positions_reactivated"], 1)


LISTING = """<div><a article-id="{newer}" article-title="General Officer Announcements for Sept. 17, 2026"
article-url="https://www.war.gov/News/Releases/Release/Article/{newer}/"></a>
<a article-id="{older}" article-title="Flag Officer Assignments" article-url="https://www.war.gov/News/Releases/Release/Article/{older}/"></a>
<a article-id="90" article-title="Contracts for Sept. 16, 2026" article-url="https://www.war.gov/x/"></a></div>"""
RELEASE = """<span class="date">Sept. 10, 2026</span><div class="body">
<p>Secretary of War announced today that the President has made the following nominations:</p>
<p>Navy Rear Adm. Dana Q. Okafor for appointment to the grade of vice admiral, with assignment as commander, Naval
Sea Systems Command, Washington, D.C. Okafor is currently serving as commander, Fleet Readiness Center Southwest,
San Diego, California.</p>
<p>Navy Rear Adm. (lower half) Riley Brandt for appointment to the grade of rear admiral. Brandt is currently serving
as director, Example Lab, Arlington, Virginia.</p>
<p>Navy Capt. Pat Quill has been nominated for appointment to the rank of rear admiral (lower half). Quill most
recently served as deputy, Example Office.</p>
</div>"""


class ReleaseTests(unittest.TestCase):
    def test_entry_forms(self) -> None:
        paragraph = ("Navy Rear Adm. Dana Q. Okafor for appointment to the grade of vice admiral, with assignment as commander, "
                     "Naval Sea Systems Command, Washington, D.C. Okafor is currently serving as commander, Fleet Readiness "
                     "Center Southwest, San Diego, California.")
        [event] = entry_events(paragraph, day=date(2026, 9, 17), url="u")
        self.assertEqual((event["name"], event["event_type"], event["department"]), ("Dana Q. Okafor", "announcement", NAVY))
        self.assertEqual(event["holds"], "commander, Fleet Readiness Center Southwest, San Diego, California")
        self.assertEqual(entry_events("Navy Capt. Pat Quill was honored.", day=date(2026, 9, 17), url="u"), None)

    def test_monitor_reads_new_releases_once(self) -> None:
        pages = {LISTING_URL.format(page=1): LISTING.format(newer=200, older=100),
                 "https://www.war.gov/News/Releases/Release/Article/200/": RELEASE,
                 "https://www.war.gov/News/Releases/Release/Article/100/": "<div class=\"body\"><p>none</p></div>"}
        invoke = _invoke_returning()
        db = _db()
        result = run_release_monitor(db, fetch=lambda url: pages[url].encode(), invoke=invoke, now=NOW, pages=1,
                                     sleep=lambda _s: None)
        self.assertEqual((result["releases_read"], result["unread"], result["model_calls"]), (2, 1, 1))
        kinds = sorted((row["name"], row["event_type"]) for row in db.tables["gov_contact_role_history"])
        self.assertEqual(kinds, [("Dana Q. Okafor", "announcement")])
        self.assertEqual({row["raw_title"] for row in db.tables["gov_contact_positions"]},
                         {"commander, Fleet Readiness Center Southwest, San Diego, California",
                          "director, Example Lab, Arlington, Virginia"})
        again = run_release_monitor(db, fetch=lambda url: pages[url].encode(), invoke=invoke, now=NOW, pages=1,
                                    sleep=lambda _s: None)
        self.assertEqual((again["releases_listed"], again["moves"]), (0, 0))
        # A backfill reads older announcements the cursor has passed, and leaves the cursor where it was.
        pages[BACKFILL_URL.format(page=1)] = LISTING.format(newer=150, older=100)
        pages["https://www.war.gov/News/Releases/Release/Article/150/"] = RELEASE
        backfill = run_release_monitor(db, fetch=lambda url: pages[url].encode(), invoke=invoke, now=NOW, pages=1,
                                       sleep=lambda _s: None, backfill=True)
        self.assertEqual(backfill["releases_read"], 2)
        cursor = next(row for row in db.tables["gov_api_cache"] if row["source"] == "people_war_gov_releases")["payload"]
        self.assertEqual(cursor["newest_id"], 200)


def _nomination(number: int, organization: str, *, military: bool, updated: str, **extra: Any) -> dict[str, Any]:
    return {"citation": f"PN{number}", "congress": 119, "number": number, "organization": organization,
            "nominationType": {"isMilitary": True} if military else {"isCivilian": True}, "receivedDate": "2026-03-02",
            "updateDate": updated, "url": f"https://api.congress.gov/v3/nomination/119/{number}?format=json",
            "latestAction": {"actionDate": "2026-03-02", "text": "Received in the Senate."}, **extra}


CONFIRMED = {"actionDate": "2026-06-01", "text": "Confirmed by the Senate by Voice Vote."}


class NominationTests(unittest.TestCase):
    def test_civilian_nominee_confirmation_and_predecessor(self) -> None:
        item = _nomination(1, "Navy", military=False, updated="u", latestAction=CONFIRMED,
                           description="Riley Brandt, of Virginia, to be an Assistant Secretary of the Navy, vice Dana Okafor, resigned.")
        events = {event["event_type"]: event for event in civilian_events(item)}
        self.assertEqual((events["announcement"]["name"], events["announcement"]["title"]), ("Riley Brandt", "Assistant Secretary of the Navy"))
        self.assertEqual((events["appointment"]["day"], events["appointment"]["started"]), (date(2026, 6, 1), False))
        self.assertEqual((events["departure"]["name"], events["departure"]["date_basis"]), ("Dana Okafor", "observed"))
        self.assertEqual(events["announcement"]["department"], NAVY)
        unexplained = {**item, "description": "Riley Brandt, of Virginia, to be Secretary of the Navy, vice Dana Okafor."}
        self.assertNotIn("departure", {event["event_type"] for event in civilian_events(unexplained)})

    def test_military_lists_read_only_flag_grades(self) -> None:
        detail = {"nominees": [
            {"introText": "The following named officer for appointment as Vice Chief of Naval Operations and appointment "
                          "to the grade indicated", "positionTitle": "Admiral", "organization": "Navy", "url": "a"},
            {"introText": "The following named officers for appointment to the grade indicated", "positionTitle": "Rear Admiral",
             "organization": "Navy", "url": "b"},
            {"introText": "x", "positionTitle": "Captain", "organization": "Navy", "url": "c"}]}
        people = {"a": [{"firstName": "Dana", "middleName": "Q.", "lastName": "Okafor"}],
                  "b": [{"firstName": "Riley", "lastName": "Brandt"}], "c": [{"firstName": "Pat", "lastName": "Quill"}]}
        asked: list[str] = []
        events = military_events(_nomination(2, "Navy", military=True, updated="u"), detail,
                                 lambda url: asked.append(url) or people[url])
        self.assertEqual([(e["name"], e["event_type"], e["title"]) for e in events],
                         [("Dana Q. Okafor", "announcement", "Vice Chief of Naval Operations")])
        self.assertEqual(asked, ["a"])
        confirmed = military_events(_nomination(2, "Navy", military=True, updated="u", latestAction=CONFIRMED), detail,
                                    lambda url: people[url])
        self.assertIn(("Riley Brandt", "promotion", "Rear Admiral"), [(e["name"], e["event_type"], e["title"]) for e in confirmed])

    def test_monitor_resumes_after_its_call_budget(self) -> None:
        listing = [
            _nomination(1, "Navy", military=False, updated="2026-09-01T00:00:00Z",
                        description="Riley Brandt, of Virginia, to be Under Secretary of the Navy, vice Dana Okafor, retired."),
            _nomination(2, "Department of State", military=False, updated="2026-09-02T00:00:00Z", description="x"),
            _nomination(3, "Navy", military=True, updated="2026-09-03T00:00:00Z"),
        ]
        calls: list[str] = []

        def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
            calls.append(path)
            if path == "nomination":
                return {"nominations": listing}
            return {"nomination": {"nominees": []}}

        db = _db()
        first = run_nomination_monitor(db, get=get, now=NOW, calls_per_run=1, since=NOW - timedelta(days=60))
        self.assertEqual((first["relevant"], first["read"], first["budget_spent"]), (2, 1, True))
        self.assertEqual({row["event_type"] for row in db.tables["gov_contact_role_history"]}, {"announcement", "departure"})
        cursor = next(row for row in db.tables["gov_api_cache"] if row["source"] == "people_congress_nominations")
        self.assertEqual(cursor["payload"]["updated_since"], "2026-09-01T00:00:00+00:00")
        second = run_nomination_monitor(db, get=get, now=NOW, calls_per_run=5)
        self.assertEqual((second["read"], second["budget_spent"], second["contacts_created"]), (2, False, 0))
        self.assertEqual(calls.count("nomination/119/3"), 1)


STORY = ("<p>EXAMPLE CITY — Capt. Lee Varga relieved Rear Adm. Dana Q. Okafor as commander, Fleet Readiness Center "
         "Southwest during a ceremony Sept. 3, 2026.</p><p>Okafor retires after 30 years.</p>")


class DvidsTests(unittest.TestCase):
    def test_monitor_reads_each_story_once_and_caps_model_calls(self) -> None:
        stories = {
            "news:1": {"body": STORY, "url": "https://www.dvidshub.net/news/1/x", "branch": "Navy", "unit_name": "FRCSW",
                       "date_published": "2026-09-04T10:00:00Z"},
            "news:2": {"body": "<p>The change of command was held, and the new leader took the helm.</p>",
                       "url": "https://www.dvidshub.net/news/2/y", "branch": "Navy", "date_published": "2026-09-05T10:00:00Z"},
        }
        listing = [{"id": key, "date": story["date_published"]} for key, story in stories.items()]

        def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
            if path == "search":
                since = params["from_date"][:10]
                return {"results": [item for item in listing if item["date"][:10] >= since] if params["page"] == 1 else []}
            return {"results": stories[params["id"]]}

        invoke = _invoke_returning()
        db = _db()
        result = run_dvids_monitor(db, get=get, invoke=invoke, now=NOW, sleep=lambda _s: None)
        self.assertEqual((result["stories_listed"], result["stories_read"], result["model_calls"], result["moves"]), (2, 2, 1, 2))
        [position] = [row for row in db.tables["gov_contact_positions"] if row.get("valid_to") is None]
        self.assertEqual((position["organization_id"], position["valid_from"]), ("org-frc", "2026-09-03"))
        again = run_dvids_monitor(db, get=get, invoke=invoke, now=NOW, sleep=lambda _s: None)
        self.assertEqual(again["stories_listed"], 0)
        self.assertEqual(len(db.tables["gov_contact_role_history"]), 2)
        # A story dated weeks back but put up after the last run is still found.
        stories["news:3"] = {**stories["news:1"], "url": "https://www.dvidshub.net/news/3/z", "date_published": "2026-09-30T10:00:00Z"}
        listing.append({"id": "news:3", "date": "2026-09-08T10:00:00Z"})
        late = run_dvids_monitor(db, get=get, invoke=invoke, now=NOW, sleep=lambda _s: None)
        self.assertEqual((late["stories_listed"], late["stories_read"]), (1, 1))

    def test_a_budget_stop_resumes_at_the_oldest_unread_story(self) -> None:
        listing = [{"id": f"news:{n}", "date": f"2026-0{month}-10T00:00:00Z"} for n, month in ((1, 7), (2, 8), (3, 9))]
        asked: list[str] = []

        def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
            if path == "search":
                since = params["from_date"][:10]
                return {"results": [item for item in listing if item["date"][:10] >= since] if params["page"] == 1 else []}
            asked.append(params["id"])
            return {"results": {"body": "<p>Nothing here.</p>", "url": "u", "date_published": "2026-09-10"}}

        db = _db()
        run_dvids_monitor(db, get=get, now=NOW, stories_per_run=1, sleep=lambda _s: None)
        cursor = next(row for row in db.tables["gov_api_cache"] if row["source"] == "people_dvids")["payload"]
        self.assertEqual(cursor["since"], "2026-08-10")
        run_dvids_monitor(db, get=get, now=NOW, stories_per_run=5, sleep=lambda _s: None)
        run_dvids_monitor(db, get=get, now=NOW, stories_per_run=5, sleep=lambda _s: None)
        self.assertEqual(asked, ["news:1", "news:2", "news:3"])

    def test_a_story_without_a_usable_date_is_read_past(self) -> None:
        listing = [{"id": "news:1", "date": "2026-09-01T00:00:00Z"}, {"id": "news:2", "date": "2026-09-02T00:00:00Z"}]
        stories = {"news:1": {"body": "<p>Undated.</p>", "url": "https://www.dvidshub.net/news/1/x", "date_published": ""},
                   "news:2": {"body": STORY, "url": "https://www.dvidshub.net/news/2/y", "branch": "Navy",
                              "date_published": "2026-09-04"}}

        def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
            if path == "search":
                since = params["from_date"][:10]
                return {"results": [item for item in listing if item["date"][:10] >= since] if params["page"] == 1 else []}
            return {"results": stories[params["id"]]}

        db = _db()
        first = run_dvids_monitor(db, get=get, now=NOW, sleep=lambda _s: None)
        self.assertEqual((first["stories_read"], first["moves"]), (2, 2))
        self.assertEqual(run_dvids_monitor(db, get=get, now=NOW, sleep=lambda _s: None)["stories_listed"], 0)

    def test_a_model_outage_keeps_what_was_read_and_resumes_at_its_story(self) -> None:
        stories = {
            "news:1": {"body": STORY, "url": "https://www.dvidshub.net/news/1/x", "branch": "Navy", "date_published": "2026-09-04"},
            "news:2": {"body": "<p>The change of command was held, and the new leader took the helm.</p>",
                       "url": "https://www.dvidshub.net/news/2/y", "branch": "Navy", "date_published": "2026-09-05"},
        }
        listing = [{"id": key, "date": story["date_published"]} for key, story in stories.items()]

        def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
            if path == "search":
                since = params["from_date"][:10]
                return {"results": [item for item in listing if item["date"][:10] >= since] if params["page"] == 1 else []}
            return {"results": stories[params["id"]]}

        async def down(**_kwargs: Any) -> ExtractedChanges:
            raise ConnectionError("model provider unreachable")

        db = _db()
        result = run_dvids_monitor(db, get=get, invoke=down, now=NOW, sleep=lambda _s: None)
        self.assertEqual((result["stories_read"], result["moves"]), (1, 2))
        again = run_dvids_monitor(db, get=get, invoke=_invoke_returning(), now=NOW, sleep=lambda _s: None)
        self.assertEqual((again["stories_listed"], again["stories_read"], again["model_calls"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()

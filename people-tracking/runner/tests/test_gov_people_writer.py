from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from orchestration.gov.people.writer import (
    _query_chunks,
    email_person_key,
    link_identities,
    parse_fpds_user,
    record_moves,
    resolve_contacts,
    upsert_positions,
)


NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
MERGE_TABLES = {"gov_contact_positions": "contact_id", "gov_contact_identifiers": "contact_id",
                "gov_contact_role_history": "gov_contact_id"}


@dataclass
class _Response:
    data: Any


class _Query:
    def __init__(self, db: "_Db", table: str) -> None:
        self.db, self.table = db, table
        self.filters: list[tuple[str, str, Any]] = []
        self.action, self.payload, self.conflict, self.ignore = "select", None, "", False
        self.start, self.end, self.single = 0, 999, False

    def select(self, *_args: Any) -> "_Query": return self
    def maybe_single(self) -> "_Query": self.single = True; return self
    def order(self, *_args: Any) -> "_Query": return self
    def range(self, start: int, end: int) -> "_Query": self.start, self.end = start, end; return self
    def eq(self, field: str, value: Any) -> "_Query": self.filters.append(("eq", field, value)); return self
    def in_(self, field: str, value: list[Any]) -> "_Query": self.filters.append(("in", field, set(value))); return self
    def like(self, field: str, value: str) -> "_Query": self.filters.append(("like", field, value)); return self
    def ilike(self, field: str, value: str) -> "_Query": self.filters.append(("ilike", field, value)); return self
    def gte(self, field: str, value: Any) -> "_Query": self.filters.append(("gte", field, value)); return self
    def update(self, values: dict[str, Any]) -> "_Query": self.action, self.payload = "update", dict(values); return self
    def insert(self, values: list[dict[str, Any]]) -> "_Query":
        self.action, self.payload = "insert", [dict(row) for row in values]
        return self
    def upsert(self, values: Any, on_conflict: str, ignore_duplicates: bool = False) -> "_Query":
        rows = values if isinstance(values, list) else [values]
        self.action, self.payload, self.conflict, self.ignore = "upsert", [dict(r) for r in rows], on_conflict, ignore_duplicates
        return self

    def _rows(self) -> list[dict[str, Any]]:
        rows = self.db.tables.setdefault(self.table, [])
        for kind, field, value in self.filters:
            if kind == "eq": rows = [row for row in rows if row.get(field) == value]
            elif kind == "in": rows = [row for row in rows if row.get(field) in value]
            elif kind == "gte": rows = [row for row in rows if str(row.get(field) or "") >= str(value)]
            else:
                pattern = re.compile("^" + re.escape(value).replace("%", ".*") + "$", re.I if kind == "ilike" else 0)
                rows = [row for row in rows if pattern.match(str(row.get(field) or ""))]
        return rows

    def execute(self) -> _Response:
        table = self.db.tables.setdefault(self.table, [])
        if self.action in {"upsert", "insert"}:
            keys = self.conflict.split(",") if self.conflict else []
            # Postgres refuses one ON CONFLICT DO UPDATE that names the same key twice (error 21000).
            seen = [tuple(row.get(k) for k in keys) for row in self.payload]
            if keys and not self.ignore and len(seen) != len(set(seen)):
                raise RuntimeError("ON CONFLICT DO UPDATE command cannot affect row a second time")
            for incoming in self.payload:
                current = next((row for row in table if keys and all(row.get(k) == incoming.get(k) for k in keys)), None)
                if current and not self.ignore: current.update(incoming)
                elif not current:
                    incoming.setdefault("id", self.db.next_id(self.table))
                    table.append(incoming)
            return _Response(self.payload)
        rows = self._rows()
        if self.action == "update":
            for row in rows: row.update(self.payload)
        if self.single:
            return _Response(rows[0] if rows else None)
        return _Response(rows[self.start : self.end + 1])


class _Rpc:
    def __init__(self, db: "_Db", name: str, params: dict[str, Any]) -> None:
        self.db, self.name, self.params = db, name, params

    def execute(self) -> _Response:
        assert self.name == "gov_merge_contacts"
        survivor, duplicate = self.params["p_survivor"], self.params["p_duplicate"]
        for table, column in MERGE_TABLES.items():
            for row in self.db.tables.get(table, []):
                if row.get(column) == duplicate: row[column] = survivor
        self.db.tables["gov_contacts"] = [row for row in self.db.tables["gov_contacts"] if row["id"] != duplicate]
        self.db.merges.append((survivor, duplicate))
        return _Response(None)


class _Db:
    def __init__(self, **tables: list[dict[str, Any]]) -> None:
        self.tables = {name: list(rows) for name, rows in tables.items()}
        self.counter = 0
        self.merges: list[tuple[str, str]] = []

    def next_id(self, table: str) -> str:
        self.counter += 1
        return f"{table}-{self.counter}"

    def table(self, name: str) -> _Query: return _Query(self, name)
    def rpc(self, name: str, params: dict[str, Any]) -> _Rpc: return _Rpc(self, name, params)


def _fpds(user: str) -> dict[str, Any]:
    return {"fpds_user": user, "agency": "Department of the Navy", "role": "contracting_officer"}


class IdentityParsingTests(unittest.TestCase):
    def test_fpds_domain_variants_are_one_person(self) -> None:
        keys = {parse_fpds_user(value)["person"] for value in (
            "JANE.Q.DOE.CIV.N00024@US.NAVY.MIL", "JANE.Q.DOE.N00024@NAVY.MIL", "jane.q.doe.N00173@NAVY")}
        self.assertEqual(keys, {"jane.q.doe@navy.mil"})
        parsed = parse_fpds_user("JOHN.SMITH4.MIL.N00024@NAVY.MIL")
        self.assertEqual((parsed["office"], parsed["kind"], parsed["name"]), ("N00024", "MIL", "John Smith"))
        self.assertIsNone(parse_fpds_user("CBPUSER17"))
        # A six-letter surname is not an office code: these are two people, not one Clarissa.
        self.assertIsNone(parse_fpds_user("CASEY.QUIXLE@US.AF.MIL"))
        self.assertIsNone(parse_fpds_user("JOHN.SIPIN1@NAVY.MIL"))
        self.assertEqual([parse_fpds_user(f"PAT.LEE.{code}@ARMY.MIL")["office"] for code in ("W912CH", "SPE4A0", "HR0011")],
                         ["W912CH", "SPE4A0", "HR0011"])

    def test_only_dod_mail_implies_an_fpds_person(self) -> None:
        self.assertEqual(email_person_key("Jane.Q.Doe.civ@us.navy.mil"), "jane.q.doe@navy.mil")
        self.assertIsNone(email_person_key("jane.doe@fda.gov"))
        self.assertIsNone(email_person_key("navsea.contracts@navy.mil"))


class QueryChunkTests(unittest.TestCase):
    def test_long_identity_keys_split_by_url_length(self) -> None:
        keys = [f"name:person number {n}|agency:department of the navy naval sea systems command" for n in range(150)]
        chunks = list(_query_chunks(keys))
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(chunks, []), keys)
        self.assertEqual([len(chunk) for chunk in _query_chunks([f"{n:036d}" for n in range(300)])], [150, 150])


class ResolveContactsTests(unittest.TestCase):
    def test_one_contact_across_offices_and_reruns(self) -> None:
        db = _Db(gov_contacts=[], gov_contact_identifiers=[])
        people = [_fpds("JANE.Q.DOE.CIV.N00024@US.NAVY.MIL"), _fpds("JANE.Q.DOE.CIV.N00173@US.NAVY.MIL")]
        first, stats = resolve_contacts(db, people, source="fpds_staff", now=NOW)
        self.assertEqual(len(set(first)), 1)
        self.assertEqual(stats["contacts_created"], 1)
        self.assertEqual(db.tables["gov_contacts"][0]["identity_key"], "fpds:jane.q.doe@navy.mil")
        self.assertEqual(db.tables["gov_contacts"][0]["name"], "Jane Q Doe")
        again, stats = resolve_contacts(db, people, source="fpds_staff", now=NOW)
        self.assertEqual((again, stats["contacts_created"], stats["identifiers_added"]), (first, 0, 0))
        self.assertEqual(sorted(row["kind"] for row in db.tables["gov_contact_identifiers"]),
                         ["fpds_person", "fpds_user", "fpds_user"])

    def test_one_batch_folds_email_and_office_ids_of_one_person(self) -> None:
        db = _Db(gov_contacts=[], gov_contact_identifiers=[])
        people = [{"email": "john.doe@navy.mil", "name": "John Doe"}, _fpds("JOHN.DOE.N00024@NAVY.MIL")]
        resolved, stats = resolve_contacts(db, people, source="fpds_staff", now=NOW)
        self.assertEqual((len(set(resolved)), stats["contacts_created"], stats["identifier_conflicts"]), (1, 1, 0))

    def test_fpds_person_resolves_to_known_email_contact(self) -> None:
        email = {"id": "c-email", "identity_key": "email:jane.q.doe.civ@us.navy.mil", "email": "jane.q.doe.civ@us.navy.mil"}
        db = _Db(gov_contacts=[email], gov_contact_identifiers=[])
        self.assertEqual(link_identities(db, now=NOW)["identifiers_added"], 1)
        resolved, stats = resolve_contacts(db, [_fpds("JANE.Q.DOE.CIV.N00024@US.NAVY.MIL")], source="fpds_staff", now=NOW)
        self.assertEqual((resolved, stats["contacts_created"]), (["c-email"], 0))

    def test_later_email_contact_absorbs_the_fpds_only_contact(self) -> None:
        db = _Db(gov_contacts=[], gov_contact_identifiers=[], gov_contact_positions=[])
        [fpds_contact], _ = resolve_contacts(db, [_fpds("JANE.Q.DOE.CIV.N00024@US.NAVY.MIL")], source="fpds_staff", now=NOW)
        db.tables["gov_contact_positions"].append({"id": "p1", "contact_id": fpds_contact})
        db.tables["gov_contacts"].append({"id": "c-email", "identity_key": "email:jane.q.doe.civ@us.navy.mil",
                                          "email": "jane.q.doe.civ@us.navy.mil"})
        stats = link_identities(db, now=NOW)
        self.assertEqual((stats["contacts_merged"], db.merges), (1, [("c-email", fpds_contact)]))
        self.assertEqual(db.tables["gov_contact_positions"][0]["contact_id"], "c-email")
        self.assertEqual(link_identities(db, now=NOW), {"identifiers_added": 0, "contacts_merged": 0, "identity_conflicts": 0})

    def test_fpds_contact_folds_into_the_holder_of_its_key(self) -> None:
        db = _Db(gov_contacts=[
            {"id": "c-email", "identity_key": "email:jane.q.doe@us.af.mil", "email": "jane.q.doe@us.af.mil"},
            {"id": "c-fpds", "identity_key": "fpds:jane.q.doe@af.mil"},
        ], gov_contact_identifiers=[
            {"id": "i1", "contact_id": "c-email", "kind": "fpds_person", "value": "jane.q.doe@af.mil"},
            {"id": "i2", "contact_id": "c-fpds", "kind": "fpds_user", "value": "jane.q.doe.fa8601@us.af.mil"},
        ])
        self.assertEqual(link_identities(db, now=NOW)["contacts_merged"], 1)
        self.assertEqual({row["contact_id"] for row in db.tables["gov_contact_identifiers"]}, {"c-email"})

    def test_two_email_contacts_with_one_key_are_never_merged(self) -> None:
        db = _Db(gov_contacts=[
            {"id": "a", "identity_key": "email:jane.q.doe.civ@us.navy.mil", "email": "jane.q.doe.civ@us.navy.mil"},
            {"id": "b", "identity_key": "email:jane.q.doe@navy.mil", "email": "jane.q.doe@navy.mil"},
        ], gov_contact_identifiers=[])
        stats = link_identities(db, now=NOW)
        self.assertEqual((stats["contacts_merged"], stats["identity_conflicts"], db.merges), (0, 1, []))


class PositionTests(unittest.TestCase):
    def _row(self, first: str, last: str, **extra: Any) -> dict[str, Any]:
        return {"contact_id": "c1", "organization_id": "o1", "role_type": "contracting_officer", "source_ref": "N00024",
                "first_observed_at": first, "last_observed_at": last, **extra}

    def test_observed_positions_have_no_start_and_extend_idempotently(self) -> None:
        db = _Db(gov_contact_positions=[])
        first = upsert_positions(db, [self._row("2025-01-05", "2025-03-01"), self._row("2024-11-02", "2025-02-01")],
                                 source="fpds_staff", now=NOW)
        [row] = db.tables["gov_contact_positions"]
        self.assertEqual((first["positions_inserted"], row["valid_from"], row["date_basis"]), (1, None, "observed"))
        self.assertEqual((row["first_observed_at"][:10], row["last_observed_at"][:10]), ("2024-11-02", "2025-03-01"))
        self.assertEqual(upsert_positions(db, [self._row("2025-01-05", "2025-03-01")], source="fpds_staff", now=NOW)
                         ["positions_updated"], 0)
        upsert_positions(db, [self._row("2025-06-01", "2025-07-15")], source="fpds_staff", now=NOW)
        self.assertEqual(row["last_observed_at"][:10], "2025-07-15")
        self.assertIsNone(row["valid_to"])

    def test_full_roster_closes_on_last_observed_day(self) -> None:
        db = _Db(gov_contact_positions=[])
        upsert_positions(db, [self._row("2025-01-05", "2025-03-01")], source="directory", now=NOW, close_missing=True)
        closed = upsert_positions(db, [], source="directory", now=NOW, close_missing=True)
        self.assertEqual((closed["positions_closed"], db.tables["gov_contact_positions"][0]["valid_to"]), (1, "2025-03-01"))

    def test_stated_start_is_kept(self) -> None:
        db = _Db(gov_contact_positions=[])
        upsert_positions(db, [self._row("2026-09-01", "2026-09-01", date_basis="stated", valid_from="2026-08-14")],
                         source="war_gov_releases", now=NOW)
        row = db.tables["gov_contact_positions"][0]
        self.assertEqual((row["valid_from"], row["date_basis"]), ("2026-08-14", "stated"))

    def test_the_latest_observation_names_the_post_in_any_order(self) -> None:
        db = _Db(gov_contact_positions=[])
        new = self._row("2026-09-01", "2026-09-01", raw_title="Contracting Officer", source_url="https://sam.gov/opp/new/view")
        old = self._row("2025-01-05", "2025-01-05", raw_title="Contract Specialist", source_url="https://sam.gov/opp/old/view")
        upsert_positions(db, [new], source="sam_notice_contacts", now=NOW)
        upsert_positions(db, [old], source="sam_notice_contacts", now=NOW)
        [row] = db.tables["gov_contact_positions"]
        self.assertEqual((row["raw_title"], row["source_url"], row["first_observed_at"][:10]),
                         ("Contracting Officer", "https://sam.gov/opp/new/view", "2025-01-05"))
        # A rerun of the same pages, in any order, writes nothing.
        for batch in ([old], [new], [old, new], [new, old]):
            self.assertEqual(upsert_positions(db, batch, source="sam_notice_contacts", now=NOW)["positions_updated"], 0)
        self.assertEqual(row["source_url"], "https://sam.gov/opp/new/view")


class MoveLedgerTests(unittest.TestCase):
    MOVE = {"person_identity_key": "fpds:jane.q.doe@navy.mil", "name": "Jane Q Doe", "agency_name": "Navy",
            "title": "Contracting Officer", "event_type": "transfer", "effective_date": "2026-03-01",
            "reported_at": NOW.isoformat(), "source_provider": "fpds_staff", "source_ref": "move:N00104>N00024",
            "source_url": "https://www.fpds.gov/", "date_basis": "observed", "evidence": [{"piid": "N0002426C0001"}]}

    def test_rerun_keeps_one_row_and_evidence_is_required(self) -> None:
        db = _Db(gov_contact_role_history=[])
        record_moves(db, [self.MOVE], now=NOW)
        record_moves(db, [self.MOVE], now=NOW)
        self.assertEqual(len(db.tables["gov_contact_role_history"]), 1)
        with self.assertRaises(ValueError):
            record_moves(db, [{**self.MOVE, "evidence": []}], now=NOW)


if __name__ == "__main__":
    unittest.main()

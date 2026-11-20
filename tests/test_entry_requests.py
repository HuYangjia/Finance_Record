import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import database, entry_requests, main


class EntryRequestsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="finance-entry-request-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        paths = patch.multiple(database, DATA_DIR=root, DATABASE_PATH=root / "finance.db",
                               BACKUP_DIR=root / "backups")
        paths.start()
        self.addCleanup(paths.stop)
        database.initialize_database()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        with database.connect() as c:
            self.accounts = []
            for index, code in enumerate(("HKD", "HKD", "USD")):
                self.accounts.append(c.execute("""INSERT INTO accounts
                    (code,name,account_class,account_type,currency_code,opening_balance_date)
                    VALUES (?,?,'asset','bank_demand',?,'2026-01-01')""",
                    (str(index), f"账户{index}", code)).lastrowid)
            self.categories = {kind: c.execute("SELECT id FROM categories WHERE kind=? LIMIT 1", (kind,)).fetchone()[0]
                               for kind in ("income", "expense")}
            self.budget = c.execute("SELECT id FROM budget_systems LIMIT 1").fetchone()[0]

    def payload(self, kind="expense", **overrides):
        body = dict(request_id=str(uuid4()), transaction_date="2026-11-20", note="重试测试")
        if kind in ("expense", "income"):
            body.update(amount="100", category_id=self.categories[kind])
            body["source_account_id" if kind == "expense" else "destination_account_id"] = self.accounts[0]
        else:
            body.update(source_account_id=self.accounts[0], destination_account_id=self.accounts[1], outflow_amount="100")
        return {**body, **overrides}

    def count(self, table):
        with database.connect() as c:
            return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def post(self, endpoint, body, code=201):
        response = self.client.post(endpoint, json=body)
        self.assertEqual(response.status_code, code, response.text)
        return response.json()

    def test_expense_income_transfer_and_fx_retries_return_original_result(self):
        cases = [("expense", {}), ("income", {}), ("transfer", {}),
                 ("transfer", dict(destination_account_id=self.accounts[2], inflow_amount="12.80"))]
        for kind, changes in cases:
            with self.subTest(kind=kind, changes=changes):
                endpoint = {"expense":"/api/expenses", "income":"/api/incomes", "transfer":"/api/transfers"}[kind]
                body = self.payload(kind, **changes)
                result = self.post(endpoint, body)
                before = self.client.get("/api/accounts").json()
                with patch("app.entry_requests.create_backup", side_effect=AssertionError("replay must not back up")):
                    replay = self.post(endpoint, body)
                self.assertTrue(replay.pop("replayed"))
                self.assertEqual(replay, result)
                self.assertEqual(before, self.client.get("/api/accounts").json())
        self.assertEqual(self.count("transactions"), 4)
        self.assertEqual(self.count("entry_requests"), 4)

    def test_same_key_different_body_or_operation_is_rejected(self):
        body = self.payload()
        self.post("/api/expenses", body)
        changed = self.post("/api/expenses", {**body, "amount":"101"}, 409)
        self.assertEqual(changed["detail"]["code"], "entry_request_conflict")
        self.post("/api/incomes", self.payload("income", request_id=body["request_id"]), 409)
        self.assertEqual(self.count("transactions"), 1)

    def test_new_keys_and_legacy_clients_allow_identical_real_entries(self):
        body = self.payload()
        first = self.post("/api/expenses", body)
        second = self.post("/api/expenses", {**body, "request_id":str(uuid4())})
        self.assertNotEqual(first["id"], second["id"])
        body.pop("request_id")
        self.post("/api/expenses", body)
        self.post("/api/expenses", body)
        self.assertEqual(self.count("transactions"), 4)
        self.assertEqual(self.count("entry_requests"), 2)

    def test_concurrent_same_key_only_writes_once(self):
        body = self.payload()
        gate = threading.Barrier(4)
        backup = entry_requests.create_backup

        def synchronized_backup():
            gate.wait(timeout=5)
            return backup()

        def submit(_):
            client = TestClient(main.app)  # No production lifespan or public network.
            try:
                response = client.post("/api/expenses", json=body)
                self.assertEqual(response.status_code, 201, response.text)
                return response.json()
            finally:
                client.close()

        with patch("app.entry_requests.create_backup", side_effect=synchronized_backup):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(submit, range(4)))
        self.assertEqual(len({r["id"] for r in results}), 1)
        self.assertEqual(sum(r.get("replayed", False) for r in results), 3)
        self.assertEqual(self.count("transactions"), 1)
        self.assertEqual(self.count("entry_requests"), 1)

    def test_income_allocation_failure_rolls_back_then_retry_can_be_corrected(self):
        body = self.payload("income", budget_allocations=[dict(budget_system_id=self.budget, amount="150")])
        self.post("/api/incomes", body, 422)
        for table in ("transactions", "entry_requests", "budget_income_allocations", "budget_plans"):
            self.assertEqual(self.count(table), 0, table)
        body["budget_allocations"][0]["amount"] = "100"
        result = self.post("/api/incomes", body)
        self.assertEqual(self.post("/api/incomes", body)["id"], result["id"])
        self.assertEqual(self.count("budget_income_allocations"), 1)

    def test_receipt_failure_rolls_back_ledger_and_budget_together(self):
        body = self.payload("income", budget_allocations=[dict(budget_system_id=self.budget, amount="100")])
        with database.connect() as c:
            c.execute("""CREATE TRIGGER reject_receipt BEFORE INSERT ON entry_requests
                BEGIN SELECT RAISE(ABORT, 'receipt failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "receipt failure"):
            self.client.post("/api/incomes", json=body)
        for table in ("transactions", "entry_requests", "budget_income_allocations", "budget_plans", "change_audit_logs"):
            self.assertEqual(self.count(table), 0, table)
        with database.connect() as c:
            c.execute("DROP TRIGGER reject_receipt")
        self.post("/api/incomes", body)
        self.assertEqual(self.count("transactions"), 1)

    def test_receipt_survives_restart_and_deletion_does_not_recreate_entry(self):
        body = self.payload()
        result = self.post("/api/expenses", body)
        database.initialize_database()
        with database.connect() as c:
            c.execute("DELETE FROM transactions WHERE id=?", (result["id"],))
        replay = self.post("/api/expenses", body)
        self.assertEqual(replay["id"], result["id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.count("transactions"), 0)

    def test_upgrade_backup_precedes_new_table_and_is_idempotent(self):
        body = self.payload()
        body.pop("request_id")
        self.post("/api/expenses", body)
        with database.connect() as c:
            c.execute("DROP TABLE entry_requests")
        database.initialize_database()
        database.initialize_database()
        backups = list(database.BACKUP_DIR.glob("finance-pre-entry-requests-*.db"))
        self.assertEqual(len(backups), 1)
        with database.open_connection(backups[0]) as c:
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='entry_requests'").fetchone())
            self.assertEqual(c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        self.assertEqual(self.count("transactions"), 1)
        self.assertEqual(self.count("entry_requests"), 0)

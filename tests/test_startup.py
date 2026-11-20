"""Startup must remain usable when the optional exchange-rate service stalls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import database, main


class StartupTest(unittest.TestCase):
    day = date(2026, 11, 20)

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="finance-startup-test-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        paths = patch.multiple(database, DATA_DIR=root, DATABASE_PATH=root / "finance.db",
                               BACKUP_DIR=root / "backups")
        paths.start()
        self.addCleanup(paths.stop)
        today = patch("app.main.today_in_shanghai", return_value=self.day)
        today.start()
        self.addCleanup(today.stop)
        database.initialize_database()
        with database.connect() as connection:
            self.account = connection.execute("""INSERT INTO accounts
                (code,name,account_class,account_type,currency_code,opening_balance_date)
                VALUES ('STARTUP','启动测试','asset','bank_demand','CNY','2026-11-01')""").lastrowid
            self.category = connection.execute(
                "SELECT id FROM categories WHERE kind='expense' AND name='餐饮'"
            ).fetchone()[0]
        self.sync_done = threading.Event()
        self.sync_failed = threading.Event()
        logging = patch("app.main.logger")
        self.logger = logging.start()
        self.addCleanup(logging.stop)
        self.logger.info.side_effect = lambda message, *args: (
            self.sync_done.set() if message.startswith("后台汇率同步完成") else None
        )
        self.logger.warning.side_effect = lambda *args: self.sync_failed.set()

    def fetched(self):
        return dict(usd_cny_rate=Decimal("7.2"), hkd_cny_rate=Decimal("0.92"),
                    source="test", market_date=self.day.isoformat())

    def cache(self, source="manual"):
        with database.connect() as connection:
            main.upsert_daily_cny_exchange_rate(
                connection, rate_date=self.day.isoformat(), usd_cny_rate=Decimal("7.1"),
                hkd_cny_rate=Decimal("0.91"), source=source, market_date=self.day.isoformat(),
            )

    def delayed_fetch(self):
        started, release, finished = (threading.Event() for _ in range(3))

        def fetch(_):
            started.set()
            try:
                # Bounded only so a regressed synchronous startup cannot hang the suite.
                if not release.wait(5):
                    raise RuntimeError("test fetch was not released")
                return self.fetched()
            finally:
                finished.set()

        def finish_worker():
            release.set()
            if started.is_set():
                self.assertTrue(finished.wait(3))

        self.addCleanup(finish_worker)
        return fetch, started, release, finished

    def test_slow_network_does_not_block_recording_or_shutdown(self):
        fetch, started, release, finished = self.delayed_fetch()
        with patch("app.main.fetch_public_cny_rates", side_effect=fetch):
            with TestClient(main.app) as client:
                self.assertTrue(started.wait(3))
                self.assertFalse(finished.is_set(), "startup waited for the network")
                self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
                self.assertEqual(client.get("/").status_code, 200)
                response = client.post("/api/expenses", json=dict(
                    transaction_date=self.day.isoformat(), source_account_id=self.account,
                    category_id=self.category, amount="12.34",
                ))
                self.assertEqual(response.status_code, 201, response.text)
                accounts = client.get("/api/accounts").json()
                self.assertEqual(next(a for a in accounts if a["id"] == self.account)["balance"], "-12.34")
                self.assertIsNone(main.cached_cny_exchange_rate(self.day.isoformat()))
            self.assertFalse(finished.is_set(), "shutdown waited for the network")
            release.set()
            self.assertTrue(finished.wait(3))
        self.assertFalse(self.sync_done.is_set())
        self.assertIsNone(main.cached_cny_exchange_rate(self.day.isoformat()))

    def test_background_success_saves_rates(self):
        with patch("app.main.fetch_public_cny_rates", return_value=self.fetched()) as fetch:
            with TestClient(main.app) as client:
                self.assertTrue(self.sync_done.wait(3))
                row = client.get("/api/fx-rates").json()[0]
                self.assertEqual(row["usd_cny_rate"], "7.2000")
                self.assertEqual(row["source"], "test")
            fetch.assert_called_once_with(self.day)

    def test_background_failure_preserves_cached_history_and_service(self):
        with database.connect() as connection:
            main.upsert_daily_cny_exchange_rate(
                connection, rate_date="2026-11-19", usd_cny_rate=Decimal("7.1"),
                hkd_cny_rate=Decimal("0.91"), source="manual", market_date="2026-11-19",
            )
        with patch("app.main.fetch_public_cny_rates", side_effect=RuntimeError("offline")):
            with TestClient(main.app) as client:
                self.assertTrue(self.sync_failed.wait(3))
                self.assertEqual(client.get("/api/health").status_code, 200)
                rows = client.get("/api/fx-rates").json()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["rate_date"], "2026-11-19")
                self.assertEqual(rows[0]["usd_cny_rate"], "7.1000")

    def test_existing_today_rate_skips_network(self):
        self.cache()
        with patch("app.main.fetch_public_cny_rates") as fetch:
            with TestClient(main.app) as client:
                self.assertEqual(client.get("/api/fx-rates").json()[0]["source"], "manual")
            fetch.assert_not_called()

    def test_background_result_preserves_manual_rate_saved_while_waiting(self):
        fetch, started, release, _ = self.delayed_fetch()
        with patch("app.main.fetch_public_cny_rates", side_effect=fetch):
            with TestClient(main.app) as client:
                self.assertTrue(started.wait(3))
                response = client.put(f"/api/fx-rates/{self.day.isoformat()}", json=dict(
                    usd_cny_rate="7.1", hkd_cny_rate="0.91", note="人工更正",
                ))
                self.assertEqual(response.status_code, 200, response.text)
                release.set()
                self.assertTrue(self.sync_done.wait(3))
                row = client.get("/api/fx-rates").json()[0]
                self.assertEqual(row["source"], "manual")
                self.assertEqual(row["usd_cny_rate"], "7.1000")
                self.assertEqual(row["note"], "人工更正")

    def test_only_explicit_force_overwrites_existing_rates(self):
        self.cache()
        with patch("app.main.fetch_public_cny_rates", return_value=self.fetched()) as fetch:
            result = main.sync_daily_cny_exchange_rates(self.day)
            self.assertEqual(result["source"], "manual")
            fetch.assert_not_called()
            result = main.sync_daily_cny_exchange_rates(self.day, force=True)
            self.assertEqual(result["source"], "test")
            self.assertEqual(result["usd_cny_rate"], "7.2000")
            fetch.assert_called_once_with(self.day)

    def test_database_failure_still_prevents_startup(self):
        with patch("app.main.initialize_database", side_effect=RuntimeError("migration failed")):
            with patch("app.main.fetch_public_cny_rates") as fetch:
                with self.assertRaisesRegex(RuntimeError, "migration failed"):
                    with TestClient(main.app):
                        self.fail("must not serve an uninitialized database")
                fetch.assert_not_called()


class StartupImportTest(unittest.TestCase):
    def test_application_import_does_not_load_excel_library(self):
        result = subprocess.run(
            [sys.executable, "-c", "import sys; import app.main; assert 'openpyxl' not in sys.modules"],
            cwd=database.ROOT_DIR, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database, main


class DatabaseLifecycleTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="finance-connection-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        paths = patch.multiple(database, DATA_DIR=root, DATABASE_PATH=root / "finance.db",
                               BACKUP_DIR=root / "backups")
        paths.start()
        self.addCleanup(paths.stop)

    def assert_closed(self, connection):
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_context_commits_or_rolls_back_and_always_closes(self):
        database.initialize_database()
        with database.connect() as connection:
            connection.execute("INSERT INTO app_settings(key,value) VALUES ('lifecycle','saved')")
        self.assert_closed(connection)
        with self.assertRaisesRegex(ValueError, "cancel"):
            with database.connect() as failed:
                failed.execute("UPDATE app_settings SET value='wrong' WHERE key='lifecycle'")
                raise ValueError("cancel")
        self.assert_closed(failed)
        with database.connect() as check:
            self.assertEqual(check.execute("SELECT value FROM app_settings WHERE key='lifecycle'").fetchone()[0], "saved")

    def test_commit_failure_rolls_back_and_closes(self):
        database.initialize_database()
        with self.assertRaises(sqlite3.IntegrityError):
            with database.connect() as connection:
                connection.execute("PRAGMA defer_foreign_keys=ON")
                connection.execute("INSERT INTO categories(kind,name,parent_id) VALUES ('expense','invalid parent',999999)")
        self.assert_closed(connection)
        with database.connect() as check:
            self.assertIsNone(check.execute("SELECT 1 FROM categories WHERE name='invalid parent'").fetchone())

    def test_initialization_and_backup_close_all_connections(self):
        opened = []
        original = database.open_connection

        def track(*args, **kwargs):
            connection = original(*args, **kwargs)
            opened.append(connection)
            return connection

        with patch.object(database, "open_connection", side_effect=track):
            database.initialize_database()
            database.initialize_database()
            database.create_backup()
        self.assertGreaterEqual(len(opened), 6)
        for connection in opened:
            self.assert_closed(connection)

    def test_backup_preserves_committed_data_without_initializing(self):
        database.initialize_database()
        with database.connect() as connection:
            connection.execute("INSERT INTO app_settings(key,value) VALUES ('backup-check','saved')")
        with patch.object(database, "initialize_database", side_effect=AssertionError("must not migrate")):
            path = database.create_backup()
        with database.open_connection(path) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT value FROM app_settings WHERE key='backup-check'").fetchone()[0], "saved")

    def test_backup_missing_source_never_creates_empty_ledger(self):
        with self.assertRaises(sqlite3.OperationalError):
            database.create_backup()
        self.assertFalse(database.DATABASE_PATH.exists())
        self.assertEqual(list(database.BACKUP_DIR.glob("*.db")), [])

    def test_backup_failure_prevents_business_write(self):
        database.initialize_database()
        with patch("app.main.create_backup", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                main.create_expense(main.ExpenseCreate(source_account_id=1, category_id=1, amount="1"))
        with database.connect() as check:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)

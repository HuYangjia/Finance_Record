import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import database
from app.main import list_accounts


class AccountDirectoryMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.original = (
            database.DATA_DIR,
            database.DATABASE_PATH,
            database.BACKUP_DIR,
        )
        database.DATA_DIR = root / "data"
        database.DATABASE_PATH = database.DATA_DIR / "finance.db"
        database.BACKUP_DIR = root / "backups"
        database.DATA_DIR.mkdir(parents=True)

    def tearDown(self):
        database.DATA_DIR, database.DATABASE_PATH, database.BACKUP_DIR = self.original
        self.temporary_directory.cleanup()

    def create_legacy_database(self):
        with sqlite3.connect(database.DATABASE_PATH) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE currencies (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    symbol TEXT,
                    decimal_places INTEGER NOT NULL DEFAULT 2,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    account_class TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    currency_code TEXT NOT NULL REFERENCES currencies(code),
                    institution TEXT,
                    opening_balance_minor INTEGER NOT NULL DEFAULT 0,
                    opening_balance_date TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    note TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO currencies(code,name) VALUES ('CNY','人民币'),('HKD','港币');
                """
            )
            connection.executemany(
                """
                INSERT INTO accounts(
                    code,name,account_class,account_type,currency_code,
                    institution,opening_balance_date
                ) VALUES (?,?,?,?,?,?, '2026-01-01')
                """,
                [
                    ("CCB", "建设银行", "asset", "bank_demand", "CNY", "中国建设银行"),
                    ("HSBC", "汇丰银行", "asset", "bank_demand", "HKD", "汇丰银行"),
                    ("INVEST", "投资", "asset", "investment_asset", "HKD", "汇丰银行"),
                    ("WALLET", "钱包", "asset", "e_wallet", "CNY", "平台"),
                    ("OCTOPUS", "八达通", "asset", "transit_card", "HKD", "八达通"),
                    ("DEBT", "贷款", "liability", "student_loan", "CNY", "学校"),
                    ("UNKNOWN", "未知银行", "asset", "bank_demand", "CNY", "其他银行"),
                ],
            )

    def test_legacy_accounts_are_grouped_backed_up_and_not_overwritten(self):
        self.create_legacy_database()
        database.initialize_database()

        accounts = {row["code"]: row for row in list_accounts()}
        self.assertEqual(accounts["CCB"]["directory_key"], "domestic_bank")
        self.assertEqual(accounts["HSBC"]["directory_key"], "overseas_bank")
        self.assertEqual(accounts["INVEST"]["directory_key"], "investment")
        self.assertEqual(accounts["WALLET"]["directory_key"], "cash_wallet")
        self.assertEqual(accounts["OCTOPUS"]["directory_key"], "stored_value")
        self.assertEqual(accounts["DEBT"]["directory_key"], "liability")
        self.assertEqual(accounts["UNKNOWN"]["directory_key"], "other")
        self.assertEqual(accounts["HSBC"]["directory_name"], "香港及境外银行账户")
        self.assertEqual(accounts["HSBC"]["directory_sort_order"], 20)
        self.assertEqual(len(list(
            database.BACKUP_DIR.glob("finance-pre-account-directories-*.db")
        )), 1)

        with database.connect() as connection:
            connection.execute(
                "UPDATE accounts SET directory_key='domestic_bank' WHERE code='UNKNOWN'"
            )
        database.initialize_database()
        accounts = {row["code"]: row for row in list_accounts()}
        self.assertEqual(accounts["UNKNOWN"]["directory_key"], "domestic_bank")
        self.assertEqual(len(list(
            database.BACKUP_DIR.glob("finance-pre-account-directories-*.db")
        )), 1)
        with database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()

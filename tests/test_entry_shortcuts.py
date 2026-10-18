import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app import database
from app.entry_shortcuts import (
    ShortcutOrderWrite,
    ShortcutWrite,
    create_entry_shortcut,
    delete_entry_shortcut,
    get_entry_shortcuts,
    reorder_entry_shortcuts,
    update_entry_shortcut,
)


class EntryShortcutTest(unittest.TestCase):
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
        database.initialize_database()
        with database.connect() as connection:
            self.source = connection.execute(
                """
                INSERT INTO accounts(
                    code,name,account_class,account_type,currency_code,opening_balance_date
                ) VALUES ('SOURCE','来源账户','asset','bank_demand','HKD','2026-01-01')
                """
            ).lastrowid
            self.destination = connection.execute(
                """
                INSERT INTO accounts(
                    code,name,account_class,account_type,currency_code,opening_balance_date
                ) VALUES ('DESTINATION','去向账户','asset','bank_demand','HKD','2026-01-01')
                """
            ).lastrowid
            self.usd = connection.execute(
                """
                INSERT INTO accounts(
                    code,name,account_class,account_type,currency_code,opening_balance_date
                ) VALUES ('USD','美元账户','asset','bank_demand','USD','2026-01-01')
                """
            ).lastrowid
            self.expense_category = connection.execute(
                "SELECT id FROM categories WHERE kind='expense' AND name='餐饮'"
            ).fetchone()[0]
            self.income_category = connection.execute(
                "SELECT id FROM categories WHERE kind='income' AND name='工资/劳务'"
            ).fetchone()[0]
            self.budget = connection.execute(
                "SELECT id FROM budget_systems WHERE name='日常预算'"
            ).fetchone()[0]

    def tearDown(self):
        database.DATA_DIR, database.DATABASE_PATH, database.BACKUP_DIR = self.original
        self.temporary_directory.cleanup()

    def write(self, **overrides):
        values = dict(
            expected_version=get_entry_shortcuts()["version"],
            name="餐饮按钮",
            mode="expense",
            source_account_id=self.source,
            category_id=self.expense_category,
            budget_system_id=self.budget,
            outflow_amount="12.34",
            counterparty="餐厅",
            note="工作餐",
        )
        values.update(overrides)
        return ShortcutWrite(**values)

    def test_crud_order_validation_backup_and_audit(self):
        created = create_entry_shortcut(self.write())
        self.assertEqual(created["version"], 2)
        expense = created["shortcuts"][0]
        self.assertEqual(expense["outflow_amount"], "12.34")
        self.assertEqual(expense["source_currency_code"], "HKD")
        self.assertTrue(expense["valid"])

        transfer = create_entry_shortcut(self.write(
            expected_version=2,
            name="账户转账",
            mode="transfer",
            source_account_id=self.source,
            destination_account_id=self.usd,
            category_id=None,
            budget_system_id=None,
            outflow_amount="20",
            inflow_amount=None,
            counterparty=None,
            note="换汇",
        ))
        self.assertEqual(transfer["version"], 3)
        transfer_row = next(row for row in transfer["shortcuts"] if row["mode"] == "transfer")
        self.assertEqual(transfer_row["outflow_amount"], "20.00")
        self.assertIsNone(transfer_row["inflow_amount"])

        reordered = reorder_entry_shortcuts(ShortcutOrderWrite(
            expected_version=3,
            ids=[transfer_row["id"], expense["id"]],
        ))
        self.assertEqual([row["id"] for row in reordered["shortcuts"]],
                         [transfer_row["id"], expense["id"]])

        updated = update_entry_shortcut(expense["id"], self.write(
            expected_version=4,
            name="餐饮快速记账",
            is_active=False,
        ))
        updated_expense = next(row for row in updated["shortcuts"] if row["id"] == expense["id"])
        self.assertFalse(updated_expense["is_active"])

        final = delete_entry_shortcut(transfer_row["id"], expected_version=5)
        self.assertEqual([row["id"] for row in final["shortcuts"]], [expense["id"]])
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM change_audit_logs WHERE entity_type='entry_shortcut'"
            ).fetchone()[0], 5)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(len(list(database.BACKUP_DIR.glob("finance-*.db"))), 5)

    def test_rejects_stale_wrong_category_and_same_currency_mismatch(self):
        create_entry_shortcut(self.write())
        with self.assertRaises(HTTPException) as stale:
            create_entry_shortcut(self.write(expected_version=1, name="旧页面"))
        self.assertEqual(stale.exception.status_code, 409)

        with self.assertRaises(HTTPException) as category_error:
            create_entry_shortcut(self.write(
                expected_version=2,
                name="错分类",
                category_id=self.income_category,
            ))
        self.assertEqual(category_error.exception.status_code, 422)

        with self.assertRaises(HTTPException) as amount_error:
            create_entry_shortcut(self.write(
                expected_version=2,
                name="错误转账",
                mode="transfer",
                source_account_id=self.source,
                destination_account_id=self.destination,
                category_id=None,
                budget_system_id=None,
                outflow_amount="10",
                inflow_amount="9",
                counterparty=None,
            ))
        self.assertEqual(amount_error.exception.status_code, 422)
        self.assertEqual(get_entry_shortcuts()["version"], 2)

    def test_shortcut_is_disabled_when_category_becomes_a_parent(self):
        created = create_entry_shortcut(self.write())
        shortcut_id = created["shortcuts"][0]["id"]
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO categories(kind,parent_id,name) VALUES ('expense',?,'工作餐')",
                (self.expense_category,),
            )

        shortcut = next(
            row for row in get_entry_shortcuts()["shortcuts"]
            if row["id"] == shortcut_id
        )
        self.assertFalse(shortcut["valid"])
        self.assertIn("末级分类", shortcut["invalid_reason"])

    def test_existing_installation_seeds_legacy_buttons_once(self):
        with database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO accounts(
                    code,name,account_class,account_type,currency_code,opening_balance_date
                ) VALUES (?,?,'asset','bank_demand','CNY','2026-01-01')
                """,
                [
                    ("OCTOPUS-STUDENT", "学生八达通"),
                    ("WECHAT", "微信"),
                    ("ALIPAY", "支付宝"),
                    ("ALIPAY-FUND", "支付宝基金"),
                ],
            )
            connection.execute("DROP TABLE transaction_entry_shortcuts")
            connection.execute("DELETE FROM app_settings WHERE key IN ('entry_shortcut_version','entry_shortcuts_initialized')")

        database.initialize_database()
        seeded = get_entry_shortcuts()
        self.assertEqual(
            [row["name"] for row in seeded["shortcuts"]],
            ["八达通餐饮", "微信转八达通", "支付宝定投 35"],
        )
        self.assertEqual(seeded["shortcuts"][2]["outflow_amount"], "35.00")
        self.assertEqual(len(list(database.BACKUP_DIR.glob("finance-pre-entry-shortcuts-*.db"))), 1)
        database.initialize_database()
        self.assertEqual(len(get_entry_shortcuts()["shortcuts"]), 3)
        self.assertEqual(len(list(database.BACKUP_DIR.glob("finance-pre-entry-shortcuts-*.db"))), 1)


if __name__ == "__main__":
    unittest.main()

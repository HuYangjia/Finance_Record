import tempfile
import unittest
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from app import database
from app.main import (
    BalanceAdjustmentCreate,
    DailyCnyRateUpdate,
    ExpenseCreate,
    IncomeCreate,
    InvestmentValuationUpsert,
    OpeningBalanceUpdate,
    TransactionDelete,
    TransactionFlagUpdate,
    TransferCreate,
    TransactionUpdate,
    build_excel_export,
    account_detail,
    create_balance_adjustment,
    create_income,
    create_expense,
    create_transfer,
    dashboard,
    delete_investment_valuation,
    delete_transaction,
    get_transaction,
    list_accounts,
    list_categories,
    list_transactions,
    investment_monthly,
    investment_performance,
    search_transactions,
    sync_daily_cny_exchange_rates,
    transaction_analysis,
    update_daily_cny_exchange_rate,
    update_opening_balance,
    update_transaction,
    upsert_investment_valuation,
    upsert_transaction_flag,
)


class FinanceAppSmokeTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.original_data_dir = database.DATA_DIR
        self.original_database_path = database.DATABASE_PATH
        self.original_backup_dir = database.BACKUP_DIR
        database.DATA_DIR = root / "data"
        database.DATABASE_PATH = database.DATA_DIR / "finance.db"
        database.BACKUP_DIR = root / "backups"
        database.initialize_database()

        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO accounts (
                    code, name, account_class, account_type, currency_code,
                    opening_balance_date
                ) VALUES ('CNY-BANK-01', '人民币银行卡', 'asset', 'bank_demand', 'CNY', '2026-01-01')
                """
            )
            connection.execute(
                """
                INSERT INTO accounts (
                    code, name, account_class, account_type, currency_code,
                    opening_balance_date
                ) VALUES ('CNY-CARD-01', '人民币信用卡', 'liability', 'credit_card', 'CNY', '2026-01-01')
                """
            )
            connection.execute(
                """
                INSERT INTO accounts (
                    code, name, account_class, account_type, currency_code,
                    opening_balance_date
                ) VALUES ('CNY-CASH', '人民币现金', 'asset', 'cash', 'CNY', '2026-01-01')
                """
            )
            connection.execute(
                """
                INSERT INTO accounts (
                    code, name, account_class, account_type, currency_code,
                    opening_balance_minor, opening_balance_date
                ) VALUES (
                    'CNY-INVEST-01', '人民币投资账户', 'asset', 'investment_asset', 'CNY',
                    500000, '2026-01-01'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO accounts (
                    code, name, account_class, account_type, currency_code,
                    opening_balance_date
                ) VALUES ('HKD-BANK-01', '港币银行卡', 'asset', 'bank_demand', 'HKD', '2026-01-01')
                """
            )
            connection.execute(
                """
                INSERT INTO accounts (
                    code, name, account_class, account_type, currency_code,
                    opening_balance_minor, opening_balance_date
                ) VALUES (
                    'USD-BANK-01', '美元银行卡', 'asset', 'bank_demand', 'USD',
                    10000, '2026-01-01'
                )
                """
            )
            self.food_category_id = connection.execute(
                "SELECT id FROM categories WHERE kind = 'expense' AND name = '餐饮'"
            ).fetchone()["id"]
            self.salary_category_id = connection.execute(
                "SELECT id FROM categories WHERE kind = 'income' AND name = '工资/劳务'"
            ).fetchone()["id"]
            self.payroll_expense_category_id = connection.execute(
                """
                SELECT id
                FROM categories
                WHERE kind = 'expense' AND name = '工资/劳务支出'
                """
            ).fetchone()["id"]

    def tearDown(self):
        database.DATA_DIR = self.original_data_dir
        database.DATABASE_PATH = self.original_database_path
        database.BACKUP_DIR = self.original_backup_dir
        self.temporary_directory.cleanup()

    def test_expense_updates_balance_and_exports_excel(self):
        accounts = list_accounts()
        bank_account = next(account for account in accounts if account["code"] == "CNY-BANK-01")

        response = create_expense(
            ExpenseCreate(
                transaction_date="2026-09-02",
                source_account_id=bank_account["id"],
                amount="12.34",
                category_id=self.food_category_id,
                counterparty="餐厅",
            )
        )
        self.assertEqual(response["amount"], "12.34")

        accounts = list_accounts()
        bank_account = next(account for account in accounts if account["code"] == "CNY-BANK-01")
        self.assertEqual(bank_account["balance"], "-12.34")

        dashboard_data = dashboard("2026-09")
        self.assertEqual(dashboard_data["cash_flow"][0]["expense"], "12.34")
        available_cash = {
            item["currency_code"]: item["available_cash"]
            for item in dashboard_data["available_cash"]
        }
        self.assertEqual(available_cash["CNY"], "-12.34")

        workbook = load_workbook(filename=build_excel_export("2026-09"))
        self.assertIn("月度总览", workbook.sheetnames)
        self.assertIn("账户内流动", workbook.sheetnames)
        self.assertEqual(workbook["月度总览"]["E3"].value, "可用现金")

    def test_default_expense_categories_include_food_groceries_and_payroll(self):
        with database.connect() as connection:
            food_category = connection.execute(
                """
                SELECT sort_order
                FROM categories
                WHERE kind = 'expense' AND name = '食品杂货'
                """
            ).fetchone()
            payroll_category = connection.execute(
                """
                SELECT sort_order
                FROM categories
                WHERE kind = 'expense' AND name = '工资/劳务支出'
                """
            ).fetchone()

        self.assertIsNotNone(food_category)
        self.assertEqual(food_category["sort_order"], 25)
        self.assertIsNotNone(payroll_category)
        self.assertEqual(payroll_category["sort_order"], 95)

    def test_daily_snapshot_drives_account_and_category_common_data(self):
        with database.connect() as connection:
            accounts = {
                row["code"]: {"id": row["id"]}
                for row in connection.execute("SELECT id, code FROM accounts")
            }

        with patch("app.main.today_in_shanghai", return_value=date(2026, 9, 8)):
            create_expense(
                ExpenseCreate(
                    transaction_date="2026-09-07",
                    source_account_id=accounts["CNY-BANK-01"]["id"],
                    amount="10",
                    category_id=self.food_category_id,
                )
            )
            create_expense(
                ExpenseCreate(
                    transaction_date="2026-09-08",
                    source_account_id=accounts["CNY-BANK-01"]["id"],
                    amount="12",
                    category_id=self.food_category_id,
                )
            )
            create_expense(
                ExpenseCreate(
                    transaction_date="2026-09-08",
                    source_account_id=accounts["HKD-BANK-01"]["id"],
                    amount="20",
                    category_id=self.food_category_id,
                )
            )
            create_income(
                IncomeCreate(
                    transaction_date="2026-09-08",
                    destination_account_id=accounts["CNY-BANK-01"]["id"],
                    amount="100",
                    category_id=self.salary_category_id,
                )
            )
            create_transfer(
                TransferCreate(
                    transaction_date="2026-09-08",
                    source_account_id=accounts["CNY-BANK-01"]["id"],
                    destination_account_id=accounts["CNY-CASH"]["id"],
                    outflow_amount="30",
                )
            )

            account_activity = {
                account["code"]: account["activity"]
                for account in list_accounts()
            }
            self.assertEqual(
                account_activity["CNY-BANK-01"]["expense_source"],
                {"record_count": 2, "last_transaction_date": "2026-09-08"},
            )
            self.assertEqual(
                account_activity["CNY-BANK-01"]["income_destination"],
                {"record_count": 1, "last_transaction_date": "2026-09-08"},
            )
            self.assertEqual(
                account_activity["CNY-BANK-01"]["movement_source"],
                {"record_count": 1, "last_transaction_date": "2026-09-08"},
            )
            self.assertEqual(
                account_activity["CNY-CASH"]["movement_destination"],
                {"record_count": 1, "last_transaction_date": "2026-09-08"},
            )

            categories = {
                category["name"]: category["activity"]
                for category in list_categories(kind="expense")
            }
            self.assertEqual(
                categories["餐饮"],
                {"record_count": 3, "last_transaction_date": "2026-09-08"},
            )

            create_expense(
                ExpenseCreate(
                    transaction_date="2026-09-08",
                    source_account_id=accounts["CNY-CASH"]["id"],
                    amount="8",
                    category_id=self.food_category_id,
                )
            )
            frozen_accounts = {
                account["code"]: account["activity"]
                for account in list_accounts()
            }
            frozen_categories = {
                category["name"]: category["activity"]
                for category in list_categories(kind="expense")
            }
            self.assertEqual(
                frozen_accounts["CNY-CASH"]["expense_source"],
                {"record_count": 0, "last_transaction_date": None},
            )
            self.assertEqual(
                frozen_categories["餐饮"],
                {"record_count": 3, "last_transaction_date": "2026-09-08"},
            )

        with patch("app.main.today_in_shanghai", return_value=date(2026, 9, 9)):
            next_day_accounts = {
                account["code"]: account["activity"]
                for account in list_accounts()
            }
            next_day_categories = {
                category["name"]: category["activity"]
                for category in list_categories(kind="expense")
            }
        self.assertEqual(
            next_day_accounts["CNY-CASH"]["expense_source"],
            {"record_count": 1, "last_transaction_date": "2026-09-08"},
        )
        self.assertEqual(
            next_day_categories["餐饮"],
            {"record_count": 4, "last_transaction_date": "2026-09-08"},
        )
        with database.connect() as connection:
            snapshot_count = connection.execute(
                "SELECT COUNT(*) AS count FROM daily_frequent_snapshots"
            ).fetchone()["count"]
        self.assertEqual(snapshot_count, 2)

    def test_payroll_expense_category_creates_expense(self):
        accounts = {account["code"]: account for account in list_accounts()}
        response = create_expense(
            ExpenseCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                amount="500",
                category_id=self.payroll_expense_category_id,
                counterparty="兼职助理",
                note="2026 年 9 月劳务费",
            )
        )

        self.assertEqual(response["amount"], "500.00")
        result = search_transactions(
            start_date="2026-09-02",
            end_date="2026-09-02",
            category_ids=[self.payroll_expense_category_id],
        )
        self.assertEqual(result["pagination"]["total"], 1)
        self.assertEqual(result["transactions"][0]["category_name"], "工资/劳务支出")

    def test_liability_expense_increases_debt(self):
        accounts = list_accounts()
        card_account = next(account for account in accounts if account["code"] == "CNY-CARD-01")

        response = create_expense(
            ExpenseCreate(
                transaction_date="2026-09-02",
                source_account_id=card_account["id"],
                amount="50",
                category_id=self.food_category_id,
            )
        )
        self.assertEqual(response["amount"], "50.00")

        accounts = list_accounts()
        card_account = next(account for account in accounts if account["code"] == "CNY-CARD-01")
        self.assertEqual(card_account["balance"], "50.00")

    def test_income_transfer_and_exchange_update_accounts(self):
        accounts = {account["code"]: account for account in list_accounts()}

        income = create_income(
            IncomeCreate(
                transaction_date="2026-09-02",
                destination_account_id=accounts["CNY-BANK-01"]["id"],
                amount="100",
                category_id=self.salary_category_id,
                counterparty="劳务平台",
            )
        )
        self.assertEqual(income["amount"], "100.00")

        transfer = create_transfer(
            TransferCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                destination_account_id=accounts["CNY-CASH"]["id"],
                outflow_amount="30",
            )
        )
        self.assertEqual(transfer["transaction_type"], "transfer")
        self.assertEqual(transfer["inflow_amount"], "30.00")

        exchange = create_transfer(
            TransferCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["CNY-CASH"]["id"],
                destination_account_id=accounts["HKD-BANK-01"]["id"],
                outflow_amount="10",
                inflow_amount="11.20",
            )
        )
        self.assertEqual(exchange["transaction_type"], "fx_exchange")
        self.assertEqual(exchange["inflow_amount"], "11.20")

        balances = {account["code"]: account["balance"] for account in list_accounts()}
        self.assertEqual(balances["CNY-BANK-01"], "70.00")
        self.assertEqual(balances["CNY-CASH"], "20.00")
        self.assertEqual(balances["HKD-BANK-01"], "11.20")

        transaction_types = {
            transaction["transaction_type"] for transaction in list_transactions(limit=20)
        }
        self.assertEqual(transaction_types, {"income", "transfer", "fx_exchange"})
        dashboard_data = dashboard("2026-09")
        self.assertEqual(dashboard_data["cash_flow"][0]["income"], "100.00")
        self.assertEqual(dashboard_data["cash_flow"][0]["expense"], "0.00")
        available_cash = {
            item["currency_code"]: item["available_cash"]
            for item in dashboard_data["available_cash"]
        }
        self.assertEqual(available_cash["CNY"], "90.00")
        self.assertEqual(available_cash["HKD"], "11.20")

        detail = account_detail(
            accounts["CNY-BANK-01"]["id"],
            start_date="2026-09-02",
            end_date="2026-09-02",
        )
        self.assertEqual(detail["account"]["current_balance"], "70.00")
        self.assertEqual(detail["account"]["total_inflow"], "100.00")
        self.assertEqual(detail["account"]["total_outflow"], "30.00")
        self.assertEqual(detail["pagination"]["total"], 2)
        self.assertEqual(detail["transactions"][0]["balance_after"], "70.00")
        self.assertEqual(detail["transactions"][0]["direction"], "转出")
        self.assertEqual(detail["transactions"][0]["counter_account_name"], "人民币现金")

        income_detail = account_detail(
            accounts["CNY-BANK-01"]["id"],
            category_id=self.salary_category_id,
            keyword="劳务",
        )
        self.assertEqual(income_detail["pagination"]["total"], 1)
        self.assertEqual(income_detail["transactions"][0]["direction"], "入账")
        self.assertEqual(income_detail["transactions"][0]["balance_after"], "100.00")

        transfer_detail = account_detail(
            accounts["CNY-BANK-01"]["id"],
            transaction_type="transfer",
        )
        self.assertEqual(transfer_detail["pagination"]["total"], 1)
        self.assertEqual(transfer_detail["transactions"][0]["outflow_amount"], "30.00")

        multi_type_search = search_transactions(
            start_date="2026-09-02",
            end_date="2026-09-02",
            transaction_types=["income", "transfer"],
        )
        self.assertEqual(multi_type_search["pagination"]["total"], 2)
        self.assertEqual(
            {item["transaction_type"] for item in multi_type_search["transactions"]},
            {"income", "transfer"},
        )

        category_search = search_transactions(
            category_ids=[self.salary_category_id],
            keyword="劳务",
        )
        self.assertEqual(category_search["pagination"]["total"], 1)
        self.assertEqual(category_search["transactions"][0]["transaction_type"], "income")

        movement_summary = {
            item["currency_code"]: item
            for item in dashboard_data["internal_movement_summary"]
        }
        self.assertEqual(movement_summary["CNY"]["outflow"], "40.00")
        self.assertEqual(movement_summary["CNY"]["inflow"], "30.00")
        self.assertEqual(movement_summary["CNY"]["transaction_count"], 2)
        self.assertEqual(movement_summary["HKD"]["inflow"], "11.20")
        self.assertEqual(
            {item["transaction_type"] for item in dashboard_data["internal_movements"]},
            {"transfer", "fx_exchange"},
        )

    def test_transaction_search_summary_uses_native_and_base_currencies(self):
        accounts = {account["code"]: account for account in list_accounts()}
        update_daily_cny_exchange_rate(
            "2026-09-03",
            DailyCnyRateUpdate(
                usd_cny_rate="7.2",
                hkd_cny_rate="0.9",
                note="查询汇总测试汇率",
            ),
        )
        create_expense(
            ExpenseCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                amount="90",
                category_id=self.food_category_id,
            )
        )
        create_expense(
            ExpenseCreate(
                transaction_date="2026-09-03",
                source_account_id=accounts["HKD-BANK-01"]["id"],
                amount="20",
                category_id=self.food_category_id,
            )
        )
        create_income(
            IncomeCreate(
                transaction_date="2026-09-02",
                destination_account_id=accounts["USD-BANK-01"]["id"],
                amount="10",
                category_id=self.salary_category_id,
            )
        )
        create_transfer(
            TransferCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                destination_account_id=accounts["CNY-CASH"]["id"],
                outflow_amount="30",
            )
        )
        create_transfer(
            TransferCreate(
                transaction_date="2026-09-03",
                source_account_id=accounts["CNY-CASH"]["id"],
                destination_account_id=accounts["HKD-BANK-01"]["id"],
                outflow_amount="9",
                inflow_amount="10",
            )
        )

        result = search_transactions(
            start_date="2026-09-02",
            end_date="2026-09-03",
            transaction_types=["income", "expense", "transfer", "fx_exchange"],
        )
        summary = result["summary"]
        self.assertTrue(summary["available"])
        self.assertEqual(summary["scope"]["mode"], "all_accounts")
        self.assertEqual(summary["transaction_count"], 5)
        self.assertEqual(result["pagination"]["total"], 5)
        self.assertEqual(
            {item["currency_code"]: item for item in summary["raw_totals_by_currency"]},
            {
                "CNY": {
                    "currency_code": "CNY",
                    "inflow_amount": "30.00",
                    "outflow_amount": "129.00",
                    "net_amount": "-99.00",
                },
                "HKD": {
                    "currency_code": "HKD",
                    "inflow_amount": "10.00",
                    "outflow_amount": "20.00",
                    "net_amount": "-10.00",
                },
                "USD": {
                    "currency_code": "USD",
                    "inflow_amount": "10.00",
                    "outflow_amount": "0.00",
                    "net_amount": "10.00",
                },
            },
        )
        valuations = {item["currency_code"]: item for item in summary["valuations"]}
        self.assertEqual(valuations["CNY"]["inflow_amount"], "111.00")
        self.assertEqual(valuations["CNY"]["outflow_amount"], "147.00")
        self.assertEqual(valuations["CNY"]["net_amount"], "-36.00")
        self.assertEqual(
            valuations["CNY"]["rate_coverage"],
            {
                "direct_base_currency_leg_count": 4,
                "exact_rate_leg_count": 2,
                "nearest_rate_leg_count": 1,
                "excluded_leg_count": 0,
                "missing_currencies": [],
            },
        )
        self.assertEqual(valuations["HKD"]["inflow_amount"], "123.33")
        self.assertEqual(valuations["HKD"]["outflow_amount"], "163.33")
        self.assertEqual(valuations["HKD"]["net_amount"], "-40.00")
        self.assertEqual(
            valuations["HKD"]["rate_coverage"],
            {
                "direct_base_currency_leg_count": 2,
                "exact_rate_leg_count": 1,
                "nearest_rate_leg_count": 4,
                "excluded_leg_count": 0,
                "missing_currencies": [],
            },
        )
        by_type = {
            item["transaction_type"]: item
            for item in summary["by_type"]
        }
        self.assertEqual(by_type["expense"]["transaction_count"], 2)
        self.assertEqual(by_type["transfer"]["transaction_count"], 1)
        self.assertEqual(by_type["fx_exchange"]["transaction_count"], 1)
        self.assertEqual(by_type["income"]["transaction_count"], 1)

        account_summary = search_transactions(
            account_id=accounts["CNY-BANK-01"]["id"],
            start_date="2026-09-02",
            end_date="2026-09-03",
            transaction_types=["income", "expense", "transfer", "fx_exchange"],
        )["summary"]
        self.assertEqual(account_summary["scope"]["mode"], "selected_account")
        self.assertEqual(account_summary["transaction_count"], 2)
        self.assertEqual(
            account_summary["raw_totals_by_currency"],
            [
                {
                    "currency_code": "CNY",
                    "inflow_amount": "0.00",
                    "outflow_amount": "120.00",
                    "net_amount": "-120.00",
                }
            ],
        )
        account_types = {
            item["transaction_type"]: item
            for item in account_summary["by_type"]
        }
        self.assertEqual(account_types["transfer"]["raw_totals_by_currency"][0]["inflow_amount"], "0.00")
        self.assertEqual(account_types["transfer"]["raw_totals_by_currency"][0]["outflow_amount"], "30.00")

        unavailable = search_transactions(start_date="2026-09-02")["summary"]
        self.assertFalse(unavailable["available"])

    def test_corrections_backup_audit_and_adjustment(self):
        accounts = {account["code"]: account for account in list_accounts()}
        expense = create_expense(
            ExpenseCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                amount="12.34",
                category_id=self.food_category_id,
                counterparty="餐厅",
            )
        )
        original = get_transaction(expense["id"])
        self.assertEqual(original["outflow_amount"], "12.34")

        updated = update_transaction(
            expense["id"],
            TransactionUpdate(
                transaction_date="2026-09-02",
                transaction_type="expense",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                outflow_amount="20",
                category_id=self.food_category_id,
                counterparty="餐厅",
                correction_reason="金额录错",
            ),
        )
        self.assertEqual(updated["outflow_amount"], "20.00")
        self.assertEqual(
            next(account for account in list_accounts() if account["code"] == "CNY-BANK-01")["balance"],
            "-20.00",
        )

        upsert_transaction_flag(
            expense["id"],
            TransactionFlagUpdate(
                flag_type="suspected_duplicate",
                note="等待核对小票",
            ),
        )
        flagged_detail = account_detail(
            accounts["CNY-BANK-01"]["id"],
            flagged_only=True,
        )
        self.assertEqual(flagged_detail["pagination"]["total"], 1)
        self.assertEqual(
            flagged_detail["transactions"][0]["flag"]["flag_type"],
            "suspected_duplicate",
        )

        deletion = delete_transaction(
            expense["id"],
            TransactionDelete(reason="确认重复记账"),
        )
        self.assertTrue(deletion["deleted"])
        self.assertEqual(
            next(account for account in list_accounts() if account["code"] == "CNY-BANK-01")["balance"],
            "0.00",
        )

        asset_adjustment = create_balance_adjustment(
            accounts["CNY-BANK-01"]["id"],
            BalanceAdjustmentCreate(
                adjustment_date="2026-09-02",
                actual_balance="250",
                reason="缺少历史流水",
            ),
        )
        self.assertEqual(asset_adjustment["difference"], "250.00")
        self.assertIsNotNone(asset_adjustment["adjustment_transaction_id"])
        self.assertEqual(
            next(account for account in list_accounts() if account["code"] == "CNY-BANK-01")["balance"],
            "250.00",
        )

        liability_increase = create_balance_adjustment(
            accounts["CNY-CARD-01"]["id"],
            BalanceAdjustmentCreate(
                adjustment_date="2026-09-02",
                actual_balance="90",
                reason="漏记信用卡消费",
            ),
        )
        self.assertEqual(liability_increase["difference"], "90.00")
        liability_decrease = create_balance_adjustment(
            accounts["CNY-CARD-01"]["id"],
            BalanceAdjustmentCreate(
                adjustment_date="2026-09-03",
                actual_balance="50",
                reason="漏记还款",
            ),
        )
        self.assertEqual(liability_decrease["difference"], "-40.00")
        self.assertEqual(
            next(account for account in list_accounts() if account["code"] == "CNY-CARD-01")["balance"],
            "50.00",
        )

        opening_balance = update_opening_balance(
            accounts["CNY-BANK-01"]["id"],
            OpeningBalanceUpdate(
                opening_balance="10",
                opening_balance_date="2026-01-01",
                reason="建账时漏填现金余额",
            ),
        )
        self.assertEqual(opening_balance["opening_balance"], "10.00")
        bank_detail = account_detail(accounts["CNY-BANK-01"]["id"])
        self.assertEqual(bank_detail["account"]["current_balance"], "260.00")
        self.assertEqual(bank_detail["account"]["reconciliation"]["status"], "difference")

        with database.connect() as connection:
            audit_actions = {
                row["action"]
                for row in connection.execute(
                    "SELECT action FROM change_audit_logs"
                ).fetchall()
            }
        self.assertTrue({"update", "delete", "flag", "create", "reconcile"} <= audit_actions)
        self.assertGreaterEqual(len(list(database.BACKUP_DIR.glob("finance-*.db"))), 7)

    def test_transaction_analysis_aggregates_days_and_converts_to_hkd(self):
        accounts = {account["code"]: account for account in list_accounts()}
        update_daily_cny_exchange_rate(
            "2026-09-03",
            DailyCnyRateUpdate(
                usd_cny_rate="7.2",
                hkd_cny_rate="0.9",
                note="统计测试汇率",
            ),
        )
        create_expense(
            ExpenseCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                amount="9",
                category_id=self.food_category_id,
                note="CNY 邻近汇率",
            )
        )
        create_expense(
            ExpenseCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["HKD-BANK-01"]["id"],
                amount="20",
                category_id=self.food_category_id,
                note="HKD 直接统计",
            )
        )
        create_expense(
            ExpenseCreate(
                transaction_date="2026-09-02",
                source_account_id=accounts["USD-BANK-01"]["id"],
                amount="10",
                category_id=self.food_category_id,
                note="USD 邻近汇率",
            )
        )
        create_expense(
            ExpenseCreate(
                transaction_date="2026-09-03",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                amount="9",
                category_id=self.food_category_id,
                note="CNY 当天汇率",
            )
        )

        analysis = transaction_analysis(
            transaction_type="expense",
            start_date="2026-09-02",
            end_date="2026-09-03",
            category_ids=[self.food_category_id],
        )
        self.assertEqual(analysis["currency_code"], "HKD")
        self.assertEqual(analysis["matched_transaction_count"], 4)
        self.assertEqual(analysis["rate_coverage"]["direct_hkd_count"], 1)
        self.assertEqual(analysis["rate_coverage"]["exact_rate_count"], 1)
        self.assertEqual(analysis["rate_coverage"]["nearest_rate_count"], 2)
        self.assertEqual(analysis["rate_coverage"]["excluded_count"], 0)
        self.assertEqual(analysis["statistics"]["total_amount"], "120.00")
        self.assertEqual(analysis["statistics"]["converted_transaction_count"], 4)
        self.assertEqual(analysis["statistics"]["active_day_count"], 2)
        self.assertEqual(analysis["statistics"]["calendar_day_mean"], "60.00")
        self.assertEqual(analysis["statistics"]["active_day_mean"], "60.00")
        self.assertEqual(analysis["statistics"]["median_daily_amount"], "60.00")
        self.assertEqual(analysis["statistics"]["minimum_daily_amount"], "10.00")
        self.assertEqual(analysis["statistics"]["maximum_daily_amount"], "110.00")
        self.assertEqual(analysis["statistics"]["population_daily_variance"], "2500.0000")
        self.assertEqual(
            analysis["statistics"]["population_daily_standard_deviation"],
            "50.00",
        )
        self.assertEqual(analysis["statistics"]["maximum_day_date"], "2026-09-02")
        self.assertEqual(analysis["statistics"]["maximum_day_amount"], "110.00")
        self.assertEqual(
            analysis["daily_totals"],
            [
                {"transaction_date": "2026-09-02", "amount": "110.00", "count": 3},
                {"transaction_date": "2026-09-03", "amount": "10.00", "count": 1},
            ],
        )
        self.assertEqual(sum(item["count"] for item in analysis["histogram"]), 2)
        self.assertTrue(
            all(
                isinstance(item["start"], int) and isinstance(item["end"], int)
                for item in analysis["histogram"]
            )
        )
        self.assertEqual(
            analysis["weekday_distribution"][2],
            {"weekday": 2, "label": "周三", "amount": "110.00", "active_day_count": 1},
        )
        self.assertEqual(
            analysis["weekday_distribution"][3],
            {"weekday": 3, "label": "周四", "amount": "10.00", "active_day_count": 1},
        )
        self.assertEqual(
            analysis["category_distribution"],
            [{"name": "餐饮", "amount": "120.00", "count": 4, "amount_share": "100.0"}],
        )

    def test_investment_valuation_recalculates_and_converts_base_currency(self):
        accounts = {account["code"]: account for account in list_accounts()}
        investment_account = accounts["CNY-INVEST-01"]

        first_valuation = upsert_investment_valuation(
            investment_account["id"],
            "2026-09-02",
            InvestmentValuationUpsert(total_value="5200", note="月初估值"),
        )
        self.assertEqual(first_valuation["generated_change"], "200.00")
        self.assertIsNotNone(first_valuation["generated_transaction_id"])
        self.assertTrue(
            get_transaction(first_valuation["generated_transaction_id"])[
                "is_investment_valuation"
            ]
        )

        create_transfer(
            TransferCreate(
                transaction_date="2026-09-03",
                source_account_id=accounts["CNY-BANK-01"]["id"],
                destination_account_id=investment_account["id"],
                outflow_amount="100",
            )
        )
        second_valuation = upsert_investment_valuation(
            investment_account["id"],
            "2026-09-03",
            InvestmentValuationUpsert(total_value="5400", note="月末估值"),
        )
        self.assertEqual(second_valuation["generated_change"], "100.00")

        corrected_first_valuation = upsert_investment_valuation(
            investment_account["id"],
            "2026-09-02",
            InvestmentValuationUpsert(total_value="5100", note="修正月初估值"),
        )
        self.assertEqual(corrected_first_valuation["generated_change"], "100.00")
        self.assertEqual(
            next(
                account
                for account in list_accounts()
                if account["code"] == "CNY-INVEST-01"
            )["balance"],
            "5400.00",
        )

        performance = investment_performance(
            start_date="2026-09-01",
            end_date="2026-09-03",
            account_id=investment_account["id"],
        )
        self.assertEqual(performance["results"][0]["contribution"], "100.00")
        self.assertEqual(performance["results"][0]["profit"], "300.00")
        self.assertEqual(performance["results"][0]["status"], "valued")

        monthly = investment_monthly(
            start_month="2026-09",
            end_month="2026-09",
            account_id=investment_account["id"],
        )
        self.assertEqual(monthly["results"][0]["profit"], "300.00")
        self.assertEqual(monthly["results"][0]["status"], "valued")

        update_daily_cny_exchange_rate(
            "2026-09-04",
            DailyCnyRateUpdate(
                usd_cny_rate="7.2",
                hkd_cny_rate="0.92",
                note="测试汇率",
            ),
        )
        create_expense(
            ExpenseCreate(
                transaction_date="2026-09-04",
                source_account_id=accounts["CNY-CARD-01"]["id"],
                category_id=self.food_category_id,
                amount="50",
                note="测试本币负债换算",
            )
        )
        with patch("app.main.today_in_shanghai", return_value=date(2026, 9, 4)):
            dashboard_data = dashboard("2026-09")
        totals = {
            item["currency_code"]: item
            for item in dashboard_data["base_currency_totals"]
        }
        self.assertEqual(totals["CNY"]["total_assets"], "6020.00")
        self.assertEqual(totals["HKD"]["total_assets"], "6543.48")
        self.assertEqual(totals["CNY"]["total_liabilities"], "50.00")
        self.assertEqual(totals["HKD"]["total_liabilities"], "54.35")
        self.assertEqual(totals["CNY"]["net_worth"], "5970.00")
        self.assertEqual(totals["HKD"]["net_worth"], "6489.13")
        self.assertTrue(totals["CNY"]["is_complete"])
        self.assertNotIn("fx_change_analysis", dashboard_data)

        with patch(
            "app.main.fetch_public_cny_rates",
            return_value={
                "usd_cny_rate": Decimal("7.3"),
                "hkd_cny_rate": Decimal("0.93"),
                "source": "test",
                "market_date": "2026-09-04",
            },
        ):
            synced = sync_daily_cny_exchange_rates(date(2026, 9, 5))
        self.assertEqual(synced["usd_cny_rate"], "7.3000")
        self.assertEqual(synced["hkd_cny_rate"], "0.9300")

        deletion = delete_investment_valuation(first_valuation["id"])
        self.assertTrue(deletion["deleted"])
        self.assertEqual(
            next(
                account
                for account in list_accounts()
                if account["code"] == "CNY-INVEST-01"
            )["balance"],
            "5400.00",
        )


if __name__ == "__main__":
    unittest.main()

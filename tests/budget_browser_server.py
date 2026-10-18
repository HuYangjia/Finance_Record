"""Isolated browser fixture: run manually, stop with Ctrl+C to remove its database."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import database
from app.main import FREQUENT_SNAPSHOT_RULE_VERSION, app, today_in_shanghai
import uvicorn


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='finance-budget-browser-') as directory:
        root = Path(directory)
        database.DATA_DIR = root/'data'
        database.DATABASE_PATH = root/'data/finance.db'
        database.BACKUP_DIR = root/'backups'
        database.initialize_database()
        with database.connect() as c:
            for code, name, currency, directory, institution in [
                ('OCTOPUS-STUDENT','八达通','HKD','stored_value','八达通'),
                ('ZABANK-HKD','ZABank（港币）','HKD','overseas_bank','ZABank'),
                ('WECHAT','微信','CNY','cash_wallet','微信'),
                ('ALIPAY','支付宝','CNY','cash_wallet','支付宝'),
                ('ALIPAY-FUND','支付宝基金','CNY','investment','支付宝'),
            ]:
                # Use a common valid account type; the fixture tests account IDs and currencies.
                c.execute("""INSERT INTO accounts(
                    code,name,account_class,account_type,currency_code,
                    institution,directory_key,opening_balance_date
                ) VALUES (?,?,'asset','bank_demand',?,?,?,'2026-01-01')""",
                    (code,name,currency,institution,directory))
            c.execute("INSERT INTO daily_cny_exchange_rates(rate_date,usd_cny_rate,hkd_cny_rate,source,market_date) VALUES ('2026-10-01','7','1','test','2026-10-01')")
            snapshot_date = today_in_shanghai().isoformat()
            c.execute(
                "INSERT INTO daily_frequent_snapshots(snapshot_date,rules_version) VALUES (?,?)",
                (snapshot_date, FREQUENT_SNAPSHOT_RULE_VERSION),
            )
            account_ids = {
                row['code']: row['id']
                for row in c.execute("SELECT id,code FROM accounts")
            }
            c.executemany(
                """INSERT INTO daily_account_frequent_activity(
                       snapshot_date,account_id,activity_key,record_count,
                       recent_record_count,last_transaction_date
                   ) VALUES (?,?,'expense_source',?,?,?)""",
                [
                    (snapshot_date, account_ids['ZABANK-HKD'], 18, 18, snapshot_date),
                    (snapshot_date, account_ids['OCTOPUS-STUDENT'], 47, 12, snapshot_date),
                ],
            )
            category_ids = {
                row['name']: row['id']
                for row in c.execute("SELECT id,name FROM categories WHERE kind='expense'")
            }
            c.executemany(
                """INSERT INTO daily_category_frequent_activity(
                       snapshot_date,category_id,record_count,
                       recent_record_count,last_transaction_date
                   ) VALUES (?,?,?,?,?)""",
                [
                    (snapshot_date, category_ids['餐饮'], 3, 0, '2026-09-20'),
                    (snapshot_date, category_ids['交通'], 1, 1, snapshot_date),
                ],
            )
            c.executemany(
                """INSERT INTO transaction_entry_shortcuts(
                       name,mode,source_account_id,destination_account_id,
                       category_id,outflow_minor,note,sort_order)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    ('八达通餐饮','expense',account_ids['OCTOPUS-STUDENT'],None,category_ids['餐饮'],None,'',10),
                    ('微信转八达通','transfer',account_ids['WECHAT'],account_ids['OCTOPUS-STUDENT'],None,None,'',20),
                    ('支付宝定投 35','transfer',account_ids['ALIPAY'],account_ids['ALIPAY-FUND'],None,3500,'每日定投',30),
                ],
            )

        @app.get('/__budget_test_fixture', include_in_schema=False)
        def fixture():
            return {'fixture':'budget-browser','temporary':True}

        try:
            uvicorn.run(app,host='127.0.0.1',port=18110,lifespan='off',log_level='warning')
        except KeyboardInterrupt:
            pass

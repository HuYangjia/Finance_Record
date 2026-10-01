"""Isolated browser fixture: run manually, stop with Ctrl+C to remove its database."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import database
from app.main import app
import uvicorn


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='finance-budget-browser-') as directory:
        root = Path(directory)
        database.DATA_DIR = root/'data'
        database.DATABASE_PATH = root/'data/finance.db'
        database.BACKUP_DIR = root/'backups'
        database.initialize_database()
        with database.connect() as c:
            for code, name, currency, kind in [
                ('OCTOPUS-STUDENT','八达通','HKD','stored_value'),
                ('WECHAT','微信','CNY','e_wallet'),
                ('ALIPAY','支付宝','CNY','e_wallet'),
                ('ALIPAY-FUND','支付宝基金','CNY','investment_asset'),
            ]:
                # Use a common valid account type; the fixture tests account IDs and currencies.
                c.execute("INSERT INTO accounts(code,name,account_class,account_type,currency_code,opening_balance_date) VALUES (?,?,'asset','bank_demand',?,'2026-01-01')",(code,name,currency))
            c.execute("INSERT INTO daily_cny_exchange_rates(rate_date,usd_cny_rate,hkd_cny_rate,source,market_date) VALUES ('2026-10-01','7','1','test','2026-10-01')")

        @app.get('/__budget_test_fixture', include_in_schema=False)
        def fixture():
            return {'fixture':'budget-browser','temporary':True}

        try:
            uvicorn.run(app,host='127.0.0.1',port=18110,lifespan='off',log_level='warning')
        except KeyboardInterrupt:
            pass

from datetime import date
from io import BytesIO
from unittest.mock import patch
import unittest

from openpyxl import load_workbook

from app import database
import test_budgets


class TermDepositTest(unittest.TestCase):
    call = test_budgets.BudgetTest.call

    def setUp(self):
        test_budgets.BudgetTest.setUp(self)
        self.date_patch = patch('app.term_deposits.today', return_value=date(2026,10,29))
        self.date_patch.start()
        with database.connect() as c:
            c.execute("UPDATE accounts SET opening_balance_minor=1000000,opening_balance_date='2024-01-01' WHERE id=?",(self.account,))
        self.body = dict(name='银行三个月',source_account_id=self.account,principal='1000',
                         start_date='2026-10-01',term_value=3,term_unit='month',annual_rate='3.5',day_basis=365)

    def tearDown(self):
        self.date_patch.stop()
        test_budgets.BudgetTest.tearDown(self)

    def create(self, **changes):
        return self.call('POST','/api/term-deposits',{**self.body,**changes},201)

    def balances(self):
        return {r['id']:r['balance'] for r in self.call('GET','/api/accounts')}

    def settle(self, deposit, **changes):
        return self.call('POST',f'/api/term-deposits/{deposit["id"]}/settle',{
            'expected_version':deposit['version'],'settlement_date':'2026-10-29',
            'destination_account_id':self.account,'actual_interest':'2.34',**changes})

    def test_internal_principal_then_only_actual_interest_changes_income_assets_and_cash(self):
        original=self.call('GET','/api/dashboard?month=2026-10')
        deposit=self.create()
        self.assertEqual(deposit['maturity_date'],'2027-01-01')
        self.assertEqual(deposit['expected_interest'],'8.82')
        self.assertEqual(self.balances()[self.account],'9000.00')
        self.assertEqual(self.balances()[deposit['term_account_id']],'1000.00')
        dashboard=self.call('GET','/api/dashboard?month=2026-10')
        self.assertEqual(dashboard['net_worth'],original['net_worth'])
        self.assertEqual(dashboard['cash_flow'],[])
        cash=next(r for r in dashboard['available_cash'] if r['currency_code']=='HKD')
        self.assertEqual(cash['available_cash'],'9000.00')
        settled=self.settle(deposit)
        self.assertEqual(settled['status'],'settled')
        self.assertEqual(self.balances()[self.account],'10002.34')
        self.assertEqual(self.balances()[deposit['term_account_id']],'0.00')
        self.assertEqual(self.call('GET','/api/transactions/search?transaction_types=income')['transactions'][0]['inflow_amount'],'2.34')
        self.assertEqual(self.call('GET','/api/budget/overview?end_month=2026-10')['plans'],[])
        self.call('POST',f'/api/term-deposits/{deposit["id"]}/settle',dict(expected_version=1,
                  settlement_date='2026-10-29',destination_account_id=self.account,actual_interest='2.34'),409)
        dashboard=self.call('GET','/api/dashboard?month=2026-10')
        self.assertEqual(dashboard['cash_flow'][0]['income'],'2.34')
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],3)

    def test_maturity_month_end_leap_year_days_and_override_preview_no_writes(self):
        for start,value,unit,expected in [('2024-01-31',1,'month','2024-02-29'),
                                         ('2025-01-31',1,'month','2025-02-28'),
                                         ('2026-08-31',2,'month','2026-10-31'),
                                         ('2026-10-01',7,'day','2026-10-08')]:
            query=f'source_account_id={self.account}&principal=1000&start_date={start}&term_value={value}&term_unit={unit}&annual_rate=3.5&day_basis=365'
            preview=self.call('GET','/api/term-deposits/preview?'+query)
            self.assertEqual(preview['maturity_date'],expected)
        preview=self.call('GET','/api/term-deposits/preview?'+query+'&maturity_override=2026-10-09')
        self.assertEqual(preview['automatic_maturity_date'],'2026-10-08')
        self.assertEqual(preview['maturity_date'],'2026-10-09')
        preview360=self.call('GET','/api/term-deposits/preview?'+query.replace('day_basis=365','day_basis=360'))
        self.assertEqual(preview360['expected_interest'],'0.68')
        self.call('GET','/api/term-deposits/preview?'+query.replace('term_value=7','term_value=-1'),code=422)
        self.call('GET','/api/term-deposits/preview?'+query+'&maturity_override=2026-10-01',code=422)
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM term_deposit_accounts').fetchone()[0],0)

    def test_correction_reuses_account_reopen_and_delete_restore_balances(self):
        deposit=self.create()
        second=self.create(name='另一笔',principal='200')
        self.assertEqual(second['term_account_id'],deposit['term_account_id'])
        updated=self.call('PUT',f'/api/term-deposits/{deposit["id"]}',{
            **self.body,'expected_version':1,'principal':'1500','term_value':1})
        self.assertEqual(updated['maturity_date'],'2026-11-01')
        self.assertEqual(self.balances()[self.account],'8300.00')
        self.call('PUT',f'/api/term-deposits/{deposit["id"]}',{**self.body,'expected_version':1},409)
        settled=self.settle(updated)
        self.call('DELETE',f'/api/term-deposits/{deposit["id"]}',dict(expected_version=settled['version']),409)
        reopened=self.call('POST',f'/api/term-deposits/{deposit["id"]}/reopen',dict(expected_version=settled['version']))
        self.assertEqual(self.balances()[self.account],'8300.00')
        self.call('DELETE',f'/api/term-deposits/{deposit["id"]}',dict(expected_version=reopened['version']))
        self.call('DELETE',f'/api/term-deposits/{second["id"]}',dict(expected_version=1))
        self.assertEqual(self.balances()[self.account],'10000.00')
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],0)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
            self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM change_audit_logs WHERE entity_type='term_deposit'").fetchone()[0],7)

    def test_currency_precision_dates_and_invalid_settlement_roll_back(self):
        self.call('POST','/api/term-deposits',{**self.body,'principal':'1.001'},422)
        self.call('POST','/api/term-deposits',{**self.body,'start_date':'2026-10-30'},422)
        self.call('POST','/api/term-deposits',{**self.body,'name':' '},422)
        deposit=self.create()
        with database.connect() as c:
            other=c.execute("INSERT INTO accounts(code,name,account_class,account_type,currency_code,opening_balance_date) VALUES ('USD','美元','asset','bank_demand','USD','2024-01-01')").lastrowid
        for changes in [dict(destination_account_id=other),dict(actual_interest='0.001'),dict(settlement_date='2026-10-30')]:
            body=dict(expected_version=1,settlement_date='2026-10-29',destination_account_id=self.account,actual_interest='2')
            self.call('POST',f'/api/term-deposits/{deposit["id"]}/settle',{**body,**changes},422)
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],1)
            c.execute("UPDATE categories SET is_active=0 WHERE name='利息/股息' AND kind='income'")
        self.call('POST',f'/api/term-deposits/{deposit["id"]}/settle',dict(expected_version=1,
            settlement_date='2026-10-29',destination_account_id=self.account,actual_interest='1'),422)
        self.assertEqual(self.call('GET','/api/term-deposits')['deposits'][0]['version'],1)
        self.assertEqual(self.call('GET','/api/transactions/search')['pagination']['total'],1)
        settled=self.settle(deposit,actual_interest='0')
        self.assertIsNone(settled['interest_transaction_id'])
        self.assertEqual(self.balances()[self.account],'10000.00')

    def test_linked_transactions_and_managed_account_are_protected(self):
        deposit=self.create()
        opening=deposit['opening_transaction_id']
        tx=self.call('GET',f'/api/transactions/{opening}')
        self.assertEqual(tx['term_deposit_id'],deposit['id'])
        self.call('PUT',f'/api/transactions/{opening}',dict(transaction_date='2026-10-01',transaction_type='transfer',
            source_account_id=self.account,destination_account_id=self.other_account,outflow_amount='1000',inflow_amount='1000'),409)
        self.call('DELETE',f'/api/transactions/{opening}',dict(reason='测试保护'),409)
        self.call('POST','/api/transfers',dict(transaction_date='2026-10-01',source_account_id=self.account,
            destination_account_id=deposit['term_account_id'],outflow_amount='1'),409)
        self.call('POST',f'/api/accounts/{deposit["term_account_id"]}/adjustments',dict(
            adjustment_date='2026-10-01',actual_balance='1',reason='不应直接调整'),409)
        self.call('PUT',f'/api/accounts/{deposit["term_account_id"]}/opening-balance',dict(
            opening_balance_date='2024-01-01',opening_balance='1',reason='不应直接调整'),409)

    def test_reopen_requires_unlinking_income_allocations_and_offsets(self):
        deposit=self.settle(self.create())
        interest=deposit['interest_transaction_id']
        detail=self.call('GET',f'/api/budget/income-allocations/transaction/{interest}')
        self.call('PUT',f'/api/budget/income-allocations/transaction/{interest}',dict(expected_token=detail['token'],
            items=[dict(budget_system_id=self.daily,amount='2.34')]))
        self.call('POST',f'/api/term-deposits/{deposit["id"]}/reopen',dict(expected_version=2),409)
        detail=self.call('GET',f'/api/budget/income-allocations/transaction/{interest}')
        self.call('PUT',f'/api/budget/income-allocations/transaction/{interest}',dict(expected_token=detail['token'],items=[]))
        group=self.call('POST','/api/offset-groups',dict(title='利息事项',kind='income',transaction_ids=[interest]),201)
        self.call('POST',f'/api/term-deposits/{deposit["id"]}/reopen',dict(expected_version=2),409)
        self.call('DELETE',f'/api/offset-groups/{group["id"]}',dict(expected_version=1))
        self.call('POST',f'/api/term-deposits/{deposit["id"]}/reopen',dict(expected_version=2))

    def test_summary_status_filtering_and_historical_export(self):
        self.create(name='到期未到账',start_date='2026-09-01',term_value=1)
        self.create(name='即将到期',start_date='2026-10-01',term_value=1)
        deposit=self.create(name='长期存款')
        data=self.call('GET','/api/term-deposits')
        self.assertEqual(data['open_count'],3)
        self.assertEqual(data['summary'][0]['principal'],'3000.00')
        self.assertEqual(data['summary'][0]['due_principal'],'2000.00')
        self.assertEqual(self.call('GET','/api/term-deposits?status=due')['total'],1)
        self.assertEqual(self.call('GET','/api/term-deposits?status=soon')['total'],1)
        self.settle(deposit)
        self.assertEqual(self.call('GET','/api/term-deposits?status=settled')['total'],1)
        self.assertEqual(self.call('GET','/api/term-deposits')['total'],2)
        book=load_workbook(BytesIO(self.client.get('/api/export.xlsx?month=2026-09').content))
        self.assertEqual(book['定期存款'].max_row,4)
        self.assertEqual(book['定期存款']['A4'].value,'到期未到账')
        self.assertEqual(self.client.get('/term-deposits').status_code,200)
        self.assertIn('open-term-deposit',self.client.get('/').text)

    def test_upgrade_backup_is_idempotent_and_preserves_existing_ledger(self):
        with database.connect() as c:
            c.execute('DROP TABLE term_deposits')
            c.execute('DROP TABLE term_deposit_accounts')
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-term-deposits-*.db'))),1)
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-term-deposits-*.db'))),1)
        self.assertEqual(self.balances()[self.account],'10000.00')
        self.assertEqual(self.call('GET','/api/term-deposits')['deposits'],[])

    def test_public_pool_is_shared_between_banks_and_separates_currencies(self):
        first=self.create()
        other=self.create(name='第二家银行',source_account_id=self.other_account,principal='200')
        self.assertEqual(first['term_account_id'],other['term_account_id'])
        self.assertEqual(first['term_account_name'],'定期存款（港币）')
        self.assertEqual(self.balances()[first['term_account_id']],'1200.00')
        self.assertEqual(self.balances()[self.other_account],'-200.00')
        with database.connect() as c:
            usd=c.execute("INSERT INTO accounts(code,name,account_class,account_type,currency_code,opening_balance_date) VALUES ('USD','美元','asset','bank_demand','USD','2024-01-01')").lastrowid
        dollar=self.create(name='美元定存',source_account_id=usd,principal='300')
        self.assertNotEqual(dollar['term_account_id'],first['term_account_id'])
        self.assertEqual(dollar['term_account_name'],'定期存款（美元）')
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM term_deposit_pools').fetchone()[0],2)
        self.settle(other,actual_interest='0')
        self.assertEqual(self.balances()[first['term_account_id']],'1000.00')
        self.call('PUT',f'/api/term-deposits/{first["id"]}',{
            **self.body,'source_account_id':self.other_account,'expected_version':1})
        self.assertEqual(self.balances()[first['term_account_id']],'1000.00')
        self.assertEqual(self.call('GET','/api/term-deposits?source_account_id='+str(self.other_account))['total'],1)

    def legacy_bank_accounts(self):
        """Build the previously released one-bank/one-account schema in this fixture."""
        first=self.create()
        second=self.create(name='旧银行第二笔',source_account_id=self.other_account,principal='200')
        settled=self.settle(second)
        c=database.connect()
        try:
            c.execute('PRAGMA foreign_keys=OFF')
            c.execute('BEGIN IMMEDIATE')
            separate=c.execute("""INSERT INTO accounts(code,name,account_class,account_type,currency_code,
                opening_balance_date) VALUES ('TERM-LEGACY-B','第二家银行旧定存','asset','bank_term','HKD','2026-10-01')""").lastrowid
            c.execute('UPDATE transactions SET destination_account_id=? WHERE id=?',(separate,second['opening_transaction_id']))
            c.execute('UPDATE transactions SET source_account_id=? WHERE id=?',(separate,settled['return_transaction_id']))
            c.execute('UPDATE term_deposit_accounts SET term_account_id=? WHERE source_account_id=?',(separate,self.other_account))
            c.execute('''CREATE TABLE term_deposit_accounts_legacy (
                source_account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE RESTRICT,
                term_account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE RESTRICT)''')
            c.execute('INSERT INTO term_deposit_accounts_legacy SELECT * FROM term_deposit_accounts')
            c.execute('DROP TABLE term_deposit_accounts')
            c.execute('ALTER TABLE term_deposit_accounts_legacy RENAME TO term_deposit_accounts')
            c.execute('DROP TABLE term_deposit_pools')
            c.execute("DELETE FROM app_settings WHERE key='shared_term_deposit_version'")
            c.commit()
            c.execute('PRAGMA foreign_keys=ON')
        finally:
            c.close()
        return first,settled,separate

    def test_legacy_banks_merge_preserves_contracts_interest_and_history_then_restarts_idempotently(self):
        from app.term_migrations import ledger_signature,unique_target
        first,settled,separate=self.legacy_bank_accounts()
        with database.connect() as c:
            original=ledger_signature(c)
            old_contracts=[dict(r) for r in c.execute('SELECT * FROM term_deposits ORDER BY id')]
            self.assertTrue(unique_target(c))
        database.initialize_database()
        backups=list(database.BACKUP_DIR.glob('finance-pre-shared-term-deposits-*.db'))
        self.assertEqual(len(backups),1)
        with database.open_connection(backups[0]) as c:
            self.assertEqual(c.execute('SELECT term_account_id FROM term_deposit_accounts WHERE source_account_id=?',
                                      (self.other_account,)).fetchone()[0],separate)
        with database.connect() as c:
            self.assertEqual(ledger_signature(c),original)
            self.assertFalse(unique_target(c))
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual(c.execute('SELECT COUNT(*) FROM term_deposit_pools').fetchone()[0],1)
            archive=c.execute('SELECT is_active FROM accounts WHERE id=?',(separate,)).fetchone()
            self.assertEqual(archive['is_active'],0)
            self.assertEqual(c.execute('SELECT balance_minor FROM account_balances WHERE id=?',(separate,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT source_account_id FROM transactions WHERE id=?',
                                      (settled['return_transaction_id'],)).fetchone()[0],first['term_account_id'])
            for old in old_contracts:
                new=dict(c.execute('SELECT * FROM term_deposits WHERE id=?',(old['id'],)).fetchone())
                self.assertEqual(new['version'],old['version']+1)
                for field,value in old.items():
                    if field not in ('version','updated_at'):
                        self.assertEqual(new[field],value,field)
        data=self.call('GET','/api/term-deposits?status=all')
        self.assertTrue(all(r['term_account_id']==first['term_account_id'] for r in data['deposits']))
        self.assertEqual(next(r for r in data['deposits'] if r['id']==settled['id'])['actual_interest'],'2.34')
        self.call('PUT',f'/api/term-deposits/{first["id"]}',{**self.body,'expected_version':1},409)
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-shared-term-deposits-*.db'))),1)
        self.assertEqual(data,self.call('GET','/api/term-deposits?status=all'))
        self.create(source_account_id=self.other_account,principal='50')
        self.assertEqual(self.balances()[first['term_account_id']],'1050.00')

    def test_unknown_legacy_money_stops_migration_without_partial_rewrite(self):
        from app.term_migrations import ledger_signature,unique_target
        first,_,_=self.legacy_bank_accounts()
        with database.connect() as c:
            c.execute("""INSERT INTO transactions(transaction_date,transaction_type,destination_account_id,inflow_minor)
                         VALUES ('2026-10-01','adjustment',?,100)""",(first['term_account_id'],))
            original=ledger_signature(c)
            mapping=[tuple(r) for r in c.execute('SELECT * FROM term_deposit_accounts ORDER BY source_account_id')]
        with self.assertRaisesRegex(RuntimeError,'合同以外'):
            database.initialize_database()
        with database.connect() as c:
            self.assertEqual(ledger_signature(c),original)
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM term_deposit_accounts ORDER BY source_account_id')],mapping)
            self.assertTrue(unique_target(c))
            self.assertFalse(c.execute('SELECT 1 FROM term_deposit_pools').fetchone())
            self.assertFalse(c.execute("SELECT 1 FROM app_settings WHERE key='shared_term_deposit_version'").fetchone())
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())

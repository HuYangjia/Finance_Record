import json
import sqlite3
import unittest
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook
from app import database
import test_budgets


class StorageTest(unittest.TestCase):
    setUp = test_budgets.BudgetTest.setUp
    tearDown = test_budgets.BudgetTest.tearDown
    call = test_budgets.BudgetTest.call
    setting = test_budgets.BudgetTest.setting
    transaction = test_budgets.BudgetTest.transaction

    def setup_plan(self, total='1000', code='HKD', month='2026-10', allocations=None):
        self.cash=next(s['id'] for s in self.config['storage'] if s['name']=='活期')
        self.etf=next(s['id'] for s in self.config['storage'] if s['name']=='ETF')
        return self.call('PUT',f'/api/budget/monthly/{self.daily}/{month}',dict(plans=[dict(expected_version=0,currency_code=code,total=total,
            items=[dict(storage_method_id=k,amount=v) for k,v in (allocations if allocations is not None else [(self.cash,'300'),(self.etf,'700')])])]))

    def detail(self, month='2026-10', sid=None):
        return self.call('GET',f'/api/budget/storage/{sid or self.daily}/{month}')

    def write(self, suffix, body, method='PUT', status=200, sid=None):
        version=self.call('GET','/api/budget/config')['version']
        return self.call(method,f'/api/budget/storage/{sid or self.daily}/{suffix}',dict(expected_version=version,**body),status)

    def default(self, target):
        return self.write('default',dict(storage_method_id=target))

    def move(self, amount='100', source=None, target=None, **kwargs):
        body=dict(month='2026-10',source_id=source if source is not None else self.etf,
                  destination_id=target if target is not None else self.cash,amount=amount,**kwargs)
        return self.write('transfers',body,'POST',201)

    def balances(self, month='2026-10'):
        row=self.detail(month)['row']
        if row['complete']:
            self.assertEqual(sum(Decimal(c['balance']) for c in row['cells']),Decimal(row['total']))
        return {c['key']:c['balance'] for c in row['cells']}

    def assign(self, entry, target, status=200, sid=None):
        return self.write('expense',dict(entry_type=entry['entry_type'],entry_id=entry['entry_id'],expected_entry_token=entry['token'],storage_method_id=target),status=status,sid=sid)

    def test_default_negative_storage_transfer_and_carry_preserve_actuals_and_funding(self):
        self.setup_plan(); self.transaction(amount='350')
        before=self.call('GET','/api/accounts')
        balances=self.balances();self.assertEqual((balances[str(self.cash)],balances['pending']),('0.00','-50.00'))
        self.write('transfers',dict(month='2026-10',source_id=self.etf,destination_id=self.cash,amount='100'),'POST',422)
        self.default(self.cash)
        self.assertEqual(self.balances()[str(self.cash)],'-50.00')
        self.move(); balances=self.balances()
        self.assertEqual((balances[str(self.cash)],balances[str(self.etf)]),('50.00','600.00'))
        self.assertEqual(self.balances('2026-11'),balances)
        overview=self.call('GET','/api/budget/overview?start_month=2026-11&end_month=2026-11')
        self.assertEqual(overview['plans'],[])
        self.assertEqual(overview['storage']['rows'][0]['total'],'650.00')
        self.assertEqual(self.call('GET','/api/accounts'),before)
        plan=self.call('GET',f'/api/budget/monthly/{self.daily}/2026-10')['plans'][0]
        self.assertEqual(plan['total'],'1000.00');self.assertEqual(plan['version'],1)
        with database.connect() as c:
            log=c.execute("SELECT after_json,backup_file FROM change_audit_logs WHERE entity_type='budget' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(len(json.loads(log['after_json'])['storage']['budget_storage_transfers']),1)
            self.assertTrue((database.BACKUP_DIR/log['backup_file']).exists())

    def test_unassigned_expense_uses_unallocated_then_cash_and_leaves_only_shortfall_pending(self):
        cash=next(s['id'] for s in self.config['storage'] if s['name']=='活期')
        etf=next(s['id'] for s in self.config['storage'] if s['name']=='ETF')
        self.setup_plan(allocations=[(cash,'300'),(etf,'600')])
        self.transaction(amount='350')
        row=self.detail()['row'];balances=self.balances()
        self.assertEqual((balances['unallocated'],balances[str(self.cash)],balances[str(self.etf)]),('0.00','50.00','600.00'))
        self.assertEqual(balances['pending'],'0.00')
        self.assertEqual((row['automatic_unallocated'],row['automatic_cash'],row['pending_amount']),('100.00','250.00','0.00'))
        self.assertEqual((row['automatic_count'],row['pending_count']),(1,0))
        self.assertTrue(row['expenses'][0]['automatic']);self.assertIsNone(row['expenses'][0]['storage_method_id'])
        self.transaction(amount='100')
        row=self.detail()['row'];balances=self.balances()
        self.assertEqual((balances[str(self.cash)],balances['pending']),('0.00','-50.00'))
        self.assertEqual((row['automatic_unallocated'],row['automatic_cash'],row['pending_amount']),('100.00','300.00','50.00'))
        self.assertEqual((row['automatic_count'],row['pending_count']),(2,2))
        self.assertEqual(row['total'],'550.00')

    def test_automatic_spending_precedes_month_end_transfer_and_next_month_carry(self):
        cash=next(s['id'] for s in self.config['storage'] if s['name']=='活期')
        self.setup_plan(total='100',allocations=[(cash,'100')])
        self.transaction(amount='50')
        before=self.call('GET','/api/accounts')
        move=self.write('transfers',dict(month='2026-10',source_id=cash,destination_id=None,amount='50'),'POST',201)
        balances=self.balances()
        self.assertEqual((balances[str(cash)],balances['unallocated']),('0.00','50.00'))
        self.assertEqual(self.balances('2026-11'),balances)
        self.transaction(amount='20',day='2026-11-01')
        self.assertEqual(self.balances('2026-11')['unallocated'],'30.00')
        self.assertEqual(self.balances(),balances)
        self.write(f"transfers/{move['id']}",dict(transfer_version=move['version']),'DELETE')
        self.assertEqual(self.balances('2026-11')[str(cash)],'30.00')
        self.assertEqual(self.balances('2026-11')['unallocated'],'0.00')

    def test_future_funding_does_not_reassign_previous_cash_consumption(self):
        cash=next(s['id'] for s in self.config['storage'] if s['name']=='活期')
        self.setup_plan(total='100',allocations=[(cash,'100')])
        self.transaction(amount='80')
        self.setup_plan(month='2026-11',total='100',allocations=[])
        balances=self.balances('2026-11')
        self.assertEqual((balances[str(cash)],balances['unallocated']),('20.00','100.00'))
        self.transaction(amount='150',day='2026-11-01')
        self.assertEqual(self.balances('2026-11')['pending'],'-30.00')
        self.setup_plan(month='2026-12',total='40',allocations=[])
        balances=self.balances('2026-12')
        self.assertEqual((balances['pending'],balances['unallocated']),('0.00','10.00'))

    def test_negative_automatic_expense_restores_unallocated_not_previously_used_cash(self):
        cash=next(s['id'] for s in self.config['storage'] if s['name']=='活期')
        self.setup_plan(total='300',allocations=[(cash,'300')])
        self.transaction(amount='200',day='2026-10-01')
        paid=self.transaction(amount='10',day='2026-10-02')['id']
        refund=self.transaction(kind='income',amount='60',day='2026-10-03')['id']
        self.call('POST','/api/offset-groups',dict(title='超额回款',kind='expense',transaction_ids=[paid,refund]),201)
        balances=self.balances()
        self.assertEqual((balances[str(cash)],balances['unallocated']),('100.00','50.00'))

    def test_complete_aa_negative_refund_salary_excluded_override_and_dissolve(self):
        self.setup_plan();self.default(self.cash)
        expense=self.transaction(amount='600')['id'];refund=self.transaction(kind='income',amount='350')['id']
        aa=self.call('POST','/api/offset-groups',dict(title='AA',kind='expense',transaction_ids=[expense,refund]),201)
        salary=self.transaction(kind='income',amount='1000')['id'];cost=self.transaction(amount='300')['id']
        self.call('POST','/api/offset-groups',dict(title='净工资',kind='income',transaction_ids=[salary,cost]),201)
        entries=self.detail()['row']['expenses'];self.assertEqual(len(entries),1);self.assertEqual(entries[0]['amount'],'250.00')
        self.assign(entries[0],self.etf);self.assertEqual(self.balances()[str(self.etf)],'450.00')
        extra=self.transaction(kind='income',amount='400',day='2026-11-01')['id']
        aa=self.call('PUT',f"/api/offset-groups/{aa['id']}",dict(title='AA',kind='expense',transaction_ids=[expense,refund,extra],expected_version=aa['version']))
        self.assertEqual(self.detail()['row']['expenses'][0]['amount'],'-150.00')
        self.assertEqual(self.balances()[str(self.etf)],'850.00')
        self.assign(self.detail()['row']['expenses'][0],None)
        self.assertEqual(self.balances()[str(self.cash)],'450.00')
        self.call('DELETE',f"/api/offset-groups/{aa['id']}",dict(expected_version=aa['version']))
        self.assertEqual(self.balances()[str(self.cash)],'-300.00')
        self.assertEqual(self.detail()['row']['expenses'][0]['entry_type'],'transaction')

    def test_bound_income_occupancy_unchanged_by_consumption_and_transfer(self):
        plan=self.setup_plan()['plans'][0]
        income=self.transaction(kind='income',amount='1000')['id']
        url=f'/api/budget/funding-sources?entry_type=transaction&entry_id={income}'
        source=self.call('GET',url)
        self.call('PUT',f'/api/budget/monthly/{self.daily}/2026-10',dict(plans=[dict(expected_version=1,currency_code='HKD',total='1000',items=plan['items'],
            funding=dict(entry_type='transaction',entry_id=income,expected_token=source['token']))]))
        self.transaction(amount='350');self.default(self.cash);self.move()
        self.assertEqual(self.call('GET',url)['available'],'0.00')
        self.assertEqual(self.detail()['row']['total'],'650.00')

    def test_stale_expense_and_cross_budget_assignment_rejected(self):
        self.setup_plan();expense=self.transaction(amount='10');entry=self.detail()['row']['expenses'][0]
        self.call('PUT',f"/api/transactions/{expense['id']}",dict(**{**expense,'outflow_amount':'12','correction_reason':'更正'}))
        self.assign(entry,self.etf,409)
        other=self.setting('systems',name='其他用途',currency_code='HKD')['id']
        self.assign(self.detail()['row']['expenses'][0],self.etf,409,sid=other)
        version=self.call('GET','/api/budget/config')['version'];self.default(self.cash)
        self.call('PUT',f'/api/budget/storage/{self.daily}/default',dict(expected_version=version,storage_method_id=self.etf),409)
        self.assertEqual(self.balances()[str(self.cash)],'288.00')

    def test_fx_rounding_conserves_monthly_execution_and_missing_rates(self):
        self.setup_plan(total='1',code='CNY',allocations=[(self.storage,'1')]);self.default(self.etf)
        with database.connect() as c:c.execute("UPDATE daily_cny_exchange_rates SET hkd_cny_rate='0.5'")
        for _ in range(2):self.transaction(amount='0.01')
        row=self.detail()['row'];self.assertEqual(sorted(e['amount'] for e in row['expenses']),['0.00','0.01'])
        self.assertEqual(row['total'],'0.99');self.assertEqual(self.balances()[str(self.etf)],'0.99')
        self.assign(row['expenses'][0],self.cash);self.balances()
        with database.connect() as c:c.execute('DELETE FROM daily_cny_exchange_rates')
        row=self.detail()['row'];self.assertFalse(row['complete']);self.assertTrue(all(s['balance'] is None for s in row['cells']))
        self.write('transfers',dict(month='2026-10',source_id=self.etf,destination_id=self.cash,amount='0.1'),'POST',422)

    def test_transfer_edit_delete_insufficient_and_inactive_source(self):
        self.setup_plan();self.default(self.cash);move=self.move()
        body=dict(month='2026-10',source_id=self.etf,destination_id=self.cash,amount='800',transfer_version=move['version'])
        self.write('transfers/'+str(move['id']),body,status=422)
        self.assertEqual(self.detail()['row']['transfers'][0]['amount'],'100.00')
        body['amount']='150';saved=self.write('transfers/'+str(move['id']),body)
        self.assertEqual(saved['version'],2);self.assertEqual(self.balances()[str(self.cash)],'450.00')
        self.write('transfers/'+str(move['id']),dict(transfer_version=1),'DELETE',409)
        self.write('transfers/'+str(move['id']),dict(transfer_version=2),'DELETE')
        self.assertEqual(self.balances()[str(self.cash)],'300.00')
        self.write('transfers',dict(month='2026-10',source_id=self.cash,destination_id=self.cash,amount='1'),'POST',422)
        self.setting('storage',self.etf,name='ETF',is_active=False)
        self.move('50')
        self.write('transfers',dict(month='2026-10',source_id=self.cash,destination_id=self.etf,amount='1'),'POST',422)

    def test_currency_correction_transfers_history_and_precision_rollback(self):
        self.setup_plan();self.move('0.01',note='=安全文本')
        def correct(code):
            preview=self.call('GET',f'/api/budget/systems/{self.daily}/currency')
            return dict(expected_version=preview['expected_version'],currency_code=code,plans=[dict(id=p['id'],version=p['version']) for p in preview['plans']])
        self.call('PUT',f'/api/budget/systems/{self.daily}/currency',correct('JPY'),422)
        self.assertEqual(self.detail()['row']['currency_code'],'HKD')
        self.call('PUT',f'/api/budget/systems/{self.daily}/currency',correct('CNY'))
        move=self.detail()['row']['transfers'][0]
        self.assertEqual((move['amount'],move['currency_code'],move['version']),('0.01','CNY',2))
        book=load_workbook(BytesIO(self.client.get('/api/budget/overview.xlsx?end_month=2026-10').content))
        self.assertIn('存储剩余',book.sheetnames);self.assertEqual(book['存储调拨']['G2'].value,"'=安全文本")
        self.assertEqual(book['存储调拨']['F2'].value,0.01)

    def test_opening_unallocated_and_prior_to_origin(self):
        self.cash=next(s['id'] for s in self.config['storage'] if s['name']=='活期');self.etf=self.storage
        self.setting('systems',self.daily,name='日常预算',currency_code='HKD',execution_start_month='2026-10',opening_balance='100')
        self.write('transfers',dict(month='2026-09',source_id=None,destination_id=self.etf,amount='20'),'POST',422)
        self.write('transfers',dict(month='2026-10',source_id=None,destination_id=self.etf,amount='20'),'POST',201)
        self.assertEqual(self.balances()['unallocated'],'80.00');self.assertEqual(self.balances()[str(self.etf)],'20.00')
        self.assertFalse(self.detail('2026-09')['row']['complete'])
        version=self.call('GET','/api/budget/config')['version']
        self.call('PUT',f'/api/budget/config/systems/{self.daily}',dict(expected_version=version,name='日常预算',currency_code='CNY'),409)

    def test_storage_migration_backs_up_once_without_assigning_old_expenses(self):
        self.setup_plan();self.transaction(amount='20')
        with database.connect() as c:
            for table in ('budget_storage_defaults','budget_storage_expenses','budget_storage_transfers'):c.execute('DROP TABLE '+table)
        database.initialize_database();database.initialize_database()
        backups=list(database.BACKUP_DIR.glob('finance-pre-storage-balances-*.db'));self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as c:self.assertEqual(c.execute('SELECT total_minor FROM budget_plans').fetchone()[0],100000)
        row=self.detail()['row'];self.assertIsNone(row['default_storage_method_id'])
        self.assertEqual((row['pending_count'],row['automatic_count'],row['automatic_cash']),(0,1,'20.00'))
        with database.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM budget_storage_expenses').fetchone()[0],0)


if __name__=='__main__':unittest.main()

import unittest
from io import BytesIO

from openpyxl import load_workbook
from app import database
import test_budgets


class IncomeAllocationTest(unittest.TestCase):
    setUp = test_budgets.BudgetTest.setUp
    tearDown = test_budgets.BudgetTest.tearDown
    call = test_budgets.BudgetTest.call
    setting = test_budgets.BudgetTest.setting
    transaction = test_budgets.BudgetTest.transaction
    plan = test_budgets.BudgetTest.plan
    item = test_budgets.BudgetTest.item

    def income(self, amount='1000', allocations=None):
        return self.call('POST','/api/incomes',dict(transaction_date='2026-10-05',
            destination_account_id=self.account,category_id=self.categories['income'],amount=amount,
            budget_allocations=allocations),201)

    def item_for(self, amount, system=None, month=None):
        return dict(budget_system_id=system or self.daily,amount=amount,month=month)

    def details(self, source, kind='transaction'):
        return self.call('GET',f'/api/budget/income-allocations/{kind}/{source}')

    def allocate(self, source, items, kind='transaction', code=200, **kwargs):
        return self.call('PUT',f'/api/budget/income-allocations/{kind}/{source}',
            dict(expected_token=self.details(source,kind)['token'],items=items,**kwargs),code)

    def overview(self, month='2026-10'):
        return self.call('GET',f'/api/budget/overview?end_month={month}')

    def test_split_income_multiple_sources_balances_carry_storage_and_export(self):
        other=self.setting('systems',name='留存',currency_code='HKD')['id']
        a=self.income(allocations=[self.item_for('600'),self.item_for('400',other)])
        b=self.income('200',[self.item_for(None)])
        self.assertEqual(b['account_balance'],'1200.00')
        data=self.overview()
        plans={p['budget_system_id']:p for p in data['plans']}
        self.assertEqual(plans[self.daily]['budget_total'],'800.00')
        self.assertEqual(len(plans[self.daily]['funding_sources']),2)
        self.assertEqual(plans[self.daily]['funding_mode'],'income')
        self.transaction(amount='100')
        execution={r['budget_system_id']:r for r in self.overview('2026-11')['execution']['rows']}
        self.assertEqual(execution[self.daily]['closing'],'700.00')
        p=self.call('GET',f'/api/budget/plans/{self.daily}/2026-10/HKD')
        self.plan(currency='HKD',version=p['version'],total='800',items=[self.item('500')])
        response=self.client.put(f'/api/budget/monthly/{self.daily}/2026-10',
            json=dict(plans=[dict(currency_code='HKD',expected_version=p['version']+1,total='801',items=[])]))
        self.assertEqual(response.status_code,422)
        data=self.overview('2026-11')
        storage=next(r for r in data['storage']['rows'] if r['budget_system_id']==self.daily)
        self.assertEqual(storage['total'],'700.00')
        self.assertEqual(sum(float(r['balance']) for r in storage['cells']),700)
        book=load_workbook(BytesIO(self.client.get('/api/budget/overview.xlsx?end_month=2026-11').content))
        self.assertEqual(book['收入预算分配'].max_row,4)
        self.assertEqual(self.details(a['id'])['source']['remaining'],'0.00')

    def test_overallocation_rolls_back_income_and_stale_edit_is_rejected(self):
        before=self.call('GET','/api/accounts')
        body=dict(transaction_date='2026-10-05',destination_account_id=self.account,
            category_id=self.categories['income'],amount='100',budget_allocations=[self.item_for('101')])
        self.call('POST','/api/incomes',body,422)
        self.assertEqual(self.call('GET','/api/accounts'),before)
        self.assertEqual(self.overview()['plans'],[])
        source=self.income()['id']
        stale=self.details(source)['token']
        self.allocate(source,[self.item_for('400')])
        self.call('PUT',f'/api/budget/income-allocations/transaction/{source}',
                  dict(expected_token=stale,items=[self.item_for('500')]),409)
        self.allocate(source,[self.item_for('800'),self.item_for('300',month='2026-11')],code=422)
        self.assertEqual(self.details(source)['source']['occupied'],'400.00')
        self.allocate(source,[])
        self.assertEqual(self.overview()['plans'][0]['budget_total'],'0.00')
        self.assertEqual(self.details(source)['source']['remaining'],'1000.00')

    def test_net_income_and_aa_only_contributes_net_expense(self):
        income=self.income()['id']; expense=self.transaction(amount='200')['id']
        group=self.call('POST','/api/offset-groups',dict(title='净工资',kind='income',
            transaction_ids=[income,expense]),201)['id']
        self.allocate(group,[self.item_for('800')],kind='offset_group')
        self.allocate(group,[self.item_for('801')],kind='offset_group',code=422)
        self.call('GET',f'/api/budget/income-allocations/transaction/{income}',code=422)
        paid=self.transaction(amount='300')['id']; refund=self.income('200')['id']
        aa=self.call('POST','/api/offset-groups',dict(title='AA',kind='expense',
            transaction_ids=[paid,refund]),201)['id']
        self.call('GET',f'/api/budget/income-allocations/offset_group/{aa}',code=422)
        row=next(r for r in self.overview()['execution']['rows'] if r['budget_system_id']==self.daily)
        self.assertEqual((row['added'],row['expense'],row['closing']),('800.00','100.00','700.00'))
        self.call('DELETE',f'/api/offset-groups/{group}',dict(expected_version=1),409)
        self.allocate(group,[],kind='offset_group')
        self.call('DELETE',f'/api/offset-groups/{group}',dict(expected_version=1))

    def test_foreign_currency_full_allocation_precision_and_currency_correction(self):
        self.setting('systems',self.daily,name='日常预算',currency_code='CNY')
        with database.connect() as c:c.execute("UPDATE daily_cny_exchange_rates SET hkd_cny_rate='0.9'")
        source=self.income('1000',[self.item_for(None)])['id']
        self.assertEqual(self.overview()['plans'][0]['budget_total'],'900.00')
        self.assertEqual(self.details(source)['source']['remaining'],'0.00')
        self.allocate(source,[self.item_for('900.01')],code=422)
        data=self.call('GET',f'/api/budget/systems/{self.daily}/currency')
        self.call('PUT',f'/api/budget/systems/{self.daily}/currency',
            dict(expected_version=data['expected_version'],currency_code='HKD',
                 plans=[dict(id=p['id'],version=p['version']) for p in data['plans']]))
        self.assertEqual(self.details(source)['items'][0]['amount'],'900.00')
        self.assertEqual(self.details(source)['source']['remaining'],'100.00')
        with database.connect() as c:c.execute('DELETE FROM daily_cny_exchange_rates')
        # Same-currency allocations remain calculable without FX snapshots.
        self.assertTrue(self.overview()['execution']['rows'][0]['complete'])
        self.setting('systems',name='美元',currency_code='USD')
        usd=self.config['systems'][-1]['id']
        self.allocate(source,[self.item_for('1',usd)],code=422)

    def test_remainder_cross_currency_preview_matches_save_and_replaces_old_amount(self):
        cny=self.setting('systems',name='人民币留存',currency_code='CNY')['id']
        with database.connect() as c:c.execute("UPDATE daily_cny_exchange_rates SET hkd_cny_rate='0.9'")
        source=self.income('1000.01',[self.item_for('400')])['id']
        before_accounts=self.call('GET','/api/accounts')
        detail=self.details(source)
        # Automatic first, explicit last: order must not affect the remainder.
        items=[self.item_for(None,cny),self.item_for('400')]
        preview=self.call('POST',f'/api/budget/income-allocations/transaction/{source}/preview',
                          dict(expected_token=detail['token'],items=items))
        self.assertEqual(preview['items'][0]['amount'],'540.00')
        self.assertEqual((preview['occupied'],preview['remaining']),('1000.00','0.01'))
        self.assertEqual(self.details(source)['token'],detail['token'])
        self.allocate(source,items)
        saved=self.details(source)
        self.assertEqual(saved['source']['remaining'],preview['remaining'])
        self.assertEqual(next(i for i in saved['items'] if i['budget_system_id']==cny)['amount'],'540.00')
        self.assertEqual(self.call('GET','/api/accounts'),before_accounts)
        # One more target cent would overdraw the source; a second save is stable.
        self.allocate(source,[self.item_for('540.01',cny),self.item_for('400')],code=422)
        self.allocate(source,list(reversed(items)))
        self.assertEqual(self.details(source)['source']['remaining'],'0.01')
        self.allocate(source,[self.item_for(None,cny),self.item_for('100')])
        self.assertEqual(next(i for i in self.details(source)['items'] if i['budget_system_id']==cny)['amount'],'810.00')

    def test_remainder_income_atomic_save_and_invalid_remainders(self):
        other=self.setting('systems',name='留存',currency_code='HKD')['id']
        source=self.income('1000',[self.item_for('300'),self.item_for(None,other)])['id']
        self.assertEqual(self.details(source)['source']['remaining'],'0.00')
        self.assertEqual(next(i for i in self.details(source)['items'] if i['budget_system_id']==other)['amount'],'700.00')
        original=self.details(source)['token']
        for items in ([self.item_for(None),self.item_for(None,other)],
                      [self.item_for('1000'),self.item_for(None,other)],
                      [self.item_for('1001'),self.item_for(None,other)]):
            self.allocate(source,items,code=422)
            self.call('POST',f'/api/budget/income-allocations/transaction/{source}/preview',
                      dict(expected_token=original,items=items),422)
            self.assertEqual(self.details(source)['token'],original)
        accounts=self.call('GET','/api/accounts')
        self.call('POST','/api/incomes',dict(transaction_date='2026-10-05',
            destination_account_id=self.account,category_id=self.categories['income'],amount='100',
            budget_allocations=[self.item_for('100'),self.item_for(None,other)]),422)
        self.assertEqual(self.call('GET','/api/accounts'),accounts)

    def test_remainder_missing_fx_tiny_balance_and_zero_decimal_currency(self):
        usd=self.setting('systems',name='美元',currency_code='USD')['id']
        source=self.income('1')['id']
        # Source remainder 0.01 HKD cannot buy the minimum 0.01 USD at USD/HKD=7.
        self.allocate(source,[self.item_for('0.99'),self.item_for(None,usd)],code=422)
        with database.connect() as c:c.execute('DELETE FROM daily_cny_exchange_rates')
        self.allocate(source,[self.item_for(None,usd)],code=422)
        self.assertEqual(self.details(source)['items'],[])
        yen=self.setting('systems',name='日元',currency_code='JPY')['id']
        with database.connect() as c:
            account=c.execute("""INSERT INTO accounts(code,name,account_class,account_type,currency_code,opening_balance_date)
                VALUES ('YEN','日元','asset','cash','JPY','2026-01-01')""").lastrowid
        self.account=account
        jpy=self.income('101',[self.item_for('40',yen),self.item_for(None,yen,'2026-11')])['id']
        self.assertEqual([i['amount'] for i in self.details(jpy)['items']],['40','61'])
        self.assertEqual(self.details(jpy)['source']['remaining'],'0')

    def test_historical_plan_replacement_preserves_storage_and_no_double_count(self):
        p=self.plan(currency='HKD',total='600',items=[self.item('400')])
        source=self.income()['id']
        self.allocate(source,[self.item_for('600')],code=409)
        self.allocate(source,[self.item_for('600')],replace_legacy_plan_ids=[p['id']])
        data=self.overview();plan=data['plans'][0]
        self.assertEqual((plan['budget_total'],plan['allocated']),('600.00','400.00'))
        self.assertEqual(plan['id'],p['id'])
        self.assertEqual(data['execution']['rows'][0]['added'],'600.00')
        revisions=self.call('GET',f"/api/budget/plans/{self.daily}/2026-10/HKD/revisions")
        self.assertTrue(any(r['plan'].get('funding_mode')=='legacy' for r in revisions))

    def test_source_correction_marks_balance_incomplete_and_can_be_repaired(self):
        source=self.income(allocations=[self.item_for('800')])['id']
        tx=self.call('GET',f'/api/transactions/{source}')
        self.call('PUT',f'/api/transactions/{source}',dict(
            transaction_date=tx['transaction_date'],transaction_type='income',
            destination_account_id=self.account,inflow_amount='500',category_id=self.categories['income']))
        row=self.overview()['execution']['rows'][0]
        self.assertFalse(row['complete']);self.assertIsNone(row['closing'])
        self.assertIsNone(row['available'])
        self.assertEqual(row['expense'],'0.00')
        self.allocate(source,[self.item_for('500')])
        self.assertEqual(self.overview()['execution']['rows'][0]['closing'],'500.00')

    def test_budget_search_keeps_original_and_sums_only_assigned_portion(self):
        other=self.setting('systems',name='专项',currency_code='HKD')['id']
        source=self.income(allocations=[self.item_for('600'),self.item_for('400',other)])['id']
        data=self.call('GET',f'/api/transactions/search?budget_system_id={other}&transaction_types=income')
        self.assertEqual(data['pagination']['total'],1)
        self.assertEqual(data['transactions'][0]['inflow_amount'],'1000.00')
        self.assertEqual(data['summary']['budget_income_allocations'],[dict(currency_code='HKD',amount='400.00')])
        net=self.call('GET',f'/api/personal-cashflow/search?budget_system_id={other}&transaction_types=income&start_date=2026-10-01&end_date=2026-10-31')
        self.assertEqual(next(v for v in net['summary']['valuations'] if v['currency_code']=='HKD')['income'],'400.00')
        self.assertEqual(self.call('GET',f'/api/transactions/{source}')['inflow_amount'],'1000.00')

    def test_migration_known_binding_and_idempotence_keeps_unbound_plan(self):
        source=self.income()['id']
        preview=self.call('GET',f'/api/budget/funding-sources?entry_type=transaction&entry_id={source}')
        p=self.plan(currency='HKD',total='600',items=[],funding=dict(entry_type='transaction',
            entry_id=source,expected_token=preview['token']))
        self.plan(month='2026-09',currency='HKD',total='50',items=[])
        with database.connect() as c:
            c.execute("DELETE FROM app_settings WHERE key='income_allocations_initialized'")
            c.execute('DROP TABLE budget_income_allocations')
        database.initialize_database()
        self.assertEqual(self.details(source)['items'][0]['amount'],'600.00')
        self.assertEqual(self.overview()['plans'][0]['funding_mode'],'legacy')
        self.assertEqual(self.overview()['plans'][1]['funding_mode'],'income')
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-income-allocations-*.db'))),1)
        database.initialize_database()
        self.assertEqual(len(self.details(source)['items']),1)
        with database.connect() as c:
            self.assertIsNone(c.execute('PRAGMA foreign_key_check').fetchone())
            self.assertEqual(c.execute('SELECT total_minor FROM budget_plans WHERE id=?',(p['id'],)).fetchone()[0],60000)

    def test_preview_is_read_only_and_group_query_does_not_double_count(self):
        first=self.income('600')['id'];second=self.income('400')['id']
        group=self.call('POST','/api/offset-groups',dict(title='分两次收到工资',kind='income',
            transaction_ids=[first,second]),201)['id']
        detail=self.details(group,'offset_group')
        preview=self.call('POST',f'/api/budget/income-allocations/offset_group/{group}/preview',
            dict(expected_token=detail['token'],items=[self.item_for('1100')]))
        self.assertTrue(preview['overallocated'])
        self.assertEqual(self.overview()['plans'],[])
        self.assertEqual(self.details(group,'offset_group')['token'],detail['token'])
        self.allocate(group,[self.item_for('300')],kind='offset_group')
        data=self.call('GET',f'/api/transactions/search?budget_system_id={self.daily}&transaction_types=income&limit=1')
        self.assertEqual(data['pagination']['total'],2)
        self.assertEqual(data['summary']['budget_income_allocations'][0]['amount'],'300.00')
        data=self.call('GET',f'/api/personal-cashflow/search?budget_system_id={self.daily}&start_date=2026-10-01&end_date=2026-10-31')
        self.assertEqual(data['pagination']['total'],1)
        self.assertEqual(next(r for r in data['summary']['valuations'] if r['currency_code']=='HKD')['income'],'300.00')


if __name__=='__main__':
    unittest.main()

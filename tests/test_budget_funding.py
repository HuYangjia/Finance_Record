import unittest
from io import BytesIO
from urllib.parse import urlencode

from openpyxl import load_workbook
from app import database
import test_budgets


class FundingTest(unittest.TestCase):
    setUp = test_budgets.BudgetTest.setUp
    tearDown = test_budgets.BudgetTest.tearDown
    call = test_budgets.BudgetTest.call
    setting = test_budgets.BudgetTest.setting
    transaction = test_budgets.BudgetTest.transaction

    def source(self, entry, kind='transaction', **kwargs):
        return self.call('GET','/api/budget/funding-sources?'+urlencode(dict(entry_type=kind,entry_id=entry,**kwargs)))

    def save(self, amount, source=None, month='2026-09', system=None, version=0, code='HKD', **kwargs):
        binding=None if source is None else {k:source[k] for k in ('entry_type','entry_id')}
        if binding: binding['expected_token']=source['token']
        return self.call('PUT',f'/api/budget/monthly/{system or self.daily}/{month}',dict(plans=[dict(currency_code=code,total=amount,expected_version=version,items=[],funding=binding)]),**kwargs)

    def test_shared_source_cap_across_months_and_budgets_release_and_history(self):
        income=self.transaction(kind='income',amount='1000')['id']
        before=self.call('GET','/api/accounts')
        first=self.save('400',self.source(income))['plans'][0]
        other=self.setting('systems',name='其他预算',currency_code='HKD')['id']
        self.save('300',self.source(income),month='2026-10',system=other)
        available=self.source(income,currency_code='HKD',total='301')
        self.assertEqual(available['available'],'300.00'); self.assertFalse(available['can_bind'])
        response=self.client.put(f'/api/budget/monthly/{self.daily}/2026-11',json=dict(plans=[dict(currency_code='HKD',total='301',expected_version=0,items=[],funding=dict(entry_type='transaction',entry_id=income,expected_token=available['token']))]))
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.call('GET',f'/api/budget/monthly/{self.daily}/2026-11')['plans'],[])
        self.save('300',self.source(income),month='2026-11')
        # Same-plan save replaces the old charge; versions don't consume more money.
        self.save('400',self.source(income),version=1)
        self.assertEqual(self.source(income)['available'],'0.00')
        self.save('400',None,version=2)
        self.assertEqual(self.source(income)['available'],'400.00')
        history=self.call('GET',f'/api/budget/plans/{self.daily}/2026-09/HKD/revisions')
        self.assertIsNone(history[0]['plan']['funding'])
        self.assertEqual(history[1]['plan']['funding']['entry_id'],income)
        self.assertEqual(self.call('GET','/api/accounts'),before)
        self.assertEqual(first['funding']['net'],'1000.00')

    def test_stale_preview_old_api_preserves_binding_and_reduction_repairs_shortfall(self):
        income=self.transaction(kind='income',amount='1000')['id']
        preview=self.source(income)
        self.save('400',preview)
        response=self.client.put(f'/api/budget/monthly/{self.daily}/2026-10',json=dict(plans=[dict(currency_code='HKD',total='10',expected_version=0,items=[],funding=dict(entry_type='transaction',entry_id=income,expected_token=preview['token']))]))
        self.assertEqual(response.status_code,409)
        self.call('PUT',f'/api/budget/plans/{self.daily}/2026-09/HKD',dict(expected_version=1,total='400',items=[]))
        current=self.call('GET',f'/api/budget/plans/{self.daily}/2026-09/HKD')
        self.assertEqual(current['funding']['entry_id'],income)
        with database.connect() as c:c.execute('UPDATE transactions SET inflow_minor=20000 WHERE id=?',(income,))
        current=self.call('GET',f'/api/budget/plans/{self.daily}/2026-09/HKD')
        self.assertTrue(current['funding']['overallocated'])
        self.assertEqual(current['total'],'400.00')
        self.save('300',self.source(income),version=2)
        self.assertEqual(self.source(income)['remaining'],'-100.00')
        self.save('200',self.source(income),version=3)
        self.assertEqual(self.source(income)['remaining'],'0.00')

    def test_offset_net_excludes_aa_and_members_and_lifecycle_guards(self):
        income=self.transaction(kind='income',amount='1000')['id']
        expense=self.transaction(amount='300')['id']
        group=self.call('POST','/api/offset-groups',dict(title='工资净额',kind='income',transaction_ids=[income,expense]),201)
        source=self.source(group['id'],'offset_group')
        self.assertEqual(source['net'],'700.00')
        self.save('700',source)
        rows=self.call('GET','/api/budget/funding-sources')['sources']
        self.assertEqual([(r['entry_type'],r['entry_id']) for r in rows],[('offset_group',group['id'])])
        self.call('DELETE',f"/api/offset-groups/{group['id']}",dict(expected_version=group['version']),409)
        self.call('PUT',f"/api/offset-groups/{group['id']}",dict(title='改方向',kind='expense',transaction_ids=[income,expense],expected_version=group['version']),409)
        self.save('700',None,version=1)
        self.call('PUT',f"/api/offset-groups/{group['id']}",dict(title='AA 回款',kind='expense',transaction_ids=[income,expense],expected_version=group['version']))
        self.assertEqual(self.call('GET','/api/budget/funding-sources')['total'],0)
        solo=self.transaction(kind='income',amount='100')['id']
        self.save('100',self.source(solo),month='2026-10')
        self.call('DELETE',f'/api/transactions/{solo}',dict(reason='测试删除'),409)
        self.call('POST','/api/offset-groups',dict(title='绑定收入加入事项',kind='income',transaction_ids=[solo]),409)
        self.save('100',None,month='2026-10',version=1)
        self.call('DELETE',f'/api/transactions/{solo}',dict(reason='解除绑定后删除'))

    def test_foreign_currency_rounding_rates_missing_and_currency_change_rollback(self):
        income=self.transaction(kind='income',amount='1000')['id']
        with database.connect() as c:c.execute("UPDATE daily_cny_exchange_rates SET hkd_cny_rate='0.9'")
        self.assertEqual(self.source(income,currency_code='CNY',total='0.01')['requested'],'0.02')
        self.save('450',self.source(income),code='CNY')
        self.assertEqual(self.source(income)['occupied'],'500.00')
        other=self.setting('systems',name='另一港币预算',currency_code='HKD')['id']
        self.save('500',self.source(income),system=other)
        preview=self.call('GET',f'/api/budget/systems/{self.daily}/currency')
        self.call('PUT',f'/api/budget/systems/{self.daily}/currency',dict(expected_version=preview['expected_version'],currency_code='USD',plans=[dict(id=p['id'],version=p['version']) for p in preview['plans']]),422)
        self.assertEqual(self.call('GET',f'/api/budget/monthly/{self.daily}/2026-09')['currency_code'],'CNY')
        with database.connect() as c:c.execute('DELETE FROM daily_cny_exchange_rates')
        source=self.source(income)
        self.assertFalse(source['complete']); self.assertIsNone(source['available'])
        book=load_workbook(BytesIO(self.client.get('/api/budget/overview.xlsx?end_month=2026-09').content))
        self.assertEqual(book['预算总额']['H2'].value,source['title'])
        self.assertIn('汇率',book['预算总额']['I2'].value)

    def test_source_net_changes_and_source_currency_is_stable(self):
        income=self.transaction(kind='income',amount='1000',day='2026-09-01')['id']
        expense=self.transaction(amount='300',day='2026-10-02')['id']
        group=self.call('POST','/api/offset-groups',dict(title='跨月来源',kind='income',transaction_ids=[income,expense]),201)
        self.save('700',self.source(group['id'],'offset_group'))
        extra=self.transaction(amount='100',day='2026-10-03')['id']
        self.call('PUT',f"/api/offset-groups/{group['id']}",dict(title='跨月来源',kind='income',transaction_ids=[income,expense,extra],expected_version=group['version']))
        source=self.source(group['id'],'offset_group')
        self.assertEqual((source['net'],source['remaining']),('600.00','-100.00'))
        overview=self.call('GET','/api/budget/overview?end_month=2026-09')
        self.assertTrue(overview['plans'][0]['funding']['overallocated'])
        self.assertEqual(overview['execution']['rows'][0]['expense'],'0.00')
        self.assertEqual(overview['execution']['rows'][0]['closing'],'700.00')
        self.assertEqual(len(overview['execution']['rows'][0]['funding_warnings']),1)

    def test_migration_backup_once_leaves_existing_plans_unbound(self):
        self.save('100')
        with database.connect() as c:c.execute('DROP TABLE budget_plan_funding')
        database.initialize_database()
        backups=list(database.BACKUP_DIR.glob('finance-pre-budget-funding-*.db'))
        self.assertEqual(len(backups),1)
        with database.open_connection(backups[0]) as c:
            self.assertEqual(c.execute('SELECT total_minor FROM budget_plans').fetchone()[0],10000)
        self.assertIsNone(self.call('GET',f'/api/budget/plans/{self.daily}/2026-09/HKD')['funding'])
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-budget-funding-*.db'))),1)

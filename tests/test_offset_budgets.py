"""Whole-event responsibility, signed income and unchanged atomic ledger."""
from io import BytesIO
import unittest

from openpyxl import load_workbook

from app import database
import test_budgets


class OffsetBudgetTest(unittest.TestCase):
    tearDown = test_budgets.BudgetTest.tearDown
    call = test_budgets.BudgetTest.call
    setting = test_budgets.BudgetTest.setting
    transaction = test_budgets.BudgetTest.transaction

    def setUp(self):
        test_budgets.BudgetTest.setUp(self)
        self.setting('systems', self.daily, name='日常预算', currency_code='HKD')

    def group(self, income='500', expense='800', budget='omit', kind='income'):
        received = self.transaction(kind='income', amount=income)['id']
        paid = self.transaction(amount=expense)['id']
        body = dict(title='事项预算验证', kind=kind, transaction_ids=[received, paid])
        if budget != 'omit':
            body['budget_system_id'] = budget
        return self.call('POST', '/api/offset-groups', body, 201)

    def update(self, group, **values):
        return self.call('PUT', f"/api/offset-groups/{group['id']}", dict(
            title=group['title'], kind=group['kind'], transaction_ids=group['member_transaction_ids'],
            expected_version=group['version'], **values))

    def execution(self, month='2026-10', system=None):
        data = self.call('GET', f'/api/budget/execution?end_month={month}&system_id={system or self.daily}')
        return data['rows'][0]

    def storage_detail(self, group, system=None):
        data = self.call('GET', f'/api/budget/storage/{system or self.daily}/2026-10')
        return data, next(e for e in data['row']['expenses'] if e['entry_id'] == group['id'])

    def allocations(self, group, items):
        path = f"/api/budget/income-allocations/offset_group/{group['id']}"
        detail = self.call('GET', path)
        return self.call('PUT', path, dict(expected_token=detail['token'], items=items))

    def raw_snapshot(self):
        with database.connect() as c:
            return [tuple(r) for r in c.execute('SELECT * FROM transactions ORDER BY id')]

    def test_negative_income_consumes_budget_carries_and_preserves_personal_direction(self):
        group = self.group()
        self.assertEqual((group['kind'], group['budget_role'], group['budget_net_income']),
                         ('income', 'expense', '-300.00'))
        self.assertEqual(group['budget_system_id'], self.daily)
        row = self.execution()
        self.assertEqual((row['added'], row['expense'], row['closing']), ('0.00', '300.00', '-300.00'))
        self.assertEqual(self.execution('2026-11')['opening'], '-300.00')
        data, expense = self.storage_detail(group)
        self.assertTrue(expense['net_income_deficit'])
        self.assertEqual((expense['amount'], data['row']['total']), ('300.00', '-300.00'))
        self.call('PUT', f'/api/budget/storage/{self.daily}/expense', dict(
            expected_version=data['config']['version'], entry_type='offset_group', entry_id=group['id'],
            expected_entry_token=expense['token'], storage_method_id=self.storage_id()))
        self.assertEqual(self.storage_detail(group)[1]['storage_method_id'], self.storage_id())
        query = self.call('GET', f'/api/personal-cashflow/search?budget_system_id={self.daily}'
                          '&transaction_types=income&start_date=2026-10-01&end_date=2026-10-31')
        self.assertEqual(query['pagination']['total'], 1)
        self.assertEqual(query['summary']['valuations'][1]['income'], '-300.00')
        book = load_workbook(BytesIO(self.client.get('/api/budget/overview.xlsx?end_month=2026-10').content))
        self.assertEqual(list(book['预算执行'].values)[1][7:9], (300, -300))

    def storage_id(self):
        return next(s['id'] for s in self.config['storage'] if s['name'] == 'ETF')

    def test_explicit_budget_moves_whole_event_and_omitted_field_preserves_it(self):
        other = self.setting('systems', name='专项', currency_code='HKD')['id']
        group = self.group()
        before = self.raw_snapshot()
        balances = self.call('GET', '/api/accounts')
        group = self.update(group, budget_system_id=other)
        self.assertEqual(group['budget_explicit_system_id'], other)
        self.assertEqual(self.execution(system=other)['expense'], '300.00')
        self.assertEqual(self.execution()['expense'], '0.00')
        group = self.update(group)
        self.assertEqual(group['budget_explicit_system_id'], other)
        group = self.update(group, budget_system_id=None)
        self.assertIsNone(group['budget_explicit_system_id'])
        self.assertEqual(self.execution()['expense'], '300.00')
        self.assertEqual(self.raw_snapshot(), before)
        self.assertEqual(self.call('GET', '/api/accounts'), balances)

    def test_expense_and_surplus_refund_use_event_override(self):
        other = self.setting('systems', name='随心花', currency_code='HKD')['id']
        group = self.group(income='120', expense='100', budget=other, kind='expense')
        self.assertEqual((group['budget_role'], group['budget_system_id']), ('expense', other))
        self.assertEqual(self.execution(system=other)['expense'], '-20.00')
        self.assertEqual(self.execution(system=other)['closing'], '20.00')
        self.assertEqual(self.storage_detail(group, other)[0]['row']['total'], '20.00')

    def test_positive_net_split_uses_real_allocations_and_no_duplicate_spending(self):
        other = self.setting('systems', name='储备', currency_code='HKD')['id']
        group = self.group(income='1000', expense='200', budget=other)
        self.assertEqual(group['budget_role'], 'income')
        self.assertEqual(group['budget_system_name'], '尚未分给预算')
        self.allocations(group, [dict(budget_system_id=self.daily, amount='300'),
                                 dict(budget_system_id=other, amount='500')])
        saved = self.call('GET', f"/api/offset-groups/{group['id']}")
        self.assertEqual(len(saved['income_allocations']), 2)
        self.assertIn('储备', saved['budget_system_name'])
        self.assertEqual((self.execution()['added'], self.execution()['expense']), ('300.00', '0.00'))
        self.assertEqual(self.execution(system=other)['closing'], '500.00')

    def test_income_turning_negative_keeps_allocation_warning_and_known_deficit(self):
        group = self.group(income='1000', expense='200')
        self.allocations(group, [dict(budget_system_id=self.daily, amount='800')])
        member = next(m for m in group['members'] if m['transaction_type'] == 'expense')
        self.call('PUT', f"/api/transactions/{member['id']}", dict(transaction_date='2026-10-01',
            transaction_type='expense', source_account_id=self.account, outflow_amount='1200',
            category_id=self.categories['expense']))
        row = self.execution()
        self.assertFalse(row['complete'])
        self.assertIsNone(row['closing'])
        self.assertEqual(row['known_expense'], '200.00')
        self.assertTrue(row['funding_warnings'])
        query = self.call('GET', f'/api/personal-cashflow/search?budget_system_id={self.daily}'
                          '&start_date=2026-10-01&end_date=2026-10-31')
        self.assertFalse(query['summary']['budget_complete'])
        self.assertEqual(query['summary']['valuations'][1]['income'], '-200.00')
        self.allocations(group, [])
        self.assertEqual(self.execution()['closing'], '-200.00')
        self.call('PUT', f"/api/transactions/{member['id']}", dict(transaction_date='2026-10-01',
            transaction_type='expense', source_account_id=self.account, outflow_amount='100',
            category_id=self.categories['expense']))
        self.allocations(group, [dict(budget_system_id=self.daily, amount='900')])
        self.assertEqual(self.execution()['closing'], '900.00')

    def test_mixed_loss_needs_explicit_responsibility_in_execution_and_query(self):
        other = self.setting('systems', name='专项', currency_code='HKD')['id']
        group = self.group(income='50', expense='100')
        extra = self.transaction(amount='20', budget=other)['id']
        group = self.call('PUT', f"/api/offset-groups/{group['id']}", dict(title=group['title'],
            kind='income', transaction_ids=group['member_transaction_ids']+[extra], expected_version=group['version']))
        self.assertTrue(group['budget_mixed'])
        for system in (self.daily, other):
            self.assertFalse(self.execution(system=system)['complete'])
            query = self.call('GET', f'/api/personal-cashflow/search?budget_system_id={system}'
                '&start_date=2026-10-01&end_date=2026-10-31')
            self.assertTrue(query['budget_warnings'])
            self.assertFalse(query['summary']['budget_complete'])
        self.update(group, budget_system_id=other)
        self.assertEqual(self.execution(system=other)['closing'], '-70.00')
        self.assertTrue(self.execution()['complete'])

    def test_missing_fx_never_silently_drops_potential_deficit(self):
        group = self.group(income='50', expense='100')
        with database.connect() as c:
            c.execute("UPDATE accounts SET currency_code='USD' WHERE id=?", (self.other_account,))
        extra = self.call('POST', '/api/incomes', dict(transaction_date='2026-10-01',
            amount='1', destination_account_id=self.other_account, category_id=self.categories['income']), 201)['id']
        with database.connect() as c:
            c.execute('DELETE FROM daily_cny_exchange_rates')
        group = self.call('PUT', f"/api/offset-groups/{group['id']}", dict(title=group['title'],
            kind='income', transaction_ids=group['member_transaction_ids']+[extra], expected_version=group['version']))
        self.assertEqual(group['budget_role'], 'unresolved')
        self.assertFalse(self.execution()['complete'])
        self.assertIsNone(self.storage_detail(group)[0]['row']['total'])
        with database.connect() as c:
            c.execute("INSERT INTO daily_cny_exchange_rates(rate_date,usd_cny_rate,hkd_cny_rate,source,market_date) VALUES ('2026-10-01','7','1','test','2026-10-01')")
        self.assertEqual(self.execution()['closing'], '-43.00')

    def test_usd_source_sign_and_cny_budget_share_conversion_and_zero_adds_nothing(self):
        cny = self.setting('systems', name='人民币承担', currency_code='CNY')['id']
        with database.connect() as c:
            c.execute("UPDATE accounts SET currency_code='USD' WHERE id=?", (self.other_account,))
        income = self.call('POST', '/api/incomes', dict(transaction_date='2026-10-01', amount='10',
            destination_account_id=self.other_account, category_id=self.categories['income']), 201)['id']
        expense = self.transaction(amount='100')['id']
        group = self.call('POST', '/api/offset-groups', dict(title='跨币种事项', kind='income',
            transaction_ids=[income, expense], budget_system_id=cny), 201)
        source = self.call('GET', f"/api/budget/income-allocations/offset_group/{group['id']}")['source']
        self.assertEqual((group['budget_reference_currency_code'], group['budget_net_income']), ('USD', '-4.29'))
        self.assertEqual(source['net'], group['budget_net_income'])
        self.assertEqual(self.execution(system=cny)['closing'], '-30.00')
        self.assertEqual(self.storage_detail(group, cny)[0]['row']['total'], '-30.00')
        zero = self.group(income='50', expense='50')
        self.assertEqual((zero['budget_role'], zero['budget_net_income']), ('income', '0.00'))
        self.assertEqual(self.execution()['closing'], '0.00')
        self.allocations(zero, [])

    def test_upgrade_backs_up_once_and_preserves_unassigned_existing_groups(self):
        group = self.group()
        before = self.raw_snapshot()
        with database.connect() as c:
            c.execute('ALTER TABLE transaction_offset_groups DROP COLUMN budget_system_id')
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-offset-budget-*.db'))), 1)
        self.assertIsNone(self.call('GET', f"/api/offset-groups/{group['id']}")['budget_explicit_system_id'])
        self.assertEqual(self.raw_snapshot(), before)
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-offset-budget-*.db'))), 1)
        with database.connect() as c:
            self.assertIsNone(c.execute('PRAGMA foreign_key_check').fetchone())

    def test_invalid_budget_stale_update_and_inactive_preservation(self):
        other = self.setting('systems', name='可停用专项', currency_code='HKD')['id']
        group = self.group(budget=other)
        self.call('PUT', f"/api/offset-groups/{group['id']}", dict(title=group['title'], kind='income',
            transaction_ids=group['member_transaction_ids'], expected_version=99, budget_system_id=self.daily), 409)
        self.call('PUT', f"/api/offset-groups/{group['id']}", dict(title=group['title'], kind='income',
            transaction_ids=group['member_transaction_ids'], expected_version=group['version'], budget_system_id=99999), 422)
        self.setting('systems', other, name='可停用专项', is_active=False)
        group = self.update(group, budget_system_id=other)
        self.assertEqual(group['budget_system_id'], other)

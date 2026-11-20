"""Report reuse must preserve snapshot consistency and fresh results after writes."""
import copy
import unittest
from unittest.mock import patch

from app import budget_funding, budgets, database, personal_cashflow
from app.budget_calculation import BudgetCalculation
from app.budget_execution import execution_report
from app.budget_storage import storage_report
import test_budgets
import test_income_allocations


class BudgetCalculationTest(unittest.TestCase):
    setUp = test_budgets.BudgetTest.setUp
    tearDown = test_budgets.BudgetTest.tearDown
    call = test_budgets.BudgetTest.call
    setting = test_budgets.BudgetTest.setting
    transaction = test_budgets.BudgetTest.transaction
    income = test_income_allocations.IncomeAllocationTest.income
    item_for = test_income_allocations.IncomeAllocationTest.item_for
    details = test_income_allocations.IncomeAllocationTest.details
    allocate = test_income_allocations.IncomeAllocationTest.allocate
    overview = test_income_allocations.IncomeAllocationTest.overview

    def daily_row(self, data):
        return next(r for r in data['execution']['rows'] if r['budget_system_id'] == self.daily)

    def correct_amount(self, transaction_id, kind, amount):
        body = dict(transaction_date='2026-10-05', transaction_type=kind,
                    category_id=self.categories[kind])
        if kind == 'income':
            body.update(destination_account_id=self.account, inflow_amount=amount)
        else:
            body.update(source_account_id=self.account, outflow_amount=amount)
        self.call('PUT', f'/api/transactions/{transaction_id}', body)

    def test_report_endpoints_build_the_ledger_once_and_do_not_write(self):
        self.income(allocations=[self.item_for('800')])
        self.transaction(amount='100')
        accounts = self.call('GET', '/api/accounts')
        urls = [
            '/api/budget/overview?end_month=2026-10',
            '/api/budget/overview?start_month=2026-11&end_month=2026-11',
            '/api/budget/execution?start_month=2026-09&end_month=2026-11',
            f'/api/budget/storage/{self.daily}/2026-10',
            '/api/budget/overview.xlsx?end_month=2026-10',
        ]
        for url in urls:
            with self.subTest(url=url), \
                 patch.object(personal_cashflow, 'read_ledger', wraps=personal_cashflow.read_ledger) as read, \
                 patch.object(personal_cashflow, 'build_entries', wraps=personal_cashflow.build_entries) as build, \
                 patch.object(budget_funding, 'FundingCatalog', wraps=budget_funding.FundingCatalog) as funding, \
                 patch.object(budgets, 'config_data', wraps=budgets.config_data) as config:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual((read.call_count, build.call_count, funding.call_count, config.call_count), (1, 1, 1, 1))
        self.assertEqual(self.call('GET', '/api/accounts'), accounts)
        self.assertEqual(self.daily_row(self.overview())['closing'], '700.00')

    def test_shared_reports_match_independent_reports_without_mutating_inputs(self):
        other = self.setting('systems', name='专项', currency_code='CNY')['id']
        income_ids = [self.income('600')['id'], self.income('500')['id']]
        deduction = self.transaction(amount='100')['id']
        source = self.call('POST', '/api/offset-groups', dict(
            title='合并净工资', kind='income', transaction_ids=income_ids+[deduction]), 201)['id']
        self.allocate(source, [self.item_for('500'), self.item_for('200', month='2026-11'),
                               self.item_for('200', other)], kind='offset_group')
        paid = self.transaction(amount='300')['id']
        refund = self.transaction('income', '100', day='2026-11-01')['id']
        self.call('POST', '/api/offset-groups', dict(
            title='跨月 AA', kind='expense', transaction_ids=[paid, refund]), 201)
        loss_income = self.income('50')['id']
        loss_expense = self.transaction(amount='80')['id']
        self.call('POST', '/api/offset-groups', dict(
            title='净亏损', kind='income', transaction_ids=[loss_income, loss_expense]), 201)
        self.transaction(amount='25', day='2026-11-03')
        self.transaction(amount='30', budget=other)

        with database.connect() as c:
            c.execute('BEGIN')
            calculation = BudgetCalculation(c)
            funding = calculation.funding
            inputs = (calculation.ledger, calculation.entries, calculation.expenses,
                      calculation.config, funding.entries, funding.bindings)
            original = copy.deepcopy(inputs)
            for month, filters in [('2026-10', {}), ('2026-11', {}),
                                   ('2026-11', dict(system_id=self.daily)),
                                   ('2026-10', dict(currency_code='CNY')), ('2026-09', {})]:
                with self.subTest(month=month, filters=filters):
                    shared = execution_report(c, month, calculation=calculation, **filters)
                    self.assertEqual(shared, execution_report(c, month, **filters))
                    storage = storage_report(c, month, execution=shared, details=True, calculation=calculation, **filters)
                    self.assertEqual(storage, storage_report(c, month, details=True, **filters))
            source_before = funding.describe('offset_group', source)
            for binding in funding.bindings:
                funding.for_plan(binding['plan_id'])
                funding.plan_sources(binding['plan_id'])
            self.assertEqual(funding.describe('offset_group', source), source_before)
            self.assertEqual(inputs, original)
            with database.connect() as another:
                with self.assertRaises(ValueError):
                    execution_report(another, '2026-10', calculation=calculation)
        self.assertEqual(self.daily_row(self.overview())['closing'], '270.00')
        self.assertEqual(self.daily_row(self.overview('2026-11'))['closing'], '445.00')

    def test_transactions_offsets_and_allocations_refresh_on_the_next_request(self):
        source = self.income(allocations=[self.item_for('800')])['id']
        expense = self.transaction(amount='100')['id']
        self.assertEqual(self.daily_row(self.overview())['closing'], '700.00')
        self.correct_amount(expense, 'expense', '175')
        self.assertEqual(self.daily_row(self.overview())['closing'], '625.00')
        refund = self.income('25')['id']
        self.call('POST', '/api/offset-groups', dict(
            title='部分退回', kind='expense', transaction_ids=[expense, refund]), 201)
        self.assertEqual(self.daily_row(self.overview())['closing'], '650.00')
        self.allocate(source, [self.item_for('700')])
        self.assertEqual(self.daily_row(self.overview())['closing'], '550.00')
        self.correct_amount(source, 'income', '500')
        invalid = self.overview()
        self.assertIsNone(self.daily_row(invalid)['closing'])
        self.assertTrue(invalid['plans'][0]['funding_sources'][0]['overallocated'])
        self.allocate(source, [self.item_for('400')])
        self.assertEqual(self.daily_row(self.overview())['closing'], '250.00')

    def test_rates_storage_defaults_and_transfers_refresh_without_stale_results(self):
        self.setting('systems', self.daily, name='日常预算', currency_code='CNY')
        self.income(allocations=[self.item_for('800')])
        self.transaction(amount='100')
        plan = self.overview()['plans'][0]
        self.call('PUT', f'/api/budget/plans/{self.daily}/2026-10/CNY', dict(
            expected_version=plan['version'], total='800', items=[dict(storage_method_id=self.storage, amount='300')]))
        self.assertEqual(self.daily_row(self.overview())['closing'], '700.00')
        with database.connect() as c:
            c.execute("UPDATE daily_cny_exchange_rates SET hkd_cny_rate='0.9'")
        self.assertEqual(self.daily_row(self.overview())['closing'], '710.00')

        def balances():
            result = self.call('GET', f'/api/budget/storage/{self.daily}/2026-10')
            return {cell['key']:cell['balance'] for cell in result['row']['cells']}

        self.assertEqual(balances()['unallocated'], '410.00')
        version = self.call('GET', '/api/budget/config')['version']
        self.call('PUT', f'/api/budget/storage/{self.daily}/default', dict(
            expected_version=version, storage_method_id=self.storage))
        self.assertEqual(balances()[str(self.storage)], '210.00')
        version = self.call('GET', '/api/budget/config')['version']
        self.call('POST', f'/api/budget/storage/{self.daily}/transfers', dict(
            expected_version=version, month='2026-10', source_id=self.storage,
            destination_id=None, amount='100'), 201)
        self.assertEqual(balances()[str(self.storage)], '110.00')
        self.assertEqual(balances()['unallocated'], '600.00')
        with database.connect() as c:
            c.execute('DELETE FROM daily_cny_exchange_rates')
        missing = self.overview()
        self.assertIsNone(self.daily_row(missing)['expense'])
        self.assertFalse(missing['plans'][0]['funding_sources'][0]['complete'])
        self.assertIsNone(next(r for r in missing['storage']['rows'] if r['budget_system_id'] == self.daily)['total'])

    def test_concurrent_write_cannot_mix_snapshots_and_next_request_sees_it(self):
        source = self.income(allocations=[self.item_for('800')])['id']
        expense = self.transaction(amount='100')['id']
        original_read = personal_cashflow.read_ledger

        def read_then_write(c):
            ledger = original_read(c)
            # Independent committed writer in WAL mode, after report reads began.
            with database.connect() as writer:
                writer.execute('UPDATE transactions SET inflow_minor=50000 WHERE id=?', (source,))
                writer.execute('UPDATE transactions SET outflow_minor=20000 WHERE id=?', (expense,))
            return ledger

        with patch.object(personal_cashflow, 'read_ledger', side_effect=read_then_write) as read:
            before = self.overview()
            self.assertEqual(read.call_count, 1)
        self.assertEqual(self.daily_row(before)['closing'], '700.00')
        self.assertFalse(before['plans'][0]['funding_sources'][0]['overallocated'])
        after = self.overview()
        self.assertEqual(self.daily_row(after)['expense'], '200.00')
        self.assertIsNone(self.daily_row(after)['closing'])
        self.assertTrue(after['plans'][0]['funding_sources'][0]['overallocated'])

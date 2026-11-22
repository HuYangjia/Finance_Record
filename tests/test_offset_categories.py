"""Mixed expense categories: precise shares with the same event and actual ledger."""
from decimal import Decimal
import unittest

from openpyxl import load_workbook

from app import database
from app.main import build_excel_export
from app.offset_categories import rounded_parts
import test_budgets


class OffsetCategoryTest(unittest.TestCase):
    tearDown = test_budgets.BudgetTest.tearDown
    call = test_budgets.BudgetTest.call
    setting = test_budgets.BudgetTest.setting

    def setUp(self):
        test_budgets.BudgetTest.setUp(self)
        self.setting('systems', self.daily, name='日常预算', currency_code='HKD')
        with database.connect() as c:
            self.cats = [r[0] for r in c.execute("SELECT id FROM categories WHERE kind='expense' ORDER BY id LIMIT 3")]
            self.names = {r['id']: r['name'] for r in c.execute('SELECT id,name FROM categories')}

    def tx(self, amount, category=None, kind='expense', day='2026-10-01', account=None):
        return self.call('POST', '/api/expenses' if kind == 'expense' else '/api/incomes', dict(
            transaction_date=day, amount=amount, category_id=category or self.categories[kind],
            **{'source_account_id' if kind == 'expense' else 'destination_account_id': account or self.account}), 201)['id']

    def group(self, amounts=('100', '50'), refund='30', **kwargs):
        ids = [self.tx(amount, self.cats[i]) for i, amount in enumerate(amounts)]
        if refund is not None:
            ids.append(self.tx(refund, kind='income'))
        return self.call('POST', '/api/offset-groups', dict(title='混合分类验证', kind='expense',
            transaction_ids=ids, **kwargs), 201)

    def update(self, group, **kwargs):
        body = dict(title=group['title'], kind=group['kind'], transaction_ids=group['member_transaction_ids'],
                    expected_version=group['version'])
        body.update(kwargs)
        return self.call('PUT', f"/api/offset-groups/{group['id']}", body)

    def query(self, categories=None, analysis=False, **extra):
        from urllib.parse import urlencode
        args = dict(start_date='2026-10-01', end_date='2026-10-31', **extra)
        args['transaction_type' if analysis else 'transaction_types'] = 'expense'
        if categories:
            args['category_ids'] = categories
        path = '/api/personal-cashflow/' + ('analysis' if analysis else 'search')
        return self.call('GET', path + '?' + urlencode(args, doseq=True))

    def test_default_proportional_filter_keeps_members_one_event_and_union(self):
        group = self.group()
        self.assertEqual((group['category_allocation_mode'], group['category_name']), ('proportional', '多分类'))
        self.assertEqual([r['amount'] for r in group['category_breakdown']['HKD']], ['80.00', '40.00'])
        for cats, expected in (([self.cats[0]], '80.00'), ([self.cats[1]], '40.00'), (self.cats[:2], '120.00')):
            result = self.query(cats)
            self.assertEqual((result['pagination']['total'], result['summary']['entry_count']), (1, 1))
            self.assertEqual(result['summary']['valuations'][1]['expense'], expected)
            entry = result['entries'][0]
            self.assertEqual(len(entry['members']), 3)
            self.assertTrue(entry['category_basis'])
            self.assertEqual(entry['whole_valuations']['HKD']['amount'], '120.00')
        self.assertEqual(self.query([self.cats[2]])['pagination']['total'], 0)
        listing = self.call('GET', f'/api/offset-groups?category_id={self.cats[1]}')
        self.assertEqual(listing['groups'][0]['valuations']['HKD']['amount'], '40.00')

    def test_category_chart_daily_histogram_account_and_counts_are_consistent(self):
        self.group()
        data = self.query(analysis=True)
        distribution = {r['name']: r for r in data['category_distribution']}
        self.assertEqual(distribution[self.names[self.cats[0]]]['amount'], '80.00')
        self.assertEqual(distribution[self.names[self.cats[1]]]['amount'], '40.00')
        self.assertEqual(data['daily_totals'], [dict(transaction_date='2026-10-01', amount='120.00', count=1)])
        self.assertEqual(data['statistics']['converted_transaction_count'], 1)
        self.assertEqual(data['account_distribution'][0]['count'], 1)
        self.assertEqual(sum(b['count'] for b in data['histogram']), 1)
        selected = self.query([self.cats[1]], analysis=True)
        self.assertEqual(selected['statistics']['total_amount'], '40.00')
        self.assertEqual(len(selected['category_distribution']), 1)

    def test_targeted_refund_and_shared_coupon_use_original_weights(self):
        group = self.group()
        a, b, returned = group['member_transaction_ids']
        extra = self.tx('30', kind='income')
        group = self.update(group, transaction_ids=[a, b, returned, extra], category_targets=[
            dict(refund_transaction_id=returned, expense_transaction_id=b)])
        self.assertEqual([p['amount'] for p in group['category_breakdown']['HKD']], ['80.00', '10.00'])
        # Old clients omit fields: editing a title must not erase explicit refund targets.
        group = self.update(group, title='保留指定冲减')
        self.assertEqual(group['category_targets'][0]['expense_transaction_id'], b)
        group = self.update(group, category_targets=[])
        self.assertEqual([p['amount'] for p in group['category_breakdown']['HKD']], ['60.00', '30.00'])

    def test_negative_and_zero_net_keep_signed_categories_and_hide_percentages(self):
        group = self.group(refund='200')
        refund = group['member_transaction_ids'][-1]
        group = self.update(group, category_targets=[dict(refund_transaction_id=refund,
            expense_transaction_id=group['member_transaction_ids'][1])])
        self.assertEqual([p['amount'] for p in group['category_breakdown']['HKD']], ['100.00', '-150.00'])
        data = self.query(analysis=True)
        self.assertEqual(data['statistics']['total_amount'], '-50.00')
        self.assertTrue(all('amount_share' not in p for p in data['category_distribution']))
        group = self.update(group, category_targets=[])
        values = group['category_breakdown']['HKD']
        self.assertEqual(sum(Decimal(r['amount']) for r in values), Decimal('-50'))
        zero = self.group(refund='150')
        self.assertEqual([p['amount'] for p in zero['category_breakdown']['HKD']], ['0.00', '0.00'])

    def test_rounding_conserves_event_and_aggregated_category_display(self):
        group = self.group(amounts=('1', '1', '1'), refund='2')
        values = [p['amount'] for p in group['category_breakdown']['HKD']]
        self.assertEqual(values, ['0.34', '0.33', '0.33'])
        self.assertEqual(sum(map(Decimal, values)), Decimal(group['valuations']['HKD']['amount']))
        distribution = {r['name']: r['amount'] for r in self.query(analysis=True)['category_distribution']}
        self.assertEqual(distribution, {p['category_name']: p['amount'] for p in group['category_breakdown']['HKD']})
        self.group(amounts=('1', '1', '1'), refund='2')
        data = self.query(analysis=True)
        self.assertEqual(sum(Decimal(p['amount']) for p in data['category_distribution']),
                         Decimal(data['statistics']['total_amount']))
        for amounts in (['-.333', '-.333', '-.334'], ['.005', '.005'], ['-.005', '-.005']):
            values = list(map(Decimal, amounts))
            self.assertEqual(sum(rounded_parts(values)), sum(values).quantize(Decimal('.01')))

    def test_fx_on_original_dates_and_missing_rate_preserve_complete_coverage_rules(self):
        with database.connect() as c:
            c.execute("UPDATE accounts SET currency_code='CNY' WHERE id=?", (self.other_account,))
            c.execute("UPDATE daily_cny_exchange_rates SET hkd_cny_rate='0.5'")
            c.execute("INSERT INTO daily_cny_exchange_rates(rate_date,usd_cny_rate,hkd_cny_rate,source,market_date) VALUES ('2026-10-05','7','1','test','2026-10-05')")
        a = self.tx('100', self.cats[0])
        b = self.tx('50', self.cats[1], account=self.other_account)
        refund = self.tx('20', kind='income', day='2026-10-05')
        group = self.call('POST', '/api/offset-groups', dict(title='逐日汇率', kind='expense', transaction_ids=[a,b,refund]), 201)
        self.assertEqual([r['amount'] for r in group['category_breakdown']['CNY']], ['40.00', '40.00'])
        self.assertEqual([r['amount'] for r in group['category_breakdown']['HKD']], ['90.00', '90.00'])
        with database.connect() as c:
            c.execute('DELETE FROM daily_cny_exchange_rates')
        result = self.query([self.cats[0]])
        self.assertFalse(result['summary']['valuations'][1]['complete'])
        self.assertIsNone(result['entries'][0]['category_breakdown']['HKD'][0]['amount'])

    def test_mode_changes_preserve_atomic_ledger_budget_storage_and_export_all_parts(self):
        group = self.group(category_allocation_mode='anchor')
        with database.connect() as c:
            original = [tuple(r) for r in c.execute('SELECT * FROM transactions ORDER BY id')]
            for cid in self.cats[:2]:
                c.execute("INSERT INTO monthly_budgets(budget_month,category_id,currency_code,amount_minor) VALUES ('2026-10-01',?,'HKD',10000)", (cid,))
        accounts = self.call('GET', '/api/accounts')
        execution = self.call('GET', '/api/budget/execution?end_month=2026-10')
        storage = self.call('GET', f'/api/budget/storage/{self.daily}/2026-10')['row']['total']
        self.assertEqual(self.query([self.cats[0]])['summary']['valuations'][1]['expense'], '120.00')
        group = self.update(group, category_allocation_mode='proportional')
        self.assertEqual(self.call('GET', '/api/budget/execution?end_month=2026-10'), execution)
        self.assertEqual(self.call('GET', f'/api/budget/storage/{self.daily}/2026-10')['row']['total'], storage)
        self.assertEqual(self.call('GET', '/api/accounts'), accounts)
        with database.connect() as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM transactions ORDER BY id')], original)
        book = load_workbook(build_excel_export('2026-10', 'offset'))
        self.assertEqual(book['冲销事项']['E4'].value, '多分类')
        self.assertEqual([r[9] for r in list(book['冲销分类分摊'].values)[3:]], ['80.00', '40.00'])
        self.assertEqual({r[2]: r[4] for r in list(book['月度预算'].values)[3:]},
                         {self.names[self.cats[0]]: '80.00', self.names[self.cats[1]]: '40.00'})

    def test_invalid_targets_versions_type_changes_and_removal_are_atomic(self):
        group = self.group()
        a, b, refund = group['member_transaction_ids']
        foreign = self.tx('1')
        invalid = [[dict(refund_transaction_id=refund, expense_transaction_id=foreign)],
                   [dict(refund_transaction_id=a, expense_transaction_id=b)],
                   [dict(refund_transaction_id=refund, expense_transaction_id=b)] * 2]
        for targets in invalid:
            self.call('PUT', f"/api/offset-groups/{group['id']}", dict(title=group['title'], kind='expense',
                transaction_ids=[a,b,refund], expected_version=1, category_targets=targets), 422)
        target = [dict(refund_transaction_id=refund, expense_transaction_id=b)]
        group = self.update(group, category_targets=target)
        self.call('PUT', f'/api/transactions/{b}', dict(transaction_date='2026-10-01', transaction_type='income',
            destination_account_id=self.account, inflow_amount='50', category_id=self.categories['income']), 409)
        self.call('PUT', f"/api/offset-groups/{group['id']}", dict(title=group['title'], kind='expense',
            transaction_ids=[a,refund], expected_version=group['version']), 422)
        group = self.update(group, transaction_ids=[a,refund], category_targets=[])
        self.assertEqual(group['category_targets'], [])
        self.assertEqual(len(group['members']), 2)

    def test_original_corrections_recompute_shares_and_backup_migration_is_idempotent(self):
        group = self.group()
        a = group['member_transaction_ids'][0]
        self.call('PUT', f'/api/transactions/{a}', dict(transaction_date='2026-10-01', transaction_type='expense',
            source_account_id=self.account, outflow_amount='50', category_id=self.cats[0]))
        saved = self.call('GET', f"/api/offset-groups/{group['id']}")
        self.assertEqual([r['amount'] for r in saved['category_breakdown']['HKD']], ['35.00', '35.00'])
        with database.connect() as c:
            c.execute('DROP TABLE transaction_offset_category_targets')
            c.execute('ALTER TABLE transaction_offset_groups DROP COLUMN category_allocation_mode')
        database.initialize_database()
        backups = list(database.BACKUP_DIR.glob('finance-pre-offset-categories-*.db'))
        self.assertEqual(len(backups), 1)
        with database.open_connection(backups[0]) as c:
            self.assertNotIn('category_allocation_mode', {r[1] for r in c.execute('PRAGMA table_info(transaction_offset_groups)')})
        saved = self.call('GET', f"/api/offset-groups/{group['id']}")
        self.assertEqual(saved['category_allocation_mode'], 'anchor')
        self.assertNotIn('category_breakdown', saved)
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-offset-categories-*.db'))), 1)
        with database.connect() as c:
            self.assertIsNone(c.execute('PRAGMA foreign_key_check').fetchone())

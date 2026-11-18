"""Original-currency statistics and composable currency filters use isolated ledgers."""
import unittest

from openpyxl import load_workbook

from app import database
from app.main import build_excel_export
import test_budgets


class NativeCurrencyTest(unittest.TestCase):
    setUp = test_budgets.BudgetTest.setUp
    tearDown = test_budgets.BudgetTest.tearDown
    call = test_budgets.BudgetTest.call
    setting = test_budgets.BudgetTest.setting

    def account_for(self, code):
        with database.connect() as c:
            return c.execute("""INSERT INTO accounts(code,name,account_class,account_type,currency_code,opening_balance_date)
                VALUES (?,?,'asset','bank_demand',?,'2026-01-01')""", ('NATIVE-'+code, code, code)).lastrowid

    def tx(self, kind, amount, account=None, cat=None, day='2026-10-05', **values):
        return self.call('POST', '/api/incomes' if kind == 'income' else '/api/expenses', dict(
            transaction_date=day, amount=amount, category_id=cat or self.categories[kind],
            **{'destination_account_id' if kind == 'income' else 'source_account_id':account or self.account}, **values), 201)['id']

    def group(self, ids, kind='expense'):
        return self.call('POST', '/api/offset-groups', dict(title='原币统计验证', kind=kind, transaction_ids=ids), 201)

    def search(self, code=None, basis='offset', **values):
        from urllib.parse import urlencode
        args = dict(start_date='2026-10-01', end_date='2026-10-31', **values)
        if code:
            args['currency_code'] = code
        path = '/api/personal-cashflow/search' if basis == 'offset' else '/api/transactions/search'
        return self.call('GET', path+'?'+urlencode(args, doseq=True))

    def analysis(self, code, basis='offset', kind='expense', **values):
        from urllib.parse import urlencode
        args = dict(start_date='2026-10-01', end_date='2026-10-31', transaction_type=kind, currency_code=code, **values)
        path = '/api/personal-cashflow/analysis' if basis == 'offset' else '/api/transactions/analysis'
        return self.call('GET', path+'?'+urlencode(args, doseq=True))

    def test_dashboard_separates_original_currencies_from_converted_reference(self):
        cny = self.account_for('CNY')
        a = self.tx('expense','100',cny); b = self.tx('income','70',cny)
        self.tx('expense','200')
        self.group([a,b])
        raw = self.call('GET','/api/dashboard?month=2026-10&basis=raw')
        net = self.call('GET','/api/dashboard?month=2026-10&basis=offset')
        native = {r['currency_code']:r for r in net['personal_cash_flow']['native_totals']}
        self.assertEqual(native['CNY']['expense'],'30.00')
        self.assertEqual(native['HKD']['expense'],'200.00')
        self.assertEqual({r['currency_code']:r['balance'] for r in raw['cash_flow']},
                         {code:r['balance'] for code,r in native.items()})
        self.assertEqual(net['personal_cash_flow']['valuations'][0]['expense'],'230.00')
        self.assertEqual(raw['net_worth'],net['net_worth'])
        self.assertEqual(raw['base_currency_totals'],net['base_currency_totals'])

    def test_cross_currency_refund_is_not_converted_into_selected_currency(self):
        cny = self.account_for('CNY')
        group = self.group([self.tx('expense','100'),self.tx('income','80',cny)])
        self.assertEqual({r['currency_code']:r['amount'] for r in group['native_amounts']},
                         {'CNY':'-80.00','HKD':'100.00'})
        for code, expense in [('CNY','-80.00'),('HKD','100.00')]:
            data = self.search(code)
            self.assertEqual(data['pagination']['total'],1)
            self.assertEqual(data['summary']['native_totals'][0]['expense'],expense)
            self.assertEqual(len(data['entries'][0]['members']),2)
            graph = self.analysis(code)
            self.assertEqual((graph['statistics']['total_amount'],graph['currency_code'],graph['amount_scope']),
                             (expense,code,'native'))
        with database.connect() as c:
            c.execute('DELETE FROM daily_cny_exchange_rates')
        self.assertTrue(self.search('CNY')['summary']['native_totals'][0]['complete'])
        self.assertEqual(self.analysis('CNY')['statistics']['total_amount'],'-80.00')

    def test_raw_fx_filter_matches_only_selected_leg_and_account_intersection(self):
        cny = self.account_for('CNY')
        self.call('POST','/api/transfers',dict(transaction_date='2026-10-05',source_account_id=self.account,
            destination_account_id=cny,outflow_amount='100',inflow_amount='90'),201)
        for code, inward, outward in [('CNY','90.00','0.00'),('HKD','0.00','100.00')]:
            data = self.search(code,basis='raw')
            self.assertEqual(data['pagination']['total'],1)
            self.assertEqual(len(data['summary']['raw_totals_by_currency']),1)
            row = data['summary']['raw_totals_by_currency'][0]
            self.assertEqual((row['currency_code'],row['inflow_amount'],row['outflow_amount']),(code,inward,outward))
            self.assertEqual(data['transactions'][0]['outflow_amount'],'100.00')
            self.assertEqual(data['transactions'][0]['inflow_amount'],'90.00')
        self.assertEqual(self.search('CNY','raw',account_id=self.account)['pagination']['total'],0)
        self.assertEqual(self.search('CNY','raw',account_id=cny)['pagination']['total'],1)
        data = self.analysis('CNY','raw','fx_exchange',account_id=cny)
        self.assertEqual((data['statistics']['total_amount'],data['currency_code']),('90.00','CNY'))

    def test_category_filter_and_currency_filter_keep_native_shares(self):
        cny = self.account_for('CNY')
        with database.connect() as c:
            cats = [r[0] for r in c.execute("SELECT id FROM categories WHERE kind='expense' LIMIT 2")]
        self.group([self.tx('expense','100',cat=cats[0]), self.tx('expense','100',cat=cats[1]),
                    self.tx('income','30',cny)])
        with database.connect() as c:
            c.execute('DELETE FROM daily_cny_exchange_rates')
        data = self.search('CNY',category_ids=[cats[0]])
        self.assertEqual(data['summary']['native_totals'][0]['expense'],'-15.00')
        self.assertTrue(data['entries'][0]['native_currency_code']=='CNY')
        graph = self.analysis('CNY',category_ids=[cats[0]])
        self.assertEqual(graph['statistics']['total_amount'],'-15.00')
        self.assertEqual(len(graph['category_distribution']),1)
        both = self.search('CNY',category_ids=cats)
        self.assertEqual(both['summary']['native_totals'][0]['expense'],'-30.00')
        self.assertEqual(self.search('HKD')['summary']['native_totals'][0]['expense'],'200.00')

    def test_allocated_income_uses_only_selected_budget_share_in_source_currency(self):
        other = self.setting('systems',name='人民币分配',currency_code='CNY')['id']
        with database.connect() as c:
            c.execute("UPDATE daily_cny_exchange_rates SET hkd_cny_rate='0.5'")
        source = self.tx('income','1000',budget_allocations=[dict(budget_system_id=other,amount='400')])
        data = self.search('HKD',budget_system_id=other)
        self.assertEqual(data['summary']['native_totals'][0]['income'],'800.00')
        self.assertEqual(data['summary']['valuations'][0]['income'],'400.00')
        self.assertEqual(self.search('CNY',budget_system_id=other)['pagination']['total'],0)
        self.assertEqual(self.call('GET',f'/api/transactions/{source}')['inflow_amount'],'1000.00')

    def test_missing_mixed_primary_weights_never_fakes_category_completeness(self):
        cny = self.account_for('CNY')
        with database.connect() as c:
            cats = [r[0] for r in c.execute("SELECT id FROM categories WHERE kind='expense' LIMIT 2")]
        self.group([self.tx('expense','100',cat=cats[0]),self.tx('expense','50',cny,cat=cats[1]),self.tx('income','10',cny)])
        with database.connect() as c:
            c.execute('DELETE FROM daily_cny_exchange_rates')
        self.assertEqual(self.search('CNY')['summary']['native_totals'][0]['expense'],'40.00')
        selected = self.search('CNY',category_ids=[cats[0]])['summary']['native_totals'][0]
        self.assertFalse(selected['complete'])
        self.assertIsNone(self.analysis('CNY')['statistics'])

    def test_inactive_and_zero_decimal_currencies_are_searchable_without_fx(self):
        yen = self.account_for('JPY')
        self.tx('expense','123',yen)
        with database.connect() as c:
            c.execute("UPDATE currencies SET is_active=0 WHERE code='JPY'")
            c.execute('DELETE FROM daily_cny_exchange_rates')
        self.assertIn('JPY',[r['code'] for r in self.call('GET','/api/currencies?include_inactive=true')])
        self.assertNotIn('JPY',[r['code'] for r in self.call('GET','/api/currencies')])
        self.assertEqual(self.search('JPY')['summary']['native_totals'][0]['expense'],'123')
        self.assertEqual(self.search('JPY','raw')['summary']['raw_totals_by_currency'][0]['outflow_amount'],'123')
        self.assertEqual(self.analysis('JPY')['currency_code'],'JPY')

    def test_currency_validation_pagination_and_no_matching_currency(self):
        self.tx('expense','10');self.tx('expense','20')
        for basis in ['offset','raw']:
            first = self.search('HKD',basis,limit=1)
            second = self.search('HKD',basis,limit=1,offset=1)
            self.assertEqual(first['pagination']['total'],2)
            self.assertEqual(first['summary'],second['summary'])
            self.assertEqual(self.search('USD',basis)['pagination']['total'],0)
        for path in ['/api/transactions/search','/api/personal-cashflow/search']:
            self.call('GET',path+'?currency_code=INVALID',code=422)

    def test_export_has_native_overview_and_explicit_converted_reference(self):
        cny = self.account_for('CNY')
        self.group([self.tx('expense','100'),self.tx('income','80',cny)])
        book = load_workbook(build_excel_export('2026-10','offset'))
        rows = list(book['月度总览'].values)
        self.assertEqual({r[0]:r[2] for r in rows[3:]},{'CNY':'-80.00','HKD':'100.00'})
        self.assertEqual(book['折算收支参考']['C4'].value,'20.00')
        self.assertIn('原始月度总览',book.sheetnames)

    def test_income_group_preserves_native_balance_by_currency(self):
        cny = self.account_for('CNY')
        self.group([self.tx('income','100'),self.tx('expense','30',cny)],kind='income')
        native = {r['currency_code']:r for r in self.search()['summary']['native_totals']}
        self.assertEqual((native['CNY']['income'],native['CNY']['balance']),('-30.00','-30.00'))
        self.assertEqual((native['HKD']['income'],native['HKD']['balance']),('100.00','100.00'))

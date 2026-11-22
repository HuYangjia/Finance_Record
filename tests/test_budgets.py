import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import database
from app.main import app


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(database, 'DATA_DIR', root/'data'),
                        patch.object(database, 'DATABASE_PATH', root/'data/finance.db'),
                        patch.object(database, 'BACKUP_DIR', root/'backups')]
        for p in self.patches:
            p.start()
        database.initialize_database()
        # Do not start the production lifespan or its exchange-rate worker.
        self.client = TestClient(app)
        self.config = self.call('GET', '/api/budget/config')
        self.daily = self.config['defaults']['expense']
        with database.connect() as c:
            self.account = c.execute("INSERT INTO accounts(code,name,account_class,account_type,currency_code,opening_balance_date) VALUES ('BANK','银行卡','asset','bank_demand','HKD','2026-01-01')").lastrowid
            self.other_account = c.execute("INSERT INTO accounts(code,name,account_class,account_type,currency_code,opening_balance_date) VALUES ('CASH','现金','asset','cash','HKD','2026-01-01')").lastrowid
            self.categories = {kind:c.execute('SELECT id FROM categories WHERE kind=? ORDER BY id LIMIT 1',(kind,)).fetchone()[0] for kind in ('expense','income')}
            c.execute("INSERT INTO daily_cny_exchange_rates(rate_date,usd_cny_rate,hkd_cny_rate,source,market_date) VALUES ('2026-10-01','7','1','test','2026-10-01')")
        self.storage = next(r['id'] for r in self.config['storage'] if r['name']=='ETF')
        self.purpose = next(r['id'] for r in self.config['purposes'] if r['name']=='慈善')

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def call(self, method, path, body=None, code=200):
        response = self.client.request(method,path,json=body)
        self.assertEqual(response.status_code,code,response.text)
        return response.json()

    def setting(self, collection, item_id=None, **values):
        self.config = self.call('GET', '/api/budget/config')
        values['expected_version'] = self.config['version']
        self.config = self.call('PUT' if item_id else 'POST',f'/api/budget/config/{collection}'+(f'/{item_id}' if item_id else ''),values,200 if item_id else 201)
        return next((r for r in self.config[collection] if r['id']==item_id),None) if item_id else max(self.config[collection],key=lambda r:r['id'])

    def item(self, amount='500', **values):
        return dict(storage_method_id=self.storage,purpose_id=self.purpose,amount=amount,title='慈善储备',**values)

    def plan(self, month='2026-10', items=None, version=0, system=None, currency='CNY', **values):
        return self.call('PUT',f'/api/budget/plans/{system or self.daily}/{month}/{currency}',dict(expected_version=version,items=items if items is not None else [self.item()],**values))

    def summary(self, month='2026-11', **values):
        from urllib.parse import urlencode
        query=dict(system_id=self.daily,end_month=month,currency_code='CNY',**values)
        return self.call('GET','/api/budget/summary?'+urlencode(query))

    def transaction(self, kind='expense', amount='100', budget='omit', day='2026-10-01'):
        body=dict(transaction_date=day,amount=amount,category_id=self.categories[kind])
        body['source_account_id' if kind=='expense' else 'destination_account_id']=self.account
        if budget!='omit':body['budget_system_id']=budget
        result=self.call('POST','/api/expenses' if kind=='expense' else '/api/incomes',body,201)
        return self.call('GET',f"/api/transactions/{result['id']}")

    def test_monthly_latest_versions_currency_range_and_no_actual_changes(self):
        before=self.call('GET','/api/accounts')
        self.plan()
        nov=self.plan('2026-11',[self.item('300')])
        self.plan('2026-12',[self.item('200')])
        other=self.setting('systems',name='港币预算',currency_code='HKD')['id']
        self.plan(system=other,currency='HKD',total='777',items=[])
        self.assertEqual(self.summary()['cumulative'],'800.00')
        edited=[dict(id=nov['items'][0]['id'],**self.item('350'))]
        nov=self.plan('2026-11',edited,version=1)
        self.plan('2026-11',edited,version=nov['version'])
        summary=self.summary()
        self.assertEqual((summary['previous'],summary['current'],summary['cumulative']),('500.00','350.00','850.00'))
        self.assertEqual(self.summary(start_month='2026-11')['cumulative'],'350.00')
        self.assertEqual(self.call('GET','/api/accounts'),before)
        self.assertEqual(self.call('GET','/api/transactions/search')['pagination']['total'],0)
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM budget_plans').fetchone()[0],4)

    def test_all_history_and_single_boundary_search_summary_uses_full_filtered_results(self):
        self.transaction(amount='10',day='2026-09-01')
        self.transaction(amount='20',day='2026-10-01')
        self.transaction(kind='income',amount='30',day='2026-10-01')
        special=self.setting('systems',name='专项查询')['id']
        self.transaction(amount='40',budget=special,day='2026-11-01')
        before=self.call('GET','/api/accounts')
        url=f'/api/transactions/search?budget_system_id={self.daily}&transaction_types=expense&limit=1'
        first=self.call('GET',url)
        second=self.call('GET',url+'&offset=1')
        self.assertEqual(len(first['transactions']),1)
        self.assertEqual(first['pagination']['total'],2)
        self.assertEqual(first['summary'],second['summary'])
        self.assertTrue(first['summary']['available'])
        self.assertEqual(first['summary']['raw_totals_by_currency'][0]['outflow_amount'],'30.00')
        for bounds,count,total in [('&start_date=2026-10-01',1,'20.00'),('&end_date=2026-09-30',1,'10.00'),('&start_date=2026-12-01',0,None)]:
            result=self.call('GET',url+bounds)
            self.assertTrue(result['summary']['available'])
            self.assertEqual(result['summary']['transaction_count'],count)
            self.assertEqual(result['pagination']['total'],count)
            if total:
                self.assertEqual(result['summary']['raw_totals_by_currency'][0]['outflow_amount'],total)
        all_rows=self.call('GET','/api/transactions/search?limit=1')['summary']
        self.assertEqual(all_rows['transaction_count'],4)
        self.assertEqual(all_rows['raw_totals_by_currency'][0]['outflow_amount'],'70.00')
        self.assertEqual(all_rows['raw_totals_by_currency'][0]['inflow_amount'],'30.00')
        self.call('GET',url+'&start_date=2026-10-31&end_date=2026-10-01',code=422)
        self.assertEqual(self.call('GET','/api/accounts'),before)

    def test_multi_item_cells_revisions_snapshot_and_existing_parent_amount(self):
        oct_plan=self.plan(items=[self.item('300'),self.item('200')])
        self.assertEqual(self.summary()['cells'][0]['amount'],'500.00')
        self.setting('purposes',self.purpose,budget_system_id=self.daily,name='公益')
        child=self.setting('purposes',budget_system_id=self.daily,parent_id=self.purpose,name='助学')
        current=self.call('GET',f'/api/budget/plans/{self.daily}/2026-10/CNY')
        self.assertEqual(current['items'][0]['purpose_path'],'公益')
        parent=next(p for p in current['dimensions']['purposes'] if p['id']==self.purpose)
        self.assertFalse(parent['selectable'])
        # Existing unsplit allocations may still be saved, without duplicating children.
        self.plan(items=current['items'],version=1)
        self.assertEqual(self.summary()['cumulative'],'500.00')
        revisions=self.call('GET',f'/api/budget/plans/{self.daily}/2026-10/CNY/revisions')
        self.assertEqual(revisions[-1]['plan']['items'][0]['purpose_path'],'慈善')
        self.call('PUT',f'/api/budget/plans/{self.daily}/2026-11/CNY',dict(expected_version=0,items=[self.item()]),422)
        self.assertEqual(self.call('GET',f"/api/budget/config/purposes/{self.purpose}/impact")['item_count'],2)
        self.assertNotEqual(child['id'],self.purpose)

    def test_total_budget_then_storage_allocations_without_separate_purpose(self):
        before=self.call('GET','/api/accounts')
        url=f'/api/budget/plans/{self.daily}/2026-09/CNY'
        plan=self.call('PUT',url,dict(expected_version=0,total='1000',items=[],allocation_mode=True))
        self.assertEqual(plan['total'],'1000.00')
        allocations=[dict(storage_method_id=self.storage,amount='600',title='ETF 储备')]
        plan=self.call('PUT',url,dict(expected_version=1,total='1000',items=allocations,allocation_mode=True))
        placeholder=next(p['id'] for p in self.config['purposes'] if p['is_placeholder'] and p['budget_system_id']==self.daily)
        self.assertEqual(plan['items'][0]['purpose_id'],placeholder)
        overview=self.call('GET','/api/budget/overview?end_month=2026-09')
        self.assertEqual(overview['currencies'][0]['monthly'],dict(budget_total='1000.00',allocated='600.00',unallocated='400.00'))
        for total in (None,'500','0.001'):
            self.call('PUT',url,dict(expected_version=2,total=total,items=allocations,allocation_mode=True),422)
        self.call('PUT',url,dict(expected_version=1,total='1500',items=[],allocation_mode=True),409)
        self.assertEqual(self.call('GET',url)['version'],2)
        self.assertEqual(self.call('GET','/api/accounts'),before)
        self.assertEqual(self.call('GET','/api/transactions/search')['pagination']['total'],0)

    def test_budget_overview_totals_currencies_history_and_legacy_labels(self):
        other=self.setting('systems',name='慈善专用')['id']
        storage=self.storage  # Shared by every budget.
        self.plan(month='2026-09',total='1000',allocation_mode=True,items=[dict(storage_method_id=self.storage,amount='600')])
        self.plan(total='200',allocation_mode=True,items=[])
        self.plan(system=other,total='500',allocation_mode=True,items=[dict(storage_method_id=storage,amount='300')])
        hkd=self.setting('systems',name='港币预算',currency_code='HKD')['id']
        self.plan(system=hkd,currency='HKD',total='800',allocation_mode=True,items=[])
        old=self.plan(month='2026-08',items=[self.item('50')])
        self.plan(month='2026-08',version=1,total='100',allocation_mode=True,items=[dict(id=old['items'][0]['id'],storage_method_id=self.storage,amount='60')])
        old=self.call('GET',f'/api/budget/plans/{self.daily}/2026-08/CNY')
        self.assertEqual(old['items'][0]['purpose_id'],self.purpose)
        overview=self.call('GET','/api/budget/overview?end_month=2026-10&start_month=2026-09')
        blocks={r['currency_code']:r for r in overview['currencies']}
        self.assertEqual(blocks['CNY']['monthly'],dict(budget_total='700.00',allocated='300.00',unallocated='400.00'))
        self.assertEqual(blocks['CNY']['cumulative'],dict(budget_total='1700.00',allocated='900.00',unallocated='800.00'))
        self.assertEqual(blocks['HKD']['cumulative']['budget_total'],'800.00')
        filtered=self.call('GET',f'/api/budget/overview?end_month=2026-10&system_id={other}&currency_code=CNY')
        self.assertEqual(len(filtered['plans']),1)
        self.assertEqual(filtered['items'][0]['storage_path'],'ETF')
        self.call('GET','/api/budget/overview?end_month=2026-09&start_month=2026-10',code=422)
        self.call('GET','/api/budget/overview?end_month=2026-10&currency_code=BAD',code=422)
        # Legacy missing or overspent targets remain visible, not silently rewritten.
        self.plan(month='2026-07',items=[self.item('40')])
        self.plan(month='2026-06',total='20',items=[self.item('30')])
        legacy=self.call('GET','/api/budget/overview?end_month=2026-08')
        self.assertTrue(next(p for p in legacy['plans'] if p['month']=='2026-07')['total_inferred'])
        self.assertEqual(next(p for p in legacy['plans'] if p['month']=='2026-06')['unallocated'],'-10.00')
        self.assertEqual(next(i for i in legacy['items'] if i['month']=='2026-08')['legacy_purpose'],'慈善')

    def test_total_budget_export_preserves_totals_unallocated_and_currency(self):
        self.setting('systems',self.daily,name='=用途')
        self.plan(total='1000',allocation_mode=True,items=[dict(storage_method_id=self.storage,amount='600',title='=1+1')])
        hkd=self.setting('systems',name='港币预算',currency_code='HKD')['id']
        self.plan(system=hkd,currency='HKD',total='200',items=[],allocation_mode=True)
        response=self.client.get('/api/budget/overview.xlsx?end_month=2026-10')
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(response.content))
        self.assertEqual(book['预算总额']['B2'].data_type,'s')
        self.assertEqual(book['预算总额']['D2'].value,1000)
        self.assertEqual(book['预算总额']['F2'].value,400)
        self.assertEqual(book['二级存储分配']['E2'].value,600)
        self.assertEqual(book['二级存储分配']['F2'].data_type,'s')
        self.assertEqual(book['分币种汇总']['C4'].value,200)

    def test_monthly_single_currency_save_legacy_ids_and_system_currency(self):
        legacy=self.plan(month='2026-09',items=[self.item('600')])
        url=f'/api/budget/monthly/{self.daily}/2026-09'
        old=self.call('GET',url)
        self.assertEqual(old['plans'][0]['items'][0]['id'],legacy['items'][0]['id'])
        before=self.call('GET','/api/accounts')
        plans=[dict(currency_code='CNY',expected_version=1,total='1000',items=legacy['items'])]
        result=self.call('PUT',url,dict(plans=plans))
        self.assertEqual([p['currency_code'] for p in result['plans']],['CNY'])
        self.assertEqual([p['version'] for p in result['plans']],[2])
        self.assertEqual(result['currency_code'],'CNY')
        self.assertEqual(result['plans'][0]['items'][0]['purpose_id'],self.purpose)
        self.assertEqual(self.call('GET',url),result)
        overview=self.call('GET','/api/budget/overview?end_month=2026-09')
        self.assertEqual([b['monthly']['unallocated'] for b in overview['currencies']],['400.00'])
        self.assertEqual(self.call('GET','/api/accounts'),before)
        self.assertEqual(self.call('GET','/api/transactions/search')['pagination']['total'],0)

    def test_monthly_rejects_multiple_and_other_currencies_and_preserves_plan(self):
        url=f'/api/budget/monthly/{self.daily}/2026-09'
        first=dict(currency_code='CNY',expected_version=0,total='1000',items=[])
        bad=dict(currency_code='JPY',expected_version=0,total='200.1',items=[])
        self.call('PUT',url,dict(plans=[first,bad]),422)
        self.assertEqual(self.call('GET',url)['plans'],[])
        self.call('PUT',url,dict(plans=[first]))
        before=self.call('GET',url)
        for failing,code in [(dict(first,currency_code='HKD'),409),(dict(first,expected_version=0),409),(dict(first,expected_version=1,total='1',allocation_mode=False,items=[dict(storage_method_id=self.storage,amount='2')]),422)]:
            self.call('PUT',url,dict(plans=[failing]),code)
            self.assertEqual(self.call('GET',url),before)
        self.call('PUT',f'/api/budget/plans/{self.daily}/2026-11/HKD',dict(expected_version=0,items=[]),409)
        self.call('PUT',url,dict(plans=[]),422)
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM budget_plan_revisions').fetchone()[0],1)

    def currency_change_body(self, code='HKD'):
        preview=self.call('GET',f'/api/budget/systems/{self.daily}/currency')
        return dict(expected_version=preview['expected_version'],currency_code=code,
                    plans=[dict(id=p['id'],version=p['version']) for p in preview['plans']])

    def test_config_currency_can_be_selected_and_reset_before_first_plan(self):
        selected=self.setting('systems',self.daily,name='日常预算',currency_code='HKD')
        self.assertEqual(selected['currency_code'],'HKD')
        reset=self.setting('systems',self.daily,name='日常预算',currency_code=None)
        self.assertIsNone(reset['currency_code'])
        self.plan(total='1000',items=[])
        version=self.call('GET','/api/budget/config')['version']
        self.call('PUT',f'/api/budget/config/systems/{self.daily}',dict(expected_version=version,name='日常预算',currency_code=None),409)

    def test_currency_correction_all_months_numbers_ids_history_and_actuals(self):
        actual=self.transaction(budget=self.daily)  # HKD actuals can reference a CNY budget.
        before_accounts=self.call('GET','/api/accounts')
        sep=self.plan(month='2026-09',total='1000',items=[self.item('600')])
        oct=self.plan(items=[self.item('400')])  # Retain the legacy inferred total.
        other=self.setting('systems',name='保持不变')['id']
        other_plan=self.plan(system=other,items=[],total='90')
        url=f'/api/budget/systems/{self.daily}/currency'
        self.config=self.call('PUT',url,self.currency_change_body())
        for old in (sep,oct):
            p=self.call('GET',f"/api/budget/plans/{self.daily}/{old['month']}/HKD")
            self.assertEqual(p['id'],old['id']);self.assertEqual(p['version'],2)
            self.assertEqual(p['total'],old['total']);self.assertEqual(p['items'],old['items'])
            revisions=self.call('GET',f"/api/budget/plans/{self.daily}/{old['month']}/HKD/revisions")
            self.assertEqual([r['plan']['currency_code'] for r in revisions],['HKD','CNY'])
        # Changing decimal precision re-encodes minor units while preserving the number.
        self.call('PUT',url,self.currency_change_body('JPY'))
        jpy=self.call('GET',f'/api/budget/plans/{self.daily}/2026-09/JPY')
        self.assertEqual(jpy['total'],'1000');self.assertEqual(jpy['total_minor'],1000)
        self.assertEqual(jpy['items'][0]['amount_minor'],600)
        self.call('PUT',url,self.currency_change_body('CNY'))
        self.assertEqual(self.call('GET',f'/api/budget/plans/{self.daily}/2026-09/CNY')['total'],'1000.00')
        self.assertEqual(self.call('GET',f'/api/budget/plans/{other}/2026-10/CNY'),other_plan)
        self.assertEqual(self.call('GET',f"/api/transactions/{actual['id']}"),actual)
        self.assertEqual(self.call('GET','/api/accounts'),before_accounts)
        self.call('PUT',f'/api/budget/plans/{self.daily}/2026-09/HKD',dict(expected_version=2,total='1000',items=[]),409)

    def test_currency_correction_precision_and_concurrency_roll_back(self):
        sep=self.plan(month='2026-09',total='1000',items=[self.item('200')])
        self.plan(total='1000',items=[self.item('0.01')])
        url=f'/api/budget/systems/{self.daily}/currency'
        before=self.call('GET',url)
        self.call('PUT',url,self.currency_change_body('JPY'),422)
        self.assertEqual(self.call('GET',url),before)
        body=self.currency_change_body()
        self.call('PUT',url,dict(body,plans=body['plans'][:1]),409)
        self.call('PUT',url,dict(body,plans=body['plans']+body['plans'][:1]),409)
        self.plan(month='2026-09',version=1,total='1100',items=sep['items'])
        self.call('PUT',url,body,409)
        body=self.currency_change_body()
        self.plan(month='2026-11',total='800',items=[])
        self.call('PUT',url,body,409)
        self.assertEqual(self.call('GET',f'/api/budget/monthly/{self.daily}/2026-09')['currency_code'],'CNY')
        self.call('PUT',f'/api/budget/config/systems/{self.daily}',dict(expected_version=body['expected_version'],name='日常预算',currency_code='HKD'),409)
        self.call('GET',f'/api/budget/monthly/{self.daily}/2026-13',code=422)

    def test_currency_migration_backs_up_and_preserves_legacy_multicurrency(self):
        self.plan(month='2026-09',total='500')
        other=self.setting('systems',name='旧多币种')['id']
        with database.connect() as c:
            c.executemany('INSERT INTO budget_plans(budget_system_id,month,currency_code,total_minor) VALUES (?,?,?,?)',
                          [(other,'2026-09','CNY',10000),(other,'2026-09','HKD',20000)])
            c.execute('ALTER TABLE budget_systems DROP COLUMN currency_code')
        database.initialize_database()
        backups=list(database.BACKUP_DIR.glob('finance-pre-budget-currency-*.db'))
        self.assertEqual(len(backups),1)
        with database.open_connection(backups[0]) as c:
            self.assertNotIn('currency_code',[r[1] for r in c.execute('PRAGMA table_info(budget_systems)')])
        config=self.call('GET','/api/budget/config')
        daily=next(s for s in config['systems'] if s['id']==self.daily)
        legacy=next(s for s in config['systems'] if s['id']==other)
        self.assertEqual(daily['currency_code'],'CNY');self.assertIsNone(legacy['currency_code'])
        self.assertTrue(legacy['currency_conflict'])
        overview=self.call('GET',f'/api/budget/overview?end_month=2026-09&system_id={other}')
        self.assertEqual([p['budget_total'] for p in overview['plans']],['100.00','200.00'])
        self.call('PUT',f'/api/budget/plans/{other}/2026-10/CNY',dict(expected_version=0,total='1',items=[]),409)
        preview=self.call('GET',f'/api/budget/systems/{other}/currency')
        self.call('PUT',f'/api/budget/systems/{other}/currency',dict(expected_version=preview['expected_version'],currency_code='CNY',plans=[dict(id=p['id'],version=p['version']) for p in preview['plans']]),409)
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-budget-currency-*.db'))),1)

    def test_version_conflict_rolls_back_and_deletion_retains_history(self):
        plan=self.plan()
        body=dict(expected_version=0,items=[self.item('999')])
        self.call('PUT',f'/api/budget/plans/{self.daily}/2026-10/CNY',body,409)
        self.assertEqual(self.summary()['cumulative'],'500.00')
        self.plan(items=[],version=plan['version'])
        self.assertEqual(self.summary()['cumulative'],'0.00')
        self.call('DELETE',f"/api/budget/config/storage/{self.storage}?expected_version={self.config['version']}",code=409)
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM budget_plan_revisions').fetchone()[0],2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM change_audit_logs WHERE entity_type='budget'").fetchone()[0],2)

    def test_plan_precision_limits_ids_and_cross_system_references(self):
        url=f'/api/budget/plans/{self.daily}/2026-10/JPY'
        self.call('PUT',url,dict(expected_version=0,items=[self.item('1.1')]),422)
        jpy=self.setting('systems',name='日元预算',currency_code='JPY')['id']
        self.plan(system=jpy,currency='JPY',total='123',items=[])
        for amount in ['-1','1.001','100000000000000']:
            self.call('PUT',f'/api/budget/plans/{self.daily}/2026-10/CNY',dict(expected_version=0,items=[self.item(amount)]),422)
        other=self.setting('systems',name='专项预算')['id']
        self.call('PUT',f'/api/budget/plans/{other}/2026-10/CNY',dict(expected_version=0,items=[self.item()]),422)
        plan=self.plan()
        duplicate=[plan['items'][0],plan['items'][0]]
        self.call('PUT',f'/api/budget/plans/{self.daily}/2026-10/CNY',dict(expected_version=1,items=duplicate),422)
        self.assertEqual(self.summary()['cumulative'],'500.00')

    def test_tree_cycles_unique_names_deactivation_and_config_conflicts(self):
        child=self.setting('storage',name='宽基',parent_id=self.storage,budget_system_id=self.daily)
        version=self.config['version']
        self.call('PUT',f'/api/budget/config/storage/{self.storage}',dict(expected_version=version,name='ETF',parent_id=child['id'],budget_system_id=self.daily),422)
        self.call('POST','/api/budget/config/storage',dict(expected_version=version,name='ETF',budget_system_id=self.daily),409)
        self.call('POST','/api/budget/config/systems',dict(expected_version=1,name='过时保存'),409)
        self.setting('storage',self.storage,name='ETF',budget_system_id=self.daily,is_active=False)
        self.assertFalse(next(r for r in self.config['storage'] if r['id']==child['id'])['selectable'])
        self.call('PUT',f'/api/budget/config/systems/{self.daily}',dict(expected_version=self.config['version'],name='日常预算',is_active=False),409)

    def test_settings_defaults_rules_presets_and_catalog_copy(self):
        other=self.setting('systems',name='工资二')['id']
        self.config=self.call('POST','/api/budget/copy-catalog',dict(expected_version=self.config['version'],source_system_id=self.daily,target_system_id=other))
        shared=next(r for r in self.config['storage'] if r['name']=='ETF')
        self.assertEqual(shared['id'],self.storage)
        self.setting('storage',shared['id'],name='股票型ETF')
        self.assertEqual(next(r for r in self.config['storage'] if r['id']==self.storage)['name'],'股票型ETF')
        self.setting('rules',mode='expense',category_id=self.categories['expense'],budget_system_id=other)
        self.setting('presets',name='工资B',mode='income',category_id=self.categories['income'],account_id=self.account,budget_system_id=other)
        self.config=self.call('PUT','/api/budget/defaults',dict(expected_version=self.config['version'],expense=other,income=self.daily))
        self.assertEqual(self.config['defaults'],dict(expense=other,income=self.daily))
        self.assertIsNone(self.transaction()['budget_system_id'])  # Legacy callers are not silently mapped.
        unused=self.setting('systems',name='可删除预算')['id']
        self.config=self.call('DELETE',f"/api/budget/config/systems/{unused}?expected_version={self.config['version']}")
        self.assertFalse(any(s['id']==unused for s in self.config['systems']))

    def test_shared_storage_creation_usage_rename_and_deactivation(self):
        other=self.setting('systems',name='公共存储测试')['id']
        storage=self.setting('storage',name='公共定存')['id']
        allocations=[dict(storage_method_id=storage,amount='10')]
        first=self.plan(items=allocations,total='20')
        second=self.plan(system=other,items=allocations,total='30')
        self.setting('storage',storage,name='一年定存')
        for sid in (self.daily,other):
            current=self.call('GET',f'/api/budget/plans/{sid}/2026-10/CNY')
            self.assertEqual(current['items'][0]['storage_path'],'一年定存')
            self.assertTrue(any(s['id']==storage for s in current['dimensions']['storage']))
        self.assertEqual(self.call('GET',f'/api/budget/config/storage/{storage}/impact')['item_count'],2)
        self.setting('storage',storage,name='一年定存',is_active=False)
        self.plan(system=other,items=second['items'],version=1,total='30')
        self.call('PUT',f'/api/budget/plans/{other}/2026-11/CNY',dict(expected_version=0,total='30',items=allocations),422)
        self.assertEqual(first['items'][0]['storage_path'],'公共定存')

    def test_shared_storage_migration_preserves_history_aliases_and_amounts(self):
        other=self.setting('systems',name='旧目录预算')['id']
        first=self.plan(total='500')
        second=self.plan(system=other,total='80',items=[dict(storage_method_id=self.storage,amount='50')])
        with database.connect() as c:
            c.execute('PRAGMA foreign_keys=OFF')
            c.execute('BEGIN')
            c.execute('''CREATE TABLE old_storage (
                id INTEGER PRIMARY KEY,budget_system_id INTEGER NOT NULL REFERENCES budget_systems(id),
                parent_id INTEGER REFERENCES budget_storage_methods(id),name TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,is_active INTEGER DEFAULT 1,is_placeholder INTEGER DEFAULT 0,note TEXT DEFAULT '')''')
            c.execute('INSERT INTO old_storage SELECT id,?,parent_id,name,sort_order,is_active,is_placeholder,note FROM budget_storage_methods',(self.daily,))
            c.execute("INSERT INTO old_storage(id,budget_system_id,name) VALUES (50,?,'ETF')",(other,))
            c.execute("INSERT INTO old_storage(id,budget_system_id,name,note) VALUES (60,?,'活期','独立旧说明')",(other,))
            c.execute('UPDATE budget_plan_items SET storage_method_id=50 WHERE plan_id=?',(second['id'],))
            snapshot=dict(second,items=[dict(second['items'][0],storage_method_id=50)])
            c.execute('UPDATE budget_plan_revisions SET snapshot=? WHERE plan_id=?',(json.dumps(snapshot),second['id']))
            c.execute('DROP TABLE budget_storage_methods')
            c.execute('ALTER TABLE old_storage RENAME TO budget_storage_methods')
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-shared-storage-*.db'))),1)
        config=self.call('GET','/api/budget/config')
        self.assertEqual(len([s for s in config['storage'] if s['name']=='ETF']),1)
        self.assertTrue(any(s['name']=='活期（旧目录预算）' for s in config['storage']))
        current=self.call('GET',f'/api/budget/plans/{other}/2026-10/CNY')
        self.assertEqual(current['items'][0]['id'],second['items'][0]['id'])
        self.assertEqual(current['items'][0]['storage_method_id'],self.storage)
        self.assertEqual(current['allocated'],'50.00')
        history=self.call('GET',f'/api/budget/plans/{other}/2026-10/CNY/revisions')
        self.assertEqual(history[0]['plan']['items'][0]['storage_method_id'],50)
        # The old ID can still be submitted by a previously open editor.
        self.plan(system=other,total='80',items=snapshot['items'],version=1)
        created=self.setting('storage',name='全新公共栏目')['id']
        self.assertGreater(created,60)
        self.plan(items=[],version=first['version']);self.plan(system=other,items=[],version=2)
        self.call('DELETE',f"/api/budget/config/storage/{self.storage}?expected_version={self.config['version']}",code=409)
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-shared-storage-*.db'))),1)
        with database.connect() as c:
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_storage_placeholder_migrates_to_derived_unallocated_balance(self):
        explicit=self.plan(month='2026-08',total='100',items=[self.item('50')])
        expense=self.transaction(amount='5',day='2026-08-02')
        with database.connect() as c:
            placeholder=c.execute("""INSERT INTO budget_storage_methods(name,sort_order,is_placeholder)
                                      VALUES ('待定存储方式',0,1)""").lastrowid
            c.execute('''INSERT INTO budget_plan_items(
                plan_id,storage_method_id,purpose_id,title,amount_minor,sort_order)
                VALUES (?,?,?,?,?,?)''',(explicit['id'],placeholder,self.purpose,'尚未决定',3000,1))
            snapshot=json.loads(c.execute(
                'SELECT snapshot FROM budget_plan_revisions WHERE plan_id=? AND version=1',
                (explicit['id'],)).fetchone()[0])
            snapshot['items'].append(dict(storage_method_id=placeholder,amount='30.00',title='尚未决定'))
            snapshot_text=json.dumps(snapshot,ensure_ascii=False)
            c.execute('UPDATE budget_plan_revisions SET snapshot=? WHERE plan_id=? AND version=1',
                      (snapshot_text,explicit['id']))

            inferred=c.execute('''INSERT INTO budget_plans(
                budget_system_id,month,currency_code,total_minor,version)
                VALUES (?,?,?,?,3)''',(self.daily,'2026-07','CNY',None)).lastrowid
            c.executemany('''INSERT INTO budget_plan_items(
                plan_id,storage_method_id,purpose_id,title,amount_minor,sort_order)
                VALUES (?,?,?,?,?,?)''',[
                    (inferred,self.storage,self.purpose,'已分配',2000,0),
                    (inferred,placeholder,self.purpose,'尚未决定',4000,1),
                ])
            c.execute('INSERT INTO budget_storage_defaults VALUES (?,?)',(self.daily,placeholder))
            c.execute('''INSERT INTO budget_storage_expenses(
                budget_system_id,transaction_id,storage_method_id) VALUES (?,?,?)''',
                (self.daily,expense['id'],placeholder))
            c.executemany('''INSERT INTO budget_storage_transfers(
                budget_system_id,month,source_id,destination_id,currency_code,amount_minor)
                VALUES (?,?,?,?,?,?)''',[
                    (self.daily,'2026-08',placeholder,self.storage,'CNY',100),
                    (self.daily,'2026-08',self.storage,placeholder,'CNY',100),
                    (self.daily,'2026-08',None,placeholder,'CNY',100),
                ])
            c.execute('INSERT INTO budget_storage_aliases(old_id,storage_id) VALUES (?,?)',(999,placeholder))
            child=c.execute("""INSERT INTO budget_storage_methods(parent_id,name)
                               VALUES (?,'活期')""",(placeholder,)).lastrowid
            config_version=int(c.execute(
                "SELECT value FROM app_settings WHERE key='budget_config_version'").fetchone()[0])

        database.initialize_database()
        backups=list(database.BACKUP_DIR.glob('finance-pre-unallocated-storage-*.db'))
        self.assertEqual(len(backups),1)
        current=self.call('GET',f'/api/budget/plans/{self.daily}/2026-08/CNY')
        self.assertEqual((current['total'],current['allocated'],current['version']),
                         ('100.00','50.00',2))
        inferred_plan=self.call('GET',f'/api/budget/plans/{self.daily}/2026-07/CNY')
        self.assertEqual((inferred_plan['total'],inferred_plan['allocated'],inferred_plan['version']),
                         ('60.00','20.00',4))
        overview=self.call('GET','/api/budget/overview?end_month=2026-08')
        migrated={row['month']:row for row in overview['plans'] if row['month'] in ('2026-07','2026-08')}
        self.assertEqual((migrated['2026-08']['unallocated'],migrated['2026-08']['total_inferred']),
                         ('50.00',False))
        self.assertEqual((migrated['2026-07']['unallocated'],migrated['2026-07']['total_inferred']),
                         ('40.00',False))
        with database.connect() as c:
            self.assertEqual(c.execute(
                'SELECT snapshot FROM budget_plan_revisions WHERE plan_id=? AND version=1',
                (explicit['id'],)).fetchone()[0],snapshot_text)
            self.assertEqual(c.execute(
                'SELECT COUNT(*) FROM budget_storage_methods WHERE is_placeholder=1').fetchone()[0],0)
            self.assertEqual(c.execute(
                'SELECT COUNT(*) FROM budget_storage_defaults').fetchone()[0],0)
            self.assertEqual(c.execute(
                'SELECT COUNT(*) FROM budget_storage_expenses').fetchone()[0],0)
            transfers=[tuple(r) for r in c.execute(
                'SELECT source_id,destination_id,version FROM budget_storage_transfers ORDER BY id')]
            self.assertEqual(transfers,[(None,self.storage,2),(self.storage,None,2)])
            renamed=c.execute(
                'SELECT parent_id,name FROM budget_storage_methods WHERE id=?',(child,)).fetchone()
            self.assertEqual(tuple(renamed),(None,'活期 2'))
            self.assertFalse(c.execute(
                'SELECT 1 FROM budget_storage_aliases WHERE old_id=999').fetchone())
            self.assertEqual(int(c.execute(
                "SELECT value FROM app_settings WHERE key='budget_config_version'").fetchone()[0]),
                config_version+1)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-unallocated-storage-*.db'))),1)

    def test_new_ledger_has_no_storage_placeholder(self):
        self.assertFalse(any(row['is_placeholder'] for row in self.config['storage']))
        self.assertEqual([row['name'] for row in self.config['storage']],['活期','定期','ETF','股票'])

    def execution(self, start='2026-09', end='2026-11', system=None):
        return self.call('GET',f'/api/budget/execution?system_id={system or self.daily}&start_month={start}&end_month={end}')['rows']

    def test_execution_negative_carry_missing_plan_and_historical_recalculation(self):
        sep=self.plan(month='2026-09',total='100',items=[])
        self.plan(month='2026-11',total='100',items=[])
        self.transaction(amount='150',day='2026-09-12')
        self.transaction(amount='20',day='2026-10-01')
        before=self.call('GET','/api/accounts')
        rows=self.execution()
        self.assertEqual([r['closing'] for r in rows],['-50.00','-70.00','30.00'])
        self.assertEqual(rows[1]['opening'],'-50.00');self.assertFalse(rows[1]['compiled'])
        self.assertEqual(self.execution(start='2026-11')[0]['opening'],'-70.00')
        filtered=self.call('GET','/api/budget/overview?start_month=2026-11&end_month=2026-11')
        self.assertEqual(filtered['execution']['rows'][0]['closing'],'30.00')
        self.plan(month='2026-09',version=sep['version'],total='200',items=[])
        self.assertEqual(self.execution()[-1]['closing'],'130.00')
        self.assertEqual(self.call('GET','/api/accounts'),before)
        book=load_workbook(BytesIO(self.client.get('/api/budget/overview.xlsx?end_month=2026-11').content))
        self.assertEqual(book['预算执行']['I2'].value,130)

    def test_execution_complete_offsets_refunds_income_and_currency(self):
        self.setting('systems',self.daily,name='日常预算',currency_code='CNY')
        expense=self.transaction(amount='100',day='2026-09-15')
        refund=self.transaction('income','150',day='2026-11-01')
        self.call('POST','/api/offset-groups',dict(title='跨月多退',kind='expense',transaction_ids=[expense['id'],refund['id']]),201)
        self.plan(total='100',items=[])
        self.transaction('income','999',budget=self.daily,day='2026-11-02')
        rows=self.execution()
        self.assertEqual(rows[0]['expense'],'-50.00')
        self.assertFalse(rows[0]['compiled'])
        self.assertEqual([r['closing'] for r in rows],['50.00','150.00','150.00'])
        self.assertGreater(rows[0]['nearest_count'],0)

    def test_execution_missing_rates_and_mixed_budgets_remain_incomplete(self):
        self.plan(month='2026-09',items=[],total='100')
        a=self.transaction(amount='20',day='2026-09-01')
        with database.connect() as c:
            c.execute('DELETE FROM daily_cny_exchange_rates')
        rows=self.execution()
        self.assertIsNone(rows[0]['expense']);self.assertIsNone(rows[-1]['closing'])
        self.assertEqual(rows[0]['opening'],'0.00')
        self.assertIn('缺少汇率',rows[0]['warnings'][0]['reason'])
        with database.connect() as c:
            c.execute("INSERT INTO daily_cny_exchange_rates(rate_date,usd_cny_rate,hkd_cny_rate,source,market_date) VALUES ('2026-09-01','7','1','test','2026-09-01')")
        self.assertEqual(self.execution()[-1]['closing'],'80.00')
        other=self.setting('systems',name='混合预算',currency_code='CNY')['id']
        b=self.transaction(amount='10',budget=other,day='2026-09-01')
        group=self.call('POST','/api/offset-groups',dict(title='跨预算消费',kind='expense',transaction_ids=[a['id'],b['id']]),201)
        for sid in (self.daily,other):
            row=self.execution(system=sid)[-1]
            self.assertIsNone(row['closing']);self.assertEqual(row['warnings'][0]['entry_id'],group['id'])

    def test_execution_manual_start_opening_and_currency_correction(self):
        self.setting('systems',self.daily,name='日常预算',currency_code='HKD',execution_start_month='2026-10',opening_balance='-30.50')
        self.transaction(amount='999',day='2026-09-01')
        self.transaction(amount='20',day='2026-10-01')
        self.plan(currency='HKD',total='100',items=[])
        rows=self.execution()
        self.assertFalse(rows[0]['started']);self.assertIsNone(rows[0]['closing'])
        self.assertEqual(rows[1]['closing'],'49.50')
        self.call('PUT',f'/api/budget/systems/{self.daily}/currency',self.currency_change_body('JPY'),422)
        self.call('PUT',f'/api/budget/systems/{self.daily}/currency',self.currency_change_body('CNY'))
        self.assertEqual(self.execution()[1]['opening'],'-30.50')
        self.config=self.call('GET','/api/budget/config')
        self.call('PUT',f'/api/budget/config/systems/{self.daily}',dict(expected_version=self.config['version'],name='日常预算',execution_start_month=None),422)

    def test_execution_usd_conversion_and_unsupported_currency(self):
        self.plan(currency='USD',total='100',items=[])
        self.transaction(amount='70')
        self.assertEqual(self.execution()[1]['expense'],'10.00')
        self.assertEqual(self.execution()[1]['closing'],'90.00')
        with database.connect() as c:
            c.execute("UPDATE accounts SET currency_code='EUR' WHERE id=?",(self.account,))
        self.assertIsNone(self.execution()[1]['closing'])
        self.call('GET',f'/api/budget/execution?system_id={self.daily}&start_month=2026-11&end_month=2026-10',code=422)

    def test_execution_settings_migration_is_backed_up_and_idempotent(self):
        self.plan(items=[],total='100')
        with database.connect() as c:
            c.execute('ALTER TABLE budget_systems DROP COLUMN execution_start_month')
            c.execute('ALTER TABLE budget_systems DROP COLUMN opening_minor')
        database.initialize_database()
        self.assertEqual(self.execution()[1]['closing'],'100.00')
        backups=list(database.BACKUP_DIR.glob('finance-pre-budget-execution-*.db'))
        self.assertEqual(len(backups),1)
        with database.open_connection(backups[0]) as c:
            self.assertNotIn('opening_minor',[r[1] for r in c.execute('PRAGMA table_info(budget_systems)')])
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-budget-execution-*.db'))),1)

    def test_budget_filter_affects_raw_summary_and_analysis_not_balances(self):
        other=self.setting('systems',name='专项')['id']
        a=self.transaction(amount='100',budget=self.daily)
        self.transaction(amount='200',budget=other)
        inherited=self.transaction(amount='30')
        self.transaction('income','50')
        before=self.call('GET','/api/accounts')
        query=f'start_date=2026-10-01&end_date=2026-10-31&budget_system_id={self.daily}'
        result=self.call('GET','/api/transactions/search?transaction_types=expense&'+query)
        self.assertEqual({r['id'] for r in result['transactions']},{a['id'],inherited['id']})
        self.assertEqual(result['summary']['valuations'][0]['outflow_amount'],'130.00')
        analysis=self.call('GET','/api/transactions/analysis?transaction_type=expense&'+query)
        self.assertEqual(analysis['statistics']['total_amount'],'130.00')
        self.assertEqual(self.call('GET','/api/transactions/search?budget_system_id=0')['pagination']['total'],1)
        self.assertEqual(self.call('GET','/api/transactions/search')['pagination']['total'],4)
        self.assertEqual(self.call('GET','/api/accounts'),before)
        self.assertEqual(result['transactions'][0]['effective_budget_system_name'],'日常预算')
        self.assertIsNone(inherited['budget_system_id'])
        self.assertTrue(inherited['budget_inherited'])
        self.assertEqual(inherited['effective_budget_system_id'],self.daily)

    def test_daily_assignment_is_stable_across_rename_and_prefill_changes(self):
        implicit=self.transaction(amount='12')
        explicit=self.transaction(amount='20',budget=self.daily)
        other=self.setting('systems',name='专项')['id']
        self.setting('systems',self.daily,name='生活预算')
        self.config=self.call('PUT','/api/budget/defaults',dict(expected_version=self.config['version'],expense=other,income=other))
        self.assertEqual(self.config['daily_expense'],self.daily)
        query=f'/api/transactions/search?budget_system_id={self.daily}'
        self.assertEqual(self.call('GET',query)['pagination']['total'],2)
        self.assertEqual(self.call('GET',f"/api/transactions/{implicit['id']}")['effective_budget_system_name'],'生活预算')
        self.call('PUT',f'/api/budget/config/systems/{self.daily}',dict(expected_version=self.config['version'],name='生活预算',is_active=False),409)
        self.call('DELETE',f"/api/budget/config/systems/{self.daily}?expected_version={self.config['version']}",code=409)
        self.call('PUT','/api/budget/defaults',dict(expected_version=self.config['version'],daily_expense=None),422)
        self.config=self.call('PUT','/api/budget/defaults',dict(expected_version=self.config['version'],daily_expense=other,expense=other,income=None))
        self.assertEqual([t['id'] for t in self.call('GET',query)['transactions']],[explicit['id']])
        self.assertEqual([t['id'] for t in self.call('GET',f'/api/transactions/search?budget_system_id={other}')['transactions']],[implicit['id']])
        with database.connect() as c:
            self.assertIsNone(c.execute('SELECT budget_system_id FROM transactions WHERE id=?',(implicit['id'],)).fetchone()[0])

    def test_implicit_and_explicit_daily_primary_members_share_offset_budget(self):
        other=self.setting('systems',name='专项')['id']
        a=self.transaction(amount='100')
        b=self.transaction(amount='40',budget=self.daily)
        refund=self.transaction('income','20',other,day='2026-11-01')
        group=self.call('POST','/api/offset-groups',dict(title='日常合并',kind='expense',transaction_ids=[a['id'],b['id'],refund['id']]),201)
        query=f'/api/personal-cashflow/search?start_date=2026-10-01&end_date=2026-10-31&budget_system_id={self.daily}'
        result=self.call('GET',query)
        self.assertFalse(result['budget_warnings'])
        self.assertEqual(result['entries'][0]['budget_system_id'],self.daily)
        self.assertEqual(result['summary']['valuations'][0]['expense'],'120.00')
        self.assertFalse(result['entries'][0]['budget_mixed'])
        special=self.transaction(amount='10',budget=other)
        self.call('PUT',f"/api/offset-groups/{group['id']}",dict(title='日常及专项',kind='expense',transaction_ids=[a['id'],special['id'],refund['id']],expected_version=1))
        result=self.call('GET',query)
        self.assertEqual(result['budget_warnings'][0]['id'],group['id'])
        self.assertEqual(result['entries'][0]['id'],b['id'])

    def test_daily_migration_backs_up_once_without_rewriting_transactions(self):
        original=self.transaction()
        with database.connect() as c:
            c.execute("DELETE FROM app_settings WHERE key='daily_expense_budget_system_id'")
        database.initialize_database()
        backups=list(database.BACKUP_DIR.glob('finance-pre-daily-budget-*.db'))
        self.assertEqual(len(backups),1)
        with database.open_connection(backups[0]) as c:
            self.assertIsNone(c.execute("SELECT value FROM app_settings WHERE key='daily_expense_budget_system_id'").fetchone())
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-daily-budget-*.db'))),1)
        record=self.call('GET',f"/api/transactions/{original['id']}")
        self.assertIsNone(record['budget_system_id'])
        self.assertEqual(record['effective_budget_system_id'],self.daily)

    def test_edit_omitted_budget_preserves_explicit_null_clears_transfer_clears(self):
        expense=self.transaction(budget=self.daily)
        url=f"/api/transactions/{expense['id']}"
        body=dict(transaction_date='2026-10-01',transaction_type='expense',source_account_id=self.account,outflow_amount='100',category_id=self.categories['expense'])
        self.assertEqual(self.call('PUT',url,body)['budget_system_id'],self.daily)
        self.assertIsNone(self.call('PUT',url,{**body,'budget_system_id':None})['budget_system_id'])
        self.call('PUT',url,{**body,'budget_system_id':self.daily})
        transfer=dict(transaction_date='2026-10-01',transaction_type='transfer',source_account_id=self.account,destination_account_id=self.other_account,outflow_amount='100',inflow_amount='100')
        self.assertIsNone(self.call('PUT',url,transfer)['budget_system_id'])

    def test_offset_full_group_then_budget_filter_and_mixed_warning(self):
        self.plan()
        other=self.setting('systems',name='专项')['id']
        a=self.transaction(amount='100',budget=self.daily)
        refund=self.transaction('income','120',other,day='2026-11-01')
        group=self.call('POST','/api/offset-groups',dict(title='跨月退回',kind='expense',transaction_ids=[a['id'],refund['id']]),201)
        query=f'start_date=2026-10-01&end_date=2026-10-31&budget_system_id={self.daily}'
        result=self.call('GET','/api/personal-cashflow/search?'+query)
        self.assertEqual(result['summary']['valuations'][0]['expense'],'-20.00')
        self.assertEqual(len(result['entries'][0]['members']),2)
        self.assertFalse(result['budget_warnings'])
        b=self.transaction(amount='50',budget=other)
        self.call('PUT',f"/api/offset-groups/{group['id']}",dict(title='跨预算',kind='expense',transaction_ids=[a['id'],b['id'],refund['id']],expected_version=1))
        result=self.call('GET','/api/personal-cashflow/search?'+query)
        self.assertEqual(result['pagination']['total'],0)
        self.assertFalse(result['summary']['budget_complete'])
        self.assertEqual(result['budget_warnings'][0]['id'],group['id'])
        multi=(f'start_date=2026-10-01&end_date=2026-10-31'
               f'&budget_system_ids={self.daily}&budget_system_ids={other}')
        combined=self.call('GET','/api/personal-cashflow/search?'+multi)
        self.assertEqual(combined['pagination']['total'],1)
        self.assertFalse(combined['budget_warnings'])
        self.assertEqual(combined['summary']['valuations'][0]['expense'],'30.00')
        self.assertEqual(combined['filters']['budget_system_ids'],[self.daily,other])
        raw=self.call('GET','/api/transactions/search?transaction_types=expense&'+multi)
        self.assertEqual(raw['pagination']['total'],2)
        self.assertEqual(raw['summary']['raw_totals_by_currency'][0]['outflow_amount'],'150.00')
        all_budgets=self.call('GET','/api/personal-cashflow/search?start_date=2026-10-01&end_date=2026-10-31')
        self.assertEqual(all_budgets['summary']['valuations'][0]['expense'],'30.00')
        self.assertEqual(self.summary()['cumulative'],'500.00')

    def test_migration_backup_preserves_old_records_and_is_idempotent(self):
        original=self.transaction()
        with database.connect() as c:
            c.execute('DROP INDEX transactions_budget_index')
            c.execute('ALTER TABLE transactions DROP COLUMN budget_system_id')
        database.initialize_database()
        backups=list(database.BACKUP_DIR.glob('finance-pre-budget-*.db'))
        self.assertEqual(len(backups),1)
        with database.open_connection(backups[0]) as c:
            self.assertNotIn('budget_system_id',[r[1] for r in c.execute('PRAGMA table_info(transactions)')])
        self.assertIsNone(self.call('GET',f"/api/transactions/{original['id']}")['budget_system_id'])
        database.initialize_database()
        self.assertEqual(len(list(database.BACKUP_DIR.glob('finance-pre-budget-*.db'))),1)
        with database.connect() as c:
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_export_numeric_cells_formula_escaping_and_no_accounts_required(self):
        self.setting('systems',self.daily,name='=预算测试')
        item=self.item();item['title']='=1+1'
        self.plan(items=[item])
        response=self.client.get(f'/api/budget/export.xlsx?system_id={self.daily}&end_month=2026-10&currency_code=CNY')
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(response.content))
        self.assertEqual(book['预算计划']['A1'].data_type,'s')
        self.assertEqual(book['预算计划']['D3'].data_type,'s')
        self.assertEqual(book['预算计划']['E3'].value,500)
        with database.connect() as c:c.execute('DELETE FROM accounts')
        self.plan('2026-11',[self.item('1')])
        self.assertEqual(self.summary()['cumulative'],'501.00')


if __name__ == '__main__':
    unittest.main()

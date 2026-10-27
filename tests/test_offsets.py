import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import database
from app.main import app, dashboard, build_excel_export, update_transaction, delete_transaction, TransactionUpdate, TransactionDelete
from app.offsets import (OffsetWrite, OffsetDelete, create_group, update_group, get_group, dissolve_group,
                         preview_group, query_entries, search_personal, analyze_personal)
from app.personal_cashflow import read_ledger, build_entries, summarize
from app.reporting import build_hkd_histogram


class OffsetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(database, 'DATA_DIR', root/'data'),
                        patch.object(database, 'DATABASE_PATH', root/'data'/'finance.db'),
                        patch.object(database, 'BACKUP_DIR', root/'backups')]
        for p in self.patches: p.start()
        database.initialize_database()
        with database.connect() as c:
            for currency in ('CNY', 'HKD', 'USD', 'EUR'):
                c.execute("INSERT INTO accounts (code,name,account_class,account_type,currency_code,opening_balance_date) VALUES (?,?,'asset','bank_demand',?,'2026-01-01')", (currency,currency,currency))
            self.accounts = {r['currency_code']:r['id'] for r in c.execute('SELECT * FROM accounts')}
            self.categories = {kind:c.execute('SELECT id FROM categories WHERE kind=? ORDER BY id LIMIT 1',(kind,)).fetchone()['id'] for kind in ('income','expense')}
        self.rate('2026-09-01','0.9')

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def rate(self, day, hkd):
        with database.connect() as c:
            c.execute("INSERT INTO daily_cny_exchange_rates (rate_date,usd_cny_rate,hkd_cny_rate,source,market_date) VALUES (?,'7',?,'test',?)", (day,hkd,day))

    def txn(self, kind, amount, day='2026-09-01', currency='CNY', note=None):
        with database.connect() as c:
            return c.execute('''INSERT INTO transactions (transaction_date,transaction_type,source_account_id,destination_account_id,outflow_minor,inflow_minor,category_id,note)
                VALUES (?,?,?,?,?,?,?,?)''', (day,kind,self.accounts[currency] if kind=='expense' else None,
                    self.accounts[currency] if kind=='income' else None,
                    int(Decimal(str(amount))*100) if kind=='expense' else None,
                    int(Decimal(str(amount))*100) if kind=='income' else None,self.categories[kind],note)).lastrowid

    def payload(self, ids, kind='expense', anchor=None, version=None, day='2026-09-01', **kwargs):
        return OffsetWrite(title='测试事项',kind=kind,anchor_transaction_id=anchor or ids[0],statistics_date=day,
                           category_id=self.categories[kind],transaction_ids=ids,expected_version=version,**kwargs)

    def balance(self):
        with database.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM account_balances ORDER BY id')]

    def test_many_to_many_conserves_balance_net_and_dissolves(self):
        ids=[self.txn('income',3000),self.txn('income',2000)]+[self.txn('expense',x) for x in (1000,1500,500)]
        before=self.balance(); raw=dashboard('2026-09')['cash_flow']
        group=create_group(self.payload(ids,'income'))
        self.assertEqual(group['valuations']['CNY']['amount'],'2000.00')
        self.assertEqual(group['transaction_count'],5)
        self.assertEqual(self.balance(),before)
        self.assertEqual(dashboard('2026-09')['cash_flow'],raw)
        summary=search_personal('2026-09-01','2026-09-30')['summary']
        self.assertEqual(summary['entry_count'],1)
        self.assertEqual(summary['valuations'][0]['balance'],'2000.00')
        dissolve_group(group['id'],OffsetDelete(expected_version=1))
        self.assertEqual(search_personal('2026-09-01','2026-09-30')['summary']['entry_count'],5)
        self.assertEqual(self.balance(),before)
        with database.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM change_audit_logs WHERE entity_type='offset_group'").fetchone()[0],2)
        self.assertEqual(len(list(database.BACKUP_DIR.glob('*.db'))),2)

    def test_append_cross_month_and_filter_complete_event(self):
        a=self.txn('expense',100,'2026-09-10',note='聚餐')
        g=create_group(self.payload([a],day='2026-09-10'))
        b=self.txn('income',80,'2026-10-02',currency='HKD',note='特定回款')
        self.rate('2026-10-02','1')
        g=update_group(g['id'],self.payload([a,b],version=1,day='2026-09-10'))
        data=search_personal('2026-09-01','2026-09-30',transaction_types=['expense'],category_ids=[self.categories['expense']],
                              involved_account_id=self.accounts['HKD'],keyword='特定回款')
        self.assertEqual(data['summary']['valuations'][0]['expense'],'20.00')
        self.assertEqual(len(data['entries'][0]['members']),2)
        self.assertEqual(search_personal('2026-10-01','2026-10-31')['pagination']['total'],0)
        self.assertEqual(g['statistics_date'],'2026-09-10')

    def test_negative_zero_and_daily_histogram(self):
        for spend,receive,day in [(100,120,'2026-09-01'),(100,100,'2026-09-02'),(100,70,'2026-09-03')]:
            ids=[self.txn('expense',spend,day,'HKD'),self.txn('income',receive,day,'HKD')]
            create_group(self.payload(ids,day=day))
        data=analyze_personal('2026-09-01','2026-09-03','expense')
        self.assertEqual(data['statistics']['total_amount'],'10.00')
        self.assertEqual(data['statistics']['active_day_count'],3)
        self.assertEqual([x['amount'] for x in data['daily_totals']],['-20.00','0.00','30.00'])
        self.assertEqual(sum(x['count'] for x in data['histogram']),3)
        self.assertLessEqual(data['histogram'][0]['start'],-20)
        for values in [[Decimal('-0.3')],[Decimal('-2.1'),Decimal('-1.2')],[Decimal(0)]]:
            bins=build_hkd_histogram(values)
            self.assertEqual(sum(b['count'] for b in bins),len(values))
            self.assertLessEqual(bins[0]['start'],min(values))
        g=create_group(self.payload([self.txn('income',100),self.txn('expense',120)],'income'))
        self.assertEqual(g['valuations']['CNY']['amount'],'-20.00')

    def test_fx_each_original_day_and_conservation(self):
        self.rate('2026-09-02','1')
        ids=[self.txn('expense',100,'2026-09-01','HKD'),self.txn('income',100,'2026-09-02','HKD')]
        before=search_personal('2026-09-01','2026-09-30')['summary']['valuations']
        group=create_group(self.payload(ids))
        self.assertEqual(group['raw_totals'][0]['net'],'0.00')
        self.assertEqual(group['valuations']['CNY']['amount'],'-10.00')
        self.assertEqual(group['valuations']['HKD']['amount'],'0.00')
        after=search_personal('2026-09-01','2026-09-30')['summary']['valuations']
        self.assertEqual([v['balance'] for v in before],[v['balance'] for v in after])
        self.assertEqual([m['rate_date'] for m in group['valuations']['CNY']['members']],['2026-09-01','2026-09-02'])

    def test_missing_rate_excludes_whole_event_and_never_double_counts(self):
        ids=[self.txn('expense',100),self.txn('income',80,currency='EUR')]
        create_group(self.payload(ids))
        result=search_personal('2026-09-01','2026-09-30')
        self.assertEqual(result['pagination']['total'],1)
        self.assertIsNone(result['entries'][0]['valuations']['CNY']['amount'])
        self.assertFalse(result['summary']['valuations'][0]['complete'])
        self.assertEqual(result['summary']['valuations'][0]['expense'],'0.00')
        self.assertEqual(result['summary']['valuations'][0]['excluded_count'],1)

    def test_one_basis_can_be_available_without_any_rates(self):
        with database.connect() as c: c.execute('DELETE FROM daily_cny_exchange_rates')
        g=create_group(self.payload([self.txn('expense',100),self.txn('income',80)]))
        self.assertEqual(g['valuations']['CNY']['amount'],'20.00')
        self.assertIsNone(g['valuations']['HKD']['amount'])

    def test_pagination_and_ungrouped_records(self):
        for n in range(6): self.txn('expense',n+1)
        result=search_personal('2026-09-01','2026-09-30',limit=2,offset=2)
        self.assertEqual(result['pagination']['total'],6)
        self.assertEqual(len(result['entries']),2)
        self.assertEqual(result['summary']['valuations'][0]['expense'],'21.00')

    def test_membership_conflicts_versions_and_invalid_anchor(self):
        a=self.txn('expense',100); b=self.txn('income',80)
        group=create_group(self.payload([a,b]))
        for operation in [lambda:create_group(self.payload([a])),
                          lambda:update_group(group['id'],self.payload([a,b],version=9))]:
            with self.assertRaises(HTTPException) as e: operation()
            self.assertEqual(e.exception.status_code,409)
        with self.assertRaises(HTTPException): update_group(group['id'],self.payload([b],anchor=b,version=1))
        self.assertEqual(get_group(group['id'])['version'],1)
        self.assertEqual(get_group(group['id'])['member_transaction_ids'],[a,b])

    def test_member_edit_updates_version_and_attribution_and_blocks_delete(self):
        a=self.txn('expense',100);b=self.txn('income',80)
        group=create_group(self.payload([a,b]))
        update_transaction(a,TransactionUpdate(transaction_date='2026-08-01',transaction_type='expense',source_account_id=self.accounts['CNY'],outflow_amount='120',category_id=self.categories['expense']))
        changed=get_group(group['id'])
        self.assertEqual(changed['version'],2)
        self.assertEqual(changed['statistics_date'],'2026-08-01')
        self.assertEqual(changed['valuations']['CNY']['amount'],'40.00')
        with self.assertRaises(HTTPException) as e: delete_transaction(a,TransactionDelete(reason='测试'))
        self.assertEqual(e.exception.status_code,409)
        with self.assertRaises(HTTPException):
            update_transaction(a,TransactionUpdate(transaction_date='2026-08-01',transaction_type='income',destination_account_id=self.accounts['CNY'],inflow_amount='120',category_id=self.categories['income']))
        self.assertEqual(get_group(group['id'])['version'],2)

    def test_auto_attribution_tracks_add_remove_and_original_category(self):
        later=self.txn('expense',100,'2026-09-15')
        earlier=self.txn('expense',50,'2026-09-10')
        returned=self.txn('income',20,'2026-08-31')
        with database.connect() as c:
            other=c.execute("SELECT id FROM categories WHERE kind='expense' AND id!=? ORDER BY id LIMIT 1",(self.categories['expense'],)).fetchone()[0]
            c.execute('UPDATE transactions SET category_id=? WHERE id=?',(other,earlier))
            # Existing records retain historical inactive categories automatically.
            c.execute('UPDATE categories SET is_active=0 WHERE id=?',(other,))
            originals=[dict(r) for r in c.execute('SELECT * FROM transactions ORDER BY id')]
        group=create_group(OffsetWrite(title='自动归属',kind='expense',transaction_ids=[later,returned]))
        self.assertEqual(group['statistics_date'],'2026-08-31')
        self.assertEqual(group['anchor_transaction_id'],later)
        self.assertEqual(group['category_id'],self.categories['expense'])
        group=update_group(group['id'],OffsetWrite(title='自动归属',kind='expense',transaction_ids=[later,returned,earlier],expected_version=1))
        self.assertEqual(group['statistics_date'],'2026-08-31')
        self.assertEqual(group['category_id'],other)
        self.assertEqual(group['anchor_transaction_id'],earlier)
        group=update_group(group['id'],OffsetWrite(title='自动归属',kind='expense',transaction_ids=[later],expected_version=2))
        self.assertEqual(group['statistics_date'],'2026-09-15')
        self.assertEqual(group['category_id'],self.categories['expense'])
        with database.connect() as c:
            self.assertEqual([dict(r) for r in c.execute('SELECT * FROM transactions ORDER BY id')],originals)

    def test_old_override_fields_ignored_and_ties_use_original_id(self):
        first=self.txn('expense',100);second=self.txn('expense',50)
        with database.connect() as c:
            other=c.execute("SELECT id FROM categories WHERE kind='expense' AND id!=? ORDER BY id LIMIT 1",(self.categories['expense'],)).fetchone()[0]
            c.execute('UPDATE transactions SET category_id=? WHERE id=?',(other,second))
        group=create_group(OffsetWrite(title='旧客户端',kind='expense',transaction_ids=[second,first],
            anchor_transaction_id=second,statistics_date='2025-01-01',category_id=other))
        self.assertEqual(group['anchor_transaction_id'],first)
        self.assertEqual(group['statistics_date'],'2026-09-01')
        self.assertEqual(group['category_id'],self.categories['expense'])
        with database.connect() as c:
            c.execute("UPDATE transaction_offset_groups SET statistics_date='2025-01-01',category_id=?,anchor_transaction_id=? WHERE id=?",(other,second,group['id']))
        self.assertEqual(get_group(group['id'])['statistics_date'],'2026-09-01')
        result=search_personal('2026-09-01','2026-09-30',category_ids=[self.categories['expense']])
        self.assertEqual(result['summary']['valuations'][0]['expense'],'150.00')
        self.assertEqual(dashboard('2026-09','offset')['personal_cash_flow']['valuations'][0]['expense'],'150.00')
        workbook=load_workbook(build_excel_export('2026-09','offset'))
        self.assertEqual(workbook['月度总览']['C4'].value,'150.00')

    def test_edit_original_can_replace_primary_and_updates_cached_attribution(self):
        first=self.txn('expense',100,'2026-09-01');second=self.txn('expense',80,'2026-09-05')
        group=create_group(OffsetWrite(title='调整主流水',kind='expense',transaction_ids=[first,second]))
        update_transaction(first,TransactionUpdate(transaction_date='2026-08-31',transaction_type='income',destination_account_id=self.accounts['CNY'],inflow_amount='100',category_id=self.categories['income']))
        changed=get_group(group['id'])
        self.assertEqual(changed['anchor_transaction_id'],second)
        self.assertEqual(changed['statistics_date'],'2026-08-31')
        self.assertEqual(changed['category_id'],self.categories['expense'])
        self.assertEqual(changed['valuations']['CNY']['amount'],'-20.00')
        with database.connect() as c:
            cached=dict(c.execute('SELECT * FROM transaction_offset_groups WHERE id=?',(group['id'],)).fetchone())
            for key in ('statistics_date','category_id','anchor_transaction_id','version'):
                self.assertEqual(cached[key],changed[key])

    def test_only_groups_filter_search_analysis_and_raw_compatibility(self):
        ids=[self.txn('expense',100,currency='HKD'),self.txn('income',20,currency='HKD')]
        other=self.txn('expense',50,currency='HKD')
        client=TestClient(app)
        before=client.get('/api/transactions/search?start_date=2026-09-01&end_date=2026-09-30').json()
        response=client.post('/api/offset-groups',json={'title':'无需填写时期分类','kind':'expense','transaction_ids':ids})
        self.assertEqual(response.status_code,201,response.text)
        only=client.get('/api/personal-cashflow/search?start_date=2026-09-01&end_date=2026-09-30&entry_type=offset_group').json()
        self.assertEqual(only['pagination']['total'],1)
        self.assertEqual(only['summary']['valuations'][1]['expense'],'80.00')
        raw=client.get('/api/transactions/search?start_date=2026-09-01&end_date=2026-09-30').json()
        self.assertEqual(raw['summary'],before['summary'])
        self.assertEqual(raw['pagination'],before['pagination'])
        separate=client.get('/api/personal-cashflow/search?entry_type=transaction').json()
        self.assertEqual([e['id'] for e in separate['entries']],[other])
        analysis=client.get('/api/personal-cashflow/analysis?start_date=2026-09-01&end_date=2026-09-30&transaction_type=expense&entry_type=offset_group').json()
        self.assertEqual(analysis['statistics']['total_amount'],'80.00')
        self.assertEqual(analysis['entry_count'],1)
        self.assertEqual(client.get('/api/personal-cashflow/search?entry_type=bad').status_code,422)
        client.close()

    def test_preview_is_read_only_and_failure_rolls_back(self):
        a=self.txn('expense',100)
        result=preview_group(self.payload([a]))
        self.assertEqual(result['valuations']['CNY']['amount'],'100.00')
        self.assertFalse(database.BACKUP_DIR.exists())
        with patch('app.offsets.audit_group',side_effect=RuntimeError('failed audit')):
            with self.assertRaises(RuntimeError):create_group(self.payload([a]))
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transaction_offset_groups').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transaction_offset_members').fetchone()[0],0)

    def test_export_budget_and_cross_month_members(self):
        a=self.txn('expense',100,'2026-09-01');b=self.txn('income',120,'2026-10-01')
        create_group(self.payload([a,b]))
        with database.connect() as c:
            c.execute("INSERT INTO monthly_budgets (budget_month,category_id,currency_code,amount_minor) VALUES ('2026-09-01',?,'CNY',10000)",(self.categories['expense'],))
        workbook=load_workbook(build_excel_export('2026-09','offset'))
        self.assertEqual(workbook['月度总览']['C4'].value,'-20.00')
        self.assertEqual(workbook['月度预算']['E4'].value,'-20.00')
        self.assertEqual(workbook['冲销关联明细'].max_row,5)
        self.assertEqual(workbook['冲销关联明细']['E5'].value,'2026-10-01')
        self.assertIn('原始月度总览',workbook.sheetnames)
        self.assertEqual(workbook['交易流水'].max_row,5)

    def test_legacy_schema_backup_and_idempotent_migration(self):
        with database.connect() as c:
            c.execute('DROP TABLE transaction_offset_members');c.execute('DROP TABLE transaction_offset_groups')
            c.execute('DROP TABLE change_audit_logs')
            c.execute('''CREATE TABLE change_audit_logs (id INTEGER PRIMARY KEY, entity_type TEXT CHECK(entity_type IN ('transaction','investment_valuation','exchange_rate')),entity_id INTEGER,action TEXT,reason TEXT,before_json TEXT,after_json TEXT,backup_file TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            c.execute("INSERT INTO change_audit_logs (entity_type,action,backup_file) VALUES ('exchange_rate','create','old.db')")
        database.initialize_database();database.initialize_database()
        with database.connect() as c:
            self.assertEqual(c.execute('SELECT entity_type FROM change_audit_logs').fetchone()[0],'exchange_rate')
            self.assertIn('offset_group',c.execute("SELECT sql FROM sqlite_master WHERE name='change_audit_logs'").fetchone()[0])
        backups=list(database.BACKUP_DIR.glob('finance-pre-offset-*.db'))
        self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name='transaction_offset_groups'").fetchone()[0],0)

    def test_http_routes_candidate_selection_validation_and_static_pages(self):
        a=self.txn('expense',100);b=self.txn('income',80)
        # No lifespan: no real startup FX sync. Use the already initialized isolated DB.
        client=TestClient(app)
        payload=self.payload([a,b]).model_dump(mode='json')
        response=client.post('/api/offset-groups/preview',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        response=client.post('/api/offset-groups',json=payload)
        self.assertEqual(response.status_code,201,response.text)
        group=response.json()
        self.assertEqual(client.get('/api/offset-groups').json()['pagination']['total'],1)
        candidate=client.get('/api/offset-candidates?transaction_type=expense').json()['transactions'][0]
        self.assertEqual(candidate['offset_group_id'],group['id'])
        original=client.get('/api/transactions/search').json()['transactions']
        self.assertTrue(all(r['offset_group_id']==group['id'] for r in original))
        self.assertEqual(client.get('/api/personal-cashflow/search?transaction_types=transfer').status_code,422)
        self.assertEqual(client.get('/api/personal-cashflow/search?start_date=bad').status_code,422)
        self.assertEqual(client.get('/api/personal-cashflow/analysis?start_date=2025-01-01&end_date=2026-09-01&transaction_type=expense').status_code,422)
        for url in ('/','/overview','/accounts','/investments','/offsets'):
            page=client.get(url);self.assertEqual(page.status_code,200);self.assertIn('href="/offsets"',page.text)
            self.assertEqual(page.headers['cache-control'],'no-store')
        accounts_page=client.get('/accounts').text
        self.assertIn('id="search-budget-options"',accounts_page)
        self.assertIn('id="select-all-expense-categories"',accounts_page)
        self.assertIn('id="invert-expense-categories"',accounts_page)
        self.assertEqual(client.get('/api/dashboard?month=2026-09&basis=offset').json()['personal_cash_flow']['valuations'][0]['expense'],'20.00')
        self.assertEqual(client.get('/api/export.xlsx?month=2026-09&basis=offset').status_code,200)
        client.close()


if __name__=='__main__': unittest.main()

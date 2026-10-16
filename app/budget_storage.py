"""Storage envelopes: a projection of plans, net expenses and explicit reallocations."""
import hashlib
import json
from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import connect
from app.budgets import config_data, currency, money, minor, mutation, require_system, valid_month
from app.budget_execution import execution_report, member_amount
from app.personal_cashflow import build_entries, read_ledger

router = APIRouter()


def entry_token(entry):
    return hashlib.sha256(json.dumps(entry,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()


def snapshot(c, system_id):
    return {table:[dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE budget_system_id=?',(system_id,))]
            for table in ('budget_storage_defaults','budget_storage_expenses','budget_storage_transfers')}


def storage_key(storage_id):
    return str(storage_id) if storage_id is not None else 'unallocated'


def require_storage(c, storage_id, allow_inactive=False):
    if storage_id is None:
        return
    row = next((r for r in config_data(c)['storage'] if r['id']==storage_id),None)
    if not row or (not allow_inactive and (row['is_placeholder'] or not row['selectable'])):
        raise HTTPException(422,'请选择有效的末级存储方式；停用项目仍可保留历史或调出已有额度。')


def transfers_data(c, system_id):
    rows = [dict(r) for r in c.execute('SELECT * FROM budget_storage_transfers WHERE budget_system_id=? ORDER BY month,id',(system_id,))]
    names = {r['id']:r['path'] for r in config_data(c)['storage']}
    for row in rows:
        row.update(amount=money(row['amount_minor'],currency(c,row['currency_code'])),
                   source_name=names.get(row['source_id'],'待分配'),destination_name=names.get(row['destination_id'],'待分配'))
    return rows


def storage_report(c, end_month, system_id=None, currency_code=None, execution=None, details=False):
    valid_month(end_month)
    config = config_data(c)
    execution = execution or execution_report(c,end_month,system_id=system_id,currency_code=currency_code)
    systems = {s['id']:s for s in config['systems']}
    storage = {s['id']:s for s in config['storage']}
    cash_root = next((s for s in config['storage'] if s['parent_id'] is None and s['name'].strip()=='活期'
                      and s['is_active'] and not s['is_placeholder']),None)
    cash_keys = []
    if cash_root:
        for item in config['storage']:
            current = item
            while current and current['parent_id']:
                current = storage.get(current['parent_id'])
            if current and current['id']==cash_root['id'] and item['is_active'] and not item['is_placeholder']:
                cash_keys.append(storage_key(item['id']))
    defaults = {r['budget_system_id']:r['storage_method_id'] for r in c.execute('SELECT * FROM budget_storage_defaults')}
    overrides = {(r['budget_system_id'],'transaction' if r['transaction_id'] else 'offset_group',r['transaction_id'] or r['group_id']):r['storage_method_id']
                 for r in c.execute('SELECT * FROM budget_storage_expenses')}
    records,groups,rates = read_ledger(c)
    entries = build_entries(records,groups,rates)
    plans = [dict(r) for r in c.execute('SELECT * FROM budget_plans WHERE month<=?',(end_month,))]
    items = defaultdict(list)
    for row in c.execute('SELECT * FROM budget_plan_items'):
        items[row['plan_id']].append(dict(row))
    rows = []
    for execution_row in execution['rows']:
        sid = execution_row['budget_system_id']; system = systems[sid]
        code = system['currency_code']; places = currency(c,code) if code else 2
        origin = execution_row['origin_month']; complete = execution_row['complete']
        balances = defaultdict(int); balances['unallocated'] = system['opening_minor'] if execution_row['started'] else 0
        balances['pending'] = 0
        used_default = defaults.get(sid)
        if used_default:
            balances[storage_key(used_default)] += 0
        for p in plans:
            if p['budget_system_id']!=sid or p['currency_code']!=code or not origin<=p['month']<=end_month:
                continue
            allocated = sum(i['amount_minor'] for i in items[p['id']])
            balances['unallocated'] += (p['total_minor'] if p['total_minor'] is not None else allocated)-allocated
            for item in items[p['id']]:
                balances[storage_key(item['storage_method_id'])] += item['amount_minor']
        moves = transfers_data(c,sid)
        for move in moves:
            if origin<=move['month']<=end_month and move['currency_code']==code:
                balances[storage_key(move['source_id'])] -= move['amount_minor']
                balances[storage_key(move['destination_id'])] += move['amount_minor']
        monthly = defaultdict(list); expense_rows = []; automatic_rows = []
        for entry in entries:
            if entry['kind']!='expense' or entry['budget_mixed'] or entry['budget_system_id']!=sid or not origin<=entry['statistics_date'][:7]<=end_month:
                continue
            override = overrides.get((sid,entry['entry_type'],entry['entry_id']))
            target = override or used_default
            key = storage_key(target) if target else None
            total = Decimal(0); missing = False
            for member in entry['members']:
                value,_ = member_amount(member,rates,code)
                if value is None:
                    missing = True
                else:
                    total += value if member['transaction_type']=='expense' else -value
            row = dict(entry_type=entry['entry_type'],entry_id=entry['entry_id'],title=entry['title'],date=entry['statistics_date'],
                       token=entry_token(entry),storage_method_id=target,override_storage_id=override,
                       inherited=override is None,automatic=target is None,key=key,amount=None,
                       url=f"/offsets?group={entry['entry_id']}" if entry['entry_type']=='offset_group' else f"/accounts?transaction={entry['entry_id']}")
            expense_rows.append(row)
            if not missing:
                scaled = total*10**places
                floor = int(scaled.to_integral_value(rounding=ROUND_FLOOR))
                monthly[entry['statistics_date'][:7]].append((row,scaled,floor))
        # Round once per month, as execution does; distribute sub-cent remainders deterministically.
        for values in monthly.values():
            target = int(sum((v[1] for v in values),Decimal(0)).quantize(Decimal(1),rounding=ROUND_HALF_UP))
            remainder = target-sum(v[2] for v in values)
            ordered = sorted(values,key=lambda v:(-(v[1]-v[2]),v[0]['date'],v[0]['entry_type'],v[0]['entry_id']))
            for index,(row,_,floor) in enumerate(ordered):
                amount = floor+int(index<remainder)
                row['amount'] = money(amount,places)
                if row['key'] is None:
                    automatic_rows.append((row,amount))
                else:
                    balances[row['key']] -= amount
        automatic_total = sum(amount for _,amount in automatic_rows)
        automatic_unallocated = automatic_cash = pending_amount = 0
        if automatic_total < 0:
            # With no explicit storage to restore, a net refund becomes newly unallocated budget.
            balances['unallocated'] -= automatic_total
        elif automatic_total > 0:
            automatic_unallocated = min(automatic_total,max(balances['unallocated'],0))
            balances['unallocated'] -= automatic_unallocated
            remaining = automatic_total-automatic_unallocated
            for cash_key in cash_keys:
                used = min(remaining,max(balances.get(cash_key,0),0))
                balances[cash_key] -= used
                automatic_cash += used
                remaining -= used
                if not remaining:
                    break
            pending_amount = remaining
            balances['pending'] -= pending_amount
        pending_count = sum(amount>0 for _,amount in automatic_rows) if pending_amount else 0
        cells = []
        for key,value in balances.items():
            item = storage.get(int(key)) if key.isdigit() else None
            root = item
            while root and root['parent_id']:
                root = storage[root['parent_id']]
            cells.append(dict(key=key,storage_method_id=item['id'] if item else None,
                              label=item['path'] if item else '待分配' if key=='unallocated' else '待归属消费',
                              root_key=str(root['id']) if root else key,root_label=root['path'] if root else '待分配' if key=='unallocated' else '待归属消费',
                              balance=money(value,places) if complete else None))
        if complete and money(sum(balances.values()),places)!=execution_row['closing']:
            raise HTTPException(409,'存储余额与预算结转不一致，请检查预算及存储记录。')
        row = dict(budget_system_id=sid,system_name=system['name'],is_active=system['is_active'],currency_code=code,
                   decimal_places=places,month=end_month,origin_month=origin,complete=complete,reason=execution_row['reason'],
                   total=execution_row['closing'],default_storage_method_id=used_default,
                   pending_count=pending_count,negative=complete and any(v<0 for k,v in balances.items() if k!='pending'),
                   pending_amount=money(pending_amount,places),automatic_count=len(automatic_rows),
                   automatic_unallocated=money(automatic_unallocated,places),automatic_cash=money(automatic_cash,places),
                   automatic_cash_storage_id=cash_root['id'] if cash_root else None,
                   cells=cells,funding_warnings=execution_row.get('funding_warnings',[]),warnings=execution_row['warnings'],
                   transfers=[dict(m,in_range=origin<=m['month']) for m in moves if m['month']<=end_month])
        if details:
            row.update(expenses=[e for e in expense_rows if e['date'][:7]==end_month])
        rows.append(row)
    return dict(end_month=end_month,rows=rows)


@router.get('/api/budget/storage/{system_id}/{month}')
def storage_detail(system_id: int, month: str):
    with connect() as c:
        c.execute('BEGIN')
        report = storage_report(c,month,system_id=system_id,details=True)
        if not report['rows']:
            raise HTTPException(404,'此预算暂无可查看的计划或消费。')
        return dict(row=report['rows'][0],config=config_data(c))


class StorageWrite(BaseModel):
    expected_version: int = Field(ge=1)
    storage_method_id: int | None = Field(default=None,gt=0)


@router.put('/api/budget/storage/{system_id}/default')
def set_storage_default(system_id: int, body: StorageWrite):
    with mutation(body.expected_version,lambda c:snapshot(c,system_id)) as c:
        require_system(c,system_id)
        previous = c.execute('SELECT storage_method_id FROM budget_storage_defaults WHERE budget_system_id=?',(system_id,)).fetchone()
        require_storage(c,body.storage_method_id,bool(previous and previous[0]==body.storage_method_id))
        if body.storage_method_id:
            c.execute('INSERT INTO budget_storage_defaults VALUES (?,?) ON CONFLICT(budget_system_id) DO UPDATE SET storage_method_id=excluded.storage_method_id',(system_id,body.storage_method_id))
        else:
            c.execute('DELETE FROM budget_storage_defaults WHERE budget_system_id=?',(system_id,))
    return {'saved':True}


class ExpenseWrite(StorageWrite):
    entry_type: Literal['transaction','offset_group']
    entry_id: int = Field(gt=0)
    expected_entry_token: str = Field(min_length=64,max_length=64)


@router.put('/api/budget/storage/{system_id}/expense')
def set_expense_storage(system_id: int, body: ExpenseWrite):
    with mutation(body.expected_version,lambda c:snapshot(c,system_id)) as c:
        require_system(c,system_id)
        records,groups,rates = read_ledger(c)
        entry = next((e for e in build_entries(records,groups,rates) if e['entry_type']==body.entry_type and e['entry_id']==body.entry_id),None)
        if not entry or entry['kind']!='expense' or entry['budget_mixed'] or entry['budget_system_id']!=system_id:
            raise HTTPException(409,'消费事项或预算归属已变化，请重新加载后选择。')
        if entry_token(entry)!=body.expected_entry_token:
            raise HTTPException(409,'消费事项已更新，请重新加载后核对扣款归属。')
        column = 'transaction_id' if body.entry_type=='transaction' else 'group_id'
        old = c.execute(f'SELECT * FROM budget_storage_expenses WHERE budget_system_id=? AND {column}=?',(system_id,body.entry_id)).fetchone()
        require_storage(c,body.storage_method_id,bool(old and old['storage_method_id']==body.storage_method_id))
        c.execute(f'DELETE FROM budget_storage_expenses WHERE budget_system_id=? AND {column}=?',(system_id,body.entry_id))
        if body.storage_method_id:
            c.execute(f'INSERT INTO budget_storage_expenses(budget_system_id,{column},storage_method_id) VALUES (?,?,?)',(system_id,body.entry_id,body.storage_method_id))
    return {'saved':True}


class TransferWrite(BaseModel):
    expected_version: int = Field(ge=1)
    transfer_version: int = Field(default=0,ge=0)
    month: str
    source_id: int | None = Field(default=None,gt=0)
    destination_id: int | None = Field(default=None,gt=0)
    amount: Decimal = Field(gt=0)
    note: str = Field(default='',max_length=500)


def save_transfer(system_id, body, transfer_id=None):
    valid_month(body.month)
    with mutation(body.expected_version,lambda c:snapshot(c,system_id)) as c:
        system = require_system(c,system_id)
        if not system['currency_code']:
            raise HTTPException(422,'请先设置预算币种。')
        old = c.execute('SELECT * FROM budget_storage_transfers WHERE id=? AND budget_system_id=?',(transfer_id,system_id)).fetchone() if transfer_id else None
        if transfer_id and (not old or old['version']!=body.transfer_version):
            raise HTTPException(409,'调拨已更新或撤销，请重新加载。')
        if body.source_id==body.destination_id:
            raise HTTPException(422,'转出与转入存储不能相同。')
        require_storage(c,body.source_id,True)
        require_storage(c,body.destination_id,bool(old and old['destination_id']==body.destination_id))
        amount = minor(body.amount,currency(c,system['currency_code']))
        if old:
            c.execute('DELETE FROM budget_storage_transfers WHERE id=?',(transfer_id,))
        row = storage_report(c,body.month,system_id=system_id)['rows'][0]
        if not row['complete'] or body.month<row['origin_month']:
            raise HTTPException(422,'该月余额未完整计算或早于起算月份，不能据此调拨。')
        if row['pending_count']:
            raise HTTPException(422,'尚有待归属消费，请先设置默认扣款存储或单独指定，再按剩余额度调拨。')
        source = next((s for s in row['cells'] if s['key']==storage_key(body.source_id)),None)
        available = Decimal(source['balance']) if source else Decimal(0)
        if body.amount>available:
            raise HTTPException(422,f"转出存储仅剩 {available} {system['currency_code']}，不足以调拨；请先调整金额或选择其他存储。")
        version = old['version']+1 if old else 1
        cursor = c.execute('''INSERT INTO budget_storage_transfers(id,budget_system_id,month,source_id,destination_id,currency_code,amount_minor,note,version)
            VALUES (?,?,?,?,?,?,?,?,?)''',(transfer_id,system_id,body.month,body.source_id,body.destination_id,system['currency_code'],amount,body.note,version))
        result = dict(id=transfer_id or cursor.lastrowid,version=version)
    return result


@router.post('/api/budget/storage/{system_id}/transfers',status_code=201)
def create_transfer(system_id: int, body: TransferWrite):
    return save_transfer(system_id,body)


@router.put('/api/budget/storage/{system_id}/transfers/{transfer_id}')
def update_transfer(system_id: int, transfer_id: int, body: TransferWrite):
    return save_transfer(system_id,body,transfer_id)


class TransferDelete(BaseModel):
    expected_version: int = Field(ge=1)
    transfer_version: int = Field(ge=1)


@router.delete('/api/budget/storage/{system_id}/transfers/{transfer_id}')
def delete_transfer(system_id: int, transfer_id: int, body: TransferDelete):
    with mutation(body.expected_version,lambda c:snapshot(c,system_id)) as c:
        require_system(c,system_id)
        deleted = c.execute('DELETE FROM budget_storage_transfers WHERE id=? AND budget_system_id=? AND version=?',(transfer_id,system_id,body.transfer_version))
        if not deleted.rowcount:
            raise HTTPException(409,'调拨已更新或撤销，请重新加载。')
    return {'deleted':True}


def correct_currency(c, system_id, target):
    places = currency(c,target)
    for row in transfers_data(c,system_id):
        amount = minor(row['amount'],places)
        c.execute('UPDATE budget_storage_transfers SET currency_code=?,amount_minor=?,version=version+1 WHERE id=?',(target,amount,row['id']))

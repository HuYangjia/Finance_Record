"""Manual budget plans and transaction attribution; never changes actual balances."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from io import BytesIO
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

from app.database import ROOT_DIR, connect, create_backup

router = APIRouter()
COLLECTIONS = {'systems': 'budget_systems', 'storage': 'budget_storage_methods',
               'purposes': 'budget_purposes', 'rules': 'budget_assignment_rules',
               'presets': 'budget_entry_presets'}
DEFAULT_KEYS = {mode: f'default_{mode}_budget_system_id' for mode in ('expense', 'income')}
DAILY_EXPENSE_KEY = 'daily_expense_budget_system_id'


def daily_expense_system(c):
    row = c.execute('''SELECT b.id,b.name FROM budget_systems b JOIN app_settings s
        ON b.id=CAST(s.value AS INTEGER) WHERE s.key=?''', (DAILY_EXPENSE_KEY,)).fetchone()
    return dict(row) if row else None


def effective_budget(record, daily):
    inherited = record.get('transaction_type') == 'expense' and record.get('budget_system_id') is None and daily is not None
    record.update(
        effective_budget_system_id=daily['id'] if inherited else record.get('budget_system_id'),
        effective_budget_system_name=daily['name'] if inherited else record.get('budget_system_name'),
        budget_inherited=inherited,
    )
    return record


def require_system(c, system_id, allow_inactive=False):
    if system_id is None:
        return None
    row = c.execute('SELECT * FROM budget_systems WHERE id=?', (system_id,)).fetchone()
    if not row or (not row['is_active'] and not allow_inactive):
        raise HTTPException(422, '预算不存在或已停用。')
    return dict(row)


def budget_filter_sql(filters, parameters, budget_system_id):
    """Filter the effective budget; untagged expenses inherit the daily system."""
    effective = """COALESCE(transactions.budget_system_id, CASE WHEN transactions.transaction_type='expense'
        THEN (SELECT CAST(value AS INTEGER) FROM app_settings WHERE key='daily_expense_budget_system_id') END)"""
    if budget_system_id is not None:
        if budget_system_id < 0:
            raise HTTPException(422, '预算筛选不正确。')
        if budget_system_id == 0:
            filters.append(f'{effective} IS NULL')
        else:
            filters.append(f'{effective} = ?')
            parameters.append(budget_system_id)


def paths(rows):
    by_id = {r['id']: r for r in rows}
    for row in rows:
        names, seen, current = [], set(), row
        active = True
        while current:
            if current['id'] in seen:
                raise HTTPException(409, '预算栏目层级存在循环。')
            seen.add(current['id']); names.append(current['name'])
            active = active and bool(current['is_active'])
            current = by_id.get(current.get('parent_id'))
        row['path'] = ' / '.join(reversed(names))
        row['selectable'] = active and not any(r.get('parent_id') == row['id'] for r in rows)
    return rows


def config_data(c):
    data = {key: [dict(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY '+('sort_order,id' if key in ('systems','storage','purposes') else 'id'))]
            for key, table in COLLECTIONS.items()}
    for system in data['systems']:
        codes = [r[0] for r in c.execute('SELECT DISTINCT currency_code FROM budget_plans WHERE budget_system_id=? ORDER BY currency_code', (system['id'],))]
        system['plan_currencies'] = codes
        system['currency_conflict'] = len(codes)>1 or bool(codes and system['currency_code'] and codes != [system['currency_code']])
    for key in ('storage', 'purposes'):
        paths(data[key])
    settings = {r['key']: r['value'] for r in c.execute('SELECT * FROM app_settings')}
    data['defaults'] = {mode: json.loads(settings.get(key, 'null')) for mode, key in DEFAULT_KEYS.items()}
    data['daily_expense'] = json.loads(settings.get(DAILY_EXPENSE_KEY, 'null'))
    data['version'] = int(settings.get('budget_config_version', '1'))
    data['currencies'] = [dict(r) for r in c.execute('SELECT * FROM currencies ORDER BY code')]
    return data


@contextmanager
def mutation(expected_config=None):
    backup = create_backup()
    try:
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            before = config_data(c) if expected_config is not None else None
            if before is not None and expected_config != before['version']:
                raise HTTPException(409, '设置已更新，请重新加载后再编辑。')
            yield c
            if before is not None:
                c.execute("UPDATE app_settings SET value=?,updated_at=CURRENT_TIMESTAMP WHERE key='budget_config_version'", (str(before['version'] + 1),))
            c.execute('''INSERT INTO change_audit_logs(entity_type,action,before_json,after_json,backup_file)
                         VALUES ('budget','update',?,?,?)''',
                      (json.dumps(before, ensure_ascii=False) if before else None,
                       json.dumps(config_data(c), ensure_ascii=False) if before else '{"operation":"plan_revision"}', backup.name))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, '名称重复或存在引用，请保留历史项目并使用停用。') from exc


class ConfigWrite(BaseModel):
    expected_version: int = Field(ge=1)
    name: str = Field(default='', max_length=120)
    budget_system_id: int | None = Field(default=None, gt=0)
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    is_active: bool = True
    note: str = Field(default='', max_length=500)
    mode: Literal['income', 'expense'] = 'expense'
    category_id: int | None = Field(default=None, gt=0)
    account_id: int | None = Field(default=None, gt=0)
    currency_code: str | None = Field(default=None, min_length=1, max_length=12)


class DefaultsWrite(BaseModel):
    expected_version: int = Field(ge=1)
    expense: int | None = Field(default=None, gt=0)
    income: int | None = Field(default=None, gt=0)
    daily_expense: int | None = Field(default=None, gt=0)


class CatalogCopy(BaseModel):
    expected_version: int = Field(ge=1)
    source_system_id: int = Field(gt=0)
    target_system_id: int = Field(gt=0)


@router.post('/api/budget/copy-catalog')
def copy_catalog(body: CatalogCopy):
    if body.source_system_id == body.target_system_id:
        raise HTTPException(422, '请选择不同的来源预算。')
    with mutation(body.expected_version) as c:
        require_system(c,body.source_system_id,True); require_system(c,body.target_system_id)
        for table in ('budget_purposes',):
            if c.execute(f'SELECT 1 FROM {table} WHERE budget_system_id=? AND is_placeholder=0',(body.target_system_id,)).fetchone():
                raise HTTPException(409, '目标预算已有自定义栏目，请使用仅含待定栏目的新预算。')
            remaining=[dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE budget_system_id=? AND is_placeholder=0',(body.source_system_id,))]
            mapping={}
            while remaining:
                ready=[r for r in remaining if r['parent_id'] is None or r['parent_id'] in mapping]
                if not ready:
                    raise HTTPException(409, '来源栏目层级异常，无法复制。')
                for row in ready:
                    mapping[row['id']]=c.execute(f'INSERT INTO {table}(budget_system_id,parent_id,name,sort_order,is_active,note) VALUES (?,?,?,?,?,?)',
                        (body.target_system_id,mapping.get(row['parent_id']),row['name'],row['sort_order'],row['is_active'],row['note'])).lastrowid
                    remaining.remove(row)
    return get_config()


@router.get('/api/budget/config/{collection}/{item_id}/impact')
def config_impact(collection: str, item_id: int):
    if collection == 'systems':
        with connect() as c:
            require_system(c,item_id,True)
            filters, parameters = [], []
            budget_filter_sql(filters,parameters,item_id)
            return {'plan_count':c.execute('SELECT COUNT(*) FROM budget_plans WHERE budget_system_id=?',(item_id,)).fetchone()[0],
                    'transaction_count':c.execute('SELECT COUNT(*) FROM transactions WHERE '+ ' AND '.join(filters),parameters).fetchone()[0]}
    if collection not in ('storage','purposes'):
        raise HTTPException(422, '仅支持预算系统及栏目影响预览。')
    with connect() as c:
        config=config_data(c);ids={item_id}
        while True:
            expanded=ids|{r['id'] for r in config[collection] if r['parent_id'] in ids}
            if expanded==ids:break
            ids=expanded
        field='storage_method_id' if collection=='storage' else 'purpose_id'
        rows=c.execute(f'SELECT DISTINCT p.id,p.month,i.id AS item_id FROM budget_plan_items i JOIN budget_plans p ON p.id=i.plan_id WHERE i.{field} IN ({",".join("?" for _ in ids)})',tuple(ids)).fetchall()
        return {'item_count':len(rows),'months':sorted({r['month'] for r in rows})}


@router.get('/budgets', include_in_schema=False)
def budgets_page():
    return FileResponse(ROOT_DIR/'static/budgets.html', headers={'Cache-Control': 'no-store'})


@router.get('/settings', include_in_schema=False)
def settings_page():
    return FileResponse(ROOT_DIR/'static/settings.html', headers={'Cache-Control': 'no-store'})


@router.get('/api/budget/config')
def get_config():
    with connect() as c:
        c.execute('BEGIN')
        return config_data(c)


@router.put('/api/budget/defaults')
def save_defaults(body: DefaultsWrite):
    with mutation(body.expected_version) as c:
        if 'daily_expense' in body.model_fields_set:
            if body.daily_expense is None:
                raise HTTPException(422, '请选择承接未标注支出的日常预算。')
            require_system(c, body.daily_expense)
            c.execute('UPDATE app_settings SET value=?,updated_at=CURRENT_TIMESTAMP WHERE key=?', (str(body.daily_expense),DAILY_EXPENSE_KEY))
        for mode, key in DEFAULT_KEYS.items():
            value = getattr(body, mode)
            require_system(c, value)
            c.execute('INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP', (key,json.dumps(value)))
    return get_config()


def save_config_item(collection, item_id, body):
    table = COLLECTIONS.get(collection)
    if table is None:
        raise HTTPException(404, '设置类型不存在。')
    with mutation(body.expected_version) as c:
        old = c.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone() if item_id else None
        if item_id and not old:
            raise HTTPException(404, '设置项目不存在。')
        if collection in ('systems', 'storage', 'purposes', 'presets') and not body.name.strip():
            raise HTTPException(422, '请填写名称。')
        if collection == 'systems':
            values = dict(name=body.name.strip(),sort_order=body.sort_order,is_active=int(body.is_active),note=body.note)
            if 'currency_code' in body.model_fields_set:
                if body.currency_code is not None:
                    currency(c, body.currency_code)
                if old and old['currency_code'] != body.currency_code and c.execute('SELECT 1 FROM budget_plans WHERE budget_system_id=?', (item_id,)).fetchone():
                    raise HTTPException(409, '已有月度计划，请使用“修改预算币种”预览并统一更正。')
                values['currency_code'] = body.currency_code
            if old and not body.is_active:
                config = config_data(c)
                if item_id == config['daily_expense'] or item_id in config['defaults'].values() or any(r['budget_system_id']==item_id and r['is_active'] for key in ('rules','presets') for r in config[key]):
                    raise HTTPException(409, '请先更换日常归属及默认预算，并停用或改选引用此预算的映射和预设。')
        else:
            values = dict(is_active=int(body.is_active))
            if collection != 'storage':
                if body.budget_system_id is None:
                    raise HTTPException(422, '请选择预算系统。')
                require_system(c, body.budget_system_id, allow_inactive=bool(old and not body.is_active))
                values['budget_system_id'] = body.budget_system_id
            if collection in ('storage', 'purposes'):
                if collection != 'storage' and old and old['budget_system_id'] != body.budget_system_id:
                    raise HTTPException(422, '栏目不能移到另一预算；请在目标预算新增。')
                if old and old['is_placeholder']:
                    raise HTTPException(422, '待定栏目由系统保留。')
                ancestors, parent = {item_id} if item_id else set(), body.parent_id
                while parent:
                    if parent in ancestors:
                        raise HTTPException(422, '不能形成循环层级。')
                    ancestors.add(parent)
                    row = c.execute(f'SELECT * FROM {table} WHERE id=?', (parent,)).fetchone()
                    if not row or (collection != 'storage' and row['budget_system_id'] != body.budget_system_id) or row['is_placeholder'] or not row['is_active']:
                        raise HTTPException(422, '请选择有效、启用的父栏目。')
                    parent = row['parent_id']
                values.update(name=body.name.strip(),parent_id=body.parent_id,sort_order=body.sort_order,note=body.note)
            else:
                if collection == 'rules' and body.category_id is None:
                    raise HTTPException(422, '映射必须选择分类。')
                if body.category_id:
                    category = c.execute('SELECT * FROM categories WHERE id=? AND kind=?', (body.category_id,body.mode)).fetchone()
                    if not category or (body.is_active and (not category['is_active'] or c.execute('SELECT 1 FROM categories WHERE parent_id=?',(body.category_id,)).fetchone())):
                        raise HTTPException(422, '请选择该模式下启用的末级分类。')
                values.update(mode=body.mode, category_id=body.category_id)
                if collection == 'presets':
                    if body.account_id and not c.execute('SELECT 1 FROM accounts WHERE id=? AND is_active=1', (body.account_id,)).fetchone():
                        raise HTTPException(422, '预设账户不存在或已停用。')
                    values.update(name=body.name.strip(),account_id=body.account_id)
        if item_id:
            c.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in values)} WHERE id=?", (*values.values(),item_id))
        else:
            item_id = c.execute(f"INSERT INTO {table}({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values())).lastrowid
            if collection == 'systems':
                for dimension, label in [('budget_purposes','待定用途')]:
                    c.execute(f'INSERT INTO {dimension}(budget_system_id,name,is_placeholder) VALUES (?,?,1)', (item_id,label))
    return get_config()


@router.post('/api/budget/config/{collection}', status_code=201)
def create_config_item(collection: str, body: ConfigWrite):
    return save_config_item(collection, None, body)


@router.put('/api/budget/config/{collection}/{item_id}')
def update_config_item(collection: str, item_id: int, body: ConfigWrite):
    return save_config_item(collection, item_id, body)


@router.delete('/api/budget/config/{collection}/{item_id}')
def delete_config_item(collection: str, item_id: int, expected_version: int):
    table = COLLECTIONS.get(collection)
    if not table:
        raise HTTPException(404, '设置类型不存在。')
    with mutation(expected_version) as c:
        row = c.execute(f'SELECT * FROM {table} WHERE id=?',(item_id,)).fetchone()
        if not row:
            raise HTTPException(404, '项目不存在。')
        if collection == 'systems':
            config = config_data(c)
            if item_id == config['daily_expense'] or item_id in config['defaults'].values():
                raise HTTPException(409, '请先更换日常归属及默认预算。')
        if collection == 'systems':
            # An unused system still owns its initial placeholder rows.
            # Remove leaves first; any plan/history/reference causes a rollback.
            for dimension in ('budget_purposes',):
                rows=[dict(r) for r in c.execute(f'SELECT id,parent_id FROM {dimension} WHERE budget_system_id=?',(item_id,))]
                while rows:
                    parents={r['parent_id'] for r in rows}
                    leaves=[r for r in rows if r['id'] not in parents]
                    if not leaves:raise HTTPException(409,'栏目层级异常，无法删除。')
                    for row_to_delete in leaves:
                        c.execute(f'DELETE FROM {dimension} WHERE id=?',(row_to_delete['id'],));rows.remove(row_to_delete)
        if collection in ('storage','purposes'):
            if row['is_placeholder']:
                raise HTTPException(409, '待定栏目不能删除。')
            field = 'storage_method_id' if collection=='storage' else 'purpose_id'
            historic_ids = {item_id}
            if collection == 'storage':
                historic_ids.update(r[0] for r in c.execute('SELECT old_id FROM budget_storage_aliases WHERE storage_id=?', (item_id,)))
            for revision in c.execute('SELECT snapshot FROM budget_plan_revisions'):
                if any(i[field] in historic_ids for i in json.loads(revision['snapshot'])['items']):
                    raise HTTPException(409, '历史计划曾使用此栏目，请停用以保留历史。')
        c.execute(f'DELETE FROM {table} WHERE id=?',(item_id,))
    return get_config()


def valid_month(value):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value) or value[:4]=='0000':
        raise HTTPException(422, '月份须为 YYYY-MM。')
    return value


def currency(c, code):
    row = c.execute('SELECT * FROM currencies WHERE code=?',(code,)).fetchone()
    if not row:
        raise HTTPException(422, '币种不存在。')
    return row['decimal_places']


def minor(value, places):
    amount = Decimal(value)
    scaled = amount * 10**places
    if not scaled.is_finite() or scaled < 0 or scaled != scaled.to_integral_value() or scaled > 10**15:
        raise HTTPException(422, '预算金额须非负，并符合币种的小数位数。')
    return int(scaled)


def money(value, places):
    return f'{Decimal(value) / 10**places:.{places}f}'


class PlanItem(BaseModel):
    id: int | None = Field(default=None, gt=0)
    storage_method_id: int = Field(gt=0)
    purpose_id: int | None = Field(default=None, gt=0)
    title: str = Field(default='', max_length=120)
    amount: Decimal = Field(ge=0)
    note: str = Field(default='', max_length=500)


class PlanWrite(BaseModel):
    expected_version: int = Field(ge=0)
    total: Decimal | None = Field(default=None, ge=0)
    note: str = Field(default='', max_length=500)
    items: list[PlanItem] = Field(default_factory=list, max_length=2000)
    allocation_mode: bool = False


def plan_data(c, system_id, month, code):
    system = require_system(c, system_id, True)
    places = currency(c, code)
    plan = c.execute('SELECT * FROM budget_plans WHERE budget_system_id=? AND month=? AND currency_code=?', (system_id,valid_month(month),code)).fetchone()
    data = dict(plan) if plan else dict(id=None,budget_system_id=system_id,month=month,currency_code=code,version=0,total_minor=None,note='')
    config = config_data(c)
    storage = {r['id']:r for r in config['storage']}; purposes = {r['id']:r for r in config['purposes']}
    items = [dict(r) for r in c.execute('SELECT * FROM budget_plan_items WHERE plan_id=? ORDER BY sort_order,id', (data['id'],))]
    for item in items:
        item.update(amount=money(item['amount_minor'],places),storage_path=storage[item['storage_method_id']]['path'],purpose_path=purposes[item['purpose_id']]['path'])
    data.update(system_name=system['name'],items=items, total=money(data['total_minor'],places) if data['total_minor'] is not None else None,
                allocated=money(sum(i['amount_minor'] for i in items),places),decimal_places=places,
                dimensions={key:[r for r in config[key] if key=='storage' or r['budget_system_id']==system_id] for key in ('storage','purposes')})
    return data


@router.get('/api/budget/plans/{system_id}/{month}/{code}')
def get_plan(system_id: int, month: str, code: str):
    with connect() as c:
        c.execute('BEGIN')
        return plan_data(c,system_id,month,code)


def save_plan_in_connection(c, system_id: int, month: str, code: str, body: PlanWrite):
    before = plan_data(c,system_id,month,code)
    system = require_system(c,system_id)
    codes = {r[0] for r in c.execute('SELECT DISTINCT currency_code FROM budget_plans WHERE budget_system_id=?', (system_id,))}
    if len(codes)>1 or (codes and system['currency_code'] and codes != {system['currency_code']}):
        raise HTTPException(409, '此预算存在旧版多币种计划，须先整理，原记录已完整保留。')
    chosen = system['currency_code'] or next(iter(codes), None)
    if chosen and chosen != code:
        raise HTTPException(409, f'此预算统一使用 {chosen}，请重新加载；需要更正时使用“修改预算币种”。')
    if not system['currency_code']:
        c.execute('UPDATE budget_systems SET currency_code=? WHERE id=?', (code,system_id))
        c.execute("UPDATE app_settings SET value=CAST(value AS INTEGER)+1 WHERE key='budget_config_version'")
    if before['version'] != body.expected_version:
        raise HTTPException(409, '本月计划已更新，请重新加载后再编辑。')
    existing = {i['id']:i for i in before['items']}; seen = set()
    config = config_data(c)
    for item in body.items:
        alias = c.execute('SELECT storage_id FROM budget_storage_aliases WHERE old_id=?', (item.storage_method_id,)).fetchone()
        if alias:
            item.storage_method_id = alias['storage_id']
        if item.id and (item.id not in existing or item.id in seen):
            raise HTTPException(422, '预算明细编号重复或不属于本月。')
        if item.id: seen.add(item.id)
        if item.purpose_id is None:
            # New plans use the budget system as purpose. Preserve legacy labels on edits.
            item.purpose_id = existing[item.id]['purpose_id'] if item.id in existing else next(
                r['id'] for r in config['purposes'] if r['budget_system_id']==system_id and r['is_placeholder'])
        for key, field in [('storage','storage_method_id'),('purposes','purpose_id')]:
            value = getattr(item,field)
            row = next((r for r in config[key] if r['id']==value and (key=='storage' or r['budget_system_id']==system_id)),None)
            unchanged = item.id in existing and existing[item.id][field]==value
            if row is None or (not unchanged and not row['selectable']):
                raise HTTPException(422, '新明细须选择本预算中启用的末级栏目。')
    total = minor(body.total,before['decimal_places']) if body.total is not None else None
    allocated = sum(minor(i.amount,before['decimal_places']) for i in body.items)
    if body.allocation_mode and (total is None or allocated > total):
        raise HTTPException(422, '请填写总预算，存储分配合计不能超过总预算。')
    if allocated>10**15:
        raise HTTPException(422, '本月预算明细合计超出支持范围。')
    if before['id']:
        plan_id = before['id']
        c.execute('UPDATE budget_plans SET total_minor=?,note=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?', (total,body.note,plan_id))
    else:
        plan_id = c.execute('INSERT INTO budget_plans(budget_system_id,month,currency_code,total_minor,note) VALUES (?,?,?,?,?)', (system_id,month,code,total,body.note)).lastrowid
    for old_id in existing.keys()-seen:
        c.execute('DELETE FROM budget_plan_items WHERE id=?',(old_id,))
    for order,item in enumerate(body.items):
        values = (item.storage_method_id,item.purpose_id,item.title,minor(item.amount,before['decimal_places']),order,item.note)
        if item.id:
            c.execute('UPDATE budget_plan_items SET storage_method_id=?,purpose_id=?,title=?,amount_minor=?,sort_order=?,note=? WHERE id=?', (*values,item.id))
        else:
            c.execute('INSERT INTO budget_plan_items(storage_method_id,purpose_id,title,amount_minor,sort_order,note,plan_id) VALUES (?,?,?,?,?,?,?)', (*values,plan_id))
    result = plan_data(c,system_id,month,code)
    c.execute('INSERT INTO budget_plan_revisions(plan_id,version,snapshot) VALUES (?,?,?)',(plan_id,result['version'],json.dumps(result,ensure_ascii=False)))
    return result


@router.put('/api/budget/plans/{system_id}/{month}/{code}')
def save_plan(system_id: int, month: str, code: str, body: PlanWrite):
    valid_month(month)
    with mutation() as c:
        return save_plan_in_connection(c,system_id,month,code,body)


class CurrencyPlanWrite(PlanWrite):
    currency_code: str = Field(min_length=1, max_length=12)
    total: Decimal = Field(ge=0)


class MonthlyPlanWrite(BaseModel):
    plans: list[CurrencyPlanWrite] = Field(min_length=1, max_length=1)


def monthly_plan_data(c, system_id, month):
    valid_month(month)
    system = require_system(c,system_id,True)
    codes = [r[0] for r in c.execute(
        'SELECT currency_code FROM budget_plans WHERE budget_system_id=? AND month=? ORDER BY currency_code',
        (system_id,month))]
    return dict(budget_system_id=system_id,system_name=system['name'],month=month,currency_code=system['currency_code'],
                plans=[plan_data(c,system_id,month,code) for code in codes])


@router.get('/api/budget/monthly/{system_id}/{month}')
def get_monthly_plan(system_id: int, month: str):
    with connect() as c:
        c.execute('BEGIN')
        return monthly_plan_data(c,system_id,month)


@router.put('/api/budget/monthly/{system_id}/{month}')
def save_monthly_plan(system_id: int, month: str, body: MonthlyPlanWrite):
    valid_month(month)
    with mutation() as c:
        for plan in body.plans:
            try:
                save_plan_in_connection(c,system_id,month,plan.currency_code,
                                        plan.model_copy(update={'allocation_mode':True}))
            except HTTPException as exc:
                raise HTTPException(exc.status_code, f'{plan.currency_code}：{exc.detail}') from exc
        return monthly_plan_data(c,system_id,month)


class PlanVersion(BaseModel):
    id: int = Field(gt=0)
    version: int = Field(ge=1)


class SystemCurrencyWrite(BaseModel):
    expected_version: int = Field(ge=1)
    currency_code: str = Field(min_length=1, max_length=12)
    plans: list[PlanVersion]


def system_currency_data(c, system_id):
    system = require_system(c,system_id,True)
    plans = [plan_data(c,system_id,r['month'],r['currency_code']) for r in c.execute(
        'SELECT month,currency_code FROM budget_plans WHERE budget_system_id=? ORDER BY month,currency_code', (system_id,))]
    return dict(system=system,expected_version=config_data(c)['version'],plans=plans)


@router.get('/api/budget/systems/{system_id}/currency')
def get_system_currency(system_id: int):
    with connect() as c:
        c.execute('BEGIN')
        return system_currency_data(c,system_id)


@router.put('/api/budget/systems/{system_id}/currency')
def change_system_currency(system_id: int, body: SystemCurrencyWrite):
    """Correct the denomination of every monthly plan, retaining visible numbers and history."""
    with mutation(body.expected_version) as c:
        require_system(c,system_id)
        places = currency(c,body.currency_code)
        before = system_currency_data(c,system_id)
        actual = {(p['id'],p['version']) for p in before['plans']}
        expected = {(p.id,p.version) for p in body.plans}
        if actual != expected or len(expected) != len(body.plans):
            raise HTTPException(409, '月度计划已更新，请重新打开币种预览后再更正。')
        if len({p['currency_code'] for p in before['plans']})>1:
            raise HTTPException(409, '此预算存在旧版多币种计划，不能直接合并更正，原记录已完整保留。')
        # Validate every amount before applying the correction; never round silently.
        amounts = []
        for p in before['plans']:
            try:
                total = minor(p['total'],places) if p['total'] is not None else None
                items = [(i['id'],minor(i['amount'],places)) for i in p['items']]
                if sum(amount for _,amount in items)>10**15:
                    raise HTTPException(422, '本月分配合计超出支持范围。')
                amounts.append((total,items))
            except HTTPException as exc:
                raise HTTPException(422, f"{p['month']} 的金额不符合 {body.currency_code} 精度或范围，请先修改该月金额。") from exc
        c.execute('UPDATE budget_systems SET currency_code=? WHERE id=?', (body.currency_code,system_id))
        for p,(total,items) in zip(before['plans'],amounts):
            c.execute('INSERT OR IGNORE INTO budget_plan_revisions(plan_id,version,snapshot) VALUES (?,?,?)',
                      (p['id'],p['version'],json.dumps(p,ensure_ascii=False)))
            c.execute('UPDATE budget_plans SET currency_code=?,total_minor=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                      (body.currency_code,total,p['id']))
            c.executemany('UPDATE budget_plan_items SET amount_minor=? WHERE id=?', [(amount,item_id) for item_id,amount in items])
            after = plan_data(c,system_id,p['month'],body.currency_code)
            c.execute('INSERT INTO budget_plan_revisions(plan_id,version,snapshot) VALUES (?,?,?)',
                      (p['id'],after['version'],json.dumps(after,ensure_ascii=False)))
    return get_config()


@router.get('/api/budget/overview')
def budget_overview(end_month: str, start_month: str = '0001-01', system_id: int | None = None, currency_code: str | None = None):
    valid_month(start_month); valid_month(end_month)
    if start_month > end_month:
        raise HTTPException(422, '累计起始月不能晚于编制月份。')
    with connect() as c:
        c.execute('BEGIN')
        config = config_data(c)
        if system_id is not None:
            if system_id <= 0:
                raise HTTPException(422, '请选择有效的预算用途。')
            require_system(c,system_id,True)
        if currency_code is not None:
            currency(c,currency_code)
        filters=['p.month BETWEEN ? AND ?']; args=[start_month,end_month]
        if system_id is not None:
            filters.append('p.budget_system_id=?'); args.append(system_id)
        if currency_code is not None:
            filters.append('p.currency_code=?'); args.append(currency_code)
        where=' AND '.join(filters)
        plans=[dict(r) for r in c.execute(f'SELECT p.* FROM budget_plans p WHERE {where} ORDER BY p.month,p.budget_system_id,p.currency_code',args)]
        items=[dict(r) for r in c.execute(f'''SELECT i.*,p.month,p.budget_system_id,p.currency_code FROM budget_plan_items i
            JOIN budget_plans p ON p.id=i.plan_id WHERE {where} ORDER BY p.month,i.sort_order,i.id''',args)]
        systems={s['id']:s for s in config['systems']}
        storage={s['id']:s for s in config['storage']}
        purposes={s['id']:s for s in config['purposes']}
        currencies={r['code']:r for r in config['currencies']}
        by_plan={p['id']:[] for p in plans}
        for item in items:
            by_plan[item['plan_id']].append(item)
            storage_parent=storage[item['storage_method_id']]
            while storage_parent['parent_id'] is not None:
                storage_parent=storage[storage_parent['parent_id']]
            item.update(amount=money(item['amount_minor'],currencies[item['currency_code']]['decimal_places']),
                        system_name=systems[item['budget_system_id']]['name'],storage_path=storage[item['storage_method_id']]['path'],
                        storage_root=storage_parent['path'],
                        legacy_purpose=purposes[item['purpose_id']]['path'] if not purposes[item['purpose_id']]['is_placeholder'] else '')
        for p in plans:
            places=currencies[p['currency_code']]['decimal_places']
            allocated=sum(i['amount_minor'] for i in by_plan[p['id']])
            total=p['total_minor'] if p['total_minor'] is not None else allocated
            p.update(system_name=systems[p['budget_system_id']]['name'],is_active=systems[p['budget_system_id']]['is_active'],
                     budget_total=money(total,places),allocated=money(allocated,places),unallocated=money(total-allocated,places),
                     budget_total_minor=total,allocated_minor=allocated,unallocated_minor=total-allocated,
                     total_inferred=p['total_minor'] is None,items=by_plan[p['id']])
        blocks=[]
        for code in sorted({p['currency_code'] for p in plans}):
            places=currencies[code]['decimal_places']; selected=[p for p in plans if p['currency_code']==code]
            def sums(rows):
                return {key:money(sum(p[key+'_minor'] for p in rows),places) for key in ('budget_total','allocated','unallocated')}
            blocks.append(dict(currency_code=code,decimal_places=places,monthly=sums([p for p in selected if p['month']==end_month]),cumulative=sums(selected)))
        return dict(start_month=start_month,end_month=end_month,plans=plans,currencies=blocks,items=items)


@router.get('/api/budget/overview.xlsx')
def budget_overview_export(end_month: str, start_month: str = '0001-01', system_id: int | None = None, currency_code: str | None = None):
    data=budget_overview(end_month,start_month,system_id,currency_code)
    def safe(value):
        return "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value
    book=Workbook(); sheet=book.active; sheet.title='预算总额'
    sheet.append(['月份','用途（预算系统）','币种','总预算','已分配存储','待分配','说明'])
    for p in data['plans']:
        sheet.append([p['month'],safe(p['system_name']),p['currency_code'],Decimal(p['budget_total']),Decimal(p['allocated']),Decimal(p['unallocated']),safe(p['note'])])
    sheet.freeze_panes='A2'; sheet=book.create_sheet('二级存储分配')
    sheet.append(['月份','用途（预算系统）','币种','存储方式','金额','明细名称','备注','历史用途标签'])
    for i in data['items']:
        sheet.append([i['month'],safe(i['system_name']),i['currency_code'],safe(i['storage_path']),Decimal(i['amount']),safe(i['title']),safe(i['note']),safe(i['legacy_purpose'])])
    sheet.freeze_panes='A2'; sheet=book.create_sheet('分币种汇总')
    sheet.append(['币种','区间','总预算','已分配存储','待分配'])
    for block in data['currencies']:
        for scope,label in [('monthly',end_month),('cumulative',f'{start_month} 至 {end_month}')]:
            sheet.append([block['currency_code'],label]+[Decimal(block[scope][key]) for key in ('budget_total','allocated','unallocated')])
    out=BytesIO(); book.save(out); out.seek(0)
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="budget-plan.xlsx"'})


@router.get('/api/budget/plans/{system_id}/{month}/{code}/revisions')
def revisions(system_id: int, month: str, code: str):
    with connect() as c:
        c.execute('BEGIN')
        plan = plan_data(c,system_id,month,code)
        return [dict(version=r['version'],created_at=r['created_at'],plan=json.loads(r['snapshot'])) for r in c.execute('SELECT * FROM budget_plan_revisions WHERE plan_id=? ORDER BY version DESC',(plan['id'],))]


@router.get('/api/budget/summary')
def budget_summary(system_id: int, end_month: str, currency_code: str, start_month: str = '0001-01'):
    valid_month(start_month); valid_month(end_month)
    if start_month>end_month:
        raise HTTPException(422, '开始月份不能晚于结束月份。')
    with connect() as c:
        c.execute('BEGIN')
        current = plan_data(c,system_id,end_month,currency_code)
        places = current['decimal_places']
        rows = [dict(r) for r in c.execute('''SELECT i.*,p.month FROM budget_plan_items i JOIN budget_plans p ON p.id=i.plan_id
            WHERE p.budget_system_id=? AND p.currency_code=? AND p.month BETWEEN ? AND ? ORDER BY p.month,i.sort_order,i.id''', (system_id,currency_code,start_month,end_month))]
        months = [r[0] for r in c.execute('SELECT month FROM budget_plans WHERE budget_system_id=? AND currency_code=? AND month BETWEEN ? AND ? ORDER BY month',(system_id,currency_code,start_month,end_month))]
        dims = current['dimensions']
        lookup = {key:{r['id']:r for r in dims[key]} for key in dims}
        for row in rows:
            row.update(amount=money(row['amount_minor'],places),storage_path=lookup['storage'][row['storage_method_id']]['path'],purpose_path=lookup['purposes'][row['purpose_id']]['path'])
        def cells(items):
            buckets = {}
            for i in items:
                key=(i['purpose_id'],i['storage_method_id'])
                buckets[key]=buckets.get(key,0)+i['amount_minor']
            return [dict(purpose_id=p,storage_method_id=s,amount=money(n,places),amount_minor=n) for (p,s),n in buckets.items()]
        monthly = [r for r in rows if r['month']==end_month]
        return dict(system_id=system_id,system_name=current['system_name'],currency_code=currency_code,decimal_places=places,
                    start_month=start_month,end_month=end_month,compiled_months=months,
                    previous=money(sum(r['amount_minor'] for r in rows if r['month']<end_month),places),
                    current=money(sum(r['amount_minor'] for r in monthly),places),
                    cumulative=money(sum(r['amount_minor'] for r in rows),places),
                    cells=cells(rows),monthly_cells=cells(monthly),items=rows,dimensions=dims)


@router.get('/api/budget/export.xlsx')
def budget_export(system_id: int, end_month: str, currency_code: str, start_month: str='0001-01'):
    data = budget_summary(system_id,end_month,currency_code,start_month)
    book=Workbook(); sheet=book.active; sheet.title='预算计划'
    def safe(value):
        return "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value
    sheet.append([safe(data['system_name']),start_month,end_month,currency_code,'手工计划金额'])
    sheet.append(['月份','用途','存储方式','明细','金额','备注'])
    for i in data['items']:
        sheet.append([i['month'],safe(i['purpose_path']),safe(i['storage_path']),safe(i['title']),Decimal(i['amount']),safe(i['note'])])
    sheet.freeze_panes='A3'
    sheet=book.create_sheet('累计矩阵')
    storage=data['dimensions']['storage']; purposes=data['dimensions']['purposes']
    sheet.append(['用途 / 存储方式']+[safe(s['path']) for s in storage]+['合计'])
    values={(i['purpose_id'],i['storage_method_id']):i['amount_minor'] for i in data['cells']}
    for p in purposes:
        amounts=[values.get((p['id'],s['id']),0) for s in storage]
        sheet.append([safe(p['path'])]+[Decimal(money(v,data['decimal_places'])) for v in amounts]+[Decimal(money(sum(amounts),data['decimal_places']))])
    sheet.append(['合计']+[Decimal(money(sum(values.get((p['id'],s['id']),0) for p in purposes),data['decimal_places'])) for s in storage]+[Decimal(data['cumulative'])])
    out=BytesIO(); book.save(out); out.seek(0)
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="budget-plan.xlsx"'})

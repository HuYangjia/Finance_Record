"""Allocate real net income to envelopes; monthly totals are a transactional cache."""
import hashlib
import json
from decimal import Decimal, ROUND_FLOOR
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import connect
from app.budgets import config_data, currency, minor, money, mutation, plan_data, require_system, valid_month
from app.budget_funding import FundingCatalog, reference

router = APIRouter()


class AllocationItem(BaseModel):
    budget_system_id: int = Field(gt=0)
    month: str | None = None
    amount: Decimal | None = Field(default=None, gt=0)


class LegacyDecision(BaseModel):
    plan_id: int = Field(gt=0)
    action: Literal['replace', 'independent']


class AllocationWrite(BaseModel):
    expected_token: str = Field(min_length=64, max_length=64)
    items: list[AllocationItem] = Field(default_factory=list, max_length=300)
    replace_legacy_plan_ids: list[int] = Field(default_factory=list, max_length=300)
    legacy_decisions: list[LegacyDecision] = Field(default_factory=list, max_length=300)


def allocation_snapshot(c):
    return {name: [dict(r) for r in c.execute(f'SELECT * FROM {name} ORDER BY 1')]
            for name in ('budget_income_allocations', 'budget_plan_funding', 'budget_plans', 'budget_legacy_reviews')}


def legacy_plans(c):
    """No inference from old income tags: these amounts require explicit review."""
    return [dict(r) for r in c.execute('''SELECT p.*,b.name AS system_name,
        COALESCE(p.total_minor,(SELECT SUM(amount_minor) FROM budget_plan_items WHERE plan_id=p.id),0) AS budget_total,
        COALESCE(r.plan_version=p.version,0) AS legacy_reviewed
        FROM budget_plans p JOIN budget_systems b ON b.id=p.budget_system_id
        LEFT JOIN budget_legacy_reviews r ON r.plan_id=p.id
        WHERE p.funding_mode='legacy'
        AND NOT EXISTS (SELECT 1 FROM budget_plan_funding f WHERE f.plan_id=p.id)
        AND NOT EXISTS (SELECT 1 FROM budget_income_allocations a WHERE a.plan_id=p.id)
        ORDER BY p.month,p.id''')]


def allocation_token(c, catalog, source):
    payload = [source['token'], allocation_snapshot(c), config_data(c)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def source_detail(c, kind, entry_id):
    catalog = FundingCatalog(c)
    source = catalog.describe(kind, entry_id)
    rows = []
    for row in catalog.bindings:
        if reference(row) != (kind, entry_id):
            continue
        rows.append(dict(budget_system_id=row['budget_system_id'], month=row['month'],
                         amount=money(row['total_minor'], catalog.places[row['plan_currency']]) if row['total_minor'] is not None else None,
                         currency_code=row['plan_currency'], plan_id=row['plan_id'],
                         legacy='allocation_id' not in row))
    config = config_data(c)
    plans = [dict(r) for r in c.execute('SELECT * FROM budget_plans ORDER BY month,id')]
    for p in plans:
        total = p['total_minor']
        if total is None:
            total = c.execute('SELECT COALESCE(SUM(amount_minor),0) FROM budget_plan_items WHERE plan_id=?', (p['id'],)).fetchone()[0]
        p['total'] = money(total, currency(c,p['currency_code']))
    historical = legacy_plans(c)
    for p in historical:
        p['total'] = money(p['budget_total'],currency(c,p['currency_code']))
    return dict(source=source, items=rows, config=config, plans=plans,
                legacy_plans=historical,token=allocation_token(c,catalog,source))


def record_plan_revision(c, plan_id):
    p = c.execute('SELECT * FROM budget_plans WHERE id=?', (plan_id,)).fetchone()
    snapshot = plan_data(c,p['budget_system_id'],p['month'],p['currency_code'])
    c.execute('INSERT OR IGNORE INTO budget_plan_revisions(plan_id,version,snapshot) VALUES (?,?,?)',
              (plan_id,p['version'],json.dumps(snapshot,ensure_ascii=False)))


def resolve_remainder(c, catalog, source, items):
    """Resolve the one automatic row against the entire replacement draft."""
    automatic = [i for i,item in enumerate(items) if item.amount is None]
    if not automatic:
        return items
    if len(automatic) > 1:
        raise HTTPException(422,'只能选择一行“全部剩余”。')
    if not source['complete'] or source['net'] is None:
        raise HTTPException(422,source['reason'] or '来源净收入无法计算。')
    remaining = minor(Decimal(source['net']),source['decimal_places'])
    codes = []
    for item in items:
        system = require_system(c,item.budget_system_id,True)
        code = system['currency_code'] or source['currency_code']
        codes.append(code)
        if item.amount is not None:
            minor(item.amount,currency(c,code))
            cost,_ = catalog.charge(item.amount,code,source)
            if cost is None:
                raise HTTPException(422,'缺少汇率，无法计算全部剩余。')
            remaining -= cost
    if remaining <= 0:
        raise HTTPException(422,'其他分配已用完或超出收入，没有可分配的剩余；请减少金额或移除“全部剩余”行。')
    index = automatic[0]
    code = codes[index]
    places = currency(c,code)
    # Invert the same conversion used for charging to avoid mismatched rounding.
    rate,_ = catalog.amount(Decimal(1),code,source['date'],source['currency_code'])
    if rate is None or rate <= 0:
        raise HTTPException(422,'缺少汇率，无法计算全部剩余。')
    value = Decimal(remaining) / 10**source['decimal_places'] / rate
    amount = minor(value.quantize(Decimal(1).scaleb(-places),rounding=ROUND_FLOOR),places)
    def cost(units):
        return catalog.charge(Decimal(units) / 10**places,code,source)[0]
    # Decimal division at a currency boundary can differ by one unit. Verify
    # against the authoritative source charge and use the largest safe amount.
    while amount > 0 and cost(amount) > remaining:
        amount -= 1
    while amount < 10**15 and cost(amount+1) <= remaining:
        amount += 1
    if amount <= 0:
        raise HTTPException(422,'剩余不足此预算币种的最小金额，请换一个预算或保留余款。')
    resolved = list(items)
    resolved[index] = items[index].model_copy(update={'amount':Decimal(money(amount,places))})
    return resolved


def review_legacy(c, items, source_date, replace_legacy, decisions):
    targets = {(i.budget_system_id,valid_month(i.month or (source_date or '')[:7])) for i in items}
    systems = {sid for sid,_ in targets}
    candidates = {p['id']:p for p in legacy_plans(c) if p['budget_system_id'] in systems and
                  ((p['budget_system_id'],p['month']) in targets or
                   p['budget_total']>0 and not p['legacy_reviewed'])}
    actions = {pid:'replace' for pid in replace_legacy}
    for decision in decisions:
        if decision.plan_id in actions:
            raise HTTPException(422,'历史预案核对不能重复提交。')
        actions[decision.plan_id] = decision.action
    if set(actions)-set(candidates):
        raise HTTPException(409,'历史预案状态已改变，请重新打开分配窗口核对。')
    for pid,p in candidates.items():
        if pid not in actions:
            raise HTTPException(409,f"{p['system_name']} · {p['month']} 有历史手填额度，请在预算页“分配收入”中核对：替代重复额度，或确认是独立资金。新入账可先暂不分配。")
        if actions[pid]=='independent' and (p['budget_system_id'],p['month']) in targets:
            raise HTTPException(422,'同月旧总额必须被收入分配替代，不能另行叠加为独立资金。')
    replaced = set()
    for pid,action in actions.items():
        p = candidates[pid]
        if action=='replace':
            record_plan_revision(c,pid)
            # Version and derived total are updated together with affected income plans.
            c.execute("UPDATE budget_plans SET funding_mode='income',total_minor=0 WHERE id=?",(pid,))
            c.execute('DELETE FROM budget_legacy_reviews WHERE plan_id=?',(pid,))
            replaced.add(pid)
        else:
            c.execute('''INSERT INTO budget_legacy_reviews(plan_id,plan_version,decision)
                VALUES (?,?,'independent') ON CONFLICT(plan_id) DO UPDATE SET
                plan_version=excluded.plan_version,decision=excluded.decision,reviewed_at=CURRENT_TIMESTAMP''',
                (pid,p['version']))
    return replaced


def save_allocations(c, kind, entry_id, items, replace_legacy=(), expected_token=None, legacy_decisions=()):
    """Caller owns the write transaction, including a new income when supplied."""
    catalog = FundingCatalog(c)
    before = catalog.describe(kind, entry_id)
    if expected_token is not None and expected_token != allocation_token(c,catalog,before):
        raise HTTPException(409,'收入、分配或预算已更新，请重新打开分配窗口核对；当前草稿未保存。')
    items = resolve_remainder(c,catalog,before,items)
    replaced = review_legacy(c,items,before['date'],replace_legacy,legacy_decisions)
    previous = [r for r in catalog.bindings if reference(r)==(kind,entry_id)]
    affected = {r['plan_id'] for r in previous} | replaced
    for pid in affected:
        record_plan_revision(c,pid)
    column = 'transaction_id' if kind=='transaction' else 'group_id'
    c.execute(f'DELETE FROM budget_income_allocations WHERE {column}=?',(entry_id,))
    # Compatible old clients may still have created a single-source binding.
    c.execute(f'DELETE FROM budget_plan_funding WHERE {column}=?',(entry_id,))
    selected = set()
    for item in items:
        old_same = any(r['budget_system_id']==item.budget_system_id for r in previous)
        system = require_system(c,item.budget_system_id,allow_inactive=old_same)
        code = system['currency_code'] or before['currency_code']
        month = valid_month(item.month or (before['date'] or '')[:7])
        identity = (item.budget_system_id,month)
        if identity in selected:
            raise HTTPException(422,'同一收入分到同一预算同一月份，请合并成一行。')
        selected.add(identity)
        if system['execution_start_month'] and month < system['execution_start_month']:
            raise HTTPException(422,f"{system['name']} 从 {system['execution_start_month']} 起算，不能将新增分配放在此前月份。")
        existing_codes = {r[0] for r in c.execute('SELECT DISTINCT currency_code FROM budget_plans WHERE budget_system_id=?',(system['id'],))}
        if existing_codes and existing_codes != {code}:
            raise HTTPException(409,'此预算存在旧多币种计划，请先整理。')
        places = currency(c,code)
        amount = minor(item.amount,places)
        if amount <= 0:
            raise HTTPException(422,'分配金额须大于零。')
        plan = c.execute('SELECT * FROM budget_plans WHERE budget_system_id=? AND month=? AND currency_code=?',
                         (system['id'],month,code)).fetchone()
        if plan:
            pid = plan['id']
            if plan['funding_mode']=='legacy' and pid not in replace_legacy and not any(r['plan_id']==pid for r in previous):
                raise HTTPException(409,f"{system['name']} · {month} 已有历史手填预案，请在预算页“分配收入”中勾选替代旧总额；新入账可先选“暂不分配预算”保存，再补分配，避免重复计算。")
            if c.execute('SELECT 1 FROM budget_plan_funding WHERE plan_id=?',(pid,)).fetchone():
                raise HTTPException(409,'该旧预案仍绑定另一个收入，请先整理原来源分配。')
            if pid not in affected:
                record_plan_revision(c,pid)
        else:
            pid = c.execute("""INSERT INTO budget_plans(budget_system_id,month,currency_code,total_minor,funding_mode,version)
                               VALUES (?,?,?,0,'income',0)""",(system['id'],month,code)).lastrowid
        affected.add(pid)
        if not system['currency_code']:
            c.execute('UPDATE budget_systems SET currency_code=? WHERE id=?',(code,system['id']))
            c.execute("UPDATE app_settings SET value=CAST(value AS INTEGER)+1 WHERE key='budget_config_version'")
        c.execute(f'''INSERT INTO budget_income_allocations(plan_id,{column},currency_code,amount_minor)
                      VALUES (?,?,?,?)''',(pid,entry_id,before['currency_code'],amount))
    for pid in affected:
        total = c.execute('SELECT COALESCE(SUM(amount_minor),0) FROM budget_income_allocations WHERE plan_id=?',(pid,)).fetchone()[0]
        if total > 10**15:
            raise HTTPException(422,'预算合计超出支持范围。')
        c.execute("""UPDATE budget_plans SET funding_mode='income',total_minor=?,version=version+1,
                     updated_at=CURRENT_TIMESTAMP WHERE id=?""",(total,pid))
    if items:
        after = FundingCatalog(c).describe(kind,entry_id)
        reducing = all(i.amount is not None and i.amount <= sum(
            (Decimal(r['total_minor']) / 10**catalog.places[r['plan_currency']]
             for r in previous if r['budget_system_id']==i.budget_system_id
             and r['month']==(i.month or (before['date'] or '')[:7]) and r['total_minor'] is not None), Decimal(0)) for i in items)
        if (not after['complete'] or after['overallocated']) and not reducing:
            raise HTTPException(422,after['reason'] or
                f"来源净收入 {after['net']} {after['currency_code']}，分配占用 {after['occupied']}，超出额度；请减少分配。")
    for pid in affected:
        record_plan_revision(c,pid)
    return dict(saved=True,plan_ids=sorted(affected))


@router.get('/api/budget/income-sources')
def sources(keyword: str='', offset: int=0, limit: int=20):
    if offset<0 or not 1<=limit<=100 or len(keyword)>120:
        raise HTTPException(422,'查询条件不正确。')
    with connect() as c:
        c.execute('BEGIN')
        catalog = FundingCatalog(c)
        keys = set(catalog.entries) | {reference(r) for r in catalog.bindings}
        rows = [catalog.describe(*key) for key in keys]
        rows = [r for r in rows if keyword.strip().casefold() in
                (r['title']+' '+str(catalog.entries.get((r['entry_type'],r['entry_id']),{}).get('note') or '')).casefold()]
        rows.sort(key=lambda r:(r['date'] or '',r['entry_id']),reverse=True)
        return dict(sources=rows[offset:offset+limit],total=len(rows))


@router.get('/api/budget/income-allocations/{kind}/{entry_id}')
def get_allocations(kind: Literal['transaction','offset_group'], entry_id: int):
    with connect() as c:
        c.execute('BEGIN')
        return source_detail(c,kind,entry_id)


@router.put('/api/budget/income-allocations/{kind}/{entry_id}')
def update_allocations(kind: Literal['transaction','offset_group'], entry_id: int, body: AllocationWrite):
    with mutation(extra_snapshot=allocation_snapshot) as c:
        return save_allocations(c,kind,entry_id,body.items,body.replace_legacy_plan_ids,body.expected_token,body.legacy_decisions)


@router.post('/api/budget/income-allocations/{kind}/{entry_id}/preview')
def preview_allocations(kind: Literal['transaction','offset_group'], entry_id: int, body: AllocationWrite):
    with connect() as c:
        c.execute('BEGIN')
        catalog = FundingCatalog(c)
        source = catalog.describe(kind,entry_id)
        if body.expected_token != allocation_token(c,catalog,source):
            raise HTTPException(409,'来源或预算已变化，请重新打开分配窗口核对。')
        used = 0
        items = resolve_remainder(c,catalog,source,body.items)
        resolved = []
        for item in items:
            system = require_system(c,item.budget_system_id,True)
            code = system['currency_code'] or source['currency_code']
            places = currency(c,code)
            units = minor(item.amount,places)
            cost,_ = catalog.charge(item.amount,code,source)
            if cost is None:
                raise HTTPException(422,'缺少汇率，无法核验分配。')
            used += cost
            resolved.append(dict(amount=money(units,places),currency_code=code))
        remaining = Decimal(source['net']) - Decimal(money(used,source['decimal_places'])) if source['net'] is not None else None
        return dict(occupied=money(used,source['decimal_places']),
                    remaining=str(remaining) if remaining is not None else None,
                    currency_code=source['currency_code'],complete=source['complete'],
                    overallocated=remaining is not None and remaining<0,items=resolved)


ALLOCATION_NOTE = ('日期筛选按收入发生日／事项统计日；以下分配包含匹配来源的所有生效月份，'
                   '不等于该月新增预算。第六页按预算生效月计算。历史标签仅供原始流水筛选，不代表已分配收入。')


def allocation_records(c):
    rows = [dict(r) for r in c.execute('''SELECT a.*,p.budget_system_id,p.month,p.currency_code AS budget_currency,
                       b.name AS budget_name FROM budget_income_allocations a
                       JOIN budget_plans p ON p.id=a.plan_id JOIN budget_systems b ON b.id=p.budget_system_id
                       ORDER BY p.month,a.id''')]
    catalog = FundingCatalog(c)
    sources = {}
    for row in rows:
        key = reference(row)
        if key not in sources:
            sources[key] = catalog.describe(*key)
        source = sources[key]
        row.update(amount=money(row['amount_minor'],catalog.places[row['budget_currency']]),
                   source_date=source['date'],source_title=source['title'],source_url=source['url'],
                   source_complete=source['complete'] and not source['overallocated'],
                   source_status=source['reason'] or ('收入来源超额分配，请调整' if source['overallocated'] else '来源额度已核验'))
    return rows


def allocation_metadata(rows):
    rows = list({r['id']:r for r in rows}.values())
    warnings = {reference(r):dict(entry_type=reference(r)[0],entry_id=reference(r)[1],
                title=r['source_title'],url=r['source_url'],reason=r['source_status'])
                for r in rows if not r['source_complete']}
    return dict(allocation_details=rows,allocation_warnings=list(warnings.values()),
                allocation_complete=not warnings,allocation_note=ALLOCATION_NOTE)


def _budget_selection(value):
    if value is None:
        return None
    if isinstance(value, int):
        return {value}
    return set(value)


def allocations_for_transactions(c, transaction_ids, budget_system_ids=None):
    """Metadata only: a source group is returned once in the separate summary."""
    ids = set(transaction_ids)
    selected_systems = _budget_selection(budget_system_ids)
    memberships = {r['transaction_id']:r['group_id'] for r in c.execute('SELECT * FROM transaction_offset_members')}
    groups = {memberships[i] for i in ids if i in memberships}
    selected = [r for r in allocation_records(c) if (r['transaction_id'] in ids or r['group_id'] in groups)
                and (selected_systems is None or r['budget_system_id'] in selected_systems)]
    per_transaction = {i:[r for r in selected if r['transaction_id']==i or
                          r['group_id'] is not None and r['group_id']==memberships.get(i)] for i in ids}
    totals = {}
    for row in selected:
        code = row['budget_currency']
        totals[code] = totals.get(code,0)+(row['amount_minor'] if row['source_complete'] else 0)
    return per_transaction, [dict(currency_code=code,amount=money(value,currency(c,code))) for code,value in sorted(totals.items())]


def project_income_budget(c, entries, budget_system_ids):
    """Budget-filtered personal reports use allocated portions, retaining original members."""
    from app.personal_cashflow import value_entry
    selected_systems = _budget_selection(budget_system_ids)
    rates = [dict(r) for r in c.execute('SELECT * FROM daily_cny_exchange_rates ORDER BY rate_date')]
    allocations = allocation_records(c)
    result = []
    for entry in entries:
        matched = [r for r in allocations if reference(r)==(entry['entry_type'],entry['entry_id'])]
        if entry['kind']!='income':
            result.append(entry)
            continue
        if entry.get('budget_role') in ('expense', 'unresolved'):
            # Deficits use the event's expense responsibility, never a positive allocation.
            if (selected_systems is None or
                    set(entry.get('budget_system_ids', [])) & selected_systems):
                result.append(dict(entry, income_allocations=matched))
                continue
            if not matched:
                continue
        if not matched:
            # Legacy tags remain searchable in raw records, but never create budget funding.
            if selected_systems is None:
                result.append(dict(entry,budget_system_name='尚未分给预算')
                              if entry['entry_type']=='offset_group' else entry)
            elif 0 in selected_systems:
                result.append(dict(entry,budget_system_id=None,budget_system_ids=[0],
                                   budget_system_name=None,budget_mixed=False))
            continue
        if selected_systems is None:
            result.append(dict(entry,budget_system_name='、'.join(dict.fromkeys(r['budget_name'] for r in matched)),
                               income_allocations=matched))
            continue
        selected = [r for r in matched if r['budget_system_id'] in selected_systems]
        if not selected:
            continue
        synthetic = [dict(id=r['id'],amount=Decimal(money(r['amount_minor'],currency(c,r['budget_currency']))),
                          currency_code=r['budget_currency'],transaction_date=entry['statistics_date'],
                          transaction_type='income') for r in selected]
        projected_ids = sorted({r['budget_system_id'] for r in selected})
        projected = dict(
            entry,
            budget_system_id=projected_ids[0] if len(projected_ids) == 1 else None,
            budget_system_ids=projected_ids,
            budget_system_name='、'.join(dict.fromkeys(r['budget_name'] for r in selected)),
            budget_mixed=False,
            allocation_basis=True,
            income_allocations=selected,
        )
        from app.native_cashflow import project_income_share
        native = project_income_share(entry, selected, rates)
        projected.update({k: native[k] for k in ('_native_amounts', '_native_parts')})
        projected['_valuations'] = {code:value_entry(dict(kind='income',members=synthetic),rates,code)
                                    for code in ('CNY','HKD')}
        invalid = next((r for r in selected if not r['source_complete']),None)
        if invalid:
            for valuation in projected['_valuations'].values():
                valuation.update(amount=None,funding_issue=invalid['source_status'])
        result.append(projected)
    return result

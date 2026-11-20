"""Income references validate plan funding without changing ledger or plan amounts."""
import hashlib
import json
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from fastapi import HTTPException


def reference(row):
    return ('transaction', row['transaction_id']) if row['transaction_id'] else ('offset_group', row['group_id'])


def guard_reference(c, kind, entry_id):
    column = 'transaction_id' if kind == 'transaction' else 'group_id'
    rows = c.execute(f'''SELECT p.month,b.name FROM budget_plan_funding f
        JOIN budget_plans p ON p.id=f.plan_id JOIN budget_systems b ON b.id=p.budget_system_id
        WHERE f.{column}=?
        UNION SELECT p.month,b.name FROM budget_income_allocations f
        JOIN budget_plans p ON p.id=f.plan_id JOIN budget_systems b ON b.id=p.budget_system_id
        WHERE f.{column}=? ORDER BY month,name''', (entry_id,entry_id)).fetchall()
    if rows:
        names = '、'.join(f"{r['month']} {r['name']}" for r in rows[:5])
        raise HTTPException(409, f'该收入来源已分配预算（{names}），请先在预算页“分配收入”核对并移除分配，再调整事项关系或删除；随后可重新分配完整事项。')


class FundingCatalog:
    def __init__(self, c, *, calculation=None):
        from app.budget_calculation import get_calculation
        calculation = get_calculation(c, calculation)
        self.c = c
        self.rates = calculation.rates
        self.entries = {(e['entry_type'], e['entry_id']): e for e in calculation.entries
                        if e['kind'] == 'income'}
        self.places = {r['code']: r['decimal_places'] for r in c.execute('SELECT code,decimal_places FROM currencies')}
        self.bindings = [dict(r) for r in c.execute('''SELECT f.*,p.total_minor,p.currency_code AS plan_currency,
            p.month,p.version,p.budget_system_id,b.name AS system_name FROM budget_plan_funding f
            JOIN budget_plans p ON p.id=f.plan_id JOIN budget_systems b ON b.id=p.budget_system_id
            ORDER BY p.month,p.id''')]
        self.bindings.extend(dict(r) for r in c.execute('''SELECT a.plan_id,a.transaction_id,a.group_id,
            a.currency_code,a.amount_minor AS total_minor,p.currency_code AS plan_currency,
            p.month,p.version,p.budget_system_id,b.name AS system_name,a.id AS allocation_id
            FROM budget_income_allocations a JOIN budget_plans p ON p.id=a.plan_id
            JOIN budget_systems b ON b.id=p.budget_system_id ORDER BY p.month,p.id,a.id'''))

    def amount(self, value, code, day, target):
        from app.budget_execution import member_amount
        return member_amount(dict(amount=value,currency_code=code,transaction_date=day), self.rates, target)

    def charge(self, total, code, source):
        if code not in self.places:
            raise HTTPException(422, '预算币种不存在。')
        if total is None or not source['date']:
            return None, 'missing'
        amount, status = self.amount(Decimal(total), code, source['date'], source['currency_code'])
        if amount is None:
            return None, 'missing'
        return int((amount*10**self.places[source['currency_code']]).to_integral_value(rounding=ROUND_CEILING)), status

    def describe(self, kind, entry_id, exclude_plan_id=None):
        from app.budgets import money
        key = (kind, entry_id)
        entry = self.entries.get(key)
        bound = [r for r in self.bindings if reference(r) == key]
        if not entry and not bound:
            raise HTTPException(422, '请选择未加入事项的普通收入，或有效的收入冲销事项；AA 回款不能作为收入来源。')
        primary = [m for m in entry['members'] if m['transaction_type'] == 'income'] if entry else []
        code = bound[0]['currency_code'] if bound else primary[0]['currency_code']
        places = self.places[code]
        reason = '' if entry else '来源已变为其他类型或已加入事项，请解除绑定后重新选择'
        value = Decimal(0); nearest = 0
        if entry:
            for member in entry['members']:
                amount, rate_status = self.amount(member['amount'],member['currency_code'],member['transaction_date'],code)
                nearest += int(rate_status == 'nearest')
                if amount is None:
                    reason = '来源缺少汇率，无法完整计算净收入'
                else:
                    value += amount if member['transaction_type'] == 'income' else -amount
        net = None if reason else int((value*10**places).quantize(Decimal(1),rounding=ROUND_HALF_UP))
        result = dict(entry_type=kind,entry_id=entry_id,title=entry['title'] if entry else f'待处理来源 #{entry_id}',
                      date=entry['statistics_date'] if entry else None,currency_code=code,decimal_places=places,
                      member_count=len(entry['members']) if entry else 0,
                      url=f'/offsets?group={entry_id}' if kind=='offset_group' else f'/accounts?transaction={entry_id}')
        charges=[]; occupied=0; all_occupied=0; missing=False
        for row in bound:
            total = Decimal(row['total_minor'])/10**self.places[row['plan_currency']] if row['total_minor'] is not None else None
            cost,status = self.charge(total,row['plan_currency'],result)
            nearest += int(status=='nearest')
            if cost is None:
                missing = True
            else:
                all_occupied += cost
                if row['plan_id'] != exclude_plan_id:
                    occupied += cost
            charges.append(dict(plan_id=row['plan_id'],system_id=row['budget_system_id'],system_name=row['system_name'],
                                month=row['month'],currency_code=row['plan_currency'],total=str(total) if total is not None else None,
                                charge=money(cost,places) if cost is not None else None,current=row['plan_id']==exclude_plan_id))
        if missing and not reason:
            reason = '已绑定预案缺少总额或换算汇率，请先整理来源占用'
        if net is not None and net < 0 and not reason:
            reason = '来源净收入为负，无法增加预算占用'
        available = net-occupied if net is not None and not missing else None
        remaining = net-all_occupied if net is not None and not missing else None
        token = hashlib.sha256(json.dumps([entry,bound,self.rates],sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()
        result.update(net=money(net,places) if net is not None else None,
                      occupied=money(occupied,places) if not missing else None,
                      available=money(available,places) if available is not None else None,
                      remaining=money(remaining,places) if remaining is not None else None,
                      complete=not reason,reason=reason,overallocated=remaining is not None and remaining<0,
                      nearest_count=nearest,bindings=charges,token=token)
        return result

    def preview(self, kind, entry_id, plan_id=None, code=None, total=None):
        from app.budgets import money, minor
        source = self.describe(kind,entry_id,plan_id)
        if code is not None and total is not None:
            minor(total,self.places.get(code,2))
            cost,status = self.charge(total,code,source)
            requested = money(cost,source['decimal_places']) if cost is not None else None
            gap = Decimal(source['available'])-Decimal(requested) if requested is not None and source['available'] is not None else None
            source.update(requested=requested,after_binding=str(gap) if gap is not None else None,
                          can_bind=source['complete'] and gap is not None and gap>=0,
                          charge_rate_status=status)
        return source

    def for_plan(self, plan_id):
        row = next((r for r in self.bindings if r['plan_id']==plan_id),None)
        if not row:
            return None
        source = self.describe(*reference(row),exclude_plan_id=plan_id)
        source['status'] = source['reason'] or ('收入来源超额占用，请调整预案' if source['overallocated'] else '来源额度已核验')
        return source

    def plan_sources(self, plan_id):
        keys = list(dict.fromkeys(reference(r) for r in self.bindings if r['plan_id']==plan_id))
        result = []
        for key in keys:
            source = self.describe(*key)
            source['status'] = source['reason'] or ('收入来源超额分配，请调整' if source['overallocated'] else '来源额度已核验')
            matching = [r for r in self.bindings if r['plan_id']==plan_id and reference(r)==key]
            source['allocated_to_plan'] = None if any(r['total_minor'] is None for r in matching) else str(sum(
                (Decimal(r['total_minor']) / 10**self.places[r['plan_currency']]
                 for r in matching), Decimal(0)))
            result.append(source)
        return result


def save_binding(c, plan_id, code, total, body, before):
    old = c.execute('SELECT * FROM budget_plan_funding WHERE plan_id=?',(plan_id,)).fetchone()
    explicit = 'funding' in body.model_fields_set
    selected = body.funding
    if explicit and selected is None:
        c.execute('DELETE FROM budget_plan_funding WHERE plan_id=?',(plan_id,))
        return
    if not explicit and not old:
        return
    key = (selected.entry_type, selected.entry_id) if selected else reference(old)
    if total is None:
        raise HTTPException(422, '绑定收入前请先填写预案总额。')
    catalog = FundingCatalog(c)
    source = catalog.preview(*key,plan_id,code,total)
    # The plan row is already updated in this transaction. Ignore its version/amount in the token check;
    # compare the preview token before writing the plan (handled by validate_preview).
    same = old is not None and reference(old)==key and before['currency_code']==code
    reducing = same and before['total'] is not None and Decimal(total)<=Decimal(before['total'])
    if not source['can_bind'] and not reducing:
        reason = source['reason'] or (f"可用 {source['available']} {source['currency_code']}，本预案需占用 {source['requested']}；请调小金额或更换来源。"
                                      if source['requested'] is not None else '缺少换算汇率，无法核验来源额度。')
        raise HTTPException(422, reason)
    if not old and not source['complete']:
        raise HTTPException(422, source['reason'])
    c.execute('''INSERT INTO budget_plan_funding(plan_id,transaction_id,group_id,currency_code) VALUES (?,?,?,?)
        ON CONFLICT(plan_id) DO UPDATE SET transaction_id=excluded.transaction_id,group_id=excluded.group_id,currency_code=excluded.currency_code''',
        (plan_id,key[1] if key[0]=='transaction' else None,key[1] if key[0]=='offset_group' else None,source['currency_code']))


def validate_preview(c, body, before):
    if body.funding is None:
        return
    source = FundingCatalog(c).describe(body.funding.entry_type,body.funding.entry_id,before['id'])
    if body.funding.expected_token != source['token']:
        raise HTTPException(409, '收入来源或其他预案占用已变化，请刷新来源额度后重试，当前草稿仍可保留。')


def check_currency_change(c, before_plans):
    catalog = FundingCatalog(c)
    keys = {reference(r) for r in catalog.bindings if r['plan_id'] in {p['id'] for p in before_plans}}
    for key in keys:
        source = catalog.describe(*key)
        if not source['complete'] or source['overallocated']:
            raise HTTPException(422, f"更正币种后，来源「{source['title']}」额度不足或无法换算，请先调整预案或解除绑定。")

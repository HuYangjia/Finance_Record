"""Read-only monthly budget execution; plans and actual ledger amounts remain separate."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException

from app.budgets import config_data, money, require_system, valid_month
from app.personal_cashflow import build_entries, read_ledger
from app.reporting import amount_to_base_currency, nearest_cny_exchange_rate


def month_index(month):
    year, number = map(int,month.split('-'))
    return (year-1)*12+number-1


def month_name(index):
    return f'{index//12+1:04d}-{index%12+1:02d}'


def member_amount(member, rates, target):
    if member['currency_code']==target:
        return member['amount'],'direct'
    if target in ('CNY','HKD'):
        return amount_to_base_currency(member['amount'],member['currency_code'],member['transaction_date'],rates,target)
    if target=='USD':
        amount,status=amount_to_base_currency(member['amount'],member['currency_code'],member['transaction_date'],rates,'CNY')
        if amount is not None and rates:
            rate=nearest_cny_exchange_rate(rates,member['transaction_date'])
            divisor=Decimal(rate['usd_cny_rate'])
            if divisor.is_finite() and divisor>0:
                return amount/divisor,('exact' if rate['rate_date']==member['transaction_date'] else 'nearest')
    return None,'missing'


def execution_report(c, end_month, start_month=None, system_id=None, currency_code=None):
    valid_month(end_month); start_month=valid_month(start_month or end_month)
    if start_month>end_month or month_index(end_month)-month_index(start_month)>=120:
        raise HTTPException(422,'执行历史请选择不超过 120 个月的有效区间；更早结余仍会自动承接。')
    config=config_data(c)
    if system_id is not None:
        require_system(c,system_id,True)
    if currency_code is not None and not any(r['code']==currency_code for r in config['currencies']):
        raise HTTPException(422,'币种不存在。')
    currencies={r['code']:r['decimal_places'] for r in config['currencies']}
    plans=defaultdict(list)
    for row in c.execute('''SELECT p.*,COALESCE(p.total_minor,(SELECT SUM(i.amount_minor)
            FROM budget_plan_items i WHERE i.plan_id=p.id),0) AS budget_total FROM budget_plans p'''):
        plans[row['budget_system_id']].append(dict(row))
    records,groups,rates=read_ledger(c)
    expenses=defaultdict(list)
    for entry in build_entries(records,groups,rates):
        if entry['kind']!='expense':
            continue
        targets = ({m['effective_budget_system_id'] for m in entry['members'] if m['transaction_type']=='expense'}
                   if entry['budget_mixed'] else {entry['budget_system_id']})
        for target in targets:
            if target is not None:
                expenses[target].append(entry)
    wanted=[month_name(i) for i in range(month_index(start_month),month_index(end_month)+1)]
    rows=[]
    for system in config['systems']:
        sid=system['id']; code=system['currency_code']; places=currencies.get(code,2)
        if system_id is not None and sid!=system_id or currency_code and code!=currency_code:
            continue
        if not system['is_active'] and not plans[sid] and not expenses[sid]:
            continue
        dates=[p['month'] for p in plans[sid]]+[e['statistics_date'][:7] for e in expenses[sid]]
        origin=system['execution_start_month'] or min(dates,default=end_month)
        deposits=defaultdict(int); compiled=set(); spending=defaultdict(Decimal)
        issues=defaultdict(list); nearest=defaultdict(int); counts=defaultdict(int)
        for p in plans[sid]:
            month=p['month']
            if not origin<=month<=end_month:
                continue
            compiled.add(month)
            if p['currency_code']==code:
                deposits[month]+=p['budget_total']
        for e in expenses[sid]:
            month=e['statistics_date'][:7]
            if not origin<=month<=end_month:
                continue
            counts[month]+=1
            issue=dict(month=month,entry_type=e['entry_type'],entry_id=e['entry_id'],title=e['title'])
            if e['budget_mixed']:
                issues[month].append(dict(issue,reason='主流水跨预算，尚未分配'))
                continue
            total=Decimal(0); missing=False
            for member in e['members']:
                value,status=member_amount(member,rates,code)
                nearest[month]+=int(status=='nearest')
                if value is None:
                    missing=True
                else:
                    total+=value if member['transaction_type']=='expense' else -value
            if missing:
                issues[month].append(dict(issue,reason='缺少汇率，无法完整换算'))
            else:
                spending[month]+=total
        reason = '请先设置预算币种' if not code else ('旧多币种计划待整理' if system['currency_conflict'] else '')
        balance=system['opening_minor']; complete=not reason; past_issues=[]; prior_nearest=0
        relevant=sorted(set(wanted)|set(deposits)|set(spending)|set(issues))
        for month in relevant:
            if month<origin:
                if month in wanted:
                    rows.append(dict(budget_system_id=sid,system_name=system['name'],currency_code=code,month=month,
                                     origin_month=origin,started=False,compiled=False,complete=False,reason='尚未到起算月份',
                                     opening=None,added=None,available=None,expense=None,closing=None,warnings=[]))
                continue
            opening=balance if complete else None
            added=None if system['currency_conflict'] or not code else deposits[month]
            available=opening+added if opening is not None and added is not None else None
            spent=int((spending[month]*10**places).quantize(Decimal(1),rounding=ROUND_HALF_UP))
            past_issues.extend(issues[month])
            complete=complete and not issues[month]
            balance+=deposits[month]-spent
            if month in wanted:
                amounts=dict(opening=opening,added=added,available=available,
                             expense=spent if not issues[month] and not reason else None,closing=balance if complete else None)
                rows.append(dict(budget_system_id=sid,system_name=system['name'],currency_code=code,month=month,
                                 origin_month=origin,origin_automatic=not system['execution_start_month'],started=True,
                                 compiled=month in compiled,complete=complete,reason=reason,
                                 warnings=list(past_issues),entry_count=counts[month],nearest_count=nearest[month],
                                 prior_nearest_count=prior_nearest,known_expense=money(spent,places),
                                 **{key:money(value,places) if value is not None else None for key,value in amounts.items()}))
            prior_nearest+=nearest[month]
    return dict(start_month=start_month,end_month=end_month,basis='offset',rows=rows)

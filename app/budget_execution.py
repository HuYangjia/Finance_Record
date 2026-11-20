"""Read-only monthly budget execution; plans and actual ledger amounts remain separate."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException

from app.budgets import money, require_system, valid_month
from app.budget_calculation import get_calculation
from app.reporting import amount_to_budget_currency


def month_index(month):
    year, number = map(int,month.split('-'))
    return (year-1)*12+number-1


def month_name(index):
    return f'{index//12+1:04d}-{index%12+1:02d}'


def member_amount(member, rates, target):
    return amount_to_budget_currency(member, rates, target)


def execution_report(c, end_month, start_month=None, system_id=None, currency_code=None, *, calculation=None):
    valid_month(end_month); start_month=valid_month(start_month or end_month)
    if start_month>end_month or month_index(end_month)-month_index(start_month)>=120:
        raise HTTPException(422,'执行历史请选择不超过 120 个月的有效区间；更早结余仍会自动承接。')
    calculation=get_calculation(c,calculation)
    config=calculation.config
    if system_id is not None:
        require_system(c,system_id,True)
    if currency_code is not None and not any(r['code']==currency_code for r in config['currencies']):
        raise HTTPException(422,'币种不存在。')
    currencies={r['code']:r['decimal_places'] for r in config['currencies']}
    plans=defaultdict(list)
    reviews={r['plan_id']:r['plan_version'] for r in c.execute('SELECT * FROM budget_legacy_reviews')}
    for row in c.execute('''SELECT p.*,COALESCE(p.total_minor,(SELECT SUM(i.amount_minor)
            FROM budget_plan_items i WHERE i.plan_id=p.id),0) AS budget_total FROM budget_plans p'''):
        plans[row['budget_system_id']].append(dict(row))
    rates=calculation.rates
    expenses=defaultdict(list)
    for entry in calculation.expenses:
        targets = ({m['effective_budget_system_id'] for m in entry['members'] if m['transaction_type']=='expense'}
                   if entry['budget_mixed'] else {entry['budget_system_id']})
        for target in targets:
            if target is not None:
                expenses[target].append(entry)
    wanted=[month_name(i) for i in range(month_index(start_month),month_index(end_month)+1)]
    rows=[]
    from app.budget_funding import reference
    funding=calculation.funding
    funding_issues=defaultdict(list)
    for binding in funding.bindings:
        source=funding.describe(*reference(binding))
        if source['reason'] or source['overallocated']:
            warning=dict(month=binding['month'],title=source['title'],
                         status=source['reason'] or '收入来源超额分配，请调整',
                         income_mode='allocation_id' in binding)
            if warning not in funding_issues[binding['budget_system_id']]:
                funding_issues[binding['budget_system_id']].append(warning)
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
            if e.get('budget_issue'):
                issues[month].append(dict(issue, reason=e['budget_issue']))
                continue
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
            source_issues=[dict(month=month,title=w['title'],reason=w['status'],
                               entry_type='income_allocation',entry_id=None)
                           for w in funding_issues[sid] if w['income_mode'] and w['month']==month]
            legacy_months=[p['month'] for p in plans[sid] if origin<=p['month']<=month
                           and p['funding_mode']=='legacy' and p['budget_total']>0
                           and reviews.get(p['id'])!=p['version']
                           and not any(b['plan_id']==p['id'] for b in funding.bindings)]
            if legacy_months and any(p['funding_mode']=='income' and p['budget_total']>0
                                     and origin<=p['month']<=month for p in plans[sid]):
                source_issues.append(dict(month=month,title='历史额度待核对',
                    reason='新收入与历史手填额度同时累计，请在“分配收入”中替代重复额度或确认独立资金',
                    entry_type='legacy_review',entry_id=None))
            past_issues.extend(source_issues)
            complete=complete and not issues[month] and not source_issues
            balance+=deposits[month]-spent
            if month in wanted:
                amounts=dict(opening=opening,added=added,available=None if source_issues else available,
                             expense=spent if not issues[month] and not reason else None,closing=balance if complete else None)
                rows.append(dict(budget_system_id=sid,system_name=system['name'],currency_code=code,month=month,
                                 origin_month=origin,origin_automatic=not system['execution_start_month'],started=True,
                                 compiled=month in compiled,complete=complete,reason=reason,
                                 legacy_plan_months=legacy_months,
                                 warnings=list(past_issues),funding_warnings=[w for w in funding_issues[sid] if origin<=w['month']<=month],entry_count=counts[month],nearest_count=nearest[month],
                                 prior_nearest_count=prior_nearest,known_expense=money(spent,places),
                                 **{key:money(value,places) if value is not None else None for key,value in amounts.items()}))
            prior_nearest+=nearest[month]
    return dict(start_month=start_month,end_month=end_month,basis='offset',rows=rows)

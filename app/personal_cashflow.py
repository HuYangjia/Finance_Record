"""Read-only, whole-event personal cashflow projection over the actual ledger."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException

from app.reporting import amount_to_base_currency, format_decimal, summarize_daily_records
from app.budgets import (
    daily_expense_system,
    effective_budget,
    normalize_budget_filter_ids,
)


def read_ledger(connection):
    """Call within a read/write transaction so members and rates share a snapshot."""
    rows = connection.execute("""
        SELECT t.*, a.id AS account_id, a.name AS account_name, a.currency_code,
               c.decimal_places, cat.name AS category_name,
               f.flag_type, f.note AS flag_note, m.group_id AS offset_group_id,
               g.title AS offset_group_title, b.name AS budget_system_name
        FROM transactions t
        JOIN accounts a ON a.id = COALESCE(t.source_account_id, t.destination_account_id)
        JOIN currencies c ON c.code = a.currency_code
        JOIN categories cat ON cat.id = t.category_id
        LEFT JOIN transaction_flags f ON f.transaction_id = t.id
        LEFT JOIN transaction_offset_members m ON m.transaction_id = t.id
        LEFT JOIN transaction_offset_groups g ON g.id = m.group_id
        LEFT JOIN budget_systems b ON b.id = t.budget_system_id
        WHERE t.transaction_type IN ('income', 'expense')
        ORDER BY t.transaction_date, t.id
    """).fetchall()
    records = []
    daily = daily_expense_system(connection)
    for row in rows:
        record = dict(row)
        record['amount'] = Decimal(row['outflow_minor'] if row['transaction_type'] == 'expense'
                                   else row['inflow_minor']) / (10 ** row['decimal_places'])
        records.append(effective_budget(record, daily))
    groups = [dict(row) for row in connection.execute("""
        SELECT g.*, c.name AS category_name FROM transaction_offset_groups g
        JOIN categories c ON c.id = g.category_id
    """)]
    rates = [dict(row) for row in connection.execute('SELECT * FROM daily_cny_exchange_rates ORDER BY rate_date')]
    return records, groups, rates


def make_entry(metadata, members, rates, entry_type='offset_group'):
    result = dict(metadata)
    if entry_type == 'offset_group':
        primary = sorted((m for m in members if m['transaction_type'] == metadata['kind']),
                         key=lambda m: (m['transaction_date'], m['id']))
        if not primary:
            raise HTTPException(422, '事项至少需要一笔与方向一致的主流水。')
        anchor = primary[0]
        result.update(statistics_date=min(m['transaction_date'] for m in members),
                      anchor_transaction_id=anchor['id'], category_id=anchor['category_id'],
                      category_name=anchor['category_name'])
    result.update(entry_type=entry_type, entry_id=metadata['id'], members=members,
                  transaction_count=len(members), member_transaction_ids=[m['id'] for m in members])
    primary_members = [m for m in members if m['transaction_type']==metadata['kind']]
    budgets = {m.get('effective_budget_system_id',m.get('budget_system_id')) for m in primary_members}
    result['budget_mixed'] = len(budgets)>1
    result['budget_system_id'] = next(iter(budgets)) if len(budgets)==1 else None
    result['budget_system_ids'] = sorted(0 if value is None else value for value in budgets)
    result['budget_system_name'] = '跨预算 / 待归属' if len(budgets)>1 else (primary_members[0].get('effective_budget_system_name',primary_members[0].get('budget_system_name')) or '未归属')
    result['involved_accounts'] = list({m['account_id']: {'id': m['account_id'], 'name': m['account_name']}
                                       for m in members}.values())
    result['flagged'] = any(m['flag_type'] for m in members)
    result['_valuations'] = {base: value_entry(result, rates, base) for base in ('CNY', 'HKD')}
    return result


def value_entry(entry, rates, base):
    total = Decimal(0)
    coverage = {'direct': 0, 'exact': 0, 'nearest': 0, 'missing': 0}
    missing_currencies = set()
    details = []
    for member in entry['members']:
        amount, status = amount_to_base_currency(member['amount'], member['currency_code'],
                                                 member['transaction_date'], rates, base)
        coverage[status or 'missing'] += 1
        if amount is None:
            missing_currencies.add(member['currency_code'])
        else:
            total += amount if member['transaction_type'] == entry['kind'] else -amount
        selected_rate = None
        if status in ('exact', 'nearest'):
            from app.reporting import nearest_cny_exchange_rate
            selected_rate = nearest_cny_exchange_rate(rates, member['transaction_date'])
        details.append({'transaction_id': member['id'], 'status': status or 'missing',
                        'amount': format(amount, 'f') if amount is not None else None,
                        'rate_date': selected_rate['rate_date'] if selected_rate else None,
                        'rate_updated_at': selected_rate.get('updated_at') if selected_rate else None})
    return {'amount': None if coverage['missing'] else total, 'coverage': coverage,
            'missing_currencies': sorted(missing_currencies), 'members': details}


def build_entries(records, groups, rates):
    members = defaultdict(list)
    entries = []
    for row in records:
        if row['offset_group_id'] is not None:
            members[row['offset_group_id']].append(row)
        else:
            metadata = {'id': row['id'], 'title': row['counterparty'] or row['category_name'],
                        'kind': row['transaction_type'], 'statistics_date': row['transaction_date'],
                        'category_id': row['category_id'], 'category_name': row['category_name'], 'note': row['note']}
            entries.append(make_entry(metadata, [row], rates, 'transaction'))
    entries.extend(make_entry(group, members[group['id']], rates) for group in groups)
    return sorted(entries, key=lambda e: (e['statistics_date'], e['entry_type'], e['entry_id']), reverse=True)


def public_member(member):
    return {**{k: v for k, v in member.items() if k != 'amount'},
            'amount': format_decimal(member['amount'], member['decimal_places'])}


def serialize_entry(entry):
    raw = {}
    for member in entry['members']:
        currency = member['currency_code']
        bucket = raw.setdefault(currency, {'income': Decimal(0), 'expense': Decimal(0), 'places': member['decimal_places']})
        bucket[member['transaction_type']] += member['amount']
    result = {k: v for k, v in entry.items() if k not in ('members', '_valuations')}
    result['members'] = [public_member(member) for member in entry['members']]
    result['valuations'] = {
        base: {**value, 'available': value['amount'] is not None,
               'amount': format_decimal(value['amount'], 2) if value['amount'] is not None else None}
        for base, value in entry['_valuations'].items()
    }
    result['raw_totals'] = [
        {'currency_code': currency, 'income': format_decimal(bucket['income'], bucket['places']),
         'expense': format_decimal(bucket['expense'], bucket['places']),
         'net': format_decimal(bucket[entry['kind']] - bucket['expense' if entry['kind'] == 'income' else 'income'], bucket['places'])}
        for currency, bucket in sorted(raw.items())
    ]
    return result


def validate_filters(start_date=None, end_date=None, transaction_types=None, category_ids=None,
                     involved_account_id=None, keyword=None, limit=50, offset=0, **_):
    try:
        for value in (start_date, end_date):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError()
    except ValueError:
        raise HTTPException(422, '日期格式须为 YYYY-MM-DD。')
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, '开始日期不能晚于结束日期。')
    if set(transaction_types or []) - {'income', 'expense'}:
        raise HTTPException(422, '冲销后收支只支持收入和支出。')
    if any(i < 1 for i in category_ids or []) or (involved_account_id is not None and involved_account_id < 1):
        raise HTTPException(422, '账户和分类编号须大于零。')
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, '分页参数不正确。')
    if keyword and len(keyword) > 120:
        raise HTTPException(422, '关键词不能超过 120 个字符。')


def entry_budget_ids(entry):
    values = entry.get('budget_system_ids')
    if values is not None:
        return set(values)
    return {entry.get('budget_system_id') or 0}


def filter_entries(entries, start_date=None, end_date=None, transaction_types=None,
                   category_ids=None, involved_account_id=None, keyword=None, flagged_only=False,
                   entry_type=None, budget_system_id=None, budget_system_ids=None, **_):
    selected_budgets = set(normalize_budget_filter_ids(budget_system_ids, budget_system_id))
    if entry_type not in (None, 'transaction', 'offset_group'):
        raise HTTPException(422, '统计记录类型不支持。')
    validate_filters(start_date, end_date, transaction_types, category_ids, involved_account_id, keyword)
    result = []
    keyword = (keyword or '').strip().casefold()
    for entry in entries:
        if selected_budgets and not entry_budget_ids(entry).issubset(selected_budgets):
            continue
        if entry_type and entry['entry_type'] != entry_type:
            continue
        if start_date and entry['statistics_date'] < start_date or end_date and entry['statistics_date'] > end_date:
            continue
        if transaction_types and entry['kind'] not in transaction_types or category_ids and entry['category_id'] not in category_ids:
            continue
        if involved_account_id is not None and involved_account_id not in {a['id'] for a in entry['involved_accounts']}:
            continue
        if flagged_only and not entry['flagged']:
            continue
        text = ' '.join(str(entry.get(k) or '') for k in ('title', 'note', 'category_name'))
        text += ' ' + ' '.join(str(m.get(k) or '') for m in entry['members'] for k in ('counterparty', 'note', 'category_name'))
        if keyword and keyword not in text.casefold():
            continue
        result.append(entry)
    return result


def summarize(entries):
    totals = []
    for base in ('CNY', 'HKD'):
        amounts = {'income': Decimal(0), 'expense': Decimal(0)}
        missing = set()
        excluded = 0
        for entry in entries:
            value = entry['_valuations'][base]
            if value['amount'] is None:
                excluded += 1
                missing.update(value['missing_currencies'])
            else:
                amounts[entry['kind']] += value['amount']
        totals.append({'currency_code': base, **{k: format_decimal(v, 2) for k, v in amounts.items()},
                       'balance': format_decimal(amounts['income'] - amounts['expense'], 2),
                       'complete': excluded == 0, 'excluded_count': excluded,
                       'included_count': len(entries) - excluded, 'missing_currencies': sorted(missing)})
    from app.income_allocations import allocation_metadata
    return {'available': True, 'entry_count': len(entries),
            'transaction_count': sum(e['transaction_count'] for e in entries), 'valuations': totals,
            **allocation_metadata([r for e in entries for r in e.get('income_allocations',[])])}


def analyze(entries, start_date, end_date, kind):
    validate_filters(start_date=start_date, end_date=end_date, transaction_types=[kind])
    if not start_date or not end_date:
        raise HTTPException(422, '请选择完整日期范围。')
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if (end.year - start.year) * 12 + end.month - start.month + 1 > 12:
        raise HTTPException(422, '流水详情最多统计 12 个自然月。')
    converted = []
    coverage = {'direct_hkd_count': 0, 'exact_rate_count': 0, 'nearest_rate_count': 0,
                'excluded_count': 0, 'missing_currencies': set()}
    for entry in entries:
        value = entry['_valuations']['HKD']
        if value['amount'] is None:
            coverage['excluded_count'] += 1
            coverage['missing_currencies'].update(value['missing_currencies'])
            continue
        status = 'nearest_rate_count' if value['coverage']['nearest'] else 'exact_rate_count' if value['coverage']['exact'] else 'direct_hkd_count'
        coverage[status] += 1
        converted.append({'transaction_date': entry['statistics_date'], 'amount_hkd': value['amount'],
                          'category_name': entry['category_name'], 'direction': None,
                          'account_name': entry['involved_accounts'][0]['name'] if len(entry['involved_accounts']) == 1 else '多账户事项'})
    data = summarize_daily_records(converted, coverage, len(entries), start_date, end_date, kind)
    data.update(basis='offset', entry_count=len(entries), transaction_count=sum(e['transaction_count'] for e in entries),
                main_amount_label='净支出' if kind == 'expense' else '净收入', account_label='涉及账户',
                filters={'start_date': start_date, 'end_date': end_date})
    return data

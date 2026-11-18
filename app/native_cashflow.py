"""Original-currency event amounts: never convert other currencies into a total."""
from collections import defaultdict
from decimal import Decimal

from app.offset_categories import proportional, rounded_parts
from app.reporting import amount_to_budget_currency, format_decimal


def category_parts(entry, rates, code):
    members = entry['members']
    if not proportional(entry):
        return [dict(category_id=entry['category_id'], category_name=entry['category_name'], amount=sum(
            (m['amount'] if m['transaction_type'] == entry['kind'] else -m['amount']
             for m in members if m['currency_code'] == code), Decimal(0)))]
    primary = [m for m in members if m['transaction_type'] == 'expense']
    names = {m['category_id']: m['category_name'] for m in primary}
    values = dict.fromkeys(sorted(names), Decimal(0))
    targets = {t['refund_transaction_id']: t['expense_transaction_id'] for t in entry.get('category_targets', [])}
    primary_categories = {m['id']: m['category_id'] for m in primary}
    shared = Decimal(0)
    for m in members:
        if m['currency_code'] != code:
            continue
        if m['transaction_type'] == 'expense':
            values[m['category_id']] += m['amount']
        elif m['id'] in targets:
            values[primary_categories[targets[m['id']]]] -= m['amount']
        else:
            shared += m['amount']
    if shared:
        weights = defaultdict(Decimal)
        for m in primary:
            amount, _ = amount_to_budget_currency(m, rates, primary[0]['currency_code'])
            if amount is None:
                return [dict(category_id=cid, category_name=names[cid], amount=None) for cid in values]
            weights[m['category_id']] += amount
        total = sum(weights.values(), Decimal(0)); allocated = Decimal(0)
        for i, cid in enumerate(values):
            share = shared - allocated if i == len(values)-1 else shared * weights[cid] / total
            allocated += share
            values[cid] -= share
    return [dict(category_id=cid, category_name=names[cid], amount=amount) for cid, amount in values.items()]


def prepare(entry, rates):
    totals = defaultdict(Decimal)
    places = {}
    for m in entry['members']:
        code = m['currency_code']; places[code] = m['decimal_places']
        totals[code] += m['amount'] if m['transaction_type'] == entry['kind'] else -m['amount']
    entry['_native_amounts'] = dict(totals)
    entry['_native_places'] = places
    entry['_native_parts'] = {code: category_parts(entry, rates, code) for code in totals}


def project_currency(entry, code):
    if code not in entry['_native_amounts']:
        return None
    return dict(entry, native_currency_code=code,
                _native_amounts={code: entry['_native_amounts'][code]},
                _native_parts={code: entry['_native_parts'][code]})


def public_amounts(entry):
    return [dict(currency_code=code, amount=format_decimal(value, entry['_native_places'][code]) if value is not None else None,
                 complete=value is not None) for code, value in sorted(entry['_native_amounts'].items())]


def public_parts(entry):
    result = {}
    for code, rows in entry['_native_parts'].items():
        places = entry['_native_places'][code]
        complete = all(r['amount'] is not None for r in rows)
        amounts = rounded_parts([r['amount'] for r in rows], places, [r['category_id'] for r in rows]) if complete else [None] * len(rows)
        result[code] = [dict(category_id=r['category_id'], category_name=r['category_name'],
                            amount=format_decimal(value, places) if value is not None else None,
                            selected=not entry.get('category_basis') or r['category_id'] in entry['selected_category_ids'])
                       for r, value in zip(rows, amounts)]
    return result


def summarize(entries):
    totals = {}; places = {}
    for entry in entries:
        for code, value in entry['_native_amounts'].items():
            places[code] = entry['_native_places'][code]
            bucket = totals.setdefault(code, dict(income=Decimal(0), expense=Decimal(0), excluded_count=0))
            if value is None:
                bucket['excluded_count'] += 1
            else:
                bucket[entry['kind']] += value
    return [dict(currency_code=code, income=format_decimal(v['income'], places[code]),
                 expense=format_decimal(v['expense'], places[code]), balance=format_decimal(v['income']-v['expense'], places[code]),
                 complete=not v['excluded_count'], excluded_count=v['excluded_count']) for code, v in sorted(totals.items())]


def project_income_share(entry, selected, rates):
    """Allocated budgets contribute only their occupied share of the original source."""
    reference = entry.get('budget_reference_currency_code') or entry['members'][0]['currency_code']
    net = Decimal(0); occupied = Decimal(0); complete = all(r['source_complete'] for r in selected)
    for m in entry['members']:
        value, _ = amount_to_budget_currency(m, rates, reference)
        if value is None:
            complete = False
        else:
            net += value if m['transaction_type'] == 'income' else -value
    for row in selected:
        value, _ = amount_to_budget_currency(dict(amount=Decimal(row['amount']), currency_code=row['budget_currency'],
            transaction_date=entry['statistics_date']), rates, reference)
        if value is None:
            complete = False
        else:
            occupied += value
    ratio = occupied/net if complete and net > 0 else None
    result = dict(entry)
    result['_native_amounts'] = {code: value * ratio if ratio is not None else None for code, value in entry['_native_amounts'].items()}
    result['_native_parts'] = {code: [dict(p, amount=p['amount'] * ratio if ratio is not None and p['amount'] is not None else None)
        for p in parts] for code, parts in entry['_native_parts'].items()}
    return result

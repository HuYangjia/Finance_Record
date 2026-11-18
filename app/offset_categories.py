"""Category shares are statistical projections; the actual ledger stays intact."""
from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from app.reporting import amount_to_budget_currency, format_decimal


def proportional(entry):
    return (entry['entry_type'] == 'offset_group' and entry['kind'] == 'expense'
            and entry.get('category_allocation_mode') == 'proportional')


def rounded_parts(values, places=2, keys=None):
    """Largest remainder, including signed amounts; sum equals rounded total."""
    scale = Decimal(10) ** places
    scaled = [v * scale for v in values]
    floors = [int(v.to_integral_value(rounding=ROUND_FLOOR)) for v in scaled]
    total = int(sum(scaled, Decimal(0)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    remainder = total - sum(floors)
    order = sorted(range(len(values)), key=lambda i: (-(scaled[i] - floors[i]), keys[i] if keys is not None else i))
    for i in order[:remainder]:
        floors[i] += 1
    return [Decimal(v) / scale for v in floors]


def category_parts(entry, rates, code):
    """Convert every original on its own date before shared/targeted deductions."""
    if not proportional(entry):
        values = [amount_to_budget_currency(m, rates, code)[0] for m in entry['members']]
        amount = None if any(v is None for v in values) else sum(
            (v if m['transaction_type'] == entry['kind'] else -v
             for m, v in zip(entry['members'], values)), Decimal(0))
        return [dict(category_id=entry['category_id'], category_name=entry['category_name'], amount=amount)]
    primary = [m for m in entry['members'] if m['transaction_type'] == 'expense']
    names = {m['category_id']: m['category_name'] for m in primary}
    result = {cid: dict(category_id=cid, category_name=names[cid], gross=Decimal(0), amount=None)
              for cid in sorted(names)}
    values = {m['id']: amount_to_budget_currency(m, rates, code)[0] for m in entry['members']}
    if any(v is None for v in values.values()):
        return list(result.values())
    categories = {m['id']: m['category_id'] for m in primary}
    for m in primary:
        result[m['category_id']]['gross'] += values[m['id']]
    gross = sum((row['gross'] for row in result.values()), Decimal(0))
    targets = {r['refund_transaction_id']: r['expense_transaction_id'] for r in entry.get('category_targets', [])}
    direct = defaultdict(Decimal)
    shared = Decimal(0)
    for m in entry['members']:
        if m['transaction_type'] != 'income':
            continue
        if m['id'] in targets:
            direct[categories[targets[m['id']]]] += values[m['id']]
        else:
            shared += values[m['id']]
    # The last category absorbs Decimal division residue, without rounding the event.
    assigned = Decimal(0)
    for i, row in enumerate(result.values()):
        deduction = shared - assigned if i == len(result) - 1 else shared * row['gross'] / gross
        assigned += deduction
        row['amount'] = row['gross'] - direct[row['category_id']] - deduction
    return list(result.values())


def project_categories(entry, category_ids):
    """Keep one whole event and all members, but count only selected shares."""
    available = entry.get('category_ids', [entry['category_id']])
    selected = set(category_ids) & set(available)
    if not selected:
        return None
    if not proportional(entry):
        return entry
    result = dict(entry, category_basis=True, selected_category_ids=sorted(selected))
    result['_whole_valuations'] = entry.get('_whole_valuations', entry['_valuations'])
    result['_valuations'] = {}
    result['_native_amounts'] = {}
    for code, rows in entry['_native_parts'].items():
        parts = [r for r in rows if r['category_id'] in selected]
        result['_native_amounts'][code] = None if any(r['amount'] is None for r in parts) else sum((r['amount'] for r in parts), Decimal(0))
    for code, whole in result['_whole_valuations'].items():
        parts = [r for r in entry['_category_parts'][code] if r['category_id'] in selected]
        result['_valuations'][code] = dict(whole, amount=None if whole['amount'] is None else
            sum((r['amount'] for r in parts), Decimal(0)))
    return result


def public_breakdown(entry):
    parts = entry.get('_category_parts', {})
    result = {}
    for code, rows in parts.items():
        complete = all(r['amount'] is not None for r in rows)
        amounts = rounded_parts([r['amount'] for r in rows], keys=[r['category_id'] for r in rows]) if complete else [None] * len(rows)
        result[code] = [dict(category_id=r['category_id'], category_name=r['category_name'],
            gross=format_decimal(r['gross'], 2) if complete else None,
            deduction=format_decimal(r['gross'] - a, 2) if complete else None,
            amount=format_decimal(a, 2) if a is not None else None,
            selected=not entry.get('category_basis') or r['category_id'] in entry['selected_category_ids'])
            for r, a in zip(rows, amounts)]
    return result

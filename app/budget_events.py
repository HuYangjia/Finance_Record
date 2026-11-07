"""Budget consumption projection, including signed net-income deficits."""


def expense_entries(entries):
    for entry in entries:
        if entry['kind'] == 'expense':
            yield entry
        elif entry['entry_type'] == 'offset_group' and entry.get('budget_role') in ('expense', 'unresolved'):
            result = dict(entry, kind='expense', original_kind='income', net_income_deficit=True)
            if entry['budget_role'] == 'unresolved':
                result['budget_issue'] = '收入事项缺少汇率，无法核实净额及预算扣减'
            yield result

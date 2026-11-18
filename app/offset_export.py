"""Export the same personal cashflow projection used by the UI."""
from decimal import Decimal

from openpyxl.styles import Font, PatternFill

from app.personal_cashflow import filter_entries, summarize, serialize_entry
from app.reporting import format_decimal
from app.offset_categories import category_parts


def add_sheet(workbook, name, title, headers, rows):
    sheet = workbook.create_sheet(name)
    sheet.append([title])
    sheet.append([])
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    for cell in sheet[3]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1F4E78')
    sheet.freeze_panes = 'A4'
    sheet.auto_filter.ref = f'A3:{sheet.cell(sheet.max_row, len(headers)).coordinate}'
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(60, max(14, max(len(str(c.value or '')) for c in column) + 3))
    return sheet


def append_offset_sheets(workbook, entries, rates, budgets, currency_places, selected_month, end_date, basis):
    selected = filter_entries(entries, start_date=selected_month+'-01', end_date=end_date)
    public = [serialize_entry(e) for e in selected]
    label = '冲销后收支（事项按统计日期，汇率按每笔原始日期）'
    totals = summarize(selected)['valuations']
    if basis == 'offset':
        workbook['月度总览'].title = '原始月度总览'
        add_sheet(workbook, '月度总览', f'{selected_month} · {label}；账户资产见账户余额 / 本币总统计',
                  ['计价币种', '收入', '支出', '结余', '覆盖情况', '未纳入数量', '缺失币种'],
                  [[v['currency_code'], v['income'], v['expense'], v['balance'],
                    '完整' if v['complete'] else '仅可计算部分', v['excluded_count'], '、'.join(v['missing_currencies'])] for v in totals])
        workbook['月度预算'].title = '原始月度预算'
        budget_rows = []
        for row in budgets:
            month = row['budget_month'][:7]
            matched = [e for e in entries if e['statistics_date'][:7] == month and e['kind'] == 'expense'
                       and row['category_id'] in e.get('category_ids', [e['category_id']])]
            values = []
            for entry in matched:
                parts = [p for p in category_parts(entry, rates, row['currency_code']) if p['category_id'] == row['category_id']]
                values.append(dict(amount=None if any(p['amount'] is None for p in parts) else
                                   sum((p['amount'] for p in parts), Decimal(0))))
            excluded = sum(v['amount'] is None for v in values)
            actual = sum((v['amount'] for v in values if v['amount'] is not None), Decimal(0))
            places = currency_places[row['currency_code']]
            budget = Decimal(row['budget_minor']) / 10**places
            budget_rows.append([month, row['currency_code'], row['category'], format_decimal(budget, places),
                                format_decimal(actual, places), format_decimal(budget-actual, places) if not excluded else None,
                                f'{actual / budget * 100:.1f}%' if budget and not excluded else None,
                                f'仅可计算部分，未纳入 {excluded} 条' if excluded else '完整'])
        add_sheet(workbook, '月度预算', '冲销后预算执行 · 折算至预算币种',
                  ['月份', '预算币种', '分类', '预算', '实际支出', '差额', '执行率', '覆盖情况'], budget_rows)
    for name in ('月度总览', '月度预算'):
        if basis == 'raw':
            workbook[name]['A2'] = f'{selected_month} · 原始收付口径'
    headers = ['统计日期', '类型', '标题', '分类', 'CNY 净额', 'HKD 净额', '原始流水数', '原币分项', '换算覆盖', '备注']
    def export_entry(e):
        return [e['statistics_date'], '净支出' if e['kind']=='expense' else '净收入', e['title'], e['category_name'],
                e['valuations']['CNY']['amount'], e['valuations']['HKD']['amount'], e['transaction_count'],
                '；'.join(f"{v['currency_code']}: 收 {v['income']} / 支 {v['expense']}" for v in e['raw_totals']),
                '；'.join(f"{b}: " + ('完整' if v['available'] else '无法完整换算 '+','.join(v['missing_currencies'])) for b,v in e['valuations'].items()), e.get('note')]
    add_sheet(workbook, '个人收支', f'{selected_month} · {label}', headers, [export_entry(e) for e in public])
    groups = [e for e in public if e['entry_type']=='offset_group']
    add_sheet(workbook, '冲销事项', f'{selected_month} · 按事项统计日期', ['事项编号']+headers,
              [[e['entry_id']]+export_entry(e) for e in groups])
    category_rows = []
    for entry in groups:
        breakdown = entry.get('category_breakdown', {})
        for part in breakdown.get('CNY', []):
            other = next(p for p in breakdown['HKD'] if p['category_id'] == part['category_id'])
            category_rows.append([entry['entry_id'], entry['title'], entry['statistics_date'], part['category_name'],
                part['gross'], part['deduction'], part['amount'], other['gross'], other['deduction'], other['amount']])
    add_sheet(workbook, '冲销分类分摊', '按原支出分类分摊；原始收付款与整项净额不变，缺汇率时对应金额留空',
              ['事项编号', '事项', '统计日期', '分类', 'CNY 原支出', 'CNY 冲减', 'CNY 净支出',
               'HKD 原支出', 'HKD 冲减', 'HKD 净支出'], category_rows)
    targets = {e['entry_id']: {t['refund_transaction_id']: t['expense_transaction_id']
               for t in e.get('category_targets', [])} for e in groups}
    add_sheet(workbook, '冲销关联明细', '相关事项全部成员，含查询月份之外的原始流水',
              ['事项编号', '事项', '统计日期', '流水编号', '原始日期', '原始类型', '账户', '币种', '金额', '分类', '对象', '备注', '指定冲减支出编号'],
              [[e['entry_id'], e['title'], e['statistics_date'], m['id'], m['transaction_date'], m['transaction_type'],
                m['account_name'], m['currency_code'], m['amount'], m['category_name'], m['counterparty'], m['note'],
                targets[e['entry_id']].get(m['id'])]
               for e in groups for m in e['members']])

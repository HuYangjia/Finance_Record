from datetime import date
try:
    import openpyxl
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl>=3.1.0"])
    import openpyxl

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

OUTPUT = r"D:\Person\财务\个人财务账本.xlsx"
TITLE = "个人财务账本"
MAX_TXN = 2004
MAX_ACCOUNTS = 104


def xl_color(css_hex: str) -> str:
    value = css_hex.removeprefix("#").upper()
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB, got: {css_hex}")
    return "FF" + value


XL_PRIMARY = xl_color("#1F4E78")
XL_HEADER = xl_color("#4472C4")
XL_LIGHT_BLUE = xl_color("#D9EAF7")
XL_INPUT = xl_color("#FFF2CC")
XL_CALC = xl_color("#E7E6E6")
XL_LIGHT_GREEN = xl_color("#E2F0D9")
XL_LIGHT_RED = xl_color("#FCE4D6")
XL_WHITE = xl_color("#FFFFFF")
XL_BORDER = xl_color("#B7C9D6")
XL_TEXT = xl_color("#1A1A1A")

thin = Side(style="thin", color=XL_BORDER)
header_fill = PatternFill("solid", fgColor=XL_HEADER)
title_fill = PatternFill("solid", fgColor=XL_PRIMARY)
input_fill = PatternFill("solid", fgColor=XL_INPUT)
calc_fill = PatternFill("solid", fgColor=XL_CALC)
summary_fill = PatternFill("solid", fgColor=XL_LIGHT_BLUE)
subheader_fill = PatternFill("solid", fgColor=XL_LIGHT_GREEN)
warn_fill = PatternFill("solid", fgColor=XL_LIGHT_RED)
white_font = Font(color=XL_WHITE, bold=True)
header_font = Font(color=XL_WHITE, bold=True)
title_font = Font(color=XL_WHITE, bold=True, size=16)
bold_font = Font(bold=True, color=XL_TEXT)
normal_font = Font(color=XL_TEXT)
center = Alignment(horizontal="center", vertical="center")
left_wrap = Alignment(horizontal="left", vertical="center", wrap_text=True)
right = Alignment(horizontal="right", vertical="center")

wb = Workbook()
wb.remove(wb.active)
wb.properties.title = TITLE
wb.properties.subject = "多币种个人收支、账户与投资跟踪"
wb.properties.creator = "WorkBuddy"
wb.calculation.fullCalcOnLoad = True
wb.calculation.forceFullCalc = True


def set_title(ws, title, end_col):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=end_col)
    c = ws.cell(1, 1, title)
    c.fill = title_fill
    c.font = title_font
    c.alignment = center
    ws.row_dimensions[1].height = 28


def style_headers(ws, row, headers):
    for col, value in enumerate(headers, 1):
        cell = ws.cell(row, col, value)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)
    ws.row_dimensions[row].height = 28


def style_data_area(ws, start_row, end_row, cols, input_cols=None, calc_cols=None):
    input_cols = input_cols or []
    calc_cols = calc_cols or []
    for row in range(start_row, end_row + 1):
        for col in range(1, cols + 1):
            cell = ws.cell(row, col)
            cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)
            cell.font = normal_font
            cell.alignment = left_wrap if col not in input_cols + calc_cols else center
            if col in input_cols:
                cell.fill = input_fill
            elif col in calc_cols:
                cell.fill = calc_fill
        ws.row_dimensions[row].height = 20


def add_table(ws, name, ref):
    table = Table(displayName=name, ref=ref)
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
    ws.add_table(table)


def add_list_validation(ws, formula, target):
    dv = DataValidation(type="list", formula1=formula, allow_blank=True)
    dv.error = "请从下拉列表选择，或在设置_分类表中补充选项。"
    dv.errorTitle = "输入不在预设范围内"
    dv.prompt = "可从下拉菜单选择。"
    dv.promptTitle = "录入提示"
    ws.add_data_validation(dv)
    dv.add(target)

# 1. 设置与分类
ws = wb.create_sheet("设置_分类")
set_title(ws, "个人财务账本 - 设置与分类", 11)
ws["A3"] = "账户清单（新增账户后，请同步在此处与账户主表添加）"
ws["A3"].font = bold_font
account_headers = ["账户编号", "账户名称", "资产/负债", "账户类型", "默认币种", "机构/位置", "是否启用", "备注"]
style_headers(ws, 4, account_headers)
accounts = [
    ["CNY-CASH", "人民币现金", "资产", "纸币现金", "CNY", "随身/家中", "是", ""],
    ["HKD-CASH", "港币现金", "资产", "纸币现金", "HKD", "随身/家中", "是", ""],
    ["ALIPAY", "支付宝余额", "资产", "电子钱包", "CNY", "支付宝", "是", ""],
    ["WECHAT", "微信零钱", "资产", "电子钱包", "CNY", "微信", "是", ""],
    ["CNY-BANK-01", "人民币银行卡", "资产", "银行活期", "CNY", "待填写", "是", ""],
    ["HKD-BANK-01", "港币银行卡", "资产", "银行活期", "HKD", "待填写", "是", ""],
    ["BROKER-CASH", "券商现金账户", "资产", "券商现金", "HKD", "待填写", "是", "可按实际币种拆分多个账户"],
    ["BROKER-INV", "券商投资市值", "资产", "投资资产", "HKD", "待填写", "是", "月末通过投资快照对账"],
    ["CC-HKD", "港币信用卡待还", "负债", "信用卡", "HKD", "未来新增", "否", "刷卡计支出；还款计转账"],
]
for r, values in enumerate(accounts, 5):
    for c, value in enumerate(values, 1):
        ws.cell(r, c, value)
style_data_area(ws, 5, MAX_ACCOUNTS, 8, input_cols=list(range(1, 9)))
add_table(ws, "AccountList", f"A4:H{MAX_ACCOUNTS}")

ws["J3"] = "下拉选项"
ws["J3"].font = bold_font
ws["J4"] = "交易类型"
transaction_types = ["收入", "支出", "同币种转账", "换汇", "券商入金", "券商出金", "投资收益/费用", "调整"]
for i, item in enumerate(transaction_types, 5):
    ws.cell(i, 10, item)
ws["K4"] = "一级分类"
primary_categories = ["奖学金/科研资助", "工资/劳务", "父母支持", "利息/股息", "报销/返还", "住房", "餐饮", "交通", "学习与科研", "医疗与保险", "通信与订阅", "日常购物", "社交与娱乐", "家庭与人情", "金融费用", "投资", "其他"]
for i, item in enumerate(primary_categories, 5):
    ws.cell(i, 11, item)
ws["L4"] = "币种"
for i, item in enumerate(["CNY", "HKD", "USD", "EUR", "JPY", "其他"], 5):
    ws.cell(i, 12, item)
ws["M4"] = "账户类型"
for i, item in enumerate(["纸币现金", "电子钱包", "银行活期", "银行定期", "券商现金", "投资资产", "信用卡", "其他"], 5):
    ws.cell(i, 13, item)
ws["N4"] = "资产/负债"
for i, item in enumerate(["资产", "负债"], 5):
    ws.cell(i, 14, item)
for c in range(10, 15):
    ws.cell(4, c).fill = header_fill
    ws.cell(4, c).font = header_font
    ws.cell(4, c).alignment = center
    ws.cell(4, c).border = Border(top=thin, bottom=thin, left=thin, right=thin)
for r in range(5, 30):
    for c in range(10, 15):
        ws.cell(r, c).border = Border(top=thin, bottom=thin, left=thin, right=thin)
        ws.cell(r, c).alignment = left_wrap
for col, width in {"A": 18, "B": 18, "C": 12, "D": 14, "E": 12, "F": 18, "G": 10, "H": 28, "J": 18, "K": 20, "L": 10, "M": 14, "N": 12}.items():
    ws.column_dimensions[col].width = width
ws.freeze_panes = "A5"
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = XL_LIGHT_GREEN

# 2. 账户主表
ws = wb.create_sheet("账户主表")
set_title(ws, "账户主表（账面余额由流水自动计算；每月填写实际余额用于对账）", 15)
headers = ["账户编号", "账户名称", "资产/负债", "账户类型", "币种", "机构/位置", "期初余额", "本年流入", "本年流出", "账面余额", "实际余额", "对账差额", "最近对账日", "是否启用", "备注"]
style_headers(ws, 3, headers)
for r, values in enumerate(accounts, 4):
    for c, value in enumerate(values, 1):
        if c <= 6:
            ws.cell(r, c, value)
        elif c == 7:
            ws.cell(r, c, 0)
        elif c == 14:
            ws.cell(r, c, values[6])
        elif c == 15:
            ws.cell(r, c, values[7])
for r in range(4, MAX_ACCOUNTS + 1):
    ws.cell(r, 8, f'=IF(A{r}="","",SUMIFS(\'流水_2026\'!$H$5:$H${MAX_TXN},\'流水_2026\'!$E$5:$E${MAX_TXN},A{r},\'流水_2026\'!$I$5:$I${MAX_TXN},E{r}))')
    ws.cell(r, 9, f'=IF(A{r}="","",SUMIFS(\'流水_2026\'!$F$5:$F${MAX_TXN},\'流水_2026\'!$D$5:$D${MAX_TXN},A{r},\'流水_2026\'!$G$5:$G${MAX_TXN},E{r}))')
    ws.cell(r, 10, f'=IF(A{r}="","",G{r}+H{r}-I{r})')
    ws.cell(r, 12, f'=IF(OR(A{r}="",K{r}=""),"",K{r}-J{r})')
style_data_area(ws, 4, MAX_ACCOUNTS, 15, input_cols=[1,2,3,4,5,6,7,11,13,14,15], calc_cols=[8,9,10,12])
for r in range(4, MAX_ACCOUNTS + 1):
    for c in [7,8,9,10,11,12]:
        ws.cell(r, c).number_format = '#,##0.00;[Red]-#,##0.00'
    ws.cell(r, 13).number_format = 'yyyy-mm-dd'
add_table(ws, "AccountLedger", f"A3:O{MAX_ACCOUNTS}")
add_list_validation(ws, "'设置_分类'!$D$5:$D$12", f"D4:D{MAX_ACCOUNTS}")
add_list_validation(ws, "'设置_分类'!$E$5:$E$10", f"E4:E{MAX_ACCOUNTS}")
add_list_validation(ws, "'设置_分类'!$N$5:$N$6", f"C4:C{MAX_ACCOUNTS}")
for col, width in {"A":18,"B":18,"C":11,"D":14,"E":10,"F":18,"G":14,"H":14,"I":14,"J":14,"K":14,"L":14,"M":14,"N":10,"O":28}.items():
    ws.column_dimensions[col].width = width
ws.freeze_panes = "A4"
ws.auto_filter.ref = f"A3:O{MAX_ACCOUNTS}"
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = XL_HEADER

# 3. 流水_2026
ws = wb.create_sheet("流水_2026")
set_title(ws, "流水_2026（黄色为手动录入；灰色为自动计算）", 16)
ws.merge_cells("A2:P2")
ws["A2"] = "规则：收入=外部→账户；支出=账户→外部；转账/入出金/换汇不计生活收支。换汇要同时填写付出和收到的金额及币种。"
ws["A2"].fill = summary_fill
ws["A2"].alignment = left_wrap
ws["A2"].font = bold_font
headers = ["日期", "月份", "交易编号", "来源账户", "去向账户", "付出金额", "付出币种", "收到金额", "收到币种", "交易类型", "一级分类", "二级分类", "对象/来源", "成交汇率", "收支归属", "备注"]
style_headers(ws, 4, headers)
for r in range(5, MAX_TXN + 1):
    ws.cell(r, 2, f'=IF(A{r}="","",DATE(YEAR(A{r}),MONTH(A{r}),1))')
    ws.cell(r, 14, f'=IF(OR(F{r}="",H{r}="",H{r}=0),"",F{r}/H{r})')
    ws.cell(r, 15, f'=IF(J{r}="收入","收入",IF(J{r}="支出","支出","不计收支"))')
style_data_area(ws, 5, MAX_TXN, 16, input_cols=[1,3,4,5,6,7,8,9,10,11,12,13,16], calc_cols=[2,14,15])
for r in range(5, MAX_TXN + 1):
    ws.cell(r, 1).number_format = 'yyyy-mm-dd'
    ws.cell(r, 2).number_format = 'yyyy-mm'
    for c in [6,8,14]:
        ws.cell(r, c).number_format = '#,##0.00;[Red]-#,##0.00'
add_table(ws, "Transactions2026", f"A4:P{MAX_TXN}")
add_list_validation(ws, "'设置_分类'!$A$5:$A$104", f"D5:E{MAX_TXN}")
add_list_validation(ws, "'设置_分类'!$L$5:$L$10", f"G5:G{MAX_TXN}")
add_list_validation(ws, "'设置_分类'!$L$5:$L$10", f"I5:I{MAX_TXN}")
add_list_validation(ws, "'设置_分类'!$J$5:$J$12", f"J5:J{MAX_TXN}")
add_list_validation(ws, "'设置_分类'!$K$5:$K$21", f"K5:K{MAX_TXN}")
ws.conditional_formatting.add(f"O5:O{MAX_TXN}", CellIsRule(operator="equal", formula=['"不计收支"'], fill=calc_fill))
for col, width in {"A":13,"B":11,"C":14,"D":18,"E":18,"F":13,"G":10,"H":13,"I":10,"J":15,"K":18,"L":16,"M":20,"N":13,"O":12,"P":30}.items():
    ws.column_dimensions[col].width = width
ws.freeze_panes = "A5"
ws.auto_filter.ref = f"A4:P{MAX_TXN}"
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = xl_color("#70AD47")

# 4. 月度预算
ws = wb.create_sheet("月度预算")
set_title(ws, "月度预算（只统计交易类型为“支出”的生活消费）", 8)
headers = ["月份", "币种", "一级分类", "预算金额", "实际支出", "差额", "执行率", "备注"]
style_headers(ws, 3, headers)
budget_categories = ["住房", "餐饮", "交通", "学习与科研", "医疗与保险", "通信与订阅", "日常购物", "社交与娱乐", "家庭与人情", "其他"]
row = 4
for month in range(1, 13):
    for currency in ["CNY", "HKD"]:
        for category in budget_categories:
            ws.cell(row, 1, date(2026, month, 1))
            ws.cell(row, 2, currency)
            ws.cell(row, 3, category)
            ws.cell(row, 5, f'=SUMIFS(\'流水_2026\'!$F$5:$F${MAX_TXN},\'流水_2026\'!$B$5:$B${MAX_TXN},A{row},\'流水_2026\'!$G$5:$G${MAX_TXN},B{row},\'流水_2026\'!$J$5:$J${MAX_TXN},"支出",\'流水_2026\'!$K$5:$K${MAX_TXN},C{row})')
            ws.cell(row, 6, f'=IF(D{row}="","",D{row}-E{row})')
            ws.cell(row, 7, f'=IF(OR(D{row}="",D{row}=0),"",E{row}/D{row})')
            row += 1
BUDGET_END = row - 1
style_data_area(ws, 4, BUDGET_END, 8, input_cols=[4,8], calc_cols=[5,6,7])
for r in range(4, BUDGET_END + 1):
    ws.cell(r, 1).number_format = 'yyyy-mm'
    for c in [4,5,6]:
        ws.cell(r, c).number_format = '#,##0.00;[Red]-#,##0.00'
    ws.cell(r, 7).number_format = '0.0%'
add_table(ws, "MonthlyBudget", f"A3:H{BUDGET_END}")
add_list_validation(ws, "'设置_分类'!$L$5:$L$10", f"B4:B{BUDGET_END}")
add_list_validation(ws, "'设置_分类'!$K$5:$K$21", f"C4:C{BUDGET_END}")
ws.conditional_formatting.add(f"F4:F{BUDGET_END}", CellIsRule(operator="lessThan", formula=["0"], fill=warn_fill))
for col, width in {"A":13,"B":10,"C":18,"D":14,"E":14,"F":14,"G":12,"H":28}.items():
    ws.column_dimensions[col].width = width
ws.freeze_panes = "A4"
ws.auto_filter.ref = f"A3:H{BUDGET_END}"
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = xl_color("#ED7D31")

# 5. 月度汇总
ws = wb.create_sheet("月度汇总")
set_title(ws, "月度汇总（按币种独立统计，不做强制汇率折算）", 13)
headers = ["月份", "币种", "总收入", "奖学金/科研", "工资/劳务", "父母支持", "其他收入", "总支出", "当月结余", "自有收入", "自有收入覆盖率", "支出笔数", "备注"]
style_headers(ws, 3, headers)
row = 4
for month in range(1, 13):
    for currency in ["CNY", "HKD"]:
        ws.cell(row, 1, date(2026, month, 1))
        ws.cell(row, 2, currency)
        ws.cell(row, 3, f'=SUMIFS(\'流水_2026\'!$H$5:$H${MAX_TXN},\'流水_2026\'!$B$5:$B${MAX_TXN},A{row},\'流水_2026\'!$I$5:$I${MAX_TXN},B{row},\'流水_2026\'!$J$5:$J${MAX_TXN},"收入")')
        ws.cell(row, 4, f'=SUMIFS(\'流水_2026\'!$H$5:$H${MAX_TXN},\'流水_2026\'!$B$5:$B${MAX_TXN},A{row},\'流水_2026\'!$I$5:$I${MAX_TXN},B{row},\'流水_2026\'!$J$5:$J${MAX_TXN},"收入",\'流水_2026\'!$K$5:$K${MAX_TXN},"奖学金/科研资助")')
        ws.cell(row, 5, f'=SUMIFS(\'流水_2026\'!$H$5:$H${MAX_TXN},\'流水_2026\'!$B$5:$B${MAX_TXN},A{row},\'流水_2026\'!$I$5:$I${MAX_TXN},B{row},\'流水_2026\'!$J$5:$J${MAX_TXN},"收入",\'流水_2026\'!$K$5:$K${MAX_TXN},"工资/劳务")')
        ws.cell(row, 6, f'=SUMIFS(\'流水_2026\'!$H$5:$H${MAX_TXN},\'流水_2026\'!$B$5:$B${MAX_TXN},A{row},\'流水_2026\'!$I$5:$I${MAX_TXN},B{row},\'流水_2026\'!$J$5:$J${MAX_TXN},"收入",\'流水_2026\'!$K$5:$K${MAX_TXN},"父母支持")')
        ws.cell(row, 7, f'=C{row}-D{row}-E{row}-F{row}')
        ws.cell(row, 8, f'=SUMIFS(\'流水_2026\'!$F$5:$F${MAX_TXN},\'流水_2026\'!$B$5:$B${MAX_TXN},A{row},\'流水_2026\'!$G$5:$G${MAX_TXN},B{row},\'流水_2026\'!$J$5:$J${MAX_TXN},"支出")')
        ws.cell(row, 9, f'=C{row}-H{row}')
        ws.cell(row, 10, f'=C{row}-F{row}')
        ws.cell(row, 11, f'=IF(OR(H{row}="",H{row}=0),"",J{row}/H{row})')
        ws.cell(row, 12, f'=COUNTIFS(\'流水_2026\'!$B$5:$B${MAX_TXN},A{row},\'流水_2026\'!$G$5:$G${MAX_TXN},B{row},\'流水_2026\'!$J$5:$J${MAX_TXN},"支出")')
        row += 1
SUMMARY_END = row - 1
style_data_area(ws, 4, SUMMARY_END, 13, input_cols=[13], calc_cols=list(range(3, 13)))
for r in range(4, SUMMARY_END + 1):
    ws.cell(r, 1).number_format = 'yyyy-mm'
    for c in range(3, 11):
        ws.cell(r, c).number_format = '#,##0.00;[Red]-#,##0.00'
    ws.cell(r, 11).number_format = '0.0%'
add_table(ws, "MonthlySummary", f"A3:M{SUMMARY_END}")
ws.conditional_formatting.add(f"I4:I{SUMMARY_END}", CellIsRule(operator="lessThan", formula=["0"], fill=warn_fill))
for col, width in {"A":13,"B":10,"C":14,"D":14,"E":14,"F":14,"G":14,"H":14,"I":14,"J":14,"K":16,"L":12,"M":28}.items():
    ws.column_dimensions[col].width = width
ws.freeze_panes = "A4"
ws.auto_filter.ref = f"A3:M{SUMMARY_END}"
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = XL_PRIMARY

# 6. 投资快照
ws = wb.create_sheet("投资快照")
set_title(ws, "投资快照（月末填写，用于追踪券商现金、投资市值和对账）", 11)
headers = ["快照日期", "券商/平台", "币种", "券商现金", "证券/基金市值", "其他投资资产", "总资产", "当月入金", "当月出金", "已实现收益", "备注"]
style_headers(ws, 3, headers)
for r in range(4, 104):
    ws.cell(r, 7, f'=IF(A{r}="","",SUM(D{r}:F{r}))')
style_data_area(ws, 4, 103, 11, input_cols=[1,2,3,4,5,6,8,9,10,11], calc_cols=[7])
for r in range(4, 104):
    ws.cell(r, 1).number_format = 'yyyy-mm-dd'
    for c in range(4, 11):
        ws.cell(r, c).number_format = '#,##0.00;[Red]-#,##0.00'
add_table(ws, "InvestmentSnapshots", "A3:K103")
add_list_validation(ws, "'设置_分类'!$L$5:$L$10", "C4:C103")
for col, width in {"A":13,"B":18,"C":10,"D":14,"E":16,"F":16,"G":14,"H":14,"I":14,"J":14,"K":30}.items():
    ws.column_dimensions[col].width = width
ws.freeze_panes = "A4"
ws.auto_filter.ref = "A3:K103"
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = xl_color("#8064A2")

# 7. 月度总览
ws = wb.create_sheet("月度总览", 0)
set_title(ws, "个人财务月度总览（原币种独立口径）", 12)
ws["A3"] = "查看月份"
ws["B3"] = date(2026, 9, 1)
ws["B3"].number_format = 'yyyy-mm'
ws["A3"].font = bold_font
ws["B3"].fill = input_fill
ws["B3"].border = Border(top=thin, bottom=thin, left=thin, right=thin)
ws["D3"] = "使用方式"
ws["D3"].font = bold_font
ws["E3"] = "修改 B3 查看不同月份；黄色单元格为需要自行录入或确认的内容。"
ws.merge_cells("E3:L3")
ws["E3"].alignment = left_wrap

for start_col, currency in [(1, "CNY"), (7, "HKD")]:
    end_col = start_col + 4
    ws.merge_cells(start_row=5, start_column=start_col, end_row=5, end_column=end_col)
    cell = ws.cell(5, start_col, f"{currency} 月度收支")
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = center
    metrics = [("总收入", 3), ("总支出", 8), ("当月结余", 9), ("自有收入", 10), ("自有收入覆盖率", 11)]
    for idx, (label, source_col) in enumerate(metrics, 6):
        ws.cell(idx, start_col, label)
        ws.cell(idx, start_col).fill = summary_fill
        ws.cell(idx, start_col).font = bold_font
        ws.merge_cells(start_row=idx, start_column=start_col + 1, end_row=idx, end_column=end_col)
        formula_col = get_column_letter(source_col)
        ws.cell(idx, start_col + 1, f'=SUMIFS(\'月度汇总\'!${formula_col}$4:${formula_col}${SUMMARY_END},\'月度汇总\'!$A$4:$A${SUMMARY_END},$B$3,\'月度汇总\'!$B$4:$B${SUMMARY_END},"{currency}")')
        ws.cell(idx, start_col + 1).fill = calc_fill
        ws.cell(idx, start_col + 1).font = bold_font
        ws.cell(idx, start_col + 1).alignment = right
        for c in range(start_col, end_col + 1):
            ws.cell(idx, c).border = Border(top=thin, bottom=thin, left=thin, right=thin)
    for r in [6,7,8,9]:
        ws.cell(r, start_col + 1).number_format = '#,##0.00;[Red]-#,##0.00'
    ws.cell(10, start_col + 1).number_format = '0.0%'

# Assets section
ws.merge_cells("A13:D13")
ws["A13"] = "账户余额概览（按账面余额；实际余额请在账户主表月度对账）"
ws["A13"].fill = title_fill
ws["A13"].font = white_font
ws["A13"].alignment = center
asset_headers = ["币种", "资产账面余额", "负债账面余额", "净资产（原币种）"]
for idx, header in enumerate(asset_headers, 1):
    ws.cell(14, idx, header)
    ws.cell(14, idx).fill = header_fill
    ws.cell(14, idx).font = header_font
    ws.cell(14, idx).alignment = center
for r, currency in enumerate(["CNY", "HKD", "USD", "EUR"], 15):
    ws.cell(r, 1, currency)
    ws.cell(r, 2, f'=SUMIFS(\'账户主表\'!$J$4:$J${MAX_ACCOUNTS},\'账户主表\'!$E$4:$E${MAX_ACCOUNTS},A{r},\'账户主表\'!$C$4:$C${MAX_ACCOUNTS},"资产")')
    ws.cell(r, 3, f'=SUMIFS(\'账户主表\'!$J$4:$J${MAX_ACCOUNTS},\'账户主表\'!$E$4:$E${MAX_ACCOUNTS},A{r},\'账户主表\'!$C$4:$C${MAX_ACCOUNTS},"负债")')
    ws.cell(r, 4, f'=B{r}-C{r}')
    for c in range(1, 5):
        ws.cell(r, c).border = Border(top=thin, bottom=thin, left=thin, right=thin)
        ws.cell(r, c).alignment = right if c > 1 else center
    for c in [2,3,4]:
        ws.cell(r, c).number_format = '#,##0.00;[Red]-#,##0.00'

# Budget overview
ws.merge_cells("F13:J13")
ws["F13"] = "预算执行（查看月份）"
ws["F13"].fill = title_fill
ws["F13"].font = white_font
ws["F13"].alignment = center
for c, header in enumerate(["币种", "预算", "实际支出", "差额", "执行率"], 6):
    ws.cell(14, c, header)
    ws.cell(14, c).fill = header_fill
    ws.cell(14, c).font = header_font
    ws.cell(14, c).alignment = center
for r, currency in enumerate(["CNY", "HKD"], 15):
    ws.cell(r, 6, currency)
    ws.cell(r, 7, f'=SUMIFS(\'月度预算\'!$D$4:$D${BUDGET_END},\'月度预算\'!$A$4:$A${BUDGET_END},$B$3,\'月度预算\'!$B$4:$B${BUDGET_END},F{r})')
    ws.cell(r, 8, f'=SUMIFS(\'月度预算\'!$E$4:$E${BUDGET_END},\'月度预算\'!$A$4:$A${BUDGET_END},$B$3,\'月度预算\'!$B$4:$B${BUDGET_END},F{r})')
    ws.cell(r, 9, f'=G{r}-H{r}')
    ws.cell(r, 10, f'=IF(OR(G{r}="",G{r}=0),"",H{r}/G{r})')
    for c in range(6, 11):
        ws.cell(r, c).border = Border(top=thin, bottom=thin, left=thin, right=thin)
        ws.cell(r, c).alignment = right if c > 6 else center
    for c in [7,8,9]:
        ws.cell(r, c).number_format = '#,##0.00;[Red]-#,##0.00'
    ws.cell(r, 10).number_format = '0.0%'
ws.conditional_formatting.add("I15:I16", CellIsRule(operator="lessThan", formula=["0"], fill=warn_fill))

# Charts from monthly summary
bar = BarChart()
bar.type = "col"
bar.style = 10
bar.title = "全年月度支出趋势（CNY）"
bar.y_axis.title = "金额"
bar.x_axis.title = "月份"
bar.height = 7
bar.width = 13
cats = Reference(wb["月度汇总"], min_col=1, min_row=4, max_row=26)
data = Reference(wb["月度汇总"], min_col=8, max_col=8, min_row=3, max_row=26)
bar.add_data(data, titles_from_data=True)
bar.set_categories(cats)
ws.add_chart(bar, "A21")

pie = PieChart()
pie.title = "查看月份：CNY 支出分类"
pie.height = 7
pie.width = 10
# Helper section for chart data
ws["N13"] = "CNY 支出分类"
ws["O13"] = "金额"
for c in [14,15]:
    ws.cell(13, c).fill = header_fill
    ws.cell(13, c).font = header_font
for i, category in enumerate(budget_categories, 14):
    ws.cell(i, 14, category)
    ws.cell(i, 15, f'=SUMIFS(\'月度预算\'!$E$4:$E${BUDGET_END},\'月度预算\'!$A$4:$A${BUDGET_END},$B$3,\'月度预算\'!$B$4:$B${BUDGET_END},"CNY",\'月度预算\'!$C$4:$C${BUDGET_END},N{i})')
    ws.cell(i, 15).number_format = '#,##0.00;[Red]-#,##0.00'
pie_data = Reference(ws, min_col=15, min_row=13, max_row=23)
pie_labels = Reference(ws, min_col=14, min_row=14, max_row=23)
pie.add_data(pie_data, titles_from_data=True)
pie.set_categories(pie_labels)
ws.add_chart(pie, "H21")
for col, width in {"A":16,"B":16,"C":12,"D":12,"E":12,"F":16,"G":14,"H":14,"I":14,"J":14,"K":14,"L":14,"N":18,"O":14}.items():
    ws.column_dimensions[col].width = width
ws.freeze_panes = "A5"
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = XL_PRIMARY

# 8. 使用说明
ws = wb.create_sheet("使用说明")
set_title(ws, "使用说明与记账规则", 6)
notes = [
    ("1. 每笔记账", "在“流水_2026”逐笔录入黄色列；灰色列由公式计算。不要在灰色列覆盖公式。"),
    ("2. 收入与支出", "收入：来源账户填“外部”，去向账户填实际到账账户，收到金额/币种必填。支出：来源账户填支付账户，去向账户填“外部”，付出金额/币种必填。"),
    ("3. 转账与券商", "银行卡间转账、券商入金/出金、信用卡还款都属于账户间资金移动，不计入生活收入或支出。券商入金可用“券商入金”类型；出金可用“券商出金”。"),
    ("4. 换汇", "换汇必须同时记录人民币/港币或其他外币的付出与收到金额。表中自动计算成交汇率=付出金额÷收到金额；手续费请另记为“支出-金融费用”。"),
    ("5. 信用卡", "未来新增信用卡时，在设置_分类与账户主表加入一个“负债-信用卡”账户。刷卡时记录为支出，银行卡还款时记录为转账，避免重复计支出。"),
    ("6. 每月对账", "月末将银行、电子钱包、现金和券商实际余额填进账户主表“实际余额”，查看“对账差额”。券商投资市值同步在“投资快照”记录。"),
    ("7. 年度归档", "跨年时复制“流水_2026”并命名为“流水_2027”；账户主表、设置、预算、投资快照和历史汇总保留。若新增下一年流水，应把汇总公式引用改到对应年度或扩展为跨年汇总。"),
    ("8. 币种原则", "CNY、HKD 等各币种独立统计。本模板不强制合并为单一净资产，避免汇率波动影响生活收支判断。以后可单独加汇率与估值视图。"),
]
style_headers(ws, 3, ["主题", "操作规则"])
for r, (topic, rule) in enumerate(notes, 4):
    ws.cell(r, 1, topic)
    ws.cell(r, 2, rule)
    for c in [1,2]:
        ws.cell(r, c).border = Border(top=thin, bottom=thin, left=thin, right=thin)
        ws.cell(r, c).alignment = left_wrap
    ws.cell(r, 1).font = bold_font
    ws.row_dimensions[r].height = 42
ws.column_dimensions["A"].width = 18
ws.column_dimensions["B"].width = 100
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = xl_color("#A5A5A5")

# Common print and view settings
for sheet in wb.worksheets:
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.sheet_view.zoomScale = 90

wb.save(OUTPUT)
print(OUTPUT)

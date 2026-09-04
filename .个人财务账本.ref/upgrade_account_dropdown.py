from pathlib import Path
try:
    import openpyxl
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl>=3.1.0"])
    import openpyxl
from openpyxl import load_workbook
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

PATH = Path(r"D:\Person\财务\个人财务账本.xlsx")
wb = load_workbook(PATH)
accounts = wb["账户主表"]
transactions = wb["流水_2026"]
settings = wb["设置_分类"]
notes = wb["使用说明"]

# 账户主表是唯一的真实账户来源。预留到第 104 行，新增账户填入任一空白行后会自动进入流水下拉。
# A3 为表头，A4 是第一条账户记录；公式故意排除系统行“外部”。
name_formula = "'账户主表'!$B$4:$B$104"
if "账户名称列表" in wb.defined_names:
    del wb.defined_names["账户名称列表"]
wb.defined_names.add(DefinedName("账户名称列表", attr_text=name_formula))

# 外部不是资产账户；保留为系统选项，避免收支录入时手动输入。
# 将其置于账户主表末端后方，不参与资产/负债汇总。
external_row = 104
if accounts.cell(external_row, 1).value in (None, ""):
    accounts.cell(external_row, 1, "SYSTEM-EXTERNAL")
    accounts.cell(external_row, 2, "外部")
    accounts.cell(external_row, 3, "系统")
    accounts.cell(external_row, 4, "系统对象")
    accounts.cell(external_row, 5, "")
    accounts.cell(external_row, 6, "非账户交易对手")
    accounts.cell(external_row, 14, "是")
    accounts.cell(external_row, 15, "收入、支出的交易对手；不参与资产、负债、净资产汇总。")

# 动态下拉替换原有账户编号下拉：流水中显示账户名称，新增账户后直接可选。
# 删除仅作用于来源/去向账户列的旧数据验证，其他下拉保持不变。
for dv in list(transactions.data_validations.dataValidation):
    sqref = str(dv.sqref)
    if "D5:D2004" in sqref or "E5:E2004" in sqref:
        transactions.data_validations.dataValidation.remove(dv)
account_dv = DataValidation(type="list", formula1="=账户名称列表", allow_blank=True)
account_dv.error = "请从账户主表的账户名称下拉中选择。"
account_dv.errorTitle = "账户不存在"
account_dv.prompt = "新增账户请先在账户主表新增一行；随后会自动出现在这里。"
account_dv.promptTitle = "账户下拉"
transactions.add_data_validation(account_dv)
account_dv.add("D5:E2004")

# 在使用说明里明确唯一维护入口。
notes["B4"] = "在“流水_2026”逐笔录入黄色列；灰色列由公式计算。账户名称下拉自动读取“账户主表”，新增账户只需在账户主表增加一行。不要在灰色列覆盖公式。"
notes["B7"] = "收入：来源账户选“外部”，去向账户选实际到账账户，收到金额/币种必填。支出：来源账户选支付账户，去向账户选“外部”，付出金额/币种必填。"
notes["B11"] = "未来新增八达通、PayMe、信用卡或券商子账户时，只在账户主表添加一行。流水表来源/去向账户下拉会自动出现；“外部”为系统交易对手，不参与资产和负债汇总。"

wb.calculation.fullCalcOnLoad = True
wb.calculation.forceFullCalc = True
wb.save(PATH)
print(PATH)

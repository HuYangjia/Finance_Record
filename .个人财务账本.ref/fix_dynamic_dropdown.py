from pathlib import Path
from openpyxl import load_workbook
from openpyxl.worksheet.datavalidation import DataValidation

path = Path(r"D:\Person\财务\个人财务账本.xlsx")
wb = load_workbook(path)
transactions = wb["流水_2026"]

# Clear every former source/destination-account validation so only one dynamic rule remains.
for dv in list(transactions.data_validations.dataValidation):
    if str(dv.sqref) == "D5:E2004":
        transactions.data_validations.dataValidation.remove(dv)

account_dv = DataValidation(type="list", formula1="=账户名称列表", allow_blank=True)
account_dv.error = "请从账户主表的账户名称下拉中选择。"
account_dv.errorTitle = "账户不存在"
account_dv.prompt = "新增账户请先在账户主表新增一行；随后会自动出现在这里。"
account_dv.promptTitle = "账户下拉"
transactions.add_data_validation(account_dv)
account_dv.add("D5:E2004")

# Preserve calculated values before recalculation: formulas engine cannot evaluate Chinese external sheet references,
# but Excel/WPS will calculate them when opened.
wb.calculation.fullCalcOnLoad = True
wb.calculation.forceFullCalc = True
wb.save(path)
print(path)

# 数据库直接维护说明

数据库文件为 `data/finance.db`。这是唯一数据源；前端和 Excel 导出都会直接读取它。

## 编辑原则

- 编辑前先关闭本地服务，或至少不要同时在前端提交数据。
- 修改前复制一份 `data/finance.db`，或使用 `backups/` 中的最近备份恢复。
- 不要修改任何表的 `id`、`created_at` 或 `updated_at`。
- 不要删除已经被流水、预算或对账记录引用的账户和分类；将 `is_active` 改为 `0` 停用即可。

## 账户

在 `accounts` 表新增账户时，至少填写：

- `code`：唯一且稳定的编号，例如 `HKD-BANK-01`
- `name`
- `account_class`：`asset` 或 `liability`
- `account_type`
- `currency_code`：必须先存在于 `currencies`
- `opening_balance_minor`：最小货币单位金额
- `opening_balance_date`：`YYYY-MM-DD`

金额使用最小货币单位：

- CNY、HKD、USD、EUR：`12345` 代表 `123.45`
- JPY：`12345` 代表 `12345`

资产与负债的期初余额都填写正数。负债账户的余额会在净资产中自动扣除。

示例：

```sql
INSERT INTO accounts (
  code, name, account_class, account_type, currency_code,
  institution, opening_balance_minor, opening_balance_date, note
) VALUES (
  'HKD-BANK-01', '港币银行卡', 'asset', 'bank_demand', 'HKD',
  '待填写', 0, '2026-01-01', ''
);
```

若漏填初始化金额，直接修改即可：

```sql
UPDATE accounts
SET opening_balance_minor = 250000
WHERE code = 'HKD-BANK-01';
```

例如，支付宝基金账户的起始市值为 `1,234.56 CNY`：

```sql
UPDATE accounts
SET opening_balance_minor = 123456
WHERE code = 'ALIPAY-FUND';
```

花呗初始欠款为 `500.00 CNY`：

```sql
UPDATE accounts
SET opening_balance_minor = 50000
WHERE code = 'ALIPAY-HUABEI';
```

### 常用账户与分类

前端不会读取账户名称、账户编号或人工排序来决定常用项，也不需要维护 YAML 等配置文件。

常用账户和分类由最近 90 天的真实流水自动生成：先按使用笔数排序，同笔数时按最近使用日期排序，最多显示 6 项。没有近期流水的账户或分类仍在“其他”中，可以正常选择。

系统会按上海日期保存每日快照：后端当天第一次启动时生成；若服务跨日仍未重启，账户或分类接口在当天第一次读取时补生成。当天已有快照时不会重新统计或覆盖，因此当天新录入、修改或删除的流水会在次日快照中反映。

## 币种与分类

新增币种：

```sql
INSERT INTO currencies (code, name, symbol, decimal_places)
VALUES ('GBP', '英镑', '£', 2);
```

新增支出分类：

```sql
INSERT INTO categories (kind, name, sort_order)
VALUES ('expense', '宠物', 120);
```

## 历史流水

前端支持新增支出、入账、转账和换汇。需要手工补录或修改流水时，必须同时遵守 `transactions` 表的字段约束：

- `income`：仅填写 `destination_account_id` 和 `inflow_minor`，并填写收入分类。
- `expense`：仅填写 `source_account_id` 和 `outflow_minor`，并填写支出分类。
- `transfer`、`fx_exchange`、`broker_deposit`、`broker_withdrawal`：同时填写来源、去向、付出和收到金额，不填写分类。

直接输入 SQL 前可先查看账户与分类编号：

```sql
SELECT id, code, name, currency_code FROM accounts ORDER BY id;
SELECT id, kind, name FROM categories ORDER BY kind, sort_order, id;
```

## 投资估值与汇率

投资账户应优先通过“投资与汇率”页面录入 `investment_valuations`，不要直接修改关联的 `transactions` 调整流水。每条估值可自动生成一笔单边 `adjustment`，并由 `generated_transaction_id` 关联；直接改该流水会使后续估值损益无法正确重算。

需要由数据库或 AI 协助修改投资估值时，修改 `investment_valuations.total_value_minor` 后，应重新保存该账户任一估值，或由后端重算该账户全部估值损益流水。金额仍使用最小货币单位。

`daily_cny_exchange_rates` 保存每天的 `USD/CNY` 与 `HKD/CNY` 快照。后端启动时会自动补齐当天缺失的记录。只有在公开数据源不可用或需要使用实际银行/券商换汇价时，才手工修改这张表。

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

常用账户和分类由真实流水自动生成，不使用全部历史记录：

- 账户统计最近 45 天；最近 14 天有流水的账户优先于仅在第 15-45 天有流水的账户。每一层内先按使用笔数排序，再按最近使用日期排序。
- 分类统计最近 90 天；先按使用笔数排序，同笔数时按最近使用日期排序。
- 账户或分类没有对应窗口内流水时，仍在“其他”中，可以正常选择。

账户明细页首次打开时，会默认选中优先级最高的启用账户。

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

## 冲销事项

- `transaction_offset_groups` 保存方向、备注和版本，并缓存自动推导的起点、日期、分类；没有结清状态、预测金额或储存净额。统计日期取全部成员最早日期，分类取最早一笔主方向流水（同日按编号）。读取以实际成员推导为准，兼容旧事项的缓存字段。
- `transaction_offset_members` 通过 `transaction_id` 主键保证一笔流水最多属于一个事项。成员只能是收入 / 支出，起点必须属于成员且方向匹配。
- 事项金额从原始成员和系统汇率实时计算。不要修改原流水金额来制造冲销，也不要把净额写成额外的调整流水。
- 通过第五页修改关系会自动备份并写 `offset_group` 审计。解散事项会级联删除成员关联，不删除任何原始流水。
- 原始流水被关联时，应用禁止直接删除；先移除关联或解散事项。直接编辑 SQLite 不执行服务层的跨表校验，优先使用第五页维护这些关系。
- 原始金额、日期等编辑会更新相关事项版本，并同步自动归属字段。添加或移除成员也会重算日期、分类和起点。历史汇率修改会影响统一计价结果。

完整规则见 [transaction-offset-design.md](transaction-offset-design.md)。

## 多预算计划与流水归属

通过第六页“预算计划”和“设置”管理新增预算，无需手工操作 SQLite。预算系统就是用途，先填写月总额，再填写二级存储分配；新增或细分存储栏目不新增数据库列。

- `budget_systems` 保存预算用途名称和启用状态；`budget_storage_methods` 保存存储层级，`budget_purposes` 保留旧用途标签，新分配不要求选择此标签。
- `budget_plans` 按系统、月份、币种唯一，`total_minor` 保存总预算；`budget_plan_items` 保存其存储分配，`budget_plan_revisions` 保存历史快照。累计总预算只加每份计划的当前总额，分配在总额内拆分，不重复相加。旧总额为 NULL 时查询暂按明细合计，不回填旧记录。
- 页面把同系统、同月的这些记录组成一份多币种预案，不新建重复父表。通过月度批量接口在一个事务内保存全部币种，失败全部回滚；每币种历史版本和原明细 ID 保持独立关联。
- `budget_assignment_rules`、`budget_entry_presets` 保存精确分类映射和命名预设。`app_settings` 保存分别适用于支出、入账的默认预算 ID，以及设置版本。
- `transactions.budget_system_id` 是可空的单独标记。NULL 支出在查询时按 `app_settings.daily_expense_budget_system_id` 计入日常，NULL 收入保持未归属；接口用 `effective_budget_system_id/name` 和 `budget_inherited` 区分有效归属与原始标记。此字段不决定真实支付账户，不自动扣减计划；转账、换汇、调整不使用日常归属。
- 原 `monthly_budgets` 保留原有含义和导出逻辑；第六页导出使用独立的“预算总额”“二级存储分配”“分币种汇总”。旧导出接口仍兼容。

首次安装预算功能前创建 `finance-pre-budget-*.db`；升级日常自动归属规则前创建 `finance-pre-daily-budget-*.db`。不批量回填流水，未标注支出在查询时自动映射到日常。设置和计划修改均创建备份并写入 `budget` 审计，更新时检查版本；使用过的栏目应停用，保留关联和历史。正在承接日常归属的预算须先更换关联，才能停用或删除。

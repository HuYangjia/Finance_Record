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
- `directory_key`：选择器目录，例如 `domestic_bank`、`overseas_bank` 或 `investment`
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
  institution, directory_key, opening_balance_minor, opening_balance_date, note
) VALUES (
  'HKD-BANK-01', '港币银行卡', 'asset', 'bank_demand', 'HKD',
  '待填写', 'overseas_bank', 0, '2026-01-01', ''
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

账户常用区和分类排序由真实流水自动生成，不使用全部历史记录：

- 账户同时统计最近 45 天和最近 14 天，排序分数为“45 天笔数 + 14 天笔数 × 5”；同分时依次比较 14 天笔数、45 天笔数和最近使用日期。这样长期习惯提供稳定性，近期变化占更大权重。
- 分类统计最近 45 天和 14 天，分数为“45 天笔数 + 14 天笔数 × 5”，再比较短窗口笔数、长窗口笔数和最近日期。收入与支出分类分别计算。
- 账户没有对应窗口内流水时仍在“全部账户”的业务目录中；分类没有近期流水时仍保留在连续列表末尾，不会被隐藏。

账户的 `directory_key` 决定“全部账户”中的一级目录，具体账户直接作为第二级。`institution` 仅用于目录内排序，不单独形成折叠层。内置目录保存在 `account_directories`，包括国内银行、香港及境外银行、投资、现金与电子钱包、交通与储值、负债及其他账户。目录只影响选择器位置，不影响账户类型、余额或统计。新增账户若暂未填写有效目录，接口会将它显示在“其他账户”。

账户明细页首次打开以及首页支出表单刷新时，会默认选中各自语义下优先级最高的启用账户。支出表单使用“支出来源”频率，不再固定选择某个账户。

系统会按上海日期保存每日快照：后端当天第一次启动时生成；若服务跨日仍未重启，账户或分类接口在当天第一次读取时补生成。当天已有快照时不会重新统计或覆盖，因此当天新录入、修改或删除的流水会在次日快照中反映。

首页便捷操作保存在 `transaction_entry_shortcuts`，只保存预填模板，不是流水。请优先通过设置页维护；相关写入带配置版本、全库备份和 `entry_shortcut` 审计。模板删除或停用不会改变已保存流水。

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

- `budget_systems` 保存预算用途名称、启用状态及唯一预算币种 `currency_code`；未指定时首次编制确定。`budget_storage_methods` 保存所有预算共用的存储层级，不再隶属某个预算；旧 ID 映射存入 `budget_storage_aliases`，迁移前备份 `finance-pre-shared-storage-*.db`。`budget_purposes` 保留各预算旧用途标签，新分配不要求选择此标签。
- `budget_plans` 按系统、月份、币种唯一，`total_minor` 保存总预算；`budget_plan_items` 保存其存储分配，`budget_plan_revisions` 保存历史快照。累计总预算只加每份计划的当前总额，分配在总额内拆分，不重复相加。旧总额为 NULL 时查询暂按明细合计，不回填旧记录。
- `budget_systems.execution_start_month` 可空，留空按该预算最早计划或支出月起算；`opening_minor` 保存有正负号的期初预算结余。两者通过设置页维护。执行结余按计划与冲销后支出只读计算，不存结转流水、不写回月计划；更正预算币种也保持期初可见数字并重新编码精度。新增字段前备份 `finance-pre-budget-execution-*.db`。
- 页面每系统、每月只编制一份计划，币种继承系统；原表的三字段唯一键保留以兼容旧多币种记录，所有写接口增加系统币种校验。更正币种走专用预览接口，统一更正该系统各月金额的单位，数字保持不变；保留计划与明细 ID，历史快照保留原币种，精度错误或版本冲突全部回滚。勿直接改数据库币种字段，以免把整数最小单位误当相同数字。
- 升级前备份 `finance-pre-budget-currency-*.db`，旧系统仅有一种计划币种时自动继承，旧多币种系统原样保留并只读展示，避免静默混加。
- `budget_assignment_rules`、`budget_entry_presets` 保存精确分类映射和命名预设。`app_settings` 保存分别适用于支出、入账的默认预算 ID，以及设置版本。
- `transactions.budget_system_id` 是可空的单独标记。NULL 支出在查询时按 `app_settings.daily_expense_budget_system_id` 计入日常，NULL 收入保持未归属；接口用 `effective_budget_system_id/name` 和 `budget_inherited` 区分有效归属与原始标记。此字段不决定真实支付账户，不自动扣减计划；转账、换汇、调整不使用日常归属。
- 原 `monthly_budgets` 保留原有含义和导出逻辑；第六页导出使用独立的“预算总额”“二级存储分配”“分币种汇总”。旧导出接口仍兼容。

首次安装预算功能前创建 `finance-pre-budget-*.db`；升级日常自动归属规则前创建 `finance-pre-daily-budget-*.db`。不批量回填流水，未标注支出在查询时自动映射到日常。设置和计划修改均创建备份并写入 `budget` 审计，更新时检查版本；使用过的栏目应停用，保留关联和历史。正在承接日常归属的预算须先更换关联，才能停用或删除。

# 个人财务账本：架构与数据设计

## 项目目标

以本地 SQLite 数据库作为唯一数据源，提供一个本地前端用于新增支出记录，并可一键导出清晰的 Excel 报表。

Excel 不再承担编辑职责，只用于查看、分享和归档。数据库允许使用 SQLiteStudio、DB Browser for SQLite 或 AI 辅助直接修改。

## 已确认决策

1. 系统需要支持多个币种，并允许未来增加新的币种。
2. 投资模块不记录证券、基金等单个标的；通过账户期末估值、定投流水和月度统计追踪整体收益。
3. Excel 导出以数据清晰、统计正确为优先，不要求复刻原账本的八张工作表、格式或图表。
4. 数据库是本地个人使用，不需要加密、登录、权限控制或云同步。
5. 前端提供支出、入账、账户内转账、换汇、账户明细和数据纠错；投资估值与汇率的前端设计见独立文档。

## 技术方向

- 数据库：SQLite，文件名暂定为 `data/finance.db`。
- 后端：本地 HTTP API，负责校验数据、写入交易、生成汇总和导出 Excel。
- 前端：本地浏览器页面，优先支持新增支出。
- Excel：后端从数据库生成 `.xlsx` 文件，不从 Excel 回写数据库。

启动只等待数据库初始化及常用项快照，Excel 库在导出入口按需加载。当天汇率由 AnyIO 生命周期任务组后台同步，网络工作线程只取数；取消等待后不落库。自动同步写入前使用短事务复查，避免覆盖期间的人工更新，显式强制同步仍允许覆盖。实现与隔离性能验证见 [startup-performance.md](startup-performance.md)。

## 数据对象

### currencies

币种定义。

- `code`：主键，例如 `CNY`、`HKD`、`USD`、`JPY`
- `name`：显示名称
- `symbol`：显示符号，可为空
- `decimal_places`：金额小数位，例如 JPY 为 0
- `is_active`：是否允许继续使用

### accounts

资金账户与负债账户。

- `id`：主键
- `code`：稳定且唯一的账户编号，例如 `CNY-BANK-01`
- `name`：账户名称
- `account_class`：`asset` 或 `liability`
- `account_type`：现金、电子钱包、活期账户、券商现金、投资资产、信用卡等
- `currency_code`：账户固定币种
- `institution`：银行、平台或存放位置
- `opening_balance_minor`：期初余额，按最小货币单位保存，资产和负债均填写正数
- `opening_balance_date`：期初余额对应日期
- `is_active`：是否可在新流水中使用
- `note`：备注
- `created_at`、`updated_at`

账户的当前余额不保存，始终由期初余额和流水计算。若漏填或填错初始化金额，可以直接修改 `opening_balance_minor`。

### categories

收入与支出分类，支持两级及更多层级。

内置支出分类将食品和日用品统一为“食品杂货”，洗衣、剪发、修鞋等日常生活服务归入“生活开销”。启动迁移把旧“日常购物”的流水、冲销缓存、预算映射、预设、旧月度分类预算和常用分类快照合并到“食品杂货”，随后删除旧分类；旧“个人护理”在目标名称尚未存在时原位改名为“生活开销”，保留分类 ID 及全部引用。两项迁移分别生成 `finance-pre-category-merge-*.db` 与 `finance-pre-living-expense-category-*.db`，不修改金额、账户或预算归属。

- `id`：主键
- `kind`：`income` 或 `expense`
- `parent_id`：上级分类，可为空
- `name`：分类名称
- `sort_order`：显示顺序
- `is_active`：是否可继续使用

### transactions

统一保存收入、支出、账户转账、换汇和调整。

- `id`：主键
- `transaction_date`：交易日期
- `transaction_type`：`income`、`expense`、`transfer`、`fx_exchange`、`broker_deposit`、`broker_withdrawal` 或 `adjustment`
- `source_account_id`：资金付出账户；收入时为空
- `destination_account_id`：资金收到账户；支出时为空
- `outflow_minor`：付出金额，按来源账户币种的最小单位保存
- `inflow_minor`：收到金额，按去向账户币种的最小单位保存
- `category_id`：收入或支出分类；账户间移动通常为空
- `budget_system_id`：可空的预算归属，供收入、支出筛选；不改变账户余额或预算计划
- `counterparty`：交易对象或商户，可为空
- `note`：备注
- `created_at`、`updated_at`

规则：

- 支出：有来源账户、无去向账户、必须选择支出分类。
- 收入：无来源账户、有去向账户、必须选择收入分类。
- 转账、券商入金和券商出金：来源与去向账户均存在，不计入生活收支。
- 换汇：来源与去向账户均存在，两个金额可不同，币种由账户确定。
- 调整：用于修正历史账实差异，不用于日常消费。
- 投资估值损益：数据库中复用单边 `adjustment` 流水，由 `investment_valuations.generated_transaction_id` 关联并标记为自动生成；更新投资账户余额但不计入生活收支。

账户余额计算规则为：

```text
资产账户：期初余额 + 所有流入金额 - 所有流出金额
负债账户：期初余额 + 所有流出金额 - 所有流入金额
```

资产和负债余额均使用正数保存；同一币种净资产为资产余额减去负债余额。

### monthly_budgets

月度生活预算。

- `id`：主键
- `budget_month`：月份，以当月第一天保存
- `category_id`：支出分类
- `currency_code`：预算币种
- `amount_minor`：预算金额
- `note`

同一月份、分类和币种只能有一条预算。不同币种独立统计，不强制换算。

### account_reconciliations

月末或任意日期的实际余额对账记录。

- `id`：主键
- `account_id`
- `reconciliation_date`
- `actual_balance_minor`
- `note`

账面余额由流水计算，实际余额由用户填写；两者的差额用于发现漏记或错误。

### investment_snapshots

第一阶段的投资汇总快照，不记录单个证券持仓。

- `id`：主键
- `snapshot_date`
- `platform`
- `currency_code`
- `cash_minor`
- `market_value_minor`
- `other_assets_minor`
- `deposit_minor`
- `withdrawal_minor`
- `realized_profit_minor`
- `note`

新的账户级投资估值、区间损益、本币总资产和历史汇率设计见 [investment-valuation-and-fx-design.md](investment-valuation-and-fx-design.md)。`investment_snapshots` 保留用于兼容旧数据和 Excel 导出，不作为后续投资页面的主数据源。

## 汇总与导出

以下内容由 SQL 查询或数据库视图实时计算，不做重复存储：

- 各账户账面余额
- 各币种资产、负债和净资产
- 按月、按币种的收入、支出、结余
- 按月、按币种的账户内转入、转出和换汇路径
- 按分类的预算、实际支出和差额
- 对账差额

Excel 导出至少包含：月度总览、账户余额、交易流水、月度预算与执行、月度汇总、投资快照、分类与账户清单。

## 账户明细页面

前端页面按职责分为：

1. 记账：只新增支出、入账、转账和换汇。
2. 概况与查看：按月份查看整体收支、可用现金、净资产、内部资金流动和最近流水。
3. 账户明细：查看一个具体账户的当前状态和全部资金路径。
4. 流水查询：跨账户查询历史流水，不要求先选定一个账户。
5. 投资与汇率：录入投资账户期末估值与历史汇率，查询区间损益、月统计和本币总资产。

账户明细页不新增数据库表，也不承担账户编辑。第一版应包含：

- 账户选择列表：名称、币种、资产/负债类型和当前账面余额。
- 当前状态：期初余额与日期、累计流入、累计流出、当前账面余额。
- 流水筛选：电脑端默认当前自然月，支持本月/上月/季度/年/全部/自定义及前后期切换；账户明细与流水查询各自保留范围。API 不传日期仍查全历史，支持分页，不限制为最近 20 笔。
- 单账户流水：日期、类型、对方账户或外部对象、流入、流出、该笔后的账面余额、分类和备注。
- 转账 / 换汇显示：明确标示“转入”或“转出”，显示对方账户；换汇同时显示付出和收到金额及两种币种。

逐笔余额必须从期初余额和完整历史流水计算，不能只从筛选后的第一笔开始累计：

```text
资产账户逐笔余额 = 期初余额 + 累计流入 - 累计流出
负债账户逐笔余额 = 期初余额 + 累计流出 - 累计流入
```

后端提供 `GET /api/accounts/{account_id}/detail`，支持起止日期、流水类型、分类、对象/备注关键词、`limit` 和 `offset`。接口返回账户摘要、当前页流水和每笔后的余额；前端以独立“账户明细”导航页展示，不与综合概况混放。

同一页面还提供 `GET /api/transactions/search`，支持可选账户、日期范围、多个流水类型、多个分类、对象/备注关键词及分页。分类列表读取数据库中的收入、支出末级分类，包含已停用分类，保证历史流水仍可被筛选。

`static/date-range.js` 为两种查询提供独立日期控件；快捷操作重置分页并保留其他条件，连续切换忽略旧请求返回。原始流水汇总对完整日期、单边日期和全部历史使用相同筛选，不受分页影响；逐日分析继续限制完整日期且不超过 12 个月。所有日期筛选不影响账户的完整余额计算。

## 数据纠错与余额调整

“账户与流水”页已提供修改、删除、余额调整、期初余额修正、异常标记和最近对账状态；详细规则与数据模型见 [data-correction-design.md](data-correction-design.md)。

核心边界为：录错的真实业务优先修改或删除原流水；无法还原历史明细的账实差额才通过 `adjustment` 流水修正；期初余额录错则单独修正账户期初值。所有前端纠错写操作均需先创建数据库备份并写入审计日志。

## 冲销事项与个人收支

第五页“冲销与代收代付”将多笔实际收入、支出关联成可编辑事项，提供“原始收付 / 冲销后收支”两种统计口径。事项不设置结清状态，不预测未来金额；支持跨月、跨币种和负净额。账户余额、逐笔余额与原始资金流仍完全由实际流水决定。

详细数据模型、日期和汇率口径、统计筛选、页面、API、导出及验收标准见 [transaction-offset-design.md](transaction-offset-design.md)。

`transaction_offset_groups` 保存事项归属缓存与版本，`transaction_offset_members` 保存唯一流水关联。日期取全部成员最早日期，分类继承最早主流水；读取以实际成员为准，关联及原流水写入同步缓存。`app/offsets.py` 负责事项接口、事务校验与审计，`app/personal_cashflow.py` 生成个人收支结果，`app/reporting.py` 共享换算及每日统计，`app/offset_export.py` 将相同结果写入 Excel。

2026-11-18：支出事项的 `category_allocation_mode=proportional` 保留原消费分类，`transaction_offset_category_targets` 保存回款到同事项支出的可选指定。`app/offset_categories.py` 先按原日期换算，再按原金额权重分摊共享回款，指定回款只扣对应分类；Decimal 保留精度，分类展示以最大余数法处理分币尾差。查询保留一件事项与完整成员，分类过滤只投影命中份额，分析只拆分类分布、不拆日历和账户笔数；Excel 分类月预算及“冲销分类分摊”复用该计算。缓存分类 ID 仍继承最早主流水，混合模式显示“多分类”。预算执行和存储始终使用完整事项，不因分摊改变扣减。迁移前备份 `finance-pre-offset-categories-*.db`，旧事项保留 `anchor`，新建支出默认 `proportional`；收入事项规则不变。

原币口径修正：`app/native_cashflow.py` 生成逐币种原币净额、原币分类份额及汇总，概况与冲销查询默认显示 `native_totals/native_amounts`，旧 `valuations` 保留为明确标注的折算参考。`currency_code` 筛选串联原始流水 SQL、资金腿汇总、冲销投影及分析；`GET /api/currencies` 支持停用历史币种。原始换汇任一腿可命中，账户与币种在同一腿匹配，汇总仅累加匹配腿；事项保留完整成员而只累计所选原币。已分配收入按来源占用比例投影原币，避免预算筛选退回全额到账。选定币种后图表原币直算并动态显示单位，未选时旧 HKD 折算图保留且明确标注。Excel 月度总览与原币页面一致，完整折算移到独立附表。预算执行、存储、账本和资产总计均保持原规则，本轮无需数据库迁移。

2026-11-07：事项可空 `budget_system_id` 保存消费或净亏损承担预算，省略字段保留原值，显式空值恢复继承支出成员；升级前备份 `finance-pre-offset-budget-*.db`。`personal_cashflow.make_entry` 以来源计价币种推导 `budget_role`，个人收入方向和净额保留；`app/budget_events.py` 为执行与存储共同生成预算消费投影，把负净收入按净亏损扣减。跨预算或缺汇率会阻止显示完整结余。正净收入仍通过 `budget_income_allocations` 分配，详情接口显示实际分配；已有分配失效时保留并警示，绝不自动删减。冲销页统一提供承担预算选择与收入分配弹窗，归组流水编辑链接到该入口。

## 定期存款

`/term-deposits` 统一维护不同银行和币种的真实定存；首页“存入定期”打开同一记账弹窗。`app/term_deposits.py` 维护合同、预览、存入、编辑、确认到账、撤销到账与删除。`term_deposit_pools` 按币种关联唯一的公共 `bank_term` 账户，`term_deposit_accounts` 将多个来源映射到同币种公共账户，`term_deposits` 保存银行来源、期限、预期利率、计息基准、版本及真实流水引用。存入及本金回流为普通内部转账，只有实际到账利息计收入；定存资产参与净资产、不计可用现金、不参与投资估值，预期利息不入账。

全部写操作同事务处理合同与流水，并备份、审计和校验版本。受管账户和关联流水从定存页维护，账户页提供定存跳转；原始查询与余额继续使用统一流水。Excel 附加按报告截止日显示的“定期存款”表。升级备份 `finance-pre-term-deposits-*.db`，保留所有历史金额；预算存储“定期”仍单独表示用途额度。详见 [term-deposits-design.md](term-deposits-design.md)。

`app/term_migrations.py` 将旧按银行的汇总账户归集为公共币种账户。升级前备份 `finance-pre-shared-term-deposits-*.db`，复用账户及合同 ID，重定向本金流水端点、保留银行来源；多余旧账户归零停用。迁移校验活期余额、资产与原始金额，写入审计和版本标记，不新增收付款流水。

## 第六页：预算计划与设置

2026-10-20：预算改为由收入分配形成额度。`budget_income_allocations` 关联普通收入或收入事项与月计划，保存目标预算币种金额及来源计价币种；一收入多预算、多收入同预算均可。`budget_plans.funding_mode=income` 时总额是分配合计的只读缓存，与分配、版本、审计在同一写事务更新；`legacy` 仅保留历史手填及旧客户端兼容。下面的手填总额和单来源绑定描述属于历史实现，以本段及预算设计最新规则为准。

`app/income_allocations.py` 提供来源清单、详情、预览、分配写入、预算收入查询投影；`static/income-allocations.js` 提供首页拆分、共享来源弹窗和流水纠错入口。首页 `budget_allocations` 显式数组才产生分配，省略保留旧接口标签语义。现有 `FundingCatalog` 同时核验旧绑定与新分配，禁止双重占用；预算执行、存储和导出使用同一月总额。真实收入或汇率变更造成超配时，新模式结余标记未完整，已知消费仍可查询。

升级在备份后将已绑定预案转换为等额分配，未绑定计划保留并标明历史来源待核对。首次分配到旧手填月须明确替代旧总额，存储明细保留；旧收入标签不自动全额转换。第三页原始收付保留全额并另列分配汇总，冲销口径按所选预算的份额计收入；账户余额和原始流水金额不变。

一致性修正：预算筛选下的个人收入只来自实际分配，不再回退旧标签金额；来源有效性由 `FundingCatalog` 统一核验，查询汇总、分析与预算导出提供来源状态、发生／统计日和预算生效月。`budget_legacy_reviews` 按预案版本记录用户确认的独立历史资金；跨月分配前须核对目标预算未确认的旧额度，替代旧贡献时与新分配同事务修订版本并审计。新旧未核对额度并存时不显示已核实结余，原数据不被静默删除。新表升级前备份为 `finance-pre-budget-review-*.db`。

`budget_storage.py` 的剩余投影按月份依次加入预算、扣减消费、执行月末调拨。自动扣款不会在调拨后重新吸收历史消费；负净消费回到待分配，后续月份新增可吸收旧的待归属缺口。每月舍入及总余额继续与预算执行严格一致。

存储方式现为公共目录：`budget_storage_methods` 不再拥有预算系统外键，`budget_storage_aliases` 保留旧 ID 到公共项的映射。`app/budget_migrations.py` 在备份后以事务重建目录、映射当前明细引用，保留明细 ID、金额与旧版本快照；新增 ID 不复用旧别名。公共目录不再保留“待定存储方式”：迁移将当前占位明细移除并把其金额还原为计划总额中的待分配差额，无明确总额的旧计划先固化迁移前明细合计。用途历史标签仍按预算保留，旧栏目复制接口只复制这些标签。所有预算使用同一存储树，名称唯一约束按公共父项计算。

`app/budget_execution.py` 是只读执行投影，复用完整冲销事项，将各预算每月净支出按预算币种计算，并连续承接正负结余；不创建结转流水，也不改写计划。`budget_systems.execution_start_month/opening_minor` 保存可选起算月及有符号期初，迁移前备份 `finance-pre-budget-execution-*.db`。默认最早相关计划或支出月起算，缺汇率或主方向跨预算时完整性状态继续传到下月。`/api/budget/overview` 同快照返回所选月执行表，`/api/budget/execution` 支持按区间显示历史，始终计算显示区间以前的结余，Excel 同步执行结果。

第六页 `/budgets` 已实现，详细设计见 [budget-and-fund-allocation-design.md](budget-and-fund-allocation-design.md)。预算系统直接作为用途，按系统、月份、币种先录总预算，再填写二级存储分配。总预算独立于分配合计，允许保留待分配金额，禁止新模式超额分配。总表按“预算系统 × 存储方式”展示，各币种分别统计。计划金额不由实际收支或估值自动改变，保留现有 `monthly_budgets`。

实际流水新增可空的预算归属，第三页可按日常、专项等预算筛选。默认预算、分类映射、命名工资预设和栏目管理放入独立 `/settings`；首页支出、入账、转账采用独立表单与草稿。详细规则见 [budget-attribution-and-settings-design.md](budget-attribution-and-settings-design.md)。预算筛选影响查询明细、汇总和图表，不改变原始余额和收支算法。

支出、收入单选分类采用与账户相同的 45 天 / 14 天加权排序，在固定 8 行菜单中连续滚动；收入与支出分别统计，第三页查询的分类多选保持固定业务顺序。`daily_category_frequent_activity` 保存 45 天笔数、14 天笔数和最近日期，每日快照保证当天排序稳定。

`transaction_entry_shortcuts` 保存首页支出、入账和转账 / 换汇预填模板；`app/entry_shortcuts.py` 提供读取、增删改、启停和完整排序接口。设置页用卡片和弹窗维护，写入采用 `app_settings` 乐观版本、全库备份和 `entry_shortcut` 审计。模板只预填既有表单，不写流水、不参与余额或统计；引用对象失效时首页按钮禁用。详细规则见 [category-picker-and-entry-shortcuts-design.md](category-picker-and-entry-shortcuts-design.md)。

`account_directories` 保存账户选择器的稳定业务目录，`accounts.directory_key` 保存归属。账户单选组件顶部仍按角色显示最多 6 个近期优先账户，下方按“业务目录 → 账户”两级重新包含全部可选账户；现有 `institution` 只参与迁移判断和目录内排序，目录元数据不参与余额、资产负债、投资或流水统计。详细规则见 [account-picker-directory-design.md](account-picker-directory-design.md)。

`app/budget_schema.sql` 定义预算系统、用途、存储方式、月计划、明细、版本、映射及预设表。`app/budgets.py` 提供独立路由与事务校验，设置和计划写入复用全库备份与 `budget` 审计；`static/budgets.*` 和 `static/settings.*` 提供管理界面。每系统、月份和币种仅一份当前计划，历史快照不参与累计。首次迁移先创建 `finance-pre-budget-*.db`，旧流水新增字段保持 NULL。

总额与分配模型复用已有表。`budget_plans.total_minor` 保存父级总额，`budget_plan_items` 保存二级存储分配；旧 `purpose_id` 保留，新分配默认使用所属系统的占位标签，编辑旧明细时保留原标签。单币种接口通过 `allocation_mode=true` 校验总额及分配上限，兼容旧请求；当前页面使用下述月度接口，强制执行同一校验。新增 `/api/budget/overview` 与 `/api/budget/overview.xlsx` 按系统、币种、区间返回各份总额、分配和待分配，并分别累计。旧总额为空时只在查询时以明细合计代替；不回填、不静默修正旧超额计划。

2026-10-02 修正：`budget_systems.currency_code` 保存该预算系统唯一币种，可先留空，首次编制时确定。电脑端每月只编辑一个总额。`GET/PUT /api/budget/monthly/{system_id}/{month}` 保留列表包装以兼容读取，但提交只允许一份单币种计划，旧单币种写入接口同样校验系统币种。不同系统可选不同币种，累计与导出继续分币种统计。

`GET/PUT /api/budget/systems/{system_id}/currency` 提供全部月份预览与币种更正；使用设置版本和完整月计划 ID / 版本列表防止并发覆盖。更正保持可见数字，按目标精度重新编码最小单位，校验不通过则整体回滚。每月保留计划和明细 ID、追加版本快照，旧快照继续使用原币种。`static/budget-currency.js` 供预算页及设置页共用。迁移前创建 `finance-pre-budget-currency-*.db`，仅推导旧系统的唯一币种，不改历史计划；旧多币种系统保留只读，阻止继续混入金额。真实流水及其币种不受预算币种限制或变更影响。

`budget_plan_funding` 为月预案保存可选的单一收入来源：计划主键、普通收入或收入事项的互斥外键、来源计价币种。占用金额直接使用计划总额，不另设可编辑分配金额。`app/budget_funding.py` 复用完整冲销投影与执行汇率工具，计算净收入及全部预算、月份的当前占用。`GET /api/budget/funding-sources` 提供搜索与预览，计划写入校验预览令牌并在同一事务核验额度，币种更正也重新核验。绑定省略时兼容保留，显式空值解绑；迁移前备份 `finance-pre-budget-funding-*.db`，旧计划不自动绑定。

来源收入的金额、日期和事项成员可纠错，超配或缺少汇率会投影为警告，不改写计划、真实流水或预算结转。预算执行表同时提示此前月份的来源问题，Excel 包含来源及状态。删除已绑定来源、将已绑定普通收入加入事项、解散或改变已绑定事项方向须先解绑，防止双重来源与悬挂引用。具体币种折算及精度规则见预算设计文档。

电脑端 `/budgets` 主页面展示筛选、计划来源、执行结余及存储总表；新增和编辑使用原生 `dialog`，固定标题和操作区，内部滚动。`static/budget-funding.js` 负责来源选择弹窗、搜索分页、占用预览及版本令牌；`static/budgets.js` 统一保存计划和来源，成功关闭，失败留窗并保留输入。历史版本记录保存时的来源快照；复制仅带入总额及存储，不复制来源绑定。

存储余额由 `app/budget_storage.py` 投影：预算起算余额、各月存储分配、完整净支出及手动调拨共同计算，每月采用与执行表一致的舍入合计，所有存储（含待分配、待归属消费）之和等于预算结转。`budget_storage_defaults` 为每预算保存默认消费存储；`budget_storage_expenses` 为当前普通消费/支出事项保存例外；`budget_storage_transfers` 保存同预算内调拨月份、原币金额与版本。未设置默认的消费单列待归属，不猜测历史扣款。修改事项身份后按新身份选择默认或例外，原始流水不增字段、不改金额。

`GET /api/budget/storage/{system_id}/{month}` 提供余额与维护明细；默认、消费例外及调拨写入使用配置版本与 `BEGIN IMMEDIATE`，消费另外校验事项令牌。调拨校验该月转出项可用余额，消费不足仍保留负数；币种更正同时处理调拨精度与数字保留。`mutation` 可附加存储记录快照，修改前后与备份文件一起审计。迁移前备份 `finance-pre-storage-balances-*.db`，旧计划保持原样。

`static/budget-storage.js` 提供默认显示的剩余矩阵，以及“默认扣款 / 存储调拨 / 特殊消费”管理弹窗；原计划月度、累计视图保留并标明未扣消费。余额从执行起点连续计算，不应用计划累计起始月。概览、执行及存储投影处于同一读取事务；Excel 同步导出存储剩余及调拨记录。

日常消费采用有效归属：支出未单独标注时，统一按 `app_settings.daily_expense_budget_system_id` 统计，不回填历史流水。原始查询 SQL 与冲销主方向归属采用同一规则，返回原始 ID 和有效 ID，收入及资金移动不自动归入日常。该设置独立于首页预填，改名或变更预填不改变历史统计；升级前备份 `finance-pre-daily-budget-*.db`。`static/workspace.css` 提供统一视觉层，沿用原有字段、下拉组件和业务操作。

## 直接编辑数据库的约定

允许直接编辑基础数据，例如账户名称、初始余额、分类、预算和历史流水。编辑时应避免修改主键、删除被流水引用的账户或分类，以及手动创建不完整交易。

应用每次写入数据库前应创建备份。直接在数据库工具中编辑前，建议先关闭本地服务，避免数据库锁和未提交的数据冲突。

## 非第一阶段范围

- 用户系统、登录、权限和数据加密
- 云同步和多人协作
- 票据图片和附件
- 单个证券的买卖、持仓成本和实时行情
- 复杂拆分账单

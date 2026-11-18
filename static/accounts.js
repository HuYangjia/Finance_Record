const basisSelect = document.querySelector('#cashflow-basis');
try {
  const saved = localStorage.getItem('finance-accounts-basis');
  if (basisSelect) basisSelect.value = ['offset', 'groups'].includes(saved) ? saved : 'raw';
} catch (_) {}
const isOffsetBasis = () => Boolean(basisSelect && basisSelect.value !== 'raw');
const searchCurrency = document.querySelector('#search-currency');
let analysisCurrency = 'HKD';
let searchRequestId = 0;
let detailRequestId = 0;
function updateBasisDescription() {
  const description = document.querySelector('#basis-description');
  if (description) description.textContent = isOffsetBasis()
    ? '事项日期取最早原始流水。按原分类分摊的支出只汇总所选分类份额；收入及旧方式保留主流水分类。涉及账户命中后仍可展开完整事项。'
    : '按原始流水日期、类型和账户资金流查询。';
  const label = document.querySelector('#search-account').closest('label').querySelector('span');
  if (label) label.textContent = isOffsetBasis() ? '涉及账户' : '账户';
}
basisSelect?.addEventListener('change', () => {
  try { localStorage.setItem('finance-accounts-basis', basisSelect.value); } catch (_) {}
  updateBasisDescription();
  loadSearch(true).catch(error => setStatus(searchStatus, error.message, 'error'));
});
// Older cached HTML must not prevent the basic ledger from initializing.
const primaryNavigation = document.querySelector('.primary-nav');
if (primaryNavigation && !primaryNavigation.querySelector('a[href="/offsets"]')) {
  const link = document.createElement('a');
  link.href = '/offsets'; link.className = 'nav-link'; link.textContent = '冲销与代收代付';
  primaryNavigation.append(link);
}
const FLAG_LABELS = {
  suspected_duplicate: "疑似重复",
  missing_information: "信息不完整",
  suspicious_amount: "金额异常",
  other: "其他待核实",
};

const state = {
  accounts: [],
  categories: [],
  view: "account",
  detail: { offset: 0, total: 0, loaded: false, account: null, investmentRecords: [] },
  search: { offset: 0, total: 0, loaded: false },
  detailLimit: 50,
  searchLimit: 20,
};

const accountFilterPanel = document.querySelector(".account-filter-panel");
const accountWarning = document.querySelector("#detail-account-warning");
const accountSummaryPanel = document.querySelector("#account-summary-panel");
const ledgerPanel = document.querySelector("#ledger-panel");
const investmentPanel = document.querySelector("#account-investment-panel");
const searchPanel = document.querySelector("#transaction-search-panel");
const searchResultsPanel = document.querySelector("#search-results-panel");
const searchResultsDetails = document.querySelector("#search-results-details");

const detailForm = document.querySelector("#detail-filter-form");
const detailAccountSelect = document.querySelector("#detail-account");
const detailStartDateInput = document.querySelector("#detail-start-date");
const detailEndDateInput = document.querySelector("#detail-end-date");
const detailTransactionTypeSelect = document.querySelector("#detail-transaction-type");
const detailCategorySelect = document.querySelector("#detail-category");
const detailKeywordInput = document.querySelector("#detail-keyword");
const detailAdjustmentOnlyInput = document.querySelector("#detail-adjustment-only");
const detailFlaggedOnlyInput = document.querySelector("#detail-flagged-only");
const detailStatus = document.querySelector("#detail-status");
const previousDetailPageButton = document.querySelector("#previous-page");
const nextDetailPageButton = document.querySelector("#next-page");

const searchForm = document.querySelector("#transaction-search-form");
const searchBudgetOptions = document.querySelector('#search-budget-options');
const searchBudgetSummary = document.querySelector('#search-budget-summary');
const editorBudgetSelect = document.querySelector('#editor-budget-system');
let budgetConfig = null;

async function loadBudgetFilters() {
  try {
    budgetConfig=await request('/api/budget/config');
    const dailyHint=document.querySelector('#daily-budget-hint');
    if(dailyHint)dailyHint.textContent=`未单独指定预算的支出，自动计入「${budgetConfig.systems.find(s=>s.id===budgetConfig.daily_expense)?.name||'日常预算'}」，包含历史消费。`;
    renderSearchBudgetOptions(budgetConfig.systems);
  } catch(e) {
    const warning=document.querySelector('#budget-query-warning');
    if(warning){warning.textContent='预算选项加载失败，请刷新重试。账户与分类查询仍可使用。';warning.hidden=false;}
  }
}

function renderBudgetWarnings(data) {
  const warning=document.querySelector('#budget-query-warning');if(!warning||!budgetConfig)return;
  warning.replaceChildren();const rows=data.budget_warnings||[];warning.hidden=!rows.length;
  if(rows.length){warning.append(document.createTextNode(`以下 ${rows.length} 个跨预算事项尚未归属，未计入当前预算的冲销后汇总和图表：`));for(const row of rows){const a=document.createElement('a');a.href=`/offsets?group=${row.id}`;a.textContent=` ${row.title} `;warning.append(a);}warning.append(document.createTextNode('。可查看全部预算下的完整净额，或统一事项主流水的预算归属后重查。'));}
}
const searchAccountSelect = document.querySelector("#search-account");
const searchStartDateInput = document.querySelector("#search-start-date");
const searchEndDateInput = document.querySelector("#search-end-date");
const searchKeywordInput = document.querySelector("#search-keyword");
const searchAdjustmentOnlyInput = document.querySelector("#search-adjustment-only");
const searchFlaggedOnlyInput = document.querySelector("#search-flagged-only");
const searchCategoryOptions = document.querySelector("#search-category-options");
const searchCategorySummary = document.querySelector("#search-category-summary");
const searchStatus = document.querySelector("#search-status");
const previousSearchPageButton = document.querySelector("#previous-search-page");
const nextSearchPageButton = document.querySelector("#next-search-page");
const transactionSearchSummaryPanel = document.querySelector("#transaction-search-summary-panel");
const transactionSearchSummaryMeta = document.querySelector("#transaction-search-summary-meta");
const transactionSearchSummaryContent = document.querySelector("#transaction-search-summary-content");
const transactionAnalysisPanel = document.querySelector("#transaction-analysis-panel");
const analysisStatus = document.querySelector("#analysis-status");
const analysisConstraint = document.querySelector("#analysis-constraint");
const analysisContent = document.querySelector("#analysis-content");

const detailDates = window.DateRangeControls.create({
  host: document.querySelector('#detail-date-presets'), start: detailStartDateInput, end: detailEndDateInput,
  onApply: () => loadDetail(true).catch(error => setStatus(detailStatus, error.message, 'error')),
});
const searchDates = window.DateRangeControls.create({
  host: document.querySelector('#search-date-presets'), start: searchStartDateInput, end: searchEndDateInput,
  onApply: () => loadSearch(true).catch(error => setStatus(searchStatus, error.message, 'error')),
});

const editorDialog = document.querySelector("#transaction-editor-dialog");
const editorForm = document.querySelector("#transaction-editor-form");
const editorTitle = document.querySelector("#transaction-editor-title");
const editorStatus = document.querySelector("#editor-status");
const editorAdjustmentGuidance = document.querySelector("#editor-adjustment-guidance");
const editorClassifyAdjustmentButton = document.querySelector("#editor-classify-adjustment");
const editorTransactionId = document.querySelector("#editor-transaction-id");
const editorDateInput = document.querySelector("#editor-transaction-date");
const editorTypeSelect = document.querySelector("#editor-transaction-type");
const editorSourceSelect = document.querySelector("#editor-source-account");
const editorDestinationSelect = document.querySelector("#editor-destination-account");
const editorOutflowInput = document.querySelector("#editor-outflow-amount");
const editorInflowInput = document.querySelector("#editor-inflow-amount");
const editorCategorySelect = document.querySelector("#editor-category");
const editorCounterpartyInput = document.querySelector("#editor-counterparty");
const editorNoteInput = document.querySelector("#editor-note");
const editorReasonInput = document.querySelector("#editor-correction-reason");

const deleteDialog = document.querySelector("#transaction-delete-dialog");
const deleteForm = document.querySelector("#transaction-delete-form");
const deleteStatus = document.querySelector("#delete-status");
const deleteTransactionId = document.querySelector("#delete-transaction-id");
const deleteReasonInput = document.querySelector("#delete-reason");

const flagDialog = document.querySelector("#transaction-flag-dialog");
const flagForm = document.querySelector("#transaction-flag-form");
const flagStatus = document.querySelector("#flag-status");
const flagTransactionId = document.querySelector("#flag-transaction-id");
const flagTypeSelect = document.querySelector("#flag-type");
const flagNoteInput = document.querySelector("#flag-note");

const adjustmentDialog = document.querySelector("#adjustment-dialog");
const adjustmentForm = document.querySelector("#adjustment-form");
const adjustmentStatus = document.querySelector("#adjustment-status");
const adjustmentAccountId = document.querySelector("#adjustment-account-id");
const adjustmentDateInput = document.querySelector("#adjustment-date");
const adjustmentActualInput = document.querySelector("#adjustment-actual-balance");
const adjustmentReasonInput = document.querySelector("#adjustment-reason");
const adjustmentNoteInput = document.querySelector("#adjustment-note");

const openingBalanceDialog = document.querySelector("#opening-balance-dialog");
const openingBalanceForm = document.querySelector("#opening-balance-form");
const openingBalanceStatus = document.querySelector("#opening-balance-status");
const openingBalanceAccountId = document.querySelector("#opening-balance-account-id");
const openingBalanceDateInput = document.querySelector("#opening-balance-date");
const openingBalanceValueInput = document.querySelector("#opening-balance-value");
const openingBalanceReasonInput = document.querySelector("#opening-balance-reason");

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "请求失败，请稍后重试。");
  }
  return response.json();
}

function localDate() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function setStatus(target, message, type = "") {
  target.textContent = message;
  target.className = `dialog-status ${type}`.trim();
}

function setSelectOptions(select, records, placeholder, displayName, disabledPlaceholder = false) {
  select.replaceChildren();
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = placeholder;
  emptyOption.disabled = disabledPlaceholder;
  select.append(emptyOption);

  for (const record of records) {
    const option = document.createElement("option");
    option.value = record.id;
    option.textContent = displayName(record);
    option.dataset.activity = JSON.stringify(record.activity || {});
    if (record.directory_key) {
      option.dataset.directoryKey = record.directory_key;
      option.dataset.directoryName = record.directory_name || "其他账户";
      option.dataset.directoryOrder = record.directory_sort_order ?? 90;
      option.dataset.institution = record.institution || "其他机构";
      option.dataset.currency = record.currency_code || "";
    }
    select.append(option);
  }
}

function initializeFrequentPickers() {
  for (const select of [
    detailAccountSelect,
    searchAccountSelect,
    editorSourceSelect,
    editorDestinationSelect,
  ]) {
    window.createFrequentPicker(select, {
      commonLabel: "优先账户",
      displayMode: "account-directory",
      activityKey: "overall",
    });
  }
  for (const select of [detailCategorySelect, editorCategorySelect]) {
    window.createFrequentPicker(select, {
      activityKey: "usage",
      displayMode: "ranked-scroll",
      visibleRows: 8,
    });
  }
}

function refreshPickers(root = document) {
  window.refreshFrequentPickers(root);
}

function setPickerRequired(select, required) {
  window.setFrequentPickerRequired(select, required);
}

function formatAccountName(account) {
  const inactiveLabel = account.is_active ? "" : "（已停用）";
  return `${account.name} · ${account.currency_code}${inactiveLabel}`;
}

function formatCategoryName(category) {
  const inactiveLabel = category.is_active ? "" : "（已停用）";
  return `${category.display_name}${inactiveLabel}`;
}

function compareAccountsByPriority(left, right) {
  const leftActivity = left.activity?.overall || {};
  const rightActivity = right.activity?.overall || {};
  const leftRecordCount = Number(leftActivity.record_count) || 0;
  const rightRecordCount = Number(rightActivity.record_count) || 0;
  const leftRecentCount = Number(leftActivity.recent_record_count);
  const rightRecentCount = Number(rightActivity.recent_record_count);
  const leftTier = Number.isFinite(leftRecentCount)
    ? leftRecentCount > 0
      ? 2
      : leftRecordCount > 0
        ? 1
        : 0
    : leftRecordCount > 0
      ? 1
      : 0;
  const rightTier = Number.isFinite(rightRecentCount)
    ? rightRecentCount > 0
      ? 2
      : rightRecordCount > 0
        ? 1
        : 0
    : rightRecordCount > 0
      ? 1
      : 0;
  const leftPriorityCount = leftTier === 2 ? leftRecentCount : leftRecordCount;
  const rightPriorityCount = rightTier === 2 ? rightRecentCount : rightRecordCount;

  return (
    rightTier - leftTier
    || rightPriorityCount - leftPriorityCount
    || (rightActivity.last_transaction_date || "").localeCompare(
      leftActivity.last_transaction_date || "",
    )
    || left.name.localeCompare(right.name, "zh-CN")
  );
}

function selectedDetailAccountId() {
  return Number(detailAccountSelect.value);
}

function selectedSearchTypes() {
  return Array.from(
    document.querySelectorAll("#search-type-options input:checked"),
    (input) => input.value,
  );
}

function selectedSearchBudgets() {
  return Array.from(
    searchBudgetOptions?.querySelectorAll("input:checked") || [],
    (input) => input.value,
  );
}

function updateBudgetSummary() {
  if (!searchBudgetSummary) return;
  const selected = Array.from(searchBudgetOptions?.querySelectorAll("input:checked") || []);
  if (!selected.length) {
    searchBudgetSummary.textContent = "全部预算";
  } else if (selected.length <= 2) {
    searchBudgetSummary.textContent = selected.map((input) => input.dataset.label).join("、");
  } else {
    searchBudgetSummary.textContent = `已选 ${selected.length} 个预算`;
  }
}

function renderSearchBudgetOptions(systems) {
  if (!searchBudgetOptions) return;
  searchBudgetOptions.replaceChildren();
  const records = [
    { id: 0, name: "未归属（不含日常支出）" },
    ...systems.map((system) => ({
      id: system.id,
      name: system.name + (system.is_active ? "" : "（停用）"),
    })),
  ];
  for (const record of records) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    const text = document.createElement("span");
    checkbox.type = "checkbox";
    checkbox.value = record.id;
    checkbox.dataset.label = record.name;
    checkbox.addEventListener("change", updateBudgetSummary);
    text.textContent = record.name;
    label.append(checkbox, text);
    searchBudgetOptions.append(label);
  }
  updateBudgetSummary();
}

function selectedSearchCategories() {
  return Array.from(
    searchCategoryOptions.querySelectorAll("input:checked"),
    (input) => input.value,
  );
}

function updateCategorySummary() {
  const count = selectedSearchCategories().length;
  searchCategorySummary.textContent = count === 0 ? "全部分类" : `已选 ${count} 个分类`;
}

function openDialog(dialog) {
  dialog.showModal();
}

function closeDialog(dialog) {
  dialog.close();
}

function updateViewVisibility() {
  const showingAccount = state.view === "account";
  accountFilterPanel.hidden = !showingAccount;
  accountWarning.hidden = !showingAccount || state.accounts.length > 0;
  accountSummaryPanel.hidden = !showingAccount || !state.detail.loaded;
  ledgerPanel.hidden = !showingAccount || !state.detail.loaded;
  investmentPanel.hidden = !showingAccount || !state.detail.loaded || !state.detail.investmentRecords.length;
  searchPanel.hidden = showingAccount;
  transactionSearchSummaryPanel.hidden = showingAccount || !state.search.loaded;
  searchResultsPanel.hidden = showingAccount || !state.search.loaded;
  transactionAnalysisPanel.hidden = showingAccount || !state.search.loaded;
}

async function setView(view) {
  state.view = view;
  for (const button of document.querySelectorAll(".detail-view-mode")) {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  updateViewVisibility();
}

function buildDetailPath() {
  const parameters = new URLSearchParams({
    limit: String(state.detailLimit),
    offset: String(state.detail.offset),
  });
  if (detailStartDateInput.value) {
    parameters.set("start_date", detailStartDateInput.value);
  }
  if (detailEndDateInput.value) {
    parameters.set("end_date", detailEndDateInput.value);
  }
  if (detailTransactionTypeSelect.value) {
    parameters.set("transaction_type", detailTransactionTypeSelect.value);
  }
  if (detailCategorySelect.value) {
    parameters.set("category_id", detailCategorySelect.value);
  }
  if (detailKeywordInput.value.trim()) {
    parameters.set("keyword", detailKeywordInput.value.trim());
  }
  if (detailAdjustmentOnlyInput.checked) {
    parameters.set("manual_adjustments_only", "true");
  }
  if (detailFlaggedOnlyInput.checked) {
    parameters.set("flagged_only", "true");
  }
  return `/api/accounts/${selectedDetailAccountId()}/detail?${parameters.toString()}`;
}

function buildSearchPath() {
  const parameters = new URLSearchParams({
    limit: String(state.searchLimit),
    offset: String(state.search.offset),
  });
  if (searchCurrency?.value) parameters.set('currency_code', searchCurrency.value);
  for (const budgetId of selectedSearchBudgets()) {
    parameters.append('budget_system_ids', budgetId);
  }
  if (searchAccountSelect.value) {
    parameters.set(isOffsetBasis() ? "involved_account_id" : "account_id", searchAccountSelect.value);
  }
  if (searchStartDateInput.value) {
    parameters.set("start_date", searchStartDateInput.value);
  }
  if (searchEndDateInput.value) {
    parameters.set("end_date", searchEndDateInput.value);
  }
  for (const transactionType of selectedSearchTypes()) {
    parameters.append("transaction_types", transactionType);
  }
  for (const categoryId of selectedSearchCategories()) {
    parameters.append("category_ids", categoryId);
  }
  if (searchKeywordInput.value.trim()) {
    parameters.set("keyword", searchKeywordInput.value.trim());
  }
  if (searchAdjustmentOnlyInput.checked) {
    parameters.set("manual_adjustments_only", "true");
  }
  if (searchFlaggedOnlyInput.checked) {
    parameters.set("flagged_only", "true");
  }
  if (basisSelect?.value === "groups") parameters.set("entry_type", "offset_group");
  return `${isOffsetBasis() ? "/api/personal-cashflow/search" : "/api/transactions/search"}?${parameters.toString()}`;
}

function createStatusBadges(record) {
  const target = document.createElement("div");
  target.className = "status-badges";
  if (record.term_deposit_id) {
    const link = document.createElement('a'); link.href = `/term-deposits?deposit=${record.term_deposit_id}`;
    link.className = 'status-badge'; link.textContent = '定存记录'; target.append(link);
  }
  if(['expense','income'].includes(record.transaction_type)&&!(record.transaction_type==='income'&&record.income_allocations?.length)){
    const badge=document.createElement('span');badge.className='status-badge budget-badge';badge.textContent=(record.effective_budget_system_name||record.budget_system_name||'未归属预算')+(record.budget_inherited?' · 自动':'');target.append(badge);
  }
  if(record.transaction_type==='income'){
    for(const allocation of record.income_allocations||[])target.append(
      BudgetUI.node('span',`${allocation.budget_name}：${allocation.amount} ${allocation.budget_currency}${allocation.group_id?'（完整事项分配）':''}`,'status-badge budget-badge'));
  }
  if (record.is_manual_adjustment) {
    const badge = document.createElement("span");
    badge.className = "status-badge adjustment-badge";
    badge.textContent = "手工调整";
    target.append(badge);
  }
  if (record.is_investment_valuation) {
    const badge = document.createElement("span");
    badge.className = "status-badge investment-badge";
    badge.textContent = "投资估值";
    target.append(badge);
  }
  if (record.flag) {
    const badge = document.createElement("span");
    badge.className = "status-badge flag-badge";
    badge.title = record.flag.note;
    badge.textContent = FLAG_LABELS[record.flag.flag_type] || "待核实";
    target.append(badge);
  }
  if (record.offset_group_id) {
    const link = document.createElement('a');
    link.href = `/offsets?group=${record.offset_group_id}`;
    link.textContent = record.offset_group_title || `冲销事项 #${record.offset_group_id}`;
    target.append(link);
  }
  if (!target.childElementCount) {
    target.textContent = "-";
  }
  return target;
}

function createActionButtons(record) {
  const target = document.createElement("div");
  target.className = "row-actions";

  if (record.is_investment_valuation) {
    const valuationButton = document.createElement("button");
    valuationButton.className = "table-action";
    valuationButton.type = "button";
    valuationButton.textContent = "修改估值";
    valuationButton.addEventListener("click", () => {
      window.location.assign("/investments");
    });
    target.append(valuationButton);
    return target;
  }

  const editButton = document.createElement("button");
  editButton.className = "table-action";
  editButton.type = "button";
  editButton.textContent = record.is_manual_adjustment ? "确认用途" : "编辑";
  editButton.addEventListener("click", () => {
    openTransactionEditor(record.id).catch((error) => setStatus(detailStatus, error.message, "error"));
  });

  const deleteButton = document.createElement("button");
  deleteButton.className = "table-action danger-action";
  deleteButton.type = "button";
  deleteButton.textContent = "删除";
  deleteButton.addEventListener("click", () => {
    openDeleteDialog(record).catch((error) => setStatus(detailStatus, error.message, "error"));
  });

  const flagButton = document.createElement("button");
  flagButton.className = "table-action";
  flagButton.type = "button";
  flagButton.textContent = record.flag ? "解除标记" : "标记";
  flagButton.addEventListener("click", () => {
    if (record.flag) {
      clearTransactionFlag(record.id).catch((error) => setStatus(detailStatus, error.message, "error"));
    } else {
      openFlagDialog(record.id);
    }
  });
  target.append(editButton, deleteButton, flagButton);
  return target;
}

function renderAccountState(account) {
  state.detail.account = account;
  for (const id of ['open-adjustment-dialog','open-opening-balance-dialog']) {
    const button = document.getElementById(id);
    button.disabled = account.account_type === 'bank_term';
    button.title = button.disabled ? '请在定期存款页面维护本金及到账' : '';
  }
  document.querySelector("#account-name").textContent = account.name;
  document.querySelector("#account-meta").textContent =
    `${account.code} · ${account.institution || "未填写机构"} · ${account.account_class_label} · ${account.currency_code}`;
  document.querySelector("#account-status").textContent = account.is_active ? "启用中" : "已停用";

  const noteElement = document.querySelector("#account-summary-note");
  noteElement.hidden = !account.note;
  noteElement.textContent = account.note || "";

  const reconciliation = account.reconciliation || { label: "待对账" };
  const reconciliationValue = reconciliation.reconciliation_date
    ? `${reconciliation.label} · ${reconciliation.reconciliation_date} · 差异 ${reconciliation.difference} ${account.currency_code}`
    : reconciliation.label;
  const stateTarget = document.querySelector("#account-state");
  stateTarget.replaceChildren();
  const items = [
    { label: "当前账面余额", value: `${account.current_balance} ${account.currency_code}`, primary: true },
    { label: "期初余额", value: `${account.opening_balance} ${account.currency_code}` },
    { label: "期初日期", value: account.opening_balance_date },
    { label: "累计流入", value: `${account.total_inflow} ${account.currency_code}` },
    { label: "累计流出", value: `${account.total_outflow} ${account.currency_code}` },
    {
      label: "最近对账",
      value: reconciliationValue,
      alert: reconciliation.status === "difference",
      action: reconciliation.manual_adjustment_count > 0
        ? {
            label: `查看 / 修改当日调整（${reconciliation.manual_adjustment_count}）`,
            run: () => showReconciliationAdjustments(reconciliation.reconciliation_date),
          }
        : null,
    },
  ];
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "account-state-item";
    if (item.primary) {
      card.classList.add("is-primary");
    }
    if (item.alert) {
      card.classList.add("is-alert");
    }
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = item.label;
    value.textContent = item.value;
    card.append(label, value);
    if (item.action) {
      const button = document.createElement("button");
      button.className = "table-action account-state-action";
      button.type = "button";
      button.textContent = item.action.label;
      button.addEventListener("click", item.action.run);
      card.append(button);
    }
    stateTarget.append(card);
  }
}

function showReconciliationAdjustments(reconciliationDate) {
  detailStartDateInput.value = reconciliationDate;
  detailEndDateInput.value = reconciliationDate;
  detailTransactionTypeSelect.value = "adjustment";
  detailCategorySelect.value = "";
  detailKeywordInput.value = "";
  detailAdjustmentOnlyInput.checked = true;
  detailFlaggedOnlyInput.checked = false;
  refreshPickers(detailForm);
  loadDetail(true)
    .then(() => ledgerPanel.scrollIntoView({ behavior: "smooth", block: "start" }))
    .catch((error) => setStatus(detailStatus, error.message, "error"));
}

function purposeFor(record) {
  return [record.category_name, record.counterparty].filter(Boolean).join(" · ");
}

function appendCells(row, values) {
  for (const value of values) {
    const cell = document.createElement("td");
    if (value instanceof Node) {
      cell.append(value);
    } else {
      cell.textContent = value;
    }
    row.append(cell);
  }
}

function renderInvestmentRecords(records, account) {
  state.detail.investmentRecords = records;
  const body = document.querySelector("#account-investment-body");
  body.replaceChildren();
  for (const record of records) {
    const row = document.createElement("tr");
    const change = Number(record.generated_change) > 0 ? `+${record.generated_change}` : record.generated_change;
    appendCells(row, [record.valuation_date, `${record.total_value} ${account.currency_code}`,
      `${change} ${account.currency_code}`, record.note || ""]);
    body.append(row);
  }
}

function renderAccountLedger(records, account) {
  const body = document.querySelector("#ledger-body");
  body.replaceChildren();
  if (!records.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="11" class="empty-cell">没有符合筛选条件的流水</td>';
    body.append(row);
    return;
  }
  for (const record of records) {
    const row = document.createElement("tr");
    appendCells(row, [
      record.transaction_date,
      record.transaction_type_label,
      record.direction,
      record.counter_account_name,
      record.inflow_amount ? `${record.inflow_amount} ${account.currency_code}` : "",
      record.outflow_amount ? `${record.outflow_amount} ${account.currency_code}` : "",
      `${record.balance_after} ${account.currency_code}`,
      purposeFor(record),
      record.note || "",
      createStatusBadges(record),
      createActionButtons(record),
    ]);
    body.append(row);
  }
}

function updateAccountPagination(pagination) {
  state.detail.total = pagination.total;
  const start = pagination.total === 0 ? 0 : pagination.offset + 1;
  const end = Math.min(pagination.offset + pagination.limit, pagination.total);
  document.querySelector("#ledger-summary").textContent =
    `共 ${pagination.total} 笔，当前 ${start}-${end} 笔`;
  document.querySelector("#page-label").textContent =
    `第 ${Math.floor(pagination.offset / pagination.limit) + 1} 页`;
  previousDetailPageButton.disabled = pagination.offset === 0;
  nextDetailPageButton.disabled = pagination.offset + pagination.limit >= pagination.total;
}

async function loadDetail(resetPage = false) {
  detailDates.sync();
  const serial = ++detailRequestId;
  if (resetPage) {
    state.detail.offset = 0;
  }
  if (!detailAccountSelect.value) {
    state.detail.loaded = false;
    updateViewVisibility();
    return;
  }
  setStatus(detailStatus, "");
  let data;
  try { data = await request(buildDetailPath()); }
  catch (error) { if (serial === detailRequestId) throw error; else return; }
  if (serial !== detailRequestId) return;
  renderAccountState(data.account);
  renderInvestmentRecords(data.investment_records || [], data.account);
  renderAccountLedger(data.transactions, data.account);
  updateAccountPagination(data.pagination);
  state.detail.loaded = true;
  updateViewVisibility();
}

function formatSearchAmount(amount, currencyCode) {
  return amount ? `${amount} ${currencyCode}` : "";
}

function renderSearchResults(records) {
  const body = document.querySelector("#search-results-body");
  body.replaceChildren();
  if (!records.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="9" class="empty-cell">没有符合筛选条件的流水</td>';
    body.append(row);
    return;
  }
  for (const record of records) {
    const row = document.createElement("tr");
    appendCells(row, [
      record.transaction_date,
      record.transaction_type_label,
      `${record.source_account_name} -> ${record.destination_account_name}`,
      formatSearchAmount(record.outflow_amount, record.source_currency_code),
      formatSearchAmount(record.inflow_amount, record.destination_currency_code),
      purposeFor(record),
      record.note || "",
      createStatusBadges(record),
      createActionButtons(record),
    ]);
    body.append(row);
  }
}

function updateSearchPagination(pagination) {
  state.search.total = pagination.total;
  const start = pagination.total === 0 ? 0 : pagination.offset + 1;
  const end = Math.min(pagination.offset + pagination.limit, pagination.total);
  document.querySelector("#search-summary").textContent =
    `共 ${pagination.total} 笔，当前 ${start}-${end} 笔`;
  document.querySelector("#search-page-label").textContent =
    `第 ${Math.floor(pagination.offset / pagination.limit) + 1} 页`;
  previousSearchPageButton.disabled = pagination.offset === 0;
  nextSearchPageButton.disabled = pagination.offset + pagination.limit >= pagination.total;
}

function formatSummaryAmount(amount, currencyCode, includeSign = false) {
  const numericAmount = Number(amount);
  const sign = includeSign && numericAmount > 0 ? "+" : "";
  return `${sign}${amount} ${currencyCode}`;
}

function createSummaryFlowTable(records, currencyLabel, includeCoverage = false) {
  const wrapper = document.createElement("div");
  wrapper.className = "table-wrap";
  const table = document.createElement("table");
  table.className = "search-summary-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  const headers = [currencyLabel, "流入", "流出", "净流动"];
  if (includeCoverage) {
    headers.push("汇率覆盖");
  }
  for (const header of headers) {
    const cell = document.createElement("th");
    cell.textContent = header;
    headRow.append(cell);
  }
  head.append(headRow);

  const body = document.createElement("tbody");
  for (const record of records) {
    const values = [
      record.currency_code,
      formatSummaryAmount(record.inflow_amount, record.currency_code),
      formatSummaryAmount(record.outflow_amount, record.currency_code),
      formatSummaryAmount(record.net_amount, record.currency_code, true),
    ];
    if (includeCoverage) {
      const coverage = record.rate_coverage;
      const coverageParts = [
        `直接 ${coverage.direct_base_currency_leg_count} 腿`,
        `当日 ${coverage.exact_rate_leg_count} 腿`,
        `邻近 ${coverage.nearest_rate_leg_count} 腿`,
      ];
      if (coverage.excluded_leg_count) {
        const missing = coverage.missing_currencies.length
          ? `（${coverage.missing_currencies.join("、")}）`
          : "";
        coverageParts.push(`未纳入 ${coverage.excluded_leg_count} 腿${missing}`);
      }
      values.push(coverageParts.join("；"));
    }
    const row = document.createElement("tr");
    appendCells(row, values);
    if (isOffsetBasis()) {
      const magnitude = Math.max(1, ...records.map(item => Math.abs(Number(item.amount))));
      const track = document.createElement('div'); track.className='signed-track';
      const bar = document.createElement('span'); const number=Number(record.amount);
      bar.className=number < 0 ? 'signed-bar negative' : 'signed-bar positive';
      bar.style.width=`${Math.abs(number)/magnitude*50}%`; track.append(bar);row.children[1].append(track);
    }
    body.append(row);
  }
  table.append(head, body);
  wrapper.append(table);
  return wrapper;
}

function createSearchSummarySection(title, records, currencyLabel, includeCoverage = false) {
  const section = document.createElement("section");
  section.className = "search-summary-block";
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading, createSummaryFlowTable(records, currencyLabel, includeCoverage));
  return section;
}

function renderTransactionSearchSummary(summary) {
  transactionSearchSummaryContent.replaceChildren();
  transactionSearchSummaryMeta.textContent = "";
  if(summary.budget_income_allocations?.length||selectedSearchBudgets().some(id=>Number(id)>0)){
    const section=BudgetUI.node('section','','budget-allocation-summary');
    section.append(BudgetUI.node('h3',selectedSearchBudgets().length?'分配给所选预算的收入（已核实部分）':'已分配到预算的收入（已核实部分）'));
    for(const row of summary.budget_income_allocations)section.append(BudgetUI.node('strong',`${row.amount} ${row.currency_code} `));
    if(!summary.budget_income_allocations.length)section.append(BudgetUI.node('p','匹配来源没有分配给所选预算的收入，分配合计为 0。'));
    transactionSearchSummaryContent.append(section);
  }
  IncomeAllocations.renderTrace(transactionSearchSummaryContent,summary);

  if (!summary.available) {
    const notice = document.createElement("div");
    notice.className = "empty-notice";
    notice.textContent = summary.reason;
    transactionSearchSummaryContent.append(notice);
    return;
  }

  const scopeLabel = summary.scope.mode === "selected_account"
    ? searchAccountSelect.selectedOptions[0]?.textContent || "所选账户"
    : "全部账户";
  transactionSearchSummaryMeta.textContent =
    `${searchDates.label()} · ${summary.transaction_count} 笔 · ${scopeLabel}`;

  if (summary.transaction_count === 0) {
    const notice = document.createElement("div");
    notice.className = "empty-notice";
    notice.textContent = "没有符合筛选条件的流水。";
    transactionSearchSummaryContent.append(notice);
    return;
  }

  const combined = document.createElement("section");
  combined.className = "search-summary-block";
  const heading = document.createElement("h3");
  heading.textContent = "原始收付合计";
  combined.append(
    heading,
    createSearchSummarySection("原币种明细", summary.raw_totals_by_currency, "币种"),
  );
  const converted = document.createElement('details');
  const convertedLabel = document.createElement('summary'); convertedLabel.textContent = '查看匹配资金腿的折算参考（非原币账单）';
  converted.append(convertedLabel, createSearchSummarySection('全部命中资金腿折算', summary.valuations, '折算币种', true));
  combined.append(converted);
  transactionSearchSummaryContent.append(combined);

  if (summary.by_type.length <= 1) {
    return;
  }

  const typeBreakdown = document.createElement("details");
  typeBreakdown.className = "search-summary-block search-summary-breakdown";
  const typeHeading = document.createElement("summary");
  typeHeading.textContent = `按流水类型查看（${summary.by_type.map(
    (item) => `${item.transaction_type_label} ${item.transaction_count} 笔`,
  ).join("、")}）`;
  typeBreakdown.append(typeHeading);
  const types = document.createElement("div");
  types.className = "search-summary-type-list";
  for (const typeSummary of summary.by_type) {
    const type = document.createElement("section");
    type.className = "search-summary-type";
    const typeHeader = document.createElement("div");
    typeHeader.className = "search-summary-type-heading";
    const name = document.createElement("h4");
    const count = document.createElement("span");
    name.textContent = typeSummary.transaction_type_label;
    count.textContent = `${typeSummary.transaction_count} 笔`;
    typeHeader.append(name, count);
    type.append(typeHeader);
    if (typeSummary.transaction_count === 0) {
      const empty = document.createElement("p");
      empty.className = "analysis-summary";
      empty.textContent = "没有符合其他筛选条件的流水。";
      type.append(empty);
    } else {
      const tables = document.createElement("div");
      tables.className = "search-summary-type-tables";
      tables.append(
        createSearchSummarySection(
          "原币种明细",
          typeSummary.raw_totals_by_currency,
          "币种",
        ),
        createSearchSummarySection(
          "统一计价合计",
          typeSummary.valuations,
          "计价货币",
        ),
      );
      type.append(tables);
    }
    types.append(type);
  }
  typeBreakdown.append(types);
  transactionSearchSummaryContent.append(typeBreakdown);
}

function analysisConstraintMessage() {
  const selectedTypes = selectedSearchTypes();
  if (!searchStartDateInput.value || !searchEndDateInput.value) {
    return "请选择完整的开始日期和结束日期后生成流水详情。";
  }
  if (searchStartDateInput.value > searchEndDateInput.value) {
    return "开始日期不能晚于结束日期。";
  }
  const start = new Date(`${searchStartDateInput.value}T00:00:00`);
  const end = new Date(`${searchEndDateInput.value}T00:00:00`);
  const monthCount =
    (end.getFullYear() - start.getFullYear()) * 12
    + end.getMonth()
    - start.getMonth()
    + 1;
  if (monthCount > 12) {
    return "流水详情最多统计 12 个自然月，请拆分日期范围。";
  }
  if (selectedTypes.length !== 1) {
    return "请选择唯一一种流水类型后生成流水详情。";
  }
  return "";
}

function buildAnalysisPath() {
  const parameters = new URLSearchParams({
    start_date: searchStartDateInput.value,
    end_date: searchEndDateInput.value,
    transaction_type: selectedSearchTypes()[0],
  });
  if (searchCurrency?.value) parameters.set('currency_code', searchCurrency.value);
  for (const budgetId of selectedSearchBudgets()) {
    parameters.append('budget_system_ids', budgetId);
  }
  if (searchAccountSelect.value) {
    parameters.set(isOffsetBasis() ? "involved_account_id" : "account_id", searchAccountSelect.value);
  }
  for (const categoryId of selectedSearchCategories()) {
    parameters.append("category_ids", categoryId);
  }
  if (searchKeywordInput.value.trim()) {
    parameters.set("keyword", searchKeywordInput.value.trim());
  }
  if (searchAdjustmentOnlyInput.checked) {
    parameters.set("manual_adjustments_only", "true");
  }
  if (searchFlaggedOnlyInput.checked) {
    parameters.set("flagged_only", "true");
  }
  if (basisSelect?.value === "groups") parameters.set("entry_type", "offset_group");
  return `${isOffsetBasis() ? "/api/personal-cashflow/analysis" : "/api/transactions/analysis"}?${parameters.toString()}`;
}

function createAnalysisBlock(title) {
  const block = document.createElement("section");
  block.className = "analysis-block";
  const heading = document.createElement("h3");
  heading.textContent = title;
  block.append(heading);
  return block;
}

function createAnalysisMetric(labelText, valueText, primary = false) {
  const item = document.createElement("article");
  item.className = "account-state-item";
  if (primary) {
    item.classList.add("is-primary");
  }
  const label = document.createElement("span");
  const value = document.createElement("strong");
  label.textContent = labelText;
  value.textContent = valueText;
  item.append(label, value);
  return item;
}

function createAnalysisSummary(text) {
  const summary = document.createElement("p");
  summary.className = "analysis-summary";
  summary.textContent = text;
  return summary;
}

function monthRange(startDate, endDate) {
  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  const months = [];
  let year = start.getFullYear();
  let month = start.getMonth();
  while (year < end.getFullYear() || (year === end.getFullYear() && month <= end.getMonth())) {
    months.push({ year, month });
    month += 1;
    if (month === 12) {
      year += 1;
      month = 0;
    }
  }
  return months;
}

function isoDate(year, month, day) {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function renderAnalysisCalendar(data) {
  const block = createAnalysisBlock("按日历查看");
  block.append(createAnalysisSummary(
    `按${data.main_amount_label}汇总，${data.amount_scope === 'native' ? `仅计 ${data.currency_code} 原币` : '全部命中金额折算为 HKD'}；点击日期查看${data.basis === "offset" ? "归属当天的统计记录，再展开原始流水" : "当天逐笔流水"}。`,
  ));
  const dayTotals = new Map(
    data.daily_totals.map((item) => [item.transaction_date, item]),
  );
  const months = document.createElement("div");
  months.className = "analysis-calendar-months";
  for (const month of monthRange(data.filters.start_date, data.filters.end_date)) {
    const monthElement = document.createElement("section");
    monthElement.className = "analysis-calendar-month";
    const heading = document.createElement("h4");
    heading.textContent = `${month.year} 年 ${month.month + 1} 月`;
    const grid = document.createElement("div");
    grid.className = "analysis-calendar-grid";
    for (const label of ["一", "二", "三", "四", "五", "六", "日"]) {
      const weekday = document.createElement("div");
      weekday.className = "analysis-calendar-weekday";
      weekday.textContent = label;
      grid.append(weekday);
    }

    const firstWeekday = (new Date(month.year, month.month, 1).getDay() + 6) % 7;
    const daysInMonth = new Date(month.year, month.month + 1, 0).getDate();
    for (let index = 0; index < firstWeekday; index += 1) {
      const blank = document.createElement("div");
      blank.className = "analysis-calendar-day is-outside-range";
      blank.setAttribute("aria-hidden", "true");
      grid.append(blank);
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      const dateValue = isoDate(month.year, month.month, day);
      const daily = dayTotals.get(dateValue);
      const inRange =
        dateValue >= data.filters.start_date && dateValue <= data.filters.end_date;
      const cell = document.createElement(daily && inRange ? "button" : "div");
      cell.className = "analysis-calendar-day";
      if (!inRange) {
        cell.classList.add("is-outside-range");
      }
      const dateLabel = document.createElement("span");
      dateLabel.textContent = String(day);
      cell.append(dateLabel);
      if (daily && inRange) {
        cell.type = "button";
        cell.title = `${dateValue}：${daily.amount} ${analysisCurrency}，${daily.count} 笔`;
        const amount = document.createElement("strong");
        amount.textContent = `${daily.amount} ${analysisCurrency}`;
        const count = document.createElement("small");
        count.textContent = `${daily.count} ${data.basis === "offset" ? "条" : "笔"}`;
        cell.append(amount, count);
        cell.addEventListener("click", () => {
          searchStartDateInput.value = dateValue;
          searchEndDateInput.value = dateValue;
          loadSearch(true, true)
            .then(() => searchResultsPanel.scrollIntoView({ behavior: "smooth", block: "start" }))
            .catch((error) => setStatus(searchStatus, error.message, "error"));
        });
      }
      grid.append(cell);
    }
    const trailingDays = (firstWeekday + daysInMonth) % 7;
    if (trailingDays) {
      for (let index = trailingDays; index < 7; index += 1) {
        const blank = document.createElement("div");
        blank.className = "analysis-calendar-day is-outside-range";
        blank.setAttribute("aria-hidden", "true");
        grid.append(blank);
      }
    }
    monthElement.append(heading, grid);
    months.append(monthElement);
  }
  block.append(months);
  return block;
}

function renderHistogram(data) {
  const block = createAnalysisBlock("每日金额分布");
  block.append(createAnalysisSummary(
    `单位：${analysisCurrency}；按每日${data.main_amount_label}合计自动分箱，区间边界为整数。`,
  ));
  const histogram = document.createElement("div");
  histogram.className = "analysis-histogram";
  const highestCount = Math.max(...data.histogram.map((item) => item.count), 1);
  for (const bin of data.histogram) {
    const item = document.createElement("div");
    item.className = "analysis-histogram-bin";
    item.title = `${bin.start}-${bin.end} ${analysisCurrency}：${bin.count} 天`;
    const count = document.createElement("strong");
    count.textContent = `${bin.count} 天`;
    const barArea = document.createElement("div");
    barArea.className = "analysis-histogram-bar-area";
    const bar = document.createElement("div");
    bar.className = "analysis-histogram-bar";
    bar.style.height = `${Math.max(6, (bin.count / highestCount) * 100)}%`;
    const label = document.createElement("span");
    label.textContent = `${bin.start}-${bin.end}`;
    barArea.append(bar);
    item.append(count, barArea, label);
    histogram.append(item);
  }
  block.append(histogram);
  return block;
}

function renderWeekdayDistribution(data) {
  const block = createAnalysisBlock("星期分布");
  block.append(createAnalysisSummary(`按每天金额归集，金额单位为 ${analysisCurrency}。`));
  const grid = document.createElement("div");
  grid.className = "analysis-weekday-grid";
  for (const item of data.weekday_distribution) {
    const weekday = document.createElement("div");
    weekday.className = "analysis-weekday-item";
    const label = document.createElement("span");
    const amount = document.createElement("strong");
    const count = document.createElement("span");
    label.textContent = item.label;
    amount.textContent = `${item.amount} ${analysisCurrency}`;
    count.textContent = `${item.active_day_count} 天`;
    weekday.append(label, amount, count);
    grid.append(weekday);
  }
  block.append(grid);
  return block;
}

function createDistributionTable(title, records, amountLabel, includeAmountShare = false) {
  if (!records.length || (!includeAmountShare && records.length <= 1)) {
    return null;
  }
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = title;
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  const headers = includeAmountShare
    ? ["项目", amountLabel, "金额占比", "笔数"]
    : ["项目", amountLabel, "笔数"];
  for (const value of headers) {
    const cell = document.createElement("th");
    cell.textContent = value;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const record of records) {
    const row = document.createElement("tr");
    const values = [record.name, `${record.amount} ${analysisCurrency}`];
    if (includeAmountShare) {
      values.push(record.amount_share === undefined ? "—" : `${record.amount_share}%`);
    }
    values.push(`${record.count}`);
    appendCells(row, values);
    if (isOffsetBasis()) {
      const magnitude = Math.max(1, ...records.map(item => Math.abs(Number(item.amount))));
      const track = document.createElement('div'); track.className='signed-track';
      const bar = document.createElement('span'); const number=Number(record.amount);
      bar.className=number < 0 ? 'signed-bar negative' : 'signed-bar positive';
      bar.style.width=`${Math.abs(number)/magnitude*50}%`; track.append(bar);row.children[1].append(track);
    }
    body.append(row);
  }
  table.append(head, body);
  section.append(heading, table);
  return section;
}

function renderTransactionAnalysis(data) {
  analysisCurrency = data.currency_code || 'HKD';
  analysisContent.replaceChildren();
  analysisConstraint.hidden = true;
  if(selectedSearchBudgets().some(id=>Number(id)>0)&&data.allocation_warnings?.length)analysisContent.append(createAnalysisSummary('收入来源存在超配或无法核实的分配，未计入本图；请按上方来源提示核对，当前仅展示可核实部分。'));
  if(selectedSearchBudgets().length && data.transaction_type==='income'){
    analysisContent.append(createAnalysisSummary(data.basis==='offset'
      ? '已分配收入按所选预算的份额计算，展开记录仍可查看原始到账。'
      : '当前图表使用原始到账金额；分给所选预算的金额见上方“收入分配”汇总，或切换冲销口径查看份额统计。'));
  }

  const coverage = data.rate_coverage;
  const coverageParts = [
    data.basis === "offset" ? `符合条件 ${data.entry_count} 条统计记录，涉及 ${data.transaction_count} 笔原始流水` : `符合条件 ${data.matched_transaction_count} 笔`,
    `原币 ${analysisCurrency} ${coverage.direct_hkd_count} ${data.basis === "offset" ? "条记录" : "笔"}`,
    `当天汇率换算 ${coverage.exact_rate_count} ${data.basis === "offset" ? "条记录" : "笔"}`,
    `${data.basis === "offset" ? "含邻近汇率" : "邻近日期汇率换算"} ${coverage.nearest_rate_count} ${data.basis === "offset" ? "条记录" : "笔"}`,
  ];
  if (coverage.excluded_count) {
    const missing = coverage.missing_currencies.length
      ? `（${coverage.missing_currencies.join("、")}）`
      : "";
    coverageParts.push(`未纳入 ${coverage.excluded_count} ${data.basis === "offset" ? "条记录" : "笔"}${missing}`);
  }

  if (!data.statistics) {
    const empty = document.createElement("div");
    empty.className = "empty-notice";
    empty.textContent =
      `${coverageParts.join("；")}。没有可完整统计的 ${analysisCurrency} 金额，因此无法生成日历和统计。`;
    analysisContent.append(empty);
    analysisContent.hidden = false;
    return;
  }

  const statistics = data.statistics;
  const statisticsBlock = createAnalysisBlock("核心统计");
  statisticsBlock.append(createAnalysisSummary(
    `${data.transaction_type_label} · ${data.main_amount_label} · ${coverageParts.join("；")}。`,
  ));
  const metricGrid = document.createElement("div");
  metricGrid.className = "account-state-grid";
  const metrics = [
    { label: "总金额", value: `${statistics.total_amount} ${analysisCurrency}`, primary: true },
    { label: data.basis === "offset" ? "纳入统计记录" : "纳入统计笔数", value: `${statistics.converted_transaction_count} ${data.basis === "offset" ? "条" : "笔"}` },
    { label: "有流水天数", value: `${statistics.active_day_count} / ${statistics.range_day_count} 天` },
    { label: "范围日均", value: `${statistics.calendar_day_mean} ${analysisCurrency}` },
    { label: "有流水日均", value: `${statistics.active_day_mean} ${analysisCurrency}` },
    { label: "每日中位数", value: `${statistics.median_daily_amount} ${analysisCurrency}` },
    { label: "每日最小 / 最大", value: `${statistics.minimum_daily_amount} / ${statistics.maximum_daily_amount} ${analysisCurrency}` },
    { label: "每日总体标准差", value: `${statistics.population_daily_standard_deviation} ${analysisCurrency}` },
    { label: "每日总体方差", value: `${statistics.population_daily_variance} ${analysisCurrency}^2` },
    { label: "最高金额日", value: `${statistics.maximum_day_date} · ${statistics.maximum_day_amount} ${analysisCurrency}` },
  ];
  for (const metric of metrics) {
    metricGrid.append(createAnalysisMetric(metric.label, metric.value, metric.primary));
  }
  statisticsBlock.append(metricGrid);
  analysisContent.append(statisticsBlock, renderAnalysisCalendar(data), renderHistogram(data), renderWeekdayDistribution(data));

  if (data.basis === 'offset' && data.category_distribution.some(item => item.amount_share === undefined)) {
    analysisContent.append(createAnalysisSummary('存在负分类净额、零合计或换算不完整时，分类仅显示金额，不计算占比。'));
  }
  const distributions = document.createElement("section");
  distributions.className = "analysis-block";
  const distributionGrid = document.createElement("div");
  distributionGrid.className = "analysis-distribution-grid";
  const distributionSections = [
    createDistributionTable(
      "分类分布",
      data.category_distribution,
      "金额",
      true,
    ),
    createDistributionTable(`${data.account_label}分布`, data.account_distribution, "金额"),
    createDistributionTable("调整方向分布", data.direction_distribution, "金额"),
  ].filter(Boolean);
  for (const section of distributionSections) {
    distributionGrid.append(section);
  }
  if (distributionSections.length) {
    distributions.append(distributionGrid);
    analysisContent.append(distributions);
  }
  analysisContent.hidden = false;
}

async function loadTransactionAnalysis(serial = searchRequestId) {
  analysisContent.replaceChildren();
  analysisContent.hidden = true;
  setStatus(analysisStatus, "");
  const constraint = analysisConstraintMessage();
  if (constraint) {
    analysisConstraint.textContent = constraint;
    analysisConstraint.hidden = false;
    return;
  }

  analysisConstraint.hidden = true;
  try {
    const data = await request(buildAnalysisPath());
    if (serial !== searchRequestId) return;
    renderTransactionAnalysis(data);
    const unit = data.amount_scope === 'native' ? `${data.currency_code} 原币` : '全部命中金额折算 HKD';
    setStatus(analysisStatus, `已按 ${unit} 统计${data.allocation_warnings?.length ? ' · 收入分配待核实' : data.budget_warnings?.length ? ' · 未包含跨预算待归属事项' : ''}`, 'success');
  } catch (error) {
    if (serial !== searchRequestId) return;
    analysisConstraint.textContent = error.message;
    analysisConstraint.hidden = false;
    setStatus(analysisStatus, "", "");
  }
}

async function loadSearch(resetPage = false, openResults = false) {
  searchDates.sync();
  if (resetPage) {
    state.search.offset = 0;
  }
  setStatus(searchStatus, "");
  const serial = ++searchRequestId;
  document.querySelector('#search-results-body').replaceChildren();
  transactionSearchSummaryContent.replaceChildren();
  analysisContent.replaceChildren();
  if (isOffsetBasis() && !window.OffsetUI) throw new Error('冲销界面未加载完整，请刷新页面；原始流水查询仍可使用。');
  if (isOffsetBasis() && (searchAdjustmentOnlyInput.checked || selectedSearchTypes().some(type => !['expense','income'].includes(type)))) {
    throw new Error('冲销后收支只支持收入和支出，请取消转账、换汇、调整等类型及“仅手工调整”后查询。');
  }
  let data;
  try { data = await request(buildSearchPath()); }
  catch (error) { if (serial === searchRequestId) throw error; else return; }
  if (serial !== searchRequestId) return;
  renderBudgetWarnings(data);
  document.querySelector('#search-results-body').closest('table').querySelector('thead').hidden = isOffsetBasis();
  if (isOffsetBasis()) {
    const body = document.querySelector('#search-results-body');
    for (const entry of data.entries) {
      const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan=9;
      cell.append(window.OffsetUI.entryCell(entry, id => openTransactionEditor(id).catch(error => setStatus(searchStatus,error.message,'error'))));
      row.append(cell); body.append(row);
    }
    if (!data.entries.length) {const row=document.createElement('tr'); const cell=document.createElement('td'); cell.colSpan=9;cell.textContent='没有符合条件的统计记录';row.append(cell);body.append(row);}
  } else renderSearchResults(data.transactions);
  updateSearchPagination(data.pagination);
  if (isOffsetBasis()) {
    window.OffsetUI.summary(transactionSearchSummaryContent, data.summary);
    transactionSearchSummaryMeta.textContent = `${searchDates.label()} · 冲销后收支 · 统计日期归属`+(data.budget_warnings?.length?' · 仅已归属事项':'');
    document.querySelector('#search-summary').textContent = `共 ${data.pagination.total} 条统计记录`;
  } else renderTransactionSearchSummary(data.summary);
  if (resetPage && !openResults) {
    searchResultsDetails.open = false;
  } else if (openResults) {
    searchResultsDetails.open = true;
  }
  state.search.loaded = true;
  updateViewVisibility();
  await loadTransactionAnalysis(serial);
}

function renderSearchCategoryOptions(categories) {
  searchCategoryOptions.replaceChildren();
  const common = categories
    .map((category, index) => ({ category, index }))
    .filter((item) => item.category.activity?.record_count > 0)
    .sort((left, right) => (
      right.category.activity.record_count - left.category.activity.record_count
      || (right.category.activity.last_transaction_date || "").localeCompare(
        left.category.activity.last_transaction_date || "",
      )
      || left.index - right.index
    ))
    .slice(0, 6)
    .map((item) => item.category);
  const commonIds = new Set(common.map((category) => category.id));
  const other = categories.filter((category) => !commonIds.has(category.id));

  function appendOptions(records, target) {
    for (const category of records) {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      const text = document.createElement("span");
      checkbox.type = "checkbox";
      checkbox.value = category.id;
      checkbox.dataset.kind = category.kind;
      checkbox.addEventListener("change", updateCategorySummary);
      text.textContent = formatCategoryName(category);
      label.append(checkbox, text);
      target.append(label);
    }
  }

  if (common.length) {
    const heading = document.createElement("p");
    heading.className = "filter-option-group-label";
    heading.textContent = "常用分类";
    searchCategoryOptions.append(heading);
    appendOptions(common, searchCategoryOptions);
  }
  if (other.length) {
    const otherDetails = document.createElement("details");
    otherDetails.className = "filter-option-other";
    const summary = document.createElement("summary");
    summary.textContent = `其他分类（${other.length}）`;
    const options = document.createElement("div");
    options.className = "filter-option-list";
    appendOptions(other, options);
    otherDetails.append(summary, options);
    searchCategoryOptions.append(otherDetails);
  }
  updateCategorySummary();
}

function setEditorCategoryOptions(kind, selectedId) {
  const categories = kind ? state.categories.filter((category) => category.kind === kind) : [];
  setSelectOptions(
    editorCategorySelect,
    categories,
    kind ? "选择分类" : "不需要分类",
    formatCategoryName,
    Boolean(kind),
  );
  editorCategorySelect.disabled = !kind;
  editorCategorySelect.value = selectedId ? String(selectedId) : "";
  setPickerRequired(editorCategorySelect, Boolean(kind));
  refreshPickers(editorDialog);
}

function updateEditorFieldState() {
  const type = editorTypeSelect.value;
  const isIncome = type === "income";
  const isExpense = type === "expense";
  const isAdjustment = type === "adjustment";
  const isMovement = !isIncome && !isExpense && !isAdjustment;
  if(editorBudgetSelect)editorBudgetSelect.disabled=!budgetConfig||(!isIncome&&!isExpense);
  if(editorBudgetSelect?.options[0])editorBudgetSelect.options[0].textContent=isExpense?`${budgetConfig?.systems.find(s=>s.id===budgetConfig.daily_expense)?.name||'日常预算'}（自动）`:'未归属';

  editorSourceSelect.disabled = isIncome;
  editorDestinationSelect.disabled = isExpense;
  editorOutflowInput.disabled = isIncome;
  editorInflowInput.disabled = isExpense;
  editorOutflowInput.required = isExpense || isMovement;
  editorInflowInput.required = isIncome || isMovement;
  setPickerRequired(editorSourceSelect, isExpense || isMovement);
  setPickerRequired(editorDestinationSelect, isIncome || isMovement);
  setEditorCategoryOptions(isIncome ? "income" : isExpense ? "expense" : null, editorCategorySelect.value);

  if (isIncome) {
    editorSourceSelect.value = "";
    editorOutflowInput.value = "";
  }
  if (isExpense) {
    editorDestinationSelect.value = "";
    editorInflowInput.value = "";
  }
  refreshPickers(editorDialog);
}

async function openTransactionEditor(transactionId) {
  const transaction = await request(`/api/transactions/${transactionId}`);
  if (transaction.term_deposit_id) {
    window.location.href = `/term-deposits?deposit=${transaction.term_deposit_id}`;
    return;
  }
  editorForm.reset();
  setStatus(editorStatus, "");
  editorTransactionId.value = transaction.id;
  editorDateInput.value = transaction.transaction_date;
  editorTypeSelect.value = transaction.transaction_type;
  editorSourceSelect.value = transaction.source_account_id ? String(transaction.source_account_id) : "";
  editorDestinationSelect.value = transaction.destination_account_id
    ? String(transaction.destination_account_id)
    : "";
  editorOutflowInput.value = transaction.outflow_amount || "";
  editorInflowInput.value = transaction.inflow_amount || "";
  editorCounterpartyInput.value = transaction.counterparty || "";
  editorNoteInput.value = transaction.note || "";
  const isManualAdjustment = transaction.transaction_type === "adjustment";
  editorTitle.textContent = isManualAdjustment ? "确认对账调整用途" : "修改流水";
  editorAdjustmentGuidance.hidden = !isManualAdjustment;
  if (isManualAdjustment) {
    const suggestedType = transaction.inflow_amount ? "income" : "expense";
    editorClassifyAdjustmentButton.dataset.transactionType = suggestedType;
    editorClassifyAdjustmentButton.textContent = suggestedType === "income"
      ? "改为普通入账"
      : "改为普通支出";
  }
  if(editorBudgetSelect&&budgetConfig){
    setSelectOptions(editorBudgetSelect,budgetConfig.systems.filter(s=>s.is_active||s.id===transaction.budget_system_id),'未归属',s=>s.name+(s.is_active?'':'（停用）'));
    editorBudgetSelect.value=transaction.budget_system_id?String(transaction.budget_system_id):'';
  }
  setEditorCategoryOptions(
    transaction.transaction_type === "income"
      ? "income"
      : transaction.transaction_type === "expense"
        ? "expense"
        : null,
    transaction.category_id,
  );
  updateEditorFieldState();
  document.getElementById('editor-income-allocation-action')?.remove();
  if(transaction.offset_group_id){
    const host=BudgetUI.node('div','','full-width');host.id='editor-income-allocation-action';
    host.append(BudgetUI.node('p','本笔已归入冲销事项。预算按整件事的净额处理，请在事项中分配净收入或设置消费 / 净亏损承担预算。','subtle-note'));
    const link=BudgetUI.node('a','打开事项预算');link.href=`/offsets?group=${transaction.offset_group_id}`;link.target='_blank';link.rel='noopener';host.append(link);editorForm.append(host);
  }else if(transaction.transaction_type==='income'){
    const host=BudgetUI.node('div','','full-width');host.id='editor-income-allocation-action';
    host.append(BudgetUI.node('p','此处修改真实到账。预算金额在收入分配中维护；已有分配不会因修改标签而搬移。','subtle-note'));
    host.append(BudgetUI.button('查看 / 修改收入预算分配',()=>IncomeAllocations.open(
      'transaction',transaction.id,()=>loadSearch())));
    editorForm.append(host);
  }
  refreshPickers(editorDialog);
  openDialog(editorDialog);
}

function buildEditorPayload() {
  return {
    ...(editorBudgetSelect&&budgetConfig?{budget_system_id:editorBudgetSelect.disabled?null:Number(editorBudgetSelect.value)||null}:{}),
    transaction_date: editorDateInput.value,
    transaction_type: editorTypeSelect.value,
    source_account_id: editorSourceSelect.value ? Number(editorSourceSelect.value) : null,
    destination_account_id: editorDestinationSelect.value
      ? Number(editorDestinationSelect.value)
      : null,
    outflow_amount: editorOutflowInput.value || null,
    inflow_amount: editorInflowInput.value || null,
    category_id: editorCategorySelect.value ? Number(editorCategorySelect.value) : null,
    counterparty: editorCounterpartyInput.value.trim() || null,
    note: editorNoteInput.value.trim() || null,
    correction_reason: editorReasonInput.value.trim() || null,
  };
}

async function openDeleteDialog(record) {
  const transaction = await request(`/api/transactions/${record.id}`);
  deleteForm.reset();
  setStatus(deleteStatus, "");
  deleteTransactionId.value = transaction.id;
  document.querySelector("#delete-transaction-summary").textContent =
    `${transaction.transaction_date} · ${transaction.transaction_type} · ${transaction.outflow_amount || transaction.inflow_amount || ""}`;
  openDialog(deleteDialog);
}

function openFlagDialog(transactionId) {
  flagForm.reset();
  setStatus(flagStatus, "");
  flagTransactionId.value = transactionId;
  openDialog(flagDialog);
}

async function clearTransactionFlag(transactionId) {
  if (!window.confirm("解除该笔流水的待核实标记？")) {
    return;
  }
  await request(`/api/transactions/${transactionId}/flag`, { method: "DELETE" });
  await refreshAfterChange();
}

async function openAdjustmentDialog() {
  const account = state.detail.account;
  if (!account) {
    return;
  }
  adjustmentForm.reset();
  setStatus(adjustmentStatus, "");
  adjustmentAccountId.value = account.id;
  adjustmentDateInput.value = localDate();
  adjustmentActualInput.value = account.current_balance;
  adjustmentReasonInput.value = "";
  adjustmentNoteInput.value = "";
  document.querySelector("#adjustment-account-summary").textContent =
    `${account.name} · 当前账面余额 ${account.current_balance} ${account.currency_code}`;
  await loadAdjustmentPreview();
  openDialog(adjustmentDialog);
}

async function loadAdjustmentPreview() {
  const accountId = adjustmentAccountId.value;
  const onDate = adjustmentDateInput.value;
  if (!accountId || !onDate) {
    return;
  }
  const data = await request(`/api/accounts/${accountId}/balance-at?on_date=${encodeURIComponent(onDate)}`);
  const actual = Number(adjustmentActualInput.value);
  const difference = Number.isFinite(actual)
    ? `${(actual - Number(data.book_balance)).toFixed(data.decimal_places)} ${data.currency_code}`
    : "填写实际余额后计算";
  document.querySelector("#adjustment-preview").textContent =
    `该日账面余额：${data.book_balance} ${data.currency_code}；预计差异：${difference}`;
  const warning = document.querySelector("#adjustment-warning");
  warning.hidden = !data.has_later_transactions;
  warning.textContent = data.has_later_transactions
    ? `该日期之后仍有流水，保存后会重算后续余额。最近后续流水日期：${data.last_later_transaction_date}。`
    : "";
}

function openOpeningBalanceDialog() {
  const account = state.detail.account;
  if (!account) {
    return;
  }
  openingBalanceForm.reset();
  setStatus(openingBalanceStatus, "");
  openingBalanceAccountId.value = account.id;
  openingBalanceDateInput.value = account.opening_balance_date;
  openingBalanceValueInput.value = account.opening_balance;
  document.querySelector("#opening-balance-summary").textContent =
    `${account.name} · 当前期初余额 ${account.opening_balance} ${account.currency_code}`;
  openDialog(openingBalanceDialog);
}

async function refreshAfterChange() {
  state.detail.loaded = false;
  if (detailAccountSelect.value) {
    await loadDetail();
  }
  if (state.search.loaded) {
    await loadSearch();
  }
}

async function initialize() {
  updateBasisDescription();
  const budgetLoading=loadBudgetFilters();
  const [accounts, expenseCategories, incomeCategories] = await Promise.all([
    request("/api/accounts?include_inactive=true"),
    request("/api/categories?kind=expense&include_inactive=true"),
    request("/api/categories?kind=income&include_inactive=true"),
  ]);
  state.accounts = accounts;
  if (searchCurrency) {
    try {
      const currencies = await request('/api/currencies?include_inactive=true');
      for (const c of currencies) {
        const option = document.createElement('option'); option.value = c.code;
        option.textContent = `${c.name} · ${c.code}${c.is_active ? '' : '（停用）'}`; searchCurrency.append(option);
      }
      searchCurrency.disabled = false;
    } catch (error) { setStatus(searchStatus, `币种选项暂未加载：${error.message}，可继续查询全部币种。`, 'error'); }
  }
  state.categories = [...expenseCategories, ...incomeCategories];
  setSelectOptions(detailAccountSelect, accounts, "选择账户", formatAccountName, true);
  setSelectOptions(searchAccountSelect, accounts, "全部账户", formatAccountName);
  setSelectOptions(detailCategorySelect, state.categories, "全部分类", formatCategoryName);
  setSelectOptions(editorSourceSelect, accounts, "无来源账户", formatAccountName);
  setSelectOptions(editorDestinationSelect, accounts, "无去向账户", formatAccountName);
  initializeFrequentPickers();
  renderSearchCategoryOptions(state.categories);

  if (accounts.length) {
    const defaultAccount = accounts
      .filter((account) => account.is_active)
      .sort(compareAccountsByPriority)[0] || accounts[0];
    detailAccountSelect.value = String(defaultAccount.id);
    refreshPickers();
    await loadDetail(true);
  }
  updateViewVisibility();
  await budgetLoading;
}

for (const button of document.querySelectorAll(".detail-view-mode")) {
  button.addEventListener("click", () => {
    setView(button.dataset.view).catch((error) => {
      setStatus(button.dataset.view === "search" ? searchStatus : detailStatus, error.message, "error");
    });
  });
}

for (const button of document.querySelectorAll("[data-close-dialog]")) {
  button.addEventListener("click", () => closeDialog(document.querySelector(`#${button.dataset.closeDialog}`)));
}

detailForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadDetail(true).catch((error) => setStatus(detailStatus, error.message, "error"));
});

detailAccountSelect.addEventListener("change", () => {
  loadDetail(true).catch((error) => setStatus(detailStatus, error.message, "error"));
});

document.querySelector("#clear-detail-filters").addEventListener("click", () => {
  detailDates.reset();
  detailTransactionTypeSelect.value = "";
  detailCategorySelect.value = "";
  detailKeywordInput.value = "";
  detailAdjustmentOnlyInput.checked = false;
  detailFlaggedOnlyInput.checked = false;
  refreshPickers(detailForm);
  loadDetail(true).catch((error) => setStatus(detailStatus, error.message, "error"));
});

previousDetailPageButton.addEventListener("click", () => {
  state.detail.offset = Math.max(0, state.detail.offset - state.detailLimit);
  loadDetail().catch((error) => setStatus(detailStatus, error.message, "error"));
});

nextDetailPageButton.addEventListener("click", () => {
  if (state.detail.offset + state.detailLimit >= state.detail.total) {
    return;
  }
  state.detail.offset += state.detailLimit;
  loadDetail().catch((error) => setStatus(detailStatus, error.message, "error"));
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadSearch(true).catch((error) => setStatus(searchStatus, error.message, "error"));
});

function updateCheckboxes(container, predicate, mode) {
  for (const checkbox of container?.querySelectorAll("input[type=checkbox]") || []) {
    if (!predicate(checkbox)) continue;
    checkbox.checked = mode === "all" ? true : mode === "invert" ? !checkbox.checked : false;
  }
}

document.querySelector("#select-all-budgets").addEventListener("click", () => {
  updateCheckboxes(searchBudgetOptions, () => true, "all");
  updateBudgetSummary();
});
document.querySelector("#invert-budgets").addEventListener("click", () => {
  updateCheckboxes(searchBudgetOptions, () => true, "invert");
  updateBudgetSummary();
});
document.querySelector("#clear-budgets").addEventListener("click", () => {
  updateCheckboxes(searchBudgetOptions, () => true, "clear");
  updateBudgetSummary();
});
document.querySelector("#select-all-expense-categories").addEventListener("click", () => {
  updateCheckboxes(searchCategoryOptions, (checkbox) => checkbox.dataset.kind === "expense", "all");
  updateCategorySummary();
});
document.querySelector("#invert-expense-categories").addEventListener("click", () => {
  updateCheckboxes(searchCategoryOptions, (checkbox) => checkbox.dataset.kind === "expense", "invert");
  updateCategorySummary();
});
document.querySelector("#clear-categories").addEventListener("click", () => {
  updateCheckboxes(searchCategoryOptions, () => true, "clear");
  updateCategorySummary();
});

document.querySelector("#clear-search-filters").addEventListener("click", () => {
  for (const checkbox of searchBudgetOptions?.querySelectorAll("input") || []) {
    checkbox.checked = false;
  }
  updateBudgetSummary();
  searchAccountSelect.value = "";
  if (searchCurrency) searchCurrency.value = '';
  searchDates.reset();
  searchKeywordInput.value = "";
  searchAdjustmentOnlyInput.checked = false;
  searchFlaggedOnlyInput.checked = false;
  for (const checkbox of document.querySelectorAll("#search-type-options input")) {
    checkbox.checked = false;
  }
  for (const checkbox of searchCategoryOptions.querySelectorAll("input")) {
    checkbox.checked = false;
  }
  updateCategorySummary();
  refreshPickers(searchForm);
  loadSearch(true).catch((error) => setStatus(searchStatus, error.message, "error"));
});

previousSearchPageButton.addEventListener("click", () => {
  state.search.offset = Math.max(0, state.search.offset - state.searchLimit);
  loadSearch(false, true).catch((error) => setStatus(searchStatus, error.message, "error"));
});

nextSearchPageButton.addEventListener("click", () => {
  if (state.search.offset + state.searchLimit >= state.search.total) {
    return;
  }
  state.search.offset += state.searchLimit;
  loadSearch(false, true).catch((error) => setStatus(searchStatus, error.message, "error"));
});

editorTypeSelect.addEventListener("change", updateEditorFieldState);
editorClassifyAdjustmentButton.addEventListener("click", () => {
  editorTypeSelect.value = editorClassifyAdjustmentButton.dataset.transactionType;
  updateEditorFieldState();
  editorCategorySelect.focus();
});

editorForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!window.validateFrequentPickers(editorForm) || !editorForm.reportValidity()) {
    return;
  }
  try {
    setStatus(editorStatus, "");
    if (!window.confirm("保存后会重新计算相关账户余额、概况和 Excel 数据。确认保存修改？")) {
      return;
    }
    await request(`/api/transactions/${editorTransactionId.value}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildEditorPayload()),
    });
    closeDialog(editorDialog);
    await refreshAfterChange();
  } catch (error) {
    setStatus(editorStatus, error.message, "error");
  }
});

deleteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!deleteForm.reportValidity()) {
    return;
  }
  try {
    setStatus(deleteStatus, "");
    await request(`/api/transactions/${deleteTransactionId.value}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: deleteReasonInput.value.trim() }),
    });
    closeDialog(deleteDialog);
    await refreshAfterChange();
  } catch (error) {
    setStatus(deleteStatus, error.message, "error");
  }
});

flagForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!flagForm.reportValidity()) {
    return;
  }
  try {
    setStatus(flagStatus, "");
    await request(`/api/transactions/${flagTransactionId.value}/flag`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        flag_type: flagTypeSelect.value,
        note: flagNoteInput.value.trim(),
      }),
    });
    closeDialog(flagDialog);
    await refreshAfterChange();
  } catch (error) {
    setStatus(flagStatus, error.message, "error");
  }
});

document.querySelector("#open-adjustment-dialog").addEventListener("click", () => {
  openAdjustmentDialog().catch((error) => setStatus(detailStatus, error.message, "error"));
});
document.querySelector("#open-opening-balance-dialog").addEventListener("click", openOpeningBalanceDialog);

adjustmentDateInput.addEventListener("change", () => {
  loadAdjustmentPreview().catch((error) => setStatus(adjustmentStatus, error.message, "error"));
});
adjustmentActualInput.addEventListener("input", () => {
  loadAdjustmentPreview().catch((error) => setStatus(adjustmentStatus, error.message, "error"));
});

adjustmentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!adjustmentForm.reportValidity()) {
    return;
  }
  try {
    setStatus(adjustmentStatus, "");
    await request(`/api/accounts/${adjustmentAccountId.value}/adjustments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        adjustment_date: adjustmentDateInput.value,
        actual_balance: adjustmentActualInput.value,
        reason: adjustmentReasonInput.value.trim(),
        note: adjustmentNoteInput.value.trim() || null,
      }),
    });
    closeDialog(adjustmentDialog);
    await refreshAfterChange();
  } catch (error) {
    setStatus(adjustmentStatus, error.message, "error");
  }
});

openingBalanceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!openingBalanceForm.reportValidity()) {
    return;
  }
  try {
    setStatus(openingBalanceStatus, "");
    await request(`/api/accounts/${openingBalanceAccountId.value}/opening-balance`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        opening_balance: openingBalanceValueInput.value,
        opening_balance_date: openingBalanceDateInput.value,
        reason: openingBalanceReasonInput.value.trim(),
      }),
    });
    closeDialog(openingBalanceDialog);
    await refreshAfterChange();
  } catch (error) {
    setStatus(openingBalanceStatus, error.message, "error");
  }
});

initialize().then(async () => {
  const transactionId = new URLSearchParams(location.search).get("transaction");
  if (transactionId) await openTransactionEditor(Number(transactionId));
}).catch((error) => setStatus(detailStatus, error.message, "error"));

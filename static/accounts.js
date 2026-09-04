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
  detail: { offset: 0, total: 0, loaded: false, account: null },
  search: { offset: 0, total: 0, loaded: false },
  limit: 50,
};

const accountFilterPanel = document.querySelector(".account-filter-panel");
const accountWarning = document.querySelector("#detail-account-warning");
const accountSummaryPanel = document.querySelector("#account-summary-panel");
const ledgerPanel = document.querySelector("#ledger-panel");
const searchPanel = document.querySelector("#transaction-search-panel");
const searchResultsPanel = document.querySelector("#search-results-panel");

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

const editorDialog = document.querySelector("#transaction-editor-dialog");
const editorForm = document.querySelector("#transaction-editor-form");
const editorStatus = document.querySelector("#editor-status");
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
    select.append(option);
  }
}

function formatAccountName(account) {
  const inactiveLabel = account.is_active ? "" : "（已停用）";
  return `${account.name} · ${account.currency_code}${inactiveLabel}`;
}

function formatCategoryName(category) {
  const inactiveLabel = category.is_active ? "" : "（已停用）";
  return `${category.display_name}${inactiveLabel}`;
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
  searchPanel.hidden = showingAccount;
  searchResultsPanel.hidden = showingAccount || !state.search.loaded;
}

async function setView(view) {
  state.view = view;
  for (const button of document.querySelectorAll(".detail-view-mode")) {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  updateViewVisibility();
  if (view === "search" && !state.search.loaded) {
    await loadSearch(true);
  }
}

function buildDetailPath() {
  const parameters = new URLSearchParams({
    limit: String(state.limit),
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
    limit: String(state.limit),
    offset: String(state.search.offset),
  });
  if (searchAccountSelect.value) {
    parameters.set("account_id", searchAccountSelect.value);
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
  return `/api/transactions/search?${parameters.toString()}`;
}

function createStatusBadges(record) {
  const target = document.createElement("div");
  target.className = "status-badges";
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
  editButton.textContent = "编辑";
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
    { label: "最近对账", value: reconciliationValue, alert: reconciliation.status === "difference" },
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
    stateTarget.append(card);
  }
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
  if (resetPage) {
    state.detail.offset = 0;
  }
  if (!detailAccountSelect.value) {
    state.detail.loaded = false;
    updateViewVisibility();
    return;
  }
  setStatus(detailStatus, "");
  const data = await request(buildDetailPath());
  renderAccountState(data.account);
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

async function loadSearch(resetPage = false) {
  if (resetPage) {
    state.search.offset = 0;
  }
  setStatus(searchStatus, "");
  const data = await request(buildSearchPath());
  renderSearchResults(data.transactions);
  updateSearchPagination(data.pagination);
  state.search.loaded = true;
  updateViewVisibility();
}

function renderSearchCategoryOptions(categories) {
  searchCategoryOptions.replaceChildren();
  for (const category of categories) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    const text = document.createElement("span");
    checkbox.type = "checkbox";
    checkbox.value = category.id;
    checkbox.addEventListener("change", updateCategorySummary);
    text.textContent = formatCategoryName(category);
    label.append(checkbox, text);
    searchCategoryOptions.append(label);
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
}

function updateEditorFieldState() {
  const type = editorTypeSelect.value;
  const isIncome = type === "income";
  const isExpense = type === "expense";
  const isAdjustment = type === "adjustment";
  const isMovement = !isIncome && !isExpense && !isAdjustment;

  editorSourceSelect.disabled = isIncome;
  editorDestinationSelect.disabled = isExpense;
  editorOutflowInput.disabled = isIncome;
  editorInflowInput.disabled = isExpense;
  editorOutflowInput.required = isExpense || isMovement;
  editorInflowInput.required = isIncome || isMovement;
  setEditorCategoryOptions(isIncome ? "income" : isExpense ? "expense" : null, editorCategorySelect.value);

  if (isIncome) {
    editorSourceSelect.value = "";
    editorOutflowInput.value = "";
  }
  if (isExpense) {
    editorDestinationSelect.value = "";
    editorInflowInput.value = "";
  }
}

async function openTransactionEditor(transactionId) {
  const transaction = await request(`/api/transactions/${transactionId}`);
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
  setEditorCategoryOptions(
    transaction.transaction_type === "income"
      ? "income"
      : transaction.transaction_type === "expense"
        ? "expense"
        : null,
    transaction.category_id,
  );
  updateEditorFieldState();
  openDialog(editorDialog);
}

function buildEditorPayload() {
  return {
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
  const [accounts, expenseCategories, incomeCategories] = await Promise.all([
    request("/api/accounts?include_inactive=true"),
    request("/api/categories?kind=expense&include_inactive=true"),
    request("/api/categories?kind=income&include_inactive=true"),
  ]);
  state.accounts = accounts;
  state.categories = [...expenseCategories, ...incomeCategories];
  setSelectOptions(detailAccountSelect, accounts, "选择账户", formatAccountName, true);
  setSelectOptions(searchAccountSelect, accounts, "全部账户", formatAccountName);
  setSelectOptions(detailCategorySelect, state.categories, "全部分类", formatCategoryName);
  setSelectOptions(editorSourceSelect, accounts, "无来源账户", formatAccountName);
  setSelectOptions(editorDestinationSelect, accounts, "无去向账户", formatAccountName);
  renderSearchCategoryOptions(state.categories);

  if (accounts.length) {
    detailAccountSelect.value = String(
      accounts.find((account) => account.is_active)?.id || accounts[0].id,
    );
    await loadDetail(true);
  }
  updateViewVisibility();
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
  detailStartDateInput.value = "";
  detailEndDateInput.value = "";
  detailTransactionTypeSelect.value = "";
  detailCategorySelect.value = "";
  detailKeywordInput.value = "";
  detailAdjustmentOnlyInput.checked = false;
  detailFlaggedOnlyInput.checked = false;
  loadDetail(true).catch((error) => setStatus(detailStatus, error.message, "error"));
});

previousDetailPageButton.addEventListener("click", () => {
  state.detail.offset = Math.max(0, state.detail.offset - state.limit);
  loadDetail().catch((error) => setStatus(detailStatus, error.message, "error"));
});

nextDetailPageButton.addEventListener("click", () => {
  if (state.detail.offset + state.limit >= state.detail.total) {
    return;
  }
  state.detail.offset += state.limit;
  loadDetail().catch((error) => setStatus(detailStatus, error.message, "error"));
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadSearch(true).catch((error) => setStatus(searchStatus, error.message, "error"));
});

document.querySelector("#clear-search-filters").addEventListener("click", () => {
  searchAccountSelect.value = "";
  searchStartDateInput.value = "";
  searchEndDateInput.value = "";
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
  loadSearch(true).catch((error) => setStatus(searchStatus, error.message, "error"));
});

previousSearchPageButton.addEventListener("click", () => {
  state.search.offset = Math.max(0, state.search.offset - state.limit);
  loadSearch().catch((error) => setStatus(searchStatus, error.message, "error"));
});

nextSearchPageButton.addEventListener("click", () => {
  if (state.search.offset + state.limit >= state.search.total) {
    return;
  }
  state.search.offset += state.limit;
  loadSearch().catch((error) => setStatus(searchStatus, error.message, "error"));
});

editorTypeSelect.addEventListener("change", updateEditorFieldState);

editorForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!editorForm.reportValidity()) {
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

initialize().catch((error) => setStatus(detailStatus, error.message, "error"));

const state = {
  accounts: [],
  expenseCategories: [],
  incomeCategories: [],
  mode: "expense",
};

const form = document.querySelector("#transaction-form");
const statusElement = document.querySelector("#form-status");
const accountWarning = document.querySelector("#account-warning");
const submitButton = document.querySelector("#submit-transaction");
const dateInput = document.querySelector("#transaction-date");
const expenseAccountSelect = document.querySelector("#source-account");
const expenseAmountInput = document.querySelector("#expense-amount");
const expenseCategorySelect = document.querySelector("#expense-category");
const incomeAccountSelect = document.querySelector("#income-destination-account");
const incomeAmountInput = document.querySelector("#income-amount");
const incomeCategorySelect = document.querySelector("#income-category");
const transferSourceSelect = document.querySelector("#transfer-source-account");
const transferDestinationSelect = document.querySelector("#transfer-destination-account");
const transferOutflowInput = document.querySelector("#transfer-outflow-amount");
const transferInflowField = document.querySelector("#transfer-inflow-field");
const transferInflowInput = document.querySelector("#transfer-inflow-amount");
const counterpartyInput = document.querySelector("#counterparty");
const counterpartyLabel = document.querySelector("#counterparty-label");
const noteInput = document.querySelector("#note");

const modeLabels = {
  expense: "支出",
  transfer: "转账 / 换汇",
  income: "入账",
};

const quickActions = {
  "octopus-food": {
    mode: "expense",
    sourceAccountCode: "OCTOPUS-STUDENT",
    categoryName: "餐饮",
    focus: expenseAmountInput,
  },
  "wechat-to-octopus": {
    mode: "transfer",
    sourceAccountCode: "WECHAT",
    destinationAccountCode: "OCTOPUS-STUDENT",
    focus: transferOutflowInput,
  },
  "alipay-daily-investment": {
    mode: "transfer",
    sourceAccountCode: "ALIPAY",
    destinationAccountCode: "ALIPAY-FUND",
    amount: "35",
    note: "每日定投",
    focus: transferOutflowInput,
  },
};

function localDate() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "请求失败，请稍后重试。");
  }
  return response.json();
}

function accountById(value) {
  return state.accounts.find((account) => account.id === Number(value));
}

function accountByCode(code) {
  return state.accounts.find((account) => account.code === code);
}

function expenseCategoryByName(name) {
  return state.expenseCategories.find((category) => category.name === name);
}

function addOptions(select, records, placeholder) {
  select.replaceChildren();
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = placeholder;
  emptyOption.disabled = true;
  emptyOption.selected = true;
  select.append(emptyOption);

  for (const record of records) {
    const option = document.createElement("option");
    option.value = record.id;
    option.textContent = record.display_name || `${record.name} · ${record.currency_code}`;
    option.dataset.activity = JSON.stringify(record.activity || {});
    select.append(option);
  }
}

function setPickerRequired(select, required) {
  window.setFrequentPickerRequired(select, required);
}

function refreshPickers() {
  window.refreshFrequentPickers(form);
}

function initializeFrequentPickers() {
  for (const select of [
    expenseAccountSelect,
    transferSourceSelect,
    transferDestinationSelect,
    incomeAccountSelect,
  ]) {
    window.createFrequentPicker(select, {
      commonLabel: "常用账户",
      otherLabel: "账户",
      activityKey: select === expenseAccountSelect
        ? "expense_source"
        : select === transferSourceSelect
          ? "movement_source"
          : select === transferDestinationSelect
            ? "movement_destination"
            : "income_destination",
    });
  }
  for (const select of [expenseCategorySelect, incomeCategorySelect]) {
    window.createFrequentPicker(select, {
      commonLabel: "常用分类",
      otherLabel: "分类",
      activityKey: "usage",
    });
  }
}

function setStatus(message, type = "") {
  statusElement.textContent = message;
  statusElement.className = type;
}

function selectDefaultExpenseAccount() {
  const preferredAccount =
    state.accounts.find((account) => account.code === "OCTOPUS-STUDENT") ||
    state.accounts[0];
  expenseAccountSelect.value = preferredAccount ? String(preferredAccount.id) : "";
}

function selectDefaultExpenseCategory() {
  const preferredCategory =
    state.expenseCategories.find((category) => category.name === "餐饮") ||
    state.expenseCategories[0];
  expenseCategorySelect.value = preferredCategory ? String(preferredCategory.id) : "";
}

function setMode(mode) {
  state.mode = mode;

  for (const button of document.querySelectorAll(".entry-mode")) {
    const active = button.dataset.mode === mode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }

  for (const field of document.querySelectorAll("[data-modes]")) {
    const active = field.dataset.modes.split(" ").includes(mode);
    field.hidden = !active;
    for (const control of field.querySelectorAll("input, select")) {
      control.disabled = !active;
    }
  }

  expenseAmountInput.required = mode === "expense";
  incomeAmountInput.required = mode === "income";
  transferOutflowInput.required = mode === "transfer";
  setPickerRequired(expenseAccountSelect, mode === "expense");
  setPickerRequired(expenseCategorySelect, mode === "expense");
  setPickerRequired(incomeAccountSelect, mode === "income");
  setPickerRequired(incomeCategorySelect, mode === "income");
  setPickerRequired(transferSourceSelect, mode === "transfer");
  setPickerRequired(transferDestinationSelect, mode === "transfer");
  counterpartyLabel.textContent = mode === "income" ? "来源或对象" : "商户或对象";
  submitButton.textContent = `保存${modeLabels[mode]}`;

  updateTransferAmounts();
  refreshPickers();
  refreshSubmitState();
}

function updateTransferAmounts() {
  if (state.mode !== "transfer") {
    return;
  }

  const sourceAccount = accountById(transferSourceSelect.value);
  const destinationAccount = accountById(transferDestinationSelect.value);
  const receivedLabel = transferInflowField.querySelector("span");

  if (!sourceAccount || !destinationAccount) {
    receivedLabel.textContent = "收到金额";
    transferInflowField.hidden = false;
    transferInflowInput.disabled = true;
    transferInflowInput.required = false;
    transferInflowInput.value = "";
    return;
  }

  const isFxExchange = sourceAccount.currency_code !== destinationAccount.currency_code;
  receivedLabel.textContent = isFxExchange ? "收到金额" : "转入金额";
  transferInflowField.hidden = false;
  transferInflowInput.disabled = !isFxExchange;
  transferInflowInput.required = isFxExchange;

  if (isFxExchange) {
    transferInflowInput.placeholder = destinationAccount.currency_code;
  } else {
    transferInflowInput.value = transferOutflowInput.value;
    transferInflowInput.placeholder = sourceAccount.currency_code;
  }
}

function refreshSubmitState() {
  const categories =
    state.mode === "income" ? state.incomeCategories : state.expenseCategories;
  const accountRequirement = state.mode === "transfer" ? 2 : 1;
  submitButton.disabled =
    state.accounts.length < accountRequirement ||
    (state.mode !== "transfer" && categories.length === 0);
}

function quickActionUnavailableMessage(action) {
  const missing = [];
  if (action.sourceAccountCode && !accountByCode(action.sourceAccountCode)) {
    missing.push(action.sourceAccountCode);
  }
  if (
    action.destinationAccountCode &&
    !accountByCode(action.destinationAccountCode)
  ) {
    missing.push(action.destinationAccountCode);
  }
  if (action.categoryName && !expenseCategoryByName(action.categoryName)) {
    missing.push(`支出分类“${action.categoryName}”`);
  }
  return missing.length
    ? `缺少或已停用：${missing.join("、")}`
    : "";
}

function refreshQuickActionState() {
  for (const button of document.querySelectorAll("[data-quick-action]")) {
    const action = quickActions[button.dataset.quickAction];
    const message = action ? quickActionUnavailableMessage(action) : "快捷操作不存在。";
    button.disabled = Boolean(message);
    button.title = message;
  }
}

function applyQuickAction(actionId) {
  const action = quickActions[actionId];
  if (!action) {
    setStatus("快捷操作不存在。", "error");
    return;
  }

  const unavailableMessage = quickActionUnavailableMessage(action);
  if (unavailableMessage) {
    setStatus(unavailableMessage, "error");
    return;
  }

  const sourceAccount = accountByCode(action.sourceAccountCode);
  const destinationAccount = action.destinationAccountCode
    ? accountByCode(action.destinationAccountCode)
    : null;
  const category = action.categoryName
    ? expenseCategoryByName(action.categoryName)
    : null;

  form.reset();
  dateInput.value = localDate();
  setMode(action.mode);

  if (action.mode === "expense") {
    expenseAccountSelect.value = String(sourceAccount.id);
    expenseCategorySelect.value = String(category.id);
    expenseAmountInput.value = action.amount || "";
  } else if (action.mode === "transfer") {
    transferSourceSelect.value = String(sourceAccount.id);
    transferDestinationSelect.value = String(destinationAccount.id);
    transferOutflowInput.value = action.amount || "";
    noteInput.value = action.note || "";
    updateTransferAmounts();
  }

  refreshSubmitState();
  refreshPickers();
  action.focus.focus();
}

function resetTransactionForm({ preserveExpenseAccount = false } = {}) {
  const expenseAccountId = preserveExpenseAccount
    ? expenseAccountSelect.value
    : "";
  form.reset();
  dateInput.value = localDate();
  if (state.mode === "expense") {
    if (expenseAccountId) {
      expenseAccountSelect.value = expenseAccountId;
    } else {
      selectDefaultExpenseAccount();
    }
    selectDefaultExpenseCategory();
  }
  updateTransferAmounts();
  refreshPickers();
}

async function loadFormOptions() {
  const [accounts, expenseCategories, incomeCategories] = await Promise.all([
    request("/api/accounts"),
    request("/api/categories?kind=expense"),
    request("/api/categories?kind=income"),
  ]);
  state.accounts = accounts;
  state.expenseCategories = expenseCategories;
  state.incomeCategories = incomeCategories;

  addOptions(expenseAccountSelect, accounts, "选择支付账户");
  addOptions(transferSourceSelect, accounts, "选择转出账户");
  addOptions(transferDestinationSelect, accounts, "选择转入账户");
  addOptions(incomeAccountSelect, accounts, "选择到账账户");
  addOptions(expenseCategorySelect, expenseCategories, "选择支出分类");
  addOptions(incomeCategorySelect, incomeCategories, "选择收入分类");
  initializeFrequentPickers();

  accountWarning.hidden = accounts.length > 0;
  resetTransactionForm();
  setMode(state.mode);
  refreshQuickActionState();
}

function buildPayload() {
  const common = {
    transaction_date: dateInput.value,
    note: noteInput.value.trim() || null,
  };

  if (state.mode === "expense") {
    return {
      endpoint: "/api/expenses",
      payload: {
        ...common,
        source_account_id: Number(expenseAccountSelect.value),
        amount: expenseAmountInput.value,
        category_id: Number(expenseCategorySelect.value),
        counterparty: counterpartyInput.value.trim() || null,
      },
    };
  }

  if (state.mode === "income") {
    return {
      endpoint: "/api/incomes",
      payload: {
        ...common,
        destination_account_id: Number(incomeAccountSelect.value),
        amount: incomeAmountInput.value,
        category_id: Number(incomeCategorySelect.value),
        counterparty: counterpartyInput.value.trim() || null,
      },
    };
  }

  const sourceAccount = accountById(transferSourceSelect.value);
  const destinationAccount = accountById(transferDestinationSelect.value);
  const payload = {
    ...common,
    source_account_id: Number(transferSourceSelect.value),
    destination_account_id: Number(transferDestinationSelect.value),
    outflow_amount: transferOutflowInput.value,
  };
  if (sourceAccount && destinationAccount && sourceAccount.currency_code !== destinationAccount.currency_code) {
    payload.inflow_amount = transferInflowInput.value;
  }
  return { endpoint: "/api/transfers", payload };
}

for (const button of document.querySelectorAll(".entry-mode")) {
  button.addEventListener("click", () => {
    setStatus("");
    setMode(button.dataset.mode);
    resetTransactionForm();
  });
}

for (const button of document.querySelectorAll("[data-quick-action]")) {
  button.addEventListener("click", () => {
    setStatus("");
    applyQuickAction(button.dataset.quickAction);
  });
}

transferSourceSelect.addEventListener("change", updateTransferAmounts);
transferDestinationSelect.addEventListener("change", updateTransferAmounts);
transferOutflowInput.addEventListener("input", updateTransferAmounts);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("");
  if (!window.validateFrequentPickers(form) || !form.reportValidity()) {
    return;
  }

  submitButton.disabled = true;
  const { endpoint, payload } = buildPayload();

  try {
    const result = await request(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const amount = result.amount || result.outflow_amount;
    const currency = result.currency_code || result.source_currency_code;
    setStatus(`已记录 ${amount} ${currency}`, "success");
    resetTransactionForm({ preserveExpenseAccount: state.mode === "expense" });
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    refreshSubmitState();
  }
});

async function initialize() {
  dateInput.value = localDate();
  try {
    await loadFormOptions();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

initialize();

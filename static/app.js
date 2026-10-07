const state = {
  accounts: [],
  expenseCategories: [],
  incomeCategories: [],
  mode: "expense",
  budgetConfig: null,
  manualBudget: { expense: false, income: false },
  statuses: {},
  saving: new Set(),
};

// Each mode owns its inputs and draft. Keep the original expense selectors.
const forms = { expense: document.querySelector('#transaction-form') };
for (const mode of ['income', 'transfer']) {
  const next = document.createElement('form'); next.id = `${mode}-form`; next.hidden = true;
  const grid = document.createElement('div'); grid.className = 'form-grid';
  const date = document.querySelector('#transaction-date').closest('label').cloneNode(true);
  date.querySelector('input').id = `${mode}-date`; grid.append(date);
  for (const label of forms.expense.querySelectorAll(`[data-modes="${mode}"]`)) grid.append(label);
  if (mode === 'income') {
    const counterparty = document.querySelector('#counterparty').closest('label').cloneNode(true);
    counterparty.removeAttribute('data-modes');counterparty.querySelector('input').id='income-counterparty';
    counterparty.querySelector('span').removeAttribute('id');counterparty.querySelector('span').textContent='来源或对象';grid.append(counterparty);
  }
  const note = document.querySelector('#note').closest('label').cloneNode(true);note.querySelector('textarea').id=`${mode}-note`;grid.append(note);
  const actions=document.createElement('div');actions.className='form-actions';
  const submit=document.createElement('button');submit.type='submit';submit.id=`submit-${mode}`;submit.textContent=mode==='income'?'保存入账':'保存转账 / 换汇';actions.append(submit);
  next.append(grid,actions);forms.expense.after(next);forms[mode]=next;
}
let form = forms.expense;
const statusElement = document.querySelector("#form-status");
const accountWarning = document.querySelector("#account-warning");
let submitButton = document.querySelector("#submit-transaction");
let dateInput = document.querySelector("#transaction-date");
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
let counterpartyInput = document.querySelector("#counterparty");
const counterpartyLabel = document.querySelector("#counterparty-label");
let noteInput = document.querySelector("#note");

for (const mode of ['expense','income']) {
  const grid=forms[mode].querySelector('.form-grid');
  const label=document.createElement('label');label.className='entry-budget-field';const span=document.createElement('span');span.textContent=mode==='expense'?'消费自预算':'收入归属';
  const select=document.createElement('select');select.id=`${mode}-budget-system`;
  const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=mode==='expense'?'日常预算（自动）':'未归属';select.append(placeholder);select.disabled=true;
  const hint=document.createElement('small');hint.id=`${mode}-budget-hint`;hint.textContent='正在加载预算设置…';
  label.append(span,select,hint);
  const presetLabel=document.createElement('label'),presetTitle=document.createElement('span'),preset=document.createElement('select');
  presetTitle.textContent='记账预设（可选）';preset.id=`${mode}-preset`;const none=document.createElement('option');none.value='';none.textContent='不使用预设';preset.append(none);preset.disabled=true;presetLabel.append(presetTitle,preset);
  const auto=document.createElement('button');auto.type='button';auto.className='secondary-button budget-auto-button';auto.textContent='恢复自动预算';auto.onclick=()=>{state.manualBudget[mode]=false;resolveBudget(mode);};label.append(auto);
  const noteField=grid.querySelector('textarea').closest('label');
  const counterpartyField=grid.querySelector('input[maxlength="120"]')?.closest('label');
  counterpartyField?.classList.add('full-width');
  grid.insertBefore(label,counterpartyField||noteField);grid.insertBefore(presetLabel,counterpartyField||noteField);
  select.onchange=()=>{state.manualBudget[mode]=true;resolveBudget(mode);};
  preset.onchange=()=>{
    const selected=state.budgetConfig?.presets.find(p=>p.id===Number(preset.value));
    if(selected?.category_id)(mode==='expense'?expenseCategorySelect:incomeCategorySelect).value=selected.category_id;
    if(selected?.account_id)(mode==='expense'?expenseAccountSelect:incomeAccountSelect).value=selected.account_id;
    resolveBudget(mode);window.refreshFrequentPickers(forms[mode]);
  };
}

function resolveBudget(mode) {
  const config=state.budgetConfig;if(!config)return;
  const select=document.getElementById(`${mode}-budget-system`),hint=document.getElementById(`${mode}-budget-hint`);
  if(state.manualBudget[mode]){hint.textContent=mode==='expense'&&!select.value?'未单独指定，自动计入日常预算。':'仅用于本笔，下次记账恢复自动。';return;}
  const preset=config.presets.find(p=>p.is_active&&p.mode===mode&&p.id===Number(document.getElementById(`${mode}-preset`).value));
  const category=Number((mode==='expense'?expenseCategorySelect:incomeCategorySelect).value);
  const rule=config.rules.find(r=>r.is_active&&r.mode===mode&&r.category_id===category);
  select.value=String(preset?.budget_system_id??rule?.budget_system_id??config.defaults[mode]??'');
  hint.textContent=preset?'来自所选预设':rule?'来自分类映射':'来自默认预算设置';
}

async function loadBudgetOptions() {
  try {
    state.budgetConfig=await request('/api/budget/config');
    for(const mode of ['expense','income']) {
      const select=document.getElementById(`${mode}-budget-system`),preset=document.getElementById(`${mode}-preset`);
      if(mode==='expense')select.options[0].textContent=`${state.budgetConfig.systems.find(s=>s.id===state.budgetConfig.daily_expense)?.name||'日常预算'}（自动）`;
      for(const s of state.budgetConfig.systems.filter(s=>s.is_active)){const o=document.createElement('option');o.value=s.id;o.textContent=s.name;select.append(o);}
      for(const p of state.budgetConfig.presets.filter(p=>p.is_active&&p.mode===mode)){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;preset.append(o);}
      select.disabled=false;preset.disabled=false;resolveBudget(mode);
    }
  } catch(e) {
    for(const mode of ['expense','income'])document.getElementById(`${mode}-budget-hint`).textContent=mode==='expense'?'预算设置加载失败；仍可记账，未单独指定的支出自动计入日常。':'预算设置加载失败；仍可记账为未归属。刷新后重试。';
  }
}

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
  for (const target of Object.values(forms)) window.refreshFrequentPickers(target);
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
  state.statuses[state.mode] = {message,type};
  statusElement.textContent = message;
  statusElement.className = type;
}

function selectDefaultExpenseAccount() {
  const preferredValue = expenseAccountSelect._frequentPicker?.preferredValue();
  expenseAccountSelect.value = preferredValue || (state.accounts[0] ? String(state.accounts[0].id) : "");
}

function selectDefaultExpenseCategory() {
  const preferredCategory =
    state.expenseCategories.find((category) => category.name === "餐饮") ||
    state.expenseCategories[0];
  expenseCategorySelect.value = preferredCategory ? String(preferredCategory.id) : "";
}

function setMode(mode) {
  state.mode = mode;
  form=forms[mode];dateInput=form.querySelector('input[type="date"]');noteInput=form.querySelector('textarea');
  counterpartyInput=document.getElementById(mode==='income'?'income-counterparty':'counterparty');submitButton=form.querySelector('button[type="submit"]');
  for(const [key,target] of Object.entries(forms))target.hidden=key!==mode;
  const saved=state.statuses[mode]||{message:'',type:''};setStatus(saved.message,saved.type);

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
  counterpartyLabel.textContent = "商户或对象";
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
    state.saving.has(state.mode) ||
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
  if(action && state.saving.has(action.mode))return;
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

  setMode(action.mode);
  resetTransactionForm();

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
  if(action.mode==='expense')resolveBudget('expense');
  action.focus.focus();
}

function resetTransactionForm({ preserveExpenseAccount = false } = {}) {
  const expenseAccountId = preserveExpenseAccount
    ? expenseAccountSelect.value
    : "";
  form.reset();
  dateInput.value = localDate();
  if(state.mode!=='transfer'){state.manualBudget[state.mode]=false;document.getElementById(`${state.mode}-preset`).value='';}
  if (state.mode === "expense") {
    if (expenseAccountId) {
      expenseAccountSelect.value = expenseAccountId;
    } else {
      selectDefaultExpenseAccount();
    }
    selectDefaultExpenseCategory();
  }
  if(state.mode!=='transfer')resolveBudget(state.mode);
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
  for (const target of Object.values(forms)) target.querySelector('input[type="date"]').value=localDate();

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
  if(state.mode!=='transfer'&&state.budgetConfig)common.budget_system_id=Number(document.getElementById(`${state.mode}-budget-system`).value)||null;

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
    setMode(button.dataset.mode);
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

for (const [mode, target] of Object.entries(forms)) target.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (mode!==state.mode || state.saving.has(mode))return;
  setStatus("");
  if (!window.validateFrequentPickers(form) || !form.reportValidity()) {
    return;
  }

  submitButton.disabled = true;
  state.saving.add(mode);
  // Keep this mode's draft stable while the request is in flight.
  target.inert=true;
  const { endpoint, payload } = buildPayload();

  try {
    const result = await request(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const amount = result.amount || result.outflow_amount;
    const currency = result.currency_code || result.source_currency_code;
    const viewing=state.mode;setMode(mode);
    setStatus(`已记录 ${amount} ${currency}`, "success");
    resetTransactionForm({ preserveExpenseAccount: mode === "expense" });
    setMode(viewing);
  } catch (error) {
    state.statuses[mode]={message:error.message,type:'error'};
    if(state.mode===mode)setStatus(error.message,'error');
  } finally {
    state.saving.delete(mode);target.inert=false;
    refreshSubmitState();
  }
});

async function initialize() {
  dateInput.value = localDate();
  try {
    await loadFormOptions();
    await loadBudgetOptions();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

expenseCategorySelect.addEventListener('change',()=>resolveBudget('expense'));
incomeCategorySelect.addEventListener('change',()=>resolveBudget('income'));

initialize();

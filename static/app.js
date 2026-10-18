const state = {
  accounts: [],
  expenseCategories: [],
  incomeCategories: [],
  mode: "expense",
  budgetConfig: null,
  manualBudget: { expense: false, income: false },
  statuses: {},
  saving: new Set(),
  entryShortcuts: [],
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
      commonLabel: "优先账户",
      displayMode: "account-directory",
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
      activityKey: "usage",
      displayMode: "ranked-scroll",
      visibleRows: 8,
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

function hasShortcutDraft(mode) {
  if (mode === "expense") {
    return Boolean(expenseAmountInput.value || document.querySelector("#counterparty").value || document.querySelector("#note").value);
  }
  if (mode === "income") {
    return Boolean(incomeAmountInput.value || document.querySelector("#income-counterparty").value || document.querySelector("#income-note").value);
  }
  return Boolean(transferOutflowInput.value || transferInflowInput.value || document.querySelector("#transfer-note").value);
}

function renderQuickActions() {
  const target = document.querySelector("#quick-action-list");
  target.replaceChildren();
  const rows = state.entryShortcuts.filter((shortcut) => shortcut.is_active);
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "subtle-note";
    empty.textContent = "暂无便捷操作，可在设置中新增。";
    target.append(empty);
    return;
  }
  for (const shortcut of rows) {
    const button = document.createElement("button");
    button.className = "quick-action";
    button.type = "button";
    button.dataset.quickAction = shortcut.id;
    button.textContent = shortcut.name;
    button.disabled = !shortcut.valid;
    button.title = shortcut.invalid_reason || "点击后预填表单，仍需确认保存";
    button.addEventListener("click", () => {
      setStatus("");
      applyQuickAction(shortcut.id);
    });
    target.append(button);
  }
}

async function loadEntryShortcuts() {
  try {
    const data = await request("/api/entry-shortcuts");
    state.entryShortcuts = data.shortcuts;
    renderQuickActions();
  } catch (_) {
    state.entryShortcuts = [];
    const target = document.querySelector("#quick-action-list");
    target.replaceChildren();
    const message = document.createElement("p");
    message.className = "subtle-note";
    message.textContent = "便捷操作加载失败，普通记账仍可使用。";
    target.append(message);
  }
}

function applyQuickAction(actionId) {
  const action = state.entryShortcuts.find((shortcut) => shortcut.id === Number(actionId));
  if(action && state.saving.has(action.mode))return;
  if (!action) {
    setStatus("快捷操作不存在。", "error");
    return;
  }
  if (!action.valid) {
    setStatus(action.invalid_reason || "便捷操作当前不可用。", "error");
    return;
  }

  if (hasShortcutDraft(action.mode) && !window.confirm("使用便捷操作会替换该模式尚未保存的内容，继续吗？")) return;

  setMode(action.mode);
  resetTransactionForm();

  if (action.mode === "expense") {
    expenseAccountSelect.value = String(action.source_account_id);
    expenseCategorySelect.value = String(action.category_id);
    expenseAmountInput.value = action.outflow_amount || "";
    counterpartyInput.value = action.counterparty || "";
  } else if (action.mode === "income") {
    incomeAccountSelect.value = String(action.destination_account_id);
    incomeCategorySelect.value = String(action.category_id);
    incomeAmountInput.value = action.inflow_amount || "";
    counterpartyInput.value = action.counterparty || "";
  } else if (action.mode === "transfer") {
    transferSourceSelect.value = String(action.source_account_id);
    transferDestinationSelect.value = String(action.destination_account_id);
    transferOutflowInput.value = action.outflow_amount || "";
    noteInput.value = action.note || "";
    updateTransferAmounts();
    if (!transferInflowInput.disabled) transferInflowInput.value = action.inflow_amount || "";
  }

  if (action.mode !== "transfer") {
    noteInput.value = action.note || "";
    document.getElementById(`${action.mode}-preset`).value = "";
    state.manualBudget[action.mode] = Boolean(action.budget_system_id);
    if (action.budget_system_id) {
      document.getElementById(`${action.mode}-budget-system`).value = String(action.budget_system_id);
    }
    resolveBudget(action.mode);
  }
  refreshSubmitState();
  refreshPickers();
  const focusTarget = action.mode === "expense"
    ? expenseAmountInput
    : action.mode === "income"
      ? incomeAmountInput
      : (!transferOutflowInput.value ? transferOutflowInput : (!transferInflowInput.disabled && !transferInflowInput.value ? transferInflowInput : transferOutflowInput));
  focusTarget.focus();
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

function successMessage(mode, result) {
  if (mode === "transfer") {
    const movement = `${result.outflow_amount} ${result.source_currency_code} → ${result.inflow_amount} ${result.destination_currency_code}`;
    const sourceBalance = `${result.source_account_name}余额 ${result.source_account_balance} ${result.source_currency_code}`;
    const destinationBalance = `${result.destination_account_name}余额 ${result.destination_account_balance} ${result.destination_currency_code}`;
    return `已记录 ${movement} · ${sourceBalance} · ${destinationBalance}`;
  }

  const accountBalance = `${result.account_name}余额 ${result.account_balance} ${result.currency_code}`;
  return `已记录 ${result.amount} ${result.currency_code} · ${accountBalance}`;
}

for (const button of document.querySelectorAll(".entry-mode")) {
  button.addEventListener("click", () => {
    setMode(button.dataset.mode);
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
    const viewing=state.mode;setMode(mode);
    setStatus(successMessage(mode, result), "success");
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
    await loadEntryShortcuts();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

expenseCategorySelect.addEventListener('change',()=>resolveBudget('expense'));
incomeCategorySelect.addEventListener('change',()=>resolveBudget('income'));

initialize();

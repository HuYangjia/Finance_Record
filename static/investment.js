const state = {
  accounts: [],
  valuations: [],
  monthlyLoaded: false,
  fxLoaded: false,
};

const valuationForm = document.querySelector("#valuation-form");
const valuationStatus = document.querySelector("#valuation-status");
const valuationAccount = document.querySelector("#valuation-account");
const valuationDate = document.querySelector("#valuation-date");
const valuationTotalValue = document.querySelector("#valuation-total-value");
const valuationNote = document.querySelector("#valuation-note");

const performanceForm = document.querySelector("#performance-form");
const performanceStatus = document.querySelector("#performance-status");
const performanceAccount = document.querySelector("#performance-account");
const performanceStartDate = document.querySelector("#performance-start-date");
const performanceEndDate = document.querySelector("#performance-end-date");

const monthlyForm = document.querySelector("#monthly-investment-form");
const monthlyStatus = document.querySelector("#monthly-investment-status");
const monthlyAccount = document.querySelector("#monthly-investment-account");
const monthlyStart = document.querySelector("#monthly-investment-start");
const monthlyEnd = document.querySelector("#monthly-investment-end");
const monthlyInvestmentDetails = document.querySelector("#monthly-investment-details");

const fxStatus = document.querySelector("#fx-status");
const fxRateDetails = document.querySelector("#fx-rate-details");
const fxRateDetailsSummary = document.querySelector("#fx-rate-details-summary");
const manualFxForm = document.querySelector("#manual-fx-form");
const manualFxDate = document.querySelector("#manual-fx-date");
const manualUsdCny = document.querySelector("#manual-usd-cny");
const manualHkdCny = document.querySelector("#manual-hkd-cny");
const manualFxNote = document.querySelector("#manual-fx-note");

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

function localMonth() {
  return localDate().slice(0, 7);
}

function setStatus(target, message, type = "") {
  target.textContent = message;
  target.className = `inline-status ${type}`.trim();
}

function clearTable(body, colspan, emptyText) {
  body.replaceChildren();
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = colspan;
  cell.className = "empty-cell";
  cell.textContent = emptyText;
  row.append(cell);
  body.append(row);
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

function accountLabel(account) {
  const inactive = account.is_active ? "" : "（已停用）";
  return `${account.name} · ${account.currency_code}${inactive}`;
}

function setAccountOptions(select, includeAll = false) {
  const selected = select.value;
  select.replaceChildren();
  if (includeAll) {
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = "全部投资账户";
    select.append(allOption);
  }
  for (const account of state.accounts) {
    const option = document.createElement("option");
    option.value = account.id;
    option.textContent = accountLabel(account);
    select.append(option);
  }
  if (Array.from(select.options).some((option) => option.value === selected)) {
    select.value = selected;
  }
}

function amount(value, currencyCode) {
  return `${value} ${currencyCode}`;
}

function createActionButton(label, className, listener) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", listener);
  return button;
}

function resetValuationForm() {
  valuationForm.reset();
  valuationDate.value = localDate();
  if (state.accounts.length) {
    valuationAccount.value = String(
      state.accounts.find((account) => account.is_active)?.id || state.accounts[0].id,
    );
  }
  setStatus(valuationStatus, "");
}

function editValuation(valuation) {
  valuationAccount.value = String(valuation.account_id);
  valuationDate.value = valuation.valuation_date;
  valuationTotalValue.value = valuation.total_value;
  valuationNote.value = valuation.note || "";
  valuationForm.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadValuations() {
  const rows = await request("/api/investments/valuations?limit=200");
  state.valuations = rows;
  const body = document.querySelector("#valuation-list-body");
  if (!rows.length) {
    clearTable(body, 6, "暂无投资估值");
    document.querySelector("#valuation-list-summary").textContent = "共 0 条";
    return;
  }
  body.replaceChildren();
  for (const valuation of rows) {
    const actions = document.createElement("div");
    actions.className = "row-actions";
    actions.append(
      createActionButton("修改", "table-action", () => editValuation(valuation)),
      createActionButton("删除", "table-action danger-action", () => {
        deleteValuation(valuation).catch((error) => setStatus(valuationStatus, error.message, "error"));
      }),
    );
    const generatedChange = Number(valuation.generated_change);
    const row = document.createElement("tr");
    appendCells(row, [
      valuation.valuation_date,
      valuation.account_name,
      amount(valuation.total_value, valuation.currency_code),
      `${generatedChange > 0 ? "+" : ""}${valuation.generated_change} ${valuation.currency_code}`,
      valuation.note || "",
      actions,
    ]);
    body.append(row);
  }
  document.querySelector("#valuation-list-summary").textContent = `共 ${rows.length} 条`;
}

async function deleteValuation(valuation) {
  if (!window.confirm(`删除 ${valuation.account_name} 在 ${valuation.valuation_date} 的估值？后续自动损益会重算。`)) {
    return;
  }
  await request(`/api/investments/valuations/${valuation.id}`, { method: "DELETE" });
  await refreshInvestmentData();
}

function renderPerformance(rows) {
  const body = document.querySelector("#performance-body");
  if (!rows.length) {
    clearTable(body, 7, "没有可查询的投资账户");
    return;
  }
  body.replaceChildren();
  for (const record of rows) {
    const row = document.createElement("tr");
    appendCells(row, [
      record.account_name,
      amount(record.opening_balance, record.currency_code),
      amount(record.contribution, record.currency_code),
      amount(record.withdrawal, record.currency_code),
      amount(record.closing_balance, record.currency_code),
      amount(record.profit, record.currency_code),
      record.status_label,
    ]);
    body.append(row);
  }
}

async function loadPerformance() {
  const parameters = new URLSearchParams({
    start_date: performanceStartDate.value,
    end_date: performanceEndDate.value,
  });
  if (performanceAccount.value) {
    parameters.set("account_id", performanceAccount.value);
  }
  const data = await request(`/api/investments/performance?${parameters.toString()}`);
  renderPerformance(data.results);
}

function renderMonthly(rows) {
  const body = document.querySelector("#monthly-investment-body");
  if (!rows.length) {
    clearTable(body, 8, "没有可查询的投资账户");
    return;
  }
  body.replaceChildren();
  for (const record of rows) {
    const row = document.createElement("tr");
    appendCells(row, [
      record.month,
      record.account_name,
      amount(record.opening_balance, record.currency_code),
      amount(record.contribution, record.currency_code),
      amount(record.withdrawal, record.currency_code),
      amount(record.closing_balance, record.currency_code),
      amount(record.profit, record.currency_code),
      record.status_label,
    ]);
    body.append(row);
  }
}

async function loadMonthly() {
  const parameters = new URLSearchParams({
    start_month: monthlyStart.value,
    end_month: monthlyEnd.value,
  });
  if (monthlyAccount.value) {
    parameters.set("account_id", monthlyAccount.value);
  }
  const data = await request(`/api/investments/monthly?${parameters.toString()}`);
  renderMonthly(data.results);
  state.monthlyLoaded = true;
}

function renderFxRates(rows) {
  const body = document.querySelector("#fx-rates-body");
  if (!rows.length) {
    clearTable(body, 6, "暂无汇率快照");
    return;
  }
  body.replaceChildren();
  for (const rate of rows) {
    const row = document.createElement("tr");
    appendCells(row, [
      rate.rate_date,
      rate.usd_cny_rate,
      rate.hkd_cny_rate,
      rate.source,
      rate.market_date,
      rate.note || "",
    ]);
    body.append(row);
  }
}

async function loadFxRates() {
  const rows = await request("/api/fx-rates?limit=120");
  renderFxRates(rows);
  state.fxLoaded = true;
  fxRateDetailsSummary.textContent = rows.length
    ? `每日汇率：最近快照 ${rows[0].rate_date}，已加载 ${rows.length} 条`
    : "每日汇率：暂无快照";
}

async function refreshInvestmentData() {
  const loaders = [loadValuations(), loadPerformance()];
  if (state.monthlyLoaded) {
    loaders.push(loadMonthly());
  }
  await Promise.all(loaders);
}

valuationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!valuationForm.reportValidity()) {
    return;
  }
  try {
    setStatus(valuationStatus, "");
    await request(
      `/api/investments/accounts/${valuationAccount.value}/valuations/${valuationDate.value}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          total_value: valuationTotalValue.value,
          note: valuationNote.value.trim() || null,
        }),
      },
    );
    setStatus(valuationStatus, "估值已保存，投资损益流水已重算。", "success");
    await refreshInvestmentData();
  } catch (error) {
    setStatus(valuationStatus, error.message, "error");
  }
});

document.querySelector("#valuation-reset").addEventListener("click", resetValuationForm);

performanceForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadPerformance().catch((error) => setStatus(performanceStatus, error.message, "error"));
});

monthlyForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadMonthly().catch((error) => setStatus(monthlyStatus, error.message, "error"));
});

monthlyInvestmentDetails.addEventListener("toggle", () => {
  if (monthlyInvestmentDetails.open && !state.monthlyLoaded) {
    loadMonthly().catch((error) => setStatus(monthlyStatus, error.message, "error"));
  }
});

fxRateDetails.addEventListener("toggle", () => {
  if (fxRateDetails.open && !state.fxLoaded) {
    loadFxRates().catch((error) => setStatus(fxStatus, error.message, "error"));
  }
});

document.querySelector("#sync-fx-rates").addEventListener("click", async () => {
  try {
    setStatus(fxStatus, "");
    const result = await request("/api/fx-rates/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rate_date: localDate() }),
    });
    setStatus(
      fxStatus,
      `已使用 ${result.market_date} 市场汇率更新 ${result.rate_date} 快照。`,
      "success",
    );
    await loadFxRates();
  } catch (error) {
    setStatus(fxStatus, error.message, "error");
  }
});

manualFxForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!manualFxForm.reportValidity()) {
    return;
  }
  try {
    setStatus(fxStatus, "");
    await request(`/api/fx-rates/${manualFxDate.value}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        usd_cny_rate: manualUsdCny.value,
        hkd_cny_rate: manualHkdCny.value,
        note: manualFxNote.value.trim() || null,
      }),
    });
    setStatus(fxStatus, "手工汇率已保存。", "success");
    await loadFxRates();
  } catch (error) {
    setStatus(fxStatus, error.message, "error");
  }
});

async function initialize() {
  const accounts = await request("/api/investments/accounts");
  state.accounts = accounts;
  setAccountOptions(valuationAccount);
  setAccountOptions(performanceAccount, true);
  setAccountOptions(monthlyAccount, true);

  resetValuationForm();
  const today = localDate();
  const currentMonth = localMonth();
  performanceEndDate.value = today;
  performanceStartDate.value = `${currentMonth}-01`;
  monthlyStart.value = `${today.slice(0, 4)}-01`;
  monthlyEnd.value = currentMonth;
  monthlyEnd.max = currentMonth;
  monthlyStart.max = currentMonth;
  manualFxDate.value = today;

  await Promise.all([loadValuations(), loadPerformance()]);
}

initialize().catch((error) => setStatus(valuationStatus, error.message, "error"));

const monthInput = document.querySelector("#month");
const monthLabel = document.querySelector("#month-label");
const statusElement = document.querySelector("#overview-status");

function localMonth() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  return `${now.getFullYear()}-${month}`;
}

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "请求失败，请稍后重试。");
  }
  return response.json();
}

function setStatus(message, type = "") {
  statusElement.textContent = message;
  statusElement.className = type;
}

function renderMetricGroup(targetId, records, fields, emptyMessage) {
  const target = document.querySelector(targetId);
  target.replaceChildren();

  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "empty-notice";
    empty.textContent = emptyMessage;
    target.append(empty);
    return;
  }

  for (const record of records) {
    const item = document.createElement("article");
    item.className = "metric-item";
    const heading = document.createElement("h3");
    heading.textContent = record.currency_code;
    item.append(heading);
    for (const field of fields) {
      const row = document.createElement("div");
      const name = document.createElement("span");
      const value = document.createElement("strong");
      name.textContent = field.label;
      value.textContent = record[field.key];
      row.append(name, value);
      item.append(row);
    }
    target.append(item);
  }
}

function formatTransactionAmounts(record) {
  if (record.outflow_amount && record.inflow_amount) {
    return `${record.outflow_amount} ${record.source_currency_code} -> ${record.inflow_amount} ${record.destination_currency_code}`;
  }
  if (record.outflow_amount) {
    return `${record.outflow_amount} ${record.source_currency_code}`;
  }
  return `${record.inflow_amount} ${record.destination_currency_code}`;
}

function renderInternalMovements(records) {
  const body = document.querySelector("#internal-movements-body");
  body.replaceChildren();

  if (!records.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="5" class="empty-cell">本月暂无账户内资金流动</td>';
    body.append(row);
    return;
  }

  for (const record of records) {
    const row = document.createElement("tr");
    const values = [
      record.transaction_date,
      record.transaction_type_label,
      `${record.source_account_name} -> ${record.destination_account_name}`,
      formatTransactionAmounts(record),
      record.note || "",
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
}

function renderBaseCurrencyTotals(records) {
  const target = document.querySelector("#base-currency-totals");
  target.replaceChildren();
  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "empty-notice";
    empty.textContent = "暂无可换算的账户余额。";
    target.append(empty);
    return;
  }

  for (const record of records) {
    const item = document.createElement("article");
    item.className = "metric-item";
    const heading = document.createElement("h3");
    heading.textContent = record.currency_code;
    item.append(heading);

    for (const [labelText, amount] of [
      ["总资产", record.total_assets],
      ["总负债", record.total_liabilities],
      ["净资产", record.net_worth],
    ]) {
      const row = document.createElement("div");
      const label = document.createElement("span");
      const value = document.createElement("strong");
      label.textContent = labelText;
      value.textContent = `${amount} ${record.currency_code}`;
      row.append(label, value);
      item.append(row);
    }

    const rate = document.createElement("div");
    const rateLabel = document.createElement("span");
    const rateValue = document.createElement("strong");
    rateLabel.textContent = "汇率快照";
    rateValue.textContent = record.rate
      ? `${record.rate.rate_date} · ${record.rate.source}`
      : "暂无可用汇率";
    rate.append(rateLabel, rateValue);
    item.append(rate);

    if (record.missing_currencies.length) {
      item.classList.add("is-alert");
      const missing = document.createElement("div");
      const missingLabel = document.createElement("span");
      const missingValue = document.createElement("strong");
      missingLabel.textContent = "待补汇率";
      missingValue.textContent = record.missing_currencies.join("、");
      missing.append(missingLabel, missingValue);
      item.append(missing);
    }
    target.append(item);
  }
}

async function loadDashboard() {
  const data = await request(`/api/dashboard?month=${encodeURIComponent(monthInput.value)}`);
  monthLabel.textContent = data.month;
  document.querySelector("#movement-month-label").textContent = data.month;
  document.querySelector("#base-currency-date").textContent = `截至 ${data.as_of_date}`;
  renderMetricGroup("#cash-flow", data.cash_flow, [
    { key: "income", label: "收入" },
    { key: "expense", label: "支出" },
    { key: "balance", label: "结余" },
  ], "本月暂无收入或支出。");
  renderMetricGroup("#available-cash", data.available_cash || [], [
    { key: "available_cash", label: "可用现金（不含投资）" },
  ], "暂无可用现金账户。");
  renderMetricGroup("#net-worth", data.net_worth, [
    { key: "assets", label: "资产" },
    { key: "liabilities", label: "负债" },
    { key: "net_worth", label: "净资产" },
  ], "尚未创建账户。");
  renderBaseCurrencyTotals(data.base_currency_totals || []);
  renderMetricGroup("#internal-movement-summary", data.internal_movement_summary || [], [
    { key: "outflow", label: "内部转出" },
    { key: "inflow", label: "内部转入" },
    { key: "transaction_count", label: "涉及笔数" },
  ], "本月暂无账户内资金流动。");
  renderInternalMovements(data.internal_movements || []);
}

async function loadTransactions() {
  const rows = await request("/api/transactions?limit=20");
  const body = document.querySelector("#transactions-body");
  body.replaceChildren();

  if (!rows.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="5" class="empty-cell">暂无流水</td>';
    body.append(row);
    return;
  }

  for (const record of rows) {
    const row = document.createElement("tr");
    const values = [
      record.transaction_date,
      record.transaction_type_label,
      `${record.source_account_name || "外部"} -> ${record.destination_account_name || "外部"}`,
      formatTransactionAmounts(record),
      record.category_name || record.counterparty || "",
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
}

monthInput.addEventListener("change", () => {
  setStatus("");
  loadDashboard().catch((error) => setStatus(error.message, "error"));
});

document.querySelector("#export-button").addEventListener("click", () => {
  window.location.assign(`/api/export.xlsx?month=${encodeURIComponent(monthInput.value)}`);
});

async function initialize() {
  monthInput.value = localMonth();
  monthInput.max = localMonth();
  try {
    await Promise.all([loadDashboard(), loadTransactions()]);
  } catch (error) {
    setStatus(error.message, "error");
  }
}

initialize();

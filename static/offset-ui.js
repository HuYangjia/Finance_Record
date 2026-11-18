/* Shared rendering for event summaries and expandable original records. */
window.OffsetUI = (() => {
  function node(tag, text = '', className = '') {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  async function request(path, options) {
    const response = await fetch(path, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '请检查必填字段和金额格式。');
    return payload;
  }
  function link(id, title) {
    const element = node('a', title || `冲销事项 #${id}`);
    element.href = `/offsets?group=${id}`;
    return element;
  }
  function coverage(value) {
    if(value.funding_issue)return value.funding_issue;
    if (!value.available) return `无法完整换算（${value.missing_currencies.join('、')}）`;
    const c = value.coverage;
    return `原币 ${c.direct} 笔 · 当日汇率 ${c.exact} 笔 · 邻近汇率 ${c.nearest} 笔`;
  }
  function net(entry, currency) {
    const v = entry.valuations[currency];
    const result = node('span', v.available ? `${v.amount} ${currency}` : v.funding_issue?'来源待核实':'无法完整换算');
    result.title = coverage(v);
    if (Number(v.amount) < 0) result.classList.add('negative-amount');
    return result;
  }
  function summary(target, data) {
    target.replaceChildren();
    if (!data?.available) { target.append(node('p', data?.reason || '请选择完整日期范围。', 'empty-notice')); return; }
    target.append(node('p', `${data.entry_count} 条统计记录，涉及 ${data.transaction_count} 笔原始流水；按已关联流水计算。`, 'analysis-summary'));
    window.IncomeAllocations?.renderTrace(target,data);
    const grid = node('div', '', 'metric-grid');
    for (const v of data.valuations) {
      const card = node('article', '', 'metric-item');
      card.append(node('h3', `${v.currency_code} · ${data.budget_complete===false?'预算统计未完整 · ':''}${v.complete ? '汇率完整' : '仅已核实部分'}`));
      for (const [name, amount] of [['收入', v.income], ['支出', v.expense], ['结余', v.balance]]) {
        const row = node('div'); row.append(node('span', name), node('strong', amount)); card.append(row);
      }
      if (!v.complete) card.append(node('p', `未纳入 ${v.excluded_count} 条 · ${v.missing_currencies.join('、')}`, 'error'));
      grid.append(card);
    }
    target.append(grid);
  }
  function members(entry, editTransaction) {
    const details = node('details', '', 'offset-members');
    details.append(node('summary', `展开 ${entry.transaction_count} 笔原始流水`));
    const wrap = node('div', '', 'table-wrap');
    const table = node('table');
    const head = node('tr');
    for (const title of ['日期', '类型', '账户', '金额', '分类', '预算归属', '对象 / 备注', '操作']) head.append(node('th', title));
    const thead = node('thead'); thead.append(head); table.append(thead);
    const body = node('tbody');
    for (const m of entry.members) {
      const row = node('tr');
      const budget = entry.kind === 'income'
        ? (m.transaction_type === 'expense' ? '先冲抵事项收入；净亏损随事项扣减' : '随事项净额分配')
        : (m.transaction_type === 'income' ? '冲减事项消费，不另分配收入' : `随事项承担：${entry.budget_system_name}`);
      for (const text of [m.transaction_date, m.transaction_type === 'expense' ? '支出' : '收入', m.account_name,
        `${m.amount} ${m.currency_code}`, m.category_name, budget, [m.counterparty, m.note].filter(Boolean).join(' · ')]) row.append(node('td', text));
      const action = node('td');
      if (editTransaction) {
        const button = node('button', '编辑原流水', 'table-action'); button.type = 'button';
        button.addEventListener('click', () => editTransaction(m.id)); action.append(button);
      } else {
        const a = node('a', `查看流水 #${m.id}`); a.href = `/accounts?transaction=${m.id}`; action.append(a);
      }
      row.append(action); body.append(row);
    }
    table.append(body); wrap.append(table); details.append(wrap); return details;
  }
  function transactionCell(entry, editTransaction) {
    const member = entry.members[0];
    const cell = node('div', '', 'offset-transaction-entry');
    cell.append(
      node('span', member.transaction_date, 'offset-transaction-date'),
      node('span', member.transaction_type === 'expense' ? '支出' : '收入', 'status-badge'),
      node('strong', member.account_name, 'offset-transaction-account'),
      node('strong', `${member.amount} ${member.currency_code}`, 'offset-transaction-amount'),
      node('span', [member.category_name, member.counterparty, member.note].filter(Boolean).join(' · '), 'offset-transaction-description'),
      node('span', entry.budget_system_name || member.effective_budget_system_name || member.budget_system_name || '未归属', 'status-badge'),
    );
    if(entry.allocation_basis){
      const allocated=node('span','所选预算分配：','offset-amounts');
      allocated.append(net(entry,'CNY'),net(entry,'HKD'));cell.append(allocated);
    }
    const action = node('span', '', 'offset-transaction-action');
    if (editTransaction) {
      const button = node('button', '编辑', 'table-action'); button.type = 'button';
      button.addEventListener('click', () => editTransaction(member.id)); action.append(button);
    } else {
      const original = node('a', `查看流水 #${member.id}`); original.href = `/accounts?transaction=${member.id}`; action.append(original);
    }
    cell.append(action);
    return cell;
  }
  function entryCell(entry, editTransaction) {
    if (entry.entry_type === 'transaction') return transactionCell(entry, editTransaction);
    const cell = node('div', '', 'offset-entry');
    const title = node('div', '', 'offset-entry-title');
    title.append(node('strong', entry.title), node('span', `${entry.statistics_date} · ${entry.category_basis?'所选分类份额':entry.allocation_basis?'所选预算分配':entry.kind === 'expense' ? '净支出' : '净收入'} · ${entry.category_name}`));
    title.append(node('span',(entry.kind === 'income' && entry.budget_role === 'expense' ? '净亏损承担：' : '')+(entry.budget_system_name||'未归属'),'status-badge'));
    if (entry.entry_type === 'offset_group' && entry.entry_id) title.append(link(entry.entry_id, '编辑事项'));
    cell.append(title);
    const amounts = node('div', '', 'offset-amounts');
    amounts.append(net(entry, 'CNY'), net(entry, 'HKD'));
    cell.append(amounts, node('p', entry.raw_totals.map(v => `${v.currency_code}：原始支出 ${v.expense} / 原始收入 ${v.income}`).join('；'), 'analysis-summary'));
    if (entry.category_basis) cell.append(node('p', `上方仅计所选分类；完整事项净额：${Object.entries(entry.whole_valuations).map(([code,v]) => `${v.amount ?? '未完整'} ${code}`).join(' / ')}`, 'analysis-summary'));
    if (entry.category_breakdown) {
      const details = node('details', '', 'offset-members'); details.append(node('summary', '查看分类净额与优惠分摊'));
      details.append(node('p', '共享回款按原支出比例分摊；指定回款只冲减对应支出。金额按各自日期汇率换算，显示尾差统一分配。', 'analysis-summary'));
      const wrap = node('div', '', 'table-wrap'), table = node('table'), head = node('thead'), tr = node('tr'), body = node('tbody');
      for (const label of ['分类', '原支出 CNY', '冲减 CNY', '净支出 CNY', '原支出 HKD', '冲减 HKD', '净支出 HKD']) tr.append(node('th', label));
      head.append(tr); table.append(head);
      for (const row of entry.category_breakdown.CNY) {
        const hkd = entry.category_breakdown.HKD.find(r => r.category_id === row.category_id), line = node('tr');
        for (const value of [row.category_name + (entry.category_basis ? row.selected ? '（本次计入）' : '（未选）' : ''), row.gross, row.deduction, row.amount, hkd.gross, hkd.deduction, hkd.amount]) line.append(node('td', value ?? '未完整'));
        body.append(line);
      }
      table.append(body); wrap.append(table); details.append(wrap); cell.append(details);
    }
    if (entry.note) cell.append(node('p', entry.note));
    for (const [base,v] of Object.entries(entry.valuations)) cell.append(node('small', `${base}：${coverage(v)}`, 'offset-coverage'));
    cell.append(members(entry, editTransaction)); return cell;
  }
  return {node, request, link, net, summary, members, entryCell};
})();

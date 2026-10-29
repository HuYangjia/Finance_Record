window.TermDeposits = (() => {
  const {node, request, button} = BudgetUI;
  const labels = {active: '存续中', soon: '30 天内到期', due: '到期待到账', settled: '已到账'};
  let accounts = [], deposits = [], editor, form, saving = false, callback, record;
  let previewSerial = 0, previewTimer;
  const $ = id => document.getElementById(id);
  const localDate = () => {
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    const part = key => parts.find(p => p.type === key).value;
    return `${part('year')}-${part('month')}-${part('day')}`;
  };
  const liquid = account => account.account_class === 'asset'
    && ['cash','e_wallet','bank_demand','transit_card'].includes(account.account_type);

  function field(title, control, full = false) {
    const label = node('label'); label.append(node('span', title), control);
    if (full) label.classList.add('full-width');
    return label;
  }
  function input(id, type, value = '', attrs = {}) {
    const el = node('input'); el.id = id; el.type = type; el.value = value;
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    return el;
  }
  function select(id, options, value) {
    const el = node('select'); el.id = id;
    for (const [id, title] of options) { const option = node('option', title); option.value = id; el.append(option); }
    el.value = String(value); return el;
  }
  function accountSelect(id, selected, code) {
    const options = accounts.filter(a => liquid(a) && (a.is_active || a.id === selected) && (!code || a.currency_code === code));
    const el = select(id, [['','请选择账户'], ...options.map(a => [a.id, `${a.name} · ${a.currency_code}${a.is_active?'':'（停用）'}`])], selected || '');
    el.required = true; return el;
  }
  function ensureEditor() {
    if (editor) return;
    editor = node('dialog', '', 'app-dialog term-dialog'); editor.id = 'term-deposit-editor';
    form = node('form'); form.id = 'term-deposit-form';
    editor.append(form); document.body.append(editor);
    editor.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
  }
  function editorHeading(title) {
    const heading = node('div', '', 'dialog-heading');
    const close = button('×', () => editor.close(), 'icon-button dialog-close');
    close.setAttribute('aria-label','关闭定存弹窗');
    heading.append(node('h2', title), close); form.append(heading);
  }
  function finishForm(submitLabel, onSubmit) {
    const status = node('p', '', 'dialog-status'); status.id = 'term-editor-status'; status.setAttribute('role','alert');
    const actions = node('div', '', 'form-actions detail-filter-actions');
    const submit = node('button', submitLabel); submit.type = 'submit';
    actions.append(button('取消', () => editor.close()), submit); form.append(status, actions);
    form.onsubmit = async event => {
      event.preventDefault(); if (saving || !form.reportValidity()) return;
      saving = true; form.inert = true; status.textContent = '';
      try {
        const result = await onSubmit();
        editor.close(); await callback?.(result);
        if ($('term-deposit-table')) await load();
      } catch (error) { status.textContent = error.message; status.classList.add('error'); }
      finally { saving = false; form.inert = false; }
    };
  }
  function writePayload() {
    return {
      name: $('term-name').value.trim(), source_account_id: Number($('term-source').value),
      principal: $('term-principal').value, start_date: $('term-start').value,
      term_value: Number($('term-value').value), term_unit: $('term-unit').value,
      annual_rate: $('term-rate').value, day_basis: Number($('term-basis').value),
      maturity_override: $('term-maturity').value || null, note: $('term-note').value.trim(),
      ...(record ? {expected_version: record.version} : {}),
    };
  }
  async function preview() {
    const serial = ++previewSerial;
    const target = $('term-preview'); if (!target) return;
    const payload = writePayload();
    if (!payload.source_account_id || !payload.principal || !payload.start_date || !payload.term_value || payload.annual_rate === '') {
      target.textContent = '填写账户、本金、存入日期、期限与年利率后，自动计算到期日和预期利息。'; return;
    }
    const account = accounts.find(a => a.id === payload.source_account_id);
    $('term-principal-label').textContent = `存入本金（${account?.currency_code || ''}）`;
    const params = new URLSearchParams(Object.entries(payload).filter(([key,value]) => !['name','note','expected_version'].includes(key) && value !== null));
    try {
      const data = await request(`/api/term-deposits/preview?${params}`);
      if (serial !== previewSerial || !editor.open) return;
      target.textContent = `到期日 ${data.maturity_date} · 共 ${data.term_days} 天 · 预期利息 ${data.expected_interest} ${data.currency_code}。按实际天数 / ${payload.day_basis} 单利估算，实际以银行结算为准。`;
    } catch (error) { if (serial === previewSerial) target.textContent = error.message; }
  }

  async function open(deposit = null, onSaved) {
    accounts = await request('/api/accounts?include_inactive=true');
    ensureEditor(); form.replaceChildren(); record = deposit; callback = onSaved;
    editorHeading(deposit ? '修改定期存款' : '存入定期');
    const hint = node('p', '保存后从来源账户扣出本金，转入定存资产。补录历史定存时，请先确认这笔本金没有另记过转账。', 'subtle-note');
    form.append(hint);
    const grid = node('div', '', 'form-grid');
    const source = accountSelect('term-source', deposit?.source_account_id);
    const principal = input('term-principal','number',deposit?.principal || '',{required:'',min:'0.0001',step:'any',inputmode:'decimal'});
    const principalField = field('存入本金',principal); principalField.querySelector('span').id = 'term-principal-label';
    const note = node('textarea'); note.id = 'term-note'; note.maxLength = 500; note.rows = 2; note.value = deposit?.note || '';
    grid.append(field('定存名称',input('term-name','text',deposit?.name || '',{required:'',maxlength:'120',placeholder:'例如：汇丰 3 个月定存'})),
      field('扣款账户 / 币种',source), principalField,
      field('存入日期',input('term-start','date',deposit?.start_date || localDate(),{required:'',max:localDate()})),
      field('存款期限',input('term-value','number',deposit?.term_value || 3,{required:'',min:1,max:36500,step:1})),
      field('期限单位',select('term-unit',[['month','个月'],['day','天']],deposit?.term_unit || 'month')),
      field('预期年利率（%）',input('term-rate','number',deposit?.annual_rate ?? '',{required:'',min:0,max:100,step:'0.000001',placeholder:'例如：3.5'})),
      field('计息年天数',select('term-basis',[[365,'365 天'],[360,'360 天']],deposit?.day_basis || 365)),
      field('银行到期日更正（可选）',input('term-maturity','date',deposit?.maturity_override || '')),
      field('备注',note,true));
    const previewText = node('p','','term-preview'); previewText.id = 'term-preview'; previewText.setAttribute('aria-live','polite');
    form.append(grid, previewText);
    grid.addEventListener('input', () => { clearTimeout(previewTimer); previewTimer = setTimeout(preview,180); });
    source.addEventListener('change', () => {
      const account = accounts.find(a => a.id === Number(source.value));
      if (!$('term-name').value.trim() && account) $('term-name').value = `${account.name}定期`;
      preview();
    });
    finishForm(deposit ? '保存修改' : '确认存入并记账', () => request(
      deposit ? `/api/term-deposits/${deposit.id}` : '/api/term-deposits', writePayload(), deposit ? 'PUT' : 'POST'));
    editor.showModal(); await preview();
  }

  async function settle(deposit) {
    accounts = await request('/api/accounts?include_inactive=true'); ensureEditor();
    form.replaceChildren(); record = deposit; callback = null;
    editorHeading('确认本金与利息到账');
    form.append(node('p', `${deposit.name} · 本金 ${deposit.principal} ${deposit.currency_code} · 约定到期日 ${deposit.maturity_date}`, 'term-preview'),
      node('p','填写银行实际到账的利息，提前全额取出也使用此处，可填 0。保存后本金回流，并单独记一笔利息收入；利息之后可在预算页分配。','subtle-note'));
    const grid = node('div','','form-grid');
    grid.append(field('实际到账日期',input('term-settle-date','date',localDate(),{required:'',min:deposit.start_date,max:localDate()})),
      field('到账账户',accountSelect('term-destination',deposit.source_account_id,deposit.currency_code)),
      field(`实际利息（${deposit.currency_code}）`,input('term-interest','number','',{required:'',min:0,step:'any',inputmode:'decimal'})));
    form.append(grid);
    finishForm('确认到账并记账', () => request(`/api/term-deposits/${deposit.id}/settle`,{
      expected_version:deposit.version,settlement_date:$('term-settle-date').value,
      destination_account_id:Number($('term-destination').value),actual_interest:$('term-interest').value,
    },'POST'));
    editor.showModal();
  }

  async function action(path, deposit, message, method) {
    if (!window.confirm(message)) return;
    try { await request(path,{expected_version:deposit.version},method); await load(); }
    catch (error) { $('term-page-status').textContent = error.message; }
  }
  function render(data) {
    deposits = data.deposits;
    $('term-page-status').textContent = `截至 ${data.as_of_date} · 共 ${data.open_count} 笔存续定存`;
    const summary = $('term-summary'); summary.replaceChildren();
    for (const item of data.summary) {
      const card = node('article','','term-summary-card');
      card.append(node('span',`${item.currency_code} · ${item.count} 笔存续`),node('strong',`${item.principal} ${item.currency_code}`),
        node('p',`30 天内到期及待到账 ${item.due_principal}`),node('small',`存续预期利息 ${item.expected_interest}（未入账）`)); summary.append(card);
    }
    if (!data.summary.length) summary.append(node('p','暂无定存，点击“存入定期”开始记录。','subtle-note'));
    const body = $('term-deposit-body'); body.replaceChildren();
    for (const deposit of deposits) {
      const row = node('tr'); row.dataset.depositId = deposit.id;
      const name = node('td'); name.append(node('strong',deposit.name),node('small',deposit.source_account_name,'term-secondary'));
      const days = deposit.status === 'settled' ? `到账 ${deposit.settlement_date}`
        : deposit.days_remaining < 0 ? `已过 ${-deposit.days_remaining} 天` : deposit.days_remaining === 0 ? '今天到期' : `还有 ${deposit.days_remaining} 天`;
      const dateCell = node('td'); dateCell.append(node('span',deposit.maturity_date),node('small',days,'term-secondary'));
      const status = node('td'); status.append(node('span',labels[deposit.status],`term-status term-status-${deposit.status}`));
      const rateCell = node('td'); rateCell.append(node('span',`${deposit.annual_rate}%`),node('small',`实际天数 / ${deposit.day_basis}`,'term-secondary'));
      const actual = deposit.status === 'settled' ? `${deposit.actual_interest} ${deposit.currency_code}` : '待到账';
      row.append(name,node('td',`${deposit.principal} ${deposit.currency_code}`),node('td',deposit.start_date),
        node('td',`${deposit.term_value} ${deposit.term_unit === 'month'?'个月':'天'}`),dateCell,rateCell,
        node('td',`${deposit.expected_interest} ${deposit.currency_code}`),node('td',actual),status);
      const actions = node('td'); const buttons = node('div','','term-row-actions');
      if (deposit.status !== 'settled') {
        buttons.append(button('确认到账',() => settle(deposit).catch(showError),'table-action'),
          button('修改',() => open(deposit).catch(showError),'table-action'),
          button('删除',() => action(`/api/term-deposits/${deposit.id}`,deposit,'删除录错的定存及存入转账，将恢复来源账户余额。实际取出请使用“确认到账”。确认删除？','DELETE'),'table-action'));
      } else buttons.append(button('撤销到账',() => action(`/api/term-deposits/${deposit.id}/reopen`,deposit,'撤销本金回流和利息收入，恢复存续状态。确认撤销到账？','POST'),'table-action'));
      if (deposit.note) {name.append(node('small',deposit.note,'term-secondary'));}
      actions.append(buttons);row.append(actions);body.append(row);
    }
    if (!deposits.length) { const row = node('tr'), cell = node('td','没有符合筛选条件的定存','empty-cell'); cell.colSpan=10;row.append(cell);body.append(row); }
    $('term-filter-count').textContent = `显示 ${data.total} 笔 · 按到期日排列`;
  }
  function showError(error) { if ($('term-page-status')) $('term-page-status').textContent = error.message; }
  let listSerial = 0;
  async function load() {
    const serial = ++listSerial;
    const params = new URLSearchParams({status:$('term-status-filter').value});
    if ($('term-source-filter').value) params.set('source_account_id',$('term-source-filter').value);
    if ($('term-currency-filter').value) params.set('currency_code',$('term-currency-filter').value);
    const data = await request(`/api/term-deposits?${params}`);
    if (serial === listSerial) render(data);
  }
  async function initializePage() {
    if (!$('term-deposit-table')) return;
    accounts = await request('/api/accounts?include_inactive=true');
    BudgetUI.options($('term-source-filter'),accounts.filter(liquid),'全部扣款账户');
    const codes = [...new Set(accounts.filter(liquid).map(a=>a.currency_code))].sort();
    BudgetUI.options($('term-currency-filter'),codes.map(code=>({code,name:code})),'全部币种');
    $('term-filters').addEventListener('change',()=>load().catch(showError));
    $('new-term-deposit').addEventListener('click',()=>open().catch(showError));
    await load();
    const selected = Number(new URLSearchParams(location.search).get('deposit'));
    if (selected) {
      const all = await request('/api/term-deposits?status=all');
      const deposit = all.deposits.find(d=>d.id===selected);
      if (deposit && deposit.status !== 'settled') await open(deposit);
      else if (deposit) { $('term-status-filter').value='settled';await load(); }
    }
  }
  initializePage().catch(showError);
  return {open};
})();

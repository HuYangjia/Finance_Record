(() => {
  const {node, request, link, entryCell} = window.OffsetUI;
  const $ = (id) => document.getElementById(id);
  const state = {accounts: [], categories: [], budgetConfig: null, group: null, members: new Map(), categoryTargets: new Map(), offset: 0, candidateOffset: 0, selected: new Map(), dirty: false, busy: false};
  let listRequest = 0, candidateRequest = 0;
  function status(id, message, error = false) { $(id).textContent = message; $(id).className = error ? 'dialog-status error' : 'dialog-status'; }
  function options(target, rows, empty = '全部') {
    target.replaceChildren(node('option', empty)); target.firstChild.value = '';
    for (const r of rows) { const option = node('option', r.display_name || r.name); option.value = r.id; target.append(option); }
  }
  function params(form) { const p = new URLSearchParams(); for (const [k,v] of new FormData(form)) if (v) p.set(k,v); return p; }
  function markDirty() { state.dirty = true; $('preview-content').replaceChildren(); renderBudget(); status('editor-status', '有未保存的修改。'); }
  function renderBudget(entry = null) {
    const button = $('allocate-group-income');
    const current = entry || (!state.dirty ? state.group : null);
    const allocations = current?.income_allocations || (!state.dirty ? state.group?.income_allocations : null) || [];
    const hasAllocations = allocations.length > 0;
    button.hidden = !state.group || state.group.kind !== 'income';
    button.disabled = state.dirty || !current || (!hasAllocations && (current.budget_role !== 'income' || Number(current.budget_net_income) <= 0));
    button.textContent = hasAllocations ? '查看 / 修改净收入分配' : '分配净收入';
    let message = '选择流水后预览，查看整件事的预算处理。';
    if (state.dirty) message = '请先预览并保存事项，再分配净收入。';
    if (current) {
      if (current.budget_role === 'unresolved') message = '缺少汇率，无法核实净额；相关预算会标记为待核对。';
      else if (current.budget_role === 'expense') message = `${current.kind === 'income' ? '净亏损' : '净消费 / 退款'}由“${current.budget_system_name}”承担${current.budget_explicit_system_id ? '（事项指定）' : '（继承支出流水）'}。${current.budget_mixed ? '请指定一个承担预算，才能计算完整结余。' : ''}`;
      else message = `净收入 ${current.budget_net_income ?? '待核实'} ${current.budget_reference_currency_code || ''}；${state.dirty ? '保存后核对收入分配' : state.group?.budget_system_name || '尚未分给预算'}。通过右侧按钮按金额分配，成员标签不决定收入分配。`;
      if (state.dirty) message += ' 预览尚未保存。';
      if (allocations.some(a => !a.source_complete)) message += ' 原有收入分配待核对，请打开分配窗口调整。';
    }
    if (!state.budgetConfig) message += ' 预算设置暂未加载，重新打开页面后可修改承担预算。';
    $('offset-budget-status').textContent = message;
  }
  async function allocateIncome(group) {
    if (state.dirty) { status('editor-status', '请先保存事项，再调整净收入分配。', true); return; }
    await window.IncomeAllocations.open('offset_group', group.id || group.entry_id, async () => {
      if (state.group?.id === (group.id || group.entry_id)) openEditor(await request(`/api/offset-groups/${state.group.id}`));
      await loadGroups();
    });
  }
  function discardAllowed() { return !state.dirty || confirm('放弃当前尚未保存的编辑？'); }
  function attribution() {
    const members = [...state.members.values()].sort((a,b) => a.transaction_date.localeCompare(b.transaction_date) || a.id-b.id);
    return {date: members[0]?.transaction_date, primary: members.find(m => m.transaction_type === $('offset-kind').value)};
  }
  function renderMembers() {
    const kind = $('offset-kind').value;
    const split = kind === 'expense' && $('offset-category-mode').value === 'proportional';
    $('offset-category-mode-field').hidden = kind !== 'expense';
    $('offset-category-hint').textContent = kind !== 'expense' ? '收入事项保留最早主流水分类。'
      : split ? '共享优惠按各笔原支出金额比例分摊；回款可指定只冲减某笔支出。保存前可预览分类净额。'
      : '旧方式会把所有净额归到最早支出分类。混合消费可切换为按原分类分摊，再预览保存。';
    const derived = attribution();
    $('offset-attribution').textContent = derived.date
      ? `自动归属：${derived.date} · ${split ? '保留各笔原支出分类' : derived.primary?.category_name || '请加入至少一笔主流水'}`
      : '选择原始流水后自动显示日期和分类。';
    $('primary-label').textContent = `主流水：${kind === 'expense' ? '支出' : '收入'}`;
    $('opposite-label').textContent = `冲抵流水：${kind === 'expense' ? '收入' : '支出'}`;
    for (const side of ['primary','opposite']) {
      const target = $(`${side}-members`); target.replaceChildren();
      const rows = [...state.members.values()].filter(m => (m.transaction_type === kind) === (side === 'primary')).sort((a,b) => a.transaction_date.localeCompare(b.transaction_date) || a.id-b.id);
      if (!rows.length) target.append(node('p', '尚未选择流水', 'analysis-summary'));
      for (const m of rows) {
        const row = node('div', '', 'offset-member-row');
        row.append(node('span', `#${m.id} · ${m.transaction_date} · ${m.account_name} · ${m.amount} ${m.currency_code}`), node('small', [m.category_name,m.counterparty,m.note].filter(Boolean).join(' · ')));
        const actions = node('div', '', 'row-actions');
        if (!split && derived.primary?.id === m.id) actions.append(node('small', '分类取自此笔'));
        if (split && m.transaction_type === 'income') {
          const label = node('label', '回款冲减方式');
          const select = node('select'); select.dataset.categoryRefund = m.id;
          const shared = node('option', '共享：按全部原支出比例'); shared.value = ''; select.append(shared);
          for (const paid of state.members.values()) if (paid.transaction_type === 'expense') {
            const option = node('option', `只冲减 #${paid.id} · ${paid.category_name} · ${paid.amount} ${paid.currency_code}`);
            option.value = paid.id; select.append(option);
          }
          select.value = state.categoryTargets.get(m.id) || '';
          select.onchange = () => { if (select.value) state.categoryTargets.set(m.id, Number(select.value)); else state.categoryTargets.delete(m.id); markDirty(); };
          label.append(select); row.append(label);
        }
        const remove = node('button', '移除关联', 'secondary-button'); remove.type = 'button';
        remove.onclick = () => {
          state.members.delete(m.id); let cleared = 0;
          for (const [refund, paid] of state.categoryTargets) if (refund === m.id || paid === m.id) { state.categoryTargets.delete(refund); cleared++; }
          markDirty(); renderMembers(); if (cleared) status('editor-status', '已移除成员并解除相关指定冲减，请预览后保存。');
        };
        actions.append(remove); row.append(actions); target.append(row);
      }
    }
  }
  async function loadGroups() {
    const serial = ++listRequest;
    const p = params($('group-filter')); p.set('offset',state.offset); p.set('limit',20);
    const data = await request(`/api/offset-groups?${p}`); if (serial !== listRequest) return;
    window.OffsetUI.summary($('group-summary'), data.summary);
    $('group-list').replaceChildren();
    for (const group of data.groups) {
      const card = entryCell(group); const button = node('button', '编辑 / 事项预算', 'secondary-button'); button.type='button';
      button.onclick = () => editGroup(group.id).catch(showError); card.append(button); $('group-list').append(card);
      if (group.kind === 'income' && (group.budget_role === 'income' && Number(group.budget_net_income) > 0 || group.income_allocations?.length)) {
        const allocate = node('button', '分配净收入', 'secondary-button'); allocate.type = 'button';
        allocate.onclick = () => allocateIncome(group).catch(showError); card.append(allocate);
      }
    }
    if (!data.groups.length) $('group-list').append(node('p','没有符合条件的事项。','empty-notice'));
    $('groups-page').textContent = `共 ${data.pagination.total} 项 · 第 ${Math.floor(state.offset/20)+1} 页`;
    $('groups-prev').disabled = state.offset === 0; $('groups-next').disabled = state.offset+20 >= data.pagination.total;
  }
  function showError(error) { status('group-status',error.message,true); }
  function openEditor(group = null) {
    state.group = group; state.members = new Map((group?.members || []).map(m=>[m.id,m])); state.dirty=false;
    state.categoryTargets = new Map((group?.category_targets || []).map(t => [t.refund_transaction_id, t.expense_transaction_id]));
    $('offset-form').reset(); $('offset-kind').value = group?.kind || 'expense';
    $('offset-category-mode').value = group?.category_allocation_mode || 'proportional';
    $('offset-title').value = group?.title || ''; $('offset-note').value=group?.note || '';
    if (state.budgetConfig) {
      options($('offset-budget-system'), state.budgetConfig.systems.filter(s => s.is_active || s.id === group?.budget_explicit_system_id), '自动继承支出流水的预算');
      $('offset-budget-system').value = group?.budget_explicit_system_id || '';
    }
    $('editor-title').textContent=group ? `编辑事项 #${group.id}` : '新建事项'; $('dissolve-group').hidden=!group;
    $('preview-content').replaceChildren(); status('editor-status',''); renderMembers(); renderBudget(group); $('group-editor').hidden=false; $('group-editor').scrollIntoView({behavior:'smooth'}); $('offset-title').focus();
  }
  async function editGroup(id) { if (!discardAllowed()) return; const group=await request(`/api/offset-groups/${id}`); openEditor(group); }
  function payload() {
    if (!$('offset-form').reportValidity()) throw new Error('请填写事项名称。');
    if (!attribution().primary) throw new Error('请至少选择一笔与事项方向一致的主流水。');
    return {title:$('offset-title').value.trim(), kind:$('offset-kind').value, note:$('offset-note').value,
      category_allocation_mode: $('offset-kind').value === 'expense' ? $('offset-category-mode').value : 'anchor',
      category_targets: $('offset-kind').value === 'expense' && $('offset-category-mode').value === 'proportional'
        ? [...state.categoryTargets].map(([refund_transaction_id, expense_transaction_id]) => ({refund_transaction_id, expense_transaction_id})) : [],
      ...(state.budgetConfig ? {budget_system_id:Number($('offset-budget-system').value) || null} : {}),
      transaction_ids:[...state.members.keys()], expected_version:state.group?.version, group_id:state.group?.id};
  }
  async function preview() {
    const body = payload(); const result=await request('/api/offset-groups/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    $('preview-content').replaceChildren(entryCell(result)); renderBudget(result); return body;
  }
  async function save(event) {
    event.preventDefault(); if (state.busy) return; state.busy=true; $('save-group').disabled=true; $('offset-form').inert=true; $('new-group').disabled=true; $('group-list').inert=true;
    try {
      const body = await preview();
      const result = await request(state.group ? `/api/offset-groups/${state.group.id}` : '/api/offset-groups', {method:state.group?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      openEditor(result); status('editor-status','事项已保存，冲销后统计已更新。'); await loadGroups();
    } catch(error) {status('editor-status',error.message,true);} finally {state.busy=false;$('save-group').disabled=false;$('offset-form').inert=false;$('new-group').disabled=false;$('group-list').inert=false;}
  }
  async function loadCandidates() {
    const serial = ++candidateRequest; const p=params($('candidate-filter')); p.set('transaction_type',state.candidateType); p.set('offset',state.candidateOffset); p.set('limit',20);
    const data=await request(`/api/offset-candidates?${p}`); if (serial !== candidateRequest) return;
    const target=$('candidate-list'); target.replaceChildren();
    for(const m of data.transactions) {
      const row=node('div','','offset-member-row'); const label=node('label'); const input=node('input');input.type='checkbox';input.value=m.id;
      const occupied=m.offset_group_id && m.offset_group_id !== state.group?.id;
      input.disabled=Boolean(occupied || state.members.has(m.id));input.checked=state.selected.has(m.id) || state.members.has(m.id);
      input.onchange=()=>{if(input.checked) state.selected.set(m.id,m);else state.selected.delete(m.id);$('add-candidates').textContent=`加入所选（${state.selected.size} 笔）`;};
      label.append(input,node('span',`#${m.id} · ${m.transaction_date} · ${m.account_name} · ${m.amount} ${m.currency_code}`));
      row.append(label,node('small',[m.category_name,m.counterparty,m.note].filter(Boolean).join(' · ')));
      if(occupied) {const a=link(m.offset_group_id,`已属于：${m.offset_group_title}`);a.target='_blank';a.rel='noopener';row.append(a);}
      else if(state.members.has(m.id)) row.append(node('small','已选入当前事项'));
      target.append(row);
    }
    if(!data.transactions.length) target.append(node('p','没有符合条件的流水。'));
    $('candidates-page').textContent=`共 ${data.pagination.total} 笔 · 第 ${Math.floor(state.candidateOffset/20)+1} 页`;
    $('candidates-prev').disabled=state.candidateOffset===0;$('candidates-next').disabled=state.candidateOffset+20>=data.pagination.total;
  }
  async function pick(side) {
    state.candidateType=side==='primary'?$('offset-kind').value:($('offset-kind').value==='expense'?'income':'expense');state.candidateOffset=0;state.selected=new Map();
    $('candidate-filter').reset();options($('candidate-category'),state.categories.filter(c=>c.kind===state.candidateType));
    $('candidate-title').textContent=`选择已有${state.candidateType==='expense'?'支出':'收入'}流水`;status('candidate-status','');$('add-candidates').textContent='加入所选';$('candidate-dialog').showModal();
    try{await loadCandidates();}catch(e){status('candidate-status',e.message,true);}
  }
  $('new-group').onclick=()=>{if(discardAllowed())openEditor();};
  $('group-filter').onsubmit=e=>{e.preventDefault();state.offset=0;loadGroups().catch(showError);};
  $('group-filter').onreset=()=>{state.offset=0;setTimeout(()=>loadGroups().catch(showError),0);};
  $('groups-prev').onclick=()=>{state.offset=Math.max(0,state.offset-20);loadGroups().catch(showError);};
  $('groups-next').onclick=()=>{state.offset+=20;loadGroups().catch(showError);};
  $('offset-form').addEventListener('input',markDirty);$('offset-form').onsubmit=save;
  $('allocate-group-income').onclick=()=>allocateIncome(state.group).catch(e=>status('editor-status',e.message,true));
  $('offset-kind').onchange=()=>{renderMembers();markDirty();};
  $('offset-category-mode').onchange=()=>{renderMembers();markDirty();};
  $('preview-group').onclick=()=>preview().catch(e=>status('editor-status',e.message,true));
  $('cancel-group').onclick=()=>{if(discardAllowed()){$('group-editor').hidden=true;state.dirty=false;}};
  $('dissolve-group').onclick=async()=>{
    if(state.busy || !state.group || !confirm(`解散“${state.group.title}”？原始流水保留，恢复逐笔统计。`))return;
    state.busy=true;
    try{await request(`/api/offset-groups/${state.group.id}`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_version:state.group.version})});state.dirty=false;state.group=null;$('group-editor').hidden=true;await loadGroups();status('group-status','事项已解散，原始流水保留。');}
    catch(e){status('editor-status',e.message,true);}finally{state.busy=false;}
  };
  document.querySelectorAll('[data-pick]').forEach(b=>b.onclick=()=>pick(b.dataset.pick));
  $('close-candidates').onclick=()=>{$('candidate-dialog').close();candidateRequest++;};
  $('candidate-filter').onsubmit=e=>{e.preventDefault();state.candidateOffset=0;loadCandidates().catch(e=>status('candidate-status',e.message,true));};
  for(const [id,step] of [['candidates-prev',-20],['candidates-next',20]])$(id).onclick=()=>{state.candidateOffset=Math.max(0,state.candidateOffset+step);loadCandidates().catch(e=>status('candidate-status',e.message,true));};
  $('add-candidates').onclick=()=>{
    for(const [id,m] of state.selected)state.members.set(id,m);
    markDirty();renderMembers();$('candidate-dialog').close();
  };
  window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});
  async function initialize(){
    const [accounts,expenses,incomes]=await Promise.all([request('/api/accounts?include_inactive=true'),request('/api/categories?kind=expense&include_inactive=true'),request('/api/categories?kind=income&include_inactive=true')]);
    state.accounts=accounts;state.categories=[...expenses,...incomes];
    try { state.budgetConfig=await request('/api/budget/config'); $('offset-budget-system').disabled=false; }
    catch(e) { status('group-status',`预算设置加载失败：${e.message}；仍可维护冲销关联。`,true); }
    for(const id of ['group-account-filter','candidate-account'])options($(id),state.accounts.map(a=>({...a,name:`${a.name} · ${a.currency_code}`})));
    options($('group-category-filter'),state.categories);await loadGroups();
    const id=new URLSearchParams(location.search).get('group');if(id)await editGroup(Number(id));
  }
  initialize().catch(showError);
})();

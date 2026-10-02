(() => {
  const {$,node,request,options,status,button}=BudgetUI;
  let config,overview,scope,plan,identity,chooseIdentity=false,dirty=false,busy=false,transpose=false,storageTarget=null,groups=[];
  const currentMonth=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;};
  const validMonth=value=>/^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value);
  const endpoint=(key=identity)=>`/api/budget/monthly/${key.system}/${key.month}`;
  const money=(n,places)=>{const sign=n<0n?'-':'';const s=(n<0n?-n:n).toString().padStart(places+1,'0');return sign+(places?s.slice(0,-places)+'.'+s.slice(-places):s);};
  function minor(value,places){
    const match=/^(-?)(\d+)(?:\.(\d*))?$/.exec(String(value));
    if(!match||(match[3]||'').length>places)throw new Error('请输入符合币种精度的非负金额。');
    return (match[1]?-1n:1n)*(BigInt(match[2])*10n**BigInt(places)+BigInt(((match[3]||'')+'0'.repeat(places)).slice(0,places)||'0'));
  }
  const sum=(rows,key,places)=>rows.reduce((n,row)=>n+minor(row[key],places),0n);
  function query(value=scope){const q=new URLSearchParams({end_month:value.month,start_month:value.start||'0001-01'});if(value.system)q.set('system_id',value.system);if(value.code)q.set('currency_code',value.code);return q;}
  async function action(fn){
    if(busy)return;busy=true;const editor=$('plan-editor');document.querySelector('main').inert=true;editor.inert=true;
    try{await fn();}catch(e){status(e.message,true);$('plan-error').textContent=e.message;if(editor.open)$('plan-error').scrollIntoView({block:'nearest'});}
    finally{
      busy=false;document.querySelector('main').inert=false;editor.inert=false;
      if(editor.open&&!editor.contains(document.activeElement)&&!document.querySelector('dialog[open]:not(#plan-editor)'))editor.querySelector('input:not(:disabled),select:not(:disabled)')?.focus({preventScroll:true});
    }
  }
  const allowDiscard=()=>!dirty||confirm('当前草稿尚未保存，确定放弃修改？');
  function restoreScope(){if(!scope)return;for(const key of ['system','month','start'])$('plan-'+key).value=scope[key];$('plan-currency').value=scope.code;}
  function closeEditor(){for(const g of groups)g.funding?.dispose();$('plan-editor').close();dirty=false;identity=null;plan=null;groups=[];}
  async function reload(close=true){
    const next={system:$('plan-system').value,month:$('plan-month').value,code:$('plan-currency').value,start:$('plan-start').value};
    try{
      if(!validMonth(next.month))throw new Error('请选择有效的编制月份。');
      if(next.start&&(!validMonth(next.start)||next.start>next.month))throw new Error('累计起始月不能晚于编制月份。');
      const result=await request(`/api/budget/overview?${query(next)}`);
      scope=next;overview=result;if(close)closeEditor();renderOverview();status(`已加载 ${scope.month}，可新增、补充或修改预算。`);
    }catch(e){restoreScope();throw e;}
  }
  function switchMonth(month){action(async()=>{
    if(!allowDiscard()){restoreScope();status('已保留未保存草稿。');return;}
    $('plan-month').value=month;const reset=$('plan-start').value>month;if(reset)$('plan-start').value='';
    await reload();if(reset)status(`已加载 ${scope.month}。累计起始月已清空。`);
  });}
  function shiftMonth(delta){const [year,month]=(scope?.month||currentMonth()).split('-').map(Number),index=year*12+month-1+delta;if(index>=12&&index<120000)switchMonth(`${String(Math.floor(index/12)).padStart(4,'0')}-${String(index%12+1).padStart(2,'0')}`);}
  function renderOverview(){
    $('month-plans-title').textContent=`${scope.month} 用途总预算`;
    $('plan-previous-month').disabled=scope.month==='0001-01';$('plan-next-month').disabled=scope.month==='9999-12';
    const metrics=$('plan-metrics');metrics.replaceChildren();
    for(const block of overview.currencies){
      const card=node('div','','metric-card is-featured');card.append(node('span',`${block.currency_code} · ${scope.month} 总预算`,'metric-label'),node('strong',block.monthly.budget_total),node('small',`已分配 ${block.monthly.allocated} · 待分配 ${block.monthly.unallocated}`,'metric-caption'),node('small',`所选区间累计总预算 ${block.cumulative.budget_total}`,'metric-caption'));metrics.append(card);
    }
    const host=$('month-plans');host.replaceChildren();const rows=overview.plans.filter(p=>p.month===scope.month);
    if(!rows.length)host.append(node('p','这个月还没有预算。点击“新增预案”，选择用途和月份，再填写金额与币种。','plan-empty'));
    else{
      const table=node('table'),head=node('thead'),tr=node('tr');for(const title of ['用途（预算系统）','总预算','已分配存储','待分配','收入来源','操作'])tr.append(node('th',title));head.append(tr);table.append(head);const body=node('tbody');
      for(const id of new Set(rows.map(p=>p.budget_system_id))){const list=rows.filter(p=>p.budget_system_id===id),p=list[0],tr=node('tr');tr.dataset.systemId=id;tr.append(node('td',p.system_name));for(const field of ['budget_total','allocated','unallocated']){const td=node('td');for(const row of list)td.append(node('div',`${row[field]} ${row.currency_code}`,'currency-line'));tr.append(td);}const sourceCell=node('td');sourceCell.append(fundingLabel(p.funding));tr.append(sourceCell);const td=node('td');td.append(button(p.is_active?'编辑整份预案':'查看整份预案',()=>action(()=>openEditor({system:id,month:p.month}))));tr.append(td);body.append(tr);}table.append(body);host.append(table);
    }
    renderMatrix();renderExecution();
  }
  function fundingLabel(source){
    const host=node('div','','plan-funding-label');
    if(!source){host.append(node('span','未绑定来源','subtle-note'));return host;}
    const link=node('a',source.title);link.href=source.url;link.target='_blank';link.rel='noopener';host.append(link);
    host.append(node('small',source.status,source.reason||source.overallocated?'funding-warning':'subtle-note'));return host;
  }
  function executionTable(rows,history=false){
    const table=node('table'),head=node('thead'),tr=node('tr');
    for(const title of [history?'月份':'预算','币种','上月结余','本月新增','本月可用','净支出','结转余额','状态',...(history?[]:['操作'])])tr.append(node('th',title));
    head.append(tr);table.append(head);const body=node('tbody');
    for(const row of rows){
      const tr=node('tr');tr.dataset.systemId=row.budget_system_id;tr.dataset.month=row.month;
      tr.append(node('th',history?row.month:row.system_name),node('td',row.currency_code||'未设置'));
      for(const key of ['opening','added','available','expense','closing']){const td=node('td',row[key]??(row.started?'未完整':'—'));td.dataset.amount=key;if(row[key]?.startsWith('-'))td.classList.add('is-negative');tr.append(td);}
      const state=node('td');state.append(node('div',row.reason||(!row.complete?'结余未完整计算':row.compiled?'已编制':'未编制 · 新增按零')));
      if(row.started)state.append(node('small',`起算 ${row.origin_month}`));
      if(row.nearest_count||row.prior_nearest_count)state.append(node('div','含邻近日期汇率','subtle-note'));
      for(const warning of row.funding_warnings||[])state.append(node('div',`${warning.month} · ${warning.title}：${warning.status}`,'funding-warning'));
      for(const warning of row.warnings){const text=`${warning.month} · ${warning.title}：${warning.reason}`;if(warning.entry_type==='offset_group'){const link=node('a',text);link.href=`/offsets?group=${warning.entry_id}`;state.append(link);}else state.append(node('div',text));}
      tr.append(state);
      if(!history){const actions=node('td');actions.append(button('结转历史',()=>action(()=>showExecutionHistory(row))),button(row.compiled?'编辑计划':'编制计划',()=>action(()=>openEditor({system:row.budget_system_id,month:row.month}))));tr.append(actions);}
      body.append(tr);
    }
    table.append(body);return table;
  }
  function renderExecution(){
    const host=$('budget-execution');host.replaceChildren();const rows=overview.execution?.rows||[];
    if(rows.length)host.append(executionTable(rows));else host.append(node('p','所选范围暂无预算系统。','plan-empty'));
  }
  async function showExecutionHistory(row){
    const [year,month]=row.month.split('-').map(Number),index=Math.max(12,year*12+month-1-11),start=`${String(Math.floor(index/12)).padStart(4,'0')}-${String(index%12+1).padStart(2,'0')}`;
    const data=await request(`/api/budget/execution?system_id=${row.budget_system_id}&start_month=${start}&end_month=${row.month}`);
    $('budget-detail-title').textContent=`${row.system_name} · 结转历史`;
    const host=$('budget-detail-content');host.replaceChildren(node('p','显示截至所选月的最近 12 个月。更早结余自动承接；修改历史计划、流水或汇率后重新计算。','subtle-note'));
    const wrap=node('div','','table-wrap');wrap.append(executionTable(data.rows,true));host.append(wrap);$('budget-detail-dialog').showModal();
  }
  function showDetails(title,plans,items){
    $('budget-detail-title').textContent=title;const host=$('budget-detail-content');host.replaceChildren();
    for(const p of plans){const row=node('p',`${p.month} · ${p.system_name} · 总预算 ${p.budget_total} ${p.currency_code} · 待分配 ${p.unallocated} `);row.append(button('编辑计划',()=>action(async()=>{if(await openEditor({system:p.budget_system_id,month:p.month,code:p.currency_code}))$('budget-detail-dialog').close();})));host.append(row);}
    for(const i of items)host.append(node('p',`${i.month} · ${i.system_name} · ${i.storage_path} · ${i.amount} ${i.currency_code}${i.title?' · '+i.title:''}${i.note?' · '+i.note:''}${i.legacy_purpose?' · 历史用途：'+i.legacy_purpose:''}`));
    if(!plans.length)host.append(node('p','尚未编制计划。'));$('budget-detail-dialog').showModal();
  }
  function renderMatrix(){
    if(!overview)return;const host=$('budget-matrix');host.replaceChildren();const cumulative=$('matrix-scope').value==='cumulative',root=$('matrix-level').value==='root';
    const all=overview.plans.filter(p=>cumulative||p.month===scope.month);
    for(const code of [...new Set(all.map(p=>p.currency_code))]){
      const ps=all.filter(p=>p.currency_code===code),items=ps.flatMap(p=>p.items),places=overview.currencies.find(c=>c.currency_code===code).decimal_places;
      const storageKey=i=>root?i.storage_root:i.storage_path;
      const columns=[...new Set(items.map(storageKey))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
      const table=node('table'),head=node('tr');for(const title of ['用途（预算系统）',...columns,'待分配','总预算'])head.append(node('th',title));const thead=node('thead');thead.append(head);table.append(thead);const body=node('tbody');
      for(const system of config.systems.filter(s=>!scope.system||s.id===Number(scope.system))){
        const selected=ps.filter(p=>p.budget_system_id===system.id),si=items.filter(i=>i.budget_system_id===system.id);if(!selected.length&&(!system.is_active||system.currency_code!==code))continue;
        const tr=node('tr');tr.append(node('th',system.name));
        for(const column of columns){const matched=si.filter(i=>storageKey(i)===column),td=node('td');td.append(button(matched.length?money(sum(matched,'amount',places),places):'—',()=>showDetails(`${system.name} × ${column} · ${code}`,selected,matched),'matrix-amount'));tr.append(td);}
        tr.append(node('td',selected.length?money(sum(selected,'unallocated',places),places):'—'));const total=node('td');total.append(button(selected.length?money(sum(selected,'budget_total',places),places):'未编制',()=>showDetails(`${system.name} · ${code}`,selected,si),'matrix-amount'));tr.append(total);body.append(tr);
      }
      const foot=node('tr');foot.append(node('th','合计'));for(const column of columns)foot.append(node('td',money(sum(items.filter(i=>storageKey(i)===column),'amount',places),places)));foot.append(node('td',money(sum(ps,'unallocated',places),places)),node('th',money(sum(ps,'budget_total',places),places)));body.append(foot);table.append(body);
      if(transpose){const cells=[...table.rows].map(r=>[...r.cells]);table.replaceChildren();const body=node('tbody');for(let col=0;col<cells[0].length;col++){const tr=node('tr');for(let row=0;row<cells.length;row++){const cell=node(row===0||col===0?'th':'td');cell.append(...cells[row][col].childNodes);tr.append(cell);}body.append(tr);}table.append(body);}
      const wrap=node('div','','table-wrap');wrap.append(table);host.append(node('h3',code),wrap);
    }
    if(!all.length)host.append(node('p','所选范围暂无已保存预算。','plan-empty'));
    $('matrix-caption').textContent=`${cumulative?(scope.start||'全部历史')+' 至 ':''}${scope.month} · 只统计已保存计划。总预算＝存储分配＋待分配；点击金额查看来源。`;
  }
  function setIdentityControls(){options($('entry-system'),config.systems.filter(s=>s.is_active||s.id===Number(identity.system)),null,identity.system);$('entry-month').value=identity.month;}
  async function openEditor(key,selectable=false){
    if(!allowDiscard())return false;
    const [next,cfg]=await Promise.all([request(endpoint(key)),request('/api/budget/config')]);
    config=cfg;identity={system:key.system,month:key.month};plan=next;chooseIdentity=selectable;renderEditor();if(!$('plan-editor').open)$('plan-editor').showModal();$('plan-editor').querySelector('.plan-editor-scroll').scrollTop=0;return true;
  }
  function storageOptions(selected){return config.storage.filter(s=>(s.selectable&&!s.is_placeholder)||s.id===Number(selected));}
  function input(type,value,maxLength){const el=node('input');el.type=type;el.value=value??'';if(maxLength)el.maxLength=maxLength;return el;}
  function field(label,control){const el=node('label',label);el.append(control);return el;}
  const places=g=>config.currencies.find(c=>c.code===g.code).decimal_places;
  function draftItems(g){return [...g.items.rows].map(tr=>{const item={};if(tr.dataset.id)item.id=Number(tr.dataset.id);for(const el of tr.querySelectorAll('[data-field]'))item[el.dataset.field]=el.tagName==='SELECT'?Number(el.value):el.value;return item;});}
  function refreshCurrencyOptions(){
    const system=config.systems.find(s=>s.id===Number(identity.system));
    for(const g of groups){options(g.currency,config.currencies.filter(c=>c.is_active||c.code===g.code),null,g.code);g.currency.disabled=!system?.is_active||system.currency_conflict;}
    $('change-currency').disabled=!system?.is_active||system.currency_conflict;
  }
  function addGroup(saved=null){
    const currency=saved?.currency_code||plan.currency_code||'CNY';
    if(!currency)return;
    const g={code:currency,saved:saved||{id:null,version:0,items:[],note:''},el:node('div','','currency-budget'),currency:node('select','','currency-select'),total:input('number',saved?.total??(saved?.id?saved.allocated:'')),note:input('text',saved?.note||'',500),items:node('tbody','','allocation-items'),balance:node('strong','','allocation-balance'),empty:node('p','可以先保存总预算，再分配存储方式。','plan-empty'),caption:node('p','','subtle-note')};
    g.el.dataset.currency=currency;g.total.className='currency-total';g.note.className='currency-note';g.total.min='0';g.total.required=true;g.currency.required=true;
    const header=node('div','','currency-budget-heading'),amount=node('div','','currency-amount-input');amount.append(field('总预算金额',g.total),field('币种',g.currency));header.append(amount);
    const version=node('span',saved?.version?`${currency} · 已保存版本 ${saved.version}`:'新增金额','plan-state');version.dataset.role='version';header.append(version);
    g.el.append(header,field('本月计划说明（可选）',g.note));
    g.funding=BudgetFunding.create(g.el,saved?.funding||null,()=>({planId:g.saved.id,code:g.code,total:g.total.value}),changed);
    if(saved?.id&&saved.total===null)g.el.append(node('p','旧计划未填总额，暂按原明细合计展示，确认后保存。','subtle-note'));
    const heading=node('div','','allocation-heading');heading.append(node('h3','二级存储分配'),g.balance);g.balance.setAttribute('role','status');g.el.append(heading,g.caption);
    const table=node('table'),head=node('thead'),tr=node('tr');for(const title of ['存储方式','分配金额','明细名称（可选）','备注（可选）','操作'])tr.append(node('th',title));head.append(tr);table.append(head,g.items);const wrap=node('div','','table-wrap plan-items-wrap');wrap.append(table);g.el.append(wrap,g.empty,button('＋分配存储方式',()=>{addRow(g);changed();},'secondary-button add-allocation'));
    groups.push(g);$('currency-budgets').append(g.el);
    g.currency.onchange=()=>{
      const code=g.currency.value;
      if(plan.currency_code){g.currency.value=g.code;action(()=>changeCurrency(code));return;}
      g.code=code;g.el.dataset.currency=code;updateGroup(g);refreshCurrencyOptions();changed();g.funding.schedule();
    };
    g.total.oninput=()=>{changed();g.funding.schedule();};g.note.oninput=changed;
    for(const item of saved?.items||[])addRow(g,item);updateGroup(g);refreshCurrencyOptions();return g;
  }
  function addRow(g,item={}){
    const tr=node('tr');if(item.id)tr.dataset.id=item.id;
    const select=node('select');select.dataset.field='storage_method_id';select.required=true;select.setAttribute('aria-label','存储方式');options(select,storageOptions(item.storage_method_id),'选择存储方式',item.storage_method_id);
    const amount=input('number',item.amount);amount.min='0';amount.step=String(10**-places(g));amount.required=true;amount.dataset.field='amount';amount.setAttribute('aria-label','分配金额');
    const title=input('text',item.title,120);title.dataset.field='title';title.setAttribute('aria-label','明细名称');const note=input('text',item.note,500);note.dataset.field='note';note.setAttribute('aria-label','备注');
    for(const el of [select,amount,title,note,button('移除',()=>{tr.remove();changed();})]){const td=node('td');td.append(el);if(el===select)td.append(button('＋新增',()=>openStorage(select),'matrix-amount'));if(el===title&&item.purpose_id&&!config.purposes.find(p=>p.id===item.purpose_id)?.is_placeholder)td.append(node('small',`历史用途：${item.purpose_path||''}`));tr.append(td);}
    tr.addEventListener('input',changed);tr.addEventListener('change',changed);g.items.append(tr);updateGroup(g);
  }
  function changed(){dirty=true;$('plan-state').textContent=`${plan.system_name} · ${identity.month} · 有未保存修改`;$('plan-state').classList.add('is-dirty');$('plan-error').textContent='';for(const g of groups)updateGroup(g);}
  function updateGroup(g){
    g.total.step=String(10**-places(g));for(const el of g.items.querySelectorAll('[data-field=amount]'))el.step=g.total.step;
    g.caption.textContent=`以下分配均为 ${g.code}，自动使用上方金额的币种。`;
    g.empty.hidden=g.items.rows.length>0;
    try{const total=minor(g.total.value||'0',places(g)),allocated=sum(draftItems(g).map(i=>({...i,amount:i.amount||'0'})),'amount',places(g)),gap=total-allocated;g.balance.textContent=`已分配 ${money(allocated,places(g))} · ${gap<0n?'超出总预算':'待分配'} ${money(gap<0n?-gap:gap,places(g))} ${g.code}`;g.balance.classList.toggle('is-over-budget',gap<0n);}catch(e){g.balance.textContent=e.message;}
  }
  function renderEditor(){
    for(const g of groups)g.funding?.dispose();setIdentityControls();$('currency-budgets').replaceChildren();groups=[];
    for(const p of plan.plans)addGroup(p);if(!groups.length)addGroup();dirty=false;
    $('plan-state').textContent=`${plan.system_name} · ${identity.month} · ${plan.plans.length?'已保存 · '+(plan.currency_code||'旧多币种待整理'):'尚未编制'}`;$('plan-state').classList.remove('is-dirty');
    $('plan-editor-title').textContent=`${identity.month} · ${plan.system_name}`;$('save-plan').textContent='保存并关闭';$('plan-error').textContent='';
    const system=config.systems.find(s=>s.id===Number(identity.system)),inactive=!system?.is_active||system.currency_conflict;
    for(const control of $('plan-form').querySelectorAll('input,select,button'))control.disabled=inactive&&!['close-plan','plan-history'].includes(control.id);
    for(const id of ['save-plan','add-storage'])$(id).disabled=inactive;
    for(const id of ['entry-system','entry-month'])$(id).disabled=!chooseIdentity||inactive;
    refreshCurrencyOptions();$('copy-plan').disabled=inactive;
    $('plan-identity-hint').textContent=system.currency_conflict?'旧版计划包含多种币种，原金额与历史已完整保留，暂只供查看，请先确认整理方式。':'同一预算的所有月份使用一种币种。修改币种会先预览全部月份，数字保持不变；实际消费可用其他币种，比较时再折算。';
  }
  async function changeCurrency(code){
    if(dirty)throw new Error('请先保存或取消当前草稿，再修改预算币种。');
    await BudgetCurrency.open(Number(identity.system),config,async next=>{
      config=next;await openEditor(identity,chooseIdentity);
      if($('plan-currency').value)$('plan-currency').value=plan.currency_code;
      await reload(false);status('预算币种已更正，各月数字保持不变。可以继续修改金额。');
    },code);
  }
  function openStorage(target=null){if(!identity)return;storageTarget=target;$('new-dimension-name').value='';$('storage-error').textContent='';options($('new-dimension-parent'),config.storage.filter(s=>s.is_active&&!s.is_placeholder),'顶级栏目','');$('storage-dialog').showModal();}
  $('storage-form').onsubmit=e=>{e.preventDefault();if(busy)return;action(async()=>{
    $('storage-form').inert=true;
    try{const ids=new Set(config.storage.map(s=>s.id));config=await request('/api/budget/config/storage',{expected_version:config.version,budget_system_id:Number(identity.system),name:$('new-dimension-name').value,parent_id:Number($('new-dimension-parent').value)||null},'POST');const created=config.storage.find(s=>!ids.has(s.id));for(const select of $('currency-budgets').querySelectorAll('[data-field=storage_method_id]')){const selected=select.value;options(select,storageOptions(selected),'选择存储方式',select===storageTarget?created.id:selected);}if(storageTarget)changed();$('storage-dialog').close();status('存储栏目已创建，预算草稿已保留。');}catch(e){$('storage-error').textContent=e.message;throw e;}finally{$('storage-form').inert=false;}
  });};
  $('cancel-storage').onclick=()=>$('storage-dialog').close();$('add-storage').onclick=()=>openStorage();
  $('new-budget').onclick=()=>action(()=>openEditor({system:scope.system||config.systems.find(s=>s.is_active)?.id,month:scope.month},true));
  for(const id of ['entry-system','entry-month'])$(id).onchange=()=>action(async()=>{
    const key={system:$('entry-system').value,month:$('entry-month').value};
    try{if(!validMonth(key.month))throw new Error('请选择有效的预算月份。');if(!await openEditor(key,true))setIdentityControls();}catch(e){setIdentityControls();throw e;}
  });
  const dismissEditor=()=>{if(busy)return;if(allowDiscard())closeEditor();else status('已保留未保存草稿。');};
  $('close-plan').onclick=dismissEditor;$('dismiss-plan').onclick=dismissEditor;
  $('plan-editor').addEventListener('cancel',e=>{e.preventDefault();dismissEditor();});
  $('change-currency').onclick=()=>action(()=>changeCurrency());
  $('plan-form').onsubmit=e=>{e.preventDefault();action(async()=>{
    if(groups.length!==1)throw new Error('一个预算只能使用一种币种。');
    const plans=[];for(const g of groups){const items=draftItems(g),total=g.total.value,allocated=sum(items,'amount',places(g));if(allocated>minor(total,places(g)))throw new Error(`${g.code}：存储分配合计不能超过总预算，请调整总额或分配金额。`);plans.push({currency_code:g.code,expected_version:g.saved.version,total,note:g.note.value,items,funding:await g.funding.payload()});}
    const saved=await request(endpoint(),{plans},'PUT'),savedIdentity={...identity};dirty=false;plan=saved;chooseIdentity=false;closeEditor();
    $('plan-month').value=savedIdentity.month;if($('plan-start').value>savedIdentity.month)$('plan-start').value='';if($('plan-system').value&&Number($('plan-system').value)!==Number(savedIdentity.system))$('plan-system').value='';
    try{config=await request('/api/budget/config');await reload();status(`${savedIdentity.month} · ${saved.system_name} 预案已保存，预算币种为 ${saved.currency_code}。`);}catch(e){throw new Error(`预案已保存，但概览刷新失败：${e.message}。请重新加载页面，勿重复新增。`);}
  });};
  $('copy-plan').onclick=()=>action(async()=>{
    const month=$('copy-month').value;if(!validMonth(month)||month===identity.month)throw new Error('请选择另一个有效的来源月份。');
    const source=await request(endpoint({...identity,month}));if(!source.plans.length)throw new Error('来源月份尚未编制计划。');
    if(source.plans.length!==1||source.currency_code!==plan.currency_code)throw new Error('预算币种已改变或存在旧多币种计划，请重新加载。');
    if(!confirm(`复制 ${month} 的总预算及存储分配，替换当前草稿？保存后生效。`))return;
    const p=source.plans[0],g=groups[0];g.total.value=p.total??p.allocated;g.note.value=p.note;g.items.replaceChildren();for(const i of p.items)addRow(g,{...i,id:undefined,storage_method_id:config.storage.find(s=>s.id===i.storage_method_id&&s.selectable&&!s.is_placeholder)?i.storage_method_id:undefined});
    changed();g.funding.schedule();status('已复制为草稿。请检查总额及存储分配后保存。');
  });
  $('plan-history').onclick=()=>action(async()=>{
    const histories=await Promise.all(plan.plans.map(async p=>({code:p.currency_code,rows:await request(`/api/budget/plans/${identity.system}/${identity.month}/${p.currency_code}/revisions`)})));
    $('budget-detail-title').textContent=`${identity.month} · ${plan.system_name} 历史版本`;const host=$('budget-detail-content');host.replaceChildren();
    for(const h of histories){for(const r of h.rows){const detail=node('details'),p=r.plan;detail.append(node('summary',`版本 ${r.version} · ${r.created_at} · 总预算 ${p.total??p.allocated} ${p.currency_code} · 存储分配 ${p.allocated}`));detail.append(node('p',p.funding?`收入来源：${p.funding.title} · 保存时净收入 ${p.funding.net??'未完整'} ${p.funding.currency_code} · ${p.funding.status}`:'收入来源：未绑定'));for(const i of p.items)detail.append(node('p',`${i.storage_path} · ${i.amount} ${p.currency_code} · ${i.title} · ${i.note}`));host.append(detail);}}if(!histories.length)host.append(node('p','尚无保存版本。'));$('budget-detail-dialog').showModal();
  });
  $('budget-load').onsubmit=e=>{e.preventDefault();action(async()=>{if(allowDiscard())await reload();else restoreScope();});};
  $('plan-month').onchange=()=>switchMonth($('plan-month').value);$('plan-previous-month').onclick=()=>shiftMonth(-1);$('plan-next-month').onclick=()=>shiftMonth(1);$('plan-current-month').onclick=()=>switchMonth(currentMonth());
  for(const id of ['plan-system','plan-currency','plan-start'])$(id).onchange=()=>status('点击“加载所选计划”应用筛选。');
  $('budget-export').onclick=()=>{if(scope)location.href=`/api/budget/overview.xlsx?${query()}`;};$('matrix-scope').onchange=renderMatrix;$('matrix-level').onchange=renderMatrix;$('matrix-transpose').onclick=()=>{transpose=!transpose;renderMatrix();};
  document.querySelector('[data-budget-close]').onclick=()=>$('budget-detail-dialog').close();window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  $('plan-form').prepend(Object.assign(node('p'),{id:'plan-error',role:'alert',className:'dialog-status error'}));
  $('plan-month').value=currentMonth();action(async()=>{config=await request('/api/budget/config');options($('plan-system'),config.systems,'全部用途','');options($('plan-currency'),config.currencies,'全部币种','');await reload();});
})();

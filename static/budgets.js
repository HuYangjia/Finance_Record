(() => {
  const {$,node,request,options,status,button}=BudgetUI;
  let config,overview,scope,plan,identity,chooseIdentity=false,dirty=false,busy=false,transpose=false,storageTarget=null;
  const currentMonth=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;};
  const validMonth=value=>/^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value);
  const endpoint=(key=identity)=>`/api/budget/plans/${key.system}/${key.month}/${key.code}`;
  const money=(n,places)=>{const sign=n<0n?'-':'';const s=(n<0n?-n:n).toString().padStart(places+1,'0');return sign+(places?s.slice(0,-places)+'.'+s.slice(-places):s);};
  function minor(value,places){
    const match=/^(-?)(\d+)(?:\.(\d*))?$/.exec(String(value));
    if(!match||(match[3]||'').length>places)throw new Error('请输入符合币种精度的非负金额。');
    return (match[1]?-1n:1n)*(BigInt(match[2])*10n**BigInt(places)+BigInt(((match[3]||'')+'0'.repeat(places)).slice(0,places)||'0'));
  }
  const sum=(rows,key,places)=>rows.reduce((n,row)=>n+minor(row[key],places),0n);
  function query(value=scope){const q=new URLSearchParams({end_month:value.month,start_month:value.start||'0001-01'});if(value.system)q.set('system_id',value.system);if(value.code)q.set('currency_code',value.code);return q;}
  async function action(fn){if(busy)return;busy=true;document.querySelector('main').inert=true;try{await fn();}catch(e){status(e.message,true);$('plan-error').textContent=e.message;}finally{busy=false;document.querySelector('main').inert=false;}}
  const allowDiscard=()=>!dirty||confirm('当前草稿尚未保存，确定放弃修改？');
  function restoreScope(){if(!scope)return;for(const key of ['system','month','start'])$('plan-'+key).value=scope[key];$('plan-currency').value=scope.code;}
  function closeEditor(){$('plan-editor').hidden=true;dirty=false;identity=null;plan=null;}
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
    if(!rows.length)host.append(node('p','这个月还没有预算。点击“新增预算”，选择用途、币种并填写总额。','plan-empty'));
    else{
      const table=node('table'),head=node('thead'),tr=node('tr');for(const title of ['用途（预算系统）','币种','总预算','已分配存储','待分配','操作'])tr.append(node('th',title));head.append(tr);table.append(head);const body=node('tbody');
      for(const p of rows){const tr=node('tr');tr.dataset.planId=p.id;for(const text of [p.system_name,p.currency_code,p.budget_total,p.allocated,p.unallocated])tr.append(node('td',text));const td=node('td');td.append(button(p.is_active?'修改总额 / 存储分配':'查看计划',()=>action(()=>openEditor({system:p.budget_system_id,month:p.month,code:p.currency_code}))));tr.append(td);body.append(tr);}table.append(body);host.append(table);
    }
    renderMatrix();
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
        const selected=ps.filter(p=>p.budget_system_id===system.id),si=items.filter(i=>i.budget_system_id===system.id);if(!system.is_active&&!selected.length)continue;
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
  function setIdentityControls(){options($('entry-system'),config.systems.filter(s=>s.is_active||s.id===Number(identity.system)),null,identity.system);options($('entry-currency'),config.currencies,null,identity.code);$('entry-month').value=identity.month;}
  async function openEditor(key,selectable=false){
    if(!allowDiscard())return false;
    const [next,cfg]=await Promise.all([request(endpoint(key)),request('/api/budget/config')]);
    config=cfg;identity={...key};plan=next;chooseIdentity=selectable;renderEditor();$('plan-editor').hidden=false;$('plan-editor').scrollIntoView({behavior:'smooth',block:'start'});return true;
  }
  function storageOptions(selected){return config.storage.filter(s=>s.budget_system_id===Number(identity.system)&&((s.selectable&&!s.is_placeholder)||s.id===Number(selected)));}
  function input(type,value,maxLength){const el=node('input');el.type=type;el.value=value??'';if(maxLength)el.maxLength=maxLength;return el;}
  function addRow(item={}){
    const tr=node('tr');if(item.id)tr.dataset.id=item.id;
    const select=node('select');select.dataset.field='storage_method_id';select.required=true;select.setAttribute('aria-label','存储方式');options(select,storageOptions(item.storage_method_id),'选择存储方式',item.storage_method_id);
    const amount=input('number',item.amount);amount.min='0';amount.step=String(10**-plan.decimal_places);amount.required=true;amount.dataset.field='amount';amount.setAttribute('aria-label','分配金额');
    const title=input('text',item.title,120);title.dataset.field='title';title.setAttribute('aria-label','明细名称');const note=input('text',item.note,500);note.dataset.field='note';note.setAttribute('aria-label','备注');
    for(const el of [select,amount,title,note,button('移除',()=>{tr.remove();changed();})]){const td=node('td');td.append(el);if(el===select)td.append(button('＋新增',()=>openStorage(select),'matrix-amount'));if(el===title&&item.purpose_id&&!config.purposes.find(p=>p.id===item.purpose_id)?.is_placeholder)td.append(node('small',`历史用途：${item.purpose_path||''}`));tr.append(td);}
    tr.addEventListener('input',changed);tr.addEventListener('change',changed);$('plan-items').append(tr);updateBalance();
  }
  function draft(){return [...$('plan-items').rows].map(tr=>{const item={};if(tr.dataset.id)item.id=Number(tr.dataset.id);for(const el of tr.querySelectorAll('[data-field]'))item[el.dataset.field]=el.tagName==='SELECT'?Number(el.value):el.value;return item;});}
  function changed(){dirty=true;$('plan-state').textContent=`${plan.system_name} · ${identity.month} · ${identity.code} · 有未保存修改`;$('plan-state').classList.add('is-dirty');$('plan-error').textContent='';updateBalance();}
  function updateBalance(){
    $('plan-empty').hidden=$('plan-items').rows.length>0;
    if(!plan)return;
    try{const total=minor($('plan-total').value||'0',plan.decimal_places),allocated=sum(draft().map(i=>({...i,amount:i.amount||'0'})),'amount',plan.decimal_places),gap=total-allocated;$('allocation-balance').textContent=`已分配 ${money(allocated,plan.decimal_places)} · ${gap<0n?'超出总预算':'待分配'} ${money(gap<0n?-gap:gap,plan.decimal_places)} ${identity.code}`;$('allocation-balance').classList.toggle('is-over-budget',gap<0n);}catch(e){$('allocation-balance').textContent=e.message;}
  }
  function renderEditor(){
    setIdentityControls();$('plan-items').replaceChildren();$('plan-total').step=String(10**-plan.decimal_places);$('plan-total').value=plan.total??(plan.id?plan.allocated:'');$('plan-note').value=plan.note;
    for(const item of plan.items)addRow(item);updateBalance();dirty=false;
    $('plan-state').textContent=`${plan.system_name} · ${identity.month} · ${identity.code} · ${plan.version?'已保存版本 '+plan.version:'尚未编制'}`;$('plan-state').classList.remove('is-dirty');
    $('plan-editor-title').textContent=`${identity.month} · ${plan.system_name}`;$('save-plan').textContent=`保存 ${identity.month} 计划`;$('plan-error').textContent='';
    const inactive=!config.systems.find(s=>s.id===Number(identity.system))?.is_active;
    for(const control of $('plan-form').querySelectorAll('input,select,button'))control.disabled=inactive&&!['close-plan','plan-history'].includes(control.id);
    for(const id of ['entry-system','entry-month','entry-currency'])$(id).disabled=!chooseIdentity||inactive;
    $('copy-plan').disabled=inactive;
    $('plan-identity-hint').textContent=(chooseIdentity?'直接选择已有预算系统作为用途；同月同币种已有计划时会打开原计划。':'正在修改已保存计划。新增其他用途或币种，请点击上方“新增预算”。')+(plan.id&&plan.total===null?' 这份旧计划未填总额，暂按原明细合计展示，确认后保存。':'');
  }
  function openStorage(target=null){if(!identity)return;storageTarget=target;$('new-dimension-name').value='';$('storage-error').textContent='';options($('new-dimension-parent'),config.storage.filter(s=>s.budget_system_id===Number(identity.system)&&s.is_active&&!s.is_placeholder),'顶级栏目','');$('storage-dialog').showModal();}
  $('storage-form').onsubmit=e=>{e.preventDefault();if(busy)return;action(async()=>{
    $('storage-form').inert=true;
    try{const ids=new Set(config.storage.map(s=>s.id));config=await request('/api/budget/config/storage',{expected_version:config.version,budget_system_id:Number(identity.system),name:$('new-dimension-name').value,parent_id:Number($('new-dimension-parent').value)||null},'POST');const created=config.storage.find(s=>!ids.has(s.id));for(const select of $('plan-items').querySelectorAll('[data-field=storage_method_id]')){const selected=select.value;options(select,storageOptions(selected),'选择存储方式',select===storageTarget?created.id:selected);}if(storageTarget)changed();$('storage-dialog').close();status('存储栏目已创建，预算草稿已保留。');}catch(e){$('storage-error').textContent=e.message;throw e;}finally{$('storage-form').inert=false;}
  });};
  $('cancel-storage').onclick=()=>$('storage-dialog').close();$('add-storage').onclick=()=>openStorage();
  $('new-budget').onclick=()=>action(()=>openEditor({system:scope.system||config.systems.find(s=>s.is_active)?.id,month:scope.month,code:scope.code||'CNY'},true));
  for(const id of ['entry-system','entry-month','entry-currency'])$(id).onchange=()=>action(async()=>{
    const key={system:$('entry-system').value,month:$('entry-month').value,code:$('entry-currency').value};
    try{if(!validMonth(key.month))throw new Error('请选择有效的预算月份。');if(!await openEditor(key,true))setIdentityControls();}catch(e){setIdentityControls();throw e;}
  });
  $('close-plan').onclick=()=>{if(allowDiscard())closeEditor();};$('add-plan-item').onclick=()=>{addRow();changed();};$('plan-total').oninput=changed;$('plan-note').oninput=changed;
  $('plan-form').onsubmit=e=>{e.preventDefault();action(async()=>{
    const items=draft(),total=$('plan-total').value,allocated=sum(items,'amount',plan.decimal_places);if(allocated>minor(total,plan.decimal_places))throw new Error('存储分配合计不能超过总预算，请调整总额或分配金额。');
    plan=await request(endpoint(),{expected_version:plan.version,total,note:$('plan-note').value,items,allocation_mode:true},'PUT');chooseIdentity=false;renderEditor();
    $('plan-month').value=identity.month;if($('plan-start').value>identity.month)$('plan-start').value='';if($('plan-system').value&&Number($('plan-system').value)!==Number(identity.system))$('plan-system').value='';if($('plan-currency').value&&$('plan-currency').value!==identity.code)$('plan-currency').value='';
    await reload(false);status(`${identity.month} · ${plan.system_name} · ${identity.code} 计划已保存，后续累计已更新。`);
  });};
  $('copy-plan').onclick=()=>action(async()=>{
    const month=$('copy-month').value;if(!validMonth(month)||month===identity.month)throw new Error('请选择另一个有效的来源月份。');
    const source=await request(endpoint({...identity,month}));if(!source.id)throw new Error('来源月份尚未编制计划。');
    if(!confirm(`用 ${month} 的总预算和存储分配替换当前草稿？保存 ${identity.month} 计划后才生效。`))return;
    $('plan-items').replaceChildren();$('plan-total').value=source.total??source.allocated;$('plan-note').value=source.note;
    for(const i of source.items)addRow({...i,id:undefined,storage_method_id:config.storage.find(s=>s.id===i.storage_method_id&&s.selectable&&!s.is_placeholder)?i.storage_method_id:undefined});changed();status('已复制为草稿。请检查总额及存储方式后保存。');
  });
  $('plan-history').onclick=()=>action(async()=>{
    const history=await request(endpoint()+'/revisions');$('budget-detail-title').textContent=`${identity.month} · ${plan.system_name} · ${identity.code} 历史版本`;const host=$('budget-detail-content');host.replaceChildren();
    for(const r of history){const detail=node('details'),p=r.plan;detail.append(node('summary',`版本 ${r.version} · ${r.created_at} · 总预算 ${p.total??p.allocated} ${identity.code} · 存储分配 ${p.allocated}`));for(const i of p.items)detail.append(node('p',`${i.storage_path} · ${i.amount} ${identity.code} · ${i.title} · ${i.note}`));host.append(detail);}if(!history.length)host.append(node('p','尚无保存版本。'));$('budget-detail-dialog').showModal();
  });
  $('budget-load').onsubmit=e=>{e.preventDefault();action(async()=>{if(allowDiscard())await reload();else restoreScope();});};
  $('plan-month').onchange=()=>switchMonth($('plan-month').value);$('plan-previous-month').onclick=()=>shiftMonth(-1);$('plan-next-month').onclick=()=>shiftMonth(1);$('plan-current-month').onclick=()=>switchMonth(currentMonth());
  for(const id of ['plan-system','plan-currency','plan-start'])$(id).onchange=()=>status('点击“加载所选计划”应用筛选。');
  $('budget-export').onclick=()=>{if(scope)location.href=`/api/budget/overview.xlsx?${query()}`;};$('matrix-scope').onchange=renderMatrix;$('matrix-level').onchange=renderMatrix;$('matrix-transpose').onclick=()=>{transpose=!transpose;renderMatrix();};
  document.querySelector('[data-budget-close]').onclick=()=>$('budget-detail-dialog').close();window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  $('plan-form').prepend(Object.assign(node('p'),{id:'plan-error',role:'alert',className:'dialog-status error'}));
  $('plan-month').value=currentMonth();action(async()=>{config=await request('/api/budget/config');options($('plan-system'),config.systems,'全部用途','');options($('plan-currency'),config.currencies,'全部币种','');await reload();});
})();

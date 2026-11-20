window.IncomeAllocations = (() => {
  const {node,button,request,options} = BudgetUI;
  const field=(label,input)=>{const el=node('label',label);el.append(input);return el;};
  const input=(type,value='')=>{const el=node('input');el.type=type;el.value=value;return el;};
  function dialog(title) {
    const el=node('dialog','','app-dialog budget-dialog income-allocation-dialog');
    const heading=node('div','','dialog-heading'),name=node('h2',title);
    heading.append(name,button('×',()=>el.dispatchEvent(new Event('request-close'))));
    heading.lastChild.setAttribute('aria-label','关闭'+title);
    el.append(heading);document.body.append(el);return el;
  }
  function amountRow(config, sourceCode, initial={}, withMonth=false) {
    const row=node('div','','income-allocation-row'),select=node('select'),amount=input('number',initial.amount??'');
    select.required=true;select.className='allocation-budget';amount.className='allocation-amount';amount.required=true;amount.min='0.00000001';
    options(select,config.systems.filter(s=>s.is_active||s.id===initial.budget_system_id),'选择预算',initial.budget_system_id);
    const code=node('small'),amountLabel=field('分配金额',amount),amountCell=node('div');
    const remainder=input('checkbox'),remainderLabel=field('全部剩余',remainder);
    remainder.className='allocation-remainder';remainderLabel.className='allocation-remainder-option';
    amountLabel.append(code);amountCell.append(amountLabel,remainderLabel);
    let manualAmount=amount.value;
    remainder.addEventListener('change',()=>{
      if(remainder.checked){
        for(const other of row.parentElement.querySelectorAll('.allocation-remainder:checked')){
          if(other===remainder)continue;
          other.checked=false;other.dispatchEvent(new Event('change'));
        }
        manualAmount=amount.value;amount.value='';
      }else amount.value=manualAmount;
      amount.readOnly=remainder.checked;amount.required=!remainder.checked;
      amount.placeholder=remainder.checked?'自动换算剩余':'';
    });
    const month=input('month',initial.month||'');month.required=withMonth;month.className='allocation-month';
    row.append(field('预算用途',select),amountCell);if(withMonth)row.append(field('生效月份',month));
    const remove=button('移除',()=>{row.remove();row.dispatchEvent(new Event('allocation-change'));});row.append(remove);
    function update() {
      const system=config.systems.find(s=>s.id===Number(select.value));
      const currency=system?.currency_code||sourceCode;
      code.textContent=currency;amount.step=String(10**-(config.currencies.find(c=>c.code===currency)?.decimal_places??2));
      amount.min=amount.step;
    }
    select.addEventListener('change',update);update();
    return {row,select,amount,month,update};
  }
  const rowAmount=row=>row.querySelector('.allocation-remainder').checked?null:row.querySelector('.allocation-amount').value;

  const chooser=dialog('分配收入'),searchForm=node('form','','funding-search'),search=input('search');
  search.placeholder='搜索收入或冲销事项';search.maxLength=120;
  const searchButton=node('button','搜索');searchButton.type='submit';searchForm.append(field('收入来源',search),searchButton);
  const list=node('div','','funding-candidates'),notice=node('p','','subtle-note'),nav=node('div','','funding-pagination');
  let offset=0,serial=0,onSaved=null,preferred=null;
  const prev=button('上一页',()=>{offset=Math.max(0,offset-20);load();}),next=button('下一页',()=>{offset+=20;load();});
  nav.append(prev,next);chooser.append(node('p','普通收入可分到多个预算；收入事项使用净额，AA 退款只冲减消费。','subtle-note'),searchForm,notice,list,nav);
  chooser.id='income-source-dialog';
  chooser.addEventListener('request-close',()=>chooser.close());
  async function load() {
    const ticket=++serial;notice.textContent='正在读取收入…';prev.disabled=next.disabled=true;
    try {
      const data=await request(`/api/budget/income-sources?${new URLSearchParams({keyword:search.value,offset,limit:20})}`);
      if(ticket!==serial)return;list.replaceChildren();notice.textContent=`共 ${data.total} 个收入来源`;
      for(const source of data.sources) {
        const card=node('article','','funding-candidate');
        card.dataset.sourceKey=`${source.entry_type}:${source.entry_id}`;
        card.append(node('strong',source.title),node('p',`${source.date||'待整理'} · 净收入 ${source.net??'未完整'} ${source.currency_code}`),
          node('p',`已分给预算 ${source.occupied??'未完整'} · 尚可分配 ${source.remaining??'未完整'} ${source.currency_code}`));
        if(source.reason||source.overallocated)card.append(node('p',source.reason||'分配超出净收入，请调整。','dialog-status error'));
        card.append(button(source.bindings.length?'查看 / 修改分配':'分配这笔收入',()=>open(source.entry_type,source.entry_id,async()=>{
          await load();await onSaved?.();
        },preferred)));
        list.append(card);
      }
      prev.disabled=offset===0;next.disabled=offset+20>=data.total;
    }catch(e){notice.textContent=e.message;}
  }
  searchForm.onsubmit=e=>{e.preventDefault();offset=0;load();};
  function browse(callback, target=null) {onSaved=callback;preferred=target;offset=0;search.value='';chooser.showModal();load();}

  const editor=dialog('收入预算分配'),form=node('form','','income-allocation-form'),scroll=node('div','','plan-editor-scroll');
  const summary=node('div','','funding-content'),rows=node('div','','income-allocation-rows'),legacy=node('div','','allocation-legacy');
  const error=node('p','','dialog-status error'),footer=node('div','','form-actions'),save=node('button','保存分配');
  const preview=node('p','','funding-preview');preview.setAttribute('role','status');
  save.type='submit';error.setAttribute('role','alert');
  let current=null,currentKey=null,done=null,dirty=false,busy=false,editorSerial=0,previewSerial=0,previewTimer;
  const add=button('＋添加预算',()=>{addRow();dirty=true;schedulePreview();});
  scroll.append(summary,rows,add,preview,legacy,error);
  footer.append(button('取消',dismiss),save);form.append(scroll,footer);editor.append(form);editor.id='income-allocation-editor';
  function dismiss(){if(!busy&&(!dirty||confirm('分配尚未保存，确定放弃修改？')))editor.close();}
  editor.addEventListener('request-close',dismiss);editor.addEventListener('cancel',e=>{e.preventDefault();dismiss();});
  form.addEventListener('input',e=>{
    // Checkboxes emit input before change; let the row preserve its manual value first.
    if(e.target.matches('.allocation-remainder'))return;
    dirty=true;error.textContent='';schedulePreview();
  });
  window.addEventListener('beforeunload',e=>{if(editor.open&&dirty){e.preventDefault();e.returnValue='';}});
  const draftItems=()=>[...rows.children].map(row=>({budget_system_id:Number(row.querySelector('select').value),
    month:row.querySelector('[type=month]').value,amount:rowAmount(row)}));
  function schedulePreview(){
    clearTimeout(previewTimer);const ticket=++previewSerial;preview.textContent='';
    for(const row of rows.children)if(rowAmount(row)===null)row.querySelector('.allocation-amount').value='';
    previewTimer=setTimeout(async()=>{
      const items=draftItems();
      if(items.some(i=>!i.budget_system_id||i.amount===''||!i.month))return;
      try{
        const data=await request(`/api/budget/income-allocations/${currentKey.kind}/${currentKey.id}/preview`,
          {expected_token:current.token,items},'POST');
        if(ticket!==previewSerial)return;
        [...rows.children].forEach((row,index)=>{
          if(rowAmount(row)===null)row.querySelector('.allocation-amount').value=data.items[index].amount;
        });
        preview.textContent=`本次合计占用 ${data.occupied} ${data.currency_code} · 保存后尚可分配 ${data.remaining??'未完整'} ${data.currency_code}${data.overallocated?' · 已超额，请减少金额':''}`;
        preview.classList.toggle('error',data.overallocated||!data.complete);
      }catch(e){if(ticket===previewSerial)preview.textContent=e.message;}
    },250);
  }
  function refreshLegacy() {
    if(!current)return;
    const checked=new Set([...legacy.querySelectorAll('input:checked')].map(el=>Number(el.value)));
    const choices=new Map([...legacy.querySelectorAll('select[data-plan-id]')].map(el=>[Number(el.dataset.planId),el.value]));
    legacy.replaceChildren();
    const targets=draftItems();
    for(const p of current.legacy_plans||[]) {
      if(!targets.some(i=>i.budget_system_id===p.budget_system_id))continue;
      const same=targets.some(i=>i.budget_system_id===p.budget_system_id&&i.month===p.month);
      if(!same&&(p.legacy_reviewed||Number(p.total)===0))continue;
      if(!legacy.children.length)legacy.append(node('strong','核对历史额度，避免跨月重复累计'),
        node('p','下列旧额度缺少收入来源。替代会移除旧贡献并重算结转，保留原存储安排（可能需要调整）；独立资金只适用于与本次收入无关的旧额度。','subtle-note'));
      if(same){
        const checkbox=input('checkbox');checkbox.value=p.id;checkbox.checked=checked.has(p.id);checkbox.required=true;
        legacy.append(field(`替代 ${p.system_name} · ${p.month} 的历史手填总额 ${p.total} ${p.currency_code}（保留存储安排，不重复累计）`,checkbox));
      }else{
        const select=node('select');select.dataset.planId=p.id;select.required=true;
        for(const [value,title] of [['','请选择处理方式'],['replace','替代重复额度：旧总额归零'],['independent','保留为独立资金：与本次收入无关']]){
          const option=node('option',title);option.value=value;select.append(option);
        }
        select.value=choices.get(p.id)||'';
        const label=field(`${p.system_name} · ${p.month} · 历史额度 ${p.total} ${p.currency_code}`,select);
        label.className='legacy-decision';legacy.append(label);
      }
    }
  }
  function addRow(initial={}) {
    const r=amountRow(current.config,current.source.currency_code,{month:current.source.date?.slice(0,7)||'',...initial},true);
    r.row.addEventListener('change',()=>{dirty=true;error.textContent='';refreshLegacy();schedulePreview();});
    r.row.addEventListener('allocation-change',()=>{dirty=true;refreshLegacy();schedulePreview();});
    rows.append(r.row);refreshLegacy();return r;
  }
  async function open(kind,id,callback,target=null) {
    const ticket=++editorSerial;
    try {
      const data=await request(`/api/budget/income-allocations/${kind}/${id}`);
      if(ticket!==editorSerial)return;
      clearTimeout(previewTimer);previewSerial++;preview.textContent='';
      current=data;currentKey={kind,id};done=callback;dirty=false;rows.replaceChildren();legacy.replaceChildren();summary.replaceChildren();error.textContent='';
      const s=data.source;
      summary.append(node('strong',s.title),node('p',`${s.date||'来源日期待整理'} · 净收入 ${s.net??'未完整'} ${s.currency_code} · 已分配 ${s.occupied??'未完整'} ${s.currency_code}`),
        node('p','填写各预算币种的金额，或选一行“全部剩余”自动换算其他行之后的余款。精度尾差保留在来源；移除全部行表示暂不分配。保存后金额固定，正负余额连续结转。','subtle-note'));
      if(s.reason||s.overallocated)summary.append(node('p',s.reason||'来源已超额，减少分配后保存。','dialog-status error'));
      for(const item of data.items)addRow(item);
      if(!data.items.length)addRow({budget_system_id:target?.system?Number(target.system):undefined,
        month:target?.month||s.date?.slice(0,7)});
      editor.showModal();schedulePreview();
    }catch(e){if(chooser.open)notice.textContent=e.message;else alert(e.message);}
  }
  form.onsubmit=async e=>{
    e.preventDefault();if(busy||!form.reportValidity())return;
    const items=draftItems();clearTimeout(previewTimer);previewSerial++;
    busy=true;form.inert=true;error.textContent='';
    try {
      await request(`/api/budget/income-allocations/${currentKey.kind}/${currentKey.id}`,{expected_token:current.token,items,
        replace_legacy_plan_ids:[...legacy.querySelectorAll('input:checked')].map(el=>Number(el.value)),
        legacy_decisions:[...legacy.querySelectorAll('select[data-plan-id]')].map(el=>({plan_id:Number(el.dataset.planId),action:el.value}))},'PUT');
      dirty=false;editor.close();
      try{await done?.();}catch(e){alert(`分配已保存，刷新失败：${e.message}。请刷新页面。`);}
    }catch(e){error.textContent=e.message;error.scrollIntoView({block:'nearest'});}
    finally{busy=false;form.inert=false;}
  };

  function entry(host,getConfig,budgetSelect,getSourceCode) {
    const section=node('section','','full-width income-entry-allocation');
    const toggle=input('checkbox'),label=field('按金额拆分到多个预算',toggle),body=node('fieldset');
    body.hidden=true;body.disabled=true;const parts=node('div','','income-allocation-rows');
    const hint=node('p','按各预算币种填写金额，可选一行“全部剩余”，保存时自动换算其余金额；余款也可以后分配，合计不能超过本笔收入。','subtle-note');
    const addPart=()=>{const cfg=getConfig();if(!cfg)return;const r=amountRow(cfg,getSourceCode());parts.append(r.row);};
    body.append(hint,parts,button('＋添加预算',addPart));section.append(label,body);
    host.insertBefore(section,host.querySelector('input[id$="-counterparty"]')?.closest('label')||host.querySelector('textarea')?.closest('label')||null);
    toggle.onchange=()=>{body.hidden=!toggle.checked;body.disabled=!toggle.checked;budgetSelect.disabled=toggle.checked;if(toggle.checked&&!parts.children.length)addPart();};
    function reset(){toggle.checked=false;body.hidden=true;body.disabled=true;parts.replaceChildren();budgetSelect.disabled=!getConfig();}
    function restore(allocations=[]) {
      reset();
      if (!allocations.length) { budgetSelect.value=''; return; }
      if (allocations.length===1 && allocations[0].amount==null) {
        budgetSelect.value=String(allocations[0].budget_system_id);return;
      }
      const cfg=getConfig();if(!cfg)return;
      toggle.checked=true;body.hidden=false;body.disabled=false;budgetSelect.disabled=true;
      for (const item of allocations) {
        const r=amountRow(cfg,getSourceCode(),item);parts.append(r.row);
        if(item.amount==null){const remaining=r.row.querySelector('.allocation-remainder');remaining.checked=true;remaining.dispatchEvent(new Event('change'));}
      }
    }
    return {reset,restore,payload(){
      if(!toggle.checked)return budgetSelect.value?[{budget_system_id:Number(budgetSelect.value),amount:null}]:[];
      return [...parts.children].map(row=>({budget_system_id:Number(row.querySelector('select').value),amount:rowAmount(row)}));
    }};
  }
  function renderTrace(host,data) {
    if(data.allocation_note)host.append(node('p',data.allocation_note,'subtle-note allocation-date-note'));
    for(const warning of data.allocation_warnings||[]){
      const p=node('p','','funding-warning'),link=node('a',warning.title);link.href=warning.url;
      p.append(link,document.createTextNode(`：${warning.reason}。未计入已核实预算收入，请修改分配。`));host.append(p);
    }
    if(!data.allocation_details?.length)return;
    const details=node('details','','allocation-trace'),wrap=node('div','','table-wrap'),table=node('table');
    details.append(node('summary','查看收入来源与预算生效月份'));
    const head=node('thead'),heading=node('tr');
    for(const title of ['来源发生／统计日','收入来源','预算生效月','预算','已记录分配','状态'])heading.append(node('th',title));
    head.append(heading);table.append(head);const body=node('tbody');
    for(const r of data.allocation_details){
      const tr=node('tr'),source=node('td'),link=node('a',r.source_title);link.href=r.source_url;source.append(link);
      const month=node('td'),budgetLink=node('a',r.month);
      budgetLink.href=`/budgets?month=${encodeURIComponent(r.month)}&system=${r.budget_system_id}`;month.append(budgetLink);
      tr.append(node('td',r.source_date||'待核对'),source,month,node('td',r.budget_name),
        node('td',`${r.amount} ${r.budget_currency}`),node('td',r.source_status,r.source_complete?'':'funding-warning'));body.append(tr);
    }
    table.append(body);wrap.append(table);details.append(wrap);host.append(details);
  }
  return {browse,open,entry,renderTrace};
})();

window.BudgetStorage = (() => {
  const {node,button,request,options,status}=BudgetUI;
  const minor=value=>BigInt(String(value).replace('.',''));
  const money=(value,places)=>{const negative=value<0n;let text=(negative?-value:value).toString().padStart(places+1,'0');return (negative?'-':'')+(places?text.slice(0,-places)+'.'+text.slice(-places):text);};
  const sum=(values,places)=>values.some(v=>v==null)?null:money(values.reduce((a,v)=>a+minor(v),0n),places);
  function render(host,report,root,transpose,onOpen){
    const rows=report?.rows||[];
    for(const code of [...new Set(rows.map(r=>r.currency_code).filter(Boolean))]){
      const selected=rows.filter(r=>r.currency_code===code),places=selected[0].decimal_places,columns=new Map();
      const key=c=>root?c.root_key:c.key,label=c=>root?c.root_label:c.label;
      for(const row of selected)for(const cell of row.cells){if(cell.key==='pending'&&!selected.some(r=>r.pending_count))continue;columns.set(key(cell),label(cell));}
      const keys=[...columns.keys()].sort((a,b)=>{const rank=k=>k==='pending'?2:k==='unallocated'?1:0;return rank(a)-rank(b)||columns.get(a).localeCompare(columns.get(b),'zh-CN');});
      const table=node('table'),head=node('tr'),thead=node('thead'),body=node('tbody');
      for(const title of ['预算',...keys.map(k=>columns.get(k)),'合计剩余'])head.append(node('th',title));thead.append(head);table.append(thead);
      const totals=new Map(keys.map(k=>[k,[]]));
      for(const row of selected){
        const tr=node('tr');tr.dataset.systemId=row.budget_system_id;
        const name=node('th',row.system_name);name.append(button('管理',()=>onOpen(row),'matrix-manage'));tr.append(name);
        for(const k of keys){const cells=row.cells.filter(c=>key(c)===k),value=row.complete?sum(cells.map(c=>c.balance),places):null;
          totals.get(k).push(value);const td=node('td'),b=button(value??'未完整',()=>onOpen(row),'matrix-amount');
          td.dataset.storageKey=k;if(value!=null&&minor(value)<0n)td.classList.add('storage-negative');td.append(b);tr.append(td);}
        const total=node('td',row.total??'未完整');total.dataset.storageTotal='';if(row.total!=null&&minor(row.total)<0n)total.classList.add('storage-negative');tr.append(total);body.append(tr);
      }
      const foot=node('tr');foot.append(node('th','合计'));for(const k of keys)foot.append(node('td',sum(totals.get(k),places)??'未完整'));foot.append(node('th',sum(selected.map(r=>r.total),places)??'未完整'));body.append(foot);table.append(body);
      if(transpose){const cells=[...table.rows].map(r=>[...r.cells]);table.replaceChildren();const rotated=node('tbody');for(let col=0;col<cells[0].length;col++){const tr=node('tr');for(let row=0;row<cells.length;row++){const original=cells[row][col],cell=node(row===0||col===0?'th':'td','',original.className);cell.append(...original.childNodes);tr.append(cell);}rotated.append(tr);}table.append(rotated);}
      const wrap=node('div','','table-wrap');wrap.append(table);host.append(node('h3',code),wrap);
      for(const row of selected){const notes=[];if(!row.complete)notes.push(row.reason||'消费或汇率未完整，余额暂无法确认');if(row.pending_count)notes.push(`${row.pending_count} 个消费尚未指定存储`);if(row.negative)notes.push('有存储额度不足，可手动调拨补足');if(row.funding_warnings.length)notes.push('收入来源存在待处理问题，余额仍按计划计算');if(notes.length){const p=node('p',`${row.system_name}：${notes.join('；')}。`,'storage-warning');p.append(button('管理扣款与调拨',()=>onOpen(row),'matrix-manage'));host.append(p);}}
    }
    for(const row of rows.filter(r=>!r.currency_code))host.append(node('p',`${row.system_name}：请先在设置或预案中指定预算币种。`,'subtle-note'));
    if(!rows.length)host.append(node('p','暂无可查看的预算。','plan-empty'));
  }

  const dialog=node('dialog','','app-dialog storage-manage-dialog');dialog.id='storage-manage-dialog';dialog.setAttribute('aria-labelledby','storage-manage-title');
  const heading=node('div','','dialog-heading'),title=node('h2');title.id='storage-manage-title';
  const close=button('×',dismiss);close.setAttribute('aria-label','关闭存储管理');heading.append(title,close);
  const error=node('p','','dialog-status error');error.id='storage-manage-error';error.role='alert';
  const summary=node('div','','storage-summary'),tabs=node('div','','storage-tabs'),content=node('div','','storage-manage-content');
  const scroll=node('div','','storage-manage-scroll'),refresh=button('重新加载',()=>{if(allowDiscard())act(load);});refresh.id='storage-reload';
  scroll.append(summary,tabs,content);dialog.append(heading,error,scroll,refresh);document.body.append(dialog);
  let system,month,onChanged,data,mode='default',editing=null,expense=null,dirty=false,busy=false;
  function allowDiscard(){return !dirty||confirm('存储管理中有未保存修改，确定放弃？');}
  function dismiss(){if(!busy&&allowDiscard()){dirty=false;dialog.close();}}
  dialog.addEventListener('cancel',e=>{e.preventDefault();dismiss();});
  dialog.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();if(!e.repeat)dismiss();}},true);
  dialog.addEventListener('input',e=>{if(e.target.closest('[data-storage-draft]'))dirty=true;});
  dialog.addEventListener('change',e=>{if(e.target.closest('[data-storage-draft]'))dirty=true;});
  window.addEventListener('beforeunload',e=>{if(dirty&&dialog.open){e.preventDefault();e.returnValue='';}});
  const path=suffix=>`/api/budget/storage/${system}/${suffix}`;
  async function act(fn){if(busy)return;busy=true;scroll.inert=true;refresh.disabled=true;error.textContent='';try{await fn();}catch(e){error.textContent=e.message;}finally{busy=false;scroll.inert=false;refresh.disabled=false;}}
  async function load(){data=await request(path(month));editing=null;expense=null;dirty=false;draw();}
  async function save(suffix,body,method='PUT'){
    await request(path(suffix),{...body,expected_version:data.config.version},method);dirty=false;
    try{await load();await onChanged();}catch(e){throw new Error(`修改已保存，但刷新失败：${e.message}。请重新加载后继续。`);}
  }
  function field(label,el){const host=node('label',label);host.append(el);return host;}
  function selectStorage(selected,empty,allowInactive=false){const select=node('select');options(select,data.config.storage.filter(s=>allowInactive||!s.is_placeholder&&(s.selectable||s.id===selected)),empty,selected);return select;}
  function input(type,value,id){const el=node('input');el.type=type;el.value=value??'';if(id)el.id=id;return el;}
  function newForm(){const f=node('form','','storage-form');f.dataset.storageDraft='';return f;}
  function switchMode(next,row=null){if(!allowDiscard())return;dirty=false;mode=next;editing=next==='transfers'?row:null;expense=next==='expenses'?row:null;draw();}
  function draw(){
    title.textContent=`${data.row.system_name} · ${month} · 扣款与调拨`;
    summary.replaceChildren();for(const c of data.row.cells){if(c.key==='pending'&&!data.row.pending_count)continue;const p=node('div');p.append(node('small',c.label),node('strong',`${c.balance??'未完整'} ${data.row.currency_code||''}`,c.balance!=null&&minor(c.balance)<0n?'storage-negative':''));summary.append(p);}
    tabs.replaceChildren();for(const [key,label] of [['default','默认扣款'],['transfers','存储调拨'],['expenses','特殊消费']]){const b=button(label,()=>switchMode(key));b.dataset.storageTab=key;b.classList.toggle('is-active',key===mode);tabs.append(b);}
    content.replaceChildren();if(mode==='default')drawDefault();else if(mode==='transfers')drawTransfers();else drawExpenses();
    if(!data.row.is_active)for(const control of content.querySelectorAll('input,select,button'))control.disabled=true;
  }
  function drawDefault(){
    const form=newForm(),select=selectStorage(data.row.default_storage_method_id,'未指定（消费列为待归属）');select.id='default-storage';
    const submit=node('button','保存默认扣款');submit.type='submit';submit.id='save-default-storage';
    form.append(field('本预算默认消费存储',select),node('p','保存后，本预算所有未单独指定的消费（包括历史月份）都会按这个存储扣减。特殊消费可另选；未指定时单列待归属，预算整体结余仍照常计算。','subtle-note'),submit);
    form.onsubmit=e=>{e.preventDefault();act(()=>save('default',{storage_method_id:Number(select.value)||null}));};content.append(form);
  }
  function drawTransfers(){
    const form=newForm(),date=input('month',editing?.month||month,'storage-transfer-month');date.required=true;date.min=data.row.origin_month;date.max=month;
    const from=selectStorage(editing?.source_id??null,'待分配',true),to=selectStorage(editing?.destination_id??data.row.default_storage_method_id,'待分配');from.id='storage-transfer-from';to.id='storage-transfer-to';
    const amount=input('number',editing?.amount,'storage-transfer-amount');amount.required=true;amount.min=String(10**-data.row.decimal_places);amount.step=amount.min;
    const note=input('text',editing?.note,'storage-transfer-note');note.maxLength=500;
    const grid=node('div','','form-grid storage-transfer-grid');grid.append(field('生效月份',date),field('转出存储',from),field('转入存储',to),field(`调拨金额（${data.row.currency_code||'未设币种'}）`,amount),field('说明（可选）',note));
    const submit=node('button',editing?'保存调拨修改':'新增调拨');submit.type='submit';submit.id='save-storage-transfer';
    form.append(node('p','调拨只移动同一预算中的存储额度，总剩余保持不变。转出存储必须有足够余额；存储不足的负数可由其他存储调入补足。投资卖出或账户转账仍需按实际情况记账。','subtle-note'),grid,submit);
    if(editing)form.append(button('取消修改',()=>switchMode('transfers')));
    form.onsubmit=e=>{e.preventDefault();const id=editing?.id;act(()=>save(`transfers${id?'/'+id:''}`,{month:date.value,source_id:Number(from.value)||null,destination_id:Number(to.value)||null,amount:amount.value,note:note.value,transfer_version:editing?.version||0},id?'PUT':'POST'));};content.append(form);
    const table=node('table'),head=node('tr');for(const label of ['月份','转出 → 转入','金额','说明','操作'])head.append(node('th',label));table.append(head);
    for(const row of [...data.row.transfers].reverse()){
      const tr=node('tr');tr.dataset.transferId=row.id;tr.append(node('td',row.month),node('td',`${row.source_name} → ${row.destination_name}${row.in_range?'':'（起算月之前，未纳入）'}`),node('td',`${row.amount} ${row.currency_code}`),node('td',row.note));
      const actions=node('td');actions.append(button('修改',()=>switchMode('transfers',row)),button('撤销',()=>{if(!allowDiscard()||!confirm('撤销这笔存储调拨？将重新计算余额，原记录保留在审计中。'))return;act(()=>save('transfers/'+row.id,{transfer_version:row.version},'DELETE'));}));tr.append(actions);table.append(tr);
    }
    const wrap=node('div','','table-wrap');wrap.append(table);content.append(node('h3','截至所选月的调拨记录'),wrap);
    if(!data.row.transfers.length)content.append(node('p','尚无存储调拨。','subtle-note'));
  }
  function drawExpenses(){
    content.append(node('p',`以下为 ${month} 的普通支出及完整支出事项，AA 返还已抵扣，负金额回补存储。其他月份可关闭弹窗后切换月份查看。`,'subtle-note'));
    if(expense){
      const form=newForm(),select=selectStorage(expense.override_storage_id,'使用本预算默认扣款');select.id='expense-storage-target';
      const submit=node('button','保存消费归属');submit.type='submit';submit.id='save-expense-storage';
      form.append(node('strong',`${expense.date} · ${expense.title} · ${expense.amount??'未完整'} ${data.row.currency_code}`),field('扣款存储',select),submit,button('取消修改',()=>switchMode('expenses')));
      form.onsubmit=e=>{e.preventDefault();act(()=>save('expense',{entry_type:expense.entry_type,entry_id:expense.entry_id,expected_entry_token:expense.token,storage_method_id:Number(select.value)||null}));};content.append(form);
    }
    const table=node('table'),head=node('tr');for(const label of ['日期','消费 / 事项','净消费','扣款存储','操作'])head.append(node('th',label));table.append(head);
    for(const row of data.row.expenses){
      const tr=node('tr');tr.dataset.entryKey=`${row.entry_type}:${row.entry_id}`;const source=node('td'),link=node('a',row.title);link.href=row.url;link.target='_blank';link.rel='noopener';source.append(link);
      const name=data.config.storage.find(s=>s.id===row.storage_method_id)?.path||'待归属消费';
      tr.append(node('td',row.date),source,node('td',`${row.amount??'未完整'} ${data.row.currency_code}`),node('td',name+(row.inherited?' · 默认':' · 单独指定')));
      const td=node('td');td.append(button('改归属',()=>switchMode('expenses',row)));tr.append(td);table.append(tr);
    }
    const wrap=node('div','','table-wrap');wrap.append(table);content.append(wrap);if(!data.row.expenses.length)content.append(node('p','所选月没有可单独归属的消费。跨预算事项请先整理主流水预算归属。','subtle-note'));
  }
  async function open(sid,selectedMonth,callback){
    if(busy||dialog.open&&!allowDiscard())return;busy=true;system=sid;month=selectedMonth;onChanged=callback;mode='default';
    try{await load();error.textContent='';if(!dialog.open)dialog.showModal();}catch(e){status(e.message,true);}finally{busy=false;}
  }
  return {render,open};
})();

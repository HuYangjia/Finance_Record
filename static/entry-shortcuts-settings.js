(() => {
  const {$,node,request,options,status,button}=BudgetUI;
  let shortcutConfig={version:1,shortcuts:[]},budgetConfig={systems:[],currencies:[]};
  let accounts=[],categories=[],editing=null,busy=false;
  const dialog=$('shortcut-dialog'),form=$('shortcut-form');
  const modeLabel={expense:'支出',income:'入账',transfer:'转账 / 换汇'};

  async function act(fn){
    if(busy)return;busy=true;
    try{await fn();}catch(error){
      const target=dialog.open?$('shortcut-dialog-status'):null;
      if(target){target.textContent=error.message;target.className='dialog-status error';}
      else status(error.message,true);
    }finally{busy=false;}
  }

  const accountName=id=>accounts.find(row=>row.id===Number(id))?.name||'未选择账户';
  const categoryName=id=>categories.find(row=>row.id===Number(id))?.display_name||'未选择分类';
  const budgetName=id=>budgetConfig.systems.find(row=>row.id===Number(id))?.name||'自动预算';
  const decimalPlaces=id=>{
    const code=accounts.find(row=>row.id===Number(id))?.currency_code;
    return budgetConfig.currencies.find(row=>row.code===code)?.decimal_places??2;
  };

  function summary(row){
    const amount=row.mode==='income'?row.inflow_amount:row.outflow_amount;
    if(row.mode==='expense')return `${row.source_account_name||'未选账户'} · ${row.category_name||'未选分类'}${amount?' · '+amount+' '+row.source_currency_code:''}`;
    if(row.mode==='income')return `${row.destination_account_name||'未选账户'} · ${row.category_name||'未选分类'}${amount?' · '+amount+' '+row.destination_currency_code:''}`;
    return `${row.source_account_name||'未选账户'} → ${row.destination_account_name||'未选账户'}${amount?' · '+amount+' '+row.source_currency_code:''}`;
  }

  function payload(row,overrides={}){
    return {...{
      expected_version:shortcutConfig.version,name:row.name,mode:row.mode,
      source_account_id:row.source_account_id,destination_account_id:row.destination_account_id,
      category_id:row.category_id,budget_system_id:row.budget_system_id,
      outflow_amount:row.outflow_amount,inflow_amount:row.inflow_amount,
      counterparty:row.counterparty,note:row.note,sort_order:row.sort_order,is_active:row.is_active,
    },...overrides};
  }

  function render(){
    const target=$('shortcut-settings-list');target.replaceChildren();
    if(!shortcutConfig.shortcuts.length){target.append(node('p','暂无便捷操作，点击“新增便捷操作”创建。','subtle-note'));return;}
    for(const [index,row] of shortcutConfig.shortcuts.entries()){
      const card=node('article','','shortcut-card');
      if(!row.is_active)card.classList.add('is-inactive');
      if(!row.valid)card.classList.add('has-warning');
      const copy=node('div','','shortcut-card-copy');
      copy.append(node('strong',row.name),node('span',`${modeLabel[row.mode]} · ${summary(row)}`,'shortcut-card-summary'));
      if(row.budget_system_name)copy.append(node('small',`预算：${row.budget_system_name}`));
      if(!row.valid)copy.append(node('small',`需要修复：${row.invalid_reason}`,'shortcut-warning'));
      else if(!row.is_active)copy.append(node('small','已停用'));
      const actions=node('div','','shortcut-card-actions');
      const up=button('上移',()=>move(index,-1),'quiet-button');up.disabled=index===0;
      const down=button('下移',()=>move(index,1),'quiet-button');down.disabled=index===shortcutConfig.shortcuts.length-1;
      actions.append(up,down,
        button('编辑',()=>open(row),'quiet-button'),
        button(row.is_active?'停用':'启用',()=>act(async()=>{
          shortcutConfig=await request(`/api/entry-shortcuts/${row.id}`,payload(row,{is_active:!row.is_active}),'PUT');render();status(`${row.name}已${row.is_active?'停用':'启用'}。`);
        }),'quiet-button'),
        button('删除',()=>act(async()=>{
          if(!confirm(`删除“${row.name}”？这不会删除任何流水。`))return;
          shortcutConfig=await request(`/api/entry-shortcuts/${row.id}?expected_version=${shortcutConfig.version}`,undefined,'DELETE');render();status('便捷操作已删除。');
        }),'quiet-button danger-button')
      );
      card.append(copy,actions);target.append(card);
    }
  }

  function move(index,delta){return act(async()=>{
    const rows=[...shortcutConfig.shortcuts];const [row]=rows.splice(index,1);rows.splice(index+delta,0,row);
    shortcutConfig=await request('/api/entry-shortcuts/order',{expected_version:shortcutConfig.version,ids:rows.map(item=>item.id)},'PUT');render();status('首页便捷操作顺序已更新。');
  });}

  function updateFields(){
    const mode=$('shortcut-mode').value;
    document.querySelectorAll('[data-shortcut-modes]').forEach(label=>{
      const visible=label.dataset.shortcutModes.split(' ').includes(mode);label.hidden=!visible;
      for(const control of label.querySelectorAll('input,select'))control.disabled=!visible;
    });
    $('shortcut-source-account').required=['expense','transfer'].includes(mode);
    $('shortcut-destination-account').required=['income','transfer'].includes(mode);
    window.setFrequentPickerRequired($('shortcut-category'),['expense','income'].includes(mode));
    const selected=$('shortcut-category').value;
    options($('shortcut-category'),categories.filter(row=>row.kind===mode&&(row.is_active||row.id===Number(selected))),null,selected);
    window.refreshFrequentPickers(form);
    updateAmountSteps();updatePreview();
  }

  function updateAmountSteps(){
    const outflowStep=String(10**-decimalPlaces($('shortcut-source-account').value));
    const inflowStep=String(10**-decimalPlaces($('shortcut-destination-account').value));
    $('shortcut-outflow').step=outflowStep;$('shortcut-outflow').min=outflowStep;
    $('shortcut-inflow').step=inflowStep;$('shortcut-inflow').min=inflowStep;
  }

  function updatePreview(){
    const mode=$('shortcut-mode').value,name=$('shortcut-name').value.trim()||'未命名按钮';
    let detail='';
    if(mode==='expense')detail=`${accountName($('shortcut-source-account').value)} · ${categoryName($('shortcut-category').value)} · ${budgetName($('shortcut-budget').value)}`;
    else if(mode==='income')detail=`${accountName($('shortcut-destination-account').value)} · ${categoryName($('shortcut-category').value)} · ${budgetName($('shortcut-budget').value)}`;
    else detail=`${accountName($('shortcut-source-account').value)} → ${accountName($('shortcut-destination-account').value)}`;
    $('shortcut-preview').textContent=`首页预览：${name}｜${detail}`;
  }

  function open(row=null){
    editing=row;form.reset();$('shortcut-dialog-status').textContent='';
    $('shortcut-dialog-title').textContent=row?'编辑便捷操作':'新增便捷操作';
    $('shortcut-name').value=row?.name||'';$('shortcut-mode').value=row?.mode||'expense';
    options($('shortcut-source-account'),accounts,null,row?.source_account_id);
    options($('shortcut-destination-account'),accounts,null,row?.destination_account_id);
    options($('shortcut-budget'),budgetConfig.systems.filter(item=>item.is_active||item.id===row?.budget_system_id),'自动应用分类映射和默认预算',row?.budget_system_id);
    const categoryRows=categories.filter(item=>item.kind===$('shortcut-mode').value&&(item.is_active||item.id===row?.category_id));
    options($('shortcut-category'),categoryRows,null,row?.category_id);
    $('shortcut-outflow').value=row?.outflow_amount||'';$('shortcut-inflow').value=row?.inflow_amount||'';
    $('shortcut-counterparty').value=row?.counterparty||'';$('shortcut-note').value=row?.note||'';
    $('shortcut-active').value=row?.is_active===false?'0':'1';
    updateFields();dialog.showModal();$('shortcut-name').focus();
  }

  function close(){dialog.close();editing=null;}

  form.addEventListener('submit',event=>{event.preventDefault();act(async()=>{
    const mode=$('shortcut-mode').value;
    const body={
      expected_version:shortcutConfig.version,name:$('shortcut-name').value,mode,
      source_account_id:['expense','transfer'].includes(mode)?Number($('shortcut-source-account').value)||null:null,
      destination_account_id:['income','transfer'].includes(mode)?Number($('shortcut-destination-account').value)||null:null,
      category_id:['expense','income'].includes(mode)?Number($('shortcut-category').value)||null:null,
      budget_system_id:['expense','income'].includes(mode)?Number($('shortcut-budget').value)||null:null,
      outflow_amount:['expense','transfer'].includes(mode)?$('shortcut-outflow').value||null:null,
      inflow_amount:['income','transfer'].includes(mode)?$('shortcut-inflow').value||null:null,
      counterparty:['expense','income'].includes(mode)?$('shortcut-counterparty').value||null:null,
      note:$('shortcut-note').value||null,is_active:$('shortcut-active').value==='1',
    };
    shortcutConfig=await request(`/api/entry-shortcuts${editing?'/'+editing.id:''}`,body,editing?'PUT':'POST');
    close();render();status('首页便捷操作已保存。');
  });});

  $('new-shortcut').onclick=()=>open();$('close-shortcut-dialog').onclick=close;$('cancel-shortcut').onclick=close;
  $('shortcut-mode').onchange=updateFields;
  for(const id of ['shortcut-name','shortcut-source-account','shortcut-destination-account','shortcut-category','shortcut-budget'])$(id).addEventListener('input',updatePreview);
  $('shortcut-source-account').addEventListener('change',updateAmountSteps);$('shortcut-destination-account').addEventListener('change',updateAmountSteps);
  window.createFrequentPicker($('shortcut-category'),{activityKey:'usage',displayMode:'ranked-scroll',visibleRows:8});

  Promise.all([
    request('/api/entry-shortcuts'),request('/api/budget/config'),request('/api/accounts?include_inactive=true'),
    Promise.all([request('/api/categories?kind=expense&include_inactive=true'),request('/api/categories?kind=income&include_inactive=true')]).then(rows=>rows.flat()),
  ]).then(([shortcuts,budgets,accountRows,categoryRows])=>{
    shortcutConfig=shortcuts;budgetConfig=budgets;accounts=accountRows.map(row=>({...row,name:`${row.name} · ${row.currency_code}`}));categories=categoryRows;render();
  }).catch(error=>{const target=$('shortcut-settings-list');target.replaceChildren(node('p',error.message,'dialog-status error'));});
})();

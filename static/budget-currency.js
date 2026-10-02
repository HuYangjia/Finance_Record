window.BudgetCurrency = (() => {
  const {node,request,options,button}=BudgetUI;
  const dialog=node('dialog','','app-dialog budget-dialog'),form=node('form'),title=node('h2','修改预算币种');
  dialog.id='budget-currency-dialog';
  const note=node('p','','subtle-note'),select=node('select'),label=node('label','预算币种'),preview=node('div','','table-wrap'),error=node('p','','dialog-status error'),actions=node('div','','form-actions');
  select.id='budget-currency-code';select.required=true;label.append(select);error.id='budget-currency-error';error.role='alert';
  const submit=node('button','确认更正，保持数字'),cancel=button('取消',()=>dialog.close());submit.type='submit';submit.id='save-budget-currency';
  actions.append(cancel,submit);form.append(title,note,label,preview,error,actions);dialog.append(form);document.body.append(dialog);
  let snapshot,onSaved,saving=false;
  function render(){
    preview.replaceChildren();
    const table=node('table'),head=node('tr');for(const text of ['月份','原总预算','更正后总预算','存储分配'])head.append(node('th',text));table.append(head);
    for(const p of snapshot.plans){const row=node('tr');for(const text of [p.month,`${p.total??p.allocated} ${p.currency_code}`,`${p.total??p.allocated} ${select.value}`,`${p.allocated} ${select.value}`])row.append(node('td',text));table.append(row);}
    preview.append(table);submit.disabled=!select.value||select.value===snapshot.system.currency_code;
  }
  select.onchange=render;
  dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault();});
  form.onsubmit=async e=>{
    e.preventDefault();if(saving)return;saving=true;form.inert=true;error.textContent='';
    try{
      const config=await request(`/api/budget/systems/${snapshot.system.id}/currency`,{expected_version:snapshot.expected_version,currency_code:select.value,plans:snapshot.plans.map(p=>({id:p.id,version:p.version}))},'PUT');
      dialog.close();await onSaved(config);
    }catch(e){error.textContent=e.message;if(!dialog.open)BudgetUI.status(e.message,true);}
    finally{saving=false;form.inert=false;}
  };
  async function open(system,config,callback,selected){
    snapshot=await request(`/api/budget/systems/${system}/currency`);onSaved=callback;
    if(new Set(snapshot.plans.map(p=>p.currency_code)).size>1)throw new Error('此预算存在旧版多币种计划，原记录已保留。请先确认各笔金额的整理方式，不能直接合并更正。');
    title.textContent=`${snapshot.system.name} · 修改预算币种`;
    note.textContent=`将统一更正此预算的全部 ${snapshot.plans.length} 个月计划。总额和每笔存储分配的数字保持不变，不按汇率换算；之后可逐月修改金额。历史版本保留原币种，真实流水不变。`;
    options(select,config.currencies.filter(c=>c.is_active||c.code===snapshot.system.currency_code),null,selected||snapshot.system.currency_code||'CNY');
    error.textContent='';render();dialog.showModal();
  }
  return {open};
})();

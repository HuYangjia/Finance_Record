window.BudgetSource = (() => {
  const {node, button, request, options, status} = BudgetUI;
  const field = (label, control) => {const el=node('label',label);el.append(control);return el;};
  const input = (type, id, maxLength) => {
    const el=node(type==='textarea'?'textarea':'input');
    if(type!=='textarea')el.type=type;
    el.id=id;if(maxLength)el.maxLength=maxLength;
    return el;
  };
  function dialog(id, title) {
    const el=node('dialog','','app-dialog plan-editor-dialog budget-source-dialog');el.id=id;
    const heading=node('div','','dialog-heading plan-editor-heading'),name=node('h2',title);
    name.id=id+'-title';el.setAttribute('aria-labelledby',name.id);
    const close=button('×',()=>dismiss(el));close.setAttribute('aria-label','关闭'+title);
    heading.append(name,close);
    const form=node('form'),scroll=node('div','','plan-editor-scroll'),error=node('p','','dialog-status error');
    error.setAttribute('role','alert');
    const footer=node('div','','plan-editor-footer'),cancel=button('取消',()=>dismiss(el)),save=node('button','保存并关闭');
    save.type='submit';save.className='primary-button';footer.append(cancel,save);
    scroll.append(error);form.append(scroll,footer);el.append(heading,form);document.body.append(el);
    el.addEventListener('cancel',event=>{event.preventDefault();dismiss(el);});
    el.addEventListener('close',()=>{const session=el.session;el.session=null;session?.onClosed?.();});
    form.addEventListener('input',()=>{if(el.session){el.session.dirty=true;error.textContent='';}});
    form.addEventListener('change',()=>{if(el.session){el.session.dirty=true;error.textContent='';}});
    return {el,form,scroll,error,save};
  }
  function dismiss(el) {
    const session=el.session;
    if(!session||session.busy)return;
    if(session.dirty&&!confirm('收入来源有未保存修改，确定放弃？'))return;
    el.close();
  }
  const transaction=dialog('budget-source-editor','修改收入来源');
  const grid=node('div','','form-grid');
  const date=input('date','source-date');
  // Select elements use the same account/category API as the original ledger editor.
  const accountSelect=node('select');accountSelect.id='source-account';
  const amount=input('number','source-amount'),category=node('select');category.id='source-category';
  const counterparty=input('text','source-counterparty',120),note=input('textarea','source-note',500);
  const reason=input('textarea','source-correction-reason',500),currency=node('small');
  date.required=amount.required=accountSelect.required=category.required=true;
  const amountField=field('金额',amount);amountField.append(currency);
  grid.append(field('日期',date),field('资金账户',accountSelect),amountField,field('分类',category),
    field('来源或对象',counterparty),field('备注',note),field('修改说明（可选）',reason));
  const guidance=node('p','','subtle-note'),transactionActions=node('div','','budget-source-actions');
  transaction.scroll.append(guidance,grid,transactionActions);
  const group=dialog('budget-source-group','收入冲销事项');
  const groupTitle=input('text','source-group-title',120),groupNote=input('textarea','source-group-note',500);
  groupTitle.required=true;
  const groupSummary=node('p','','subtle-note'),members=node('div','','table-wrap');
  const groupActions=node('div','','budget-source-actions');
  group.scroll.append(field('事项名称',groupTitle),field('备注',groupNote),groupSummary,members,groupActions);
  let loading=false;
  const openOriginal=(url,label)=>{const link=node('a',label,'text-link');link.href=url;link.target='_blank';link.rel='noopener';return link;};
  function setCurrency() {
    const session=transaction.el.session;if(!session)return;
    const selected=session.accounts.find(a=>a.id===Number(accountSelect.value));
    const code=selected?.currency_code,places=session.config.currencies.find(c=>c.code===code)?.decimal_places??2;
    amount.step=String(10**-places);amount.min=amount.step;currency.textContent=code||'';
  }
  accountSelect.addEventListener('change',setCurrency);
  async function refreshBudget(session) {
    try {await session.onSaved?.();}
    catch(error) {status(`收入来源已保存，但预算刷新失败：${error.message}。请重新加载页面，勿重复保存。`,true);}
  }
  function allocationButton(kind,id,session) {
    return button('查看 / 修改收入预算分配',()=>{
      if(session.busy)return;
      if(session.dirty){session.error.textContent='请先保存或取消来源修改，再调整预算分配。';return;}
      IncomeAllocations.open(kind,id,async()=>{await refreshBudget(session);});
    });
  }
  async function openTransaction(id, callbacks) {
    const [record,accounts,config,incomeCategories,expenseCategories]=await Promise.all([
      request(`/api/transactions/${id}`),request('/api/accounts?include_inactive=true'),
      request('/api/budget/config'),request('/api/categories?kind=income&include_inactive=true'),
      request('/api/categories?kind=expense&include_inactive=true'),
    ]);
    if(!['income','expense'].includes(record.transaction_type))throw new Error('来源已不再是收入或支出，请重新核对来源。');
    transaction.form.reset();transaction.error.textContent='';
    const session={record,accounts,config,...callbacks,error:transaction.error,dirty:false,busy:false};
    transaction.el.session=session;
    const accountId=record.source_account_id||record.destination_account_id;
    options(accountSelect,accounts.filter(a=>(a.is_active&&a.account_type!=='bank_term')||a.id===accountId),'选择账户',accountId);
    for(const option of accountSelect.options){const a=accounts.find(a=>String(a.id)===option.value);if(a)option.textContent+=` · ${a.currency_code}`;}
    options(category,[...incomeCategories,...expenseCategories].filter(c=>c.kind===record.transaction_type&&(c.is_active||c.id===record.category_id)),
      '选择分类',record.category_id);
    date.value=record.transaction_date;amount.value=record.inflow_amount||record.outflow_amount;
    counterparty.value=record.counterparty||'';note.value=record.note||'';
    transaction.el.querySelector('h2').textContent=record.transaction_type==='income'?'修改收入来源':'修改事项支出';
    guidance.textContent=record.term_deposit_id?'定存利息由定存到账记录维护，请通过下方入口修改。':
      '修改真实流水后，账户余额及来源净额会重新计算；已有预算分配保留，超配时会提示核对。';
    transactionActions.replaceChildren(openOriginal(record.term_deposit_id?`/term-deposits?deposit=${record.term_deposit_id}`:
      `/accounts?transaction=${id}`,record.term_deposit_id?'查看定存到账记录 ↗':'查看原始流水 ↗'));
    if(record.transaction_type==='income'&&(!record.offset_group_id||record.offset_group_kind==='income'))
      transactionActions.append(allocationButton(record.offset_group_id?'offset_group':'transaction',record.offset_group_id||id,session));
    for(const control of grid.querySelectorAll('input,select,textarea'))control.disabled=Boolean(record.term_deposit_id);
    transaction.save.hidden=Boolean(record.term_deposit_id);setCurrency();transaction.el.showModal();
  }
  function renderGroupMembers(record) {
    groupSummary.textContent=`归属日期 ${record.statistics_date} · ${record.category_name} · `+
      Object.entries(record.valuations).map(([code,v])=>`净额 ${v.amount??'未完整'} ${code}`).join('；');
    const table=node('table'),head=node('tr');
    for(const label of ['日期','方向','账户','金额','对象 / 分类','操作'])head.append(node('th',label));
    const thead=node('thead');thead.append(head);table.append(thead);const body=node('tbody');
    for(const member of record.members){
      const row=node('tr');row.dataset.transactionId=member.id;
      row.append(node('td',member.transaction_date),node('td',member.transaction_type==='income'?'收入':'支出'),
        node('td',member.account_name),node('td',`${member.amount} ${member.currency_code}`),
        node('td',member.counterparty||member.category_name));
      const actions=node('td');actions.append(button('修改流水',()=>editMember(member.id)));row.append(actions);body.append(row);
    }
    table.append(body);members.replaceChildren(table);
  }
  async function editMember(id) {
    const session=group.el.session;if(!session||session.busy)return;
    session.busy=true;group.error.textContent='';
    try {
      await openTransaction(id,{
        onSaved:async()=>{
          session.record=await request(`/api/offset-groups/${session.record.id}`);
          renderGroupMembers(session.record);await refreshBudget(session);
        },
        onClosed:()=>members.querySelector(`tr[data-transaction-id="${id}"] button`)?.focus({preventScroll:true}),
      });
    }catch(error){group.error.textContent=error.message;}
    finally{session.busy=false;}
  }
  async function open(source,onSaved,onClosed) {
    if(loading||transaction.el.open||group.el.open)return;
    loading=true;
    try {
      if(source.entry_type!=='offset_group')await openTransaction(source.entry_id,{onSaved,onClosed});
      else {
        const record=await request(`/api/offset-groups/${source.entry_id}`);
        group.form.reset();group.error.textContent='';
        const session={record,onSaved,onClosed,error:group.error,dirty:false,busy:false};
        group.el.session=session;groupTitle.value=record.title;groupNote.value=record.note||'';renderGroupMembers(record);
        groupActions.replaceChildren(allocationButton('offset_group',record.id,session),
          openOriginal(`/offsets?group=${record.id}`,'查看完整事项 / 调整成员 ↗'));
        group.el.showModal();
      }
    }finally{loading=false;}
  }
  transaction.form.addEventListener('submit',async event=>{
    event.preventDefault();const session=transaction.el.session;
    if(!session||session.busy||session.record.term_deposit_id)return;
    session.busy=true;transaction.error.textContent='';transaction.form.inert=true;
    try {
      const income=session.record.transaction_type==='income';
      await request(`/api/transactions/${session.record.id}`,{
        transaction_type:session.record.transaction_type,transaction_date:date.value,
        source_account_id:income?null:Number(accountSelect.value),destination_account_id:income?Number(accountSelect.value):null,
        outflow_amount:income?null:amount.value,inflow_amount:income?amount.value:null,
        category_id:Number(category.value),counterparty:counterparty.value.trim()||null,note:note.value.trim()||null,
        correction_reason:reason.value.trim()||null,
      },'PUT');
      session.dirty=false;await refreshBudget(session);transaction.el.close();
    }catch(error){transaction.error.textContent=error.message;}
    finally{session.busy=false;transaction.form.inert=false;}
  });
  group.form.addEventListener('submit',async event=>{
    event.preventDefault();const session=group.el.session;if(!session||session.busy)return;
    session.busy=true;group.error.textContent='';group.form.inert=true;
    try {
      await request(`/api/offset-groups/${session.record.id}`,{
        title:groupTitle.value.trim(),note:groupNote.value.trim()||null,kind:session.record.kind,
        transaction_ids:session.record.member_transaction_ids,expected_version:session.record.version,
      },'PUT');
      session.dirty=false;await refreshBudget(session);group.el.close();
    }catch(error){group.error.textContent=error.message;}
    finally{session.busy=false;group.form.inert=false;}
  });
  window.addEventListener('beforeunload',event=>{
    if([transaction,group].some(d=>d.el.open&&d.el.session?.dirty)){event.preventDefault();event.returnValue='';}
  });
  return {open};
})();

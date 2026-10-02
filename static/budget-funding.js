window.BudgetFunding = (() => {
  const {node,button,request} = BudgetUI;
  const picker=node('dialog','','app-dialog budget-dialog funding-picker');picker.id='funding-picker';
  picker.setAttribute('aria-labelledby','funding-picker-title');
  const heading=node('div','','dialog-heading'),title=node('h2','选择收入来源');title.id='funding-picker-title';
  heading.append(title,button('×',()=>picker.close()));heading.lastChild.setAttribute('aria-label','关闭收入选择');
  const form=node('form','','funding-search'),label=node('label','搜索收入或事项'),search=node('input');
  search.type='search';search.id='funding-keyword';search.placeholder='事项名称、收入备注';search.maxLength=120;label.append(search);
  const submit=node('button','搜索');submit.type='submit';form.append(label,submit);
  const notice=node('p','','subtle-note');notice.setAttribute('role','status');
  const list=node('div','','funding-candidates');list.id='funding-candidates';
  let owner=null,offset=0,total=0,serial=0;
  const navigation=node('div','','funding-pagination'),previous=button('上一页',()=>{offset=Math.max(0,offset-20);load();}),next=button('下一页',()=>{offset+=20;load();});
  navigation.append(previous,next);picker.append(heading,node('p','选择普通收入或收入冲销事项。事项使用净收入，AA 回款不计入。','subtle-note'),form,notice,list,navigation);document.body.append(picker);
  form.onsubmit=e=>{e.preventDefault();offset=0;load();};
  function parameters(context){
    const q=new URLSearchParams();if(context.planId)q.set('plan_id',context.planId);
    if(context.code)q.set('currency_code',context.code);
    if(context.total && /^\d+(\.\d+)?$/.test(context.total))q.set('total',context.total);
    return q;
  }
  function details(source){
    const el=node('details','','funding-usage');el.append(node('summary',`查看已绑定预案（${source.bindings.length}）`));
    for(const row of source.bindings)el.append(node('p',`${row.month} · ${row.system_name}${row.current?' · 当前预案':''}：${row.total??'未填总额'} ${row.currency_code}，占用 ${row.charge??'无法换算'} ${source.currency_code}`));
    if(!source.bindings.length)el.append(node('p','目前没有预案占用。'));return el;
  }
  async function load(){
    const ticket=++serial,current=owner;list.replaceChildren();notice.textContent='正在读取可用来源…';
    previous.disabled=next.disabled=true;
    try{
      const q=parameters(current.context());q.set('keyword',search.value.trim());q.set('offset',offset);q.set('limit',20);
      const data=await request(`/api/budget/funding-sources?${q}`);if(ticket!==serial||owner!==current)return;
      total=data.total;notice.textContent=total?`共 ${total} 个来源 · 只占用本预案总额，其他预算可继续使用剩余额度。`:'没有符合条件的收入。可修改关键词，或先记录收入。';
      for(const source of data.sources){
        const card=node('article','','funding-candidate');card.dataset.sourceKey=`${source.entry_type}:${source.entry_id}`;
        const top=node('div','','funding-candidate-heading');top.append(node('strong',source.title),node('span',source.entry_type==='offset_group'?'收入冲销事项':'普通收入','small-badge'));
        card.append(top,node('p',`${source.date} · 净收入 ${source.net??'未完整'} ${source.currency_code} · 当前可用 ${source.available??'未完整'} ${source.currency_code}`));
        if(source.reason||source.overallocated)card.append(node('p',source.reason||'已有预案超额占用，请先整理。','dialog-status error'));
        if(source.can_bind===false&&source.complete)card.append(node('p','当前预案金额超出可用额度，选中后请调整金额。','subtle-note'));
        card.append(details(source));
        const select=button('选择此来源',()=>{current.select(source);picker.close();});select.disabled=!source.complete;card.append(select);list.append(card);
      }
      previous.disabled=offset===0;next.disabled=offset+20>=total;
    }catch(error){if(ticket===serial)notice.textContent=error.message;}
  }
  function create(host,initial,context,onChange){
    let source=initial,error='',revision=0,pending=Promise.resolve(),timer;
    const content=node('div','','funding-content');
    const section=node('section','','funding-section'),head=node('div','','funding-heading'),actions=node('div','','funding-actions');
    const choose=button('选择收入来源',()=>{owner=api;offset=0;search.value='';picker.showModal();load();});choose.classList.add('choose-funding');
    const clear=button('解除绑定',()=>{clearTimeout(timer);revision++;source=null;error='';render();onChange();});clear.classList.add('clear-funding');
    actions.append(choose,clear);head.append(node('h3','收入来源'),actions);section.append(head,content);host.append(section);
    function render(){
      content.replaceChildren();clear.hidden=!source;choose.textContent=source?'更换来源':'选择收入来源';
      if(!source){content.append(node('p','可暂不绑定。绑定后按本预案总额核验来源额度，不会额外增加预算。','subtle-note'));return;}
      const link=node('a',`${source.title} ↗`,'funding-source-link');link.href=source.url;link.target='_blank';link.rel='noopener';content.append(link);
      const values=node('div','','funding-metrics');
      for(const [text,key] of [['来源净收入','net'],['其他预案占用','occupied'],['本预案可用','available']]){
        const value=node('div');value.append(node('small',text),node('strong',`${source[key]??'未完整'} ${source.currency_code}`));values.append(value);
      }
      content.append(values);
      if(source.requested!=null)content.append(node('p',`本预案占用 ${source.requested} ${source.currency_code} · 绑定后剩余 ${source.after_binding??'未完整'} ${source.currency_code}`,'funding-preview'));
      const message=error||source.reason||(source.can_bind===false?'预案金额超过来源可用额度，请调整金额后保存。':source.overallocated?'来源已超额占用，请调整相关预案。':'');
      if(message)content.append(node('p',message,'dialog-status error'));
      content.append(node('p',`来源计价 ${source.currency_code} · 预案换算参考日 ${source.date||'未知'}${source.nearest_count||source.charge_rate_status==='nearest'?' · 含邻近日期汇率':''}`,'subtle-note'),details(source),button('刷新来源额度',()=>refresh(),'matrix-amount refresh-funding'));
    }
    function refresh(){
      clearTimeout(timer);timer=null;if(!source)return Promise.resolve();
      const ticket=++revision,key={entry_type:source.entry_type,entry_id:source.entry_id};
      const q=parameters(context());for(const [k,v] of Object.entries(key))q.set(k,v);
      pending=(async()=>{try{const result=await request(`/api/budget/funding-sources?${q}`);if(ticket!==revision)return;source=result;error='';render();}
        catch(e){if(ticket===revision){error=e.message;render();}}})();return pending;
    }
    const api={context,select(value){source=value;error='';render();onChange();refresh();},
      schedule(){clearTimeout(timer);timer=setTimeout(refresh,200);},
      async payload(){if(timer){clearTimeout(timer);timer=null;await refresh();}await pending;if(!source)return null;if(error)throw new Error(error);return {entry_type:source.entry_type,entry_id:source.entry_id,expected_token:source.token};},
      dispose(){clearTimeout(timer);revision++;},refresh};
    render();if(source)refresh();return api;
  }
  return {create};
})();

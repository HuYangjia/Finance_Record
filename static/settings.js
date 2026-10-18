(() => {
  const {$,node,request,options,status,button}=BudgetUI;
  let config,categories=[],accounts=[],editing=null,editCollection=null,busy=false;
  const names={systems:'预算系统（预算用途）',storage:'存储方式',purposes:'历史用途标签',rules:'分类映射',presets:'记账预设'};
  const systemName=id=>config.systems.find(s=>s.id===id)?.name||'未选择';
  const categoryName=id=>categories.find(c=>c.id===id)?.display_name||'未指定分类';
  async function act(fn){if(busy)return;busy=true;document.querySelector('main').inert=true;try{await fn();}catch(e){status(e.message,true);}finally{busy=false;document.querySelector('main').inert=false;}}
  const copyPanel=node('details'),copyTitle=node('summary','复制历史用途标签'),copySource=node('select'),copyTarget=node('select');
  copyPanel.className='catalog-copy';
  copySource.id='catalog-source';copyTarget.id='catalog-target';
  for(const [label,select] of [['来源预算',copySource],['目标预算（须仅含待定栏目）',copyTarget]]){const el=node('label',label);el.append(select);copyPanel.append(el);}
  copyPanel.prepend(copyTitle);copyPanel.append(button('复制历史标签',()=>act(async()=>{config=await request('/api/budget/copy-catalog',{expected_version:config.version,source_system_id:Number(copySource.value),target_system_id:Number(copyTarget.value)},'POST');render();status('历史用途标签已复制。存储方式已由所有预算共用。');})));
  $('settings-list').after(copyPanel);
  function render(){
    options(copySource,config.systems,null,copySource.value||config.systems[0]?.id);options(copyTarget,config.systems.filter(s=>s.is_active),null,copyTarget.value||config.systems.at(-1)?.id);
    options($('daily-expense'),config.systems.filter(s=>s.is_active),null,config.daily_expense);
    options($('default-expense'),config.systems.filter(s=>s.is_active),'自动计入日常预算',config.defaults.expense);
    options($('default-income'),config.systems.filter(s=>s.is_active),'未归属',config.defaults.income);
    const selected=$('settings-system').value||new URLSearchParams(location.search).get('system')||config.systems[0]?.id;
    options($('settings-system'),config.systems,null,selected);
    const key=$('settings-collection').value, dimension=['storage','purposes'].includes(key);
    $('settings-system').disabled=key!=='purposes';copyPanel.hidden=key!=='purposes';
    $('settings-hint').textContent=key==='purposes'?'仅用于保留和维护旧记录的用途标签。新预算直接选择预算系统作为用途，无须创建这些标签。':key==='systems'?'这里创建的预算系统就是预算用途。先为每月填写总额，再分配存储方式。':dimension?'存储方式是预算总额下面的二级分配，可继续细分；已使用的栏目请停用保留历史。':'新支出按手动选择、预设、分类映射、模式默认的顺序确定预算。';
    const target=$('settings-list');target.replaceChildren();
    const rows=config[key].filter(r=>key!=='purposes'||r.budget_system_id===Number($('settings-system').value)).sort((a,b)=>(a.sort_order||0)-(b.sort_order||0)||a.id-b.id);
    if(key==='storage')$('settings-hint').textContent='全部预算共用此目录。新增一次即可选择；改名影响所有预算，已有金额和历史保留。';
    for(const r of rows){
      const row=node('div','','budget-setting-row');
      const title=r.path||r.name||`${r.mode==='expense'?'支出':'入账'} / ${categoryName(r.category_id)}`;
      row.append(node('strong',title),node('span',`${r.is_active?'启用':'已停用'}${key==='systems'&&r.id===config.daily_expense?' · 日常归属':''}${key==='rules'||key==='presets'?' · '+systemName(r.budget_system_id):''}`,r.is_active?'setting-state':'setting-state is-inactive'));
      if(key==='systems'){
        row.append(node('span',r.currency_conflict?'旧多币种待整理':r.currency_code||'首次编制时选币种','setting-state'));
        if(r.is_active&&!r.currency_conflict)row.append(button('修改币种',()=>act(()=>BudgetCurrency.open(r.id,config,async next=>{config=next;$('setting-editor').hidden=true;render();status('预算币种已更正，所有月份的数字保持不变，可逐月修改金额。');})),'quiet-button'));
      }
      if(!r.is_placeholder){row.append(button('编辑',()=>act(()=>open(key,r)),'quiet-button'),button('删除',()=>act(async()=>{if(!confirm(`删除“${title}”？有历史引用时请改用停用。`))return;config=await request(`/api/budget/config/${key}/${r.id}?expected_version=${config.version}`,undefined,'DELETE');render();status('已删除。');}),'quiet-button danger-button'));}
      else row.append(node('small','系统保留，用于尚未细分的计划'));
      target.append(row);
    }
    if(!rows.length)target.append(node('p','暂无项目，点击“新增”创建。'));
  }
  function refreshCategory(selected=''){options($('setting-category'),categories.filter(c=>c.kind===$('setting-mode').value&&c.is_active),'不预填分类',selected);window.refreshFrequentPickers($('setting-form'));}
  function refreshParent(selected=''){
    const rows=config[editCollection]||[];
    const descendants=new Set(editing?[editing.id]:[]);let changed=true;
    while(changed){changed=false;for(const r of rows)if(descendants.has(r.parent_id)&&!descendants.has(r.id)){descendants.add(r.id);changed=true;}}
    options($('setting-parent'),rows.filter(r=>(editCollection==='storage'||r.budget_system_id===Number($('setting-system').value))&&r.is_active&&!r.is_placeholder&&!descendants.has(r.id)),'顶级栏目',selected);
  }
  async function open(key,row=null){
    editCollection=key;editing=row;$('setting-form').reset();
    const dimension=['storage','purposes'].includes(key);
    const fields=new Set(key==='systems'?['name','currency','execution-start','opening','order','note']:key==='storage'?['name','parent','order','note']:dimension?['name','system','parent','order','note']:key==='rules'?['system','mode','category']:['name','system','mode','category','account']);
    document.querySelectorAll('[data-setting]').forEach(e=>{e.hidden=!fields.has(e.dataset.setting);for(const i of e.querySelectorAll('input,select'))i.disabled=e.hidden;});
    options($('setting-system'),config.systems.filter(s=>s.is_active||s.id===row?.budget_system_id),null,row?.budget_system_id||$('settings-system').value||config.systems[0]?.id);
    if(dimension&&row)$('setting-system').disabled=true;
    $('setting-name').required=fields.has('name');window.setFrequentPickerRequired($('setting-category'),key==='rules');
    $('setting-name').value=row?.name||'';$('setting-note').value=row?.note||'';$('setting-order').value=row?.sort_order||0;
    options($('setting-currency'),config.currencies.filter(c=>c.is_active||c.code===row?.currency_code),'首次编制时选择',row?.currency_code);
    $('setting-currency').disabled=key!=='systems'||Boolean(row?.plan_currencies?.length);
    $('setting-execution-start').value=row?.execution_start_month||'';$('setting-opening').value=row?.opening_balance||'0';
    $('setting-opening').step=String(10**-(config.currencies.find(c=>c.code===$('setting-currency').value)?.decimal_places??2));
    $('setting-mode').value=row?.mode||'expense';$('setting-active').value=row?.is_active===0?'0':'1';
    refreshCategory(row?.category_id);if(dimension)refreshParent(row?.parent_id);
    options($('setting-account'),accounts,'不预填账户',row?.account_id);
    $('setting-title').textContent=(row?'编辑':'新增')+names[key];$('setting-editor').hidden=false;$('setting-editor').scrollIntoView({behavior:'smooth'});
    let impact=$('setting-impact');if(!impact){impact=node('p');impact.id='setting-impact';$('setting-title').after(impact);}impact.textContent='';
    if(dimension&&row){const result=await request(`/api/budget/config/${key}/${row.id}/impact`);impact.textContent=`当前栏目及子项涉及 ${result.months.length} 个月、${result.item_count} 笔计划。改名或调整层级后，累计按新结构整理，历史版本保留原名称。`;}
    if(key==='systems'&&row){const result=await request(`/api/budget/config/systems/${row.id}/impact`);impact.textContent=`此预算已有 ${result.plan_count} 份月计划、${result.transaction_count} 笔原始流水引用。改名不改变归属，有引用时请停用保留历史。`;}
  }
  $('new-setting').onclick=()=>act(()=>open($('settings-collection').value));
  $('cancel-setting').onclick=()=>{$('setting-editor').hidden=true;};
  $('settings-collection').onchange=()=>{$('setting-editor').hidden=true;render();};$('settings-system').onchange=render;
  $('setting-system').onchange=()=>{if(['storage','purposes'].includes(editCollection))refreshParent();};$('setting-mode').onchange=()=>refreshCategory();
  $('setting-currency').onchange=()=>{$('setting-opening').step=String(10**-(config.currencies.find(c=>c.code===$('setting-currency').value)?.decimal_places??2));};
  $('setting-form').onsubmit=e=>{e.preventDefault();act(async()=>{
    const key=editCollection, body={expected_version:config.version,is_active:$('setting-active').value==='1'};
    if(key!=='rules')body.name=$('setting-name').value;
    if(!['systems','storage'].includes(key))body.budget_system_id=Number($('setting-system').value);
    if(key==='systems'&&!$('setting-currency').disabled)body.currency_code=$('setting-currency').value||null;
    if(key==='systems'){body.execution_start_month=$('setting-execution-start').value||null;body.opening_balance=$('setting-opening').value||'0';}
    if(['systems','storage','purposes'].includes(key)){body.sort_order=Number($('setting-order').value);body.note=$('setting-note').value;}
    if(['storage','purposes'].includes(key))body.parent_id=Number($('setting-parent').value)||null;
    if(['rules','presets'].includes(key)){body.mode=$('setting-mode').value;body.category_id=Number($('setting-category').value)||null;}
    if(key==='presets')body.account_id=Number($('setting-account').value)||null;
    config=await request(`/api/budget/config/${key}${editing?'/'+editing.id:''}`,body,editing?'PUT':'POST');
    $('setting-editor').hidden=true;render();status('已保存。历史流水归属保持不变。');
  });};
  $('budget-defaults').onsubmit=e=>{e.preventDefault();act(async()=>{config=await request('/api/budget/defaults',{expected_version:config.version,daily_expense:Number($('daily-expense').value),expense:Number($('default-expense').value)||null,income:Number($('default-income').value)||null},'PUT');render();status('规则已保存。未单独标注的支出按日常预算统计，明确归属的记录保持不变。');});};
  async function load(){
    [config,categories,accounts]=await Promise.all([request('/api/budget/config'),Promise.all([request('/api/categories?kind=expense&include_inactive=true'),request('/api/categories?kind=income&include_inactive=true')]).then(a=>a.flat()),request('/api/accounts')]);
    render();
  }
  $('reload-settings').onclick=()=>act(async()=>{await load();$('setting-editor').hidden=true;status('已重新加载。');});
  window.createFrequentPicker($('setting-category'),{activityKey:'usage',displayMode:'ranked-scroll',visibleRows:8});
  load().catch(e=>status(e.message,true));
})();

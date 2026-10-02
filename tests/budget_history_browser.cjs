// Run only against tests/budget_browser_server.py and its disposable database.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async r=>{assert(r.ok(),await r.text());return r.json();};
  const get=async path=>json(await api.get(path));
  let fixture;
  for(let attempt=0;attempt<30;attempt++){
    try{fixture=await get('/__budget_test_fixture');break;}
    catch(e){if(attempt===29)throw e;await new Promise(resolve=>setTimeout(resolve,300));}
  }
  assert.deepEqual(fixture,{fixture:'budget-browser',temporary:true});
  let config=await get('/api/budget/config');
  config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name:'历史月份回归'}}));
  const system=config.systems.find(s=>s.name==='历史月份回归').id;
  const storage=config.storage.find(s=>s.name==='ETF').id;
  const item={storage_method_id:storage,purpose_id:config.purposes.find(s=>s.budget_system_id===system).id,title:'十月旧预算',amount:'500'};
  const path=(month,code='CNY')=>`/api/budget/plans/${system}/${month}/${code}`;
  const october=await json(await api.put(path('2026-10'),{data:{expected_version:0,items:[item]}}));
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];page.setDefaultTimeout(15000);
    page.on('pageerror',e=>errors.push(e.message));let discard=true;page.on('dialog',d=>discard?d.accept():d.dismiss());
    const text=async(s,v)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[s,v]);
    const idle=()=>page.waitForFunction(()=>!document.querySelector('main').inert);
    const loaded=async m=>{await text('#month-plans-title',m);await idle();};
    const close=async()=>{if(await page.locator('#plan-editor[open]').count())await page.click('#close-plan');};
    const month=async m=>{await close();await page.fill('#plan-month',m);await loaded(m);};
    const save=async(v,code='CNY')=>{await page.click('#save-plan');await page.waitForSelector('#plan-editor[open]',{state:'hidden'});await idle();await edit(code);await text('.currency-budget[data-currency='+code+'] [data-role=version]',`已保存版本 ${v}`);};
    const edit=async(code='CNY')=>{await page.locator('#month-plans tr').filter({hasText:'历史月份回归'}).filter({hasText:code}).getByRole('button').click();await page.waitForSelector('.currency-budget[data-currency='+code+']');await idle();};
    await page.goto(base+'/budgets');await text('#budget-status','已加载');
    await page.selectOption('#plan-system',String(system));await page.locator('#budget-load button[type=submit]').click();await idle();
    await month('2026-10');await edit();assert.equal(await page.locator('.currency-budget[data-currency=CNY] .currency-total').inputValue(),'500.00');await text('#currency-budgets','旧计划未填总额');
    await close();await page.click('#plan-previous-month');await loaded('2026-09');await page.click('#new-budget');await text('#plan-state','尚未编制');
    assert.equal(await page.locator('#entry-system').inputValue(),String(system));assert.equal(await page.locator('.currency-budget[data-currency=CNY] .currency-select').inputValue(),'CNY');
    assert.equal(await page.locator('[data-field=purpose_id]').count(),0);
    await page.fill('.currency-budget[data-currency=CNY] .currency-total','1000');await save(1);await text('.currency-budget[data-currency=CNY] .allocation-balance','待分配 1000.00');
    for(const amount of ['150','40']){await page.click('.currency-budget[data-currency=CNY] .add-allocation');const row=page.locator('.currency-budget[data-currency=CNY] .allocation-items tr').last();await row.locator('[data-field=storage_method_id]').selectOption(String(storage));await row.locator('[data-field=amount]').fill(amount);}
    await page.fill('.currency-budget[data-currency=CNY] .currency-total','1200');await save(2);await save(3);
    const september=await get(path('2026-09'));assert.equal(september.total,'1200.00');assert.equal(september.allocated,'190.00');assert.equal(september.items.length,2);
    assert.deepEqual(await get(path('2026-10')),october);
    let summary=await get(`/api/budget/overview?system_id=${system}&end_month=2026-10&currency_code=CNY`);
    assert.equal(summary.currencies[0].cumulative.budget_total,'1700.00');assert.equal(summary.currencies[0].cumulative.allocated,'690.00');
    await page.click('#plan-history');await text('#budget-detail-content','版本 3');await text('#budget-detail-content','1000.00');await page.locator('[data-budget-close]').click();
    console.log('PASS September total-first backfill, secondary allocations, revisions and later totals; legacy October unchanged');

    await page.locator('.currency-budget[data-currency=CNY] [data-field=amount]').first().fill('175');discard=false;
    await page.keyboard.press('Escape');await text('#budget-status','已保留未保存草稿');assert.equal(await page.locator('#plan-month').inputValue(),'2026-09');assert.equal(await page.locator('.currency-budget[data-currency=CNY] [data-field=amount]').first().inputValue(),'175');
    discard=true;
    await page.route('**/api/budget/monthly/**',route=>route.request().method()==='PUT'?route.fulfill({status:503,contentType:'application/json',body:'{"detail":"计划保存失败"}'}):route.continue());
    await page.click('#save-plan');await text('#plan-error','计划保存失败');assert.equal(await page.locator('.currency-budget[data-currency=CNY] [data-field=amount]').first().inputValue(),'175');await page.unroute('**/api/budget/monthly/**');await save(4);
    await close();
    await page.route('**/api/budget/overview?**',route=>new URL(route.request().url()).searchParams.get('end_month')==='2026-08'?route.fulfill({status:503,contentType:'application/json',body:'{"detail":"月份加载失败"}'}):route.continue());
    await page.click('#plan-previous-month');await text('#budget-status','月份加载失败');assert.equal(await page.locator('#plan-month').inputValue(),'2026-09');await page.unroute('**/api/budget/overview?**');await edit();assert.equal(await page.locator('.currency-budget[data-currency=CNY] [data-field=amount]').first().inputValue(),'175.00');
    assert.equal((await get(path('2026-09'))).allocated,'215.00');assert.equal((await get(path('2026-08'))).version,0);
    console.log('PASS modal cancellation and failed saves preserve draft; failed loads preserve September scope');

    assert.equal(await page.locator('#add-currency').count(),0);
    assert.equal(await page.locator('.currency-budget').count(),1);
    assert.equal(await page.locator('.currency-select').isDisabled(),false);
    await page.selectOption('.currency-select','HKD');
    await text('#budget-currency-dialog','全部 2 个月计划');
    await text('#budget-currency-dialog','1200.00 HKD');
    await page.locator('#budget-currency-dialog').getByRole('button',{name:'取消'}).click();
    assert.equal((await get(path('2026-09'))).currency_code,'CNY');
    await page.selectOption('.currency-select','HKD');
    await page.click('#save-budget-currency');
    await text('.currency-budget[data-currency=HKD] [data-role=version]','已保存版本 5');await idle();
    assert.equal((await get(path('2026-09','HKD'))).total,'1200.00');
    assert.equal((await get(path('2026-09','HKD'))).allocated,'215.00');
    assert.equal((await get(path('2026-10','HKD'))).id,october.id);
    assert.equal((await get(path('2026-10','HKD'))).allocated,'500.00');
    assert.equal((await get(path('2026-09'))).version,0);
    await page.click('#plan-history');await text('#budget-detail-content','1200.00 HKD');await text('#budget-detail-content','1000.00 CNY');await page.locator('[data-budget-close]').click();
    console.log('PASS existing currency editable, preview cancellation, all-month correction retains numbers and original currency history');

    // A changed month invalidates the entire currency preview.
    await page.click('#change-currency');await page.selectOption('#budget-currency-code','JPY');
    const current=await get(path('2026-09','HKD'));
    await json(await api.put(path('2026-09','HKD'),{data:{expected_version:current.version,total:'1250',items:current.items,allocation_mode:true}}));
    await page.click('#save-budget-currency');await text('#budget-currency-error','已更新');
    await page.locator('#budget-currency-dialog').getByRole('button',{name:'取消'}).click();
    assert.equal((await get(path('2026-09','HKD'))).total,'1250.00');
    await page.locator('.currency-total').fill('1300');await page.click('#save-plan');await text('#plan-error','已更新');
    assert.equal(await page.locator('.currency-total').inputValue(),'1300');
    await page.selectOption('.currency-select','CNY');await text('#plan-error','请先保存或取消');
    assert.equal(await page.locator('.currency-select').inputValue(),'HKD');
    await page.click('#close-plan');await edit('HKD');
    await text('.currency-budget [data-role=version]','已保存版本 6');
    console.log('PASS currency preview and month version conflicts preserve data and drafts');

    await month('2026-11');await page.click('#new-budget');await text('#plan-state','2026-11');await idle();
    assert.equal(await page.locator('.currency-select').inputValue(),'HKD');
    await page.locator('summary').filter({hasText:'从其他月份复制'}).click();
    await page.fill('#copy-month','2026-09');await page.click('#copy-plan');await text('#budget-status','已复制为草稿');
    assert.equal((await get(path('2026-11','HKD'))).version,0);
    assert.equal(await page.locator('.currency-budget').count(),1);
    await save(1,'HKD');assert.equal((await get(path('2026-11','HKD'))).total,'1250.00');
    assert.equal((await get(path('2026-11','HKD'))).allocated,'215.00');
    assert.equal((await get(path('2026-11'))).version,0);
    await month('2026-10');await page.fill('#plan-start','2026-10');await page.locator('#budget-load button[type=submit]').click();await idle();
    await page.click('#plan-previous-month');await loaded('2026-09');assert.equal(await page.locator('#plan-start').inputValue(),'');await text('#budget-status','累计起始月已清空');
    await month('2026-01');await page.click('#plan-previous-month');await loaded('2025-12');await page.click('#plan-next-month');await loaded('2026-01');
    await page.click('#plan-current-month');await loaded(await page.evaluate(()=>{const d=new Date();return String(d.getFullYear())+'-'+String(d.getMonth()+1).padStart(2,'0');}));
    console.log('PASS new month inherits budget currency, copy preview, cumulative range and cross-year navigation');

    config=await get('/api/budget/config');
    config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name:'港币第二预算',currency_code:'HKD'}}));
    const peer=config.systems.find(s=>s.name==='港币第二预算').id;
    const peerETF=config.storage.find(s=>s.name==='ETF').id;
    await json(await api.put('/api/budget/plans/'+peer+'/2026-09/HKD',{data:{expected_version:0,total:'2000',allocation_mode:true,items:[{storage_method_id:peerETF,amount:'800'}]}}));
    await page.goto(base+'/budgets');await text('#budget-status','已加载');await month('2026-09');
    const hkdTable=page.locator('#budget-matrix > h3').filter({hasText:'HKD'}).locator('+ .table-wrap table');
    assert.equal(await hkdTable.locator('thead th').filter({hasText:/^ETF$/}).count(),1);
    assert.deepEqual(await hkdTable.locator('tbody tr').last().locator('td,th').allTextContents(),['合计','1015.00','2235.00','3250.00']);
    await edit('HKD');await page.screenshot({path:'/tmp/finance-budget-history-desktop.png',fullPage:true});

    // Settings offers the same correction flow, including all saved months.
    await page.goto(base+'/settings');await text('#settings-list','历史月份回归');
    const row=page.locator('.budget-setting-row').filter({hasText:'历史月份回归'});
    await row.getByRole('button',{name:'修改币种'}).click();await text('#budget-currency-dialog','全部 3 个月计划');
    await page.selectOption('#budget-currency-code','CNY');await page.click('#save-budget-currency');await text('#budget-status','预算币种已更正');
    for(const m of ['2026-09','2026-11'])assert.equal((await get(path(m))).total,'1250.00');
    assert.equal((await get(path('2026-10'))).allocated,'500.00');
    assert.equal((await get('/api/budget/plans/'+peer+'/2026-09/HKD')).total,'2000.00');
    await page.goto(base+'/budgets');await text('#budget-status','已加载');await month('2026-09');await edit();
    assert.equal(await page.locator('.currency-select').inputValue(),'CNY');
    await page.click('#plan-history');await text('#budget-detail-content','1250.00 CNY');await text('#budget-detail-content','1250.00 HKD');
    await page.locator('[data-budget-close]').click();
    assert.deepEqual(errors,[]);
    console.log('PASS shared ETF matrix, settings currency correction, other budget isolation and desktop flow');
  }finally{await browser.close();await api.dispose();}
})().catch(error=>{console.error(error);process.exit(1);});

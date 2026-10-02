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
  config=await json(await api.post('/api/budget/config/storage',{data:{expected_version:config.version,budget_system_id:system,name:'ETF'}}));
  const storage=config.storage.find(s=>s.budget_system_id===system&&s.name==='ETF').id;
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
    const month=async m=>{await page.fill('#plan-month',m);await loaded(m);};
    const save=async(v,code='CNY')=>{await page.click('#save-plan');await text('.currency-budget[data-currency='+code+'] [data-role=version]',`已保存版本 ${v}`);await idle();};
    const edit=async(code='CNY')=>{await page.locator('#month-plans tr').filter({hasText:'历史月份回归'}).filter({hasText:code}).getByRole('button').click();await page.waitForSelector('.currency-budget[data-currency='+code+']');await idle();};
    await page.goto(base+'/budgets');await text('#budget-status','已加载');
    await page.selectOption('#plan-system',String(system));await page.locator('#budget-load button[type=submit]').click();await idle();
    await month('2026-10');await edit();assert.equal(await page.locator('.currency-budget[data-currency=CNY] .currency-total').inputValue(),'500.00');await text('#currency-budgets','旧计划未填总额');
    await page.click('#plan-previous-month');await loaded('2026-09');await page.click('#new-budget');await text('#plan-state','尚未编制');
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
    await page.click('#plan-next-month');await text('#budget-status','已保留未保存草稿');assert.equal(await page.locator('#plan-month').inputValue(),'2026-09');assert.equal(await page.locator('.currency-budget[data-currency=CNY] [data-field=amount]').first().inputValue(),'175');
    discard=true;
    await page.route('**/api/budget/overview?**',route=>new URL(route.request().url()).searchParams.get('end_month')==='2026-08'?route.fulfill({status:503,contentType:'application/json',body:'{"detail":"月份加载失败"}'}):route.continue());
    await page.click('#plan-previous-month');await text('#budget-status','月份加载失败');assert.equal(await page.locator('#plan-month').inputValue(),'2026-09');assert.equal(await page.locator('.currency-budget[data-currency=CNY] [data-field=amount]').first().inputValue(),'175');await save(4);await page.unroute('**/api/budget/overview?**');
    assert.equal((await get(path('2026-09'))).allocated,'215.00');assert.equal((await get(path('2026-08'))).version,0);
    console.log('PASS cancellation and failed loads preserve the draft and save to September');

    await page.click('#add-currency');await page.locator('.currency-budget').last().locator('.currency-select').selectOption('HKD');const hkdGroup=page.locator('.currency-budget[data-currency=HKD]');
    await hkdGroup.locator('.currency-total').fill('300');await hkdGroup.locator('.add-allocation').click();
    await hkdGroup.locator('[data-field=storage_method_id]').selectOption(String(storage));await hkdGroup.locator('[data-field=amount]').fill('100');
    assert(await hkdGroup.locator('.currency-select option[value=CNY]').evaluate(option=>option.disabled));
    await save(5);await text('.currency-budget[data-currency=HKD] [data-role=version]','已保存版本 1');
    assert.equal((await get(path('2026-09','HKD'))).total,'300.00');assert.equal((await get(path('2026-09'))).total,'1200.00');
    await text('#month-plans','CNY');await text('#month-plans','HKD');assert.equal(await page.locator('#month-plans tbody tr').count(),1);assert.equal(await page.locator('#budget-matrix > h3').count(),2);
    await page.selectOption('#plan-currency','CNY');await page.locator('#budget-load button[type=submit]').click();await idle();await edit();
    assert.equal(await page.locator('.currency-budget').count(),2,'Currency filter must not hide other editor groups');await text('.currency-budget[data-currency=CNY] .allocation-balance','985.00');
    // A conflict in the second currency rolls back the first and retains both drafts.
    const hkd=await get(path('2026-09','HKD'));await json(await api.put(path('2026-09','HKD'),{data:{expected_version:hkd.version,total:'350',items:hkd.items,allocation_mode:true}}));
    await page.locator('.currency-budget[data-currency=CNY] .currency-total').fill('1300');await page.locator('.currency-budget[data-currency=HKD] .currency-total').fill('400');await page.click('#save-plan');await text('#plan-error','HKD');await text('#plan-error','已更新');
    assert.equal(await page.locator('.currency-budget[data-currency=CNY] .currency-total').inputValue(),'1300');assert.equal(await page.locator('.currency-budget[data-currency=HKD] .currency-total').inputValue(),'400');assert.equal((await get(path('2026-09'))).total,'1200.00');
    await page.click('#close-plan');await edit();await text('.currency-budget[data-currency=HKD] [data-role=version]','已保存版本 2');
    await page.click('#add-currency');const unsaved=page.locator('.currency-budget').last();await unsaved.locator('.currency-select').selectOption('USD');await unsaved.locator('.currency-total').fill('80');await unsaved.getByRole('button',{name:'移除此组'}).click();assert.equal(await page.locator('.currency-budget').count(),2);
    await page.click('#close-plan');await page.selectOption('#plan-currency','');await page.locator('#budget-load button[type=submit]').click();await idle();
    await month('2026-10');await page.fill('#plan-start','2026-10');await page.locator('#budget-load button[type=submit]').click();await idle();await page.click('#plan-previous-month');await loaded('2026-09');assert.equal(await page.locator('#plan-start').inputValue(),'');await text('#budget-status','累计起始月已清空');
    await month('2026-01');await page.click('#plan-previous-month');await loaded('2025-12');await page.click('#plan-next-month');await loaded('2026-01');
    await page.click('#plan-current-month');await loaded(await page.evaluate(()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;}));
    const daily=config.defaults.expense,dailyETF=config.storage.find(s=>s.budget_system_id===daily&&s.name==='ETF').id;
    await json(await api.put(`/api/budget/plans/${daily}/2026-09/CNY`,{data:{expected_version:0,total:'2000',allocation_mode:true,items:[{storage_method_id:dailyETF,amount:'800'}]}}));
    await month('2026-09');await page.selectOption('#plan-system','');await page.locator('#budget-load button[type=submit]').click();await idle();
    const cny=page.locator('#budget-matrix > h3').filter({hasText:'CNY'}).locator('+ .table-wrap table');
    assert.equal(await cny.locator('thead th').filter({hasText:/^ETF$/}).count(),1,'Identical storage paths must share a column');
    await text('#budget-matrix','日常预算');await text('#budget-matrix','历史月份回归');
    const totalRow=cny.locator('tbody tr').last();assert.deepEqual(await totalRow.locator('td,th').allTextContents(),['合计','1015.00','2185.00','3200.00']);
    const hkdTable=page.locator('#budget-matrix > h3').filter({hasText:'HKD'}).locator('+ .table-wrap table');assert.equal(await hkdTable.locator('tbody tr').last().locator('th').last().textContent(),'350.00');
    await page.selectOption('#plan-system',String(system));await page.locator('#budget-load button[type=submit]').click();await idle();
    console.log('PASS all-system matrix merges ETF column, preserves each purpose and separates currencies');
    await month('2026-09');await edit();await page.screenshot({path:'/tmp/finance-budget-history-desktop.png',fullPage:true});
    // Copy all currencies in one preview; retain target-only saved currencies.
    await month('2026-11');await page.click('#new-budget');await text('#plan-state','2026-11');await idle();await page.locator('.currency-select').selectOption('JPY');await page.locator('.currency-total').fill('100');await save(1,'JPY');
    await page.locator('summary').filter({hasText:'从其他月份复制'}).click();await page.fill('#copy-month','2026-09');await page.click('#copy-plan');await text('#budget-status','已复制为草稿');
    assert.equal(await page.locator('.currency-budget').count(),3);await text('#budget-status','保留来源月没有的 JPY');assert.equal((await get(path('2026-11'))).version,0);
    await save(2,'JPY');assert.equal((await get(path('2026-11'))).total,'1200.00');assert.equal((await get(path('2026-11','HKD'))).total,'350.00');assert.equal((await get(path('2026-11','JPY'))).total,'100');
    await page.fill('#copy-month','2026-10');await page.click('#copy-plan');await text('#budget-status','保留来源月没有的 HKD、JPY');await save(3,'JPY');assert.equal((await get(path('2026-11','HKD'))).total,'350.00');
    // A blank default group must not become an unwanted extra currency on copy.
    await json(await api.put(path('2026-06','HKD'),{data:{expected_version:0,total:'90',items:[],allocation_mode:true}}));
    await month('2026-12');await page.click('#new-budget');await text('#plan-state','2026-12');await idle();await page.fill('#copy-month','2026-06');await page.click('#copy-plan');await text('#budget-status','已复制为草稿');assert.equal(await page.locator('.currency-budget').count(),1);assert.equal(await page.locator('.currency-select').inputValue(),'HKD');await save(1,'HKD');
    console.log('PASS multi-currency copy, target-only currency retention, preview isolation and no empty default group');
    assert.deepEqual(errors,[]);console.log('PASS multi-currency monthly editor, grouped list, filter isolation, atomic version conflict, drafts and desktop layout');
  }finally{await browser.close();await api.dispose();}
})().catch(error=>{console.error(error);process.exit(1);});

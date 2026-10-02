// Run only against tests/budget_browser_server.py; it owns a disposable database.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  let fixture;
  for(let attempt=0;attempt<30;attempt++){
    try{fixture=await (await api.get('/__budget_test_fixture')).json();break;}
    catch(e){if(attempt===29)throw e;await new Promise(resolve=>setTimeout(resolve,300));}
  }
  assert.deepEqual(fixture,{fixture:'budget-browser',temporary:true});
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];
  page.setDefaultTimeout(15000);
  page.on('pageerror',e=>{errors.push(e.message);console.error('BROWSER ERROR:',e.message);});page.on('dialog',d=>d.accept());
  const get=async path=>{const r=await api.get(path);assert(r.ok(),await r.text());return r.json();};
  const text=async (selector,value)=>{await page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[selector,value]);};
  const pick=async (id,value)=>page.locator('#'+id).evaluate((el,v)=>{el.value=String(v);el.dispatchEvent(new Event('change',{bubbles:true}));window.refreshFrequentPickers?.();},value);
  const config=await get('/api/budget/config'),daily=config.defaults.expense;
  const cats=await get('/api/categories?kind=expense'),incomeCats=await get('/api/categories?kind=income');
  const food=cats.find(c=>c.name==='餐饮').id,traffic=cats.find(c=>c.name==='交通').id,salary=incomeCats.find(c=>c.name==='工资/劳务').id;
  const accounts=await get('/api/accounts'),octopus=accounts.find(a=>a.code==='OCTOPUS-STUDENT').id,wechat=accounts.find(a=>a.code==='WECHAT').id;
  if(process.argv.includes('--visual')){
    for(const width of (process.argv.includes('--mobile')?[1440,390]:[1440])){
      await page.setViewportSize({width,height:width===390?844:1050});
      for(const [url,name] of [['/','entry'],['/budgets','plan'],['/settings','settings'],['/accounts','query']]){
        await page.goto(base+url);await page.waitForLoadState('networkidle');
        if(url==='/accounts')await page.locator('.detail-view-mode[data-view=search]').click();
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),url+' overflows '+width);
        await page.screenshot({path:`/tmp/finance-budget-${name}-${width}.png`,fullPage:true,animations:'disabled'});
      }
    }
    await page.goto(base+'/');await page.waitForFunction(()=>!document.querySelector('#expense-budget-system').disabled);
    await page.fill('#expense-amount','19');await page.locator('.entry-mode[data-mode=income]').click();await page.fill('#income-amount','22');
    await page.locator('.entry-mode[data-mode=transfer]').click();await page.fill('#transfer-outflow-amount','7');
    await page.locator('.entry-mode[data-mode=expense]').click();assert.equal(await page.locator('#expense-amount').inputValue(),'19');
    await page.selectOption('#expense-budget-system','');await text('#expense-budget-hint','自动计入日常');
    assert.deepEqual(errors,[]);console.log('PASS final Chinese desktop/mobile layouts, independent drafts and automatic daily option');
    await browser.close();await api.dispose();return;
  }
  await page.goto(base+'/settings');await text('#settings-list','日常预算');
  await page.click('#new-setting');await page.fill('#setting-name','专项预算');
  await page.locator('#setting-form button[type=submit]').click();await text('#settings-list','专项预算');
  let settings=await get('/api/budget/config');const special=settings.systems.find(s=>s.name==='专项预算').id;
  await page.selectOption('#settings-collection','storage');await text('#settings-list','ETF');assert(await page.locator('#settings-system').isDisabled());
  const sharedPlan=await get('/api/budget/plans/'+special+'/2026-10/CNY');assert(sharedPlan.dimensions.storage.some(s=>s.name==='ETF'));
  await page.selectOption('#settings-collection','rules');await page.click('#new-setting');await page.selectOption('#setting-system',String(special));await page.selectOption('#setting-category',String(traffic));await page.locator('#setting-form button[type=submit]').click();await text('#settings-list','交通');
  await page.selectOption('#settings-collection','presets');await page.click('#new-setting');await page.fill('#setting-name','工资B');await page.selectOption('#setting-mode','income');await page.selectOption('#setting-system',String(special));await page.selectOption('#setting-category',String(salary));await page.selectOption('#setting-account',String(octopus));await page.locator('#setting-form button[type=submit]').click();await text('#settings-list','工资B');
  settings=await get('/api/budget/config');const preset=settings.presets.find(p=>p.name==='工资B').id;
  assert.equal(await page.locator('#daily-expense').inputValue(),String(daily));
  await page.locator('#budget-defaults button[type=submit]').click();await text('#budget-status','规则已保存');
  assert.equal((await get('/api/budget/config')).daily_expense,daily);
  await page.selectOption('#settings-collection','systems');await page.screenshot({path:'/tmp/finance-budget-settings.png',fullPage:true});
  console.log('PASS settings, catalog copy, category rule and salary preset');

  await page.goto(base+'/budgets');await text('#budget-status','已加载');
  await page.fill('#plan-month','2026-10');await text('#month-plans-title','2026-10');
  await page.click('#new-budget');await text('#plan-state','2026-10');
  assert.equal(await page.locator('#entry-system option').count(),settings.systems.filter(s=>s.is_active).length);
  assert(await page.locator('.currency-select option').count()>1);assert.equal(await page.locator('[data-field=purpose_id]').count(),0);
  await page.fill('.currency-total','1000');await page.click('#save-plan');await text('[data-role=version]','已保存版本 1');await text('.allocation-balance','待分配 1000.00');
  const storage=config.storage.find(r=>r.name==='ETF').id;
  for(const amount of ['200','300']){
    await page.click('.add-allocation');const row=page.locator('.allocation-items tr').last();
    await row.locator('[data-field=title]').fill('慈善储备');await row.locator('[data-field=storage_method_id]').selectOption(String(storage));await row.locator('[data-field=amount]').fill(amount);
  }
  await page.fill('.currency-total','400');await page.click('#save-plan');await text('#plan-error','不能超过总预算');
  assert.equal((await get('/api/budget/plans/'+daily+'/2026-10/CNY')).version,1);
  await page.fill('.currency-total','1000');await page.click('#save-plan');await text('[data-role=version]','已保存版本 2');await text('.allocation-balance','待分配 500.00');await text('#budget-matrix','1000.00');
  await page.locator('#budget-matrix').getByRole('button',{name:'500.00',exact:true}).click();await text('#budget-detail-content','200.00');await text('#budget-detail-content','300.00');await page.locator('[data-budget-close]').click();
  await page.click('.add-allocation');const third=page.locator('.allocation-items tr').nth(2);await third.locator('[data-field=amount]').fill('50');await third.locator('[data-field=note]').fill('草稿保留');
  await third.locator('td').first().getByRole('button',{name:'＋新增',exact:true}).click();await page.fill('#new-dimension-name','黄金');await page.getByRole('button',{name:'创建栏目',exact:true}).click();await text('#budget-status','存储栏目已创建');
  assert.equal(await third.locator('[data-field=amount]').inputValue(),'50');assert.equal(await third.locator('[data-field=note]').inputValue(),'草稿保留');assert.equal(await third.locator('[data-field=storage_method_id] option:checked').textContent(),'黄金');
  await page.click('#save-plan');await text('[data-role=version]','已保存版本 3');await text('.allocation-balance','待分配 450.00');
  await page.fill('#plan-month','2026-11');await text('#month-plans-title','2026-11');await page.click('#new-budget');await text('#plan-state','2026-11');
  await page.locator('summary').filter({hasText:'从其他月份复制'}).click();await page.fill('#copy-month','2026-10');await page.click('#copy-plan');await text('#budget-status','已复制为草稿');
  assert.equal((await get('/api/budget/plans/'+daily+'/2026-11/CNY')).version,0);
  await page.click('#save-plan');await text('[data-role=version]','已保存版本 1');await page.selectOption('#matrix-scope','cumulative');await text('#budget-matrix','2000.00');await text('#budget-matrix','1000.00');
  await page.click('#save-plan');await text('[data-role=version]','已保存版本 2');await text('#budget-matrix','2000.00');
  await page.click('#plan-history');await text('#budget-detail-content','版本 2');await page.locator('[data-budget-close]').click();
  const download=page.waitForEvent('download');await page.click('#budget-export');assert.equal((await download).suggestedFilename(),'budget-plan.xlsx');
  await page.screenshot({path:'/tmp/finance-budget-plan.png',fullPage:true});
  console.log('PASS budget system as purpose, explicit currency, total before allocation, overspend validation, inline storage, matrix, copy, versions and export');

  await page.goto(base+'/');await page.waitForFunction(()=>!document.querySelector('#expense-budget-system').disabled);
  assert.equal(await page.locator('form').count(),3);assert.equal(await page.locator('#expense-budget-system').inputValue(),String(daily));
  const picker=page.locator('#source-account').locator('..').locator('.frequent-picker');await picker.locator('.frequent-picker-trigger').click();if(await picker.locator('.frequent-picker-other-toggle').count())await picker.locator('.frequent-picker-other-toggle').click();assert(await picker.locator('.frequent-picker-option').count()>1);await page.keyboard.press('Escape');
  await page.fill('#transaction-date','2026-10-01');await page.fill('#expense-amount','25');await page.fill('#note','支出草稿');
  await page.locator('.entry-mode[data-mode=income]').click();await page.fill('#income-date','2026-10-02');await page.fill('#income-amount','10');await page.fill('#income-note','入账草稿');
  await page.locator('.entry-mode[data-mode=transfer]').click();await page.fill('#transfer-note','转账草稿');
  await page.locator('.entry-mode[data-mode=expense]').click();assert.equal(await page.locator('#expense-amount').inputValue(),'25');assert.equal(await page.locator('#note').inputValue(),'支出草稿');
  await page.selectOption('#expense-budget-system','');await pick('expense-category',traffic);assert.equal(await page.locator('#expense-budget-system').inputValue(),'');
  await page.locator('#transaction-form').getByRole('button',{name:'恢复自动预算'}).click();assert.equal(await page.locator('#expense-budget-system').inputValue(),String(special));
  await pick('expense-category',food);assert.equal(await page.locator('#expense-budget-system').inputValue(),String(daily));await page.selectOption('#expense-budget-system',String(special));
  await page.click('#submit-transaction');await text('#form-status','已记录 25');assert.equal(await page.locator('#expense-budget-system').inputValue(),String(daily));
  await page.fill('#transaction-date','2026-10-01');await page.fill('#expense-amount','40');await page.selectOption('#expense-budget-system','');await page.click('#submit-transaction');await text('#form-status','已记录 40');
  await page.locator('.entry-mode[data-mode=income]').click();assert.equal(await page.locator('#income-amount').inputValue(),'10');assert.equal(await page.locator('#income-note').inputValue(),'入账草稿');assert.equal(await page.locator('#income-date').inputValue(),'2026-10-02');
  await page.selectOption('#income-preset',String(preset));assert.equal(await page.locator('#income-budget-system').inputValue(),String(special));await page.click('#submit-income');await text('#form-status','已记录 10');
  await page.locator('[data-quick-action=wechat-to-octopus]').click();await page.fill('#transfer-date','2026-10-03');await page.fill('#transfer-outflow-amount','100');await page.fill('#transfer-inflow-amount','110');await page.click('#submit-transfer');await text('#form-status','已记录 100');
  const transactions=await get('/api/transactions/search');assert.equal(transactions.pagination.total,4);
  assert.equal(transactions.transactions.filter(t=>t.budget_system_id===special).length,2);
  await page.locator('.entry-mode[data-mode=expense]').click();await page.screenshot({path:'/tmp/finance-budget-entry.png',fullPage:true});
  console.log('PASS original dropdowns, independent drafts, manual/default/rule/preset precedence and expense/income/FX saves');

  await page.goto(base+'/accounts');await page.waitForFunction(()=>document.querySelector('#search-budget-system')?.options.length>2);
  assert(await page.locator('#detail-account option').count()>2);assert(await page.locator('#detail-category option').count()>2);
  await page.locator('.detail-view-mode[data-view=search]').click();await page.selectOption('#cashflow-basis','raw');await page.fill('#search-start-date','2026-10-01');await page.fill('#search-end-date','2026-10-31');await page.selectOption('#search-budget-system',String(daily));await page.locator('#search-type-options input[value=expense]').check();await page.locator('#transaction-search-form button[type=submit]').click();
  await text('#transaction-search-summary-content','40.00');await text('#analysis-content','40.00');
  await page.locator('#search-results-details summary').click();await text('#search-results-body','日常预算');
  await text('#search-results-body','自动');
  const edit=page.locator('#search-results-body button').filter({hasText:'编辑'}).first();await edit.click();await page.waitForSelector('#transaction-editor-dialog[open]');assert.equal(await page.locator('#editor-budget-system').inputValue(),'');await text('#editor-budget-system option:checked','日常预算');await page.selectOption('#editor-budget-system',String(special));await page.locator('#transaction-editor-form button[type=submit]').click();await page.waitForSelector('#transaction-editor-dialog[open]',{state:'hidden'});await text('#search-summary','0');
  await page.screenshot({path:'/tmp/finance-budget-query.png',fullPage:true});
  console.log('PASS page 3 dropdowns, budget search, summary, analysis and edit attribution');

  const members=(await get('/api/transactions/search')).transactions.filter(t=>['expense','income'].includes(t.transaction_type));
  const groupResponse=await api.post('/api/offset-groups',{data:{title:'浏览器跨预算',kind:'expense',transaction_ids:members.map(t=>t.id)}});assert(groupResponse.ok(),await groupResponse.text());
  const main=await get(`/api/transactions/${members.find(t=>t.transaction_type==='expense').id}`);
  const update=await api.put(`/api/transactions/${main.id}`,{data:{...main,budget_system_id:null}});assert(update.ok(),await update.text());
  await page.selectOption('#search-budget-system',String(special));await page.selectOption('#cashflow-basis','offset');await page.locator('#transaction-search-form button[type=submit]').click();await text('#budget-query-warning','浏览器跨预算');await text('#transaction-search-summary-content','仅已归属事项');
  await page.selectOption('#search-budget-system','');await page.locator('#transaction-search-form button[type=submit]').click();await text('#transaction-search-summary-content','55.00');assert(await page.locator('#budget-query-warning').isHidden());
  console.log('PASS cross-budget offset warning and all-budget net counted once');

  if(process.argv.includes('--mobile')){
  await page.setViewportSize({width:390,height:844});await page.goto(base+'/budgets');await text('#budget-status','已加载');
  await page.getByRole('button',{name:'交换行列',exact:true}).click();await page.selectOption('#matrix-level','root');await page.screenshot({path:'/tmp/finance-budget-mobile.png',fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Budget page overflows mobile viewport');
  for(const url of ['/settings','/','/accounts']){await page.goto(base+url);await page.waitForLoadState('networkidle');assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),url+' overflows mobile viewport');}
  await page.setViewportSize({width:1440,height:1050});console.log('PASS mobile budget layout and matrix transpose');

  }

  // A budget request failure must not break the original account/category controls.
  await page.route('**/api/budget/config',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"test unavailable"}'}));
  await page.goto(base+'/');await text('#expense-budget-hint','加载失败');assert(await page.locator('#source-account option').count()>2);assert(!await page.locator('#submit-transaction').isDisabled());
  await page.goto(base+'/accounts');await text('#budget-query-warning','加载失败');assert(await page.locator('#detail-account option').count()>2);assert(await page.locator('#detail-category option').count()>2);
  await page.unroute('**/api/budget/config');
  for(const url of ['/overview','/investments','/offsets']){await page.goto(base+url);await page.waitForLoadState('networkidle');assert.equal(await page.locator('.primary-nav a[href="/budgets"]').count(),1);assert.equal(await page.locator('.primary-nav a[href="/settings"]').count(),1);}
  assert.deepEqual(errors,[]);console.log('PASS remaining pages and budget service failure fallback; no browser errors');
  await browser.close();await api.dispose();
})().catch(error=>{console.error(error);process.exit(1);});

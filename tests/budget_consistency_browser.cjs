// Desktop regression; refuses to run against a real ledger.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async r=>{assert(r.ok(),await r.text());return r.json();};
  const get=async path=>json(await api.get(path));
  assert.deepEqual(await get('/__budget_test_fixture'),{fixture:'budget-browser',temporary:true});
  let config=await get('/api/budget/config');
  config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name:'一致性桌面验证',currency_code:'HKD'}}));
  const sid=config.systems.find(s=>s.name==='一致性桌面验证').id;
  const account=(await get('/api/accounts')).find(a=>a.currency_code==='HKD').id;
  const category=(await get('/api/categories?kind=income'))[0].id;
  const cash=config.storage.find(s=>s.name==='活期').id;
  const income=async amount=>json(await api.post('/api/incomes',{data:{
    transaction_date:'2026-10-05',destination_account_id:account,category_id:category,amount,budget_allocations:[]}}));
  const old=await json(await api.put(`/api/budget/plans/${sid}/2026-09/HKD`,{data:{expected_version:0,total:'500',items:[]}}));
  const source=await income('500');
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const text=(selector,value)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[selector,value]);
  const setSearchBudgets=values=>page.locator('#search-budget-options input').evaluateAll((inputs,selected)=>{
    for(const input of inputs){input.checked=selected.map(String).includes(input.value);input.dispatchEvent(new Event('change',{bubbles:true}));}
  },values);
  try{
    await page.goto(`${base}/budgets?month=2026-10&system=${sid}`);await text('#budget-status','已加载');
    assert.equal(await page.locator('#plan-system').inputValue(),String(sid));
    assert.equal(await page.locator('#plan-month').inputValue(),'2026-10');
    await page.evaluate(id=>IncomeAllocations.open('transaction',id),source.id);
    await page.waitForSelector('#income-allocation-editor[open]');
    await page.locator('#income-allocation-editor .allocation-budget').selectOption(String(sid));
    await page.locator('#income-allocation-editor .allocation-amount').fill('500');
    const review=page.locator(`#income-allocation-editor select[data-plan-id="${old.id}"]`);
    await review.waitFor();assert.equal(await review.inputValue(),'');
    await page.getByRole('button',{name:'保存分配',exact:true}).click();
    assert.equal(await page.locator('#income-allocation-editor').getAttribute('open'),'');
    assert.equal((await get(`/api/budget/plans/${sid}/2026-09/HKD`)).total,'500.00');
    await review.selectOption('replace');
    await page.screenshot({path:'/tmp/finance-consistency-legacy.png',fullPage:true});
    await page.getByRole('button',{name:'保存分配',exact:true}).click();
    await page.waitForSelector('#income-allocation-editor[open]',{state:'hidden'});
    assert.equal((await get(`/api/budget/plans/${sid}/2026-09/HKD`)).total,'0.00');

    // Add a second source split between source month and the following month.
    const second=await income('1000');
    let detail=await get(`/api/budget/income-allocations/transaction/${second.id}`);
    await json(await api.put(`/api/budget/income-allocations/transaction/${second.id}`,{data:{
      expected_token:detail.token,items:[{budget_system_id:sid,month:'2026-10',amount:'400'},{budget_system_id:sid,month:'2026-11',amount:'600'}]}}));
    await page.goto(base+'/accounts');await page.waitForLoadState('networkidle');
    await page.locator('.detail-view-mode[data-view=search]').click();
    await setSearchBudgets([sid]);
    await page.fill('#search-start-date','2026-10-01');await page.fill('#search-end-date','2026-10-31');
    await page.locator('#search-type-options input[value=income]').check();
    await page.locator('#transaction-search-form button[type=submit]').click();
    await text('#transaction-search-summary-content','预算生效月份');
    await page.locator('.allocation-trace summary').click();
    await text('.allocation-trace','600.00 HKD');
    const target=page.locator(`.allocation-trace a[href="/budgets?month=2026-11&system=${sid}"]`);
    await target.click();await text('#budget-status','已加载');
    assert.equal(await page.locator('#plan-month').inputValue(),'2026-11');
    await text('#month-plans','600.00');

    // Source correction must also be visible in the third-page budget report.
    await json(await api.put(`/api/transactions/${second.id}`,{data:{
      transaction_date:'2026-10-05',transaction_type:'income',destination_account_id:account,
      inflow_amount:'500',category_id:category}}));
    await page.goto(base+'/accounts');await page.waitForLoadState('networkidle');
    await page.locator('.detail-view-mode[data-view=search]').click();
    await setSearchBudgets([sid]);
    await page.fill('#search-start-date','2026-10-01');await page.fill('#search-end-date','2026-10-31');
    await page.locator('#search-type-options input[value=income]').check();
    await page.selectOption('#cashflow-basis','offset');
    await page.locator('#transaction-search-form button[type=submit]').click();
    await text('#transaction-search-summary-content','预算统计未完整');
    await text('#transaction-search-summary-content','收入来源超额分配');
    await page.screenshot({path:'/tmp/finance-consistency-query.png',fullPage:true});
    // All-budget actual income still includes the corrected source; it is not
    // an allocation report and must not claim the source was removed from its chart.
    await setSearchBudgets([]);
    await page.locator('#transaction-search-form button[type=submit]').click();
    await text('#analysis-status','已按 HKD 统计');
    assert.equal((await page.locator('#analysis-content').textContent()).includes('未计入本图'),false);
    // Remove the invalid source allocation before the storage check.
    detail=await get(`/api/budget/income-allocations/transaction/${second.id}`);
    await json(await api.put(`/api/budget/income-allocations/transaction/${second.id}`,{data:{expected_token:detail.token,items:[]}}));
    const plan=await get(`/api/budget/plans/${sid}/2026-10/HKD`);
    await json(await api.put(`/api/budget/plans/${sid}/2026-10/HKD`,{data:{
      expected_version:plan.version,total:'500',items:[{storage_method_id:cash,amount:'500'}]}}));
    const expenseCategory=(await get('/api/categories?kind=expense'))[0].id;
    await json(await api.post('/api/expenses',{data:{transaction_date:'2026-10-06',source_account_id:account,
      category_id:expenseCategory,amount:'450',budget_system_id:sid}}));
    await page.goto(`${base}/budgets?month=2026-10&system=${sid}`);await text('#budget-status','已加载');
    await page.evaluate(id=>BudgetStorage.open(id,'2026-10',async()=>{}),sid);
    await page.locator('[data-storage-tab=transfers]').click();
    await page.selectOption('#storage-transfer-from',String(cash));await page.selectOption('#storage-transfer-to','');
    await page.fill('#storage-transfer-amount','50');await page.click('#save-storage-transfer');
    await text('.storage-manage-content','活期 → 待分配');
    const storage=(await get(`/api/budget/storage/${sid}/2026-10`)).row;
    assert.equal(storage.cells.find(c=>c.key===String(cash)).balance,'0.00');
    assert.equal(storage.cells.find(c=>c.key==='unallocated').balance,'50.00');
    await page.screenshot({path:'/tmp/finance-consistency-storage.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('PASS desktop historical review, source/effective-month trace, invalid-source warning, and month-end storage transfer');
  }finally{await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exitCode=1;});

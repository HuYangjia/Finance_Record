// Desktop integration on the isolated fixture; never writes to the real ledger.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async r=>{assert(r.ok(),await r.text());return r.json();};
  const get=async path=>json(await api.get(path));
  assert.deepEqual(await get('/__budget_test_fixture'),{fixture:'budget-browser',temporary:true});
  let config=await get('/api/budget/config');
  const daily=config.defaults.expense;
  config=await json(await api.put(`/api/budget/config/systems/${daily}`,{data:{expected_version:config.version,name:'日常预算',currency_code:'HKD'}}));
  config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name:'事项预算验证专项',currency_code:'HKD'}}));
  const other=config.systems.find(s=>s.name==='事项预算验证专项').id;
  const account=(await get('/api/accounts')).find(a=>a.code==='ZABANK-HKD').id;
  const cats={};for(const kind of ['income','expense'])cats[kind]=(await get('/api/categories?kind='+kind))[0].id;
  const transaction=async(kind,amount)=>json(await api.post(kind==='income'?'/api/incomes':'/api/expenses',{data:{
    transaction_date:'2026-10-05',amount,category_id:cats[kind],
    [kind==='income'?'destination_account_id':'source_account_id']:account,...(kind==='income'?{budget_allocations:[]}:{})}}));
  const group=async(title,income,expense)=>{
    const a=await transaction('income',income),b=await transaction('expense',expense);
    return json(await api.post('/api/offset-groups',{data:{title,kind:'income',transaction_ids:[a.id,b.id]}}));
  };
  const positive=await group('桌面净收入分配','1000','200');
  const negative=await group('桌面净亏损承担','200','500');
  const originalBalance=(await get('/api/accounts')).find(a=>a.id===account).balance;
  const atomic=rows=>rows.map(({income_allocations,...original})=>original);
  const originalTransactions=atomic((await get('/api/transactions/search')).transactions);
  const browser=await chromium.launch({headless:true,...(process.env.FINANCE_TEST_BROWSER?{executablePath:process.env.FINANCE_TEST_BROWSER}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const text=(selector,value)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[selector,value]);
  const save=()=>page.locator('#income-allocation-editor button[type=submit]').click();
  try{
    await page.goto(`${base}/offsets?group=${positive.id}`);await text('#editor-title',`#${positive.id}`);
    await text('#offset-budget-status','尚未分给预算');
    await page.click('#allocate-group-income');await page.waitForSelector('#income-allocation-editor[open]');
    const rows=page.locator('#income-allocation-editor .income-allocation-row');
    await rows.nth(0).locator('.allocation-budget').selectOption(String(daily));
    await rows.nth(0).locator('.allocation-amount').fill('300');
    await page.locator('#income-allocation-editor').getByRole('button',{name:'＋添加预算'}).click();
    await rows.nth(1).locator('.allocation-budget').selectOption(String(other));
    await rows.nth(1).locator('.allocation-amount').fill('500');
    await save();await page.waitForSelector('#income-allocation-editor[open]',{state:'hidden'});
    await text('#offset-budget-status','事项预算验证专项');
    assert.equal((await get(`/api/offset-groups/${positive.id}`)).income_allocations.length,2);
    await page.fill('#offset-note','未保存草稿');assert(await page.locator('#allocate-group-income').isDisabled());
    await page.click('#cancel-group');

    await page.goto(`${base}/offsets?group=${negative.id}`);await text('#offset-budget-status','净亏损');
    assert(await page.locator('#allocate-group-income').isDisabled());
    await page.selectOption('#offset-budget-system',String(other));
    await page.route(`**/api/offset-groups/${negative.id}`,route=>route.request().method()==='PUT'
      ?route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'模拟冲突保留草稿'})}):route.continue());
    await page.click('#save-group');await text('#editor-status','模拟冲突保留草稿');
    assert.equal(await page.locator('#offset-budget-system').inputValue(),String(other));
    await page.unroute(`**/api/offset-groups/${negative.id}`);
    await page.click('#save-group');await text('#editor-status','事项已保存');
    await text('#offset-budget-status','事项指定');
    assert.equal((await get(`/api/offset-groups/${negative.id}`)).budget_explicit_system_id,other);
    const execution=await get(`/api/budget/execution?end_month=2026-10&system_id=${other}`);
    assert.equal(execution.rows[0].expense,'300.00');assert.equal(execution.rows[0].closing,'200.00');
    await page.locator('#group-list .offset-entry').filter({hasText:'桌面净亏损承担'}).locator('summary').click();
    await text('#group-list','先冲抵事项收入');
    assert.equal((await get('/api/accounts')).find(a=>a.id===account).balance,originalBalance);
    assert.deepEqual(atomic((await get('/api/transactions/search')).transactions),originalTransactions);
    await page.screenshot({path:'/tmp/finance-offset-budgets-desktop.png',fullPage:true});
    // A real source correction crosses zero. Old allocations remain editable for repair.
    const cost=positive.members.find(m=>m.transaction_type==='expense');
    await json(await api.put(`/api/transactions/${cost.id}`,{data:{transaction_date:'2026-10-05',
      transaction_type:'expense',source_account_id:account,outflow_amount:'1200',category_id:cats.expense}}));
    await page.goto(`${base}/offsets?group=${positive.id}`);await text('#offset-budget-status','原有收入分配待核对');
    await page.click('#preview-group');await text('#offset-budget-status','原有收入分配待核对');
    assert(await page.locator('#allocate-group-income').isEnabled());
    await page.click('#allocate-group-income');await page.waitForSelector('#income-allocation-editor[open]');
    while(await rows.count())await rows.first().getByRole('button',{name:'移除',exact:true}).click();
    await save();await page.waitForSelector('#income-allocation-editor[open]',{state:'hidden'});
    await text('#offset-budget-status','净亏损');assert(await page.locator('#allocate-group-income').isDisabled());
    const repaired=await get(`/api/budget/execution?end_month=2026-10&system_id=${daily}`);
    assert.equal(repaired.rows[0].closing,'-200.00');assert(repaired.rows[0].complete);
    assert.deepEqual(errors,[]);
    console.log('PASS desktop event budget: split net income, loss responsibility, draft protection, storage/execution, unchanged ledger');
  }finally{await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exitCode=1;});

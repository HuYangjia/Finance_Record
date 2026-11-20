// Desktop-only: response loss, reload recovery, rejected writes, and independent drafts.
const {chromium, request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async response=>{assert(response.ok(),await response.text());return response.json();};
  const get=async path=>json(await api.get(path));
  assert.deepEqual(await get('/__budget_test_fixture'),{fixture:'budget-browser',temporary:true});
  let config=await get('/api/budget/config');
  const daily=config.daily_expense;
  config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name:'重试留存',currency_code:'HKD'}}));
  const reserve=config.systems.find(s=>s.name==='重试留存').id;
  const accounts=await get('/api/accounts'),hkd=accounts.find(a=>a.currency_code==='HKD').id,cny=accounts.find(a=>a.currency_code==='CNY').id;
  const expense=(await get('/api/categories?kind=expense'))[0].id,income=(await get('/api/categories?kind=income'))[0].id;
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const text=(selector,value)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[selector,value]);
  const pick=(id,value)=>page.locator('#'+id).evaluate((el,v)=>{
    el.value=String(v);el.dispatchEvent(new Event('change',{bubbles:true}));window.refreshFrequentPickers?.();
  },value);
  const rows=async note=>(await get('/api/transactions/search?keyword='+encodeURIComponent(note))).transactions;
  const ready=()=>page.waitForFunction(()=>document.querySelector('#income-budget-system').options.length>1 && document.querySelector('#quick-action-list button'));
  async function fillExpense(note){
    await page.click('.entry-mode[data-mode=expense]');await pick('source-account',hkd);await pick('expense-category',expense);
    await page.fill('#transaction-date','2026-11-20');await page.fill('#expense-amount','12.34');await page.fill('#note',note);
  }
  async function loseResponse(endpoint,forward=true){
    await page.route('**'+endpoint,async route=>{
      if(forward){const response=await route.fetch();assert.equal(response.status(),201);}
      await route.abort('failed');
    },{times:1});
  }
  try{
    await page.goto(base+'/');await ready();
    await fillExpense('丢失响应支出');await loseResponse('/api/expenses');
    await page.click('#submit-transaction');await text('#form-status','尚未确认');
    assert.equal((await rows('丢失响应支出')).length,1);
    const firstKey=await page.evaluate(()=>EntryRequests.get('expense').payload.request_id);
    assert(await page.locator('#transaction-form').evaluate(el=>el.inert));
    await page.reload();await ready();await page.waitForSelector('#entry-recovery:not([hidden])');
    assert.equal(await page.locator('#expense-amount').inputValue(),'12.34');
    assert.equal(await page.locator('#note').inputValue(),'丢失响应支出');
    assert.equal(await page.evaluate(()=>EntryRequests.get('expense').payload.request_id),firstKey);
    await page.screenshot({path:'/tmp/finance-entry-recovery.png',fullPage:true});
    await page.getByRole('button',{name:'重试并确认'}).click();await text('#form-status','未重复记账');
    assert.equal((await rows('丢失响应支出')).length,1);
    assert.equal(await page.locator('#expense-amount').inputValue(),'');
    // A genuinely new identical expense receives a fresh request ID.
    await fillExpense('丢失响应支出');await page.click('#submit-transaction');await text('#form-status','已记录');
    assert.equal((await rows('丢失响应支出')).length,2);

    await fillExpense('未送达支出');await loseResponse('/api/expenses',false);
    await page.click('#submit-transaction');await text('#form-status','尚未确认');
    assert.equal((await rows('未送达支出')).length,0);
    // Another mode remains usable while expense confirmation is pending.
    await page.click('.entry-mode[data-mode=income]');await pick('income-destination-account',hkd);await pick('income-category',income);
    await page.fill('#income-date','2026-11-20');await page.fill('#income-amount','10');await page.fill('#income-note','独立模式收入');
    await page.click('#submit-income');await text('#form-status','已记录');
    assert.equal((await rows('独立模式收入')).length,1);
    await page.click('.entry-mode[data-mode=expense]');await page.getByRole('button',{name:'重试并确认'}).click();
    await text('#form-status','已记录');assert.equal((await rows('未送达支出')).length,1);

    await page.click('.entry-mode[data-mode=income]');await pick('income-destination-account',hkd);await pick('income-category',income);
    await page.fill('#income-date','2026-11-20');await page.fill('#income-amount','100');await page.fill('#income-note','拆分收入重试');
    await page.getByLabel('按金额拆分到多个预算').check();
    const parts=page.locator('.income-entry-allocation .income-allocation-row');
    await parts.nth(0).locator('select').selectOption(String(daily));await parts.nth(0).locator('[type=number]').fill('40');
    await page.locator('.income-entry-allocation').getByRole('button',{name:'＋添加预算'}).click();
    await parts.nth(1).locator('select').selectOption(String(reserve));await parts.nth(1).getByLabel('全部剩余',{exact:true}).check();
    await loseResponse('/api/incomes');await page.click('#submit-income');await text('#form-status','尚未确认');
    await page.reload();await ready();await page.waitForSelector('#income-form:not([hidden])');
    assert.equal(await parts.count(),2);
    assert.equal(await parts.nth(0).locator('[type=number]').inputValue(),'40');
    assert(await parts.nth(1).getByLabel('全部剩余',{exact:true}).isChecked());
    await page.getByRole('button',{name:'重试并确认'}).click();await text('#form-status','未重复记账');
    const allocated=await rows('拆分收入重试');assert.equal(allocated.length,1);
    const source=await get('/api/budget/income-allocations/transaction/'+allocated[0].id);
    assert.deepEqual(source.items.map(i=>i.amount).sort(),['40.00','60.00']);

    // Explicit rejection clears pending state and allows correcting the original draft.
    await page.fill('#income-amount','100');await page.fill('#income-note','拒绝后修正');
    await page.getByLabel('按金额拆分到多个预算').check();
    await parts.nth(0).locator('select').selectOption(String(daily));await parts.nth(0).locator('[type=number]').fill('150');
    await page.click('#submit-income');await page.waitForFunction(()=>document.querySelector('#form-status').className==='error');
    assert.equal(await page.evaluate(()=>EntryRequests.get('income')),undefined);
    assert.equal(await page.locator('#income-form').evaluate(el=>el.inert),false);
    assert.equal((await rows('拒绝后修正')).length,0);
    await parts.nth(0).locator('[type=number]').fill('100');await page.click('#submit-income');await text('#form-status','已记录');
    assert.equal((await rows('拒绝后修正')).length,1);

    await page.click('.entry-mode[data-mode=transfer]');await pick('transfer-source-account',hkd);await pick('transfer-destination-account',cny);
    await page.fill('#transfer-date','2026-11-20');await page.fill('#transfer-outflow-amount','20');await page.fill('#transfer-inflow-amount','18');
    await page.fill('#transfer-note','换汇重试');await loseResponse('/api/transfers');
    await page.click('#submit-transfer');await text('#form-status','尚未确认');
    await page.reload();await ready();await page.waitForSelector('#transfer-form:not([hidden])');
    assert.equal(await page.locator('#transfer-inflow-amount').inputValue(),'18');
    await page.getByRole('button',{name:'重试并确认'}).click();await text('#form-status','未重复记账');
    assert.equal((await rows('换汇重试')).length,1);
    assert.deepEqual(errors,[]);
    console.log('Entry retry browser regression passed: lost/not-sent responses, reload, independent modes, split income, validation, FX.');
  }finally{await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exitCode=1;});

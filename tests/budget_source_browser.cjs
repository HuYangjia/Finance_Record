// Desktop regression. The fixture marker prevents writes to a real ledger.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const {join}=require('node:path');
const {tmpdir}=require('node:os');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async response=>{assert(response.ok(),await response.text());return response.json();};
  const get=async path=>json(await api.get(path));
  assert.deepEqual(await get('/__budget_test_fixture'),{fixture:'budget-browser',temporary:true});
  let config=await get('/api/budget/config');
  config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name:'来源返回验证',currency_code:'HKD'}}));
  const sid=config.systems.find(s=>s.name==='来源返回验证').id;
  const account=(await get('/api/accounts')).find(a=>a.code==='ZABANK-HKD');
  const salary=(await get('/api/categories?kind=income'))[0].id;
  const expenseCategory=(await get('/api/categories?kind=expense'))[0].id;
  const income=async(amount,counterparty)=>json(await api.post('/api/incomes',{data:{transaction_date:'2026-10-10',
    destination_account_id:account.id,category_id:salary,amount,counterparty,budget_allocations:[]}}));
  const assign=async(kind,id,amount)=>{
    const detail=await get(`/api/budget/income-allocations/${kind}/${id}`);
    return json(await api.put(`/api/budget/income-allocations/${kind}/${id}`,{data:{expected_token:detail.token,
      items:[{budget_system_id:sid,month:'2026-11',amount}]}}));
  };
  const ordinary=await income('1000','原始来源');await assign('transaction',ordinary.id,'600');
  const gross=await income('900','事项工资');
  const cost=await json(await api.post('/api/expenses',{data:{transaction_date:'2026-10-11',source_account_id:account.id,
    category_id:expenseCategory,amount:'100',budget_system_id:sid}}));
  const offset=await json(await api.post('/api/offset-groups',{data:{title:'工资事项',kind:'income',transaction_ids:[gross.id,cost.id]}}));
  await assign('offset_group',offset.id,'500');
  const originalBalance=Number((await get('/api/accounts')).find(a=>a.id===account.id).balance);
  const browser=await chromium.launch({headless:true,...(process.env.FINANCE_TEST_BROWSER?{executablePath:process.env.FINANCE_TEST_BROWSER}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let acceptDiscard=true;page.on('dialog',d=>acceptDiscard?d.accept():d.dismiss());
  const text=(selector,value)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[selector,value]);
  const source=(kind,id,host='#month-plans')=>page.locator(`${host} [data-source-key="${kind}:${id}"]`);
  const save=host=>page.locator(`${host} button[type=submit]`).click();
  const close=host=>page.locator(`${host} .plan-editor-heading button`).click();
  const closed=host=>page.waitForSelector(`${host}[open]`,{state:'hidden'});
  try{
    await page.goto(`${base}/budgets?month=2026-11&system=${sid}`);await text('#budget-status','已加载');
    await page.selectOption('#plan-currency','HKD');await page.fill('#plan-start','2026-10');
    await page.locator('#budget-load button[type=submit]').click();await text('#budget-status','已加载');
    await source('transaction',ordinary.id).scrollIntoViewIfNeeded();
    const originalScroll=await page.evaluate(()=>window.scrollY),url=page.url();
    await source('transaction',ordinary.id).click();await page.waitForSelector('#budget-source-editor[open]');
    assert.equal(page.context().pages().length,1);assert.equal(page.url(),url);
    await page.fill('#source-counterparty','修正后的来源');await page.fill('#source-note','来源弹窗更正');
    await page.screenshot({path:join(tmpdir(),'finance-budget-source-editor.png'),fullPage:true});
    await save('#budget-source-editor');await closed('#budget-source-editor');
    await text('#month-plans','修正后的来源');
    assert.equal(await page.locator('#plan-month').inputValue(),'2026-11');
    assert.equal(await page.locator('#plan-system').inputValue(),String(sid));
    assert.equal(await page.locator('#plan-currency').inputValue(),'HKD');
    assert.equal(await page.locator('#plan-start').inputValue(),'2026-10');
    await page.waitForFunction(y=>Math.abs(window.scrollY-y)<3,originalScroll);
    assert.equal(await source('transaction',ordinary.id).evaluate(el=>el===document.activeElement),true);
    assert.equal((await get(`/api/transactions/${ordinary.id}`)).note,'来源弹窗更正');
    assert.equal(Number((await get('/api/accounts')).find(a=>a.id===account.id).balance),originalBalance);

    // Cancel keeps the record; rejecting discard keeps the draft visible.
    await source('transaction',ordinary.id).click();await page.fill('#source-counterparty','取消草稿');
    acceptDiscard=false;await page.keyboard.press('Escape');assert(await page.locator('#budget-source-editor').isVisible());
    assert.equal(await page.locator('#source-counterparty').inputValue(),'取消草稿');
    acceptDiscard=true;await close('#budget-source-editor');await closed('#budget-source-editor');
    assert.equal((await get(`/api/transactions/${ordinary.id}`)).counterparty,'修正后的来源');

    // A failed write retains the editor and draft. Real corrections keep the existing over-allocation warning.
    await source('transaction',ordinary.id).click();await page.fill('#source-note','失败保留草稿');
    await page.route(`**/api/transactions/${ordinary.id}`,route=>route.request().method()==='PUT'
      ?route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'模拟版本冲突'})}):route.continue());
    await save('#budget-source-editor');await text('#budget-source-editor','模拟版本冲突');
    assert.equal(await page.locator('#source-note').inputValue(),'失败保留草稿');
    await page.unroute(`**/api/transactions/${ordinary.id}`);
    await page.fill('#source-amount','500');await save('#budget-source-editor');await closed('#budget-source-editor');
    await text('#budget-execution','收入来源超额分配');
    assert.equal(Number((await get('/api/accounts')).find(a=>a.id===account.id).balance),originalBalance-500);
    await source('transaction',ordinary.id).click();await page.fill('#source-amount','1000');
    await save('#budget-source-editor');await closed('#budget-source-editor');

    // A saved write followed by refresh failure is not presented as an unsaved write.
    await source('transaction',ordinary.id).click();await page.fill('#source-note','刷新失败但已保存');
    await page.route('**/api/budget/overview?**',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'模拟刷新失败'})}));
    await save('#budget-source-editor');await closed('#budget-source-editor');
    await text('#budget-status','已保存，但预算刷新失败');
    assert.equal((await get(`/api/transactions/${ordinary.id}`)).note,'刷新失败但已保存');
    await page.unroute('**/api/budget/overview?**');

    // Opening from the monthly editor restores it and preserves unsaved storage inputs.
    await page.locator('#month-plans button').filter({hasText:'安排存储 / 查看来源'}).click();
    await page.waitForSelector('#plan-editor[open]');
    await page.locator('.currency-note').fill('尚未保存的存储草稿');
    await source('transaction',ordinary.id,'#plan-editor').click();await page.fill('#source-note','预案内修改');
    await save('#budget-source-editor');await closed('#budget-source-editor');
    assert.equal(await page.locator('.currency-note').inputValue(),'尚未保存的存储草稿');
    assert(await page.locator('#plan-editor').isVisible());
    await page.click('#close-plan');await closed('#plan-editor');
    await page.locator('#month-plans button').filter({hasText:'安排存储 / 查看来源'}).click();
    await source('transaction',ordinary.id,'#plan-editor').click();await page.fill('#source-counterparty','预案内更新来源');
    await save('#budget-source-editor');await closed('#budget-source-editor');
    await text('#plan-editor','预案内更新来源');await page.click('#close-plan');await closed('#plan-editor');

    // Existing allocation editing stays on the budget page and refreshes its total.
    await source('transaction',ordinary.id).click();
    await page.locator('#budget-source-editor').getByRole('button',{name:'查看 / 修改收入预算分配'}).click();
    await page.waitForSelector('#income-allocation-editor[open]');
    await page.locator('#income-allocation-editor .allocation-amount').fill('650');
    await page.locator('#income-allocation-editor').getByRole('button',{name:'保存分配',exact:true}).click();
    await closed('#income-allocation-editor');await text('#month-plans','1150.00');
    await close('#budget-source-editor');await closed('#budget-source-editor');

    // Income events and their actual members use nested dialogs, retaining group drafts and current versions.
    await source('offset_group',offset.id).click();await page.waitForSelector('#budget-source-group[open]');
    await page.fill('#source-group-title','事项新名称');await page.fill('#source-group-note','事项草稿保留');
    await page.locator(`#budget-source-group tr[data-transaction-id="${cost.id}"] button`).click();
    await page.waitForSelector('#budget-source-editor[open]');await page.fill('#source-amount','150');
    await save('#budget-source-editor');await closed('#budget-source-editor');
    assert.equal(await page.locator('#source-group-title').inputValue(),'事项新名称');
    await text('#budget-source-group','净额 750.00 HKD');
    await save('#budget-source-group');await closed('#budget-source-group');
    await text('#month-plans','事项新名称');
    assert.equal((await get(`/api/offset-groups/${offset.id}`)).note,'事项草稿保留');
    assert.equal(Number((await get('/api/accounts')).find(a=>a.id===account.id).balance),originalBalance-50);
    assert.equal(page.context().pages().length,1);assert.equal(page.url(),url);
    await page.screenshot({path:join(tmpdir(),'finance-budget-source-return.png'),fullPage:true});

    // The original third-page editor remains usable.
    await page.goto(`${base}/accounts?transaction=${ordinary.id}`);await page.waitForSelector('#transaction-editor-dialog[open]');
    await page.fill('#editor-note','第三页仍可更正');
    await page.locator('#transaction-editor-form button[type=submit]').click();
    await closed('#transaction-editor-dialog');assert.equal((await get(`/api/transactions/${ordinary.id}`)).note,'第三页仍可更正');
    assert.deepEqual(errors,[]);
    console.log('PASS budget-local source editing, saved refresh and return context, cancel/error/draft protection, allocations, income-event members, balances and original ledger editor');
  }catch(error){await page.screenshot({path:join(tmpdir(),'finance-budget-source-failure.png'),fullPage:true});throw error;}
  finally{await browser.close();await api.dispose();}
})().catch(error=>{console.error(error);process.exitCode=1;});

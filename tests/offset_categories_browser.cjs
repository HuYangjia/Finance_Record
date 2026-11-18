// Desktop only. Refuse to write unless the server declares an isolated ledger.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async r=>{assert(r.ok(),await r.text());return r.json();};
  const get=async path=>json(await api.get(path));
  assert.deepEqual(await get('/__budget_test_fixture'),{fixture:'budget-browser',temporary:true});
  const cats=await get('/api/categories?kind=expense'),inc=(await get('/api/categories?kind=income'))[0].id;
  const account=(await get('/api/accounts')).find(a=>a.code==='ZABANK-HKD').id;
  let config=await get('/api/budget/config');
  const daily=config.defaults.expense;
  config=await json(await api.put(`/api/budget/config/systems/${daily}`,{data:{expected_version:config.version,name:'日常预算',currency_code:'HKD'}}));
  const tx=async(kind,amount,cat)=>json(await api.post(kind==='expense'?'/api/expenses':'/api/incomes',{data:{
    transaction_date:'2026-10-05',amount,category_id:cat,
    [kind==='expense'?'source_account_id':'destination_account_id']:account}}));
  const a=await tx('expense','100',cats[0].id),b=await tx('expense','50',cats[1].id),r=await tx('income','30',inc);
  const group=await json(await api.post('/api/offset-groups',{data:{title:'桌面混合优惠验证',kind:'expense',
    transaction_ids:[a.id,b.id,r.id],category_allocation_mode:'anchor'}}));
  const balance=(await get('/api/accounts')).find(a=>a.id===account).balance;
  const execution=(await get(`/api/budget/execution?end_month=2026-10&system_id=${daily}`)).rows[0];
  const browser=await chromium.launch({headless:true,...(process.env.FINANCE_TEST_BROWSER?{executablePath:process.env.FINANCE_TEST_BROWSER}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const text=(selector,value)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[selector,value]);
  const preview=page.locator('#preview-content');
  try{
    await page.goto(`${base}/offsets?group=${group.id}`);await text('#offset-category-hint','旧方式');
    assert.equal(await page.locator('#offset-category-mode').inputValue(),'anchor');
    await page.selectOption('#offset-category-mode','proportional');
    const target=page.locator(`[data-category-refund="${r.id}"]`);assert(await target.isVisible());
    await page.click('#preview-group');await text('#preview-content','多分类');
    await preview.getByText('查看分类净额与优惠分摊',{exact:true}).click();
    await text('#preview-content','80.00');await text('#preview-content','40.00');
    await target.selectOption(String(b.id));
    await page.click('#preview-group');await preview.getByText('查看分类净额与优惠分摊',{exact:true}).click();
    await text('#preview-content','20.00');
    await page.route(`**/api/offset-groups/${group.id}`,route=>route.request().method()==='PUT'
      ?route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'模拟保存冲突'})}):route.continue());
    await page.click('#save-group');await text('#editor-status','模拟保存冲突');
    assert.equal(await target.inputValue(),String(b.id));
    await page.unroute(`**/api/offset-groups/${group.id}`);
    await page.click('#save-group');await text('#editor-status','事项已保存');
    const saved=await get(`/api/offset-groups/${group.id}`);
    assert.equal(saved.category_targets[0].expense_transaction_id,b.id);
    assert.deepEqual(saved.category_breakdown.HKD.map(r=>r.amount),['100.00','20.00']);
    assert.equal((await get('/api/accounts')).find(a=>a.id===account).balance,balance);
    assert.deepEqual((await get(`/api/budget/execution?end_month=2026-10&system_id=${daily}`)).rows[0],execution);
    // Removing a targeted member explicitly clears its links in the editable draft.
    await page.locator('#primary-members .offset-member-row').filter({hasText:`#${b.id} ·`}).getByRole('button',{name:'移除关联'}).click();
    await text('#editor-status','解除相关指定冲减');assert.equal(await target.inputValue(),'');
    await page.click('#cancel-group');

    await page.goto(base+'/accounts');await page.waitForFunction(()=>document.querySelectorAll('#search-category-options input').length>1);
    await page.locator('.detail-view-mode[data-view=search]').click();await page.selectOption('#cashflow-basis','offset');
    await page.fill('#search-start-date','2026-10-01');await page.fill('#search-end-date','2026-10-31');
    await page.locator('#search-type-options input[value=expense]').check();
    await page.locator('#search-category-select > summary').click();
    await page.locator(`#search-category-options input[value="${cats[1].id}"]`).check();
    await page.locator('#transaction-search-form button[type=submit]').click();
    await text('#transaction-search-summary-content','20.00');await text('#analysis-content','20.00');
    await page.locator('#search-results-details > summary').click();
    await text('#search-results-body','所选分类份额');await text('#search-results-body','完整事项净额：120.00');
    assert.equal(await page.locator('#search-results-body .offset-entry').count(),1);
    await page.locator('#search-results-body').getByText('查看分类净额与优惠分摊',{exact:true}).click();
    await text('#search-results-body','（本次计入）');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:'/tmp/finance-category-query-desktop.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('PASS desktop mixed-category mode, targeted refund, preview, conflict draft, member removal, query shares and unchanged budget/balance');
  }finally{await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exitCode=1;});

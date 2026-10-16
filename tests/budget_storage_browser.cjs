// Run only against the disposable budget_browser_server.py fixture.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async r=>{assert(r.ok(),await r.text());return r.json();};
  const get=async p=>json(await api.get(p));
  let fixture;for(let i=0;i<30;i++){try{fixture=await get('/__budget_test_fixture');break;}catch(e){if(i===29)throw e;await new Promise(resolve=>setTimeout(resolve,300));}}
  assert.deepEqual(fixture,{fixture:'budget-browser',temporary:true});
  let cfg=await get('/api/budget/config');
  cfg=await json(await api.post('/api/budget/config/systems',{data:{expected_version:cfg.version,name:'存储验收',currency_code:'HKD'}}));
  const sid=cfg.systems.find(s=>s.name==='存储验收').id,cash=cfg.storage.find(s=>s.name==='活期').id,etf=cfg.storage.find(s=>s.name==='ETF').id;
  const account=(await get('/api/accounts')).find(a=>a.currency_code==='HKD').id,category=(await get('/api/categories?kind=expense'))[0].id;
  await json(await api.put(`/api/budget/monthly/${sid}/2026-10`,{data:{plans:[{currency_code:'HKD',expected_version:0,total:'1000',items:[{storage_method_id:cash,amount:'300'},{storage_method_id:etf,amount:'700'}]}]}}));
  await json(await api.post('/api/expenses',{data:{transaction_date:'2026-10-03',amount:'350',category_id:category,source_account_id:account,budget_system_id:sid}}));
  const before=await get('/api/accounts'),browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];page.setDefaultTimeout(15000);
    page.on('pageerror',e=>errors.push(e.message));let discard=true;page.on('dialog',d=>discard?d.accept():d.dismiss());
    const text=(s,v)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[s,v]);
    const idle=()=>page.waitForFunction(()=>!document.querySelector('main').inert&&!document.querySelector('#storage-reload').disabled);
    const report=()=>get(`/api/budget/storage/${sid}/2026-10`);
    const row=()=>page.locator(`#budget-matrix tr[data-system-id="${sid}"]`);
    const save=async button=>{const response=page.waitForResponse(r=>r.request().method()==='PUT'||r.request().method()==='POST'||r.request().method()==='DELETE');await page.click(button);await response;await idle();};
    await page.goto(base+'/budgets');await text('#budget-status','已加载');await page.fill('#plan-month','2026-10');await text('#month-plans-title','2026-10');await idle();
    assert.equal(await page.locator('#matrix-scope').inputValue(),'remaining');assert.equal((await row().locator('[data-storage-total]').textContent()).trim(),'650.00');
    assert.equal((await row().locator(`[data-storage-key="${cash}"]`).textContent()).trim(),'0.00');assert.equal((await row().locator('[data-storage-key="pending"]').textContent()).trim(),'-50.00');await text('#budget-matrix','仍有 50.00 HKD 消费待归属');
    await row().getByRole('button',{name:'管理',exact:true}).click();await page.waitForSelector('#storage-manage-dialog[open]');
    await page.selectOption('#default-storage',String(cash));await save('#save-default-storage');
    assert.equal(await row().locator(`[data-storage-key="${cash}"]`).textContent(),'-50.00');await text('#budget-matrix','有存储额度不足');
    await page.click('[data-storage-tab=transfers]');await page.selectOption('#storage-transfer-from',String(etf));await page.selectOption('#storage-transfer-to',String(cash));
    await page.fill('#storage-transfer-amount','800');await save('#save-storage-transfer');await text('#storage-manage-error','仅剩 700');assert.equal(await page.locator('#storage-transfer-amount').inputValue(),'800');
    discard=false;await page.keyboard.press('Escape');assert(await page.locator('#storage-manage-dialog').evaluate(d=>d.open));
    await page.focus('#storage-transfer-month');await page.keyboard.press('Escape');assert(await page.locator('#storage-manage-dialog').evaluate(d=>d.open));
    assert.equal(await page.locator('#storage-transfer-amount').inputValue(),'800');discard=true;
    await page.fill('#storage-transfer-amount','100');await save('#save-storage-transfer');assert.equal((await report()).row.transfers[0].amount,'100.00');
    assert.equal(await row().locator(`[data-storage-key="${cash}"]`).textContent(),'50.00');assert.equal(await row().locator(`[data-storage-key="${etf}"]`).textContent(),'600.00');
    await page.locator('[data-transfer-id]').getByRole('button',{name:'修改',exact:true}).click();await page.fill('#storage-transfer-amount','150');await save('#save-storage-transfer');
    assert.equal((await report()).row.transfers[0].amount,'150.00');
    await page.click('[data-storage-tab=expenses]');await page.locator('[data-entry-key]').getByRole('button',{name:'改归属'}).click();await page.selectOption('#expense-storage-target',String(etf));await save('#save-expense-storage');
    assert.equal(await row().locator(`[data-storage-key="${cash}"]`).textContent(),'450.00');assert.equal(await row().locator(`[data-storage-key="${etf}"]`).textContent(),'200.00');
    await page.locator('[data-entry-key]').getByRole('button',{name:'改归属'}).click();await page.selectOption('#expense-storage-target','');await save('#save-expense-storage');
    await page.click('[data-storage-tab=transfers]');await page.screenshot({path:'/tmp/finance-storage-manager.png'});
    await page.keyboard.press('Escape');await page.selectOption('#matrix-level','root');await text('#budget-matrix','650.00');await page.click('#matrix-transpose');await text('#budget-matrix','100.00');
    await page.click('#matrix-transpose');await page.fill('#plan-month','2026-11');await text('#month-plans-title','2026-11');await idle();await page.fill('#plan-start','2026-11');await page.locator('#budget-load button[type=submit]').click();await idle();
    assert.equal(await row().locator('[data-storage-total]').textContent(),'650.00');await page.screenshot({path:'/tmp/finance-storage-remaining.png',fullPage:true});
    await row().getByRole('button',{name:'管理',exact:true}).click();await page.click('[data-storage-tab=transfers]');await page.locator('[data-transfer-id]').getByRole('button',{name:'撤销'}).click();await idle();
    await text('.storage-summary','-50.00');assert.equal((await report()).row.transfers.length,0);await page.keyboard.press('Escape');
    const download=page.waitForEvent('download');await page.click('#budget-export');assert.equal((await download).suggestedFilename(),'budget-plan.xlsx');
    assert.deepEqual(await get('/api/accounts'),before);assert.deepEqual(errors,[]);
    console.log('PASS default deductions, negative storage, capped transfers, editing, expense overrides, restoring default and cancellation');
    console.log('PASS current balance matrix, transpose, root grouping, carry without monthly plan, range independence, export and unchanged accounts');
  }finally{await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exit(1);});

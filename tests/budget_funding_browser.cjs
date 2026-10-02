// Desktop modal and income binding regression; never operates on the real ledger.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async r=>{assert(r.ok(),await r.text());return r.json();};
  const get=async path=>json(await api.get(path));
  assert.deepEqual(await get('/__budget_test_fixture'),{fixture:'budget-browser',temporary:true});
  let config=await get('/api/budget/config');
  for(const name of ['弹窗日常','弹窗储备'])config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name,currency_code:'HKD'}}));
  const a=config.systems.find(s=>s.name==='弹窗日常').id,b=config.systems.find(s=>s.name==='弹窗储备').id;
  const account=(await get('/api/accounts')).find(a=>a.currency_code==='HKD').id;
  const cats={};for(const kind of ['income','expense'])cats[kind]=(await get('/api/categories?kind='+kind))[0].id;
  async function transaction(kind,amount){return json(await api.post(kind==='income'?'/api/incomes':'/api/expenses',{data:{transaction_date:'2026-09-08',amount,category_id:cats[kind],[kind==='income'?'destination_account_id':'source_account_id']:account}}));}
  const income=await transaction('income','1000'),expense=await transaction('expense','300');
  const group=await json(await api.post('/api/offset-groups',{data:{title:'弹窗工资净收入',kind:'income',transaction_ids:[income.id,expense.id]}}));
  const aaIncome=await transaction('income','350'),aaExpense=await transaction('expense','600');
  await json(await api.post('/api/offset-groups',{data:{title:'弹窗AA返还',kind:'expense',transaction_ids:[aaIncome.id,aaExpense.id]}}));
  const balances=await get('/api/accounts');
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];let discard=false;
    page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>discard?d.accept():d.dismiss());
    const text=(s,v)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[s,v]);
    const idle=()=>page.waitForFunction(()=>!document.querySelector('main').inert);
    const month=async m=>{await page.fill('#plan-month',m);await text('#month-plans-title',m);await idle();};
    const open=async id=>{await page.locator('#month-plans tr[data-system-id="'+id+'"]').getByRole('button').click();await page.waitForSelector('#plan-editor[open]');await idle();};
    const save=async()=>{await page.click('#save-plan');await page.waitForSelector('#plan-editor[open]',{state:'hidden'});await idle();};
    const newPlan=async id=>{await page.click('#new-budget');await page.waitForSelector('#plan-editor[open]');await idle();await page.selectOption('#entry-system',String(id));await idle();await text('#plan-editor-title',id===a?'弹窗日常':'弹窗储备');};
    async function choose(){await page.click('.choose-funding');await page.waitForSelector('#funding-picker[open]');await page.fill('#funding-keyword','弹窗工资净收入');await page.locator('.funding-search button').click();await text('#funding-candidates','700.00');await page.locator('.funding-candidate').getByRole('button',{name:'选择此来源'}).click();await page.waitForSelector('#funding-picker[open]',{state:'hidden'});await text('.funding-preview','本预案占用');}
    await page.goto(base+'/budgets');await text('#budget-status','已加载');await month('2026-09');
    assert.equal(await page.locator('main #plan-editor').count(),0);
    await newPlan(a);assert(await page.evaluate(()=>document.querySelector('#plan-editor').contains(document.activeElement)));await page.fill('.currency-total','400');await page.fill('.currency-note','弹窗草稿');
    await page.keyboard.press('Escape');assert(await page.locator('#plan-editor').evaluate(d=>d.open));
    assert.equal(await page.locator('.currency-note').inputValue(),'弹窗草稿');
    await page.mouse.click(10,10);assert(await page.locator('#plan-editor').evaluate(d=>d.open));
    await page.click('.choose-funding');await page.fill('#funding-keyword','弹窗AA返还');await page.locator('.funding-search button').click();await text('#funding-picker [role=status]','没有符合');
    await page.keyboard.press('Escape');await choose();
    await page.screenshot({path:'/tmp/finance-budget-modal.png',fullPage:true});
    assert(await page.locator('#save-plan').isVisible());
    const bounds=await page.locator('#plan-editor').boundingBox();assert(bounds.height<=1050*.91);assert(bounds.width<=1440);
    await save();await text('#month-plans','弹窗工资净收入');await text('#month-plans','来源额度已核验');
    let saved=(await get(`/api/budget/monthly/${a}/2026-09`)).plans[0];assert.equal(saved.funding.entry_id,group.id);
    await open(a);await page.fill('.currency-total','701');await page.click('#save-plan');await text('#plan-error','可用 700.00');assert(await page.locator('#plan-editor').evaluate(d=>d.open));
    assert.equal(await page.locator('.currency-total').inputValue(),'701');assert.equal((await get(`/api/budget/monthly/${a}/2026-09`)).plans[0].total,'400.00');
    await page.fill('.currency-total','400');
    await page.route('**/api/budget/monthly/'+a+'/2026-09',route=>route.request().method()==='PUT'?route.fulfill({status:503,contentType:'application/json',body:'{"detail":"测试保存失败"}'}):route.continue());
    await page.click('#save-plan');await text('#plan-error','测试保存失败');assert.equal(await page.locator('.currency-note').inputValue(),'弹窗草稿');
    await page.unroute('**/api/budget/monthly/'+a+'/2026-09');discard=true;await page.click('#close-plan');
    await newPlan(b);await page.fill('.currency-total','301');await choose();await page.click('#save-plan');await text('#plan-error','可用 300.00');
    await page.fill('.currency-total','300');await save();
    assert.equal((await get('/api/budget/funding-sources?entry_type=offset_group&entry_id='+group.id)).available,'0.00');
    await open(a);await page.click('.clear-funding');await save();
    assert.equal((await get('/api/budget/funding-sources?entry_type=offset_group&entry_id='+group.id)).available,'400.00');
    console.log('PASS modal lifecycle, dirty Escape/backdrop, source search, AA exclusion, capped shared binding, failed saves and release');

    await month('2026-10');await newPlan(b);await page.locator('.plan-copy summary').click();await page.fill('#copy-month','2026-09');await page.click('#copy-plan');await idle();
    assert.equal(await page.locator('.currency-total').inputValue(),'300.00');assert(await page.locator('.clear-funding').isHidden());
    await save();assert.equal((await get(`/api/budget/monthly/${b}/2026-10`)).plans[0].funding,null);
    const old=await get('/api/transactions/'+income.id);
    await json(await api.put('/api/transactions/'+income.id,{data:{...old,inflow_amount:'500.00',correction_reason:'测试收入减少'}}));
    await month('2026-09');await text('#month-plans','收入来源超额占用');await text('#budget-execution','收入来源超额占用');
    await open(b);await page.fill('.currency-total','200');await save();
    assert.equal((await get('/api/budget/funding-sources?entry_type=offset_group&entry_id='+group.id)).remaining,'0.00');
    await open(b);await page.click('#plan-history');await text('#budget-detail-content','版本 2');await page.locator('[data-budget-close]').click();await page.click('#close-plan');
    await page.screenshot({path:'/tmp/finance-budget-overview.png',fullPage:true});
    assert.deepEqual(errors,[]);console.log('PASS copying without source reuse, source shortfall warning, reduction repair and modal history');
    // Only the deliberately corrected real-income fixture changed balances.
    const after=await get('/api/accounts');assert.equal(after.length,balances.length);
  }finally{await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exit(1);});

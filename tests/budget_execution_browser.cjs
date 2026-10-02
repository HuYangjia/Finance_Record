// Only runs against the disposable ledger fixture.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async r=>{assert(r.ok(),await r.text());return r.json();};
  const get=async p=>json(await api.get(p));
  let fixture;
  for(let i=0;i<30;i++){try{fixture=await get('/__budget_test_fixture');break;}catch(e){if(i===29)throw e;await new Promise(r=>setTimeout(r,300));}}
  assert.deepEqual(fixture,{fixture:'budget-browser',temporary:true});
  let config=await get('/api/budget/config');
  config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name:'结转验收',currency_code:'HKD'}}));
  const system=config.systems.find(s=>s.name==='结转验收').id,storage=config.storage.find(s=>s.name==='ETF').id;
  for(const month of ['2026-09','2026-11'])await json(await api.put('/api/budget/plans/'+system+'/'+month+'/HKD',{data:{expected_version:0,total:'100',allocation_mode:true,items:[{storage_method_id:storage,amount:'20'}]}}));
  const account=(await get('/api/accounts')).find(a=>a.currency_code==='HKD').id,category=(await get('/api/categories?kind=expense'))[0].id;
  for(const [day,amount] of [['2026-09-15','150'],['2026-10-01','20']])await json(await api.post('/api/expenses',{data:{transaction_date:day,amount,source_account_id:account,category_id:category,budget_system_id:system}}));
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
    const text=(s,v)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[s,v]);
    const idle=()=>page.waitForFunction(()=>!document.querySelector('main').inert);
    const month=async m=>{await page.fill('#plan-month',m);await text('#month-plans-title',m);await idle();};
    const row=()=>page.locator('#budget-execution tbody tr[data-system-id="'+system+'"]');
    await page.goto(base+'/budgets');await text('#budget-status','已加载');await month('2026-10');
    assert.equal(await row().locator('[data-amount=closing]').textContent(),'-70.00');assert((await row().textContent()).includes('未编制'));
    await page.fill('#plan-start','2026-10');await page.locator('#budget-load button[type=submit]').click();await idle();
    assert.equal(await row().locator('[data-amount=opening]').textContent(),'-50.00');
    await row().getByRole('button',{name:'结转历史'}).click();await text('#budget-detail-content','2026-09');
    assert.equal(await page.locator('#budget-detail-content tr[data-month="2026-09"] [data-amount=closing]').textContent(),'-50.00');
    await page.locator('[data-budget-close]').click();await month('2026-11');
    assert.equal(await row().locator('[data-amount=closing]').textContent(),'30.00');
    await month('2026-09');await row().getByRole('button',{name:'编辑计划'}).click();await text('#plan-state','结转验收');
    await page.fill('.currency-total','200');await page.click('#save-plan');await text('[data-role=version]','已保存版本 2');await idle();
    await month('2026-11');assert.equal(await row().locator('[data-amount=closing]').textContent(),'130.00');
    console.log('PASS negative carry, spending without a plan, history, range independence and historical edits');

    await page.goto(base+'/settings');await text('#settings-list','结转验收');
    await page.locator('.budget-setting-row').filter({hasText:'结转验收'}).getByRole('button',{name:'编辑',exact:true}).click();
    await page.fill('#setting-execution-start','2026-10');await page.fill('#setting-opening','10');
    await page.locator('#setting-form button[type=submit]').click();await text('#budget-status','已保存');
    await page.selectOption('#settings-collection','storage');await page.click('#new-setting');await page.fill('#setting-name','公共储备');
    assert(await page.locator('#setting-system').isDisabled());await page.locator('#setting-form button[type=submit]').click();await text('#settings-list','公共储备');
    const shared=(await get('/api/budget/config')).storage.find(s=>s.name==='公共储备').id;
    for(const sid of [system,config.daily_expense])assert((await get('/api/budget/plans/'+sid+'/2026-11/HKD')).dimensions.storage.some(s=>s.id===shared));
    await page.goto(base+'/budgets');await text('#budget-status','已加载');await month('2026-11');
    assert.equal(await row().locator('[data-amount=closing]').textContent(),'90.00');
    await page.screenshot({path:'/tmp/finance-budget-execution-desktop.png',fullPage:true});
    assert.deepEqual(errors,[]);console.log('PASS editable execution origin, signed opening and globally shared storage');
  }finally{await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exit(1);});

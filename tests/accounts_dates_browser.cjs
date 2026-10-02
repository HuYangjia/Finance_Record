// Date controls and full-history summaries, using only the disposable fixture.
const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18110';
(async()=>{
  const api=await request.newContext({baseURL:base});
  const json=async r=>{assert(r.ok(),await r.text());return r.json();};
  const get=async path=>json(await api.get(path));
  assert.deepEqual(await get('/__budget_test_fixture'),{fixture:'budget-browser',temporary:true});
  let config=await get('/api/budget/config');
  config=await json(await api.post('/api/budget/config/systems',{data:{expected_version:config.version,name:'日期筛选验收',currency_code:'HKD'}}));
  const system=config.systems.find(s=>s.name==='日期筛选验收').id;
  const account=(await get('/api/accounts')).find(a=>a.currency_code==='HKD').id,category=(await get('/api/categories?kind=expense'))[0].id;
  const dates=[['2024-02-29','30'],['2025-12-31','40'],['2026-09-15','10'],['2026-10-01','20'],...Array.from({length:21},()=>['2026-10-02','1'])];
  for(const [transaction_date,amount] of dates)await json(await api.post('/api/expenses',{data:{transaction_date,amount,source_account_id:account,category_id:category,budget_system_id:system}}));
  const balances=await get('/api/accounts'),errors=[];
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:'Asia/Shanghai'});
    await page.clock.install({time:new Date('2026-10-02T12:00:00+08:00')});
    page.on('pageerror',error=>errors.push(error.message));
    const text=(selector,value)=>page.waitForFunction(([s,v])=>document.querySelector(s)?.textContent.includes(v),[selector,value]);
    const range=async (prefix,start,end)=>{
      assert.equal(await page.locator('#'+prefix+'-start-date').inputValue(),start);
      assert.equal(await page.locator('#'+prefix+'-end-date').inputValue(),end);
    };
    const click=async (prefix,selector)=>{
      const response=page.waitForResponse(r=>r.url().includes(prefix==='detail'?'/detail?':'/api/transactions/search?'));
      await page.locator('#'+prefix+'-date-presets '+selector).click();await response;
    };
    await page.goto(base+'/accounts');await text('#ledger-summary','共');
    await page.waitForFunction(()=>document.querySelector('#search-budget-system').options.length>2);
    await range('detail','2026-10-01','2026-10-31');await range('search','2026-10-01','2026-10-31');
    await click('detail','[data-period=previous-month]');await range('detail','2026-09-01','2026-09-30');
    await click('detail','[data-shift="-1"]');await range('detail','2026-08-01','2026-08-31');
    await page.locator('[data-view=search]').click();await page.selectOption('#search-budget-system',String(system));
    await page.locator('#search-type-options input[value=expense]').check();
    await click('search','[data-period=all]');await text('#transaction-search-summary-meta','25 笔');
    await range('search','','');await text('#transaction-search-summary-meta','全部历史');await text('#transaction-search-summary-content','121.00 HKD');
    assert.equal(await page.locator('#search-budget-system').inputValue(),String(system));
    assert(await page.locator('#search-type-options input[value=expense]').isChecked());
    await text('#analysis-constraint','日期');assert(await page.locator('#search-date-presets [data-shift="-1"]').isHidden());
    await page.locator('#search-results-details summary').click();await page.click('#next-search-page');await text('#search-page-label','第 2 页');
    await click('search','[data-period=month]');await text('#search-page-label','第 1 页');await text('#transaction-search-summary-meta','22 笔');
    await click('search','[data-period=quarter]');await range('search','2026-10-01','2026-12-31');
    await click('search','[data-shift="-1"]');await range('search','2026-07-01','2026-09-30');await text('#transaction-search-summary-meta','1 笔');
    await click('search','[data-period=year]');await click('search','[data-shift="-1"]');await range('search','2025-01-01','2025-12-31');await text('#transaction-search-summary-content','40.00 HKD');
    await page.fill('#search-start-date','2024-02-29');await page.fill('#search-end-date','2024-02-29');
    assert.equal(await page.locator('#search-date-presets [data-period=custom]').getAttribute('aria-pressed'),'true');
    await page.locator('#transaction-search-form button[type=submit]').click();await text('#transaction-search-summary-content','30.00 HKD');
    await page.fill('#search-start-date','');await page.locator('#transaction-search-form button[type=submit]').click();await text('#transaction-search-summary-meta','截至 2024-02-29');
    await page.locator('[data-view=account]').click();await range('detail','2026-08-01','2026-08-31');
    await page.click('#clear-detail-filters');await range('detail','2026-10-01','2026-10-31');
    await page.locator('[data-view=search]').click();await range('search','','2024-02-29');
    await page.click('#clear-search-filters');await range('search','2026-10-01','2026-10-31');
    assert.equal(await page.locator('#search-budget-system').inputValue(),'');
    assert.equal(await page.locator('#search-type-options input:checked').count(),0);
    await text('#transaction-search-summary-meta','2026-10-01 至 2026-10-31');
    console.log('PASS defaults, independent views, month/quarter/year navigation, custom/one-sided/all summaries, filter preservation, pagination and reset');

    // Slow old success and old error must not overwrite the newly requested period.
    await page.route('**/api/transactions/search?**',async route=>{
      if(new URL(route.request().url()).searchParams.get('start_date')==='2026-09-01'){
        await new Promise(resolve=>setTimeout(resolve,500));
        await route.fulfill({status:503,contentType:'application/json',body:'{"detail":"过期请求错误"}'});
      }else await route.continue();
    });
    const stale=page.waitForResponse(r=>r.status()===503);
    await page.locator('#search-date-presets [data-period=previous-month]').click();
    await click('search','[data-period=month]');await stale;
    await text('#transaction-search-summary-meta','2026-10-01 至 2026-10-31');
    assert.equal(await page.locator('#search-status').textContent(),'');
    await page.unroute('**/api/transactions/search?**');
    let oldDone;
    await page.route('**/api/accounts/*/detail?**',async route=>{
      if(new URL(route.request().url()).searchParams.get('start_date')==='2026-09-01'){
        const response=await route.fetch();await new Promise(resolve=>setTimeout(resolve,500));await route.fulfill({response});oldDone?.();
      }else await route.continue();
    });
    await page.locator('[data-view=account]').click();
    const finished=new Promise(resolve=>{oldDone=resolve;});
    await page.locator('#detail-date-presets [data-period=previous-month]').click();await click('detail','[data-period=month]');await finished;
    await range('detail','2026-10-01','2026-10-31');
    const ledgerDates=await page.locator('#ledger-body tr td:first-child').allTextContents();
    assert(ledgerDates.every(d=>d.startsWith('2026-10')||d.includes('没有符合')));
    await page.unroute('**/api/accounts/*/detail?**');
    await page.locator('[data-view=search]').click();await page.screenshot({path:'/tmp/finance-accounts-dates-desktop.png',fullPage:true});
    assert.deepEqual(await get('/api/accounts'),balances);assert.deepEqual(errors,[]);
    console.log('PASS stale responses ignored and full account balances unchanged');

    const leap=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:'Asia/Shanghai'});
    await leap.clock.install({time:new Date('2024-03-10T12:00:00+08:00')});await leap.goto(base+'/accounts');
    await leap.waitForFunction(()=>document.querySelector('#ledger-summary').textContent.includes('共'));
    await leap.locator('#detail-date-presets [data-period=previous-month]').click();
    assert.equal(await leap.locator('#detail-end-date').inputValue(),'2024-02-29');
    await leap.locator('#detail-date-presets [data-shift="-1"]').click();
    await leap.locator('#detail-date-presets [data-shift="-1"]').click();
    assert.equal(await leap.locator('#detail-start-date').inputValue(),'2023-12-01');
    assert.equal(await leap.locator('#detail-end-date').inputValue(),'2023-12-31');
    console.log('PASS leap-year month end and year boundary');
  }finally{await browser.close();await api.dispose();}
})().catch(error=>{console.error(error);process.exit(1);});

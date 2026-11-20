// One stable request per pending entry, retained across reloads in this tab.
window.EntryRequests = (() => {
  const endpoints = {expense:'/api/expenses',income:'/api/incomes',transfer:'/api/transfers'};
  const prefix = 'finance-pending-entry-v1:';
  const pending = new Map();
  for (const [mode, endpoint] of Object.entries(endpoints)) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(prefix + mode) || 'null');
      if (saved?.endpoint === endpoint && typeof saved.payload?.request_id === 'string') {
        pending.set(mode, {...saved, persistent:true});
      }
    } catch (_) { /* Unavailable browser storage does not disable ordinary entry. */ }
  }
  const get = mode => pending.get(mode);
  function prepare(mode, endpoint, payload) {
    if (get(mode)) throw new Error('请先确认该模式上一笔记录的保存结果。');
    if (endpoints[mode] !== endpoint) throw new Error('记账模式不正确，请刷新页面。');
    const item = {endpoint, payload:{...payload, request_id:crypto.randomUUID()}, persistent:false};
    pending.set(mode, item);
    try {
      sessionStorage.setItem(prefix + mode, JSON.stringify(item));
      item.persistent = true;
    } catch (_) { /* The in-memory copy still permits safe retries in this page. */ }
    return item;
  }
  function clear(mode) {
    pending.delete(mode);
    try { sessionStorage.removeItem(prefix + mode); } catch (_) {}
  }
  async function send(mode) {
    const item = get(mode);
    if (!item) throw new Error('没有需要确认的记录。');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(item.endpoint, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify(item.payload), signal:controller.signal,
      });
      // An unreadable response is uncertain, even if the server sent HTTP 201.
      const result = await response.json();
      if (!response.ok) {
        const detail = result.detail;
        const error = new Error(typeof detail === 'string' ? detail :
          detail?.message || '请检查日期、金额和必填选项。');
        error.rejected = [400,404,409,422].includes(response.status) &&
          detail?.code !== 'entry_request_conflict';
        throw error;
      }
      if (!Number.isInteger(result.id) || result.id < 1) throw new Error('未收到完整的保存回执。');
      return result;
    } finally {
      clearTimeout(timeout);
    }
  }
  return {get, prepare, clear, send};
})();

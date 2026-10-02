(() => {
  // Calendar-only arithmetic avoids timezone and daylight-saving shifts.
  function day(year, month, date) {
    const value = new Date(0);
    value.setUTCFullYear(year, month, date);
    return `${String(value.getUTCFullYear()).padStart(4, '0')}-${String(value.getUTCMonth()+1).padStart(2, '0')}-${String(value.getUTCDate()).padStart(2, '0')}`;
  }
  function create({host, start, end, onApply}) {
    if (!host) return {reset() {}, sync() {}, label: () => ''};
    let mode = 'month', year, month, lastStart, lastEnd;
    host.classList.add('date-range-controls');
    const presets = document.createElement('div');
    presets.className = 'date-range-presets';
    presets.setAttribute('role', 'group');
    presets.setAttribute('aria-label', '快捷日期范围');
    const period = document.createElement('div');
    period.className = 'date-range-period';
    function button(text, action) {
      const element = document.createElement('button');
      element.type = 'button'; element.className = 'secondary-button'; element.textContent = text;
      element.addEventListener('click', action);
      return element;
    }
    const buttons = new Map();
    for (const [key, text] of [['month','本月'],['previous-month','上月'],['quarter','本季度'],['year','本年'],['all','全部'],['custom','自定义']]) {
      const control = button(text, () => {
        if (key === 'custom') {mode = 'custom'; render(); start.focus(); return;}
        mode = key === 'previous-month' ? 'month' : key;
        const now = new Date(); year = now.getFullYear(); month = now.getMonth();
        if (key === 'previous-month') {month--; if (month < 0) {year--; month = 11;}}
        apply(); onApply();
      });
      control.dataset.period = key; buttons.set(key, control); presets.append(control);
    }
    const previous = button('← 上一月', () => move(-1)); previous.dataset.shift = '-1';
    const next = button('下一月 →', () => move(1)); next.dataset.shift = '1';
    const caption = document.createElement('span'); caption.className = 'date-range-caption';
    caption.setAttribute('aria-live', 'polite');
    period.append(previous, caption, next); host.replaceChildren(presets, period);
    function label() {
      if (start.value && end.value) return `${start.value} 至 ${end.value}`;
      if (start.value) return `${start.value} 起`;
      if (end.value) return `截至 ${end.value}`;
      return '全部历史';
    }
    function periodStart(mode, year, month) {
      return day(year, mode === 'year' ? 0 : mode === 'quarter' ? Math.floor(month/3)*3 : month, 1);
    }
    function render() {
      const now = new Date(), current = periodStart(mode, now.getFullYear(), now.getMonth());
      const lastMonth = day(now.getFullYear(), now.getMonth()-1, 1);
      for (const [key, control] of buttons) {
        const active = key === 'previous-month' ? mode === 'month' && start.value === lastMonth
          : key === mode && (['custom','all'].includes(mode) || start.value === current);
        control.classList.toggle('is-active', active); control.setAttribute('aria-pressed', String(active));
      }
      const movable = ['month','quarter','year'].includes(mode);
      previous.hidden = next.hidden = !movable;
      const unit = {month:'月',quarter:'季度',year:'年'}[mode];
      previous.textContent = `← 上一${unit}`; next.textContent = `下一${unit} →`;
      previous.disabled = start.value === '0001-01-01'; next.disabled = end.value === '9999-12-31';
      const name = mode === 'month' ? `${year} 年 ${month+1} 月` : mode === 'quarter' ? `${year} 年第 ${Math.floor(month/3)+1} 季度` : mode === 'year' ? `${year} 年` : '';
      caption.textContent = name ? `${name} · ${label()}` : label();
    }
    function apply() {
      if (mode === 'all') start.value = end.value = '';
      else {
        const length = {month:1,quarter:3,year:12}[mode];
        month = mode === 'year' ? 0 : mode === 'quarter' ? Math.floor(month/3)*3 : month;
        start.value = day(year, month, 1); end.value = day(year, month+length, 0);
      }
      lastStart = start.value; lastEnd = end.value; render();
    }
    function move(direction) {
      const index = year*12+month+direction*({month:1,quarter:3,year:12}[mode] || 0);
      if (index < 12 || index >= 120000) return;
      year = Math.floor(index/12); month = index%12; apply(); onApply();
    }
    function reset() {
      const now = new Date(); year = now.getFullYear(); month = now.getMonth(); mode = 'month'; apply();
    }
    function sync() {
      if (start.value !== lastStart || end.value !== lastEnd) {
        mode = !start.value && !end.value ? 'all' : 'custom';
        lastStart = start.value; lastEnd = end.value; render();
      }
    }
    for (const input of [start, end]) input.addEventListener('input', sync);
    reset();
    return {reset, sync, label};
  }
  window.DateRangeControls = {create};
})();

window.BudgetUI = (() => {
  const $ = id => document.getElementById(id);
  function node(tag, text='', className='') { const e=document.createElement(tag);e.textContent=text;e.className=className;return e; }
  async function request(url, body, method='GET') {
    const r=await fetch(url,{method, ...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'请检查名称、金额及必填选项。');
    return data;
  }
  function options(select, rows, empty='请选择', selected=select.value) {
    select.replaceChildren();if(empty!==null){const o=node('option',empty);o.value='';select.append(o);}
    for(const r of rows){const o=node('option',r.path||r.name||r.code);o.value=r.id??r.code;if(r.is_active===0)o.textContent+='（停用）';select.append(o);}
    select.value=selected==null?'':String(selected);
  }
  function status(message,error=false){$('budget-status').textContent=message;$('budget-status').className=error?'dialog-status error':'dialog-status';}
  function button(label, fn, cls='secondary-button'){const b=node('button',label,cls);b.type='button';b.onclick=fn;return b;}
  return {$,node,request,options,status,button};
})();

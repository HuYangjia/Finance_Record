"""Run with project-local Windows Python; exercise the real launcher on a disposable copy."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    assert sys.platform == 'win32', 'Run with .venv-win/Scripts/python.exe'
    root = Path(__file__).resolve().parents[1]
    runtime = root/'.runtime'/'windows'
    with tempfile.TemporaryDirectory(prefix='native startup 中文-', dir=runtime/'tmp') as directory:
        fixture = Path(directory)
        for name in ('app','static','scripts'):
            shutil.copytree(root/name, fixture/name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('requirements.txt','requirements-dev.txt'):
            shutil.copy(root/name, fixture/name)
        python_root = fixture/'.runtime'/'windows'/'python'
        shutil.copytree(Path(sys.base_prefix), python_root/'cpython-test')
        interpreter = python_root/'cpython-test'/'python.exe'
        venv = fixture/'.venv-win'
        environment = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(python_root), UVICORN_USE_COLORS='true')
        subprocess.run([str(runtime/'tools'/'uv.exe'),'venv','--python',str(interpreter),str(venv)],
                       env=environment, cwd=fixture, check=True)
        python = venv/'Scripts'/'python.exe'
        subprocess.run([str(runtime/'tools'/'uv.exe'),'pip','install','--offline','--python',str(python),
                        '-r','requirements.txt'], env=environment, cwd=fixture, check=True)
        bootstrap = r'''
from app import database
from datetime import datetime
from zoneinfo import ZoneInfo
database.initialize_database()
day=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
with database.connect() as c:
    c.execute("INSERT INTO accounts(code,name,account_class,account_type,currency_code,opening_balance_date) VALUES ('CASH','test cash','asset','cash','HKD',?)",(day,))
    c.execute("INSERT INTO accounts(code,name,account_class,account_type,currency_code,directory_key,opening_balance_date) VALUES ('CARD','test card','liability','credit_card','HKD','liability',?)",(day,))
    c.execute("INSERT INTO daily_cny_exchange_rates(rate_date,usd_cny_rate,hkd_cny_rate,source,market_date) VALUES (?,'7','0.9','fixture',?)",(day,day))
'''
        subprocess.run([str(python),'-c',bootstrap],cwd=fixture,env=environment,check=True)
        # Record the same ready marker the setup script writes, after preparing only local files.
        prepare = r'''
        . .\scripts\windows-environment.ps1; $p=Get-FinanceWindowsPaths; Set-FinanceWindowsEnvironment $p;
        $s=@{root=$p.Root;signature=(Get-FinanceEnvironmentSignature $p);dev=$false};
        [IO.File]::WriteAllText($p.Stamp,($s | ConvertTo-Json));
        '''
        subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-Command',prepare],cwd=fixture,check=True)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0))
            port=reservation.getsockname()[1]
        log_path=runtime/'service-smoke.log'
        started=time.perf_counter()
        with log_path.open('w',encoding='utf-8') as log:
            child=subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                str(fixture/'scripts'/'start-windows.ps1'),'-Port',str(port)],cwd=fixture,env=environment,
                stdout=log,stderr=log,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                base=f'http://127.0.0.1:{port}'
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

                def request(path,body=None):
                    payload=json.dumps(body).encode() if body is not None else None
                    with opener.open(urllib.request.Request(base+path,data=payload,
                            headers={'Content-Type':'application/json'}),timeout=5) as response:
                        data=response.read()
                        return json.loads(data) if 'json' in response.headers.get('Content-Type','') else data

                while True:
                    assert child.poll() is None, log_path.read_text(encoding='utf-8',errors='replace')
                    try:
                        assert request('/api/health')=={'status':'ok'}
                        break
                    except (OSError,urllib.error.URLError):
                        if time.perf_counter()-started>20:
                            raise AssertionError('Windows launcher did not become ready')
                        time.sleep(.1)
                ready=time.perf_counter()-started
                from datetime import datetime
                from zoneinfo import ZoneInfo
                day=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
                month=day[:7]
                categories={kind:request('/api/categories?kind='+kind)[0]['id'] for kind in ('income','expense')}
                daily=request('/api/budget/config')['defaults']['expense']
                income=request('/api/incomes',dict(transaction_date=day,destination_account_id=1,
                    amount='100',category_id=categories['income'],budget_allocations=[dict(budget_system_id=daily,amount=None)]))
                expense=request('/api/expenses',dict(transaction_date=day,source_account_id=2,
                    amount='10',category_id=categories['expense']))
                assert expense['account_balance']=='10.00'
                request('/api/transfers',dict(transaction_date=day,source_account_id=1,destination_account_id=2,outflow_amount='10'))
                accounts={a['id']:a for a in request('/api/accounts')}
                assert accounts[1]['balance']=='90.00' and accounts[2]['balance']=='0.00'
                overview=request('/api/budget/overview?end_month='+month)
                row=next(r for r in overview['execution']['rows'] if r['budget_system_id']==daily)
                assert row['closing']=='90.00',row
                assert request('/api/budget/overview.xlsx?end_month='+month).startswith(b'PK')
                assert b'<html' in request('/').lower()
                logs=log_path.read_text(encoding='utf-8',errors='replace')
                assert 'GET /api/health' in logs and '200 OK' in logs,logs
                assert '\x1b[' not in logs, 'ANSI control codes leaked into Windows logs'
                print(json.dumps(dict(ready_seconds=round(ready,3),health=True,html=True,
                    income_allocation=True,credit_card_expense_and_repayment=True,budget=True,excel=True,
                    plain_text_logs=True,formal_ledger_modified=False)))
            finally:
                if child.poll() is None:
                    try:
                        child.send_signal(signal.CTRL_BREAK_EVENT)
                        child.wait(timeout=8)
                    except (OSError,subprocess.TimeoutExpired):
                        subprocess.run(['taskkill.exe','/PID',str(child.pid),'/T','/F'],capture_output=True)
                        child.wait(timeout=8)


if __name__=='__main__':
    main()

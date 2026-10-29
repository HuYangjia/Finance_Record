"""Fixed principal deposits: contract metadata and atomic internal cash movements."""
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from app.database import ROOT_DIR, connect, create_backup

router = APIRouter()
LIQUID_TYPES = {'cash', 'e_wallet', 'bank_demand', 'transit_card'}


def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


class DepositWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_account_id: int = Field(gt=0)
    principal: Decimal = Field(gt=0, le=Decimal('1000000000000'))
    start_date: date
    term_value: int = Field(gt=0, le=36500)
    term_unit: Literal['day', 'month'] = 'month'
    annual_rate: Decimal = Field(ge=0, le=100, decimal_places=6)
    day_basis: Literal[360, 365] = 365
    maturity_override: date | None = None
    note: str = Field(default='', max_length=500)
    expected_version: int | None = Field(default=None, gt=0)


class DepositVersion(BaseModel):
    expected_version: int = Field(gt=0)


class DepositSettlement(DepositVersion):
    settlement_date: date
    destination_account_id: int = Field(gt=0)
    actual_interest: Decimal = Field(ge=0, le=Decimal('1000000000000'))


def money(value, places):
    return f'{Decimal(value) / (10 ** places):.{places}f}'


def minor(value, places):
    scaled = value * 10 ** places
    if scaled != scaled.to_integral_value():
        raise HTTPException(422, f'该币种最多支持 {places} 位小数。')
    return int(scaled)


def dates(start, value, unit, override=None):
    try:
        if unit == 'month':
            if value > 1200:
                raise ValueError()
            month = start.year * 12 + start.month - 1 + value
            year, month_index = divmod(month, 12)
            automatic = date(year, month_index + 1,
                             min(start.day, monthrange(year, month_index + 1)[1]))
        else:
            automatic = start + timedelta(days=value)
    except (ValueError, OverflowError):
        raise HTTPException(422, '期限超出支持范围（最长 100 年）。')
    maturity = override or automatic
    if maturity <= start:
        raise HTTPException(422, '到期日必须晚于存入日期。')
    return automatic, maturity


def estimate(principal, rate, days, basis, places):
    return (principal * rate / 100 * days / basis).quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def require_liquid(c, account_id, allow_inactive=False):
    row = c.execute('''SELECT a.*,cur.decimal_places FROM accounts a
        JOIN currencies cur ON cur.code=a.currency_code WHERE a.id=?''', (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '账户不存在。')
    if (row['account_class'] != 'asset' or row['account_type'] not in LIQUID_TYPES
            or not row['is_active'] and not allow_inactive):
        raise HTTPException(422, '请选择启用的活期、现金或钱包资产账户。')
    return row


def validate_write(c, payload, previous=None):
    if not payload.name.strip():
        raise HTTPException(422, '请填写定存名称。')
    if payload.start_date > today():
        raise HTTPException(422, '存入日期不能晚于今天；请在实际存入后记账。')
    account = require_liquid(c, payload.source_account_id,
                             previous is not None and payload.source_account_id == previous['source_account_id'])
    if payload.start_date.isoformat() < account['opening_balance_date']:
        raise HTTPException(422, '存入日期不能早于来源账户期初日期。')
    minor(payload.principal, account['decimal_places'])
    automatic, maturity = dates(payload.start_date, payload.term_value, payload.term_unit, payload.maturity_override)
    return account, automatic, maturity


def term_account(c, account, start):
    linked = c.execute('SELECT term_account_id FROM term_deposit_accounts WHERE source_account_id=?',
                       (account['id'],)).fetchone()
    if linked:
        account_id = linked['term_account_id']
        c.execute('''UPDATE accounts SET opening_balance_date=MIN(opening_balance_date,?),
                     is_active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?''', (start, account_id))
        return account_id
    # IDs determine identity; names and bank labels are only presentation.
    code = f'TERM-{account["id"]}'
    while c.execute('SELECT 1 FROM accounts WHERE code=?', (code,)).fetchone():
        code += '-D'
    account_id = c.execute('''INSERT INTO accounts(code,name,account_class,account_type,
        currency_code,institution,directory_key,opening_balance_date,note)
        VALUES (?,?,'asset','bank_term',?,?,?,?,'由定期存款页面维护')''',
        (code, f'{account["name"]} · 定存', account['currency_code'], account['institution'],
         account['directory_key'], start)).lastrowid
    c.execute('INSERT INTO term_deposit_accounts VALUES (?,?)', (account['id'], account_id))
    return account_id


def transfer(c, day, source, destination, amount, note):
    return c.execute('''INSERT INTO transactions(transaction_date,transaction_type,source_account_id,
        destination_account_id,outflow_minor,inflow_minor,note)
        VALUES (?,'transfer',?,?,?,?,?)''', (day, source, destination, amount, amount, note)).lastrowid


def snapshot(c, deposit_id):
    row = c.execute('SELECT * FROM term_deposits WHERE id=?', (deposit_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '定期存款不存在。')
    data = dict(row)
    ids = [data[key] for key in ('opening_transaction_id', 'return_transaction_id', 'interest_transaction_id')
           if data[key] is not None]
    data['transactions'] = [dict(r) for r in c.execute(
        f'SELECT * FROM transactions WHERE id IN ({",".join("?" for _ in ids)}) ORDER BY id', ids)]
    return data


def audit(c, deposit_id, action, backup, before=None, after=None):
    c.execute('''INSERT INTO change_audit_logs(entity_type,entity_id,action,before_json,after_json,backup_file)
        VALUES ('term_deposit',?,?,?,?,?)''',
        (deposit_id, action, json.dumps(before, ensure_ascii=False) if before else None,
         json.dumps(after, ensure_ascii=False) if after else None, backup.name))


def require_version(record, expected, open_only=False):
    if expected != record['version']:
        raise HTTPException(409, '定存已更新，请关闭弹窗后重新打开。')
    if open_only and record['settlement_date']:
        raise HTTPException(409, '定存已经到账；如需更正，请先撤销到账。')


def guard_transaction(c, transaction_id):
    row = c.execute('''SELECT id FROM term_deposits WHERE opening_transaction_id=?
        OR return_transaction_id=? OR interest_transaction_id=?''', (transaction_id,) * 3).fetchone()
    if row:
        raise HTTPException(409, f'此流水属于定存 #{row["id"]}，请在“定期存款”页面维护。')


def guard_account(c, account_id):
    if c.execute('SELECT 1 FROM term_deposit_accounts WHERE term_account_id=?', (account_id,)).fetchone():
        raise HTTPException(409, '定存汇总账户由“定期存款”页面维护，请从该页面操作。')


def rows(c, as_of=None):
    as_of = as_of or today()
    data = []
    for row in c.execute('''SELECT d.*,a.name AS source_account_name,a.institution,
        a.currency_code,cur.decimal_places,m.term_account_id,
        ret.destination_account_id, dest.name AS destination_account_name
        FROM term_deposits d JOIN accounts a ON a.id=d.source_account_id
        JOIN currencies cur ON cur.code=a.currency_code
        JOIN term_deposit_accounts m ON m.source_account_id=a.id
        LEFT JOIN transactions ret ON ret.id=d.return_transaction_id
        LEFT JOIN accounts dest ON dest.id=ret.destination_account_id'''):
        record = dict(row)
        start = date.fromisoformat(record['start_date'])
        if start > as_of:
            continue
        automatic, maturity = dates(start, record['term_value'], record['term_unit'],
                                    date.fromisoformat(record['maturity_override']) if record['maturity_override'] else None)
        settled = bool(record['settlement_date'] and record['settlement_date'] <= as_of.isoformat())
        days_remaining = (maturity - as_of).days
        interest = estimate(Decimal(record['principal_minor']) / 10 ** record['decimal_places'],
                            Decimal(record['annual_rate']), (maturity - start).days,
                            record['day_basis'], record['decimal_places'])
        record.update(principal=money(record['principal_minor'], record['decimal_places']),
                      automatic_maturity_date=automatic.isoformat(), maturity_date=maturity.isoformat(),
                      term_days=(maturity - start).days, days_remaining=days_remaining,
                      expected_interest=f'{interest:.{record["decimal_places"]}f}',
                      actual_interest=money(record['actual_interest_minor'], record['decimal_places']) if settled else None,
                      status='settled' if settled else 'due' if days_remaining <= 0 else 'soon' if days_remaining <= 30 else 'active')
        data.append(record)
    return sorted(data, key=lambda r: (r['status'] == 'settled', r['maturity_date'], r['id']))


def response(c, deposit_id):
    record = next(r for r in rows(c) if r['id'] == deposit_id)
    balance = c.execute('SELECT balance_minor FROM account_balances WHERE id=?',
                        (record['source_account_id'],)).fetchone()
    record['source_balance'] = money(balance['balance_minor'], record['decimal_places'])
    return record


@router.get('/term-deposits', include_in_schema=False)
def page():
    return FileResponse(ROOT_DIR / 'static' / 'term-deposits.html', headers={'Cache-Control': 'no-store'})


@router.get('/api/term-deposits/preview')
def preview(source_account_id: int, principal: Decimal, start_date: date, term_value: int,
            annual_rate: Decimal, term_unit: Literal['day', 'month'] = 'month',
            day_basis: int = 365, maturity_override: date | None = None):
    try:
        payload = DepositWrite(name='预览', source_account_id=source_account_id, principal=principal,
                               start_date=start_date, term_value=term_value, term_unit=term_unit,
                               annual_rate=annual_rate, day_basis=day_basis, maturity_override=maturity_override)
    except ValidationError:
        raise HTTPException(422, '请检查本金、年利率和期限；期限及本金须大于零。')
    with connect() as c:
        account, automatic, maturity = validate_write(c, payload, {'source_account_id': source_account_id})
    value = estimate(principal, annual_rate, (maturity - start_date).days, day_basis, account['decimal_places'])
    return dict(currency_code=account['currency_code'], automatic_maturity_date=automatic.isoformat(),
                maturity_date=maturity.isoformat(), term_days=(maturity - start_date).days,
                expected_interest=f'{value:.{account["decimal_places"]}f}')


@router.get('/api/term-deposits')
def listing(source_account_id: int | None = None, currency_code: str | None = None,
            status: Literal['open', 'all', 'active', 'soon', 'due', 'settled'] = 'open'):
    with connect() as c:
        c.execute('BEGIN')
        records = rows(c)
    totals = {}
    for record in records:
        bucket = totals.setdefault(record['currency_code'], dict(currency_code=record['currency_code'],
            count=0, principal_minor=0, due_minor=0, expected_interest=Decimal(0), places=record['decimal_places']))
        if record['status'] != 'settled':
            bucket['count'] += 1
            bucket['principal_minor'] += record['principal_minor']
            bucket['expected_interest'] += Decimal(record['expected_interest'])
            if record['days_remaining'] <= 30:
                bucket['due_minor'] += record['principal_minor']
    summary = [dict(currency_code=code, count=b['count'], principal=money(b['principal_minor'], b['places']),
                    due_principal=money(b['due_minor'], b['places']),
                    expected_interest=f'{b["expected_interest"]:.{b["places"]}f}')
               for code, b in sorted(totals.items())]
    filtered = [r for r in records if (source_account_id is None or r['source_account_id'] == source_account_id)
                and (not currency_code or r['currency_code'] == currency_code)
                and (status == 'all' or status == 'open' and r['status'] != 'settled' or r['status'] == status)]
    return dict(as_of_date=today().isoformat(), deposits=filtered, summary=summary,
                total=len(filtered), open_count=sum(b['count'] for b in totals.values()))


@router.post('/api/term-deposits', status_code=201)
def create(payload: DepositWrite):
    backup = create_backup()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        account, _, _ = validate_write(c, payload)
        term_id = term_account(c, account, payload.start_date.isoformat())
        principal = minor(payload.principal, account['decimal_places'])
        opening = transfer(c, payload.start_date.isoformat(), account['id'], term_id, principal,
                           f'定存存入：{payload.name.strip()}')
        deposit_id = c.execute('''INSERT INTO term_deposits(name,source_account_id,principal_minor,
            start_date,term_value,term_unit,annual_rate,day_basis,maturity_override,opening_transaction_id,note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (payload.name.strip(), account['id'], principal, payload.start_date.isoformat(), payload.term_value,
             payload.term_unit, str(payload.annual_rate), payload.day_basis,
             payload.maturity_override.isoformat() if payload.maturity_override else None, opening, payload.note.strip())).lastrowid
        audit(c, deposit_id, 'create', backup, after=snapshot(c, deposit_id))
        return response(c, deposit_id)


@router.put('/api/term-deposits/{deposit_id}')
def update(deposit_id: int, payload: DepositWrite):
    backup = create_backup()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        before = snapshot(c, deposit_id)
        require_version(before, payload.expected_version, open_only=True)
        account, _, _ = validate_write(c, payload, before)
        term_id = term_account(c, account, payload.start_date.isoformat())
        principal = minor(payload.principal, account['decimal_places'])
        c.execute('''UPDATE transactions SET transaction_date=?,source_account_id=?,destination_account_id=?,
            outflow_minor=?,inflow_minor=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
            (payload.start_date.isoformat(), account['id'], term_id, principal, principal,
             f'定存存入：{payload.name.strip()}', before['opening_transaction_id']))
        c.execute('''UPDATE term_deposits SET name=?,source_account_id=?,principal_minor=?,start_date=?,
            term_value=?,term_unit=?,annual_rate=?,day_basis=?,maturity_override=?,note=?,
            version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
            (payload.name.strip(), account['id'], principal, payload.start_date.isoformat(), payload.term_value,
             payload.term_unit, str(payload.annual_rate), payload.day_basis,
             payload.maturity_override.isoformat() if payload.maturity_override else None, payload.note.strip(), deposit_id))
        audit(c, deposit_id, 'update', backup, before, snapshot(c, deposit_id))
        return response(c, deposit_id)


@router.post('/api/term-deposits/{deposit_id}/settle')
def settle(deposit_id: int, payload: DepositSettlement):
    backup = create_backup()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        before = snapshot(c, deposit_id)
        require_version(before, payload.expected_version, open_only=True)
        account = c.execute('SELECT a.*,cur.decimal_places FROM accounts a JOIN currencies cur ON cur.code=a.currency_code WHERE a.id=?',
                            (before['source_account_id'],)).fetchone()
        destination = require_liquid(c, payload.destination_account_id)
        if destination['currency_code'] != account['currency_code']:
            raise HTTPException(422, '本金和利息请先回到同币种账户；换汇到账后另行记录。')
        if not before['start_date'] <= payload.settlement_date.isoformat() <= today().isoformat():
            raise HTTPException(422, '到账日期须在存入日期至今天之间。')
        if payload.settlement_date.isoformat() < destination['opening_balance_date']:
            raise HTTPException(422, '到账日期不能早于收款账户期初日期。')
        interest = minor(payload.actual_interest, account['decimal_places'])
        term_id = c.execute('SELECT term_account_id FROM term_deposit_accounts WHERE source_account_id=?',
                            (before['source_account_id'],)).fetchone()[0]
        returned = transfer(c, payload.settlement_date.isoformat(), term_id, destination['id'],
                            before['principal_minor'], f'定存本金到账：{before["name"]}')
        interest_id = None
        if interest:
            category = c.execute("SELECT id FROM categories WHERE kind='income' AND name='利息/股息' AND is_active=1 AND NOT EXISTS (SELECT 1 FROM categories child WHERE child.parent_id=categories.id)").fetchone()
            if not category:
                raise HTTPException(422, '请先启用“利息/股息”末级收入分类。')
            interest_id = c.execute('''INSERT INTO transactions(transaction_date,transaction_type,
                destination_account_id,inflow_minor,category_id,note) VALUES (?,'income',?,?,?,?)''',
                (payload.settlement_date.isoformat(), destination['id'], interest, category['id'],
                 f'定存利息：{before["name"]}')).lastrowid
        c.execute('''UPDATE term_deposits SET settlement_date=?,return_transaction_id=?,interest_transaction_id=?,
            actual_interest_minor=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
            (payload.settlement_date.isoformat(), returned, interest_id, interest, deposit_id))
        audit(c, deposit_id, 'update', backup, before, snapshot(c, deposit_id))
        return response(c, deposit_id)


@router.post('/api/term-deposits/{deposit_id}/reopen')
def reopen(deposit_id: int, payload: DepositVersion):
    backup = create_backup()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        before = snapshot(c, deposit_id)
        require_version(before, payload.expected_version)
        if not before['settlement_date']:
            raise HTTPException(409, '该定存尚未确认到账。')
        interest_id = before['interest_transaction_id']
        if interest_id:
            from app.budget_funding import guard_reference
            guard_reference(c, 'transaction', interest_id)
            if c.execute('SELECT 1 FROM transaction_offset_members WHERE transaction_id=?', (interest_id,)).fetchone():
                raise HTTPException(409, '请先将该笔利息从冲销事项中移除。')
        c.execute('''UPDATE term_deposits SET settlement_date=NULL,return_transaction_id=NULL,
            interest_transaction_id=NULL,actual_interest_minor=NULL,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?''', (deposit_id,))
        c.execute('DELETE FROM transactions WHERE id IN (?,?)', (before['return_transaction_id'], interest_id))
        audit(c, deposit_id, 'update', backup, before, snapshot(c, deposit_id))
        return response(c, deposit_id)


@router.delete('/api/term-deposits/{deposit_id}')
def delete(deposit_id: int, payload: DepositVersion):
    backup = create_backup()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        before = snapshot(c, deposit_id)
        require_version(before, payload.expected_version, open_only=True)
        c.execute('DELETE FROM term_deposits WHERE id=?', (deposit_id,))
        c.execute('DELETE FROM transactions WHERE id=?', (before['opening_transaction_id'],))
        audit(c, deposit_id, 'delete', backup, before)
    return dict(id=deposit_id, deleted=True)

"""Editable offset groups; only relationship changes, never balance transactions."""
from __future__ import annotations

import json
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import connect, create_backup
from app.personal_cashflow import (read_ledger, build_entries, make_entry, serialize_entry,
                                   public_member, filter_entries, summarize, analyze, validate_filters)

router = APIRouter()


class OffsetWrite(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    kind: Literal['income', 'expense']
    anchor_transaction_id: int | None = Field(default=None, gt=0)
    statistics_date: date | None = None
    category_id: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=500)
    transaction_ids: list[int] = Field(min_length=1)
    expected_version: int | None = Field(default=None, gt=0)
    group_id: int | None = Field(default=None, gt=0)


class OffsetDelete(BaseModel):
    expected_version: int = Field(gt=0)


def group_snapshot(connection, group_id):
    records, groups, rates = read_ledger(connection)
    group = next((g for g in groups if g['id'] == group_id), None)
    if group is None:
        raise HTTPException(404, '冲销事项不存在。')
    return serialize_entry(make_entry(group, [r for r in records if r['offset_group_id'] == group_id], rates))


def group_for_transaction(connection, transaction_id):
    row = connection.execute('SELECT group_id FROM transaction_offset_members WHERE transaction_id = ?', (transaction_id,)).fetchone()
    return row['group_id'] if row else None


def audit_group(connection, group_id, action, backup_file, before=None, after=None):
    connection.execute("""INSERT INTO change_audit_logs
        (entity_type, entity_id, action, before_json, after_json, backup_file)
        VALUES ('offset_group', ?, ?, ?, ?, ?)""",
        (group_id, action, json.dumps(before, ensure_ascii=False) if before else None,
         json.dumps(after, ensure_ascii=False) if after else None, backup_file))


def validate_group(connection, payload, group_id=None):
    ids = payload.transaction_ids
    if not payload.title.strip() or len(ids) != len(set(ids)) or any(i < 1 for i in ids):
        raise HTTPException(422, '请填写事项名称，流水编号不能重复或小于 1。')
    records, groups, rates = read_ledger(connection)
    by_id = {r['id']: r for r in records}
    if any(i not in by_id for i in ids):
        raise HTTPException(422, '成员必须是仍然存在的收入或支出流水。')
    for i in ids:
        occupied = by_id[i]['offset_group_id']
        if occupied is not None and occupied != group_id:
            raise HTTPException(409, f'流水 #{i} 已属于冲销事项 #{occupied}，请先移除原关联。')
    original = next((g for g in groups if g['id'] == group_id), None)
    if group_id is not None and original is None:
        raise HTTPException(404, '冲销事项不存在。')
    if original and payload.expected_version != original['version']:
        raise HTTPException(409, '事项已经更新，请重新打开后核对；当前编辑尚未保存。')
    from app.budget_funding import guard_reference
    for member_id in ids:
        guard_reference(connection,'transaction',member_id)
    if original and original['kind']!=payload.kind:
        guard_reference(connection,'offset_group',group_id)
    metadata = {'id': group_id or 0, 'title': payload.title.strip(), 'kind': payload.kind,
                'note': (payload.note or '').strip() or None, 'version': original['version'] if original else 1}
    # Dates, category and anchor always come from the original atomic records.
    return make_entry(metadata, sorted((by_id[i] for i in ids), key=lambda r: (r['transaction_date'], r['id'])), rates)


@router.post('/api/offset-groups/preview')
def preview_group(payload: OffsetWrite):
    with connect() as connection:
        connection.execute('BEGIN')
        return serialize_entry(validate_group(connection, payload, payload.group_id))


@router.post('/api/offset-groups', status_code=201)
def create_group(payload: OffsetWrite):
    with connect() as connection:
        connection.execute('BEGIN')
        validate_group(connection, payload)
    backup = create_backup()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        entry = validate_group(connection, payload)
        cursor = connection.execute("""INSERT INTO transaction_offset_groups
            (title, kind, anchor_transaction_id, statistics_date, category_id, note)
            VALUES (?, ?, ?, ?, ?, ?)""", tuple(entry[k] for k in
            ('title', 'kind', 'anchor_transaction_id', 'statistics_date', 'category_id', 'note')))
        group_id = cursor.lastrowid
        connection.executemany('INSERT INTO transaction_offset_members (transaction_id, group_id) VALUES (?, ?)',
                               [(i, group_id) for i in payload.transaction_ids])
        result = group_snapshot(connection, group_id)
        audit_group(connection, group_id, 'create', backup.name, after=result)
    return result


@router.put('/api/offset-groups/{group_id}')
def update_group(group_id: int, payload: OffsetWrite):
    with connect() as connection:
        connection.execute('BEGIN')
        validate_group(connection, payload, group_id)
    backup = create_backup()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        entry = validate_group(connection, payload, group_id)
        before = group_snapshot(connection, group_id)
        connection.execute("""UPDATE transaction_offset_groups SET title=?, kind=?, anchor_transaction_id=?,
            statistics_date=?, category_id=?, note=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            tuple(entry[k] for k in ('title', 'kind', 'anchor_transaction_id', 'statistics_date', 'category_id', 'note')) + (group_id,))
        connection.execute('DELETE FROM transaction_offset_members WHERE group_id=?', (group_id,))
        connection.executemany('INSERT INTO transaction_offset_members (transaction_id, group_id) VALUES (?, ?)',
                               [(i, group_id) for i in payload.transaction_ids])
        after = group_snapshot(connection, group_id)
        audit_group(connection, group_id, 'update', backup.name, before, after)
    return after


@router.delete('/api/offset-groups/{group_id}')
def dissolve_group(group_id: int, payload: OffsetDelete):
    backup = create_backup()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        before = group_snapshot(connection, group_id)
        if payload.expected_version != before['version']:
            raise HTTPException(409, '事项已经更新，请刷新后重试。')
        from app.budget_funding import guard_reference
        guard_reference(connection,'offset_group',group_id)
        connection.execute('DELETE FROM transaction_offset_groups WHERE id=?', (group_id,))
        audit_group(connection, group_id, 'delete', backup.name, before)
    return {'id': group_id, 'deleted': True}


@router.get('/api/offset-groups/{group_id}')
def get_group(group_id: int):
    with connect() as connection:
        connection.execute('BEGIN')
        return group_snapshot(connection, group_id)


def query_entries(start_date=None, end_date=None, transaction_types=None, category_ids=None,
                  involved_account_id=None, keyword=None, flagged_only=False, limit=50, offset=0,
                  groups_only=False, entry_type=None, budget_system_id=None):
    filters = dict(start_date=start_date, end_date=end_date, transaction_types=transaction_types or [],
                   category_ids=category_ids or [], involved_account_id=involved_account_id,
                   keyword=keyword, flagged_only=flagged_only, entry_type=entry_type)
    validate_filters(**filters, limit=limit, offset=offset)
    with connect() as connection:
        connection.execute('BEGIN')
        if involved_account_id and not connection.execute('SELECT 1 FROM accounts WHERE id=?', (involved_account_id,)).fetchone():
            raise HTTPException(404, '账户不存在。')
        entries = build_entries(*read_ledger(connection))
        from app.income_allocations import project_income_budget
        entries = project_income_budget(connection,entries,budget_system_id)
    if groups_only:
        entries = [e for e in entries if e['entry_type'] == 'offset_group']
    entries = filter_entries(entries, **filters)
    warnings = []
    if budget_system_id is not None:
        warnings = [{'id':e['id'],'title':e['title']} for e in entries if e['budget_mixed'] and any(
            m['transaction_type']==e['kind'] and m.get('effective_budget_system_id',m.get('budget_system_id'))==(budget_system_id or None) for m in e['members'])]
    filters.update(budget_system_id=budget_system_id, budget_warnings=warnings)
    return filter_entries(entries, budget_system_id=budget_system_id), filters


@router.get('/api/personal-cashflow/search')
def search_personal(start_date: str | None = None, end_date: str | None = None,
                    transaction_types: list[str] = Query(default=[]), category_ids: list[int] = Query(default=[]),
                    involved_account_id: int | None = None, keyword: str | None = None,
                    flagged_only: bool = False, limit: int = 50, offset: int = 0, manual_adjustments_only: bool = False,
                    entry_type: Literal['transaction', 'offset_group'] | None = None, budget_system_id: int | None = None):
    if manual_adjustments_only:
        raise HTTPException(422, "冲销后收支不支持仅手工调整条件。")
    types = transaction_types if isinstance(transaction_types, list) else []
    categories = category_ids if isinstance(category_ids, list) else []
    entries, filters = query_entries(start_date, end_date, types, categories, involved_account_id,
                                      keyword, flagged_only, limit, offset, entry_type=entry_type, budget_system_id=budget_system_id)
    return {'basis': 'offset', 'filters': filters, 'budget_warnings': filters['budget_warnings'], 'pagination': {'limit': limit, 'offset': offset, 'total': len(entries)},
            'entries': [serialize_entry(e) for e in entries[offset:offset+limit]],
            'summary': {**summarize(entries), 'budget_complete': not filters['budget_warnings'] and
                        not any(e.get('allocation_basis') and any(not r['source_complete'] for r in e['income_allocations']) for e in entries)}
                       if start_date and end_date else {'available': False, 'reason': '请选择完整日期范围后查看金额汇总。'}}


@router.get('/api/offset-groups')
def list_groups(start_date: str | None = None, end_date: str | None = None, kind: Literal['income', 'expense'] | None = None,
                involved_account_id: int | None = None, keyword: str | None = None, category_id: int | None = None,
                limit: int = 50, offset: int = 0):
    entries, _ = query_entries(start_date, end_date, [kind] if kind else [], [category_id] if category_id is not None else [],
                               involved_account_id, keyword, limit=limit, offset=offset, groups_only=True)
    return {'groups': [serialize_entry(e) for e in entries[offset:offset+limit]], 'summary': summarize(entries),
            'pagination': {'limit': limit, 'offset': offset, 'total': len(entries)}}


@router.get('/api/personal-cashflow/analysis')
def analyze_personal(start_date: str, end_date: str, transaction_type: Literal['income', 'expense'],
                     category_ids: list[int] = Query(default=[]), involved_account_id: int | None = None,
                     keyword: str | None = None, flagged_only: bool = False, manual_adjustments_only: bool = False,
                     entry_type: Literal['transaction', 'offset_group'] | None = None, budget_system_id: int | None = None):
    if manual_adjustments_only:
        raise HTTPException(422, "冲销后收支不支持仅手工调整条件。")
    categories = category_ids if isinstance(category_ids, list) else []
    entries, filters = query_entries(start_date, end_date, [transaction_type], categories, involved_account_id, keyword, flagged_only, entry_type=entry_type, budget_system_id=budget_system_id)
    from app.income_allocations import allocation_metadata
    return {**analyze(entries, start_date, end_date, transaction_type), 'budget_warnings': filters['budget_warnings'],
            **allocation_metadata([r for e in entries for r in e.get('income_allocations',[])])}


@router.get('/api/offset-candidates')
def candidates(transaction_type: Literal['income', 'expense'], start_date: str | None = None,
               end_date: str | None = None, account_id: int | None = None, category_id: int | None = None,
               keyword: str | None = None, limit: int = 50, offset: int = 0):
    validate_filters(start_date=start_date, end_date=end_date, limit=limit, offset=offset, keyword=keyword,
                     involved_account_id=account_id, category_ids=[category_id] if category_id is not None else [])
    with connect() as connection:
        connection.execute('BEGIN')
        records, _, _ = read_ledger(connection)
    rows = [r for r in records if r['transaction_type'] == transaction_type
            and (not start_date or r['transaction_date'] >= start_date)
            and (not end_date or r['transaction_date'] <= end_date)
            and (not account_id or r['account_id'] == account_id)
            and (not category_id or r['category_id'] == category_id)
            and (not keyword or keyword.strip().casefold() in ' '.join(str(r.get(k) or '') for k in ('counterparty', 'note', 'category_name')).casefold())]
    rows.reverse()
    return {'transactions': [public_member(r) for r in rows[offset:offset+limit]],
            'pagination': {'limit': limit, 'offset': offset, 'total': len(rows)}}


def annotate_transactions(records):
    from app.budgets import daily_expense_system, effective_budget
    if not records:
        return records
    ids=list(dict.fromkeys(r['id'] for r in records))
    budgets,links={},{}
    with connect() as connection:
        connection.execute('BEGIN')
        daily = daily_expense_system(connection)
        from app.income_allocations import allocations_for_transactions
        allocations, _ = allocations_for_transactions(connection,ids)
        for start in range(0,len(ids),500):
            batch=ids[start:start+500];placeholders=','.join('?' for _ in batch)
            budgets.update({r['id']:dict(r) for r in connection.execute(f'''SELECT t.id,t.budget_system_id,b.name AS budget_system_name
                FROM transactions t LEFT JOIN budget_systems b ON b.id=t.budget_system_id WHERE t.id IN ({placeholders})''',batch)})
            links.update({row['transaction_id']:dict(row) for row in connection.execute(f'''
                SELECT m.transaction_id,g.id AS offset_group_id,g.title AS offset_group_title,
                       g.kind AS offset_group_kind FROM transaction_offset_members m
                JOIN transaction_offset_groups g ON g.id=m.group_id WHERE m.transaction_id IN ({placeholders})
            ''',batch)})
    for record in records:
        record['income_allocations'] = allocations.get(record['id'],[])
        record.update({k: budgets.get(record['id'],{}).get(k) for k in ('budget_system_id','budget_system_name')})
        effective_budget(record,daily)
        link = links.get(record['id'], {})
        record.update({k: link.get(k) for k in ('offset_group_id', 'offset_group_title', 'offset_group_kind')})
    return records


def check_member_update(connection, transaction_id, transaction_type):
    group_id = group_for_transaction(connection, transaction_id)
    if group_id is None:
        return None
    before = group_snapshot(connection, group_id)
    has_primary = transaction_type == before['kind'] or any(
        m['id'] != transaction_id and m['transaction_type'] == before['kind']
        for m in before['members']
    )
    if transaction_type not in ('income', 'expense') or not has_primary:
        raise HTTPException(409, f'事项 #{group_id} 须保留主方向流水，请先移除关联或调整事项方向。')
    return before


def record_member_update(connection, before, backup_file):
    if before is None:
        return
    derived = group_snapshot(connection, before['id'])
    connection.execute('''UPDATE transaction_offset_groups SET anchor_transaction_id=?, statistics_date=?,
        category_id=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?''',
        (derived['anchor_transaction_id'], derived['statistics_date'], derived['category_id'], before['id']))
    audit_group(connection, before['id'], 'update', backup_file, before, group_snapshot(connection, before['id']))

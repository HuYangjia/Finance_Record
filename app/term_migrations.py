"""Consolidate managed fixed deposits into one account for each currency."""
import json

from app.database import connect


VERSION_KEY = 'shared_term_deposit_version'


def ledger_signature(c):
    """Account consolidation may change endpoints, never money or ordinary balances."""
    return {
        'liquid_accounts': [tuple(r) for r in c.execute('''SELECT id,balance_minor FROM account_balances
            WHERE account_type!='bank_term' ORDER BY id''')],
        'assets': [tuple(r) for r in c.execute('''SELECT account_class,currency_code,SUM(balance_minor)
            FROM account_balances GROUP BY account_class,currency_code ORDER BY account_class,currency_code''')],
        'transactions': [tuple(r) for r in c.execute('''SELECT id,transaction_date,transaction_type,
            outflow_minor,inflow_minor,category_id,budget_system_id,counterparty,note,created_at
            FROM transactions ORDER BY id''')],
    }


def unique_target(c):
    for index in c.execute('PRAGMA index_list(term_deposit_accounts)').fetchall():
        columns = [r['name'] for r in c.execute('SELECT name FROM pragma_index_info(?)', (index['name'],))]
        if index['unique'] and columns == ['term_account_id']:
            return True
    return False


def migrate_shared_term_accounts(backup_path):
    c = connect()
    try:
        if c.execute('SELECT 1 FROM app_settings WHERE key=?', (VERSION_KEY,)).fetchone():
            return
        # SQLite table replacement must not cascade/rewrite the contract foreign key.
        # Only this connection disables enforcement; validate the full database before commit.
        c.execute('PRAGMA foreign_keys=OFF')
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM app_settings WHERE key=?', (VERSION_KEY,)).fetchone():
            c.rollback()
            return
        mappings = [dict(r) for r in c.execute('''SELECT m.*,a.currency_code,cur.name AS currency_name
            FROM term_deposit_accounts m JOIN accounts a ON a.id=m.source_account_id
            JOIN currencies cur ON cur.code=a.currency_code ORDER BY m.source_account_id''')]
        accounts = {r['id']: dict(r) for r in c.execute('''SELECT a.* FROM accounts a
            WHERE a.id IN (SELECT term_account_id FROM term_deposit_accounts)''')}
        deposits = [dict(r) for r in c.execute('SELECT * FROM term_deposits ORDER BY id')]
        original = ledger_signature(c)
        if mappings and backup_path is None:
            raise RuntimeError('定存归集升级缺少迁移前备份，已停止迁移。')
        source_mapping = {m['source_account_id']: m for m in mappings}
        by_currency = {}
        for mapping in mappings:
            account = accounts[mapping['term_account_id']]
            if (account['currency_code'] != mapping['currency_code'] or account['account_class'] != 'asset'
                    or account['account_type'] != 'bank_term' or account['opening_balance_minor'] != 0):
                raise RuntimeError('旧定存账户币种、类型或期初异常，已停止归集，请先核对。')
            by_currency.setdefault(mapping['currency_code'], []).append(mapping)
        from app.term_deposits import snapshot
        before = {d['id']: snapshot(c, d['id']) for d in deposits}
        transaction_ids = set()
        for deposit in deposits:
            mapping = source_mapping[deposit['source_account_id']]
            opening = c.execute('SELECT * FROM transactions WHERE id=?', (deposit['opening_transaction_id'],)).fetchone()
            if (opening is None or opening['transaction_type'] != 'transfer'
                    or opening['source_account_id'] != deposit['source_account_id']
                    or opening['destination_account_id'] != mapping['term_account_id']
                    or opening['outflow_minor'] != deposit['principal_minor']
                    or opening['inflow_minor'] != deposit['principal_minor']):
                raise RuntimeError('定存合同与本金存入流水不一致，已停止归集，请先核对。')
            transaction_ids.add(opening['id'])
            if deposit['return_transaction_id'] is not None:
                returned = c.execute('SELECT * FROM transactions WHERE id=?', (deposit['return_transaction_id'],)).fetchone()
                if (returned is None or returned['transaction_type'] != 'transfer'
                        or returned['source_account_id'] != mapping['term_account_id']
                        or returned['outflow_minor'] != deposit['principal_minor']
                        or returned['inflow_minor'] != deposit['principal_minor']):
                    raise RuntimeError('定存合同与本金回流流水不一致，已停止归集，请先核对。')
                transaction_ids.add(returned['id'])
        # Unknown adjustments/transfers cannot silently be folded into a new pool.
        for account_id in accounts:
            if any(r['id'] not in transaction_ids for r in c.execute('''SELECT id FROM transactions
                    WHERE source_account_id=? OR destination_account_id=?''', (account_id, account_id))):
                raise RuntimeError('旧定存账户有合同以外的流水，已停止归集，请先核对。')
        rebuild = unique_target(c)
        if rebuild:
            c.execute('''CREATE TABLE term_deposit_accounts_shared (
                source_account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE RESTRICT,
                term_account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT)''')
        canonical = {}
        for code, group in by_currency.items():
            account_id = min(m['term_account_id'] for m in group)
            canonical[code] = account_id
            currency_name = group[0]['currency_name']
            account_code = f'TERM-{code}'
            while c.execute('SELECT 1 FROM accounts WHERE code=? AND id!=?', (account_code, account_id)).fetchone():
                account_code += '-D'
            earliest = min(accounts[m['term_account_id']]['opening_balance_date'] for m in group)
            c.execute('''UPDATE accounts SET code=?,name=?,institution='定期存款',directory_key='term_deposit',
                opening_balance_date=?,is_active=1,updated_at=CURRENT_TIMESTAMP,
                note='各银行同币种定存统一归集，由定期存款页面维护' WHERE id=?''',
                (account_code, f'定期存款（{currency_name}）', earliest, account_id))
            c.execute('''INSERT INTO term_deposit_pools VALUES (?,?) ON CONFLICT(currency_code)
                DO UPDATE SET term_account_id=excluded.term_account_id''', (code, account_id))
            for mapping in group:
                if rebuild:
                    c.execute('INSERT INTO term_deposit_accounts_shared VALUES (?,?)', (mapping['source_account_id'], account_id))
                else:
                    c.execute('UPDATE term_deposit_accounts SET term_account_id=? WHERE source_account_id=?',
                              (account_id, mapping['source_account_id']))
            for old_id in {m['term_account_id'] for m in group} - {account_id}:
                c.execute('''UPDATE accounts SET is_active=0,updated_at=CURRENT_TIMESTAMP,
                    note=COALESCE(note,'') || ? WHERE id=?''',
                    (f'；已归集至定期存款（{currency_name}）账户 #{account_id}', old_id))
        if rebuild:
            c.execute('DROP TABLE term_deposit_accounts')
            c.execute('ALTER TABLE term_deposit_accounts_shared RENAME TO term_deposit_accounts')
        for deposit in deposits:
            target = canonical[source_mapping[deposit['source_account_id']]['currency_code']]
            c.execute('UPDATE transactions SET destination_account_id=? WHERE id=?',
                      (target, deposit['opening_transaction_id']))
            if deposit['return_transaction_id'] is not None:
                c.execute('UPDATE transactions SET source_account_id=? WHERE id=?',
                          (target, deposit['return_transaction_id']))
            c.execute('UPDATE term_deposits SET version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?', (deposit['id'],))
            c.execute('''INSERT INTO change_audit_logs(entity_type,entity_id,action,reason,before_json,after_json,backup_file)
                VALUES ('term_deposit',?,'update','按币种归集公共定存账户',?,?,?)''',
                (deposit['id'], json.dumps(before[deposit['id']], ensure_ascii=False),
                 json.dumps(snapshot(c, deposit['id']), ensure_ascii=False), backup_path.name))
        if ledger_signature(c) != original:
            raise RuntimeError('定存归集前后资金核对失败，迁移已回滚。')
        if mappings:
            c.execute('''INSERT INTO change_audit_logs(entity_type,action,reason,before_json,after_json,backup_file)
                VALUES ('term_deposit','update','按币种归集公共定存账户',?,?,?)''',
                (json.dumps(dict(mappings=mappings, accounts=list(accounts.values())), ensure_ascii=False),
                 json.dumps(dict(pools=canonical,
                     mappings=[dict(r) for r in c.execute('SELECT * FROM term_deposit_accounts')],
                     accounts=[dict(c.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone())
                               for account_id in sorted(accounts)]), ensure_ascii=False),
                 backup_path.name))
        if c.execute('PRAGMA foreign_key_check').fetchall() or c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('定存归集后的数据库校验失败，迁移已回滚。')
        c.execute('INSERT INTO app_settings(key,value) VALUES (?,?)', (VERSION_KEY, '1'))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.execute('PRAGMA foreign_keys=ON')
        c.close()

"""Idempotent migration of the per-budget storage catalog to a shared tree."""


def migrate_shared_storage(c):
    if 'budget_system_id' not in {r['name'] for r in c.execute('PRAGMA table_info(budget_storage_methods)')}:
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS budget_storage_shared_name ON budget_storage_methods(COALESCE(parent_id,0),name)')
        return
    # Called after schema scripts, before any other migration starts a transaction.
    c.execute('PRAGMA foreign_keys=OFF')
    try:
        c.execute('BEGIN IMMEDIATE')
        rows = [dict(r) for r in c.execute('SELECT * FROM budget_storage_methods ORDER BY id')]
        systems = {r['id']:r['name'] for r in c.execute('SELECT id,name FROM budget_systems')}
        c.execute('''CREATE TABLE budget_storage_methods_shared (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER REFERENCES budget_storage_methods(id) ON DELETE RESTRICT,
            name TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
            is_placeholder INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '')''')
        remaining = list(rows)
        mapping, identities, names = {}, {}, set()
        while remaining:
            ready = [r for r in remaining if r['parent_id'] is None or r['parent_id'] in mapping]
            if not ready:
                raise ValueError('旧存储栏目存在循环或缺失父项，迁移已回滚。')
            for row in ready:
                parent = mapping.get(row['parent_id'])
                identity = (parent,row['name'],row['is_active'],row['is_placeholder'],row['note'])
                canonical = identities.get(identity)
                if canonical is None:
                    canonical = row['id']
                    name = row['name']
                    if (parent,name) in names:
                        name = f"{name}（{systems[row['budget_system_id']]}）"
                    base, suffix = name, 1
                    while (parent,name) in names:
                        suffix += 1
                        name = f'{base} {suffix}'
                    names.add((parent,name)); identities[identity] = canonical
                    c.execute('INSERT INTO budget_storage_methods_shared VALUES (?,?,?,?,?,?,?)',
                              (canonical,parent,name,row['sort_order'],row['is_active'],row['is_placeholder'],row['note']))
                mapping[row['id']] = canonical
                remaining.remove(row)
        for old_id,canonical in mapping.items():
            c.execute('UPDATE budget_plan_items SET storage_method_id=? WHERE storage_method_id=?', (canonical,old_id))
            c.execute('INSERT INTO budget_storage_aliases(old_id,storage_id) VALUES (?,?)', (old_id,canonical))
        c.execute('DROP TABLE budget_storage_methods')
        c.execute('ALTER TABLE budget_storage_methods_shared RENAME TO budget_storage_methods')
        c.execute('CREATE UNIQUE INDEX budget_storage_shared_name ON budget_storage_methods(COALESCE(parent_id,0),name)')
        if rows:
            c.execute("UPDATE sqlite_sequence SET seq=? WHERE name='budget_storage_methods'", (max(r['id'] for r in rows),))
        c.execute("UPDATE app_settings SET value=CAST(value AS INTEGER)+1 WHERE key='budget_config_version'")
        if c.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('公共存储迁移的引用校验失败，已回滚。')
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.execute('PRAGMA foreign_keys=ON')


def migrate_unallocated_storage(c):
    """Replace persisted storage placeholders with the derived unallocated balance."""
    placeholder_ids = [
        row['id']
        for row in c.execute(
            'SELECT id FROM budget_storage_methods WHERE is_placeholder=1 ORDER BY id'
        )
    ]
    if not placeholder_ids:
        return

    marks = ','.join('?' for _ in placeholder_ids)
    c.execute('BEGIN IMMEDIATE')
    try:
        affected_plans = [
            row['plan_id']
            for row in c.execute(
                f'''SELECT DISTINCT plan_id FROM budget_plan_items
                    WHERE storage_method_id IN ({marks})''',
                placeholder_ids,
            )
        ]
        for plan_id in affected_plans:
            plan = c.execute(
                'SELECT total_minor FROM budget_plans WHERE id=?', (plan_id,)
            ).fetchone()
            if plan['total_minor'] is None:
                inferred_total = c.execute(
                    'SELECT COALESCE(SUM(amount_minor),0) FROM budget_plan_items WHERE plan_id=?',
                    (plan_id,),
                ).fetchone()[0]
                c.execute(
                    'UPDATE budget_plans SET total_minor=? WHERE id=?',
                    (inferred_total, plan_id),
                )

        c.execute(
            f'DELETE FROM budget_storage_defaults WHERE storage_method_id IN ({marks})',
            placeholder_ids,
        )
        c.execute(
            f'DELETE FROM budget_storage_expenses WHERE storage_method_id IN ({marks})',
            placeholder_ids,
        )

        transfers = [
            dict(row)
            for row in c.execute(
                f'''SELECT * FROM budget_storage_transfers
                    WHERE source_id IN ({marks}) OR destination_id IN ({marks})''',
                placeholder_ids * 2,
            )
        ]
        placeholder_set = set(placeholder_ids)
        for transfer in transfers:
            source_id = None if transfer['source_id'] in placeholder_set else transfer['source_id']
            destination_id = None if transfer['destination_id'] in placeholder_set else transfer['destination_id']
            if source_id == destination_id:
                c.execute('DELETE FROM budget_storage_transfers WHERE id=?', (transfer['id'],))
            else:
                c.execute(
                    '''UPDATE budget_storage_transfers
                       SET source_id=?,destination_id=?,version=version+1
                       WHERE id=?''',
                    (source_id, destination_id, transfer['id']),
                )

        children = [
            dict(row)
            for row in c.execute(
                f'''SELECT id,name FROM budget_storage_methods
                    WHERE parent_id IN ({marks}) ORDER BY id''',
                placeholder_ids,
            )
        ]
        root_names = {
            row['name']
            for row in c.execute(
                f'''SELECT name FROM budget_storage_methods
                    WHERE parent_id IS NULL AND id NOT IN ({marks})''',
                placeholder_ids,
            )
        }
        for child in children:
            base = name = child['name']
            suffix = 1
            while name in root_names:
                suffix += 1
                name = f'{base} {suffix}'
            root_names.add(name)
            c.execute(
                'UPDATE budget_storage_methods SET parent_id=NULL,name=? WHERE id=?',
                (name, child['id']),
            )

        c.execute(
            f'DELETE FROM budget_plan_items WHERE storage_method_id IN ({marks})',
            placeholder_ids,
        )
        if affected_plans:
            plan_marks = ','.join('?' for _ in affected_plans)
            c.execute(
                f'''UPDATE budget_plans
                    SET version=version+1,updated_at=CURRENT_TIMESTAMP
                    WHERE id IN ({plan_marks})''',
                affected_plans,
            )
        c.execute(
            f'DELETE FROM budget_storage_methods WHERE id IN ({marks})',
            placeholder_ids,
        )
        c.execute(
            '''INSERT INTO app_settings(key,value) VALUES ('budget_config_version','2')
               ON CONFLICT(key) DO UPDATE SET
                   value=CAST(value AS INTEGER)+1,
                   updated_at=CURRENT_TIMESTAMP'''
        )
        if c.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('待定存储迁移的引用校验失败，已回滚。')
        c.commit()
    except Exception:
        c.rollback()
        raise

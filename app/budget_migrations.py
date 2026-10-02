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

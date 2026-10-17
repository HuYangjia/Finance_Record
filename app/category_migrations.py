"""Idempotent migrations for built-in income and expense categories."""


def migrate_daily_shopping_to_groceries(connection):
    old = connection.execute(
        """SELECT * FROM categories
           WHERE kind='expense' AND parent_id IS NULL AND name='日常购物'"""
    ).fetchone()
    if old is None:
        return

    connection.execute('BEGIN IMMEDIATE')
    try:
        groceries = connection.execute(
            """SELECT * FROM categories
               WHERE kind='expense' AND parent_id IS NULL AND name='食品杂货'"""
        ).fetchone()
        if groceries is None:
            groceries_id = connection.execute(
                """INSERT INTO categories(kind,name,sort_order)
                   VALUES ('expense','食品杂货',25)"""
            ).lastrowid
        else:
            groceries_id = groceries['id']
        old_id = old['id']

        connection.execute(
            'UPDATE transactions SET category_id=?,updated_at=CURRENT_TIMESTAMP WHERE category_id=?',
            (groceries_id, old_id),
        )
        connection.execute(
            '''UPDATE transaction_offset_groups
               SET category_id=?,version=version+1,updated_at=CURRENT_TIMESTAMP
               WHERE category_id=?''',
            (groceries_id, old_id),
        )

        for row in connection.execute(
            'SELECT * FROM monthly_budgets WHERE category_id=? ORDER BY id', (old_id,)
        ).fetchall():
            existing = connection.execute(
                '''SELECT * FROM monthly_budgets
                   WHERE budget_month=? AND category_id=? AND currency_code=?''',
                (row['budget_month'], groceries_id, row['currency_code']),
            ).fetchone()
            if existing:
                notes = [value.strip() for value in (existing['note'] or '', row['note'] or '') if value.strip()]
                connection.execute(
                    '''UPDATE monthly_budgets
                       SET amount_minor=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                    (existing['amount_minor'] + row['amount_minor'], '；'.join(dict.fromkeys(notes)) or None,
                     existing['id']),
                )
                connection.execute('DELETE FROM monthly_budgets WHERE id=?', (row['id'],))
            else:
                connection.execute(
                    '''UPDATE monthly_budgets
                       SET category_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                    (groceries_id, row['id']),
                )

        for row in connection.execute(
            'SELECT * FROM daily_category_frequent_activity WHERE category_id=?', (old_id,)
        ).fetchall():
            existing = connection.execute(
                '''SELECT * FROM daily_category_frequent_activity
                   WHERE snapshot_date=? AND category_id=?''',
                (row['snapshot_date'], groceries_id),
            ).fetchone()
            if existing:
                connection.execute(
                    '''UPDATE daily_category_frequent_activity
                       SET record_count=?,last_transaction_date=?
                       WHERE snapshot_date=? AND category_id=?''',
                    (existing['record_count'] + row['record_count'],
                     max(existing['last_transaction_date'], row['last_transaction_date']),
                     row['snapshot_date'], groceries_id),
                )
                connection.execute(
                    '''DELETE FROM daily_category_frequent_activity
                       WHERE snapshot_date=? AND category_id=?''',
                    (row['snapshot_date'], old_id),
                )
            else:
                connection.execute(
                    '''UPDATE daily_category_frequent_activity SET category_id=?
                       WHERE snapshot_date=? AND category_id=?''',
                    (groceries_id, row['snapshot_date'], old_id),
                )

        for row in connection.execute(
            'SELECT * FROM budget_assignment_rules WHERE category_id=?', (old_id,)
        ).fetchall():
            existing = connection.execute(
                'SELECT 1 FROM budget_assignment_rules WHERE mode=? AND category_id=?',
                (row['mode'], groceries_id),
            ).fetchone()
            if existing:
                connection.execute('DELETE FROM budget_assignment_rules WHERE id=?', (row['id'],))
            else:
                connection.execute(
                    'UPDATE budget_assignment_rules SET category_id=? WHERE id=?',
                    (groceries_id, row['id']),
                )
        connection.execute(
            'UPDATE budget_entry_presets SET category_id=? WHERE category_id=?',
            (groceries_id, old_id),
        )

        root_names = {
            row['name']
            for row in connection.execute(
                """SELECT name FROM categories
                   WHERE kind='expense' AND parent_id IS NULL AND id<>?""",
                (old_id,),
            )
        }
        children = connection.execute(
            'SELECT id,name FROM categories WHERE parent_id=? ORDER BY id', (old_id,)
        ).fetchall()
        for child in children:
            base = name = child['name']
            suffix = 1
            while name in root_names:
                suffix += 1
                name = f'{base} {suffix}'
            root_names.add(name)
            connection.execute(
                'UPDATE categories SET parent_id=NULL,name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                (name, child['id']),
            )

        connection.execute('DELETE FROM categories WHERE id=?', (old_id,))
        connection.execute(
            '''INSERT INTO app_settings(key,value) VALUES ('budget_config_version','2')
               ON CONFLICT(key) DO UPDATE SET
                   value=CAST(value AS INTEGER)+1,
                   updated_at=CURRENT_TIMESTAMP'''
        )
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('日常购物分类合并的引用校验失败，已回滚。')
        connection.commit()
    except Exception:
        connection.rollback()
        raise

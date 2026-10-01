PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS currencies (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    symbol TEXT,
    decimal_places INTEGER NOT NULL DEFAULT 2
        CHECK (decimal_places BETWEEN 0 AND 4),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    account_class TEXT NOT NULL CHECK (account_class IN ('asset', 'liability')),
    account_type TEXT NOT NULL,
    currency_code TEXT NOT NULL REFERENCES currencies(code) ON UPDATE CASCADE,
    institution TEXT,
    opening_balance_minor INTEGER NOT NULL DEFAULT 0 CHECK (opening_balance_minor >= 0),
    opening_balance_date TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    parent_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS categories_root_unique
    ON categories(kind, name) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS categories_child_unique
    ON categories(kind, parent_id, name) WHERE parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    budget_system_id INTEGER REFERENCES budget_systems(id) ON DELETE RESTRICT,
    transaction_date TEXT NOT NULL,
    transaction_type TEXT NOT NULL CHECK (
        transaction_type IN (
            'income',
            'expense',
            'transfer',
            'fx_exchange',
            'broker_deposit',
            'broker_withdrawal',
            'adjustment'
        )
    ),
    source_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
    destination_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
    outflow_minor INTEGER CHECK (outflow_minor > 0),
    inflow_minor INTEGER CHECK (inflow_minor > 0),
    category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
    counterparty TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        source_account_id IS NOT NULL
        OR destination_account_id IS NOT NULL
    ),
    CHECK (
        source_account_id IS NULL
        OR destination_account_id IS NULL
        OR source_account_id != destination_account_id
    ),
    CHECK (
        (
            transaction_type = 'income'
            AND source_account_id IS NULL
            AND destination_account_id IS NOT NULL
            AND outflow_minor IS NULL
            AND inflow_minor IS NOT NULL
            AND category_id IS NOT NULL
        )
        OR (
            transaction_type = 'expense'
            AND source_account_id IS NOT NULL
            AND destination_account_id IS NULL
            AND outflow_minor IS NOT NULL
            AND inflow_minor IS NULL
            AND category_id IS NOT NULL
        )
        OR (
            transaction_type IN ('transfer', 'fx_exchange', 'broker_deposit', 'broker_withdrawal')
            AND source_account_id IS NOT NULL
            AND destination_account_id IS NOT NULL
            AND outflow_minor IS NOT NULL
            AND inflow_minor IS NOT NULL
            AND category_id IS NULL
        )
        OR (
            transaction_type = 'adjustment'
            AND category_id IS NULL
            AND (
                (
                    source_account_id IS NOT NULL
                    AND destination_account_id IS NULL
                    AND outflow_minor IS NOT NULL
                    AND inflow_minor IS NULL
                )
                OR (
                    source_account_id IS NULL
                    AND destination_account_id IS NOT NULL
                    AND outflow_minor IS NULL
                    AND inflow_minor IS NOT NULL
                )
            )
        )
    )
);

CREATE INDEX IF NOT EXISTS transactions_date_index
    ON transactions(transaction_date DESC);
CREATE INDEX IF NOT EXISTS transactions_source_index
    ON transactions(source_account_id);
CREATE INDEX IF NOT EXISTS transactions_destination_index
    ON transactions(destination_account_id);
CREATE INDEX IF NOT EXISTS transactions_category_index
    ON transactions(category_id);

CREATE TABLE IF NOT EXISTS transaction_flags (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL UNIQUE
        REFERENCES transactions(id) ON DELETE CASCADE,
    flag_type TEXT NOT NULL CHECK (
        flag_type IN (
            'suspected_duplicate',
            'missing_information',
            'suspicious_amount',
            'other'
        )
    ),
    note TEXT NOT NULL CHECK (length(trim(note)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS transaction_flags_type_index
    ON transaction_flags(flag_type);

CREATE TABLE IF NOT EXISTS change_audit_logs (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (
        entity_type IN (
            'transaction',
            'account',
            'transaction_flag',
            'reconciliation',
            'investment_valuation',
            'exchange_rate',
            'offset_group',
            'budget'
        )
    ),
    entity_id INTEGER,
    action TEXT NOT NULL CHECK (
        action IN ('create', 'update', 'delete', 'flag', 'unflag', 'reconcile')
    ),
    reason TEXT,
    before_json TEXT,
    after_json TEXT,
    backup_file TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS change_audit_logs_entity_index
    ON change_audit_logs(entity_type, entity_id, created_at DESC);

CREATE TABLE IF NOT EXISTS monthly_budgets (
    id INTEGER PRIMARY KEY,
    budget_month TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    currency_code TEXT NOT NULL REFERENCES currencies(code) ON UPDATE CASCADE,
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (budget_month, category_id, currency_code)
);

CREATE TABLE IF NOT EXISTS account_reconciliations (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    reconciliation_date TEXT NOT NULL,
    actual_balance_minor INTEGER NOT NULL CHECK (actual_balance_minor >= 0),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, reconciliation_date)
);

CREATE TABLE IF NOT EXISTS investment_snapshots (
    id INTEGER PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    platform TEXT NOT NULL,
    currency_code TEXT NOT NULL REFERENCES currencies(code) ON UPDATE CASCADE,
    cash_minor INTEGER NOT NULL DEFAULT 0 CHECK (cash_minor >= 0),
    market_value_minor INTEGER NOT NULL DEFAULT 0 CHECK (market_value_minor >= 0),
    other_assets_minor INTEGER NOT NULL DEFAULT 0 CHECK (other_assets_minor >= 0),
    deposit_minor INTEGER NOT NULL DEFAULT 0 CHECK (deposit_minor >= 0),
    withdrawal_minor INTEGER NOT NULL DEFAULT 0 CHECK (withdrawal_minor >= 0),
    realized_profit_minor INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS investment_snapshots_date_index
    ON investment_snapshots(snapshot_date DESC);

CREATE TABLE IF NOT EXISTS investment_valuations (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    valuation_date TEXT NOT NULL,
    total_value_minor INTEGER NOT NULL CHECK (total_value_minor >= 0),
    generated_transaction_id INTEGER UNIQUE
        REFERENCES transactions(id) ON DELETE SET NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, valuation_date)
);

CREATE INDEX IF NOT EXISTS investment_valuations_account_date_index
    ON investment_valuations(account_id, valuation_date DESC);

CREATE TABLE IF NOT EXISTS daily_cny_exchange_rates (
    id INTEGER PRIMARY KEY,
    rate_date TEXT NOT NULL UNIQUE,
    usd_cny_rate TEXT NOT NULL CHECK (length(trim(usd_cny_rate)) > 0),
    hkd_cny_rate TEXT NOT NULL CHECK (length(trim(hkd_cny_rate)) > 0),
    source TEXT NOT NULL,
    quoted_at TEXT,
    market_date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS daily_cny_exchange_rates_date_index
    ON daily_cny_exchange_rates(rate_date DESC);

CREATE TABLE IF NOT EXISTS daily_frequent_snapshots (
    snapshot_date TEXT PRIMARY KEY,
    rules_version INTEGER NOT NULL DEFAULT 2,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_account_frequent_activity (
    snapshot_date TEXT NOT NULL
        REFERENCES daily_frequent_snapshots(snapshot_date) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    activity_key TEXT NOT NULL CHECK (
        activity_key IN (
            'overall',
            'expense_source',
            'income_destination',
            'movement_source',
            'movement_destination'
        )
    ),
    record_count INTEGER NOT NULL CHECK (record_count > 0),
    recent_record_count INTEGER NOT NULL DEFAULT 0 CHECK (recent_record_count >= 0),
    last_transaction_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, account_id, activity_key)
);

CREATE INDEX IF NOT EXISTS daily_account_frequent_activity_date_index
    ON daily_account_frequent_activity(snapshot_date DESC, account_id);

CREATE TABLE IF NOT EXISTS daily_category_frequent_activity (
    snapshot_date TEXT NOT NULL
        REFERENCES daily_frequent_snapshots(snapshot_date) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    record_count INTEGER NOT NULL CHECK (record_count > 0),
    last_transaction_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, category_id)
);

CREATE INDEX IF NOT EXISTS daily_category_frequent_activity_date_index
    ON daily_category_frequent_activity(snapshot_date DESC, category_id);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS validate_transaction_category_before_insert
BEFORE INSERT ON transactions
FOR EACH ROW
WHEN NEW.category_id IS NOT NULL
    AND (
        (NEW.transaction_type = 'income' AND (
            SELECT kind FROM categories WHERE id = NEW.category_id
        ) != 'income')
        OR
        (NEW.transaction_type = 'expense' AND (
            SELECT kind FROM categories WHERE id = NEW.category_id
        ) != 'expense')
    )
BEGIN
    SELECT RAISE(ABORT, 'Transaction category does not match transaction type');
END;

CREATE TRIGGER IF NOT EXISTS validate_transaction_category_before_update
BEFORE UPDATE OF transaction_type, category_id ON transactions
FOR EACH ROW
WHEN NEW.category_id IS NOT NULL
    AND (
        (NEW.transaction_type = 'income' AND (
            SELECT kind FROM categories WHERE id = NEW.category_id
        ) != 'income')
        OR
        (NEW.transaction_type = 'expense' AND (
            SELECT kind FROM categories WHERE id = NEW.category_id
        ) != 'expense')
    )
BEGIN
    SELECT RAISE(ABORT, 'Transaction category does not match transaction type');
END;

CREATE TRIGGER IF NOT EXISTS validate_budget_category_before_insert
BEFORE INSERT ON monthly_budgets
FOR EACH ROW
WHEN (SELECT kind FROM categories WHERE id = NEW.category_id) != 'expense'
BEGIN
    SELECT RAISE(ABORT, 'Budget category must be an expense category');
END;

CREATE TRIGGER IF NOT EXISTS validate_budget_category_before_update
BEFORE UPDATE OF category_id ON monthly_budgets
FOR EACH ROW
WHEN (SELECT kind FROM categories WHERE id = NEW.category_id) != 'expense'
BEGIN
    SELECT RAISE(ABORT, 'Budget category must be an expense category');
END;

CREATE TRIGGER IF NOT EXISTS accounts_set_updated_at
AFTER UPDATE ON accounts
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE accounts SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS categories_set_updated_at
AFTER UPDATE ON categories
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE categories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS transactions_set_updated_at
AFTER UPDATE ON transactions
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE transactions SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS transaction_flags_set_updated_at
AFTER UPDATE ON transaction_flags
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE transaction_flags SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS investment_valuations_set_updated_at
AFTER UPDATE ON investment_valuations
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE investment_valuations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS daily_cny_exchange_rates_set_updated_at
AFTER UPDATE ON daily_cny_exchange_rates
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE daily_cny_exchange_rates SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS monthly_budgets_set_updated_at
AFTER UPDATE ON monthly_budgets
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE monthly_budgets SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE VIEW IF NOT EXISTS account_balances AS
WITH movements AS (
    SELECT
        destination_account_id AS account_id,
        inflow_minor AS inflow_minor,
        0 AS outflow_minor
    FROM transactions
    WHERE destination_account_id IS NOT NULL

    UNION ALL

    SELECT
        source_account_id AS account_id,
        0 AS inflow_minor,
        outflow_minor AS outflow_minor
    FROM transactions
    WHERE source_account_id IS NOT NULL
),
movement_totals AS (
    SELECT
        account_id,
        SUM(inflow_minor) AS total_inflow_minor,
        SUM(outflow_minor) AS total_outflow_minor
    FROM movements
    GROUP BY account_id
)
SELECT
    accounts.id,
    accounts.code,
    accounts.name,
    accounts.account_class,
    accounts.account_type,
    accounts.currency_code,
    accounts.institution,
    accounts.opening_balance_minor,
    accounts.opening_balance_date,
    accounts.is_active,
    accounts.note,
    COALESCE(movement_totals.total_inflow_minor, 0) AS total_inflow_minor,
    COALESCE(movement_totals.total_outflow_minor, 0) AS total_outflow_minor,
    CASE accounts.account_class
        WHEN 'asset' THEN
            accounts.opening_balance_minor
            + COALESCE(movement_totals.total_inflow_minor, 0)
            - COALESCE(movement_totals.total_outflow_minor, 0)
        WHEN 'liability' THEN
            accounts.opening_balance_minor
            + COALESCE(movement_totals.total_outflow_minor, 0)
            - COALESCE(movement_totals.total_inflow_minor, 0)
    END AS balance_minor
FROM accounts
LEFT JOIN movement_totals ON movement_totals.account_id = accounts.id;

CREATE TABLE IF NOT EXISTS transaction_offset_groups (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 120),
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    anchor_transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    statistics_date TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    note TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS offset_groups_date_index ON transaction_offset_groups(statistics_date, id);
CREATE INDEX IF NOT EXISTS offset_groups_category_index ON transaction_offset_groups(kind, category_id, statistics_date);
CREATE TABLE IF NOT EXISTS transaction_offset_members (
    transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE RESTRICT,
    group_id INTEGER NOT NULL REFERENCES transaction_offset_groups(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS offset_members_group_index ON transaction_offset_members(group_id);

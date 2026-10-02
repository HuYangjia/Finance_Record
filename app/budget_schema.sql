CREATE TABLE IF NOT EXISTS budget_systems (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    note TEXT NOT NULL DEFAULT '',
    currency_code TEXT REFERENCES currencies(code),
    execution_start_month TEXT,
    opening_minor INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS budget_storage_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES budget_storage_methods(id) ON DELETE RESTRICT,
    name TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    is_placeholder INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT ''
);
-- The global name index is installed after migration of the legacy per-system catalog.
CREATE TABLE IF NOT EXISTS budget_storage_aliases (
    old_id INTEGER PRIMARY KEY,
    storage_id INTEGER NOT NULL REFERENCES budget_storage_methods(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS budget_purposes (
    id INTEGER PRIMARY KEY, budget_system_id INTEGER NOT NULL REFERENCES budget_systems(id) ON DELETE RESTRICT,
    parent_id INTEGER REFERENCES budget_purposes(id) ON DELETE RESTRICT,
    name TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    is_placeholder INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS budget_purpose_name ON budget_purposes(budget_system_id, COALESCE(parent_id,0), name);
CREATE TABLE IF NOT EXISTS budget_plans (
    id INTEGER PRIMARY KEY, budget_system_id INTEGER NOT NULL REFERENCES budget_systems(id) ON DELETE RESTRICT,
    month TEXT NOT NULL, currency_code TEXT NOT NULL REFERENCES currencies(code),
    total_minor INTEGER CHECK(total_minor >= 0), version INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(budget_system_id,month,currency_code)
);
CREATE TABLE IF NOT EXISTS budget_plan_items (
    id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES budget_plans(id) ON DELETE CASCADE,
    storage_method_id INTEGER NOT NULL REFERENCES budget_storage_methods(id) ON DELETE RESTRICT,
    purpose_id INTEGER NOT NULL REFERENCES budget_purposes(id) ON DELETE RESTRICT,
    title TEXT NOT NULL DEFAULT '', amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
    sort_order INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS budget_plan_revisions (
    id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES budget_plans(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL, snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(plan_id,version)
);
CREATE TABLE IF NOT EXISTS budget_assignment_rules (
    id INTEGER PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('expense','income')),
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    budget_system_id INTEGER NOT NULL REFERENCES budget_systems(id) ON DELETE RESTRICT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)), UNIQUE(mode,category_id)
);
CREATE TABLE IF NOT EXISTS budget_entry_presets (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK(mode IN ('expense','income')),
    budget_system_id INTEGER NOT NULL REFERENCES budget_systems(id) ON DELETE RESTRICT,
    category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
    account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1))
);

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
BACKUP_DIR = ROOT_DIR / "backups"
DATABASE_PATH = DATA_DIR / "finance.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
BUDGET_SCHEMA_PATH = Path(__file__).with_name("budget_schema.sql")

CURRENCY_SEED = (
    ("CNY", "人民币", "¥", 2),
    ("HKD", "港币", "HK$", 2),
    ("USD", "美元", "$", 2),
    ("EUR", "欧元", "€", 2),
    ("JPY", "日元", "¥", 0),
)

CATEGORY_SEED = (
    ("income", "奖学金/科研资助", 10),
    ("income", "工资/劳务", 20),
    ("income", "父母支持", 30),
    ("income", "利息/股息", 40),
    ("income", "回赠", 45),
    ("income", "报销/返还", 50),
    ("income", "其他收入", 90),
    ("expense", "住房", 10),
    ("expense", "餐饮", 20),
    ("expense", "食品杂货", 25),
    ("expense", "交通", 30),
    ("expense", "学习与科研", 40),
    ("expense", "医疗与保险", 50),
    ("expense", "通信与订阅", 60),
    ("expense", "日常购物", 70),
    ("expense", "社交与娱乐", 80),
    ("expense", "家庭与人情", 90),
    ("expense", "工资/劳务支出", 95),
    ("expense", "金融费用", 100),
    ("expense", "其他", 110),
)


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    if DATABASE_PATH.exists():
        with sqlite3.connect(DATABASE_PATH) as existing:
            tables = {row[0] for row in existing.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if 'budget_plans' in tables and 'budget_storage_transfers' not in tables:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(BACKUP_DIR / f"finance-pre-storage-balances-{datetime.now():%Y%m%d-%H%M%S-%f}.db") as destination:
                    existing.backup(destination)
            if 'budget_plans' in tables and 'budget_plan_funding' not in tables:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(BACKUP_DIR / f"finance-pre-budget-funding-{datetime.now():%Y%m%d-%H%M%S-%f}.db") as destination:
                    existing.backup(destination)
            if 'budget_systems' in tables and 'execution_start_month' not in {r[1] for r in existing.execute('PRAGMA table_info(budget_systems)')}:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(BACKUP_DIR / f"finance-pre-budget-execution-{datetime.now():%Y%m%d-%H%M%S-%f}.db") as destination:
                    existing.backup(destination)
            if 'budget_storage_methods' in tables and 'budget_system_id' in {r[1] for r in existing.execute('PRAGMA table_info(budget_storage_methods)')}:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(BACKUP_DIR / f"finance-pre-shared-storage-{datetime.now():%Y%m%d-%H%M%S-%f}.db") as destination:
                    existing.backup(destination)
            storage_columns = ({r[1] for r in existing.execute('PRAGMA table_info(budget_storage_methods)')}
                               if 'budget_storage_methods' in tables else set())
            if 'is_placeholder' in storage_columns and existing.execute(
                    'SELECT 1 FROM budget_storage_methods WHERE is_placeholder=1').fetchone():
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(BACKUP_DIR / f"finance-pre-unallocated-storage-{datetime.now():%Y%m%d-%H%M%S-%f}.db") as destination:
                    existing.backup(destination)
            if 'budget_systems' in tables and 'currency_code' not in {r[1] for r in existing.execute('PRAGMA table_info(budget_systems)')}:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(BACKUP_DIR / f"finance-pre-budget-currency-{datetime.now():%Y%m%d-%H%M%S-%f}.db") as destination:
                    existing.backup(destination)
            if "transactions" in tables and "budget_system_id" not in {r[1] for r in existing.execute('PRAGMA table_info(transactions)')}:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(BACKUP_DIR / f"finance-pre-budget-{datetime.now():%Y%m%d-%H%M%S-%f}.db") as destination:
                    existing.backup(destination)
            elif 'budget_systems' in tables and 'app_settings' in tables and not existing.execute("SELECT 1 FROM app_settings WHERE key='daily_expense_budget_system_id'").fetchone():
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(BACKUP_DIR / f"finance-pre-daily-budget-{datetime.now():%Y%m%d-%H%M%S-%f}.db") as destination:
                    existing.backup(destination)
            if "transactions" in tables and "transaction_offset_groups" not in tables:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                migration_backup = BACKUP_DIR / f"finance-pre-offset-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
                with sqlite3.connect(migration_backup) as destination:
                    existing.backup(destination)
    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(BUDGET_SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        from app.budget_migrations import migrate_shared_storage, migrate_unallocated_storage
        migrate_shared_storage(connection)
        migrate_unallocated_storage(connection)
        if 'execution_start_month' not in {r['name'] for r in connection.execute('PRAGMA table_info(budget_systems)')}:
            connection.execute('ALTER TABLE budget_systems ADD COLUMN execution_start_month TEXT')
            connection.execute('ALTER TABLE budget_systems ADD COLUMN opening_minor INTEGER NOT NULL DEFAULT 0')
            connection.execute("UPDATE app_settings SET value=CAST(value AS INTEGER)+1 WHERE key='budget_config_version'")
        if 'currency_code' not in {r['name'] for r in connection.execute('PRAGMA table_info(budget_systems)')}:
            connection.execute('ALTER TABLE budget_systems ADD COLUMN currency_code TEXT REFERENCES currencies(code)')
            connection.execute('''UPDATE budget_systems SET currency_code=(
                SELECT MIN(currency_code) FROM budget_plans WHERE budget_system_id=budget_systems.id
                GROUP BY budget_system_id HAVING COUNT(DISTINCT currency_code)=1)''')
            connection.execute("UPDATE app_settings SET value=CAST(value AS INTEGER)+1 WHERE key='budget_config_version'")
        if 'budget_system_id' not in {r['name'] for r in connection.execute('PRAGMA table_info(transactions)')}:
            connection.execute('ALTER TABLE transactions ADD COLUMN budget_system_id INTEGER REFERENCES budget_systems(id) ON DELETE RESTRICT')
        connection.execute('CREATE INDEX IF NOT EXISTS transactions_budget_index ON transactions(budget_system_id,transaction_date)')
        migrate_change_audit_logs(connection)
        migrate_daily_frequent_snapshot_schema(connection)
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.executemany(
            """
            INSERT OR IGNORE INTO currencies (code, name, symbol, decimal_places)
            VALUES (?, ?, ?, ?)
            """,
            CURRENCY_SEED,
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO categories (kind, name, sort_order)
            VALUES (?, ?, ?)
            """,
            CATEGORY_SEED,
        )
        if not connection.execute("SELECT 1 FROM app_settings WHERE key='budget_initialized'").fetchone():
            system_id = connection.execute("INSERT INTO budget_systems(name) VALUES ('日常预算')").lastrowid
            for table, names in [('budget_storage_methods', ['活期','定期','ETF','股票']),
                                 ('budget_purposes', ['待定用途','日常生活','慈善','留存'])]:
                for order, name in enumerate(names, start=1 if table == 'budget_storage_methods' else 0):
                    if table == 'budget_storage_methods':
                        connection.execute(f'INSERT INTO {table}(name,sort_order,is_placeholder) VALUES (?,?,0)', (name,order))
                    else:
                        connection.execute(f'INSERT INTO {table}(budget_system_id,name,sort_order,is_placeholder) VALUES (?,?,?,?)', (system_id,name,order,int(order==0)))
            connection.executemany('INSERT INTO app_settings(key,value) VALUES (?,?)', [
                ('budget_initialized','1'), ('budget_config_version','1'),
                ('default_expense_budget_system_id',str(system_id)), ('default_income_budget_system_id','null')])
        if not connection.execute("SELECT 1 FROM app_settings WHERE key='daily_expense_budget_system_id'").fetchone():
            daily = connection.execute("SELECT id FROM budget_systems WHERE name='日常预算' AND is_active=1").fetchone()
            if daily is None:
                daily = connection.execute("""SELECT b.id FROM budget_systems b JOIN app_settings s
                    ON b.id=CAST(s.value AS INTEGER) WHERE s.key='default_expense_budget_system_id' AND b.is_active=1""").fetchone()
            if daily is None:
                name, suffix = '日常预算', 1
                while connection.execute('SELECT 1 FROM budget_systems WHERE name=?', (name,)).fetchone():
                    suffix += 1
                    name = f'日常预算 {suffix}'
                daily_id = connection.execute('INSERT INTO budget_systems(name) VALUES (?)', (name,)).lastrowid
                for table, label in [('budget_purposes','待定用途')]:
                    connection.execute(f'INSERT INTO {table}(budget_system_id,name,is_placeholder) VALUES (?,?,1)', (daily_id,label))
            else:
                daily_id = daily['id']
            connection.execute("INSERT INTO app_settings(key,value) VALUES ('daily_expense_budget_system_id',?)", (str(daily_id),))
            connection.execute("UPDATE app_settings SET value=CAST(value AS INTEGER)+1 WHERE key='budget_config_version'")


def migrate_change_audit_logs(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'change_audit_logs'
        """
    ).fetchone()
    current_sql = (row["sql"] or "") if row is not None else ""
    if (
        row is None
        or (
            "investment_valuation" in current_sql
            and "exchange_rate" in current_sql
            and "offset_group" in current_sql
            and "'budget'" in current_sql
        )
    ):
        return

    connection.execute("DROP INDEX IF EXISTS change_audit_logs_entity_index")
    connection.execute("ALTER TABLE change_audit_logs RENAME TO change_audit_logs_legacy")
    connection.execute(
        """
        CREATE TABLE change_audit_logs (
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
        )
        """
    )
    connection.execute(
        """
        INSERT INTO change_audit_logs (
            id, entity_type, entity_id, action, reason,
            before_json, after_json, backup_file, created_at
        )
        SELECT
            id, entity_type, entity_id, action, reason,
            before_json, after_json, backup_file, created_at
        FROM change_audit_logs_legacy
        """
    )
    connection.execute("DROP TABLE change_audit_logs_legacy")


def migrate_daily_frequent_snapshot_schema(connection: sqlite3.Connection) -> None:
    snapshot_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(daily_frequent_snapshots)"
        ).fetchall()
    }
    if "rules_version" not in snapshot_columns:
        connection.execute(
            """
            ALTER TABLE daily_frequent_snapshots
            ADD COLUMN rules_version INTEGER NOT NULL DEFAULT 1
            """
        )

    account_activity_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(daily_account_frequent_activity)"
        ).fetchall()
    }
    if "recent_record_count" not in account_activity_columns:
        connection.execute(
            """
            ALTER TABLE daily_account_frequent_activity
            ADD COLUMN recent_record_count INTEGER NOT NULL DEFAULT 0
            """
        )


def create_backup() -> Path:
    initialize_database()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = BACKUP_DIR / f"finance-{timestamp}.db"

    with connect() as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)

    return backup_path

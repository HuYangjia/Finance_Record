from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
BACKUP_DIR = ROOT_DIR / "backups"
DATABASE_PATH = DATA_DIR / "finance.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

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
    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        migrate_change_audit_logs(connection)
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
                    'exchange_rate'
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


def create_backup() -> Path:
    initialize_database()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = BACKUP_DIR / f"finance-{timestamp}.db"

    with connect() as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)

    return backup_path

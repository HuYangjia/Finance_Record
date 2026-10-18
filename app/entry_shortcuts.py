from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import connect, create_backup


router = APIRouter()
VERSION_KEY = "entry_shortcut_version"


class ShortcutWrite(BaseModel):
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["expense", "income", "transfer"]
    source_account_id: int | None = Field(default=None, gt=0)
    destination_account_id: int | None = Field(default=None, gt=0)
    category_id: int | None = Field(default=None, gt=0)
    budget_system_id: int | None = Field(default=None, gt=0)
    outflow_amount: Decimal | None = Field(default=None, gt=0)
    inflow_amount: Decimal | None = Field(default=None, gt=0)
    counterparty: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    is_active: bool = True


class ShortcutOrderWrite(BaseModel):
    expected_version: int = Field(ge=1)
    ids: list[int] = Field(min_length=0)


def clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def minor(amount: Decimal | None, places: int) -> int | None:
    if amount is None:
        return None
    try:
        scaled = amount * (Decimal(10) ** places)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(422, "金额格式不正确。") from exc
    rounded = scaled.to_integral_value(rounding=ROUND_HALF_UP)
    if scaled != rounded:
        raise HTTPException(422, f"该币种最多支持 {places} 位小数。")
    value = int(rounded)
    if value <= 0:
        raise HTTPException(422, "预填金额必须大于 0。")
    return value


def money(value: int | None, places: int | None) -> str | None:
    if value is None or places is None:
        return None
    amount = Decimal(value) / (Decimal(10) ** places)
    return f"{amount:.{places}f}"


def config_version(connection) -> int:
    row = connection.execute(
        "SELECT value FROM app_settings WHERE key=?", (VERSION_KEY,)
    ).fetchone()
    return int(row["value"]) if row else 1


def shortcut_rows(connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT s.*,
               source.name AS source_account_name,
               source.currency_code AS source_currency_code,
               source.is_active AS source_account_active,
               source_currency.decimal_places AS source_decimal_places,
               destination.name AS destination_account_name,
               destination.currency_code AS destination_currency_code,
               destination.is_active AS destination_account_active,
               destination_currency.decimal_places AS destination_decimal_places,
               category.name AS category_name,
               category.kind AS category_kind,
               category.is_active AS category_active,
               EXISTS(
                   SELECT 1 FROM categories AS child
                   WHERE child.parent_id=category.id AND child.is_active=1
               ) AS category_has_active_children,
               parent.name AS category_parent_name,
               budget.name AS budget_system_name,
               budget.is_active AS budget_system_active
        FROM transaction_entry_shortcuts AS s
        LEFT JOIN accounts AS source ON source.id=s.source_account_id
        LEFT JOIN currencies AS source_currency
          ON source_currency.code=source.currency_code
        LEFT JOIN accounts AS destination ON destination.id=s.destination_account_id
        LEFT JOIN currencies AS destination_currency
          ON destination_currency.code=destination.currency_code
        LEFT JOIN categories AS category ON category.id=s.category_id
        LEFT JOIN categories AS parent ON parent.id=category.parent_id
        LEFT JOIN budget_systems AS budget ON budget.id=s.budget_system_id
        ORDER BY s.sort_order, s.id
        """
    ).fetchall()
    return [serialize_shortcut(dict(row)) for row in rows]


def invalid_reason(row: dict) -> str | None:
    if row["mode"] in ("expense", "transfer"):
        if not row["source_account_name"]:
            return "来源账户不存在"
        if not row["source_account_active"]:
            return "来源账户已停用"
    if row["mode"] in ("income", "transfer"):
        if not row["destination_account_name"]:
            return "去向账户不存在"
        if not row["destination_account_active"]:
            return "去向账户已停用"
    if row["mode"] in ("expense", "income"):
        if not row["category_name"]:
            return "分类不存在"
        if not row["category_active"]:
            return "分类已停用"
        if row["category_kind"] != row["mode"]:
            return "分类类型不匹配"
        if row["category_has_active_children"]:
            return "分类已变为父级，请选择末级分类"
        if row["budget_system_id"] and not row["budget_system_name"]:
            return "预算不存在"
        if row["budget_system_id"] and not row["budget_system_active"]:
            return "预算已停用"
    return None


def serialize_shortcut(row: dict) -> dict:
    reason = invalid_reason(row)
    return {
        "id": row["id"],
        "name": row["name"],
        "mode": row["mode"],
        "source_account_id": row["source_account_id"],
        "source_account_name": row["source_account_name"],
        "source_currency_code": row["source_currency_code"],
        "destination_account_id": row["destination_account_id"],
        "destination_account_name": row["destination_account_name"],
        "destination_currency_code": row["destination_currency_code"],
        "category_id": row["category_id"],
        "category_name": (
            f"{row['category_parent_name']} / {row['category_name']}"
            if row["category_parent_name"] else row["category_name"]
        ),
        "budget_system_id": row["budget_system_id"],
        "budget_system_name": row["budget_system_name"],
        "outflow_amount": money(row["outflow_minor"], row["source_decimal_places"]),
        "inflow_amount": money(row["inflow_minor"], row["destination_decimal_places"]),
        "counterparty": row["counterparty"],
        "note": row["note"],
        "sort_order": row["sort_order"],
        "is_active": bool(row["is_active"]),
        "valid": reason is None,
        "invalid_reason": reason,
    }


def snapshot(connection) -> dict:
    return {"version": config_version(connection), "shortcuts": shortcut_rows(connection)}


def require_version(connection, expected: int) -> None:
    if config_version(connection) != expected:
        raise HTTPException(409, "便捷操作已更新，请重新加载后再编辑。")


def account(connection, account_id: int | None, label: str, allow_inactive: bool):
    if account_id is None:
        raise HTTPException(422, f"请选择{label}。")
    row = connection.execute(
        """
        SELECT accounts.*, currencies.decimal_places
        FROM accounts JOIN currencies ON currencies.code=accounts.currency_code
        WHERE accounts.id=?
        """,
        (account_id,),
    ).fetchone()
    if not row or (not allow_inactive and not row["is_active"]):
        raise HTTPException(422, f"{label}不存在或已停用。")
    return row


def category(connection, category_id: int | None, kind: str, allow_inactive: bool):
    if category_id is None:
        raise HTTPException(422, "请选择分类。")
    row = connection.execute(
        "SELECT * FROM categories WHERE id=? AND kind=?",
        (category_id, kind),
    ).fetchone()
    child = connection.execute(
        "SELECT 1 FROM categories WHERE parent_id=? AND is_active=1", (category_id,)
    ).fetchone()
    if not row or child or (not allow_inactive and not row["is_active"]):
        raise HTTPException(422, "请选择对应模式下启用的末级分类。")
    return row


def budget(connection, system_id: int | None, allow_inactive: bool) -> None:
    if system_id is None:
        return
    row = connection.execute(
        "SELECT is_active FROM budget_systems WHERE id=?", (system_id,)
    ).fetchone()
    if not row or (not allow_inactive and not row["is_active"]):
        raise HTTPException(422, "预算不存在或已停用。")


def values_for_write(connection, body: ShortcutWrite, old: sqlite3.Row | None) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请填写按钮名称。")
    allow_inactive = not body.is_active
    values = {
        "name": name,
        "mode": body.mode,
        "source_account_id": None,
        "destination_account_id": None,
        "category_id": None,
        "budget_system_id": None,
        "outflow_minor": None,
        "inflow_minor": None,
        "counterparty": None,
        "note": clean_optional(body.note),
        "sort_order": body.sort_order,
        "is_active": int(body.is_active),
    }
    if values["sort_order"] is None:
        values["sort_order"] = old["sort_order"] if old else (
            connection.execute(
                "SELECT COALESCE(MAX(sort_order),0)+10 FROM transaction_entry_shortcuts"
            ).fetchone()[0]
        )

    if body.mode == "expense":
        source = account(connection, body.source_account_id, "支付账户", allow_inactive)
        category(connection, body.category_id, "expense", allow_inactive)
        budget(connection, body.budget_system_id, allow_inactive)
        values.update(
            source_account_id=source["id"],
            category_id=body.category_id,
            budget_system_id=body.budget_system_id,
            outflow_minor=minor(body.outflow_amount, source["decimal_places"]),
            counterparty=clean_optional(body.counterparty),
        )
    elif body.mode == "income":
        destination = account(
            connection, body.destination_account_id, "到账账户", allow_inactive
        )
        category(connection, body.category_id, "income", allow_inactive)
        budget(connection, body.budget_system_id, allow_inactive)
        values.update(
            destination_account_id=destination["id"],
            category_id=body.category_id,
            budget_system_id=body.budget_system_id,
            inflow_minor=minor(body.inflow_amount, destination["decimal_places"]),
            counterparty=clean_optional(body.counterparty),
        )
    else:
        source = account(connection, body.source_account_id, "转出账户", allow_inactive)
        destination = account(
            connection, body.destination_account_id, "转入账户", allow_inactive
        )
        if source["id"] == destination["id"]:
            raise HTTPException(422, "转出和转入账户不能相同。")
        outflow = minor(body.outflow_amount, source["decimal_places"])
        inflow = minor(body.inflow_amount, destination["decimal_places"])
        if (
            source["currency_code"] == destination["currency_code"]
            and outflow is not None and inflow is not None and outflow != inflow
        ):
            raise HTTPException(422, "同币种转账的两个预填金额必须相同。")
        values.update(
            source_account_id=source["id"],
            destination_account_id=destination["id"],
            outflow_minor=outflow,
            inflow_minor=inflow,
        )
    return values


def audit(connection, *, action: str, entity_id: int | None, before, after, backup) -> None:
    connection.execute(
        """
        INSERT INTO change_audit_logs(
            entity_type,entity_id,action,before_json,after_json,backup_file
        ) VALUES ('entry_shortcut',?,?,?,?,?)
        """,
        (
            entity_id,
            action,
            json.dumps(before, ensure_ascii=False, sort_keys=True) if before is not None else None,
            json.dumps(after, ensure_ascii=False, sort_keys=True) if after is not None else None,
            backup.name,
        ),
    )


def bump_version(connection) -> None:
    connection.execute(
        """
        UPDATE app_settings SET value=CAST(value AS INTEGER)+1,
            updated_at=CURRENT_TIMESTAMP WHERE key=?
        """,
        (VERSION_KEY,),
    )


@router.get("/api/entry-shortcuts")
def get_entry_shortcuts():
    with connect() as connection:
        return snapshot(connection)


@router.post("/api/entry-shortcuts", status_code=201)
def create_entry_shortcut(body: ShortcutWrite):
    backup_path = create_backup()
    try:
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            require_version(connection, body.expected_version)
            values = values_for_write(connection, body, None)
            cursor = connection.execute(
                f"INSERT INTO transaction_entry_shortcuts({','.join(values)}) "
                f"VALUES ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
            entity_id = cursor.lastrowid
            bump_version(connection)
            after = next(row for row in shortcut_rows(connection) if row["id"] == entity_id)
            audit(connection, action="create", entity_id=entity_id, before=None,
                  after=after, backup=backup_path)
            return snapshot(connection)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "按钮名称重复或配置不完整。") from exc


@router.put("/api/entry-shortcuts/order")
def reorder_entry_shortcuts(body: ShortcutOrderWrite):
    backup_path = create_backup()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_version(connection, body.expected_version)
        current_ids = [row["id"] for row in connection.execute(
            "SELECT id FROM transaction_entry_shortcuts ORDER BY sort_order,id"
        )]
        if len(set(body.ids)) != len(body.ids) or set(body.ids) != set(current_ids):
            raise HTTPException(422, "排序必须包含当前全部便捷操作。")
        before = snapshot(connection)
        for order, entity_id in enumerate(body.ids, start=1):
            connection.execute(
                "UPDATE transaction_entry_shortcuts SET sort_order=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (order * 10, entity_id),
            )
        bump_version(connection)
        after = snapshot(connection)
        audit(connection, action="update", entity_id=None, before=before,
              after=after, backup=backup_path)
        return after


@router.put("/api/entry-shortcuts/{shortcut_id}")
def update_entry_shortcut(shortcut_id: int, body: ShortcutWrite):
    backup_path = create_backup()
    try:
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            require_version(connection, body.expected_version)
            old = connection.execute(
                "SELECT * FROM transaction_entry_shortcuts WHERE id=?", (shortcut_id,)
            ).fetchone()
            if not old:
                raise HTTPException(404, "便捷操作不存在。")
            before = next(row for row in shortcut_rows(connection) if row["id"] == shortcut_id)
            values = values_for_write(connection, body, old)
            values["updated_at"] = "CURRENT_TIMESTAMP"
            assignments = []
            parameters = []
            for key, value in values.items():
                if key == "updated_at":
                    assignments.append("updated_at=CURRENT_TIMESTAMP")
                else:
                    assignments.append(f"{key}=?")
                    parameters.append(value)
            connection.execute(
                f"UPDATE transaction_entry_shortcuts SET {','.join(assignments)} WHERE id=?",
                (*parameters, shortcut_id),
            )
            bump_version(connection)
            after = next(row for row in shortcut_rows(connection) if row["id"] == shortcut_id)
            audit(connection, action="update", entity_id=shortcut_id, before=before,
                  after=after, backup=backup_path)
            return snapshot(connection)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "按钮名称重复或配置不完整。") from exc


@router.delete("/api/entry-shortcuts/{shortcut_id}")
def delete_entry_shortcut(shortcut_id: int, expected_version: int):
    backup_path = create_backup()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_version(connection, expected_version)
        rows = shortcut_rows(connection)
        before = next((row for row in rows if row["id"] == shortcut_id), None)
        if before is None:
            raise HTTPException(404, "便捷操作不存在。")
        connection.execute(
            "DELETE FROM transaction_entry_shortcuts WHERE id=?", (shortcut_id,)
        )
        bump_version(connection)
        audit(connection, action="delete", entity_id=shortcut_id, before=before,
              after=None, backup=backup_path)
        return snapshot(connection)

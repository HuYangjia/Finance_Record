from __future__ import annotations

from calendar import monthrange
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
import json
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.database import ROOT_DIR, connect, create_backup, initialize_database
from app.budgets import router as budgets_router, require_system, budget_filter_sql
from app.budget_storage import router as budget_storage_router
from app.entry_shortcuts import router as entry_shortcuts_router
from app.income_allocations import router as income_allocations_router, AllocationItem, save_allocations
from app.offsets import (
    router as offsets_router,
    annotate_transactions,
    group_for_transaction,
    check_member_update,
    record_member_update,
)
from app.personal_cashflow import (
    read_ledger, build_entries, filter_entries, summarize as summarize_personal,
)
from app.reporting import (
    amount_to_base_currency, amount_to_hkd, analysis_main_amount,
    build_hkd_histogram, build_transaction_analysis, format_decimal,
    hkd_bucket_bounds, nearest_cny_exchange_rate,
    next_nice_hkd_bucket_width, nice_hkd_bucket_width,
)


STATIC_DIR = ROOT_DIR / "static"
TRANSACTION_TYPE_LABELS = {
    "income": "入账",
    "expense": "支出",
    "transfer": "转账",
    "fx_exchange": "换汇",
    "broker_deposit": "券商入金",
    "broker_withdrawal": "券商出金",
    "adjustment": "调整",
}
TRANSACTION_ANALYSIS_AMOUNT_LABELS = {
    "expense": "支出金额",
    "income": "入账金额",
    "transfer": "转出金额",
    "fx_exchange": "转出金额",
    "broker_deposit": "转出金额",
    "broker_withdrawal": "转出金额",
    "adjustment": "调整金额",
}
TRANSACTION_ANALYSIS_ACCOUNT_LABELS = {
    "expense": "支付账户",
    "income": "到账账户",
    "transfer": "转出账户",
    "fx_exchange": "转出账户",
    "broker_deposit": "转出账户",
    "broker_withdrawal": "转出账户",
    "adjustment": "调整账户",
}
WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
BASE_CURRENCY_CODES = ("CNY", "HKD")
CURRENCY_DISPLAY_ORDER = ("CNY", "HKD", "USD", "EUR", "JPY")
FLAG_TYPE_LABELS = {
    "suspected_duplicate": "疑似重复",
    "missing_information": "信息不完整",
    "suspicious_amount": "金额异常",
    "other": "其他待核实",
}
INVESTMENT_ACCOUNT_TYPES = {"investment_asset", "broker_cash"}
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
FX_RATE_SOURCE = "Frankfurter"
FREQUENT_SNAPSHOT_RULE_VERSION = 3
ACCOUNT_ACTIVITY_WINDOW_DAYS = 45
ACCOUNT_RECENT_WINDOW_DAYS = 14
CATEGORY_ACTIVITY_WINDOW_DAYS = 45
CATEGORY_RECENT_WINDOW_DAYS = 14
FREQUENT_ACCOUNT_ACTIVITY_KEYS = (
    "overall",
    "expense_source",
    "income_destination",
    "movement_source",
    "movement_destination",
)


def transaction_type_label(
    transaction_type: str,
    investment_valuation_id: int | None = None,
) -> str:
    if investment_valuation_id is not None:
        return "投资估值损益"
    return TRANSACTION_TYPE_LABELS.get(transaction_type, transaction_type)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    try:
        sync_daily_cny_exchange_rates(today_in_shanghai())
    except Exception as exc:
        print(f"汇率自动同步失败：{exc}")
    try:
        with connect() as connection:
            ensure_daily_frequent_snapshot(connection, today_in_shanghai())
    except Exception as exc:
        print(f"常用项每日统计失败：{exc}")
    yield


app = FastAPI(title="个人财务账本", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(offsets_router)
app.include_router(budgets_router)
app.include_router(budget_storage_router)
app.include_router(entry_shortcuts_router)
app.include_router(income_allocations_router)


@app.get("/offsets", include_in_schema=False)
def offsets_page():
    return FileResponse(STATIC_DIR / "offsets.html", headers={"Cache-Control": "no-store"})


class ExpenseCreate(BaseModel):
    budget_system_id: int | None = Field(default=None, gt=0)
    transaction_date: date = Field(default_factory=date.today)
    source_account_id: int
    amount: Decimal = Field(gt=0)
    category_id: int
    counterparty: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class IncomeCreate(BaseModel):
    budget_system_id: int | None = Field(default=None, gt=0)
    budget_allocations: list[AllocationItem] | None = Field(default=None, max_length=300)
    transaction_date: date = Field(default_factory=date.today)
    destination_account_id: int
    amount: Decimal = Field(gt=0)
    category_id: int
    counterparty: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class TransferCreate(BaseModel):
    transaction_date: date = Field(default_factory=date.today)
    source_account_id: int
    destination_account_id: int
    outflow_amount: Decimal = Field(gt=0)
    inflow_amount: Decimal | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=500)


class TransactionUpdate(BaseModel):
    budget_system_id: int | None = Field(default=None, gt=0)
    transaction_date: date
    transaction_type: str
    source_account_id: int | None = None
    destination_account_id: int | None = None
    outflow_amount: Decimal | None = Field(default=None, gt=0)
    inflow_amount: Decimal | None = Field(default=None, gt=0)
    category_id: int | None = None
    counterparty: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    correction_reason: str | None = Field(default=None, max_length=500)


class TransactionDelete(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class BalanceAdjustmentCreate(BaseModel):
    adjustment_date: date = Field(default_factory=date.today)
    actual_balance: Decimal = Field(ge=0)
    reason: str = Field(min_length=1, max_length=350)
    note: str | None = Field(default=None, max_length=500)


class OpeningBalanceUpdate(BaseModel):
    opening_balance: Decimal = Field(ge=0)
    opening_balance_date: date
    reason: str = Field(min_length=1, max_length=500)


class TransactionFlagUpdate(BaseModel):
    flag_type: str
    note: str = Field(min_length=1, max_length=500)


class ReconciliationCreate(BaseModel):
    reconciliation_date: date = Field(default_factory=date.today)
    actual_balance: Decimal = Field(ge=0)
    note: str | None = Field(default=None, max_length=500)


class InvestmentValuationUpsert(BaseModel):
    total_value: Decimal = Field(ge=0)
    note: str | None = Field(default=None, max_length=500)


class DailyCnyRateUpdate(BaseModel):
    usd_cny_rate: Decimal = Field(gt=0)
    hkd_cny_rate: Decimal = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)


class FxRateSyncRequest(BaseModel):
    rate_date: date = Field(default_factory=lambda: today_in_shanghai())
    force: bool = False


def amount_to_minor(amount: Decimal, decimal_places: int) -> int:
    try:
        multiplier = Decimal(10) ** decimal_places
        minor_amount = amount * multiplier
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=422, detail="金额格式不正确。") from exc

    rounded = minor_amount.to_integral_value(rounding=ROUND_HALF_UP)
    if minor_amount != rounded:
        raise HTTPException(
            status_code=422,
            detail=f"该币种最多支持 {decimal_places} 位小数。",
        )
    return int(rounded)


def format_minor(amount_minor: int, decimal_places: int) -> str:
    divisor = Decimal(10) ** decimal_places
    amount = Decimal(amount_minor) / divisor
    return f"{amount:.{decimal_places}f}"


def today_in_shanghai() -> date:
    return datetime.now(SHANGHAI_TIMEZONE).date()


def decimal_to_storage(value: Decimal) -> str:
    return format(value.normalize(), "f")


def month_as_of_date(month: str) -> str:
    selected = datetime.strptime(month, "%Y-%m").date()
    current_date = today_in_shanghai()
    current_month = current_date.strftime("%Y-%m")
    if month > current_month:
        raise HTTPException(status_code=422, detail="不能查看未来月份。")
    if month == current_month:
        return current_date.isoformat()
    return date(
        selected.year,
        selected.month,
        monthrange(selected.year, selected.month)[1],
    ).isoformat()


def previous_month_end(month: str) -> str:
    selected = datetime.strptime(month, "%Y-%m").date()
    return (selected - timedelta(days=1)).isoformat()


def fetch_public_cny_rates(rate_date: date) -> dict[str, Any]:
    start_date = rate_date - timedelta(days=10)
    query = urlencode(
        {
            "base": "USD",
            "quotes": "CNY,HKD",
            "from": start_date.isoformat(),
            "to": rate_date.isoformat(),
        }
    )
    request = Request(
        f"https://api.frankfurter.dev/v2/rates?{query}",
        headers={"User-Agent": "personal-finance-ledger/1.0"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法从公开汇率服务获取数据。") from exc

    rates_by_date: dict[str, dict[str, Decimal]] = {}
    for item in payload:
        item_date = item.get("date")
        quote_currency = item.get("quote")
        rate = item.get("rate")
        if item_date is None or quote_currency not in {"CNY", "HKD"}:
            continue
        try:
            rates_by_date.setdefault(item_date, {})[quote_currency] = Decimal(str(rate))
        except (InvalidOperation, TypeError):
            continue

    complete_dates = [
        item_date
        for item_date, values in rates_by_date.items()
        if values.get("CNY", Decimal(0)) > 0 and values.get("HKD", Decimal(0)) > 0
    ]
    if not complete_dates:
        raise RuntimeError("公开汇率服务未返回可用的美元、港币兑人民币报价。")

    market_date = max(complete_dates)
    usd_cny_rate = rates_by_date[market_date]["CNY"]
    usd_hkd_rate = rates_by_date[market_date]["HKD"]
    return {
        "usd_cny_rate": usd_cny_rate,
        "hkd_cny_rate": usd_cny_rate / usd_hkd_rate,
        "source": FX_RATE_SOURCE,
        "market_date": market_date,
    }


def serialize_daily_cny_exchange_rate(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "rate_date": row["rate_date"],
        "usd_cny_rate": format_decimal(Decimal(row["usd_cny_rate"]), 4),
        "hkd_cny_rate": format_decimal(Decimal(row["hkd_cny_rate"]), 4),
        "source": row["source"],
        "quoted_at": row["quoted_at"],
        "market_date": row["market_date"],
        "note": row["note"],
    }


def get_daily_cny_exchange_rate_as_of(connection, as_of_date: str):
    return connection.execute(
        """
        SELECT *
        FROM daily_cny_exchange_rates
        WHERE rate_date <= ?
        ORDER BY rate_date DESC
        LIMIT 1
        """,
        (as_of_date,),
    ).fetchone()


def upsert_daily_cny_exchange_rate(
    connection,
    *,
    rate_date: str,
    usd_cny_rate: Decimal,
    hkd_cny_rate: Decimal,
    source: str,
    market_date: str,
    quoted_at: str | None = None,
    note: str | None = None,
):
    connection.execute(
        """
        INSERT INTO daily_cny_exchange_rates (
            rate_date, usd_cny_rate, hkd_cny_rate,
            source, quoted_at, market_date, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rate_date) DO UPDATE SET
            usd_cny_rate = excluded.usd_cny_rate,
            hkd_cny_rate = excluded.hkd_cny_rate,
            source = excluded.source,
            quoted_at = excluded.quoted_at,
            market_date = excluded.market_date,
            note = excluded.note
        """,
        (
            rate_date,
            decimal_to_storage(usd_cny_rate),
            decimal_to_storage(hkd_cny_rate),
            source,
            quoted_at,
            market_date,
            clean_optional_text(note),
        ),
    )
    return connection.execute(
        "SELECT * FROM daily_cny_exchange_rates WHERE rate_date = ?",
        (rate_date,),
    ).fetchone()


def sync_daily_cny_exchange_rates(rate_date: date, force: bool = False) -> dict[str, Any]:
    requested_date = rate_date.isoformat()
    with connect() as connection:
        existing = connection.execute(
            "SELECT * FROM daily_cny_exchange_rates WHERE rate_date = ?",
            (requested_date,),
        ).fetchone()
    if existing is not None and not force:
        return serialize_daily_cny_exchange_rate(existing)

    fetched = fetch_public_cny_rates(rate_date)
    with connect() as connection:
        row = upsert_daily_cny_exchange_rate(
            connection,
            rate_date=requested_date,
            usd_cny_rate=fetched["usd_cny_rate"],
            hkd_cny_rate=fetched["hkd_cny_rate"],
            source=fetched["source"],
            market_date=fetched["market_date"],
        )
    return serialize_daily_cny_exchange_rate(row)


def validate_month(month: str) -> str:
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="月份必须使用 YYYY-MM 格式。") from exc
    return month


def validate_optional_date(value: str | None, field_label: str) -> str | None:
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_label}必须使用 YYYY-MM-DD 格式。",
        ) from exc
    return value


def get_currency_decimal_places(connection, currency_code: str) -> int:
    currency = connection.execute(
        "SELECT decimal_places FROM currencies WHERE code = ?",
        (currency_code,),
    ).fetchone()
    if currency is None:
        raise HTTPException(status_code=404, detail="币种不存在。")
    return currency["decimal_places"]


def get_active_account(connection, account_id: int, field_label: str):
    account = connection.execute(
        """
        SELECT accounts.id, accounts.name, accounts.currency_code, currencies.decimal_places
        FROM accounts
        JOIN currencies ON currencies.code = accounts.currency_code
        WHERE accounts.id = ? AND accounts.is_active = 1
        """,
        (account_id,),
    ).fetchone()
    if account is None:
        raise HTTPException(status_code=404, detail=f"{field_label}不存在或已停用。")
    return account


def get_active_category(connection, category_id: int, kind: str):
    category = connection.execute(
        """
        SELECT id, name FROM categories
        WHERE id = ? AND kind = ? AND is_active = 1
        """,
        (category_id, kind),
    ).fetchone()
    if category is None:
        category_label = "支出" if kind == "expense" else "收入"
        raise HTTPException(status_code=404, detail=f"{category_label}分类不存在或已停用。")
    return category


def clean_optional_text(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def clean_required_text(value: str, field_label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"{field_label}不能为空。")
    return cleaned


def json_snapshot(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def write_audit_log(
    connection,
    *,
    entity_type: str,
    entity_id: int | None,
    action: str,
    backup_file: str,
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO change_audit_logs (
            entity_type, entity_id, action, reason,
            before_json, after_json, backup_file
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            action,
            clean_optional_text(reason),
            json_snapshot(before),
            json_snapshot(after),
            backup_file,
        ),
    )


def get_transaction_account(
    connection,
    account_id: int,
    field_label: str,
    allowed_inactive_ids: set[int] | None = None,
):
    account = connection.execute(
        """
        SELECT
            accounts.id,
            accounts.name,
            accounts.account_class,
            accounts.currency_code,
            accounts.is_active,
            currencies.decimal_places
        FROM accounts
        JOIN currencies ON currencies.code = accounts.currency_code
        WHERE accounts.id = ?
        """,
        (account_id,),
    ).fetchone()
    if account is None:
        raise HTTPException(status_code=404, detail=f"{field_label}不存在。")
    if not account["is_active"] and account_id not in (allowed_inactive_ids or set()):
        raise HTTPException(status_code=422, detail=f"{field_label}已停用，不能用于新的账户路径。")
    return account


def get_transaction_category(
    connection,
    category_id: int,
    kind: str,
    allowed_inactive_ids: set[int] | None = None,
):
    category = connection.execute(
        """
        SELECT id, name, kind, is_active FROM categories
        WHERE id = ? AND kind = ?
        """,
        (category_id, kind),
    ).fetchone()
    if category is None:
        category_label = "支出" if kind == "expense" else "收入"
        raise HTTPException(status_code=404, detail=f"{category_label}分类不存在。")
    if not category["is_active"] and category_id not in (allowed_inactive_ids or set()):
        raise HTTPException(status_code=422, detail="已停用分类不能用于新的流水。")
    return category


def require_minor_amount(
    amount: Decimal | None,
    decimal_places: int,
    field_label: str,
) -> int:
    if amount is None:
        raise HTTPException(status_code=422, detail=f"{field_label}不能为空。")
    return amount_to_minor(amount, decimal_places)


def normalize_transaction_update(
    connection,
    transaction: TransactionUpdate,
    original: dict[str, Any] | None = None,
) -> dict[str, Any]:
    transaction_type = transaction.transaction_type
    if transaction_type not in TRANSACTION_TYPE_LABELS:
        raise HTTPException(status_code=422, detail="流水类型不支持。")

    original = original or {}
    allowed_inactive_account_ids = {
        account_id
        for account_id in (
            original.get("source_account_id"),
            original.get("destination_account_id"),
        )
        if account_id is not None
    }
    allowed_inactive_category_ids = {
        category_id
        for category_id in (original.get("category_id"),)
        if category_id is not None
    }
    source_account = (
        get_transaction_account(
            connection,
            transaction.source_account_id,
            "来源账户",
            allowed_inactive_account_ids,
        )
        if transaction.source_account_id is not None
        else None
    )
    destination_account = (
        get_transaction_account(
            connection,
            transaction.destination_account_id,
            "去向账户",
            allowed_inactive_account_ids,
        )
        if transaction.destination_account_id is not None
        else None
    )
    payload: dict[str, Any] = {
        "transaction_date": transaction.transaction_date.isoformat(),
        "transaction_type": transaction_type,
        "source_account_id": transaction.source_account_id,
        "destination_account_id": transaction.destination_account_id,
        "outflow_minor": None,
        "inflow_minor": None,
        "category_id": transaction.category_id,
        "counterparty": clean_optional_text(transaction.counterparty),
        "note": clean_optional_text(transaction.note),
    }

    if transaction_type == "income":
        if source_account is not None or destination_account is None:
            raise HTTPException(status_code=422, detail="入账只能填写到账账户。")
        if transaction.category_id is None:
            raise HTTPException(status_code=422, detail="入账必须选择收入分类。")
        get_transaction_category(
            connection,
            transaction.category_id,
            "income",
            allowed_inactive_category_ids,
        )
        payload["inflow_minor"] = require_minor_amount(
            transaction.inflow_amount,
            destination_account["decimal_places"],
            "入账金额",
        )
    elif transaction_type == "expense":
        if source_account is None or destination_account is not None:
            raise HTTPException(status_code=422, detail="支出只能填写支付账户。")
        if transaction.category_id is None:
            raise HTTPException(status_code=422, detail="支出必须选择支出分类。")
        get_transaction_category(
            connection,
            transaction.category_id,
            "expense",
            allowed_inactive_category_ids,
        )
        payload["outflow_minor"] = require_minor_amount(
            transaction.outflow_amount,
            source_account["decimal_places"],
            "支出金额",
        )
    elif transaction_type == "adjustment":
        if transaction.category_id is not None:
            raise HTTPException(status_code=422, detail="余额调整不能选择分类。")
        if (source_account is None) == (destination_account is None):
            raise HTTPException(status_code=422, detail="余额调整必须且只能选择一个账户。")
        if source_account is not None:
            payload["outflow_minor"] = require_minor_amount(
                transaction.outflow_amount,
                source_account["decimal_places"],
                "调减金额",
            )
            if transaction.inflow_amount is not None:
                raise HTTPException(status_code=422, detail="调减不能填写流入金额。")
        else:
            payload["inflow_minor"] = require_minor_amount(
                transaction.inflow_amount,
                destination_account["decimal_places"],
                "调增金额",
            )
            if transaction.outflow_amount is not None:
                raise HTTPException(status_code=422, detail="调增不能填写流出金额。")
    else:
        if source_account is None or destination_account is None:
            raise HTTPException(status_code=422, detail="账户内资金移动必须填写来源和去向账户。")
        if source_account["id"] == destination_account["id"]:
            raise HTTPException(status_code=422, detail="来源和去向账户不能相同。")
        if transaction.category_id is not None:
            raise HTTPException(status_code=422, detail="账户内资金移动不能选择分类。")
        payload["outflow_minor"] = require_minor_amount(
            transaction.outflow_amount,
            source_account["decimal_places"],
            "转出金额",
        )
        payload["inflow_minor"] = require_minor_amount(
            transaction.inflow_amount,
            destination_account["decimal_places"],
            "转入金额",
        )
        same_currency = source_account["currency_code"] == destination_account["currency_code"]
        if transaction_type == "transfer" and not same_currency:
            raise HTTPException(status_code=422, detail="跨币种账户应使用换汇类型。")
        if transaction_type == "fx_exchange" and same_currency:
            raise HTTPException(status_code=422, detail="同币种账户应使用转账类型。")
        if same_currency and payload["outflow_minor"] != payload["inflow_minor"]:
            raise HTTPException(status_code=422, detail="同币种转账的转出和转入金额必须相同。")

    return payload


def fetch_transaction_snapshot(connection, transaction_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT
            transactions.*,
            source.name AS source_account_name,
            source.currency_code AS source_currency_code,
            source_currency.decimal_places AS source_decimal_places,
            destination.name AS destination_account_name,
            destination.currency_code AS destination_currency_code,
            destination_currency.decimal_places AS destination_decimal_places,
            categories.name AS category_name,
            flags.flag_type,
            flags.note AS flag_note,
            investment_valuations.id AS investment_valuation_id
        FROM transactions
        LEFT JOIN accounts AS source ON source.id = transactions.source_account_id
        LEFT JOIN currencies AS source_currency ON source_currency.code = source.currency_code
        LEFT JOIN accounts AS destination ON destination.id = transactions.destination_account_id
        LEFT JOIN currencies AS destination_currency
            ON destination_currency.code = destination.currency_code
        LEFT JOIN categories ON categories.id = transactions.category_id
        LEFT JOIN transaction_flags AS flags ON flags.transaction_id = transactions.id
        LEFT JOIN investment_valuations
            ON investment_valuations.generated_transaction_id = transactions.id
        WHERE transactions.id = ?
        """,
        (transaction_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def fetch_account_snapshot(connection, account_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT accounts.*, currencies.decimal_places
        FROM accounts
        JOIN currencies ON currencies.code = accounts.currency_code
        WHERE accounts.id = ?
        """,
        (account_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def account_balance_as_of(connection, account: dict[str, Any] | Any, on_date: str) -> int:
    if on_date < account["opening_balance_date"]:
        raise HTTPException(status_code=422, detail="日期不能早于账户期初日期。")
    movement_row = connection.execute(
        """
        SELECT
            COALESCE(SUM(
                CASE WHEN destination_account_id = ? THEN inflow_minor ELSE 0 END
            ), 0) AS inflow_minor,
            COALESCE(SUM(
                CASE WHEN source_account_id = ? THEN outflow_minor ELSE 0 END
            ), 0) AS outflow_minor
        FROM transactions
        WHERE transaction_date <= ?
          AND (source_account_id = ? OR destination_account_id = ?)
        """,
        (account["id"], account["id"], on_date, account["id"], account["id"]),
    ).fetchone()
    if account["account_class"] == "asset":
        return (
            account["opening_balance_minor"]
            + movement_row["inflow_minor"]
            - movement_row["outflow_minor"]
        )
    return (
        account["opening_balance_minor"]
        + movement_row["outflow_minor"]
        - movement_row["inflow_minor"]
    )


def latest_reconciliation_status(connection, account: dict[str, Any] | Any) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT reconciliation_date, actual_balance_minor, note
        FROM account_reconciliations
        WHERE account_id = ?
        ORDER BY reconciliation_date DESC
        LIMIT 1
        """,
        (account["id"],),
    ).fetchone()
    if row is None:
        return {"status": "pending", "label": "待对账"}
    book_balance_minor = account_balance_as_of(connection, account, row["reconciliation_date"])
    difference_minor = row["actual_balance_minor"] - book_balance_minor
    adjustment_row = connection.execute(
        """
        SELECT COUNT(*) AS adjustment_count
        FROM transactions
        WHERE transaction_date = ?
          AND transaction_type = 'adjustment'
          AND (source_account_id = ? OR destination_account_id = ?)
          AND NOT EXISTS (
              SELECT 1
              FROM investment_valuations
              WHERE investment_valuations.generated_transaction_id = transactions.id
          )
        """,
        (row["reconciliation_date"], account["id"], account["id"]),
    ).fetchone()
    decimal_places = account["decimal_places"]
    return {
        "status": "matched" if difference_minor == 0 else "difference",
        "label": "已对齐" if difference_minor == 0 else "存在差异",
        "reconciliation_date": row["reconciliation_date"],
        "actual_balance": format_minor(row["actual_balance_minor"], decimal_places),
        "book_balance": format_minor(book_balance_minor, decimal_places),
        "difference": format_minor(difference_minor, decimal_places),
        "note": row["note"],
        "manual_adjustment_count": adjustment_row["adjustment_count"],
    }


def get_account_balance_row(connection, account_id: int):
    account = connection.execute(
        """
        SELECT account_balances.*, currencies.decimal_places
        FROM account_balances
        JOIN currencies ON currencies.code = account_balances.currency_code
        WHERE account_balances.id = ?
        """,
        (account_id,),
    ).fetchone()
    if account is None:
        raise HTTPException(status_code=404, detail="账户不存在。")
    return account


def format_transaction_for_edit(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "budget_system_id": snapshot.get("budget_system_id"),
        "id": snapshot["id"],
        "transaction_date": snapshot["transaction_date"],
        "transaction_type": snapshot["transaction_type"],
        "source_account_id": snapshot["source_account_id"],
        "destination_account_id": snapshot["destination_account_id"],
        "outflow_amount": (
            format_minor(snapshot["outflow_minor"], snapshot["source_decimal_places"])
            if snapshot["outflow_minor"] is not None
            else None
        ),
        "inflow_amount": (
            format_minor(snapshot["inflow_minor"], snapshot["destination_decimal_places"])
            if snapshot["inflow_minor"] is not None
            else None
        ),
        "category_id": snapshot["category_id"],
        "counterparty": snapshot["counterparty"],
        "note": snapshot["note"],
        "flag": (
            {
                "flag_type": snapshot["flag_type"],
                "note": snapshot["flag_note"],
            }
            if snapshot["flag_type"] is not None
            else None
        ),
        "is_investment_valuation": bool(snapshot.get("investment_valuation_id")),
    }


def get_investment_account(connection, account_id: int):
    account = get_account_balance_row(connection, account_id)
    if (
        account["account_class"] != "asset"
        or account["account_type"] not in INVESTMENT_ACCOUNT_TYPES
    ):
        raise HTTPException(status_code=422, detail="该账户不是可估值的投资账户。")
    return account


def fetch_investment_valuation_snapshot(connection, valuation_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT
            investment_valuations.*,
            accounts.name AS account_name,
            accounts.code AS account_code,
            accounts.currency_code,
            currencies.decimal_places,
            transactions.transaction_date AS generated_transaction_date,
            transactions.outflow_minor AS generated_outflow_minor,
            transactions.inflow_minor AS generated_inflow_minor,
            transactions.note AS generated_transaction_note
        FROM investment_valuations
        JOIN accounts ON accounts.id = investment_valuations.account_id
        JOIN currencies ON currencies.code = accounts.currency_code
        LEFT JOIN transactions
            ON transactions.id = investment_valuations.generated_transaction_id
        WHERE investment_valuations.id = ?
        """,
        (valuation_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def serialize_investment_valuation(snapshot: dict[str, Any]) -> dict[str, Any]:
    generated_change_minor = (
        snapshot["generated_inflow_minor"]
        if snapshot["generated_inflow_minor"] is not None
        else -snapshot["generated_outflow_minor"]
        if snapshot["generated_outflow_minor"] is not None
        else 0
    )
    return {
        "id": snapshot["id"],
        "account_id": snapshot["account_id"],
        "account_name": snapshot["account_name"],
        "account_code": snapshot["account_code"],
        "currency_code": snapshot["currency_code"],
        "valuation_date": snapshot["valuation_date"],
        "total_value": format_minor(
            snapshot["total_value_minor"],
            snapshot["decimal_places"],
        ),
        "note": snapshot["note"],
        "generated_transaction_id": snapshot["generated_transaction_id"],
        "generated_change": format_minor(
            generated_change_minor,
            snapshot["decimal_places"],
        ),
        "generated_change_minor": generated_change_minor,
        "created_at": snapshot["created_at"],
        "updated_at": snapshot["updated_at"],
    }


def recalculate_investment_valuation_transactions(connection, account_id: int) -> list[int]:
    account = get_investment_account(connection, account_id)
    valuation_rows = connection.execute(
        """
        SELECT id, valuation_date, total_value_minor, generated_transaction_id
        FROM investment_valuations
        WHERE account_id = ?
        ORDER BY valuation_date ASC, id ASC
        """,
        (account_id,),
    ).fetchall()
    transaction_ids = [
        row["generated_transaction_id"]
        for row in valuation_rows
        if row["generated_transaction_id"] is not None
    ]
    if transaction_ids:
        placeholders = ", ".join("?" for _ in transaction_ids)
        connection.execute(
            f"DELETE FROM transactions WHERE id IN ({placeholders})",
            transaction_ids,
        )

    recalculated_valuation_ids = []
    valuation_rows = connection.execute(
        """
        SELECT id, valuation_date, total_value_minor
        FROM investment_valuations
        WHERE account_id = ?
        ORDER BY valuation_date ASC, id ASC
        """,
        (account_id,),
    ).fetchall()
    for valuation in valuation_rows:
        book_balance_minor = account_balance_as_of(
            connection,
            account,
            valuation["valuation_date"],
        )
        difference_minor = valuation["total_value_minor"] - book_balance_minor
        generated_transaction_id = None
        if difference_minor != 0:
            note = (
                f"投资估值损益：{account['name']} "
                f"{valuation['valuation_date']} 总资产 "
                f"{format_minor(valuation['total_value_minor'], account['decimal_places'])} "
                f"{account['currency_code']}"
            )
            cursor = connection.execute(
                """
                INSERT INTO transactions (
                    transaction_date,
                    transaction_type,
                    source_account_id,
                    destination_account_id,
                    outflow_minor,
                    inflow_minor,
                    note
                ) VALUES (?, 'adjustment', ?, ?, ?, ?, ?)
                """,
                (
                    valuation["valuation_date"],
                    account_id if difference_minor < 0 else None,
                    account_id if difference_minor > 0 else None,
                    -difference_minor if difference_minor < 0 else None,
                    difference_minor if difference_minor > 0 else None,
                    note,
                ),
            )
            generated_transaction_id = cursor.lastrowid
        connection.execute(
            """
            UPDATE investment_valuations
            SET generated_transaction_id = ?
            WHERE id = ?
            """,
            (generated_transaction_id, valuation["id"]),
        )
        recalculated_valuation_ids.append(valuation["id"])
    return recalculated_valuation_ids


def generated_investment_valuation_id(connection, transaction_id: int) -> int | None:
    row = connection.execute(
        """
        SELECT id
        FROM investment_valuations
        WHERE generated_transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()
    return row["id"] if row is not None else None


def investment_cash_flows(
    connection,
    account_id: int,
    start_date: str,
    end_date: str,
) -> tuple[int, int]:
    rows = connection.execute(
        """
        SELECT
            transactions.source_account_id,
            transactions.destination_account_id,
            transactions.outflow_minor,
            transactions.inflow_minor,
            source.account_type AS source_account_type,
            destination.account_type AS destination_account_type
        FROM transactions
        LEFT JOIN accounts AS source ON source.id = transactions.source_account_id
        LEFT JOIN accounts AS destination ON destination.id = transactions.destination_account_id
        WHERE transactions.transaction_date > ?
          AND transactions.transaction_date <= ?
          AND transactions.transaction_type != 'adjustment'
          AND (
              transactions.source_account_id = ?
              OR transactions.destination_account_id = ?
          )
        """,
        (start_date, end_date, account_id, account_id),
    ).fetchall()
    contribution_minor = 0
    withdrawal_minor = 0
    for row in rows:
        if row["destination_account_id"] == account_id:
            if row["source_account_type"] not in INVESTMENT_ACCOUNT_TYPES:
                contribution_minor += row["inflow_minor"] or 0
        elif row["source_account_id"] == account_id:
            if row["destination_account_type"] not in INVESTMENT_ACCOUNT_TYPES:
                withdrawal_minor += row["outflow_minor"] or 0
    return contribution_minor, withdrawal_minor


def investment_valuation_profit_minor(
    connection,
    account_id: int,
    start_date: str,
    end_date: str,
) -> int:
    row = connection.execute(
        """
        SELECT
            COALESCE(SUM(
                CASE
                    WHEN transactions.destination_account_id = investment_valuations.account_id
                    THEN transactions.inflow_minor
                    ELSE -transactions.outflow_minor
                END
            ), 0) AS profit_minor
        FROM investment_valuations
        JOIN transactions
            ON transactions.id = investment_valuations.generated_transaction_id
        WHERE investment_valuations.account_id = ?
          AND investment_valuations.valuation_date > ?
          AND investment_valuations.valuation_date <= ?
        """,
        (account_id, start_date, end_date),
    ).fetchone()
    return row["profit_minor"]


def investment_report_accounts(connection, account_id: int | None = None):
    if account_id is not None:
        return [get_investment_account(connection, account_id)]
    return connection.execute(
        """
        SELECT accounts.*, currencies.decimal_places
        FROM accounts
        JOIN currencies ON currencies.code = accounts.currency_code
        WHERE accounts.account_class = 'asset'
          AND accounts.account_type IN ('investment_asset', 'broker_cash')
        ORDER BY accounts.is_active DESC, accounts.currency_code, accounts.name
        """
    ).fetchall()


def account_balance_for_report(connection, account, on_date: str) -> int:
    if on_date < account["opening_balance_date"]:
        return 0
    return account_balance_as_of(connection, account, on_date)


def account_balance_rows_as_of(connection, on_date: str, active_only: bool = True):
    active_clause = "WHERE accounts.is_active = 1" if active_only else ""
    return connection.execute(
        f"""
        WITH movements AS (
            SELECT
                destination_account_id AS account_id,
                inflow_minor AS inflow_minor,
                0 AS outflow_minor
            FROM transactions
            WHERE destination_account_id IS NOT NULL
              AND transaction_date <= ?

            UNION ALL

            SELECT
                source_account_id AS account_id,
                0 AS inflow_minor,
                outflow_minor AS outflow_minor
            FROM transactions
            WHERE source_account_id IS NOT NULL
              AND transaction_date <= ?
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
            accounts.*,
            currencies.decimal_places,
            COALESCE(movement_totals.total_inflow_minor, 0) AS total_inflow_minor,
            COALESCE(movement_totals.total_outflow_minor, 0) AS total_outflow_minor,
            CASE accounts.account_class
                WHEN 'asset' THEN
                    CASE
                        WHEN accounts.opening_balance_date <= ?
                        THEN accounts.opening_balance_minor
                        ELSE 0
                    END
                    + COALESCE(movement_totals.total_inflow_minor, 0)
                    - COALESCE(movement_totals.total_outflow_minor, 0)
                WHEN 'liability' THEN
                    CASE
                        WHEN accounts.opening_balance_date <= ?
                        THEN accounts.opening_balance_minor
                        ELSE 0
                    END
                    + COALESCE(movement_totals.total_outflow_minor, 0)
                    - COALESCE(movement_totals.total_inflow_minor, 0)
            END AS balance_minor
        FROM accounts
        JOIN currencies ON currencies.code = accounts.currency_code
        LEFT JOIN movement_totals ON movement_totals.account_id = accounts.id
        {active_clause}
        ORDER BY accounts.currency_code, accounts.account_class, accounts.name
        """,
        (on_date, on_date, on_date, on_date),
    ).fetchall()


def amount_from_minor(amount_minor: int, decimal_places: int) -> Decimal:
    return Decimal(amount_minor) / (Decimal(10) ** decimal_places)


def calculate_base_currency_totals(
    account_rows,
    rate_row,
) -> list[dict[str, Any]]:
    totals = {
        "CNY": {"assets": Decimal(0), "liabilities": Decimal(0)},
        "HKD": {"assets": Decimal(0), "liabilities": Decimal(0)},
    }
    missing = {"CNY": set(), "HKD": set()}
    rate = (
        {
            "usd_cny": Decimal(rate_row["usd_cny_rate"]),
            "hkd_cny": Decimal(rate_row["hkd_cny_rate"]),
        }
        if rate_row is not None
        else None
    )

    for row in account_rows:
        if row["balance_minor"] == 0:
            continue
        total_key = "assets" if row["account_class"] == "asset" else "liabilities"
        amount = amount_from_minor(row["balance_minor"], row["decimal_places"])
        currency_code = row["currency_code"]
        if currency_code == "CNY":
            totals["CNY"][total_key] += amount
            if rate is None:
                missing["HKD"].add("CNY")
            else:
                totals["HKD"][total_key] += amount / rate["hkd_cny"]
        elif currency_code == "HKD":
            totals["HKD"][total_key] += amount
            if rate is None:
                missing["CNY"].add("HKD")
            else:
                totals["CNY"][total_key] += amount * rate["hkd_cny"]
        elif currency_code == "USD":
            if rate is None:
                missing["CNY"].add("USD")
                missing["HKD"].add("USD")
            else:
                totals["CNY"][total_key] += amount * rate["usd_cny"]
                totals["HKD"][total_key] += amount * rate["usd_cny"] / rate["hkd_cny"]
        else:
            missing["CNY"].add(currency_code)
            missing["HKD"].add(currency_code)

    rate_metadata = (
        {
            "rate_date": rate_row["rate_date"],
            "market_date": rate_row["market_date"],
            "source": rate_row["source"],
        }
        if rate_row is not None
        else None
    )
    return [
        {
            "currency_code": currency_code,
            "total_assets": format_decimal(totals[currency_code]["assets"], 2),
            "total_liabilities": format_decimal(
                totals[currency_code]["liabilities"], 2
            ),
            "net_worth": format_decimal(
                totals[currency_code]["assets"] - totals[currency_code]["liabilities"],
                2,
            ),
            "missing_currencies": sorted(missing[currency_code]),
            "is_complete": not missing[currency_code],
            "rate": rate_metadata,
        }
        for currency_code in ("CNY", "HKD")
    ]


def month_sequence(start_month: str, end_month: str) -> list[str]:
    start = datetime.strptime(start_month, "%Y-%m").date().replace(day=1)
    end = datetime.strptime(end_month, "%Y-%m").date().replace(day=1)
    if start > end:
        raise HTTPException(status_code=422, detail="开始月份不能晚于结束月份。")
    result = []
    current = start
    while current <= end:
        result.append(current.strftime("%Y-%m"))
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    return result


def serialize_account(row) -> dict[str, Any]:
    decimal_places = get_currency_decimal_places_for_row(row)
    row_keys = set(row.keys())
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "account_class": row["account_class"],
        "account_type": row["account_type"],
        "currency_code": row["currency_code"],
        "institution": row["institution"],
        "directory_key": (
            row["directory_key"]
            if "directory_key" in row_keys and row["directory_key"]
            else "other"
        ),
        "directory_name": (
            row["directory_name"]
            if "directory_name" in row_keys and row["directory_name"]
            else "其他账户"
        ),
        "directory_sort_order": (
            row["directory_sort_order"]
            if "directory_sort_order" in row_keys
            and row["directory_sort_order"] is not None
            else 90
        ),
        "is_active": bool(row["is_active"]),
        "balance": format_minor(row["balance_minor"], decimal_places),
        "balance_minor": row["balance_minor"],
    }


def get_currency_decimal_places_for_row(row) -> int:
    return row["decimal_places"]


def activity_start_date(snapshot_date: date, days: int) -> str:
    return (snapshot_date - timedelta(days=days - 1)).isoformat()


def serialize_recent_activity(
    record_count: int | None,
    last_transaction_date: str | None,
) -> dict[str, Any]:
    return {
        "record_count": int(record_count or 0),
        "last_transaction_date": last_transaction_date,
    }


def serialize_account_recent_activity(
    record_count: int | None,
    last_transaction_date: str | None,
    recent_record_count: int | None,
) -> dict[str, Any]:
    return {
        **serialize_recent_activity(record_count, last_transaction_date),
        "recent_record_count": int(recent_record_count or 0),
    }


def empty_account_recent_activity() -> dict[str, dict[str, Any]]:
    return {
        key: serialize_account_recent_activity(0, None, 0)
        for key in FREQUENT_ACCOUNT_ACTIVITY_KEYS
    }


def list_account_recent_activity(
    connection,
    start_date: str,
    recent_start_date: str,
) -> dict[int, dict[str, Any]]:
    rows = connection.execute(
        """
        WITH account_legs AS (
            SELECT
                source_account_id AS account_id,
                transaction_type,
                transaction_date,
                'source' AS leg_direction
            FROM transactions
            WHERE source_account_id IS NOT NULL
              AND transaction_date >= ?

            UNION ALL

            SELECT
                destination_account_id AS account_id,
                transaction_type,
                transaction_date,
                'destination' AS leg_direction
            FROM transactions
            WHERE destination_account_id IS NOT NULL
              AND transaction_date >= ?
        )
        SELECT
            account_id,
            COUNT(*) AS overall_record_count,
            MAX(transaction_date) AS overall_last_transaction_date,
            SUM(CASE
                WHEN transaction_date >= ?
                THEN 1 ELSE 0
            END) AS overall_recent_record_count,
            SUM(CASE
                WHEN transaction_type = 'expense'
                 AND leg_direction = 'source'
                THEN 1 ELSE 0
            END) AS expense_source_record_count,
            MAX(CASE
                WHEN transaction_type = 'expense'
                 AND leg_direction = 'source'
                THEN transaction_date
            END) AS expense_source_last_transaction_date,
            SUM(CASE
                WHEN transaction_date >= ?
                 AND transaction_type = 'expense'
                 AND leg_direction = 'source'
                THEN 1 ELSE 0
            END) AS expense_source_recent_record_count,
            SUM(CASE
                WHEN transaction_type = 'income'
                 AND leg_direction = 'destination'
                THEN 1 ELSE 0
            END) AS income_destination_record_count,
            MAX(CASE
                WHEN transaction_type = 'income'
                 AND leg_direction = 'destination'
                THEN transaction_date
            END) AS income_destination_last_transaction_date,
            SUM(CASE
                WHEN transaction_date >= ?
                 AND transaction_type = 'income'
                 AND leg_direction = 'destination'
                THEN 1 ELSE 0
            END) AS income_destination_recent_record_count,
            SUM(CASE
                WHEN transaction_type IN (
                    'transfer',
                    'fx_exchange',
                    'broker_deposit',
                    'broker_withdrawal'
                )
                 AND leg_direction = 'source'
                THEN 1 ELSE 0
            END) AS movement_source_record_count,
            MAX(CASE
                WHEN transaction_type IN (
                    'transfer',
                    'fx_exchange',
                    'broker_deposit',
                    'broker_withdrawal'
                )
                 AND leg_direction = 'source'
                THEN transaction_date
            END) AS movement_source_last_transaction_date,
            SUM(CASE
                WHEN transaction_date >= ?
                 AND transaction_type IN (
                    'transfer',
                    'fx_exchange',
                    'broker_deposit',
                    'broker_withdrawal'
                )
                 AND leg_direction = 'source'
                THEN 1 ELSE 0
            END) AS movement_source_recent_record_count,
            SUM(CASE
                WHEN transaction_type IN (
                    'transfer',
                    'fx_exchange',
                    'broker_deposit',
                    'broker_withdrawal'
                )
                 AND leg_direction = 'destination'
                THEN 1 ELSE 0
            END) AS movement_destination_record_count,
            MAX(CASE
                WHEN transaction_type IN (
                    'transfer',
                    'fx_exchange',
                    'broker_deposit',
                    'broker_withdrawal'
                )
                 AND leg_direction = 'destination'
                THEN transaction_date
            END) AS movement_destination_last_transaction_date,
            SUM(CASE
                WHEN transaction_date >= ?
                 AND transaction_type IN (
                    'transfer',
                    'fx_exchange',
                    'broker_deposit',
                    'broker_withdrawal'
                )
                 AND leg_direction = 'destination'
                THEN 1 ELSE 0
            END) AS movement_destination_recent_record_count
        FROM account_legs
        GROUP BY account_id
        """,
        (
            start_date,
            start_date,
            recent_start_date,
            recent_start_date,
            recent_start_date,
            recent_start_date,
            recent_start_date,
        ),
    ).fetchall()
    return {
        row["account_id"]: {
            "overall": serialize_account_recent_activity(
                row["overall_record_count"],
                row["overall_last_transaction_date"],
                row["overall_recent_record_count"],
            ),
            "expense_source": serialize_account_recent_activity(
                row["expense_source_record_count"],
                row["expense_source_last_transaction_date"],
                row["expense_source_recent_record_count"],
            ),
            "income_destination": serialize_account_recent_activity(
                row["income_destination_record_count"],
                row["income_destination_last_transaction_date"],
                row["income_destination_recent_record_count"],
            ),
            "movement_source": serialize_account_recent_activity(
                row["movement_source_record_count"],
                row["movement_source_last_transaction_date"],
                row["movement_source_recent_record_count"],
            ),
            "movement_destination": serialize_account_recent_activity(
                row["movement_destination_record_count"],
                row["movement_destination_last_transaction_date"],
                row["movement_destination_recent_record_count"],
            ),
        }
        for row in rows
    }


def list_category_recent_activity(
    connection,
    start_date: str,
    recent_start_date: str,
) -> dict[int, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            category_id,
            COUNT(*) AS record_count,
            SUM(CASE WHEN transaction_date >= ? THEN 1 ELSE 0 END)
                AS recent_record_count,
            MAX(transaction_date) AS last_transaction_date
        FROM transactions
        WHERE category_id IS NOT NULL
          AND transaction_date >= ?
        GROUP BY category_id
        """,
        (recent_start_date, start_date),
    ).fetchall()
    return {
        row["category_id"]: serialize_account_recent_activity(
            row["record_count"],
            row["last_transaction_date"],
            row["recent_record_count"],
        )
        for row in rows
    }


def ensure_daily_frequent_snapshot(connection, snapshot_date: date) -> None:
    snapshot_date_text = snapshot_date.isoformat()
    connection.execute("BEGIN IMMEDIATE")
    existing = connection.execute(
        """
        SELECT rules_version
        FROM daily_frequent_snapshots
        WHERE snapshot_date = ?
        """,
        (snapshot_date_text,),
    ).fetchone()
    if (
        existing is not None
        and existing["rules_version"] == FREQUENT_SNAPSHOT_RULE_VERSION
    ):
        return
    if existing is not None:
        connection.execute(
            "DELETE FROM daily_frequent_snapshots WHERE snapshot_date = ?",
            (snapshot_date_text,),
        )

    account_activity = list_account_recent_activity(
        connection,
        activity_start_date(snapshot_date, ACCOUNT_ACTIVITY_WINDOW_DAYS),
        activity_start_date(snapshot_date, ACCOUNT_RECENT_WINDOW_DAYS),
    )
    category_activity = list_category_recent_activity(
        connection,
        activity_start_date(snapshot_date, CATEGORY_ACTIVITY_WINDOW_DAYS),
        activity_start_date(snapshot_date, CATEGORY_RECENT_WINDOW_DAYS),
    )

    connection.execute(
        """
        INSERT INTO daily_frequent_snapshots (snapshot_date, rules_version)
        VALUES (?, ?)
        """,
        (snapshot_date_text, FREQUENT_SNAPSHOT_RULE_VERSION),
    )
    account_rows = [
        (
            snapshot_date_text,
            account_id,
            activity_key,
            activity["record_count"],
            activity["recent_record_count"],
            activity["last_transaction_date"],
        )
        for account_id, activity_by_key in account_activity.items()
        for activity_key, activity in activity_by_key.items()
        if activity["record_count"] > 0
    ]
    if account_rows:
        connection.executemany(
            """
            INSERT INTO daily_account_frequent_activity (
                snapshot_date, account_id, activity_key,
                record_count, recent_record_count, last_transaction_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            account_rows,
        )

    category_rows = [
        (
            snapshot_date_text,
            category_id,
            activity["record_count"],
            activity["recent_record_count"],
            activity["last_transaction_date"],
        )
        for category_id, activity in category_activity.items()
        if activity["record_count"] > 0
    ]
    if category_rows:
        connection.executemany(
            """
            INSERT INTO daily_category_frequent_activity (
                snapshot_date, category_id, record_count,
                recent_record_count, last_transaction_date
            ) VALUES (?, ?, ?, ?, ?)
            """,
            category_rows,
        )


def list_daily_account_frequent_activity(
    connection,
    snapshot_date: str,
) -> dict[int, dict[str, dict[str, Any]]]:
    rows = connection.execute(
        """
        SELECT
            account_id,
            activity_key,
            record_count,
            recent_record_count,
            last_transaction_date
        FROM daily_account_frequent_activity
        WHERE snapshot_date = ?
        """,
        (snapshot_date,),
    ).fetchall()
    result: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        activity = result.setdefault(row["account_id"], empty_account_recent_activity())
        activity[row["activity_key"]] = serialize_account_recent_activity(
            row["record_count"],
            row["last_transaction_date"],
            row["recent_record_count"],
        )
    return result


def list_daily_category_frequent_activity(
    connection,
    snapshot_date: str,
) -> dict[int, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT category_id, record_count, recent_record_count,
               last_transaction_date
        FROM daily_category_frequent_activity
        WHERE snapshot_date = ?
        """,
        (snapshot_date,),
    ).fetchall()
    return {
        row["category_id"]: serialize_account_recent_activity(
            row["record_count"],
            row["last_transaction_date"],
            row["recent_record_count"],
        )
        for row in rows
    }


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(
        STATIC_DIR / "compass-rose.svg",
        media_type="image/svg+xml",
    )


@app.get("/overview", include_in_schema=False)
def overview():
    return FileResponse(STATIC_DIR / "overview.html", headers={"Cache-Control": "no-store"})


@app.get("/accounts", include_in_schema=False)
def accounts_page():
    return FileResponse(STATIC_DIR / "accounts.html", headers={"Cache-Control": "no-store"})


@app.get("/investments", include_in_schema=False)
def investments_page():
    return FileResponse(STATIC_DIR / "investment.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/accounts")
def list_accounts(include_inactive: bool = False):
    snapshot_date = today_in_shanghai().isoformat()
    with connect() as connection:
        ensure_daily_frequent_snapshot(connection, date.fromisoformat(snapshot_date))
        active_clause = "" if include_inactive else "WHERE account_balances.is_active = 1"
        rows = connection.execute(
            f"""
            SELECT account_balances.*, currencies.decimal_places,
                   account_source.directory_key,
                   account_directories.name AS directory_name,
                   account_directories.sort_order AS directory_sort_order
            FROM account_balances
            JOIN currencies ON currencies.code = account_balances.currency_code
            JOIN accounts AS account_source ON account_source.id = account_balances.id
            LEFT JOIN account_directories
              ON account_directories.key = account_source.directory_key
            {active_clause}
            ORDER BY account_balances.currency_code,
                     account_balances.account_class,
                     account_balances.name
            """,
        ).fetchall()
        recent_activity = list_daily_account_frequent_activity(
            connection,
            snapshot_date,
        )
    result = [serialize_account(row) for row in rows]
    for account in result:
        account["activity"] = recent_activity.get(
            account["id"],
            empty_account_recent_activity(),
        )
    return result


def account_detail_direction(transaction_type: str, is_source: bool) -> str:
    if transaction_type == "expense":
        return "支出"
    if transaction_type == "income":
        return "入账"
    if transaction_type == "adjustment":
        return "调减" if is_source else "调增"
    return "转出" if is_source else "转入"


@app.get("/api/accounts/{account_id}/detail")
def account_detail(
    account_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
    transaction_type: str | None = None,
    category_id: int | None = None,
    keyword: str | None = None,
    manual_adjustments_only: bool = False,
    flagged_only: bool = False,
    limit: int = 50,
    offset: int = 0,
):
    start_date = validate_optional_date(start_date, "开始日期")
    end_date = validate_optional_date(end_date, "结束日期")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")
    if transaction_type and transaction_type not in TRANSACTION_TYPE_LABELS:
        raise HTTPException(status_code=422, detail="流水类型不支持。")
    if category_id is not None and category_id < 1:
        raise HTTPException(status_code=422, detail="分类编号必须大于零。")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="每页数量必须在 1 到 200 之间。")
    if offset < 0:
        raise HTTPException(status_code=422, detail="分页起点不能小于零。")
    keyword = clean_optional_text(keyword)
    if keyword and len(keyword) > 120:
        raise HTTPException(status_code=422, detail="关键词不能超过 120 个字符。")

    with connect() as connection:
        account = connection.execute(
            """
            SELECT account_balances.*, currencies.decimal_places
            FROM account_balances
            JOIN currencies ON currencies.code = account_balances.currency_code
            WHERE account_balances.id = ?
            """,
            (account_id,),
        ).fetchone()
        if account is None:
            raise HTTPException(status_code=404, detail="账户不存在。")

        filters = []
        filter_parameters: list[Any] = []
        if start_date:
            filters.append("running.transaction_date >= ?")
            filter_parameters.append(start_date)
        if end_date:
            filters.append("running.transaction_date <= ?")
            filter_parameters.append(end_date)
        if transaction_type:
            filters.append("running.transaction_type = ?")
            filter_parameters.append(transaction_type)
        if category_id is not None:
            filters.append("running.category_id = ?")
            filter_parameters.append(category_id)
        if manual_adjustments_only:
            filters.append(
                "running.transaction_type = 'adjustment' "
                "AND running.investment_valuation_id IS NULL"
            )
        if flagged_only:
            filters.append("running.flag_id IS NOT NULL")
        if keyword:
            filters.append(
                """
                (
                    COALESCE(running.category_name, '') LIKE ?
                    OR COALESCE(running.counterparty, '') LIKE ?
                    OR COALESCE(running.note, '') LIKE ?
                    OR COALESCE(running.source_account_name, '') LIKE ?
                    OR COALESCE(running.destination_account_name, '') LIKE ?
                )
                """
            )
            search_term = f"%{keyword}%"
            filter_parameters.extend([search_term] * 5)
        filter_clause = f" WHERE {' AND '.join(filters)}" if filters else ""

        ledger_query = """
            WITH ledger AS (
                SELECT
                    transactions.id,
                    transactions.transaction_date,
                    transactions.transaction_type,
                    transactions.source_account_id,
                    transactions.destination_account_id,
                    transactions.outflow_minor,
                    transactions.inflow_minor,
                    transactions.category_id,
                    transactions.counterparty,
                    transactions.note,
                    source.name AS source_account_name,
                    destination.name AS destination_account_name,
                    categories.name AS category_name,
                    flags.id AS flag_id,
                    flags.flag_type,
                    flags.note AS flag_note,
                    investment_valuations.id AS investment_valuation_id,
                    CASE
                        WHEN ? = 'asset' THEN
                            CASE WHEN transactions.destination_account_id = ?
                                THEN COALESCE(transactions.inflow_minor, 0) ELSE 0 END
                            - CASE WHEN transactions.source_account_id = ?
                                THEN COALESCE(transactions.outflow_minor, 0) ELSE 0 END
                        ELSE
                            CASE WHEN transactions.source_account_id = ?
                                THEN COALESCE(transactions.outflow_minor, 0) ELSE 0 END
                            - CASE WHEN transactions.destination_account_id = ?
                                THEN COALESCE(transactions.inflow_minor, 0) ELSE 0 END
                    END AS balance_change_minor
                FROM transactions
                LEFT JOIN accounts AS source ON source.id = transactions.source_account_id
                LEFT JOIN accounts AS destination ON destination.id = transactions.destination_account_id
                LEFT JOIN categories ON categories.id = transactions.category_id
                LEFT JOIN transaction_flags AS flags ON flags.transaction_id = transactions.id
                LEFT JOIN investment_valuations
                    ON investment_valuations.generated_transaction_id = transactions.id
                WHERE transactions.source_account_id = ?
                   OR transactions.destination_account_id = ?
            ),
            running AS (
                SELECT
                    ledger.*,
                    ? + SUM(balance_change_minor) OVER (
                        ORDER BY transaction_date ASC, id ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS balance_after_minor
                FROM ledger
            )
        """
        ledger_parameters = [
            account["account_class"],
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account["opening_balance_minor"],
        ]
        total_row = connection.execute(
            f"{ledger_query} SELECT COUNT(*) AS total FROM running{filter_clause}",
            ledger_parameters + filter_parameters,
        ).fetchone()
        transaction_rows = connection.execute(
            f"""
            {ledger_query}
            SELECT *
            FROM running
            {filter_clause}
            ORDER BY transaction_date DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            ledger_parameters + filter_parameters + [limit, offset],
        ).fetchall()
        reconciliation = latest_reconciliation_status(connection, account)

    decimal_places = account["decimal_places"]
    transactions = []
    for row in transaction_rows:
        is_source = row["source_account_id"] == account_id
        amount_minor = row["outflow_minor"] if is_source else row["inflow_minor"]
        transactions.append(
            {
                "id": row["id"],
                "transaction_date": row["transaction_date"],
                "transaction_type": row["transaction_type"],
                "direction": account_detail_direction(row["transaction_type"], is_source),
                "counter_account_name": (
                    row["destination_account_name"] if is_source else row["source_account_name"]
                )
                or "外部",
                "inflow_amount": (
                    format_minor(row["inflow_minor"], decimal_places)
                    if not is_source and row["inflow_minor"] is not None
                    else None
                ),
                "outflow_amount": (
                    format_minor(row["outflow_minor"], decimal_places)
                    if is_source and row["outflow_minor"] is not None
                    else None
                ),
                "amount": format_minor(amount_minor, decimal_places),
                "balance_after": format_minor(row["balance_after_minor"], decimal_places),
                "category_name": row["category_name"],
                "counterparty": row["counterparty"],
                "note": row["note"],
                "transaction_type_label": transaction_type_label(
                    row["transaction_type"],
                    row["investment_valuation_id"],
                ),
                "is_manual_adjustment": (
                    row["transaction_type"] == "adjustment"
                    and row["investment_valuation_id"] is None
                ),
                "is_investment_valuation": row["investment_valuation_id"] is not None,
                "flag": (
                    {
                        "flag_type": row["flag_type"],
                        "note": row["flag_note"],
                    }
                    if row["flag_id"] is not None
                    else None
                ),
            }
        )

    return {
        "account": {
            "id": account["id"],
            "code": account["code"],
            "name": account["name"],
            "account_class": account["account_class"],
            "account_class_label": "资产" if account["account_class"] == "asset" else "负债",
            "account_type": account["account_type"],
            "currency_code": account["currency_code"],
            "institution": account["institution"],
            "is_active": bool(account["is_active"]),
            "opening_balance": format_minor(
                account["opening_balance_minor"],
                decimal_places,
            ),
            "opening_balance_date": account["opening_balance_date"],
            "total_inflow": format_minor(account["total_inflow_minor"], decimal_places),
            "total_outflow": format_minor(account["total_outflow_minor"], decimal_places),
            "current_balance": format_minor(account["balance_minor"], decimal_places),
            "note": account["note"],
            "reconciliation": reconciliation,
        },
        "filters": {
            "start_date": start_date,
            "end_date": end_date,
            "transaction_type": transaction_type,
            "category_id": category_id,
            "keyword": keyword,
            "manual_adjustments_only": manual_adjustments_only,
            "flagged_only": flagged_only,
        },
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total_row["total"],
        },
        "transactions": annotate_transactions(transactions),
    }


@app.get("/api/accounts/{account_id}/balance-at")
def account_balance_at(account_id: int, on_date: str):
    on_date = validate_optional_date(on_date, "日期")
    if on_date is None:
        raise HTTPException(status_code=422, detail="日期不能为空。")
    with connect() as connection:
        account = get_account_balance_row(connection, account_id)
        balance_minor = account_balance_as_of(connection, account, on_date)
        later_row = connection.execute(
            """
            SELECT MAX(transaction_date) AS last_transaction_date
            FROM transactions
            WHERE (source_account_id = ? OR destination_account_id = ?)
              AND transaction_date > ?
            """,
            (account_id, account_id, on_date),
        ).fetchone()
    return {
        "account_id": account_id,
        "on_date": on_date,
        "currency_code": account["currency_code"],
        "decimal_places": account["decimal_places"],
        "book_balance": format_minor(balance_minor, account["decimal_places"]),
        "has_later_transactions": later_row["last_transaction_date"] is not None,
        "last_later_transaction_date": later_row["last_transaction_date"],
    }


@app.post("/api/accounts/{account_id}/reconciliations")
def create_reconciliation(account_id: int, reconciliation: ReconciliationCreate):
    backup_path = create_backup()
    with connect() as connection:
        account = get_account_balance_row(connection, account_id)
        reconciliation_date = reconciliation.reconciliation_date.isoformat()
        actual_balance_minor = amount_to_minor(
            reconciliation.actual_balance,
            account["decimal_places"],
        )
        book_balance_minor = account_balance_as_of(connection, account, reconciliation_date)
        before_row = connection.execute(
            """
            SELECT account_id, reconciliation_date, actual_balance_minor, note
            FROM account_reconciliations
            WHERE account_id = ? AND reconciliation_date = ?
            """,
            (account_id, reconciliation_date),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO account_reconciliations (
                account_id, reconciliation_date, actual_balance_minor, note
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, reconciliation_date) DO UPDATE SET
                actual_balance_minor = excluded.actual_balance_minor,
                note = excluded.note
            """,
            (
                account_id,
                reconciliation_date,
                actual_balance_minor,
                clean_optional_text(reconciliation.note),
            ),
        )
        after_row = connection.execute(
            """
            SELECT account_id, reconciliation_date, actual_balance_minor, note
            FROM account_reconciliations
            WHERE account_id = ? AND reconciliation_date = ?
            """,
            (account_id, reconciliation_date),
        ).fetchone()
        write_audit_log(
            connection,
            entity_type="reconciliation",
            entity_id=account_id,
            action="reconcile",
            backup_file=backup_path.name,
            before=dict(before_row) if before_row else None,
            after=dict(after_row),
        )
    return {
        "account_id": account_id,
        "reconciliation_date": reconciliation_date,
        "currency_code": account["currency_code"],
        "actual_balance": format_minor(actual_balance_minor, account["decimal_places"]),
        "book_balance": format_minor(book_balance_minor, account["decimal_places"]),
        "difference": format_minor(
            actual_balance_minor - book_balance_minor,
            account["decimal_places"],
        ),
    }


@app.get("/api/accounts/{account_id}/reconciliations")
def list_reconciliations(account_id: int):
    with connect() as connection:
        account = get_account_balance_row(connection, account_id)
        rows = connection.execute(
            """
            SELECT reconciliation_date, actual_balance_minor, note
            FROM account_reconciliations
            WHERE account_id = ?
            ORDER BY reconciliation_date DESC
            """,
            (account_id,),
        ).fetchall()
        result = []
        for row in rows:
            book_balance_minor = account_balance_as_of(
                connection,
                account,
                row["reconciliation_date"],
            )
            result.append(
                {
                    "reconciliation_date": row["reconciliation_date"],
                    "actual_balance": format_minor(
                        row["actual_balance_minor"],
                        account["decimal_places"],
                    ),
                    "book_balance": format_minor(
                        book_balance_minor,
                        account["decimal_places"],
                    ),
                    "difference": format_minor(
                        row["actual_balance_minor"] - book_balance_minor,
                        account["decimal_places"],
                    ),
                    "note": row["note"],
                }
            )
    return result


@app.post("/api/accounts/{account_id}/adjustments", status_code=201)
def create_balance_adjustment(
    account_id: int,
    adjustment: BalanceAdjustmentCreate,
):
    reason = clean_required_text(adjustment.reason, "调整原因")
    adjustment_date = adjustment.adjustment_date.isoformat()
    backup_path = create_backup()
    with connect() as connection:
        account = get_account_balance_row(connection, account_id)
        actual_balance_minor = amount_to_minor(
            adjustment.actual_balance,
            account["decimal_places"],
        )
        book_balance_minor = account_balance_as_of(connection, account, adjustment_date)
        difference_minor = actual_balance_minor - book_balance_minor
        reconciliation_before = connection.execute(
            """
            SELECT account_id, reconciliation_date, actual_balance_minor, note
            FROM account_reconciliations
            WHERE account_id = ? AND reconciliation_date = ?
            """,
            (account_id, adjustment_date),
        ).fetchone()
        adjustment_snapshot = None
        if difference_minor != 0:
            source_account_id = None
            destination_account_id = None
            outflow_minor = None
            inflow_minor = None
            if account["account_class"] == "asset":
                if difference_minor > 0:
                    destination_account_id = account_id
                    inflow_minor = difference_minor
                else:
                    source_account_id = account_id
                    outflow_minor = -difference_minor
            elif difference_minor > 0:
                source_account_id = account_id
                outflow_minor = difference_minor
            else:
                destination_account_id = account_id
                inflow_minor = -difference_minor

            adjustment_note = f"余额调整：{reason}"
            extra_note = clean_optional_text(adjustment.note)
            if extra_note:
                adjustment_note = f"{adjustment_note}；{extra_note}"
            if len(adjustment_note) > 500:
                raise HTTPException(status_code=422, detail="调整原因和备注合计不能超过 500 个字符。")
            cursor = connection.execute(
                """
                INSERT INTO transactions (
                    transaction_date,
                    transaction_type,
                    source_account_id,
                    destination_account_id,
                    outflow_minor,
                    inflow_minor,
                    note
                ) VALUES (?, 'adjustment', ?, ?, ?, ?, ?)
                """,
                (
                    adjustment_date,
                    source_account_id,
                    destination_account_id,
                    outflow_minor,
                    inflow_minor,
                    adjustment_note,
                ),
            )
            adjustment_snapshot = fetch_transaction_snapshot(connection, cursor.lastrowid)
            write_audit_log(
                connection,
                entity_type="transaction",
                entity_id=cursor.lastrowid,
                action="create",
                backup_file=backup_path.name,
                reason=reason,
                after=adjustment_snapshot,
            )

        connection.execute(
            """
            INSERT INTO account_reconciliations (
                account_id, reconciliation_date, actual_balance_minor, note
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, reconciliation_date) DO UPDATE SET
                actual_balance_minor = excluded.actual_balance_minor,
                note = excluded.note
            """,
            (account_id, adjustment_date, actual_balance_minor, clean_optional_text(adjustment.note)),
        )
        reconciliation_after = connection.execute(
            """
            SELECT account_id, reconciliation_date, actual_balance_minor, note
            FROM account_reconciliations
            WHERE account_id = ? AND reconciliation_date = ?
            """,
            (account_id, adjustment_date),
        ).fetchone()
        write_audit_log(
            connection,
            entity_type="reconciliation",
            entity_id=account_id,
            action="reconcile",
            backup_file=backup_path.name,
            reason=reason,
            before=dict(reconciliation_before) if reconciliation_before else None,
            after=dict(reconciliation_after),
        )

    return {
        "account_id": account_id,
        "adjustment_date": adjustment_date,
        "currency_code": account["currency_code"],
        "book_balance_before": format_minor(book_balance_minor, account["decimal_places"]),
        "actual_balance": format_minor(actual_balance_minor, account["decimal_places"]),
        "difference": format_minor(difference_minor, account["decimal_places"]),
        "adjustment_transaction_id": (
            adjustment_snapshot["id"] if adjustment_snapshot is not None else None
        ),
    }


@app.put("/api/accounts/{account_id}/opening-balance")
def update_opening_balance(account_id: int, opening_balance: OpeningBalanceUpdate):
    reason = clean_required_text(opening_balance.reason, "修正原因")
    backup_path = create_backup()
    with connect() as connection:
        account = get_account_balance_row(connection, account_id)
        opening_balance_date = opening_balance.opening_balance_date.isoformat()
        earliest_activity = connection.execute(
            """
            SELECT MIN(transaction_date) AS earliest_date
            FROM transactions
            WHERE source_account_id = ? OR destination_account_id = ?
            """,
            (account_id, account_id),
        ).fetchone()
        earliest_reconciliation = connection.execute(
            """
            SELECT MIN(reconciliation_date) AS earliest_date
            FROM account_reconciliations
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        earliest_dates = [
            row["earliest_date"]
            for row in (earliest_activity, earliest_reconciliation)
            if row["earliest_date"] is not None
        ]
        if earliest_dates and opening_balance_date > min(earliest_dates):
            raise HTTPException(
                status_code=422,
                detail="期初日期不能晚于该账户已有流水或对账记录。",
            )
        before = fetch_account_snapshot(connection, account_id)
        opening_balance_minor = amount_to_minor(
            opening_balance.opening_balance,
            account["decimal_places"],
        )
        connection.execute(
            """
            UPDATE accounts
            SET opening_balance_minor = ?, opening_balance_date = ?
            WHERE id = ?
            """,
            (opening_balance_minor, opening_balance_date, account_id),
        )
        after = fetch_account_snapshot(connection, account_id)
        write_audit_log(
            connection,
            entity_type="account",
            entity_id=account_id,
            action="update",
            backup_file=backup_path.name,
            reason=reason,
            before=before,
            after=after,
        )
    return {
        "id": account_id,
        "opening_balance": format_minor(opening_balance_minor, account["decimal_places"]),
        "opening_balance_date": opening_balance_date,
    }


@app.get("/api/investments/accounts")
def list_investment_accounts():
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT account_balances.*, currencies.decimal_places,
                   account_source.directory_key,
                   account_directories.name AS directory_name,
                   account_directories.sort_order AS directory_sort_order
            FROM account_balances
            JOIN currencies ON currencies.code = account_balances.currency_code
            JOIN accounts AS account_source ON account_source.id = account_balances.id
            LEFT JOIN account_directories
              ON account_directories.key = account_source.directory_key
            WHERE account_balances.account_class = 'asset'
              AND account_balances.account_type IN ('investment_asset', 'broker_cash')
            ORDER BY account_balances.is_active DESC, account_balances.currency_code,
                     account_balances.name
            """
        ).fetchall()
    return [serialize_account(row) for row in rows]


@app.get("/api/investments/valuations")
def list_investment_valuations(account_id: int | None = None, limit: int = 100):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="每页数量必须在 1 到 500 之间。")
    with connect() as connection:
        if account_id is not None:
            get_investment_account(connection, account_id)
        query = """
            SELECT investment_valuations.id
            FROM investment_valuations
            WHERE (? IS NULL OR investment_valuations.account_id = ?)
            ORDER BY investment_valuations.valuation_date DESC, investment_valuations.id DESC
            LIMIT ?
        """
        rows = connection.execute(query, (account_id, account_id, limit)).fetchall()
        snapshots = [
            fetch_investment_valuation_snapshot(connection, row["id"])
            for row in rows
        ]
    return [serialize_investment_valuation(snapshot) for snapshot in snapshots]


@app.put("/api/investments/accounts/{account_id}/valuations/{valuation_date}")
def upsert_investment_valuation(
    account_id: int,
    valuation_date: str,
    valuation: InvestmentValuationUpsert,
):
    valuation_date = validate_optional_date(valuation_date, "估值日期")
    if valuation_date is None:
        raise HTTPException(status_code=422, detail="估值日期不能为空。")
    with connect() as connection:
        account = get_investment_account(connection, account_id)
        if valuation_date < account["opening_balance_date"]:
            raise HTTPException(status_code=422, detail="估值日期不能早于账户期初日期。")
        existing_row = connection.execute(
            """
            SELECT id
            FROM investment_valuations
            WHERE account_id = ? AND valuation_date = ?
            """,
            (account_id, valuation_date),
        ).fetchone()
        before = (
            fetch_investment_valuation_snapshot(connection, existing_row["id"])
            if existing_row is not None
            else None
        )

    backup_path = create_backup()
    with connect() as connection:
        account = get_investment_account(connection, account_id)
        total_value_minor = amount_to_minor(
            valuation.total_value,
            account["decimal_places"],
        )
        connection.execute(
            """
            INSERT INTO investment_valuations (
                account_id, valuation_date, total_value_minor, note
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, valuation_date) DO UPDATE SET
                total_value_minor = excluded.total_value_minor,
                note = excluded.note
            """,
            (
                account_id,
                valuation_date,
                total_value_minor,
                clean_optional_text(valuation.note),
            ),
        )
        valuation_id = connection.execute(
            """
            SELECT id
            FROM investment_valuations
            WHERE account_id = ? AND valuation_date = ?
            """,
            (account_id, valuation_date),
        ).fetchone()["id"]
        recalculated_ids = recalculate_investment_valuation_transactions(
            connection,
            account_id,
        )
        after = fetch_investment_valuation_snapshot(connection, valuation_id)
        write_audit_log(
            connection,
            entity_type="investment_valuation",
            entity_id=valuation_id,
            action="update" if before is not None else "create",
            backup_file=backup_path.name,
            before=before,
            after={
                **after,
                "recalculated_valuation_ids": recalculated_ids,
            },
        )
    return {
        **serialize_investment_valuation(after),
        "recalculated_valuation_ids": recalculated_ids,
    }


@app.delete("/api/investments/valuations/{valuation_id}")
def delete_investment_valuation(valuation_id: int):
    with connect() as connection:
        before = fetch_investment_valuation_snapshot(connection, valuation_id)
    if before is None:
        raise HTTPException(status_code=404, detail="投资估值不存在。")

    backup_path = create_backup()
    with connect() as connection:
        current = fetch_investment_valuation_snapshot(connection, valuation_id)
        if current is None:
            raise HTTPException(status_code=404, detail="投资估值不存在。")
        account_id = current["account_id"]
        generated_ids = [
            row["generated_transaction_id"]
            for row in connection.execute(
                """
                SELECT generated_transaction_id
                FROM investment_valuations
                WHERE account_id = ? AND generated_transaction_id IS NOT NULL
                """,
                (account_id,),
            ).fetchall()
        ]
        if generated_ids:
            placeholders = ", ".join("?" for _ in generated_ids)
            connection.execute(
                f"DELETE FROM transactions WHERE id IN ({placeholders})",
                generated_ids,
            )
        connection.execute("DELETE FROM investment_valuations WHERE id = ?", (valuation_id,))
        recalculated_ids = recalculate_investment_valuation_transactions(
            connection,
            account_id,
        )
        write_audit_log(
            connection,
            entity_type="investment_valuation",
            entity_id=valuation_id,
            action="delete",
            backup_file=backup_path.name,
            before=current,
            after={"recalculated_valuation_ids": recalculated_ids},
        )
    return {
        "id": valuation_id,
        "deleted": True,
        "recalculated_valuation_ids": recalculated_ids,
    }


@app.get("/api/investments/performance")
def investment_performance(
    start_date: str,
    end_date: str,
    account_id: int | None = None,
):
    start_date = validate_optional_date(start_date, "开始日期")
    end_date = validate_optional_date(end_date, "结束日期")
    if start_date is None or end_date is None:
        raise HTTPException(status_code=422, detail="开始日期和结束日期不能为空。")
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")

    with connect() as connection:
        accounts = investment_report_accounts(connection, account_id)
        results = []
        for account in accounts:
            opening_balance_minor = account_balance_for_report(
                connection,
                account,
                start_date,
            )
            closing_balance_minor = account_balance_for_report(
                connection,
                account,
                end_date,
            )
            contribution_minor, withdrawal_minor = investment_cash_flows(
                connection,
                account["id"],
                start_date,
                end_date,
            )
            profit_minor = investment_valuation_profit_minor(
                connection,
                account["id"],
                start_date,
                end_date,
            )
            valuation_count = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM investment_valuations
                WHERE account_id = ?
                  AND valuation_date > ?
                  AND valuation_date <= ?
                """,
                (account["id"], start_date, end_date),
            ).fetchone()["total"]
            has_any_valuation = connection.execute(
                """
                SELECT 1
                FROM investment_valuations
                WHERE account_id = ? AND valuation_date <= ?
                LIMIT 1
                """,
                (account["id"], end_date),
            ).fetchone()
            results.append(
                {
                    "account_id": account["id"],
                    "account_name": account["name"],
                    "account_code": account["code"],
                    "currency_code": account["currency_code"],
                    "opening_balance": format_minor(
                        opening_balance_minor,
                        account["decimal_places"],
                    ),
                    "contribution": format_minor(
                        contribution_minor,
                        account["decimal_places"],
                    ),
                    "withdrawal": format_minor(
                        withdrawal_minor,
                        account["decimal_places"],
                    ),
                    "closing_balance": format_minor(
                        closing_balance_minor,
                        account["decimal_places"],
                    ),
                    "profit": format_minor(profit_minor, account["decimal_places"]),
                    "valuation_count": valuation_count,
                    "status": (
                        "valued"
                        if valuation_count
                        else "zero_return"
                        if has_any_valuation is not None
                        else "no_valuation_base"
                    ),
                    "status_label": (
                        "已估值"
                        if valuation_count
                        else "按零收益"
                        if has_any_valuation is not None
                        else "无估值基础"
                    ),
                }
            )
    return {
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
    }


@app.get("/api/investments/monthly")
def investment_monthly(
    start_month: str,
    end_month: str,
    account_id: int | None = None,
):
    start_month = validate_month(start_month)
    end_month = validate_month(end_month)
    if end_month > today_in_shanghai().strftime("%Y-%m"):
        raise HTTPException(status_code=422, detail="不能查看未来月份。")
    months = month_sequence(start_month, end_month)
    with connect() as connection:
        accounts = investment_report_accounts(connection, account_id)
        results = []
        for month in months:
            month_start = f"{month}-01"
            month_end = month_as_of_date(month)
            month_previous_end = previous_month_end(month)
            for account in accounts:
                opening_balance_minor = account_balance_for_report(
                    connection,
                    account,
                    month_previous_end,
                )
                closing_balance_minor = account_balance_for_report(
                    connection,
                    account,
                    month_end,
                )
                contribution_minor, withdrawal_minor = investment_cash_flows(
                    connection,
                    account["id"],
                    month_previous_end,
                    month_end,
                )
                profit_minor = investment_valuation_profit_minor(
                    connection,
                    account["id"],
                    month_previous_end,
                    month_end,
                )
                has_valuation = connection.execute(
                    """
                    SELECT 1
                    FROM investment_valuations
                    WHERE account_id = ?
                      AND valuation_date >= ?
                      AND valuation_date <= ?
                    LIMIT 1
                    """,
                    (account["id"], month_start, month_end),
                ).fetchone()
                has_any_valuation = connection.execute(
                    """
                    SELECT 1
                    FROM investment_valuations
                    WHERE account_id = ? AND valuation_date <= ?
                    LIMIT 1
                    """,
                    (account["id"], month_end),
                ).fetchone()
                results.append(
                    {
                        "month": month,
                        "account_id": account["id"],
                        "account_name": account["name"],
                        "currency_code": account["currency_code"],
                        "opening_balance": format_minor(
                            opening_balance_minor,
                            account["decimal_places"],
                        ),
                        "contribution": format_minor(
                            contribution_minor,
                            account["decimal_places"],
                        ),
                        "withdrawal": format_minor(
                            withdrawal_minor,
                            account["decimal_places"],
                        ),
                        "closing_balance": format_minor(
                            closing_balance_minor,
                            account["decimal_places"],
                        ),
                        "profit": format_minor(
                            profit_minor,
                            account["decimal_places"],
                        ),
                        "status": (
                            "valued"
                            if has_valuation is not None
                            else "zero_return"
                            if has_any_valuation is not None
                            else "no_valuation_base"
                        ),
                        "status_label": (
                            "已估值"
                            if has_valuation is not None
                            else "按零收益"
                            if has_any_valuation is not None
                            else "无估值基础"
                        ),
                    }
                )
    return {
        "start_month": start_month,
        "end_month": end_month,
        "results": results,
    }


@app.get("/api/fx-rates")
def list_daily_cny_exchange_rates(limit: int = 90):
    if limit < 1 or limit > 3650:
        raise HTTPException(status_code=422, detail="查询数量必须在 1 到 3650 之间。")
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM daily_cny_exchange_rates
            ORDER BY rate_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [serialize_daily_cny_exchange_rate(row) for row in rows]


@app.post("/api/fx-rates/sync")
def sync_daily_cny_exchange_rate_endpoint(request: FxRateSyncRequest):
    if request.rate_date > today_in_shanghai():
        raise HTTPException(status_code=422, detail="不能同步未来日期的汇率。")
    return sync_daily_cny_exchange_rates(request.rate_date, request.force)


@app.put("/api/fx-rates/{rate_date}")
def update_daily_cny_exchange_rate(rate_date: str, rate: DailyCnyRateUpdate):
    rate_date = validate_optional_date(rate_date, "汇率日期")
    if rate_date is None:
        raise HTTPException(status_code=422, detail="汇率日期不能为空。")
    if rate_date > today_in_shanghai().isoformat():
        raise HTTPException(status_code=422, detail="不能保存未来日期的汇率。")
    backup_path = create_backup()
    with connect() as connection:
        before_row = connection.execute(
            "SELECT * FROM daily_cny_exchange_rates WHERE rate_date = ?",
            (rate_date,),
        ).fetchone()
        row = upsert_daily_cny_exchange_rate(
            connection,
            rate_date=rate_date,
            usd_cny_rate=rate.usd_cny_rate,
            hkd_cny_rate=rate.hkd_cny_rate,
            source="manual",
            market_date=rate_date,
            note=rate.note,
        )
        write_audit_log(
            connection,
            entity_type="exchange_rate",
            entity_id=row["id"],
            action="update" if before_row is not None else "create",
            backup_file=backup_path.name,
            before=dict(before_row) if before_row else None,
            after=dict(row),
        )
    return serialize_daily_cny_exchange_rate(row)


@app.get("/api/categories")
def list_categories(
    kind: str = Query(pattern="^(income|expense)$"),
    include_inactive: bool = False,
):
    snapshot_date = today_in_shanghai().isoformat()
    with connect() as connection:
        ensure_daily_frequent_snapshot(connection, date.fromisoformat(snapshot_date))
        rows = connection.execute(
            """
            SELECT parent.id AS parent_id, parent.name AS parent_name,
                   child.id, child.name, child.kind, child.sort_order, child.is_active
            FROM categories AS child
            LEFT JOIN categories AS parent ON parent.id = child.parent_id
            WHERE child.kind = ?
              AND (? = 1 OR child.is_active = 1)
              AND NOT EXISTS (
                  SELECT 1 FROM categories AS nested
                  WHERE nested.parent_id = child.id
                    AND (? = 1 OR nested.is_active = 1)
              )
            ORDER BY COALESCE(parent.sort_order, child.sort_order),
                     parent.name,
                     child.sort_order,
                     child.name
            """,
            (kind, int(include_inactive), int(include_inactive)),
        ).fetchall()
        recent_activity = list_daily_category_frequent_activity(
            connection,
            snapshot_date,
        )
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "parent_id": row["parent_id"],
            "parent_name": row["parent_name"],
            "is_active": bool(row["is_active"]),
            "activity": recent_activity.get(
                row["id"],
                serialize_account_recent_activity(0, None, 0),
            ),
            "display_name": f"{row['parent_name']} / {row['name']}"
            if row["parent_name"]
            else row["name"],
        }
        for row in rows
    ]


@app.post("/api/expenses", status_code=201)
def create_expense(expense: ExpenseCreate):
    create_backup()

    with connect() as connection:
        require_system(connection, expense.budget_system_id)
        account = get_active_account(connection, expense.source_account_id, "支付账户")
        category = get_active_category(connection, expense.category_id, "expense")

        amount_minor = amount_to_minor(expense.amount, account["decimal_places"])
        cursor = connection.execute(
            """
            INSERT INTO transactions (
                transaction_date,
                transaction_type,
                source_account_id,
                outflow_minor,
                category_id,
                counterparty,
                note,
                budget_system_id
            ) VALUES (?, 'expense', ?, ?, ?, ?, ?, ?)
            """,
            (
                expense.transaction_date.isoformat(),
                expense.source_account_id,
                amount_minor,
                expense.category_id,
                clean_optional_text(expense.counterparty),
                clean_optional_text(expense.note),
                expense.budget_system_id,
            ),
        )
        updated_account = get_account_balance_row(connection, expense.source_account_id)

    return {
        "id": cursor.lastrowid,
        "transaction_date": expense.transaction_date.isoformat(),
        "account_name": account["name"],
        "currency_code": account["currency_code"],
        "amount": format_minor(amount_minor, account["decimal_places"]),
        "account_balance": format_minor(
            updated_account["balance_minor"], account["decimal_places"]
        ),
        "category_name": category["name"],
        "budget_system_id": expense.budget_system_id,
    }


@app.post("/api/incomes", status_code=201)
def create_income(income: IncomeCreate):
    backup_path = create_backup()
    budget_tag = income.budget_system_id if income.budget_allocations is None else None

    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        require_system(connection, budget_tag)
        account = get_active_account(connection, income.destination_account_id, "到账账户")
        category = get_active_category(connection, income.category_id, "income")
        amount_minor = amount_to_minor(income.amount, account["decimal_places"])
        cursor = connection.execute(
            """
            INSERT INTO transactions (
                transaction_date,
                transaction_type,
                destination_account_id,
                inflow_minor,
                category_id,
                counterparty,
                note,
                budget_system_id
            ) VALUES (?, 'income', ?, ?, ?, ?, ?, ?)
            """,
            (
                income.transaction_date.isoformat(),
                income.destination_account_id,
                amount_minor,
                income.category_id,
                clean_optional_text(income.counterparty),
                clean_optional_text(income.note),
                budget_tag,
            ),
        )
        if income.budget_allocations is not None:
            result = save_allocations(connection,'transaction',cursor.lastrowid,income.budget_allocations)
            if result['plan_ids']:
                write_audit_log(connection,entity_type='budget',entity_id=None,action='update',
                    backup_file=backup_path.name,reason='收入入账并分配预算',before=None,
                    after=dict(transaction_id=cursor.lastrowid,plan_ids=result['plan_ids'],
                               allocations=[dict(r) for r in connection.execute(
                                   'SELECT * FROM budget_income_allocations WHERE transaction_id=?',(cursor.lastrowid,))]))
        updated_account = get_account_balance_row(connection, income.destination_account_id)

    return {
        "id": cursor.lastrowid,
        "transaction_date": income.transaction_date.isoformat(),
        "account_name": account["name"],
        "currency_code": account["currency_code"],
        "amount": format_minor(amount_minor, account["decimal_places"]),
        "account_balance": format_minor(
            updated_account["balance_minor"], account["decimal_places"]
        ),
        "category_name": category["name"],
        "budget_system_id": budget_tag,
    }


@app.post("/api/transfers", status_code=201)
def create_transfer(transfer: TransferCreate):
    if transfer.source_account_id == transfer.destination_account_id:
        raise HTTPException(status_code=422, detail="转出和转入账户不能相同。")

    create_backup()

    with connect() as connection:
        source_account = get_active_account(connection, transfer.source_account_id, "转出账户")
        destination_account = get_active_account(
            connection,
            transfer.destination_account_id,
            "转入账户",
        )
        outflow_minor = amount_to_minor(
            transfer.outflow_amount,
            source_account["decimal_places"],
        )
        is_fx_exchange = source_account["currency_code"] != destination_account["currency_code"]

        if is_fx_exchange:
            if transfer.inflow_amount is None:
                raise HTTPException(status_code=422, detail="换汇必须填写收到金额。")
            inflow_minor = amount_to_minor(
                transfer.inflow_amount,
                destination_account["decimal_places"],
            )
            transaction_type = "fx_exchange"
        else:
            if transfer.inflow_amount is None:
                inflow_minor = outflow_minor
            else:
                inflow_minor = amount_to_minor(
                    transfer.inflow_amount,
                    destination_account["decimal_places"],
                )
            if inflow_minor != outflow_minor:
                raise HTTPException(
                    status_code=422,
                    detail="同币种转账的转出和转入金额必须相同。",
                )
            transaction_type = "transfer"

        cursor = connection.execute(
            """
            INSERT INTO transactions (
                transaction_date,
                transaction_type,
                source_account_id,
                destination_account_id,
                outflow_minor,
                inflow_minor,
                note
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transfer.transaction_date.isoformat(),
                transaction_type,
                transfer.source_account_id,
                transfer.destination_account_id,
                outflow_minor,
                inflow_minor,
                clean_optional_text(transfer.note),
            ),
        )
        updated_source_account = get_account_balance_row(
            connection, transfer.source_account_id
        )
        updated_destination_account = get_account_balance_row(
            connection, transfer.destination_account_id
        )

    return {
        "id": cursor.lastrowid,
        "transaction_date": transfer.transaction_date.isoformat(),
        "transaction_type": transaction_type,
        "transaction_type_label": TRANSACTION_TYPE_LABELS[transaction_type],
        "source_account_name": source_account["name"],
        "source_currency_code": source_account["currency_code"],
        "outflow_amount": format_minor(outflow_minor, source_account["decimal_places"]),
        "source_account_balance": format_minor(
            updated_source_account["balance_minor"], source_account["decimal_places"]
        ),
        "destination_account_name": destination_account["name"],
        "destination_currency_code": destination_account["currency_code"],
        "inflow_amount": format_minor(inflow_minor, destination_account["decimal_places"]),
        "destination_account_balance": format_minor(
            updated_destination_account["balance_minor"],
            destination_account["decimal_places"],
        ),
    }


@app.get("/api/transactions")
def list_transactions(limit: int = Query(default=20, ge=1, le=100)):
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                transactions.id,
                transactions.transaction_date,
                transactions.transaction_type,
                transactions.outflow_minor,
                transactions.inflow_minor,
                transactions.counterparty,
                transactions.note,
                source.name AS source_account_name,
                source.currency_code AS source_currency_code,
                source_currency.decimal_places AS source_decimal_places,
                destination.name AS destination_account_name,
                destination.currency_code AS destination_currency_code,
                destination_currency.decimal_places AS destination_decimal_places,
                categories.name AS category_name,
                investment_valuations.id AS investment_valuation_id
            FROM transactions
            LEFT JOIN accounts AS source ON source.id = transactions.source_account_id
            LEFT JOIN currencies AS source_currency ON source_currency.code = source.currency_code
            LEFT JOIN accounts AS destination ON destination.id = transactions.destination_account_id
            LEFT JOIN currencies AS destination_currency ON destination_currency.code = destination.currency_code
            LEFT JOIN categories ON categories.id = transactions.category_id
            LEFT JOIN investment_valuations
                ON investment_valuations.generated_transaction_id = transactions.id
            ORDER BY transactions.transaction_date DESC, transactions.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    result = []
    for row in rows:
        is_outflow = row["outflow_minor"] is not None
        decimal_places = (
            row["source_decimal_places"] if is_outflow else row["destination_decimal_places"]
        )
        amount_minor = row["outflow_minor"] if is_outflow else row["inflow_minor"]
        currency_code = (
            row["source_currency_code"] if is_outflow else row["destination_currency_code"]
        )
        result.append(
            {
                "id": row["id"],
                "transaction_date": row["transaction_date"],
                "transaction_type": row["transaction_type"],
                "transaction_type_label": transaction_type_label(
                    row["transaction_type"],
                    row["investment_valuation_id"],
                ),
                "source_account_name": row["source_account_name"],
                "source_currency_code": row["source_currency_code"],
                "outflow_amount": (
                    format_minor(row["outflow_minor"], row["source_decimal_places"])
                    if row["outflow_minor"] is not None
                    else None
                ),
                "destination_account_name": row["destination_account_name"],
                "destination_currency_code": row["destination_currency_code"],
                "inflow_amount": (
                    format_minor(row["inflow_minor"], row["destination_decimal_places"])
                    if row["inflow_minor"] is not None
                    else None
                ),
                "account_name": (
                    row["source_account_name"] if is_outflow else row["destination_account_name"]
                ),
                "amount": format_minor(amount_minor, decimal_places),
                "currency_code": currency_code,
                "category_name": row["category_name"],
                "counterparty": row["counterparty"],
                "note": row["note"],
            }
        )
    return annotate_transactions(result)


def build_transaction_search_filters(
    account_id: int | None,
    start_date: str | None,
    end_date: str | None,
    transaction_types: list[str],
    category_ids: list[int],
    keyword: str | None,
    manual_adjustments_only: bool,
    flagged_only: bool,
) -> tuple[list[str], list[Any]]:
    filters = []
    parameters: list[Any] = []
    if account_id is not None:
        filters.append(
            "(transactions.source_account_id = ? OR transactions.destination_account_id = ?)"
        )
        parameters.extend([account_id, account_id])
    if start_date:
        filters.append("transactions.transaction_date >= ?")
        parameters.append(start_date)
    if end_date:
        filters.append("transactions.transaction_date <= ?")
        parameters.append(end_date)
    if transaction_types:
        placeholders = ", ".join("?" for _ in transaction_types)
        filters.append(f"transactions.transaction_type IN ({placeholders})")
        parameters.extend(transaction_types)
    if category_ids:
        placeholders = ", ".join("?" for _ in category_ids)
        filters.append(f"transactions.category_id IN ({placeholders})")
        parameters.extend(category_ids)
    if manual_adjustments_only:
        filters.append(
            "transactions.transaction_type = 'adjustment' "
            "AND investment_valuations.id IS NULL"
        )
    if flagged_only:
        filters.append("flags.id IS NOT NULL")
    if keyword:
        filters.append(
            """
            (
                COALESCE(categories.name, '') LIKE ?
                OR COALESCE(transactions.counterparty, '') LIKE ?
                OR COALESCE(transactions.note, '') LIKE ?
                OR COALESCE(source.name, '') LIKE ?
                OR COALESCE(destination.name, '') LIKE ?
            )
            """
        )
        search_term = f"%{keyword}%"
        parameters.extend([search_term] * 5)
    return filters, parameters


@app.get("/api/transactions/search")
def search_transactions(
    account_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    transaction_types: list[str] = Query(default=[]),
    category_ids: list[int] = Query(default=[]),
    keyword: str | None = None,
    manual_adjustments_only: bool = False,
    flagged_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    budget_system_id: int | None = None,
):
    start_date = validate_optional_date(start_date, "开始日期")
    end_date = validate_optional_date(end_date, "结束日期")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")
    if not isinstance(transaction_types, list):
        transaction_types = []
    if not isinstance(category_ids, list):
        category_ids = []
    transaction_types = list(dict.fromkeys(transaction_types))
    invalid_types = set(transaction_types) - set(TRANSACTION_TYPE_LABELS)
    if invalid_types:
        raise HTTPException(status_code=422, detail="流水类型不支持。")
    category_ids = list(dict.fromkeys(category_ids))
    if any(category_id < 1 for category_id in category_ids):
        raise HTTPException(status_code=422, detail="分类编号必须大于零。")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="每页数量必须在 1 到 200 之间。")
    if offset < 0:
        raise HTTPException(status_code=422, detail="分页起点不能小于零。")
    keyword = clean_optional_text(keyword)
    if keyword and len(keyword) > 120:
        raise HTTPException(status_code=422, detail="关键词不能超过 120 个字符。")

    filters, parameters = build_transaction_search_filters(
        account_id,
        start_date,
        end_date,
        transaction_types,
        category_ids,
        keyword,
        manual_adjustments_only,
        flagged_only,
    )
    budget_filter_sql(filters, parameters, budget_system_id)
    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""

    transaction_query = """
        FROM transactions
        LEFT JOIN accounts AS source ON source.id = transactions.source_account_id
        LEFT JOIN currencies AS source_currency ON source_currency.code = source.currency_code
        LEFT JOIN accounts AS destination ON destination.id = transactions.destination_account_id
        LEFT JOIN currencies AS destination_currency
            ON destination_currency.code = destination.currency_code
        LEFT JOIN categories ON categories.id = transactions.category_id
        LEFT JOIN transaction_flags AS flags ON flags.transaction_id = transactions.id
        LEFT JOIN investment_valuations
            ON investment_valuations.generated_transaction_id = transactions.id
    """
    with connect() as connection:
        if account_id is not None:
            account_exists = connection.execute(
                "SELECT 1 FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if account_exists is None:
                raise HTTPException(status_code=404, detail="账户不存在。")
        total_row = connection.execute(
            f"SELECT COUNT(*) AS total {transaction_query}{where_clause}",
            parameters,
        ).fetchone()
        rows = connection.execute(
            f"""
            SELECT
                transactions.id,
                transactions.transaction_date,
                transactions.transaction_type,
                transactions.outflow_minor,
                transactions.inflow_minor,
                transactions.counterparty,
                transactions.note,
                source.name AS source_account_name,
                source.currency_code AS source_currency_code,
                source_currency.decimal_places AS source_decimal_places,
                destination.name AS destination_account_name,
                destination.currency_code AS destination_currency_code,
                destination_currency.decimal_places AS destination_decimal_places,
                categories.name AS category_name,
                flags.flag_type,
                flags.note AS flag_note,
                investment_valuations.id AS investment_valuation_id
            {transaction_query}
            {where_clause}
            ORDER BY transactions.transaction_date DESC, transactions.id DESC
            LIMIT ? OFFSET ?
            """,
            parameters + [limit, offset],
        ).fetchall()
        summary_rows = connection.execute(
            f"""
            SELECT
                transactions.id,
                transactions.transaction_date,
                transactions.transaction_type,
                transactions.source_account_id,
                transactions.destination_account_id,
                transactions.outflow_minor,
                transactions.inflow_minor,
                source.currency_code AS source_currency_code,
                source_currency.decimal_places AS source_decimal_places,
                destination.currency_code AS destination_currency_code,
                destination_currency.decimal_places AS destination_decimal_places
            {transaction_query}
            {where_clause}
            """,
            parameters,
        ).fetchall()
        rate_rows = connection.execute(
            """
            SELECT rate_date, usd_cny_rate, hkd_cny_rate
            FROM daily_cny_exchange_rates
            ORDER BY rate_date
            """
        ).fetchall()
        currency_decimal_places = {
            row["code"]: row["decimal_places"]
            for row in connection.execute(
                "SELECT code, decimal_places FROM currencies"
            ).fetchall()
        }
        from app.income_allocations import allocations_for_transactions, allocation_metadata
        allocation_rows, allocation_totals = allocations_for_transactions(
            connection, [r['id'] for r in summary_rows if r['transaction_type']=='income'],
            budget_system_id)
        allocation_info = allocation_metadata([r for rows in allocation_rows.values() for r in rows])

    transactions = [
        {
            "id": row["id"],
            "transaction_date": row["transaction_date"],
            "transaction_type": row["transaction_type"],
            "transaction_type_label": transaction_type_label(
                row["transaction_type"],
                row["investment_valuation_id"],
            ),
            "source_account_name": row["source_account_name"] or "外部",
            "source_currency_code": row["source_currency_code"],
            "outflow_amount": (
                format_minor(row["outflow_minor"], row["source_decimal_places"])
                if row["outflow_minor"] is not None
                else None
            ),
            "destination_account_name": row["destination_account_name"] or "外部",
            "destination_currency_code": row["destination_currency_code"],
            "inflow_amount": (
                format_minor(row["inflow_minor"], row["destination_decimal_places"])
                if row["inflow_minor"] is not None
                else None
            ),
            "category_name": row["category_name"],
            "counterparty": row["counterparty"],
            "note": row["note"],
            "is_manual_adjustment": (
                row["transaction_type"] == "adjustment"
                and row["investment_valuation_id"] is None
            ),
            "is_investment_valuation": row["investment_valuation_id"] is not None,
            "flag": (
                {
                    "flag_type": row["flag_type"],
                    "note": row["flag_note"],
                }
                if row["flag_type"] is not None
                else None
            ),
        }
        for row in rows
    ]
    summary = build_transaction_search_summary(
        summary_rows,
        rate_rows,
        account_id,
        transaction_types,
        currency_decimal_places,
    )
    summary['budget_income_allocations'] = allocation_totals
    summary.update(allocation_info)
    for transaction in transactions:
        transaction['income_allocations'] = allocation_rows.get(transaction['id'],[])
    return {
        "filters": {
            "account_id": account_id,
            "start_date": start_date,
            "end_date": end_date,
            "transaction_types": transaction_types,
            "budget_system_id": budget_system_id,
            "category_ids": category_ids,
            "keyword": keyword,
            "manual_adjustments_only": manual_adjustments_only,
            "flagged_only": flagged_only,
        },
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total_row["total"],
        },
        "summary": summary,
        "transactions": annotate_transactions(transactions),
    }


def analysis_calendar_month_count(start_date: str, end_date: str) -> int:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    return (end.year - start.year) * 12 + end.month - start.month + 1


def new_flow_totals() -> dict[str, Decimal]:
    return {
        "inflow": Decimal(0),
        "outflow": Decimal(0),
    }


def new_rate_coverage() -> dict[str, Any]:
    return {
        "direct_base_currency_leg_count": 0,
        "exact_rate_leg_count": 0,
        "nearest_rate_leg_count": 0,
        "excluded_leg_count": 0,
        "missing_currencies": set(),
    }


def new_transaction_search_summary_bucket() -> dict[str, Any]:
    return {
        "transaction_count": 0,
        "raw_totals_by_currency": {},
        "valuations": {
            currency_code: {
                **new_flow_totals(),
                "rate_coverage": new_rate_coverage(),
            }
            for currency_code in BASE_CURRENCY_CODES
        },
    }


def add_flow_total(
    totals_by_currency: dict[str, dict[str, Decimal]],
    currency_code: str,
    direction: str,
    amount: Decimal,
) -> None:
    totals = totals_by_currency.setdefault(currency_code, new_flow_totals())
    totals[direction] += amount


def add_valuation_coverage(
    coverage: dict[str, Any],
    rate_status: str | None,
    currency_code: str,
) -> None:
    if rate_status == "direct":
        coverage["direct_base_currency_leg_count"] += 1
    elif rate_status == "exact":
        coverage["exact_rate_leg_count"] += 1
    elif rate_status == "nearest":
        coverage["nearest_rate_leg_count"] += 1
    else:
        coverage["excluded_leg_count"] += 1
        coverage["missing_currencies"].add(currency_code)


def add_transaction_search_summary_leg(
    buckets: tuple[dict[str, Any], ...],
    *,
    currency_code: str,
    direction: str,
    amount: Decimal,
    transaction_date: str,
    rate_rows,
) -> None:
    for bucket in buckets:
        add_flow_total(
            bucket["raw_totals_by_currency"],
            currency_code,
            direction,
            amount,
        )

    for base_currency_code in BASE_CURRENCY_CODES:
        converted, rate_status = amount_to_base_currency(
            amount,
            currency_code,
            transaction_date,
            rate_rows,
            base_currency_code,
        )
        for bucket in buckets:
            valuation = bucket["valuations"][base_currency_code]
            add_valuation_coverage(
                valuation["rate_coverage"],
                rate_status,
                currency_code,
            )
            if converted is not None:
                valuation[direction] += converted


def currency_display_sort_key(currency_code: str) -> tuple[int, str]:
    try:
        return CURRENCY_DISPLAY_ORDER.index(currency_code), currency_code
    except ValueError:
        return len(CURRENCY_DISPLAY_ORDER), currency_code


def serialize_flow_totals(
    totals_by_currency: dict[str, dict[str, Decimal]],
    currency_decimal_places: dict[str, int],
    currency_codes: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    codes = (
        currency_codes
        if currency_codes is not None
        else tuple(sorted(totals_by_currency, key=currency_display_sort_key))
    )
    result = []
    for currency_code in codes:
        totals = totals_by_currency.get(currency_code, new_flow_totals())
        decimal_places = currency_decimal_places.get(currency_code, 2)
        inflow = totals["inflow"]
        outflow = totals["outflow"]
        result.append(
            {
                "currency_code": currency_code,
                "inflow_amount": format_decimal(inflow, decimal_places),
                "outflow_amount": format_decimal(outflow, decimal_places),
                "net_amount": format_decimal(inflow - outflow, decimal_places),
            }
        )
    return result


def serialize_rate_coverage(coverage: dict[str, Any]) -> dict[str, Any]:
    return {
        "direct_base_currency_leg_count": coverage[
            "direct_base_currency_leg_count"
        ],
        "exact_rate_leg_count": coverage["exact_rate_leg_count"],
        "nearest_rate_leg_count": coverage["nearest_rate_leg_count"],
        "excluded_leg_count": coverage["excluded_leg_count"],
        "missing_currencies": sorted(coverage["missing_currencies"]),
    }


def serialize_transaction_search_valuations(
    bucket: dict[str, Any],
    include_rate_coverage: bool,
) -> list[dict[str, Any]]:
    result = []
    for currency_code in BASE_CURRENCY_CODES:
        valuation = bucket["valuations"][currency_code]
        item: dict[str, Any] = {
            "currency_code": currency_code,
            "inflow_amount": format_decimal(valuation["inflow"], 2),
            "outflow_amount": format_decimal(valuation["outflow"], 2),
            "net_amount": format_decimal(
                valuation["inflow"] - valuation["outflow"],
                2,
            ),
        }
        if include_rate_coverage:
            item["rate_coverage"] = serialize_rate_coverage(
                valuation["rate_coverage"]
            )
        result.append(item)
    return result


def build_transaction_search_summary(
    rows,
    rate_rows,
    account_id: int | None,
    selected_transaction_types: list[str],
    currency_decimal_places: dict[str, int],
) -> dict[str, Any]:
    combined = new_transaction_search_summary_bucket()
    type_buckets: dict[str, dict[str, Any]] = {}

    for row in rows:
        transaction_type = row["transaction_type"]
        type_bucket = type_buckets.setdefault(
            transaction_type,
            new_transaction_search_summary_bucket(),
        )
        combined["transaction_count"] += 1
        type_bucket["transaction_count"] += 1
        buckets = (combined, type_bucket)

        if row["source_account_id"] is not None and (
            account_id is None or row["source_account_id"] == account_id
        ):
            add_transaction_search_summary_leg(
                buckets,
                currency_code=row["source_currency_code"],
                direction="outflow",
                amount=amount_from_minor(
                    row["outflow_minor"],
                    row["source_decimal_places"],
                ),
                transaction_date=row["transaction_date"],
                rate_rows=rate_rows,
            )
        if row["destination_account_id"] is not None and (
            account_id is None or row["destination_account_id"] == account_id
        ):
            add_transaction_search_summary_leg(
                buckets,
                currency_code=row["destination_currency_code"],
                direction="inflow",
                amount=amount_from_minor(
                    row["inflow_minor"],
                    row["destination_decimal_places"],
                ),
                transaction_date=row["transaction_date"],
                rate_rows=rate_rows,
            )

    transaction_types = (
        selected_transaction_types
        if selected_transaction_types
        else [
            transaction_type
            for transaction_type in TRANSACTION_TYPE_LABELS
            if transaction_type in type_buckets
        ]
    )
    return {
        "available": True,
        "scope": {
            "mode": "selected_account" if account_id is not None else "all_accounts",
            "account_id": account_id,
        },
        "transaction_count": combined["transaction_count"],
        "raw_totals_by_currency": serialize_flow_totals(
            combined["raw_totals_by_currency"],
            currency_decimal_places,
        ),
        "valuations": serialize_transaction_search_valuations(
            combined,
            include_rate_coverage=True,
        ),
        "by_type": [
            {
                "transaction_type": transaction_type,
                "transaction_type_label": TRANSACTION_TYPE_LABELS[transaction_type],
                "transaction_count": type_buckets.get(
                    transaction_type,
                    new_transaction_search_summary_bucket(),
                )["transaction_count"],
                "raw_totals_by_currency": serialize_flow_totals(
                    type_buckets.get(
                        transaction_type,
                        new_transaction_search_summary_bucket(),
                    )["raw_totals_by_currency"],
                    currency_decimal_places,
                ),
                "valuations": serialize_transaction_search_valuations(
                    type_buckets.get(
                        transaction_type,
                        new_transaction_search_summary_bucket(),
                    ),
                    include_rate_coverage=False,
                ),
            }
            for transaction_type in transaction_types
        ],
    }


@app.get("/api/transactions/analysis")
def transaction_analysis(
    transaction_type: str,
    start_date: str,
    end_date: str,
    account_id: int | None = None,
    category_ids: list[int] = Query(default=[]),
    keyword: str | None = None,
    manual_adjustments_only: bool = False,
    flagged_only: bool = False,
    budget_system_id: int | None = None,
):
    start_date = validate_optional_date(start_date, "开始日期")
    end_date = validate_optional_date(end_date, "结束日期")
    if start_date is None or end_date is None:
        raise HTTPException(status_code=422, detail="流水详情需要完整的开始日期和结束日期。")
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")
    if analysis_calendar_month_count(start_date, end_date) > 12:
        raise HTTPException(status_code=422, detail="流水详情最多统计 12 个自然月，请拆分查询。")
    if transaction_type not in TRANSACTION_TYPE_LABELS:
        raise HTTPException(status_code=422, detail="流水类型不支持。")
    if not isinstance(category_ids, list):
        category_ids = []
    category_ids = list(dict.fromkeys(category_ids))
    if any(category_id < 1 for category_id in category_ids):
        raise HTTPException(status_code=422, detail="分类编号必须大于零。")
    keyword = clean_optional_text(keyword)
    if keyword and len(keyword) > 120:
        raise HTTPException(status_code=422, detail="关键词不能超过 120 个字符。")

    filters = [
        "transactions.transaction_type = ?",
        "transactions.transaction_date >= ?",
        "transactions.transaction_date <= ?",
    ]
    parameters: list[Any] = [transaction_type, start_date, end_date]
    if account_id is not None:
        filters.append(
            "(transactions.source_account_id = ? OR transactions.destination_account_id = ?)"
        )
        parameters.extend([account_id, account_id])
    if category_ids:
        placeholders = ", ".join("?" for _ in category_ids)
        filters.append(f"transactions.category_id IN ({placeholders})")
        parameters.extend(category_ids)
    if manual_adjustments_only:
        filters.append(
            "transactions.transaction_type = 'adjustment' "
            "AND investment_valuations.id IS NULL"
        )
    if flagged_only:
        filters.append("flags.id IS NOT NULL")
    if keyword:
        filters.append(
            """
            (
                COALESCE(categories.name, '') LIKE ?
                OR COALESCE(transactions.counterparty, '') LIKE ?
                OR COALESCE(transactions.note, '') LIKE ?
                OR COALESCE(source.name, '') LIKE ?
                OR COALESCE(destination.name, '') LIKE ?
            )
            """
        )
        search_term = f"%{keyword}%"
        parameters.extend([search_term] * 5)

    budget_filter_sql(filters, parameters, budget_system_id)
    where_clause = f" WHERE {' AND '.join(filters)}"
    with connect() as connection:
        if account_id is not None:
            account_exists = connection.execute(
                "SELECT 1 FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if account_exists is None:
                raise HTTPException(status_code=404, detail="账户不存在。")
        rows = connection.execute(
            f"""
            SELECT
                transactions.transaction_date,
                transactions.transaction_type,
                transactions.outflow_minor,
                transactions.inflow_minor,
                source.name AS source_account_name,
                source.currency_code AS source_currency_code,
                source_currency.decimal_places AS source_decimal_places,
                destination.name AS destination_account_name,
                destination.currency_code AS destination_currency_code,
                destination_currency.decimal_places AS destination_decimal_places,
                categories.name AS category_name
            FROM transactions
            LEFT JOIN accounts AS source ON source.id = transactions.source_account_id
            LEFT JOIN currencies AS source_currency ON source_currency.code = source.currency_code
            LEFT JOIN accounts AS destination ON destination.id = transactions.destination_account_id
            LEFT JOIN currencies AS destination_currency
                ON destination_currency.code = destination.currency_code
            LEFT JOIN categories ON categories.id = transactions.category_id
            LEFT JOIN transaction_flags AS flags ON flags.transaction_id = transactions.id
            LEFT JOIN investment_valuations
                ON investment_valuations.generated_transaction_id = transactions.id
            {where_clause}
            ORDER BY transactions.transaction_date ASC, transactions.id ASC
            """,
            parameters,
        ).fetchall()
        rate_rows = connection.execute(
            """
            SELECT rate_date, usd_cny_rate, hkd_cny_rate
            FROM daily_cny_exchange_rates
            ORDER BY rate_date ASC
            """
        ).fetchall()

    result = build_transaction_analysis(
        rows,
        rate_rows,
        start_date,
        end_date,
        transaction_type,
    )
    result["filters"] = {
        "account_id": account_id,
        "budget_system_id": budget_system_id,
        "start_date": start_date,
        "end_date": end_date,
        "transaction_type": transaction_type,
        "category_ids": category_ids,
        "keyword": keyword,
        "manual_adjustments_only": manual_adjustments_only,
        "flagged_only": flagged_only,
    }
    return result


@app.get("/api/transactions/{transaction_id}")
def get_transaction(transaction_id: int):
    with connect() as connection:
        snapshot = fetch_transaction_snapshot(connection, transaction_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="流水不存在。")
    return annotate_transactions([format_transaction_for_edit(snapshot)])[0]


@app.put("/api/transactions/{transaction_id}")
def update_transaction(transaction_id: int, transaction: TransactionUpdate):
    with connect() as connection:
        original = fetch_transaction_snapshot(connection, transaction_id)
        investment_valuation_id = generated_investment_valuation_id(
            connection,
            transaction_id,
        )
    if original is None:
        raise HTTPException(status_code=404, detail="流水不存在。")
    if investment_valuation_id is not None:
        raise HTTPException(
            status_code=409,
            detail="投资估值损益由估值记录自动生成，请修改对应投资估值。",
        )

    backup_path = create_backup()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = fetch_transaction_snapshot(connection, transaction_id)
        if current is None:
            raise HTTPException(status_code=404, detail="流水不存在。")
        payload = normalize_transaction_update(connection, transaction, current)
        budget_id = transaction.budget_system_id if 'budget_system_id' in transaction.model_fields_set else current.get('budget_system_id')
        if payload['transaction_type'] not in ('income','expense'):
            budget_id = None
        require_system(connection, budget_id, allow_inactive=budget_id == current.get('budget_system_id'))
        offset_before = check_member_update(connection, transaction_id, payload["transaction_type"])
        connection.execute(
            """
            UPDATE transactions
            SET
                transaction_date = ?,
                transaction_type = ?,
                source_account_id = ?,
                destination_account_id = ?,
                outflow_minor = ?,
                inflow_minor = ?,
                category_id = ?,
                counterparty = ?,
                note = ?
            WHERE id = ?
            """,
            (
                payload["transaction_date"],
                payload["transaction_type"],
                payload["source_account_id"],
                payload["destination_account_id"],
                payload["outflow_minor"],
                payload["inflow_minor"],
                payload["category_id"],
                payload["counterparty"],
                payload["note"],
                transaction_id,
            ),
        )
        connection.execute('UPDATE transactions SET budget_system_id=? WHERE id=?', (budget_id,transaction_id))
        updated = fetch_transaction_snapshot(connection, transaction_id)
        write_audit_log(
            connection,
            entity_type="transaction",
            entity_id=transaction_id,
            action="update",
            backup_file=backup_path.name,
            reason=transaction.correction_reason,
            before=current,
            after=updated,
        )
        record_member_update(connection, offset_before, backup_path.name)
    return format_transaction_for_edit(updated)


@app.delete("/api/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, deletion: TransactionDelete):
    reason = clean_required_text(deletion.reason, "删除原因")
    with connect() as connection:
        original = fetch_transaction_snapshot(connection, transaction_id)
        investment_valuation_id = generated_investment_valuation_id(
            connection,
            transaction_id,
        )
    if original is None:
        raise HTTPException(status_code=404, detail="流水不存在。")
    if investment_valuation_id is not None:
        raise HTTPException(
            status_code=409,
            detail="投资估值损益由估值记录自动生成，请删除对应投资估值。",
        )

    backup_path = create_backup()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        group_id = group_for_transaction(connection, transaction_id)
        if group_id is not None:
            raise HTTPException(409, f"请先从冲销事项 #{group_id} 移除此流水，或解散事项。")
        current = fetch_transaction_snapshot(connection, transaction_id)
        if current is None:
            raise HTTPException(status_code=404, detail="流水不存在。")
        from app.budget_funding import guard_reference
        guard_reference(connection,'transaction',transaction_id)
        connection.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        write_audit_log(
            connection,
            entity_type="transaction",
            entity_id=transaction_id,
            action="delete",
            backup_file=backup_path.name,
            reason=reason,
            before=current,
        )
    return {"id": transaction_id, "deleted": True}


@app.put("/api/transactions/{transaction_id}/flag")
def upsert_transaction_flag(transaction_id: int, flag: TransactionFlagUpdate):
    if flag.flag_type not in FLAG_TYPE_LABELS:
        raise HTTPException(status_code=422, detail="异常类型不支持。")
    note = clean_required_text(flag.note, "异常说明")
    with connect() as connection:
        original = fetch_transaction_snapshot(connection, transaction_id)
    if original is None:
        raise HTTPException(status_code=404, detail="流水不存在。")

    backup_path = create_backup()
    with connect() as connection:
        current = fetch_transaction_snapshot(connection, transaction_id)
        if current is None:
            raise HTTPException(status_code=404, detail="流水不存在。")
        connection.execute(
            """
            INSERT INTO transaction_flags (transaction_id, flag_type, note)
            VALUES (?, ?, ?)
            ON CONFLICT(transaction_id) DO UPDATE SET
                flag_type = excluded.flag_type,
                note = excluded.note
            """,
            (transaction_id, flag.flag_type, note),
        )
        updated = fetch_transaction_snapshot(connection, transaction_id)
        write_audit_log(
            connection,
            entity_type="transaction_flag",
            entity_id=transaction_id,
            action="flag",
            backup_file=backup_path.name,
            before=current,
            after=updated,
        )
    return {
        "transaction_id": transaction_id,
        "flag_type": flag.flag_type,
        "flag_label": FLAG_TYPE_LABELS[flag.flag_type],
        "note": note,
    }


@app.delete("/api/transactions/{transaction_id}/flag")
def delete_transaction_flag(transaction_id: int):
    with connect() as connection:
        original = fetch_transaction_snapshot(connection, transaction_id)
    if original is None:
        raise HTTPException(status_code=404, detail="流水不存在。")
    if original["flag_type"] is None:
        raise HTTPException(status_code=404, detail="该流水没有待核实标记。")

    backup_path = create_backup()
    with connect() as connection:
        current = fetch_transaction_snapshot(connection, transaction_id)
        if current is None or current["flag_type"] is None:
            raise HTTPException(status_code=404, detail="该流水没有待核实标记。")
        connection.execute(
            "DELETE FROM transaction_flags WHERE transaction_id = ?",
            (transaction_id,),
        )
        updated = fetch_transaction_snapshot(connection, transaction_id)
        write_audit_log(
            connection,
            entity_type="transaction_flag",
            entity_id=transaction_id,
            action="unflag",
            backup_file=backup_path.name,
            before=current,
            after=updated,
        )
    return {"transaction_id": transaction_id, "flagged": False}


@app.get("/api/dashboard")
def dashboard(month: str | None = None, basis: str = "raw"):
    with connect() as connection:
        connection.execute("BEGIN")
        return dashboard_data(month, basis, connection)


def dashboard_data(month, basis, connection):
    if basis not in {"raw", "offset"}:
        raise HTTPException(422, "统计口径不支持。")
    selected_month = (
        validate_month(month)
        if month
        else today_in_shanghai().strftime("%Y-%m")
    )
    as_of_date = month_as_of_date(selected_month)
    personal_summary = None
    if basis == "offset":
        entries = filter_entries(build_entries(*read_ledger(connection)), start_date=selected_month + "-01", end_date=as_of_date)
        personal_summary = summarize_personal(entries)
    cash_flow_rows = connection.execute(
        """
        WITH activity AS (
            SELECT
                source.currency_code,
                0 AS income_minor,
                transactions.outflow_minor AS expense_minor
            FROM transactions
            JOIN accounts AS source ON source.id = transactions.source_account_id
            WHERE transactions.transaction_type = 'expense'
              AND strftime('%Y-%m', transactions.transaction_date) = ?

            UNION ALL

            SELECT
                destination.currency_code,
                transactions.inflow_minor AS income_minor,
                0 AS expense_minor
            FROM transactions
            JOIN accounts AS destination ON destination.id = transactions.destination_account_id
            WHERE transactions.transaction_type = 'income'
              AND strftime('%Y-%m', transactions.transaction_date) = ?
        )
        SELECT
            currencies.code,
            currencies.decimal_places,
            COALESCE(SUM(activity.income_minor), 0) AS income_minor,
            COALESCE(SUM(activity.expense_minor), 0) AS expense_minor
        FROM currencies
        LEFT JOIN activity ON activity.currency_code = currencies.code
        WHERE currencies.is_active = 1
        GROUP BY currencies.code, currencies.decimal_places
        HAVING income_minor != 0 OR expense_minor != 0
        ORDER BY currencies.code
        """,
        (selected_month, selected_month),
    ).fetchall()
    account_rows = account_balance_rows_as_of(connection, as_of_date)
    current_rate_row = get_daily_cny_exchange_rate_as_of(
        connection,
        as_of_date,
    )
    internal_movement_summary_rows = connection.execute(
        """
        WITH movement_legs AS (
            SELECT
                transactions.id AS transaction_id,
                source.currency_code,
                transactions.outflow_minor,
                0 AS inflow_minor
            FROM transactions
            JOIN accounts AS source ON source.id = transactions.source_account_id
            WHERE transactions.transaction_type IN (
                'transfer',
                'fx_exchange',
                'broker_deposit',
                'broker_withdrawal'
            )
              AND strftime('%Y-%m', transactions.transaction_date) = ?

            UNION ALL

            SELECT
                transactions.id AS transaction_id,
                destination.currency_code,
                0 AS outflow_minor,
                transactions.inflow_minor
            FROM transactions
            JOIN accounts AS destination ON destination.id = transactions.destination_account_id
            WHERE transactions.transaction_type IN (
                'transfer',
                'fx_exchange',
                'broker_deposit',
                'broker_withdrawal'
            )
              AND strftime('%Y-%m', transactions.transaction_date) = ?
        )
        SELECT
            movement_legs.currency_code,
            currencies.decimal_places,
            SUM(movement_legs.outflow_minor) AS outflow_minor,
            SUM(movement_legs.inflow_minor) AS inflow_minor,
            COUNT(DISTINCT movement_legs.transaction_id) AS transaction_count
        FROM movement_legs
        JOIN currencies ON currencies.code = movement_legs.currency_code
        GROUP BY movement_legs.currency_code, currencies.decimal_places
        ORDER BY movement_legs.currency_code
        """,
        (selected_month, selected_month),
    ).fetchall()
    internal_movement_rows = connection.execute(
        """
        SELECT
            transactions.id,
            transactions.transaction_date,
            transactions.transaction_type,
            transactions.outflow_minor,
            transactions.inflow_minor,
            transactions.note,
            source.name AS source_account_name,
            source.currency_code AS source_currency_code,
            source_currency.decimal_places AS source_decimal_places,
            destination.name AS destination_account_name,
            destination.currency_code AS destination_currency_code,
            destination_currency.decimal_places AS destination_decimal_places
        FROM transactions
        JOIN accounts AS source ON source.id = transactions.source_account_id
        JOIN currencies AS source_currency ON source_currency.code = source.currency_code
        JOIN accounts AS destination ON destination.id = transactions.destination_account_id
        JOIN currencies AS destination_currency ON destination_currency.code = destination.currency_code
        WHERE transactions.transaction_type IN (
            'transfer',
            'fx_exchange',
            'broker_deposit',
            'broker_withdrawal'
        )
          AND strftime('%Y-%m', transactions.transaction_date) = ?
        ORDER BY transactions.transaction_date DESC, transactions.id DESC
        """,
        (selected_month,),
    ).fetchall()

    cash_flow = [
        {
            "currency_code": row["code"],
            "income": format_minor(row["income_minor"], row["decimal_places"]),
            "expense": format_minor(row["expense_minor"], row["decimal_places"]),
            "balance": format_minor(
                row["income_minor"] - row["expense_minor"],
                row["decimal_places"],
            ),
        }
        for row in cash_flow_rows
    ]
    balance_by_currency: dict[str, dict[str, int]] = {}
    available_cash_by_currency: dict[str, int] = {}
    decimal_places_by_currency: dict[str, int] = {}
    for row in account_rows:
        currency_code = row["currency_code"]
        decimal_places_by_currency[currency_code] = row["decimal_places"]
        totals = balance_by_currency.setdefault(
            currency_code,
            {"assets": 0, "liabilities": 0},
        )
        if row["account_class"] == "asset":
            totals["assets"] += row["balance_minor"]
            if row["account_type"] in {
                "cash",
                "e_wallet",
                "bank_demand",
                "transit_card",
            }:
                available_cash_by_currency[currency_code] = (
                    available_cash_by_currency.get(currency_code, 0)
                    + row["balance_minor"]
                )
        else:
            totals["liabilities"] += row["balance_minor"]
    net_worth = [
        {
            "currency_code": currency_code,
            "assets": format_minor(
                values["assets"],
                decimal_places_by_currency[currency_code],
            ),
            "liabilities": format_minor(
                values["liabilities"],
                decimal_places_by_currency[currency_code],
            ),
            "net_worth": format_minor(
                values["assets"] - values["liabilities"],
                decimal_places_by_currency[currency_code],
            ),
        }
        for currency_code, values in sorted(balance_by_currency.items())
    ]
    available_cash = [
        {
            "currency_code": currency_code,
            "available_cash": format_minor(
                amount_minor,
                decimal_places_by_currency[currency_code],
            ),
        }
        for currency_code, amount_minor in sorted(available_cash_by_currency.items())
    ]
    internal_movement_summary = [
        {
            "currency_code": row["currency_code"],
            "outflow": format_minor(row["outflow_minor"], row["decimal_places"]),
            "inflow": format_minor(row["inflow_minor"], row["decimal_places"]),
            "transaction_count": row["transaction_count"],
        }
        for row in internal_movement_summary_rows
    ]
    internal_movements = [
        {
            "id": row["id"],
            "transaction_date": row["transaction_date"],
            "transaction_type": row["transaction_type"],
            "transaction_type_label": TRANSACTION_TYPE_LABELS.get(
                row["transaction_type"],
                row["transaction_type"],
            ),
            "source_account_name": row["source_account_name"],
            "source_currency_code": row["source_currency_code"],
            "outflow_amount": format_minor(
                row["outflow_minor"],
                row["source_decimal_places"],
            ),
            "destination_account_name": row["destination_account_name"],
            "destination_currency_code": row["destination_currency_code"],
            "inflow_amount": format_minor(
                row["inflow_minor"],
                row["destination_decimal_places"],
            ),
            "note": row["note"],
        }
        for row in internal_movement_rows
    ]
    return {
        "basis": basis,
        "personal_cash_flow": personal_summary,
        "month": selected_month,
        "as_of_date": as_of_date,
        "cash_flow": cash_flow,
        "available_cash": available_cash,
        "net_worth": net_worth,
        "base_currency_totals": calculate_base_currency_totals(
            account_rows,
            current_rate_row,
        ),
        "internal_movement_summary": internal_movement_summary,
        "internal_movements": internal_movements,
    }


def sheet_title(worksheet, title: str, columns: int) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=columns)
    title_cell = worksheet.cell(1, 1, title)
    title_cell.font = Font(color="FFFFFF", bold=True, size=14)
    title_cell.fill = PatternFill("solid", fgColor="1F4E78")
    title_cell.alignment = Alignment(horizontal="center")
    worksheet.row_dimensions[1].height = 26


def sheet_headers(worksheet, headers: list[str], row: int = 3) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    for column, header in enumerate(headers, 1):
        cell = worksheet.cell(row, column, header)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="4472C4")
        cell.alignment = Alignment(horizontal="center")
    worksheet.freeze_panes = f"A{row + 1}"


def autosize_columns(worksheet) -> None:
    from openpyxl.utils import get_column_letter

    for index, column_cells in enumerate(worksheet.columns, 1):
        letter = get_column_letter(index)
        longest = max(
            (len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells),
            default=10,
        )
        worksheet.column_dimensions[letter].width = min(max(longest + 2, 10), 32)


def build_excel_export(selected_month: str, basis: str = "raw") -> BytesIO:
    if basis not in {"raw", "offset"}:
        raise HTTPException(422, "统计口径不支持。")
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Excel 导出依赖尚未安装。",
        ) from exc

    with connect() as connection:
        connection.execute("BEGIN")
        export_records, export_groups, export_rates = read_ledger(connection)
        export_entries = build_entries(export_records, export_groups, export_rates)
        dashboard_result = dashboard_data(selected_month, "raw", connection)
        account_rows = account_balance_rows_as_of(
            connection,
            dashboard_result["as_of_date"],
            active_only=False,
        )
        transaction_rows = connection.execute(
            """
            SELECT
                transactions.transaction_date,
                transactions.transaction_type,
                source.name AS source_account,
                source.currency_code AS source_currency,
                transactions.outflow_minor,
                destination.name AS destination_account,
                destination.currency_code AS destination_currency,
                transactions.inflow_minor,
                categories.name AS category,
                transactions.counterparty,
                transactions.note,
                investment_valuations.id AS investment_valuation_id
            FROM transactions
            LEFT JOIN accounts AS source ON source.id = transactions.source_account_id
            LEFT JOIN accounts AS destination ON destination.id = transactions.destination_account_id
            LEFT JOIN categories ON categories.id = transactions.category_id
            LEFT JOIN investment_valuations
                ON investment_valuations.generated_transaction_id = transactions.id
            ORDER BY transactions.transaction_date DESC, transactions.id DESC
            """
        ).fetchall()
        budget_rows = connection.execute(
            """
            SELECT
                monthly_budgets.budget_month,
                monthly_budgets.category_id,
                monthly_budgets.currency_code,
                categories.name AS category,
                monthly_budgets.amount_minor AS budget_minor,
                COALESCE((
                    SELECT SUM(transactions.outflow_minor)
                    FROM transactions
                    JOIN accounts AS expense_account
                        ON expense_account.id = transactions.source_account_id
                    WHERE transactions.transaction_type = 'expense'
                      AND transactions.category_id = monthly_budgets.category_id
                      AND expense_account.currency_code = monthly_budgets.currency_code
                      AND strftime('%Y-%m', transactions.transaction_date)
                          = substr(monthly_budgets.budget_month, 1, 7)
                ), 0) AS actual_minor
            FROM monthly_budgets
            JOIN categories ON categories.id = monthly_budgets.category_id
            ORDER BY monthly_budgets.budget_month, monthly_budgets.currency_code, categories.sort_order
            """
        ).fetchall()
        snapshot_rows = connection.execute(
            """
            SELECT *
            FROM investment_snapshots
            ORDER BY snapshot_date DESC, id DESC
            """
        ).fetchall()
        valuation_rows = connection.execute(
            """
            SELECT
                investment_valuations.*,
                accounts.name AS account_name,
                accounts.currency_code,
                currencies.decimal_places,
                transactions.outflow_minor AS generated_outflow_minor,
                transactions.inflow_minor AS generated_inflow_minor
            FROM investment_valuations
            JOIN accounts ON accounts.id = investment_valuations.account_id
            JOIN currencies ON currencies.code = accounts.currency_code
            LEFT JOIN transactions
                ON transactions.id = investment_valuations.generated_transaction_id
            ORDER BY investment_valuations.valuation_date DESC, investment_valuations.id DESC
            """
        ).fetchall()
        exchange_rate_rows = connection.execute(
            """
            SELECT *
            FROM daily_cny_exchange_rates
            ORDER BY rate_date DESC
            """
        ).fetchall()
        currency_rows = connection.execute(
            "SELECT code, name, decimal_places, is_active FROM currencies ORDER BY code"
        ).fetchall()
        category_rows = connection.execute(
            """
            SELECT categories.kind, parent.name AS parent_name, categories.name, categories.is_active
            FROM categories
            LEFT JOIN categories AS parent ON parent.id = categories.parent_id
            ORDER BY categories.kind, COALESCE(parent.sort_order, categories.sort_order),
                     parent.name, categories.sort_order, categories.name
            """
        ).fetchall()

    workbook = Workbook()
    workbook.remove(workbook.active)

    overview = workbook.create_sheet("月度总览")
    sheet_title(overview, "个人财务月度总览", 8)
    overview["A2"] = f"查看月份：{selected_month}"
    sheet_headers(
        overview,
        ["币种", "收入", "支出", "当月结余", "可用现金", "资产", "负债", "净资产"],
    )
    summary_by_currency = {
        item["currency_code"]: item for item in dashboard_result["cash_flow"]
    }
    available_cash_by_currency = {
        item["currency_code"]: item for item in dashboard_result["available_cash"]
    }
    worth_by_currency = {
        item["currency_code"]: item for item in dashboard_result["net_worth"]
    }
    for row_number, currency_code in enumerate(
        sorted(set(summary_by_currency) | set(available_cash_by_currency) | set(worth_by_currency)),
        4,
    ):
        cash_flow = summary_by_currency.get(currency_code, {})
        available_cash = available_cash_by_currency.get(currency_code, {})
        worth = worth_by_currency.get(currency_code, {})
        overview.append(
            [
                currency_code,
                cash_flow.get("income", "0.00"),
                cash_flow.get("expense", "0.00"),
                cash_flow.get("balance", "0.00"),
                available_cash.get("available_cash", "0.00"),
                worth.get("assets", "0.00"),
                worth.get("liabilities", "0.00"),
                worth.get("net_worth", "0.00"),
            ]
        )
    autosize_columns(overview)

    base_currency = workbook.create_sheet("本币总统计")
    sheet_title(base_currency, "人民币与港币计价资产、负债与净资产", 8)
    base_currency["A2"] = f"统计截至：{dashboard_result['as_of_date']}"
    sheet_headers(
        base_currency,
        [
            "本币",
            "总资产",
            "总负债",
            "净资产",
            "是否完整",
            "待补汇率币种",
            "汇率快照日期",
            "数据源",
        ],
    )
    for item in dashboard_result["base_currency_totals"]:
        rate = item["rate"] or {}
        base_currency.append(
            [
                item["currency_code"],
                item["total_assets"],
                item["total_liabilities"],
                item["net_worth"],
                "是" if item["is_complete"] else "否",
                "、".join(item["missing_currencies"]),
                rate.get("rate_date", ""),
                rate.get("source", ""),
            ]
        )
    autosize_columns(base_currency)

    accounts = workbook.create_sheet("账户余额")
    sheet_title(accounts, "账户余额与对账基础数据", 10)
    sheet_headers(
        accounts,
        ["编号", "名称", "资产/负债", "类型", "币种", "期初余额", "累计流入", "累计流出", "账面余额", "备注"],
    )
    for row in account_rows:
        places = row["decimal_places"]
        accounts.append(
            [
                row["code"],
                row["name"],
                "资产" if row["account_class"] == "asset" else "负债",
                row["account_type"],
                row["currency_code"],
                format_minor(row["opening_balance_minor"], places),
                format_minor(row["total_inflow_minor"], places),
                format_minor(row["total_outflow_minor"], places),
                format_minor(row["balance_minor"], places),
                row["note"],
            ]
        )
    autosize_columns(accounts)

    transactions_sheet = workbook.create_sheet("交易流水")
    sheet_title(transactions_sheet, "交易流水", 11)
    sheet_headers(
        transactions_sheet,
        ["日期", "类型", "来源账户", "付出金额", "付出币种", "去向账户", "收到金额", "收到币种", "分类", "对象", "备注"],
    )
    for row in transaction_rows:
        outflow = (
            format_minor(
                row["outflow_minor"],
                next(
                    currency["decimal_places"]
                    for currency in currency_rows
                    if currency["code"] == row["source_currency"]
                ),
            )
            if row["outflow_minor"] is not None
            else None
        )
        inflow = (
            format_minor(
                row["inflow_minor"],
                next(
                    currency["decimal_places"]
                    for currency in currency_rows
                    if currency["code"] == row["destination_currency"]
                ),
            )
            if row["inflow_minor"] is not None
            else None
        )
        transactions_sheet.append(
            [
                row["transaction_date"],
                transaction_type_label(
                    row["transaction_type"],
                    row["investment_valuation_id"],
                ),
                row["source_account"],
                outflow,
                row["source_currency"],
                row["destination_account"],
                inflow,
                row["destination_currency"],
                row["category"],
                row["counterparty"],
                row["note"],
            ]
        )
    autosize_columns(transactions_sheet)

    internal_movements_sheet = workbook.create_sheet("账户内流动")
    sheet_title(internal_movements_sheet, "账户内资金流动", 6)
    internal_movements_sheet["A2"] = f"查看月份：{selected_month}"
    sheet_headers(
        internal_movements_sheet,
        ["币种", "内部转出", "内部转入", "涉及笔数"],
    )
    for item in dashboard_result["internal_movement_summary"]:
        internal_movements_sheet.append(
            [
                item["currency_code"],
                item["outflow"],
                item["inflow"],
                item["transaction_count"],
            ]
        )
    detail_header_row = internal_movements_sheet.max_row + 2
    sheet_headers(
        internal_movements_sheet,
        ["日期", "类型", "来源账户", "转出金额", "去向账户", "收到金额"],
        row=detail_header_row,
    )
    for item in dashboard_result["internal_movements"]:
        internal_movements_sheet.append(
            [
                item["transaction_date"],
                item["transaction_type_label"],
                item["source_account_name"],
                f"{item['outflow_amount']} {item['source_currency_code']}",
                item["destination_account_name"],
                f"{item['inflow_amount']} {item['destination_currency_code']}",
            ]
        )
    autosize_columns(internal_movements_sheet)

    budgets = workbook.create_sheet("月度预算")
    sheet_title(budgets, "月度预算与执行", 7)
    sheet_headers(budgets, ["月份", "币种", "分类", "预算", "实际支出", "差额", "执行率"])
    currency_places = {row["code"]: row["decimal_places"] for row in currency_rows}
    for row in budget_rows:
        places = currency_places[row["currency_code"]]
        budget = row["budget_minor"]
        actual = row["actual_minor"]
        budgets.append(
            [
                row["budget_month"][:7],
                row["currency_code"],
                row["category"],
                format_minor(budget, places),
                format_minor(actual, places),
                format_minor(budget - actual, places),
                f"{(actual / budget * 100):.1f}%" if budget else "",
            ]
        )
    autosize_columns(budgets)

    snapshots = workbook.create_sheet("投资快照")
    sheet_title(snapshots, "投资快照", 10)
    sheet_headers(
        snapshots,
        ["日期", "平台", "币种", "券商现金", "证券/基金市值", "其他投资资产", "总资产", "入金", "出金", "已实现收益"],
    )
    for row in snapshot_rows:
        places = currency_places[row["currency_code"]]
        total = row["cash_minor"] + row["market_value_minor"] + row["other_assets_minor"]
        snapshots.append(
            [
                row["snapshot_date"],
                row["platform"],
                row["currency_code"],
                format_minor(row["cash_minor"], places),
                format_minor(row["market_value_minor"], places),
                format_minor(row["other_assets_minor"], places),
                format_minor(total, places),
                format_minor(row["deposit_minor"], places),
                format_minor(row["withdrawal_minor"], places),
                format_minor(row["realized_profit_minor"], places),
            ]
        )
    autosize_columns(snapshots)

    valuations = workbook.create_sheet("投资估值")
    sheet_title(valuations, "投资账户期末估值与自动损益", 7)
    sheet_headers(
        valuations,
        ["日期", "账户", "币种", "账户总资产", "自动损益", "关联流水", "备注"],
    )
    for row in valuation_rows:
        generated_change_minor = (
            row["generated_inflow_minor"]
            if row["generated_inflow_minor"] is not None
            else -row["generated_outflow_minor"]
            if row["generated_outflow_minor"] is not None
            else 0
        )
        valuations.append(
            [
                row["valuation_date"],
                row["account_name"],
                row["currency_code"],
                format_minor(row["total_value_minor"], row["decimal_places"]),
                format_minor(generated_change_minor, row["decimal_places"]),
                row["generated_transaction_id"],
                row["note"],
            ]
        )
    autosize_columns(valuations)

    exchange_rates = workbook.create_sheet("每日汇率")
    sheet_title(exchange_rates, "美元和港币兑人民币每日快照", 7)
    sheet_headers(
        exchange_rates,
        ["快照日期", "USD/CNY", "HKD/CNY", "数据源", "报价时间", "市场日期", "备注"],
    )
    for row in exchange_rate_rows:
        exchange_rates.append(
            [
                row["rate_date"],
                format_decimal(Decimal(row["usd_cny_rate"]), 4),
                format_decimal(Decimal(row["hkd_cny_rate"]), 4),
                row["source"],
                row["quoted_at"],
                row["market_date"],
                row["note"],
            ]
        )
    autosize_columns(exchange_rates)

    settings = workbook.create_sheet("设置")
    sheet_title(settings, "币种与分类", 4)
    sheet_headers(settings, ["类型", "代码/分类", "名称/父分类", "是否启用"])
    for row in currency_rows:
        settings.append(
            [
                "币种",
                row["code"],
                f"{row['name']}（{row['decimal_places']} 位小数）",
                "是" if row["is_active"] else "否",
            ]
        )
    for row in category_rows:
        settings.append(
            [
                "收入分类" if row["kind"] == "income" else "支出分类",
                row["name"],
                row["parent_name"] or "",
                "是" if row["is_active"] else "否",
            ]
        )
    autosize_columns(settings)

    from app.offset_export import append_offset_sheets
    append_offset_sheets(workbook, export_entries, export_rates, budget_rows, currency_places,
                         selected_month, dashboard_result["as_of_date"], basis)
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


@app.get("/api/export.xlsx")
def export_excel(month: str | None = None, basis: str = "raw"):
    selected_month = validate_month(month) if month else date.today().strftime("%Y-%m")
    output = build_excel_export(selected_month, basis)
    filename = quote(f"个人财务账本-{selected_month}.xlsx")
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )

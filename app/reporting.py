from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_FLOOR
from typing import Any


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


def format_decimal(value: Decimal, decimal_places: int = 4) -> str:
    quantizer = Decimal(1).scaleb(-decimal_places)
    return f"{value.quantize(quantizer, rounding=ROUND_HALF_UP):.{decimal_places}f}"


def analysis_main_amount(row) -> dict[str, Any]:
    transaction_type = row["transaction_type"]
    if transaction_type == "income" or (
        transaction_type == "adjustment" and row["inflow_minor"] is not None
    ):
        return {
            "amount": amount_from_minor(
                row["inflow_minor"],
                row["destination_decimal_places"],
            ),
            "currency_code": row["destination_currency_code"],
            "account_name": row["destination_account_name"] or "外部",
            "direction": "调增" if transaction_type == "adjustment" else None,
        }
    return {
        "amount": amount_from_minor(
            row["outflow_minor"],
            row["source_decimal_places"],
        ),
        "currency_code": row["source_currency_code"],
        "account_name": row["source_account_name"] or "外部",
        "direction": "调减" if transaction_type == "adjustment" else None,
    }


def nearest_cny_exchange_rate(
    rate_rows,
    transaction_date: str,
):
    target_date = date.fromisoformat(transaction_date)
    return min(
        rate_rows,
        key=lambda row: (
            abs((date.fromisoformat(row["rate_date"]) - target_date).days),
            0 if row["rate_date"] <= transaction_date else 1,
        ),
    )


def amount_to_base_currency(
    amount: Decimal,
    currency_code: str,
    transaction_date: str,
    rate_rows,
    base_currency_code: str,
) -> tuple[Decimal | None, str | None]:
    if currency_code == base_currency_code:
        return amount, "direct"
    if (
        base_currency_code not in BASE_CURRENCY_CODES
        or currency_code not in {"CNY", "HKD", "USD"}
        or not rate_rows
    ):
        return None, None
    rate = nearest_cny_exchange_rate(rate_rows, transaction_date)
    try:
        hkd_cny_rate = Decimal(rate["hkd_cny_rate"])
        usd_cny_rate = Decimal(rate["usd_cny_rate"])
    except InvalidOperation:
        return None, None
    if hkd_cny_rate <= 0 or usd_cny_rate <= 0:
        return None, None
    if base_currency_code == "CNY":
        converted = (
            amount * hkd_cny_rate
            if currency_code == "HKD"
            else amount * usd_cny_rate
        )
    else:
        converted = (
            amount / hkd_cny_rate
            if currency_code == "CNY"
            else amount * usd_cny_rate / hkd_cny_rate
        )
    return converted, "exact" if rate["rate_date"] == transaction_date else "nearest"


def amount_to_hkd(
    amount: Decimal,
    currency_code: str,
    transaction_date: str,
    rate_rows,
) -> tuple[Decimal | None, str | None]:
    return amount_to_base_currency(
        amount,
        currency_code,
        transaction_date,
        rate_rows,
        "HKD",
    )


def nice_hkd_bucket_width(target_width: Decimal) -> int:
    """Return a 1/2/5-based integer HKD bucket width near the target width."""
    target_width = max(target_width, Decimal(1))
    exponent = max(target_width.adjusted(), 0)
    candidates = [
        multiplier * (10**power)
        for power in range(max(0, exponent - 2), exponent + 3)
        for multiplier in (1, 2, 5)
    ]
    return min(
        candidates,
        key=lambda candidate: (abs(Decimal(candidate) - target_width), candidate),
    )


def next_nice_hkd_bucket_width(current_width: int) -> int:
    exponent = len(str(current_width))
    candidates = [
        multiplier * (10**power)
        for power in range(0, exponent + 2)
        for multiplier in (1, 2, 5)
    ]
    return min(candidate for candidate in candidates if candidate > current_width)


def hkd_bucket_bounds(
    minimum: Decimal,
    maximum: Decimal,
    width: int,
) -> tuple[int, int]:
    lower = int((minimum / width).to_integral_value(rounding=ROUND_FLOOR)) * width
    upper = int((maximum / width).to_integral_value(rounding=ROUND_FLOOR)) * width
    if Decimal(upper) < maximum:
        upper += width
    if lower == upper:
        upper += width
    return lower, upper


def build_hkd_histogram(daily_amounts: list[Decimal]) -> list[dict[str, Any]]:
    if not daily_amounts:
        return []
    minimum = min(daily_amounts)
    maximum = max(daily_amounts)
    target_bin_count = min(8, max(2, round(len(daily_amounts) ** 0.5)))
    target_width = max((maximum - minimum) / Decimal(target_bin_count), Decimal(1))
    width = nice_hkd_bucket_width(target_width)
    lower, upper = hkd_bucket_bounds(minimum, maximum, width)

    while (upper - lower) // width > 8:
        width = next_nice_hkd_bucket_width(width)
        lower, upper = hkd_bucket_bounds(minimum, maximum, width)

    bin_count = (upper - lower) // width
    counts = [0] * bin_count
    for amount in daily_amounts:
        index = min(int((amount - Decimal(lower)) // width), bin_count - 1)
        counts[index] += 1
    return [
        {
            "start": lower + width * index,
            "end": lower + width * (index + 1),
            "count": counts[index],
        }
        for index in range(bin_count)
    ]


def build_transaction_analysis(
    rows,
    rate_rows,
    start_date: str,
    end_date: str,
    transaction_type: str,
) -> dict[str, Any]:
    converted_records = []
    rate_coverage = {
        "direct_hkd_count": 0,
        "exact_rate_count": 0,
        "nearest_rate_count": 0,
        "excluded_count": 0,
        "missing_currencies": set(),
    }

    for row in rows:
        main_amount = analysis_main_amount(row)
        amount_hkd, rate_status = amount_to_hkd(
            main_amount["amount"],
            main_amount["currency_code"],
            row["transaction_date"],
            rate_rows,
        )
        if amount_hkd is None:
            rate_coverage["excluded_count"] += 1
            rate_coverage["missing_currencies"].add(main_amount["currency_code"])
            continue
        if rate_status == "direct":
            rate_coverage["direct_hkd_count"] += 1
        elif rate_status == "exact":
            rate_coverage["exact_rate_count"] += 1
        else:
            rate_coverage["nearest_rate_count"] += 1
        converted_records.append(
            {
                "transaction_date": row["transaction_date"],
                "amount_hkd": amount_hkd,
                "category_name": row["category_name"] or "未分类",
                "account_name": main_amount["account_name"],
                "direction": main_amount["direction"],
            }
        )

    return summarize_daily_records(converted_records, rate_coverage, len(rows), start_date, end_date, transaction_type)


def summarize_daily_records(converted_records, rate_coverage, matched_count, start_date, end_date, transaction_type):
    daily_totals: dict[str, dict[str, Any]] = {}
    category_totals: dict[str, dict[str, Any]] = {}
    account_totals: dict[str, dict[str, Any]] = {}
    weekday_totals = [
        {
            "weekday": index,
            "label": label,
            "amount": Decimal(0),
            "active_day_count": 0,
        }
        for index, label in enumerate(WEEKDAY_LABELS)
    ]
    direction_totals: dict[str, dict[str, Any]] = {}
    for record in converted_records:
        day = daily_totals.setdefault(
            record["transaction_date"],
            {"amount": Decimal(0), "count": 0},
        )
        day["amount"] += record["amount_hkd"]
        day["count"] += 1

        category = category_totals.setdefault(
            record["category_name"],
            {"amount": Decimal(0), "count": 0},
        )
        category["amount"] += record["amount_hkd"]
        category["count"] += 1

        account = account_totals.setdefault(
            record["account_name"],
            {"amount": Decimal(0), "count": 0},
        )
        account["amount"] += record["amount_hkd"]
        account["count"] += 1

        if record["direction"]:
            direction = direction_totals.setdefault(
                record["direction"],
                {"amount": Decimal(0), "count": 0},
            )
            direction["amount"] += record["amount_hkd"]
            direction["count"] += 1

    daily_amounts = [values["amount"] for values in daily_totals.values()]
    for transaction_date, values in daily_totals.items():
        weekday = weekday_totals[date.fromisoformat(transaction_date).weekday()]
        weekday["amount"] += values["amount"]
        weekday["active_day_count"] += 1

    statistics = None
    if daily_amounts:
        total = sum(daily_amounts, Decimal(0))
        active_day_count = len(daily_amounts)
        range_day_count = (
            date.fromisoformat(end_date) - date.fromisoformat(start_date)
        ).days + 1
        active_day_mean = total / active_day_count
        calendar_day_mean = total / range_day_count
        sorted_daily_amounts = sorted(daily_amounts)
        middle = active_day_count // 2
        median = (
            sorted_daily_amounts[middle]
            if active_day_count % 2
            else (
                sorted_daily_amounts[middle - 1] + sorted_daily_amounts[middle]
            )
            / 2
        )
        variance = sum(
            ((amount - active_day_mean) ** 2 for amount in daily_amounts),
            Decimal(0),
        ) / active_day_count
        maximum_day_date, maximum_day = max(
            daily_totals.items(),
            key=lambda item: (item[1]["amount"], item[0]),
        )
        statistics = {
            "total_amount": format_decimal(total, 2),
            "converted_transaction_count": len(converted_records),
            "active_day_count": active_day_count,
            "range_day_count": range_day_count,
            "calendar_day_mean": format_decimal(calendar_day_mean, 2),
            "active_day_mean": format_decimal(active_day_mean, 2),
            "median_daily_amount": format_decimal(median, 2),
            "minimum_daily_amount": format_decimal(min(daily_amounts), 2),
            "maximum_daily_amount": format_decimal(max(daily_amounts), 2),
            "population_daily_variance": format_decimal(variance, 4),
            "population_daily_standard_deviation": format_decimal(
                variance.sqrt(),
                2,
            ),
            "maximum_day_date": maximum_day_date,
            "maximum_day_amount": format_decimal(maximum_day["amount"], 2),
        }

    def serialize_distribution(
        items: dict[str, dict[str, Any]],
        include_amount_share: bool = False,
    ) -> list[dict[str, Any]]:
        total_amount = sum(
            (values["amount"] for values in items.values()),
            Decimal(0),
        )
        return [
            {
                "name": name,
                "amount": format_decimal(values["amount"], 2),
                "count": values["count"],
                **(
                    {
                        "amount_share": format_decimal(
                            values["amount"] * Decimal(100) / total_amount,
                            1,
                        ),
                    }
                    if include_amount_share and total_amount > 0
                    and not rate_coverage["excluded_count"]
                    and all(value["amount"] >= 0 for value in items.values())
                    else {}
                ),
            }
            for name, values in sorted(
                items.items(),
                key=lambda item: (-item[1]["amount"], item[0]),
            )
        ]

    return {
        "transaction_type": transaction_type,
        "transaction_type_label": TRANSACTION_TYPE_LABELS[transaction_type],
        "main_amount_label": TRANSACTION_ANALYSIS_AMOUNT_LABELS[transaction_type],
        "account_label": TRANSACTION_ANALYSIS_ACCOUNT_LABELS[transaction_type],
        "currency_code": "HKD",
        "matched_transaction_count": matched_count,
        "rate_coverage": {
            "direct_hkd_count": rate_coverage["direct_hkd_count"],
            "exact_rate_count": rate_coverage["exact_rate_count"],
            "nearest_rate_count": rate_coverage["nearest_rate_count"],
            "excluded_count": rate_coverage["excluded_count"],
            "missing_currencies": sorted(rate_coverage["missing_currencies"]),
        },
        "daily_totals": [
            {
                "transaction_date": transaction_date,
                "amount": format_decimal(values["amount"], 2),
                "count": values["count"],
            }
            for transaction_date, values in sorted(daily_totals.items())
        ],
        "statistics": statistics,
        "histogram": build_hkd_histogram(daily_amounts),
        "weekday_distribution": [
            {
                "weekday": item["weekday"],
                "label": item["label"],
                "amount": format_decimal(item["amount"], 2),
                "active_day_count": item["active_day_count"],
            }
            for item in weekday_totals
        ],
        "category_distribution": serialize_distribution(
            category_totals,
            include_amount_share=True,
        ),
        "account_distribution": serialize_distribution(account_totals),
        "direction_distribution": serialize_distribution(direction_totals),
    }



def amount_from_minor(amount_minor: int, decimal_places: int) -> Decimal:
    return Decimal(amount_minor) / (Decimal(10) ** decimal_places)

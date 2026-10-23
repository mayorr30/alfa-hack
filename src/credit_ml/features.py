"""Инженерия признаков: из сырых данных заявки и внешних источников в признаки модели."""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import PRODUCTS_BY_CODE

NUMERIC_FEATURES = [
    "business_age_months",
    "log_revenue",
    "net_margin",
    "revenue_trend",
    "revenue_volatility",
    "debt_to_revenue",
    "dscr",
    "loan_to_revenue",
    "bki_max_overdue_days_12m",
    "bki_overdue_count_12m",
    "bki_inquiries_3m",
    "director_changes_12m",
]
CATEGORICAL_FEATURES = ["legal_form", "industry", "tax_arrears", "mass_address"]
MODEL_FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

FEATURE_LABELS = {
    "business_age_months": "Срок ведения бизнеса",
    "log_revenue": "Масштаб выручки",
    "net_margin": "Рентабельность",
    "revenue_trend": "Динамика выручки",
    "revenue_volatility": "Стабильность оборотов",
    "debt_to_revenue": "Текущая долговая нагрузка",
    "dscr": "Покрытие платежей денежным потоком (DSCR)",
    "loan_to_revenue": "Размер запроса относительно выручки",
    "bki_max_overdue_days_12m": "Максимальная просрочка в БКИ",
    "bki_overdue_count_12m": "Количество просрочек в БКИ",
    "bki_inquiries_3m": "Число запросов кредитной истории за 3 мес.",
    "director_changes_12m": "Смены руководителя за 12 мес.",
    "legal_form": "Организационно-правовая форма",
    "industry": "Отрасль",
    "tax_arrears": "Налоговая задолженность",
    "mass_address": "Адрес массовой регистрации",
}


def annuity_payment(amount: float, annual_rate: float, months: int) -> float:
    """Ежемесячный аннуитетный платеж."""
    if months <= 0:
        raise ValueError("months must be positive")
    r = annual_rate / 12.0
    if r == 0:
        return amount / months
    return amount * r / (1.0 - (1.0 + r) ** (-months))


def max_amount_for_payment(payment: float, annual_rate: float, months: int) -> float:
    """Обратная задача: максимальная сумма при заданном платеже."""
    if payment <= 0:
        return 0.0
    r = annual_rate / 12.0
    if r == 0:
        return payment * months
    return payment * (1.0 - (1.0 + r) ** (-months)) / r


def monthly_cash_flow(annual_revenue: float, net_margin: float) -> float:
    """Упрощенный операционный денежный поток в месяц (до обслуживания долга)."""
    return annual_revenue * net_margin / 12.0


def compute_dscr(annual_revenue: float, net_margin: float,
                 existing_monthly_payments: float, new_payment: float) -> float:
    """DSCR = денежный поток до обслуживания долга / все платежи по долгу.

    Прибыль считается после текущих процентов, поэтому к ней возвращаем
    текущие платежи, а делим на сумму текущих и нового платежа.
    """
    cf_before_debt = monthly_cash_flow(annual_revenue, net_margin) + existing_monthly_payments
    total = existing_monthly_payments + new_payment
    if total <= 0:
        return 10.0
    return float(np.clip(cf_before_debt / total, -5.0, 10.0))


def build_features(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Признаки модели для одной заявки.

    raw — плоский словарь: анкета клиента + данные ЕГРЮЛ/ФНС/БКИ (после обогащения).
    """
    product = PRODUCTS_BY_CODE[raw["product_code"]]
    revenue = float(raw["annual_revenue"])
    new_payment = annuity_payment(float(raw["requested_amount"]), product.base_rate,
                                  int(raw["term_months"]))
    existing_debt = float(raw.get("existing_debt", 0.0) or 0.0)
    existing_pay = float(raw.get("existing_monthly_payments", 0.0) or 0.0)
    return {
        "business_age_months": float(raw["business_age_months"]),
        "log_revenue": math.log10(max(revenue, 1.0)),
        "net_margin": float(raw["net_margin"]),
        "revenue_trend": float(raw.get("revenue_trend", 0.0) or 0.0),
        "revenue_volatility": float(raw.get("revenue_volatility", 0.3) or 0.0),
        "debt_to_revenue": existing_debt / max(revenue, 1.0),
        "dscr": compute_dscr(revenue, float(raw["net_margin"]), existing_pay, new_payment),
        "loan_to_revenue": float(raw["requested_amount"]) / max(revenue, 1.0),
        "bki_max_overdue_days_12m": float(raw.get("bki_max_overdue_days_12m", 0) or 0),
        "bki_overdue_count_12m": float(raw.get("bki_overdue_count_12m", 0) or 0),
        "bki_inquiries_3m": float(raw.get("bki_inquiries_3m", 0) or 0),
        "director_changes_12m": float(raw.get("director_changes_12m", 0) or 0),
        "legal_form": str(raw["legal_form"]),
        "industry": str(raw["industry"]),
        "tax_arrears": "yes" if raw.get("tax_arrears") else "no",
        "mass_address": "yes" if raw.get("mass_address") else "no",
        "_new_payment": new_payment,
    }


def build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Векторная версия build_features для обучения."""
    rows = [build_features(r) for r in df.to_dict(orient="records")]
    out = pd.DataFrame(rows)
    return out


# --- ИНН: контрольные разряды (используется генератором и валидацией) ---

_W10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_W11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_W12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def _ctrl(digits: list[int], weights: tuple[int, ...]) -> int:
    return sum(d * w for d, w in zip(digits, weights)) % 11 % 10


def inn_is_valid(inn: str) -> bool:
    if not inn.isdigit() or len(inn) not in (10, 12):
        return False
    d = [int(c) for c in inn]
    if len(inn) == 10:
        return _ctrl(d[:9], _W10) == d[9]
    return _ctrl(d[:10], _W11) == d[10] and _ctrl(d[:11], _W12) == d[11]


def make_inn(rng: np.random.Generator, legal_form: str) -> str:
    """Синтетический ИНН с корректными контрольными разрядами (регион 77)."""
    if legal_form == "OOO":
        d = [7, 7] + list(rng.integers(0, 10, 7))
        d.append(_ctrl(d, _W10))
    else:
        d = [7, 7] + list(rng.integers(0, 10, 8))
        d.append(_ctrl(d, _W11))
        d.append(_ctrl(d, _W12))
    return "".join(str(int(x)) for x in d)

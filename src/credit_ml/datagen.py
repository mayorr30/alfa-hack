"""Генератор синтетических данных.

1. Обучающая выборка заявок с исходом (дефолт в течение 12 мес. — да/нет).
2. Демо-компании для моков ЕГРЮЛ / ФНС «Прозрачный бизнес» / БКИ / выписки.

Все данные выдуманы. Зависимости между признаками и риском заданы вручную
в функции latent_risk и описаны в docs/ml_design.md. Это не статистика
реального рынка: модель учится восстанавливать заложенную логику.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DATA_DIR, INDUSTRIES, PRODUCTS, RANDOM_SEED
from .features import build_feature_frame, make_inn, max_amount_for_payment

INDUSTRY_CODES = list(INDUSTRIES)
INDUSTRY_P = [0.26, 0.10, 0.10, 0.08, 0.10, 0.12, 0.18, 0.06]
INDUSTRY_MARGIN = {  # средняя рентабельность в генераторе
    "trade": 0.06, "food": 0.08, "construction": 0.07, "it": 0.15,
    "manufacturing": 0.09, "transport": 0.07, "services": 0.12, "agro": 0.08,
}
INDUSTRY_RISK = {  # надбавка к логиту риска
    "trade": 0.0, "food": 0.25, "construction": 0.35, "it": -0.2,
    "manufacturing": -0.05, "transport": 0.15, "services": 0.0, "agro": 0.2,
}
PURPOSE_P = {
    "cash_gap": 0.20, "working_capital": 0.32, "contract": 0.08,
    "equipment": 0.18, "expansion": 0.12, "real_estate": 0.04, "other": 0.06,
}
OKVED = {
    "trade": "47.19", "food": "56.10", "construction": "41.20", "it": "62.01",
    "manufacturing": "25.62", "transport": "49.41", "services": "96.02", "agro": "01.11",
}


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _pick_product(purpose: str, age: float, legal_form: str, rng) -> str | None:
    cands = [p for p in PRODUCTS if purpose in p.purposes
             and age >= p.min_business_age and legal_form in p.legal_forms]
    if not cands:
        cands = [p for p in PRODUCTS if p.code == "EXPRESS" and age >= p.min_business_age]
    if not cands:
        return None
    return cands[int(rng.integers(0, len(cands)))].code


def latent_risk(f: pd.DataFrame, rng, drift: np.ndarray) -> np.ndarray:
    """Заложенная «истинная» логика риска (логит вероятности дефолта)."""
    od = f["bki_max_overdue_days_12m"].to_numpy()
    od_sev = np.select([od == 0, od <= 30, od <= 60], [0.0, 0.8, 1.6], 2.4)
    z = (
        -3.95
        + od_sev
        + 0.25 * np.minimum(f["bki_overdue_count_12m"], 5)
        - 0.45 * np.log(np.clip(f["business_age_months"], 3, 240) / 24.0)
        + 1.4 * np.clip(1.4 - f["dscr"], -1.0, 2.0)
        + 1.1 * np.clip(f["debt_to_revenue"], 0, 1.5)
        + 1.2 * f["revenue_volatility"]
        - 1.3 * np.clip(f["revenue_trend"], -0.5, 0.6)
        - 2.5 * np.clip(f["net_margin"], -0.2, 0.35)
        + 0.12 * f["bki_inquiries_3m"]
        + 0.35 * f["director_changes_12m"]
        + 0.6 * (f["tax_arrears"] == "yes")
        + 0.45 * (f["mass_address"] == "yes")
        + f["industry"].map(INDUSTRY_RISK).to_numpy()
        - 0.15 * (f["legal_form"] == "OOO")
        + drift
        + rng.normal(0, 0.45, len(f))
    )
    return z.to_numpy() if hasattr(z, "to_numpy") else z


def generate_applications(n: int = 30_000, seed: int = RANDOM_SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    legal_form = np.where(rng.random(n) < 0.55, "IP", "OOO")
    industry = rng.choice(INDUSTRY_CODES, n, p=INDUSTRY_P)
    age = np.clip(np.round(np.exp(rng.normal(np.log(40), 0.85, n))), 3, 300)

    rev_mu = np.where(legal_form == "OOO", np.log(45e6), np.log(12e6))
    revenue = np.exp(rng.normal(rev_mu + 0.25 * np.log(age / 36), 0.9))
    revenue = np.clip(revenue, 1.2e6, 2.0e9).round(-3)

    margin = rng.normal([INDUSTRY_MARGIN[i] for i in industry], 0.06)
    margin = np.clip(margin, -0.15, 0.40).round(3)
    trend = np.clip(rng.normal(0.08, 0.18, n), -0.5, 0.8).round(3)
    volatility = np.clip(rng.gamma(2.2, 0.12, n), 0.03, 1.2).round(3)

    has_debt = rng.random(n) < 0.62
    debt = np.where(has_debt, revenue * np.clip(rng.gamma(1.6, 0.13, n), 0, 1.6), 0.0).round(-3)
    # текущие платежи: аннуитет на остаток 24 мес. по 20% годовых
    r = 0.20 / 12
    existing_pay = (debt * r / (1 - (1 + r) ** -24)).round(0)

    # платежная дисциплина коррелирует с долговой нагрузкой и маржой
    od_base = 0.10 + 0.25 * (debt / revenue) - 0.4 * np.clip(margin, -0.1, 0.3)
    has_od = rng.random(n) < np.clip(od_base, 0.02, 0.6)
    od_days = np.where(has_od, np.clip(rng.gamma(1.3, 18, n), 1, 89), 0).round(0)
    od_count = np.where(has_od, 1 + rng.poisson(1.0, n), 0)
    inquiries = rng.poisson(1.0 + 1.5 * has_od, n)
    dir_changes = rng.choice([0, 1, 2, 3], n, p=[0.86, 0.10, 0.03, 0.01])
    tax_arrears = rng.random(n) < 0.05 + 0.10 * has_od
    mass_address = (legal_form == "OOO") & (rng.random(n) < 0.04)

    purposes = list(PURPOSE_P)
    purpose = rng.choice(purposes, n, p=list(PURPOSE_P.values()))
    products, amounts, terms = [], [], []
    for i in range(n):
        code = _pick_product(purpose[i], age[i], legal_form[i], rng)
        products.append(code)
        if code is None:
            amounts.append(np.nan)
            terms.append(np.nan)
            continue
        p = next(x for x in PRODUCTS if x.code == code)
        term = int(rng.choice([t for t in (3, 6, 12, 18, 24, 36, 48, 60, 84)
                               if p.min_term <= t <= p.max_term]))
        # клиент обычно просит сумму, соизмеримую с денежным потоком;
        # часть заявок намеренно «перезапрошена» (целевой DSCR < 1.25)
        cf = revenue[i] * margin[i] / 12 + existing_pay[i]
        target_dscr = float(np.exp(rng.normal(np.log(1.9), 0.5)))
        pay_room = cf / target_dscr - existing_pay[i]
        if pay_room > 0:
            amt = max_amount_for_payment(pay_room, p.base_rate, term)
        else:
            amt = revenue[i] * rng.uniform(0.02, 0.08)
        amt = float(np.clip(round(amt, -4), p.min_amount, p.max_amount))
        amounts.append(amt)
        terms.append(term)

    start = date(2024, 7, 1)
    day_offsets = rng.integers(0, 730, n)
    app_date = [start + timedelta(days=int(d)) for d in day_offsets]

    df = pd.DataFrame({
        "application_id": [f"A{100000 + i}" for i in range(n)],
        "application_date": pd.to_datetime(app_date),
        "inn": [make_inn(rng, lf) for lf in legal_form],
        "legal_form": legal_form,
        "industry": industry,
        "business_age_months": age.astype(int),
        "annual_revenue": revenue,
        "net_margin": margin,
        "revenue_trend": trend,
        "revenue_volatility": volatility,
        "existing_debt": debt,
        "existing_monthly_payments": existing_pay,
        "bki_max_overdue_days_12m": od_days.astype(int),
        "bki_overdue_count_12m": od_count.astype(int),
        "bki_inquiries_3m": inquiries.astype(int),
        "director_changes_12m": dir_changes.astype(int),
        "tax_arrears": tax_arrears,
        "mass_address": mass_address,
        "purpose": purpose,
        "product_code": products,
        "requested_amount": amounts,
        "term_months": terms,
    })
    df = df[df["product_code"].notna()].reset_index(drop=True)
    df["term_months"] = df["term_months"].astype(int)

    feats = build_feature_frame(df)
    # лёгкий дрейф: заявки последних 6 мес. чуть рискованнее (для проверки стабильности)
    months = (df["application_date"].dt.year - 2024) * 12 + df["application_date"].dt.month
    drift = np.where(months >= (2026 - 2024) * 12 + 1, 0.15, 0.0)
    z = latent_risk(feats, rng, drift)
    df["default_12m"] = (rng.random(len(df)) < _sigmoid(z)).astype(int)
    return df


# --------------------------------------------------------------------------
# Демо-компании для моков. Каждая рассчитана на свой исход кредитного решения.
# --------------------------------------------------------------------------

DEMO_COMPANIES = [
    {
        "key": "approve",
        "name": "ООО «Пекарня Колосок» (демо)", "legal_form": "OOO", "industry": "food",
        "reg_date": "2020-03-12", "director": "Смирнова Анна Викторовна (демо)",
        "egrul_status": "active", "director_changes_12m": 0, "mass_address": False,
        "tax_arrears": False, "has_reporting": True,
        "annual_revenue": 62_000_000, "net_margin": 0.14, "revenue_trend": 0.12,
        "revenue_volatility": 0.12, "existing_debt": 4_000_000,
        "bki": {"max_overdue_days_12m": 0, "overdue_count_12m": 0, "inquiries_3m": 1},
        "demo_request": {"purpose": "equipment", "product_code": "EQUIPMENT",
                         "requested_amount": 5_000_000, "term_months": 36},
        "expected": "APPROVE",
    },
    {
        "key": "counter_offer",
        "name": "ООО «ТехноСнаб» (демо)", "legal_form": "OOO", "industry": "trade",
        "reg_date": "2019-09-02", "director": "Волков Игорь Петрович (демо)",
        "egrul_status": "active", "director_changes_12m": 0, "mass_address": False,
        "tax_arrears": False, "has_reporting": True,
        "annual_revenue": 85_000_000, "net_margin": 0.10, "revenue_trend": 0.05,
        "revenue_volatility": 0.18, "existing_debt": 6_000_000,
        "bki": {"max_overdue_days_12m": 0, "overdue_count_12m": 0, "inquiries_3m": 1},
        "demo_request": {"purpose": "working_capital", "product_code": "WORKING_CAPITAL",
                         "requested_amount": 25_000_000, "term_months": 12},
        "expected": "COUNTER_OFFER",
    },
    {
        "key": "request_info",
        "name": "ИП Кузнецова Мария Олеговна (демо)", "legal_form": "IP", "industry": "services",
        "reg_date": "2021-05-20", "director": "Кузнецова Мария Олеговна (демо)",
        "egrul_status": "active", "director_changes_12m": 0, "mass_address": False,
        "tax_arrears": False, "has_reporting": False,
        "annual_revenue": None, "net_margin": None, "revenue_trend": None,
        "revenue_volatility": None, "existing_debt": 0,
        "bki": {"max_overdue_days_12m": 0, "overdue_count_12m": 0, "inquiries_3m": 0},
        "demo_request": {"purpose": "working_capital", "product_code": "EXPRESS",
                         "requested_amount": 1_500_000, "term_months": 24},
        "statement_profile": {"monthly_inflow": 1_150_000, "growth": 0.01, "noise": 0.12,
                              "expense_ratio": 0.82},
        "expected": "REQUEST_INFO",
    },
    {
        "key": "manual_review",
        "name": "ООО «СтройМонтаж-Плюс» (демо)", "legal_form": "OOO", "industry": "construction",
        "reg_date": "2018-11-15", "director": "Егоров Павел Сергеевич (демо)",
        "egrul_status": "active", "director_changes_12m": 1, "mass_address": True,
        "tax_arrears": False, "has_reporting": True,
        "annual_revenue": 95_000_000, "net_margin": 0.11, "revenue_trend": 0.06,
        "revenue_volatility": 0.22, "existing_debt": 9_000_000,
        "bki": {"max_overdue_days_12m": 0, "overdue_count_12m": 0, "inquiries_3m": 2},
        "demo_request": {"purpose": "contract", "product_code": "WORKING_CAPITAL",
                         "requested_amount": 8_000_000, "term_months": 18},
        "expected": "MANUAL_REVIEW",
    },
    {
        "key": "decline_stop",
        "name": "ООО «Вектор-Трейд» (демо)", "legal_form": "OOO", "industry": "trade",
        "reg_date": "2017-02-01", "director": "Павлов Олег Николаевич (демо)",
        "egrul_status": "liquidating", "director_changes_12m": 0, "mass_address": False,
        "tax_arrears": True, "has_reporting": True,
        "annual_revenue": 30_000_000, "net_margin": 0.03, "revenue_trend": -0.25,
        "revenue_volatility": 0.35, "existing_debt": 8_000_000,
        "bki": {"max_overdue_days_12m": 15, "overdue_count_12m": 1, "inquiries_3m": 3},
        "demo_request": {"purpose": "working_capital", "product_code": "WORKING_CAPITAL",
                         "requested_amount": 3_000_000, "term_months": 12},
        "expected": "DECLINE",
    },
    {
        "key": "decline_risk",
        "name": "ИП Орлов Денис Андреевич (демо)", "legal_form": "IP", "industry": "transport",
        "reg_date": "2022-08-10", "director": "Орлов Денис Андреевич (демо)",
        "egrul_status": "active", "director_changes_12m": 0, "mass_address": False,
        "tax_arrears": True, "has_reporting": True,
        "annual_revenue": 9_000_000, "net_margin": 0.02, "revenue_trend": -0.20,
        "revenue_volatility": 0.55, "existing_debt": 6_500_000,
        "bki": {"max_overdue_days_12m": 75, "overdue_count_12m": 4, "inquiries_3m": 7},
        "demo_request": {"purpose": "working_capital", "product_code": "EXPRESS",
                         "requested_amount": 1_000_000, "term_months": 24},
        "expected": "DECLINE",
    },
    {
        "key": "alternative_product",
        "name": "ООО «Кофе Старт» (демо)", "legal_form": "OOO", "industry": "food",
        "reg_date": None,  # заполняется: 9 месяцев до даты генерации
        "director": "Ли Вэй (демо)",
        "egrul_status": "active", "director_changes_12m": 0, "mass_address": False,
        "tax_arrears": False, "has_reporting": True,
        "annual_revenue": 14_000_000, "net_margin": 0.12, "revenue_trend": 0.30,
        "revenue_volatility": 0.20, "existing_debt": 0,
        "bki": {"max_overdue_days_12m": 0, "overdue_count_12m": 0, "inquiries_3m": 1},
        "demo_request": {"purpose": "expansion", "product_code": "INVESTMENT",
                         "requested_amount": 4_000_000, "term_months": 36},
        "expected": "ALTERNATIVE_PRODUCT",
    },
]


def _months_between(d1: date, d2: date) -> int:
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) - (1 if d2.day < d1.day else 0)


def build_demo_companies(as_of: date, seed: int = RANDOM_SEED + 1) -> list[dict]:
    """Карточки демо-компаний в формате, который отдают моки."""
    rng = np.random.default_rng(seed)
    out = []
    for c in DEMO_COMPANIES:
        c = json.loads(json.dumps(c))  # глубокая копия
        if c["reg_date"] is None:
            y, m = as_of.year, as_of.month - 9
            while m <= 0:
                m += 12
                y -= 1
            c["reg_date"] = date(y, m, 1).isoformat()
        inn = make_inn(rng, c["legal_form"])
        ogrn_len = 13 if c["legal_form"] == "OOO" else 15
        ogrn = "1" + "".join(str(int(x)) for x in rng.integers(0, 10, ogrn_len - 1))
        debt = c["existing_debt"] or 0
        r = 0.20 / 12
        pay = round(debt * r / (1 - (1 + r) ** -24)) if debt else 0
        out.append({
            "inn": inn,
            "key": c["key"],
            "expected_decision": c["expected"],
            "demo_request": c["demo_request"],
            "egrul": {
                "inn": inn, "ogrn": ogrn, "name": c["name"], "legal_form": c["legal_form"],
                "status": c["egrul_status"], "registration_date": c["reg_date"],
                "okved_main": OKVED[c["industry"]], "industry": c["industry"],
                "director": c["director"], "region": "Москва",
                "business_age_months": _months_between(date.fromisoformat(c["reg_date"]), as_of),
            },
            "fns": {
                "tax_arrears": c["tax_arrears"], "mass_address": c["mass_address"],
                "director_changes_12m": c["director_changes_12m"],
                "has_reporting": c["has_reporting"],
                "reporting": None if not c["has_reporting"] else {
                    "year": as_of.year - 1,
                    "annual_revenue": c["annual_revenue"],
                    "net_margin": c["net_margin"],
                    "revenue_trend": c["revenue_trend"],
                    "revenue_volatility": c["revenue_volatility"],
                },
            },
            "bki": {
                "consent_required": True,
                "existing_debt": debt,
                "existing_monthly_payments": pay,
                "max_overdue_days_12m": c["bki"]["max_overdue_days_12m"],
                "overdue_count_12m": c["bki"]["overdue_count_12m"],
                "inquiries_3m": c["bki"]["inquiries_3m"],
            },
            "statement_profile": c.get("statement_profile"),
        })
    return out


def generate_statement_csv(profile: dict, months: int = 12, as_of: date | None = None,
                           seed: int = RANDOM_SEED + 2) -> str:
    """Синтетическая выписка по расчетному счету (CSV, разделитель ;)."""
    rng = np.random.default_rng(seed)
    as_of = as_of or date.today()
    lines = ["date;amount;direction;counterparty;purpose"]
    base = profile["monthly_inflow"]
    for k in range(months, 0, -1):
        y, m = as_of.year, as_of.month - k
        while m <= 0:
            m += 12
            y -= 1
        level = base * (1 + profile["growth"]) ** (months - k)
        month_in = level * max(0.2, rng.normal(1.0, profile["noise"]))
        n_in = int(rng.integers(18, 40))
        parts = rng.dirichlet(np.ones(n_in)) * month_in
        for p in parts:
            d = date(y, m, int(rng.integers(1, 29)))
            lines.append(f"{d.isoformat()};{p:.2f};in;Клиент {int(rng.integers(1, 300))};"
                         f"Оплата по счету")
        month_out = month_in * profile["expense_ratio"] * rng.normal(1.0, 0.05)
        n_out = int(rng.integers(8, 20))
        for p in rng.dirichlet(np.ones(n_out)) * month_out:
            d = date(y, m, int(rng.integers(1, 29)))
            lines.append(f"{d.isoformat()};{p:.2f};out;Поставщик {int(rng.integers(1, 60))};"
                         f"Оплата поставщику")
    return "\n".join(lines) + "\n"


def main(out_dir: Path = DATA_DIR, n: int = 30_000, as_of: date = date(2026, 10, 1)) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = generate_applications(n)
    df.to_csv(out_dir / "train.csv", index=False)
    companies = build_demo_companies(as_of)
    (out_dir / "companies.json").write_text(
        json.dumps({"as_of": as_of.isoformat(), "companies": companies},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    st_dir = out_dir / "statements"
    st_dir.mkdir(exist_ok=True)
    for c in companies:
        if c.get("statement_profile"):
            (st_dir / f"{c['inn']}.csv").write_text(
                generate_statement_csv(c["statement_profile"], as_of=as_of), encoding="utf-8")
    print(f"train.csv: {len(df)} заявок, доля дефолтов {df['default_12m'].mean():.4f}")
    print(f"companies.json: {len(companies)} демо-компаний")


if __name__ == "__main__":
    main()

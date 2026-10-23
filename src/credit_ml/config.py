"""Конфигурация модели и кредитной политики.

ВАЖНО: все ставки, лимиты и пороги ниже — демонстрационные параметры прототипа.
Это не условия Альфа-Банка и не рыночные данные. В реальном внедрении они
загружаются из справочника продуктов и кредитной политики банка.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = Path(os.getenv("CREDIT_ML_ARTIFACTS", ROOT / "artifacts"))
DATA_DIR = Path(os.getenv("CREDIT_ML_DATA", ROOT / "data"))

MODEL_NAME = "msb-scorecard"
MODEL_VERSION = "1.0.0"
RANDOM_SEED = int(os.getenv("CREDIT_ML_SEED", "42"))


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    description: str
    purposes: tuple[str, ...]
    min_amount: int
    max_amount: int
    min_term: int
    max_term: int
    base_rate: float          # годовая ставка, доли (демо)
    min_business_age: int     # мес.
    collateral: str
    legal_forms: tuple[str, ...] = ("IP", "OOO")


PRODUCTS: tuple[Product, ...] = (
    Product(
        code="OVERDRAFT",
        name="Овердрафт",
        description="Лимит на расчетном счете для кассовых разрывов",
        purposes=("cash_gap",),
        min_amount=100_000, max_amount=10_000_000,
        min_term=1, max_term=12,
        base_rate=0.21, min_business_age=6, collateral="не требуется",
    ),
    Product(
        code="EXPRESS",
        name="Экспресс-кредит без залога",
        description="Небольшая сумма на любые цели бизнеса, решение онлайн",
        purposes=("working_capital", "cash_gap", "contract", "equipment", "expansion", "other"),
        min_amount=300_000, max_amount=5_000_000,
        min_term=6, max_term=36,
        base_rate=0.23, min_business_age=6, collateral="не требуется",
    ),
    Product(
        code="WORKING_CAPITAL",
        name="Оборотный кредит",
        description="Закупка товара, сырья, исполнение контрактов",
        purposes=("working_capital", "contract"),
        min_amount=1_000_000, max_amount=150_000_000,
        min_term=3, max_term=36,
        base_rate=0.19, min_business_age=12, collateral="поручительство собственника",
    ),
    Product(
        code="EQUIPMENT",
        name="Кредит на оборудование и транспорт",
        description="Покупка оборудования, техники, транспорта",
        purposes=("equipment",),
        min_amount=500_000, max_amount=100_000_000,
        min_term=12, max_term=60,
        base_rate=0.18, min_business_age=12, collateral="приобретаемое имущество",
    ),
    Product(
        code="INVESTMENT",
        name="Инвестиционный кредит",
        description="Развитие бизнеса, ремонт, запуск нового направления",
        purposes=("expansion", "real_estate"),
        min_amount=3_000_000, max_amount=300_000_000,
        min_term=12, max_term=84,
        base_rate=0.18, min_business_age=24, collateral="залог имущества",
        legal_forms=("OOO", "IP"),
    ),
)

PRODUCTS_BY_CODE = {p.code: p for p in PRODUCTS}

PURPOSES = {
    "cash_gap": "Покрыть кассовый разрыв",
    "working_capital": "Пополнить оборотные средства",
    "contract": "Исполнить контракт / госзаказ",
    "equipment": "Купить оборудование или транспорт",
    "expansion": "Развитие: новая точка, направление, ремонт",
    "real_estate": "Покупка коммерческой недвижимости",
    "refinance": "Рефинансировать кредиты другого банка",
    "other": "Другое",
}

INDUSTRIES = {
    "trade": "Торговля",
    "food": "Общепит",
    "construction": "Строительство",
    "it": "IT и связь",
    "manufacturing": "Производство",
    "transport": "Транспорт и логистика",
    "services": "Услуги",
    "agro": "Сельское хозяйство",
}

# Грейды риска по PD (вероятность дефолта на 12 мес.) — верхняя граница не включительно
GRADES: tuple[tuple[str, float], ...] = (
    ("A", 0.02),
    ("B", 0.05),
    ("C", 0.10),
    ("D", 0.20),
    ("E", 1.01),
)

# Надбавка к базовой ставке по грейду, п.п. (демо)
RISK_PREMIUM_PP = {"A": 0.0, "B": 1.5, "C": 3.0, "D": 5.0}


@dataclass(frozen=True)
class Policy:
    """Правила кредитной политики (демо-значения, настраиваются через env)."""

    min_business_age_months: int = 3
    stop_overdue_days: int = 90            # просрочка > N дней за 12 мес. — стоп-фактор
    min_dscr: float = 1.25                 # покрытие долга денежным потоком
    max_auto_amount: int = int(os.getenv("POLICY_MAX_AUTO_AMOUNT", "30000000"))
    manual_review_grades: tuple[str, ...] = ("D",)
    decline_grades: tuple[str, ...] = ("E",)
    max_director_changes_12m: int = 2
    max_inquiries_3m: int = 6
    counter_offer_min_share: float = 0.3   # контрпредложение не меньше 30% запроса
    inactive_egrul_statuses: tuple[str, ...] = ("liquidating", "liquidated", "bankruptcy")
    manual_flags: tuple[str, ...] = field(default=("mass_address", "tax_arrears"))


POLICY = Policy()

# Параметры шкалы баллов (стандартная скоринговая шкала: PDO и базовые шансы)
SCORE_BASE = 600
SCORE_BASE_ODDS = 50.0   # шансы good:bad при базовом балле
SCORE_PDO = 40           # баллов на удвоение шансов

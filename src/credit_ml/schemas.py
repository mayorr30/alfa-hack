"""Контракт API (pydantic). Из этих же моделей собирается OpenAPI-спецификация."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .features import inn_is_valid

PurposeCode = Literal["cash_gap", "working_capital", "contract", "equipment", "expansion",
                      "real_estate", "refinance", "other"]
ProductCode = Literal["OVERDRAFT", "EXPRESS", "WORKING_CAPITAL", "EQUIPMENT", "INVESTMENT"]
IndustryCode = Literal["trade", "food", "construction", "it", "manufacturing", "transport",
                       "services", "agro"]
DecisionCode = Literal["APPROVE", "COUNTER_OFFER", "ALTERNATIVE_PRODUCT", "REQUEST_INFO",
                       "MANUAL_REVIEW", "DECLINE"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_inn(v: Optional[str]) -> Optional[str]:
    if v is not None and not inn_is_valid(v):
        raise ValueError("Некорректный ИНН: нужно 10 (ООО) или 12 (ИП) цифр с верными "
                         "контрольными разрядами")
    return v


class CompanyProfile(_Base):
    """Профиль компании, если бэкенд уже собрал данные из ЕГРЮЛ/ФНС/БКИ сам."""
    name: Optional[str] = None
    legal_form: Literal["IP", "OOO"]
    industry: IndustryCode
    egrul_status: Literal["active", "liquidating", "liquidated", "bankruptcy"] = "active"
    business_age_months: int = Field(ge=0, le=1200)
    director_changes_12m: int = Field(0, ge=0, le=50)
    mass_address: bool = False
    tax_arrears: bool = False
    annual_revenue: Optional[float] = Field(None, gt=0)
    net_margin: Optional[float] = Field(None, ge=-1, le=1)
    revenue_trend: Optional[float] = Field(None, ge=-1, le=5)
    revenue_volatility: Optional[float] = Field(None, ge=0, le=5)
    existing_debt: float = Field(0, ge=0)
    existing_monthly_payments: float = Field(0, ge=0)
    bki_max_overdue_days_12m: int = Field(0, ge=0, le=3650)
    bki_overdue_count_12m: int = Field(0, ge=0, le=1000)
    bki_inquiries_3m: int = Field(0, ge=0, le=1000)


class AssessRequest(_Base):
    inn: Optional[str] = Field(None, description="ИНН. Если company не передан — данные "
                                                 "берутся из моков по ИНН",
                               examples=["7756405021"])
    consent_bki: bool = Field(False, description="Согласие клиента на запрос в БКИ")
    purpose: PurposeCode
    product_code: Optional[ProductCode] = Field(None, description="Если не задан — подберем")
    requested_amount: float = Field(gt=0, le=1_000_000_000)
    term_months: int = Field(ge=1, le=120)
    annual_revenue: Optional[float] = Field(None, gt=0, description="Выручка со слов клиента")
    net_margin: Optional[float] = Field(None, ge=-1, le=1,
                                        description="Чистая рентабельность, доля (0.12 = 12%)")
    statement_csv: Optional[str] = Field(None, max_length=5_000_000,
                                         description="Выписка по счету, CSV (см. README)")
    company: Optional[CompanyProfile] = None

    @field_validator("inn")
    @classmethod
    def _v_inn(cls, v):
        return _check_inn(v)


class RecommendRequest(_Base):
    purpose: Optional[PurposeCode] = None
    purpose_text: Optional[str] = Field(None, max_length=2000,
                                        description="Цель своими словами, если purpose не задан",
                                        examples=["Хотим купить новую печь для пекарни"])
    requested_amount: float = Field(gt=0, le=1_000_000_000)
    term_months: int = Field(ge=1, le=120)
    inn: Optional[str] = None
    company: Optional[CompanyProfile] = None

    @field_validator("inn")
    @classmethod
    def _v_inn(cls, v):
        return _check_inn(v)


class ClassifyRequest(_Base):
    text: str = Field(min_length=1, max_length=2000)


class StatementRequest(_Base):
    statement_csv: str = Field(min_length=1, max_length=5_000_000)


# ---------------- ответы ----------------

class Risk(BaseModel):
    score: int = Field(description="Балл балльной карты (больше — надежнее)")
    pd: float = Field(description="Вероятность дефолта за 12 мес.")
    grade: Literal["A", "B", "C", "D", "E"]


class Offer(BaseModel):
    kind: Literal["AS_REQUESTED", "LONGER_TERM", "LOWER_AMOUNT", "LOWER_AMOUNT_LONGER_TERM",
                  "ALTERNATIVE"]
    comment: str
    product_code: ProductCode
    product_name: str
    amount: float
    term_months: int
    rate: Optional[float] = Field(description="Годовая ставка, доля (демо)")
    monthly_payment: Optional[float]
    dscr: Optional[float]
    collateral: str
    grade: str
    pd: float


class Reason(BaseModel):
    code: str
    source: Literal["policy", "model"]
    impact: Literal["negative", "positive", "neutral"]
    text: str
    points: Optional[int] = Field(None, description="Вклад в балл относительно среднего")
    advice: Optional[str] = None


class Check(BaseModel):
    code: str
    name: str
    status: Literal["passed", "failed", "warning"]
    details: str = ""


class MissingField(BaseModel):
    field: str
    label: str
    why: str
    alternative: Optional[str] = None


class RequestEcho(BaseModel):
    purpose: str
    purpose_label: Optional[str]
    product_code: Optional[str]
    requested_amount: float
    term_months: int


class AssessResponse(BaseModel):
    request_id: str
    decision: DecisionCode
    decision_label: str
    next_status: Literal["OFFER_READY", "COUNTER_OFFER", "ALTERNATIVE_OFFER", "INFO_REQUESTED",
                         "MANUAL_REVIEW", "DECLINED"]
    client_message: str
    requires_manual_review: bool
    manual_review_flags: list[str]
    offer: Optional[Offer]
    offers: list[Offer]
    risk: Optional[Risk]
    reasons: list[Reason]
    advice: list[str]
    missing_data: list[MissingField]
    checks: list[Check]
    notes: list[str]
    request: RequestEcho
    features: Optional[dict]
    financials_source: Optional[str]
    model_version: str
    policy_version: str
    decided_at: str
    disclaimer: str


class ProductRecommendation(BaseModel):
    product_code: ProductCode
    product_name: str
    description: str
    eligible: bool
    fit_score: int
    suggested_amount: float
    suggested_term_months: int
    base_rate: float
    estimated_monthly_payment: float
    estimated_dscr: Optional[float]
    collateral: str
    why: list[str]
    why_not: list[str]
    warnings: list[str]


class PurposeClassification(BaseModel):
    purpose: PurposeCode
    label: str
    confidence: float
    matched: list[str]
    alternatives: list[str]


class Prescore(BaseModel):
    grade: str
    pd: float
    approval_chance: Literal["высокий", "средний", "низкий", "очень низкий"]
    product_code: str


class RecommendResponse(BaseModel):
    purpose: PurposeClassification
    recommendations: list[ProductRecommendation]
    missing_data: list[MissingField]
    prescore: Optional[Prescore]
    note: Optional[str]


class StatementResponse(BaseModel):
    months: int
    period_from: str
    period_to: str
    monthly: list[dict]
    annual_revenue: float
    net_margin: float
    revenue_trend: float
    revenue_volatility: float
    top_counterparty_share: float
    warnings: list[str]


class ErrorResponse(BaseModel):
    error: str
    detail: object = None

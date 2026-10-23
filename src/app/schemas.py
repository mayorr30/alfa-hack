"""Формат запросов и ответов API бэкенда (из этих классов строится Swagger)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

PurposeCode = Literal["cash_gap", "working_capital", "contract", "equipment", "expansion",
                      "real_estate", "refinance", "other"]
ProductCode = Literal["OVERDRAFT", "EXPRESS", "WORKING_CAPITAL", "EQUIPMENT", "INVESTMENT"]


def _digits_inn(v: str) -> str:
    v = v.strip()
    if not v.isdigit() or len(v) not in (10, 12):
        raise ValueError("ИНН — 10 цифр для ООО или 12 цифр для ИП")
    return v


# ---------------- запросы ----------------

class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str = Field(examples=["7756405021"])
    purpose: PurposeCode = Field(examples=["equipment"])
    purpose_text: Optional[str] = Field(None, max_length=2000,
                                        description="Цель своими словами (как ввёл клиент)")
    product_code: Optional[ProductCode] = Field(None, description="Не задан — ML подберёт сам")
    requested_amount: float = Field(gt=0, le=1_000_000_000, examples=[5_000_000])
    term_months: int = Field(ge=1, le=120, examples=[36])
    consent_bki: bool = Field(False, description="Согласие на запрос кредитной истории")
    annual_revenue: Optional[float] = Field(None, gt=0, description="Выручка за год со слов клиента")
    net_margin: Optional[float] = Field(None, ge=-1, le=1, description="Рентабельность, доля")

    @field_validator("inn")
    @classmethod
    def _check_inn(cls, v: str) -> str:
        return _digits_inn(v)


class ProvideInfo(BaseModel):
    """Дозагрузка данных по заявке в статусе «Нужны данные»."""
    model_config = ConfigDict(extra="forbid")

    consent_bki: Optional[bool] = None
    statement_csv: Optional[str] = Field(None, max_length=5_000_000)
    statement_filename: Optional[str] = Field(None, max_length=255)
    annual_revenue: Optional[float] = Field(None, gt=0)
    net_margin: Optional[float] = Field(None, ge=-1, le=1)


class AcceptOffer(BaseModel):
    offer_index: int = Field(0, ge=0, description="Номер варианта из списка offers")


class AnalystDecision(BaseModel):
    approve: bool
    comment: str = Field(min_length=3, max_length=2000)


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: Optional[str] = None
    purpose: Optional[PurposeCode] = None
    purpose_text: Optional[str] = Field(None, max_length=2000)
    requested_amount: float = Field(gt=0, le=1_000_000_000)
    term_months: int = Field(ge=1, le=120)


# ---------------- ответы ----------------

class EventOut(BaseModel):
    from_status: Optional[str]
    to_status: str
    to_status_label: str
    actor: str
    comment: Optional[str]
    created_at: datetime


class AssessmentOut(BaseModel):
    """Результат ML по заявке — ровно то, что нужно экрану."""
    decision: str
    decision_label: str
    client_message: str
    risk: Optional[dict]
    offer: Optional[dict]
    offers: list[dict]
    reasons: list[dict]
    advice: list[str]
    missing_data: list[dict]
    checks: list[dict]
    notes: list[str]
    requires_manual_review: bool
    manual_review_flags: list[str]
    model_version: str
    policy_version: str
    assessed_at: datetime


class DocumentOut(BaseModel):
    id: int
    kind: str
    filename: Optional[str]
    created_at: datetime


class ApplicationOut(BaseModel):
    id: int
    inn: str
    company_name: str
    purpose: str
    purpose_label: Optional[str]
    purpose_text: Optional[str]
    product_code: Optional[str]
    requested_amount: float
    term_months: int
    consent_bki: bool
    status: str
    status_label: str
    status_hint: str
    actions: list[str] = Field(description="Что клиент может сделать сейчас")
    accepted_offer: Optional[dict]
    assessment: Optional[AssessmentOut] = Field(description="Последнее решение ML")
    assessments_count: int
    documents: list[DocumentOut]
    events: list[EventOut]
    created_at: datetime
    updated_at: datetime


class ApplicationShort(BaseModel):
    id: int
    inn: str
    company_name: str
    product_code: Optional[str]
    requested_amount: float
    term_months: int
    status: str
    status_label: str
    grade: Optional[str]
    score: Optional[int]
    requires_manual_review: bool
    created_at: datetime
    updated_at: datetime


class ErrorOut(BaseModel):
    error: str
    message: str
    detail: Any = None

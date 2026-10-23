"""Таблицы БД.

companies          — компании-заявители (данные из ЕГРЮЛ-мока)
applications       — кредитные заявки и их текущий статус
assessments        — каждый прогон заявки через ML (их может быть несколько:
                     например, после загрузки выписки заявка пересчитывается)
application_events — история смены статусов (кто, когда, из какого в какой)
documents          — документы, загруженные клиентом (выписка по счёту)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String,
                        Text, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    inn: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    legal_form: Mapped[str] = mapped_column(String(8))
    industry: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    applications: Mapped[list[Application]] = relationship(back_populates="company")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    purpose_text: Mapped[str | None] = mapped_column(Text)
    product_code: Mapped[str | None] = mapped_column(String(32))
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    term_months: Mapped[int] = mapped_column(Integer)
    consent_bki: Mapped[bool] = mapped_column(Boolean, default=False)
    # выручка и рентабельность со слов клиента (если он их указал)
    client_financials: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    accepted_offer: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    company: Mapped[Company] = relationship(back_populates="applications")
    assessments: Mapped[list[Assessment]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="Assessment.id")
    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="ApplicationEvent.id")
    documents: Mapped[list[Document]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="Document.id")


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"),
                                                index=True)
    decision: Mapped[str] = mapped_column(String(32))
    next_status: Mapped[str] = mapped_column(String(32))
    score: Mapped[int | None] = mapped_column(Integer)
    pd: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(1))
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    model_version: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(64))
    # полный ответ ML: предложения, причины, проверки — для экрана клиента и аналитика
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    application: Mapped[Application] = relationship(back_populates="assessments")


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"),
                                                index=True)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(16))  # client / system / analyst
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    application: Mapped[Application] = relationship(back_populates="events")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"),
                                                index=True)
    kind: Mapped[str] = mapped_column(String(32))  # bank_statement
    filename: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    application: Mapped[Application] = relationship(back_populates="documents")

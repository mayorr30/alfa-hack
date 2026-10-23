from sqlalchemy import ForeignKey, String, Float, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    inn: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    revenue: Mapped[float] = mapped_column(Float, nullable=True)

    applications = relationship("Application", back_populates="company", cascade="all, delete-orphan")


class Application(Base):
    __tablename__ = "credit_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="processing")

    company = relationship("Company", back_populates="applications")
    scoring_result = relationship("ScoringResult", back_populates="application", uselist=False,
                                  cascade="all, delete-orphan")


class ScoringResult(Base):
    __tablename__ = "scoring_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("credit_applications.id", ondelete="CASCADE"), unique=True,
                                                nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    reasons: Mapped[dict] = mapped_column(JSON, nullable=True)

    application = relationship("Application", back_populates="scoring_result")

"""Единственная точка связи бэкенда с ML-модулем credit_ml.

Бэкенд не лезет внутрь модели: он отдаёт заявку в credit_ml.service и получает
готовое решение. Если ML поменяется внутри, достаточно, чтобы не изменился этот файл.

Модель сохранена под именем пакета `credit_ml` (не `src.credit_ml`), поэтому папка src
должна быть в sys.path. Ниже это гарантируется даже при запуске из IDE.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from credit_ml import service as ml_service  # noqa: E402
from credit_ml.config import INDUSTRIES, PRODUCTS, PURPOSES  # noqa: E402
from credit_ml.mocks import CompanyNotFound, get_store  # noqa: E402
from credit_ml.model_store import ModelNotTrained, get_model  # noqa: E402
from credit_ml.statement import StatementError  # noqa: E402


class MLError(Exception):
    """Ошибка, которую можно показать клиенту: код HTTP + понятный текст."""

    status_code = 400
    error = "ml_error"

    def __init__(self, message: str, detail: Any = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class MLValidationError(MLError):
    status_code = 422
    error = "validation_error"


class MLNotFound(MLError):
    status_code = 404
    error = "not_found"


class MLUnavailable(MLError):
    status_code = 503
    error = "ml_unavailable"


def _call(fn, payload: dict) -> dict:
    """Вызов ML с переводом его исключений в понятные бэкенду ошибки."""
    try:
        return fn(payload)
    except ValidationError as e:
        raise MLValidationError("Данные заявки не прошли проверку",
                                json.loads(e.json(include_url=False, include_context=False))) from e
    except CompanyNotFound as e:
        raise MLNotFound(f"Компания с ИНН {e.args[0]} не найдена в реестре (мок ЕГРЮЛ)") from e
    except StatementError as e:
        raise MLValidationError(f"Выписку не удалось прочитать: {e}") from e
    except ModelNotTrained as e:
        raise MLUnavailable("ML-модель не загружена") from e
    except ValueError as e:
        raise MLValidationError(str(e)) from e


# ---------------- вызовы ML (тяжёлые — выполняются в пуле потоков) ----------------

async def assess(payload: dict) -> dict:
    """Кредитное решение по заявке. Ответ — схема AssessResponse из credit_ml."""
    return await run_in_threadpool(_call, ml_service.run_assess, payload)


async def recommend(payload: dict) -> dict:
    return await run_in_threadpool(_call, ml_service.run_recommend, payload)


async def analyze_statement(csv_text: str) -> dict:
    return await run_in_threadpool(_call, ml_service.run_statement, {"statement_csv": csv_text})


# ---------------- справочники и моки (лёгкие) ----------------

def company_card(inn: str) -> dict:
    """Карточка компании из мока ЕГРЮЛ + флаги ФНС."""
    store = get_store()
    try:
        eg = store.egrul(inn)
        fns = store.fns(inn)
    except CompanyNotFound as e:
        raise MLNotFound(f"Компания с ИНН {inn} не найдена в реестре (мок ЕГРЮЛ)") from e
    return {
        "inn": inn, "name": eg["name"], "legal_form": eg["legal_form"],
        "industry": eg["industry"], "industry_label": INDUSTRIES.get(eg["industry"]),
        "status": eg["status"], "registration_date": eg["registration_date"],
        "business_age_months": eg["business_age_months"], "okved_main": eg["okved_main"],
        "director": eg["director"], "region": eg["region"],
        "has_reporting": fns["has_reporting"],
    }


def demo_companies() -> list[dict]:
    return [{
        "inn": c["inn"], "name": c["egrul"]["name"], "scenario": c["key"],
        "expected_decision": c["expected_decision"], "demo_request": c["demo_request"],
        "has_statement": c.get("statement_profile") is not None,
    } for c in get_store().all()]


def demo_statement(inn: str) -> str | None:
    return get_store().statement_csv(inn)


def products() -> list[dict]:
    return [{
        "code": p.code, "name": p.name, "description": p.description,
        "purposes": list(p.purposes), "min_amount": p.min_amount, "max_amount": p.max_amount,
        "min_term": p.min_term, "max_term": p.max_term, "base_rate": p.base_rate,
        "min_business_age_months": p.min_business_age, "collateral": p.collateral,
    } for p in PRODUCTS]


def purposes() -> list[dict]:
    return [{"code": k, "label": v} for k, v in PURPOSES.items()]


def model_version() -> str:
    try:
        return get_model().version
    except ModelNotTrained as e:
        raise MLUnavailable("ML-модель не загружена") from e

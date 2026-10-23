"""Сервисный слой: склейка запросов API с моками, моделью и логикой решения."""

from __future__ import annotations

import json

from .config import ROOT
from .decision import assess
from .mocks import get_store
from .model_store import get_model
from .recommend import classify_purpose, missing_for_decision, recommend
from .schemas import (AssessRequest, AssessResponse, RecommendRequest, RecommendResponse,
                      StatementRequest, StatementResponse)
from .statement import analyze_statement

CHANCE = {"A": "высокий", "B": "высокий", "C": "средний", "D": "низкий", "E": "очень низкий"}


def _profile(inn: str | None, company) -> dict | None:
    if company is not None:
        p = company.model_dump()
        p["inn"] = inn
        return p
    if inn:
        return get_store().profile(inn)  # CompanyNotFound → 404
    return None


def run_assess(payload: dict) -> dict:
    req = AssessRequest.model_validate(payload)
    if req.inn is None and req.company is None:
        raise ValueError("Нужен inn или company")
    profile = _profile(req.inn, req.company)
    result = assess(req.model_dump(exclude={"company"}), profile, get_model())
    return AssessResponse.model_validate(result).model_dump()


def run_recommend(payload: dict) -> dict:
    req = RecommendRequest.model_validate(payload)
    if req.purpose:
        from .config import PURPOSES
        cls = {"purpose": req.purpose, "label": PURPOSES[req.purpose], "confidence": 1.0,
               "matched": [], "alternatives": []}
    elif req.purpose_text:
        cls = classify_purpose(req.purpose_text)
    else:
        raise ValueError("Нужен purpose или purpose_text")
    profile = _profile(req.inn, req.company)
    recs = recommend(cls["purpose"], req.requested_amount, req.term_months, profile)

    prescore = None
    note = None
    eligible = [r for r in recs if r["eligible"]]
    if not eligible:
        note = "В линейке нет продукта под эту цель и профиль. Уточните задачу или параметры."
    missing = missing_for_decision(profile) if profile else []
    if profile and eligible and not missing and profile.get("egrul_status") == "active":
        top = eligible[0]
        raw = {**profile, "product_code": top["product_code"], "purpose": cls["purpose"],
               "requested_amount": top["suggested_amount"],
               "term_months": top["suggested_term_months"],
               "revenue_trend": profile.get("revenue_trend") or 0.0,
               "revenue_volatility": profile.get("revenue_volatility")
               if profile.get("revenue_volatility") is not None else 0.3}
        r = get_model().evaluate(raw)
        prescore = {"grade": r["grade"], "pd": r["pd"], "approval_chance": CHANCE[r["grade"]],
                    "product_code": top["product_code"]}
    out = {"purpose": cls, "recommendations": recs, "missing_data": missing,
           "prescore": prescore, "note": note}
    return RecommendResponse.model_validate(out).model_dump()


def run_statement(payload: dict) -> dict:
    req = StatementRequest.model_validate(payload)
    return StatementResponse.model_validate(analyze_statement(req.statement_csv).to_dict()).model_dump()


def model_info() -> dict:
    m = get_model()
    metrics_path = ROOT / "reports" / "metrics.json"
    summary = None
    if metrics_path.exists():
        mt = json.loads(metrics_path.read_text(encoding="utf-8"))["metrics"]
        summary = {k: mt[k] for k in ("test", "oot", "psi_score_train_vs_oot", "challenger_gbm")}
    return {"version": m.version, "meta": m.meta, "validation": summary}

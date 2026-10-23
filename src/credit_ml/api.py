"""HTTP API ML-сервиса (Starlette + pydantic). Swagger UI: /docs, спецификация: /openapi.json.

Запуск: uvicorn credit_ml.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import time

from pydantic import ValidationError
from pydantic.json_schema import models_json_schema
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from . import __version__
from .config import INDUSTRIES, PRODUCTS, PURPOSES
from .mocks import CompanyNotFound, get_store
from .model_store import ModelNotTrained
from .recommend import classify_purpose
from .schemas import (AssessRequest, AssessResponse, ClassifyRequest, ErrorResponse,
                      PurposeClassification, RecommendRequest, RecommendResponse,
                      StatementRequest, StatementResponse)
from .service import model_info, run_assess, run_recommend, run_statement
from .statement import StatementError

log = logging.getLogger("credit_ml")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


class JSONUTF8(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=str).encode("utf-8")


def _err(status: int, error: str, detail=None) -> JSONUTF8:
    return JSONUTF8({"error": error, "detail": detail}, status_code=status)


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Тело запроса должно быть JSON: {e}") from e
    if not isinstance(body, dict):
        raise ValueError("Тело запроса должно быть JSON-объектом")
    return body


def handler(fn):
    async def wrapped(request: Request):
        t0 = time.perf_counter()
        try:
            res = await fn(request)
        except ValidationError as e:
            return _err(422, "validation_error",
                        json.loads(e.json(include_url=False, include_context=False)))
        except CompanyNotFound as e:
            return _err(404, "company_not_found", f"Компания с ИНН {e.args[0]} не найдена "
                                                  f"в моках. Список: GET /demo/companies")
        except StatementError as e:
            return _err(422, "statement_error", str(e))
        except ValueError as e:
            return _err(400, "bad_request", str(e))
        except ModelNotTrained as e:
            return _err(503, "model_not_trained", str(e))
        finally:
            log.info("%s %s %.1fms", request.method, request.url.path,
                     (time.perf_counter() - t0) * 1000)
        return res
    return wrapped


# ---------------- handlers ----------------

@handler
async def health(request: Request):
    from .model_store import get_model
    try:
        v = get_model().version
        return JSONUTF8({"status": "ok", "model": v, "service_version": __version__})
    except ModelNotTrained:
        return JSONUTF8({"status": "degraded", "model": None}, status_code=503)


@handler
async def products(request: Request):
    return JSONUTF8([{
        "code": p.code, "name": p.name, "description": p.description,
        "purposes": list(p.purposes), "min_amount": p.min_amount, "max_amount": p.max_amount,
        "min_term": p.min_term, "max_term": p.max_term, "base_rate": p.base_rate,
        "min_business_age_months": p.min_business_age, "collateral": p.collateral,
        "legal_forms": list(p.legal_forms)} for p in PRODUCTS])


@handler
async def purposes(request: Request):
    return JSONUTF8([{"code": k, "label": v} for k, v in PURPOSES.items()])


@handler
async def industries(request: Request):
    return JSONUTF8([{"code": k, "label": v} for k, v in INDUSTRIES.items()])


@handler
async def classify(request: Request):
    req = ClassifyRequest.model_validate(await _json(request))
    return JSONUTF8(classify_purpose(req.text))


@handler
async def recommend_ep(request: Request):
    return JSONUTF8(run_recommend(await _json(request)))


@handler
async def assess_ep(request: Request):
    res = run_assess(await _json(request))
    log.info("assess request_id=%s decision=%s grade=%s", res["request_id"], res["decision"],
             (res["risk"] or {}).get("grade"))
    return JSONUTF8(res)


@handler
async def statement_ep(request: Request):
    return JSONUTF8(run_statement(await _json(request)))


@handler
async def model_ep(request: Request):
    return JSONUTF8(model_info())


# ---------------- моки ----------------

@handler
async def mock_egrul(request: Request):
    return JSONUTF8(get_store().egrul(request.path_params["inn"]))


@handler
async def mock_fns(request: Request):
    return JSONUTF8(get_store().fns(request.path_params["inn"]))


@handler
async def mock_bki(request: Request):
    if request.query_params.get("consent") not in ("true", "1"):
        return _err(403, "consent_required", "Нужно согласие клиента: ?consent=true")
    return JSONUTF8(get_store().bki(request.path_params["inn"]))


@handler
async def mock_statement(request: Request):
    inn = request.path_params["inn"]
    get_store().egrul(inn)
    csv_text = get_store().statement_csv(inn)
    if csv_text is None:
        return _err(404, "statement_not_found", "Для этой компании нет демо-выписки")
    return PlainTextResponse(csv_text, media_type="text/csv; charset=utf-8")


@handler
async def demo_companies(request: Request):
    s = get_store()
    return JSONUTF8({"as_of": s.as_of, "companies": [{
        "inn": c["inn"], "name": c["egrul"]["name"], "scenario": c["key"],
        "expected_decision": c["expected_decision"], "demo_request": c["demo_request"],
        "has_statement": c.get("statement_profile") is not None} for c in s.all()]})


# ---------------- OpenAPI ----------------

def build_openapi() -> dict:
    models = [AssessRequest, AssessResponse, RecommendRequest, RecommendResponse,
              ClassifyRequest, PurposeClassification, StatementRequest, StatementResponse,
              ErrorResponse]
    _, top = models_json_schema([(m, "validation" if m.__name__.endswith("Request")
                                  else "serialization") for m in models],
                                ref_template="#/components/schemas/{model}")
    ref = lambda m: {"$ref": f"#/components/schemas/{m.__name__}"}  # noqa: E731
    err = {"description": "Ошибка", "content": {"application/json": {"schema": ref(ErrorResponse)}}}

    def post(summary, req, resp, tag, desc=""):
        return {"post": {"tags": [tag], "summary": summary, "description": desc,
                         "requestBody": {"required": True,
                                         "content": {"application/json": {"schema": ref(req)}}},
                         "responses": {"200": {"description": "OK", "content": {
                             "application/json": {"schema": ref(resp)}}},
                             "400": err, "404": err, "422": err, "503": err}}}

    def get(summary, tag, params=None, desc="", content="application/json"):
        return {"get": {"tags": [tag], "summary": summary, "description": desc,
                        "parameters": params or [],
                        "responses": {"200": {"description": "OK",
                                              "content": {content: {"schema": {}}}},
                                      "404": err}}}

    inn_p = [{"name": "inn", "in": "path", "required": True, "schema": {"type": "string"}}]
    return {
        "openapi": "3.1.0",
        "info": {"title": "Credit ML — скоринг, кредитное решение и подбор продукта (МСБ)",
                 "version": __version__,
                 "description": "ML-модуль кредитного конвейера. Демо на синтетических данных. "
                                "Внешние источники (ЕГРЮЛ, ФНС, БКИ) — локальные моки."},
        "paths": {
            "/health": get("Проверка работоспособности", "service"),
            "/v1/assess": post("Оценить заявку и принять кредитное решение", AssessRequest,
                               AssessResponse, "decision",
                               "Стоп-факторы → полнота данных → продукт → скоринг → "
                               "посильность (DSCR) → решение с причинами."),
            "/v1/recommend": post("Подобрать продукты под цель, сумму и срок", RecommendRequest,
                                  RecommendResponse, "recommendation"),
            "/v1/purpose/classify": post("Определить цель кредита по тексту", ClassifyRequest,
                                         PurposeClassification, "recommendation"),
            "/v1/statement/analyze": post("Разобрать выписку по счету", StatementRequest,
                                          StatementResponse, "documents"),
            "/v1/products": get("Справочник продуктов (демо)", "reference"),
            "/v1/purposes": get("Справочник целей кредита", "reference"),
            "/v1/industries": get("Справочник отраслей", "reference"),
            "/v1/model": get("Версия модели и метрики валидации", "service"),
            "/demo/companies": get("Демо-компании для сценариев", "mocks"),
            "/mock/egrul/{inn}": get("Мок ЕГРЮЛ/ЕГРИП", "mocks", inn_p),
            "/mock/fns/{inn}": get("Мок ФНС «Прозрачный бизнес»", "mocks", inn_p),
            "/mock/bki/{inn}": get("Мок БКИ (нужен ?consent=true)", "mocks", inn_p + [
                {"name": "consent", "in": "query", "required": True,
                 "schema": {"type": "boolean"}}]),
            "/mock/statement/{inn}": get("Демо-выписка по счету (CSV)", "mocks", inn_p,
                                         content="text/csv"),
        },
        "components": {"schemas": top["$defs"]},
    }


OPENAPI = build_openapi()

SWAGGER_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Credit ML API</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">
</head><body><div id="ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url: "/openapi.json", dom_id: "#ui"});</script>
</body></html>"""


async def openapi_ep(request: Request):
    return JSONUTF8(OPENAPI)


async def docs_ep(request: Request):
    return HTMLResponse(SWAGGER_HTML)


routes = [
    Route("/health", health),
    Route("/openapi.json", openapi_ep),
    Route("/docs", docs_ep),
    Route("/v1/assess", assess_ep, methods=["POST"]),
    Route("/v1/recommend", recommend_ep, methods=["POST"]),
    Route("/v1/purpose/classify", classify, methods=["POST"]),
    Route("/v1/statement/analyze", statement_ep, methods=["POST"]),
    Route("/v1/products", products),
    Route("/v1/purposes", purposes),
    Route("/v1/industries", industries),
    Route("/v1/model", model_ep),
    Route("/demo/companies", demo_companies),
    Route("/mock/egrul/{inn}", mock_egrul),
    Route("/mock/fns/{inn}", mock_fns),
    Route("/mock/bki/{inn}", mock_bki),
    Route("/mock/statement/{inn}", mock_statement),
]

origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app = Starlette(routes=routes, middleware=[
    Middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
])

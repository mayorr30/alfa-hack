"""Точка входа FastAPI. Запуск: uvicorn app.main:app --app-dir src --port 8000"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from . import __version__, ml_client
from .database import Base, SessionLocal, engine
from .routes import bank, client, reference
from .services import NotFound, WorkflowError
from .settings import settings

log = logging.getLogger("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Модель загружаем сразу: если её нет, узнаем при старте, а не на первой заявке
    log.info("ML-модель: %s", ml_client.model_version())
    if settings.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="Умный кредитный кабинет предпринимателя",
    description="Бэкенд кредитного конвейера МСБ. Решение по заявке принимает модуль credit_ml. "
                "Все данные синтетические, внешние источники — моки.",
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","),
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

API = "/api/v1"
app.include_router(reference.router, prefix=API)
app.include_router(client.router, prefix=API)
app.include_router(bank.router, prefix=API)


# ---------------- ошибки → понятный JSON ----------------

def _error(status: int, error: str, message: str, detail=None) -> JSONResponse:
    return JSONResponse({"error": error, "message": message, "detail": detail}, status_code=status)


@app.exception_handler(ml_client.MLError)
async def ml_error(_: Request, exc: ml_client.MLError):
    return _error(exc.status_code, exc.error, exc.message, exc.detail)


@app.exception_handler(WorkflowError)
async def workflow_error(_: Request, exc: WorkflowError):
    return _error(409, "invalid_action", str(exc))


@app.exception_handler(NotFound)
async def not_found(_: Request, exc: NotFound):
    return _error(404, "not_found", str(exc))


@app.exception_handler(DBAPIError)
async def db_error(_: Request, exc: DBAPIError):
    log.exception("Ошибка БД")
    text_ = str(exc.orig) if exc.orig is not None else str(exc)
    if "does not exist" in text_ or "UndefinedTable" in type(exc.orig).__name__ \
            or "UndefinedColumn" in type(exc.orig).__name__:
        msg = ("Схема базы данных устарела или таблиц нет. Выполните: alembic upgrade head. "
               "Если в базе остались таблицы старой версии — удалите её: docker compose down -v")
    else:
        msg = "Ошибка базы данных"
    return _error(500, "database_error", msg, f"{type(exc.orig).__name__}: {text_}"[:500])


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception):
    log.exception("Необработанная ошибка")
    return _error(500, "internal_error", "Внутренняя ошибка сервера",
                  f"{type(exc).__name__}: {exc}"[:500])


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    detail = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
    return _error(422, "validation_error", "Проверьте поля запроса", detail)


# ---------------- страницы и служебное ----------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def cabinet(request: Request):
    return templates.TemplateResponse(request, "cabinet.html")


@app.get("/bank", response_class=HTMLResponse, include_in_schema=False)
async def bank_page(request: Request):
    return templates.TemplateResponse(request, "bank.html")


@app.get("/health", tags=["Служебное"], summary="Проверка БД и ML-модели")
async def health():
    db_ok, schema_ok = True, True
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
            try:
                await s.execute(text("SELECT id, legal_form FROM companies LIMIT 1"))
                await s.execute(text("SELECT id FROM applications LIMIT 1"))
            except Exception:  # noqa: BLE001
                schema_ok = False
    except Exception as e:  # noqa: BLE001
        log.warning("БД недоступна: %s", e)
        db_ok = schema_ok = False
    ok = db_ok and schema_ok
    body = {"status": "ok" if ok else "degraded", "database": db_ok, "schema": schema_ok,
            "ml_model": ml_client.model_version(), "service_version": __version__}
    if db_ok and not schema_ok:
        body["hint"] = "Таблиц нет или они от старой версии: alembic upgrade head (или docker compose down -v)"
    return JSONResponse(body, status_code=200 if ok else 503)

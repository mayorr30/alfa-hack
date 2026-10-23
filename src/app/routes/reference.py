"""Справочники, демо-компании и подбор продукта — всё, что нужно форме до подачи заявки."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .. import ml_client
from ..schemas import RecommendRequest

router = APIRouter(tags=["Справочники и подбор"])


@router.get("/reference/products", summary="Кредитные продукты (демо)")
async def products():
    return ml_client.products()


@router.get("/reference/purposes", summary="Цели кредита")
async def purposes():
    return ml_client.purposes()


@router.get("/demo/companies", summary="Демо-компании для сценариев")
async def demo_companies():
    return ml_client.demo_companies()


@router.get("/companies/{inn}", summary="Карточка компании из реестра (мок ЕГРЮЛ)")
async def company(inn: str):
    return ml_client.company_card(inn)


@router.get("/companies/{inn}/demo-statement", response_class=PlainTextResponse,
            summary="Демо-выписка по счёту (CSV) для сценария «нужны данные»")
async def demo_statement(inn: str):
    ml_client.company_card(inn)
    text = ml_client.demo_statement(inn)
    if text is None:
        raise ml_client.MLNotFound("Для этой компании нет демо-выписки")
    return PlainTextResponse(text, media_type="text/csv; charset=utf-8")


@router.post("/recommendations", summary="Подобрать продукты под цель, сумму и срок")
async def recommendations(body: RecommendRequest):
    return await ml_client.recommend(body.model_dump(exclude_none=True))

"""API кабинета клиента: подать заявку, следить за статусом, принять условия."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services
from ..database import get_db
from ..schemas import (AcceptOffer, ApplicationCreate, ApplicationOut, ApplicationShort,
                       ErrorOut, ProvideInfo)

router = APIRouter(prefix="/applications", tags=["Заявки клиента"])

ERRORS = {404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut},
          503: {"model": ErrorOut}}


@router.post("", response_model=ApplicationOut, status_code=201, responses=ERRORS,
             summary="Подать заявку: сохраняется и сразу оценивается ML")
async def create(body: ApplicationCreate, db: AsyncSession = Depends(get_db)):
    return await services.create_application(db, body)


@router.get("", response_model=list[ApplicationShort], summary="Заявки компании")
async def list_by_inn(inn: str = Query(..., min_length=10, max_length=12),
                      db: AsyncSession = Depends(get_db)):
    return await services.list_applications(db, inn=inn)


@router.get("/{app_id}", response_model=ApplicationOut, responses=ERRORS,
            summary="Заявка целиком: статус, решение, история")
async def get(app_id: int, db: AsyncSession = Depends(get_db)):
    return await services.get_application(db, app_id)


@router.post("/{app_id}/info", response_model=ApplicationOut, responses=ERRORS,
             summary="Дозагрузить данные (выписка, финансы, согласие) и пересчитать")
async def provide_info(app_id: int, body: ProvideInfo, db: AsyncSession = Depends(get_db)):
    return await services.provide_info(db, app_id, body)


@router.post("/{app_id}/accept", response_model=ApplicationOut, responses=ERRORS,
             summary="Принять один из предложенных вариантов")
async def accept(app_id: int, body: AcceptOffer, db: AsyncSession = Depends(get_db)):
    return await services.accept_offer(db, app_id, body)


@router.post("/{app_id}/sign", response_model=ApplicationOut, responses=ERRORS,
             summary="Подписать договор (симуляция выдачи)")
async def sign(app_id: int, db: AsyncSession = Depends(get_db)):
    return await services.sign(db, app_id)


@router.post("/{app_id}/cancel", response_model=ApplicationOut, responses=ERRORS,
             summary="Отозвать заявку")
async def cancel(app_id: int, db: AsyncSession = Depends(get_db)):
    return await services.cancel(db, app_id)

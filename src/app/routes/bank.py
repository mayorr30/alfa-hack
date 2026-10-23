"""API сотрудника банка: очередь заявок и решение по ручной проверке."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services
from ..database import get_db
from ..schemas import AnalystDecision, ApplicationOut, ApplicationShort
from .client import ERRORS

router = APIRouter(prefix="/bank", tags=["Кабинет аналитика"])


@router.get("/applications", response_model=list[ApplicationShort],
            summary="Все заявки (фильтр по статусу, например MANUAL_REVIEW)")
async def queue(status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    return await services.list_applications(db, status=status)


@router.post("/applications/{app_id}/decision", response_model=ApplicationOut, responses=ERRORS,
             summary="Решение аналитика по заявке на ручной проверке")
async def decide(app_id: int, body: AnalystDecision, db: AsyncSession = Depends(get_db)):
    return await services.analyst_decide(db, app_id, body)

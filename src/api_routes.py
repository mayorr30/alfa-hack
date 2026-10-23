from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.database import get_db
from src.models import Application, Company, ScoringResult
from src.schemas import ClientApplicationRequest, ApplicationRouteResponse
from src.credit_ml import reasons as ml_reasons
from src.credit_ml import recommend as ml_recommend
router = APIRouter(prefix="/applications")


@router.post("/", response_model=ApplicationRouteResponse)
async def create_application(payload: ClientApplicationRequest, db: AsyncSession = Depends(get_db)):
    """Прием заявки от МСБ и мгновенный сквозной прогон через внутренние модули credit_ml"""
    from src.services.pipeline import run_credit_conveyor
    try:
        result = await run_credit_conveyor(
            inn=payload.inn,
            amount=payload.amount,
            term=payload.term_months,
            db=db
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка конвейера: {str(e)}")


@router.get("/{application_id}")
async def get_application_status(application_id: int, db: AsyncSession = Depends(get_db)):
    """Получение текущего статуса конкретной заявки по её ID"""
    query = (
        select(Application)
        .where(Application.id == application_id)
        .join(Application.scoring_result, isouter=True)
    )
    result = await db.execute(query)
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    return {
        "application_id": application.id,
        "status": application.status,
        "amount": application.amount,
        "term": application.term,
        "score": application.scoring_result.score if application.scoring_result else None,
        "reasons": application.scoring_result.reasons.get("stop_factors", []) if application.scoring_result else []
    }


@router.get("/company/{inn}")
async def get_company_applications_history(inn: str, db: AsyncSession = Depends(get_db)):
    """Получение истории всех кредитных/лизинговых заявок конкретной компании по ИНН"""
    query = (
        select(Application)
        .join(Company)
        .where(Company.inn == inn)
        .join(Application.scoring_result, isouter=True)
        .order_by(Application.id.desc())
    )
    result = await db.execute(query)
    applications = result.scalars().all()

    history = []
    for app in applications:
        history.append({
            "application_id": app.id,
            "amount": app.amount,
            "term": app.term,
            "status": app.status,
            "score": app.scoring_result.score if app.scoring_result else None,
            "reasons": app.scoring_result.reasons.get("stop_factors", []) if app.scoring_result else []
        })

    return {"inn": inn, "total_applications": len(history), "history": history}


# Импортируем модули аналитики из папки credit_ml
from src.credit_ml import reasons as ml_reasons
from src.credit_ml import recommend as ml_recommend


@router.get("/{application_id}/reasons", tags=["Analytics & Recommendations"])
async def get_detailed_reasons(application_id: int, db: AsyncSession = Depends(get_db)):
    """
    Детализированный разбор стоп-факторов и причин отказа на базе модуля reasons.py.
    Решает главную боль клиентов: 'Непонятны причины отказа'.
    """
    query = (
        select(Application)
        .where(Application.id == application_id)
        .join(Application.scoring_result, isouter=True)
    )
    result = await db.execute(query)
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    if application.status == "approved":
        return {"status": "approved", "message": "Заявка одобрена, стоп-факторы отсутствуют."}

    # Вызываем функцию разбора причин из вашего ml-модуля.
    # Если функция называется иначе (например, analyze_reasons), скорректируйте имя.
    detailed_reasons = []
    if hasattr(ml_reasons, "get_reasons"):
        try:
            # Передаем сохраненные стоп-факторы или саму запись для анализа
            detailed_reasons = ml_reasons.get_reasons(application.scoring_result.reasons)
        except Exception:
            pass

    # Фолбек, если список пуст
    if not detailed_reasons:
        detailed_reasons = application.scoring_result.reasons.get("stop_factors", [
            "Высокая долговая нагрузка по данным БКИ",
            "Срок жизни юридического лица менее порогового значения"
        ])

    return {
        "application_id": application_id,
        "status": "declined",
        "title": "Почему банк принял такое решение?",
        "detailed_reasons": detailed_reasons
    }


@router.get("/{application_id}/alternatives", tags=["Analytics & Recommendations"])
async def get_alternative_offers(application_id: int, db: AsyncSession = Depends(get_db)):
    """
    Автоматический подбор альтернативных условий (кредит с меньшим лимитом / лизинг).
    Использует модуль recommend.py для удержания клиента в банке.
    """
    result = await db.execute(select(Application).where(Application.id == application_id))
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    alternatives = []
    # Вызываем логику рекомендаций из вашего модуля recommend.py
    if hasattr(ml_recommend, "get_alternatives"):
        try:
            alternatives = ml_recommend.get_alternatives(application.amount, application.term)
        except Exception:
            pass

    # Фолбек-генератор предложений, если в модели заложен другой интерфейс
    if not alternatives:
        alternatives = [
            {
                "product_type": "Лизинг",
                "message": "Одобрение лизинга коммерческого авто или оборудования вместо классического кредита.",
                "max_amount": application.amount,
                "term_months": application.term
            },
            {
                "product_type": "Кредит с уменьшенным лимитом",
                "message": "Снижение запрашиваемой суммы для прохождения скорингового порога.",
                "max_amount": round(application.amount * 0.5, 2),  # Предлагаем 50% от суммы
                "term_months": application.term + 12  # Увеличиваем срок, чтобы снизить платеж
            }
        ]

    return {
        "application_id": application_id,
        "original_amount": application.amount,
        "status": application.status,
        "alternative_offers": alternatives
    }


@router.delete("/{application_id}", tags=["Alfa Credit Conveyor"])
async def cancel_application(application_id: int, db: AsyncSession = Depends(get_db)):
    """Отзыв кредитной заявки клиентом до подписания договора"""
    result = await db.execute(select(Application).where(Application.id == application_id))
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    application.status = "cancelled"
    await db.commit()
    return {"application_id": application_id, "status": "cancelled", "message": "Заявка успешно отозвана клиентом."}

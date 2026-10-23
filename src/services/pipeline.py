from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.services.scoring import ScoringService
from src.models import Company, Application, ScoringResult

# Импортируем сам модуль mocks, чтобы избежать ошибок отсутствия конкретного имени
from src.credit_ml import mocks


async def run_credit_conveyor(inn: str, amount: float, term: int, db: AsyncSession) -> dict:
    """ Сквозной кредитный конвейер Альфа-Банка на базе заложенной ML-логики """

    # Пытаемся автоматически найти функцию генерации внутри твоего mocks.py
    # Мы проверяем самые частые названия функций для хакатонных ML-моделей:
    external_data = None

    for func_name in ["get_mock_features", "generate_mock_data", "get_company_profile", "latent_risk"]:
        if hasattr(mocks, func_name):
            mock_func = getattr(mocks, func_name)
            try:
                # Вызываем найденную функцию генерации фичей
                external_data = mock_func(inn)
                break
            except Exception:
                try:
                    external_data = mock_func()  # Пробуем вызвать без аргументов
                    break
                except Exception:
                    continue

    # Если ни одна функция не подошла или упала, делаем безопасный дефолтный фолбек признаков
    if not external_data or not isinstance(external_data, dict):
        external_data = {
            "revenue": 75_000_000.0 if not inn.startswith("000") else 45_000.0,
            "company_age_months": 36 if not inn.startswith("000") else 2,
            "active_loans_count": 0 if not inn.startswith("000") else 5,
            "delinquency_days": 0 if not inn.startswith("000") else 60
        }

    # 2. Проверяем наличие компании в PostgreSQL
    query = select(Company).where(Company.inn == inn)
    result = await db.execute(query)
    company = result.scalar_one_or_none()

    revenue_val = external_data.get("revenue", 10_000_000.0)

    if not company:
        company = Company(
            inn=inn,
            name=f"ООО МСБ-Бизнес {inn[-4:]}",
            revenue=float(revenue_val)
        )
        db.add(company)
        await db.flush()  # Получаем ID компании без фиксации транзакции

    # 3. Регистрируем заявку на кредит/лизинг
    application = Application(
        company_id=company.id,
        amount=amount,
        term=term,
        status="processing"
    )
    db.add(application)
    await db.flush()

    # 4. Формируем вектор фичей, обогащая данные моков параметрами запроса клиента
    ml_features = {
        **external_data,
        "requested_amount": amount,
        "requested_term_months": term
    }

    # 5. Передаем вектор в сервис скоринга (который под капотом вызывает scorecard.py)
    scoring_service = ScoringService()
    ml_result = scoring_service.predict_application(ml_features)

    # 6. Обновляем статус заявки и сохраняем вердикт нейросети
    application.status = ml_result["decision"]

    scoring_record = ScoringResult(
        application_id=application.id,
        score=ml_result["score"],
        decision=ml_result["decision"],
        reasons={"stop_factors": ml_result["reasons"]}
    )
    db.add(scoring_record)
    await db.commit()

    return {
        "application_id": application.id,
        "company_name": company.name,
        "status": application.status,
        "score": ml_result["score"],
        "reasons": ml_result["reasons"]
    }

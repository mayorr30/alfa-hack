"""Бизнес-логика заявки: создать → оценить в ML → сменить статус → сохранить историю.

Роуты (routes/*.py) только принимают HTTP-запрос и вызывают функции отсюда.
Всё, что меняет заявку, проходит через _move(), который проверяет машину состояний.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import ml_client
from . import statuses as S
from .models import Application, ApplicationEvent, Assessment, Company, Document
from .schemas import (AcceptOffer, AnalystDecision, ApplicationCreate, ApplicationOut,
                      ApplicationShort, AssessmentOut, DocumentOut, EventOut, ProvideInfo)


class WorkflowError(Exception):
    """Действие недоступно в текущем статусе заявки (HTTP 409)."""


class NotFound(Exception):
    """Заявка не найдена (HTTP 404)."""


# ---------------- внутренние помощники ----------------

def _move(app: Application, target: str, actor: str, comment: str | None = None) -> None:
    current = app.status  # у новой заявки статуса ещё нет (None)
    if not S.can_transition(current, target):
        raise WorkflowError(
            f"Нельзя перевести заявку из статуса «{S.LABELS.get(current, current)}» "
            f"в «{S.LABELS[target]}»")
    app.status = target
    app.events.append(ApplicationEvent(from_status=current, to_status=target, actor=actor,
                                       comment=comment))


async def _load(db: AsyncSession, app_id: int) -> Application:
    q = (select(Application).where(Application.id == app_id)
         .options(selectinload(Application.company), selectinload(Application.assessments),
                  selectinload(Application.events), selectinload(Application.documents))
         .execution_options(populate_existing=True))
    app = (await db.execute(q)).scalar_one_or_none()
    if app is None:
        raise NotFound(f"Заявка №{app_id} не найдена")
    return app


async def _get_or_create_company(db: AsyncSession, inn: str) -> Company:
    card = ml_client.company_card(inn)  # 404, если ИНН нет в моке ЕГРЮЛ
    company = (await db.execute(select(Company).where(Company.inn == inn))).scalar_one_or_none()
    if company is None:
        company = Company(inn=inn, name=card["name"], legal_form=card["legal_form"],
                          industry=card["industry"])
        db.add(company)
        await db.flush()
    return company


def _ml_payload(app: Application) -> dict:
    """Собираем запрос в ML из того, что сохранено по заявке."""
    payload = {
        "inn": app.company.inn,
        "consent_bki": app.consent_bki,
        "purpose": app.purpose,
        "product_code": app.product_code,
        "requested_amount": float(app.requested_amount),
        "term_months": app.term_months,
    }
    payload.update({k: v for k, v in (app.client_financials or {}).items() if v is not None})
    statements = [d for d in app.documents if d.kind == "bank_statement"]
    if statements:
        payload["statement_csv"] = statements[-1].content
    return payload


async def _assess_and_route(app: Application) -> Assessment:
    """Прогон через ML, сохранение результата и перевод заявки в статус по решению."""
    result = await ml_client.assess(_ml_payload(app))
    risk = result.get("risk") or {}
    assessment = Assessment(
        decision=result["decision"], next_status=result["next_status"],
        score=risk.get("score"), pd=risk.get("pd"), grade=risk.get("grade"),
        requires_manual_review=result["requires_manual_review"],
        model_version=result["model_version"], policy_version=result["policy_version"],
        result=result,
    )
    app.assessments.append(assessment)
    _move(app, S.FROM_ML[result["next_status"]], "system",
          f"Решение ML: {result['decision_label']}")
    return assessment


# ---------------- преобразование в ответы API ----------------

def to_out(app: Application) -> ApplicationOut:
    last = app.assessments[-1] if app.assessments else None
    assessment = None
    if last is not None:
        r = last.result
        assessment = AssessmentOut(
            decision=r["decision"], decision_label=r["decision_label"],
            client_message=r["client_message"], risk=r.get("risk"), offer=r.get("offer"),
            offers=r.get("offers", []), reasons=r.get("reasons", []), advice=r.get("advice", []),
            missing_data=r.get("missing_data", []), checks=r.get("checks", []),
            notes=r.get("notes", []), requires_manual_review=r["requires_manual_review"],
            manual_review_flags=r.get("manual_review_flags", []),
            model_version=last.model_version, policy_version=last.policy_version,
            assessed_at=last.created_at,
        )
    purpose_labels = {p["code"]: p["label"] for p in ml_client.purposes()}
    return ApplicationOut(
        id=app.id, inn=app.company.inn, company_name=app.company.name,
        purpose=app.purpose, purpose_label=purpose_labels.get(app.purpose),
        purpose_text=app.purpose_text, product_code=app.product_code,
        requested_amount=float(app.requested_amount), term_months=app.term_months,
        consent_bki=app.consent_bki, status=app.status, status_label=S.LABELS[app.status],
        status_hint=S.HINTS[app.status], actions=S.client_actions(app.status),
        accepted_offer=app.accepted_offer, assessment=assessment,
        assessments_count=len(app.assessments),
        documents=[DocumentOut(id=d.id, kind=d.kind, filename=d.filename, created_at=d.created_at)
                   for d in app.documents],
        events=[EventOut(from_status=e.from_status, to_status=e.to_status,
                         to_status_label=S.LABELS[e.to_status], actor=e.actor, comment=e.comment,
                         created_at=e.created_at) for e in app.events],
        created_at=app.created_at, updated_at=app.updated_at,
    )


def to_short(app: Application) -> ApplicationShort:
    last = app.assessments[-1] if app.assessments else None
    return ApplicationShort(
        id=app.id, inn=app.company.inn, company_name=app.company.name,
        product_code=app.product_code, requested_amount=float(app.requested_amount),
        term_months=app.term_months, status=app.status, status_label=S.LABELS[app.status],
        grade=last.grade if last else None, score=last.score if last else None,
        requires_manual_review=bool(last and last.requires_manual_review),
        created_at=app.created_at, updated_at=app.updated_at,
    )


# ---------------- действия клиента ----------------

async def create_application(db: AsyncSession, data: ApplicationCreate) -> ApplicationOut:
    company = await _get_or_create_company(db, data.inn)
    fin = {"annual_revenue": data.annual_revenue, "net_margin": data.net_margin}
    app = Application(
        company=company, purpose=data.purpose, purpose_text=data.purpose_text,
        product_code=data.product_code, requested_amount=data.requested_amount,
        term_months=data.term_months, consent_bki=data.consent_bki,
        client_financials=fin if any(v is not None for v in fin.values()) else None,
        assessments=[], events=[], documents=[],
    )
    _move(app, S.SUBMITTED, "client", "Заявка создана в кабинете")
    db.add(app)
    await _assess_and_route(app)  # ошибка ML → исключение → ничего не сохранится
    await db.commit()
    return to_out(await _load(db, app.id))


async def provide_info(db: AsyncSession, app_id: int, data: ProvideInfo) -> ApplicationOut:
    app = await _load(db, app_id)
    if app.status != S.INFO_REQUESTED:
        raise WorkflowError("Дозагрузка данных доступна только в статусе «Нужны данные»")
    if not any([data.consent_bki, data.statement_csv, data.annual_revenue, data.net_margin]):
        raise WorkflowError("Ничего не передано: нужна выписка, финансы или согласие на БКИ")
    if data.statement_csv:
        await ml_client.analyze_statement(data.statement_csv)  # проверим формат до сохранения
        app.documents.append(Document(kind="bank_statement", filename=data.statement_filename,
                                      content=data.statement_csv))
    if data.consent_bki:
        app.consent_bki = True
    if data.annual_revenue is not None or data.net_margin is not None:
        fin = dict(app.client_financials or {})
        if data.annual_revenue is not None:
            fin["annual_revenue"] = data.annual_revenue
        if data.net_margin is not None:
            fin["net_margin"] = data.net_margin
        app.client_financials = fin
    _move(app, S.SUBMITTED, "client", "Клиент дозагрузил данные")
    await _assess_and_route(app)
    await db.commit()
    return to_out(await _load(db, app.id))


async def accept_offer(db: AsyncSession, app_id: int, data: AcceptOffer) -> ApplicationOut:
    app = await _load(db, app_id)
    if app.status not in S.OFFER_STATUSES:
        raise WorkflowError("Принять предложение можно только после положительного решения")
    offers = app.assessments[-1].result.get("offers") or []
    if data.offer_index >= len(offers):
        raise WorkflowError(f"Варианта №{data.offer_index} нет: доступно {len(offers)}")
    offer = offers[data.offer_index]
    app.accepted_offer = offer
    _move(app, S.OFFER_ACCEPTED, "client",
          f"Принят вариант: {offer['product_name']}, {offer['amount']:,.0f} ₽ на "
          f"{offer['term_months']} мес.".replace(",", " "))
    await db.commit()
    return to_out(await _load(db, app.id))


async def sign(db: AsyncSession, app_id: int) -> ApplicationOut:
    app = await _load(db, app_id)
    _move(app, S.ISSUED, "client", "Договор подписан, деньги перечислены (симуляция)")
    await db.commit()
    return to_out(await _load(db, app.id))


async def cancel(db: AsyncSession, app_id: int) -> ApplicationOut:
    app = await _load(db, app_id)
    _move(app, S.CANCELLED, "client", "Клиент отозвал заявку")
    await db.commit()
    return to_out(await _load(db, app.id))


async def get_application(db: AsyncSession, app_id: int) -> ApplicationOut:
    return to_out(await _load(db, app_id))


async def list_applications(db: AsyncSession, inn: str | None = None,
                            status: str | None = None) -> list[ApplicationShort]:
    q = (select(Application).join(Application.company)
         .options(selectinload(Application.company), selectinload(Application.assessments))
         .order_by(Application.id.desc()))
    if inn:
        q = q.where(Company.inn == inn)
    if status:
        q = q.where(Application.status == status)
    rows = (await db.execute(q)).scalars().all()
    return [to_short(a) for a in rows]


# ---------------- действия банка ----------------

async def analyst_decide(db: AsyncSession, app_id: int, data: AnalystDecision) -> ApplicationOut:
    app = await _load(db, app_id)
    if app.status != S.MANUAL_REVIEW:
        raise WorkflowError("Решение аналитика нужно только для заявок на ручной проверке")
    _move(app, S.OFFER_READY if data.approve else S.DECLINED, "analyst", data.comment)
    await db.commit()
    return to_out(await _load(db, app.id))

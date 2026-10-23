"""Статусы заявки и разрешённые переходы между ними (машина состояний).

Почему отдельный файл: правила «из какого статуса куда можно перейти» должны жить
в одном месте. Тогда API не сможет, например, выдать кредит по отклонённой заявке.
"""

from __future__ import annotations

SUBMITTED = "SUBMITTED"
OFFER_READY = "OFFER_READY"
COUNTER_OFFER = "COUNTER_OFFER"
ALTERNATIVE_OFFER = "ALTERNATIVE_OFFER"
INFO_REQUESTED = "INFO_REQUESTED"
MANUAL_REVIEW = "MANUAL_REVIEW"
DECLINED = "DECLINED"
OFFER_ACCEPTED = "OFFER_ACCEPTED"
ISSUED = "ISSUED"
CANCELLED = "CANCELLED"

LABELS = {
    SUBMITTED: "Заявка отправлена",
    OFFER_READY: "Одобрено",
    COUNTER_OFFER: "Одобрено на других условиях",
    ALTERNATIVE_OFFER: "Предложен другой продукт",
    INFO_REQUESTED: "Нужны данные",
    MANUAL_REVIEW: "На проверке у аналитика",
    DECLINED: "Отказ",
    OFFER_ACCEPTED: "Условия приняты",
    ISSUED: "Кредит выдан",
    CANCELLED: "Отозвана",
}

# Что делать клиенту дальше — показывается в кабинете
HINTS = {
    SUBMITTED: "Заявка обрабатывается.",
    OFFER_READY: "Проверьте условия и примите предложение.",
    COUNTER_OFFER: "Выберите подходящий вариант условий.",
    ALTERNATIVE_OFFER: "Посмотрите предложенный продукт и примите его, если подходит.",
    INFO_REQUESTED: "Загрузите недостающие данные — заявка будет пересчитана.",
    MANUAL_REVIEW: "Заявку проверяет кредитный аналитик. Статус обновится здесь.",
    DECLINED: "Посмотрите причины и советы, что можно улучшить.",
    OFFER_ACCEPTED: "Подпишите договор, чтобы получить деньги.",
    ISSUED: "Деньги перечислены на счёт (симуляция).",
    CANCELLED: "Заявка отозвана.",
}

FINAL = {DECLINED, ISSUED, CANCELLED}
OFFER_STATUSES = {OFFER_READY, COUNTER_OFFER, ALTERNATIVE_OFFER}

# Решение ML (next_status) → статус заявки. Совпадают по названию, но маппинг явный.
FROM_ML = {
    "OFFER_READY": OFFER_READY,
    "COUNTER_OFFER": COUNTER_OFFER,
    "ALTERNATIVE_OFFER": ALTERNATIVE_OFFER,
    "INFO_REQUESTED": INFO_REQUESTED,
    "MANUAL_REVIEW": MANUAL_REVIEW,
    "DECLINED": DECLINED,
}

# Разрешённые переходы: из статуса → в какие статусы
TRANSITIONS: dict[str | None, set[str]] = {
    None: {SUBMITTED},
    SUBMITTED: set(FROM_ML.values()) | {CANCELLED},
    INFO_REQUESTED: {SUBMITTED, CANCELLED},
    MANUAL_REVIEW: {OFFER_READY, DECLINED, CANCELLED},
    OFFER_READY: {OFFER_ACCEPTED, CANCELLED},
    COUNTER_OFFER: {OFFER_ACCEPTED, CANCELLED},
    ALTERNATIVE_OFFER: {OFFER_ACCEPTED, CANCELLED},
    OFFER_ACCEPTED: {ISSUED, CANCELLED},
    DECLINED: set(),
    ISSUED: set(),
    CANCELLED: set(),
}


def can_transition(current: str | None, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def client_actions(status: str) -> list[str]:
    """Какие кнопки показать клиенту в этом статусе."""
    actions = []
    if status in OFFER_STATUSES:
        actions.append("accept")
    if status == INFO_REQUESTED:
        actions.append("provide_info")
    if status == OFFER_ACCEPTED:
        actions.append("sign")
    if status not in FINAL:
        actions.append("cancel")
    return actions

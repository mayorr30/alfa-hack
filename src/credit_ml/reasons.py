"""Перевод вклада признаков балльной карты в понятные причины и советы клиенту."""

from __future__ import annotations

from .config import INDUSTRIES


def rub(x: float) -> str:
    """Форматирование суммы: 12 500 000 ₽."""
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def _years(months: float) -> str:
    y, m = divmod(int(months), 12)
    parts = []
    if y:
        parts.append(f"{y} г.")
    if m:
        parts.append(f"{m} мес.")
    return " ".join(parts) or "менее месяца"


# feature -> (текст если ухудшает, текст если улучшает, совет клиенту)
TEMPLATES = {
    "business_age_months": (
        lambda v, r: f"Бизнес работает недолго: {_years(v)}",
        lambda v, r: f"Бизнес работает {_years(v)}",
        "Рассмотрите продукт с меньшими требованиями к сроку работы бизнеса",
    ),
    "log_revenue": (
        lambda v, r: f"Небольшой масштаб выручки: {rub(r['annual_revenue'])} в год",
        lambda v, r: f"Достаточный масштаб выручки: {rub(r['annual_revenue'])} в год",
        "Подтвердите полную выручку выпиской по всем расчетным счетам",
    ),
    "net_margin": (
        lambda v, r: f"Низкая рентабельность бизнеса: {v:.0%}",
        lambda v, r: f"Хорошая рентабельность бизнеса: {v:.0%}",
        None,
    ),
    "revenue_trend": (
        lambda v, r: (f"Выручка снижается: {v:+.0%} к прошлому периоду" if v < 0 else
                      f"Слабый рост выручки: {v:+.0%} к прошлому периоду"),
        lambda v, r: f"Выручка растет: {v:+.0%} к прошлому периоду",
        None,
    ),
    "revenue_volatility": (
        lambda v, r: "Обороты сильно колеблются от месяца к месяцу",
        lambda v, r: "Стабильные обороты по месяцам",
        None,
    ),
    "debt_to_revenue": (
        lambda v, r: f"Высокая текущая долговая нагрузка: {v:.0%} годовой выручки",
        lambda v, r: "Невысокая текущая долговая нагрузка",
        "Снизьте текущую задолженность или рассмотрите рефинансирование",
    ),
    "dscr": (
        lambda v, r: f"Денежного потока мало для платежей: коэффициент покрытия {v:.2f}",
        lambda v, r: f"Денежный поток покрывает платежи с запасом: коэффициент {v:.2f}",
        "Уменьшите сумму или увеличьте срок кредита — платеж станет ниже",
    ),
    "loan_to_revenue": (
        lambda v, r: f"Сумма велика относительно выручки: {v:.0%} годовой выручки",
        lambda v, r: "Сумма соразмерна выручке бизнеса",
        "Уменьшите запрашиваемую сумму",
    ),
    "bki_max_overdue_days_12m": (
        lambda v, r: f"Просрочки по кредитам за последние 12 мес. (до {v:.0f} дн.)",
        lambda v, r: "Нет просрочек по кредитам за последние 12 мес.",
        "Погасите текущие просрочки и не допускайте новых",
    ),
    "bki_overdue_count_12m": (
        lambda v, r: f"Количество просрочек за 12 мес.: {v:.0f}",
        lambda v, r: "Нет просрочек по кредитам за последние 12 мес.",
        "Погасите текущие просрочки и не допускайте новых",
    ),
    "bki_inquiries_3m": (
        lambda v, r: f"Много обращений за кредитами за 3 мес.: {v:.0f}",
        lambda v, r: "Немного обращений за кредитами в последнее время",
        None,
    ),
    "director_changes_12m": (
        lambda v, r: f"Руководитель менялся {v:.0f} раз(а) за 12 мес.",
        lambda v, r: "Руководитель не менялся",
        None,
    ),
    "industry": (
        lambda v, r: f"Отрасль с повышенным риском: {INDUSTRIES.get(v, v)}",
        lambda v, r: f"Отрасль с умеренным риском: {INDUSTRIES.get(v, v)}",
        None,
    ),
    "tax_arrears": (
        lambda v, r: "Есть задолженность по налогам",
        lambda v, r: "Нет задолженности по налогам",
        "Погасите налоговую задолженность",
    ),
    "mass_address": (
        lambda v, r: "Адрес массовой регистрации",
        lambda v, r: "Адрес регистрации без признаков массовости",
        None,
    ),
    "legal_form": (
        lambda v, r: "Организационно-правовая форма: ИП",
        lambda v, r: "Организационно-правовая форма: ООО",
        None,
    ),
}

# признаки одной природы — показываем только самый весомый
FAMILY = {"bki_overdue_count_12m": "bki_overdue", "bki_max_overdue_days_12m": "bki_overdue"}
SKIP_IN_REASONS = {"legal_form"}  # не объясняем клиенту то, на что он не влияет
MIN_POINTS = 5  # факторы со вкладом меньше 5 баллов не показываем — это шум


def model_reasons(contribs: list[dict], raw: dict, n_neg: int = 3, n_pos: int = 2,
                  skip: set[str] | None = None) -> list[dict]:
    """Топ факторов, ухудшающих и улучшающих оценку."""
    best: dict[str, dict] = {}
    skip = SKIP_IN_REASONS | (skip or set())
    for c in contribs:
        if c["feature"] in skip or abs(c["delta"]) < MIN_POINTS:
            continue
        key = (FAMILY.get(c["feature"], c["feature"]), c["delta"] < 0)
        if key not in best or abs(c["delta"]) > abs(best[key]["delta"]):
            best[key] = c
    items = list(best.values())
    neg = sorted([c for c in items if c["delta"] < 0], key=lambda c: c["delta"])[:n_neg]
    pos = sorted([c for c in items if c["delta"] > 0], key=lambda c: -c["delta"])[:n_pos]
    out = []
    for c, impact in [(c, "negative") for c in neg] + [(c, "positive") for c in pos]:
        neg_t, pos_t, advice = TEMPLATES[c["feature"]]
        text = (neg_t if impact == "negative" else pos_t)(c["value"], raw)
        out.append({
            "code": f"MODEL_{c['feature'].upper()}",
            "source": "model",
            "impact": impact,
            "text": text,
            "points": int(c["delta"]),
            "advice": advice if impact == "negative" else None,
        })
    return out

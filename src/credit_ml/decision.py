"""Кредитное решение: стоп-факторы → полнота данных → продукт → скоринг → посильность → решение.

Скоринг дает уровень риска (PD, грейд). Решение принимает кредитная логика поверх него:
одинаковый риск может привести к разным условиям для разных продуктов и сумм (стр. 14 кейса).
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from .config import (POLICY, PRODUCTS, PRODUCTS_BY_CODE, PURPOSES, RISK_PREMIUM_PP, Product)
from .features import annuity_payment, compute_dscr, max_amount_for_payment
from .model_store import RiskModel
from .reasons import model_reasons, rub
from .recommend import missing_for_decision, recommend
from .statement import StatementError, analyze_statement

POLICY_VERSION = "demo-policy-1.0"
STD_TERMS = (3, 6, 12, 18, 24, 36, 48, 60, 72, 84)

DECISION_LABELS = {
    "APPROVE": "Одобрено",
    "COUNTER_OFFER": "Одобрено на измененных условиях",
    "ALTERNATIVE_PRODUCT": "Предложен другой продукт",
    "REQUEST_INFO": "Нужны дополнительные данные",
    "MANUAL_REVIEW": "Передано кредитному аналитику",
    "DECLINE": "Отказ",
}
# рекомендуемый следующий статус заявки для бэкенда
NEXT_STATUS = {
    "APPROVE": "OFFER_READY",
    "COUNTER_OFFER": "COUNTER_OFFER",
    "ALTERNATIVE_PRODUCT": "ALTERNATIVE_OFFER",
    "REQUEST_INFO": "INFO_REQUESTED",
    "MANUAL_REVIEW": "MANUAL_REVIEW",
    "DECLINE": "DECLINED",
}
EGRUL_STATUS_RU = {"liquidating": "в процессе ликвидации", "liquidated": "ликвидирована",
                   "bankruptcy": "в процедуре банкротства"}


def _round_down(x: float, step: int = 10_000) -> int:
    return int(math.floor(x / step) * step)


class Assessment:
    """Сборщик ответа: проверки, причины, предложения."""

    def __init__(self):
        self.checks: list[dict] = []
        self.reasons: list[dict] = []
        self.missing: list[dict] = []
        self.offers: list[dict] = []
        self.manual_flags: list[str] = []
        self.risk: dict | None = None
        self.notes: list[str] = []

    def check(self, code: str, name: str, status: str, details: str = "") -> None:
        self.checks.append({"code": code, "name": name, "status": status, "details": details})

    def reason(self, code: str, text: str, impact: str = "negative", advice: str | None = None,
               source: str = "policy") -> None:
        self.reasons.append({"code": code, "source": source, "impact": impact, "text": text,
                             "points": None, "advice": advice})


# ---------------------------------------------------------------------------
def evaluate_offer(model: RiskModel, profile: dict, p: Product, purpose: str,
                   amount: float, term: int) -> dict:
    raw = {**profile, "product_code": p.code, "purpose": purpose,
           "requested_amount": amount, "term_months": term}
    r = model.evaluate(raw)
    rate = None
    payment = None
    dscr = None
    if r["grade"] in RISK_PREMIUM_PP:
        rate = round(p.base_rate + RISK_PREMIUM_PP[r["grade"]] / 100, 4)
        payment = annuity_payment(amount, rate, term)
        dscr = compute_dscr(profile["annual_revenue"], profile["net_margin"],
                            profile.get("existing_monthly_payments") or 0, payment)
    return {
        "product_code": p.code, "product_name": p.name, "amount": float(amount),
        "term_months": int(term), "rate": rate,
        "monthly_payment": round(payment, 2) if payment else None,
        "dscr": round(dscr, 2) if dscr is not None else None,
        "collateral": p.collateral,
        "risk": r,
    }


def _ok(ev: dict) -> bool:
    return ev["rate"] is not None and ev["dscr"] >= POLICY.min_dscr


def _public(ev: dict, kind: str, comment: str) -> dict:
    o = {k: v for k, v in ev.items() if k != "risk"}
    o.update({"kind": kind, "comment": comment, "grade": ev["risk"]["grade"],
              "pd": ev["risk"]["pd"]})
    return o


def counter_offers(model, profile, p: Product, purpose, amount, term) -> list[dict]:
    """Варианты, при которых платеж посилен: дольше срок и/или меньше сумма."""
    out = []
    # 1) та же сумма, дольше срок
    for t in [t for t in STD_TERMS if term < t <= p.max_term]:
        ev = evaluate_offer(model, profile, p, purpose, amount, t)
        if _ok(ev):
            out.append(_public(ev, "LONGER_TERM",
                               f"Та же сумма на {t} мес. вместо {term} мес."))
            break
    # 2) меньше сумма, тот же срок; 3) меньше сумма, максимальный срок
    for t, kind in ((term, "LOWER_AMOUNT"), (p.max_term, "LOWER_AMOUNT_LONGER_TERM")):
        if kind == "LOWER_AMOUNT_LONGER_TERM" and (t == term or out and out[0]["kind"] == "LONGER_TERM"):
            continue
        cf = profile["annual_revenue"] * profile["net_margin"] / 12
        exist = profile.get("existing_monthly_payments") or 0
        room = (cf + exist) / POLICY.min_dscr - exist
        if room <= 0:
            continue
        rate_guess = p.base_rate
        a = 0.0
        ev = None
        for _ in range(6):  # ставка зависит от грейда, грейд — от суммы
            a = _round_down(min(max_amount_for_payment(room, rate_guess, t), amount * 0.999))
            if a < max(p.min_amount, POLICY.counter_offer_min_share * amount):
                ev = None
                break
            ev = evaluate_offer(model, profile, p, purpose, a, t)
            if ev["rate"] is None:
                ev = None
                break
            if _ok(ev):
                break
            rate_guess = ev["rate"]
        if ev is not None and _ok(ev):
            out.append(_public(ev, kind, f"{rub(a)} на {t} мес. вместо {rub(amount)}"))
    return out


# ---------------------------------------------------------------------------
def resolve_financials(profile: dict, req: dict, a: Assessment) -> dict:
    profile = dict(profile)
    source = "fns_reporting" if profile.get("annual_revenue") else None
    if req.get("annual_revenue"):
        profile["annual_revenue"] = float(req["annual_revenue"])
        source = "client"
    if req.get("net_margin") is not None:
        profile["net_margin"] = float(req["net_margin"])
    if req.get("statement_csv"):
        try:
            st = analyze_statement(req["statement_csv"])
        except StatementError as e:
            a.check("STATEMENT", "Разбор выписки", "failed", str(e))
            a.missing.append({"field": "statement_csv", "label": "Выписка по счету",
                              "why": f"Выписку не удалось прочитать: {e}", "alternative": None})
            return profile
        profile.update({"annual_revenue": st.annual_revenue, "net_margin": st.net_margin,
                        "revenue_trend": st.revenue_trend,
                        "revenue_volatility": st.revenue_volatility})
        source = "bank_statement"
        a.check("STATEMENT", "Разбор выписки", "passed",
                f"{st.months} мес., выручка в год {rub(st.annual_revenue)}, "
                f"рентабельность по потоку {st.net_margin:.0%}")
        for w in st.warnings:
            a.notes.append(w)
        if st.top_counterparty_share > 0.5:
            a.manual_flags.append("Высокая концентрация на одном контрагенте")
    if profile.get("revenue_trend") is None:
        profile["revenue_trend"] = 0.0
        a.notes.append("Нет данных о динамике выручки — принято нейтральное значение 0%")
    if profile.get("revenue_volatility") is None:
        profile["revenue_volatility"] = 0.3
        a.notes.append("Нет данных о стабильности оборотов — принято значение 0.3")
    profile["_financials_source"] = source
    return profile


def assess(req: dict, profile: dict, model: RiskModel) -> dict:
    a = Assessment()
    purpose = req["purpose"]
    amount = float(req["requested_amount"])
    term = int(req["term_months"])
    product_code = req.get("product_code")

    decision = None
    offer = None

    # 1. Статус в ЕГРЮЛ/ЕГРИП
    st = profile.get("egrul_status", "active")
    if st in POLICY.inactive_egrul_statuses:
        a.check("EGRUL_STATUS", "Статус в ЕГРЮЛ/ЕГРИП", "failed", EGRUL_STATUS_RU.get(st, st))
        a.reason("STOP_EGRUL_STATUS", f"Компания {EGRUL_STATUS_RU.get(st, st)} по данным реестра")
        decision = "DECLINE"
    else:
        a.check("EGRUL_STATUS", "Статус в ЕГРЮЛ/ЕГРИП", "passed", "действующая")

    # 2. Согласие на запрос в БКИ
    if decision is None and not req.get("consent_bki", False):
        a.check("BKI_CONSENT", "Согласие на запрос кредитной истории", "failed")
        a.missing.append({"field": "consent_bki", "label": "Согласие на запрос в БКИ",
                          "why": "Без согласия банк не может проверить кредитную историю",
                          "alternative": None})
        decision = "REQUEST_INFO"
    elif decision is None:
        a.check("BKI_CONSENT", "Согласие на запрос кредитной истории", "passed")

    # 3. Стоп-факторы
    if decision is None:
        age = profile["business_age_months"]
        if age < POLICY.min_business_age_months:
            a.check("BUSINESS_AGE", "Минимальный срок бизнеса", "failed", f"{age} мес.")
            a.reason("STOP_BUSINESS_AGE",
                     f"Бизнес работает меньше {POLICY.min_business_age_months} мес.",
                     advice="Подайте заявку, когда бизнесу исполнится 3 месяца")
            decision = "DECLINE"
        else:
            a.check("BUSINESS_AGE", "Минимальный срок бизнеса", "passed", f"{age} мес.")
        od = profile.get("bki_max_overdue_days_12m") or 0
        if od > POLICY.stop_overdue_days:
            a.check("BKI_OVERDUE", "Просрочки в БКИ", "failed", f"{od} дн.")
            a.reason("STOP_BKI_OVERDUE", f"Просрочка более {POLICY.stop_overdue_days} дней "
                                         f"за последние 12 мес.")
            decision = "DECLINE"
        elif decision is None:
            a.check("BKI_OVERDUE", "Просрочки в БКИ (стоп > 90 дн.)", "passed", f"макс. {od} дн.")

    # 4. Полнота финансовых данных
    if decision is None:
        profile = resolve_financials(profile, req, a)
        miss = missing_for_decision(profile) if not a.missing else a.missing
        if miss:
            a.missing = miss
            a.check("FINANCIALS", "Финансовые показатели", "failed", "нет данных о выручке")
            a.reason("NEED_FINANCIALS", "Недостаточно данных о выручке и прибыли бизнеса",
                     impact="neutral",
                     advice="Загрузите выписку по расчетному счету за 12 месяцев")
            decision = "REQUEST_INFO"
        else:
            src = {"fns_reporting": "бухотчетность (ФНС)", "client": "данные клиента",
                   "bank_statement": "выписка по счету"}.get(profile["_financials_source"], "—")
            a.check("FINANCIALS", "Финансовые показатели", "passed", f"источник: {src}")

    # 5. Продукт
    if decision is None:
        recs = recommend(purpose, amount, term, profile)
        chosen = PRODUCTS_BY_CODE.get(product_code) if product_code else None
        if chosen is None:
            top = next((r for r in recs if r["eligible"]), None)
            chosen = PRODUCTS_BY_CODE[top["product_code"]] if top else None
            if chosen:
                a.notes.append(f"Продукт не выбран клиентом — подобран «{chosen.name}»")
        chosen_rec = next((r for r in recs if chosen and r["product_code"] == chosen.code), None)
        in_range = chosen and chosen.min_amount <= amount <= chosen.max_amount \
            and chosen.min_term <= term <= chosen.max_term

        if chosen_rec and chosen_rec["eligible"] and in_range:
            a.check("PRODUCT_FIT", "Соответствие условиям продукта", "passed", chosen.name)
            decision, offer = _decide_for_product(a, model, profile, chosen, purpose, amount,
                                                  term)
            if decision == "DECLINE_AFFORDABILITY":
                decision, offer = _try_alternatives(a, model, profile, purpose, amount, term,
                                                    exclude=chosen.code, recs=recs)
                if decision is None:
                    decision = "DECLINE"
        else:
            why = (chosen_rec["why_not"] if chosen_rec else []) or []
            if chosen and not in_range:
                why.append(f"Сумма/срок вне условий продукта: {rub(chosen.min_amount)}–"
                           f"{rub(chosen.max_amount)}, {chosen.min_term}–{chosen.max_term} мес.")
            a.check("PRODUCT_FIT", "Соответствие условиям продукта", "failed",
                    "; ".join(why) or "нет подходящего продукта")
            for w in why:
                a.reason("PRODUCT_NOT_ELIGIBLE", w, impact="neutral")
            decision, offer = _try_alternatives(a, model, profile, purpose, amount, term,
                                                exclude=chosen.code if chosen else None,
                                                recs=recs)
            if decision is None:
                a.reason("NO_SUITABLE_PRODUCT", "Нет продукта, условия которого посильны "
                                                "для бизнеса при запрошенных параметрах")
                decision = "DECLINE"

    # 6. Ручная проверка — только поверх положительного решения
    if decision == "APPROVE" and a.manual_flags:
        decision = "MANUAL_REVIEW"
        a.reasons.insert(0, {"code": "MANUAL_REVIEW", "source": "policy", "impact": "neutral",
                             "text": "Нужна проверка кредитного аналитика: "
                                     + "; ".join(a.manual_flags).lower(),
                             "points": None, "advice": None})

    return _response(req, profile, a, decision, offer, model)


def _risk_check(a: Assessment, ev: dict) -> bool:
    r = ev["risk"]
    a.risk = r
    status = "failed" if r["grade"] in POLICY.decline_grades else \
        "warning" if r["grade"] in POLICY.manual_review_grades else "passed"
    a.check("SCORING", "Скоринговая оценка", status,
            f"балл {r['score']}, PD {r['pd']:.1%}, грейд {r['grade']}")
    return r["grade"] not in POLICY.decline_grades


def _manual_triggers(a: Assessment, profile: dict, amount: float, grade: str) -> None:
    if grade in POLICY.manual_review_grades:
        a.manual_flags.append(f"Грейд риска {grade} — нужна экспертная оценка")
    if amount > POLICY.max_auto_amount:
        a.manual_flags.append(f"Сумма выше лимита автоматического решения "
                              f"{rub(POLICY.max_auto_amount)}")
    if profile.get("mass_address"):
        a.manual_flags.append("Адрес массовой регистрации")
    if profile.get("tax_arrears"):
        a.manual_flags.append("Есть налоговая задолженность")
    if (profile.get("director_changes_12m") or 0) > POLICY.max_director_changes_12m:
        a.manual_flags.append("Частая смена руководителя")
    if (profile.get("bki_inquiries_3m") or 0) > POLICY.max_inquiries_3m:
        a.manual_flags.append("Много запросов кредитной истории за 3 мес.")
    if a.manual_flags:
        a.check("MANUAL_TRIGGERS", "Признаки для ручной проверки", "warning",
                "; ".join(a.manual_flags))
    else:
        a.check("MANUAL_TRIGGERS", "Признаки для ручной проверки", "passed", "нет")


def _decide_for_product(a, model, profile, p, purpose, amount, term):
    ev = evaluate_offer(model, profile, p, purpose, amount, term)
    if not _risk_check(a, ev):
        a.reason("RISK_TOO_HIGH", "Уровень кредитного риска выше допустимого")
        return "DECLINE", None
    _manual_triggers(a, profile, amount, ev["risk"]["grade"])
    if _ok(ev):
        a.check("DSCR", "Посильность платежа (DSCR ≥ 1.25)", "passed", f"{ev['dscr']:.2f}")
        return "APPROVE", _public(ev, "AS_REQUESTED", "На запрошенных условиях")
    a.check("DSCR", "Посильность платежа (DSCR ≥ 1.25)", "failed", f"{ev['dscr']:.2f}")
    a.reason("DSCR_LOW", f"Ежемесячный платеж {rub(ev['monthly_payment'])} слишком высок для "
                         f"текущего денежного потока",
             advice="Увеличьте срок или уменьшите сумму")
    opts = counter_offers(model, profile, p, purpose, amount, term)
    if opts:
        a.offers = opts
        return "COUNTER_OFFER", opts[0]
    return "DECLINE_AFFORDABILITY", None


def _try_alternatives(a, model, profile, purpose, amount, term, exclude, recs):
    for r in recs:
        if not r["eligible"] or r["product_code"] == exclude:
            continue
        p = PRODUCTS_BY_CODE[r["product_code"]]
        amt, t = r["suggested_amount"], r["suggested_term_months"]
        ev = evaluate_offer(model, profile, p, purpose, amt, t)
        if ev["rate"] is None:
            continue
        if not _ok(ev):
            opts = counter_offers(model, profile, p, purpose, amt, t)
            if not opts:
                continue
            best = opts[0]
            ev = evaluate_offer(model, profile, p, purpose, best["amount"], best["term_months"])
        a.check("ALTERNATIVE_PRODUCT", "Подбор другого продукта", "passed",
                f"{p.name}: {rub(ev['amount'])} на {ev['term_months']} мес.")
        _risk_check(a, ev)
        _manual_triggers(a, profile, ev["amount"], ev["risk"]["grade"])
        a.check("DSCR", "Посильность платежа (DSCR ≥ 1.25)", "passed", f"{ev['dscr']:.2f}")
        off = _public(ev, "ALTERNATIVE", f"Продукт «{p.name}»: {rub(ev['amount'])} на "
                                         f"{ev['term_months']} мес.")
        a.offers = [off]
        return "ALTERNATIVE_PRODUCT", off
    return None, None


CLIENT_MESSAGES = {
    "APPROVE": "Банк готов выдать кредит на запрошенных условиях. Проверьте условия и "
               "переходите к оформлению.",
    "COUNTER_OFFER": "Банк готов выдать кредит, но на других условиях, чтобы платеж был "
                     "посильным для бизнеса. Выберите подходящий вариант.",
    "ALTERNATIVE_PRODUCT": "Запрошенный продукт сейчас не подходит, но мы подобрали другой. "
                           "Посмотрите условия.",
    "REQUEST_INFO": "Чтобы принять решение, нам нужно еще немного данных.",
    "MANUAL_REVIEW": "Предварительное решение положительное. Заявку проверит кредитный "
                     "аналитик — статус обновится в кабинете.",
    "DECLINE": "К сожалению, сейчас банк не может предоставить финансирование. Ниже — "
               "причины и что можно сделать.",
}


def _response(req, profile, a: Assessment, decision: str, offer, model: RiskModel) -> dict:
    if a.risk:
        # если посильность уже объяснена правилом, не дублируем ее фактором модели
        skip = {"dscr"} if any(r["code"] == "DSCR_LOW" for r in a.reasons) else set()
        neg_first = model_reasons(a.risk["contributions"], profile, skip=skip)
        a.reasons.extend(neg_first)
    advice = []
    for r in a.reasons:
        if r.get("advice") and r["advice"] not in advice and r["impact"] == "negative":
            advice.append(r["advice"])
    return {
        "request_id": str(uuid.uuid4()),
        "decision": decision,
        "decision_label": DECISION_LABELS[decision],
        "next_status": NEXT_STATUS[decision],
        "client_message": CLIENT_MESSAGES[decision],
        "requires_manual_review": bool(a.manual_flags) or decision == "MANUAL_REVIEW",
        "manual_review_flags": a.manual_flags,
        "offer": offer,
        "offers": a.offers or ([offer] if offer else []),
        "risk": None if not a.risk else {
            "score": a.risk["score"], "pd": a.risk["pd"], "grade": a.risk["grade"]},
        "reasons": a.reasons,
        "advice": advice,
        "missing_data": a.missing,
        "checks": a.checks,
        "notes": a.notes,
        "request": {"purpose": req["purpose"], "purpose_label": PURPOSES.get(req["purpose"]),
                    "product_code": req.get("product_code"),
                    "requested_amount": float(req["requested_amount"]),
                    "term_months": int(req["term_months"])},
        "features": None if not a.risk else a.risk["features"],
        "financials_source": profile.get("_financials_source"),
        "model_version": model.version,
        "policy_version": POLICY_VERSION,
        "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer": "Демо-прототип на синтетических данных. Ставки и лимиты условные.",
    }


__all__ = ["assess", "evaluate_offer", "counter_offers", "PRODUCTS", "DECISION_LABELS"]

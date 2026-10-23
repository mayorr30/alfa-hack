"""Подбор кредитного продукта под задачу клиента.

1. Цель из свободного текста → категория цели (правила по ключевым словам, без LLM).
2. Для каждого продукта — жесткие условия (цель, срок бизнеса, форма) и мягкая
   оценка соответствия 0–100 (цель, сумма, срок, ставка, залог, посильность платежа).
3. Если известен профиль компании — предварительная оценка шанса одобрения по модели.
"""

from __future__ import annotations

from .config import POLICY, PRODUCTS, PURPOSES, Product
from .features import annuity_payment, compute_dscr
from .reasons import rub

PURPOSE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "refinance": ("рефинанс", "перекредит", "другого банка", "погасить кредит"),
    "real_estate": ("недвижимост", "помещени", "здани", "купить склад", "купить офис"),
    "contract": ("контракт", "госзаказ", "тендер", "44-фз", "223-фз", "исполнени"),
    "equipment": ("оборудован", "станок", "станк", "печь", "печи", "техник", "транспорт",
                  "автомобил", "машин", "грузовик", "трактор", "кофемашин", "холодильн",
                  "компьютер", "сервер"),
    "cash_gap": ("кассов", "разрыв", "зарплат", "налог", "отсрочк", "задерж"),
    "working_capital": ("товар", "закуп", "сырь", "материал", "оборот", "запас", "сезон"),
    "expansion": ("открыт", "точк", "филиал", "ремонт", "расширен", "развит", "направлени",
                  "магазин", "кафе", "кофейн", "производств"),
}


def classify_purpose(text: str) -> dict:
    t = (text or "").lower()
    hits = {p: [k for k in kws if k in t] for p, kws in PURPOSE_KEYWORDS.items()}
    hits = {p: h for p, h in hits.items() if h}
    if not hits:
        return {"purpose": "other", "label": PURPOSES["other"], "confidence": 0.0,
                "matched": [], "alternatives": []}
    ranked = sorted(hits.items(), key=lambda kv: -len(kv[1]))
    total = sum(len(h) for h in hits.values())
    best, words = ranked[0]
    return {
        "purpose": best,
        "label": PURPOSES[best],
        "confidence": round(len(words) / total, 2),
        "matched": words,
        "alternatives": [p for p, _ in ranked[1:]],
    }


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def score_product(p: Product, purpose: str, amount: float, term: int,
                  profile: dict | None = None) -> dict:
    why, why_not, warnings = [], [], []
    eligible = True

    targeted = purpose in p.purposes and len(p.purposes) <= 2
    universal = purpose in p.purposes and not targeted
    if targeted:
        fit = 40
        why.append(f"Создан для цели «{PURPOSES[purpose]}»")
    elif universal:
        fit = 25
        why.append("Подходит для разных целей бизнеса")
    else:
        fit = 0
        eligible = False
        why_not.append(f"Не предназначен для цели «{PURPOSES.get(purpose, purpose)}»")

    if profile:
        if profile.get("legal_form") not in p.legal_forms:
            eligible = False
            why_not.append("Недоступен для вашей организационно-правовой формы")
        age = profile.get("business_age_months")
        if age is not None and age < p.min_business_age:
            eligible = False
            why_not.append(f"Нужен срок работы бизнеса от {p.min_business_age} мес. "
                           f"(у вас {age} мес.)")

    s_amount = _clip(amount, p.min_amount, p.max_amount)
    if s_amount == amount:
        fit += 20
    else:
        fit += 8
        warnings.append(f"Сумма по продукту от {rub(p.min_amount)} до {rub(p.max_amount)} — "
                        f"предложим {rub(s_amount)}")
    s_term = int(_clip(term, p.min_term, p.max_term))
    if s_term == term:
        fit += 15
    else:
        fit += 5
        warnings.append(f"Срок по продукту от {p.min_term} до {p.max_term} мес. — "
                        f"предложим {s_term} мес.")

    fit += round(_clip((0.25 - p.base_rate) / 0.07, 0, 1) * 15)
    if p.collateral == "не требуется":
        fit += 10
        why.append("Без залога")

    payment = annuity_payment(s_amount, p.base_rate, s_term)
    dscr = None
    if profile and profile.get("annual_revenue") and profile.get("net_margin") is not None:
        dscr = compute_dscr(profile["annual_revenue"], profile["net_margin"],
                            profile.get("existing_monthly_payments") or 0, payment)
        if dscr < POLICY.min_dscr:
            fit -= 15
            warnings.append("Платеж может быть тяжелым для текущего денежного потока")
        else:
            why.append("Платеж посилен при текущих оборотах")

    return {
        "product_code": p.code,
        "product_name": p.name,
        "description": p.description,
        "eligible": eligible,
        "fit_score": int(_clip(fit, 0, 100)) if eligible else 0,
        "suggested_amount": s_amount,
        "suggested_term_months": s_term,
        "base_rate": p.base_rate,
        "estimated_monthly_payment": round(payment, 2),
        "estimated_dscr": round(dscr, 2) if dscr is not None else None,
        "collateral": p.collateral,
        "why": why,
        "why_not": why_not,
        "warnings": warnings,
    }


def recommend(purpose: str, amount: float, term: int, profile: dict | None = None) -> list[dict]:
    items = [score_product(p, purpose, amount, term, profile) for p in PRODUCTS]
    return sorted(items, key=lambda r: (not r["eligible"], -r["fit_score"]))


def missing_for_decision(profile: dict | None) -> list[dict]:
    """Каких данных не хватает для скоринга — их и надо дозапросить в форме."""
    if profile is None:
        return []
    miss = []
    if not profile.get("annual_revenue") or profile.get("net_margin") is None:
        miss.append({
            "field": "statement_csv",
            "label": "Выписка по расчетному счету за 12 месяцев",
            "why": "Нет бухгалтерской отчетности — нужно подтвердить обороты и прибыль",
            "alternative": "Или укажите годовую выручку и чистую прибыль (annual_revenue, "
                           "net_margin)",
        })
    return miss


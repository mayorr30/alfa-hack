"""Моки внешних источников: ЕГРЮЛ/ЕГРИП, ФНС «Прозрачный бизнес», БКИ, выписка.

Никаких реальных подключений: данные берутся из data/companies.json (синтетика).
Формат ответов упрощен, но сохраняет смысл интеграции (стр. 11 кейса).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import DATA_DIR


class CompanyNotFound(KeyError):
    pass


class CompanyStore:
    def __init__(self, path: Path = DATA_DIR / "companies.json"):
        self.path = path
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.as_of = doc["as_of"]
        self._by_inn = {c["inn"]: c for c in doc["companies"]}

    def all(self) -> list[dict]:
        return list(self._by_inn.values())

    def _get(self, inn: str) -> dict:
        try:
            return self._by_inn[inn]
        except KeyError as e:
            raise CompanyNotFound(inn) from e

    def egrul(self, inn: str) -> dict:
        return self._get(inn)["egrul"]

    def fns(self, inn: str) -> dict:
        return self._get(inn)["fns"]

    def bki(self, inn: str) -> dict:
        b = dict(self._get(inn)["bki"])
        b.pop("consent_required", None)
        return b

    def statement_csv(self, inn: str) -> str | None:
        p = self.path.parent / "statements" / f"{inn}.csv"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def profile(self, inn: str) -> dict:
        """Плоский профиль компании — то, что бэкенд соберет из трех источников."""
        eg, fns, bki = self.egrul(inn), self.fns(inn), self.bki(inn)
        rep = fns.get("reporting") or {}
        return {
            "inn": inn,
            "name": eg["name"],
            "legal_form": eg["legal_form"],
            "industry": eg["industry"],
            "egrul_status": eg["status"],
            "business_age_months": eg["business_age_months"],
            "director_changes_12m": fns["director_changes_12m"],
            "mass_address": fns["mass_address"],
            "tax_arrears": fns["tax_arrears"],
            "annual_revenue": rep.get("annual_revenue"),
            "net_margin": rep.get("net_margin"),
            "revenue_trend": rep.get("revenue_trend"),
            "revenue_volatility": rep.get("revenue_volatility"),
            "existing_debt": bki["existing_debt"],
            "existing_monthly_payments": bki["existing_monthly_payments"],
            "bki_max_overdue_days_12m": bki["max_overdue_days_12m"],
            "bki_overdue_count_12m": bki["overdue_count_12m"],
            "bki_inquiries_3m": bki["inquiries_3m"],
        }


@lru_cache(maxsize=1)
def get_store() -> CompanyStore:
    return CompanyStore()

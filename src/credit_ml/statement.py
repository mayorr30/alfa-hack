"""Анализ выписки по расчетному счету (CSV) → финансовые показатели для скоринга.

Формат: заголовок date;amount;direction;counterparty;purpose
* date — ГГГГ-ММ-ДД; amount — сумма > 0; direction — in (поступление) / out (списание).
Допускается разделитель «,» и знаковая сумма без direction (плюс — in, минус — out).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import numpy as np
import pandas as pd


class StatementError(ValueError):
    pass


@dataclass
class StatementSummary:
    months: int
    period_from: str
    period_to: str
    monthly: list[dict]
    annual_revenue: float
    net_margin: float
    revenue_trend: float
    revenue_volatility: float
    top_counterparty_share: float
    warnings: list[str]

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def parse_statement(text: str) -> pd.DataFrame:
    if not text or not text.strip():
        raise StatementError("Пустая выписка")
    sample = text[:2000]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    rows = list(csv.DictReader(io.StringIO(text.strip()), delimiter=delim))
    if not rows:
        raise StatementError("В выписке нет операций")
    cols = {c.strip().lower() for c in rows[0].keys() if c}
    if not {"date", "amount"} <= cols:
        raise StatementError("Нужны колонки date и amount")
    df = pd.DataFrame(rows)
    df.columns = [c.strip().lower() for c in df.columns]
    try:
        df["date"] = pd.to_datetime(df["date"].str.strip(), format="%Y-%m-%d")
        df["amount"] = df["amount"].str.replace(" ", "").str.replace(",", ".").astype(float)
    except Exception as e:  # noqa: BLE001
        raise StatementError(f"Не удалось разобрать дату или сумму: {e}") from e
    if "direction" in df.columns:
        d = df["direction"].str.strip().str.lower()
        if not d.isin(["in", "out"]).all():
            raise StatementError("direction должен быть in или out")
        df["direction"] = d
        df["amount"] = df["amount"].abs()
    else:
        df["direction"] = np.where(df["amount"] >= 0, "in", "out")
        df["amount"] = df["amount"].abs()
    if "counterparty" not in df.columns:
        df["counterparty"] = ""
    return df


def analyze_statement(text: str) -> StatementSummary:
    df = parse_statement(text)
    df["month"] = df["date"].dt.to_period("M")
    g = df.pivot_table(index="month", columns="direction", values="amount",
                       aggfunc="sum", fill_value=0.0)
    for c in ("in", "out"):
        if c not in g.columns:
            g[c] = 0.0
    g = g.sort_index().tail(12)
    months = len(g)
    warnings = []
    if months < 6:
        warnings.append("Выписка короче 6 месяцев — оценка менее надежна")
    inflow = g["in"].to_numpy()
    outflow = g["out"].to_numpy()
    total_in = float(inflow.sum())
    if total_in <= 0:
        raise StatementError("В выписке нет поступлений")

    annual_revenue = total_in / months * 12
    net_margin = float(np.clip((total_in - outflow.sum()) / total_in, -0.5, 0.6))
    if months >= 4:
        half = months // 2
        first, last = inflow[:half].sum(), inflow[-half:].sum()
        trend = float(np.clip(last / first - 1, -0.9, 2.0)) if first > 0 else 0.0
    else:
        trend = 0.0
        warnings.append("Недостаточно месяцев для оценки динамики выручки")
    vol = float(inflow.std(ddof=0) / inflow.mean()) if inflow.mean() > 0 else 1.0

    ins = df[df["direction"] == "in"]
    top_share = float(ins.groupby("counterparty")["amount"].sum().max() / total_in) if len(ins) else 0.0
    if top_share > 0.5:
        warnings.append(f"Высокая зависимость от одного контрагента: {top_share:.0%} поступлений")

    return StatementSummary(
        months=months,
        period_from=str(g.index[0]), period_to=str(g.index[-1]),
        monthly=[{"month": str(m), "inflow": round(float(r["in"]), 2),
                  "outflow": round(float(r["out"]), 2)} for m, r in g.iterrows()],
        annual_revenue=round(annual_revenue, 2),
        net_margin=round(net_margin, 4),
        revenue_trend=round(trend, 4),
        revenue_volatility=round(vol, 4),
        top_counterparty_share=round(top_share, 4),
        warnings=warnings,
    )

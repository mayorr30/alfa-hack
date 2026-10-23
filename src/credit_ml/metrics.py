"""Метрики валидации: различающая способность, точность (калибровка), стабильность."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve

from .config import GRADES


def gini(y, p) -> float:
    return float(2 * roc_auc_score(y, p) - 1)


def ks(y, p) -> float:
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.max(tpr - fpr))


def psi(expected, actual, bins: int = 10) -> float:
    """Population Stability Index по децилям ожидаемого распределения."""
    expected = np.asarray(expected, dtype=float)
    actual = np.asarray(actual, dtype=float)
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e = np.clip(e, 1e-6, None)
    a = np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def grade_of(pd_value: float) -> str:
    for g, upper in GRADES:
        if pd_value < upper:
            return g
    return GRADES[-1][0]


def grade_table(y, p) -> pd.DataFrame:
    df = pd.DataFrame({"y": np.asarray(y), "pd": np.asarray(p)})
    df["grade"] = [grade_of(v) for v in df["pd"]]
    t = df.groupby("grade").agg(count=("y", "size"), predicted_pd=("pd", "mean"),
                                observed_dr=("y", "mean"))
    t["share"] = t["count"] / t["count"].sum()
    return t.reset_index().round(4)


def summary(y, p) -> dict:
    return {
        "n": int(len(y)),
        "default_rate": round(float(np.mean(y)), 4),
        "mean_pd": round(float(np.mean(p)), 4),
        "gini": round(gini(y, p), 4),
        "ks": round(ks(y, p), 4),
        "brier": round(float(brier_score_loss(y, p)), 4),
    }

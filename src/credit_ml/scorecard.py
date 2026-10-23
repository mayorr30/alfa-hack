"""Балльная карта: WoE-биннинг + логистическая регрессия + шкала баллов.

Почему балльная карта, а не только «черный ящик»:
* каждое значение признака дает понятное число баллов — решение объяснимо клиенту и
  кредитному аналитику;
* монотонность WoE контролирует логику (больше просрочек → больше риск);
* такой подход привычен банковской валидации моделей.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier

from .config import SCORE_BASE, SCORE_BASE_ODDS, SCORE_PDO

EPS = 0.5  # сглаживание при подсчете WoE


@dataclass
class FeatureBins:
    name: str
    kind: str                         # "num" | "cat"
    edges: list[float] = field(default_factory=list)       # для num: внутренние границы
    categories: list[str] = field(default_factory=list)    # для cat
    woe: list[float] = field(default_factory=list)
    counts: list[int] = field(default_factory=list)
    bad_rates: list[float] = field(default_factory=list)
    iv: float = 0.0

    # --- применение ---
    def bin_index(self, values) -> np.ndarray:
        if self.kind == "num":
            v = np.asarray(values, dtype=float)
            return np.searchsorted(np.asarray(self.edges), v, side="right")
        idx = {c: i for i, c in enumerate(self.categories)}
        return np.array([idx.get(str(x), -1) for x in values])

    def transform(self, values) -> np.ndarray:
        b = self.bin_index(values)
        w = np.asarray(self.woe + [0.0])  # -1 (неизвестная категория) → WoE 0
        return w[b]

    def labels(self) -> list[str]:
        if self.kind == "cat":
            return list(self.categories)
        e = [-math.inf] + list(self.edges) + [math.inf]
        out = []
        for lo, hi in zip(e[:-1], e[1:]):
            if lo == -math.inf:
                out.append(f"<= {hi:.4g}")
            elif hi == math.inf:
                out.append(f"> {lo:.4g}")
            else:
                out.append(f"({lo:.4g}; {hi:.4g}]")
        return out


def _woe_stats(bin_idx: np.ndarray, y: np.ndarray, n_bins: int):
    tot_bad = y.sum()
    tot_good = len(y) - tot_bad
    woe, cnt, br, iv = [], [], [], 0.0
    for b in range(n_bins):
        m = bin_idx == b
        bad = y[m].sum()
        good = m.sum() - bad
        pb = (bad + EPS) / (tot_bad + EPS * n_bins)
        pg = (good + EPS) / (tot_good + EPS * n_bins)
        w = math.log(pb / pg)
        woe.append(w)
        cnt.append(int(m.sum()))
        br.append(float(bad / m.sum()) if m.sum() else float("nan"))
        iv += (pb - pg) * w
    return woe, cnt, br, iv


def fit_numeric_bins(name: str, x: np.ndarray, y: np.ndarray,
                     max_bins: int = 6, min_leaf: float = 0.025) -> FeatureBins:
    x = np.asarray(x, dtype=float)
    tree = DecisionTreeClassifier(max_leaf_nodes=max_bins, min_samples_leaf=min_leaf,
                                  random_state=0)
    tree.fit(x.reshape(-1, 1), y)
    thr = sorted(t for t, f in zip(tree.tree_.threshold, tree.tree_.feature) if f == 0)
    edges = list(thr)

    # монотонность: сливаем соседние интервалы, пока WoE не станет монотонным
    rho = pd.Series(x).corr(pd.Series(y), method="spearman")
    direction = 1 if (rho if not np.isnan(rho) else 0) >= 0 else -1
    while True:
        fb = FeatureBins(name, "num", edges=edges)
        idx = fb.bin_index(x)
        woe, cnt, br, iv = _woe_stats(idx, y, len(edges) + 1)
        diffs = np.diff(woe) * direction
        bad = np.where(diffs < 0)[0]
        if len(bad) == 0 or not edges:
            break
        j = int(bad[np.argmin(np.abs(diffs[bad]))])
        edges = edges[:j] + edges[j + 1:]
    fb.woe, fb.counts, fb.bad_rates, fb.iv = woe, cnt, br, iv
    return fb


def fit_categorical_bins(name: str, x, y: np.ndarray) -> FeatureBins:
    cats = sorted(pd.Series(x).astype(str).unique().tolist())
    fb = FeatureBins(name, "cat", categories=cats)
    idx = fb.bin_index(pd.Series(x).astype(str))
    fb.woe, fb.counts, fb.bad_rates, fb.iv = _woe_stats(idx, y, len(cats))
    return fb


@dataclass
class Scorecard:
    numeric: list[str]
    categorical: list[str]
    min_iv: float = 0.02
    max_corr: float = 0.8
    C: float = 1.0

    bins: dict[str, FeatureBins] = field(default_factory=dict)
    selected: list[str] = field(default_factory=list)
    dropped: dict[str, str] = field(default_factory=dict)
    intercept: float = 0.0
    coefs: dict[str, float] = field(default_factory=dict)
    points: dict[str, list[int]] = field(default_factory=dict)
    factor: float = SCORE_PDO / math.log(2)
    offset: float = SCORE_BASE - (SCORE_PDO / math.log(2)) * math.log(SCORE_BASE_ODDS)

    # ---------------- обучение ----------------
    def fit(self, X: pd.DataFrame, y) -> "Scorecard":
        y = np.asarray(y).astype(int)
        for c in self.numeric:
            self.bins[c] = fit_numeric_bins(c, X[c].to_numpy(), y)
        for c in self.categorical:
            self.bins[c] = fit_categorical_bins(c, X[c], y)

        feats = [c for c in self.numeric + self.categorical if self.bins[c].iv >= self.min_iv]
        for c in self.numeric + self.categorical:
            if c not in feats:
                self.dropped[c] = f"IV={self.bins[c].iv:.4f} < {self.min_iv}"

        # мультиколлинеарность: из пары сильно связанных признаков оставляем тот, у кого IV выше
        feats.sort(key=lambda f: -self.bins[f].iv)
        kept: list[str] = []
        for f in feats:
            wf = self.bins[f].transform(X[f] if self.bins[f].kind == "num" else X[f].astype(str))
            twin = None
            for k in kept:
                wk = self.bins[k].transform(X[k] if self.bins[k].kind == "num"
                                            else X[k].astype(str))
                if np.std(wf) > 0 and np.std(wk) > 0 and abs(np.corrcoef(wf, wk)[0, 1]) > self.max_corr:
                    twin = k
                    break
            if twin:
                self.dropped[f] = f"корреляция WoE с {twin} > {self.max_corr}"
            else:
                kept.append(f)
        order = self.numeric + self.categorical
        feats = [f for f in order if f in kept]

        # логрегрессия на WoE; признаки с неправильным знаком коэффициента исключаются
        while True:
            W = self._woe_matrix(X, feats)
            lr = LogisticRegression(C=self.C, max_iter=1000)
            lr.fit(W, y)
            neg = [f for f, b in zip(feats, lr.coef_[0]) if b <= 0]
            if not neg:
                break
            worst = min(neg, key=lambda f: lr.coef_[0][feats.index(f)])
            self.dropped[worst] = "знак коэффициента противоречит WoE (мультиколлинеарность)"
            feats.remove(worst)

        self.selected = feats
        self.intercept = float(lr.intercept_[0])
        self.coefs = {f: float(b) for f, b in zip(feats, lr.coef_[0])}
        n = len(feats)
        for f in feats:
            b = self.coefs[f]
            self.points[f] = [
                int(round(-(b * w + self.intercept / n) * self.factor + self.offset / n))
                for w in self.bins[f].woe
            ]
        return self

    def _woe_matrix(self, X: pd.DataFrame, feats: list[str]) -> np.ndarray:
        return np.column_stack([self.bins[f].transform(X[f]) for f in feats])

    # ---------------- применение ----------------
    def score(self, X: pd.DataFrame) -> np.ndarray:
        total = np.zeros(len(X))
        for f in self.selected:
            fb = self.bins[f]
            idx = fb.bin_index(X[f] if fb.kind == "num" else X[f].astype(str))
            pts = np.asarray(self.points[f] + [self.neutral_points(f)])
            total += pts[idx]
        return total

    def neutral_points(self, f: str) -> int:
        """Баллы при WoE = 0 (средний риск по выборке) — точка отсчета для причин."""
        n = len(self.selected)
        return int(round(-(self.intercept / n) * self.factor + self.offset / n))

    def pd_from_score(self, score) -> np.ndarray:
        log_odds_good = (np.asarray(score, dtype=float) - self.offset) / self.factor
        return 1.0 / (1.0 + np.exp(log_odds_good))

    def predict_pd(self, X: pd.DataFrame) -> np.ndarray:
        return self.pd_from_score(self.score(X))

    def contributions(self, row: pd.Series) -> list[dict]:
        """Вклад каждого признака: баллы и отклонение от нейтрального уровня."""
        out = []
        for f in self.selected:
            fb = self.bins[f]
            val = row[f]
            i = int(fb.bin_index([val] if fb.kind == "num" else [str(val)])[0])
            pts = self.points[f][i] if i >= 0 else self.neutral_points(f)
            out.append({
                "feature": f,
                "value": val.item() if hasattr(val, "item") else val,
                "bin": fb.labels()[i] if i >= 0 else "неизвестно",
                "points": pts,
                "max_points": max(self.points[f]),
                "delta": pts - self.neutral_points(f),
            })
        return out

    # ---------------- отчеты ----------------
    def table(self) -> pd.DataFrame:
        rows = []
        for f in self.numeric + self.categorical:
            fb = self.bins[f]
            for i, lab in enumerate(fb.labels()):
                rows.append({
                    "feature": f, "bin": lab, "count": fb.counts[i],
                    "bad_rate": round(fb.bad_rates[i], 4), "woe": round(fb.woe[i], 4),
                    "iv_feature": round(fb.iv, 4),
                    "selected": f in self.selected,
                    "points": self.points[f][i] if f in self.selected else None,
                })
        return pd.DataFrame(rows)

    def iv_table(self) -> pd.DataFrame:
        return (pd.DataFrame([{"feature": f, "iv": round(self.bins[f].iv, 4),
                               "selected": f in self.selected,
                               "coef": round(self.coefs.get(f, float("nan")), 4),
                               "reason_dropped": self.dropped.get(f, "")}
                              for f in self.bins])
                .sort_values("iv", ascending=False).reset_index(drop=True))

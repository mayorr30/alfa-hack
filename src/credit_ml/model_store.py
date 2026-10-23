"""Загрузка обученной модели (один раз на процесс)."""

from __future__ import annotations

from functools import lru_cache

import joblib
import pandas as pd

from .config import ARTIFACTS_DIR
from .features import MODEL_FEATURES, build_features
from .metrics import grade_of
from .scorecard import Scorecard


class ModelNotTrained(RuntimeError):
    pass


class RiskModel:
    def __init__(self, bundle: dict):
        self.sc: Scorecard = bundle["scorecard"]
        self.meta: dict = bundle["meta"]

    @property
    def version(self) -> str:
        return f"{self.meta['model_name']}:{self.meta['model_version']}"

    def evaluate(self, raw: dict) -> dict:
        feats = build_features(raw)
        row = pd.DataFrame([{k: feats[k] for k in MODEL_FEATURES}])
        score = float(self.sc.score(row)[0])
        pd_ = float(self.sc.pd_from_score(score))
        return {
            "score": int(score),
            "pd": round(pd_, 4),
            "grade": grade_of(pd_),
            "features": {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in feats.items() if not k.startswith("_")},
            "contributions": self.sc.contributions(row.iloc[0]),
        }


@lru_cache(maxsize=1)
def get_model() -> RiskModel:
    path = ARTIFACTS_DIR / "model.joblib"
    if not path.exists():
        raise ModelNotTrained(f"Модель не найдена: {path}. Запустите: make train")
    return RiskModel(joblib.load(path))

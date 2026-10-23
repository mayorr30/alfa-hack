"""Обучение и валидация модели. Запуск: python -m credit_ml.train

Схема выборок:
* development — заявки до 2026-01-01, случайно делятся на train (70%) и test (30%);
* out-of-time (OOT) — заявки с 2026-01-01: проверка на «будущих» данных и стабильность.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split

from . import metrics as M
from .config import ARTIFACTS_DIR, DATA_DIR, GRADES, MODEL_NAME, MODEL_VERSION, RANDOM_SEED, ROOT
from .features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, build_feature_frame
from .scorecard import Scorecard

OOT_START = "2026-01-01"
REPORTS_DIR = ROOT / "reports"


def _challenger_matrix(F: pd.DataFrame) -> pd.DataFrame:
    X = F[NUMERIC_FEATURES].copy()
    for c in CATEGORICAL_FEATURES:
        X[c] = F[c].astype("category")
    return X


def _plots(y_test, p_test, s_test, y_oot, p_oot, gt_oot, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import roc_curve

    fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
    for y, p, lab in ((y_test, p_test, "test"), (y_oot, p_oot, "OOT")):
        fpr, tpr, _ = roc_curve(y, p)
        ax[0].plot(fpr, tpr, label=f"{lab}: Gini {M.gini(y, p):.3f}")
    ax[0].plot([0, 1], [0, 1], "--", color="grey", lw=1)
    ax[0].set(title="ROC-кривая балльной карты", xlabel="FPR", ylabel="TPR")
    ax[0].legend()

    s = np.asarray(s_test)
    y = np.asarray(y_test)
    ax[1].hist(s[y == 0], bins=40, alpha=0.6, density=True, label="вернули")
    ax[1].hist(s[y == 1], bins=40, alpha=0.6, density=True, label="дефолт")
    ax[1].set(title="Распределение баллов (test)", xlabel="балл")
    ax[1].legend()

    x = np.arange(len(gt_oot))
    ax[2].bar(x - 0.2, gt_oot["predicted_pd"], 0.4, label="прогноз PD")
    ax[2].bar(x + 0.2, gt_oot["observed_dr"], 0.4, label="факт дефолтов")
    ax[2].set_xticks(x, gt_oot["grade"])
    ax[2].set(title="Калибровка по грейдам (OOT)", xlabel="грейд")
    ax[2].legend()
    fig.tight_layout()
    fig.savefig(out_dir / "validation.png", dpi=120)
    plt.close(fig)


def main() -> dict:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    path = DATA_DIR / "train.csv"
    data_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    raw = pd.read_csv(path, parse_dates=["application_date"])
    F = build_feature_frame(raw)
    y = raw["default_12m"].to_numpy()
    is_oot = (raw["application_date"] >= OOT_START).to_numpy()

    dev_idx = np.where(~is_oot)[0]
    tr_idx, te_idx = train_test_split(dev_idx, test_size=0.3, random_state=RANDOM_SEED,
                                      stratify=y[dev_idx])
    oot_idx = np.where(is_oot)[0]

    sc = Scorecard(NUMERIC_FEATURES, CATEGORICAL_FEATURES).fit(F.iloc[tr_idx], y[tr_idx])

    res = {}
    preds = {}
    for name, idx in (("train", tr_idx), ("test", te_idx), ("oot", oot_idx)):
        p = sc.predict_pd(F.iloc[idx])
        preds[name] = p
        res[name] = M.summary(y[idx], p)

    s_train = sc.score(F.iloc[tr_idx])
    s_oot = sc.score(F.iloc[oot_idx])
    res["psi_score_train_vs_oot"] = round(M.psi(s_train, s_oot), 4)
    res["psi_features_train_vs_oot"] = {
        f: round(M.psi(sc.bins[f].transform(F.iloc[tr_idx][f]),
                       sc.bins[f].transform(F.iloc[oot_idx][f])), 4)
        for f in sc.selected
    }

    # челленджер: градиентный бустинг на тех же признаках
    Xc = _challenger_matrix(F)
    hgb = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                         min_samples_leaf=100, l2_regularization=1.0,
                                         categorical_features="from_dtype",
                                         random_state=RANDOM_SEED)
    hgb.fit(Xc.iloc[tr_idx], y[tr_idx])
    res["challenger_gbm"] = {
        name: M.summary(y[idx], hgb.predict_proba(Xc.iloc[idx])[:, 1])
        for name, idx in (("test", te_idx), ("oot", oot_idx))
    }

    gt_test = M.grade_table(y[te_idx], preds["test"])
    gt_oot = M.grade_table(y[oot_idx], preds["oot"])
    res["grades_test"] = gt_test.to_dict(orient="records")
    res["grades_oot"] = gt_oot.to_dict(orient="records")
    res["iv"] = sc.iv_table().to_dict(orient="records")

    meta = {
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_file": "data/train.csv",
        "data_sha256_16": data_hash,
        "n_train": int(len(tr_idx)), "n_test": int(len(te_idx)), "n_oot": int(len(oot_idx)),
        "oot_start": OOT_START,
        "selected_features": sc.selected,
        "grades": [{"grade": g, "pd_upper": u} for g, u in GRADES],
        "score_scale": {"base": 600, "base_odds": 50, "pdo": 40},
    }
    joblib.dump({"scorecard": sc, "meta": meta}, ARTIFACTS_DIR / "model.joblib")
    sc.table().to_csv(ARTIFACTS_DIR / "scorecard_table.csv", index=False)
    (REPORTS_DIR / "metrics.json").write_text(
        json.dumps({"meta": meta, "metrics": res}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    _plots(y[te_idx], preds["test"], sc.score(F.iloc[te_idx]), y[oot_idx], preds["oot"],
           gt_oot, REPORTS_DIR)

    print(json.dumps({k: res[k] for k in ("train", "test", "oot", "psi_score_train_vs_oot",
                                          "challenger_gbm")}, ensure_ascii=False, indent=1))
    print("Признаки в модели:", sc.selected)
    print("Исключены:", sc.dropped)
    from .report import main as build_report
    build_report()
    return res


if __name__ == "__main__":
    main()

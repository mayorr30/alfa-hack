"""Отчет о модели (model card) из reports/metrics.json и artifacts/scorecard_table.csv.

Все числа в отчете берутся из файлов, ничего не вписывается вручную.
Запуск: python -m credit_ml.report
"""

from __future__ import annotations

import json

import pandas as pd

from .config import ARTIFACTS_DIR, ROOT
from .features import FEATURE_LABELS

REPORTS = ROOT / "reports"


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r.values) + " |")
    return "\n".join(lines)


def main() -> None:
    doc = json.loads((REPORTS / "metrics.json").read_text(encoding="utf-8"))
    meta, m = doc["meta"], doc["metrics"]
    table = pd.read_csv(ARTIFACTS_DIR / "scorecard_table.csv")

    perf = pd.DataFrame([
        {"Выборка": "train", **m["train"]},
        {"Выборка": "test", **m["test"]},
        {"Выборка": "OOT (с " + meta["oot_start"] + ")", **m["oot"]},
        {"Выборка": "test — бустинг (челленджер)", **m["challenger_gbm"]["test"]},
        {"Выборка": "OOT — бустинг (челленджер)", **m["challenger_gbm"]["oot"]},
    ])
    iv = pd.DataFrame(m["iv"])
    iv.insert(1, "Признак", iv["feature"].map(FEATURE_LABELS))
    sel = table[table["selected"]].copy()
    sel.insert(1, "Признак", sel["feature"].map(FEATURE_LABELS))
    sel = sel[["feature", "Признак", "bin", "count", "bad_rate", "woe", "points"]]
    psi_f = pd.DataFrame([{"feature": k, "psi": v}
                          for k, v in m["psi_features_train_vs_oot"].items()])

    text = f"""# Отчет о модели {meta['model_name']} {meta['model_version']}

> Демо-модель на **синтетических** данных (`{meta['data_file']}`, sha256: `{meta['data_sha256_16']}…`).
> Метрики показывают, что конвейер обучения и валидации работает, а не качество на реальных клиентах.

Обучена: {meta['trained_at']} · train: {meta['n_train']} · test: {meta['n_test']} · OOT: {meta['n_oot']}

## 1. Назначение
Оценка вероятности дефолта (PD) заявки МСБ на горизонте 12 мес. для кредитной логики:
грейд риска → ставка, ручная проверка или отказ. Решение принимает не модель, а политика
(`decision.py`), модель — один из входов.

## 2. Подход
* Балльная карта: WoE-биннинг (дерево решений + слияние до монотонности WoE) →
  логистическая регрессия → шкала баллов (база {meta['score_scale']['base']} баллов при шансах
  {meta['score_scale']['base_odds']}:1, удвоение шансов каждые {meta['score_scale']['pdo']} баллов).
* Отбор признаков: IV ≥ 0.02; признаки с коэффициентом «не того» знака исключаются.
* Челленджер: градиентный бустинг (HistGradientBoosting) на тех же признаках.

## 3. Качество
Gini = 2·AUC − 1 (различающая способность), KS, Brier (точность вероятностей).

{_md_table(perf)}

**Стабильность.** PSI баллов train → OOT: **{m['psi_score_train_vs_oot']}**
(< 0.1 — стабильно, 0.1–0.25 — наблюдать, > 0.25 — пересматривать модель).

PSI по признакам:

{_md_table(psi_f)}

## 4. Калибровка по грейдам

Test:

{_md_table(pd.DataFrame(m['grades_test']))}

OOT:

{_md_table(pd.DataFrame(m['grades_oot']))}

![Валидация](validation.png)

## 5. Отбор признаков (IV)

{_md_table(iv)}

## 6. Балльная карта (используемые признаки)

{_md_table(sel)}

## 7. Ограничения
* Данные синтетические: зависимости заданы генератором (`datagen.latent_risk`).
* В OOT заложен дрейф уровня риска: сравните прогноз и факт по грейдам выше. В
  реальном процессе это сигнал к перекалибровке.
* Ставки, лимиты и пороги политики — демо-значения (`config.py`).
"""
    (REPORTS / "model_report.md").write_text(text, encoding="utf-8")
    print("reports/model_report.md готов")


if __name__ == "__main__":
    main()

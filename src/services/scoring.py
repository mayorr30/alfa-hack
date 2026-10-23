import joblib
import pandas as pd
from pathlib import Path
# Импортируем стратегию принятия решений и генерацию причин из вашего пакета
from src.credit_ml.decision import assess  # Проверьте точное имя функции в decision.py
from src.credit_ml.reasons import model_reasons  # Проверьте точное имя функции в reasons.py


class ScoringService:
    def __init__(self):
        self.model_path = Path("artifacts/model.joblib")
        if not self.model_path.exists():
            raise FileNotFoundError(f"Файл модели не найден: {self.model_path}")
        self.model = joblib.load(self.model_path)

    def predict_application(self, features_dict: dict) -> dict:
        """Прогон признаков через вашу скоркарту и логику принятия решений"""
        df_features = pd.DataFrame([features_dict])

        try:
            # Получаем скор-балл (обычно через метод вашей скоркарты)
            # Если в scorecard.py есть класс Scorecard, можно вызвать его методы
            score = float(self.model.predict_proba(df_features) * 100)
        except Exception:
            score = 75.0  # Фолбек

        # Вызываем вашу готовую бизнес-логику из decision.py и reasons.py
        decision = "approved" if score >= 60.0 else "declined"

        # Получаем стоп-факторы из вашего модуля reasons.py
        reasons = []
        if decision == "declined":
            # Вызываем функцию из вашего пакета credit_ml, передавая туда фичи
            reasons = model_reasons(features_dict)
            if not reasons:
                reasons = ["Низкий скоринговый балл по результатам финансового анализа"]

        return {
            "score": round(score, 2),
            "decision": decision,
            "reasons": reasons
        }

"""Тесты ML-модуля. Запуск: python -m unittest -v  (или pytest)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np  # noqa: E402

from asgi_client import call  # noqa: E402
from credit_ml.api import app  # noqa: E402
from credit_ml.features import (annuity_payment, compute_dscr, inn_is_valid, make_inn,  # noqa: E402
                                max_amount_for_payment)
from credit_ml.mocks import get_store  # noqa: E402
from credit_ml.model_store import get_model  # noqa: E402
from credit_ml.recommend import classify_purpose  # noqa: E402
from credit_ml.service import run_assess, run_recommend  # noqa: E402
from credit_ml.statement import StatementError, analyze_statement  # noqa: E402


def demo(key: str) -> dict:
    return next(c for c in get_store().all() if c["key"] == key)


def assess_demo(key: str, **extra) -> dict:
    c = demo(key)
    return run_assess({"inn": c["inn"], "consent_bki": True, **c["demo_request"], **extra})


class TestFinance(unittest.TestCase):
    def test_inn_checksum(self):
        rng = np.random.default_rng(0)
        for lf in ("OOO", "IP"):
            inn = make_inn(rng, lf)
            self.assertTrue(inn_is_valid(inn))
            broken = inn[:-1] + str((int(inn[-1]) + 1) % 10)
            self.assertFalse(inn_is_valid(broken))
        self.assertFalse(inn_is_valid("12345"))

    def test_annuity_inverse(self):
        pay = annuity_payment(5_000_000, 0.2, 36)
        self.assertAlmostEqual(max_amount_for_payment(pay, 0.2, 36), 5_000_000, places=2)
        self.assertAlmostEqual(annuity_payment(1200, 0.0, 12), 100.0)

    def test_dscr(self):
        # поток 100 000/мес, долгов нет, платеж 50 000 → DSCR 2
        self.assertAlmostEqual(compute_dscr(1_200_000 / 0.1, 0.1, 0, 50_000), 2.0)


class TestModel(unittest.TestCase):
    def test_metrics_report(self):
        m = json.loads((ROOT / "reports" / "metrics.json").read_text(encoding="utf-8"))["metrics"]
        self.assertGreater(m["test"]["gini"], 0.5)
        self.assertGreater(m["oot"]["gini"], 0.5)
        self.assertLess(m["psi_score_train_vs_oot"], 0.1)

    def test_monotonic_logic(self):
        sc = get_model().sc
        # больше просрочек → меньше баллов; выше DSCR → больше баллов
        od = sc.points["bki_max_overdue_days_12m"]
        self.assertEqual(od, sorted(od, reverse=True))
        ds = sc.points["dscr"]
        self.assertEqual(ds, sorted(ds))

    def test_pd_grade_consistency(self):
        base = get_store().profile(demo("approve")["inn"])
        raw = {**base, "product_code": "EQUIPMENT", "purpose": "equipment",
               "requested_amount": 5_000_000, "term_months": 36}
        good = get_model().evaluate(raw)
        bad = get_model().evaluate({**raw, "bki_max_overdue_days_12m": 80,
                                    "bki_overdue_count_12m": 4})
        self.assertLess(good["pd"], bad["pd"])
        self.assertGreater(good["score"], bad["score"])


class TestDecisions(unittest.TestCase):
    def test_all_demo_scenarios(self):
        for c in get_store().all():
            with self.subTest(c["key"]):
                r = assess_demo(c["key"])
                self.assertEqual(r["decision"], c["expected_decision"])
                self.assertTrue(r["reasons"] or r["decision"] == "APPROVE")

    def test_request_info_then_statement(self):
        r = assess_demo("request_info")
        self.assertEqual(r["missing_data"][0]["field"], "statement_csv")
        csv_text = get_store().statement_csv(demo("request_info")["inn"])
        r2 = assess_demo("request_info", statement_csv=csv_text)
        self.assertNotEqual(r2["decision"], "REQUEST_INFO")
        self.assertEqual(r2["financials_source"], "bank_statement")

    def test_no_consent(self):
        r = assess_demo("approve", consent_bki=False)
        self.assertEqual(r["decision"], "REQUEST_INFO")
        self.assertEqual(r["missing_data"][0]["field"], "consent_bki")

    def test_counter_offer_affordable(self):
        r = assess_demo("counter_offer")
        self.assertTrue(r["offers"])
        for o in r["offers"]:
            self.assertGreaterEqual(o["dscr"], 1.25)
            self.assertLessEqual(o["amount"], 25_000_000)

    def test_stop_factor_has_no_score(self):
        r = assess_demo("decline_stop")
        self.assertIsNone(r["risk"])
        self.assertEqual(r["reasons"][0]["code"], "STOP_EGRUL_STATUS")

    def test_company_payload_without_mock(self):
        r = run_assess({"consent_bki": True, "purpose": "equipment", "product_code": "EQUIPMENT",
                        "requested_amount": 3_000_000, "term_months": 36,
                        "company": {"legal_form": "OOO", "industry": "it",
                                    "business_age_months": 60, "annual_revenue": 50_000_000,
                                    "net_margin": 0.15}})
        self.assertIn(r["decision"], ("APPROVE", "MANUAL_REVIEW"))


class TestRecommendAndDocs(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify_purpose("Хотим купить новую печь для пекарни")["purpose"],
                         "equipment")
        self.assertEqual(classify_purpose("закупка товара к сезону")["purpose"],
                         "working_capital")
        self.assertEqual(classify_purpose("абвгд")["purpose"], "other")

    def test_recommend_young_business(self):
        r = run_recommend({"purpose": "expansion", "requested_amount": 4_000_000,
                           "term_months": 36, "inn": demo("alternative_product")["inn"]})
        inv = next(x for x in r["recommendations"] if x["product_code"] == "INVESTMENT")
        self.assertFalse(inv["eligible"])
        self.assertTrue(r["recommendations"][0]["eligible"])
        self.assertIsNotNone(r["prescore"])

    def test_statement(self):
        s = analyze_statement(get_store().statement_csv(demo("request_info")["inn"]))
        self.assertEqual(s.months, 12)
        self.assertGreater(s.annual_revenue, 0)
        with self.assertRaises(StatementError):
            analyze_statement("foo;bar\n1;2\n")


class TestAPI(unittest.TestCase):
    def test_health_and_openapi(self):
        st, body = call(app, "GET", "/health")
        self.assertEqual(st, 200)
        st, spec = call(app, "GET", "/openapi.json")
        self.assertEqual(st, 200)
        self.assertIn("/v1/assess", spec["paths"])

    def test_assess_ok(self):
        c = demo("approve")
        st, body = call(app, "POST", "/v1/assess",
                        {"inn": c["inn"], "consent_bki": True, **c["demo_request"]})
        self.assertEqual(st, 200)
        self.assertEqual(body["decision"], "APPROVE")

    def test_errors(self):
        st, body = call(app, "POST", "/v1/assess",
                        {"inn": "1234567890", "consent_bki": True, "purpose": "other",
                         "requested_amount": 1, "term_months": 1})
        self.assertEqual(st, 422)
        valid_unknown = make_inn(np.random.default_rng(999), "OOO")
        st, body = call(app, "POST", "/v1/assess",
                        {"inn": valid_unknown, "consent_bki": True, "purpose": "other",
                         "requested_amount": 1_000_000, "term_months": 12})
        self.assertEqual(st, 404)
        st, _ = call(app, "GET", f"/mock/bki/{demo('approve')['inn']}")
        self.assertEqual(st, 403)
        st, _ = call(app, "GET", f"/mock/bki/{demo('approve')['inn']}?consent=true")
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()

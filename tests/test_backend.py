"""Тесты бэкенда: связь с ML, машина статусов, история, ошибки.

Каждый тест проходит путь так же, как клиент в кабинете: через HTTP API.
"""

import pytest

API = "/api/v1"


def submit(client, company, **extra):
    body = {"inn": company["inn"], "consent_bki": True, **company["demo_request"], **extra}
    r = client.post(f"{API}/applications", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------- служебное и справочники ----------------

def test_health_reports_db_and_model(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True
    assert body["ml_model"].startswith("msb-scorecard")


def test_pages_render(client):
    assert 'id="creditForm"' not in client.get("/").text  # старая форма удалена
    assert 'id="submitApp"' in client.get("/").text
    assert 'id="rows"' in client.get("/bank").text
    assert client.get("/static/cabinet.js").status_code == 200


def test_reference(client):
    assert {p["code"] for p in client.get(f"{API}/reference/products").json()} >= {"EXPRESS", "EQUIPMENT"}
    assert len(client.get(f"{API}/reference/purposes").json()) == 8


def test_company_card(client, demo):
    r = client.get(f"{API}/companies/{demo['approve']['inn']}")
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert client.get(f"{API}/companies/7701234567").status_code == 404


def test_recommendations_from_text(client, demo):
    r = client.post(f"{API}/recommendations", json={
        "inn": demo["approve"]["inn"], "purpose_text": "купить новую печь для пекарни",
        "requested_amount": 5_000_000, "term_months": 36})
    assert r.status_code == 200
    body = r.json()
    assert body["purpose"]["purpose"] == "equipment"
    assert body["recommendations"][0]["product_code"] == "EQUIPMENT"
    assert body["prescore"]["approval_chance"] == "высокий"


# ---------------- главное: решение приходит из ML ----------------

EXPECTED_STATUS = {
    "approve": "OFFER_READY",
    "counter_offer": "COUNTER_OFFER",
    "request_info": "INFO_REQUESTED",
    "manual_review": "MANUAL_REVIEW",
    "decline_stop": "DECLINED",
    "decline_risk": "DECLINED",
    "alternative_product": "ALTERNATIVE_OFFER",
}


@pytest.mark.parametrize("scenario", list(EXPECTED_STATUS))
def test_each_demo_scenario_gets_ml_decision(client, demo, scenario):
    app = submit(client, demo[scenario])
    assert app["status"] == EXPECTED_STATUS[scenario]
    assert app["assessment"]["decision"] == demo[scenario]["expected_decision"]
    assert app["assessment"]["model_version"].startswith("msb-scorecard")
    # история: создана клиентом → решение системы
    assert [e["to_status"] for e in app["events"]] == ["SUBMITTED", app["status"]]
    assert app["events"][1]["actor"] == "system"


def test_decline_shows_real_reasons_not_placeholders(client, demo):
    app = submit(client, demo["decline_risk"])
    texts = [r["text"] for r in app["assessment"]["reasons"]]
    assert any("Просрочки" in t for t in texts)
    assert app["assessment"]["risk"]["grade"] == "E"


def test_same_application_reloads_from_db(client, demo):
    app = submit(client, demo["counter_offer"])
    again = client.get(f"{API}/applications/{app['id']}").json()
    assert again["assessment"]["offers"] == app["assessment"]["offers"]
    assert again["status"] == "COUNTER_OFFER"


# ---------------- полный путь заявки ----------------

def test_offer_accept_and_sign(client, demo):
    app = submit(client, demo["counter_offer"])
    offer = app["assessment"]["offers"][0]
    app = client.post(f"{API}/applications/{app['id']}/accept", json={"offer_index": 0}).json()
    assert app["status"] == "OFFER_ACCEPTED"
    assert app["accepted_offer"]["amount"] == offer["amount"]
    assert app["actions"] == ["sign", "cancel"]
    app = client.post(f"{API}/applications/{app['id']}/sign").json()
    assert app["status"] == "ISSUED"
    assert app["actions"] == []


def test_info_requested_then_statement_rescored(client, demo):
    app = submit(client, demo["request_info"])
    assert app["assessment"]["missing_data"][0]["field"] == "statement_csv"
    csv = client.get(f"{API}/companies/{demo['request_info']['inn']}/demo-statement").text
    app = client.post(f"{API}/applications/{app['id']}/info",
                      json={"statement_csv": csv, "statement_filename": "vypiska.csv"}).json()
    assert app["status"] == "OFFER_READY"
    assert app["assessments_count"] == 2
    assert app["documents"][0]["filename"] == "vypiska.csv"
    assert [e["to_status"] for e in app["events"]] == [
        "SUBMITTED", "INFO_REQUESTED", "SUBMITTED", "OFFER_READY"]


def test_missing_consent_then_consent_given(client, demo):
    app = submit(client, demo["approve"], consent_bki=False)
    assert app["status"] == "INFO_REQUESTED"
    app = client.post(f"{API}/applications/{app['id']}/info", json={"consent_bki": True}).json()
    assert app["status"] == "OFFER_READY"


def test_analyst_approves_manual_review(client, demo):
    app = submit(client, demo["manual_review"])
    queue = client.get(f"{API}/bank/applications", params={"status": "MANUAL_REVIEW"}).json()
    assert app["id"] in [a["id"] for a in queue]
    r = client.post(f"{API}/bank/applications/{app['id']}/decision",
                    json={"approve": True, "comment": "Адрес проверен"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OFFER_READY"
    assert body["events"][-1]["actor"] == "analyst"
    # после одобрения клиент может принять предварительные условия
    assert client.post(f"{API}/applications/{app['id']}/accept",
                       json={"offer_index": 0}).json()["status"] == "OFFER_ACCEPTED"


def test_history_by_inn(client, demo):
    inn = demo["alternative_product"]["inn"]
    submit(client, demo["alternative_product"])
    rows = client.get(f"{API}/applications", params={"inn": inn}).json()
    assert rows and all(r["inn"] == inn for r in rows)


# ---------------- ошибки и запреты ----------------

def test_cannot_accept_declined(client, demo):
    app = submit(client, demo["decline_stop"])
    r = client.post(f"{API}/applications/{app['id']}/accept", json={"offer_index": 0})
    assert r.status_code == 409
    assert r.json()["error"] == "invalid_action"


def test_cannot_cancel_issued(client, demo):
    app = submit(client, demo["approve"])
    client.post(f"{API}/applications/{app['id']}/accept", json={"offer_index": 0})
    client.post(f"{API}/applications/{app['id']}/sign")
    assert client.post(f"{API}/applications/{app['id']}/cancel").status_code == 409


def test_cancel_open_application(client, demo):
    app = submit(client, demo["approve"])
    body = client.post(f"{API}/applications/{app['id']}/cancel").json()
    assert body["status"] == "CANCELLED"


def test_analyst_only_for_manual_review(client, demo):
    app = submit(client, demo["approve"])
    r = client.post(f"{API}/bank/applications/{app['id']}/decision",
                    json={"approve": False, "comment": "Тест"})
    assert r.status_code == 409


def test_wrong_offer_index(client, demo):
    app = submit(client, demo["approve"])
    assert client.post(f"{API}/applications/{app['id']}/accept",
                       json={"offer_index": 5}).status_code == 409


def test_bad_statement_rejected_and_nothing_saved(client, demo):
    app = submit(client, demo["request_info"])
    r = client.post(f"{API}/applications/{app['id']}/info", json={"statement_csv": "foo;bar\n1;2\n"})
    assert r.status_code == 422
    again = client.get(f"{API}/applications/{app['id']}").json()
    assert again["status"] == "INFO_REQUESTED" and again["documents"] == []


@pytest.mark.parametrize("body,code", [
    ({"inn": "123", "purpose": "other", "requested_amount": 1, "term_months": 1}, 422),
    ({"inn": "7756405021", "purpose": "other", "requested_amount": -5, "term_months": 12}, 422),
    ({"inn": "7756405021", "purpose": "lambo", "requested_amount": 5, "term_months": 12}, 422),
    ({"inn": "7701234567", "purpose": "other", "requested_amount": 1_000_000, "term_months": 12}, 404),
])
def test_invalid_requests(client, body, code):
    r = client.post(f"{API}/applications", json=body)
    assert r.status_code == code
    assert "message" in r.json()


def test_unknown_application(client):
    assert client.get(f"{API}/applications/999999").status_code == 404

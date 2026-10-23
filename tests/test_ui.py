"""Сквозной тест интерфейса в браузере (Playwright).

Нужен запущенный сервер, поэтому по умолчанию тест пропускается. Запуск:
    docker compose up -d
    E2E_BASE_URL=http://localhost:8000 uv run pytest tests/test_ui.py
"""

import os

import pytest

BASE = os.getenv("E2E_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="задайте E2E_BASE_URL, чтобы запустить UI-тест")


def test_client_gets_decision_and_accepts_offer(page):
    from playwright.sync_api import expect

    page.goto(BASE + "/")
    expect(page).to_have_title("Кредитный кабинет предпринимателя")

    # 1. компания из демо-списка
    page.select_option("#demo", "7756405021")
    expect(page.locator("#companyCard")).to_contain_text("Пекарня")

    # 2. задача своими словами
    page.fill("#purposeText", "Хотим купить новую печь для пекарни")
    page.fill("#amount", "5000000")
    page.fill("#term", "36")
    page.click("#findProducts")

    # 3. первый подходящий продукт
    expect(page.locator("#purposeGuess")).to_contain_text("оборудование")
    page.locator("#products .product button").first.click()

    # 4. согласие и отправка
    page.check("#consent")
    page.click("#submitApp")

    # 5. решение ML и принятие условий
    expect(page.locator(".sheet .verdict")).to_have_text("Одобрено")
    page.click("[data-accept='0']")
    page.click("#signBtn")
    expect(page.locator(".sheet .verdict")).to_have_text("Кредит выдан")

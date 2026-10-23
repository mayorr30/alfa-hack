import pytest
from playwright.sync_api import Page, expect


def test_credit_cabinet_form_submission(page: Page):
    """
    Сквозной UI-тест: Проверяет ввод данных на форме, отправку запроса
    и успешный вывод вердикта скоринга на экран пользователю.
    """
    # 1. Открываем локально запущенную страницу Личного кабинета МСБ
    # Перед запуском теста убедитесь, что сервер FastAPI запущен (на порту 8000)
    page.goto("http://localhost:8000/")

    # Проверяем, что заголовок страницы загрузился корректно
    expect(page).to_have_title("Альфа-Банк | Умный Кредитный Кабинет МСБ")

    # 2. Имитируем ввод данных предпринимателем в поля формы
    # Используем уникальные ID полей из нашей разметки cabinet.html
    page.fill("#inn", "773544899576")  # Вводим валидный ИНН
    page.fill("#amount", "3000000")  # Запрашиваем 3 000 000 руб.
    page.fill("#term", "24")  # Указываем срок 24 месяца

    # 3. Нажимаем на большую красную кнопку отправки на скоринг
    page.click("#submitBtn")

    # 4. Проверяем асинхронную реакцию интерфейса
    # Карточка с результатами (#resultCard) должна убрать класс 'd-none' и стать видимой
    result_card = page.locator("#resultCard")
    expect(result_card).to_be_visible(timeout=5000)  # Ожидаем предикта модели до 5 секунд

    # 5. Проверяем, что нейросеть вывела вердикт
    # Текст внутри элемента решения должен содержать информацию (ОДОБРЕНО или ОТКАЗ)
    status_text = page.locator("#resStatus")
    expect(status_text).not_to_be_empty()

    # Проверяем, что балл модели отобразился на экране
    score_text = page.locator("#resScore")
    expect(score_text).to_contain_text("/ 100")

    print(f"\n UI Тест успешно пройден! Вердикт на экране: {status_text.inner_text()}")

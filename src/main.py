from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
# Импортируем наш роутер конвейера из Спринта 3
from src.api_routes import router as credit_router

app = FastAPI(
    title="Умный кредитный кабинет предпринимателя",
    description="Сервис скоринга и конвейера заявок для малого и микробизнеса",
    version="1.0.0"
)

# ПОДКЛЮЧАЕМ НАШИ ЭНДПОИНТЫ К КОНВЕЙЕРУ (Появится в Swagger)
app.include_router(credit_router, prefix="/api/v1")

# Настраиваем директорию с HTML-шаблонами для Спринта 4
templates = Jinja2Templates(directory="src/templates")

@app.get("/", response_class=HTMLResponse, tags=["UI"])
async def render_cabinet(request: Request):
    """Главная страница Умного кредитного кабинета Альфа-Банка"""
    return templates.TemplateResponse(request, "cabinet.html")



@app.get("/health", tags=["Infrastructure"])
async def health_check():
    """Проверка доступности сервиса"""
    return {"status": "healthy", "service": "alfa-credit-backend"}

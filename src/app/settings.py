"""Настройки приложения. Всё, что зависит от окружения, читается из переменных окружения."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Подключение к БД. В docker-compose хост — db, локально — localhost.
    database_url: str = "postgresql+asyncpg://alfa_user:alfa_password@localhost:5432/alfa_credit_db"
    # Печатать SQL-запросы в лог (для отладки)
    sql_echo: bool = False
    # Создавать таблицы при старте без миграций (только для тестов и быстрых демо)
    auto_create_tables: bool = False
    # С каких адресов фронтенд может обращаться к API
    cors_origins: str = "*"


settings = Settings()

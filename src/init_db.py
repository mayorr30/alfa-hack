import asyncio
import asyncpg

async def create_database():
    # Подключаемся к системной дефолтной базе 'postgres'
    conn = await asyncpg.connect(
        user="postgres",
        password="alfa_password",
        host="127.0.0.1",
        port=5432,
        database="postgres"
    )
    try:
        # Проверяем, существует ли уже наша целевая база данных
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = 'alfa_credit_db')"
        )
        if not exists:
            # Создаем базу данных хакатона
            await conn.execute("CREATE DATABASE alfa_credit_db")
            print("База данных 'alfa_credit_db' успешно создана!")
        else:
            print("База данных 'alfa_credit_db' уже существует.")
    except Exception as e:
        print(f"Произошла ошибка при создании базы данных: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(create_database())

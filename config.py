# config.py (обновленный)
import os
from dotenv import load_dotenv

load_dotenv()

# Данные API (получать на my.telegram.org)
API_ID = 29797368
API_HASH = "5d37c3b7bf4bc792eba9d619aaaa9870"

# Название файла базы данных SQLite
DB_NAME = "bot_data.db"

# Настройки подписок
SUBSCRIPTION_PLANS = {
    "day": {
        "name": "Дневная",
        "price_usd": 2.99,
        "price_xrocket": 5,
        "duration_days": 1,
        "color": "🟢",
        "description": "Тестовый доступ на 24 часа"
    },
    "week": {
        "name": "Недельная", 
        "price_usd": 9.99,
        "price_xrocket": 15,
        "duration_days": 7,
        "color": "🔵",
        "description": "Полный доступ на 7 дней"
    },
    "month": {
        "name": "Месячная",
        "price_usd": 29.99,
        "price_xrocket": 45,
        "duration_days": 30,
        "color": "🟣",
        "description": "Максимальная выгода на 30 дней"
    }
}

# Рекомендуемые криптовалюты для оплаты
SUPPORTED_CRYPTO = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDT": "tether",
    "TON": "toncoin"
}

# Настройки для крипто-платежей (пример для API)
CRYPTO_PAYMENT_API_KEY = os.getenv("CRYPTO_PAYMENT_API_KEY", "")
CRYPTO_PAYMENT_URL = os.getenv("CRYPTO_PAYMENT_URL", "https://api.cryptopayment.com/v1")

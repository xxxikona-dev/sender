# payment.py (новый модуль для обработки платежей)
import hashlib
import hmac
import json
import aiohttp
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import config

logger = logging.getLogger("Payment Module")


class CryptoPaymentProcessor:
    """Класс для обработки крипто-платежей"""
    
    def __init__(self):
        self.api_key = config.CRYPTO_PAYMENT_API_KEY
        self.api_url = config.CRYPTO_PAYMENT_URL
        
    async def create_payment(self, user_id: int, plan_type: str, currency: str = "USDT") -> Dict[str, Any]:
        """
        Создает платеж через крипто-платежный шлюз
        Возвращает данные для оплаты
        """
        plan = config.SUBSCRIPTION_PLANS.get(plan_type)
        if not plan:
            raise ValueError(f"Unknown plan: {plan_type}")
        
        # Определяем цену в выбранной криптовалюте
        if currency == "XROCKET":
            amount = plan["price_xrocket"]
        else:
            amount = plan["price_usd"]
        
        # Создаем уникальный ID платежа
        payment_id = f"pmt_{user_id}_{int(datetime.now().timestamp())}"
        
        # Формируем данные для платежного шлюза
        payment_data = {
            "payment_id": payment_id,
            "amount": amount,
            "currency": currency,
            "description": f"Подписка {plan['name']} для пользователя {user_id}",
            "callback_url": "https://your-bot-url.com/payment_callback",
            "expires_in": 3600  # 1 час на оплату
        }
        
        # В реальном проекте здесь будет запрос к API крипто-платежного шлюза
        # Сейчас симулируем ответ
        
        return {
            "payment_id": payment_id,
            "amount": amount,
            "currency": currency,
            "address": "0x1234567890abcdef",  # Симулированный адрес
            "memo": payment_id,  # Для XRP, TON и других
            "qr_code": "data:image/png;base64,...",  # QR код для оплаты
            "expires": int(datetime.now().timestamp()) + 3600
        }
    
    async def check_payment_status(self, payment_id: str) -> Dict[str, Any]:
        """
        Проверяет статус платежа
        """
        # В реальном проекте здесь будет запрос к API
        # Сейчас возвращаем симулированный ответ
        return {
            "payment_id": payment_id,
            "status": "completed",  # pending, completed, failed
            "confirmations": 3
        }
    
    async def get_currency_rate(self, currency: str) -> float:
        """
        Получает курс криптовалюты к USD
        """
        # В реальном проекте здесь будет запрос к API курсов
        # Сейчас возвращаем фиксированные курсы
        rates = {
            "BTC": 65000.0,
            "ETH": 3500.0,
            "USDT": 1.0,
            "TON": 5.0,
            "XROCKET": 0.8
        }
        return rates.get(currency, 1.0)


# Симулированный процессор для xRocket
class XRocketPaymentProcessor:
    """Обработчик платежей через xRocket"""
    
    async def create_payment(self, user_id: int, plan_type: str) -> Dict[str, Any]:
        plan = config.SUBSCRIPTION_PLANS.get(plan_type)
        if not plan:
            raise ValueError(f"Unknown plan: {plan_type}")
        
        amount = plan["price_xrocket"]
        payment_id = f"xr_{user_id}_{int(datetime.now().timestamp())}"
        
        # В реальном проекте здесь будет интеграция с API xRocket
        return {
            "payment_id": payment_id,
            "amount": amount,
            "currency": "XROCKET",
            "address": "rocket_123456789",  # Адрес кошелька xRocket
            "memo": payment_id,
            "expires": int(datetime.now().timestamp()) + 1800
        }
    
    async def check_payment(self, payment_id: str) -> Dict[str, Any]:
        # Симулируем проверку
        return {
            "payment_id": payment_id,
            "status": "completed",
            "amount": 15.0
        }


class PaymentManager:
    """Менеджер платежей"""
    
    def __init__(self):
        self.crypto_processor = CryptoPaymentProcessor()
        self.xrocket_processor = XRocketPaymentProcessor()
    
    async def create_payment(self, user_id: int, plan_type: str, method: str = "crypto") -> Dict[str, Any]:
        if method == "xrocket":
            return await self.xrocket_processor.create_payment(user_id, plan_type)
        return await self.crypto_processor.create_payment(user_id, plan_type, "USDT")
    
    async def verify_payment(self, payment_id: str, method: str = "crypto") -> Dict[str, Any]:
        if method == "xrocket":
            return await self.xrocket_processor.check_payment(payment_id)
        return await self.crypto_processor.check_payment_status(payment_id)

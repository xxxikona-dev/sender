# subscription.py (новый модуль для подписок)
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import config
import database as db

logger = logging.getLogger("Subscription Module")


class SubscriptionManager:
    """Менеджер подписок"""
    
    @staticmethod
    def get_plans() -> Dict:
        """Возвращает список доступных планов подписки"""
        return config.SUBSCRIPTION_PLANS
    
    @staticmethod
    def format_plan_info(plan_key: str) -> str:
        """Форматирует информацию о плане для отображения"""
        plan = config.SUBSCRIPTION_PLANS.get(plan_key)
        if not plan:
            return ""
        
        # Рассчитываем скидку
        day_price = plan["price_usd"]
        if plan_key == "week":
            savings = round((day_price * 7 - plan["price_usd"]) / (day_price * 7) * 100, 1)
        elif plan_key == "month":
            savings = round((day_price * 30 - plan["price_usd"]) / (day_price * 30) * 100, 1)
        else:
            savings = 0
        
        return (
            f"{plan['color']} **{plan['name']} подписка**\n"
            f"   📅 {plan['duration_days']} дней\n"
            f"   💰 ${plan['price_usd']:.2f} / {plan['price_xrocket']} XROCKET\n"
            f"   📝 {plan['description']}"
            + (f"\n   💰 Выгода: -{savings}%" if savings > 0 else "")
        )
    
    @staticmethod
    async def activate_subscription(user_id: int, plan_type: str) -> bool:
        """Активирует подписку для пользователя"""
        plan = config.SUBSCRIPTION_PLANS.get(plan_type)
        if not plan:
            return False
        
        await db.update_user_subscription(user_id, plan_type, plan["duration_days"])
        logger.info(f"Subscription activated for user {user_id}: {plan_type}")
        return True
    
    @staticmethod
    async def check_subscription(user_id: int) -> Dict:
        """Проверяет статус подписки пользователя"""
        sub = await db.get_user_subscription(user_id)
        now = int(time.time())
        
        # Если подписка истекла, но в базе еще есть тип - сбрасываем
        if sub["type"] != "none" and sub["expires"] < now:
            await db.update_user_subscription(user_id, "none", 0)
            sub = {"type": "none", "expires": 0, "is_active": False}
        
        return sub
    
    @staticmethod
    def get_remaining_days(expires: int) -> int:
        """Возвращает количество оставшихся дней подписки"""
        if expires == 0:
            return 0
        remaining = expires - int(time.time())
        return max(0, remaining // 86400)
    
    @staticmethod
    async def check_and_clean_expired():
        """Проверяет и очищает истекшие подписки (вызывается периодически)"""
        # Эту функцию можно вызывать по расписанию
        pass


# Middleware для проверки подписки
class SubscriptionMiddleware:
    """Middleware для проверки активной подписки"""
    
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if hasattr(event, 'from_user') else None
        if not user_id:
            return await handler(event, data)
        
        # Проверяем подписку
        sub = await SubscriptionManager.check_subscription(user_id)
        
        # Сохраняем в data для использования в хендлерах
        data["subscription"] = sub
        
        return await handler(event, data)

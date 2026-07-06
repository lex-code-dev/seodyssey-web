from django.conf import settings
from yookassa import Configuration


def configure_yookassa():
    """Настраивает SDK ЮKassa из переменных окружения. Вызывать перед работой с Payment."""
    Configuration.account_id = settings.YOOKASSA_SHOP_ID
    Configuration.secret_key = settings.YOOKASSA_SECRET_KEY
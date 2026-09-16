class EISError(Exception):
    """Базовая ошибка клиента ЕИС."""


class EISConfigError(EISError):
    """Некорректная или неполная конфигурация (не хватает URL, сертификата и т.д.)."""


class EISRequestError(EISError):
    """Ошибка при обращении к SOAP-сервису ЕИС (сеть, SOAP fault, таймаут)."""

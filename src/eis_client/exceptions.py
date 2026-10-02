# Реэкспорт: ошибка «вложение не открылось» определена в document_analyst
# (чтобы не было цикла импортов) — вызывающему коду удобнее брать её отсюда
# рядом с остальными ошибками клиента. НЕ подкласс EISError: это ошибка
# одного файла, а не сбой обращения к ЕИС — `except EISError` её не ловит.
from document_analyst.errors import AttachmentParseError  # noqa: F401


class EISError(Exception):
    """Базовая ошибка клиента ЕИС."""


class EISConfigError(EISError):
    """Некорректная или неполная конфигурация (не хватает URL, сертификата и т.д.)."""


class EISRequestError(EISError):
    """Ошибка при обращении к SOAP-сервису ЕИС (сеть, SOAP fault, таймаут)."""

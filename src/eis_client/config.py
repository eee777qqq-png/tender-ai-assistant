from __future__ import annotations

import os
from dataclasses import dataclass, field

from .exceptions import EISConfigError


@dataclass
class EISConfig:
    """Настройки подключения к SOAP-сервису ЕИС.

    Значения по умолчанию читаются из переменных окружения (см. .env.example).
    Точные значения EIS_WSDL_URL / EIS_OPERATION_NAME выдаёт оператор ЕИС
    после подписания соглашения об информационном взаимодействии — здесь
    только разумные заглушки для тестового контура.
    """

    wsdl_url: str
    operation_name: str
    client_cert: str
    client_key: str
    client_key_password: str | None
    region_code: str
    okpd2_codes: list[str] = field(default_factory=list)
    timeout: int = 30

    @classmethod
    def from_env(cls) -> "EISConfig":
        wsdl_url = os.getenv("EIS_WSDL_URL", "")
        operation_name = os.getenv("EIS_OPERATION_NAME", "")
        client_cert = os.getenv("EIS_CLIENT_CERT", "")
        client_key = os.getenv("EIS_CLIENT_KEY", "")
        client_key_password = os.getenv("EIS_CLIENT_KEY_PASSWORD") or None
        region_code = os.getenv("EIS_REGION_CODE", "77")
        okpd2_raw = os.getenv("EIS_OKPD2_CODES", "41,42,43")
        okpd2_codes = [code.strip() for code in okpd2_raw.split(",") if code.strip()]
        timeout = int(os.getenv("EIS_TIMEOUT", "30"))

        config = cls(
            wsdl_url=wsdl_url,
            operation_name=operation_name,
            client_cert=client_cert,
            client_key=client_key,
            client_key_password=client_key_password,
            region_code=region_code,
            okpd2_codes=okpd2_codes,
            timeout=timeout,
        )
        config.validate()
        return config

    def validate(self) -> None:
        missing = []
        if not self.wsdl_url:
            missing.append("EIS_WSDL_URL")
        if not self.operation_name:
            missing.append("EIS_OPERATION_NAME")
        if not self.client_cert:
            missing.append("EIS_CLIENT_CERT")
        if not self.client_key:
            missing.append("EIS_CLIENT_KEY")
        if missing:
            raise EISConfigError(
                "Не заданы обязательные переменные окружения: " + ", ".join(missing)
            )
        if not os.path.isfile(self.client_cert):
            raise EISConfigError(f"Файл сертификата не найден: {self.client_cert}")
        if not os.path.isfile(self.client_key):
            raise EISConfigError(f"Файл приватного ключа не найден: {self.client_key}")

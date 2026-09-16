from __future__ import annotations

import os
from dataclasses import dataclass, field

from .exceptions import EISConfigError

# Подтверждено официальной инструкцией ЕИС «Инструкция по использованию
# сервисов отдачи информации ЕИС для юридических, физических лиц и ВСРЗ»
# (Москва, 2025), см. docs/eis-integration-instruction-2025.pdf.
ENDPOINTS = {
    "legal_entity": "https://int44-ttls-cert.zakupki.gov.ru/eis-integration/services/getDocsLE",
    "individual_person": "https://int.zakupki.gov.ru/eis-integration/services/getDocsIP",
    "vsrz": "https://int.zakupki.gov.ru/eis-integration/services/getDocsOrganization",
}

CONSUMER_TYPES = tuple(ENDPOINTS.keys())


@dataclass
class EISConfig:
    """Настройки подключения к сервису отдачи информации ЕИС.

    Три типа потребителя машиночитаемых данных, у каждого свой способ
    аутентификации (см. docs/eis-integration-instruction-2025.pdf, разделы 4-6):

    - "legal_entity" — юридическое лицо. Аутентификация сертификатом
      (руководителя/организации), которым также устанавливается mTLS-соединение.
      Токен в запросе не передаётся.
    - "individual_person" — физическое лицо/ИП. Аутентификация токеном
      (individualPerson_token), полученным в личном кабинете ЕИС. Обычный TLS,
      клиентский сертификат не нужен.
    - "vsrz" — внешняя система размещения заказов. Два токена в заголовке:
      self_registry_token (токен ВСРЗ) и organization_token (токен организации-
      владельца документов).

    Доступ получается самостоятельной регистрацией через ЕСИА в личном
    кабинете ЕИС (https://zakupki.gov.ru → Все разделы → Открытые данные →
    Получение открытых данных) — отдельного соглашения с Казначейством для
    этого НЕ требуется, в отличие от того, что предполагалось раньше.
    """

    consumer_type: str
    org_region: str
    subsystem_type: str
    document_type44: str
    client_cert: str = ""
    client_key: str = ""
    individual_person_token: str = ""
    self_registry_token: str = ""
    organization_token: str = ""
    timeout: int = 30

    @classmethod
    def from_env(cls) -> "EISConfig":
        config = cls(
            consumer_type=os.getenv("EIS_CONSUMER_TYPE", "legal_entity"),
            org_region=os.getenv("EIS_ORG_REGION", "77"),
            subsystem_type=os.getenv("EIS_SUBSYSTEM_TYPE", "RGK"),
            document_type44=os.getenv("EIS_DOCUMENT_TYPE44", "contract"),
            client_cert=os.getenv("EIS_CLIENT_CERT", ""),
            client_key=os.getenv("EIS_CLIENT_KEY", ""),
            individual_person_token=os.getenv("EIS_INDIVIDUAL_PERSON_TOKEN", ""),
            self_registry_token=os.getenv("EIS_SELF_REGISTRY_TOKEN", ""),
            organization_token=os.getenv("EIS_ORGANIZATION_TOKEN", ""),
            timeout=int(os.getenv("EIS_TIMEOUT", "30")),
        )
        config.validate()
        return config

    @property
    def endpoint(self) -> str:
        return ENDPOINTS[self.consumer_type]

    def validate(self) -> None:
        if self.consumer_type not in CONSUMER_TYPES:
            raise EISConfigError(
                f"EIS_CONSUMER_TYPE должен быть одним из {CONSUMER_TYPES}, "
                f"получено: {self.consumer_type!r}"
            )
        if not self.org_region:
            raise EISConfigError("Не задан EIS_ORG_REGION")
        if not self.subsystem_type:
            raise EISConfigError("Не задан EIS_SUBSYSTEM_TYPE")

        if self.consumer_type == "legal_entity":
            missing = [
                name
                for name, value in (("EIS_CLIENT_CERT", self.client_cert), ("EIS_CLIENT_KEY", self.client_key))
                if not value
            ]
            if missing:
                raise EISConfigError(
                    "Для consumer_type=legal_entity не заданы: " + ", ".join(missing)
                )
            if not os.path.isfile(self.client_cert):
                raise EISConfigError(f"Файл сертификата не найден: {self.client_cert}")
            if not os.path.isfile(self.client_key):
                raise EISConfigError(f"Файл приватного ключа не найден: {self.client_key}")

        elif self.consumer_type == "individual_person":
            if not self.individual_person_token:
                raise EISConfigError(
                    "Для consumer_type=individual_person не задан EIS_INDIVIDUAL_PERSON_TOKEN"
                )

        elif self.consumer_type == "vsrz":
            missing = [
                name
                for name, value in (
                    ("EIS_SELF_REGISTRY_TOKEN", self.self_registry_token),
                    ("EIS_ORGANIZATION_TOKEN", self.organization_token),
                )
                if not value
            ]
            if missing:
                raise EISConfigError("Для consumer_type=vsrz не заданы: " + ", ".join(missing))

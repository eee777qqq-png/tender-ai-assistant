from __future__ import annotations

import os
from dataclasses import dataclass, field

from .exceptions import EISConfigError

# Два РАЗНЫХ значения, которые легко перепутать (и которые уже один раз
# перепутали — см. CLAUDE.md, «Известные пробелы», п.3):
#
# 1. ENDPOINT_URLS — «куда стучимся»: физический адрес, на который уходит
#    HTTP POST. Подтверждён официальной инструкцией ЕИС «Инструкция по
#    использованию сервисов отдачи информации ЕИС для юридических, физических
#    лиц и ВСРЗ» (Москва, 2025), см. docs/eis-integration-instruction-2025.pdf.
#
# 2. ENVELOPE_NAMESPACES — «что написано в конверте»: значение xmlns:ws внутри
#    SOAP-XML (soap_request.py). Это идентификатор схемы, а не адрес — по нему
#    никто никуда не подключается. В примерах инструкции он совпадал с
#    физическим URL, и до 2026-09-23 код так и делал; сервис при этом отвечал
#    эхом запроса вместо ответа.
ENDPOINT_URLS = {
    "legal_entity": "https://int44-ttls-cert.zakupki.gov.ru/eis-integration/services/getDocsLE",
    "individual_person": "https://int.zakupki.gov.ru/eis-integration/services/getDocsIP",
    "vsrz": "https://int.zakupki.gov.ru/eis-integration/services/getDocsOrganization",
}

ENVELOPE_NAMESPACES = {
    # Официально от техподдержки ЕИС, обращение EIS-891228 (2026-09-23).
    "individual_person": "http://zakupki.gov.ru/fz44/get-docs-ip/ws",
    # НЕ подтверждено: техподдержка прислала namespace только для физлиц.
    # Для юрлиц оставлено прежнее значение (как в примере инструкции, равно
    # физическому URL) — при переходе на ООО уточнить отдельно, не угадывать
    # по аналогии с get-docs-ip.
    "legal_entity": "https://int44-ttls-cert.zakupki.gov.ru/eis-integration/services/getDocsLE",
    # vsrz: getDocsByOrgRegionRequest для ВСРЗ не строится (см. soap_request.py).
}

CONSUMER_TYPES = tuple(ENDPOINT_URLS.keys())


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
    def endpoint_url(self) -> str:
        """Физический адрес, на который уходит HTTP-запрос («куда стучимся»)."""
        return ENDPOINT_URLS[self.consumer_type]

    @property
    def envelope_namespace(self) -> str:
        """Значение xmlns:ws в SOAP-конверте («что написано в конверте»).

        Не адрес подключения — см. комментарий к ENVELOPE_NAMESPACES."""
        try:
            return ENVELOPE_NAMESPACES[self.consumer_type]
        except KeyError:
            raise EISConfigError(
                f"Для consumer_type={self.consumer_type!r} namespace SOAP-конверта не задан"
            ) from None

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

from __future__ import annotations

import logging
from datetime import date
from typing import Iterable

import requests
from requests import Session
from zeep import Client, Settings
from zeep.exceptions import Fault, TransportError
from zeep.transports import Transport

from .config import EISConfig
from .exceptions import EISRequestError
from .models import Purchase

logger = logging.getLogger(__name__)


class EISClient:
    """Тонкая обёртка над SOAP-сервисом ЕИС (int44.zakupki.gov.ru и аналоги).

    Сервис требует двустороннего TLS (mTLS) клиентским сертификатом,
    выданным при подключении по соглашению об информационном
    взаимодействии. Если сертификат выпущен на ГОСТ-криптографии,
    стандартный `ssl`/`requests` его не обработает — см. README, раздел
    "ГОСТ и КриптоПро", про варианты обхода этого ограничения.
    """

    def __init__(self, config: EISConfig):
        self.config = config
        self._session = self._build_session()
        self._client = self._build_soap_client()

    def _build_session(self) -> Session:
        session = requests.Session()
        session.cert = (self.config.client_cert, self.config.client_key)
        return session

    def _build_soap_client(self) -> Client:
        transport = Transport(session=self._session, timeout=self.config.timeout)
        settings = Settings(strict=False, xml_huge_tree=True)
        try:
            return Client(self.config.wsdl_url, transport=transport, settings=settings)
        except (TransportError, requests.RequestException) as exc:
            raise EISRequestError(f"Не удалось загрузить WSDL: {exc}") from exc

    def list_operations(self) -> list[str]:
        """Служебный метод: список доступных SOAP-операций в загруженном WSDL.

        Полезен на этапе интеграции, чтобы свериться с EIS_OPERATION_NAME.
        """
        service = self._client.service
        return [op for op in dir(service) if not op.startswith("_")]

    def get_purchases_by_okpd2(
        self,
        okpd2_codes: Iterable[str] | None = None,
        region_code: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[Purchase]:
        """Забирает список закупок по ОКПД2-кодам для заданного региона.

        Параметры запроса (`filterParams` ниже) — placeholder под реальную
        структуру входного типа операции: EIS отдаёт её в WSDL/XSD, которые
        выдаются вместе с доступом. Названия полей нужно свести к фактической
        схеме (обычно нечто вроде `docPublishDateFrom`, `okpd2Codes`,
        `regionCodes` — см. регламент информационного взаимодействия).
        """
        codes = list(okpd2_codes or self.config.okpd2_codes)
        region = region_code or self.config.region_code

        operation = getattr(self._client.service, self.config.operation_name, None)
        if operation is None:
            available = ", ".join(self.list_operations())
            raise EISRequestError(
                f"Операция '{self.config.operation_name}' не найдена в WSDL. "
                f"Доступные операции: {available}"
            )

        filter_params = {
            "regionCodes": [region],
            "okpd2Codes": codes,
        }
        if date_from is not None:
            filter_params["publishDateFrom"] = date_from
        if date_to is not None:
            filter_params["publishDateTo"] = date_to

        logger.info(
            "Запрос закупок в ЕИС: операция=%s регион=%s окпд2=%s",
            self.config.operation_name,
            region,
            codes,
        )

        try:
            response = operation(**filter_params)
        except Fault as exc:
            raise EISRequestError(f"SOAP fault от ЕИС: {exc}") from exc
        except requests.RequestException as exc:
            raise EISRequestError(f"Ошибка сети при обращении к ЕИС: {exc}") from exc

        items = self._extract_items(response)
        return [Purchase.from_soap_object(item) for item in items]

    @staticmethod
    def _extract_items(response) -> list:
        """Достаёт список закупок из ответа SOAP.

        Реальная обёртка ответа зависит от WSDL (часто это что-то вроде
        `response.dataInfo.purchaseList.purchase`). Здесь — попытка
        подобрать разумный путь автоматически, но при интеграции с боевым
        WSDL это стоит заменить на точный путь из схемы.
        """
        if response is None:
            return []
        if isinstance(response, list):
            return response

        for attr in ("purchases", "purchaseList", "items", "dataInfo"):
            value = getattr(response, attr, None)
            if value is not None:
                if isinstance(value, list):
                    return value
                nested = getattr(value, "purchase", None) or getattr(value, "items", None)
                if nested is not None:
                    return nested if isinstance(nested, list) else [nested]
        return []

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "EISClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

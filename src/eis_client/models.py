from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class Purchase:
    """Нормализованная запись о закупке.

    Поля соответствуют типичному набору атрибутов извещения о закупке в ЕИС
    (номер, наименование, НМЦК, заказчик, дата размещения). Реальные имена
    полей в ответе SOAP-сервиса зависят от WSDL — сопоставление делается в
    `Purchase.from_soap_object`, при подключении к боевому/тестовому
    контуру эти имена нужно будет свести к фактической схеме ответа.
    """

    purchase_number: str
    name: str
    customer_name: str
    okpd2_code: str
    region_code: str
    max_price: float | None
    publish_date: date | None
    raw: Any = None

    @classmethod
    def from_soap_object(cls, obj: Any) -> "Purchase":
        def get(*names: str, default: Any = None) -> Any:
            for name in names:
                value = getattr(obj, name, None)
                if value is not None:
                    return value
            return default

        publish_date_raw = get("publishDate", "publishDTInEIS")
        publish_date = None
        if publish_date_raw is not None:
            publish_date = (
                publish_date_raw.date()
                if hasattr(publish_date_raw, "date")
                else publish_date_raw
            )

        return cls(
            purchase_number=str(get("purchaseNumber", "regNum", default="")),
            name=str(get("purchaseObjectInfo", "name", default="")),
            customer_name=str(get("customerName", "orgName", default="")),
            okpd2_code=str(get("OKPD2", "okpd2Code", default="")),
            region_code=str(get("regionCode", default="")),
            max_price=get("maxPrice", "initialPrice"),
            publish_date=publish_date,
            raw=obj,
        )

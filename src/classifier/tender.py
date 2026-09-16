"""Закупка (тендер) — минимальный набор полей, нужных для сопоставления
с профилем клиента (см. `matching.py`).

Поля соответствуют типичному набору атрибутов извещения о закупке в ЕИС.
Пока Агент 1 не выдаёт полные структурированные записи закупок (см.
`eis_client.ConstructionDocument` — там пока только ОКПД2-коды из
документа), это отдельная модель для прототипа Агента 2 на тестовых
данных (`sample_tenders.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Tender:
    purchase_number: str
    name: str
    customer_name: str
    okpd2_code: str
    region_code: str
    max_price: float
    requires_sro: bool
    min_experience_years: int
    publish_date: date
    submission_deadline: date

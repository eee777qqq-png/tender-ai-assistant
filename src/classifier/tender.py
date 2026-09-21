"""Закупка (тендер) — минимальный набор полей, нужных для сопоставления
с профилем клиента (см. `matching.py`).

Поля соответствуют типичному набору атрибутов извещения о закупке в ЕИС.
Агент 1 сегодня извлекает из `ConstructionDocument` только 2 из этих 10
полей (`okpd2_code`, и с 2026-09-21 — `purchase_number` через реестровый
номер) — переходник `eis_client.tender_adapter.document_to_tender()`
строит `Tender` из них плюс остальных 8 полей, переданных явно (их схема
в реальных документах ЕИС недоступна, см. CLAUDE.md, «Известные пробелы»,
п.4). До реального источника всех 10 полей — `sample_tenders.py` остаётся
единственным источником `Tender` для тестов и прототипа Агента 2.
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

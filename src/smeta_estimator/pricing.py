"""Расчёт итоговой цены кандидата с учётом регионального индекса.

**Нерешённый методологический вопрос — см. CLAUDE.md, «Известные пробелы».**
ФСНБ-2022 (`base_price`, из `fsnb_parser.apply_prices`) заявлена как
ресурсно-индексный метод — цены ФСБЦ уже на текущем уровне (не 2001 года).
Индексы из писем Минстроя, которые парсит `index_parser.py`, исторически
считаются «индексами к ФЕР-2001/ТЕР-2001» — для другого, более старого
базисно-индексного метода (от цен 2001 года). Простое умножение
современной `base_price` на такой индекс может быть методологически
неверным (могло бы задвоить пересчёт). Формула ниже — то, что прямо
согласовано с владельцем продукта (`базисная цена × региональный индекс`),
но использовать результат как готовую цену для клиента нельзя без
подтверждения практикующим сметчиком, что это верный способ применения
именно для ресурсных данных ФСНБ-2022, а не для более старых ФЕР-2001/ТЕР-2001.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from .models import RateCandidate


def price_candidate(
    candidate: RateCandidate, index_value: float, index_as_of: date, region_name: str
) -> RateCandidate:
    return replace(
        candidate,
        regional_price=candidate.base_price * index_value,
        region_name=region_name,
        index_value=index_value,
        index_as_of=index_as_of,
    )


def price_candidates(
    candidates: list[RateCandidate], index_value: float, index_as_of: date, region_name: str
) -> list[RateCandidate]:
    return [price_candidate(c, index_value, index_as_of, region_name) for c in candidates]

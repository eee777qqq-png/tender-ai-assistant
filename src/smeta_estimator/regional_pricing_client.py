"""Загрузка региональных данных ФГИС ЦС для расчёта цены ресурса — раздел
«Сметные цены и индексы изменения сметной стоимости строительства»
(fgiscs.minstroyrf.ru/prices), проверено вживую 2026-09-18.

**Это отдельный, третий источник** — не тот же самый API, что письма
Минстроя (`docs/agent4-ai-matching-feasibility.md`, там разбирался общий
`FrsnDocument`). Здесь два разных эндпоинта под одной страницей:

- `/api/EstimatedPrice/BuildingResources/Search/{Materials|Machines}` —
  **текущая (актуальная) сметная цена ресурса напрямую**, если она
  опубликована для этого региона/квартала — приоритет №1 при расчёте цены
  (см. `pricing.py`).
- `/api/IndicesForResourcesGroups/GenerateCustomReport` — **индексы по
  группам однородных строительных ресурсов (ГОСР)**: для каждого кода
  ресурса — его базисная цена на 01.01.2022 (тот же уровень цен, что и в
  ФСБЦ, см. `fsnb_parser.py`) и индекс пересчёта именно для группы этого
  ресурса. Это правильный источник для пересчёта цены ФСНБ-2022
  (ресурсно-индексный метод), в отличие от индексов «к ФЕР-2001/ТЕР-2001»
  (для другого, более старого метода — были ошибочно использованы раньше,
  см. CLAUDE.md, «Известные пробелы»).

Идентификаторы региона/ценовой зоны/периода нужно получать через
`fetch_country_subjects()` → `fetch_price_zones()` → `fetch_periods()` —
единого прямого «по названию региона» эндпоинта нет.
"""

from __future__ import annotations

import requests

BASE_URL = "https://fgiscs.minstroyrf.ru/api"

# ID периода "3 квартал 2026 г." — актуален на 2026-09-18. Обновлять вручную
# по мере смены квартала (см. fetch_periods()) — автоматического
# отслеживания версий для этого источника, в отличие от индексов из
# документов Агента 10, не сделано.
CURRENT_PERIOD_ID = 427

# Найдено вживую 2026-09-18 через fetch_country_subjects()/fetch_price_zones() —
# у каждого из 4 пилотных регионов ровно одна ценовая зона.
PILOT_PRICE_ZONES: dict[str, dict[str, int | str]] = {
    "г. Москва": {"subject_id": 323, "price_zone_id": 191, "price_zone_name": "город Москва"},
    "Московская область": {"subject_id": 331, "price_zone_id": 127, "price_zone_name": "Московская область"},
    "Краснодарский край": {"subject_id": 280, "price_zone_id": 148, "price_zone_name": "Краснодарский край"},
    "Ростовская область": {"subject_id": 342, "price_zone_id": 170, "price_zone_name": "Ростовская область"},
}


def fetch_country_subjects(timeout: int = 30) -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/EstimatedPrice/CountrySubjects",
        params={"page": 1, "take": 100, "sort": "{}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_price_zones(subject_id: int, timeout: int = 30) -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/EstimatedPrice/PriceZones",
        params={"subjectId": subject_id, "page": 1, "take": 25, "sort": "{}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_periods(price_zone_id: int, timeout: int = 30) -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/EstimatedPrice/Periods",
        params={"priceZoneId": price_zone_id, "authorityId": "null", "page": 1, "take": 25, "sort": "{}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_current_prices_json(
    price_zone_id: int, period_id: int, category: str, timeout: int = 60
) -> bytes:
    """`category` — `"materials"` или `"machines"`. Пустой `search`/`value` —
    запрашивает весь опубликованный список категории для региона/квартала
    (проверено вживую: `take` в этом эндпоинте не ограничивает выдачу —
    сервис и так отдаёт всё сразу, постраничная догрузка не потребовалась
    на реальных объёмах данных Москвы, ~2700 позиций материалов)."""
    endpoint = "Materials" if category == "materials" else "Machines"
    response = requests.get(
        f"{BASE_URL}/EstimatedPrice/BuildingResources/Search/{endpoint}",
        params={
            "countrySubjectId": _subject_id_for_zone(price_zone_id),
            "priceZoneId": price_zone_id,
            "periodId": period_id,
            "search": "",
            "authorityId": "null",
            "refresh": "{}",
            category: "true",
            "value": "",
            "page": 1,
            "take": 100000,
            "sort": "{}",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.content


def fetch_gosr_report(price_zone_id: int, period_id: int, timeout: int = 120) -> bytes:
    """Отдаёт .xlsx (2 листа: материалы/изделия/оборудование, машины и
    механизмы) — индексы ГОСР + базисная цена на 01.01.2022 по каждому
    коду ресурса, для одной ценовой зоны и одного квартала."""
    response = requests.get(
        f"{BASE_URL}/IndicesForResourcesGroups/GenerateCustomReport",
        params={"periodId": period_id, "priceZoneId": price_zone_id, "authorityId": "null"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.content


def _subject_id_for_zone(price_zone_id: int) -> int:
    for info in PILOT_PRICE_ZONES.values():
        if info["price_zone_id"] == price_zone_id:
            return info["subject_id"]  # type: ignore[return-value]
    raise ValueError(
        f"Ценовая зона {price_zone_id} не входит в 4 пилотных региона — subject_id неизвестен "
        "этому фундаменту, нужно расширить PILOT_PRICE_ZONES или передать countrySubjectId явно"
    )

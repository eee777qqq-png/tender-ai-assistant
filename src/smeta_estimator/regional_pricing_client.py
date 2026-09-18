"""Загрузка региональных данных ФГИС ЦС для расчёта цены ресурса — раздел
«Сметные цены и индексы изменения сметной стоимости строительства»
(fgiscs.minstroyrf.ru/prices), проверено вживую 2026-09-18.

**Это отдельный, третий источник** — не тот же самый API, что письма
Минстроя (`docs/agent4-ai-matching-feasibility.md`, там разбирался общий
`FrsnDocument`). Здесь три разных эндпоинта под одной страницей:

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
- `/api/EstimatedPrice/RimWorkerSalaryRegistry` — **сметная цена затрат
  труда, руб./чел.-ч**, по коду разряда рабочего (`1-100-XX`, тот же код,
  что и `Resource Code` в ГЭСН) **и** по коду машиниста (`4-100-XXX`, тот
  же код, что `DriverCode` в ФСБЦ_Маш.xml) — единый реестр закрывает и
  трудозатраты рабочих, и часть зарплаты машиниста, см. CLAUDE.md,
  «Известные пробелы» про то, что именно ещё не подтверждено про машинистов.

**Пагинация у этих эндпоинтов устроена по-разному — проверено вживую
2026-09-18, а не предположено.** `RimWorkerSalaryRegistry` `take`
по-настоящему ограничивает выдачу (Москва: `total=186`, `take=100` реально
вернул только 100 позиций) — `fetch_worker_salary_registry()` поэтому
честно постранично догружает до `total`. У `BuildingResources/Search/*`
на практике `take` выдачу не ограничивал (Москва: ~2700 материалов
вернулись одним ответом при `take=25`) — но `fetch_current_prices_json()`
всё равно не доверяет этому слепо: сверяет фактическое количество с
`total` из ответа и, если не совпало, повторяет запрос с большим `take`,
а не возвращает то, что пришло, как будто это полный список.

Идентификаторы региона/ценовой зоны/периода нужно получать через
`fetch_country_subjects()` → `fetch_price_zones()` → `fetch_periods()` —
единого прямого «по названию региона» эндпоинта нет.
"""

from __future__ import annotations

import json

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
    price_zone_id: int,
    period_id: int,
    category: str,
    timeout: int = 60,
    initial_take: int = 25000,
    max_take: int = 400000,
) -> bytes:
    """`category` — `"materials"` или `"machines"`. Пустой `search`/`value` —
    запрашивает весь опубликованный список категории для региона/квартала.

    На практике `take` у этого эндпоинта выдачу не ограничивал (см. докстринг
    модуля), но здесь это не принимается на веру: если фактически вернувшееся
    количество позиций меньше `total` из ответа, запрос повторяется с
    увеличенным `take`, пока не совпадёт или пока не будет достигнут
    `max_take` — тогда явная ошибка, а не тихо неполные данные.
    """
    endpoint = "Materials" if category == "materials" else "Machines"
    take = initial_take
    while True:
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
                "take": take,
                "sort": "{}",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        returned = sum(len(group.get("items", [])) for group in data.get("items", []))
        total = data.get("total", returned)
        if returned >= total:
            return response.content
        if take >= max_take:
            raise ValueError(
                f"Эндпоинт текущих цен ({category}, priceZoneId={price_zone_id}, "
                f"periodId={period_id}) вернул только {returned} из {total} позиций даже при "
                f"take={take} — похоже, у него всё же есть пагинация, которую этот клиент пока "
                "не реализует постранично. Не потеряно молча, но и не собрано полностью."
            )
        take *= 4


def fetch_worker_salary_registry(price_zone_id: int, period_id: int, timeout: int = 60, page_size: int = 200) -> bytes:
    """Сметная цена затрат труда (руб./чел.-ч) по коду разряда — рабочие
    (`1-100-XX`) и машинисты (`4-100-XXX`), один реестр на оба. Настоящая
    постраничная догрузка — `take` здесь реально ограничивает выдачу
    (см. докстринг модуля), поэтому запрашивает страницы, пока не наберёт
    `total`, а не один раз с расчётом на «и так всё придёт»."""
    collected: list[dict] = []
    total = 0
    page = 1
    while True:
        response = requests.get(
            f"{BASE_URL}/EstimatedPrice/RimWorkerSalaryRegistry",
            params={
                "countrySubjectId": _subject_id_for_zone(price_zone_id),
                "priceZoneId": price_zone_id,
                "periodId": period_id,
                "search": "",
                "authorityId": "null",
                "refresh": "{}",
                "value": "",
                "page": page,
                "take": page_size,
                "sort": "{}",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        total = data.get("total", 0)
        items = data.get("items", [])
        collected.extend(items)
        if not items or len(collected) >= total:
            break
        page += 1
    return json.dumps({"items": collected, "total": total}, ensure_ascii=False).encode("utf-8")


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

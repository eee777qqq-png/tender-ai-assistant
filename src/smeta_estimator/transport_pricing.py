"""Строки транспорта и погрузочно-разгрузочных работ (ПРР) в смете —
сопоставление по ТЕКСТУ описания со сметными ценами услуг ФГИС ЦС
(вкладка «Сметные цены услуг на перевозку и погрузочно-разгрузочные
работы», `regional_pricing_client.fetch_load_works_by_auto()`/
`fetch_transportation_by_auto()`), 2026-10-02.

**Почему не через каталог ГЭСН/ФСБЦ.** Цен на перевозку и ПРР нет ни в
одном файле архива ФСНБ-2022 (ГЭСН/ГЭСНр/ГЭСНм/ФСБЦ) — отсюда «0
кандидатов» у всех строк погрузки/перевозки мусора на круге 1 и 2 (см.
CLAUDE.md, открытый п.17, «Находка 2»). Это отдельный справочник ФГИС ЦС,
публикуемый поквартально по каждой ценовой зоне.

**Коды вида "48-1" и "02-15-1-01-0030" — официальные коды этого же
справочника ФГИС ЦС, не внутренние ссылки сметной программы.** Проверено
вживую 2026-10-02 на смете «Рыбное» (Рязанская обл., II кв. 2026): "48-1" —
код погрузки груза «Мусор строительный с погрузкой транспортерами»
(217,94 руб./т — ровно как в смете), "02-15-1-01-0030" — перевозка самосвалом
до 15 т, I класс груза, усовершенствованное покрытие, 30 км (347,57 руб./т —
тоже ровно как в смете). Тем не менее сопоставление здесь идёт по
описанию, а не по коду: описание есть у любой такой строки, а код в чужой
смете может быть записан в формате другой программы или с опечаткой. Код
документа используется только как независимая сверка
(`TransportPriceMatch.document_code_agrees`) — совпал с найденным по описанию
официальным кодом или нет, эксперт видит это явно.

Что разбирается из описания:
- **ПРР** ("Погрузка в автотранспортное средство: <груз>" / "Разгрузка...") —
  вид операции + вид груза; груз сопоставляется со списком `cargoName` по
  доле слов официального названия, покрытых текстом строки. Берётся
  единственный лучший вариант с покрытием не ниже `CARGO_COVERAGE_THRESHOLD`;
  ничья между двумя лучшими — честно неоднозначно, не угадываем.
- **Перевозка** ("Перевозка грузов I класса автомобилями-самосвалами
  грузоподъемностью до 15 т по дорогам с усовершенствованным ... покрытием
  на расстояние 30 км") — класс груза, тип автотранспорта, грузоподъёмность,
  тип покрытия, расстояние. Каждый параметр сверяется со списком значений,
  который отдаёт сам ФГИС ЦС (не с зашитым здесь списком), — не нашёлся
  хотя бы один — честный отказ с указанием, какой именно.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .code_lookup import normalize_gesn_code
from .text_matching import stem_words

CARGO_COVERAGE_THRESHOLD = 0.75

SOURCE_LABEL = "ФГИС ЦС — сметные цены услуг на перевозку и погрузочно-разгрузочные работы"

_ROMAN_CLASS = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_CLASS_RE = re.compile(r"груз\w*\s+(iv|i{1,3}|[1-4])(?:-?го)?\s+класс", re.IGNORECASE)
_CAPACITY_RE = re.compile(r"грузоподъемност\w*\s+(до\s+\d+(?:[.,]\d+)?\s*т)\b", re.IGNORECASE)
_DISTANCE_RE = re.compile(r"расстояни\w*\s+(\d+)\s*км", re.IGNORECASE)
# Ключевой корень типа покрытия в описании строки -> корень в официальном
# названии ФГИС ЦС (сравниваются оба по вхождению, не по полному тексту:
# в сметах встречается и полная формулировка, и краткая "с
# усовершенствованным покрытием" — см. Краснодар, круг 2).
_ROAD_TYPE_KEYS = ("усовершенствован", "переходн", "грунтов")
# Слова-приставки в официальных названиях типов автотранспорта, которые не
# отличают один тип от другого ("Автомобили-самосвалы", "Автоцистерны").
_VEHICLE_PREFIX_RE = re.compile(r"^(автомобили[\s-]*|авто)", re.IGNORECASE)
_VEHICLE_STEM_LEN = 6


def _first_line(text: str) -> str:
    """Первая строка ячейки (ниже бывает «Кол-во: =формула» — Краснодар,
    круг 2), с «ё» -> «е» для единообразного разбора."""
    stripped = text.strip().replace("ё", "е").replace("Ё", "Е")
    return stripped.splitlines()[0] if stripped else ""


def detect_transport_operation(name: str) -> str | None:
    """"load" / "unload" / "transport" по началу описания строки сметы, или
    `None` — строка не транспорт и не ПРР (дальше идёт обычный путь
    каталога ГЭСН/ФСБЦ)."""
    low = _first_line(name).lower()
    if low.startswith("погрузка"):
        return "load"
    if low.startswith(("разгрузка", "выгрузка")):
        return "unload"
    if low.startswith("перевозка") and "груз" in low:
        return "transport"
    return None


@dataclass
class TransportPriceMatch:
    """Результат сопоставления одной строки сметы. `unit_price is None` —
    сопоставить не удалось, причина — в `failure_reason`, без угадывания.

    `official_code` — код найденной позиции в справочнике ФГИС ЦС;
    `document_code_agrees` — совпал ли он с кодом из «Обоснования» строки
    (`None` — в строке кода нет). `source` — что показывать эксперту как
    обоснование цены: справочник, регион, квартал."""

    operation: str
    unit_price: float | None
    official_code: str | None = None
    official_description: str | None = None
    source: str | None = None
    document_code_agrees: bool | None = None
    failure_reason: str | None = None
    alternatives: list[str] = field(default_factory=list)


def _codes_agree(document_code: str | None, official_code: str) -> bool | None:
    if not document_code:
        return None
    return normalize_gesn_code(document_code) == official_code


def match_load_work(
    name: str,
    operation: str,
    load_works: list[dict],
    source: str,
    document_code: str | None = None,
) -> TransportPriceMatch:
    """ПРР: вид груза — текст после двоеточия в первой строке описания
    ("Погрузка в автотранспортное средство: мусор строительный с погрузкой
    вручную"), без двоеточия — всё описание."""
    first = _first_line(name)
    cargo_text = first.split(":", 1)[1] if ":" in first else first
    query = stem_words(cargo_text)

    scored: list[tuple[float, dict]] = []
    for item in load_works:
        official = stem_words(item.get("cargoName", ""))
        if not official:
            continue
        scored.append((len(official & query) / len(official), item))
    scored.sort(key=lambda pair: -pair[0])

    if not scored or scored[0][0] < CARGO_COVERAGE_THRESHOLD:
        best = f" (лучшее покрытие {scored[0][0]:.2f})" if scored else ""
        return TransportPriceMatch(
            operation,
            None,
            failure_reason=f"вид груза не сопоставился со справочником ПРР ФГИС ЦС{best}",
        )
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return TransportPriceMatch(
            operation,
            None,
            failure_reason="вид груза одинаково подходит под несколько позиций справочника ПРР — нужно решение эксперта",
            alternatives=[s[1]["cargoName"] for s in scored if s[0] == scored[0][0]],
        )

    item = scored[0][1]
    code_key, price_key = ("loadCode", "loadPrice") if operation == "load" else ("unloadCode", "unloadPrice")
    official_code = item[code_key]
    verb = "Погрузка" if operation == "load" else "Разгрузка"
    return TransportPriceMatch(
        operation,
        float(item[price_key]),
        official_code=official_code,
        official_description=f"{verb}: {item['cargoName']}",
        source=source,
        document_code_agrees=_codes_agree(document_code, official_code),
    )


@dataclass
class TransportationQuery:
    """Параметры перевозки, разобранные из описания строки (до сверки со
    значениями ФГИС ЦС)."""

    cargo_class: int
    capacity: str
    distance_km: int
    road_key: str
    description: str


def parse_transportation(name: str) -> TransportationQuery | str:
    """Разбирает описание перевозки. Возвращает либо параметры, либо строку
    с причиной, какой именно параметр не найден в тексте."""
    first = _first_line(name)
    low = first.lower()
    class_m = _CLASS_RE.search(first)
    if class_m is None:
        return "не найден класс груза («грузов N класса»)"
    raw_class = class_m.group(1).lower()
    cargo_class = _ROMAN_CLASS.get(raw_class) or int(raw_class)
    cap_m = _CAPACITY_RE.search(first)
    if cap_m is None:
        return "не найдена грузоподъёмность («грузоподъемностью до N т»)"
    dist_m = _DISTANCE_RE.search(first)
    if dist_m is None:
        return "не найдено расстояние («на расстояние N км»)"
    road_key = next((k for k in _ROAD_TYPE_KEYS if k in low), None)
    if road_key is None:
        return "не найден тип дорожного покрытия"
    return TransportationQuery(
        cargo_class=cargo_class,
        capacity=re.sub(r"\s+", " ", cap_m.group(1).replace(",", ".")).strip().lower(),
        distance_km=int(dist_m.group(1)),
        road_key=road_key,
        description=low,
    )


def _vehicle_stem(official_name: str) -> str:
    return _VEHICLE_PREFIX_RE.sub("", official_name.strip()).lower()[:_VEHICLE_STEM_LEN]


def _normalize_capacity(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(",", ".")).strip().lower()


class TransportPriceBook:
    """Ленивый доступ к справочнику ФГИС ЦС для одной ценовой зоны и
    квартала: ПРР загружаются один раз, таблицы перевозки — по требованию
    для каждой встретившейся комбинации покрытие/авто/грузоподъёмность, с
    кэшем. Сетевые функции передаются снаружи (в тестах — подставные)."""

    def __init__(
        self,
        source: str,
        fetch_load_works: Callable[[], list[dict]],
        fetch_filter_values: Callable[..., list[str]],
        fetch_transportation: Callable[[str, str, str], list[dict]],
    ) -> None:
        self.source = source
        self._fetch_load_works = fetch_load_works
        self._fetch_filter_values = fetch_filter_values
        self._fetch_transportation = fetch_transportation
        self._load_works: list[dict] | None = None
        self._tables: dict[tuple[str, str, str], list[dict]] = {}

    def match(self, name: str, document_code: str | None = None) -> TransportPriceMatch | None:
        """`None` — строка не транспорт/ПРР по описанию (вызывающий код
        идёт обычным путём каталога)."""
        operation = detect_transport_operation(name)
        if operation is None:
            return None
        if operation in ("load", "unload"):
            if self._load_works is None:
                self._load_works = self._fetch_load_works()
            return match_load_work(name, operation, self._load_works, self.source, document_code)
        return self._match_transportation(name, document_code)

    def _match_transportation(self, name: str, document_code: str | None) -> TransportPriceMatch:
        parsed = parse_transportation(name)
        if isinstance(parsed, str):
            return TransportPriceMatch("transport", None, failure_reason=parsed)

        road_types = self._fetch_filter_values("RoadType")
        road_type = next((r for r in road_types if parsed.road_key in r.lower()), None)
        if road_type is None:
            return TransportPriceMatch(
                "transport", None, failure_reason=f"тип покрытия «{parsed.road_key}…» не найден в ФГИС ЦС"
            )

        vehicle_types = self._fetch_filter_values("VehicleType", roadType=road_type)
        vehicles = [v for v in vehicle_types if _vehicle_stem(v) and _vehicle_stem(v) in parsed.description]
        if len(vehicles) != 1:
            return TransportPriceMatch(
                "transport",
                None,
                failure_reason=(
                    "тип автотранспорта не определился однозначно по описанию"
                    if vehicles
                    else "тип автотранспорта из описания не найден в ФГИС ЦС"
                ),
                alternatives=vehicles,
            )
        vehicle_type = vehicles[0]

        capacities = self._fetch_filter_values("VehicleLoadCapacity", roadType=road_type, vehicleType=vehicle_type)
        capacity = next((c for c in capacities if _normalize_capacity(c) == parsed.capacity), None)
        if capacity is None:
            return TransportPriceMatch(
                "transport",
                None,
                failure_reason=f"грузоподъёмность «{parsed.capacity}» для «{vehicle_type}» не найдена в ФГИС ЦС",
                alternatives=list(capacities),
            )

        key = (road_type, vehicle_type, capacity)
        if key not in self._tables:
            self._tables[key] = self._fetch_transportation(*key)
        row = next(
            (r for r in self._tables[key] if int(r.get("transportationDistance", -1)) == parsed.distance_km), None
        )
        if row is None:
            return TransportPriceMatch(
                "transport", None, failure_reason=f"расстояние {parsed.distance_km} км нет в таблице ФГИС ЦС"
            )

        official_code = row[f"transportation{parsed.cargo_class}ClassCode"]
        return TransportPriceMatch(
            "transport",
            float(row[f"price{parsed.cargo_class}Class"]),
            official_code=official_code,
            official_description=(
                f"Перевозка грузов {parsed.cargo_class} класса: {vehicle_type}, {capacity}, "
                f"{road_type}, {parsed.distance_km} км"
            ),
            source=self.source,
            document_code_agrees=_codes_agree(document_code, official_code),
        )

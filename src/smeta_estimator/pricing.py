"""Расчёт региональной цены кандидата — по каждому ресурсу отдельно, с
приоритетом (согласовано после диагностики методологической ошибки, см.
CLAUDE.md, «Известные пробелы»):

1. Если для кода ресурса в этом регионе/квартале опубликована **текущая
   (актуальная) сметная цена напрямую** (`current_prices`,
   `regional_pricing_client.fetch_current_prices_json`) — используется она
   как есть, без индекса.
2. Иначе — **базисная цена на 01.01.2022** (`resource_base_prices`, из
   `fsnb_parser.parse_fsbc_*`) **× индекс ГОСР** для группы однородных
   строительных ресурсов, к которой относится именно этот код
   (`gosr_index`, `regional_pricing_parser.parse_gosr_workbook`) — не единый
   общий индекс на всю позицию, у каждой группы ресурсов свой.
3. Иначе — цена не определена, ресурс явно попадает в
   `unresolved_resource_codes`, не в тихий ноль.

**Почему не так, как было раньше.** ФСНБ-2022 — ресурсно-индексный метод,
базисный уровень цен 01.01.2022 (подтверждено официальным разъяснением
Минстроя, приказ №1046/пр — тот же приказ, которым утверждена сама
ФСНБ-2022). Индексы «к ФЕР-2001/ТЕР-2001» из общих писем Минстроя (раньше
ошибочно использовались через `index_parser.py`, удалён) — для другого,
базисно-индексного метода с базой 2001 года; применение их поверх текущих
данных ФСНБ-2022 задваивало пересчёт. Правильный источник — «Индексы по
группам однородных строительных ресурсов (ГОСР)», найден и подключён
(`regional_pricing_client.py`, `regional_pricing_parser.py`).

**Агрегатные («итого») коды трудозатрат — `2` и, реже, `1` — не ресурсы, а
рекапы, не нуждаются в собственной цене.** Найдено и проверено на реальных
данных круга 1 (2026-09-30, roof/ceiling): в самом ГЭСН.xml голые (без
дефиса) коды `"1"`/`"2"` внутри `<Resources>` — это ИТОГОВЫЕ строки
«Затраты труда рабочих» / «Затраты труда машинистов» в человеко-часах,
численно равные сумме уже присутствующих в той же позиции детальных
ресурсов (разрядных кодов `N-100-XX` — они резолвятся через тот же
`RimWorkerSalaryRegistry`, что и `current_prices`, — либо `labour_mach ×
quantity` машинных ресурсов, уже добавляемых ниже как `machinist_wage_added`).
Проверено на выборке 3000 случайных позиций каталога: код «2» численно
совпадает с суммой трудозатрат машинистов по машинам той же позиции в
2267/2286 сравнимых случаев (19 расхождений — только округление
опубликованного числа до 2 знаков, не реальное отличие; ноль случаев кода
«2» без единой машины в позиции). Раньше это давало ложный сигнал
«unresolved» на практически КАЖДОЙ позиции (76,7% каталога содержат код
«2»), из-за чего казалось, что труд вообще не оценивается — реальный
прогон `--show-resources` (roof, ГЭСНр58-01-005-03) показывает обратное:
разрядный код `1-100-30` резолвится по 657,26 ₽/ед и даёт 96,5% цены
позиции, машинист учтён отдельной добавкой `+труд машиниста`, а «2»
действительно избыточен — числа сходятся. Отмечаются отдельным источником
`"aggregate_rollup"` (не `"unresolved"`) — вклад в `total_price` всегда 0,
как и было (просто честно назван, не спутан с настоящим пробелом в данных).

**Оплата труда машиниста (`machine_labour`, необязательный параметр).**
Для машинных ресурсов, после определения их собственной цены по приоритету
выше, дополнительно прибавляется `labour_mach × текущая_ставка(driver_code)`
из `current_prices` (тот же `RimWorkerSalaryRegistry`, что уже используется
для рабочих) — см. `MachineLabourInfo`. **Логика подтверждена устно на
звонке со Smetrix (2026-09-18) и независимо — из первоисточника, публичной
веб-страницы ФГИС ЦС (2026-09-24, см. CLAUDE.md, «Известные пробелы» →
«Решено», «Трудозатраты машинистов»).** Если ставка машиниста нужна
(`labour_mach > 0`), но не нашлась в `current_prices` — ресурс честно
уходит в `unresolved`, а не тихо остаётся без зарплаты в цене.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .models import (
    MachineLabourInfo,
    MaterialRateCandidate,
    RateCandidate,
    RegionalPriceResult,
    ResourcePriceResolution,
)
from .order_modifiers import OrderModifier
from .regional_pricing_parser import GosrIndexEntry

# Голые (без дефиса) коды трудозатрат — итоговые рекап-строки «Затраты
# труда рабочих»/«Затраты труда машинистов», не самостоятельные ресурсы (см.
# докстринг модуля). Только эти два значения встречаются во всём каталоге
# ГЭСН+ГЭСНр — проверено сканированием всех 34004 позиций 2026-09-30.
_AGGREGATE_LABOUR_CODES = frozenset({"1", "2"})

# Категория ресурса для применения `OrderModifier` (см. order_modifiers.py,
# найдено на реальном документе "потолок", 2026-09-30, CLAUDE.md открытый
# п.19) — множитель модификатора применяется к КОЛИЧЕСТВУ ресурса, по той
# же категории, что и в тексте самого приказа (ОЗП/ЭМ/ЗПМ/МАТ). Машинисты
# ("4-100-XXX") проверяются ПЕРЕД общим шаблоном рабочих — код машиниста
# тоже подходит под общий шаблон "N-100-XXX".
_LABOUR_MACHINIST_RE = re.compile(r"^4-100-")
_LABOUR_WORKER_RE = re.compile(r"^\d+-100-")
_MACHINE_RE = re.compile(r"^9\d\.")


def _resource_category(resource_code: str) -> str:
    if _LABOUR_MACHINIST_RE.match(resource_code):
        return "zpm"
    if _LABOUR_WORKER_RE.match(resource_code):
        return "ozp"
    if _MACHINE_RE.match(resource_code):
        return "em"
    return "mat"


def _apply_modifier(quantity: float, resource_code: str, modifier: OrderModifier | None) -> float:
    """`modifier is None` — обычная позиция без ссылки на пункт приказа,
    поведение не меняется (количество как в норме ГЭСН). С модификатором —
    масштабирует по категории ресурса (см. `_resource_category()`), ровно
    так же, как это делает сам сметчик в колонке "коэффициенты" реальной
    сметы (найдено и подтверждено на реальных числах, см. order_modifiers.py)."""
    if modifier is None:
        return quantity
    return quantity * getattr(modifier, _resource_category(resource_code))


def resolve_resource_unit_price(
    resource_code: str,
    base_price_2022: float | None,
    current_prices: dict[str, float],
    gosr_index: dict[str, GosrIndexEntry],
) -> ResourcePriceResolution | None:
    """Только сама логика приоритета — без `quantity`/`resource_name`,
    вызывающий код (`price_candidate_for_region`) дополняет ими результат.
    Возвращает `None`, если это чисто техническая проверка без контекста
    количества — на практике используется `price_candidate_for_region`."""
    if resource_code in current_prices:
        return ResourcePriceResolution(
            resource_code=resource_code,
            resource_name="",
            quantity=0.0,
            unit_price=current_prices[resource_code],
            source="current_price",
        )
    entry = gosr_index.get(resource_code)
    if entry is not None and base_price_2022 is not None:
        return ResourcePriceResolution(
            resource_code=resource_code,
            resource_name="",
            quantity=0.0,
            unit_price=base_price_2022 * entry.index_value,
            source="gosr_index",
            index_value=entry.index_value,
            group_name=entry.group_name,
        )
    return None


def price_candidate_for_region(
    candidate: RateCandidate,
    region_name: str,
    period_label: str,
    current_prices: dict[str, float],
    gosr_index: dict[str, GosrIndexEntry],
    resource_base_prices: dict[str, float],
    machine_labour: dict[str, MachineLabourInfo] | None = None,
    modifier: OrderModifier | None = None,
    zeroed_resource_codes: frozenset[str] | set[str] | None = None,
) -> RateCandidate:
    """`resource_base_prices` — код ресурса -> базисная цена на 01.01.2022,
    то, что уже посчитано `fsnb_parser.parse_fsbc_materials_xml`/
    `parse_fsbc_machines_xml` для конкретных ресурсов. Трудозатраты и
    `AbstractResource` (нет конкретного кода продукта) сюда не попадают —
    остаются в `unresolved_resource_codes`, как и в `base_price` самого
    `GesnWorkItem` (см. CLAUDE.md, «Известные пробелы»).

    `machine_labour` — код машинного ресурса -> `MachineLabourInfo`
    (`fsnb_parser.parse_fsbc_machine_labour_xml`), необязательный: без него
    поведение как раньше, без добавки оплаты труда машиниста (см. докстринг
    модуля про независимое подтверждение этой добавки).

    `modifier` — необязательный `OrderModifier` (см. `order_modifiers.py`),
    найденный по ссылке на пункт приказа в «Обосновании» конкретной строки
    сметы (не самого кандидата каталога — один и тот же код ГЭСН может быть
    с модификатором в одной позиции документа и без него в другой, поэтому
    модификатор передаётся вызывающим кодом на уровне строки сметы, не
    хранится в `RateCandidate`/`GesnWorkItem`). `None` — поведение как
    раньше, без изменений (количество берётся прямо из нормы ГЭСН).

    `zeroed_resource_codes` — коды ресурсов, которые конкретная строка
    сметы явно обнулила внутри этой позиции (`WorkVolumeRow.
    zeroed_resource_codes`, уже нормализованные вызывающим кодом через
    `code_lookup.normalize_gesn_code()`). Такой ресурс не оценивается по
    норме каталога вообще — `source="zeroed_in_document"`, вклад 0: доверяем
    факту обнуления в документе, а не пытаемся угадать, какая отдельная
    строка его заменила (коды нормы и замены в реальных документах
    различаются, см. CLAUDE.md). Сама строка-замена считается как обычно,
    своей отдельной строкой сметы. `None` — поведение как раньше.

    Возвращает **новый** объект `RateCandidate` (не мутирует исходный).
    """
    machine_labour = machine_labour or {}
    zeroed_resource_codes = zeroed_resource_codes or frozenset()
    resolutions: list[ResourcePriceResolution] = []
    total = 0.0

    for usage in candidate.resources:
        quantity = _apply_modifier(usage.quantity, usage.resource_code, modifier)

        if usage.resource_code in _AGGREGATE_LABOUR_CODES:
            resolutions.append(
                ResourcePriceResolution(
                    resource_code=usage.resource_code,
                    resource_name=usage.resource_name,
                    quantity=quantity,
                    unit_price=0.0,
                    source="aggregate_rollup",
                )
            )
            continue

        if usage.resource_code in zeroed_resource_codes:
            resolutions.append(
                ResourcePriceResolution(
                    resource_code=usage.resource_code,
                    resource_name=usage.resource_name,
                    quantity=0.0,
                    unit_price=0.0,
                    source="zeroed_in_document",
                )
            )
            continue

        if usage.is_abstract:
            resolutions.append(
                ResourcePriceResolution(
                    resource_code=usage.resource_code,
                    resource_name=usage.resource_name,
                    quantity=quantity,
                    unit_price=None,
                    source="unresolved",
                )
            )
            continue

        base_price = resource_base_prices.get(usage.resource_code)
        resolution = resolve_resource_unit_price(
            usage.resource_code, base_price, current_prices, gosr_index
        )
        if resolution is None:
            resolutions.append(
                ResourcePriceResolution(
                    resource_code=usage.resource_code,
                    resource_name=usage.resource_name,
                    quantity=quantity,
                    unit_price=None,
                    source="unresolved",
                )
            )
            continue

        resolution.resource_name = usage.resource_name
        resolution.quantity = quantity

        labour = machine_labour.get(usage.resource_code)
        if labour is not None and labour.labour_mach > 0:
            wage_rate = current_prices.get(labour.driver_code) if labour.driver_code else None
            if wage_rate is None:
                # Нужна ставка машиниста (labour_mach > 0), но её нет в
                # current_prices — честно unresolved, а не цена машины без
                # оплаты труда оператора, выданная как будто полная.
                resolutions.append(
                    ResourcePriceResolution(
                        resource_code=usage.resource_code,
                        resource_name=usage.resource_name,
                        quantity=quantity,
                        unit_price=None,
                        source="unresolved",
                    )
                )
                continue
            wage_addition = labour.labour_mach * wage_rate
            resolution.unit_price = resolution.unit_price + wage_addition  # type: ignore[operator]
            resolution.machinist_wage_added = wage_addition

        resolutions.append(resolution)
        total += resolution.unit_price * quantity  # type: ignore[operator]

    priced = RegionalPriceResult(
        region_name=region_name, period_label=period_label, total_price=total, resolutions=resolutions
    )
    return replace(candidate, priced=priced)


def price_material_candidate_for_region(
    candidate: MaterialRateCandidate,
    current_prices: dict[str, float],
    gosr_index: dict[str, GosrIndexEntry],
) -> MaterialRateCandidate:
    """Региональная цена материала-кандидата — по тому же приоритету, что
    и у одного ресурса внутри позиции ГЭСН (`resolve_resource_unit_price()`),
    просто без разбивки на составляющие: материал сам себе единственный
    ресурс. `unit_price=None` — цена не определилась (нет ни текущей цены,
    ни индекса ГОСР для группы этого кода) — честно, не 0."""
    resolution = resolve_resource_unit_price(
        candidate.code, candidate.base_price_2022, current_prices, gosr_index
    )
    if resolution is None:
        return replace(candidate, unit_price=None, price_source=None)
    return replace(
        candidate,
        unit_price=resolution.unit_price,
        price_source=resolution.source,
        index_value=resolution.index_value,
        group_name=resolution.group_name,
    )


def price_material_candidates_for_region(
    candidates: list[MaterialRateCandidate],
    current_prices: dict[str, float],
    gosr_index: dict[str, GosrIndexEntry],
) -> list[MaterialRateCandidate]:
    return [
        price_material_candidate_for_region(c, current_prices, gosr_index) for c in candidates
    ]


def price_candidates_for_region(
    candidates: list[RateCandidate],
    region_name: str,
    period_label: str,
    current_prices: dict[str, float],
    gosr_index: dict[str, GosrIndexEntry],
    resource_base_prices: dict[str, float],
    machine_labour: dict[str, MachineLabourInfo] | None = None,
    modifier: OrderModifier | None = None,
    zeroed_resource_codes: frozenset[str] | set[str] | None = None,
) -> list[RateCandidate]:
    return [
        price_candidate_for_region(
            c,
            region_name,
            period_label,
            current_prices,
            gosr_index,
            resource_base_prices,
            machine_labour,
            modifier,
            zeroed_resource_codes,
        )
        for c in candidates
    ]

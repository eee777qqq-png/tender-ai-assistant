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

from dataclasses import replace

from .models import MachineLabourInfo, RateCandidate, RegionalPriceResult, ResourcePriceResolution
from .regional_pricing_parser import GosrIndexEntry


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

    Возвращает **новый** объект `RateCandidate` (не мутирует исходный).
    """
    machine_labour = machine_labour or {}
    resolutions: list[ResourcePriceResolution] = []
    total = 0.0

    for usage in candidate.resources:
        if usage.is_abstract:
            resolutions.append(
                ResourcePriceResolution(
                    resource_code=usage.resource_code,
                    resource_name=usage.resource_name,
                    quantity=usage.quantity,
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
                    quantity=usage.quantity,
                    unit_price=None,
                    source="unresolved",
                )
            )
            continue

        resolution.resource_name = usage.resource_name
        resolution.quantity = usage.quantity

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
                        quantity=usage.quantity,
                        unit_price=None,
                        source="unresolved",
                    )
                )
                continue
            wage_addition = labour.labour_mach * wage_rate
            resolution.unit_price = resolution.unit_price + wage_addition  # type: ignore[operator]
            resolution.machinist_wage_added = wage_addition

        resolutions.append(resolution)
        total += resolution.unit_price * usage.quantity  # type: ignore[operator]

    priced = RegionalPriceResult(
        region_name=region_name, period_label=period_label, total_price=total, resolutions=resolutions
    )
    return replace(candidate, priced=priced)


def price_candidates_for_region(
    candidates: list[RateCandidate],
    region_name: str,
    period_label: str,
    current_prices: dict[str, float],
    gosr_index: dict[str, GosrIndexEntry],
    resource_base_prices: dict[str, float],
    machine_labour: dict[str, MachineLabourInfo] | None = None,
) -> list[RateCandidate]:
    return [
        price_candidate_for_region(
            c, region_name, period_label, current_prices, gosr_index, resource_base_prices, machine_labour
        )
        for c in candidates
    ]

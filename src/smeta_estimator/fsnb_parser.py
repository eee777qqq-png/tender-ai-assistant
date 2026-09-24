"""Разбор XML ФСНБ-2022 (ГЭСН + ФСБЦ) в структурированный вид.

Формат подтверждён на реальном файле, скачанном с fgiscs.minstroyrf.ru
2026-09-18 (не выдуман по описанию) — см. `docs/agent4-ai-matching-feasibility.md`.
Три функции разбора отдельно, потому что это три разных XML-схемы:

- `parse_gesn_xml` — `<Work>` внутри вложенных `<Section>` (Сборник → Раздел →
  ... → Таблица → NameGroup), с составом ресурсов на единицу работы.
- `parse_fsbc_materials_xml` — плоские `<Resource Code="..."><Prices><Price
  Cost="..."/>` для материалов/оборудования.
- `parse_fsbc_machines_xml` — машино-часы устроены иначе: цена не одним
  числом, а `SalaryMach` (зарплата машиниста) + `PriceCostWithoutSalary`
  (остальные затраты на машино-час). Возвращается **только
  `PriceCostWithoutSalary`** — не сумма. Подтверждено вживую 2026-09-18:
  ровно это число (без зарплаты машиниста) совпадает с «базисной ценой на
  01.01.2022 без учёта оплаты труда машинистов» в отчёте ГОСР
  (`regional_pricing_parser.parse_gosr_workbook`) — то есть это правильная
  базисная величина именно для последующего умножения на индекс ГОСР.
  Зарплата машиниста (`SalaryMach`) требует отдельного источника (таблица
  оплаты труда по регионам, как и трудозатраты рабочих) и в `base_price`
  не входит — см. CLAUDE.md, «Известные пробелы». Надбавка `WithRelocation`
  (перебазировка) — отдельный, более редкий случай, тоже не учтена.
- `parse_fsbc_machine_labour_xml` — тот же файл, что и выше, но
  извлекает `LabourMach`/`DriverCode` для добавки оплаты труда машиниста
  в `pricing.py` (см. `MachineLabourInfo` — логика подтверждена пока
  только устно, не письменно, см. CLAUDE.md).
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from .models import GesnResourceUsage, GesnWorkItem, MachineLabourInfo, MaterialCandidateInfo


def parse_gesn_xml(xml_bytes: bytes) -> list[GesnWorkItem]:
    root = ET.fromstring(xml_bytes)
    items: list[GesnWorkItem] = []
    _collect_work_items(root, table_name="", begin_name="", items=items)
    return items


def _collect_work_items(
    element: ET.Element, table_name: str, begin_name: str, items: list[GesnWorkItem]
) -> None:
    """Обходит дерево `<Section>` сверху вниз, донося вниз название текущей
    «Таблица» и текущего `NameGroup.BeginName» — контекст, из которого
    складывается полное название работы. Так вместо родительской оси (`..`,
    которую stdlib `ElementTree` не поддерживает) название собирается по
    пути вниз, а не поиском вверх от `<Work>`."""
    if element.tag == "Work":
        items.append(_build_work_item(element, table_name, begin_name))
        return

    next_table_name = table_name
    next_begin_name = begin_name
    if element.tag == "Section" and element.get("Type") == "Таблица":
        next_table_name = element.get("Name", "")
        next_begin_name = ""  # новая таблица — прошлый BeginName к ней не относится
    elif element.tag == "NameGroup":
        next_begin_name = element.get("BeginName", "")

    for child in element:
        _collect_work_items(child, next_table_name, next_begin_name, items)


def _build_work_item(work_el: ET.Element, table_name: str, begin_name: str) -> GesnWorkItem:
    resources: list[GesnResourceUsage] = []
    resources_el = work_el.find("Resources")
    if resources_el is not None:
        for res_el in resources_el.findall("Resource"):
            resources.append(
                GesnResourceUsage(
                    resource_code=res_el.get("Code", ""),
                    resource_name=res_el.get("EndName", ""),
                    quantity=_to_float(res_el.get("Quantity")),
                    is_abstract=False,
                )
            )
        for res_el in resources_el.findall("AbstractResource"):
            resources.append(
                GesnResourceUsage(
                    resource_code=res_el.get("Code", ""),
                    resource_name=res_el.get("Name", ""),
                    quantity=_to_float(res_el.get("Quantity")),
                    is_abstract=True,
                )
            )
    return GesnWorkItem(
        code=work_el.get("Code", ""),
        name=_compose_name(table_name, begin_name, work_el.get("EndName", "")),
        unit=work_el.get("MeasureUnit", ""),
        resources=resources,
    )


def _compose_name(table_name: str, begin_name: str, end_name: str) -> str:
    parts = [table_name]
    if begin_name and begin_name != table_name:
        parts.append(begin_name)
    if end_name:
        parts.append(end_name)
    return " — ".join(p for p in parts if p) or "(без названия)"


def parse_fsbc_materials_xml(xml_bytes: bytes) -> dict[str, float]:
    root = ET.fromstring(xml_bytes)
    prices: dict[str, float] = {}
    for res_el in root.iter("Resource"):
        code = res_el.get("Code")
        price_el = res_el.find("Prices/Price")
        if code and price_el is not None and price_el.get("Cost"):
            prices[code] = _to_float(price_el.get("Cost"))
    return prices


def parse_material_catalog_xml(xml_bytes: bytes) -> list[MaterialCandidateInfo]:
    """Тот же формат файла, что `parse_fsbc_materials_xml`, но сохраняет
    `Name`/`MeasureUnit`, а не только код->цена — нужны для подбора
    кандидатов-продуктов под категорию `AbstractResource` по совпадению
    названия (`material_candidates.suggest_material_candidates()`)."""
    root = ET.fromstring(xml_bytes)
    catalog: list[MaterialCandidateInfo] = []
    for res_el in root.iter("Resource"):
        code = res_el.get("Code")
        price_el = res_el.find("Prices/Price")
        if not code or price_el is None or not price_el.get("Cost"):
            continue
        catalog.append(
            MaterialCandidateInfo(
                code=code,
                name=res_el.get("Name", ""),
                unit=res_el.get("MeasureUnit", ""),
                price=_to_float(price_el.get("Cost")),
            )
        )
    return catalog


def parse_fsbc_machines_xml(xml_bytes: bytes) -> dict[str, float]:
    root = ET.fromstring(xml_bytes)
    prices: dict[str, float] = {}
    for res_el in root.iter("Resource"):
        code = res_el.get("Code")
        price_el = res_el.find("Prices/Price")
        if not code or price_el is None:
            continue
        prices[code] = _to_float(price_el.get("PriceCostWithoutSalary"))
    return prices


def parse_fsbc_machine_labour_xml(xml_bytes: bytes) -> dict[str, MachineLabourInfo]:
    """Тот же файл, что `parse_fsbc_machines_xml` — здесь вместо цены
    вытаскиваются `LabourMach`/`DriverCode`, нужные `pricing.
    price_candidate_for_region()` для добавки оплаты труда машиниста (см.
    `MachineLabourInfo`, включая пометку про устное/неписьменное
    подтверждение этой логики). `DriverCode` отсутствует у машин без
    отдельного оператора (например, самоходное электрическое оборудование)
    — в этом случае `driver_code=None`, что соответствует `LabourMach=0`."""
    root = ET.fromstring(xml_bytes)
    labour: dict[str, MachineLabourInfo] = {}
    for res_el in root.iter("Resource"):
        code = res_el.get("Code")
        price_el = res_el.find("Prices/Price")
        if not code or price_el is None:
            continue
        labour[code] = MachineLabourInfo(
            resource_code=code,
            labour_mach=_to_float(price_el.get("LabourMach")),
            driver_code=price_el.get("DriverCode") or None,
        )
    return labour


def apply_prices(work_items: list[GesnWorkItem], resource_prices: dict[str, float]) -> None:
    """Считает `base_price` каждой позиции — сумма `quantity * price` по
    ресурсам, для которых нашлась цена. Мутирует переданные `work_items`
    на месте (список большой — 30 000+ позиций, копировать накладно).

    Ресурс остаётся неоценённым (`unpriced_resource_codes`), если это не
    абстрактный ресурс и цены для его кода нет в `resource_prices` — típичный
    случай: коды трудозатрат (`1-100-38` и подобные, код "2"), для которых
    нужна отдельная таблица оплаты труда, не заведённая в этом фундаменте.
    """
    for item in work_items:
        base_price = 0.0
        unpriced: list[str] = []
        abstract: list[str] = []
        for usage in item.resources:
            if usage.is_abstract:
                abstract.append(usage.resource_code)
                continue
            price = resource_prices.get(usage.resource_code)
            if price is None:
                unpriced.append(usage.resource_code)
                continue
            base_price += usage.quantity * price
        item.base_price = base_price
        item.unpriced_resource_codes = unpriced
        item.abstract_resource_codes = abstract


def _to_float(raw: str | None) -> float:
    if not raw:
        return 0.0
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return 0.0

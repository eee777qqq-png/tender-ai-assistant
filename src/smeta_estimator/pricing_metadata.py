"""Метаданные источника расчёта сметы Агента 4 — на какой версии нормативной
базы и на каком региональном квартале/акте посчитана выдача (2026-10-03).

Раньше эти сведения жили только внутри расчёта: дважды расхождение с
реальной сметой (Краснодар — смета заказчика составлена по старому
региональному акту об оплате труда; транспорт/ПРР — искали не в том
справочнике) объясняли постфактум, а не потому что дата и акт были видны в
самой выдаче. Здесь они собираются в один структурированный блок
(`PricingMetadata.to_dict()` — готов для JSON, docx, веб-интерфейса) и
прикладываются к результату расчёта (`SmetaCostResult.pricing_metadata`,
вывод `estimate_smeta_document.py`). Чисто аддитивно: на цены не влияет.

Поля берутся из того, что расчёт и так получает:
- версия базы — атрибуты корня каждого XML архива ФСНБ-2022 (`BaseType`,
  `BaseName`, `CreationDate` — дата формирования файла в ИАС ЦС, не дата
  скачивания), URL архива — `fsnb_client.DEFAULT_ARCHIVE_URL`;
- регион и квартал — параметры запроса к ФГИС ЦС (те же, по которым берутся
  текущие цены, индексы ГОСР и ставки труда);
- региональный акт об оплате труда — единственная новая мелочь: имя файла из
  `RimWorkerSalaryRegistry/DocumentInfo` (то же, что ФГИС ЦС показывает
  ссылкой «Нормативный правовой акт об утверждении среднемесячного размера
  оплаты труда»). Для некоторых зон (например, г. Москва) акт там не
  опубликован — это явно записывается, не пустое поле;
- момент запроса текущих цен — время фактической загрузки `current_prices`.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

WAGE_ACT_NOT_PUBLISHED = "не опубликован во ФГИС ЦС для этой ценовой зоны"

_ATTR_RE = {
    "base_type": re.compile(r'BaseType="([^"]*)"'),
    "base_name": re.compile(r'BaseName="([^"]*)"'),
    "creation_date": re.compile(r'CreationDate="(\d{2})\.(\d{2})\.(\d{4})"'),
    "price_level": re.compile(r'PriceLevel="([^"]*)"'),
}
_HEADER_BYTES = 4000


@dataclass
class CatalogFileInfo:
    file: str
    base_type: str
    base_name: str
    creation_date: str  # ISO, дата формирования файла в ИАС ЦС
    price_level: str


@dataclass
class PricingMetadata:
    catalog_source: str
    catalog_files: list[CatalogFileInfo]
    catalog_version_date: str
    catalog_archive_url: str
    region: str
    region_index_period: str
    wage_act: str
    fgiscs_price_fetched_at: str
    price_zone_id: int
    period_id: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_catalog_header(file_name: str, xml_bytes: bytes) -> CatalogFileInfo:
    """Атрибуты корня `<base ...>` файла сборника (читаются только первые
    несколько КБ — файлы до 85 МБ целиком для этого не разбираем)."""
    head = xml_bytes[:_HEADER_BYTES].decode("utf-8", errors="ignore")
    found = {k: rx.search(head) for k, rx in _ATTR_RE.items()}
    if found["creation_date"] is None or found["base_type"] is None:
        raise ValueError(f"В заголовке {file_name} нет BaseType/CreationDate — формат архива изменился")
    d, m, y = found["creation_date"].groups()
    return CatalogFileInfo(
        file=file_name,
        base_type=found["base_type"].group(1),
        base_name=found["base_name"].group(1) if found["base_name"] else "",
        creation_date=f"{y}-{m}-{d}",
        price_level=found["price_level"].group(1) if found["price_level"] else "",
    )


def build_pricing_metadata(
    catalog_files: dict[str, bytes],
    archive_url: str,
    region: str,
    period_label: str,
    period_id: int,
    price_zone_id: int,
    wage_act_file_name: str | None,
    price_fetched_at: datetime,
) -> PricingMetadata:
    """`catalog_files` — `{имя файла: байты}` реально подключённых сборников
    (ГЭСН/ГЭСНр/ГЭСНм); `catalog_source` называет сборники норм."""
    infos = [parse_catalog_header(name, data) for name, data in sorted(catalog_files.items())]
    norm_types = [i.base_type for i in infos if i.base_type.startswith("ГЭСН")]
    notes: list[str] = []
    if wage_act_file_name is None:
        notes.append(
            "Акт об оплате труда для этой ценовой зоны во ФГИС ЦС не опубликован — ставки труда взяты как есть."
        )
    return PricingMetadata(
        catalog_source="ФСНБ-2022 (" + ", ".join(norm_types) + ")" if norm_types else "ФСНБ-2022",
        catalog_files=infos,
        catalog_version_date=max(i.creation_date for i in infos),
        catalog_archive_url=archive_url,
        region=region,
        region_index_period=period_label,
        wage_act=wage_act_file_name or WAGE_ACT_NOT_PUBLISHED,
        fgiscs_price_fetched_at=price_fetched_at.isoformat(timespec="seconds"),
        price_zone_id=price_zone_id,
        period_id=period_id,
        notes=notes,
    )

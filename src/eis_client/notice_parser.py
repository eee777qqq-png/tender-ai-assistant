"""Извлечение полей `Tender` из реального ИЗВЕЩЕНИЯ ЕИС (epNotification*),
не из контракта — см. `tender_adapter.py` про разницу между ними.

Пути подтверждены Edwin на реальном документе, 2026-09-23: извещение
`0373200298826000007` (стройка), полученное реальным запросом с
`EIS_SUBSYSTEM_TYPE=PRIZ` + `EIS_DOCUMENT_TYPE44=epNotificationEF2020`
(электронный аукцион) — см. CLAUDE.md, «Известные пробелы» → «Решено».

Тот же принцип, что и у `client._find_okpd2_codes`/`_find_reestr_number`:
путь тега (без учёта регистра/namespace), а не имя тега — извещение и
контракт используют совсем разные схемы, поэтому это отдельный модуль, не
расширение той же эвристики.

`requires_sro`/`min_experience_years` — не отдельные структурированные
поля, а короткий текст внутри `addRequirement/content` (до нескольких
предложений). Разбирается той же логикой, что и Агент 3
(`document_analyst.extractor.mentions_sro_requirement`/
`extract_min_experience_years`) — не отдельной новой эвристикой.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from xml.etree import ElementTree as ET

from document_analyst.extractor import extract_min_experience_years, mentions_sro_requirement

# Суффиксы путей — последние звенья, без учёта регистра/namespace, тот же
# приём, что у `client._OKPD2_PATH_SUFFIX`/`_REESTR_NUMBER_PATH_SUFFIX`.
_PURCHASE_NUMBER_PATH_SUFFIX = ("commoninfo", "purchasenumber")
_NAME_PATH_SUFFIX = ("purchaseobjectsinfo", "notdrugpurchaseobjectsinfo", "purchaseobject", "name")
_CUSTOMER_NAME_PATH_SUFFIX = ("purchaseresponsibleinfo", "responsibleorginfo", "fullname")
_MAX_PRICE_PATH_SUFFIX = ("notificationinfo", "contractconditionsinfo", "maxpriceinfo", "maxprice")
_SUBMISSION_DEADLINE_PATH_SUFFIX = ("notificationinfo", "procedureinfo", "collectinginfo", "enddt")
_REQUIREMENT_TEXT_PATH_SUFFIX = (
    "notificationinfo",
    "requirementsinfo",
    "requirementinfo",
    "addrequirements",
    "addrequirement",
    "content",
)

# Находка 2026-09-23, НЕ реализовано — см. CLAUDE.md, «Известные пробелы»:
# извещение содержит структурированные суммы обеспечения заявки и гарантийных
# обязательств отдельными полями (не текстом, как СРО/опыт):
#   applicationGuarantee/amount, provisionWarranty/amount
# Потенциально закрывает то же самое, что платный источник МультиТендер
# должен был дать. Пути НЕ проверены на извлечение (только замечены в
# структуре реального документа), суффиксы здесь не заведены намеренно —
# отдельная задача, когда до неё дойдёт очередь.


@dataclass
class NoticeFields:
    """Поля `Tender`, извлечённые из реального извещения — то, что удалось
    найти структурно/текстом, честно `None`/дефолт, если не нашлось.

    НЕ входят (не подтверждены на этом документе, не угадываются):
    `okpd2_code`, `region_code`, `publish_date` — см. `tender_adapter.py`."""

    purchase_number: str | None = None
    name: str | None = None
    customer_name: str | None = None
    max_price: float | None = None
    submission_deadline: date | None = None
    requires_sro: bool = False
    min_experience_years: int = 0
    # Тексты доп. требований, из которых искали СРО/опыт — для прозрачности
    # перед экспертом (мог быть текст, а `mentions_sro_requirement`/
    # `extract_min_experience_years` его не распознали — другая формулировка).
    requirement_texts: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _collect_by_path_suffix(root: ET.Element, suffix: tuple[str, ...]) -> list[str]:
    """Текстовые значения всех тегов, чей путь (в нижнем регистре, без
    namespace) оканчивается на `suffix` — может быть несколько совпадений
    (например, несколько `addRequirement`)."""
    values: list[str] = []

    def walk(el: ET.Element, path: tuple[str, ...]) -> None:
        path = path + (_local(el.tag).lower(),)
        if path[-len(suffix) :] == suffix:
            text = (el.text or "").strip()
            if text:
                values.append(text)
        for child in el:
            walk(child, path)

    walk(root, ())
    return values


def _first(root: ET.Element, suffix: tuple[str, ...]) -> str | None:
    values = _collect_by_path_suffix(root, suffix)
    return values[0] if values else None


def _parse_price(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _parse_deadline(raw: str) -> date | None:
    # `endDT` в примере — datetime вида "2026-09-21T10:00:00"; на случай
    # чистой даты ("2026-09-21") пробуем оба формата, не падаем на первом.
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    return None


def extract_notice_fields(xml_bytes: bytes) -> NoticeFields:
    """Разбирает XML извещения (`epNotification*`) в `NoticeFields`.

    Не бросает исключений на неразобранном XML — возвращает пустой
    `NoticeFields` (все поля `None`/дефолт), тем же принципом, что
    `client._find_okpd2_codes` возвращает `[]`, а не падает."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return NoticeFields()

    max_price_raw = _first(root, _MAX_PRICE_PATH_SUFFIX)
    deadline_raw = _first(root, _SUBMISSION_DEADLINE_PATH_SUFFIX)
    requirement_texts = _collect_by_path_suffix(root, _REQUIREMENT_TEXT_PATH_SUFFIX)

    return NoticeFields(
        purchase_number=_first(root, _PURCHASE_NUMBER_PATH_SUFFIX),
        name=_first(root, _NAME_PATH_SUFFIX),
        customer_name=_first(root, _CUSTOMER_NAME_PATH_SUFFIX),
        max_price=_parse_price(max_price_raw) if max_price_raw else None,
        submission_deadline=_parse_deadline(deadline_raw) if deadline_raw else None,
        requires_sro=any(mentions_sro_requirement(text) for text in requirement_texts),
        min_experience_years=max(
            (extract_min_experience_years(text) or 0 for text in requirement_texts), default=0
        ),
        requirement_texts=requirement_texts,
    )

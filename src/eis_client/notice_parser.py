"""Извлечение полей ИЗВЕЩЕНИЯ ЕИС (epNotificationEF2020, subsystemType=PRIZ) —
region_code, publish_date и три вида обеспечения (заявки, исполнения
контракта, гарантийных обязательств).

Отдельно от `client.py`: там — поля, общие для КОНТРАКТОВ и извещений
(ОКПД2, реестровый номер закупки), здесь — поля, структура которых
подтверждена только на ИЗВЕЩЕНИЯХ. Гипотеза из CLAUDE.md («Известные
пробелы», п.3) — `EIS_SUBSYSTEM_TYPE=PRIZ`, `EIS_DOCUMENT_TYPE44=epNotificationEF2020`
— подтверждена реальным запросом 2026-09-24, с первой попытки, без
SOAP-фолта: 29 строительных извещений за один день по Москве (org_region=77),
9 — по Ростовской области (org_region=61). Пути ниже подтверждены на реальных
извещениях из обоих запросов.

**Этот модуль НЕ подключён к основному конвейеру Агента 1** (`client.py`,
`get_construction_documents()`) — тот по-прежнему по умолчанию запрашивает
КОНТРАКТЫ (`.env`: `EIS_SUBSYSTEM_TYPE=RGK`, `EIS_DOCUMENT_TYPE44=contract`).
Переход всего монитора на извещения (что закрыло бы куда больше полей
`Tender`, чем только region_code/publish_date — извещение структурно несёт
почти всё: name, customer_name, max_price, submission_deadline и т. д.) —
отдельное решение владельца, см. CLAUDE.md, «Известные пробелы», п.14.
Здесь — только то, что явно просили подключить в этой сессии.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree as ET

from document_analyst.models import SecurityRequirement

# commonInfo/plannedPublishDate = "2026-09-23+03:00" на реальном документе —
# дата с часовым поясом, без времени. Берём только дату (до "+"/"T").
_PUBLISH_DATE_PATH_SUFFIX = ("commoninfo", "plannedpublishdate")
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# region_code НЕ лежит в извещении отдельным тегом «код региона» — такого
# тега нет вообще (проверено на реальных документах обоих регионов,
# 2026-09-24). Вместо этого — первые 2 цифры ИНН заказчика
# (purchaseResponsibleInfo/responsibleOrgInfo/INN): по правилам ФНС для
# юрлиц первые 2 цифры 10-значного ИНН — код субъекта РФ, выдавшего ИНН, и
# это ТА ЖЕ схема кодов (77/50/23/61…), что уже используется в проекте
# (`classifier.tender.Tender.region_code`, `EIS_ORG_REGION`). Подтверждено
# на двух реальных извещениях: запрос с org_region=77 (Москва) вернул ИНН с
# префиксом "77", запрос с org_region=61 (Ростовская область) — ИНН с
# префиксом "61".
#
# ВАЖНАЯ ОГОВОРКА: это регион РЕГИСТРАЦИИ заказчика, не обязательно регион
# ИСПОЛНЕНИЯ контракта — для местных/муниципальных заказчиков (обычный
# случай для капремонта/строительства) это, как правило, одно и то же, но
# не гарантировано (например, заказчик с ИНН "77" может объявлять закупку
# на исполнение в другом регионе). Фактическое место исполнения лежит в
# `contractConditionsInfo/deliveryPlacesInfo/byGARInfo/GARInfo/GARAddress`
# (подтверждено на обоих документах — формат ГАР: тип места ПЕРЕД
# названием, например "г. <город>, …" или "обл. <область>, м.р-н <район>,
# …"; реальные названия из документов здесь не приводятся) — но это
# неструктурированный адрес, разбор текстом не реализован, менее надёжен
# для честного `None`, чем префикс ИНН.
_CUSTOMER_INN_PATH_SUFFIX = ("responsibleorginfo", "inn")
_INN_LEGAL_ENTITY_RE = re.compile(r"^\d{10}$")

# Обеспечение — три разных требования в извещении, разные теги:
# applicationGuarantee (заявки), contractGuarantee (исполнения контракта),
# provisionWarranty (гарантийных обязательств). Подтверждено на реальных
# извещениях обоих регионов, 2026-09-24. `contractGuarantee/amount` в одном
# из двух реальных документов (Москва) отсутствовал вовсе, хотя `part`
# (процент) был — похоже на то, что для этой конкретной закупки обеспечение
# исполнения контракта не требовалось и потому не было выгружено с суммой;
# во втором документе (Ростовская область) `amount` присутствовал. Значит
# это не пробел парсинга — честный `None`, когда тега нет, см.
# `NoticeSecurityAmounts`.
_APPLICATION_GUARANTEE_AMOUNT_SUFFIX = ("applicationguarantee", "amount")
_APPLICATION_GUARANTEE_PART_SUFFIX = ("applicationguarantee", "part")
_CONTRACT_GUARANTEE_AMOUNT_SUFFIX = ("contractguarantee", "amount")
_CONTRACT_GUARANTEE_PART_SUFFIX = ("contractguarantee", "part")
_PROVISION_WARRANTY_AMOUNT_SUFFIX = ("provisionwarranty", "amount")
_PROVISION_WARRANTY_PART_SUFFIX = ("provisionwarranty", "part")

_NUMBER_RE = re.compile(r"^\d+(\.\d+)?$")


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_by_path_suffix(root: ET.Element, suffix: tuple[str, ...]) -> str | None:
    """Первое значение тега, чей путь (без namespace, без учёта регистра)
    оканчивается на `suffix`. `None`, если не нашлось — честно, не заглушка."""

    def walk(el: ET.Element, path: tuple[str, ...]) -> str | None:
        path = path + (_local(el.tag).lower(),)
        if path[-len(suffix) :] == suffix:
            value = (el.text or "").strip()
            if value:
                return value
        for child in el:
            found = walk(child, path)
            if found is not None:
                return found
        return None

    return walk(root, ())


def _find_number(root: ET.Element, suffix: tuple[str, ...]) -> float | None:
    value = _find_by_path_suffix(root, suffix)
    if value is None or not _NUMBER_RE.match(value):
        return None
    return float(value)


@dataclass
class NoticeSecurityAmounts:
    """Обеспечение по извещению — суммы и проценты по трём видам. `None` у
    любого поля значит, что тега нет в документе (обеспечение не требуется
    для этой конкретной закупки или не указано) — не ошибка парсинга."""

    bid_amount: float | None = None
    bid_percentage: float | None = None
    contract_amount: float | None = None
    contract_percentage: float | None = None
    warranty_amount: float | None = None
    warranty_percentage: float | None = None


def extract_publish_date(xml_bytes: bytes) -> date | None:
    """Дата публикации извещения — `commonInfo/plannedPublishDate`,
    подтверждено на реальном документе, 2026-09-24. `None`, если тега нет
    или значение не разбирается как дата — честно, не сегодняшняя дата
    по умолчанию."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    raw = _find_by_path_suffix(root, _PUBLISH_DATE_PATH_SUFFIX)
    if raw is None:
        return None
    match = _DATE_PREFIX_RE.match(raw)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def extract_region_code(xml_bytes: bytes) -> str | None:
    """Код региона — из первых 2 цифр ИНН заказчика (см. докстринг модуля
    про схему кодов ФНС и оговорку про регион исполнения vs регистрации).
    `None`, если ИНН не нашёлся или не похож на 10-значный ИНН юрлица."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    inn = _find_by_path_suffix(root, _CUSTOMER_INN_PATH_SUFFIX)
    if inn is None or not _INN_LEGAL_ENTITY_RE.match(inn):
        return None
    return inn[:2]


def extract_security_amounts(xml_bytes: bytes) -> NoticeSecurityAmounts:
    """Суммы/проценты обеспечения заявки, исполнения контракта и
    гарантийных обязательств — см. `NoticeSecurityAmounts`."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return NoticeSecurityAmounts()

    return NoticeSecurityAmounts(
        bid_amount=_find_number(root, _APPLICATION_GUARANTEE_AMOUNT_SUFFIX),
        bid_percentage=_find_number(root, _APPLICATION_GUARANTEE_PART_SUFFIX),
        contract_amount=_find_number(root, _CONTRACT_GUARANTEE_AMOUNT_SUFFIX),
        contract_percentage=_find_number(root, _CONTRACT_GUARANTEE_PART_SUFFIX),
        warranty_amount=_find_number(root, _PROVISION_WARRANTY_AMOUNT_SUFFIX),
        warranty_percentage=_find_number(root, _PROVISION_WARRANTY_PART_SUFFIX),
    )


def security_amounts_to_requirements(amounts: NoticeSecurityAmounts) -> list[SecurityRequirement]:
    """Переходник в `document_analyst.models.SecurityRequirement` (Агент 3) —
    та же форма, что уже используется для требований, извлечённых Агентом 3
    из ТЕКСТА документации; здесь источник — структурированные поля
    ИЗВЕЩЕНИЯ, не текст. `kind="warranty"` — новое значение (раньше Агент 3
    строил только "bid"/"contract" из ключевых слов текста) — обеспечение
    гарантийных обязательств структурно есть в извещении отдельным
    разделом; в тексте документации закупки его тоже стоит поискать
    отдельно, чего эвристика Агента 3 сегодня не делает (см. CLAUDE.md,
    «Известные пробелы», п.5, про метод извлечения).

    Требование попадает в список, только если для него нашлась хотя бы
    сумма или процент — не создаёт пустых записей "на всякий случай"."""
    requirements: list[SecurityRequirement] = []
    if amounts.bid_amount is not None or amounts.bid_percentage is not None:
        requirements.append(
            SecurityRequirement(
                kind="bid",
                percentage=amounts.bid_percentage,
                amount=amounts.bid_amount,
                raw_text="applicationGuarantee (извещение ЕИС)",
            )
        )
    if amounts.contract_amount is not None or amounts.contract_percentage is not None:
        requirements.append(
            SecurityRequirement(
                kind="contract",
                percentage=amounts.contract_percentage,
                amount=amounts.contract_amount,
                raw_text="contractGuarantee (извещение ЕИС)",
            )
        )
    if amounts.warranty_amount is not None or amounts.warranty_percentage is not None:
        requirements.append(
            SecurityRequirement(
                kind="warranty",
                percentage=amounts.warranty_percentage,
                amount=amounts.warranty_amount,
                raw_text="provisionWarranty (извещение ЕИС)",
            )
        )
    return requirements

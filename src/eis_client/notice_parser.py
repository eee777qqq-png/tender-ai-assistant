"""Извлечение полей ИЗВЕЩЕНИЯ ЕИС (epNotificationEF2020, subsystemType=PRIZ) —
region_code, publish_date, три вида обеспечения, и (с 2026-09-24 вечером)
остальные поля `Tender` — вплоть до готового `notice_document_to_tender()`.

Отдельно от `client.py`: там — поля, общие для КОНТРАКТОВ и извещений
(ОКПД2, реестровый номер закупки), здесь — поля, структура которых
подтверждена только на ИЗВЕЩЕНИЯХ. Гипотеза из CLAUDE.md («Известные
пробелы», п.3) — `EIS_SUBSYSTEM_TYPE=PRIZ`, `EIS_DOCUMENT_TYPE44=epNotificationEF2020`
— подтверждена реальным запросом 2026-09-24, с первой попытки, без
SOAP-фолта: 29 строительных извещений за один день по Москве (org_region=77),
9 — по Ростовской области (org_region=61). Пути ниже подтверждены на реальных
извещениях из обоих запросов.

**`notice_document_to_tender()` (добавлено 2026-09-24 вечером) замыкает
Агента 1 на Агента 2 на реальных данных** — 8 из 10 полей `Tender` строятся
из самого извещения (окпд2/номер закупки — переиспользуют `EISClient`;
название/заказчик/НМЦК/срок подачи — новые пути ниже; регион/дата публикации
— уже были). Только `requires_sro`/`min_experience_years` остаются
обязательными параметрами вызова — на 463 реальных извещениях (Москва +
Ростовская область, 2026-09-24) не нашлось НИ ОДНОГО тега, похожего на
допуск СРО или минимальный опыт (см. докстринг самой функции) — честно, не
потому что не искали.

**Этот модуль по-прежнему НЕ подключён к основному конвейеру `run_monitor.py`**
(тот работает с КОНТРАКТАМИ по умолчанию, `.env`: `EIS_SUBSYSTEM_TYPE=RGK`,
`EIS_DOCUMENT_TYPE44=contract`) — но появился отдельный оркестрирующий
скрипт `src/match_real_notices.py`, который запрашивает ИЗВЕЩЕНИЯ живьём и
реально вызывает `notice_document_to_tender()` → `match_profile_to_tender()`
на настоящих документах. Переход самого `run_monitor.py`/`.env` на извещения
по умолчанию — по-прежнему отдельное решение владельца, см. CLAUDE.md,
«Известные пробелы», п.13.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree as ET

from classifier.tender import Tender
from document_analyst.models import SecurityRequirement

from .client import EISClient

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

# Номер закупки в ИЗВЕЩЕНИИ лежит по-другому, чем в контракте: не
# .../foundation/fcsOrder/order/notificationNumber (тот путь — эхо контракта
# на извещение, которого он касается, `EISClient._find_reestr_number()`
# честно возвращает `None` на реальном извещении — проверено, 2026-09-24),
# а прямо в `commonInfo/purchaseNumber` — ровно тот же реестровый номер (19
# цифр). Специально матчим по 2 звеньям пути (`commoninfo`, не просто
# `purchasenumber`) — в документе есть ВТОРОЙ, не связанный тег с тем же
# именем на 4 цифра короче (`contractConditionsInfo/IKZInfo/purchaseNumber`,
# часть кода ИКЗ) — без привязки к `commonInfo` матчер словил бы его первым
# и вернул неверное значение.
_PURCHASE_NUMBER_PATH_SUFFIX = ("commoninfo", "purchasenumber")
_PURCHASE_NUMBER_VALUE_RE = re.compile(r"^\d{15,25}$")

# Название объекта закупки — подтверждено на реальном извещении, 2026-09-24:
# .../purchaseObjectsInfo/notDrugPurchaseObjectsInfo/purchaseObject/name.
# Специально 3 звена пути — тег `name` в документе встречается десятки раз
# (shortName заказчика, названия КВР, ОКТМО и т. д.), без точной привязки к
# структуре нашёлся бы не тот `name`.
_NAME_PATH_SUFFIX = ("notdrugpurchaseobjectsinfo", "purchaseobject", "name")

# Заказчик — .../customerRequirementInfo/customer/fullName, подтверждено на
# реальном извещении. НЕ `purchaseResponsibleInfo/responsibleOrgInfo/fullName`
# — это уполномоченное учреждение, которое ведёт закупку (например,
# технический центр департамента), не сам заказчик по существу; в
# проверенном документе это разные организации.
_CUSTOMER_NAME_PATH_SUFFIX = ("customer", "fullname")

# НМЦК — .../contractConditionsInfo/maxPriceInfo/maxPrice, подтверждено на
# реальном извещении (совпадает с уже проверенным путём для обеспечения).
_MAX_PRICE_PATH_SUFFIX = ("maxpriceinfo", "maxprice")

# Срок подачи заявок — .../procedureInfo/collectingInfo/endDT, подтверждено
# на реальном извещении (значение вида "2026-10-01T10:00:00+03:00" — берём
# только дату, тем же `_DATE_PREFIX_RE`, что и для publish_date).
_SUBMISSION_DEADLINE_PATH_SUFFIX = ("collectinginfo", "enddt")

# Приложения извещения (техзадание/проект контракта/требования к заявке и
# т. п.) — attachmentsInfo/attachmentInfo, подтверждено на реальном
# извещении №0373100134626000473, 2026-09-25 (см. CLAUDE.md, «Известные
# пробелы», п.10 и п.3). У каждого attachmentInfo — fileName, fileSize,
# url (публичный, `zakupki.gov.ru/44fz/filestore/...`, не тот же сервис,
# что архивы) и docKindInfo/code — машиночитаемый тип документа (не текст
# названия файла, который произволен от заказчика к заказчику). Коды,
# подтверждённые на этом одном документе: MRJ — обоснование НМЦК, CP —
# проект контракта, POD — описание объекта закупки, CAR — требование к
# содержанию/составу заявки (это и есть источник требований к участнику —
# СРО/опыт — для Агента 3). Стабильность этих кодов на ДРУГИХ извещениях
# не проверена (один документ — не статистика), но код по смыслу — часть
# официального справочника видов документов ЕИС, надёжнее, чем парсинг
# по названию файла (то произвольный текст от заказчика).
_ATTACHMENT_PATH = ("attachmentsinfo", "attachmentinfo")
_FILE_NAME_TAG = "filename"
_FILE_SIZE_TAG = "filesize"
_URL_TAG = "url"
_DOC_KIND_INFO_TAG = "dockindinfo"
_DOC_KIND_CODE_TAG = "code"
_DOC_KIND_NAME_TAG = "name"

# Требование к содержанию/составу заявки — тот вид документа, где на
# реальном извещении нашлось требование к опыту участника (см. CLAUDE.md,
# открытый п.3, находка 2026-09-25/26).
APPLICATION_REQUIREMENTS_DOC_KIND_CODE = "CAR"


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


@dataclass
class NoticeAttachment:
    """Одно приложение извещения (файл + его тип по справочнику ЕИС).

    `url` — публичная ссылка на скачивание (`zakupki.gov.ru/44fz/filestore/...`),
    не тот сервис, что архивы (`int.zakupki.gov.ru/dstore/...`) — качается
    `EISClient.download_attachment()`, не `download_archive()`."""

    file_name: str
    file_size: int | None
    url: str
    doc_kind_code: str | None
    doc_kind_name: str | None


def extract_attachments(xml_bytes: bytes) -> list[NoticeAttachment]:
    """Список приложений извещения — см. `NoticeAttachment` и заметку про
    `_ATTACHMENT_PATH`/коды `docKindInfo` выше. Пустой список, если приложений
    нет или документ не разбирается — честно, не ошибка."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    attachments: list[NoticeAttachment] = []

    def walk(el: ET.Element) -> None:
        if _local(el.tag) == "attachmentInfo":
            attachments.append(_parse_attachment(el))
            return  # attachmentInfo не бывает вложен сам в себя
        for child in el:
            walk(child)

    walk(root)
    return attachments


def _parse_attachment(el: ET.Element) -> NoticeAttachment:
    file_name = ""
    file_size: int | None = None
    url = ""
    doc_kind_code: str | None = None
    doc_kind_name: str | None = None

    for child in el:
        tag = _local(child.tag)
        if tag == "fileName":
            file_name = (child.text or "").strip()
        elif tag == "fileSize":
            raw = (child.text or "").strip()
            file_size = int(raw) if raw.isdigit() else None
        elif tag == "url":
            url = (child.text or "").strip()
        elif tag == "docKindInfo":
            for grandchild in child:
                gtag = _local(grandchild.tag)
                if gtag == "code":
                    doc_kind_code = (grandchild.text or "").strip() or None
                elif gtag == "name":
                    doc_kind_name = (grandchild.text or "").strip() or None
        # cryptoSigns и прочее — сознательно игнорируется, не нужно Агенту 3

    return NoticeAttachment(
        file_name=file_name,
        file_size=file_size,
        url=url,
        doc_kind_code=doc_kind_code,
        doc_kind_name=doc_kind_name,
    )


def find_attachment_by_doc_kind(
    attachments: list[NoticeAttachment], doc_kind_code: str
) -> NoticeAttachment | None:
    """Первое приложение с точным совпадением `doc_kind_code` (например,
    `APPLICATION_REQUIREMENTS_DOC_KIND_CODE`). `None`, если такого нет —
    честно, а не первое попавшееся приложение наугад."""
    return next((a for a in attachments if a.doc_kind_code == doc_kind_code), None)


def extract_purchase_number(xml_bytes: bytes) -> str | None:
    """Реестровый номер закупки — `commonInfo/purchaseNumber` (не тот путь,
    что у контракта, см. `_PURCHASE_NUMBER_PATH_SUFFIX`). `None`, если не
    нашёлся или не похож на реестровый номер (15–25 цифр)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    value = _find_by_path_suffix(root, _PURCHASE_NUMBER_PATH_SUFFIX)
    if value is None or not _PURCHASE_NUMBER_VALUE_RE.match(value):
        return None
    return value


def extract_name(xml_bytes: bytes) -> str | None:
    """Название объекта закупки — см. `_NAME_PATH_SUFFIX`."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    return _find_by_path_suffix(root, _NAME_PATH_SUFFIX)


def extract_customer_name(xml_bytes: bytes) -> str | None:
    """Заказчик (не уполномоченное учреждение) — см. `_CUSTOMER_NAME_PATH_SUFFIX`."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    return _find_by_path_suffix(root, _CUSTOMER_NAME_PATH_SUFFIX)


def extract_max_price(xml_bytes: bytes) -> float | None:
    """НМЦК извещения — см. `_MAX_PRICE_PATH_SUFFIX`."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    return _find_number(root, _MAX_PRICE_PATH_SUFFIX)


def extract_submission_deadline(xml_bytes: bytes) -> date | None:
    """Срок подачи заявок — см. `_SUBMISSION_DEADLINE_PATH_SUFFIX`."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    raw = _find_by_path_suffix(root, _SUBMISSION_DEADLINE_PATH_SUFFIX)
    if raw is None:
        return None
    match = _DATE_PREFIX_RE.match(raw)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def notice_document_to_tender(xml_bytes: bytes, *, requires_sro: bool, min_experience_years: int) -> Tender:
    """Строит `Tender` НАПРЯМУЮ из реального извещения ЕИС — в отличие от
    `tender_adapter.document_to_tender()` (который берёт `ConstructionDocument`
    от реестра КОНТРАКТОВ и требует все 8 недостающих полей явными
    параметрами), эта функция сама извлекает 8 из 10 полей `Tender` из
    самого документа: `okpd2_code`/`purchase_number` — переиспользует
    `EISClient._find_okpd2_codes()`/новый `extract_purchase_number()`;
    `name`/`customer_name`/`max_price`/`submission_deadline` — новые пути
    выше; `region_code`/`publish_date` — уже существующие `extract_region_code()`/
    `extract_publish_date()`.

    **`requires_sro`/`min_experience_years` остаются обязательными
    параметрами, не заглушкой.** Проверено на 463 реальных извещениях
    (Москва + Ростовская область, 2026-09-24, см. `docs`/CLAUDE.md) — ни
    одного тега, похожего на допуск СРО или минимальный опыт участника, не
    нашлось нигде. Это может значить: (а) для этих конкретных закупок
    (текущий ремонт, не капстроительство) доп. требований по ч.1.1 ст.31
    44-ФЗ нет — они встречаются как категория `requirementsInfo` со ссылкой
    на статью закона (`ET44`/`TR442` — общие требования, не параметризованные
    цифрой лет или флагом СРО), но САМИ цифры/флаг, если и есть, не найдены
    в структуре; или (б) они лежат в приложенном PDF/DOCX-документе
    (`attachmentsInfo`, например «Требование к содержанию, составу заявки»),
    не в самом XML — тогда это не задача XML-парсера вообще. Не додумано —
    честный `ValueError` был бы неверным решением здесь (в отличие от
    остальных полей — эти два физически не подставить заглушкой 0/False,
    не исказив матчинг Агента 2, `classifier.matching.match_profile_to_tender()`
    использует их напрямую), поэтому вызывающий код обязан передать их сам.

    Отказывает (`ValueError`) с точным указанием поля, если хоть одно из
    автоматически извлекаемых 8 полей не нашлось — не подставляет заглушку."""
    try:
        ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"Документ не разбирается как XML: {exc}") from exc

    okpd2_codes = EISClient._find_okpd2_codes(xml_bytes)
    if not okpd2_codes:
        raise ValueError("В извещении не нашлось ни одного кода ОКПД2")

    purchase_number = extract_purchase_number(xml_bytes)
    if purchase_number is None:
        raise ValueError("В извещении не нашёлся реестровый номер закупки (commonInfo/purchaseNumber)")

    name = extract_name(xml_bytes)
    if name is None:
        raise ValueError("В извещении не нашлось название объекта закупки (purchaseObject/name)")

    customer_name = extract_customer_name(xml_bytes)
    if customer_name is None:
        raise ValueError("В извещении не нашлось имя заказчика (customerRequirementInfo/customer/fullName)")

    region_code = extract_region_code(xml_bytes)
    if region_code is None:
        raise ValueError("Не удалось вывести регион из ИНН заказчика (responsibleOrgInfo/INN)")

    max_price = extract_max_price(xml_bytes)
    if max_price is None:
        raise ValueError("В извещении не нашлась НМЦК (contractConditionsInfo/maxPriceInfo/maxPrice)")

    publish_date = extract_publish_date(xml_bytes)
    if publish_date is None:
        raise ValueError("В извещении не нашлась дата публикации (commonInfo/plannedPublishDate)")

    submission_deadline = extract_submission_deadline(xml_bytes)
    if submission_deadline is None:
        raise ValueError("В извещении не нашёлся срок подачи заявок (procedureInfo/collectingInfo/endDT)")

    return Tender(
        purchase_number=purchase_number,
        name=name,
        customer_name=customer_name,
        okpd2_code=okpd2_codes[0],
        region_code=region_code,
        max_price=max_price,
        requires_sro=requires_sro,
        min_experience_years=min_experience_years,
        publish_date=publish_date,
        submission_deadline=submission_deadline,
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

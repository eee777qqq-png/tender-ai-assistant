"""Переходник Агент 1 -> Агент 2: `ConstructionDocument` (что реально
парсит монитор) -> `classifier.tender.Tender` (что требует конвейер
дальше — Агенты 2/3/4/5/6/7/8).

До этого модуля (ревизия проекта, 2026-09-21) такого кода не было вообще:
типы были совместимы только теоретически — если бы ЕИС начал отвечать
реальными данными завтра, выход монитора всё равно физически не мог бы
попасть в `match_profile_to_tender()` и далее, потому что не существовало
функции, которая строит `Tender` из того, что реально возвращает Агент 1.

**Честная граница того, что здесь реально можно сделать — и почему это
структурное ограничение источника, не пробел парсинга.** `Tender` требует
10 полей; `ConstructionDocument` (собирается из РЕЕСТРА КОНТРАКТОВ,
`documentType44=contract`) даёт основания только для двух:
- `okpd2_code` — из `okpd2_codes[0]` (список уже отфильтрован
  `ConstructionClassifier` при сборе документа), путь подтверждён на
  реальном документе 2026-09-23 (`KTRU/OKPD2/code`);
- `purchase_number` — из `reestr_number` (`client._find_reestr_number()`),
  путь тоже подтверждён на реальном документе 2026-09-23
  (`foundation/fcsOrder/order/notificationNumber`) — это номер ИЗВЕЩЕНИЯ,
  на основании которого заключён контракт, а не номер самого контракта.

Остальные 8 полей разбиваются на две категории, а не одну — см. диагноз
2026-09-23 на реальном контракте (`contract_2770206615726000446`):

- **Частично покрыты контрактом, но не тем же полем, что в `Tender`:**
  `contractSubject` (≈`name`), `customer/fullName` (≈`customer_name`),
  `priceInfo/price` (похоже на `max_price`, но это цена уже ЗАКЛЮЧЁННОГО
  контракта, не НМЦК извещения — могут отличаться),
  `executionPeriod/startDate`+`endDate` (сроки ИСПОЛНЕНИЯ контракта, не
  срок подачи заявки — `submission_deadline` это другое). Даже эти поля
  сейчас не извлекаются автоматически — соответствие полю `Tender` не
  однозначное 1:1, нужно решить, что с этим делать (не сделано).
- **В контракте нет вообще, структурно:** `requires_sro`,
  `min_experience_years`, `submission_deadline` — это требования и сроки
  ИЗВЕЩЕНИЯ (объявления о закупке, ещё не разыгранной), а не контракта
  (уже заключённой сделки после определения победителя). Реестр контрактов
  своей природой не может их содержать — не важно, как улучшать парсинг.
  Единственный выход — читать реестр извещений (`subsystemType=PRIZ`, см.
  CLAUDE.md, «Известные пробелы», п.3, гипотеза не проверена) и там искать
  эти поля в документе-извещении.

`document_to_tender()` поэтому **не изобретает** эти 8 полей и не
подставляет заглушки/нули вместо них — она принимает их как обязательные
именованные параметры и явно отказывает (`ValueError`, с точным списком),
если хоть один не передан. Когда появится источник для реестра извещений,
эту функцию не придётся переписывать с нуля — но само по себе улучшение
парсинга контрактов `requires_sro`/`min_experience_years`/
`submission_deadline` не даст, это не туда искать.

**2026-09-23 — источник для реестра извещений нашёлся, гипотеза `PRIZ`
подтверждена реальным запросом Edwin** (`EIS_SUBSYSTEM_TYPE=PRIZ` +
`EIS_DOCUMENT_TYPE44=epNotificationEF2020`, см. CLAUDE.md → «Решено»).
`notice_document_to_tender()` ниже строит `Tender` из XML ИЗВЕЩЕНИЯ
(`eis_client.notice_parser`), а не из `ConstructionDocument` — извещение
и контракт совсем разные документы, это не расширение
`document_to_tender()`, а отдельная функция для отдельного источника.
Она закрывает структурно все три поля, которых контракт в принципе не
может дать (`requires_sro`, `min_experience_years`,
`submission_deadline`), плюс `name`/`customer_name`/`max_price` — с той же
оговоркой про 1:1, что и с полями контракта (`max_price` извещения — это
НМЦК, что ближе к смыслу поля `Tender`, чем цена контракта).

**2026-09-23, позже в тот же день — закрывает и `okpd2_code`.** Edwin
нашёл путь и в извещении: `purchaseObject/OKPD2/OKPDCode` (без обёртки
`KTRU`, последний тег `OKPDCode`, не `code` — другой путь, чем у контракта,
см. `client._OKPD2_PATH_SUFFIXES`). `notice_document_to_tender()` берёт
код тем же вызовом, что и классификатор Агента 1
(`EISClient._find_okpd2_codes()`), не отдельной новой эвристикой —
`okpd2_code` для `notice_document_to_tender()` больше не параметр вызова.

**НЕ закрывает:** `region_code`, `publish_date` — по-прежнему явные
обязательные параметры. Оба поля НЕ найдены Edwin в структуре, которую он
присылал, — предполагаемые пути (например, `publishDTInEIS` для даты
публикации) НЕ подтверждены на реальном документе, поэтому не угаданы и не
добавлены в код; см. CLAUDE.md, «Известные пробелы».
"""

from __future__ import annotations

from datetime import date

from classifier.tender import Tender

from .client import ConstructionDocument
from .notice_parser import extract_notice_fields

# Поля Tender, которые ConstructionDocument сегодня в принципе не может
# заполнить сама — см. докстринг модуля.
FIELDS_NOT_YET_EXTRACTABLE = (
    "name",
    "customer_name",
    "region_code",
    "max_price",
    "requires_sro",
    "min_experience_years",
    "publish_date",
    "submission_deadline",
)


def document_to_tender(
    document: ConstructionDocument,
    *,
    name: str,
    customer_name: str,
    region_code: str,
    max_price: float,
    requires_sro: bool,
    min_experience_years: int,
    publish_date: date,
    submission_deadline: date,
) -> Tender:
    """Строит `Tender` из `ConstructionDocument` + обязательных полей,
    которые Агент 1 сегодня не извлекает из реестра КОНТРАКТОВ — три из них
    (`requires_sro`, `min_experience_years`, `submission_deadline`)
    структурно принадлежат ИЗВЕЩЕНИЮ, не контракту, см. докстринг модуля.

    Явно отказывает, если у документа нет ОКПД2-кода или реестрового
    номера — такой документ либо не должен был пройти фильтр Агента 1,
    либо `_find_reestr_number` не нашёл номер в этом конкретном документе
    (тоже реальный, ожидаемый исход, не повод подставлять заглушку)."""
    if not document.okpd2_codes:
        raise ValueError(
            f"У документа {document.file_name!r} нет ни одного кода ОКПД2 — такой "
            "документ не должен был пройти фильтр Агента 1 (ConstructionClassifier) вообще"
        )
    if document.reestr_number is None:
        raise ValueError(
            f"Реестровый номер закупки не найден в документе {document.file_name!r} "
            "(эвристика client._find_reestr_number вернула None) — конвертация в Tender "
            "без него невозможна, см. Tender.purchase_number"
        )

    return Tender(
        purchase_number=document.reestr_number,
        okpd2_code=document.okpd2_codes[0],
        name=name,
        customer_name=customer_name,
        region_code=region_code,
        max_price=max_price,
        requires_sro=requires_sro,
        min_experience_years=min_experience_years,
        publish_date=publish_date,
        submission_deadline=submission_deadline,
    )


# Поля Tender, которые извещение сегодня в принципе не может заполнить —
# см. докстринг модуля, абзац про notice_document_to_tender().
NOTICE_FIELDS_NOT_YET_EXTRACTABLE = ("okpd2_code", "region_code", "publish_date")


def notice_document_to_tender(
    notice_xml: bytes,
    *,
    region_code: str,
    publish_date: date,
) -> Tender:
    """Строит `Tender` из XML ИЗВЕЩЕНИЯ (`epNotification*`), не контракта —
    см. докстринг модуля. В отличие от `document_to_tender()`, большую часть
    полей извлекает сама (`eis_client.notice_parser.extract_notice_fields`):
    `purchase_number`, `name`, `customer_name`, `max_price`,
    `submission_deadline`, `okpd2_code` — по пути тега (`okpd2_code` тем же
    вызовом, что и классификатор Агента 1, `EISClient._find_okpd2_codes()`);
    `requires_sro`/`min_experience_years` — текстовым разбором
    `addRequirement/content` той же логикой, что у Агента 3
    (`document_analyst.extractor`).

    Явно отказывает, если не нашёлся номер закупки (`purchase_number`) или
    ОКПД2-код — без них `Tender` не имеет смысла собирать, тот же принцип,
    что у `document_to_tender()` с реестровым номером/ОКПД2 контракта.
    `requires_sro=False`/`min_experience_years=0`, если требования не
    упомянуты в тексте `addRequirement/content` — это не отказ, а честный
    результат разбора: отсутствие найденного упоминания, не подтверждённое
    отсутствие требования (формулировка в конкретном документе может быть
    такой, что регулярка Агента 3 её не поймает — см.
    `NoticeFields.requirement_texts` для ручной проверки экспертом, если
    результат выглядит подозрительно)."""
    fields = extract_notice_fields(notice_xml)

    if fields.purchase_number is None:
        raise ValueError(
            "Номер закупки не найден в извещении (commonInfo/purchaseNumber) — "
            "конвертация в Tender без него невозможна, см. Tender.purchase_number"
        )
    if not fields.okpd2_codes:
        raise ValueError(
            "ОКПД2-код не найден в извещении (purchaseObject/OKPD2/OKPDCode) — "
            "такое извещение не должно было пройти фильтр Агента 1 (ConstructionClassifier) вообще"
        )
    if fields.name is None:
        raise ValueError(
            "Наименование объекта закупки не найдено в извещении "
            "(purchaseObjectsInfo/.../purchaseObject/name)"
        )
    if fields.customer_name is None:
        raise ValueError(
            "Наименование заказчика не найдено в извещении "
            "(purchaseResponsibleInfo/responsibleOrgInfo/fullName)"
        )
    if fields.max_price is None:
        raise ValueError("НМЦК не найдена или не разобралась как число (contractConditionsInfo/maxPriceInfo/maxPrice)")
    if fields.submission_deadline is None:
        raise ValueError("Срок подачи заявок не найден или не разобрался как дата (procedureInfo/collectingInfo/endDT)")

    return Tender(
        purchase_number=fields.purchase_number,
        okpd2_code=fields.okpd2_codes[0],
        name=fields.name,
        customer_name=fields.customer_name,
        region_code=region_code,
        max_price=fields.max_price,
        requires_sro=fields.requires_sro,
        min_experience_years=fields.min_experience_years,
        publish_date=publish_date,
        submission_deadline=fields.submission_deadline,
    )

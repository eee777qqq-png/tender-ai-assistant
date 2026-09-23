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
"""

from __future__ import annotations

from datetime import date

from classifier.tender import Tender

from .client import ConstructionDocument

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

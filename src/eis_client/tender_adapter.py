"""Переходник Агент 1 -> Агент 2: `ConstructionDocument` (что реально
парсит монитор) -> `classifier.tender.Tender` (что требует конвейер
дальше — Агенты 2/3/4/5/6/7/8).

До этого модуля (ревизия проекта, 2026-09-21) такого кода не было вообще:
типы были совместимы только теоретически — если бы ЕИС начал отвечать
реальными данными завтра, выход монитора всё равно физически не мог бы
попасть в `match_profile_to_tender()` и далее, потому что не существовало
функции, которая строит `Tender` из того, что реально возвращает Агент 1.

**Честная граница того, что здесь реально можно сделать.** `Tender`
требует 10 полей; `ConstructionDocument` сегодня даёт основания только для
двух:
- `okpd2_code` — из `okpd2_codes[0]` (список уже отфильтрован
  `ConstructionClassifier` при сборе документа);
- `purchase_number` — из `reestr_number` (`client._find_reestr_number()`,
  та же tag-name-эвристика, что и для ОКПД2, и с тем же уровнем
  доверия: не подтверждена на реальных документах ЕИС, потому что сервис
  пока не отдаёт реальные данные, см. CLAUDE.md, «Известные пробелы», п.3).

Остальные 8 полей (`name`, `customer_name`, `region_code`, `max_price`,
`requires_sro`, `min_experience_years`, `publish_date`,
`submission_deadline`) описаны только в закрытом «Альбоме ТФФ ЕИС» (см.
CLAUDE.md, п.4) — их извлечение не реализовано, потому что схема
недоступна, не потому что забыли.

`document_to_tender()` поэтому **не изобретает** эти 8 полей и не
подставляет заглушки/нули вместо них — она принимает их как обязательные
именованные параметры и явно отказывает (`ValueError`, с точным списком),
если хоть один не передан. Когда появится реальный источник для них
(документированная схема или наблюдение на реальных данных), их можно
будет извлекать так же, как `okpd2_code`/`purchase_number` — этот модуль
не придётся переписывать с нуля.
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
    которые Агент 1 сегодня не извлекает (см. `FIELDS_NOT_YET_EXTRACTABLE`).

    Явно отказывает, если у документа нет ОКПД2-кода или реестрового
    номера — такой документ либо не должен был пройти фильтр Агента 1,
    либо эвристика `_find_reestr_number` не нашла номер в этом конкретном
    документе (тоже реальный, ожидаемый исход, не повод подставлять
    заглушку)."""
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

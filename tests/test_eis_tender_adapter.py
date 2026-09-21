"""`eis_client.document_to_tender()` — переходник Агент 1 -> Агент 2, до
2026-09-21 не существовавший вообще (типы `ConstructionDocument` и
`Tender` были совместимы только теоретически, см. ревизия проекта).

Проверяет ровно то, что честно можно проверить сегодня: конвертация
работает, когда есть ОКПД2-код и реестровый номер плюс явно переданные
8 полей, которые Агент 1 пока не извлекает — и явно отказывает, если
чего-то из этого нет, а не подставляет заглушку."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from eis_client import FIELDS_NOT_YET_EXTRACTABLE, document_to_tender
from eis_client.client import ConstructionDocument

REQUIRED_OVERRIDES = dict(
    name="Капитальный ремонт кровли здания школы №5",
    customer_name="ГКУ г. Москвы «Дирекция капитального ремонта»",
    region_code="77",
    max_price=8_000_000,
    requires_sro=True,
    min_experience_years=2,
    publish_date=date(2026, 8, 1),
    submission_deadline=date(2026, 8, 20),
)


def test_document_to_tender_builds_a_real_tender_with_all_overrides_supplied():
    document = ConstructionDocument(
        archive_url="https://example.invalid/a.zip",
        file_name="1.xml",
        okpd2_codes=["41.20.10.110", "43.99.90.190"],
        reestr_number="0173200001426000101",
    )

    tender = document_to_tender(document, **REQUIRED_OVERRIDES)

    assert tender.purchase_number == "0173200001426000101"
    assert tender.okpd2_code == "41.20.10.110"  # первый код из отфильтрованного списка
    assert tender.name == REQUIRED_OVERRIDES["name"]
    assert tender.max_price == 8_000_000


def test_document_to_tender_rejects_missing_okpd2_code():
    document = ConstructionDocument(
        archive_url="https://example.invalid/a.zip",
        file_name="1.xml",
        okpd2_codes=[],
        reestr_number="0173200001426000101",
    )

    with pytest.raises(ValueError, match="ОКПД2"):
        document_to_tender(document, **REQUIRED_OVERRIDES)


def test_document_to_tender_rejects_missing_reestr_number():
    """Эвристика `_find_reestr_number` не всегда что-то находит — это
    честный исход, не повод подставить пустую строку вместо номера закупки."""
    document = ConstructionDocument(
        archive_url="https://example.invalid/a.zip",
        file_name="1.xml",
        okpd2_codes=["41.20.10.110"],
        reestr_number=None,
    )

    with pytest.raises(ValueError, match="[Рр]еестровый номер"):
        document_to_tender(document, **REQUIRED_OVERRIDES)


def test_document_to_tender_does_not_invent_the_unextractable_fields():
    """Без хотя бы одного из 8 полей, которые Агент 1 сегодня не извлекает,
    вызов должен упасть — Python сам откажет на отсутствующем обязательном
    keyword-параметре, а не тихо подставит 0/False/сегодняшнюю дату."""
    document = ConstructionDocument(
        archive_url="https://example.invalid/a.zip",
        file_name="1.xml",
        okpd2_codes=["41.20.10.110"],
        reestr_number="0173200001426000101",
    )
    incomplete_overrides = dict(REQUIRED_OVERRIDES)
    del incomplete_overrides["max_price"]

    with pytest.raises(TypeError, match="max_price"):
        document_to_tender(document, **incomplete_overrides)


def test_fields_not_yet_extractable_matches_the_overrides_this_module_requires():
    """Документирующая константа не должна расходиться с самой сигнатурой —
    иначе она перестанет быть честным списком."""
    assert set(FIELDS_NOT_YET_EXTRACTABLE) == set(REQUIRED_OVERRIDES.keys())

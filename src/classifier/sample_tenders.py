"""Придуманные тестовые закупки по образцу реальных строительных тендеров
44-ФЗ — для демонстрации и тестирования `matching.py`, пока Агент 1 не
выдаёт полные структурированные записи закупок из ЕИС. Коды ОКПД2 — из
`data/okpd2_construction.csv`, региональные коды — как в `EIS_ORG_REGION`
(первые 2 цифры КЛАДР): 77 — Москва, 50 — Московская область,
23 — Краснодарский край, 61 — Ростовская область (регионы пилота, см.
CLAUDE.md).
"""

from __future__ import annotations

from datetime import date

from .tender import Tender

SAMPLE_TENDERS: list[Tender] = [
    Tender(
        purchase_number="0173200001426000101",
        name="Капитальный ремонт кровли здания школы №5",
        customer_name="ГКУ г. Москвы «Дирекция капитального ремонта»",
        okpd2_code="43.91.19.110",
        region_code="77",
        max_price=8_000_000,
        requires_sro=True,
        min_experience_years=2,
        publish_date=date(2026, 8, 1),
        submission_deadline=date(2026, 8, 20),
    ),
    Tender(
        purchase_number="0350200003426000202",
        name="Строительство здания детского сада на 120 мест",
        customer_name="Министерство строительства Московской области",
        okpd2_code="41.20.40.900",
        region_code="50",
        max_price=350_000_000,
        requires_sro=True,
        min_experience_years=5,
        publish_date=date(2026, 8, 5),
        submission_deadline=date(2026, 9, 1),
    ),
    Tender(
        purchase_number="0123200004426000303",
        name="Штукатурные и фасадные работы административного здания",
        customer_name="Администрация муниципального образования г. Краснодар",
        okpd2_code="43.31.10.110",
        region_code="23",
        max_price=3_000_000,
        requires_sro=False,
        min_experience_years=1,
        publish_date=date(2026, 8, 10),
        submission_deadline=date(2026, 8, 25),
    ),
    Tender(
        purchase_number="0161200005426000404",
        name="Прокладка магистрального трубопровода водоснабжения",
        customer_name="ГУП Ростовской области «Водоканал»",
        okpd2_code="42.21.21.000",
        region_code="61",
        max_price=45_000_000,
        requires_sro=True,
        min_experience_years=3,
        publish_date=date(2026, 8, 12),
        submission_deadline=date(2026, 9, 5),
    ),
    Tender(
        purchase_number="0177200006426000505",
        name="Разработка системы электронного документооборота",
        customer_name="ГКУ г. Москвы «Центр информационных технологий»",
        okpd2_code="62.01.11.000",  # не стройка — контрольный пример
        region_code="77",
        max_price=2_000_000,
        requires_sro=False,
        min_experience_years=0,
        publish_date=date(2026, 8, 15),
        submission_deadline=date(2026, 8, 30),
    ),
]

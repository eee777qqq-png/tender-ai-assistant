"""Придуманные, но реалистичные версии двух типов источников — для
демонстрации протокола Агента 10 на понятном, стабильном примере (не
зависящем от сети/реальных сайтов), не для реального мониторинга.

`PRICE_BASE`: региональный индекс пересчёта сметной стоимости для Москвы —
структура и порядок значений похожи на реальные ежеквартальные письма
Минстроя с индексами по видам работ. Реальный источник базы расценок теперь
подключён отдельно — `smeta_estimator.version_watch`.

`LEGISLATION`: выдержка из 44-ФЗ про требования к размеру обеспечения —
пункты, которые непосредственно использует Агент 3 (`document_analyst`) и
Агент 6 (`document_assembler`). **2026-09-25: у `LEGISLATION` теперь есть и
реальный, живой мониторинг** — `regulatory_updates.legislation_watch`
(налоговые ставки на nalog.gov.ru и статьи ГК РФ/Закона о защите прав
потребителей на consultant.ru), не только этот придуманный пример. Сам
44-ФЗ (сроки/обеспечение, о которых этот файл) реальным источником пока не
покрыт — это отдельная, не начатая задача, придуманный пример здесь по-прежнему
единственная демонстрация именно для 44-ФЗ.

В обоих случаях версия "Q2" — то, что уже должно быть применено в тестах
как исходное состояние, "Q3" — обнаруживаемое обновление с explicit-диффом.
"""

from __future__ import annotations

from datetime import date

from .models import RegulatoryVersion, SourceType

MOSCOW_PRICE_INDEX_SOURCE_ID = "msk_price_index_construction"
MOSCOW_PRICE_INDEX_SOURCE_TYPE = SourceType.PRICE_BASE
MOSCOW_PRICE_INDEX_SOURCE_NAME = "Индекс пересчёта сметной стоимости строительства — Москва"

MOSCOW_PRICE_INDEX_Q2_2026 = RegulatoryVersion(
    source_id=MOSCOW_PRICE_INDEX_SOURCE_ID,
    version_label="2026-Q2",
    published_at=date(2026, 4, 1),
    items={
        "index_smr": "6.12",  # индекс на СМР к базе ФЕР-2001
        "index_pnr": "5.98",  # индекс на пусконаладочные работы
        "index_oborudovanie": "4.35",  # индекс на оборудование
        "base_edition": "ФЕР-2001 в редакции 2020 года",
    },
)

MOSCOW_PRICE_INDEX_Q3_2026 = RegulatoryVersion(
    source_id=MOSCOW_PRICE_INDEX_SOURCE_ID,
    version_label="2026-Q3",
    published_at=date(2026, 7, 1),
    items={
        "index_smr": "6.27",  # выросло с 6.12
        "index_pnr": "5.98",  # без изменений — не должно попасть в diff
        "index_oborudovanie": "4.50",  # выросло с 4.35
        "base_edition": "ФЕР-2001 в редакции 2020 года",
        "index_proezd": "1.08",  # новый пункт, которого не было в Q2
    },
)

FZ44_SECURITY_SOURCE_ID = "fz44_security_requirements"
FZ44_SECURITY_SOURCE_TYPE = SourceType.LEGISLATION
FZ44_SECURITY_SOURCE_NAME = "44-ФЗ — требования к обеспечению заявки и контракта"

FZ44_SECURITY_Q2_2026 = RegulatoryVersion(
    source_id=FZ44_SECURITY_SOURCE_ID,
    version_label="ред. от 01.04.2026",
    published_at=date(2026, 4, 1),
    items={
        "art44_p6_zayavka_percent": "0,5%–5%",  # обеспечение заявки, ст. 44 ч.6
        "art96_kontrakt_percent": "5%–30%",  # обеспечение контракта, ст. 96
        "art37_antidemping_threshold_percent": "25%",  # порог антидемпинговых мер, ст. 37
    },
)

FZ44_SECURITY_Q3_2026 = RegulatoryVersion(
    source_id=FZ44_SECURITY_SOURCE_ID,
    version_label="ред. от 01.09.2026",
    published_at=date(2026, 9, 1),
    items={
        "art44_p6_zayavka_percent": "0,5%–5%",  # без изменений — не должно попасть в diff
        "art96_kontrakt_percent": "10%–30%",  # нижняя граница выросла с 5% до 10%
        "art37_antidemping_threshold_percent": "25%",
    },
)

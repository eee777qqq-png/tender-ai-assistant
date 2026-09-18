"""Правильный источник региональных данных — ГОСР (индексы по группам
однородных строительных ресурсов) + текущие цены напрямую, взамен
ошибочно использованных раньше общих «индексов к ФЕР-2001/ТЕР-2001»
(см. CLAUDE.md, «Известные пробелы», диагноз методологической ошибки).

Фикстура `gosr_moscow_q3_2026_sample.xlsx` — не выдумана, а вырезана из
настоящего отчёта, скачанного с fgiscs.minstroyrf.ru 2026-09-18 (Москва,
3 квартал 2026 года) через `/api/IndicesForResourcesGroups/GenerateCustomReport`."""

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from regulatory_updates import RegulatoryUpdateStore, RegulatoryVersion, SourceType
from smeta_estimator.regional_pricing_client import (
    PILOT_PRICE_ZONES,
    fetch_current_prices_json,
    fetch_gosr_report,
)
from smeta_estimator.regional_pricing_parser import (
    GOSR_SOURCE_ID_TEMPLATE,
    build_regulatory_version,
    parse_current_prices_json,
    parse_gosr_workbook,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_gosr_index():
    return parse_gosr_workbook((FIXTURES / "gosr_moscow_q3_2026_sample.xlsx").read_bytes())


def test_pilot_price_zones_cover_all_4_regions():
    assert set(PILOT_PRICE_ZONES) == {
        "г. Москва",
        "Московская область",
        "Краснодарский край",
        "Ростовская область",
    }


def test_parse_gosr_workbook_reads_real_material_and_machine_indices():
    """Реальные значения, найденные на fgiscs.minstroyrf.ru/prices для
    Москвы на 3 квартал 2026 года при диагностике методологической ошибки
    2026-09-18 — не выдуманы."""
    index = load_gosr_index()

    gravel = index["02.2.01.02-1042"]
    assert gravel.base_price_2022 == pytest.approx(1174.99)
    assert gravel.group_name == "Гравий"
    assert gravel.index_value == pytest.approx(2.7)

    propane = index["01.3.02.09-0022"]
    assert propane.index_value == pytest.approx(1.37)

    crane = index["91.05.01-017"]
    # Базисная цена крана в ГОСР — БЕЗ зарплаты машиниста (622.62), в точности
    # совпадает с PriceCostWithoutSalary из ФСБЦ_Маш.xml (см. fsnb_parser.py).
    assert crane.base_price_2022 == pytest.approx(622.62)
    assert crane.index_value == pytest.approx(1.5)


def test_parse_gosr_workbook_distinguishes_index_scale_from_the_wrong_old_source():
    """Диагностика 2026-09-18: значения ГОСР (~1.2-2.7) на порядок меньше
    прежних ошибочно использованных «индексов к ФЕР-2001/ТЕР-2001» (~14-60)
    — это разные методологии, не опечатка в масштабе."""
    index = load_gosr_index()
    assert all(0.5 < e.index_value < 5.0 for e in index.values())


def test_parse_current_prices_json_flattens_grouped_response():
    raw = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "ksrType": 1,
                    "items": [
                        {"code": "01.2.01.01-1008", "name": "Битум", "estimatedPrice": "28369.92"},
                        {"code": "01.2.03.01-0011", "name": "Вяжущее", "estimatedPrice": "35828.43"},
                    ],
                }
            ],
            "total": 2,
        }
    )

    prices = parse_current_prices_json(raw)

    assert prices == {"01.2.01.01-1008": 28369.92, "01.2.03.01-0011": 35828.43}


def test_build_regulatory_version_only_includes_requested_codes():
    """Полный отчёт ГОСР на регион — десятки тысяч строк; версия для Агента
    10 должна нести только то, что реально нужно текущему каталогу, а не
    всё целиком."""
    index = load_gosr_index()

    version = build_regulatory_version(
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        published_at=date(2026, 8, 24),
        gosr_index=index,
        resource_codes=["02.2.01.02-1042", "неизвестный-код"],
    )

    assert isinstance(version, RegulatoryVersion)
    assert version.source_id == GOSR_SOURCE_ID_TEMPLATE.format(region_key="Москва")
    assert version.items == {"02.2.01.02-1042": "2.7"}


def test_gosr_version_flows_through_the_existing_agent_10_review_protocol(tmp_path):
    """Не новая структура — используется RegulatoryUpdateStore Агента 10 как
    есть: обнаружение новой версии индексов ГОСР -> PENDING -> подтверждение
    эксперта, прежде чем Агент 4 сможет их использовать."""
    index = load_gosr_index()
    store = RegulatoryUpdateStore(tmp_path / "regulatory_updates.sqlite3")

    version = build_regulatory_version(
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        published_at=date(2026, 8, 24),
        gosr_index=index,
        resource_codes=["02.2.01.02-1042", "01.3.02.09-0022"],
    )

    update = store.detect_update(SourceType.PRICE_BASE, "Индексы ГОСР ФГИС ЦС (тест)", version)
    assert update is not None
    assert update.status.value == "pending"

    store.approve_update(update.update_id, reviewer="Edwin", approved=True)
    applied = store.get_applied_version(version.source_id)
    assert applied.items["02.2.01.02-1042"] == "2.7"


def test_fetch_gosr_report_calls_the_correct_endpoint_without_real_network():
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(content=b"xlsx-bytes", raise_for_status=lambda: None)
        result = fetch_gosr_report(price_zone_id=191, period_id=427)

    assert result == b"xlsx-bytes"
    args, kwargs = mock_get.call_args
    assert "IndicesForResourcesGroups/GenerateCustomReport" in args[0]
    assert kwargs["params"] == {"periodId": 427, "priceZoneId": 191, "authorityId": "null"}


def test_fetch_current_prices_json_targets_materials_or_machines_endpoint():
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            content=b'{"items":[],"total":0}',
            json=lambda: {"items": [], "total": 0},
            raise_for_status=lambda: None,
        )
        fetch_current_prices_json(price_zone_id=191, period_id=427, category="materials")

    args, kwargs = mock_get.call_args
    assert "BuildingResources/Search/Materials" in args[0]
    assert kwargs["params"]["priceZoneId"] == 191
    assert kwargs["params"]["periodId"] == 427


def test_fetch_current_prices_json_rejects_price_zone_outside_pilot_regions():
    with pytest.raises(ValueError, match="пилотных"):
        fetch_current_prices_json(price_zone_id=999999, period_id=427, category="materials")

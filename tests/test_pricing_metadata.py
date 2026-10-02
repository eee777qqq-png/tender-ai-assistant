"""Метаданные источника расчёта Агента 4 (pricing_metadata.py) — без сети.

Заголовки сборников — дословно из реальных файлов архива ФСНБ-2022
(скачан 2026-10-02/03), имена актов об оплате труда — из ответов
`RimWorkerSalaryRegistry/DocumentInfo` ФГИС ЦС (3 кв. 2026, id периода 427,
2 кв. 2026 для Рязани — 426)."""

import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator.cost_estimate import SmetaLineItem, build_cost_estimate
from smeta_estimator.models import GesnResourceUsage, MatchResult, RateCandidate, RegionalPriceResult
from smeta_estimator.pricing_metadata import WAGE_ACT_NOT_PUBLISHED, build_pricing_metadata, parse_catalog_header
from smeta_estimator.regional_pricing_client import fetch_wage_act_file_name

_HEADER = (
    '﻿<?xml version="1.0" encoding="utf-8"?>\n'
    '<base xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" PriceLevel="01.01.2022" '
    'CreationDate="14.08.2026" CreationTime="14:0{n}" ProgramName="ИАС ЦС" '
    'BaseName="{type} от 14.08.2026 14:02:53" BaseType="{type}">\n  <Decrees/>\n</base>'
)
CATALOG = {
    "ГЭСН.xml": _HEADER.format(n=4, type="ГЭСН").encode("utf-8"),
    "ГЭСНр.xml": _HEADER.format(n=6, type="ГЭСНр").encode("utf-8"),
    "ГЭСНм.xml": _HEADER.format(n=5, type="ГЭСНм").encode("utf-8"),
}
FETCHED = datetime.fromisoformat("2026-10-03T10:15:00+03:00")

# (регион, квартал, id периода, id ценовой зоны, акт из ФГИС ЦС или None)
REAL_DOCUMENT_CASES = [
    ("Краснодарский край", "3 квартал 2026 г.", 427, 148, "147 от 27.07.2026.pdf"),  # Краснодар, круг 2
    ("Московская область", "3 квартал 2026 г.", 427, 127, "24РВ-25.pdf"),  # МО, круг 2
    ("г. Москва", "3 квартал 2026 г.", 427, 191, None),  # кровля/потолок — акт не опубликован
    ("Рязанская область", "2 квартал 2026 г.", 426, 169, "Постановление 215_п от 20.03.26 об установлении размера оплату труда.pdf"),
]


def _assert_all_filled(d: dict) -> None:
    for key in (
        "catalog_source",
        "catalog_version_date",
        "catalog_archive_url",
        "region",
        "region_index_period",
        "wage_act",
        "fgiscs_price_fetched_at",
    ):
        assert d[key], key
    assert d["catalog_files"]
    assert d["price_zone_id"] and d["period_id"]


def test_parse_catalog_header_reads_real_attributes_without_full_parse():
    info = parse_catalog_header("ГЭСНм.xml", CATALOG["ГЭСНм.xml"])

    assert info.base_type == "ГЭСНм"
    assert info.creation_date == "2026-08-14"
    assert info.price_level == "01.01.2022"
    assert "ГЭСНм от 14.08.2026" in info.base_name


def test_parse_catalog_header_fails_loudly_on_unknown_format():
    with pytest.raises(ValueError, match="нет BaseType/CreationDate"):
        parse_catalog_header("x.xml", b"<other/>")


@pytest.mark.parametrize("region,period,period_id,zone_id,act", REAL_DOCUMENT_CASES)
def test_metadata_is_fully_populated_for_every_real_document_region(region, period, period_id, zone_id, act):
    meta = build_pricing_metadata(
        CATALOG, "https://fgiscs.minstroyrf.ru/api/values/GetFileContent/x", region, period, period_id, zone_id, act, FETCHED
    )
    d = meta.to_dict()

    _assert_all_filled(d)
    assert d["region"] == region
    assert d["region_index_period"] == period
    assert d["catalog_source"] == "ФСНБ-2022 (ГЭСН, ГЭСНм, ГЭСНр)"
    assert d["catalog_version_date"] == "2026-08-14"
    assert d["fgiscs_price_fetched_at"] == "2026-10-03T10:15:00+03:00"


def test_krasnodar_reports_current_regional_act_147_not_the_customers_old_59():
    # Расхождение круга 2 по Краснодару (+10,1% на труде): смета заказчика
    # составлена по приказу №59 от 25.03.2025, а ФГИС ЦС для 3 кв. 2026
    # отдаёт приказ №147 от 27.07.2026 — расчёт идёт по нему.
    meta = build_pricing_metadata(
        CATALOG, "url", "Краснодарский край", "3 квартал 2026 г.", 427, 148, "147 от 27.07.2026.pdf", FETCHED
    )

    assert meta.wage_act == "147 от 27.07.2026.pdf"
    assert "59" not in meta.wage_act
    assert "25.03.2025" not in meta.wage_act


def test_unpublished_wage_act_is_explicit_not_empty():
    meta = build_pricing_metadata(CATALOG, "url", "г. Москва", "3 квартал 2026 г.", 427, 191, None, FETCHED)

    assert meta.wage_act == WAGE_ACT_NOT_PUBLISHED
    assert meta.notes


def _response(content: bytes, payload=None):
    resp = MagicMock()
    resp.content = content
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_wage_act_file_name_reads_real_response_shape():
    with patch(
        "smeta_estimator.regional_pricing_client.requests.get",
        return_value=_response(b"{...}", {"fileId": "55f7", "fileName": "147 от 27.07.2026.pdf"}),
    ) as get:
        assert fetch_wage_act_file_name(148, 427) == "147 от 27.07.2026.pdf"

    params = get.call_args.kwargs["params"]
    assert params["priceZoneId"] == 148 and params["periodId"] == 427 and params["countrySubjectId"] == 280


def test_fetch_wage_act_file_name_is_none_when_zone_has_no_act():
    # Реальный ответ для г. Москва — пустое тело.
    with patch("smeta_estimator.regional_pricing_client.requests.get", return_value=_response(b"")):
        assert fetch_wage_act_file_name(191, 427) is None


def _reviewed_match(region: str, period: str) -> MatchResult:
    candidate = RateCandidate(
        code="11-01-027-03",
        name="тест",
        unit="100 м2",
        base_price=0.0,
        match_score=1.0,
        resources=[GesnResourceUsage(resource_code="A", resource_name="а", quantity=1.0)],
        priced=RegionalPriceResult(region_name=region, period_label=period, total_price=100.0),
    )
    return MatchResult(
        query_text="работа", tender_purchase_number="0373200098326000005", candidates=[candidate], selected_code="11-01-027-03", expert_reviewed=True
    )


def test_cost_estimate_carries_metadata_without_changing_total():
    meta = build_pricing_metadata(
        CATALOG, "url", "Краснодарский край", "3 квартал 2026 г.", 427, 148, "147 от 27.07.2026.pdf", FETCHED
    )
    items = [SmetaLineItem(_reviewed_match("Краснодарский край", "3 квартал 2026 г."), work_volume=2.0)]

    with_meta = build_cost_estimate(items, date(2026, 10, 3), "Краснодарский край", "3 квартал 2026 г.", meta)
    without = build_cost_estimate(items, date(2026, 10, 3), "Краснодарский край", "3 квартал 2026 г.")

    assert with_meta.total_cost == without.total_cost == 200.0
    assert with_meta.pricing_metadata.wage_act == "147 от 27.07.2026.pdf"
    assert without.pricing_metadata is None

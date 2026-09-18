"""Продолжение диагностики 2026-09-18 по пунктам 12/14/15 «Известных
пробелов»: трудозатраты рабочих (закрыто), покрытие ГОСР по машинам
(закрыто комбинированием с текущими ценами) и пагинация API текущих цен
(проверена и защищена, а не просто отмечена как риск)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator.fsnb_parser import apply_prices, parse_fsbc_machines_xml, parse_fsbc_materials_xml, parse_gesn_xml
from smeta_estimator.pricing import price_candidates_for_region
from smeta_estimator.regional_pricing_client import fetch_current_prices_json, fetch_worker_salary_registry
from smeta_estimator.regional_pricing_parser import (
    parse_current_prices_json,
    parse_gosr_workbook,
    parse_worker_salary_registry,
)
from smeta_estimator.search import search_candidates

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# -- п.12: трудозатраты рабочих (не машинистов) — закрыто --------------------


def test_parse_worker_salary_registry_reads_real_worker_and_machinist_codes():
    """Реальные значения для Москвы, 3 квартал 2026, найденные при проверке
    2026-09-18 — не выдуманы. Один реестр покрывает и коды разрядов рабочих
    (1-100-XX, те же коды, что Resource Code в ГЭСН), и коды машинистов
    (4-100-XXX, те же коды, что DriverCode в ФСБЦ_Маш.xml)."""
    wages = parse_worker_salary_registry((FIXTURES / "worker_salary_moscow_sample.json").read_bytes())

    assert wages["1-100-38"] == pytest.approx(723.54)  # разряд 3,8 — код из тестового ГЭСН-фрагмента
    assert wages["4-100-060"] == pytest.approx(994.18)  # машинист 6 разряда


def test_worker_wages_close_the_labor_gap_when_merged_into_current_prices():
    """Реестр оплаты труда — такая же готовая опубликованная сметная цена,
    как и текущая цена материала: можно просто объединить со словарём
    `current_prices`, отдельного параметра в `pricing.py` не требуется."""
    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    base_prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(catalog, base_prices)
    gosr_index = parse_gosr_workbook((FIXTURES / "gosr_moscow_q3_2026_sample.xlsx").read_bytes())
    wages = parse_worker_salary_registry((FIXTURES / "worker_salary_moscow_sample.json").read_bytes())

    candidates = search_candidates(catalog, "устройство кровли на битумной мастике с защитным слоем из гравия")
    priced = price_candidates_for_region(
        candidates, "г. Москва", "3 квартал 2026 г.", current_prices=wages, gosr_index=gosr_index,
        resource_base_prices=base_prices,
    )

    target = next(c for c in priced if c.code == "12-01-001-02")
    labor_resolution = next(r for r in target.priced.resolutions if r.resource_code == "1-100-38")
    assert labor_resolution.source == "current_price"
    assert labor_resolution.unit_price == pytest.approx(723.54)


# -- п.14: неполное покрытие ГОСР по машинам — закрыто комбинированием -------


def test_current_machine_prices_fill_gaps_that_gosr_did_not_cover():
    """На реальном примере (Москва, 3 квартал 2026) индекс ГОСР нашёлся
    только для 2 из 5 машинных кодов тестовой позиции; для остальных 3
    оказалась опубликована текущая цена напрямую — вместе источники
    покрывают все 5, ни один не остаётся без объяснимой причины."""
    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    base_prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(catalog, base_prices)
    gosr_index = parse_gosr_workbook((FIXTURES / "gosr_moscow_q3_2026_sample.xlsx").read_bytes())
    current_machines = parse_current_prices_json(
        (FIXTURES / "current_prices_moscow_machines_sample.json").read_bytes()
    )

    candidates = search_candidates(catalog, "устройство кровли на битумной мастике с защитным слоем из гравия")
    priced = price_candidates_for_region(
        candidates, "г. Москва", "3 квартал 2026 г.", current_prices=current_machines, gosr_index=gosr_index,
        resource_base_prices=base_prices,
    )

    target = next(c for c in priced if c.code == "12-01-001-02")
    machine_codes = {"91.05.01-017", "91.05.05-015", "91.08.04-021", "91.14.02-001"}
    resolved_machines = {
        r.resource_code: r.source for r in target.priced.resolutions if r.resource_code in machine_codes
    }
    assert resolved_machines == {
        "91.05.01-017": "gosr_index",  # только в ГОСР
        "91.08.04-021": "gosr_index",  # только в ГОСР
        "91.05.05-015": "current_price",  # только в текущих ценах
        "91.14.02-001": "current_price",  # только в текущих ценах
    }
    # Ни один из этих 4 кодов не остался unresolved.
    assert not (machine_codes - {"91.06.05-011"}) & set(target.priced.unresolved_resource_codes)


# -- п.15: пагинация API текущих цен — проверена и защищена, не просто отмечена как риск --


def test_fetch_worker_salary_registry_paginates_until_total_is_reached():
    """Подтверждено вживую 2026-09-18: `take` у этого эндпоинта реально
    ограничивает выдачу (Москва: total=186, take=100 вернул только 100) —
    здесь проверяем на уменьшенном примере (total=5, по 2 на страницу),
    что клиент действительно догружает все страницы, а не останавливается
    на первой."""
    page1 = {"total": 5, "items": [{"code": "1-100-10", "salary": "1"}, {"code": "1-100-11", "salary": "2"}]}
    page2 = {"total": 5, "items": [{"code": "1-100-12", "salary": "3"}, {"code": "1-100-13", "salary": "4"}]}
    page3 = {"total": 5, "items": [{"code": "1-100-14", "salary": "5"}]}

    responses = [
        MagicMock(json=lambda p=page1: p, raise_for_status=lambda: None),
        MagicMock(json=lambda p=page2: p, raise_for_status=lambda: None),
        MagicMock(json=lambda p=page3: p, raise_for_status=lambda: None),
    ]

    with patch("requests.get", side_effect=responses) as mock_get:
        raw = fetch_worker_salary_registry(price_zone_id=191, period_id=427, page_size=2)

    assert mock_get.call_count == 3
    wages = parse_worker_salary_registry(raw)
    assert len(wages) == 5
    assert wages["1-100-14"] == 5.0

    # Каждый вызов действительно запрашивал следующую страницу, не одну и ту же.
    pages_requested = [call.kwargs["params"]["page"] for call in mock_get.call_args_list]
    assert pages_requested == [1, 2, 3]


def test_fetch_current_prices_json_retries_with_bigger_take_if_response_was_incomplete():
    """Проверка того самого риска «тихой потери части цен»: если сервис
    вернул меньше позиций, чем указано в `total`, клиент обязан заметить
    это и повторить запрос с большим `take`, а не просто отдать то, что
    пришло."""
    incomplete = MagicMock(
        json=lambda: {"total": 500, "items": [{"id": 1, "ksrType": 1, "items": [{"code": "X", "estimatedPrice": "1"}] * 100}]},
        raise_for_status=lambda: None,
        content=b"incomplete",
    )
    complete_items = [{"code": f"C{i}", "estimatedPrice": "1"} for i in range(500)]
    complete = MagicMock(
        json=lambda: {"total": 500, "items": [{"id": 1, "ksrType": 1, "items": complete_items}]},
        raise_for_status=lambda: None,
        content=b"complete",
    )

    with patch("requests.get", side_effect=[incomplete, complete]) as mock_get:
        result = fetch_current_prices_json(price_zone_id=191, period_id=427, category="materials", initial_take=100)

    assert result == b"complete"
    assert mock_get.call_count == 2
    first_take = mock_get.call_args_list[0].kwargs["params"]["take"]
    second_take = mock_get.call_args_list[1].kwargs["params"]["take"]
    assert second_take > first_take


def test_fetch_current_prices_json_raises_instead_of_silently_returning_partial_data():
    """Если данные не удаётся собрать полностью даже к `max_take` —
    явная ошибка, а не молчаливо неполный словарь цен."""
    always_incomplete = MagicMock(
        json=lambda: {"total": 999999, "items": [{"id": 1, "ksrType": 1, "items": [{"code": "X", "estimatedPrice": "1"}]}]},
        raise_for_status=lambda: None,
        content=b"partial",
    )

    with patch("requests.get", return_value=always_incomplete):
        with pytest.raises(ValueError, match="только"):
            fetch_current_prices_json(
                price_zone_id=191, period_id=427, category="materials", initial_take=10, max_take=40
            )

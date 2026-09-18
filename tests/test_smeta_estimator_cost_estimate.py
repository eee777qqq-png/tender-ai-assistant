"""Юнит-тесты сведения позиций сметы (Агент 4) в себестоимость для Агента 5
— независимо от реальных фикстур ФГИС ЦС (те покрыты
`test_smeta_estimator_end_to_end.py`), здесь модели собраны вручную, чтобы
явно проверить границы: непроверенная позиция, позиция без выбранного
кандидата, частично оценённый кандидат, несовпадение региона/периода.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator import (
    MatchResult,
    RateCandidate,
    RegionalPriceResult,
    ResourcePriceResolution,
    SmetaLineItem,
    build_cost_estimate,
)

REGION = "г. Москва"
PERIOD = "3 квартал 2026 г."
TENDER_PURCHASE_NUMBER = "0173200001426000101"


def _priced_candidate(code: str, unit_price: float, fully_priced: bool = True) -> RateCandidate:
    resolutions = [
        ResourcePriceResolution(
            resource_code="1-100-38",
            resource_name="Средний разряд работы 3,8",
            quantity=14.6,
            unit_price=unit_price,
            source="current_price",
        )
    ]
    if not fully_priced:
        resolutions.append(
            ResourcePriceResolution(
                resource_code="01.2.03.03",
                resource_name="Мастики битумосодержащие",
                quantity=0.712,
                unit_price=None,
                source="unresolved",
            )
        )
    return RateCandidate(
        code=code,
        name="Устройство кровли на битумной мастике",
        unit="100 м2",
        base_price=1000.0,
        match_score=1.0,
        priced=RegionalPriceResult(
            region_name=REGION, period_label=PERIOD, total_price=unit_price * 14.6, resolutions=resolutions
        ),
    )


def _reviewed_match_result(selected_code: str | None, candidates: list[RateCandidate]) -> MatchResult:
    result = MatchResult(
        query_text="устройство кровли на битумной мастике",
        tender_purchase_number=TENDER_PURCHASE_NUMBER,
        candidates=candidates,
    )
    result.select_candidate(selected_code, reviewer="Edwin")
    return result


def test_build_cost_estimate_sums_fully_priced_line_items():
    candidate = _priced_candidate("12-01-001-02", unit_price=1000.0)
    match_result = _reviewed_match_result("12-01-001-02", [candidate])
    line_item = SmetaLineItem(match_result=match_result, work_volume=8.5)

    result = build_cost_estimate([line_item], as_of_date=date(2026, 7, 1), region_name=REGION, period_label=PERIOD)

    assert result.total_cost == pytest.approx(1000.0 * 14.6 * 8.5)
    assert result.priced_line_items == 1
    assert result.unpriced_line_items == []
    assert result.partially_priced_line_items == []
    assert result.is_complete()


def test_rejects_unreviewed_match_result():
    candidate = _priced_candidate("12-01-001-02", unit_price=1000.0)
    match_result = MatchResult(
        query_text="устройство кровли на битумной мастике",
        tender_purchase_number=TENDER_PURCHASE_NUMBER,
        candidates=[candidate],
    )
    assert not match_result.expert_reviewed
    line_item = SmetaLineItem(match_result=match_result, work_volume=8.5)

    with pytest.raises(ValueError, match="проверен экспертом"):
        build_cost_estimate([line_item], as_of_date=date(2026, 7, 1), region_name=REGION, period_label=PERIOD)


def test_unselected_candidate_goes_to_unpriced_not_silent_zero():
    candidate = _priced_candidate("12-01-001-02", unit_price=1000.0)
    match_result = _reviewed_match_result(None, [candidate])  # эксперт: ни один кандидат не подходит
    line_item = SmetaLineItem(match_result=match_result, work_volume=8.5)

    result = build_cost_estimate([line_item], as_of_date=date(2026, 7, 1), region_name=REGION, period_label=PERIOD)

    assert result.total_cost == 0.0
    assert result.priced_line_items == 0
    assert result.unpriced_line_items == ["устройство кровли на битумной мастике"]
    assert not result.is_complete()


def test_partially_priced_candidate_included_but_flagged():
    candidate = _priced_candidate("12-01-001-02", unit_price=1000.0, fully_priced=False)
    match_result = _reviewed_match_result("12-01-001-02", [candidate])
    line_item = SmetaLineItem(match_result=match_result, work_volume=8.5)

    result = build_cost_estimate([line_item], as_of_date=date(2026, 7, 1), region_name=REGION, period_label=PERIOD)

    assert result.total_cost == pytest.approx(1000.0 * 14.6 * 8.5)
    assert result.priced_line_items == 1
    assert result.unpriced_line_items == []
    assert result.partially_priced_line_items == ["устройство кровли на битумной мастике"]
    assert not result.is_complete()


def test_rejects_mismatched_region_or_period():
    candidate = _priced_candidate("12-01-001-02", unit_price=1000.0)
    match_result = _reviewed_match_result("12-01-001-02", [candidate])
    line_item = SmetaLineItem(match_result=match_result, work_volume=8.5)

    with pytest.raises(ValueError, match="региона/периода"):
        build_cost_estimate(
            [line_item], as_of_date=date(2026, 7, 1), region_name="г. Краснодар", period_label=PERIOD
        )


def test_rejects_empty_line_items():
    with pytest.raises(ValueError, match="пуст"):
        build_cost_estimate([], as_of_date=date(2026, 7, 1), region_name=REGION, period_label=PERIOD)


def test_to_cost_estimate_marks_expert_reviewed_and_carries_completeness_note():
    candidate = _priced_candidate("12-01-001-02", unit_price=1000.0)
    match_result = _reviewed_match_result("12-01-001-02", [candidate])
    line_item = SmetaLineItem(match_result=match_result, work_volume=8.5)
    result = build_cost_estimate([line_item], as_of_date=date(2026, 7, 1), region_name=REGION, period_label=PERIOD)

    cost_estimate = result.to_cost_estimate()

    assert cost_estimate.expert_reviewed
    assert cost_estimate.is_complete
    assert cost_estimate.total_cost == result.total_cost
    assert cost_estimate.as_of_date == date(2026, 7, 1)
    assert "Агент 4" in cost_estimate.source_note

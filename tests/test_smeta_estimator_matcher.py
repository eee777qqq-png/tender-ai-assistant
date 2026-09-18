"""Оркестрация: подбор кандидатов + обязательная проверка эксперта через
`quality_control` — тот же протокол контроля качества, что описан в
CLAUDE.md для агентов 3/4/6 (лог расхождений + метрика перехода на
выборочный аудит), реально подключённый к коду, а не просто структура на
будущее."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog
from smeta_estimator.fsnb_parser import apply_prices, parse_fsbc_machines_xml, parse_fsbc_materials_xml, parse_gesn_xml
from smeta_estimator.matcher import AGENT_NAME, match_work_item, review_match_result

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TENDER_PURCHASE_NUMBER = "0173200001426000101"  # капремонт кровли школы №5, тот же, что у остальных агентов


def load_roof_catalog():
    items = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(items, prices)
    return items


def test_match_work_item_returns_a_candidate_list_not_a_single_answer():
    catalog = load_roof_catalog()

    result = match_work_item(catalog, "устройство кровли из рулонных материалов", TENDER_PURCHASE_NUMBER)

    assert result.tender_purchase_number == TENDER_PURCHASE_NUMBER
    assert len(result.candidates) > 1  # список, не один "правильный" ответ
    assert result.expert_reviewed is False
    assert result.selected_code is None


def test_select_candidate_requires_a_named_reviewer():
    catalog = load_roof_catalog()
    result = match_work_item(catalog, "устройство кровли из рулонных материалов", TENDER_PURCHASE_NUMBER)

    with pytest.raises(ValueError, match="эксперт"):
        result.select_candidate(result.top_candidate().code, reviewer="")


def test_review_match_result_passes_quietly_when_expert_confirms_top_candidate():
    catalog = load_roof_catalog()
    result = match_work_item(catalog, "устройство кровли из рулонных материалов", TENDER_PURCHASE_NUMBER)
    top_code = result.top_candidate().code

    tracker = AuditReadinessTracker(AGENT_NAME)
    log = CategorizedDiscrepancyLog()

    review_match_result(result, reviewer="Edwin", selected_code=top_code, tracker=tracker, discrepancy_log=log)

    assert result.expert_reviewed is True
    assert result.selected_code == top_code
    assert result.expert_reviewer == "Edwin"
    assert tracker.window_stats()["total"] == 1
    assert tracker.window_stats()["ok_ratio"] == 1.0
    assert log.for_agent(AGENT_NAME) == []  # подтверждение топ-кандидата — не расхождение


def test_review_match_result_logs_a_discrepancy_when_expert_overrides_the_top_candidate():
    """Параметризация ГЭСН означает, что похожий текст — не обязательно тот
    самый код: эксперт вправе выбрать не первый кандидат, и это должно
    попасть в лог расхождений, а не потеряться молча."""
    catalog = load_roof_catalog()
    result = match_work_item(
        catalog, "устройство кровли на битумной мастике с защитным слоем из гравия", TENDER_PURCHASE_NUMBER
    )
    codes = [c.code for c in result.candidates]
    assert len(codes) >= 2
    top_code = codes[0]
    overridden_code = next(c for c in codes if c != top_code)

    tracker = AuditReadinessTracker(AGENT_NAME)
    log = CategorizedDiscrepancyLog()

    review_match_result(
        result,
        reviewer="Edwin",
        selected_code=overridden_code,
        tracker=tracker,
        discrepancy_log=log,
        expert_comment="по факту укладывали без гравийной защиты",
    )

    assert result.selected_code == overridden_code
    entries = log.for_agent(AGENT_NAME)
    assert len(entries) == 1
    assert overridden_code in entries[0].issue
    assert top_code in entries[0].issue
    assert entries[0].expert_comment == "по факту укладывали без гравийной защиты"
    assert tracker.window_stats()["ok_ratio"] == 0.0


def test_review_match_result_treats_no_selection_as_needing_correction():
    """Эксперт может решить, что ни один из кандидатов не подходит вообще —
    это тоже сигнал для метрики аудита, не тихий провал."""
    catalog = load_roof_catalog()
    result = match_work_item(catalog, "устройство кровли из рулонных материалов", TENDER_PURCHASE_NUMBER)

    tracker = AuditReadinessTracker(AGENT_NAME)
    log = CategorizedDiscrepancyLog()

    review_match_result(result, reviewer="Edwin", selected_code=None, tracker=tracker, discrepancy_log=log)

    assert result.selected_code is None
    assert result.expert_reviewed is True
    assert len(log.for_agent(AGENT_NAME)) == 1
    assert tracker.window_stats()["ok_ratio"] == 0.0

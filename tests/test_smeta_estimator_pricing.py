"""Приоритет расчёта цены ресурса (согласовано после диагностики
методологической ошибки, CLAUDE.md): текущая цена напрямую, иначе
базисная цена (01.01.2022) × индекс ГОСР для группы ресурса, иначе —
не определена."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator.models import GesnResourceUsage, MachineLabourInfo, RateCandidate
from smeta_estimator.pricing import price_candidate_for_region, price_candidates_for_region
from smeta_estimator.regional_pricing_parser import GosrIndexEntry


def make_candidate(resources: list[GesnResourceUsage]) -> RateCandidate:
    return RateCandidate(
        code="12-01-001-02", name="тест", unit="100 м2", base_price=0.0, match_score=1.0, resources=resources
    )


def test_current_price_takes_priority_over_gosr_index():
    candidate = make_candidate([GesnResourceUsage(resource_code="A", resource_name="ресурс А", quantity=2.0)])
    gosr_index = {"A": GosrIndexEntry("A", "ресурс А", "шт", base_price_2022=50.0, group_number=1, group_name="группа", index_value=3.0)}

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"A": 999.0},
        gosr_index=gosr_index,
        resource_base_prices={"A": 50.0},
    )

    assert priced.priced.total_price == pytest.approx(1998.0)  # 999 * 2, индекс не участвует
    assert priced.priced.resolutions[0].source == "current_price"
    assert priced.priced.resolutions[0].index_value is None


def test_falls_back_to_base_price_times_gosr_index_when_no_current_price():
    candidate = make_candidate([GesnResourceUsage(resource_code="A", resource_name="ресурс А", quantity=2.0)])
    gosr_index = {"A": GosrIndexEntry("A", "ресурс А", "шт", base_price_2022=50.0, group_number=1, group_name="группа", index_value=3.0)}

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={},
        gosr_index=gosr_index,
        resource_base_prices={"A": 50.0},
    )

    assert priced.priced.total_price == pytest.approx(300.0)  # 50 * 3.0 * 2
    assert priced.priced.resolutions[0].source == "gosr_index"
    assert priced.priced.resolutions[0].index_value == pytest.approx(3.0)
    assert priced.priced.resolutions[0].group_name == "группа"


def test_resource_without_current_price_or_gosr_index_is_marked_unresolved_not_zero():
    candidate = make_candidate([GesnResourceUsage(resource_code="A", resource_name="ресурс А", quantity=2.0)])

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={},
        gosr_index={},
        resource_base_prices={},
    )

    assert priced.priced.total_price == 0.0
    assert priced.priced.unresolved_resource_codes == ["A"]
    assert not priced.priced.is_fully_priced()


def test_abstract_resources_stay_unresolved_even_with_a_matching_gosr_entry():
    """AbstractResource — категория, не конкретный код продукта; даже если
    в ГОСР случайно нашёлся бы код с тем же значением, подставлять его
    нельзя — выбор конкретного продукта остаётся за экспертом."""
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="12.1.02.15", resource_name="категория материала", quantity=100.0, is_abstract=True)]
    )
    gosr_index = {
        "12.1.02.15": GosrIndexEntry("12.1.02.15", "что-то", "м2", base_price_2022=10.0, group_number=1, group_name="г", index_value=1.5)
    }

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={},
        gosr_index=gosr_index,
        resource_base_prices={"12.1.02.15": 10.0},
    )

    assert priced.priced.unresolved_resource_codes == ["12.1.02.15"]
    assert priced.priced.total_price == 0.0


def test_original_candidate_is_not_mutated():
    candidate = make_candidate([GesnResourceUsage(resource_code="A", resource_name="ресурс А", quantity=2.0)])

    price_candidate_for_region(
        candidate, "г. Москва", "3 квартал 2026 г.", current_prices={"A": 10.0}, gosr_index={}, resource_base_prices={}
    )

    assert candidate.priced is None


# -- добавка оплаты труда машиниста через LabourMach/DriverCode (закрытый
# пункт «Известных пробелов» — подтверждено устно на звонке со Smetrix и
# независимо, из первоисточника на fgiscs.minstroyrf.ru, 2026-09-24 —
# см. models.MachineLabourInfo) --------------------------------------------


def test_machinist_wage_is_added_when_labour_mach_is_positive():
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="CRANE", resource_name="кран", quantity=0.24)]
    )
    machine_labour = {"CRANE": MachineLabourInfo(resource_code="CRANE", labour_mach=1.0, driver_code="4-100-060")}

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"CRANE": 622.62, "4-100-060": 994.18},
        gosr_index={},
        resource_base_prices={},
        machine_labour=machine_labour,
    )

    resolution = priced.priced.resolutions[0]
    assert resolution.unit_price == pytest.approx(622.62 + 994.18)
    assert resolution.machinist_wage_added == pytest.approx(994.18)
    assert resolution.source == "current_price"  # добавка не меняет источник базовой цены машины
    assert priced.priced.total_price == pytest.approx((622.62 + 994.18) * 0.24)


def test_no_machinist_wage_added_when_labour_mach_is_zero():
    """Электрическое/самоходное оборудование без отдельного оператора —
    LabourMach=0, driver_code обычно вообще не указан."""
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="BOILER", resource_name="котёл битумный", quantity=5.8)]
    )
    machine_labour = {"BOILER": MachineLabourInfo(resource_code="BOILER", labour_mach=0.0, driver_code=None)}

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"BOILER": 95.25},
        gosr_index={},
        resource_base_prices={},
        machine_labour=machine_labour,
    )

    resolution = priced.priced.resolutions[0]
    assert resolution.unit_price == pytest.approx(95.25)
    assert resolution.machinist_wage_added == 0.0


def test_missing_machinist_wage_rate_marks_resource_unresolved_not_underpriced():
    """labour_mach > 0, но ставки для driver_code нет в current_prices —
    честный unresolved, не цена машины без оплаты труда оператора."""
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="CRANE", resource_name="кран", quantity=0.24)]
    )
    machine_labour = {"CRANE": MachineLabourInfo(resource_code="CRANE", labour_mach=1.0, driver_code="4-100-060")}

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"CRANE": 622.62},  # ставки 4-100-060 нет
        gosr_index={},
        resource_base_prices={},
        machine_labour=machine_labour,
    )

    assert priced.priced.unresolved_resource_codes == ["CRANE"]
    assert priced.priced.total_price == 0.0


def test_machine_labour_defaults_to_no_addition_when_not_passed():
    """Обратная совместимость: без `machine_labour` поведение как раньше —
    никакой добавки, даже если ресурс технически машина."""
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="CRANE", resource_name="кран", quantity=0.24)]
    )

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"CRANE": 622.62, "4-100-060": 994.18},
        gosr_index={},
        resource_base_prices={},
    )

    resolution = priced.priced.resolutions[0]
    assert resolution.unit_price == pytest.approx(622.62)
    assert resolution.machinist_wage_added == 0.0


def test_price_candidates_for_region_prices_every_candidate_in_the_list():
    candidates = [
        make_candidate([GesnResourceUsage(resource_code="A", resource_name="a", quantity=1.0)]),
        make_candidate([GesnResourceUsage(resource_code="A", resource_name="a", quantity=2.0)]),
    ]

    priced = price_candidates_for_region(
        candidates, "г. Москва", "3 квартал 2026 г.", current_prices={"A": 10.0}, gosr_index={}, resource_base_prices={}
    )

    assert [c.priced.total_price for c in priced] == [10.0, 20.0]

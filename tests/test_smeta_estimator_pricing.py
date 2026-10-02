"""Приоритет расчёта цены ресурса (согласовано после диагностики
методологической ошибки, CLAUDE.md): текущая цена напрямую, иначе
базисная цена (01.01.2022) × индекс ГОСР для группы ресурса, иначе —
не определена."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator.models import GesnResourceUsage, MachineLabourInfo, MaterialRateCandidate, RateCandidate
from smeta_estimator.order_modifiers import OrderModifier
from smeta_estimator.pricing import (
    price_candidate_for_region,
    price_candidates_for_region,
    price_material_candidate_for_region,
    price_material_candidates_for_region,
)
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


def test_bare_aggregate_labour_code_2_is_not_unresolved_and_contributes_zero():
    """Голый код "2" — итоговая рекап-строка «Затраты труда машинистов»,
    не самостоятельный ресурс (найдено и проверено на реальных документах
    круга 1, 2026-09-30 — см. CLAUDE.md/докстринг pricing.py). Даже если
    для кода "2" случайно нашлась бы current_price/gosr_index запись, она
    не должна использоваться — код отсекается раньше любого поиска цены,
    даёт distinct источник "aggregate_rollup", не "unresolved"."""
    candidate = make_candidate(
        [
            GesnResourceUsage(resource_code="1-100-30", resource_name="Средний разряд работы 3,0", quantity=1.0),
            GesnResourceUsage(resource_code="2", resource_name="", quantity=0.32),
        ]
    )

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"1-100-30": 657.26, "2": 999.0},  # "2" намеренно есть в current_prices — не должна использоваться
        gosr_index={},
        resource_base_prices={},
    )

    assert priced.priced.unresolved_resource_codes == []
    code2 = next(r for r in priced.priced.resolutions if r.resource_code == "2")
    assert code2.source == "aggregate_rollup"
    assert code2.unit_price == 0.0
    assert priced.priced.total_price == pytest.approx(657.26)  # только разрядный труд, "2" не задваивает


def test_bare_aggregate_labour_code_1_is_also_treated_as_rollup():
    candidate = make_candidate([GesnResourceUsage(resource_code="1", resource_name="", quantity=203.11)])

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={},
        gosr_index={},
        resource_base_prices={},
    )

    assert priced.priced.unresolved_resource_codes == []
    assert priced.priced.resolutions[0].source == "aggregate_rollup"
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


# --- price_material_candidate_for_region() — материал сам себе единственный
# ресурс, та же логика приоритета, что у одного ресурса внутри позиции ГЭСН. ---


def make_material_candidate(code: str = "A", base_price_2022: float = 50.0) -> MaterialRateCandidate:
    return MaterialRateCandidate(
        code=code, name="материал А", unit="шт", match_score=1.0, base_price_2022=base_price_2022
    )


def test_price_material_candidate_uses_current_price_when_available():
    candidate = make_material_candidate()

    priced = price_material_candidate_for_region(
        candidate, current_prices={"A": 999.0}, gosr_index={}
    )

    assert priced.unit_price == pytest.approx(999.0)
    assert priced.price_source == "current_price"


def test_price_material_candidate_falls_back_to_base_price_times_gosr_index():
    candidate = make_material_candidate(base_price_2022=50.0)
    gosr_index = {"A": GosrIndexEntry("A", "материал А", "шт", base_price_2022=50.0, group_number=1, group_name="группа", index_value=3.0)}

    priced = price_material_candidate_for_region(candidate, current_prices={}, gosr_index=gosr_index)

    assert priced.unit_price == pytest.approx(150.0)
    assert priced.price_source == "gosr_index"


def test_price_material_candidate_is_honestly_unresolved_without_any_source():
    candidate = make_material_candidate()

    priced = price_material_candidate_for_region(candidate, current_prices={}, gosr_index={})

    assert priced.unit_price is None
    assert priced.price_source is None


def test_price_material_candidates_for_region_prices_every_candidate_in_the_list():
    candidates = [make_material_candidate("A"), make_material_candidate("A")]

    priced = price_material_candidates_for_region(candidates, current_prices={"A": 10.0}, gosr_index={})

    assert [c.unit_price for c in priced] == [10.0, 10.0]


# --- OrderModifier (найдено на реальном документе "потолок", 2026-09-30,
# см. CLAUDE.md открытый п.19, order_modifiers.py) — множитель применяется
# к КОЛИЧЕСТВУ ресурса, по категории (рабочие/машины/машинисты/материалы). ---


def test_modifier_scales_labour_quantity_by_ozp():
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="1-100-38", resource_name="рабочий", quantity=10.0)]
    )
    modifier = OrderModifier(ozp=1.15, em=1.25, zpm=1.25, mat=1.0)

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"1-100-38": 100.0},
        gosr_index={},
        resource_base_prices={},
        modifier=modifier,
    )

    # 10 * 1.15 = 11.5 -> 11.5 * 100 = 1150, не 1000 (без модификатора).
    assert priced.priced.total_price == pytest.approx(1150.0)
    assert priced.priced.resolutions[0].quantity == pytest.approx(11.5)


def test_modifier_scales_machinist_quantity_by_zpm_not_ozp():
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="4-100-060", resource_name="машинист", quantity=10.0)]
    )
    # ozp и zpm разные — проверяем, что код "4-100-XXX" использует zpm,
    # а не общий шаблон "N-100-XXX" (ozp).
    modifier = OrderModifier(ozp=1.15, em=1.25, zpm=0.7, mat=1.0)

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"4-100-060": 100.0},
        gosr_index={},
        resource_base_prices={},
        modifier=modifier,
    )

    assert priced.priced.total_price == pytest.approx(700.0)  # 10 * 0.7 * 100


def test_modifier_scales_machine_quantity_by_em():
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="91.05.01-017", resource_name="кран", quantity=4.0)]
    )
    modifier = OrderModifier(ozp=1.0, em=1.25, zpm=1.0, mat=1.0)

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"91.05.01-017": 100.0},
        gosr_index={},
        resource_base_prices={},
        modifier=modifier,
    )

    assert priced.priced.total_price == pytest.approx(500.0)  # 4 * 1.25 * 100


def test_modifier_scales_material_quantity_by_mat_including_zero():
    # Реальный случай (571/пр п.83/84, демонтаж) — МАТ=0: материалы,
    # заложенные в норму монтажа, не расходуются при демонтаже.
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="01.7.15.06-0124", resource_name="гвозди", quantity=5.0)]
    )
    modifier = OrderModifier(ozp=0.4, em=0.4, zpm=0.4, mat=0.0)

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"01.7.15.06-0124": 100.0},
        gosr_index={},
        resource_base_prices={},
        modifier=modifier,
    )

    assert priced.priced.total_price == pytest.approx(0.0)
    assert priced.priced.resolutions[0].quantity == pytest.approx(0.0)


def test_no_modifier_keeps_quantity_unchanged():
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="1-100-38", resource_name="рабочий", quantity=10.0)]
    )

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={"1-100-38": 100.0},
        gosr_index={},
        resource_base_prices={},
    )

    assert priced.priced.total_price == pytest.approx(1000.0)


def test_modifier_applies_across_aggregate_rollup_and_unresolved_resources_too():
    # Модификатор масштабирует quantity ДО проверки категории ресурса —
    # для честной диагностики (--show-resources) даже у рекапов/unresolved,
    # хотя на их итоговый вклад в total_price это не влияет (0/None).
    candidate = make_candidate(
        [
            GesnResourceUsage(resource_code="2", resource_name="ЭМ", quantity=10.0),
            GesnResourceUsage(resource_code="X", resource_name="абстрактный", quantity=4.0, is_abstract=True),
        ]
    )
    modifier = OrderModifier(ozp=1.0, em=2.0, zpm=1.0, mat=1.0)

    priced = price_candidate_for_region(
        candidate,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={},
        gosr_index={},
        resource_base_prices={},
        modifier=modifier,
    )

    aggregate = next(r for r in priced.priced.resolutions if r.resource_code == "2")
    abstract = next(r for r in priced.priced.resolutions if r.resource_code == "X")
    assert aggregate.quantity == pytest.approx(10.0)  # код "2" не начинается с "9X." -> категория "mat", mat=1.0
    assert abstract.quantity == pytest.approx(4.0)  # тоже категория "mat"


# --- Ресурс, обнулённый в документе внутри позиции (замена ресурса
# отдельной строкой — реальный документ МО, 2026-10-02, см. CLAUDE.md
# открытый п.17) — не оценивается по норме каталога, иначе двойной счёт. ---


def test_zeroed_resource_contributes_nothing_and_is_not_unresolved():
    candidate = make_candidate(
        [
            GesnResourceUsage(resource_code="1-100-32", resource_name="рабочий", quantity=106.0),
            GesnResourceUsage(resource_code="06.2.02.01-0061", resource_name="плитка", quantity=102.0),
        ]
    )

    priced = price_candidate_for_region(
        candidate,
        region_name="Московская область",
        period_label="3 квартал 2026 г.",
        current_prices={"1-100-32": 500.0, "06.2.02.01-0061": 1200.0},
        gosr_index={},
        resource_base_prices={},
        zeroed_resource_codes=frozenset({"06.2.02.01-0061"}),
    )

    # Только труд: 106 * 500 — плитка (102 * 1200 = 122 400) не входит.
    assert priced.priced.total_price == pytest.approx(53000.0)
    tile = priced.priced.resolutions[1]
    assert tile.source == "zeroed_in_document"
    assert tile.line_total == 0.0
    assert priced.priced.unresolved_resource_codes == []


def test_without_zeroed_codes_resource_is_priced_by_norm_as_before():
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="06.2.02.01-0061", resource_name="плитка", quantity=102.0)]
    )

    priced = price_candidate_for_region(
        candidate,
        region_name="Московская область",
        period_label="3 квартал 2026 г.",
        current_prices={"06.2.02.01-0061": 1200.0},
        gosr_index={},
        resource_base_prices={},
    )

    assert priced.priced.total_price == pytest.approx(122400.0)
    assert priced.priced.resolutions[0].source == "current_price"


def test_zeroed_codes_are_passed_through_price_candidates_for_region():
    candidate = make_candidate(
        [GesnResourceUsage(resource_code="06.2.02.01-0061", resource_name="плитка", quantity=102.0)]
    )

    priced = price_candidates_for_region(
        [candidate, candidate],
        region_name="Московская область",
        period_label="3 квартал 2026 г.",
        current_prices={"06.2.02.01-0061": 1200.0},
        gosr_index={},
        resource_base_prices={},
        zeroed_resource_codes=frozenset({"06.2.02.01-0061"}),
    )

    assert [c.priced.total_price for c in priced] == [0.0, 0.0]

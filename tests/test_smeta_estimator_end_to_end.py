"""Сквозной прогон фундамента Агента 4 на одной из уже существующих
тестовых закупок проекта: каталог ГЭСН/ФСБЦ -> поиск кандидатов по тексту
работы -> региональная цена -> обязательное решение эксперта, с логом
расхождений и метрикой готовности к выборочному аудиту.

Печатает результат каждого шага при запуске с `pytest -s`, по аналогии с
`tests/test_end_to_end_pipeline.py`.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog
from smeta_estimator import (
    AGENT_NAME,
    apply_prices,
    match_work_item,
    parse_fsbc_machines_xml,
    parse_fsbc_materials_xml,
    parse_gesn_xml,
    price_candidates,
    review_match_result,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TENDER_PURCHASE_NUMBER = "0173200001426000101"  # капремонт кровли школы №5

# Реальный индекс Москвы за III квартал 2026, найденный в письме Минстроя
# от 28.08.2026 № 53691-АЛ/09 при проверке источника (не выдуман для теста).
MOSCOW_INDEX_VALUE = 14.94
MOSCOW_INDEX_AS_OF = date(2026, 8, 28)


def test_end_to_end_search_price_and_expert_review_for_a_roofing_work_item():
    print("\n=== Агент 4: загрузка каталога ГЭСН/ФСБЦ (тестовый фрагмент, Сборник 12 «Кровли») ===")
    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(catalog, prices)
    print(f"  Позиций в каталоге: {len(catalog)}")

    query = "устройство кровли на битумной мастике с защитным слоем из гравия"
    print(f"\n=== Поиск кандидатов по тексту работы: {query!r} ===")
    result = match_work_item(catalog, query, TENDER_PURCHASE_NUMBER)
    assert result.candidates

    priced_candidates = price_candidates(
        result.candidates, index_value=MOSCOW_INDEX_VALUE, index_as_of=MOSCOW_INDEX_AS_OF, region_name="г. Москва"
    )
    result.candidates = priced_candidates
    for c in result.candidates:
        flags = []
        if c.unpriced_resource_codes:
            flags.append(f"не оценено трудозатрат: {len(c.unpriced_resource_codes)}")
        if c.abstract_resource_codes:
            flags.append(f"категорий материалов без выбора продукта: {len(c.abstract_resource_codes)}")
        flags_text = f" [{'; '.join(flags)}]" if flags else ""
        print(
            f"  {c.code} (score={c.match_score:.2f}): {c.name} — "
            f"{c.base_price:,.2f} руб. база -> {c.regional_price:,.2f} руб. (г. Москва, III кв. 2026){flags_text}"
        )

    top = result.top_candidate()
    print(f"\n=== Решение эксперта: подтверждён топ-кандидат {top.code} ===")
    tracker = AuditReadinessTracker(AGENT_NAME)
    log = CategorizedDiscrepancyLog()
    review_match_result(result, reviewer="Edwin", selected_code=top.code, tracker=tracker, discrepancy_log=log)

    print(f"  Выбрано: {result.selected_code}, эксперт: {result.expert_reviewer}")
    print(f"  Метрика готовности к выборочному аудиту: {tracker.window_stats()}")
    print(f"  Расхождений залогировано: {len(log.for_agent(AGENT_NAME))}")

    assert result.expert_reviewed
    assert result.selected_code == top.code
    assert top.regional_price == top.base_price * MOSCOW_INDEX_VALUE
    assert log.for_agent(AGENT_NAME) == []

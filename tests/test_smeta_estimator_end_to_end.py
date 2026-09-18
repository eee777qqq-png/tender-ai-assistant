"""Сквозной прогон фундамента Агента 4 на одной из уже существующих
тестовых закупок проекта: каталог ГЭСН/ФСБЦ -> поиск кандидатов по тексту
работы -> региональная цена (текущая цена или базисная × индекс ГОСР для
группы ресурса, приоритет — см. `pricing.py`) -> обязательное решение
эксперта, с логом расхождений и метрикой готовности к выборочному аудиту.

**Использует правильный источник индексов** — исправлено 2026-09-18 после
диагностики методологической ошибки (CLAUDE.md, «Известные пробелы»):
раньше здесь ошибочно был индекс из общего письма «к ФЕР-2001/ТЕР-2001»
(14.94 для Москвы, для другой методологии, задваивал пересчёт); теперь —
настоящие индексы ГОСР для конкретных групп ресурсов, найденные вживую на
fgiscs.minstroyrf.ru/prices для Москвы, 3 квартал 2026 года.

**Дополнено тем же вечером** — трудозатраты рабочих (`RimWorkerSalaryRegistry`)
и текущие цены на те машинные ресурсы, для которых в этом квартале не
нашлось индекса ГОСР (см. `test_smeta_estimator_labor_and_pagination.py`) —
теперь неопределённой остаётся цена только у категорий материалов без
выбранного продукта и у одного не расшифрованного кода ГЭСН («2»,
см. CLAUDE.md, «Известные пробелы»).

Печатает результат каждого шага при запуске с `pytest -s`, по аналогии с
`tests/test_end_to_end_pipeline.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog
from smeta_estimator import (
    AGENT_NAME,
    apply_prices,
    match_work_item,
    parse_current_prices_json,
    parse_fsbc_machines_xml,
    parse_fsbc_materials_xml,
    parse_gesn_xml,
    parse_gosr_workbook,
    parse_worker_salary_registry,
    price_candidates_for_region,
    review_match_result,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TENDER_PURCHASE_NUMBER = "0173200001426000101"  # капремонт кровли школы №5


def test_end_to_end_search_price_and_expert_review_for_a_roofing_work_item():
    print("\n=== Агент 4: загрузка каталога ГЭСН/ФСБЦ (тестовый фрагмент, Сборник 12 «Кровли») ===")
    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    resource_base_prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(catalog, resource_base_prices)
    print(f"  Позиций в каталоге: {len(catalog)}")

    print("\n=== Загрузка индексов ГОСР (реальный фрагмент, г. Москва, 3 квартал 2026) ===")
    gosr_index = parse_gosr_workbook((FIXTURES / "gosr_moscow_q3_2026_sample.xlsx").read_bytes())
    print(f"  Загружено индексов по кодам ресурсов: {len(gosr_index)}")

    print("\n=== Загрузка текущих цен на машины и оплаты труда (реальные фрагменты) ===")
    current_prices = {
        **parse_current_prices_json((FIXTURES / "current_prices_moscow_machines_sample.json").read_bytes()),
        **parse_worker_salary_registry((FIXTURES / "worker_salary_moscow_sample.json").read_bytes()),
    }
    print(f"  Загружено текущих цен/ставок по кодам ресурсов: {len(current_prices)}")

    query = "устройство кровли на битумной мастике с защитным слоем из гравия"
    print(f"\n=== Поиск кандидатов по тексту работы: {query!r} ===")
    result = match_work_item(catalog, query, TENDER_PURCHASE_NUMBER)
    assert result.candidates

    priced_candidates = price_candidates_for_region(
        result.candidates,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices=current_prices,
        gosr_index=gosr_index,
        resource_base_prices=resource_base_prices,
    )
    result.candidates = priced_candidates
    for c in result.candidates:
        priced = c.priced
        print(
            f"  {c.code} (score={c.match_score:.2f}): {c.name} — "
            f"{priced.total_price:,.2f} руб. (г. Москва, 3 кв. 2026, приоритет: текущая цена -> "
            f"база 01.01.2022 x индекс ГОСР группы ресурса)"
        )
        for r in priced.resolutions:
            if r.source == "unresolved":
                continue
            print(f"      {r.resource_code} [{r.source}] {r.resource_name}: {r.unit_price:,.2f} руб./ед.")
        if priced.unresolved_resource_codes:
            print(f"      Не определена цена: {priced.unresolved_resource_codes}")

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
    assert top.priced.total_price > 0
    # Оба источника региональной цены реально задействованы — не только ГОСР.
    assert any(r.source == "gosr_index" for r in top.priced.resolutions)
    assert any(r.source == "current_price" for r in top.priced.resolutions)
    # После добавления оплаты труда и текущих цен на машины неопределённой
    # остаётся цена только у категорий материалов без выбранного продукта и
    # у нерасшифрованного кода ГЭСН "2" (см. CLAUDE.md, «Известные пробелы»).
    assert set(top.priced.unresolved_resource_codes) <= {"2", "01.2.03.03", "12.1.02.15"}
    assert log.for_agent(AGENT_NAME) == []

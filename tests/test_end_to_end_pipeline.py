"""Сквозной прогон одного тестового профиля и одной тестовой закупки через
всю связанную цепочку агентов: Классификатор (2) -> Аналитик документации
(3) -> Сборщик документов (6) -> Проверка комплектности (7) -> Консультант
для клиента (8) -> Сметчик (4) -> Оценка выгоды (5).

Агент 3 теперь реально подключён к Агенту 6 (не в обход него): пакет
собирается с `extracted_requirements`, поэтому обязательность полей
обеспечения и допусков учитывает то, что извлечено из текста документации,
а не только структурированные поля `Tender`. Обязательная проверка
эксперта результата Агента 3 (`expert_reviewed`) в этой цепочке не
автоматизирована — `mark_expert_reviewed()` ниже лишь имитирует то
единственное решение, которое в реальном процессе принимает человек;
без него `assemble_document_package()` отказал бы (см.
`tests/test_document_assembler.py::test_rejects_unreviewed_agent_3_output`).

Агент 4 (сметчик) теперь тоже реально подключён — итоговая себестоимость
Агента 5 больше не придуманное число, а `smeta_estimator.build_cost_estimate()`
над настоящим подобранным и проверенным экспертом кандидатом ГЭСН/ФСБЦ для
одной позиции сметы (тот же фрагмент реальных данных ФГИС ЦС по Москве,
что и в `tests/test_smeta_estimator_end_to_end.py`). **Место, где стык
получился не таким гладким, как у Агента 3 -> Агента 6**: Агент 4 не
считает объём работ по смете конкретной закупки (сколько именно "100 м2"
кровли нужно отремонтировать) — этого объём работ в тестовых данных проекта
тоже нет, поэтому `work_volume` ниже подставлен вручную, как правдоподобная
иллюстрация, а не посчитан. Это не заглушка на месте него самого (сама цена
за единицу — настоящая, из ФГИС ЦС), а честно отмеченный отдельный пробел
Агента 4 (см. CLAUDE.md, «не реализовано»).

Агент 1 (монитор) в эту цепочку пока не встроен — ждёт реальных данных ЕИС
(см. CLAUDE.md).

Печатает результат каждого шага при запуске с `pytest -s`, чтобы можно было
увидеть весь путь профиль+закупка -> вердикт, а не только факт прохождения.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from classifier import ConstructionClassifier, match_profile_to_tender
from classifier.sample_tenders import SAMPLE_TENDERS
from client_consultant import build_client_summary, render_summary_text
from completeness_check import check_completeness
from document_analyst import extract_requirements
from document_analyst.sample_documents import SAMPLE_DOCUMENTS
from document_assembler import assemble_document_package
from onboarding import (
    Capacity,
    ClientProfile,
    CompletedContract,
    FinancialReadiness,
    LegalInfo,
    PermitsExperience,
    TaxRegimeChoice,
    validate_profile,
)
from profitability_estimator import estimate_profitability
from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog
from smeta_estimator import (
    AGENT_NAME as AGENT_4_NAME,
    SmetaLineItem,
    apply_prices,
    build_cost_estimate,
    match_work_item,
    parse_current_prices_json,
    parse_fsbc_machine_labour_xml,
    parse_fsbc_machines_xml,
    parse_fsbc_materials_xml,
    parse_gesn_xml,
    parse_gosr_workbook,
    parse_worker_salary_registry,
    price_candidates_for_region,
    review_match_result,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SMETA_REGION_NAME = "г. Москва"
SMETA_PERIOD_LABEL = "3 квартал 2026 г."


def _build_ready_profile() -> ClientProfile:
    """Один тестовый профиль, доведённый до READY по полному пайплайну
    Агента 11 (валидация формата -> экспертная проверка -> mark_ready) —
    как и должно быть перед тем, как им воспользуется Агент 6."""
    profile = ClientProfile(
        client_id="e2e-client-1",
        region_code="77",
        legal=LegalInfo(
            org_name="ООО СтройМастер",
            inn="7701234567",
            ogrn="1027700132195",
            legal_address="г. Москва, ул. Примерная, д. 1",
            contact_person="Иванов Иван",
            phone="+79991234567",
            email="info@example.ru",
        ),
        permits_experience=PermitsExperience(
            sro_membership=True,
            sro_number="СРО-С-123-456",
            completed_contracts=[
                CompletedContract(
                    object_name="Капремонт школы №5", customer="ДепОбр", amount=5_000_000, year=2024
                )
            ],
            years_of_experience=5,
        ),
        capacity=Capacity(staff_count=15, own_workforce_description="15 штатных рабочих"),
        financial=FinancialReadiness(
            tax_regime=TaxRegimeChoice.USN_6_NO_VAT,
            avg_annual_revenue=50_000_000,
            working_capital=3_000_000,
            bank_guarantee_available=True,
        ),
    )
    validate_profile(profile)
    profile.submit_expert_review(reviewer="Edwin", approved=True)
    profile.mark_ready()
    return profile


def test_pipeline_from_classifier_through_document_analyst_to_completeness_check():
    profile = _build_ready_profile()
    tender = next(t for t in SAMPLE_TENDERS if t.purchase_number == "0173200001426000101")

    print(f"\n=== Вход ===")
    print(f"Профиль: {profile.client_id}, статус={profile.status.value}, регион={profile.region_code}")
    print(f"Закупка: {tender.purchase_number} — {tender.name}")
    print(f"  НМЦК={tender.max_price:,.0f} руб., регион={tender.region_code}, "
          f"СРО={'требуется' if tender.requires_sro else 'не требуется'}, "
          f"опыт от {tender.min_experience_years} лет")

    # Агент 2 — классификатор: сектор + сопоставление профиля с закупкой
    classifier = ConstructionClassifier()
    match = match_profile_to_tender(profile, tender, classifier)

    print(f"\n=== Агент 2: Классификатор ===")
    for c in match.criteria:
        print(f"  [{'OK' if c.passed else 'FAIL'}] {c.name}: {c.message}")
    print(f"  Итог: {'ПОДХОДИТ' if match.is_match else 'НЕ ПОДХОДИТ'} (score={match.score:.2f})")

    assert match.is_match, f"Тестовые данные подобраны так, чтобы совпасть: {match.failed_reasons}"

    # Агент 3 — аналитик документации: извлечение требований из текста закупки
    extracted = extract_requirements(tender.purchase_number, SAMPLE_DOCUMENTS[tender.purchase_number])

    print(f"\n=== Агент 3: Аналитик документации ===")
    print(f"  Срок подачи: {extracted.timeline.submission_deadline}")
    print(f"  Срок исполнения: {extracted.timeline.performance_start} — {extracted.timeline.performance_end}")
    for sec in extracted.security_requirements:
        print(f"  Обеспечение [{sec.kind}]: {sec.percentage}% (сумма={sec.amount})")
    for req in extracted.participant_requirements:
        print(f"  Требование к участнику [{req.kind}]: {req.description}")
    for risk in extracted.hidden_risks:
        print(f"  РИСК [{risk.category.value}]: {risk.explanation}")

    # По протоколу контроля качества выдача Агента 3 не уходит дальше без
    # подтверждения эксперта. Здесь это решение принимает не код, а вызов
    # ниже, стоящий за место реального человека, — подключение к Агенту 6
    # его не убирает и не подменяет (без него сборка ниже отказала бы).
    extracted.mark_expert_reviewed(reviewer="Edwin")
    print(f"  Проверено экспертом: {extracted.expert_reviewed} (эксперт: {extracted.expert_reviewer})")

    # Агент 6 — сборщик документов: заготовка пакета по профилю+закупке
    # +извлечённым Агентом 3 требованиям
    package = assemble_document_package(profile, tender, extracted_requirements=extracted)

    print(f"\n=== Агент 6: Сборщик документов ===")
    for f in package.fields:
        print(f"  [{f.status:<14}] {f.name} = {f.value!r} (из {f.source}, обязательно={f.required})")
    print(f"  Скрытые риски в пакете (для будущего Агента 8): {len(package.hidden_risks)}")

    # Агент 7 — проверка комплектности пакета
    result = check_completeness(package, tender)

    print(f"\n=== Агент 7: Проверка комплектности ===")
    print(f"  Статус: {result.status.value}")
    print(f"  Недостающие обязательные поля: {result.missing_required_fields or '(нет)'}")
    print(f"  Недостающие необязательные поля (информационно): {result.missing_optional_fields or '(нет)'}")

    assert result.is_pass()
    assert result.missing_required_fields == []
    # Обеспечение исполнения контракта — требование, которого нет в самом
    # Tender, оно есть только в тексте документации; появляется в пакете
    # именно благодаря подключению Агента 3.
    by_name = {f.name: f for f in package.fields}
    assert "Обеспечение исполнения контракта (банковская гарантия)" in by_name
    assert len(package.hidden_risks) == 2

    # Агент 8 — консультант для клиента: собирает итог всей цепочки в
    # сводку для собственника, без кодов статусов и жаргона.
    summary = build_client_summary(match, result, package)

    print(f"\n=== Агент 8: Консультант для клиента ===")
    print(render_summary_text(summary))

    assert summary.tender_fits
    assert summary.package_ready
    assert summary.missing_documents == []
    assert len(summary.risks) == 2

    # Агент 4 — сметчик: подбор расценки ГЭСН/ФСБЦ под одну позицию сметы
    # (капремонт кровли), региональный пересчёт цены и обязательное решение
    # эксперта — на настоящем фрагменте данных ФГИС ЦС по Москве.
    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    resource_base_prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(catalog, resource_base_prices)
    gosr_index = parse_gosr_workbook((FIXTURES / "gosr_moscow_q3_2026_sample.xlsx").read_bytes())
    current_prices = {
        **parse_current_prices_json((FIXTURES / "current_prices_moscow_machines_sample.json").read_bytes()),
        **parse_worker_salary_registry((FIXTURES / "worker_salary_moscow_sample.json").read_bytes()),
    }
    # Оплата труда машиниста через LabourMach/DriverCode — подтверждено пока
    # только устно на звонке со Smetrix, не письменно (см. CLAUDE.md,
    # «Известные пробелы», п.12, не закрыт; models.MachineLabourInfo).
    machine_labour = parse_fsbc_machine_labour_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes())

    match_result = match_work_item(
        catalog, "устройство кровли на битумной мастике с защитным слоем из гравия", tender.purchase_number
    )
    match_result.candidates = price_candidates_for_region(
        match_result.candidates,
        region_name=SMETA_REGION_NAME,
        period_label=SMETA_PERIOD_LABEL,
        current_prices=current_prices,
        gosr_index=gosr_index,
        resource_base_prices=resource_base_prices,
        machine_labour=machine_labour,
    )
    top = match_result.top_candidate()

    print(f"\n=== Агент 4: Сметчик ===")
    print(f"  Позиция ГЭСН: {top.code} — {top.name} ({top.unit})")
    print(f"  Цена за единицу (регион {SMETA_REGION_NAME}, {SMETA_PERIOD_LABEL}): {top.priced.total_price:,.2f} руб.")

    # По протоколу контроля качества выбор кандидата — обязательное решение
    # эксперта, с тем же логом расхождений и метрикой готовности к
    # выборочному аудиту, что уже подключены у Агента 4 (см.
    # `tests/test_smeta_estimator_end_to_end.py`).
    tracker = AuditReadinessTracker(AGENT_4_NAME)
    discrepancy_log = CategorizedDiscrepancyLog()
    review_match_result(
        match_result, reviewer="Edwin", selected_code=top.code, tracker=tracker, discrepancy_log=discrepancy_log
    )
    print(f"  Проверено экспертом: {match_result.expert_reviewed} (эксперт: {match_result.expert_reviewer})")

    # Объём работ по смете этой конкретной закупки Агент 4 не считает (см.
    # CLAUDE.md, «не реализовано») — тестовые данные проекта тоже не содержат
    # реальной ведомости объёмов работ, поэтому здесь это правдоподобная
    # иллюстрация (8.5 x "100 м2" = 850 м2 кровли), а не вычисленное значение.
    smeta_result = build_cost_estimate(
        [SmetaLineItem(match_result=match_result, work_volume=8.5)],
        as_of_date=date(2026, 9, 18),
        region_name=SMETA_REGION_NAME,
        period_label=SMETA_PERIOD_LABEL,
    )
    print(f"  Себестоимость (850 м2 кровли): {smeta_result.total_cost:,.2f} руб., "
          f"полностью оценена={smeta_result.is_complete()}")
    assert smeta_result.total_cost > 0
    # Честно НЕ полностью оценена — у выбранного кандидата остаются
    # AbstractResource без выбранного экспертом продукта (см. CLAUDE.md,
    # «Известные пробелы», п.13); себестоимость ниже реальной, не точная.
    assert not smeta_result.is_complete()
    assert smeta_result.partially_priced_line_items

    # Агент 5 — оценка выгоды: реальная себестоимость от Агента 4 (не
    # переданное вручную число), плюс скрытые риски и требования к
    # участнику от Агента 3.
    cost_estimate = smeta_result.to_cost_estimate()
    profitability = estimate_profitability(profile, tender, cost_estimate, extracted)

    print(f"\n=== Агент 5: Оценка выгоды ===")
    print(f"  НМЦК={profitability.max_price:,.0f} руб., себестоимость={profitability.cost_estimate:,.2f} руб. "
          f"(по состоянию на {profitability.cost_estimate_as_of})")
    print(f"  Обеспечение={profitability.security_cost:,.2f} руб., налоги={profitability.taxes:,.2f} руб.")
    print(f"  Маржа: {profitability.margin:,.2f} руб.")
    for flag in profitability.risk_flags:
        print(f"  РИСК: {flag}")

    assert cost_estimate.expert_reviewed
    assert not cost_estimate.is_complete
    assert profitability.cost_estimate == smeta_result.total_cost
    assert any("неполная" in f and "завышена" in f for f in profitability.risk_flags)
    assert profitability.margin is not None
    assert profitability.margin == pytest.approx(
        tender.max_price - smeta_result.total_cost - profitability.security_cost - profitability.taxes,
        abs=0.5,
    )


def test_pipeline_fails_completeness_when_required_field_missing():
    """Тот же путь через Агента 3, но с намеренно неполным профилем —
    цепочка должна дойти до конца и вернуть FAIL с точным списком
    недостающих полей, а не упасть молча или пройти PASS."""
    profile = _build_ready_profile()
    profile.legal.contact_person = ""  # намеренный пробел после READY
    tender = next(t for t in SAMPLE_TENDERS if t.purchase_number == "0173200001426000101")

    classifier = ConstructionClassifier()
    match = match_profile_to_tender(profile, tender, classifier)
    assert match.is_match  # пробел в контактном лице не влияет на матчинг

    extracted = extract_requirements(tender.purchase_number, SAMPLE_DOCUMENTS[tender.purchase_number])
    extracted.mark_expert_reviewed(reviewer="Edwin")

    package = assemble_document_package(profile, tender, extracted_requirements=extracted)
    result = check_completeness(package, tender)

    print(f"\n=== Агент 7 (намеренно неполный профиль): {result.status.value} ===")
    print(f"  Недостающие обязательные поля: {result.missing_required_fields}")

    assert not result.is_pass()
    assert result.missing_required_fields == ["Контактное лицо"]

    summary = build_client_summary(match, result, package)
    print(f"\n=== Агент 8 (намеренно неполный профиль) ===")
    print(render_summary_text(summary))

    assert not summary.package_ready
    assert len(summary.missing_documents) == 1
    assert summary.missing_documents[0].name == "Контактное лицо"

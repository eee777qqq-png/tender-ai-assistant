"""Сквозной прогон одного тестового профиля и одной тестовой закупки через
всю связанную цепочку агентов: Классификатор (2) -> Аналитик документации
(3) -> Сборщик документов (6) -> Проверка комплектности (7) -> Консультант
для клиента (8).

Агент 3 теперь реально подключён к Агенту 6 (не в обход него): пакет
собирается с `extracted_requirements`, поэтому обязательность полей
обеспечения и допусков учитывает то, что извлечено из текста документации,
а не только структурированные поля `Tender`. Обязательная проверка
эксперта результата Агента 3 (`expert_reviewed`) в этой цепочке не
автоматизирована — `mark_expert_reviewed()` ниже лишь имитирует то
единственное решение, которое в реальном процессе принимает человек;
без него `assemble_document_package()` отказал бы (см.
`tests/test_document_assembler.py::test_rejects_unreviewed_agent_3_output`).

Агенты 1 (монитор), 4 (сметчик), 5 (скоринг) в эту цепочку пока не
встроены — либо ждут реальных данных ЕИС, либо не специфицированы (см.
CLAUDE.md).

Печатает результат каждого шага при запуске с `pytest -s`, чтобы можно было
увидеть весь путь профиль+закупка -> вердикт, а не только факт прохождения.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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
    validate_profile,
)


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
            tax_system="УСН",
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

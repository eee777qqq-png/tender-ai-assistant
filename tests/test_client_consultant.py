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
from legal_boundaries import CLIENT_SUMMARY_LEGAL_NOTICE
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


def make_ready_profile() -> ClientProfile:
    """Тот же тестовый профиль e2e-client-1, что и в
    tests/test_end_to_end_pipeline.py — консультант работает на итоге всей
    цепочки, поэтому тестируем его на той же связке профиль+закупка."""
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


def find_tender(purchase_number: str):
    return next(t for t in SAMPLE_TENDERS if t.purchase_number == purchase_number)


def run_pipeline(profile: ClientProfile, purchase_number: str):
    tender = find_tender(purchase_number)
    classifier = ConstructionClassifier()
    match = match_profile_to_tender(profile, tender, classifier)

    extracted = extract_requirements(purchase_number, SAMPLE_DOCUMENTS[purchase_number])
    extracted.mark_expert_reviewed(reviewer="Edwin")

    package = assemble_document_package(profile, tender, extracted_requirements=extracted)
    completeness = check_completeness(package, tender)
    return match, completeness, package


def test_summary_for_ready_profile_reports_fit_and_pass_in_plain_language():
    profile = make_ready_profile()
    match, completeness, package = run_pipeline(profile, "0173200001426000101")

    summary = build_client_summary(match, completeness, package)

    assert summary.client_id == "e2e-client-1"
    assert summary.tender_fits is True
    assert "подходит" in summary.tender_fit_explanation
    assert summary.package_ready is True
    assert "готов" in summary.package_status_explanation
    assert summary.missing_documents == []

    # Документ содержит два скрытых риска (одностороннее изменение условий +
    # безлимитная неустойка) — оба должны попасть в сводку переведёнными.
    assert len(summary.risks) == 2
    for risk in summary.risks:
        assert risk.what_it_says  # explanation агента 3, не пустое
        assert risk.why_it_matters  # практическое значение, не пустое
        assert risk.quote  # цитата-источник, не пустое

    assert "решение" in summary.decision_reminder.lower()
    assert "собственник" in summary.decision_reminder.lower()


def test_summary_lists_missing_documents_with_actionable_hints():
    profile = make_ready_profile()
    profile.legal.contact_person = ""  # намеренный пробел, как и в e2e-тесте
    match, completeness, package = run_pipeline(profile, "0173200001426000101")

    summary = build_client_summary(match, completeness, package)

    assert summary.package_ready is False
    assert "не готов" in summary.package_status_explanation
    assert len(summary.missing_documents) == 1
    item = summary.missing_documents[0]
    assert item.name == "Контактное лицо"
    assert "контактное лицо" in item.what_to_do.lower()


def test_summary_reports_when_tender_does_not_fit():
    profile = make_ready_profile()
    profile.region_code = "50"  # клиент работает не в том регионе, что закупка
    match, completeness, package = run_pipeline(profile, "0173200001426000101")

    summary = build_client_summary(match, completeness, package)

    assert summary.tender_fits is False
    assert "не подходит" in summary.tender_fit_explanation
    assert "регион" in summary.tender_fit_explanation.lower()


def test_rejects_mismatched_results_from_different_tenders():
    profile = make_ready_profile()
    match, completeness, package = run_pipeline(profile, "0173200001426000101")
    _, other_completeness, other_package = run_pipeline(profile, "0350200003426000202")

    try:
        build_client_summary(match, other_completeness, package)
        assert False, "ожидался ValueError"
    except ValueError as exc:
        assert "разным закупкам" in str(exc)


def test_render_summary_text_is_plain_and_includes_all_sections():
    profile = make_ready_profile()
    match, completeness, package = run_pipeline(profile, "0173200001426000101")
    summary = build_client_summary(match, completeness, package)

    text = render_summary_text(summary)

    assert "Капитальный ремонт кровли" in text
    assert "Подходит ли закупка" in text
    assert "Готовность документов" in text
    assert "риски" in text.lower()
    assert summary.decision_reminder in text
    # Без кодов статусов — это должен быть текст для собственника, не для разработчика.
    assert "PASS" not in text
    assert "FAIL" not in text


def test_render_summary_text_always_includes_legal_boundary_notice():
    """Агент 12: предупреждение о границах ответственности обязательно
    в каждой сводке, независимо от результатов остальных агентов —
    проверяем и на «подходит»-кейсе, и на «не подходит»-кейсе."""
    profile = make_ready_profile()
    match, completeness, package = run_pipeline(profile, "0173200001426000101")
    fitting_summary = build_client_summary(match, completeness, package)
    assert CLIENT_SUMMARY_LEGAL_NOTICE in render_summary_text(fitting_summary)

    profile_not_fitting = make_ready_profile()
    profile_not_fitting.region_code = "50"
    match2, completeness2, package2 = run_pipeline(profile_not_fitting, "0173200001426000101")
    non_fitting_summary = build_client_summary(match2, completeness2, package2)
    assert CLIENT_SUMMARY_LEGAL_NOTICE in render_summary_text(non_fitting_summary)

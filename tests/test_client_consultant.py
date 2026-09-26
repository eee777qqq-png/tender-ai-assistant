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
from profitability_estimator import CostEstimate, estimate_profitability


def make_ready_profile() -> ClientProfile:
    """Тот же тестовый профиль e2e-client-1, что и в
    tests/test_end_to_end_pipeline.py — консультант работает на итоге всей
    цепочки, поэтому тестируем его на той же связке профиль+закупка."""
    profile = ClientProfile(
        client_id="e2e-client-1",
        region_codes=["77"],
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
    return match, completeness, package, extracted


def run_profitability(profile: ClientProfile, purchase_number: str, extracted, total_cost: float = 6_000_000):
    """Реальный вызов Агента 5 (не подставной объект) — та же схема, что в
    tests/test_profitability_estimator.py: себестоимость передана вручную,
    но обязательно с expert_reviewed=True (гейт Агента 4)."""
    tender = find_tender(purchase_number)
    cost_estimate = CostEstimate(total_cost=total_cost, as_of_date=date(2026, 9, 21), expert_reviewed=True)
    return estimate_profitability(profile, tender, cost_estimate, extracted)


def test_summary_for_ready_profile_reports_fit_and_pass_in_plain_language():
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")

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
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")

    summary = build_client_summary(match, completeness, package)

    assert summary.package_ready is False
    assert "не готов" in summary.package_status_explanation
    assert len(summary.missing_documents) == 1
    item = summary.missing_documents[0]
    assert item.name == "Контактное лицо"
    assert "контактное лицо" in item.what_to_do.lower()


def test_summary_flags_unresolved_experience_requirement_when_not_backed_by_profile():
    """Реальная находка 2026-09-26 (тендер №0373200032226000750, капремонт
    ЖКХ): Агент 2 сказал «ПОДХОДИТ», Агент 3 нашёл в тексте требование к
    опыту, профиль клиента (`completed_contracts=[]`) его не подтверждает —
    сводка Агента 8 должна явно показать это противоречие, а не молчать."""
    profile = make_ready_profile()
    profile.permits_experience.completed_contracts = []  # как у реального профиля Edwin
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")

    # SAMPLE_DOCUMENT_KROVLYA содержит штатно распознанное требование
    # «опыт... не менее 2 лет» (kind="experience") — этого достаточно, чтобы
    # проверить связь, не дожидаясь реального документа с 10.12.1-полем.
    assert any(r.kind == "experience" for r in extracted.participant_requirements)

    summary = build_client_summary(
        match, completeness, package, extracted_requirements=extracted, client_profile=profile
    )

    assert match.is_match  # Агент 2 по-прежнему говорит «ПОДХОДИТ»...
    assert summary.tender_fits is True
    assert len(summary.unresolved_participant_requirements) == 1  # ...но противоречие явно показано
    warning = summary.unresolved_participant_requirements[0]
    assert "ПОДХОДИТ" in warning
    assert "требуется ручная проверка" in warning.lower()
    assert "⚠" in render_summary_text(summary)


def test_summary_no_unresolved_warning_when_profile_backs_requirements():
    """Контрольный случай: тот же документ, но профиль С исполненными
    контрактами (как в `make_ready_profile()` по умолчанию) — противоречия
    нет, список должен остаться пустым."""
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")

    summary = build_client_summary(
        match, completeness, package, extracted_requirements=extracted, client_profile=profile
    )

    assert summary.unresolved_participant_requirements == []


def test_summary_without_extracted_or_profile_has_no_unresolved_warning():
    """Оба новых параметра необязательны (обратная совместимость) — без
    них список противоречий пуст, как и раньше."""
    profile = make_ready_profile()
    profile.permits_experience.completed_contracts = []
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")

    summary = build_client_summary(match, completeness, package)
    assert summary.unresolved_participant_requirements == []


def test_rejects_extracted_requirements_for_a_different_tender():
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
    other_extracted = extract_requirements("0000000000000000000", SAMPLE_DOCUMENTS["0173200001426000101"])

    with pytest.raises(ValueError, match="другой закупке"):
        build_client_summary(
            match, completeness, package, extracted_requirements=other_extracted, client_profile=profile
        )


def test_rejects_client_profile_for_a_different_client():
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
    other_profile = make_ready_profile()
    other_profile.client_id = "someone-else"

    with pytest.raises(ValueError, match="другому клиенту"):
        build_client_summary(
            match, completeness, package, extracted_requirements=extracted, client_profile=other_profile
        )


def test_summary_reports_when_tender_does_not_fit():
    profile = make_ready_profile()
    profile.region_codes = ["50"]  # клиент работает не в том регионе, что закупка
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")

    summary = build_client_summary(match, completeness, package)

    assert summary.tender_fits is False
    assert "не подходит" in summary.tender_fit_explanation
    assert "регион" in summary.tender_fit_explanation.lower()


def test_rejects_mismatched_results_from_different_tenders():
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
    _, other_completeness, other_package, _ = run_pipeline(profile, "0350200003426000202")

    try:
        build_client_summary(match, other_completeness, package)
        assert False, "ожидался ValueError"
    except ValueError as exc:
        assert "разным закупкам" in str(exc)


def test_nonstandard_penalty_risk_explanation_includes_extracted_rate():
    """Частичное улучшение пересказа риска (CLAUDE.md, Агент 8, 2026-09-25):
    когда Агент 3 извлёк конкретную ставку штрафа, объяснение для собственника
    должно содержать именно её, а не только общую формулировку категории."""
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0350200003426000202")

    summary = build_client_summary(match, completeness, package)

    penalty_risks = [r for r in summary.risks if "15%" in r.why_it_matters]
    assert len(penalty_risks) == 1
    assert "штраф" in penalty_risks[0].why_it_matters.lower()

    # Категория без извлечённого числа (расплывчатая формулировка) по-прежнему
    # получает общий шаблон по категории, не выдуманную цифру.
    ambiguous_risks = [r for r in summary.risks if "15%" not in r.why_it_matters]
    assert len(ambiguous_risks) == 1
    assert "не до конца понятно" in ambiguous_risks[0].why_it_matters.lower()


def test_render_summary_text_is_plain_and_includes_all_sections():
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
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
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
    fitting_summary = build_client_summary(match, completeness, package)
    assert CLIENT_SUMMARY_LEGAL_NOTICE in render_summary_text(fitting_summary)

    profile_not_fitting = make_ready_profile()
    profile_not_fitting.region_codes = ["50"]
    match2, completeness2, package2, extracted2 = run_pipeline(profile_not_fitting, "0173200001426000101")
    non_fitting_summary = build_client_summary(match2, completeness2, package2)
    assert CLIENT_SUMMARY_LEGAL_NOTICE in render_summary_text(non_fitting_summary)


# -- Агент 5 (оценка выгоды) подключён к сводке — раньше маржа и риски
# считались, но до собственника в сводке не доходили вообще. -----------------


def test_summary_includes_profitability_when_provided():
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
    profitability = run_profitability(profile, "0173200001426000101", extracted)

    summary = build_client_summary(match, completeness, package, profitability)

    assert summary.profitability is not None
    assert summary.profitability.margin == profitability.margin
    assert "маржа" in summary.profitability.margin_explanation.lower()
    assert summary.profitability.risk_flags == profitability.risk_flags
    assert summary.profitability.win_probability_note == profitability.win_probability_note


def test_summary_omits_profitability_section_when_not_provided():
    """Агент 5 — необязательный вход: без него сводка собирается как раньше,
    просто без пункта про выгоду, а не с ошибкой или тихим нулём."""
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")

    summary = build_client_summary(match, completeness, package)

    assert summary.profitability is None
    assert "Ожидаемая выгода" not in render_summary_text(summary)


def test_summary_handles_margin_none_honestly_not_as_missing_data():
    """Режим налогообложения «Другое» — margin=None у Агента 5 по вполне
    определённой причине (не хватка данных для расчёта налога), не как
    признак того, что Агент 5 вообще не подключён."""
    profile = make_ready_profile()
    profile.financial.tax_regime = TaxRegimeChoice.OTHER_NEEDS_CLARIFICATION
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
    profitability = run_profitability(profile, "0173200001426000101", extracted)
    assert profitability.margin is None  # подтверждаем, что сценарий тот самый

    summary = build_client_summary(match, completeness, package, profitability)

    assert summary.profitability is not None
    assert summary.profitability.margin is None
    assert "не удалось посчитать" in summary.profitability.margin_explanation
    assert any("уточнения с бухгалтером" in f for f in summary.profitability.risk_flags)


def test_rejects_profitability_for_a_different_tender():
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
    _, _, _, other_extracted = run_pipeline(profile, "0350200003426000202")
    other_profitability = run_profitability(profile, "0350200003426000202", other_extracted)

    with pytest.raises(ValueError, match="другой закупке"):
        build_client_summary(match, completeness, package, other_profitability)


def test_render_summary_text_shows_margin_and_risk_flags():
    profile = make_ready_profile()
    match, completeness, package, extracted = run_pipeline(profile, "0173200001426000101")
    profitability = run_profitability(profile, "0173200001426000101", extracted)
    summary = build_client_summary(match, completeness, package, profitability)

    text = render_summary_text(summary)

    assert "Ожидаемая выгода" in text
    assert f"{profitability.margin:,.0f}".replace(",", " ") in text
    for flag in profitability.risk_flags:
        assert flag in text
    assert profitability.win_probability_note in text

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from classifier import ConstructionClassifier, coarse_classify, final_classify, find_matching_tenders
from classifier.sample_tenders import SAMPLE_TENDERS
from document_analyst.models import ExtractedRequirements, ParticipantRequirement
from onboarding import (
    Capacity,
    ClientProfile,
    CompletedContract,
    FinancialReadiness,
    LegalInfo,
    PermitsExperience,
    TaxRegimeChoice,
)
from profitability_estimator.models import ProfitabilityEstimate


def make_moscow_contractor() -> ClientProfile:
    """Опытный подрядчик из Москвы — подходит только под московскую кровлю
    (единственную закупку из SAMPLE_TENDERS с совпадающим регионом)."""
    return ClientProfile(
        client_id="client-moscow-1",
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
        permits_experience=PermitsExperience(sro_membership=True, sro_number="СРО-С-1", years_of_experience=5),
        capacity=Capacity(staff_count=15, own_workforce_description="15 рабочих"),
        financial=FinancialReadiness(
            tax_regime=TaxRegimeChoice.USN_6_NO_VAT, avg_annual_revenue=50_000_000
        ),
    )


def find_tender(purchase_number: str):
    return next(t for t in SAMPLE_TENDERS if t.purchase_number == purchase_number)


def test_matches_construction_tender_in_own_region():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")  # капремонт кровли школы, Москва

    result = coarse_classify(profile, tender, classifier)

    assert result.is_match is True
    assert result.score == 1.0
    assert result.failed_reasons == []


def test_rejects_tender_in_different_region():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0350200003426000202")  # детский сад, Московская область

    result = coarse_classify(profile, tender, classifier)

    assert result.is_match is False
    assert any("регион" in reason.lower() for reason in result.failed_reasons)


def test_rejects_non_construction_tender():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0177200006426000505")  # разработка ПО, не стройка

    result = coarse_classify(profile, tender, classifier)

    assert result.is_match is False
    assert any("Строительство" in reason for reason in result.failed_reasons)


def test_rejects_when_sro_required_but_missing():
    profile = make_moscow_contractor()
    profile.permits_experience.sro_membership = False
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")

    result = coarse_classify(profile, tender, classifier)

    assert result.is_match is False
    assert any("СРО" in reason for reason in result.failed_reasons)


def test_rejects_when_not_enough_experience():
    profile = make_moscow_contractor()
    profile.permits_experience.years_of_experience = 1
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")  # требует 2 года опыта

    result = coarse_classify(profile, tender, classifier)

    assert result.is_match is False
    assert any("опыта" in reason.lower() for reason in result.failed_reasons)


def test_rejects_when_price_exceeds_revenue():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0350200003426000202")  # НМЦК 350 млн > выручки 50 млн

    result = coarse_classify(profile, tender, classifier)

    assert result.is_match is False
    assert any("НМЦК" in reason for reason in result.failed_reasons)


def _make_profitability(tender, profile, margin: float | None) -> ProfitabilityEstimate:
    return ProfitabilityEstimate(
        client_id=profile.client_id,
        tender_purchase_number=tender.purchase_number,
        max_price=tender.max_price,
        cost_estimate=1_000_000,
        cost_estimate_as_of=date(2026, 9, 18),
        security_cost=0.0,
        taxes=None if margin is None else 480_000.0,
        margin=margin,
    )


def test_uses_real_margin_from_agent5_when_provided_and_ignores_revenue_heuristic():
    """Клиент, у которого выручка НЕ проходит грубую эвристику Агента 2, но
    Агент 5 посчитал положительную реальную маржу по этой закупке — должен
    пройти по financial_capacity благодаря марже, не вопреки эвристике."""
    profile = make_moscow_contractor()
    profile.financial.avg_annual_revenue = 1  # эвристика "выручка >= НМЦК" провалилась бы
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    profitability = _make_profitability(tender, profile, margin=2_000_000.0)

    result = coarse_classify(profile, tender, classifier, profitability=profitability)

    assert result.is_match is True
    financial = next(c for c in result.criteria if c.name == "financial_capacity")
    assert "маржа" in financial.message.lower()


def test_rejects_when_real_margin_from_agent5_is_not_positive():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    profitability = _make_profitability(tender, profile, margin=-50_000.0)

    result = coarse_classify(profile, tender, classifier, profitability=profitability)

    assert result.is_match is False
    financial = next(c for c in result.criteria if c.name == "financial_capacity")
    assert not financial.passed
    assert "невыгодна" in financial.message.lower()


def test_rejects_when_agent5_margin_is_none():
    """Налоговый режим 'другое' -> Агент 5 не может посчитать маржу
    (`taxes`/`margin` оба `None`) — financial_capacity не может считаться
    пройденным без реальной цифры."""
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    profitability = _make_profitability(tender, profile, margin=None)

    result = coarse_classify(profile, tender, classifier, profitability=profitability)

    assert result.is_match is False
    financial = next(c for c in result.criteria if c.name == "financial_capacity")
    assert not financial.passed


def test_financial_criterion_message_marks_heuristic_as_preliminary_without_profitability():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")

    result = coarse_classify(profile, tender, classifier)

    financial = next(c for c in result.criteria if c.name == "financial_capacity")
    assert financial.passed is True
    assert "предварительная" in financial.message.lower()


def test_raises_when_profitability_is_for_a_different_tender():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    other_tender = find_tender("0350200003426000202")
    mismatched_profitability = _make_profitability(other_tender, profile, margin=1_000_000.0)

    with pytest.raises(ValueError, match="закупке"):
        coarse_classify(profile, tender, classifier, profitability=mismatched_profitability)


def test_raises_when_profitability_is_for_a_different_client():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    profitability = _make_profitability(tender, profile, margin=1_000_000.0)
    profitability.client_id = "some-other-client"

    with pytest.raises(ValueError, match="клиенту"):
        coarse_classify(profile, tender, classifier, profitability=profitability)


def test_find_matching_tenders_sorts_best_first():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()

    results = find_matching_tenders(profile, SAMPLE_TENDERS, classifier)

    assert len(results) == len(SAMPLE_TENDERS)
    assert results[0].tender.purchase_number == "0173200001426000101"
    assert results[0].is_match is True
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# -- final_classify() — второй проход, после Агента 3 (CLAUDE.md, п.11) -----


def _make_extracted(purchase_number: str, requirements: list[ParticipantRequirement]) -> ExtractedRequirements:
    return ExtractedRequirements(tender_purchase_number=purchase_number, participant_requirements=requirements)


def test_final_classify_matches_coarse_when_no_participant_requirements_found():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    extracted = _make_extracted(tender.purchase_number, [])

    coarse = coarse_classify(profile, tender, classifier)
    final = final_classify(profile, tender, classifier, extracted)

    assert final.is_match == coarse.is_match is True
    criterion = next(c for c in final.criteria if c.name == "participant_requirements_from_documents")
    assert criterion.passed is True


def test_final_classify_confirms_coarse_match_when_profile_backs_requirements():
    profile = make_moscow_contractor()  # sro_membership=True, completed_contracts не пуст ниже
    profile.permits_experience.completed_contracts = [
        CompletedContract(object_name="Капремонт кровли", customer="ДепОбр", amount=3_000_000, year=2024)
    ]
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    extracted = _make_extracted(
        tender.purchase_number,
        [
            ParticipantRequirement(description="Опыт не менее 2 лет", kind="experience", raw_text="опыт 2 года"),
            ParticipantRequirement(description="Членство в СРО", kind="sro", raw_text="СРО обязательно"),
        ],
    )

    final = final_classify(profile, tender, classifier, extracted)

    assert final.is_match is True


def test_final_classify_overturns_coarse_match_to_not_fit_on_confirmed_experience_finding():
    """Реальный случай 2026-09-26 (капремонт ЖКХ, №0373200032226000750):
    coarse_classify() говорит «ПОДХОДИТ» (requires_sro/min_experience_years —
    честная заглушка False/0 у Tender из извещения), но Агент 3 нашёл в
    документации требование к опыту, которое профиль не подтверждает —
    final_classify() должен окончательно перевернуть вердикт, не оставить
    молчаливое «ПОДХОДИТ» первого прохода."""
    profile = make_moscow_contractor()
    profile.permits_experience.completed_contracts = []
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    coarse = coarse_classify(profile, tender, classifier)
    assert coarse.is_match is True  # предпосылка: первый проход пропускает

    extracted = _make_extracted(
        tender.purchase_number,
        [
            ParticipantRequirement(
                description="Опыт исполнения контракта на сумму не менее 20% НМЦК",
                kind="experience",
                raw_text="опыт... цена выполненных работ не менее 20 процентов НМЦК",
            )
        ],
    )

    final = final_classify(profile, tender, classifier, extracted)

    assert final.is_match is False
    criterion = next(c for c in final.criteria if c.name == "participant_requirements_from_documents")
    assert criterion.passed is False
    assert "НЕ ПОДХОДИТ" in criterion.message
    assert "требует ручной проверки" not in criterion.message.lower()


def test_final_classify_marks_unclear_finding_as_requires_review_not_definite_rejection():
    profile = make_moscow_contractor()
    profile.permits_experience.completed_contracts = []
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    extracted = _make_extracted(
        tender.purchase_number,
        [
            ParticipantRequirement(
                description="Похоже на требование к опыту (не распознано уверенно)",
                kind="unclear",
                raw_text="опыт... где-то рядом 20 процентов",
            )
        ],
    )

    final = final_classify(profile, tender, classifier, extracted)

    assert final.is_match is False
    criterion = next(c for c in final.criteria if c.name == "participant_requirements_from_documents")
    assert criterion.passed is False
    assert "ТРЕБУЕТ РУЧНОЙ ПРОВЕРКИ" in criterion.message
    assert "НЕ ПОДХОДИТ" not in criterion.message


def test_final_classify_stays_not_a_match_when_coarse_already_failed():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0350200003426000202")  # другой регион — coarse уже FAIL
    extracted = _make_extracted(tender.purchase_number, [])

    final = final_classify(profile, tender, classifier, extracted)

    assert final.is_match is False


def test_final_classify_raises_when_extracted_requirements_are_for_a_different_tender():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    extracted = _make_extracted("0000000000000000000", [])

    with pytest.raises(ValueError, match="закупке"):
        final_classify(profile, tender, classifier, extracted)


def test_final_classify_uses_real_margin_from_agent5_like_coarse_does():
    profile = make_moscow_contractor()
    profile.financial.avg_annual_revenue = 1
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    profitability = _make_profitability(tender, profile, margin=2_000_000.0)
    extracted = _make_extracted(tender.purchase_number, [])

    final = final_classify(profile, tender, classifier, extracted, profitability=profitability)

    assert final.is_match is True
    financial = next(c for c in final.criteria if c.name == "financial_capacity")
    assert "маржа" in financial.message.lower()

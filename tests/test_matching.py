import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from classifier import ConstructionClassifier, find_matching_tenders, match_profile_to_tender
from classifier.sample_tenders import SAMPLE_TENDERS
from onboarding import (
    Capacity,
    ClientProfile,
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

    result = match_profile_to_tender(profile, tender, classifier)

    assert result.is_match is True
    assert result.score == 1.0
    assert result.failed_reasons == []


def test_rejects_tender_in_different_region():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0350200003426000202")  # детский сад, Московская область

    result = match_profile_to_tender(profile, tender, classifier)

    assert result.is_match is False
    assert any("регион" in reason.lower() for reason in result.failed_reasons)


def test_rejects_non_construction_tender():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0177200006426000505")  # разработка ПО, не стройка

    result = match_profile_to_tender(profile, tender, classifier)

    assert result.is_match is False
    assert any("Строительство" in reason for reason in result.failed_reasons)


def test_rejects_when_sro_required_but_missing():
    profile = make_moscow_contractor()
    profile.permits_experience.sro_membership = False
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")

    result = match_profile_to_tender(profile, tender, classifier)

    assert result.is_match is False
    assert any("СРО" in reason for reason in result.failed_reasons)


def test_rejects_when_not_enough_experience():
    profile = make_moscow_contractor()
    profile.permits_experience.years_of_experience = 1
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")  # требует 2 года опыта

    result = match_profile_to_tender(profile, tender, classifier)

    assert result.is_match is False
    assert any("опыта" in reason.lower() for reason in result.failed_reasons)


def test_rejects_when_price_exceeds_revenue():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0350200003426000202")  # НМЦК 350 млн > выручки 50 млн

    result = match_profile_to_tender(profile, tender, classifier)

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

    result = match_profile_to_tender(profile, tender, classifier, profitability=profitability)

    assert result.is_match is True
    financial = next(c for c in result.criteria if c.name == "financial_capacity")
    assert "маржа" in financial.message.lower()


def test_rejects_when_real_margin_from_agent5_is_not_positive():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    profitability = _make_profitability(tender, profile, margin=-50_000.0)

    result = match_profile_to_tender(profile, tender, classifier, profitability=profitability)

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

    result = match_profile_to_tender(profile, tender, classifier, profitability=profitability)

    assert result.is_match is False
    financial = next(c for c in result.criteria if c.name == "financial_capacity")
    assert not financial.passed


def test_financial_criterion_message_marks_heuristic_as_preliminary_without_profitability():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")

    result = match_profile_to_tender(profile, tender, classifier)

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
        match_profile_to_tender(profile, tender, classifier, profitability=mismatched_profitability)


def test_raises_when_profitability_is_for_a_different_client():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()
    tender = find_tender("0173200001426000101")
    profitability = _make_profitability(tender, profile, margin=1_000_000.0)
    profitability.client_id = "some-other-client"

    with pytest.raises(ValueError, match="клиенту"):
        match_profile_to_tender(profile, tender, classifier, profitability=profitability)


def test_find_matching_tenders_sorts_best_first():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()

    results = find_matching_tenders(profile, SAMPLE_TENDERS, classifier)

    assert len(results) == len(SAMPLE_TENDERS)
    assert results[0].tender.purchase_number == "0173200001426000101"
    assert results[0].is_match is True
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)

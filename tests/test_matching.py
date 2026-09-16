import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classifier import ConstructionClassifier, find_matching_tenders, match_profile_to_tender
from classifier.sample_tenders import SAMPLE_TENDERS
from onboarding import Capacity, ClientProfile, FinancialReadiness, LegalInfo, PermitsExperience


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
        financial=FinancialReadiness(tax_system="УСН", avg_annual_revenue=50_000_000),
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


def test_find_matching_tenders_sorts_best_first():
    profile = make_moscow_contractor()
    classifier = ConstructionClassifier()

    results = find_matching_tenders(profile, SAMPLE_TENDERS, classifier)

    assert len(results) == len(SAMPLE_TENDERS)
    assert results[0].tender.purchase_number == "0173200001426000101"
    assert results[0].is_match is True
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)

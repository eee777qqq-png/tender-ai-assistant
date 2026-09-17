import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from onboarding import (
    Capacity,
    ClientProfile,
    CompletedContract,
    FinancialReadiness,
    LegalInfo,
    PermitsExperience,
    ProfileStatus,
    TaxRegimeChoice,
    validate_profile,
)


def make_valid_profile() -> ClientProfile:
    return ClientProfile(
        client_id="client-1",
        region_code="77",
        legal=LegalInfo(
            org_name="ООО Стройсервис",
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
                    object_name="Капремонт школы №5",
                    customer="Департамент образования",
                    amount=5_000_000,
                    year=2024,
                )
            ],
            years_of_experience=5,
        ),
        capacity=Capacity(
            staff_count=15,
            equipment=["экскаватор", "бетономешалка"],
            own_workforce_description="15 штатных рабочих, 3 бригады",
        ),
        financial=FinancialReadiness(
            tax_regime=TaxRegimeChoice.USN_6_NO_VAT,
            avg_annual_revenue=50_000_000,
            working_capital=3_000_000,
            bank_guarantee_available=True,
        ),
    )


def test_incomplete_profile_stays_draft():
    profile = ClientProfile(client_id="client-2")
    issues = validate_profile(profile)

    assert profile.status == ProfileStatus.DRAFT
    assert issues == []
    assert not profile.is_ready_for_agent_6()


def test_complete_and_valid_profile_becomes_filled():
    profile = make_valid_profile()
    issues = validate_profile(profile)

    assert issues == []
    assert profile.status == ProfileStatus.FILLED
    assert not profile.is_ready_for_agent_6()  # ещё не проверен экспертом


def test_complete_but_invalid_profile_stays_draft():
    profile = make_valid_profile()
    profile.legal.inn = "123"  # некорректный ИНН

    issues = validate_profile(profile)

    assert profile.status == ProfileStatus.DRAFT
    assert any(issue.field == "inn" for issue in issues)
    assert not profile.is_ready_for_agent_6()


def test_sro_membership_without_number_is_flagged():
    profile = make_valid_profile()
    profile.permits_experience.sro_number = ""

    issues = validate_profile(profile)

    assert any(issue.field == "sro_number" for issue in issues)
    assert profile.status == ProfileStatus.DRAFT


def test_full_pipeline_draft_to_ready():
    profile = make_valid_profile()
    validate_profile(profile)
    assert profile.status == ProfileStatus.FILLED

    profile.submit_expert_review(reviewer="Edwin", approved=True, notes="всё ок")
    assert profile.status == ProfileStatus.EXPERT_REVIEWED
    assert not profile.is_ready_for_agent_6()

    profile.mark_ready()
    assert profile.status == ProfileStatus.READY
    assert profile.is_ready_for_agent_6()

    assert len(profile.expert_reviews) == 1
    assert profile.expert_reviews[0].reviewer == "Edwin"
    assert profile.expert_reviews[0].approved is True


def test_expert_rejection_sends_profile_back_to_draft():
    profile = make_valid_profile()
    validate_profile(profile)

    profile.submit_expert_review(reviewer="Edwin", approved=False, notes="сомнительный ИНН")

    assert profile.status == ProfileStatus.DRAFT
    assert not profile.is_ready_for_agent_6()
    assert profile.expert_reviews[0].approved is False


def test_cannot_review_a_draft_profile():
    profile = ClientProfile(client_id="client-3")

    try:
        profile.submit_expert_review(reviewer="Edwin", approved=True)
        assert False, "ожидался ValueError"
    except ValueError:
        pass


def test_cannot_mark_ready_without_expert_review():
    profile = make_valid_profile()
    validate_profile(profile)

    try:
        profile.mark_ready()
        assert False, "ожидался ValueError"
    except ValueError:
        pass

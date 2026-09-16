import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classifier.sample_tenders import SAMPLE_TENDERS
from completeness_check import CompletenessStatus, check_completeness
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


def make_ready_profile() -> ClientProfile:
    profile = ClientProfile(
        client_id="client-1",
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


def find_tender(purchase_number: str):
    return next(t for t in SAMPLE_TENDERS if t.purchase_number == purchase_number)


def test_pass_when_all_required_fields_ready():
    profile = make_ready_profile()
    tender = find_tender("0173200001426000101")  # капремонт кровли, требует СРО и опыт
    package = assemble_document_package(profile, tender)

    result = check_completeness(package, tender)

    assert result.status == CompletenessStatus.PASS_
    assert result.is_pass()
    assert result.missing_required_fields == []
    assert result.client_id == "client-1"
    assert result.tender_purchase_number == tender.purchase_number


def test_pass_lists_not_applicable_fields_as_optional_missing_only():
    profile = make_ready_profile()
    profile.permits_experience.sro_number = ""  # у клиента нет номера СРО
    tender = find_tender("0123200004426000303")  # штукатурные работы, СРО не требуется
    package = assemble_document_package(profile, tender)

    result = check_completeness(package, tender)

    assert result.status == CompletenessStatus.PASS_
    assert result.missing_required_fields == []
    assert "Членство в СРО (номер)" in result.missing_optional_fields


def test_fail_when_required_field_is_missing():
    """Намеренный тестовый случай: у клиента не указано контактное лицо, а
    оно обязательно для подачи — комплектность должна провалиться с точным
    указанием, какого поля не хватает, а не общей фразой."""
    profile = make_ready_profile()
    profile.legal.contact_person = ""
    tender = find_tender("0173200001426000101")
    package = assemble_document_package(profile, tender)

    result = check_completeness(package, tender)

    assert result.status == CompletenessStatus.FAIL
    assert not result.is_pass()
    assert result.missing_required_fields == ["Контактное лицо"]


def test_fail_lists_all_missing_required_fields_not_just_first():
    profile = make_ready_profile()
    profile.legal.contact_person = ""
    profile.legal.phone = ""
    tender = find_tender("0350200003426000202")  # детский сад, требует СРО и опыт
    package = assemble_document_package(profile, tender)

    result = check_completeness(package, tender)

    assert result.status == CompletenessStatus.FAIL
    assert set(result.missing_required_fields) == {"Контактное лицо", "Телефон"}


def test_rejects_package_for_a_different_tender():
    profile = make_ready_profile()
    tender = find_tender("0173200001426000101")
    other_tender = find_tender("0350200003426000202")
    package = assemble_document_package(profile, tender)

    try:
        check_completeness(package, other_tender)
        assert False, "ожидался ValueError"
    except ValueError as exc:
        assert tender.purchase_number in str(exc)
        assert other_tender.purchase_number in str(exc)

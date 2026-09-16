import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classifier.sample_tenders import SAMPLE_TENDERS
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
    """Профиль, доведённый до статуса READY по полному пайплайну Агента 11 —
    без этого assemble_document_package() обязан отказать."""
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


def test_rejects_profile_that_is_not_ready():
    profile = ClientProfile(client_id="client-2")  # остаётся DRAFT
    tender = find_tender("0173200001426000101")

    try:
        assemble_document_package(profile, tender)
        assert False, "ожидался ValueError"
    except ValueError as exc:
        assert "READY" in str(exc)
        assert "DRAFT" in str(exc)


def test_assembles_package_skeleton_for_ready_profile():
    profile = make_ready_profile()
    tender = find_tender("0173200001426000101")  # капремонт кровли, требует СРО и опыт

    package = assemble_document_package(profile, tender)

    assert package.client_id == "client-1"
    assert package.tender_purchase_number == tender.purchase_number
    assert package.is_complete()
    assert package.missing_fields() == []

    by_name = {f.name: f for f in package.fields}
    assert by_name["ИНН"].value == "7701234567"
    assert by_name["ИНН"].source == "legal.inn"
    assert by_name["ИНН"].status == "ready"
    assert by_name["Членство в СРО (номер)"].status == "ready"
    assert by_name["Номер закупки"].source == "tender.purchase_number"


def test_sro_field_not_applicable_when_tender_does_not_require_it():
    profile = make_ready_profile()
    profile.permits_experience.sro_number = ""  # у клиента нет номера СРО
    tender = find_tender("0123200004426000303")  # штукатурные работы, СРО не требуется

    package = assemble_document_package(profile, tender)

    by_name = {f.name: f for f in package.fields}
    assert by_name["Членство в СРО (номер)"].status == "not_applicable"
    assert package.is_complete()  # not_applicable не считается пропущенным полем


def test_missing_required_field_is_flagged():
    profile = make_ready_profile()
    profile.legal.contact_person = ""
    tender = find_tender("0173200001426000101")

    # profile уже READY (статус не пересчитывается при прямом изменении поля
    # после mark_ready — это ожидаемо: правки после READY должны идти через
    # новый цикл проверки, тут просто проверяем, что сборщик увидит дыру)
    package = assemble_document_package(profile, tender)

    by_name = {f.name: f for f in package.fields}
    assert by_name["Контактное лицо"].status == "missing"
    assert not package.is_complete()
    assert any(f.name == "Контактное лицо" for f in package.missing_fields())

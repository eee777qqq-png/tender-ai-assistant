import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from classifier.sample_tenders import SAMPLE_TENDERS
from document_analyst import extract_requirements
from document_analyst.sample_documents import SAMPLE_DOCUMENTS
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
from profitability_estimator import CostEstimate, WIN_PROBABILITY_NOTE, estimate_profitability

TENDER_PURCHASE_NUMBER = "0173200001426000101"  # капремонт кровли школы №5, НМЦК 8 000 000


def find_tender(purchase_number: str):
    return next(t for t in SAMPLE_TENDERS if t.purchase_number == purchase_number)


def make_profile(tax_regime=TaxRegimeChoice.USN_6_NO_VAT, bank_guarantee_available=True) -> ClientProfile:
    """Тот же тестовый профиль e2e-client-1, что и в
    tests/test_end_to_end_pipeline.py, с настраиваемым налоговым режимом и
    наличием банковской гарантии — под конкретный тестовый сценарий."""
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
            tax_regime=tax_regime,
            avg_annual_revenue=50_000_000,
            working_capital=3_000_000,
            bank_guarantee_available=bank_guarantee_available,
        ),
    )
    validate_profile(profile)
    profile.submit_expert_review(reviewer="Edwin", approved=True)
    profile.mark_ready()
    return profile


def make_cost_estimate(total_cost: float = 6_000_000) -> CostEstimate:
    """Себестоимость передана вручную — Агент 4 (сметчик) ещё не реализован,
    см. CostEstimate.source_note."""
    return CostEstimate(total_cost=total_cost, as_of_date=date(2026, 7, 1))


def reviewed_requirements():
    extracted = extract_requirements(TENDER_PURCHASE_NUMBER, SAMPLE_DOCUMENTS[TENDER_PURCHASE_NUMBER])
    extracted.mark_expert_reviewed(reviewer="Edwin")
    return extracted


def test_margin_with_bank_guarantee_and_usn_6_no_vat():
    profile = make_profile()
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted)

    assert result.max_price == 8_000_000
    assert result.cost_estimate == 6_000_000
    assert result.cost_estimate_as_of == date(2026, 7, 1)
    # обеспечение: 10% от НМЦК = 800 000, 80 дней (01.09-20.11.2026) под 3% годовых
    assert result.security_cost == pytest.approx(5260.27, abs=0.5)
    # УСН 6% без НДС — налог с полной НМЦК, без учёта себестоимости
    assert result.taxes == pytest.approx(480_000.0, abs=0.5)
    assert result.margin == pytest.approx(1_514_739.73, abs=0.5)

    assert result.win_probability_note == WIN_PROBABILITY_NOTE
    assert "недостаточно данных" in result.win_probability_note
    assert "Агента 9" in result.win_probability_note


def test_risk_flags_reflect_participant_requirements_and_hidden_risks():
    profile = make_profile()
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted)

    # Документ содержит 2 требования к участнику (СРО + опыт от 2 лет).
    assert any("2 требования" in f and "мало конкурентов" in f for f in result.risk_flags)
    # Документ содержит риск категории uncapped_liability (безлимитная неустойка).
    assert any("Повышенный риск при срыве исполнения" in f for f in result.risk_flags)
    assert any("uncapped_liability" in f for f in result.risk_flags)
    # Никаких числовых вероятностей в флагах не должно быть — только текст.
    assert not any(f.strip().replace(".", "").replace("%", "").isdigit() for f in result.risk_flags)


def test_no_bank_guarantee_means_zero_security_cost_without_a_missing_data_flag():
    profile = make_profile(bank_guarantee_available=False)
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted)

    assert result.security_cost == 0.0
    # "нет гарантии" — сознательный факт профиля, а не нехватка данных,
    # поэтому предупреждения о нехватке данных быть не должно.
    assert not any("Не удалось точно посчитать стоимость обеспечения" in f for f in result.risk_flags)


def test_missing_agent_3_data_flags_security_cost_as_unknown_not_free():
    profile = make_profile()  # bank_guarantee_available=True
    tender = find_tender(TENDER_PURCHASE_NUMBER)

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted_requirements=None)

    assert result.security_cost == 0.0
    assert any("Не удалось точно посчитать стоимость обеспечения" in f for f in result.risk_flags)
    assert any("Строгость требований к участнику не оценена" in f for f in result.risk_flags)
    # Налоги считаются независимо от Агента 3 — из выбора клиента и НМЦК.
    assert result.taxes == pytest.approx(480_000.0, abs=0.5)


def test_other_needs_clarification_regime_blocks_margin_calculation():
    profile = make_profile(tax_regime=TaxRegimeChoice.OTHER_NEEDS_CLARIFICATION)
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted)

    assert result.taxes is None
    assert result.margin is None
    assert any("требует уточнения с бухгалтером" in f for f in result.risk_flags)


def test_usn_15_with_vat_taxes_profit_not_revenue():
    """УСН 15% — налоговая база "доходы минус расходы", в отличие от УСН 6%
    (налог с полной выручки) — формулы не должны совпадать."""
    profile = make_profile(tax_regime=TaxRegimeChoice.USN_15_VAT_5)
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted)

    assert result.taxes == pytest.approx(699_210.96, abs=0.5)
    assert result.margin == pytest.approx(1_295_528.77, abs=0.5)


def test_osn_vat_22_can_produce_a_negative_margin_when_costs_are_high():
    """Инструмент обязан честно показать убыточный сценарий, а не скрыть его —
    в этом сценарии ОСН+НДС 22% при тех же вводных даёт отрицательную маржу."""
    profile = make_profile(tax_regime=TaxRegimeChoice.OSN_VAT_22)
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted)

    assert result.margin == pytest.approx(-164_208.22, abs=0.5)


def test_rejects_agent_3_output_for_a_different_tender():
    profile = make_profile()
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    other_purchase_number = "0350200003426000202"
    extracted = extract_requirements(other_purchase_number, SAMPLE_DOCUMENTS[other_purchase_number])
    extracted.mark_expert_reviewed(reviewer="Edwin")

    with pytest.raises(ValueError, match=other_purchase_number):
        estimate_profitability(profile, tender, make_cost_estimate(), extracted)


def test_rejects_unreviewed_agent_3_output():
    profile = make_profile()
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = extract_requirements(TENDER_PURCHASE_NUMBER, SAMPLE_DOCUMENTS[TENDER_PURCHASE_NUMBER])
    assert not extracted.expert_reviewed

    with pytest.raises(ValueError, match="проверен экспертом"):
        estimate_profitability(profile, tender, make_cost_estimate(), extracted)

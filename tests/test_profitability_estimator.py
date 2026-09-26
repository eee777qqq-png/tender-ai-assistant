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


def make_cost_estimate(total_cost: float = 6_000_000, **overrides) -> CostEstimate:
    """Себестоимость в этих тестах по-прежнему собрана вручную (тесты этого
    файла — про формулу налогов/маржи, не про Агент 4 — реальная сборка из
    Агента 4 проверяется в `tests/test_end_to_end_pipeline.py`), но теперь
    обязана явно пройти тот же гейт `expert_reviewed`, что и настоящий
    результат `smeta_estimator.build_cost_estimate()`."""
    return CostEstimate(
        total_cost=total_cost, as_of_date=date(2026, 7, 1), expert_reviewed=True, **overrides
    )


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


def test_osn_ooo_vat_22_uses_25_percent_profit_tax_rate():
    """Ставка налога на прибыль ООО на ОСН — 25% (ст. 284 НК РФ, действует с
    01.01.2025, было ошибочно зафиксировано 20% — см. CLAUDE.md, «Известные
    пробелы», п.6). Инструмент обязан честно показать убыточный сценарий, а
    не скрыть его — в этом сценарии ОСН+НДС 22% при тех же вводных даёт
    отрицательную маржу."""
    profile = make_profile(tax_regime=TaxRegimeChoice.OSN_OOO_VAT_22)
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted)

    # НДС 22% от НМЦК (1 760 000) + налог на прибыль 25% от прибыли (1 994 739.73 * 0.25)
    assert result.taxes == pytest.approx(2_258_684.93, abs=0.5)
    assert result.margin == pytest.approx(-263_945.21, abs=0.5)


def test_osn_ip_vat_22_uses_progressive_ndfl_not_flat_rate():
    """ИП на ОСН платит НДФЛ, не налог на прибыль — другой налог с того же
    дохода, чем у ООО (см. CLAUDE.md, «Известные пробелы», п.6). При той же
    прибыли (1 994 739.73, целиком в первой ступени шкалы до 2,4 млн) ставка
    13% даёт меньший налог и другую маржу, чем у ООО (25%)."""
    profile = make_profile(tax_regime=TaxRegimeChoice.OSN_IP_VAT_22)
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()

    result = estimate_profitability(profile, tender, make_cost_estimate(), extracted)

    # НДС 22% от НМЦК (1 760 000) + НДФЛ 13% от прибыли (1 994 739.73 * 0.13,
    # вся прибыль ниже порога 2.4 млн — одна ступень шкалы)
    assert result.taxes == pytest.approx(2_019_316.16, abs=0.5)
    assert result.margin == pytest.approx(-24_576.44, abs=0.5)
    assert any(
        "совокупного годового дохода" in f and "НДФЛ" in f for f in result.risk_flags
    )


def test_osn_ip_progressive_ndfl_crosses_multiple_brackets_on_large_profit():
    """Отдельная проверка самой прогрессивности шкалы (13/15/18/20/22%) — не
    только что применяется одна ставка, а что ступени действительно
    складываются по порогам ст. 224 НК РФ, а не берётся единая ставка от
    верхней границы дохода."""
    profile = make_profile(tax_regime=TaxRegimeChoice.OSN_IP_VAT_22)
    tender = find_tender("0350200003426000202")  # детский сад, НМЦК 350 000 000
    extracted = extract_requirements(
        "0350200003426000202", SAMPLE_DOCUMENTS["0350200003426000202"]
    )
    extracted.mark_expert_reviewed(reviewer="Edwin")

    result = estimate_profitability(
        profile, tender, make_cost_estimate(total_cost=300_000_000), extracted
    )

    # Прибыль (48 757 260.27) пересекает 4 из 5 ступеней шкалы:
    # 2.4М*13% + 2.6М*15% + 15М*18% + 28.757...М*20% = 9 153 452.05 НДФЛ
    # + НДС 22% от НМЦК (77 000 000) = 86 153 452.05
    assert result.taxes == pytest.approx(86_153_452.05, abs=1.0)
    assert result.margin == pytest.approx(-37_396_191.78, abs=1.0)


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


def test_rejects_unreviewed_agent_4_cost_estimate():
    """Тот же паттерн блокировки, что у Агента 3 -> Агент 6/5: непроверенная
    экспертом себестоимость Агента 4 не должна тихо использоваться."""
    profile = make_profile()
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()
    unreviewed_cost_estimate = CostEstimate(total_cost=6_000_000, as_of_date=date(2026, 7, 1))
    assert not unreviewed_cost_estimate.expert_reviewed

    with pytest.raises(ValueError, match="проверена экспертом"):
        estimate_profitability(profile, tender, unreviewed_cost_estimate, extracted)


def test_incomplete_cost_estimate_flags_risk_but_still_computes_margin():
    """Себестоимость, где часть позиций сметы не оценена (например, эксперт
    не выбрал ни одного кандидата) — не блокирует расчёт, но честно
    предупреждает, что маржа может быть завышена."""
    profile = make_profile()
    tender = find_tender(TENDER_PURCHASE_NUMBER)
    extracted = reviewed_requirements()
    cost_estimate = make_cost_estimate(is_complete=False, source_note="не оценено вовсе: 1 позиции(й)")

    result = estimate_profitability(profile, tender, cost_estimate, extracted)

    assert any("неполная" in f and "завышена" in f for f in result.risk_flags)

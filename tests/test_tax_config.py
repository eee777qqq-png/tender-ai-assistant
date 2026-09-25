import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from onboarding import FinancialReadiness, TaxRegimeChoice
from onboarding.tax_config import (
    TAX_REGIME_OPTIONS,
    UNVERIFIED_NOTES,
    is_valid_choice,
    label_for,
)


def test_exactly_nine_fixed_regime_combinations():
    """Было 8 — 2026-09-25 прежний единый «ОСН + НДС 22%» разделён на ИП и
    ООО (разные налоги с дохода, см. CLAUDE.md, «Известные пробелы», п.6)."""
    assert len(TAX_REGIME_OPTIONS) == 9
    assert len(TaxRegimeChoice) == 9

    labels = {opt.label for opt in TAX_REGIME_OPTIONS}
    assert labels == {
        "УСН 6% без НДС",
        "УСН 6% + НДС 5%",
        "УСН 6% + НДС 7%",
        "УСН 15% без НДС",
        "УСН 15% + НДС 5%",
        "УСН 15% + НДС 7%",
        "ИП на ОСН + НДС 22% (с вычетом)",
        "ООО на ОСН + НДС 22% (с вычетом)",
        "Другое / требует уточнения с бухгалтером",
    }


def test_label_for_every_choice_is_defined():
    for choice in TaxRegimeChoice:
        label = label_for(choice)
        assert label  # ни для одного варианта не должно быть пустой подписи


def test_is_valid_choice_accepts_all_nine_and_rejects_garbage():
    for choice in TaxRegimeChoice:
        assert is_valid_choice(choice.value)
    assert not is_valid_choice("НДС 20%")  # не входит в фиксированный список
    assert not is_valid_choice("")


def test_unverified_notes_flag_the_2027_vat_rumor_without_changing_options():
    """Непроверенная информация — это заметка на будущее, а не действующее
    правило: список из 9 вариантов не должен меняться сам по себе из-за неё."""
    assert any("2027" in note for note in UNVERIFIED_NOTES)
    assert any("20%" in note for note in UNVERIFIED_NOTES)
    assert len(TAX_REGIME_OPTIONS) == 9


def test_financial_readiness_requires_a_tax_regime_choice_to_be_filled():
    financial = FinancialReadiness(avg_annual_revenue=50_000_000)
    assert not financial.is_filled()  # tax_regime ещё не выбран

    financial.tax_regime = TaxRegimeChoice.OSN_OOO_VAT_22
    assert financial.is_filled()


def test_choosing_other_still_counts_as_filled():
    """«Другое / требует уточнения с бухгалтером» — тоже осознанный ответ
    клиента, а не пропуск поля; профиль не должен зависать в DRAFT из-за
    него одного."""
    financial = FinancialReadiness(
        tax_regime=TaxRegimeChoice.OTHER_NEEDS_CLARIFICATION, avg_annual_revenue=1
    )
    assert financial.is_filled()


def test_revenue_still_required_independently_of_tax_regime():
    """avg_annual_revenue служит отдельной цели (финансовая состоятельность
    для Агента 2), не связана с выбором налогового режима — обе части блока
    обязательны независимо друг от друга."""
    financial = FinancialReadiness(tax_regime=TaxRegimeChoice.USN_6_NO_VAT, avg_annual_revenue=0)
    assert not financial.is_filled()

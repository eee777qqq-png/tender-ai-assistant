"""Оценка выгоды по одной паре клиент+закупка (Агент 5).

`estimate_profitability()` — единственная точка входа.

**Это каркас с грубыми приближениями, а не проверенная бухгалтером формула.**
Таблица «налоговый режим клиента → сумма налога в рублях»
(`_TAX_RULES` ниже) — рабочий вариант, требующий подтверждения бухгалтером
клиента или владельца продукта, по аналогии с тем, как уже помечена как
временная заглушка финансовая формула Агента 2 (`classifier.matching`).
В частности: НДС везде считается от полной НМЦК без учёта вычета входного
НДС (для ОСН это неточно — вычитать не на чем, входных данных о затратах с
НДС нет), и не учтены страховые взносы/прочие обязательные платежи.
"""

from __future__ import annotations

from classifier.tender import Tender
from document_analyst.models import ExtractedRequirements, RiskCategory
from onboarding.models import ClientProfile
from onboarding.tax_config import TaxRegimeChoice

from .models import CostEstimate, ProfitabilityEstimate

# 3% годовых от суммы обеспечения контракта, пересчитанная на срок
# исполнения — условная ставка, согласована как часть ТЗ на Агента 5.
_BANK_GUARANTEE_ANNUAL_RATE = 0.03
_DAYS_PER_YEAR = 365

# (налоговая база, ставка налога с дохода/прибыли, ставка НДС) для каждой
# комбинации. База "revenue" — НМЦК целиком (УСН "доходы"); база "profit" —
# НМЦК минус себестоимость минус обеспечение (УСН "доходы минус расходы" и
# ОСН, где ставка налога на прибыль ОСН зафиксирована на 20% — комбинация
# `OSN_VAT_22` не хранит эту ставку отдельно, потому что клиенту в форме
# показывается только ставка НДС, а налог на прибыль ОСН стандартный).
# НДС в обоих случаях считается от НМЦК (революционный доход), независимо от
# базы налога на доход/прибыль.
_TAX_RULES: dict[TaxRegimeChoice, tuple[str, float, float]] = {
    TaxRegimeChoice.USN_6_NO_VAT: ("revenue", 0.06, 0.0),
    TaxRegimeChoice.USN_6_VAT_5: ("revenue", 0.06, 0.05),
    TaxRegimeChoice.USN_6_VAT_7: ("revenue", 0.06, 0.07),
    TaxRegimeChoice.USN_15_NO_VAT: ("profit", 0.15, 0.0),
    TaxRegimeChoice.USN_15_VAT_5: ("profit", 0.15, 0.05),
    TaxRegimeChoice.USN_15_VAT_7: ("profit", 0.15, 0.07),
    TaxRegimeChoice.OSN_VAT_22: ("profit", 0.20, 0.22),
}


def estimate_profitability(
    profile: ClientProfile,
    tender: Tender,
    cost_estimate: CostEstimate,
    extracted_requirements: ExtractedRequirements | None = None,
) -> ProfitabilityEstimate:
    if extracted_requirements is not None:
        if extracted_requirements.tender_purchase_number != tender.purchase_number:
            raise ValueError(
                "Извлечённые Агентом 3 требования относятся к закупке "
                f"{extracted_requirements.tender_purchase_number!r}, а оценка выгоды "
                f"считается для закупки {tender.purchase_number!r}"
            )
        if not extracted_requirements.expert_reviewed:
            raise ValueError(
                "Результат Агента 3 должен быть проверен экспертом "
                "(ExtractedRequirements.expert_reviewed) до того, как им воспользуется Агент 5"
            )

    risk_flags: list[str] = []

    security_cost = _security_cost(profile, tender, extracted_requirements, risk_flags)
    taxes = _taxes(profile, tender, cost_estimate.total_cost, security_cost, risk_flags)
    margin = (
        None
        if taxes is None
        else tender.max_price - cost_estimate.total_cost - security_cost - taxes
    )

    risk_flags.append(_competition_flag(extracted_requirements))
    execution_risk = _execution_risk_flag(extracted_requirements)
    if execution_risk is not None:
        risk_flags.append(execution_risk)

    return ProfitabilityEstimate(
        client_id=profile.client_id,
        tender_purchase_number=tender.purchase_number,
        max_price=tender.max_price,
        cost_estimate=cost_estimate.total_cost,
        cost_estimate_as_of=cost_estimate.as_of_date,
        security_cost=security_cost,
        taxes=taxes,
        margin=margin,
        risk_flags=risk_flags,
    )


def _security_cost(
    profile: ClientProfile,
    tender: Tender,
    extracted_requirements: ExtractedRequirements | None,
    risk_flags: list[str],
) -> float:
    if not profile.financial.bank_guarantee_available:
        # Профиль явно указывает, что банковской гарантии нет — деньги на
        # обеспечение замораживаются из оборотных средств. Это упущенная
        # выгода, но на пилоте её не считаем (согласовано в ТЗ).
        return 0.0

    if extracted_requirements is None:
        risk_flags.append(
            "Не удалось точно посчитать стоимость обеспечения контракта — Агент 3 "
            "не подключён к этой оценке (не хватает данных, не значит, что обеспечение "
            "бесплатно)"
        )
        return 0.0

    contract_security = extracted_requirements.security_requirement("contract")
    timeline = extracted_requirements.timeline
    if (
        contract_security is None
        or timeline.performance_start is None
        or timeline.performance_end is None
    ):
        risk_flags.append(
            "Не удалось точно посчитать стоимость обеспечения контракта — в документации "
            "закупки не нашлось требования к обеспечению исполнения контракта и/или срока "
            "исполнения (не хватает данных, не значит, что обеспечение бесплатно)"
        )
        return 0.0

    security_amount = contract_security.amount
    if security_amount is None:
        if contract_security.percentage is None:
            risk_flags.append(
                "Не удалось точно посчитать стоимость обеспечения контракта — найдено "
                "требование к обеспечению, но без суммы и без процента"
            )
            return 0.0
        # Сумма обеспечения не указана явно текстом — считаем от НМЦК, так как
        # реальная цена контракта до победы в закупке не известна.
        security_amount = tender.max_price * contract_security.percentage / 100

    duration_days = (timeline.performance_end - timeline.performance_start).days
    duration_years = duration_days / _DAYS_PER_YEAR
    return security_amount * _BANK_GUARANTEE_ANNUAL_RATE * duration_years


def _taxes(
    profile: ClientProfile,
    tender: Tender,
    total_cost: float,
    security_cost: float,
    risk_flags: list[str],
) -> float | None:
    tax_regime = profile.financial.tax_regime
    if tax_regime is None or tax_regime == TaxRegimeChoice.OTHER_NEEDS_CLARIFICATION:
        risk_flags.append(
            "Налоговый режим клиента требует уточнения с бухгалтером — маржа не может "
            "быть рассчитана без него"
        )
        return None

    base, income_rate, vat_rate = _TAX_RULES[tax_regime]
    vat_amount = tender.max_price * vat_rate
    if base == "revenue":
        return tender.max_price * income_rate + vat_amount

    profit_before_tax = max(tender.max_price - total_cost - security_cost, 0.0)
    return profit_before_tax * income_rate + vat_amount


def _competition_flag(extracted_requirements: ExtractedRequirements | None) -> str:
    """Грубая эвристика по количеству найденных Агентом 3 требований к
    участнику — не наука, просто явный качественный сигнал вместо тишины."""
    if extracted_requirements is None:
        return (
            "Строгость требований к участнику не оценена — Агент 3 не подключён к этой "
            "оценке"
        )

    count = len(extracted_requirements.participant_requirements)
    if count == 0:
        return "Требований к участнику не найдено — вероятно много потенциальных конкурентов"
    if count == 1:
        return "Найдено одно требование к участнику — вероятно среднее число конкурентов"
    return (
        f"Найдено {count} требования(-ий) к участнику — вероятно мало конкурентов, "
        "высокий барьер входа"
    )


def _execution_risk_flag(extracted_requirements: ExtractedRequirements | None) -> str | None:
    if extracted_requirements is None:
        return None

    flagged_categories = {RiskCategory.NONSTANDARD_PENALTY, RiskCategory.UNCAPPED_LIABILITY}
    matching = [r for r in extracted_requirements.hidden_risks if r.category in flagged_categories]
    if not matching:
        return None

    kinds = ", ".join(sorted({r.category.value for r in matching}))
    return (
        f"Повышенный риск при срыве исполнения — в документации закупки найдены "
        f"нестандартные штрафные условия ({kinds})"
    )

"""Оценка выгоды по одной паре клиент+закупка (Агент 5).

`estimate_profitability()` — единственная точка входа. `cost_estimate`
(себестоимость) теперь реально строится Агентом 4
(`smeta_estimator.cost_estimate.build_cost_estimate()` ->
`SmetaCostResult.to_cost_estimate()`), а не передаётся вручную придуманным
числом — но эта функция сама не вызывает Агента 4, а принимает уже готовый
`CostEstimate` и проверяет на нём тот же гейт, что уже применяется к Агенту 3
(`CostEstimate.expert_reviewed`, см. `models.py`).

**Это каркас с грубыми приближениями, а не проверенная бухгалтером формула.**
Таблица «налоговый режим клиента → сумма налога в рублях»
(`_TAX_RULES` ниже) — рабочий вариант, требующий подтверждения бухгалтером
клиента или владельца продукта, по аналогии с тем, как уже помечена как
временная заглушка финансовая формула Агента 2 (`classifier.matching`).
В частности: НДС везде считается от полной НМЦК без учёта вычета входного
НДС (для ОСН это неточно — вычитать не на чем, входных данных о затратах с
НДС нет), и не учтены страховые взносы/прочие обязательные платежи.

**Обновлено 2026-09-25 — два подтверждённых пробела закрыты частично, см.
CLAUDE.md, «Известные пробелы», п.6:**
1. Ставка налога на прибыль ООО на ОСН исправлена с 20% на **25%**
   (ст. 284 НК РФ в редакции 176-ФЗ от 12.07.2024 — действует с 01.01.2025,
   не с 01.01.2026, как изначально предполагалось в задаче; сама ставка 25%
   подтверждена независимо от даты).
2. Прежний единый `OSN_VAT_22` разделён на `OSN_OOO_VAT_22` (налог на
   прибыль, теперь 25%, плоская ставка) и `OSN_IP_VAT_22` (НДФЛ, не налог на
   прибыль — у ИП другой налог с того же дохода). Для ИП реализована не
   грубая плоская ставка, а настоящая прогрессивная шкала НДФЛ (`_ndfl_progressive()`,
   13/15/18/20/22% по порогам ст. 224 НК РФ, та же реформа 176-ФЗ) —
   подтверждено несколькими независимыми источниками (Гарант, Контур,
   Бухгуру, nalog.gov.ru/new2026), не первоисточником текста статьи и не
   бухгалтером. **Честная оговорка, которую нельзя убрать без искажения
   смысла:** прогрессивная шкала НДФЛ применяется к СОВОКУПНОМУ годовому
   доходу физлица по всем источникам, а не только к прибыли по одной
   закупке — сервис не знает других доходов ИП, поэтому здесь ставка
   считается только от прибыли по этой закупке, что может занизить реальную
   ставку, если у ИП есть другие крупные доходы (флаг риска сообщает об
   этом явно, не тихо).
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
# НМЦК минус себестоимость минус обеспечение, обложенная ПЛОСКОЙ ставкой
# (УСН "доходы минус расходы" и ОСН для ООО — налог на прибыль 25%,
# ст. 284 НК РФ, действует с 01.01.2025). База "profit_progressive_ndfl" —
# та же прибыль, но обложенная ПРОГРЕССИВНОЙ шкалой НДФЛ (`_ndfl_progressive()`
# ниже, не единая ставка) — ставка налога в этой строке всегда None, реальный
# расчёт смотрит на брекеты. НДС во всех случаях считается от НМЦК (валовый
# доход), независимо от базы налога на доход/прибыль.
_TAX_RULES: dict[TaxRegimeChoice, tuple[str, float | None, float]] = {
    TaxRegimeChoice.USN_6_NO_VAT: ("revenue", 0.06, 0.0),
    TaxRegimeChoice.USN_6_VAT_5: ("revenue", 0.06, 0.05),
    TaxRegimeChoice.USN_6_VAT_7: ("revenue", 0.06, 0.07),
    TaxRegimeChoice.USN_15_NO_VAT: ("profit", 0.15, 0.0),
    TaxRegimeChoice.USN_15_VAT_5: ("profit", 0.15, 0.05),
    TaxRegimeChoice.USN_15_VAT_7: ("profit", 0.15, 0.07),
    TaxRegimeChoice.OSN_OOO_VAT_22: ("profit", 0.25, 0.22),
    TaxRegimeChoice.OSN_IP_VAT_22: ("profit_progressive_ndfl", None, 0.22),
}

# Прогрессивная шкала НДФЛ (ст. 224 НК РФ в редакции 176-ФЗ от 12.07.2024,
# действует с 01.01.2025) — ступени применяются только к части дохода сверх
# порога, не ко всей сумме сразу. Подтверждено несколькими независимыми
# источниками (Гарант, Контур, Бухгуру, nalog.gov.ru/new2026), не
# первоисточником и не бухгалтером — см. докстринг модуля.
_NDFL_PROGRESSIVE_BRACKETS: tuple[tuple[float, float], ...] = (
    (2_400_000.0, 0.13),
    (5_000_000.0, 0.15),
    (20_000_000.0, 0.18),
    (50_000_000.0, 0.20),
    (float("inf"), 0.22),
)


def _ndfl_progressive(taxable_profit: float) -> float:
    """Сумма НДФЛ по прогрессивной шкале от переданной прибыли. Считает так,
    как будто вся налогооблагаемая база физлица за год — это прибыль по
    ОДНОЙ этой закупке; реальная ставка у ИП зависит от совокупного годового
    дохода по всем источникам, которого сервис не знает — см. предупреждение
    в докстринге модуля и риск-флаг в `_taxes()`."""
    tax = 0.0
    lower = 0.0
    for upper, rate in _NDFL_PROGRESSIVE_BRACKETS:
        if taxable_profit <= lower:
            break
        tax += (min(taxable_profit, upper) - lower) * rate
        lower = upper
    return tax


def estimate_profitability(
    profile: ClientProfile,
    tender: Tender,
    cost_estimate: CostEstimate,
    extracted_requirements: ExtractedRequirements | None = None,
) -> ProfitabilityEstimate:
    if not cost_estimate.expert_reviewed:
        raise ValueError(
            "Себестоимость Агента 4 должна быть проверена экспертом "
            "(CostEstimate.expert_reviewed) до того, как ей воспользуется Агент 5"
        )

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

    if not cost_estimate.is_complete:
        risk_flags.append(
            "Себестоимость от Агента 4 неполная — не все позиции сметы удалось оценить, "
            f"итоговая маржа завышена: {cost_estimate.source_note}"
        )

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
    if base == "profit_progressive_ndfl":
        risk_flags.append(
            "НДФЛ ИП на ОСН посчитан по прогрессивной шкале только от прибыли по этой "
            "закупке — реальная ставка зависит от совокупного годового дохода ИП по всем "
            "источникам (сервис его не знает); если есть другие крупные доходы, фактическая "
            "ставка может быть выше"
        )
        return _ndfl_progressive(profit_before_tax) + vat_amount

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

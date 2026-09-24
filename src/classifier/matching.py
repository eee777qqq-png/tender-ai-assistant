"""Сопоставление профиля клиента (Агент 11) с конкретной закупкой (Агент 2).

Помимо принадлежности ОКПД2 к разделу «Строительство» (`ConstructionClassifier`),
здесь проверяются требования конкретной закупки к конкретному клиенту:
регион, членство в СРО, опыт, финансовая готовность.

Критерий финансовой готовности — единственный из шести, у которого два
режима, потому что Агент 2 в конвейере вызывается ДО Агента 4/5 (себестоимость
и маржа по конкретной закупке считаются намного позже — после сборки пакета
документов и выбора экспертом позиций сметы, см. CLAUDE.md, п.12 «Известных
пробелов»). Гонять полную смету и экспертную проверку по каждой закупке,
прежде чем узнать, стоит ли вообще ей заниматься, — не то, для чего нужен
быстрый фильтр Агента 2. Поэтому:

- без `profitability` (обычный путь — быстрая фильтрация потока закупок из
  Агента 1, до того как Агент 4/5 вообще запускались) — используется грубая
  эвристика `avg_annual_revenue >= max_price`, явно помеченная в сообщении
  как предварительная, не окончательная;
- с `profitability` (повторная, уточняющая проверка уже после того, как
  Агент 5 посчитал реальную маржу по этой паре клиент+закупка — например,
  непосредственно перед сборкой итоговой сводки для клиента у Агента 8) —
  критерий заменяется на `margin > 0`, настоящую посчитанную выгоду, а не
  прокси через выручку.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from onboarding.models import ClientProfile

from .okpd2 import ConstructionClassifier
from .tender import Tender

if TYPE_CHECKING:
    # Только для аннотаций типов (`from __future__ import annotations` выше
    # не вычисляет их в рантайме) — иначе реальный импорт создал бы цикл
    # classifier -> profitability_estimator -> classifier.tender.
    from profitability_estimator.models import ProfitabilityEstimate


@dataclass
class MatchCriterion:
    name: str
    passed: bool
    message: str


@dataclass
class MatchResult:
    tender: Tender
    client_id: str
    criteria: list[MatchCriterion] = field(default_factory=list)

    @property
    def is_match(self) -> bool:
        return all(c.passed for c in self.criteria)

    @property
    def score(self) -> float:
        if not self.criteria:
            return 0.0
        return sum(1 for c in self.criteria if c.passed) / len(self.criteria)

    @property
    def failed_reasons(self) -> list[str]:
        return [c.message for c in self.criteria if not c.passed]


def match_profile_to_tender(
    profile: ClientProfile,
    tender: Tender,
    classifier: ConstructionClassifier,
    profitability: "ProfitabilityEstimate | None" = None,
) -> MatchResult:
    criteria: list[MatchCriterion] = []

    is_construction = classifier.is_construction_code(tender.okpd2_code)
    criteria.append(
        MatchCriterion(
            "construction_sector",
            is_construction,
            "Закупка относится к разделу «Строительство»"
            if is_construction
            else f"ОКПД2 {tender.okpd2_code} не относится к разделу «Строительство»",
        )
    )

    region_ok = bool(profile.region_code) and profile.region_code == tender.region_code
    criteria.append(
        MatchCriterion(
            "region",
            region_ok,
            "Регион клиента совпадает с регионом закупки"
            if region_ok
            else f"Клиент работает в регионе {profile.region_code or '(не указан)'}, "
            f"закупка в регионе {tender.region_code}",
        )
    )

    sro_ok = not tender.requires_sro or profile.permits_experience.sro_membership
    criteria.append(
        MatchCriterion(
            "sro_membership",
            sro_ok,
            "Требование по членству в СРО выполнено"
            if sro_ok
            else "Закупка требует членства в СРО, у клиента его нет",
        )
    )

    experience_ok = profile.permits_experience.years_of_experience >= tender.min_experience_years
    criteria.append(
        MatchCriterion(
            "experience",
            experience_ok,
            "Опыта достаточно"
            if experience_ok
            else f"Нужно {tender.min_experience_years} лет опыта, у клиента "
            f"{profile.permits_experience.years_of_experience}",
        )
    )

    capacity_ok = profile.capacity.staff_count > 0
    criteria.append(
        MatchCriterion(
            "capacity",
            capacity_ok,
            "Производственные мощности заявлены" if capacity_ok else "Не указана численность персонала",
        )
    )

    criteria.append(_financial_capacity_criterion(profile, tender, profitability))

    return MatchResult(tender=tender, client_id=profile.client_id, criteria=criteria)


def _financial_capacity_criterion(
    profile: ClientProfile, tender: Tender, profitability: "ProfitabilityEstimate | None"
) -> MatchCriterion:
    if profitability is None:
        financial_ok = profile.financial.avg_annual_revenue >= tender.max_price
        return MatchCriterion(
            "financial_capacity",
            financial_ok,
            "Финансовой готовности достаточно (предварительная оценка по выручке — "
            "Агент 5 ещё не считал реальную маржу по этой закупке)"
            if financial_ok
            else f"НМЦК {tender.max_price:,.0f} ₽ превышает среднегодовую выручку клиента "
            f"{profile.financial.avg_annual_revenue:,.0f} ₽ (предварительная оценка по "
            "выручке — Агент 5 ещё не считал реальную маржу по этой закупке)",
        )

    if profitability.tender_purchase_number != tender.purchase_number:
        raise ValueError(
            "Оценка выгоды Агента 5 относится к закупке "
            f"{profitability.tender_purchase_number!r}, а матчинг считается для закупки "
            f"{tender.purchase_number!r}"
        )
    if profitability.client_id != profile.client_id:
        raise ValueError(
            f"Оценка выгоды Агента 5 относится к клиенту {profitability.client_id!r}, а "
            f"матчинг считается для клиента {profile.client_id!r}"
        )

    if profitability.margin is None:
        return MatchCriterion(
            "financial_capacity",
            False,
            "Маржа не рассчитана Агентом 5 (налоговый режим клиента требует уточнения с "
            "бухгалтером) — финансовая готовность не подтверждена",
        )

    margin_ok = profitability.margin > 0
    return MatchCriterion(
        "financial_capacity",
        margin_ok,
        f"Закупка выгодна: маржа Агента 5 положительна ({profitability.margin:,.0f} ₽)"
        if margin_ok
        else f"Закупка невыгодна: маржа Агента 5 не положительна ({profitability.margin:,.0f} ₽)",
    )


def find_matching_tenders(
    profile: ClientProfile, tenders: list[Tender], classifier: ConstructionClassifier
) -> list[MatchResult]:
    """Прогоняет все закупки через `match_profile_to_tender` и сортирует по score
    (лучшие совпадения — первые), чтобы результат сразу годился для показа клиенту."""
    results = [match_profile_to_tender(profile, tender, classifier) for tender in tenders]
    return sorted(results, key=lambda r: r.score, reverse=True)

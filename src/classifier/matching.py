"""Сопоставление профиля клиента (Агент 11) с конкретной закупкой (Агент 2).

Помимо принадлежности ОКПД2 к разделу «Строительство» (`ConstructionClassifier`),
здесь проверяются требования конкретной закупки к конкретному клиенту:
регион, членство в СРО, опыт, финансовая готовность. Критерий финансовой
готовности (`avg_annual_revenue >= max_price`) — упрощённая эвристика для
прототипа, не согласованный с владельцем продукта критерий скоринга
(это, по-хорошему, будущая зона ответственности Агента 5 — Скоринг
вероятности победы); здесь она нужна только чтобы прототип показывал
осмысленный результат на тестовых данных.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from onboarding.models import ClientProfile

from .okpd2 import ConstructionClassifier
from .tender import Tender


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
    profile: ClientProfile, tender: Tender, classifier: ConstructionClassifier
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

    financial_ok = profile.financial.avg_annual_revenue >= tender.max_price
    criteria.append(
        MatchCriterion(
            "financial_capacity",
            financial_ok,
            "Финансовой готовности достаточно"
            if financial_ok
            else f"НМЦК {tender.max_price:,.0f} ₽ превышает среднегодовую выручку клиента "
            f"{profile.financial.avg_annual_revenue:,.0f} ₽",
        )
    )

    return MatchResult(tender=tender, client_id=profile.client_id, criteria=criteria)


def find_matching_tenders(
    profile: ClientProfile, tenders: list[Tender], classifier: ConstructionClassifier
) -> list[MatchResult]:
    """Прогоняет все закупки через `match_profile_to_tender` и сортирует по score
    (лучшие совпадения — первые), чтобы результат сразу годился для показа клиенту."""
    results = [match_profile_to_tender(profile, tender, classifier) for tender in tenders]
    return sorted(results, key=lambda r: r.score, reverse=True)

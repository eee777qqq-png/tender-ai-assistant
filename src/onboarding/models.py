"""Модели профиля клиента (Агент 11 — Онбординг).

Профиль состоит из 4 блоков полей (юр.данные, допуски/опыт, мощности,
финансовая готовность) и статусной модели: черновик → заполнен → проверен
экспертом → готов. Переход на "проверен экспертом" — не автоматический:
его выполняет человек через `ClientProfile.submit_expert_review()`, что
соответствует протоколу контроля качества (CLAUDE.md) — профиль проверяется
на полноту и достоверность до того, как им воспользуется Агент 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .tax_config import TaxRegimeChoice


class ProfileStatus(str, Enum):
    DRAFT = "draft"  # черновик — не все обязательные поля заполнены
    FILLED = "filled"  # заполнен — все 4 блока есть и прошли автоматическую проверку формата
    EXPERT_REVIEWED = "expert_reviewed"  # проверен экспертом-человеком
    READY = "ready"  # готов — можно передавать Агенту 6


@dataclass
class LegalInfo:
    """Блок 1: юридические данные."""

    org_name: str = ""
    inn: str = ""
    ogrn: str = ""
    legal_address: str = ""
    contact_person: str = ""
    phone: str = ""
    email: str = ""

    def is_filled(self) -> bool:
        return all(
            [
                self.org_name,
                self.inn,
                self.ogrn,
                self.legal_address,
                self.contact_person,
                self.phone,
                self.email,
            ]
        )


@dataclass
class CompletedContract:
    """Один исполненный контракт из опыта клиента (для блока допусков/опыта)."""

    object_name: str
    customer: str
    amount: float
    year: int


@dataclass
class PermitsExperience:
    """Блок 2: допуски и опыт."""

    sro_membership: bool = False
    sro_number: str = ""
    licenses: list[str] = field(default_factory=list)
    completed_contracts: list[CompletedContract] = field(default_factory=list)
    years_of_experience: int = 0

    def is_filled(self) -> bool:
        return self.years_of_experience > 0 or bool(self.completed_contracts)


@dataclass
class Capacity:
    """Блок 3: производственные мощности."""

    staff_count: int = 0
    equipment: list[str] = field(default_factory=list)
    own_workforce_description: str = ""
    subcontractors_allowed: bool = False

    def is_filled(self) -> bool:
        return self.staff_count > 0 and bool(self.own_workforce_description)


@dataclass
class FinancialReadiness:
    """Блок 4: финансовая готовность.

    `tax_regime` — клиент выбирает готовую комбинацию режим+ставка сам, из
    фиксированного списка `tax_config.TaxRegimeChoice` (форма показывает
    `tax_config.TAX_REGIME_OPTIONS`), а не пишет систему налогообложения
    произвольным текстом. Сервис не вычисляет применимую ставку НДС из
    `avg_annual_revenue` и не следит за порогами/индексацией УСН — см.
    докстринг `tax_config`. `avg_annual_revenue` при этом остаётся: она
    нужна для отдельной задачи — финансовой состоятельности клиента у
    Агента 2 (`classifier.matching`), с выбором налогового режима не
    связана.
    """

    tax_regime: TaxRegimeChoice | None = None
    avg_annual_revenue: float = 0.0
    working_capital: float = 0.0
    bank_guarantee_available: bool = False

    def is_filled(self) -> bool:
        return self.tax_regime is not None and self.avg_annual_revenue > 0


@dataclass
class ExpertReview:
    """Запись о проверке профиля экспертом-человеком."""

    reviewer: str
    approved: bool
    notes: str = ""
    reviewed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ClientProfile:
    """Профиль клиента целиком — 4 блока + статусная модель.

    region_codes — регионы, в которых клиент готов участвовать в закупках
    (используется Агентом 2 при сопоставлении профиля с закупкой, см.
    `classifier.matching`), не часть исходных 4 блоков ТЗ, но нужен
    для сквозного сценария "профиль → подходящая закупка". Список, не одно
    значение — 2026-09-26, реальный профиль Edwin работает сразу в двух
    регионах (Москва и Московская область), один `str` этого не выражал.
    """

    client_id: str
    legal: LegalInfo = field(default_factory=LegalInfo)
    permits_experience: PermitsExperience = field(default_factory=PermitsExperience)
    capacity: Capacity = field(default_factory=Capacity)
    financial: FinancialReadiness = field(default_factory=FinancialReadiness)
    region_codes: list[str] = field(default_factory=list)
    status: ProfileStatus = ProfileStatus.DRAFT
    expert_reviews: list[ExpertReview] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_complete(self) -> bool:
        """Все 4 блока заполнены обязательными полями (ещё не проверка достоверности)."""
        return all(
            [
                self.legal.is_filled(),
                self.permits_experience.is_filled(),
                self.capacity.is_filled(),
                self.financial.is_filled(),
            ]
        )

    def submit_expert_review(self, reviewer: str, approved: bool, notes: str = "") -> None:
        """Фиксирует решение эксперта. Разрешено только из статуса FILLED —
        нельзя проверять черновик, в котором ещё не все поля на месте."""
        if self.status != ProfileStatus.FILLED:
            raise ValueError(
                f"Экспертная проверка возможна только из статуса FILLED, сейчас: {self.status}"
            )
        self.expert_reviews.append(ExpertReview(reviewer=reviewer, approved=approved, notes=notes))
        self.status = ProfileStatus.EXPERT_REVIEWED if approved else ProfileStatus.DRAFT
        self.touch()

    def mark_ready(self) -> None:
        """Финальный шаг — профиль официально передаётся в конвейер (Агенту 6)."""
        if self.status != ProfileStatus.EXPERT_REVIEWED:
            raise ValueError(
                f"Пометить готовым можно только после EXPERT_REVIEWED, сейчас: {self.status}"
            )
        self.status = ProfileStatus.READY
        self.touch()

    def is_ready_for_agent_6(self) -> bool:
        """Правило из протокола контроля качества: Агент 6 может использовать
        только проверенный на полноту и достоверность профиль."""
        return self.status == ProfileStatus.READY

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

"""Модели профиля клиента (Агент 11 — Онбординг).

Профиль состоит из 4 блоков полей (юр.данные, допуски/опыт, мощности,
финансовая готовность) и статусной модели. Согласно протоколу контроля
качества (CLAUDE.md), профиль должен быть проверен на полноту и
достоверность до того, как им воспользуется Агент 6 (Сборщик документов) —
за это отвечает `ProfileStatus.VERIFIED` и `validate_profile()` из
`onboarding.validation`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ProfileStatus(str, Enum):
    DRAFT = "draft"  # создан, не все обязательные поля заполнены
    COMPLETE = "complete"  # все 4 блока заполнены, ждёт проверки на достоверность
    VERIFIED = "verified"  # проверен и достоверен — можно передавать Агенту 6
    REJECTED = "rejected"  # проверка выявила проблемы, нужны исправления от клиента


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
    """Блок 4: финансовая готовность."""

    tax_system: str = ""
    avg_annual_revenue: float = 0.0
    working_capital: float = 0.0
    bank_guarantee_available: bool = False

    def is_filled(self) -> bool:
        return bool(self.tax_system) and self.avg_annual_revenue > 0


@dataclass
class ClientProfile:
    """Профиль клиента целиком — 4 блока + статусная модель."""

    client_id: str
    legal: LegalInfo = field(default_factory=LegalInfo)
    permits_experience: PermitsExperience = field(default_factory=PermitsExperience)
    capacity: Capacity = field(default_factory=Capacity)
    financial: FinancialReadiness = field(default_factory=FinancialReadiness)
    status: ProfileStatus = ProfileStatus.DRAFT
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

    def is_ready_for_agent_6(self) -> bool:
        """Правило из протокола контроля качества: Агент 6 может использовать
        только проверенный на полноту и достоверность профиль."""
        return self.status == ProfileStatus.VERIFIED

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

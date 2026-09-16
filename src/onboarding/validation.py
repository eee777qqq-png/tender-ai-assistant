"""Проверка профиля клиента на полноту и достоверность (Агент 11).

`validate_profile()` — единственная точка входа: прогоняет проверки формата
и полноты по всем 4 блокам, обновляет `profile.status` и возвращает список
найденных проблем. Пустой список + `ProfileStatus.VERIFIED` — сигнал, что
профилем может пользоваться Агент 6 (см. `ClientProfile.is_ready_for_agent_6`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ClientProfile, ProfileStatus

_INN_RE = re.compile(r"^\d{10}$|^\d{12}$")
_OGRN_RE = re.compile(r"^\d{13}$|^\d{15}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?\d{10,15}$")


@dataclass
class ValidationIssue:
    block: str
    field: str
    message: str


def validate_profile(profile: ClientProfile) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    issues += _validate_legal(profile)
    issues += _validate_permits_experience(profile)
    issues += _validate_capacity(profile)
    issues += _validate_financial(profile)

    if not profile.is_complete():
        profile.status = ProfileStatus.DRAFT
    elif issues:
        profile.status = ProfileStatus.REJECTED
    else:
        profile.status = ProfileStatus.VERIFIED
    profile.touch()

    return issues


def _validate_legal(profile: ClientProfile) -> list[ValidationIssue]:
    legal = profile.legal
    issues: list[ValidationIssue] = []
    if legal.inn and not _INN_RE.match(legal.inn):
        issues.append(
            ValidationIssue("legal", "inn", "ИНН должен состоять из 10 или 12 цифр")
        )
    if legal.ogrn and not _OGRN_RE.match(legal.ogrn):
        issues.append(
            ValidationIssue("legal", "ogrn", "ОГРН(ИП) должен состоять из 13 или 15 цифр")
        )
    if legal.email and not _EMAIL_RE.match(legal.email):
        issues.append(ValidationIssue("legal", "email", "Некорректный формат email"))
    if legal.phone and not _PHONE_RE.match(legal.phone):
        issues.append(ValidationIssue("legal", "phone", "Некорректный формат телефона"))
    return issues


def _validate_permits_experience(profile: ClientProfile) -> list[ValidationIssue]:
    pe = profile.permits_experience
    issues: list[ValidationIssue] = []
    if pe.sro_membership and not pe.sro_number:
        issues.append(
            ValidationIssue(
                "permits_experience", "sro_number", "Указано членство в СРО без номера"
            )
        )
    for i, contract in enumerate(pe.completed_contracts):
        if contract.amount <= 0:
            issues.append(
                ValidationIssue(
                    "permits_experience",
                    f"completed_contracts[{i}].amount",
                    "Сумма контракта должна быть положительной",
                )
            )
    return issues


def _validate_capacity(profile: ClientProfile) -> list[ValidationIssue]:
    capacity = profile.capacity
    issues: list[ValidationIssue] = []
    if capacity.staff_count < 0:
        issues.append(
            ValidationIssue(
                "capacity", "staff_count", "Численность персонала не может быть отрицательной"
            )
        )
    return issues


def _validate_financial(profile: ClientProfile) -> list[ValidationIssue]:
    financial = profile.financial
    issues: list[ValidationIssue] = []
    if financial.avg_annual_revenue < 0:
        issues.append(
            ValidationIssue(
                "financial",
                "avg_annual_revenue",
                "Среднегодовая выручка не может быть отрицательной",
            )
        )
    if financial.working_capital < 0:
        issues.append(
            ValidationIssue(
                "financial", "working_capital", "Оборотный капитал не может быть отрицательным"
            )
        )
    return issues

"""Результат проверки комплектности пакета документов (Агент 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CompletenessStatus(str, Enum):
    PASS_ = "PASS"
    FAIL = "FAIL"


@dataclass
class CompletenessResult:
    """Итог проверки комплектности одного пакета документов.

    `missing_required_fields` — конкретные имена полей, не общая фраза:
    именно их не хватает эксперту/клиенту, чтобы понять, что доделать.
    `missing_optional_fields` — необязательные поля без значения; они не
    блокируют подачу, но выводятся для информации.
    """

    client_id: str
    tender_purchase_number: str
    status: CompletenessStatus
    missing_required_fields: list[str] = field(default_factory=list)
    missing_optional_fields: list[str] = field(default_factory=list)

    def is_pass(self) -> bool:
        return self.status == CompletenessStatus.PASS_

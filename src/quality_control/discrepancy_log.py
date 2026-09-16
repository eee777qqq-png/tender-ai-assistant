"""Лог расхождений между выдачей агента и правкой эксперта.

Часть ТЗ Агента 4 (Сметчик) из CLAUDE.md: «метрика перехода на выборочный
аудит + лог расхождений». Метрика — в `audit_readiness.py`; здесь —
структурированная запись того, *что именно* эксперт поправил, чтобы потом
анализировать частые источники ошибок (например, какие расценки ФЕР/ТЕР/ГЭСН
агент чаще всего берёт неверно). Не привязан к конкретному агенту — годится
и для 3, и для 6, если появится такая же потребность.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


@dataclass
class DiscrepancyEntry:
    agent_name: str
    check_id: str
    field: str
    agent_value: str
    expert_value: str
    critical: bool = False
    note: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class DiscrepancyLog:
    def __init__(self) -> None:
        self._entries: list[DiscrepancyEntry] = []

    def log(
        self,
        agent_name: str,
        check_id: str,
        field_name: str,
        agent_value: str,
        expert_value: str,
        critical: bool = False,
        note: str = "",
    ) -> DiscrepancyEntry:
        entry = DiscrepancyEntry(
            agent_name=agent_name,
            check_id=check_id,
            field=field_name,
            agent_value=agent_value,
            expert_value=expert_value,
            critical=critical,
            note=note,
        )
        self._entries.append(entry)
        return entry

    def for_agent(self, agent_name: str) -> list[DiscrepancyEntry]:
        return [e for e in self._entries if e.agent_name == agent_name]

    def for_check(self, agent_name: str, check_id: str) -> list[DiscrepancyEntry]:
        return [
            e for e in self._entries if e.agent_name == agent_name and e.check_id == check_id
        ]

    def most_common_fields(self, agent_name: str, top_n: int = 5) -> list[tuple[str, int]]:
        """Поля, которые эксперт правит чаще всего — подсказка, где агент
        систематически ошибается."""
        counter = Counter(e.field for e in self.for_agent(agent_name))
        return counter.most_common(top_n)

    def critical_count(self, agent_name: str) -> int:
        return sum(1 for e in self.for_agent(agent_name) if e.critical)


class DiscrepancyCategory(str, Enum):
    """Трёхуровневая серьёзность — для агентов, которые извлекают/собирают
    информацию (Агент 3 — Аналитик документации; годится и для Агента 6 —
    Сборщик документов), а не считают числа (там `DiscrepancyEntry` выше со
    своим бинарным `critical` достаточен)."""

    CRITICAL = "critical"  # пропущенное требование, меняющее решение об участии или создающее юридический риск
    SIGNIFICANT = "significant"  # существенная корректировка, но не критическая
    COSMETIC = "cosmetic"  # косметическая правка, не влияет на решение


@dataclass
class CategorizedDiscrepancy:
    """Расхождение эксперт vs агент, описанное текстом, а не парой значений:
    «что именно пропущено или неверно извлечено» плюс комментарий эксперта."""

    agent_name: str
    check_id: str
    category: DiscrepancyCategory
    issue: str  # что пропущено или неверно извлечено агентом
    expert_comment: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def needed_correction(self) -> bool:
        """Для `AuditReadinessTracker.record_check()` — косметическая правка
        не считается существенной корректировкой для метрики перехода."""
        return self.category != DiscrepancyCategory.COSMETIC

    @property
    def critical(self) -> bool:
        return self.category == DiscrepancyCategory.CRITICAL


class CategorizedDiscrepancyLog:
    def __init__(self) -> None:
        self._entries: list[CategorizedDiscrepancy] = []

    def log(
        self,
        agent_name: str,
        check_id: str,
        category: DiscrepancyCategory,
        issue: str,
        expert_comment: str = "",
    ) -> CategorizedDiscrepancy:
        entry = CategorizedDiscrepancy(
            agent_name=agent_name,
            check_id=check_id,
            category=category,
            issue=issue,
            expert_comment=expert_comment,
        )
        self._entries.append(entry)
        return entry

    def for_agent(self, agent_name: str) -> list[CategorizedDiscrepancy]:
        return [e for e in self._entries if e.agent_name == agent_name]

    def for_check(self, agent_name: str, check_id: str) -> list[CategorizedDiscrepancy]:
        return [
            e for e in self._entries if e.agent_name == agent_name and e.check_id == check_id
        ]

    def critical_count(self, agent_name: str) -> int:
        return sum(1 for e in self.for_agent(agent_name) if e.critical)

    def by_category(
        self, agent_name: str, category: DiscrepancyCategory
    ) -> list[CategorizedDiscrepancy]:
        return [e for e in self.for_agent(agent_name) if e.category == category]

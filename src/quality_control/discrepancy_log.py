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

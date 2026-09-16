"""Метрика перехода агента с полной проверки эксперта на выборочный аудит.

Правило из протокола контроля качества (CLAUDE.md), общее для агентов
3 (Аналитик документации), 4 (Сметчик) и 6 (Сборщик документов):

- скользящее окно из последних 50 проверок;
- переход на выборочный аудит возможен, если в этом окне ≥90% проверок
  прошли без существенной корректировки экспертом и 0 критических ошибок;
- после перехода любая критическая ошибка немедленно откатывает агента
  обратно на полную проверку эксперта, и окно набирается заново.

`AuditReadinessTracker` не привязан к конкретному агенту — по одному
экземпляру на агента (например, хранить в реестре по `agent_name`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

WINDOW_SIZE = 50
MIN_OK_RATIO = 0.9


class ReviewMode(str, Enum):
    FULL_EXPERT_REVIEW = "full_expert_review"  # каждую выдачу смотрит эксперт
    SPOT_CHECK = "spot_check"  # выборочный аудит


@dataclass
class CheckOutcome:
    """Результат одной проверки экспертом выдачи агента."""

    needed_correction: bool  # была существенная корректировка
    critical_error: bool = False  # была найдена критическая ошибка
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RollbackEvent:
    agent_name: str
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AuditReadinessTracker:
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.mode = ReviewMode.FULL_EXPERT_REVIEW
        self.rollback_history: list[RollbackEvent] = []
        self._window: deque[CheckOutcome] = deque(maxlen=WINDOW_SIZE)

    def record_check(self, needed_correction: bool, critical_error: bool = False) -> None:
        outcome = CheckOutcome(needed_correction=needed_correction, critical_error=critical_error)
        if critical_error and self.mode == ReviewMode.SPOT_CHECK:
            self._rollback(f"критическая ошибка в проверке #{len(self._window) + 1}")
        self._window.append(outcome)
        self._maybe_promote()

    def requires_human_review(self) -> bool:
        return self.mode == ReviewMode.FULL_EXPERT_REVIEW

    def window_stats(self) -> dict[str, float | int]:
        total = len(self._window)
        ok_count = sum(1 for o in self._window if not o.needed_correction)
        critical_count = sum(1 for o in self._window if o.critical_error)
        return {
            "total": total,
            "ok_ratio": (ok_count / total) if total else 0.0,
            "critical_count": critical_count,
        }

    def _maybe_promote(self) -> None:
        if self.mode == ReviewMode.SPOT_CHECK:
            return
        if len(self._window) < WINDOW_SIZE:
            return
        if any(o.critical_error for o in self._window):
            return
        ok_ratio = sum(1 for o in self._window if not o.needed_correction) / WINDOW_SIZE
        if ok_ratio >= MIN_OK_RATIO:
            self.mode = ReviewMode.SPOT_CHECK

    def _rollback(self, reason: str) -> None:
        self.mode = ReviewMode.FULL_EXPERT_REVIEW
        self.rollback_history.append(RollbackEvent(agent_name=self.agent_name, reason=reason))
        self._window.clear()


class AuditReadinessRegistry:
    """Реестр трекеров по агентам — по одному на каждого из агентов 3, 4, 6."""

    def __init__(self) -> None:
        self._trackers: dict[str, AuditReadinessTracker] = {}

    def get(self, agent_name: str) -> AuditReadinessTracker:
        if agent_name not in self._trackers:
            self._trackers[agent_name] = AuditReadinessTracker(agent_name)
        return self._trackers[agent_name]

"""Модели Агента 10 (Парсер нормативки).

Два типа источников, которые агент отслеживает:
- `PRICE_BASE` — нормативная база расценок (ФЕР/ТЕР/ГЭСН, региональные
  индексы пересчёта), нужна Агенту 4 (Сметчик);
- `LEGISLATION` — законодательство (44-ФЗ/223-ФЗ), нужна Агентам 3 и 6 для
  актуальных требований к обеспечению, срокам и т.п.

Версия источника хранится не как один текст, а как набор именованных пунктов
(`items`) — так `build_diff()` может показать, какой конкретно пункт
изменился, а не просто «текст стал другим». Формат значения — строка: и для
процента/индекса («6,27»), и для формулировки закона это годится одинаково.

Правило протокола проекта (CLAUDE.md, «Протокол контроля качества»):
«Любое обновление нормативной базы (агент 10) применяется только после
подтверждения эксперта — автоматическое применение запрещено». Отсюда —
статусная модель `PendingUpdate` ниже: обнаруженная версия не заменяет
применённую сама по себе, а становится записью со статусом `PENDING`,
которую эксперт обязан явно подтвердить или отклонить (см. `store.py`,
`RegulatoryUpdateStore.approve_update()` / `.apply_update()`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum


class SourceType(str, Enum):
    PRICE_BASE = "price_base"  # ФЕР/ТЕР/ГЭСН, региональные индексы пересчёта
    LEGISLATION = "legislation"  # 44-ФЗ/223-ФЗ


class UpdateStatus(str, Enum):
    PENDING = "pending"  # обнаружена новая версия, ждёт решения эксперта
    APPROVED = "approved"  # эксперт подтвердил — версия применена
    REJECTED = "rejected"  # эксперт отклонил — применённая версия не менялась


@dataclass
class RegulatoryVersion:
    """Одна версия одного источника — набор именованных пунктов и их значений."""

    source_id: str
    version_label: str
    published_at: date
    items: dict[str, str] = field(default_factory=dict)


@dataclass
class ItemChange:
    """Одно изменение внутри версии: старое значение → новое.

    `old_value is None` — пункт появился впервые в новой версии.
    `new_value is None` — пункт присутствовал раньше и пропал в новой версии.
    """

    key: str
    old_value: str | None
    new_value: str | None

    @property
    def is_new_item(self) -> bool:
        return self.old_value is None

    @property
    def is_removed_item(self) -> bool:
        return self.new_value is None


def build_diff(old_items: dict[str, str] | None, new_items: dict[str, str]) -> list[ItemChange]:
    """Явный список изменённых пунктов между двумя версиями, старое → новое.

    Пункты с одинаковым значением в обеих версиях в diff не попадают —
    только то, что реально изменилось, добавилось или пропало."""
    old_items = old_items or {}
    all_keys = sorted(set(old_items) | set(new_items))
    changes = []
    for key in all_keys:
        old_value = old_items.get(key)
        new_value = new_items.get(key)
        if old_value != new_value:
            changes.append(ItemChange(key=key, old_value=old_value, new_value=new_value))
    return changes


@dataclass
class UpdateDiff:
    source_id: str
    from_version_label: str | None  # None — источник встречается впервые, применённой версии ещё не было
    to_version_label: str
    changes: list[ItemChange] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.changes


@dataclass
class PendingUpdate:
    """Обнаруженная новая версия источника, ожидающая (или уже получившая)
    решение эксперта. Само по себе создание записи не меняет применённую
    версию — см. docstring модуля."""

    update_id: int
    source_id: str
    source_type: SourceType
    source_name: str
    candidate_version: RegulatoryVersion
    diff: UpdateDiff
    status: UpdateStatus
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reviewer: str | None = None
    decision_reason: str = ""
    decided_at: datetime | None = None

"""Хранилище состояния Агента 10 (Парсер нормативки) — по аналогии с
`eis_client.store.MonitorStore` у Агента 1, но с другой задачей: не копить
найденные документы, а помнить, какая версия нормативной базы/законодательства
сейчас *применена*, и вести очередь обнаруженных новых версий, ожидающих
решения эксперта.

Ключевое архитектурное ограничение (CLAUDE.md, «Протокол контроля качества»):
«Любое обновление нормативной базы (агент 10) применяется только после
подтверждения эксперта — автоматическое применение запрещено». Поэтому
`detect_update()` только создаёт запись `PendingUpdate` со статусом
`PENDING` — применённая версия (`applied_versions`) при этом не меняется.
Единственный путь изменить применённую версию — `apply_update()`, а он
сам отказывается работать, пока запись не переведена в `APPROVED` через
`approve_update(reviewer, approved=True/False)`.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import (
    ItemChange,
    PendingUpdate,
    RegulatoryVersion,
    SourceType,
    UpdateDiff,
    UpdateStatus,
    build_diff,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "regulatory_updates.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applied_versions (
    source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
    version_label TEXT NOT NULL,
    published_at TEXT NOT NULL,
    items_json TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pending_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    version_label TEXT NOT NULL,
    published_at TEXT NOT NULL,
    items_json TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    reviewer TEXT,
    decision_reason TEXT NOT NULL DEFAULT '',
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS check_log (
    source_id TEXT PRIMARY KEY,
    last_checked_at TEXT NOT NULL
);
"""


class RegulatoryUpdateStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # -- регистрация источника --------------------------------------------

    def register_source(self, source_id: str, source_type: SourceType, source_name: str) -> None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT source_type, source_name FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO sources (source_id, source_type, source_name) VALUES (?, ?, ?)",
                    (source_id, source_type.value, source_name),
                )
                conn.commit()
            elif row[0] != source_type.value or row[1] != source_name:
                raise ValueError(
                    f"Источник {source_id!r} уже зарегистрирован с другим типом/названием: "
                    f"{row[0]!r}/{row[1]!r}, а не {source_type.value!r}/{source_name!r}"
                )

    # -- календарь проверок (не проверять чаще, чем нужно) -----------------

    def record_check(self, source_id: str) -> None:
        """Отмечает, что источник только что был проверен — вне зависимости
        от того, нашлось ли обновление. Источник может быть ещё не
        зарегистрирован (`register_source()`) на момент первой проверки —
        `check_log` не ссылается на `sources`, чтобы не требовать порядка
        вызовов."""
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO check_log (source_id, last_checked_at) VALUES (?, datetime('now')) "
                "ON CONFLICT(source_id) DO UPDATE SET last_checked_at = excluded.last_checked_at",
                (source_id,),
            )
            conn.commit()

    def last_checked_at(self, source_id: str) -> datetime | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT last_checked_at FROM check_log WHERE source_id = ?", (source_id,)
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)

    def is_due_for_check(self, source_id: str, min_interval_days: int) -> bool:
        """Источник, который никогда не проверялся, всегда due — календарь
        отвечает только за то, чтобы не бить по источнику чаще нужного, не
        за то, пропускать ли первую проверку. Законодательство и налоговые
        ставки меняются редко (`legislation_watch.py`) — квартальный/годовой
        интервал, не ежедневный, по аналогии с квартальным источником
        Агента 4 (`smeta_estimator.version_watch`), у которого интервал
        решает вызывающий скрипт по факту частоты запуска cron, здесь же —
        сам календарь, потому что законодательство меняется ещё реже, чем
        квартальные цены."""
        last_checked = self.last_checked_at(source_id)
        if last_checked is None:
            return True
        return datetime.now(timezone.utc) - last_checked >= timedelta(days=min_interval_days)

    # -- применённая версия --------------------------------------------------

    def get_applied_version(self, source_id: str) -> RegulatoryVersion | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT version_label, published_at, items_json FROM applied_versions "
                "WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return RegulatoryVersion(
            source_id=source_id,
            version_label=row[0],
            published_at=date.fromisoformat(row[1]),
            items=json.loads(row[2]),
        )

    # -- обнаружение новой версии ---------------------------------------------

    def detect_update(
        self, source_type: SourceType, source_name: str, candidate: RegulatoryVersion
    ) -> PendingUpdate | None:
        """Сравнивает `candidate` с применённой версией и, если есть разница,
        создаёт запись `PendingUpdate` со статусом PENDING. Применённая
        версия при этом не меняется — см. docstring модуля.

        Возвращает `None`, если `candidate` совпадает с уже применённой
        версией (нечего обнаруживать). Если такая же версия уже была
        обнаружена раньше и ждёт решения эксперта, возвращает существующую
        запись вместо создания дубликата.
        """
        self.register_source(candidate.source_id, source_type, source_name)
        applied = self.get_applied_version(candidate.source_id)
        if applied is not None and applied.version_label == candidate.version_label and applied.items == candidate.items:
            return None

        existing = self._find_pending_by_version(candidate.source_id, candidate.version_label)
        if existing is not None:
            return existing

        changes = build_diff(applied.items if applied else None, candidate.items)
        diff = UpdateDiff(
            source_id=candidate.source_id,
            from_version_label=applied.version_label if applied else None,
            to_version_label=candidate.version_label,
            changes=changes,
        )
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO pending_updates "
                "(source_id, version_label, published_at, items_json, diff_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    candidate.source_id,
                    candidate.version_label,
                    candidate.published_at.isoformat(),
                    json.dumps(candidate.items, ensure_ascii=False),
                    json.dumps(_diff_to_json(diff), ensure_ascii=False),
                    UpdateStatus.PENDING.value,
                ),
            )
            conn.commit()
            update_id = cursor.lastrowid

        update = self.get_pending_update(update_id)
        assert update is not None
        return update

    def _find_pending_by_version(self, source_id: str, version_label: str) -> PendingUpdate | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id FROM pending_updates WHERE source_id = ? AND version_label = ? "
                "AND status = ?",
                (source_id, version_label, UpdateStatus.PENDING.value),
            ).fetchone()
        return self.get_pending_update(row[0]) if row else None

    # -- решение эксперта и применение ----------------------------------------

    def approve_update(
        self, update_id: int, reviewer: str, approved: bool, reason: str = ""
    ) -> PendingUpdate:
        """Единственный способ решить судьбу обнаруженной версии.

        `approved=True` переводит запись в APPROVED и сразу применяет версию
        (см. `apply_update()`). `approved=False` требует непустой `reason` —
        причина отклонения фиксируется в самой записи (по аналогии с логом
        расхождений Агента 4), применённая версия не меняется.
        """
        update = self.get_pending_update(update_id)
        if update is None:
            raise ValueError(f"Обновление с id={update_id} не найдено")
        if update.status != UpdateStatus.PENDING:
            raise ValueError(
                f"Решение по обновлению {update_id} уже принято ранее: {update.status.value}"
            )
        if not reviewer.strip():
            raise ValueError(
                "Подтверждение обязательно должно быть привязано к конкретному эксперту — "
                "reviewer не может быть пустым"
            )
        if not approved and not reason.strip():
            raise ValueError("Отклонение обновления обязательно должно сопровождаться причиной")

        new_status = UpdateStatus.APPROVED if approved else UpdateStatus.REJECTED
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE pending_updates SET status = ?, reviewer = ?, decision_reason = ?, "
                "decided_at = datetime('now') WHERE id = ?",
                (new_status.value, reviewer, reason, update_id),
            )
            conn.commit()

        if approved:
            self.apply_update(update_id)

        result = self.get_pending_update(update_id)
        assert result is not None
        return result

    def apply_update(self, update_id: int) -> RegulatoryVersion:
        """Записывает версию обновления как применённую. Работает только для
        записи со статусом APPROVED — применить необнаружанное экспертом
        обновление напрямую нельзя, так протокол проекта запрещает
        автоматическое применение обновлений нормативной базы."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT source_id, version_label, published_at, items_json, status "
                "FROM pending_updates WHERE id = ?",
                (update_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Обновление с id={update_id} не найдено")
        source_id, version_label, published_at, items_json, status = row
        if status != UpdateStatus.APPROVED.value:
            raise ValueError(
                "Применить можно только обновление, подтверждённое экспертом через "
                f"approve_update(..., approved=True) — сейчас статус: {status!r}"
            )

        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO applied_versions (source_id, version_label, published_at, items_json, applied_at) "
                "VALUES (?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "version_label = excluded.version_label, "
                "published_at = excluded.published_at, "
                "items_json = excluded.items_json, "
                "applied_at = excluded.applied_at",
                (source_id, version_label, published_at, items_json),
            )
            conn.commit()

        return RegulatoryVersion(
            source_id=source_id,
            version_label=version_label,
            published_at=date.fromisoformat(published_at),
            items=json.loads(items_json),
        )

    # -- чтение очереди обновлений --------------------------------------------

    def get_pending_update(self, update_id: int) -> PendingUpdate | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT pu.id, pu.source_id, s.source_type, s.source_name, pu.version_label, "
                "pu.published_at, pu.items_json, pu.diff_json, pu.status, pu.detected_at, "
                "pu.reviewer, pu.decision_reason, pu.decided_at "
                "FROM pending_updates pu JOIN sources s ON s.source_id = pu.source_id "
                "WHERE pu.id = ?",
                (update_id,),
            ).fetchone()
        return _row_to_pending_update(row) if row else None

    def list_pending(self, source_id: str | None = None) -> list[PendingUpdate]:
        return self._list_by_status(UpdateStatus.PENDING, source_id)

    def list_rejected(self, source_id: str | None = None) -> list[PendingUpdate]:
        """Отклонённые обновления с зафиксированной причиной — для анализа
        того, что эксперт чаще всего не пропускает (по аналогии с
        `quality_control.DiscrepancyLog` у Агента 4)."""
        return self._list_by_status(UpdateStatus.REJECTED, source_id)

    def _list_by_status(self, status: UpdateStatus, source_id: str | None) -> list[PendingUpdate]:
        query = (
            "SELECT pu.id, pu.source_id, s.source_type, s.source_name, pu.version_label, "
            "pu.published_at, pu.items_json, pu.diff_json, pu.status, pu.detected_at, "
            "pu.reviewer, pu.decision_reason, pu.decided_at "
            "FROM pending_updates pu JOIN sources s ON s.source_id = pu.source_id "
            "WHERE pu.status = ?"
        )
        params: list[str] = [status.value]
        if source_id is not None:
            query += " AND pu.source_id = ?"
            params.append(source_id)
        query += " ORDER BY pu.id"
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_pending_update(row) for row in rows]


def _diff_to_json(diff: UpdateDiff) -> dict:
    return {
        "from_version_label": diff.from_version_label,
        "to_version_label": diff.to_version_label,
        "changes": [
            {"key": c.key, "old_value": c.old_value, "new_value": c.new_value} for c in diff.changes
        ],
    }


def _diff_from_json(source_id: str, raw: dict) -> UpdateDiff:
    return UpdateDiff(
        source_id=source_id,
        from_version_label=raw["from_version_label"],
        to_version_label=raw["to_version_label"],
        changes=[
            ItemChange(key=c["key"], old_value=c["old_value"], new_value=c["new_value"])
            for c in raw["changes"]
        ],
    )


def _row_to_pending_update(row: tuple) -> PendingUpdate:
    (
        update_id,
        source_id,
        source_type,
        source_name,
        version_label,
        published_at,
        items_json,
        diff_json,
        status,
        detected_at,
        reviewer,
        decision_reason,
        decided_at,
    ) = row
    candidate_version = RegulatoryVersion(
        source_id=source_id,
        version_label=version_label,
        published_at=date.fromisoformat(published_at),
        items=json.loads(items_json),
    )
    diff = _diff_from_json(source_id, json.loads(diff_json))
    return PendingUpdate(
        update_id=update_id,
        source_id=source_id,
        source_type=SourceType(source_type),
        source_name=source_name,
        candidate_version=candidate_version,
        diff=diff,
        status=UpdateStatus(status),
        detected_at=datetime.fromisoformat(detected_at),
        reviewer=reviewer,
        decision_reason=decision_reason,
        decided_at=datetime.fromisoformat(decided_at) if decided_at else None,
    )

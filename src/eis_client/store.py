"""Хранилище состояния Монитора (Агент 1): какие даты уже опрошены и что нашлось.

Сервис ЕИС отдаёт документы только за одну конкретную дату за запрос —
чтобы реально «мониторить» закупки, а не дёргать один и тот же день
вручную, нужно помнить, какие даты уже обработаны, и копить найденные
документы для последующих агентов конвейера (Классификатор уже применён
на этапе `EISClient.get_construction_documents`, здесь — просто накопление
результата).

**«Обработана» — привязано к источнику (`source_signature`), не только к
дате, с 2026-09-23.** До этой даты монитор читал реестр КОНТРАКТОВ
(`RGK`/`contract`); теперь по умолчанию — реестр ИЗВЕЩЕНИЙ
(`PRIZ`/`epNotificationEF2020`, см. CLAUDE.md → «Решено») — совсем другой
тип документа с другой структурой. Дата, обработанная под старым
источником, — не то же самое, что обработанная под новым: смешивать их
молча означало бы повторить ту же ошибку, что уже случалась с эхо-ответом
(«0 найдено» из-за поломки, а не из-за отсутствия закупок), только на
уровне источника данных, а не транспорта. Поэтому `is_date_fetched()`/
`last_fetched_date()` теперь ВСЕГДА берут `source_signature` явным
параметром — дата, обработанная под другой подписью, для текущего запроса
не считается обработанной вообще, её нужно опросить заново.

Существующая база (созданная до 2026-09-23, если такая есть) при первом
открытии мигрируется автоматически: все её строки помечаются как
`_LEGACY_SOURCE_SIGNATURE` (`RGK:contract` — это буквально то, что тогда
читал код, не предположение). Дальше `run_monitor.py` сам решает, что
делать со старыми датами под легаси-подписью (см. его докстринг) — это
хранилище только честно их не путает с новыми, само решение не принимает.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .client import ConstructionDocument
from .config import EISConfig

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "monitor.sqlite3"

# То, что код буквально читал ДО перехода на реестр извещений, 2026-09-23 —
# не предположение, факт из истории проекта (см. CLAUDE.md).
_LEGACY_SOURCE_SIGNATURE = "RGK:contract"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetched_dates (
    fetch_date TEXT NOT NULL,
    source_signature TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (fetch_date, source_signature)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_date TEXT NOT NULL,
    source_signature TEXT NOT NULL,
    file_name TEXT NOT NULL,
    archive_url TEXT NOT NULL,
    okpd2_codes TEXT NOT NULL,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(fetch_date, file_name, archive_url)
);
"""


def source_signature(config: EISConfig) -> str:
    """Подпись источника данных для текущей конфигурации — `EIS_SUBSYSTEM_TYPE`
    + `EIS_DOCUMENT_TYPE44` (то, что реально определяет тип и структуру
    документов, которые вернёт ЕИС). Один и тот же формат везде, чтобы
    строки, записанные разными запусками, было можно однозначно сравнивать."""
    return f"{config.subsystem_type}:{config.document_type44}"


@dataclass
class StoredDocument:
    fetch_date: date
    file_name: str
    archive_url: str
    okpd2_codes: list[str]
    source_signature: str


class MonitorStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._migrate_if_needed(conn)
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _migrate_if_needed(self, conn: sqlite3.Connection) -> None:
        """Переносит базу, созданную до появления `source_signature`
        (2026-09-23), в новую схему — не молча, с явной пометкой старых
        строк `_LEGACY_SOURCE_SIGNATURE`, не тихим объединением с новыми."""
        existing_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "fetched_dates" not in existing_tables:
            return  # новая база — обычный CREATE TABLE ниже, мигрировать нечего

        columns = {row[1] for row in conn.execute("PRAGMA table_info(fetched_dates)")}
        if "source_signature" in columns:
            return  # уже на новой схеме

        conn.execute("ALTER TABLE fetched_dates RENAME TO fetched_dates_legacy")
        conn.execute("ALTER TABLE documents RENAME TO documents_legacy")
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO fetched_dates (fetch_date, source_signature, fetched_at) "
            "SELECT fetch_date, ?, fetched_at FROM fetched_dates_legacy",
            (_LEGACY_SOURCE_SIGNATURE,),
        )
        conn.execute(
            "INSERT INTO documents (fetch_date, source_signature, file_name, archive_url, "
            "okpd2_codes, discovered_at) "
            "SELECT fetch_date, ?, file_name, archive_url, okpd2_codes, discovered_at "
            "FROM documents_legacy",
            (_LEGACY_SOURCE_SIGNATURE,),
        )
        conn.execute("DROP TABLE fetched_dates_legacy")
        conn.execute("DROP TABLE documents_legacy")
        conn.commit()

    def is_date_fetched(self, fetch_date: date, source_signature: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM fetched_dates WHERE fetch_date = ? AND source_signature = ?",
                (fetch_date.isoformat(), source_signature),
            ).fetchone()
        return row is not None

    def save_results(
        self, fetch_date: date, documents: list[ConstructionDocument], source_signature: str
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO fetched_dates (fetch_date, source_signature) VALUES (?, ?)",
                (fetch_date.isoformat(), source_signature),
            )
            for doc in documents:
                conn.execute(
                    "INSERT OR IGNORE INTO documents "
                    "(fetch_date, source_signature, file_name, archive_url, okpd2_codes) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        fetch_date.isoformat(),
                        source_signature,
                        doc.file_name,
                        doc.archive_url,
                        ",".join(doc.okpd2_codes),
                    ),
                )
            conn.commit()

    def last_fetched_date(self, source_signature: str) -> date | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT MAX(fetch_date) FROM fetched_dates WHERE source_signature = ?",
                (source_signature,),
            ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def dates_fetched_under_other_signatures(self, current_signature: str) -> dict[str, int]:
        """Сколько дат помечено обработанными под КАЖДОЙ другой подписью
        источника, кроме текущей — чтобы явно предупредить, а не молчать,
        когда источник поменялся (см. докстринг модуля)."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT source_signature, COUNT(*) FROM fetched_dates "
                "WHERE source_signature != ? GROUP BY source_signature",
                (current_signature,),
            ).fetchall()
        return dict(rows)

    def all_documents(self) -> list[StoredDocument]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT fetch_date, file_name, archive_url, okpd2_codes, source_signature "
                "FROM documents ORDER BY fetch_date"
            ).fetchall()
        return [
            StoredDocument(
                fetch_date=date.fromisoformat(row[0]),
                file_name=row[1],
                archive_url=row[2],
                okpd2_codes=row[3].split(",") if row[3] else [],
                source_signature=row[4],
            )
            for row in rows
        ]

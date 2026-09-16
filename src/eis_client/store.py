"""Хранилище состояния Монитора (Агент 1): какие даты уже опрошены и что нашлось.

Сервис ЕИС отдаёт документы только за одну конкретную дату за запрос —
чтобы реально «мониторить» закупки, а не дёргать один и тот же день
вручную, нужно помнить, какие даты уже обработаны, и копить найденные
документы для последующих агентов конвейера (Классификатор уже применён
на этапе `EISClient.get_construction_documents`, здесь — просто накопление
результата).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .client import ConstructionDocument

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "monitor.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetched_dates (
    fetch_date TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_date TEXT NOT NULL,
    file_name TEXT NOT NULL,
    archive_url TEXT NOT NULL,
    okpd2_codes TEXT NOT NULL,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(fetch_date, file_name, archive_url)
);
"""


@dataclass
class StoredDocument:
    fetch_date: date
    file_name: str
    archive_url: str
    okpd2_codes: list[str]


class MonitorStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def is_date_fetched(self, fetch_date: date) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM fetched_dates WHERE fetch_date = ?", (fetch_date.isoformat(),)
            ).fetchone()
        return row is not None

    def save_results(self, fetch_date: date, documents: list[ConstructionDocument]) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO fetched_dates (fetch_date) VALUES (?)",
                (fetch_date.isoformat(),),
            )
            for doc in documents:
                conn.execute(
                    "INSERT OR IGNORE INTO documents "
                    "(fetch_date, file_name, archive_url, okpd2_codes) VALUES (?, ?, ?, ?)",
                    (
                        fetch_date.isoformat(),
                        doc.file_name,
                        doc.archive_url,
                        ",".join(doc.okpd2_codes),
                    ),
                )
            conn.commit()

    def last_fetched_date(self) -> date | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT MAX(fetch_date) FROM fetched_dates").fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def all_documents(self) -> list[StoredDocument]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT fetch_date, file_name, archive_url, okpd2_codes FROM documents "
                "ORDER BY fetch_date"
            ).fetchall()
        return [
            StoredDocument(
                fetch_date=date.fromisoformat(row[0]),
                file_name=row[1],
                archive_url=row[2],
                okpd2_codes=row[3].split(",") if row[3] else [],
            )
            for row in rows
        ]

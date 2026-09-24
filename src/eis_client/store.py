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

from classifier.tender import Tender

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

CREATE TABLE IF NOT EXISTS tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_date TEXT NOT NULL,
    purchase_number TEXT NOT NULL,
    name TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    okpd2_code TEXT NOT NULL,
    region_code TEXT NOT NULL,
    max_price REAL NOT NULL,
    requires_sro INTEGER NOT NULL,
    min_experience_years INTEGER NOT NULL,
    publish_date TEXT NOT NULL,
    submission_deadline TEXT NOT NULL,
    sro_experience_verified INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(purchase_number)
);
"""


@dataclass
class StoredDocument:
    fetch_date: date
    file_name: str
    archive_url: str
    okpd2_codes: list[str]


@dataclass
class StoredTender:
    """`Tender`, построенный `notice_document_to_tender()` и сохранённый
    Монитором. `sro_experience_verified` — честный флаг, не часть `Tender`:
    `requires_sro`/`min_experience_years` не извлекаются из извещения (см.
    CLAUDE.md, «Известные пробелы», п.13) — пока это не изменится, флаг
    всегда `False`, и любой код, читающий `all_tenders()`, обязан считать
    эти два поля непроверенным предположением (сейчас — False/0), а не
    подтверждённым требованием закупки."""

    fetch_date: date
    tender: Tender
    sro_experience_verified: bool


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

    def save_tenders(
        self, fetch_date: date, tenders: list[Tender], *, sro_experience_verified: bool = False
    ) -> None:
        """Сохраняет `Tender`-ы, построенные `notice_document_to_tender()` за
        `fetch_date` (обычно — Агентом 1, `run_monitor.py`). `purchase_number`
        уникален — повторный вызов с тем же номером не создаёт дубликат
        (`INSERT OR IGNORE`), это ожидаемо при повторной обработке дня."""
        with closing(self._connect()) as conn:
            for tender in tenders:
                conn.execute(
                    "INSERT OR IGNORE INTO tenders "
                    "(fetch_date, purchase_number, name, customer_name, okpd2_code, region_code, "
                    "max_price, requires_sro, min_experience_years, publish_date, submission_deadline, "
                    "sro_experience_verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fetch_date.isoformat(),
                        tender.purchase_number,
                        tender.name,
                        tender.customer_name,
                        tender.okpd2_code,
                        tender.region_code,
                        tender.max_price,
                        int(tender.requires_sro),
                        tender.min_experience_years,
                        tender.publish_date.isoformat(),
                        tender.submission_deadline.isoformat(),
                        int(sro_experience_verified),
                    ),
                )
            conn.commit()

    def all_tenders(self) -> list[StoredTender]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT fetch_date, purchase_number, name, customer_name, okpd2_code, region_code, "
                "max_price, requires_sro, min_experience_years, publish_date, submission_deadline, "
                "sro_experience_verified FROM tenders ORDER BY fetch_date"
            ).fetchall()
        return [
            StoredTender(
                fetch_date=date.fromisoformat(row[0]),
                tender=Tender(
                    purchase_number=row[1],
                    name=row[2],
                    customer_name=row[3],
                    okpd2_code=row[4],
                    region_code=row[5],
                    max_price=row[6],
                    requires_sro=bool(row[7]),
                    min_experience_years=row[8],
                    publish_date=date.fromisoformat(row[9]),
                    submission_deadline=date.fromisoformat(row[10]),
                ),
                sro_experience_verified=bool(row[11]),
            )
            for row in rows
        ]

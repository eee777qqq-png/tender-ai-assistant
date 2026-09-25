"""Единый, переиспользуемый след экспертной проверки — ЧЕРНОВИК АРХИТЕКТУРЫ
для обсуждения, 2026-09-25. Пока НЕ подключён ни к одному реальному агенту
(3, 4, 10 продолжают работать как есть, через `audit_readiness.py`/
`discrepancy_log.py` — их логика этим модулем не тронута, как и было
условие задачи). Демонстрация применения — `tests/test_expert_review.py`,
на примере будущего текста Агента 12, не переписывание существующих
агентов.

## Зачем ещё один модуль, если уже есть `audit_readiness.py`/`discrepancy_log.py`

Те два модуля решают вопрос «готов ли агент к выборочному аудиту» (метрика
скользящего окна) и «что чаще всего путает агент» (лог расхождений по
категориям) — это про КАЧЕСТВО агента как статистику. К моменту этой записи
Агенты 3, 4 и 10 подключены к этому протоколу тремя РАЗНЫМИ способами
(`ExtractedRequirements.expert_reviewed` — простой булев флаг;
`RegulatoryUpdateStore` — своя статусная модель PENDING/APPROVED/REJECTED;
`quality_control.AuditReadinessTracker`/`CategorizedDiscrepancyLog` — общая
инфраструктура, но только у Агентов 3 и 4, и то в памяти, не переживает
перезапуск CLI) — сам факт этой задачи и есть подтверждение, что общего
интерфейса на самом деле никогда не было, несмотря на общее название.

Этот модуль решает ДРУГОЙ, ортогональный вопрос — не «насколько агент
молодец», а «что именно эксперт написал бы правильно» — материал для
будущей калибровки/LLM-пересказа (Агент 8, `client_consultant.consultant`,
CLAUDE.md «Известные пробелы» — там же и было решено не делать LLM сейчас,
но зафиксировать решение как отдельный вопрос). Поэтому:

- **Не заменяет** `AuditReadinessTracker`/`DiscrepancyLog` — агент, который
  уже на них завязан (3, 4), может продолжать пользоваться ими как есть, и
  ДОПОЛНИТЕЛЬНО вызывать `ExpertReviewStore.record_review()` тем же самым
  решением эксперта, если решим их подключить — это отдельный, не
  обязательный шаг, не часть этой задачи.
- **Персистентный, а не в памяти** — SQLite, по аналогии с
  `regulatory_updates.RegulatoryUpdateStore`, тем же файлом-паттерном
  (`data/expert_reviews.sqlite3` по умолчанию). Это прямо устраняет
  ограничение, из-за которого у Агента 3 сегодня «окно метрики не
  сохраняется между запусками CLI» (см. CLAUDE.md) — но реализовано здесь
  заново, не путём переделки существующего кода Агента 3.
- **Два типа обратной связи эксперта, не один** — по требованию задачи:
  1. Подтверждение/отклонение с причиной — `ReviewDecision.APPROVED` /
     `REJECTED` + `reason` (обязательна при отклонении) — та же форма, что
     уже есть у Агента 10 (`RegulatoryUpdateStore.approve_update()`), здесь
     просто обобщена на произвольный агент/тип документа.
  2. **НОВОЕ** — `corrected_output: str | None` — прямая правка эксперта,
     когда он не просто отклоняет, а вписывает исправленный вариант. Не
     привязана жёстко к `REJECTED`: эксперт может исправить текст и всё
     равно одобрить исправленную версию (`APPROVED` + `corrected_output`
     заполнен — «вот что реально ушло дальше»), либо отклонить оригинал, но
     оставить образец на будущее, ничего не пуская дальше по конвейеру
     прямо сейчас (`REJECTED` + `corrected_output` заполнен — учебный
     пример, не решение по этому конкретному случаю).
  3. `corrected_examples()` — отдельная выборка только тех записей, где
     `corrected_output is not None`: это и есть будущий обучающий набор,
     не вперемешку с обычным логом решений.

## Поля записи — по прямому списку из задачи, плюс одно предложенное

- `agent_name` — какой агент (тот же принцип именования, что уже
  используется: `"agent_3_document_analyst"`, `"agent_4_smeta_estimator"`).
- **`document_type`** — предложено дополнительно, не было в исходном
  списке задачи: подтип внутри агента (например, у будущего Агента 12 это
  может быть `"client_summary_legal_notice"` или
  `"contract_offer_liability_clause"` — разные виды текста с разным
  назначением и разной аудиторией). Без этого поля обучающий набор для
  калибровки смешивал бы принципиально разные по форме тексты одного
  агента в одну кучу. Если это избыточно — можно убрать и сворачивать в
  `check_id` или `agent_name`, выносится на обсуждение отдельно.
- `check_id` — какой конкретно случай (номер закупки, id документа и т.п.)
  — для трассировки к первоисточнику, не для группировки.
- `original_output` — что выдал агент, снимок на момент проверки (текст;
  если у агента выдача структурная — сериализовать вызывающей стороной,
  этот модуль не знает про формы данных конкретных агентов, намеренно).
- решение эксперта — `decision`/`reviewer`/`reason` (п.1 выше).
- `corrected_output` — правка эксперта, если есть (п.2 выше).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "expert_reviews.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS expert_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    document_type TEXT NOT NULL,
    check_id TEXT NOT NULL,
    original_output TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    corrected_output TEXT,
    reviewed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class ReviewDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ExpertReview:
    review_id: int
    agent_name: str
    document_type: str
    check_id: str
    original_output: str
    decision: ReviewDecision
    reviewer: str
    reason: str
    corrected_output: str | None
    reviewed_at: datetime

    @property
    def is_correction_example(self) -> bool:
        """Есть ли у этой записи материал для будущей калибровки — не
        зависит от `decision` (см. докстринг модуля, п.2)."""
        return self.corrected_output is not None


class ExpertReviewStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def record_review(
        self,
        agent_name: str,
        document_type: str,
        check_id: str,
        original_output: str,
        decision: ReviewDecision,
        reviewer: str,
        reason: str = "",
        corrected_output: str | None = None,
    ) -> ExpertReview:
        if not reviewer.strip():
            raise ValueError("Проверка обязательно должна быть привязана к конкретному эксперту")
        if decision == ReviewDecision.REJECTED and not reason.strip():
            raise ValueError("Отклонение обязательно должно сопровождаться причиной")

        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO expert_reviews "
                "(agent_name, document_type, check_id, original_output, decision, reviewer, "
                "reason, corrected_output) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_name,
                    document_type,
                    check_id,
                    original_output,
                    decision.value,
                    reviewer,
                    reason,
                    corrected_output,
                ),
            )
            conn.commit()
            review_id = cursor.lastrowid

        review = self.get_review(review_id)
        assert review is not None
        return review

    def get_review(self, review_id: int) -> ExpertReview | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, agent_name, document_type, check_id, original_output, decision, "
                "reviewer, reason, corrected_output, reviewed_at FROM expert_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
        return _row_to_review(row) if row else None

    def for_agent(self, agent_name: str) -> list[ExpertReview]:
        return self._query("WHERE agent_name = ?", (agent_name,))

    def for_document_type(self, agent_name: str, document_type: str) -> list[ExpertReview]:
        return self._query(
            "WHERE agent_name = ? AND document_type = ?", (agent_name, document_type)
        )

    def for_check(self, agent_name: str, check_id: str) -> list[ExpertReview]:
        return self._query("WHERE agent_name = ? AND check_id = ?", (agent_name, check_id))

    def corrected_examples(
        self, agent_name: str, document_type: str | None = None
    ) -> list[ExpertReview]:
        """Только записи с реальной правкой эксперта — будущий обучающий
        набор для калибровки/LLM-пересказа, не вперемешку с обычным логом
        решений (см. докстринг модуля, п.3)."""
        if document_type is None:
            return self._query(
                "WHERE agent_name = ? AND corrected_output IS NOT NULL", (agent_name,)
            )
        return self._query(
            "WHERE agent_name = ? AND document_type = ? AND corrected_output IS NOT NULL",
            (agent_name, document_type),
        )

    def _query(self, where_clause: str, params: tuple) -> list[ExpertReview]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, agent_name, document_type, check_id, original_output, decision, "
                f"reviewer, reason, corrected_output, reviewed_at FROM expert_reviews {where_clause} "
                "ORDER BY id",
                params,
            ).fetchall()
        return [_row_to_review(row) for row in rows]


def _row_to_review(row: tuple) -> ExpertReview:
    (
        review_id,
        agent_name,
        document_type,
        check_id,
        original_output,
        decision,
        reviewer,
        reason,
        corrected_output,
        reviewed_at,
    ) = row
    return ExpertReview(
        review_id=review_id,
        agent_name=agent_name,
        document_type=document_type,
        check_id=check_id,
        original_output=original_output,
        decision=ReviewDecision(decision),
        reviewer=reviewer,
        reason=reason,
        corrected_output=corrected_output,
        reviewed_at=datetime.fromisoformat(reviewed_at),
    )

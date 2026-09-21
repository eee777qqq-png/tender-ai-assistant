"""Очередь эксперта-проверяющего — простой интерактивный CLI, не сайт.

Собирает в одном месте всё, что по протоколу контроля качества (CLAUDE.md)
не может уйти дальше по конвейеру без подтверждения человека, и позволяет
Edwin ответить да/нет по каждому пункту прямо в терминале, не читая и не
трогая код руками:

- **Агент 10 (Парсер нормативки)** — реальная очередь: все записи со
  статусом `PENDING` из `RegulatoryUpdateStore` (обнаруженные, но ещё не
  применённые версии базы расценок/законодательства). Подтверждение здесь —
  боевое: `y` реально вызывает `approve_update(..., approved=True)` и
  применяет версию, `n` требует причину и отклоняет её по-настоящему.
- **Агент 3 (Аналитик документации)** — учебный пример по источнику
  документов, не реальная очередь: у Агента 3 пока нет постоянного
  хранилища необработанных результатов (см. CLAUDE.md, «Известные
  пробелы»), поэтому здесь показываются три придуманных тестовых документа
  из `sample_documents.py`, прогнанные через `extract_requirements()`
  "вживую" при каждом запуске. Само решение эксперта, однако, реально
  подключено к общей инфраструктуре контроля качества — `y`/`n` вызывают
  `document_analyst.review_document_analysis()`, которая прогоняет решение
  через `AuditReadinessTracker`/`CategorizedDiscrepancyLog`, как и у
  Агента 4 (`smeta_estimator.matcher.review_match_result`), а не только
  выставляют `expert_reviewed` в памяти. Сама метрика (окно из 50 проверок)
  не сохраняется между запусками CLI — только внутри одного запуска, тоже
  из-за отсутствия постоянного хранилища у Агента 3.

Использование:
    python src/review_queue.py
    python src/review_queue.py --skip-agent3-samples
    python src/review_queue.py --reviewer Edwin --db data/regulatory_updates.sqlite3
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from document_analyst import AGENT_NAME as DOCUMENT_ANALYST_AGENT_NAME
from document_analyst import ExtractedRequirements, extract_requirements, review_document_analysis
from document_analyst.sample_documents import SAMPLE_DOCUMENTS
from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog, DiscrepancyCategory
from regulatory_updates import PendingUpdate, RegulatoryUpdateStore

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


@dataclass
class ReviewItem:
    """Один пункт очереди — независимо от того, откуда он взялся."""

    section: str  # заголовок раздела ("Агент 10: ..." / "Агент 3: ...")
    title: str  # короткая строка-идентификатор пункта
    body_lines: list[str]  # человеко-читаемая сводка + diff, готовая к печати
    approve: Callable[[str], None]  # reviewer -> применить решение "подтверждено"
    reject: Callable[[str, str], None]  # reviewer, reason -> применить решение "отклонено"


# -- Агент 10: обновления нормативики -----------------------------------------


def build_regulatory_update_items(store: RegulatoryUpdateStore) -> list[ReviewItem]:
    return [_regulatory_update_item(store, update) for update in store.list_pending()]


def _regulatory_update_item(store: RegulatoryUpdateStore, update: PendingUpdate) -> ReviewItem:
    diff = update.diff
    lines = [
        f"Тип источника: {update.source_type.value}",
        f"Источник: {update.source_name}",
        f"Версия: {diff.from_version_label or '(применённой версии ещё не было)'} -> {diff.to_version_label}",
        "",
        "Что изменилось:",
    ]
    if diff.is_empty():
        lines.append("  (все пункты совпадают с применённой версией)")
    for change in diff.changes:
        if change.is_new_item:
            lines.append(f"  + {change.key}: (не было) -> {change.new_value}")
        elif change.is_removed_item:
            lines.append(f"  - {change.key}: {change.old_value} -> (пункт убран)")
        else:
            lines.append(f"  * {change.key}: {change.old_value} -> {change.new_value}")

    def approve(reviewer: str) -> None:
        store.approve_update(update.update_id, reviewer=reviewer, approved=True)

    def reject(reviewer: str, reason: str) -> None:
        store.approve_update(update.update_id, reviewer=reviewer, approved=False, reason=reason)

    return ReviewItem(
        section="Агент 10 — обновление нормативной базы (реальная очередь)",
        title=f"{update.source_name}: {diff.to_version_label}",
        body_lines=lines,
        approve=approve,
        reject=reject,
    )


# -- Агент 3: извлечённые требования -------------------------------------------


def build_document_analyst_items(
    documents: dict[str, str], tracker: AuditReadinessTracker, discrepancy_log: CategorizedDiscrepancyLog
) -> list[ReviewItem]:
    return [
        _document_analyst_item(extract_requirements(purchase_number, text), tracker, discrepancy_log)
        for purchase_number, text in documents.items()
    ]


def _document_analyst_item(
    extracted: ExtractedRequirements, tracker: AuditReadinessTracker, discrepancy_log: CategorizedDiscrepancyLog
) -> ReviewItem:
    lines = [f"Закупка: {extracted.tender_purchase_number}"]

    t = extracted.timeline
    if t.submission_deadline or t.performance_start or t.performance_end:
        lines.append(
            f"Сроки: подача до {t.submission_deadline}, "
            f"исполнение {t.performance_start} - {t.performance_end}"
        )

    for req in extracted.security_requirements:
        percent = f"{req.percentage}%" if req.percentage is not None else "?"
        amount = f"{req.amount:,.0f} руб.".replace(",", " ") if req.amount is not None else "?"
        lines.append(f"Обеспечение ({req.kind}): {percent} / {amount} — «{req.raw_text}»")

    for req in extracted.participant_requirements:
        lines.append(f"Требование к участнику: {req.description}")

    if extracted.hidden_risks:
        lines.append("Скрытые риски:")
        for risk in extracted.hidden_risks:
            lines.append(f"  [{risk.category.value}] {risk.explanation} — «{risk.excerpt}»")
    else:
        lines.append("Скрытых рисков не найдено.")

    def approve(reviewer: str) -> None:
        review_document_analysis(
            extracted, reviewer=reviewer, tracker=tracker, discrepancy_log=discrepancy_log, approved=True
        )

    def reject(reviewer: str, reason: str) -> None:
        review_document_analysis(
            extracted,
            reviewer=reviewer,
            tracker=tracker,
            discrepancy_log=discrepancy_log,
            approved=False,
            category=DiscrepancyCategory.SIGNIFICANT,
            issue="эксперт отклонил выдачу Агента 3 целиком",
            expert_comment=reason,
        )
        print(
            f"  (Отклонение прогнано через метрику готовности и лог расхождений Агента 3, "
            f"но не сохраняется между запусками CLI — у Агента 3 пока нет постоянного "
            f"хранилища результатов, см. CLAUDE.md, «Известные пробелы». Причина: {reviewer} — {reason})"
        )

    return ReviewItem(
        section=(
            "Агент 3 — извлечённые требования (документы тестовые, решение эксперта реально "
            "прогоняется через контроль качества — см. пояснение в шапке)"
        ),
        title=f"Закупка {extracted.tender_purchase_number}",
        body_lines=lines,
        approve=approve,
        reject=reject,
    )


# -- интерактивный цикл --------------------------------------------------------


def ask_decision(item: ReviewItem, reviewer: str) -> None:
    print("=" * 78)
    print(item.section)
    print(item.title)
    print("-" * 78)
    for line in item.body_lines:
        print(line)
    print()

    while True:
        answer = input("Подтвердить? [y/n]: ").strip().lower()
        if answer in ("y", "yes", "д", "да"):
            item.approve(reviewer)
            print("-> Подтверждено.\n")
            return
        if answer in ("n", "no", "н", "нет"):
            reason = input("Причина отклонения (обязательно): ").strip()
            while not reason:
                reason = input("Причина не может быть пустой. Причина отклонения: ").strip()
            item.reject(reviewer, reason)
            print("-> Отклонено.\n")
            return
        print("Не понял ответ — введите y/да или n/нет.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db", type=Path, default=None, help="Путь к SQLite Агента 10 (по умолчанию data/regulatory_updates.sqlite3)"
    )
    parser.add_argument("--reviewer", default=None, help="Имя эксперта для журнала (если не задано — спросит)")
    parser.add_argument(
        "--skip-agent3-samples",
        action="store_true",
        help="Не показывать тестовые примеры Агента 3 — только реальную очередь Агента 10",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    print("Очередь эксперта-проверяющего\n")
    reviewer = args.reviewer or input("Кто проверяет? [Edwin]: ").strip() or "Edwin"
    print()

    store = RegulatoryUpdateStore(args.db)
    document_analyst_tracker = AuditReadinessTracker(DOCUMENT_ANALYST_AGENT_NAME)
    document_analyst_log = CategorizedDiscrepancyLog()

    items = build_regulatory_update_items(store)
    if not args.skip_agent3_samples:
        items += build_document_analyst_items(SAMPLE_DOCUMENTS, document_analyst_tracker, document_analyst_log)

    if not items:
        print("Очередь пуста — проверять нечего.")
        return 0

    print(f"В очереди {len(items)} пункт(ов).\n")
    for item in items:
        ask_decision(item, reviewer)

    if not args.skip_agent3_samples:
        stats = document_analyst_tracker.window_stats()
        print(
            f"Агент 3 за этот запуск: {stats['total']} проверок, "
            f"без существенной корректировки {stats['ok_ratio']:.0%}, "
            f"расхождений залогировано {len(document_analyst_log.for_agent(DOCUMENT_ANALYST_AGENT_NAME))} "
            "(окно метрики не сохраняется между запусками, см. CLAUDE.md)."
        )

    print("Очередь обработана.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
  **С 2026-09-25 сюда же, в тот же `RegulatoryUpdateStore`, попадают и
  находки Агента 4** — `src/check_smeta_data_versions.py`
  (`smeta_estimator.version_watch`) периодически проверяет, не вышла ли
  новая версия архива ФСНБ-2022 или новый квартал цен ФГИС ЦС, и точно так
  же создаёт `PENDING`-запись вместо автоматического применения; этот файл
  не нужно было менять для этого — очередь уже читает всё из одного стора.
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
- **Агент 12 (демо нового `ExpertReviewStore`)** — тоже учебный пример, не
  реальная очередь: показывает `legal_boundaries.CLIENT_SUMMARY_LEGAL_NOTICE`
  (единственный реальный текст Агента 12 на сегодня) как пример проверки
  через `quality_control.ExpertReviewStore` — черновик архитектуры,
  набросанный 2026-09-25 для будущих текстов Агента 12 (CLAUDE.md,
  «Известные пробелы»), пока НЕ подключённый ни к одному агенту по-настоящему.
  Решение здесь реально пишется в SQLite (`data/expert_reviews.sqlite3` по
  умолчанию), в отличие от примера Агента 3 — но это по-прежнему только
  демонстрация одного текста, не полноценная очередь по всем закупкам.
  У этого пункта, в отличие от Агента 3/10, есть третий вариант ответа —
  `e` (внести правку): вместо ввода многострочного текста прямо в
  консоли (`input()` для этого не годится) скрипт сохраняет исходный текст
  во временный файл `data/review_draft_<id>.txt`, эксперт редактирует его в
  любом текстовом редакторе (Блокнот и т. п.), сохраняет, возвращается в
  консоль и подтверждает — скрипт читает файл обратно и сохраняет его
  содержимое как `corrected_output`.

Использование:
    python src/review_queue.py
    python src/review_queue.py --skip-agent3-samples
    python src/review_queue.py --show-agent12-demo
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
from legal_boundaries import CLIENT_SUMMARY_LEGAL_NOTICE
from quality_control import (
    AuditReadinessTracker,
    CategorizedDiscrepancyLog,
    DiscrepancyCategory,
    ExpertReviewStore,
    ReviewDecision,
)
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
    # НОВОЕ, необязательное — только у пунктов, поддерживающих правку через
    # файл (сейчас только демо Агента 12, см. `_agent12_demo_item()`).
    # Агенты 10/3 их не заполняют — для них поведение `ask_decision()`
    # не отличается от того, что было до этой правки.
    correction_draft_id: str | None = None  # id для имени файла-черновика
    original_text_for_edit: str | None = None  # что записать в файл-черновик
    submit_with_correction: (
        Callable[[str, ReviewDecision, str, str], None] | None
    ) = None  # reviewer, decision, reason, corrected_text -> None


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


# -- Агент 12: демо нового ExpertReviewStore (черновик, не подключён к агенту) -


AGENT_12_NAME = "agent_12_legal_boundaries"
AGENT_12_DOCUMENT_TYPE = "client_summary_legal_notice"
AGENT_12_DEMO_CHECK_ID = "demo-client-summary-notice"


def build_agent12_demo_items(store: ExpertReviewStore) -> list[ReviewItem]:
    return [_agent12_demo_item(store)]


def _agent12_demo_item(store: ExpertReviewStore) -> ReviewItem:
    lines = [
        "Единственный реальный текст Агента 12 на сегодня — юридическое",
        "предупреждение, показываемое в сводке Агента 8:",
        "",
        *CLIENT_SUMMARY_LEGAL_NOTICE.splitlines(),
        "",
        "Это демонстрация черновика ExpertReviewStore (CLAUDE.md, «Известные",
        "пробелы»), не реальная очередь по закупкам — у будущих текстов",
        "Агента 12, зависящих от конкретного случая, пока нет ни генерации,",
        "ни хранилища необработанных результатов.",
    ]

    def approve(reviewer: str) -> None:
        store.record_review(
            agent_name=AGENT_12_NAME,
            document_type=AGENT_12_DOCUMENT_TYPE,
            check_id=AGENT_12_DEMO_CHECK_ID,
            original_output=CLIENT_SUMMARY_LEGAL_NOTICE,
            decision=ReviewDecision.APPROVED,
            reviewer=reviewer,
        )

    def reject(reviewer: str, reason: str) -> None:
        store.record_review(
            agent_name=AGENT_12_NAME,
            document_type=AGENT_12_DOCUMENT_TYPE,
            check_id=AGENT_12_DEMO_CHECK_ID,
            original_output=CLIENT_SUMMARY_LEGAL_NOTICE,
            decision=ReviewDecision.REJECTED,
            reviewer=reviewer,
            reason=reason,
        )

    def submit_with_correction(
        reviewer: str, decision: ReviewDecision, reason: str, corrected_text: str
    ) -> None:
        store.record_review(
            agent_name=AGENT_12_NAME,
            document_type=AGENT_12_DOCUMENT_TYPE,
            check_id=AGENT_12_DEMO_CHECK_ID,
            original_output=CLIENT_SUMMARY_LEGAL_NOTICE,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            corrected_output=corrected_text,
        )

    return ReviewItem(
        section="Агент 12 — демо ExpertReviewStore (черновик архитектуры, не реальная очередь)",
        title="Юридическое уведомление в сводке клиенту",
        body_lines=lines,
        approve=approve,
        reject=reject,
        correction_draft_id=AGENT_12_DEMO_CHECK_ID,
        original_text_for_edit=CLIENT_SUMMARY_LEGAL_NOTICE,
        submit_with_correction=submit_with_correction,
    )


# -- интерактивный цикл --------------------------------------------------------


def _ask_reason(prompt: str = "Причина отклонения (обязательно): ") -> str:
    reason = input(prompt).strip()
    while not reason:
        reason = input("Причина не может быть пустой. " + prompt).strip()
    return reason


def _run_correction_flow(item: ReviewItem, reviewer: str, draft_dir: Path) -> None:
    """Правка многострочного текста через файл, не через `input()` в
    терминале — см. докстринг модуля. Пишет исходный текст во временный
    файл, ждёт, пока эксперт его отредактирует и сохранит в любом текстовом
    редакторе, затем читает файл обратно как `corrected_output` и спрашивает,
    одобрить ли исправленную версию или отклонить оригинал, оставив её
    только образцом на будущее."""
    assert item.submit_with_correction is not None
    assert item.original_text_for_edit is not None
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"review_draft_{item.correction_draft_id}.txt"
    draft_path.write_text(item.original_text_for_edit, encoding="utf-8")

    print(f"\nФайл для правки создан: {draft_path}")
    print("Откройте его в любом текстовом редакторе (например, Блокнот), отредактируйте")
    print("и сохраните файл. Когда закончите, вернитесь сюда.")
    input("Нажмите Enter, когда файл сохранён и готов... ")

    corrected_text = draft_path.read_text(encoding="utf-8")

    while True:
        sub_answer = input(
            "Одобрить исправленную версию [y] или отклонить оригинал, сохранив "
            "правку как образец на будущее [n]?: "
        ).strip().lower()
        if sub_answer in ("y", "yes", "д", "да"):
            item.submit_with_correction(reviewer, ReviewDecision.APPROVED, "", corrected_text)
            print(f"-> Одобрена исправленная версия (текст из {draft_path}).\n")
            return
        if sub_answer in ("n", "no", "н", "нет"):
            reason = _ask_reason()
            item.submit_with_correction(reviewer, ReviewDecision.REJECTED, reason, corrected_text)
            print(f"-> Оригинал отклонён, правка сохранена как образец (текст из {draft_path}).\n")
            return
        print("Не понял ответ — введите y/да или n/нет.")


def ask_decision(item: ReviewItem, reviewer: str, draft_dir: Path) -> None:
    print("=" * 78)
    print(item.section)
    print(item.title)
    print("-" * 78)
    for line in item.body_lines:
        print(line)
    print()

    supports_correction = item.submit_with_correction is not None
    prompt = (
        "Подтвердить как есть [y], отклонить [n], внести правку [e]: "
        if supports_correction
        else "Подтвердить? [y/n]: "
    )

    while True:
        answer = input(prompt).strip().lower()
        if answer in ("y", "yes", "д", "да"):
            item.approve(reviewer)
            print("-> Подтверждено.\n")
            return
        if answer in ("n", "no", "н", "нет"):
            reason = _ask_reason()
            item.reject(reviewer, reason)
            print("-> Отклонено.\n")
            return
        if supports_correction and answer in ("e", "edit", "п", "правка"):
            _run_correction_flow(item, reviewer, draft_dir)
            return
        hint = "y/да, n/нет" + (" или e/правка" if supports_correction else "")
        print(f"Не понял ответ — введите {hint}.")


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
    parser.add_argument(
        "--show-agent12-demo",
        action="store_true",
        help=(
            "Показать демо-пункт Агента 12 (ExpertReviewStore, черновик архитектуры) — "
            "выключено по умолчанию, чтобы не менять поведение существующей очереди"
        ),
    )
    parser.add_argument(
        "--expert-reviews-db",
        type=Path,
        default=None,
        help="Путь к SQLite демо Агента 12 (по умолчанию data/expert_reviews.sqlite3)",
    )
    parser.add_argument(
        "--draft-dir",
        type=Path,
        default=None,
        help="Куда сохранять файлы-черновики для правки (по умолчанию папка data/)",
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
    draft_dir = args.draft_dir or (Path(__file__).resolve().parent.parent / "data")

    items = build_regulatory_update_items(store)
    if not args.skip_agent3_samples:
        items += build_document_analyst_items(SAMPLE_DOCUMENTS, document_analyst_tracker, document_analyst_log)
    if args.show_agent12_demo:
        expert_review_store = ExpertReviewStore(args.expert_reviews_db)
        items += build_agent12_demo_items(expert_review_store)

    if not items:
        print("Очередь пуста — проверять нечего.")
        return 0

    print(f"В очереди {len(items)} пункт(ов).\n")
    for item in items:
        ask_decision(item, reviewer, draft_dir)

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

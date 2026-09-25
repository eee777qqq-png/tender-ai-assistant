"""Демонстрация черновика единого интерфейса экспертной проверки
(`quality_control.expert_review`, см. докстринг модуля за архитектурным
обоснованием) — НЕ подключение к реальным агентам. Агенты 3/4/10
продолжают работать через свои существующие механизмы, не тронуты.

Сценарий ниже — будущий Агент 12 (юрист): сейчас он выдаёт один
фиксированный текст (`legal_boundaries.CLIENT_SUMMARY_LEGAL_NOTICE`), но
как только появится генерация текста, зависящая от конкретного случая
(например, разный текст уведомления в зависимости от того, какие именно
скрытые риски нашёл Агент 3 для этой закупки), потребуется такая же
проверка эксперта, как у Агентов 3/4/10 — этот модуль показывает, как она
выглядела бы, без придумывания несуществующей логики генерации самого
текста."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from legal_boundaries import CLIENT_SUMMARY_LEGAL_NOTICE
from quality_control import ExpertReviewStore, ReviewDecision

AGENT_12_NAME = "agent_12_legal_boundaries"


def make_store(tmp_path) -> ExpertReviewStore:
    return ExpertReviewStore(tmp_path / "expert_reviews.sqlite3")


def test_expert_approves_the_current_fixed_notice_as_is(tmp_path):
    """Простейший случай — эксперт согласен с уже существующим текстом,
    без правок. Соответствует типу обратной связи №1 из задачи."""
    store = make_store(tmp_path)

    review = store.record_review(
        agent_name=AGENT_12_NAME,
        document_type="client_summary_legal_notice",
        check_id="0173200001426000101",
        original_output=CLIENT_SUMMARY_LEGAL_NOTICE,
        decision=ReviewDecision.APPROVED,
        reviewer="Edwin",
    )

    assert review.decision == ReviewDecision.APPROVED
    assert review.corrected_output is None
    assert not review.is_correction_example


def test_expert_rejects_a_hypothetical_generated_notice_with_a_reason(tmp_path):
    """Отклонение без правки, только с причиной — тоже тип №1, теперь
    показан на гипотетическом будущем случае: Агент 12 сгенерировал текст
    под конкретную закупку (не сегодняшняя реальность — Агент 12 сейчас
    выдаёт фиксированный текст, это иллюстрация будущего сценария), и
    эксперт с ним не согласен."""
    store = make_store(tmp_path)
    hypothetical_generated_notice = (
        "Важно: сервис гарантирует полное соответствие всем требованиям закупки "
        "и берёт на себя ответственность за решение об участии."
    )

    review = store.record_review(
        agent_name=AGENT_12_NAME,
        document_type="client_summary_legal_notice",
        check_id="0350200003426000202",
        original_output=hypothetical_generated_notice,
        decision=ReviewDecision.REJECTED,
        reviewer="Edwin",
        reason=(
            "Текст обещает гарантию соответствия и берёт на себя решение об "
            "участии — прямо противоречит протоколу контроля качества "
            "(CLAUDE.md): сервис не гарантирует результат, решение всегда за "
            "собственником."
        ),
    )

    assert review.decision == ReviewDecision.REJECTED
    assert "гарантию" in review.reason
    assert review.corrected_output is None


def test_expert_rejects_and_supplies_a_corrected_version_as_a_training_example(tmp_path):
    """НОВЫЙ тип обратной связи (задача, п.2): эксперт не просто отклоняет,
    а вписывает исправленный вариант — сохраняется отдельно как образец для
    будущей калибровки, не только как причина отказа."""
    store = make_store(tmp_path)
    hypothetical_generated_notice = (
        "Важно: сервис гарантирует полное соответствие всем требованиям закупки "
        "и берёт на себя ответственность за решение об участии."
    )
    corrected_by_expert = (
        "Важно: сервис даёт рекомендации на основе данных, предоставленных "
        "клиентом, и не гарантирует результат. Решение об участии в закупке "
        "всегда принимает собственник бизнеса."
    )

    review = store.record_review(
        agent_name=AGENT_12_NAME,
        document_type="client_summary_legal_notice",
        check_id="0350200003426000202",
        original_output=hypothetical_generated_notice,
        decision=ReviewDecision.REJECTED,
        reviewer="Edwin",
        reason="Текст обещает гарантию — недопустимо, см. протокол контроля качества.",
        corrected_output=corrected_by_expert,
    )

    assert review.is_correction_example
    assert review.corrected_output == corrected_by_expert


def test_expert_can_approve_an_edited_version_not_only_reject(tmp_path):
    """Правка не обязана идти рука об руку с отклонением — эксперт может
    подправить формулировку и одобрить именно исправленную версию, которая
    и уходит дальше по конвейеру."""
    store = make_store(tmp_path)
    minor_wording_issue = CLIENT_SUMMARY_LEGAL_NOTICE.replace(
        "Финальное решение", "Окончательное решение"
    )

    review = store.record_review(
        agent_name=AGENT_12_NAME,
        document_type="client_summary_legal_notice",
        check_id="0123200004426000303",
        original_output=CLIENT_SUMMARY_LEGAL_NOTICE,
        decision=ReviewDecision.APPROVED,
        reviewer="Edwin",
        corrected_output=minor_wording_issue,
    )

    assert review.decision == ReviewDecision.APPROVED
    assert review.is_correction_example


def test_corrected_examples_filters_out_reviews_without_a_correction(tmp_path):
    """`corrected_examples()` — будущий обучающий набор, не общий лог всех
    решений: обычное одобрение без правки в него не попадает."""
    store = make_store(tmp_path)
    store.record_review(
        agent_name=AGENT_12_NAME,
        document_type="client_summary_legal_notice",
        check_id="0173200001426000101",
        original_output=CLIENT_SUMMARY_LEGAL_NOTICE,
        decision=ReviewDecision.APPROVED,
        reviewer="Edwin",
    )
    corrected = store.record_review(
        agent_name=AGENT_12_NAME,
        document_type="client_summary_legal_notice",
        check_id="0350200003426000202",
        original_output="текст с проблемой",
        decision=ReviewDecision.REJECTED,
        reviewer="Edwin",
        reason="неверная формулировка",
        corrected_output="исправленный текст",
    )

    examples = store.corrected_examples(AGENT_12_NAME, document_type="client_summary_legal_notice")

    assert len(examples) == 1
    assert examples[0].review_id == corrected.review_id


def test_document_type_separates_different_kinds_of_output_from_the_same_agent(tmp_path):
    """Один агент может выдавать разные по форме тексты — обучающий набор
    для одного вида не должен смешиваться с другим."""
    store = make_store(tmp_path)
    store.record_review(
        agent_name=AGENT_12_NAME,
        document_type="client_summary_legal_notice",
        check_id="0173200001426000101",
        original_output="текст уведомления в интерфейсе",
        decision=ReviewDecision.REJECTED,
        reviewer="Edwin",
        reason="неточно",
        corrected_output="исправленное уведомление",
    )
    store.record_review(
        agent_name=AGENT_12_NAME,
        document_type="contract_offer_liability_clause",
        check_id="draft-v1",
        original_output="пункт черновика договора-оферты",
        decision=ReviewDecision.REJECTED,
        reviewer="Edwin",
        reason="неточно",
        corrected_output="исправленный пункт договора",
    )

    notice_examples = store.corrected_examples(AGENT_12_NAME, document_type="client_summary_legal_notice")
    clause_examples = store.corrected_examples(AGENT_12_NAME, document_type="contract_offer_liability_clause")
    all_examples = store.corrected_examples(AGENT_12_NAME)

    assert len(notice_examples) == 1
    assert len(clause_examples) == 1
    assert len(all_examples) == 2


def test_rejecting_without_a_reason_is_refused():
    """Тот же протокол, что уже есть у Агента 10 — отклонение без причины
    не проходит, независимо от того, приложена ли правка."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = ExpertReviewStore(Path(tmp) / "expert_reviews.sqlite3")
        with pytest.raises(ValueError, match="причин"):
            store.record_review(
                agent_name=AGENT_12_NAME,
                document_type="client_summary_legal_notice",
                check_id="x",
                original_output="текст",
                decision=ReviewDecision.REJECTED,
                reviewer="Edwin",
            )


def test_reviewing_without_a_named_reviewer_is_refused(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match="эксперту"):
        store.record_review(
            agent_name=AGENT_12_NAME,
            document_type="client_summary_legal_notice",
            check_id="x",
            original_output="текст",
            decision=ReviewDecision.APPROVED,
            reviewer="",
        )


def test_reviews_persist_across_separate_store_instances(tmp_path):
    """В отличие от `AuditReadinessTracker`/`CategorizedDiscrepancyLog` у
    Агента 3 (в памяти, не переживают перезапуск CLI — см. CLAUDE.md),
    `ExpertReviewStore` — SQLite, тот же файл-паттерн, что и
    `RegulatoryUpdateStore` у Агента 10."""
    db_path = tmp_path / "expert_reviews.sqlite3"
    first_process = ExpertReviewStore(db_path)
    first_process.record_review(
        agent_name=AGENT_12_NAME,
        document_type="client_summary_legal_notice",
        check_id="0173200001426000101",
        original_output=CLIENT_SUMMARY_LEGAL_NOTICE,
        decision=ReviewDecision.APPROVED,
        reviewer="Edwin",
    )

    second_process = ExpertReviewStore(db_path)

    assert len(second_process.for_agent(AGENT_12_NAME)) == 1

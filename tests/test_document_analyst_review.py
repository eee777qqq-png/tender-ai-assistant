"""`review_document_analysis()` — подключение Агента 3 к общей
инфраструктуре контроля качества (`quality_control`), тот же протокол, что
уже реально работает у Агента 4 (`smeta_estimator.matcher.
review_match_result`, см. `tests/test_smeta_estimator_matcher.py`).

Вызывается из `src/review_queue.py` (реальный CLI, не только тест) —
см. `tests/test_review_queue.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from document_analyst import AGENT_NAME, extract_requirements, review_document_analysis
from document_analyst.sample_documents import SAMPLE_DOCUMENT_KROVLYA
from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog, DiscrepancyCategory

TENDER_PURCHASE_NUMBER = "0173200001426000101"


def test_approving_as_is_marks_reviewed_and_logs_nothing():
    extracted = extract_requirements(TENDER_PURCHASE_NUMBER, SAMPLE_DOCUMENT_KROVLYA)
    tracker = AuditReadinessTracker(AGENT_NAME)
    log = CategorizedDiscrepancyLog()

    review_document_analysis(extracted, reviewer="Edwin", tracker=tracker, discrepancy_log=log, approved=True)

    assert extracted.expert_reviewed is True
    assert extracted.expert_reviewer == "Edwin"
    assert tracker.window_stats()["total"] == 1
    assert tracker.window_stats()["ok_ratio"] == 1.0
    assert log.for_agent(AGENT_NAME) == []


def test_approving_with_a_correction_marks_reviewed_but_logs_the_discrepancy():
    """Эксперт принимает выдачу, но только после того, как поправил
    что-то содержательное — это должно попасть в лог и снизить ok_ratio,
    а не потеряться молча, как «просто подтверждение»."""
    extracted = extract_requirements(TENDER_PURCHASE_NUMBER, SAMPLE_DOCUMENT_KROVLYA)
    tracker = AuditReadinessTracker(AGENT_NAME)
    log = CategorizedDiscrepancyLog()

    review_document_analysis(
        extracted,
        reviewer="Edwin",
        tracker=tracker,
        discrepancy_log=log,
        approved=True,
        category=DiscrepancyCategory.SIGNIFICANT,
        issue="пропущено требование к опыту в другом разделе документа",
        expert_comment="дополнил вручную",
    )

    assert extracted.expert_reviewed is True
    entries = log.for_agent(AGENT_NAME)
    assert len(entries) == 1
    assert entries[0].issue == "пропущено требование к опыту в другом разделе документа"
    assert entries[0].expert_comment == "дополнил вручную"
    assert tracker.window_stats()["ok_ratio"] == 0.0


def test_rejecting_does_not_mark_reviewed_but_still_records_the_check():
    """Отклонение — не тихий провал: попадает и в лог, и в метрику, но
    expert_reviewed остаётся False, так что дальше по конвейеру (Агент 6/5)
    этим результатом воспользоваться нельзя."""
    extracted = extract_requirements(TENDER_PURCHASE_NUMBER, SAMPLE_DOCUMENT_KROVLYA)
    tracker = AuditReadinessTracker(AGENT_NAME)
    log = CategorizedDiscrepancyLog()

    review_document_analysis(
        extracted,
        reviewer="Edwin",
        tracker=tracker,
        discrepancy_log=log,
        approved=False,
        category=DiscrepancyCategory.CRITICAL,
        issue="сроки исполнения извлечены неверно",
        expert_comment="перепроверить вручную",
    )

    assert extracted.expert_reviewed is False
    entries = log.for_agent(AGENT_NAME)
    assert len(entries) == 1
    assert entries[0].critical is True
    assert tracker.window_stats()["ok_ratio"] == 0.0
    assert tracker.window_stats()["critical_count"] == 1


def test_cosmetic_category_behaves_like_plain_approval():
    """`category=COSMETIC` (по умолчанию) — эксперт согласен по существу,
    правка не содержательная: не считается коррекцией для метрики."""
    extracted = extract_requirements(TENDER_PURCHASE_NUMBER, SAMPLE_DOCUMENT_KROVLYA)
    tracker = AuditReadinessTracker(AGENT_NAME)
    log = CategorizedDiscrepancyLog()

    review_document_analysis(
        extracted,
        reviewer="Edwin",
        tracker=tracker,
        discrepancy_log=log,
        approved=True,
        category=DiscrepancyCategory.COSMETIC,
        issue="опечатка в цитате, не влияет на решение",
    )

    assert tracker.window_stats()["ok_ratio"] == 1.0
    assert log.for_agent(AGENT_NAME) == []

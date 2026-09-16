import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from document_analyst import AGENT_NAME
from quality_control import CategorizedDiscrepancyLog, DiscrepancyCategory


def test_log_and_retrieve_for_agent():
    log = CategorizedDiscrepancyLog()
    log.log(
        AGENT_NAME,
        "check-1",
        DiscrepancyCategory.CRITICAL,
        issue="Не извлечено требование об обеспечении исполнения контракта (15%)",
        expert_comment="Пропуск меняет расчёт финансовой готовности клиента",
    )
    log.log(
        AGENT_NAME,
        "check-1",
        DiscrepancyCategory.COSMETIC,
        issue="Дата подачи заявок извлечена без времени (18:00)",
    )

    entries = log.for_agent(AGENT_NAME)
    assert len(entries) == 2
    assert log.for_check(AGENT_NAME, "check-1")[0].issue.startswith("Не извлечено")


def test_needed_correction_excludes_only_cosmetic():
    log = CategorizedDiscrepancyLog()
    critical = log.log(AGENT_NAME, "check-1", DiscrepancyCategory.CRITICAL, issue="x")
    significant = log.log(AGENT_NAME, "check-2", DiscrepancyCategory.SIGNIFICANT, issue="y")
    cosmetic = log.log(AGENT_NAME, "check-3", DiscrepancyCategory.COSMETIC, issue="z")

    assert critical.needed_correction is True
    assert significant.needed_correction is True
    assert cosmetic.needed_correction is False


def test_critical_count_and_by_category():
    log = CategorizedDiscrepancyLog()
    log.log(AGENT_NAME, "check-1", DiscrepancyCategory.CRITICAL, issue="пропущен скрытый риск")
    log.log(AGENT_NAME, "check-2", DiscrepancyCategory.CRITICAL, issue="неверный срок исполнения")
    log.log(AGENT_NAME, "check-3", DiscrepancyCategory.SIGNIFICANT, issue="неточный процент обеспечения")

    assert log.critical_count(AGENT_NAME) == 2
    assert len(log.by_category(AGENT_NAME, DiscrepancyCategory.SIGNIFICANT)) == 1
    assert log.critical_count("some_other_agent") == 0

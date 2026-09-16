import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from document_analyst import AGENT_NAME, RiskCategory, extract_requirements
from document_analyst.sample_documents import (
    SAMPLE_DOCUMENT_KINDERGARTEN,
    SAMPLE_DOCUMENT_KROVLYA,
    SAMPLE_DOCUMENT_PLASTER,
)
from quality_control import AuditReadinessRegistry, ReviewMode


def test_extracts_timeline_and_security_for_krovlya():
    result = extract_requirements("0173200001426000101", SAMPLE_DOCUMENT_KROVLYA)

    assert result.timeline.submission_deadline == date(2026, 8, 20)
    assert result.timeline.performance_start == date(2026, 9, 1)
    assert result.timeline.performance_end == date(2026, 11, 20)

    bid = result.security_requirement("bid")
    assert bid is not None
    assert bid.percentage == 1.0
    assert bid.amount == 80_000.0

    contract = result.security_requirement("contract")
    assert contract is not None
    assert contract.percentage == 10.0


def test_extracts_participant_requirements_for_krovlya():
    result = extract_requirements("0173200001426000101", SAMPLE_DOCUMENT_KROVLYA)

    descriptions = [r.description for r in result.participant_requirements]
    assert "Требуется членство в СРО" in descriptions
    assert any("не менее 2 лет" in d for d in descriptions)


def test_flags_unilateral_and_uncapped_liability_risks_for_krovlya():
    result = extract_requirements("0173200001426000101", SAMPLE_DOCUMENT_KROVLYA)

    categories = {r.category for r in result.hidden_risks}
    assert categories == {RiskCategory.UNILATERAL_TERMS, RiskCategory.UNCAPPED_LIABILITY}
    assert len(result.hidden_risks) == 2


def test_flags_ambiguous_condition_and_nonstandard_penalty_for_kindergarten():
    result = extract_requirements("0350200003426000202", SAMPLE_DOCUMENT_KINDERGARTEN)

    assert result.timeline.submission_deadline == date(2026, 9, 1)
    assert result.timeline.performance_start == date(2026, 9, 15)
    assert result.timeline.performance_end == date(2027, 6, 30)

    bid = result.security_requirement("bid")
    assert bid.percentage == 0.5
    assert bid.amount == 1_750_000.0

    categories = {r.category for r in result.hidden_risks}
    assert categories == {RiskCategory.AMBIGUOUS_CONDITION, RiskCategory.NONSTANDARD_PENALTY}
    assert len(result.hidden_risks) == 2


def test_plaster_document_has_no_hidden_risks():
    """Контрольный случай: обычные, некритические условия (штрафы с
    ограничением по сумме, без СРО) не должны попадать в список рисков —
    иначе список рисков быстро обесценится ложными срабатываниями."""
    result = extract_requirements("0123200004426000303", SAMPLE_DOCUMENT_PLASTER)

    assert result.hidden_risks == []
    assert result.security_requirement("bid").percentage == 1.0
    assert result.security_requirement("contract").percentage == 5.0
    descriptions = [r.description for r in result.participant_requirements]
    assert "Требуется членство в СРО" not in descriptions
    assert any("не менее 1 лет" in d for d in descriptions)


def test_reuses_shared_audit_readiness_logic_agreed_for_agent_4():
    """Та же логика, что для Агента 4: окно из 50 проверок, переход на
    выборочный аудит только при >=90% без существенной корректировки и без
    критических ошибок. Общий `AuditReadinessTracker` не меняется под
    Агента 3 — берём как есть, по имени агента."""
    registry = AuditReadinessRegistry()
    tracker = registry.get(AGENT_NAME)
    assert tracker.requires_human_review()

    for _ in range(50):
        tracker.record_check(needed_correction=False)

    assert tracker.mode == ReviewMode.SPOT_CHECK
    assert not tracker.requires_human_review()

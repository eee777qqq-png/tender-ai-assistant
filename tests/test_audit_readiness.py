import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quality_control import AuditReadinessRegistry, AuditReadinessTracker, ReviewMode


def test_starts_in_full_expert_review():
    tracker = AuditReadinessTracker("agent_4_smetchik")
    assert tracker.requires_human_review()
    assert tracker.mode == ReviewMode.FULL_EXPERT_REVIEW


def test_promotes_to_spot_check_after_50_good_checks():
    tracker = AuditReadinessTracker("agent_4_smetchik")
    for _ in range(50):
        tracker.record_check(needed_correction=False)

    assert tracker.mode == ReviewMode.SPOT_CHECK
    assert not tracker.requires_human_review()


def test_below_90_percent_ok_ratio_blocks_promotion():
    tracker = AuditReadinessTracker("agent_4_smetchik")
    # 44 без корректировки, 6 с корректировкой = 88% < 90%
    for _ in range(44):
        tracker.record_check(needed_correction=False)
    for _ in range(6):
        tracker.record_check(needed_correction=True)

    assert tracker.mode == ReviewMode.FULL_EXPERT_REVIEW


def test_any_critical_error_in_window_blocks_promotion():
    tracker = AuditReadinessTracker("agent_3_analitik")
    for _ in range(49):
        tracker.record_check(needed_correction=False)
    tracker.record_check(needed_correction=False, critical_error=True)

    assert tracker.mode == ReviewMode.FULL_EXPERT_REVIEW


def test_critical_error_after_promotion_triggers_rollback_and_resets_window():
    tracker = AuditReadinessTracker("agent_6_sborshik")
    for _ in range(50):
        tracker.record_check(needed_correction=False)
    assert tracker.mode == ReviewMode.SPOT_CHECK

    tracker.record_check(needed_correction=False, critical_error=True)

    assert tracker.mode == ReviewMode.FULL_EXPERT_REVIEW
    assert len(tracker.rollback_history) == 1
    assert tracker.rollback_history[0].agent_name == "agent_6_sborshik"
    # окно очищено до вызвавшей откат проверки — она сама остаётся в новом
    # окне, поэтому для повторного перехода нужно ещё 50 проверок без неё
    stats = tracker.window_stats()
    assert stats["total"] == 1
    assert stats["critical_count"] == 1


def test_registry_returns_same_tracker_per_agent():
    registry = AuditReadinessRegistry()
    tracker_a = registry.get("agent_4_smetchik")
    tracker_b = registry.get("agent_4_smetchik")
    tracker_c = registry.get("agent_3_analitik")

    assert tracker_a is tracker_b
    assert tracker_a is not tracker_c

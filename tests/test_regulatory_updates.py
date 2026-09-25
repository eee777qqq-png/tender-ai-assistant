import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from regulatory_updates import RegulatoryUpdateStore, UpdateStatus
from regulatory_updates.sample_versions import (
    FZ44_SECURITY_Q2_2026,
    FZ44_SECURITY_Q3_2026,
    FZ44_SECURITY_SOURCE_ID,
    FZ44_SECURITY_SOURCE_NAME,
    FZ44_SECURITY_SOURCE_TYPE,
    MOSCOW_PRICE_INDEX_Q2_2026,
    MOSCOW_PRICE_INDEX_Q3_2026,
    MOSCOW_PRICE_INDEX_SOURCE_NAME,
    MOSCOW_PRICE_INDEX_SOURCE_TYPE,
)


def make_store(tmp_path) -> RegulatoryUpdateStore:
    return RegulatoryUpdateStore(tmp_path / "regulatory_updates.sqlite3")


def detect_q3(store: RegulatoryUpdateStore):
    return store.detect_update(
        MOSCOW_PRICE_INDEX_SOURCE_TYPE, MOSCOW_PRICE_INDEX_SOURCE_NAME, MOSCOW_PRICE_INDEX_Q3_2026
    )


def seed_applied_q2(store: RegulatoryUpdateStore) -> None:
    """Заводит Q2 как уже применённую версию — через полный цикл
    обнаружение → подтверждение, чтобы не обходить протокол даже в тестовой
    подготовке."""
    update = store.detect_update(
        MOSCOW_PRICE_INDEX_SOURCE_TYPE, MOSCOW_PRICE_INDEX_SOURCE_NAME, MOSCOW_PRICE_INDEX_Q2_2026
    )
    store.approve_update(update.update_id, reviewer="Edwin", approved=True)


def test_first_seen_version_becomes_a_pending_update_not_applied_automatically(tmp_path):
    """Даже когда применённой версии ещё нет вовсе, обнаруженная версия не
    становится применённой сама по себе — тоже требует решения эксперта."""
    store = make_store(tmp_path)

    update = store.detect_update(
        MOSCOW_PRICE_INDEX_SOURCE_TYPE, MOSCOW_PRICE_INDEX_SOURCE_NAME, MOSCOW_PRICE_INDEX_Q2_2026
    )

    assert update is not None
    assert update.status == UpdateStatus.PENDING
    assert update.diff.from_version_label is None
    assert store.get_applied_version(update.source_id) is None


def test_detect_update_finds_new_version_after_baseline_applied(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)

    update = detect_q3(store)

    assert update is not None
    assert update.status == UpdateStatus.PENDING
    assert update.candidate_version.version_label == "2026-Q3"
    # Применённая версия не поменялась только от факта обнаружения.
    applied = store.get_applied_version(update.source_id)
    assert applied.version_label == "2026-Q2"


def test_detect_update_returns_none_when_candidate_matches_applied_version(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)

    same_version = store.detect_update(
        MOSCOW_PRICE_INDEX_SOURCE_TYPE, MOSCOW_PRICE_INDEX_SOURCE_NAME, MOSCOW_PRICE_INDEX_Q2_2026
    )

    assert same_version is None


def test_detect_update_does_not_duplicate_already_pending_version(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)

    first = detect_q3(store)
    second = detect_q3(store)

    assert first.update_id == second.update_id
    assert len(store.list_pending(first.source_id)) == 1


def test_diff_lists_only_changed_items_with_old_and_new_values(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)

    update = detect_q3(store)
    diff = update.diff

    assert diff.from_version_label == "2026-Q2"
    assert diff.to_version_label == "2026-Q3"

    changes_by_key = {c.key: c for c in diff.changes}
    # index_pnr не менялось — не должно попасть в diff.
    assert "index_pnr" not in changes_by_key

    assert changes_by_key["index_smr"].old_value == "6.12"
    assert changes_by_key["index_smr"].new_value == "6.27"
    assert changes_by_key["index_oborudovanie"].old_value == "4.35"
    assert changes_by_key["index_oborudovanie"].new_value == "4.50"

    # index_proezd — новый пункт, которого не было в Q2.
    assert changes_by_key["index_proezd"].old_value is None
    assert changes_by_key["index_proezd"].new_value == "1.08"
    assert changes_by_key["index_proezd"].is_new_item


def test_apply_update_without_approval_raises_error(tmp_path):
    """Протокол: применение никогда не происходит автоматически. Прямой
    вызов apply_update() на ещё не подтверждённой записи должен упасть —
    единственный путь применить версию — approve_update(..., approved=True)."""
    store = make_store(tmp_path)
    seed_applied_q2(store)

    update = detect_q3(store)

    with pytest.raises(ValueError, match="approve_update"):
        store.apply_update(update.update_id)

    # Применённая версия осталась прежней.
    assert store.get_applied_version(update.source_id).version_label == "2026-Q2"


def test_approve_update_requires_a_named_reviewer(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)
    update = detect_q3(store)

    with pytest.raises(ValueError, match="reviewer"):
        store.approve_update(update.update_id, reviewer="", approved=True)


def test_approve_update_with_approved_true_applies_the_new_version(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)
    update = detect_q3(store)

    decided = store.approve_update(update.update_id, reviewer="Edwin", approved=True)

    assert decided.status == UpdateStatus.APPROVED
    assert decided.reviewer == "Edwin"
    applied = store.get_applied_version(update.source_id)
    assert applied.version_label == "2026-Q3"
    assert applied.items["index_smr"] == "6.27"
    assert store.list_pending(update.source_id) == []


def test_approve_update_with_approved_false_requires_a_reason(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)
    update = detect_q3(store)

    with pytest.raises(ValueError, match="причин"):
        store.approve_update(update.update_id, reviewer="Edwin", approved=False)


def test_approve_update_with_approved_false_rejects_and_keeps_old_version(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)
    update = detect_q3(store)

    decided = store.approve_update(
        update.update_id,
        reviewer="Edwin",
        approved=False,
        reason="Источник пока не подтверждён Сметриксом, применять рано",
    )

    assert decided.status == UpdateStatus.REJECTED
    assert decided.decision_reason == "Источник пока не подтверждён Сметриксом, применять рано"
    # Применённая версия не поменялась.
    applied = store.get_applied_version(update.source_id)
    assert applied.version_label == "2026-Q2"

    rejected = store.list_rejected(update.source_id)
    assert len(rejected) == 1
    assert rejected[0].decision_reason == "Источник пока не подтверждён Сметриксом, применять рано"


def test_cannot_decide_the_same_update_twice(tmp_path):
    store = make_store(tmp_path)
    seed_applied_q2(store)
    update = detect_q3(store)
    store.approve_update(update.update_id, reviewer="Edwin", approved=True)

    with pytest.raises(ValueError, match="уже принято"):
        store.approve_update(update.update_id, reviewer="Edwin", approved=False, reason="передумал")


def test_is_due_for_check_is_true_before_any_check_was_ever_recorded(tmp_path):
    store = make_store(tmp_path)

    assert store.is_due_for_check("tax_profit_rate_ooo", min_interval_days=90) is True
    assert store.last_checked_at("tax_profit_rate_ooo") is None


def test_record_check_makes_source_not_due_until_interval_elapses(tmp_path):
    store = make_store(tmp_path)

    store.record_check("tax_profit_rate_ooo")

    assert store.last_checked_at("tax_profit_rate_ooo") is not None
    # Только что проверили — с интервалом хоть в 1 день ещё рано проверять снова.
    assert store.is_due_for_check("tax_profit_rate_ooo", min_interval_days=1) is False


def test_check_log_is_independent_per_source(tmp_path):
    """Квартальный интервал для налоговых ставок и годовой для гражданского
    права должны считаться независимо — проверка одного источника не
    отмечает другой как проверенный."""
    store = make_store(tmp_path)

    store.record_check("tax_vat_rates")

    assert store.is_due_for_check("tax_vat_rates", min_interval_days=90) is False
    assert store.is_due_for_check("civil_law_gk_rf_art401", min_interval_days=365) is True


def test_legislation_source_full_cycle(tmp_path):
    """Тот же протокол работает и для второго типа источника —
    законодательства (44-ФЗ), не только для базы расценок."""
    store = make_store(tmp_path)
    baseline = store.detect_update(
        FZ44_SECURITY_SOURCE_TYPE, FZ44_SECURITY_SOURCE_NAME, FZ44_SECURITY_Q2_2026
    )
    store.approve_update(baseline.update_id, reviewer="Edwin", approved=True)

    update = store.detect_update(
        FZ44_SECURITY_SOURCE_TYPE, FZ44_SECURITY_SOURCE_NAME, FZ44_SECURITY_Q3_2026
    )

    changes_by_key = {c.key: c for c in update.diff.changes}
    assert set(changes_by_key) == {"art96_kontrakt_percent"}
    assert changes_by_key["art96_kontrakt_percent"].old_value == "5%–30%"
    assert changes_by_key["art96_kontrakt_percent"].new_value == "10%–30%"

    with pytest.raises(ValueError):
        store.apply_update(update.update_id)

    store.approve_update(update.update_id, reviewer="Edwin", approved=True)
    assert store.get_applied_version(FZ44_SECURITY_SOURCE_ID).version_label == "ред. от 01.09.2026"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import review_queue
from regulatory_updates import RegulatoryUpdateStore
from regulatory_updates.sample_versions import (
    MOSCOW_PRICE_INDEX_Q2_2026,
    MOSCOW_PRICE_INDEX_Q3_2026,
    MOSCOW_PRICE_INDEX_SOURCE_NAME,
    MOSCOW_PRICE_INDEX_SOURCE_TYPE,
)


def seed_store_with_pending_q3(db_path) -> RegulatoryUpdateStore:
    """Применённая Q2 (через полный цикл обнаружение+подтверждение) плюс
    обнаруженная, но ещё не решённая Q3 — ровно то, что должно оказаться в
    очереди эксперта."""
    store = RegulatoryUpdateStore(db_path)
    baseline = store.detect_update(
        MOSCOW_PRICE_INDEX_SOURCE_TYPE, MOSCOW_PRICE_INDEX_SOURCE_NAME, MOSCOW_PRICE_INDEX_Q2_2026
    )
    store.approve_update(baseline.update_id, reviewer="Edwin", approved=True)
    store.detect_update(
        MOSCOW_PRICE_INDEX_SOURCE_TYPE, MOSCOW_PRICE_INDEX_SOURCE_NAME, MOSCOW_PRICE_INDEX_Q3_2026
    )
    return store


def make_fake_input(answers: list[str]):
    it = iter(answers)

    def fake_input(prompt: str = "") -> str:
        return next(it)

    return fake_input


def test_review_queue_shows_pending_regulatory_update_with_diff(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "regulatory_updates.sqlite3"
    seed_store_with_pending_q3(db_path)

    # Порядок ответов: имя эксперта, затем y/n на каждый пункт очереди
    # (1 обновление Агента 10 + 3 тестовых документа Агента 3).
    answers = ["Edwin", "y", "n", "нужно перепроверить сроки самому", "y", "y"]
    monkeypatch.setattr("builtins.input", make_fake_input(answers))

    exit_code = review_queue.main(["--db", str(db_path)])

    assert exit_code == 0
    out = capsys.readouterr().out

    # Diff обновления нормативки виден целиком, по пунктам.
    assert "index_smr: 6.12 -> 6.27" in out
    assert "index_oborudovanie: 4.35 -> 4.50" in out
    assert "index_proezd: (не было) -> 1.08" in out
    assert "index_pnr" not in out  # не изменился — не должен попасть в вывод diff

    assert out.count("-> Подтверждено.") == 3  # обновление Агента 10 + 2 подтверждённых документа
    assert out.count("-> Отклонено.") == 1
    assert "нужно перепроверить сроки самому" in out

    store = RegulatoryUpdateStore(db_path)
    applied = store.get_applied_version(MOSCOW_PRICE_INDEX_Q3_2026.source_id)
    assert applied.version_label == "2026-Q3"
    assert store.list_pending() == []


def test_review_queue_reports_empty_queue_when_nothing_pending(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "regulatory_updates.sqlite3"
    RegulatoryUpdateStore(db_path)  # применённой версии нет, PENDING тоже нет

    monkeypatch.setattr("builtins.input", make_fake_input(["Edwin"]))

    exit_code = review_queue.main(["--db", str(db_path), "--skip-agent3-samples"])

    assert exit_code == 0
    assert "Очередь пуста" in capsys.readouterr().out


def test_reject_on_regulatory_update_keeps_old_version_and_records_reason(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "regulatory_updates.sqlite3"
    store = seed_store_with_pending_q3(db_path)

    answers = ["Edwin", "n", "источник пока не подтверждён"]
    monkeypatch.setattr("builtins.input", make_fake_input(answers))

    review_queue.main(["--db", str(db_path), "--skip-agent3-samples"])

    applied = store.get_applied_version(MOSCOW_PRICE_INDEX_Q3_2026.source_id)
    assert applied.version_label == "2026-Q2"  # осталась прежней
    rejected = store.list_rejected(MOSCOW_PRICE_INDEX_Q3_2026.source_id)
    assert len(rejected) == 1
    assert rejected[0].decision_reason == "источник пока не подтверждён"

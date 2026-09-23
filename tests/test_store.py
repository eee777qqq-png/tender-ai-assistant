import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eis_client.client import ConstructionDocument
from eis_client.store import MonitorStore
from run_monitor import dates_to_fetch

NOTICE_SIGNATURE = "PRIZ:epNotificationEF2020"
LEGACY_SIGNATURE = "RGK:contract"


def test_new_store_has_no_fetched_dates(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    assert store.last_fetched_date(NOTICE_SIGNATURE) is None
    assert not store.is_date_fetched(date(2026, 9, 15), NOTICE_SIGNATURE)


def test_save_results_marks_date_fetched_and_stores_documents(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    docs = [
        ConstructionDocument(archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["41.20"]),
        ConstructionDocument(archive_url="https://x/a.zip", file_name="2.xml", okpd2_codes=["43.99"]),
    ]

    store.save_results(date(2026, 9, 15), docs, NOTICE_SIGNATURE)

    assert store.is_date_fetched(date(2026, 9, 15), NOTICE_SIGNATURE)
    assert store.last_fetched_date(NOTICE_SIGNATURE) == date(2026, 9, 15)
    stored = store.all_documents()
    assert len(stored) == 2
    assert stored[0].okpd2_codes == ["41.20"]
    assert stored[0].source_signature == NOTICE_SIGNATURE


def test_save_results_with_empty_list_still_marks_date_fetched(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    store.save_results(date(2026, 9, 15), [], NOTICE_SIGNATURE)
    assert store.is_date_fetched(date(2026, 9, 15), NOTICE_SIGNATURE)
    assert store.all_documents() == []


def test_save_results_is_idempotent(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    doc = ConstructionDocument(archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["41.20"])

    store.save_results(date(2026, 9, 15), [doc], NOTICE_SIGNATURE)
    store.save_results(date(2026, 9, 15), [doc], NOTICE_SIGNATURE)

    assert len(store.all_documents()) == 1


def test_date_fetched_under_one_signature_is_not_fetched_under_another(tmp_path):
    """Ядро смены источника: дата, обработанная под контрактами, не
    считается обработанной под извещениями — их нельзя молча смешивать."""
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    store.save_results(date(2026, 9, 15), [], LEGACY_SIGNATURE)

    assert store.is_date_fetched(date(2026, 9, 15), LEGACY_SIGNATURE)
    assert not store.is_date_fetched(date(2026, 9, 15), NOTICE_SIGNATURE)
    assert store.last_fetched_date(NOTICE_SIGNATURE) is None
    assert store.last_fetched_date(LEGACY_SIGNATURE) == date(2026, 9, 15)


def test_dates_fetched_under_other_signatures_reports_counts(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    store.save_results(date(2026, 9, 15), [], LEGACY_SIGNATURE)
    store.save_results(date(2026, 9, 16), [], LEGACY_SIGNATURE)
    store.save_results(date(2026, 9, 17), [], NOTICE_SIGNATURE)

    other = store.dates_fetched_under_other_signatures(NOTICE_SIGNATURE)

    assert other == {LEGACY_SIGNATURE: 2}
    assert store.dates_fetched_under_other_signatures(LEGACY_SIGNATURE) == {NOTICE_SIGNATURE: 1}


def test_pre_2026_09_23_database_migrates_old_rows_to_legacy_signature(tmp_path):
    """База, созданная до появления source_signature (старая схема без этой
    колонки), должна открыться и автоматически пометить все свои старые
    строки RGK:contract — не потерять их и не спутать с новым источником."""
    import sqlite3

    db_path = tmp_path / "old_monitor.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE fetched_dates (
            fetch_date TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetch_date TEXT NOT NULL,
            file_name TEXT NOT NULL,
            archive_url TEXT NOT NULL,
            okpd2_codes TEXT NOT NULL,
            discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(fetch_date, file_name, archive_url)
        );
        """
    )
    conn.execute("INSERT INTO fetched_dates (fetch_date) VALUES ('2026-09-10')")
    conn.execute(
        "INSERT INTO documents (fetch_date, file_name, archive_url, okpd2_codes) "
        "VALUES ('2026-09-10', 'contract_1.xml', 'https://x/a.zip', '41.20')"
    )
    conn.commit()
    conn.close()

    store = MonitorStore(db_path)

    assert store.is_date_fetched(date(2026, 9, 10), LEGACY_SIGNATURE)
    assert not store.is_date_fetched(date(2026, 9, 10), NOTICE_SIGNATURE)
    docs = store.all_documents()
    assert len(docs) == 1
    assert docs[0].source_signature == LEGACY_SIGNATURE
    assert docs[0].file_name == "contract_1.xml"


def test_dates_to_fetch_starts_from_override_when_store_empty(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    start = yesterday - timedelta(days=2)

    result = dates_to_fetch(store, start, NOTICE_SIGNATURE)

    assert result[0] == start
    assert result[-1] == yesterday
    assert len(result) == 3


def test_dates_to_fetch_continues_after_last_fetched(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    already_fetched = yesterday - timedelta(days=1)
    store.save_results(already_fetched, [], NOTICE_SIGNATURE)

    result = dates_to_fetch(store, None, NOTICE_SIGNATURE)

    assert result == [yesterday]


def test_dates_to_fetch_empty_when_already_up_to_date(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    store.save_results(yesterday, [], NOTICE_SIGNATURE)

    result = dates_to_fetch(store, None, NOTICE_SIGNATURE)

    assert result == []


def test_dates_to_fetch_ignores_dates_fetched_under_a_different_signature(tmp_path):
    """Дата, отмеченная под старым источником, не двигает точку возобновления
    для нового — монитор под новым источником начнёт заново, не продолжит
    как ни в чём не бывало с чужой даты."""
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    store.save_results(yesterday, [], LEGACY_SIGNATURE)

    result = dates_to_fetch(store, None, NOTICE_SIGNATURE)

    assert result == [yesterday]

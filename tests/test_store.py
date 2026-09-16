import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eis_client.client import ConstructionDocument
from eis_client.store import MonitorStore
from run_monitor import dates_to_fetch


def test_new_store_has_no_fetched_dates(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    assert store.last_fetched_date() is None
    assert not store.is_date_fetched(date(2026, 9, 15))


def test_save_results_marks_date_fetched_and_stores_documents(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    docs = [
        ConstructionDocument(archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["41.20"]),
        ConstructionDocument(archive_url="https://x/a.zip", file_name="2.xml", okpd2_codes=["43.99"]),
    ]

    store.save_results(date(2026, 9, 15), docs)

    assert store.is_date_fetched(date(2026, 9, 15))
    assert store.last_fetched_date() == date(2026, 9, 15)
    stored = store.all_documents()
    assert len(stored) == 2
    assert stored[0].okpd2_codes == ["41.20"]


def test_save_results_with_empty_list_still_marks_date_fetched(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    store.save_results(date(2026, 9, 15), [])
    assert store.is_date_fetched(date(2026, 9, 15))
    assert store.all_documents() == []


def test_save_results_is_idempotent(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    doc = ConstructionDocument(archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["41.20"])

    store.save_results(date(2026, 9, 15), [doc])
    store.save_results(date(2026, 9, 15), [doc])

    assert len(store.all_documents()) == 1


def test_dates_to_fetch_starts_from_override_when_store_empty(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    start = yesterday - timedelta(days=2)

    result = dates_to_fetch(store, start)

    assert result[0] == start
    assert result[-1] == yesterday
    assert len(result) == 3


def test_dates_to_fetch_continues_after_last_fetched(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    already_fetched = yesterday - timedelta(days=1)
    store.save_results(already_fetched, [])

    result = dates_to_fetch(store, None)

    assert result == [yesterday]


def test_dates_to_fetch_empty_when_already_up_to_date(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    store.save_results(yesterday, [])

    result = dates_to_fetch(store, None)

    assert result == []

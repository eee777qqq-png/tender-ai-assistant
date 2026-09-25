"""Автообновление версий данных ФГИС ЦС для Агента 4 — тем же паттерном,
что уже есть у Агента 10 (`RegulatoryUpdateStore.detect_update()`/
`approve_update()`): обнаруженная новая версия становится записью
`PENDING`, ждущей решения эксперта, применённая версия сама по себе не
меняется.

Формы ответов ниже — не выдуманы, а сняты вживую с реальных эндпоинтов
ФГИС ЦС 2026-09-25 (см. `smeta_estimator/version_watch.py` за деталями):
`GET /api/OpenData/GetByNumber/ 7707082071-fsnb` (паспорт набора данных
«ФСНБ-2022») и уже существующий `regional_pricing_client.fetch_periods()`.
На момент проверки обе версии совпадали с захардкоженными
`DEFAULT_ARCHIVE_URL`/`CURRENT_PERIOD_ID` — «нечего обновлять» тоже реально
проверено, не только сценарий с найденным обновлением на фикстурах ниже.
"""

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from regulatory_updates import RegulatoryUpdateStore
from smeta_estimator.fsnb_client import DEFAULT_ARCHIVE_URL
from smeta_estimator.regional_pricing_client import CURRENT_PERIOD_ID
from smeta_estimator.version_watch import (
    FSNB_ARCHIVE_SOURCE_ID,
    archive_url_for_guid,
    check_all_period_updates,
    check_fsnb_archive_update,
    check_period_update,
    fetch_latest_fsnb_archive_version,
    fetch_latest_period_version,
    get_active_archive_url,
    get_active_period_id,
    period_source_id,
)

# Урезанный, но реальный ответ /api/OpenData/GetByNumber/ 7707082071-fsnb,
# снятый вживую 2026-09-25 — та же версия, что уже зашита в
# fsnb_client.DEFAULT_ARCHIVE_URL (guid 7f4f249c-...).
_CURRENT_FSNB_RESPONSE = {
    "identificationNumber": " 7707082071-fsnb",
    "datasetName": "ФСНБ-2022",
    "datasetFile": {
        "path": "7f4f249c-9781-495c-9976-e795e0e8ed4e",
        "name": "data-20260812-structure-20240216.zip",
    },
    "lastChangeDate": "2026-08-12T14:51:00+03:00",
    "datasetVersionFiles": [
        {"path": "402b12bf-0bc5-458e-ac61-e9a19610ec66", "name": "data-20260709-structure-20240216.zip"},
    ],
}

_NEWER_FSNB_RESPONSE = {
    **_CURRENT_FSNB_RESPONSE,
    "datasetFile": {
        "path": "11111111-2222-3333-4444-555555555555",
        "name": "data-20261110-structure-20240216.zip",
    },
    "lastChangeDate": "2026-11-10T10:00:00+03:00",
}

# Реальный (урезанный) ответ fetch_periods() для Москвы, снят вживую
# 2026-09-25 — id=427 совпадает с CURRENT_PERIOD_ID.
_CURRENT_PERIODS_RESPONSE = [
    {"id": 427, "name": "3 квартал 2026 г."},
    {"id": 426, "name": "2 квартал 2026 г."},
    {"id": 425, "name": "1 квартал 2026 г."},
]

_NEWER_PERIODS_RESPONSE = [
    {"id": 428, "name": "4 квартал 2026 г."},
    *_CURRENT_PERIODS_RESPONSE,
]


def _mock_response(payload) -> MagicMock:
    return MagicMock(text=json.dumps(payload, ensure_ascii=False), json=lambda: payload, raise_for_status=lambda: None)


def make_store(tmp_path) -> RegulatoryUpdateStore:
    return RegulatoryUpdateStore(tmp_path / "regulatory_updates.sqlite3")


# -- разбор ответов -----------------------------------------------------------


def test_fetch_latest_fsnb_archive_version_parses_the_real_response_shape():
    with patch("requests.get", return_value=_mock_response(_CURRENT_FSNB_RESPONSE)) as mock_get:
        version = fetch_latest_fsnb_archive_version()

    assert "GetByNumber" in mock_get.call_args[0][0]
    assert version.source_id == FSNB_ARCHIVE_SOURCE_ID
    assert version.version_label == "data-20260812-structure-20240216.zip"
    assert version.published_at == date(2026, 8, 12)
    assert version.items["archive_guid"] == "7f4f249c-9781-495c-9976-e795e0e8ed4e"
    assert version.items["archive_url"] == archive_url_for_guid("7f4f249c-9781-495c-9976-e795e0e8ed4e")
    # Ровно та же ссылка, что уже захардкожена как бутстрап-дефолт — на
    # момент проверки (2026-09-25) обновлять было нечего.
    assert version.items["archive_url"] == DEFAULT_ARCHIVE_URL


def test_fetch_latest_period_version_uses_first_item_as_the_newest():
    with patch("requests.get", return_value=_mock_response(_CURRENT_PERIODS_RESPONSE)):
        version = fetch_latest_period_version("г. Москва", price_zone_id=191)

    assert version.source_id == period_source_id("г. Москва")
    assert version.version_label == "3 квартал 2026 г."
    assert version.published_at == date(2026, 7, 1)  # начало 3 квартала
    assert version.items == {"period_id": "427", "period_label": "3 квартал 2026 г."}


def test_period_source_id_matches_gosr_region_key_normalization():
    assert period_source_id("г. Москва") == "fgiscs_period_Москва"
    assert period_source_id("Московская область") == "fgiscs_period_Московская_область"


# -- обнаружение обновления через RegulatoryUpdateStore ------------------------


def test_check_fsnb_archive_update_does_not_duplicate_the_same_pending_record(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_CURRENT_FSNB_RESPONSE)):
        first = check_fsnb_archive_update(store)
        # Применённой версии всё ещё нет (ничего не подтверждено) — но та
        # же самая обнаруженная версия не должна плодить второй PENDING.
        second = check_fsnb_archive_update(store)

    assert first is not None
    assert second is not None
    assert first.update_id == second.update_id


def test_check_fsnb_archive_update_returns_none_once_approved_and_unchanged(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_CURRENT_FSNB_RESPONSE)):
        baseline = check_fsnb_archive_update(store)
    store.approve_update(baseline.update_id, reviewer="Edwin", approved=True)

    with patch("requests.get", return_value=_mock_response(_CURRENT_FSNB_RESPONSE)):
        # Тот же самый ФСНБ-2022, что уже применён, — обновлять нечего.
        again = check_fsnb_archive_update(store)

    assert again is None


def test_check_fsnb_archive_update_detects_a_real_new_version(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_CURRENT_FSNB_RESPONSE)):
        baseline = check_fsnb_archive_update(store)
    store.approve_update(baseline.update_id, reviewer="Edwin", approved=True)

    with patch("requests.get", return_value=_mock_response(_NEWER_FSNB_RESPONSE)):
        update = check_fsnb_archive_update(store)

    assert update is not None
    assert update.diff.from_version_label == "data-20260812-structure-20240216.zip"
    assert update.diff.to_version_label == "data-20261110-structure-20240216.zip"
    changed_keys = {c.key for c in update.diff.changes}
    assert "archive_guid" in changed_keys
    assert "archive_url" in changed_keys
    # Обнаружение НЕ применяет обновление само по себе.
    assert store.get_applied_version(FSNB_ARCHIVE_SOURCE_ID).items["archive_guid"] == (
        "7f4f249c-9781-495c-9976-e795e0e8ed4e"
    )


def test_check_all_period_updates_covers_all_4_pilot_regions_independently(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_CURRENT_PERIODS_RESPONSE)):
        updates = check_all_period_updates(store)

    # Все 4 региона — первая проверка на пустом сторе, все впервые обнаружены.
    assert len(updates) == 4
    assert {u.source_id for u in updates} == {
        period_source_id(name) for name in ("г. Москва", "Московская область", "Краснодарский край", "Ростовская область")
    }


def test_check_period_update_detects_a_new_quarter(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_CURRENT_PERIODS_RESPONSE)):
        baseline = check_period_update(store, "г. Москва", price_zone_id=191)
    store.approve_update(baseline.update_id, reviewer="Edwin", approved=True)

    with patch("requests.get", return_value=_mock_response(_NEWER_PERIODS_RESPONSE)):
        update = check_period_update(store, "г. Москва", price_zone_id=191)

    assert update is not None
    assert update.diff.from_version_label == "3 квартал 2026 г."
    assert update.diff.to_version_label == "4 квартал 2026 г."


# -- чтение "активной" версии для реального использования ---------------------


def test_get_active_archive_url_falls_back_to_hardcoded_default_before_any_approval(tmp_path):
    store = make_store(tmp_path)
    assert get_active_archive_url(store) == DEFAULT_ARCHIVE_URL


def test_get_active_archive_url_reflects_the_approved_version(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_CURRENT_FSNB_RESPONSE)):
        baseline = check_fsnb_archive_update(store)
    store.approve_update(baseline.update_id, reviewer="Edwin", approved=True)

    with patch("requests.get", return_value=_mock_response(_NEWER_FSNB_RESPONSE)):
        update = check_fsnb_archive_update(store)
    store.approve_update(update.update_id, reviewer="Edwin", approved=True)

    assert get_active_archive_url(store) == archive_url_for_guid("11111111-2222-3333-4444-555555555555")


def test_get_active_archive_url_does_not_change_on_rejection(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_CURRENT_FSNB_RESPONSE)):
        baseline = check_fsnb_archive_update(store)
    store.approve_update(baseline.update_id, reviewer="Edwin", approved=True)

    with patch("requests.get", return_value=_mock_response(_NEWER_FSNB_RESPONSE)):
        update = check_fsnb_archive_update(store)
    store.approve_update(update.update_id, reviewer="Edwin", approved=False, reason="ещё не проверено вручную")

    assert get_active_archive_url(store) == DEFAULT_ARCHIVE_URL


def test_get_active_period_id_falls_back_to_hardcoded_default_before_any_approval(tmp_path):
    store = make_store(tmp_path)
    assert get_active_period_id(store, "г. Москва") == CURRENT_PERIOD_ID


def test_get_active_period_id_is_independent_per_region(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_CURRENT_PERIODS_RESPONSE)):
        moscow_baseline = check_period_update(store, "г. Москва", price_zone_id=191)
    store.approve_update(moscow_baseline.update_id, reviewer="Edwin", approved=True)

    with patch("requests.get", return_value=_mock_response(_NEWER_PERIODS_RESPONSE)):
        moscow_update = check_period_update(store, "г. Москва", price_zone_id=191)
    store.approve_update(moscow_update.update_id, reviewer="Edwin", approved=True)

    # Москва подтверждена на новый квартал, Московская область — ещё нет.
    assert get_active_period_id(store, "г. Москва") == 428
    assert get_active_period_id(store, "Московская область") == CURRENT_PERIOD_ID

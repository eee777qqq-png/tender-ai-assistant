"""Обнаружение обновлений бухгалтерской/юридической базы (Агент 10,
`regulatory_updates.legislation_watch`) через `RegulatoryUpdateStore` — тот
же паттерн, что уже покрыт для Агента 4 в
`tests/test_smeta_estimator_version_watch.py`: `requests.get` подменяется
на реальные (вырезанные вживую) фрагменты HTML, сеть в тестах не
используется. Разбор текста уже покрыт отдельно в
`tests/test_legislation_parser.py` — здесь проверяется склейка с
`RegulatoryUpdateStore` (PENDING/diff/календарь), не сам regex-разбор.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from regulatory_updates import RegulatoryUpdateStore
from regulatory_updates.legislation_watch import (
    CIVIL_LAW_GK_ART401_URL,
    CIVIL_LAW_GK_ART421_URL,
    CIVIL_LAW_ZPP_ART16_URL,
    LEGAL_CHECK_INTERVAL_DAYS,
    TAX_CHECK_INTERVAL_DAYS,
    TAX_NDFL_BRACKETS_URL,
    TAX_PROFIT_RATE_OOO_URL,
    TAX_USN_RATES_URL,
    TAX_VAT_RATES_URL,
    check_all_civil_law_sources,
    check_all_tax_sources,
    check_gk_rf_art401,
    check_tax_profit_rate_ooo,
)
from regulatory_updates.legislation_parser import (
    CIVIL_LAW_GK_ART401_SOURCE_ID,
    TAX_PROFIT_RATE_OOO_SOURCE_ID,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_store(tmp_path) -> RegulatoryUpdateStore:
    return RegulatoryUpdateStore(tmp_path / "regulatory_updates.sqlite3")


def _mock_response(html: str) -> MagicMock:
    return MagicMock(text=html, raise_for_status=lambda: None)


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


_PROFIT_25 = load_fixture("nalog_profit_tax_sample.html")
_PROFIT_26 = _PROFIT_25.replace("Основная ставка 25%", "Основная ставка 26%")
_GK_401 = load_fixture("consultant_gk_art401_sample.html")

# Правильный фрагмент по каждому URL — для тестов, которые дёргают все 4
# налоговых (или все 3 юридических) источника разом и должны реально
# успешно их разобрать, не просто убедиться, что сеть была вызвана.
_TAX_FIXTURES_BY_URL = {
    TAX_PROFIT_RATE_OOO_URL: _PROFIT_25,
    TAX_VAT_RATES_URL: load_fixture("nalog_vat_sample.html"),
    TAX_USN_RATES_URL: load_fixture("nalog_usn_sample.html"),
    TAX_NDFL_BRACKETS_URL: load_fixture("nalog_ndfl_sample.html"),
}
_CIVIL_LAW_FIXTURES_BY_URL = {
    CIVIL_LAW_GK_ART401_URL: _GK_401,
    CIVIL_LAW_GK_ART421_URL: load_fixture("consultant_gk_art421_sample.html"),
    CIVIL_LAW_ZPP_ART16_URL: load_fixture("consultant_zpp_art16_sample.html"),
}


def _fake_get_by_url(fixtures_by_url: dict[str, str]):
    def _fake_get(url, *args, **kwargs):
        return _mock_response(fixtures_by_url[url])

    return _fake_get


def test_check_tax_profit_rate_ooo_does_not_duplicate_pending(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_PROFIT_25)):
        first = check_tax_profit_rate_ooo(store)
        second = check_tax_profit_rate_ooo(store)

    assert first is not None
    assert first.update_id == second.update_id
    assert first.source_type.value == "legislation"


def test_check_tax_profit_rate_ooo_detects_a_rate_change(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_PROFIT_25)):
        baseline = check_tax_profit_rate_ooo(store)
    store.approve_update(baseline.update_id, reviewer="Edwin", approved=True)

    with patch("requests.get", return_value=_mock_response(_PROFIT_26)):
        update = check_tax_profit_rate_ooo(store)

    assert update is not None
    changed = {c.key: c for c in update.diff.changes}
    assert changed["rate_percent"].old_value == "25"
    assert changed["rate_percent"].new_value == "26"
    # Обнаружение не применяет обновление само по себе.
    assert store.get_applied_version(TAX_PROFIT_RATE_OOO_SOURCE_ID).items["rate_percent"] == "25"


def test_check_gk_rf_art401_uses_edition_date_as_version_label(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", return_value=_mock_response(_GK_401)):
        update = check_gk_rf_art401(store)

    assert update.source_id == CIVIL_LAW_GK_ART401_SOURCE_ID
    assert update.candidate_version.version_label == "ред. от 10.06.2026"
    assert "ничтожно" in update.candidate_version.items["clause_4_text"]


def test_check_all_tax_sources_checks_every_source_on_first_run(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", side_effect=_fake_get_by_url(_TAX_FIXTURES_BY_URL)) as mock_get:
        results = check_all_tax_sources(store)

    assert mock_get.call_count == 4
    assert len(results) == 4  # применённой версии ещё не было — все 4 впервые обнаружены


def test_check_all_civil_law_sources_checks_every_source_on_first_run(tmp_path):
    store = make_store(tmp_path)
    with patch("requests.get", side_effect=_fake_get_by_url(_CIVIL_LAW_FIXTURES_BY_URL)) as mock_get:
        results = check_all_civil_law_sources(store)

    assert mock_get.call_count == 3
    assert len(results) == 3


def test_check_all_tax_sources_skips_sources_not_yet_due(tmp_path):
    """Квартальный календарь — источник, только что проверенный, не должен
    снова дёргать сеть при повторном запуске до истечения интервала."""
    store = make_store(tmp_path)
    with patch("requests.get", side_effect=_fake_get_by_url(_TAX_FIXTURES_BY_URL)):
        check_all_tax_sources(store, force=True)

    with patch("requests.get", side_effect=_fake_get_by_url(_TAX_FIXTURES_BY_URL)) as mock_get:
        results = check_all_tax_sources(store)  # force=False по умолчанию

    assert mock_get.call_count == 0
    assert results == []


def test_check_all_civil_law_sources_uses_a_separate_annual_interval(tmp_path):
    """Проверка налоговых источников не должна отмечать юридические как
    проверенные, и наоборот — независимые календари."""
    store = make_store(tmp_path)
    with patch("requests.get", side_effect=_fake_get_by_url(_TAX_FIXTURES_BY_URL)):
        check_all_tax_sources(store, force=True)

    assert store.is_due_for_check(CIVIL_LAW_GK_ART401_SOURCE_ID, LEGAL_CHECK_INTERVAL_DAYS) is True
    assert store.is_due_for_check(TAX_PROFIT_RATE_OOO_SOURCE_ID, TAX_CHECK_INTERVAL_DAYS) is False

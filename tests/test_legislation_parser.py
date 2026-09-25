"""Тесты разбора бухгалтерской/юридической базы (Агент 10,
`regulatory_updates.legislation_parser`) — на реальных, вырезанных вживую
2026-09-25 фрагментах страниц nalog.gov.ru и consultant.ru
(`tests/fixtures/nalog_*_sample.html`, `tests/fixtures/consultant_*_sample.html`),
не на выдуманном тексте. Сеть здесь не используется — `legislation_client.
strip_html()` применяется к локальному файлу, ровно как к ответу сервера."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from regulatory_updates import legislation_parser as parser
from regulatory_updates.legislation_client import strip_html

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return strip_html(Path(FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_profit_tax_rate_from_real_nalog_gov_ru_fragment():
    text = load("nalog_profit_tax_sample.html")

    version = parser.parse_profit_tax_rate(text, "https://example.invalid/profit")

    assert version.source_id == parser.TAX_PROFIT_RATE_OOO_SOURCE_ID
    assert version.items["rate_percent"] == "25"
    assert version.items["federal_budget_percent"] == "8"
    assert version.items["regional_budget_percent"] == "17"
    assert version.published_at == date.today()


def test_parse_vat_rates_from_real_nalog_gov_ru_fragment():
    text = load("nalog_vat_sample.html")

    version = parser.parse_vat_rates(text, "https://example.invalid/vat")

    assert version.items["standard_percent"] == "22"
    assert version.items["reduced_percent"] == "10"
    assert version.items["usn_low_percent"] == "5"
    assert version.items["usn_high_percent"] == "7"


def test_parse_usn_rates_from_real_nalog_gov_ru_fragment():
    text = load("nalog_usn_sample.html")

    version = parser.parse_usn_rates(text, "https://example.invalid/usn")

    assert version.items["income_percent"] == "6"
    assert version.items["income_minus_expense_percent"] == "15"


def test_parse_ndfl_brackets_from_real_nalog_gov_ru_fragment():
    text = load("nalog_ndfl_sample.html")

    version = parser.parse_ndfl_brackets(text, "https://example.invalid/ndfl")

    assert version.items["rate_signature"] == "13%/15%/18%/20%/22%"
    assert version.items["bracket_13_upper_mln"] == "2,4"
    assert version.items["bracket_15_upper_mln"] == "5"
    assert version.items["bracket_18_upper_mln"] == "20"
    assert version.items["bracket_20_upper_mln"] == "50"
    assert version.items["bracket_22_lower_mln"] == "50"


def test_parse_gk_rf_art401_from_real_consultant_ru_fragment():
    text = load("consultant_gk_art401_sample.html")

    version = parser.parse_gk_rf_art401(text, "https://example.invalid/art401")

    assert version.source_id == parser.CIVIL_LAW_GK_ART401_SOURCE_ID
    assert version.items["edition_date"] == "10.06.2026"
    assert version.published_at == date(2026, 6, 10)
    assert "ничтожно" in version.items["clause_4_text"]
    assert "умышленное нарушение" in version.items["clause_4_text"]


def test_parse_gk_rf_art421_from_real_consultant_ru_fragment():
    text = load("consultant_gk_art421_sample.html")

    version = parser.parse_gk_rf_art421(text, "https://example.invalid/art421")

    assert version.items["edition_date"] == "10.06.2026"
    assert "Граждане и юридические лица свободны" in version.items["clause_1_text"]


def test_parse_zpp_art16_from_real_consultant_ru_fragment():
    text = load("consultant_zpp_art16_sample.html")

    version = parser.parse_zpp_art16(text, "https://example.invalid/zpp16")

    assert version.items["edition_date"] == "28.12.2025"
    assert version.published_at == date(2025, 12, 28)
    assert "Недопустимыми условиями договора" in version.items["clause_1_text"]


def test_parse_profit_tax_rate_raises_a_loud_error_when_markup_changed():
    """Честный отказ, не тихий пропуск — если основная ставка не нашлась,
    значит разметка страницы изменилась и нужна ручная проверка, а не
    молчаливо неполная версия."""
    with pytest.raises(ValueError, match="основную ставку"):
        parser.parse_profit_tax_rate("совсем другой текст без цифр", "https://example.invalid/profit")

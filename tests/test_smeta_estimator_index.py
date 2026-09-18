"""Разбор писем Минстроя с региональными индексами + подключение к
существующей модели данных Агента 10 — не новая структура с нуля."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from regulatory_updates import RegulatoryUpdateStore, RegulatoryVersion, SourceType
from smeta_estimator.index_client import fetch_index_letter_html
from smeta_estimator.index_parser import (
    PILOT_REGIONS,
    PRICE_INDEX_SOURCE_ID,
    build_regulatory_version,
    parse_regional_index_values,
)

# Уменьшенный, но структурно реалистичный фрагмент HTML-таблицы письма
# Минстроя (реальный формат подтверждён вживую 2026-09-18, см.
# docs/agent4-ai-matching-feasibility.md) — с реальными числами индексов
# для наших 4 пилотных регионов, найденными в письме от 28.08.2026.
SAMPLE_LETTER_HTML = """
<html><body>
<table>
<tr><td>№ п/п</td><td>Субъект Российской Федерации</td><td>Индекс 1</td><td>Индекс 2</td></tr>
<tr><td>9</td><td>Московская область</td><td>13,56</td><td>20,08</td></tr>
<tr><td>18</td><td>г. Москва</td><td>14,94</td><td>20,32</td></tr>
<tr><td>37</td><td>Краснодарский край</td><td>12,66</td><td>20,02</td></tr>
<tr><td>38</td><td>Ростовская область</td><td>12,25</td><td>20,04</td></tr>
<tr><td>39</td><td>Республика Крым</td><td>14,28</td><td>21,68</td></tr>
</table>
</body></html>
"""


def test_parse_regional_index_values_extracts_all_pilot_regions():
    items = parse_regional_index_values(SAMPLE_LETTER_HTML)

    assert set(items) == set(PILOT_REGIONS)
    assert items["г. Москва"] == "14,94;20,32"
    assert items["Краснодарский край"] == "12,66;20,02"
    assert items["Ростовская область"] == "12,25;20,04"
    assert items["Московская область"] == "13,56;20,08"


def test_parse_regional_index_values_skips_regions_not_in_the_letter():
    html_without_krasnodar = SAMPLE_LETTER_HTML.replace(
        "<tr><td>37</td><td>Краснодарский край</td><td>12,66</td><td>20,02</td></tr>", ""
    )

    items = parse_regional_index_values(html_without_krasnodar)

    assert "Краснодарский край" not in items
    assert "Ростовская область" in items  # остальные регионы не пострадали


def test_build_regulatory_version_matches_agent_10_data_model():
    version = build_regulatory_version(
        version_label="III квартал 2026 (письмо от 28.08.2026 № 53691-АЛ/09)",
        published_at=date(2026, 8, 28),
        raw_html=SAMPLE_LETTER_HTML,
    )

    assert isinstance(version, RegulatoryVersion)
    assert version.source_id == PRICE_INDEX_SOURCE_ID
    assert version.items["г. Москва"] == "14,94;20,32"


def test_price_index_version_flows_through_the_existing_agent_10_review_protocol(tmp_path):
    """Не новая структура — используем RegulatoryUpdateStore Агента 10 как
    есть: обнаружение новой версии, PENDING, подтверждение эксперта."""
    store = RegulatoryUpdateStore(tmp_path / "regulatory_updates.sqlite3")
    version = build_regulatory_version(
        version_label="III квартал 2026",
        published_at=date(2026, 8, 28),
        raw_html=SAMPLE_LETTER_HTML,
    )

    update = store.detect_update(SourceType.PRICE_BASE, "Индексы ФГИС ЦС (тест)", version)
    assert update is not None
    assert update.status.value == "pending"

    store.approve_update(update.update_id, reviewer="Edwin", approved=True)
    applied = store.get_applied_version(PRICE_INDEX_SOURCE_ID)
    assert applied.items["Краснодарский край"] == "12,66;20,02"


def test_fetch_index_letter_html_reads_full_published_text_field_without_real_network():
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            json=lambda: {"fullPublishedText": SAMPLE_LETTER_HTML}, raise_for_status=lambda: None
        )
        html = fetch_index_letter_html("3477bb09-5c44-47f0-8ca1-a08183f16027")

    assert html == SAMPLE_LETTER_HTML
    mock_get.assert_called_once()

"""Парсер ГЭСН/ФСБЦ и загрузчик архива — на реальных данных.

Фикстуры в tests/fixtures/gesn_roof_sample.xml и fsbc_*_sample.xml — не
выдуманы, а вырезаны напрямую из настоящего архива ФСНБ-2022, скачанного
с fgiscs.minstroyrf.ru 2026-09-18 (Сборник 12 «Кровли», раздел «Устройство
кровель скатных», плюс реальные цены на упомянутые в нём ресурсы) — то есть
парсер здесь проверяется на подлинной государственной структуре данных, не
на своём собственном представлении о том, как она устроена.
"""

import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator.fsnb_client import (
    FSBC_MACHINES_FILENAME,
    FSBC_MATERIALS_FILENAME,
    GESN_FILENAME,
    download_fsnb_archive,
    extract_fsnb_files,
)
from smeta_estimator.fsnb_parser import (
    apply_prices,
    parse_fsbc_machines_xml,
    parse_fsbc_materials_xml,
    parse_gesn_xml,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_roof_catalog():
    gesn_bytes = (FIXTURES / "gesn_roof_sample.xml").read_bytes()
    materials_bytes = (FIXTURES / "fsbc_materials_sample.xml").read_bytes()
    machines_bytes = (FIXTURES / "fsbc_machines_sample.xml").read_bytes()

    items = parse_gesn_xml(gesn_bytes)
    prices = {**parse_fsbc_materials_xml(materials_bytes), **parse_fsbc_machines_xml(machines_bytes)}
    apply_prices(items, prices)
    return items


def test_parse_gesn_xml_reads_real_roof_section():
    items = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())

    assert len(items) == 7
    first = items[0]
    assert first.code == "12-01-001-01"
    assert first.unit == "100 м2"
    assert "Устройство кровель скатных" in first.name
    assert "на битумной мастике" in first.name
    resource_codes = {r.resource_code for r in first.resources}
    assert "91.05.01-017" in resource_codes  # кран башенный — конкретный ресурс
    assert "12.1.02.15" in resource_codes  # рулонный материал — категория (AbstractResource)
    abstract_flags = {r.resource_code: r.is_abstract for r in first.resources}
    assert abstract_flags["91.05.01-017"] is False
    assert abstract_flags["12.1.02.15"] is True


def test_parse_fsbc_materials_and_machines_return_real_prices():
    materials = parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes())
    machines = parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes())

    assert materials["02.2.01.02-1042"] == pytest.approx(1174.99)  # гравий
    assert materials["01.3.02.09-0022"] == pytest.approx(41.38)  # пропан-бутан
    # машино-час = зарплата машиниста + прочие затраты без зарплаты
    assert machines["91.05.01-017"] == pytest.approx(451.93 + 622.62)


def test_apply_prices_computes_base_price_and_flags_unresolved_resources():
    items = load_roof_catalog()
    by_code = {item.code: item for item in items}

    with_gravel = by_code["12-01-001-02"]
    assert with_gravel.base_price > 0
    assert not with_gravel.is_fully_priced()
    # Коды разрядов труда — не абстрактные, но и не оценённые (нет таблицы оплаты труда).
    assert "1-100-38" in with_gravel.unpriced_resource_codes
    assert "2" in with_gravel.unpriced_resource_codes
    # Битумная мастика в этой позиции задана категорией, не конкретным продуктом.
    assert "01.2.03.03" in with_gravel.abstract_resource_codes


def test_extract_fsnb_files_returns_expected_members():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(GESN_FILENAME, b"<base/>")
        zf.writestr(FSBC_MATERIALS_FILENAME, b"<x/>")
        zf.writestr(FSBC_MACHINES_FILENAME, b"<y/>")
        zf.writestr("ГЭСНм.xml", b"<z/>")  # лишний файл — должен быть проигнорирован

    files = extract_fsnb_files(buf.getvalue())

    assert set(files) == {GESN_FILENAME, FSBC_MATERIALS_FILENAME, FSBC_MACHINES_FILENAME}
    assert files[GESN_FILENAME] == b"<base/>"


def test_extract_fsnb_files_raises_when_archive_is_missing_expected_files():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(GESN_FILENAME, b"<base/>")

    with pytest.raises(ValueError, match="ФСБЦ"):
        extract_fsnb_files(buf.getvalue())


def test_download_fsnb_archive_uses_requests_without_hitting_the_real_service():
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(content=b"zip-bytes", raise_for_status=lambda: None)
        result = download_fsnb_archive("https://example.invalid/archive.zip")

    assert result == b"zip-bytes"
    mock_get.assert_called_once_with("https://example.invalid/archive.zip", timeout=120)

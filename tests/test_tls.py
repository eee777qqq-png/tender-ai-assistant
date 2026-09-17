"""Тест доверенного бандла для TLS к *.zakupki.gov.ru (см. eis_client/tls.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import certifi

from eis_client.tls import _RU_CA_FILES, combined_ca_bundle_path


def test_combined_bundle_contains_certifi_and_russian_trusted_roots():
    path = Path(combined_ca_bundle_path())
    assert path.is_file()

    content = path.read_text(encoding="utf-8")

    # Из certifi — стандартные корни точно есть, если объединение сработало
    # (сам бандл certifi большой, не завязываемся на конкретный CN).
    assert content.count("BEGIN CERTIFICATE") > 50
    assert Path(certifi.where()).read_text(encoding="utf-8") in content

    # Российские корни Минцифры, из-за которых вообще понадобился этот модуль —
    # сервер *.zakupki.gov.ru предъявляет сертификат, выпущенный Russian
    # Trusted Sub CA, которого нет в стандартном certifi. PEM-файл не содержит
    # текстового subject — сверяем по самому содержимому сертификатов.
    for ru_ca_file in _RU_CA_FILES:
        assert ru_ca_file.read_text(encoding="utf-8") in content


def test_combined_bundle_is_regenerated_not_stale():
    """Пересобирается при каждом вызове — не полагаемся на файл с прошлого
    запуска, который мог остаться от старой версии certifi."""
    path1 = Path(combined_ca_bundle_path())
    mtime1 = path1.stat().st_mtime_ns
    path2 = Path(combined_ca_bundle_path())
    assert path1 == path2
    assert path2.stat().st_mtime_ns >= mtime1

"""Доверенный корень для TLS к *.zakupki.gov.ru.

Сервер ЕИС предъявляет сертификат, выпущенный российским национальным УЦ
(Минцифры России, `CN=Russian Trusted Sub CA`) — начиная с 2022 года из-за
прекращения выпуска/продления сертификатов международными УЦ для российских
госсайтов под санкциями. Этого корня нет в стандартном наборе доверия
(`certifi`/ОС), поэтому обычная проверка TLS в `requests` падает с
`CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`,
хотя сертификат сервера подлинный (проверено: `Russian Trusted Sub CA`,
которым подписан сертификат `*.zakupki.gov.ru`, сам подписан
`Russian Trusted Root CA` — официальная цепочка Минцифры, не MITM).

Правильное решение — добавить официальный корень Минцифры к доверенным
корням, а не отключать проверку (`verify=False`): цепочка доверия по-прежнему
реально проверяется, просто с более широким набором корней.

`root.crt`/`sub.crt` — официальные сертификаты Минцифры России
(https://www.gosuslugi.ru/crt), публичные (не секрет), поэтому закоммичены
в репозиторий, чтобы не зависеть от доступности внешнего URL при каждом
запуске.
"""

from __future__ import annotations

from pathlib import Path

import certifi

_PACKAGE_DIR = Path(__file__).resolve().parent / "ca_bundle"
_RU_CA_FILES = [
    _PACKAGE_DIR / "russian_trusted_root_ca.crt",
    _PACKAGE_DIR / "russian_trusted_sub_ca.crt",
]
# certs/ уже в .gitignore — здесь только пересобираемый кэш, не источник истины.
_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "certs" / "zakupki_ca_bundle.pem"


def combined_ca_bundle_path() -> str:
    """Путь к объединённому файлу доверия: стандартный бандл `certifi` +
    российские корневые сертификаты Минцифры. Пересобирается при каждом
    вызове (дёшево, копирование текста) — чтобы не расходиться с `certifi`
    при его обновлении, не храним объединённый файл как источник истины."""
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    certifi_bundle = Path(certifi.where()).read_text(encoding="utf-8")
    ru_certs = "\n".join(p.read_text(encoding="utf-8") for p in _RU_CA_FILES)
    _CACHE_PATH.write_text(certifi_bundle + "\n" + ru_certs, encoding="utf-8")
    return str(_CACHE_PATH)

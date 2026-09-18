"""Загрузка архива ФСНБ-2022 с fgiscs.minstroyrf.ru.

Прямой HTTP-запрос без авторизации — проверено вживую 2026-09-18 (curl,
браузер), см. `docs/agent4-ai-matching-feasibility.md`. `DEFAULT_ARCHIVE_URL`
ниже — конкретная версия набора данных на момент проверки (12.08.2026);
на странице набора «ФСНБ-2022» в разделе «Открытые данные» перечислены
ссылки на все более новые версии — эту константу нужно будет обновлять по
мере выхода новых версий (сейчас — вручную, автоматического отслеживания
версий для этого источника, в отличие от индексов, не сделано).

Обязательное условие открытой лицензии данных ФГИС ЦС (data.gov.ru/normative_base):
использование, включая коммерческое, разрешено при условии ссылки на
первоисточник — `ATTRIBUTION_NOTICE` ниже, использовать в любом
пользовательском выводе, построенном на этих данных.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import requests

DEFAULT_ARCHIVE_URL = (
    "https://fgiscs.minstroyrf.ru/api/values/GetFileContent/"
    "7f4f249c-9781-495c-9976-e795e0e8ed4e"
)

ATTRIBUTION_NOTICE = (
    "Источник данных: ФГИС ЦС (fgiscs.minstroyrf.ru), Минстрой России / "
    "ФАУ «Главгосэкспертиза России», раздел «Открытые данные», набор «ФСНБ-2022»."
)

GESN_FILENAME = "ГЭСН.xml"
FSBC_MATERIALS_FILENAME = "ФСБЦ_Мат&Оборуд.xml"
FSBC_MACHINES_FILENAME = "ФСБЦ_Маш.xml"


def download_fsnb_archive(url: str = DEFAULT_ARCHIVE_URL, timeout: int = 120) -> bytes:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def extract_fsnb_files(archive_bytes: bytes) -> dict[str, bytes]:
    """Достаёт из ZIP-архива только три файла, нужные для фундамента Агента 4
    (основной ГЭСН и обе части ФСБЦ) — остальные (ГЭСНм/мр/п/р) в этой версии
    не разбираются, см. CLAUDE.md, «Известные пробелы»."""
    wanted = {GESN_FILENAME, FSBC_MATERIALS_FILENAME, FSBC_MACHINES_FILENAME}
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(BytesIO(archive_bytes)) as zf:
        for name in zf.namelist():
            if name in wanted:
                files[name] = zf.read(name)
    missing = wanted - files.keys()
    if missing:
        raise ValueError(f"В архиве ФСНБ не нашлось ожидаемых файлов: {sorted(missing)}")
    return files

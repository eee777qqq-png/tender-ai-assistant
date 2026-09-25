"""Автообновление версий данных ФГИС ЦС — Агент 4, тем же паттерном, что
уже реализован у Агента 10 (`regulatory_updates.RegulatoryUpdateStore`):
периодическая проверка на fgiscs.minstroyrf.ru, есть ли новая версия
источника, и создание записи `PENDING` для явного подтверждения экспертом.
Никакого автоматического применения — тот же протокол контроля качества
(CLAUDE.md), что уже действует для базы расценок и законодательства.

Закрывает CLAUDE.md, «Известные пробелы»: `DEFAULT_ARCHIVE_URL`
(`fsnb_client.py`) и `CURRENT_PERIOD_ID` (`regional_pricing_client.py`)
были захардкожены на конкретную версию/квартал без автоматического
отслеживания — их приходилось перепроверять и менять вручную. Сами
константы остаются на месте (это осознанно — они и дальше служат
бутстрап-дефолтом для чистой установки, до того как `RegulatoryUpdateStore`
вообще увидел применённую версию); `get_active_archive_url()`/
`get_active_period_id()` ниже — то, чем реальный код должен пользоваться
вместо них напрямую, когда появится сам скрипт живой загрузки данных
Агента 4 (пока такого скрипта в проекте нет — см. CLAUDE.md, статус
Агента 4).

Два источника, оба зарегистрированы как `SourceType.PRICE_BASE`:

- `FSNB_ARCHIVE_SOURCE_ID` — сам архив ФСНБ-2022 (`ГЭСН.xml`+`ФСБЦ_*.xml`),
  один источник на весь проект, не зависит от региона. Проверяется через
  **`GET /api/OpenData/GetByNumber/{identificationNumber}`** — тот же
  открытый API, что отдаёт паспорт набора данных на странице «Открытые
  данные» → «ФСНБ-2022»; поле `datasetFile` в ответе — текущая
  (не архивная) версия, `datasetVersionFiles` — список прошлых версий,
  для истории, не для сравнения. Проверено вживую 2026-09-25: текущий
  `datasetFile.path` (`7f4f249c-9781-495c-9976-e795e0e8ed4e`) совпадает с
  guid в захардкоженном `DEFAULT_ARCHIVE_URL` — новых версий с 2026-08-12
  пока не выходило, автообновление проверено на «нечего обновлять», не
  только в теории на фикстуре.
- `period_source_id(region_name)` — квартальный период (`CURRENT_PERIOD_ID`)
  ФГИС ЦС, отдельно для каждого из 4 пилотных регионов (у каждого своя
  ценовая зона, `PILOT_PRICE_ZONES`) — не одна запись на все регионы сразу,
  чтобы сразу стало видно, если кварталы у разных регионов когда-нибудь
  перестанут совпадать. Использует уже существующий
  `regional_pricing_client.fetch_periods()` — первый элемент списка и есть
  самый новый период (проверено вживую 2026-09-25: `{"id": 427, "name":
  "3 квартал 2026 г."}` идёт первым для Москвы, `id` совпадает с
  захардкоженным `CURRENT_PERIOD_ID`).
"""

from __future__ import annotations

import re
from datetime import date, datetime

import requests

from regulatory_updates.models import RegulatoryVersion, SourceType
from regulatory_updates.store import RegulatoryUpdateStore

from .fsnb_client import DEFAULT_ARCHIVE_URL
from .regional_pricing_client import CURRENT_PERIOD_ID, PILOT_PRICE_ZONES, fetch_periods

OPEN_DATA_BASE_URL = "https://fgiscs.minstroyrf.ru/api/OpenData"

# Идентификатор набора данных «ФСНБ-2022» в разделе «Открытые данные» —
# именно с ведущим пробелом: так его отдаёт сам ФГИС ЦС в ответе на запрос
# со страницы (проверено вживую 2026-09-25, не опечатка в этом файле).
FSNB_IDENTIFICATION_NUMBER = " 7707082071-fsnb"

FSNB_ARCHIVE_SOURCE_ID = "fsnb_2022_archive"
FSNB_ARCHIVE_SOURCE_NAME = "ФСНБ-2022 (архив ГЭСН/ФСБЦ, fgiscs.minstroyrf.ru/opendata)"

PERIOD_SOURCE_ID_TEMPLATE = "fgiscs_period_{region_key}"

_ARCHIVE_FILENAME_DATE_RE = re.compile(r"data-(\d{8})-")
_QUARTER_LABEL_RE = re.compile(r"(\d)\s*квартал\s*(\d{4})")


def _region_key(region_name: str) -> str:
    """Тот же принцип нормализации, что `regional_pricing_parser.
    build_regulatory_version()` для `GOSR_SOURCE_ID_TEMPLATE` — "г. Москва"
    -> "Москва", "Московская область" -> "Московская_область"."""
    return region_name.replace(" ", "_").replace(".", "").replace("г_", "")


def period_source_id(region_name: str) -> str:
    return PERIOD_SOURCE_ID_TEMPLATE.format(region_key=_region_key(region_name))


def archive_url_for_guid(guid: str) -> str:
    return f"https://fgiscs.minstroyrf.ru/api/values/GetFileContent/{guid}"


def fetch_latest_fsnb_archive_version(timeout: int = 30) -> RegulatoryVersion:
    """Текущая версия набора «ФСНБ-2022» — `datasetFile` в ответе паспорта
    набора данных, не `datasetVersionFiles` (список прошлых версий)."""
    response = requests.get(
        f"{OPEN_DATA_BASE_URL}/GetByNumber/{FSNB_IDENTIFICATION_NUMBER}", timeout=timeout
    )
    response.raise_for_status()
    data = response.json()
    archive = data["datasetFile"]
    guid = archive["path"]
    filename = archive["name"]
    return RegulatoryVersion(
        source_id=FSNB_ARCHIVE_SOURCE_ID,
        version_label=filename,
        published_at=_archive_filename_date(filename) or _last_change_date(data),
        items={
            "archive_guid": guid,
            "archive_filename": filename,
            "archive_url": archive_url_for_guid(guid),
        },
    )


def _archive_filename_date(filename: str) -> date | None:
    """Имя файла — `data-ГГГГММДД-structure-...zip`, дата публикации данных
    зашита прямо в него; надёжнее, чем `lastChangeDate` паспорта набора,
    которая относится к самой странице, не обязательно к дате файла."""
    match = _ARCHIVE_FILENAME_DATE_RE.search(filename)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def _last_change_date(data: dict) -> date:
    return datetime.fromisoformat(data["lastChangeDate"]).date()


def fetch_latest_period_version(region_name: str, price_zone_id: int, timeout: int = 30) -> RegulatoryVersion:
    """Первый элемент `fetch_periods()` — эндпоинт уже отдаёт периоды от
    новых к старым (проверено вживую 2026-09-25, не предположено)."""
    periods = fetch_periods(price_zone_id, timeout=timeout)
    if not periods:
        raise ValueError(
            f"ФГИС ЦС не вернул ни одного периода для ценовой зоны {price_zone_id} ({region_name})"
        )
    latest = periods[0]
    label = latest["name"]
    return RegulatoryVersion(
        source_id=period_source_id(region_name),
        version_label=label,
        published_at=_quarter_start_date(label),
        items={"period_id": str(latest["id"]), "period_label": label},
    )


def _quarter_start_date(label: str) -> date:
    """ФГИС ЦС не публикует отдельную дату периода, только название вида
    «3 квартал 2026 г.» — первое число этого квартала используется как
    честная дата актуальности, а не сегодняшняя дата проверки (что
    искажало бы историю версий при каждом повторном запуске)."""
    match = _QUARTER_LABEL_RE.search(label)
    if not match:
        return date.today()
    quarter, year = int(match.group(1)), int(match.group(2))
    month = (quarter - 1) * 3 + 1
    return date(year, month, 1)


def check_fsnb_archive_update(store: RegulatoryUpdateStore, timeout: int = 30):
    """Возвращает `PendingUpdate`, если найдена версия, отличная от уже
    применённой (или уже обнаруженная и ждущая решения) — `None`, если
    обновлять нечего. Само по себе ничего не применяет — см. docstring
    модуля."""
    candidate = fetch_latest_fsnb_archive_version(timeout=timeout)
    return store.detect_update(SourceType.PRICE_BASE, FSNB_ARCHIVE_SOURCE_NAME, candidate)


def check_period_update(store: RegulatoryUpdateStore, region_name: str, price_zone_id: int, timeout: int = 30):
    candidate = fetch_latest_period_version(region_name, price_zone_id, timeout=timeout)
    return store.detect_update(
        SourceType.PRICE_BASE,
        f"Квартальный период сметных цен ФГИС ЦС — {region_name}",
        candidate,
    )


def check_all_period_updates(store: RegulatoryUpdateStore, timeout: int = 30):
    """Проверяет все 4 пилотных региона по очереди — не единая проверка
    "на всех сразу", чтобы одна недоступная ценовая зона не мешала
    обнаружить обновление у остальных трёх."""
    results = []
    for region_name, info in PILOT_PRICE_ZONES.items():
        update = check_period_update(store, region_name, info["price_zone_id"], timeout=timeout)  # type: ignore[arg-type]
        if update is not None:
            results.append(update)
    return results


def get_active_archive_url(store: RegulatoryUpdateStore) -> str:
    """Ссылка на архив ФСНБ-2022, которой реально должен пользоваться живой
    код Агента 4 — применённая версия из `store`, если эксперт уже хоть раз
    подтвердил какую-то через `approve_update(..., approved=True)`, иначе
    захардкоженный бутстрап-дефолт `fsnb_client.DEFAULT_ARCHIVE_URL`."""
    applied = store.get_applied_version(FSNB_ARCHIVE_SOURCE_ID)
    if applied is None:
        return DEFAULT_ARCHIVE_URL
    return applied.items["archive_url"]


def get_active_period_id(store: RegulatoryUpdateStore, region_name: str) -> int:
    """Тот же принцип, что `get_active_archive_url()`, но для квартального
    периода конкретного региона. Бутстрап-дефолт (`CURRENT_PERIOD_ID`) один
    на все 4 пилотных региона, потому что на практике кварталы у них пока
    всегда совпадали — но применённая версия хранится отдельно per-регион,
    так что если это когда-то перестанет быть так, `get_active_period_id()`
    для разных регионов сможет честно разойтись."""
    applied = store.get_applied_version(period_source_id(region_name))
    if applied is None:
        return CURRENT_PERIOD_ID
    return int(applied.items["period_id"])

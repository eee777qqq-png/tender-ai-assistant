"""Реальное подключение `SourceType.LEGISLATION` — Агент 10, 2026-09-25.

До этой правки `SourceType.LEGISLATION` существовал только в модели
(`models.py`) и в придуманных тестовых данных (`sample_versions.py`,
44-ФЗ про обеспечение) — ни один код нигде не строил `RegulatoryVersion`
из реального источника и не вызывал `store.detect_update(SourceType.
LEGISLATION, ...)` за пределами теста. Этот модуль — тот же паттерн, что
уже реализован для базы расценок (`smeta_estimator.version_watch`, Агент 4):
периодическая проверка официального источника, `PENDING`-запись с явным
diff при обнаружении изменения, применение только через `approve_update(
..., approved=True)`. Автоматического применения нет и не будет — тот же
протокол контроля качества (CLAUDE.md), что уже действует для базы расценок.

**Это НЕ значит, что сервис сам «становится бухгалтером/юристом».** Это
база знаний, которая поддерживается актуальной, чтобы (а) черновики Агента 5
(налоговые формулы) и Агента 12 (границы ответственности) не протухали со
временем сами по себе, и (б) когда в проект придёт настоящий бухгалтер или
юрист, у них будет свежая, а не устаревшая на год основа для проверки —
готовая точка сверки, не задача собрать всё заново с нуля.

Два независимых набора источников, ОБА под `SourceType.LEGISLATION` (не
заведено отдельного значения enum — по договорённости категория различается
префиксом `source_id`/текстом `source_name`, не типом). Сеть — этот модуль
(`fetch_*_version()` ниже), разбор текста в `RegulatoryVersion` — чистые
функции в `legislation_parser.py`, протестированные на реальных вырезанных
фрагментах (`tests/fixtures/nalog_*_sample.html`,
`tests/fixtures/consultant_*_sample.html`), не на выдуманном тексте:

- **`tax_*`** — бухгалтерская база: ставки НДС/УСН/налога на прибыль/НДФЛ.
  Источник — nalog.gov.ru (Федеральная налоговая служба), официальные
  страницы-справочники по каждому налогу. Не API — обычные HTML-страницы,
  но с чистым, стабильно сформулированным текстом ставок. Квартальный
  календарь проверок (`TAX_CHECK_INTERVAL_DAYS`) — налоговые ставки меняются
  нечасто (обычно раз в год с начала года), но не так редко, как сам
  Гражданский кодекс.
- **`civil_law_*`** — юридическая база: конкретные статьи ГК РФ (свобода
  договора — ст. 421, основания и пределы ответственности — ст. 401) и
  Закона «О защите прав потребителей» (ст. 16, недопустимые условия
  договора) — те самые нормы, на которые опирается черновик Агента 12
  (`docs/legal-boundary-draft.md`, раздел про ограничение ответственности).
  Источник — consultant.ru, официальный текст статьи по прямой ссылке;
  каждая такая страница несёт метку `(ред. от ДД.ММ.ГГГГ)` — она меняется
  при любой правке статьи, это и есть сигнал обновления, не разбор diff
  самого юридического текста построчно. Годовой календарь проверок
  (`LEGAL_CHECK_INTERVAL_DAYS`) — эти нормы меняются ещё реже.

**Честно не реализовано.** Страховые взносы ИП (фиксированный платёж,
ст. 430 НК РФ) — исследовано 2026-09-25: страница nalog.gov.ru/rn77/ip/
in_premip/ не публикует саму сумму в HTML-тексте, только ссылки на
скачиваемые Docx/ZIP-файлы с реквизитами по годам — не парсится тем же
способом, что остальные ставки, без отдельной работы по разбору вложений.
Не добавлено в этот модуль, чтобы не выдавать недоделанную проверку за
готовую — см. CLAUDE.md, «Известные пробелы».
"""

from __future__ import annotations

from . import legislation_parser as parser
from .legislation_client import fetch_page_text
from .models import RegulatoryVersion, SourceType
from .store import RegulatoryUpdateStore

TAX_CHECK_INTERVAL_DAYS = 90  # квартально
LEGAL_CHECK_INTERVAL_DAYS = 365  # ежегодно

# -- бухгалтерская база (nalog.gov.ru) --------------------------------------

TAX_PROFIT_RATE_OOO_URL = "https://www.nalog.gov.ru/rn77/taxation/taxes/profitul/"
TAX_VAT_RATES_URL = "https://www.nalog.gov.ru/rn77/taxation/taxes/nds/"
TAX_USN_RATES_URL = "https://www.nalog.gov.ru/rn77/taxation/taxes/usn/"
TAX_NDFL_BRACKETS_URL = "https://www.nalog.gov.ru/rn77/taxation/taxes/ndfl/"


def fetch_profit_tax_rate_version(timeout: int = 15) -> RegulatoryVersion:
    return parser.parse_profit_tax_rate(fetch_page_text(TAX_PROFIT_RATE_OOO_URL, timeout), TAX_PROFIT_RATE_OOO_URL)


def fetch_vat_rates_version(timeout: int = 15) -> RegulatoryVersion:
    return parser.parse_vat_rates(fetch_page_text(TAX_VAT_RATES_URL, timeout), TAX_VAT_RATES_URL)


def fetch_usn_rates_version(timeout: int = 15) -> RegulatoryVersion:
    return parser.parse_usn_rates(fetch_page_text(TAX_USN_RATES_URL, timeout), TAX_USN_RATES_URL)


def fetch_ndfl_brackets_version(timeout: int = 15) -> RegulatoryVersion:
    return parser.parse_ndfl_brackets(fetch_page_text(TAX_NDFL_BRACKETS_URL, timeout), TAX_NDFL_BRACKETS_URL)


def check_tax_profit_rate_ooo(store: RegulatoryUpdateStore, timeout: int = 15):
    candidate = fetch_profit_tax_rate_version(timeout=timeout)
    return store.detect_update(
        SourceType.LEGISLATION, "Налоговая ставка — налог на прибыль ООО (ст. 284 НК РФ)", candidate
    )


def check_tax_vat_rates(store: RegulatoryUpdateStore, timeout: int = 15):
    candidate = fetch_vat_rates_version(timeout=timeout)
    return store.detect_update(SourceType.LEGISLATION, "Налоговая ставка — НДС (ст. 164 НК РФ)", candidate)


def check_tax_usn_rates(store: RegulatoryUpdateStore, timeout: int = 15):
    candidate = fetch_usn_rates_version(timeout=timeout)
    return store.detect_update(SourceType.LEGISLATION, "Налоговая ставка — УСН (ст. 346.20 НК РФ)", candidate)


def check_tax_ndfl_brackets(store: RegulatoryUpdateStore, timeout: int = 15):
    candidate = fetch_ndfl_brackets_version(timeout=timeout)
    return store.detect_update(
        SourceType.LEGISLATION, "Налоговая ставка — прогрессивная шкала НДФЛ (ст. 224 НК РФ)", candidate
    )


TAX_SOURCE_IDS = (
    parser.TAX_PROFIT_RATE_OOO_SOURCE_ID,
    parser.TAX_VAT_RATES_SOURCE_ID,
    parser.TAX_USN_RATES_SOURCE_ID,
    parser.TAX_NDFL_BRACKETS_SOURCE_ID,
)

_TAX_CHECKS = (
    check_tax_profit_rate_ooo,
    check_tax_vat_rates,
    check_tax_usn_rates,
    check_tax_ndfl_brackets,
)


def check_all_tax_sources(store: RegulatoryUpdateStore, timeout: int = 15, force: bool = False):
    """Проверяет все 4 налоговых источника, каждый — только если он due по
    квартальному календарю (`TAX_CHECK_INTERVAL_DAYS`), если не передан
    `force=True`. Источник, у которого сеть упала ошибкой (сайт недоступен,
    таймаут), не отмечается проверенным — так следующий запуск повторит
    попытку, а не будет молча ждать квартал до следующего due."""
    results = []
    for check_fn, source_id in zip(_TAX_CHECKS, TAX_SOURCE_IDS):
        if not force and not store.is_due_for_check(source_id, TAX_CHECK_INTERVAL_DAYS):
            continue
        update = check_fn(store, timeout=timeout)
        store.record_check(source_id)
        if update is not None:
            results.append(update)
    return results


# -- юридическая база (consultant.ru) ----------------------------------------

CIVIL_LAW_GK_ART401_URL = (
    "https://www.consultant.ru/document/cons_doc_LAW_5142/94ebfa384dd37d9377ee7b78ab23c150ff69e5b4/"
)
CIVIL_LAW_GK_ART421_URL = (
    "https://www.consultant.ru/document/cons_doc_LAW_5142/ad08909251f4d26ebc935648e4e708a31e160348/"
)
CIVIL_LAW_ZPP_ART16_URL = (
    "https://www.consultant.ru/document/cons_doc_LAW_305/9eb0f127ead4dc57e7d0a9d4954cf264c4b3cea8/"
)


def fetch_gk_rf_art401_version(timeout: int = 15) -> RegulatoryVersion:
    return parser.parse_gk_rf_art401(fetch_page_text(CIVIL_LAW_GK_ART401_URL, timeout), CIVIL_LAW_GK_ART401_URL)


def fetch_gk_rf_art421_version(timeout: int = 15) -> RegulatoryVersion:
    return parser.parse_gk_rf_art421(fetch_page_text(CIVIL_LAW_GK_ART421_URL, timeout), CIVIL_LAW_GK_ART421_URL)


def fetch_zpp_art16_version(timeout: int = 15) -> RegulatoryVersion:
    return parser.parse_zpp_art16(fetch_page_text(CIVIL_LAW_ZPP_ART16_URL, timeout), CIVIL_LAW_ZPP_ART16_URL)


def check_gk_rf_art401(store: RegulatoryUpdateStore, timeout: int = 15):
    candidate = fetch_gk_rf_art401_version(timeout=timeout)
    return store.detect_update(
        SourceType.LEGISLATION,
        "Гражданское право — ГК РФ ст. 401 (ответственность за нарушение обязательства)",
        candidate,
    )


def check_gk_rf_art421(store: RegulatoryUpdateStore, timeout: int = 15):
    candidate = fetch_gk_rf_art421_version(timeout=timeout)
    return store.detect_update(
        SourceType.LEGISLATION, "Гражданское право — ГК РФ ст. 421 (свобода договора)", candidate
    )


def check_zpp_art16(store: RegulatoryUpdateStore, timeout: int = 15):
    candidate = fetch_zpp_art16_version(timeout=timeout)
    return store.detect_update(
        SourceType.LEGISLATION,
        "Гражданское право — Закон «О защите прав потребителей» ст. 16 (недопустимые условия договора)",
        candidate,
    )


CIVIL_LAW_SOURCE_IDS = (
    parser.CIVIL_LAW_GK_ART401_SOURCE_ID,
    parser.CIVIL_LAW_GK_ART421_SOURCE_ID,
    parser.CIVIL_LAW_ZPP_ART16_SOURCE_ID,
)

_CIVIL_LAW_CHECKS = (
    check_gk_rf_art401,
    check_gk_rf_art421,
    check_zpp_art16,
)


def check_all_civil_law_sources(store: RegulatoryUpdateStore, timeout: int = 15, force: bool = False):
    """Тот же принцип, что `check_all_tax_sources()`, но с годовым
    календарём (`LEGAL_CHECK_INTERVAL_DAYS`) — гражданское право и закон о
    защите прав потребителей меняются ещё реже, чем налоговые ставки."""
    results = []
    for check_fn, source_id in zip(_CIVIL_LAW_CHECKS, CIVIL_LAW_SOURCE_IDS):
        if not force and not store.is_due_for_check(source_id, LEGAL_CHECK_INTERVAL_DAYS):
            continue
        update = check_fn(store, timeout=timeout)
        store.record_check(source_id)
        if update is not None:
            results.append(update)
    return results

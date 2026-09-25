"""Разбор текста бухгалтерской/юридической базы (Агент 10) в
`RegulatoryVersion` — чистые функции, без сети (сеть — `legislation_client.py`).
Тот же принцип, что `smeta_estimator.fsnb_parser`/`regional_pricing_parser`
для базы расценок Агента 4: разбор протестирован на реальных, вырезанных
вживую фрагментах страниц (`tests/fixtures/nalog_*_sample.html`,
`tests/fixtures/consultant_*_sample.html`), не на выдуманном тексте.

Два набора источников — см. `legislation_watch.py` за URL и календарём
проверок:

- `parse_profit_tax_rate()`/`parse_vat_rates()`/`parse_usn_rates()`/
  `parse_ndfl_brackets()` — nalog.gov.ru, бухгалтерская база.
- `parse_gk_rf_art401()`/`parse_gk_rf_art421()`/`parse_zpp_art16()` —
  consultant.ru, юридическая база (конкретные статьи, не весь кодекс).

Каждая функция бросает `ValueError`, если не нашла то, что искала —
намеренно не тихий пропуск: разметка источника могла измениться, и тогда
нужна ручная проверка, а не молчаливо неполная/нулевая версия (тот же
принцип, что уже применяется в `fsnb_parser.py` для нераспознанных
ресурсов ГЭСН).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .models import RegulatoryVersion

# =============================================================================
# Бухгалтерская база (nalog.gov.ru)
# =============================================================================

TAX_PROFIT_RATE_OOO_SOURCE_ID = "tax_profit_rate_ooo"
TAX_VAT_RATES_SOURCE_ID = "tax_vat_rates"
TAX_USN_RATES_SOURCE_ID = "tax_usn_rates"
TAX_NDFL_BRACKETS_SOURCE_ID = "tax_ndfl_progressive_brackets"

_PROFIT_RATE_RE = re.compile(r"Основная ставка\s*(\d+)\s*%")
_PROFIT_FEDERAL_RE = re.compile(r"в федеральный бюджет\s*\(\s*(\d+)\s*%")
_PROFIT_REGIONAL_RE = re.compile(r"бюджет субъекта РФ\s*\(\s*(\d+)\s*%")

_VAT_STANDARD_RE = re.compile(r"Ставка НДС\s*(\d+)%\s*применяется во всех остальных случаях")
_VAT_REDUCED_RE = re.compile(r"По ставке НДС\s*(\d+)%\s*налогообложение производится в случаях реализации")
_VAT_USN_RE = re.compile(r"Ставка НДС в размере\s*(\d+)%\s*применяется с 01\.01\.2025")

_USN_INCOME_RE = re.compile(r"«доходы»\s*ставка составляет\s*(\d+)\s*%")
# Без якоря на запятую сразу после кавычки regex находит первое случайное
# совпадение "«доходы минус расходы»" на всей странице (оно встречается по
# ходу текста несколько раз до собственно ставки, например в абзаце про
# смену объекта налогообложения) и уезжает на "ставка составляет 6%" из
# соседнего абзаца про "доходы" — баг найден и исправлен на живом ответе
# nalog.gov.ru 2026-09-25, не только на фикстуре (см. tests/fixtures/
# nalog_usn_sample.html, которая теперь воспроизводит этот же порядок фраз).
_USN_INCOME_MINUS_EXPENSE_RE = re.compile(r"«доходы минус расходы»,\s*ставка составляет\s*(\d+)\s*%")

_NDFL_SIGNATURE_RE = re.compile(r"шкала по НДФЛ\s*\(налоговые ставки\s*([\d%/]+)\)")
_NDFL_BOTTOM_BRACKET_RE = re.compile(r"13%\s*-\s*если сумма налоговых баз за год не более\s*([\d,]+)\s*млн")
_NDFL_TOP_BRACKET_RE = re.compile(
    r"ставка в размере 22%\s*применяется, если сумма налоговых баз превышает ([\d,]+)\s*млн руб\.\s*в год"
)


def _ndfl_bracket_upper_re(rate: int) -> re.Pattern[str]:
    return re.compile(
        rf"ставка(?: в размере)? {rate}\s*%,?\s*(?:применяется,)?\s*если сумма "
        rf"налоговых баз превышает [\d,]+\s*млн руб\.\s*и равна или не более "
        rf"([\d,]+)\s*млн"
    )


def _require(match: re.Match | None, url: str, what: str) -> str:
    if match is None:
        raise ValueError(
            f"Не удалось найти {what} на странице {url} — разметка страницы могла "
            "измениться, нужна ручная проверка (не тихий пропуск)"
        )
    return match.group(1)


def parse_profit_tax_rate(text: str, url: str) -> RegulatoryVersion:
    """Ставка налога на прибыль ООО на ОСН (ст. 284 НК РФ). Подтверждено
    вживую 2026-09-25: 25% (8% федеральный + 17% региональный бюджет)."""
    rate = _require(_PROFIT_RATE_RE.search(text), url, "основную ставку налога на прибыль")
    items = {"rate_percent": rate}
    federal = _PROFIT_FEDERAL_RE.search(text)
    regional = _PROFIT_REGIONAL_RE.search(text)
    if federal:
        items["federal_budget_percent"] = federal.group(1)
    if regional:
        items["regional_budget_percent"] = regional.group(1)
    return RegulatoryVersion(
        source_id=TAX_PROFIT_RATE_OOO_SOURCE_ID,
        version_label=f"rate={rate}%",
        # Страница не публикует отдельную дату «действует с» рядом со
        # ставкой (только диапазон бюджетного распределения «2025-2030») —
        # честно используем дату проверки, не выдумываем дату вступления в
        # силу нормы из текста, которого там нет.
        published_at=date.today(),
        items=items,
    )


def parse_vat_rates(text: str, url: str) -> RegulatoryVersion:
    """Ставки НДС (ст. 164 НК РФ) — стандартная, пониженная и специальные
    для УСН. Подтверждено вживую 2026-09-25: 22% / 10% / 5% / 7%."""
    standard = _require(_VAT_STANDARD_RE.search(text), url, "стандартную ставку НДС")
    reduced = _require(_VAT_REDUCED_RE.search(text), url, "пониженную ставку НДС 10%")
    usn_rates = sorted({m.group(1) for m in _VAT_USN_RE.finditer(text)}, key=int)
    if len(usn_rates) != 2:
        raise ValueError(
            f"Ожидалось ровно 2 специальные ставки НДС для УСН на {url}, "
            f"найдено {len(usn_rates)}: {usn_rates!r} — разметка страницы могла измениться"
        )
    items = {
        "standard_percent": standard,
        "reduced_percent": reduced,
        "usn_low_percent": usn_rates[0],
        "usn_high_percent": usn_rates[1],
    }
    return RegulatoryVersion(
        source_id=TAX_VAT_RATES_SOURCE_ID,
        version_label=f"standard={standard}%",
        published_at=date.today(),
        items=items,
    )


def parse_usn_rates(text: str, url: str) -> RegulatoryVersion:
    """Базовые федеральные ставки УСН (ст. 346.20 НК РФ) — «доходы» и
    «доходы минус расходы». Подтверждено вживую 2026-09-25: 6% / 15%.
    Региональные пониженные ставки страница тоже упоминает, но они не
    отслеживаются отдельно — не входят в фиксированный список
    `TaxRegimeChoice`, который сейчас показывает клиенту сервис."""
    income = _require(_USN_INCOME_RE.search(text), url, "ставку УСН «доходы»")
    income_minus_expense = _require(
        _USN_INCOME_MINUS_EXPENSE_RE.search(text), url, "ставку УСН «доходы минус расходы»"
    )
    items = {"income_percent": income, "income_minus_expense_percent": income_minus_expense}
    return RegulatoryVersion(
        source_id=TAX_USN_RATES_SOURCE_ID,
        version_label=f"income={income}%,income_minus_expense={income_minus_expense}%",
        published_at=date.today(),
        items=items,
    )


def parse_ndfl_brackets(text: str, url: str) -> RegulatoryVersion:
    """Прогрессивная шкала НДФЛ (ст. 224 НК РФ) — та же, что использует
    `profitability_estimator.estimator._ndfl_progressive()` для ИП на ОСН.
    Подтверждено вживую 2026-09-25: 13/15/18/20/22% по порогам
    2,4/5/20/50 млн руб."""
    signature = _require(
        _NDFL_SIGNATURE_RE.search(text), url, "сводную запись шкалы НДФЛ (13%/15%/.../22%)"
    )
    items: dict[str, str] = {"rate_signature": signature}

    bottom_upper = _NDFL_BOTTOM_BRACKET_RE.search(text)
    if bottom_upper:
        items["bracket_13_upper_mln"] = bottom_upper.group(1)
    for rate in (15, 18, 20):
        match = _ndfl_bracket_upper_re(rate).search(text)
        if match:
            items[f"bracket_{rate}_upper_mln"] = match.group(1)
    top_lower = _NDFL_TOP_BRACKET_RE.search(text)
    if top_lower:
        items["bracket_22_lower_mln"] = top_lower.group(1)

    return RegulatoryVersion(
        source_id=TAX_NDFL_BRACKETS_SOURCE_ID,
        version_label=signature,
        published_at=date.today(),
        items=items,
    )


# =============================================================================
# Юридическая база (consultant.ru)
# =============================================================================

CIVIL_LAW_GK_ART401_SOURCE_ID = "civil_law_gk_rf_art401"
CIVIL_LAW_GK_ART421_SOURCE_ID = "civil_law_gk_rf_art421"
CIVIL_LAW_ZPP_ART16_SOURCE_ID = "civil_law_zpp_art16"

_EDITION_DATE_RE = re.compile(r"ред\.\s*от\s*(\d{2}\.\d{2}\.\d{4})")
_GK_401_P4_RE = re.compile(r"([^.]{0,80}умышленное нарушение обязательства ничтожно\.)")
_GK_421_P1_RE = re.compile(r"(Граждане и юридические лица свободны[^.]{0,200}\.)")
_ZPP_16_P1_RE = re.compile(r"(Недопустимыми условиями договора,[^.]{0,400}\.)")


def _parse_edition_date(raw: str) -> date:
    return datetime.strptime(raw, "%d.%m.%Y").date()


def parse_gk_rf_art401(text: str, url: str) -> RegulatoryVersion:
    """ГК РФ ст. 401 (основания ответственности за нарушение обязательства)
    — конкретно п.4, на который опирается черновик Агента 12
    (`docs/legal-boundary-draft.md`): соглашение об устранении/ограничении
    ответственности за умышленное нарушение ничтожно независимо от решения
    суда. Подтверждено вживую 2026-09-25: ред. от 10.06.2026."""
    edition = _require(_EDITION_DATE_RE.search(text), url, "дату редакции документа")
    items = {"edition_date": edition}
    p4 = _GK_401_P4_RE.search(text)
    if p4:
        items["clause_4_text"] = p4.group(1)
    return RegulatoryVersion(
        source_id=CIVIL_LAW_GK_ART401_SOURCE_ID,
        version_label=f"ред. от {edition}",
        published_at=_parse_edition_date(edition),
        items=items,
    )


def parse_gk_rf_art421(text: str, url: str) -> RegulatoryVersion:
    """ГК РФ ст. 421 (свобода договора) — п.1, на который опирается тот же
    черновик Агента 12 как основание допустимости лимита ответственности
    суммой. Подтверждено вживую 2026-09-25: ред. от 10.06.2026."""
    edition = _require(_EDITION_DATE_RE.search(text), url, "дату редакции документа")
    items = {"edition_date": edition}
    p1 = _GK_421_P1_RE.search(text)
    if p1:
        items["clause_1_text"] = p1.group(1)
    return RegulatoryVersion(
        source_id=CIVIL_LAW_GK_ART421_SOURCE_ID,
        version_label=f"ред. от {edition}",
        published_at=_parse_edition_date(edition),
        items=items,
    )


def parse_zpp_art16(text: str, url: str) -> RegulatoryVersion:
    """Закон «О защите прав потребителей», ст. 16 (недопустимые условия
    договора, ущемляющие права потребителя) — актуально, если клиентами
    сервиса окажутся физлица (см. `docs/legal-boundary-draft.md`, раздел
    «Что нужно от юриста» про пункт 5). Подтверждено вживую 2026-09-25:
    ред. от 28.12.2025."""
    edition = _require(_EDITION_DATE_RE.search(text), url, "дату редакции документа")
    items = {"edition_date": edition}
    p1 = _ZPP_16_P1_RE.search(text)
    if p1:
        items["clause_1_text"] = p1.group(1)
    return RegulatoryVersion(
        source_id=CIVIL_LAW_ZPP_ART16_SOURCE_ID,
        version_label=f"ред. от {edition}",
        published_at=_parse_edition_date(edition),
        items=items,
    )

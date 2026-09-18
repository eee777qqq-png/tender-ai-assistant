"""Разбор писем Минстроя с региональными индексами — сырой текст, не
готовые числа.

**Важная граница того, что этот парсер умеет.** Одно письмо Минстроя
содержит несколько разных приложений/таблиц на разные типы объектов и
работ (детальная разбивка по элементам прямых затрат для жилых домов/школ/
больниц и т.д., отдельно — индексы для автодорог, отдельно — ещё для
чего-то) — точное соответствие «какое из чисел что означает и для какого
типа работ его применять» в этом фундаменте **не установлено и не
проверено** (не подтверждено методикой Минстроя, только эвристикой по
тексту). Парсер просто находит **первое** упоминание названия региона в
письме и берёт одно-два числа сразу после него — сырой текст, без
интерпретации. Использовать как готовый множитель для реальной сметы до
проверки экспертом/сметчиком нельзя — см. CLAUDE.md, «Известные пробелы».

Формат результата (`dict[str, str]`) — то, что `regulatory_updates`
ожидает в `RegulatoryVersion.items`, никакой новой структуры для индексов
не заводится (уже согласовано).
"""

from __future__ import annotations

import html as html_stdlib
import re
from datetime import date

from regulatory_updates.models import RegulatoryVersion

# Ровно 4 пилотных региона (CLAUDE.md, «Пилот») — форма названия должна
# совпадать с тем, как регион назван в самом письме Минстроя.
PILOT_REGIONS: tuple[str, ...] = (
    "г. Москва",
    "Московская область",
    "Краснодарский край",
    "Ростовская область",
)

PRICE_INDEX_SOURCE_ID = "fgiscs_regional_price_index"
PRICE_INDEX_SOURCE_NAME = "Индексы изменения сметной стоимости строительства (ФГИС ЦС, по письмам Минстроя)"

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_BREAK_RE = re.compile(r"</(p|tr|table|div)\s*>|<br\s*/?>", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_TAIL_WINDOW_CHARS = 400


def _html_to_text(raw_html: str) -> str:
    text = _BLOCK_BREAK_RE.sub("\n", raw_html)
    text = _TAG_RE.sub(" ", text)
    text = html_stdlib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def parse_regional_index_values(
    raw_html: str, region_names: tuple[str, ...] = PILOT_REGIONS
) -> dict[str, str]:
    """Для каждого региона — первое вхождение в письме, следующие 1-2 числа
    после названия, как сырой текст (например `"14,94;20,32"`). Регион, для
    которого название не нашлось в письме вовсе, в результат не попадает —
    вызывающий код должен сам решить, ошибка это или нет."""
    text = _html_to_text(raw_html)
    items: dict[str, str] = {}
    for region in region_names:
        idx = text.find(region)
        if idx == -1:
            continue
        tail = text[idx + len(region) : idx + len(region) + _TAIL_WINDOW_CHARS]
        numbers = _NUMBER_RE.findall(tail)[:2]
        if numbers:
            items[region] = ";".join(numbers)
    return items


def build_regulatory_version(
    version_label: str, published_at: date, raw_html: str
) -> RegulatoryVersion:
    return RegulatoryVersion(
        source_id=PRICE_INDEX_SOURCE_ID,
        version_label=version_label,
        published_at=published_at,
        items=parse_regional_index_values(raw_html),
    )

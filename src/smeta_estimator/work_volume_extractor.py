"""Кандидаты объёма работ по конкретной закупке — Агент 4, открытый п.8 в
CLAUDE.md ("Известные пробелы"): `SmetaLineItem.work_volume` до сих пор
обязательный ручной вход, ни Агент 3, ни Агент 4 не извлекают его из
документации сами.

**Это не решение вопроса, а тот же паттерн, что уже применён к
`AbstractResource` в `material_candidates.py`** — вспомогательный список
кандидатов из таблиц документации (`document_analyst.table_reader`), не
автоматическая подстановка. Эксперт по-прежнему обязан выбрать/подтвердить
значение (`SmetaLineItem.work_volume` остаётся explicit-полем, не
становится вычисляемым) — здесь только сокращается путь "искать вручную по
всему приложению" до "выбрать из короткого списка похожих строк".

**Не проверено на реальном документе** — см. предупреждение в
`document_analyst/table_reader.py`. Формат разбора реконструирован по
типичному оформлению ведомости объёмов работ/локального сметного расчёта
(колонки вида "Наименование работ" / "Ед. изм." / "Количество"), протестирован
на придуманных, но правдоподобных таблицах — тем же путём, каким изначально
проходили Агенты 3 и 4, прежде чем на реальных документах нашлись
неучтённые варианты форматирования (см. CLAUDE.md, открытый п.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from document_analyst.table_reader import Table

from .text_matching import stem_words

# Заголовки колонок ищем по вхождению любого из этих слов (без регистра) —
# формулировки в реальных документах varies ("Наименование работ",
# "Наименование работ и затрат", "Кол-во", "Объём работ", "Ед. изм.").
_NAME_HEADER_WORDS = ("наименование", "работ")
_UNIT_HEADER_WORDS = ("ед. изм", "ед.изм", "единица измерения", "ед изм")
_QUANTITY_HEADER_WORDS = ("количество", "кол-во", "кол во", "объём", "объем")

# Число с возможным пробелом-разделителем тысяч и запятой/точкой как
# десятичным разделителем — "1 234,5", "1234.5", "250".
_NUMBER_RE = re.compile(r"^[\d\s]+([.,]\d+)?$")


@dataclass
class WorkVolumeRow:
    """Одна распознанная строка ведомости объёмов работ — название работы,
    единица измерения (как написана в документе, без нормализации к
    единицам ГЭСН — сверка единиц остаётся на эксперте), количество."""

    name: str
    unit: str
    quantity: float
    table_index: int
    row_index: int


def _parse_quantity(raw: str) -> float | None:
    cleaned = raw.replace("\xa0", " ").strip()
    if not _NUMBER_RE.match(cleaned):
        return None
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _find_header(row: list[str]) -> tuple[int, int, int] | None:
    """Индексы колонок (наименование, ед.изм., количество) в строке-
    заголовке, или `None`, если строка не похожа на заголовок такой
    таблицы (не хватает хотя бы одной из трёх колонок)."""
    name_idx = unit_idx = qty_idx = None
    for i, cell in enumerate(row):
        low = cell.lower()
        if name_idx is None and all(w in low for w in _NAME_HEADER_WORDS):
            name_idx = i
        if unit_idx is None and any(w in low for w in _UNIT_HEADER_WORDS):
            unit_idx = i
        if qty_idx is None and any(w in low for w in _QUANTITY_HEADER_WORDS):
            qty_idx = i
    if name_idx is None or unit_idx is None or qty_idx is None:
        return None
    return name_idx, unit_idx, qty_idx


def extract_work_volume_rows(tables: list[Table]) -> list[WorkVolumeRow]:
    """Сканирует каждую таблицу в поисках строки-заголовка вида
    "Наименование работ | Ед. изм. | Количество" и разбирает строки под
    ней, пока строка ещё содержит распознаваемое число в колонке
    количества. Таблица без такого заголовка — пропускается целиком, не
    считается ошибкой (в документации закупки обычно есть и другие
    таблицы — например, график исполнения, не ведомость объёмов)."""
    rows: list[WorkVolumeRow] = []
    for table_index, table in enumerate(tables):
        header_cols: tuple[int, int, int] | None = None
        for row_index, row in enumerate(table):
            if header_cols is None:
                header_cols = _find_header(row)
                continue

            name_idx, unit_idx, qty_idx = header_cols
            if max(name_idx, unit_idx, qty_idx) >= len(row):
                continue

            name = row[name_idx].strip()
            unit = row[unit_idx].strip()
            quantity = _parse_quantity(row[qty_idx])

            if not name or not unit or quantity is None:
                # Честно пропускаем — это может быть итоговая строка
                # ("Итого"), пустая строка-разделитель, или формат, который
                # этот разбор не распознаёт. Не подставляем 0.
                continue

            rows.append(
                WorkVolumeRow(
                    name=name,
                    unit=unit,
                    quantity=quantity,
                    table_index=table_index,
                    row_index=row_index,
                )
            )
    return rows


@dataclass
class WorkVolumeCandidates:
    """Кандидаты объёма работ для одной позиции сметы Агента 4 —
    ранжированный список найденных строк, не решение. Пустой `candidates`
    — честный случай: ни одна строка ведомости не совпала по словам с
    текстом позиции, эксперту всё равно придётся ввести объём вручную."""

    query_text: str
    candidates: list[WorkVolumeRow] = field(default_factory=list)


def suggest_work_volume_candidates(
    query_text: str,
    rows: list[WorkVolumeRow],
    top_n: int = 5,
) -> WorkVolumeCandidates:
    """Ранжирует уже извлечённые строки ведомости (`extract_work_volume_rows`)
    по совпадению слов их названия с `query_text` (обычно —
    `MatchResult.query_text`, тот же текст работы, что уже подбирался по
    ГЭСН/ФСБЦ Агентом 4) — тот же принцип, что `material_candidates.
    suggest_material_candidates()` для категорий `AbstractResource`."""
    query_stems = stem_words(query_text)
    if not query_stems:
        return WorkVolumeCandidates(query_text, [])

    scored: list[tuple[float, WorkVolumeRow]] = []
    for row in rows:
        overlap = query_stems & stem_words(row.name)
        if not overlap:
            continue
        scored.append((len(overlap) / len(query_stems), row))

    scored.sort(key=lambda pair: -pair[0])
    return WorkVolumeCandidates(query_text, [row for _, row in scored[:top_n]])

"""Извлечение обычного текста из .docx-приложения документации закупки —
`extract_requirements()` (Агент 3, `extractor.py`) принимает только текст,
не файл.

**Только .docx, не PDF.** Реальные приложения извещений ЕИС, которые видели
в этом проекте (тендер №0373100134626000473, 2026-09-25/26 — см. CLAUDE.md,
«Известные пробелы», п.3/10), все были .docx. PDF (например, сканы старых
документов) этот модуль не читает — понадобилась бы отдельная библиотека
(`pypdf` + OCR для сканов), не реализовано и честно не заявлено поддержанным.
"""

from __future__ import annotations

import io

from docx import Document

from .errors import AttachmentParseError


def extract_text_from_docx(content: bytes) -> str:
    """Текст всех абзацев и таблиц документа, в порядке появления — простое
    склеивание, без попытки восстановить визуальное форматирование/нумерацию
    списков (экстрактору Агента 3 нужен только текст для regex-поиска, не
    структура документа)."""
    # Проверено на практике (2026-10-03), что реально прилетает от python-docx:
    # случайные байты/пустой/усечённый файл — `zipfile.BadZipFile`; валидный
    # zip, но не .docx — `KeyError` ('[Content_Types].xml'). Ловим широко
    # (`Exception`): повреждения внутри xml-частей дают ещё и ошибки
    # lxml/ValueError — исчерпывающий список заранее не составить, а любое из
    # них для вызывающего кода означает одно: «документ не открылся».
    # «Открылся, текста нет» — это пустая строка, не ошибка.
    try:
        document = Document(io.BytesIO(content))
        parts: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        parts.append(cell.text)
    except Exception as exc:  # noqa: BLE001
        raise AttachmentParseError(f"не удалось открыть .docx: {type(exc).__name__}: {exc}") from exc

    return "\n".join(parts)

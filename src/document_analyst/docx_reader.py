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


def extract_text_from_docx(content: bytes) -> str:
    """Текст всех абзацев и таблиц документа, в порядке появления — простое
    склеивание, без попытки восстановить визуальное форматирование/нумерацию
    списков (экстрактору Агента 3 нужен только текст для regex-поиска, не
    структура документа)."""
    document = Document(io.BytesIO(content))

    parts: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)

    return "\n".join(parts)

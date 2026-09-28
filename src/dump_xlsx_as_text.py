"""Вспомогательный скрипт: распечатать содержимое .xlsx как обычный текст.

Нужен, когда владельцу проще скопировать текст из окна терминала и вставить
его в чат, чем прикреплять файл как вложение. Не часть конвейера агентов —
разовый инструмент, созданный 2026-09-28 для передачи содержимого реальных
файлов сметы в чат при сравнении Агент 4 vs Сметрикс.

Использование:

    python src/dump_xlsx_as_text.py "data/downloaded_attachments/roof/02 Обоснование НМЦК смета.xlsx"
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl


def main() -> int:
    if len(sys.argv) != 2:
        print("Использование: python src/dump_xlsx_as_text.py <путь к файлу.xlsx>")
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 1

    workbook = openpyxl.load_workbook(path, data_only=True)
    for sheet in workbook.worksheets:
        print(f"\n=== Лист: {sheet.title} ===")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                print(" | ".join(cells))

    return 0


if __name__ == "__main__":
    sys.exit(main())

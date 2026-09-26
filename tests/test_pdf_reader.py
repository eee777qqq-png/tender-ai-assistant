"""Тесты извлечения текста из PDF (`document_analyst.pdf_reader`) —
проверяют механику (несколько страниц, пустая страница без текстового
слоя = честный скан не ломает извлечение), не конкретный реальный документ
(тот проверен вручную, см. CLAUDE.md, открытый п.3)."""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from document_analyst import extract_text_from_pdf


def _build_pdf(pages_text: list[str]) -> bytes:
    """Минимальный, но валидный однострочный-на-страницу PDF (без внешних
    библиотек авторства PDF в тестовом окружении) — только ASCII-текст:
    базовый шрифт Helvetica в PDF использует WinAnsi-кодировку, кириллица
    потребовала бы отдельного шрифта с нужной кодировкой, не нужного для
    проверки механики модуля."""
    body_objs: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
    }
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages_text)))
    body_objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages_text)} >>"

    font_id = 3 + 2 * len(pages_text)
    for i, text in enumerate(pages_text):
        page_id = 3 + 2 * i
        content_id = page_id + 1
        body_objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/MediaBox [0 0 612 792] /Contents {content_id} 0 R >>"
        )
        if text:
            stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        else:
            stream = ""  # страница без текстового слоя — имитация скана
        body_objs[content_id] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    body_objs[font_id] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for obj_id in sorted(body_objs):
        offsets[obj_id] = out.tell()
        out.write(f"{obj_id} 0 obj\n{body_objs[obj_id]}\nendobj\n".encode("latin-1"))
    xref_offset = out.tell()
    max_id = max(body_objs) + 1
    out.write(f"xref\n0 {max_id}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for obj_id in range(1, max_id):
        out.write(f"{offsets[obj_id]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {max_id} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode())
    return out.getvalue()


def test_extracts_text_from_single_page():
    content = _build_pdf(["Requirement text on one page"])
    text = extract_text_from_pdf(content)
    assert "Requirement text on one page" in text


def test_joins_multiple_pages_in_order():
    content = _build_pdf(["First page", "Second page"])
    text = extract_text_from_pdf(content)
    assert text.index("First page") < text.index("Second page")


def test_scanned_page_without_text_layer_yields_no_crash_and_no_text():
    """Честная проверка находки задачи: страница без текстового слоя (как
    в скане) не должна ронять извлечение — просто ничего не добавляет."""
    content = _build_pdf(["Real text page", ""])
    text = extract_text_from_pdf(content)
    assert text == "Real text page"


def test_all_pages_scanned_yields_empty_string_not_error():
    content = _build_pdf(["", ""])
    assert extract_text_from_pdf(content) == ""

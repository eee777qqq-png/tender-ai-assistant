"""Мост Агент 1 -> Агент 3 на реальных приложениях извещения: находит
приложение нужного типа (по умолчанию — «Требование к содержанию, составу
заявки», где нашлось требование к опыту участника, см. CLAUDE.md,
«Известные пробелы», п.3, находка 2026-09-25), скачивает его через
`EISClient.download_attachment()` (найденный вживую Edwin, 2026-09-26,
способ доступа — браузерный `User-Agent`, не токен ЕСИА/API — см. заметку
в `client.py`) и извлекает требования через уже существующий
`document_analyst.extract_requirements()`, без единого изменения его логики.

Отдельный модуль, не часть `notice_parser.py` (тот — только парсинг XML,
без сети) и не часть `document_analyst` (чтобы не тянуть `eis_client` туда
и не создавать цикл импорта — `eis_client` уже зависит от
`document_analyst.models`, зависимость в обратную сторону не нужна)."""

from __future__ import annotations

from document_analyst import (
    ExtractedRequirements,
    extract_requirements,
    extract_text_from_docx,
    extract_text_from_pdf,
)

from .client import EISClient
from .notice_parser import (
    APPLICATION_REQUIREMENTS_DOC_KIND_CODE,
    NoticeAttachment,
    extract_attachments,
    find_attachment_by_doc_kind,
)

# Найдено вживую 2026-09-26 на реальной выборке из 63 строительных закупок:
# 35 (56%) приложений «Требования к заявке» — .pdf, не .docx (см. CLAUDE.md,
# открытый п.3). Оба формата встречаются на практике достаточно часто, чтобы
# поддерживать оба здесь, не только .docx.
_TEXT_EXTRACTORS = {
    "docx": extract_text_from_docx,
    "pdf": extract_text_from_pdf,
}


def _extract_text(attachment: NoticeAttachment, content: bytes) -> str:
    ext = attachment.file_name.rsplit(".", 1)[-1].lower() if "." in attachment.file_name else ""
    extractor = _TEXT_EXTRACTORS.get(ext)
    if extractor is None:
        raise ValueError(
            f"Формат вложения не поддержан: {attachment.file_name!r} "
            f"(умеем только {sorted(_TEXT_EXTRACTORS)})"
        )
    return extractor(content)


def fetch_participant_requirements(
    client: EISClient,
    xml_bytes: bytes,
    purchase_number: str,
    *,
    doc_kind_code: str = APPLICATION_REQUIREMENTS_DOC_KIND_CODE,
) -> ExtractedRequirements | None:
    """Полностью автоматический путь: извещение -> приложение -> текст ->
    `ExtractedRequirements`, без ручного скачивания через браузер. Поддержаны
    оба реально встречающихся формата приложения — .docx и .pdf (с текстовым
    слоем; сканы `pdf_reader.extract_text_from_pdf()` тихо вернёт почти
    пустым текстом, не ошибкой — см. её докстринг).

    Результат — как и любая выдача Агента 3 — не готов к использованию
    Агентом 6 без `mark_expert_reviewed()` (протокол контроля качества,
    CLAUDE.md, не снимается и не автоматизируется этой функцией).

    `None`, если в извещении нет приложения искомого типа — честно, не
    подставляет пустой `ExtractedRequirements` вместо отсутствующих данных.
    `ValueError`, если приложение искомого типа есть, но в формате, для
    которого нет читателя текста (сегодня — что-то кроме .docx/.pdf)."""
    attachment = find_attachment_by_doc_kind(extract_attachments(xml_bytes), doc_kind_code)
    if attachment is None:
        return None

    content = client.download_attachment(attachment.url)
    text = _extract_text(attachment, content)
    return extract_requirements(purchase_number, text)

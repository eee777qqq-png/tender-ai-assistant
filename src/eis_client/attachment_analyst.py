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

from document_analyst import ExtractedRequirements, extract_requirements, extract_text_from_docx

from .client import EISClient
from .notice_parser import (
    APPLICATION_REQUIREMENTS_DOC_KIND_CODE,
    extract_attachments,
    find_attachment_by_doc_kind,
)


def fetch_participant_requirements(
    client: EISClient,
    xml_bytes: bytes,
    purchase_number: str,
    *,
    doc_kind_code: str = APPLICATION_REQUIREMENTS_DOC_KIND_CODE,
) -> ExtractedRequirements | None:
    """Полностью автоматический путь: извещение -> приложение -> текст ->
    `ExtractedRequirements`, без ручного скачивания через браузер.

    Результат — как и любая выдача Агента 3 — не готов к использованию
    Агентом 6 без `mark_expert_reviewed()` (протокол контроля качества,
    CLAUDE.md, не снимается и не автоматизируется этой функцией).

    `None`, если в извещении нет приложения искомого типа — честно, не
    подставляет пустой `ExtractedRequirements` вместо отсутствующих данных."""
    attachment = find_attachment_by_doc_kind(extract_attachments(xml_bytes), doc_kind_code)
    if attachment is None:
        return None

    content = client.download_attachment(attachment.url)
    text = extract_text_from_docx(content)
    return extract_requirements(purchase_number, text)

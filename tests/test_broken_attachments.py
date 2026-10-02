"""Защита Агента 1 от битых/нестандартных вложений (2026-10-03) — на
СОЗНАТЕЛЬНО испорченных синтетических входах, не настоящих файлах закупок.
Принцип: один плохой файл — `AttachmentParseError` на уровне ЭТОГО файла,
не падение всего прогона; штатные файлы работают как раньше."""

import io
import logging
import os
import sys
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from docx import Document

from classifier import ConstructionClassifier
from document_analyst import extract_text_from_docx, extract_text_from_pdf
from document_analyst.errors import AttachmentParseError
from eis_client.attachment_analyst import fetch_participant_requirements
from eis_client.client import EISClient
from test_attachment_analyst import (
    NOTICE_WITH_APPLICATION_REQUIREMENTS,
    make_ip_config,
)
from test_client import RESPONSE_TEMPLATE, make_zip_archive
from test_pdf_reader import _build_pdf

RANDOM_BYTES = os.urandom(600)
CONSTRUCTION_XML = (
    b"<export><contract><products><product><KTRU><OKPD2><code>41.20.40.900</code></OKPD2></KTRU>"
    b"</product></products></contract></export>"
)


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# --- ZIP ---


def test_zip_random_bytes_raise_attachment_parse_error_not_crash():
    with pytest.raises(AttachmentParseError, match="не удалось открыть zip-архив"):
        EISClient._extract_xml_files(RANDOM_BYTES, source="a.zip")


def test_zip_empty_file_raises_attachment_parse_error():
    with pytest.raises(AttachmentParseError):
        EISClient._extract_xml_files(b"", source="empty.zip")


def test_zip_truncated_valid_archive_raises_attachment_parse_error():
    good = make_zip_archive({"a.xml": b"<a/>" * 500})
    with pytest.raises(AttachmentParseError):
        EISClient._extract_xml_files(good[: len(good) // 2], source="cut.zip")


def test_zip_error_message_names_the_archive():
    with pytest.raises(AttachmentParseError, match="my_archive.zip"):
        EISClient._extract_xml_files(RANDOM_BYTES, source="my_archive.zip")


def test_zip_nested_zip_finds_xml_on_any_level():
    # Реализованное поведение: вложенные zip разбираются рекурсивно (до 3
    # уровней), имя файла — «внешний.zip!внутренний.xml».
    inner = make_zip_archive({"deep.xml": b"<d/>"})
    middle = make_zip_archive({"inner.zip": inner, "mid.xml": b"<m/>"})
    outer = make_zip_archive({"middle.zip": middle, "top.xml": b"<t/>", "readme.txt": b"x"})

    files = dict(EISClient._extract_xml_files(outer))

    assert files == {
        "top.xml": b"<t/>",
        "middle.zip!mid.xml": b"<m/>",
        "middle.zip!inner.zip!deep.xml": b"<d/>",
    }


def test_zip_nested_deeper_than_limit_is_skipped_with_warning_not_error(caplog):
    blob = make_zip_archive({"deepest.xml": b"<x/>"})
    for i in range(5):  # 5 уровней вложенности > лимита 3
        blob = make_zip_archive({f"l{i}.zip": blob})
    blob = make_zip_archive({"top.xml": b"<t/>", "nest.zip": blob})

    with caplog.at_level(logging.WARNING):
        files = dict(EISClient._extract_xml_files(blob, source="deep.zip"))

    assert files == {"top.xml": b"<t/>"}
    assert "глубже" in caplog.text


def test_zip_broken_nested_zip_is_skipped_other_files_survive(caplog):
    outer = make_zip_archive({"bad.zip": RANDOM_BYTES, "ok.xml": b"<ok/>"})

    with caplog.at_level(logging.WARNING):
        files = dict(EISClient._extract_xml_files(outer, source="outer.zip"))

    assert files == {"ok.xml": b"<ok/>"}
    assert "bad.zip" in caplog.text


def test_zip_valid_archive_behaves_as_before():
    files = EISClient._extract_xml_files(make_zip_archive({"a.xml": b"<a/>", "b.txt": b"x", "C.XML": b"<c/>"}))

    assert dict(files) == {"a.xml": b"<a/>", "C.XML": b"<c/>"}


# --- Пакетная обработка: один битый архив не роняет день ---


def test_batch_one_broken_archive_does_not_stop_the_rest(caplog):
    good = make_zip_archive({"contract.xml": CONSTRUCTION_XML})
    response = RESPONSE_TEMPLATE.format(
        archive_urls="<archiveUrl>https://example.invalid/1.zip</archiveUrl>"
        "<archiveUrl>https://example.invalid/2.zip</archiveUrl>"
        "<archiveUrl>https://example.invalid/3.zip</archiveUrl>"
    )
    archives = iter([good, RANDOM_BYTES, good])

    with patch("requests.Session.post") as mock_post, patch("requests.Session.get") as mock_get:
        mock_post.return_value = MagicMock(text=response, raise_for_status=lambda: None)
        mock_get.side_effect = lambda *a, **k: MagicMock(content=next(archives), raise_for_status=lambda: None)
        client = EISClient(make_ip_config(), construction_classifier=ConstructionClassifier())
        with caplog.at_level(logging.INFO):
            documents = client.get_construction_documents(date(2026, 9, 30))

    assert len(documents) == 2  # архивы 1 и 3 разобраны, №2 пропущен
    assert "архив №2 пропущен" in caplog.text
    assert "архивов не открылось — 1" in caplog.text


def test_batch_local_archives_skip_broken_and_continue(tmp_path, caplog):
    (tmp_path / "1.zip").write_bytes(make_zip_archive({"contract.xml": CONSTRUCTION_XML}))
    (tmp_path / "2.zip").write_bytes(RANDOM_BYTES)
    (tmp_path / "3.zip").write_bytes(b"")
    (tmp_path / "4.zip").write_bytes(make_zip_archive({"contract.xml": CONSTRUCTION_XML}))
    client = EISClient(make_ip_config(), construction_classifier=ConstructionClassifier())

    with caplog.at_level(logging.ERROR):
        documents = client.get_construction_documents_from_local_archives(sorted(tmp_path.glob("*.zip")))

    assert len(documents) == 2
    assert caplog.text.count("пропущен") == 2


# --- DOCX ---


def test_docx_random_bytes_raise_attachment_parse_error():
    with pytest.raises(AttachmentParseError, match="не удалось открыть .docx"):
        extract_text_from_docx(RANDOM_BYTES)


def test_docx_empty_file_raises_attachment_parse_error():
    with pytest.raises(AttachmentParseError):
        extract_text_from_docx(b"")


def test_docx_valid_zip_but_not_docx_raises_attachment_parse_error():
    # Реально прилетает KeyError('[Content_Types].xml') — не должен утечь.
    with pytest.raises(AttachmentParseError):
        extract_text_from_docx(make_zip_archive({"a.txt": b"x"}))


def test_docx_truncated_raises_attachment_parse_error():
    good = _docx_bytes("текст")
    with pytest.raises(AttachmentParseError):
        extract_text_from_docx(good[: len(good) // 2])


def test_docx_opened_without_text_is_empty_string_not_error():
    # «Открылся, текста нет» отличим от «не открылся».
    assert extract_text_from_docx(_docx_bytes("   ")) == ""


def test_docx_valid_behaves_as_before():
    assert extract_text_from_docx(_docx_bytes("Опыт не менее 3 лет")) == "Опыт не менее 3 лет"


# --- PDF ---


def test_pdf_random_bytes_raise_attachment_parse_error():
    with pytest.raises(AttachmentParseError, match="не удалось открыть .pdf"):
        extract_text_from_pdf(RANDOM_BYTES)


def test_pdf_empty_file_raises_attachment_parse_error():
    with pytest.raises(AttachmentParseError):
        extract_text_from_pdf(b"")


def test_pdf_truncated_raises_attachment_parse_error():
    good = _build_pdf(["Hello world"])
    with pytest.raises(AttachmentParseError):
        extract_text_from_pdf(good[: len(good) // 2])


def test_pdf_header_without_structure_raises_attachment_parse_error():
    with pytest.raises(AttachmentParseError):
        extract_text_from_pdf(b"%PDF-1.4\n%%EOF")


def test_pdf_encrypted_with_password_raises_attachment_parse_error():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)

    with pytest.raises(AttachmentParseError):
        extract_text_from_pdf(buf.getvalue())


def test_pdf_valid_behaves_as_before():
    assert "Hello world" in extract_text_from_pdf(_build_pdf(["Hello world"]))


# --- Мост Агент 1 -> Агент 3: AttachmentParseError идёт наверх как есть ---


def test_fetch_participant_requirements_passes_attachment_parse_error_through_not_value_error():
    with patch("requests.Session.get") as mock_get:
        mock_get.return_value.content = RANDOM_BYTES  # «.docx» по имени, мусор по содержимому
        mock_get.return_value.raise_for_status = lambda: None
        client = EISClient(make_ip_config())

        with pytest.raises(AttachmentParseError) as info:
            fetch_participant_requirements(client, NOTICE_WITH_APPLICATION_REQUIREMENTS, "0373100134626000473")

    assert not isinstance(info.value, ValueError)  # не «формат не поддержан»


def test_fetch_participant_requirements_unsupported_extension_is_still_value_error():
    notice = NOTICE_WITH_APPLICATION_REQUIREMENTS.replace(b".docx", b".doc")
    with patch("requests.Session.get") as mock_get:
        mock_get.return_value.content = b"whatever"
        mock_get.return_value.raise_for_status = lambda: None
        client = EISClient(make_ip_config())

        with pytest.raises(ValueError, match="не поддержан"):
            fetch_participant_requirements(client, notice, "0373100134626000473")


def test_attachment_parse_error_is_reexported_from_eis_client():
    import eis_client
    from eis_client.exceptions import AttachmentParseError as FromExceptions

    assert eis_client.AttachmentParseError is AttachmentParseError is FromExceptions

"""Тесты моста Агент 1 -> Агент 3 (`eis_client.attachment_analyst`) — на
моках сети (реальный сервис не вызывается), фикстура повторяет форму
реального извещения №0373100134626000473 (см. test_notice_parser.py)."""

import io
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from docx import Document

from eis_client.attachment_analyst import fetch_participant_requirements
from eis_client.client import EISClient
from eis_client.config import EISConfig

NOTICE_WITH_APPLICATION_REQUIREMENTS = """<export>
  <epNotificationEF2020>
    <attachmentsInfo>
      <attachmentInfo>
        <fileName>Prilozhenie_3_Trebovaniya_k_zayavke.docx</fileName>
        <fileSize>123</fileSize>
        <url>https://example.invalid/44fz/filestore/public/1.0/download/priz/file.html?uid=EXAMPLE-CAR</url>
        <docKindInfo>
          <code>CAR</code>
          <name>Требование к содержанию, составу заявки на участие в закупке</name>
        </docKindInfo>
      </attachmentInfo>
    </attachmentsInfo>
  </epNotificationEF2020>
</export>""".encode("utf-8")

NOTICE_WITHOUT_APPLICATION_REQUIREMENTS = """<export>
  <epNotificationEF2020>
    <attachmentsInfo>
      <attachmentInfo>
        <fileName>Prilozhenie_1_Opisanie.docx</fileName>
        <fileSize>123</fileSize>
        <url>https://example.invalid/44fz/filestore/public/1.0/download/priz/file.html?uid=EXAMPLE-POD</url>
        <docKindInfo>
          <code>POD</code>
          <name>Описание объекта закупки</name>
        </docKindInfo>
      </attachmentInfo>
    </attachmentsInfo>
  </epNotificationEF2020>
</export>""".encode("utf-8")


def _make_docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def make_ip_config() -> EISConfig:
    return EISConfig(
        consumer_type="individual_person",
        org_region="77",
        subsystem_type="PRIZ",
        document_type44="epNotificationEF2020",
        individual_person_token="dummy-token",
    )


def test_fetch_participant_requirements_extracts_from_real_shaped_attachment():
    docx_bytes = _make_docx_bytes(
        "Участник должен иметь опыт выполнения аналогичных работ не менее 3 лет."
    )
    with patch("requests.Session.get") as mock_get:
        mock_get.return_value.content = docx_bytes
        mock_get.return_value.raise_for_status = lambda: None

        client = EISClient(make_ip_config())
        result = fetch_participant_requirements(
            client, NOTICE_WITH_APPLICATION_REQUIREMENTS, "0373100134626000473"
        )

    assert result is not None
    assert result.tender_purchase_number == "0373100134626000473"
    assert len(result.participant_requirements) == 1
    assert result.participant_requirements[0].kind == "experience"

    # Скачивание шло тем же способом, что подтверждён вживую 2026-09-26 —
    # браузерным User-Agent, не токеном ЕСИА/API.
    assert "individualPerson_token" not in mock_get.call_args.kwargs["headers"]
    assert "Mozilla" in mock_get.call_args.kwargs["headers"]["User-Agent"]


def test_fetch_participant_requirements_none_when_attachment_missing():
    client = EISClient(make_ip_config())
    with patch("requests.Session.get") as mock_get:
        result = fetch_participant_requirements(
            client, NOTICE_WITHOUT_APPLICATION_REQUIREMENTS, "0373100134626000473"
        )

    assert result is None
    mock_get.assert_not_called()


def test_fetch_participant_requirements_honestly_returns_empty_lists_when_format_unrecognized():
    """Реальная находка 2026-09-25/26 (см. CLAUDE.md, открытый п.3): формат
    "опыт X% от НМЦК" вместо "N лет" текущий extract_requirements() не
    распознаёт — мост не должен маскировать это подстановкой чего-либо."""
    docx_bytes = _make_docx_bytes(
        "Цена выполненных работ по договору должна составлять не менее "
        "20 процентов начальной (максимальной) цены контракта."
    )
    with patch("requests.Session.get") as mock_get:
        mock_get.return_value.content = docx_bytes
        mock_get.return_value.raise_for_status = lambda: None

        client = EISClient(make_ip_config())
        result = fetch_participant_requirements(
            client, NOTICE_WITH_APPLICATION_REQUIREMENTS, "0373100134626000473"
        )

    assert result is not None
    assert result.participant_requirements == []

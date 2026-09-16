"""Тесты клиента ЕИС на моках — реальный сервис не вызывается."""

import io
import sys
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classifier import ConstructionClassifier
from eis_client.client import EISClient
from eis_client.config import EISConfig
from eis_client.exceptions import EISRequestError

RESPONSE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
 <soap:Body>
  <ns2:getDocsByOrgRegionResponse xmlns:ns2="https://example.invalid/getDocsLE">
   <dataInfo>
    {archive_urls}
   </dataInfo>
  </ns2:getDocsByOrgRegionResponse>
 </soap:Body>
</soap:Envelope>"""


def make_config(tmp_path) -> EISConfig:
    cert = tmp_path / "client.pem"
    key = tmp_path / "client.key"
    cert.write_text("dummy-cert")
    key.write_text("dummy-key")
    return EISConfig(
        consumer_type="legal_entity",
        org_region="77",
        subsystem_type="RGK",
        document_type44="contract",
        client_cert=str(cert),
        client_key=str(key),
        timeout=5,
    )


def make_zip_archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_fetch_archive_urls_parses_response(tmp_path):
    config = make_config(tmp_path)
    response_xml = RESPONSE_TEMPLATE.format(
        archive_urls="<archiveUrl>https://example.invalid/a.zip</archiveUrl>"
        "<archiveUrl>https://example.invalid/b.zip</archiveUrl>"
    )

    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = MagicMock(text=response_xml, raise_for_status=lambda: None)
        client = EISClient(config)
        urls = client.fetch_archive_urls(date(2026, 9, 15))

    assert urls == ["https://example.invalid/a.zip", "https://example.invalid/b.zip"]
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["data"].decode("utf-8").count("<exactDate>") == 1


def test_fetch_archive_urls_raises_on_soap_fault(tmp_path):
    config = make_config(tmp_path)
    fault_xml = """<?xml version="1.0"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <soapenv:Fault><faultstring>bad request</faultstring></soapenv:Fault>
  </soapenv:Body>
</soapenv:Envelope>"""

    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = MagicMock(text=fault_xml, raise_for_status=lambda: None)
        client = EISClient(config)
        try:
            client.fetch_archive_urls(date(2026, 9, 15))
            assert False, "ожидалась EISRequestError"
        except EISRequestError as exc:
            assert "bad request" in str(exc)


def test_get_construction_documents_filters_by_okpd2(tmp_path):
    config = make_config(tmp_path)
    classifier = ConstructionClassifier()

    construction_doc = b"<Document><OKPD2Code>41.20.10.110</OKPD2Code></Document>"
    unrelated_doc = b"<Document><OKPD2Code>62.01.11.000</OKPD2Code></Document>"
    archive_bytes = make_zip_archive({"1.xml": construction_doc, "2.xml": unrelated_doc})

    response_xml = RESPONSE_TEMPLATE.format(
        archive_urls="<archiveUrl>https://example.invalid/a.zip</archiveUrl>"
    )

    with patch("requests.Session.post") as mock_post, patch("requests.Session.get") as mock_get:
        mock_post.return_value = MagicMock(text=response_xml, raise_for_status=lambda: None)
        mock_get.return_value = MagicMock(content=archive_bytes, raise_for_status=lambda: None)

        client = EISClient(config, construction_classifier=classifier)
        documents = client.get_construction_documents(date(2026, 9, 15))

    assert len(documents) == 1
    assert documents[0].file_name == "1.xml"
    assert documents[0].okpd2_codes == ["41.20.10.110"]


def test_get_construction_documents_requires_classifier(tmp_path):
    config = make_config(tmp_path)
    client = EISClient(config)

    try:
        client.get_construction_documents(date(2026, 9, 15))
        assert False, "ожидалась EISRequestError"
    except EISRequestError:
        pass

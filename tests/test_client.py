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


def test_get_construction_documents_extracts_reestr_number_when_present(tmp_path):
    """Реестровый номер закупки — та же эвристика (поиск по имени тега),
    что уже применяется к ОКПД2, не подтверждённая на реальных документах
    ЕИС (сервис пока не отдаёт реальные данные, см. CLAUDE.md)."""
    config = make_config(tmp_path)
    classifier = ConstructionClassifier()

    construction_doc = (
        b"<Document><ReestrNumber>0173200001426000101</ReestrNumber>"
        b"<OKPD2Code>41.20.10.110</OKPD2Code></Document>"
    )
    archive_bytes = make_zip_archive({"1.xml": construction_doc})
    response_xml = RESPONSE_TEMPLATE.format(
        archive_urls="<archiveUrl>https://example.invalid/a.zip</archiveUrl>"
    )

    with patch("requests.Session.post") as mock_post, patch("requests.Session.get") as mock_get:
        mock_post.return_value = MagicMock(text=response_xml, raise_for_status=lambda: None)
        mock_get.return_value = MagicMock(content=archive_bytes, raise_for_status=lambda: None)

        client = EISClient(config, construction_classifier=classifier)
        documents = client.get_construction_documents(date(2026, 9, 15))

    assert len(documents) == 1
    assert documents[0].reestr_number == "0173200001426000101"


def test_get_construction_documents_leaves_reestr_number_none_without_a_match(tmp_path):
    """Ни тега, похожего на реестровый номер, ни значения, похожего на него
    по формату — честный `None`, не выдуманное значение."""
    config = make_config(tmp_path)
    classifier = ConstructionClassifier()

    construction_doc = b"<Document><OKPD2Code>41.20.10.110</OKPD2Code></Document>"
    archive_bytes = make_zip_archive({"1.xml": construction_doc})
    response_xml = RESPONSE_TEMPLATE.format(
        archive_urls="<archiveUrl>https://example.invalid/a.zip</archiveUrl>"
    )

    with patch("requests.Session.post") as mock_post, patch("requests.Session.get") as mock_get:
        mock_post.return_value = MagicMock(text=response_xml, raise_for_status=lambda: None)
        mock_get.return_value = MagicMock(content=archive_bytes, raise_for_status=lambda: None)

        client = EISClient(config, construction_classifier=classifier)
        documents = client.get_construction_documents(date(2026, 9, 15))

    assert documents[0].reestr_number is None


def test_reestr_number_extraction_ignores_organization_reg_num(tmp_path):
    """`regNum` — номер регистрации ОРГАНИЗАЦИИ в примере официальной
    инструкции, не реестровый номер закупки — не должен спутаться с ним,
    даже если по формату (только цифры) похож."""
    config = make_config(tmp_path)
    classifier = ConstructionClassifier()

    construction_doc = (
        b"<Document><OrganizationInfo><RegNum>123456789012345678</RegNum></OrganizationInfo>"
        b"<OKPD2Code>41.20.10.110</OKPD2Code></Document>"
    )
    archive_bytes = make_zip_archive({"1.xml": construction_doc})
    response_xml = RESPONSE_TEMPLATE.format(
        archive_urls="<archiveUrl>https://example.invalid/a.zip</archiveUrl>"
    )

    with patch("requests.Session.post") as mock_post, patch("requests.Session.get") as mock_get:
        mock_post.return_value = MagicMock(text=response_xml, raise_for_status=lambda: None)
        mock_get.return_value = MagicMock(content=archive_bytes, raise_for_status=lambda: None)

        client = EISClient(config, construction_classifier=classifier)
        documents = client.get_construction_documents(date(2026, 9, 15))

    assert documents[0].reestr_number is None


def test_get_construction_documents_requires_classifier(tmp_path):
    config = make_config(tmp_path)
    client = EISClient(config)

    try:
        client.get_construction_documents(date(2026, 9, 15))
        assert False, "ожидалась EISRequestError"
    except EISRequestError:
        pass


def make_ip_config() -> EISConfig:
    return EISConfig(
        consumer_type="individual_person",
        org_region="77",
        subsystem_type="RGK",
        document_type44="contract",
        individual_person_token="dummy-token",
    )


def test_ip_request_uses_official_namespace_but_posts_to_physical_url():
    """Namespace конверта и адрес подключения — разные значения (обращение EIS-891228)."""
    config = make_ip_config()
    response_xml = RESPONSE_TEMPLATE.format(archive_urls="")

    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = MagicMock(text=response_xml, raise_for_status=lambda: None)
        EISClient(config).fetch_archive_urls(date(2026, 9, 21))

    posted_to = mock_post.call_args.args[0]
    body = mock_post.call_args.kwargs["data"].decode("utf-8")
    assert posted_to == "https://int.zakupki.gov.ru/eis-integration/services/getDocsIP"
    assert 'xmlns:ws="http://zakupki.gov.ru/fz44/get-docs-ip/ws"' in body
    assert "eis-integration/services/getDocsIP" not in body


def test_legal_entity_namespace_unchanged_until_confirmed(tmp_path):
    config = make_config(tmp_path)
    assert config.endpoint_url == "https://int44-ttls-cert.zakupki.gov.ru/eis-integration/services/getDocsLE"
    assert config.envelope_namespace == config.endpoint_url


# Тело ответа из docs/eis-support-evidence.txt (реальное эхо, 2026-09-22).
ECHO_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">   <soap:Header/>   <soap:Body>      <ws:getDocsByOrgRegionRequest xmlns:ws="https://int.zakupki.gov.ru/eis-integration/services/getDocsIP">         <index>            <id>x</id>            <createDateTime>2026-09-22T17:33:46.193</createDateTime>            <mode>PROD</mode>         </index>         <selectionParams>            <orgRegion>77</orgRegion>            <subsystemType>RGK</subsystemType>            <documentType44>contract</documentType44>            <periodInfo>               <exactDate>2026-09-21</exactDate>            </periodInfo>         </selectionParams>      </ws:getDocsByOrgRegionRequest>   </soap:Body></soap:Envelope>"""


def test_echo_response_raises_instead_of_returning_empty_list():
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = MagicMock(text=ECHO_RESPONSE, raise_for_status=lambda: None)
        try:
            EISClient(make_ip_config()).fetch_archive_urls(date(2026, 9, 21))
            assert False, "ожидалась EISRequestError"
        except EISRequestError as exc:
            assert "эхо" in str(exc)


def test_real_response_without_archives_returns_empty_list():
    response_xml = RESPONSE_TEMPLATE.format(archive_urls="")
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = MagicMock(text=response_xml, raise_for_status=lambda: None)
        assert EISClient(make_ip_config()).fetch_archive_urls(date(2026, 9, 21)) == []


def test_ip_archive_download_sends_token_header():
    """Раздел 7 инструкции (скриншоты Postman): GET за архивом несёт individualPerson_token."""
    url = "https://int.zakupki.gov.ru/dstore/common/download/compound?docRequestUid=a&compoundUid=b"
    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = MagicMock(content=b"zip", raise_for_status=lambda: None)
        assert EISClient(make_ip_config()).download_archive(url) == b"zip"

    assert mock_get.call_args.args[0] == url
    assert mock_get.call_args.kwargs["headers"] == {"individualPerson_token": "dummy-token"}


def test_legal_entity_archive_download_has_no_token_header(tmp_path):
    with patch("requests.Session.get") as mock_get:
        mock_get.return_value = MagicMock(content=b"zip", raise_for_status=lambda: None)
        EISClient(make_config(tmp_path)).download_archive("https://example.invalid/a.zip")

    assert mock_get.call_args.kwargs["headers"] == {}


def test_raw_archives_saved_and_unparsed_xml_counted(tmp_path, caplog):
    import logging

    archive_bytes = make_zip_archive({"ok.xml": b"<c/>", "broken.xml": b"not xml"})
    response_xml = RESPONSE_TEMPLATE.format(archive_urls="<archiveUrl>https://example.invalid/a.zip</archiveUrl>")
    raw_dir = tmp_path / "raw"

    with patch("requests.Session.post") as mock_post, patch("requests.Session.get") as mock_get:
        mock_post.return_value = MagicMock(text=response_xml, raise_for_status=lambda: None)
        mock_get.return_value = MagicMock(content=archive_bytes, raise_for_status=lambda: None)
        client = EISClient(make_ip_config(), construction_classifier=ConstructionClassifier(), raw_archive_dir=raw_dir)
        with caplog.at_level(logging.INFO):
            assert client.get_construction_documents(date(2026, 9, 21)) == []

    assert (raw_dir / "2026-09-21_01.zip").read_bytes() == archive_bytes
    assert "XML-документов в архивах — 2 (не разобрались как XML — 1)" in caplog.text


def test_find_okpd2_codes_matches_real_ktru_okpd2_path():
    """Реальная структура документа ЕИС (Edwin, 2026-09-23, запрос за 2026-09-21):
    код лежит по пути export/contract/products/product/KTRU/OKPD2/code, не в теге
    с «okpd» в собственном имени. Несколько product — коды со всех, не только первой."""
    xml = b"""<export>
      <contract>
        <products>
          <product>
            <KTRU>
              <OKPD2><code>32.50.21.150</code></OKPD2>
            </KTRU>
          </product>
          <product>
            <KTRU>
              <OKPD2><code>41.20.40.000</code></OKPD2>
            </KTRU>
          </product>
        </products>
      </contract>
    </export>"""
    assert EISClient._find_okpd2_codes(xml) == ["32.50.21.150", "41.20.40.000"]


def test_find_okpd2_codes_falls_back_to_tag_name_when_no_ktru_path():
    """Резервный способ (не подтверждён на реальных документах) — для документов
    без структуры KTRU/OKPD2/code, если такие когда-нибудь встретятся."""
    xml = b"<Document><OKPDCode>41.20.10.110</OKPDCode></Document>"
    assert EISClient._find_okpd2_codes(xml) == ["41.20.10.110"]


def test_find_okpd2_codes_deduplicates_repeated_values():
    xml = b"""<export><products>
      <product><KTRU><OKPD2><code>41.20.40.000</code></OKPD2></KTRU></product>
      <product><KTRU><OKPD2><code>41.20.40.000</code></OKPD2></KTRU></product>
    </products></export>"""
    assert EISClient._find_okpd2_codes(xml) == ["41.20.40.000"]


def test_find_reestr_number_matches_real_notification_number_path():
    """Реальная структура документа ЕИС (Edwin, 2026-09-23, контракт
    contract_2770206615726000446, стройка ОКПД2 41.20.40.900): номер извещения,
    на основании которого заключён контракт, лежит по пути
    export/contract/foundation/fcsOrder/order/notificationNumber (19 цифр)."""
    xml = b"""<export>
      <contract>
        <foundation>
          <fcsOrder>
            <order>
              <notificationNumber>2770206615726000446</notificationNumber>
            </order>
          </fcsOrder>
        </foundation>
      </contract>
    </export>"""
    assert EISClient._find_reestr_number(xml) == "2770206615726000446"


def test_find_reestr_number_ignores_unrelated_reg_num():
    """regNum — регистрационный номер ОРГАНИЗАЦИИ, не реестровый номер закупки."""
    xml = b"<Document><regNum>1234567890123</regNum></Document>"
    assert EISClient._find_reestr_number(xml) is None


def test_find_reestr_number_falls_back_to_tag_name_when_no_notification_path():
    """Резервный способ (не подтверждён на реальных документах) — для документов
    без структуры foundation/fcsOrder/order/notificationNumber, если встретятся."""
    xml = b"<Document><reestrNumber>0138300005125000006</reestrNumber></Document>"
    assert EISClient._find_reestr_number(xml) == "0138300005125000006"


def test_find_reestr_number_none_when_absent():
    xml = b"<Document><foo>bar</foo></Document>"
    assert EISClient._find_reestr_number(xml) is None


def test_find_reestr_number_matches_notice_common_info_path():
    """Извещение (не контракт) — собственный номер по пути commonInfo/purchaseNumber
    (подтверждён Edwin на реальном извещении 0373200298826000007, 2026-09-23)."""
    xml = b"<export><notification><commonInfo><purchaseNumber>0373200298826000007</purchaseNumber></commonInfo></notification></export>"
    assert EISClient._find_reestr_number(xml) == "0373200298826000007"

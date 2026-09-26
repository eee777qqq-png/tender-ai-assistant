"""Тесты извлечения полей извещения ЕИС (`eis_client.notice_parser`) —
фикстуры воспроизводят реальную структуру, подтверждённую на живых
документах 2026-09-24 (Москва, org_region=77; Ростовская область,
org_region=61), без реальных данных заказчиков."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from classifier.tender import Tender
from document_analyst.models import SecurityRequirement
from eis_client.notice_parser import (
    APPLICATION_REQUIREMENTS_DOC_KIND_CODE,
    NoticeSecurityAmounts,
    extract_attachments,
    extract_customer_name,
    extract_max_price,
    extract_name,
    extract_publish_date,
    extract_purchase_number,
    extract_region_code,
    extract_security_amounts,
    extract_submission_deadline,
    find_attachment_by_doc_kind,
    notice_document_to_tender,
    security_amounts_to_requirements,
)

# Форма — по реальному извещению epNotificationEF2020 (Москва, 2026-09-23):
# commonInfo/plannedPublishDate несёт дату с часовым поясом, без времени.
MOSCOW_NOTICE = b"""<export>
  <epNotificationEF2020>
    <commonInfo>
      <plannedPublishDate>2026-09-23+03:00</plannedPublishDate>
    </commonInfo>
    <purchaseResponsibleInfo>
      <responsibleOrgInfo>
        <INN>7701234567</INN>
      </responsibleOrgInfo>
    </purchaseResponsibleInfo>
    <notificationInfo>
      <customerRequirementsInfo>
        <customerRequirementInfo>
          <applicationGuarantee>
            <amount>9796.33</amount>
            <part>1.0</part>
          </applicationGuarantee>
          <contractGuarantee>
            <part>5.0</part>
          </contractGuarantee>
          <provisionWarranty>
            <amount>97963.32</amount>
            <part>10.0</part>
          </provisionWarranty>
        </customerRequirementInfo>
      </customerRequirementsInfo>
    </notificationInfo>
  </epNotificationEF2020>
</export>"""

# contractGuarantee/amount отсутствует у московского извещения в реальных
# данных (см. notice_parser.py) — здесь тот же случай: обеспечение
# исполнения контракта не выгружено суммой, только процентом.

# Расширенная форма — те же реальные пути, что MOSCOW_NOTICE, плюс поля,
# которые собирает `notice_document_to_tender()` (см. notice_parser.py):
# purchaseNumber (в двух местах — настоящий, в commonInfo, и обманка того же
# имени в IKZInfo, 4-значная часть кода ИКЗ, не реестровый номер), название
# объекта закупки, заказчик, НМЦК, срок подачи заявок. Название заказчика —
# вымышленное, не из реального документа (см. обсуждение анонимизации).
FULL_MOSCOW_NOTICE = """<export>
  <epNotificationEF2020>
    <commonInfo>
      <purchaseNumber>0373200138226000791</purchaseNumber>
      <plannedPublishDate>2026-09-23+03:00</plannedPublishDate>
    </commonInfo>
    <purchaseResponsibleInfo>
      <responsibleOrgInfo>
        <INN>7701234567</INN>
      </responsibleOrgInfo>
    </purchaseResponsibleInfo>
    <notificationInfo>
      <procedureInfo>
        <collectingInfo>
          <endDT>2026-10-01T10:00:00+03:00</endDT>
        </collectingInfo>
      </procedureInfo>
      <customerRequirementsInfo>
        <customerRequirementInfo>
          <customer>
            <fullName>Тестовый заказчик (пример, не реальная организация)</fullName>
          </customer>
          <contractConditionsInfo>
            <maxPriceInfo>
              <maxPrice>979633.15</maxPrice>
            </maxPriceInfo>
            <IKZInfo>
              <purchaseNumber>0035</purchaseNumber>
            </IKZInfo>
          </contractConditionsInfo>
        </customerRequirementInfo>
      </customerRequirementsInfo>
      <purchaseObjectsInfo>
        <notDrugPurchaseObjectsInfo>
          <purchaseObject>
            <name>Выполнение работ по текущему ремонту (пример)</name>
            <OKPD2>
              <OKPDCode>41.20.40.900</OKPDCode>
            </OKPD2>
          </purchaseObject>
        </notDrugPurchaseObjectsInfo>
      </purchaseObjectsInfo>
    </notificationInfo>
  </epNotificationEF2020>
</export>""".encode("utf-8")


def test_extract_publish_date_parses_date_with_timezone_suffix():
    assert extract_publish_date(MOSCOW_NOTICE) == date(2026, 9, 23)


def test_extract_publish_date_none_when_tag_absent():
    xml = b"<export><epNotificationEF2020><commonInfo/></epNotificationEF2020></export>"
    assert extract_publish_date(xml) is None


def test_extract_publish_date_none_on_unparseable_xml():
    assert extract_publish_date(b"not xml") is None


def test_extract_region_code_from_customer_inn_prefix():
    assert extract_region_code(MOSCOW_NOTICE) == "77"


def test_extract_region_code_matches_rostov_prefix():
    xml = MOSCOW_NOTICE.replace(b"7701234567", b"6154012345")
    assert extract_region_code(xml) == "61"


def test_extract_region_code_none_when_inn_absent():
    xml = b"<export><epNotificationEF2020><purchaseResponsibleInfo/></epNotificationEF2020></export>"
    assert extract_region_code(xml) is None


def test_extract_region_code_none_when_inn_not_ten_digits():
    """Отсекает явно не-ИНН значения (например, случайно совпавший по пути
    короткий код) — не подставляет региональный код наугад."""
    xml = MOSCOW_NOTICE.replace(b"7701234567", b"12345")
    assert extract_region_code(xml) is None


def test_extract_security_amounts_reads_all_three_kinds():
    amounts = extract_security_amounts(MOSCOW_NOTICE)
    assert amounts == NoticeSecurityAmounts(
        bid_amount=9796.33,
        bid_percentage=1.0,
        contract_amount=None,
        contract_percentage=5.0,
        warranty_amount=97963.32,
        warranty_percentage=10.0,
    )


def test_extract_security_amounts_reads_contract_amount_when_present():
    """Ростовский пример (2026-09-24) — в отличие от московского,
    contractGuarantee/amount присутствует."""
    xml = MOSCOW_NOTICE.replace(
        b"<contractGuarantee>\n            <part>5.0</part>\n          </contractGuarantee>",
        b"<contractGuarantee>\n            <amount>2401757.52</amount>\n            "
        b"<part>10.0</part>\n          </contractGuarantee>",
    )
    amounts = extract_security_amounts(xml)
    assert amounts.contract_amount == 2401757.52
    assert amounts.contract_percentage == 10.0


def test_extract_security_amounts_all_none_when_absent():
    xml = b"<export><epNotificationEF2020><notificationInfo/></epNotificationEF2020></export>"
    assert extract_security_amounts(xml) == NoticeSecurityAmounts()


def test_security_amounts_to_requirements_builds_all_present_kinds():
    amounts = NoticeSecurityAmounts(
        bid_amount=9796.33,
        bid_percentage=1.0,
        contract_amount=None,
        contract_percentage=5.0,
        warranty_amount=97963.32,
        warranty_percentage=10.0,
    )
    requirements = security_amounts_to_requirements(amounts)

    assert len(requirements) == 3
    by_kind = {r.kind: r for r in requirements}
    assert by_kind["bid"].amount == 9796.33
    assert by_kind["bid"].percentage == 1.0
    assert by_kind["contract"].amount is None
    assert by_kind["contract"].percentage == 5.0
    assert by_kind["warranty"].amount == 97963.32
    assert all(isinstance(r, SecurityRequirement) for r in requirements)


def test_security_amounts_to_requirements_skips_empty_kinds():
    assert security_amounts_to_requirements(NoticeSecurityAmounts()) == []


def test_extract_purchase_number_matches_common_info_not_ikz_decoy():
    """commonInfo/purchaseNumber — настоящий реестровый номер (19 цифр);
    IKZInfo/purchaseNumber — часть кода ИКЗ (4 цифры), не должна перепутаться."""
    assert extract_purchase_number(FULL_MOSCOW_NOTICE) == "0373200138226000791"


def test_extract_purchase_number_none_when_absent():
    xml = b"<export><epNotificationEF2020><commonInfo/></epNotificationEF2020></export>"
    assert extract_purchase_number(xml) is None


def test_extract_name_reads_purchase_object_name():
    assert extract_name(FULL_MOSCOW_NOTICE) == "Выполнение работ по текущему ремонту (пример)"


def test_extract_customer_name_reads_actual_customer_not_operator():
    assert extract_customer_name(FULL_MOSCOW_NOTICE) == "Тестовый заказчик (пример, не реальная организация)"


def test_extract_max_price_reads_notice_nmck():
    assert extract_max_price(FULL_MOSCOW_NOTICE) == 979633.15


def test_extract_submission_deadline_parses_datetime_to_date():
    assert extract_submission_deadline(FULL_MOSCOW_NOTICE) == date(2026, 10, 1)


def test_extract_submission_deadline_none_when_absent():
    xml = b"<export><epNotificationEF2020><notificationInfo/></epNotificationEF2020></export>"
    assert extract_submission_deadline(xml) is None


def test_notice_document_to_tender_builds_real_tender_from_notice():
    tender = notice_document_to_tender(FULL_MOSCOW_NOTICE, requires_sro=False, min_experience_years=0)

    assert tender == Tender(
        purchase_number="0373200138226000791",
        name="Выполнение работ по текущему ремонту (пример)",
        customer_name="Тестовый заказчик (пример, не реальная организация)",
        okpd2_code="41.20.40.900",
        region_code="77",
        max_price=979633.15,
        requires_sro=False,
        min_experience_years=0,
        publish_date=date(2026, 9, 23),
        submission_deadline=date(2026, 10, 1),
    )


def test_notice_document_to_tender_passes_through_sro_and_experience_args():
    tender = notice_document_to_tender(FULL_MOSCOW_NOTICE, requires_sro=True, min_experience_years=3)
    assert tender.requires_sro is True
    assert tender.min_experience_years == 3


def test_notice_document_to_tender_rejects_missing_okpd2():
    xml = FULL_MOSCOW_NOTICE.replace(b"<OKPD2>\n              <OKPDCode>41.20.40.900</OKPDCode>\n            </OKPD2>", b"")
    with pytest.raises(ValueError, match="ОКПД2"):
        notice_document_to_tender(xml, requires_sro=False, min_experience_years=0)


def test_notice_document_to_tender_rejects_missing_purchase_number():
    xml = FULL_MOSCOW_NOTICE.replace(b"<purchaseNumber>0373200138226000791</purchaseNumber>", b"")
    with pytest.raises(ValueError, match="реестровый номер"):
        notice_document_to_tender(xml, requires_sro=False, min_experience_years=0)


def test_notice_document_to_tender_rejects_missing_name():
    xml = FULL_MOSCOW_NOTICE.replace(
        "<name>Выполнение работ по текущему ремонту (пример)</name>".encode("utf-8"), b""
    )
    with pytest.raises(ValueError, match="название"):
        notice_document_to_tender(xml, requires_sro=False, min_experience_years=0)


def test_notice_document_to_tender_rejects_missing_customer_name():
    xml = FULL_MOSCOW_NOTICE.replace(
        "<fullName>Тестовый заказчик (пример, не реальная организация)</fullName>".encode("utf-8"), b""
    )
    with pytest.raises(ValueError, match="заказчика"):
        notice_document_to_tender(xml, requires_sro=False, min_experience_years=0)


def test_notice_document_to_tender_rejects_missing_max_price():
    xml = FULL_MOSCOW_NOTICE.replace(b"<maxPrice>979633.15</maxPrice>", b"")
    with pytest.raises(ValueError, match="НМЦК"):
        notice_document_to_tender(xml, requires_sro=False, min_experience_years=0)


def test_notice_document_to_tender_rejects_missing_submission_deadline():
    xml = FULL_MOSCOW_NOTICE.replace(b"<endDT>2026-10-01T10:00:00+03:00</endDT>", b"")
    with pytest.raises(ValueError, match="срок подачи"):
        notice_document_to_tender(xml, requires_sro=False, min_experience_years=0)


def test_notice_document_to_tender_rejects_unparseable_xml():
    with pytest.raises(ValueError, match="XML"):
        notice_document_to_tender(b"not xml", requires_sro=False, min_experience_years=0)


# Форма — по реальному извещению №0373100134626000473, 2026-09-25/26 (см.
# CLAUDE.md, «Известные пробелы», п.3/10): 2 приложения из реальных 4,
# fileName/url/docKindInfo — как в настоящем документе (сами значения url
# заменены на example.invalid, не настоящий uid). cryptoSigns намеренно
# опущен — extract_attachments() его не читает.
ATTACHMENTS_NOTICE = """<export>
  <epNotificationEF2020>
    <attachmentsInfo>
      <attachmentInfo>
        <publishedContentId>EXAMPLE-CONTRACT-DRAFT</publishedContentId>
        <fileName>Prilozhenie_4_Proekt_kontrakta.docx</fileName>
        <fileSize>658241</fileSize>
        <url>https://example.invalid/44fz/filestore/public/1.0/download/priz/file.html?uid=EXAMPLE-CP</url>
        <docKindInfo>
          <code>CP</code>
          <name>Проект контракта</name>
        </docKindInfo>
      </attachmentInfo>
      <attachmentInfo>
        <publishedContentId>EXAMPLE-APPLICATION-REQUIREMENTS</publishedContentId>
        <fileName>Prilozhenie_3_Trebovaniya_k_zayavke.docx</fileName>
        <fileSize>50857</fileSize>
        <url>https://example.invalid/44fz/filestore/public/1.0/download/priz/file.html?uid=EXAMPLE-CAR</url>
        <docKindInfo>
          <code>CAR</code>
          <name>Требование к содержанию, составу заявки на участие в закупке</name>
        </docKindInfo>
      </attachmentInfo>
    </attachmentsInfo>
  </epNotificationEF2020>
</export>""".encode("utf-8")


def test_extract_attachments_parses_all_fields():
    attachments = extract_attachments(ATTACHMENTS_NOTICE)
    assert len(attachments) == 2

    contract_draft, application_requirements = attachments
    assert contract_draft.file_name == "Prilozhenie_4_Proekt_kontrakta.docx"
    assert contract_draft.file_size == 658241
    assert contract_draft.doc_kind_code == "CP"
    assert contract_draft.doc_kind_name == "Проект контракта"
    assert contract_draft.url.endswith("uid=EXAMPLE-CP")

    assert application_requirements.doc_kind_code == "CAR"
    assert application_requirements.file_size == 50857


def test_extract_attachments_empty_when_no_attachments_info():
    xml = b"<export><epNotificationEF2020><commonInfo/></epNotificationEF2020></export>"
    assert extract_attachments(xml) == []


def test_extract_attachments_empty_on_unparseable_xml():
    assert extract_attachments(b"not xml") == []


def test_find_attachment_by_doc_kind_matches_application_requirements():
    attachments = extract_attachments(ATTACHMENTS_NOTICE)
    found = find_attachment_by_doc_kind(attachments, APPLICATION_REQUIREMENTS_DOC_KIND_CODE)
    assert found is not None
    assert found.file_name == "Prilozhenie_3_Trebovaniya_k_zayavke.docx"


def test_find_attachment_by_doc_kind_none_when_absent():
    attachments = extract_attachments(ATTACHMENTS_NOTICE)
    assert find_attachment_by_doc_kind(attachments, "MRJ") is None

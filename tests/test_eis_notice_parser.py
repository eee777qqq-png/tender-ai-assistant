"""Тесты извлечения полей Tender из ИЗВЕЩЕНИЯ ЕИС (не контракта).

Фрагмент структуры ниже воспроизводит реальную (подтверждённую Edwin,
2026-09-23, на документе `0373200298826000007`) — не выдуманную с нуля,
но и не сырой реальный документ (его в этой сессии нет, см. CLAUDE.md).
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eis_client.notice_parser import NoticeFields, extract_notice_fields
from eis_client.tender_adapter import notice_document_to_tender

# Тот же скелет путей, что прислал Edwin: commonInfo/purchaseNumber,
# purchaseObjectsInfo/notDrugPurchaseObjectsInfo/purchaseObject/name,
# purchaseResponsibleInfo/responsibleOrgInfo/fullName,
# notificationInfo/contractConditionsInfo/maxPriceInfo/maxPrice,
# notificationInfo/procedureInfo/collectingInfo/endDT,
# notificationInfo/requirementsInfo/requirementInfo/addRequirements/addRequirement/content.
REAL_STRUCTURE_NOTICE_XML = """<export>
  <notification>
    <commonInfo>
      <purchaseNumber>0373200298826000007</purchaseNumber>
    </commonInfo>
    <purchaseObjectsInfo>
      <notDrugPurchaseObjectsInfo>
        <purchaseObject>
          <name>Капитальный ремонт кровли школы №5</name>
        </purchaseObject>
      </notDrugPurchaseObjectsInfo>
    </purchaseObjectsInfo>
    <purchaseResponsibleInfo>
      <responsibleOrgInfo>
        <fullName>ГБОУ Школа №5</fullName>
      </responsibleOrgInfo>
    </purchaseResponsibleInfo>
    <notificationInfo>
      <contractConditionsInfo>
        <maxPriceInfo>
          <maxPrice>12345678.90</maxPrice>
        </maxPriceInfo>
      </contractConditionsInfo>
      <procedureInfo>
        <collectingInfo>
          <endDT>2026-10-05T10:00:00</endDT>
        </collectingInfo>
      </procedureInfo>
      <requirementsInfo>
        <requirementInfo>
          <addRequirements>
            <addRequirement>
              <content>Участник должен быть членом СРО в области строительства. Требуется опыт выполнения аналогичных работ не менее 3 лет.</content>
            </addRequirement>
          </addRequirements>
        </requirementInfo>
      </requirementsInfo>
    </notificationInfo>
  </notification>
</export>""".encode("utf-8")


def test_extract_notice_fields_real_structure():
    fields = extract_notice_fields(REAL_STRUCTURE_NOTICE_XML)

    assert fields.purchase_number == "0373200298826000007"
    assert fields.name == "Капитальный ремонт кровли школы №5"
    assert fields.customer_name == "ГБОУ Школа №5"
    assert fields.max_price == 12345678.90
    assert fields.submission_deadline == date(2026, 10, 5)
    assert fields.requires_sro is True
    assert fields.min_experience_years == 3
    assert len(fields.requirement_texts) == 1


def test_extract_notice_fields_no_requirement_text_gives_honest_defaults():
    xml = b"""<export><notification>
      <commonInfo><purchaseNumber>0373200298826000008</purchaseNumber></commonInfo>
    </notification></export>"""
    fields = extract_notice_fields(xml)

    assert fields.purchase_number == "0373200298826000008"
    assert fields.requires_sro is False
    assert fields.min_experience_years == 0
    assert fields.name is None
    assert fields.requirement_texts == []


def test_extract_notice_fields_unparsable_xml_returns_empty():
    assert extract_notice_fields(b"not xml") == NoticeFields()


def test_notice_document_to_tender_builds_tender():
    tender = notice_document_to_tender(
        REAL_STRUCTURE_NOTICE_XML,
        okpd2_code="41.20.40.900",
        region_code="77",
        publish_date=date(2026, 9, 21),
    )

    assert tender.purchase_number == "0373200298826000007"
    assert tender.okpd2_code == "41.20.40.900"
    assert tender.name == "Капитальный ремонт кровли школы №5"
    assert tender.customer_name == "ГБОУ Школа №5"
    assert tender.region_code == "77"
    assert tender.max_price == 12345678.90
    assert tender.requires_sro is True
    assert tender.min_experience_years == 3
    assert tender.publish_date == date(2026, 9, 21)
    assert tender.submission_deadline == date(2026, 10, 5)


def test_notice_document_to_tender_rejects_missing_purchase_number():
    xml = b"<export><notification><commonInfo/></notification></export>"
    try:
        notice_document_to_tender(xml, okpd2_code="41.20.40.900", region_code="77", publish_date=date(2026, 9, 21))
        assert False, "ожидался ValueError"
    except ValueError as exc:
        assert "Номер закупки" in str(exc)


def test_notice_document_to_tender_rejects_missing_max_price():
    xml = """<export><notification>
      <commonInfo><purchaseNumber>0373200298826000009</purchaseNumber></commonInfo>
      <purchaseObjectsInfo><notDrugPurchaseObjectsInfo><purchaseObject>
        <name>Тест</name>
      </purchaseObject></notDrugPurchaseObjectsInfo></purchaseObjectsInfo>
      <purchaseResponsibleInfo><responsibleOrgInfo><fullName>Заказчик</fullName></responsibleOrgInfo></purchaseResponsibleInfo>
    </notification></export>""".encode("utf-8")
    try:
        notice_document_to_tender(xml, okpd2_code="41.20.40.900", region_code="77", publish_date=date(2026, 9, 21))
        assert False, "ожидался ValueError"
    except ValueError as exc:
        assert "НМЦК" in str(exc)

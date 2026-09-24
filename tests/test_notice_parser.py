"""Тесты извлечения полей извещения ЕИС (`eis_client.notice_parser`) —
фикстуры воспроизводят реальную структуру, подтверждённую на живых
документах 2026-09-24 (Москва, org_region=77; Ростовская область,
org_region=61), без реальных данных заказчиков."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from document_analyst.models import SecurityRequirement
from eis_client.notice_parser import (
    NoticeSecurityAmounts,
    extract_publish_date,
    extract_region_code,
    extract_security_amounts,
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

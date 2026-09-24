import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classifier.tender import Tender
from eis_client.client import ConstructionDocument
from eis_client.store import MonitorStore
from run_monitor import build_tenders, dates_to_fetch

# Та же структура (пути тегов), что подтверждена на реальных извещениях
# 2026-09-24 (см. tests/test_notice_parser.py) — минимальный набор полей,
# нужных notice_document_to_tender().
NOTICE_XML = """<export>
  <epNotificationEF2020>
    <commonInfo>
      <purchaseNumber>0173200001426000101</purchaseNumber>
      <plannedPublishDate>2026-08-01+03:00</plannedPublishDate>
    </commonInfo>
    <purchaseResponsibleInfo>
      <responsibleOrgInfo>
        <INN>7701234567</INN>
      </responsibleOrgInfo>
    </purchaseResponsibleInfo>
    <notificationInfo>
      <procedureInfo>
        <collectingInfo>
          <endDT>2026-08-20T10:00:00+03:00</endDT>
        </collectingInfo>
      </procedureInfo>
      <customerRequirementsInfo>
        <customerRequirementInfo>
          <customer>
            <fullName>Тестовый заказчик (пример)</fullName>
          </customer>
          <contractConditionsInfo>
            <maxPriceInfo>
              <maxPrice>8000000</maxPrice>
            </maxPriceInfo>
          </contractConditionsInfo>
        </customerRequirementInfo>
      </customerRequirementsInfo>
      <purchaseObjectsInfo>
        <notDrugPurchaseObjectsInfo>
          <purchaseObject>
            <name>Капитальный ремонт кровли (пример)</name>
            <OKPD2>
              <OKPDCode>43.91.19.110</OKPDCode>
            </OKPD2>
          </purchaseObject>
        </notDrugPurchaseObjectsInfo>
      </purchaseObjectsInfo>
    </notificationInfo>
  </epNotificationEF2020>
</export>""".encode("utf-8")


def _sample_tender(purchase_number: str = "0173200001426000101") -> Tender:
    return Tender(
        purchase_number=purchase_number,
        name="Капитальный ремонт кровли (пример)",
        customer_name="Тестовый заказчик (пример)",
        okpd2_code="43.91.19.110",
        region_code="77",
        max_price=8_000_000,
        requires_sro=False,
        min_experience_years=0,
        publish_date=date(2026, 8, 1),
        submission_deadline=date(2026, 8, 20),
    )


def test_new_store_has_no_fetched_dates(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    assert store.last_fetched_date() is None
    assert not store.is_date_fetched(date(2026, 9, 15))


def test_save_results_marks_date_fetched_and_stores_documents(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    docs = [
        ConstructionDocument(archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["41.20"]),
        ConstructionDocument(archive_url="https://x/a.zip", file_name="2.xml", okpd2_codes=["43.99"]),
    ]

    store.save_results(date(2026, 9, 15), docs)

    assert store.is_date_fetched(date(2026, 9, 15))
    assert store.last_fetched_date() == date(2026, 9, 15)
    stored = store.all_documents()
    assert len(stored) == 2
    assert stored[0].okpd2_codes == ["41.20"]


def test_save_results_with_empty_list_still_marks_date_fetched(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    store.save_results(date(2026, 9, 15), [])
    assert store.is_date_fetched(date(2026, 9, 15))
    assert store.all_documents() == []


def test_save_results_is_idempotent(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    doc = ConstructionDocument(archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["41.20"])

    store.save_results(date(2026, 9, 15), [doc])
    store.save_results(date(2026, 9, 15), [doc])

    assert len(store.all_documents()) == 1


def test_dates_to_fetch_starts_from_override_when_store_empty(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    start = yesterday - timedelta(days=2)

    result = dates_to_fetch(store, start)

    assert result[0] == start
    assert result[-1] == yesterday
    assert len(result) == 3


def test_dates_to_fetch_continues_after_last_fetched(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    already_fetched = yesterday - timedelta(days=1)
    store.save_results(already_fetched, [])

    result = dates_to_fetch(store, None)

    assert result == [yesterday]


def test_dates_to_fetch_empty_when_already_up_to_date(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    yesterday = date.today() - timedelta(days=1)
    store.save_results(yesterday, [])

    result = dates_to_fetch(store, None)

    assert result == []


def test_save_tenders_and_read_back(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    tender = _sample_tender()

    store.save_tenders(date(2026, 9, 24), [tender], sro_experience_verified=False)

    stored = store.all_tenders()
    assert len(stored) == 1
    assert stored[0].fetch_date == date(2026, 9, 24)
    assert stored[0].tender == tender
    assert stored[0].sro_experience_verified is False


def test_save_tenders_is_idempotent_by_purchase_number(tmp_path):
    store = MonitorStore(tmp_path / "monitor.sqlite3")
    tender = _sample_tender()

    store.save_tenders(date(2026, 9, 24), [tender])
    store.save_tenders(date(2026, 9, 24), [tender])

    assert len(store.all_tenders()) == 1


def test_build_tenders_builds_from_raw_xml():
    document = ConstructionDocument(
        archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["43.91.19.110"], raw_xml=NOTICE_XML
    )

    tenders, skipped = build_tenders([document])

    assert skipped == 0
    assert len(tenders) == 1
    assert tenders[0].purchase_number == "0173200001426000101"
    assert tenders[0].requires_sro is False
    assert tenders[0].min_experience_years == 0


def test_build_tenders_skips_document_without_raw_xml():
    document = ConstructionDocument(archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["43.91"])

    tenders, skipped = build_tenders([document])

    assert tenders == []
    assert skipped == 1


def test_build_tenders_skips_document_that_fails_to_parse_as_notice():
    """Например, документ из реестра КОНТРАКТОВ — другая структура,
    notice_document_to_tender() честно откажет, не упадёт молча."""
    contract_like_xml = b"<export><contract><foundation/></contract></export>"
    document = ConstructionDocument(
        archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["43.91"], raw_xml=contract_like_xml
    )

    tenders, skipped = build_tenders([document])

    assert tenders == []
    assert skipped == 1


def test_build_tenders_continues_after_one_bad_document():
    good = ConstructionDocument(
        archive_url="https://x/a.zip", file_name="1.xml", okpd2_codes=["43.91.19.110"], raw_xml=NOTICE_XML
    )
    bad = ConstructionDocument(archive_url="https://x/a.zip", file_name="2.xml", okpd2_codes=["43.91"])

    tenders, skipped = build_tenders([bad, good])

    assert skipped == 1
    assert len(tenders) == 1
    assert tenders[0].purchase_number == "0173200001426000101"

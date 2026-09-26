"""Сквозной прогон ВСЕЙ цепочки 2 -> 3 -> 6 -> 7 -> 4 -> 5 -> 8, но —
в отличие от `test_end_to_end_pipeline.py` — с `Tender`, построенным
`eis_client.notice_document_to_tender()` из XML в реальной структуре
извещения ЕИС (подтверждённой на живых документах 2026-09-24), а не взятым
из выдуманного `SAMPLE_TENDERS`.

Это прямое доказательство того, что разрыв Агент 1 -> Агент 2 — не только
теоретически устранён (есть код), а реально прогоняется через оставшуюся
часть конвейера без модификаций: `notice_document_to_tender()` возвращает
обычный `classifier.tender.Tender`, дальше он неотличим от любого другого.

Фикстура ниже — НЕ настоящий документ (см. обсуждение анонимизации данных
ЕИС в этой сессии): те же пути/структура, что подтверждены на реальных
извещениях, но название/заказчик/цены взяты из уже существующего
`classifier.sample_tenders` (тендер "0173200001426000101", капремонт кровли
школы №5) — чтобы переиспользовать существующий текст документации Агента 3
(`document_analyst.sample_documents.SAMPLE_DOCUMENTS`) и фикстуры Агента 4,
не изобретать новые. Живой прогон на НАСТОЯЩИХ данных ЕИС (не фикстуре) —
`src/match_real_notices.py`, проверено вручную на 29 реальных извещениях
по Москве и 9 по Ростовской области, 2026-09-24 (см. CLAUDE.md).

`requires_sro=True, min_experience_years=2` передаются `notice_document_to_tender()`
явными параметрами (не из XML — см. её докстринг: это поле не нашлось ни в
одном из 463 проверенных реальных извещений), подобраны так же, как в
исходном `SAMPLE_TENDERS`, чтобы результат матчинга был сопоставим.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classifier import ConstructionClassifier, match_profile_to_tender
from client_consultant import build_client_summary, render_summary_text
from completeness_check import check_completeness
from document_analyst import extract_requirements
from document_analyst.sample_documents import SAMPLE_DOCUMENTS
from document_assembler import assemble_document_package
from eis_client import notice_document_to_tender
from onboarding import (
    Capacity,
    ClientProfile,
    CompletedContract,
    FinancialReadiness,
    LegalInfo,
    PermitsExperience,
    TaxRegimeChoice,
    validate_profile,
)
from profitability_estimator import estimate_profitability
from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog
from smeta_estimator import (
    AGENT_NAME as AGENT_4_NAME,
    SmetaLineItem,
    apply_prices,
    build_cost_estimate,
    match_work_item,
    parse_current_prices_json,
    parse_fsbc_machine_labour_xml,
    parse_fsbc_machines_xml,
    parse_fsbc_materials_xml,
    parse_gesn_xml,
    parse_gosr_workbook,
    parse_worker_salary_registry,
    price_candidates_for_region,
    review_match_result,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SMETA_REGION_NAME = "г. Москва"
SMETA_PERIOD_LABEL = "3 квартал 2026 г."

# Та же структура (пути тегов), что подтверждена на реальных извещениях
# 2026-09-24 — значения подобраны под уже существующий SAMPLE_TENDERS
# ("0173200001426000101", капремонт кровли школы №5), не взяты из
# реального документа заказчика.
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
            <fullName>ГКУ г. Москвы «Дирекция капитального ремонта»</fullName>
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
            <name>Капитальный ремонт кровли здания школы №5</name>
            <OKPD2>
              <OKPDCode>43.91.19.110</OKPDCode>
            </OKPD2>
          </purchaseObject>
        </notDrugPurchaseObjectsInfo>
      </purchaseObjectsInfo>
    </notificationInfo>
  </epNotificationEF2020>
</export>""".encode("utf-8")


def _build_ready_profile() -> ClientProfile:
    profile = ClientProfile(
        client_id="e2e-client-from-notice",
        region_codes=["77"],
        legal=LegalInfo(
            org_name="ООО СтройМастер",
            inn="7701234567",
            ogrn="1027700132195",
            legal_address="г. Москва, ул. Примерная, д. 1",
            contact_person="Иванов Иван",
            phone="+79991234567",
            email="info@example.ru",
        ),
        permits_experience=PermitsExperience(
            sro_membership=True,
            sro_number="СРО-С-123-456",
            completed_contracts=[
                CompletedContract(
                    object_name="Капремонт школы №5", customer="ДепОбр", amount=5_000_000, year=2024
                )
            ],
            years_of_experience=5,
        ),
        capacity=Capacity(staff_count=15, own_workforce_description="15 штатных рабочих"),
        financial=FinancialReadiness(
            tax_regime=TaxRegimeChoice.USN_6_NO_VAT,
            avg_annual_revenue=50_000_000,
            working_capital=3_000_000,
            bank_guarantee_available=True,
        ),
    )
    validate_profile(profile)
    profile.submit_expert_review(reviewer="Edwin", approved=True)
    profile.mark_ready()
    return profile


def test_pipeline_from_real_notice_structure_through_to_client_summary():
    profile = _build_ready_profile()

    # Агент 1 -> Агент 2: Tender строится из XML в реальной структуре
    # извещения, не берётся из SAMPLE_TENDERS.
    tender = notice_document_to_tender(NOTICE_XML, requires_sro=True, min_experience_years=2)

    print(f"\n=== Агент 1 -> Агент 2: Tender из извещения ===")
    print(f"  {tender.purchase_number} — {tender.name}")
    print(f"  Заказчик: {tender.customer_name}")
    print(f"  НМЦК={tender.max_price:,.0f} руб., регион={tender.region_code}, "
          f"срок подачи={tender.submission_deadline}")

    assert tender.purchase_number == "0173200001426000101"
    assert tender.okpd2_code == "43.91.19.110"
    assert tender.region_code == "77"
    assert tender.max_price == 8_000_000
    assert tender.publish_date == date(2026, 8, 1)
    assert tender.submission_deadline == date(2026, 8, 20)

    # Агент 2 — классификатор
    classifier = ConstructionClassifier()
    match = match_profile_to_tender(profile, tender, classifier)

    print(f"\n=== Агент 2: Классификатор ===")
    for c in match.criteria:
        print(f"  [{'OK' if c.passed else 'FAIL'}] {c.name}: {c.message}")
    print(f"  Итог: {'ПОДХОДИТ' if match.is_match else 'НЕ ПОДХОДИТ'} (score={match.score:.2f})")

    assert match.is_match, f"Профиль подобран так, чтобы совпасть: {match.failed_reasons}"

    # Агент 3 — аналитик документации (тот же текст, что и в
    # test_end_to_end_pipeline.py — извещение само по себе не источник
    # текста документации, это отдельный документ закупки)
    extracted = extract_requirements(tender.purchase_number, SAMPLE_DOCUMENTS[tender.purchase_number])
    extracted.mark_expert_reviewed(reviewer="Edwin")

    # Агент 6 — сборщик документов
    package = assemble_document_package(profile, tender, extracted_requirements=extracted)

    # Агент 7 — проверка комплектности
    result = check_completeness(package, tender)
    print(f"\n=== Агент 7: Проверка комплектности ===")
    print(f"  Статус: {result.status.value}")
    assert result.is_pass()
    assert result.missing_required_fields == []

    # Агент 4 — сметчик (те же реальные фрагменты ФГИС ЦС по Москве, что и
    # в test_end_to_end_pipeline.py)
    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    resource_base_prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(catalog, resource_base_prices)
    gosr_index = parse_gosr_workbook((FIXTURES / "gosr_moscow_q3_2026_sample.xlsx").read_bytes())
    current_prices = {
        **parse_current_prices_json((FIXTURES / "current_prices_moscow_machines_sample.json").read_bytes()),
        **parse_worker_salary_registry((FIXTURES / "worker_salary_moscow_sample.json").read_bytes()),
    }
    machine_labour = parse_fsbc_machine_labour_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes())

    match_result = match_work_item(
        catalog, "устройство кровли на битумной мастике с защитным слоем из гравия", tender.purchase_number
    )
    match_result.candidates = price_candidates_for_region(
        match_result.candidates,
        region_name=SMETA_REGION_NAME,
        period_label=SMETA_PERIOD_LABEL,
        current_prices=current_prices,
        gosr_index=gosr_index,
        resource_base_prices=resource_base_prices,
        machine_labour=machine_labour,
    )
    top = match_result.top_candidate()

    tracker = AuditReadinessTracker(AGENT_4_NAME)
    discrepancy_log = CategorizedDiscrepancyLog()
    review_match_result(
        match_result, reviewer="Edwin", selected_code=top.code, tracker=tracker, discrepancy_log=discrepancy_log
    )

    smeta_result = build_cost_estimate(
        [SmetaLineItem(match_result=match_result, work_volume=8.5)],
        as_of_date=date(2026, 9, 18),
        region_name=SMETA_REGION_NAME,
        period_label=SMETA_PERIOD_LABEL,
    )
    assert smeta_result.total_cost > 0

    # Агент 5 — оценка выгоды
    cost_estimate = smeta_result.to_cost_estimate()
    profitability = estimate_profitability(profile, tender, cost_estimate, extracted)

    print(f"\n=== Агент 5: Оценка выгоды ===")
    print(f"  НМЦК={profitability.max_price:,.0f} руб., себестоимость={profitability.cost_estimate:,.2f} руб.")
    print(f"  Маржа: {profitability.margin:,.2f} руб.")

    assert profitability.margin is not None
    assert profitability.max_price == tender.max_price

    # Агент 8 — консультант для клиента
    summary = build_client_summary(match, result, package, profitability)

    print(f"\n=== Агент 8: Консультант для клиента ===")
    print(render_summary_text(summary))

    assert summary.tender_fits
    assert summary.package_ready
    assert summary.missing_documents == []
    assert summary.profitability is not None
    assert summary.profitability.margin == profitability.margin
    assert "Ожидаемая выгода" in render_summary_text(summary)

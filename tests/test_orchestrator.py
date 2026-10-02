"""Агент 13 (оркестратор) — по отдельному тесту на каждое стоп-условие
(`orchestrator/stop_rules.py`), плюс сквозной проход до READY_FOR_CLIENT.

Данные — те же, что в `test_end_to_end_pipeline_from_notice.py`: Tender из
XML в реальной структуре извещения ЕИС, текст документации Агента 3 из
`sample_documents`, реальные фрагменты ФГИС ЦС для сметы Агента 4."""

import copy
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from document_analyst import extract_requirements
from document_analyst.models import ParticipantRequirement
from document_analyst.sample_documents import SAMPLE_DOCUMENTS
from eis_client import notice_document_to_tender
from onboarding import CompletedContract
from orchestrator import (
    STOP_RULES,
    PipelineInputs,
    RunOutcome,
    StepStatus,
    record_expert_signoff,
    route_regulatory_update,
    run_tender_pipeline,
)
from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog
from quality_control.expert_review import ExpertReviewStore
from regulatory_updates import RegulatoryUpdateStore
from regulatory_updates.models import RegulatoryVersion, SourceType
from smeta_estimator import (
    SmetaLineItem,
    apply_prices,
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
from test_end_to_end_pipeline_from_notice import NOTICE_XML, _build_ready_profile

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REGION, PERIOD = "г. Москва", "3 квартал 2026 г."


def _tender():
    return notice_document_to_tender(NOTICE_XML, requires_sro=True, min_experience_years=2)


def _reviewed_requirements(tender):
    extracted = extract_requirements(tender.purchase_number, SAMPLE_DOCUMENTS[tender.purchase_number])
    extracted.mark_expert_reviewed(reviewer="Edwin")
    return extracted


def _smeta_items(tender, reviewed: bool):
    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    base = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(catalog, base)
    match = match_work_item(catalog, "устройство кровли на битумной мастике с защитным слоем из гравия", tender.purchase_number)
    match.candidates = price_candidates_for_region(
        match.candidates,
        region_name=REGION,
        period_label=PERIOD,
        current_prices={
            **parse_current_prices_json((FIXTURES / "current_prices_moscow_machines_sample.json").read_bytes()),
            **parse_worker_salary_registry((FIXTURES / "worker_salary_moscow_sample.json").read_bytes()),
        },
        gosr_index=parse_gosr_workbook((FIXTURES / "gosr_moscow_q3_2026_sample.xlsx").read_bytes()),
        resource_base_prices=base,
        machine_labour=parse_fsbc_machine_labour_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    )
    if reviewed:
        review_match_result(
            match,
            reviewer="Edwin",
            selected_code=match.top_candidate().code,
            tracker=AuditReadinessTracker("agent_4_smeta_estimator"),
            discrepancy_log=CategorizedDiscrepancyLog(),
        )
    return [SmetaLineItem(match_result=match, work_volume=8.5)]


@pytest.fixture
def store(tmp_path):
    return ExpertReviewStore(tmp_path / "reviews.sqlite3")


def _inputs(store, **overrides):
    tender = _tender()
    base = dict(
        profile=_build_ready_profile(),
        tender=tender,
        extracted_requirements=_reviewed_requirements(tender),
        smeta_line_items=_smeta_items(tender, reviewed=True),
        smeta_region_name=REGION,
        smeta_period_label=PERIOD,
        smeta_as_of_date=date(2026, 10, 3),
        signoff_store=store,
    )
    base.update(overrides)
    return PipelineInputs(**base)


def _sign_package(store, run):
    assert run.stop.rule.rule_id == "R3_AGENT6_EXPERT_SIGNOFF"
    record_expert_signoff(store, run.stop.signoff_key, reviewer="Edwin", approved=True)


# --- Сквозной проход ---


def test_full_chain_reaches_client_only_after_package_signoff(store):
    first = run_tender_pipeline(_inputs(store))
    assert first.outcome == RunOutcome.AWAITING_EXPERT
    assert first.client_summary is None  # к клиенту ничего не ушло
    _sign_package(store, first)

    second = run_tender_pipeline(_inputs(store))

    assert second.outcome == RunOutcome.READY_FOR_CLIENT, second.render_log()
    assert second.client_summary is not None
    assert second.client_summary.profitability is not None
    assert [s.step for s in second.steps] == [
        "agent_1_tender",
        "agent_2_coarse",
        "agent_3_documentation",
        "agent_2_final",
        "agent_11_profile_gate",
        "agent_6_package",
        "agent_7_completeness",
        "agent_4_smeta",
        "agent_5_profitability",
        "release_gate_agent_6",
        "agent_8_client_summary",
    ]


# --- R1: Агент 3 без подтверждения эксперта ---


def test_r1_unreviewed_agent3_output_stops_and_hands_it_to_expert(store):
    tender = _tender()
    run = run_tender_pipeline(
        _inputs(store, tender=tender, extracted_requirements=None, documentation_text=SAMPLE_DOCUMENTS[tender.purchase_number])
    )

    assert run.stop.rule.rule_id == "R1_AGENT3_EXPERT_REVIEW"
    assert run.stop.step == "agent_3_documentation"
    assert run.stop.artifact is run.extracted_requirements  # выдано эксперту на проверку
    assert run.package is None and run.client_summary is None


# --- R2: позиции сметы Агента 4 без подтверждения ---


def test_r2_unreviewed_smeta_position_stops_before_client(store):
    tender = _tender()
    run = run_tender_pipeline(_inputs(store, tender=tender, smeta_line_items=_smeta_items(tender, reviewed=False)))

    assert run.stop.rule.rule_id == "R2_AGENT4_EXPERT_REVIEW"
    assert run.stop.details  # перечислены конкретные позиции
    assert run.smeta_result is None and run.client_summary is None


# --- R3: пакет Агента 6 — подтверждение привязано к содержимому ---


def test_r3_package_without_signoff_is_not_released(store):
    run = run_tender_pipeline(_inputs(store))

    assert run.stop.rule.rule_id == "R3_AGENT6_EXPERT_SIGNOFF"
    assert run.stop.signoff_key is not None
    assert run.client_summary is None


def test_r3_signoff_does_not_survive_package_change(store):
    _sign_package(store, run_tender_pipeline(_inputs(store)))

    changed = _inputs(store)
    changed.profile.legal.phone = "+79990000000"  # профиль изменился -> пакет другой
    run = run_tender_pipeline(changed)

    assert run.stop.rule.rule_id == "R3_AGENT6_EXPERT_SIGNOFF"
    assert "изменилось" in run.stop.reason


def test_r3_rejected_package_stays_blocked(store):
    first = run_tender_pipeline(_inputs(store))
    record_expert_signoff(store, first.stop.signoff_key, reviewer="Edwin", approved=False, reason="неверный ИНН")

    run = run_tender_pipeline(_inputs(store))

    assert run.stop.rule.rule_id == "R3_AGENT6_EXPERT_SIGNOFF"
    assert "неверный ИНН" in run.stop.reason


def test_r3_without_signoff_store_never_releases():
    run = run_tender_pipeline(_inputs(None))

    assert run.stop.rule.rule_id == "R3_AGENT6_EXPERT_SIGNOFF"


# --- R4: профиль не READY — блок перед Агентом 6 ---


def test_r4_incomplete_profile_blocks_before_agent_6(store):
    profile = _build_ready_profile()
    profile.legal.inn = ""
    from onboarding.models import ProfileStatus

    profile.status = ProfileStatus.DRAFT  # неполный профиль
    run = run_tender_pipeline(_inputs(store, profile=profile))

    assert run.stop.rule.rule_id == "R4_PROFILE_NOT_READY"
    assert run.stop.step == "agent_11_profile_gate"
    assert run.package is None


# --- R5: Агент 10 — нормативка без подтверждения не применяется ---


def _pending_update(tmp_path):
    store = RegulatoryUpdateStore(tmp_path / "reg.sqlite3")
    update = store.detect_update(
        SourceType.LEGISLATION,
        "НДС",
        RegulatoryVersion(source_id="tax_vat", version_label="2027", published_at=date(2026, 10, 1), items={"vat_rate": "20"}),
    )
    return store, update.update_id


def test_r5_pending_regulatory_update_is_not_applied(tmp_path):
    store, update_id = _pending_update(tmp_path)

    result = route_regulatory_update(store, update_id)

    assert result.applied is False
    assert result.stop.rule.rule_id == "R5_REGULATORY_NOT_APPROVED"
    assert store.get_applied_version("tax_vat") is None


def test_r5_rejected_update_is_not_applied(tmp_path):
    store, update_id = _pending_update(tmp_path)
    store.approve_update(update_id, reviewer="Edwin", approved=False, reason="не подтверждено ФНС")

    result = route_regulatory_update(store, update_id)

    assert result.applied is False and result.stop.rule.rule_id == "R5_REGULATORY_NOT_APPROVED"


def test_r5_approved_update_is_applied(tmp_path):
    store, update_id = _pending_update(tmp_path)
    store.approve_update(update_id, reviewer="Edwin", approved=True)

    result = route_regulatory_update(store, update_id)

    assert result.applied is True and result.stop is None
    assert store.get_applied_version("tax_vat").version_label == "2027"


# --- R6: контракт данных ---


def test_r6_requirements_for_another_tender_violate_contract(store):
    tender = _tender()
    foreign = copy.deepcopy(_reviewed_requirements(tender))
    foreign.tender_purchase_number = "0000000000000000000"

    run = run_tender_pipeline(_inputs(store, tender=tender, extracted_requirements=foreign))

    assert run.stop.rule.rule_id == "R6_CONTRACT_VIOLATION"
    assert run.stop.step == "agent_3_documentation"
    assert any("0000000000000000000" in d for d in run.stop.details)


def test_r6_tender_without_price_violates_contract(store):
    tender = _tender()
    tender.max_price = 0
    run = run_tender_pipeline(_inputs(store, tender=tender))

    assert run.stop.rule.rule_id == "R6_CONTRACT_VIOLATION"
    assert run.stop.step == "agent_1_tender"


# --- R7: Агент 2 «требует ручной проверки» ---


def _unclear_requirements(tender):
    extracted = _reviewed_requirements(tender)
    extracted.participant_requirements = [
        ParticipantRequirement(kind="unclear", description="похоже на требование к опыту", raw_text="опыт ... 20 процентов")
    ]
    return extracted


def test_r7_unclear_requirement_goes_to_expert_not_to_client(store):
    profile = _build_ready_profile()
    profile.permits_experience.completed_contracts = []
    tender = _tender()

    run = run_tender_pipeline(_inputs(store, profile=profile, tender=tender, extracted_requirements=_unclear_requirements(tender)))

    assert run.stop.rule.rule_id == "R7_CLASSIFIER_MANUAL_CHECK"
    assert run.stop.step == "agent_2_final"


def test_r7_expert_resolution_lets_chain_continue(store):
    profile = _build_ready_profile()
    profile.permits_experience.completed_contracts = []
    tender = _tender()
    inputs = lambda: _inputs(store, profile=profile, tender=tender, extracted_requirements=_unclear_requirements(tender))

    first = run_tender_pipeline(inputs())
    record_expert_signoff(store, first.stop.signoff_key, reviewer="Edwin", approved=True)
    second = run_tender_pipeline(inputs())

    assert second.stop.rule.rule_id == "R3_AGENT6_EXPERT_SIGNOFF"  # дошли до следующего контроля


def test_confirmed_unmet_requirement_is_terminal_not_a_fit(store):
    profile = _build_ready_profile()
    profile.permits_experience.completed_contracts = []
    tender = _tender()
    extracted = _reviewed_requirements(tender)
    extracted.participant_requirements = [
        ParticipantRequirement(kind="experience", description="опыт не менее 3 лет", raw_text="опыт не менее 3 лет")
    ]

    run = run_tender_pipeline(_inputs(store, profile=profile, tender=tender, extracted_requirements=extracted))

    assert run.outcome == RunOutcome.NOT_A_FIT
    assert run.stop is None
    assert run.steps[-1].status == StepStatus.TERMINAL


# --- R8: нет входа ---


def test_r8_no_documentation_at_all_stops(store):
    run = run_tender_pipeline(_inputs(store, extracted_requirements=None, documentation_text=None))

    assert run.stop.rule.rule_id == "R8_MISSING_INPUT"


# --- R0: непредусмотренный сбой -> эксперт, не падение ---


def test_r0_unexpected_failure_is_routed_to_expert(store, monkeypatch):
    import orchestrator.router as router

    def boom(*a, **k):
        raise RuntimeError("агент 7 упал")

    monkeypatch.setattr(router, "check_completeness", boom)
    run = run_tender_pipeline(_inputs(store))

    assert run.stop.rule.rule_id == "R0_UNEXPECTED"
    assert run.stop.step == "agent_7_completeness"
    assert "агент 7 упал" in run.stop.reason


# --- Агент 4 не передан -> явный пропуск, не молчание ---


def test_smeta_not_provided_is_explicitly_skipped(store):
    first = run_tender_pipeline(_inputs(store, smeta_line_items=None))
    _sign_package(store, first)
    run = run_tender_pipeline(_inputs(store, smeta_line_items=None))

    assert run.outcome == RunOutcome.READY_FOR_CLIENT
    assert any(s.step == "agent_4_smeta" and s.status == StepStatus.SKIPPED for s in run.steps)
    assert run.client_summary.profitability is None


def test_every_stop_rule_has_expert_action_and_basis():
    for rule in STOP_RULES.values():
        assert rule.expert_action.strip() and rule.protocol_basis.strip(), rule.rule_id


def test_stop_decision_is_serializable_for_ui(store):
    run = run_tender_pipeline(_inputs(store))
    d = run.to_dict()

    assert d["stop"]["rule_id"] == "R3_AGENT6_EXPERT_SIGNOFF"
    assert d["stop"]["expert_action"]
    assert "R3_AGENT6_EXPERT_SIGNOFF" in run.render_log()

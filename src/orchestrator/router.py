"""Агент 13 — оркестратор как детерминированный роутер (2026-10-03).

Не LLM-агент и не «решает» сам: обычный код, который вызывает агентов в
фиксированном порядке, проверяет контракт данных на каждом переходе
(`contracts.py`) и останавливает цепочку по таблице стоп-условий
(`stop_rules.py`). Любой случай, который не описан явно, — остановка и
эксперт (`R0_UNEXPECTED`), не попытка продолжить.

## Порядок шагов (`run_tender_pipeline`)

Порядок — тот же, что уже прогоняется вручную/скриптами
(`tests/test_end_to_end_pipeline_from_notice.py`, `match_real_notices.py`),
а не идеализированный 1→2→3→4→6→7→8 из формулировки задачи: Агент 2
работает в два прохода (до и после Агента 3), Агент 4/5 — после 6/7.

1. Агент 1 → 2: контракт `Tender`.
2. Агент 2, проход 1 (`coarse_classify`): не подходит → исход NOT_A_FIT;
   только «требует ручной проверки» → R7.
3. Агент 3: нет ни готовых требований, ни текста документации → R8;
   требования не подтверждены экспертом → R1 (дальше не идём — ими
   пользуются Агенты 2/6/5/8).
4. Агент 2, проход 2 (`final_classify`) — те же правила, что в п.2.
5. Агент 11 → 6: профиль не READY → R4.
6. Агент 6 (`assemble_document_package`) + контракт.
7. Агент 7 (`check_completeness`) + контракт. FAIL комплектности — не
   остановка: это штатный исход, Агент 8 сообщает клиенту, чего не хватает.
8. Агент 4 (если переданы позиции сметы): контракт; хоть одна позиция не
   подтверждена экспертом → R2. Не переданы — шаги 4/5 пропускаются явно
   (сводка без раздела «Ожидаемая выгода»).
9. Агент 5 (`estimate_profitability`).
10. Выпуск к клиенту: пакет Агента 6 подтверждён экспертом для ЭТОГО
    содержимого → иначе R3.
11. Агент 8 (`build_client_summary`) → исход READY_FOR_CLIENT.

**После шага 11 шагов нет и не будет:** подача заявки и подпись КЭП без
участия собственника исключены архитектурно (протокол проекта). Агент 9
(клиентская аналитика) не специфицирован — в цепочке отсутствует.

## Как продолжить после остановки

Роутер не хранит состояние между прогонами: эксперт выполняет действие из
`StopDecision.rule.expert_action`, и вызывающий код запускает прогон снова
с теми же входами. Для подтверждений, у которых в агентах уже есть свой
механизм, используется он (Агент 3 — `ExtractedRequirements.expert_reviewed`,
Агент 4 — `MatchResult.expert_reviewed`, Агент 11 — `ProfileStatus`,
Агент 10 — `RegulatoryUpdateStore`). Там, где механизма не было (пакет
Агента 6, ручная проверка вердикта Агента 2), решение пишется в уже
существующий `quality_control.ExpertReviewStore` (SQLite) с ключом
`SignoffKey` из остановки — и привязано к содержимому: если пакет
изменился после подтверждения, подтверждение не засчитывается.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from classifier import ConstructionClassifier, coarse_classify, final_classify
from classifier.matching import MatchResult as ClassifierMatchResult
from classifier.tender import Tender
from client_consultant import build_client_summary
from client_consultant.models import ClientSummary
from completeness_check import check_completeness
from document_analyst import extract_requirements
from document_analyst.models import ExtractedRequirements
from document_assembler import assemble_document_package
from document_assembler.models import DocumentPackage
from onboarding.models import ClientProfile
from profitability_estimator import estimate_profitability
from quality_control.expert_review import ExpertReviewStore, ReviewDecision
from regulatory_updates.models import UpdateStatus
from regulatory_updates.store import RegulatoryUpdateStore
from smeta_estimator.cost_estimate import SmetaLineItem, build_cost_estimate
from smeta_estimator.pricing_metadata import PricingMetadata

from . import contracts
from .stop_rules import (
    R0_UNEXPECTED,
    R1_AGENT3_EXPERT_REVIEW,
    R2_AGENT4_EXPERT_REVIEW,
    R3_AGENT6_EXPERT_SIGNOFF,
    R4_PROFILE_NOT_READY,
    R5_REGULATORY_NOT_APPROVED,
    R6_CONTRACT_VIOLATION,
    R7_CLASSIFIER_MANUAL_CHECK,
    R8_MISSING_INPUT,
    StopRule,
)

AGENT_2_NAME = "agent_2_classifier"
AGENT_6_NAME = "agent_6_document_assembler"


class RunOutcome(str, Enum):
    READY_FOR_CLIENT = "ready_for_client"  # все проверки пройдены, сводка Агента 8 собрана
    AWAITING_EXPERT = "awaiting_expert"  # остановка по стоп-условию
    NOT_A_FIT = "not_a_fit"  # Агент 2 уверенно отсеял закупку — штатный исход, не ошибка


class StepStatus(str, Enum):
    PASSED = "passed"
    STOPPED = "stopped"
    SKIPPED = "skipped"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class SignoffKey:
    """Что именно эксперт подтверждает в `ExpertReviewStore`. `content` —
    каноническое содержимое на момент остановки; подтверждение засчитывается,
    только если при следующем прогоне содержимое то же самое."""

    agent_name: str
    document_type: str
    check_id: str
    content: str


@dataclass
class StopDecision:
    step: str
    rule: StopRule
    reason: str
    details: list[str] = field(default_factory=list)
    signoff_key: SignoffKey | None = None
    # Объект на проверку эксперту (например, ExtractedRequirements Агента 3) —
    # не сериализуется в to_dict(), только для вызывающего кода.
    artifact: Any = None

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "rule_id": self.rule.rule_id,
            "rule_title": self.rule.title,
            "reason": self.reason,
            "details": list(self.details),
            "expert_action": self.rule.expert_action,
            "protocol_basis": self.rule.protocol_basis,
            "signoff_key": asdict(self.signoff_key) if self.signoff_key else None,
        }


@dataclass
class StepRecord:
    step: str
    status: StepStatus
    message: str


@dataclass
class PipelineInputs:
    profile: ClientProfile
    tender: Tender
    # Агент 3: либо уже готовые (и, возможно, проверенные экспертом)
    # требования, либо текст документации — тогда роутер извлечёт их сам и
    # остановится на R1, отдав результат эксперту.
    extracted_requirements: ExtractedRequirements | None = None
    documentation_text: str | None = None
    # Агент 4 (необязательно): позиции сметы с объёмами и параметры расчёта.
    smeta_line_items: list[SmetaLineItem] | None = None
    smeta_region_name: str | None = None
    smeta_period_label: str | None = None
    smeta_as_of_date: date | None = None
    pricing_metadata: PricingMetadata | None = None
    # Хранилище подтверждений эксперта для пакета Агента 6 и ручной проверки
    # вердикта Агента 2. None — подтверждений нет (остановки R3/R7 сработают).
    signoff_store: ExpertReviewStore | None = None


@dataclass
class PipelineRun:
    purchase_number: str
    client_id: str
    outcome: RunOutcome | None = None
    steps: list[StepRecord] = field(default_factory=list)
    stop: StopDecision | None = None
    coarse_match: ClassifierMatchResult | None = None
    extracted_requirements: ExtractedRequirements | None = None
    final_match: ClassifierMatchResult | None = None
    package: DocumentPackage | None = None
    completeness: Any = None
    smeta_result: Any = None
    profitability: Any = None
    client_summary: ClientSummary | None = None

    def to_dict(self) -> dict:
        return {
            "purchase_number": self.purchase_number,
            "client_id": self.client_id,
            "outcome": self.outcome.value if self.outcome else None,
            "steps": [{"step": s.step, "status": s.status.value, "message": s.message} for s in self.steps],
            "stop": self.stop.to_dict() if self.stop else None,
        }

    def render_log(self) -> str:
        lines = [f"Прогон: закупка {self.purchase_number}, клиент {self.client_id}"]
        for s in self.steps:
            lines.append(f"  [{s.status.value.upper():8}] {s.step}: {s.message}")
        lines.append(f"Исход: {self.outcome.value if self.outcome else '—'}")
        if self.stop:
            lines.append(f"Остановка на шаге «{self.stop.step}» по правилу {self.stop.rule.rule_id}: {self.stop.rule.title}")
            lines.append(f"  Причина: {self.stop.reason}")
            for d in self.stop.details:
                lines.append(f"    - {d}")
            lines.append(f"  Что нужно от эксперта: {self.stop.rule.expert_action}")
        return "\n".join(lines)


class _Halt(Exception):
    """Внутренний сигнал остановки — наружу не выходит, превращается в
    `PipelineRun.stop`."""


def _pass(run: PipelineRun, step: str, message: str) -> None:
    run.steps.append(StepRecord(step, StepStatus.PASSED, message))


def _stop(run: PipelineRun, step: str, rule: StopRule, reason: str, **kwargs: Any) -> None:
    run.steps.append(StepRecord(step, StepStatus.STOPPED, f"{rule.rule_id}: {reason}"))
    run.stop = StopDecision(step=step, rule=rule, reason=reason, **kwargs)
    run.outcome = RunOutcome.AWAITING_EXPERT
    raise _Halt


def _require_contract(run: PipelineRun, step: str, problems: list[str]) -> None:
    if problems:
        _stop(run, step, R6_CONTRACT_VIOLATION, "контракт данных не выполнен", details=problems)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def package_content(package: DocumentPackage) -> str:
    """Каноническое содержимое пакета для подтверждения экспертом. Сгенерированный
    docx не включается (его байты не детерминированы между сборками —
    метаданные файла), но он целиком собирается из тех же полей профиля,
    что уже есть в `fields`."""
    return _canonical(
        {
            "client_id": package.client_id,
            "tender": package.tender_purchase_number,
            "fields": [asdict(f) for f in package.fields],
            "manual_sections": [asdict(m) for m in package.manual_sections],
            "hidden_risks": [asdict(r) for r in package.hidden_risks],
        }
    )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _find_signoff(store: ExpertReviewStore | None, key: SignoffKey) -> tuple[bool, str]:
    """Последнее решение эксперта по этому ключу. Засчитывается только
    APPROVED без правки (`corrected_output`) и для того же содержимого."""
    if store is None:
        return False, "решения эксперта нет (хранилище подтверждений не передано)"
    reviews = [
        r for r in store.for_check(key.agent_name, key.check_id) if r.document_type == key.document_type
    ]
    if not reviews:
        return False, "решения эксперта по этому объекту нет"
    last = max(reviews, key=lambda r: (r.reviewed_at, r.review_id))
    if last.decision != ReviewDecision.APPROVED:
        return False, f"эксперт отклонил: {last.reason}"
    if last.corrected_output is not None:
        return False, "эксперт внёс правку — нужно пересобрать с учётом правки и подтвердить заново"
    if last.original_output != _content_hash(key.content):
        return False, "содержимое изменилось после подтверждения экспертом — нужно подтвердить заново"
    return True, f"подтверждено экспертом {last.reviewer}"


def record_expert_signoff(
    store: ExpertReviewStore, key: SignoffKey, reviewer: str, approved: bool, reason: str = ""
) -> None:
    """Записать решение эксперта по остановке R3/R7 (ключ — `StopDecision.signoff_key`).
    В хранилище пишется хэш содержимого: подтверждается ровно то, что было
    показано эксперту."""
    store.record_review(
        agent_name=key.agent_name,
        document_type=key.document_type,
        check_id=key.check_id,
        original_output=_content_hash(key.content),
        decision=ReviewDecision.APPROVED if approved else ReviewDecision.REJECTED,
        reviewer=reviewer,
        reason=reason,
    )


def _classify_step(
    run: PipelineRun,
    step: str,
    match: ClassifierMatchResult,
    inputs: PipelineInputs,
) -> None:
    """Общий разбор вердикта Агента 2 для обоих проходов."""
    _require_contract(run, step, contracts.check_classifier_result(match, inputs.tender, inputs.profile))
    if match.is_match:
        _pass(run, step, f"ПОДХОДИТ (score={match.score:.2f})")
        return
    failed = [c for c in match.criteria if not c.passed]
    definite = [c for c in failed if not c.needs_manual_review]
    if definite:
        run.steps.append(
            StepRecord(step, StepStatus.TERMINAL, "НЕ ПОДХОДИТ: " + "; ".join(c.message for c in definite))
        )
        run.outcome = RunOutcome.NOT_A_FIT
        raise _Halt
    content = _canonical([(c.name, c.message) for c in failed])
    key = SignoffKey(AGENT_2_NAME, "manual_match_check", f"{inputs.profile.client_id}:{inputs.tender.purchase_number}", content)
    ok, why = _find_signoff(inputs.signoff_store, key)
    if ok:
        _pass(run, step, f"«ТРЕБУЕТ РУЧНОЙ ПРОВЕРКИ» разрешено экспертом ({why})")
        return
    _stop(
        run,
        step,
        R7_CLASSIFIER_MANUAL_CHECK,
        why,
        details=[c.message for c in failed],
        signoff_key=key,
        artifact=match,
    )


def run_tender_pipeline(inputs: PipelineInputs, classifier: ConstructionClassifier | None = None) -> PipelineRun:
    """Один прогон цепочки по одной закупке. Никогда не бросает исключение
    наружу из-за данных: любой сбой — `PipelineRun.stop` с правилом."""
    classifier = classifier or ConstructionClassifier()
    run = PipelineRun(
        purchase_number=getattr(inputs.tender, "purchase_number", "?"),
        client_id=getattr(inputs.profile, "client_id", "?"),
    )
    current_step = "agent_1_tender"
    try:
        _require_contract(run, current_step, contracts.check_tender(inputs.tender))
        _pass(run, current_step, "Tender прошёл контракт")
        tender, profile = inputs.tender, inputs.profile

        current_step = "agent_2_coarse"
        run.coarse_match = coarse_classify(profile, tender, classifier)
        _classify_step(run, current_step, run.coarse_match, inputs)

        current_step = "agent_3_documentation"
        extracted = inputs.extracted_requirements
        if extracted is None:
            if not inputs.documentation_text or not inputs.documentation_text.strip():
                _stop(run, current_step, R8_MISSING_INPUT, "нет ни требований Агента 3, ни текста документации закупки")
            extracted = extract_requirements(tender.purchase_number, inputs.documentation_text)
        _require_contract(run, current_step, contracts.check_extracted_requirements(extracted, tender))
        run.extracted_requirements = extracted
        if not extracted.expert_reviewed:
            _stop(
                run,
                current_step,
                R1_AGENT3_EXPERT_REVIEW,
                "требования/риски из документации не подтверждены экспертом",
                details=[
                    f"требований к участнику: {len(extracted.participant_requirements)}, "
                    f"требований к обеспечению: {len(extracted.security_requirements)}, "
                    f"скрытых рисков: {len(extracted.hidden_risks)}"
                ],
                artifact=extracted,
            )
        _pass(run, current_step, f"требования подтверждены экспертом {extracted.expert_reviewer}")

        current_step = "agent_2_final"
        run.final_match = final_classify(profile, tender, classifier, extracted)
        _classify_step(run, current_step, run.final_match, inputs)

        current_step = "agent_11_profile_gate"
        if not profile.is_ready_for_agent_6():
            _stop(
                run,
                current_step,
                R4_PROFILE_NOT_READY,
                f"статус профиля: {profile.status.value}, нужен ready",
                artifact=profile,
            )
        _pass(run, current_step, "профиль клиента в статусе READY")

        current_step = "agent_6_package"
        package = assemble_document_package(profile, tender, extracted_requirements=extracted)
        _require_contract(run, current_step, contracts.check_package(package, tender, profile))
        run.package = package
        _pass(run, current_step, f"пакет собран, полей: {len(package.fields)}")

        current_step = "agent_7_completeness"
        completeness = check_completeness(package, tender)
        _require_contract(run, current_step, contracts.check_completeness_result(completeness, package))
        run.completeness = completeness
        _pass(
            run,
            current_step,
            f"{completeness.status.value}"
            + (f", не хватает: {', '.join(completeness.missing_required_fields)}" if completeness.missing_required_fields else ""),
        )

        current_step = "agent_4_smeta"
        if inputs.smeta_line_items is None:
            run.steps.append(
                StepRecord(current_step, StepStatus.SKIPPED, "позиции сметы не переданы — Агенты 4/5 не запускались")
            )
        else:
            _require_contract(run, current_step, contracts.check_smeta_line_items(inputs.smeta_line_items, tender))
            unreviewed = [i.match_result.query_text for i in inputs.smeta_line_items if not i.match_result.expert_reviewed]
            if unreviewed:
                _stop(
                    run,
                    current_step,
                    R2_AGENT4_EXPERT_REVIEW,
                    f"не подтверждено позиций: {len(unreviewed)} из {len(inputs.smeta_line_items)}",
                    details=unreviewed,
                    artifact=inputs.smeta_line_items,
                )
            missing = [
                n
                for n, v in (
                    ("smeta_region_name", inputs.smeta_region_name),
                    ("smeta_period_label", inputs.smeta_period_label),
                    ("smeta_as_of_date", inputs.smeta_as_of_date),
                )
                if not v
            ]
            if missing:
                _stop(run, current_step, R8_MISSING_INPUT, "не заданы параметры расчёта сметы", details=missing)
            smeta = build_cost_estimate(
                inputs.smeta_line_items,
                inputs.smeta_as_of_date,
                inputs.smeta_region_name,
                inputs.smeta_period_label,
                inputs.pricing_metadata,
            )
            _require_contract(run, current_step, contracts.check_smeta_result(smeta))
            run.smeta_result = smeta
            _pass(run, current_step, f"себестоимость {smeta.total_cost:,.2f} руб., позиций с ценой: {smeta.priced_line_items}")

            current_step = "agent_5_profitability"
            run.profitability = estimate_profitability(profile, tender, smeta.to_cost_estimate(), extracted)
            margin = run.profitability.margin
            _pass(run, current_step, "маржа " + (f"{margin:,.2f} руб." if margin is not None else "не рассчитана"))

        current_step = "release_gate_agent_6"
        key = SignoffKey(
            AGENT_6_NAME, "document_package", f"{profile.client_id}:{tender.purchase_number}", package_content(package)
        )
        ok, why = _find_signoff(inputs.signoff_store, key)
        if not ok:
            _stop(run, current_step, R3_AGENT6_EXPERT_SIGNOFF, why, signoff_key=key, artifact=package)
        _pass(run, current_step, why)

        current_step = "agent_8_client_summary"
        run.client_summary = build_client_summary(
            run.final_match,
            completeness,
            package,
            run.profitability,
            extracted_requirements=extracted,
            client_profile=profile,
        )
        _pass(run, current_step, "сводка для клиента собрана")
        run.outcome = RunOutcome.READY_FOR_CLIENT
    except _Halt:
        pass
    except Exception as exc:  # noqa: BLE001 — любой непредусмотренный сбой агента -> эксперт
        run.steps.append(StepRecord(current_step, StepStatus.STOPPED, f"{R0_UNEXPECTED.rule_id}: {exc}"))
        run.stop = StopDecision(
            step=current_step,
            rule=R0_UNEXPECTED,
            reason=f"{type(exc).__name__}: {exc}",
        )
        run.outcome = RunOutcome.AWAITING_EXPERT
    return run


@dataclass
class RegulatoryRouteResult:
    applied: bool
    stop: StopDecision | None = None


def route_regulatory_update(store: RegulatoryUpdateStore, update_id: int) -> RegulatoryRouteResult:
    """Агент 10: обновление нормативной базы применяется в рабочую базу
    только со статусом APPROVED. PENDING/REJECTED/неизвестный id — остановка
    (R5 / R0), не применение. Дублирует защиту внутри `apply_update()`
    сознательно: правило видно в таблице стоп-условий оркестратора."""
    step = "agent_10_regulatory_update"
    update = store.get_pending_update(update_id)
    if update is None:
        return RegulatoryRouteResult(
            False, StopDecision(step=step, rule=R0_UNEXPECTED, reason=f"обновление id={update_id} не найдено")
        )
    if update.status != UpdateStatus.APPROVED:
        return RegulatoryRouteResult(
            False,
            StopDecision(
                step=step,
                rule=R5_REGULATORY_NOT_APPROVED,
                reason=f"{update.source_name}: версия {update.candidate_version.version_label}, статус {update.status.value}",
                artifact=update,
            ),
        )
    store.apply_update(update_id)
    return RegulatoryRouteResult(True)

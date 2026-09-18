"""Оркестрация фундамента Агента 4 — подбор кандидатов + обязательная
проверка эксперта через общую инфраструктуру контроля качества.

`review_match_result()` — первое реальное подключение
`quality_control.AuditReadinessTracker`/`CategorizedDiscrepancyLog` к коду
агента. У Агентов 3 и 10 аналогичный протокол контроля качества к моменту
написания этого модуля был устроен каждый по-своему (`ExtractedRequirements.
expert_reviewed` — просто булев флаг; `RegulatoryUpdateStore` — статусная
модель `PENDING/APPROVED/REJECTED`), не через `quality_control` напрямую —
единого класса вроде `ExpertAuditable`, применённого к обоим, в коде не
было. Здесь эта увязка сделана впервые.
"""

from __future__ import annotations

from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog, DiscrepancyCategory

from .models import GesnWorkItem, MatchResult
from .search import search_candidates

AGENT_NAME = "agent_4_smeta_estimator"


def match_work_item(
    catalog: list[GesnWorkItem], query_text: str, tender_purchase_number: str, top_n: int = 5
) -> MatchResult:
    candidates = search_candidates(catalog, query_text, top_n=top_n)
    return MatchResult(
        query_text=query_text, tender_purchase_number=tender_purchase_number, candidates=candidates
    )


def review_match_result(
    match_result: MatchResult,
    reviewer: str,
    selected_code: str | None,
    tracker: AuditReadinessTracker,
    discrepancy_log: CategorizedDiscrepancyLog,
    expert_comment: str = "",
    critical_error: bool = False,
) -> None:
    """Фиксирует решение эксперта и сразу прогоняет его через метрику
    перехода на выборочный аудит и лог расхождений (протокол контроля
    качества, CLAUDE.md). «Существенная корректировка» здесь — эксперт
    выбрал не тот кандидат, что оказался первым по релевантности поиска
    (или не выбрал ни одного из выданных). Это именно то, ради чего список
    кандидатов не заменяется автоматическим решением — параметризация ГЭСН
    означает, что топ по тексту не обязан быть правильным по существу."""
    top = match_result.top_candidate()
    needed_correction = selected_code is None or top is None or selected_code != top.code

    match_result.select_candidate(selected_code, reviewer)
    tracker.record_check(needed_correction=needed_correction, critical_error=critical_error)

    if needed_correction:
        top_code = top.code if top is not None else "(кандидатов не было)"
        discrepancy_log.log(
            agent_name=AGENT_NAME,
            check_id=f"{match_result.tender_purchase_number}:{match_result.query_text[:60]}",
            category=DiscrepancyCategory.CRITICAL if critical_error else DiscrepancyCategory.SIGNIFICANT,
            issue=f"эксперт выбрал {selected_code!r}, а не топ-кандидат поиска {top_code!r}",
            expert_comment=expert_comment,
        )

"""Подключение результата Агента 3 к общей инфраструктуре контроля
качества (`quality_control`) — тот же протокол, что уже реально работает
у Агента 4 (`smeta_estimator.matcher.review_match_result`), а не только
общее имя агента, используемое в тесте.

`review_document_analysis()` — единственная точка входа, вызывается из
`src/review_queue.py` (реальный, не тестовый код) при подтверждении или
отклонении выдачи Агента 3 экспертом.
"""

from __future__ import annotations

from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog, DiscrepancyCategory

from .extractor import AGENT_NAME
from .models import ExtractedRequirements


def review_document_analysis(
    extracted: ExtractedRequirements,
    reviewer: str,
    tracker: AuditReadinessTracker,
    discrepancy_log: CategorizedDiscrepancyLog,
    approved: bool,
    category: DiscrepancyCategory = DiscrepancyCategory.COSMETIC,
    issue: str = "",
    expert_comment: str = "",
) -> None:
    """Фиксирует решение эксперта по выдаче Агента 3 и прогоняет его через
    метрику перехода на выборочный аудит и лог расхождений — по аналогии с
    `review_match_result()` у Агента 4.

    `approved=True` — эксперт принимает выдачу (как есть или после правки,
    отражённой в `category`/`issue`) — `ExtractedRequirements.
    expert_reviewed` выставляется в `True`, дальше по конвейеру (Агент 6,
    Агент 5) можно пользоваться результатом.

    `approved=False` — эксперт отклоняет выдачу целиком; `expert_reviewed`
    НЕ выставляется (объект остаётся заблокирован для конвейера), но
    решение всё равно попадает в метрику и лог — молчания в статистике не
    должно быть даже при отклонении.

    `category=DiscrepancyCategory.COSMETIC` (по умолчанию) — эксперт
    согласен с выдачей без содержательных правок: в лог расхождений ничего
    не пишется, `needed_correction=False` для метрики (косметическая
    правка не считается существенной, см. `CategorizedDiscrepancy.
    needed_correction`). Для `SIGNIFICANT`/`CRITICAL` обязательно указать
    `issue` — что именно эксперт поправил или почему отклонил.

    Ограничение честно унаследовано от `src/review_queue.py`: `tracker`/
    `discrepancy_log` создаются заново при каждом запуске CLI (у Агента 3
    нет постоянного хранилища результатов, см. CLAUDE.md, «Известные
    пробелы») — окно из 50 проверок для перехода на выборочный аудит не
    накапливается между запусками, только внутри одного запуска.
    """
    if approved:
        extracted.mark_expert_reviewed(reviewer=reviewer)

    needed_correction = category != DiscrepancyCategory.COSMETIC
    critical_error = category == DiscrepancyCategory.CRITICAL
    tracker.record_check(needed_correction=needed_correction, critical_error=critical_error)

    if needed_correction:
        discrepancy_log.log(
            agent_name=AGENT_NAME,
            check_id=extracted.tender_purchase_number,
            category=category,
            issue=issue or "эксперт скорректировал или отклонил выдачу Агента 3",
            expert_comment=expert_comment,
        )

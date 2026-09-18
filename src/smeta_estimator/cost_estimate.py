"""Сведение подтверждённых экспертом позиций сметы (Агент 4) в одну
себестоимость для Агента 5 (`profitability_estimator`).

`match_work_item()`/`review_match_result()` в `matcher.py` работают с одной
позицией сметы (один текст работы) за раз и требуют решения эксперта по
каждой. Что этот фундамент **не делает** — не считает объём работ по смете
конкретной закупки (сколько единиц "100 м2" кровли, сколько "м3" бетона и
т.д., см. CLAUDE.md, Агент 4, «не реализовано»). Поэтому `SmetaLineItem.
work_volume` — обязательный внешний вход, не вычисляемое значение: это
единственное место в интеграции Агента 4 → Агента 5, где интерфейс не
получился таким гладким, как в паре Агент 3 → Агент 6 (там Агент 3 сам
извлекает всё нужное из текста документации).

`build_cost_estimate()` — единственная точка входа. Требует
`MatchResult.expert_reviewed=True` для КАЖДОЙ позиции (тот же паттерн
блокировки, что `assemble_document_package()`/`estimate_profitability()`
уже применяют к `ExtractedRequirements.expert_reviewed` Агента 3) — жёсткая
ошибка, а не тихий пропуск непроверенной позиции.

Позиции, где эксперт не выбрал кандидата вовсе, или выбранный кандидат не
был предварительно посчитан по региону, не входят в `total_cost` —
честно попадают в `unpriced_line_items`, не тихий ноль (тот же принцип, что
`unresolved_resource_codes` в `pricing.py`). Позиции, где кандидат посчитан
только частично (остались `unresolved_resource_codes` внутри самого
кандидата), входят в `total_cost` по той части, что удалось оценить, но
дополнительно попадают в `partially_priced_line_items` — итоговая
себестоимость по ним занижена, а не неверна в другую сторону.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from profitability_estimator.models import CostEstimate

from .models import MatchResult

AGENT_NAME = "agent_4_smeta_estimator"


@dataclass
class SmetaLineItem:
    """Одна позиция сметы: результат подбора и проверки Агента 4 для одной
    работы + объём этой работы по конкретной закупке.

    `work_volume` — в единицах `RateCandidate.unit` выбранного кандидата
    (например, количество "100 м2"). Считается вне этого фундамента —
    обычно из ведомости объёмов работ документации закупки.
    """

    match_result: MatchResult
    work_volume: float


@dataclass
class SmetaCostResult:
    """Итог сведения всех позиций сметы в одну себестоимость.

    `unpriced_line_items` и `partially_priced_line_items` — честная граница
    неполноты, по аналогии с `unresolved_resource_codes` в `pricing.py`:
    первые вообще не вошли в `total_cost`, вторые вошли частично.
    """

    total_cost: float
    as_of_date: date
    region_name: str
    period_label: str
    priced_line_items: int
    unpriced_line_items: list[str] = field(default_factory=list)
    partially_priced_line_items: list[str] = field(default_factory=list)

    def is_complete(self) -> bool:
        return not self.unpriced_line_items and not self.partially_priced_line_items

    def to_cost_estimate(self) -> CostEstimate:
        """Переходник к входу Агента 5. `expert_reviewed=True` здесь —
        не автоматическое подтверждение, а перенос гарантии, уже
        обеспеченной `build_cost_estimate()` (каждая позиция внутри уже
        проверена экспертом до попадания сюда)."""
        note = (
            f"Агент 4: себестоимость по {self.priced_line_items} позиции(ям) сметы, "
            f"регион {self.region_name}, период {self.period_label}"
        )
        if self.unpriced_line_items:
            note += f"; не оценено вовсе: {len(self.unpriced_line_items)} позиции(й)"
        if self.partially_priced_line_items:
            note += f"; оценено частично: {len(self.partially_priced_line_items)} позиции(й)"
        return CostEstimate(
            total_cost=self.total_cost,
            as_of_date=self.as_of_date,
            expert_reviewed=True,
            is_complete=self.is_complete(),
            source_note=note,
        )


def build_cost_estimate(
    line_items: list[SmetaLineItem],
    as_of_date: date,
    region_name: str,
    period_label: str,
) -> SmetaCostResult:
    if not line_items:
        raise ValueError("Список позиций сметы пуст — нечего сводить в себестоимость")

    total = 0.0
    priced_count = 0
    unpriced: list[str] = []
    partially_priced: list[str] = []

    for item in line_items:
        match_result = item.match_result
        if not match_result.expert_reviewed:
            raise ValueError(
                "Результат подбора Агента 4 должен быть проверен экспертом "
                f"(MatchResult.expert_reviewed) для позиции {match_result.query_text!r} "
                "до того, как им воспользуется Агент 5"
            )

        if match_result.selected_code is None:
            unpriced.append(match_result.query_text)
            continue

        selected = next(
            (c for c in match_result.candidates if c.code == match_result.selected_code), None
        )
        if selected is None or selected.priced is None:
            unpriced.append(match_result.query_text)
            continue

        if (
            selected.priced.region_name != region_name
            or selected.priced.period_label != period_label
        ):
            raise ValueError(
                f"Позиция {match_result.query_text!r} посчитана для региона/периода "
                f"{selected.priced.region_name!r}/{selected.priced.period_label!r}, а "
                f"себестоимость собирается для {region_name!r}/{period_label!r}"
            )

        total += selected.priced.total_price * item.work_volume
        priced_count += 1
        if not selected.priced.is_fully_priced():
            partially_priced.append(match_result.query_text)

    return SmetaCostResult(
        total_cost=total,
        as_of_date=as_of_date,
        region_name=region_name,
        period_label=period_label,
        priced_line_items=priced_count,
        unpriced_line_items=unpriced,
        partially_priced_line_items=partially_priced,
    )

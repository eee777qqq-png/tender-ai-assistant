"""Результат Агента 5 (Оценка выгоды) и заглушка для входа от Агента 4.

Маржа = НМЦК − Себестоимость − Стоимость_обеспечения − Налоги.

Явно НЕ считаем вероятность победы числом — своей статистики побед/поражений
ещё нет (cold start), и вычислять её из непроверенных допущений было бы хуже,
чем честно сказать, что данных недостаточно. Вместо числа — качественные
флаги риска без цифр (`risk_flags`) и `win_probability_note`, всегда
одинаковый и обязательный, как у `legal_boundaries` — не необязательное
поле, которое можно забыть заполнить.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

WIN_PROBABILITY_NOTE = (
    "Оценка вероятности победы не производится — недостаточно данных (своей "
    "статистики побед/поражений ещё нет). Появится после накопления "
    "статистики через Агента 9 (клиентская аналитика)."
)


@dataclass
class CostEstimate:
    """Себестоимость на входе Агента 5 — строится `smeta_estimator.
    cost_estimate.build_cost_estimate()` (Агент 4) через
    `SmetaCostResult.to_cost_estimate()`, а не читается напрямую из модуля
    Агента 4: фундамент Агента 4 не считает объём работ по смете конкретной
    закупки (см. CLAUDE.md, Агент 4, «не реализовано»), поэтому передача
    остаётся через явную структуру, не сквозной вызов.

    `expert_reviewed` — тот же паттерн блокировки, что `ExtractedRequirements.
    expert_reviewed` у Агента 3: `estimate_profitability()` отказывает, если
    он не выставлен в `True`. По умолчанию `False` — осознанно, чтобы
    вручную собранный (например, в тестах) `CostEstimate` не проходил гейт
    случайно, без явного решения.

    `is_complete=False` — часть позиций сметы, из которых сложена
    `total_cost`, не оценена (эксперт не выбрал кандидата) или оценена
    только частично (не все ресурсы кандидата определены по цене); сумма в
    этом случае — нижняя граница себестоимости, не точная цифра.

    `as_of_date` обязателен по протоколу контроля качества (CLAUDE.md): любая
    смета должна нести дату актуальности нормативных данных, на которые
    опирается расчёт.
    """

    total_cost: float
    as_of_date: date
    expert_reviewed: bool = False
    is_complete: bool = True
    source_note: str = ""


@dataclass
class ProfitabilityEstimate:
    """Результат Агента 5 по одной паре клиент+закупка."""

    client_id: str
    tender_purchase_number: str
    max_price: float
    cost_estimate: float
    cost_estimate_as_of: date
    security_cost: float
    taxes: float | None  # None — режим "Другое", налог не может быть посчитан
    margin: float | None  # None — по той же причине, что и taxes
    risk_flags: list[str] = field(default_factory=list)
    win_probability_note: str = WIN_PROBABILITY_NOTE

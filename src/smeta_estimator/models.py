"""Модели фундамента Агента 4 (Сметчик).

Источник данных — ФГИС ЦС (fgiscs.minstroyrf.ru), открытый доступ без
авторизации, проверено вживую 2026-09-18 (см. `docs/agent4-ai-matching-feasibility.md`):
ГЭСН (нормы расхода ресурсов на единицу работы) + ФСБЦ (цены материалов и
машино-часов) + ежеквартальные региональные индексы пересчёта. Обязательное
условие открытой лицензии данных ФГИС ЦС — ссылка на первоисточник при
использовании, см. `ATTRIBUTION_NOTICE` в `fsnb_client.py`.

Важно про `base_price`: считается только по тем ресурсам ГЭСН, для которых
нашлась прямая цена в ФСБЦ (`<Resource Code="...">`). Это НЕ полная
себестоимость:
- Трудозатраты (`<Resource Code="1-100-38">`-подобные коды разрядов работ и
  агрегированный код "2") не оцениваются — для них нужна отдельная таблица
  оплаты труда по регионам (открытый набор «Среднемесячные размеры оплаты
  труда» ФГИС ЦС, не подключена в этом фундаменте).
- Часть материалов в ГЭСН задана не конкретным кодом, а категорией
  (`<AbstractResource Code="...">`, например «Материалы рулонные кровельные
  для нижних слоев») — точный продукт и его цену эксперт должен выбрать
  вручную, здесь это не автоматизировано.

Поэтому `unpriced_resource_codes` и `abstract_resource_codes` в
`GesnWorkItem` — не техническая деталь, а честная граница того, что
`base_price` реально покрывает. Финальную цену без эксперта использовать
нельзя — отсюда и вся модель `MatchResult` ниже.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class GesnResourceUsage:
    """Один ресурс в составе работы ГЭСН — сколько единиц на единицу работы."""

    resource_code: str
    resource_name: str
    quantity: float
    is_abstract: bool = False  # True — категория ресурса (AbstractResource), не конкретный продукт


@dataclass
class GesnWorkItem:
    """Одна позиция ГЭСН — работа с нормой расхода ресурсов.

    `name` собран из полной иерархии (Сборник → Раздел → ... → Таблица →
    NameGroup → Work), чтобы быть самодостаточным для полнотекстового
    поиска — отдельно от иерархии эта строка не разбирается на части.
    """

    code: str
    name: str
    unit: str
    resources: list[GesnResourceUsage] = field(default_factory=list)
    base_price: float = 0.0
    unpriced_resource_codes: list[str] = field(default_factory=list)
    abstract_resource_codes: list[str] = field(default_factory=list)

    def is_fully_priced(self) -> bool:
        return not self.unpriced_resource_codes and not self.abstract_resource_codes


@dataclass
class RateCandidate:
    """Один кандидат в выдаче поиска — с опциональной региональной ценой."""

    code: str
    name: str
    unit: str
    base_price: float
    match_score: float
    unpriced_resource_codes: list[str] = field(default_factory=list)
    abstract_resource_codes: list[str] = field(default_factory=list)
    regional_price: float | None = None
    region_name: str | None = None
    index_value: float | None = None
    index_as_of: date | None = None


@dataclass
class MatchResult:
    """Результат подбора расценки под один текст работы — список кандидатов,
    не готовое решение. Выбор конкретного кандидата — обязательное действие
    эксперта (`select_candidate`), как и `mark_expert_reviewed` у Агента 3."""

    query_text: str
    tender_purchase_number: str
    candidates: list[RateCandidate] = field(default_factory=list)
    selected_code: str | None = None
    expert_reviewer: str = ""
    expert_reviewed: bool = False
    reviewed_at: datetime | None = None

    def top_candidate(self) -> RateCandidate | None:
        return self.candidates[0] if self.candidates else None

    def select_candidate(self, code: str | None, reviewer: str) -> None:
        """`code=None` — эксперт решил, что ни один кандидат не подходит.

        Не проверяет, что `code` действительно есть среди `candidates` —
        эксперт мог выбрать код, которого не было в top-N (поиск неполный),
        это тоже валидный, просто не бесплатный для дальнейшего анализа
        случай, см. `record_expert_decision` в `matcher.py`.
        """
        if not reviewer.strip():
            raise ValueError("Выбор кандидата обязательно должен быть привязан к эксперту")
        self.selected_code = code
        self.expert_reviewer = reviewer
        self.expert_reviewed = True
        self.reviewed_at = datetime.now(timezone.utc)

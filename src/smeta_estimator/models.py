"""Модели фундамента Агента 4 (Сметчик).

Источник данных — ФГИС ЦС (fgiscs.minstroyrf.ru), открытый доступ без
авторизации, проверено вживую 2026-09-18 (см. `docs/agent4-ai-matching-feasibility.md`):
ГЭСН (нормы расхода ресурсов на единицу работы) + ФСБЦ (цены материалов и
машино-часов, базисный уровень цен на 01.01.2022 — подтверждено официальным
разъяснением Минстроя, приказ №1046/пр от 30.12.2021, тот же приказ, которым
утверждена сама ФСНБ-2022) + ежеквартальные индексы по группам однородных
строительных ресурсов (ГОСР) для регионального пересчёта, см. `pricing.py`.
Обязательное условие открытой лицензии данных ФГИС ЦС — ссылка на
первоисточник при использовании, см. `ATTRIBUTION_NOTICE` в `fsnb_client.py`.

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
from datetime import datetime, timezone


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
class MachineLabourInfo:
    """Трудозатраты машиниста на единицу машино-часа конкретной машины —
    из атрибутов `LabourMach`/`DriverCode` в ФСБЦ_Маш.xml (`fsnb_parser.
    parse_fsbc_machine_labour_xml`).

    **Подтверждено устно на звонке со Smetrix, 2026-09-18 — письменного
    подтверждения пока нет** (см. CLAUDE.md, «Известные пробелы», п.12,
    не закрыт до письменного ответа). Практик подтвердил: это не единая
    формула на все машины, а свойство конкретной позиции — для одной
    техники оплата труда машиниста уже учтена в её собственной цене
    (`labour_mach == 0`, обычно когда `driver_code` вообще не указан —
    самоходное/электрическое оборудование без отдельного оператора), для
    другой — не учтена и её нужно добавить отдельно (`labour_mach > 0`,
    на практике встречалось только `1.0`), умножив на текущую ставку
    машиниста по `driver_code` из того же `RimWorkerSalaryRegistry`, что
    уже используется для рабочих (`regional_pricing_client.
    fetch_worker_salary_registry`). **Если письменное подтверждение будет
    противоречить этой логике — пересмотреть `pricing.py`, не считать
    вопрос закрытым только на основании этого класса.**
    """

    resource_code: str
    labour_mach: float
    driver_code: str | None


@dataclass
class ResourcePriceResolution:
    """Как определилась цена одного ресурса при региональном пересчёте —
    приоритет из `pricing.resolve_resource_unit_price()`: текущая цена
    напрямую, иначе базисная цена (01.01.2022) × индекс ГОСР для группы
    этого ресурса, иначе не определилась вовсе. Для машинных ресурсов
    `pricing.price_candidate_for_region()` может дополнительно прибавить
    оплату труда машиниста через `MachineLabourInfo` — `machinist_wage_added`
    показывает, сколько из `unit_price` пришлось на эту добавку (0.0 —
    либо не машина, либо `labour_mach == 0`, то есть уже учтено в
    собственной цене машины, см. `MachineLabourInfo`)."""

    resource_code: str
    resource_name: str
    quantity: float
    unit_price: float | None
    source: str  # "current_price" | "gosr_index" | "unresolved"
    index_value: float | None = None
    group_name: str | None = None
    machinist_wage_added: float = 0.0

    @property
    def line_total(self) -> float | None:
        return None if self.unit_price is None else self.unit_price * self.quantity


@dataclass
class RegionalPriceResult:
    """Итог пересчёта одного кандидата на конкретный регион и квартал —
    сумма по ресурсам, для которых удалось определить цену, плюс полная
    видимость того, как именно определилась цена каждого ресурса."""

    region_name: str
    period_label: str
    total_price: float
    resolutions: list[ResourcePriceResolution] = field(default_factory=list)

    @property
    def unresolved_resource_codes(self) -> list[str]:
        return [r.resource_code for r in self.resolutions if r.source == "unresolved"]

    def is_fully_priced(self) -> bool:
        return not self.unresolved_resource_codes


@dataclass
class RateCandidate:
    """Один кандидат в выдаче поиска — с опциональной региональной ценой
    (`priced`, см. `pricing.price_candidate_for_region()`).

    `resources` скопирован с исходного `GesnWorkItem` — нужен, чтобы посчитать
    региональную цену *по каждому ресурсу отдельно* (текущая цена или свой
    ГОСР-индекс для его группы), а не одним общим множителем на всю позицию.
    """

    code: str
    name: str
    unit: str
    base_price: float
    match_score: float
    resources: list[GesnResourceUsage] = field(default_factory=list)
    unpriced_resource_codes: list[str] = field(default_factory=list)
    abstract_resource_codes: list[str] = field(default_factory=list)
    priced: RegionalPriceResult | None = None


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

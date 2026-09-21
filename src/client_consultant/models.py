"""Итоговая сводка для собственника бизнеса (Агент 8 — Консультант для клиента).

Собирает вместе то, что уже посчитали другие агенты — подходит ли закупка
(Агент 2), готов ли пакет документов (Агент 7) и какие скрытые риски нашёл
Агент 3 (пробрасываются через `DocumentPackage.hidden_risks` от Агента 6) —
и переизлагает это простыми словами, без кодов статусов и юридического
жаргона. Ничего не решает и не рекомендует — см. `DECISION_REMINDER`
в `consultant.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MissingDocumentItem:
    """Один недостающий обязательный документ/поле — что это и что сделать."""

    name: str
    what_to_do: str


@dataclass
class PlainRisk:
    """Один скрытый риск, переизложенный для собственника."""

    what_it_says: str  # что нашли, простыми словами
    why_it_matters: str  # что это значит на практике для собственника
    quote: str  # цитата из документации закупки — для проверки первоисточника


@dataclass
class ProfitabilitySummary:
    """Оценка выгоды (Агент 5), переизложенная для собственника — по тому
    же принципу, что `PlainRisk` переизлагает `HiddenRisk` Агента 3: свой
    текст для интерфейса, не прямой проброс `ProfitabilityEstimate`."""

    margin: float | None  # None — маржу посчитать не удалось, см. risk_flags почему
    margin_explanation: str
    risk_flags: list[str] = field(default_factory=list)
    win_probability_note: str = ""


@dataclass
class ClientSummary:
    """Готовая сводка по паре клиент+закупка."""

    client_id: str
    tender_purchase_number: str
    tender_name: str
    tender_fits: bool
    tender_fit_explanation: str
    package_ready: bool
    package_status_explanation: str
    missing_documents: list[MissingDocumentItem] = field(default_factory=list)
    risks: list[PlainRisk] = field(default_factory=list)
    # None — Агент 5 не подключён к этому вызову (необязательный вход,
    # см. build_client_summary()), не то же самое, что «маржа не посчитана»
    # (это ProfitabilitySummary.margin=None при подключённом Агенте 5).
    profitability: ProfitabilitySummary | None = None
    decision_reminder: str = ""

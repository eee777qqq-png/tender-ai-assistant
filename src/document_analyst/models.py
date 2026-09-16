"""Структурированные требования, извлечённые из текста документации закупки
(Агент 3 — Аналитик документации).

Каркас: настоящего NLP/LLM-парсера нет — источник и метод извлечения на
реальных документах не выбран (см. CLAUDE.md, раздел «Известные пробелы»).
Здесь работает эвристический экстрактор по ключевым словам и регулярным
выражениям (`extractor.py`) над реалистичными тестовыми текстами
(`sample_documents.py`) — чтобы зафиксировать форму результата и протокол
контроля качества. По протоколу (CLAUDE.md) результат не уходит клиенту без
подтверждения эксперта.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class RiskCategory(str, Enum):
    """Тип скрытого риска — формулировки, которая может создать проблему
    при исполнении контракта."""

    NONSTANDARD_PENALTY = "nonstandard_penalty"  # нестандартный (завышенный) штраф/неустойка
    UNCAPPED_LIABILITY = "uncapped_liability"  # ответственность без верхнего предела
    UNILATERAL_TERMS = "unilateral_terms"  # одностороннее право заказчика менять условия
    AMBIGUOUS_CONDITION = "ambiguous_condition"  # расплывчатая, неоднозначно трактуемая формулировка
    OTHER = "other"


@dataclass
class SubmissionTimeline:
    """Сроки подачи заявки и исполнения контракта."""

    submission_deadline: date | None = None
    performance_start: date | None = None
    performance_end: date | None = None
    raw_mentions: list[str] = field(default_factory=list)  # фрагменты текста-источники


@dataclass
class SecurityRequirement:
    """Требование к обеспечению — заявки или исполнения контракта."""

    kind: str  # "bid" | "contract"
    percentage: float | None = None
    amount: float | None = None
    raw_text: str = ""


@dataclass
class ParticipantRequirement:
    """Требование к участнику закупки (допуски, опыт и т. п.).

    `kind` — машиночитаемая метка ("sro" | "experience" | "other"), нужна
    Агенту 6, чтобы связать конкретное извлечённое требование с полем
    пакета документов, не разбирая `description` текстом заново."""

    description: str
    kind: str = "other"
    raw_text: str = ""


@dataclass
class HiddenRisk:
    """Формулировка, отмеченная как потенциальная проблема при исполнении —
    отдельно от «обычных» требований, чтобы эксперт не пропустил её при
    беглом просмотре."""

    category: RiskCategory
    excerpt: str  # цитата из документа
    explanation: str  # почему это риск


@dataclass
class ExtractedRequirements:
    """Результат работы Агента 3 по одному документу закупки.

    `expert_reviewed` — по протоколу контроля качества (CLAUDE.md) выдачу
    Агента 3 нельзя использовать ниже по конвейеру (Агент 6) без
    подтверждения эксперта-человека. Подключение к Агенту 6 не снимает и не
    автоматизирует эту проверку — `assemble_document_package()` откажет,
    если этот флаг не выставлен явно через `mark_expert_reviewed()`."""

    tender_purchase_number: str
    timeline: SubmissionTimeline = field(default_factory=SubmissionTimeline)
    security_requirements: list[SecurityRequirement] = field(default_factory=list)
    participant_requirements: list[ParticipantRequirement] = field(default_factory=list)
    hidden_risks: list[HiddenRisk] = field(default_factory=list)
    expert_reviewed: bool = False
    expert_reviewer: str = ""

    def security_requirement(self, kind: str) -> SecurityRequirement | None:
        return next((r for r in self.security_requirements if r.kind == kind), None)

    def mark_expert_reviewed(self, reviewer: str) -> None:
        self.expert_reviewed = True
        self.expert_reviewer = reviewer

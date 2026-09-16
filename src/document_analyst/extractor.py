"""Эвристический экстрактор требований из текста документации закупки
(Агент 3 — Аналитик документации).

**Это не production-парсер.** Настоящий источник/метод извлечения на
реальных документах закупок не выбран (нет решения — LLM, промышленный
NLP-парсер или что-то ещё), см. CLAUDE.md → «Известные пробелы». Здесь —
поиск по ключевым словам и регулярным выражениям, подобранный под
реалистичные тестовые документы (`sample_documents.py`), чтобы зафиксировать
форму результата (`ExtractedRequirements`) и протокол контроля качества
(обязательная проверка эксперта, см. `quality_control`). На реальных текстах
с другими формулировками ничего не гарантирует полноту извлечения — именно
поэтому 100% выдачи идёт через эксперта, пока не накоплена статистика.

`extract_requirements()` — единственная точка входа.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .models import (
    ExtractedRequirements,
    HiddenRisk,
    ParticipantRequirement,
    RiskCategory,
    SecurityRequirement,
    SubmissionTimeline,
)

AGENT_NAME = "agent_3_document_analyst"

# Типовая практика по неустойке за просрочку исполнения — доли процента в
# день; ставка на этом уровне и выше считается нестандартной и подсвечивается
# как скрытый риск, а не тихо принимается как обычное условие.
_NONSTANDARD_PENALTY_THRESHOLD_PCT = 1.0

_SENTENCE_SPLIT_RE = re.compile(r"(?<!\d)\.(?!\d)\s*|\n+")

_SUBMISSION_DEADLINE_RE = re.compile(
    r"срок[а-яё\s]{0,40}подачи заявок[^\n]{0,80}?(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE
)
_PERFORMANCE_PERIOD_RE = re.compile(
    r"срок[а-яё\s]{0,40}(?:исполнения контракта|выполнения работ)"
    r"[^\n]{0,40}?с\s+(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)
_BID_SECURITY_RE = re.compile(
    r"обеспечени[а-яё]*\s+заявки[^\n]{0,80}?(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE
)
_CONTRACT_SECURITY_RE = re.compile(
    r"обеспечени[а-яё]*\s+исполнения контракта[^\n]{0,80}?(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE
)
_AMOUNT_RE = re.compile(r"(\d{1,3}(?:\s\d{3})+)\s*руб")
_SRO_RE = re.compile(r"\bСРО\b|саморегулируем", re.IGNORECASE)
_EXPERIENCE_RE = re.compile(
    r"опыт[а-яё\s]{0,40}не менее\s*(\d+)\s*(?:лет|года|год)", re.IGNORECASE
)
_PENALTY_RATE_RE = re.compile(
    r"штраф[а-яё\s]{0,20}размере\s*(\d+(?:[.,]\d+)?)\s*%[^\n]{0,30}за каждый день",
    re.IGNORECASE,
)

# (триггер, категория, объяснение почему это риск для эксперта)
_RISK_PATTERNS: list[tuple[re.Pattern[str], RiskCategory, str]] = [
    (
        re.compile(r"в одностороннем порядке", re.IGNORECASE),
        RiskCategory.UNILATERAL_TERMS,
        "Заказчик оставляет за собой право менять условия в одностороннем порядке — "
        "подрядчик не может на это повлиять",
    ),
    (
        re.compile(
            r"без ограничения (?:предельного )?размера неустойки|неустойк\w* не ограничивается",
            re.IGNORECASE,
        ),
        RiskCategory.UNCAPPED_LIABILITY,
        "Неустойка/пеня не ограничена предельной суммой — потенциально неограниченная "
        "финансовая ответственность подрядчика",
    ),
    (
        re.compile(r"по своему усмотрению", re.IGNORECASE),
        RiskCategory.AMBIGUOUS_CONDITION,
        "Формулировка оставляет заказчику право трактовать условие произвольно — объём "
        "обязательств подрядчика заранее не определён",
    ),
]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%d.%m.%Y").date()


def _parse_number(raw: str) -> float:
    return float(raw.replace(",", "."))


def _extract_amount(sentence: str) -> float | None:
    m = _AMOUNT_RE.search(sentence)
    return float(m.group(1).replace(" ", "")) if m else None


def _scan_hidden_risks(sentence: str) -> list[HiddenRisk]:
    risks: list[HiddenRisk] = []
    for pattern, category, explanation in _RISK_PATTERNS:
        if pattern.search(sentence):
            risks.append(HiddenRisk(category=category, excerpt=sentence, explanation=explanation))

    m = _PENALTY_RATE_RE.search(sentence)
    if m:
        rate = _parse_number(m.group(1))
        if rate >= _NONSTANDARD_PENALTY_THRESHOLD_PCT:
            risks.append(
                HiddenRisk(
                    category=RiskCategory.NONSTANDARD_PENALTY,
                    excerpt=sentence,
                    explanation=(
                        f"Штраф {rate:g}% цены контракта за каждый день просрочки — заметно "
                        "выше типовой практики (доли процента в день)"
                    ),
                )
            )
    return risks


def extract_requirements(tender_purchase_number: str, document_text: str) -> ExtractedRequirements:
    timeline = SubmissionTimeline()
    security_requirements: list[SecurityRequirement] = []
    participant_requirements: list[ParticipantRequirement] = []
    hidden_risks: list[HiddenRisk] = []

    for sentence in _sentences(document_text):
        m = _SUBMISSION_DEADLINE_RE.search(sentence)
        if m:
            timeline.submission_deadline = _parse_date(m.group(1))
            timeline.raw_mentions.append(sentence)

        m = _PERFORMANCE_PERIOD_RE.search(sentence)
        if m:
            timeline.performance_start = _parse_date(m.group(1))
            timeline.performance_end = _parse_date(m.group(2))
            timeline.raw_mentions.append(sentence)

        m = _BID_SECURITY_RE.search(sentence)
        if m:
            security_requirements.append(
                SecurityRequirement(
                    kind="bid",
                    percentage=_parse_number(m.group(1)),
                    amount=_extract_amount(sentence),
                    raw_text=sentence,
                )
            )

        m = _CONTRACT_SECURITY_RE.search(sentence)
        if m:
            security_requirements.append(
                SecurityRequirement(
                    kind="contract",
                    percentage=_parse_number(m.group(1)),
                    amount=_extract_amount(sentence),
                    raw_text=sentence,
                )
            )

        if _SRO_RE.search(sentence):
            participant_requirements.append(
                ParticipantRequirement(
                    description="Требуется членство в СРО", kind="sro", raw_text=sentence
                )
            )

        m = _EXPERIENCE_RE.search(sentence)
        if m:
            years = int(m.group(1))
            participant_requirements.append(
                ParticipantRequirement(
                    description=(
                        f"Требуется опыт выполнения аналогичных работ не менее {years} лет"
                    ),
                    kind="experience",
                    raw_text=sentence,
                )
            )

        hidden_risks.extend(_scan_hidden_risks(sentence))

    return ExtractedRequirements(
        tender_purchase_number=tender_purchase_number,
        timeline=timeline,
        security_requirements=security_requirements,
        participant_requirements=participant_requirements,
        hidden_risks=hidden_risks,
    )

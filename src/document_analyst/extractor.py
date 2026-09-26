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

# Защитная сетка НАД _EXPERIENCE_RE (и любым другим будущим паттерном по
# опыту) — не замена. Находка на реальном тендере №0373100134626000473
# (2026-09-25/26, см. CLAUDE.md, открытый п.3): реальное требование к опыту
# было сформулировано не как «не менее N лет», а как «опыт... цена которого
# не менее 20% НМЦК» — экстрактор молча вернул пустой список, неотличимый
# от «требования реально нет». Ищет по ВСЕМУ тексту (не по отдельным
# предложениям, как основной цикл ниже) — именно потому, что в реальном
# документе фраза с числом лежала в ОТДЕЛЬНОМ предложении от слова «опыт».
_EXPERIENCE_WORD_RE = re.compile(r"опыт[а-яё]*", re.IGNORECASE)
_NUMBER_NEARBY_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|процент[а-яё]*|лет\b|год[а-яё]*|руб[а-яё.]*)", re.IGNORECASE
)
# Задача предложила «скажем, 200 символов» как отправную точку — на реальном
# документе (тендер №0373100134626000473) расстояние от слова «опыт» до
# «20 процентов» оказалось около 285 символов (число лежит в отдельном
# предложении/абзаце после перечисления «1) ... или 2) ...», см. заметку
# выше) — со строгими 200 находка не сработала бы на собственном тестовом
# примере задачи. Увеличено до 300 с запасом, проверено именно на этом
# документе, не подобрано произвольно.
_UNCLEAR_WINDOW_CHARS = 300
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
                    rate_pct=rate,
                )
            )
    return risks


def _scan_unclear_experience(document_text: str) -> list[ParticipantRequirement]:
    """Помечает «похоже на требование к опыту, но не распознано» вместо
    молчаливого пропуска — см. заметку у `_EXPERIENCE_WORD_RE` выше.

    Для каждого вхождения корня «опыт»: если в окне ±`_UNCLEAR_WINDOW_CHARS`
    символов уже срабатывает `_EXPERIENCE_RE` — штатный паттерн справился,
    ничего не добавляем (не дублируем то, что и так нашлось). Если нет, но
    в этом же окне встретилось число, похожее на процент/годы/рубли —
    штатный паттерн зря промолчал: добавляем `kind="unclear"` с фрагментом
    текста для ручной проверки эксперта. Дедуплицируется по позиции
    найденного числа — иначе несколько упоминаний «опыт» рядом с одним и
    тем же числом (обычное дело в перечислениях «1) ... или 2) ...») дали
    бы несколько одинаковых находок."""
    found: list[ParticipantRequirement] = []
    reported_number_spans: set[tuple[int, int]] = set()

    for word_match in _EXPERIENCE_WORD_RE.finditer(document_text):
        window_start = max(0, word_match.start() - _UNCLEAR_WINDOW_CHARS)
        window_end = min(len(document_text), word_match.end() + _UNCLEAR_WINDOW_CHARS)
        window = document_text[window_start:window_end]

        if _EXPERIENCE_RE.search(window):
            continue

        number_match = _NUMBER_NEARBY_RE.search(window)
        if number_match is None:
            continue

        number_span = (window_start + number_match.start(), window_start + number_match.end())
        if number_span in reported_number_spans:
            continue
        reported_number_spans.add(number_span)

        found.append(
            ParticipantRequirement(
                description=(
                    "Похоже, есть требование к опыту, формулировка не распознана "
                    "автоматически — требуется ручная проверка текста"
                ),
                kind="unclear",
                raw_text=" ".join(window.split()),
            )
        )

    return found


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

    participant_requirements.extend(_scan_unclear_experience(document_text))

    return ExtractedRequirements(
        tender_purchase_number=tender_purchase_number,
        timeline=timeline,
        security_requirements=security_requirements,
        participant_requirements=participant_requirements,
        hidden_risks=hidden_risks,
    )

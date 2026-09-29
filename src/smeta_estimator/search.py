"""Простой поиск кандидатов по ключевым словам — не embeddings.

По договорённости (`docs/agent4-ai-matching-feasibility.md`): полноценный
семантический поиск можно добавить позже, для фундамента достаточно
пересечения слов запроса с названием позиции ГЭСН. Формулировки ГЭСН сильно
параметризованы — «похожий текст» не гарантия «тот самый код», поэтому
результат — всегда список кандидатов на выбор эксперта, не единственный
ответ (см. `models.MatchResult`).

`search_material_candidates()` — тот же принцип, применённый к каталогу
материалов (ФСБЦ_Мат&Оборуд), добавлен 2026-09-28: строка ведомости объёмов
работ может оказаться по сути материалом/изделием, а не нормируемой
работой (найдено на реальных сметах — «Уголок алюминиевый декоративный»,
«Светильник светодиодный...» и подобные искали по каталогу работ и
находили случайные, ничего не значащие совпадения). Использует ТОТ ЖЕ
`tokenize_words()`, что и `search_candidates()` — принципиально, не другую
функцию сопоставления (например, `text_matching.stem_words`, которая
используется в `material_candidates.py`/`work_volume_extractor.py` для
других задач): `estimate_smeta_document.py` сравнивает `match_score` из
обоих поисков напрямую, и это сравнение честно только если оценка
считается одинаково для обоих каталогов."""

from __future__ import annotations

import re

from .models import GesnWorkItem, MaterialCandidateInfo, MaterialRateCandidate, RateCandidate

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
_STOPWORDS = {"и", "в", "с", "со", "на", "из", "для", "по", "к", "от", "не", "или", "при", "без"}


def tokenize_words(text: str) -> set[str]:
    return {
        word
        for word in (m.lower() for m in _WORD_RE.findall(text))
        if len(word) >= 3 and word not in _STOPWORDS
    }


def search_candidates(
    catalog: list[GesnWorkItem], query_text: str, top_n: int = 5
) -> list[RateCandidate]:
    """Оценка релевантности — доля слов запроса, найденных в названии
    позиции (0..1). Позиции без пересечения слов вообще не попадают в
    выдачу — не нулевой скор, а полное отсутствие в списке."""
    query_tokens = tokenize_words(query_text)
    if not query_tokens:
        return []

    scored: list[tuple[float, GesnWorkItem]] = []
    for item in catalog:
        item_tokens = tokenize_words(item.name)
        overlap = query_tokens & item_tokens
        if not overlap:
            continue
        scored.append((len(overlap) / len(query_tokens), item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        RateCandidate(
            code=item.code,
            name=item.name,
            unit=item.unit,
            base_price=item.base_price,
            match_score=score,
            resources=list(item.resources),
            unpriced_resource_codes=list(item.unpriced_resource_codes),
            abstract_resource_codes=list(item.abstract_resource_codes),
        )
        for score, item in scored[:top_n]
    ]


# Порог уверенности для выбора между каталогом работ и каталогом материалов
# (и для честного "кандидат не найден") — подобран по факту распределения
# score на двух реальных сметах (кровля №0373100025626000005, потолок
# №0373100025726000259, 2026-09-28), не с потолка: все подтверждённые
# правильные совпадения (реальный код из смёты заказчика нашёлся среди
# кандидатов) дали score >= 0.50 (минимум — 0.50, у «Устройство защитной
# декоративной сетки... демонтаж»), все случайные/ошибочные совпадения по
# обоим документам — score <= 0.42 (максимум — 0.42, у «Вырезка сухих
# ветвей», и это сам по себе спорный случай: найден код для другой породы
# дерева, не тот, что в смете — то есть даже 0.42 стоило бы отсеять).
# 0.5 — прямо на нижней границе подтверждённых верных совпадений, с запасом
# выше любого найденного шума.
MATCH_SCORE_THRESHOLD = 0.5


def choose_candidate_source(
    work_candidates: list[RateCandidate],
    material_candidates: list[MaterialRateCandidate],
    threshold: float = MATCH_SCORE_THRESHOLD,
) -> str | None:
    """Какой из двух списков кандидатов использовать для строки ведомости —
    по факту, чему совпадение нашлось увереннее (выше `match_score`), а не
    по эвристике вроде единицы измерения. Возвращает `"work"`, `"material"`
    или `None`.

    `None` — честный случай "кандидат не найден", в двух случаях: (1) оба
    списка пусты; (2) лучший score из обоих каталогов ниже `threshold` —
    низкая уверенность в обоих путях не должна выдаваться как находка
    только потому, что один edge чуть выше другого (например, 0.29 против
    0.21 — оба одинаково не находка)."""
    work_best = work_candidates[0].match_score if work_candidates else -1.0
    material_best = material_candidates[0].match_score if material_candidates else -1.0
    best = max(work_best, material_best)
    if best < threshold:
        return None
    return "work" if work_best >= material_best else "material"


def search_material_candidates(
    catalog: list[MaterialCandidateInfo], query_text: str, top_n: int = 5
) -> list[MaterialRateCandidate]:
    """То же самое, что `search_candidates()`, но по каталогу материалов
    ФСБЦ вместо каталога работ ГЭСН/ГЭСНр. Региональная цена (`unit_price`/
    `price_source`) здесь ещё не посчитана — только `base_price_2022` и
    `match_score`, цену определяет отдельно `pricing.
    price_material_candidates_for_region()`, как и у `RateCandidate`."""
    query_tokens = tokenize_words(query_text)
    if not query_tokens:
        return []

    scored: list[tuple[float, MaterialCandidateInfo]] = []
    for item in catalog:
        item_tokens = tokenize_words(item.name)
        overlap = query_tokens & item_tokens
        if not overlap:
            continue
        scored.append((len(overlap) / len(query_tokens), item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        MaterialRateCandidate(
            code=item.code,
            name=item.name,
            unit=item.unit,
            match_score=score,
            base_price_2022=item.price,
        )
        for score, item in scored[:top_n]
    ]

"""Простой поиск кандидатов по ключевым словам — не embeddings.

По договорённости (`docs/agent4-ai-matching-feasibility.md`): полноценный
семантический поиск можно добавить позже, для фундамента достаточно
пересечения слов запроса с названием позиции ГЭСН. Формулировки ГЭСН сильно
параметризованы — «похожий текст» не гарантия «тот самый код», поэтому
результат — всегда список кандидатов на выбор эксперта, не единственный
ответ (см. `models.MatchResult`).
"""

from __future__ import annotations

import re

from .models import GesnWorkItem, RateCandidate

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
_STOPWORDS = {"и", "в", "с", "со", "на", "из", "для", "по", "к", "от", "не", "или", "при", "без"}


def _tokenize(text: str) -> set[str]:
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
    query_tokens = _tokenize(query_text)
    if not query_tokens:
        return []

    scored: list[tuple[float, GesnWorkItem]] = []
    for item in catalog:
        item_tokens = _tokenize(item.name)
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
            unpriced_resource_codes=list(item.unpriced_resource_codes),
            abstract_resource_codes=list(item.abstract_resource_codes),
        )
        for score, item in scored[:top_n]
    ]

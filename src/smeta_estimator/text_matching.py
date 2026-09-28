"""Общий грубый морфологический разбор для сопоставления русскоязычных
названий по словам — вынесено из `material_candidates.py`, 2026-09-28, при
добавлении второго потребителя (`work_volume_extractor.py`), чтобы логика
сопоставления не разъезжалась по двум копиям.

Сопоставление по первым `STEM_LEN` символам каждого слова, не полноценная
морфология — тот же осознанный компромисс, что был описан изначально в
`material_candidates.py`: не теряет совпадения из-за русских словоформ
("рулонный"/"рулонные", "кровля"/"кровли"), но и не идеально (может изредка
склеить разные слова с общим началом)."""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
_STOPWORDS = {"и", "в", "с", "со", "на", "из", "для", "по", "к", "от", "не", "или", "при", "без"}
STEM_LEN = 5


def stem_words(text: str) -> set[str]:
    words = (m.lower() for m in _WORD_RE.findall(text))
    return {w[:STEM_LEN] for w in words if len(w) >= 3 and w not in _STOPWORDS}

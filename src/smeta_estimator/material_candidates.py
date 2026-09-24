"""Подбор кандидатов-продуктов для категории `AbstractResource` — Агент 4.

Продуктовое решение владельца (подтверждено сверкой с двумя независимыми
источниками, 2026-09-24 — официальная документация ГРАНД-Сметы и
Турбо-сметчика): в обеих программах неучтённые/абстрактные ресурсы явно
выделяются цветом (красным/розовым) и требуют ручного выбора эксперта, не
подставляются автоматически. Это отраслевой стандарт, не наша осторожность
— поэтому `AbstractResource` по-прежнему остаётся `unresolved`
(`fsnb_parser.apply_prices`), автоматической подстановки продукта здесь и
не будет.

Единственное, что добавлено — вспомогательный список из нескольких (обычно
3-5) кандидатов-продуктов той же категории, по аналогии с «подбором из
списка однотипных материалов по коду» в ГРАНД-Смете, чтобы эксперту было из
чего выбирать, а не искать вручную по всему каталогу ФГИС ЦС. Источник
кандидатов — тот же каталог материалов (`fsnb_parser.
parse_material_catalog_xml`, то есть те же данные, что `resource_base_prices`
/`current_prices`, но с сохранённым названием), отфильтрованный по
совпадению слов названия категории со словами названия ресурса.

Сопоставление названий — грубое, по первым `_STEM_LEN` символам каждого
слова (не полноценная морфология), чтобы не терять совпадения из-за
русских словоформ вроде "рулонный"/"рулонные", "материал"/"материалы".
Итоговый список кандидатов — не решение агента: сортирован по цене и несёт
видимый диапазон (`AbstractResourceCandidates.price_range`), а не один
«самый дешёвый» вариант — окончательный выбор всегда за экспертом
(`select_candidate` у `MatchResult`, тот же паттерн, что и у самой позиции
ГЭСН).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import MaterialCandidateInfo

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
_STOPWORDS = {"и", "в", "с", "со", "на", "из", "для", "по", "к", "от", "не", "или", "при", "без"}
_STEM_LEN = 5


def _stems(text: str) -> set[str]:
    words = (m.lower() for m in _WORD_RE.findall(text))
    return {w[:_STEM_LEN] for w in words if len(w) >= 3 and w not in _STOPWORDS}


@dataclass
class AbstractResourceCandidates:
    """Кандидаты-продукты для одной категории `AbstractResource` — не
    решение, эксперт выбирает сам. Пустой `candidates` — честный случай:
    в каталоге не нашлось ничего похожего по названию, не тихая заглушка."""

    category_code: str
    category_name: str
    candidates: list[MaterialCandidateInfo] = field(default_factory=list)

    @property
    def price_range(self) -> tuple[float, float] | None:
        if not self.candidates:
            return None
        prices = [c.price for c in self.candidates]
        return (min(prices), max(prices))


def suggest_material_candidates(
    category_code: str,
    category_name: str,
    catalog: list[MaterialCandidateInfo],
    top_n: int = 5,
) -> AbstractResourceCandidates:
    """Ищет в `catalog` продукты, чьё название пересекается по словам с
    `category_name` (названием `AbstractResource` из ГЭСН), и возвращает до
    `top_n` кандидатов, отсортированных по цене — по возрастанию, чтобы
    сразу был виден диапазон, а не единственный «самый дешёвый» вариант.

    Ранжирование при отборе (если подходящих кандидатов больше `top_n`) —
    по доле пересечения слов с названием категории, с ценой как
    вторичным критерием; но итоговый порядок в выдаче — всегда по цене."""
    query_stems = _stems(category_name)
    if not query_stems:
        return AbstractResourceCandidates(category_code, category_name, [])

    scored: list[tuple[float, MaterialCandidateInfo]] = []
    for item in catalog:
        overlap = query_stems & _stems(item.name)
        if not overlap:
            continue
        scored.append((len(overlap) / len(query_stems), item))

    scored.sort(key=lambda pair: (-pair[0], pair[1].price))
    top = [item for _, item in scored[:top_n]]
    top.sort(key=lambda item: item.price)
    return AbstractResourceCandidates(category_code, category_name, top)

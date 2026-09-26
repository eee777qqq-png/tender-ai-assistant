"""Справочник ОКПД2 по разделу «Строительство» (Агент 2 — Классификатор).

Источник данных: официальный классификатор ОК 034-2014 (КПЕС 2008), выгрузка
владельца продукта (`data/okpd2_construction.csv`) — все коды с корнем 41
(«Здания и работы по возведению зданий»), 42 («Сооружения и строительные
работы в области гражданского строительства») и 43 («Работы строительные
специализированные»), 548 записей всех уровней вложенности.

Это полный официальный раздел «Строительство» — по решению владельца
продукта в скоуп идёт вся стройка целиком, без сужения под нишу пилота
(капремонт), см. CLAUDE.md.

**2026-09-26: скоуп расширен за пределы раздела «Строительство».** Реальный
профиль первого клиента пилота (Edwin, ИП, самосвалы) фактически занимается
не только стройкой, а перевозкой сыпучих материалов и благоустройством —
это отдельные коды ОКПД2 49.41 («Услуги по автомобильной перевозке грузов»)
и 81.30 («Услуги по благоустройству ландшафта»), не входящие в разделы
41/42/43. Добавлены 15 записей (родительская иерархия + конкретные коды
49.41.19.900/49.41.20.000/81.30.10.000, встреченные в реальных извещениях
ЕИС) — проверено `grep`-подобным сканированием `data/raw_notices`/
`data/raw_notices_r61` перед добавлением: 5 реальных документов с этими
кодами нашлось (из 1293 просканированных), не ноль. Названия этих 15 новых
записей — рабочий перевод по памяти официального текста ОК 034-2014, НЕ
сверены построчно с тем же источником (`okpd2.xlsx` владельца), что и
исходные 548 записей — для работы классификатора (`classify()`/
`is_construction_code()`) это не критично: сопоставление идёт по коду, не
по названию, а имя используется только для отображения.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CSV_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "okpd2_construction.csv"


@dataclass(frozen=True)
class Okpd2Code:
    code: str
    name: str
    level: int


def load_construction_codes(csv_path: Path | str | None = None) -> list[Okpd2Code]:
    path = Path(csv_path) if csv_path is not None else DEFAULT_CSV_PATH
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            Okpd2Code(code=row["code"], name=row["name"], level=int(row["level"]))
            for row in reader
        ]


class ConstructionClassifier:
    """Определяет, относится ли ОКПД2-код к разделу «Строительство»,
    и подбирает для него человекочитаемое название из справочника."""

    def __init__(self, codes: list[Okpd2Code] | None = None):
        self._codes = codes if codes is not None else load_construction_codes()
        self._by_code = {c.code: c for c in self._codes}

    def is_construction_code(self, code: str) -> bool:
        return self.classify(code) is not None

    def classify(self, code: str) -> Okpd2Code | None:
        """Возвращает наиболее точную запись справочника для кода.

        Если точного совпадения нет, поднимается вверх по иерархии
        (отбрасывая последний `.сегмент`), чтобы найти ближайшего предка —
        это покрывает коды глубже, чем есть в справочнике.
        """
        segments = code.strip().split(".")
        while segments:
            candidate = ".".join(segments)
            match = self._by_code.get(candidate)
            if match is not None:
                return match
            segments.pop()
        return None

    def search(self, keyword: str) -> list[Okpd2Code]:
        keyword_lower = keyword.lower()
        return [c for c in self._codes if keyword_lower in c.name.lower()]

    def all_codes(self) -> list[Okpd2Code]:
        return list(self._codes)

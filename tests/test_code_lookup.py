"""Тесты прямого поиска кандидата по коду из колонки «Обоснование»
готовой сметы (Агент 4, CLAUDE.md — открытый п.17, «Следующий шаг круга 2»,
пункт «а»). Нормализация проверяется на реальных формах записи кода,
встретившихся в круге 2 бенчмарка (2026-09-29): «ГЭСНр 68-02-004-04» (с
пробелом после приставки — так пишет эксперт-сметчик в «Обосновании»,
хотя в самом каталоге код хранится без пробела), «ГЭСН 01-02-057-02»
(приставка обычная, в каталоге хранится без неё вообще), код материала
ФСБЦ с точками (не трогается)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smeta_estimator.code_lookup import (
    classify_unresolved_row,
    find_exact_material_candidate,
    find_exact_work_candidate,
    normalize_gesn_code,
)
from smeta_estimator.models import GesnWorkItem, MaterialCandidateInfo


def test_normalize_strips_space_after_gesnr_prefix():
    assert normalize_gesn_code("ГЭСНр 68-02-004-04") == "ГЭСНр68-02-004-04"


def test_normalize_strips_gesn_prefix_entirely():
    # Базовый каталог ГЭСН хранит код БЕЗ приставки вообще — "ГЭСН " в
    # "Обосновании" эксперта убирается целиком, не просто пробел.
    assert normalize_gesn_code("ГЭСН 01-02-057-02") == "01-02-057-02"


def test_normalize_leaves_material_code_with_dots_unchanged():
    assert normalize_gesn_code("08.3.05.05-0053") == "08.3.05.05-0053"


def test_normalize_handles_gesnr_without_space_already():
    assert normalize_gesn_code("ГЭСНр68-02-004-04") == "ГЭСНр68-02-004-04"


def test_normalize_empty_string_gives_empty_string():
    assert normalize_gesn_code("") == ""
    assert normalize_gesn_code("   ") == ""


def test_normalize_strips_fsbc_prefix():
    # Найдено на реальном документе круга 1 (roof, №0373100025626000005):
    # ГРАНД-Смета пишет в "Обосновании" материала код с приставкой
    # "ФСБЦ-", которую каталог материалов не хранит вообще — до фикса
    # ни один материал с этой приставкой не находился точным поиском.
    assert normalize_gesn_code("ФСБЦ-11.1.03.01-0065") == "11.1.03.01-0065"


def test_normalize_drops_footnote_after_newline():
    # Найдено там же: ячейка иногда содержит перенос строки с названием
    # файла-источника цены после кода ("...\nСплит-форма город Москва на
    # 3 квартал 2026 года.xlsx") — раньше это склеивалось в мусорный токен.
    raw = "ФСБЦ-11.1.03.01-0065\nСплит-форма город Москва на 3 квартал 2026 года.xlsx"
    assert normalize_gesn_code(raw) == "11.1.03.01-0065"


def test_find_exact_material_candidate_matches_real_fsbc_code_with_footnote():
    catalog = [
        MaterialCandidateInfo(
            code="11.1.03.01-0065",
            name="Брус обрезной хвойных пород",
            unit="м3",
            price=16655.0,
        ),
    ]
    raw = "ФСБЦ-11.1.03.01-0065\nСплит-форма город Москва на 3 квартал 2026 года.xlsx"
    found = find_exact_material_candidate(catalog, raw)
    assert found is not None
    assert found.code == "11.1.03.01-0065"


def _work_catalog():
    return [
        GesnWorkItem(code="01-02-057-02", name="Разборка покрытий", unit="100 м2"),
        GesnWorkItem(code="ГЭСНр68-02-004-04", name="Ремонт отмостки", unit="м2"),
    ]


def _material_catalog():
    return [
        MaterialCandidateInfo(code="08.3.05.05-0053", name="Щебень фракция 20-40 мм", unit="м3", price=1500.0),
    ]


def test_find_exact_work_candidate_matches_after_normalization():
    catalog = _work_catalog()
    found = find_exact_work_candidate(catalog, "ГЭСН 01-02-057-02")
    assert found is not None
    assert found.code == "01-02-057-02"


def test_find_exact_work_candidate_matches_gesnr_with_space():
    catalog = _work_catalog()
    found = find_exact_work_candidate(catalog, "ГЭСНр 68-02-004-04")
    assert found is not None
    assert found.code == "ГЭСНр68-02-004-04"


def test_find_exact_work_candidate_returns_none_for_empty_code():
    assert find_exact_work_candidate(_work_catalog(), "") is None
    assert find_exact_work_candidate(_work_catalog(), "   ") is None


def test_find_exact_work_candidate_returns_none_when_code_not_in_catalog():
    assert find_exact_work_candidate(_work_catalog(), "ГЭСН 99-99-999-99") is None


def test_find_exact_material_candidate_matches_by_code():
    catalog = _material_catalog()
    found = find_exact_material_candidate(catalog, "08.3.05.05-0053")
    assert found is not None
    assert found.name == "Щебень фракция 20-40 мм"


def test_find_exact_material_candidate_returns_none_when_not_found():
    assert find_exact_material_candidate(_material_catalog(), "08.3.05.05-9999") is None


def test_classify_unresolved_row_recognizes_market_quote_reference():
    # Реальный паттерн круга 1 (ceiling): "ТЦ_20.3.03.07_77_..." — снятая
    # котировка конкретного поставщика, не код каталога.
    result = classify_unresolved_row("ТЦ_20.3.03.07_77_7722753969_30.06.2026_02_11.2", "Светильник ЭРА SPO-6-36-4K-P-EM")
    assert result is not None
    assert "рыночная котировка" in result


def test_classify_unresolved_row_recognizes_gesnm_prefix():
    result = classify_unresolved_row("ГЭСНм10-08-002-02", "Извещатель ПС автоматический")
    assert result is not None
    assert "ГЭСНм" in result


def test_classify_unresolved_row_recognizes_order_paragraph_reference():
    # Реальный паттерн круга 1 (ceiling): "421/пр_2020_п.75_пп.а".
    result = classify_unresolved_row("421/пр_2020_п.75_пп.а", "Вспомогательные ненормируемые материальные ресурсы")
    assert result is not None
    assert "приказ" in result


def test_classify_unresolved_row_recognizes_transport_by_name():
    result = classify_unresolved_row("47-1", "Погрузка в автотранспортное средство: мусор строительный")
    assert result is not None
    assert "транспортный" in result

    result2 = classify_unresolved_row("02-15-1-01-0014", "Перевозка грузов I класса автомобилями-самосвалами")
    assert result2 is not None


def test_classify_unresolved_row_returns_none_for_genuinely_unclear_case():
    # Ни один известный класс не подходит — причина должна остаться общей,
    # не домысленной.
    assert classify_unresolved_row("12-34-567-89", "Устройство чего-то совершенно нового") is None
    assert classify_unresolved_row(None, "Работа без кода в Обосновании вообще") is None


def test_normalize_keeps_gesnm_prefix_like_gesnr():
    # Реальный код из «Потолка» (2026-10-03): приставка ГЭСНм — часть кода в
    # каталоге, её нельзя снимать как общую "ГЭСН" (получилось бы "м10-08-...").
    assert normalize_gesn_code("ГЭСНм10-08-002-02") == "ГЭСНм10-08-002-02"
    assert normalize_gesn_code("ГЭСНм 08-03-610-01") == "ГЭСНм08-03-610-01"
    assert normalize_gesn_code("ГЭСН 11-01-027-03") == "11-01-027-03"

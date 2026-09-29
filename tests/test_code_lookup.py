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

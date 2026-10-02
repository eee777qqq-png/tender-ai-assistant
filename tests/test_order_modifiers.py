"""Модификаторы условий производства работ из ссылок на пункт приказа в
«Обосновании» — найдено на реальном документе ("потолок", тендер
№0373100025726000259, 2026-09-30, см. CLAUDE.md открытый п.19). Паттерны
ниже — реальный текст, скопированный дословно из этого документа."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smeta_estimator.order_modifiers import (
    ORDER_MODIFIER_REGISTRY,
    OrderModifier,
    normalize_order_reference,
    resolve_order_modifiers,
)


def test_normalizes_real_421_pr_reference():
    assert normalize_order_reference("421/пр_2020_п.58_пп.б") == "421/пр.58_пп.б"


def test_normalizes_real_571_pr_reference_keeps_table_cell_suffix_in_key():
    # Суффикс "_т.N_стр.M_стб.K" — часть ключа, НЕ отбрасывается: один и тот
    # же пункт 83 — таблица с разными строками, дающими разные коэффициенты
    # (найдено на реальных документах "потолок" и "roof", 2026-09-30, см.
    # докстринг order_modifiers.py про найденную и исправленную коллизию).
    assert normalize_order_reference("571/пр_2022_п.83_т.2_стр.3_стб.3") == "571/пр.83_т.2_стр.3_стб.3"
    assert normalize_order_reference("571/пр_2022_п.83_т.2_стр.4_стб.3") == "571/пр.83_т.2_стр.4_стб.3"
    assert normalize_order_reference("571/пр_2022_п.84_т.3_стр.1_стб.3") == "571/пр.84_т.3_стр.1_стб.3"


def test_normalizes_unrelated_421_pr_75_reference_distinctly():
    # Другой пункт того же приказа — не про модификатор условий, про 2%
    # надбавку на вспомогательные материалы (см. code_lookup.classify_unresolved_row).
    # Ключ отличается от п.58, коллизии нет.
    assert normalize_order_reference("421/пр_2020_п.75_пп.а") == "421/пр.75_пп.а"


def test_non_reference_text_returns_none():
    assert normalize_order_reference("ГЭСНр58-01-005-03") is None
    assert normalize_order_reference("08.3.05.05-0055") is None
    assert normalize_order_reference("") is None
    assert normalize_order_reference(None) is None  # type: ignore[arg-type]


def test_registry_has_all_confirmed_real_values_including_the_collision_fix():
    assert ORDER_MODIFIER_REGISTRY["421/пр.58_пп.б"] == OrderModifier(ozp=1.15, em=1.25, zpm=1.25, mat=1.0)
    # Тот же пункт 83, РАЗНЫЕ строки таблицы -> разные значения (потолок vs roof).
    assert ORDER_MODIFIER_REGISTRY["571/пр.83_т.2_стр.3_стб.3"] == OrderModifier(ozp=0.4, em=0.4, zpm=0.4, mat=0.0)
    assert ORDER_MODIFIER_REGISTRY["571/пр.83_т.2_стр.4_стб.3"] == OrderModifier(ozp=0.7, em=0.7, zpm=0.7, mat=0.0)
    assert ORDER_MODIFIER_REGISTRY["571/пр.84_т.3_стр.1_стб.3"] == OrderModifier(ozp=0.7, em=0.7, zpm=0.7, mat=0.0)


def test_resolve_single_known_reference():
    resolved = resolve_order_modifiers(["421/пр_2020_п.58_пп.б"])

    assert resolved.combined == OrderModifier(ozp=1.15, em=1.25, zpm=1.25, mat=1.0)
    assert resolved.matched_references == ["421/пр_2020_п.58_пп.б"]
    assert resolved.unmatched_references == []


def test_resolve_ignores_overhead_order_references_that_are_not_condition_modifiers():
    # "Пр/812-..." / "Пр/774-..." — ссылки на пункты приказов НР/СП, тоже
    # содержат "N/пр" (широкий фильтр work_volume_extractor их подхватывает
    # вместе с реальными модификаторами условий, см. реальный пример на
    # документе "потолок"), но это не про модификатор условий вообще —
    # normalize_order_reference() их не матчит (после "N/пр" не следует
    # "п.NN" напрямую), они должны молча игнорироваться, не путаться с
    # реально применённым модификатором.
    resolved = resolve_order_modifiers(
        [
            "421/пр_2020_п.58_пп.б",
            "Пр/812-016.0-1, Приказ № 812/пр от 21.12.2020 п.25",
            "Пр/774-016.0, Приказ № 774/пр от 11.12.2020 п.16",
        ]
    )

    assert resolved.combined == OrderModifier(ozp=1.15, em=1.25, zpm=1.25, mat=1.0)
    assert resolved.matched_references == ["421/пр_2020_п.58_пп.б"]
    assert resolved.unmatched_references == []


def test_resolve_combines_two_references_multiplicatively():
    # Реальный случай (позиция "2" документа "потолок") — демонтажный код,
    # переиспользующий монтажную норму, несёт ОБЕ ссылки одновременно.
    resolved = resolve_order_modifiers(["571/пр_2022_п.83_т.2_стр.3_стб.3", "421/пр_2020_п.58_пп.б"])

    assert resolved.combined is not None
    assert resolved.combined.ozp == 0.4 * 1.15
    assert resolved.combined.em == 0.4 * 1.25
    assert resolved.combined.zpm == 0.4 * 1.25
    assert resolved.combined.mat == 0.0 * 1.0
    assert resolved.unmatched_references == []


def test_resolve_no_references_gives_no_modifier():
    resolved = resolve_order_modifiers([])

    assert resolved.combined is None
    assert resolved.unmatched_references == []


def test_resolve_unknown_reference_is_reported_not_guessed():
    # Ссылка похожа на настоящую (проходит normalize_order_reference), но
    # справочник её не знает — критерий готовности задачи: не угадывать
    # модификатор, помечать для эксперта.
    resolved = resolve_order_modifiers(["999/пр_2030_п.1"])

    assert resolved.combined is None
    assert resolved.unmatched_references == ["999/пр.1"]


def test_resolve_mix_of_known_and_unknown_references():
    resolved = resolve_order_modifiers(["421/пр_2020_п.58_пп.б", "999/пр_2030_п.1"])

    assert resolved.combined == OrderModifier(ozp=1.15, em=1.25, zpm=1.25, mat=1.0)
    assert resolved.unmatched_references == ["999/пр.1"]


def test_resolve_distinguishes_same_point_different_table_row():
    # Прямая проверка исправленной коллизии: "571/пр.83" сам по себе
    # неоднозначен, а с суффиксом — нет.
    ceiling_case = resolve_order_modifiers(["571/пр_2022_п.83_т.2_стр.3_стб.3"])
    roof_case = resolve_order_modifiers(["571/пр_2022_п.83_т.2_стр.4_стб.3"])

    assert ceiling_case.combined == OrderModifier(ozp=0.4, em=0.4, zpm=0.4, mat=0.0)
    assert roof_case.combined == OrderModifier(ozp=0.7, em=0.7, zpm=0.7, mat=0.0)
    assert ceiling_case.combined != roof_case.combined


def test_resolve_ignores_strings_that_are_not_order_references_at_all():
    # work_volume_extractor._ORDER_REFERENCE_HINT_RE — широкий фильтр,
    # order_modifiers должен честно игнорировать то, что до него дошло
    # случайно (не должен попадать ни в combined, ни в unmatched).
    resolved = resolve_order_modifiers(["ГЭСНр58-01-005-03"])

    assert resolved.combined is None
    assert resolved.unmatched_references == []

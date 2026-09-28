"""Тесты подбора кандидатов объёма работ (Агент 4, открытый п.8 в CLAUDE.md)
— на придуманных, но правдоподобных таблицах ведомости объёмов работ, тем
же путём, каким изначально проверялись Агенты 3/4 до появления реальных
документов. Не подтверждено на реальной ведомости — см. предупреждение в
`work_volume_extractor.py`/`table_reader.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smeta_estimator.work_volume_extractor import (
    extract_work_volume_rows,
    suggest_work_volume_candidates,
)

_TYPICAL_TABLE = [
    ["№", "Наименование работ", "Ед. изм.", "Количество"],
    ["1", "Ремонт кровли из рулонных материалов", "м2", "250"],
    ["2", "Устройство стяжки цементно-песчаной", "м3", "12,5"],
    ["3", "Итого", "", ""],
]


def test_extracts_rows_below_header_and_skips_total_row():
    rows = extract_work_volume_rows([_TYPICAL_TABLE])

    assert len(rows) == 2
    assert rows[0].name == "Ремонт кровли из рулонных материалов"
    assert rows[0].unit == "м2"
    assert rows[0].quantity == 250.0
    # Десятичная запятая, не точка — как обычно оформляют в русских сметах.
    assert rows[1].quantity == 12.5
    # Строка "Итого" без числа в графе количества — честно пропущена, не 0.
    assert all(row.name != "Итого" for row in rows)


def test_table_without_matching_header_yields_no_rows():
    unrelated_table = [["Заказчик", "ГБУЗ №1"], ["Адрес", "г. Москва"]]
    assert extract_work_volume_rows([unrelated_table]) == []


def test_handles_thousands_separator_with_space():
    table = [
        ["Наименование работ", "Ед. изм.", "Количество"],
        ["Устройство ограждения", "м", "1 250"],
    ]
    rows = extract_work_volume_rows([table])
    assert rows[0].quantity == 1250.0


def test_scans_multiple_tables_independently():
    other_table = [["Наименование работ", "Ед. изм.", "Количество"], ["Демонтаж кровли", "м2", "300"]]
    rows = extract_work_volume_rows([_TYPICAL_TABLE, other_table])
    assert len(rows) == 3
    assert rows[-1].name == "Демонтаж кровли"
    assert rows[-1].table_index == 1


def test_suggests_candidates_ranked_by_word_overlap():
    rows = extract_work_volume_rows([_TYPICAL_TABLE])

    result = suggest_work_volume_candidates("Ремонт кровли на битумной мастике", rows)

    assert result.query_text == "Ремонт кровли на битумной мастике"
    assert len(result.candidates) == 1
    assert result.candidates[0].name == "Ремонт кровли из рулонных материалов"


def test_no_matching_rows_is_honest_empty_list():
    rows = extract_work_volume_rows([_TYPICAL_TABLE])

    result = suggest_work_volume_candidates("Монтаж вентиляционной системы приточной", rows)

    assert result.candidates == []


def test_matches_across_singular_plural_word_forms():
    rows = extract_work_volume_rows([_TYPICAL_TABLE])

    # "стяжки"/"стяжка" — тот же грубый стемминг по первым 5 символам, что и
    # в material_candidates.py, теперь общий (text_matching.stem_words).
    result = suggest_work_volume_candidates("Устройство стяжка из цементно-песчаного раствора", rows)

    assert len(result.candidates) == 1
    assert result.candidates[0].name == "Устройство стяжки цементно-песчаной"

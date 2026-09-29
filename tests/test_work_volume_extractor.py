"""Тесты подбора кандидатов объёма работ (Агент 4, открытый п.8 в CLAUDE.md)
— на придуманных, но правдоподобных таблицах ведомости объёмов работ, тем
же путём, каким изначально проверялись Агенты 3/4 до появления реальных
документов. Не подтверждено на реальной ведомости — см. предупреждение в
`work_volume_extractor.py`/`table_reader.py`.

Класс `test_real_lsr_structure_*` — исключение: воспроизводит структуру
колонок РЕАЛЬНОЙ сметы (ЛСР 02-01-01, закупка №0373100025626000005,
"Ремонт кровли...", скачана 2026-09-27), где обнаружился баг — вложенные
строки ресурсов/труда/накладных расходов внутри позиции принимались за
самостоятельные позиции работ (итог был завышен в ~180 раз). Значения
урезаны/не все колонки — форма таблицы (номер позиции в отдельной колонке,
у вложенных строк эта колонка пустая) воспроизведена по реальному файлу."""

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


# Колонки в том же порядке, что у реальной ЛСР: № п/п | Обоснование |
# Наименование работ и затрат | Единица измерения | Количество.
_REAL_LSR_TABLE = [
    ["№ п/п", "Обоснование", "Наименование работ и затрат", "Единица измерения", "Количество"],
    # Строка-легенда номеров столбцов, которую ГРАНД-Смета печатает сразу
    # под заголовком — у неё случайно валидный номер позиции ("1"), но
    # название — тоже голое число ("3"), не текст работы.
    ["1", "2", "3", "4", "5"],
    ["1", "ГЭСНр58-01-005-03", "Ремонт деревянных элементов конструкций крыш: смена стропильных ног", "100 м", "0.16"],
    # Вложенные строки ресурсов/труда внутри позиции 1 — колонка № п/п пустая.
    ["", "1", "ОТ(ЗТ)", "чел.-ч", "24.0144"],
    ["", "1-100-30", "Средний разряд работы 3,0", "чел.-ч", "150.09"],
    ["", "Пр/812-092.0-1", "НР Крыши, кровли (ремонтно-строительные)", "%", "90"],
    ["", "Пр/774-092.0", "СП Крыши, кровли (ремонтно-строительные)", "%", "46"],
    # Позиция 2 — материал по прямой ФСБЦ-позиции, без кода ГЭСН.
    ["2", "ФСБЦ-11.1.03.01-0065", "Брус обрезной хвойных пород (ель, сосна)", "м3", "0.36"],
]


def test_real_lsr_structure_keeps_only_numbered_positions():
    rows = extract_work_volume_rows([_REAL_LSR_TABLE])

    names = [row.name for row in rows]
    assert names == [
        "Ремонт деревянных элементов конструкций крыш: смена стропильных ног",
        "Брус обрезной хвойных пород (ель, сосна)",
    ]


def test_real_lsr_structure_drops_nested_resource_and_overhead_rows():
    rows = extract_work_volume_rows([_REAL_LSR_TABLE])

    names = [row.name for row in rows]
    assert "Средний разряд работы 3,0" not in names
    assert "НР Крыши, кровли (ремонтно-строительные)" not in names
    assert "СП Крыши, кровли (ремонтно-строительные)" not in names
    assert "ОТ(ЗТ)" not in names


def test_real_lsr_structure_drops_column_number_legend_row():
    rows = extract_work_volume_rows([_REAL_LSR_TABLE])

    # Строка-легенда ("1 | 2 | 3 | 4 | 5") не должна была бы дать позицию
    # с названием "3" — она голое число, не текст работы.
    assert all(row.name != "3" for row in rows)


def test_table_without_position_column_keeps_old_behaviour():
    # Без колонки "№ п/п" в заголовке фильтр по номеру позиции не
    # применяется вообще — обратная совместимость с _TYPICAL_TABLE и
    # другими таблицами без выделенной колонки номера (см. тесты выше).
    rows = extract_work_volume_rows([_TYPICAL_TABLE])
    assert len(rows) == 2

"""Транспорт/ПРР по описанию строки — на реальном фрагменте ответов ФГИС ЦС
(Рязанская обл., 2 кв. 2026) и реальных формулировках строк из 5 смет
проекта («Рыбное», Краснодар, МО, кровля, потолок)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator.transport_pricing import (
    TransportPriceBook,
    detect_transport_operation,
    parse_transportation,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "fgiscs_transport_ryazan_sample.json").read_text(encoding="utf-8")
)

RYBNOE_LOAD = "Погрузка в автотранспортное средство: мусор строительный с погрузкой транспортерами"
RYBNOE_TRANSPORT = (
    "Перевозка грузов I класса автомобилями-самосвалами грузоподъемностью до 15 т по дорогам с "
    "усовершенствованным (асфальтобетонным, цементобетонным, железобетонным, обработанным органическим "
    "вяжущим) дорожным покрытием на расстояние 30 км"
)


def _filter_values(level, **selected):
    if level == "RoadType":
        return FIXTURE["road_types"]
    if level == "VehicleType":
        return FIXTURE["vehicle_types"]
    if selected.get("vehicleType") == "Автомобили-самосвалы":
        return FIXTURE["capacities_samosval"]
    return []


def make_book(calls=None):
    calls = calls if calls is not None else []

    def fetch_transportation(road, vehicle, capacity):
        calls.append((road, vehicle, capacity))
        if vehicle == "Автомобили-самосвалы" and capacity == "до 15 т" and road.startswith("усовершенствованное"):
            return FIXTURE["transport_samosval_15t_improved"]
        return []

    return TransportPriceBook(
        source="ФГИС ЦС тест, Рязанская область, 2 кв. 2026",
        fetch_load_works=lambda: FIXTURE["load_works"],
        fetch_filter_values=_filter_values,
        fetch_transportation=fetch_transportation,
    )


def test_rybnoe_load_line_resolves_by_description_with_exact_price_and_source():
    match = make_book().match(RYBNOE_LOAD, "48-1")

    assert match.unit_price == pytest.approx(217.94)  # ровно как в смете «Рыбное»
    assert match.official_code == "48-1"
    assert match.document_code_agrees is True
    assert "ФГИС ЦС" in match.source


def test_rybnoe_transport_line_resolves_by_description_with_exact_price_and_source():
    match = make_book().match(RYBNOE_TRANSPORT, "02-15-1-01-0030")

    assert match.unit_price == pytest.approx(347.57)  # ровно как в смете «Рыбное»
    assert match.official_code == "02-15-1-01-0030"
    assert match.document_code_agrees is True
    assert "ФГИС ЦС" in match.source


def test_match_does_not_depend_on_document_code():
    # Тот же результат без кода и с чужим/опечатанным кодом — код только
    # сверяется, но не определяет найденную позицию.
    book = make_book()
    no_code = book.match(RYBNOE_LOAD, None)
    wrong_code = book.match(RYBNOE_LOAD, "ЛЮБОЙ-КОД-123")

    assert no_code.unit_price == wrong_code.unit_price == pytest.approx(217.94)
    assert no_code.document_code_agrees is None
    assert wrong_code.document_code_agrees is False


def test_manual_loading_picks_its_own_cargo_not_the_conveyor_variant():
    match = make_book().match("Погрузка в автотранспортное средство: мусор строительный с погрузкой вручную", "47-1")

    assert match.official_code == "47-1"
    assert match.unit_price == pytest.approx(884.84)


def test_krasnodar_style_line_with_formula_on_second_line_and_capital_letter():
    name = (
        "Погрузка в автотранспортное средство: Мусор строительный с погрузкой экскаваторами емкостью ковша до 0,5 м3\n"
        "Кол-во: =(9.542+16.401)*0.97"
    )
    match = make_book().match(name, "49-1")

    assert match.official_code == "49-1"


def test_roof_style_line_with_trailing_volume_note_still_matches_cargo():
    name = "Погрузка в автотранспортное средство: прочие материалы, детали (с использованием погрузчика) 20м3*0,6т/м3+0,84 (банер)"
    match = make_book().match(name, "51-1\nСплит-форма город Москва на 3 квартал 2026 года.xlsx")

    assert match.official_code == "51-1"
    assert match.document_code_agrees is True


def test_unload_uses_unload_code_and_price():
    match = make_book().match("Разгрузка: мусор строительный с погрузкой вручную")

    assert match.operation == "unload"
    assert match.official_code == "47-2"
    assert match.unit_price == pytest.approx(733.24)


def test_ambiguous_cargo_is_not_guessed():
    # «Мусор строительный» без способа погрузки одинаково подходит под 3
    # позиции — не угадываем.
    match = make_book().match("Погрузка в автотранспортное средство: мусор строительный с погрузкой")

    assert match.unit_price is None
    assert len(match.alternatives) >= 2


def test_unknown_cargo_is_honestly_unresolved():
    match = make_book().match("Погрузка в автотранспортное средство: оборудование космическое")

    assert match.unit_price is None
    assert "не сопоставился" in match.failure_reason


def test_short_road_type_and_singular_vehicle_form_krasnodar():
    name = (
        "Перевозка грузов I класса автомобилем-самосвалом грузоподъемностью до 15 т по дорогам с "
        "усовершенствованным покрытием на расстояние 9 км\nКол-во: =9.542+16.401"
    )
    match = make_book().match(name, "02-15-1-01-0009")

    assert match.official_code == "02-15-1-01-0009"
    assert match.document_code_agrees is True


def test_transport_cargo_class_selects_price_column():
    name = RYBNOE_TRANSPORT.replace("грузов I класса", "грузов III класса")
    match = make_book().match(name)

    assert match.official_code == "02-15-3-01-0030"
    assert match.unit_price == pytest.approx(579.28)


def test_transport_table_is_fetched_once_per_combination():
    calls = []
    book = make_book(calls)
    book.match(RYBNOE_TRANSPORT)
    book.match(RYBNOE_TRANSPORT.replace("30 км", "51 км"))

    assert len(calls) == 1


def test_transport_missing_distance_in_table_is_unresolved():
    match = make_book().match(RYBNOE_TRANSPORT.replace("30 км", "31 км"))

    assert match.unit_price is None
    assert "31 км" in match.failure_reason


def test_unknown_capacity_is_unresolved_with_alternatives():
    match = make_book().match(RYBNOE_TRANSPORT.replace("до 15 т", "до 7 т"))

    assert match.unit_price is None
    assert match.alternatives == ["до 15 т", "до 30 т"]


def test_parse_reports_which_parameter_is_missing():
    assert parse_transportation("Перевозка грузов автомобилями-самосвалами на расстояние 5 км") == (
        "не найден класс груза («грузов N класса»)"
    )


def test_non_transport_rows_are_left_to_the_catalog():
    assert detect_transport_operation("Устройство покрытий на цементном растворе из плиток") is None
    assert make_book().match("Разборка покрытий кровель: из рулонных материалов", "ГЭСН46-04-008-01") is None

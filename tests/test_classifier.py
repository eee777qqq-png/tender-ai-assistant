import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classifier import ConstructionClassifier, load_construction_codes


def test_loads_expected_number_of_codes():
    codes = load_construction_codes()
    # 548 — исходный официальный раздел «Строительство» (41/42/43) + 13,
    # добавленные 2026-09-26 (49.41 автогрузоперевозки, 81.30 благоустройство
    # ландшафта — реальная ниша первого клиента пилота, см. okpd2.py).
    # Изначально было добавлено 15 строк, включая корни "49"/"81" целиком —
    # это оказалось ошибкой (поднимало в скоуп весь раздел 81, включая
    # клининг/уборку снега/вентиляцию, не только благоустройство), корневые
    # записи убраны в тот же день после честной проверки на реальных данных.
    assert len(codes) == 561


def test_scope_extension_does_not_admit_whole_root_sections():
    """Регрессия на находку 2026-09-26: добавление 49.41/81.30 не должно
    поднимать в скоуп весь раздел 49 или 81 целиком (было — по ошибке,
    из-за корневых записей "49"/"81" в CSV, которые открывали посторонние
    услуги вроде уборки снега/такси под тем же корнем)."""
    classifier = ConstructionClassifier()
    assert not classifier.is_construction_code("81.29.12.000")  # уборка снега
    assert not classifier.is_construction_code("81.22.12.000")  # очистка вентиляции
    assert not classifier.is_construction_code("81.21.10.000")  # клининг помещений
    assert not classifier.is_construction_code("49.32.11.000")  # такси


def test_scope_extended_codes_present():
    classifier = ConstructionClassifier()
    assert classifier.is_construction_code("49.41.19.900")  # автогрузоперевозки
    assert classifier.is_construction_code("81.30.10.000")  # благоустройство ландшафта


def test_top_level_sections_present():
    codes = {c.code: c.name for c in load_construction_codes()}
    assert codes["41"] == "Здания и работы по возведению зданий"
    assert codes["42"] == "Сооружения и строительные работы в области гражданского строительства"
    assert codes["43"] == "Работы строительные специализированные"


def test_is_construction_code_for_known_codes():
    classifier = ConstructionClassifier()
    assert classifier.is_construction_code("41.20.10.110")
    assert classifier.is_construction_code("43.99.90.280")


def test_is_construction_code_false_for_unrelated_code():
    classifier = ConstructionClassifier()
    assert not classifier.is_construction_code("62.01.11.000")  # разработка ПО


def test_classify_falls_back_to_closest_ancestor():
    classifier = ConstructionClassifier()
    match = classifier.classify("41.20.10.110.999")  # глубже, чем есть в справочнике
    assert match is not None
    assert match.code == "41.20.10.110"


def test_search_finds_by_keyword():
    classifier = ConstructionClassifier()
    results = classifier.search("реставрации")
    assert len(results) > 0
    assert all("реставрации" in r.name.lower() for r in results)

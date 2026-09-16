import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from classifier import ConstructionClassifier, load_construction_codes


def test_loads_expected_number_of_codes():
    codes = load_construction_codes()
    assert len(codes) == 548


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

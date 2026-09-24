"""Подбор кандидатов-продуктов для категории `AbstractResource` — на
реальной категории из наших фикстур («Материалы рулонные кровельные для
нижних слоев», код `12.1.02.15`, `tests/fixtures/gesn_roof_sample.xml`) и
на реальных ценах ФГИС ЦС (Москва, 3 квартал 2026, живьём проверено на
`fgiscs.minstroyrf.ru` 2026-09-24, не выдумано — см.
`tests/fixtures/roofing_materials_catalog_sample.xml`).

Продуктовое решение (закрывает CLAUDE.md, «Известные пробелы», пункт про
AbstractResource): подставлять автоматически нельзя (сверено с ГРАНД-Сметой
и Турбо-сметчиком — обе явно требуют ручного выбора эксперта для таких
ресурсов), но эксперту можно подсказать список из нескольких кандидатов по
совпадению названия, а не заставлять искать вручную по всему каталогу.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smeta_estimator.fsnb_parser import parse_gesn_xml, parse_material_catalog_xml
from smeta_estimator.material_candidates import suggest_material_candidates

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _find_abstract_usage(work_code: str, category_name: str):
    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    work_item = next(item for item in catalog if item.code == work_code)
    return next(
        usage
        for usage in work_item.resources
        if usage.is_abstract and usage.resource_name == category_name
    )


def test_suggests_real_candidates_for_lower_layer_roofing_category():
    # "12-01-001-02" — та же позиция ГЭСН, что и в test_end_to_end_pipeline.py
    # ("на битумной мастике с защитным слоем из гравия"), с реальной
    # AbstractResource "Материалы рулонные кровельные для нижних слоев".
    usage = _find_abstract_usage("12-01-001-02", "Материалы рулонные кровельные для нижних слоев")
    assert usage.resource_code == "12.1.02.15"

    catalog = parse_material_catalog_xml((FIXTURES / "roofing_materials_catalog_sample.xml").read_bytes())
    result = suggest_material_candidates(usage.resource_code, usage.resource_name, catalog)

    assert result.category_code == "12.1.02.15"
    assert result.category_name == "Материалы рулонные кровельные для нижних слоев"

    # 5 из 6 записей фикстуры — рулонные кровельные материалы; шестая
    # ("Пропан-бутан смесь техническая") не должна попасть в выдачу вообще,
    # не как кандидат с низким скором, а как полностью отфильтрованная.
    codes = [c.code for c in result.candidates]
    assert "01.3.02.09-0022" not in codes
    assert len(result.candidates) == 5

    # Отсортированы по цене, по возрастанию — виден весь диапазон, не
    # единственный "самый дешёвый" вариант.
    prices = [c.price for c in result.candidates]
    assert prices == sorted(prices)
    assert prices[0] == 383.58
    assert prices[-1] == 509.53

    assert result.price_range == (383.58, 509.53)


def test_limits_to_top_n_candidates():
    usage = _find_abstract_usage("12-01-001-02", "Материалы рулонные кровельные для нижних слоев")
    catalog = parse_material_catalog_xml((FIXTURES / "roofing_materials_catalog_sample.xml").read_bytes())

    result = suggest_material_candidates(usage.resource_code, usage.resource_name, catalog, top_n=3)

    assert len(result.candidates) == 3
    prices = [c.price for c in result.candidates]
    assert prices == sorted(prices)


def test_no_candidates_is_honest_empty_list_not_a_crash():
    catalog = parse_material_catalog_xml((FIXTURES / "roofing_materials_catalog_sample.xml").read_bytes())

    result = suggest_material_candidates("99.99.99.99", "Оборудование холодильное промышленное", catalog)

    assert result.candidates == []
    assert result.price_range is None


def test_matches_across_singular_plural_word_forms():
    """"Материалы"/"рулонные"/"кровельные" (категория, множественное число) и
    "Материал"/"рулонный"/"кровельный" (конкретный продукт, единственное) —
    без грубого стемминга (см. `_stems`, сравнение по первым 5 символам)
    ни один продукт не нашёлся бы вообще."""
    catalog = parse_material_catalog_xml((FIXTURES / "roofing_materials_catalog_sample.xml").read_bytes())

    result = suggest_material_candidates(
        "12.1.02.15", "Материалы рулонные кровельные для верхнего слоя", catalog
    )

    assert len(result.candidates) == 5

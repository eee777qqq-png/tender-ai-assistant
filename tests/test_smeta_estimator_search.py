"""Поиск кандидатов — на примере работ из уже существующей тестовой закупки
проекта (`0173200001426000101`, «капитальный ремонт кровли здания школы №5»,
см. `document_analyst.sample_documents.SAMPLE_DOCUMENT_KROVLYA`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smeta_estimator.fsnb_parser import apply_prices, parse_fsbc_machines_xml, parse_fsbc_materials_xml, parse_gesn_xml
from smeta_estimator.search import search_candidates

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_roof_catalog():
    items = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(items, prices)
    return items


def test_search_finds_roofing_candidates_for_a_work_item_from_the_krovlya_tender():
    """Закупка 0173200001426000101 — «капитальный ремонт кровли здания школы
    №5». В реальном пайплайне конкретную работу («устройство кровли из
    рулонных материалов на битумной мастике») сметчик берёт из ведомости
    объёмов работ — Агент 4 её пока не строит (это отдельная задача, не
    решается этим поиском), здесь просто текст, который такая работа могла
    бы содержать."""
    catalog = load_roof_catalog()

    candidates = search_candidates(catalog, "устройство кровли из рулонных материалов на битумной мастике")

    assert candidates
    assert all(c.code.startswith("12-01-001") for c in candidates)
    assert candidates[0].match_score == max(c.match_score for c in candidates)
    # Кандидаты отсортированы по убыванию релевантности.
    scores = [c.match_score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_search_ranks_more_specific_wording_higher_for_its_own_variant():
    catalog = load_roof_catalog()

    candidates = search_candidates(
        catalog, "устройство кровли на битумной мастике с защитным слоем из гравия", top_n=10
    )

    by_code = {c.code: c for c in candidates}
    # 12-01-001-02 — именно тот вариант "с защитным слоем из гравия на битумной мастике".
    assert by_code["12-01-001-02"].match_score > by_code["12-01-001-01"].match_score


def test_search_returns_empty_list_for_a_query_unrelated_to_the_catalog():
    """Каталог в этом фундаменте — только кровельные работы (Сборник 12).
    Запрос про совсем другой вид работ не должен получать случайные
    "похожие" совпадения — честная пустая выдача, не натянутый скор."""
    catalog = load_roof_catalog()

    candidates = search_candidates(catalog, "земляные работы разработка грунта экскаватором")

    assert candidates == []


def test_search_respects_top_n_limit():
    catalog = load_roof_catalog()

    candidates = search_candidates(catalog, "устройство кровель скатных рулонных материалов", top_n=2)

    assert len(candidates) <= 2

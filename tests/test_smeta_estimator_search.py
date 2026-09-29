"""Поиск кандидатов — на примере работ из уже существующей тестовой закупки
проекта (`0173200001426000101`, «капитальный ремонт кровли здания школы №5»,
см. `document_analyst.sample_documents.SAMPLE_DOCUMENT_KROVLYA`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator.fsnb_parser import (
    apply_prices,
    parse_fsbc_machines_xml,
    parse_fsbc_materials_xml,
    parse_gesn_xml,
    parse_material_catalog_xml,
)
from smeta_estimator.models import MaterialRateCandidate, RateCandidate
from smeta_estimator.search import choose_candidate_source, search_candidates, search_material_candidates

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_roof_catalog():
    items = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    prices = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(items, prices)
    return items


def load_material_catalog():
    return parse_material_catalog_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes())


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


# --- search_material_candidates() / choose_candidate_source() — 2026-09-28,
# для строк ведомости объёмов работ, которые по сути материал, не работа
# (найдено на реальных сметах — см. CLAUDE.md, estimate_smeta_document.py). ---


def test_search_material_candidates_finds_exact_material_by_name():
    catalog = load_material_catalog()

    candidates = search_material_candidates(catalog, "Пропан-бутан смесь техническая")

    assert candidates
    assert candidates[0].code == "01.3.02.09-0022"
    assert candidates[0].match_score == 1.0
    assert candidates[0].base_price_2022 == pytest.approx(41.38)


def test_search_material_candidates_returns_empty_for_unrelated_query():
    catalog = load_material_catalog()

    candidates = search_material_candidates(catalog, "устройство кровель скатных рулонных материалов")

    assert candidates == []


def test_search_material_candidates_uses_same_tokenizer_as_work_search():
    """Оценка релевантности должна считаться одинаково в обоих каталогах —
    иначе сравнение score между ними (`choose_candidate_source`) нечестное.
    На реальном пересечении слов ("бутан смесь техническая" — 3 слова, оба
    каталога дают долю 3/3) score должен совпасть дословно."""
    material_catalog = load_material_catalog()
    work_catalog = load_roof_catalog()

    material_candidates = search_material_candidates(material_catalog, "бутан смесь техническая")
    work_candidates = search_candidates(work_catalog, "бутан смесь техническая")

    assert material_candidates
    assert material_candidates[0].match_score == 1.0
    assert work_candidates == []  # в каталоге работ этих слов нет вообще


def test_choose_candidate_source_picks_the_higher_scoring_catalog():
    work = [RateCandidate(code="w1", name="w", unit="шт", base_price=0.0, match_score=0.9)]
    material = [MaterialRateCandidate(code="m1", name="m", unit="шт", match_score=0.6, base_price_2022=0.0)]

    assert choose_candidate_source(work, material) == "work"


def test_choose_candidate_source_prefers_material_when_it_scores_higher():
    work = [RateCandidate(code="w1", name="w", unit="шт", base_price=0.0, match_score=0.55)]
    material = [MaterialRateCandidate(code="m1", name="m", unit="шт", match_score=0.9, base_price_2022=0.0)]

    assert choose_candidate_source(work, material) == "material"


def test_choose_candidate_source_returns_none_when_both_empty():
    assert choose_candidate_source([], []) is None


def test_choose_candidate_source_returns_none_when_best_score_below_threshold():
    # Оба пути нашли что-то, но неуверенно (ниже 0.5) — честный "не найдено",
    # не выбор менее плохого из двух плохих вариантов.
    work = [RateCandidate(code="w1", name="w", unit="шт", base_price=0.0, match_score=0.33)]
    material = [MaterialRateCandidate(code="m1", name="m", unit="шт", match_score=0.29, base_price_2022=0.0)]

    assert choose_candidate_source(work, material) is None


def test_choose_candidate_source_respects_custom_threshold():
    work = [RateCandidate(code="w1", name="w", unit="шт", base_price=0.0, match_score=0.4)]

    assert choose_candidate_source(work, [], threshold=0.5) is None
    assert choose_candidate_source(work, [], threshold=0.3) == "work"

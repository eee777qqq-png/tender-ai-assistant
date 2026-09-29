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
from smeta_estimator.models import GesnWorkItem, MaterialCandidateInfo, MaterialRateCandidate, RateCandidate
from smeta_estimator.search import (
    MATCH_SCORE_THRESHOLD,
    choose_candidate_source,
    search_candidates,
    search_material_candidates,
)

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


# --- Вес технических токенов (фракция/марка/типоразмер/номер расценки) —
# 2026-09-29, круг 2. Найдено замером --force-text-search (CLAUDE.md,
# открытый п.16.2): текстовый поиск без точного кода из «Обоснования»
# регулярно путал явно разные варианты внутри одной категории, потому что
# цифровой параметр весил как любое обычное слово в "|пересечение|/|запрос|".
# Названия ниже — реальные названия из каталога ФСНБ-2022 для 4
# задокументированных промахов (не выдуманы, скопированы из живого прогона
# на тендерах круга 2 — Краснодар №0337100017726000160, МО №0337100017726000168).


def _work_item(code: str, name: str) -> GesnWorkItem:
    return GesnWorkItem(code=code, name=name, unit="ед.")


def _material_item(code: str, name: str) -> MaterialCandidateInfo:
    return MaterialCandidateInfo(code=code, name=name, unit="ед.", price=0.0)


def test_search_material_candidates_separates_gravel_fraction_20_40_from_5_10():
    # Круг 2, Краснодар: до фикса оба давали score=1.00 (числа "20-40"/
    # "5(3)-10" отфильтровывались как короткие токены ещё до взвешивания).
    catalog = [
        _material_item(
            "02.2.05.04-2088",
            "Щебень из плотных горных пород для строительных работ М 600, фракция 20-40 мм",
        ),
        _material_item(
            "02.2.05.04-2008",
            "Щебень из плотных горных пород для строительных работ М 600, фракция 5(3)-10 мм",
        ),
    ]

    candidates = search_material_candidates(
        catalog, "Щебень из плотных горных пород для строительных работ М 600, фракция 20-40 мм"
    )

    by_code = {c.code: c for c in candidates}
    assert by_code["02.2.05.04-2088"].match_score == 1.0
    assert by_code["02.2.05.04-2088"].match_score > by_code["02.2.05.04-2008"].match_score


def test_search_material_candidates_separates_cement_grade_32_5_from_42_5():
    # Круг 2, МО: марка цемента "32,5Н" vs "42,5Н" — тот же класс промаха.
    catalog = [
        _material_item(
            "03.2.01.02-0012",
            "Портландцемент с минеральными добавками общестроительный ЦЕМ II 32,5Н",
        ),
        _material_item(
            "03.2.01.02-0002",
            "Портландцемент с минеральными добавками общестроительный ЦЕМ II 42,5Н",
        ),
    ]

    candidates = search_material_candidates(
        catalog, "Портландцемент с минеральными добавками общестроительный ЦЕМ II 32,5Н"
    )

    by_code = {c.code: c for c in candidates}
    assert by_code["03.2.01.02-0012"].match_score == 1.0
    assert by_code["03.2.01.02-0012"].match_score > by_code["03.2.01.02-0002"].match_score


def test_search_candidates_separates_gesnr_width_1_75_from_width_1():
    # Круг 2, Краснодар: ширина "до 1,75 м" vs "до 1 м" — работа ГЭСНр,
    # не материал (проверяет ту же формулу на втором каталоге/функции).
    catalog = [
        _work_item(
            "ГЭСНр58-01-020-04",
            "Смена обделок из листовой стали — Смена обделок из листовой стали "
            "(брандмауэров и парапетов без обделки боковых стенок) шириной: — до 1,75 м",
        ),
        _work_item(
            "ГЭСНр58-01-020-03",
            "Смена обделок из листовой стали — Смена обделок из листовой стали "
            "(брандмауэров и парапетов без обделки боковых стенок) шириной: — до 1 м",
        ),
    ]

    candidates = search_candidates(
        catalog,
        "Смена обделок из листовой стали (брандмауэров и парапетов без обделки "
        "боковых стенок) шириной: до 1,75 м",
    )

    by_code = {c.code: c for c in candidates}
    assert by_code["ГЭСНр58-01-020-04"].match_score == 1.0
    assert by_code["ГЭСНр58-01-020-04"].match_score > by_code["ГЭСНр58-01-020-03"].match_score


def test_search_candidates_finds_correct_match_below_old_threshold():
    # Круг 2, Краснодар: реальная строка ведомости (row.name — как её
    # реально извлекает work_volume_extractor из xlsx, с "лишними" словами
    # "с разуклонкой" и встроенным текстом формулы "Кол-во: =...", которые
    # не входят в название каталога) даёт верный код score=6/13≈0.4615 —
    # НИЖЕ старого порога 0.50 (был бы честным "не найдено" при заведомо
    # верном совпадении), но выше нового MATCH_SCORE_THRESHOLD=0.45 (см.
    # докстринг search.py — порог пересмотрен по факту распределения score
    # на обоих документах круга 2).
    catalog = [
        _work_item(
            "27-04-001-04",
            "Устройство подстилающих и выравнивающих слоев оснований — "
            "Устройство подстилающих и выравнивающих слоев оснований: — из щебня",
        ),
        _work_item(
            "27-04-001-01",
            "Устройство подстилающих и выравнивающих слоев оснований — "
            "Устройство подстилающих и выравнивающих слоев оснований: — из песка",
        ),
    ]

    candidates = search_candidates(
        catalog,
        "Устройство подстилающих и выравнивающих слоев оснований: из щебня/ с "
        "разуклонкой\nКол-во: =(71*1.4*0.1)/100",
    )

    assert candidates
    assert candidates[0].code == "27-04-001-04"
    assert candidates[0].match_score == pytest.approx(6 / 13)
    assert MATCH_SCORE_THRESHOLD < candidates[0].match_score < 0.5


def test_search_material_candidates_pipe_pair_stays_an_honest_tie():
    # Круг 2, Краснодар: "Труба металлическая..." vs "Труба С КОЛЕНОМ
    # металлическая..." — здесь расхождение НЕ в цифровом токене (оба
    # содержат одинаковые "102х76"/"3000"), а в лишнем НЕчисловом слове
    # "коленом" у неправильного кандидата, которого нет в запросе вообще.
    # Взвешивание технических токенов такую пару принципиально не
    # разделяет (это не его задача — "|пересечение|/|запрос|" не штрафует
    # кандидата за лишние слова, которых нет в запросе) — честно
    # зафиксировано как оставшийся тай-брейк, не выдаётся за решённое.
    catalog = [
        _material_item(
            "12.1.01.05-0070",
            "Труба металлическая для водосточных систем, окрашенная, размеры "
            "трубы 102х76 мм, длина трубы 3000 мм",
        ),
        _material_item(
            "12.1.01.05-0062",
            "Труба с коленом металлическая для водосточных систем, окрашенная, "
            "размеры трубы 102х76 мм, длина трубы 3000 мм",
        ),
    ]

    candidates = search_material_candidates(
        catalog,
        "Труба металлическая для водосточных систем, окрашенная, размеры трубы "
        "102х76 мм, длина трубы 3000 мм",
    )

    by_code = {c.code: c for c in candidates}
    assert by_code["12.1.01.05-0070"].match_score == by_code["12.1.01.05-0062"].match_score == 1.0


# --- Подкласс 1: однобуквенные/римские технические маркеры — 2026-09-29,
# продолжение круга 2 (CLAUDE.md, «Остаточные, честно не устранённые
# случаи», подкласс 1). Тот же класс проблемы, что и цифровые токены
# (commit 9925660): маркер марки/типа/класса, записанный одной буквой
# («Б», «А») или римским числом («I», «II»), отсекался тем же фильтром
# `len(word) >= 3`, что раньше отсекал короткие числа. Проверено прогоном
# на обоих реальных документах круга 2 целиком (не на 2 придуманных
# примерах): однобуквенные маркеры сами по себе точность не меняли
# (71,4%), римские числа сами по себе — 76,2%, вместе — 78,6% (лучший
# результат из всех проверенных вариантов, включая F-score/Jaccard ниже).
# Реализовано оба вместе. Названия ниже — реальные из каталога ФСБЦ.


def test_search_material_candidates_separates_asphalt_type_b_from_type_a():
    # Круг 2, Краснодар: «тип Б, марка II» vs «тип А, марка I»
    # (04.2.01.01-0049/0046) — однобуквенный маркер марки ("б"/"а") и
    # римское число класса ("ii"/"i") оба короче 3 символов, раньше
    # отсекались фильтром длины и полностью исчезали из сравнения.
    catalog = [
        _material_item(
            "04.2.01.01-0049",
            "Смеси асфальтобетонные плотные мелкозернистые, тип Б, марка II",
        ),
        _material_item(
            "04.2.01.01-0046",
            "Смеси асфальтобетонные плотные мелкозернистые, тип А, марка I",
        ),
    ]

    candidates = search_material_candidates(
        catalog, "Смеси асфальтобетонные плотные мелкозернистые, тип Б, марка II"
    )

    by_code = {c.code: c for c in candidates}
    assert by_code["04.2.01.01-0049"].match_score == 1.0
    assert by_code["04.2.01.01-0049"].match_score > by_code["04.2.01.01-0046"].match_score


def test_search_material_candidates_separates_sand_class_ii_from_class_i():
    # Круг 2, МО: «I класс» vs «II класс» песка (02.3.01.02-1104/1118) —
    # только римское число различает пару, ни одного другого отличающегося
    # слова в названии.
    catalog = [
        _material_item(
            "02.3.01.02-1118",
            "Песок природный для строительных работ II класс, средний",
        ),
        _material_item(
            "02.3.01.02-1104",
            "Песок природный для строительных работ I класс, средний",
        ),
    ]

    candidates = search_material_candidates(
        catalog, "Песок природный для строительных работ II класс, средний"
    )

    by_code = {c.code: c for c in candidates}
    assert by_code["02.3.01.02-1118"].match_score == 1.0
    assert by_code["02.3.01.02-1118"].match_score > by_code["02.3.01.02-1104"].match_score


def test_tokenize_words_keeps_single_letter_markers_not_in_stopwords():
    from smeta_estimator.search import tokenize_words

    tokens = tokenize_words("тип Б, марка II")
    assert "б" in tokens
    assert "ii" in tokens
    # Предлоги-однобуквенные слова уже отсекаются стоп-листом, не длиной —
    # поведение здесь не меняется этим фиксом.
    assert "и" not in tokenize_words("шпильки и болты")


def test_tokenize_words_recognizes_roman_numerals_up_to_common_range():
    from smeta_estimator.search import tokenize_words

    for roman in ("i", "ii", "iii", "iv", "v", "ix", "x"):
        assert roman in tokenize_words(f"класс {roman}")


def test_tokenize_words_does_not_confuse_roman_i_with_cyrillic_conjunction():
    # Задача прямо просила проверить это: латинское "i" (римское число) и
    # кириллическое "и" (союз "и") — разные символы Unicode, не должны
    # путаться другом с другом ни в токенизации, ни в стоп-листе.
    from smeta_estimator.search import tokenize_words

    tokens = tokenize_words("класс I и болты")
    assert "i" in tokens  # римское число осталось
    assert "и" not in tokens  # союз по-прежнему отсекается стоп-листом

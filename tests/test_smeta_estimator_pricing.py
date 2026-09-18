import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from smeta_estimator.models import RateCandidate
from smeta_estimator.pricing import price_candidate, price_candidates


def make_candidate(base_price: float = 1000.0) -> RateCandidate:
    return RateCandidate(code="12-01-001-02", name="тест", unit="100 м2", base_price=base_price, match_score=1.0)


def test_price_candidate_multiplies_base_price_by_the_regional_index():
    candidate = make_candidate(base_price=1000.0)

    priced = price_candidate(candidate, index_value=14.94, index_as_of=date(2026, 8, 28), region_name="г. Москва")

    assert priced.regional_price == pytest.approx(14940.0)
    assert priced.region_name == "г. Москва"
    assert priced.index_value == pytest.approx(14.94)
    assert priced.index_as_of == date(2026, 8, 28)
    # Исходный кандидат не мутирован — price_candidate возвращает новый объект.
    assert candidate.regional_price is None


def test_price_candidates_prices_every_item_in_the_list():
    candidates = [make_candidate(1000.0), make_candidate(2000.0)]

    priced = price_candidates(candidates, index_value=2.0, index_as_of=date(2026, 8, 28), region_name="Ростовская область")

    assert [c.regional_price for c in priced] == [2000.0, 4000.0]
    assert all(c.region_name == "Ростовская область" for c in priced)

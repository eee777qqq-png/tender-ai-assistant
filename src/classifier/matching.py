"""Сопоставление профиля клиента (Агент 11) с конкретной закупкой (Агент 2).

**Два явных прохода классификации, не один — архитектура закрывает CLAUDE.md,
п.11, реализовано 2026-09-27.** До этой правки `match_profile_to_tender()`
(теперь `coarse_classify()`) запускался один раз, на данных `Tender` из
извещения — `requires_sro`/`min_experience_years` там всегда честная
заглушка `False`/`0` (см. CLAUDE.md, открытый п.9), поэтому вердикт
«ПОДХОДИТ» первого прохода мог быть ложным: на реальном тендере (капремонт
ЖКХ, №0373200032226000750, 2026-09-26) он был ложным на практике — Агент 3,
разобрав документацию, нашёл требование к опыту, которого профиль клиента
не подтверждал, а формальный вердикт `is_match` не менялся вообще. Тогда это
закрыли точечно (предупреждение о противоречии в `client_consultant`, см.
там `_find_unresolved_participant_requirements` — оставлено как
дополнительная страховка, не единственный механизм) — сама классификация
оставалась однопроходной.

- `coarse_classify(profile, tender, classifier, profitability=None)` — то,
  что раньше было единственным проходом: сектор (ОКПД2), регион, СРО/опыт
  по (пока всегда пустым) полям `Tender`, мощности, финансовая готовность.
  Работает ДО скачивания документации закупки — дешёвый фильтр над потоком
  закупок Агента 1, экономит сеть/время на явно нерелевантных закупках.
  Критерий финансовой готовности — единственный из шести, у которого два
  режима, потому что Агент 2 в конвейере вызывается ДО Агента 4/5
  (себестоимость и маржа по конкретной закупке считаются намного позже —
  после сборки пакета документов и выбора экспертом позиций сметы, см.
  CLAUDE.md, «Известные пробелы»). Гонять полную смету и экспертную
  проверку по каждой закупке, прежде чем узнать, стоит ли вообще ей
  заниматься, — не то, для чего нужен быстрый фильтр Агента 2. Поэтому:
    - без `profitability` (обычный путь — быстрая фильтрация потока закупок
      из Агента 1, до того как Агент 4/5 вообще запускались) — используется
      грубая эвристика `avg_annual_revenue >= max_price`, явно помеченная в
      сообщении как предварительная, не окончательная;
    - с `profitability` (повторная, уточняющая проверка уже после того, как
      Агент 5 посчитал реальную маржу по этой паре клиент+закупка) —
      критерий заменяется на `margin > 0`, настоящую посчитанную выгоду.

- `final_classify(profile, tender, classifier, extracted_requirements,
  profitability=None)` — вызывается ПОСЛЕ того, как Агент 3 скачал и
  разобрал документацию закупки (`eis_client.attachment_analyst.
  fetch_participant_requirements()`). Пересчитывает те же критерии, что и
  `coarse_classify()` (сектор/регион/мощности/финансы не зависят от
  документации — пересчёт дешёвый, без сети), и добавляет новый критерий
  `participant_requirements_from_documents`, сверяющий реально найденные
  Агентом 3 требования (`kind` — "sro"/"experience"/"unclear") с профилем.
  Если находка распознана уверенно (regex/структурированное поле ЕАИСТ,
  `kind="sro"`/`"experience"`) и профиль её не подтверждает — критерий
  проваливается с вердиктом «не подходит»: категория известна точно, не
  предположение. Если находка — только `unclear` (защитная сетка Агента 3,
  сама по себе неуверенная догадка, не разобранное регуляркой требование) —
  критерий тоже проваливается, но с вердиктом «требует ручной проверки», не
  «не подходит»: у нас нет уверенности, что там действительно есть
  требование, только сигнал «похоже на требование, гляньте текст». В обоих
  случаях `is_match` становится `False` — не молчаливое «ПОДХОДИТ» первого
  прохода, которое не отражало бы то, что Агент 3 в итоге нашёл.

  **Осознанно НЕ требует `extracted_requirements.expert_reviewed=True`** —
  в отличие от Агента 6 (`assemble_document_package`) и Агента 5
  (`estimate_profitability`), которые именно эту проверку и гейтуют. Смысл
  финального прохода — поймать противоречие АВТОМАТИЧЕСКИ, сразу после
  Агента 3, до того как к закупке подключается человек-эксперт (тот всё
  равно проверяет находки Агента 3 позже, перед тем как их использует
  Агент 6/5) — требовать экспертной проверки здесь означало бы, что
  `final_classify()` мог бы запуститься только после ручного шага, что и
  обесценивает смысл автоматического второго прохода. Только `Агенты 3, 4,
  6 не уходят клиенту без подтверждения эксперта» (протокол CLAUDE.md)
  касается выдачи, доходящей до клиента — сам вердикт матчинга (подходит/не
  подходит/требует проверки) в клиентскую сводку по-прежнему попадает через
  Агента 8, у которого текст сводки такой находки прямо просит ручной
  проверки, а не выдаёт её как готовый факт.

Оркестратор (`match_real_notices.py` и любой будущий) должен вызывать
`coarse_classify()` → если прошёл, скачать/разобрать документацию через
Агента 3 → `final_classify()` → и только его результат передавать в Агент 8,
не результат `coarse_classify()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from document_analyst.models import ExtractedRequirements
from onboarding.models import ClientProfile

from .okpd2 import ConstructionClassifier
from .tender import Tender

if TYPE_CHECKING:
    # Только для аннотаций типов (`from __future__ import annotations` выше
    # не вычисляет их в рантайме) — иначе реальный импорт создал бы цикл
    # classifier -> profitability_estimator -> classifier.tender.
    from profitability_estimator.models import ProfitabilityEstimate


@dataclass
class MatchCriterion:
    name: str
    passed: bool
    message: str


@dataclass
class MatchResult:
    tender: Tender
    client_id: str
    criteria: list[MatchCriterion] = field(default_factory=list)

    @property
    def is_match(self) -> bool:
        return all(c.passed for c in self.criteria)

    @property
    def score(self) -> float:
        if not self.criteria:
            return 0.0
        return sum(1 for c in self.criteria if c.passed) / len(self.criteria)

    @property
    def failed_reasons(self) -> list[str]:
        return [c.message for c in self.criteria if not c.passed]


def coarse_classify(
    profile: ClientProfile,
    tender: Tender,
    classifier: ConstructionClassifier,
    profitability: "ProfitabilityEstimate | None" = None,
) -> MatchResult:
    """Первый, дешёвый проход — до скачивания документации закупки. См.
    докстринг модуля про то, зачем нужен второй проход (`final_classify`)."""
    criteria: list[MatchCriterion] = []

    is_construction = classifier.is_construction_code(tender.okpd2_code)
    criteria.append(
        MatchCriterion(
            "construction_sector",
            is_construction,
            "Закупка относится к разделу «Строительство»"
            if is_construction
            else f"ОКПД2 {tender.okpd2_code} не относится к разделу «Строительство»",
        )
    )

    region_ok = tender.region_code in profile.region_codes
    criteria.append(
        MatchCriterion(
            "region",
            region_ok,
            "Регион закупки входит в регионы работы клиента"
            if region_ok
            else f"Клиент работает в регионах {profile.region_codes or '(не указаны)'}, "
            f"закупка в регионе {tender.region_code}",
        )
    )

    sro_ok = not tender.requires_sro or profile.permits_experience.sro_membership
    criteria.append(
        MatchCriterion(
            "sro_membership",
            sro_ok,
            "Требование по членству в СРО выполнено"
            if sro_ok
            else "Закупка требует членства в СРО, у клиента его нет",
        )
    )

    experience_ok = profile.permits_experience.years_of_experience >= tender.min_experience_years
    criteria.append(
        MatchCriterion(
            "experience",
            experience_ok,
            "Опыта достаточно"
            if experience_ok
            else f"Нужно {tender.min_experience_years} лет опыта, у клиента "
            f"{profile.permits_experience.years_of_experience}",
        )
    )

    capacity_ok = profile.capacity.staff_count > 0
    criteria.append(
        MatchCriterion(
            "capacity",
            capacity_ok,
            "Производственные мощности заявлены" if capacity_ok else "Не указана численность персонала",
        )
    )

    criteria.append(_financial_capacity_criterion(profile, tender, profitability))

    return MatchResult(tender=tender, client_id=profile.client_id, criteria=criteria)


def _financial_capacity_criterion(
    profile: ClientProfile, tender: Tender, profitability: "ProfitabilityEstimate | None"
) -> MatchCriterion:
    if profitability is None:
        financial_ok = profile.financial.avg_annual_revenue >= tender.max_price
        return MatchCriterion(
            "financial_capacity",
            financial_ok,
            "Финансовой готовности достаточно (предварительная оценка по выручке — "
            "Агент 5 ещё не считал реальную маржу по этой закупке)"
            if financial_ok
            else f"НМЦК {tender.max_price:,.0f} ₽ превышает среднегодовую выручку клиента "
            f"{profile.financial.avg_annual_revenue:,.0f} ₽ (предварительная оценка по "
            "выручке — Агент 5 ещё не считал реальную маржу по этой закупке)",
        )

    if profitability.tender_purchase_number != tender.purchase_number:
        raise ValueError(
            "Оценка выгоды Агента 5 относится к закупке "
            f"{profitability.tender_purchase_number!r}, а матчинг считается для закупки "
            f"{tender.purchase_number!r}"
        )
    if profitability.client_id != profile.client_id:
        raise ValueError(
            f"Оценка выгоды Агента 5 относится к клиенту {profitability.client_id!r}, а "
            f"матчинг считается для клиента {profile.client_id!r}"
        )

    if profitability.margin is None:
        return MatchCriterion(
            "financial_capacity",
            False,
            "Маржа не рассчитана Агентом 5 (налоговый режим клиента требует уточнения с "
            "бухгалтером) — финансовая готовность не подтверждена",
        )

    margin_ok = profitability.margin > 0
    return MatchCriterion(
        "financial_capacity",
        margin_ok,
        f"Закупка выгодна: маржа Агента 5 положительна ({profitability.margin:,.0f} ₽)"
        if margin_ok
        else f"Закупка невыгодна: маржа Агента 5 не положительна ({profitability.margin:,.0f} ₽)",
    )


_UNCLEAR_ONLY_MESSAGE = (
    "Агент 3 нашёл в документации формулировку, похожую на требование к участнику "
    "(защитная сетка «unclear» — сама по себе не уверенное распознавание), которую "
    "профиль клиента пока не подтверждает: {description} Цитата: «{quote}» — "
    "ТРЕБУЕТ РУЧНОЙ ПРОВЕРКИ, не отклонено и не подтверждено автоматически."
)
_CONFIRMED_UNMET_MESSAGE = (
    "Агент 3 нашёл в документации требование к участнику ({kind}), которое профиль "
    "клиента не подтверждает: {description} Цитата: «{quote}» — НЕ ПОДХОДИТ по факту "
    "документации, хотя грубый фильтр по извещению это пропустил."
)


def _participant_requirements_criterion(
    profile: ClientProfile, extracted: ExtractedRequirements
) -> MatchCriterion:
    """Второй проход (`final_classify`) — сверяет реально найденные Агентом 3
    требования к участнику с профилем. `kind="sro"`/`"experience"` — регулярка
    или структурированное поле ЕАИСТ уверенно распознали категорию, профиль её
    не подтверждает -> точно не подходит. `kind="unclear"` — защитная сетка
    нашла похожую на требование формулировку, но не уверена, что это точно
    требование -> не молчаливое «подходит», но и не уверенное «не подходит»,
    а «требует проверки» (оба случая — `passed=False`, различаются только
    текстом сообщения, см. докстринг модуля)."""
    has_completed_contract = bool(profile.permits_experience.completed_contracts)
    has_sro = profile.permits_experience.sro_membership

    unmet_confirmed: list[str] = []
    unmet_unclear: list[str] = []
    for req in extracted.participant_requirements:
        if req.kind == "sro":
            if not has_sro:
                unmet_confirmed.append(
                    _CONFIRMED_UNMET_MESSAGE.format(kind="СРО", description=req.description, quote=req.raw_text[:200])
                )
        elif req.kind == "experience":
            if not has_completed_contract:
                unmet_confirmed.append(
                    _CONFIRMED_UNMET_MESSAGE.format(
                        kind="опыт", description=req.description, quote=req.raw_text[:200]
                    )
                )
        elif req.kind == "unclear":
            if not has_completed_contract:
                unmet_unclear.append(
                    _UNCLEAR_ONLY_MESSAGE.format(description=req.description, quote=req.raw_text[:200])
                )

    if not unmet_confirmed and not unmet_unclear:
        return MatchCriterion(
            "participant_requirements_from_documents",
            True,
            "Найденные Агентом 3 в документации требования к участнику (если были) "
            "подтверждены профилем клиента",
        )

    message = " ".join(unmet_confirmed + unmet_unclear)
    return MatchCriterion("participant_requirements_from_documents", False, message)


def final_classify(
    profile: ClientProfile,
    tender: Tender,
    classifier: ConstructionClassifier,
    extracted_requirements: ExtractedRequirements,
    profitability: "ProfitabilityEstimate | None" = None,
) -> MatchResult:
    """Второй, окончательный проход — ПОСЛЕ того, как Агент 3 скачал и
    разобрал документацию закупки. См. докстринг модуля — заменяет исход
    `coarse_classify()` окончательным вердиктом, а не просит доверять
    первому проходу и находкам Агента 3 одновременно."""
    if extracted_requirements.tender_purchase_number != tender.purchase_number:
        raise ValueError(
            "Требования Агента 3 относятся к другой закупке: "
            f"агент 3={extracted_requirements.tender_purchase_number!r}, "
            f"закупка={tender.purchase_number!r}"
        )

    result = coarse_classify(profile, tender, classifier, profitability=profitability)
    result.criteria.append(_participant_requirements_criterion(profile, extracted_requirements))
    return result


def find_matching_tenders(
    profile: ClientProfile, tenders: list[Tender], classifier: ConstructionClassifier
) -> list[MatchResult]:
    """Прогоняет все закупки через `coarse_classify` и сортирует по score
    (лучшие совпадения — первые) — быстрый фильтр над потоком закупок,
    без документации закупки (см. докстринг модуля про `final_classify`)."""
    results = [coarse_classify(profile, tender, classifier) for tender in tenders]
    return sorted(results, key=lambda r: r.score, reverse=True)

"""Сборка клиентской сводки (Агент 8).

`build_client_summary()` — единственная точка входа. `render_summary_text()`
рядом — готовый текстовый блок для показа собственнику (без кодов статусов,
без структур данных), на случай если сводка выводится как есть, а не
рендерится отдельным UI.

`profitability` (результат Агента 5, `ProfitabilityEstimate`) — необязательный
четвёртый вход, подключён 2026-09-21: раньше маржа и риски выгоды считались,
но никуда не попадали дальше — до собственника в сводке они вообще не
доходили, хотя показать их клиенту и есть весь смысл Агента 5. Необязательный,
а не обязательный — по тому же принципу, что `extracted_requirements` у
Агента 6: вызывающий код может собрать сводку без оценки выгоды (например,
если Агент 4 для этой закупки ещё не прогонялся), но если она есть — сверяется
на закупку/клиента и попадает в сводку как отдельный пункт «Ожидаемая выгода».

Важно: это каркас. Переводы категорий скрытых рисков в «что это значит на
практике» и подсказки «что сделать» для недостающих полей — фиксированные
шаблоны на 5 категорий риска / известные сейчас поля пакета, а не
интеллектуальный пересказ произвольного текста — см. CLAUDE.md, раздел
«Известные пробелы». Частичное улучшение 2026-09-25: когда Агент 3 извлёк
конкретное число находки (`HiddenRisk.rate_pct` — сейчас только ставка
штрафа/пени в день для NONSTANDARD_PENALTY), `_practical_meaning()`
подставляет его в текст вместо общей формулировки категории — это не
LLM-пересказ, а точечная подстановка уже структурированного значения.

`render_summary_text()` безусловно дописывает в конец сводки стандартное
юридическое предупреждение о границах ответственности сервиса (Агент 12,
`legal_boundaries.CLIENT_SUMMARY_LEGAL_NOTICE`) — оно вшито в саму функцию,
а не передаётся отдельным параметром, чтобы его нельзя было забыть
подключить.
"""

from __future__ import annotations

from classifier.matching import MatchResult
from completeness_check.models import CompletenessResult
from document_analyst.models import HiddenRisk, RiskCategory
from document_assembler.models import DocumentPackage
from legal_boundaries import CLIENT_SUMMARY_LEGAL_NOTICE
from profitability_estimator.models import ProfitabilityEstimate

from .models import ClientSummary, MissingDocumentItem, PlainRisk, ProfitabilitySummary

DECISION_REMINDER = (
    "Это справка для ознакомления, а не рекомендация участвовать или отказаться. "
    "Решение об участии в закупке всегда принимает собственник бизнеса — "
    "консультант только объясняет, что нашли другие агенты, и сам ничего "
    "не советует и не решает."
)

# Что сделать, чтобы получить/заполнить конкретное недостающее поле пакета.
# Покрывает поля, которые сейчас реально строит Агент 6 (`document_assembler`).
# Новое поле, которого здесь нет, попадёт под generic-подсказку ниже — см.
# «Известные пробелы» в CLAUDE.md.
_ACTION_HINTS: dict[str, str] = {
    "Наименование организации": "Проверьте, что в профиле указано полное юридическое наименование компании.",
    "ИНН": "Добавьте ИНН компании в профиль.",
    "ОГРН/ОГРНИП": "Добавьте ОГРН (или ОГРНИП для ИП) в профиль.",
    "Юридический адрес": "Укажите юридический адрес компании в профиле.",
    "Контактное лицо": "Укажите контактное лицо (ФИО ответственного) в профиле.",
    "Телефон": "Добавьте контактный телефон в профиль.",
    "Email": "Добавьте контактный email в профиль.",
    "Членство в СРО (номер)": "Оформите членство в СРО (если его ещё нет) и укажите номер в профиле — для этой закупки это обязательно.",
    "Опыт выполнения аналогичных работ (лет)": "Укажите в профиле опыт выполнения аналогичных работ, в годах — для этой закупки это обязательное условие.",
    "Справки об исполненных контрактах": "Добавьте в профиль справки о ранее исполненных аналогичных контрактах.",
    "Численность персонала": "Укажите численность персонала компании в профиле.",
    "Описание собственных трудовых ресурсов": "Добавьте в профиль короткое описание собственных трудовых ресурсов компании.",
    "Система налогообложения": (
        "Выберите в профиле подходящий вариант режима налогообложения и ставки НДС "
        "(например, «УСН 6% без НДС» или «ОСН + НДС 22%») — если не уверены, выберите "
        "«Другое / требует уточнения с бухгалтером»."
    ),
    "Среднегодовая выручка": "Добавьте в профиль данные о среднегодовой выручке компании.",
    "Обеспечение заявки (банковская гарантия)": "Оформите банковскую гарантию (или другое обеспечение) для заявки и отметьте это в профиле.",
    "Обеспечение исполнения контракта (банковская гарантия)": "Оформите банковскую гарантию (или другое обеспечение) для исполнения контракта — по документации закупки это обязательно.",
}


def _action_hint(field_name: str) -> str:
    return _ACTION_HINTS.get(
        field_name, f"Заполните поле «{field_name}» в профиле компании или уточните его у эксперта."
    )


# Что найденный тип скрытого риска значит на практике для собственника —
# общая суть категории без цифр. Используется как есть, когда для конкретной
# находки нет извлечённого числа (HiddenRisk.rate_pct is None), и как fallback
# для NONSTANDARD_PENALTY, если оно почему-то не заполнено. Когда число есть —
# `_practical_meaning()` ниже строит текст с ним вместо этого шаблона.
_RISK_PRACTICAL_MEANING: dict[RiskCategory, str] = {
    RiskCategory.NONSTANDARD_PENALTY: (
        "Штраф за просрочку выше обычного. Если работы будут сданы позже срока, "
        "неустойка окажется больше, чем в типовом контракте — стоит заранее "
        "оценить, реально ли уложиться в сроки."
    ),
    RiskCategory.UNCAPPED_LIABILITY: (
        "Неустойка ничем не ограничена сверху. Чем дольше просрочка — тем "
        "больше сумма штрафа, потолка нет. Затягивание сроков может обойтись "
        "очень дорого."
    ),
    RiskCategory.UNILATERAL_TERMS: (
        "Заказчик может менять условия в одностороннем порядке. По ходу "
        "исполнения контракта заказчик вправе скорректировать условия без "
        "согласия компании — стоит учитывать этот риск при планировании."
    ),
    RiskCategory.AMBIGUOUS_CONDITION: (
        "Формулировка условия нечёткая. Не до конца понятно, что именно "
        "потребуется — заказчик может трактовать объём или условия по своему "
        "усмотрению. Стоит уточнить это до подачи заявки."
    ),
    RiskCategory.OTHER: (
        "Найдена нестандартная формулировка, которая не попадает в типовые "
        "категории риска. Рекомендуем внимательно прочитать этот пункт "
        "документации самостоятельно или с юристом."
    ),
}


def _practical_meaning(risk: HiddenRisk) -> str:
    """Как и `_RISK_PRACTICAL_MEANING`, но подставляет в текст конкретную
    извлечённую цифру находки, если она есть (`HiddenRisk.rate_pct`), вместо
    общей формулировки категории. Сейчас числа извлекаются только для
    NONSTANDARD_PENALTY (ставка штрафа/пени в день, см. extractor.py) — для
    остальных категорий `rate_pct` всегда None, и работает обычный шаблон
    по категории. Это не интеллектуальный пересказ произвольного текста, а
    точечная подстановка уже структурированного числа — см. CLAUDE.md,
    «Известные пробелы», про полноценный LLM-пересказ."""
    if risk.category == RiskCategory.NONSTANDARD_PENALTY and risk.rate_pct is not None:
        rate = f"{risk.rate_pct:g}"
        return (
            f"Штраф за просрочку — {rate}% цены контракта за каждый день. Это заметно "
            "выше типовой практики (доли процента в день), и сумма растёт с каждым днём "
            "задержки — стоит заранее оценить, реально ли уложиться в срок."
        )
    return _RISK_PRACTICAL_MEANING[risk.category]


def _translate_risk(risk: HiddenRisk) -> PlainRisk:
    return PlainRisk(
        what_it_says=risk.explanation,
        why_it_matters=_practical_meaning(risk),
        quote=risk.excerpt,
    )


def _translate_profitability(profitability: ProfitabilityEstimate) -> ProfitabilitySummary:
    if profitability.margin is not None:
        formatted = f"{profitability.margin:,.0f}".replace(",", " ")
        margin_explanation = (
            f"Ожидаемая маржа по этой закупке — примерно {formatted} руб. "
            "(НМЦК минус оценочная себестоимость, стоимость обеспечения и налоги; "
            "показательная оценка, не окончательная цифра — см. пометки ниже)."
        )
    else:
        margin_explanation = (
            "Маржу по этой закупке пока не удалось посчитать — см. пометки ниже, почему."
        )
    return ProfitabilitySummary(
        margin=profitability.margin,
        margin_explanation=margin_explanation,
        risk_flags=list(profitability.risk_flags),
        win_probability_note=profitability.win_probability_note,
    )


def build_client_summary(
    match: MatchResult,
    completeness: CompletenessResult,
    package: DocumentPackage,
    profitability: ProfitabilityEstimate | None = None,
) -> ClientSummary:
    if not (
        match.tender.purchase_number == completeness.tender_purchase_number == package.tender_purchase_number
    ):
        raise ValueError(
            "Результаты агентов относятся к разным закупкам: "
            f"матчинг={match.tender.purchase_number!r}, "
            f"комплектность={completeness.tender_purchase_number!r}, "
            f"пакет={package.tender_purchase_number!r}"
        )
    if match.client_id != package.client_id:
        raise ValueError(
            f"Результаты агентов относятся к разным клиентам: "
            f"матчинг={match.client_id!r}, пакет={package.client_id!r}"
        )
    if profitability is not None:
        if profitability.tender_purchase_number != match.tender.purchase_number:
            raise ValueError(
                "Оценка выгоды Агента 5 относится к другой закупке: "
                f"выгода={profitability.tender_purchase_number!r}, "
                f"матчинг={match.tender.purchase_number!r}"
            )
        if profitability.client_id != match.client_id:
            raise ValueError(
                f"Оценка выгоды Агента 5 относится к другому клиенту: "
                f"выгода={profitability.client_id!r}, матчинг={match.client_id!r}"
            )

    if match.is_match:
        tender_fit_explanation = f"Закупка «{match.tender.name}» подходит компании — все условия участия выполняются."
    else:
        reasons = "; ".join(match.failed_reasons)
        tender_fit_explanation = f"Закупка «{match.tender.name}» пока не подходит компании: {reasons}."

    if completeness.is_pass():
        package_status_explanation = "Пакет документов полностью готов к подаче."
    else:
        package_status_explanation = "Пакет документов пока не готов — не хватает обязательных данных."

    missing_documents = [
        MissingDocumentItem(name=name, what_to_do=_action_hint(name))
        for name in completeness.missing_required_fields
    ]

    risks = [_translate_risk(r) for r in package.hidden_risks]
    profitability_summary = _translate_profitability(profitability) if profitability is not None else None

    return ClientSummary(
        client_id=match.client_id,
        tender_purchase_number=match.tender.purchase_number,
        tender_name=match.tender.name,
        tender_fits=match.is_match,
        tender_fit_explanation=tender_fit_explanation,
        package_ready=completeness.is_pass(),
        package_status_explanation=package_status_explanation,
        missing_documents=missing_documents,
        risks=risks,
        profitability=profitability_summary,
        decision_reminder=DECISION_REMINDER,
    )


def render_summary_text(summary: ClientSummary) -> str:
    """Готовый текстовый блок для показа собственнику — без кодов статусов
    и структур данных, только читаемый текст."""
    lines: list[str] = []
    lines.append(f"Закупка: {summary.tender_name} ({summary.tender_purchase_number})")
    lines.append("")
    lines.append(f"1. Подходит ли закупка: {summary.tender_fit_explanation}")
    lines.append(f"2. Готовность документов: {summary.package_status_explanation}")

    if summary.profitability is not None:
        lines.append(f"3. Ожидаемая выгода: {summary.profitability.margin_explanation}")
        for flag in summary.profitability.risk_flags:
            lines.append(f"   - {flag}")
        if summary.profitability.win_probability_note:
            lines.append(f"   {summary.profitability.win_probability_note}")

    if summary.missing_documents:
        lines.append("")
        lines.append("Не хватает следующих документов/данных:")
        for item in summary.missing_documents:
            lines.append(f"  - {item.name}: {item.what_to_do}")

    if summary.risks:
        lines.append("")
        lines.append("Обратите внимание на найденные в документации закупки риски:")
        for i, risk in enumerate(summary.risks, start=1):
            lines.append(f"  {i}. {risk.what_it_says}")
            lines.append(f"     Что это значит: {risk.why_it_matters}")
            lines.append(f"     Цитата из документации: «{risk.quote}»")
    else:
        lines.append("")
        lines.append("Скрытых рисков в документации закупки не найдено.")

    lines.append("")
    lines.append(summary.decision_reminder)

    # Обязательное юридическое предупреждение (Агент 12) — часть самой
    # функции рендера, а не опциональное поле ClientSummary, чтобы его
    # нельзя было забыть подключить при выводе сводки.
    lines.append("")
    lines.append(CLIENT_SUMMARY_LEGAL_NOTICE)

    return "\n".join(lines)

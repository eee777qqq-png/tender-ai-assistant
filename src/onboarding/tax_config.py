"""Справочник режимов налогообложения для профиля клиента (Агент 11, блок Г).

Раньше это были два раздельных поля — свободный текст "система
налогообложения" и "среднегодовая выручка" для НДС — из которых сервису
пришлось бы самому вычислять применимую ставку НДС и следить за порогами
доходности УСН и их ежегодной индексацией. Это ненадёжно: пороги меняются
государством, а не по нашей воле, и ошибка в расчёте ставки — это ошибка в
документах клиента.

Решение: клиент выбирает готовую комбинацию режим+ставка сам, из
фиксированного списка (`TaxRegimeChoice`). Сервис (и будущий Агент 5) берёт
ставку прямо из этого выбора, ничего не вычисляя из дохода. Среднегодовая
выручка (`FinancialReadiness.avg_annual_revenue`) никуда не делась — она
осталась для совершенно другой цели, финансовой состоятельности клиента
(Агент 2, `classifier.matching`), и с выбором налогового режима не связана.

Этот модуль — справочник для валидации выбора в форме (`TAX_REGIME_OPTIONS`),
не калькулятор.

**2026-09-25:** прежний единый вариант «ОСН + НДС 22%» разделён на
`OSN_IP_VAT_22` и `OSN_OOO_VAT_22` — у ИП и у ООО на ОСН разные налоги с
дохода (НДФЛ по прогрессивной шкале у ИП, налог на прибыль у ООО), одной
ставки для обоих не существует. Клиент выбирает нужный вариант сам, по тому
же принципу, что и весь остальной справочник — сервис не выводит
организационно-правовую форму клиента из других полей профиля. Сами формулы
расчёта — в `profitability_estimator.estimator` (Агент 5), не здесь; этот
файл только даёт клиенту выбрать из списка. Подробности находок (ставка
налога на прибыль 25%, пороги прогрессивной шкалы НДФЛ) — см. CLAUDE.md,
«Известные пробелы», п.6.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaxRegimeChoice(str, Enum):
    """Ровно 9 вариантов, которые видит клиент в форме — не более и не менее."""

    USN_6_NO_VAT = "usn_6_no_vat"  # УСН 6% без НДС
    USN_6_VAT_5 = "usn_6_vat_5"  # УСН 6% + НДС 5%
    USN_6_VAT_7 = "usn_6_vat_7"  # УСН 6% + НДС 7%
    USN_15_NO_VAT = "usn_15_no_vat"  # УСН 15% без НДС
    USN_15_VAT_5 = "usn_15_vat_5"  # УСН 15% + НДС 5%
    USN_15_VAT_7 = "usn_15_vat_7"  # УСН 15% + НДС 7%
    OSN_IP_VAT_22 = "osn_ip_vat_22"  # ИП на ОСН + НДС 22% (с вычетом)
    OSN_OOO_VAT_22 = "osn_ooo_vat_22"  # ООО на ОСН + НДС 22% (с вычетом)
    OTHER_NEEDS_CLARIFICATION = "other_needs_clarification"  # Другое / требует уточнения с бухгалтером


@dataclass(frozen=True)
class TaxRegimeOption:
    """Один пункт формы: машиночитаемый выбор + человекочитаемая подпись."""

    choice: TaxRegimeChoice
    label: str


TAX_REGIME_OPTIONS: tuple[TaxRegimeOption, ...] = (
    TaxRegimeOption(TaxRegimeChoice.USN_6_NO_VAT, "УСН 6% без НДС"),
    TaxRegimeOption(TaxRegimeChoice.USN_6_VAT_5, "УСН 6% + НДС 5%"),
    TaxRegimeOption(TaxRegimeChoice.USN_6_VAT_7, "УСН 6% + НДС 7%"),
    TaxRegimeOption(TaxRegimeChoice.USN_15_NO_VAT, "УСН 15% без НДС"),
    TaxRegimeOption(TaxRegimeChoice.USN_15_VAT_5, "УСН 15% + НДС 5%"),
    TaxRegimeOption(TaxRegimeChoice.USN_15_VAT_7, "УСН 15% + НДС 7%"),
    TaxRegimeOption(TaxRegimeChoice.OSN_IP_VAT_22, "ИП на ОСН + НДС 22% (с вычетом)"),
    TaxRegimeOption(TaxRegimeChoice.OSN_OOO_VAT_22, "ООО на ОСН + НДС 22% (с вычетом)"),
    TaxRegimeOption(
        TaxRegimeChoice.OTHER_NEEDS_CLARIFICATION, "Другое / требует уточнения с бухгалтером"
    ),
)

_LABEL_BY_CHOICE: dict[TaxRegimeChoice, str] = {opt.choice: opt.label for opt in TAX_REGIME_OPTIONS}


def label_for(choice: TaxRegimeChoice) -> str:
    return _LABEL_BY_CHOICE[choice]


def is_valid_choice(value: str) -> bool:
    """Для валидации сырого значения из формы до того, как оно стало `TaxRegimeChoice`."""
    return value in _LABEL_BY_CHOICE.keys() or value in {c.value for c in TaxRegimeChoice}


# Непроверенные заметки о готовящихся изменениях законодательства — ничего
# в справочнике или форме автоматически не меняют. Порядок работы такой же,
# как с обновлениями нормативной базы у Агента 10 (CLAUDE.md, «Протокол
# контроля качества»): подтверждение только через человека, до этого —
# просто заметка на будущее, а не действующее правило.
UNVERIFIED_NOTES: tuple[str, ...] = (
    "Есть непроверенная информация о возможном возврате базовой ставки НДС "
    "к 20% начиная с 2027 года — источник и точная дата вступления в силу "
    "не подтверждены. До официального подтверждения список вариантов и "
    "ставки в форме не меняются.",
)

"""Агент 1 -> Агент 2 на РЕАЛЬНЫХ данных ЕИС: запрашивает извещения
(subsystemType=PRIZ, documentType44=epNotificationEF2020), строит настоящий
`Tender` из каждого через `eis_client.notice_document_to_tender()` и
прогоняет через `classifier.match_profile_to_tender()`.

**С 2026-09-24 вечером `run_monitor.py` тоже строит и сохраняет `Tender`
(`build_tenders()` → `MonitorStore.save_tenders()`), а извещения — теперь
продакшен-умолчание в `.env`/`.env.example`.** Этот скрипт — не дублирует
монитор, а делает то, чего у монитора сознательно нет: сразу МАТЧИТ каждый
построенный `Tender` против клиента (`classifier.match_profile_to_tender()`)
и печатает результат в консоль — удобно для ручной проверки/демонстрации.
У монитора эта функция по-прежнему не его задача: он копит `Tender`-ы для
конвейера, не решает, кому они подходят (нет и не должно быть в нём
единственного «клиента», под которого можно матчить каждый запуск).

Запускать можно и без явных переменных окружения — `.env`/`config.py`
теперь по умолчанию PRIZ/epNotificationEF2020:

    python src/match_real_notices.py --date 2026-09-23

(явное переопределение `EIS_SUBSYSTEM_TYPE=PRIZ` всё ещё работает, если
`.env` вручную настроен иначе — например, временно на контракты.)

**Честная оговорка про requires_sro/min_experience_years** — эти 2 поля
`Tender` не извлекаются из извещения (проверено на 463 реальных документах,
2026-09-24, см. `notice_parser.notice_document_to_tender()`) — задаются
явно флагами `--requires-sro`/`--min-experience-years`, по умолчанию
False/0. Результат матчинга по этим двум критериям **не соответствует
реальным требованиям закупки**, пока их не проверит человек по вложенным
документам извещения — скрипт печатает предупреждение об этом на каждом
запуске, не только в докстринге.

Профиль клиента — один иллюстративный пример (см. `_example_profile()`),
не настоящий клиент: у проекта пока нет хранилища профилей (Агент 11 —
только форма и валидация, без БД), реальный профиль будет передаваться
сюда, когда оно появится.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from classifier import ConstructionClassifier, match_profile_to_tender
from eis_client import EISClient, EISConfig, notice_document_to_tender
from eis_client.exceptions import EISError
from onboarding import (
    Capacity,
    ClientProfile,
    CompletedContract,
    FinancialReadiness,
    LegalInfo,
    PermitsExperience,
    TaxRegimeChoice,
    validate_profile,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=date.today() - timedelta(days=1),
        help="Дата извещений (YYYY-MM-DD), по умолчанию — вчера",
    )
    parser.add_argument(
        "--region-code",
        default=None,
        help="Регион профиля-примера (по умолчанию — тот же, что EIS_ORG_REGION в конфиге)",
    )
    parser.add_argument(
        "--requires-sro",
        action="store_true",
        help="ЧЕСТНАЯ ЗАГЛУШКА: пометить все найденные закупки как требующие СРО "
        "(поле не извлекается из извещения — см. докстринг модуля)",
    )
    parser.add_argument(
        "--min-experience-years",
        type=int,
        default=0,
        help="ЧЕСТНАЯ ЗАГЛУШКА: минимальный опыт для всех найденных закупок "
        "(поле не извлекается из извещения — см. докстринг модуля)",
    )
    return parser.parse_args()


def _example_profile(region_code: str) -> ClientProfile:
    """Один иллюстративный профиль клиента — не настоящий (см. докстринг модуля)."""
    profile = ClientProfile(
        client_id="demo-client",
        region_code=region_code,
        legal=LegalInfo(
            org_name="ООО СтройМастер (пример)",
            inn="7701234567",
            ogrn="1027700132195",
            legal_address="г. Москва, ул. Примерная, д. 1",
            contact_person="Иванов Иван",
            phone="+79991234567",
            email="info@example.ru",
        ),
        permits_experience=PermitsExperience(
            sro_membership=True,
            sro_number="СРО-С-123-456",
            completed_contracts=[
                CompletedContract(object_name="Капремонт школы №5", customer="ДепОбр", amount=5_000_000, year=2024)
            ],
            years_of_experience=5,
        ),
        capacity=Capacity(staff_count=15, own_workforce_description="15 штатных рабочих"),
        financial=FinancialReadiness(
            tax_regime=TaxRegimeChoice.USN_6_NO_VAT,
            avg_annual_revenue=50_000_000,
            working_capital=3_000_000,
            bank_guarantee_available=True,
        ),
    )
    validate_profile(profile)
    profile.submit_expert_review(reviewer="Edwin", approved=True)
    profile.mark_ready()
    return profile


def main() -> int:
    load_dotenv()
    args = parse_args()

    try:
        config = EISConfig.from_env()
    except EISError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        return 1

    if config.subsystem_type != "PRIZ":
        logger.warning(
            "EIS_SUBSYSTEM_TYPE=%s — не PRIZ. notice_document_to_tender() рассчитан на "
            "извещения (epNotificationEF2020), не на контракты; запрос, скорее всего, "
            "даст SOAP-фолт или документы, которые не разберутся как извещение.",
            config.subsystem_type,
        )

    logger.warning(
        "requires_sro=%s, min_experience_years=%d — ЧЕСТНАЯ ЗАГЛУШКА, не из данных ЕИС "
        "(это поле не извлекается из извещения, проверено на 463 реальных документах, "
        "см. докстринг notice_parser.notice_document_to_tender()). Критерии матчинга "
        "sro_membership/experience по найденным закупкам не отражают реальные требования, "
        "пока их не проверит человек по вложенным документам извещения.",
        args.requires_sro,
        args.min_experience_years,
    )

    region_code = args.region_code or config.org_region
    profile = _example_profile(region_code)
    classifier = ConstructionClassifier()

    with EISClient(config, construction_classifier=classifier) as client:
        try:
            documents = client.get_construction_documents(args.date)
        except EISError as exc:
            logger.error("Ошибка при обращении к ЕИС: %s", exc)
            return 1

    logger.info("Строительных документов за %s (регион %s): %d", args.date, config.org_region, len(documents))

    built = 0
    skipped = 0
    for document in documents:
        if document.raw_xml is None:
            skipped += 1
            continue
        try:
            tender = notice_document_to_tender(
                document.raw_xml,
                requires_sro=args.requires_sro,
                min_experience_years=args.min_experience_years,
            )
        except ValueError as exc:
            logger.warning("Пропущен %s — не удалось построить Tender: %s", document.file_name, exc)
            skipped += 1
            continue

        built += 1
        match = match_profile_to_tender(profile, tender, classifier)

        print(f"\n=== {tender.purchase_number} ===")
        print(f"  {tender.name}")
        print(f"  Заказчик: {tender.customer_name}")
        print(
            f"  НМЦК: {tender.max_price:,.2f} руб., регион: {tender.region_code}, "
            f"срок подачи: {tender.submission_deadline}"
        )
        for c in match.criteria:
            print(f"  [{'OK' if c.passed else 'FAIL'}] {c.name}: {c.message}")
        print(f"  Итог: {'ПОДХОДИТ' if match.is_match else 'НЕ ПОДХОДИТ'} (score={match.score:.2f})")

    logger.info("Готово. Tender построен и сматчен: %d, пропущено: %d", built, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

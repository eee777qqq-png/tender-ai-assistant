"""Агент 1 -> Агент 2 на РЕАЛЬНЫХ данных ЕИС: строит настоящий `Tender` из
извещения (subsystemType=PRIZ, documentType44=epNotificationEF2020) через
`eis_client.notice_document_to_tender()` и прогоняет через два явных прохода
Агента 2 — против реального профиля первого клиента пилота
(`real_client_profile.get_real_client_profile()`, 2026-09-26), не
иллюстративного примера.

**Два прохода классификации, не один — CLAUDE.md, п.11, закрыт 2026-09-27.**
Раньше здесь был единственный вызов `match_profile_to_tender()` сразу на
`Tender` из извещения — с честной заглушкой `requires_sro=False`,
`min_experience_years=0` (см. ниже) вердикт «ПОДХОДИТ» мог быть ложным, и на
практике был (тендер №0373200032226000750, капремонт ЖКХ, 2026-09-26).
Теперь:

1. `classifier.coarse_classify()` — дешёвый фильтр без сети, как и раньше.
2. Если прошёл — `eis_client.fetch_participant_requirements()` скачивает и
   разбирает приложение «Требования к заявке» (Агент 3), без единого
   ручного шага.
3. `classifier.final_classify()` — окончательный вердикт с учётом того, что
   реально нашёл Агент 3, а не заглушки `requires_sro`/`min_experience_years`.
   Именно этот вердикт печатается как итоговый, не вердикт прохода 1.

Если в извещении нет приложения нужного типа (`fetch_participant_requirements()`
вернул `None`) — используется пустой `ExtractedRequirements` (нет находок,
значит и противоречить нечему), не пропуск закупки: coarse-вердикт остаётся
окончательным, честно помечен как «документация не проверена».

**С 2026-09-24 вечером `run_monitor.py` тоже строит и сохраняет `Tender`
(`build_tenders()` → `MonitorStore.save_tenders()`), а извещения — теперь
продакшен-умолчание в `.env`/`.env.example`.** Этот скрипт — не дублирует
монитор, а делает то, чего у монитора сознательно нет: сразу МАТЧИТ каждый
построенный `Tender` против клиента (`classifier.coarse_classify()`/
`final_classify()`, см. выше) и печатает результат в консоль — удобно для
ручной проверки/демонстрации.
У монитора эта функция по-прежнему не его задача: он копит `Tender`-ы для
конвейера, не решает, кому они подходят (нет и не должно быть в нём
единственного «клиента», под которого можно матчить каждый запуск).

**Два режима получения документов, 2026-09-26:**

- сетевой (по умолчанию) — как и раньше, живой SOAP-запрос к ЕИС за
  конкретную дату:

      python src/match_real_notices.py --date 2026-09-23

  (запускать можно и без явных переменных окружения — `.env`/`config.py`
  по умолчанию PRIZ/epNotificationEF2020; явное переопределение
  `EIS_SUBSYSTEM_TYPE=PRIZ` всё ещё работает, если `.env` настроен иначе);

- офлайн (`--archive-dir`) — без единого сетевого запроса читает уже
  скачанные ранее архивы (`data/raw_notices`/`data/raw_notices_r61`,
  накопленные `run_monitor.py`/предыдущими прогонами с `raw_archive_dir=`)
  через новый `EISClient.get_construction_documents_from_local_archives()`
  (та же логика разбора/фильтрации, что и у сетевого пути — не отдельная
  копия):

      python src/match_real_notices.py --archive-dir data/raw_notices

  `--date` в этом режиме не используется — архивы за все даты в каталоге
  обрабатываются разом.

**Честная оговорка про requires_sro/min_experience_years** — эти 2 поля
`Tender` не извлекаются из извещения (проверено на 463 реальных документах,
2026-09-24, см. `notice_parser.notice_document_to_tender()`) — задаются
явно флагами `--requires-sro`/`--min-experience-years`, по умолчанию
False/0. Результат матчинга по этим двум критериям **не соответствует
реальным требованиям закупки**, пока их не проверит человек по вложенным
документам извещения — скрипт печатает предупреждение об этом на каждом
запуске, не только в докстринге.
"""

from __future__ import annotations

import argparse
import glob
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from classifier import ConstructionClassifier, coarse_classify, final_classify
from document_analyst.models import ExtractedRequirements
from eis_client import EISClient, EISConfig, fetch_participant_requirements, notice_document_to_tender
from eis_client.exceptions import AttachmentParseError, EISError

_MISSING_PROFILE_HINT = """
Не найден src/real_client_profile.py — реальный профиль клиента (Агент 11).

Это ОЖИДАЕМО для любого окружения, кроме личной машины Edwin: файл содержит
настоящие ПДн (ИНН, домашний адрес, телефон, email) и намеренно НЕ хранится
в git (см. .gitignore, тот же принцип, что и у data/raw_notices/.env).

Что делать:
    cp src/real_client_profile.py.example src/real_client_profile.py
и заполнить реальными данными клиента (см. комментарии в файле).

Скрипт НЕ подставляет вместо этого файла никакую заглушку/демо-профиль —
молчаливая подмена реального клиента демо-данными означала бы, что вывод
матчинга выглядел бы правдоподобно, но относился бы не к тому клиенту.
""".strip()

try:
    from real_client_profile import get_real_client_profile
except ModuleNotFoundError as exc:
    if exc.name != "real_client_profile":
        raise
    get_real_client_profile = None  # type: ignore[assignment]

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
        "--archive-dir",
        default=None,
        help="Офлайн-режим: читать уже скачанные архивы *.zip из этого каталога "
        "(например, data/raw_notices) вместо живого запроса к ЕИС. --date игнорируется.",
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


def main() -> int:
    load_dotenv()
    args = parse_args()

    if get_real_client_profile is None:
        logger.error(_MISSING_PROFILE_HINT)
        return 1

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

    profile = get_real_client_profile()
    classifier = ConstructionClassifier()

    built = 0
    skipped = 0
    coarse_rejected = 0
    overturned_by_agent3 = 0

    with EISClient(config, construction_classifier=classifier) as client:
        try:
            if args.archive_dir:
                archive_paths = sorted(Path(p) for p in glob.glob(str(Path(args.archive_dir) / "*.zip")))
                logger.info("Офлайн-режим: %s — найдено архивов: %d", args.archive_dir, len(archive_paths))
                documents = client.get_construction_documents_from_local_archives(archive_paths)
            else:
                documents = client.get_construction_documents(args.date)
        except EISError as exc:
            logger.error("Ошибка при обращении к ЕИС: %s", exc)
            return 1

        if args.archive_dir:
            logger.info("Строительных документов в %s: %d", args.archive_dir, len(documents))
        else:
            logger.info(
                "Строительных документов за %s (регион %s): %d", args.date, config.org_region, len(documents)
            )

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
            print(f"\n=== {tender.purchase_number} ===")
            print(f"  {tender.name}")
            print(f"  Заказчик: {tender.customer_name}")
            print(
                f"  НМЦК: {tender.max_price:,.2f} руб., регион: {tender.region_code}, "
                f"срок подачи: {tender.submission_deadline}"
            )

            # Проход 1 — coarse, до скачивания документации закупки.
            coarse = coarse_classify(profile, tender, classifier)
            print(f"  --- Агент 2, проход 1 (coarse) ---")
            for c in coarse.criteria:
                print(f"  [{'OK' if c.passed else 'FAIL'}] {c.name}: {c.message}")

            if not coarse.is_match:
                coarse_rejected += 1
                print(f"  Итог: НЕ ПОДХОДИТ (score={coarse.score:.2f}) — документация не запрашивалась")
                continue

            # Проход 1 прошёл — только теперь скачиваем и разбираем
            # документацию закупки (Агент 3), не раньше.
            try:
                extracted = fetch_participant_requirements(client, document.raw_xml, tender.purchase_number)
            except (EISError, AttachmentParseError) as exc:
                logger.warning(
                    "Не удалось скачать/открыть документацию закупки %s (Агент 3): %s — используем только coarse-вердикт",
                    tender.purchase_number,
                    exc,
                )
                extracted = None

            if extracted is None:
                extracted = ExtractedRequirements(tender_purchase_number=tender.purchase_number)
                print(
                    "  (в извещении не нашлось приложения «Требования к заявке» — "
                    "coarse-вердикт остаётся окончательным, документация не проверена)"
                )

            final = final_classify(profile, tender, classifier, extracted)
            print(f"  --- Агент 2, проход 2 (final, после Агента 3) ---")
            for c in final.criteria:
                print(f"  [{'OK' if c.passed else 'FAIL'}] {c.name}: {c.message}")
            print(f"  Итог: {'ПОДХОДИТ' if final.is_match else 'НЕ ПОДХОДИТ'} (score={final.score:.2f})")

            if coarse.is_match and not final.is_match:
                overturned_by_agent3 += 1
                print(
                    "  ⚠ Проход 1 сказал «ПОДХОДИТ», но Агент 3 нашёл в документации то, "
                    "что профиль не подтверждает — итоговый вердикт изменён."
                )

    logger.info(
        "Готово. Tender построен: %d, пропущено: %d, отсеяно на проходе 1: %d, "
        "перевёрнуто Агентом 3 на проходе 2: %d",
        built,
        skipped,
        coarse_rejected,
        overturned_by_agent3,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

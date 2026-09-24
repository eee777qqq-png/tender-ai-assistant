"""Агент 1 (Монитор): опрашивает ЕИС за каждый ещё не обработанный день,
копит найденные документы по разделу «Строительство» в локальном хранилище
(data/monitor.sqlite3) и — если конфиг настроен на ИЗВЕЩЕНИЯ (по умолчанию с
2026-09-24, см. CLAUDE.md) — строит и сохраняет полноценный `Tender` для
каждого документа через `eis_client.notice_document_to_tender()`.

Рассчитан на регулярный запуск (cron / Планировщик заданий Windows) — при
каждом запуске продолжает с даты, следующей за последней уже обработанной
(или с --start-date / вчера, если хранилище пустое), и доходит до вчера
включительно. Сервис ЕИС не отдаёт сегодняшний день до его окончания,
поэтому монитор останавливается на "вчера".

**Честная оговорка про `requires_sro`/`min_experience_years`** — эти 2 поля
`Tender` не извлекаются из извещения нигде (см. CLAUDE.md, «Известные
пробелы», п.13) — здесь всегда `False`/`0`, и `StoredTender.sro_experience_verified`
явно помечает это `False`, чтобы код, который когда-нибудь будет читать
`store.all_tenders()`, не принял заглушку за подтверждённое требование
закупки.

Не путать с `src/match_real_notices.py` — тот дополнительно матчит каждый
построенный `Tender` против одного иллюстративного профиля клиента (для
демонстрации/ручной проверки); у монитора эта функция была и остаётся не
его: он строит `Tender`-ы для дальнейшего конвейера, не решает, кому они
подходят.

Использование:
    python src/run_monitor.py
    python src/run_monitor.py --start-date 2026-09-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from classifier import ConstructionClassifier
from classifier.tender import Tender
from eis_client import EISClient, EISConfig, notice_document_to_tender
from eis_client.client import ConstructionDocument
from eis_client.exceptions import EISError
from eis_client.store import MonitorStore

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Дата, с которой начинать при пустом хранилище (по умолчанию — вчера)",
    )
    return parser.parse_args()


def dates_to_fetch(store: MonitorStore, start_date_override: date | None) -> list[date]:
    yesterday = date.today() - timedelta(days=1)
    last_fetched = store.last_fetched_date()

    if last_fetched is not None:
        start = last_fetched + timedelta(days=1)
    elif start_date_override is not None:
        start = start_date_override
    else:
        start = yesterday

    if start > yesterday:
        return []
    return [start + timedelta(days=i) for i in range((yesterday - start).days + 1)]


def build_tenders(documents: list[ConstructionDocument]) -> tuple[list[Tender], int]:
    """Строит `Tender` из каждого документа, у которого есть `raw_xml`, через
    `notice_document_to_tender()`. `requires_sro`/`min_experience_years` —
    всегда `False`/`0` (не данные ЕИС, см. докстринг модуля).

    Возвращает (построенные Tender-ы, число пропущенных). Пропуск — честный
    исход (например, документ пришёл из реестра КОНТРАКТОВ, у него другая
    структура и `notice_document_to_tender()` не находит нужные поля), не
    падение всего запуска — один плохой документ не должен останавливать
    обработку остальных за день."""
    tenders: list[Tender] = []
    skipped = 0
    for document in documents:
        if document.raw_xml is None:
            skipped += 1
            continue
        try:
            tender = notice_document_to_tender(document.raw_xml, requires_sro=False, min_experience_years=0)
        except ValueError as exc:
            logger.warning("Пропущен %s — не удалось построить Tender: %s", document.file_name, exc)
            skipped += 1
            continue
        tenders.append(tender)
    return tenders, skipped


def main() -> int:
    load_dotenv()
    args = parse_args()

    try:
        config = EISConfig.from_env()
    except EISError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        logger.error("Проверьте .env — см. .env.example и README.md")
        return 1

    store = MonitorStore()
    pending_dates = dates_to_fetch(store, args.start_date)

    if not pending_dates:
        logger.info("Нечего опрашивать — хранилище уже актуально по вчерашний день")
        return 0

    logger.info("Дней к опросу: %d (с %s по %s)", len(pending_dates), pending_dates[0], pending_dates[-1])

    build_tenders_enabled = config.subsystem_type == "PRIZ"
    if build_tenders_enabled:
        logger.warning(
            "requires_sro=False, min_experience_years=0 у ВСЕХ построенных Tender — ЧЕСТНАЯ ЗАГЛУШКА, "
            "не данные ЕИС (проверено на 463 реальных извещениях — поле не нашлось нигде, см. CLAUDE.md, "
            "«Известные пробелы», п.13). StoredTender.sro_experience_verified=False."
        )
    else:
        logger.info(
            "EIS_SUBSYSTEM_TYPE=%s — не PRIZ, Tender не строится (notice_document_to_tender() рассчитан "
            "на извещения, не на контракты): будут сохранены только документы, как раньше.",
            config.subsystem_type,
        )

    classifier = ConstructionClassifier()
    total_found = 0
    total_tenders = 0
    total_skipped = 0

    with EISClient(config, construction_classifier=classifier) as client:
        for fetch_date in pending_dates:
            try:
                documents = client.get_construction_documents(fetch_date)
            except EISError as exc:
                logger.error("Ошибка при опросе %s: %s — останавливаюсь, повторю в следующий запуск", fetch_date, exc)
                return 1

            store.save_results(fetch_date, documents)
            total_found += len(documents)
            logger.info("%s: найдено документов по стройке — %d", fetch_date, len(documents))

            if build_tenders_enabled:
                tenders, skipped = build_tenders(documents)
                store.save_tenders(fetch_date, tenders, sro_experience_verified=False)
                total_tenders += len(tenders)
                total_skipped += skipped
                logger.info("%s: Tender построено — %d, пропущено — %d", fetch_date, len(tenders), skipped)

    if build_tenders_enabled:
        logger.info(
            "Готово. Документов найдено: %d, Tender построено: %d, пропущено: %d",
            total_found, total_tenders, total_skipped,
        )
    else:
        logger.info("Готово. Всего найдено за этот запуск: %d", total_found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

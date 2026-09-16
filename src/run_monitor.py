"""Агент 1 (Монитор): опрашивает ЕИС за каждый ещё не обработанный день
и копит найденные документы по разделу «Строительство» в локальном
хранилище (data/monitor.sqlite3).

Рассчитан на регулярный запуск (cron / Планировщик заданий Windows) — при
каждом запуске продолжает с даты, следующей за последней уже обработанной
(или с --start-date / вчера, если хранилище пустое), и доходит до вчера
включительно. Сервис ЕИС не отдаёт сегодняшний день до его окончания,
поэтому монитор останавливается на "вчера".

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
from eis_client import EISClient, EISConfig
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

    classifier = ConstructionClassifier()
    total_found = 0

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

    logger.info("Готово. Всего найдено за этот запуск: %d", total_found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

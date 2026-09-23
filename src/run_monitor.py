"""Агент 1 (Монитор): опрашивает ЕИС за каждый ещё не обработанный день
и копит найденные документы по разделу «Строительство» в локальном
хранилище (data/monitor.sqlite3).

**Источник данных с 2026-09-23 — реестр ИЗВЕЩЕНИЙ (`PRIZ`/
`epNotificationEF2020` по умолчанию, см. `.env.example`), не контрактов.**
Продуктовое решение: клиенту нужны закупки, в которых ещё можно
поучаствовать (предстоящие извещения), а не архив уже заключённых сделок
(контракты, что читалось раньше). Гипотеза кода реестра подтверждена
реальным запросом — см. CLAUDE.md, «Известные пробелы» → «Решено».

**«Обработана» теперь означает «обработана под ЭТИМ источником», не
только «под этой датой»** (`eis_client.store.source_signature()` —
`EIS_SUBSYSTEM_TYPE:EIS_DOCUMENT_TYPE44`). Даты, ранее опрошенные под
старым источником (контракты), при переходе на извещения автоматически
считаются НЕобработанными для нового источника — это не баг, это защита
от того, чтобы «0 контрактов в тот день» тихо сошло за «0 извещений»,
хотя это два разных вопроса. См. предупреждение при запуске ниже, если
такие даты найдутся.

Рассчитан на регулярный запуск (cron / Планировщик заданий Windows) — при
каждом запуске продолжает с даты, следующей за последней уже обработанной
ПОД ТЕКУЩИМ ИСТОЧНИКОМ (или с --start-date / вчера, если для текущего
источника хранилище пустое), и доходит до вчера включительно. Сервис ЕИС
не отдаёт сегодняшний день до его окончания, поэтому монитор
останавливается на "вчера".

**Важно после перехода на извещения:** по умолчанию монитор НЕ идёт назад
и не перезапрашивает автоматически весь диапазон, что раньше был покрыт
под старым источником, — только явно предупреждает о нём (сколько дат,
под какой подписью). Решение, насколько глубоко backfill'ить историю под
новым источником — за Edwin: `python src/run_monitor.py --start-date ...`
с нужной датой.

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
from eis_client.store import MonitorStore, source_signature

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


def dates_to_fetch(store: MonitorStore, start_date_override: date | None, signature: str) -> list[date]:
    yesterday = date.today() - timedelta(days=1)
    last_fetched = store.last_fetched_date(signature)

    if last_fetched is not None:
        start = last_fetched + timedelta(days=1)
    elif start_date_override is not None:
        start = start_date_override
    else:
        start = yesterday

    if start > yesterday:
        return []
    return [start + timedelta(days=i) for i in range((yesterday - start).days + 1)]


def _warn_about_other_signatures(store: MonitorStore, signature: str) -> None:
    """Явное предупреждение, не молчание, если в хранилище есть даты,
    обработанные под ДРУГИМ источником (например, старым — реестром
    контрактов, до перехода на извещения 2026-09-23). Такие даты для
    текущего источника не считаются обработанными и не отражены в
    `dates_to_fetch()` автоматически — see докстринг модуля."""
    other = store.dates_fetched_under_other_signatures(signature)
    if not other:
        return
    for other_signature, count in other.items():
        logger.warning(
            "В хранилище %d дат(ы) помечены обработанными под ДРУГИМ источником (%s), "
            "не текущим (%s) — под текущим источником они НЕ считаются обработанными и "
            "требуют полного перезапроса, если нужны: python src/run_monitor.py --start-date <дата>. "
            "Это не '0 закупок в тот день' и не 'устаревшие ОКПД2-коды' — это данные другого "
            "типа документа (например, контракты вместо извещений), полностью несопоставимые.",
            count,
            other_signature,
            signature,
        )


def main() -> int:
    load_dotenv()
    args = parse_args()

    try:
        config = EISConfig.from_env()
    except EISError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        logger.error("Проверьте .env — см. .env.example и README.md")
        return 1

    signature = source_signature(config)
    logger.info("Источник данных: %s", signature)

    store = MonitorStore()
    _warn_about_other_signatures(store, signature)

    pending_dates = dates_to_fetch(store, args.start_date, signature)

    if not pending_dates:
        logger.info("Нечего опрашивать — хранилище уже актуально по вчерашний день для этого источника")
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

            store.save_results(fetch_date, documents, signature)
            total_found += len(documents)
            logger.info("%s: найдено документов по стройке — %d", fetch_date, len(documents))

    logger.info("Готово. Всего найдено за этот запуск: %d", total_found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

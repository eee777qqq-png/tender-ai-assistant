"""Периодическая проверка новых версий данных ФГИС ЦС для Агента 4
(Сметчик) — тем же паттерном, что уже есть у Агента 10: обнаруженная новая
версия становится записью `PENDING` в `RegulatoryUpdateStore` и ждёт
явного решения эксперта через `src/review_queue.py`, автоматически не
применяется ни при каких обстоятельствах.

Проверяет два независимых источника (см. `smeta_estimator.version_watch`
за подробностями и ссылками на проверенные вживую эндпоинты):
- архив ФСНБ-2022 (ГЭСН/ФСБЦ) — один на весь проект;
- квартальный период сметных цен ФГИС ЦС — отдельно для каждого из 4
  пилотных регионов.

Рассчитан на регулярный запуск (cron / Планировщик заданий Windows), по
аналогии с `src/run_monitor.py` у Агента 1 — но, в отличие от него, здесь
нечего "продолжать со вчерашнего дня": каждый запуск просто сверяет текущее
состояние ФГИС ЦС с уже применённой версией, без собственного состояния,
кроме самого `RegulatoryUpdateStore`.

Использование:
    python src/check_smeta_data_versions.py
    python src/check_smeta_data_versions.py --db data/regulatory_updates.sqlite3
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regulatory_updates import RegulatoryUpdateStore
from smeta_estimator.version_watch import check_all_period_updates, check_fsnb_archive_update

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=None, help="Путь к SQLite Агента 10 (по умолчанию data/regulatory_updates.sqlite3)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = RegulatoryUpdateStore(args.db)

    logger.info("Проверяю версию архива ФСНБ-2022...")
    archive_update = check_fsnb_archive_update(store)
    if archive_update is not None:
        logger.info(
            "Найдена новая версия ФСНБ-2022: %s -> %s (ждёт решения эксперта, id=%s)",
            archive_update.diff.from_version_label or "(применённой версии ещё не было)",
            archive_update.diff.to_version_label,
            archive_update.update_id,
        )
    else:
        logger.info("Архив ФСНБ-2022 — новой версии нет, применённая актуальна.")

    logger.info("Проверяю квартальные периоды по 4 пилотным регионам...")
    period_updates = check_all_period_updates(store)
    if period_updates:
        for update in period_updates:
            logger.info(
                "Найден новый период (%s): %s -> %s (ждёт решения эксперта, id=%s)",
                update.source_name,
                update.diff.from_version_label or "(применённой версии ещё не было)",
                update.diff.to_version_label,
                update.update_id,
            )
    else:
        logger.info("Квартальные периоды — новых версий нет ни у одного из 4 регионов.")

    total_new = len(period_updates) + (1 if archive_update is not None else 0)
    if total_new:
        logger.info(
            "Итого %d новых версий ждут решения эксперта — запустите src/review_queue.py.",
            total_new,
        )
    else:
        logger.info("Итого: ничего не изменилось, решение эксперта не требуется.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

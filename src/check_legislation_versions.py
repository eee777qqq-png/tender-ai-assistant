"""Периодическая проверка бухгалтерской/юридической базы (Агент 10) — тем
же паттерном, что уже есть у Агента 4 (`src/check_smeta_data_versions.py`):
обнаруженная новая версия становится записью `PENDING` в
`RegulatoryUpdateStore` и ждёт явного решения эксперта через
`src/review_queue.py`, автоматически не применяется ни при каких
обстоятельствах.

Проверяет два независимых набора источников (см.
`regulatory_updates.legislation_watch` за подробностями, URL и разбором
регулярных выражений):
- бухгалтерская база (ставки НДС/УСН/налога на прибыль/НДФЛ, nalog.gov.ru)
  — квартальный календарь;
- юридическая база (ГК РФ ст. 401/421, Закон «О защите прав потребителей»
  ст. 16, consultant.ru) — годовой календарь.

Оба календаря учитываются самим модулем (`is_due_for_check()`) — обычный
ежедневный/еженедельный запуск этого скрипта из cron / Планировщика заданий
не будет реально дёргать сеть чаще, чем нужно; для внепланового прогона
(например, сразу после того как стало известно об изменении закона) есть
`--force`.

Использование:
    python src/check_legislation_versions.py
    python src/check_legislation_versions.py --force
    python src/check_legislation_versions.py --db data/regulatory_updates.sqlite3
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regulatory_updates import RegulatoryUpdateStore
from regulatory_updates.legislation_watch import check_all_civil_law_sources, check_all_tax_sources

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db", type=Path, default=None, help="Путь к SQLite Агента 10 (по умолчанию data/regulatory_updates.sqlite3)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Проверить все источники сейчас, игнорируя квартальный/годовой календарь",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = RegulatoryUpdateStore(args.db)

    logger.info("Проверяю бухгалтерскую базу (НДС/УСН/налог на прибыль/НДФЛ, nalog.gov.ru)...")
    tax_updates = check_all_tax_sources(store, force=args.force)
    for update in tax_updates:
        logger.info(
            "Найдено изменение (%s): %s -> %s (ждёт решения эксперта, id=%s)",
            update.source_name,
            update.diff.from_version_label or "(применённой версии ещё не было)",
            update.diff.to_version_label,
            update.update_id,
        )
    if not tax_updates:
        logger.info("Бухгалтерская база — изменений нет (или ещё не due по календарю).")

    logger.info("Проверяю юридическую базу (ГК РФ, Закон о защите прав потребителей, consultant.ru)...")
    civil_law_updates = check_all_civil_law_sources(store, force=args.force)
    for update in civil_law_updates:
        logger.info(
            "Найдено изменение (%s): %s -> %s (ждёт решения эксперта, id=%s)",
            update.source_name,
            update.diff.from_version_label or "(применённой версии ещё не было)",
            update.diff.to_version_label,
            update.update_id,
        )
    if not civil_law_updates:
        logger.info("Юридическая база — изменений нет (или ещё не due по календарю).")

    total_new = len(tax_updates) + len(civil_law_updates)
    if total_new:
        logger.info(
            "Итого %d новых версий ждут решения эксперта — запустите src/review_queue.py.", total_new
        )
    else:
        logger.info("Итого: ничего не изменилось, решение эксперта не требуется.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

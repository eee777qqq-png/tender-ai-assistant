"""CLI: выгружает документы ЕИС за конкретную дату для региона Москва
и оставляет только те, у которых ОКПД2 относится к разделу «Строительство».

Использование:
    python src/fetch_purchases.py --date 2026-09-15
    python src/fetch_purchases.py --date 2026-09-15 --out docs.csv
    python src/fetch_purchases.py --date 2026-09-21 --save-raw data/raw

Перед запуском заполните .env (см. .env.example) — нужны consumer_type
(legal_entity/individual_person) и соответствующие ему учётные данные
(сертификат для mTLS или токен).

Важно: сервис ЕИС отдаёт документы только за ОДНУ конкретную дату за
запрос (параметр exactDate) — диапазон дат не поддерживается, для
мониторинга нужно опрашивать сервис за каждый день отдельно.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from classifier import ConstructionClassifier
from eis_client import EISClient, EISConfig
from eis_client.exceptions import EISError

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today() - timedelta(days=1),
        help="Дата документов (YYYY-MM-DD), по умолчанию — вчера",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Путь к CSV-файлу для сохранения результата (по умолчанию — вывод в консоль)",
    )
    parser.add_argument(
        "--save-raw",
        type=Path,
        default=None,
        metavar="ПАПКА",
        help="Сохранить скачанные архивы ЕИС как есть в эту папку (например data/raw) — "
        "для ручного разбора структуры документов, см. src/inspect_eis_xml.py",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    try:
        config = EISConfig.from_env()
    except EISError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        logger.error("Проверьте .env — см. .env.example и README.md")
        return 1

    classifier = ConstructionClassifier()

    try:
        with EISClient(config, construction_classifier=classifier, raw_archive_dir=args.save_raw) as client:
            documents = client.get_construction_documents(args.date)
    except EISError as exc:
        logger.error("Ошибка при обращении к ЕИС: %s", exc)
        return 1

    logger.info("Найдено документов по стройке за %s: %d", args.date, len(documents))

    if args.out:
        write_csv(documents, args.out)
        logger.info("Результат сохранён в %s", args.out)
    else:
        for doc in documents:
            print(f"{doc.file_name}\t{', '.join(doc.okpd2_codes)}\t{doc.archive_url}")

    return 0


def write_csv(documents, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Файл", "ОКПД2-коды", "Ссылка на архив"])
        for doc in documents:
            writer.writerow([doc.file_name, ", ".join(doc.okpd2_codes), doc.archive_url])


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI: выгружает закупки по ОКПД2-кодам строительства для региона Москва.

Использование:
    python src/fetch_purchases.py
    python src/fetch_purchases.py --okpd2 41,42,43 --region 77 --limit 20

Перед запуском заполните .env (см. .env.example) — нужны рабочий WSDL_URL,
название операции и путь к клиентскому сертификату/ключу для mTLS.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
        "--okpd2",
        help="Список ОКПД2-кодов через запятую (по умолчанию — из .env)",
        default=None,
    )
    parser.add_argument(
        "--region",
        help="Код региона (по умолчанию — из .env, 77 = Москва)",
        default=None,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Максимум записей для вывода (по умолчанию — все полученные)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Путь к CSV-файлу для сохранения результата (по умолчанию — вывод в консоль)",
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

    okpd2_codes = args.okpd2.split(",") if args.okpd2 else None
    region_code = args.region

    try:
        with EISClient(config) as client:
            purchases = client.get_purchases_by_okpd2(
                okpd2_codes=okpd2_codes,
                region_code=region_code,
            )
    except EISError as exc:
        logger.error("Ошибка при обращении к ЕИС: %s", exc)
        return 1

    if args.limit:
        purchases = purchases[: args.limit]

    logger.info("Получено закупок: %d", len(purchases))

    if args.out:
        write_csv(purchases, args.out)
        logger.info("Результат сохранён в %s", args.out)
    else:
        for purchase in purchases:
            print(
                f"{purchase.purchase_number}\t{purchase.name}\t"
                f"{purchase.customer_name}\t{purchase.max_price}"
            )

    return 0


def write_csv(purchases, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            ["Номер закупки", "Наименование", "Заказчик", "ОКПД2", "НМЦК", "Дата публикации"]
        )
        for p in purchases:
            writer.writerow(
                [p.purchase_number, p.name, p.customer_name, p.okpd2_code, p.max_price, p.publish_date]
            )


if __name__ == "__main__":
    raise SystemExit(main())

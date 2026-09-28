"""Вспомогательный скрипт: найти файл сметы/ведомости объёмов работ (ВОР) в
реальном извещении ЕИС и скачать его — для запуска на компьютере, где ЕИС
реально доступен (см. CLAUDE.md, «Хостинг для Агента 1» — датацентровые IP
заблокированы, только домашний компьютер).

Не часть основного конвейера агентов — разовый инструмент, созданный
2026-09-28 для подготовки сравнения Агент 4 vs Сметрикс на одном и том же
исходном документе (см. CLAUDE.md, п.13, раздел про клиентские инструкции —
это отдельная задача, не связанная с ним напрямую, инструмент просто рядом
по времени создания).

Использование:

    # 1. Посмотреть все стройки за дату и список файлов каждой (имя, вид документа)
    python src/list_notice_attachments.py --date 2026-09-27

    # 2. Скачать все приложения конкретной закупки в папку (по номеру закупки
    #    из вывода шага 1)
    python src/list_notice_attachments.py --date 2026-09-27 \
        --download 0373200012326000999 --out-dir data/downloaded_attachments

Ищите в выводе шага 1 файлы со словами «смета», «ведомость объёмов работ»,
«ВОР», «локальный сметный расчёт» в названии — это то, что нужно для
сравнения с Сметриксом. Скрипт сам не угадывает, какой файл — смета: он
просто показывает список, выбор делает человек, чтобы не подсовывать
Агенту 4 не тот документ по ошибке.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from classifier import ConstructionClassifier
from eis_client import (
    EISClient,
    EISConfig,
    extract_attachments,
    extract_name,
    extract_purchase_number,
)


def _parse_date(value: str) -> date:
    year, month, day = (int(part) for part in value.split("-"))
    return date(year, month, day)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Дата извещений, ГГГГ-ММ-ДД")
    parser.add_argument(
        "--download",
        metavar="PURCHASE_NUMBER",
        help="Номер закупки — скачать её приложения вместо простого списка",
    )
    parser.add_argument(
        "--out-dir",
        default="data/downloaded_attachments",
        help="Куда сохранять скачанные файлы (только с --download)",
    )
    args = parser.parse_args()

    load_dotenv()
    target_date = _parse_date(args.date)
    config = EISConfig.from_env()
    classifier = ConstructionClassifier()

    with EISClient(config, construction_classifier=classifier) as client:
        documents = client.get_construction_documents(target_date)

        if not documents:
            print(f"Ничего не найдено за {target_date} — попробуйте другую дату.")
            return 1

        if args.download:
            return _download_one(client, documents, args.download, Path(args.out_dir))

        _list_all(documents)
        return 0


def _list_all(documents) -> None:
    for doc in documents:
        if not doc.raw_xml:
            continue
        purchase_number = extract_purchase_number(doc.raw_xml)
        name = extract_name(doc.raw_xml)
        attachments = extract_attachments(doc.raw_xml)

        print(f"\n=== Закупка №{purchase_number} ===")
        print(f"  Название: {name}")
        if not attachments:
            print("  (приложений не найдено)")
            continue
        for i, att in enumerate(attachments):
            kind = f"{att.doc_kind_code} — {att.doc_kind_name}" if att.doc_kind_code else "?"
            print(f"  [{i}] {att.file_name}  ({kind}, {att.file_size} байт)")


def _download_one(client: EISClient, documents, purchase_number: str, out_dir: Path) -> int:
    for doc in documents:
        if not doc.raw_xml:
            continue
        if extract_purchase_number(doc.raw_xml) != purchase_number:
            continue

        attachments = extract_attachments(doc.raw_xml)
        if not attachments:
            print(f"У закупки №{purchase_number} нет приложений.")
            return 1

        out_dir.mkdir(parents=True, exist_ok=True)
        for att in attachments:
            content = client.download_attachment(att.url)
            dest = out_dir / att.file_name
            dest.write_bytes(content)
            print(f"Скачано: {dest} ({len(content)} байт)")
        return 0

    print(f"Закупка №{purchase_number} не найдена за эту дату.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

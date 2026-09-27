"""Разведка для открытого п.10 CLAUDE.md: есть ли в реальных приложениях
«Требования к заявке» (CAR) текстовые упоминания требований к мощностям
исполнителя (техника/оборудование/персонал/бригада), не только к опыту.

Не меняет `classifier/matching.py` и не реализует извлечение — только
собирает факты для решения, стоит ли этим заниматься (см. CLAUDE.md,
открытый п.10, подробный план разведки).

Использование:
    python src/investigate_capacity_requirements.py --date 2026-09-25

Перед запуском — заполненный `.env` (см. .env.example), как и для
fetch_purchases.py."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from classifier import ConstructionClassifier
from document_analyst import extract_text_from_docx, extract_text_from_pdf
from eis_client import EISClient, EISConfig
from eis_client.exceptions import EISError
from eis_client.notice_parser import (
    APPLICATION_REQUIREMENTS_DOC_KIND_CODE,
    extract_attachments,
    find_attachment_by_doc_kind,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Ключевые слова про мощности исполнителя — не про опыт (тот уже покрыт
# структурированным полем ЕАИСТ 10.12.1 и regex в extractor.py). Ищем
# именно то, чего сейчас нет ни в одном источнике проекта.
_CAPACITY_KEYWORDS = [
    "наличие техник", "парк техник", "спецтехник", "оборудован",
    "квалифицированн", "специалист", "штат", "бригад", "трудовые ресурс",
    "материально-техническ", "укомплектован", "рабоч", "самосвал",
    "экскаватор", "автокран",
]

_TEXT_EXTRACTORS = {"docx": extract_text_from_docx, "pdf": extract_text_from_pdf}
_WINDOW = 200


def _extract_text(file_name: str, content: bytes) -> str | None:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    extractor = _TEXT_EXTRACTORS.get(ext)
    if extractor is None:
        logger.warning("Формат вложения не поддержан, пропускаю: %s", file_name)
        return None
    return extractor(content)


def _find_keyword_hits(text: str) -> list[str]:
    hits = []
    lowered = text.lower()
    seen_spans: set[tuple[int, int]] = set()
    for keyword in _CAPACITY_KEYWORDS:
        for match in re.finditer(re.escape(keyword), lowered):
            start = max(0, match.start() - _WINDOW)
            end = min(len(text), match.end() + _WINDOW)
            span = (start, end)
            if span in seen_spans:
                continue
            seen_spans.add(span)
            snippet = " ".join(text[start:end].split())
            hits.append(f'...{snippet}...  [ключевое слово: "{keyword}"]')
    return hits


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today() - timedelta(days=1),
        help="Дата извещений (YYYY-MM-DD), по умолчанию — вчера",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Максимум документов проверить за один запуск (чтобы не ждать вечность)",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    try:
        config = EISConfig.from_env()
    except EISError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        return 1

    classifier = ConstructionClassifier()

    checked = 0
    no_attachment = 0
    unsupported_format = 0
    empty_text = 0
    with_hits = 0

    try:
        with EISClient(config, construction_classifier=classifier) as client:
            documents = client.get_construction_documents(args.date)
            logger.info("Найдено документов по стройке за %s: %d", args.date, len(documents))

            for doc in documents:
                if checked >= args.limit:
                    break
                if doc.raw_xml is None:
                    continue
                checked += 1

                print(f"\n=== {doc.file_name} (ОКПД2: {', '.join(doc.okpd2_codes)}) ===")

                attachments = extract_attachments(doc.raw_xml)
                attachment = find_attachment_by_doc_kind(attachments, APPLICATION_REQUIREMENTS_DOC_KIND_CODE)
                if attachment is None:
                    print("  нет приложения CAR («Требования к заявке») в этом извещении")
                    no_attachment += 1
                    continue

                try:
                    content = client.download_attachment(attachment.url)
                except EISError as exc:
                    print(f"  ОШИБКА скачивания приложения: {exc}")
                    continue

                text = _extract_text(attachment.file_name, content)
                if text is None:
                    unsupported_format += 1
                    continue

                if not text.strip():
                    print(f"  приложение {attachment.file_name} — пустой текст (возможно, скан)")
                    empty_text += 1
                    continue

                hits = _find_keyword_hits(text)
                if not hits:
                    print(f"  приложение {attachment.file_name}, {len(text)} символов — "
                          f"НИЧЕГО не найдено по ключевым словам мощностей/техники/персонала")
                else:
                    with_hits += 1
                    print(f"  приложение {attachment.file_name}, {len(text)} символов — "
                          f"{len(hits)} совпадений:")
                    for hit in hits:
                        print(f"    - {hit}")
    except EISError as exc:
        logger.error("Ошибка при обращении к ЕИС: %s", exc)
        return 1

    print("\n=== ИТОГ ===")
    print(f"Проверено документов: {checked}")
    print(f"Без приложения CAR: {no_attachment}")
    print(f"Неподдержанный формат приложения: {unsupported_format}")
    print(f"Пустой текст (вероятный скан): {empty_text}")
    print(f"С совпадениями по ключевым словам мощностей/техники: {with_hits}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

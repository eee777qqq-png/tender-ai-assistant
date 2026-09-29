"""Круг 2 бенчмарка Агента 4 — скачивание документации по двум выбранным
реальным тендерам (не-Москва + другой тип работ, не кровля/потолок).

Не входит в постоянный конвейер (Агент 1/`run_monitor.py`) — разовый
вспомогательный скрипт для подготовки следующего раунда сравнения со
Сметриксом (`areas/smetrix-benchmark.md`), запускать вручную, один раз.

Выбор тендеров сделан по данным `data/monitor.sqlite3` (таблица `tenders`,
результат ночного самовосстановления монитора 2026-09-28/29):

1. №0337100017726000160 — «текущий ремонт (отмостка, кровля)» административного
   здания ФКУ «Налог-Сервис» ФНС России в Краснодарском крае (пилотный регион,
   не Москва) — 2026-09-18.
2. №0337100017726000168 — «текущий ремонт (ремонт плиточного покрытия пола)»
   в Московской области (пилотный регион, не Москва, другой тип работ —
   полы, не кровля/потолок) — 2026-09-28.

Оба заказчика — ФКУ «Налог-Сервис» ФНС России (Москва), но объекты — в
регионах исполнения, не регистрации заказчика (см. CLAUDE.md, открытый п.12,
про то, что `Tender.region_code` — регион ИНН заказчика, не место исполнения
работ; для этой задачи важно место исполнения, не регистрации).

Запуск (как и раньше, переменные окружения — на извещения, если .env ещё не
переключён на PRIZ по умолчанию для этой машины):

    cd C:\\Users\\User\\projects\\tender-ai-assistant
    .venv\\Scripts\\activate
    python scripts\\fetch_round2_tenders.py

Результат — все вложения обоих извещений (не только CAR) сохраняются в
data/round2_attachments/<purchase_number>/<file_name>, плюс краткий отчёт в
консоль: сколько вложений найдено, какие типы (docKindInfo), какие похожи на
смету/ЛСР (по расширению .xlsx/.xls/.docx — сметы обычно в них).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eis_client.client import EISClient  # noqa: E402
from eis_client.config import EISConfig  # noqa: E402
from eis_client.notice_parser import extract_attachments  # noqa: E402

TARGETS = {
    "0337100017726000160": date(2026, 9, 18),
    "0337100017726000168": date(2026, 9, 28),
}

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "round2_attachments"

SMETA_EXTENSIONS = {".xlsx", ".xls", ".docx", ".doc"}


def _find_purchase_number(xml_bytes: bytes, wanted: str) -> bool:
    # Извещение хранит номер закупки в commonInfo/purchaseNumber — простой
    # текстовый поиск достаточен для разового скрипта, не нужен полный разбор.
    return wanted.encode() in xml_bytes


def main() -> None:
    config = EISConfig.from_env()
    remaining = dict(TARGETS)

    with EISClient(config) as client:
        for purchase_number, fetch_date in TARGETS.items():
            print(f"\n=== Ищу {purchase_number} за {fetch_date} ===")
            archive_urls = client.fetch_archive_urls(fetch_date)
            print(f"Архивов за эту дату: {len(archive_urls)}")

            found_xml: bytes | None = None
            for url in archive_urls:
                archive_bytes = client.download_archive(url)
                for _name, xml_bytes in EISClient._extract_xml_files(archive_bytes):
                    if _find_purchase_number(xml_bytes, purchase_number):
                        found_xml = xml_bytes
                        break
                if found_xml is not None:
                    break

            if found_xml is None:
                print(f"НЕ НАЙДЕН в архивах за {fetch_date} — возможно, дата "
                      f"в БД не совпадает с датой публикации в архиве, "
                      f"нужно проверить соседние даты вручную.")
                continue

            attachments = extract_attachments(found_xml)
            print(f"Вложений найдено: {len(attachments)}")

            target_dir = OUTPUT_DIR / purchase_number
            target_dir.mkdir(parents=True, exist_ok=True)

            for att in attachments:
                kind = att.doc_kind_name or att.doc_kind_code or "?"
                is_smeta_like = Path(att.file_name).suffix.lower() in SMETA_EXTENSIONS
                marker = " <-- похоже на смету/ЛСР (проверить вручную)" if is_smeta_like else ""
                print(f"  - {att.file_name} [{kind}]{marker}")

                try:
                    content = client.download_attachment(att.url)
                except Exception as exc:  # noqa: BLE001 — разовый скрипт, важно не падать на одном файле
                    print(f"    ОШИБКА скачивания: {exc}")
                    continue

                out_path = target_dir / att.file_name
                out_path.write_bytes(content)

            del remaining[purchase_number]

    if remaining:
        print(f"\nНе найдено: {list(remaining.keys())} — см. сообщения выше.")
    else:
        print(f"\nГотово. Все вложения — в {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

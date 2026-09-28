"""Сквозной прогон Агента 4 на реальном файле сметы (.xlsx) — первый
собранный end-to-end инструмент, не только отдельные функции по частям.

Раньше у Агента 4 не было ни одного CLI-скрипта, принимающего файл целиком:
были только отдельные куски (`search_candidates`, `price_candidates_for_region`,
`extract_work_volume_rows` и т.д.), каждый проверялся своим тестом на
фрагментах/фикстурах. Этот скрипт впервые собирает их в одну цепочку на
РЕАЛЬНОМ файле — создан 2026-09-28 для сравнения Агент 4 vs Сметрикс на
одном и том же исходном документе.

**Нужна реальная сеть до fgiscs.minstroyrf.ru** (скачивание архива ФСНБ,
региональных цен и индексов ГОСР) — не работает из этой облачной песочницы
(проверено вживую 2026-09-28, тот же класс сетевого ограничения, что и для
ЕИС — см. CLAUDE.md, «Хостинг для Агента 1»). Запускать там же, где уже
работают `run_monitor.py`/`list_notice_attachments.py`.

**Честные ограничения, не доработано вслепую:**
- Формат таблицы — та же эвристика заголовков, что и `work_volume_extractor.
  extract_work_volume_rows()` (колонки "Наименование работ" / "Ед. изм." /
  "Количество"). Если реальная смета оформлена иначе — скрипт честно
  вернёт 0 строк, не будет угадывать.
- **Сопоставление единиц измерения не реализовано.** `work_volume`
  (в терминах `cost_estimate.py`) должен быть в единицах выбранного
  кандидата ГЭСН (например, "100 м2"), а строка сметы обычно уже даёт
  количество именно в этих единицах (так оформляют ГРАНД-Смета и подобные
  программы) — но скрипт этого не проверяет и не пересчитывает, просто
  берёт число как есть. Если единица кандидата и единица строки не
  совпадают по масштабу — итоговая сумма будет неверной, это ограничение
  скрипта, не общая методология.
- Категории материалов без выбранного продукта (`AbstractResource`,
  например «баннер»/индивидуальные материалы по счёту поставщика) и любые
  другие `unresolved_resource_codes` — считаются как 0 в позиции, честно
  показаны в выводе, не додуманы.
- НР/СП/НДС этот скрипт не считает — это отдельная надстройка, не часть
  фундамента Агента 4 (см. CLAUDE.md, таблица агентов, Агент 4/5). Итог
  скрипта сравнивайте с «Прямые затраты» из сметы заказчика, не с
  «ВСЕГО по смете».

Использование:

    python src/estimate_smeta_document.py "data/downloaded_attachments/roof/02 Обоснование НМЦК смета.xlsx" \
        --region "г. Москва" --top-n 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from document_analyst.table_reader import extract_xlsx_tables
from smeta_estimator import (
    CURRENT_PERIOD_ID,
    PILOT_PRICE_ZONES,
    apply_prices,
    download_fsnb_archive,
    extract_fsnb_files,
    extract_work_volume_rows,
    fetch_current_prices_json,
    fetch_gosr_report,
    fetch_worker_salary_registry,
    parse_current_prices_json,
    parse_fsbc_machine_labour_xml,
    parse_fsbc_machines_xml,
    parse_fsbc_materials_xml,
    parse_gesn_xml,
    parse_gosr_workbook,
    parse_worker_salary_registry,
    price_candidates_for_region,
    search_candidates,
)
from smeta_estimator.fsnb_client import (
    ATTRIBUTION_NOTICE,
    FSBC_MACHINES_FILENAME,
    FSBC_MATERIALS_FILENAME,
    GESN_FILENAME,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", help="Путь к файлу сметы (.xlsx)")
    parser.add_argument(
        "--region",
        default="г. Москва",
        choices=list(PILOT_PRICE_ZONES),
        help="Один из 4 пилотных регионов",
    )
    parser.add_argument("--period-id", type=int, default=CURRENT_PERIOD_ID)
    parser.add_argument(
        "--period-label",
        default="3 квартал 2026 г.",
        help="Только для подписи в выводе — не влияет на запрос",
    )
    parser.add_argument("--top-n", type=int, default=3, help="Кандидатов на строку")
    args = parser.parse_args()

    path = Path(args.xlsx)
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 1

    print("=== Шаг 1: извлечение строк ведомости объёмов работ из файла ===")
    tables = extract_xlsx_tables(path.read_bytes())
    rows = extract_work_volume_rows(tables)
    if not rows:
        print(
            "Не нашлось ни одной строки вида «Наименование работ | Ед. изм. | "
            "Количество» — формат этой конкретной таблицы не распознан "
            "extract_work_volume_rows(). Это честная находка (см. CLAUDE.md, "
            "открытый п.3/8 про форматы реальных документов), не ошибка скрипта."
        )
        return 1
    print(f"  Извлечено строк: {len(rows)}")

    print("\n=== Шаг 2: скачивание каталога ГЭСН/ФСБЦ (архив ФСНБ-2022) ===")
    print(f"  {ATTRIBUTION_NOTICE}")
    archive_files = extract_fsnb_files(download_fsnb_archive())
    catalog = parse_gesn_xml(archive_files[GESN_FILENAME])
    resource_base_prices = {
        **parse_fsbc_materials_xml(archive_files[FSBC_MATERIALS_FILENAME]),
        **parse_fsbc_machines_xml(archive_files[FSBC_MACHINES_FILENAME]),
    }
    apply_prices(catalog, resource_base_prices)
    machine_labour = parse_fsbc_machine_labour_xml(archive_files[FSBC_MACHINES_FILENAME])
    print(f"  Позиций в каталоге ГЭСН: {len(catalog)}")

    print(f"\n=== Шаг 3: региональные цены и индексы ГОСР — {args.region}, период {args.period_id} ===")
    zone = PILOT_PRICE_ZONES[args.region]
    price_zone_id = int(zone["price_zone_id"])
    current_prices = {
        **parse_current_prices_json(
            fetch_current_prices_json(price_zone_id, args.period_id, "materials")
        ),
        **parse_current_prices_json(
            fetch_current_prices_json(price_zone_id, args.period_id, "machines")
        ),
        **parse_worker_salary_registry(
            fetch_worker_salary_registry(price_zone_id, args.period_id)
        ),
    }
    gosr_index = parse_gosr_workbook(fetch_gosr_report(price_zone_id, args.period_id))
    print(f"  Текущих цен/ставок по кодам: {len(current_prices)}, индексов ГОСР: {len(gosr_index)}")

    print("\n=== Шаг 4: подбор кандидатов и цена по каждой строке ===")
    grand_total = 0.0
    unresolved_rows: list[str] = []

    for row in rows:
        print(f"\n--- {row.name} ({row.unit}, количество {row.quantity}) ---")
        candidates = search_candidates(catalog, row.name, top_n=args.top_n)
        if not candidates:
            print("  Кандидатов не найдено вообще — слов текста работы нет ни в одном названии ГЭСН.")
            unresolved_rows.append(row.name)
            continue

        priced = price_candidates_for_region(
            candidates,
            region_name=args.region,
            period_label=args.period_label,
            current_prices=current_prices,
            gosr_index=gosr_index,
            resource_base_prices=resource_base_prices,
            machine_labour=machine_labour,
        )
        for c in priced:
            row_total = c.priced.total_price * row.quantity
            unresolved = c.priced.unresolved_resource_codes or "нет"
            print(
                f"  {c.code} (score={c.match_score:.2f}): {c.name} [{c.unit}] — "
                f"{c.priced.total_price:,.2f} руб./ед., на объём {row.quantity} -> "
                f"{row_total:,.2f} руб. (unresolved: {unresolved})"
            )

        top = priced[0]
        if top.unit != row.unit:
            print(
                f"  ⚠ Единица кандидата ({top.unit}) не совпадает дословно с единицей "
                f"строки сметы ({row.unit}) — сумма ниже может быть в другом масштабе, "
                "сверьте вручную."
            )
        grand_total += top.priced.total_price * row.quantity

    print(f"\n=== Итого по топ-кандидатам всех строк: {grand_total:,.2f} руб. ===")
    print(
        "(без НР/СП/НДС — сравнивайте с «Прямые затраты» из сметы заказчика, "
        "не с «ВСЕГО по смете»; единицы измерения по строкам не сверялись — "
        "см. ограничения в докстринге скрипта)"
    )
    if unresolved_rows:
        print(f"\nСтроки без единого кандидата ({len(unresolved_rows)}):")
        for name in unresolved_rows:
            print(f"  - {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

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
- **Строки-материалы, не работы — с 2026-09-28 подбираются из двух
  каталогов, не только из ГЭСН/ГЭСНр.** Найдено на реальных сметах: строки
  вида «Уголок алюминиевый декоративный», «Светильник светодиодный...» —
  по сути изделие/материал, не нормируемая работа, и поиск по каталогу
  работ находил для них случайные, ничего не значащие совпадения (score
  0.07–0.29). Теперь для каждой строки пробуются оба пути —
  `search_candidates()` по ГЭСН/ГЭСНр и `search_material_candidates()` по
  каталогу материалов ФСБЦ_Мат&Оборуд — и используется тот, у которого
  выше `match_score` (`choose_candidate_source()`), не выбор каталога
  заранее по эвристике вроде единицы измерения. Порог `MATCH_SCORE_THRESHOLD`
  (0.5, подобран по фактическому распределению score на двух реальных
  сметах, не с потолка — см. докстринг `search.py`) отсекает случай, когда
  оба пути одинаково неуверенны: строка честно уходит в «кандидат не
  найден», а не подставляется как «лучшее из плохого».
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
from smeta_estimator.code_lookup import classify_unresolved_row
from smeta_estimator import (
    CURRENT_PERIOD_ID,
    MATCH_SCORE_THRESHOLD,
    PILOT_PRICE_ZONES,
    apply_prices,
    choose_candidate_source,
    download_fsnb_archive,
    extract_fsnb_files,
    extract_work_volume_rows,
    fetch_current_prices_json,
    fetch_gosr_report,
    fetch_worker_salary_registry,
    find_exact_material_candidate,
    find_exact_work_candidate,
    normalize_gesn_code,
    parse_current_prices_json,
    parse_fsbc_machine_labour_xml,
    parse_fsbc_machines_xml,
    parse_fsbc_materials_xml,
    parse_gesn_xml,
    parse_gosr_workbook,
    parse_material_catalog_xml,
    parse_worker_salary_registry,
    price_candidates_for_region,
    price_material_candidates_for_region,
    resolve_order_modifiers,
    search_candidates,
    search_material_candidates,
)
from smeta_estimator.models import MaterialRateCandidate, RateCandidate
from smeta_estimator.regional_pricing_client import (
    BENCHMARK_PRICE_ZONES,
    fetch_load_works_by_auto,
    fetch_transport_filter_values,
    fetch_transportation_by_auto,
)
from smeta_estimator.transport_pricing import SOURCE_LABEL as TRANSPORT_SOURCE_LABEL
from smeta_estimator.transport_pricing import TransportPriceBook
from smeta_estimator.fsnb_client import (
    ATTRIBUTION_NOTICE,
    FSBC_MACHINES_FILENAME,
    FSBC_MATERIALS_FILENAME,
    GESN_FILENAME,
    GESNM_FILENAME,
    GESNR_FILENAME,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", help="Путь к файлу сметы (.xlsx)")
    parser.add_argument(
        "--region",
        default="г. Москва",
        choices=[*PILOT_PRICE_ZONES, *BENCHMARK_PRICE_ZONES],
        help="Один из 4 пилотных регионов (или регион бенчмарка вне пилота, см. BENCHMARK_PRICE_ZONES)",
    )
    parser.add_argument("--period-id", type=int, default=CURRENT_PERIOD_ID)
    parser.add_argument(
        "--period-label",
        default="3 квартал 2026 г.",
        help="Только для подписи в выводе — не влияет на запрос",
    )
    parser.add_argument("--top-n", type=int, default=3, help="Кандидатов на строку")
    parser.add_argument(
        "--show-resources",
        action="store_true",
        help=(
            "Печатать разбивку по каждому ресурсу внутри топ-1 позиции ГЭСН/ГЭСНр "
            "(код/название/количество/цена/источник цены — current_price/gosr_index/"
            "unresolved) — для диагностики подозрительно больших сумм по работе, "
            "не только по материалам (см. CLAUDE.md, открытый п.17, «Находка 5»)."
        ),
    )
    parser.add_argument(
        "--force-text-search",
        action="store_true",
        help=(
            "Диагностический режим (не меняет прод-поведение без флага): точный "
            "поиск по коду из «Обоснования» пропускается ВСЕГДА, даже когда код "
            "распознан — сразу текстовый поиск. row.code при этом не участвует в "
            "поиске, только сверяется с топ-1 результатом текстового поиска "
            "(по коду, после normalize_gesn_code()) для измерения точности "
            "text search без точного кода. В конце — отдельный отчёт "
            "«% совпадений», см. CLAUDE.md."
        ),
    )
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
    # ГЭСНр — ремонтно-строительные расценки — подключён к общему каталогу
    # 2026-09-28 (см. CLAUDE.md): без него реальные позиции с приставкой
    # "р" (капремонт/текущий ремонт — основной сегмент пилота) не находились
    # вообще. Префикс кода читается из самого файла ГЭСНр.xml
    # (`apply_code_prefix=True`), не зашит текстом здесь — иначе короткие
    # коды вида "51-01-001-01" коллидировали бы с одноимёнными кодами
    # основного ГЭСН при объединении в один список.
    # ГЭСНм (монтаж оборудования) — тот же приём, 2026-10-03: префикс
    # "ГЭСНм" читается из файла, коды вида "08-03-610-01" иначе коллидируют
    # с основным ГЭСН.
    catalog = (
        parse_gesn_xml(archive_files[GESN_FILENAME])
        + parse_gesn_xml(archive_files[GESNR_FILENAME], apply_code_prefix=True)
        + parse_gesn_xml(archive_files[GESNM_FILENAME], apply_code_prefix=True)
    )
    resource_base_prices = {
        **parse_fsbc_materials_xml(archive_files[FSBC_MATERIALS_FILENAME]),
        **parse_fsbc_machines_xml(archive_files[FSBC_MACHINES_FILENAME]),
    }
    apply_prices(catalog, resource_base_prices)
    machine_labour = parse_fsbc_machine_labour_xml(archive_files[FSBC_MACHINES_FILENAME])
    # Каталог материалов ФСБЦ — для строк ведомости, которые по сути
    # материал/изделие, не нормируемая работа (см. докстринг выше).
    material_catalog = parse_material_catalog_xml(archive_files[FSBC_MATERIALS_FILENAME])
    print(f"  Позиций в каталоге ГЭСН+ГЭСНр+ГЭСНм: {len(catalog)}, материалов ФСБЦ: {len(material_catalog)}")

    print(f"\n=== Шаг 3: региональные цены и индексы ГОСР — {args.region}, период {args.period_id} ===")
    zone = {**PILOT_PRICE_ZONES, **BENCHMARK_PRICE_ZONES}[args.region]
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
    print(f"  (порог уверенности: {MATCH_SCORE_THRESHOLD:.2f} — см. докстринг search.py)")
    grand_total = 0.0
    unresolved_rows: list[str] = []
    unmatched_rows: list[str] = []
    material_row_count = 0

    exact_code_row_count = 0

    # Транспорт и погрузочно-разгрузочные работы — отдельный справочник
    # ФГИС ЦС (не ГЭСН/ФСБЦ), сопоставление по описанию строки, см.
    # transport_pricing.py. Данные подгружаются лениво, только если в смете
    # встретилась такая строка.
    transport_book = TransportPriceBook(
        source=f"{TRANSPORT_SOURCE_LABEL}, {args.region}, {args.period_label}",
        fetch_load_works=lambda: fetch_load_works_by_auto(price_zone_id, args.period_id),
        fetch_filter_values=lambda level, **sel: fetch_transport_filter_values(
            price_zone_id, args.period_id, level, **sel
        ),
        fetch_transportation=lambda road, vehicle, capacity: fetch_transportation_by_auto(
            price_zone_id, args.period_id, road, vehicle, capacity
        ),
    )
    transport_row_count = 0

    # Диагностика точности текстового поиска без точного кода — только под
    # --force-text-search, не влияет на прод-путь без флага (см. докстринг
    # аргумента выше и CLAUDE.md).
    diag_known_code_rows = 0
    diag_text_search_correct = 0

    for row in rows:
        print(f"\n--- {row.name} ({row.unit}, количество {row.quantity}) ---")

        # Прямой поиск по коду из «Обоснования» — приоритетный путь перед
        # текстовым поиском, см. code_lookup.py и CLAUDE.md, открытый п.17
        # («Следующий шаг круга 2», пункт «а»). Строка без распознанного
        # кода (row.code is None) или код, не нашедшийся ни в одном из двух
        # каталогов, — ведут себя ровно как раньше, без изменений.
        source: str | None = None
        work_candidates: list[RateCandidate] = []
        material_candidates: list[MaterialRateCandidate] = []

        if row.code and not args.force_text_search:
            exact_work = find_exact_work_candidate(catalog, row.code)
            if exact_work is not None:
                work_candidates = [
                    RateCandidate(
                        code=exact_work.code,
                        name=exact_work.name,
                        unit=exact_work.unit,
                        base_price=exact_work.base_price,
                        match_score=1.0,
                        resources=list(exact_work.resources),
                        unpriced_resource_codes=list(exact_work.unpriced_resource_codes),
                        abstract_resource_codes=list(exact_work.abstract_resource_codes),
                        match_method="exact_code",
                    )
                ]
                source = "work"
            else:
                exact_material = find_exact_material_candidate(material_catalog, row.code)
                if exact_material is not None:
                    material_candidates = [
                        MaterialRateCandidate(
                            code=exact_material.code,
                            name=exact_material.name,
                            unit=exact_material.unit,
                            match_score=1.0,
                            base_price_2022=exact_material.price,
                            match_method="exact_code",
                        )
                    ]
                    source = "material"

            if source is not None:
                exact_code_row_count += 1
                print(f"  Код из «Обоснования» («{row.code}») найден точно в каталоге — текстовый поиск пропущен.")

        if source is None:
            transport = transport_book.match(row.name, row.code)
            if transport is not None:
                if transport.unit_price is None:
                    alt = f" Варианты: {'; '.join(transport.alternatives)}." if transport.alternatives else ""
                    print(f"  Транспорт/ПРР: не сопоставлено — {transport.failure_reason}.{alt}")
                    unresolved_rows.append(f"{row.name} [транспорт/ПРР: {transport.failure_reason}]")
                    continue
                transport_row_count += 1
                code_note = {
                    True: f"совпадает с кодом документа «{normalize_gesn_code(row.code or '')}»",
                    False: f"НЕ совпадает с кодом документа «{normalize_gesn_code(row.code or '')}» — сверьте вручную",
                    None: "в документе кода нет",
                }[transport.document_code_agrees]
                print(
                    f"  Транспорт/ПРР по описанию: {transport.official_code} «{transport.official_description}» — "
                    f"{transport.unit_price:,.2f} руб./т, на объём {row.quantity} -> "
                    f"{transport.unit_price * row.quantity:,.2f} руб."
                )
                print(f"    Источник: {transport.source}. Код {code_note}.")
                if "т" not in row.unit.lower():
                    print(
                        f"  ⚠ Цена справочника — за 1 т груза, а единица строки «{row.unit}» — "
                        "сумма может быть в другом масштабе, сверьте вручную."
                    )
                grand_total += transport.unit_price * row.quantity
                continue

        if source is None:
            work_candidates = search_candidates(catalog, row.name, top_n=args.top_n)
            material_candidates = search_material_candidates(material_catalog, row.name, top_n=args.top_n)
            source = choose_candidate_source(work_candidates, material_candidates)

        # Диагностика --force-text-search: row.code — эталон (известный
        # правильный код из «Обоснования»), НЕ используется для поиска в
        # этом режиме (см. строку с `not args.force_text_search` выше) —
        # только для сверки с тем, что реально нашёл текстовый поиск.
        if args.force_text_search and row.code:
            expected_code = normalize_gesn_code(row.code)
            if expected_code:
                diag_known_code_rows += 1
                top1_code = None
                if source == "work" and work_candidates:
                    top1_code = work_candidates[0].code
                elif source == "material" and material_candidates:
                    top1_code = material_candidates[0].code
                guessed = top1_code is not None and normalize_gesn_code(top1_code) == expected_code
                if guessed:
                    diag_text_search_correct += 1
                print(
                    f"  [диагностика] эталон «{expected_code}», топ-1 текстового поиска "
                    f"«{top1_code or '—'}» — {'СОВПАЛО' if guessed else 'не совпало'}"
                )

        if source is None:
            known_class = classify_unresolved_row(row.code, row.name)
            if known_class is not None:
                print(f"  Кандидатов не найдено — известный класс: {known_class}.")
                unresolved_rows.append(f"{row.name} [{known_class}]")
                continue
            best = max(
                (work_candidates[0].match_score if work_candidates else 0.0),
                (material_candidates[0].match_score if material_candidates else 0.0),
            )
            reason = (
                "слов текста работы нет ни в одном из двух каталогов"
                if not work_candidates and not material_candidates
                else f"лучший score ({best:.2f}) ниже порога уверенности {MATCH_SCORE_THRESHOLD:.2f}"
            )
            print(f"  Кандидатов не найдено вообще — {reason}.")
            unresolved_rows.append(row.name)
            continue

        # Модификатор условий из ссылки на пункт приказа в «Обосновании»
        # (найдено на реальном документе "потолок", 2026-09-30, см.
        # order_modifiers.py и CLAUDE.md открытый п.19) — только для позиций
        # с полным разбором по ресурсам ГЭСН ("work"), у материалов нет
        # ресурсной структуры, к которой можно применить ОЗП/ЭМ/ЗПМ/МАТ.
        resolved_modifiers = resolve_order_modifiers(row.modifier_references)
        if resolved_modifiers.unmatched_references:
            unmatched_rows.append(row.name)
            print(
                "  ⚠ Ссылка на пункт приказа в «Обосновании» "
                f"({', '.join(resolved_modifiers.unmatched_references)}) не найдена в справочнике "
                "модификаторов (order_modifiers.ORDER_MODIFIER_REGISTRY) — цена ниже БЕЗ модификатора, "
                "source=\"unmatched_modifier_reference\", нужна проверка эксперта."
            )
        if source == "work" and resolved_modifiers.combined is not None:
            m = resolved_modifiers.combined
            print(
                f"  Найден модификатор условий из «Обоснования» ({', '.join(resolved_modifiers.matched_references)}): "
                f"ОЗП×{m.ozp:g}, ЭМ×{m.em:g}, ЗПМ×{m.zpm:g}, МАТ×{m.mat:g}."
            )

        # Ресурсы внутри позиции, явно обнулённые в самом документе (замена
        # ресурса отдельной строкой — см. WorkVolumeRow.zeroed_resource_codes
        # и CLAUDE.md, открытый п.17) — не считаем их по норме каталога,
        # иначе материал учитывается дважды (норма + строка-замена).
        zeroed_codes = frozenset(
            code for code in (normalize_gesn_code(c) for c in row.zeroed_resource_codes) if code
        )
        if source == "work" and zeroed_codes:
            print(
                "  Ресурсы, обнулённые в документе внутри этой позиции (не считаются по норме): "
                f"{', '.join(sorted(zeroed_codes))}."
            )

        if source == "work":
            priced = price_candidates_for_region(
                work_candidates,
                region_name=args.region,
                period_label=args.period_label,
                current_prices=current_prices,
                gosr_index=gosr_index,
                resource_base_prices=resource_base_prices,
                machine_labour=machine_labour,
                modifier=resolved_modifiers.combined,
                zeroed_resource_codes=zeroed_codes,
            )
            for c in priced:
                row_total = c.priced.total_price * row.quantity
                unresolved = c.priced.unresolved_resource_codes or "нет"
                label = (
                    "точный код из документа"
                    if c.match_method == "exact_code"
                    else f"score={c.match_score:.2f}, каталог работ"
                )
                print(
                    f"  {c.code} ({label}): {c.name} [{c.unit}] — "
                    f"{c.priced.total_price:,.2f} руб./ед., на объём {row.quantity} -> "
                    f"{row_total:,.2f} руб. (unresolved: {unresolved})"
                )
            top = priced[0]
            top_price = top.priced.total_price
            if args.show_resources:
                print(f"    Разбивка по ресурсам топ-1 ({top.code}):")
                for res in top.priced.resolutions:
                    if res.source == "zeroed_in_document":
                        print(f"      {res.resource_code} «{res.resource_name}» — обнулён в документе (zeroed_in_document), 0 руб.")
                        continue
                    if res.source == "unresolved":
                        print(f"      {res.resource_code} «{res.resource_name}» x{res.quantity} — не определена (unresolved)")
                        continue
                    extra = f", +труд машиниста {res.machinist_wage_added:,.2f}" if res.machinist_wage_added else ""
                    idx = f", индекс {res.index_value} ({res.group_name})" if res.source == "gosr_index" else ""
                    # res.line_total — цена ресурса НА ЕДИНИЦУ самой позиции
                    # ГЭСН/ГЭСНр (например, на 100 м3), а не на фактический
                    # объём этой строки сметы (row.quantity). Раньше здесь
                    # печаталась только эта немасштабированная величина — на
                    # круге 2 (2026-09-29) это привело к ложному выводу, что
                    # щебень (код 02.2.05.04-2088, Краснодар) переоценен в
                    # ~10 раз: 164 568,73 руб. оказались ценой на 100 м3
                    # позиции, а не на фактические 9,94 м3 (row.quantity
                    # 0.0994) — реальный вклад в строку ближе к 16 355 руб.,
                    # что почти совпадает с реальной сметой (16 121,03 руб.,
                    # см. CLAUDE.md, открытый п.17, «Находка 6»). Теперь
                    # печатается явно и отмасштабированная величина тоже —
                    # чтобы не повторить ту же ошибку чтения диагностики.
                    scaled = None if res.line_total is None else res.line_total * row.quantity
                    scaled_note = (
                        f", на объём строки {row.quantity} -> {scaled:,.2f} руб."
                        if scaled is not None
                        else ""
                    )
                    print(
                        f"      {res.resource_code} «{res.resource_name}» x{res.quantity} — "
                        f"{res.unit_price:,.2f} руб./ед. ({res.source}{idx}{extra}) -> "
                        f"{res.line_total:,.2f} руб. на единицу позиции{scaled_note}"
                    )
        else:
            material_row_count += 1
            priced_materials = price_material_candidates_for_region(
                material_candidates, current_prices=current_prices, gosr_index=gosr_index
            )
            for c in priced_materials:
                label = (
                    "точный код из документа"
                    if c.match_method == "exact_code"
                    else f"score={c.match_score:.2f}, каталог материалов"
                )
                if c.unit_price is None:
                    print(
                        f"  {c.code} ({label}): {c.name} "
                        f"[{c.unit}] — цена не определена (unresolved)"
                    )
                    continue
                row_total = c.unit_price * row.quantity
                idx_note = (
                    f", индекс {c.index_value} ({c.group_name}), базисная цена 2022: "
                    f"{c.base_price_2022:,.2f} руб./ед."
                    if c.price_source == "gosr_index"
                    else ""
                )
                print(
                    f"  {c.code} ({label}): {c.name} "
                    f"[{c.unit}] — {c.unit_price:,.2f} руб./ед. ({c.price_source}{idx_note}), на объём "
                    f"{row.quantity} -> {row_total:,.2f} руб."
                )
            top = priced_materials[0]
            top_price = top.unit_price or 0.0

        if top.unit != row.unit:
            print(
                f"  ⚠ Единица кандидата ({top.unit}) не совпадает дословно с единицей "
                f"строки сметы ({row.unit}) — сумма ниже может быть в другом масштабе, "
                "сверьте вручную."
            )
        grand_total += top_price * row.quantity

    print(f"\n=== Итого по топ-кандидатам всех строк: {grand_total:,.2f} руб. ===")
    print(
        "(без НР/СП/НДС — сравнивайте с «Прямые затраты» из сметы заказчика, "
        "не с «ВСЕГО по смете»; единицы измерения по строкам не сверялись — "
        "см. ограничения в докстринге скрипта)"
    )
    print(f"Строк, для которых выбран каталог материалов (не работ): {material_row_count}")
    print(f"Строк, найденных точным кодом из «Обоснования» (без текстового поиска): {exact_code_row_count}")
    print(f"Строк транспорта/ПРР, оценённых по справочнику ФГИС ЦС (по описанию): {transport_row_count}")

    if args.force_text_search:
        pct = (
            100.0 * diag_text_search_correct / diag_known_code_rows
            if diag_known_code_rows
            else 0.0
        )
        print("\n=== Диагностика --force-text-search: точность текстового поиска без точного кода ===")
        print(f"Строк с известным эталонным кодом («Обоснование»): {diag_known_code_rows}")
        print(f"Текстовый поиск угадал топ-1: {diag_text_search_correct}")
        print(f"Точность: {pct:.1f}%")

    if unresolved_rows:
        print(f"\nСтроки без единого кандидата ({len(unresolved_rows)}):")
        for name in unresolved_rows:
            print(f"  - {name}")

    if unmatched_rows:
        print(
            f"\nСтроки со ссылкой на пункт приказа без справочника модификаторов ({len(unmatched_rows)}) "
            "— цена посчитана БЕЗ модификатора, нужна проверка эксперта (см. order_modifiers.py):"
        )
        for name in unmatched_rows:
            print(f"  - {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

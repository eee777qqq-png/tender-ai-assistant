"""Прогон оркестратора (Агент 13) на реальных данных пилота: реальный профиль
клиента (`real_client_profile.py`), реальные извещения из локальных архивов
ЕИС, реальная документация закупки (`fetch_participant_requirements()`).

Что делает:
1. Для каждого строительного извещения в архивах строит `Tender` и
   прогоняет `run_tender_pipeline()` — печатает, на каком шаге и по какому
   правилу остановилась цепочка. Без решений эксперта прогон по протоколу
   не может дойти до клиента — это и проверяется.
2. `--demo-purchase НОМЕР` — пошаговая демонстрация стоп-условий на одной
   реальной закупке: каждое «решение эксперта» здесь ИМИТИРУЕТСЯ в памяти
   (помечено «ДЕМО») только чтобы увидеть следующую остановку. Реальный
   профиль на диске не меняется, подтверждения пишутся во временную базу,
   не в `data/expert_reviews.sqlite3`.

Запуск (из корня репозитория):
    python src/run_orchestrator.py --archive-dir data/raw_notices --limit 10
    python src/run_orchestrator.py --archive-dir data/raw_notices --demo-purchase 0373200032226000750
"""

from __future__ import annotations

import argparse
import collections
import copy
import glob
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from classifier import ConstructionClassifier
from eis_client import EISClient, EISConfig, fetch_participant_requirements, notice_document_to_tender
from eis_client.exceptions import AttachmentParseError, EISError
from onboarding.models import ProfileStatus
from orchestrator import PipelineInputs, RunOutcome, record_expert_signoff, run_tender_pipeline
from quality_control.expert_review import ExpertReviewStore

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _demo_smeta_items(purchase_number: str):
    """Позиция сметы для демонстрации R2 — каталог и цены из реальных
    фрагментов ФГИС ЦС по Москве (tests/fixtures, те же, что в сквозных
    тестах), подбор под текст работы; НЕ подтверждена экспертом."""
    from smeta_estimator import (
        SmetaLineItem,
        apply_prices,
        match_work_item,
        parse_current_prices_json,
        parse_fsbc_machine_labour_xml,
        parse_fsbc_machines_xml,
        parse_fsbc_materials_xml,
        parse_gesn_xml,
        parse_gosr_workbook,
        parse_worker_salary_registry,
        price_candidates_for_region,
    )

    catalog = parse_gesn_xml((FIXTURES / "gesn_roof_sample.xml").read_bytes())
    base = {
        **parse_fsbc_materials_xml((FIXTURES / "fsbc_materials_sample.xml").read_bytes()),
        **parse_fsbc_machines_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    }
    apply_prices(catalog, base)
    match = match_work_item(catalog, "устройство кровли на битумной мастике с защитным слоем из гравия", purchase_number)
    match.candidates = price_candidates_for_region(
        match.candidates,
        region_name="г. Москва",
        period_label="3 квартал 2026 г.",
        current_prices={
            **parse_current_prices_json((FIXTURES / "current_prices_moscow_machines_sample.json").read_bytes()),
            **parse_worker_salary_registry((FIXTURES / "worker_salary_moscow_sample.json").read_bytes()),
        },
        gosr_index=parse_gosr_workbook((FIXTURES / "gosr_moscow_q3_2026_sample.xlsx").read_bytes()),
        resource_base_prices=base,
        machine_labour=parse_fsbc_machine_labour_xml((FIXTURES / "fsbc_machines_sample.xml").read_bytes()),
    )
    return [SmetaLineItem(match_result=match, work_volume=1.0)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--limit", type=int, default=10, help="Сколько закупок, прошедших грубый фильтр, гонять целиком")
    parser.add_argument("--demo-purchase", help="Пошаговая демонстрация стоп-условий на одной закупке")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    from real_client_profile import get_real_client_profile

    profile = get_real_client_profile()
    print(f"Реальный профиль: client_id={profile.client_id}, статус={profile.status.value}")
    classifier = ConstructionClassifier()
    config = EISConfig.from_env()
    outcomes: collections.Counter = collections.Counter()

    with EISClient(config, construction_classifier=classifier) as client:
        archives = sorted(Path(p) for p in glob.glob(str(Path(args.archive_dir) / "*.zip")))
        documents = client.get_construction_documents_from_local_archives(archives)
        print(f"Строительных документов в архивах: {len(documents)}")
        fetched = 0
        seen: set[str] = set()
        for document in documents:
            if document.raw_xml is None:
                continue
            try:
                tender = notice_document_to_tender(document.raw_xml, requires_sro=False, min_experience_years=0)
            except ValueError:
                continue
            if tender.purchase_number in seen:
                continue
            seen.add(tender.purchase_number)
            if args.demo_purchase and tender.purchase_number != args.demo_purchase:
                continue

            # Первый прогон без документации — дешёвый: если грубый фильтр
            # отсеял, документацию не качаем (как и в match_real_notices.py).
            probe = run_tender_pipeline(PipelineInputs(profile=profile, tender=tender), classifier)
            if probe.outcome == RunOutcome.NOT_A_FIT and probe.steps[-1].step == "agent_2_coarse":
                outcomes["NOT_A_FIT на проходе 1"] += 1
                continue
            if not args.demo_purchase and fetched >= args.limit:
                continue
            fetched += 1
            try:
                extracted = fetch_participant_requirements(client, document.raw_xml, tender.purchase_number)
            except (EISError, AttachmentParseError) as exc:
                print(f"\n{tender.purchase_number}: документация не скачалась ({exc}) — прогон без неё")
                extracted = None

            run = run_tender_pipeline(PipelineInputs(profile=profile, tender=tender, extracted_requirements=extracted), classifier)
            key = run.outcome.value + (f" / {run.stop.rule.rule_id}" if run.stop else "")
            outcomes[key] += 1
            print(f"\n{'=' * 70}\n{tender.name[:90]}")
            print(run.render_log())

            if args.demo_purchase:
                _demo(profile, tender, extracted, classifier)
                return 0

    print("\n=== Итог по исходам ===")
    for k, v in outcomes.most_common():
        print(f"  {v:4}  {k}")
    return 0


def _demo(profile, tender, extracted, classifier) -> None:
    print("\n" + "#" * 70 + "\nДЕМО: пошаговое прохождение стоп-условий (решения эксперта ИМИТИРУЮТСЯ)\n" + "#" * 70)
    tmp = tempfile.mkdtemp()
    store = ExpertReviewStore(Path(tmp) / "demo_reviews.sqlite3")
    if extracted is None:
        print("Документации нет — демонстрация дальше R8 невозможна.")
        return
    extracted = copy.deepcopy(extracted)
    demo_profile = copy.deepcopy(profile)
    smeta = _demo_smeta_items(tender.purchase_number)

    def again(label: str):
        r = run_tender_pipeline(
            PipelineInputs(
                profile=demo_profile,
                tender=tender,
                extracted_requirements=extracted,
                smeta_line_items=smeta,
                smeta_region_name="г. Москва",
                smeta_period_label="3 квартал 2026 г.",
                smeta_as_of_date=__import__("datetime").date.today(),
                pricing_metadata=PricingMetadata(
                    catalog_source="ДЕМО (тестовые фрагменты tests/fixtures)",
                    catalog_files=[],
                    catalog_version_date="демо",
                    catalog_archive_url="демо",
                    region="г. Москва",
                    region_index_period="3 квартал 2026 г.",
                    wage_act="демо",
                    fgiscs_price_fetched_at="демо",
                    price_zone_id=0,
                    period_id=0,
                ),
                signoff_store=store,
            ),
            classifier,
        )
        print(f"\n--- {label} ---\n{r.render_log()}")
        return r

    from quality_control import AuditReadinessTracker, CategorizedDiscrepancyLog
    from smeta_estimator.pricing_metadata import PricingMetadata
    from smeta_estimator import review_match_result

    r = again("Старт: без единого решения эксперта")
    for step in range(1, 10):
        if r.stop is None:
            break
        rule = r.stop.rule.rule_id
        if rule == "R7_CLASSIFIER_MANUAL_CHECK":
            record_expert_signoff(store, r.stop.signoff_key, reviewer="ДЕМО-эксперт", approved=True)
            label = "ДЕМО — ручная проверка вердикта Агента 2 «разрешена»"
        elif rule == "R1_AGENT3_EXPERT_REVIEW":
            extracted.mark_expert_reviewed(reviewer="ДЕМО-эксперт")
            record_expert_signoff(store, r.stop.signoff_key, reviewer="ДЕМО-эксперт", approved=True)
            label = "ДЕМО — требования Агента 3 «подтверждены»"
        elif rule == "R4_PROFILE_NOT_READY":
            print(f"(реальный профиль в статусе {profile.status.value} — блок перед Агентом 6 сработал на реальных данных)")
            demo_profile.status = ProfileStatus.READY
            label = "ДЕМО — копия профиля временно переведена в READY (реальный профиль на диске не тронут)"
        elif rule == "R2_AGENT4_EXPERT_REVIEW":
            m = smeta[0].match_result
            review_match_result(
                m,
                reviewer="ДЕМО-эксперт",
                selected_code=m.top_candidate().code,
                tracker=AuditReadinessTracker("agent_4_smeta_estimator"),
                discrepancy_log=CategorizedDiscrepancyLog(),
            )
            label = "ДЕМО — позиция сметы «подтверждена»"
        elif rule == "R3_AGENT6_EXPERT_SIGNOFF":
            record_expert_signoff(store, r.stop.signoff_key, reviewer="ДЕМО-эксперт", approved=True)
            label = "ДЕМО — пакет Агента 6 «подтверждён»"
        else:
            print(f"Правило {rule} демонстрация не имитирует — остановка.")
            break
        r = again(f"Шаг {step}: {label}")
    print(f"\nИтоговый исход демонстрации: {r.outcome.value}")


if __name__ == "__main__":
    sys.exit(main())

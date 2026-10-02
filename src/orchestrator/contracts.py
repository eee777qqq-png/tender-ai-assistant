"""Контракты данных между агентами — проверки поверх УЖЕ существующих
dataclass-моделей агентов (`classifier.tender.Tender`,
`document_analyst.models.ExtractedRequirements`, `smeta_estimator.cost_estimate.
SmetaLineItem`, `document_assembler.models.DocumentPackage`, ...), без
второго стандарта схем (не pydantic, не новые модели).

Каждая функция возвращает список нарушений (пустой — контракт выполнен).
Роутер на непустом списке останавливает цепочку по `R6_CONTRACT_VIOLATION`
и передаёт список в причину остановки."""

from __future__ import annotations

from classifier.matching import MatchResult as ClassifierMatchResult
from classifier.tender import Tender
from completeness_check.models import CompletenessResult
from document_analyst.models import ExtractedRequirements
from document_assembler.models import DocumentPackage
from onboarding.models import ClientProfile
from smeta_estimator.cost_estimate import SmetaCostResult, SmetaLineItem


def check_tender(tender: object) -> list[str]:
    """Агент 1 -> Агент 2: построенный из извещения `Tender`."""
    if not isinstance(tender, Tender):
        return [f"ожидался classifier.tender.Tender, получен {type(tender).__name__}"]
    problems = []
    for name in ("purchase_number", "name", "customer_name", "okpd2_code", "region_code"):
        if not str(getattr(tender, name) or "").strip():
            problems.append(f"Tender.{name} пустой")
    if not tender.max_price or tender.max_price <= 0:
        problems.append(f"Tender.max_price должен быть > 0, сейчас {tender.max_price!r}")
    if tender.submission_deadline and tender.publish_date and tender.submission_deadline < tender.publish_date:
        problems.append("Tender.submission_deadline раньше publish_date")
    return problems


def check_classifier_result(result: object, tender: Tender, profile: ClientProfile) -> list[str]:
    """Агент 2 -> следующий шаг: вердикт относится к этой закупке/клиенту."""
    if not isinstance(result, ClassifierMatchResult):
        return [f"ожидался classifier.MatchResult, получен {type(result).__name__}"]
    problems = []
    if result.tender.purchase_number != tender.purchase_number:
        problems.append(f"вердикт Агента 2 для закупки {result.tender.purchase_number!r}, а не {tender.purchase_number!r}")
    if result.client_id != profile.client_id:
        problems.append(f"вердикт Агента 2 для клиента {result.client_id!r}, а не {profile.client_id!r}")
    if not result.criteria:
        problems.append("вердикт Агента 2 без единого критерия")
    return problems


def check_extracted_requirements(extracted: object, tender: Tender) -> list[str]:
    """Агент 3 -> Агенты 2 (final)/6/5/8."""
    if not isinstance(extracted, ExtractedRequirements):
        return [f"ожидался ExtractedRequirements, получен {type(extracted).__name__}"]
    if extracted.tender_purchase_number != tender.purchase_number:
        return [
            f"требования Агента 3 для закупки {extracted.tender_purchase_number!r}, а не {tender.purchase_number!r}"
        ]
    return []


def check_smeta_line_items(items: object, tender: Tender) -> list[str]:
    """Вход Агента 4 (позиции сметы с объёмами) -> сведение в себестоимость."""
    if not isinstance(items, list) or not items:
        return ["список позиций сметы пуст или не список"]
    problems = []
    for i, item in enumerate(items):
        if not isinstance(item, SmetaLineItem):
            problems.append(f"позиция {i}: ожидался SmetaLineItem, получен {type(item).__name__}")
            continue
        mr = item.match_result
        if mr.tender_purchase_number != tender.purchase_number:
            problems.append(
                f"позиция «{mr.query_text}» подобрана для закупки {mr.tender_purchase_number!r}, "
                f"а не {tender.purchase_number!r}"
            )
        if item.work_volume is None or item.work_volume == 0:
            problems.append(f"позиция «{mr.query_text}»: объём работ не задан (0/None)")
    return problems


def check_smeta_result(result: object) -> list[str]:
    """Агент 4 -> Агент 5."""
    if not isinstance(result, SmetaCostResult):
        return [f"ожидался SmetaCostResult, получен {type(result).__name__}"]
    if result.priced_line_items == 0:
        return ["ни одна позиция сметы не получила цену — себестоимость не посчитана"]
    return []


def check_package(package: object, tender: Tender, profile: ClientProfile) -> list[str]:
    """Агент 6 -> Агент 7."""
    if not isinstance(package, DocumentPackage):
        return [f"ожидался DocumentPackage, получен {type(package).__name__}"]
    problems = []
    if package.tender_purchase_number != tender.purchase_number:
        problems.append(f"пакет для закупки {package.tender_purchase_number!r}, а не {tender.purchase_number!r}")
    if package.client_id != profile.client_id:
        problems.append(f"пакет для клиента {package.client_id!r}, а не {profile.client_id!r}")
    if not package.fields:
        problems.append("пакет без единого поля")
    return problems


def check_completeness_result(result: object, package: DocumentPackage) -> list[str]:
    """Агент 7 -> Агент 8."""
    if not isinstance(result, CompletenessResult):
        return [f"ожидался CompletenessResult, получен {type(result).__name__}"]
    problems = []
    if result.tender_purchase_number != package.tender_purchase_number:
        problems.append("вердикт комплектности относится к другой закупке, чем пакет")
    if result.client_id != package.client_id:
        problems.append("вердикт комплектности относится к другому клиенту, чем пакет")
    return problems

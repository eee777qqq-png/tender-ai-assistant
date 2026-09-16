"""Проверка комплектности пакета документов (Агент 7).

Каркас: не пытается заново решать, какие поля нужны для закупки (это уже
сделал Агент 6 — каждое поле пакета несёт `required`, вычисленный из данных
закупки), а формально сверяет пакет и закупку и выносит вердикт. Данные
закупки (`Tender`) — вход не для пересчёта обязательности, а для сверки, что
пакет действительно относится к этой закупке, и для контекста в результате.

`check_completeness()` — единственная точка входа.
"""

from __future__ import annotations

from classifier.tender import Tender
from document_assembler import DocumentPackage

from .models import CompletenessResult, CompletenessStatus


def check_completeness(package: DocumentPackage, tender: Tender) -> CompletenessResult:
    if package.tender_purchase_number != tender.purchase_number:
        raise ValueError(
            "Пакет документов относится к закупке "
            f"{package.tender_purchase_number!r}, а передана закупка {tender.purchase_number!r}"
        )

    missing_required = [f.name for f in package.fields if f.status == "missing" and f.required]
    missing_optional = [
        f.name for f in package.fields if f.status != "ready" and not f.required
    ]

    status = CompletenessStatus.FAIL if missing_required else CompletenessStatus.PASS_

    return CompletenessResult(
        client_id=package.client_id,
        tender_purchase_number=package.tender_purchase_number,
        status=status,
        missing_required_fields=missing_required,
        missing_optional_fields=missing_optional,
    )

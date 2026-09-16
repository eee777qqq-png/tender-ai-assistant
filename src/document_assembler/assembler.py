"""Сборка заготовки пакета документов (Агент 6).

`assemble_document_package()` — единственная точка входа. По протоколу
контроля качества (CLAUDE.md) профиль клиента должен быть проверен на
полноту и достоверность до того, как им пользуется Агент 6 — это
`ClientProfile.is_ready_for_agent_6()` (статус READY, выставляется только
человеком через `submit_expert_review()`/`mark_ready()`, см.
`onboarding.models`). Если профиль не READY — явная ошибка, никакого
тихого пропуска шага.
"""

from __future__ import annotations

from classifier.tender import Tender
from onboarding.models import ClientProfile

from .models import DocumentPackage, PackageField


def assemble_document_package(profile: ClientProfile, tender: Tender) -> DocumentPackage:
    if not profile.is_ready_for_agent_6():
        raise ValueError(
            "Профиль клиента должен быть в статусе READY для Агента 6, "
            f"сейчас: {profile.status} (client_id={profile.client_id})"
        )

    fields: list[PackageField] = []

    def add(name: str, source: str, value: object, required: bool = True) -> None:
        has_value = bool(value) or value == 0
        if not required and not has_value:
            status = "not_applicable"
        elif has_value:
            status = "ready"
        else:
            status = "missing"
        fields.append(
            PackageField(
                name=name, source=source, value=str(value), status=status, required=required
            )
        )

    # Данные закупки — из Tender (Агент 2), не из профиля.
    add("Номер закупки", "tender.purchase_number", tender.purchase_number)
    add("Наименование закупки", "tender.name", tender.name)
    add("Заказчик", "tender.customer_name", tender.customer_name)
    add("НМЦК", "tender.max_price", tender.max_price)

    # Блок 1: юридические данные.
    add("Наименование организации", "legal.org_name", profile.legal.org_name)
    add("ИНН", "legal.inn", profile.legal.inn)
    add("ОГРН/ОГРНИП", "legal.ogrn", profile.legal.ogrn)
    add("Юридический адрес", "legal.legal_address", profile.legal.legal_address)
    add("Контактное лицо", "legal.contact_person", profile.legal.contact_person)
    add("Телефон", "legal.phone", profile.legal.phone)
    add("Email", "legal.email", profile.legal.email)

    # Блок 2: допуски и опыт — членство в СРО нужно, только если закупка его требует.
    add(
        "Членство в СРО (номер)",
        "permits_experience.sro_number",
        profile.permits_experience.sro_number,
        required=tender.requires_sro,
    )
    add(
        "Опыт выполнения аналогичных работ (лет)",
        "permits_experience.years_of_experience",
        profile.permits_experience.years_of_experience,
        required=tender.min_experience_years > 0,
    )
    add(
        "Справки об исполненных контрактах",
        "permits_experience.completed_contracts",
        len(profile.permits_experience.completed_contracts),
        required=tender.min_experience_years > 0,
    )

    # Блок 3: мощности.
    add("Численность персонала", "capacity.staff_count", profile.capacity.staff_count)
    add(
        "Описание собственных трудовых ресурсов",
        "capacity.own_workforce_description",
        profile.capacity.own_workforce_description,
    )

    # Блок 4: финансовая готовность.
    add("Система налогообложения", "financial.tax_system", profile.financial.tax_system)
    add(
        "Среднегодовая выручка",
        "financial.avg_annual_revenue",
        profile.financial.avg_annual_revenue,
    )
    add(
        "Обеспечение заявки (банковская гарантия)",
        "financial.bank_guarantee_available",
        profile.financial.bank_guarantee_available,
    )

    return DocumentPackage(
        client_id=profile.client_id, tender_purchase_number=tender.purchase_number, fields=fields
    )

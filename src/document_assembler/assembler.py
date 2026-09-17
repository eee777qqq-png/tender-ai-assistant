"""Сборка заготовки пакета документов (Агент 6).

`assemble_document_package()` — единственная точка входа. По протоколу
контроля качества (CLAUDE.md) профиль клиента должен быть проверен на
полноту и достоверность до того, как им пользуется Агент 6 — это
`ClientProfile.is_ready_for_agent_6()` (статус READY, выставляется только
человеком через `submit_expert_review()`/`mark_ready()`, см.
`onboarding.models`). Если профиль не READY — явная ошибка, никакого
тихого пропуска шага.

`extracted_requirements` (результат Агента 3, см. `document_analyst`) —
необязательный дополнительный вход. Когда он передан, извлечённые из текста
документации требования дополняют то, что уже известно из структурированных
полей `Tender`: находка Агентом 3 требования к обеспечению контракта (его
`Tender` вообще не хранит) или к СРО/опыту делает соответствующее поле
пакета обязательным, даже если структурированные данные закупки этого не
показывают. Скрытые риски прокидываются в `DocumentPackage.hidden_risks` —
информационно, для будущего Агента 8, не влияют на комплектность.

Ровно так же, как профиль обязан быть READY, `extracted_requirements`
обязан быть проверен экспертом (`expert_reviewed`) — подключение Агента 3
к Агенту 6 соединяет данные технически и не подменяет и не автоматизирует
эту проверку.
"""

from __future__ import annotations

from classifier.tender import Tender
from document_analyst.models import ExtractedRequirements
from onboarding.models import ClientProfile
from onboarding.tax_config import label_for as tax_regime_label

from .models import DocumentPackage, PackageField


def assemble_document_package(
    profile: ClientProfile,
    tender: Tender,
    extracted_requirements: ExtractedRequirements | None = None,
) -> DocumentPackage:
    if not profile.is_ready_for_agent_6():
        raise ValueError(
            "Профиль клиента должен быть в статусе READY для Агента 6, "
            f"сейчас: {profile.status} (client_id={profile.client_id})"
        )

    if extracted_requirements is not None:
        if extracted_requirements.tender_purchase_number != tender.purchase_number:
            raise ValueError(
                "Извлечённые Агентом 3 требования относятся к закупке "
                f"{extracted_requirements.tender_purchase_number!r}, а пакет собирается "
                f"для закупки {tender.purchase_number!r}"
            )
        if not extracted_requirements.expert_reviewed:
            raise ValueError(
                "Результат Агента 3 должен быть проверен экспертом "
                "(ExtractedRequirements.expert_reviewed) до того, как им воспользуется Агент 6"
            )

    # Требования из документации (Агент 3) дополняют структурированные поля
    # закупки — Tender не хранит требование к обеспечению контракта вовсе,
    # а по СРО/опыту это независимая сверка с тем, что реально написано в
    # документации, а не только с полями `Tender`.
    sro_required = tender.requires_sro
    experience_required = tender.min_experience_years > 0
    bid_security_required = True
    contract_security_required = False
    if extracted_requirements is not None:
        sro_required = sro_required or any(
            r.kind == "sro" for r in extracted_requirements.participant_requirements
        )
        experience_required = experience_required or any(
            r.kind == "experience" for r in extracted_requirements.participant_requirements
        )
        bid_security_required = extracted_requirements.security_requirement("bid") is not None
        contract_security_required = (
            extracted_requirements.security_requirement("contract") is not None
        )

    fields: list[PackageField] = []

    def add(name: str, source: str, value: object, required: bool = True) -> None:
        # `value == 0` — 0 считается настоящим значением для количественных
        # полей (например, опыт "0 лет" — это известный факт, а не пропуск).
        # Явно исключаем bool: `False == 0` истинно в Python, из-за чего
        # булево "нет" (например, банковской гарантии нет) иначе всегда
        # считалось бы заполненным и не могло стать "missing".
        has_value = bool(value) or (value == 0 and not isinstance(value, bool))
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
        required=sro_required,
    )
    add(
        "Опыт выполнения аналогичных работ (лет)",
        "permits_experience.years_of_experience",
        profile.permits_experience.years_of_experience,
        required=experience_required,
    )
    add(
        "Справки об исполненных контрактах",
        "permits_experience.completed_contracts",
        len(profile.permits_experience.completed_contracts),
        required=experience_required,
    )

    # Блок 3: мощности.
    add("Численность персонала", "capacity.staff_count", profile.capacity.staff_count)
    add(
        "Описание собственных трудовых ресурсов",
        "capacity.own_workforce_description",
        profile.capacity.own_workforce_description,
    )

    # Блок 4: финансовая готовность.
    tax_regime = profile.financial.tax_regime
    add(
        "Система налогообложения",
        "financial.tax_regime",
        tax_regime_label(tax_regime) if tax_regime is not None else "",
    )
    add(
        "Среднегодовая выручка",
        "financial.avg_annual_revenue",
        profile.financial.avg_annual_revenue,
    )
    add(
        "Обеспечение заявки (банковская гарантия)",
        "financial.bank_guarantee_available",
        profile.financial.bank_guarantee_available,
        required=bid_security_required,
    )
    if extracted_requirements is not None:
        add(
            "Обеспечение исполнения контракта (банковская гарантия)",
            "financial.bank_guarantee_available",
            profile.financial.bank_guarantee_available,
            required=contract_security_required,
        )

    return DocumentPackage(
        client_id=profile.client_id,
        tender_purchase_number=tender.purchase_number,
        fields=fields,
        hidden_risks=list(extracted_requirements.hidden_risks) if extracted_requirements else [],
    )

"""Заготовка пакета документов (Агент 6 — Сборщик документов).

Это каркас, не генератор: он не пишет содержимое документов (содержательная
генерация — будущая зона ответственности Агента 3, который пока сам —
эвристический каркас, см. `document_analyst`), а только фиксирует, какие
поля пакета нужны для подачи заявки на закупку и откуда каждое берётся — из
профиля клиента (Агент 11), данных закупки (Агент 2) или извлечённых
Агентом 3 требований документации (сроки, обеспечение, допуски).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from document_analyst.models import HiddenRisk


@dataclass
class PackageField:
    """Одно поле будущего пакета документов."""

    name: str
    source: str  # путь к полю-источнику, например "legal.inn" или "tender.max_price"
    value: str
    status: str  # "ready" | "missing" | "not_applicable"
    required: bool = True  # обязательно ли поле для этой конкретной закупки


@dataclass
class DocumentPackage:
    """Заготовка пакета документов для конкретной пары клиент+закупка."""

    client_id: str
    tender_purchase_number: str
    fields: list[PackageField] = field(default_factory=list)
    # Скрытые риски от Агента 3 — информационно, для будущего Агента 8
    # (консультант для клиента). Намеренно не часть `fields`: не участвует
    # в проверке комплектности (Агент 7 смотрит только `fields`), поэтому
    # ничего не блокирует по построению, а не по соглашению об именовании.
    hidden_risks: list[HiddenRisk] = field(default_factory=list)

    def missing_fields(self) -> list[PackageField]:
        return [f for f in self.fields if f.status == "missing"]

    def is_complete(self) -> bool:
        """Все применимые поля заполнены (не значит, что пакет готов к отправке —
        содержательная проверка эксперта всё равно обязательна по протоколу)."""
        return len(self.missing_fields()) == 0

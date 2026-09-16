"""Заготовка пакета документов (Агент 6 — Сборщик документов).

Это каркас, не генератор: он не пишет содержимое документов (это будущая
зона ответственности Агента 3 — Аналитика документации, который пока не
специфицирован), а только фиксирует, какие поля пакета нужны для подачи
заявки на закупку и откуда каждое берётся — из какого поля профиля
клиента (Агент 11) или из данных закупки (Агент 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field


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

    def missing_fields(self) -> list[PackageField]:
        return [f for f in self.fields if f.status == "missing"]

    def is_complete(self) -> bool:
        """Все применимые поля заполнены (не значит, что пакет готов к отправке —
        содержательная проверка эксперта всё равно обязательна по протоколу)."""
        return len(self.missing_fields()) == 0

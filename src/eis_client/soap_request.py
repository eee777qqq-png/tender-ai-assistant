"""Построение SOAP-конвертов для сервиса отдачи информации ЕИС.

Формат целиком взят из официальной инструкции (Москва, 2025), см.
docs/eis-integration-instruction-2025.pdf, разделы 4.3 и 5.3 — примеры
запросов getDocsByOrgRegionRequest для юридических и физических лиц.

Важно: у сервиса НЕТ параметра фильтра по ОКПД2 — запрос отбирает документы
только по региону заказчика (orgRegion), подсистеме (subsystemType), типу
документа (documentType44/223) и точной дате (exactDate, ровно одна дата за
запрос). Фильтрация по ОКПД2 (раздел «Строительство») делается уже на своей
стороне после скачивания и разбора документов — см. `classifier.ConstructionClassifier`.
"""

from __future__ import annotations

from datetime import date
from xml.sax.saxutils import escape

from .config import EISConfig


def build_docs_by_org_region_request(config: EISConfig, exact_date: date, request_id: str) -> str:
    if config.consumer_type == "vsrz":
        raise ValueError(
            "getDocsByOrgRegionRequest недоступен для consumer_type=vsrz — "
            "у ВСРЗ другой запрос (getDocsOrgRequest, отбор по организациям, "
            "не по региону), см. раздел 6.3 инструкции"
        )

    # xmlns:ws — namespace конверта (config.envelope_namespace), НЕ адрес
    # подключения. Адрес, на который уходит POST, — config.endpoint_url
    # (используется в client.py). До 2026-09-23 сюда подставлялся физический
    # URL сервиса, и ЕИС отвечал эхом запроса; официальный namespace для
    # физлиц прислала техподдержка ЕИС (обращение EIS-891228).
    header = _build_header(config)
    body = f"""\
    <ws:getDocsByOrgRegionRequest>
      <index>
        <id>{escape(request_id)}</id>
        <createDateTime>{_now_iso()}</createDateTime>
        <mode>PROD</mode>
      </index>
      <selectionParams>
        <orgRegion>{escape(config.org_region)}</orgRegion>
        <subsystemType>{escape(config.subsystem_type)}</subsystemType>
        <documentType44>{escape(config.document_type44)}</documentType44>
        <periodInfo>
          <exactDate>{exact_date.isoformat()}</exactDate>
        </periodInfo>
      </selectionParams>
    </ws:getDocsByOrgRegionRequest>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ws="{escape(config.envelope_namespace)}">
  <soapenv:Header>{header}</soapenv:Header>
  <soapenv:Body>
{body}
  </soapenv:Body>
</soapenv:Envelope>"""


def _build_header(config: EISConfig) -> str:
    if config.consumer_type == "individual_person":
        return f"\n    <individualPerson_token>{escape(config.individual_person_token)}</individualPerson_token>\n  "
    # legal_entity аутентифицируется клиентским сертификатом на транспортном
    # уровне (mTLS) — токен в заголовке не передаётся.
    return ""


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

"""Разбор ответов сервиса отдачи информации ЕИС.

Ответ на getDocsByOrgRegionRequest — SOAP-конверт с одним или несколькими
<archiveUrl> внутри <dataInfo> (см. пример в разделе 4.3 инструкции). Каждый
archiveUrl — прямая ссылка на ZIP-архив с документами, скачивается обычным
GET-запросом (тикет уже встроен в URL).
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from .exceptions import EISRequestError


def extract_archive_urls(response_xml: str) -> list[str]:
    root = _parse(response_xml)
    fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
    if fault is not None:
        raise EISRequestError(f"SOAP fault от ЕИС: {ET.tostring(fault, encoding='unicode')}")

    body = root.find("{http://schemas.xmlsoap.org/soap/envelope/}Body")
    body_elements = list(body) if body is not None else []
    if any(_local_name(el.tag).endswith("Request") for el in body_elements):
        # Сервис вернул наш же запрос вместо ...Response. Без этой проверки
        # эхо неотличимо от честного «за этот день архивов нет» (оба дают
        # пустой список), и монитор ошибочно помечал бы день обработанным.
        raise EISRequestError(
            "ЕИС вернул эхо запроса вместо ответа (в теле — "
            f"{_local_name(body_elements[0].tag)}, а не ...Response). "
            "Запрос сервисом не обработан — см. CLAUDE.md, «Известные пробелы», п.3"
        )

    urls = [el.text.strip() for el in root.iter() if _local_name(el.tag) == "archiveUrl" and el.text]
    return urls


def _parse(response_xml: str) -> ET.Element:
    try:
        return ET.fromstring(response_xml)
    except ET.ParseError as exc:
        raise EISRequestError(f"Не удалось разобрать ответ ЕИС как XML: {exc}") from exc


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag

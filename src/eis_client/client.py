from __future__ import annotations

import io
import logging
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date
from xml.etree import ElementTree as ET

import requests

from .config import EISConfig
from .exceptions import EISRequestError
from .soap_request import build_docs_by_org_region_request
from .soap_response import extract_archive_urls
from .tls import combined_ca_bundle_path

logger = logging.getLogger(__name__)

_OKPD2_TAG_RE = re.compile(r"okpd", re.IGNORECASE)
_OKPD2_CODE_RE = re.compile(r"^\d{2}(\.\d{1,3}){0,3}$")

# Реестровый номер закупки — та же эвристика (поиск по имени тега), что и
# для ОКПД2 выше, и с тем же ограничением: точная XSD-схема содержимого
# документов не входит в общедоступную инструкцию (см. докстринг
# `_find_okpd2_codes`), поэтому это не подтверждённое на реальных
# документах поле, а обоснованная попытка (см. `tender_adapter.py`).
# Специально НЕ ловит `regNum` — по примеру в самой инструкции это номер
# регистрации ОРГАНИЗАЦИИ, а не реестровый номер ЗАКУПКИ, это разные вещи.
_REESTR_NUMBER_TAG_RE = re.compile(r"reestrnum", re.IGNORECASE)
_REESTR_NUMBER_VALUE_RE = re.compile(r"^\d{15,25}$")


@dataclass
class ConstructionDocument:
    """Документ из архива ЕИС, в котором нашёлся ОКПД2-код раздела «Строительство».

    `reestr_number` — тоже эвристика (см. `_find_reestr_number`), не
    подтверждённая на реальных документах ЕИС (сервис пока не отдаёт
    реальные данные, см. CLAUDE.md, «Известные пробелы», п.3) — `None`,
    если не нашёлся."""

    archive_url: str
    file_name: str
    okpd2_codes: list[str] = field(default_factory=list)
    reestr_number: str | None = None


class EISClient:
    """Клиент сервиса отдачи информации ЕИС (docs/eis-integration-instruction-2025.pdf).

    Реализует подтверждённый в официальной инструкции протокол getDocsByOrgRegionRequest:
    отбор документов по региону заказчика + подсистеме + типу документа + точной дате.
    У сервиса нет фильтра по ОКПД2 — после скачивания архива документы
    сканируются на предмет ОКПД2-кодов и прогоняются через
    `classifier.ConstructionClassifier`, чтобы оставить только стройку.

    Аутентификация зависит от `config.consumer_type` (см. EISConfig):
    - legal_entity — mTLS клиентским сертификатом;
    - individual_person — токен в заголовке SOAP;
    - vsrz — не поддерживается этим методом (другой запрос, см. soap_request.py).
    """

    def __init__(self, config: EISConfig, construction_classifier=None):
        self.config = config
        self._session = self._build_session()
        self._classifier = construction_classifier

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        if self.config.consumer_type == "legal_entity":
            session.cert = (self.config.client_cert, self.config.client_key)
        # *.zakupki.gov.ru использует сертификат, выпущенный российским
        # национальным УЦ (Минцифры) — его нет в стандартном certifi, см. tls.py.
        session.verify = combined_ca_bundle_path()
        session.headers["Content-Type"] = "text/xml; charset=utf-8"
        return session

    def fetch_archive_urls(self, exact_date: date) -> list[str]:
        """Отправляет getDocsByOrgRegionRequest и возвращает ссылки на архивы с документами."""
        request_xml = build_docs_by_org_region_request(
            self.config, exact_date=exact_date, request_id=str(uuid.uuid4())
        )
        try:
            response = self._session.post(
                self.config.endpoint_url, data=request_xml.encode("utf-8"), timeout=self.config.timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise EISRequestError(f"Ошибка сети при обращении к ЕИС: {exc}") from exc

        return extract_archive_urls(response.text)

    def download_archive(self, archive_url: str) -> bytes:
        try:
            response = self._session.get(
                archive_url, headers=self._download_auth_headers(), timeout=self.config.timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise EISRequestError(f"Не удалось скачать архив {archive_url}: {exc}") from exc
        return response.content

    def _download_auth_headers(self) -> dict[str, str]:
        """Аутентификация GET-запроса за архивом (раздел 7 инструкции).

        Для физлица токен передаётся HTTP-заголовком с тем же именем, что и
        поле в SOAP-заголовке, — `individualPerson_token`, не `Authorization`.
        В тексте инструкции об этом нет ни слова; видно только на скриншотах
        Postman в разделе 7 (стр. 37–39, вкладка Headers). Без него ЕИС
        отвечал 403 на ссылку вида /dstore/common/download/compound?...
        (2026-09-23) — что заголовок это исправляет, ещё не проверено вживую.

        legal_entity аутентифицируется mTLS-сертификатом сессии, отдельный
        заголовок не нужен.
        """
        if self.config.consumer_type == "individual_person":
            return {"individualPerson_token": self.config.individual_person_token}
        return {}

    def get_construction_documents(self, exact_date: date) -> list[ConstructionDocument]:
        """Забирает документы за дату, отбирает те, у которых ОКПД2 относится к строительству.

        Требует классификатор (`classifier.ConstructionClassifier`), переданный при
        создании клиента или через `construction_classifier=`.
        """
        classifier = self._require_classifier()
        results: list[ConstructionDocument] = []

        # Счётчики для диагностики: «0 документов по стройке» может значить
        # три разные вещи — архивов не пришло вовсе / документы есть, но не
        # по стройке / ОКПД2 в реальных документах не находится нашей
        # эвристикой (см. `_find_okpd2_codes`). Лог разводит эти случаи.
        archive_urls = self.fetch_archive_urls(exact_date)
        xml_total = 0
        with_any_okpd2 = 0
        sample_codes: list[str] = []
        logger.info("%s: архивов в ответе ЕИС — %d", exact_date, len(archive_urls))

        for archive_url in archive_urls:
            archive_bytes = self.download_archive(archive_url)
            for file_name, xml_bytes in self._extract_xml_files(archive_bytes):
                xml_total += 1
                codes = self._find_okpd2_codes(xml_bytes)
                if codes:
                    with_any_okpd2 += 1
                    sample_codes.extend(c for c in codes if c not in sample_codes and len(sample_codes) < 10)
                construction_codes = [c for c in codes if classifier.is_construction_code(c)]
                if construction_codes:
                    results.append(
                        ConstructionDocument(
                            archive_url=archive_url,
                            file_name=file_name,
                            okpd2_codes=construction_codes,
                            reestr_number=self._find_reestr_number(xml_bytes),
                        )
                    )

        logger.info(
            "%s: XML-документов в архивах — %d, из них с найденным ОКПД2 — %d, по стройке — %d; "
            "примеры найденных кодов: %s",
            exact_date, xml_total, with_any_okpd2, len(results), ", ".join(sample_codes) or "нет",
        )
        return results

    def _require_classifier(self):
        if self._classifier is None:
            raise EISRequestError(
                "Не передан ConstructionClassifier — создайте EISClient(config, "
                "construction_classifier=ConstructionClassifier())"
            )
        return self._classifier

    @staticmethod
    def _extract_xml_files(archive_bytes: bytes) -> list[tuple[str, bytes]]:
        files = []
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".xml"):
                    files.append((name, zf.read(name)))
        return files

    @staticmethod
    def _find_okpd2_codes(xml_bytes: bytes) -> list[str]:
        """Эвристический поиск ОКПД2-кодов в документе.

        Точная XSD-схема содержимого документов (извещений/контрактов) не
        входит в инструкцию по сервису отдачи информации — здесь ищутся
        элементы, чьё имя похоже на «ОКПД2», и из их текста/атрибутов
        вытаскивается код вида "41.20.10.110". Стоит уточнить/расширить,
        когда появятся реальные образцы документов.
        """
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return []

        codes: list[str] = []
        for el in root.iter():
            tag = el.tag.split("}", 1)[-1] if "}" in el.tag else el.tag
            if _OKPD2_TAG_RE.search(tag):
                for value in (el.text, *el.attrib.values()):
                    if value and _OKPD2_CODE_RE.match(value.strip()):
                        codes.append(value.strip())
        return codes

    @staticmethod
    def _find_reestr_number(xml_bytes: bytes) -> str | None:
        """Эвристический поиск реестрового номера закупки — см. докстринг
        `_REESTR_NUMBER_TAG_RE`. Возвращает первое найденное значение или
        `None`, честно, а не выдуманный номер."""
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return None

        for el in root.iter():
            tag = el.tag.split("}", 1)[-1] if "}" in el.tag else el.tag
            if not _REESTR_NUMBER_TAG_RE.search(tag):
                continue
            for value in (el.text, *el.attrib.values()):
                if value and _REESTR_NUMBER_VALUE_RE.match(value.strip()):
                    return value.strip()
        return None

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "EISClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

from __future__ import annotations

import io
import logging
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from .config import EISConfig
from .exceptions import EISRequestError
from .soap_request import build_docs_by_org_region_request
from .soap_response import extract_archive_urls
from .tls import combined_ca_bundle_path

logger = logging.getLogger(__name__)

# До 2026-09-23 искали только по ИМЕНИ тега (что-то похожее на "okpd") — не
# нашли НИ ОДНОГО кода ни в одном из 1640 реальных документов ЕИС (запрос
# Edwin, 2026-09-21). Два подтверждённых на реальных документах пути, для
# двух разных типов документа (в зависимости от EIS_DOCUMENT_TYPE44):
# - контракт (`contract_2770206615726000446`, 2026-09-23): путь
#   .../products/product/KTRU/OKPD2/code — сам тег называется просто <code>,
#   «okpd» есть только в имени тега-предка через один уровень (<OKPD2>),
#   обёрнутого в <KTRU>;
# - извещение (`epNotificationEF2020`, файл №37, 2026-09-23 — с 2026-09-23
#   основной источник, см. CLAUDE.md → «Решено»): путь
#   .../purchaseObjectsInfo/notDrugPurchaseObjectsInfo/purchaseObject/OKPD2/OKPDCode
#   — БЕЗ обёртки KTRU, и последний тег называется `OKPDCode`, не `code`.
# Оба пути пробуются по очереди (не гадание — каждый отдельно подтверждён,
# просто для разных типов документа), обход всего дерева документа (не
# первое совпадение — учитываются все позиции, если их несколько). Старая
# эвристика по имени тега оставлена третьим, резервным способом на случай
# ещё не встреченного типа документа — она НЕ подтверждена ни на одном
# реальном документе, в отличие от обоих структурных путей.
_OKPD2_PATH_SUFFIXES = (
    ("ktru", "okpd2", "code"),  # контракт
    ("purchaseobject", "okpd2", "okpdcode"),  # извещение
)
_OKPD2_TAG_RE = re.compile(r"okpd", re.IGNORECASE)
_OKPD2_CODE_RE = re.compile(r"^\d{2}(\.\d{1,3}){0,3}$")

# Реестровый номер закупки — два подтверждённых на реальных документах пути,
# для двух разных типов документа, которые эта функция может получить (в
# зависимости от EIS_DOCUMENT_TYPE44):
# - контракт (`contract_2770206615726000446`, 2026-09-23): путь
#   .../foundation/fcsOrder/order/notificationNumber (19 цифр) — это номер
#   ИЗВЕЩЕНИЯ, на основании которого заключён контракт, не номер контракта;
# - извещение (`0373200298826000007`, 2026-09-23, реестр PRIZ — с 2026-09-23
#   основной источник, см. CLAUDE.md → «Решено»): путь
#   .../commonInfo/purchaseNumber — собственный номер извещения.
# Оба пути пробуются по очереди (первое совпадение побеждает) — не гадание,
# у каждого пути отдельное реальное подтверждение, просто для разных типов
# документа. Старая эвристика по имени тега (`reestrnum`) оставлена
# резервным способом — не подтверждена на реальных документах.
# Специально НЕ ловит `regNum` — по примеру в инструкции это номер
# регистрации ОРГАНИЗАЦИИ, а не реестровый номер ЗАКУПКИ, это разные вещи.
_REESTR_NUMBER_PATH_SUFFIXES = (
    ("foundation", "fcsorder", "order", "notificationnumber"),  # контракт
    ("commoninfo", "purchasenumber"),  # извещение
)
_REESTR_NUMBER_TAG_RE = re.compile(r"reestrnum", re.IGNORECASE)
_REESTR_NUMBER_VALUE_RE = re.compile(r"^\d{15,25}$")


@dataclass
class ConstructionDocument:
    """Документ из архива ЕИС, в котором нашёлся ОКПД2-код раздела «Строительство».

    `reestr_number` — путь подтверждён на реальном документе (см. `_find_reestr_number`,
    2026-09-23) — это номер ИЗВЕЩЕНИЯ, на основании которого заключён контракт,
    а не номер самого контракта. `None`, если не нашёлся."""

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

    def __init__(self, config: EISConfig, construction_classifier=None, raw_archive_dir=None):
        """`raw_archive_dir` — если задан, каждый скачанный архив сохраняется туда
        как есть (`<дата>_<NN>.zip`) для ручного разбора структуры реальных
        документов. По умолчанию архивы живут только в памяти."""
        self.config = config
        self._session = self._build_session()
        self._classifier = construction_classifier
        self._raw_archive_dir = Path(raw_archive_dir) if raw_archive_dir else None

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
        xml_unparsed = 0
        with_any_okpd2 = 0
        sample_codes: list[str] = []
        logger.info("%s: архивов в ответе ЕИС — %d", exact_date, len(archive_urls))

        for index, archive_url in enumerate(archive_urls, start=1):
            archive_bytes = self.download_archive(archive_url)
            if self._raw_archive_dir is not None:
                self._raw_archive_dir.mkdir(parents=True, exist_ok=True)
                raw_path = self._raw_archive_dir / f"{exact_date.isoformat()}_{index:02d}.zip"
                raw_path.write_bytes(archive_bytes)
                logger.info("Архив сохранён: %s", raw_path)
            for file_name, xml_bytes in self._extract_xml_files(archive_bytes):
                xml_total += 1
                try:
                    ET.fromstring(xml_bytes)
                except ET.ParseError:
                    # `_find_okpd2_codes` в этом случае молча вернёт [] — без
                    # отдельного счётчика неразобранный файл неотличим от
                    # «ОКПД2 в документе нет».
                    xml_unparsed += 1
                    continue
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
            "%s: XML-документов в архивах — %d (не разобрались как XML — %d), с найденным ОКПД2 — %d, "
            "по стройке — %d; примеры найденных кодов: %s",
            exact_date, xml_total, xml_unparsed, with_any_okpd2, len(results),
            ", ".join(sample_codes) or "нет",
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
        """Поиск ОКПД2-кодов в документе — по реальной структуре, не по имени тега.

        Основной способ (оба пути подтверждены на реальных документах ЕИС,
        2026-09-23, см. CLAUDE.md «Известные пробелы» → «Решено» и
        `_OKPD2_PATH_SUFFIXES`): путь тега оканчивается на .../KTRU/OKPD2/code
        (контракт) или .../purchaseObject/OKPD2/OKPDCode (извещение), без
        учёта регистра и namespace. Документ может содержать несколько
        позиций — обходится всё дерево, а не первое совпадение, коды
        собираются со всех.

        Резервный способ — прежняя эвристика по имени тега (что-то похожее
        на «okpd»): не подтверждена ни на одном реальном документе, оставлена
        на случай ещё не встреченного типа документа.
        """
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return []

        codes: list[str] = []
        EISClient._walk_for_okpd2(root, (), codes)
        return codes

    @staticmethod
    def _walk_for_okpd2(el: ET.Element, path: tuple[str, ...], codes: list[str]) -> None:
        tag = el.tag.split("}", 1)[-1] if "}" in el.tag else el.tag
        path = path + (tag.lower(),)

        if path[-3:] in _OKPD2_PATH_SUFFIXES:
            value = (el.text or "").strip()
            if value and _OKPD2_CODE_RE.match(value) and value not in codes:
                codes.append(value)
        elif _OKPD2_TAG_RE.search(tag):
            for raw in (el.text, *el.attrib.values()):
                value = raw.strip() if raw else ""
                if value and _OKPD2_CODE_RE.match(value) and value not in codes:
                    codes.append(value)

        for child in el:
            EISClient._walk_for_okpd2(child, path, codes)

    @staticmethod
    def _find_reestr_number(xml_bytes: bytes) -> str | None:
        """Поиск реестрового номера закупки — по реальной структуре, не по имени тега.

        Основной способ (оба пути подтверждены на реальных документах ЕИС,
        2026-09-23 — см. `_REESTR_NUMBER_PATH_SUFFIXES`): путь тега
        оканчивается на .../foundation/fcsOrder/order/notificationNumber
        (контракт) или .../commonInfo/purchaseNumber (извещение). Резервный
        способ — прежняя эвристика по имени тега (`reestrnum`), не
        подтверждена на реальных документах.

        Возвращает первое найденное значение или `None`, честно, а не
        выдуманный номер."""
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return None

        result: list[str | None] = [None]
        EISClient._walk_for_reestr_number(root, (), result)
        return result[0]

    @staticmethod
    def _walk_for_reestr_number(el: ET.Element, path: tuple[str, ...], result: list[str | None]) -> None:
        if result[0] is not None:
            return

        tag = el.tag.split("}", 1)[-1] if "}" in el.tag else el.tag
        path = path + (tag.lower(),)

        if any(path[-len(suffix) :] == suffix for suffix in _REESTR_NUMBER_PATH_SUFFIXES):
            value = (el.text or "").strip()
            if value and _REESTR_NUMBER_VALUE_RE.match(value):
                result[0] = value
                return
        elif _REESTR_NUMBER_TAG_RE.search(tag):
            for raw in (el.text, *el.attrib.values()):
                value = raw.strip() if raw else ""
                if value and _REESTR_NUMBER_VALUE_RE.match(value):
                    result[0] = value
                    return

        for child in el:
            EISClient._walk_for_reestr_number(child, path, result)
            if result[0] is not None:
                return

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "EISClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

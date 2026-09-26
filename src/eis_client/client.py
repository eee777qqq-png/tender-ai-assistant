from __future__ import annotations

import io
import logging
import re
import time
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

# Найдено вживую Edwin, 2026-09-26: скачивание приложений извещения
# (attachmentsInfo/attachmentInfo/url, https://zakupki.gov.ru/44fz/filestore/...)
# 404-ило не из-за авторизации (ЕСИА-сессия НЕ нужна вообще — проверено
# независимо ещё раз в этой сессии, не только со слов Edwin), а из-за
# защиты от ботов по заголовку User-Agent: без него — честный 404 от той
# же настоящей инфраструктуры ЕИС (см. CLAUDE.md, «Известные пробелы»,
# п.10 — там же ошибочная гипотеза про ЕСИА, оставлена для истории), с
# обычным браузерным User-Agent — 200 и настоящий файл (проверено: размер
# ответа побайтово совпал с `fileSize` из XML извещения). Не тот же
# механизм, что `_download_auth_headers()`/`individualPerson_token` —
# тот нужен для архивов (`int.zakupki.gov.ru/dstore/...`), это — для
# приложений на публичном портале (`zakupki.gov.ru/44fz/filestore/...`),
# два разных сервиса на одном доменном семействе.
_ATTACHMENT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Эмпирический безопасный темп запросов к этому сайту (не из официальной
# инструкции — та описывает только SOAP/dstore API, не публичный портал) —
# не чаще 8 запросов в секунду. Выдерживается между ПОСЛЕДОВАТЕЛЬНЫМИ
# вызовами `download_attachment()` на одном клиенте, не глобально на
# процесс — двух параллельных `EISClient` этот лимит не свяжет.
_ATTACHMENT_MIN_INTERVAL_SECONDS = 1.0 / 8

# До 2026-09-23 искали только по ИМЕНИ тега (что-то похожее на "okpd") — не
# нашли НИ ОДНОГО кода ни в одном из 1640 реальных документов ЕИС (запрос
# Edwin, 2026-09-21). Реальная структура (подтверждена Edwin на настоящем
# документе, не по инструкции): код лежит по ПУТИ
# .../products/product/KTRU/OKPD2/code — сам тег называется просто <code>,
# «okpd» есть только в имени тега-ПРЕДКА через один уровень. Основной способ
# теперь — совпадение по последним трём звеньям пути (без учёта регистра);
# старая эвристика по имени тега оставлена вторым, резервным способом на
# случай других типов документов (извещения и т.п.), которых мы ещё не
# видели вживую — она подтверждена НЕ была, в отличие от основного способа.
_OKPD2_PATH_SUFFIX = ("ktru", "okpd2", "code")
_OKPD2_TAG_RE = re.compile(r"okpd", re.IGNORECASE)
_OKPD2_CODE_RE = re.compile(r"^\d{2}(\.\d{1,3}){0,3}$")

# Реестровый номер закупки — подтверждён на реальном документе, 2026-09-23
# (контракт `contract_2770206615726000446`, стройка, ОКПД2 41.20.40.900):
# путь .../foundation/fcsOrder/order/notificationNumber (19 цифр) — это
# реестровый номер ИЗВЕЩЕНИЯ, на основании которого заключён контракт, не
# номер самого контракта. Основной способ теперь — та же логика, что у
# ОКПД2 (см. `_OKPD2_PATH_SUFFIX` выше): совпадение по последним 4 звеньям
# пути тега. Старая эвристика по имени тега (`reestrnum`) оставлена
# резервным способом — не подтверждена на реальных документах, но
# пригодится, если формат окажется другим у извещений (не контрактов).
# Специально НЕ ловит `regNum` — по примеру в инструкции это номер
# регистрации ОРГАНИЗАЦИИ, а не реестровый номер ЗАКУПКИ, это разные вещи.
_REESTR_NUMBER_PATH_SUFFIX = ("foundation", "fcsorder", "order", "notificationnumber")
_REESTR_NUMBER_TAG_RE = re.compile(r"reestrnum", re.IGNORECASE)
_REESTR_NUMBER_VALUE_RE = re.compile(r"^\d{15,25}$")


@dataclass
class ConstructionDocument:
    """Документ из архива ЕИС, в котором нашёлся ОКПД2-код раздела «Строительство».

    `reestr_number` — путь подтверждён на реальном документе (см. `_find_reestr_number`,
    2026-09-23) — это номер ИЗВЕЩЕНИЯ, на основании которого заключён контракт,
    а не номер самого контракта (для документов из реестра КОНТРАКТОВ; для
    ИЗВЕЩЕНИЙ этот путь не работает — см. `notice_parser.extract_purchase_number`).
    `None`, если не нашёлся.

    `raw_xml` — исходные байты документа, добавлено 2026-09-24 вместе с
    `eis_client.notice_parser.notice_document_to_tender()`: чтобы Агент 1 ->
    Агент 2 замкнулся на реальных данных извещений, нужен весь документ (имя
    заказчика, НМЦК, срок подачи и т. д.), а не только ОКПД2/реестровый
    номер, которые счётчик оставлял раньше. `None` только у документов,
    собранных вручную (тесты) без реального XML — `get_construction_documents()`
    всегда заполняет это поле."""

    archive_url: str
    file_name: str
    okpd2_codes: list[str] = field(default_factory=list)
    reestr_number: str | None = None
    raw_xml: bytes | None = None


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
        self._last_attachment_request_at: float | None = None

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

    def download_attachment(self, url: str) -> bytes:
        """Скачивает приложение извещения (техзадание/проект контракта/
        требования к заявке и т. п.) с публичного портала `zakupki.gov.ru`
        (`attachmentsInfo/attachmentInfo/url` в XML извещения — см.
        `notice_parser.extract_attachments()`).

        **Не то же самое, что `download_archive()`** — другой сервис (публичный
        веб-портал, не API `int.zakupki.gov.ru`), другой способ доступа:
        не токен в заголовке, а обычный браузерный `User-Agent` (см. модульную
        заметку выше про находку Edwin, 2026-09-26 — защита от ботов, не
        авторизация; ЕСИА-сессия не нужна, подтверждено независимо).

        Выдерживает `_ATTACHMENT_MIN_INTERVAL_SECONDS` между последовательными
        вызовами на этом клиенте — простая защита от превышения безопасного
        темпа запросов при скачивании нескольких приложений подряд."""
        if self._last_attachment_request_at is not None:
            elapsed = time.monotonic() - self._last_attachment_request_at
            wait = _ATTACHMENT_MIN_INTERVAL_SECONDS - elapsed
            if wait > 0:
                time.sleep(wait)

        try:
            response = self._session.get(
                url, headers={"User-Agent": _ATTACHMENT_USER_AGENT}, timeout=self.config.timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise EISRequestError(f"Не удалось скачать приложение {url}: {exc}") from exc
        finally:
            self._last_attachment_request_at = time.monotonic()
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
                            raw_xml=xml_bytes,
                        )
                    )

        logger.info(
            "%s: XML-документов в архивах — %d (не разобрались как XML — %d), с найденным ОКПД2 — %d, "
            "по стройке — %d; примеры найденных кодов: %s",
            exact_date, xml_total, xml_unparsed, with_any_okpd2, len(results),
            ", ".join(sample_codes) or "нет",
        )
        return results

    def get_construction_documents_from_local_archives(
        self, archive_paths: list[Path]
    ) -> list[ConstructionDocument]:
        """То же самое, что `get_construction_documents()`, но БЕЗ сети — читает
        архивы, уже скачанные ранее на диск (`raw_archive_dir=` при прошлом
        запуске `get_construction_documents()`, см. `data/raw_notices*`).

        Добавлено 2026-09-26, чтобы можно было гонять матчинг (`match_real_notices.py`)
        на реальных, уже полученных архивах, не делая новый сетевой запрос
        к ЕИС при каждой проверке. Использует ровно ту же логику разбора и
        фильтрации (`_extract_xml_files`/`_find_okpd2_codes`/`_find_reestr_number`,
        фильтр `classifier.is_construction_code()`), что и сетевой путь — это
        не отдельная, потенциально расходящаяся копия."""
        classifier = self._require_classifier()
        results: list[ConstructionDocument] = []
        for archive_path in archive_paths:
            archive_bytes = Path(archive_path).read_bytes()
            for file_name, xml_bytes in self._extract_xml_files(archive_bytes):
                try:
                    ET.fromstring(xml_bytes)
                except ET.ParseError:
                    continue
                codes = self._find_okpd2_codes(xml_bytes)
                construction_codes = [c for c in codes if classifier.is_construction_code(c)]
                if construction_codes:
                    results.append(
                        ConstructionDocument(
                            archive_url=str(archive_path),
                            file_name=file_name,
                            okpd2_codes=construction_codes,
                            reestr_number=self._find_reestr_number(xml_bytes),
                            raw_xml=xml_bytes,
                        )
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

        Основной способ (подтверждён на реальном документе ЕИС, 2026-09-23,
        см. CLAUDE.md «Известные пробелы» → «Решено»): путь тега оканчивается
        на .../KTRU/OKPD2/code (без учёта регистра и namespace) — так лежит
        код позиции в реальном документе. Документ может содержать несколько
        позиций (`products/product`) — обходится всё дерево, а не первое
        совпадение, коды собираются со всех.

        Резервный способ — прежняя эвристика по имени тега (что-то похожее
        на «okpd»): на 1640 реальных КОНТРАКТАХ (2026-09-23) не нашла ничего,
        но подтверждена 2026-09-24 на реальных ИЗВЕЩЕНИЯХ (epNotificationEF2020,
        PRIZ) — там код лежит по пути `purchaseObject/OKPD2/OKPDCode`, тег
        `OKPDCode` (без `KTRU`-обёртки) ловится именно этой эвристикой, не
        путём выше. Оставлена основной причиной, почему `notice_document_to_tender()`
        (`notice_parser.py`) может переиспользовать этот же метод для извещений
        без отдельного пути.
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

        if path[-3:] == _OKPD2_PATH_SUFFIX:
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

        Основной способ (подтверждён на реальном документе ЕИС, 2026-09-23 —
        см. `_REESTR_NUMBER_PATH_SUFFIX`): путь тега оканчивается на
        .../foundation/fcsOrder/order/notificationNumber. Резервный способ —
        прежняя эвристика по имени тега (`reestrnum`), не подтверждена на
        реальных документах.

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

        if path[-4:] == _REESTR_NUMBER_PATH_SUFFIX:
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

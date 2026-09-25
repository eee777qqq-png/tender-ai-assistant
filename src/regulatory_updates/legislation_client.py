"""Сетевой слой для бухгалтерской/юридической базы (Агент 10) — та же
роль, что `smeta_estimator.fsnb_client`/`regional_pricing_client` играют для
базы расценок Агента 4: только скачивание и минимальная нормализация
(снятие HTML-тегов), без разбора конкретных ставок/статей — это
`legislation_parser.py`. См. `legislation_watch.py` за общей картиной и
источниками.
"""

from __future__ import annotations

import re

import requests

_REQUEST_HEADERS = {
    # Без реалистичного User-Agent оба сайта (nalog.gov.ru, consultant.ru) в
    # проверке вживую 2026-09-25 отвечали нормально и без него, но заголовок
    # оставлен явным, а не опущен — устойчивее к будущим изменениям на
    # стороне сайтов.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def strip_html(html: str) -> str:
    """Снимает теги и схлопывает пробелы — та же грубая, но достаточная
    нормализация, что уже подтвердилась вживую при разборе nalog.gov.ru и
    consultant.ru (не полноценный HTML-парсер вроде BeautifulSoup — в
    зависимостях проекта его нет, а текст обеих страниц оказался
    структурирован достаточно просто для regex после снятия тегов)."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text)


def fetch_page_text(url: str, timeout: int = 15) -> str:
    response = requests.get(url, headers=_REQUEST_HEADERS, timeout=timeout)
    response.raise_for_status()
    return strip_html(response.text)

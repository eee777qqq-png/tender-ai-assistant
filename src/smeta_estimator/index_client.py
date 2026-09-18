"""Загрузка писем Минстроя с региональными индексами пересчёта цен.

Прямой JSON API без авторизации, проверено вживую 2026-09-18: `GET
/api/FrsnDocument/DocDataByGuid/{guid}` отдаёт письмо целиком (поле
`fullPublishedText` — HTML), см. `docs/agent4-ai-matching-feasibility.md`.
`guid` конкретного письма нужно смотреть на странице раздела «Индексы
изменения сметной стоимости строительства» (fgiscs.minstroyrf.ru/frsn/) —
единого «текущего письма» API не отдаёт, автоматического отслеживания
новых писем в этом фундаменте нет (см. CLAUDE.md, «Известные пробелы»).
"""

from __future__ import annotations

import requests

DOC_DATA_URL_TEMPLATE = "https://fgiscs.minstroyrf.ru/api/FrsnDocument/DocDataByGuid/{guid}"


def fetch_index_letter_html(guid: str, timeout: int = 60) -> str:
    response = requests.get(
        DOC_DATA_URL_TEMPLATE.format(guid=guid),
        params={"page": 1, "take": 25, "sort": "{}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["fullPublishedText"]

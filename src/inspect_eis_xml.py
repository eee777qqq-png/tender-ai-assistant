"""Показывает СТРУКТУРУ реального документа ЕИС — без содержимого.

Нужен, чтобы понять, где в настоящих документах лежит код классификации
(ОКПД2/КТРУ), и прислать это разработчику, не раскрывая данные заказчика:
печатаются только пути тегов (без namespace) и сколько раз каждый встретился.
Значения скрыты («…, 37 симв.»), кроме тех, что похожи на код классификации
(вида 41.20.40.000 или 41.20.40.000-00000002) — их можно показывать смело,
это не личные данные.

Использование:
    python src/inspect_eis_xml.py data/raw/2026-09-21_01.zip --list
    python src/inspect_eis_xml.py data/raw/2026-09-21_01.zip --file 3
    python src/inspect_eis_xml.py документ.xml --show code

--list       — не разбирать ничего, а показать номер, имя файла и найденные
             ОКПД2-коды для каждого XML в архиве (та же логика, что в
             `EISClient._find_okpd2_codes`) — чтобы найти номер нужного
             документа, не подбирая --file вслепую по одному.
--file N   — какой по счёту XML внутри архива разобрать, число (по умолчанию 1;
             посмотрите номер через --list).
--show СЛОВО — дополнительно показать значения тегов, в имени которых есть
             это слово (без учёта регистра). Используйте только для тегов
             с кодами, не для названий/ИНН/адресов.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eis_client.client import EISClient  # noqa: E402  (нужен путь до src/ выше)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

_CODE_LIKE_RE = re.compile(r"^\d{2}(\.\d{1,3}){1,3}(-\d{1,10})?$")


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _mask(value: str, reveal: bool) -> str:
    value = value.strip()
    if reveal or _CODE_LIKE_RE.match(value):
        return value
    return f"…, {len(value)} симв."


def describe(xml_bytes: bytes, show: str | None = None) -> list[str]:
    root = ET.fromstring(xml_bytes)
    counts: Counter[str] = Counter()
    first_value: dict[str, str] = {}
    attrs: dict[str, set[str]] = {}

    def walk(el: ET.Element, path: str) -> None:
        name = _local(el.tag)
        here = f"{path}/{name}" if path else name
        counts[here] += 1
        if el.attrib:
            attrs.setdefault(here, set()).update(_local(a) for a in el.attrib)
        text = (el.text or "").strip()
        if text and len(el) == 0 and here not in first_value:
            reveal = bool(show) and show.lower() in name.lower()
            first_value[here] = _mask(text, reveal)
        for child in el:
            walk(child, here)

    walk(root, "")
    lines = []
    for path, n in counts.items():
        line = f"{path}  ×{n}"
        if path in first_value:
            line += f"  = {first_value[path]}"
        if path in attrs:
            line += f"  [атрибуты: {', '.join(sorted(attrs[path]))}]"
        lines.append(line)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="Архив .zip из --save-raw или отдельный .xml")
    parser.add_argument("--list", action="store_true", help="Список файлов архива с найденными ОКПД2-кодами, без разбора структуры")
    parser.add_argument("--file", type=int, default=1, help="Номер XML внутри архива, число (с 1) — посмотрите через --list")
    parser.add_argument("--show", default=None, help="Показать значения тегов, в имени которых есть это слово")
    args = parser.parse_args()

    if args.path.suffix.lower() != ".zip":
        if args.list:
            print("--list работает только для .zip-архивов, у отдельного XML-файла нечего перечислять.")
            return 1
        xml_bytes = args.path.read_bytes()
    else:
        with zipfile.ZipFile(args.path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            print(f"В архиве XML-файлов: {len(names)}")
            if not names:
                print("Все файлы архива:", ", ".join(zf.namelist()[:20]))
                return 1

            if args.list:
                for i, name in enumerate(names, start=1):
                    try:
                        codes = EISClient._find_okpd2_codes(zf.read(name))
                    except ET.ParseError:
                        codes = ["<не разобрался как XML>"]
                    print(f"{i:>3}. {name}  ОКПД2: {', '.join(codes) or '—'}")
                print("\nЗапустите повторно с --file <номер> для нужного документа.")
                return 0

            name = names[min(max(args.file, 1), len(names)) - 1]
            xml_bytes = zf.read(name)
        print(f"Разбираю файл №{args.file}: {name}\n")

    try:
        lines = describe(xml_bytes, args.show)
    except ET.ParseError as exc:
        print(f"Файл не разбирается как XML: {exc}")
        print("Первые 200 байт:", xml_bytes[:200])
        return 1

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

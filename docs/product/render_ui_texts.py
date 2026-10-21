"""Генерирует docs/product/ui-texts.md из docs/product/ui-texts.ru.json.

Запуск из корня репо: python3 docs/product/render_ui_texts.py
Только стандартная библиотека.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "ui-texts.ru.json"
DST = HERE / "ui-texts.md"


def cell(value):
    if value is None:
        return "—"
    if isinstance(value, list):
        return ", ".join(f"`{v}`" for v in value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def flatten(prefix, node):
    """Плоский список (ключ, текст) для простых разделов."""
    for key, value in node.items():
        if key.startswith("_"):
            continue
        path = f"{prefix}.{key}"
        if isinstance(value, dict):
            yield from flatten(path, value)
        else:
            yield path, value


def items(node):
    return [(k, v) for k, v in node.items() if not k.startswith("_")]


def main():
    t = json.loads(SRC.read_text(encoding="utf-8"))
    names = {code: p["name"] for code, p in items(t["products"])}
    parts = [
        "# Тексты интерфейса",
        "",
        "<!-- Файл сгенерирован из ui-texts.ru.json: python3 docs/product/render_ui_texts.py. Правьте JSON. -->",
        "",
        f"Версия {t['_meta']['version']} · владелец: Продукт · задача {t['_meta']['issue']}",
        "",
        "Источник истины — [`ui-texts.ru.json`](ui-texts.ru.json): фронтенд берёт тексты по ключам оттуда. "
        "Этот файл — читаемая версия для команды и жюри.",
        "",
        "**Правила:** обращение на «Вы» с заглавной; без банковского жаргона, термины поясняем при первом упоминании; "
        "у каждого статуса есть «что происходит» и «что дальше»; отказ всегда с причиной и советом. "
        "Плейсхолдеры — `{name}`.",
        "",
        "**Словари кодов** (`products`, `purposes`, `rejectionReasons`, `counterOfferReasons`, `requiredDocuments`) — "
        "общий контракт: риски возвращают эти `code` в `Reason.code` и `RequiredDocument.code`, бэкенд — в `Product.code`. "
        "Новый код = сначала строка здесь.",
        "",
        "## Продукты",
        "",
        "> " + t["products"]["_note"],
        "",
        table(["code", "Название", "Коротко", "Описание", "Параметры"],
              [(f"`{c}`", p["name"], p["short"], p["description"], p["params"]) for c, p in items(t["products"])]),
        "",
        "## Цели («Для чего деньги»)",
        "",
        table(["code", "Подпись", "Продукты (первый — рекомендуемый)"],
              [(f"`{c}`", p["label"], ", ".join(names[x] for x in p["products"])) for c, p in items(t["purposes"])]),
        "",
        "## Статусы заявки",
        "",
        "Статусы — по [`application-states.md`](../application-states.md).",
        "",
        table(["Статус", "Таймлайн", "Заголовок", "Что происходит", "Что дальше", "Кабинет банка"],
              [(f"`{s}`", v["timeline"], v["title"], v["description"], v["next"], v["bank"])
               for s, v in items(t["statuses"])]),
        "",
        f"Если в `CHECKING` внешний сервис отвечает долго: «{t['statuses']['CHECKING']['slow']}»",
        "",
        "## Причины отказа",
        "",
        "> " + t["rejectionReasons"]["_note"],
        "",
        table(["code", "Что случилось", "Что можно сделать"],
              [(f"`{c}`", v["message"], v["advice"]) for c, v in items(t["rejectionReasons"])]),
        "",
        "## Причины контрпредложения",
        "",
        table(["code", "Текст"], [(f"`{c}`", v) for c, v in items(t["counterOfferReasons"])]),
        "",
        "## Запрос данных",
        "",
        "> " + t["requiredDocuments"]["_note"],
        "",
        table(["code", "Что нужно", "Зачем"],
              [(f"`{c}`", v["title"], v["explanation"]) for c, v in items(t["requiredDocuments"])]),
        "",
    ]
    simple = [
        ("Общее", "common"), ("Вход", "login"), ("Подбор продукта", "productPicker"), ("Анкета", "form"),
        ("Экран решения", "decision"), ("Подписание", "signing"), ("Мои заявки", "applications"),
        ("Ошибки", "errors"), ("Кабинет аналитика", "bank"),
    ]
    for title, key in simple:
        parts += [f"## {title}", "", table(["Ключ", "Текст"], [(f"`{k}`", v) for k, v in flatten(key, t[key])]), ""]
    DST.write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()

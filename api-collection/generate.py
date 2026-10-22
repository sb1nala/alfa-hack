"""Генерирует коллекцию Bruno из seed/companies.json и seed/accounts.json.

Запуск из корня репо: python3 api-collection/generate.py
Только стандартная библиотека. Ручные правки .bru-файлов перезапишутся — правьте этот скрипт.
"""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "api-collection"

UUID_JS = (
    "const uuid = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {\n"
    "  const r = Math.random() * 16 | 0;\n"
    "  return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);\n"
    "});"
)
KEEP_REVISION = (
    "if (res.body && res.body.revision) bru.setVar(\"revision\", res.body.revision);"
)


def bru(name, seq, method, url, *, body=None, multipart=None, headers=None, pre=None, post=None,
        expect=None, docs=None):
    parts = [f"meta {{\n  name: {name}\n  type: http\n  seq: {seq}\n}}\n"]
    body_kind = "json" if body is not None else "multipartForm" if multipart else "none"
    parts.append(f"{method} {{\n  url: {url}\n  body: {body_kind}\n  auth: none\n}}\n")
    hdrs = dict(headers or {})
    if method in ("post", "patch", "put", "delete"):
        hdrs.setdefault("Origin", "{{origin}}")
        hdrs.setdefault("X-CSRF-Token", "{{csrf}}")
    hdrs = {k: v for k, v in hdrs.items() if v is not None}
    if hdrs:
        parts.append("headers {\n" + "".join(f"  {k}: {v}\n" for k, v in hdrs.items()) + "}\n")
    if body is not None:
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
        parts.append("body:json {\n" + "\n".join("  " + line for line in text.splitlines()) + "\n}\n")
    if multipart:
        parts.append("body:multipart-form {\n" + "".join(f"  {k}: {v}\n" for k, v in multipart.items()) + "}\n")
    if pre:
        parts.append("script:pre-request {\n" + indent(pre) + "\n}\n")
    if post:
        parts.append("script:post-response {\n" + indent(post) + "\n}\n")
    if expect:
        codes = expect if isinstance(expect, list) else [expect]
        parts.append(
            "tests {\n"
            f"  test(\"HTTP {' или '.join(map(str, codes))}\", function () {{\n"
            f"    expect({json.dumps(codes)}).to.include(res.status);\n"
            "  });\n}\n"
        )
    if docs:
        parts.append("docs {\n" + indent(docs) + "\n}\n")
    return "\n".join(parts)


def indent(text):
    return "\n".join("  " + line if line else "" for line in text.splitlines())


def auth_requests(seq0, login_var, password_var, who):
    return [
        (f"{seq0:02d} CSRF (аноним)", bru("CSRF (аноним)", seq0, "get", "{{baseUrl}}/api/auth/csrf",
            post='bru.setVar("csrf", res.body.csrf_token);', expect=200)),
        (f"{seq0+1:02d} Вход — {who}", bru(f"Вход — {who}", seq0 + 1, "post", "{{baseUrl}}/api/auth/login",
            body=f'{{\n  "login": "{{{{{login_var}}}}}",\n  "password": "{{{{{password_var}}}}}"\n}}', expect=200,
            docs="JWT приходит в HttpOnly cookie `access_token`; Bruno хранит cookie сам.")),
        (f"{seq0+2:02d} CSRF (после входа)", bru("CSRF (после входа)", seq0 + 2, "get", "{{baseUrl}}/api/auth/csrf",
            post='bru.setVar("csrf", res.body.csrf_token);', expect=200)),
        (f"{seq0+3:02d} Кто я", bru("Кто я", seq0 + 3, "get", "{{baseUrl}}/api/auth/me", expect=200)),
    ]


def client_folder():
    reqs = auth_requests(1, "clientLogin", "clientPassword", "клиент")
    reqs += [
        ("05 Каталог продуктов", bru("Каталог продуктов", 5, "get", "{{baseUrl}}/api/products",
            post='const p = res.body.items.find(x => x.code === bru.getEnvVar("productCode"));\n'
                 'if (p) bru.setVar("productId", p.id);', expect=200)),
        ("06 Компания по ИНН", bru("Компания по ИНН", 6, "get", "{{baseUrl}}/api/companies/lookup?inn={{inn}}",
            post='if (res.body && res.body.company) bru.setVar("companyJson", JSON.stringify(res.body.company));',
            expect=200, docs="Автозаполнение анкеты по ЕГРЮЛ/ЕГРИП.")),
        ("07 Создать черновик", bru("Создать черновик", 7, "post", "{{baseUrl}}/api/applications",
            body="{}",
            pre='req.setBody({\n'
                '  product_id: bru.getVar("productId"),\n'
                '  purpose: bru.getEnvVar("purpose"),\n'
                '  amount: bru.getEnvVar("amount"),\n'
                '  term_months: Number(bru.getEnvVar("termMonths")),\n'
                '  annual_revenue: bru.getEnvVar("annualRevenue"),\n'
                '  company: JSON.parse(bru.getVar("companyJson") || "null")\n'
                '});',
            post='bru.setVar("applicationId", res.body.id);\n' + KEEP_REVISION, expect=201)),
        ("08 Отправить заявку", bru("Отправить заявку", 8, "post",
            "{{baseUrl}}/api/applications/{{applicationId}}/submit",
            body='{\n  "expected_revision": {{revision}},\n  "consent_to_checks": true\n}',
            pre=UUID_JS + '\nreq.setHeader("Idempotency-Key", uuid());',
            post=KEEP_REVISION, expect=202)),
        ("09 Статус и решение", bru("Статус и решение", 9, "get", "{{baseUrl}}/api/applications/{{applicationId}}",
            post=KEEP_REVISION + '\nbru.setVar("status", res.body.status);\n'
                 'const offer = res.body.decision && res.body.decision.offer;\n'
                 'if (offer) bru.setVar("offerId", offer.id);\n'
                 'console.log("Статус:", res.body.status, "| ожидается:", bru.getEnvVar("expectedOutcome"));',
            expect=200,
            docs="Повторяйте, пока статус не станет итоговым. Ожидаемый исход — переменная `expectedOutcome` окружения.\n"
                 "Live-обновления (SSE) Bruno не показывает — см. `docs/test-scenarios.md`, раздел про SSE.")),
        ("10 История заявки", bru("История заявки", 10, "get",
            "{{baseUrl}}/api/applications/{{applicationId}}/history", expect=200,
            docs="Аудит: каждый переход статуса — отдельное событие.")),
        ("11 Принять предложение", bru("Принять предложение", 11, "post",
            "{{baseUrl}}/api/applications/{{applicationId}}/accept-offer",
            body='{\n  "expected_revision": {{revision}},\n  "offer_id": "{{offerId}}"\n}',
            post=KEEP_REVISION, expect=200, docs="Для APPROVED и COUNTER_OFFER.")),
        ("12 Подписать договор", bru("Подписать договор", 12, "post",
            "{{baseUrl}}/api/applications/{{applicationId}}/sign",
            body='{\n  "expected_revision": {{revision}},\n  "offer_id": "{{offerId}}",\n  "consent": true\n}',
            post=KEEP_REVISION, expect=200, docs="Симуляция подписи. Затем запрос 09 покажет SIGNED → ISSUED.")),
        ("13 Загрузить документ", bru("Загрузить документ", 13, "post",
            "{{baseUrl}}/api/applications/{{applicationId}}/documents",
            multipart={"document_type": "bank_statement_12m", "expected_revision": "{{revision}}",
                       "file": "@file(fixtures/bank-statement-demo.pdf)"},
            post='if (res.body && res.body.id) bru.setVar("documentId", res.body.id);\n' + KEEP_REVISION,
            expect=201, docs="Для NEEDS_INFO (demo-info).")),
        ("14 Дополнить данные", bru("Дополнить данные", 14, "post",
            "{{baseUrl}}/api/applications/{{applicationId}}/additional-info",
            body='{\n  "expected_revision": {{revision}},\n  "annual_revenue": "{{correctedRevenue}}",\n'
                 '  "comment": "Уточнила выручку по данным бухгалтерии",\n  "document_ids": []\n}',
            post=KEEP_REVISION, expect=202,
            docs="demo-info: выручка 9 400 000 ₽ → заявка уходит на повторную проверку и одобряется.")),
        ("15 Отозвать заявку", bru("Отозвать заявку", 15, "post",
            "{{baseUrl}}/api/applications/{{applicationId}}/cancel",
            body='{\n  "expected_revision": {{revision}},\n  "reason": "Клиент отказался от предложения"\n}',
            post=KEEP_REVISION, expect=200, docs="Отказ от контрпредложения или отзыв до подписания → CANCELLED.")),
        ("16 Мои заявки", bru("Мои заявки", 16, "get", "{{baseUrl}}/api/applications", expect=200)),
        ("17 Выход", bru("Выход", 17, "post", "{{baseUrl}}/api/auth/logout", expect=204)),
    ]
    return reqs


def underwriter_folder():
    reqs = auth_requests(1, "analystLogin", "analystPassword", "аналитик")
    card_post = ('bru.setVar("uwRevision", res.body.application.revision);\n'
                 'bru.setVar("uwProductId", res.body.application.product_id);')
    reqs += [
        ("05 Очередь ручной проверки", bru("Очередь ручной проверки", 5, "get",
            "{{baseUrl}}/api/underwriter/applications?status=MANUAL_REVIEW",
            post='if (res.body.items.length) bru.setVar("uwApplicationId", res.body.items[0].id);', expect=200,
            docs="Сначала проведите клиента demo-manual до MANUAL_REVIEW.")),
        ("06 Карточка заявки", bru("Карточка заявки", 6, "get",
            "{{baseUrl}}/api/underwriter/applications/{{uwApplicationId}}", post=card_post, expect=200,
            docs="Анкета, внешние проверки, факторы скоринга, рекомендация системы.")),
    ]
    decisions = [
        ("07 Решение — одобрить", "APPROVED", {
            "reasons": [], "offer_terms": {"product_id": "{{uwProductId}}", "amount": "8000000.00", "term_months": 24},
            "explanation": "Мы готовы выдать кредит на запрошенных условиях.",
            "analytical_explanation": "Просрочка 45 дней погашена, обороты стабильны; риск отрасли принят."}),
        ("08 Решение — другие условия", "COUNTER_OFFER", {
            "reasons": [{"code": "amount_reduced", "message": "Мы можем выдать меньшую сумму — так ежемесячный платёж будет посильным при Вашей выручке."}],
            "offer_terms": {"product_id": "{{uwProductId}}", "amount": "5000000.00", "term_months": 24},
            "explanation": "Готовы выдать 5 млн ₽ на 24 месяца.",
            "analytical_explanation": "Класс D, строительство: снижаем лимит до 5 млн."}),
        ("09 Решение — запросить данные", "NEEDS_INFO", {
            "reasons": [],
            "required_documents": [{"code": "financial_statements", "title": "Бухгалтерская отчётность за последний год",
                                    "explanation": "Баланс и отчёт о финансовых результатах — чтобы оценить финансовое положение компании."}],
            "explanation": "Чтобы принять решение, нам нужна отчётность компании.",
            "analytical_explanation": "Нужна отчётность для оценки долговой нагрузки."}),
        ("10 Решение — отказать", "REJECTED", {
            "reasons": [{"code": "high_risk", "message": "По совокупности данных о компании риск оказался выше, чем мы можем принять сейчас."}],
            "explanation": "Сейчас мы не можем выдать кредит.",
            "analytical_explanation": "Просрочка + 6 запросов в БКИ за 30 дней — признаки долговой нагрузки."}),
    ]
    for i, (name, outcome, extra) in enumerate(decisions, start=7):
        body = {"expected_revision": "__REV__", "outcome": outcome, **extra}
        text = json.dumps(body, ensure_ascii=False, indent=2).replace('"__REV__"', "{{uwRevision}}")
        reqs.append((name, bru(name[3:], i, "post",
            "{{baseUrl}}/api/underwriter/applications/{{uwApplicationId}}/decision",
            body=text, expect=200, docs="Выполните одно из четырёх решений. Клиент увидит его по SSE.")))
    return reqs


def negative_folder():
    reqs = auth_requests(1, "clientLogin", "clientPassword", "клиент")
    reqs += [
        ("05 Клиент не видит очередь банка", bru("Клиент не видит очередь банка", 5, "get",
            "{{baseUrl}}/api/underwriter/applications", expect=403)),
        ("06 ИНН неверного формата", bru("ИНН неверного формата", 6, "get",
            "{{baseUrl}}/api/companies/lookup?inn=123", expect=422)),
        ("07 Изменение без CSRF-токена", bru("Изменение без CSRF-токена", 7, "post",
            "{{baseUrl}}/api/applications", body="{}", headers={"X-CSRF-Token": None}, expect=403)),
        ("08 Запрос с чужого сайта", bru("Запрос с чужого сайта", 8, "post",
            "{{baseUrl}}/api/applications", body="{}", headers={"Origin": "https://evil.example"}, expect=403,
            docs="Origin не совпадает с AUTH_ALLOWED_ORIGIN — мутация отклоняется даже с валидными cookie и CSRF.")),
        ("09 Черновик для проверки версий", bru("Черновик для проверки версий", 9, "post",
            "{{baseUrl}}/api/applications", body="{}",
            post='bru.setVar("negApplicationId", res.body.id);', expect=201)),
        ("10 Устаревшая версия заявки", bru("Устаревшая версия заявки", 10, "patch",
            "{{baseUrl}}/api/applications/{{negApplicationId}}",
            body='{\n  "expected_revision": 999,\n  "term_months": 12\n}', expect=409,
            docs="Защита от одновременного редактирования: ожидаем 409 и текст errors.conflict.")),
        ("11 Выход", bru("Выход", 11, "post", "{{baseUrl}}/api/auth/logout", expect=204)),
        ("12 Без входа", bru("Без входа", 12, "get", "{{baseUrl}}/api/applications", expect=401)),
    ]
    return reqs


def environments():
    companies = json.loads((ROOT / "seed/companies.json").read_text(encoding="utf-8"))["companies"]
    accounts = json.loads((ROOT / "seed/accounts.json").read_text(encoding="utf-8"))
    analyst = accounts["underwriters"][0]
    passwords = {c["login"]: c["password"] for c in accounts["clients"]}
    envs = {}
    for c in companies:
        app = c["application"]
        corrected = "9400000.00" if c["client_login"] == "demo-info" else app["annual_revenue"]
        outcome = c["expected_outcome"]
        outcome = outcome if isinstance(outcome, str) else outcome.get("outcome", json.dumps(outcome, ensure_ascii=False))
        values = {
            "baseUrl": "http://localhost:8080",
            "origin": "http://localhost:8080",
            "clientLogin": c["client_login"],
            "clientPassword": passwords[c["client_login"]],
            "analystLogin": analyst["login"],
            "analystPassword": analyst["password"],
            "inn": c["company"]["inn"],
            "productCode": app["product_code"],
            "purpose": app["purpose"],
            "amount": app["amount"],
            "termMonths": app["term_months"],
            "annualRevenue": app["annual_revenue"],
            "correctedRevenue": corrected,
            "expectedOutcome": outcome,
        }
        envs[c["client_login"]] = values
    return envs


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main():
    for sub in ("01 Клиент", "02 Аналитик", "03 Негативные проверки", "environments"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
    write(OUT / "bruno.json", json.dumps({"version": "1", "name": "Кредитный кабинет — Alfa Hack", "type": "collection",
                                          "ignore": ["node_modules", ".git", "generate.py"]},
                                         ensure_ascii=False, indent=2) + "\n")
    folders = [("01 Клиент", client_folder()), ("02 Аналитик", underwriter_folder()),
               ("03 Негативные проверки", negative_folder())]
    for i, (folder, reqs) in enumerate(folders, start=1):
        write(OUT / folder / "folder.bru", f"meta {{\n  name: {folder}\n  seq: {i}\n}}\n")
        for fname, text in reqs:
            write(OUT / folder / f"{fname}.bru", text)
    for login, values in environments().items():
        body = "vars {\n" + "".join(f"  {k}: {v}\n" for k, v in values.items()) + "}\n"
        write(OUT / "environments" / f"{login}.bru", body)
        prism = body.replace("http://localhost:8080", "http://127.0.0.1:4010")
        if login == "demo-approve":
            write(OUT / "environments" / "prism-mock.bru", prism)


if __name__ == "__main__":
    main()

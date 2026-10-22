"""Сквозной прогон «как жюри» через API: все синтетические компании, решения аналитика, негативные проверки.

Запуск из корня репо на поднятом стенде (docker compose up --build --wait):

    python3 api-collection/run_e2e.py                 # всё
    python3 api-collection/run_e2e.py demo-approve    # одна компания
    python3 api-collection/run_e2e.py --markdown      # плюс строка для журнала в docs/test-scenarios.md

Только стандартная библиотека Python 3.10+. Каждый запуск создаёт новые заявки — повторять можно сколько угодно.
Ожидаемые исходы — seed/companies.json и docs/test-data.md.
"""

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
ORIGIN = os.environ.get("E2E_ORIGIN", BASE)
MOCKS = os.environ.get("E2E_MOCKS_URL", "http://localhost:8001")
DECISION_TIMEOUT = float(os.environ.get("E2E_TIMEOUT", "90"))

FINAL = {"APPROVED", "COUNTER_OFFER", "NEEDS_INFO", "MANUAL_REVIEW", "REJECTED", "CANCELLED", "ISSUED"}

# Коды причин из docs/test-data.md (подмножество, которое обязано присутствовать).
EXPECTED_REASONS = {
    "demo-term": {"term_extended"},
    "demo-amount": {"amount_reduced"},
    "demo-product": {"product_changed", "collateral_required"},
    "demo-stop": {"company_liquidating"},
    "demo-risk": {"high_risk"},
}
EXPECTED_DOCS = {"demo-info": {"annual_revenue", "bank_statement_12m"}}


class HTTPError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status, self.body = status, body


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None

    def call(self, method, path, body=None, *, headers=None, base=BASE, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Accept": "application/json"}
        if data is not None:
            h["Content-Type"] = "application/json"
        if method != "GET":
            h["Origin"] = ORIGIN
            if self.csrf:
                h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        req = urllib.request.Request(base + path, data=data, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=30) as resp:
                text = resp.read().decode() or "null"
                return resp.status if raw else json.loads(text)
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            try:
                payload = json.loads(text)
            except ValueError:
                payload = text
            raise HTTPError(e.code, payload) from None

    def refresh_csrf(self):
        self.csrf = self.call("GET", "/api/auth/csrf")["csrf_token"]

    def login(self, login, password):
        self.refresh_csrf()
        self.call("POST", "/api/auth/login", {"login": login, "password": password})
        self.refresh_csrf()  # старый токен после входа недействителен
        return self.call("GET", "/api/auth/me")


def expect_status(code, fn):
    try:
        fn()
    except HTTPError as e:
        if e.status == code:
            return True
        raise AssertionError(f"ожидали {code}, получили {e.status}: {e.body}") from None
    raise AssertionError(f"ожидали {code}, получили 2xx")


def wait_status(client, app_id, targets, timeout=DECISION_TIMEOUT):
    seen, start = [], time.monotonic()
    while time.monotonic() - start < timeout:
        app = client.call("GET", f"/api/applications/{app_id}")
        if not seen or seen[-1] != app["status"]:
            seen.append(app["status"])
        if app["status"] in targets:
            return app, seen, time.monotonic() - start
        time.sleep(0.5)
    raise AssertionError(f"нет статуса {sorted(targets)} за {timeout:.0f} с, путь: {' → '.join(seen)}")


def reason_codes(app):
    decision = app.get("decision") or {}
    return {r["code"] for r in decision.get("reasons") or []}


def doc_codes(app):
    decision = app.get("decision") or {}
    return {d["code"] for d in decision.get("required_documents") or []}


def submit(client, company, products):
    a = company["application"]
    product_id = next(p["id"] for p in products if p["code"] == a["product_code"])
    found = client.call("GET", f"/api/companies/lookup?inn={company['company']['inn']}")["company"]
    assert found and found["inn"] == company["company"]["inn"], "автозаполнение по ИНН не вернуло компанию"
    draft = client.call("POST", "/api/applications", {
        "product_id": product_id, "purpose": a["purpose"], "amount": a["amount"],
        "term_months": a["term_months"], "annual_revenue": a["annual_revenue"], "company": found,
    })
    app = client.call("POST", f"/api/applications/{draft['id']}/submit",
                      {"expected_revision": draft["revision"], "consent_to_checks": True},
                      headers={"Idempotency-Key": str(uuid.uuid4())})
    return app


def accept_and_sign(client, app):
    offer_id = app["decision"]["offer"]["id"]
    app = client.call("POST", f"/api/applications/{app['id']}/accept-offer",
                      {"expected_revision": app["revision"], "offer_id": offer_id})
    app = client.call("POST", f"/api/applications/{app['id']}/sign",
                      {"expected_revision": app["revision"], "offer_id": offer_id, "consent": True})
    app, _, _ = wait_status(client, app["id"], {"ISSUED"}, timeout=30)
    return app


def underwriter_counter_offer(app_id):
    analyst = Client()
    accounts = json.loads((ROOT / "seed/accounts.json").read_text())["underwriters"][0]
    analyst.login(accounts["login"], accounts["password"])
    queue = analyst.call("GET", "/api/underwriter/applications?status=MANUAL_REVIEW")
    assert any(i["id"] == app_id for i in queue["items"]), "заявки нет в очереди аналитика"
    card = analyst.call("GET", f"/api/underwriter/applications/{app_id}")
    assert card["decisions"], "в карточке аналитика нет решения системы с факторами"
    application = card["application"]
    analyst.call("POST", f"/api/underwriter/applications/{app_id}/decision", {
        "expected_revision": application["revision"], "outcome": "COUNTER_OFFER",
        "reasons": [{"code": "amount_reduced",
                     "message": "Мы можем выдать меньшую сумму — так ежемесячный платёж будет посильным при Вашей выручке."}],
        "offer_terms": {"product_id": application["product_id"], "amount": "5000000.00", "term_months": 24},
        "explanation": "Готовы выдать 5 млн ₽ на 24 месяца.",
        "analytical_explanation": "E2E: класс D, снижаем лимит до 5 млн.",
    })


def run_company(company, password):
    login, expected = company["client_login"], company["expected_outcome"]
    client = Client()
    client.login(login, password)
    products = client.call("GET", "/api/products")["items"]
    if login == "demo-bki":
        try:
            Client().call("POST", "/admin/reset", base=MOCKS, raw=True)
        except Exception:  # noqa: BLE001 — сброс счётчика не обязателен
            pass
    app = submit(client, company, products)
    app, path, took = wait_status(client, app["id"], FINAL - {"CANCELLED", "ISSUED"})
    notes = [f"{' → '.join(path)} за {took:.1f} с"]
    assert app["status"] == expected, f"исход {app['status']}, ожидали {expected}"
    missing = EXPECTED_REASONS.get(login, set()) - reason_codes(app)
    assert not missing, f"нет причин {sorted(missing)}, есть {sorted(reason_codes(app))}"
    missing = EXPECTED_DOCS.get(login, set()) - doc_codes(app)
    assert not missing, f"нет запросов документов {sorted(missing)}"
    if login == "demo-bki":
        assert "CHECKING" in path or took > 2, "не видно ожидания в CHECKING при сбое БКИ"

    if expected == "NEEDS_INFO":
        after = company["expected_after_info"]
        app = client.call("POST", f"/api/applications/{app['id']}/additional-info", {
            "expected_revision": app["revision"], "comment": "E2E: уточнённая выручка",
            "document_ids": [], **after["change"],
        })
        app, path, took = wait_status(client, app["id"], FINAL - {"NEEDS_INFO", "CANCELLED", "ISSUED"})
        assert app["status"] == after["outcome"], f"после дозагрузки {app['status']}, ожидали {after['outcome']}"
        notes.append(f"после данных: {' → '.join(path)}")
    if expected == "MANUAL_REVIEW":
        underwriter_counter_offer(app["id"])
        app, _, took = wait_status(client, app["id"], {"COUNTER_OFFER"}, timeout=15)
        notes.append(f"аналитик → COUNTER_OFFER, клиент увидел за {took:.1f} с")
    if login == "demo-amount":
        app = client.call("POST", f"/api/applications/{app['id']}/cancel",
                          {"expected_revision": app["revision"], "reason": "E2E: отказ от предложения"})
        assert app["status"] == "CANCELLED", f"после отказа {app['status']}"
        notes.append("отказ → CANCELLED")
    elif app["status"] in {"APPROVED", "COUNTER_OFFER"}:
        app = accept_and_sign(client, app)
        notes.append("принято → подписано → ISSUED")

    history = client.call("GET", f"/api/applications/{app['id']}/history")["items"]
    statuses = [e["status"] for e in history if e.get("event_type") == "status_changed"]
    assert app["status"] in statuses, f"в истории нет текущего статуса {app['status']}: {statuses}"
    notes.append(f"аудит: {len(history)} событий")
    return "; ".join(notes)


def run_negative(company, password):
    c = Client()
    expect_status(401, lambda: c.call("GET", "/api/applications"))
    c.login(company["client_login"], password)
    expect_status(403, lambda: c.call("GET", "/api/underwriter/applications"))
    expect_status(422, lambda: c.call("GET", "/api/companies/lookup?inn=123"))
    expect_status(403, lambda: c.call("POST", "/api/applications", {}, headers={"X-CSRF-Token": None}))
    expect_status(403, lambda: c.call("POST", "/api/applications", {}, headers={"Origin": "https://evil.example"}))
    draft = c.call("POST", "/api/applications", {})
    expect_status(409, lambda: c.call("PATCH", f"/api/applications/{draft['id']}",
                                      {"expected_revision": draft["revision"] + 100, "term_months": 12}))
    other = str(uuid.uuid4())
    expect_status(404, lambda: c.call("GET", f"/api/applications/{other}"))
    c.call("POST", "/api/auth/logout", raw=True)
    expect_status(401, lambda: c.call("GET", "/api/auth/me"))
    return "401 · 403 роль · 422 ИНН · 403 без CSRF · 403 чужой Origin · 409 версия · 404 · logout"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logins", nargs="*", help="логины компаний (по умолчанию все)")
    parser.add_argument("--markdown", action="store_true", help="напечатать строку для журнала прогонов")
    args = parser.parse_args()

    companies = json.loads((ROOT / "seed/companies.json").read_text())["companies"]
    passwords = {a["login"]: a["password"] for a in json.loads((ROOT / "seed/accounts.json").read_text())["clients"]}
    if args.logins:
        companies = [c for c in companies if c["client_login"] in args.logins]

    try:
        Client().call("GET", "/api/auth/csrf")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Стенд недоступен на {BASE}: {e}. Запустите docker compose up --build --wait")

    results = []
    jobs = [(c["client_login"], lambda c=c: run_company(c, passwords[c["client_login"]])) for c in companies]
    if not args.logins:
        jobs.append(("negative", lambda: run_negative(companies[0], passwords[companies[0]["client_login"]])))
    for name, job in jobs:
        start = time.monotonic()
        try:
            note, ok = job(), True
        except Exception as e:  # noqa: BLE001
            note, ok = f"{type(e).__name__}: {e}", False
        results.append((name, ok, note, time.monotonic() - start))
        print(f"{'✅' if ok else '❌'} {name:<13} {time.monotonic() - start:5.1f} с  {note}", flush=True)

    failed = [r for r in results if not r[1]]
    print(f"\nИтого: {len(results) - len(failed)}/{len(results)} прошли")
    if args.markdown:
        marks = " · ".join(f"{n} {'✅' if ok else '❌'}" for n, ok, _, _ in results)
        print(f"\n| {time.strftime('%d.%m %H:%M')} | API, run_e2e.py | {marks} |")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

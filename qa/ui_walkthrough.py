"""Прогон интерфейса «как жюри» в браузере (Playwright) со скриншотами каждого шага.

Запуск на поднятом стенде:
    pip install playwright==1.48.0 && python -m playwright install --with-deps chromium
    python3 qa/ui_walkthrough.py            # скриншоты в qa/screenshots/

Тексты кнопок и полей берутся из docs/product/ui-texts.ru.json — тот же источник, что у фронтенда.
"""

import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8080"
OUT = ROOT / "qa/screenshots"
T = json.loads((ROOT / "docs/product/ui-texts.ru.json").read_text())
COMPANIES = {c["client_login"]: c for c in json.loads((ROOT / "seed/companies.json").read_text())["companies"]}
PASSWORD = "demo-2026"
DECISION_TIMEOUT = 90_000

results: list[tuple[str, bool, str]] = []


def shot(page: Page, name: str):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)


def login(page: Page, login: str, password: str):
    page.goto(f"{BASE}/login")
    page.get_by_label(T["login"]["loginLabel"]).fill(login)
    page.get_by_label(T["login"]["passwordLabel"]).fill(password)
    page.get_by_role("button", name=T["login"]["submit"]).click()
    page.wait_for_url(re.compile(r"/(client|bank)"))


def apply(page: Page, company: dict, prefix: str):
    a = company["application"]
    page.goto(f"{BASE}/client")
    page.get_by_role("link", name=T["applications"]["newApplication"]).first.click()
    page.get_by_text(T["purposes"][a["purpose"]]["label"], exact=True).click()
    product_name = T["products"][a["product_code"]]["name"]
    card = page.locator("div", has=page.get_by_role("heading", name=product_name, exact=True)).filter(
        has=page.get_by_role("button", name=T["productPicker"]["choose"])).last
    if prefix:
        shot(page, f"{prefix}-02-product-picker")
    card.get_by_role("button", name=T["productPicker"]["choose"]).click()
    page.wait_for_url(re.compile(r"/edit$"))
    page.get_by_label(T["form"]["inn"]["label"]).fill(company["company"]["inn"])
    expect(page.get_by_text(T["form"]["autofillNote"])).to_be_visible(timeout=15_000)
    page.get_by_label(T["form"]["amount"]["label"]).fill(str(int(float(a["amount"]))))
    page.get_by_label(T["form"]["term"]["label"]).fill(str(a["term_months"]))
    page.get_by_label(T["form"]["annualRevenue"]["label"]).fill(str(int(float(a["annual_revenue"]))))
    if prefix:
        shot(page, f"{prefix}-03-form-autofilled")
    page.get_by_role("button", name=T["form"]["submit"]).click()
    page.wait_for_url(re.compile(r"/client/applications/[0-9a-f-]+$"))


def wait_title(page: Page, status: str, timeout=DECISION_TIMEOUT):
    expect(page.get_by_text(T["statuses"][status]["title"]).first).to_be_visible(timeout=timeout)


def step(name: str, fn):
    start = time.monotonic()
    try:
        fn()
        results.append((name, True, f"{time.monotonic() - start:.1f} с"))
    except Exception as e:  # noqa: BLE001
        results.append((name, False, f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"))
    print(("✅" if results[-1][1] else "❌"), name, results[-1][2], flush=True)


def scenario_approve(browser):
    page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="ru-RU")
    page.goto(f"{BASE}/login")
    shot(page, "01-login")
    login(page, "demo-approve", PASSWORD)
    shot(page, "approve-01-applications")
    apply(page, COMPANIES["demo-approve"], "approve")
    shot(page, "approve-04-status-in-progress")
    wait_title(page, "APPROVED")
    shot(page, "approve-05-decision-approved")
    page.get_by_role("button", name=T["decision"]["approved"]["accept"]).click()
    page.wait_for_url(re.compile(r"/sign$"))
    shot(page, "approve-06-sign")
    page.get_by_label(T["signing"]["confirmCheckbox"]).check()
    page.get_by_role("button", name=T["signing"]["sign"]).click()
    wait_title(page, "ISSUED", timeout=30_000)
    shot(page, "approve-07-issued")
    page.close()


def scenario_outcome(browser, login_name: str, status: str):
    page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="ru-RU")
    login(page, login_name, PASSWORD)
    apply(page, COMPANIES[login_name], "")
    wait_title(page, status)
    shot(page, f"{login_name}-decision-{status.lower()}")
    page.close()


def scenario_bki(browser):
    import urllib.request
    urllib.request.urlopen(urllib.request.Request("http://localhost:8001/admin/reset", method="POST"), timeout=5)
    page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="ru-RU")
    login(page, "demo-bki", PASSWORD)
    apply(page, COMPANIES["demo-bki"], "")
    expect(page.get_by_text(T["statuses"]["CHECKING"]["slow"])).to_be_visible(timeout=30_000)
    shot(page, "demo-bki-checking-slow")
    wait_title(page, "APPROVED")
    shot(page, "demo-bki-decision-approved")
    page.close()


def scenario_manual(browser):
    client = browser.new_page(viewport={"width": 1280, "height": 900}, locale="ru-RU")
    login(client, "demo-manual", PASSWORD)
    apply(client, COMPANIES["demo-manual"], "")
    wait_title(client, "MANUAL_REVIEW")
    shot(client, "manual-01-client-waiting")
    bank = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ru-RU").new_page()
    login(bank, "analyst", "analyst-2026")
    shot(bank, "manual-02-bank-queue")
    bank.get_by_role("row").filter(has_text=COMPANIES["demo-manual"]["company"]["name"]).first.click()
    bank.wait_for_url(re.compile(r"/bank/applications/"))
    bank.wait_for_load_state("networkidle")
    shot(bank, "manual-03-bank-card")
    bank.get_by_label(T["bank"]["decision"]["counterOffer"]).check()
    amount = bank.get_by_label(T["decision"]["terms"]["amount"])
    amount.fill("5000000")
    bank.get_by_label(T["decision"]["terms"]["term"]).fill("24")
    reason = bank.get_by_label(T["counterOfferReasons"]["amount_reduced"])
    if reason.count():
        reason.first.check()
    bank.get_by_label(T["bank"]["decision"]["auditCommentLabel"]).fill(
        "QA: класс D, строительство — снижаем лимит до 5 млн ₽.")
    shot(bank, "manual-04-bank-decision-form")
    bank.get_by_role("button", name=T["bank"]["decision"]["submit"]).click()
    expect(bank.get_by_text(T["bank"]["decision"]["done"])).to_be_visible(timeout=15_000)
    wait_title(client, "COUNTER_OFFER", timeout=30_000)
    shot(client, "manual-05-client-sees-counter-offer")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        step("TS-01 одобрение → подписание → выдача (UI)", lambda: scenario_approve(context))
        step("TS-06 ручная проверка: клиент ↔ аналитик (UI)", lambda: scenario_manual(context))
        for login_name, status in [("demo-term", "COUNTER_OFFER"), ("demo-amount", "COUNTER_OFFER"),
                                   ("demo-product", "COUNTER_OFFER"), ("demo-info", "NEEDS_INFO"),
                                   ("demo-stop", "REJECTED"), ("demo-risk", "REJECTED")]:
            step(f"{login_name}: экран {status} (UI)",
                 lambda ln=login_name, st=status: scenario_outcome(browser.new_context(), ln, st))
        step("TS-09 сбой БКИ: «отвечает дольше обычного» → одобрение (UI)", lambda: scenario_bki(browser.new_context()))
        browser.close()
    failed = [r for r in results if not r[1]]
    print(f"\nUI: {len(results) - len(failed)}/{len(results)} прошли, скриншоты: {OUT}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

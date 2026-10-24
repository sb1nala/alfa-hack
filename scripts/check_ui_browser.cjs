// Working SPA smoke: real controls, no injected API calls or page reloads.
const fs = require("fs");
const path = require("path");
const texts = JSON.parse(
  fs.readFileSync(path.join(__dirname, "../docs/product/ui-texts.ru.json"), "utf8"),
);

module.exports = async function checkUI(browser, cfg) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    let streams = 0;
    let openStreams = 0;
    let reads = 0;
    let documents = 0;
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (request.resourceType() === "document") documents++;
      if (url.pathname.endsWith("/events") && request.resourceType() === "eventsource") streams++;
      if (request.method() === "GET" && /^\/api\/applications\/[^/]+$/.test(url.pathname)) reads++;
    });
    page.on("response", (response) => {
      if (new URL(response.url()).pathname.endsWith("/events") &&
          response.request().resourceType() === "eventsource" && response.ok() &&
          (response.headers()["content-type"] || "").startsWith("text/event-stream")) openStreams++;
    });
    await page.goto(cfg.origin, { waitUntil: "networkidle" });
    // MUI adds the required-field asterisk to these accessible labels.
    await page.getByLabel(texts.login.loginLabel).fill("demo-approve");
    await page.getByLabel(texts.login.passwordLabel).fill("demo-2026");
    await page.getByRole("button", { name: texts.login.submit, exact: true }).click();
    await page.getByRole("link", { name: texts.applications.newApplication, exact: true }).click();
    await page.getByRole("radio", { name: /Закупить товар или сырьё/ }).click();
    // Product buttons are named "Выбрать: <продукт>"; the first one is the recommended product.
    await page.getByRole("button", { name: new RegExp(`^${texts.productPicker.choose}: `) }).first().click();
    await page.getByLabel(texts.form.inn.label, { exact: true }).fill(cfg.record.company.inn);
    await page.getByText(texts.form.autofillNote, { exact: true }).waitFor();
    // UI fields accept whole rubles; seed/API money strings include .00.
    await page.getByLabel(texts.form.amount.label, { exact: true }).fill(cfg.record.application.amount.split(".")[0]);
    await page.getByLabel(texts.form.term.label, { exact: true }).fill(String(cfg.record.application.term_months));
    await page.getByLabel(texts.form.annualRevenue.label, { exact: true }).fill(cfg.record.application.annual_revenue.split(".")[0]);
    await page.getByRole("button", { name: texts.form.submit, exact: true }).click();
    await page.getByRole("button", { name: texts.decision.approved.accept, exact: true }).waitFor({ timeout: 30000 });
    await page.getByRole("button", { name: texts.decision.approved.accept, exact: true }).click();
    await page.getByRole("checkbox", { name: texts.signing.confirmCheckbox, exact: true }).check();
    await page.getByRole("button", { name: texts.signing.sign, exact: true }).click();
    await page.getByRole("heading", { name: texts.statuses.ISSUED.title, exact: true }).waitFor({ timeout: 30000 });
    if (documents !== 1) throw Error("UI flow unexpectedly reloaded a document");
    console.log("PASS working SPA: login, picker, INN, submit, decision, accept, sign, ISSUED without reload");
    console.log(`UI transport observed: ${streams} EventSource requests, ${reads} card GETs; ` +
      (streams ? "SSE requests observed; resilience acceptance remains separate" : "polling; frontend SSE acceptance remains BLOCKED"));
    if (process.env.REQUIRE_UI_SSE === "1" && (streams === 0 || openStreams === 0))
      throw Error("Working React UI did not establish EventSource /events; frontend SSE acceptance is BLOCKED (#6)");
  } finally {
    await context.close();
  }
};

if (require.main === module) {
  (async () => {
    const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
    const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
    const browser = await chromium.launch({
      ...(process.env.CHROMIUM_EXECUTABLE
        ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
        : {}),
      headless: true,
      args: ["--no-sandbox"],
    });
    try {
      await module.exports(browser, cfg);
    } finally {
      await browser.close();
    }
  })().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

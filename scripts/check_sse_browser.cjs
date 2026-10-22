const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs"),
  { execFileSync } = require("child_process");
const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const assert = (x, m) => {
  if (!x) throw Error(m);
};
const compose = (...args) =>
  execFileSync("docker", [...cfg.compose, ...args], {
    env: { ...process.env, ...cfg.env },
    stdio: "pipe",
    timeout: 45000,
  });
(async () => {
  const browser = await chromium.launch({
    ...(process.env.CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
      : {}),
    headless: true,
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage();
    let lastHeader;
    page.on("request", async (r) => {
      if (r.url().endsWith("/events")) {
        let h = await r.allHeaders();
        if (h["last-event-id"]) lastHeader = h["last-event-id"];
        assert(!h.authorization && !h["x-csrf-token"], "cookie GET");
      }
    });
    await page.goto(cfg.origin, { waitUntil: "networkidle", timeout: 30000 });
    const app = await page.evaluate(async (record) => {
      window.call = async (path, body, extra = {}) => {
        const deadline = Date.now() + 20000;
        while (true) {
          const r = await fetch("/api/" + path, {
            method: body ? "POST" : "GET",
            headers: body
              ? {
                  "Content-Type": "application/json",
                  "X-CSRF-Token": window.csrf,
                  ...extra,
                }
              : {},
            body: body ? JSON.stringify(body) : undefined,
          });
          if (r.ok) return r.json();
          // A recreated backend can leave different nginx workers with different
          // DNS cache ages. Recover idempotent reads with a bounded backoff.
          if (body || r.status < 500 || Date.now() >= deadline)
            throw Error(path + ": " + r.status);
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
      };
      window.csrf = (await call("auth/csrf")).csrf_token;
      await call("auth/login", {
        login: "demo-approve",
        password: "demo-2026",
      });
      window.csrf = (await call("auth/csrf")).csrf_token;
      const products = (await call("products")).items,
        { product_code, ...form } = record.application;
      const app = await call("applications", {
        ...form,
        company: record.company,
        product_id: products.find((p) => p.code === product_code).id,
      });
      window.events = [];
      window.opens = 0;
      window.errors = 0;
      window.cards = [];
      window.connect = () => {
        window.source = new EventSource(
          "/api/applications/" + app.id + "/events",
        );
        source.onopen = async () => {
          opens++;
          cards.push(await call("applications/" + app.id));
        };
        source.onerror = () => errors++;
        for (const t of [
          "status_changed",
          "application_updated",
          "decision_created",
          "document_uploaded",
        ])
          source.addEventListener(t, (e) =>
            events.push({
              id: e.lastEventId,
              type: t,
              data: JSON.parse(e.data),
              at: Date.now(),
            }),
          );
      };
      connect();
      return app;
    }, cfg.record);
    await page.waitForFunction(() => opens === 1 && cards.length === 1, null, {
      timeout: 5000,
    });
    assert((await page.evaluate(() => events)).length === 0, "no history");
    let start = Date.now();
    await page.evaluate(
      (app) =>
        call(
          "applications/" + app.id + "/submit",
          { expected_revision: app.revision, consent_to_checks: true },
          { "Idempotency-Key": crypto.randomUUID() },
        ),
      app,
    );
    await page.waitForFunction(
      () => events.some((e) => e.data.status === "APPROVED"),
      null,
      { timeout: 25000 },
    );
    const events = await page.evaluate(() => window.events);
    assert(events[0].at - start < 3000, "unbuffered frame");
    assert(
      JSON.stringify(events.map((e) => e.data.status)) ===
        '["SUBMITTED","CHECKING","SCORING","APPROVED"]',
      "pipeline",
    );
    assert(
      events.every(
        (e) =>
          e.type === e.data.event_type &&
          !("data" in e.data) &&
          !("risk_result" in e.data),
      ),
      "public SSEData",
    );
    assert(
      (await page.evaluate((id) => call("applications/" + id), app.id)).decision
        .outcome === "APPROVED",
      "decision",
    );
    console.log(
      "PASS native Chromium EventSource, cookie, nginx, real pipeline; first frame " +
        (events[0].at - start) +
        "ms",
    );
    const heartbeat = await page.evaluate(async (id) => {
      const ctrl = new AbortController(),
        timer = setTimeout(() => ctrl.abort(), 19000);
      try {
        const r = await fetch("/api/applications/" + id + "/events", {
            signal: ctrl.signal,
          }),
          reader = r.body.getReader(),
          dec = new TextDecoder();
        let text = "",
          start = Date.now();
        while (true) {
          let chunk = await reader.read();
          if (chunk.done) throw Error("EOF");
          text += dec.decode(chunk.value);
          if (text.includes(": heartbeat\n\n")) {
            await reader.cancel();
            return {
              text,
              elapsed: Date.now() - start,
              cache: r.headers.get("cache-control"),
              type: r.headers.get("content-type"),
            };
          }
        }
      } finally {
        clearTimeout(timer);
        ctrl.abort();
      }
    }, app.id);
    assert(
      heartbeat.text === "retry: 3000\n\n: heartbeat\n\n" &&
        heartbeat.elapsed >= 14000 &&
        heartbeat.elapsed < 18500,
      "heartbeat",
    );
    console.log(
      "PASS nginx heartbeat " +
        heartbeat.elapsed +
        "ms, " +
        heartbeat.type +
        ", " +
        heartbeat.cache,
    );
    const last = events.at(-1).id;
    compose("restart", "-t", "15", "backend");
    await page.waitForFunction(() => opens >= 2 && cards.length >= 2, null, {
      timeout: 30000,
    });
    assert(lastHeader === last, "native Last-Event-ID");
    assert(
      (await page.evaluate(() => cards.at(-1).decision.outcome)) === "APPROVED",
      "onopen refetch",
    );
    console.log(
      "PASS native reconnect after backend restart with Last-Event-ID and onopen card",
    );
    compose("up", "-d", "--no-deps", "--force-recreate", "backend");
    // HTTP 502 can close native EventSource permanently. Check API recovery through
    // nginx, restore card/history, then recreate a CLOSED source without headers.
    await page.waitForFunction(
      async (id) => {
        try {
          return (await fetch("/api/applications/" + id)).ok;
        } catch {
          return false;
        }
      },
      app.id,
      { timeout: 30000, polling: 500 },
    );
    await page.evaluate(
      (id) => call("applications/" + id + "/history"),
      app.id,
    );
    await page.waitForFunction(
      () => {
        if (source.readyState === EventSource.CLOSED) {
          source.close();
          connect();
        }
        return opens >= 3 && cards.length >= 3;
      },
      null,
      { timeout: 30000, polling: 500 },
    );
    await page.evaluate(async (id) => {
      const c = await call("applications/" + id);
      await call("applications/" + id + "/cancel", {
        expected_revision: c.revision,
      });
    }, app.id);
    await page.waitForFunction(
      () => events.some((e) => e.data.status === "CANCELLED"),
      null,
      { timeout: 5000 },
    );
    const h = await page.evaluate(
      (id) => call("applications/" + id + "/history"),
      app.id,
    );
    assert(h.items.length === 6, "only saved events");
    const replay = await page.evaluate(
      async ({ id, cursor }) => {
        const r = await fetch("/api/applications/" + id + "/events", {
            headers: { "Last-Event-ID": cursor },
          }),
          reader = r.body.getReader(),
          dec = new TextDecoder();
        let text = "";
        while (!text.includes("event: status_changed"))
          text += dec.decode((await reader.read()).value);
        await reader.cancel();
        return text;
      },
      { id: app.id, cursor: last },
    );
    assert(replay.includes('"status":"CANCELLED"'), "saved replay");
    await page.evaluate(() => source.close());
    console.log(
      "PASS backend recreation, nginx DNS recovery, native Last-Event-ID, onopen card, continued live delivery, saved replay",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});

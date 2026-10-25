"""B10: unmodified Compose build, product API, persistence and native EventSource.

No frontend regeneration or test overrides. Requires Node/Playwright/Chromium for
the existing B09 browser check. Deep lease/fencing checks remain in check_docker.py.
"""

import importlib.util
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

from check_docker import free_port

ROOT = Path(__file__).resolve().parents[1]


def load_e2e():
    spec = importlib.util.spec_from_file_location("product_e2e", ROOT / "api-collection/run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_persistence(compose, env, origin):
    """Fill the gaps in the product runner; reuse its HTTP client and submit logic."""
    os.environ.update(E2E_BASE_URL=origin, E2E_ORIGIN=origin)
    e2e = load_e2e()

    def run(*args):
        return subprocess.check_output([*compose, *args], env=env, cwd=ROOT, timeout=180)

    def sql(statement):
        return (
            run(
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                env["POSTGRES_USER"],
                "-d",
                env["POSTGRES_DB"],
                "-v",
                "ON_ERROR_STOP=1",
                "-Atc",
                statement,
            )
            .decode()
            .strip()
        )

    client = e2e.Client()
    accounts = json.loads((ROOT / "seed/accounts.json").read_text())["clients"]
    account = next(a for a in accounts if a["login"] == "demo-info")
    client.login(account["login"], account["password"])
    products = client.call("GET", "/api/products")["items"]
    record = next(
        c
        for c in json.loads((ROOT / "seed/companies.json").read_text())["companies"]
        if c["client_login"] == "demo-info"
    )
    app = e2e.submit(client, record, products)
    app, _, _ = e2e.wait_status(client, app["id"], {"NEEDS_INFO"})
    path = "/api/applications/" + app["id"]
    boundary = "b10-" + secrets.token_hex(16)
    content = b"%PDF-1.4\n% Synthetic B10 bank statement\n%%EOF\n"
    fields = {
        "document_type": "bank_statement_12m",
        "expected_revision": str(app["revision"]),
    }
    multipart = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    )
    multipart += (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="synthetic.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = Request(
        origin + path + "/documents",
        data=multipart,
        headers={
            "Origin": origin,
            "X-CSRF-Token": client.csrf,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
    )
    with client.opener.open(request, timeout=10) as response:
        assert response.status == 201
        document = json.load(response)
    download = path + "/documents/" + document["id"] + "/download"

    def check_document():
        assert client.call("GET", path + "/documents")["items"] == [document]
        with client.opener.open(origin + download, timeout=10) as response:
            assert response.read() == content, "document content changed"
            assert response.headers.get_content_type() == "application/pdf"

    check_document()
    app = client.call("GET", path)
    app = client.call(
        "POST",
        path + "/additional-info",
        {
            "expected_revision": app["revision"],
            "annual_revenue": "9400000.00",
            "comment": "Synthetic B10 verification",
            "document_ids": [document["id"]],
        },
    )
    app, _, _ = e2e.wait_status(client, app["id"], {"APPROVED"})
    app = e2e.accept_and_sign(client, app)
    history = client.call("GET", path + "/history")
    statuses = [e["status"] for e in history["items"] if e["event_type"] == "status_changed"]
    assert history["items"][0]["status"] == "DRAFT"
    assert history["items"][0]["event_type"] == "application_updated"
    assert statuses == [
        "SUBMITTED",
        "CHECKING",
        "SCORING",
        "NEEDS_INFO",
        "CHECKING",
        "SCORING",
        "APPROVED",
        "OFFER_ACCEPTED",
        "SIGNED",
        "ISSUED",
    ], statuses
    assert any(e["event_type"] == "document_uploaded" for e in history["items"])
    print(
        "PASS document bytes, NEEDS_INFO resubmission, issuance and ordered audit",
        flush=True,
    )

    snapshot_sql = "SELECT " + ", ".join(
        f"(SELECT count(*) FROM {table})"
        for table in (
            "users",
            "companies",
            "products",
            "decisions",
            "offers",
            "documents",
            "application_events",
            "application_submissions",
            "pipeline_jobs",
        )
    )
    before = sql(snapshot_sql)
    # Real down/up, same project/volumes: migrate runs again, all containers get new IDs.
    run("down", "--timeout", "45")
    run("up", "--wait", "--wait-timeout", "120")
    assert sql(snapshot_sql) == before, "seed or business rows duplicated"
    assert client.call("GET", path) == app
    assert client.call("GET", path + "/history") == history
    check_document()
    run("run", "--rm", "migrate")
    assert sql(snapshot_sql) == before, "repeated migrate/seed changed row counts"
    print(
        "PASS down/up with volumes: session, card/offer, audit, file, idempotent seed",
        flush=True,
    )

    run("stop", "db")
    try:
        # Health diagnostics use backend directly; user HTTP flows above use nginx.
        from urllib.error import HTTPError
        from urllib.request import ProxyHandler, build_opener

        opener = build_opener(ProxyHandler({}))
        base = "http://127.0.0.1:" + env["BACKEND_PORT"]
        try:
            opener.open(base + "/health/ready", timeout=8)
        except HTTPError as exc:
            assert exc.code == 503
        else:
            raise AssertionError("readiness did not reflect database outage")
        with opener.open(base + "/health/live", timeout=8) as response:
            assert response.status == 200
    finally:
        run("start", "db")
    deadline = time.monotonic() + 40
    while True:
        try:
            assert client.call("GET", path) == app
            break
        except (e2e.HTTPError, URLError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)
    check_document()
    assert client.call("GET", path + "/history") == history
    assert sql(snapshot_sql) == before
    print(
        "PASS PostgreSQL ready=503/live=200, recovery and preserved product data",
        flush=True,
    )


def main():
    env = os.environ.copy()
    ports = set()
    while len(ports) < 4:
        ports.add(str(free_port()))
    env.update(
        zip(
            ("FRONTEND_PORT", "BACKEND_PORT", "POSTGRES_PORT", "MOCKS_PORT"),
            ports,
            strict=True,
        )
    )
    env.update(
        COMPOSE_PROJECT_NAME="alfa-b10-" + secrets.token_hex(5),
        POSTGRES_HOST="127.0.0.1",
        POSTGRES_DB="alfa_hack",
        POSTGRES_USER="alfa_hack",
        POSTGRES_PASSWORD=secrets.token_urlsafe(24),
        AUTH_JWT_SECRET=secrets.token_urlsafe(48),
        AUTH_COOKIE_SECURE="false",
    )
    origin = "http://127.0.0.1:" + env["FRONTEND_PORT"]
    env.update(
        AUTH_ALLOWED_ORIGIN=origin,
        E2E_BASE_URL=origin,
        E2E_ORIGIN=origin,
        E2E_MOCKS_URL="http://127.0.0.1:" + env["MOCKS_PORT"],
    )
    # Product runner uses urllib's inherited NO_PROXY. Keep external proxy policy.
    env["NO_PROXY"] = env["no_proxy"] = ",".join(
        filter(
            None,
            [
                env.get("NO_PROXY", ""),
                env.get("no_proxy", ""),
                "localhost",
                "127.0.0.1",
            ],
        )
    )
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / ".env.example"),
        "-f",
        str(ROOT / "docker-compose.yml"),
    ]
    if state_path := env.get("PRODUCT_CI_STATE"):
        # Only the CI wrapper reads this private file, never an artifact. Persist
        # before the first Docker call so cancellation can clean up this project.
        with open(state_path, "x", opener=lambda p, f: os.open(p, f, 0o600)) as state:
            json.dump(
                {
                    "compose": compose,
                    "env": {
                        k: v
                        for k, v in env.items()
                        if k.startswith(("POSTGRES_", "AUTH_", "COMPOSE_PROJECT_NAME"))
                        or k in ("FRONTEND_PORT", "BACKEND_PORT", "MOCKS_PORT")
                    },
                },
                state,
            )

    def terminate(_signum, _frame):
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, terminate)

    def run(*args):
        subprocess.run([*compose, *args], cwd=ROOT, env=env, check=True, timeout=900)

    try:
        run("up", "--build", "--wait", "--wait-timeout", "120")
        with tempfile.TemporaryDirectory(prefix="alfa-b10-") as temporary:
            cfg = Path(temporary) / "browser.json"
            cfg.write_text(
                json.dumps(
                    {
                        "compose": compose[1:],
                        "env": {
                            k: env[k]
                            for k in (
                                "COMPOSE_PROJECT_NAME",
                                "POSTGRES_PASSWORD",
                                "AUTH_JWT_SECRET",
                                "AUTH_ALLOWED_ORIGIN",
                                "AUTH_COOKIE_SECURE",
                                "FRONTEND_PORT",
                                "BACKEND_PORT",
                                "POSTGRES_PORT",
                                "MOCKS_PORT",
                            )
                        },
                        "origin": origin,
                        "record": json.loads((ROOT / "seed/companies.json").read_text())[
                            "companies"
                        ][0],
                    }
                )
            )
            cfg.chmod(0o600)
            subprocess.run(
                [sys.executable, str(ROOT / "api-collection/run_e2e.py")],
                env=env,
                check=True,
                timeout=600,
            )
            check_persistence(compose, env, origin)
            subprocess.run(
                ["node", str(ROOT / "scripts/check_sse_browser.cjs"), str(cfg)],
                env=env,
                check=True,
                timeout=360,
            )
        print("PASS Compose/API/persistence/test EventSource/UI smoke; UI SSE acceptance separate")
    except BaseException:
        # The CI wrapper filters these lines before printing or saving them.
        if state_path:
            subprocess.run([*compose, "ps", "-a"], cwd=ROOT, env=env, check=False, timeout=30)
            subprocess.run(
                [*compose, "logs", "--no-color", "--timestamps", "--tail", "150"],
                cwd=ROOT,
                env=env,
                check=False,
                timeout=30,
            )
        raise
    finally:
        run("down", "--volumes", "--remove-orphans", "--timeout", "45")


if __name__ == "__main__":
    main()

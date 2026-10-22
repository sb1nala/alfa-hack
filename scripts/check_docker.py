"""Run real Docker checks in an isolated Compose project, then remove its resources."""

import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    env = os.environ.copy()
    env["BACKEND_PORT"] = str(free_port())
    env["POSTGRES_PORT"] = str(free_port())
    while env["POSTGRES_PORT"] == env["BACKEND_PORT"]:
        env["POSTGRES_PORT"] = str(free_port())
    env["POSTGRES_DB"] = "alfa_hack"
    env["POSTGRES_USER"] = "alfa_hack"
    env["POSTGRES_PASSWORD"] = uuid4().hex
    env["MOCKS_PORT"] = str(free_port())
    env["MOCKS_LATENCY_MS"] = "500"
    env.update(
        WORKER_POLL_INTERVAL="0.1",
        WORKER_HTTP_TIMEOUT="2",
        WORKER_ATTEMPT_TIMEOUT="6",
        WORKER_DB_TIMEOUT="1",
        WORKER_LEASE_SECONDS="12",
        WORKER_RETRY_BASE="0.2",
        WORKER_RETRY_MAX="0.5",
    )
    env["DB_READINESS_TIMEOUT"] = "2"
    env["AUTH_JWT_SECRET"] = secrets.token_urlsafe(48)
    env["AUTH_ALLOWED_ORIGIN"] = f"http://127.0.0.1:{env['BACKEND_PORT']}"
    env["AUTH_COOKIE_SECURE"] = "false"
    env["COMPOSE_PROJECT_NAME"] = f"alfa-b08-check-{uuid4().hex[:10]}"
    compose = [
        "docker",
        "compose",
        "--project-name",
        env["COMPOSE_PROJECT_NAME"],
        "--env-file",
        str(ROOT / ".env.example"),
        "-f",
        str(ROOT / "docker-compose.yml"),
    ]
    cookies = CookieJar()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(cookies))

    def run(*args: str) -> None:
        subprocess.run([*compose, *args], cwd=ROOT, env=env, check=True)

    def check(path: str, expected: int, body: dict | None = None) -> None:
        started = time.monotonic()
        url = f"http://127.0.0.1:{env['BACKEND_PORT']}{path}"
        try:
            response = opener.open(url, timeout=6)
        except HTTPError as exc:
            response = exc
        with response:
            assert response.code == expected, (path, response.code, expected)
            if body is not None:
                assert json.load(response) == body
        elapsed = time.monotonic() - started
        # asyncpg/SQLAlchemy may spend up to 2s closing a cancelled connection.
        assert elapsed < 5, (path, elapsed)
        print(f"PASS {path}: HTTP {expected} ({elapsed:.2f}s)", flush=True)

    def wait_ready() -> None:
        deadline = time.monotonic() + 30
        while True:
            try:
                check("/health/ready", 200, {"status": "ok"})
                return
            except (AssertionError, URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)

    def api_request(path, expected, *, body=None, token=None, cookie=None, method=None, key=None):
        request_headers = {"Origin": env["AUTH_ALLOWED_ORIGIN"]}
        if key:
            request_headers["Idempotency-Key"] = str(key)
        if token:
            request_headers["X-CSRF-Token"] = token
        if cookie:
            request_headers["Cookie"] = cookie
        data = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = Request(
            env["AUTH_ALLOWED_ORIGIN"] + "/api/" + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            response = opener.open(request, timeout=6)
        except HTTPError as exc:
            response = exc
        with response:
            assert response.code == expected, (path, response.code, expected)
            assert response.headers["Cache-Control"] == "no-store"
            raw = response.read()
            result = json.loads(raw) if raw else None
            if path == "auth/login" and expected == 200:
                assert set(result) == {"id", "login", "display_name", "role"}
                assert all(
                    "HttpOnly" in value and "SameSite=lax" in value and "Path=/" in value
                    for value in response.headers.get_all("Set-Cookie")
                )
            if path == "auth/logout" and expected == 204:
                assert not raw and not list(cookies)
            return result

    def auth_request(path, expected, **kwargs):
        return api_request("auth/" + path, expected, **kwargs)

    with tempfile.TemporaryDirectory(prefix="alfa-docker-check-") as temporary:
        # Cloud checkouts may expose files as mode 0600 owned by the executor user.
        # Stage synthetic inputs for UID 10001 without changing the owners' files.
        staging = Path(temporary)
        staging.chmod(0o755)
        mounts = []
        for source, target in (
            ("seed/accounts.json", "accounts.json"),
            ("seed/companies.json", "companies.json"),
            ("seed/products.json", "products.json"),
            ("docs/product/ui-texts.ru.json", "product-texts.json"),
        ):
            staged = staging / target
            shutil.copyfile(ROOT / source, staged)
            staged.chmod(0o444)
            mounts.append(f"{staged}:/seed/{target}:ro")
        override_data = {
            "services": {
                "backend": {"volumes": mounts},
                # PR #61 runs seed in a separate UID 10001 service before API/worker.
                "migrate": {"volumes": mounts},
                "mocks": {},
            }
        }
        # Optional corporate/proxy CA; never copied into the image.
        if ca_file := env.get("DOCKER_BUILD_CA"):
            override_data["services"]["backend"]["build"] = {"secrets": ["proxy_ca"]}
            override_data["services"]["mocks"]["build"] = {"secrets": ["proxy_ca"]}
            override_data["secrets"] = {"proxy_ca": {"file": str(Path(ca_file).resolve())}}
        override = staging / "check-override.json"
        override.write_text(json.dumps(override_data))
        compose.extend(["-f", str(override)])
        try:
            run("build", "backend", "mocks")
            run("up", "-d", "--wait", "db")
            run("run", "--rm", "backend", "alembic", "upgrade", "head")
            run("run", "--rm", "backend", "alembic", "check")
            for _ in range(2):
                run(
                    "run",
                    "--rm",
                    "backend",
                    "python",
                    "-m",
                    "app.seed",
                    "--accounts",
                    "/seed/accounts.json",
                    "--companies",
                    "/seed/companies.json",
                    "--products",
                    "/seed/products.json",
                    "--product-texts",
                    "/seed/product-texts.json",
                )
            run("up", "-d", "--wait", "--wait-timeout", "90", "backend", "mocks")
            run(
                "exec",
                "-T",
                "backend",
                "python",
                "-c",
                "import os, sys; assert sys.version_info[:2] == (3, 12); assert os.getuid() != 0",
            )
            run(
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SHOW server_version" '
                "| tee /tmp/server-version | head -1; "
                'test "$(cut -d. -f1 /tmp/server-version)" = 16',
            )
            check("/health/live", 200, {"status": "ok"})
            wait_ready()
            check("/docs", 200)
            check("/openapi.json", 200)

            accounts = json.loads((ROOT / "seed/accounts.json").read_text())
            account = accounts["clients"][0]
            token = auth_request("csrf", 200)["csrf_token"]
            user = auth_request(
                "login",
                200,
                body={"login": account["login"], "password": account["password"]},
                token=token,
            )
            saved_jwt = next(c.value for c in cookies if c.name == "access_token")
            token = auth_request("csrf", 200)["csrf_token"]
            assert auth_request("me", 200) == user

            products = api_request("products", 200)["items"]
            assert len(products) == 4
            company_data = json.loads((ROOT / "seed/companies.json").read_text())["companies"][0]
            inn = company_data["company"]["inn"]
            company = api_request(f"companies/lookup?inn={inn}", 200)["company"]
            assert company == company_data["company"]
            assert api_request("companies/lookup?inn=0000000000", 200) == {"company": None}
            draft = api_request("applications", 201, body={}, token=token)
            path = "applications/" + draft["id"]
            assert draft["revision"] == 1 and draft["status"] == "DRAFT"
            edited = api_request(
                path,
                200,
                method="PATCH",
                token=token,
                body={
                    "expected_revision": 1,
                    "company": company,
                    "product_id": products[0]["id"],
                    "amount": "100000.01",
                    "term_months": 12,
                    "purpose": "purchase_goods",
                },
            )
            assert edited["revision"] == 2 and edited["form"]["amount"] == "100000.01"
            assert api_request(path, 200) == edited
            assert api_request("applications?offset=0&limit=1&status=DRAFT", 200)["total"] == 1
            api_request(path, 409, method="PATCH", token=token, body={"expected_revision": 1})
            submit_key = uuid4()
            submit_body = {"expected_revision": 2, "consent_to_checks": True}
            submitted = api_request(
                path + "/submit", 202, body=submit_body, token=token, key=submit_key
            )
            assert submitted["status"] == "SUBMITTED" and submitted["revision"] == 3
            assert (
                api_request(path + "/submit", 202, body=submit_body, token=token, key=submit_key)
                == submitted
            )

            def assert_database(cancelled=False):
                application_id = draft["id"]
                expected = (
                    "CANCELLED|4|3|1|1|cancelled|1|1,2,3,4"
                    if cancelled
                    else ("SUBMITTED|3|3|1|1|pending|1|1,2,3")
                )
                sql = f"""SELECT a.status, a.revision, a.input_revision,
                    (SELECT count(*) FROM application_submissions WHERE application_id=a.id),
                    (SELECT count(*) FROM pipeline_jobs WHERE application_id=a.id),
                    (SELECT state FROM pipeline_jobs WHERE application_id=a.id),
                    (SELECT count(*) FROM submit_idempotency WHERE application_id=a.id),
                    (SELECT string_agg(sequence::text, ',' ORDER BY sequence)
                     FROM application_events WHERE application_id=a.id)
                    FROM applications a WHERE a.id='{application_id}'"""
                result = subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "db",
                        "psql",
                        "-U",
                        env["POSTGRES_USER"],
                        "-d",
                        env["POSTGRES_DB"],
                        "-Atc",
                        sql,
                    ],
                    env=env,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                assert result.stdout.strip() == expected, result.stdout
                print(
                    "PASS transactional audit, input version, idempotency and single job",
                    flush=True,
                )

            assert_database()
            edited = submitted
            run("stop", "mocks")
            api_request(f"companies/lookup?inn={inn}", 500)
            assert api_request(path, 200) == edited
            run("start", "mocks")

            print("Checking query timeout while PostgreSQL is paused", flush=True)
            run("pause", "db")
            try:
                check("/health/ready", 503, {"status": "unavailable"})
                check("/health/live", 200, {"status": "ok"})
            finally:
                run("unpause", "db")
            wait_ready()

            print(
                "Checking stopped database and backend startup without database",
                flush=True,
            )
            run("stop", "db")
            check("/health/ready", 503, {"status": "unavailable"})
            check("/health/live", 200, {"status": "ok"})
            run("restart", "backend")
            deadline = time.monotonic() + 15
            while True:
                try:
                    check("/health/live", 200, {"status": "ok"})
                    break
                except (URLError, OSError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.5)
            check("/health/ready", 503, {"status": "unavailable"})
            run("start", "db")
            wait_ready()
            assert auth_request("me", 200) == user
            assert auth_request("csrf", 200)["csrf_token"] == token
            assert api_request(path, 200) == edited
            assert (
                api_request(path + "/submit", 202, body=submit_body, token=token, key=submit_key)
                == submitted
            )
            cancelled = api_request(
                path + "/cancel",
                200,
                token=token,
                body={"expected_revision": 3, "reason": "Проверка отмены"},
            )
            assert cancelled["status"] == "CANCELLED" and cancelled["revision"] == 4
            assert cancelled["allowed_actions"] == []
            assert_database(cancelled=True)
            assert (
                api_request(path + "/submit", 202, body=submit_body, token=token, key=submit_key)
                == submitted
            )

            def sql(statement):
                result = subprocess.run(
                    [
                        *compose,
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
                    ],
                    env=env,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                return result.stdout.strip()

            def until(condition, seconds=35):
                deadline = time.monotonic() + seconds
                while True:
                    value = condition()
                    if value:
                        return value
                    if time.monotonic() >= deadline:
                        raise AssertionError("Docker condition deadline exceeded")
                    time.sleep(0.1)

            def new_submission(company_record):
                form = company_record["application"]
                product = next(p for p in products if p["code"] == form["product_code"])
                new_draft = api_request(
                    "applications",
                    201,
                    token=token,
                    body={
                        "company": company_record["company"],
                        "product_id": product["id"],
                        **{k: v for k, v in form.items() if k != "product_code"},
                    },
                )
                new_path = "applications/" + new_draft["id"]
                sent = api_request(
                    new_path + "/submit",
                    202,
                    token=token,
                    key=uuid4(),
                    body={
                        "expected_revision": new_draft["revision"],
                        "consent_to_checks": True,
                    },
                )
                return new_path, sent

            def at_status(new_path, status):
                card = api_request(new_path, 200)
                return card if card["status"] == status else None

            def assert_decided(sent, retries=0, outcome="APPROVED"):
                identity = sent["id"]
                assert (
                    sql(
                        f"SELECT a.status, a.input_revision, a.revision, j.phase, j.state, "
                        f"j.attempts, (j.lease_token IS NULL), "
                        "(SELECT count(*) FROM submission_checks c "
                        "WHERE c.submission_id=j.submission_id), "
                        f"(SELECT count(*) FROM application_events e WHERE e.application_id=a.id "
                        f"AND e.status='SCORING') "
                        f"FROM applications a JOIN pipeline_jobs j ON j.application_id=a.id "
                        f"WHERE a.id='{identity}'"
                    )
                    == f"{outcome}|2|{5 + retries}|decision|completed|1|t|1|1"
                )
                # All three raw sources persist, remain internal, and share one submission.
                assert (
                    sql(
                        f"SELECT c.egrul->>'inn'=c.pb->>'inn' AND c.pb->>'inn'=c.bki->>'inn' "
                        f"FROM submission_checks c JOIN pipeline_jobs j USING(submission_id) "
                        f"WHERE j.application_id='{identity}'"
                    )
                    == "t"
                )

                assert (
                    sql(
                        "SELECT count(*) FROM decisions "
                        f"WHERE application_id='{identity}' AND risk_result IS NOT NULL"
                    )
                    == "1"
                )
                assert sql(f"SELECT count(*) FROM offers WHERE application_id='{identity}'") == (
                    "1" if outcome in {"APPROVED", "COUNTER_OFFER"} else "0"
                )
                decision = api_request("applications/" + identity, 200)["decision"]
                assert decision["outcome"] == outcome
                assert set(decision) == {
                    "id",
                    "created_at",
                    "outcome",
                    "reasons",
                    "explanation",
                    "required_documents",
                } | ({"offer"} if outcome in {"APPROVED", "COUNTER_OFFER"} else set())

            new_path, sent = new_submission(company_data)
            run("up", "-d", "--scale", "worker=2", "worker")
            until(lambda: at_status(new_path, "CHECKING"))
            until(lambda: at_status(new_path, "APPROVED"))
            assert_decided(sent)
            print(
                "PASS two real workers: CHECKING -> stored responses -> SCORING -> real APPROVED",
                flush=True,
            )

            records = json.loads((ROOT / "seed/companies.json").read_text())["companies"]
            bki_company = next(c for c in records if c["client_login"] == "demo-bki")
            new_path, bki_sent = new_submission(bki_company)
            until(lambda: at_status(new_path, "CHECKING"))
            until(lambda: at_status(new_path, "APPROVED"))
            assert_decided(bki_sent, retries=2)
            print(
                "PASS real demo-bki: two 503, two audited retries, then real APPROVED", flush=True
            )

            for record in records:
                if record["key"] in {"approve", "bki_failure"}:
                    continue
                scenario_path, scenario = new_submission(record)
                expected = record["expected_outcome"]
                until(lambda p=scenario_path, status=expected: at_status(p, status))
                assert_decided(scenario, outcome=expected)
                print(f"PASS real decide scenario {record['key']}: {expected}", flush=True)

            # Graceful SIGTERM and successful commit survive process restart.
            run("restart", "worker")
            assert_decided(sent)
            assert_decided(bki_sent, retries=2)
            new_path, crashed = new_submission(company_data)
            until(lambda: at_status(new_path, "CHECKING"))
            run("kill", "-s", "SIGKILL", "worker")
            assert (
                sql(
                    "SELECT count(*) FROM submission_checks c "
                    "JOIN pipeline_jobs j USING(submission_id) "
                    f"WHERE j.application_id='{crashed['id']}'"
                )
                == "0"
            )
            run("start", "worker")
            until(lambda: at_status(new_path, "APPROVED"))
            assert_decided(crashed)
            print(
                "PASS SIGTERM/restart and crash during HTTP recovered by expired lease", flush=True
            )

            new_path, late = new_submission(company_data)
            current = until(lambda: at_status(new_path, "CHECKING"))
            cancelled = api_request(
                new_path + "/cancel",
                200,
                token=token,
                body={"expected_revision": current["revision"]},
            )
            assert cancelled["status"] == "CANCELLED"

            # Bound wait on actual worker completion in logs; do not use a fixed sleep.
            def cancellation_observed():
                logs = subprocess.run(
                    [*compose, "logs", "--no-color", "worker"],
                    env=env,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                return any(
                    late["id"] in line and "category=lost_lease" in line
                    for line in logs.stdout.splitlines()
                )

            until(cancellation_observed)
            assert (
                sql(
                    f"SELECT a.status, j.state, "
                    "(SELECT count(*) FROM submission_checks c "
                    "WHERE c.submission_id=j.submission_id) "
                    f"FROM applications a JOIN pipeline_jobs j ON j.application_id=a.id "
                    f"WHERE a.id='{late['id']}'"
                )
                == "CANCELLED|cancelled|0"
            )
            print("PASS cancel during real HTTP prevents late result persistence", flush=True)

            # Completed jobs remain idle across restarts and database outages.
            assert_decided(sent)
            assert_decided(bki_sent, retries=2)
            run("stop", "db")
            until(
                lambda: (
                    "database_or_internal"
                    in subprocess.run(
                        [*compose, "logs", "--no-color", "worker"],
                        env=env,
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=10,
                    ).stdout
                )
            )
            run("start", "db")
            wait_ready()
            new_path, recovered = new_submission(company_data)
            until(lambda: at_status(new_path, "APPROVED"))
            assert_decided(recovered)
            print("PASS workers survive DB disconnect and resume processing", flush=True)

            auth_request("logout", 204, body={}, token=token)
            auth_request("me", 401, cookie="access_token=" + saved_jwt)
            cookies.clear()
            auth_request("csrf", 200)
            print(
                "PASS Docker login, drafts, submit/replay, restart, cancel, "
                "audit, queue, real decisions, lease and recovery",
                flush=True,
            )
        except BaseException:
            run("logs", "--no-color", "--tail", "100")
            raise
        finally:
            run("down", "--volumes", "--remove-orphans", "--timeout", "10")


if __name__ == "__main__":
    main()

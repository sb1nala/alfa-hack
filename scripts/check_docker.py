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
    env["DB_READINESS_TIMEOUT"] = "2"
    env["AUTH_JWT_SECRET"] = secrets.token_urlsafe(48)
    env["AUTH_ALLOWED_ORIGIN"] = f"http://127.0.0.1:{env['BACKEND_PORT']}"
    env["AUTH_COOKIE_SECURE"] = "false"
    compose = [
        "docker",
        "compose",
        "--project-name",
        f"alfa-b04-check-{uuid4().hex[:10]}",
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

    def auth_request(path, expected, *, body=None, token=None, cookie=None):
        request_headers = {"Origin": env["AUTH_ALLOWED_ORIGIN"]}
        if token:
            request_headers["X-CSRF-Token"] = token
        if cookie:
            request_headers["Cookie"] = cookie
        data = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = Request(
            env["AUTH_ALLOWED_ORIGIN"] + "/api/auth/" + path,
            data=data,
            headers=request_headers,
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
            if path == "login" and expected == 200:
                assert set(result) == {"id", "login", "display_name", "role"}
                assert all(
                    "HttpOnly" in value and "SameSite=lax" in value and "Path=/" in value
                    for value in response.headers.get_all("Set-Cookie")
                )
            if path == "logout" and expected == 204:
                assert not raw and not list(cookies)
            return result

    with tempfile.TemporaryDirectory(prefix="alfa-docker-check-") as temporary:
        # Cloud checkouts may expose files as mode 0600 owned by the executor user.
        # Stage synthetic accounts for UID 10001 without changing the owner's seed/ file.
        staging = Path(temporary)
        staging.chmod(0o755)
        staged_accounts = staging / "accounts.json"
        shutil.copyfile(ROOT / "seed/accounts.json", staged_accounts)
        staged_accounts.chmod(0o444)
        override_data = {
            "services": {"backend": {"volumes": [f"{staged_accounts}:/seed/accounts.json:ro"]}}
        }
        # Optional corporate/proxy CA; never copied into the image.
        if ca_file := env.get("DOCKER_BUILD_CA"):
            override_data["services"]["backend"]["build"] = {"secrets": ["proxy_ca"]}
            override_data["secrets"] = {"proxy_ca": {"file": str(Path(ca_file).resolve())}}
        override = staging / "check-override.json"
        override.write_text(json.dumps(override_data))
        compose.extend(["-f", str(override)])
        try:
            run("build", "backend")
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
                )
            run("up", "-d", "--wait", "--wait-timeout", "90", "backend")
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
            auth_request("logout", 204, body={}, token=token)
            auth_request("me", 401, cookie="access_token=" + saved_jwt)
            cookies.clear()
            auth_request("csrf", 200)
            print(
                "PASS Docker migrations, seed, auth, restart, logout/replay and outage recovery",
                flush=True,
            )
        except BaseException:
            run("logs", "--no-color", "--tail", "100")
            raise
        finally:
            run("down", "--volumes", "--remove-orphans", "--timeout", "10")


if __name__ == "__main__":
    main()

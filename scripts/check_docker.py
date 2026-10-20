"""Run real Docker checks in an isolated Compose project, then remove its resources."""

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
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
    compose = [
        "docker",
        "compose",
        "--project-name",
        f"alfa-task1-check-{uuid4().hex[:10]}",
        "--env-file",
        str(ROOT / ".env.example"),
        "-f",
        str(ROOT / "docker-compose.yml"),
    ]
    opener = build_opener(ProxyHandler({}))

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

    with tempfile.TemporaryDirectory(prefix="alfa-docker-check-") as temporary:
        # Optional corporate/proxy CA; never copied into the image.
        if ca_file := env.get("DOCKER_BUILD_CA"):
            override = Path(temporary) / "build-ca.json"
            override.write_text(
                json.dumps(
                    {
                        "services": {"backend": {"build": {"secrets": ["proxy_ca"]}}},
                        "secrets": {"proxy_ca": {"file": str(Path(ca_file).resolve())}},
                    }
                )
            )
            compose.extend(["-f", str(override)])
        try:
            run("up", "--build", "--wait", "--wait-timeout", "90")
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

            print("Checking query timeout while PostgreSQL is paused", flush=True)
            run("pause", "db")
            try:
                check("/health/ready", 503, {"status": "unavailable"})
                check("/health/live", 200, {"status": "ok"})
            finally:
                run("unpause", "db")
            wait_ready()

            print("Checking stopped database and backend startup without database", flush=True)
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
            print("PASS Docker checks, outage handling and recovery", flush=True)
        except BaseException:
            run("logs", "--no-color", "--tail", "100")
            raise
        finally:
            run("down", "--volumes", "--remove-orphans", "--timeout", "10")


if __name__ == "__main__":
    main()

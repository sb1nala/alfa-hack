"""Isolated native browser SSE smoke through the repository frontend and nginx.

Requires Node, Playwright and Chromium; see backend/README.md. Never changes frontend.
"""

import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from check_docker import free_port

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = os.environ.copy()
    ports = set()
    while len(ports) < 4:
        ports.add(str(free_port()))
    frontend, backend, postgres, mocks = ports
    env.update(
        POSTGRES_DB="b09",
        POSTGRES_USER="b09",
        POSTGRES_PASSWORD=secrets.token_hex(16),
        AUTH_JWT_SECRET=secrets.token_hex(32),
        AUTH_COOKIE_SECURE="false",
        FRONTEND_PORT=frontend,
        BACKEND_PORT=backend,
        POSTGRES_PORT=postgres,
        MOCKS_PORT=mocks,
        SSE_POLL_INTERVAL="0.1",
        WORKER_POLL_INTERVAL="0.1",
        MOCKS_LATENCY_MS="500",
    )
    env["AUTH_ALLOWED_ORIGIN"] = f"http://127.0.0.1:{frontend}"
    project = "alfa-b09-browser-" + secrets.token_hex(5)
    with tempfile.TemporaryDirectory(prefix="alfa-b09-browser-") as temporary:
        staging = Path(temporary)
        staging.chmod(0o755)
        mounts = []
        for source, target in (
            ("seed/accounts.json", "accounts.json"),
            ("seed/companies.json", "companies.json"),
            ("seed/products.json", "products.json"),
            ("docs/product/ui-texts.ru.json", "product-texts.json"),
        ):
            dest = staging / target
            shutil.copyfile(ROOT / source, dest)
            dest.chmod(0o444)
            mounts.append(f"{dest}:/seed/{target}:ro")
        front = staging / "frontend"
        shutil.copytree(
            ROOT / "frontend",
            front,
            ignore=shutil.ignore_patterns("node_modules", "dist", ".env*"),
        )
        # Removing implemented 501 responses changes generated API types. Only this
        # disposable build regenerates them; the frontend owner updates the source.
        dockerfile = (
            (front / "Dockerfile")
            .read_text()
            .replace("RUN pnpm build", "RUN pnpm api:generate && pnpm build")
        )
        install = "RUN NODE_USE_ENV_PROXY=1 pnpm install --frozen-lockfile"
        override = {
            "services": {
                "backend": {"build": {}, "volumes": mounts},
                "migrate": {"volumes": mounts},
                "mocks": {"build": {}},
                "frontend": {
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "wget",
                            "-Y",
                            "off",
                            "-q",
                            "-O",
                            "/dev/null",
                            "http://127.0.0.1:8080/",
                        ]
                    },
                    "build": {
                        "context": str(front),
                        "additional_contexts": {
                            "docs": str(ROOT / "docs"),
                            "seed": str(ROOT / "seed"),
                        },
                    },
                },
            },
        }
        if ca := env.get("DOCKER_BUILD_CA"):
            override["secrets"] = {"proxy_ca": {"file": str(Path(ca).resolve())}}
            for service in ("backend", "mocks", "frontend"):
                override["services"][service]["build"]["secrets"] = ["proxy_ca"]
            install = (
                "RUN --mount=type=secret,id=proxy_ca NODE_USE_ENV_PROXY=1 "
                "NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca pnpm install --frozen-lockfile"
            )
        dockerfile = dockerfile.replace("RUN pnpm install --frozen-lockfile", install)
        (front / "Dockerfile").write_text(dockerfile)
        # Cloud checkouts may be mode 0600; nginx runs without root privileges.
        for entry in front.rglob("*"):
            entry.chmod(0o755 if entry.is_dir() else 0o644)
        # Retain the configured sidecar proxy; route package requests through it.
        build_args = {}
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            if value := env.get(name):
                url = urlsplit(value) if name != "NO_PROXY" else None
                if url and url.hostname == "proxy":
                    import socket

                    value = urlunsplit(
                        url._replace(netloc=f"{socket.gethostbyname('proxy')}:{url.port}")
                    )
                build_args[name] = value
        if build_args:
            build_args["NO_PROXY"] = "localhost,127.0.0.1"
            for service in ("backend", "mocks", "frontend"):
                override["services"][service]["build"]["args"] = build_args
        config = staging / "compose.json"
        config.write_text(json.dumps(override))
        compose = [
            "compose",
            "-p",
            project,
            "--env-file",
            str(ROOT / ".env.example"),
            "-f",
            str(ROOT / "docker-compose.yml"),
            "-f",
            str(config),
        ]

        def run(*args):
            subprocess.run(["docker", *compose, *args], cwd=ROOT, env=env, check=True, timeout=300)

        browser = staging / "browser.json"
        browser.write_text(
            json.dumps(
                {
                    "compose": compose,
                    "env": {
                        k: v
                        for k, v in env.items()
                        if k.startswith(
                            (
                                "POSTGRES_",
                                "AUTH_",
                                "SSE_",
                                "FRONTEND_",
                                "BACKEND_",
                                "MOCKS_",
                                "WORKER_",
                            )
                        )
                    },
                    "origin": env["AUTH_ALLOWED_ORIGIN"],
                    "record": json.loads((ROOT / "seed/companies.json").read_text())["companies"][
                        0
                    ],
                }
            )
        )
        browser.chmod(0o600)
        try:
            run("build", "backend", "mocks", "frontend")
            run("up", "-d", "--wait", "--wait-timeout", "90")
            subprocess.run(
                ["node", str(ROOT / "scripts/check_sse_browser.cjs"), str(browser)],
                env=env,
                check=True,
                timeout=100,
            )
        except Exception:
            run("logs", "--no-color", "--tail", "60", "frontend", "backend", "migrate")
            raise
        finally:
            run("down", "--volumes", "--remove-orphans", "--rmi", "local")


if __name__ == "__main__":
    main()

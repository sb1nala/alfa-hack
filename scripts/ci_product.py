"""Stream sanitized product diagnostics and retain private state for always-cleanup.

Only report-dir is uploadable. state-dir contains ephemeral credentials and must
stay outside artifacts. The original product runner still owns the build/tests.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from ci_reports import safe_line

ROOT = Path(__file__).resolve().parents[1]


def read_state(directory):
    path = directory / "stand.json"
    return json.loads(path.read_text()) if path.exists() else None


def stream(command, env, output, state_dir):
    with subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            for line in process.stdout:
                state = read_state(state_dir)
                secrets = (
                    [v for k, v in state["env"].items() if k.endswith(("PASSWORD", "SECRET"))]
                    if state
                    else []
                )
                # Synthetic demo credentials must not appear in uploaded diagnostics either.
                secrets += ["demo-2026", "analyst-2026"]
                clean = safe_line(line, secrets)
                print(clean, end="", flush=True)
                output.write(clean)
                output.flush()
            return process.wait()
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            raise


def cleanup(state_dir, output):
    state = read_state(state_dir)
    if state is None:
        return 0
    # Never discover/remove arbitrary Docker resources: exact project and env only.
    result = stream(
        [
            *state["compose"],
            "down",
            "--volumes",
            "--remove-orphans",
            "--rmi",
            "local",
            "--timeout",
            "45",
        ],
        {**os.environ, **state["env"]},
        output,
        state_dir,
    )
    if result == 0:
        (state_dir / "stand.json").unlink()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "cleanup"])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    def terminate(_signum, _frame):
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, terminate)
    with (args.report_dir / "product.log").open("a") as output:
        if args.command == "cleanup":
            return cleanup(args.state_dir, output)
        if read_state(args.state_dir):
            raise SystemExit("Private state already exists; clean up the previous stand first")
        result = 1
        try:
            result = stream(
                [sys.executable, "-u", str(ROOT / "scripts/check_product_docker.py")],
                {**os.environ, "PRODUCT_CI_STATE": str(args.state_dir / "stand.json")},
                output,
                args.state_dir,
            )
        finally:
            cleanup_result = cleanup(args.state_dir, output)
        return result or cleanup_result


if __name__ == "__main__":
    sys.exit(main())

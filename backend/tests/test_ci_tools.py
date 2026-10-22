"""CI must preserve failure status and never publish credentials from diagnostics."""

import importlib
import io
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def ci_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("ci_reports"), importlib.import_module("ci_product")


def test_diagnostics_filter_credentials_and_preserve_useful_failure(
    ci_tools, tmp_path, monkeypatch
):
    reports, product = ci_tools
    secret = "random-stand-value-23456789"
    (tmp_path / "stand.json").write_text(json.dumps({"env": {"POSTGRES_PASSWORD": secret}}))
    output = io.StringIO()
    payload = "\n".join(
        [
            secret,
            "Cookie: session=opaque",
            '"csrf_token": "opaque"',
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.signature",
            "postgresql+asyncpg://user:opaque@db/test",
            "demo-2026",
            "Generated types are not up-to-date!",
        ]
    )
    result = product.stream(
        [sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(23)", payload],
        {},
        output,
        tmp_path,
    )
    assert result == 23
    assert output.getvalue().count("omitted") == 6
    assert "Generated types are not up-to-date!" in output.getvalue()
    assert reports.safe_line("PASS nginx HTTP 200\n") == "PASS nginx HTTP 200\n"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    raw = tmp_path / "raw.log"
    raw.write_text(payload)
    safe = artifacts / "report.txt"
    monkeypatch.setenv("POSTGRES_PASSWORD", secret)
    monkeypatch.setattr(sys, "argv", ["ci_reports.py", str(raw), "--safe-copy", str(safe)])
    reports.main()
    reports.audit_reports(artifacts)
    safe.write_text("session=opaque\n")
    with pytest.raises(SystemExit, match="upload denied"):
        reports.audit_reports(artifacts)


@pytest.mark.parametrize(
    "cases",
    [
        "",
        '<testcase><skipped message="postgres disabled"/></testcase>',
        "<testcase><failure/></testcase>",
        "<testcase><error/></testcase>",
    ],
)
def test_pytest_gate_rejects_incomplete_or_failed_runs(ci_tools, tmp_path, cases):
    report = tmp_path / "pytest.xml"
    report.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    with pytest.raises(SystemExit):
        ci_tools[0].check_pytest(report)


def test_pytest_gate_accepts_complete_run_without_fixed_count(ci_tools, tmp_path):
    report = tmp_path / "pytest.xml"
    report.write_text('<testsuites><testsuite><testcase name="postgres"/></testsuite></testsuites>')
    ci_tools[0].check_pytest(report)


def test_cleanup_targets_recorded_project_and_retains_state_on_failure(
    ci_tools, tmp_path, monkeypatch
):
    product = ci_tools[1]
    state = {
        "compose": ["docker", "compose", "-p", "unique-project"],
        "env": {"POSTGRES_DB": "test"},
    }
    path = tmp_path / "stand.json"
    path.write_text(json.dumps(state))
    calls = []

    def fail(command, env, output, state_dir):
        calls.append(command)
        return 1

    monkeypatch.setattr(product, "stream", fail)
    assert product.cleanup(tmp_path, io.StringIO()) == 1
    assert path.exists()
    assert calls == [
        [
            *state["compose"],
            "down",
            "--volumes",
            "--remove-orphans",
            "--rmi",
            "local",
            "--timeout",
            "45",
        ]
    ]
    monkeypatch.setattr(product, "stream", lambda *args: 0)
    assert product.cleanup(tmp_path, io.StringIO()) == 0
    assert not path.exists()
    assert product.cleanup(tmp_path, io.StringIO()) == 0

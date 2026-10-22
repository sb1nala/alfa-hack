"""Fail closed on skipped tests and credential-bearing diagnostic lines."""

import argparse
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# Omit whole lines: cookies/CSRF need not be JWTs, and exception output can contain
# request headers, URLs or source snippets. Artifacts are diagnostic text only.
SENSITIVE = re.compile(
    r"password|secret|cookie|csrf|authorization|bearer|access_token|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"postgres(?:ql)?(?:\+\w+)?://|(?:token|session)[\"']?\s*[:=]",
    re.IGNORECASE,
)


def safe_line(line, secrets=()):
    if SENSITIVE.search(line) or any(value and value in line for value in secrets):
        return "[credential-bearing diagnostic line omitted]\n"
    return line


def check_pytest(path):
    cases = ET.parse(path).getroot().findall(".//testcase")
    if not cases or any(case.find("skipped") is not None for case in cases):
        raise SystemExit("FAIL: empty pytest report or skipped tests; inspect the full run")
    if any(case.find(tag) is not None for case in cases for tag in ("failure", "error")):
        raise SystemExit("FAIL: pytest report contains failures/errors")
    print(f"PASS pytest report: {len(cases)} tests, no skips/failures/errors")


def audit_reports(directory):
    secrets = [v for k, v in os.environ.items() if re.search("PASSWORD|SECRET|TOKEN", k)]
    secrets += ["demo-2026", "analyst-2026"]
    for path in directory.iterdir() if directory.exists() else []:
        if path.suffix not in {".txt", ".log"} or not path.is_file():
            raise SystemExit("FAIL: unexpected diagnostic artifact type")
        for line in path.read_text().splitlines(True):
            if safe_line(line, secrets) != line:
                raise SystemExit("FAIL: diagnostic artifact failed credential audit; upload denied")
    print("PASS diagnostic artifact credential audit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--check-pytest", action="store_true")
    parser.add_argument("--safe-copy", type=Path)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    if args.safe_copy:
        secrets = [v for k, v in os.environ.items() if re.search("PASSWORD|SECRET|TOKEN", k)]
        args.safe_copy.parent.mkdir(parents=True, exist_ok=True)
        args.safe_copy.write_text(
            "".join(safe_line(line, secrets) for line in args.report.read_text().splitlines(True))
        )
    if args.check_pytest:
        check_pytest(args.report)
    if args.audit:
        audit_reports(args.report)


if __name__ == "__main__":
    main()

"""Fail closed on skipped tests and credential-bearing diagnostic lines."""

import argparse
import os
import re
import sys
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


def copy_report(lines, destination, *, echo=False):
    secrets = [v for k, v in os.environ.items() if re.search("PASSWORD|SECRET|TOKEN", k)]
    secrets += ["demo-2026", "analyst-2026"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w") as output:
        for line in lines:
            clean = safe_line(line, secrets)
            output.write(clean)
            output.flush()
            if echo:
                print(clean, end="", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--check-pytest", action="store_true")
    parser.add_argument("--safe-copy", type=Path)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--stream", action="store_true", help="Filter stdin live into --safe-copy")
    args = parser.parse_args()
    if args.stream:
        if not args.safe_copy:
            parser.error("--stream requires --safe-copy")
        copy_report(sys.stdin, args.safe_copy, echo=True)
    elif args.safe_copy:
        with args.report.open() as source:
            copy_report(source, args.safe_copy)
    if args.check_pytest:
        check_pytest(args.report)
    if args.audit:
        audit_reports(args.report)


if __name__ == "__main__":
    main()

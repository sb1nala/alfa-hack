"""Run from backend/: uv run --locked python -m app.export_openapi."""

import argparse
from pathlib import Path

import yaml

from app.main import create_app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"


def render_openapi() -> str:
    return yaml.safe_dump(create_app().openapi(), allow_unicode=True, sort_keys=True, width=100)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="Fail if output differs from app schema"
    )
    args = parser.parse_args()
    rendered = render_openapi()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            parser.exit(1, f"OpenAPI is out of date: {args.output}\n")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # LF on every OS: docs/openapi.yaml is compared byte-for-byte (see .gitattributes)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

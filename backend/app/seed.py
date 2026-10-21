"""Explicit, atomic import of accounts only. Never log input records or passwords."""

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.database import create_engine, create_session_factory
from app.models import User
from app.security import password_hasher


class Account(BaseModel):
    model_config = ConfigDict(extra="ignore")  # company_inn is demo context, not authorization.
    login: str = Field(min_length=1, max_length=254)
    display_name: str = Field(min_length=1, max_length=4000)
    password: SecretStr = Field(min_length=1, max_length=1024)


class Accounts(BaseModel):
    model_config = ConfigDict(extra="ignore")
    clients: list[Account]
    underwriters: list[Account]


class ImportError(ValueError):
    """A safe public diagnostic; contains no source data."""


async def import_accounts(factory, path: Path) -> tuple[int, int]:
    try:
        data = Accounts.model_validate_json(
            await run_in_threadpool(path.read_text, encoding="utf-8")
        )
    except (OSError, ValidationError, json.JSONDecodeError):
        raise ImportError(
            "Cannot read accounts: expected valid clients/underwriters records"
        ) from None
    records = [(a, "client") for a in data.clients] + [
        (a, "underwriter") for a in data.underwriters
    ]
    if len({a.login for a, _ in records}) != len(records):
        raise ImportError("Duplicate login in accounts source")
    created = 0
    async with factory() as db, db.begin():
        # Serialize concurrent invocations of this importer, including its first run.
        await db.execute(text("SELECT pg_advisory_xact_lock(42004)"))
        for account, role in records:
            existing = await db.scalar(
                select(User).where(User.login == account.login).with_for_update()
            )
            if existing:
                if existing.role != role:
                    raise ImportError(
                        "Existing account role conflicts with source; import rolled back"
                    )
                continue
            password_hash = await run_in_threadpool(
                password_hasher.hash, account.password.get_secret_value()
            )
            db.add(
                User(
                    login=account.login,
                    display_name=account.display_name,
                    role=role,
                    password_hash=password_hash,
                )
            )
            created += 1
        await db.flush()
    return created, len(records) - created


async def run(path: Path) -> tuple[int, int]:
    engine = create_engine(Settings())
    try:
        return await import_accounts(create_session_factory(engine), path)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import demo accounts atomically (no business data)"
    )
    parser.add_argument("--accounts", type=Path, required=True)
    args = parser.parse_args()
    try:
        created, unchanged = asyncio.run(run(args.accounts))
    except ImportError as exc:
        parser.exit(1, f"{exc}\n")
    except Exception:
        # SQLAlchemy diagnostics can contain bound parameters, including hashes. Do not print them.
        parser.exit(
            1, "Account import failed; transaction rolled back. Check database configuration.\n"
        )
    print(f"Accounts imported: created={created}, unchanged={unchanged}")


if __name__ == "__main__":
    main()

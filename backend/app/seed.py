"""Explicit atomic demo import. Never log source records or credentials."""

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from app.catalog import company_identity
from app.config import Settings
from app.database import create_engine, create_session_factory
from app.models import Company, Product, User
from app.schemas.catalog import CompanyInput
from app.schemas.common import Money, Months
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


async def read_source(path: Path, model):
    try:
        return model.model_validate_json(await run_in_threadpool(path.read_text, encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError):
        raise ImportError("Cannot read seed: invalid or unreadable source") from None


async def account_rows(db, data: Accounts) -> tuple[int, int]:
    records = [(a, "client") for a in data.clients] + [
        (a, "underwriter") for a in data.underwriters
    ]
    if len({a.login for a, _ in records}) != len(records):
        raise ImportError("Duplicate login in accounts source")
    created = 0
    for account, role in records:
        existing = await db.scalar(
            select(User).where(User.login == account.login).with_for_update()
        )
        if existing:
            if existing.role != role:
                raise ImportError("Existing account role conflicts with source; import rolled back")
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


class CompanyRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company: CompanyInput


class Companies(BaseModel):
    model_config = ConfigDict(extra="ignore")
    companies: list[CompanyRecord] = Field(min_length=1)


class RiskProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1)
    min_amount: Money
    max_amount: Money
    min_term_months: Months
    max_term_months: Months
    base_rate_percent: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    collateral: bool
    auto_decision_max_amount: Money | None
    limit_rule: str | None = None

    @model_validator(mode="after")
    def valid_ranges(self):
        if Decimal(self.min_amount) > Decimal(self.max_amount):
            raise ValueError("Invalid amount range")
        if self.min_term_months > self.max_term_months:
            raise ValueError("Invalid term range")
        return self


class Metadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policy_version: str = Field(min_length=1)


class Products(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: Metadata = Field(alias="_meta")
    products: list[RiskProduct] = Field(min_length=1)
    purposes: dict[str, list[str]]


class ProductText(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ProductTexts(BaseModel):
    model_config = ConfigDict(extra="ignore")
    products: dict[str, ProductText | str]


async def import_seed(
    factory,
    *,
    accounts: Path | None = None,
    companies: Path | None = None,
    products: Path | None = None,
    product_texts: Path | None = None,
) -> dict[str, tuple[int, int]]:
    account_data = await read_source(accounts, Accounts) if accounts else None
    company_data = await read_source(companies, Companies) if companies else None
    product_data = await read_source(products, Products) if products else None
    texts = await read_source(product_texts, ProductTexts) if products and product_texts else None
    if company_data:
        inns = [row.company.inn for row in company_data.companies]
        if len(inns) != len(set(inns)):
            raise ImportError("Duplicate company in seed")
    if product_data:
        codes = [row.code for row in product_data.products]
        if len(codes) != len(set(codes)):
            raise ImportError("Duplicate product in seed")
        if not texts or any(not isinstance(texts.products.get(c), ProductText) for c in codes):
            raise ImportError("Product texts missing for seed codes")
        if any(code not in codes for group in product_data.purposes.values() for code in group):
            raise ImportError("Unknown product in seed purposes")
    report = {}
    async with factory() as db, db.begin():
        # Same lock as B04, including calls importing only accounts or only catalogs.
        await db.execute(text("SELECT pg_advisory_xact_lock(42004)"))
        if account_data:
            report["Accounts"] = await account_rows(db, account_data)
        if company_data:
            changed = 0
            for record in sorted(company_data.companies, key=lambda r: r.company.inn):
                existing = await db.scalar(select(Company).where(Company.inn == record.company.inn))
                if existing is None or any(
                    getattr(existing, key) != value
                    for key, value in record.company.model_dump().items()
                ):
                    await company_identity(db, record.company, refresh=True)
                    changed += 1
            report["Companies"] = changed, len(company_data.companies) - changed
        if product_data:
            changed = 0
            for record in sorted(product_data.products, key=lambda r: r.code):
                presentation = texts.products[record.code]
                values = {
                    "name": presentation.name,
                    "description": presentation.description,
                    "purposes": [
                        p for p, codes in product_data.purposes.items() if record.code in codes
                    ],
                    "policy_snapshot": {
                        "policy_version": product_data.metadata.policy_version,
                        **record.model_dump(mode="json", exclude_unset=True),
                    },
                }
                existing = await db.scalar(select(Product).where(Product.code == record.code))
                if existing is None:
                    db.add(Product(code=record.code, **values))
                    changed += 1
                elif any(getattr(existing, key) != value for key, value in values.items()):
                    for key, value in values.items():
                        setattr(existing, key, value)
                    existing.version += 1
                    changed += 1
            report["Products"] = changed, len(product_data.products) - changed
        await db.flush()
    return report


async def import_accounts(factory, path: Path) -> tuple[int, int]:
    return (await import_seed(factory, accounts=path))["Accounts"]


async def run(**paths):
    engine = create_engine(Settings())
    try:
        return await import_seed(create_session_factory(engine), **paths)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import selected demo sources in one atomic transaction"
    )
    parser.add_argument("--accounts", type=Path)
    parser.add_argument("--companies", type=Path)
    parser.add_argument("--products", type=Path)
    parser.add_argument("--product-texts", type=Path)
    args = parser.parse_args()
    if not any((args.accounts, args.companies, args.products)):
        parser.error("select --accounts, --companies and/or --products")
    if args.products and not args.product_texts:
        parser.error("--products requires --product-texts (docs/product/ui-texts.ru.json)")
    try:
        report = asyncio.run(run(**vars(args)))
    except ImportError as exc:
        parser.exit(1, f"{exc}\n")
    except Exception:
        # SQLAlchemy diagnostics can contain bound parameters, including hashes. Do not print them.
        parser.exit(
            1, "Seed import failed; transaction rolled back. Check database configuration.\n"
        )
    for source, (changed, unchanged) in report.items():
        label = "created" if source == "Accounts" else "changed"
        print(f"{source} imported: {label}={changed}, unchanged={unchanged}")


if __name__ == "__main__":
    main()

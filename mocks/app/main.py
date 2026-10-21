"""Local mocks of external services: БКИ, ЕГРЮЛ/ЕГРИП, ФНС «Прозрачный бизнес».

Data comes from seed/companies.json. Response bodies are the ``mocks.egrul``,
``mocks.pb`` and ``mocks.bki`` objects of that file plus ``inn`` (ЕГРЮЛ also adds
``name`` and ``legal_form`` for form autofill), so ``decide()`` parses them as is.

``mock_behavior`` of a company injects failures, e.g.
``{"bki": {"fail_first_n": 2, "status_code": 503, "delay_ms": 0}}``: the first two
requests to БКИ for that INN fail with 503, the next ones succeed.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

INN_PATTERN = r"^([0-9]{10}|[0-9]{12})$"
DEFAULT_SEED = Path(__file__).resolve().parents[2] / "seed" / "companies.json"

Inn = Annotated[str, PathParam(pattern=INN_PATTERN, description="ИНН: 10 цифр ООО, 12 — ИП")]
InnQuery = Annotated[str, Query(pattern=INN_PATTERN, description="ИНН: 10 цифр ООО, 12 — ИП")]


def load_companies(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {company["company"]["inn"]: company for company in data["companies"]}


def create_app(seed_path: Path | None = None, latency_ms: int | None = None) -> FastAPI:
    path = seed_path or Path(os.environ.get("MOCKS_SEED_PATH", DEFAULT_SEED))
    companies = load_companies(path)
    base_latency = (
        latency_ms if latency_ms is not None else int(os.environ.get("MOCKS_LATENCY_MS", "0"))
    )
    calls: Counter[tuple[str, str]] = Counter()

    app = FastAPI(
        title="Моки внешних сервисов",
        version="1.0.0",
        description="Синтетические ответы БКИ, ЕГРЮЛ/ЕГРИП и ФНС «Прозрачный бизнес». "
        "Реальные данные не используются.",
    )

    async def respond(service: str, inn: str, body: dict[str, Any] | None) -> JSONResponse:
        """Apply latency and failure injection, then return the body or 404."""
        company = companies.get(inn)
        behavior = (company or {}).get("mock_behavior", {}).get(service, {})
        calls[(service, inn)] += 1
        delay = base_latency + int(behavior.get("delay_ms", 0))
        if delay:
            await asyncio.sleep(delay / 1000)
        if calls[(service, inn)] <= int(behavior.get("fail_first_n", 0)):
            return JSONResponse(
                status_code=int(behavior.get("status_code", 503)),
                content={
                    "code": "service_unavailable",
                    "message": f"{service}: сервис временно недоступен",
                },
            )
        if body is None:
            return JSONResponse(
                status_code=404,
                content={"code": "not_found", "message": f"{service}: нет сведений по ИНН {inn}"},
            )
        return JSONResponse(content={"inn": inn, **body})

    @app.get("/health", tags=["service"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "companies": len(companies)}

    @app.get("/egrul/{inn}", tags=["ЕГРЮЛ/ЕГРИП"], summary="Сведения о компании или ИП")
    async def egrul(inn: Inn) -> JSONResponse:
        company = companies.get(inn)
        body = None
        if company is not None:
            body = {
                "name": company["company"]["name"],
                "legal_form": company["company"]["legal_form"],
                **company["mocks"]["egrul"],
            }
        return await respond("egrul", inn, body)

    @app.get("/pb/{inn}", tags=["ФНС «Прозрачный бизнес»"], summary="Финансы, налоги, флаги риска")
    async def pb(inn: Inn) -> JSONResponse:
        company = companies.get(inn)
        return await respond("pb", inn, company["mocks"]["pb"] if company else None)

    @app.get("/bki/report", tags=["БКИ"], summary="Кредитный отчёт")
    async def bki(inn: InnQuery) -> JSONResponse:
        company = companies.get(inn)
        return await respond("bki", inn, company["mocks"]["bki"] if company else None)

    @app.post("/admin/reset", tags=["service"], summary="Сбросить счётчики сбоев")
    async def reset() -> dict[str, str]:
        """For repeatable demos: the БКИ failure for demo-bki happens again."""
        calls.clear()
        return {"status": "reset"}

    return app


app = create_app()

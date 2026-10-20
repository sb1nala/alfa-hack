"""Deterministic schema generation without application lifespan or settings."""

from collections.abc import Sequence
from copy import deepcopy

from fastapi import APIRouter, FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from app.schemas.applications import SSEEvent


def restore_examples(schema: dict, routers: Sequence[APIRouter]) -> None:
    # FastAPI's final exclude_none=True also strips JSON null from opaque example
    # values, making required nullable fields disappear. Restore raw examples only;
    # generated schemas, paths and responses remain authoritative.
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods:
                operation = schema["paths"][route.path][method.lower()]
                for status, response in route.responses.items():
                    for media_type, media in response.get("content", {}).items():
                        if "examples" in media:
                            operation["responses"][str(status)]["content"][media_type][
                                "examples"
                            ] = deepcopy(media["examples"])
                for param in route.dependant.body_params:
                    values = getattr(param.field_info, "openapi_examples", None)
                    if values:
                        operation["requestBody"]["content"][param.field_info.media_type][
                            "examples"
                        ] = deepcopy(values)
                extra = (route.openapi_extra or {}).get("requestBody", {})
                for media_type, media in extra.get("content", {}).items():
                    if "examples" in media:
                        operation["requestBody"]["content"][media_type]["examples"] = deepcopy(
                            media["examples"]
                        )


def install_openapi(app: FastAPI, routers: Sequence[APIRouter]) -> None:
    def openapi() -> dict:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=[{"url": "/"}],
        )
        restore_examples(schema, routers)
        components = schema.setdefault("components", {})
        components["securitySchemes"] = {
            "SessionCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": "access_token",
                "description": "JWT в HttpOnly cookie; только контракт, проверки пока отсутствуют.",
            },
            "CSRFToken": {
                "type": "apiKey",
                "in": "header",
                "name": "X-CSRF-Token",
                "description": "GET /api/auth/csrf; токен в памяти; X-CSRF-Token при мутациях.",
            },
        }
        event_schema = SSEEvent.model_json_schema(ref_template="#/components/schemas/{model}")
        components["schemas"].update(event_schema.pop("$defs", {}))
        components["schemas"]["SSEEvent"] = event_schema
        schema["x-contract-version"] = "v1"
        app.openapi_schema = schema
        return schema

    app.openapi = openapi

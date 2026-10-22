import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response

from app.api import examples as ex
from app.api.applications import ApplicationID
from app.api.contract import endpoint
from app.event_stream import EventStreamResponse, prepare_stream, stream_events

router = APIRouter(prefix="/api/applications", tags=["events"])
SSE_DESCRIPTION = """
SSE для владельца client или underwriter, стандартный EventSource одного
origin: `new EventSource('/api/applications/' + id + '/events')`. Cookie JWT
передаётся браузером автоматически; заголовок Authorization и CSRF для GET не нужны.

Успешный ответ: `text/event-stream`, UTF-8, Cache-Control: no-cache,
X-Accel-Buffering: no. Кадр содержит `id: <UUID application_event>`,
`event: status_changed|application_updated|decision_created|document_uploaded`,
`data: <одна JSON-строка>` и пустую строку в конце. Схемы data/envelope — SSEEvent
(расширение x-event-schema). Название event совпадает с data.event_type.
Heartbeat — комментарий `: heartbeat` и пустая строка каждые ~15 секунд; без id,
не меняет ревизию и не создаёт событие аудита. `retry: 3000` задаёт задержку
переподключения браузера, не является бизнес-событием.

При переподключении EventSource сам передаёт Last-Event-ID. Сервер воспроизводит
события строго после курсора в порядке записи; возможны повторы, дедупликация по id.
UUID — непрозрачный курсор, сортировать его лексикографически нельзя. Чужой курсор
и неизвестный курсор — 404. Очистки/TTL событий нет; 410 зарезервирован для
подтверждённого истечения после согласования политики хранения.
Без курсора — новые события с момента подключения. На onopen клиент перечитывает
карточку (в том числе после reconnect), использует revision для защиты от устаревших
обновлений. При ошибке соединения перечитывает карточку/историю; EventSource
не предоставляет коду HTTP-статус и не позволяет задавать произвольные заголовки.
История доступна отдельно через GET /history. События содержат только публичную
инвалидацию состояния; подробное решение нужно получать из подходящей роли карточки.
"""
STREAM_EXAMPLES = {
    "status_changed": (
        "retry: 3000\n"
        + f"id: {ex.EVENT_ID}\nevent: status_changed\n"
        + "data: "
        + json.dumps(ex.SSE_DATA, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    ),
    "heartbeat": ": heartbeat\n\n",
}
EVENT_EXAMPLES = {"status_changed": ex.SSE_FRAME}
for index, event_type in enumerate(
    ("application_updated", "decision_created", "document_uploaded"), start=2
):
    frame = {
        "id": f"70000000-0000-4000-8000-{index:012d}",
        "event": event_type,
        "data": {
            "event_type": event_type,
            "application_id": ex.APPLICATION_ID,
            "revision": index + 1,
            "status": "CHECKING",
            "occurred_at": ex.NOW,
        },
    }
    EVENT_EXAMPLES[event_type] = frame
    STREAM_EXAMPLES[event_type] = (
        f"id: {frame['id']}\nevent: {event_type}\ndata: "
        + json.dumps(frame["data"], ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )
stream_contract = endpoint(
    None, {}, SSE_DESCRIPTION, role="both", extra_errors=(410,), implemented=True
)
# The ordinary guards depend on a request-scoped yield DB session. Authenticate
# explicitly in prepare_stream so no dependency holds a connection through SSE.
stream_contract["dependencies"] = []
stream_contract["responses"][200].update(
    {
        "headers": {
            "Cache-Control": {"schema": {"type": "string", "const": "no-cache"}},
            "X-Accel-Buffering": {"schema": {"type": "string", "const": "no"}},
        },
        "content": {
            "text/event-stream": {
                "schema": {"type": "string"},
                "examples": ex.examples(STREAM_EXAMPLES),
                "x-event-schema": {"$ref": "#/components/schemas/SSEEvent"},
                "x-event-examples": ex.examples(EVENT_EXAMPLES),
            }
        },
    }
)


@router.get("/{application_id}/events", response_class=Response, **stream_contract)
async def events(
    application_id: ApplicationID,
    request: Request,
    last_event_id: Annotated[
        UUID | None, Header(alias="Last-Event-ID", examples=[ex.EVENT_ID])
    ] = None,
) -> Response:
    start = await prepare_stream(request, application_id, last_event_id)
    return EventStreamResponse(
        stream_events(request, start), send_timeout=request.app.state.settings.sse_send_timeout
    )

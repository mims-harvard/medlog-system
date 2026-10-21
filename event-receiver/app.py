"""FastAPI event receiver for the MedLog OpenAPI spec.

Validates each POSTed event against ../schemas/openapi.yaml, then forwards it as an
OTLP log record to the collector endpoint configured via OTEL_EXPORTER_OTLP_* env vars / .env.
"""

import json
import hmac
import os
import threading
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from openapi_core import OpenAPI
from openapi_core.contrib.starlette.requests import StarletteOpenAPIRequest
from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from event_store import init_db, record_if_new
from schema_paths import schema_file
from semantics import build_attributes, span_attributes, validate_context, SemanticError

load_dotenv(os.getenv("ENV_FILE"))

SPEC_PATH = schema_file("openapi.yaml")
with SPEC_PATH.open() as f:
    _spec_dict = yaml.safe_load(f)
# The spec's example `servers:` (SwaggerHub mock / docs placeholder) don't match this server's
# actual host, so path matching is done relative to "/" instead of validating against them.
_spec_dict["servers"] = [{"url": "/"}]
openapi = OpenAPI.from_dict(_spec_dict, base_uri=SPEC_PATH.as_uri())
# Spec has no response body schema (only a bare "201" description), so skip response validation.

_logger_provider = LoggerProvider()
_logger_provider.add_log_record_processor(SimpleLogRecordProcessor(OTLPLogExporter()))
logger = _logger_provider.get_logger("medlog-event-receiver")

_trace_provider = TracerProvider()
_trace_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(_trace_provider)
tracer = trace.get_tracer("medlog-event-receiver")

class ApiError(Exception):
    def __init__(self, code: str, message: str, status_code: int, details: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _error_body(code: str, message: str, details: dict | None = None) -> dict:
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    return error


def _error_response(code: str, message: str, status_code: int, details: dict | None = None):
    return JSONResponse(_error_body(code, message, details), status_code=status_code)


def require_api_key(x_api_key: str = Header(default="")):
    expected_key = os.getenv("MEDLOG_API_KEY")
    if not expected_key or not hmac.compare_digest(x_api_key, expected_key):
        raise ApiError("unauthorized", "A valid X-API-Key header is required.", 401)


app = FastAPI(dependencies=[Depends(require_api_key)])
app.state.db_conn = init_db(os.getenv("EVENT_ID_DB_PATH", str(Path(__file__).parent / "data" / "event_ids.db")))
app.state.db_lock = threading.Lock()
app.state.run_roots: dict[str, str] = {}
app.state.span_by_event_id: dict[str, object] = {}


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, error: ApiError):
    return _error_response(error.code, error.message, error.status_code, error.details)


async def _validate(request: Request) -> dict:
    try:
        body = await request.body()
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise ApiError("bad_request", "The request body must contain valid JSON.", 400)

    openapi_request = StarletteOpenAPIRequest(request, body=body)
    result = openapi.unmarshal_request(openapi_request)
    if result.errors:
        raise ApiError(
            "validation_error",
            "The request body does not match the event schema.",
            400,
            {"reason": "; ".join(str(error) for error in result.errors)},
        )
    try:
        validate_context(payload.get("semantic_context"))
    except SemanticError as error:
        raise ApiError("validation_error", "The semantic context is invalid.", 400,
                       {"reason": str(error)})
    return payload


def _record_event(payload: dict) -> None:
    if not record_if_new(app.state.db_conn, app.state.db_lock, payload["event_id"]):
        raise ApiError(
            "duplicate_event",
            "An event with this event_id was already received.",
            400,
        )


async def get_event_payload(payload: dict = Depends(_validate)) -> dict:
    _record_event(payload)
    return payload


def _emit(payload: dict, attributes: dict) -> None:
    now_ns = time.time_ns()
    attributes = {k: v for k, v in attributes.items() if v is not None}
    logger.emit(
        timestamp=now_ns,
        observed_timestamp=now_ns,
        body=json.dumps(payload),
        attributes=attributes,
        severity_number=SeverityNumber.INFO,
        severity_text="INFO",
    )


def _register_run_root(payload: dict, event_type: str) -> str | None:
    run_id = payload.get("run_id")
    if not run_id:
        return None

    root_event_id = app.state.run_roots.get(run_id)
    if root_event_id is None:
        if event_type == "inference-start":
            root_event_id = payload.get("event_id") or f"synthetic:{run_id}"
            app.state.run_roots[run_id] = root_event_id
        else:
            app.state.run_roots[run_id] = f"synthetic:{run_id}"
            root_event_id = app.state.run_roots[run_id]
    elif event_type == "inference-start" and root_event_id.startswith("synthetic:"):
        app.state.run_roots[run_id] = payload.get("event_id") or root_event_id
        root_event_id = app.state.run_roots[run_id]
    return root_event_id


def _emit_trace(payload: dict, event_type: str, attributes: dict) -> None:
    run_id = payload.get("run_id")
    event_id = payload.get("event_id")
    root_event_id = _register_run_root(payload, event_type)
    parent_event_id = payload.get("parent_event_id")
    parent_context = None

    if parent_event_id and parent_event_id in app.state.span_by_event_id:
        parent_context = trace.set_span_in_context(app.state.span_by_event_id[parent_event_id])
    elif root_event_id and root_event_id in app.state.span_by_event_id:
        parent_context = trace.set_span_in_context(app.state.span_by_event_id[root_event_id])

    attributes_for_span = {
        "medlog.event_type": event_type,
        "medlog.event_id": event_id,
        "medlog.run_id": run_id,
        "medlog.parent_event_id": parent_event_id,
        "gen_ai.conversation.id": run_id,
        **attributes,
    }
    attributes_for_span = span_attributes(attributes_for_span)

    span_kwargs = {
        "name": f"medlog.{event_type}",
        "attributes": attributes_for_span,
    }
    if parent_context is not None:
        span_kwargs["context"] = parent_context

    with tracer.start_as_current_span(**span_kwargs) as span:
        evaluation = (payload.get("semantic_context") or {}).get("evaluation") or {}
        if evaluation.get("emit_event", False):
            span.add_event("gen_ai.evaluation.result", attributes={
                key: value for key, value in attributes_for_span.items()
                if key.startswith("gen_ai.evaluation.") or key == "gen_ai.response.id"
            })
        if event_id is not None:
            app.state.span_by_event_id[event_id] = span


@app.post("/event/inference-start", status_code=201)
async def post_inference_start(payload: dict = Depends(get_event_payload)):
    attributes = build_attributes(payload, "inference-start")
    _emit(payload, attributes)
    _emit_trace(payload, "inference-start", attributes)
    return {"event_id": payload.get("event_id")}


@app.post("/event/internal-artifact", status_code=201)
async def post_internal_artifact(payload: dict = Depends(get_event_payload)):
    attributes = build_attributes(payload, "internal-artifact")
    _emit(payload, attributes)
    _emit_trace(payload, "internal-artifact", attributes)
    return {"event_id": payload.get("event_id")}


@app.post("/event/human-output", status_code=201)
async def post_human_output(payload: dict = Depends(get_event_payload)):
    attributes = build_attributes(payload, "human-output")
    _emit(payload, attributes)
    _emit_trace(payload, "human-output", attributes)
    return {"event_id": payload.get("event_id")}


@app.post("/event/outcome", status_code=201)
async def post_outcome(payload: dict = Depends(get_event_payload)):
    attributes = build_attributes(payload, "outcome")
    _emit(payload, attributes)
    _emit_trace(payload, "outcome", attributes)
    return {"event_id": payload.get("event_id")}


@app.post("/event/user-feedback", status_code=201)
async def post_user_feedback(payload: dict = Depends(get_event_payload)):
    attributes = build_attributes(payload, "user-feedback")
    _emit(payload, attributes)
    _emit_trace(payload, "user-feedback", attributes)
    return {"event_id": payload.get("event_id")}


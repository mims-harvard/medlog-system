"""MedLog 0.2 semantic mappings; OpenTelemetry semantic conventions 1.40.0.

Legacy projections stay intact. Explicit semantic_context values add attributes.
No clinical identities, evaluation scores, or message contents are inferred.
"""
import json
from datetime import datetime
from importlib.resources import files

from jsonschema import Draft202012Validator

SEMANTIC_VERSION = "0.2.0"


class SemanticError(ValueError):
    pass


_STRUCTURED_SCHEMAS = {
    key: Draft202012Validator(json.loads(
        files("semantic_schemas").joinpath("gen-ai-" + filename + ".json").read_text()
    ))
    for key, filename in {
        "input_messages": "input-messages",
        "output_messages": "output-messages",
        "system_instructions": "system-instructions",
        "retrieval_documents": "retrieval-documents",
    }.items()
}


def validate_context(context: dict | None) -> None:
    """Validate rich values after the OpenAPI envelope, before deduplication."""
    if context is None:
        return
    for key, validator in _STRUCTURED_SCHEMAS.items():
        if key in context:
            error = next(validator.iter_errors(context[key]), None)
            if error:
                path = ".".join(str(part) for part in error.absolute_path)
                raise SemanticError(f"semantic_context.{key}.{path}: {error.message}")
    period = (context.get("fhir") or {}).get("occurred_period") or {}
    if "start" in period and "end" in period:
        start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
        if end < start:
            raise SemanticError("fhir.occurred_period.end must not precede start")


def span_attributes(attributes: dict) -> dict:
    """Spans need scalars; JSON preserves structured values without Python repr."""
    return {key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if isinstance(value, (dict, list)) else value
            for key, value in attributes.items() if value is not None}


def _common_attributes(payload: dict, event_type: str) -> dict:
    """Attributes shared by every MedLog event: BaseEvent fields plus the gen_ai conversation id.

    event_type backs medlog_events.EventType, the leading column of that table's ORDER BY key
    (clickhouse/ddl/001_events_table.sql), so every handler must pass its own event type.
    """
    system_metadata = payload.get("system_metadata") or {}
    return {
        "medlog.event_type": event_type,
        "medlog.event_id": payload.get("event_id"),
        "medlog.run_id": payload.get("run_id"),
        "medlog.parent_event_id": payload.get("parent_event_id"),
        "medlog.timestamp": payload.get("timestamp"),
        "medlog.medlog_version": payload.get("medlog_version"),
        "medlog.system_metadata.hostname": system_metadata.get("hostname"),
        "medlog.system_metadata.app_name": system_metadata.get("app_name"),
        "medlog.system_metadata.proc_id": system_metadata.get("proc_id"),
        "medlog.system_metadata.latency_ms": system_metadata.get("latency_ms"),
        "gen_ai.conversation.id": payload.get("run_id"),
    }


def inference_start(payload: dict) -> dict:
    model_instance = payload.get("model_instance") or {}
    user_identity = payload.get("user_identity") or {}
    target_identity = payload.get("target_identity") or {}
    inputs = payload.get("inputs") or {}
    attributes = {
        **_common_attributes(payload, "inference-start"),
        # Dual-written: gen_ai.* is the OTel semconv alias, medlog.* is the name the ClickHouse
        # materialized view reads (clickhouse/ddl/003_run_state_mv.sql).
        "gen_ai.request.model": model_instance.get("model_id"),
        "medlog.model_instance.model_id": model_instance.get("model_id"),
        "medlog.model_instance.model_version": model_instance.get("model_version"),
        "medlog.model_instance.model_card_url": model_instance.get("model_card_url"),
        "medlog.model_instance.data_sheet_url": model_instance.get("data_sheet_url"),
        "medlog.user_identity.caller_type": user_identity.get("caller_type"),
        "medlog.user_identity.caller_id": user_identity.get("caller_id"),
        "medlog.target_identity.target_type": target_identity.get("target_type"),
        "medlog.target_identity.target_id": target_identity.get("target_id"),
        "medlog.inputs.prompt": inputs.get("prompt"),
        "medlog.inputs.input_uri": inputs.get("input_uri"),
    }
    return attributes


def internal_artifact(payload: dict) -> dict:
    artifact = payload.get("artifact") or {}
    attributes = {
        **_common_attributes(payload, "internal-artifact"),
        "medlog.artifact.artifact_type": artifact.get("artifact_type"),
        "medlog.artifact.artifact_uri": artifact.get("artifact_uri"),
        "medlog.artifact.mime_type": artifact.get("mime_type"),
        "medlog.artifact.description": artifact.get("description"),
    }
    return attributes


def human_output(payload: dict) -> dict:
    output = payload.get("output") or {}
    attributes = {
        **_common_attributes(payload, "human-output"),
        # Dual-written, as with gen_ai.request.model above.
        "gen_ai.output.type": output.get("output_type"),
        "medlog.output.output_type": output.get("output_type"),
        "medlog.output.output_uri": output.get("output_uri"),
        "medlog.output.summary": output.get("summary"),
    }
    return attributes


def outcome(payload: dict) -> dict:
    outcome = payload.get("outcome") or {}
    attributes = {
        **_common_attributes(payload, "outcome"),
        "medlog.outcome.outcome_type": outcome.get("outcome_type"),
        "medlog.outcome.occurred_at": outcome.get("occurred_at"),
        "medlog.outcome.description": outcome.get("description"),
        "medlog.outcome.evidence_uri": outcome.get("evidence_uri"),
    }
    return attributes


def user_feedback(payload: dict) -> dict:
    feedback = payload.get("feedback") or {}
    attributes = {
        **_common_attributes(payload, "user-feedback"),
        "medlog.feedback.feedback_type": feedback.get("feedback_type"),
        "medlog.feedback.rating_value": feedback.get("rating_value"),
        "medlog.feedback.value": feedback.get("value"),
        "medlog.feedback.text": feedback.get("text"),
    }
    return attributes


_BUILDERS = {
    "inference-start": inference_start,
    "internal-artifact": internal_artifact,
    "human-output": human_output,
    "outcome": outcome,
    "user-feedback": user_feedback,
}

# Paths relative to semantic_context. These aliases never overwrite legacy keys.
STANDARD_MAPPINGS = {
    "input_messages": "gen_ai.input.messages",
    "system_instructions": "gen_ai.system_instructions",
    "output_messages": "gen_ai.output.messages",
    "retrieval_documents": "gen_ai.retrieval.documents",
    "tool.name": "gen_ai.tool.name",
    "tool.call_id": "gen_ai.tool.call.id",
    "tool.type": "gen_ai.tool.type",
    "user.id": "user.id",
    "user.name": "user.name",
    "session.id": "session.id",
    "evaluation.name": "gen_ai.evaluation.name",
    "evaluation.score": "gen_ai.evaluation.score.value",
    "evaluation.label": "gen_ai.evaluation.score.label",
    "evaluation.explanation": "gen_ai.evaluation.explanation",
    "evaluation.response_id": "gen_ai.response.id",
}
CUSTOM_MAPPINGS = {
    "fhir.version": "medlog.fhir.version",
    "fhir.references": "medlog.fhir.references",
    "fhir.occurred_period": "medlog.fhir.occurred_period",
    "fhir.recorded": "medlog.fhir.recorded",
    "provenance.entities": "medlog.prov.entities",
    "provenance.activities": "medlog.prov.activities",
    "provenance.agents": "medlog.prov.agents",
    "provenance.relations": "medlog.prov.relations",
    "lineage.run_id": "medlog.openlineage.run_id",
    "lineage.job": "medlog.openlineage.job",
    "lineage.inputs": "medlog.openlineage.inputs",
    "lineage.outputs": "medlog.openlineage.outputs",
}


def _lookup(context, path):
    value = context
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def build_attributes(payload: dict, event_type: str) -> dict:
    attributes = _BUILDERS[event_type](payload)
    context = payload.get("semantic_context")
    if context is not None:
        attributes["medlog.semantic_version"] = SEMANTIC_VERSION
        for path, key in {**STANDARD_MAPPINGS, **CUSTOM_MAPPINGS}.items():
            value = _lookup(context, path)
            if value is not None:
                attributes.setdefault(key, value)
    return attributes

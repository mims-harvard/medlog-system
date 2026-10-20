# MedLog semantic interoperability

The receiver accepts an optional `semantic_context` object on all five event endpoints.
Existing fields, endpoints, event IDs, responses, timestamps and MedLog attributes retain
their original meanings. New clients still supply the legacy required fields, including
artifact/output URIs. `medlog_version` remains the producer's protocol version;
`medlog.semantic_version = 0.2.0` identifies this mapping only when context is supplied.

Start with the [complete inference example](examples/semantic-inference.json),
[OpenAPI contract](../openapi.yaml), [Weaver registry](../semconv/medlog.yaml), or
[FHIR export guide](fhir-export.md).

## Versioned standards

The registry [manifest](../semconv/manifest.yaml) pins OpenTelemetry semantic conventions
**1.40.0**. This deliberately does not track the latest experimental GenAI conventions.
Its schema URL is an identifier for this repository's registry, not a claim that a schema
has been published at that address. Validate with Weaver 0.26.1:

```sh
weaver registry check -r semconv
```

Weaver resolves the pinned upstream registry over the network. Request validation works
offline: the four original GenAI JSON schemas are vendored in
[`event-receiver/semantic_schemas`](../event-receiver/semantic_schemas), with their Apache-2.0
license. The [upstream definitions](https://github.com/open-telemetry/semantic-conventions/tree/v1.40.0/docs/gen-ai)
are authoritative for message, instruction and retrieved-document shapes. The OpenAPI
schema validates context envelopes; these JSON schemas validate the structured values.
An invalid context returns the existing `validation_error` response before consuming the
event ID, so a corrected request can reuse it.

FHIR references declare **4.0.1 or 5.0.0**. The export recipe targets **FHIR R5 (5.0.0)**.
PROV relationships use the [W3C PROV-O recommendation](https://www.w3.org/TR/prov-o/).
OpenLineage references follow its [job/run/dataset model](https://openlineage.io/docs/spec/object-model/).
USCDI is a clinical data classification, not a telemetry namespace or resource format;
these correspondences do not claim USCDI certification or completeness.

## OpenTelemetry mapping

All input paths below are relative to `semantic_context`. Attributes are added to both
the existing MedLog log and span. No message, user identity, score or clinical fact is inferred.

| Input | Registered attribute | Meaning / constraint |
|---|---|---|
| `input_messages` | `gen_ai.input.messages` | Ordered messages with `role` and `parts`; preserves supplied roles and content |
| `system_instructions` | `gen_ai.system_instructions` | Separate instructions, not instructions embedded in message history |
| `output_messages` | `gen_ai.output.messages` | One message per output choice, including `finish_reason` |
| `tool.name`, `tool.call_id`, `tool.type` | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.type` | Explicit tool identity and invocation |
| `retrieval_documents` | `gen_ai.retrieval.documents` | Objects with `id` and numeric `score`; extra document properties retained |
| `user.id`, `user.name` | `user.id`, `user.name` | Explicit user, distinct from an immediate calling service |
| `session.id` | `session.id` | Explicit session; not inferred from an inference run |
| `evaluation.name` | `gen_ai.evaluation.name` | Metric name; producer documents its scoring system |
| `evaluation.score`, `evaluation.label` | `gen_ai.evaluation.score.value`, `gen_ai.evaluation.score.label` | Numeric score / categorical interpretation; zero preserved |
| `evaluation.explanation` | `gen_ai.evaluation.explanation` | Evaluator's explanation |
| `evaluation.response_id` | `gen_ai.response.id` | Identifies the evaluated completion |

An evaluation requires its name. With `evaluation.emit_event: true`, the receiver also
adds `gen_ai.evaluation.result` to the existing span. This is a span event, not another
MedLog log record: log counts, duplicate handling and run-summary aggregation are unchanged.
The supplied collector configuration only exports logs; viewing span events requires a
trace-capable OTLP endpoint/pipeline. Evaluation attributes remain available in logs.

Legacy aliases are preserved exactly:

| Existing source | Existing alias | Compatibility qualification |
|---|---|---|
| `run_id` | `gen_ai.conversation.id` | Historical run/conversation equivalence; not an inferred user session |
| `model_instance.model_id` | `gen_ai.request.model` | Still emitted for existing producers, including non-GenAI models |
| `output.output_type` | `gen_ai.output.type` | Historical alias of actual human output; upstream describes requested output modality |

These qualifications are intentional compatibility exceptions, not a claim that every
legacy event is a fully conformant GenAI operation. Explicit context cannot override
these attributes. `inputs.prompt` remains `medlog.inputs.prompt`, `output.summary` remains
`medlog.output.summary`, and ratings/thumbs/text/surveys retain their MedLog attributes.
A summary is not automatically the complete response; a rating is not automatically a
named evaluation. Source facts that have no equivalent remain in `medlog.*`.

## Registered clinical and provenance semantics

FHIR rows below describe export destinations, not automatic resource creation. All
`fhir.*`, `provenance.*` and `lineage.*` input paths are relative to `semantic_context`.

| Registered attribute(s) | Source | Standard / FHIR R5 destination |
|---|---|---|
| `medlog.event_id`, `medlog.event_type` | Base event | Stable local AuditEvent identity and locally coded audit activity; MedLog fragments can be PROV Entities |
| `medlog.run_id`, `medlog.parent_event_id` | Base event | Grouping and fragment relationship; not automatically a FHIR Encounter or a PROV derivation |
| `medlog.timestamp`, `medlog.medlog_version`, `medlog.semantic_version` | Base event / mapping | Original event time and format versions retained with source record |
| `medlog.system_metadata.*` | System metadata | Operational metadata; exporter identifies `AuditEvent.source.observer` explicitly |
| `medlog.model_instance.model_id`, `.model_version` | Model | `Device.identifier`, `Device.version.value` when creating an identified software Device |
| `medlog.model_instance.model_card_url`, `.data_sheet_url` | Model | Source documentation links; retain in source record or profile-defined extension |
| `medlog.user_identity.caller_type`, `.caller_id` | Immediate caller | `AuditEvent.agent.who.identifier` with an exporter-supplied identifier system; do not equate a service account with a clinician |
| `user.id`, `user.name` | Explicit user | Resolve to Practitioner/PractitionerRole/Patient using a configured identifier system; not directly a FHIR resource ID |
| `medlog.target_identity.target_type`, `.target_id` | Legacy target | Resolve Patient or other resource through an explicit mapping; no fabricated Patient references |
| `medlog.inputs.prompt`, `.input_uri`; `gen_ai.input.messages`, `gen_ai.system_instructions` | Inputs | Input source content; explicit Observation/DocumentReference links can anchor it clinically |
| `medlog.artifact.*`; `gen_ai.tool.*`, `gen_ai.retrieval.documents` | Artifacts | Intermediate entities, tool activities and source-document references; no universal FHIR resource equivalent |
| `medlog.output.*`; `gen_ai.output.messages`, `gen_ai.output.type` | Outputs | DocumentReference for documents, RiskAssessment for a clinically defined risk prediction; no conversion based solely on output modality |
| `medlog.outcome.*` | Outcomes | Reference a clinically appropriate resource, e.g. AdverseEvent; `validated` does not establish a clinical outcome code |
| `medlog.feedback.*`; `gen_ai.evaluation.*` | Feedback/evaluation | QuestionnaireResponse only for a known questionnaire and item mapping; otherwise retain source feedback |
| `session.id` | Explicit session | Application session, not automatically an Encounter |
| `medlog.fhir.version` | `fhir.version` | Version governing supplied references |
| `medlog.fhir.references` | `fhir.references` | Role-labelled FHIR References, with `reference` or `{system,value}` identifier; remove local `role` before copying into FHIR |
| `medlog.fhir.occurred_period` | `fhir.occurred_period` | `AuditEvent.occurredPeriod`; start/end are supplied execution times |
| `medlog.fhir.recorded` | `fhir.recorded` | `AuditEvent.recorded`; distinct from execution interval and ingest time |
| `medlog.prov.entities` | `provenance.entities` | IDs of PROV Entities; data references become `Provenance.target` or `.entity.what` according to relation direction |
| `medlog.prov.activities` | `provenance.activities` | IDs of PROV Activities; invocation represented in an export activity mapping |
| `medlog.prov.agents` | `provenance.agents` | IDs and types Agent/SoftwareAgent/Person/Organization; resolved FHIR `Provenance.agent.who` |
| `medlog.prov.relations` | `provenance.relations` | Explicit directed PROV relationships; export recipe below |
| `medlog.openlineage.run_id` | `lineage.run_id` | External OpenLineage `run.runId`; independent of MedLog `run_id` |
| `medlog.openlineage.job` | `lineage.job` | OpenLineage `job.namespace` and `job.name` |
| `medlog.openlineage.inputs`, `.outputs` | `lineage.inputs`, `.outputs` | Dataset namespace/name pairs; no direct standard FHIR field |

Reference roles are `caller`, `user`, `target`, `encounter`, `model`, `input`, `artifact`,
`output`, `outcome`, `feedback`, and `facility`. For example, a `target` reference may name
Patient or Appointment; `encounter` supplies encounter context, `model` identifies a
Device, and `feedback` may identify a QuestionnaireResponse. References are stored, not
fetched or checked against a FHIR server. The caller is responsible for their validity.

For `provenance.relations`, `source` and `target` are absolute URIs (URNs are allowed):
`used` points Activity → Entity, `wasGeneratedBy` Entity → Activity, and
`wasAssociatedWith` Activity → Agent. These names expand in `http://www.w3.org/ns/prov#`.
The receiver validates structure and known relation names; graph completeness, referenced
entity existence and domain/range consistency remain exporter responsibilities. A
`parent_event_id` alone does not establish any of these relationships.

USCDI correspondence from MedLog R2: caller/user/facility references can identify care-team
and facility context; target/encounter references anchor the patient interaction; input
references can identify lab results, vital signs, medications, clinical notes or imaging;
outcome references can identify adverse events. These are semantic correspondences.
Clinical codes, units, questionnaire definitions and local implementation-guide requirements
must come from the source clinical system. See the [USCDI catalog](https://www.healthit.gov/isp/united-states-core-data-interoperability-uscdi).

## Transport and storage

Log attributes preserve native structured OTLP values. Spans serialize objects/arrays as
compact Unicode JSON because the Python span API only accepts scalar/simple sequence
attributes. JSON is used consistently, never Python dictionary repr. The log body remains
the JSON serialization of the submitted payload, including its optional context (not a
byte-for-byte copy of request formatting). Empty collections and zero scores survive.

The pinned [ClickHouse exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.158.0/exporter/clickhouseexporter/internal/util.go)
uses the collector's [JSON conversion for maps and arrays](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.158.0/pdata/pcommon/value.go)
to populate strings in `LogAttributes`;
use JSON extraction to read complex values. The body is the authoritative reconstruction
source. Existing materialized views continue reading the same scalar `medlog.*` keys;
no DDL changes or historical backfill are required. The Python SDK/exporter minimum is
1.44, the version tested for structured log encoding. No arbitrary standard attribute
passthrough is accepted in context: only the documented fields are projected.

To send the example (each event ID is write-once):

```sh
curl -X POST "$MEDLOG_API_URL/event/inference-start" \
  -H "X-API-Key: $MEDLOG_API_KEY" -H 'Content-Type: application/json' \
  --data-binary @docs/examples/semantic-inference.json
```

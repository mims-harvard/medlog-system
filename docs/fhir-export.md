# Exporting MedLog records to FHIR R5

This is an export contract and worked example for an adapter. The receiver stores
semantic references; it does not expose a FHIR export endpoint, create resources or send
requests to a FHIR server. See the [registered attribute crosswalk](semantic-standards.md).

Use the original event JSON (`medlog_events.Body`) as the export source. It retains
structured values and optional fields that are not indexed. Group by `run_id` only when
a run-level export is desired; preserve each `event_id` and fragment relationship.
A run can remain incomplete because outcomes and feedback can arrive later.

## Required export inputs

Choose a FHIR version and deployment profile explicitly. This recipe uses R5, package
`hl7.fhir.r5.core#5.0.0`. R4 references can be ingested but must not be copied into an R5
Bundle without a deliberate version conversion. Supply:

- The audit observer (Device or another permitted reference), immediate calling agent,
  and whether that agent initiated the action.
- The recording instant, preferably `semantic_context.fhir.recorded`; an exporter may
  use the captured OTLP ingest timestamp with that policy documented. Do not substitute
  execution start time silently.
- Resolvable clinical references, or identifier systems for resolving legacy IDs.
- Locally governed codes/profiles for the audit activity, clinical outputs and outcomes.

The receiver's timestamp and short event span do not measure the full inference interval.
Only use supplied `fhir.occurred_period` for that interval. A latency measurement alone
should not fabricate start/end timestamps. Never infer audit success from a clinical
outcome such as `validated` or `contradicted`.

## Export steps

1. Read the source record and verify the declared FHIR version. Keep an export ledger
   keyed by event ID and mapping version for idempotency. For this recipe, the event UUID
   is the AuditEvent resource ID; confirm the target server permits client-assigned IDs.
2. Build an **AuditEvent** for the recorded interaction. Map explicit occurrence/recording
   times; include the immediate caller in `agent`, an explicit human user as an additional
   agent when appropriate, and the audit observer in `source.observer`. Map roles only
   from known clinical/security role codes.
3. Strip the local `role` discriminator from each stored FHIR reference. Resolve `target`
   and `encounter` to the appropriate R5 patient/encounter fields when their types match.
   Put model/input/output/artifact references in `entity.what` as appropriate. A target
   Appointment belongs in an entity reference, not the patient field.
4. Reuse existing clinical resources. Create Device, DocumentReference, RiskAssessment,
   AdverseEvent or QuestionnaireResponse only when the adapter has the required clinical
   data, codes and profile mappings. Plain text output is insufficient for RiskAssessment;
   a rating alone is insufficient for QuestionnaireResponse.
5. If exporting provenance, resolve PROV URIs to FHIR references. Generated entities become
   **Provenance.target**; `used` input entities become **Provenance.entity.what** with a
   suitable entity role (normally `source`); associated agents become **Provenance.agent**.
   Supply required `recorded` and agent information. Preserve unmappable graph edges in
   the retained source document, rather than inventing standard FHIR fields.
6. Retain the original MedLog JSON in durable storage. A DocumentReference can point to it
   with `content.attachment.contentType = application/json`; configure its status and URL.
   PROV, OpenLineage and MedLog-specific fields without direct equivalents remain in that
   document, or in separately defined and validated profile extensions.
7. Package resources as a collection Bundle for a portable artifact, or build a transaction
   Bundle with explicit per-entry requests for a server import. Resolve internal references
   with `fullUrl`; do not confuse a collection Bundle with an import transaction.
8. Validate against the selected FHIR package and implementation guide before submission.
   Check terminology, cardinality, reference targets and identifier resolution. Publish
   only through the deployment's configured FHIR client; no server write is performed by
   this repository.

[AuditEvent R5](https://hl7.org/fhir/R5/auditevent.html),
[Provenance R5](https://hl7.org/fhir/R5/provenance.html), and
[Bundle R5](https://hl7.org/fhir/R5/bundle.html) define the target structures.

## Worked example

The input is [semantic-inference.json](examples/semantic-inference.json). The corresponding
[AuditEvent example](examples/fhir-auditevent.json) adds deployment-specific facts:
`Device/medlog-receiver` is the audit observer; `Device/calling-service` is the immediate
requesting service; the local audit code system is under `example.org`. These assumptions
are explicit and must be replaced by the deploying institution. References name existing
resources; this example does not create them or include a complete clinical record.

The UUID is preserved as AuditEvent.id. The supplied execution interval and recorded time
remain distinct. The model Device and input DocumentReference are included as entities;
the patient and encounter are clinical context. Legacy prompt content remains in the
source record and is not relabelled as a clinical observation.

The example passes the official R5 `fhir.schema.json` structural check. Full profile and
terminology validation is a separate deployment check, for example:

```sh
java -jar validator_cli.jar docs/examples/fhir-auditevent.json -version 5.0.0
```

Add the institution's `-ig` and `-profile` options when applicable. The validator may
require access to package and terminology services.

For an R4 export, use an independently versioned mapping: AuditEvent uses `period` rather
than R5 `occurredPeriod`, and its event coding and outcome representation differ. Do not
perform a blind field rename. For any target version, validate the resulting resource
with the official [FHIR validator](https://confluence.hl7.org/display/FHIR/Using+the+FHIR+Validator)
and the institution's implementation guide. The example illustrates mapping and has no
claim of conformance to a specific institutional profile.

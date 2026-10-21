# Pinned OpenTelemetry schemas

The four JSON files are unmodified copies from
https://github.com/open-telemetry/semantic-conventions/tree/v1.40.0/docs/gen-ai.

The receiver loads these files locally from the adjacent `schemas/` directory and validates
structured semantic context with JSON Schema Draft 2020-12. No network resolution
is needed: all references within these schemas are local `$defs` references.
Update these files together with the Weaver dependency, mapping version,
interoperability documentation and regression tests.

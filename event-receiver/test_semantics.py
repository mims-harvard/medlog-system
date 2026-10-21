"""Interoperability tests, separate from the unchanged legacy receiver suite."""
import copy
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from threading import Lock
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.exporter.otlp.proto.common._internal._log_encoder import encode_logs

import app
from semantics import build_attributes, CUSTOM_MAPPINGS, STANDARD_MAPPINGS
from schema_paths import schema_file

ROOT = Path(__file__).resolve().parent.parent


def event(kind, context=None):
    payload = {
        'event_id': str(uuid.uuid4()), 'run_id': str(uuid.uuid4()),
        'timestamp': '2026-09-19T12:00:00Z', 'medlog_version': '0.1',
        'system_metadata': {'hostname': 'h1'},
    }
    payload.update({
        'inference-start': {
            'model_instance': {'model_id': 'model-1'},
            'user_identity': {'caller_type': 'service', 'caller_id': 'svc'},
            'target_identity': {'target_type': 'patient', 'target_id': 'p1'},
            'inputs': {'prompt': 'legacy prompt'},
        },
        'internal-artifact': {'artifact': {'artifact_type': 'tool_call', 'artifact_uri': 'https://example.org/a'}},
        'human-output': {'output': {'output_type': 'text', 'output_uri': 'https://example.org/o', 'summary': 'summary only'}},
        'outcome': {'outcome': {'outcome_type': 'validated'}},
        'user-feedback': {'feedback': {'feedback_type': 'rating', 'rating_value': 4}},
    }[kind])
    if context is not None:
        payload['semantic_context'] = context
    return payload


class SemanticReceiverTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        app.app.state.db_conn.close()
        app.app.state.db_conn = app.init_db(str(Path(self.directory.name) / 'events.db'))
        self.addCleanup(app.app.state.db_conn.close)
        app.app.state.db_lock = Lock()
        app.app.state.run_roots.clear()
        app.app.state.span_by_event_id.clear()
        self.logs = InMemoryLogRecordExporter()
        self.log_provider = LoggerProvider()
        self.log_provider.add_log_record_processor(SimpleLogRecordProcessor(self.logs))
        self.addCleanup(self.log_provider.shutdown)
        self.spans = InMemorySpanExporter()
        self.trace_provider = TracerProvider()
        self.trace_provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.addCleanup(self.trace_provider.shutdown)
        for patcher in (
            patch.dict(os.environ, {'MEDLOG_API_KEY': 'semantic-tests'}),
            patch.object(app, 'logger', self.log_provider.get_logger('test')),
            patch.object(app, 'tracer', self.trace_provider.get_tracer('test')),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app.app)

    def post(self, kind, payload):
        return self.client.post('/event/' + kind, json=payload, headers={'X-API-Key': 'semantic-tests'})

    def test_legacy_attributes_preserved_on_every_endpoint(self):
        for kind in ('inference-start', 'internal-artifact', 'human-output', 'outcome', 'user-feedback'):
            with self.subTest(kind=kind):
                payload = event(kind)
                expected = {k: v for k, v in build_attributes(payload, kind).items() if v is not None}
                payload['semantic_context'] = {'session': {'id': 'session-1'}}
                self.assertEqual(self.post(kind, payload).status_code, 201)
                record = self.logs.get_finished_logs()[-1].log_record
                self.assertEqual({k: record.attributes[k] for k in expected}, expected)
                self.assertEqual(record.attributes['session.id'], 'session-1')
                self.assertEqual(json.loads(record.body), payload)
                self.assertEqual(self.spans.get_finished_spans()[-1].name, 'medlog.' + kind)
        self.assertEqual(len(self.logs.get_finished_logs()), 5)

    def test_native_log_structure_json_spans_and_otlp_roundtrip(self):
        messages = [{'role': 'user', 'parts': [{'type': 'text', 'content': 'café'}]}]
        payload = event('inference-start', {
            'input_messages': messages,
            'system_instructions': [{'type': 'text', 'content': 'Be precise.'}],
            'user': {'id': 'clinician-1', 'name': 'Example'},
        })
        original = copy.deepcopy(payload)
        self.assertEqual(self.post('inference-start', payload).status_code, 201)
        log = self.logs.get_finished_logs()[0].log_record
        span = self.spans.get_finished_spans()[0]
        self.assertEqual(json.loads(span.attributes['gen_ai.input.messages']), messages)
        self.assertEqual(log.attributes['user.id'], 'clinician-1')
        self.assertEqual(log.attributes['medlog.user_identity.caller_id'], 'svc')
        wire = encode_logs(self.logs.get_finished_logs())
        wire = type(wire).FromString(wire.SerializeToString())
        attrs = {a.key: a.value for a in wire.resource_logs[0].scope_logs[0].log_records[0].attributes}
        self.assertEqual(len(attrs['gen_ai.input.messages'].array_value.values), 1)
        role = attrs['gen_ai.input.messages'].array_value.values[0].kvlist_value.values[0]
        self.assertEqual((role.key, role.value.string_value), ('role', 'user'))
        self.assertEqual(payload, original)
        self.assertEqual(json.loads(log.body), original)

    def test_no_invented_user_messages_or_evaluations(self):
        for kind in ('inference-start', 'human-output', 'user-feedback'):
            self.assertEqual(self.post(kind, event(kind)).status_code, 201)
            attrs = self.logs.get_finished_logs()[-1].log_record.attributes
            for key in ('user.id', 'gen_ai.input.messages', 'gen_ai.output.messages', 'gen_ai.evaluation.name', 'medlog.semantic_version'):
                self.assertNotIn(key, attrs)

    def test_output_and_tools(self):
        output = [{'role': 'assistant', 'parts': [{'type': 'text', 'content': 'full answer'}], 'finish_reason': 'stop'}]
        self.assertEqual(self.post('human-output', event('human-output', {'output_messages': output})).status_code, 201)
        attrs = self.spans.get_finished_spans()[-1].attributes
        self.assertEqual(json.loads(attrs['gen_ai.output.messages']), output)
        self.assertEqual(attrs['medlog.output.summary'], 'summary only')
        context = {'tool': {'name': 'search', 'call_id': 'call1', 'type': 'datastore'},
                   'retrieval_documents': [{'id': 'doc1', 'score': 0.9, 'content': 'evidence'}]}
        self.assertEqual(self.post('internal-artifact', event('internal-artifact', context)).status_code, 201)
        attrs = self.spans.get_finished_spans()[-1].attributes
        self.assertEqual(attrs['gen_ai.tool.name'], 'search')
        self.assertEqual(json.loads(attrs['gen_ai.retrieval.documents']), context['retrieval_documents'])

    def test_evaluation_event_is_opt_in_and_never_adds_log_rows(self):
        for enabled in (False, True):
            context = {'evaluation': {'name': 'helpfulness', 'score': 0, 'label': 'fail',
                                     'explanation': 'Insufficient detail', 'response_id': 'response1', 'emit_event': enabled}}
            self.assertEqual(self.post('user-feedback', event('user-feedback', context)).status_code, 201)
            span = self.spans.get_finished_spans()[-1]
            self.assertEqual(span.attributes['gen_ai.evaluation.score.value'], 0)
            self.assertEqual(len(span.events), int(enabled))
            if enabled:
                self.assertEqual(span.events[0].name, 'gen_ai.evaluation.result')
                self.assertEqual(span.events[0].attributes['gen_ai.response.id'], 'response1')
        self.assertEqual(len(self.logs.get_finished_logs()), 2)

    def test_clinical_provenance_and_lineage_roundtrip(self):
        context = {
            'fhir': {'version': '5.0.0', 'references': [{'role': 'target', 'reference': 'Patient/p1'}],
                     'recorded': '2026-09-19T12:01:00Z',
                     'occurred_period': {'start': '2026-09-19T12:00:00Z', 'end': '2026-09-19T12:00:01Z'}},
            'provenance': {'entities': [{'id': 'urn:example:input'}],
                           'activities': [{'id': 'urn:example:run'}],
                           'agents': [{'id': 'urn:example:model', 'type': 'SoftwareAgent'}],
                           'relations': [{'type': 'used', 'source': 'urn:example:run', 'target': 'urn:example:input'}]},
            'lineage': {'run_id': str(uuid.uuid4()), 'job': {'namespace': 'hospital', 'name': 'inference'},
                        'inputs': [{'namespace': 'hospital', 'name': 'notes'}], 'outputs': []},
        }
        payload = event('inference-start', context)
        self.assertEqual(self.post('inference-start', payload).status_code, 201)
        span = self.spans.get_finished_spans()[0]
        self.assertEqual(span.attributes['medlog.run_id'], payload['run_id'])
        self.assertEqual(span.attributes['medlog.openlineage.run_id'], context['lineage']['run_id'])
        self.assertEqual(json.loads(span.attributes['medlog.fhir.references']), context['fhir']['references'])
        self.assertEqual(json.loads(span.attributes['medlog.prov.relations']), context['provenance']['relations'])
        self.assertEqual(json.loads(span.attributes['medlog.openlineage.outputs']), [])

    def test_invalid_extensions_rejected_before_deduplication(self):
        invalid = [
            {'input_messages': [{'parts': []}]},
            {'output_messages': [{'role': 'assistant', 'parts': []}]},
            {'system_instructions': 'plain string'},
            {'retrieval_documents': [{'id': 'doc1', 'score': 'high'}]},
            {'evaluation': {'score': 4}},
            {'evaluation': {'name': 'quality', 'emit_event': 'yes'}},
            {'fhir': {'version': '5.0.0', 'references': [{'role': 'target'}]}},
            {'fhir': {'version': '5.0.0', 'occurred_period': {'start': '2026-09-19T13:00:00Z', 'end': '2026-09-19T12:00:00Z'}}},
            {'provenance': {'relations': [{'type': 'unknown', 'source': 'urn:x:a', 'target': 'urn:x:b'}]}},
            {'lineage': {'run_id': 'not-a-uuid'}},
            {'user': {'id': 10}},
        ]
        for context in invalid:
            with self.subTest(context=context):
                payload = event('inference-start', context)
                response = self.post('inference-start', payload)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()['code'], 'validation_error')
                payload['semantic_context'] = {}
                self.assertEqual(self.post('inference-start', payload).status_code, 201)

    def test_enriched_duplicates_still_rejected(self):
        payload = event('outcome', {'session': {'id': 's1'}})
        self.assertEqual(self.post('outcome', payload).status_code, 201)
        self.assertEqual(self.post('outcome', payload).json()['code'], 'duplicate_event')
        self.assertEqual(len(self.logs.get_finished_logs()), 1)

    def test_all_emitted_attributes_registered(self):
        registry = yaml.safe_load(schema_file('semconv/medlog.yaml').read_text())
        registered = {attribute.get('id', attribute.get('ref'))
                      for group in registry['groups'] for attribute in group['attributes']}
        for kind in ('inference-start', 'internal-artifact', 'human-output', 'outcome', 'user-feedback'):
            self.assertLessEqual(set(build_attributes(event(kind, {}), kind)), registered)
        self.assertLessEqual(set(STANDARD_MAPPINGS.values()) | set(CUSTOM_MAPPINGS.values()), registered)

    def test_documented_example_is_accepted(self):
        payload = json.loads((ROOT / 'docs/examples/semantic-inference.json').read_text())
        self.assertEqual(self.post('inference-start', payload).status_code, 201)

    def test_fhir_example_preserves_supplied_times_and_references(self):
        payload = json.loads((ROOT / 'docs/examples/semantic-inference.json').read_text())
        audit = json.loads((ROOT / 'docs/examples/fhir-auditevent.json').read_text())
        fhir = payload['semantic_context']['fhir']
        self.assertEqual(audit['id'], payload['event_id'])
        self.assertEqual(audit['recorded'], fhir['recorded'])
        self.assertEqual(audit['occurredPeriod'], fhir['occurred_period'])
        self.assertNotEqual(audit['recorded'], audit['occurredPeriod']['start'])
        refs = {r['role']: r['reference'] for r in fhir['references']}
        self.assertEqual(audit['patient']['reference'], refs['target'])
        self.assertEqual(audit['encounter']['reference'], refs['encounter'])
        self.assertEqual(audit['agent'][0]['who']['reference'], refs['caller'])
        self.assertEqual(audit['agent'][1]['who']['reference'], refs['user'])

    def test_ingest_timestamp_and_legacy_output_alias_unchanged(self):
        payload = event('human-output', {})
        with patch.object(app.time, 'time_ns', return_value=123456789):
            self.assertEqual(self.post('human-output', payload).status_code, 201)
        record = self.logs.get_finished_logs()[0].log_record
        self.assertEqual(record.timestamp, 123456789)
        self.assertEqual(record.observed_timestamp, 123456789)
        self.assertEqual(record.attributes['medlog.timestamp'], payload['timestamp'])
        self.assertEqual(record.attributes['gen_ai.output.type'], 'text')
        self.assertEqual(record.attributes['medlog.output.output_type'], 'text')


if __name__ == '__main__':
    unittest.main()

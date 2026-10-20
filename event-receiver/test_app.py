"""Unit tests for app.py using FastAPI's test client (no real server/socket)."""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from threading import Lock
from unittest.mock import patch

import app
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from medlog_tool import Medlog  # noqa: E402


class EventReceiverTestCase(unittest.TestCase):
    def setUp(self):
        app.app.testing = True
        self.api_key_patcher = patch.dict(os.environ, {"MEDLOG_API_KEY": "test-key"})
        self.api_key_patcher.start()
        self.addCleanup(self.api_key_patcher.stop)
        self.db_file = tempfile.NamedTemporaryFile(delete=False)
        self.db_file.close()
        app.app.state.db_conn.close()
        app.app.state.db_conn = app.init_db(self.db_file.name)
        app.app.state.db_lock = Lock()
        self.client = TestClient(app.app)
        self.auth_headers = {"X-API-Key": "test-key"}
        # Avoid emitting real OTLP log records over the network.
        patcher = patch.object(app, "_emit")
        self.emit_mock = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._cleanup_db)

    def _cleanup_db(self):
        app.app.state.db_conn.close()
        Path(self.db_file.name).unlink(missing_ok=True)
        Path(f"{self.db_file.name}-wal").unlink(missing_ok=True)
        Path(f"{self.db_file.name}-shm").unlink(missing_ok=True)

    def test_post_inference_start(self):
        payload = {
            "event_id": "11111111-1111-1111-1111-111111111111",
            "timestamp": "2026-08-24T00:00:00Z",
            "medlog_version": "0.1",
            "system_metadata": {"hostname": "h1"},
            "model_instance": {"model_id": "gpt-4o"},
            "user_identity": {"caller_type": "clinician"},
            "target_identity": {"target_type": "patient"},
            "inputs": {"prompt": "hello"},
        }
        response = self.client.post("/event/inference-start", json=payload, headers=self.auth_headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"event_id": payload["event_id"]})

        duplicate_response = self.client.post(
            "/event/inference-start", json=payload, headers=self.auth_headers
        )
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertEqual(duplicate_response.json()["code"], "duplicate_event")
        self.emit_mock.assert_called_once()

    def test_post_internal_artifact(self):
        payload = {
            "event_id": "22222222-2222-2222-2222-222222222222",
            "timestamp": "2026-08-24T00:00:00Z",
            "medlog_version": "0.1",
            "system_metadata": {"hostname": "h1"},
            "artifact": {"artifact_type": "text_chunk", "artifact_uri": "https://example.com/a"},
        }
        response = self.client.post("/event/internal-artifact", json=payload, headers=self.auth_headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"event_id": payload["event_id"]})

    def test_post_human_output(self):
        payload = {
            "event_id": "33333333-3333-3333-3333-333333333333",
            "timestamp": "2026-08-24T00:00:00Z",
            "medlog_version": "0.1",
            "system_metadata": {"hostname": "h1"},
            "output": {"output_type": "text", "output_uri": "https://example.com/o"},
        }
        response = self.client.post("/event/human-output", json=payload, headers=self.auth_headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"event_id": payload["event_id"]})

    def test_post_outcome(self):
        payload = {
            "event_id": "44444444-4444-4444-4444-444444444444",
            "timestamp": "2026-08-24T00:00:00Z",
            "medlog_version": "0.1",
            "system_metadata": {"hostname": "h1"},
            "outcome": {"outcome_type": "validated"},
        }
        response = self.client.post("/event/outcome", json=payload, headers=self.auth_headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"event_id": payload["event_id"]})

    def test_post_user_feedback(self):
        payload = {
            "event_id": "55555555-5555-5555-5555-555555555555",
            "timestamp": "2026-08-24T00:00:00Z",
            "medlog_version": "0.1",
            "system_metadata": {"hostname": "h1"},
            "feedback": {"feedback_type": "thumbs", "value": "up"},
        }
        response = self.client.post("/event/user-feedback", json=payload, headers=self.auth_headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"event_id": payload["event_id"]})

    def test_trace_routing_uses_run_root_and_parent_event(self):
        run_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        start_event = {
            "event_id": "11111111-1111-1111-1111-111111111111",
            "run_id": run_id,
            "timestamp": "2026-08-24T00:00:00Z",
            "medlog_version": "0.1",
            "system_metadata": {"hostname": "h1"},
            "model_instance": {"model_id": "gpt-4o"},
            "user_identity": {"caller_type": "clinician"},
            "target_identity": {"target_type": "patient"},
            "inputs": {"prompt": "hello"},
        }
        child_event = {
            "event_id": "22222222-2222-2222-2222-222222222222",
            "run_id": run_id,
            "parent_event_id": start_event["event_id"],
            "timestamp": "2026-08-24T00:00:01Z",
            "medlog_version": "0.1",
            "system_metadata": {"hostname": "h1"},
            "artifact": {"artifact_type": "text_chunk", "artifact_uri": "https://example.com/a"},
        }

        with patch.object(app.tracer, "start_as_current_span", wraps=app.tracer.start_as_current_span) as span_factory:
            app._emit_trace(start_event, "inference-start", {"medlog.event_type": "inference-start"})
            app._emit_trace(child_event, "internal-artifact", {"medlog.event_type": "internal-artifact"})

        self.assertIn(run_id, app.app.state.run_roots)
        self.assertEqual(app.app.state.run_roots[run_id], start_event["event_id"])
        self.assertEqual(span_factory.call_args_list[0].kwargs["name"], "medlog.inference-start")
        self.assertEqual(span_factory.call_args_list[1].kwargs["name"], "medlog.internal-artifact")
        self.assertEqual(
            span_factory.call_args_list[1].kwargs["attributes"]["medlog.parent_event_id"],
            start_event["event_id"],
        )

    def test_missing_api_key_returns_error(self):
        response = self.client.post("/event/outcome", json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "unauthorized")

    def test_invalid_api_key_returns_error(self):
        response = self.client.post(
            "/event/outcome", json={}, headers={"X-API-Key": "wrong-key"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "unauthorized")

    def test_invalid_payload_returns_error(self):
        response = self.client.post("/event/outcome", json={}, headers=self.auth_headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_feedback_variants_emit_variant_fields(self):
        variants = [
            ({"feedback_type": "rating", "rating_value": 4}, "medlog.feedback.rating_value", 4),
            ({"feedback_type": "thumbs", "value": "down"}, "medlog.feedback.value", "down"),
            ({"feedback_type": "text", "text": "Clear answer."}, "medlog.feedback.text", "Clear answer."),
            ({"feedback_type": "survey", "text": "Very useful."}, "medlog.feedback.text", "Very useful."),
        ]
        for index, (feedback, attribute_name, attribute_value) in enumerate(variants, 1):
            payload = {
                "event_id": f"{index:08d}-0000-0000-0000-000000000000",
                "timestamp": "2026-08-24T00:00:00Z",
                "medlog_version": "0.1",
                "system_metadata": {"hostname": "h1"},
                "feedback": feedback,
            }
            response = self.client.post(
                "/event/user-feedback", json=payload, headers=self.auth_headers
            )
            self.assertEqual(response.status_code, 201)
            attributes = self.emit_mock.call_args.args[1]
            self.assertEqual(attributes[attribute_name], attribute_value)


class MedlogToolRecordTestCase(unittest.TestCase):
    """Records produced by scripts/medlog_tool.py must be accepted by the receiver."""

    setUp = EventReceiverTestCase.setUp
    _cleanup_db = EventReceiverTestCase._cleanup_db

    @staticmethod
    def _base_event(record, event_id=None, parent_event_id=None):
        header = record["header"]
        payload = {
            "event_id": event_id or header["event_id"],
            "run_id": header["run_id"],
            "timestamp": header["timestamp"],
            "medlog_version": header["medlog_version"],
            "system_metadata": header["system_info"],
        }
        if parent_event_id:
            payload["parent_event_id"] = parent_event_id
        return payload

    def _build_records(self, n_runs=2):
        medlog = Medlog(system_info={"hostname": "h1", "app_name": "medlog-test"})
        medlog.start_run()
        medlog.set_target({"target_type": "patient", "target_id": "patient-1"})
        for index in range(n_runs):
            medlog(
                model_instance={
                    "model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                    "model_version": "bedrock",
                },
                inputs={"prompt": "Does the note document sepsis?"},
                internal_artifacts={
                    "chain_of_thought": f"reasoning {index}",
                    "extracts": ["septic shock noted"],
                },
                outputs={"option": "Yes"},
            )
        run_id = medlog.run_id
        medlog.stop_run()
        return medlog.logs, run_id

    def test_medlog_tool_records_are_accepted(self):
        records, run_id = self._build_records()
        self.assertEqual(len(records), 2)

        for record in records:
            start_payload = {
                **self._base_event(record),
                "model_instance": record["model_instance"],
                "user_identity": record["user_identity"],
                "target_identity": record["target_identity"],
                "inputs": record["inputs"],
            }
            response = self.client.post(
                "/event/inference-start", json=start_payload, headers=self.auth_headers
            )
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json(), {"event_id": record["header"]["event_id"]})

            attributes = self.emit_mock.call_args.args[1]
            self.assertEqual(attributes["medlog.run_id"], run_id)
            self.assertEqual(
                attributes["medlog.model_instance.model_id"],
                record["model_instance"]["model_id"],
            )
            self.assertEqual(
                attributes["medlog.inputs.prompt"], record["inputs"]["prompt"]
            )
            self.assertEqual(attributes["medlog.target_identity.target_id"], "patient-1")

            artifact_payload = {
                **self._base_event(
                    record,
                    event_id=str(uuid.uuid4()),
                    parent_event_id=record["header"]["event_id"],
                ),
                "artifact": {
                    "artifact_type": "reasoning_trace",
                    "artifact_uri": "https://example.com/reasoning",
                    "description": record["internal_artifacts"]["chain_of_thought"],
                },
            }
            response = self.client.post(
                "/event/internal-artifact", json=artifact_payload, headers=self.auth_headers
            )
            self.assertEqual(response.status_code, 201, response.text)
            attributes = self.emit_mock.call_args.args[1]
            self.assertEqual(
                attributes["medlog.parent_event_id"], record["header"]["event_id"]
            )

            output_payload = {
                **self._base_event(
                    record,
                    event_id=str(uuid.uuid4()),
                    parent_event_id=record["header"]["event_id"],
                ),
                "output": {
                    "output_type": "text",
                    "output_uri": "https://example.com/output",
                    "summary": record["outputs"]["option"],
                },
            }
            response = self.client.post(
                "/event/human-output", json=output_payload, headers=self.auth_headers
            )
            self.assertEqual(response.status_code, 201, response.text)
            attributes = self.emit_mock.call_args.args[1]
            self.assertEqual(attributes["medlog.output.summary"], "Yes")

    def test_duplicate_medlog_tool_record_is_rejected(self):
        records, _ = self._build_records(n_runs=1)
        payload = {
            **self._base_event(records[0]),
            "model_instance": records[0]["model_instance"],
            "user_identity": records[0]["user_identity"],
            "target_identity": records[0]["target_identity"],
            "inputs": records[0]["inputs"],
        }
        first = self.client.post(
            "/event/inference-start", json=payload, headers=self.auth_headers
        )
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(
            "/event/inference-start", json=payload, headers=self.auth_headers
        )
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()["code"], "duplicate_event")


if __name__ == "__main__":
    unittest.main()

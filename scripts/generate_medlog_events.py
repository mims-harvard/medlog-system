#!/usr/bin/env python3
"""Generate synthetic MedLog events (per schemas/openapi.yaml) and POST them to an HTTP server.

MedLog is a write-once event log for clinical AI: every time a model is invoked
to interact with a human, another algorithm, or an automated workflow, one or
more MedLog records are created. This script simulates realistic "inference
runs" -- each run emits an inference-start event, zero or more internal
artifacts, a human-output event, and (probabilistically) a downstream outcome
and/or user-feedback event -- chained together via run_id/parent_event_id as
described in the spec, and pushes each event to the corresponding
/event/<name> endpoint of a target server.

Usage:
    python3 scripts/generate_medlog_events.py --url http://localhost:5000 --runs 20
    python3 scripts/generate_medlog_events.py --url http://localhost:5000 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

MEDLOG_VERSION = "0.1"

MODEL_INSTANCES = [
    {"model_id": "gpt-4o", "model_version": "2025-05-01"},
    {"model_id": "sepsis-alert-v2", "model_version": "2024-11-03"},
    {"model_id": "icu-deterioration-rnn", "model_version": "3.2.1"},
    {"model_id": "tetanus-progression-cnn", "model_version": "1.0.0"},
    {"model_id": "attendance-predictor", "model_version": "2025-01-15"},
]

CALLER_TYPES = ["clinician", "patient", "admin", "service"]
TARGET_TYPES = ["patient", "document", "claim", "none"]
ARTIFACT_TYPES = ["text_chunk", "reasoning_trace", "tool_call", "explanation", "other"]
OUTPUT_TYPES = ["text", "image", "video", "audio", "other"]
OUTCOME_TYPES = ["validated", "contradicted", "partial", "unknown"]
FEEDBACK_TYPES = ["rating", "thumbs", "text", "survey"]

HOSTNAMES = ["node-a1", "node-b2", "node-c3", "icu-edge-01", "vn-edge-02"]
APP_NAMES = ["clinician-portal", "llm-proxy", "sepsis-monitor", "wearable-gateway"]


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_iso(offset_seconds: float = 0.0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.isoformat()


def build_system_metadata() -> dict:
    return {
        "hostname": random.choice(HOSTNAMES),
        "app_name": random.choice(APP_NAMES),
        "proc_id": str(random.randint(1000, 65000)),
        "latency_ms": random.randint(20, 4000),
    }


def build_base_event(run_id: str, parent_event_id: str | None, offset_seconds: float = 0.0) -> dict:
    return {
        "event_id": new_uuid(),
        "run_id": run_id,
        "parent_event_id": parent_event_id,
        "timestamp": now_iso(offset_seconds),
        "medlog_version": MEDLOG_VERSION,
        "system_metadata": build_system_metadata(),
    }


def build_inference_start(run_id: str) -> dict:
    event = build_base_event(run_id, parent_event_id=None)
    event.update(
        {
            "model_instance": random.choice(MODEL_INSTANCES),
            "user_identity": {
                "caller_type": random.choice(CALLER_TYPES),
                "caller_id": f"user-{random.randint(1, 500)}",
            },
            "target_identity": {
                "target_type": random.choice(TARGET_TYPES),
                "target_id": f"target-{random.randint(1, 5000)}",
            },
            "inputs": {
                "prompt": random.choice(
                    [
                        "Summarize the patient's last 24 hours of vitals.",
                        "Predict probability of ICU deterioration in the next 6 hours.",
                        "Assess tetanus progression from wearable accelerometer signal.",
                        "Draft a sepsis quality report for this encounter.",
                    ]
                ),
                "input_uri": f"s3://medlog-inputs/{new_uuid()}.json",
            },
        }
    )
    return event


def build_internal_artifact(run_id: str, parent_event_id: str, offset_seconds: float) -> dict:
    event = build_base_event(run_id, parent_event_id, offset_seconds)
    artifact_type = random.choice(ARTIFACT_TYPES)
    event["artifact"] = {
        "artifact_type": artifact_type,
        "artifact_uri": f"s3://medlog-artifacts/{new_uuid()}.txt",
        "mime_type": "text/plain",
        "description": f"{artifact_type} produced during inference",
    }
    return event


def build_human_output(run_id: str, parent_event_id: str, offset_seconds: float) -> dict:
    event = build_base_event(run_id, parent_event_id, offset_seconds)
    output_type = random.choice(OUTPUT_TYPES)
    event["output"] = {
        "output_type": output_type,
        "output_uri": f"s3://medlog-outputs/{new_uuid()}",
        "summary": "Model output shown to the requesting user.",
    }
    return event


def build_outcome(run_id: str, parent_event_id: str, offset_seconds: float) -> dict:
    event = build_base_event(run_id, parent_event_id, offset_seconds)
    outcome_type = random.choice(OUTCOME_TYPES)
    event["outcome"] = {
        "outcome_type": outcome_type,
        "occurred_at": now_iso(offset_seconds),
        "description": f"Downstream outcome recorded as {outcome_type}.",
        "evidence_uri": f"s3://medlog-evidence/{new_uuid()}.json",
    }
    return event


def build_user_feedback(run_id: str, parent_event_id: str, offset_seconds: float) -> dict:
    event = build_base_event(run_id, parent_event_id, offset_seconds)
    feedback_type = random.choice(FEEDBACK_TYPES)
    feedback = {"feedback_type": feedback_type}
    if feedback_type == "rating":
        feedback["rating_value"] = round(random.uniform(0, 5), 1)
    elif feedback_type == "thumbs":
        feedback["value"] = random.choice(["up", "down"])
    else:
        feedback["text"] = "Feedback provided by the end user."
    event["feedback"] = feedback
    return event


EVENT_PATHS = {
    "inference-start": "/event/inference-start",
    "internal-artifact": "/event/internal-artifact",
    "human-output": "/event/human-output",
    "outcome": "/event/outcome",
    "user-feedback": "/event/user-feedback",
}


def post_event(
    base_url: str,
    event_name: str,
    payload: dict,
    timeout: float,
    dry_run: bool,
    api_key: str | None,
) -> None:
    path = EVENT_PATHS[event_name]
    if dry_run:
        print(f"[dry-run] POST {path}\n{json.dumps(payload, indent=2)}\n")
        return

    url = base_url.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            print(f"POST {path} -> {response.status}")
    except urllib.error.HTTPError as exc:
        print(f"POST {path} -> HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"POST {path} -> failed: {exc.reason}", file=sys.stderr)


def simulate_run(
    base_url: str, timeout: float, interval: float, dry_run: bool, api_key: str | None
) -> None:
    run_id = new_uuid()

    start_event = build_inference_start(run_id)
    post_event(base_url, "inference-start", start_event, timeout, dry_run, api_key)
    time.sleep(interval)

    last_event_id = start_event["event_id"]
    for artifact_index in range(random.randint(0, 2)):
        artifact_event = build_internal_artifact(
            run_id, last_event_id, offset_seconds=0.1 * (artifact_index + 1)
        )
        post_event(base_url, "internal-artifact", artifact_event, timeout, dry_run, api_key)
        last_event_id = artifact_event["event_id"]
        time.sleep(interval)

    output_event = build_human_output(run_id, last_event_id, offset_seconds=0.5)
    post_event(base_url, "human-output", output_event, timeout, dry_run, api_key)
    time.sleep(interval)

    if random.random() < 0.5:
        outcome_event = build_outcome(run_id, start_event["event_id"], offset_seconds=random.uniform(60, 3600))
        post_event(base_url, "outcome", outcome_event, timeout, dry_run, api_key)
        time.sleep(interval)

    if random.random() < 0.4:
        feedback_event = build_user_feedback(run_id, output_event["event_id"], offset_seconds=random.uniform(1, 30))
        post_event(base_url, "user-feedback", feedback_event, timeout, dry_run, api_key)
        time.sleep(interval)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--url",
        default=os.getenv("MEDLOG_API_URL", "http://localhost:5000"),
        help="Base URL of the MedLog HTTP server (default: MEDLOG_API_URL or http://localhost:5000)",
    )
    parser.add_argument("--runs", type=int, default=10, help="Number of inference runs to simulate (default: 10)")
    parser.add_argument("--interval", type=float, default=0.0, help="Seconds to sleep between individual event posts")
    parser.add_argument("--run-interval", type=float, default=0.0, help="Seconds to sleep between simulated runs")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP request timeout in seconds")
    parser.add_argument(
        "--api-key",
        default=os.getenv("MEDLOG_API_KEY"),
        help="X-API-Key value (default: MEDLOG_API_KEY environment variable)",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible event generation")
    parser.add_argument("--dry-run", action="store_true", help="Print events instead of sending them over HTTP")
    parser.add_argument(
        "--emit",
        choices=["outcome", "user-feedback"],
        help="Emit a single event against an existing run instead of simulating full runs, "
        "to test late-arriving fragments merging into medlog_run_summary",
    )
    parser.add_argument("--run-id", help="Existing run_id to attach the --emit event to")
    parser.add_argument("--parent-event-id", help="event_id this --emit event expands on (usually the inference-start event_id)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.seed is not None:
        random.seed(args.seed)

    if args.emit:
        if not args.run_id or not args.parent_event_id:
            print("--emit requires --run-id and --parent-event-id", file=sys.stderr)
            return 2
        builder = build_outcome if args.emit == "outcome" else build_user_feedback
        event = builder(args.run_id, args.parent_event_id, offset_seconds=0.0)
        if not args.dry_run and not args.api_key:
            print("--api-key or MEDLOG_API_KEY is required when sending events", file=sys.stderr)
            return 2
        post_event(args.url, args.emit, event, args.timeout, args.dry_run, args.api_key)
        return 0

    if not args.dry_run and not args.api_key:
        print("--api-key or MEDLOG_API_KEY is required when sending events", file=sys.stderr)
        return 2

    for run_index in range(args.runs):
        simulate_run(args.url, args.timeout, args.interval, args.dry_run, args.api_key)
        if run_index < args.runs - 1:
            time.sleep(args.run_interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

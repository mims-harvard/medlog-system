#!/usr/bin/env python3
"""Send one Medlog smoke-test run to the configured MedLog receiver."""

import argparse
import os

from medlog_tool import Medlog


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.getenv("MEDLOG_API_URL", "http://localhost:5000"),
        help="MedLog receiver base URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("MEDLOG_API_KEY"),
        help="X-API-Key value (default: MEDLOG_API_KEY)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.api_key:
        raise SystemExit("--api-key or MEDLOG_API_KEY is required")

    medlog = Medlog(
        system_info={
            "hostname": "readme-smoke-test",
            "app_name": "medlog-tool",
            "proc_id": "readme",
            "latency_ms": 1,
        },
        api_url=args.url,
        api_key=args.api_key,
    )
    medlog.start_run()
    medlog.set_target({"target_type": "patient", "target_id": "readme-patient"})
    medlog(
        model_instance={"model_id": "medlog-tool-readme-test", "model_version": "test"},
        inputs={
            "prompt": "README Medlog smoke test",
            "input_uri": "https://example.test/readme-input",
        },
        internal_artifacts={
            "artifact_type": "text_chunk",
            "artifact_uri": "https://example.test/readme-artifact",
            "description": "README Medlog smoke test",
        },
        outputs={
            "output_type": "text",
            "output_uri": "https://example.test/readme-output",
            "summary": "README Medlog smoke test",
        },
    )
    print(medlog.logs[0]["header"]["run_id"])


if __name__ == "__main__":
    main()

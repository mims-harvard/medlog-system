"""Regression coverage for the receiver's external schema directory."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import schema_paths


RECEIVER = Path(__file__).resolve().parent


class SchemaPathTests(unittest.TestCase):
    def test_receiver_loads_contracts_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(RECEIVER)
            environment["EVENT_ID_DB_PATH"] = str(Path(directory) / "events.db")
            result = subprocess.run(
                [sys.executable, "-c", "\n".join([
                    "import app, semantics",
                    "from schema_paths import SCHEMA_ROOT",
                    "assert app.SPEC_PATH == SCHEMA_ROOT / 'openapi.yaml'",
                    "assert len(semantics._STRUCTURED_SCHEMAS) == 4",
                    "assert len(app._spec_dict['paths']) == 5",
                    "app.app.state.db_conn.close()",
                ])],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_contract_identifies_the_expected_file_and_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "spec"
            with patch.object(schema_paths, "SCHEMA_ROOT", root):
                for relative_path in (
                    "openapi.yaml",
                    "semantic_schemas/gen-ai-input-messages.json",
                    "semantic_schemas/gen-ai-output-messages.json",
                    "semantic_schemas/gen-ai-system-instructions.json",
                    "semantic_schemas/gen-ai-retrieval-documents.json",
                ):
                    with self.subTest(relative_path=relative_path):
                        with self.assertRaises(FileNotFoundError) as error:
                            schema_paths.schema_file(relative_path)
                        self.assertIn(str(root / relative_path), str(error.exception))
                        self.assertIn("spec/ directory alongside event-receiver/",
                                      str(error.exception))


if __name__ == "__main__":
    unittest.main()

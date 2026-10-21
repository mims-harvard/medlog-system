"""Paths to the language-neutral contracts adjacent to the receiver directory."""

from pathlib import Path


SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "spec"


def schema_file(relative_path: str) -> Path:
    """Locate a required contract without depending on the working directory."""
    path = SCHEMA_ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(
            f"Required MedLog schema file is missing: {path}. "
            "The receiver requires the spec/ directory alongside event-receiver/. "
            "From the repository root, run: git submodule update --init --recursive spec"
        )
    return path

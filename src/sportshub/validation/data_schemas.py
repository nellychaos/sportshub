"""JSON schema validation for data files.

Validates data/*.json files against their corresponding schemas
in data/schemas/*.schema.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
if not _DATA_DIR.is_dir():
    _DATA_DIR = Path("/app/data")
_SCHEMA_DIR = _DATA_DIR / "schemas"


def _get_schema_path(data_filename: str) -> Path | None:
    """Find the schema file for a given data file."""
    stem = Path(data_filename).stem
    schema_path = _SCHEMA_DIR / f"{stem}.schema.json"
    return schema_path if schema_path.exists() else None


def validate_data_file(filename: str) -> list[str]:
    """Validate a data file against its JSON schema.

    Args:
        filename: Filename relative to data/ (e.g., "nba_teams.json")

    Returns:
        List of validation error messages. Empty list means valid.
    """
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema package not installed (pip install jsonschema)"]

    data_path = _DATA_DIR / filename
    if not data_path.exists():
        return [f"Data file not found: {filename}"]

    schema_path = _get_schema_path(filename)
    if not schema_path:
        return []  # No schema defined -- skip validation

    with open(schema_path) as f:
        schema = json.load(f)
    with open(data_path) as f:
        data = json.load(f)

    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{path}: {error.message}")

    return errors


def validate_all_data_files() -> dict[str, list[str]]:
    """Validate all data files that have schemas.

    Returns:
        Dict mapping filename -> list of errors. Files with no errors
        are included with an empty list.
    """
    results: dict[str, list[str]] = {}

    for schema_file in sorted(_SCHEMA_DIR.glob("*.schema.json")):
        data_name = schema_file.stem.replace(".schema", "") + ".json"
        data_path = _DATA_DIR / data_name
        if data_path.exists():
            results[data_name] = validate_data_file(data_name)

    return results

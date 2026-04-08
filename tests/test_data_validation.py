"""Parametrized tests validating data files against their JSON schemas.

Run: pytest tests/test_data_validation.py -v
"""

import json
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SCHEMA_DIR = DATA_DIR / "schemas"


def _discover_schema_pairs():
    """Find all data files that have corresponding schemas."""
    if not SCHEMA_DIR.exists():
        return []
    pairs = []
    for schema_file in sorted(SCHEMA_DIR.glob("*.schema.json")):
        data_name = schema_file.stem.replace(".schema", "") + ".json"
        data_path = DATA_DIR / data_name
        if data_path.exists():
            pairs.append((data_name, data_path, schema_file))
    return pairs


SCHEMA_PAIRS = _discover_schema_pairs()


@pytest.mark.parametrize(
    "data_name,data_path,schema_path",
    SCHEMA_PAIRS,
    ids=[p[0] for p in SCHEMA_PAIRS],
)
def test_data_file_matches_schema(data_name, data_path, schema_path):
    """Validate each data file against its JSON schema."""
    jsonschema = pytest.importorskip("jsonschema")

    with open(schema_path) as f:
        schema = json.load(f)
    with open(data_path) as f:
        data = json.load(f)

    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(data))

    if errors:
        messages = []
        for error in sorted(errors, key=lambda e: list(e.path))[:10]:
            path = ".".join(str(p) for p in error.absolute_path) or "(root)"
            messages.append(f"  {path}: {error.message}")
        error_summary = "\n".join(messages)
        pytest.fail(
            f"{data_name} has {len(errors)} validation error(s):\n{error_summary}"
        )


def test_all_schemas_are_valid_json_schema():
    """Ensure all schema files are valid JSON Schema documents."""
    jsonschema = pytest.importorskip("jsonschema")

    for schema_file in sorted(SCHEMA_DIR.glob("*.schema.json")):
        with open(schema_file) as f:
            schema = json.load(f)

        # Should not raise
        jsonschema.Draft202012Validator.check_schema(schema)

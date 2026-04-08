"""Shared I/O utilities for data scripts.

Provides consistent JSON loading/saving with path resolution to the
project's data/ directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Resolve the project's data directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = _PROJECT_ROOT / "data"


def data_path(filename: str) -> Path:
    """Resolve a filename relative to the data/ directory.

    Args:
        filename: Filename or relative path (e.g., "nba_teams.json")

    Returns:
        Absolute Path to the file in data/.
    """
    return DATA_DIR / filename


def load_data(filename: str) -> Any:
    """Load a JSON file from the data/ directory.

    Args:
        filename: Filename relative to data/ (e.g., "nba_teams.json")

    Returns:
        Parsed JSON content.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        json.JSONDecodeError: If the file isn't valid JSON.
    """
    path = data_path(filename)
    with open(path) as f:
        return json.load(f)


def save_data(
    filename: str,
    data: Any,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> Path:
    """Save data as JSON to the data/ directory.

    Args:
        filename: Filename relative to data/ (e.g., "nba_players.json")
        data: JSON-serializable data.
        indent: JSON indentation (default 2).
        ensure_ascii: Whether to escape non-ASCII characters (default False).

    Returns:
        Path to the written file.
    """
    path = data_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
        f.write("\n")  # trailing newline
    return path

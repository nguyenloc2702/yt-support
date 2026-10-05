import json
from pathlib import Path
from typing import Any, Dict


def atomic_write_json(path: Path, data: Any) -> None:
    """
    Write data as JSON to a temporary file then atomically replace the destination path.
    Guarantees that readers never observe partially written JSON.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)

    temp_path.replace(path)


def read_json(path: Path) -> Dict[str, Any]:
    """
    Read and decode JSON from the given file path.
    Raises FileNotFoundError or json.JSONDecodeError on issues.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

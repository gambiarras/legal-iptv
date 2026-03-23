import json
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_json_atomic(path: Path, payload: list | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=path.parent) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        temp_path = Path(tmp.name)

    temp_path.replace(path)
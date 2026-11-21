import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile


@contextmanager
def _atomic_writer(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile("w", prefix=".write-", suffix=".tmp", delete=False,
                                encoding="utf-8", dir=path.parent) as stream:
            temporary = Path(stream.name)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: list | dict) -> None:
    with _atomic_writer(path) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def write_text_atomic(path: Path, value: str) -> None:
    with _atomic_writer(path) as stream:
        stream.write(value)

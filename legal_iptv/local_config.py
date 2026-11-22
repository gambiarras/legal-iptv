"""Load operator-owned inputs without bundling source inventories in the package."""

import copy
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    root: Path
    runtime: Mapping[str, Any]
    resource_values: Mapping[str, Any]
    private_resources: frozenset[str]
    extra_files: Mapping[str, Any]
    environment: Mapping[str, str]


_SNAPSHOT: ContextVar[ConfigurationSnapshot | None] = ContextVar(
    "local_configuration", default=None
)


@contextmanager
def configuration_snapshot(
    extra_files: tuple[Path, ...] = (),
) -> Iterator[ConfigurationSnapshot]:
    """Read one isolated in-memory configuration for an entire aggregation round."""
    active = _SNAPSHOT.get()
    if active is not None:
        yield active
        return
    root = local_config_dir()
    runtime_path = root / "runtime.json"
    runtime = (
        json.loads(runtime_path.read_text(encoding="utf-8"))
        if runtime_path.is_file()
        else {}
    )
    if not isinstance(runtime, dict):
        raise ValueError("Private runtime configuration must be an object")
    packaged = Path(str(resources.files("legal_iptv.resources")))
    names = {path.name for path in packaged.glob("*.json")} | {
        path.name for path in (root / "resources").glob("*.json")
    }
    private = frozenset(name for name in names if (root / "resources" / name).is_file())
    values = {
        name: json.loads(
            (
                (root / "resources" / name) if name in private else packaged / name
            ).read_text(encoding="utf-8")
        )
        for name in names
    }
    extras = {
        str(path.resolve()): json.loads(path.read_text(encoding="utf-8"))
        for path in extra_files
    }
    snapshot = ConfigurationSnapshot(
        root,
        MappingProxyType(runtime),
        MappingProxyType(values),
        private,
        MappingProxyType(extras),
        MappingProxyType(dict(os.environ)),
    )
    token = _SNAPSHOT.set(snapshot)
    try:
        yield snapshot
    finally:
        _SNAPSHOT.reset(token)


def environment_value(name: str, default: Any = None) -> Any:
    active = _SNAPSHOT.get()
    return (active.environment if active is not None else os.environ).get(name, default)


def load_json_file(path: Path) -> Any:
    active = _SNAPSHOT.get()
    key = str(path.resolve())
    if active is not None and key in active.extra_files:
        return copy.deepcopy(active.extra_files[key])
    return json.loads(path.read_text(encoding="utf-8"))


def local_config_dir() -> Path:
    """Return the effective private configuration directory for this component."""
    active = _SNAPSHOT.get()
    if active is not None:
        return active.root
    configured = os.environ.get("IPTV_LOCAL_CONFIG_DIR")
    return (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parents[1] / ".local"
    )


def runtime_value(key: str, default: Any = None) -> Any:
    """Read a private runtime setting, retaining inert defaults for library use."""
    active = _SNAPSHOT.get()
    if active is not None:
        return copy.deepcopy(active.runtime.get(key, default))
    path = local_config_dir() / "runtime.json"
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Private runtime configuration must be an object")
    return payload.get(key, default)


def snapshot_value(key: str, fallback: Any) -> Any:
    """Use the active round configuration, retaining legacy library overrides."""
    return runtime_value(key, fallback) if _SNAPSHOT.get() is not None else fallback


def resource_path(name: str) -> Path:
    """Return a private resource override or the packaged empty schema example."""
    if Path(name).name != name:
        raise ValueError("Resource name must be a filename")
    private = local_config_dir() / "resources" / name
    if private.exists():
        return private
    return Path(str(resources.files("legal_iptv.resources").joinpath(name)))


def load_resource(name: str) -> Any:
    active = _SNAPSHOT.get()
    if active is not None and name in active.resource_values:
        return copy.deepcopy(active.resource_values[name])
    return json.loads(resource_path(name).read_text(encoding="utf-8"))


def require_local_configuration() -> None:
    """Fail before collection/publication when operational inputs were not installed."""
    if runtime_value("configured") is not True:
        raise RuntimeError(
            "Private configuration missing: set IPTV_LOCAL_CONFIG_DIR and install runtime.json"
        )
    required = (
        "extra_channels.json",
        "playlist_profiles.json",
        "companion_policy.json",
        "epg_aliases.json",
        "epg_providers.json",
        "epg_site_mappings.json",
    )
    active = _SNAPSHOT.get()
    missing = [
        name
        for name in required
        if (
            name not in active.private_resources
            if active is not None
            else not (local_config_dir() / "resources" / name).is_file()
        )
    ]
    if missing:
        raise RuntimeError(
            "Private resource configuration missing: " + ", ".join(missing)
        )
    expected_types = {
        "extra_channels.json": list,
        "playlist_profiles.json": dict,
        "companion_policy.json": dict,
        "epg_aliases.json": list,
        "epg_providers.json": list,
        "epg_site_mappings.json": dict,
    }
    for name, expected_type in expected_types.items():
        try:
            payload = load_resource(name)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "Invalid private resource configuration: " + name
            ) from exc
        if not isinstance(payload, expected_type):
            raise RuntimeError("Invalid private resource schema: " + name)
    required_settings = (
        "catalog_url",
        "public_catalog_base_url",
        "reference_base_url",
        "reference_disabled_url",
    )
    if any(
        not isinstance(runtime_value(key), str) or not runtime_value(key).strip()
        for key in required_settings
    ):
        raise RuntimeError("Private source endpoints are missing from runtime.json")

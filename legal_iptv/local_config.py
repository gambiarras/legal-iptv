"""Load operator-owned inputs without bundling source inventories in the package."""

import json
import os
from importlib import resources
from pathlib import Path
from typing import Any


def local_config_dir() -> Path:
    """Return the effective private configuration directory for this component."""
    configured = os.environ.get("IPTV_LOCAL_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path(__file__).resolve().parents[1] / ".local"


def runtime_value(key: str, default: Any = None) -> Any:
    """Read a private runtime setting, retaining inert defaults for library use."""
    path = local_config_dir() / "runtime.json"
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Private runtime configuration must be an object")
    return payload.get(key, default)


def resource_path(name: str) -> Path:
    """Return a private resource override or the packaged empty schema example."""
    if Path(name).name != name:
        raise ValueError("Resource name must be a filename")
    private = local_config_dir() / "resources" / name
    if private.exists():
        return private
    return Path(str(resources.files("legal_iptv.resources").joinpath(name)))


def load_resource(name: str) -> Any:
    return json.loads(resource_path(name).read_text(encoding="utf-8"))


def require_local_configuration() -> None:
    """Fail before collection/publication when operational inputs were not installed."""
    if runtime_value("configured") is not True:
        raise RuntimeError("Private configuration missing: set IPTV_LOCAL_CONFIG_DIR and install runtime.json")
    required = ("extra_channels.json", "playlist_profiles.json", "companion_policy.json", "epg_aliases.json", "epg_providers.json", "epg_site_mappings.json")
    missing = [name for name in required if not (local_config_dir() / "resources" / name).is_file()]
    if missing:
        raise RuntimeError("Private resource configuration missing: " + ", ".join(missing))
    expected_types = {"extra_channels.json": list, "playlist_profiles.json": dict, "companion_policy.json": dict, "epg_aliases.json": list, "epg_providers.json": list, "epg_site_mappings.json": dict}
    for name, expected_type in expected_types.items():
        try:
            payload = load_resource(name)
        except (OSError, ValueError) as exc:
            raise RuntimeError("Invalid private resource configuration: " + name) from exc
        if not isinstance(payload, expected_type):
            raise RuntimeError("Invalid private resource schema: " + name)
    required_settings = ("catalog_url", "public_catalog_base_url", "reference_base_url", "reference_disabled_url")
    if any(not isinstance(runtime_value(key), str) or not runtime_value(key).strip() for key in required_settings):
        raise RuntimeError("Private source endpoints are missing from runtime.json")

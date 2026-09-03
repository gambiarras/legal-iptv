import json
import os
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path


RESOURCE_NAME = "playlist_profiles.json"


@dataclass(slots=True, frozen=True)
class PlaylistProfile:
    name: str
    selection_mode: str
    inherits: str | None = None
    include_sources: tuple[str, ...] = ()
    include_live_source_types: tuple[str, ...] = ()
    exclude_explicit_adult: bool = False
    require_publishable_static: bool = False
    include_ids: frozenset[str] = frozenset()
    exclude_ids: frozenset[str] = frozenset()
    include_groups: frozenset[str] = frozenset()
    exclude_groups: frozenset[str] = frozenset()
    include_variants: frozenset[str] = frozenset()
    exclude_variants: frozenset[str] = frozenset()

    @classmethod
    def from_dict(cls, name: str, value: dict) -> "PlaylistProfile":
        return cls(
            name=name,
            selection_mode=str(value.get("selection_mode", "legacy")),
            inherits=value.get("inherits"),
            include_sources=tuple(str(item) for item in value.get("include_sources", ())),
            include_live_source_types=tuple(
                str(item) for item in value.get("include_live_source_types", ())
            ),
            exclude_explicit_adult=bool(value.get("exclude_explicit_adult", False)),
            require_publishable_static=bool(value.get("require_publishable_static", False)),
            include_ids=frozenset(str(item) for item in value.get("include_ids", ())),
            exclude_ids=frozenset(str(item) for item in value.get("exclude_ids", ())),
            include_groups=frozenset(str(item) for item in value.get("include_groups", ())),
            exclude_groups=frozenset(str(item) for item in value.get("exclude_groups", ())),
            include_variants=frozenset(str(item) for item in value.get("include_variants", ())),
            exclude_variants=frozenset(str(item) for item in value.get("exclude_variants", ())),
        )


@dataclass(slots=True, frozen=True)
class PlaylistConfiguration:
    default_profile: str
    guide_url: str | None
    profiles: dict[str, PlaylistProfile]
    provider_priorities: dict[str, int]
    category_map: dict[str, str]
    group_order: tuple[str, ...]
    alternatives_group: str
    variant_quality_order: tuple[str, ...]
    overrides: dict[str, dict] = field(default_factory=dict)

    def profile(self, name: str) -> PlaylistProfile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise ValueError(f"Unknown playlist profile: {name}") from exc


def load_playlist_configuration(path: Path | None = None) -> PlaylistConfiguration:
    if path is None:
        resource = resources.files("legal_iptv.resources").joinpath(RESOURCE_NAME)
        raw = json.loads(resource.read_text(encoding="utf-8"))
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))

    profiles = {
        name: PlaylistProfile.from_dict(name, value)
        for name, value in raw.get("profiles", {}).items()
        if isinstance(value, dict)
    }
    guide_url = raw.get("guide_url")
    guide_url_env = raw.get("guide_url_env")
    if guide_url_env:
        guide_url = os.environ.get(str(guide_url_env), guide_url)

    return PlaylistConfiguration(
        default_profile=str(raw.get("default_profile", "full")),
        guide_url=str(guide_url) if guide_url else None,
        profiles=profiles,
        provider_priorities={
            str(key): int(value)
            for key, value in raw.get("provider_priorities", {}).items()
        },
        category_map={
            str(key).casefold(): str(value)
            for key, value in raw.get("category_map", {}).items()
        },
        group_order=tuple(str(item) for item in raw.get("group_order", ())),
        alternatives_group=str(raw.get("alternatives_group", "Alternativos")),
        variant_quality_order=tuple(
            str(item).upper()
            for item in raw.get("variant_quality_order", ("4K", "FHD", "HD", "SD"))
        ),
        overrides={
            str(key): value
            for key, value in raw.get("overrides", {}).items()
            if isinstance(value, dict)
        },
    )

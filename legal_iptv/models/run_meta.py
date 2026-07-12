from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class RunMetadata:
    generated_at: str
    total_input_channels: int
    selected_channels: int
    extra_channels: int
    iptv_org_channels: int
    live_stream_catalog_channels: int
    source_errors: dict[str, str] = field(default_factory=dict)
    selected_before_stream_filter: int = 0
    stream_filtered_channels: int = 0
    deduplicated_channels: int = 0
    selected_by_source: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

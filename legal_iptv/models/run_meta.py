from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class RunMetadata:
    generated_at: str
    total_input_channels: int
    selected_channels: int
    extra_channels: int
    public_catalog_channels: int
    live_stream_catalog_channels: int
    contract_version: int = 1
    catalog_input_sha256: str | None = None
    decision_counts: dict[str, int] = field(default_factory=dict)
    selection_decisions: list[dict[str, str]] = field(default_factory=list)
    decisions_truncated: int = 0
    ingestion_counts: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)
    selected_before_stream_filter: int = 0
    selected_after_stream_filter: int = 0
    stream_filtered_channels: int = 0
    selection_filtered_channels: int = 0
    deduplicated_channels: int = 0
    selected_by_source: dict[str, int] = field(default_factory=dict)
    selected_by_provider: dict[str, int] = field(default_factory=dict)
    timings_seconds: dict[str, float] = field(default_factory=dict)
    epg_aliases_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

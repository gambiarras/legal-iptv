from dataclasses import asdict, dataclass


@dataclass(slots=True)
class RunMetadata:
    generated_at: str
    total_input_channels: int
    selected_channels: int
    extra_channels: int
    iptv_org_channels: int
    live_stream_catalog_channels: int

    def to_dict(self) -> dict:
        return asdict(self)
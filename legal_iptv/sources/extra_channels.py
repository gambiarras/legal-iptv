import json
from importlib import resources

from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import localized_category_name


def fetch_channels() -> list[Channel]:
    with resources.files("legal_iptv.resources").joinpath("extra_channels.json").open("r", encoding="utf-8") as file:
        raw = json.load(file)

    return [
        Channel(
            id=item["id"],
            name=item["name"],
            stream_url=item["url"],
            logo=item.get("logo", ""),
            group=localized_category_name(item.get("group", "general")),
            source="extra",
        )
        for item in raw
    ]
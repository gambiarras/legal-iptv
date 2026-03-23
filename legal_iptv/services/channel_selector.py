from collections import defaultdict

from legal_iptv.models import Channel


SOURCE_PRIORITY = {
    "extra": 300,
    "live_stream_catalog": 200,
    "iptv_org": 100,
}


def _score(channel: Channel) -> tuple:
    ttl = channel.ttl_seconds

    if ttl is None:
        ttl_state = 1
        ttl_value = 0
    elif ttl > 0:
        ttl_state = 2
        ttl_value = ttl
    else:
        ttl_state = 0
        ttl_value = 0

    return (
        SOURCE_PRIORITY.get(channel.source, 0),
        1 if channel.status == "resolved" else 0,
        ttl_state,
        ttl_value,
        1 if channel.logo else 0,
    )


def select_best_channels(channels: list[Channel]) -> list[Channel]:
    grouped: dict[str, list[Channel]] = defaultdict(list)

    for channel in channels:
        grouped[channel.id].append(channel)

    selected: list[Channel] = []
    for items in grouped.values():
        best = max(items, key=_score)
        selected.append(best)

    return selected
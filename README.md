# legal-iptv

Aggregates public IPTV and live streaming sources into a static M3U playlist.

This project combines channels from multiple sources such as:

- IPTV-ORG
- manually curated channels from `extra_channels.json`
- `live-stream-catalog` generated channels

The output is a static `playlist.m3u` file that can be published through GitHub without requiring paid hosting.

---

## ⚠️ Disclaimer / Legal Notice

This repository **does not host, stream, retransmit, or redistribute audiovisual content**.

It only:

- collects metadata and stream URLs from publicly accessible sources
- aggregates these sources into a single playlist
- makes access to these publicly available stream URLs easier through a machine-readable M3U output

All content:

- is served directly by the original platforms, providers, broadcasters, or CDNs
- remains under the responsibility of the respective content owners, broadcasters, and platforms
- may be subject to availability changes, geoblocking, licensing restrictions, platform policies, or removal at any time

This project:

- does not bypass paywalls, authentication systems, DRM, or access controls
- does not modify, restream, mirror, proxy, or rehost media content
- does not guarantee legality, licensing, availability, uptime, or long-term validity of any listed stream

The generated playlist is provided **for informational and convenience purposes only**.

Users are solely responsible for ensuring compliance with:

- local laws and regulations
- copyright and neighboring rights rules
- platform terms of service
- any contractual or licensing restrictions applicable in their jurisdiction

If any channel, stream, or source should not be listed, the appropriate action is to remove it from the source configuration or upstream catalog.

---

## Goals

- generate a public `playlist.m3u` without paid hosting
- aggregate legal and publicly accessible streaming sources
- consume the `live-stream-catalog` output safely
- keep the project maintainable and extensible
- prepare the codebase for future source integrations

---

## How it works

The project fetches and merges channels from different sources:

### 1. IPTV-ORG
Loads channel metadata, stream URLs and logos from the IPTV-ORG public datasets.

### 2. Extra channels
Loads manually curated channels from `extra_channels.json`.

### 3. live-stream-catalog
Loads dynamically resolved channels from the `live-stream-catalog` repository.

This source may include metadata such as:

- `stream_url`
- `status`
- `resolved_at`
- `expires_at`
- `ttl_seconds`

The aggregation pipeline filters and selects channels, then renders the final M3U playlist.

---

## Local usage

Use a local `live-stream-catalog` output when running both repositories on the same machine:

```bash
python3.11 -m legal_iptv \
  --live-catalog-file ../live-stream-catalog/channels.json \
  --output playlist.m3u \
  --meta-output playlist.meta.json
```

When `--live-catalog-file` is provided, the file must exist. This keeps local runs from silently falling back to a remote catalog.

`live-stream-catalog` entries have higher selection priority than manually curated extra channels and IPTV-ORG channels. If duplicate candidates point to the same URL and have the same or a very similar name, only the best candidate is kept. If their URLs differ, both are kept and duplicate channel IDs are made unique.

Optionally validate stream URLs before rendering the playlist:

```bash
python3.11 -m legal_iptv \
  --live-catalog-file ../live-stream-catalog/channels.json \
  --validate-streams \
  --validation-max-workers 32 \
  --validation-timeout 6
```

Validation is disabled by default because it performs network checks. When enabled,
it checks due transport contexts and writes `stream-status.json`; ordinary generation
only reads cached failures and does not initiate probes.

For scheduled environments, run validation periodically, for example every 4 hours, outside this repository. Normal playlist generation reads `stream-status.json` and skips URLs recently marked offline:

```bash
python3.11 -m legal_iptv \
  --live-catalog-file ../live-stream-catalog/channels.json \
  --stream-status-file stream-status.json \
  --stream-status-max-age 14400
```

Only offline statuses newer than `--stream-status-max-age` are applied, so stale failures do not block channels forever.

### Adaptive validation

Scheduling is based on observed results for the exact URL, declared request
headers and transport context, rather than a provider's name. With default
settings, consecutive HTTP successes schedule checks after **4, 8, 12 and
24 hours**, capped at 24 hours. A cache hit preserves the original `checked_at`,
deadline and streak; it is not another success. Unknown/offline results reset
the success streak and schedule retries after **15, 30, 60, 120 and 240 minutes**,
capped at the base interval. With a validation job every four hours, retries
still wait for that job; this change does not modify the external scheduler.

| Option | Default | Purpose |
| --- | --- | --- |
| `--stream-status-max-age` | `14400` | Base success interval, maximum retry interval and freshness of cached suppression |
| `--validation-max-success-age` | `86400` | Maximum success interval; use `14400` to retain a four-hour ceiling |
| `--validation-retry-delay` | `900` | Initial unsuccessful retry interval |
| `--force-validation` | off | Ignore scheduling for eligible transports; requires `--validate-streams` |

Larger success intervals reduce HTTP work while increasing potential failure
detection delay. New or changed contexts and legacy records without a context
fingerprint are checked before reuse. Malformed/future state cannot suppress a
channel through the scheduling cache. A late job rechecks due state but can
retain its streak for less than twice the larger of the base and success cap
(48 hours with defaults), without extending the cache validity.

Known expiration bounds the deadline. `expires_at` is authoritative because
ingestion recalculates the remaining TTL on each load; `resolved_at + ttl_seconds`
is a fallback only when no absolute expiration was provided. Expired or invalid
explicit validity declarations are not probed or selected, including with force;
the producing catalog must renew those transports. YouTube/Twitch/Kick live
transports keep the base success ceiling rather than advancing to 24 hours.

Declared transport headers are used by both HEAD and fallback GET. Fingerprints,
not header values, are written to the status state. Redirects to another origin
drop custom headers so credentials do not follow the stream to a different host.
This remains an HTTP reachability check, not a manifest or player compatibility
test.

Two contexts sharing a URL are checked and filtered independently. A single
context keeps the legacy flat `urls[url]` record. Multiple contexts are stored
under `urls[url].contexts[context_id]`; their legacy URL summary is `unknown`,
preventing old URL-only readers from suppressing every context. Context-aware
generation applies each context's actual result. Status remains private.

### Validation history and metrics

`stream-status.json` now uses an additive `schema_version: 2`. Existing `urls`,
`active`, `checked_at` and summary counts remain available to older consumers.
Temporary suppression also expires according to `--stream-status-max-age`;
missing, invalid or future observation timestamps do not suppress channels.

The snapshot provides:

| Field | Meaning |
| --- | --- |
| `summary.validation_metrics` | This run's actual checks, cache hits, HTTP status/error counts, measured duration and HEAD/GET call attempts |
| `summary.top_failing_channels` | Up to 20 channel/provider/feed identities, ordered by unsuccessful observation count, with sample counts and rates |
| `summary.provider_metrics` | Observation totals and rates attributed to each provider |
| `summary.scheduling` | Total, validated, cached and expired transport context counts |
| `history.transports` | History for each exact transport context, identified by SHA-256; prior URL-only series remain until retention expires |
| `history.channels` | History keyed by provider, logical channel and feed, independent of URL/variant rotation when that identity is available |
| `history.providers` | History attributed to providers, without assuming a commercial identity from adapter type |

Only real checks increment history. Reusing a cached result preserves its
original timestamp and does not count as another success. HTTP 404/410 results
are recorded separately from inconclusive failures such as timeout, 429 or 503.
Repeated inconclusive checks can trigger temporary suppression, while their
history remains `unknown_checks`, not confirmed `offline_checks`.

One transport context is one global observation; attribution to several channels or
providers can make their totals differ from the global total. Duplicate entries
for the same provider/logical channel/feed do not multiply that attribution.
The legacy `summary.validated_urls` and `summary.cached_urls` still count unique
URLs (a partially checked URL counts as validated). `summary.validation_metrics`
counts contexts and declares `cache_unit: transport_context`; its `cached_urls`
field is retained as a compatibility alias for cached contexts. Distinct headers
at one URL therefore produce separate observations and HTTP attempt counts.
Counters cover the lifetime of a retained series, starting with the first real
observation under the new format. They are sampled HTTP results, not continuous
uptime, manifest validation or confirmed playback on a device. HEAD/GET attempt
counts exclude redirects performed internally by Requests.
Channels that never reach validation, such as failed discovery/resolution
results, are not counted by this history.

Inactive series expire after 30 days. Each transport/channel/provider collection
retains at most 5,000 recently checked series, with at most 20 recent events per
series; recent events also expire after 30 days. Cumulative counters for a
continuously observed series can cover a longer period. Legacy snapshots do not
create fabricated historical observations. Invalid history is rebuilt without
discarding valid current URL state.

History and rankings omit transport URLs, headers and free-form exception
messages. The compatibility `urls` map still contains operational URLs: keep
the snapshot private and out of Git/public artifacts. No dashboard deployment
or change in verification frequency is required for this delivery.

`playlist.meta.json` also separates the input/output counts of stream filtering
from `selection_filtered_channels` (profile exclusions, provider/variant choice
and deduplication). The legacy `deduplicated_channels` field remains an alias
for selection removals; it is not a count of URL duplicates alone. These metrics
do not alter the channels selected for the playlist.

---

## Development

Local XMLTV channel aliases are indexed next to the guide in
`.cache/<guide-filename>.aliases.json`. Repeated runs verify the guide's SHA-256
and reuse the index without parsing XML again. Guide content changes, index
version changes, and invalid indexes trigger a rebuild. An unwritable cache
does not prevent playlist generation.

This local index is separate from `epg-cache.json`, which caches remote aliases.
It does not skip guide freshness or temporal coverage validation, and does not
change channel selection or remote fallback precedence. The index is disposable,
ignored by Git, and should not be published with the playlist.

Run the unit tests:

```bash
python3.11 -m unittest discover -s tests
```

---

## Project structure

```text
legal_iptv/
  models/       # domain models
  io/           # file persistence helpers
  clients/      # HTTP client abstraction
  sources/      # channel ingestion from each source
  services/     # aggregation, selection and metadata logic
  exporters/    # playlist rendering
  resources/    # static resources such as extra channels

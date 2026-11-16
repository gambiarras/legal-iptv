# legal-iptv

Aggregates public IPTV metadata into a main M3U playlist, a complementary M3U
and one compact XMLTV guide. Python 3.11+; the operations container owns scheduling
and publication.

## Documentation

- [Complete delivery report, consolidated 16 November 2026](docs/delivery-2026-11-15.md):
  implementation, decisions, source investigations, dated results, activation and recovery.
- [Principal and Companion](docs/companion.md): selection, reference cache and generation.
- [Transport sampling and recovery](docs/stream-validation.md): media evidence and private state.
- [EPG sources, collectors and cache limits](docs/epg-collectors.md).
- [Dated pilot evidence](docs/pilot-evidence/README.md).

## Principal and Companion

Both lists use the same exporter, channel IDs, groups, selected transports and
EPG header. Companion is the selected main list minus identities in the cached
visible Saimo catalogue. It does not independently rerank transports or require
membership in the Televisão.TV directory.

Provider order is **Televisão.TV → Batista Play → IPTV-ORG → YouTube → extras**
among eligible candidates with media evidence. Quality breaks ties within a
provider. FrostView and explicit option labels are excluded; known paid Batista
channels are filtered. Regional feeds remain distinct. Aliases merge reviewed
synonyms without treating TV Alternativa or Adult Swim as false positives.

A platform page or expiring/resolver-dependent URL is not a permanent IPTV
transport. The optional YouTube compatibility artifact has separate client
requirements; it does not change the regular Companion format.

## Output and private state

| Public output | Purpose |
| --- | --- |
| `playlist.m3u`, `playlist.meta.json` | Main winners and selection counts by source/provider |
| `companion.m3u`, `companion.meta.json` | Main winners absent from Saimo, reference and schedule coverage |
| `guide.xml` | Shared canonical XMLTV |
| `companion-guide.xml` | Byte-identical compatibility name, using a hardlink when available |
| `companion-youtube.kodi.m3u` | Optional public-video IDs for the Kodi YouTube resolver |

`stream-status.json`, `extra-removed.json`, source/reference/alias caches,
`guide-diagnostics.json` and raw `.epg-cache/` responses are private operational
state. Do not commit raw state or credentials. A public catalogue's `resolved`
flag is not a media validation result.

Ops administrative status/log downloads follow its configured authentication;
calling state private here means it is excluded from public Git artifacts,
not that an unauthenticated server configuration has been audited or prevented.

## Local usage

The following deliberately generates files and performs public-source requests;
it is not a read-only diagnostic. Use a dedicated output directory or the Ops
staging pipeline for production promotion.

```bash
python3.11 -m pip install -e .
python3.11 -m legal_iptv --profile full --validate-streams \
  --output playlist.m3u --meta-output playlist.meta.json \
  --guide-output guide.xml \
  --guide-url https://raw.githubusercontent.com/gambiarras/legal-iptv/main/guide.xml \
  --companion-output companion.m3u \
  --companion-meta-output companion.meta.json \
  --companion-guide-output companion-guide.xml
```

The CLI defaults to `full`, guide generation and recent media proof. Direct
`AppConfig` construction retains compatibility defaults; use `from_args()` or
set these fields deliberately. `--live-catalog-file` consumes an explicit local
catalogue without silently substituting a remote file when it is absent.

### Adaptive validation

`--validate-streams` checks due contexts; `--force-validation` requests fresh
checks. The CLI requires `probe_version=2` sampled-media evidence before provider
selection. Initial inconclusive candidates do not hide proven lower sources.
A first inconclusive retry may retain a real success within the configured
maximum age. Rejected contexts require a new real success, regardless of cache age.

`--validation-due-only --live-catalog-file channels.json` uses existing local
catalogue, source, reference and EPG caches. It forbids forced refreshes and
fails safely when required inputs are absent. Ops schedules this mode every
15 minutes. `--no-require-media-proof` exists for explicit offline fixtures;
it is not the production default.

HLS/TS/fMP4 probes have request/time/byte/concurrency limits and write no media
to disk. Unsupported formats remain inconclusive. HTTP success alone does not
qualify; a sampled transport does not prove continuous device playback.

### Validation history and metrics

Private status preserves context fingerprints, observation times, sampled
successes, rejection state, bounded history and aggregate provider metrics.
Cache reuse does not increment observations or refresh their timestamps.
`selected_by_source`, `selected_by_provider`, stream-filter and selection-filter
counts describe different stages; they are not continuous uptime statistics.
See [state and recovery details](docs/stream-validation.md).

## Shared EPG and storage

Our canonical guide is first in both M3U headers; external guides follow.
Only selected IDs and a three-day window are published. Empty declarations
keep IDs consistent without inventing programmes. Providers have interval
precedence, preserving valid gaps and affiliate identity. Fox Kids requires
present/future coverage when published.

Fox Kids, Open EPG, selected MeuGuia mappings and official dated schedules are
integrated. GuiaDeTV remains opt-in as documented. Nova TVI had no published
grade in the inspected snapshot; a working stream does not manufacture EPG.

XMLTV/gzip is parsed incrementally. Raw cache defaults: 256 MiB total, 96 files,
48 hours, 128 MiB per input/decoded XML and 200 MiB minimum free headroom.
Atomic writes clean temporary files and retain valid prior inputs after failure.
The final guide also omits long descriptions; reduction is not solely filtering.
See [collector/cache configuration](docs/epg-collectors.md).

## Operation and development

Use the coordinated [Ops 1.2.0 documentation](https://github.com/gambiarras/iptv-ops/blob/bc072df/README.md)
for staging, contract checks, promotion, rollback, schedules and dashboard.
Push does not rebuild a running container. Reference/source overrides remain
operator configuration; never print or commit their private values.

Local alias indices reuse guide content hashes and are disposable. The Saimo
reference has six-hour TTL and a maximum 48-hour last-known-good lifetime without
refreshing its original timestamp; failure is closed, not an unfiltered Companion.

```bash
python3.11 -m unittest discover -s tests
```

Historical test counts and pilot measurements are in the delivery report;
they do not establish live service availability.

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

# Companion and the main playlist

Both playlists use the same M3U exporter, category order, channel IDs, playback headers and provider selection. `companion.m3u` is the selected main list minus identities already present in the visible Saimo Android catalogue. It does not select a separate set of transports or require membership in another directory. Regional affiliates remain distinct; reviewed aliases merge different labels for the same feed.

## Selection

The first eligible available provider wins: **Televisão.TV → Batista Play → IPTV-ORG → YouTube → extra_channels**. Quality breaks ties within a provider. Health filtering runs before this choice so a failed preferred transport can fall back to a lower source. Lower providers do not create duplicate entries. Explicit option labels such as Alternativo 1/Backup are excluded; TV Alternativa is a legitimate broadcaster. Known paid channels from Batista and FrostView/addon catalogue sources are excluded before validation/publication.

Only exportable direct public media enters the regular output. Resolver-dependent, signed, private and incompatible transports do not become permanent URLs. Existing optional YouTube addon output remains a separate compatibility artifact; it does not change the structure or membership rules of the regular Companion.

## One guide, our URL first

`playlist.m3u` and `companion.m3u` have the same EPG header: the configured `--guide-url` (default public `guide.xml`) first, followed by deduplicated external fallbacks. `companion-guide.xml` remains a byte-identical compatibility name, created with an atomic hardlink when supported. It is no longer a second subset collection or a separate source of truth.

The public guide contains selected IDs and a three-day programme window. Official schedules and mapped collectors have interval precedence; other providers fill compatible gaps. Do not fabricate programmes for missing schedules. Fox Kids, when selected, requires present/future coverage. TV Sonata has an official dated partial schedule collector; Nova TVI currently publishes an empty weekly page and therefore has no invented guide.

The compact output keeps titles, subtitles, categories, episode identifiers, icons, dates and ratings; long descriptions are omitted. Size measurements therefore include field compaction as well as channel/time filtering. Upstream XML is parsed incrementally, not retained as one in-memory document. The optional compatibility guide is linked to the same output bytes.

Guide and source cache limits, diagnostics and collector configuration are documented in [EPG collectors](epg-collectors.md). Stream checks and recovery are documented in [stream validation](stream-validation.md). A successful sample is a dated transport observation, not a permanent playback guarantee.

## Generation

Enable the existing Companion output flags together with the main outputs:

```sh
python -m legal_iptv --profile full --validate-streams \
  --output playlist.m3u --meta-output playlist.meta.json \
  --guide-output guide.xml \
  --guide-url https://raw.githubusercontent.com/gambiarras/legal-iptv/main/guide.xml \
  --companion-output companion.m3u \
  --companion-meta-output companion.meta.json \
  --companion-guide-output companion-guide.xml
```

The Saimo reference stores names and hashes only, never its media credentials. Each generation subtracts those normalized/aliased identities from the main winners. Its six-hour cache may fall back to the original timestamp for at most 48 hours. Without a valid reference, generation fails closed instead of publishing an unfiltered Companion. Reference version 3 reads only Saimo catalogue/extras/disabled-host endpoints; it no longer depends on the Televisão.TV directory. Version 2 caches remain readable during migration. This reference controls membership, not playback.

`--validation-due-only --live-catalog-file channels.json` validates due transport contexts using local source/EPG/reference caches. It forbids forced refreshes and does not rediscover source catalogs or download guides. Missing required source caches fail without publishing partial output. It does not extend source timestamps, suppressed failures or transport expiry.

The CLI requires recent media proof by default. An initial timeout or unsupported sample stays pending and cannot mask a confirmed lower provider. A first inconclusive result may retain a previous real media success within the configured maximum age; it cannot resurrect a rejected context. `--no-require-media-proof` is provided for explicit offline fixture generation, not the production operations pipeline. Library configurations retain the opt-in field for compatibility.

Public artifacts are playlists, metadata and the shared guide. Stream status, removals, references and upstream caches remain private. All temporary writes are atomic with failure cleanup. The operations repository stages and verifies the complete bundle before promotion; do not use its publishing wrapper as a diagnostic command.

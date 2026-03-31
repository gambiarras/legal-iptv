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
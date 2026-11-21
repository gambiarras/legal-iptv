"""Bounded transport sampling; HTTP reachability is not video playback."""

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from urllib3.exceptions import HTTPError

from legal_iptv.models.stream_validation import UrlValidation

PROBE_VERSION = 2
MANIFEST_BYTES = 262_144
MEDIA_BYTES = 65_536
MAX_REQUESTS = 8
SAFE_CROSS_ORIGIN_HEADERS = {
    "accept", "accept-encoding", "accept-language", "connection", "user-agent", "range",
}


def _origin(url: str) -> tuple:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Unsupported URL scheme")
    return parsed.scheme, parsed.hostname, parsed.port


def _is_media(data: bytes) -> bool:
    # Several complete TS packets, allowing a short prefix before sync.
    for offset in range(min(188, max(0, len(data) - 376))):
        if data[offset] == data[offset + 188] == data[offset + 376] == 0x47:
            return True
    # Fragmented MP4 media must contain payload, not just an initialization header.
    return len(data) >= 16 and b"mdat" in data[:4096] and (
        b"moof" in data[:4096] or b"ftyp" in data[:4096]
    )


def _looks_html(data: bytes) -> bool:
    prefix = data[:256].lstrip().lower()
    return prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"{", b"["))


@dataclass
class _Failure(Exception):
    active: bool | None
    reason: str
    status: int | None = None


class _Probe:
    def __init__(self, session: requests.Session, url: str, timeout: int, headers: dict):
        self.session = session
        self.url = url
        self.headers = {str(k).casefold(): str(v) for k, v in headers.items()}
        self.started = time.monotonic()
        self.deadline = self.started + max(0.1, timeout)
        self.requests = 0
        self.root_status: int | None = None

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _Failure(None, "ProbeTimeout")
        return remaining

    def fetch(self, url: str, *, media: bool = False) -> tuple[bytes, str, int]:
        cap = MEDIA_BYTES if media else MANIFEST_BYTES
        for _redirect in range(4):
            self._remaining()
            if self.requests >= MAX_REQUESTS:
                raise _Failure(None, "ProbeRequestLimit")
            try:
                same_origin = _origin(url) == _origin(self.url)
            except ValueError:
                raise _Failure(None, "UnsupportedMediaUrl") from None
            headers = {
                key: value for key, value in self.headers.items()
                if same_origin or key in SAFE_CROSS_ORIGIN_HEADERS
            }
            if media:
                headers["range"] = f"bytes=0-{cap - 1}"
            self.requests += 1
            remaining = self._remaining()
            response = self.session.get(
                url, headers=headers, stream=True, allow_redirects=False,
                timeout=(min(3.0, remaining), min(3.0, remaining)),
            )
            try:
                status = response.status_code
                if self.root_status is None:
                    self.root_status = status
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise _Failure(None, "InvalidRedirect", status)
                    url = urljoin(url, location)
                    continue
                if status in {404, 410}:
                    raise _Failure(False, "HttpNotFound" if url == self.url else "MediaNotFound", status)
                if status < 200 or status >= 300:
                    raise _Failure(None, "HttpUnavailable", status)
                data = bytearray()
                raw = getattr(response, "raw", None)
                if raw is not None and callable(getattr(raw, "read1", None)):
                    def chunks():
                        while True:
                            self._remaining()
                            chunk = raw.read1(min(4096, cap - len(data)), decode_content=True)
                            if not chunk:
                                return
                            yield chunk
                    iterator = chunks()
                else:
                    iterator = response.iter_content(chunk_size=1024)
                for chunk in iterator:
                    self._remaining()
                    if not chunk:
                        continue
                    data.extend(chunk[:cap - len(data)])
                    if not media and _looks_html(data):
                        raise _Failure(False, "InvalidMediaContent", status)
                    if not media and len(data) >= MEDIA_BYTES and _is_media(data):
                        break
                    if len(data) >= cap:
                        if not media:
                            raise _Failure(None, "ProbeByteLimit", status)
                        break
                if not data:
                    raise _Failure(False, "EmptyMedia", status)
                return bytes(data), url, status
            finally:
                response.close()
        raise _Failure(None, "RedirectLimit")

    def hls(self, data: bytes, url: str, *, depth: int = 0) -> None:
        if depth > 1:
            raise _Failure(None, "HlsVariantLimit")
        try:
            lines = [line.strip() for line in data.decode("utf-8-sig").splitlines() if line.strip()]
        except UnicodeError:
            raise _Failure(False, "InvalidManifest") from None
        if not lines or lines[0] != "#EXTM3U":
            raise _Failure(False, "InvalidManifest")
        variants = []
        for index, line in enumerate(lines[:-1]):
            if line.startswith("#EXT-X-STREAM-INF:") and not lines[index + 1].startswith("#"):
                bandwidth = re.search(r"(?:^|,)BANDWIDTH=(\d+)", line.split(":", 1)[1])
                variants.append((int(bandwidth.group(1)) if bandwidth else 0, lines[index + 1]))
        if variants:
            # Sample the least expensive declared rendition; no recursive fan-out.
            target = urljoin(url, min(variants)[1])
            child, child_url, _ = self.fetch(target)
            self.hls(child, child_url, depth=depth + 1)
            return
        if any(
            line.startswith("#EXT-X-KEY:")
            and not re.search(r"(?:^|,)METHOD=NONE(?:,|$)", line.split(":", 1)[1])
            for line in lines
        ):
            raise _Failure(None, "EncryptedHlsUnsupported")
        if any(
            line.startswith("#EXT-X-BYTERANGE:")
            or line.startswith("#EXT-X-MAP:") and "BYTERANGE=" in line
            for line in lines
        ):
            raise _Failure(None, "HlsByteRangeUnsupported")
        segments = [line for line in lines if not line.startswith("#")]
        if "#EXTINF:" not in "\n".join(lines) or not segments:
            raise _Failure(False, "InvalidManifest")
        init = next((line for line in lines if line.startswith("#EXT-X-MAP:")), None)
        if init:
            match = re.search(r'URI="([^"]+)"', init)
            if not match:
                raise _Failure(False, "InvalidManifest")
            initialization, _, _ = self.fetch(urljoin(url, match.group(1)), media=True)
            if b"ftyp" not in initialization[:256] and b"moov" not in initialization[:4096]:
                raise _Failure(False, "InvalidInitialization")
        # A newest segment may not yet exist on the origin. One preceding segment
        # avoids declaring failure because of a prefetch/CDN window race.
        for target in reversed(segments[-2:]):
            try:
                payload, _, _ = self.fetch(urljoin(url, target), media=True)
            except _Failure as exc:
                if exc.reason == "MediaNotFound":
                    continue
                raise
            if _looks_html(payload):
                raise _Failure(False, "InvalidMediaContent")
            if not _is_media(payload):
                raise _Failure(None, "UnsupportedMediaSample")
            return
        raise _Failure(None, "MediaNotFound", 404)

    def run(self) -> UrlValidation:
        try:
            data, url, status = self.fetch(self.url)
            if data.lstrip().startswith((b"#EXTM3U", b"\xef\xbb\xbf#EXTM3U")):
                self.hls(data, url)
            elif _is_media(data):
                pass
            elif b"<MPD" in data[:4096] or b":MPD" in data[:4096]:
                raise _Failure(None, "DashSamplingUnsupported", status)
            else:
                raise _Failure(None, "UnsupportedMediaSample", status)
            result = UrlValidation(True, status)
        except _Failure as exc:
            result = UrlValidation(exc.active, exc.status or self.root_status, exc.reason)
        except requests.RequestException as exc:
            result = UrlValidation(None, self.root_status, type(exc).__name__)
        except (HTTPError, OSError) as exc:
            result = UrlValidation(None, self.root_status, type(exc).__name__)
        return UrlValidation(
            result.active, result.http_status, result.error_type,
            round((time.monotonic() - self.started) * 1000, 3), self.requests,
        )


def probe_transport(session: requests.Session, url: str, timeout: int, headers: dict) -> UrlValidation:
    return _Probe(session, url, timeout, headers).run()

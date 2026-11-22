import unittest
from dataclasses import replace

from legal_iptv.exporters import render_m3u
from legal_iptv.models import Channel
from legal_iptv.services.publication import is_exportable
from tests.unit import test_video_platform_transport as video_fixtures


def channel(**values):
    base = dict(
        id="example",
        name="Example",
        stream_url="https://media.example.invalid/live.m3u8",
        logo="",
        group="Variedades",
        source="extra",
    )
    return Channel(**{**base, **values})


class PublicationPrivacyTest(unittest.TestCase):
    def test_generic_query_aliases_and_credential_header_urls_never_export(self):
        cases = [
            {
                "stream_url": "https://media.example.invalid/live.m3u8?"
                + name
                + "=fake-private"
            }
            for name in ("api_key", "api-key", "x-api-key", "AUTH", "signature", "sig")
        ] + [
            {
                "request_headers": {
                    " Referer ": "https://page.example.invalid/?token=fake-private"
                }
            },
            {
                "request_headers": {
                    "X-Player": "https://user:fake-private@page.example.invalid/"
                }
            },
            {"request_headers": {" Authorization ": "fake-private"}},
            {
                "request_headers": {
                    "Origin": "https://page.example.invalid/?api_key=fake-private"
                }
            },
        ]
        for static, dynamic in ((True, False), (False, True)):
            for changes in cases:
                with self.subTest(static=static, dynamic=dynamic, changes=changes):
                    current = channel(
                        publishable_static=static,
                        requires_dynamic_resolution=dynamic,
                        **changes,
                    )
                    self.assertFalse(is_exportable(current, "portable"))
                    self.assertNotIn(
                        "#EXTINF:", render_m3u([current], enforce_capabilities=True)
                    )
                    self.assertEqual(
                        (
                            current.publishable_static,
                            current.requires_dynamic_resolution,
                        ),
                        (static, dynamic),
                    )

    def test_public_headers_remain_usable_and_malformed_urls_fail_closed(self):
        current = channel(
            request_headers={
                "User-Agent": "Public player/1.0",
                "Referer": "https://page.example.invalid/watch?language=pt",
                "X-Display": "custom:public",
            }
        )
        self.assertTrue(is_exportable(current, "portable"))
        self.assertIn("#EXTINF:", render_m3u([current], enforce_capabilities=True))
        self.assertFalse(
            is_exportable(
                replace(current, requires_dynamic_resolution=True), "portable"
            )
        )
        for header_url in (
            "https://[broken/player",
            "https://page.example.invalid:bad/player",
            "https:///player",
        ):
            with self.subTest(header_url=header_url):
                self.assertFalse(
                    is_exportable(
                        replace(current, request_headers={"Referer": header_url}),
                        "portable",
                    )
                )
        for url in (
            "https://[broken/live.m3u8",
            "https://media.example.invalid:bad/live.m3u8",
            "https:///live.m3u8",
            "relative/live.m3u8",
            "https://media.example.invalid/path with spaces",
            "https://media.example.invalid/\\private",
        ):
            with self.subTest(url=url):
                self.assertFalse(
                    is_exportable(replace(current, stream_url=url), "portable")
                )


class RenewableHeaderPrivacyTest(unittest.TestCase):
    def setUp(self):
        video_fixtures.VideoPlatformTransportTest.setUp(self)

    def test_validated_media_signature_does_not_exempt_header_credentials(self):
        current = video_fixtures.channel(
            request_headers={
                "Referer": "https://page.example.invalid/player",
                "User-Agent": "Public player/1.0",
            }
        )
        self.assertTrue(is_exportable(current, "portable"))
        private = replace(
            current,
            request_headers={
                "Referer": "https://page.example.invalid/?token=fake-private"
            },
        )
        self.assertFalse(is_exportable(private, "portable"))
        self.assertEqual(
            (private.publishable_static, private.requires_dynamic_resolution),
            (False, True),
        )

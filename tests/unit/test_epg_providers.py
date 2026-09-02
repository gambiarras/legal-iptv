import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from legal_iptv.services.epg_providers import (
    json_epg_to_xmltv,
    load_epg_providers,
    solr_epg_to_xmltv,
)
from legal_iptv.services.guide import validate_guide_root


class EpgProvidersTest(unittest.TestCase):
    def test_loads_private_provider_endpoints_and_headers_from_environment(self):
        with patch.dict(
            "os.environ",
            {
                "IPTV_EPG_PRIMARY_URL": "https://primary.example.test/guide",
                "IPTV_EPG_PRIMARY_FORMAT": "json",
                "IPTV_EPG_PRIMARY_HEADERS": '{"Authorization":"Bearer runtime"}',
                "IPTV_EPG_PRIMARY_CITY": "Petropolis-RJ,Rio de Janeiro-RJ",
                "IPTV_EPG_PRIMARY_DAYS": "3",
                "IPTV_EPG_ADDITIONAL_URL": "https://additional.example.test/guide.xml",
            },
        ):
            providers = load_epg_providers()

        primary = next(provider for provider in providers if provider.id == "primary")
        additional = next(provider for provider in providers if provider.id == "additional")
        self.assertEqual(primary.format, "json")
        self.assertEqual(primary.headers, {"Authorization": "Bearer runtime"})
        self.assertEqual(primary.city, "Petropolis-RJ,Rio de Janeiro-RJ")
        self.assertEqual(primary.days, 3)
        self.assertEqual(additional.format, "xmltv")

    def test_converts_neutral_json_schedule_to_valid_xmltv(self):
        now = datetime.now(timezone.utc)
        root = json_epg_to_xmltv(
            {
                "channels": [
                    {
                        "id": "Example.br",
                        "name": "Example",
                        "programmes": [
                            {
                                "title": "Current",
                                "start": (now - timedelta(hours=1)).isoformat(),
                                "stop": (now + timedelta(hours=1)).isoformat(),
                            },
                            {
                                "title": "Next",
                                "start": (now + timedelta(hours=1)).isoformat(),
                                "stop": (now + timedelta(hours=2)).isoformat(),
                            },
                        ],
                    }
                ]
            }
        )

        stats = validate_guide_root(root, now=now)

        self.assertEqual(stats.channels, 1)
        self.assertEqual(stats.programmes, 2)

    def test_converts_camel_case_json_schedule_to_valid_xmltv(self):
        now = datetime.now(timezone.utc)
        root = json_epg_to_xmltv(
            [
                {
                    "id": "example",
                    "title": "Example",
                    "custom_fields": {"channel_logo": "https://example.test/logo.png"},
                    "programs": [
                        {
                            "title": "Current",
                            "startTime": int((now - timedelta(hours=1)).timestamp() * 1000),
                            "endTime": int((now + timedelta(hours=1)).timestamp() * 1000),
                            "parentalRating": "10",
                            "macros": {"content_genre": "variedade"},
                        },
                        {
                            "title": "Next",
                            "startTime": int((now + timedelta(hours=1)).timestamp() * 1000),
                            "endTime": int((now + timedelta(hours=2)).timestamp() * 1000),
                        },
                    ],
                }
            ]
        )

        stats = validate_guide_root(root, now=now)

        self.assertEqual(stats.programmes, 2)
        self.assertEqual(root.findtext("channel/display-name"), "Example")
        self.assertEqual(root.find("channel/icon").attrib["src"], "https://example.test/logo.png")
        self.assertEqual(root.findtext("programme/category"), "variedade")
        self.assertEqual(root.findtext("programme/rating/value"), "10")

    def test_builds_xmltv_from_solr_city_channels_and_programmes(self):
        now = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)

        class Client:
            def get_bytes(self, url, headers=None):
                parsed = urlsplit(url)
                query = parse_qs(parsed.query)
                if parsed.path.endswith("/cidade/select"):
                    if query["q"] == ["nome_novo:petropolis"]:
                        return b'{"response":{"docs":[]}}'
                    return b'{"response":{"docs":[{"id_cidade":1}]}}'
                if parsed.path.endswith("/canal/select"):
                    return (
                        b'{"response":{"docs":[{"id_canal":1196,'
                        b'"st_canal":"Example","url_imagem":"https://example.test/logo.png"}]}}'
                    )
                if parsed.path.endswith("/exibicao/select"):
                    self.programme_query = query
                    return (
                        b'{"response":{"docs":['
                        b'{"id_canal":1196,"titulo":"Current",'
                        b'"dh_inicio":"2026-09-02T11:00:00Z",'
                        b'"dh_fim":"2026-09-02T13:00:00Z"},'
                        b'{"id_revel":"1_1196","titulo":"Next",'
                        b'"dh_inicio":"2026-09-02T13:00:00Z",'
                        b'"dh_fim":"2026-09-02T14:00:00Z"}'
                        b']}}'
                    )
                raise AssertionError(url)

        client = Client()
        root = solr_epg_to_xmltv(
            client,
            "https://provider.example/gatekeeper",
            city="Petropolis-RJ,Rio de Janeiro-RJ",
            now=now,
        )
        stats = validate_guide_root(root, now=now)

        self.assertEqual(stats.channels, 1)
        self.assertEqual(stats.programmes, 2)
        self.assertEqual(root.findtext("channel/display-name"), "Example")
        self.assertIn("1_1196", client.programme_query["q"][0])


if __name__ == "__main__":
    unittest.main()

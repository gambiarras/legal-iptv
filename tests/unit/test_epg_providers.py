import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from legal_iptv.services.epg_providers import json_epg_to_xmltv, load_epg_providers
from legal_iptv.services.guide import validate_guide_root


class EpgProvidersTest(unittest.TestCase):
    def test_loads_private_provider_endpoints_and_headers_from_environment(self):
        with patch.dict(
            "os.environ",
            {
                "IPTV_EPG_PRIMARY_URL": "https://primary.example.test/guide",
                "IPTV_EPG_PRIMARY_FORMAT": "json",
                "IPTV_EPG_PRIMARY_HEADERS": '{"Authorization":"Bearer runtime"}',
                "IPTV_EPG_ADDITIONAL_URL": "https://additional.example.test/guide.xml",
            },
        ):
            providers = load_epg_providers()

        primary = next(provider for provider in providers if provider.id == "primary")
        additional = next(provider for provider in providers if provider.id == "additional")
        self.assertEqual(primary.format, "json")
        self.assertEqual(primary.headers, {"Authorization": "Bearer runtime"})
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


if __name__ == "__main__":
    unittest.main()

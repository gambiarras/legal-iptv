import os
import unittest

from legal_iptv.clients import HttpClient
from legal_iptv.services.epg_providers import load_epg_providers
from legal_iptv.services.guide import validate_guide_root


@unittest.skipUnless(
    os.environ.get("RUN_INTEGRATION_TESTS") == "1",
    "Set RUN_INTEGRATION_TESTS=1 to enable network tests",
)
class EpgProviderIntegrationTest(unittest.TestCase):
    def test_primary_provider_returns_valid_current_xmltv(self):
        if not os.environ.get("IPTV_EPG_PRIMARY_URL"):
            self.skipTest("Set IPTV_EPG_PRIMARY_URL to test the primary provider")
        provider = next(
            item for item in load_epg_providers()
            if item.id == "primary"
        )
        client = HttpClient(timeout=20, retries=1)
        try:
            stats = validate_guide_root(provider.fetch(client))
        finally:
            client.close()

        self.assertGreater(stats.channels, 0)
        self.assertGreater(stats.programmes, 0)


if __name__ == "__main__":
    unittest.main()

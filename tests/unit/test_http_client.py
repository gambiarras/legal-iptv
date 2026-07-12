import unittest
from unittest.mock import Mock, patch

from legal_iptv.clients.http_client import HttpClient


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        return None


class HttpClientTest(unittest.TestCase):
    @patch("legal_iptv.clients.http_client.time.sleep")
    def test_reports_last_status_when_blocked_every_attempt(self, sleep_mock: Mock):
        client = HttpClient(retries=2, base_delay=0)
        client.session.get = Mock(return_value=FakeResponse(429))

        with self.assertLogs("legal_iptv.clients.http_client", level="WARNING"):
            with self.assertRaisesRegex(RuntimeError, "last_status=429"):
                client.get_json("https://example.test/data.json")

        self.assertEqual(client.session.get.call_count, 2)
        self.assertEqual(sleep_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()

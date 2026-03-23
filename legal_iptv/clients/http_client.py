import logging
import random
import time
from typing import Optional

import requests


logger = logging.getLogger(__name__)


BROWSER_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",

    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",

    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",

    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
]


def _build_headers(
    referer: Optional[str] = None,
    accept: str = "application/json, text/plain, */*",
) -> dict:
    headers = {
        "User-Agent": random.choice(BROWSER_USER_AGENTS),
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8",
        "Connection": "keep-alive",
    }

    if referer:
        headers["Referer"] = referer

    return headers


class HttpClient:
    def __init__(
        self,
        timeout: int = 20,
        retries: int = 3,
        base_delay: float = 1.0,
    ):
        self.timeout = timeout
        self.retries = retries
        self.base_delay = base_delay
        self.session = requests.Session()

    def get_json(
        self,
        url: str,
        referer: Optional[str] = None,
    ) -> dict | list:
        return self._request(
            url=url,
            expect_json=True,
            referer=referer,
        )

    def get_text(
        self,
        url: str,
        referer: Optional[str] = None,
    ) -> str:
        return self._request(
            url=url,
            expect_json=False,
            referer=referer,
        )

    def _request(
        self,
        url: str,
        expect_json: bool,
        referer: Optional[str] = None,
    ):
        last_error = None

        for attempt in range(self.retries):
            headers = _build_headers(
                referer=referer,
                accept=(
                    "application/json, text/plain, */*"
                    if expect_json
                    else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
            )

            try:
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                )

                if response.status_code in (403, 429):
                    logger.warning(
                        "Blocked or rate-limited (status=%s) url=%s attempt=%s",
                        response.status_code,
                        url,
                        attempt + 1,
                    )
                    self._sleep(attempt)
                    continue

                response.raise_for_status()

                if expect_json:
                    return response.json()

                return response.text

            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Request failed (attempt %s/%s) url=%s error=%s",
                    attempt + 1,
                    self.retries,
                    url,
                    exc,
                )
                self._sleep(attempt)

        raise RuntimeError(f"Failed to fetch {url}: {last_error}")

    def _sleep(self, attempt: int) -> None:
        delay = self.base_delay * (2 ** attempt)
        jitter = random.uniform(0.1, 0.5)
        time.sleep(delay + jitter)

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass
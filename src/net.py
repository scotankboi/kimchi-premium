"""One HTTP client for every source: retries with backoff and a polite sleep."""
import time

import requests

RETRY_STATUS = {418, 429, 500, 502, 503, 504}


class HttpClient:
    def __init__(self, sleep_sec: float = 0.1, max_retries: int = 5, timeout: int = 30):
        self.sleep_sec = sleep_sec
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "kimchi-premium-research/0.1 (public data only)"

    def get(self, url: str, params: dict | None = None) -> requests.Response:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:  # network blip: retry
                last_error = exc
            else:
                if resp.status_code not in RETRY_STATUS:
                    resp.raise_for_status()
                    time.sleep(self.sleep_sec)
                    return resp
                last_error = requests.HTTPError(f"HTTP {resp.status_code} for {resp.url}")
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(int(retry_after))
                    continue
            time.sleep(min(2 ** attempt, 30))
        raise RuntimeError(f"GET {url} failed after {self.max_retries} attempts: {last_error}")

    def get_json(self, url: str, params: dict | None = None):
        return self.get(url, params).json()

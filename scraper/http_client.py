"""Small polite HTTP helper shared by both scrapers: global rate limit + retries."""
import threading
import time

import requests

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class Throttle:
    """Allows at most one request start every `interval` seconds across all threads."""

    def __init__(self, interval):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            time.sleep(delay)


class Client:
    def __init__(self, base_url, headers=None, interval=0.25, retries=4, timeout=30):
        self.base = base_url.rstrip("/")
        self.retries = retries
        self.timeout = timeout
        self.throttle = Throttle(interval)
        self.local = threading.local()
        self.headers = {"User-Agent": BROWSER_UA, "Accept-Language": "en-IE,en;q=0.9"}
        if headers:
            self.headers.update(headers)
        self.errors = 0

    def _session(self):
        s = getattr(self.local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update(self.headers)
            self.local.s = s
        return s

    def get(self, path):
        """Returns (status, text, final_url). status 0 = network failure after retries.
        404/410 are returned as-is (a genuine 'not there'), never retried."""
        url = path if path.startswith("http") else self.base + path
        status, text, final = 0, "", url
        for attempt in range(self.retries):
            self.throttle.wait()
            try:
                r = self._session().get(url, timeout=self.timeout)
                status, text, final = r.status_code, r.text, r.url
                if status in (404, 410) or status < 400:
                    return status, text, final
                if status == 429:
                    time.sleep(float(r.headers.get("Retry-After", 5 * (attempt + 1))))
                    continue
            except requests.RequestException:
                status = 0
            time.sleep(1.5 * (attempt + 1))
        self.errors += 1
        return status, text, final
